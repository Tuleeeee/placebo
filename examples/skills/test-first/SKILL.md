---
name: test-first
description: Use when implementing a feature or fixing a bug in a codebase that has an automated test suite. Write or update a failing test before changing source code.
---

# Test first

1. Find the existing tests closest to the code you are about to change and run them.
2. Write a test that captures the requested behavior and confirm it fails for the right reason.
3. Make the smallest source change that makes the test pass.
4. Run the whole relevant test file (not just the new test) before you finish.
5. Never weaken or delete an existing assertion to make a test pass; report the conflict instead.
