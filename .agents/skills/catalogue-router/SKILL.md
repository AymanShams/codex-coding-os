---
name: catalogue-router
description: Use at the start of any non-trivial task, repo or project start, capability review, tool or skill selection, plugin or MCP selection, or when the user asks what local capability to use. Routes through the shared skills-and-plugins catalogue without loading the whole file.
metadata:
  short-description: Route tasks through the local skills and plugins catalogue
---

# Catalogue Router

Use this skill to choose the narrowest useful local skill, plugin, MCP server, installed tool, or project-local candidate before doing non-trivial work.

Bundled catalogue:

`references/capability-catalogue.md`

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
2. Search the catalogue by keyword. Prefer `rg`.
   Use the bundled helper if useful:

   ```powershell
   & "$HOME\.agents\skills\catalogue-router\scripts\query-catalogue.ps1" -Query "React|Next.js|security|PRD"
   ```

3. Load only the matching section, not the whole catalogue.
4. Choose the narrowest active capability first:
   local skill, enabled plugin skill, configured MCP server, then local installed tool.
5. If the task may need a non-installed capability, check `Candidate Backlog` before recommending anything new.
6. If no catalogue hit is useful, proceed normally and say nothing unless the missing capability affects the recommendation.

## Routing Hints

- For product ideas that are vague, too linear, or too tidy, use `create-prd`, `working-backwards`, `product-strategy`, and `customer-journey-map` to add real users, constraints, edge cases, and tradeoffs before coding.

## Project Start Workflow

When starting or reviewing a project:

1. Search by project domain and stack, for example `React|Next.js|Supabase|RAG|security`.
2. Pick only 3-7 relevant capabilities.
3. Add them to the project `AGENTS.md` or project context only when actively working in that project or when the user asks for project setup.
4. Keep candidate tools labeled as `project-local pilot`, `reference only`, or `skip/avoid`.
5. Do not promote candidates to global skills or plugins without a capability-delta review.

## Decision Rules

- Installed active capability beats a new repo.
- Project-local pilot beats global install when setup cost, licensing, API keys, privacy, or behavior drift are material.
- Reference-only is correct for lists, prompt packs, broad skill packs, and design inspiration sources.
- Skip/avoid is correct when legal, privacy, security, provenance, or execution risk dominates the likely benefit.

## Response Pattern

Usually keep this invisible and proceed with the selected capability.

Mention the routing only when it changes the approach:

`I checked the catalogue by keyword and found X is already the best fit, so I am using that instead of adding Y.`

For new projects, summarize the selected capabilities in one short block:

`Project-local capabilities: X for docs, Y for frontend QA, Z as a candidate pilot.`
