"""MiMoTable (English) scoring: fixed LLM-judge prompt, binary correct.

A normalised exact match is accepted without calling the judge, and so is a
single numeric answer equal to a numeric gold answer within 1%.
Unanswerable questions are correct only when the reply says "not found".
"""
from __future__ import annotations

from . import common
from .frames import judged_binary


def score(row: dict, result: dict, judge, ctx=None) -> dict:
    ans = common.strip_citations(result.get("answer") or "")
    gold = row.get("gold") or ""
    if row.get("answerable", True):
        g = common.parse_number(gold)
        nums = common.numbers_in(ans)
        if g is not None and len(nums) == 1 and common.num_close(nums[0], g,
                                                                 include_percentage=False):
            return common.result_dict(1.0, True, "numeric", {"value": nums[0]})
    return judged_binary(row, result, judge, "mimotable")
