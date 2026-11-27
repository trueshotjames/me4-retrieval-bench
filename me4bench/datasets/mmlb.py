"""MMLongBench-Doc (mayubo2333/MMLongBench-Doc): 1,082 questions over 135 PDFs (~0.67 GB).

Strata need to know which PDF pages have no text layer. That scan is cached in
manifest/mmlb_textlayer.json ({doc_id: [chars per page]}) so a Raspberry Pi never has to run it.
Computing it needs `pdftotext` (poppler) or `pip install pypdf`.
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

from .. import fetchlib as fl
from .base import Ctx, make_row

KEY = "mmlb"
REPO = "mayubo2333/MMLongBench-Doc"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/"
MEDIA = f"https://media.githubusercontent.com/media/{REPO}/main/"   # serves Git-LFS content
EMPTY_PAGE_CHARS = 20


def _evlist(v):
    if isinstance(v, str):
        try:
            return ast.literal_eval(v)
        except (ValueError, SyntaxError):
            return []
    return v or []


def scan_pdf(path: Path) -> list:
    """characters of text per page"""
    if shutil.which("pdftotext"):
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, timeout=300)
        pages = out.stdout.decode("utf-8", "replace").split("\f")
        if pages and not pages[-1].strip():
            pages = pages[:-1]
        return [len("".join(p.split())) for p in pages]
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise fl.FetchError("Scanning PDFs for a text layer needs `pdftotext` or `pip install pypdf`. "
                            "Or use the committed manifest/mmlb_textlayer.json.") from e
    return [len("".join((p.extract_text() or "").split())) for p in PdfReader(str(path)).pages]


def textlayer(root: Path, doc_ids, manifest_dir: Path, log=print) -> dict:
    cache = fl.load_json(manifest_dir / "mmlb_textlayer.json", {})
    need = [d for d in doc_ids if d not in cache]
    if need:
        log(f"  scanning {len(need)} PDFs for a text layer (cached in manifest/mmlb_textlayer.json)")
        for k, d in enumerate(need, 1):
            cache[d] = scan_pdf(Path(root) / KEY / "documents" / d)
            if k % 20 == 0:
                log(f"    {k}/{len(need)}")
        fl.save_json(manifest_dir / "mmlb_textlayer.json", cache)
    return cache


def load(root: Path, manifest_dir: Path | None = None, log=print) -> list:
    samples = json.load(open(Path(root) / KEY / "data" / "samples.json", encoding="utf-8"))
    md = manifest_dir or Path(__file__).resolve().parents[2] / "manifest"
    scan = textlayer(root, sorted({s["doc_id"] for s in samples}), md, log)
    out = []
    for i, x in enumerate(samples):
        d = x["doc_id"]
        ch = scan[d]
        empty = sum(1 for c in ch if c < EMPTY_PAGE_CHARS) / max(1, len(ch))
        doc_class = "img" if empty >= .9 else ("mixed" if empty > .05 else "born")
        pages = _evlist(x.get("evidence_pages"))
        srcs = _evlist(x.get("evidence_sources"))
        unans = x["answer"] == "Not answerable" or x.get("answer_format") == "None"
        if unans:
            lab = "Unanswerable, image-heavy document" if doc_class != "born" else "Unanswerable, text document"
        elif pages and all(p - 1 < len(ch) and ch[p - 1] < EMPTY_PAGE_CHARS for p in pages):
            lab = "Evidence only on image-only pages"
        elif len(pages) > 1:
            lab = "Several pages (text layer)"
        else:
            lab = "(no slot) plain text"
            for k, name in [("Table", "Table (one page)"), ("Chart", "Chart (one page)"),
                            ("Figure", "Figure or image (one page)"),
                            ("Generalized-text (Layout)", "Layout text: footnotes, headers, lists")]:
                if k in srcs:
                    lab = name
                    break
        out.append(make_row(KEY, i, x["question"], str(x["answer"]), answerable=not unans,
                            gold_docs=[] if unans else [f"documents/{d}"], stratum=lab,
                            bucket="0" if unans else "1",
                            meta={"doc_id": d, "doc_type": x.get("doc_type"), "evidence_pages": pages,
                                  "evidence_sources": srcs, "answer_format": x.get("answer_format"),
                                  "doc_class": doc_class, "doc": f"documents/{d}"}))
    return out


def ready(root: Path) -> bool:
    return (Path(root) / KEY / "_done").exists()


def fetch(ctx: Ctx) -> None:
    d = ctx.dir(KEY)
    sj = d / "data" / "samples.json"
    if ctx.dry_run:
        ctx.get(KEY, RAW + "data/samples.json", sj)
        ctx.note(KEY, "mmlb/documents/*.pdf (135 PDFs)", MEDIA + "documents/", 670_000_000)
        return
    ok = lambda p: not fl.looks_like_lfs_pointer(p) and fl.load_json(p) is not None  # noqa: E731
    try:
        ctx.get(KEY, RAW + "data/samples.json", sj, validate=ok)
    except fl.FetchError:
        ctx.get(KEY, MEDIA + "data/samples.json", sj, validate=ok)   # LFS pointer at main
    docs = sorted({s["doc_id"] for s in fl.load_json(sj)})
    ctx.log(f"  MMLongBench-Doc: {len(docs)} PDFs")
    for k, name in enumerate(docs, 1):
        dest = d / "documents" / name
        try:
            ctx.get(KEY, MEDIA + "documents/" + quote(name), dest, validate=fl.is_pdf)
        except fl.FetchError:
            ctx.get(KEY, RAW + "documents/" + quote(name), dest, validate=fl.is_pdf)
        if k % 20 == 0:
            ctx.log(f"    {k}/{len(docs)} PDFs")
    (d / "_done").write_text(f"{len(docs)} pdfs")
