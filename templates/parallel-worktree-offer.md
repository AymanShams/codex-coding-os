# Parallel Worktree Offer

Codex can split this task into separate worktree lanes only if the workflow
manifest already permits coding and each lane has isolated file ownership.

## Why Codex Is Offering This
-

## Proposed Lanes
| Lane | Purpose | Allowed Files | Review |
|---|---|---|---|
|  |  |  |  |

## What Codex Will Create
- Separate Git worktrees.
- Separate branches.
- One task contract per lane.
- One paste-ready prompt per lane.
- One parent/orchestrator lane manifest.

## Default Mode

Manual mode is recommended. Codex creates worktrees and prompts, then the user
opens each new Codex thread manually.

## Fully Automated Thread Mode

Fully automated thread mode is advanced. It may send the wrong prompt to a thread,
miss local context, or make ownership unclear. Use it only if Codex confirms that
trusted thread-creation tools are available and the user explicitly accepts the risk.

## Approval

Codex must wait for an explicit yes before creating worktrees or threads.
