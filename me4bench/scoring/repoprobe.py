"""RepoProbe scoring: a checklist score.

``row["meta"]["checklist"]`` is a list of ``{"item": str, "weight": num}`` or
plain strings (weight 1). The judge is asked about each item separately
(prompt ``repoprobe_item``). The score is the weighted share of items the
answer covers, from 0 to 1 (``detail["score10"]`` is out of 10). A *perfect
solve* means every item is covered (``detail["perfect"]``). ``correct`` is
``score >= 0.5``. If an item cannot be judged (judge "none"), the question is
marked ``needs_judge``. Without a checklist, the binary ``qa`` judge is used
against ``gold``.
"""
from __future__ import annotations

from . import common
from .frames import judged_binary


def checklist_items(row: dict):
    out = []
    for it in (row.get("meta") or {}).get("checklist") or []:
        if isinstance(it, dict):
            text = str(it.get("item") or it.get("text") or "").strip()
            try:
                w = float(it.get("weight", 1) or 0)
            except (TypeError, ValueError):
                w = 1.0
        else:
            text, w = str(it).strip(), 1.0
        if text and w > 0:
            out.append((text, w))
    return out


def score(row: dict, result: dict, judge, ctx=None) -> dict:
    ans = result.get("answer") or ""
    items = checklist_items(row)
    if not items:
        return judged_binary(row, result, judge, "qa")
    if not row.get("answerable", True):
        nf = common.says_not_found(ans)
        return common.result_dict(1.0 if nf else 0.0, nf, "not_found", {"said_not_found": nf})
    total = sum(w for _, w in items)
    got, verdicts, missing = 0.0, [], False
    for text, w in items:
        v = judge.verdict("repoprobe_item", question=row.get("question", ""), item=text,
                          answer=ans) if judge is not None else None
        if v is None:
            missing = True
            verdicts.append({"item": text, "weight": w, "correct": None})
            continue
        verdicts.append({"item": text, "weight": w, "correct": bool(v["correct"]),
                         "reason": v.get("reason", "")})
        if v["correct"]:
            got += w
    if missing:
        return common.result_dict(0.0, False, "needs_judge", {"items": verdicts},
                                  needs_judge=True)
    s = got / total if total else 0.0
    perfect = all(v["correct"] for v in verdicts)
    return common.result_dict(s, s >= 0.5, "checklist",
                              {"items": verdicts, "score10": round(10 * s, 2),
                               "perfect": perfect})
