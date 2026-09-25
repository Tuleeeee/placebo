"""A deterministic fake agent for testing Placebo itself. Never use it for real measurements.

It mimics Claude Code's stream-json output and reads skills from `.claude/skills`.
Behavior is driven by directives so tests can create known effects:

* Prompt line   ``FAKE_SOLVE: write <path> <<< <content>``
    applied with probability ``PLACEBO_FAKE_BASE_RATE`` (default 1.0).
* Skill line    ``FAKE: activate-if <keyword>``
    the skill only activates when the prompt contains the keyword.
* Skill line    ``FAKE: write <path> <<< <content>``
    applied when the skill activates (``\\n`` in content becomes a newline).
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys
from pathlib import Path

from placebo_cli.adapters.base import AgentAdapter, ParseState, RunOptions, Usage, Watch
from placebo_cli.util import python_exe


class FakeAdapter(AgentAdapter):
    id = "fake"
    label = "Fake agent (tests only)"
    project_skill_dir = ".claude/skills"
    reports_cost = True

    def binary(self) -> str | None:
        return python_exe()

    def version(self) -> str | None:
        return "fake-1.0"

    def command(self, workdir: Path, opts: RunOptions) -> tuple[list[str], str | None]:
        return [python_exe(), "-m", "placebo_cli.adapters.fake"], ""

    def parse_event(self, obj: dict, st: ParseState, watch: Watch) -> None:
        t = obj.get("type")
        if t == "system":
            st.model = obj.get("model")
            st.visible_skills = obj.get("skills")
        elif t == "assistant":
            for item in (obj.get("message") or {}).get("content") or []:
                if item.get("type") == "tool_use":
                    st.tool_calls += 1
                    st.activate(watch.match(item.get("name"), item.get("input")))
        elif t == "result":
            u = obj.get("usage") or {}
            st.usage = Usage(int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0)))
            st.cost_usd = obj.get("total_cost_usd")
            st.turns = obj.get("num_turns")
            st.final_text = obj.get("result") or ""
            if obj.get("is_error"):
                st.error = f"{obj.get('terminal_reason')}: {obj.get('result')}"


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _write(cwd: Path, spec: str) -> None:
    path, _, content = spec.partition("<<<")
    target = cwd / path.strip()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.strip().replace("\\n", "\n") + "\n", encoding="utf-8")


def main() -> int:
    prompt = sys.stdin.read()
    cwd = Path.cwd()
    if os.environ.get("PLACEBO_FAKE_FAIL"):
        # Mimics Claude Code when it is not logged in.
        _emit({"type": "result", "subtype": "success", "is_error": True, "terminal_reason": "api_error",
               "result": "Not logged in", "usage": {}, "total_cost_usd": 0})
        return 1
    key = os.environ.get("PLACEBO_TRIAL_KEY", "") + prompt
    rng = random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:12], 16))
    skills_dir = cwd / ".claude" / "skills"
    skills = sorted(p for p in skills_dir.glob("*/SKILL.md")) if skills_dir.is_dir() else []
    names = [p.parent.name for p in skills]
    _emit({"type": "system", "subtype": "init", "model": "fake-1", "skills": names})
    tokens_in = 800 + len(prompt) // 4 + sum(20 for _ in skills)

    base_rate = float(os.environ.get("PLACEBO_FAKE_BASE_RATE", "1.0"))
    for line in prompt.splitlines():
        if line.startswith("FAKE_SOLVE: write ") and rng.random() < base_rate:
            _write(cwd, line[len("FAKE_SOLVE: write "):])
            _emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write", "input": {}}]}})

    for md in skills:
        text = md.read_text(encoding="utf-8")
        directives = [l[len("FAKE: "):] for l in text.splitlines() if l.startswith("FAKE: ")]
        gate = [d[len("activate-if "):].strip() for d in directives if d.startswith("activate-if ")]
        if gate and not any(g.lower() in prompt.lower() for g in gate):
            continue
        _emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Skill", "input": {"skill": md.parent.name}}]}})
        tokens_in += len(text) // 4
        for d in directives:
            if d.startswith("write "):
                _write(cwd, d[len("write "):])

    _emit({
        "type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
        "result": "done", "usage": {"input_tokens": tokens_in, "output_tokens": 150},
        "total_cost_usd": round(tokens_in * 3e-6 + 150 * 15e-6, 6),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
