"""Tests for me4bench.voice.ask_one and helpers, with a tiny scripted adapter."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from me4bench import marks  # noqa: E402
from me4bench.speech import CLARIFY_REPLY  # noqa: E402
from me4bench.voice import ClipStore, FillerRule, ask_one  # noqa: E402


class FakeClock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


class Scripted:
    """Adapter whose ask() plays a script of steps.

    A step is ``("wait", seconds)`` (advance the fake clock), ``("sleep", s)``
    (real sleep), ``("raise", exc)`` or an event dict to yield. Values received
    via send() are recorded in ``self.received``.
    """

    def __init__(self, clock, script):
        self.clock, self.script = clock, script
        self.received, self.utts, self.closed = [], [], False

    def ask(self, utt, scope):
        self.utts.append((utt, scope))
        try:
            for step in self.script:
                if isinstance(step, tuple):
                    kind, val = step
                    if kind == "wait":
                        self.clock.t += val
                    elif kind == "sleep":
                        time.sleep(val)
                    elif kind == "raise":
                        raise val
                    continue
                got = yield step
                if isinstance(step, dict) and step.get("t") == "clarify":
                    self.received.append(got)
        finally:
            self.closed = True


ROW = {"id": "frames-0001", "ds": "frames", "question": "Who won in 1999?",
       "spoken": "", "gold": "x"}
SCOPE = {"mode": "scoped", "namespace": "frames", "docs": []}
ANSWER = {"t": "answer", "text": "Ada Lovelace [a.txt, 1]",
          "citations": [{"file": "a.txt", "page": 1}], "files_opened": ["a.txt"],
          "tokens": {"in": 10, "out": 5}}


def run(script, **kw):
    clk = FakeClock()
    ad = Scripted(clk, script)
    res = ask_one(ad, ROW, SCOPE, ClipStore(None), clock=clk, **kw)
    return res, ad


class TestAskOne(unittest.TestCase):
    def test_first_audio_timing_and_fields(self):
        res, ad = run([("wait", 2.0), {"t": "question_end"}, ("wait", 1.0),
                       {"t": "plan", "text": "search"}, ("wait", 0.5),
                       {"t": "tool", "name": "search", "phase": "start", "args": {}},
                       ("wait", 1.0),
                       {"t": "tool", "name": "search", "phase": "end", "args": {}},
                       {"t": "answer_start"}, ("wait", 0.25), {"t": "first_audio"},
                       ("wait", 2.0), ANSWER])
        self.assertIsNone(res["error"])
        self.assertFalse(res["failed"])
        self.assertEqual(res["id"], "frames-0001")
        self.assertEqual(res["timing_source"], "first_audio")
        self.assertAlmostEqual(res["t_first_answer_s"], 2.75)   # clip time excluded
        self.assertAlmostEqual(res["t_answer_start_s"], 2.5)
        self.assertAlmostEqual(res["t_total_s"], 4.75)
        self.assertEqual(res["n_plan"], 1)
        self.assertEqual(res["tool_events"], 2)
        self.assertEqual(res["answer"], "Ada Lovelace [a.txt, 1]")
        self.assertEqual(res["citations"], [{"file": "a.txt", "page": 1}])
        self.assertEqual(res["files_opened"], ["a.txt"])
        self.assertEqual(res["tokens"], {"in": 10, "out": 5})
        self.assertEqual(res["events"][0], (0.0, {"t": "question_end"}))
        json.dumps(res)   # serialisable
        # utterance: normalised question text when no spoken text
        self.assertEqual(ad.utts[0][0]["text"], "Who won in nineteen ninety-nine?")
        self.assertIsNone(ad.utts[0][0]["clip"])
        self.assertTrue(ad.closed)

    def test_no_question_end_uses_call_time(self):
        res, _ = run([("wait", 3.0), {"t": "first_audio"}, ANSWER])
        self.assertAlmostEqual(res["t_first_answer_s"], 3.0)

    def test_question_end_only_counts_as_first_event(self):
        res, _ = run([("wait", 1.0), {"t": "plan", "text": "p"}, ("wait", 1.0),
                      {"t": "question_end"}, ("wait", 1.0), {"t": "first_audio"}, ANSWER])
        self.assertAlmostEqual(res["t_first_answer_s"], 3.0)

    def test_clarify_once_then_none(self):
        res, ad = run([{"t": "question_end"}, ("wait", 1.0),
                       {"t": "clarify", "text": "Which year?"}, ("wait", 4.0),
                       {"t": "clarify", "text": "Really?"}, ("wait", 1.0),
                       {"t": "answer_start"}, ("wait", 1.0), ANSWER])
        self.assertIsNone(res["error"])
        self.assertEqual(res["n_clarify"], 2)
        self.assertEqual(len(ad.received), 2)
        self.assertEqual(ad.received[0]["text"], CLARIFY_REPLY)
        self.assertEqual(ad.received[0]["id"], "_clarify")
        self.assertIsNone(ad.received[1])
        # clarification never stops the clock
        self.assertEqual(res["timing_source"], "answer_start")
        self.assertAlmostEqual(res["t_first_answer_s"], 6.0)
        self.assertAlmostEqual(res["t_total_s"], 7.0)

    def test_answer_end_fallback(self):
        res, _ = run([("wait", 5.0), ANSWER])
        self.assertEqual(res["timing_source"], "answer_end")
        self.assertAlmostEqual(res["t_first_answer_s"], 5.0)
        self.assertIsNone(res["t_answer_start_s"])

    def test_filler_rule_fallback(self):
        res, _ = run([{"t": "question_end"}, ("wait", 1.0),
                      {"t": "speech", "text": "One moment, let me look."}, ("wait", 2.0),
                      {"t": "speech", "text": "The winner was Ada Lovelace."},
                      ("wait", 3.0), ANSWER])
        self.assertEqual(res["timing_source"], "filler_rule")
        self.assertEqual(res["timing_note"], "filler rule, unvalidated")
        self.assertAlmostEqual(res["t_first_answer_s"], 3.0)

    def test_answer_timeout_real_time(self):
        res, ad = run([{"t": "question_end"}, ("sleep", 1.0), ANSWER],
                      answer_timeout_s=0.2)
        self.assertEqual(res["error"], "timeout")
        self.assertTrue(res["failed"])
        self.assertEqual(res["answer"], "")

    def test_answer_timeout_by_clock(self):
        res, _ = run([{"t": "question_end"}, ("wait", 200.0), ANSWER],
                     answer_timeout_s=120)
        self.assertEqual(res["error"], "timeout")
        self.assertTrue(res["failed"])

    def test_clarify_timeout(self):
        res, _ = run([{"t": "clarify", "text": "?"}, ("sleep", 1.0), ANSWER],
                     clarify_timeout_s=0.2, answer_timeout_s=30)
        self.assertTrue(res["failed"])
        self.assertIn("clarification", res["error"])
        self.assertEqual(res["n_clarify"], 1)

    def test_exception_inside_generator(self):
        res, _ = run([{"t": "plan", "text": "x"}, ("raise", RuntimeError("boom"))])
        self.assertTrue(res["failed"])
        self.assertIn("RuntimeError: boom", res["error"])
        self.assertEqual(res["n_plan"], 1)

    def test_exception_when_calling_ask(self):
        class Bad:
            def ask(self, utt, scope):
                raise ValueError("no index")
        res = ask_one(Bad(), ROW, SCOPE)
        self.assertTrue(res["failed"])
        self.assertIn("ValueError", res["error"])

    def test_generator_ends_without_answer(self):
        res, _ = run([{"t": "plan", "text": "x"}])
        self.assertTrue(res["failed"])
        self.assertIn("no answer", res["error"])

    def test_plain_iterator_adapter(self):
        class ListAdapter:
            def ask(self, utt, scope):
                return [{"t": "first_audio"}, ANSWER]
        res = ask_one(ListAdapter(), ROW, SCOPE)
        self.assertIsNone(res["error"])
        self.assertEqual(res["timing_source"], "first_audio")

    def test_non_dict_event_recorded(self):
        res, _ = run(["hello", ANSWER])
        self.assertIsNone(res["error"])
        self.assertEqual(res["events"][0][1]["t"], "invalid")


class TestClipsAndFiller(unittest.TestCase):
    def test_clipstore(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "frames"))
            os.makedirs(os.path.join(d, "_fixed"))
            open(os.path.join(d, "frames", "frames-0001.wav"), "wb").close()
            open(os.path.join(d, "_fixed", "clarify.wav"), "wb").close()
            cs = ClipStore(d)
            u = cs.utterance(dict(ROW, spoken="Who won in nineteen ninety-nine?"))
            self.assertEqual(u["clip"], os.path.join(d, "frames", "frames-0001.wav"))
            self.assertEqual(u["text"], "Who won in nineteen ninety-nine?")
            self.assertTrue(cs.clarify_utterance()["clip"].endswith("clarify.wav"))
            self.assertIsNone(cs.utterance(dict(ROW, id="frames-0002"))["clip"])

    def test_filler_rule(self):
        fr = FillerRule()
        self.assertFalse(fr.is_substantive("One moment, let me look."))
        self.assertFalse(fr.is_substantive("Give me a second."))
        self.assertTrue(fr.is_substantive("It was 42 in total."))
        self.assertTrue(fr.is_substantive("The winner was Ada Lovelace."))
        self.assertFalse(fr.is_substantive("that is a good point to consider."))
        self.assertTrue(fr.is_substantive("the budget covers road repairs this year.",
                                          retrieved="The budget covers road repairs."))
        self.assertFalse(fr.is_substantive(""))


class TestMarks(unittest.TestCase):
    def test_noop_and_active(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "marks.jsonl")
            old = dict(os.environ)
            try:
                os.environ.pop("ME4_BENCH", None)
                os.environ["ME4_BENCH_MARKS"] = path
                self.assertFalse(marks.bench_active())
                marks.mark("answer_start")
                self.assertFalse(os.path.exists(path))
                os.environ["ME4_BENCH"] = "1"
                self.assertTrue(marks.bench_active())
                marks.mark("answer_start")
                marks.mark("retrieval_end", n=3, obj=object())
                with open(path) as f:
                    lines = [json.loads(x) for x in f]
                self.assertEqual([x["mark"] for x in lines], ["answer_start", "retrieval_end"])
                self.assertEqual(lines[1]["n"], 3)
                self.assertIsInstance(lines[0]["t"], int)
                os.environ["ME4_BENCH_MARKS"] = os.path.join(d, "missing", "x.jsonl")
                marks.mark("x")   # never raises
            finally:
                os.environ.clear()
                os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
