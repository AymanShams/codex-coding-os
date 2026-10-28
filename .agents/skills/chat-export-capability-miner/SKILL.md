---
name: chat-export-capability-miner
description: Use when reviewing local ChatGPT, Codex, Claude, Gemini, or other AI chat export folders to extract useful repos, skills, tools, MCPs, plugins, apps, reusable workflows, and skill-conversion candidates into the shared skills-and-plugins catalogue.
metadata:
  short-description: Mine chat exports for reusable capabilities
---

# Chat Export Capability Miner

Use this skill to run a selective capability-delta review over local AI chat exports.

Default comparison ledger: `references/capability-catalogue.md`, or a project-local catalogue when one exists. This ledger records candidates and is not task-routing authority.

## Workflow

1. Choose the output ledger and inspect its current candidate entries.
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
- Keep credentials and live business systems out of candidate pilots unless the current task explicitly authorizes their use.

## Useful Commands

Run the scanner:

```powershell
python ".agents\skills\chat-export-capability-miner\scripts\chat_capability_scanner.py" --roots "<EXPORT_ROOT_1>" "<EXPORT_ROOT_2>" --out "<OUTPUT_DIR>" --catalogue ".agents\skills\chat-export-capability-miner\references\capability-catalogue.md"
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
