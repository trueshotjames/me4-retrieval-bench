"""Scripted fake agent for smoke tests. ME4_FAKE_MODE: gold (default; answers the gold string),
wrong (answers 'banana'), notfound (always says not found), clarify (asks once, then gold).
Looks the gold up from data/_manifest (or ME4_FAKE_ROWS=<jsonl>) by question text."""
import json
import os
import time
from pathlib import Path

_gold = {}
MODE = os.environ.get("ME4_FAKE_MODE", "gold")


def describe():
    return {"name": "fake-agent", "version": MODE, "needs_gpu_for_ingest": False}


def ingest(folder, namespace):
    raise RuntimeError("fake agent has nothing to ingest")


def attach(index_dir, namespace):
    paths = [Path(os.environ["ME4_FAKE_ROWS"])] if os.environ.get("ME4_FAKE_ROWS") else \
        list(Path(os.environ.get("ME4_DATA", "data")).glob("_manifest/*.jsonl"))
    for p in paths:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                _gold[r["question"].strip()] = r


def reset():
    pass


def ask(utt, scope):
    r = _gold.get(utt["text"].strip()) or {}
    if MODE == "clarify":
        yield {"t": "clarify", "text": "Which file do you mean?"}
        yield {"t": "plan", "text": "Search then answer"}
    time.sleep(float(os.environ.get("ME4_FAKE_DELAY", "0.01")))
    yield {"t": "answer_start"}
    yield {"t": "first_audio"}
    if MODE == "wrong":
        text = "banana"
    elif MODE == "notfound" or not r.get("answerable", True):
        text = "Not found in the files."
    else:
        text = r.get("gold", "")
    docs = r.get("gold_docs") or []
    yield {"t": "answer", "text": text, "citations": [{"file": d} for d in docs[:1]],
           "files_opened": docs, "tokens": {"in": 10, "out": 5}}


def shutdown():
    pass
