"""Spreadsheet file types for the MiMoTable fetch: .xlsx -> .ods / .csv / .tsv (standard library only),
and .xls through LibreOffice when it is installed.

Used so that a share of the MiMoTable workbooks is stored in other file types, which exercises a
project's spreadsheet readers. The reader covers what the MiMoTable files use (inline and shared
strings, numbers, booleans, errors, dates and times by number format, merged cells, several sheets).
Cell values are copied; formulas, fonts, colours and column widths are not.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import html
import io
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_SHARE = 0.30
_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_BUILTIN_DATE = {14: "d", 15: "d", 16: "d", 17: "d", 18: "t", 19: "t", 20: "t", 21: "t", 22: "dt",
                 45: "t", 46: "t", 47: "t"}
_BUILTIN_PCT = {9: 0, 10: 2}


# ---- reading .xlsx -------------------------------------------------------------------------
def col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def _ref(ref: str):
    m = re.fullmatch(r"\$?([A-Z]+)\$?(\d+)", ref)
    return int(m.group(2)), col_index(m.group(1))


def _fmt_kind(code: str):
    """-> ('d'|'t'|'dt', None) for dates/times, ('pct', decimals) for percentages, or (None, None)."""
    c = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.|_.|\*.', "", code).lower()
    if "%" in c:
        m = re.search(r"\.(0+)", c)
        return "pct", len(m.group(1)) if m else 0
    has_d, has_t = bool(re.search(r"[yd]", c)) or "mmm" in c, bool(re.search(r"[hs]", c))
    if has_d and has_t:
        return "dt", None
    if has_d:
        return "d", None
    if has_t:
        return "t", None
    return None, None


def _text(el) -> str:
    """Text of an <is> or <si> element: <t>, or rich-text runs <r><t>, without phonetic <rPh>."""
    if el is None:
        return ""
    t = el.find(_M + "t")
    if t is not None and not el.findall(_M + "r"):
        return t.text or ""
    return "".join(x.text or "" for r in el.findall(_M + "r") for x in r.findall(_M + "t"))


def read_xlsx(path) -> list:
    """-> [{'name', 'cells': {(row, col): (kind, value)}, 'merges': [(r1, c1, r2, c2)], 'rows', 'cols'}]
    kind: 's' text, 'n' number (value is the stored number text), 'b' boolean, 'e' error text,
    'd' date, 'dt' date and time, 't' time of day (value is a datetime / time), 'pct' (value, decimals)."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        pr = wb.find(_M + "workbookPr")
        epoch = dt.datetime(1904, 1, 1) if pr is not None and pr.get("date1904") in ("1", "true") \
            else dt.datetime(1899, 12, 30)
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).iter(_PR + "Relationship"):
                t = r.get("Target", "")
                rels[r.get("Id")] = t.lstrip("/") if t.startswith("/") else posixpath.normpath("xl/" + t)
        sst = []
        if "xl/sharedStrings.xml" in names:
            sst = [_text(si) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(_M + "si")]
        xf_kind = []
        if "xl/styles.xml" in names:
            st = ET.fromstring(z.read("xl/styles.xml"))
            fmts = {int(f.get("numFmtId")): f.get("formatCode", "") for f in st.iter(_M + "numFmt")}
            xfs = st.find(_M + "cellXfs")
            for xf in (xfs if xfs is not None else []):
                i = int(xf.get("numFmtId", "0"))
                if i in fmts:
                    xf_kind.append(_fmt_kind(fmts[i]))
                elif i in _BUILTIN_DATE:
                    xf_kind.append((_BUILTIN_DATE[i], None))
                elif i in _BUILTIN_PCT:
                    xf_kind.append(("pct", _BUILTIN_PCT[i]))
                else:
                    xf_kind.append((None, None))
        sheets = []
        for k, sh in enumerate(wb.iter(_M + "sheet")):
            target = rels.get(sh.get(_R + "id"), f"xl/worksheets/sheet{k + 1}.xml")
            root = ET.fromstring(z.read(target))
            cells, maxr, maxc = {}, 0, 0
            rn = 0
            for row in root.iter(_M + "row"):
                rn = int(row.get("r")) if row.get("r") else rn + 1
                cn = 0
                for c in row.findall(_M + "c"):
                    if c.get("r"):
                        rn, cn = _ref(c.get("r"))
                    else:
                        cn += 1
                    v = _cell(c, sst, xf_kind, epoch)
                    if v is not None:
                        cells[(rn, cn)] = v
                        maxr, maxc = max(maxr, rn), max(maxc, cn)
            merges = []
            for m in root.iter(_M + "mergeCell"):
                a, _, b = m.get("ref", "").partition(":")
                if a and b:
                    (r1, c1), (r2, c2) = _ref(a), _ref(b)
                    merges.append((r1, c1, r2, c2))
                    maxr, maxc = max(maxr, r2), max(maxc, c2)
            sheets.append({"name": sh.get("name", f"Sheet{k + 1}"), "cells": cells, "merges": merges,
                           "rows": maxr, "cols": maxc})
    return sheets


