---
name: frontend-qa-gate
description: Use after frontend UI work, responsive layout changes, forms, routes, dashboards, browser interactions, or when a local app needs visual and behavior verification.
---

# Frontend QA Gate

Use this skill after any visible UI change.

## Required Checks

1. Start the dev server if needed.
2. Open the app in a browser.
3. Check the changed route.
4. Test the primary workflow.
5. Test at least one error or empty state when relevant.
6. Check desktop and mobile widths.
7. Capture screenshot evidence when useful.
8. Check console errors if browser tooling is available.

## UI Review Criteria

Check:

- text fits inside containers
- no overlapping elements
- buttons are clear
- forms have labels and validation
- loading states exist
- empty states exist
- errors are visible and useful
- keyboard navigation works for core controls
- focus states are visible
- mobile layout is usable
- no unrelated marketing layout was added to an operational tool

## Browser Tool Preference

Use the available environment:

- in-app Browser plugin for local app inspection
- Playwright for repeatable shell-driven browser checks
- Chrome tools for profile-dependent or deep debugging
- manual browser check only when tool access is not available

## Output

Report:

- routes checked
- viewport sizes checked
- screenshots or evidence created
- browser errors
- issues fixed
- remaining UI risks

