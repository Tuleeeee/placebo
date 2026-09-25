"""`placebo trigger`: does the agent use the skill when it should, and only then?"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import yaml

from placebo_cli.adapters.base import AgentAdapter, RunOptions, Watch
from placebo_cli.skills.model import Skill
from placebo_cli.util import git

GEN_PROMPT = """You are helping test an AI coding-agent "skill" (a reusable instruction file).

Skill name: {name}
Skill description: {description}

Write {n} realistic, varied requests a developer might type to a coding agent that SHOULD make the
agent use this skill, and {n} realistic near-miss requests (related topic, but a different job) that
should NOT use it. Do not mention the skill's name. Do not use any tools and do not do the tasks.
Reply with JSON only, exactly in this shape:
{{"should_trigger": ["..."], "should_not_trigger": ["..."]}}
"""


@dataclass
class Probe:
    prompt: str
    expect: bool  # True = the skill should activate


@dataclass
class ProbeResult:
    prompt: str
    expect: bool
    activated: bool
    visible: bool | None
    cost_usd: float | None
    error: str | None
    trial: int


def load_probes(path: Path) -> list[Probe]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    pos = data.get("should_trigger") or []
    neg = data.get("should_not_trigger") or []
    return [Probe(str(p), True) for p in pos] + [Probe(str(p), False) for p in neg]


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def generate_probes(adapter: AgentAdapter, skill: Skill, n: int, timeout_s: float = 300) -> list[Probe]:
    tmp = Path(tempfile.mkdtemp(prefix="plc-gen-"))
    try:
        git(["init", "-q"], tmp, check=False)
        prompt = GEN_PROMPT.format(name=skill.display_name, description=skill.description or "(none)", n=n)
        res = adapter.run(prompt, tmp, RunOptions(timeout_s=timeout_s, max_tool_calls=3))
        data = _extract_json(res.final_text or "")
        if not data:
            raise RuntimeError(f"could not parse probes from the agent's reply: {(res.final_text or res.error or '')[:300]}")
        pos = [str(p) for p in data.get("should_trigger", [])][:n]
        neg = [str(p) for p in data.get("should_not_trigger", [])][:n]
        return [Probe(p, True) for p in pos] + [Probe(p, False) for p in neg]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def save_probes(probes: list[Probe], path: Path) -> None:
    doc = {
        "should_trigger": [p.prompt for p in probes if p.expect],
        "should_not_trigger": [p.prompt for p in probes if not p.expect],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")


def run_trigger(
    adapter: AgentAdapter,
    skill: Skill,
    probes: list[Probe],
    trials: int = 1,
    timeout_s: float = 180,
    max_tool_calls: int = 6,
    budget_usd: float | None = None,
    trace_dir: Path | None = None,
    on_result: Callable[[ProbeResult, int, int], None] | None = None,
) -> dict:
    results: list[ProbeResult] = []
    spent = 0.0
    total = len(probes) * trials
    name = skill.display_name
    for t in range(trials):
        for i, probe in enumerate(probes):
            if budget_usd is not None and spent >= budget_usd:
                break
            tmp = Path(tempfile.mkdtemp(prefix="plc-trg-"))
            try:
                git(["init", "-q"], tmp, check=False)
                dst = tmp / adapter.project_skill_dir / skill.root.name
                shutil.copytree(skill.root, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules"))
                trace = (trace_dir / f"probe{i}_t{t}.jsonl") if trace_dir else None
                if trace:
                    trace.parent.mkdir(parents=True, exist_ok=True)
                res = adapter.run(
                    probe.prompt, tmp,
                    RunOptions(timeout_s=timeout_s, stop_on_activation=True, max_tool_calls=max_tool_calls,
                               env={"PLACEBO_TRIAL_KEY": f"trigger-{i}-{t}"}),
                    trace_path=trace, watch=Watch([name]),
                )
                visible = None if res.visible_skills is None else any(
                    v.split(":")[-1] == name for v in res.visible_skills)
                pr = ProbeResult(probe.prompt, probe.expect, name in res.activated, visible, res.cost_usd,
                                 None if res.activated or res.stopped_early else res.error, t)
                spent += res.cost_usd or 0.0
                results.append(pr)
                if on_result:
                    on_result(pr, len(results), total)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    pos = [r for r in results if r.expect]
    neg = [r for r in results if not r.expect]
    invisible = [r for r in results if r.visible is False]
    return {
        "skill": name,
        "agent": adapter.id,
        "agent_version": adapter.version(),
        "recall": (sum(r.activated for r in pos) / len(pos)) if pos else None,
        "false_trigger_rate": (sum(r.activated for r in neg) / len(neg)) if neg else None,
        "n_should": len(pos),
        "n_should_not": len(neg),
        "not_visible": len(invisible),
        "total_cost_usd": round(spent, 4),
        "results": [asdict(r) for r in results],
    }
