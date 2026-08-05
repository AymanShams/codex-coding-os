# Campaign Worktree Contract

## Immutable binding

- Campaign ID:
- Node ID:
- Specification digest:
- Authority epoch:
- Cancellation epoch:
- Actor ID:
- Actor role:
- One-use lease ID:
- Fencing epoch:
- Repository remote:
- Exact Git root:
- Exact worktree:
- Branch:
- Base SHA:
- Allowed paths:

## Objective


## Non-goals

- No writes outside the allowed paths.
- No graph, scope, budget, validation, review, or publication changes.
- No successor campaign or lifecycle decision in repository files.

## Validation commands

Each command must be executed by the campaign engine with its approved
executable, argument array, working directory, environment allowlist, timeout,
output limit, candidate head, expected tree condition, and required exit code.

## Terminal receipt

- Terminal state:
- Exact head:
- Tree:
- Diff digest:
- Receipt digest:

Late receipts and stale fencing epochs are rejected.
