"""Voice-only question feed and answer timing (the heart of the benchmark).

The runner never shows the agent text it would not hear: each question is an
*utterance* ``{"text", "clip", "id"}`` (``clip`` = pre-rendered WAV, or None).
The adapter feeds the clip inside ``ask`` and yields events (docs/SCHEMA.md).

Timing (``ask_one``):

* ``t0`` is the moment ``adapter.ask`` is called. Adapters that play the
  question clip should yield ``{"t": "question_end"}`` as their FIRST event once
  the clip has been delivered; ``t0`` is then reset to that event's time, so
  the clip's duration is excluded.
* ``plan``, ``clarify``, ``tool`` and ``speech`` events never stop the clock.
* ``first_audio`` stops the clock -> ``t_first_answer_s`` (timing_source
  ``"first_audio"``).
* Without ``first_audio``: ``answer_start`` time (``"answer_start"``); else,
  when the adapter reports spoken chunks (``{"t": "speech", "text": ...}``)
  but no marks, the :class:`FillerRule` picks the first substantive chunk
  (``"filler_rule"``, flagged unvalidated); else the final ``answer`` event
  time (``"answer_end"``).
* The first ``clarify`` gets ``gen.send(clarify_utterance)`` exactly once; any
  later ``clarify`` gets ``send(None)``. All are counted in ``n_clarify``.

Timeouts and exceptions are caught and reported as a failed question; nothing
is raised. The generator is driven from a worker thread so a hung adapter
cannot stall the run.
"""
from __future__ import annotations

import os
import queue
import re
import threading
import time
from typing import Callable, List, Optional

from .speech import CLARIFY_REPLY, normalise

DEFAULT_ANSWER_TIMEOUT_S = 120.0
DEFAULT_CLARIFY_TIMEOUT_S = 60.0
FILLER_FLAG = "filler rule, unvalidated"


# ------------------------------------------------------------------- clips
class ClipStore:
    """Resolve pre-rendered question clips: ``<audio_dir>/<ds>/<id>.wav`` and
    the fixed clarification reply ``<audio_dir>/_fixed/clarify.wav``.

    ``audio_dir=None`` means text-only utterances (``clip`` is None).
    """

    def __init__(self, audio_dir: Optional[str] = "audio"):
        self.audio_dir = audio_dir

    def _path(self, *parts: str) -> Optional[str]:
        if not self.audio_dir:
            return None
        p = os.path.join(self.audio_dir, *parts)
        return p if os.path.isfile(p) else None

    def clip_for(self, row: dict) -> Optional[str]:
        return self._path(row.get("ds", ""), "%s.wav" % row.get("id", ""))

    def clarify_clip(self) -> Optional[str]:
        return self._path("_fixed", "clarify.wav")

    def utterance(self, row: dict) -> dict:
        text = row.get("spoken") or normalise(row.get("question", ""))
        return {"text": text, "clip": self.clip_for(row), "id": row.get("id")}

    def clarify_utterance(self) -> dict:
        return {"text": CLARIFY_REPLY, "clip": self.clarify_clip(), "id": "_clarify"}


