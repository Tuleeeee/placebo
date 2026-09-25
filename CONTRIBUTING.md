# Contributing to placebo

Thanks for helping! This project lives or dies by trust in its measurements, so correctness and
clarity beat features.

## Development setup

```bash
git clone https://github.com/Tuleeeee/placebo && cd placebo
uv sync                      # creates .venv with dev dependencies
uv run pytest                # ~1 minute; no network, no agent accounts needed
uv run placebo scan examples/skills
```

Without uv: `python -m venv .venv && . .venv/bin/activate && pip install -e . pytest`.

The test suite uses a deterministic **fake agent** (`placebo_cli/adapters/fake.py`) that mimics
Claude Code's event stream and reads directives from prompts and skills. It exists only to test Placebo
itself. Never use it to make claims about real skills.

## Project layout

```
src/placebo_cli/
  cli.py                 argparse commands
  skills/                SKILL.md parsing and per-agent discovery (AGENT_SKILL_DIRS)
  static/                scan: tokens, lint, refs, collisions, security heuristics
  adapters/              one module per agent + registry
  tasks/                 task model and git-history miner
  engine/                arms (incl. sham), worktrees, runner, trigger tests
  stats.py               paired bootstrap, TOST, verdict rules
  report/                terminal (rich) and single-file HTML
tests/                   pytest; fixtures/ holds recorded agent traces
```

## Adding an agent adapter

1. Create `src/placebo_cli/adapters/<agent>.py` with a subclass of `AgentAdapter`:
   - `binary_names` / `binary_env` for discovery
   - `project_skill_dir`: where the agent reads project-level skills
   - `command()`: headless argv; return `""` as stdin text to receive the prompt on stdin (preferred)
   - `parse_event()`: update `ParseState` from one JSON event (tool calls, activation via
     `watch.match(...)`, tokens, cost, final text, errors)
   - `user_skill_dirs()`: used to warn when the skill under test is also installed globally
2. Register it in `adapters/registry.py` and add its skill folders to `skills/discovery.py`.
3. **Record a real trace** (`--keep-worktrees` plus the trace in `.placebo/runs/*/traces/`), sanitize
   paths, IDs and personal data, and save it to `tests/fixtures/<agent>_<version>_<scenario>.jsonl`.
4. Add a replay test in `tests/test_adapters.py`, covering at least tool-call counting, skill
   activation, tokens and cost, and one error case.

## Security rules

Heuristics live in `static/security.py`. New rules need a positive test *and* a check that
benign skills don't trigger them (false positives erode trust fast). Don't commit real malware. For
hidden-character tests, build the payload inside the test.

## Statistics changes

Any change to `stats.py` or the verdict rules must update `METHODOLOGY.md` and include tests for
the affected verdicts. Please explain the reasoning in the PR; a review from someone with a
statistics background is very welcome.

## Pull requests

- Keep PRs focused, and add or adjust tests.
- `uv run pytest` must pass on your platform. CI runs Linux, macOS and Windows, on Python 3.10–3.13.
- User-facing changes get a line in `CHANGELOG.md`.

By contributing you agree that your contributions are licensed under Apache-2.0.
