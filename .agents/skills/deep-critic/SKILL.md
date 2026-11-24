---
name: deep-critic
description: Use when the user asks to review, critique, audit, validate, challenge, stress-check, pressure-test, compare, find flaws in, or give a hard second opinion on an artifact, recommendation, analysis, strategy, proposal, plan, model, workflow, coding approach, or opinion, including an explicit request for judgment about submitted material. For those requested critiques, apply skeptical reasoning, evidence-quality review, hidden-assumption detection, methodology critique, adversarial simulation, and a rebuilt recommendation as relevant. Use the full 12-step critique workflow only when the user asks for deep analysis, a source-backed audit, recurring failure analysis, stress test, premortem, or "the same 12 steps." Do not use for mere attachment, normal execution, drafting from scratch, implementation, pure summarization, grammar-only editing, or routine answers. If the primary task is fact checking or source validation, use evidence-checker. If the primary task is numeric or model review, use quant-review. If the artifact is a formal company procedure or controlled template, use ssot-auditor. If the user asks what could kill a concrete plan before launch, use pre-mortem.
---

# Deep Critic

Apply deep critique mode only when the user asks to review, critique, audit, validate, challenge, compare, stress-test, pressure-test, or explicitly asks for judgment about submitted material or a recurring failure. Otherwise, default to execution mode and only flag major risks.

Source phrasing: Apply deep critique mode automatically only when I ask to review, critique, audit, validate, challenge, or compare. Otherwise, default to execution mode and only flag major risks.

When the user requests critique or judgment, examine the material at the depth needed for that decision. Test material assumptions, calculations, methods, reasoning, and claims, then rebuild the strongest supported conclusion. A submission alone does not request critique.

In critiques and reviews, challenge not only the conclusion but also the evidence quality, source credibility, recency, sampling bias, and hidden assumptions. If the source chain is weak, say so explicitly.

## Depth Modes

Use the smallest mode that fits the user's request.

- Compact critique: use for quick but serious feedback. Lead with the verdict, key risks, exact fixes, and next action.
- Standard critique: use for most reviews, strategy critiques, coding reviews, artifact critiques, and decision checks.
- Full 12-step critique: use when the user asks for deep analysis, source-backed critique, recurring failure analysis, workflow audit, stress test, premortem, or "the same 12 steps." Load and follow `templates/full-12-step-analysis.md`.

Do not force the full 12-step structure into routine execution tasks. Apply only relevant methods internally when they materially improve a requested critique.

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

- Section 11 considers only frameworks that materially improve the critique. Do not require a fixed list.
- Section 12 provides the verdict, source-backed findings, practical recommendations, relevant checks, residual uncertainty, and next action. Include rule wording, repository placement, response examples, or approval decisions only when the task calls for them.

## Source Discipline

- Verify live files, diffs, repos, PRs, or source documents before treating a claim as fact when access is available.
- Separate verified facts, inferences, assumptions, and open questions.
- Do not treat the user's prompt as source truth when the task asks for source-backed critique.
- If verification is unavailable, say so and label the critique provisional.
