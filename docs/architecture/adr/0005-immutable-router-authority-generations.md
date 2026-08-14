# ADR 0005: Immutable Router Authority Generations

- Status: Accepted
- Date: 2026-08-14
- Owner: Codex Coding OS implementation owner
- Approver: Ayman Shams
- Authority source: The user's 2026-08-14 instruction to implement the permanent Catalogue Router and local worker freshness repair

## Context

The live router previously bound executable code, schemas, local configuration projection, plugin inventory, and worker availability into one mutable manifest file and one global freshness result. A legitimate plugin or application update therefore disabled every capability. Rebuilding the manifest repaired the immediate state but did not remove the recurrence mechanism. The repository also had no authoritative source-to-live router transaction, and gateway-managed workers had no immutable release identity bound to the manifest.

## Decision

1. Keep the ordinary Coding OS installer unable to deploy or activate the universal router.
2. Provide a separate, explicitly authorized static router deployment transaction with a fixed allowlist, content digest, target-local lock, compare-and-swap preconditions, durable journal, rollback, and terminal receipt.
3. Preserve deployment-owned policy materialization outside that static bundle. The router owns `_hook_io.py` and has no runtime dependency on the ordinary installer's shared `_common.py` helper.
4. Split static and dynamic authority. Any static code or schema mismatch denies the complete router.
5. Scope only provable dynamic drift. Plugin drift is isolated by exact package closure, recognized application configuration drift by exact capability mapping, and worker identity drift by worker family. Unknown, malformed, or mixed closure remains globally denied.
6. Build every active manifest as an immutable generation. Promote one `current-generation.json` pointer through compare-and-swap and never overwrite an existing generation identifier.
7. Treat the mutable `active-capabilities.json` file as a compatibility copy after a generation pointer exists.
8. Bind gateway-managed workers through a deployment-owned worker runtime bill of materials containing the exact identity-file hash, release identifier, source root relationship, route schema, and registry schema.
9. Report router admission, dynamic authority, and each worker runtime as separate status components.
10. Retain route schema 3.0 and registry schema 3 because the route and registry payload contracts do not change. Authority rotation invalidates new verification through the existing exact manifest and policy hashes.

## Consequences

A routine plugin or application update can no longer disable unrelated skills when the changed dependency closure is provable. A broken or unrecognized change still fails closed. The universal layer gains one reproducible source deployment path, one immutable active manifest path, and exact worker release binding. Static deployment and dynamic generation promotion remain separate transactions, so a partially completed static update cannot silently become authoritative.

The tradeoff is additional operational state: immutable generations, a current pointer, update receipts, quarantine observations, and a worker runtime bill of materials. These files are deployment-owned and must remain outside the ordinary Coding OS package.

## Evidence test

Acceptance requires all of the following:

- static drift denies every route
- one changed plugin package quarantines only its recorded capabilities
- a malformed or unprovable package closure denies every route
- recognized application configuration drift quarantines only mapped surfaces
- worker identity drift disables only the affected external worker family
- generation identifiers are immutable
- pointer promotion is compare-and-swap protected and recoverable
- exact transaction replay returns the original terminal receipt
- the ordinary package installer still rejects all router targets
- LAS and Antigravity verify route schema 3.0, registry schema 3, current authority hashes, and their exact runtime identities

## Related sources

- `capability-routing/README.md`
- `capability-routing/deployment/router-authority.bundle.json`
- `capability-routing/reference-runtime/capability_manifest_recovery.py`
- `capability-routing/worker-runtime-bom.schema.json`
