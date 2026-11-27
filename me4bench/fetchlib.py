"""Cross-platform, resumable, checksum-aware downloading. Standard library only.

Works on Windows, macOS, x86 Linux and ARM64 Linux (Raspberry Pi 4/5). No pip packages,
no git, no curl, no tar binaries needed.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

UA = "me4bench-fetch/0.1 (+research benchmark downloader)"
CHUNK = 1 << 20


class FetchError(RuntimeError):
    """A download problem with a human-readable hint."""


def hint_for(url: str, err: Exception) -> str:
    s = str(err)
    host = url.split("/")[2] if "//" in url else url
    if isinstance(err, urllib.error.HTTPError):
        if err.code in (403, 429) and "github" in host:
            return ("GitHub refused the request (rate limit or block). Set GITHUB_TOKEN to a "
                    "personal access token (no scopes needed) and retry; downloads resume.")
        if err.code == 404:
            return f"Not found: the upstream file moved or was renamed. URL: {url}"
        if err.code in (401, 403) and "huggingface" in host:
            return "Hugging Face needs a login for this file: set HF_TOKEN and retry."
        return f"HTTP {err.code} from {host}."
    if isinstance(err, (urllib.error.URLError, OSError, TimeoutError)):
        return (f"Cannot reach {host} ({s}). Check the network/proxy (HTTPS_PROXY is honoured), "
                f"or run fetch on a machine with open internet and copy the data/ folder.")
    return s


def human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return str(n)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def _headers(url: str, extra: dict | None = None) -> dict:
    h = {"User-Agent": UA, "Accept-Encoding": "identity"}
    host = url.split("/")[2] if "//" in url else ""
    tok = os.environ.get("GITHUB_TOKEN")
    if tok and (host.endswith("github.com") or host.endswith("githubusercontent.com")):
        h["Authorization"] = f"Bearer {tok}"
    hf = os.environ.get("HF_TOKEN")
    if hf and host.endswith("huggingface.co"):
        h["Authorization"] = f"Bearer {hf}"
    h.update(extra or {})
    return h


def hf_url(path: str) -> str:
    return os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/") + path


def probe(url: str, timeout: int = 20) -> dict:
    """Cheap reachability check (ranged GET of 1 byte). Returns {ok, size, status|error}."""
    req = urllib.request.Request(url, headers=_headers(url, {"Range": "bytes=0-0"}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            cr = r.headers.get("Content-Range", "")
            size = int(cr.split("/")[-1]) if "/" in cr and cr.split("/")[-1].isdigit() else \
                int(r.headers.get("Content-Length") or 0)
            return {"ok": True, "size": size, "status": r.status}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": hint_for(url, e)}


def download(url: str, dest: Path, *, expect_sha: str | None = None, retries: int = 6,
             timeout: int = 60, quiet: bool = False, validate=None) -> str:
    """Download url -> dest, resumable. Returns sha256. Skips if dest exists and matches expect_sha
    (or, with no expect_sha, if it exists and passes `validate`)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if expect_sha:
            if sha256_file(dest) == expect_sha:
                return expect_sha
            dest.unlink()
        elif validate is None or validate(dest):
            return sha256_file(dest)
        else:
            dest.unlink()
    part = dest.with_name(dest.name + ".part")
    last: Exception | None = None
    for attempt in range(retries):
        have = part.stat().st_size if part.exists() else 0
        hdrs = {"Range": f"bytes={have}-"} if have else {}
        req = urllib.request.Request(url, headers=_headers(url, hdrs))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status = r.status
                total = None
                if status == 206:
                    cr = r.headers.get("Content-Range", "")
                    if "/" in cr and cr.split("/")[-1].isdigit():
                        total = int(cr.split("/")[-1])
                    mode = "ab"
                else:  # server ignored Range (or fresh start)
                    have = 0
                    mode = "wb"
                    cl = r.headers.get("Content-Length")
                    total = int(cl) if cl and cl.isdigit() else None
                done = have
                t0 = time.time()
                with open(part, mode) as f:
                    while True:
                        b = r.read(CHUNK)
                        if not b:
                            break
                        f.write(b)
                        done += len(b)
                        if not quiet and sys.stderr.isatty() and total:
                            sys.stderr.write(f"\r  {dest.name[:40]:40} {human(done)}/{human(total)}")
                            sys.stderr.flush()
                if total and done < total:
                    raise OSError(f"connection closed early at {done}/{total} bytes")
        except urllib.error.HTTPError as e:
            if e.code == 416 and part.exists():  # already complete
                pass
            elif e.code in (429, 500, 502, 503, 504):
                last = e
                time.sleep(min(60, 2 ** attempt * (5 if e.code == 429 else 1)))
                continue
            else:
                raise FetchError(hint_for(url, e)) from e
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as e:
            last = e
            time.sleep(min(30, 2 ** attempt))
            continue
        if not quiet and sys.stderr.isatty():
            sys.stderr.write("\r" + " " * 90 + "\r")
        if validate is not None and not validate(part):
            part.unlink(missing_ok=True)
            raise FetchError(f"Downloaded file from {url} failed validation (wrong content type, "
                             f"an LFS pointer, or a login page).")
        sha = sha256_file(part)
        if expect_sha and sha != expect_sha:
            part.unlink(missing_ok=True)
            raise FetchError(f"Checksum mismatch for {dest.name}: expected {expect_sha[:12]}…, "
                             f"got {sha[:12]}…. Upstream changed, or the download was corrupted. "
                             f"Delete manifest/pins.json entry to accept the new file.")
        os.replace(part, dest)  # atomic
        return sha
    raise FetchError(hint_for(url, last) if last else f"Failed to download {url}")


def safe_extract_zip(zpath: Path, outdir: Path, *, strip_top: bool = True,
                     keep=lambda rel: True) -> int:
    """Extract with zip-slip protection. strip_top drops the single top folder GitHub adds.
    `keep(rel_posix_path)` filters members. Returns number of files written."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    root = outdir.resolve()
    n = 0
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            rel = info.filename.replace("\\", "/")
            if strip_top and "/" in rel:
                rel = rel.split("/", 1)[1]
            if not rel or not keep(rel):
                continue
            target = (root / rel).resolve()
            if root not in target.parents:
                continue  # zip-slip
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(_long(target), "wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK)
            n += 1
    return n


def _long(p: Path) -> str:
    """Windows long-path prefix so deep repo trees do not hit MAX_PATH."""
    s = str(p)
    if os.name == "nt" and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + os.path.abspath(s)
    return s


def is_good_zip(path: Path) -> bool:
    """Whole-archive check; a truncated or spliced zip fails here and is re-downloaded."""
    try:
        with zipfile.ZipFile(path) as z:
            return z.testzip() is None
    except (zipfile.BadZipFile, OSError):
        return False


def free_bytes(path: Path) -> int:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free


def load_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def looks_like_lfs_pointer(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(40).startswith(b"version https://git-lfs")
    except OSError:
        return False


def is_pdf(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False
