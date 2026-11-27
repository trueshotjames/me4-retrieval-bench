"""Speech normalisation: turn question text into something a TTS reads correctly.

``normalise(text)`` rewrites numbers, years, dates, money, percents, ordinals,
codes, file names, URLs and common symbols into words, e.g.::

    "In 1601"            -> "In sixteen oh one"
    "$5.2 million"       -> "five point two million dollars"
    "2023-05-03"         -> "May third, twenty twenty-three"
    "ABC-4"              -> "A B C dash four"
    "https://www.example.com/x" -> "example dot com"

Pure functions, standard library only.
"""
from __future__ import annotations

import re

CLARIFY_REPLY = "No extra details. Answer from the files, or say not found."

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety"]
_SCALES = [(10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"),
           (1000, "thousand")]
_ORD_IRREG = {"one": "first", "two": "second", "three": "third", "five": "fifth",
              "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
_MONTH_ABBR = {m[:3].lower(): m for m in MONTHS}
_MONTH_ABBR["sept"] = "September"
_MONTH_RE = (r"(?:January|February|March|April|May|June|July|August|September|"
             r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|"
             r"Oct|Nov|Dec)\.?")
_DIGIT_WORD = {str(i): _ONES[i] for i in range(10)}


# ------------------------------------------------------------ number words
def int_to_words(n: int) -> str:
    """0 -> 'zero', 1234 -> 'one thousand two hundred thirty-four', -5 -> 'minus five'."""
    n = int(n)
    if n < 0:
        return "minus " + int_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] + ("-" + _ONES[o] if o else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return _ONES[h] + " hundred" + (" " + int_to_words(r) if r else "")
    for value, name in _SCALES:
        if n >= value:
            q, r = divmod(n, value)
            return int_to_words(q) + " " + name + (" " + int_to_words(r) if r else "")
    raise AssertionError("unreachable")


def digits_to_words(s: str) -> str:
    """'007' -> 'zero zero seven'."""
    return " ".join(_DIGIT_WORD[c] for c in s if c.isdigit())


def ordinal_words(n: int) -> str:
    """3 -> 'third', 21 -> 'twenty-first', 100 -> 'one hundredth'."""
    w = int_to_words(n)
    head, sep, last = w.rpartition(" ")
    pre, dash, lastw = last.rpartition("-")
    if lastw in _ORD_IRREG:
        lastw = _ORD_IRREG[lastw]
    elif lastw.endswith("y"):
        lastw = lastw[:-1] + "ieth"
    else:
        lastw = lastw + "th"
    return head + sep + pre + dash + lastw


def year_words(y: int) -> str:
    """1601 -> 'sixteen oh one', 2023 -> 'twenty twenty-three', 2005 -> 'two thousand five'."""
    y = int(y)
    if y < 1000 or y > 9999:
        return int_to_words(y)
    if 2000 <= y <= 2009:
        return "two thousand" + (" " + _ONES[y - 2000] if y > 2000 else "")
    hi, lo = divmod(y, 100)
    if hi % 10 == 0 and lo == 0:          # 1000, 3000
        return int_to_words(y)
    if lo == 0:
        return int_to_words(hi) + " hundred"
    if lo < 10:
        return int_to_words(hi) + " oh " + _ONES[lo]
    return int_to_words(hi) + " " + int_to_words(lo)


def _plural_number(words: str) -> str:
    """'nineteen ninety' -> 'nineteen nineties', 'two thousand' -> 'two thousands'."""
    if words.endswith("y"):
        return words[:-1] + "ies"
    if words.endswith("x"):
        return words + "es"
    return words + "s"


def number_words(s: str) -> str:
    """Speak a numeric string: '1,234' -> words, '3.5' -> 'three point five'."""
    s = s.replace(",", "")
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    if "." in s:
        parts = s.split(".")
        head = int_to_words(int(parts[0])) if parts[0] else ""
        tail = " point ".join(digits_to_words(p) for p in parts[1:])
        out = (head + " point " + tail).strip()
    elif len(s) > 1 and s.startswith("0"):
        out = digits_to_words(s)
    elif len(s) > 15:
        out = digits_to_words(s)
    else:
        out = int_to_words(int(s))
    return ("minus " + out) if neg else out


# ---------------------------------------------------------------- patterns
_TLDS = ("com|org|net|edu|gov|io|ai|co|uk|de|fr|info|biz|us|ca|au|eu|nl|ch|it|es|"
         "jp|cn|in|ru|br|dev|app|me|tv|ly")
_URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>()\"']+", re.I)
_DOMAIN_RE = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:%s))\b(?:/[^\s]*)?" % _TLDS, re.I)
_EMAIL_RE = re.compile(r"\b([\w.+-]+)@([\w-]+(?:\.[\w-]+)+)\b")

