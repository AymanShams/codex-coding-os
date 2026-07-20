# Changelog

All notable package changes are recorded here. The authoritative package release version is `pack.manifest.json#version`.

## [Unreleased]

No unreleased changes.

## [0.9.0] - 2026-07-17

### Added

- Added strict product and Coding OS source repository profiles with fail-closed
  automatic detection and embedded, asset, and generated-template parity tests.
- Added a standard-library typed JSON validation-evidence schema, inert validator,
  synthetic example, and identity, full-head, and working-tree checks.
- Added a read-only reentry summary for product and Coding OS source repositories.
- Added Windows, Ubuntu, and macOS CI coverage for the finite case-state engine,
  repository profiles, typed evidence, and reentry summary.
- Added one cross-platform transactional install and uninstall engine with durable
  journals, exact bundle hashing, exclusive locking, finite crash recovery, and
  version 3 local provenance.
- Added marked universal policy migrations that preserve all bytes outside the
  exact automation-policy and pull-request-merge authority blocks.
- Added the `ccos-git-snapshot-v1` exact-head Git-object snapshot contract so
  candidate identity comes only from committed regular-file modes and blob
  bytes, with adversarial checks for drift, dirty state, unsafe entries, and
  cross-worktree determinism.

### Changed

- Made the canonical case-state engine the sole lifecycle authority. Continuity,
  review collectors, comments, prose, manifests, handoffs, and validation evidence
  now report facts without authorizing a repair, closure, or reopen transition.
- Bounded each case to one implementation generation, one frozen review cohort,
  at most one explicitly authorized combined blocker repair, and one closure check
  limited to the frozen blocker identifiers.
- Kept failed closure and control failures scoped to the exact case so unrelated
  product work remains available.
- Preserved configuration, case state, plugins, and non-managed skills outside
  the managed transaction inventory.
- Limited rollback to one verified recovery attempt and fail closed when source,
  ownership, bundle, policy, or rollback evidence does not match.
- Made Git-tracked snapshots the sole lifecycle snapshot authority. Ignored
  review metadata, caches, and dependency folders cannot change a frozen
  candidate, while dirty tracked or nonignored untracked files fail closed.

### Removed

- Removed duplicate review-round and finding counters, prose-derived approval,
  `COMMENTED`-as-approval behavior, clean-phrase authority, and coordination-file
  unlocks.

## [0.8.4] - 2026-06-20

### Changed

- Promoted the package status from public beta to public release.
- Made routing hypotheses explicit by recording route-tree branch metadata and deterministic algorithm-step metadata.
- Documented Tree of Thoughts and Algorithm of Thoughts as bounded routing discipline, not as free-form reasoning expansion.

## [0.8.3] - 2026-06-20

### Added

- Added a concrete clinic follow-up workflow example to make the Coding OS use case more concrete for public beta readers.
- Added an active-slice manifest gate for project-session continuity so coding, handoffs, review markers, notifications, and new sessions cannot bypass the current slice permission boundary.
- Added installer support for refreshing the copied capability index after installation.
- Added deterministic active-slice enforcement for dirty files, required review approval, reviewed SHA matching, and repair of older current-state files that are missing active-slice fields.
- Added registry-backed and active-only capability routing support with canonical-family metadata.
- Added five-layer routing support for container, action, domain, risk/validation, and authority framing.

### Changed

- Clarified public beta feedback intake through GitHub Issues and pull requests.
- Clarified that `codex-capabilities/` is a human/operator routing reference, not a layer for teaching Codex its own defaults.
- Routed new software project starts more reliably through the Coding OS master workflow.
- Updated new-project, handoff, repo-agent, and continuity instructions to require both the workflow manifest and active-slice manifest before coding or parallel worktree lanes.
- Tightened session-start behavior so new-session starts fail closed on dirty working trees, while same-slice continuation remains explicit.
- Stabilized README badges for validation, license, stars, and commit history.

### Fixed

- Avoided frontend capability routing from generic "next steps" wording while preserving explicit `Next.js` routing.
- Replaced Python 3.11-only UTC construction in the fresh-context review helper with Python 3.10-compatible `datetime.timezone.utc`.
- Hardened capability-router noise guards so generic audit, workflow, and release hygiene prompts do not pull unrelated skills.

## [0.8.2] - 2026-06-14

### Added

- Added compact issue templates for install friction, documentation confusion, workflow critique, and security or safety concerns.
- Added an optional capability-router hook candidate with conservative scoring,
  generic-term suppression, and regression coverage.

### Changed

- Expanded `codex-coding-os-master` routing metadata so project starts, repo rescue, PRD/TDD/docs, `AGENTS.md`, handoff, and continuity work trigger the master workflow more reliably.
- Tightened `catalogue-router` guidance so generated routing hints are treated
  as candidates, not authority, and generic keyword matches must be rejected.
- Clarified that `new-project-documentation-system` owns the documentation phase when the master workflow is available, not final implementation, review, validation, or maintenance authority.
- Added public-safe agent harness security gates to `ai-coding-discipline` for skill packs, hooks, MCP servers, external agents, local services, secrets, observability, and memory boundaries.
- Added README first-impression trust notes for OpenAI non-affiliation, third-party provenance, and installing without global Codex changes.
- Moved repository-specific license notes into `NOTICE.md` so `LICENSE.md` starts with the standard MPL-2.0 text.
- Updated release status to public beta.

## [0.8.1] - 2026-06-13

### Changed

- Polished the README opening and added concise CI, license, stars, and last-commit badges.
- Clarified commercial-use FAQ authority, contribution expectations for skill changes, and manifest schema enforcement boundaries.
- Strengthened release safety checks for absolute local paths in committed parallel-lane audit files.
- Added the public-promotion history-scan command to the README validation guidance.

## [0.8.0] - 2026-06-13

### Added

- Added `SECURITY.md`, `CONTRIBUTING.md`, and `pack.schema.json` for public-release hygiene.
- Added compact README fast path, audience labels, and GitHub Actions status badge.
- Added release-safety support for external scanner Git history mode.

### Changed

- Hardened parallel worktree lanes with plan-to-create flow, local-only runtime state, commit-safe audit summaries, neutral default run IDs, and contract-specific validation wording.
- Hardened optional lane hooks to fail closed when an active lane marker exists but the validator script is missing.
- Renamed `COMMERCIAL.md` to `COMMERCIAL-USE.md` to clarify that commercial use is allowed under MPL-2.0 and no separate commercial license is created by the FAQ.

## [0.7.0] - 2026-06-13

### Added

- Added fail-closed parallel worktree lane doctrine, templates, optional hooks, and orchestration script.
- Added manual-by-default lane prompts with an explicit warning and acknowledgement gate for fully automated thread mode.
- Added smoke coverage for blocked workflow manifests, dirty Git state, missing user approval, lane overlap, auto-thread risk acknowledgement, valid lane creation, and controlled-source violations.

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
