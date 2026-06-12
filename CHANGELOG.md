# Changelog

All notable package changes are recorded here. The authoritative package release version is `pack.manifest.json#version`.

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
