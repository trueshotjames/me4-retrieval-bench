"""Tests for me4bench.sampler (synthetic rows built from the real stratum names)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from me4bench import sampler, slots  # noqa: E402

_ANSWERED_BUCKETS = ["1", "1", "2", "3-4", "1", "2", "3-4", "5+", "1", "2"]


def _bucket(ds, st, i):
    if ds == "repoprobe":
        return "n/a"
    if st.startswith("Null") or st.startswith("Unanswerable"):
        return "0"
    if "4+" in st:
        return ["3-4", "5+", "3-4"][i % 3]
    return _ANSWERED_BUCKETS[i % len(_ANSWERED_BUCKETS)]


def make_rows(test_rounds=6, extra=0, train_per_slot=3):
    """Pool with exactly ``test_rounds * slots (+extra)`` test rows per stratum."""
    rows, idx = [], Counter()
    for ds, strata in slots.SLOTS.items():
        for st, n in strata.items():
            for split, count in (("test", test_rounds * n + extra), ("train", train_per_slot * n)):
                for i in range(count):
                    k = idx[ds]
                    idx[ds] += 1
                    rows.append({"id": "%s-%04d" % (ds, k), "ds": ds, "idx": k,
                                 "question": "q", "stratum": st,
                                 "bucket": _bucket(ds, st, i), "split": split,
                                 "answerable": True, "gold": "g", "meta": {}})
    k = idx["mmlb"]
    for i in range(5):  # NO_SLOT rows are never drawn
        rows.append({"id": "mmlb-%04d" % (k + i), "ds": "mmlb", "idx": k + i,
                     "stratum": slots.NO_SLOT, "bucket": "1", "split": "test"})
        rows.append({"id": "mmlb-%04d" % (k + 100 + i), "ds": "mmlb", "idx": k + 100 + i,
                     "stratum": slots.NO_SLOT, "bucket": "1", "split": "train"})
    return rows


class TestPlanRounds(unittest.TestCase):
    def setUp(self):
        self.rows = make_rows()

    def test_six_rounds_exact_slots_no_repeats(self):
        rounds, seed, repeats = sampler.plan_rounds(self.rows, "test", 6, seed=42)
        self.assertEqual(seed, 42)
        self.assertEqual(repeats, [])
        self.assertEqual(len(rounds), 6)
        all_ids = [r["id"] for rnd in rounds for r in rnd]
        self.assertEqual(len(all_ids), len(set(all_ids)), "a question repeated")
        for st in sampler.round_stats(rounds):
            self.assertEqual(st["n"], 40)
            self.assertEqual(dict(st["datasets"]), {d: 8 for d in slots.DATASETS})
            expected = {(ds, s): n for ds, v in slots.SLOTS.items() for s, n in v.items()}
            self.assertEqual(dict(st["strata"]), expected)
            self.assertEqual(st["repeats"], 0)
        for rnd in rounds:
            for r in rnd:
                self.assertEqual(r["split"], "test")
                self.assertNotEqual(r["stratum"], slots.NO_SLOT)
                self.assertNotIn("repeat", r)
            self.assertEqual([r["pos"] for r in rnd], list(range(1, 41)))

    def test_reproducible_by_seed(self):
        a = sampler.plan_rounds(self.rows, "test", 6, seed=7)[0]
        b = sampler.plan_rounds(list(reversed(self.rows)), "test", 6, seed=7)[0]
        self.assertEqual([[r["id"] for r in x] for x in a], [[r["id"] for r in x] for x in b])
        c = sampler.plan_rounds(self.rows, "test", 6, seed=8)[0]
        self.assertNotEqual([[r["id"] for r in x] for x in a],
                            [[r["id"] for r in x] for x in c])

    def test_random_seed_is_returned_and_reusable(self):
        rounds, seed, _ = sampler.plan_rounds(self.rows, "test", 2)
        self.assertIsInstance(seed, int)
        again = sampler.plan_rounds(self.rows, "test", 2, seed=seed)[0]
        self.assertEqual([[r["id"] for r in x] for x in rounds],
                         [[r["id"] for r in x] for x in again])

    def test_order_mixes_datasets(self):
        rounds = sampler.plan_rounds(self.rows, "test", 1, seed=3)[0]
        first8 = [r["ds"] for r in rounds[0][:8]]
        self.assertGreater(len(set(first8)), 1)

    def test_repeats_after_pool_runs_dry(self):
        rounds, _, repeats = sampler.plan_rounds(self.rows, "test", 7, seed=1)
        self.assertEqual(len(repeats), 40)   # every stratum had exactly 6 rounds
        r7 = rounds[6]
        self.assertTrue(all(r.get("repeat") for r in r7))
        self.assertEqual(len({r["id"] for r in r7}), 40)  # no duplicate inside a round
        self.assertEqual(sorted(repeats), sorted(r["id"] for r in r7))
        for st in sampler.round_stats(rounds):
            self.assertEqual(st["n"], 40)

    def test_single_dataset(self):
        rounds, _, _ = sampler.plan_rounds(self.rows, "test", 6, seed=5, datasets=["mmlb"])
        for rnd in rounds:
            self.assertEqual(len(rnd), 8)
            self.assertTrue(all(r["ds"] == "mmlb" for r in rnd))
            self.assertEqual(Counter(r["stratum"] for r in rnd), Counter(slots.SLOTS["mmlb"]))
        ids = [r["id"] for rnd in rounds for r in rnd]
        self.assertEqual(len(ids), len(set(ids)))

    def test_train_mode(self):
        rounds, _, repeats = sampler.plan_rounds(self.rows, "train", 3, seed=2)
        self.assertEqual(repeats, [])
        for rnd in rounds:
            self.assertEqual(len(rnd), 40)
            self.assertTrue(all(r["split"] == "train" for r in rnd))

    def test_bucket_mix_near_target(self):
        rows = make_rows(test_rounds=6, extra=6)
        rounds, _, _ = sampler.plan_rounds(rows, "test", 6, seed=11)
        cum = Counter()
        for i, st in enumerate(sampler.round_stats(rounds), 1):
            cum.update(st["buckets"])
            for b, tgt in slots.BUCKET_TARGET.items():
                self.assertLessEqual(abs(cum[b] - tgt * i), 2.0 + 1e-9,
                                     "bucket %s after round %d: %d vs %.1f" % (b, i, cum[b], tgt * i))

    def test_empty_stratum_is_an_error(self):
        rows = [r for r in self.rows if r["stratum"] != "Business logic"]
        with self.assertRaises(ValueError):
            sampler.plan_rounds(rows, "test", 1, seed=1)

    def test_bad_mode(self):
        with self.assertRaises(ValueError):
            sampler.plan_rounds(self.rows, "dev", 1)


class TestExclude(unittest.TestCase):
    def test_effective_slots_default_is_slots(self):
        self.assertEqual(sampler.effective_slots(), slots.SLOTS)

    def test_effective_slots_without_repoprobe(self):
        t = sampler.effective_slots(("repoprobe",))
        self.assertNotIn("repoprobe", t)
        self.assertEqual(sum(sum(v.values()) for v in t.values()), 40)
        for ds, v in t.items():
            self.assertEqual(sum(v.values()), 10, ds)
            for st, n in v.items():
                self.assertGreaterEqual(n, slots.SLOTS[ds][st])
        # largest remainder: comparison (3 -> 0.75) first, then inference (tie, order)
        self.assertEqual(t["multihop"]["Comparison query"], 4)
        self.assertEqual(t["multihop"]["Inference query"], 3)
        self.assertEqual(t["mimotable"]["One sheet, simple header"], 3)
        # ties among one-slot strata break by larger pool: never the 6-question one
        self.assertEqual(t["mmlb"]["Layout text: footnotes, headers, lists"], 1)

    def test_cap_half_pool(self):
        sizes = {ds: {st: 100 for st in v} for ds, v in slots.SLOTS.items()}
        sizes["multihop"] = {st: 100 for st in slots.SLOTS["multihop"]}
        sizes["multihop"]["Comparison query"] = 6       # cap 3: cannot grow
        t = sampler.effective_slots(("repoprobe",), pool_sizes=sizes)
        self.assertEqual(t["multihop"]["Comparison query"], 3)
        self.assertEqual(sum(t["multihop"].values()), 10)
        with self.assertRaises(ValueError):
            sampler.effective_slots(("repoprobe",), pool_sizes={
                "frames": {st: 2 for st in slots.SLOTS["frames"]}})

    def test_rounds_without_repoprobe(self):
        rows = make_rows(test_rounds=8)
        rounds, _, repeats = sampler.plan_rounds(rows, "test", 4, seed=9, exclude=("repoprobe",))
        self.assertEqual(repeats, [])
        for rnd in rounds:
            self.assertEqual(len(rnd), 40)
            c = Counter(r["ds"] for r in rnd)
            self.assertEqual(dict(c), {d: 10 for d in slots.DATASETS if d != "repoprobe"})
            self.assertFalse(any(r["ds"] == "repoprobe" for r in rnd))

    def test_bucket_target_renormalised(self):
        t = sampler.bucket_target(("repoprobe",))
        self.assertNotIn("n/a", t)
        self.assertAlmostEqual(sum(t.values()), 40.0)

    def test_test_count(self):
        split = sampler.load_split()
        rows = [{"ds": ds, "idx": i} for ds, s in split.items() for i in s]
        sampler.assign_split(rows, split)
        self.assertEqual(sampler.test_count(rows), 459)
        self.assertEqual(sampler.test_count(rows, ("repoprobe",)), 459 - len(split["repoprobe"]))
        self.assertEqual(len(split["repoprobe"]), 86)


class TestSplitAndHelpers(unittest.TestCase):
    def test_load_and_assign_split(self):
        split = sampler.load_split()
        self.assertEqual(set(split), set(slots.DATASETS))
        rows = [{"ds": "frames", "idx": 6}, {"ds": "frames", "idx": 7}]
        sampler.assign_split(rows, split)
        self.assertEqual([r["split"] for r in rows], ["test", "train"])

    def test_distinct_limit(self):
        rows = make_rows()
        self.assertEqual(sampler.distinct_limit("test", rows), 6)
        self.assertEqual(sampler.distinct_limit("train", rows), 3)
        self.assertEqual(sampler.distinct_limit("test"), 6)   # expected manifest sizes

    def test_stats_helpers(self):
        self.assertAlmostEqual(sampler.margin_95(100), 9.8, places=1)
        self.assertAlmostEqual(sampler.margin_95(240), 6.33, places=2)
        self.assertEqual(sampler.p95_rank(40), 3)
        self.assertEqual(sampler.p95_rank(20), 2)
        self.assertEqual(sampler.p95_rank(240), 13)
        self.assertEqual(sampler.p95_rank(1), 1)
        self.assertEqual(sampler.estimate_duration(40, 30), 1200.0)
        self.assertEqual(sampler.rounds_for_minutes(60, 30), 3)
        self.assertEqual(sampler.rounds_for_minutes(5, 30), 1)
        self.assertEqual(sampler.rounds_for_minutes(60, 30, per_round=8), 15)
        self.assertEqual(sampler.preset_rounds("full"), 6)
        self.assertEqual(sampler.preset_rounds("hour", 30), 3)
        self.assertEqual(sampler.format_duration(3900), "1 h 05 min")

    def test_counter(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(sampler.test_run_count(d), 0)
            self.assertEqual(sampler.bump_test_counter(d, "ana", seed=1, rounds=6), 1)
            self.assertEqual(sampler.bump_test_counter(d, "ben", seed=2, rounds=1), 2)
            self.assertEqual(sampler.test_run_count(d), 2)
            with open(os.path.join(d, "test_runs.jsonl")) as f:
                recs = [json.loads(x) for x in f]
            self.assertEqual(recs[1]["student"], "ben")
            self.assertEqual(set(recs[0]), {"n", "date", "code_version", "student", "seed", "rounds"})


if __name__ == "__main__":
    unittest.main()
