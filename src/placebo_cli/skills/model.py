"""Parsing SKILL.md files (agentskills.io format: YAML frontmatter + markdown body)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from placebo_cli.util import read_text, sha256_text

_FM_RE = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


@dataclass
class Visibility:
    agent: str
    scope: str  # "user" | "project" | "plugin" | "path"
    detail: str = ""  # e.g. plugin id


@dataclass
class Skill:
    path: Path  # the SKILL.md file
    root: Path  # the skill directory
    raw: str
    frontmatter: dict
    body: str
    parse_error: str | None = None
    visibility: list[Visibility] = field(default_factory=list)

    @property
    def name(self) -> str:
        val = self.frontmatter.get("name")
        return str(val).strip() if val is not None else ""

    @property
    def display_name(self) -> str:
        return self.name or self.root.name

    @property
    def description(self) -> str:
        val = self.frontmatter.get("description")
        if val is None:
            return ""
        return " ".join(str(val).split())

    @property
    def content_hash(self) -> str:
        return sha256_text(self.raw)

    def agents(self) -> list[str]:
        return sorted({v.agent for v in self.visibility})


def split_frontmatter(text: str) -> tuple[dict, str, str | None]:
    """Return (frontmatter, body, error)."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text, "missing YAML frontmatter"
    raw_fm = m.group(1)
    body = text[m.end():]
    try:
        data = yaml.safe_load(raw_fm) or {}
    except yaml.YAMLError as exc:
        return {}, body, f"invalid YAML frontmatter: {str(exc).splitlines()[0]}"
    if not isinstance(data, dict):
        return {}, body, "frontmatter is not a mapping"
    return data, body, None


def parse_skill(path: Path) -> Skill:
    text = read_text(path)
    fm, body, err = split_frontmatter(text)
    return Skill(path=path, root=path.parent, raw=text, frontmatter=fm, body=body, parse_error=err)


def load_skill_dir(skill_dir: Path) -> Skill:
    """Load a skill from its directory (or directly from a SKILL.md path)."""
    p = Path(skill_dir)
    if p.is_file():
        return parse_skill(p)
    md = p / "SKILL.md"
    if not md.exists():
        # tolerate lowercase variants
        for cand in p.glob("*"):
            if cand.is_file() and cand.name.lower() == "skill.md":
                md = cand
                break
    if not md.exists():
        raise FileNotFoundError(f"No SKILL.md found in {p}")
    return parse_skill(md)
