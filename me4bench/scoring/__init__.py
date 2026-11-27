"""Scoring: per-question scores and run aggregates.

``score_question(row, result, judge, ctx=None)`` returns::

    {"score": 0..1, "correct": bool, "metric": str, "detail": dict,
     "cited_ok": True | False | None, "needs_judge": bool}

``cited_ok`` None means "not reported". ``needs_judge`` True means the judge
backend was "none". Re-run scoring with a judge later. Such rows are left out
of the accuracy means and counted on their own.

``aggregate(scored_rows, ctx=None)`` takes flat dicts that merge the question
row, the ``voice.ask_one`` result and the score (see :func:`merge`).
``ctx["stratum_sizes"] = {ds: {stratum: n}}`` (sizes in the full question
file) gives the weighted dataset score. Without it the weighted score is the
plain mean of the stratum means (``"weighted": False``).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional

from . import common, frames, mimotable, mmlb, multihop, repoprobe
from .judge import BACKENDS, JUDGE_PROMPTS, Judge, JudgeError, parse_verdict

SCORERS = {"frames": frames.score, "multihop": multihop.score, "mmlb": mmlb.score,
           "repoprobe": repoprobe.score, "mimotable": mimotable.score}

__all__ = ["score_question", "aggregate", "merge", "Judge", "JudgeError",
           "JUDGE_PROMPTS", "BACKENDS", "SCORERS", "parse_verdict"]


def _cited_ok_generic(row: dict, result: dict) -> Optional[bool]:
    if not row.get("answerable", True):
        return None
    if row.get("ds") == "repoprobe":
        gold_files = (row.get("meta") or {}).get("gold_files")
        return common.cited_file_ok(result.get("citations"), gold_files) if gold_files else None
    return common.cited_file_ok(result.get("citations"), row.get("gold_docs"))


def score_question(row: dict, result: dict, judge, ctx: Optional[dict] = None) -> dict:
    """Score one answered question. Never raises on bad answer data."""
    result = result or {}
    if result.get("failed") or result.get("error"):
        return common.result_dict(0.0, False, "failed", {"error": result.get("error")},
                                  None)
    if (ctx or {}).get("scope") == "decoy":   # layer-2 refusal check: only "not found" is right
        ok = common.says_not_found(result.get("answer") or "")
        return common.result_dict(1.0 if ok else 0.0, ok, "refusal_check", {"decoy": True}, None)
    fn = SCORERS.get(row.get("ds"))
    if fn is None:
        raise ValueError("no scorer for dataset %r" % row.get("ds"))
    out = fn(row, result, judge, ctx)
    if out.get("cited_ok") is None and row.get("ds") != "mmlb":
        out["cited_ok"] = _cited_ok_generic(row, result)
    out.setdefault("needs_judge", False)
    return out


def merge(row: dict, result: dict, scored: dict) -> dict:
    """Flat record for results.jsonl / aggregate (score fields win)."""
    rec = dict(row)
    rec.update({k: v for k, v in (result or {}).items() if k != "events"})
    rec.update(scored or {})
    return rec


def _mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def _block(rows: List[dict], sizes: Optional[Dict[str, int]] = None) -> dict:
    from ..sampler import margin_95
    scored = [r for r in rows if not r.get("needs_judge")]
    failed = [r for r in rows if r.get("failed") or r.get("error")]
    ok_rows = [r for r in rows if not (r.get("failed") or r.get("error"))]
    lat = [r.get("t_first_answer_s") for r in ok_rows if r.get("t_first_answer_s") is not None]

    strata: Dict[str, List[float]] = defaultdict(list)
    for r in scored:
        strata[r.get("stratum")].append(float(r.get("score", 0.0)))
    per_stratum = {s: {"n": len(v), "mean": _mean(v)} for s, v in strata.items()}
    weighted, wflag = None, False
    if per_stratum:
        if sizes and all(s in sizes for s in per_stratum):
            tot = sum(sizes[s] for s in per_stratum)
            if tot > 0:
                weighted = sum(sizes[s] * per_stratum[s]["mean"] for s in per_stratum) / tot
                wflag = True
        if weighted is None:
            weighted = _mean([v["mean"] for v in per_stratum.values()])

    unans = [r for r in scored if not r.get("answerable", True)]
    judged_ok = [r for r in scored if not (r.get("failed") or r.get("error"))]
    conf_wrong = [r for r in judged_ok if not r.get("correct")
                  and not common.says_not_found(r.get("answer") or "")]
    false_nf = [r for r in judged_ok if r.get("answerable", True)
                and common.says_not_found(r.get("answer") or "")]
    buckets: Dict[str, List[float]] = defaultdict(list)
    for r in scored:
        buckets[str(r.get("bucket"))].append(float(r.get("score", 0.0)))
    files = [len(r["files_opened"]) for r in ok_rows if isinstance(r.get("files_opened"), list)]
    cited = Counter("ok" if r.get("cited_ok") is True else "wrong" if r.get("cited_ok") is False
                    else "not reported" for r in ok_rows if r.get("answerable", True))
    n_sc = len(scored)
    return {
        "n": len(rows), "n_scored": n_sc, "needs_judge": len(rows) - n_sc,
        "failed": len(failed),
        "accuracy": _mean([float(r.get("score", 0.0)) for r in scored]),
        "correct_rate": _mean([1.0 if r.get("correct") else 0.0 for r in scored]),
        "margin_95": margin_95(n_sc) if n_sc else None,
        "weighted_score": weighted, "weighted": wflag,
        "strata": per_stratum,
        "p50_s": common.percentile_nearest(lat, 50),
        "p95_s": common.percentile_nearest(lat, 95),
        "n_timed": len(lat),
        "timing_sources": dict(Counter(r.get("timing_source") for r in ok_rows)),
        "not_found_correct_rate": _mean([1.0 if r.get("correct") else 0.0 for r in unans]),
        "n_unanswerable": len(unans),
        "confident_wrong_rate": (len(conf_wrong) / len(judged_ok)) if judged_ok else None,
        "false_not_found_rate": (len(false_nf) / len(judged_ok)) if judged_ok else None,
        "clarification_rate": _mean([1.0 if (r.get("n_clarify") or 0) > 0 else 0.0
                                     for r in rows]),
        "bucket_accuracy": {b: {"n": len(v), "mean": _mean(v)} for b, v in buckets.items()},
        "files_opened_median": common.median(files),
        "citations": dict(cited),
    }


def aggregate(scored_rows: Iterable[dict], ctx: Optional[dict] = None) -> dict:
    """Run summary: ``{"overall": {...}, "datasets": {ds: {...}}}``.

    ``overall["score"]`` is the mean of the datasets' weighted scores.
    """
    rows = list(scored_rows)
    sizes_all = (ctx or {}).get("stratum_sizes") or {}
    by_ds: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_ds[r.get("ds")].append(r)
    datasets = {}
    for ds, rs in by_ds.items():
        b = _block(rs, sizes_all.get(ds))
        if ds == "repoprobe":
            perf = [1.0 if (r.get("detail") or {}).get("perfect") else 0.0
                    for r in rs if not r.get("needs_judge")]
            b["perfect_solve_rate"] = _mean(perf)
        if ds == "multihop":
            len_ = [1.0 if (r.get("detail") or {}).get("lenient") else 0.0
                    for r in rs if "lenient" in (r.get("detail") or {})]
            b["lenient_accuracy"] = _mean(len_)
        datasets[ds] = b
    overall = _block(rows)
    ws = [d["weighted_score"] for d in datasets.values() if d["weighted_score"] is not None]
    overall["score"] = _mean(ws)
    overall.pop("weighted_score", None)
    overall.pop("weighted", None)
    return {"overall": overall, "datasets": datasets}
