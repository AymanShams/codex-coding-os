---
name: codex-design-artifacts
description: "Use when the user asks to create, revise, explore, compare, polish, or visually verify a design-heavy Codex artifact such as a standalone HTML prototype, interactive UI concept, visual exploration, HTML deck, fixed-size slide-like artifact, design-system exploration, motion demo, high-fidelity mockup, or visual artifact. Use when art direction, layout, interaction, responsive framing, screenshots, browser verification, or design variants matter. Do not use for routine coding, normal frontend bug fixes, pure copywriting, non-visual documents, DOCX/PDF/PPTX deliverables, production web-app implementation without design exploration, or image generation alone. If the primary task is a real frontend app, dashboard, game, or production UI, use build-web-apps frontend skills alongside or instead of this skill. If the requested deliverable is an editable presentation file, use the relevant presentation or PPTX skill. If the primary task is prose naturalness, use humanizer."
---

# Codex Design Artifacts

Use this skill to produce visual work that needs art direction, interaction thinking, and verification, not just functional code. This skill adapts useful design-artifact discipline into Codex-native behavior. Do not copy Claude artifact mechanics, tool names, host protocols, or `window.claude` patterns.

## Core Principles

- Treat HTML/CSS/JS as a design medium, not only a web page format.
- Start from the actual product, brand, codebase, design system, screenshots, assets, or content whenever available.
- Prefer a strong system: clear hierarchy, intentional spacing, restrained palette, real imagery or honest placeholders, and one dominant visual idea per screen.
- Avoid filler content, decorative metrics, generic icon grids, overused gradient backgrounds, emoji unless brand-native, fake data, and UI sections that exist only to fill space.
- Ask a concise question only when missing context would materially change the output. Otherwise state assumptions and build a useful first version.

## Workflow

1. Identify the artifact type: static visual, clickable prototype, HTML deck, fixed-size presentation, motion demo, design-system exploration, or implementation-ready UI.
2. Gather context before inventing:
   - existing repo files, components, tokens, global styles, screenshots, brand assets, copy, and design systems
   - target audience, fidelity, platform, viewport, constraints, and whether variants are desired
3. State a brief design thesis before building:
   - visual direction
   - content structure
   - interaction or motion idea
4. Build in the current project style if a project exists. For standalone HTML, keep files readable and split large work into smaller CSS/JS files when practical.
5. Verify the artifact with Codex-native tools:
   - run the local server or open the HTML if no server is needed
   - use browser/Playwright checks for load errors, layout fit, mobile/desktop framing, console errors, and obvious overlap
   - for canvas/3D/motion work, check that pixels render and the scene is not blank
6. Finish with a short summary of what was created, where it lives, verification performed, and concrete next options.

## Design Context Rules

- For high-fidelity UI, code and design tokens beat memory. Read the actual source when available.
- Look for theme files, global CSS, Tailwind config, component libraries, layouts, icon systems, and existing route/page patterns.
- If only screenshots exist, use them for visual vocabulary but avoid pretending to know hidden implementation details.
- If no brand assets exist, create a coherent temporary system and label it as an assumption.
- Use placeholders when the real asset is missing; a clean placeholder is better than an invented logo, fake product image, or misleading mock data.

## Variations And Tweaks

- When the user asks for exploration, provide 2-4 meaningful variants that differ by layout, density, visual tone, or interaction pattern.
- Prefer one artifact with switchable variants over many disconnected files when that improves comparison.
- For standalone prototypes, use simple in-page controls or tabs for variants. Persist useful state in `localStorage` when refresh continuity matters.
- Do not add tweak panels or controls to final production UI unless the user wants exploratory controls.

## Fixed-Size Decks And Presentations

- For HTML decks, use a fixed logical canvas such as 16:9 and scale it to fit the viewport with controls outside the scaled slide area.
- Slide numbers are human-facing and 1-indexed.
- Keep text large enough for the medium. For 1920x1080 slides, avoid tiny body text.
- Do not add speaker notes unless the user explicitly asks.
- Preserve slide position in `localStorage` if the deck is interactive.

## Content Discipline

- Do not add filler sections, dummy stats, invented testimonials, decorative iconography, or explanatory design commentary inside the artifact.
- Ask before adding new substantive content that changes the message, offer, policy, or product claim.
- Use real user-provided copy when available. When drafting temporary copy, make it concise and clearly replaceable.
- For healthcare, compliance, finance, or operational claims, avoid unsupported promises and label assumptions.

## Codex-Native Verification

- Do not rely on Claude-only `done`, `fork_verifier_agent`, `questions_v2`, `copy_starter_component`, `/projects/...`, asset panes, `CLAUDE.md`, or artifact host messages.
- Use the local filesystem, dev server, browser automation, screenshots, and console/log inspection available in Codex.
- If verification cannot be run, say exactly what was not verified and why.
- If the artifact is meant to be tried by the user, provide the local URL or absolute file path as appropriate.

## Relationship To Other Skills

- Use `build-web-apps:frontend-skill` alongside this skill for polished websites, landing pages, apps, and prototype UI.
- Use `build-web-apps:web-design-guidelines` when the task is a UI/UX/design review.
- Use `playwright` or browser automation for interactive verification.
- Use `PowerPoint` or `document-skills:pptx` when the requested deliverable must be an editable `.pptx`, not an HTML deck.
