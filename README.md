# ME4 retrieval benchmark

**Indexing never runs on the Raspberry Pi.** Embedding and OCR/vision run on a cloud VM, a GPU cluster over SSH, or a free Kaggle notebook (`tools/remote_index.sh`). The Pi only copies the finished `index/` and answers questions. `python -m me4bench index` refuses to run on ARM boards.

Five public datasets: FRAMES, MultiHop-RAG, MMLongBench-Doc, RepoProbe, MiMoTable (English). Nothing is committed; they are downloaded from the official sources.

FRAMES articles: about 30% are stored as .docx, .html, .txt or .odt (the rest as .md) so your file readers get exercised. Which article gets which type is fixed by its title. Size effect is small (FRAMES is roughly 65 MB). Turn it off with `python tools/fetch_data.py --no-mixed-formats`. Wikipedia text is fetched live, so it can differ slightly from the version FRAMES was written against.

## 1. Download (Windows, macOS, Linux, Raspberry Pi 4/5)
Python 3.9+ only, no packages, no git.
```
python tools/fetch_data.py --dry-run     # what will be downloaded, with sizes (about 1.5 GB)
python tools/fetch_data.py               # resumable: re-run after any failure
python -m me4bench build-manifest        # builds question lists, checks them against the fixed split
```
Always downloads all five. If GitHub rate-limits you, set `GITHUB_TOKEN`; for Hugging Face set `HF_TOKEN`. Behind a proxy, `HTTPS_PROXY` is honoured.
**Unverified:** the loaders for MultiHop-RAG, MMLongBench-Doc, RepoProbe and MiMoTable were written without network access to the real files. `build-manifest` prints any stratum whose counts differ from the expected ones; send that output if it does.

## 2. Connect your project
Copy `adapters/agent_entry_template.py` to `adapters/agent_entry.py` and fill it in (`--project <folder>` or `ME4_PROJECT`). Project-side edits are minimal: see `docs/PROJECT_HOOKS.md`. Try the harness first with `--adapter adapters/fake_agent.py`.

## 3. Run
```
python -m me4bench menu                          # test or training, rounds with duration estimates, one dataset, exclude code
python -m me4bench run --rounds hour --exclude-repoprobe
python -m me4bench run --dataset mmlb --rounds 2 --mode train
python -m me4bench resume runs/<student>/<stamp>
```
Voice-only: pre-recorded clips (`tools/make_clips.py`), one fixed clarification reply, the clock stops at the first synthesised audio chunk of the real answer. No speaker, microphone or audio recording in benchmark mode.
Test set: 459 questions (373 without RepoProbe). Test runs are counted in `runs/test_runs.jsonl`. Results: `runs/<student>/<stamp>/results.html`.
Judge: `--judge none|heuristic|anthropic|openai_compat` (`ANTHROPIC_API_KEY` for the default).

Docs: `docs/SCHEMA.md` (contracts), `docs/PROJECT_HOOKS.md`. Tests: `python -m unittest discover -s tests`.
