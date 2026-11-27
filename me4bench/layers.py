"""Three-layer debug metrics, next to the official scores (which they never change).

Layer 1, retrieval: did the agent find the gold evidence? Uses the ranked list the adapter reports
  in the answer event as ``retrieved`` (best first). If it reports none, ``files_opened`` and then
  ``citations`` stand in, and the source used is recorded. A hit is counted per page for
  MMLongBench-Doc (PDF + evidence page) and per file everywhere else. RepoProbe has no
  per-file gold, so it has no layer 1.
  Recall@5, Recall@10, nDCG@10 (binary relevance), MRR (1 / rank of the first gold item).
  Pass: every gold item (up to 10) is in the top 10.
Layer 2, generation: is the answer right when the evidence is handed over? That is a run with
  ``--scope given`` (oracle documents); its accuracy is the layer-2 correctness. A run with
  ``--scope decoy`` hands over another question's documents instead, and the only right answer is
  "not found" (refusal check).
Layer 3, end to end: a normal ``--scope scoped`` run. Each answer is correct, acceptable (score
  0.5 or more but not correct), missing ("not found" to an answerable question, or no answer) or
  wrong (a hallucination). Truthfulness = +1 correct, +0.5 acceptable, 0 missing, -1 wrong (CRAG).
  Evidence sufficiency: share of the gold items that the cited files/pages cover.

``tag_runs(e2e_rows, oracle_rows)`` joins a scoped run and a given run into one tag per question:
retrieval miss (L1 fail), generation miss (L1 pass, L2 fail), integration miss (L1 and L2 pass,
L3 fail), or pass. Without a retrieval list, a question that also fails with the documents handed
over counts as a generation miss; anything else that cannot be decided is "unknown".
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from .scoring import common


# ---- evidence units ------------------------------------------------------------------------
def _base(s) -> str:
    s = str(s or "").strip().lower().replace("\\", "/").rsplit("/", 1)[-1]
    s = re.sub(r"#.*$", "", s)
    return re.sub(r"\.[a-z0-9]{1,5}$", "", s)


def _same_file(a: str, b: str) -> bool:
    return bool(a) and bool(b) and (a == b or (len(a) >= 4 and len(b) >= 4 and (a in b or b in a)))


def _pages(v) -> List[int]:
    if v is None:
        return []
    vals = v if isinstance(v, (list, tuple)) else re.findall(r"\d+", str(v))
    out = []
    for x in vals:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            pass
    return out


def _items(lst) -> List[tuple]:
    """Ranked (file_base, page|None) from strings ("a.pdf", "a.pdf#p3"), dicts or [file, page]."""
    out = []
    for c in lst or []:
        f, p = None, None
        if isinstance(c, dict):
            f, p = c.get("file") or c.get("doc") or c.get("id"), c.get("page")
        elif isinstance(c, (list, tuple)) and c:
            f, p = c[0], (c[1] if len(c) > 1 else None)
        elif isinstance(c, str):
            m = re.search(r"#p(?:age)?=?(\d+)$", c.strip(), re.I)
            f, p = c, (m.group(1) if m else None)
        if not f:
            continue
        ps = _pages(p)
        if ps:
            out += [(_base(f), x) for x in ps]
        else:
            out.append((_base(f), None))
    return out


def gold_units(row: dict) -> List[tuple]:
    """Gold evidence as (file_base, page|None). Empty when the question has none or no layer 1."""
    if not row.get("answerable", True):
        return []
    ds, meta = row.get("ds"), row.get("meta") or {}
    if ds == "repoprobe":
        return [(_base(f), None) for f in meta.get("gold_files") or []]
    docs = [g for g in row.get("gold_docs") or [] if g]
    if ds == "mmlb" and docs and meta.get("evidence_pages"):
        return [(_base(docs[0]), p) for p in meta["evidence_pages"]]
    return list(dict.fromkeys((_base(g), None) for g in docs))


def _match(item: tuple, gold: tuple, page_level: bool) -> bool:
    if not _same_file(item[0], gold[0]):
        return False
    return not page_level or item[1] == gold[1]


def _hits(ranked: List[tuple], gold: List[tuple], page_level: bool) -> List[Optional[int]]:
    """For each ranked item, the gold index it newly covers (or None). Each gold item counts once."""
    used, out = set(), []
    for it in ranked:
        k = next((i for i, g in enumerate(gold) if i not in used and _match(it, g, page_level)), None)
        if k is not None:
            used.add(k)
        out.append(k)
    return out


# ---- per question --------------------------------------------------------------------------
def retrieval(row: dict, rec: dict) -> Optional[dict]:
    gold = gold_units(row)
    if not gold:
        return None
    src, lst = None, None
    for k in ("retrieved", "files_opened", "citations"):
        if rec.get(k):
            src, lst = k, rec[k]
            break
    if src is None:
        return {"source": None}
    ranked = list(dict.fromkeys(_items(lst)))
    page_level = all(g[1] is not None for g in gold) and any(it[1] is not None for it in ranked)
    if not page_level:
        gold = list(dict.fromkeys((g[0], None) for g in gold))
        ranked = list(dict.fromkeys((it[0], None) for it in ranked))
    hits = _hits(ranked, gold, page_level)
    n = len(gold)
    rec_at = lambda k: sum(1 for h in hits[:k] if h is not None) / n  # noqa: E731
    dcg = sum(1 / math.log2(i + 2) for i, h in enumerate(hits[:10]) if h is not None)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(n, 10)))
    first = next((i for i, h in enumerate(hits) if h is not None), None)
    return {"source": src, "level": "page" if page_level else "file", "n_gold": n,
            "recall@5": rec_at(5), "recall@10": rec_at(10), "ndcg@10": dcg / idcg if idcg else 0.0,
            "mrr": 1 / (first + 1) if first is not None else 0.0,
            "pass": sum(1 for h in hits[:10] if h is not None) >= min(n, 10)}


def outcome(rec: dict) -> Optional[str]:
    if rec.get("needs_judge"):
        return None
    if rec.get("correct"):
        return "correct"
    if rec.get("failed") or rec.get("error") or not (rec.get("answer") or "").strip() \
            or common.says_not_found(rec.get("answer") or ""):
        return "missing"
    if float(rec.get("score") or 0) >= 0.5:
        return "acceptable"
    return "wrong"


TRUTH = {"correct": 1.0, "acceptable": 0.5, "missing": 0.0, "wrong": -1.0}


def evidence(row: dict, rec: dict) -> Optional[float]:
    gold = gold_units(row)
    cites = _items(rec.get("citations"))
    if not gold or not cites:
        return None
    page_level = all(g[1] is not None for g in gold)
    hits = _hits(list(dict.fromkeys(cites)), gold, page_level)
    return sum(1 for h in hits if h is not None) / len(gold)


def per_question(row: dict, rec: dict) -> dict:
    """Layer fields for one scored record (computed while gold is still known)."""
    o = outcome(rec)
    return {"l1": retrieval(row, rec), "l3": {"outcome": o, "evidence": evidence(row, rec)}}


# ---- decoy documents (refusal check) -------------------------------------------------------
def decoy_docs(rows: List[dict]) -> Dict[str, list]:
    """{question id: another question's gold documents from the same dataset, sharing no file}."""
    by_ds = defaultdict(list)
    for r in rows:
        if r.get("gold_docs"):
            by_ds[r["ds"]].append(r)
    for pool in by_ds.values():
        pool.sort(key=lambda r: r["id"])
    out = {}
    for r in rows:
        pool = by_ds.get(r["ds"], [])
        mine = {_base(g) for g in r.get("gold_docs") or []}
        if not pool:
            continue
        start = int(hashlib.sha1(("decoy:" + r["id"]).encode()).hexdigest(), 16) % len(pool)
        for k in range(len(pool)):
            c = pool[(start + k) % len(pool)]
            if c["id"] != r["id"] and not ({_base(g) for g in c["gold_docs"]} & mine):
                out[r["id"]] = list(c["gold_docs"])
                break
    return out


# ---- aggregates ----------------------------------------------------------------------------
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _block(rows: List[dict], scope: str) -> dict:
    lay = [r.get("layers") or {} for r in rows]
    l1 = [x["l1"] for x in lay if x.get("l1") and x["l1"].get("source")]
    l1_none = sum(1 for x in lay if x.get("l1") and not x["l1"].get("source"))
    outs = [x["l3"]["outcome"] for x in lay if (x.get("l3") or {}).get("outcome")]
    ev = [x["l3"]["evidence"] for x in lay if (x.get("l3") or {}).get("evidence") is not None]
    oc = Counter(outs)
    n = len(outs)
    out = {
        "l1": {"n": len(l1), "n_without_list": l1_none,
               "sources": dict(Counter(x["source"] for x in l1)),
               "levels": dict(Counter(x["level"] for x in l1)),
               "recall@5": _mean([x["recall@5"] for x in l1]), "recall@10": _mean([x["recall@10"] for x in l1]),
               "ndcg@10": _mean([x["ndcg@10"] for x in l1]), "mrr": _mean([x["mrr"] for x in l1]),
               "pass_rate": _mean([1.0 if x["pass"] else 0.0 for x in l1])},
        "l3": {"n": n, "accuracy": oc["correct"] / n if n else None,
               "acceptable_rate": oc["acceptable"] / n if n else None,
               "missing_rate": oc["missing"] / n if n else None,
               "hallucination_rate": oc["wrong"] / n if n else None,
               "truthfulness": _mean([TRUTH[o] for o in outs]),
               "evidence_sufficiency": _mean(ev), "evidence_full_rate": _mean([1.0 if e >= 1 else 0.0 for e in ev]),
               "n_evidence": len(ev)},
    }
    if scope in ("given", "decoy"):
        sc = [r for r in rows if not r.get("needs_judge")]
        key = "oracle_accuracy" if scope == "given" else "refusal_rate"
        out["l2"] = {"n": len(sc), key: _mean([1.0 if r.get("correct") else 0.0 for r in sc])}
    return out


def aggregate(rows: List[dict], scope: str = "scoped") -> dict:
    by = defaultdict(list)
    for r in rows:
        by[r.get("ds")].append(r)
    return {"scope": scope, "overall": _block(rows, scope), "datasets": {d: _block(rs, scope) for d, rs in by.items()}}


# ---- joining runs into tags ----------------------------------------------------------------
TAGS = ("pass", "retrieval miss", "generation miss", "integration miss", "unknown")


def tag_runs(e2e_rows: List[dict], oracle_rows: Optional[List[dict]] = None) -> List[dict]:
    """One tag per question of the end-to-end run. The oracle (``--scope given``) run gives layer 2."""
    l2 = {}
    for r in oracle_rows or []:
        if not r.get("needs_judge"):
            l2[r["id"]] = l2.get(r["id"], False) or bool(r.get("correct"))
    out = []
    for r in e2e_rows:
        if r.get("needs_judge"):
            continue
        l1 = (r.get("layers") or {}).get("l1") or {}
        p1 = l1.get("pass") if l1.get("source") else None
        p2 = l2.get(r["id"])
        p3 = bool(r.get("correct"))
        if p3:
            tag = "pass"
        elif p1 is False:
            tag = "retrieval miss"
        elif p1 and p2 is False:
            tag = "generation miss"
        elif p1 and p2:
            tag = "integration miss"
        elif p1 is None and p2 is False:
            tag = "generation miss"     # fails even with the documents handed over
        else:
            tag = "unknown"
        out.append({"key": r.get("key"), "id": r["id"], "ds": r.get("ds"), "stratum": r.get("stratum"),
                    "l1_pass": p1, "l2_pass": p2, "l3_pass": p3, "tag": tag})
    return out


def tag_summary(tags: List[dict]) -> dict:
    by = defaultdict(Counter)
    for t in tags:
        by[t["ds"]][t["tag"]] += 1
        by["all"][t["tag"]] += 1
    return {ds: {k: c[k] for k in TAGS} for ds, c in by.items()}
