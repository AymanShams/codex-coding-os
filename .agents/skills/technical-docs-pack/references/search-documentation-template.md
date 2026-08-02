# Public Search Surfaces

Use this template only when the project documentation manifest records at least
one public, indexable surface. Do not create this document for private,
authenticated-only, internal, or non-indexable products.

## Applicability Evidence

| Condition | Value | Controlling evidence |
|---|---|---|
| Public indexable surfaces exist | `<true-or-false>` | `<source>` |

## Surface Inventory

| Surface or route pattern | Public purpose | Indexing decision | Canonical location | Content source |
|---|---|---|---|---|
| `<surface>` | `<purpose>` | `<index-or-exclude>` | `<canonical location>` | `<source>` |

## Discovery Contract

- Sitemap ownership and generation: `<owner and method>`
- Crawler directives: `<rules>`
- Canonical-location rules: `<rules>`
- Redirect behavior: `<rules>`
- Pagination or filtered-view treatment: `<rules>`

## Search Presentation

- Page-title pattern: `<pattern>`
- Summary pattern: `<pattern>`
- Structured data, when used: `<types and source>`
- Preview-image rules: `<rules>`

## Content Lifecycle

| Event | Required search-documentation update | Owner | Validation |
|---|---|---|---|
| `<route or content change>` | `<update>` | `<owner>` | `<check>` |

## Validation Checklist

- Every documented surface is public and intentionally indexable.
- Indexing, exclusion, and canonical-location decisions agree.
- Discovery files and metadata derive from named project sources.
- Validation checks cover added, changed, redirected, and removed surfaces.