# ------------------------------------------------------------- filler rule
class FillerRule:
    """Decide whether a spoken chunk is the real answer or filler.

    Used only when an adapter yields no ``answer_start``/``first_audio`` marks.
    A chunk is *filler* if it matches a filler phrase. Otherwise its first
    sentence counts only if it contains a digit, a capitalised name (a
    capitalised word that is not the sentence's first word and not "I"), or at
    least ``min_quote`` consecutive words quoted from the retrieved text.
    Results using this rule are flagged ``"filler rule, unvalidated"``.
    """

    FILLERS = ("one moment", "let me look", "let me check", "let me see",
               "give me a second", "give me a moment", "just a moment",
               "just a second", "hold on", "hang on", "looking that up",
               "let me find", "searching", "checking", "i'm looking",
               "i am looking", "one second", "bear with me", "let me think",
               "good question", "sure", "okay", "ok", "alright", "hmm", "um")

    def __init__(self, fillers=None, min_quote: int = 3):
        self.fillers = tuple(fillers) if fillers else self.FILLERS
        self.min_quote = min_quote
        self._re = re.compile(r"^\W*(?:%s)\b" % "|".join(
            re.escape(f) for f in sorted(self.fillers, key=len, reverse=True)), re.I)

    @staticmethod
    def first_sentence(text: str) -> str:
        m = re.match(r"\s*(.+?[.!?])(?:\s|$)", text or "", re.S)
        return (m.group(1) if m else (text or "")).strip()

    def is_filler(self, text: str) -> bool:
        return bool(self._re.match(text or ""))

    def is_substantive(self, text: str, retrieved: str = "") -> bool:
        if not text or not text.strip() or self.is_filler(text):
            return False
        s = self.first_sentence(text)
        if re.search(r"\d", s):
            return True
        words = re.findall(r"[A-Za-z][\w'-]*", s)
        if any(w[0].isupper() and w != "I" for w in words[1:]):
            return True
        if retrieved and len(words) >= self.min_quote:
            src = " " + " ".join(re.findall(r"[a-z0-9']+", retrieved.lower())) + " "
            low = [w.lower() for w in words]
            for i in range(len(low) - self.min_quote + 1):
                if " " + " ".join(low[i:i + self.min_quote]) + " " in src:
                    return True
        return False


# ------------------------------------------------------------------ ask_one
def _empty_result(row: dict) -> dict:
    return {"id": row.get("id"), "answer": "", "citations": None, "files_opened": None,
            "tokens": None, "t_first_answer_s": None, "t_answer_start_s": None,
            "t_total_s": None, "timing_source": None, "timing_note": None,
            "n_clarify": 0, "n_plan": 0, "tool_events": 0, "error": None,
            "failed": False, "events": []}


