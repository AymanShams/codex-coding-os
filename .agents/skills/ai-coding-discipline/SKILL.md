---
name: ai-coding-discipline
description: Use for bounded repository implementation and verification, manually or as one node admitted by the Coding OS campaign engine.
---

# AI Coding Discipline

- Read the exact repository rules, controlling sources, target code, callers,
  and tests before editing.
- Preserve user changes and isolate material work in a clean worktree.
- Keep the diff within the declared objective and allowed paths. Reuse existing
  code before adding abstractions or dependencies.
- Run trusted validation commands bound to the exact candidate head. A nonzero
  process exit is failure regardless of assertion text.
- Review the exact diff. For automated campaigns, freeze the required finding
  set, follow the campaign's repair limits, and rerun its declared validation.
  For manual work, address relevant findings within the current task and rerun
  affected checks when changes warrant it.
- For automated campaigns, publish only the frozen reviewed head through the
  campaign engine outbox. Manual delivery follows the current user authorization
  and repository checks and does not require a campaign.
- Manual tasks do not require volatile Git state. Automated tasks require a
  current actor lease from the executable engine.
- Stop work immediately when the user says STOP. For an active automated
  campaign owned by this task, cancel it through `campaign_engine/cli.py cancel`.
  Manual work does not require a campaign command. Do not resume a cancelled
  campaign from another task or branch.

For workspace isolation, implementation planning, fault isolation, test-first
work, delegated changes, review feedback or branch integration, use the applicable section of
[Repository work](references/repository-work.md) within the current assignment.

Lifecycle details belong only to `campaign_engine/reducer.py`, not this skill.
