"""Adapter parsing tests.

The fixtures below are synthetic: hand-written to match each agent's documented
JSON event format. When you capture a real trace, add it under tests/fixtures/
and a test that replays it.
"""

from __future__ import annotations

import json

from placebo_cli.adapters.base import ParseState, Watch
from placebo_cli.adapters.claude_code import ClaudeCodeAdapter
from placebo_cli.adapters.codex import CodexAdapter

CLAUDE_EVENTS = [
    {"type": "system", "subtype": "init", "model": "claude-x", "claude_code_version": "2.1.281",
     "skills": ["tdd-guard", "pdf"]},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Let me check the skill."},
        {"type": "tool_use", "name": "Skill", "input": {"skill": "tdd-guard"}},
    ]}},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}},
    ]}},
    {"type": "result", "subtype": "success", "is_error": False, "num_turns": 4, "total_cost_usd": 0.1234,
     "result": "All tests pass.",
     "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 400}},
]

CODEX_EVENTS = [
    {"type": "thread.started", "thread_id": "abc"},
    {"type": "item.completed", "item": {"id": "1", "type": "command_execution",
                                         "command": "cat .agents/skills/tdd-guard/SKILL.md", "exit_code": 0,
                                         "aggregated_output": "---\nname: tdd-guard"}},
    {"type": "item.completed", "item": {"id": "2", "type": "command_execution",
                                         "command": "ls", "aggregated_output": ".agents/skills/pdf/SKILL.md"}},
    {"type": "item.completed", "item": {"id": "3", "type": "agent_message", "text": "Done."}},
    {"type": "turn.completed", "usage": {"input_tokens": 5000, "cached_input_tokens": 4000, "output_tokens": 300}},
]


def replay(adapter, events, watch):
    st = ParseState()
    for e in events:
        adapter.parse_event(json.loads(json.dumps(e)), st, watch)
    return st


def test_claude_parsing():
    st = replay(ClaudeCodeAdapter(), CLAUDE_EVENTS, Watch(["tdd-guard", "pdf"]))
    assert st.activated == ["tdd-guard"]
    assert st.visible_skills == ["tdd-guard", "pdf"]
    assert st.tool_calls == 2
    assert st.cost_usd == 0.1234
    assert st.usage.total == 100 + 50 + 3000 + 400
    assert st.final_text == "All tests pass."
    assert st.agent_version == "2.1.281"


def test_codex_parsing_counts_commands_not_output():
    st = replay(CodexAdapter(), CODEX_EVENTS, Watch(["tdd-guard", "pdf"]))
    assert st.activated == ["tdd-guard"]  # `ls` output mentioning pdf is not an activation
    assert st.tool_calls == 2
    assert st.usage.input_tokens == 1000 and st.usage.cache_read_tokens == 4000
    assert st.last_text == "Done."
    assert st.cost_usd is None


def test_claude_real_trace_not_logged_in():
    """Replays real Claude Code 2.1.281 output (sanitized) from an unauthenticated run."""
    from pathlib import Path

    events = [json.loads(l) for l in (Path(__file__).parent / "fixtures" / "claude_code_2.1.281_not_logged_in.jsonl")
              .read_text(encoding="utf-8").splitlines() if l.strip()]
    st = replay(ClaudeCodeAdapter(), events, Watch(["greeting-style"]))
    assert st.agent_version == "2.1.281"
    assert st.model == "claude-haiku-4-5-20251001"
    assert "greeting-style" in st.visible_skills  # project-level skill is visible headless
    assert st.activated == []
    assert st.error and "Not logged in" in st.error
    assert st.cost_usd == 0 and st.tool_calls == 0


def test_watch_matches_paths_and_plugin_prefixes():
    w = Watch(["my-skill"])
    assert w.match("Read", {"file_path": "C:\\repo\\.claude\\skills\\my-skill\\SKILL.md"}) == ["my-skill"]
    assert w.match("Skill", {"skill": "plugin-x:my-skill"}) == ["my-skill"]
    assert w.match("Bash", {"command": "ls skills"}) == []


def test_claude_command_uses_stdin_and_budget(monkeypatch, tmp_path):
    from placebo_cli.adapters.base import RunOptions

    monkeypatch.setenv("PLACEBO_CLAUDE_BIN", "claude-bin")
    argv, stdin = ClaudeCodeAdapter().command(tmp_path, RunOptions(model="sonnet", budget_usd=0.5))
    assert argv[0] == "claude-bin" and "-p" in argv and stdin == ""
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--max-budget-usd") + 1] == "0.50"
    assert "--no-session-persistence" in argv
