"""MMLongBench-Doc scoring: a reimplementation of the official rule scoring.

Official pipeline: an LLM extracts a short answer in the question's
``answer_format`` (Int, Float, Str, List, None), then ``eval_score`` applies:

* Int: integer equality
* Float: equal within 1% relative, also accepting a x100 or /100 scaling
  (percent vs fraction)
* Str: exact match for "exact-type" strings (dates, urls, file names, page
  references, numbers with units), otherwise ANLS with threshold 0.5
* List: same length, sorted; exact join for numeric/exact items, otherwise
  the minimum ANLS over the items
* None (unanswerable): correct iff the reply says not found

The extraction step is optional (``judge.extract``, remote judges only).
Without it, a documented heuristic is used: any number in the reply matches
(Int/Float), the gold is contained in the reply or ANLS (Str), every gold item
is contained (List).

``correct`` is ``score >= 0.5``. ``cited_ok`` is True if any cited page is in
``meta["evidence_pages"]``, and None if no pages were cited.
"""
from __future__ import annotations

import ast
import re

from . import common

_UNITS = ("mile", "miles", "million", "millions", "billion", "billions", "thousand",
          "thousands", "km", "kilometers", "kilometres", "meter", "meters", "metres",
          "percent", "percentage", "dollars", "dollar", "usd", "years", "year",
          "people", "users", "acres", "hours", "minutes", "points")


def get_clean_string(s) -> str:
    """Lowercase, strip, drop currency/percent signs, thousands separators,
    surrounding punctuation/quotes and a trailing unit word."""
    s = str(s if s is not None else "").lower().strip()
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)
    s = s.replace("$", "").replace("%", "").replace("£", "").replace("€", "")
    s = s.strip(" \t\n.,;:!?\"'`()[]{}")
    for u in _UNITS:
        if s.endswith(" " + u):
            s = s[: -len(u) - 1].strip()
            break
    return s


def is_exact_match(s: str) -> bool:
    """Strings that must match exactly (official heuristics, approximated)."""
    s = str(s)
    if "https://" in s or "http://" in s or "www." in s:
        return True
    if re.search(r"\.(py|ipynb|pdf|csv|xlsx|json|txt|md)\b", s):
        return True
    if s.startswith("page"):
        return True
    if re.fullmatch(r"\b\d+(-\d+|\s\d+)?\b", s):
        return True
    if "a.m." in s or "p.m." in s:
        return True
    if re.fullmatch(r"\b\d{4}[-\s]\d{2}[-\s]\d{2}\b", s):
        return True
    if re.fullmatch(r"\b\d{4}[-\s]\d{2}\b", s):
        return True
    if re.fullmatch(r"[\w.+-]+@[\w-]+\.[\w.-]+", s):
        return True
    return False


def _isfloat(s) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _as_list(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip().startswith("["):
        try:
            out = ast.literal_eval(v.strip())
            if isinstance(out, (list, tuple)):
                return list(out)
        except (ValueError, SyntaxError):
            pass
    return [v]


def eval_score(gt, pred, answer_type: str) -> float:
    """Official-style rule score of an *extracted* prediction (0..1)."""
    t = (answer_type or "Str").capitalize()
    if t == "Int":
        try:
            g = int(float(get_clean_string(gt)))
            p = int(float(get_clean_string(pred)))
        except (TypeError, ValueError):
            return 0.0
        return float(g == p)
    if t == "Float":
        try:
            g = float(get_clean_string(gt))
            p = float(get_clean_string(pred))
        except (TypeError, ValueError):
            return 0.0
        return float(common.num_close(p, g, rel=0.01, include_percentage=True))
    if t == "Str":
        g, p = get_clean_string(gt), get_clean_string(pred)
        if is_exact_match(g):
            return float(g == p)
        return common.anls(p, g, threshold=0.5)
    if t == "List":
        g = _as_list(gt)
        p = _as_list(pred)
        if len(g) != len(p):
            return 0.0
        g = sorted(get_clean_string(x) for x in g)
        p = sorted(get_clean_string(x) for x in p)
        if not g:
            return 1.0
        if _isfloat(g[0]) or is_exact_match(g[0]):
            return float("-".join(g) == "-".join(p))
        return min(common.anls(pv, gv, threshold=0.5) for gv, pv in zip(g, p))
    # None / unknown: compare as strings
    return float(get_clean_string(gt) == get_clean_string(pred))


def heuristic_score(gt, answer: str, answer_type: str) -> float:
    """Score a free-form reply without the LLM extraction step."""
    t = (answer_type or "Str").capitalize()
    ans = common.strip_citations(answer)
    if t in ("Int", "Float"):
        try:
            g = float(get_clean_string(gt))
        except (TypeError, ValueError):
            return float(common.contains(ans, gt))
        nums = common.numbers_in(ans)
        if t == "Int":
            return float(any(abs(n - g) < 1e-9 for n in nums))
        return float(any(common.num_close(n, g) for n in nums))
    if t == "List":
        items = [x for x in _as_list(gt) if str(x).strip()]
        if not items:
            return 0.0
        return float(all(_item_in(x, ans) for x in items))
    g = get_clean_string(gt)
    if common.contains(ans, g):
        return 1.0
    return common.anls(get_clean_string(ans), g, threshold=0.5)


def _item_in(item, ans: str) -> bool:
    if _isfloat(get_clean_string(item)):
        v = float(get_clean_string(item))
        return any(common.num_close(n, v, include_percentage=False)
                   for n in common.numbers_in(ans))
    return common.contains(ans, get_clean_string(item))


def cited_page_ok(row: dict, result: dict):
    """True/False if pages were cited and evidence pages are known, else None."""
    pages = common.cited_pages(result.get("citations"))
    ev = (row.get("meta") or {}).get("evidence_pages")
    if isinstance(ev, str):
        ev = [int(x) for x in re.findall(r"\d+", ev)]
    if pages is None or not ev:
        return None
    return bool(set(int(x) for x in ev) & set(pages))


def score(row: dict, result: dict, judge, ctx=None) -> dict:
    ans = result.get("answer") or ""
    meta = row.get("meta") or {}
    fmt = meta.get("answer_format") or ("None" if not row.get("answerable", True) else "Str")
    nf = common.says_not_found(ans)
    cited = cited_page_ok(row, result) if row.get("answerable", True) else None
    detail = {"answer_format": fmt, "said_not_found": nf}
    if not row.get("answerable", True) or str(fmt).lower() == "none":
        ok = nf
        extracted = None
        if not ok and judge is not None and getattr(judge, "remote", False):
            extracted = judge.extract(row.get("question", ""), ans, "None")
            ok = extracted is not None and get_clean_string(extracted) == "not answerable"
        detail["extracted"] = extracted
        return common.result_dict(1.0 if ok else 0.0, ok, "not_found", detail, None)
    if nf:
        return common.result_dict(0.0, False, "said_not_found", detail, cited)
    extracted = None
    if judge is not None and getattr(judge, "remote", False):
        extracted = judge.extract(row.get("question", ""), ans, fmt)
    gold = row.get("gold") or ""
    if extracted is not None:
        detail["extracted"] = extracted
        cl = get_clean_string(extracted)
        if cl in ("not answerable", "fail to answer"):
            s = 0.0
        else:
            s = eval_score(gold, extracted, fmt)
        metric = "rule"
    else:
        s = heuristic_score(gold, ans, fmt)
        metric = "rule_heuristic"
    return common.result_dict(s, s >= 0.5, metric, detail, cited)
