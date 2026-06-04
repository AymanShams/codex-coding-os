# Scoped AGENTS.md

Use this template inside a major folder such as `apps/web`, `apps/api`, `packages/ui`, `packages/core`, `supabase`, or `docs`.

## Scope
These instructions apply to everything under this folder.

## Responsibility
{{describe_what_this_folder_owns}}

## Read First
- {{doc_or_file_1}}
- {{doc_or_file_2}}

## Do
- Keep changes inside this folder unless the task requires cross-folder work.
- Preserve public interfaces unless the calling code is updated.
- Add or update tests when behavior changes.
- Keep examples and docs aligned with implementation.

## Do Not
- Do not store secrets.
- Do not duplicate logic owned by another folder.
- Do not change generated files by hand unless the project documents say to.
- Do not bypass root `AGENTS.md`.

## Validation
```powershell
{{folder_specific_validation_command}}
```

