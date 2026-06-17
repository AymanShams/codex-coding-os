# First Codex Prompt

Paste this into a new Codex chat after installing the pack.

```text
Use $codex-coding-os-master.

I am starting or continuing a Codex coding project. I do not want to start coding from a vague idea or weak context. I want you to help me turn the idea or existing repo into approved project documentation, a technical design, repo instructions, and eventually a first implementation slice.

My idea:
{{write_the_idea_here}}

What I know so far:
- Target users:
- Problem:
- Desired outcome:
- Platform: web app, mobile app, API, automation, data tool, or not sure
- Data involved:
- Login or accounts needed:
- Payments needed:
- Integrations needed:
- Deadline:
- Budget or hosting constraint:
- Anything that must not happen:

Your job:
1. Create `project-documentation-manifest.json` first and use it as the phase ledger.
2. Inventory and classify the sources.
3. Ask all unresolved material questions needed to avoid building the wrong product. Stop before drafting if material decisions or source conflicts remain.
4. Explain any technical terms in simple language.
5. Create a project brief first.
6. Then create these source-controlled docs:
   - PRD
   - app-flow-doc
   - tech-stack-doc
   - frontend-guidelines
   - backend-structure
   - security-guidelines
   - implementation-plan
7. Treat those documents as drafts until I approve them.
8. Create a TDD technical design document and alignment review after the docs are approved.
9. Create the full stage-appropriate repo documentation and root/scoped AGENTS.md instructions.
10. Add `docs/delivery/current-state.md`, `docs/delivery/active-slice-manifest.json`, and install the project session continuity command.
11. Create a persistent handoff note and final validation report.
12. Recommend the first vertical implementation slice only after the workflow manifest and active-slice manifest both permit coding.
13. Do not start implementation until I explicitly approve coding.

Rules:
- Do not assume paid services unless I approve them.
- Do not store secrets in code.
- Do not invent requirements.
- If you are missing a material answer, ask me and stop before the PRD.
- Use assumptions only for low-risk, reversible presentation choices.
- Do not call coding the next step until the manifest validator passes.
- Do not let a new chat, handoff, current-state file, review marker, or notification bypass unresolved questions or either manifest.
- Keep the output practical and clear for a non-technical user.
```
