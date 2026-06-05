# Codex Rules Templates

This folder contains optional command approval rules for Codex.

Rules control which commands Codex can run outside the sandbox. They are not installed automatically by this pack because they affect user-level execution policy.

## Use

1. Review `.codex/rules/default.rules`.
2. Copy it into an active Codex rules layer, such as `~/.codex/rules/default.rules`, or keep it in a trusted project `.codex/rules/` folder.
3. Restart Codex.
4. Test important rules with `codex execpolicy check` when the Codex CLI is available.

## Scope

The template prompts or blocks common high-risk commands:

- recursive deletion
- hard reset and broad git cleanup
- git push
- package installation
- database migrations
- cloud deployment
- infrastructure changes
- environment and `.env` reads

Review current Codex rules documentation before publishing modified rules.
