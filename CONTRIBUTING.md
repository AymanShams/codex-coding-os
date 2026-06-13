# Contributing

## Contribution scope

Useful contributions improve the pack without making Codex noisier or less bounded.
Good changes usually fall into one of these areas:

- clearer install, uninstall, or first-run guidance;
- safer validation, packaging, hooks, or release checks;
- corrected attribution, license, or source links;
- focused skill improvements that prevent a recurring coding failure;
- small template fixes that make project handoff, review, or validation clearer.

Avoid broad prompt dumps, duplicate skill packs, speculative frameworks, private
company context, credentials, local plugin caches, and large behavior changes that
are not tied to a demonstrated failure mode.

## Pull request checklist

Before opening a pull request:

1. Run `.\scripts\validate-pack.ps1` on Windows, or `./scripts/validate-pack.ps1`
   from PowerShell on macOS or Linux.
2. Run the relevant smoke test for the files changed.
3. Update `pack.manifest.json` when adding, removing, or renaming a required file.
4. Update `CHANGELOG.md` for user-visible package changes.
5. Read `docs/skill-quality-standard.md` before adding or materially changing a skill.
6. Check that new files contain no credentials, private context, local paths,
   generated plugin caches, or unreviewed third-party source copies.
7. Add or correct attribution in `THIRD_PARTY_SKILLS.md` when a change uses an
   external source.

## Style

Keep instructions short, direct, and operational. Prefer links and scoped templates
over embedding large reference material. New rules should have a clear owner:
root `AGENTS.md` for universal safety, scoped `AGENTS.md` for folder behavior, and
skills for task-specific procedures.
