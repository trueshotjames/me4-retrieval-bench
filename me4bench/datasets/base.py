"""Shared pieces for dataset modules: fetch context, question-row builder, helpers."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import fetchlib as fl
from ..slots import NO_SLOT

BUCKETS = ["0", "1", "2", "3-4", "5+", "n/a"]


def bucket_for(n_docs: int) -> str:
    return "0" if n_docs == 0 else "1" if n_docs == 1 else "2" if n_docs == 2 else "3-4" if n_docs <= 4 else "5+"


def pick(d: dict, keys, default=""):
    """First present key (case-insensitive) in dict d."""
    low = {k.lower(): k for k in d}
    for k in keys:
        if k.lower() in low and d[low[k.lower()]] not in (None, ""):
            return d[low[k.lower()]]
    return default


def safe_name(s: str, limit: int = 120) -> str:
    """Windows-safe file name stem."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip(" .")
    if len(s) > limit:
        import hashlib
        s = s[: limit - 9] + "_" + hashlib.sha1(s.encode()).hexdigest()[:8]
    return s or "_"


def make_row(ds: str, idx: int, question: str, gold: str, *, answerable: bool, gold_docs: list,
             stratum: str, bucket: str, meta: dict | None = None) -> dict:
    return {"id": f"{ds}-{idx:04d}", "ds": ds, "idx": idx, "question": question.strip(),
            "spoken": "", "gold": gold if answerable else "", "answerable": answerable,
            "gold_docs": list(gold_docs), "stratum": stratum, "bucket": bucket, "split": "train",
            "meta": meta or {}}


@dataclass
class Ctx:
    """What a dataset's fetch() needs."""
    root: Path                       # data/
    dry_run: bool = False
    offline: bool = False            # dry-run without touching the network
    force: bool = False
    pins: dict = field(default_factory=dict)       # manifest/pins.json: relpath -> sha256
    lock: dict = field(default_factory=dict)       # data/_fetch/lock.json (this machine's hashes)
    log: callable = print
    plan: list = field(default_factory=list)       # dry-run: [(dataset, what, url, est_bytes)]
    jobs: int = 4

    def dir(self, ds: str) -> Path:
        d = self.root / ds
        d.mkdir(parents=True, exist_ok=True)
        return d

    def note(self, ds, what, url, est=None):
        self.plan.append((ds, what, url, est))

    def get(self, ds: str, url: str, dest: Path, validate=None, progress=None) -> Path:
        """Download with pin/lock checking. In dry-run, only records the plan."""
        rel = f"{ds}/" + dest.relative_to(self.root / ds).as_posix()
        if self.dry_run:
            info = {"ok": None} if self.offline else fl.probe(url)
            self.note(ds, rel, url, info.get("size"))
            if info.get("ok") is False:
                self.log(f"  ! {rel}: {info['error']}")
            return dest
        if self.force and dest.exists():
            dest.unlink()
        sha = fl.download(url, dest, expect_sha=self.pins.get(rel), validate=validate, progress=progress)
        if rel in self.lock and self.lock[rel] != sha and rel not in self.pins:
            self.log(f"  ! {rel}: upstream content changed since your last fetch "
                     f"(was {self.lock[rel][:10]}…, now {sha[:10]}…).")
        self.lock[rel] = sha
        return dest