def _cell(c, sst, xf_kind, epoch):
    t = c.get("t", "n")
    if t == "inlineStr":
        s = _text(c.find(_M + "is"))
        return ("s", s) if s != "" else None
    v = c.find(_M + "v")
    v = v.text if v is not None else None
    if v is None or v == "":
        return None
    if t == "s":
        s = sst[int(v)] if int(v) < len(sst) else ""
        return ("s", s) if s != "" else None
    if t == "str":
        return ("s", v)
    if t == "b":
        return ("b", v in ("1", "true"))
    if t == "e":
        return ("e", v)
    if t == "d":   # ISO date text
        try:
            return ("dt", dt.datetime.fromisoformat(v.replace("Z", "")))
        except ValueError:
            return ("s", v)
    s = int(c.get("s", "0"))
    kind, extra = xf_kind[s] if s < len(xf_kind) else (None, None)
    try:
        x = float(v)
    except ValueError:
        return ("s", v)
    if kind in ("d", "dt", "t"):
        if kind == "t" and x >= 1:   # a duration or a stray date: keep as date-time
            kind = "dt"
        when = epoch + dt.timedelta(seconds=round(x * 86400))
        if kind == "t":
            return ("t", when.time())
        if kind == "dt" and when.time() == dt.time(0):
            kind = "d"
        return (kind, when)
    if kind == "pct":
        return ("pct", (v, extra))
    return ("n", v)


# ---- display text (csv/tsv cells and the visible text in .ods) -----------------------------
def _num(v: str) -> str:
    try:
        x = float(v)
    except ValueError:
        return v
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return repr(x) if "e" not in v.lower() else v


def display(cell) -> str:
    kind, v = cell
    if kind == "s" or kind == "e":
        return v
    if kind == "n":
        return _num(v)
    if kind == "b":
        return "TRUE" if v else "FALSE"
    if kind == "d":
        return v.strftime("%Y-%m-%d")
    if kind == "dt":
        return v.strftime("%Y-%m-%d %H:%M:%S" if v.second else "%Y-%m-%d %H:%M")
    if kind == "t":
        return v.strftime("%H:%M:%S" if v.second else "%H:%M")
    if kind == "pct":
        raw, dec = v
        return f"{float(raw) * 100:.{dec}f}%"
    return str(v)


