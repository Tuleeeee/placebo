# Roadmap

Placebo's goal: make "does this skill / instruction / setup actually help?" a question anyone can
answer with evidence, on their own code, for any coding agent.

## v0.1 (current)

- `scan`: discovery across agents, context tax, collisions, duplicates, lint, broken references, security flags
- `trigger`: generated or hand-written probes, activation and false-activation rates
- `ab`: git-mined validated tasks or YAML tasks; baseline / treatment / sham; worktree isolation;
  randomized interleaving; two-level bootstrap and TOST verdicts; HTML report; budgets; resume
- Adapters: Claude Code, Codex (beta)

## v0.2: every agent, every PR

- Adapters: Gemini CLI, OpenCode (including local models via Ollama), Cursor CLI, Copilot CLI, Pi
- GitHub Action for `ab` with a PR comment summary; shields.io badge endpoint for skill authors
- Sequential early stopping (alpha-spending) to cut cost
- Results cache keyed by task, arm hash, agent version and model
- Optional sandbox backends (Docker, anthropics/sandbox-runtime)
- AGENTS.md / MCP-server / model changes as first-class arms (beyond `--file`)
- More task miners: Go, Rust, JS monorepos

## v0.3: the leaderboard

- **SkillBench v1**: pre-registered, reproducible trials of popular public skills on permissively
  licensed repos, with every trace published and authors given a right of reply
- `placebo compare`: which agent and model combination completes *your* repo's tasks best, and at what cost
- Session-miner task source (turn your own agent transcripts into tasks)
- Trace viewer: what filled the context window on each turn

## v1.0

- Stable plugin API for adapters, task sources, graders and reporters
- `placebo optimize`: shrink skills (split core vs references, compress descriptions), validated by A/B
- Docs site; multiple maintainers; governance doc

## Not planned (for now)

A hosted service, a skill registry or package manager (use `npx skills`), our own sandbox
technology, or auto-publishing results anywhere.
