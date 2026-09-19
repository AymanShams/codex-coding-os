# Exact-Head Review Doctrine

Review one frozen candidate head and diff against the approved objective,
acceptance criteria, stable product sources, and explicit non-goals.

The review packet binds the campaign, node, specification digest, repository,
base, candidate head, tree, diff digest, allowed paths, validation evidence, and
required reviewer identity.

Review correctness, reuse, duplication, architecture, tests, performance, error
handling, public interfaces, schemas, migrations, role and permission
enforcement, deployment behavior, product-source drift, and refactoring inside
the approved scope.

The complete finding set freezes once. One combined repair may address the
frozen blockers. Revalidation runs every relevant command. Closure checks the
original blocker identifiers and records any repair-introduced defects. Any
remaining or new blocking defect fails that exact node. Closure cannot create
another repair or review generation.

A blocker identifies an unmet acceptance expectation, an established invariant
with evidence, or a reproducible regression. Explicit nonblocking observations
remain in the review evidence without authorizing more work. Reviewers check
the actual customer behavior and relevant existing sources. Passing tests,
matching hashes, and a completion label do not substitute for that judgment.

Reviewers are read-only evidence producers. Review prose, comments, labels, and
repository mirrors cannot authorize repair or publication.
