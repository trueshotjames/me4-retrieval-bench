"""Download all five datasets from their official sources. Standard library only.

  python tools/fetch_data.py              # fetch everything (resumable; re-run after any failure)
  python tools/fetch_data.py --dry-run    # list what would be downloaded (probes sizes)
  python tools/fetch_data.py --offline --dry-run   # list without touching the network
"""
from __future__ import annotations

import argparse
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
    ap.add_argument("--write-pins", action="store_true", help="write sha256 of fetched files to manifest/pins.json")
    a = ap.parse_args(argv)

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
        if not a.dry_run and not a.force and m.ready(root):
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
        print(f"\nFailed: {', '.join(failed)}")
        return 1
    print("\nOK. Next: python -m me4bench build-manifest" if not a.dry_run else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
