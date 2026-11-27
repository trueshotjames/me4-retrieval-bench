"""FRAMES scoring: fixed LLM-judge prompt, binary correct.

A normalised exact match is accepted without calling the judge. An answer that
says "not found" is wrong for an answerable question; for an unanswerable
question, saying "not found" is the only correct reply.
"""
from __future__ import annotations

from . import common


def judged_binary(row: dict, result: dict, judge, kind: str) -> dict:
    """Shared binary judge flow used by FRAMES, MiMoTable and MultiHop."""
    ans = result.get("answer") or ""
    gold = row.get("gold") or ""
    nf = common.says_not_found(ans)
    if not row.get("answerable", True):
        return common.result_dict(1.0 if nf else 0.0, nf, "not_found",
                                  {"said_not_found": nf})
    if common.exact_match(ans, gold):
        return common.result_dict(1.0, True, "exact", {"said_not_found": nf})
    if nf and not common.contains(ans, gold):
        return common.result_dict(0.0, False, "said_not_found", {"said_not_found": True})
    v = judge.verdict(kind, question=row.get("question", ""), gold=gold, answer=ans) \
        if judge is not None else None
    if v is None:
        return common.result_dict(0.0, False, "needs_judge", {"said_not_found": nf},
                                  needs_judge=True)
    ok = bool(v["correct"])
    return common.result_dict(1.0 if ok else 0.0, ok, "judge",
                              {"said_not_found": nf, "judge": v})


def score(row: dict, result: dict, judge, ctx=None) -> dict:
    return judged_binary(row, result, judge, "frames")
