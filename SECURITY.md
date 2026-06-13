# Security Policy

## Supported versions

Security fixes are handled on the current `main` branch.

## Reporting a vulnerability

Do not include secrets, exploit payloads, private project data, or sensitive user
data in a public issue.

Preferred reporting path:

1. Use GitHub's private security advisory flow for this repository if it is enabled.
2. If private advisories are not enabled, open a short public issue asking for a
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
