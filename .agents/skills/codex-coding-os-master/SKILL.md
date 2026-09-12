---
name: codex-coding-os-master
description: Use for software implementation, review, validation, publication, installation, cancellation, or recovery through the single executable Coding OS campaign engine.
---

# Codex Coding OS Master

Use `scripts/agent/campaign_engine/cli.py` as the only automated lifecycle
authority. The canonical installed copy is under
`%USERPROFILE%\.codex\coding-os`; its state is under
`%USERPROFILE%\.codex\coding-os-state`.

For manual coding, follow the current task, repository sources, exact Git
identity, and project validation. For automated work, admit one immutable finite
campaign specification, obtain approval of its exact specification digest, and
run only the engine command named in its receipt.

Start with the customer's requested outcome and approved sources. Reuse prior
decisions while their scope and assumptions still hold. Derive routine technical
choices from existing architecture and validation. The founder need not choose
files, test commands, or internal code structure.

Ask the founder only when an unresolved choice changes customer behavior,
priorities, spending, delivery commitments, or consequential external actions.
State the consequence, your recommendation, and the smallest decision needed.
Pause dependent work while that answer is required and continue independent work.
Resolve source conflicts through established source precedence first. Bring only
the remaining business choice to the founder. Investigate technical blockers
within scope and report their status.
When the current request explicitly presents a new unresolved business proposal,
surface that decision once with a recommendation while continuing work already
authorized. An existing implementation does not answer a newly raised spending
or customer-behavior choice. Do not replace the question with "unless you want"
or silently discard the proposal. Prefer preserving the approved behavior when
sources conflict and no customer benefit supports changing it. Producing a code
change is not itself a reason to change the customer's outcome.

For a status request, report the outcome, evidence, and remaining blocker from
the engine or current Git evidence. Do not reopen answered questions, require a
new documentation run, or create a status-only change. Change durable documents
only when an approved requirement or technical contract has changed.
Report only progress that the evidence establishes. A failed test alone proves
neither that implementation is complete nor that delivery occurred.

Use the installed `catalogue-router` for non-trivial manual work. Repository
`AGENTS.md` owns routing and authority boundaries. Skill selection grants no
additional action authority.
Check `doctor` entry prerequisites before promising routed execution. A missing
canonical router is an operating dependency. Do not activate the repository's
reference router. Declare required host tools in the proposed nodes so unavailable
browser or other capabilities are rejected before admission.

Use the installed CLI's `--help` and the command in the engine receipt. Command
contracts belong to `scripts/agent/campaign_engine/cli.py`.

For a new automated campaign, draft the proposed specification from approved
business scope and repository sources. Use `prepare --spec <proposed.json>
--repository <exact-repo> --output <new.json>` to derive and verify Git identity,
the runtime pin, command working directories, and acceptance-source hashes.
Keep source-linked acceptance scenarios, spending, deadlines, operation limits,
and publication authority explicit. Preparation creates no campaign and grants
no approval. Present the business outcome and unresolved business choices to the
founder, then use the existing admission and approval commands when authorized.

Lifecycle rules belong to `campaign_engine/reducer.py`. Git-tracked execution
state is informational. Retired case commands return `LEGACY_ENGINE_RETIRED`.
