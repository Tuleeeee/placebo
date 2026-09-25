"""Single-file HTML report for an A/B run (no external assets, works offline)."""

from __future__ import annotations

import html
import json
import math
from pathlib import Path

from placebo_cli import __version__

VERDICT_COLORS = {
    "HELPS": ("#0f7b3f", "#e3f6ea"),
    "HURTS": ("#b42318", "#fde8e6"),
    "PLACEBO": ("#7a2e8f", "#f5e8fa"),
    "INCONCLUSIVE": ("#8a6100", "#fff4d6"),
    "NOT_ACTIVATED": ("#1f4fb4", "#e6eeff"),
}
BLURB = {
    "HELPS": "The skill measurably improves results.",
    "HURTS": "The skill measurably makes results worse.",
    "PLACEBO": "No meaningful effect: the agent does just as well without it.",
    "INCONCLUSIVE": "Not enough evidence either way yet.",
    "NOT_ACTIVATED": "The agent rarely used the skill, so its content was never really tested.",
}

CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1f;--muted:#6b6b70;--card:#fff;--line:#e6e6e3;--accent:#6b3fa0;--pos:#0f7b3f;--neg:#b42318;--band:#efe7f7}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececef;--muted:#a0a0a8;--card:#1d1d21;--line:#2e2e34;--accent:#c39bea;--pos:#5cc98b;--neg:#ff8a80;--band:#2b2338}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1040px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:32px 0 12px}h2 .muted{font-weight:400;font-size:13px}
.muted{color:var(--muted)}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.verdict{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin-top:20px}
.badge{font-weight:800;letter-spacing:.04em;padding:6px 12px;border-radius:8px;font-size:18px}
table{border-collapse:collapse;width:100%}th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600}
.grid td{text-align:center}.cell{display:inline-block;min-width:44px;padding:2px 6px;border-radius:6px;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}details{margin:6px 0}summary{cursor:pointer}pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);padding:10px;border-radius:8px;border:1px solid var(--line);max-height:320px;overflow:auto}
.kv{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px}
footer{margin-top:40px;font-size:13px}a{color:var(--accent)}
"""


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _pct(x, signed=False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x * 100:+.0f} pp" if signed else f"{x * 100:.0f}%"


def _ci_svg(comps: dict, delta: float) -> str:
    rows = [(k, c) for k, c in comps.items()]
    if not rows:
        return ""
    w, row_h, left, right = 760, 38, 190, 30
    h = row_h * len(rows) + 46
    lo = min(-0.25, *(c["ci95"]["low"] for _, c in rows)) - 0.05
    hi = max(0.25, *(c["ci95"]["high"] for _, c in rows)) + 0.05
    lo, hi = max(lo, -1.0), min(hi, 1.0)

    def x(v: float) -> float:
        return left + (v - lo) / (hi - lo) * (w - left - right)

    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Effect sizes with confidence intervals">']
    parts.append(f'<rect x="{x(-delta):.1f}" y="8" width="{x(delta) - x(-delta):.1f}" height="{h - 36}" fill="var(--band)"/>')
    parts.append(f'<line x1="{x(0):.1f}" x2="{x(0):.1f}" y1="8" y2="{h - 28}" stroke="var(--muted)" stroke-dasharray="3 3"/>')
    for tick in [t / 100 for t in range(-100, 101, 10)]:
        if lo <= tick <= hi and abs(tick * 100) % 20 == 0:
            parts.append(f'<text x="{x(tick):.1f}" y="{h - 10}" font-size="11" text-anchor="middle" fill="var(--muted)">{tick * 100:+.0f}</text>')
    parts.append(f'<text x="{w - right}" y="{h - 10}" font-size="11" text-anchor="end" fill="var(--muted)">pp</text>')
    for i, (k, c) in enumerate(rows):
        y = 26 + i * row_h
        color = "var(--pos)" if c["ci95"]["low"] > 0 else ("var(--neg)" if c["ci95"]["high"] < 0 else "var(--fg)")
        parts.append(f'<text x="8" y="{y + 5}" font-size="15" fill="var(--fg)">{_e(k.replace("_vs_", " − "))}</text>')
        parts.append(f'<line x1="{x(c["ci95"]["low"]):.1f}" x2="{x(c["ci95"]["high"]):.1f}" y1="{y}" y2="{y}" stroke="{color}" stroke-width="3" stroke-linecap="round"/>')
        parts.append(f'<circle cx="{x(c["diff"]):.1f}" cy="{y}" r="6" fill="{color}"/>')
    parts.append("</svg>")
    return "".join(parts)


def _cell(passed: int, total: int) -> str:
    if total == 0:
        return '<span class="cell muted">–</span>'
    r = passed / total
    hue = int(120 * r)
    return f'<span class="cell" style="background:hsla({hue},65%,45%,.18)">{passed}/{total}</span>'


def render_run(summary: dict, records: list[dict]) -> str:
    meta = summary["meta"]
    v = summary["verdict"]
    fg, bg = VERDICT_COLORS.get(v["label"], ("#333", "#eee"))
    arms = [a["id"] for a in meta["arms"]]
    arm_labels = {a["id"]: a["label"] for a in meta["arms"]}
    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>Placebo report: {_e(meta['subject'])}</title><style>{CSS}</style></head><body><main>"]
    out.append(f"<div class=muted>placebo {__version__} · A/B trial report</div>")
    out.append(f"<h1>{_e(meta['kind'])}: <span class=mono>{_e(meta['subject'])}</span></h1>")
    models = ", ".join(summary.get("models_observed") or []) or (meta.get("model") or "agent default")
    out.append(f"<div class=muted>agent <b>{_e(meta['agent'])}</b> {_e(meta.get('agent_version') or '')} · model {_e(models)} · "
               f"{len(meta['tasks'])} tasks × {len(arms)} arms × {meta['trials']} trials · created {_e(meta['created'])}</div>")

    reasons = "".join(f"<div class=muted>{_e(r)}</div>" for r in v.get("reasons", []))
    out.append(f"<div class='card verdict'><span class=badge style='color:{fg};background:{bg}'>{_e(v['label'])}</span>"
               f"<div><div><b>{_e(BLURB.get(v['label'], ''))}</b></div>{reasons}</div></div>")

    out.append("<h2>Arms</h2><div class='card scroll'><table><tr><th>Arm</th><th>Pass rate</th><th>Trials</th>"
               "<th>Tokens / run</th><th>$ / run</th><th>Time / run</th><th>Skill used</th></tr>")
    for a in summary["arms"]:
        tokens = "–" if a["mean_tokens"] is None else "{:,.0f}".format(a["mean_tokens"])
        cost = "–" if a["mean_cost"] is None else "${:.3f}".format(a["mean_cost"])
        dur = "–" if a["mean_duration_ms"] is None else "{:.0f}s".format(a["mean_duration_ms"] / 1000)
        used = _pct(a["activation_rate"]) if a["activation_rate"] is not None else "–"
        out.append(
            f"<tr><td><b>{_e(a['arm'])}</b> <span class=muted>{_e(arm_labels.get(a['arm'], ''))}</span></td>"
            f"<td>{_pct(a['pass_rate'])}</td><td>{a['trials']}</td>"
            f"<td>{tokens}</td><td>{cost}</td><td>{dur}</td><td>{used}</td></tr>")
    out.append("</table></div>")

    comps = summary.get("comparisons") or {}
    if comps:
        out.append(f"<h2>Effect on pass rate <span class=muted>(dot = mean paired difference, bar = 95% CI, "
                   f"shaded = ±{summary['delta'] * 100:.0f} pp equivalence band)</span></h2>")
        out.append(f"<div class=card>{_ci_svg(comps, summary['delta'])}")
        out.append("<div class=scroll><table><tr><th>Comparison</th><th>Δ pass</th><th>95% CI</th><th>90% CI</th>"
                   "<th>McNemar p</th><th>Δ tokens/run</th><th>Δ $/run</th><th>Tasks</th></tr>")
        for k, c in comps.items():
            tok = "–" if c["tokens_diff"] is None else "{:+,.0f}".format(c["tokens_diff"])
            cost = "–" if c["cost_diff"] is None else "{:+.3f}".format(c["cost_diff"])
            pval = "–" if c["mcnemar_p"] is None else "{:.2f}".format(c["mcnemar_p"])
            label = k.replace("_vs_", " − ")
            out.append(
                f"<tr><td>{_e(label)}</td><td>{_pct(c['diff'], True)}</td>"
                f"<td>{c['ci95']['low'] * 100:+.0f} .. {c['ci95']['high'] * 100:+.0f}</td>"
                f"<td>{c['ci90']['low'] * 100:+.0f} .. {c['ci90']['high'] * 100:+.0f}</td>"
                f"<td>{pval}</td><td>{tok}</td><td>{cost}</td><td>{c['n_tasks']}</td></tr>")
        out.append("</table></div></div>")

    out.append("<h2>Per task <span class=muted>(passes / trials)</span></h2><div class='card scroll'><table class=grid><tr><th>Task</th>")
    out += [f"<th>{_e(a)}</th>" for a in arms]
    out.append("</tr>")
    for task_id, cells in sorted(summary["per_task"].items()):
        out.append(f"<tr><td class=mono>{_e(task_id)}</td>")
        for a in arms:
            p, n = cells.get(a, [0, 0])
            out.append(f"<td>{_cell(p, n)}</td>")
        out.append("</tr>")
    out.append("</table></div>")

    out.append("<h2>Trials</h2><div class=card>")
    for r in sorted(records, key=lambda r: (r["task"], r["arm"], r["trial"])):
        res = r.get("result") or {}
        icon = "✅" if r.get("passed") else "❌"
        status = "" if r.get("status") == "ok" else f" · {r.get('status')}"
        act = "" if r.get("activated") is None else (" · skill used" if r["activated"] else " · skill not used")
        cost = res.get("cost_usd")
        cost_s = f" · ${cost:.3f}" if isinstance(cost, (int, float)) else ""
        head = f"{icon} {_e(r['task'])} · {_e(r['arm'])} #{r['trial'] + 1}{_e(status)}{_e(act)}{cost_s}"
        body = []
        if r.get("error"):
            body.append(f"<div><b>error</b>: {_e(r['error'])}</div>")
        if r.get("changed_files"):
            body.append(f"<div class=muted>changed: <span class=mono>{_e(', '.join(r['changed_files'][:20]))}</span></div>")
        for c in r.get("checks") or []:
            body.append(f"<div class=mono>$ {_e(c['cmd'])} → exit {c['exit_code']}</div><pre>{_e(c['output_tail'])}</pre>")
        if res.get("final_text"):
            body.append(f"<div class=muted>agent's final message</div><pre>{_e(res['final_text'][-2000:])}</pre>")
        if res.get("trace_path"):
            body.append(f"<div class=muted>trace: <span class=mono>{_e(res['trace_path'])}</span></div>")
        out.append(f"<details><summary>{head}</summary>{''.join(body)}</details>")
    out.append("</div>")

    out.append("<h2>Setup</h2><div class='card kv'>")
    for a in meta["arms"]:
        out.append(f"<div class=muted>{_e(a['id'])}</div><div>{_e(a['label'])} <span class='mono muted'>#{_e(a['hash'])}</span></div>")
    for k in ("repo", "seed", "delta", "budget_usd"):
        out.append(f"<div class=muted>{k}</div><div class=mono>{_e(meta.get(k))}</div>")
    out.append(f"<div class=muted>statuses</div><div class=mono>{_e(json.dumps(summary.get('statuses')))}</div>")
    for n in meta.get("notes") or []:
        out.append(f"<div class=muted>note</div><div>{_e(n)}</div>")
    out.append("</div>")
    out.append("<footer class=muted>Generated by <a href='https://github.com/Tuleeeee/placebo'>placebo</a>. "
               "Pass/fail comes from the task's checks; token and cost figures are as reported by the agent. "
               "See METHODOLOGY.md for how verdicts are decided.</footer></main></body></html>")
    return "".join(out)


def write_run_report(run_dir: Path, summary: dict, records: list[dict]) -> Path:
    path = run_dir / "report.html"
    path.write_text(render_run(summary, records), encoding="utf-8")
    return path
