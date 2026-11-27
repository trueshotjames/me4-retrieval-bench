"""LLM judge with offline fallbacks and a disk cache.

Backends:

* ``"none"``: never calls out. ``verdict()`` returns None, so the question is
  marked ``needs_judge`` and can be re-scored later.
* ``"heuristic"``: offline. Correct if token F1 >= 0.6 or the gold answer is
  contained in the answer. For checklist items, correct if >= 60% of the
  item's content words appear in the answer. Used in tests and as a fallback.
* ``"anthropic"``: POST https://api.anthropic.com/v1/messages with
  ``ANTHROPIC_API_KEY``. The model comes from ``ME4B_JUDGE_MODEL`` (default
  ``claude-sonnet-5-5``).
* ``"openai_compat"``: ``ME4B_JUDGE_URL`` (base URL or the full
  /chat/completions URL), ``ME4B_JUDGE_KEY``, ``ME4B_JUDGE_MODEL``.

The remote judges must reply with a strict JSON verdict
``{"correct": bool, "reason": str}``. Verdicts are cached in
``runs/.judge_cache.jsonl``, keyed by ``sha256(prompt + model)``, so
re-scoring costs nothing. Token counts are recorded in ``Judge.usage``.

Sampling note: current Claude models reject non-default ``temperature``, so
the anthropic backend sends no temperature. The judge is kept stable by the
fixed prompt and the cache. Set ``ME4B_JUDGE_TEMPERATURE`` to send one
anyway, for example to an older model.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from . import common

DEFAULT_MODEL = "claude-sonnet-5-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
BACKENDS = ("none", "heuristic", "anthropic", "openai_compat")

_JSON_RULE = ('Reply with ONLY a JSON object on one line, no other text: '
              '{{"correct": true or false, "reason": "<one short sentence>"}}')
# (templates use str.format: literal braces are doubled)

JUDGE_PROMPTS = {
    "qa": (
        "You are grading an answer from a question-answering system against a "
        "ground-truth answer.\n\nQuestion: {question}\nGround truth: {gold}\n"
        "Answer to grade: {answer}\n\n"
        "The answer is correct if it states the same essential fact as the ground "
        "truth: same entity, same number (small rounding or unit wording is fine), "
        "same yes/no. Extra correct detail is fine. It is incorrect if it gives a "
        "different or contradictory answer, hedges between several answers, or "
        "says the information was not found. Ignore citations in square brackets.\n"
        + _JSON_RULE),
    "frames": (
        "===Task===\nYou are evaluating an answer to a multi-hop factual question "
        "against the ground-truth answer.\n\n===Question===\n{question}\n\n"
        "===Ground truth===\n{gold}\n\n===Answer to grade===\n{answer}\n\n"
        "===Instructions===\nDecide whether the answer contains the essential "
        "meaning of the ground truth. Minor wording, formatting or rounding "
        "differences are fine. Additional information that does not contradict "
        "the ground truth is fine. If the answer gives a different final answer, "
        "lists several candidate answers without committing, or says the "
        "information is unavailable, it is incorrect. Ignore citations in square "
        "brackets.\n" + _JSON_RULE),
    "mimotable": (
        "You are grading an answer to a question about spreadsheet tables.\n\n"
        "Question: {question}\nGround truth: {gold}\nAnswer to grade: {answer}\n\n"
        "The answer is correct if its final value(s) match the ground truth. "
        "Numbers must agree after rounding to the precision of the ground truth "
        "(units, thousands separators and percent signs may differ). For lists, "
        "every ground-truth item must be present and no wrong item added. "
        "Explanations are fine. Ignore citations in square brackets.\n" + _JSON_RULE),
    "multihop": (
        "You are grading an answer to a multi-hop question over news articles.\n\n"
        "Question: {question}\nGround truth: {gold}\nAnswer to grade: {answer}\n\n"
        "The answer is correct only if its conclusion matches the ground truth "
        "(for yes/no questions the yes/no must match; for entity questions the "
        "same entity must be named). Mentioning the right words while concluding "
        "something else is incorrect. Ignore citations in square brackets.\n" + _JSON_RULE),
    "repoprobe_item": (
        "You are grading an answer to a question about a source code repository "
        "against ONE checklist item from the reference answer.\n\n"
        "Question: {question}\nChecklist item: {item}\nAnswer to grade: {answer}\n\n"
        "Mark correct if the answer clearly states or covers this item (it may use "
        "different words). Mark incorrect if the item is missing, contradicted, or "
        "only vaguely hinted at.\n" + _JSON_RULE),
    "mmlb_extract": (
        "Extract the short final answer from a model response to a question "
        "about a document.\n\nQuestion: {question}\nResponse: {answer}\n"
        "Answer format: {answer_format}\n\n"
        "Rules: Int -> a single integer; Float -> a single number; Str -> a short "
        "string; List -> a JSON list of short strings or numbers; None -> the "
        "response says the question cannot be answered from the document, then "
        'extract "Not answerable". If the response gives no answer, extract '
        '"Fail to answer".\nReply with ONLY a JSON object on one line: '
        '{{"extracted": <the extracted answer>}}'),
}


def _fmt(kind: str, **fields) -> str:
    tmpl = JUDGE_PROMPTS[kind]
    safe = {k: ("" if v is None else str(v)) for k, v in fields.items()}
    return tmpl.format_map(_Missing(safe))


class _Missing(dict):
    def __missing__(self, key):
        return ""


_STOP = set("a an the of to in on for and or is are was were be by with as at "
            "that this it its from which who what how does do uses use used".split())


def _heuristic(kind: str, fields: dict) -> dict:
    answer = fields.get("answer", "")
    if kind == "repoprobe_item":
        item_words = [w for w in common.tokens(fields.get("item", "")) if w not in _STOP]
        ans = set(common.tokens(answer))
        if not item_words:
            return {"correct": False, "reason": "empty checklist item"}
        frac = sum(1 for w in item_words if w in ans) / len(item_words)
        return {"correct": frac >= 0.6, "reason": "heuristic word recall %.2f" % frac}
    gold = fields.get("gold", "")
    f1 = common.token_f1(common.strip_citations(answer), gold)
    cont = common.contains(answer, gold)
    if common.says_not_found(answer) and not common.contains(answer, gold):
        return {"correct": False, "reason": "heuristic: answer says not found"}
    return {"correct": bool(f1 >= 0.6 or cont),
            "reason": "heuristic token F1 %.2f%s" % (f1, ", contains gold" if cont else "")}


def parse_verdict(text: str) -> Optional[dict]:
    """Parse ``{"correct": bool, "reason": str}`` from model text; None if invalid."""
    if not text:
        return None
    for m in re.finditer(r"\{[^{}]*\}", text, re.S):
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            continue
        c = obj.get("correct")
        if isinstance(c, str) and c.lower() in ("true", "false"):
            c = c.lower() == "true"
        if isinstance(c, bool):
            return {"correct": c, "reason": str(obj.get("reason", ""))}
    return None


class JudgeError(RuntimeError):
    pass


class Judge:
    """Grades answers. ``verdict(kind, **fields)`` -> dict or None."""

    def __init__(self, backend: str = "heuristic", model: Optional[str] = None,
                 cache_path: Optional[str] = os.path.join("runs", ".judge_cache.jsonl"),
                 timeout_s: float = 60.0, max_retries: int = 4, backoff_s: float = 2.0,
                 api_key: Optional[str] = None, url: Optional[str] = None):
        if backend not in BACKENDS:
            raise ValueError("judge backend must be one of %s" % (BACKENDS,))
        self.backend = backend
        self.timeout_s, self.max_retries, self.backoff_s = timeout_s, max_retries, backoff_s
        self.model = model or os.environ.get("ME4B_JUDGE_MODEL") or (
            DEFAULT_MODEL if backend == "anthropic" else backend)
        self.url = url
        self.api_key = api_key
        if backend == "anthropic":
            self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            self.url = url or ANTHROPIC_URL
            if not self.api_key:
                raise JudgeError("ANTHROPIC_API_KEY is not set (use --judge none to score later)")
        elif backend == "openai_compat":
            self.api_key = api_key or os.environ.get("ME4B_JUDGE_KEY", "")
            base = url or os.environ.get("ME4B_JUDGE_URL")
            if not base:
                raise JudgeError("ME4B_JUDGE_URL is not set")
            base = base.rstrip("/")
            self.url = base if base.endswith("/chat/completions") else base + "/chat/completions"
            if not os.environ.get("ME4B_JUDGE_MODEL") and not model:
                raise JudgeError("ME4B_JUDGE_MODEL is not set")
        self.cache_path = cache_path if backend in ("anthropic", "openai_compat") else None
        self._cache = {}
        self._lock = threading.Lock()
        self.usage = {"calls": 0, "cache_hits": 0, "errors": 0, "in": 0, "out": 0}
        self.last_error: Optional[str] = None
        self._load_cache()

    @property
    def remote(self) -> bool:
        return self.backend in ("anthropic", "openai_compat")

    def config(self) -> dict:
        """What to save into a run's config.json."""
        return {"backend": self.backend, "model": self.model, "url": self.url,
                "prompts": dict(JUDGE_PROMPTS)}

    # ------------------------------------------------------------- cache
    def _load_cache(self):
        if not self.cache_path or not os.path.exists(self.cache_path):
            return
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        self._cache[rec["key"]] = rec
                    except (ValueError, KeyError):
                        continue
        except OSError:
            pass

    def _key(self, prompt: str) -> str:
        return hashlib.sha256((prompt + self.model).encode("utf-8")).hexdigest()

    def _store(self, rec: dict):
        self._cache[rec["key"]] = rec
        if not self.cache_path:
            return
        try:
            d = os.path.dirname(self.cache_path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self.cache_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ---------------------------------------------------------- public
    def verdict(self, kind: str, **fields) -> Optional[dict]:
        """Grade with prompt ``JUDGE_PROMPTS[kind]``. Returns ``{"correct",
        "reason", "source", "tokens"}`` or None (no judge, or the call failed)."""
        if self.backend == "none":
            return None
        if self.backend == "heuristic":
            v = _heuristic(kind, fields)
            v.update(source="heuristic", tokens=None)
            return v
        prompt = _fmt(kind, **fields)
        text, tokens, cached = self._complete(prompt)
        if text is None:
            return None
        v = parse_verdict(text)
        if v is None:
            self.last_error = "unparseable verdict: %r" % text[:200]
            self.usage["errors"] += 1
            return None
        v.update(source="cache" if cached else self.backend, tokens=tokens)
        return v

    def extract(self, question: str, answer: str, answer_format: str) -> Optional[str]:
        """MMLB answer extraction (optional official step). None offline/failure."""
        if not self.remote:
            return None
        prompt = _fmt("mmlb_extract", question=question, answer=answer,
                      answer_format=answer_format)
        text, _, _ = self._complete(prompt)
        if text is None:
            return None
        for m in re.finditer(r"\{.*\}", text, re.S):
            try:
                obj = json.loads(m.group(0))
            except ValueError:
                continue
            if "extracted" in obj:
                ex = obj["extracted"]
                return json.dumps(ex) if isinstance(ex, list) else str(ex)
        return None

    # ---------------------------------------------------------- remote
    def _complete(self, prompt: str):
        key = self._key(prompt)
        with self._lock:
            if key in self._cache:
                self.usage["cache_hits"] += 1
                rec = self._cache[key]
                return rec.get("text"), rec.get("tokens"), True
        text, tokens = self._call_with_retries(prompt)
        if text is not None:
            with self._lock:
                self._store({"key": key, "model": self.model, "backend": self.backend,
                             "text": text, "tokens": tokens,
                             "t": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return text, tokens, False

    def _call_with_retries(self, prompt: str):
        delay = self.backoff_s
        for attempt in range(self.max_retries + 1):
            try:
                text, tokens = self._call(prompt)
                self.usage["calls"] += 1
                if tokens:
                    self.usage["in"] += tokens.get("in") or 0
                    self.usage["out"] += tokens.get("out") or 0
                return text, tokens
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:300]
                except Exception:  # noqa: BLE001
                    pass
                self.last_error = "HTTP %s: %s" % (e.code, body)
                retry = e.code in (408, 409, 429) or e.code >= 500
                if not retry:
                    break
                ra = e.headers.get("retry-after") if e.headers else None
                wait = float(ra) if ra and ra.replace(".", "", 1).isdigit() else delay
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
                self.last_error = "%s: %s" % (type(e).__name__, e)
                wait = delay
            if attempt < self.max_retries:
                time.sleep(min(wait, 60.0))
                delay *= 2
        self.usage["errors"] += 1
        return None, None

    def _post(self, headers: dict, body: dict) -> dict:
        req = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _temperature(self):
        t = os.environ.get("ME4B_JUDGE_TEMPERATURE")
        try:
            return float(t) if t not in (None, "") else None
        except ValueError:
            return None

    def _call(self, prompt: str):
        if self.backend == "anthropic":
            body = {"model": self.model, "max_tokens": 2048,
                    "messages": [{"role": "user", "content": prompt}],
                    "output_config": {"effort": "low"}}
            temp = self._temperature()
            if temp is not None:
                body["temperature"] = temp
            data = self._post({"x-api-key": self.api_key,
                               "anthropic-version": ANTHROPIC_VERSION,
                               "content-type": "application/json"}, body)
            text = "".join(b.get("text", "") for b in data.get("content", [])
                           if b.get("type") == "text")
            u = data.get("usage") or {}
            return text, {"in": u.get("input_tokens"), "out": u.get("output_tokens")}
        body = {"model": self.model, "temperature": 0,
                "messages": [{"role": "user", "content": prompt}]}
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = "Bearer " + self.api_key
        data = self._post(headers, body)
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        u = data.get("usage") or {}
        return text, {"in": u.get("prompt_tokens"), "out": u.get("completion_tokens")}
