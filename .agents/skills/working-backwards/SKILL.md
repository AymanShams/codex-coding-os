---
name: working-backwards
description: Use when the user asks to create an Amazon-style Working Backwards artifact or serious written decision artifact before discussion. Use for PR/FAQ, PRFAQ, press release FAQ, internal press release, six-page narrative memo, narrative decision memo, one-way door versus two-way door decision framing, memo-read meeting protocol, decision log, customer-backward product or service design, replacing slides with a memo, or a major product, strategy, operating model, pricing, governance, board, or executive approval decision that needs written reasoning before debate. Use when the user says working backwards, Amazon method, write the decision memo, draft the PR/FAQ, start from the customer outcome, or make this board-grade before discussion. Do not use for routine status updates, simple PRDs, feature specs, lightweight SOPs, casual brainstorming, small reversible approvals, generic strategy notes, or generic mental-model explanations. If the primary task is a PRD or feature requirements document, use create-prd. If the primary task is product direction or strategy canvas work without a decision memo, use product-strategy. If the primary task is failure-first risk testing, use pre-mortem. If the primary task is delivery sequencing, roadmap, dependency tree, or WBS, use wbs-artifact-planner. If the primary task is a controlled SOP, policy, workflow, or governance artifact, use ssot-drafter.
---

# Working Backwards

Use this skill to turn a product idea or major decision into a written decision artifact before discussion. The output should expose customer value, evidence, assumptions, options, risks, owners, metrics, and the exact decision needed.

## Format Selector

Choose the lightest format that can safely carry the decision.

| Situation | Format |
|---|---|
| New product, service, feature, offer, module, or customer experience | PR/FAQ |
| Complex decision, operating model, pricing change, governance change, board decision, or strategic approval | Six-page narrative memo |
| Small reversible approval | One-page decision note, unless the user explicitly asks for the full method |
| Root-cause or operational failure diagnosis | A3, RCA, premortem, or process-doc skill may fit better |
| Technical architecture choice | Architecture Decision Record may fit better |
| Routine status update | Do not use this skill |

If another specialist skill is clearly required, combine lenses explicitly. Examples: use `pricing-strategy` for pricing logic, `pre-mortem` for failure-first risk testing, `ssot-drafter` for controlled SOPs or policies, and `quant-review` for numerical models.

Use `storyscope-structural-audit` as a companion lens when the memo, PR/FAQ, or launch narrative risks sounding inevitable, generic, too tidy, or detached from the customer's real sequence of pressure, failed workaround, trigger, decision, tradeoff, and consequence.

## Minimum Context Gate

Before drafting, use available files and conversation first. Ask only for missing information that would materially change the artifact.

Required context:

1. What is being proposed or decided?
2. Who is the target customer, user, stakeholder, or decision owner?
3. What decision is needed now?
4. What evidence is available?
5. What would success look like, including metrics or observable outcomes?

If evidence is weak, say so inside the artifact. Do not write a polished narrative that hides factual uncertainty.

## Workflow

1. Select the format.
2. State known facts, inferences, assumptions, and open questions.
3. Write the artifact in plain language.
4. Include options rejected and why they were rejected.
5. Classify the decision as one-way door or two-way door.
6. Name the single accountable owner.
7. Add risks, controls, metrics, and follow-up cadence.
8. End with the exact decision requested from the reader or meeting.

## PR/FAQ Template

Use PR/FAQ when designing from the desired customer outcome backward.

