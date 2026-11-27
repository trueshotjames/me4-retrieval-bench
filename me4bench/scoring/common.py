"""Shared scoring helpers: normalisation, token F1, ANLS, numbers, lists,
not-found detection, citation checks and percentiles."""
from __future__ import annotations

import math
import re
import string
from collections import Counter
from typing import Iterable, List, Optional, Sequence

_ARTICLES = re.compile(r"\b(a|an|the)\b", re.I)
_PUNCT = set(string.punctuation) | {"“", "”", "‘", "’", "–", "—"}
_CITATION_RE = re.compile(r"\[[^\[\]]*\]")

NOT_FOUND_RE = re.compile(
    r"\b(?:not\s+found|cannot\s+be\s+(?:determined|found|answered)|"
    r"can(?:no|')t\s+be\s+(?:determined|found|answered)|"
    r"(?:is|are)\s*n[o']t\s+(?:in|mentioned\s+in|available\s+in|present\s+in)\s+the\s+"
    r"(?:files?|documents?|sources?|provided|text|context)|"
    r"not\s+in\s+the\s+(?:files?|documents?|sources?|provided|context)|"
    r"no\s+(?:relevant\s+)?information|not\s+answerable|unanswerable|"
    r"(?:could|can)\s*(?:n[o']t|not)\s+find|unable\s+to\s+(?:find|determine|locate)|"
    r"insufficient\s+information|not\s+enough\s+information|"
    r"(?:do|does)\s*(?:n[o']t|not)\s+(?:have|contain|mention|include)\s+"
    r"(?:any\s+|enough\s+|that\s+|this\s+)?(?:information|details|data|mention)|"
    r"(?:is|are)\s*(?:n[o']t|not)\s+mentioned|no\s+mention\s+of|"
    r"there\s+is\s+no\s+(?:record|data|answer)\s+(?:of|for|in)|"
    r"not\s+(?:specified|stated|provided)\s+in)\b", re.I)


def strip_citations(text: str) -> str:
    """Remove ``[file, page]`` style citations from an answer."""
    return _CITATION_RE.sub(" ", text or "")


def says_not_found(text: str) -> bool:
    """True if the answer says the information is not in the files."""
    return bool(NOT_FOUND_RE.search(strip_citations(text or "")))


def normalise(s) -> str:
    """SQuAD-style: lowercase, drop punctuation and articles, squeeze spaces."""
    s = strip_citations(str(s if s is not None else "")).lower()
    s = "".join(" " if ch in _PUNCT else ch for ch in s)
    s = _ARTICLES.sub(" ", s)
    return " ".join(s.split())


def _num_join(s: str) -> str:
    # "1,234" -> "1234" before punctuation removal
    return re.sub(r"(?<=\d),(?=\d{3}\b)", "", s or "")


def tokens(s) -> List[str]:
    return normalise(_num_join(str(s if s is not None else ""))).split()


def token_f1(pred, gold) -> float:
    """Token-level F1 between two strings (0..1)."""
    p, g = tokens(pred), tokens(gold)
    if not p and not g:
        return 1.0
    if not p or not g:
        return 0.0
    common = Counter(p) & Counter(g)
    same = sum(common.values())
    if same == 0:
        return 0.0
    prec, rec = same / len(p), same / len(g)
    return 2 * prec * rec / (prec + rec)


def contains(pred, gold) -> bool:
    """Normalised gold appears as a whole-token run inside normalised pred."""
    g = " ".join(tokens(gold))
    if not g:
        return False
    return (" " + g + " ") in (" " + " ".join(tokens(pred)) + " ")


def exact_match(pred, gold) -> bool:
    return tokens(pred) == tokens(gold) and bool(tokens(gold))


# ------------------------------------------------------------------- ANLS
def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def anls(pred, gold, threshold: float = 0.5) -> float:
    """Average Normalised Levenshtein Similarity for one pair (MMLB style):
    ``1 - NL`` if ``NL < threshold`` else 0, on lowercased stripped strings."""
    p = " ".join(str(pred or "").lower().split())
    g = " ".join(str(gold or "").lower().split())
    length = max(len(p), len(g))
    if length == 0:
        return 1.0
    nl = levenshtein(p, g) / length
    return 1.0 - nl if nl < threshold else 0.0


# ---------------------------------------------------------------- numbers
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_WORDNUM = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}


