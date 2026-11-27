"""FRAMES (google/frames-benchmark): 824 multi-hop questions over Wikipedia articles.

fetch: test.tsv from Hugging Face, then every linked Wikipedia article (REST HTML -> Markdown),
one polite request at a time per worker, resumable (existing articles are skipped).
"""
from __future__ import annotations

import csv
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import sys
from pathlib import Path

from .. import fetchlib as fl
from ..htmlmd import html_to_markdown
from .base import Ctx, bucket_for, make_row, safe_name

KEY = "frames"
TSV_URL_PATH = "/datasets/google/frames-benchmark/resolve/main/test.tsv"
WIKI_REST = "https://en.wikipedia.org/api/rest_v1/page/html/"
EST_BYTES = 150_000_000  # Markdown output; the HTML download is several times larger
_PRI = ["Tabular reasoning", "Numerical reasoning", "Temporal reasoning", "Post processing",
        "Multiple constraints"]
_LAB = {("Tabular reasoning", "2-3"): "Tabular, 2–3 articles",
        ("Tabular reasoning", "4+"): "Tabular, 4+ articles",
        ("Numerical reasoning", "2-3"): "Numerical, 2–3 articles",
        ("Numerical reasoning", "4+"): "Numerical, 4+ articles",
        ("Multiple constraints", "2-3"): "Multiple constraints, 2–3 articles",
        ("Multiple constraints", "4+"): "Multiple constraints, 4+ articles"}
_WIKI = re.compile(r"https?://en\.wikipedia\.org/wiki/[^\s'\",\]\[]+")


def title_of(url: str) -> str:
    """Article title from a link. Handles /wiki/X, index.php?title=X, ?search=X and w.wiki short links
    (short links are keyed by their code and resolved by redirect at fetch time)."""
    if "/wiki/" in url:
        t = urllib.parse.unquote(url.split("/wiki/", 1)[1]).split("#")[0]
        return t.replace("_", " ")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    t = (q.get("title") or [""])[0]
    if not t or t.startswith("Special:"):
        t = (q.get("search") or [""])[0] or t
    if not t:
        t = "w.wiki-" + url.rstrip("/").rsplit("/", 1)[-1]
    return t.replace("_", " ")


def article_file(url: str) -> str:
    return "articles/" + safe_name(title_of(url)) + ".md"


def _links(r: dict) -> list:
    out = []
    for j in range(1, 11):
        v = (r.get(f"wikipedia_link_{j}") or "").strip()
        if v:
            out.append(v)
    out += _WIKI.findall(r.get("wikipedia_link_11+") or "")
    seen, res = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            res.append(u)
    return res


