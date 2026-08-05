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
- Review the exact diff. Freeze one complete finding set, perform no more than
  the repair authorized by the campaign, and rerun the declared validation.
- Publish only the frozen reviewed head through the campaign engine outbox.
- Manual tasks do not require volatile Git state. Automated tasks require a
  current actor lease from the executable engine.
- Stop immediately through `campaign_engine/cli.py cancel` when the user says
  STOP. Do not resume a cancelled campaign from another task or branch.

Lifecycle details belong only to `campaign_engine/reducer.py`, not this skill.
