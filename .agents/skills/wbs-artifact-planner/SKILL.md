---
name: wbs-artifact-planner
description: Use when the user asks to break down, decompose, sequence, structure, plan, roadmap, or turn a large initiative into a WBS, work breakdown, workstream plan, delivery plan, implementation plan, artifact plan, issue tree, project tree, dependency map, execution roadmap, or node-based plan. Use for strategies, development efforts, documentation programs, governance buildouts, product initiatives, audits, implementations, research programs, artifact creation programs, and multi-deliverable work where nodes, dependencies, deliverables, validation methods, owners, statuses, acceptance gates, or execution order matter. Use when the user asks for GitHub issues, Linear issue structure, task contracts, delivery sequencing, work packages, milestones, acceptance gates, or a traceable plan that can be executed and reviewed. Do not use for casual todo lists, single-document drafting, ordinary brainstorming, generic strategy notes, or simple next-step advice. If the primary task is a PRD or feature requirements document, use create-prd. If the primary task is a serious written decision memo or PR/FAQ, use working-backwards. If the primary task is failure-first risk testing, use pre-mortem. If the primary task is a controlled SOP, policy, workflow, or governance artifact, use ssot-drafter. If the primary task is designing the surrounding artifact system, folder structure, metadata, lifecycle, or source-of-truth model, use artifact-system-designer. If the primary task is validation checklist, defect log, evidence requirements, or pass/fail readiness, use artifact-validation-workflow.
---

# WBS Artifact Planner

Use this skill to convert a large initiative into a traceable work tree. WBS means Work Breakdown Structure. Each node should represent a real work outcome, not a vague activity.

## Use When

- The user asks for a WBS, work breakdown, roadmap, implementation plan, artifact plan, delivery tree, issue tree, or project decomposition.
- The work requires dependencies, deliverables, validation methods, and status tracking.
- The user wants to plan development, strategy, operations, governance, documentation, research, audits, or artifact creation.
- The user needs a GitHub issue tree, Linear project tree, local markdown plan, or execution-ready breakdown.

## Do Not Use When

- The user only needs a simple checklist.
- The user needs a PRD. Use `create-prd` and chain this skill only if execution planning is needed.
- The user needs an SOP. Use `process-docs` or `ssot-drafter`.
- The user needs a board memo or PR/FAQ. Use `board-update` or `working-backwards`.

## Related Skills

- Use `artifact-system-designer` before this when the repository or artifact operating model is unclear.
- Use `artifact-validation-workflow` after this when each node needs readiness checks.
- Use `pre-mortem` to stress test high-cost plans.
- Use specialist skills for the actual deliverables after the WBS is stable.

## Planning Rules

1. Use outcome nouns, not activity verbs, for stable WBS node names.
2. Parent nodes define capabilities or artifact groups.
3. Leaf nodes define concrete deliverables that can be reviewed.
4. Every leaf must include outputs and validation.
5. Dependencies must be explicit.
6. Do not treat dates as structure. Dates belong in fields.
7. Do not create parallel structures for the same plan unless the user needs both.

## Minimum Context Gate

Use available files and conversation first. Ask only for missing information that would materially change the plan.

Required context:

1. Initiative goal.
2. Desired final outputs.
3. Deadline or sequencing constraint, if any.
4. Known workstreams.
5. Validation standard: user review, tests, source evidence, approvals, metrics, or operating readiness.
6. Tooling destination: markdown, GitHub issues, Linear, spreadsheet, local files, or chat.

If details are missing, build a Version 1 plan and mark assumptions.

## Workflow

1. State the planning verdict and overall structure.
2. Define top-level WBS branches.
3. Break each branch into reviewable nodes.
4. For each leaf, define objective, outputs, dependencies, validation, owner, and status.
5. Sequence the work by dependency, not by preference.
6. Identify high-risk nodes and missing decisions.
7. Add acceptance gates.
8. Produce the plan in the requested format.
9. End with the next execution step.

## Default WBS Branches

Adapt these branches to the domain:

| Branch | Purpose |
|---|---|
| WBS 0 | Program charter and control model |
| WBS 1 | Requirements, scope, and success criteria |
| WBS 2 | Data, sources, source-of-truth, or core model |
| WBS 3 | Common mechanisms, templates, or reusable foundations |
| WBS 4 | Primary deliverables or workstreams |
| WBS 5 | Review, validation, testing, or acceptance |
| WBS 6 | Launch, handoff, operating cadence, or training |
| WBS 7 | Reporting, observation, metrics, and continuous improvement |

Rename branches when the user's domain calls for it.

## Required Output Sequence

1. Direct answer: recommended WBS shape.
2. Assumptions and missing context.
3. WBS tree.
4. Node contracts.
5. Dependency order.
6. Validation gates.
7. Risks and failure modes.
8. Next actions ranked by impact.

## WBS Tree Template

```markdown
# WBS Plan: [Initiative]

## Direct Answer
[Recommended structure and first execution priority.]

## Assumptions
- [Assumption]

## WBS Tree
| WBS | Node | Purpose | Status | Depends On |
|---|---|---|---|---|
| 0 | Program control |  | Planned | None |
| 1 | Requirements and scope |  | Planned | 0 |
| 2 | Source model |  | Planned | 1 |
| 3 | Common mechanisms |  | Planned | 2 |
| 4 | Primary deliverables |  | Planned | 3 |
| 5 | Validation and acceptance |  | Planned | 4 |
| 6 | Launch and handoff |  | Planned | 5 |

## Node Contracts

### WBS [x.y] [Node Name]
Objective:
[What this node must achieve.]

Work Content:
1. [Work item]
2. [Work item]

Outputs:
- [File, artifact, feature, decision, or evidence]

Depends On:
- [WBS node or external dependency]

Validation:
- [User-visible check, command, evidence check, review action, or acceptance test]

Current Status:
[Planned, In Progress, For Review, Accepted, Blocked, Deferred]

Open Decisions:
- [Decision needed]

## Dependency Order
1. [Node]
2. [Node]

## Acceptance Gates
| Gate | Required evidence | Owner | Pass condition |
|---|---|---|---|
| Scope freeze |  |  |  |
| Source model accepted |  |  |  |
| Deliverables ready |  |  |  |
| Handoff complete |  |  |  |
```

## Failure Modes

- Parent nodes are too broad to validate.
- Leaf nodes produce discussion instead of artifacts.
- Plans use dates as hierarchy, then break when timing changes.
- Validation is written as "review" without observable pass criteria.
- Dependencies are implied in prose instead of shown in fields.
- High-risk work is hidden under a generic implementation node.
