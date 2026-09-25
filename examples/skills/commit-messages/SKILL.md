---
name: commit-messages
description: Use when the user asks you to commit changes or write a git commit message. Produces Conventional Commits style messages.
---

# Commit messages

1. Run `git diff --staged` and read the full change before writing anything.
2. Use the Conventional Commits format: `type(scope): summary` where type is one of
   feat, fix, docs, refactor, test, chore, perf.
3. Keep the summary under 72 characters, imperative mood, no trailing period.
4. In the body, explain *why* the change was made, not what the diff already shows.
5. Reference issue numbers as `Refs #123` on the last line when the user mentions one.
