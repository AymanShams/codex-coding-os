---
name: deep-critic
description: Use when the user asks to review, critique, audit, validate, challenge, stress-check, pressure-test, compare, poke holes in, find flaws in, or give a hard second opinion on a user-submitted artifact, document, screenshot, recommendation, analysis, strategy, proposal, plan, argument, model, table, deck, AI answer, workflow, rule, agent behavior, coding approach, or opinion. Use when the task needs skeptical reasoning, evidence-quality review, hidden-assumption detection, source-chain critique, methodology critique, risk identification, adversarial simulation, premortem thinking, self-grilling, or a rebuilt recommendation after critique. Use the full 12-step critique workflow when the user asks for deep analysis, a source-backed audit, recurring failure analysis, stress test, premortem, or "the same 12 steps." Do not use for normal execution, drafting from scratch, implementation, pure summarization, grammar-only editing, or routine answers unless critique depth is explicitly requested. If the primary task is fact checking, source validation, or citation review, use evidence-checker. If the primary task is numeric, forecasting, pricing, KPI, or model review, use quant-review. If the artifact is an SSOT-style SOP, policy, workflow, governance document, implementation guide, controlled template, or formal company artifact, use ssot-auditor. If the user asks what could kill a concrete plan or decision before launch, use pre-mortem.
---

# Deep Critic

Apply deep critique mode only when the user asks to review, critique, audit, validate, challenge, compare, stress-test, pressure-test, or analyze a non-trivial artifact, recommendation, workflow, strategy, business decision, coding approach, or recurring failure. Otherwise, default to execution mode and only flag major risks.

Source phrasing: Apply deep critique mode automatically only when I ask to review, critique, audit, validate, challenge, or compare. Otherwise, default to execution mode and only flag major risks.

For any user-submitted artifact/document/screenshot/opinion, always critique it in-depth and granularly: evaluate, challenge, and question every assumption, calculation, methodology, and reasoning; dismantle the logic and rebuild it; validate claims; identify all flaws.

In critiques and reviews, challenge not only the conclusion but also the evidence quality, source credibility, recency, sampling bias, and hidden assumptions. If the source chain is weak, say so explicitly.

## Depth Modes

Use the smallest mode that fits the user's request.

- Compact critique: use for quick but serious feedback. Lead with the verdict, key risks, exact fixes, and next action.
- Standard critique: use for most reviews, strategy critiques, coding reviews, artifact critiques, and decision checks.
- Full 12-step critique: use when the user asks for deep analysis, source-backed critique, recurring failure analysis, workflow audit, stress test, premortem, or "the same 12 steps." Load and follow `templates/full-12-step-analysis.md`.

Do not force the full 12-step structure into routine execution tasks. Do use its thinking internally when the task is high-risk, cross-system, or recurring.

## Standard Output Sequence

1. Direct verdict
2. What is correct
3. What is weak, missing, unsupported, or risky
4. Hidden assumptions and failure modes
5. Exact fixes
6. Rebuilt recommendation, argument, model, or structure
7. Assumptions and confidence

## Full 12-Step Workflow

When full mode is triggered, read `templates/full-12-step-analysis.md` and use its headings unless the user explicitly asks for a shorter format.

Sections 11 and 12 are mandatory in full mode:

- Section 11 must consider the canonical framework index without decorating the answer with unnecessary jargon. At minimum consider Root Cause Analysis, 5 Whys, FMEA, Bowtie analysis, Fault Tree Analysis, Poka-Yoke, Control Plan, PDCA or PDSA, OODA Loop, Cynefin, RACI or DACI where ownership matters, Second-order thinking, and Inversion.
- Section 12 must provide the final verdict, source-backed findings, recommended rule wording, exact placement recommendations by repo and control surface when applicable, what not to change, acceptance tests, examples of correct and forbidden responses or behaviors, residual risks, and the specific next action for the user to approve or reject.

## Source Discipline

- Verify live files, diffs, repos, PRs, or source documents before treating a claim as fact when access is available.
- Separate verified facts, inferences, assumptions, and open questions.
- Do not treat the user's prompt as source truth when the task asks for source-backed critique.
- If verification is unavailable, say so and label the critique provisional.
