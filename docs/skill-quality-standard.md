# First-Party Skill Quality Standard

Apply this standard to new first-party skills and material changes to existing first-party skills. External or bundled upstream skills keep their own structure unless a local overlay is explicitly maintained.

## Admission Rule

A skill must close a demonstrated capability gap, prevent a recurring failure mode, or materially improve verification. Do not add a skill that only restates existing instructions or changes tone.

## Required Definition

Every first-party skill must define:

- precise trigger and non-trigger conditions
- controlling inputs and source hierarchy
- owned outputs
- procedure and routing boundaries
- material decisions it must not assume
- stop, escalation, and handoff conditions
- validation and completion criteria
- non-goals and adjacent skill ownership

## Design Rules

- Keep one primary owner for each workflow phase or detailed template.
- Route to existing specialist skills instead of duplicating their procedures.
- Keep examples generic and portable.
- Keep coordination artifacts subordinate to controlled sources.
- Prefer deterministic scripts for repeatable checks.
- Treat unavailable checks as reported gaps, not implicit passes.
- Keep root `AGENTS.md` concise. Put folder-specific rules in scoped `AGENTS.md` files, detailed procedures in skills or docs, and repeatable checks in scripts or hooks.
- Do not add broad prompt packs, persona rules, or roleplay instructions unless they close a verified coding failure mode.
- Prefer a pointer to the owning source over copied detail when the copied detail would drift.

## Validation

Before accepting a new or materially changed first-party skill:

1. Confirm the capability delta.
2. Check overlap with the catalogue and adjacent skills.
3. Verify frontmatter and referenced files.
4. Exercise scripts against passing and failing cases.
5. Confirm stop gates fail closed.
6. Check that outputs and completion claims match the skill contract.
7. Test trigger behavior with one obvious trigger, one paraphrase trigger, one unrelated negative case, and one adjacent-skill negative case.
8. Update the pack inventory, routing catalogue, and manifest.

## Removal Or Consolidation

Retire or merge a skill when its capability is fully owned elsewhere, its procedures drift from the controlling workflow, or its maintenance cost exceeds its verified value.
