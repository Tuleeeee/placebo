"""Mine tasks from git history ("golden-commit replay").

A commit becomes a task when it changes both source files and test files.
The workspace starts at the parent commit *plus the commit's new tests*, the
agent gets the commit message as its prompt, and the task passes when those
tests pass. With validation on (default), we check that the tests fail before
the change and pass after it, so every task is solvable and non-trivial.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from placebo_cli.tasks.model import Task, expand_check
from placebo_cli.util import git, run_shell

TEST_PATTERNS = [
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)__tests__/"),
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"_test\.(py|go)$"),
    re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$"),
    re.compile(r"(^|/)spec/.*_spec\.rb$"),
]
NON_SOURCE = re.compile(
    r"(\.(md|rst|txt|lock|svg|png|jpg|gif|ico)$|(^|/)(package-lock\.json|pnpm-lock\.yaml|yarn\.lock|uv\.lock|poetry\.lock|CHANGELOG[^/]*)$)",
    re.I,
)


def is_test_file(path: str) -> bool:
    return any(p.search(path) for p in TEST_PATTERNS)


@dataclass
class Candidate:
    sha: str
    parent: str
    subject: str
    body: str
    test_files: list[str]
    source_files: list[str]


def list_candidates(repo: Path, limit: int, since: str | None = None, paths: list[str] | None = None) -> list[Candidate]:
    fmt = "%H%x1f%P%x1f%s%x1f%b%x1e"
    args = ["log", "--no-merges", f"--format={fmt}", f"-n{max(limit * 8, 50)}"]
    if since:
        args.append(f"--since={since}")
    if paths:
        args += ["--", *paths]
    out = git(args, repo)
    cands = []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        parts = rec.split("\x1f")
        if len(parts) < 4:
            continue
        sha, parents, subject, body = parts[0].strip(), parts[1].strip(), parts[2], parts[3]
        if not parents or " " in parents:
            continue  # root or merge commit
        status = git(["diff", "--name-status", "--no-renames", parents, sha], repo)
        tests, sources = [], []
        for line in status.splitlines():
            bits = line.split("\t")
            if len(bits) < 2:
                continue
            st, path = bits[0], bits[-1]
            if is_test_file(path):
                if st != "D":
                    tests.append(path)
            elif not NON_SOURCE.search(path):
                sources.append(path)
        if tests and sources:
            cands.append(Candidate(sha, parents, subject.strip(), body.strip(), tests, sources))
    return cands


def detect_test_command(repo: Path, test_files: list[str]) -> str | None:
    if any(f.endswith(".py") for f in test_files):
        return "python -m pytest -q {files}"
    pkg = repo / "package.json"
    if pkg.exists() and any(re.search(r"\.[cm]?[jt]sx?$", f) for f in test_files):
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
        if "vitest" in deps:
            return "npx vitest run {files}"
        if "jest" in deps:
            return "npx jest {files}"
    if any(f.endswith("_test.go") for f in test_files):
        return "go test ./..."
    if (repo / "Cargo.toml").exists():
        return "cargo test"
    return None


def build_prompt(c: Candidate) -> str:
    body = c.body[:2000]
    files = "\n".join(f"- {f}" for f in c.test_files)
    parts = [c.subject]
    if body:
        parts.append(body)
    parts.append(
        "The following test file(s) describe the expected behavior and currently fail:\n"
        f"{files}\n\n"
        "Implement the change in the source code so these tests pass. "
        "Do not modify the test files."
    )
    return "\n\n".join(parts)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "task"


def validate(
    repo: Path, task: Task, setup: list[str], timeout_s: int, log: Callable[[str], None]
) -> tuple[bool, str]:
    """Tests must fail at parent+tests and pass at the golden commit."""
    tmp = Path(tempfile.mkdtemp(prefix="placebo-validate-"))
    wt = tmp / "wt"
    try:
        git(["worktree", "add", "--detach", str(wt), task.base], repo)
        if task.test_files:
            git(["checkout", task.golden or "HEAD", "--", *task.test_files], wt)
        for cmd in setup:
            r = run_shell(cmd, wt, timeout_s)
            if r.exit_code != 0:
                return False, f"setup failed: {cmd}"
        checks = [expand_check(c, task) for c in task.checks]
        before = [run_shell(c, wt, timeout_s) for c in checks]
        if all(r.exit_code == 0 for r in before):
            return False, "tests already pass before the change"
        git(["checkout", task.golden or "HEAD", "--", "."], wt)
        after = [run_shell(c, wt, timeout_s) for c in checks]
        if not all(r.exit_code == 0 for r in after):
            return False, "tests do not pass at the golden commit (environment/setup issue?)"
        return True, "ok"
    finally:
        try:
            git(["worktree", "remove", "--force", str(wt)], repo, check=False)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            git(["worktree", "prune"], repo, check=False)


def mine_tasks(
    repo: Path,
    max_tasks: int = 10,
    since: str | None = None,
    test_cmd: str | None = None,
    setup: list[str] | None = None,
    do_validate: bool = True,
    timeout_s: int = 600,
    paths: list[str] | None = None,
    log: Callable[[str], None] = lambda s: None,
) -> tuple[list[Task], list[tuple[str, str]]]:
    """Return (tasks, rejected[(sha, reason)])."""
    repo = Path(git(["rev-parse", "--show-toplevel"], repo).strip())
    setup = setup or []
    tasks: list[Task] = []
    rejected: list[tuple[str, str]] = []
    for c in list_candidates(repo, max_tasks, since, paths):
        if len(tasks) >= max_tasks:
            break
        cmd = test_cmd or detect_test_command(repo, c.test_files)
        if not cmd:
            rejected.append((c.sha[:10], "no test command detected (pass --test-cmd)"))
            continue
        task = Task(
            id=f"{c.sha[:8]}-{_slug(c.subject)}",
            prompt=build_prompt(c),
            base=c.parent,
            source="git",
            checks=[cmd],
            setup=list(setup),
            golden=c.sha,
            test_files=c.test_files,
            timeout_s=timeout_s,
        )
        if do_validate:
            log(f"validating {c.sha[:10]} {c.subject[:60]}")
            ok, reason = validate(repo, task, setup, timeout_s, log)
            if not ok:
                rejected.append((c.sha[:10], reason))
                continue
        tasks.append(task)
    return tasks, rejected
