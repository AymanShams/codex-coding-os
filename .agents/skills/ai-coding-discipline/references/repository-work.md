# Repository work

## Planning and following a change

When a change needs an implementation plan, map each material requirement to
a code change and an observable check. Keep setup with the change it enables.
Call out any requirement that lacks a way to check it.

If a step produces an unexpected result, compare the observed behavior with
the controlling requirement and the current interface. Correct a stale step
when the implementation already satisfies the requirement. Investigate the
code when the behavior violates it.

## Diagnosing and verifying a fix

Reproduce the original symptom with the relevant input and environment. If
the failure is intermittent, capture a failing case and a comparable passing
case when available. Follow the input through the affected components until
the first observation that differs from the expected result. Test one
explanation there before changing another variable.

When test-first development is selected, run a test of the intended behavior
and check why it fails. Fix a broken fixture or setup before counting that
failure as evidence. Make the smallest implementation change that passes the
test, then refactor with the relevant checks passing. To verify a bug fix,
exercise the original failing behavior against the candidate and report any
part that could not be reproduced.

## Delegated implementation

When delegation is already authorized, check whether assignments edit the
same files, share mutable state, or depend on an unsettled interface.
Sequence dependent changes. Give independent workers their edit boundaries,
the requirement and the expected result. Record the starting revision.
Review all commits through the worker's final revision and inspect any
uncommitted changes included in the handoff.

## Review feedback

Trace a reported defect through the actual caller, inputs and relevant tests.
Evaluate the proposed fix separately. If it would break another supported
case, identify that case and choose a correction that preserves it.

## Workspace and integration

Before creating a Git worktree, check the current checkout against
`git worktree list --porcelain`. Use `git rev-parse --show-superproject-working-tree`
to identify submodule context. Reuse the intended isolated checkout if it
already exists.

For an authorized merge, confirm the intended destination branch and run the
relevant checks on the merged result. Before any authorized cleanup, confirm
the exact worktree path and who created it. Inspect its status for uncommitted
and untracked work. Preserve the directory if ownership is unclear or files
still need to be kept.
