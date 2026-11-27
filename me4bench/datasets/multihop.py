"""MultiHop-RAG (yixuantt/MultiHop-RAG): 2,556 queries over a 609-article news corpus."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import fetchlib as fl
from .base import Ctx, bucket_for, make_row

KEY = "multihop"
RAW = "https://raw.githubusercontent.com/yixuantt/MultiHop-RAG/main/dataset/"
LABEL = {"inference_query": "Inference query", "comparison_query": "Comparison query",
         "temporal_query": "Temporal query", "null_query": 'Null query ("not in the files")'}


def article_id(url: str) -> str:
    return "articles/" + hashlib.sha1(url.encode()).hexdigest()[:12] + ".md"


def load(root: Path) -> list:
    qs = json.load(open(Path(root) / KEY / "MultiHopRAG.json", encoding="utf-8"))
    out = []
    for i, x in enumerate(qs):
        qt = x.get("question_type", "")
        urls = sorted({e["url"] for e in x.get("evidence_list", []) if e.get("url")})
        ans = qt != "null_query"
        out.append(make_row(KEY, i, str(x.get("query", "")), str(x.get("answer", "")), answerable=ans,
                            gold_docs=[article_id(u) for u in urls] if ans else [],
                            stratum=LABEL.get(qt, qt), bucket="0" if not ans else bucket_for(len(urls)),
                            meta={"question_type": qt, "evidence_urls": urls,
                                  "evidence": x.get("evidence_list", [])}))
    return out


def ready(root: Path) -> bool:
    return (Path(root) / KEY / "_done").exists()


def fetch(ctx: Ctx) -> None:
    d = ctx.dir(KEY)
    ok_json = lambda p: p.stat().st_size > 1000 and fl.load_json(p) is not None  # noqa: E731
    files = ["MultiHopRAG.json", "corpus.json"]
    if ctx.dry_run:
        for f in files:
            ctx.get(KEY, RAW + f, d / f, validate=ok_json)
    else:
        with fl.Progress("files", total_items=len(files), log=ctx.log) as pg, \
                ThreadPoolExecutor(max_workers=2) as ex:
            def one(f):
                pg.start_item(f)
                ctx.get(KEY, RAW + f, d / f, validate=ok_json, progress=pg)
                pg.item_done()
            list(ex.map(one, files))   # re-raises the first failure
    corpus = d / "corpus.json"
    if ctx.dry_run:
        return
    arts = fl.load_json(corpus)
    (d / "articles").mkdir(exist_ok=True)
    for a in arts:
        head = (f"# {a.get('title','')}\n\nSource: {a.get('source','')}  \nAuthor: {a.get('author','')}  \n"
                f"Published: {a.get('published_at','')}  \nCategory: {a.get('category','')}  \n"
                f"URL: {a.get('url','')}\n\n")
        (d / article_id(a["url"])).write_text(head + str(a.get("body", "")) + "\n", encoding="utf-8")
    (d / "_done").write_text(f"{len(arts)} articles")