def ask_one(adapter, row: dict, scope: dict, clips: Optional[ClipStore] = None,
            clock: Callable[[], float] = time.monotonic,
            clarify_timeout_s: float = DEFAULT_CLARIFY_TIMEOUT_S,
            answer_timeout_s: float = DEFAULT_ANSWER_TIMEOUT_S,
            filler_rule: Optional[FillerRule] = None) -> dict:
    """Ask one question and time the answer; never raises.

    ``answer_timeout_s`` bounds the whole question (from ``t0``);
    ``clarify_timeout_s`` bounds the wait for the next event after the
    clarification reply has been sent. Both are enforced in real time while
    waiting, and the ``clock``-measured total is checked as well.

    Returns a dict with: ``id, answer, citations, files_opened, tokens,
    t_first_answer_s, t_answer_start_s, t_total_s, timing_source, timing_note,
    n_clarify, n_plan, tool_events, error, failed, events`` where ``events`` is
    a list of ``(t, event)`` with ``t`` in seconds relative to ``t0``.
    """
    clips = clips or ClipStore(None)
    filler_rule = filler_rule or FillerRule()
    res = _empty_result(row)
    try:
        utt = clips.utterance(row)
        reply = clips.clarify_utterance()
    except Exception as e:  # noqa: BLE001
        res.update(error="exception: %s: %s" % (type(e).__name__, e), failed=True)
        return res

    q: "queue.Queue" = queue.Queue()
    stop = threading.Event()
    t_call = clock()

    def worker():
        try:
            gen = adapter.ask(utt, scope)
            can_send = hasattr(gen, "send")
            it = gen if can_send else iter(gen)
        except BaseException as e:  # noqa: BLE001
            q.put(("exc", clock(), "%s: %s" % (type(e).__name__, e)))
            return
        to_send, first, clarified = None, True, False
        try:
            while not stop.is_set():
                try:
                    if first or not can_send:
                        ev = next(it)
                        first = False
                    else:
                        ev = it.send(to_send)
                except StopIteration:
                    q.put(("end", clock(), None))
                    return
                t = clock()
                to_send = None
                if isinstance(ev, dict) and ev.get("t") == "clarify":
                    if not clarified:
                        to_send, clarified = reply, True
                        q.put(("ev", t, ev, "clarify_reply"))
                        continue
                q.put(("ev", t, ev, None))
                if isinstance(ev, dict) and ev.get("t") == "answer":
                    return
        except BaseException as e:  # noqa: BLE001
            q.put(("exc", clock(), "%s: %s" % (type(e).__name__, e)))
        finally:
            if can_send:
                try:
                    gen.close()
                except BaseException:  # noqa: BLE001
                    pass

    th = threading.Thread(target=worker, name="ask-%s" % row.get("id"), daemon=True)
    th.start()

    t0 = t_call
    wall_start = time.monotonic()
    wall_deadline = wall_start + answer_timeout_s
    events: List[tuple] = []
    t_first_audio = t_answer_start = t_answer = t_filler = t_last_clarify = None
    audio_ts: list = []
    retrieved_parts: List[str] = []
    answer_ev = None
    error = None
    after_clarify = False
    n_events = 0

    while True:
        now = time.monotonic()
        wait = wall_deadline - now
        if after_clarify:
            wait = min(wait, clarify_timeout_s)
        if wait <= 0:
            error = "timeout"
            break
        try:
            item = q.get(timeout=wait)
        except queue.Empty:
            error = "timeout (no reply after clarification)" if after_clarify and \
                time.monotonic() < wall_deadline else "timeout"
            break
        kind, t = item[0], item[1]
        if kind == "exc":
            error = "exception: " + item[2]
            break
        if kind == "end":
            error = "no answer (generator ended without an 'answer' event)"
            break
        ev, note = item[2], item[3]
        n_events += 1
        after_clarify = note == "clarify_reply"
        if not isinstance(ev, dict):
            events.append((t, {"t": "invalid", "value": repr(ev)}))
            continue
        events.append((t, ev))
        typ = ev.get("t")
        if typ == "question_end" and n_events == 1:
            t0 = t
            wall_deadline = time.monotonic() + answer_timeout_s
        elif typ == "plan":
            res["n_plan"] += 1
        elif typ == "clarify":
            res["n_clarify"] += 1
            t_last_clarify = t
        elif typ == "tool":
            res["tool_events"] += 1
            if ev.get("phase") == "end":
                for k in ("text", "result", "output"):
                    if isinstance(ev.get(k), str):
                        retrieved_parts.append(ev[k])
        elif typ == "answer_start":
            if t_answer_start is None:
                t_answer_start = t
        elif typ == "first_audio":
            # audio of a plan or clarifying question must not stop the clock: only count after answer_start
            audio_ts.append(t)
        elif typ == "speech":
            if t_filler is None and filler_rule.is_substantive(
                    str(ev.get("text", "")), " ".join(retrieved_parts)):
                t_filler = t
        elif typ == "answer":
            t_answer = t
            answer_ev = ev
            break

    # audio of plans/clarifications must not stop the clock: take the first audio at/after answer_start;
    # with no answer_start, the first audio after the last clarification.
    after = t_answer_start if t_answer_start is not None else t_last_clarify
    cand = [x for x in audio_ts if after is None or x >= after]
    t_first_audio = cand[0] if cand else None

    if error is not None:
        stop.set()

    rel = lambda t: None if t is None else round(t - t0, 4)  # noqa: E731
    res["events"] = [(rel(t), ev) for t, ev in events]
    if answer_ev is not None:
        res["answer"] = str(answer_ev.get("text") or "")
        res["citations"] = answer_ev.get("citations")
        res["files_opened"] = answer_ev.get("files_opened")
        res["tokens"] = answer_ev.get("tokens")
    res["t_answer_start_s"] = rel(t_answer_start)
    if t_answer is not None:
        res["t_total_s"] = rel(t_answer)
    elif events:
        res["t_total_s"] = rel(events[-1][0])
    if t_first_audio is not None:
        res["t_first_answer_s"], res["timing_source"] = rel(t_first_audio), "first_audio"
    elif t_answer_start is not None:
        res["t_first_answer_s"], res["timing_source"] = rel(t_answer_start), "answer_start"
    elif t_filler is not None:
        res["t_first_answer_s"], res["timing_source"] = rel(t_filler), "filler_rule"
        res["timing_note"] = FILLER_FLAG
    elif t_answer is not None:
        res["t_first_answer_s"], res["timing_source"] = rel(t_answer), "answer_end"
    if error is None and res["t_total_s"] is not None and res["t_total_s"] > answer_timeout_s:
        error = "timeout"
    res["error"] = error
    res["failed"] = error is not None
    return res
