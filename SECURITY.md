# Security Policy

## Supported versions

Security fixes are handled on the current `main` branch.

## Reporting a vulnerability

Do not include secrets, exploit payloads, private project data, or sensitive user
data in a public issue.

Preferred reporting path:

1. Use GitHub private vulnerability reporting for this repository.
2. If private vulnerability reporting is unavailable, open a short public issue asking for a
   private reporting channel. Do not include technical exploit details in that issue.

## Scope

Security reports are most useful when they affect:

- install or uninstall scripts;
- release packaging;
- Codex rules, hooks, or command approval behavior;
- external skill installation or overlays;
- MCP, plugin, or connector guidance;
- secret handling, local path exposure, or provenance controls.

This repository is a workflow and tooling pack. Vulnerabilities in applications
built with the pack should be reported to those application owners unless the issue
comes from a bundled file in this repository.

## Capability boundary

Codex Security, Supabase, and Neon Postgres are Codex-managed third-party
plugins. Their skills, connectors, MCP runtime, credentials, and receipts are
not bundled here. The router under `capability-routing/` is dormant reference
source and is not activated by the installer.

See `docs/security-capability-operating-model.md` for the 13-skill Codex
Security map, repository-owned security skills, provider composition, fallback
limits, and live-validation requirements.
