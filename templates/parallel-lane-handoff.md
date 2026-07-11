# Parallel Lane Handoff

## Lane Identity
- Repository: {{repository_identity}}
- Run ID: {{run_id}}
- Lane: {{lane_slug}}
- Branch: {{full_branch_name}}
- Base commit: {{full_base_sha}}
- Tested head: {{full_tested_head_sha}}
- Dirty state: {{clean_or_dirty}}
- Handoff target: Parent by default in parent/orchestrator automation
- Parent consumes next: Yes or No

Use the safe repository identity recorded in the local lane manifest. Keep machine-local paths and private repository identifiers out of committed summaries.

## Environment And Authority Boundaries
- Platform: {{operating_system_and_version}}
- Runtime versions: {{runtime_names_and_versions}}
- Tool versions: {{validation_tool_names_and_versions}}
- Accounts or credentials boundary: {{none_local_test_or_other_explicit_boundary}}
- Secrets recorded: No
- Production mutation: No
- Deploy authorized: No
- Merge authorized: No
- Merge authority source: Not authorized for this lane
- Release authorized: No

Never include a credential, token, private key, production record, or other secret. Redact sensitive command output before recording evidence.
For a merged lane, replace the default merge fields only after an independent user, owner, maintainer, reviewer, parent, or pull-request authority explicitly authorizes the merge. The validator checks the tested head against the live target branch. It never performs the merge.

## Work Completed
- {{bounded_outcome_actually_completed}}

## Changed Files
- `{{relative_path_to_changed_file}}`

## Acceptance Criteria
- [ ] {{observable_criterion_and_judgment}}

## Commands And Actions Performed
- Command: `{{exact_command}}` | Result: {{pass_or_fail}} | Evidence: {{exit_code_and_concise_output_fact}}
- Manual action: `{{exact_action}}` | Result: {{pass_or_fail}} | Evidence: {{artifact_or_observation}}

## Evidence References
- Command: `{{exact_command}}` | Result: {{pass_or_fail}} | Evidence: {{exit_code_and_concise_output_fact}}
- File: `{{relative_path_to_existing_artifact}}`
- URL: `{{checked_https_evidence_url}}` only when external evidence was authorized and checked

File and artifact evidence must use repository-relative paths that resolve inside the lane worktree.

## Retest Result
- Result: {{pass_or_fail}}
- Evidence: {{exact_retest_and_current_head_result}}

## What This Proves
- {{behavior_demonstrated_by_the_recorded_local_evidence}}

## What This Does Not Prove
- Local checks do not prove browser or UI behavior, deployment success, production behavior, or complete security unless each claim has separate authorized evidence.

## Checks Not Run
- {{material_check_and_reason_or_none_omitted}}

## Source Documents Read Or Used
- `{{relative_path_to_source}}`

## Review Packet
- Diff: exact base-to-head or working-tree diff reviewed
- Controlling sources: exact paths
- Validation output: exact evidence references above
- Non-goals: what remained excluded
- Scope creep and hidden dependencies: none, or exact items
- Assumptions that entered code: none, or exact items
- Local runtime files: `.codex/parallel-worktrees/<run-id>/`

## Remaining Risks Or Blockers
- {{remaining_risk_or_none_identified_with_basis}}

## Merge Readiness
- Ready / Blocked / Needs parent decision

## Stop Conditions Hit
- {{exact_stop_condition_or_none_identified}}

## Exact Next Action
- {{one_bounded_action_owner_and_target}}
