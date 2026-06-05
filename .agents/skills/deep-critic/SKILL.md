---
name: deep-critic
description: Use when the user asks to review, critique, audit, validate, challenge, stress-check, pressure-test, compare, poke holes in, find flaws in, or give a hard second opinion on a user-submitted artifact, document, screenshot, recommendation, analysis, strategy, proposal, plan, argument, model, table, deck, AI answer, or opinion. Use when the task needs skeptical reasoning, evidence-quality review, hidden-assumption detection, source-chain critique, methodology critique, risk identification, or a rebuilt recommendation after critique. Do not use for normal execution, drafting from scratch, implementation, pure summarization, grammar-only editing, or routine answers unless critique depth is explicitly requested. If the primary task is fact checking, source validation, or citation review, use evidence-checker. If the primary task is numeric, forecasting, pricing, KPI, or model review, use quant-review. If the artifact is an SSOT-style SOP, policy, workflow, governance document, implementation guide, controlled template, or formal company artifact, use ssot-auditor. If the user asks what could kill a concrete plan or decision before launch, use pre-mortem.
---

Apply deep critique mode automatically only when the user asks to review, critique, audit, validate, challenge, or compare. Otherwise, default to execution mode and only flag major risks.

Source phrasing: Apply deep critique mode automatically only when I ask to review, critique, audit, validate, challenge, or compare. Otherwise, default to execution mode and only flag major risks.

For any user-submitted artifact/document/screenshot/opinion, always critique it in-depth and granularly: evaluate, challenge, and question every assumption, calculation, methodology, and reasoning; dismantle the logic and rebuild it; validate claims; identify all flaws.

In critiques and reviews, challenge not only the conclusion but also the evidence quality, source credibility, recency, sampling bias, and hidden assumptions. If the source chain is weak, say so explicitly.

## Required output sequence
1. Direct verdict
2. What is correct
3. What is weak, missing, unsupported, or risky
4. Hidden assumptions and failure modes
5. Exact fixes
6. Rebuilt recommendation, argument, model, or structure
7. Assumptions and confidence
