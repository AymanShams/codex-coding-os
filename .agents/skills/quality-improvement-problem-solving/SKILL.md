---
name: quality-improvement-problem-solving
description: Use when the user asks to investigate, solve, reduce, prevent, or improve an operational problem, quality issue, defect, recurrence, incident, service failure, process waste, bottleneck, variation, rework, backlog, SLA miss, audit finding, CAPA, RCA, 8D, A3, PDCA, Kaizen, CIMS, 5 Whys, fishbone, Ishikawa, Pareto, FMEA, control plan, or continuous-improvement workflow. Do not use for pre-failure risk analysis, generic critique, SOP drafting, artifact readiness validation, broad strategy, product direction, or pure calculations unless operational problem-solving is the main task.
---

# Quality Improvement Problem Solving

## Purpose

Solve operational and quality problems with evidence, containment, root-cause analysis, corrective actions, recurrence prevention, and verified results. The goal is not a generic list of ideas. The goal is to move from problem to controlled improvement.

## When To Use

Use for:

- Root-cause analysis, RCA, CAPA, 8D, A3, PDCA, Kaizen, CIMS, Lean, Six Sigma, and continuous improvement.
- Defects, rework, waste, bottlenecks, variation, backlog growth, SLA misses, recurring incidents, audit findings, service failures, and process instability.
- Healthcare, operations, service delivery, claims, patient access, billing, support, intake, fulfillment, compliance operations, and internal admin processes.
- Choosing problem-solving tools such as 5 Whys, fishbone, Pareto, check sheets, control charts, FMEA, SIPOC, process maps, value stream maps, poka-yoke, and control plans.
- Building an improvement action plan with owners, measures, due dates, validation, and standardization.

## Do Not Use

- Pre-failure risk analysis on a proposed plan. Use `pre-mortem`.
- Broad skeptical critique of an artifact, argument, recommendation, workflow, recurring failure, or AI answer. Use `deep-critic`; for source-backed audits or the same 12-step structure, use `deep-critic` full mode and keep this skill as the RCA/FMEA/control-plan support lens.
- Formal SOP, policy, playbook, workflow, RACI, or governance drafting. Use `ssot-drafter`.
- Auditing an existing SOP, policy, workflow, or controlled artifact for completeness. Use `ssot-auditor`.
- Pass/fail readiness, acceptance criteria, defect logs, or approval gates for a deliverable. Use `artifact-validation-workflow`.
- Delivery sequencing, roadmap, dependency tree, or work breakdown as the main task. Use `wbs-artifact-planner`.
- Pure forecasts, ROI, KPI math, or model review. Use `quant-review`, then return here if the numbers drive an operational fix.
- Broad product, GTM, pricing, or market strategy. Use the relevant strategy skill.

## Operating Rules

- Contain first when harm, compliance risk, patient/customer impact, financial leakage, or operational disruption is active.
- Define the problem before solving it. Do not jump to recommendations from symptoms.
- Use evidence over opinion: logs, tickets, timestamps, process data, samples, screenshots, audit records, interviews, or direct observation.
- Separate root cause from contributing factors, detection failure, and escape point.
- Do not call something a root cause unless removing it would materially reduce recurrence.
- Distinguish corrective action from preventive action. Corrective action fixes this problem. Preventive action prevents this class of problem from recurring.
- In regulated, healthcare, financial, HR, legal, or safety-sensitive contexts, add safety, privacy, compliance, auditability, and approval gates before implementation.
- Do not recommend changes to clinical, legal, financial, security, or compliance controls without naming the approval owner or review requirement.
- Validate with measures after implementation. A fix is not complete until results are checked and the new standard is controlled.

## Minimum Context Gate

Use available files and conversation first. Ask only if missing information would cause a serious error.

Needed context:

1. Problem statement or symptom.
2. Where it occurs: process, team, system, customer segment, location, product, or workflow.
3. Impact: quality, cost, time, customer/patient, compliance, risk, revenue, staff workload, or reputation.
4. Evidence available: data, reports, tickets, logs, screenshots, audits, timelines, samples, or interviews.
5. Desired output: diagnosis, RCA, CAPA, 8D, A3, Kaizen plan, CIMS maturity review, or implementation plan.

If the user only gives a vague problem, proceed with a provisional problem statement and list the missing evidence needed to strengthen it.

## Workflow

1. **Triage and contain**
   - Decide whether the issue is active and harmful.
   - If active, define interim containment: stop the bleeding, isolate scope, protect users/customers/patients, preserve evidence, and set a containment expiry date.

2. **Frame the problem**
   - Write a specific problem statement: what, where, when, who is affected, extent, baseline, target, and business or safety impact.
   - Use 5W2H, Is/Is-Not, SIPOC, or a simple process map when the boundary is unclear.

3. **Measure and baseline**
   - Identify the current defect rate, cycle time, lead time, first-pass yield, backlog, SLA miss rate, cost, rework, error count, or other relevant metric.
   - If data is weak, create a check sheet or sampling plan before over-analyzing.

4. **Prioritize**
   - Use Pareto or impact/effort/risk when there are many issues.
   - Do not spend RCA effort on low-impact noise unless recurrence risk is high.

5. **Find root cause and escape point**
   - Use 5 Whys for simple causal chains.
   - Use fishbone/Ishikawa when causes span people, process, technology, policy, environment, suppliers, or measurement.
   - Use process map, control chart, histogram, scatter plot, or cohort slices when variation or data pattern matters.
   - Identify the escape point: why the system failed to detect or stop the issue earlier.

