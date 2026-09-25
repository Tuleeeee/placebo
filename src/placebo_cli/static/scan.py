"""`placebo scan`: static analysis of installed skills (free, no API calls)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from placebo_cli.skills.discovery import Location, discover_installed, discover_path
from placebo_cli.skills.model import Skill
from placebo_cli.static.collisions import Collision, Duplicate, find_collisions, find_duplicates
from placebo_cli.static.lint import LintIssue, lint_skill
from placebo_cli.static.refs import BrokenRef, broken_refs
from placebo_cli.static.security import SecurityFlag, is_quarantined, scan_skill
from placebo_cli.static.tokens import always_loaded_tokens, body_tokens, resource_tokens


@dataclass
class SkillRow:
    name: str
    path: str
    agents: list[str]
    scopes: list[str]
    always_tokens: int
    body_tokens: int
    resource_tokens: int
    description: str
    lint_errors: int
    security: str | None  # worst severity
    quarantined: bool


@dataclass
class AgentTotal:
    agent: str
    skills: int
    always_tokens: int


@dataclass
class ScanReport:
    mode: str  # "installed" | "path"
    root: str
    skills: list[SkillRow] = field(default_factory=list)
    agents: list[AgentTotal] = field(default_factory=list)
    locations: list[dict] = field(default_factory=list)
    collisions: list[Collision] = field(default_factory=list)
    duplicates: list[Duplicate] = field(default_factory=list)
    lint: list[LintIssue] = field(default_factory=list)
    broken_refs: list[BrokenRef] = field(default_factory=list)
    security: list[SecurityFlag] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def run_scan(
    path: Path | None,
    project: Path,
    agents: list[str] | None = None,
    collision_threshold: float = 0.35,
) -> ScanReport:
    locations: list[Location] = []
    if path is not None:
        skills: list[Skill] = discover_path(path)
        report = ScanReport(mode="path", root=str(path))
    else:
        skills, locations = discover_installed(project, agents)
        report = ScanReport(mode="installed", root=str(project))

    report.locations = [
        {"agent": l.agent, "scope": l.scope, "path": str(l.path), "detail": l.detail} for l in locations
    ]
    per_agent: dict[str, list[int]] = {}
    for s in skills:
        lint = lint_skill(s)
        refs = broken_refs(s)
        sec = scan_skill(s)
        report.lint += lint
        report.broken_refs += refs
        report.security += sec
        always = always_loaded_tokens(s)
        worst = None
        if sec:
            order = {"critical": 3, "high": 2, "medium": 1, "low": 0}
            worst = max(sec, key=lambda f: order[f.severity]).severity
        report.skills.append(SkillRow(
            name=s.display_name,
            path=str(s.path),
            agents=s.agents(),
            scopes=sorted({f"{v.scope}:{v.detail}" if v.detail and v.scope == "plugin" else v.scope for v in s.visibility}),
            always_tokens=always,
            body_tokens=body_tokens(s),
            resource_tokens=resource_tokens(s),
            description=s.description,
            lint_errors=sum(1 for i in lint if i.level == "error"),
            security=worst,
            quarantined=is_quarantined(sec),
        ))
        for a in s.agents():
            per_agent.setdefault(a, []).append(always)

    report.agents = sorted(
        (AgentTotal(a, len(v), sum(v)) for a, v in per_agent.items()),
        key=lambda t: -t.always_tokens,
    )
    report.collisions = find_collisions(skills, threshold=collision_threshold)
    report.duplicates = find_duplicates(skills)
    report.skills.sort(key=lambda r: -r.always_tokens)
    return report
