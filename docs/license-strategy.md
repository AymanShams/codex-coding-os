# License Strategy

The requested licensing goal has a conflict:

- If commercial use is restricted, the license is not open source under the Open Source Definition.
- If the repo uses Apache-2.0 or MIT, anyone can use it commercially without buying an enterprise license.

## Recommended Model

Use a source-available commercial licensing strategy:

1. Public PolyForm Noncommercial-style license for personal and noncommercial use.
2. Separate commercial license for enterprise, agency, hosted-service, or revenue-generating use.

## Alternative If True Open Source Matters More

Use Apache-2.0.

Apache-2.0 is permissive and commercially usable by anyone. It is better for adoption, weaker for enterprise monetization control.

## Current Repo State

This package includes `LICENSE.md` as a practical wrapper around PolyForm Noncommercial and `COMMERCIAL.md` for the commercial licensing position. Review both before public release.

## Apache-2.0 Comparison

Apache-2.0 is better when adoption, contribution, and low-friction company use matter more than controlling commercial use.

PolyForm Noncommercial is better when public source access and personal use are allowed, but commercial and enterprise use should require permission.
