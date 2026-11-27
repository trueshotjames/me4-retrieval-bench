"""MiMoTable (jasonNLP/MiMoTable, MIT): English split, 641 questions over 206 .xlsx files."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .. import fetchlib as fl
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


def load(root: Path) -> list:
    base = Path(root) / KEY
    qs = _problems(base / "english" / "problems.json")
    out = []
    for i, x in enumerate(qs):
        files = [os.path.basename(f) for f in x.get("spreadsheet_list", [])]
        tt = x.get("table_type", "")
        out.append(make_row(KEY, i, str(pick(x, ["question", "query", "problem"])),
                            str(pick(x, ["answer", "reference_answer", "gold_answer"])),
                            answerable=True,  # cannot-be-determined handling is done by the scorer
                            gold_docs=[f"english/spreadsheets/{f}" for f in files],
                            stratum=LABEL.get(tt, tt), bucket=bucket_for(len(set(files))),
                            meta={"table_type": tt,
                                  "operation": pick(x, ["operation", "operation_type", "question_type"], None),
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
    z.unlink()
    (d / "_done").write_text(f"{n} files")
