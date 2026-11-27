import json, os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from me4bench import runner, slots, sampler  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def make_manifest(root: Path, per=14):
    md = root / "_manifest"
    md.mkdir(parents=True)
    for ds, strata in slots.SLOTS.items():
        rows, i = [], 0
        for st in strata:
            for k in range(per * 2):
                rows.append({"id": f"{ds}-{i:04d}", "ds": ds, "idx": i, "question": f"Question {ds} {st} {k}?",
                             "spoken": f"Question {ds} {st} {k}?", "gold": f"answer {i}", "answerable": k % 7 != 0,
                             "gold_docs": [f"doc{i}.txt"], "stratum": st, "bucket": "1" if ds != "repoprobe" else "n/a",
                             "split": "test" if k < per else "train", "meta": {}})
                i += 1
        (md / f"{ds}.jsonl").write_text("\n".join(json.dumps(r) for r in rows))


class Smoke(unittest.TestCase):
    def run_it(self, mode, exclude=(), datasets=None, rounds=1):
        d = Path(tempfile.mkdtemp())
        make_manifest(d / "data")
        os.environ["ME4_FAKE_MODE"] = "gold"
        os.environ["ME4_DATA"] = str(d / "data")
        ad = runner.load_adapter(str(ROOT / "adapters" / "fake_agent.py"))
        out = runner.run(root=str(d / "data"), adapter=ad, mode=mode, n_rounds=rounds, exclude=exclude,
                         datasets=datasets, judge="heuristic", runs_dir=str(d / "runs"), index_dir=str(d / "idx"),
                         audio_dir=None, log=lambda *_: None)
        return json.loads((out / "results.json").read_text()), out

    def test_one_round_perfect_agent(self):
        data, out = self.run_it("train")
        self.assertEqual(len(data["rows"]), 40)
        self.assertGreaterEqual(data["summary"]["overall"]["score"], 0.95)
        self.assertTrue((out / "results.html").exists())

    def test_exclude_repoprobe(self):
        data, _ = self.run_it("test", exclude=("repoprobe",))
        ds = {r["ds"] for r in data["rows"]}
        self.assertNotIn("repoprobe", ds)
        self.assertEqual(len(data["rows"]), 40)

    def test_single_dataset_and_no_gold_in_test(self):
        data, _ = self.run_it("test", datasets=["frames"])
        self.assertEqual({r["ds"] for r in data["rows"]}, {"frames"})
        self.assertTrue(all("gold" not in r for r in data["rows"]))


if __name__ == "__main__":
    unittest.main()


class Resume(unittest.TestCase):
    def test_resume_completes_all(self):
        d = Path(tempfile.mkdtemp())
        make_manifest(d / "data")
        os.environ["ME4_DATA"] = str(d / "data")
        ad = runner.load_adapter(str(ROOT / "adapters" / "fake_agent.py"))
        kw = dict(root=str(d / "data"), adapter=ad, mode="train", n_rounds=2, seed=5, judge="heuristic",
                  runs_dir=str(d / "runs"), index_dir=str(d / "i"), audio_dir=None)
        n = {"k": 0}
        out = runner.run(stop_flag=lambda: (n.__setitem__("k", n["k"] + 1) or n["k"] > 10), **kw)
        rp = out / "results.jsonl"
        self.assertLess(len(rp.read_text().splitlines()), 80)
        rp.write_text(rp.read_text() + '{"half')           # crash mid-write
        out2 = runner.run(resume=str(out), adapter=ad, root=str(d / "data"), runs_dir=str(d / "runs"),
                          index_dir=str(d / "i"), audio_dir=None)
        self.assertEqual(len(json.loads((out2 / "results.json").read_text())["rows"]), 80)


class Clock(unittest.TestCase):
    def test_clarify_audio_does_not_stop_clock(self):
        from me4bench import voice
        class A:
            def reset(self): pass
            def ask(self, utt, scope):
                yield {"t": "clarify", "text": "which?"}
                yield {"t": "first_audio"}
                yield {"t": "answer_start"}
                yield {"t": "first_audio"}
                yield {"t": "answer", "text": "x"}
        row = {"id": "a", "ds": "frames", "question": "q?", "spoken": "q?"}
        r = voice.ask_one(A(), row, {})
        evs = [e["t"] for _, e in r["events"]]
        ts = [t for t, e in r["events"] if e["t"] == "first_audio"]
        self.assertEqual(r["t_first_answer_s"], ts[1])
