# Changelog

All notable package changes are recorded here. The authoritative package release version is `pack.manifest.json#version`.

## [0.6.0] - 2026-06-13

### Changed

- Renamed the public package from `codex-coding-os-starter` to `codex-coding-os`.
- Updated public docs, package metadata, support install folder, archive name, and AGENTS block markers to use Codex Coding OS naming.
- Added cleanup compatibility for the previous `coding-os-starter` support folder during install and uninstall.

## [0.5.1] - 2026-06-13

### Changed

- Added explicit instruction-context-cost, progressive-disclosure, instruction-budget, and deterministic formatter/linter guidance to the first-party skill standard.
- Slimmed `codex-coding-os-master` into a routing skill that defers detailed workflow ownership to narrower skills.

## [0.5.0] - 2026-06-13

### Changed

- Switched the package license model to MPL-2.0 and aligned public license guidance, notices, and commercial-use notes.
- Reworked the README into the hybrid full-system introduction while preserving the installable package name.
- Strengthened session handoff rules so new sessions receive a paste-ready next-session prompt, not only a command.
- Added context-budget and instruction-placement rules for first-party skill design.
- Added focused retry, reproduction, out-of-scope, and architecture-legibility guardrails from a narrow project review.

## [0.4.0] - 2026-06-13

### Added

- Generic automated project session continuity with live current state, deterministic boundary decisions, and persistent handoffs.
- Filled-artifact placeholder validation inside the new-project documentation workflow.
- Internal Markdown link and declared-path validation.
- Concise philosophy, risk-tiered review doctrine, fresh-context review template, and first-party skill quality standard.

### Changed

- Made the new-project workflow block controlled-document drafting while material decisions remain unresolved.
- Made later documentation phases fail closed when an earlier phase is incomplete.
- Made Bash install, upgrade, and uninstall consume a portable installed-state manifest.
- Added obsolete pack-managed skill cleanup and upgrade coverage to both installer families.
- Clarified that Git commit pinning is the enforced integrity control for pinned external Git sources.
- Added stage-bounded workflow guidance and a lighter path for small, reversible changes.
- Updated GitHub Actions to current Node.js 24-based action majors and pinned the Windows runner to an explicit GA image.

## [0.3.0] - 2026-06-11

### Added

- Ubuntu and macOS bash install/uninstall smoke tests in GitHub Actions.
- Public getting-started guide with first-run checks and troubleshooting.
- Standalone architecture decision record template and workflow routing.
- Self-contained conflict-control rules in the bundled catalogue router.

### Changed

- Pinned the optional installable external skill source to a reviewed commit.
- Declared `pack.manifest.json#version` as the sole package release version.
- Strengthened release validation for semantic versioning, changelog coverage, and external-source pin status.
- Changed Git-based packaging to require tracked files to match `HEAD` and archive only the committed revision.
- Expanded the new-project workflow with fail-closed documentation and approval gates.

## [0.2.0] - 2026-06-09

### Changed

- Expanded the repository into the full Coding OS skill and template pack.
- Generalized external-source provenance and pinning policy.
- Hardened release safety, packaging, install, uninstall, and Windows smoke-test workflows.
