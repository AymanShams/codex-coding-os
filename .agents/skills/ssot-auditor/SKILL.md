---
name: ssot-auditor
description: Use when the user asks to review, audit, critique, check, assess, validate, gap-check, pressure-test, improve, or find issues in an existing SSOT-style or high-stakes operational artifact. Use for SOPs, policies, playbooks, operating models, workflows, governance documents, legal or contract drafts, process maps, implementation guides, RACI or DACI matrices, controlled templates, and formal company artifacts where completeness, company fit, roles, approvals, controls, evidence, escalation, records, execution steps, or source-of-truth quality matter. Use when the user asks whether an artifact is strong, complete, usable, board-ready, audit-ready, implementation-ready, or missing anything. Do not use for drafting from scratch, lightweight notes, casual summaries, ordinary prose review, or grammar-only editing. If the primary task is creating or substantively rewriting the artifact, use ssot-drafter. If the primary task needs acceptance criteria, validation checklist, defect log, evidence requirements, or a pass/fail readiness recommendation, use artifact-validation-workflow. If the primary task is designing the surrounding document system, folder structure, metadata, naming, lifecycle, or governance library, use artifact-system-designer.
---

Audit the artifact against the same standard used for SSOT drafting.

For SSOT-style and high-stakes operational artifacts, the required standard is output aligned to the specified user company’s scope, culture, systems, and workflows, or all specified companies if explicitly requested, with complete, fully fleshed-out, granular, step-by-step instructions down to single-click level.

If the user asks for a broad source-backed critique, recurring governance failure analysis, stress test, premortem, or the same 12-step structure, use `deep-critic` full mode with SSOT audit checks as a support lens.

## Required audit checks
1. Does the artifact clearly fit its stated type, such as policy, process, SOP, work instruction, implementation guide, operating model, governance artifact, job aid, template, or matrix?
2. Is it aligned to the company’s actual systems, workflows, roles, approvals, and constraints rather than generic filler?
3. Are owners, approvers, controls, escalation, evidence requirements, records, inputs, outputs, interfaces, and service levels explicit where relevant?
4. Are there vague instructions, hidden assumptions, missing decision rights, missing records, missing approvals, or missing exception handling?
5. Is the level of granularity sufficient to execute step by step down to single-click level where relevant?

## Required output sequence
1. Direct verdict
2. Critical gaps
3. Medium gaps
4. Hidden assumptions and failure modes
5. Exact fixes
6. Finalization questions, only if still necessary
