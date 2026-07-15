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
- One human-reviewed lane manifest.

## Default Mode

Manual mode is recommended. Codex creates worktrees and prompts, then the user
opens each new Codex thread manually.

## Automated Thread Mode

Automated thread creation and parent-orchestrator chaining are disabled. The user
must open each approved lane deliberately.

## Approval

Codex must wait for an explicit yes before creating worktrees or threads.
