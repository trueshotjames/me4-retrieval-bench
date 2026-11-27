import json, os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from me4bench import layers, runner  # noqa: E402
from me4bench.__main__ import main  # noqa: E402
from test_smoke import ROOT, make_manifest  # noqa: E402


def row(ds="frames", docs=("a.md", "b.md"), **kw):
    r = {"id": f"{ds}-0001", "ds": ds, "answerable": True, "gold_docs": list(docs), "meta": {}}
    r.update(kw)
    return r


class TestRetrieval(unittest.TestCase):
    def test_file_level(self):
        m = layers.retrieval(row(), {"retrieved": ["x.md", "articles/b.docx", "a.md", "a.md"]})
        self.assertEqual((m["source"], m["level"], m["n_gold"]), ("retrieved", "file", 2))
        self.assertEqual(m["recall@5"], 1.0)
        self.assertAlmostEqual(m["mrr"], 0.5)
        import math
        self.assertAlmostEqual(m["ndcg@10"], (1 / math.log2(3) + 1 / math.log2(4)) / (1 + 1 / math.log2(3)))
        self.assertTrue(m["pass"])

    def test_page_level_and_fallback(self):
        r = row("mmlb", ["documents/rep.pdf"], meta={"evidence_pages": [3, 5]})
        m = layers.retrieval(r, {"retrieved": [{"file": "rep.pdf", "page": 3}, "rep.pdf#p4"]})
        self.assertEqual((m["level"], m["recall@10"], m["pass"]), ("page", 0.5, False))
        m = layers.retrieval(r, {"citations": [{"file": "rep.pdf"}]})     # no pages at all: file level
        self.assertEqual((m["source"], m["level"], m["recall@10"]), ("citations", "file", 1.0))
        self.assertEqual(layers.retrieval(r, {}), {"source": None})
        self.assertIsNone(layers.retrieval(row(answerable=False), {"retrieved": ["a.md"]}))
        self.assertIsNone(layers.retrieval(row("repoprobe", ["repo"]), {"retrieved": ["x"]}))

    def test_outcome_truth_evidence(self):
        self.assertEqual(layers.outcome({"correct": True}), "correct")
        self.assertEqual(layers.outcome({"answer": "It is not in the files."}), "missing")
        self.assertEqual(layers.outcome({"answer": "x", "score": 0.6}), "acceptable")
        self.assertEqual(layers.outcome({"answer": "x", "score": 0}), "wrong")
        self.assertIsNone(layers.outcome({"needs_judge": True}))
        self.assertEqual(layers.evidence(row(), {"citations": [{"file": "a.md"}]}), 0.5)
        self.assertIsNone(layers.evidence(row(), {"citations": None}))

    def test_decoys_disjoint(self):
        rows = [row(docs=[f"d{i}.md"], id=f"frames-{i:04d}") for i in range(5)] + [row(docs=["d0.md"], id="frames-0099")]
        d = layers.decoy_docs(rows)
        self.assertEqual(set(d), {r["id"] for r in rows})
        for r in rows:
            self.assertFalse(set(d[r["id"]]) & set(r["gold_docs"]))
        self.assertEqual(d, layers.decoy_docs(list(reversed(rows))))

    def test_tags(self):
        e2e = [{"id": "q1", "correct": True, "layers": {"l1": {"source": "retrieved", "pass": True}}},
               {"id": "q2", "correct": False, "layers": {"l1": {"source": "retrieved", "pass": False}}},
               {"id": "q3", "correct": False, "layers": {"l1": {"source": "retrieved", "pass": True}}},
               {"id": "q4", "correct": False, "layers": {"l1": {"source": "retrieved", "pass": True}}},
               {"id": "q5", "correct": False, "layers": {"l1": {"source": None}}}]
        orc = [{"id": "q3", "correct": False}, {"id": "q4", "correct": True}]
        t = {x["id"]: x["tag"] for x in layers.tag_runs(e2e, orc)}
        self.assertEqual(t, {"q1": "pass", "q2": "retrieval miss", "q3": "generation miss",
                             "q4": "integration miss", "q5": "unknown"})


class TestRuns(unittest.TestCase):
    def test_scoped_given_decoy_and_tags(self):
        d = Path(tempfile.mkdtemp())
        make_manifest(d / "data")
        os.environ["ME4_FAKE_MODE"] = "gold"
        os.environ["ME4_DATA"] = str(d / "data")
        ad = runner.load_adapter(str(ROOT / "adapters" / "fake_agent.py"))
        outs = {}
        for sc in ("scoped", "given", "decoy"):
            outs[sc] = runner.run(root=str(d / "data"), adapter=ad, mode="test", n_rounds=1, scope=sc, judge="heuristic",
                                  runs_dir=str(d / "runs" / sc), index_dir=str(d / "idx"), audio_dir=None, log=lambda *_: None)
        s = json.loads((outs["scoped"] / "results.json").read_text())["summary"]
        l1 = s["layers"]["overall"]["l1"]
        self.assertGreater(l1["n"], 0)
        self.assertAlmostEqual(l1["recall@10"], 1.0)
        self.assertAlmostEqual(l1["mrr"], 0.5)          # the fake agent ranks an unrelated file first
        self.assertGreater(s["layers"]["overall"]["l3"]["truthfulness"], 0.9)
        self.assertIn("Debug layers", (outs["scoped"] / "results.html").read_text())
        g = json.loads((outs["given"] / "results.json").read_text())["summary"]["layers"]["overall"]
        self.assertGreater(g["l2"]["oracle_accuracy"], 0.9)
        dc = json.loads((outs["decoy"] / "results.json").read_text())["summary"]["layers"]["overall"]
        self.assertLess(dc["l2"]["refusal_rate"], 0.5)   # the fake agent answers anyway
        self.assertEqual(main(["layers", str(outs["scoped"]), "--oracle", str(outs["given"])]), 0)
        lj = json.loads((outs["scoped"] / "layers.json").read_text())
        self.assertEqual(sum(lj["summary"]["all"].values()), len(lj["questions"]))
        self.assertTrue((outs["scoped"] / "layers.html").exists())


if __name__ == "__main__":
    unittest.main()
