# ME4 retrieval benchmark

A harness that asks your ME4 voice agent questions from five public datasets, times how fast the real answer starts, scores it, and writes an HTML report. The datasets are FRAMES, MultiHop-RAG, MMLongBench-Doc, RepoProbe and MiMoTable (English).

- Standard library only, Python 3.9+. Runs on Windows, macOS, Linux and Raspberry Pi 4/5.
- No dataset files are in this repo. One command downloads them from their official sources.
- **Indexing never runs on the Raspberry Pi.** Embedding and OCR or vision run on a cloud VM, a GPU box over SSH, or a free Kaggle notebook. The Pi only loads the finished `index/` and answers questions.
- Benchmark mode is voice-only: no microphone, no speaker, nothing recorded.

```
 official sources ──fetch──▶ data/<ds>/  ──build-manifest──▶ data/_manifest/<ds>.jsonl (questions + split + strata)
                                 │
                                 └─ index (remote machine) ──▶ index/  ──copy──▶ Pi
                                                                            │
 runner: plan rounds ─▶ adapter.ask(question) ─▶ events + answer ─▶ score ─▶ runs/<student>/<stamp>/results.html
```

## Contents
- [Quick start](#quick-start)
- [Setup and run guide](#setup-and-run-guide): download, connect your agent, build the index, record clips, check, run, read results
- [How the benchmark works](#how-the-benchmark-works): split, rounds, question delivery, timing, scope, scoring, metrics, test-mode rules
- [Datasets](#datasets) and [Sample questions](#sample-questions)
- [Repository layout](#repository-layout), [Known limitations](#known-limitations), [Tests](#tests)

## Quick start
```
git clone https://github.com/trueshotjames/me4-retrieval-bench && cd me4-retrieval-bench
python tools/fetch_data.py                                            # 1. download all five datasets (~1.5 GB) and build the manifest
python -m me4bench run --adapter adapters/fake_agent.py --mode train  # 2. try the harness with the scripted fake agent
cp adapters/agent_entry_template.py adapters/agent_entry.py            # 3. connect your agent (fill in six functions)
bash tools/remote_index.sh                                             # 4. on a GPU/cloud machine: build index/, then copy it to the Pi
python -m me4bench menu                                                # 5. on the Pi: pick test or training, rounds, datasets, and go
```
On Windows use `copy` instead of `cp`, and run step 4 on a Linux machine or a Kaggle notebook. Use `python3` where `python` is Python 2.

## Setup and run guide

### 0. Requirements
- Python 3.9 or newer. No packages are needed. Optional: `rich` (nicer console) and `pypdf` (one-time MMLB text-layer scan).
- About 1.5 GB of free disk for `data/`, plus whatever your index needs.
- Network access to huggingface.co, en.wikipedia.org, github.com, codeload.github.com, raw.githubusercontent.com and media.githubusercontent.com.
- For the remote judge (recommended for real scores): an Anthropic API key, or any OpenAI-compatible endpoint.

### 1. Download the datasets
```
python tools/fetch_data.py --dry-run     # list every download with its size; nothing is written
python tools/fetch_data.py               # download all five, retry failed FRAMES articles, build and check the manifest
```
One run does everything, and it is resumable: after a network failure, run the same command again. Files already downloaded are skipped, and FRAMES articles that failed last time are retried. When it finishes it prints `All strata match the expected counts. Test set: 459 questions.` and `All done. Next: python -m me4bench menu`.

| Flag | What it does |
|---|---|
| `--dry-run` | List downloads and sizes only. Add `--offline` to skip probing the network. |
| `--force` | Download again even if present. |
| `--no-mixed-formats` | Store every FRAMES article as `.md` and every MiMoTable workbook as `.xlsx` (default: about 30% of FRAMES become .docx, .html, .txt or .odt, and about 30% of MiMoTable become .ods, .csv/.tsv or .xls). |
| `--no-manifest` | Skip the manifest step at the end. |
| `--only <ds> ...` | Debugging only. Fetches just those datasets and skips the manifest step. Normal use always fetches all five. |
| `--root <dir>` | Where datasets go (default `./data`). |
| `--write-pins` | Write the sha256 of fetched files to `manifest/pins.json`. |

| Environment variable | Use |
|---|---|
| `GITHUB_TOKEN` | Avoids GitHub rate limits (RepoProbe and MiMoTable come from GitHub). |
| `HF_TOKEN` | Hugging Face token, if you get rate-limited there. `HF_ENDPOINT` points at a mirror. |
| `HTTPS_PROXY` | Honoured for all downloads. |
| `ME4_CONTACT` | Optional URL that identifies you (for example this repo's URL). Added to the Wikipedia User-Agent, since Wikimedia throttles anonymous clients harder. |
| `ME4_FRAMES_MIXED_SHARE` | Share of FRAMES articles in non-Markdown formats, 0 to 1 (default 0.3). |
| `ME4_MIMOTABLE_MIXED_SHARE` | Share of MiMoTable workbooks in non-.xlsx formats, 0 to 1 (default 0.3). |
| `ME4_SOFFICE` | Path to LibreOffice `soffice`, if it is not on PATH or in the usual install folder. Only needed for the .xls share. |

Nothing reads `.env` automatically. `.env.example` lists the variables; set them in your shell.

**Where the data comes from**
| Dataset | Source |
|---|---|
| FRAMES | `test.tsv` from Hugging Face `google/frames-benchmark`; each linked article from the Wikipedia Action API |
| MultiHop-RAG | `yixuantt/MultiHop-RAG` on GitHub, pinned to commit `3dd4d4e` |
| MMLongBench-Doc | `mayubo2333/MMLongBench-Doc` on GitHub (questions and 135 PDFs) |
| RepoProbe | `Tencent-Hunyuan/RepoProbe` on GitHub for the questions; each of the 15 repos as a zip at its pinned commit, media stripped |
| MiMoTable | `jasonNLP/MiMoTable` on GitHub, English problems and spreadsheets |

**How FRAMES articles are fetched:** 50 titles per request, one request at a time, about 52 requests for all 2,551 articles. Wikipedia returns wikitext, which is converted to Markdown locally. Templates are not expanded, so infoboxes become key/value lists, common templates (dates, unit conversions, lists) are rendered, and citations and other templates are dropped. Each article's revision id is saved in `data/frames/_revisions.json`. Which article gets which file type is fixed by its title, so every machine gets the same mix.

**How MiMoTable file types are mixed:** after the download, 62 of the 206 workbooks (30%) are stored in another type, split about evenly: .ods (written in pure Python, any workbook), .csv or .tsv (only one-sheet files with a simple header, no merged cells, and no question that names a sheet), and .xls (only with LibreOffice installed, and only workbooks whose sheet names fit the 31-character .xls limit). Without LibreOffice the .xls share becomes .ods. Cell values, dates, merged cells and sheet names are kept; formulas, fonts and column widths are not. The choice is fixed by file name and content, the mapping is saved in `data/mimotable/english/_formats.json`, and each question's gold files follow it. Strata and the test split do not change. To change the mix, delete `data/mimotable` and fetch again.

**The manifest step** loads each dataset's official question file, assigns the fixed train/test split, labels each question with its stratum, and writes `data/_manifest/<ds>.jsonl` (row format in `docs/SCHEMA.md`). It then compares every stratum's size and test count with `manifest/strata_expected.json`. If anything differs it prints each mismatch and records it in `data/_manifest/_status.json`, and **test runs are refused** until it matches. `python -m me4bench build-manifest` reruns this step alone.

### 2. Connect your agent (the adapter)
The harness talks to your project only through `adapters/agent_entry.py`. Your project code stays where it is.

1. `cp adapters/agent_entry_template.py adapters/agent_entry.py` (this file is git-ignored).
2. Point it at your project with `--project <folder>` or `ME4_PROJECT=<folder>`. The template adds that folder to `sys.path` so you can import your code.
3. Fill in six functions:

| Function | Called | Must do |
|---|---|---|
| `describe()` | once | Return `{"name", "version", "needs_gpu_for_ingest"}`. |
| `ingest(folder, namespace)` | only by `index`, never on a Pi | Embed/OCR every file under `folder` into your index, tagged `namespace` (the dataset key). |
| `attach(index_dir, scope)` | once per run | Open the prebuilt index read-only. The second argument is the run's `--scope` value. |
| `reset()` | before every question | Clear conversation history and memory; keep the index loaded. |
| `ask(utt, scope)` | once per question | A generator that yields events (below). |
| `shutdown()` | once at the end | Clean up. |

`utt` is `{"text": spoken question, "clip": WAV path or None, "id": question id}`. `scope` is `{"mode", "namespace", "docs"}` (see [Scope](#scope-which-documents-the-agent-may-search)).

Events `ask` yields, in order:

| Event | Meaning | Stops the clock? |
|---|---|---|
| `{"t":"question_end"}` | Optional, and only as the first event: the question clip finished playing. The clock restarts here, so clip length is not counted. | no |
| `{"t":"plan","text":...}` | A plan or "let me look" filler. | no |
| `{"t":"clarify","text":...}` | A clarifying question. The first one gets the fixed reply via `reply = yield {...}`; later ones get `None`. | no |
| `{"t":"tool","name":...,"phase":"start"\|"end"}` | Optional tool trace. | no |
| `{"t":"speech","text":...}` | Optional spoken chunk; only used by the filler-rule fallback. | no |
| `{"t":"answer_start"}` | The real answer begins. | fallback |
| `{"t":"first_audio"}` | First synthesised audio chunk of the real answer (synthesised, not played). | **yes** |
| `{"t":"answer","text":...,"citations":[...],"files_opened":[...],"tokens":{"in","out"}}` | Final answer. Ends the question. | |

The runner timestamps events as it receives them, so adapters never measure time themselves. Citations are a list like `[{"file": "report.pdf", "page": 3}]` (`sheet`, `slide`, `line` also accepted), or written at the end of the answer as `[report.pdf, page 3]`.

The small edits your project itself needs (a `ME4_BENCH` switch that turns off mic and speaker, a `reset` hook, a namespace filter, and `mark("answer_start")`) are in [`docs/PROJECT_HOOKS.md`](docs/PROJECT_HOOKS.md), with Pipecat and plain-Python examples. `me4bench/marks.py` is the file to copy into your project.

**Try the harness first** with the scripted fake agent, which needs no index:
```
python -m me4bench run --adapter adapters/fake_agent.py --mode train
```
`ME4_FAKE_MODE` sets its behaviour: `gold` (answers the gold answer, default), `wrong`, `notfound`, `clarify` (asks once, then answers). A `gold` run should score close to 100%.

### 3. Build the index on a remote machine
```
bash tools/remote_index.sh              # all five datasets
bash tools/remote_index.sh mmlb frames  # or some of them
```
Run this on a cloud VM, a GPU box over SSH, or a Kaggle notebook (clone the repo in a cell and run the same command). It fetches the data if needed, builds the manifest, calls your `ingest()` once per dataset, and checks the result with `tools/verify_index.py`. It refuses to run on a Raspberry Pi, and so does `python -m me4bench index`. ARM cloud VMs are fine. `ME4_ALLOW_INDEX=1` overrides the check.

`ingest()` gets `data/_corpus/<ds>/`, a copy (hard links) of the documents only. Question files, answer keys and any file starting with `_` are left out, so the index cannot contain the answers.

Your index should write `index/<ds>/MANIFEST.json` with `{"files": [{"path", "bytes"}], "built"}` so `verify_index.py` can check it. Then copy it to the Pi:
```
rsync -av index data/_manifest pi@<host>:~/me4-retrieval-bench/
```
The Pi also needs `data/` itself only if your agent opens original files at answer time (for example to read a page). Otherwise the index and `data/_manifest` are enough.

### 4. Record the question clips (optional)
```
python tools/make_clips.py [--dataset frames] [--tts auto|espeak|piper|say|sapi] [--limit N]
```
Renders each question once with a local TTS (espeak-ng, piper, macOS `say` or Windows SAPI) into `audio/<ds>/<id>.wav`, plus the fixed clarification reply `audio/_fixed/clarify.wav`. The spoken text is the speech-normalised question (numbers, dates, URLs and file names written out the way a person says them). Without clips, `utt["clip"]` is `None` and your agent gets the spoken text.

### 5. Check the setup
```
python -m me4bench check
```
Prints `ok` or `MISSING` for each dataset, whether the manifest and adapter exist, and whether this machine may index.

### 6. Run
```
python -m me4bench menu                                   # interactive: test or training, dataset, exclude code, rounds with time estimates
python -m me4bench run --rounds hour --exclude-repoprobe  # a test run of about an hour, no code questions
python -m me4bench run --dataset mmlb --rounds 2 --mode train
python -m me4bench run --rounds full --dry-run            # print question count, time estimate and repeats; asks nothing
python -m me4bench resume runs/<student>/<stamp>          # continue a stopped run
```
Press Ctrl+C once to stop after the current question; `resume` continues from there with the same questions in the same order.

| Flag | Default | Meaning |
|---|---|---|
| `--mode test\|train` | `test` | Test runs use the fixed test set and are counted. Training runs use the rest and show gold answers. |
| `--rounds N\|round\|hour\|full` | `round` | 40 questions per round. `hour` = what fits in 60 minutes at `--sec-per-q` (3 rounds at 25 s). `full` = 6 rounds, the most a test run can draw without repeating a question. |
| `--dataset <ds>` | all | Ask only this dataset (8 slots per round). |
| `--exclude-repoprobe` | off | Skip code questions; their 8 slots go 2 each to the other four datasets. |
| `--scope scoped\|given\|mixed` | `scoped` | Which documents the agent may search (see below). |
| `--judge none\|heuristic\|anthropic\|openai_compat` | `heuristic` | Scoring judge. Use `anthropic` or `openai_compat` for real scores. |
| `--judge-model` | `claude-sonnet-5-5` (anthropic) | Judge model; also `ME4B_JUDGE_MODEL`. |
| `--seed N` | random | Same seed and data give the same questions in the same order. The seed is saved in `config.json`. |
| `--student <name>` | `$USER` | Names the run folder. |
| `--sec-per-q` | 25 | Only used for time estimates and the `hour` preset. |
| `--adapter <file>` | `adapters/agent_entry.py` | Also `ME4_ADAPTER`. |
| `--project <folder>` | | Sets `ME4_PROJECT` for the adapter. |
| `--root`, `--index`, `--audio`, `--runs` | `data`, `index`, `audio`, `runs` | Folders. |

Judge setup: `--judge anthropic` needs `ANTHROPIC_API_KEY`. `--judge openai_compat` needs `ME4B_JUDGE_URL` (base URL or full `/chat/completions` URL), `ME4B_JUDGE_KEY` and `ME4B_JUDGE_MODEL`. `--judge none` scores nothing that needs a judge, so you can score later.

### 7. Read the results
Each run writes `runs/<student>/<YYYYmmdd-HHMMSS>/`:

| File | Contents |
|---|---|
| `results.html` | The report: overall score, per-dataset table (weighted score, raw accuracy ± 95% margin, p50 and p95 latency, failures), a bar per stratum, accuracy by files needed, behaviour table, machine stats. |
| `results.json` | Config, summary and every scored row. |
| `results.csv` | One line per question: key, dataset, stratum, bucket, score, correct, latency, timing source, citation check, failure. |
| `results.jsonl` | Rows as they are scored (used by `resume`). |
| `events.jsonl` | Every event from your adapter with its time. |
| `marks.jsonl` | Your project's `mark()` calls. |
| `traces/<id>.txt` | Question, answer, score and latency per question. |
| `config.json`, `run.log` | Run settings, question order, progress log. |

`runs/test_runs.jsonl` counts every test run (number, date, code version, student, seed, rounds).

Re-score a run later, for example after a `--judge none` run, without asking the agent again:
```
python -m me4bench score runs/<student>/<stamp> --judge anthropic
python -m me4bench report runs/<student>/<stamp>      # rebuild results.html only
```
Remote judge verdicts are cached in `runs/.judge_cache.jsonl`, so re-scoring the same answers costs nothing.

## How the benchmark works

### The fixed split
`manifest/split.json` lists, for each dataset, the 0-based row indexes of the test questions in the official question file. Everything else is training. The split was drawn once, stratified, and never changes, so every student is tested on the same 459 questions (373 without RepoProbe). `manifest/strata_expected.json` holds the expected size and test count of every stratum; the manifest step checks the downloaded data against it, so a changed upstream file cannot silently move the split.

RepoProbe uses one repo per programming language (the one with the most questions), 15 repos and 209 of its 500 questions. Its test set is 6 whole repos (ProxmoxVE, adk-python, ducklake, opencloud, pkl, valkey, 86 questions), so the agent is tested on code it never practised on.

### Rounds and sampling
A round is 40 questions: 8 per dataset, and each of those 8 slots is a stratum (`me4bench/slots.py`). For each stratum the sampler shuffles its pool with the run's seed and draws without repeats until the pool runs out; only then does it reshuffle and mark the repeats (shown in the menu and saved in `config.json`). The test set gives 6 rounds (240 questions) with no repeats.

Inside a stratum, the sampler prefers questions whose "files needed" bucket (0, 1, 2, 3–4, 5+, n/a for code) is furthest behind its target mix per 40 questions: 3.0, 12.3, 7.5, 7.8, 1.4 and 8.0. This keeps each round's difficulty close to the same mix.

With `--exclude-repoprobe`, RepoProbe's 8 slots go 2 each to the other datasets, spread over their strata by largest remainder, and no stratum gets more than half its pool. The "n/a" bucket is dropped and the others are scaled back to 40.

### Question delivery: voice only
The agent hears what a user would say and nothing more. Each question is sent as the speech-normalised text (and the WAV clip, if recorded). The agent never sees gold answers or the question file, and it sees the gold documents only with `--scope given`. If the agent asks a clarifying question, it gets one fixed reply, "No extra details. Answer from the files, or say not found.", and any further clarifying question gets no reply.

### Timing
- The clock starts when `ask()` is called, or at `question_end` if the adapter plays the clip.
- It stops at the first `first_audio` event at or after `answer_start`. Audio for a plan or a clarifying question does not stop it.
- If there is no `first_audio`, it falls back to `answer_start`; then to the first substantive `speech` chunk (a rule that skips fillers like "let me look"; such results are flagged "filler rule, unvalidated"); then to the final `answer` event. Each row records which source was used.
- A question fails after 120 s in total, or 60 s with no event after the clarification reply. A crash in your adapter also fails just that question; the run goes on.
- Reported latency is p50 and p95 (nearest rank) over the questions that did not fail.

### Scope: which documents the agent may search
| `--scope` | `scope["namespace"]` | `scope["docs"]` | Use |
|---|---|---|---|
| `scoped` (default) | the question's dataset | empty | The agent searches that dataset's whole corpus. |
| `given` | the question's dataset | the gold documents | Isolates reading from retrieval: the agent is told which files to use. |
| `mixed` | `all` | empty | One index over all five corpora; hardest. |

### Scoring
Every answer is scored 0 to 1. "Not found" is detected by a fixed list of phrases ("not in the files", "cannot be determined", "no information", "not answerable", and similar). For a question with no answer in the files, saying not found is the only correct reply; for an answerable question, saying not found is wrong.

| Dataset | How an answer is scored |
|---|---|
| FRAMES | Exact match after normalising, else the LLM judge decides correct or wrong (fixed prompt: same essential fact, no hedging between answers). |
| MultiHop-RAG | Yes/no questions decided offline from the leading yes or no. Others by the judge, which requires the same conclusion. The official "any shared word" score is reported separately as *lenient accuracy*, because it is very lenient on long spoken answers. |
| MMLongBench-Doc | The official rules by answer format. Int: equal. Float: within 1%, also accepting ×100 or ÷100. Str: exact for dates, URLs, file names and similar, else ANLS ≥ 0.5. List: same items. None: must say not found. With a remote judge the short answer is extracted first, as in the official pipeline; without one, a documented heuristic is used. |
| RepoProbe | The reference answer's weighted checklist. The judge checks each item separately; the score is the weighted share covered (also shown out of 10). Correct means at least half; *perfect solve rate* counts answers that cover every item. |
| MiMoTable | A single number within 1% of a numeric gold answer is correct without the judge. Otherwise the judge decides (numbers must agree to the gold's precision; lists must match exactly). |

**Judges.** `heuristic` (the default) is offline: correct if token F1 ≥ 0.6 or the gold answer appears in the reply, and a checklist item counts if 60% of its content words appear. It is good for smoke tests, not for reported scores. `anthropic` and `openai_compat` send the fixed prompts in `me4bench/scoring/judge.py` and require a strict JSON verdict. `none` leaves those rows unscored (`needs_judge`); they are left out of accuracy and counted separately until you re-score.

**Citations** are checked but never cost points. `cited_ok` is true if any cited file matches a gold document (for MMLB, if any cited page is an evidence page), false if none do, and "not reported" if the agent gave no citations.

### Metrics in the report
- **Dataset score:** the mean score per stratum, weighted by each stratum's size in the full question file, so the score estimates accuracy on the whole dataset rather than on the slot mix.
- **Overall score:** the mean of the dataset scores.
- **Raw accuracy:** the plain mean, with a worst-case 95% margin (1.96·√(0.25/n)).
- **Behaviour:** correct "not found" rate on unanswerable questions, confident-wrong rate (wrong and did not say not found), false "not found" rate (said not found when the answer exists), clarification rate.
- **Accuracy by files needed**, median files opened, citation check counts, p50/p95 latency and timing sources, failures, max CPU temperature and RAM during the run.

### Test-mode rules
- Test runs are refused if the manifest check failed (`ME4_ALLOW_UNVERIFIED=1` overrides, for practice only).
- Every test run is numbered in `runs/test_runs.jsonl`.
- Test results leave out gold answers, gold documents and judge details, so the test set does not leak through the run folder.
- Training runs show everything, for debugging.

## Datasets

| Dataset | Questions used | Test | Corpus the agent searches | Formats | Scoring |
|---|---|---|---|---|---|
| FRAMES | 824 | 94 | 2,551 Wikipedia articles (about 65 MB) | .md, plus about 30% as .docx, .html, .txt, .odt | LLM judge, correct or wrong |
| MultiHop-RAG | 2,556 | 96 | 609 news articles (6.8 MB, one JSON file) | .json | Judge accuracy; yes/no decided offline; null queries must say "not found" |
| MMLongBench-Doc | 1,082 | 89 | 135 long PDFs (about 636 MB), many pages image-only | .pdf | Official rule scoring by answer format (Int, Float, Str, List, None) |
| RepoProbe | 209 of 500 (15 repos, one per language) | 86 (6 whole repos) | 15 source repositories at pinned commits, media stripped | source code, configs, docs | Weighted checklist, judged item by item (0 to 1) |
| MiMoTable (English) | 641 | 94 | 206 spreadsheets (1.7 MB) | .xlsx, plus about 30% as .ods, .csv/.tsv, .xls | LLM judge, correct or wrong; numbers accepted within 1% |

Test set: 459 questions in total (373 without RepoProbe). Everything else is training. A round has 40 slots, 8 per dataset, and each slot is a stratum below.

**What each dataset tests**
- **FRAMES:** multi-hop questions over 2 to 11 Wikipedia articles. Tests finding several articles from one question, chaining facts across them, and doing light reasoning on top (counting, dates, arithmetic). It also tests the file readers, because the same article can arrive as .md, .docx, .html, .txt or .odt. Strata: tabular (2–3 / 4+ articles), numerical (2–3 / 4+), multiple constraints (2–3 / 4+), temporal, post-processing.
- **MultiHop-RAG:** questions over a news corpus where the answer needs facts from 2 to 4 articles. Tests retrieval across similar articles (same story, different outlets and dates), comparison, time ordering, and refusing when the corpus has no answer. Strata: inference, comparison, temporal, null ("not in the files").
- **MMLongBench-Doc:** questions about long PDFs (reports, papers, guidebooks, brochures). Tests PDF parsing, OCR or vision for pages with no text layer, reading tables, charts and figures, collecting evidence across many pages, and saying "not answerable" when the PDF has no answer. Strata: evidence only on image-only pages, several pages, table, chart, figure, layout text (footnotes, headers, lists), unanswerable (image-heavy / text document). Plain single-page text questions are training only.
- **RepoProbe:** real GitHub Discussions questions about a code repository, with the maintainer's reply as reference. Tests code search over a whole repo: finding the right config key, source file, docs page or design reason, and explaining it the way a maintainer would. Strata: names a source file, config question, error or log text, mentions README or docs, project architecture, business logic, implementation details.
- **MiMoTable:** questions about real-world spreadsheets with messy layouts. Tests reading spreadsheets (.xlsx, and .ods, .csv, .tsv or .xls for about 30% of files) with merged and multi-row headers, several sheets, several tables on one sheet, and several files, then doing lookups, counts, sums, averages and comparisons correctly. Strata: one sheet (simple / complicated header), several sheets (simple / complicated), several tables on one sheet, several files (simple / complicated).

## Sample questions
All samples are real rows from the training split (none are test questions). "Processing" is what a good agent does; the harness only sees the spoken answer and its cited files.

### FRAMES
**1. Numerical + tabular + multiple constraints** (row 5)
> According to the 2000 United States census, what was the 2000 population of the birth city of the only 21st-century mayor of Austin, Texas who also served as mayor in the 1990s? Round your answer to the nearest thousand.

- **Gold:** 506000. **Files:** Mayor of Austin, Kirk Watson, Oklahoma City.
- **Tests:** chaining three articles, reading a table, rounding.
- **Processing:** search the list of Austin mayors and find the one who served in both the 1990s and the 21st century (Kirk Watson). Open his article to get his birthplace (Oklahoma City). Open that city's article, read the 2000 census population from its population table, and round to the nearest thousand.
- **Expected answer:** "About 506,000", citing the three articles.

**2. Temporal + tabular + multiple constraints** (row 3)
> As of August 1, 2024, which country were holders of the FIFA World Cup the last time the UEFA Champions League was won by a club from London?

- **Gold:** France. **Files:** UEFA Champions League, London, FIFA World Cup.
- **Tests:** date reasoning across two lists of winners.
- **Processing:** from the Champions League winners table, find the latest London winner up to August 2024 (Chelsea, 2021). From the World Cup results, find who held the title in 2021 (France, champions from 2018 until the 2022 final).
- **Expected answer:** "France".

### MultiHop-RAG
**3. Inference query** (row 1445)
> Which company, covered by both Engadget and Polygon, is set to release an updated version of their hardware with numerous improvements and immediate availability starting November 16th?

- **Gold:** Valve. **Evidence:** 3 facts from 3 articles (Engadget and Polygon, Steam Deck OLED coverage, 2023-11-09).
- **Tests:** retrieving the same story from two outlets and inferring the company, which the question never names.
- **Processing:** search for hardware releases on November 16 in Engadget and Polygon, find the Steam Deck OLED articles, and answer with the company that makes it.
- **Expected answer:** "Valve", citing the Engadget and Polygon articles.

**4. Null query** (row 420)
> Considering the information from an article in the New York Times and another in the Wall Street Journal about Pets Best, which company, known for its insurance products and mentioned in both articles, has a CEO with the initial 'M'?

- **Gold:** Insufficient information. **Evidence:** none.
- **Tests:** refusing instead of guessing when the corpus has no such articles.
- **Processing:** search for Pets Best in NYT and WSJ articles, find nothing that matches, and stop.
- **Expected answer:** says the files do not contain this (for example "That's not in the files"). Any company name is scored wrong.

### MMLongBench-Doc
**5. Table (one page)** (row 1078)
> What is the FY2018 capital expenditure amount (in USD millions) for 3M based on cash flow statement?

- **Gold:** $1577.00 (Float). **File:** 3M_2018_10K.pdf, page 60.
- **Tests:** finding one table in a long financial report and reading the right cell.
- **Processing:** locate the consolidated statement of cash flows, find "Purchases of property, plant and equipment (PP&E)" for 2018, and report it in USD millions.
- **Expected answer:** "1,577 million dollars" (within 1% counts as correct), citing the PDF and page.

**6. Unanswerable, text document** (row 626)
> For the dataset that uses HOVER as the symbolic solver, how many examples in this dataset are used for evaluation?

- **Gold:** Not answerable. **File:** 2023.findings-emnlp.248.pdf.
- **Tests:** not inventing a number when the paper does not support the premise.
- **Processing:** search the paper for HOVER and its dataset and solver descriptions, find nothing that supports the question, and refuse.
- **Expected answer:** says it is not in the document.

### RepoProbe
**7. Config question** (blink.cmp, Lua, row 148)
> How to display descriptions next to ex commands: I noticed in the demo on a PR about blink.cmp there are these little descriptions of what the current command is, and I'm not sure what I do to my config to get those to appear. Any ideas? *(plus a text description of the demo video)*

- **Reference:** the component is `label_description`; add it to `completion.menu.draw.columns`, for example `{ 'label', 'label_description' }`.
- **Checklist (10 points):** names `label_description` (3), lists the other draw components such as `kind_icon`, `kind`, `label`, `source_name` (3), gives the Lua config (3), clear and no hallucination (1).
- **Tests:** mapping a vague UI description to a config key by reading the repo's docs and config code.
- **Processing:** search the repo docs and source for the menu draw components, find `label_description`, and show where it goes in the config.

**8. Config question** (walker, Rust, row 487)
> Emoji Size. Is there a way to increase the size of emojis? I checked the config and css, but I can't see any obvious way to do this.

- **Reference:** use a different theme for that module; each module can have its own `theme`, so set it to a custom theme with a bigger emoji size.
- **Checklist (10 points):** theme-based solution (4), per-module themes (3), set the module's `theme` property (2), clear (1).
- **Tests:** understanding how the project's theming and config fit together, beyond a keyword match.
- **Processing:** look at the config schema and the theme files, see that styling lives in themes that are set per module, and explain that.

### MiMoTable
**9. One sheet, complicated header** (row 11)
> Did Shop A's actual sales on April 6 exceed the sales target?

- **Gold:** yes, actual 5035.3 against a target of 4000. **File:** StoreSalesDataStatisticsAnalysisTable1.xlsx.
- **Tests:** reading a sheet with merged, multi-row headers and comparing two cells.
- **Processing:** open the sheet, work out which columns are "actual" and "target" under the merged header, find the Shop A row for April 6, and compare.
- **Expected answer:** "Yes, 5,035.3 against a target of 4,000."

**10. Several files, complicated header** (row 590)
> Which product has the highest actual inventory quantity?

- **Gold:** Fleece Jeans, 500. **Files:** three workbooks (accounts receivable, e-commerce operations, inventory count).
- **Tests:** picking the right file out of several, then taking a maximum over a column.
- **Processing:** look through the three files, find the one with an inventory count table, find the "actual quantity" column, and take the row with the largest value.
- **Expected answer:** "Fleece Jeans, with 500."

## Repository layout
| Path | What it is |
|---|---|
| `tools/fetch_data.py` | The one download command (wraps `me4bench/fetch.py`). |
| `tools/remote_index.sh`, `tools/verify_index.py` | Build and check the index on a remote machine. |
| `tools/make_clips.py` | Render question clips with a local TTS. |
| `adapters/agent_entry_template.py` | Copy to `adapters/agent_entry.py` and fill in. |
| `adapters/fake_agent.py` | Scripted agent for trying the harness. |
| `manifest/split.json`, `manifest/strata_expected.json` | The fixed test split and the expected stratum counts. |
| `me4bench/datasets/*.py` | One module per dataset: download, load questions, assign strata and files-needed buckets. |
| `me4bench/manifest.py` | Builds and checks `data/_manifest/`. |
| `me4bench/slots.py`, `me4bench/sampler.py` | The 40 slots per round and the seeded round planner. |
| `me4bench/runner.py`, `me4bench/voice.py` | Run loop, question delivery, timing. |
| `me4bench/speech.py` | Speech normalisation of questions and the fixed clarification reply. |
| `me4bench/scoring/` | Per-dataset scorers, the judge and its prompts, aggregation. |
| `me4bench/report.py` | Writes `results.html`. |
| `me4bench/marks.py` | Timing marks to copy into your project. |
| `me4bench/wikitext.py`, `me4bench/convert.py`, `me4bench/htmlmd.py` | Wikitext to Markdown, and Markdown to docx/html/txt/odt. |
| `docs/SCHEMA.md`, `docs/PROJECT_HOOKS.md` | The data and adapter contracts, and the edits your project needs. |

Created by the run, all git-ignored: `data/` (datasets, `_manifest/`, `_corpus/`), `index/`, `audio/`, `runs/`.

## Known limitations
- **FRAMES articles are fetched live** from Wikipedia, so their text can differ a little from the version the questions were written against. A few linked articles no longer exist (4 of 2,551 on a recent run); the fetch reports them and retries them on the next run.
- **The default judge is the offline heuristic.** It is fine for smoke tests but over- and under-counts on long spoken answers. Report scores from `--judge anthropic` or `openai_compat`.
- **The filler-rule timing** is a fallback for projects that send no marks. It is a guess and is flagged in the results.
- **Sample "processing" steps** below describe what a good agent does; they were written by hand and not checked against an agent run.
- **Test-set size limits precision:** about ±10 points (95%) per dataset on 94 questions, about ±4.6 points over all 459.

## Tests
```
python -m unittest discover -s tests
```
Unit tests for fetching, conversion, sampling, speech, timing and scoring, plus an end-to-end smoke run with the fake agent. They use small fixtures and need no network or downloaded data.
