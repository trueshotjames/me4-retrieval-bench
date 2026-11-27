"""Copy this file to adapters/agent_entry.py and fill in the six functions.
Your project code stays where it is; import it from here (set ME4_PROJECT or use --project).

The harness calls these in order: describe -> attach -> (reset -> ask)* -> shutdown.
`ingest` is only called on the remote machine that builds the index; never on a Raspberry Pi.
See docs/SCHEMA.md for the event dicts `ask` must yield.
"""
import os
import sys

PROJECT = os.environ.get("ME4_PROJECT", "")
if PROJECT and PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)
os.environ["ME4_BENCH"] = "1"     # your project's benchmark switch: no speaker, no mic, no recording


def describe():
    return {"name": "my-me4-agent", "version": "0", "needs_gpu_for_ingest": True}


def ingest(folder, namespace):
    """REMOTE ONLY. Embed/OCR every file under `folder` into your index, tagged `namespace`."""
    raise NotImplementedError


def attach(index_dir, namespace):
    """Open the prebuilt index (read-only) so queries can run on this machine."""
    raise NotImplementedError


def reset():
    """Forget conversation state between questions."""


def ask(utt, scope):
    """Generator. utt={'text','clip','id'}; scope={'mode','namespace','docs'}.
    yield {"t":"plan","text":...}            # optional; does not stop the clock
    yield {"t":"clarify","text":...}         # optional; reply arrives via `reply = yield ...`
    yield {"t":"answer_start"}               # real answer begins
    yield {"t":"first_audio"}                # first synthesised audio chunk (not played)
    yield {"t":"answer","text":...,"citations":[],"files_opened":[],"tokens":{"in":0,"out":0},
           "retrieved":[]}                  # optional: ranked retriever hits, best first (layer-1 metrics)
    """
    raise NotImplementedError
    yield  # pragma: no cover


def shutdown():
    pass
