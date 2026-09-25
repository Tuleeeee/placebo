"""Structural checks for SKILL.md files (agentskills.io conventions)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from placebo_cli.skills.model import Skill
from placebo_cli.static.tokens import body_tokens

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TRIGGER_HINT_RE = re.compile(r"\b(use (it |this )?(when|for|to|if)|when (the )?user|trigger|invoke|whenever|if the user)\b", re.I)


@dataclass
class LintIssue:
    code: str
    level: str  # "error" | "warn" | "info"
    message: str
    skill: str
    path: str


def lint_skill(skill: Skill) -> list[LintIssue]:
    out: list[LintIssue] = []
    name = skill.display_name
    path = str(skill.path)

    def add(code: str, level: str, msg: str) -> None:
        out.append(LintIssue(code, level, msg, name, path))

    if skill.parse_error:
        add("L001", "error", skill.parse_error)
        return out
    if not skill.name:
        add("L002", "error", "frontmatter has no `name`")
    else:
        if not NAME_RE.match(skill.name) or len(skill.name) > 64:
            add("L003", "warn", "`name` should be lowercase letters, digits and hyphens (max 64 chars)")
        if skill.name != skill.root.name:
            add("L004", "info", f"`name` ({skill.name}) differs from its directory name ({skill.root.name})")
    desc = skill.description
    if not desc:
        add("L005", "error", "frontmatter has no `description`: agents cannot decide when to use this skill")
    else:
        if len(desc) < 40:
            add("L006", "warn", f"description is very short ({len(desc)} chars); triggering will be unreliable")
        if len(desc) > 1024:
            add("L007", "warn", f"description is {len(desc)} chars (spec limit is 1024) and is paid on every request")
        if not TRIGGER_HINT_RE.search(desc):
            add("L008", "info", "description does not say *when* to use the skill (e.g. \"Use when...\")")
    lines = skill.body.count("\n") + 1
    if lines > 500:
        add("L009", "warn", f"SKILL.md body is {lines} lines; move details into referenced files")
    elif body_tokens(skill) > 5000:
        add("L009", "warn", "SKILL.md body is large (>5k tokens est.); move details into referenced files")
    return out
