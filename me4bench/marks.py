"""Benchmark timing marks for a voice agent project (copy this file as-is).

Usage in your project::

    from marks import mark
    mark("answer_start")                 # the real answer begins
    mark("retrieval_start"); ...; mark("retrieval_end", n=5)

Does nothing unless the environment has ``ME4_BENCH=1``. When active, each call
appends one JSON line ``{"t": <time.monotonic_ns()>, "mark": name, ...}`` to
the file named by ``ME4_BENCH_MARKS`` (flushed at once). Never raises.
Standard library only.
"""
import json
import os
import threading
import time

_lock = threading.Lock()


def bench_active():
    """True when the benchmark harness is driving this process (ME4_BENCH=1)."""
    return os.environ.get("ME4_BENCH", "").strip() == "1"


def mark(name, **kw):
    """Record a timing mark; a no-op outside the benchmark. Never raises."""
    try:
        if not bench_active():
            return
        path = os.environ.get("ME4_BENCH_MARKS")
        if not path:
            return
        rec = {"t": time.monotonic_ns(), "mark": str(name)}
        for k, v in kw.items():
            if k not in rec:
                rec[k] = v
        line = json.dumps(rec, default=str, ensure_ascii=False) + "\n"
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
    except Exception:  # noqa: BLE001 - marks must never break the project
        pass
