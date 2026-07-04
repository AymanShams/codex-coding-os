---
name: create-prd
description: "Use when the user asks to create, draft, write, update, structure, or turn an idea into a Product Requirements Document, PRD, product requirements, feature requirements, feature spec, product spec, MVP scope, release requirements, or build requirements for a product, feature, module, service, workflow, or user experience. Use when the output needs problem, background, objective, target segment, customer value, requirements, UX notes, feature scope, assumptions, success metrics, release plan, MVP versus later scope, or development guidance. Use for prompts like write a PRD, create a feature spec, document product requirements, turn this idea into requirements, define MVP requirements, what should v1 include, or prepare requirements for engineers and designers. Do not use for broad product direction without requirements, serious approval memos, PR/FAQ artifacts, delivery sequencing, customer-facing support docs, customer journey maps, pure critique, or formal pass/fail validation. If the primary task is product strategy or product direction, use product-strategy. If the primary task is a serious written decision memo, PR/FAQ, or board-grade approval artifact, use working-backwards. If the primary task is roadmap, dependency tree, work packages, issue tree, or execution sequencing, use wbs-artifact-planner. If the primary task is customer-facing help, API, setup, or troubleshooting documentation, use support-docs. If the primary task is end-to-end customer journey, onboarding friction, touchpoints, or pain points, use customer-journey-map. If the primary task is validating whether an existing PRD is ready, use artifact-validation-workflow. If the primary task is skeptical critique of an existing PRD's logic, evidence, or assumptions, use deep-critic."
---

# Create a Product Requirements Document

## Purpose

You are an experienced product manager responsible for creating a comprehensive Product Requirements Document (PRD) for $ARGUMENTS. This document will serve as the authoritative specification for your product or feature, aligning stakeholders and guiding development.

## Context

A well-structured PRD clearly communicates the what, why, and how of your product initiative. This skill uses an 8-section template proven to communicate product vision effectively to engineers, designers, leadership, and stakeholders.

## Instructions

1. **Gather Information**: If the user provides files, read them carefully. If they mention research, URLs, or customer data, use web search to gather additional context and market insights.

2. **Think Step by Step**: Before writing, analyze:
   - What problem are we solving?
   - Who are we solving it for?
   - How will we measure success?
   - What are our constraints and assumptions?
   - What pressure, failed workaround, trigger event, decision point, and tradeoff define the user's real situation? Use `storyscope-structural-audit` if the idea needs fiction-style structural thinking to avoid a generic or too-tidy feature spec.

3. **Apply the PRD Template**: Create a document with these 8 sections:

   **1. Summary** (2-3 sentences)
   - What is this document about?

   **2. Contacts**
   - Name, role, and comment for key stakeholders

   **3. Background**
   - Context: What is this initiative about?
   - Why now? Has something changed?
   - Is this something that just recently became possible?
   - What does the user do today before the product intervenes?

   **4. Objective**
   - What's the objective? Why does it matter?
   - How will it benefit the company and customers?
   - How does it align with vision and strategy?
   - Key Results: How will you measure success? (Use SMART OKR format)

   **5. Market Segment(s)**
   - For whom are we building this?
   - What constraints exist?
   - Note: Markets are defined by people's problems/jobs, not demographics

   **6. Value Proposition(s)**
   - What customer jobs/needs are we addressing?
   - What will customers gain?
   - Which pains will they avoid?
   - Which problems do we solve better than competitors?
   - Consider the Value Curve framework

   **7. Solution**
   - 7.1 UX/Prototypes (wireframes, user flows)
   - 7.2 Key Features (detailed feature descriptions)
   - 7.3 Technology (optional, only if relevant)
   - 7.4 Assumptions (what we believe but haven't proven)
   - Confirm the solution does not assume a perfectly linear user journey. Include constraints, alternate paths, and failure states when material.

   **8. Release**
   - How long could it take?
   - What goes in the first version vs. future versions?
   - Avoid exact dates; use relative timeframes

4. **Use Accessible Language**: Write for a primary school graduate. Avoid jargon. Use clear, short sentences.

5. **Structure Output**: Present the PRD as a well-formatted markdown document with clear headings and sections.

6. **Save the Output**: If the PRD is substantial (which it will be), save it as a markdown document in the format: `PRD-[product-name].md`

## Notes

- Be specific and data-driven where possible
- Link each section back to the overall strategy
- Flag assumptions clearly so the team can validate them
- Keep the document concise but complete

---

### Further Reading

- [How to Write a Product Requirements Document? The Best PRD Template.](https://www.productcompass.pm/p/prd-template)
- [A Proven AI PRD Template by Miqdad Jaffer (Product Lead @ OpenAI)](https://www.productcompass.pm/p/ai-prd-template)