def load(root: Path) -> list:
    p = Path(root) / KEY / "test.tsv"
    with open(p, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    out = []
    for i, r in enumerate(rows):
        tags = [x.strip() for x in (r.get("reasoning_types") or "").split("|")]
        prim = next((x for x in _PRI if x in tags), "Multiple constraints")
        links = _links(r)
        band = "4+" if len(links) >= 4 else "2-3"
        lab = ("Temporal (any article count)" if prim == "Temporal reasoning" else
               "Post-processing (any article count)" if prim == "Post processing" else _LAB[(prim, band)])
        out.append(make_row(KEY, i, r["Prompt"], r["Answer"], answerable=True,
                            gold_docs=[article_file(u) for u in links], stratum=lab,
                            bucket=bucket_for(len(links)),
                            meta={"reasoning_types": tags, "links": links}))
    return out


def ready(root: Path) -> bool:
    return (Path(root) / KEY / "_done").exists()


def _fetch_article(url: str, dest: Path, gap: float, lock: threading.Lock, state: dict):
    if dest.exists() and dest.stat().st_size > 0:
        return None
    title = title_of(url)
    state["active"] = state.get("active", 0) + 1
    state["last"] = title
    try:
        return _fetch_article2(url, dest, gap, lock, state, title)
    finally:
        state["active"] -= 1


def _fetch_article2(url: str, dest: Path, gap: float, lock: threading.Lock, state: dict, title: str):
    if "w.wiki" in url:  # short link: follow the redirect to learn the real title
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": fl.UA}), timeout=30) as r:
                if "/wiki/" in r.geturl():
                    title = title_of(r.geturl())
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
    api = WIKI_REST + urllib.parse.quote(title.replace(" ", "_"), safe="")
    err = None
    for attempt in range(5):
        with lock:  # global politeness gap between request starts
            wait = state["next"] - time.time()
            state["next"] = max(state["next"], time.time()) + gap
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(api, headers={"User-Agent": fl.UA, "Accept": "text/html"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                html = r.read().decode("utf-8", "replace")
                rev = r.headers.get("ETag", "")
            md = f"# {title_of(url)}\n\nSource: {url}\nRevision: {rev}\n\n" + html_to_markdown(html)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            tmp.write_text(md, encoding="utf-8")
            tmp.replace(dest)
            state["bytes"] = state.get("bytes", 0) + len(html)
            return None
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}"
            if e.code == 404:
                break
            state["retries"] = state.get("retries", 0) + 1
            ra = e.headers.get("Retry-After", "") if e.headers else ""
            time.sleep(min(60, int(ra) if ra.isdigit() else 3 * 2 ** attempt))
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            err = str(e)
            time.sleep(min(30, 2 ** attempt))
    return (url, err)


def fetch(ctx: Ctx) -> None:
    d = ctx.dir(KEY)
    tsv = ctx.get(KEY, fl.hf_url(TSV_URL_PATH), d / "test.tsv",
                  validate=lambda p: p.stat().st_size > 10_000 and b"Prompt" in open(p, "rb").read(2000))
    if ctx.dry_run:
        ctx.note(KEY, "frames/articles/ (~2,500 Wikipedia pages, via REST API)", WIKI_REST, EST_BYTES)
        return
    rows = load(ctx.root)
    urls = sorted({u for r in rows for u in r["meta"]["links"]})
    todo = [(u, d / article_file(u)) for u in urls]
    missing = [t for t in todo if not (t[1].exists() and t[1].stat().st_size > 0)]
    ctx.log(f"  FRAMES: {len(urls)} articles, {len(missing)} to fetch (resumable; Ctrl+C is safe)")
    lock, state = threading.Lock(), {"next": time.time()}
    fails = []
    t0, done = time.time(), 0
    workers = max(ctx.jobs, 12)  # Wikipedia answers slowly per request; parallelism is what helps
    tty = sys.stderr.isatty()
    total = len(missing)

    def status() -> str:
        el = max(1e-9, time.time() - t0)
        rate = done / el
        eta = (total - done) / rate / 60 if rate else 0
        n = 20 * done // max(1, total)
        bar = "#" * n + "-" * (20 - n)
        return (f"    [{bar}] {done}/{total} ({100 * done // max(1, total)}%) | {rate * 60:.0f}/min | "
                f"{state.get('active', 0)} in flight | {state.get('retries', 0)} retries | "
                f"{len(fails)} failed | {fl.human(state.get('bytes', 0))} | "
                f"{int(el // 60)}m{int(el % 60):02d}s elapsed, ~{eta:.0f} min left | {state.get('last', '')[:30]}")

    def show(final=False):
        line = status()
        if tty:
            sys.stderr.write("\r" + line.ljust(170)[:170] + ("\n" if final else ""))
            sys.stderr.flush()
        else:
            ctx.log(line)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        pending = {ex.submit(_fetch_article, u, p, 0.05, lock, state) for u, p in missing}
        last_print = 0.0
        while pending:
            fin, pending = wait(pending, timeout=1.0, return_when=FIRST_COMPLETED)
            for f in fin:
                done += 1
                res = f.result()
                if res:
                    fails.append(res)
            if tty or time.time() - last_print >= 5:   # every second on a terminal, every 5 s in a log
                show()
                last_print = time.time()
        if total:
            show(final=True)
    if fails:
        fl.save_json(d / "_failed.json", fails)
        ctx.log(f"  ! {len(fails)} articles failed (see data/frames/_failed.json). Re-run fetch to retry.")
        if len(fails) > 0.02 * len(urls):
            raise fl.FetchError("Too many Wikipedia failures; check the network and re-run (resumes).")
    (d / "_done").write_text(time.strftime("fetched %Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    ctx.lock[f"{KEY}/_wikipedia_fetched_at"] = time.strftime("%Y-%m-%d", time.gmtime())
