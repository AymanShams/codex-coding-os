# Coding Project Agent Instructions

## Coding OS bootstrap

- The executable campaign engine at `scripts/agent/campaign_engine/cli.py` is
  the only lifecycle authority.
- Campaign state lives outside Git at
  `%USERPROFILE%\.codex\coding-os-state\campaigns.sqlite3`.
- For an approved automated campaign, run the engine command named by the
  campaign receipt. For manual work, use normal repository rules and Git
  evidence. Repository mirrors, handoffs, current-state files, and manifests
  are informational only.
- Never call `scripts/agent/case_state.py` for lifecycle work. It is a retired
  fail-closed compatibility sentinel and always returns
  `LEGACY_ENGINE_RETIRED`.
- Do not implement lifecycle transitions, budget resets, role inference,
  publication approval, or cancellation in prose, hooks, skills, or adapters.

## Repository work

- Preserve user changes and use an isolated worktree for material changes.
- Read target code and direct tests before editing. Reuse existing patterns.
- Make the smallest complete change and do not add dependencies without an
  explicit reason.
- Run focused tests first, then the full checks justified by the diff.
- Do not claim completion from a summary. Use exact Git heads, trusted command
  receipts, required review receipts, and publication evidence.
- Never commit credentials, tokens, private keys, or real production values.
