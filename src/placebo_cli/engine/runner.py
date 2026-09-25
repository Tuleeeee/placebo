"""Run an experiment: arms x tasks x trials, in randomized order, with a budget."""

from __future__ import annotations

import datetime as _dt
import json
import random
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from placebo_cli import __version__
from placebo_cli.adapters.base import AgentAdapter, RunOptions, Watch
from placebo_cli.engine import workspace
from placebo_cli.engine.arms import Arm
from placebo_cli.stats import Trial, summarize
from placebo_cli.tasks.model import Task, expand_check
from placebo_cli.util import git, run_shell

EXCLUDED_STATUSES = {"setup_error", "agent_error"}


@dataclass
class ExperimentConfig:
    repo: Path
    adapter: AgentAdapter
    tasks: list[Task]
    arms: list[Arm]
    trials: int
    run_dir: Path
    kind: str  # "skill" | "file"
    subject: str
    model: str | None = None
    timeout_s: int = 900
    budget_usd: float | None = None
    per_run_budget_usd: float | None = None
    max_runs: int | None = None
    parallel: int = 1
    seed: int = 0
    delta: float = 0.05
    keep_worktrees: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class TrialSpec:
    index: int
    task: Task
    arm: Arm
    trial: int

    @property
    def key(self) -> str:
        return f"{self.task.id}__{self.arm.id}__{self.trial}"


def plan(cfg: ExperimentConfig) -> list[TrialSpec]:
    """Round-robin by trial index; within each round, a seeded shuffle of (task, arm).

    Interleaving arms over time protects against drift (API load, silent model
    updates) that would bias an "all baselines first" schedule.
    """
    rng = random.Random(cfg.seed)
    specs: list[TrialSpec] = []
    idx = 0
    for r in range(cfg.trials):
        pairs = [(t, a) for t in cfg.tasks for a in cfg.arms]
        rng.shuffle(pairs)
        for t, a in pairs:
            specs.append(TrialSpec(idx, t, a, r))
            idx += 1
    return specs


def load_records(run_dir: Path) -> list[dict]:
    p = run_dir / "trials.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def records_to_trials(records: list[dict]) -> list[Trial]:
    trials = []
    for r in records:
        if r.get("status") in EXCLUDED_STATUSES:
            continue
        res = r.get("result") or {}
        usage = res.get("usage") or {}
        trials.append(Trial(
            task=r["task"],
            arm=r["arm"],
            passed=bool(r.get("passed")),
            tokens=usage.get("total") if usage else None,
            cost=res.get("cost_usd"),
            duration_ms=res.get("duration_ms"),
            activated=r.get("activated"),
        ))
    return trials


def _changed_files(ws: Path, skill_dir: str) -> list[str]:
    out = git(["status", "--porcelain", "--untracked-files=all"], ws, check=False)
    files = []
    for line in out.splitlines():
        path = line[3:].strip().strip('"')
        if path.startswith(skill_dir) or path.startswith(".placebo"):
            continue
        files.append(path)
    return files[:200]


