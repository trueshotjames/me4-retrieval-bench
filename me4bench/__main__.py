"""python -m me4bench <fetch|build-manifest|menu|run|score|report|check|index|resume>"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path

from . import runner, sampler
from .slots import DATASETS, TITLES

ALL = list(DATASETS)


def _rows(root, exclude=(), datasets=None):
    from .manifest import load_all
    return load_all(Path(root), exclude=exclude, datasets=datasets)


def estimate(root, mode, n_rounds, datasets, exclude, sec_per_q):
    rows = _rows(root, exclude, datasets)
    rounds, seed, rep = sampler.plan_rounds(rows, mode, n_rounds, seed=1, datasets=datasets, exclude=exclude)
    n = sum(len(r) for r in rounds)
    return n, sampler.estimate_duration(n, sec_per_q), len(rep)


def menu(a):
    """Plain-text menu: mode, rounds with duration estimates, dataset, exclude code."""
    root = a.root
    ask = lambda p, d="": (input(f"{p} [{d}]: ").strip() or d)
    print("ME4 benchmark\n  1) test run (fixed 373/459-question test set, counted)\n  2) training run (practice, gold shown)")
    mode = "test" if ask("Choose", "1") == "1" else "train"
    ds = ask("Dataset (all, " + ", ".join(ALL) + ")", "all")
    while ds != "all" and ds not in ALL:
        ds = ask("Unknown. Dataset", "all")
    datasets = None if ds == "all" else [ds]
    exclude = ()
    if datasets is None and ask("Exclude RepoProbe (code)? y/n", "n").lower().startswith("y"):
        exclude = ("repoprobe",)
    spq = a.sec_per_q
    print("\nRounds (40 questions each):")
    for name in ("round", "hour", "full"):
        r = sampler.preset_rounds(name, spq)
        n, secs, rep = estimate(root, mode, r, datasets, exclude, spq)
        print(f"  {name:6} {r} round(s), {n} questions, about {sampler.format_duration(secs)}" + (f", {rep} repeats" if rep else ""))
    c = ask("Rounds: round / hour / full / a number", "round")
    n_rounds = int(c) if c.isdigit() else sampler.preset_rounds(c, spq)
    n, secs, _ = estimate(root, mode, n_rounds, datasets, exclude, spq)
    print(f"\n{n_rounds} round(s), {n} questions, about {sampler.format_duration(secs)}.")
    if ask("Start? y/n", "y").lower().startswith("y"):
        _go(a, mode, n_rounds, datasets, exclude)


def _go(a, mode, n_rounds, datasets, exclude):
    stop = {"v": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(v=True) or print("\nfinishing this question, then stopping ..."))

    def show(k, tot, rec, allrec):
        okc = sum(1 for r in allrec if r.get("correct"))
        print(f"\r{k}/{tot}  {rec['ds']:9} score {rec.get('score', 0):.2f}  t={rec.get('t_first_answer_s')}  running acc {okc}/{len(allrec)}   ", end="", flush=True)

    out = runner.run(root=a.root, adapter=runner.load_adapter(a.adapter), student=a.student, mode=mode,
                     n_rounds=n_rounds, seed=a.seed, datasets=datasets, exclude=exclude, scope=a.scope,
                     judge=a.judge, judge_model=a.judge_model, runs_dir=a.runs, index_dir=a.index,
                     audio_dir=a.audio, on_result=show, stop_flag=lambda: stop["v"])
    print(f"\nDone: {out}/results.html")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="me4bench")
    ap.add_argument("cmd", choices=["fetch", "build-manifest", "menu", "run", "score", "report", "check", "index", "resume"])
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--root", default="data")
    ap.add_argument("--runs", default="runs")
    ap.add_argument("--index", default="index")
    ap.add_argument("--audio", default="audio")
    ap.add_argument("--adapter")
    ap.add_argument("--project", help="path of the ME4 project (sets ME4_PROJECT for the adapter)")
    ap.add_argument("--student", default=os.environ.get("USER", "me"))
    ap.add_argument("--mode", choices=["test", "train"], default="test")
    ap.add_argument("--rounds", default="round", help="number, or round|hour|full")
    ap.add_argument("--dataset", choices=ALL, help="benchmark only this dataset (all are still downloaded)")
    ap.add_argument("--exclude-repoprobe", action="store_true", help="skip code questions; their slots go to the other four")
    ap.add_argument("--scope", choices=["scoped", "given", "mixed"], default="scoped")
    ap.add_argument("--judge", choices=["none", "heuristic", "anthropic", "openai_compat"], default="heuristic")
    ap.add_argument("--judge-model")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--sec-per-q", type=float, default=sampler.DEFAULT_SEC_PER_Q)
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args(argv)
    if a.project:
        os.environ["ME4_PROJECT"] = a.project
    exclude = ("repoprobe",) if a.exclude_repoprobe else ()
    datasets = [a.dataset] if a.dataset else None
    if a.dataset == "repoprobe" and exclude:
        print("Cannot both exclude RepoProbe and select it.")
        return 2

    if a.cmd == "fetch":
        from .fetch import main as fm
        return fm((["--dry-run"] if a.dry_run else []) + (["--offline"] if a.offline else []) + ["--root", a.root])
    if a.cmd == "build-manifest":
        from .manifest import build
        return build(Path(a.root), only=a.only)
    if a.cmd == "menu":
        menu(a)
        return 0
    if a.cmd in ("run",):
        n = int(a.rounds) if a.rounds.isdigit() else sampler.preset_rounds(a.rounds, a.sec_per_q)
        if a.dry_run:
            cnt, secs, rep = estimate(a.root, a.mode, n, datasets, exclude, a.sec_per_q)
            print(f"{n} round(s), {cnt} questions, about {sampler.format_duration(secs)}, {rep} repeats")
            return 0
        _go(a, a.mode, n, datasets, exclude)
        return 0
    if a.cmd == "resume":
        out = runner.run(root=a.root, adapter=runner.load_adapter(a.adapter), resume=a.rest[0],
                         runs_dir=a.runs, index_dir=a.index, audio_dir=a.audio)
        print(out)
        return 0
    if a.cmd == "report":
        from . import report
        print(report.write(Path(a.rest[0])))
        return 0
    if a.cmd == "score":
        from . import scoring
        d = Path(a.rest[0])
        data = json.loads((d / "results.json").read_text())
        j = scoring.Judge(a.judge, model=a.judge_model, cache_path=str(Path(a.runs) / ".judge_cache.jsonl"))
        rows = _rows(a.root)
        byid = {r["id"]: r for r in rows}
        new = []
        for rec in data["rows"]:
            q = byid[rec["id"]]
            sc = scoring.score_question(q, rec, j, {"stratum_sizes": runner.stratum_sizes(rows)})
            new.append({**rec, **sc})
        data["rows"] = new
        data["summary"] = scoring.aggregate(new, {"stratum_sizes": runner.stratum_sizes(rows)})
        (d / "results.json").write_text(json.dumps(data, indent=1, default=str))
        from . import report
        print(report.write(d))
        return 0
    if a.cmd == "index":
        if runner.is_pi():
            print("Refusing: indexing (embedding, OCR, vision) never runs on a Raspberry Pi / ARM board.\n"
                  "Run tools/remote_index.sh on a cloud VM, GPU cluster (SSH) or a free Kaggle notebook, then copy ./index here.")
            return 2
        ad = runner.load_adapter(a.adapter)
        for ds in a.rest or ALL:
            print(f"indexing {ds} ...")
            ad.ingest(str(runner.corpus_view(Path(a.root), ds)), ds)
        return 0
    if a.cmd == "check":
        from .fetch import MODS
        ok = True
        for n, m in MODS.items():
            r = m.ready(Path(a.root))
            ok &= r
            print(f"{'ok ' if r else 'MISSING'} {n}")
        print("manifest:", "ok" if (Path(a.root) / "_manifest").exists() else "missing (build-manifest)")
        print("adapter:", "ok" if Path(a.adapter or "adapters/agent_entry.py").exists() else "missing")
        print("platform:", "Raspberry Pi/ARM: query only" if runner.is_pi() else "can index")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
