---
name: write-commented-code
description: Write, modify, refactor, or review code in the AcqStaging repository with concise comments explaining every logical code block. Use for implementation work in src, scripts, tests, adapters, services, configuration, GUI code, hardware integrations, and other source files where a future developer needs to understand what each block does and why.
---

# Write Commented Code

Apply the repository contract first, then make each logical code block understandable without narrating obvious syntax.

## Comment every logical block

Add a comment immediately above each meaningful block, including:

- validation and safety checks;
- setup, initialization, and state transitions;
- loops or branches that implement a distinct rule;
- hardware connection, command, conversion, timeout, and cleanup stages;
- worker-thread, callback, signal, polling, and GUI handoffs;
- parsing, serialization, file I/O, rollback, and error recovery;
- non-obvious calculations, ordering, or data transformations.

Limit each comment to one line. Use a longer comment only when a complex algorithm, hardware lifecycle, safety invariant, or difficult concept cannot be explained accurately in one line.

## Use docstrings separately

Use complete docstrings for public functions, classes, protocols, and modules when they improve the API contract. Docstrings are not subject to the one-line comment limit.

A docstring explains what a function or class promises. Block comments explain the important stages inside its implementation.

## Keep comments useful

- Explain intent, constraints, ordering, units, safety, or why a block exists.
- Prefer `# Preflight every tile so limits fail before the first hardware command.`
- Avoid `# Loop over tiles.` when the loop is already self-explanatory.
- Do not comment imports, plain assignments, simple returns, dataclass fields, or protocol stubs.
- Do not add an end-of-line comment when a preceding block comment is clearer.
- Preserve accurate existing comments and update them when behavior changes.
- Remove stale commented-out code instead of presenting it as documentation.
- Never use comments to excuse unclear structure; refactor first when a small structural change improves clarity.

## Workflow

1. Read `AGENTS.md`, `PLAN.md`, and the relevant implementation before editing.
2. Implement the requested behavior without changing unrelated user work.
3. Review each changed function from top to bottom and identify its logical blocks.
4. Add or update concise comments at every meaningful block boundary.
5. Check that comments describe current behavior and do not claim unverified hardware behavior.
6. Run the repository's required pytest, Ruff, and mypy checks.

## Review standard

Before finishing, confirm that a developer unfamiliar with the code can identify initialization, validation, main processing, external I/O, error handling, and cleanup by scanning docstrings and comments.
