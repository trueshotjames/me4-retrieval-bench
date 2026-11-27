# ME4 retrieval benchmark

**Indexing never runs on the Raspberry Pi.** Embedding and OCR/vision run on a cloud VM, a GPU cluster over SSH, or a free Kaggle notebook (`tools/remote_index.sh`). The Pi only copies the finished `index/` and answers questions. `python -m me4bench index` refuses to run on ARM boards.

Five public datasets: FRAMES, MultiHop-RAG, MMLongBench-Doc, RepoProbe, MiMoTable (English). Nothing is committed; they are downloaded from the official sources.

FRAMES articles: about 30% are stored as .docx, .html, .txt or .odt (the rest as .md) so your file readers get exercised. Which article gets which type is fixed by its title. Size effect is small (FRAMES is roughly 65 MB). Turn it off with `python tools/fetch_data.py --no-mixed-formats`. Wikipedia text is fetched live, so it can differ slightly from the version FRAMES was written against.

FRAMES articles come from the Wikipedia Action API as wikitext, 50 articles per request, one request at a time, and are converted to Markdown locally (about 52 requests for all 2,551 articles). Templates are not expanded by Wikipedia, so infoboxes become key/value lists, common templates (dates, unit conversions, lists) are rendered, and citations and other templates are dropped. The revision id of each article is saved in `data/frames/_revisions.json`. Optional: set `ME4_CONTACT` to a URL that identifies you, such as the repo URL, to add it to the User-Agent (Wikimedia throttles anonymous clients harder).

## Datasets

| Dataset | Questions used | Test | Corpus the agent searches | Formats | Scoring |
|---|---|---|---|---|---|
| FRAMES | 824 | 94 | 2,551 Wikipedia articles (about 65 MB) | .md, plus about 30% as .docx, .html, .txt, .odt | LLM judge, correct or wrong |
| MultiHop-RAG | 2,556 | 96 | 609 news articles (6.8 MB, one JSON file) | .json | Judge accuracy; yes/no decided offline; null queries must say "not found" |
| MMLongBench-Doc | 1,082 | 89 | 135 long PDFs (about 636 MB), many pages image-only | .pdf | Official rule scoring by answer format (Int, Float, Str, List, None) |
| RepoProbe | 209 of 500 (15 repos, one per language) | 86 (6 whole repos) | 15 source repositories at pinned commits, media stripped | source code, configs, docs | Weighted checklist, judged item by item (0 to 1) |
| MiMoTable (English) | 641 | 94 | 206 spreadsheets (1.7 MB) | .xlsx | LLM judge, correct or wrong; numbers accepted within 1% |

Test set: 459 questions in total (373 without RepoProbe). Everything else is training. A round has 40 slots, 8 per dataset, and each slot is a stratum below.

**What each dataset tests**
- **FRAMES:** multi-hop questions over 2 to 11 Wikipedia articles. Tests finding several articles from one question, chaining facts across them, and doing light reasoning on top (counting, dates, arithmetic). It also tests the file readers, because the same article can arrive as .md, .docx, .html, .txt or .odt. Strata: tabular (2–3 / 4+ articles), numerical (2–3 / 4+), multiple constraints (2–3 / 4+), temporal, post-processing.
- **MultiHop-RAG:** questions over a news corpus where the answer needs facts from 2 to 4 articles. Tests retrieval across similar articles (same story, different outlets and dates), comparison, time ordering, and refusing when the corpus has no answer. Strata: inference, comparison, temporal, null ("not in the files").
- **MMLongBench-Doc:** questions about long PDFs (reports, papers, guidebooks, brochures). Tests PDF parsing, OCR or vision for pages with no text layer, reading tables, charts and figures, collecting evidence across many pages, and saying "not answerable" when the PDF has no answer. Strata: evidence only on image-only pages, several pages, table, chart, figure, layout text (footnotes, headers, lists), unanswerable (image-heavy / text document). Plain single-page text questions are training only.
- **RepoProbe:** real GitHub Discussions questions about a code repository, with the maintainer's reply as reference. Tests code search over a whole repo: finding the right config key, source file, docs page or design reason, and explaining it the way a maintainer would. Strata: names a source file, config question, error or log text, mentions README or docs, project architecture, business logic, implementation details.
- **MiMoTable:** questions about real-world spreadsheets with messy layouts. Tests reading .xlsx with merged and multi-row headers, several sheets, several tables on one sheet, and several files, then doing lookups, counts, sums, averages and comparisons correctly. Strata: one sheet (simple / complicated header), several sheets (simple / complicated), several tables on one sheet, several files (simple / complicated).

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

## 1. Download (Windows, macOS, Linux, Raspberry Pi 4/5)
Python 3.9+ only, no packages, no git.
```
python tools/fetch_data.py --dry-run     # what will be downloaded, with sizes (about 1.5 GB)
python tools/fetch_data.py               # downloads everything, then builds and checks the question manifest
```
One run does it all; it is resumable, so after a network failure just run it again (failed FRAMES articles are retried too). `python -m me4bench build-manifest` alone rebuilds the manifest without fetching.
Always downloads all five. If GitHub rate-limits you, set `GITHUB_TOKEN`; for Hugging Face set `HF_TOKEN`. Behind a proxy, `HTTPS_PROXY` is honoured.
The manifest step checks every stratum against `manifest/strata_expected.json` and prints any that differ.

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
