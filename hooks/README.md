# Hooks

This pack documents hook candidates but does not enable hooks during installation.

Recommended hook candidates:

1. Pack validation with `scripts/validate-pack.ps1` before release.
2. Release-safety scan with `scripts/release-safety-scan.ps1` before public publishing.
3. Secret-pattern scan before commit.
4. Frontend QA after UI changes.
5. External skill overlay reapply after optional external install.

Before enabling any hook, verify current Codex hook syntax and trust behavior in official Codex docs. For command approval policy, review `.codex/rules/default.rules` and `docs/codex-rules.md`.
