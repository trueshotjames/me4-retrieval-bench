"""Markdown (the subset htmlmd produces) -> txt / html / docx / odt, standard library only.

Used by the FRAMES fetch so that a share of the articles is stored in other file types, which
exercises a project's file readers. Pure functions: bytes out, no files touched.
Supported: headings, paragraphs, bullet/numbered lists, pipe tables, code blocks, **bold**, *italic*.
"""
from __future__ import annotations

import hashlib
import html
import io
import re
import zipfile

FORMATS = ("docx", "html", "txt", "odt")   # no pdf, no rtf (rtf is ~1.6x larger and adds nothing)
DEFAULT_SHARE = 0.30


def choose_format(title: str, share: float = DEFAULT_SHARE) -> str:
    """'md' for most articles; otherwise one of FORMATS. Fixed by the title, so every machine
    and every run picks the same file type for the same article."""
    if share <= 0:
        return "md"
    n = int(hashlib.sha1(title.encode("utf-8")).hexdigest(), 16)
    if (n % 1000) / 1000 >= share:
        return "md"
    return FORMATS[(n // 1000) % len(FORMATS)]


# ---- parsing -------------------------------------------------------------------------------
_INLINE = re.compile(r"(\*\*.+?\*\*|\*[^*\s][^*]*?\*)")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")   # not allowed in XML 1.0


def _clean(s: str) -> str:
    return _CTRL.sub("", s)


def _split_row(line: str) -> list:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", inner)]


def parse(md: str) -> list:
    """-> [('h', level, text) | ('p', text) | ('li', depth, mark, text) | ('table', rows) | ('code', text)]"""
    out, lines, i = [], _clean(md).split("\n"), 0
    para: list = []

    def flush():
        if para:
            out.append(("p", " ".join(x.strip() for x in para)))
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("```"):
            flush()
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            out.append(("code", "\n".join(buf)))
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            flush()
            out.append(("h", len(m.group(1)), m.group(2).strip()))
        elif ln.lstrip().startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = _split_row(lines[i])
                if not all(re.fullmatch(r":?-{2,}:?", c) or c == "" for c in cells) or not any(cells):
                    rows.append(cells)
                i += 1
            if rows:
                out.append(("table", rows))
            continue
        else:
            m = re.match(r"^(\s*)(-|\d+\.)\s+(.*)$", ln)
            if m:
                flush()
                out.append(("li", len(m.group(1)) // 2, "•" if m.group(2) == "-" else m.group(2), m.group(3)))
            elif not ln.strip():
                flush()
            else:
                para.append(ln)
        i += 1
    flush()
    return out


def _runs(text: str) -> list:
    """-> [(text, bold, italic)]"""
    res = []
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            res.append((part[2:-2], True, False))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            res.append((part[1:-1], False, True))
        else:
            res.append((part, False, False))
    return res


def _plain(text: str) -> str:
    return "".join(t for t, _, _ in _runs(text))


# ---- txt -----------------------------------------------------------------------------------
def to_txt(md: str) -> bytes:
    lines = []
    for b in parse(md):
        if b[0] == "h":
            lines += ["", _plain(b[2]).upper() if b[1] == 1 else _plain(b[2]), ""]
        elif b[0] == "p":
            lines += [_plain(b[1]), ""]
        elif b[0] == "li":
            lines.append("  " * b[1] + f"{b[2]} {_plain(b[3])}")
        elif b[0] == "table":
            lines += [""] + ["\t".join(_plain(c) for c in r) for r in b[1]] + [""]
        elif b[0] == "code":
            lines += [b[1], ""]
    return (re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n").encode("utf-8")


# ---- html ----------------------------------------------------------------------------------
def _h(text: str) -> str:
    out = []
    for t, b, i in _runs(text):
        t = html.escape(t)
        out.append(f"<strong>{t}</strong>" if b else f"<em>{t}</em>" if i else t)
    return "".join(out)


def to_html(md: str, title: str = "") -> bytes:
    body, in_list = [], False
    for b in parse(md):
        if b[0] != "li" and in_list:
            body.append("</ul>")
            in_list = False
        if b[0] == "h":
            body.append(f"<h{b[1]}>{_h(b[2])}</h{b[1]}>")
        elif b[0] == "p":
            body.append(f"<p>{_h(b[1])}</p>")
        elif b[0] == "li":
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{'' if b[2] == '•' else html.escape(b[2]) + ' '}{_h(b[3])}</li>")
        elif b[0] == "table":
            rows = "".join("<tr>" + "".join(f"<td>{_h(c)}</td>" for c in r) + "</tr>" for r in b[1])
            body.append(f"<table border=\"1\">{rows}</table>")
        elif b[0] == "code":
            body.append(f"<pre>{html.escape(b[1])}</pre>")
    if in_list:
        body.append("</ul>")
    doc = ("<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
           f"<title>{html.escape(title)}</title></head>\n<body>\n" + "\n".join(body) + "\n</body></html>\n")
    return doc.encode("utf-8")


# ---- docx ----------------------------------------------------------------------------------
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _x(t: str) -> str:
    return html.escape(t, quote=False)


def _w_runs(text: str, extra: str = "") -> str:
    out = []
    for t, b, i in _runs(text):
        rpr = ("<w:b/>" if b else "") + ("<w:i/>" if i else "") + extra
        out.append(f"<w:r>{'<w:rPr>' + rpr + '</w:rPr>' if rpr else ''}"
                   f"<w:t xml:space=\"preserve\">{_x(t)}</w:t></w:r>")
    return "".join(out)


_MONO = "<w:rFonts w:ascii=\"Courier New\" w:hAnsi=\"Courier New\"/>"


def to_docx(md: str) -> bytes:
    body = []
    for b in parse(md):
        if b[0] == "h":
            body.append(f"<w:p><w:pPr><w:pStyle w:val=\"Heading{min(b[1], 6)}\"/></w:pPr>{_w_runs(b[2])}</w:p>")
        elif b[0] == "p":
            body.append(f"<w:p>{_w_runs(b[1])}</w:p>")
        elif b[0] == "li":
            ind = 360 * (b[1] + 1)
            body.append(f"<w:p><w:pPr><w:ind w:left=\"{ind}\" w:hanging=\"260\"/></w:pPr>"
                        f"<w:r><w:t xml:space=\"preserve\">{_x(b[2])} </w:t></w:r>{_w_runs(b[3])}</w:p>")
        elif b[0] == "table":
            w = max(len(r) for r in b[1])
            rows = []
            for r in b[1]:
                r = r + [""] * (w - len(r))
                rows.append("<w:tr>" + "".join(f"<w:tc><w:p>{_w_runs(c)}</w:p></w:tc>" for c in r) + "</w:tr>")
            body.append("<w:tbl><w:tblPr><w:tblBorders>"
                        + "".join(f"<w:{s} w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"999999\"/>"
                                  for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
                        + "</w:tblBorders></w:tblPr>" + "".join(rows) + "</w:tbl><w:p/>")
        elif b[0] == "code":
            for ln in b[1].split("\n"):
                body.append(f"<w:p>{_w_runs(ln, _MONO)}</w:p>")
    doc = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<w:document xmlns:w=\"{_W}\"><w:body>"
           + "".join(body) + "<w:sectPr/></w:body></w:document>")
    styles = (f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n<w:styles xmlns:w=\"{_W}\">"
              + "".join(f"<w:style w:type=\"paragraph\" w:styleId=\"Heading{n}\"><w:name w:val=\"heading {n}\"/>"
                        f"<w:pPr><w:keepNext/><w:outlineLvl w:val=\"{n - 1}\"/></w:pPr>"
                        f"<w:rPr><w:b/><w:sz w:val=\"{max(24, 44 - 4 * n)}\"/></w:rPr></w:style>" for n in range(1, 7))
              + "</w:styles>")
    ct = ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
          "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
          "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
          "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
          "<Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>"
          "<Override PartName=\"/word/styles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml\"/>"
          "</Types>")
    rels = ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
            "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>"
            "</Relationships>")
    drels = ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
             "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
             "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles\" Target=\"styles.xml\"/>"
             "</Relationships>")
    return _zip([("[Content_Types].xml", ct), ("_rels/.rels", rels), ("word/document.xml", doc),
                 ("word/_rels/document.xml.rels", drels), ("word/styles.xml", styles)])


# ---- odt -----------------------------------------------------------------------------------
def _o_runs(text: str) -> str:
    out = []
    for t, b, i in _runs(text):
        t = _x(t)
        out.append(f"<text:span text:style-name=\"{'B' if b else 'I'}\">{t}</text:span>" if (b or i) else t)
    return "".join(out)


def to_odt(md: str) -> bytes:
    body, tn = [], 0
    for b in parse(md):
        if b[0] == "h":
            body.append(f"<text:h text:outline-level=\"{min(b[1], 6)}\">{_o_runs(b[2])}</text:h>")
        elif b[0] == "p":
            body.append(f"<text:p>{_o_runs(b[1])}</text:p>")
        elif b[0] == "li":
            body.append(f"<text:p>{'&#160;&#160;' * b[1]}{_x(b[2])} {_o_runs(b[3])}</text:p>")
        elif b[0] == "table":
            tn += 1
            w = max(len(r) for r in b[1])
            rows = "".join("<table:table-row>" + "".join(
                f"<table:table-cell office:value-type=\"string\"><text:p>{_o_runs(c)}</text:p></table:table-cell>"
                for c in r + [""] * (w - len(r))) + "</table:table-row>" for r in b[1])
            body.append(f"<table:table table:name=\"T{tn}\"><table:table-column table:number-columns-repeated=\"{w}\"/>"
                        f"{rows}</table:table>")
        elif b[0] == "code":
            body.append("".join(f"<text:p>{_x(ln)}</text:p>" for ln in b[1].split("\n")))
    ns = ("xmlns:office=\"urn:oasis:names:tc:opendocument:xmlns:office:1.0\" "
          "xmlns:text=\"urn:oasis:names:tc:opendocument:xmlns:text:1.0\" "
          "xmlns:table=\"urn:oasis:names:tc:opendocument:xmlns:table:1.0\" "
          "xmlns:style=\"urn:oasis:names:tc:opendocument:xmlns:style:1.0\" "
          "xmlns:fo=\"urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0\"")
    content = (f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<office:document-content {ns} office:version=\"1.2\">"
               "<office:automatic-styles>"
               "<style:style style:name=\"B\" style:family=\"text\"><style:text-properties fo:font-weight=\"bold\"/></style:style>"
               "<style:style style:name=\"I\" style:family=\"text\"><style:text-properties fo:font-style=\"italic\"/></style:style>"
               "</office:automatic-styles><office:body><office:text>" + "".join(body) + "</office:text></office:body></office:document-content>")
    manifest = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
                "<manifest:manifest xmlns:manifest=\"urn:oasis:names:tc:opendocument:xmlns:manifest:1.0\" manifest:version=\"1.2\">"
                "<manifest:file-entry manifest:full-path=\"/\" manifest:media-type=\"application/vnd.oasis.opendocument.text\"/>"
                "<manifest:file-entry manifest:full-path=\"content.xml\" manifest:media-type=\"text/xml\"/>"
                "</manifest:manifest>")
    # the mimetype entry must be first and stored uncompressed
    return _zip([("mimetype", "application/vnd.oasis.opendocument.text"), ("content.xml", content),
                 ("META-INF/manifest.xml", manifest)], stored_first=True)


def _zip(files: list, stored_first: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for k, (name, data) in enumerate(files):
            zi = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))   # fixed time: reproducible bytes
            zi.compress_type = zipfile.ZIP_STORED if (stored_first and k == 0) else zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
    return buf.getvalue()


def convert(md: str, fmt: str, title: str = "") -> bytes:
    if fmt == "txt":
        return to_txt(md)
    if fmt == "html":
        return to_html(md, title)
    if fmt == "docx":
        return to_docx(md)
    if fmt == "odt":
        return to_odt(md)
    raise ValueError(f"unsupported format: {fmt}")
