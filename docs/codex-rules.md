# Codex rules

Codex rules control which commands Codex can run outside the sandbox.

This pack includes an optional rules template at:

`.codex/rules/default.rules`

## Install options

### User-level rules

Use this when you want the rules to affect every Codex project for the current user:

```powershell
New-Item -ItemType Directory -Force -Path "$HOME\.codex\rules"
Copy-Item -LiteralPath ".\.codex\rules\default.rules" -Destination "$HOME\.codex\rules\default.rules" -Force
```

Restart Codex after copying the file.

### Project-level rules

Use this when you want a project to carry its own command policy:

```powershell
New-Item -ItemType Directory -Force -Path ".\.codex\rules"
Copy-Item -LiteralPath "<path-to-this-pack>\.codex\rules\default.rules" -Destination ".\.codex\rules\default.rules" -Force
```

Project-local rules load only when the project `.codex/` layer is trusted.

## Review before enabling

Check that the rules match the project and team workflow:

- deletion policy is strict enough for the repo
- dependency-manager prompts match the package managers in use
- deployment commands match the hosting stack
- migration commands match the database stack
- environment and `.env` reads require approval
- destructive git commands are blocked or prompted

## Test

When the Codex CLI is available, test representative commands:

```powershell
codex execpolicy check --pretty --rules "$HOME\.codex\rules\default.rules" -- git push origin main
codex execpolicy check --pretty --rules "$HOME\.codex\rules\default.rules" -- rm -rf .git
codex execpolicy check --pretty --rules "$HOME\.codex\rules\default.rules" -- npm install
```

## Notes

Rules are experimental and may change. Re-check the official Codex rules documentation before a public release or before adopting these rules in a team environment.