def parse_number(s) -> Optional[float]:
    """Parse a single number from a short string ('1,234', '$5.2', '45%', 'seven')."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = _num_join(str(s)).strip().lower()
    t = re.sub(r"[$£€¥%]", "", t).strip()
    try:
        return float(t)
    except ValueError:
        pass
    if t in _WORDNUM:
        return float(_WORDNUM[t])
    m = _NUM_RE.fullmatch(t.split()[0]) if t.split() else None
    return float(m.group(0)) if m else None


def numbers_in(text) -> List[float]:
    """All numbers in a text (citations removed); includes small number words."""
    t = _num_join(strip_citations(str(text or "")))
    out = [float(x) for x in _NUM_RE.findall(t)]
    for w in re.findall(r"[a-z]+", t.lower()):
        if w in _WORDNUM:
            out.append(float(_WORDNUM[w]))
    return out


def num_close(a: float, b: float, rel: float = 0.01, include_percentage: bool = True) -> bool:
    """MMLB-style float equality: relative tolerance 1%, optionally also
    accepting the value scaled by 100 (percent vs fraction)."""
    cands = [b, b / 100.0, b * 100.0] if include_percentage else [b]
    for c in cands:
        if a == c or math.isclose(a, c, rel_tol=rel, abs_tol=1e-9):
            return True
    return False


# ------------------------------------------------------------------ lists
def list_f1(pred: Sequence, gold: Sequence) -> float:
    """F1 over normalised list items (order-insensitive)."""
    p = Counter(normalise(x) for x in pred if normalise(x))
    g = Counter(normalise(x) for x in gold if normalise(x))
    if not p and not g:
        return 1.0
    if not p or not g:
        return 0.0
    same = sum((p & g).values())
    if same == 0:
        return 0.0
    prec, rec = same / sum(p.values()), same / sum(g.values())
    return 2 * prec * rec / (prec + rec)


# -------------------------------------------------------------- citations
def _base(s) -> str:
    s = str(s or "").strip().lower().replace("\\", "/")
    s = s.rsplit("/", 1)[-1]
    return re.sub(r"\.[a-z0-9]{1,5}$", "", s)


def cited_files(citations) -> Optional[List[str]]:
    """File names from a citation list, or None when not reported."""
    if not citations:
        return None
    out = []
    for c in citations:
        if isinstance(c, dict):
            if c.get("file"):
                out.append(str(c["file"]))
        elif isinstance(c, (list, tuple)) and c:
            out.append(str(c[0]))
        elif isinstance(c, str):
            out.append(c.split(",")[0])
    return out or None


def cited_file_ok(citations, gold_docs: Iterable) -> Optional[bool]:
    """True if any cited file matches a gold document; None if either side is
    missing (no citations reported, or no gold documents)."""
    files = cited_files(citations)
    gold = [g for g in (gold_docs or []) if g]
    if files is None or not gold:
        return None
    gb = {_base(g) for g in gold}
    for f in files:
        b = _base(f)
        if not b:
            continue
        if b in gb or any(b in g or g in b for g in gb if len(g) >= 4 and len(b) >= 4):
            return True
    return False


def cited_pages(citations) -> Optional[List[int]]:
    """Integer page numbers from citations, or None when none reported."""
    if not citations:
        return None
    out = []
    for c in citations:
        page = None
        if isinstance(c, dict):
            page = c.get("page")
        elif isinstance(c, (list, tuple)) and len(c) > 1:
            page = c[1]
        if page is None:
            continue
        if isinstance(page, (list, tuple)):
            vals = page
        else:
            vals = re.findall(r"\d+", str(page))
        for v in vals:
            try:
                out.append(int(v))
            except (TypeError, ValueError):
                pass
    return out or None


# ------------------------------------------------------------ percentiles
def percentile_nearest(values: Sequence[float], q: float) -> Optional[float]:
    """Nearest-rank percentile (q in 0..100); None for an empty list."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = int(math.ceil(q / 100.0 * len(v) - 1e-9))
    return v[min(max(k, 1), len(v)) - 1]


def median(values: Sequence[float]) -> Optional[float]:
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def result_dict(score: float, correct: bool, metric: str, detail: Optional[dict] = None,
                cited_ok: Optional[bool] = None, needs_judge: bool = False) -> dict:
    """The standard score_question output."""
    return {"score": float(score), "correct": bool(correct), "metric": metric,
            "detail": detail or {}, "cited_ok": cited_ok, "needs_judge": bool(needs_judge)}
