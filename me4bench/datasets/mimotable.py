"""MiMoTable (jasonNLP/MiMoTable, MIT): English split, 641 questions over 206 .xlsx files.

After download, about 30% of the workbooks are stored as .ods, .csv/.tsv or .xls instead
(me4bench/sheetconv.py), so a project's spreadsheet readers get exercised. The mapping is written to
english/_formats.json and the questions' gold documents follow it. Turn it off with
ME4_MIMOTABLE_MIXED_SHARE=0 or fetch --no-mixed-formats (then re-download MiMoTable)."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from .. import fetchlib as fl
from .. import sheetconv
from .base import Ctx, bucket_for, make_row, pick

KEY = "mimotable"
ZIP = "https://codeload.github.com/jasonNLP/MiMoTable/zip/refs/heads/main"
LABEL = {"single file, single sheet, simple header": "One sheet, simple header",
         "single file, single sheet, complicate header": "One sheet, complicated header (merged cells)",
         "single file, multiple sheets, simple header": "Several sheets, simple header",
         "single file, multiple sheets, complicate header": "Several sheets, complicated header",
         "multiple tables": "Several tables on one sheet",
         "multiple files, complicate header": "Several files, complicated header",
         "multiple files, simple header": "Several files, simple header"}


def _problems(path: Path) -> list:
    t = path.read_text(encoding="utf-8").strip()
    try:
        v = json.loads(t)
        return v if isinstance(v, list) else list(v.values())
    except ValueError:
        return [json.loads(line) for line in t.splitlines() if line.strip()]


def mixed_share() -> float:
    """Share of workbooks stored as .ods/.csv/.tsv/.xls instead of .xlsx. 0 turns it off.
    Set ME4_MIMOTABLE_MIXED_SHARE=0 or run fetch with --no-mixed-formats."""
    try:
        return max(0.0, min(1.0, float(os.environ.get("ME4_MIMOTABLE_MIXED_SHARE", sheetconv.DEFAULT_SHARE))))
    except ValueError:
        return sheetconv.DEFAULT_SHARE


def formats(root: Path) -> dict:
    """{original .xlsx name: stored name} for the converted workbooks (empty if none)."""
    return (fl.load_json(Path(root) / KEY / "english" / "_formats.json", {}) or {}).get("files", {})


def load(root: Path) -> list:
    base = Path(root) / KEY
    qs = _problems(base / "english" / "problems.json")
    fmap = formats(root)
    out = []
    for i, x in enumerate(qs):
        files = [fmap.get(f, f) for f in (os.path.basename(f) for f in x.get("spreadsheet_list", []))]
        tt = x.get("table_type", "")
        out.append(make_row(KEY, i, str(pick(x, ["question", "query", "problem"])),
                            str(pick(x, ["answer", "reference_answer", "gold_answer"])),
                            answerable=True,  # cannot-be-determined handling is done by the scorer
                            gold_docs=[f"english/spreadsheet/{f}" for f in files],
                            stratum=LABEL.get(tt, tt), bucket=bucket_for(len(set(files))),
                            meta={"table_type": tt,
                                  "operation": pick(x, ["operation", "operation_type", "question_type"], None),
                                  "file_types": sorted({os.path.splitext(f)[1].lstrip(".") for f in files}),
                                  "raw_keys": sorted(x)}))
    return out


def ready(root: Path) -> bool:
    return (Path(root) / KEY / "_done").exists()


def fetch(ctx: Ctx) -> None:
    d = ctx.dir(KEY)
    if ctx.dry_run:
        ctx.get(KEY, ZIP, d / "_src.zip", validate=fl.is_good_zip)
        return
    with fl.Progress("archive", log=ctx.log) as pg:
        z = ctx.get(KEY, ZIP, d / "_src.zip", validate=fl.is_good_zip, progress=pg)
    n = fl.safe_extract_zip(z, d, keep=lambda rel: rel.startswith("data/english/") or rel.upper().startswith("LICENSE"))
    # flatten data/english -> english
    src = d / "data" / "english"
    if src.exists():
        dst = d / "english"
        if dst.exists():
            import shutil
            shutil.rmtree(dst)
        src.rename(dst)
        try:
            (d / "data").rmdir()
        except OSError:
            pass
    if not (d / "english" / "problems.json").exists():
        raise fl.FetchError("MiMoTable archive has no data/english/problems.json; upstream layout changed.")
    mix(d / "english", mixed_share(), log=ctx.log)
    z.unlink()
    (d / "_done").write_text(f"{n} files")


_SHEET_WORD = re.compile(r"\b(work)?sheets?\b", re.I)


def mix(eng: Path, share: float, log=print) -> dict:
    """Convert the planned share of english/spreadsheet/*.xlsx in place; write english/_formats.json."""
    sp = eng / "spreadsheet"
    qs = _problems(eng / "problems.json")
    by_file = {}
    for x in qs:
        for f in x.get("spreadsheet_list", []):
            by_file.setdefault(os.path.basename(f), []).append(x)
    names = sorted(p.name for p in sp.glob("*.xlsx"))
    if share <= 0 or not names:
        fl.save_json(eng / "_formats.json", {"share": 0, "files": {}})
        return {}
    books, flat = {}, set()
    for n in names:
        books[n] = sheetconv.read_xlsx(sp / n)
        qx = by_file.get(n, [])
        sh = books[n]
        named = any(_SHEET_WORD.search(q.get("question", "")) or
                    any(len(s["name"]) > 2 and s["name"].lower() in q.get("question", "").lower() for s in sh)
                    for q in qx)
        plain = all("simple header" in q.get("table_type", "") for q in qx) and qx
        if len(sh) == 1 and not sh[0]["merges"] and plain and not named:
            flat.add(n)
    exe = sheetconv.find_soffice()
    pl = sheetconv.plan(names, flat, share, have_xls=bool(exe),
                        xls_fit={n for n in names if sheetconv.xls_ok(books[n])})
    want_xls = [n for n, f in pl.items() if f == "xls"]
    if want_xls:
        tmp = eng / "_xls"
        done = {p.stem for p in sheetconv.soffice_convert(exe, [sp / n for n in want_xls], tmp)}
        for n in want_xls:
            stem = n[:-5]
            if stem in done:
                os.replace(tmp / (stem + ".xls"), sp / (stem + ".xls"))
            else:
                pl[n] = "ods"
        shutil.rmtree(tmp, ignore_errors=True)
        if len(done) < len(want_xls):
            log(f"  ! LibreOffice converted {len(done)} of {len(want_xls)} .xls files; the rest became .ods.")
    elif share > 0:
        log("  LibreOffice not found: the .xls share is stored as .ods instead (set ME4_SOFFICE to its soffice path).")
    for n, f in sorted(pl.items()):
        stem = n[:-5]
        if f == "ods":
            (sp / (stem + ".ods")).write_bytes(sheetconv.to_ods(books[n]))
        elif f in ("csv", "tsv"):
            (sp / (stem + "." + f)).write_bytes(sheetconv.to_delimited(books[n][0], "," if f == "csv" else "\t"))
        (sp / n).unlink()
    fmap = {n: n[:-5] + "." + f for n, f in sorted(pl.items())}
    counts = {}
    for f in pl.values():
        counts[f] = counts.get(f, 0) + 1
    fl.save_json(eng / "_formats.json", {"share": share, "files": fmap})
    log(f"  stored {len(fmap)} of {len(names)} workbooks in other file types: "
        + ", ".join(f"{counts[k]} .{k}" for k in sorted(counts)))
    return fmap
