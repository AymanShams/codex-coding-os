---
name: simplify-review-gate
description: Use when reviewing changed code for reuse, quality, efficiency, scope control, unnecessary duplication, unrequested changes, or before accepting Codex-generated work.
---

# Simplify Review Gate

Use this skill after meaningful code changes.

## Scope

1. Inspect the current diff:

```powershell
git diff
git diff --staged
```

2. If there is no diff, review only files explicitly named by the user or changed in this thread.
3. Do not guess broadly.

## Review Lenses

### 1. Reuse

Check for:

- existing helpers
- existing components
- existing hooks
- existing services
- existing types
- existing validators
- existing constants
- existing test utilities

Reject duplicate logic unless duplication is intentionally safer.

### 2. Code Quality

Check for:

- redundant state
- parameter sprawl
- copy-paste variants
- leaky abstractions
- raw strings where constants or enums exist
- unnecessary comments
- broad catch-all logic
- unrelated formatting churn

### 3. Efficiency

Check for:

- repeated file reads
- duplicate API calls
- N+1 queries
- unbounded loops
- work added to startup or request hot paths
- repeated state updates with no value change
- missed safe concurrency

### 4. Verification

Check whether the right tests ran:

- unit
- integration
- typecheck
- lint
- build
- browser flow
- migration validation

## Fix Policy

For small safe findings, fix directly.

For risky findings, report first and ask before changing.

Do not rewrite clean code only to match personal preference.

## Output

Report:

- findings fixed
- findings skipped as false positives
- checks run
- checks not run
- residual risk

