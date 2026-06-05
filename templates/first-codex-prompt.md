# First Codex Prompt

Paste this into a new Codex chat after installing the pack.

```text
Use $codex-coding-os-master.

I am starting or continuing a Codex coding project. I do not want to start coding from a vague idea or weak context. I want you to help me turn the idea or existing repo into a PRD, a technical design, repo instructions, and a first implementation slice.

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
1. Ask only the questions needed to avoid building the wrong product.
2. Explain any technical terms in simple language.
3. Create a project brief first.
4. Then create these source-controlled docs:
   - PRD
   - app-flow-doc
   - tech-stack-doc
   - frontend-guidelines
   - backend-structure
   - security-guidelines
   - implementation-plan
5. Create a TDD technical design document after the docs are stable.
6. Create root and scoped AGENTS.md instructions for the repo.
7. Recommend the first vertical implementation slice.
8. Do not start implementation until I approve the docs and first slice.

Rules:
- Do not assume paid services unless I approve them.
- Do not store secrets in code.
- Do not invent requirements.
- If you are missing a critical answer, ask me.
- If a reasonable assumption is safe, state it and proceed.
- Keep the output practical and clear for a non-technical user.
```
