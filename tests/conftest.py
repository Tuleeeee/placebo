from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


def run(cmd: list[str], cwd: Path) -> str:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


def git(cwd: Path, *args: str) -> str:
    return run(["git", *args], cwd)


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def make_skill(root: Path, name: str, description: str, body: str = "Do the thing.\n") -> Path:
    d = root / name
    write(d / "SKILL.md", f"---\nname: {name}\ndescription: {description}\n---\n\n{body}")
    return d


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("PLACEBO_HOME", str(home))
    return home


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A tiny Python project with history: each commit adds a function + its test."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "Test")
    git(r, "config", "commit.gpgsign", "false")
    write(r / "calc.py", "def add(a, b):\n    return a + b\n")
    write(r / "tests" / "test_add.py", "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    write(r / "conftest.py", "import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "initial")
    funcs = {
        "mul": ("def mul(a, b):\n    return a * b\n", "assert mul(3, 4) == 12"),
        "sub": ("def sub(a, b):\n    return a - b\n", "assert sub(5, 3) == 2"),
        "neg": ("def neg(a):\n    return -a\n", "assert neg(4) == -4"),
    }
    for name, (src, check) in funcs.items():
        with (r / "calc.py").open("a", encoding="utf-8") as fh:
            fh.write("\n\n" + src)
        write(r / "tests" / f"test_{name}.py", f"from calc import {name}\n\ndef test_{name}():\n    {check}\n")
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", f"Add {name}()", "-m", f"Implements {name} in calc.py.")
    return r


PYTEST_CMD = f'"{sys.executable}" -m pytest -q -p no:cacheprovider {{files}}'
