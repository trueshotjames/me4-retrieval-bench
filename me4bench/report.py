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
    m = summ.get("machine") or {}
    h.append(f"<p class=note>Machine: max temp {m.get('max_temp_c')} °C, max RAM {m.get('max_ram_mb')} MB. "
             f"Latency = from question start to first synthesised audio of the real answer (clarifications and plans do not stop the clock)."
             f"{' Gold answers are hidden in test mode.' if test else ''}</p>")
    return "\n".join(h)


def write(run_dir: Path) -> Path:
    run_dir = Path(run_dir)
    data = json.loads((run_dir / "results.json").read_text())
    p = run_dir / "results.html"
    p.write_text(render(data), encoding="utf-8")
    return p