# ---- writing .csv / .tsv -------------------------------------------------------------------
def to_delimited(sheet: dict, sep: str = ",") -> bytes:
    """One sheet as UTF-8 text. Trailing empty cells and rows are dropped."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep, lineterminator="\n")
    rows = []
    for r in range(1, sheet["rows"] + 1):
        vals = [display(sheet["cells"][(r, c)]) if (r, c) in sheet["cells"] else "" for c in range(1, sheet["cols"] + 1)]
        while vals and vals[-1] == "":
            vals.pop()
        rows.append(vals)
    while rows and not rows[-1]:
        rows.pop()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


# ---- writing .ods --------------------------------------------------------------------------
_NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
       'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
       'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
       'xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0" '
       'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"')
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _x(s: str) -> str:
    return html.escape(_CTRL.sub("", s), quote=True)


def _paras(s: str) -> str:
    """Cell text as <text:p> lines; runs of spaces and tabs kept (ODF collapses them otherwise)."""
    out = []
    for line in s.split("\n"):
        line = _x(line.replace("\r", ""))
        line = line.replace("\t", "<text:tab/>")
        line = re.sub(r"(?<= ) +|^ +", lambda m: f'<text:s text:c="{len(m.group(0))}"/>', line)
        out.append(f"<text:p>{line}</text:p>")
    return "".join(out)


def _ods_cell(cell, span: str, pct_styles: dict) -> str:
    kind, v = cell
    txt = _paras(display(cell))
    if kind == "n":
        a = f'office:value-type="float" office:value="{_x(v)}"'
    elif kind == "b":
        a = f'office:value-type="boolean" office:boolean-value="{"true" if v else "false"}" table:style-name="ceB"'
    elif kind == "d":
        a = f'office:value-type="date" office:date-value="{v.strftime("%Y-%m-%d")}" table:style-name="ceD"'
    elif kind == "dt":
        a = f'office:value-type="date" office:date-value="{v.strftime("%Y-%m-%dT%H:%M:%S")}" table:style-name="ceDT"'
    elif kind == "t":
        a = f'office:value-type="time" office:time-value="PT{v.hour:02d}H{v.minute:02d}M{v.second:02d}S" table:style-name="ceT"'
    elif kind == "pct":
        raw, dec = v
        a = f'office:value-type="percentage" office:value="{_x(raw)}" table:style-name="ceP{dec}"'
        pct_styles[dec] = True
    else:
        a = 'office:value-type="string"'
    return f"<table:table-cell {a}{span}>{txt}</table:table-cell>"


def _ods_table(sheet: dict, pct_styles: dict) -> str:
    covered, spans = set(), {}
    for r1, c1, r2, c2 in sheet["merges"]:
        if (r1, c1) == (r2, c2):
            continue
        spans[(r1, c1)] = (r2 - r1 + 1, c2 - c1 + 1)
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                if (r, c) != (r1, c1):
                    covered.add((r, c))
    ncols = max(sheet["cols"], 1)
    rows, empty_run = [], 0
    empty_row = f'<table:table-row{{rep}}><table:table-cell table:number-columns-repeated="{ncols}"/></table:table-row>'

    def flush_empty():
        nonlocal empty_run
        if empty_run:
            rows.append(empty_row.replace("{rep}", f' table:number-rows-repeated="{empty_run}"' if empty_run > 1 else ""))
            empty_run = 0

    for r in range(1, sheet["rows"] + 1):
        parts, blank = [], 0
        any_content = False
        for c in range(1, ncols + 1):
            cell = sheet["cells"].get((r, c))
            sp = spans.get((r, c))
            if (r, c) in covered:
                if blank:
                    parts.append(f'<table:table-cell table:number-columns-repeated="{blank}"/>' if blank > 1 else "<table:table-cell/>")
                    blank = 0
                parts.append("<table:covered-table-cell/>")
                any_content = True
                continue
            if cell is None and sp is None:
                blank += 1
                continue
            if blank:
                parts.append(f'<table:table-cell table:number-columns-repeated="{blank}"/>' if blank > 1 else "<table:table-cell/>")
                blank = 0
            span = f' table:number-rows-spanned="{sp[0]}" table:number-columns-spanned="{sp[1]}"' if sp else ""
            parts.append(_ods_cell(cell, span, pct_styles) if cell is not None else f"<table:table-cell{span}/>")
            any_content = True
        if not any_content:
            empty_run += 1
            continue
        flush_empty()
        if blank:
            parts.append(f'<table:table-cell table:number-columns-repeated="{blank}"/>' if blank > 1 else "<table:table-cell/>")
        rows.append("<table:table-row>" + "".join(parts) + "</table:table-row>")
    flush_empty()
    if not rows:
        rows.append(empty_row.replace("{rep}", ""))
    return (f'<table:table table:name="{_x(sheet["name"])}">'
            f'<table:table-column table:number-columns-repeated="{ncols}"/>' + "".join(rows) + "</table:table>")


def to_ods(sheets: list) -> bytes:
    pct = {}
    tables = "".join(_ods_table(s, pct) for s in sheets)
    two = 'number:style="long"'
    styles = (f'<number:date-style style:name="ND"><number:year {two}/><number:text>-</number:text>'
              f'<number:month {two}/><number:text>-</number:text><number:day {two}/></number:date-style>'
              f'<number:date-style style:name="NDT"><number:year {two}/><number:text>-</number:text>'
              f'<number:month {two}/><number:text>-</number:text><number:day {two}/><number:text> </number:text>'
              f'<number:hours {two}/><number:text>:</number:text><number:minutes {two}/></number:date-style>'
              f'<number:time-style style:name="NT"><number:hours {two}/><number:text>:</number:text>'
              f'<number:minutes {two}/></number:time-style>'
              '<number:boolean-style style:name="NB"><number:boolean/></number:boolean-style>'
              + "".join(f'<number:percentage-style style:name="NP{d}"><number:number number:decimal-places="{d}" '
                        f'number:min-integer-digits="1"/><number:text>%</number:text></number:percentage-style>'
                        for d in sorted(pct))
              + '<style:style style:name="ceD" style:family="table-cell" style:data-style-name="ND"/>'
              '<style:style style:name="ceDT" style:family="table-cell" style:data-style-name="NDT"/>'
              '<style:style style:name="ceT" style:family="table-cell" style:data-style-name="NT"/>'
              '<style:style style:name="ceB" style:family="table-cell" style:data-style-name="NB"/>'
              + "".join(f'<style:style style:name="ceP{d}" style:family="table-cell" style:data-style-name="NP{d}"/>'
                        for d in sorted(pct)))
    content = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               f'<office:document-content {_NS} office:version="1.2">'
               f"<office:automatic-styles>{styles}</office:automatic-styles>"
               f"<office:body><office:spreadsheet>{tables}</office:spreadsheet></office:body></office:document-content>")
    mt = "application/vnd.oasis.opendocument.spreadsheet"
    manifest = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
                f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{mt}"/>'
                '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                "</manifest:manifest>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for k, (name, data) in enumerate([("mimetype", mt), ("content.xml", content), ("META-INF/manifest.xml", manifest)]):
            zi = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))   # fixed time: reproducible bytes
            zi.compress_type = zipfile.ZIP_STORED if k == 0 else zipfile.ZIP_DEFLATED   # mimetype first, stored
            z.writestr(zi, data)
    return buf.getvalue()


# ---- choosing the file types ---------------------------------------------------------------
def _h(name: str) -> int:
    return int(hashlib.sha1(("mimotable:" + name).encode("utf-8")).hexdigest(), 16)


def xls_ok(sheets: list) -> bool:
    """Fits Excel 97 (.xls) without loss: sheet names of 31 characters or fewer, 65,536 rows, 256 columns."""
    return all(len(s["name"]) <= 31 and s["rows"] <= 65536 and s["cols"] <= 256 for s in sheets)


def plan(names, flat_ok, share: float = DEFAULT_SHARE, have_xls: bool = True, xls_fit=None) -> dict:
    """-> {name: 'ods'|'csv'|'tsv'|'xls'} for the converted share; names not listed stay .xlsx.

    About `share` of the files are converted, split as evenly as possible between .ods, .csv/.tsv
    (alternating) and .xls. Only names in `flat_ok` (one sheet, plain header, no sheet named in a
    question) may become .csv/.tsv; names in `xls_fit` (default: all) may become .xls. Without
    LibreOffice the .xls share becomes .ods. Fixed by the file names and their contents, so every
    machine and every run picks the same types."""
    names = sorted(set(names), key=lambda n: (_h(n), n))
    total = round(max(0.0, min(1.0, share)) * len(names))
    if total <= 0:
        return {}
    n_flat = min(round(total / 3), sum(1 for n in names if n in flat_ok))
    out = {}
    for n in names:
        if len(out) >= n_flat:
            break
        if n in flat_ok:
            out[n] = "csv" if len(out) % 2 == 0 else "tsv"
    rest = [n for n in names if n not in out][: total - len(out)]
    n_xls, want_xls = round(total / 3), True    # alternate .xls / .ods; skip files .xls cannot hold
    for n in rest:
        fits = xls_fit is None or n in xls_fit
        if want_xls and fits and sum(1 for v in out.values() if v == "xls") < n_xls:
            out[n], want_xls = "xls", False
        else:
            out[n], want_xls = "ods", True
    if not have_xls:
        out = {n: ("ods" if f == "xls" else f) for n, f in out.items()}
    return out


# ---- LibreOffice (.xls only) ---------------------------------------------------------------
def find_soffice():
    """LibreOffice's soffice, or None. ME4_SOFFICE overrides; then PATH; then the usual install places."""
    env = os.environ.get("ME4_SOFFICE")
    if env:
        return env if Path(env).exists() or shutil.which(env) else None
    for n in ("soffice", "libreoffice"):
        p = shutil.which(n)
        if p:
            return p
    for p in (r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if Path(p).exists():
            return p
    return None


def soffice_convert(exe: str, files, outdir, fmt: str = "xls", timeout: int = 900) -> list:
    """Convert files with LibreOffice in one headless run (own temporary profile, so an open
    LibreOffice window does not get in the way). Returns the output paths that exist."""
    files = [Path(f) for f in files]
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    prof = Path(tempfile.mkdtemp(prefix="me4-lo-"))
    try:
        subprocess.run([exe, f"-env:UserInstallation={prof.as_uri()}", "--headless", "--norestore",
                        "--convert-to", fmt, "--outdir", str(outdir)] + [str(f) for f in files],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    return [outdir / (f.stem + "." + fmt) for f in files if (outdir / (f.stem + "." + fmt)).exists()]
