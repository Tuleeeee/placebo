"""Detect references from SKILL.md to files that do not exist."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

from placebo_cli.skills.model import Skill

MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# Only folders that conventionally hold a skill's *own* resources; paths like
# `src/...` or `examples/...` usually point into the user's project instead.
CODE_PATH_RE = re.compile(r"`((?:\./)?(?:scripts|references|reference|assets|templates|resources)/[\w.\-/]+)`")


@dataclass
class BrokenRef:
    skill: str
    path: str
    target: str


def _is_local(target: str) -> bool:
    t = target.strip()
    if not t or t.startswith(("#", "http://", "https://", "mailto:", "data:", "/", "~", "$", "{")):
        return False
    if "://" in t or "${" in t or "{{" in t:
        return False
    return True


def broken_refs(skill: Skill) -> list[BrokenRef]:
    targets: set[str] = set()
    for m in MD_LINK_RE.finditer(skill.body):
        t = m.group(1)
        if _is_local(t):
            targets.add(unquote(t.split("#", 1)[0]))
    for m in CODE_PATH_RE.finditer(skill.body):
        targets.add(m.group(1))
    out = []
    for t in sorted(targets):
        if not t:
            continue
        rel = t[2:] if t.startswith("./") else t
        # glob-ish or placeholder references are not checkable
        if any(ch in rel for ch in "*?<>|"):
            continue
        if not (skill.root / rel).exists():
            out.append(BrokenRef(skill.display_name, str(skill.path), t))
    return out
