"""Detect references from SKILL.md to files that do not exist.

Skills often contain *example* markdown (ADR templates, link placeholders such as
`[title](url)`, paths inside the user's project). To keep false positives low we
only check targets that plausibly point at the skill's own files, and we ignore
everything inside fenced code blocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

from placebo_cli.skills.model import Skill

MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
RESOURCE_DIRS = ("scripts", "references", "reference", "assets", "templates", "resources")
# Only folders that conventionally hold a skill's *own* resources; paths like
# `src/...` or `examples/...` usually point into the user's project instead.
CODE_PATH_RE = re.compile(r"`((?:\./)?(?:" + "|".join(RESOURCE_DIRS) + r")/[\w.\-/]+)`")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")


@dataclass
class BrokenRef:
    skill: str
    path: str
    target: str


def _is_local(target: str) -> bool:
    t = target.strip()
    if not t or t.startswith(("#", "http://", "https://", "mailto:", "data:", "/", "~", "$", "{", "<")):
        return False
    if "://" in t or "${" in t or "{{" in t:
        return False
    return True


def _outside_fences(text: str) -> str:
    keep, in_fence = [], False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            keep.append(line)
    return "\n".join(keep)


def _plausibly_skill_file(skill: Skill, rel: str) -> bool:
    first = rel.split("/", 1)[0]
    if "/" not in rel.rstrip("/"):
        return bool(EXT_RE.search(rel))  # sibling file like `forms.md`; bare words are placeholders
    return first in RESOURCE_DIRS or (skill.root / first).exists()


def _exists_near(skill: Skill, rel: str, levels: int = 4) -> bool:
    """True if `rel` resolves from the skill folder or from a parent folder.

    Plugin skills often reference files relative to the plugin/repo root
    (e.g. `${CLAUDE_PLUGIN_ROOT}/scripts/...`); those are not broken.
    """
    if (skill.root / rel).exists():
        return True
    for parent in list(skill.root.parents)[:levels]:
        if (parent / rel).exists():
            return True
    return False


def broken_refs(skill: Skill) -> list[BrokenRef]:
    text = _outside_fences(skill.body)
    links: set[str] = set()
    code_paths: set[str] = set()
    for m in MD_LINK_RE.finditer(text):
        t = m.group(1)
        if _is_local(t):
            links.add(unquote(t.split("#", 1)[0]))
    for m in CODE_PATH_RE.finditer(text):
        code_paths.add(m.group(1))
    out = []
    for t in sorted(links | code_paths):
        rel = t[2:] if t.startswith("./") else t
        if not rel or any(ch in rel for ch in "*?<>|"):
            continue  # globs and placeholders are not checkable
        if rel.startswith("../") or not _plausibly_skill_file(skill, rel):
            continue
        # An inline `scripts/x.sh` mention is often just an example; only trust it when
        # the skill really ships that folder (and the specific file is missing).
        if t in code_paths and t not in links and not (skill.root / rel.split("/", 1)[0]).is_dir():
            continue
        if not _exists_near(skill, rel):
            out.append(BrokenRef(skill.display_name, str(skill.path), t))
    return out
