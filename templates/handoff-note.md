# Session Handoff

## Session Boundary Decision
- Reason:
- Exact next permitted action:
- New session required: Yes or No

## Current State
{{what_is_true_now}}

- Current-state path: `docs/delivery/current-state.md`
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
Continue from this repo state.

Run python scripts/agent/session_continuity.py start.
Read AGENTS.md, docs/delivery/current-state.md, its latest handoff, project-documentation-manifest.json, and the controlling docs.
Then handle only this next permitted task:
{{next_task}}

If the workflow manifest does not permit coding, continue from its first blocked or incomplete phase. Before editing, summarize the affected files and validation plan.
```
