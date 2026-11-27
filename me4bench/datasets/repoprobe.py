"""RepoProbe (Tencent-Hunyuan/RepoProbe): 500 code questions; we use 15 repos (one per language, the
repo with most questions), fetched as commit zips from codeload (no git needed), media stripped.

The upstream layout is not verifiable offline, so file discovery is tolerant: any *.csv with a
question column, and any repos_info*.json. `build-manifest` verifies per-stratum counts."""
from __future__ import annotations

import collections
import csv
import io
import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import fetchlib as fl
from .base import Ctx, make_row, pick, safe_name

KEY = "repoprobe"
ZIP = "https://codeload.github.com/Tencent-Hunyuan/RepoProbe/zip/refs/heads/main"
MEDIA = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".svg", ".mp4", ".mov", ".avi",
         ".webm", ".mp3", ".wav", ".pdf", ".psd", ".zip", ".gz", ".tar", ".jar", ".woff", ".woff2",
         ".ttf", ".otf", ".eot", ".class", ".so", ".dll", ".exe", ".bin", ".pyc"}
KW = [("Names a source file", r"[\w\-/]+\.(py|ts|js|go|rs|cpp|cs|java|rb|lua|swift|sh|php)\b"),
      ("Config question (yaml, json, toml)", r"\b(yaml|yml|json|toml|config|\.env|dockerfile)\b"),
      ("Error or log text in the question", r"\b(error|traceback|exception|log)\b"),
      ("Mentions README or docs", r"\b(readme|docs?|documentation)\b")]
WORKERS = 4   # parallel repo zip downloads
# The fixed split was drawn on these 15 repos (C++ and C have ties at the top; this decides them).
SPLIT_REPOS = {"ProxmoxVE", "TUnit", "adk-python", "blink.cmp", "dawarich", "desktop", "ducklake", "neuron-ai",
               "opencloud", "pangolin", "pkl", "shell", "sqlite-data", "valkey", "walker"}
TAX = {"Project Architecture": "Project architecture", "Business Logic": "Business logic"}


def stratum_of(question: str, taxonomy: str) -> str:
    for name, rx in KW:
        if re.search(rx, question, re.I):
            return name
    return TAX.get(taxonomy, "Implementation details")


def _read_csvs(src: Path) -> list:
    rows = []
    for p in sorted(src.rglob("*.csv"), key=lambda q: q.as_posix()):
        try:
            txt = p.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        rd = list(csv.DictReader(io.StringIO(txt)))
        if rd and any(k.lower() in ("question", "query") for k in rd[0]):
            rows += rd
    return rows


def _repo_info(src: Path) -> dict:
    """repo_name -> {language, commit, url}; tolerant of list/dict shapes. Upstream (2026) is a list of
    {"name": "owner/repo", "url", "primary_language", "snapshot": {"git": {"head": sha}}}, while the
    CSVs use the short name, so each repo is keyed by both."""
    out = {}
    for p in sorted(src.rglob("repos_info*.json")):
        v = fl.load_json(p, None)
        items = v.items() if isinstance(v, dict) else enumerate(v or [])
        for k, x in items:
            if not isinstance(x, dict):
                continue
            name = pick(x, ["repo_name", "name", "repo", "full_name"], k if isinstance(k, str) else "")
            if not name:
                continue
            git = ((x.get("snapshot") or {}).get("git") or {}) if isinstance(x.get("snapshot"), dict) else {}
            url = str(pick(x, ["url", "repo_url", "github_url", "html_url"], "") or git.get("origin", ""))
            if not url and "/" in str(name):
                url = "https://github.com/" + str(name)
            rec = {"language": str(pick(x, ["language", "lang", "main_language", "primary_language"], "")),
                   "commit": str(pick(x, ["commit", "commit_id", "commit_hash", "sha", "base_commit"], "")
                                 or git.get("head", "")),
                   "url": url}
            out[str(name)] = rec
            out.setdefault(str(name).rsplit("/", 1)[-1], rec)
    return out


def _src(root: Path) -> Path:
    return Path(root) / KEY / "_meta"


def _rows_and_repos(root: Path):
    src = _src(root)
    rows, info = _read_csvs(src), _repo_info(src)
    by = collections.defaultdict(collections.Counter)
    for r in rows:
        name = str(pick(r, ["repo_name", "repo", "repository"]))
        by[info.get(name, {}).get("language") or str(pick(r, ["language", "lang"], "?"))][name] += 1
    chosen = set()
    for c in by.values():   # most questions per language; ties go to the repo the split was drawn on
        top = max(c.values())
        chosen.add(min((n for n, k in c.items() if k == top), key=lambda n: (n not in SPLIT_REPOS, list(c).index(n))))
    return rows, info, chosen


