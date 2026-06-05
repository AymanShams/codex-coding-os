---
name: crisis-command-center
description: "Use when the user asks to handle, triage, coordinate, communicate, log, investigate, contain, recover from, or review an active or potential crisis, incident, emergency, operational disruption, service outage, patient or customer safety issue, security incident, data incident, privacy incident, regulator-sensitive event, media risk, reputational risk, executive escalation, board escalation, public complaint, partner escalation, or urgent organizational threat. Use for severity classification, command roles, escalation paths, incident logs, evidence logs, decision logs, holding statements, stakeholder updates, recovery checklists, crisis playbooks, and after-action reviews. Do not use for routine PR, generic marketing, normal legal drafting, non-urgent operational documents, ordinary stakeholder updates, or generic critique. If the primary task is routine investor or board communication, use board-update. If the primary task is stakeholder mapping without an active incident, use stakeholder-map. If the primary task is reviewing contract clauses, use contract-review. If the primary task is drafting post-crisis SOPs or controlled procedures, use ssot-drafter or process-docs after the crisis response is stable."
---

# Crisis Command Center

Use this skill to produce controlled, evidence-based crisis response work. Optimize for safety, speed, facts, accountability, and calm execution.

## Core Rules

- Separate verified facts, inferences, assumptions, and speculation.
- Do not invent facts, numbers, dates, legal duties, clinical duties, or regulatory requirements.
- Treat legal, regulatory, clinical, security, and data incidents as review-required by the proper owner.
- For healthcare contexts, avoid medical advice. Escalate patient safety and clinical judgment to licensed clinical leadership.
- Preserve evidence with timestamps, source, owner, confidence level, and verification status.
- Keep external statements short, factual, and approved by the designated decision owner.
- Do not assign blame during response. Focus on containment, affected parties, decisions, and next update time.

## Activation Triage

Classify severity before drafting detailed work. If uncertain during the first response window, choose the higher severity and downgrade later.

| Severity | Use When | Response Posture |
|---|---|---|
| P0 | Active harm, patient/customer safety risk, major outage, data exposure, regulator-sensitive event, public media risk, or executive/board-level threat | Activate command structure immediately, contain harm, create holding statement, begin incident log |
| P1 | High-impact disruption, credible complaint, partner escalation, reputational risk, or compliance concern with unclear scope | Assign commander, verify facts, prepare stakeholder updates, define decision cadence |
| P2 | Limited incident with known scope and no active harm | Track actions, communicate to affected internal owners, monitor escalation triggers |
| P3 | Early warning, near miss, rumor, or weak signal | Log, investigate, define threshold for escalation |

## Command Structure

Assign named owners when possible:

- Incident commander: owns coordination, priorities, cadence, and final response brief.
- Evidence owner: maintains fact base, source trail, incident log, and open questions.
- Operations owner: owns containment, service continuity, customer/patient impact, and recovery actions.
- Communications owner: drafts internal and external messages for approval.
- Legal/compliance owner: reviews regulator, contract, legal, privacy, employment, or liability exposure.
- Subject-matter owner: provides clinical, technical, security, finance, HR, or product judgment.
- Executive approver: approves high-risk decisions and external statements.

If the user has not provided roles, propose a provisional structure using functions instead of names.

## Response Workflow

1. Stabilize first:
   - Identify immediate harm or risk.
   - Stop the bleeding: pause risky activity, protect people, protect data, protect systems, preserve records.
   - Define what must happen in the next 30 minutes, 2 hours, and 24 hours.

2. Build the fact base:
   - Create an incident log with time, source, claim, status, confidence, owner, and next verification step.
   - List known facts, unknowns, assumptions, and decisions already made.
   - Mark unsupported claims as unverified.

3. Map affected stakeholders:
   - Internal: leadership, managers, frontline teams, legal/compliance, finance, HR, security, operations.
   - External: customers, patients, caregivers, vendors, partners, regulators, media, investors, board.
   - For each group, state what they need to know, who owns contact, channel, timing, and approval path.

4. Control communications:
   - Draft a holding statement if facts are incomplete.
   - Keep messages factual: what happened, who may be affected, what is being done, what to do now, next update time.
   - Avoid speculation, blame, unsupported promises, legal conclusions, medical advice, and admissions of liability.
   - Use plain language and define technical terms.

5. Track decisions and actions:
   - Record decision, owner, deadline, evidence used, risk accepted, and reversal trigger.
   - Separate must-do containment actions from monitoring actions.
   - Escalate blocked decisions quickly.

6. Recover and prevent recurrence:
   - Confirm containment.
   - Define corrective actions, owners, deadlines, evidence of completion, and monitoring period.
   - Identify needed policy, SOP, training, technical, staffing, vendor, or governance changes.

7. Close with an after-action review:
   - Produce timeline, root cause analysis, what worked, what failed, missed signals, impact, corrective actions, and owner/date commitments.
   - Distinguish root cause from contributing factors.
   - Archive the evidence log and approved communications.

## Output Templates

For an active crisis, produce:

```markdown
# Crisis Command Brief: [Name]

## Severity
[P0/P1/P2/P3 and reason]

## Current Decision
[What needs to be decided now]

## Verified Facts
- [Fact, source, timestamp]

## Unverified Claims / Assumptions
- [Claim, why unverified, next verification step]

## Immediate Risks
- [Risk, affected party, likely impact, urgency]

## Command Roles
| Role | Owner | Backup | Notes |
|---|---|---|---|

## Actions
| Action | Owner | Due | Status | Evidence Needed |
|---|---|---:|---|---|

## Stakeholder Communications
| Stakeholder | Message Needed | Owner | Channel | Timing | Approval |
|---|---|---|---|---|---|

## Holding Statement
[Short factual statement with next update time]

## Open Questions
- [Question, owner, deadline]
```

For a crisis plan, produce:

```markdown
# Crisis Response Playbook: [Organization / Scenario]

## Activation Criteria
## Severity Levels
## Command Roles
## Escalation Path
## Evidence Log Procedure
## Stakeholder Map
## Communication Approval Workflow
## Message Templates
## Decision Log
## Recovery Checklist
## After-Action Review Template
## Drill / Training Plan
```

For a post-crisis review, produce:

```markdown
# After-Action Review: [Incident]

## Executive Summary
## Timeline
## Impact
## Root Cause
## Contributing Factors
## What Worked
## What Failed
## Missed Signals
## Corrective Actions
## Owners And Deadlines
## Evidence Archive
```

## Relationship To Other Skills

- Use this skill as the crisis coordination layer.
- Use evidence-review discipline when source quality, claims, or citations matter.
- Use security skills for security, privacy, data, or infrastructure incidents.
- Use contract-review only for reviewing contract terms or legal-risk language, not as a substitute for counsel.
- Use SSOT drafting or process-document skills after the crisis to turn lessons into controlled procedures.