_EXT_SPOKEN = {"pdf": "P D F", "csv": "C S V", "xlsx": "X L S X", "xls": "X L S",
               "json": "J S O N", "yaml": "yaml", "yml": "Y M L", "toml": "toml",
               "md": "M D", "py": "P Y", "txt": "T X T", "html": "H T M L",
               "htm": "H T M", "js": "J S", "ts": "T S", "ini": "I N I",
               "cfg": "C F G", "docx": "D O C X", "pptx": "P P T X",
               "png": "P N G", "jpg": "J P G", "jpeg": "J P E G", "log": "log",
               "sh": "S H", "xml": "X M L", "ipynb": "I P Y N B", "env": "env",
               "lock": "lock", "rs": "R S", "go": "go", "java": "java",
               "c": "C", "h": "H", "cpp": "C P P", "sql": "S Q L", "wav": "wave",
               "zip": "zip", "gitignore": "git ignore", "dockerfile": "docker file"}
_EXT_ALT = "|".join(sorted(_EXT_SPOKEN, key=len, reverse=True))
_FILE_RE = re.compile(r"(?<![\w.])([\w-]*[A-Za-z_][\w-]*)?\.(%s)\b(?![\w-])" % _EXT_ALT, re.I)

_CODE_RE = re.compile(
    r"\b(?:[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)+"      # ABC-4, GPT-4, X_Y
    r"|[A-Z]+[0-9]+[A-Z0-9]*"                     # A4, B2B
    r"|[0-9]+[A-Z][A-Z0-9]*)\b")                   # 4K, 3D
