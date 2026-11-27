"""Rounds and splits: which questions are asked, in which order.

A *round* is 40 questions: the slots of ``slots.SLOTS`` (8 per dataset). Rounds
are drawn by a seeded shuffle per stratum, without repeats across rounds until a
stratum runs dry; then that stratum is reshuffled and the repeated questions are
marked (``row["repeat"] = True`` and listed in ``repeats``).

Inside a stratum the sampler prefers the files-needed bucket with the largest
deficit versus ``slots.BUCKET_TARGET`` scaled to the rounds drawn so far, which
keeps every round close to the target bucket mix.

``exclude=("repoprobe",)`` drops a dataset and redistributes its 8 slots over the
remaining datasets (2 each) so a round stays 40 questions; see
:func:`effective_slots`.

Same seed + same rows + same arguments -> identical plan.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os
import random
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import slots

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_SEC_PER_Q = 25.0   # rough wall time per question incl. reset; used by presets
FULL_TEST_ROUNDS = 6       # "full" preset: 6 test rounds = 240 questions, no repeats


def _resolve(path: str) -> str:
    """Resolve a repo-relative path: cwd first, then the package's repo root."""
    if os.path.isabs(path) or os.path.exists(path):
        return path
    alt = os.path.join(_PKG_ROOT, path)
    return alt if os.path.exists(alt) else path


# --------------------------------------------------------------------- split
def load_split(path: str = "manifest/split.json") -> Dict[str, Set[int]]:
    """Return ``{ds: set(idx)}`` of TEST row indexes from the fixed split file."""
    with open(_resolve(path), encoding="utf-8") as f:
        data = json.load(f)
    test = data.get("test", data)
    return {ds: set(int(i) for i in idxs) for ds, idxs in test.items()
            if isinstance(idxs, list)}


def assign_split(rows: Iterable[dict], split: Dict[str, Set[int]]) -> list:
    """Set ``row["split"]`` to ``"test"`` or ``"train"`` in place; return the rows."""
    out = []
    for r in rows:
        r["split"] = "test" if int(r["idx"]) in split.get(r["ds"], ()) else "train"
        out.append(r)
    return out


def test_count(rows: Iterable[dict], exclude: Sequence[str] = ()) -> int:
    """Number of test questions available, ignoring excluded datasets."""
    ex = set(exclude or ())
    return sum(1 for r in rows if r.get("split") == "test" and r.get("ds") not in ex)


# --------------------------------------------------------------- slot table
def _expected_pool_sizes(mode: str = "test") -> Dict[str, Dict[str, int]]:
    """Pool sizes per stratum from manifest/strata_expected.json (fallback)."""
    try:
        with open(_resolve("manifest/strata_expected.json"), encoding="utf-8") as f:
            data = json.load(f)["strata"]
    except (OSError, ValueError, KeyError):
        return {}
    out: Dict[str, Dict[str, int]] = {}
    for ds, strata in data.items():
        for st, v in strata.items():
            n = int(v.get("test", 0)) if mode == "test" else int(v.get("n", 0)) - int(v.get("test", 0))
            out.setdefault(ds, {})[st] = n
    return out


