"""Tests for me4bench.scoring (synthetic rows, offline judges only)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from me4bench.scoring import (Judge, aggregate, common, merge, mmlb,  # noqa: E402
                              multihop, parse_verdict, repoprobe, score_question)

H = Judge("heuristic")
NONE = Judge("none")
NF = "I could not find that in the files."


def row(ds, gold="Ada Lovelace", answerable=True, **kw):
    r = {"id": ds + "-0001", "ds": ds, "question": "Who?", "gold": gold,
         "answerable": answerable, "stratum": "s", "bucket": "1", "meta": {}}
    r.update(kw)
    return r


def res(answer, **kw):
    r = {"answer": answer, "error": None, "failed": False, "citations": None}
    r.update(kw)
    return r


class TestBinaryScorers(unittest.TestCase):
    def test_gold_and_wrong(self):
        for ds in ("frames", "mimotable", "multihop", "repoprobe"):
            with self.subTest(ds=ds):
                good = score_question(row(ds), res("Ada Lovelace [a.txt, 1]"), H)
                self.assertEqual(good["score"], 1.0)
                self.assertTrue(good["correct"])
                bad = score_question(row(ds), res("Charles Babbage."), H)
                self.assertEqual(bad["score"], 0.0)
                self.assertFalse(bad["correct"])

    def test_not_found_both_ways(self):
        for ds in ("frames", "mimotable", "multihop", "repoprobe", "mmlb"):
            with self.subTest(ds=ds):
                un = row(ds, gold="", answerable=False)
                self.assertEqual(score_question(un, res(NF), H)["score"], 1.0)
                self.assertEqual(score_question(un, res("It was Ada Lovelace."), H)["score"],
                                 0.0)
                # answerable question answered "not found" is wrong
                self.assertEqual(score_question(row(ds), res(NF), H)["score"], 0.0)

    def test_needs_judge(self):
        out = score_question(row("frames"), res("The first programmer, Countess Ada."), NONE)
        self.assertTrue(out["needs_judge"])
        self.assertEqual(out["metric"], "needs_judge")
        # exact match needs no judge
        out = score_question(row("frames"), res("Ada Lovelace."), NONE)
        self.assertFalse(out["needs_judge"])
        self.assertEqual(out["metric"], "exact")

    def test_failed_result(self):
        out = score_question(row("frames"), res("", error="timeout", failed=True), H)
        self.assertEqual(out["metric"], "failed")
        self.assertEqual(out["score"], 0.0)

    def test_mimotable_numeric(self):
        r = row("mimotable", gold="1,234.5")
        self.assertEqual(score_question(r, res("The total is 1234.6."), NONE)["metric"],
                         "numeric")
        self.assertEqual(score_question(r, res("The total is 1500."), H)["score"], 0.0)

    def test_multihop_yes_no_and_lenient(self):
        r = row("multihop", gold="Yes")
        out = score_question(r, res("Yes, both reports agree."), NONE)
        self.assertEqual((out["score"], out["metric"]), (1.0, "yes_no"))
        self.assertEqual(score_question(r, res("No, they differ."), NONE)["score"], 0.0)
        r = row("multihop", gold="Ada Lovelace")
        out = score_question(r, res("Lovelace's colleague Babbage."), H)
        self.assertIn("lenient", out["detail"])
        self.assertTrue(multihop.lenient_correct(r, "Ada was there"))
        self.assertFalse(multihop.lenient_correct(r, "Babbage"))
        self.assertTrue(multihop.has_intersection("a b", "b c"))

    def test_cited_ok(self):
        r = row("frames", gold_docs=["wiki/Ada_Lovelace.txt"])
        ok = score_question(r, res("Ada Lovelace", citations=[{"file": "Ada_Lovelace.txt",
                                                                "page": 1}]), H)
        self.assertTrue(ok["cited_ok"])
        bad = score_question(r, res("Ada Lovelace", citations=[{"file": "other.txt"}]), H)
        self.assertFalse(bad["cited_ok"])
        none = score_question(r, res("Ada Lovelace"), H)
        self.assertIsNone(none["cited_ok"])


class TestMMLB(unittest.TestCase):
    def test_eval_score_formats(self):
        self.assertEqual(mmlb.eval_score("12", "12", "Int"), 1.0)
        self.assertEqual(mmlb.eval_score("12", "13", "Int"), 0.0)
        self.assertEqual(mmlb.eval_score("0.255", "25.5", "Float"), 1.0)   # x100
        self.assertEqual(mmlb.eval_score("3.14", "3.15", "Float"), 1.0)    # within 1%
        self.assertEqual(mmlb.eval_score("3.14", "3.5", "Float"), 0.0)
        self.assertEqual(mmlb.eval_score("Lovelace", "Lovelace", "Str"), 1.0)
        self.assertGreater(mmlb.eval_score("Lovelace", "Lovelase", "Str"), 0.5)
        self.assertEqual(mmlb.eval_score("Lovelace", "Babbage", "Str"), 0.0)
        self.assertEqual(mmlb.eval_score("['a', 'b']", "['b', 'a']", "List"), 1.0)
        self.assertEqual(mmlb.eval_score("['a', 'b']", "['a']", "List"), 0.0)

    def test_score_heuristic_and_pages(self):
        r = row("mmlb", gold="42", meta={"answer_format": "Int", "evidence_pages": "[3, 4]"})
        good = score_question(r, res("There are 42 sites [doc.pdf, 3]",
                                     citations=[{"file": "doc.pdf", "page": 3}]), H)
        self.assertEqual((good["score"], good["metric"]), (1.0, "rule_heuristic"))
        self.assertTrue(good["cited_ok"])
        bad = score_question(r, res("There are 41 sites",
                                    citations=[{"file": "doc.pdf", "page": 9}]), H)
        self.assertEqual(bad["score"], 0.0)
        self.assertFalse(bad["cited_ok"])
        self.assertIsNone(score_question(r, res("42"), H)["cited_ok"])
        lst = row("mmlb", gold="['red', 'blue']", meta={"answer_format": "List"})
        self.assertEqual(score_question(lst, res("Red and blue."), H)["score"], 1.0)
        self.assertEqual(score_question(lst, res("Only red."), H)["score"], 0.0)


class TestRepoProbe(unittest.TestCase):
    def setUp(self):
        self.row = row("repoprobe", gold="", meta={"checklist": [
            {"item": "config loader reads yaml", "weight": 3},
            "retries with backoff"]})

    def test_weights(self):
        full = score_question(self.row, res("The config loader reads yaml and retries "
                                            "with backoff."), H)
        self.assertEqual(full["score"], 1.0)
        self.assertTrue(full["detail"]["perfect"])
        part = score_question(self.row, res("The config loader reads yaml files."), H)
        self.assertAlmostEqual(part["score"], 0.75)
        self.assertTrue(part["correct"])
        self.assertFalse(part["detail"]["perfect"])
        self.assertEqual(part["detail"]["score10"], 7.5)
        none = score_question(self.row, res("It is written in Go."), H)
        self.assertEqual(none["score"], 0.0)

    def test_needs_judge(self):
        out = score_question(self.row, res("anything"), NONE)
        self.assertTrue(out["needs_judge"])
        self.assertEqual(repoprobe.checklist_items(self.row)[1], ("retries with backoff", 1.0))


class TestJudge(unittest.TestCase):
    def test_parse_verdict(self):
        self.assertEqual(parse_verdict('ok {"correct": true, "reason": "x"}'),
                         {"correct": True, "reason": "x"})
        self.assertEqual(parse_verdict('{"correct": "false"}')["correct"], False)
        self.assertIsNone(parse_verdict("yes it is"))
        self.assertIsNone(parse_verdict(""))

    def test_cache_with_mocked_call(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cache.jsonl")
            calls = []

            def fake(prompt):
                calls.append(prompt)
                return '{"correct": true, "reason": "same"}', {"in": 5, "out": 2}

            j = Judge("anthropic", api_key="test", cache_path=path)
            j._call = fake
            v = j.verdict("frames", question="q", gold="g", answer="a")
            self.assertEqual((v["correct"], v["source"]), (True, "anthropic"))
            v2 = j.verdict("frames", question="q", gold="g", answer="a")
            self.assertEqual(v2["source"], "cache")
            self.assertEqual(len(calls), 1)
            self.assertIn("q", calls[0])
            j2 = Judge("anthropic", api_key="test", cache_path=path)
            j2._call = fake
            self.assertEqual(j2.verdict("frames", question="q", gold="g", answer="a")["source"],
                             "cache")
            self.assertEqual(len(calls), 1)
            with open(path) as f:
                self.assertEqual(len([json.loads(x) for x in f]), 1)

    def test_backend_errors(self):
        from me4bench.scoring import JudgeError
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with self.assertRaises(JudgeError):
                Judge("anthropic", cache_path=None)
        finally:
            if old is not None:
                os.environ["ANTHROPIC_API_KEY"] = old
        with self.assertRaises(ValueError):
            Judge("bogus")


class TestAggregate(unittest.TestCase):
    def test_aggregate(self):
        recs = []
        for i, (st, ok, t) in enumerate([("A", True, 1.0), ("A", True, 2.0),
                                         ("B", False, 3.0), ("B", True, 4.0)]):
            r = row("frames", stratum=st, id="frames-%04d" % i)
            rs = res("Ada Lovelace" if ok else "Babbage", t_first_answer_s=t,
                     timing_source="first_audio", files_opened=["a", "b"][:i % 2 + 1])
            recs.append(merge(r, rs, score_question(r, rs, H)))
        fr = row("frames", stratum="B", id="frames-0009")
        fres = res("", error="timeout", failed=True)
        recs.append(merge(fr, fres, score_question(fr, fres, H)))
        out = aggregate(recs, {"stratum_sizes": {"frames": {"A": 30, "B": 10}}})
        f = out["datasets"]["frames"]
        self.assertEqual(f["n"], 5)
        self.assertEqual(f["failed"], 1)
        self.assertAlmostEqual(f["accuracy"], 3 / 5)
        self.assertTrue(f["weighted"])
        # A mean 1.0 (w30), B mean 1/3 (w10)
        self.assertAlmostEqual(f["weighted_score"], (30 * 1.0 + 10 / 3) / 40)
        self.assertEqual(f["p50_s"], 2.0)
        self.assertEqual(f["p95_s"], 4.0)
        self.assertEqual(f["n_timed"], 4)
        self.assertEqual(f["citations"], {"not reported": 4})
        self.assertAlmostEqual(out["overall"]["score"], f["weighted_score"])
        unw = aggregate(recs)["datasets"]["frames"]
        self.assertFalse(unw["weighted"])
        self.assertAlmostEqual(unw["weighted_score"], (1.0 + 1 / 3) / 2)

    def test_common_helpers(self):
        self.assertTrue(common.says_not_found("That is not in the files."))
        self.assertFalse(common.says_not_found("Ada Lovelace [a.txt, 1]"))
        self.assertEqual(common.percentile_nearest([], 50), None)
        self.assertEqual(common.anls("abc", "xyz"), 0.0)


if __name__ == "__main__":
    unittest.main()
