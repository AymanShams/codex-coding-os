# First Codex Prompt

Paste this into a new Codex task after installing Coding OS.

```text
Use $codex-coding-os-master and $new-project-documentation-system.

Help me turn this idea or existing repository into stable project documentation before implementation.

My idea:
{{write_the_idea_here}}

Known context:
- Repository or source path:
- Target users:
- Problem:
- Desired outcome:
- Product surface:
- Data and integrations:
- Deadline:
- Budget or hosting constraints:
- Explicit non-goals:

Documentation work:
1. Create `project-documentation-manifest.json` as a documentation ledger with `execution_authority: false`.
2. Inventory and classify the sources.
3. Identify source conflicts and ask all material questions before drafting dependent content.
4. Create a project brief.
5. Create the requested product documents, including PRD, app flow, tech stack, frontend guidance, backend structure, implementation plan, and any other source-supported artifact.
6. Treat generated documents as drafts until I approve them.
7. Create a source-locked TDD and alignment review after the controlled documents are approved.
8. Create stage-appropriate repository documentation and root/scoped agent instructions.
9. Run the documentation manifest and filled-artifact validators.
10. Report documentation completion separately from product implementation and campaign status.

Rules:
- Do not invent requirements or material decisions.
- Use assumptions only for reversible presentation details.
- Preserve existing repository work.
- Do not store volatile campaign state in Git.
- Do not treat current-state files, active-slice files, work summaries, reviews, branches, pull requests, or chats as execution authority.
- Do not start implementation until I explicitly request manual coding or approve an exact automated campaign specification digest.

When documentation is ready, ask me to choose:
- Manual implementation from my current request, or
- Automated campaign admission using `python <installed-cli> --json admit --spec <path>`.
```