_CURRENCY_RE = re.compile(
    r"(US\$|\$|£|€|¥)\s?(\d[\d,]*(?:\.\d+)?)"
    r"(?:\s?(trillion|billion|million|thousand|bn|tn|[kKmMbB])\b)?")
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MDY_RE = re.compile(r"\b(%s)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*|\s+)(\d{4})\b" % _MONTH_RE)
_DMY_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(%s)(?:,)?\s+(\d{4})\b" % _MONTH_RE)
_MD_RE = re.compile(r"\b(%s)\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!\s*(?:%%|,?\s*\d))" % _MONTH_RE)
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})(?:\s?([ap])\.?m\.?)?(?![\d:])", re.I)
_PERCENT_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s?%")
_ORDINAL_RE = re.compile(r"\b(\d[\d,]*)(st|nd|rd|th)\b", re.I)
_DECADE_RE = re.compile(r"(?:\b|')(\d{4}|\d{2})'?s\b")
_YEAR_RANGE_RE = re.compile(r"\b(1[1-9]\d\d|20\d\d)\s?[-–—]\s?(1[1-9]\d\d|20\d\d)\b")
_NUM_RANGE_RE = re.compile(r"(?<![\w.,-])(\d[\d,]*(?:\.\d+)?)\s?[–—-]\s?(\d[\d,]*(?:\.\d+)?)\b")
_FRACTION_RE = re.compile(r"(?<![\w/])(\d+)/(\d+)(?![\w/])")
_YEAR_RE = re.compile(r"(?<![\d.,$£€¥-])\b(1[1-9]\d\d|20\d\d)\b(?![.,]\d|\s?%)")
_NUMBER_RE = re.compile(r"(?<![\w.])(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)*|\.\d+)\b")
_LETTER_DIGIT_RE = re.compile(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")

_COUNT_NOUNS = {"people", "persons", "employees", "votes", "copies", "units", "items",
                "times", "pages", "words", "rows", "lines", "km", "miles", "meters",
                "metres", "feet", "kg", "tons", "tonnes", "dollars", "points",
                "students", "users", "cases", "deaths", "seats", "members", "cars",
                "km²", "acres", "hectares", "calories", "files", "documents"}
_UNITS = {"km": "kilometres", "kg": "kilograms", "cm": "centimetres",
          "mm": "millimetres", "mph": "miles per hour", "kmh": "kilometres per hour",
          "ms": "milliseconds", "gb": "gigabytes", "mb": "megabytes", "kb": "kilobytes",
          "tb": "terabytes", "ghz": "gigahertz", "mhz": "megahertz", "kw": "kilowatts",
          "mw": "megawatts", "lbs": "pounds", "lb": "pounds", "ft": "feet", "sq": "square"}
_CUR_NAME = {"$": ("dollar", "dollars", "cent", "cents"),
             "US$": ("US dollar", "US dollars", "cent", "cents"),
             "£": ("pound", "pounds", "penny", "pence"),
             "€": ("euro", "euros", "cent", "cents"),
             "¥": ("yen", "yen", "sen", "sen")}
_SCALE_WORD = {"k": "thousand", "m": "million", "b": "billion", "bn": "billion",
               "tn": "trillion", "thousand": "thousand", "million": "million",
               "billion": "billion", "trillion": "trillion"}


def _month(name: str) -> str:
    n = name.rstrip(".")
    if n.lower() in _MONTH_ABBR:
        return _MONTH_ABBR[n.lower()]
    return n[:1].upper() + n[1:].lower()


def _date(month: str, day: int, year=None) -> str:
    out = month + " " + ordinal_words(day)
    if year is not None:
        out += ", " + year_words(int(year))
    return out


def _spell_code(tok: str) -> str:
    """'ABC-4' -> 'A B C dash four'; long letter runs stay words."""
    parts = []
    for run in re.findall(r"[A-Za-z]+|\d+|[-_.]", tok):
        if run == "-":
            parts.append("dash")
        elif run == "_":
            parts.append("underscore")
        elif run == ".":
            parts.append("dot")
        elif run.isdigit():
            parts.append(digits_to_words(run) if (run.startswith("0") and len(run) > 1)
                         or len(run) > 4 else int_to_words(int(run)))
        elif len(run) <= 4 and run.isupper():
            parts.append(" ".join(run))
        else:
            parts.append(run)
    return " ".join(parts)


def _domain_words(host: str) -> str:
    host = re.sub(r"^www\.", "", host.lower())
    return " dot ".join(p.replace("-", " dash ") for p in host.split(".") if p)


# --------------------------------------------------------------- rewriters
def _sub_urls(t: str) -> str:
    def url(m):
        raw = m.group(0).rstrip(".,;:!?")
        trail = m.group(0)[len(raw):]
        host = re.sub(r"^(?:https?://)?(?:www\.)?", "", raw, flags=re.I).split("/")[0]
        host = host.split("?")[0].split("#")[0].split(":")[0]
        return _domain_words(host) + trail
    t = _EMAIL_RE.sub(lambda m: m.group(1).replace(".", " dot ") + " at "
                      + _domain_words(m.group(2)), t)
    t = _URL_RE.sub(url, t)
    return _DOMAIN_RE.sub(lambda m: _domain_words(m.group(1)), t)


def _sub_files(t: str) -> str:
    def f(m):
        name, ext = m.group(1) or "", m.group(2)
        spoken = _EXT_SPOKEN[ext.lower()]
        name = name.replace("_", " underscore ")
        return ((name + " ") if name else "") + "dot " + spoken
    return _FILE_RE.sub(f, t)


def _sub_currency(t: str) -> str:
    def f(m):
        sym, num, scale = m.group(1), m.group(2).replace(",", ""), m.group(3)
        one, many, sub1, subn = _CUR_NAME[sym]
        if scale:
            return number_words(num) + " " + _SCALE_WORD[scale.lower()] + " " + many
        if "." in num:
            whole, frac = num.split(".", 1)
            if len(frac) == 2:
                w, c = int(whole or 0), int(frac)
                parts = []
                if w:
                    parts.append(int_to_words(w) + " " + (one if w == 1 else many))
                if c:
                    parts.append(int_to_words(c) + " " + (sub1 if c == 1 else subn))
                return " and ".join(parts) or ("zero " + many)
            return number_words(num) + " " + many
        n = int(num)
        return int_to_words(n) + " " + (one if n == 1 else many)
    return _CURRENCY_RE.sub(f, t)


def _sub_dates(t: str) -> str:
    def iso(m):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return _date(MONTHS[mo - 1], d, y)
        return m.group(0)

    def slash(m):
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return _date(MONTHS[mo - 1], d, y)
        if 1 <= d <= 12 and 1 <= mo <= 31:      # day/month/year
            return _date(MONTHS[d - 1], mo, y)
        return m.group(0)
    t = _ISO_DATE_RE.sub(iso, t)
    t = _SLASH_DATE_RE.sub(slash, t)
    t = _MDY_RE.sub(lambda m: _date(_month(m.group(1)), int(m.group(2)), m.group(3)), t)
    t = _DMY_RE.sub(lambda m: _date(_month(m.group(2)), int(m.group(1)), m.group(3)), t)
    t = _MD_RE.sub(lambda m: _date(_month(m.group(1)), int(m.group(2)))
                   if 1 <= int(m.group(2)) <= 31 else m.group(0), t)
    return t


def _sub_times(t: str) -> str:
    def f(m):
        h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
        if h > 23 or mi > 59:
            return m.group(0)
        out = int_to_words(h)
        if mi == 0:
            out += "" if ap else " o'clock"
        elif mi < 10:
            out += " oh " + _ONES[mi]
        else:
            out += " " + int_to_words(mi)
        if ap:
            out += " " + ap.lower() + " m"
        return out
    return _TIME_RE.sub(f, t)


def _sub_decades(t: str) -> str:
    def f(m):
        s = m.group(1)
        if len(s) == 4:
            y = int(s)
            if y % 10:
                return m.group(0)
            if y % 100 == 0 and y % 1000 == 0:
                return _plural_number(int_to_words(y))
            if 2000 <= y <= 2009:
                return "two thousands"
            if y % 100 == 0:
                return _plural_number(int_to_words(y // 100) + " hundred")
            return _plural_number(year_words(y))
        n = int(s)
        if n % 10 or n < 20:
            return m.group(0)
        return _plural_number(int_to_words(n))
    return _DECADE_RE.sub(f, t)


def _sub_years(t: str) -> str:
    def f(m):
        nxt = re.match(r"\s*([A-Za-z²]+)", t[m.end():])
        if nxt and nxt.group(1).lower() in _COUNT_NOUNS:
            return m.group(0)
        return year_words(int(m.group(1)))
    return _YEAR_RE.sub(f, t)


def _sub_fractions(t: str) -> str:
    def f(m):
        a, b = int(m.group(1)), int(m.group(2))
        if b == 0:
            return m.group(0)
        if b == 2:
            den = "half" if a == 1 else "halves"
        elif b == 4:
            den = "quarter" if a == 1 else "quarters"
        else:
            return int_to_words(a) + " over " + int_to_words(b)
        return int_to_words(a) + " " + den
    return _FRACTION_RE.sub(f, t)


def _sub_symbols(t: str) -> str:
    t = re.sub(r"\band/or\b", "and or", t, flags=re.I)
    t = re.sub(r"\s*&\s*", " and ", t)
    t = re.sub(r"(?<=\s)~\s?(?=\d)|^~\s?(?=\d)", "about ", t)
    t = re.sub(r"\s*\+\s*", " plus ", t)
    t = re.sub(r"\s+=\s+|=", " equals ", t)
    t = re.sub(r"\s*×\s*", " times ", t)
    t = re.sub(r"°\s?C\b", " degrees Celsius", t)
    t = re.sub(r"°\s?F\b", " degrees Fahrenheit", t)
    t = re.sub(r"°", " degrees", t)
    t = re.sub(r"\s*%", " percent", t)
    t = re.sub(r"\s*@\s*", " at ", t)
    t = re.sub(r"(?<=\w)\s*/\s*(?=\w)", " slash ", t)
    t = re.sub(r"\s+[-–—]+\s+", ", ", t)          # spaced dash = pause
    t = re.sub(r"(?<=\w)[–—](?=\w)", " to ", t)
    t = t.replace("_", " ")
    return t


def normalise(text: str) -> str:
    """Return speakable text (see module doc). Idempotent on its own output."""
    if not text:
        return ""
    t = str(text).replace(" ", " ").replace("’", "'")
    t = _sub_urls(t)
    t = re.sub(r"#\s?(?=\d)", "number ", t)
    t = _sub_files(t)
    t = _CODE_RE.sub(lambda m: _spell_code(m.group(0)), t)
    t = _sub_currency(t)
    t = _sub_dates(t)
    t = _sub_times(t)
    t = _PERCENT_RE.sub(lambda m: number_words(m.group(1)) + " percent", t)
    t = _ORDINAL_RE.sub(lambda m: ordinal_words(int(m.group(1).replace(",", ""))), t)
    t = _sub_decades(t)
    t = _YEAR_RANGE_RE.sub(lambda m: year_words(int(m.group(1))) + " to "
                           + year_words(int(m.group(2))), t)
    t = _sub_years(t)
    t = _sub_fractions(t)
    t = _NUM_RANGE_RE.sub(lambda m: number_words(m.group(1)) + " to "
                          + number_words(m.group(2)), t)
    # numbers glued to units: 10km -> 10 kilometres
    t = re.sub(r"(\d)\s?(%s)\b" % "|".join(_UNITS), lambda m: m.group(1) + " "
               + _UNITS[m.group(2).lower()], t, flags=re.I)
    t = _LETTER_DIGIT_RE.sub(" ", t)
    t = _NUMBER_RE.sub(lambda m: number_words(m.group(1)), t)
    t = _sub_symbols(t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t