def effective_slots(exclude: Sequence[str] = (),
                    pool_sizes: Optional[Dict[str, Dict[str, int]]] = None,
                    mode: str = "test") -> Dict[str, Dict[str, int]]:
    """Return the dataset -> stratum -> slots-per-round table after exclusions.

    Without exclusions this is a copy of ``slots.SLOTS``. Each excluded dataset's
    slots are spread evenly over the remaining datasets (8 -> 2 per dataset for
    4 remaining). Inside a dataset the extra slots go to strata by largest
    remainder, in proportion to each stratum's existing slot count; ties break by
    larger pool, then by the stratum order in ``slots.SLOTS``. A stratum never
    gets more than half its pool (``floor(pool/2)``, at least its base slots).
    ``pool_sizes`` (``{ds: {stratum: n}}``) defaults to the expected sizes of
    ``mode`` from manifest/strata_expected.json.
    """
    ex = [d for d in slots.DATASETS if d in set(exclude or ())]
    unknown = set(exclude or ()) - set(slots.DATASETS)
    if unknown:
        raise ValueError("unknown dataset(s) in exclude: %s" % sorted(unknown))
    table = {ds: dict(v) for ds, v in slots.SLOTS.items() if ds not in ex}
    if not ex:
        return table
    if not table:
        raise ValueError("cannot exclude every dataset")
    if pool_sizes is None:
        pool_sizes = _expected_pool_sizes(mode)
    freed = sum(sum(slots.SLOTS[d].values()) for d in ex)
    remaining = [d for d in slots.DATASETS if d in table]
    base, extra = divmod(freed, len(remaining))
    for i, ds in enumerate(remaining):
        add = base + (1 if i < extra else 0)
        strata = list(table[ds])
        total = sum(table[ds].values())
        pools = pool_sizes.get(ds, {})

        def cap(st: str) -> int:
            if st not in pools:
                return 10 ** 9
            return max(slots.SLOTS[ds][st], pools[st] // 2)

        quota = {st: add * table[ds][st] / total for st in strata}
        alloc = {st: int(math.floor(quota[st])) for st in strata}
        alloc = {st: min(alloc[st], cap(st) - table[ds][st]) for st in strata}
        order = sorted(strata, key=lambda st: (-(quota[st] - math.floor(quota[st])),
                                               -pools.get(st, 0), strata.index(st)))
        left = add - sum(alloc.values())
        # largest remainder first; loop again (by order) if caps blocked a stratum
        while left > 0:
            progressed = False
            for st in order:
                if left <= 0:
                    break
                if table[ds][st] + alloc[st] < cap(st):
                    alloc[st] += 1
                    left -= 1
                    progressed = True
            if not progressed:
                raise ValueError("pool of %s too small to absorb %d extra slots" % (ds, add))
        for st in strata:
            table[ds][st] += alloc[st]
    return table


def bucket_target(exclude: Sequence[str] = ()) -> Dict[str, float]:
    """Bucket targets per 40 questions; with RepoProbe excluded, 'n/a' is
    removed and the other buckets are renormalised to 40."""
    tgt = dict(slots.BUCKET_TARGET)
    if "repoprobe" in set(exclude or ()):
        tgt.pop("n/a", None)
        s = sum(tgt.values())
        tgt = {b: v * 40.0 / s for b, v in tgt.items()}
    return tgt


# ------------------------------------------------------------------ planning
def plan_rounds(rows: Iterable[dict], mode: str, n_rounds: int,
                seed: Optional[int] = None,
                datasets: Optional[Sequence[str]] = None,
                exclude: Sequence[str] = ()) -> Tuple[List[List[dict]], int, List[str]]:
    """Plan ``n_rounds`` rounds of questions.

    Returns ``(rounds, seed, repeats)``: ``rounds`` is a list of lists of row
    copies (each copy gets ``round``, ``pos`` and, if repeated, ``repeat=True``);
    ``repeats`` lists the ids drawn a second (or later) time.

    ``mode`` is ``"test"`` or ``"train"``; the pool is rows with that ``split``
    whose stratum is in the slot table (``NO_SLOT`` rows are never drawn).
    ``datasets`` restricts which datasets are asked (8 slots each per round);
    ``exclude`` drops datasets and redistributes their slots (see
    :func:`effective_slots`).
    """
    if mode not in ("test", "train"):
        raise ValueError("mode must be 'test' or 'train'")
    if n_rounds < 1:
        raise ValueError("n_rounds must be >= 1")
    if seed is None:
        seed = random.SystemRandom().randrange(1, 2 ** 31)
    seed = int(seed)
    rng = random.Random(seed)

    rows = list(rows)
    pool_rows: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for r in rows:
        if r.get("split") == mode and r.get("ds") in slots.SLOTS \
                and r.get("stratum") in slots.SLOTS[r["ds"]]:
            pool_rows[(r["ds"], r["stratum"])].append(r)
    sizes: Dict[str, Dict[str, int]] = {}
    for (ds, st), lst in pool_rows.items():
        sizes.setdefault(ds, {})[st] = len(lst)
    table = effective_slots(exclude, pool_sizes=sizes, mode=mode)

    if datasets:
        bad = set(datasets) - set(slots.DATASETS)
        if bad:
            raise ValueError("unknown dataset(s): %s" % sorted(bad))
        active = [d for d in slots.DATASETS if d in set(datasets) and d in table]
        if not active:
            raise ValueError("no datasets left to ask after exclusions")
    else:
        active = [d for d in slots.DATASETS if d in table]

    keys = [(ds, st) for ds in active for st in table[ds] if table[ds][st] > 0]
    for k in keys:
        if not pool_rows.get(k):
            raise ValueError("no %s questions for %s / %s" % (mode, k[0], k[1]))
        pool_rows[k].sort(key=lambda r: r["id"])

    per_round = sum(table[ds][st] for ds, st in keys)
    target = bucket_target(exclude)
    scale = per_round / 40.0

    remaining: Dict[Tuple[str, str], List[dict]] = {}
    for k in keys:
        lst = list(pool_rows[k])
        rng.shuffle(lst)
        remaining[k] = lst
    used_ever: Set[str] = set()
    bucket_counts: Counter = Counter()
    rounds: List[List[dict]] = []
    repeats: List[str] = []

    for r_i in range(n_rounds):
        expected = {b: v * scale * (r_i + 1) for b, v in target.items()}
        order = list(keys)
        rng.shuffle(order)
        in_round: Set[str] = set()
        picked: List[dict] = []
        for k in order:
            for _ in range(table[k[0]][k[1]]):
                cand = [c for c in remaining[k] if c["id"] not in in_round]
                if not cand:
                    refill = list(pool_rows[k])
                    rng.shuffle(refill)
                    remaining[k] = refill
                    cand = [c for c in refill if c["id"] not in in_round] or refill
                best_i, best_def = 0, None
                for i, c in enumerate(cand):
                    d = expected.get(str(c.get("bucket")), 0.0) - bucket_counts[str(c.get("bucket"))]
                    if best_def is None or d > best_def:
                        best_i, best_def = i, d
                chosen = cand[best_i]
                remaining[k].remove(chosen)
                in_round.add(chosen["id"])
                bucket_counts[str(chosen.get("bucket"))] += 1
                cp = dict(chosen)
                cp["round"] = r_i + 1
                if chosen["id"] in used_ever:
                    cp["repeat"] = True
                    repeats.append(chosen["id"])
                used_ever.add(chosen["id"])
                picked.append(cp)
        rng.shuffle(picked)
        for pos, cp in enumerate(picked, 1):
            cp["pos"] = pos
        rounds.append(picked)
    return rounds, seed, repeats


def round_stats(rounds: List[List[dict]]) -> List[dict]:
    """Per round: ``{"n", "buckets", "strata", "datasets", "repeats"}`` counters.

    ``strata`` is keyed by ``(ds, stratum)``.
    """
    out = []
    for rnd in rounds:
        out.append({
            "n": len(rnd),
            "buckets": Counter(str(r.get("bucket")) for r in rnd),
            "strata": Counter((r["ds"], r["stratum"]) for r in rnd),
            "datasets": Counter(r["ds"] for r in rnd),
            "repeats": sum(1 for r in rnd if r.get("repeat")),
        })
    return out


def distinct_limit(mode: str, rows: Optional[Iterable[dict]] = None,
                   datasets: Optional[Sequence[str]] = None,
                   exclude: Sequence[str] = ()) -> int:
    """Rounds that can be drawn before any question repeats.

    ``min over slotted strata of floor(pool_size / slots)``. Uses the actual
    pool in ``rows`` when given, else manifest/strata_expected.json.
    """
    if rows is not None:
        sizes: Dict[str, Dict[str, int]] = {}
        for r in rows:
            if r.get("split") == mode and r.get("ds") in slots.SLOTS \
                    and r.get("stratum") in slots.SLOTS[r["ds"]]:
                d = sizes.setdefault(r["ds"], {})
                d[r["stratum"]] = d.get(r["stratum"], 0) + 1
    else:
        sizes = _expected_pool_sizes(mode)
    table = effective_slots(exclude, pool_sizes=sizes, mode=mode)
    active = [d for d in table if not datasets or d in set(datasets)]
    lim = None
    for ds in active:
        for st, n_slots in table[ds].items():
            if n_slots <= 0:
                continue
            v = sizes.get(ds, {}).get(st, 0) // n_slots
            lim = v if lim is None else min(lim, v)
    return lim or 0


# ------------------------------------------------------- statistics helpers
def margin_95(n: int, p: float = 0.5) -> float:
    """Worst-case 95% margin of error in percentage points (normal approx.)."""
    if n <= 0:
        return float("nan")
    return 100.0 * 1.96 * math.sqrt(p * (1.0 - p) / n)


def p95_rank(n: int) -> int:
    """Which slowest sample is the p95 (nearest-rank): k-th slowest of n.

    ``k = n - ceil(0.95 n) + 1``; e.g. 40 -> 3 (the 3rd slowest), 240 -> 13.
    """
    if n <= 0:
        return 0
    return n - int(math.ceil(0.95 * n - 1e-9)) + 1


def estimate_duration(n_questions: int, sec_per_q: float = DEFAULT_SEC_PER_Q) -> float:
    """Estimated wall time in seconds."""
    return float(n_questions) * float(sec_per_q)


def format_duration(seconds: float) -> str:
    """'1 h 05 min' / '12 min' / '45 s'."""
    s = int(round(seconds))
    if s < 60:
        return "%d s" % s
    m = s // 60
    if m < 60:
        return "%d min" % m
    return "%d h %02d min" % (m // 60, m % 60)


def rounds_for_minutes(minutes: float, sec_per_q: float = DEFAULT_SEC_PER_Q,
                       per_round: int = slots.SLOTS_PER_ROUND) -> int:
    """Whole rounds that fit in ``minutes`` (at least 1)."""
    if sec_per_q <= 0 or per_round <= 0:
        return 1
    return max(1, int(math.floor(minutes * 60.0 / (per_round * sec_per_q) + 1e-9)))


def preset_rounds(kind: str, sec_per_q: float = DEFAULT_SEC_PER_Q,
                  per_round: int = slots.SLOTS_PER_ROUND) -> int:
    """Rounds for a preset: ``"full"`` = 6 test rounds, ``"hour"`` = what fits
    in 60 minutes, ``"round"``/``"quick"`` = 1."""
    k = kind.lower()
    if k == "full":
        return FULL_TEST_ROUNDS
    if k == "hour":
        return rounds_for_minutes(60, sec_per_q, per_round)
    if k in ("round", "quick", "one"):
        return 1
    raise ValueError("unknown preset %r (full, hour, round)" % kind)


# ------------------------------------------------------------ test counter
_COUNTER_FILE = "test_runs.jsonl"


def test_run_count(runs_dir: str = "runs") -> int:
    """Number of test runs recorded in ``runs/test_runs.jsonl``."""
    path = os.path.join(runs_dir, _COUNTER_FILE)
    n = 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    n += 1
    except OSError:
        return 0
    return n


def bump_test_counter(runs_dir: str, student: str, seed: Optional[int] = None,
                      rounds: Optional[int] = None,
                      code_version: Optional[str] = None) -> int:
    """Append one test-run record and return this run's number (1-based)."""
    if code_version is None:
        from . import __version__ as code_version  # type: ignore
    os.makedirs(runs_dir, exist_ok=True)
    n = test_run_count(runs_dir) + 1
    rec = {"n": n, "date": _dt.datetime.now().isoformat(timespec="seconds"),
           "code_version": code_version, "student": student, "seed": seed,
           "rounds": rounds}
    with open(os.path.join(runs_dir, _COUNTER_FILE), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
    return n
