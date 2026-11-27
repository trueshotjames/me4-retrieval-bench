"""FRAMES (google/frames-benchmark): 824 multi-hop questions over Wikipedia articles.

fetch: test.tsv from Hugging Face, then every linked Wikipedia article as wikitext from the Action
API, 50 titles per request, one request at a time (maxlag, gzip, Retry-After), converted locally to
Markdown (me4bench/wikitext.py) and then to docx/html/txt/odt for the mixed-format share.
Resumable: existing articles are skipped. Revision ids go to data/frames/_revisions.json.
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .. import fetchlib as fl
from .. import convert
from ..wikitext import to_markdown
from .base import Ctx, bucket_for, make_row, safe_name

KEY = "frames"
WIKI_UA = os.environ.get("ME4_WIKI_UA") or (fl.UA + " " + (os.environ.get("ME4_CONTACT") or ""))
TSV_URL_PATH = "/datasets/google/frames-benchmark/resolve/main/test.tsv"
WIKI_API = "https://en.wikipedia.org/w/api.php"
BATCH = 50      # titles per Action API request (the API maximum for normal clients)
GAP = 1.0       # seconds between requests; requests are serial, as Wikimedia asks
EST_BYTES = 150_000_000  # Markdown output (upper bound)
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


def mixed_share() -> float:
    """Share of articles stored as docx/html/txt/odt instead of .md. 0 turns it off.
    Set ME4_FRAMES_MIXED_SHARE=0 or run fetch with --no-mixed-formats."""
    try:
        return max(0.0, min(1.0, float(os.environ.get("ME4_FRAMES_MIXED_SHARE", convert.DEFAULT_SHARE))))
    except ValueError:
        return convert.DEFAULT_SHARE


def article_file(url: str) -> str:
    t = title_of(url)
    fmt = convert.choose_format(t, mixed_share())
    return "articles/" + safe_name(t) + "." + fmt


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


def _sleep_until(state: dict) -> None:
    w = state["next"] - time.time()
    if w > 0:
        time.sleep(w)
    state["next"] = time.time() + state.get("gap", GAP)


def _api(params: dict, state: dict, pg=None) -> dict:
    """One Action API GET, serial and polite: maxlag, gzip, Retry-After, backoff on 429/503/maxlag."""
    q = dict(params, format="json", formatversion="2", maxlag="5")
    url = WIKI_API + "?" + urllib.parse.urlencode(q)
    err = ""
    for attempt in range(8):
        _sleep_until(state)
        req = urllib.request.Request(url, headers={"User-Agent": WIKI_UA.strip(), "Accept-Encoding": "gzip"})
        wait_s = min(60, 5 * 2 ** attempt)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    if pg:
                        pg.add(len(raw))
                    raw = gzip.decompress(raw)
                elif pg:
                    pg.add(len(raw))
                ra = r.headers.get("Retry-After", "")
            data = json.loads(raw.decode("utf-8"))
            code = (data.get("error") or {}).get("code", "")
            if not code:
                return data
            if code not in ("maxlag", "ratelimited", "readonly", "internal_api_error_DBQueryError"):
                raise fl.FetchError(f"Wikipedia API error: {code}: {data['error'].get('info', '')}")
            err = code
            if ra.isdigit():
                wait_s = min(60, max(int(ra), 1))
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}"
            if e.code not in (429, 500, 502, 503, 504):
                raise fl.FetchError(f"Wikipedia API: {err}") from e
            ra = e.headers.get("Retry-After", "") if e.headers else ""
            if ra.isdigit():
                wait_s = min(60, max(int(ra), 1))
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            err = str(e)
        state["retries"] = state.get("retries", 0) + 1
        state["gap"] = min(10.0, state.get("gap", GAP) * 1.5)      # stay slower after a throttle
        if pg:
            pg.note = f"{state['retries']} retries ({err}), waiting {wait_s}s"
        time.sleep(wait_s)
    raise fl.FetchError(f"Wikipedia API kept failing ({err}); re-run fetch to resume.")


def fetch_batch(titles: list, state: dict, pg=None) -> dict:
    """Up to 50 titles in one request (plus continuation if the reply was split).
    -> {requested title: {"title", "revid", "timestamp", "wikitext"} or None if missing}"""
    params = {"action": "query", "prop": "revisions", "rvprop": "ids|timestamp|content",
              "rvslots": "main", "redirects": "1", "titles": "|".join(titles)}
    norm, redir, pages, cont = {}, {}, {}, {}
    while True:
        data = _api(dict(params, **cont), state, pg)
        qy = data.get("query", {})
        for x in qy.get("normalized", []):
            norm[x["from"]] = x["to"]
        for x in qy.get("redirects", []):
            redir[x["from"]] = x["to"]
        for p in qy.get("pages", []):
            revs = p.get("revisions") or []
            if revs and "slots" in revs[0]:
                pages[p["title"]] = {"title": p["title"], "revid": revs[0].get("revid"),
                                     "timestamp": revs[0].get("timestamp", ""),
                                     "wikitext": revs[0]["slots"]["main"].get("content", "")}
        if "continue" not in data:
            break
        cont = data["continue"]
    out = {}
    for t in titles:
        f = norm.get(t, t)
        for _ in range(5):
            if f not in redir:
                break
            f = redir[f]
        out[t] = pages.get(f)
    return out


def _resolve_short(url: str, state: dict) -> str:
    """w.wiki short link -> real title (one redirect lookup)."""
    _sleep_until(state)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": WIKI_UA.strip()}),
                                    timeout=30) as r:
            if "/wiki/" in r.geturl():
                return title_of(r.geturl())
    except (urllib.error.URLError, OSError, TimeoutError):
        pass
    return ""


def _write(dest: Path, url: str, page: dict) -> None:
    md = (f"# {title_of(url)}\n\nSource: {url}\nRevision: {page['revid']} ({page['timestamp']})\n\n"
          + to_markdown(page["wikitext"]))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    ext = dest.suffix.lstrip(".")
    if ext == "md":
        tmp.write_text(md, encoding="utf-8")
    else:
        tmp.write_bytes(convert.convert(md, ext, title_of(url)))
    tmp.replace(dest)


def fetch(ctx: Ctx) -> None:
    d = ctx.dir(KEY)
    ctx.get(KEY, fl.hf_url(TSV_URL_PATH), d / "test.tsv",
            validate=lambda p: p.stat().st_size > 10_000 and b"Prompt" in open(p, "rb").read(2000))
    if ctx.dry_run:
        ctx.note(KEY, f"frames/articles/ (~2,500 Wikipedia pages, {BATCH} per API request)", WIKI_API, EST_BYTES)
        return
    rows = load(ctx.root)
    urls = sorted({u for r in rows for u in r["meta"]["links"]})
    missing = [(u, d / article_file(u)) for u in urls]
    missing = [(u, p) for u, p in missing if not (p.exists() and p.stat().st_size > 0)]
    ctx.log(f"  FRAMES: {len(urls)} articles, {len(missing)} to fetch, {BATCH} per request "
            "(resumable; Ctrl+C is safe)")
    state = {"next": time.time(), "gap": GAP}
    fails = []
    revs_path = d / "_revisions.json"
    revs = fl.load_json(revs_path, {}) or {}
    by_title: dict = {}
    with fl.Progress("articles", total_items=len(missing), log=ctx.log) as pg:
        for u, p in missing:
            t = title_of(u)
            if "w.wiki" in u:
                pg.start_item("resolving " + u.rsplit("/", 1)[-1])
                t = _resolve_short(u, state)
                if not t:
                    fails.append((u, "short link did not resolve"))
                    pg.item_done(ok=False)
                    continue
            by_title.setdefault(t, []).append((u, p))
        titles = list(by_title)
        for i in range(0, len(titles), BATCH):
            chunk = titles[i:i + BATCH]
            pg.start_item(chunk[0])
            try:
                got = fetch_batch(chunk, state, pg)
            except fl.FetchError as e:
                for t in chunk:
                    for u, _ in by_title[t]:
                        fails.append((u, str(e)))
                        pg.item_done(ok=False)
                continue
            pg.note = f"{state.get('retries', 0)} retries" if state.get("retries") else ""
            for t in chunk:
                page = got.get(t)
                for u, p in by_title[t]:
                    if page is None:
                        fails.append((u, "missing on Wikipedia"))
                        pg.item_done(ok=False)
                        continue
                    try:
                        _write(p, u, page)
                        revs[u] = page["revid"]
                        pg.item_done()
                    except Exception as e:  # noqa: BLE001 - one bad article must not stop the run
                        fails.append((u, f"convert: {e}"))
                        pg.item_done(ok=False)
            fl.save_json(revs_path, revs)
    if fails:
        fl.save_json(d / "_failed.json", fails)
        ctx.log(f"  ! {len(fails)} articles failed (see data/frames/_failed.json). Re-run fetch to retry.")
        if len(fails) > 0.02 * len(urls):
            raise fl.FetchError("Too many Wikipedia failures; check the network and re-run (resumes).")
    (d / "_done").write_text(time.strftime("fetched %Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    ctx.lock[f"{KEY}/_wikipedia_fetched_at"] = time.strftime("%Y-%m-%d", time.gmtime())
