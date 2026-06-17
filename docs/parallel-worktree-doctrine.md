# Parallel Worktree Doctrine

Parallel worktree lanes are an advanced execution control for material coding work.
They are not a roleplay framework and they are not a way to bypass project discovery.

Use them only when the workflow manifest already permits coding and the task can be
split into isolated file ownership lanes.

## Plain-English Rule

Codex may offer parallel worktrees when a task is large, high-risk, or naturally
separable. Codex must not create them until the user explicitly approves the exact
lane plan.

The default mode is manual: Codex creates worktrees and paste-ready prompts, and
the user opens each lane thread intentionally.

Fully automated thread creation is advanced. Use it only when Codex has trusted
thread-creation tools available and the parent session can still inspect every
prompt before work starts.

## Allowed

- Implementation plus fresh-context review.
- Implementation plus test hardening.
- Implementation plus documentation alignment after accepted implementation changes.
- Implementation plus security review for high-risk changes.
- Time-boxed architecture alternatives before a final ADR is accepted.

## Blocked

Parallel worktree lanes are blocked when:

- the workflow manifest or active-slice manifest does not permit coding;
- material decisions or source conflicts remain open;
- the Git baseline is dirty, behind, or unreviewed;
- lane file ownership cannot be stated;
- lanes would edit the same files;
- lanes would edit controlled source files without explicit approval;
- validation commands are missing;
- the user has not explicitly approved creating worktrees.

## Lane Model

Each lane is defined by:

- one objective;
- one branch;
- one base commit;
- allowed files;
- forbidden files;
- controlling sources;
- validation commands;
- review requirement;
- stop conditions;
- handoff requirement.

Commit-safe audit files live under `docs/delivery/parallel-worktrees/<run-id>/`.
They must not contain local absolute paths.

Local runtime files live under `.codex/parallel-worktrees/<run-id>/`. Runtime
contracts, prompts, auto-thread request files, and lane manifests may contain
machine-specific worktree paths and are excluded from Git.

Lane names describe bounded work, not personas. Use names such as `auth-api`,
`checkout-tests`, `docs-alignment`, or `security-review`. Do not use roleplay names
such as `pm-agent`, `architect-agent`, or `qa-persona`.

## Integration Ownership

The parent/orchestrator session owns:

- the lane plan;
- user approval;
- `docs/delivery/current-state.md`;
- merge order;
- final validation;
- cleanup decisions.

Lane sessions must not merge themselves and must not update current delivery state.

## Controlled Files

These files are locked by default:

- `AGENTS.md` and scoped `AGENTS.md`;
- `CLAUDE.md` files;
- `project-documentation-manifest.json`;
- `docs/delivery/current-state.md`;
- `docs/delivery/**`;
- migrations and schema files;
- dependency and package manager files;
- deployment and CI configuration;
- auth, security, payment, billing, export, and admin files.

If a lane needs a controlled file, stop and ask whether the lane should be expanded,
split, or converted to a serial workflow.

## Merge Protocol

1. Validate the lane contract.
2. Run the lane validation commands.
3. Produce a lane handoff with changed files and evidence.
4. Review the lane from fresh context when required.
5. Rebase the lane from current main.
6. Merge one lane.
7. Run integration validation.
8. Update docs or ADRs only after accepted implementation changes.
9. Update current state from the parent session.
10. Continue to the next lane.

## Thread Modes

### Manual Mode

Manual mode is the default and recommended mode. Codex creates worktrees and
paste-ready prompts. The user opens each new Codex thread manually.

### Fully Automated Thread Mode

Fully automated thread mode can be offered only after manual mode is explained.
It requires explicit user opt-in and an acknowledgement that automated thread
creation can send the wrong prompt, miss local context, or make ownership unclear.

If thread-creation tools are unavailable, Codex must fall back to manual prompts.

## Command Flow

For non-technical users, prefer this sequence:

1. `python scripts/agent/worktree_lanes.py plan ...`
2. Review the generated offer under `docs/delivery/parallel-worktrees/<run-id>/`.
3. Create lanes only after explicit approval with
   `python scripts/agent/worktree_lanes.py create --from-run <run-id> --user-approved`.
4. Validate lane ownership with
   `python scripts/agent/worktree_lanes.py validate --run-id <run-id>`.

The validation command reports contract status only. Lane sessions must still run
their declared validation commands and include the results in the lane handoff.

## Failure Response

If any gate fails, Codex must stop and offer a serial workflow. Do not partially
create worktrees, do not create threads, and do not continue from stale approvals.
