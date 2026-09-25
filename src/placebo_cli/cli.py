"""Command-line interface."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import re
import shutil
import sys
import webbrowser
from pathlib import Path

from rich.console import Console
from rich.table import Table

from placebo_cli import __version__

console = Console(highlight=False)
err = Console(stderr=True, highlight=False)


# --------------------------------------------------------------------------- helpers
def _repo_root(path: Path) -> Path:
    from placebo_cli.util import git

    try:
        return Path(git(["rev-parse", "--show-toplevel"], path).strip())
    except RuntimeError:
        raise SystemExit(f"{path} is not inside a git repository (placebo replays tasks from git).")


def _confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        err.print("[red]Refusing to start paid agent runs without confirmation. Re-run with --yes.[/red]")
        return False
    ans = console.input(f"{question} [y/N] ").strip().lower()
    return ans in ("y", "yes")


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()[:40] or "run"


def _gitignore_hint(repo: Path) -> None:
    import subprocess

    rc = subprocess.run(["git", "check-ignore", "-q", ".placebo/x"], cwd=str(repo)).returncode
    if rc != 0:
        console.print("[dim]Tip: add `.placebo/` to your .gitignore (runs, traces and reports live there).[/dim]")


# --------------------------------------------------------------------------- scan
def cmd_scan(args: argparse.Namespace) -> int:
    from placebo_cli.report.terminal import render_scan
    from placebo_cli.static.scan import run_scan

    project = Path(args.project).resolve()
    path = Path(args.path).resolve() if args.path else None
    if path is not None and not path.exists():
        raise SystemExit(f"{path} does not exist")
    report = run_scan(path, project, agents=args.agent or None, collision_threshold=args.threshold)
    if args.json:
        data = json.dumps(report.to_dict(), indent=2, default=str)
        if args.json == "-":
            print(data)
            return 0
        Path(args.json).write_text(data, encoding="utf-8")
    render_scan(report, console, verbose=args.verbose, top=args.top)
    if args.json and args.json != "-":
        console.print(f"[dim]JSON written to {args.json}[/dim]")
    if args.fail_on:
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        if any(order[f.severity] >= order[args.fail_on] for f in report.security):
            return 2
    return 0


# --------------------------------------------------------------------------- doctor
def cmd_doctor(args: argparse.Namespace) -> int:
    from placebo_cli.adapters.registry import detect_agents
    from placebo_cli.util import git

    console.print(f"[bold]placebo {__version__}[/bold] · Python {platform.python_version()} · {platform.system()} {platform.release()}")
    gitv = shutil.which("git")
    console.print(f"git: {'[green]' + git(['--version'], Path.cwd()).strip() + '[/green]' if gitv else '[red]not found[/red]'}")
    t = Table(box=None, show_edge=False, header_style="bold")
    t.add_column("Agent")
    t.add_column("Installed")
    t.add_column("Version")
    t.add_column("placebo adapter")
    for row in detect_agents():
        t.add_row(
            row["label"],
            "[green]yes[/green]" if row["binary"] else "[dim]no[/dim]",
            row["version"] or "",
            "[green]supported[/green]" if row["adapter"] else "[dim]detect only (adapter welcome)[/dim]",
        )
    console.print()
    console.print(t)
    try:
        root = git(["rev-parse", "--show-toplevel"], Path.cwd()).strip()
        console.print(f"\nCurrent repo: {root}")
    except RuntimeError:
        console.print("\n[yellow]Current directory is not a git repository[/yellow] (needed for `placebo ab`).")
    keys = [k for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY") if os.environ.get(k)]
    console.print(f"API key env vars present: {', '.join(keys) if keys else 'none (agents may use their own login)'}")
    console.print("\n[dim]Next: `placebo scan` (free) · `placebo trigger <skill-dir>` · `placebo ab <skill-dir> --tasks git:10`[/dim]")
    return 0


# --------------------------------------------------------------------------- tasks
def _mine(repo: Path, n: int, args: argparse.Namespace):
    from placebo_cli.tasks.git_miner import mine_tasks

    with console.status("Mining tasks from git history (running tests to validate each one)…"):
        tasks, rejected = mine_tasks(
            repo,
            max_tasks=n,
            since=args.since,
            test_cmd=args.test_cmd,
            setup=args.setup or [],
            do_validate=not args.no_validate,
            timeout_s=args.task_timeout,
            log=lambda s: None,
        )
    console.print(f"Mined [bold]{len(tasks)}[/bold] task(s); skipped {len(rejected)} candidate commit(s).")
    if rejected and getattr(args, "verbose", False):
        for sha, why in rejected[:30]:
            console.print(f"  [dim]{sha}: {why}[/dim]")
    return tasks


def cmd_tasks_mine(args: argparse.Namespace) -> int:
    from placebo_cli.tasks.model import save_tasks

    repo = _repo_root(Path(args.repo).resolve())
    tasks = _mine(repo, args.max, args)
    out = Path(args.output) if args.output else repo / ".placebo" / "tasks.yaml"
    save_tasks(tasks, out)
    for t in tasks:
        console.print(f"  • {t.id}  [dim]({len(t.test_files)} test file(s))[/dim]")
    console.print(f"Saved to {out}")
    return 0 if tasks else 1


# --------------------------------------------------------------------------- trigger
def cmd_trigger(args: argparse.Namespace) -> int:
    from placebo_cli.adapters.registry import get_adapter
    from placebo_cli.engine.trigger import generate_probes, load_probes, run_trigger, save_probes
    from placebo_cli.skills.model import load_skill_dir
    from placebo_cli.static.security import is_quarantined, scan_skill

    skill = load_skill_dir(Path(args.skill).resolve())
    adapter = get_adapter(args.agent)
    if not adapter.binary():
        raise SystemExit(f"{adapter.label} CLI not found. Run `placebo doctor`.")
    flags = scan_skill(skill)
    if is_quarantined(flags) and not args.allow_flagged:
        raise SystemExit(f"Refusing to run: {skill.display_name} has high-severity security flags "
                         f"(see `placebo scan {skill.root}`). Use --allow-flagged inside a sandbox if you trust it.")
    if args.probes:
        probes = load_probes(Path(args.probes))
    else:
        if not _confirm(f"Generate {args.generate}+{args.generate} test prompts with {adapter.label}? (one short agent run)", args.yes):
            return 1
        with console.status("Generating probe prompts…"):
            probes = generate_probes(adapter, skill, args.generate)
        out = Path(args.save_probes) if args.save_probes else skill.root / "evals" / "triggers.yaml"
        try:
            save_probes(probes, out)
            console.print(f"[dim]Saved probes to {out} (edit and reuse with --probes)[/dim]")
        except OSError:
            pass
    n_runs = len(probes) * args.trials
    budget = f" (budget cap ${args.budget:.2f})" if args.budget is not None else ""
    if not _confirm(f"Run {n_runs} short {adapter.label} session(s) to test triggering{budget}?", args.yes):
        return 1

    def on_result(r, i, total):
        mark = "✓" if r.activated == r.expect else "✗"
        want = "should use" if r.expect else "should NOT use"
        got = "[green]used[/green]" if r.activated else "[dim]not used[/dim]"
        console.print(f"  {mark} [{i}/{total}] {want:14} → {got}  [dim]{r.prompt[:70]}[/dim]")

    res = run_trigger(adapter, skill, probes, trials=args.trials, timeout_s=args.timeout,
                      budget_usd=args.budget, on_result=on_result)
    rec = res["recall"]
    ftr = res["false_trigger_rate"]
    console.print()
    console.print(f"[bold]{skill.display_name}[/bold] on {adapter.label}: "
                  f"activation when it should: [bold]{'–' if rec is None else f'{rec:.0%}'}[/bold] "
                  f"({res['n_should']} prompts) · false activations: [bold]{'–' if ftr is None else f'{ftr:.0%}'}[/bold] "
                  f"({res['n_should_not']} prompts)")
    if res["not_visible"]:
        console.print(f"[yellow]The agent did not list the skill in {res['not_visible']} run(s): it may not be installed "
                      f"where {adapter.label} looks ({adapter.project_skill_dir}).[/yellow]")
    if rec is not None and rec == 0:
        console.print("[yellow]The skill never activated. Some agents do not auto-activate skills in headless mode; "
                      "if so, `placebo ab` will report NOT_ACTIVATED.[/yellow]")
    if res["total_cost_usd"]:
        console.print(f"[dim]Reported cost: ${res['total_cost_usd']:.2f}[/dim]")
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=2), encoding="utf-8")
    return 0


# --------------------------------------------------------------------------- ab
def cmd_ab(args: argparse.Namespace) -> int:
    from placebo_cli.adapters.registry import get_adapter
    from placebo_cli.engine.arms import file_arms, skill_arms
    from placebo_cli.engine.runner import ExperimentConfig, load_records, plan, run_experiment
    from placebo_cli.report.html import write_run_report
    from placebo_cli.report.terminal import render_summary
    from placebo_cli.skills.model import load_skill_dir
    from placebo_cli.static.security import is_quarantined, scan_skill
    from placebo_cli.tasks.model import load_tasks, save_tasks
    from placebo_cli.util import git

    if bool(args.skill) == bool(args.file):
        raise SystemExit("Give either a skill directory or --file TARGET=VARIANT (e.g. --file AGENTS.md=AGENTS.new.md).")
    repo = _repo_root(Path(args.repo).resolve())
    adapter = get_adapter(args.agent)
    if not adapter.binary() and not args.dry_run:
        raise SystemExit(f"{adapter.label} CLI not found. Run `placebo doctor`.")

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    notes = ["Your user-level skills, plugins and settings are identical in every arm; "
             "only the subject of the test changes between arms."]

    if args.resume:
        run_dir = Path(args.resume).resolve()
        if not (run_dir / "config.json").exists():
            raise SystemExit(f"{run_dir} is not a placebo run directory")

    if args.skill:
        skill = load_skill_dir(Path(args.skill).resolve())
        flags = scan_skill(skill)
        if is_quarantined(flags) and not args.allow_flagged:
            raise SystemExit(f"Refusing to run: {skill.display_name} has high-severity security flags "
                             f"(see `placebo scan {skill.root}`). Use --allow-flagged inside a sandbox if you trust it.")
        # A copy installed at user level would leak into the baseline arm.
        for d in adapter.user_skill_dirs():
            if (d / skill.root.name).is_dir() or (skill.name and (d / skill.name).is_dir()):
                if not args.allow_global:
                    raise SystemExit(
                        f"'{skill.display_name}' is also installed at {d}, so the baseline would still see it.\n"
                        "Temporarily move it out of that folder (or pass --allow-global to measure the "
                        "effect of a *second* copy).")
        subject, kind = skill.display_name, "skill"
    else:
        target, _, variant = args.file.partition("=")
        if not variant:
            raise SystemExit("--file expects TARGET=VARIANT, e.g. --file AGENTS.md=experiments/AGENTS.v2.md")
        variant_path = Path(variant).resolve()
        if not variant_path.is_file():
            raise SystemExit(f"{variant_path} not found")
        subject, kind = target, "file"

    run_dir = Path(args.resume).resolve() if args.resume else repo / ".placebo" / "runs" / f"{stamp}-{_slug(subject)}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # tasks
    if args.resume:
        tasks = load_tasks(run_dir / "tasks.yaml")
    elif args.tasks.startswith("git:"):
        n = int(args.tasks.split(":", 1)[1] or 10)
        tasks = _mine(repo, n, args)
        if not tasks:
            raise SystemExit("No usable tasks found in git history. Pass --test-cmd/--setup, raise the search window "
                             "with --since, or write tasks by hand (see examples/tasks.yaml).")
    else:
        tasks = load_tasks(Path(args.tasks))
    if args.max_tasks:
        tasks = tasks[: args.max_tasks]
    save_tasks(tasks, run_dir / "tasks.yaml")

    # arms
    if kind == "skill":
        arms = skill_arms(skill, run_dir, sham=not args.no_sham)
    else:
        current = None
        try:
            current = git(["show", f"HEAD:{target}"], repo)
        except RuntimeError:
            current = None
        arms = file_arms(target, variant_path, current, sham=not args.no_sham)

    cfg = ExperimentConfig(
        repo=repo, adapter=adapter, tasks=tasks, arms=arms, trials=args.trials, run_dir=run_dir,
        kind=kind, subject=subject, model=args.model, timeout_s=args.timeout, budget_usd=args.budget,
        per_run_budget_usd=args.run_budget, max_runs=args.max_runs, parallel=args.parallel, seed=args.seed,
        delta=args.delta, keep_worktrees=args.keep_worktrees, notes=notes,
    )
    specs = plan(cfg)
    done = {r["key"] for r in load_records(run_dir)}
    remaining = [s for s in specs if s.key not in done]

    console.print(f"[bold]Plan[/bold]: {len(tasks)} task(s) × {len(arms)} arm(s) × {args.trials} trial(s) = "
                  f"[bold]{len(specs)}[/bold] agent runs ({len(remaining)} to go) with {adapter.label}")
    for a in arms:
        console.print(f"  [bold]{a.id:9}[/bold] {a.label}")
    for t in tasks[:10]:
        console.print(f"  [dim]task {t.id}[/dim]")
    if len(tasks) > 10:
        console.print(f"  [dim]… {len(tasks) - 10} more[/dim]")
    if args.budget is None and adapter.reports_cost:
        console.print("[yellow]No --budget set: runs are billed to your agent account until the plan completes.[/yellow]")
    if not adapter.reports_cost:
        console.print(f"[yellow]{adapter.label} does not report dollar cost; use --max-runs to cap spending.[/yellow]")
    if args.dry_run:
        console.print(f"[dim]Dry run: nothing executed. Tasks saved to {run_dir / 'tasks.yaml'}[/dim]")
        return 0
    if not _confirm(f"Start {len(remaining)} {adapter.label} runs?", args.yes):
        return 1
    _gitignore_hint(repo)

    def on_trial(rec: dict, i: int, total: int) -> None:
        res = rec.get("result") or {}
        mark = "[green]pass[/green]" if rec.get("passed") else "[red]fail[/red]"
        if rec.get("status") not in ("ok", None):
            mark += f" [yellow]{rec['status']}[/yellow]"
        cost = res.get("cost_usd")
        extra = []
        if isinstance(cost, (int, float)):
            extra.append(f"${cost:.3f}")
        if res.get("duration_ms"):
            extra.append(f"{res['duration_ms'] / 1000:.0f}s")
        if rec.get("activated") is not None:
            extra.append("skill used" if rec["activated"] else "skill not used")
        console.print(f"  [{i}/{total}] {rec['task'][:38]:38} {rec['arm']:9} #{rec['trial'] + 1}  {mark}  [dim]{' · '.join(extra)}[/dim]")

    summary = run_experiment(cfg, on_trial=on_trial)
    if summary.get("aborted"):
        err.print(f"\n[red]Stopped: the agent failed before doing any work in the first runs:[/red] {summary['aborted']}")
        err.print(f"[dim]Check that `{adapter.label}` works on its own (logged in, model name valid), then resume with "
                  f"`placebo ab --resume {run_dir} …`.[/dim]")
        return 3
    report = write_run_report(run_dir, summary, load_records(run_dir))
    console.print()
    render_summary(summary, console, report_path=report)
    if args.open:
        webbrowser.open(report.as_uri())
    return 0


# --------------------------------------------------------------------------- report
def cmd_report(args: argparse.Namespace) -> int:
    from placebo_cli.engine.runner import build_summary, load_records
    from placebo_cli.report.html import write_run_report
    from placebo_cli.report.terminal import render_summary

    run_dir = Path(args.run_dir).resolve()
    if not (run_dir / "config.json").exists():
        raise SystemExit(f"{run_dir} is not a placebo run directory")
    summary = build_summary(run_dir)
    report = write_run_report(run_dir, summary, load_records(run_dir))
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        render_summary(summary, console, report_path=report)
    if args.open:
        webbrowser.open(report.as_uri())
    return 0


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="placebo",
        description="Is your agent skill a placebo? Controlled A/B trials for agent skills and AGENTS.md.",
    )
    p.add_argument("--version", action="version", version=f"placebo {__version__}")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("scan", help="free static analysis of installed skills (tokens, collisions, lint, security)")
    s.add_argument("path", nargs="?", help="scan every SKILL.md under this path instead of installed skills")
    s.add_argument("--project", default=".", help="project root for project-level skills (default: .)")
    s.add_argument("--agent", action="append", help="limit to an agent (repeatable): claude-code, codex, …")
    s.add_argument("--threshold", type=float, default=0.35, help="collision similarity threshold (0-1)")
    s.add_argument("--top", type=int, default=12, help="how many skills to list")
    s.add_argument("--json", metavar="FILE", help="write the full report as JSON ('-' for stdout)")
    s.add_argument("--fail-on", choices=["low", "medium", "high", "critical"], help="exit 2 if a flag this severe exists (CI)")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_scan)

    d = sub.add_parser("doctor", help="check installed agents and environment")
    d.set_defaults(func=cmd_doctor)

    t = sub.add_parser("tasks", help="work with task sets")
    tsub = t.add_subparsers(dest="tasks_command")
    tm = tsub.add_parser("mine", help="mine validated tasks from git history")
    tm.add_argument("--repo", default=".")
    tm.add_argument("--max", type=int, default=10)
    _task_source_args(tm)
    tm.add_argument("-o", "--output", help="output YAML (default .placebo/tasks.yaml)")
    tm.add_argument("-v", "--verbose", action="store_true")
    tm.set_defaults(func=cmd_tasks_mine)

    tr = sub.add_parser("trigger", help="does the agent use the skill when it should (and only then)?")
    tr.add_argument("skill", help="skill directory (containing SKILL.md)")
    tr.add_argument("--agent", default="claude-code")
    tr.add_argument("--probes", help="YAML with should_trigger / should_not_trigger prompt lists")
    tr.add_argument("--generate", type=int, default=5, help="probes per side to generate with the agent (default 5)")
    tr.add_argument("--save-probes", help="where to save generated probes (default <skill>/evals/triggers.yaml)")
    tr.add_argument("--trials", type=int, default=1)
    tr.add_argument("--timeout", type=float, default=180)
    tr.add_argument("--budget", type=float, help="stop after this many dollars (agents that report cost)")
    tr.add_argument("--allow-flagged", action="store_true", help="run even if the skill has high-severity security flags")
    tr.add_argument("--json", metavar="FILE")
    tr.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    tr.set_defaults(func=cmd_trigger)

    a = sub.add_parser("ab", help="controlled A/B trial: baseline vs skill (vs sham) on real tasks")
    a.add_argument("skill", nargs="?", help="skill directory to test")
    a.add_argument("--file", metavar="TARGET=VARIANT", help="A/B a file instead, e.g. AGENTS.md=AGENTS.v2.md")
    a.add_argument("--agent", default="claude-code")
    a.add_argument("--repo", default=".")
    a.add_argument("--tasks", default="git:10", help="'git:N' to mine N tasks from history, or a tasks YAML file")
    a.add_argument("--max-tasks", type=int)
    a.add_argument("--trials", type=int, default=3, help="trials per task and arm (default 3)")
    a.add_argument("--no-sham", action="store_true", help="skip the sham arm (cheaper, weaker conclusions)")
    a.add_argument("--model", help="model to pass to the agent")
    a.add_argument("--budget", type=float, help="stop starting new runs after this many dollars in total")
    a.add_argument("--run-budget", type=float, help="per-run dollar cap passed to the agent (if supported)")
    a.add_argument("--max-runs", type=int, help="hard cap on agent runs")
    a.add_argument("--timeout", type=int, default=900, help="per-run timeout in seconds")
    a.add_argument("--parallel", type=int, default=1)
    a.add_argument("--seed", type=int, default=0)
    a.add_argument("--delta", type=float, default=0.05, help="equivalence margin for PLACEBO (default 0.05 = 5 pp)")
    _task_source_args(a)
    a.add_argument("--allow-flagged", action="store_true", help="run even if the skill has high-severity security flags")
    a.add_argument("--allow-global", action="store_true", help="allow the skill to also be installed at user level")
    a.add_argument("--keep-worktrees", action="store_true")
    a.add_argument("--resume", metavar="RUN_DIR", help="continue an interrupted run")
    a.add_argument("--dry-run", action="store_true", help="show the plan without running agents")
    a.add_argument("--open", action="store_true", help="open the HTML report when done")
    a.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    a.add_argument("-v", "--verbose", action="store_true")
    a.set_defaults(func=cmd_ab)

    r = sub.add_parser("report", help="re-render the summary and HTML report of a run")
    r.add_argument("run_dir")
    r.add_argument("--json", action="store_true")
    r.add_argument("--open", action="store_true")
    r.set_defaults(func=cmd_report)
    return p


def _task_source_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--test-cmd", help="test command for mined tasks; {files} = the task's test files")
    sp.add_argument("--setup", action="append", help="setup command run in each fresh workspace (repeatable)")
    sp.add_argument("--since", help="only mine commits since this date (e.g. 2026-06-01)")
    sp.add_argument("--no-validate", action="store_true", help="skip fail-before/pass-after validation (faster, riskier)")
    sp.add_argument("--task-timeout", type=int, default=600, help="timeout for setup/test commands in seconds")


def main(argv: list[str] | None = None) -> int:
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        if args.command == "tasks":
            parser.parse_args(["tasks", "--help"])
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        err.print("\n[yellow]Interrupted. Resume an A/B run with `placebo ab --resume <run-dir> …`.[/yellow]")
        return 130
