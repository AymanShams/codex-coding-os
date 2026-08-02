# Content Guidelines

Use this template only when the project documentation manifest records that at
least one of these conditions is true:

- the product contains substantial user-facing copy
- localization is required
- the product generates user-facing content

Do not create this document when all three conditions are false.

## Applicability Evidence

| Condition | Value | Controlling evidence |
|---|---|---|
| Substantial user-facing copy | `<true-or-false>` | `<source>` |
| Localization required | `<true-or-false>` | `<source>` |
| Generated user-facing content | `<true-or-false>` | `<source>` |

## Voice And Terminology

- Voice: `<project-specific voice>`
- Required terms: `<approved terms>`
- Avoided terms: `<terms that create ambiguity or contradict product language>`
- Naming source of truth: `<glossary or controlled product source>`

## Content Surfaces

| Surface | Audience | Content owner | Source of truth | Review trigger |
|---|---|---|---|---|
| `<surface>` | `<audience>` | `<owner>` | `<source>` | `<event>` |

## User-Facing Copy Rules

- Headings and labels: `<rules>`
- Instructions and empty states: `<rules>`
- Errors and recovery copy: `<rules>`
- Notifications and confirmations: `<rules>`

## Localization

Complete this section only when localization is required.

- Supported locales: `<locales>`
- Fallback locale: `<locale>`
- Translation ownership: `<owner and workflow>`
- Formatting rules: `<dates, numbers, currency, units, and names>`
- Validation: `<locale-specific checks>`

## Generated Content

Complete this section only when the product generates user-facing content.

- Generated content types: `<types>`
- Controlling inputs: `<sources>`
- Required structure and tone: `<rules>`
- Failure and fallback behavior: `<behavior>`
- Quality checks: `<checks>`

## Validation Checklist

- Every rule is tied to an actual project surface.
- Terminology matches the project glossary and controlled product sources.
- Localization rules exist only for supported locales.
- Generated-content rules identify inputs, fallbacks, and checks.
