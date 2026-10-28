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
- how the task's relevant failure could compound
- what signal would have caught it earlier

## 8. Stress Test Adversarial Cases

Use adversarial or ambiguous cases where the wording can be misread.

Choose adversarial cases that fit the actual task. For software, consider misleading formatting changes, stale state, and checks that pass while behavior is wrong. For business, consider weak samples, incentives, missing customer evidence, and assumptions that break under a different market condition. Do not import unrelated review lenses.

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

Ask hard questions adapted to the task. Use 12 when the user asks for the full 12-step workflow.

Include questions like:
- Am I treating the prompt as source truth?
- Am I overcorrecting against the last failure?
- Would this skip a check the outcome requires?
- Would this create a new blocker or process loop?
- Would this fail differently across relevant projects, teams, markets, or customer segments?
- Can the proposed change actually be carried out by its owner?
- Is the wording precise enough that another model cannot call it optional?

## 11. Frameworks

Use a framework only when it materially changes the critique or recommendation. State it only if useful to the reader.

## 12. Final Deliverable

Provide the verdict, source-backed findings, practical recommendation, relevant checks, residual uncertainty, and next action. Add rule wording, repository placement, examples, or an approval decision only when the task needs them.
