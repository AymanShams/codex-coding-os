---
name: code-review-graph
description: "Use when the user asks for code-review-graph, graph-backed code review, structural codebase analysis, blast-radius analysis, dependency impact, affected-flow tracing, callers/callees/importers, architecture maps, repo onboarding, risk-scored diffs, token-efficient codebase exploration, or persistent graph context using the installed code-review-graph MCP or CLI. Use for unfamiliar repositories, large diffs, risky refactors, hidden dependency impact, affected tests, architectural hotspots, and compact code context retrieval. Do not use for tiny single-file edits, simple exact-text search, semantic file discovery only, normal GitHub issue or PR triage, generic architecture advice without repo evidence, or security-specific threat modeling. If the primary task is locating files by intent, use vexor-cli first. If the primary task is improving architecture or refactoring strategy, use improve-codebase-architecture and add this skill only for graph evidence. If the primary task is AppSec threat modeling, use security-threat-model."
---

# Code Review Graph

Use this skill for the gap not covered by normal Codex file search, GitHub skills, Superpowers review workflows, or manual code review: persistent structural graph context.

## First Choice: MCP Tools

When MCP tools are available, start with `get_minimal_context_tool(task="<task>")`. Keep outputs compact by using `detail_level="minimal"` first, then escalate only when needed.

Core tool choices:

- Build or refresh graph: `build_or_update_graph_tool`.
- Check graph state: `list_graph_stats_tool`.
- Review changed work: `detect_changes_tool`, then `get_affected_flows_tool` for higher-risk changes.
- Trace impact: `get_impact_radius_tool`.
- Query relationships: `query_graph_tool` with patterns such as `callers_of`, `callees_of`, `imports_of`, `importers_of`, `children_of`, `tests_for`, `inheritors_of`, or `file_summary`.
- Explore architecture: `get_architecture_overview_tool`, `list_communities_tool`, `get_community_tool`, `list_flows_tool`, and `get_flow_tool`.
- Refactor safely: `refactor_tool` in preview mode first. Use `apply_refactor_tool` only after reviewing the preview and confirming it matches the user's requested change.

## CLI Fallback

If MCP tools are not visible in the session, use the local CLI directly:

```powershell
& "$HOME\.codex\tools\code-review-graph\Scripts\code-review-graph.exe" status
& "$HOME\.codex\tools\code-review-graph\Scripts\code-review-graph.exe" build
& "$HOME\.codex\tools\code-review-graph\Scripts\code-review-graph.exe" detect-changes --brief
```

Use the CLI from the target repository root. The graph database lives in `.code-review-graph/graph.db` under that repo.

## When To Use

Use this skill for:

- large pull requests or branch diffs
- risky refactors
- unfamiliar repositories
- hidden dependency impact
- finding callers, callees, tests, imports, and architectural hotspots
- checking whether a change has a broad blast radius

Do not use it for tiny single-file edits where normal file reading is cheaper.

## Review Output

For reviews, report:

- summary of changed behavior
- risk level: Low, Medium, or High
- impacted files/functions
- missing or weak tests
- concrete findings before general advice
- suggested next verification command

Keep this skill subordinate to the user's active instructions and the repo's existing `AGENTS.md`.