def load(root: Path) -> list:
    """Rows for the chosen repos only; idx = row index in the full question list (split alignment)."""
    rows, info, chosen = _rows_and_repos(root)
    out = []
    for i, r in enumerate(rows):
        name = str(pick(r, ["repo_name", "repo", "repository"]))
        if name not in chosen:
            continue
        q = str(pick(r, ["question", "query"]))
        tax = str(pick(r, ["taxonomy", "category", "type"]))
        out.append(make_row(KEY, i, q, str(pick(r, ["answer", "reference_answer", "gold_answer"])),
                            answerable=True, gold_docs=[safe_name(name)], stratum=stratum_of(q, tax), bucket="n/a",
                            meta={"checklist": pick(r, ["checklist", "rubric", "scoring_points"], None),
                                  "taxonomy": tax, "language": info.get(name, {}).get("language", ""),
                                  "repo": name}))
    return out


def ready(root: Path) -> bool:
    """_done plus every chosen repo extracted (an older run could finish with repos missing)."""
    if not (Path(root) / KEY / "_done").exists():
        return False
    try:
        _, _, chosen = _rows_and_repos(root)
    except Exception:  # noqa: BLE001
        return False
    return len(chosen) > 1 and all((Path(root) / KEY / "repos" / safe_name(n) / "_ok").exists() for n in chosen)


def _strip_media(d: Path) -> int:
    n = 0
    for p in list(d.rglob("*")):
        if p.is_file() and p.suffix.lower() in MEDIA:
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n


def fetch(ctx: Ctx) -> None:
    d = ctx.dir(KEY)
    if ctx.dry_run:
        z = ctx.get(KEY, ZIP, d / "_src.zip", validate=fl.is_good_zip)
    else:
        with fl.Progress("question files", log=ctx.log) as pg:
            z = ctx.get(KEY, ZIP, d / "_src.zip", validate=fl.is_good_zip, progress=pg)
    if ctx.dry_run:
        ctx.note(KEY, "15 repo commit zips (one per language; sizes known after the question files)",
                 "https://codeload.github.com/<owner>/<repo>/zip/<commit>", 370 * 2 ** 20)
        return
    meta = _src(ctx.root)
    if meta.exists():
        shutil.rmtree(meta)
    fl.safe_extract_zip(z, meta, keep=lambda rel: rel.lower().endswith((".csv", ".json")) and "/node_modules/" not in rel)
    rows, info, chosen = _rows_and_repos(ctx.root)
    if not rows or not chosen:
        raise fl.FetchError("RepoProbe: found no question CSV in the upstream repo; layout changed. "
                            "Open an issue with the folder listing of Tencent-Hunyuan/RepoProbe.")
    repos = d / "repos"
    todo = []
    for name in sorted(chosen):
        dest = repos / safe_name(name)
        if (dest / "_ok").exists() and not ctx.force:
            continue
        i = info.get(name, {})
        m = re.search(r"github\.com/([^/]+/[^/#?]+)", i.get("url", ""))
        slug = m.group(1).removesuffix(".git") if m else name.replace("__", "/", 1) if "__" in name else name
        if "/" not in slug:
            ctx.log(f"  ! {name}: no GitHub URL in repos_info.json, skipped (its questions will be unanswerable)")
            continue
        todo.append((name, slug, i.get("commit") or "HEAD", dest))
    ctx.log(f"  RepoProbe: {len(chosen)} repos, {len(todo)} to fetch, {WORKERS} at a time")
    failed = []
    with fl.Progress("repos", total_items=len(todo), log=ctx.log) as pg:
        def one(job):
            name, slug, ref, dest = job
            pg.start_item(name)
            try:
                zdest = d / "_zips" / f"{safe_name(name)}.zip"
                try:
                    zp = ctx.get(KEY, f"https://codeload.github.com/{slug}/zip/{ref}", zdest,
                                 validate=fl.is_good_zip, progress=pg)
                except fl.FetchError:
                    if ref == "HEAD":
                        raise
                    ctx.log(f"  ! {name}: commit {ref[:10]} is gone upstream; using the current default branch")
                    ref = "HEAD"
                    zp = ctx.get(KEY, f"https://codeload.github.com/{slug}/zip/HEAD", zdest,
                                 validate=fl.is_good_zip, progress=pg)
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                fl.safe_extract_zip(zp, dest)
                _strip_media(dest)
                (dest / "_ok").write_text(ref)
                zp.unlink(missing_ok=True)
                pg.item_done()
            except Exception as e:  # noqa: BLE001
                pg.item_done(ok=False)
                failed.append(f"{name}: {e}")
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            list(ex.map(one, todo))
    if failed:
        raise fl.FetchError(f"RepoProbe: {len(failed)} repos failed (rerun to resume):\n  " + "\n  ".join(failed))
    shutil.rmtree(d / "_zips", ignore_errors=True)
    z.unlink(missing_ok=True)
    (d / "_done").write_text(f"{len(chosen)} repos")
