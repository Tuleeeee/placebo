"""Agent adapter interface.

An adapter knows how to (1) find an agent CLI, (2) run it headless in a
directory with a prompt, and (3) turn its JSON event stream into a
`RunResult`: tokens, cost, tool calls, and which skills were activated.

Adding an agent = one subclass + a recorded trace fixture for its tests.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from placebo_cli.util import stream_process


@dataclass
class RunOptions:
    model: str | None = None
    timeout_s: float = 900
    budget_usd: float | None = None
    stop_on_activation: bool = False
    max_tool_calls: int | None = None
    extra_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class Usage:
    input_tokens: int = 0  # uncached input
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass
class RunResult:
    agent: str
    agent_version: str | None
    model: str | None
    exit_code: int | None
    timed_out: bool
    stopped_early: bool
    duration_ms: int
    cost_usd: float | None
    usage: Usage
    turns: int | None
    tool_calls: int
    activated: list[str]
    visible_skills: list[str] | None
    final_text: str
    error: str | None
    trace_path: str | None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["usage"]["total"] = self.usage.total
        return d


class Watch:
    """Detects activation of specific skills from tool calls."""

    def __init__(self, names: list[str] | None = None):
        self.names = [n for n in (names or []) if n]

    def match(self, tool_name: str | None, tool_input: Any) -> list[str]:
        if not self.names:
            return []
        hits: list[str] = []
        tn = (tool_name or "").lower()
        if tn == "skill" and isinstance(tool_input, dict):
            for key in ("skill", "command", "name"):
                val = tool_input.get(key)
                if isinstance(val, str):
                    v = val.strip().lstrip("/").split(":")[-1].strip()
                    for n in self.names:
                        if v == n:
                            hits.append(n)
        try:
            blob = json.dumps(tool_input, ensure_ascii=False).lower() if not isinstance(tool_input, str) else tool_input.lower()
        except (TypeError, ValueError):
            blob = str(tool_input).lower()
        blob = blob.replace("\\\\", "/").replace("\\", "/")
        for n in self.names:
            nl = n.lower()
            if f"{nl}/skill.md" in blob or f"skills/{nl}/" in blob:
                hits.append(n)
        return sorted(set(hits))


@dataclass
class ParseState:
    agent_version: str | None = None
    model: str | None = None
    cost_usd: float | None = None
    usage: Usage = field(default_factory=Usage)
    turns: int | None = None
    tool_calls: int = 0
    activated: list[str] = field(default_factory=list)
    visible_skills: list[str] | None = None
    last_text: str = ""
    final_text: str = ""
    error: str | None = None

    def activate(self, names: list[str]) -> None:
        for n in names:
            if n not in self.activated:
                self.activated.append(n)


class AgentAdapter(ABC):
    id: str = ""
    label: str = ""
    project_skill_dir: str = ".agents/skills"
    reports_cost: bool = False
    binary_names: tuple[str, ...] = ()
    binary_env: str = ""

    def __init__(self) -> None:
        self._version: str | None = None
        self._version_checked = False

    # --- discovery -------------------------------------------------------
    def extra_binary_candidates(self) -> list[Path]:
        return []

    def binary(self) -> str | None:
        if self.binary_env and os.environ.get(self.binary_env):
            return os.environ[self.binary_env]
        for name in self.binary_names:
            found = shutil.which(name)
            if found:
                return found
        for cand in self.extra_binary_candidates():
            if cand.is_file():
                return str(cand)
        return None

    def version(self) -> str | None:
        if self._version_checked:
            return self._version
        self._version_checked = True
        b = self.binary()
        if not b:
            return None
        try:
            out = subprocess.run([b, "--version"], capture_output=True, text=True, timeout=30,
                                 encoding="utf-8", errors="replace")
            text = (out.stdout or out.stderr).strip().splitlines()
            self._version = text[0].strip() if text else None
        except (OSError, subprocess.TimeoutExpired):
            self._version = None
        return self._version

    def user_skill_dirs(self) -> list[Path]:
        return []

    # --- running ---------------------------------------------------------
    @abstractmethod
    def command(self, workdir: Path, opts: RunOptions) -> tuple[list[str], str | None]:
        """Return (argv, stdin_text). The prompt is passed via stdin when possible."""

    def prompt_argv(self, prompt: str) -> list[str]:
        return []

    @abstractmethod
    def parse_event(self, obj: dict, st: ParseState, watch: Watch) -> None:
        ...

    def build_env(self, opts: RunOptions) -> dict[str, str]:
        env = dict(os.environ)
        env.update(opts.env)
        return env

    def run(
        self,
        prompt: str,
        workdir: Path,
        opts: RunOptions,
        trace_path: Path | None = None,
        watch: Watch | None = None,
    ) -> RunResult:
        watch = watch or Watch()
        st = ParseState()
        argv, stdin_text = self.command(workdir, opts)
        if stdin_text is None:
            argv = argv + self.prompt_argv(prompt)
        else:
            stdin_text = prompt
        fh = trace_path.open("w", encoding="utf-8") if trace_path else None

        def on_line(line: str) -> bool | None:
            if fh:
                fh.write(line + "\n")
            s = line.strip()
            if not s.startswith("{"):
                return None
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                return None
            if isinstance(obj, dict):
                self.parse_event(obj, st, watch)
            if opts.stop_on_activation and st.activated:
                return True
            if opts.max_tool_calls is not None and st.tool_calls >= opts.max_tool_calls:
                return True
            return None

        try:
            res = stream_process(argv, workdir, self.build_env(opts), on_line, opts.timeout_s, stdin_text=stdin_text)
        finally:
            if fh:
                fh.close()
        error = st.error
        if error is None and res.exit_code not in (0, None) and not res.stopped_early and not res.timed_out:
            error = f"exit code {res.exit_code}: {res.stderr_tail.strip()[-500:]}"
        if res.timed_out:
            error = f"timed out after {opts.timeout_s:.0f}s"
        return RunResult(
            agent=self.id,
            agent_version=st.agent_version or self.version(),
            model=st.model or opts.model,
            exit_code=res.exit_code,
            timed_out=res.timed_out,
            stopped_early=res.stopped_early,
            duration_ms=res.duration_ms,
            cost_usd=st.cost_usd,
            usage=st.usage,
            turns=st.turns,
            tool_calls=st.tool_calls,
            activated=list(st.activated),
            visible_skills=st.visible_skills,
            final_text=st.final_text or st.last_text,
            error=error,
            trace_path=str(trace_path) if trace_path else None,
        )