6. **Select corrective and preventive actions**
   - Generate options, then score by impact, effort, risk, speed, reversibility, and control strength.
   - Prefer error-proofing, standard work, automation with controls, training plus verification, workflow redesign, and monitoring over reminders alone.
   - Use FMEA or risk assessment before implementing changes that can create new harm.

7. **Implement with ownership**
   - Assign owner, approver, due date, evidence required, dependencies, and communication needs.
   - For service and healthcare operations, include patient/customer communication, escalation, privacy, and audit trail where relevant.

8. **Validate results**
   - Compare post-change data to baseline.
   - Define acceptance criteria before declaring success.
   - Use a control chart, trend, audit sample, SLA review, before/after metric, or direct observation as appropriate.

9. **Standardize and prevent recurrence**
   - Update SOPs, checklists, templates, training, system controls, dashboards, alerts, QA sampling, and ownership.
   - Add control plan owner, review cadence, and rollback path.

10. **Close and learn**
   - Document lessons learned, residual risks, open actions, and follow-up review date.
   - Recognize contributors when team-based improvement was involved.

## Method Selector

| Situation | Best first method |
|---|---|
| Active harm or service disruption | Containment plan, incident log, escalation path |
| Unclear problem boundary | 5W2H, Is/Is-Not, SIPOC |
| Many defects or complaints | Check sheet, Pareto |
| Process flow or handoff unclear | Process map, swimlane, SIPOC |
| Bottleneck, waiting, overprocessing, motion, handoff waste | Value stream map, Kaizen, ECRS |
| Simple recurring problem | 5 Whys |
| Multi-factor problem | Fishbone/Ishikawa |
| Process instability or variation | Run chart, control chart, histogram |
| Suspected factor relationship | Scatter plot, cohort analysis, DOE if feasible |
| Preventing future failure | FMEA, poka-yoke, control plan |
| Cross-functional recurring issue | 8D or A3 |
| Maturity or operating system review | CIMS dimensions: plan, progress, process, project, performance |

## 8D Structure

Use 8D for serious recurring or cross-functional issues:

1. D0: Plan the response when severity is high.
2. D1: Establish the team and RACI.
3. D2: Describe the problem with baseline and scope.
4. D3: Implement interim containment with owner and expiry.
5. D4: Identify root cause and escape point.
6. D5: Choose and verify permanent corrective actions.
7. D6: Implement and validate permanent corrective actions.
8. D7: Prevent recurrence through standard work, training, controls, monitoring, and audits.
9. D8: Close, recognize the team, and capture learning.

## Output Format

Use this structure unless the user asks for a specific method format:

1. **Direct verdict**
   - What is most likely happening.
   - Whether containment is needed.
   - Highest-impact next action.

2. **Problem statement**
   - Specific scope, impact, baseline, and target.

3. **Evidence reviewed**
   - Files, data, observations, interviews, tickets, logs, reports, or missing evidence.

4. **Root-cause analysis**
   - Root cause.
   - Contributing factors.
   - Escape point.
   - Evidence strength.

5. **Corrective and preventive action plan**
   - Action.
   - Owner.
   - Due date.
   - Approval needed.
   - Evidence required.
   - Risk.

6. **Validation plan**
   - Metric, baseline, target, sample, review date, and pass/fail rule.

7. **Standardization**
   - SOP/checklist/training/system/dashboard/control updates needed.

8. **Open questions and assumptions**
   - Only the remaining items that materially affect the fix.

## CAPA Table

| Field | Required content |
|---|---|
| Problem | Specific defect, failure, gap, or incident |
| Impact | Customer/patient, financial, quality, compliance, time, or staff impact |
| Containment | Temporary control and expiry |
| Root cause | Verified cause, not symptom |
| Escape point | Why detection or prevention failed |
| Corrective action | Fix for this problem |
| Preventive action | Control to prevent recurrence |
| Owner | Named accountable person or role |
| Due date | Date or review cadence |
| Evidence | What proves the action happened |
| Validation | What proves the action worked |
| Residual risk | What remains after the fix |

## Common Failure Modes

- Solving the visible symptom instead of the root cause.
- Treating training or reminders as a complete fix when the process needs a control.
- Skipping containment while the issue continues harming customers, patients, staff, money, or compliance.
- Closing CAPA before validation data proves the result.
- Confusing detection controls with prevention controls.
- Writing an SOP update without changing workflow, ownership, system controls, or measurement.
- Overusing 8D for small issues that only need a quick PDCA or Kaizen action.
- Using manufacturing-only tools without translating them for service, healthcare, or digital workflows.

## Coordination With Other Skills

- Use `ssot-drafter` after the fix if a formal SOP, policy, work instruction, or controlled template must be created.
- Use `ssot-auditor` if the main question is whether an existing operational artifact is complete.
- Use `artifact-validation-workflow` if the main output is a pass/fail readiness decision.
- Use `pre-mortem` if the issue has not happened yet and the task is to prevent a planned initiative from failing.
- Use `deep-critic` if the task is broad critique rather than operational problem-solving.
- Use `quant-review` for calculations, KPI math, forecasts, sensitivity analysis, or ROI before returning to action design.
- Use `wbs-artifact-planner` if the improvement plan becomes a multi-workstream delivery plan.
