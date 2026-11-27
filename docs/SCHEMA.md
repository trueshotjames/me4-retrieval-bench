# Shared schemas (the contract between modules)

## Question row (dict, one per question) — `data/_manifest/<ds>.jsonl`
| key | meaning |
|---|---|
| `id` | `"<ds>-<idx:04d>"`, e.g. `frames-0172` |
| `ds` | one of `frames multihop mmlb repoprobe mimotable` |
| `idx` | 0-based row index in the official question file (the split uses it) |
| `question` | official question text |
| `spoken` | speech-normalised text for TTS (filled by `speech.normalise`) |
| `gold` | gold answer, string (`"Not answerable"`/`"null"` normalised to `""` when `answerable` is false) |
| `answerable` | bool; false = correct reply is "not found" |
| `gold_docs` | list of document ids relative to `data/<ds>/` (FRAMES: article titles, MMLB: pdf name, MiMoTable: xlsx names, RepoProbe: repo name, MultiHop: article urls). Empty for unanswerable |
| `stratum` | slot label, exactly as in `slots.py` (or `NO_SLOT`) |
| `bucket` | files-needed bucket: `0 1 2 3-4 5+ n/a` |
| `split` | `test` or `train` (from `manifest/split.json`) |
| `meta` | dataset-specific extras, e.g. MMLB `evidence_pages`, `answer_format`; RepoProbe `checklist`; MultiHop `question_type`; FRAMES `reasoning_types` |

## Adapter protocol (`adapters/agent_entry.py`)
Functions: `describe()`, `ingest(folder, namespace)` (remote only), `attach(index_dir, namespace)`, `reset()`,
`ask(utt, scope)` (generator), `shutdown()`.

`utt` = `{"text": str, "clip": path|None, "id": str}`. `scope` = `{"mode": "scoped|given|mixed", "namespace": ds, "docs": [...]}`.

`ask` yields event dicts and receives the clarification reply via `send()`:
- `{"t":"plan","text":...}` plan of action. Does NOT stop the clock.
- `{"t":"clarify","text":...}` clarifying question. Runner does `gen.send(reply_utt)` once (fixed reply); a second `clarify` is answered with `None`. Does NOT stop the clock.
- `{"t":"tool","name":...,"phase":"start|end","args":{}}` optional.
- `{"t":"answer_start"}` real answer begins (the `mark("answer_start")` of the plan).
- `{"t":"first_audio"}` first synthesised audio chunk of the real answer (stops the clock; not played).
- `{"t":"answer","text":..., "citations":[{"file":..,"page":..,"sheet":..,"slide":..,"line":..}], "files_opened":[..], "tokens":{"in":..,"out":..}}` final. Ends the question.
The runner timestamps events on receipt (`time.monotonic()`), so adapters only yield.

## Run folder `runs/<student>/<YYYYmmdd-HHMMSS>/`
`config.json`, `events.jsonl` (every event + time), `results.jsonl` (one row per question: question row id, answer, citations, timings, score, judge), `results.json`, `results.csv`, `results.html`, `run.log`, `traces/<id>.txt`. No audio is recorded.
