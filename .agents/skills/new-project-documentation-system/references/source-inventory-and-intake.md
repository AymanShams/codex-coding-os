# Source Inventory And Intake

Use this reference before drafting any project documentation from existing files, chat exports, templates, or folders.

## Source Classes

Classify every source as one of:

| Class | Meaning | Treatment |
|---|---|---|
| Controlling source | Approved project truth or explicit user decision | Use as authority |
| Reference template | Structure, not content authority | Use for coverage only |
| External draft | Output from another AI, consultant, or team | Compare and correct before merging |
| Evidence/source material | Raw research, files, transcripts, screenshots, exports | Extract facts, preserve source chain |
| Media asset | Images, video, audio, logos, diagrams | Inventory, inspect only when relevant |
| Restricted/sensitive | PHI, secrets, credentials, personal files, pilot data | Do not copy to Git or public outputs |
| Historical | Older names, superseded decisions, archived docs | Use only to detect drift or explain history |

## Source Manifest

Create a manifest with:

- path
- file type
- extracted text status
- source class
- project area
- sensitivity flag
- notes on authority or conflicts

## Minimum Intake Questions

Ask only questions that materially change the work:

1. Official project or product name.
2. Company context and owner.
3. V1 scope and out-of-scope boundaries.
4. Target users and roles.
5. Source-of-truth folder and final output path.
6. Required formats: Markdown, DOCX, repo files, or all.
7. Whether application code is allowed now.
8. Identity, MFA, roles, and access model.
9. Data privacy, encryption, PHI, audit, and retention rules.
10. Integrations and external systems.
11. AI features, autonomy level, and human approval gates.
12. Development stack, deployment path, and migration constraints.
13. Whether GitHub, Supabase, Vercel, or other services should be created now.

If the answer can be inferred safely from sources, state the assumption and proceed.

## Material Decision Stop Rule

Do not infer answers about scope, users, roles, workflows, approvals, data, PHI, identity, security, integrations, AI, stack, deployment, repositories, external services, output location, formats, or coding permission.

If any source conflicts or any answer is missing in those categories:

1. Create or update the decision register.
2. Set the material-decision phase to `blocked`.
3. Ask all material questions in one numbered request.
4. Wait for the user before drafting the PRD.

The “draft Version 1 with assumptions” option applies only when no material decision is missing.

## Drafting Rule

Do not draft from memory when current source folders are available. Use memory only for routing and prior-context hints, then verify against current files.
