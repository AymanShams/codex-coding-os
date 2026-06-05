---
name: pre-mortem
description: "Use when the user asks to run a premortem or failure-first risk analysis on a concrete plan, launch, product, pricing change, hire, partnership, strategy, SOP, operating model, implementation, project, rollout, or high-cost decision before or during execution. Use for requests like stress test this plan, what could kill this, what could go wrong, find blind spots, what am I missing, where will this break, future-proof this, poke holes in this plan, identify failure modes, pressure-test before launch, reduce execution risk, or revise the plan to avoid failure. The skill assumes the initiative has failed in the future, works backward to find real causes, then produces hidden assumptions, warning signs, mitigation actions, and a revised plan. Do not use for simple factual questions, vague ideas without a concrete plan, ordinary critique of an existing argument, lightweight feedback, creative editing, grammar review, or decisions that are already irreversible. If the primary task is skeptical review of an artifact or recommendation, use deep-critic. If the primary task is reviewing an SSOT-style SOP, policy, workflow, governance document, or controlled artifact, use ssot-auditor. If the primary task is acceptance criteria, readiness checklist, defect log, evidence requirements, or pass/fail approval, use artifact-validation-workflow. If the primary task is building a delivery plan, dependency tree, roadmap, or WBS, use wbs-artifact-planner."
---

# Premortem: Failure-First Risk Analysis

## Purpose

Run a premortem by assuming the user's plan has already failed, then working backward to identify why. The goal is not generic risk listing. The goal is to expose the specific assumptions, constraints, blind spots, and execution gaps that could kill the plan while there is still time to change it.

Use this skill for product launches, business plans, pricing changes, go-to-market plans, hiring decisions, partnerships, operating models, SOPs, governance artifacts, strategy choices, and any commitment where being wrong would be costly.

## Trigger Guidance

Strong triggers include:

- "premortem this"
- "run a premortem"
- "what could kill this"
- "stress test this plan"
- "find the blind spots"
- "what am I missing here"
- "what could go wrong"
- "poke holes in this"
- "where will this break"
- "future-proof this"

Do not use this skill for:

- Simple factual questions with one correct answer
- Vague ideas without a concrete plan yet
- General writing feedback or creative critique
- Decisions that are already irreversible
- Requests that need a different specialist skill first, such as contract review, quant review, security threat modeling, or evidence checking

If another specialist skill is clearly needed, use that skill first or combine lenses explicitly.

## Minimum Context Gate

Before running the premortem, gather enough context to avoid generic output. Use available conversation and files first. Ask only for missing critical information.

You need three things:

1. What is being premortemed?
   - Summarize the plan, launch, decision, or artifact in one sentence.

2. Who is affected?
   - Identify the customer, user, team, stakeholder, regulator, partner, or decision owner.

3. What does success mean?
   - Identify the expected outcome, metric, deadline, acceptance standard, or decision result.

If one of these is missing and cannot be inferred, ask one focused question before continuing. Do not make the user fill out a long form.

## Workflow

1. Set the failure frame.
   - State the premise clearly: it is a defined future point, usually 3 to 6 months from now, and the plan has failed.
   - Work backward from failure instead of asking whether the plan is good.

2. Generate failure reasons.
   - List every genuine failure mode that is specific to this plan.
   - Do not pad with weak risks.
   - Do not stop early if more real failure modes exist.
   - Ground each reason in the user's actual context, evidence, constraints, or missing information.

3. Classify each failure mode.
   - Tiger: a real risk supported by evidence, logic, precedent, or clear dependency exposure.
   - Paper Tiger: a concern that sounds plausible but is likely overblown or lower priority.
   - Elephant: an unspoken or under-tested assumption that may become a real risk.

4. Prioritize Tigers.
   - Launch-blocking: must be resolved before execution.
   - Fast-follow: can proceed only if there is a clear owner and near-term follow-up.
   - Track: monitor with a defined signal or threshold.

5. Identify the deeper pattern.
   - Most likely failure: the scenario most probable based on current evidence.
   - Most dangerous failure: the scenario with the highest damage if it happens.
   - Hidden assumption: the single biggest assumption the user is making without enough validation.

6. Revise the plan.
   - Convert the analysis into concrete changes the user can make.
   - Each change must map to a failure mode.
   - Prefer specific tests, owners, dates, thresholds, and decision gates over generic advice.

7. Add warning signs.
   - For major failure modes, name observable signals that show the failure may be starting.
   - Signals should be measurable or directly inspectable, not vague feelings.

## Subagents and Files

Do not spawn subagents by default. Use subagents only when the user explicitly asks for parallel agent work or when the current environment instructions allow delegation and the task is large enough to benefit from independent deep dives.

Do not create files by default. If the user asks for a durable artifact, save a Markdown report. If the user asks for a visual report, create a self-contained HTML report. Do not open generated files or browser windows unless the user asks.

## Output Format

Start with the decision-level answer, then the analysis.

```markdown
## Premortem: [Plan or Decision Name]

### Direct Answer
[One short paragraph with the main risk judgment and the most important change.]

### Premise
It is [timeframe] from now. [Plan] has failed. We are working backward to identify why.

### Most Important Findings
| Type | Finding | Why It Matters | Priority |
|---|---|---|---|
| Most likely failure | [failure] | [logic] | [action priority] |
| Most dangerous failure | [failure] | [logic] | [action priority] |
| Hidden assumption | [assumption] | [logic] | [validation priority] |

### Tigers: Real Risks
| Risk | Evidence or Logic | Priority | Mitigation | Owner | Due Date or Gate |
|---|---|---|---|---|---|

### Paper Tigers: Overblown Concerns
| Concern | Why It Is Lower Priority | What To Monitor |
|---|---|---|

### Elephants: Under-Discussed Assumptions
| Assumption | Why It Could Matter | How To Validate |
|---|---|---|

### Revised Plan
1. [Concrete change mapped to a specific failure mode]
2. [Concrete change mapped to a specific failure mode]
3. [Concrete change mapped to a specific failure mode]

### Pre-Execution Checklist
- [ ] [Specific test, decision gate, owner, or evidence check]
- [ ] [Specific test, decision gate, owner, or evidence check]
- [ ] [Specific test, decision gate, owner, or evidence check]

### Assumptions and Confidence
- Assumptions: [list material assumptions]
- Confidence: High / Medium / Low, based on evidence quality and context completeness
```

## Quality Bar

- Be specific to the plan.
- Separate verified facts, inferences, assumptions, and speculation when factual accuracy matters.
- Challenge evidence quality, recency, sampling bias, incentives, and hidden constraints.
- If context is weak, say so and label the premortem provisional.
- Do not soften serious risks to be agreeable.
- Do not produce a risk catalog without a revised plan.
