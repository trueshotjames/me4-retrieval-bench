"""The 40 slots of one round: (dataset key, stratum label) -> slots per round.

Source: build plan v6, section 7. 8 slots per dataset, 40 per round.
Stratum labels are produced by me4bench/datasets/*.py and must match exactly.
"""
DATASETS = ["frames", "multihop", "mmlb", "repoprobe", "mimotable"]
TITLES = {"frames": "FRAMES", "multihop": "MultiHop-RAG", "mmlb": "MMLongBench-Doc",
          "repoprobe": "RepoProbe", "mimotable": "MiMoTable (English)"}
BUCKETS = ["0", "1", "2", "3-4", "5+", "n/a"]          # files needed to answer
BUCKET_TARGET = {"0": 3.0, "1": 12.3, "2": 7.5, "3-4": 7.8, "5+": 1.4, "n/a": 8.0}  # per 40

SLOTS = {
    "frames": {
        "Tabular, 2–3 articles": 1, "Tabular, 4+ articles": 1,
        "Numerical, 2–3 articles": 1, "Numerical, 4+ articles": 1,
        "Temporal (any article count)": 1,
        "Multiple constraints, 2–3 articles": 1, "Multiple constraints, 4+ articles": 1,
        "Post-processing (any article count)": 1,
    },
    "multihop": {
        "Inference query": 2, "Comparison query": 3, "Temporal query": 2,
        'Null query ("not in the files")': 1,
    },
    "mmlb": {
        "Evidence only on image-only pages": 1, "Several pages (text layer)": 1,
        "Table (one page)": 1, "Chart (one page)": 1, "Figure or image (one page)": 1,
        "Layout text: footnotes, headers, lists": 1,
        "Unanswerable, image-heavy document": 1, "Unanswerable, text document": 1,
    },
    "repoprobe": {
        "Names a source file": 1, "Config question (yaml, json, toml)": 1,
        "Error or log text in the question": 1, "Mentions README or docs": 1,
        "Project architecture": 1, "Business logic": 1, "Implementation details": 2,
    },
    "mimotable": {
        "One sheet, simple header": 2, "One sheet, complicated header (merged cells)": 1,
        "Several sheets, simple header": 1, "Several sheets, complicated header": 1,
        "Several tables on one sheet": 1, "Several files, complicated header": 1,
        "Several files, simple header": 1,
    },
}
NO_SLOT = "(no slot) plain text"   # MMLB plain-text questions: training only
assert all(sum(v.values()) == 8 for v in SLOTS.values())
SLOTS_PER_ROUND = 40
