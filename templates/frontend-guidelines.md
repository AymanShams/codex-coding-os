# Frontend Guidelines

## Product Feel
{{describe_the_interface_feel_in_plain_language}}

## Layout Rules
- Use predictable navigation.
- Keep primary workflows visible without hunting through menus.
- Avoid decorative sections that do not help the task.
- Keep repeated items in cards or rows with consistent spacing.
- Make mobile and desktop layouts deliberate, not accidental.

## Components
| Component | Purpose | States |
|---|---|---|
| {{component}} | {{purpose}} | default, loading, error, disabled, empty |

## Forms
- Label every field clearly.
- Validate on the server.
- Show errors next to the relevant field.
- Preserve user input after errors.
- Make destructive actions confirmable.

## Accessibility
- Use semantic HTML when possible.
- Ensure keyboard navigation works.
- Maintain visible focus states.
- Use sufficient color contrast.
- Do not rely on color alone to communicate status.

## Responsive Behavior
| Viewport | Expected behavior |
|---|---|
| Mobile | {{mobile_behavior}} |
| Tablet | {{tablet_behavior}} |
| Desktop | {{desktop_behavior}} |

## Visual QA Checklist
- Text does not overlap.
- Buttons and inputs are reachable on mobile.
- Loading, empty, and error states exist.
- Navigation works after refresh.
- Browser console has no relevant errors.

