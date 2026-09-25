"""Tasks: a prompt, a starting commit, and checks that decide pass/fail."""

from __future__ import annotations

import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml


@dataclass
class Task:
    id: str
    prompt: str
    base: str = "HEAD"  # commit the workspace starts from
    source: str = "yaml"  # "yaml" | "git"
    checks: list[str] = field(default_factory=list)  # shell commands; all must exit 0
    setup: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # written before the agent runs
    golden: str | None = None  # git tasks: the commit whose tests define success
    test_files: list[str] = field(default_factory=list)  # git tasks: restored before grading
    timeout_s: int = 900

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, [], {}, "")}


def quote_files(files: list[str]) -> str:
    import os

    if os.name == "nt":  # cmd.exe understands double quotes only
        return " ".join(f'"{f}"' if " " in f else f for f in files)
    return " ".join(shlex.quote(f) if " " in f or "'" in f else f for f in files)


def expand_check(cmd: str, task: Task) -> str:
    return cmd.replace("{files}", quote_files(task.test_files))


def load_tasks(path: Path) -> list[Task]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = data.get("tasks", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError(f"{path}: expected a list under `tasks:`")
    defaults = data.get("defaults", {}) if isinstance(data, dict) else {}
    tasks = []
    for i, raw in enumerate(items):
        if not isinstance(raw, dict) or "prompt" not in raw:
            raise ValueError(f"{path}: task #{i + 1} needs at least a `prompt`")
        merged = {**defaults, **raw}
        checks = merged.get("checks") or merged.get("check") or []
        if isinstance(checks, str):
            checks = [checks]
        setup = merged.get("setup") or []
        if isinstance(setup, str):
            setup = [setup]
        tasks.append(Task(
            id=str(merged.get("id") or f"task-{i + 1}"),
            prompt=str(merged["prompt"]),
            base=str(merged.get("base") or "HEAD"),
            source=str(merged.get("source") or "yaml"),
            checks=[str(c) for c in checks],
            setup=[str(s) for s in setup],
            files={str(k): str(v) for k, v in (merged.get("files") or {}).items()},
            golden=merged.get("golden"),
            test_files=[str(f) for f in merged.get("test_files") or []],
            timeout_s=int(merged.get("timeout_s") or 900),
        ))
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: task ids must be unique")
    return tasks


def save_tasks(tasks: list[Task], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"tasks": [t.to_dict() for t in tasks]}
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
