# Documentation Validation Checks

Use these checks before reporting a new project documentation setup as complete.

## Required Checks

1. Workflow manifest: `scripts/validate_workflow_manifest.py` passes for the documentation contract, conditional trigger evidence, artifact lineage, and phase evidence.
2. Manifest boundary: `document_role` identifies a stable documentation ledger and `execution_authority` is `false`.
3. File presence: expected product documents, repository docs, instruction files, and optional work summary exist.
4. Source inventory: sources are classified and controlling sources are named.
5. Source lock: filled documents identify controlling sources and unresolved assumptions.
6. Approval evidence: material decisions, controlled documents, and TDD approvals are recorded where required.
7. Stage fit: documents do not claim implementation or operational evidence that does not exist.
8. Drift: material terms, workflows, interfaces, and scope agree across the project brief, product documents, TDD, and repository docs.
9. Generic filler: `scripts/validate_filled_artifacts.py` passes for filled documents. Blank templates are excluded.
10. Name hygiene: obsolete project names and template placeholders are absent from filled artifacts.
11. Secret hygiene: no credentials, tokens, private keys, generated passwords, or real environment values are staged.
12. Agent context: root and scoped instructions identify stable sources, exact validation, and the installed campaign CLI without reproducing lifecycle rules.
13. Git evidence: report exact root, branch, HEAD, and working-tree state when a repository exists.
14. Diff integrity: run `git diff --check` when a repository exists.

## Automation Separation

The documentation validators do not execute product tests, admit campaigns, approve campaign specifications, dispatch workers, or publish changes. Verify automation separately with:

```text
python <installed-cli> --json doctor
python <installed-cli> --json status --repository-root .
```

## Final Report

Report created or updated paths, checks passed, checks not run, unresolved documentation gaps, and remaining user decisions. Report product completion and campaign status separately from documentation completion.
