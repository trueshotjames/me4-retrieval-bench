# Project hooks: the small edits your project needs

The benchmark drives your voice agent without a microphone or speaker. It feeds
each question as text (plus an optional WAV clip) and listens for a few
**marks** that say when your real answer starts. You need four small edits.

## 1. Copy `marks.py`

Copy `me4bench/marks.py` into your project. It has no dependencies and does
nothing unless `ME4_BENCH=1`.

```python
from marks import mark, bench_active
```

When active, every `mark()` call writes a line to the file named by
`ME4_BENCH_MARKS`. The time is `time.monotonic_ns()`, the same clock the harness
uses, so the harness can line your marks up with its own timings.

## 2. The `ME4_BENCH` switch: no microphone, no speaker

When `bench_active()` is true:

- do not open the microphone and do not wait for a wake word: the question comes in as text or a WAV clip
- do not play audio: synthesise the first chunk (so the time is real), then throw it away
- do not write audio files

```python
if bench_active():
    transport = None          # no mic / speaker
    wake_word = None
else:
    transport = LocalAudioTransport(...)
```

## 3. A reset hook and a scope

Between questions the harness calls `reset()`. It must clear the conversation
history and any memory from the last question, but keep the index loaded.

Each dataset can be searched on its own. Do either of these:

- **Scope filter:** accept a namespace, for example `search(query, ns="mmlb")`, or
- **One index per dataset:** read the index folder from `ME4_INDEX_DIR`.

```python
def reset():
    agent.history.clear()
    agent.scratchpad = {}

INDEX_DIR = os.environ.get("ME4_INDEX_DIR", "index/")
```

## 4. Marks

Required: call `mark("answer_start")` right before the real answer starts, after
any "let me look" filler and after any clarifying question.

Optional, but they make the report clearer:

| mark | when |
|---|---|
| `clarify` | you ask the user a clarifying question (`text=` the question) |
| `retrieval_start` / `retrieval_end` | around a search (`n=` hits) |
| `tool_start` / `tool_end` | around a tool call (`name=`) |

Plain Python:

```python
mark("retrieval_start")
hits = index.search(question, ns=scope)
mark("retrieval_end", n=len(hits))
mark("answer_start")
say(answer_text)
```

Pipecat-style pipeline (put a small processor in front of TTS):

```python
class AnswerMark(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.sent = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            self.sent = False
        if isinstance(frame, TextFrame) and not self.sent and not is_filler(frame.text):
            mark("answer_start")
            self.sent = True
        await self.push_frame(frame, direction)
```

If you report no marks at all, the harness falls back to a filler rule. That
rule is only a guess, and results that use it are marked "filler rule, unvalidated".

## 5. Citations

End the answer text with the sources in square brackets, or return them as a list:

```
The total was 42 [report.pdf, page 3] [budget.xlsx, sheet Q2]
```

As a list: `[{"file": "report.pdf", "page": 3}]`. If you report no citations,
the report says "not reported". You are not marked down for it.

## Timing marks reach the harness
The runner sets `ME4_BENCH=1`, `ME4_BENCH_MARKS=<run folder>/marks.jsonl` and `ME4_INDEX_DIR`. Your adapter turns your project's marks into events: `mark("answer_start")` -> `{"t":"answer_start"}`, and the first TTS chunk **of the real answer** -> `{"t":"first_audio"}`. A `first_audio` seen before `answer_start` (plan or clarification audio) is ignored by the clock.
