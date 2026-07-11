# Local Validation Task Contract

Use this contract when one worker validates another worker's exact repository state. Local validation is evidence for a bounded claim. It is not permission to merge, deploy, release, mutate production, access secrets, or overstate what local checks prove.

## Validation Identity
- Repository: {{repository_identity}}
- Branch: {{full_branch_name}}
- Base commit: {{full_base_sha}}
- Tested head: {{full_tested_head_sha}}
- Dirty state: {{clean_or_dirty}}

The validator must confirm each identity value from live Git immediately before testing. If the tested state is dirty, list the changed and untracked files. A later commit or working-tree change invalidates the result until retested.

Use a credential-free remote repository identity when available. Keep machine-local paths and private repository identifiers in local-only evidence, not committed public summaries.

## Environment And Authority Boundaries
- Platform: {{operating_system_and_version}}
- Runtime versions: {{runtime_names_and_versions}}
- Tool versions: {{validation_tool_names_and_versions}}
- Accounts or credentials boundary: {{none_local_test_or_other_explicit_boundary}}
- Secrets recorded: No
- Production mutation: No
- Deploy authorized: No
- Merge authorized: No
- Merge authority source: Not authorized for this task
- Release authorized: No

Never request, display, copy, or store a credential, token, private key, production record, or other secret in the task, command output, screenshot, fixture, or report. Stop if validation requires authority outside this boundary.

## Acceptance Criteria
- [ ] {{observable_criterion_1}}
- [ ] {{observable_criterion_2}}

## Commands And Actions To Perform
- {{exact_command_or_manual_action_1}}
- {{exact_command_or_manual_action_2}}

Use repository-native commands. Record the command or action exactly, its result, and a concrete evidence reference. Do not convert an unavailable check into a pass.

## Required Evidence Record

For every completed action, record one typed entry:

- `Command: <exact command> | Result: Pass or Fail | Evidence: <exit code and concise output fact>`
- `Manual action: <exact action> | Result: Pass or Fail | Evidence: <artifact or observation>`
- `File: <existing relative artifact path>`
- `Artifact: <existing relative artifact path>`
- `URL: <checked HTTPS evidence URL>` only when external access was authorized

Placeholder text, a path that does not exist, a different branch or commit, and an unsupported claim are not evidence.

## Retest
- Retest trigger: any code, configuration, fixture, dependency, branch, or working-tree change after the first result
- Retest result: {{pass_or_fail}}
- Retest evidence: {{current_tested_head_evidence}}

## What This Proves
- {{claim_directly_supported_by_the_recorded_evidence}}

## What This Does Not Prove
- Local checks do not prove browser or UI behavior, deployment success, production behavior, or complete security unless each claim has separate authorized evidence.

## Remaining Risks Or Blockers
- {{risk_blocker_or_none_identified_with_basis}}

## Exact Next Action
- {{one_bounded_action_owner_and_target}}
