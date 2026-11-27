"""build-manifest: load all five datasets, assign train/test, verify against the expected strata,
write data/_manifest/<ds>.jsonl. A mismatch means the upstream data differs from what the split was
drawn on (the split is by row index), so we say so loudly and still write the files."""
from __future__ import annotations

import collections
import json
from pathlib import Path

from . import fetchlib as fl
from . import sampler
from .datasets import frames, mimotable, mmlb, multihop, repoprobe

LOADERS = {"frames": frames, "multihop": multihop, "mmlb": mmlb, "repoprobe": repoprobe, "mimotable": mimotable}
REPO = Path(__file__).resolve().parent.parent


def _normalise(text: str) -> str:
    try:
        from .speech import normalise
        return normalise(text)
    except ImportError:
        return text


def manifest_dir(root: Path) -> Path:
    return Path(root) / "_manifest"


def load_rows(root: Path, ds: str) -> list:
    """Rows of one dataset from the written manifest."""
    p = manifest_dir(root) / f"{ds}.jsonl"
    if not p.exists():
        raise FileNotFoundError(f"{p} missing: run `python -m me4bench build-manifest` first.")
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_all(root: Path, exclude=(), datasets=None) -> list:
    out = []
    for ds in LOADERS:
        if ds not in exclude and (not datasets or ds in datasets):
            out += load_rows(root, ds)
    return out


def verify(rows_by_ds: dict, expected: dict) -> list:
    """Return human-readable problems (empty = everything matches)."""
    bad = []
    for ds, rows in rows_by_ds.items():
        got = collections.defaultdict(lambda: [0, 0])
        for r in rows:
            g = got[r["stratum"]]
            g[0] += 1
            g[1] += r["split"] == "test"
        for st, e in expected.get("strata", {}).get(ds, {}).items():
            n, t = got.get(st, [0, 0])
            if (n, t) != (e["n"], e["test"]):
                bad.append(f"{ds} / {st}: have n={n} test={t}, expected n={e['n']} test={e['test']}")
    return bad


def build(root: Path, only=None, log=print) -> int:
    root = Path(root)
    split = sampler.load_split(str(REPO / "manifest" / "split.json"))
    expected = fl.load_json(REPO / "manifest" / "strata_expected.json", {})
    out, problems = {}, []
    for ds, mod in LOADERS.items():
        if only and ds not in only:
            continue
        if not mod.ready(root):
            problems.append(f"{ds}: not downloaded yet (run tools/fetch_data.py)")
            continue
        try:
            rows = mod.load(root)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{ds}: could not load ({type(e).__name__}: {e})")
            continue
        if any(not r["question"] for r in rows) or any(r["answerable"] and not r["gold"] and ds != "repoprobe" for r in rows):
            problems.append(f"{ds}: some rows have an empty question or gold answer (upstream field names differ from the loader's guess)")
        for r in rows:
            r["spoken"] = _normalise(r["question"])
        sampler.assign_split(rows, split)
        out[ds] = rows
        md = manifest_dir(root)
        md.mkdir(parents=True, exist_ok=True)
        with open(md / f"{ds}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        nt = sum(r["split"] == "test" for r in rows)
        log(f"[{ds}] {len(rows)} questions, {nt} test")
    problems += verify(out, expected)
    (manifest_dir(root)).mkdir(parents=True, exist_ok=True)
    fl.save_json(manifest_dir(root) / "_status.json", {"ok": not problems and not only, "problems": problems[:100]})
    if problems:
        log("\nCheck these (the data may differ from what the split was drawn on):")
        for p in problems[:40]:
            log("  - " + p)
        if len(problems) > 40:
            log(f"  ... {len(problems) - 40} more")
        return 1
    log("\nAll strata match the expected counts. Test set: "
        f"{sum(r['split'] == 'test' for v in out.values() for r in v)} questions.")
    return 0
