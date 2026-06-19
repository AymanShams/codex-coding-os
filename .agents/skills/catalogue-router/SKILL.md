---
name: catalogue-router
description: Use at the start of any non-trivial task, repo or project start, capability review, tool or skill selection, plugin or MCP selection, or when the user asks what local capability to use. Routes through the shared skills-and-plugins catalogue without loading the whole file.
metadata:
  short-description: Route tasks through the local skills and plugins catalogue
---

# Catalogue Router

Use this skill to choose the narrowest useful active local skill, enabled plugin, MCP server, or installed tool before doing non-trivial work. Candidate, project-local, and reference-only entries can be considered only as gated session-only support.

Bundled catalogue:

`references/capability-catalogue.md`

Optional generated routing hints from hooks or index scripts are candidates, not
authority. A capability is valid only when it materially helps the actual task
after the task, non-goals, source of truth, allowed action, validation standard,
and stop rule are clear.

## Skip Gate

Skip the catalogue check for clearly trivial or self-contained tasks:

- current time/date
- simple translation
- one-line rewrite
- direct shell command answer
- casual conversation with no tool choice
- a factual answer where live web/source verification is the controlling issue

For everything else, do a quick routing pass.

## Fast Routing Workflow

1. Extract 2-5 keywords from the user request.
   Include domain, file type, tool names, company names, framework names, and task type.
2. Run the task gate silently:
   actual task, non-goals, example-only material, controlling scope, source of
   truth, allowed action, validation standard, and stop rule.
3. Treat generated hook or index results as candidate hints only. Reject matches
   based only on generic words such as file, edit, verify, audit log, skill,
   plugin, tool, issue, workflow, that, or why.
4. Search the catalogue by keyword when a candidate needs context, precedence,
   or boundary details. Prefer `rg`.
   Use the bundled helper if useful:

   ```powershell
   & "$HOME\.agents\skills\catalogue-router\scripts\query-catalogue.ps1" -Query "React|Next.js|security|PRD"
   ```

5. Load only the matching section, not the whole catalogue.
6. Choose the narrowest active capability first:
   local skill, enabled plugin skill, configured MCP server, then local installed tool.
7. If active capabilities leave a material gap, check `Candidate Backlog` only as a second pass for session-only support.
   Candidate, project-local, and reference-only items require explicit user authorization before use, must never be primary owners, and must never be installed universally by default.
8. If no catalogue hit is useful, proceed normally and say nothing unless the missing capability affects the recommendation.

## Routing Hints

- For product ideas that are vague, too linear, or too tidy, use `create-prd`, `working-backwards`, `product-strategy`, and `customer-journey-map` to add real users, constraints, edge cases, and tradeoffs before coding.

## Project Start Workflow

When starting or reviewing a project:

1. Search by project domain and stack, for example `React|Next.js|Supabase|RAG|security`.
2. Pick only 3-7 relevant capabilities.
3. Add them to the project `AGENTS.md` or project context only when actively working in that project or when the user asks for project setup.
4. Keep candidate tools labeled as `project-local pilot`, `reference only`, or `skip/avoid`.
5. Do not promote candidates to global skills or plugins. Use them only as authorized session-only support after active capabilities have been checked.

## Decision Rules

- Installed active capability beats a new repo.
- Project-local pilot beats global install when setup cost, licensing, API keys, privacy, or behavior drift are material.
- Reference-only is correct for lists, prompt packs, broad skill packs, and design inspiration sources.
- Candidate and reference-only items are never primary skills. Ask before using them and keep use bounded to the current session.
- Skip/avoid is correct when legal, privacy, security, provenance, or execution risk dominates the likely benefit.

## Conflict Control

- Choose one primary skill for the requested output or workflow.
- Add supporting skills only when they materially change a defined phase.
- Keep skill and plugin selection active for non-trivial tasks, but do not
  accept a capability that only matched generic routing language.
- The primary orchestrator controls sequence, stop gates, and completion claims. Supporting skills must not bypass it.
- Do not stack critique, pre-mortem, evidence, validation, and interview skills unless the user requests a formal review or the risk justifies it.
- When two skills overlap, use the one whose description most directly matches the requested output.
- Prefer execution skills for implementation, critique skills for explicit review, and evidence checking when source quality or recency is the core risk.
- If instructions conflict, stop at the safer or more source-faithful gate and state the conflict.

## Response Pattern

Usually keep this invisible and proceed with the selected capability.

Mention the routing only when it changes the approach:

`I checked the catalogue by keyword and found X is already the best fit, so I am using that instead of adding Y.`

For new projects, summarize the selected capabilities in one short block:

`Project-local capabilities: X for docs, Y for frontend QA, Z as a candidate pilot.`
