---
name: chat-export-capability-miner
description: Use when reviewing local ChatGPT, Codex, Claude, Gemini, or other AI chat export folders to extract useful repos, skills, tools, MCPs, plugins, apps, reusable workflows, and skill-conversion candidates into the shared skills-and-plugins catalogue.
metadata:
  short-description: Mine chat exports for reusable capabilities
---

# Chat Export Capability Miner

Use this skill to run a selective capability-delta review over local AI chat exports.

Default catalogue:

`references/capability-catalogue.md` from the bundled `catalogue-router` skill, or the user's project-local capability catalogue when one exists.

## Workflow

1. Use `catalogue-router` first and keep the catalogue as the decision ledger.
2. Inventory export folders by file type and count. Do not read every chat into context.
3. Run a mechanical scan for:
   - GitHub repos
   - external tool/app domains
   - MCP mentions
   - skill/plugin/tool/install/pilot/avoid signals
   - chats that can become reusable skills
4. Deduplicate against the existing catalogue.
5. Inspect only high-signal chats, especially direct repo reviews and recommendation summaries.
6. Classify candidates as:
   - `install/run`
   - `conditional future skill`
   - `project-local pilot`
   - `reference only`
   - `skip/avoid`
7. Patch the catalogue, keeping active capabilities separate from backlog.
8. Create a local skill directly only when the workflow is useful now, not merely interesting.

## Guardrails

- Treat old chat conclusions as memory-derived and possibly stale.
- Re-verify live sources before installing or running external code.
- Do not globally install broad packs, clone ecosystems, prompt libraries, or catalogues.
- Prefer selective mining of the useful subset over wholesale install.
- For regulated, client, or sensitive workflows, keep protected data, credentials, and production systems out of pilots unless a governance review exists.

## Useful Commands

Run the scanner:

```powershell
python ".agents\skills\chat-export-capability-miner\scripts\chat_capability_scanner.py" --roots "<EXPORT_ROOT_1>" "<EXPORT_ROOT_2>" --out "<OUTPUT_DIR>" --catalogue ".agents\skills\catalogue-router\references\capability-catalogue.md"
```

Then inspect:

```powershell
Get-Content "<OUTPUT_DIR>\capability_scan_summary.md" -TotalCount 260
```

## Output Standard

End with:

- folders scanned
- number of Markdown files scanned
- catalogue entries added or updated
- skills created, if any
- candidates deferred for live verification
- skipped items and why
