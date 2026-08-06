# ADR 0003: Campaign Engine Correction Exceptions

- Status: Accepted
- Date: 2026-08-06
- Owner: Coding OS implementation owner
- Approver: Ayman Shams
- Authority source: The user's 2026-08-06 instructions to complete the original cross-repository Coding OS replacement and its named corrections

## Decision

Record the replacement rollout's historical sequencing deviations as factual, non-precedential exceptions and complete the named corrections in the same implementation slice:

1. Restore the exact managed `doc` skill through the verified installer without modifying unrelated skills.
2. Remove remaining repository lifecycle-authority text and validator blind spots in Leheta and HealPath.
3. Add real mutation testing for critical reducer invariants.
4. Remove retired installed direct command rules while retaining the read-only `case_state.py` tombstone.
5. Restore explicit universal-policy synchronization after the markerless installation state and make ordinary reinstalls preserve managed policy by default.
6. Validate the final three-repository diff only for alignment with the original replacement prompt, then merge, install, and verify the live runtime.

This record is historical evidence. It is not lifecycle state, publication authority, or a second engine.

## Historical Exceptions

The following deviations are accepted as historical facts only:

1. The initial repository merges and runtime installation occurred before final acceptance of the actual live heads:
   - Coding OS PR #41 head `3caf927185cb2aca4ebb76b2593d9812c949df6e` merged as `55014c4205a36f3b284222ece0b8bb6a6f17f31c`, followed by direct-main heads `144bbf6f1ca8cd8f2b8ce87ad4dcb2df90e15199` and `ff8d3e96aabb64705dcb5db52f5b441ced527011`.
   - Leheta PR #36 head `dbd2955e5566074436ff0d97f672736a09eeb1b4` merged as `828a25f83390b6dfdcf05b4fbf6e374946a516a0`, followed by direct-main head `14220fca14a2eadf9705ccea57d96fa08aed8c10`.
   - HealPath PR #99 head `ddc18a69d22b0193667d6a4550b1f0e673d2ffd3` merged as `31b2d7d2deb449399ece0ad77689808f77e3fa47`. The recorded bot review covered `9716ada2101e2cf9f9015e9a295e26f390ca9ec3`, not the final PR head.
2. The initial acceptance evidence did not include execution by a mutation-testing framework.
3. Direct allow rules for the retired installed `case_state.py` command remained after legacy-engine retirement.
4. Runtime skill reconciliation removed the Coding OS-managed `doc` skill and caused installed runtime bootstrap verification to fail.
5. The immediate exact-file restoration transaction `c3cd340a23cb448c8640bb880f1a69dd` used the installer's prior default policy opt-out behavior. It restored the runtime payload but removed the managed universal policy blocks.
6. Later exact-head inspection exposed two additional repository-text defects: a Leheta validator negation false positive and contradictory HealPath SP-SLICE-007 merge status. Those defects were corrected before final publication.

These facts do not convert missing evidence into success and do not reactivate any retired command or repository lifecycle gate.

## Alternatives Rejected

- Hide or erase the sequencing deviations.
- Reintroduce the retired engine or retain its direct command permissions as a fallback.
- Remove the explicitly requested managed `doc` skill.
- Modify unrelated universal skills.
- Make ordinary bundle reinstalls silently remove managed policy.

## Reason

The corrections close concrete discrepancies between the original replacement contract and the live installation while preserving one reducer, one external store, and one executable engine path.

## Revisit Trigger

A material requirement change requires a new explicit user decision. Historical records in this ADR cannot authorize work.

## Evidence Test

Acceptance requires all of the following on the final exact heads:

- The verified bundle contains the exact managed `doc` skill and preserves every unrelated managed and unmanaged skill.
- An explicit universal-policy install succeeds from the exact markerless campaign layout.
- A subsequent install with no policy action preserves both managed policy files byte-for-byte and retains their authority record.
- Only exact direct allows targeting the installed retired `case_state.py` path and the exact retired `corepack pnpm run agent:case-state -- --help` alias are removed.
- The pinned fork-based mutation gate kills every behaviorally distinct mutation in the selected critical reducer invariants and reports only exact documented equivalent mutants.
- Coding OS, Leheta, and HealPath validations pass.
- The final changed files and behavior map directly to the original replacement prompt and the explicitly named correction scope.
- The exact merged bundle installs successfully, engine doctor passes, and both repository admission probes pass against that installation.

## Outcome

The historical deviations are recorded, the named corrections remain inside the original replacement scope, and no retired lifecycle authority or follow-up engine phase is created.