def run_trial(cfg: ExperimentConfig, spec: TrialSpec, tmp_root: Path) -> dict:
    task, arm = spec.task, spec.arm
    ws = tmp_root / f"t{spec.index}"
    rec: dict = {
        "key": spec.key, "task": task.id, "arm": arm.id, "trial": spec.trial,
        "arm_hash": arm.hash, "passed": False, "status": "ok", "activated": None,
        "started": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        workspace.create(cfg.repo, task, ws)
        workspace.apply_arm(ws, arm, cfg.adapter.project_skill_dir)
        for cmd in task.setup:
            r = run_shell(cmd, ws, task.timeout_s)
            if r.exit_code != 0:
                rec.update(status="setup_error", error=f"setup failed: {cmd}", output=r.output[-2000:])
                return rec
        trace = cfg.run_dir / "traces" / f"{spec.key}.jsonl"
        trace.parent.mkdir(parents=True, exist_ok=True)
        opts = RunOptions(
            model=cfg.model,
            timeout_s=min(task.timeout_s, cfg.timeout_s) if task.timeout_s else cfg.timeout_s,
            budget_usd=cfg.per_run_budget_usd,
            env={"PLACEBO_TRIAL_KEY": spec.key},
        )
        result = cfg.adapter.run(task.prompt, ws, opts, trace_path=trace, watch=Watch(arm.watch))
        rec["result"] = result.to_dict()
        if arm.watch:
            rec["activated"] = bool(set(arm.watch) & set(result.activated))
        rec["changed_files"] = _changed_files(ws, cfg.adapter.project_skill_dir)
        if result.timed_out:
            rec["status"] = "timeout"  # counts as a failure: the agent ran out of time
        elif result.error and not result.tool_calls and not rec["changed_files"]:
            # the agent never got going (auth, network, crash): infrastructure, not performance
            rec.update(status="agent_error", error=result.error)
            return rec
        workspace.restore_tests(ws, task)
        checks = []
        passed = True
        for c in task.checks:
            cmd = expand_check(c, task)
            r = run_shell(cmd, ws, task.timeout_s)
            checks.append({"cmd": cmd, "exit_code": r.exit_code, "timed_out": r.timed_out,
                           "duration_ms": r.duration_ms, "output_tail": r.output[-1500:]})
            if r.exit_code != 0:
                passed = False
        rec["checks"] = checks
        rec["passed"] = bool(passed and task.checks)
        if not task.checks:
            rec["status"] = "no_checks"
    except Exception as exc:  # noqa: BLE001 - recorded, never crashes the whole run
        rec.update(status="setup_error", error=f"{type(exc).__name__}: {exc}")
    finally:
        rec["finished"] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        if not cfg.keep_worktrees:
            workspace.remove(cfg.repo, ws)
    return rec


def write_config(cfg: ExperimentConfig) -> None:
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "placebo_version": __version__,
        "created": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "repo": str(cfg.repo),
        "agent": cfg.adapter.id,
        "agent_version": cfg.adapter.version(),
        "model": cfg.model,
        "kind": cfg.kind,
        "subject": cfg.subject,
        "trials": cfg.trials,
        "seed": cfg.seed,
        "delta": cfg.delta,
        "budget_usd": cfg.budget_usd,
        "arms": [{"id": a.id, "label": a.label, "hash": a.hash, "watch": a.watch} for a in cfg.arms],
        "tasks": [t.to_dict() for t in cfg.tasks],
        "notes": cfg.notes,
    }
    (cfg.run_dir / "config.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def build_summary(run_dir: Path) -> dict:
    meta = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    records = load_records(run_dir)
    trials = records_to_trials(records)
    arm_ids = [a["id"] for a in meta["arms"]]
    summary = summarize(trials, arm_ids, delta=meta.get("delta", 0.05), seed=meta.get("seed", 0))
    statuses: dict[str, int] = {}
    for r in records:
        statuses[r.get("status", "?")] = statuses.get(r.get("status", "?"), 0) + 1
    costs = [((r.get("result") or {}).get("cost_usd") or 0.0) for r in records]
    models = sorted({(r.get("result") or {}).get("model") for r in records if (r.get("result") or {}).get("model")})
    versions = sorted({(r.get("result") or {}).get("agent_version") for r in records if (r.get("result") or {}).get("agent_version")})
    summary.update({
        "meta": meta,
        "statuses": statuses,
        "total_cost_usd": round(sum(costs), 4),
        "models_observed": models,
        "agent_versions_observed": versions,
        "n_records": len(records),
    })
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def run_experiment(
    cfg: ExperimentConfig,
    on_trial: Callable[[dict, int, int], None] | None = None,
) -> dict:
    write_config(cfg)
    specs = plan(cfg)
    done = {r["key"] for r in load_records(cfg.run_dir) if r.get("status") not in EXCLUDED_STATUSES}
    todo = [s for s in specs if s.key not in done]
    lock = threading.Lock()
    spent = sum(((r.get("result") or {}).get("cost_usd") or 0.0) for r in load_records(cfg.run_dir))
    started = 0
    completed = 0
    aborted: list[str] = []  # set when the agent cannot run at all (auth, missing CLI)
    tmp_root = Path(tempfile.mkdtemp(prefix="plc-"))
    out_path = cfg.run_dir / "trials.jsonl"

    def job(spec: TrialSpec) -> None:
        nonlocal spent, started, completed
        with lock:
            if aborted:
                return
            if cfg.budget_usd is not None and spent >= cfg.budget_usd:
                return
            if cfg.max_runs is not None and started >= cfg.max_runs:
                return
            started += 1
        rec = run_trial(cfg, spec, tmp_root)
        with lock:
            spent += ((rec.get("result") or {}).get("cost_usd") or 0.0)
            completed += 1
            with out_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
            recent.append(rec.get("status"))
            # Circuit breaker: if the first runs all fail before the agent does anything
            # (not logged in, bad model name, CLI crash), stop instead of repeating it.
            if len(recent) >= 2 and all(s == "agent_error" for s in recent[:2]) and completed <= 2:
                aborted.append(rec.get("error") or "agent failed to start")
            if on_trial:
                on_trial(rec, completed, len(todo))

    recent: list[str | None] = []

    try:
        if cfg.parallel <= 1:
            for s in todo:
                job(s)
        else:
            with ThreadPoolExecutor(max_workers=cfg.parallel) as pool:
                list(pool.map(job, todo))
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
        git(["worktree", "prune"], cfg.repo, check=False)
    summary = build_summary(cfg.run_dir)
    if aborted:
        summary["aborted"] = aborted[0]
    return summary


def dump_dataclass(obj) -> dict:
    return asdict(obj)