```markdown
# PR/FAQ: [Product, Service, Feature, or Offer]

## Direct Answer
[One paragraph stating whether this should proceed, what should be built first, and the main condition for approval.]

## Known Facts, Inferences, Assumptions, and Open Questions
| Type | Item | Evidence or Source | Decision Impact |
|---|---|---|---|
| Fact |  |  |  |
| Inference |  |  |  |
| Assumption |  |  |  |
| Open question |  |  |  |

## Internal Press Release

### Headline
[Customer-facing launch claim.]

### Subheadline
[One sentence explaining the core benefit.]

### Date and Location
[Future launch date and market, if relevant.]

### Customer Problem
[Plain-language pain point and who has it.]

### Launch Announcement
[What is launching.]

### Customer Benefit
[Why this is meaningfully better, faster, easier, cheaper, safer, or more reliable.]

### How It Works
[Customer journey in plain language.]

### Customer Quote
[Realistic target-customer quote.]

### Company Quote
[Realistic leadership quote explaining strategic intent.]

### Availability and Pricing
[Who can access it, where, when, and at what price.]

## Customer FAQ
1. Who is this for?
2. What problem does it solve?
3. How is this different from current alternatives?
4. What does it cost?
5. What is included?
6. What is excluded?
7. What does the customer need to do?
8. What happens if the customer is dissatisfied?
9. What data is collected, stored, or shared?
10. How does the customer stop, change, or cancel?

## Internal FAQ
1. What decision is needed today?
2. What customer evidence supports this?
3. What alternatives were rejected and why?
4. What are the minimum launch features?
5. What are the major operational dependencies?
6. What are the technology dependencies?
7. What are the compliance, privacy, legal, or brand risks?
8. What are the financial assumptions?
9. What could make this fail?
10. Is this a one-way-door or two-way-door decision?
11. Who is the single-threaded leader?
12. What metrics will prove success or failure?

## Recommendation
[Proceed, revise, pilot, pause, or reject.]

## Approval Request
[Exact decision requested, owner, next step, and follow-up date.]
```

## Six-Page Narrative Memo Template

Use this for major decisions where the question is what to decide, change, fund, approve, or stop.

```markdown
# Six-Page Narrative Memo: [Decision Name]

## Direct Answer
[One paragraph with the recommendation, decision needed, and main risk.]

## 1. Decision Required
[State the exact decision needed. Include owner, deadline, and whether this is a one-way-door or two-way-door decision.]

## 2. Executive Summary
[Recommendation, why now, expected impact, required resources, and main risks.]

## 3. Current Situation
[Current state, affected stakeholders, relevant data, constraints, and why the current approach is insufficient.]

## 4. Problem Analysis
[Root causes, operational drivers, financial drivers, customer impact, and evidence. Separate facts from assumptions.]

## 5. Options Considered
| Option | Pros | Cons | Cost | Risk | Decision |
|---|---|---|---|---|---|
| A |  |  |  |  | Accepted or rejected because... |
| B |  |  |  |  | Accepted or rejected because... |
| C |  |  |  |  | Accepted or rejected because... |

## 6. Recommended Approach
[Proposed solution, operating model, roles, process changes, technology changes, dependencies, and decision rights.]

## 7. Risks and Mitigations
| Risk | Evidence or Logic | Likelihood | Impact | Mitigation | Owner |
|---|---|---:|---:|---|---|

## 8. Execution Plan
| Workstream | Owner | Start | Due | Output |
|---|---|---|---|---|

## 9. Success Metrics and Warning Signals
| Metric or Signal | Baseline | Target or Threshold | Frequency | Owner |
|---|---:|---:|---|---|

## 10. Open Questions
[Unresolved issues that need leadership direction.]

## Appendix
[Use for charts, data tables, screenshots, process maps, financial model extracts, legal references, or architecture diagrams.]
```

## Meeting Protocol

Use this protocol when the user asks how to run the decision meeting.

1. Send the memo only if readers have enough time to read it seriously.
2. At the meeting, reserve silent reading time before discussion.
3. Ask participants to mark questions, objections, factual gaps, and decision blockers.
4. Discuss the written argument, not the author.
5. Resolve objections explicitly.
6. Record decision, dissent, owner, due dates, success metrics, and follow-up review date.
7. If no decision is made, record the blocker and the evidence needed to unblock it.

## Quality Bar

- Do not let the artifact become slide-deck thinking in paragraph form.
- Prefer evidence and causal logic over polished wording.
- Do not bury the decision request.
- Do not use PR/FAQ when the product outcome is vague.
- Do not use a six-page memo for routine work.
- Do not let AI-generated fluency hide weak facts, missing ownership, or operational reality.
- Do not make the customer journey too clean. Show the current workaround, real constraint, decision point, rejected alternative, and remaining risk when they affect the decision.
- If external claims about Amazon or another company matter to the answer, verify them live from primary sources when browsing is available.
