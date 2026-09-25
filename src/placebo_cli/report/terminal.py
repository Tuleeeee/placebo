"""Terminal rendering (rich)."""

from __future__ import annotations

import math
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from placebo_cli.skills.discovery import AGENT_LABELS
from placebo_cli.static.scan import ScanReport
from placebo_cli.static.tokens import fmt_tokens

SEV_STYLE = {"critical": "bold white on red", "high": "bold red", "medium": "yellow", "low": "dim"}
VERDICT_STYLE = {
    "HELPS": "bold black on green",
    "HURTS": "bold white on red",
    "PLACEBO": "bold black on bright_magenta",
    "INCONCLUSIVE": "bold black on yellow",
    "NOT_ACTIVATED": "bold white on blue",
}
VERDICT_BLURB = {
    "HELPS": "The skill measurably improves results.",
    "HURTS": "The skill measurably makes results worse.",
    "PLACEBO": "No meaningful effect: the agent does just as well without it.",
    "INCONCLUSIVE": "Not enough evidence either way yet.",
    "NOT_ACTIVATED": "The agent rarely used the skill, so its content was never really tested.",
}


def _short(p: str, n: int = 60) -> str:
    return p if len(p) <= n else "…" + p[-(n - 1):]


def render_scan(r: ScanReport, console: Console, verbose: bool = False, top: int = 12) -> None:
    title = "installed skills" if r.mode == "installed" else f"skills under {r.root}"
    console.print(Text.assemble(("placebo scan", "bold"), (f" · {title}", "dim")))
    if not r.skills:
        console.print("\nNo skills found." + (" Looked in:" if r.locations else ""))
        if r.mode == "installed":
            console.print("[dim]Checked ~/.claude/skills, ~/.agents/skills, ~/.codex/skills, project .claude/skills, "
                          ".agents/skills and enabled Claude Code plugins.[/dim]")
        return

    if r.agents and r.mode == "installed":
        t = Table(title=None, box=None, pad_edge=False, show_edge=False)
        t.add_column("Agent", style="bold")
        t.add_column("Skills", justify="right")
        t.add_column("Always-loaded context", justify="right")
        for a in r.agents:
            t.add_row(AGENT_LABELS.get(a.agent, a.agent), str(a.skills), f"{fmt_tokens(a.always_tokens)} tokens / request")
        console.print()
        console.print(t)

    t = Table(title=f"Heaviest skills (top {min(top, len(r.skills))} of {len(r.skills)})", title_justify="left",
              box=None, show_edge=False, header_style="bold")
    t.add_column("Skill")
    t.add_column("Always", justify="right")
    t.add_column("On activation", justify="right")
    t.add_column("Resources", justify="right")
    installed = r.mode == "installed"
    if installed:
        t.add_column("Seen by", style="dim")
    t.add_column("Flags")
    for s in r.skills[:top]:
        flag = Text("")
        if s.security:
            flag = Text(s.security, style=SEV_STYLE[s.security])
        elif s.lint_errors:
            flag = Text(f"{s.lint_errors} lint error(s)", style="yellow")
        cells = [s.name, fmt_tokens(s.always_tokens), fmt_tokens(s.body_tokens), fmt_tokens(s.resource_tokens)]
        if installed:
            cells.append(", ".join(AGENT_LABELS.get(a, a) for a in s.agents))
        t.add_row(*cells, flag)
    console.print()
    console.print(t)

    if r.collisions:
        t = Table(title="Colliding skills: similar descriptions compete for the same requests", title_justify="left",
                  box=None, show_edge=False, header_style="bold")
        t.add_column("Skill A")
        t.add_column("Skill B")
        t.add_column("Similarity", justify="right")
        t.add_column("Shared terms", style="dim")
        for c in r.collisions[: (50 if verbose else 10)]:
            t.add_row(c.a, c.b, f"{c.similarity:.2f}", ", ".join(c.shared_terms))
        console.print()
        console.print(t)
        if len(r.collisions) > 10 and not verbose:
            console.print(f"[dim]  … {len(r.collisions) - 10} more pairs (use --verbose)[/dim]")

    if r.duplicates:
        console.print()
        console.print("[bold]Duplicates[/bold]")
        for d in r.duplicates:
            kind = "identical copies" if d.identical else "[yellow]same name, different content[/yellow]"
            console.print(f"  {d.name}: {len(d.paths)} {kind}")

    sec = [f for f in r.security if verbose or f.severity != "low"]
    if sec:
        t = Table(title="Security flags (heuristic: review before trusting)", title_justify="left",
                  box=None, show_edge=False, header_style="bold")
        t.add_column("Severity")
        t.add_column("Skill")
        t.add_column("Rule")
        t.add_column("Where", style="dim")
        t.add_column("Snippet", style="dim", overflow="fold")
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        for f in sorted(sec, key=lambda f: order[f.severity])[: (100 if verbose else 15)]:
            t.add_row(Text(f.severity, style=SEV_STYLE[f.severity]), f.skill, f.rule,
                      f"{_short(Path(f.file).name, 30)}:{f.line}", f.snippet)
        console.print()
        console.print(t)

    lint = [i for i in r.lint if verbose or i.level != "info"]
    if lint:
        console.print()
        console.print("[bold]Lint[/bold]")
        for i in lint[: (200 if verbose else 15)]:
            style = {"error": "red", "warn": "yellow", "info": "dim"}[i.level]
            console.print(f"  [{style}]{i.level:5}[/{style}] {i.code} {i.skill}: {i.message}")
        if len(lint) > 15 and not verbose:
            console.print(f"[dim]  … {len(lint) - 15} more (use --verbose)[/dim]")

    if r.broken_refs:
        console.print()
        console.print("[bold]Broken references[/bold]")
        for b in r.broken_refs[: (100 if verbose else 10)]:
            console.print(f"  {b.skill}: [yellow]{b.target}[/yellow] does not exist")

    n_high = sum(1 for f in r.security if f.severity in ("critical", "high"))
    parts = [f"[bold]{len(r.skills)}[/bold] skills"]
    if r.agents and r.mode == "installed":
        a = r.agents[0]
        parts.append(f"[bold]{fmt_tokens(a.always_tokens)}[/bold] tokens always loaded ({AGENT_LABELS.get(a.agent, a.agent)})")
    else:
        parts.append(f"[bold]{fmt_tokens(sum(s.always_tokens for s in r.skills))}[/bold] tokens always loaded if all installed")
    def plural(n: int, word: str) -> str:
        return f"{word}" if n == 1 else f"{word}s"

    parts.append(f"[bold]{len(r.collisions)}[/bold] colliding {plural(len(r.collisions), 'pair')}")
    parts.append(("[bold red]" if n_high else "[bold]") + f"{n_high}[/] high-severity {plural(n_high, 'flag')}")
    console.print()
    console.print(Panel(" · ".join(parts), expand=False))
    console.print("[dim]Token counts are estimates (~4 bytes/token). Next: "
                  "`placebo trigger <skill-dir>` · `placebo ab <skill-dir> --tasks git:10`[/dim]")


