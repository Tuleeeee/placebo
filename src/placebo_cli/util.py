"""Small shared helpers: subprocess handling, hashing, text IO."""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

IS_WINDOWS = os.name == "nt"

TEXT_SUFFIXES = {
    ".md", ".txt", ".py", ".sh", ".bash", ".zsh", ".ps1", ".js", ".mjs", ".cjs", ".ts",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html", ".css", ".sql", ".rb",
    ".go", ".rs", ".java", ".kt", ".swift", ".c", ".h", ".cpp", ".hpp", ".cs", ".xml",
    ".csv", ".tsv", ".env", ".bat", ".cmd", "",
}


def read_text(path: Path, limit: int = 2_000_000) -> str:
    """Read a text file as UTF-8, replacing undecodable bytes. Returns '' for huge files."""
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def is_probably_text(path: Path) -> bool:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return False
    try:
        with path.open("rb") as fh:
            chunk = fh.read(4096)
    except OSError:
        return False
    return b"\x00" not in chunk


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def hash_dir(root: Path) -> str:
    """Stable content hash of a directory tree (paths + bytes)."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            try:
                h.update(p.read_bytes())
            except OSError:
                pass
    return h.hexdigest()


def kill_tree(proc: subprocess.Popen) -> None:
    """Terminate a process and its children, best effort, cross-platform."""
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001 - best effort
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


@dataclass
class StreamResult:
    exit_code: int | None
    timed_out: bool
    stopped_early: bool
    duration_ms: int
    stderr_tail: str


def stream_process(
    cmd: list[str],
    cwd: Path,
    env: dict[str, str] | None,
    on_line: Callable[[str], bool | None],
    timeout_s: float,
    stdin_text: str | None = None,
) -> StreamResult:
    """Run `cmd`, calling `on_line` for each stdout line.

    If `on_line` returns True the process is stopped early (used by trigger tests).
    """
    start = time.monotonic()
    popen_kwargs: dict = {}
    if not IS_WINDOWS:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        **popen_kwargs,
    )
    stderr_chunks: list[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_chunks.append(line)
            if len(stderr_chunks) > 400:
                del stderr_chunks[:200]

    t_err = threading.Thread(target=_drain_stderr, daemon=True)
    t_err.start()

    if stdin_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        except OSError:
            pass

    timed_out = False
    stopped = False
    stop_flag = threading.Event()

    def _watchdog() -> None:
        nonlocal timed_out
        if not stop_flag.wait(timeout_s):
            timed_out = True
            kill_tree(proc)

    t_dog = threading.Thread(target=_watchdog, daemon=True)
    t_dog.start()

    assert proc.stdout is not None
    for line in proc.stdout:
        try:
            if on_line(line.rstrip("\r\n")):
                stopped = True
                kill_tree(proc)
                break
        except Exception:  # noqa: BLE001 - a parser bug must not hang the run
            continue
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
    stop_flag.set()
    t_err.join(timeout=2)
    duration_ms = int((time.monotonic() - start) * 1000)
    return StreamResult(
        exit_code=proc.returncode,
        timed_out=timed_out,
        stopped_early=stopped,
        duration_ms=duration_ms,
        stderr_tail="".join(stderr_chunks)[-4000:],
    )


@dataclass
class CmdResult:
    exit_code: int
    output: str
    timed_out: bool
    duration_ms: int


def run_shell(command: str, cwd: Path, timeout_s: float, env: dict[str, str] | None = None) -> CmdResult:
    """Run a shell command, capturing combined output (tail)."""
    start = time.monotonic()
    popen_kwargs: dict = {}
    if not IS_WINDOWS:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        shell=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        **popen_kwargs,
    )
    try:
        out, _ = proc.communicate(timeout=timeout_s)
        timed_out = False
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        out, _ = proc.communicate()
        timed_out = True
    return CmdResult(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        output=(out or "")[-6000:],
        timed_out=timed_out,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


def git(args: Iterable[str], cwd: Path, check: bool = True) -> str:
    res = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout


def python_exe() -> str:
    return sys.executable or "python"
