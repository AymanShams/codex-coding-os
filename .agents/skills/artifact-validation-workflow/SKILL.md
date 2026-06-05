---
name: artifact-validation-workflow
description: Use when the user asks to validate, acceptance-check, quality-gate, readiness-review, approve, sign off, submit-check, publish-check, handoff-check, archive-check, or decide whether an artifact is ready to use. Use for SOPs, policies, reports, decks, strategy documents, project plans, dossiers, implementation guides, templates, evidence packs, data specs, academic or professional deliverables, and controlled artifacts where acceptance criteria, evidence requirements, pass/fail verdicts, defect logs, required fixes, decision-needed reports, re-validation steps, or readiness recommendations matter. Use when the user asks "is this ready", "can this be approved", "what blocks submission", "make a validation checklist", "create acceptance criteria", "log defects", "give a pass/fail verdict", or "what evidence is required". Do not use for source code behavior review unless the artifact being reviewed is a document or controlled deliverable. If the primary task is broad skeptical critique of reasoning, use deep-critic. If the primary task is SSOT-style artifact completeness and operational quality without a pass/fail acceptance workflow, use ssot-auditor. If the primary task is source, citation, or claim verification, use evidence-checker. If the primary task is designing the surrounding artifact system, use artifact-system-designer. If failed validation requires delivery sequencing or remediation planning, use wbs-artifact-planner.
---

# Artifact Validation Workflow

Use this skill to decide whether an artifact is ready to use, approve, submit, publish, hand off, or archive. The output should expose evidence, defects, risks, missing decisions, and exact fixes.

## Use When

- The user asks to validate, audit, review, acceptance-check, quality-gate, or approve an artifact.
- The artifact is a document, deck, SOP, policy, plan, report, dossier, template, evidence pack, spec, or deliverable.
- The user needs a checklist, verdict, defect log, pass or fail decision, or readiness report.
- The artifact has downstream operational, strategic, academic, governance, legal, or client-facing consequences.

## Do Not Use When

- The user asks for source code review. Use code review methods instead.
- The user asks for general critique only. Use `deep-critic` unless an acceptance workflow is also needed.
- The artifact is still early brainstorming and no readiness decision is required.

## Related Skills

- Use `deep-critic` for adversarial critique of logic, evidence, and assumptions.
- Use `ssot-drafter` to fix high-stakes operational artifacts after validation.
- Use `dossier-builder` when the validation reveals missing source facts.
- Use `wbs-artifact-planner` when failed validation requires a remediation plan.

## Validation Layers

Check the artifact through seven layers:

1. Scope: does it answer the actual request and audience need?
2. Source chain: are facts, data, claims, and assumptions traceable?
3. Structure: is the format fit for purpose?
4. Completeness: are required sections, fields, roles, steps, or evidence present?
5. Consistency: do terms, numbers, dates, statuses, owners, and cross-references align?
6. Usability: can the target user act on it without hidden knowledge?
7. Risk: what could fail if this artifact is used as-is?

## Minimum Context Gate

Use available files and conversation first. Ask only for missing information that would materially change the verdict.

Required context:

1. Artifact type.
2. Intended audience and use.
3. Acceptance standard, rubric, policy, prompt, approval criteria, or business goal.
4. Source files or evidence base.
5. Required verdict format: pass, pass with fixes, fail, decision-needed, or remediation plan.

If no standard is provided, build a reasonable standard from the artifact type and label it as assumed.

## Workflow

1. Identify artifact type and acceptance standard.
2. Inventory sources and evidence.
3. Build the validation checklist.
4. Review the artifact against each validation layer.
5. Record defects with severity and exact location when possible.
6. Separate required fixes from optional improvements.
7. Give a verdict.
8. Provide a remediation plan.
9. If appropriate, define re-validation steps.

## Severity Scale

| Severity | Meaning | Required action |
|---|---|---|
| Blocker | Artifact should not be used | Fix before use |
| Major | Material risk or missing requirement | Fix before approval |
| Minor | Quality issue that does not block use | Fix if time allows |
| Note | Observation or future improvement | Track only |

## Verdict Scale

- Pass: ready as-is.
- Pass with minor fixes: usable after minor edits.
- Conditional pass: usable only if named assumptions are accepted.
- Fail: not ready because material requirements are missing.
- Decision-needed: cannot validate until a human decision is made.

## Required Output Sequence

1. Direct verdict.
2. Acceptance standard used.
3. Findings by severity.
4. Required fixes.
5. Optional improvements.
6. Re-validation steps.
7. Assumptions and confidence.
8. Sources checked.

## Validation Report Template

```markdown
# Artifact Validation Report: [Artifact Name]

## Direct Verdict
[Pass, pass with minor fixes, conditional pass, fail, or decision-needed.]

## Acceptance Standard
[Provided standard or assumed standard.]

## Sources Checked
- [File, page, section, URL, prompt, rubric, policy, or evidence source]

## Findings
| Severity | Location | Issue | Why it matters | Required fix |
|---|---|---|---|---|
| Blocker |  |  |  |  |
| Major |  |  |  |  |
| Minor |  |  |  |  |

## Checklist
| Layer | Pass | Evidence | Notes |
|---|---|---|---|
| Scope | Yes or No |  |  |
| Source chain | Yes or No |  |  |
| Structure | Yes or No |  |  |
| Completeness | Yes or No |  |  |
| Consistency | Yes or No |  |  |
| Usability | Yes or No |  |  |
| Risk | Yes or No |  |  |

## Required Fixes
1. [Fix]
2. [Fix]

## Optional Improvements
1. [Improvement]

## Re-Validation Steps
1. [Step]
2. [Step]

## Assumptions and Confidence
[High, Medium, or Low, with reason.]
```

## Failure Modes

- The review says "looks good" without acceptance criteria.
- Style comments distract from blockers.
- Missing source evidence is treated as a wording issue.
- A fail verdict is avoided even when the artifact is not usable.
- The reviewer asks for broad rewrites instead of exact fixes.
- The report does not say what to do next.
