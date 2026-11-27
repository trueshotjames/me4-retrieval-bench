"""results.html: summary, per-dataset and per-stratum bars, latency, files-needed accuracy. Inline SVG, no libraries."""
from __future__ import annotations

import html
import json
from pathlib import Path

from .slots import TITLES

TARGET_P50 = 3.0  # seconds, display only


def _pct(x):
    return "–" if x is None else f"{100 * x:.1f}%"


def _s(x):
    return "–" if x is None else f"{x:.2f}s"


def _bar(label, v, n=None, w=360):
    v0 = 0 if v is None else max(0, min(1, v))
    return (f'<div class="b"><span class="l">{html.escape(str(label))}</span>'
            f'<svg width="{w}" height="14" role="img" aria-label="{html.escape(str(label))} {_pct(v)}">'
            f'<rect width="{w}" height="14" fill="var(--bg2)"/><rect width="{w * v0:.0f}" height="14" fill="var(--bar)"/></svg>'
            f'<span class="v">{_pct(v)}{f" · n={n}" if n is not None else ""}</span></div>')


def render(data: dict) -> str:
    cfg, summ = data["config"], data["summary"]
    ov, dss = summ["overall"], summ["datasets"]
    test = cfg.get("mode") == "test"
    h = [f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>ME4 benchmark results</title><style>
:root{{--bg:#fff;--fg:#1a1a1a;--bg2:#e8e8ee;--bar:#3b6fd4;--mut:#666}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16181d;--fg:#eee;--bg2:#2a2d36;--bar:#6c9bff;--mut:#9aa}}}}
body{{background:var(--bg);color:var(--fg);font:15px system-ui;margin:0 auto;max-width:900px;padding:16px}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:4px 8px;border-bottom:1px solid var(--bg2);text-align:left}}
.b{{display:flex;gap:8px;align-items:center;margin:2px 0;flex-wrap:wrap}}.l{{width:260px;font-size:13px}}.v{{font-size:13px;color:var(--mut)}}
.k{{font-size:28px;font-weight:600}}.note{{color:var(--mut);font-size:13px}}</style>
<h1>ME4 benchmark results</h1>
<p class=note>{html.escape(cfg.get('student',''))} · mode {cfg.get('mode')} · {cfg.get('rounds')} round(s) · seed {cfg.get('seed')} ·
scope {cfg.get('scope')} · judge {html.escape(str(cfg.get('judge')))}{' · excluded: ' + ', '.join(cfg['exclude']) if cfg.get('exclude') else ''}
{f" · test run #{cfg['test_run_number']}" if cfg.get('test_run_number') else ''}</p>
<p><span class=k>{_pct(ov.get('score'))}</span> overall score (mean of dataset scores, stratum-weighted) ·
<b>{ov.get('n', '?')}</b> questions · elapsed {summ.get('elapsed_s')}s</p>"""]
    if any(b.get("needs_judge") for b in dss.values()):
        h.append("<p class=note><b>Some answers were not judged</b> (judge none). They are excluded from accuracy; re-score with a judge.</p>")
    h.append("<h2>Datasets</h2><table><tr><th>Dataset<th>n<th>Weighted<th>Raw<th>p50<th>p95<th>Failed</tr>")
    for ds, b in dss.items():
        h.append(f"<tr><td>{html.escape(TITLES.get(ds, ds))}<td>{b['n']}<td>{_pct(b.get('weighted_score'))}"
                 f"<td>{_pct(b.get('accuracy'))} ±{100 * (b.get('margin_95') or 0):.0f}<td>{_s(b.get('p50_s'))}"
                 f"<td>{_s(b.get('p95_s'))}<td>{b.get('failed')}</tr>")
    h.append("</table>")
    for ds, b in dss.items():
        h.append(f"<h3>{html.escape(TITLES.get(ds, ds))}</h3>")
        for st, v in sorted(b.get("strata", {}).items()):
            h.append(_bar(st, v["mean"], v["n"]))
    h.append("<h2>Accuracy by files needed</h2>")
    agg = {}
    for b in dss.values():
        for k, v in b.get("bucket_accuracy", {}).items():
            a = agg.setdefault(k, [0, 0.0])
            a[0] += v["n"]
            a[1] += (v["mean"] or 0) * v["n"]
    for k in ["0", "1", "2", "3-4", "5+", "n/a"]:
        if k in agg:
            h.append(_bar(f"{k} file(s)", agg[k][1] / agg[k][0], agg[k][0]))
    h.append("<h2>Behaviour</h2><table><tr><th>Dataset<th>Correct 'not found'<th>Confident wrong<th>False 'not found'<th>Clarified</tr>")
    for ds, b in dss.items():
        h.append(f"<tr><td>{html.escape(ds)}<td>{_pct(b.get('not_found_correct_rate'))}<td>{_pct(b.get('confident_wrong_rate'))}"
                 f"<td>{_pct(b.get('false_not_found_rate'))}<td>{_pct(b.get('clarification_rate'))}</tr>")
    h.append("</table>")
    h.append(_layers_html(summ.get("layers")))
    m = summ.get("machine") or {}
    h.append(f"<p class=note>Machine: max temp {m.get('max_temp_c')} °C, max RAM {m.get('max_ram_mb')} MB. "
             f"Latency = from question start to first synthesised audio of the real answer (clarifications and plans do not stop the clock)."
             f"{' Gold answers are hidden in test mode.' if test else ''}</p>")
    return "\n".join(h)


def _f(x, pct=True):
    return "–" if x is None else (f"{100 * x:.1f}%" if pct else f"{x:.3f}")


def _layers_html(lay) -> str:
    """Debug layers (me4bench/layers.py). Separate from the official scores above."""
    if not lay:
        return ""
    h = ["<h2>Debug layers</h2><p class=note>For finding where answers go wrong. The official scores above are unchanged. "
         "Layer 1 needs a ranked <code>retrieved</code> list in the answer event (otherwise files opened or citations stand in). "
         "Layer 2 needs a <code>--scope given</code> run (and <code>--scope decoy</code> for the refusal check). "
         "Per-question tags: <code>python -m me4bench layers &lt;this run&gt; --oracle &lt;given run&gt;</code>.</p>",
         "<h3>Layer 1: retrieval</h3><table><tr><th>Dataset<th>n<th>Recall@5<th>Recall@10<th>nDCG@10<th>MRR<th>Pass<th>From<th>Hit level</tr>"]
    rows = list(lay["datasets"].items()) + [("all", lay["overall"])]
    for ds, b in rows:
        l1 = b["l1"]
        src = ", ".join(f"{k} {v}" for k, v in l1["sources"].items()) or "–"
        if l1["n_without_list"]:
            src += f" · {l1['n_without_list']} without a list"
        lev = ", ".join(f"{k} {v}" for k, v in l1["levels"].items()) or "–"
        h.append(f"<tr><td>{html.escape(ds)}<td>{l1['n']}<td>{_f(l1['recall@5'])}<td>{_f(l1['recall@10'])}"
                 f"<td>{_f(l1['ndcg@10'], False)}<td>{_f(l1['mrr'], False)}<td>{_f(l1['pass_rate'])}"
                 f"<td>{html.escape(src)}<td>{html.escape(lev)}</tr>")
    h.append("</table>")
    if any("l2" in b for _, b in rows):
        h.append("<h3>Layer 2: generation</h3><table><tr><th>Dataset<th>n<th>"
                 + ("Accuracy with gold documents" if lay.get("scope") == "given" else "Refusal rate (decoy documents)") + "</tr>")
        for ds, b in rows:
            l2 = b.get("l2") or {}
            h.append(f"<tr><td>{html.escape(ds)}<td>{l2.get('n')}<td>{_f(l2.get('oracle_accuracy', l2.get('refusal_rate')))}</tr>")
        h.append("</table>")
    h.append("<h3>Layer 3: end to end</h3><table><tr><th>Dataset<th>n<th>Correct<th>Acceptable<th>Missing"
             "<th>Hallucination<th>Truthfulness<th>Evidence covered<th>Fully covered</tr>")
    for ds, b in rows:
        l3 = b["l3"]
        tr = l3["truthfulness"]
        h.append(f"<tr><td>{html.escape(ds)}<td>{l3['n']}<td>{_f(l3['accuracy'])}<td>{_f(l3['acceptable_rate'])}"
                 f"<td>{_f(l3['missing_rate'])}<td>{_f(l3['hallucination_rate'])}<td>{'–' if tr is None else f'{tr:+.3f}'}"
                 f"<td>{_f(l3['evidence_sufficiency'])}<td>{_f(l3['evidence_full_rate'])} (n={l3['n_evidence']})</tr>")
    h.append("</table><p class=note>Truthfulness (CRAG): +1 correct, +0.5 acceptable (score of 0.5 or more but not correct), "
             "0 missing (\"not found\" or no answer), −1 wrong. Evidence covered: share of gold files (MMLongBench-Doc: gold pages) the citations cover.</p>")
    return "\n".join(h)


def write_layers(run_dir: Path, data: dict) -> Path:
    """layers.html: per-question tags joined from an end-to-end run and an oracle run."""
    from .layers import TAGS
    s = data["summary"]
    h = [f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>ME4 layer tags</title><style>
:root{{--bg:#fff;--fg:#1a1a1a;--bg2:#e8e8ee;--mut:#666}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16181d;--fg:#eee;--bg2:#2a2d36;--mut:#9aa}}}}
body{{background:var(--bg);color:var(--fg);font:15px system-ui;margin:0 auto;max-width:900px;padding:16px}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:4px 8px;border-bottom:1px solid var(--bg2);text-align:left}}
.note{{color:var(--mut);font-size:13px}}</style><h1>Where answers go wrong</h1>
<p class=note>End-to-end run {html.escape(str(data.get('e2e_run')))} · oracle run {html.escape(str(data.get('oracle_run') or 'none'))}.
Retrieval miss: gold evidence not in the top 10. Generation miss: found (or handed over) but still wrong with the gold documents.
Integration miss: right with the gold documents and found, but wrong end to end.</p>
<table><tr><th>Dataset""" + "".join(f"<th>{t}" for t in TAGS) + "</tr>"]
    for ds, c in s.items():
        h.append(f"<tr><td>{html.escape(ds)}" + "".join(f"<td>{c.get(t, 0)}" for t in TAGS) + "</tr>")
    h.append("</table><h2>Questions</h2><table><tr><th>Question<th>Stratum<th>L1<th>L2<th>L3<th>Tag</tr>")
    yn = lambda v: "–" if v is None else ("pass" if v else "fail")  # noqa: E731
    for q in data["questions"]:
        if q["tag"] != "pass":
            h.append(f"<tr><td>{html.escape(str(q['key'] or q['id']))}<td>{html.escape(str(q['stratum']))}<td>{yn(q['l1_pass'])}"
                     f"<td>{yn(q['l2_pass'])}<td>{yn(q['l3_pass'])}<td>{q['tag']}</tr>")
    h.append("</table>")
    p = Path(run_dir) / "layers.html"
    p.write_text("\n".join(h), encoding="utf-8")
    return p


def write(run_dir: Path) -> Path:
    run_dir = Path(run_dir)
    data = json.loads((run_dir / "results.json").read_text())
    p = run_dir / "results.html"
    p.write_text(render(data), encoding="utf-8")
    return p
