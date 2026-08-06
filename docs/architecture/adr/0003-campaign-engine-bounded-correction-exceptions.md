# ADR 0003: Campaign Engine Bounded Correction Exceptions

- Status: Accepted
- Date: 2026-08-06
- Owner: Coding OS implementation owner
- Approver: Ayman Shams
- Authority source: The user's 2026-08-06 instruction to proceed with all bounded correction recommendations across Coding OS, Leheta, and the HealPath Platform universal layer

## Decision

Record the replacement rollout's historical sequencing deviations as explicit, non-precedential exceptions and complete one bounded correction. The correction is limited to the live-head defects identified during verification:

1. Remove remaining lifecycle-authority text and validator blind spots in the HealPath adapter.
2. Add real mutation testing for critical reducer invariants.
3. Remove retired installed direct command rules while retaining the read-only `case_state.py` tombstone.
4. Resolve the vendor-retired `doc` skill conflict without modifying unrelated skills.
5. Restore explicit universal-policy synchronization after the markerless opt-out state and make ordinary reinstalls preserve policy by default.
6. Obtain a fresh two-reviewer acceptance of the actual installed and live repository heads before final acceptance.

This record documents authority for the bounded correction. It is not lifecycle state, publication authority, or permission for any later correction cycle.

## Historical Exceptions

The following deviations are accepted as historical facts only:

1. The initial repository merges and runtime installation occurred before a final fresh two-reviewer exact-head acceptance of the actual live heads. The recorded sequence was:
   - Coding OS PR #41 head `3caf927185cb2aca4ebb76b2593d9812c949df6e` merged as `55014c4205a36f3b284222ece0b8bb6a6f17f31c`, followed by direct-main heads `144bbf6f1ca8cd8f2b8ce87ad4dcb2df90e15199` and `ff8d3e96aabb64705dcb5db52f5b441ced527011`.
   - Leheta PR #36 head `dbd2955e5566074436ff0d97f672736a09eeb1b4` merged as `828a25f83390b6dfdcf05b4fbf6e374946a516a0`, followed by direct-main head `14220fca14a2eadf9705ccea57d96fa08aed8c10`.
   - HealPath PR #99 head `ddc18a69d22b0193667d6a4550b1f0e673d2ffd3` merged as `31b2d7d2deb449399ece0ad77689808f77e3fa47`. The bot review covered `9716ada2101e2cf9f9015e9a295e26f390ca9ec3`, not the final PR head.
2. The initial acceptance evidence did not include execution by a mutation-testing framework.
3. Direct allow rules for the retired installed `case_state.py` command remained after the legacy engine was retired.
4. The Codex primary runtime's vendor skill reconciliation removed the Coding OS-managed `doc` skill, which caused installed runtime bootstrap verification to fail.
5. The immediate exact-file restoration transaction `c3cd340a23cb448c8640bb880f1a69dd` used the installer's prior default policy opt-out behavior. It restored the runtime payload but removed the managed universal policy blocks.

These exceptions do not convert failed or missing evidence into success. Current acceptance requires fresh evidence from the corrected exact heads and installed bundle.

## Alternatives Rejected

- Treat the deviations as implicit or erase them from the acceptance record.
- Reintroduce the retired engine or keep its direct command permissions as a fallback.
- Modify the external vendor runtime or unrelated universal skills to retain a duplicate `doc` skill.
- Make ordinary bundle reinstalls silently remove managed policy.
- Start another open-ended migration or correction cycle.

## Reason

The bounded correction closes concrete discrepancies between the merged replacement contract and the live installed runtime. Explicitly preserving the deviations prevents later evidence from being misread while keeping the current reducer, state store, and installation transaction as the only active engine path.

## Revisit Trigger

Revisit only if the vendor runtime changes its declared retired-skill inventory, the universal-policy ownership contract changes, or fresh exact-head review finds a material defect outside this bounded correction. Any material expansion requires a new user decision.

## Evidence Test

Acceptance requires all of the following on the final exact heads:

- The verified bundle omits `doc` from managed-skill inventory and preserves every unrelated managed and unmanaged skill.
- An explicit universal-policy install succeeds from the exact markerless campaign layout.
- A subsequent install with no policy action preserves both managed policy files byte-for-byte and retains their authority record.
- Only exact direct allows targeting the installed retired `case_state.py` path and the exact retired `corepack pnpm run agent:case-state -- --help` alias are removed.
- The pinned fork-based mutation gate kills every behaviorally distinct mutation in the selected critical reducer invariants and reports only exact, documented equivalent mutants.
- Coding OS, Leheta, and HealPath validation and admission probes pass against the actual installed bundle.
- Two fresh read-only reviewers accept the complete exact-head correction and a closure review confirms any combined repair.

## Outcome

The historical deviations are accepted as bounded exceptions. They do not waive current tests, review, installation proof, or exact-head acceptance, and they do not authorize a successor engine or a second repair cycle.
