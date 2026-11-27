"""MultiHop-RAG scoring.

Two numbers per question:

* ``lenient``: the official ``has_intersection`` accuracy. Correct if the
  lowercased answer and gold share at least one whitespace token. This is
  very lenient on long spoken answers, so it is reported in ``detail`` only.
* the main score: a stricter judge accuracy. Yes/no gold answers are decided
  offline from the answer's leading yes/no. Null queries ("not in the
  files") are correct iff the answer says not found.
"""
from __future__ import annotations

import re

from . import common
from .frames import judged_binary


def has_intersection(a: str, b: str) -> bool:
    """Official MultiHop-RAG check: any shared whitespace-separated word."""
    return len(set(a.split()) & set(b.split())) > 0


def lenient_correct(row: dict, answer: str) -> bool:
    if not row.get("answerable", True):
        return common.says_not_found(answer)
    return has_intersection((answer or "").lower(), (row.get("gold") or "").lower())


def score(row: dict, result: dict, judge, ctx=None) -> dict:
    ans = result.get("answer") or ""
    lenient = lenient_correct(row, ans)
    gold = common.normalise(row.get("gold") or "")
    if row.get("answerable", True) and gold in ("yes", "no"):
        m = re.match(r"\W*(yes|no)\b", common.strip_citations(ans), re.I)
        if m:
            ok = m.group(1).lower() == gold
            out = common.result_dict(1.0 if ok else 0.0, ok, "yes_no", {})
            out["detail"]["lenient"] = lenient
            return out
    out = judged_binary(row, result, judge, "multihop")
    out["detail"]["lenient"] = lenient
    return out
