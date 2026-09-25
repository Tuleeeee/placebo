"""Claude Code adapter (`claude -p --output-format stream-json`)."""

from __future__ import annotations

import os
import re
from pathlib import Path

from placebo_cli.adapters.base import AgentAdapter, ParseState, RunOptions, Usage, Watch
from placebo_cli.skills.discovery import claude_plugin_skill_dirs, home_dir

ALLOWED_TOOLS = "Read,Edit,Write,MultiEdit,NotebookEdit,Glob,Grep,Bash,PowerShell,Skill,TodoWrite,Task"

# CLAUDE_CODE_* variables that configure auth/providers and must survive.
KEEP_ENV = {
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH", "CLAUDE_CODE_SKIP_VERTEX_AUTH", "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_API_KEY_HELPER_TTL_MS", "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "CLAUDE_CODE_SUBAGENT_MODEL",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "CLAUDE_CODE_GIT_BASH_PATH", "CLAUDE_CODE_SHELL",
}


def _version_key(name: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", name))


class ClaudeCodeAdapter(AgentAdapter):
    id = "claude-code"
    label = "Claude Code"
    project_skill_dir = ".claude/skills"
    reports_cost = True
    binary_names = ("claude",)
    binary_env = "PLACEBO_CLAUDE_BIN"

    def extra_binary_candidates(self) -> list[Path]:
        home = home_dir()
        cands = [
            home / ".local" / "bin" / ("claude.exe" if os.name == "nt" else "claude"),
            home / ".claude" / "local" / "claude",
        ]
        # Claude desktop app bundles the CLI under %APPDATA%\Claude\claude-code\<version>\
        appdata = os.environ.get("APPDATA")
        if appdata:
            base = Path(appdata) / "Claude" / "claude-code"
            if base.is_dir():
                versions = sorted((d for d in base.iterdir() if d.is_dir()), key=lambda d: _version_key(d.name), reverse=True)
                cands += [d / "claude.exe" for d in versions]
        return cands

    def user_skill_dirs(self) -> list[Path]:
        return [home_dir() / ".claude" / "skills"] + [d for d, _ in claude_plugin_skill_dirs()]

    def build_env(self, opts: RunOptions) -> dict[str, str]:
        # When placebo itself runs inside a Claude Code session, the parent's session
        # variables would couple the child run to it (or block it as "nested").
        # Keep auth/provider settings; drop session plumbing.
        env = super().build_env(opts)
        for k in list(env):
            if k in ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_EFFORT"):
                env.pop(k, None)
            elif k.startswith("CLAUDE_CODE_") and k not in KEEP_ENV and k not in opts.env:
                env.pop(k, None)
        return env

    def command(self, workdir: Path, opts: RunOptions) -> tuple[list[str], str | None]:
        b = self.binary()
        if not b:
            raise RuntimeError("Claude Code CLI not found (install it or set PLACEBO_CLAUDE_BIN)")
        argv = [
            b, "-p",
            "--output-format", "stream-json", "--verbose",
            "--no-session-persistence",
            "--permission-mode", "acceptEdits",
            "--permission-prompts", "none",
            "--allowedTools", ALLOWED_TOOLS,
        ]
        if opts.model:
            argv += ["--model", opts.model]
        if opts.budget_usd:
            argv += ["--max-budget-usd", f"{opts.budget_usd:.2f}"]
        argv += list(opts.extra_args)
        return argv, ""  # prompt goes through stdin (robust to quoting and .cmd shims)

    def parse_event(self, obj: dict, st: ParseState, watch: Watch) -> None:
        t = obj.get("type")
        if t == "system" and obj.get("subtype") == "init":
            st.model = obj.get("model") or st.model
            st.agent_version = obj.get("claude_code_version") or st.agent_version
            skills = obj.get("skills")
            names: list[str] = []
            if isinstance(skills, list):
                for s in skills:
                    if isinstance(s, str):
                        names.append(s)
                    elif isinstance(s, dict) and s.get("name"):
                        names.append(str(s["name"]))
            if names:
                st.visible_skills = names
        elif t == "assistant":
            if obj.get("is_api_error_message") and obj.get("error"):
                st.error = str(obj["error"])  # e.g. authentication_failed, rate_limit
            msg = obj.get("message") or {}
            for item in msg.get("content") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use":
                    st.tool_calls += 1
                    st.activate(watch.match(item.get("name"), item.get("input")))
                elif item.get("type") == "text" and item.get("text"):
                    st.last_text = item["text"]
        elif t == "result":
            if obj.get("total_cost_usd") is not None:
                st.cost_usd = float(obj["total_cost_usd"])
            u = obj.get("usage") or {}
            st.usage = Usage(
                input_tokens=int(u.get("input_tokens") or 0),
                output_tokens=int(u.get("output_tokens") or 0),
                cache_read_tokens=int(u.get("cache_read_input_tokens") or 0),
                cache_write_tokens=int(u.get("cache_creation_input_tokens") or 0),
            )
            if obj.get("num_turns") is not None:
                st.turns = int(obj["num_turns"])
            if isinstance(obj.get("result"), str):
                st.final_text = obj["result"]
            if obj.get("is_error"):
                reason = obj.get("terminal_reason") or obj.get("subtype") or "error"
                detail = obj.get("result") if isinstance(obj.get("result"), str) else ""
                st.error = f"{reason}: {detail}".strip(": ")[:300]
