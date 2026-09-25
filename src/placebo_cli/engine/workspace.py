"""Per-trial workspaces: an isolated git worktree with the arm applied."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

from placebo_cli.engine.arms import Arm
from placebo_cli.skills.discovery import AGENT_SKILL_DIRS
from placebo_cli.tasks.model import Task
from placebo_cli.util import git

_GIT_LOCK = threading.Lock()  # `git worktree add` is not safe to run concurrently

PROJECT_SKILL_DIRS = sorted({p[2:] for dirs in AGENT_SKILL_DIRS.values() for scope, p in dirs if scope == "project"})


def create(repo: Path, task: Task, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _GIT_LOCK:
        git(["worktree", "add", "--detach", str(dest), task.base], repo)
    if task.golden and task.test_files:
        git(["checkout", task.golden, "--", *task.test_files], dest)
    for rel, content in task.files.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def apply_arm(ws: Path, arm: Arm, project_skill_dir: str) -> None:
    # Remove the skill under test from every project-level skill dir, for every agent,
    # so the baseline never sees it by accident.
    for rel in PROJECT_SKILL_DIRS:
        for name in arm.remove_skills:
            victim = ws / rel / name
            if victim.is_dir():
                shutil.rmtree(victim)
    for src in arm.install_skills:
        dst = ws / project_skill_dir / src.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules"))
    for rel in arm.delete_files:
        p = ws / rel
        if p.is_file():
            p.unlink()
    for rel, content in arm.write_files.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def restore_tests(ws: Path, task: Task) -> None:
    """Undo any edits the agent made to the task's test files before grading."""
    if task.golden and task.test_files:
        git(["checkout", task.golden, "--", *task.test_files], ws, check=False)
    for rel, content in task.files.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def remove(repo: Path, ws: Path) -> None:
    with _GIT_LOCK:
        git(["worktree", "remove", "--force", str(ws)], repo, check=False)
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    with _GIT_LOCK:
        git(["worktree", "prune"], repo, check=False)
