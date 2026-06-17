# Session Handoff

## Session Boundary Decision
- Reason:
- Exact next permitted action:
- New session required: Yes or No

## Current State
{{what_is_true_now}}

- Current-state path: `docs/delivery/current-state.md`
- Active-slice manifest path: `docs/delivery/active-slice-manifest.json`
- Workflow-manifest path: `project-documentation-manifest.json`

## Decisions Made
- {{decision_1}}
- {{decision_2}}

## ADRs Created or Updated
| ADR | Status | Summary |
|---|---|---|
| {{adr_path_or_id}} | Proposed, Accepted, or Superseded | {{summary}} |

## Files Changed
| File | Change |
|---|---|
| {{file}} | {{change}} |

## Validation Run
| Command | Result | Notes |
|---|---|---|
| {{command}} | Pass, Fail, or Not run | {{notes}} |

## Known Issues
- {{issue_1}}
- {{issue_2}}

## Work Explicitly Not Done
- {{excluded_or_deferred_work}}

## Next Prompt
```text
Continue this project in the repository containing this handoff.

First run:
python scripts/agent/session_continuity.py start --start-new

Then read:
1. AGENTS.md and the closest scoped AGENTS.md files
2. docs/delivery/current-state.md
3. docs/delivery/active-slice-manifest.json
4. this latest handoff
5. project-documentation-manifest.json
6. the task-controlling docs

Handle only this next permitted task:
{{next_task}}

If the workflow manifest or active-slice manifest does not permit coding, continue from its first blocked or incomplete phase. Before editing, summarize the affected files and validation plan.
```
