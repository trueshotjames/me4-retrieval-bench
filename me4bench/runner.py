"""Run loop: plan rounds, ask each question through the adapter, score, write the run folder.
No speaker, mic or audio recording here: clips are only handed to the adapter as file paths."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import platform
import sys
import threading
import time
from pathlib import Path

from . import sampler, scoring, voice
from .manifest import load_all

REPO = Path(__file__).resolve().parent.parent


def load_adapter(path: str | None = None):
    p = Path(path or os.environ.get("ME4_ADAPTER") or REPO / "adapters" / "agent_entry.py")
    if not p.exists():
        raise SystemExit(f"No adapter at {p}. Copy adapters/agent_entry_template.py to adapters/agent_entry.py "
                         f"(or pass --adapter adapters/fake_agent.py for a dry run).")
    spec = importlib.util.spec_from_file_location("me4_adapter", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["me4_adapter"] = m
    spec.loader.exec_module(m)
    for fn in ("describe", "attach", "reset", "ask", "shutdown"):
        if not hasattr(m, fn):
            raise SystemExit(f"Adapter {p} lacks {fn}(). See adapters/agent_entry_template.py.")
    return m


def is_pi() -> bool:
    if os.environ.get("ME4_ALLOW_INDEX") == "1":
        return False
    try:
        return "raspberry" in Path("/proc/device-tree/model").read_text(errors="ignore").lower()
    except OSError:
        return False


class MachineStats(threading.Thread):
    """Samples CPU load, RAM, temperature (and Pi throttle flags) every few seconds."""

    def __init__(self, every=5.0):
        super().__init__(daemon=True)
        self.every, self.samples, self._stop_ev = every, [], threading.Event()

    @staticmethod
    def snap() -> dict:
        s = {"t": time.time()}
        try:
            s["load1"] = os.getloadavg()[0]
        except (OSError, AttributeError):
            pass
        try:
            mem = {l.split(":")[0]: int(l.split()[1]) for l in Path("/proc/meminfo").read_text().splitlines()}
            s["ram_used_mb"] = (mem["MemTotal"] - mem["MemAvailable"]) // 1024
        except (OSError, KeyError, ValueError):
            pass
        try:
            s["temp_c"] = int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
        except (OSError, ValueError):
            pass
        return s

    def run(self):
        while not self._stop_ev.wait(self.every):
            self.samples.append(self.snap())

    def stop(self) -> dict:
        self._stop_ev.set()
        t = [x["temp_c"] for x in self.samples if "temp_c" in x]
        r = [x["ram_used_mb"] for x in self.samples if "ram_used_mb" in x]
        return {"samples": len(self.samples), "max_temp_c": max(t) if t else None,
                "max_ram_mb": max(r) if r else None}


KEYFILES = {"frames": ["test.tsv"], "multihop": ["MultiHopRAG.json"], "mmlb": ["data"], "repoprobe": ["_meta", "_done"],
            "mimotable": ["english/problems.json", "_done"]}


def corpus_view(root: Path, ds: str) -> Path:
    """data/_corpus/<ds>: the documents only (hardlinks, copy fallback), never the questions or answer keys."""
    import shutil
    src, dst = Path(root) / ds, Path(root) / "_corpus" / ds
    skip = {(src / k).resolve() for k in KEYFILES.get(ds, [])}
    if dst.exists():
        shutil.rmtree(dst)
    for p in src.rglob("*"):
        if p.is_dir() or any(p.resolve() == s or s in p.resolve().parents for s in skip) or p.name.startswith("_"):
            continue
        t = dst / p.relative_to(src)
        t.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(p, t)
        except OSError:
            shutil.copy2(p, t)
    return dst


def stratum_sizes(rows) -> dict:
    out = {}
    for r in rows:
        d = out.setdefault(r["ds"], {})
        d[r["stratum"]] = d.get(r["stratum"], 0) + 1
    return out


def scope_for(row, mode: str) -> dict:
    return {"mode": mode, "namespace": row["ds"] if mode != "mixed" else "all",
            "docs": row.get("gold_docs", []) if mode == "given" else []}


def plan(root, mode, n_rounds, seed, datasets, exclude):
    rows = load_all(Path(root), exclude=exclude, datasets=datasets)
    rounds, seed, repeats = sampler.plan_rounds(rows, mode, n_rounds, seed=seed, datasets=datasets, exclude=exclude)
    return rows, rounds, seed, repeats


def run(*, root="data", adapter=None, student="me", mode="test", n_rounds=1, seed=None, datasets=None,
        exclude=(), scope="scoped", judge="heuristic", judge_model=None, runs_dir="runs", index_dir="index",
        audio_dir="audio", resume=None, log=print, on_result=None, stop_flag=None) -> Path:
    if resume:
        c0 = json.loads((Path(resume) / "config.json").read_text())
        mode, n_rounds, seed = c0["mode"], c0["rounds"], c0["seed"]
        datasets, exclude, scope = c0.get("datasets"), tuple(c0.get("exclude") or ()), c0.get("scope", scope)
        judge, judge_model, student = c0.get("judge", judge), c0.get("judge_model"), c0.get("student", student)
    ad = adapter or load_adapter()
    st = json.loads((Path(root) / "_manifest" / "_status.json").read_text()) if (Path(root) / "_manifest" / "_status.json").exists() else {}
    if mode == "test" and st.get("ok") is False and os.environ.get("ME4_ALLOW_UNVERIFIED") != "1":
        raise SystemExit("Manifest did not match the expected strata (see `build-manifest` output). Test runs are refused "
                         "because the fixed split may point at the wrong questions. Fix the data, or set ME4_ALLOW_UNVERIFIED=1 for a practice run.")
    rows, rounds, seed, repeats = plan(root, mode, n_rounds, seed, datasets, exclude)
    flat = [q for rd in rounds for q in rd]
    if resume:
        out = Path(resume)
        cfg = json.loads((out / "config.json").read_text())
        flat_ids = cfg["ids"]
        byid = {q["id"] + f"@{q['round']}.{q['pos']}": q for q in flat}
        flat = [byid[k] for k in flat_ids if k in byid]
    else:
        out = Path(runs_dir) / student / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        out.mkdir(parents=True, exist_ok=True)
        run_no = sampler.bump_test_counter(runs_dir, student, seed=seed, rounds=n_rounds) if mode == "test" else None
        cfg = {"student": student, "mode": mode, "rounds": n_rounds, "seed": seed, "datasets": datasets,
               "exclude": list(exclude), "scope": scope, "judge": judge, "judge_model": judge_model,
               "test_run_number": run_no, "repeats": repeats, "started": dt.datetime.now().isoformat(),
               "machine": {"platform": platform.platform(), "python": platform.python_version()},
               "ids": [f"{q['id']}@{q['round']}.{q['pos']}" for q in flat]}
        (out / "config.json").write_text(json.dumps(cfg, indent=1))
    resp = out / "results.jsonl"
    done = set()
    if resp.exists():
        done = {r["key"] for r in _read_jsonl(resp)}
    os.environ["ME4_BENCH"] = "1"
    os.environ["ME4_BENCH_MARKS"] = str(out / "marks.jsonl")   # project's mark() calls land here
    os.environ["ME4_INDEX_DIR"] = str(index_dir)
    os.environ.setdefault("ME4_DATA", str(root))
    j = scoring.Judge(judge, model=judge_model, cache_path=str(Path(runs_dir) / ".judge_cache.jsonl"))
    ctx = {"stratum_sizes": stratum_sizes(rows)}
    clips = voice.ClipStore(audio_dir)
    ad.attach(index_dir, scope)
    stats = MachineStats()
    stats.start()
    evf = open(out / "events.jsonl", "a", encoding="utf-8")
    runlog = open(out / "run.log", "a", encoding="utf-8")
    (out / "traces").mkdir(exist_ok=True)
    t_start = time.time()
    scored_all = _read_jsonl(resp) if resp.exists() else []
    try:
        for k, q in enumerate(flat, 1):
            key = f"{q['id']}@{q['round']}.{q['pos']}"
            if key in done:
                continue
            if stop_flag and stop_flag():
                log("stopped; resume later with --resume " + str(out))
                break
            try:
                ad.reset()
            except Exception as e:  # noqa: BLE001
                runlog.write(f"reset failed {key}: {e}\n")
            res = voice.ask_one(ad, q, scope_for(q, scope), clips=clips)
            sc = scoring.score_question(q, res, j, ctx)
            rec = scoring.merge(q, res, sc)
            events = rec.pop("events", [])
            rec["key"], rec["round"], rec["pos"] = key, q["round"], q["pos"]
            if mode == "test":                      # no gold details in test mode output
                for k in ("gold", "gold_docs", "detail"):
                    rec.pop(k, None)
                rec["meta"] = {k: v for k, v in (rec.get("meta") or {}).items() if k in ("question_type", "table_type", "language", "taxonomy")}
            for t, ev in events:
                evf.write(json.dumps({"key": key, "t": round(t, 4), **ev}, ensure_ascii=False) + "\n")
            evf.flush()
            with open(resp, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            (out / "traces" / f"{key.replace('@', '_')}.txt").write_text(
                f"Q: {q['question']}\nA: {rec.get('answer')}\nscore={rec.get('score')} "
                f"t={rec.get('t_first_answer_s')} src={rec.get('timing_source')}\n", encoding="utf-8")
            scored_all.append(rec)
            runlog.write(f"{k}/{len(flat)} {key} score={rec.get('score')} t={rec.get('t_first_answer_s')}\n")
            runlog.flush()
            if on_result:
                on_result(k, len(flat), rec, scored_all)
    finally:
        try:
            ad.shutdown()
        except Exception:  # noqa: BLE001
            pass
        machine = stats.stop()
        evf.close()
        runlog.close()
    agg = scoring.aggregate(scored_all, ctx)
    agg["machine"] = machine
    agg["elapsed_s"] = round(time.time() - t_start, 1)
    agg["judge"] = j.config() if hasattr(j, "config") else judge
    (out / "results.json").write_text(json.dumps({"config": {k: v for k, v in cfg.items() if k != "ids"},
                                                  "summary": agg, "rows": scored_all}, indent=1, default=str))
    _csv(out / "results.csv", scored_all)
    from . import report
    report.write(out)
    return out


def _read_jsonl(p: Path) -> list:
    out = []
    for l in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(l))
        except ValueError:      # half-written last line after a crash
            pass
    return out


def _csv(path: Path, rows):
    import csv
    cols = ["key", "ds", "stratum", "bucket", "score", "correct", "t_first_answer_s", "timing_source",
            "cited_ok", "needs_judge", "failed", "error"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c) for c in cols])
