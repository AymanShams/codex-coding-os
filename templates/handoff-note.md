# Session Handoff

## Session Boundary Decision
- Reason:
- Exact next permitted action:
- New session required: Yes or No

## Handoff Routing
- Automation mode: Off, Sequential manual, or Parent/orchestrator
- Actor role: Single session, Parent, Implementer child, Review child, Fix child, or Publication child
- Handoff target: Manual next session, User, Parent, Child, or None
- Parent consumes next: Yes or No
- Prompt is fallback only: Yes or No

## Current State
{{what_is_true_now}}

- Current-state path: `docs/delivery/current-state.md`
- Active-slice manifest path: `docs/delivery/active-slice-manifest.json`
- Workflow-manifest path: `project-documentation-manifest.json`

## Decisions Made
| Decision | Alternatives rejected | Reason | Owner / approver | Revisit trigger | Evidence test | Status | Authority source |
|---|---|---|---|---|---|---|---|
| {{decision}} | {{alternatives}} | {{reason}} | {{owner_or_approver}} | {{revisit_trigger}} | {{evidence_test}} | Proposed, Approved, Rejected, Deferred, Needs human, or Superseded | {{source}} |

## Material Decisions Still Needing Human Judgment
- {{decision_or_none}}

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

## Recommended Next Action
{{next_task}}

## Next Prompt
```text
Use this prompt only when the handoff target is Manual next session or when parent automation tooling is unavailable.

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
