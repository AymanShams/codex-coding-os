# Publishing checklist

## Before private sharing

- Run `.\scripts\validate-pack.ps1`.
- Run `.\tests\install-uninstall-smoke.ps1`.
- Open `templates/first-codex-prompt.md` and confirm it is clear for a non-technical user.
- Rebuild the ZIP with `.\scripts\package.ps1`.
- Install from the ZIP on a clean Windows profile or temp profile.

## Before public release

- Confirm `pack.manifest.json` is the current source of truth.
- Confirm `pack.manifest.json#version` is valid semantic versioning and has a matching `CHANGELOG.md` entry.
- Commit the reviewed release state and confirm tracked Git files match `HEAD` before packaging.
- Run `.\scripts\validate-pack.ps1 -RequireExternalScanners` after installing `gitleaks` and `trufflehog`.
- Run `.\tests\install-uninstall-smoke.ps1`.
- Confirm the GitHub Actions Ubuntu and macOS bash smoke tests pass.
- Rebuild the ZIP with `.\scripts\package.ps1`.
- Inspect the ZIP and confirm excluded local files are absent.
- Verify every third-party source in `external-skills/manifest.json`.
- Confirm attribution and license notes in `THIRD_PARTY_SKILLS.md`.
- Pin installable external sources or keep them marked as not repeatable in `external-skills/manifest.json`.
- Confirm optional Codex rules in `.codex/rules/default.rules` match current Codex rules docs.
- Confirm MCP guidance in `docs/mcp-review-checklist.md` matches current Codex and OpenAI MCP docs.
- Confirm GitHub Actions passes on a fresh clone.
- Test `scripts/install.sh` and `scripts/uninstall.sh` on macOS or Linux before advertising cross-platform support.
- Confirm examples and templates are generic and contain no private context.
- Confirm no credentials, API keys, production values, private exports, local caches, or connected-app state are present.

## Before enterprise use

- Have counsel review `LICENSE.md`, `COMMERCIAL.md`, third-party notices, and attribution.
- Require pinned commits, hashes, or signed releases for installable external sources.
- Require CI secret scanning and artifact upload on protected branches.
- Require branch protection and PR review for manifest, installer, rules, and security changes.
- Decide whether Codex rules should be user-level, team-level, or project-level.
- Review MCPs, plugins, and connectors against `docs/mcp-review-checklist.md`.
- Define support, update, and vulnerability-reporting expectations.

## Release notes template

```text
Version:
Date:

Added:
- 

Changed:
- 

Fixed:
- 

Known limitations:
- 
```