def _pct(x: float | None, signed: bool = False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x * 100:+.0f} pp" if signed else f"{x * 100:.0f}%"


def _money(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "–"
    return f"{x:+.3f}" if signed else f"${x:.3f}"


def render_summary(s: dict, console: Console, report_path: Path | None = None) -> None:
    meta = s["meta"]
    head = f"placebo ab · {meta['kind']}: {meta['subject']} · agent: {meta['agent']}"
    if meta.get("agent_version"):
        head += f" ({meta['agent_version']})"
    models = s.get("models_observed") or ([meta["model"]] if meta.get("model") else [])
    if models:
        head += f" · model: {', '.join(models)}"
    console.print(Text(head, style="bold"))
    console.print(f"[dim]{len(meta['tasks'])} tasks × {len(meta['arms'])} arms × {meta['trials']} trials · "
                  f"records: {s['n_records']} · statuses: {s['statuses']}[/dim]")

    t = Table(box=None, show_edge=False, header_style="bold")
    t.add_column("Arm")
    t.add_column("Pass rate", justify="right")
    t.add_column("Trials", justify="right")
    t.add_column("Tokens/run", justify="right")
    t.add_column("$/run", justify="right")
    t.add_column("Time/run", justify="right")
    t.add_column("Skill used", justify="right")
    for a in s["arms"]:
        t.add_row(
            a["arm"], _pct(a["pass_rate"]), str(a["trials"]),
            f"{a['mean_tokens']:,.0f}" if a["mean_tokens"] is not None else "–",
            _money(a["mean_cost"]),
            f"{a['mean_duration_ms'] / 1000:.0f}s" if a["mean_duration_ms"] is not None else "–",
            _pct(a["activation_rate"]) if a["activation_rate"] is not None else "–",
        )
    console.print()
    console.print(t)

    comps = s.get("comparisons") or {}
    if comps:
        t = Table(box=None, show_edge=False, header_style="bold")
        t.add_column("Comparison")
        t.add_column("Δ pass rate", justify="right")
        t.add_column("95% CI", justify="right")
        t.add_column("Δ tokens/run", justify="right")
        t.add_column("Δ $/run", justify="right")
        t.add_column("Tasks", justify="right")
        for key, c in comps.items():
            t.add_row(
                key.replace("_vs_", " − "),
                _pct(c["diff"], True),
                f"{c['ci95']['low'] * 100:+.0f} .. {c['ci95']['high'] * 100:+.0f} pp",
                f"{c['tokens_diff']:+,.0f}" if c["tokens_diff"] is not None else "–",
                _money(c["cost_diff"], True),
                str(c["n_tasks"]),
            )
        console.print()
        console.print(t)

    v = s["verdict"]
    label = v["label"]
    body = Text.assemble((f" {label} ", VERDICT_STYLE.get(label, "bold")), "  ", (VERDICT_BLURB.get(label, ""), "bold"))
    for r in v.get("reasons", []):
        body.append(f"\n{r}", style="dim")
    console.print()
    console.print(Panel(body, title="Verdict", title_align="left", expand=False))
    if s.get("total_cost_usd"):
        console.print(f"[dim]Total reported agent cost: ${s['total_cost_usd']:.2f}[/dim]")
    if report_path:
        console.print(f"[dim]Report: {report_path}[/dim]")
