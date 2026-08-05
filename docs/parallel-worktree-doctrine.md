# Campaign Worktree Doctrine

An automated campaign binds one exact Git root and one exact worktree. Every
write-capable actor lease binds the branch, base commit, candidate head,
filesystem identity, allowed paths, authority epoch, cancellation epoch, and
fencing epoch before the first turn.

The engine rejects another active write lease for the same resource or an
overlapping admitted scope. Read-only reviewers may run concurrently using
separate read-only resources. Their sandboxes expose no writable roots.

Repository state files and handoffs are informational. They cannot create a
lane, change scope, replenish budget, waive review, publish, cancel, or reset a
campaign.

User branches, worktrees, and uncommitted files are never deleted or reset by
the engine. Admission must use an isolated clean worktree when existing work is
present.
