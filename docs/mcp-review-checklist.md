# MCP review checklist

Use this checklist before enabling an MCP server, connector, or plugin-managed tool for a project.

## Identity

- MCP name:
- Publisher or maintainer:
- Source repository or official docs:
- Version, tag, or commit reviewed:
- Date reviewed:

## Hosting and trust

- Who hosts the MCP server?
- Is the source code available for review?
- Is the server official, community-maintained, or internal?
- Is the maintainer active?
- Are releases versioned?
- Are installation commands pinned or reproducible?

## Permissions

- What read actions can the MCP perform?
- What write actions can the MCP perform?
- Can it access the local filesystem?
- Can it access the shell?
- Can it browse authenticated websites?
- Can it access source code, credentials, production data, or customer data?
- Can it create, update, delete, deploy, or publish anything?

## Authentication

- Does the MCP require OAuth, API keys, PATs, service accounts, or local credentials?
- Where are credentials stored?
- Are credentials scoped to least privilege?
- Can credentials be rotated?
- Are credentials excluded from repo files, logs, and prompt output?

## Data handling

- What data can be sent to the MCP?
- What external services can receive data through it?
- Are logs retained?
- Can logs contain prompts, source code, secrets, or customer data?
- Is there a privacy policy or data-processing statement?

## Prompt-injection exposure

- Can untrusted web pages, issues, emails, tickets, documents, or database rows enter the MCP context?
- Can the MCP combine untrusted content with privileged tools?
- Are tool calls reviewed before write actions?
- Are destructive actions blocked or approval-gated?

## Tool surface

- Are tool parameters narrow and typed?
- Are broad free-form shell, SQL, browser, or filesystem tools exposed?
- Are dangerous operations split into separate explicit tools?
- Is there a read-only mode?
- Is there a dry-run or preview mode?

## Decision

- Approved for this project: yes/no
- Approved scope:
- Required restrictions:
- Required Codex rules:
- Required environment-variable limits:
- Required monitoring or logs:
- Reviewer:
