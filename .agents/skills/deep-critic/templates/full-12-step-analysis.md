# Full 12-Step Critique Template

Use this template for deep analysis, source-backed critique, recurring failure analysis, workflow audit, stress test, premortem, or when the user asks for the same 12 steps.

Keep the answer direct and source-backed. Do not add framework jargon for decoration. Keep headings unless the user asks for another structure.

## 1. Direct Verdict

State whether the proposal, workflow, artifact, rule, strategy, code approach, or decision is sound, unsafe, incomplete, overcorrecting, underpowered, or blocked.

## 2. Source Inventory

List the files, repos, PRs, documents, data, pages, logs, screenshots, or other surfaces inspected.

For each source, state:
- path, URL, PR number, or identifier
- current branch and HEAD when relevant
- whether it is controlling, supporting, template-only, stale, advisory, or unverified
- any access or verification limits

## 3. Reconstruct The System Or Causal Chain

Explain how the issue, workflow, decision, or failure mode actually works.

Separate:
- root cause
- contributing factors
- escape point
- detection failure
- incentives or feedback loops

## 4. Challenge Assumptions

Dismantle the candidate logic.

Ask:
- What would make this false?
- What hidden assumptions does it rely on?
- What evidence is missing?
- What is being overgeneralized from one example?
- What source or stakeholder would most improve confidence?

## 5. Rebuild The Rule, Recommendation, Or Model

Propose the smallest stronger version that solves the real problem without creating avoidable new problems.

Include:
- recommended wording or decision rule
- scope
- non-goals
- owner or authority source
- when to escalate

## 6. Simulate Normal Cases

Build a matrix of normal cases. For each case, state:
- condition
- correct response
- review or approval need
- evidence required
- forbidden response

## 7. Simulate The Opposite Risk

Assume the rule, recommendation, or process fails in the opposite direction.

Explain:
- what would be skipped
- how bugs, risk, security gaps, architecture drift, financial loss, customer harm, or operational failure could compound
- what signal would have caught it earlier

## 8. Stress Test Adversarial Cases

Use adversarial or ambiguous cases where the wording can be misread.

Consider:
- a safety label used as a fake waiver
- high-risk path with formatting-only change
- low-risk path with dangerous behavior change
- controlled-source wording change disguised as coordination
- validation passes but review is still needed
- stale state hides out-of-scope files
- multiple small changes compound into material risk

## 9. Premortem

Assume the final rule, artifact, strategy, or process is adopted and fails later.

Work backward and identify:
- most likely failure
- most dangerous failure
- hidden assumption
- early warning signs
- prevention controls
- rollback path

## 10. Self-Grill

Ask at least 12 hard questions, adapted to the task.

Include questions like:
- Am I treating the prompt as source truth?
- Am I overcorrecting against the last failure?
- Would this suppress necessary review, approval, testing, or escalation?
- Would this create a new blocker or process loop?
- Would this make agents ask for more handoffs, artifacts, or sessions?
- Would this fail differently across projects, teams, repos, markets, or customer segments?
- Is this enforceable by humans, models, hooks, scripts, and managers?
- Is the wording precise enough that another model cannot call it optional?

## 11. Frameworks

Use relevant frameworks from the canonical framework index, but do not decorate the answer with unnecessary jargon. At minimum consider:
- Root Cause Analysis
- 5 Whys
- FMEA
- Bowtie analysis
- Fault Tree Analysis
- Poka-Yoke
- Control Plan
- PDCA or PDSA
- OODA Loop
- Cynefin
- RACI or DACI where ownership matters
- Second-order thinking
- Inversion

State only the frameworks that materially changed the critique or recommendation.

## 12. Final Deliverable

Provide:
- final verdict
- source-backed findings
- recommended rule wording
- exact placement recommendations by repo and control surface
- what not to change
- acceptance tests
- examples of correct agent responses
- examples of forbidden agent responses
- residual risks
- specific next action for the user to approve or reject
