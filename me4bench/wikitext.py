"""MediaWiki wikitext -> Markdown, standard library only.

Good enough for retrieval, not a full parser: templates are not expanded by MediaWiki, so a few
common ones are rendered here (infoboxes as key/value lists, convert, dates, lang, lists, ...) and
the rest are dropped. Tables become Markdown tables (rowspan/colspan ignored). Citations, files,
categories and the References / External links sections are removed.
"""
from __future__ import annotations

import html
import re

_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"]
_DROP_SECTIONS = {"references", "external links", "further reading", "notes", "footnotes",
                  "bibliography", "sources", "citations", "notes and references", "works cited"}
_SKIP_INFOBOX_KEYS = re.compile(r"^(image|img|logo|signature|map|alt|caption|pushpin|coordinates|coord|"
                                r"module|embed|child|footnotes?|website|url|label_?name|"
                                r"name_?style|native_name_lang|.*_size|.*image.*|.*_alt|.*map.*)$", re.I)
_PASS_FIRST = {"nowrap", "small", "big", "nobr", "abbr", "sic", "ill", "interlanguage link", "flag",
               "flagcountry", "flagu", "nts", "ntsh", "tooltip", "nihongo", "oldstyledate", "lang-rtl",
               "mvar", "math", "var", "keypress", "code", "smallcaps", "sc", "noitalic", "resize",
               "wikt-lang", "uss", "hms", "sclass", "ship", "proper name", "nastaliq", "korean",
               "noflag", "visible anchor", "vanchor", "linktext", "not a typo", "as written", "strong",
               "em", "highlight", "angbr", "bracket", "ubli", "transliteration", "audio", "respell",
               "post-nominals", "honorific prefix", "flagathlete"}
_LIST = {"ubl", "unbulleted list", "plainlist", "plain list", "flatlist", "hlist", "bulleted list",
         "ordered list", "collapsible list", "enum", "bulleted", "unbulleted", "flowlist", "pagelist",
         "startplainlist", "endplainlist", "toggle list", "nobold"}
_DATES = {"birth date", "death date", "birth date and age", "death date and age", "start date",
          "end date", "start date and age", "end date and age", "date", "dob", "bda", "dda",
          "birth year and age", "death year and age", "birth-date", "death-date", "film date",
          "release date", "start and end dates", "birth date and age2", "death date and age2",
          "date and age", "birth based on age as of date", "age"}


# ---------- balanced scanning ----------

