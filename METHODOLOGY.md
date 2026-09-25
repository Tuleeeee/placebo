# Methodology

This document explains how `placebo ab` turns agent runs into a verdict, and where its limits are.
If you find a flaw, please open an issue; the method matters more than the tool.

## Question

> Given *my* repository, *my* agent and *my* setup, does adding this skill (or changing this file)
> change how often the agent completes real tasks, and at what cost?

## Arms

| Arm | Contents |
|---|---|
| `baseline` | The skill under test is absent (removed from every project-level skill folder). |
| `treatment` | The skill is copied into the agent's project-level skill folder (e.g. `.claude/skills/`, `.agents/skills/`). |
| `sham` | Same `name` and `description` as the skill, so it is listed and triggers the same way, but the body is replaced with inert, instruction-free prose of equal length. Supporting files are omitted. |

For `--file TARGET=VARIANT`: `baseline` = the file as committed (or absent), `treatment` = the variant,
`sham` = inert text of the variant's length.

**Why a sham?** Adding *any* text to an agent's context changes its behavior: it spends more tokens,
reads more, and sometimes becomes more careful. If `treatment` beats `baseline` but not `sham`,
the gain comes from the ritual, not the instructions. Placebo then reports `INCONCLUSIVE`
rather than `HELPS`.

**Background setup.** User-level skills, plugins, settings and MCP servers are left as they are and are
identical in every arm. The measured effect is therefore "adding this skill *to my setup*". If the
skill under test is *also* installed at user level, the baseline would still see it, so Placebo refuses
to run unless you pass `--allow-global`.

## Tasks

**Git-mined tasks (`--tasks git:N`).** A commit is a candidate when it is not a merge and it changes at
least one test file and at least one non-test source file. For each candidate:

- workspace = parent commit + the commit's versions of the changed test files
- prompt = commit subject and body + the list of test files, plus an instruction not to modify them
- check = run those test files (auto-detected pytest/vitest/jest/go/cargo, or `--test-cmd`)
- **validation** (default on): the check must *fail* in that workspace and *pass* at the commit itself.
  Candidates failing validation are skipped and reported. This removes unsolvable tasks and tasks
  that were already passing.

Commit messages can leak implementation hints, and popular public repositories may be in a model's
training data. For decision-making on your own private code this is usually acceptable. For public
benchmarks, mine commits made after the model's training cutoff.

**Hand-written tasks** (`--tasks tasks.yaml`) specify a prompt, optional files to create (e.g. hidden
acceptance tests), setup commands and checks. See `examples/tasks.yaml`.

## Execution

- Each trial runs in a fresh `git worktree` at the task's base commit, with the arm applied, then setup.
- The agent runs headless with the task prompt. Its JSON event stream is saved as a trace.
- Before grading, the task's test files are restored, so edits the agent made to tests don't count.
- Grading = all checks exit 0.
- Order: for each trial round, the (task, arm) pairs are shuffled with a seeded RNG. Arms are
  interleaved in time so that drift (API load, silent model updates, rate limits) affects all arms equally.

**Exclusions.** `setup_error` (the workspace could not be prepared) and `agent_error` (the agent never
started working: not logged in, crash) are excluded from statistics and shown in the report. Timeouts
count as failures, since running out of time is part of performance. If the first two runs are both
`agent_error`, the run stops.

**Activation.** For skill arms, a trial is "activated" when the trace shows the agent invoking the
skill (a `Skill` tool call naming it, or a tool call that reads its `SKILL.md`). Command *output* that
merely mentions the skill does not count.

## Statistics

The unit of analysis is the **task**. For arms A and B, and each task *t* with results in both,
dₜ = mean pass(A, t) − mean pass(B, t). The point estimate is the mean of dₜ.

**Two-level bootstrap** (4,000 resamples, seeded): resample tasks with replacement, then, within each
selected task, resample that task's trials in each arm. This reflects both task-selection uncertainty
and run-to-run agent noise. Percentile intervals give the 95% and 90% CIs.

Also reported: exact McNemar p-value on per-task majority outcomes, and the mean paired differences in
tokens, dollars and time with bootstrap CIs.

## Verdict rules (in order)

1. **NOT_ACTIVATED**: treatment activation rate < 30%. The content was not really tested.
2. **HELPS**: 95% CI of treatment − baseline is above 0 **and**, when a sham arm exists, the 95% CI
   of treatment − sham is above 0. If only the first holds → **INCONCLUSIVE** (possible context effect).
3. **HURTS**: 95% CI of treatment − baseline is below 0.
4. **INCONCLUSIVE** when every trial passed (ceiling) or failed (floor) in both arms: the tasks can't
   show a difference.
5. **INCONCLUSIVE** when fewer than 5 tasks have paired results (too few to claim equivalence).
6. **PLACEBO**: the 90% CI of treatment − baseline lies within ±δ (default 5 percentage points).
   This is a two one-sided tests (TOST) equivalence test at α = 0.05.
7. Otherwise **INCONCLUSIVE**, with the CI width, so you know how much more data you need.

## Known limitations

- **Small samples.** With 10 tasks × 3 trials, only large effects are detectable. Treat
  `INCONCLUSIVE` as "keep measuring", not as "no effect".
- **Headless ≠ interactive.** Agents may activate skills differently in non-interactive mode.
  `NOT_ACTIVATED` surfaces this rather than hiding it.
- **Pass/fail only.** v0.1 grades by checks. Code quality, style and review-readiness are not measured unless
  your checks measure them (linters, type checkers, custom scripts).
- **Token counts in `scan` are estimates** (~4 UTF-8 bytes per token). Token and dollar figures in
  `ab` come from the agent's own reports; Codex reports tokens but not dollars.
- **Multiple skills.** When testing many skills, some will look significant by chance. Correct
  for multiple comparisons (e.g. Holm) before acting on a batch.
- **Model drift.** Results are specific to the agent and model versions recorded in the report.
  Re-run after upgrades.

## Publishing results about someone else's skill

If you publish results about a named public skill, please: share the full report and traces, state
agent and model versions and the number of tasks and trials, report nulls as well as wins, and give the
author a chance to respond before publication.
