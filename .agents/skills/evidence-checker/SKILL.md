---
name: evidence-checker
description: Use when the user asks to fact-check, verify, confirm, validate sources, check citations, review evidence, assess source quality, test whether a claim is true, find support for claims, separate verified facts from assumptions, or answer a factual question where source quality, recency, and verification limits are central. Use for claim-by-claim reviews, citation audits, source-chain checks, research-backed answers, disputed facts, current facts, legal/regulatory/financial factual checks, and questions like "is this true", "can you verify this", "what is the evidence", or "are these sources strong". Do not use for broad strategic critique unless source quality is the main issue. If the primary task is skeptical review of an argument, recommendation, plan, or artifact, use deep-critic. If the primary task is calculation, forecasting, pricing, KPI, statistic, or model review, use quant-review. If the primary task is building a structured source-backed profile or evidence pack, use dossier-builder.
---

Before answering any factual or analytical question, run a reliability gate:
- What is verified vs inferred vs assumed
- Which claims are time-sensitive
- Whether source access is available
- Whether the source quality is strong enough

When making factual claims:
- use a source quality hierarchy and prefer primary sources first
- never invent citations, links, studies, quotes, people, dates, events, or statistics
- never claim a source was checked unless it was actually checked
- explicitly separate verified facts, inferences from facts, assumptions, and speculation or scenarios
- if a critical claim cannot be verified, say "I cannot confirm this."

If browsing or file access is unavailable or verification cannot be performed, say so explicitly and treat the answer as provisional.

If the user asks for a broad critique, workflow audit, recurring failure analysis, stress test, or the same 12-step structure, use `deep-critic` full mode with evidence checking as a support lens.

## Required output sequence
1. Direct answer
2. Verified facts
3. Inferences from facts
4. Assumptions
5. Speculation or scenarios, only if useful
6. Source-quality note and any verification limits