def _replace_balanced(text: str, open_: str, close: str, fn) -> str:
    """Replace every outermost open_...close span (nesting aware) with fn(inner)."""
    out, i, n = [], 0, len(text)
    while i < n:
        j = text.find(open_, i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        depth, k = 0, j
        while k < n:
            if text.startswith(open_, k):
                depth += 1
                k += len(open_)
            elif text.startswith(close, k):
                depth -= 1
                k += len(close)
                if depth == 0:
                    break
            else:
                k += 1
        if depth != 0:              # unbalanced: keep the rest as is
            out.append(text[j:])
            break
        out.append(fn(text[j + len(open_):k - len(close)]))
        i = k
    return "".join(out)


def _split_top(s: str, sep: str = "|") -> list:
    """Split on sep outside [[...]], {{...}} and [...]."""
    parts, buf, depth, i = [], [], 0, 0
    while i < len(s):
        two = s[i:i + 2]
        if two in ("[[", "{{"):
            depth += 1
            buf.append(two)
            i += 2
            continue
        if two in ("]]", "}}") and depth:
            depth -= 1
            buf.append(two)
            i += 2
            continue
        if s[i] == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(s[i])
        i += 1
    parts.append("".join(buf))
    return parts


# ---------- inline ----------

def _link(inner: str) -> str:
    target, _, label = inner.partition("|")
    t = target.strip()
    low = t.lower().lstrip(":")
    if re.match(r"^(file|image|media|category|wikipedia|wp|help|template|portal|special|"
                r"[a-z]{2,3}(-[a-z]+)?):", low) and not t.startswith(":") and not low.startswith("wikt:"):
        return ""          # files, categories, interlanguage links
    if label.strip():
        return label.split("|")[-1].strip()
    t = t.lstrip(":")
    if t.lower().startswith("wikt:"):
        t = t[5:]
    return t.split("#")[0] if not t.startswith("#") else t[1:]


def inline(s: str) -> str:
    s = _replace_balanced(s, "[[", "]]", lambda x: _link(_replace_balanced(x, "[[", "]]", _link)))
    s = re.sub(r"\[(?:https?:)?//[^\s\]]+\s+([^\]]+)\]", r"\1", s)     # [http://x label]
    s = re.sub(r"\[(?:https?:)?//[^\s\]]+\]", "", s)
    s = re.sub(r"'''''(.+?)'''''", r"***\1***", s)
    s = re.sub(r"'''(.+?)'''", r"**\1**", s)
    s = re.sub(r"''(.+?)''", r"*\1*", s)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</?[a-zA-Z][^<>]*>", "", s)
    return s


# ---------- templates ----------

def _args(inner: str):
    parts = _split_top(inner)
    name = parts[0].strip()
    pos, named = [], {}
    for p in parts[1:]:
        m = re.match(r"^\s*([^=\[\]{}]+?)\s*=(.*)$", p, re.S)
        if m:
            named[m.group(1).strip().lower()] = m.group(2).strip()
        else:
            pos.append(p.strip())
    return name, pos, named


def _date(pos: list) -> str:
    nums = [p for p in pos if re.fullmatch(r"\d{1,4}", p)]
    if not nums:
        return next((p for p in pos if p and "=" not in p), "")
    y = nums[0]
    if len(nums) >= 3 and 1 <= int(nums[1]) <= 12:
        return f"{_MONTHS[int(nums[1]) - 1]} {int(nums[2])}, {y}"
    if len(nums) >= 2 and 1 <= int(nums[1]) <= 12:
        return f"{_MONTHS[int(nums[1]) - 1]} {y}"
    return y


def _list_items(pos: list) -> list:
    items = []
    for p in pos:
        for ln in p.split("\n"):
            ln = re.sub(r"^\s*[*#]+\s*", "", ln).strip()
            if ln:
                items.append(ln)
    return items


def render_template(inner: str) -> str:
    name, pos, named = _args(inner)
    low = re.sub(r"\s+", " ", name.replace("_", " ")).strip().lower()
    if low.startswith("#") or low.startswith("subst:") or low.startswith("safesubst:"):
        return ""
    if ":" in low:
        fn, _, val = name.partition(":")
        if fn.strip().lower() in ("formatnum", "lc", "uc", "ucfirst", "lcfirst"):
            return val.strip()
        return ""
    if low.startswith("infobox") or low.endswith("infobox") or low in ("taxobox", "speciesbox", "automatic taxobox"):
        rows = []
        for k, v in named.items():
            v = inline(v).strip()
            if v and not _SKIP_INFOBOX_KEYS.match(k):
                v = re.sub(r"\s*\n\s*", "; ", v)
                rows.append(f"- {k.replace('_', ' ')}: {v}")
        title = name[len("infobox"):].strip() if low.startswith("infobox") else name
        return f"\n\nInfobox{(' ' + title) if title else ''}:\n\n" + "\n".join(rows) + "\n\n" if rows else ""
    if low in ("convert", "cvt"):
        if len(pos) >= 4 and pos[1] in ("-", "–", "to", "and", "or", "x", "by", "+/-", "±"):
            return f"{pos[0]} {pos[1]} {pos[2]} {pos[3]}"
        return " ".join(pos[:2])
    if low in _DATES:
        return _date(pos)
    if low.startswith("lang-") or low in ("lang", "langx", "transl", "translit", "script"):
        return pos[-1] if pos else ""
    if low in _LIST:
        return ", ".join(_list_items(pos))
    if low in ("sortname",):
        return " ".join(pos[:2])
    if low in ("sort", "sort key", "hs", "ntsh"):
        return pos[1] if len(pos) > 1 else (pos[0] if pos else "")
    if low in ("circa", "c.", "c"):
        return "c. " + (pos[0] if pos else "")
    if low in ("as of",):
        return "As of " + " ".join(pos[:3])
    if low in ("frac", "fraction"):
        return "/".join(pos) if len(pos) <= 2 else f"{pos[0]} {pos[1]}/{pos[2]}"
    if low in ("us$", "usd", "us dollar"):
        return "US$" + (pos[0] if pos else "")
    if low in ("val",):
        return " ".join([pos[0] if pos else ""] + [named.get("u", named.get("ul", ""))]).strip()
    if low in ("quote", "blockquote", "quotation", "cquote", "quote box"):
        return (pos[0] if pos else named.get("text", named.get("quote", "")))
    if low in ("mp", "nbsp", "spaced ndash", "snd", "ndash", "spnd"):
        return {"ndash": "–", "snd": " – ", "spaced ndash": " – ", "spnd": " – "}.get(low, " ")
    if low in ("abbrlink", "ill") or low in _PASS_FIRST:
        return pos[0] if pos else ""
    if low in ("marriage", "married"):
        return (pos[0] if pos else "") + (f" (m. {' '.join(pos[1:3])})" if len(pos) > 1 else "")
    if low in ("height",):
        return " ".join(f"{v} {k}" for k, v in named.items() if v)
    return ""   # citations, navboxes, maintenance tags, hatnotes, ...


# ---------- tables ----------

def _cell(c: str) -> str:
    parts = _split_top(c)
    if len(parts) > 1 and re.search(r"=|^\s*$", parts[0]):    # attributes | content
        c = "|".join(parts[1:])
    c = inline(c).strip()
    return re.sub(r"\s*\n\s*", "; ", c).replace("|", "\\|")


def table_to_md(body: str) -> str:
    rows, cur, caption, header_row = [], None, "", None
    lines = body.split("\n")[1:]          # first line = table attributes
    for ln in lines:
        s = ln.strip()
        if s.startswith("|+"):
            caption = inline(s[2:].split("|")[-1]).strip()
        elif s.startswith("|-"):
            if cur:
                rows.append(cur)
            cur = []
        elif s.startswith("!") or s.startswith("|"):
            if cur is None:
                cur = []
            is_head = s.startswith("!")
            parts = re.split(r"!!|\|\|", s[1:]) if is_head else s[1:].split("||")
            for p in parts:
                cur.append((_cell(p), is_head))
        elif cur:                                   # continuation of the previous cell
            if cur and s:
                t, h = cur[-1]
                cur[-1] = ((t + "; " + _cell(s)).strip("; "), h)
    if cur:
        rows.append(cur)
    rows = [r for r in rows if r]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    if all(h for _, h in rows[0]):
        header_row, rows = rows[0], rows[1:]
    head = [t for t, _ in header_row] if header_row else [""] * width
    head += [""] * (width - len(head))
    out = ([f"Table: {caption}", ""] if caption else []) + ["| " + " | ".join(head) + " |",
                                                      "|" + "---|" * width]
    for r in rows:
        cells = [t for t, _ in r] + [""] * (width - len(r))
        out.append("| " + " | ".join(cells) + " |")
    return "\n\n" + "\n".join(out) + "\n\n"


def _tables(s: str) -> str:
    rx = re.compile(r"^[ \t]*\{\|((?:(?!^[ \t]*\{\|)[\s\S])*?)^[ \t]*\|\}", re.M)
    for _ in range(20):                    # innermost first
        s2 = rx.sub(lambda m: table_to_md(m.group(1)), s)
        if s2 == s:
            break
        s = s2
    return s


# ---------- main ----------

def to_markdown(wt: str) -> str:
    s = wt.replace("\r\n", "\n")
    s = re.sub(r"<!--[\s\S]*?(-->|$)", "", s)
    s = re.sub(r"<ref[^>/]*/>", "", s, flags=re.I)
    s = re.sub(r"<ref[^>]*>[\s\S]*?</ref\s*>", "", s, flags=re.I)
    s = re.sub(r"<(gallery|timeline|score|graph|mapframe|imagemap|templatedata)[^>]*>[\s\S]*?</\1\s*>", "", s, flags=re.I)
    s = re.sub(r"<(math|chem)[^>]*>([\s\S]*?)</\1\s*>", r"\2", s, flags=re.I)
    s = re.sub(r"<(nowiki|code|pre|syntaxhighlight|source)[^>]*>([\s\S]*?)</\1\s*>", r"\2", s, flags=re.I)
    s = re.sub(r"__[A-Z]+__", "", s)
    keep: list = []

    def tpl(inner):                         # innermost-first rendering, kept as placeholders
        out = render_template(_replace_balanced(inner, "{{", "}}", tpl))
        keep.append(out)
        return f"\x00{len(keep) - 1}\x00"

    s = _replace_balanced(s, "{{", "}}", tpl)

    def unhold(x: str) -> str:
        for _ in range(10):
            y = re.sub(r"\x00(\d+)\x00", lambda m: keep[int(m.group(1))], x)
            if y == x:
                break
            x = y
        return x

    s = unhold(s)
    # links first (their | would break table cells), then tables
    s = _replace_balanced(s, "[[", "]]", lambda x: _link(_replace_balanced(x, "[[", "]]", _link)))
    s = _tables(s)
    out, drop = [], False
    for ln in s.split("\n"):
        m = re.match(r"^(={1,6})\s*(.+?)\s*\1\s*$", ln)
        if m:
            title = inline(m.group(2)).strip()
            level = len(m.group(1))
            if level <= 2:
                drop = title.lower() in _DROP_SECTIONS
            if not drop:
                out.append("")
                out.append("#" * min(6, level) + " " + title)
                out.append("")
            continue
        if drop:
            continue
        m = re.match(r"^([*#:;]+)\s*(.*)$", ln)
        if m:
            marks, body = m.group(1), inline(m.group(2)).strip()
            if not body:
                continue
            ind = "  " * (len(marks) - 1)
            last = marks[-1]
            if last == "*":
                out.append(f"{ind}- {body}")
            elif last == "#":
                out.append(f"{ind}1. {body}")
            elif last == ";":
                out.append(f"**{body}**")
            else:
                out.append(body)
            continue
        out.append(ln if ln.startswith("| ") else inline(ln))
    s = "\n".join(out)
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\(\s*[,;]?\s*\)", "", s)          # "( )" left by dropped templates
    s = re.sub(r"\(\s*[,;]\s*", "(", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip() + "\n"
