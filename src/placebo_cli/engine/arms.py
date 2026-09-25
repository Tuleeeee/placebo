"""Experiment arms: baseline, treatment and sham.

The sham is Placebo's namesake. It keeps the skill's name and description
(so it is listed and triggers like the real one), but its body is replaced
with inert text of the same length. If the treatment beats the baseline but
not the sham, the effect comes from *any* extra context or ritual, not from
what the skill says.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from placebo_cli.skills.model import Skill
from placebo_cli.util import hash_dir, sha256_text

# Original, instruction-free prose used to fill sham skills. It is deliberately
# unrelated to software so it carries no procedural information.
INERT_TEXT = """\
Lighthouses were once tended by keepers who trimmed wicks, polished lenses and logged the weather
in careful handwriting. Many towers stood on rocky islands where supply boats arrived only when the
sea allowed, so keepers kept gardens, mended nets and read whatever books the tender brought.
The first lenses were simple mirrors; later, stacked glass prisms bent the light into a narrow beam
that could be seen far across the water. Each station had its own pattern of flashes, a signature
that sailors learned the way people recognise a familiar voice.

Tea travelled along caravan routes for centuries before it crossed oceans in fast sailing ships.
Merchants pressed leaves into bricks that survived long journeys and could even serve as money in
remote markets. In cold mountain towns the bricks were boiled with salt and butter, while coastal
ports preferred loose leaves steeped quickly in small cups. Every region kept its own customs of
serving, and guests were often judged by how gracefully they accepted a second cup.

Old maps mixed careful measurement with rumour. Coastlines that sailors had seen were drawn with
confidence, while interiors were filled with guessed rivers, invented mountains and decorative
creatures. Mapmakers copied one another, so a single mistaken island could persist for generations
until a ship finally sailed through the empty water where it was supposed to be.
"""


@dataclass
class Arm:
    id: str
    label: str
    install_skills: list[Path] = field(default_factory=list)  # skill dirs copied into the workspace
    remove_skills: list[str] = field(default_factory=list)  # skill names removed from project dirs
    write_files: dict[str, str] = field(default_factory=dict)  # relpath -> content
    delete_files: list[str] = field(default_factory=list)
    watch: list[str] = field(default_factory=list)  # skill names whose activation we track
    hash: str = ""

    def compute_hash(self) -> str:
        parts = [self.id]
        parts += [hash_dir(p) for p in self.install_skills]
        parts += sorted(self.remove_skills)
        parts += [f"{k}:{sha256_text(v)}" for k, v in sorted(self.write_files.items())]
        parts += sorted(self.delete_files)
        self.hash = sha256_text("|".join(parts))[:12]
        return self.hash


def inert_text(n_chars: int) -> str:
    if n_chars <= 0:
        return ""
    reps = n_chars // len(INERT_TEXT) + 1
    text = (INERT_TEXT + "\n") * reps
    cut = text[:n_chars]
    # end on a sentence boundary when possible
    dot = cut.rfind(".")
    return cut[: dot + 1] if dot > n_chars * 0.8 else cut


def build_sham_skill(skill: Skill, out_root: Path) -> Path:
    """Write a sham copy of `skill` (same frontmatter, inert body) and return its dir."""
    dest = out_root / skill.root.name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    fm = dict(skill.frontmatter) or {"name": skill.display_name, "description": skill.description}
    body_len = len(skill.body)
    # count referenced resources too, so the sham has comparable bulk
    body = "# Notes\n\n" + inert_text(max(body_len - 10, 200))
    doc = "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip() + "\n---\n\n" + body + "\n"
    (dest / "SKILL.md").write_text(doc, encoding="utf-8")
    return dest


def build_sham_file(content: str) -> str:
    return inert_text(max(len(content), 200)) + "\n"


def skill_arms(skill: Skill, work_dir: Path, sham: bool = True) -> list[Arm]:
    name = skill.display_name
    arms = [
        Arm("baseline", "without the skill", remove_skills=[name, skill.root.name]),
        Arm("treatment", f"with {name}", install_skills=[skill.root], remove_skills=[name, skill.root.name], watch=[name]),
    ]
    if sham:
        sham_dir = build_sham_skill(skill, work_dir / "sham")
        arms.append(Arm("sham", f"sham {name} (inert body)", install_skills=[sham_dir],
                        remove_skills=[name, skill.root.name], watch=[name]))
    for a in arms:
        a.compute_hash()
    return arms


def file_arms(target: str, variant_path: Path, current: str | None, sham: bool = True) -> list[Arm]:
    """A/B a single file (e.g. AGENTS.md or CLAUDE.md)."""
    content = variant_path.read_text(encoding="utf-8")
    arms = [
        Arm("baseline", f"{target} as committed" if current is not None else f"no {target}"),
        Arm("treatment", f"{target} from {variant_path.name}", write_files={target: content}),
    ]
    if sham:
        arms.append(Arm("sham", f"{target} with inert text of equal length", write_files={target: build_sham_file(content)}))
    for a in arms:
        a.compute_hash()
    return arms
