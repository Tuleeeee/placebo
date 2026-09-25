"""Token estimates.

Placebo does not ship a tokenizer: each vendor uses its own. We use a
byte-based estimate (~4 UTF-8 bytes per token for English prose and code),
which is typically within ~15-25% of real tokenizers. Every number derived
from this module is labeled "~" in reports.
"""

from __future__ import annotations

import math
from pathlib import Path

from placebo_cli.skills.model import Skill
from placebo_cli.util import is_probably_text, read_text

# Rough per-skill wrapper overhead in the agent's skill listing (name, markup).
LISTING_OVERHEAD = 12


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text.encode("utf-8", errors="replace")) / 4))


def always_loaded_tokens(skill: Skill) -> int:
    """Tokens the agent pays on every request just to know the skill exists."""
    return LISTING_OVERHEAD + estimate_tokens(skill.display_name) + estimate_tokens(skill.description)


def body_tokens(skill: Skill) -> int:
    """Tokens loaded when the skill activates (SKILL.md body)."""
    return estimate_tokens(skill.body)


def resource_tokens(skill: Skill, max_files: int = 200) -> int:
    """Tokens in other text files shipped with the skill (loaded on demand)."""
    total = 0
    count = 0
    for p in skill.root.rglob("*"):
        if count >= max_files:
            break
        if not p.is_file() or p.name == "SKILL.md":
            continue
        if any(part in {".git", "node_modules", "__pycache__"} for part in p.parts):
            continue
        if not is_probably_text(p):
            continue
        total += estimate_tokens(read_text(p, limit=500_000))
        count += 1
    return total


def fmt_tokens(n: int) -> str:
    if n >= 10_000:
        return f"~{n / 1000:.1f}k"
    return f"~{n:,}"


def file_tokens(path: Path) -> int:
    return estimate_tokens(read_text(path))
