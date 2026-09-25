---
name: release-notes
description: Use when the user asks for release notes or a changelog entry for a new version.
---

# Release notes

Collect merged changes since the last tag with `git log --oneline <last-tag>..HEAD`, group them into
Added / Changed / Fixed, and fill in [the template](references/release-template.md).

Mention breaking changes first, in bold, with a one-line migration hint.
