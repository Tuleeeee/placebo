# Security policy

## Reporting a vulnerability

Please report vulnerabilities **privately** through GitHub: *Security* tab → *Report a vulnerability*
on this repository. Do not open a public issue. You should get a response within a few days.

## Threat model (what Placebo does and doesn't protect against)

Placebo runs third-party **skills** through real **coding agents** that can edit files and execute
shell commands. Please understand the boundaries:

- **Static flags are heuristics.** `placebo scan` catches common, documented patterns (hidden Unicode
  instructions, pipe-to-shell, exfiltration endpoints, credential paths, instruction overrides). It
  will miss novel or well-obfuscated attacks. A clean scan is not a security guarantee.
- **Quarantine.** `trigger` and `ab` refuse skills with high or critical flags unless you pass
  `--allow-flagged`.
- **Worktrees are not sandboxes.** Trials run in temporary git worktrees, which isolate *repository
  state* but not your machine. The agent runs with your user's permissions and network access, limited
  only by the agent's own permission system. For untrusted skills, run Placebo inside a container or VM
  with no credentials mounted.
- **Traces may contain secrets** that the agent printed. They stay local in `.placebo/`. Review them
  before sharing a run.
- **Environment.** Placebo passes your environment to the agent, so the agent can authenticate. When
  launched from inside another Claude Code session, it strips that session's plumbing variables
  (`CLAUDE_CODE_*`, except auth and provider settings).

Reports of bypasses of the quarantine heuristics, or of ways a skill can influence Placebo's own
grading, are especially welcome.
