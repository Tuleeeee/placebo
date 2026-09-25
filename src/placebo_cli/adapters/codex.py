"""OpenAI Codex CLI adapter (`codex exec --json`)."""

from __future__ import annotations

from pathlib import Path

from placebo_cli.adapters.base import AgentAdapter, ParseState, RunOptions, Watch
from placebo_cli.skills.discovery import home_dir

TOOL_ITEMS = {"command_execution", "file_change", "mcp_tool_call", "web_search", "local_shell_call"}


class CodexAdapter(AgentAdapter):
    id = "codex"
    label = "Codex"
    project_skill_dir = ".agents/skills"
    reports_cost = False  # Codex reports tokens, not dollars
    binary_names = ("codex",)
    binary_env = "PLACEBO_CODEX_BIN"

    def user_skill_dirs(self) -> list[Path]:
        return [home_dir() / ".codex" / "skills", home_dir() / ".agents" / "skills"]

    def command(self, workdir: Path, opts: RunOptions) -> tuple[list[str], str | None]:
        b = self.binary()
        if not b:
            raise RuntimeError("Codex CLI not found (install it or set PLACEBO_CODEX_BIN)")
        argv = [b, "exec", "--json", "--full-auto", "--skip-git-repo-check", "-C", str(workdir)]
        if opts.model:
            argv += ["-m", opts.model]
        argv += list(opts.extra_args)
        argv.append("-")  # read the prompt from stdin
        return argv, ""

    def parse_event(self, obj: dict, st: ParseState, watch: Watch) -> None:
        t = obj.get("type")
        if t == "item.completed":
            item = obj.get("item") or {}
            it = item.get("type") or item.get("item_type")
            if it in TOOL_ITEMS:
                st.tool_calls += 1
                # Match only what the agent *asked for*, not command output
                # (a directory listing mentioning a skill is not an activation).
                if it in ("command_execution", "local_shell_call"):
                    payload = item.get("command")
                elif it == "mcp_tool_call":
                    payload = {"tool": item.get("tool"), "arguments": item.get("arguments")}
                else:
                    payload = None
                if payload is not None:
                    st.activate(watch.match(it, payload))
            elif it in ("agent_message", "assistant_message") and isinstance(item.get("text"), str):
                st.last_text = item["text"]
        elif t == "turn.completed":
            u = obj.get("usage") or {}
            inp = int(u.get("input_tokens") or 0)
            cached = int(u.get("cached_input_tokens") or 0)
            st.usage.input_tokens += max(0, inp - cached)
            st.usage.cache_read_tokens += cached
            st.usage.output_tokens += int(u.get("output_tokens") or 0)
            st.turns = (st.turns or 0) + 1
        elif t in ("turn.failed", "error"):
            err = obj.get("error") or obj.get("message") or t
            st.error = err.get("message") if isinstance(err, dict) else str(err)
        elif t == "thread.started" and obj.get("model"):
            st.model = obj.get("model")
