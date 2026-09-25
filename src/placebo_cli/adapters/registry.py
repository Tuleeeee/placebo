"""Adapter registry and agent detection."""

from __future__ import annotations

import shutil
import subprocess

from placebo_cli.adapters.base import AgentAdapter
from placebo_cli.adapters.claude_code import ClaudeCodeAdapter
from placebo_cli.adapters.codex import CodexAdapter
from placebo_cli.adapters.fake import FakeAdapter

ADAPTERS: dict[str, type[AgentAdapter]] = {
    "claude-code": ClaudeCodeAdapter,
    "codex": CodexAdapter,
    "fake": FakeAdapter,
}

ALIASES = {"claude": "claude-code", "cc": "claude-code", "openai-codex": "codex"}

# Agents we can detect but cannot drive yet (adapters welcome!).
DETECT_ONLY = {
    "gemini-cli": ("Gemini CLI", "gemini"),
    "opencode": ("OpenCode", "opencode"),
    "cursor": ("Cursor CLI", "cursor-agent"),
    "copilot": ("Copilot CLI", "copilot"),
    "pi": ("Pi", "pi"),
}


def get_adapter(agent_id: str) -> AgentAdapter:
    key = ALIASES.get(agent_id, agent_id)
    if key not in ADAPTERS:
        supported = ", ".join(k for k in ADAPTERS if k != "fake")
        raise SystemExit(f"Unknown or unsupported agent '{agent_id}'. Supported: {supported}")
    return ADAPTERS[key]()


def detect_agents() -> list[dict]:
    rows = []
    for key, cls in ADAPTERS.items():
        if key == "fake":
            continue
        a = cls()
        b = a.binary()
        rows.append({"id": key, "label": a.label, "binary": b, "version": a.version() if b else None, "adapter": True})
    for key, (label, exe) in DETECT_ONLY.items():
        b = shutil.which(exe)
        version = None
        if b:
            try:
                out = subprocess.run([b, "--version"], capture_output=True, text=True, timeout=20,
                                     encoding="utf-8", errors="replace")
                version = ((out.stdout or out.stderr).strip().splitlines() or [None])[0]
            except (OSError, subprocess.TimeoutExpired):
                version = None
        rows.append({"id": key, "label": label, "binary": b, "version": version, "adapter": False})
    return rows
