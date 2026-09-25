# Changelog

## 0.1.0 (unreleased)

First public release.

- `placebo scan`: static analysis of installed skills (Claude Code user, project and plugin skills;
  Codex, Gemini CLI, OpenCode, Cursor, Copilot and Pi folders) or any path: context-token estimates,
  description collisions, duplicates, lint, broken references and security heuristics (including
  decoding of hidden Unicode-tag text). `--json`, `--fail-on`.
- `placebo trigger`: activation and false-activation rates from generated or YAML probes.
- `placebo ab`: controlled trials with baseline / treatment / sham arms on validated tasks mined from
  git history or written in YAML; `--file` mode for AGENTS.md-style files; worktree isolation;
  randomized interleaving; two-level bootstrap CIs, McNemar, TOST equivalence; verdicts HELPS / HURTS /
  PLACEBO / INCONCLUSIVE / NOT_ACTIVATED; single-file HTML report; budgets, resume and a circuit
  breaker for agents that fail to start.
- `placebo tasks mine`, `placebo report`, `placebo doctor`.
- Adapters: Claude Code (tested against real 2.1.281 output), Codex (beta, format-based).
