"""Download all five datasets from their official sources, then build and check the question
manifest. Standard library only.

  python tools/fetch_data.py              # fetch everything + build-manifest (resumable; re-run after any failure)
  python tools/fetch_data.py --dry-run    # list what would be downloaded (probes sizes)
  python tools/fetch_data.py --offline --dry-run   # list without touching the network
"""
from __future__ import annotations

import argparse
import os
import platform
import sys
import time
from pathlib import Path

from . import fetchlib as fl
from .datasets import base, frames, mimotable, mmlb, multihop, repoprobe
from .datasets.base import Ctx

MODS = {"frames": frames, "multihop": multihop, "mmlb": mmlb, "repoprobe": repoprobe, "mimotable": mimotable}
EST = {"frames": 0.3, "multihop": 0.01, "mmlb": 0.7, "repoprobe": 0.5, "mimotable": 0.01}  # GB, rough
REPO = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fetch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(REPO / "data"), help="where datasets go (default: ./data)")
    ap.add_argument("--dry-run", action="store_true", help="only list downloads")
    ap.add_argument("--offline", action="store_true", help="with --dry-run: do not probe the network")
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    ap.add_argument("--only", choices=sorted(MODS), nargs="+", help="debug: fetch only these (normal use: all five)")
    ap.add_argument("--no-mixed-formats", action="store_true",
                    help="store every FRAMES article as .md and every MiMoTable workbook as .xlsx "
                         "(default: ~30%% of each become docx/html/txt/odt and ods/csv/tsv/xls)")
    ap.add_argument("--no-manifest", action="store_true", help="skip the build-manifest step at the end")
    ap.add_argument("--write-pins", action="store_true", help="write sha256 of fetched files to manifest/pins.json")
    a = ap.parse_args(argv)

    if a.no_mixed_formats:
        os.environ["ME4_FRAMES_MIXED_SHARE"] = "0"
        os.environ["ME4_MIMOTABLE_MIXED_SHARE"] = "0"
    root = Path(a.root)
    pins = fl.load_json(REPO / "manifest" / "pins.json", {}) or {}
    lockp = root / "_fetch" / "lock.json"
    ctx = Ctx(root=root, dry_run=a.dry_run, offline=a.offline, force=a.force, pins=pins,
              lock=fl.load_json(lockp, {}) or {}, log=lambda s: print(s, flush=True))
    names = a.only or list(MODS)
    print(f"Python {platform.python_version()} on {platform.system()} {platform.machine()}; data dir: {root}")
    if not a.dry_run:
        need = sum(EST[n] for n in names if not MODS[n].ready(root)) * 2 ** 30 * 1.5
        free = fl.free_bytes(root)
        if need and free < need:
            print(f"Not enough disk: need about {fl.human(need)} free, have {fl.human(free)} at {root}.")
            return 2
    failed = []
    for n in names:
        m = MODS[n]
        if not a.dry_run and not a.force and m.ready(root) and not _retry_frames(n, root):
            print(f"[{n}] already downloaded")
            continue
        print(f"[{n}] {'planning' if a.dry_run else 'downloading'} ...", flush=True)
        t0 = time.time()
        try:
            m.fetch(ctx)
        except fl.FetchError as e:
            failed.append(n)
            print(f"[{n}] FAILED: {e}\n       Re-run the same command; finished parts are kept.")
        except Exception as e:  # noqa: BLE001
            failed.append(n)
            print(f"[{n}] FAILED: {type(e).__name__}: {e}")
        else:
            if not a.dry_run:
                print(f"[{n}] done in {time.time() - t0:.0f}s")
        if not a.dry_run:
            fl.save_json(lockp, ctx.lock)
    if a.dry_run:
        print("\nPlan:")
        tot = 0
        for ds, what, url, est in ctx.plan:
            tot += est or 0
            print(f"  {ds:10} {what:40} {fl.human(est) if est else '?':>10}  {url}")
        print(f"  total known: {fl.human(tot)} (rough full size ~{sum(EST[n] for n in names):.1f} GB)")
    elif a.write_pins:
        fl.save_json(REPO / "manifest" / "pins.json", ctx.lock)
        print("wrote manifest/pins.json")
    if failed:
        print(f"\nFailed: {', '.join(failed)}. Re-run the same command; finished parts are kept.")
        return 1
    if a.dry_run or a.no_manifest:
        return 0
    if a.only:
        print("\nOK (partial fetch with --only; build-manifest skipped).")
        return 0
    print("\nBuilding the question manifest ...", flush=True)
    from .manifest import build
    rc = build(root)
    print("\nAll done. Next: python -m me4bench menu" if rc == 0 else "")
    return rc


def _retry_frames(name: str, root: Path) -> bool:
    """FRAMES articles that failed last time are retried on every normal run (existing ones are kept)."""
    if name != "frames":
        return False
    bad = fl.load_json(root / "frames" / "_failed.json", None)
    if bad:
        print(f"[frames] retrying {len(bad)} article(s) that failed last time")
    return bool(bad)


if __name__ == "__main__":
    sys.exit(main())
