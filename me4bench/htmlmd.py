"""Small HTML -> Markdown converter (standard library). Keeps headings, lists and tables
(pipe tables), drops scripts, styles, navigation boxes and citation superscripts.
Built for Wikipedia REST HTML but works on ordinary pages."""
from __future__ import annotations

import re
from html.parser import HTMLParser

SKIP = {"script", "style", "noscript", "head", "svg", "math", "figure", "audio", "video"}
SKIP_CLASS = ("navbox", "reflist", "mw-references", "hatnote", "mbox", "sidebar-navbar", "noprint",
              "mw-editsection", "reference", "metadata", "shortdescription", "thumbcaption")


class _P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.skip_stack: list[bool] = []
        self.list_stack: list[list] = []
        self.table: list | None = None  # rows of cells
        self.cell: list[str] | None = None
        self.row: list | None = None
        self.in_pre = False

    def _emit(self, s):
        if self.cell is not None:
            self.cell.append(s)
        else:
            self.out.append(s)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "") + " " + (a.get("role") or "")
        skip = tag in SKIP or any(c in cls for c in SKIP_CLASS) or tag == "sup" and "reference" in cls
        if tag not in ("br", "img", "hr", "meta", "link", "input"):
            self.skip_stack.append(skip)
        if skip:
            self.skip += 1
        if self.skip:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._emit("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in ("p", "div", "section", "blockquote") and self.cell is None:
            self._emit("\n\n")
        elif tag == "br":
            self._emit(" " if self.cell is not None else "\n")
        elif tag in ("ul", "ol"):
            self.list_stack.append([tag, 0])
            self._emit("\n")
        elif tag == "li":
            depth = max(0, len(self.list_stack) - 1)
            if self.list_stack:
                self.list_stack[-1][1] += 1
                mark = "-" if self.list_stack[-1][0] == "ul" else f"{self.list_stack[-1][1]}."
            else:
                mark = "-"
            self._emit("\n" + "  " * depth + mark + " ")
        elif tag == "table":
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag == "pre":
            self.in_pre = True
            self._emit("\n\n```\n")
        elif tag in ("b", "strong"):
            self._emit("**")
        elif tag in ("i", "em"):
            self._emit("*")

    def handle_endtag(self, tag):
        if tag in ("br", "img", "hr", "meta", "link", "input"):
            return
        skip = self.skip_stack.pop() if self.skip_stack else False
        if skip:
            self.skip -= 1
            return
        if self.skip:
            return
        if tag in ("ul", "ol") and self.list_stack:
            self.list_stack.pop()
            self._emit("\n")
        elif tag in ("td", "th") and self.cell is not None and self.row is not None:
            txt = re.sub(r"\s+", " ", "".join(self.cell)).strip().replace("|", "\\|")
            self.row.append(txt)
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            if any(c for c in self.row):
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            rows, self.table = self.table, None
            if rows:
                w = max(len(r) for r in rows)
                rows = [r + [""] * (w - len(r)) for r in rows]
                lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * w]
                lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                self._emit("\n\n" + "\n".join(lines) + "\n\n")
        elif tag == "pre":
            self.in_pre = False
            self._emit("\n```\n")
        elif tag in ("b", "strong"):
            self._emit("**")
        elif tag in ("i", "em"):
            self._emit("*")
        elif tag in ("p", "div", "section", "blockquote") and self.cell is None:
            self._emit("\n")

    def handle_data(self, data):
        if self.skip:
            return
        if not self.in_pre:
            data = re.sub(r"\s+", " ", data)
        self._emit(data)


def html_to_markdown(html: str) -> str:
    p = _P()
    p.feed(html)
    p.close()
    text = "".join(p.out)
    text = re.sub(r"\*\*\s*\*\*", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"
