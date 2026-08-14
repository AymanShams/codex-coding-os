# Capability routing reference source

This directory is the repository single source of truth for routing decisions and a dormant, public-safe reference port of the universal Codex router architecture.

It is not installed by the ordinary Coding OS package installer and is not an active router from this repository checkout. The package installer must not copy this directory into a user's Codex home, register its entry points in `hooks.json`, execute the manifest builder, create a route registry, or replace universal routing state. Deployment to a universal layer requires separate explicit authorization and the dedicated transaction under `deployment/`.

## Contents

- `routing-policy.yaml` owns ordered repository routing decisions.
- `reference-runtime/` preserves the manifest-and-policy consumer architecture for tests and future reviewed deployment.
- `reference-runtime/routing_policy_validation.py` is the single strict schema and semantic validator imported by both the materializer and live router.
- `builder/` is deployment reference source only. It requires explicit inventory inputs and must not be run by package installation.
- The JSON schemas define the active manifest, policy, route decision, authority receipt, and deployment-owned project scope map contracts.
- `project-scope-map.example.json` is synthetic. A real deployment supplies an external map through `CODEX_PROJECT_SCOPE_MAP_PATH`.
- `provenance.json` records the frozen upstream snapshot and repository adaptations.
- `deployment/router-authority.bundle.json` is the exact static source-to-live allowlist.
- `deployment/deploy_router_authority.py` builds a deterministic, content-hashed bundle and performs one explicit target-local deployment transaction.
- `deployment/materialize_routing_policy.py` applies one validated deployment overlay to the repository policy base, then promotes the result through a separate compare-and-swap transaction.
- `routing-policy-overlay.schema.json` defines the only permitted overlay operations.
- `routing-policy.deployment-overlay.example.json` records the reviewed shape of the current universal deployment delta. It is evidence and an input candidate, not an automatically installed overlay.

## Explicit universal deployment

The dedicated deployment path does not weaken the ordinary installer quarantine. It requires an absolute `--codex-home`, an explicit stable transaction identifier, and the expected deterministic bundle digest. It stages beneath the target Codex home, acquires one exclusive deployment lock, records durable preconditions and receipts, verifies every live target again before replacement, promotes each file with an atomic same-filesystem rename, restores every transaction-owned write to its exact baseline after a later failure, and preserves unowned external drift. Replaying the same transaction identifier returns the unchanged terminal receipt only when its source bundle and live outcome still match.

Build and inspect the exact bundle first:

```powershell
python -B .\capability-routing\deployment\deploy_router_authority.py manifest `
  --source-root C:\path\to\codex-coding-os `
  --output C:\path\to\router-authority-bundle.json
```

Read `bundle_sha256` from that file, then run the explicitly authorized transaction:

```powershell
python -B .\capability-routing\deployment\deploy_router_authority.py deploy `
  --source-root C:\path\to\codex-coding-os `
  --codex-home (Join-Path $env:USERPROFILE '.codex') `
  --transaction-id router-authority-YYYYMMDD-N `
  --expected-bundle-sha256 64_LOWERCASE_HEX_CHARACTERS
```

The static source bundle includes router-specific code, the router-owned `_hook_io.py` helper, the shared routing-policy validator, schemas, the worker runtime BOM schema, the Catalogue Router skill, query wrapper, evidence-only historical catalogue reference, and the repository policy at the non-live `capability-routing/policy-base/routing-policy.yaml` path. It never writes the live `capability-routing/routing-policy.yaml`. The router has no runtime or deployment precondition on the ordinary installer's `_common.py` helper. Whole `hooks.json`, `AGENTS.md`, `task-routing-gate.md`, and the Node dependency guard are likewise outside manifest authority because their unrelated writers and documentation-only changes do not change router semantics. Hook carrier registrations remain checked semantically and separately. The live policy is owned only by the policy materializer. The ordinary Coding OS installer also rejects and preserves the router-owned `catalogue-router` skill so install, upgrade, and uninstall cannot become a second writer.

The bundle never deploys `active-capabilities.json`, generation pointers or generations, route registry files, recovery or update receipts, quarantine observations, a project scope map, a deployment overlay, policy materialization transaction state, a local overlay, or `worker-runtime-bom.json`. Those are deployment-owned runtime state and require their own explicit compare-and-swap operations. The transaction also refuses to run while a retired `capability-router` or `capability-index` directory remains under the target Codex home.

After the static transaction, render the candidate live policy from the deployed base and an explicitly reviewed overlay:

```powershell
$RouterMaterializer = Join-Path $env:USERPROFILE '.codex\capability-routing\materialize_routing_policy.py'
$RouterPolicyBase = Join-Path $env:USERPROFILE '.codex\capability-routing\policy-base\routing-policy.yaml'
$RouterPolicySchema = Join-Path $env:USERPROFILE '.codex\capability-routing\routing-policy.schema.json'
$RouterOverlaySchema = Join-Path $env:USERPROFILE '.codex\capability-routing\routing-policy-overlay.schema.json'
$RouterCapabilityManifest = 'C:\path\to\reviewed-capability-generation-candidate.json'
python -B $RouterMaterializer render `
  --base $RouterPolicyBase `
  --overlay C:\path\to\routing-policy.deployment-overlay.json `
  --capability-manifest $RouterCapabilityManifest `
  --policy-schema $RouterPolicySchema `
  --overlay-schema $RouterOverlaySchema `
  --output C:\path\to\routing-policy.candidate.json
```

The capability manifest is a read-only validation input. It is not copied or promoted by the materializer. The render output prints the exact base, overlay, both schemas, capability manifest, materialized policy, and combined materialization hashes. Read the current live policy SHA-256, review the candidate and complete materialization digest, then apply with all preconditions:

```powershell
$RouterMaterializer = Join-Path $env:USERPROFILE '.codex\capability-routing\materialize_routing_policy.py'
$RouterPolicyBase = Join-Path $env:USERPROFILE '.codex\capability-routing\policy-base\routing-policy.yaml'
$RouterPolicySchema = Join-Path $env:USERPROFILE '.codex\capability-routing\routing-policy.schema.json'
$RouterOverlaySchema = Join-Path $env:USERPROFILE '.codex\capability-routing\routing-policy-overlay.schema.json'
$RouterCapabilityManifest = 'C:\path\to\reviewed-capability-generation-candidate.json'
$LiveRouterPolicy = Join-Path $env:USERPROFILE '.codex\capability-routing\routing-policy.yaml'
python -B $RouterMaterializer apply `
  --base $RouterPolicyBase `
  --overlay C:\path\to\routing-policy.deployment-overlay.json `
  --capability-manifest $RouterCapabilityManifest `
  --policy-schema $RouterPolicySchema `
  --overlay-schema $RouterOverlaySchema `
  --target-policy $LiveRouterPolicy `
  --transaction-id routing-policy-YYYYMMDD-N `
  --expected-target-sha256 CURRENT_LIVE_POLICY_SHA256 `
  --expected-materialized-sha256 REVIEWED_CANDIDATE_SHA256 `
  --expected-materialization-digest REVIEWED_MATERIALIZATION_DIGEST
```

Overlay edits are limited to replacing one existing object member, inserting one absent unique list value into one exactly selected object, or inserting one absent object by a unique key. Missing paths, ambiguous selectors, duplicate keys, duplicate edits, stale anchors, and no-op edits fail closed. Before and after overlay application, the materializer calls the same shared validator as the live runtime for the complete policy schema, unique identifiers and aliases, execution-profile references, declared capability identities, live-dependency and fallback references, support limits, approved worker contracts, and contradictory declarations. Policy identity is separate from current availability: a reviewed dormant capability may remain declared or suppressed without forcing policy churn, but only active manifest entries can be selected at runtime. The exact shared-validator bytes are bound into the materialization digest and checked again after target promotion before the terminal receipt. Repository rule updates therefore flow through the latest base while deployment-only additions remain explicit and reviewable.

No multi-file filesystem primitive can replace all targets in one indivisible operation. This transaction uses an atomic rename for each target under one exclusive lock and a durable compensating rollback for the complete set. A completed static deployment does not build or promote an active manifest generation and does not prove hook registration. Those steps remain separate and must bind the deployed hashes before the router becomes authoritative.

A deployment must inventory plugin executable and integration roots separately
from managed prompt-skill roots. A plugin package can expose an MCP or app from
its package root while exposing skills from a versioned `skills` root. Finding
the executable surface does not prove that the prompt-skill inventory is
complete, and finding the skill root does not prove that the executable
surface is callable.

Project root maps may contain valid parent and nested roots. Resolution uses
the longest normalized matching root first so a broad workspace root cannot
mask a more specific project root. Only an exact normalized root assigned to
different project IDs is invalid and must fail closed.

The reference runtime treats manifest and configuration presence as
configured-only evidence. A live dependency is callable only when complete task
input supplies a successful `live_call` probe for that dependency, bound to the
same `execution_request_id`. The normalized routing prompt, task instruction,
and optional bounded task text must also match. Missing, failed, or rebound
probes select the declared
fallback. An explicit static-only or no-live instruction overrides a callable
probe. Requests that span multiple provider or frontend security surfaces,
multiple lifecycle phases, or multiple tracker destinations fail closed with a
split-task reason instead of silently selecting the first ordered rule.

Runtime state is intentionally excluded. This repository never ships `active-capabilities.json`, `route-decisions.sqlite3`, live authority receipts, plugin cache contents, authentication state, user configuration, or private project paths.

Manifest schema 1.3 separates immutable static authority from mutable deployment authority. A static code or schema mismatch denies the complete router. A provable plugin-package replacement quarantines only the capabilities owned by that package. A recognized Codex Desktop configuration-leaf change quarantines only its mapped app surfaces. A changed worker runtime BOM disables external worker selection without disabling Codex-only skill routes. Any malformed, mixed, or unprovable dependency closure remains a whole-router denial.

Scheduled `openai-primary-runtime` updates use a separate recovery lane. The receipt reads the canonical detached `~/.cache/codex-runtimes/codex-primary-runtime/runtime.json` and requires the `documents`, `pdf`, `presentations`, `spreadsheets`, and `template-creator` package authority files to match the live plugin cache byte for byte. Automatic recovery accepts only one strictly newer complete cohort with identical capability IDs and no unrelated authority delta. An exact simultaneous Codex Desktop and primary-runtime transition is decomposed into the independently valid app and runtime cohorts, then rebuilt and promoted once under one mutex and one stable observed receipt. This is byte-coherent dual-tree evidence, not authenticated updater provenance. Any writer that can modify both trees can forge it, so mixed or incomplete changes still require an operator rebaseline.

The worker-runtime BOM has its own receipt-bound automatic lane. It accepts only the isolated BOM hash delta after a completed immutable promoter receipt binds the previous generation hash to the current candidate. The BOM must contain exactly Local Agent Stack and Antigravity plus the separate stability-gateway runtime binding. It binds each complete execution stanza, isolated Python closure, verified-empty bytecode-cache prefix, source and dependency tree, nested Hermes or AGY artifact identity, and the gateway's loaded source, Python, dependency, startup-policy, and semantic config closure. Both workers remain `enabled=false` and `gateway_managed=true`; direct registration, missing workers, hollow identities, mixed deltas, or a missing/corrupt promoter receipt remain worker-scoped fail-closed. Recovery refreshes the exact current Codex MCP and plugin inventories before its first catalogue-dependent receipt capture, takes stable reads under the authority mutex, preserves the complete routing-semantic projection, builds one candidate, and verifies the gateway and both worker identities again after one generation promotion.

Every successful rebuild writes an immutable manifest generation and promotes a single content-bound `current-generation.json` pointer. Existing generation identifiers are never overwritten. Promotion uses compare-and-swap against the previous pointer, and an interrupted or replayed operator transaction resolves through durable terminal receipts. The compatibility copy of `active-capabilities.json` is not routing authority after a 1.3 pointer exists.

The cutover order is normative:

1. Drain durable gateway work, disable and stop the verified scheduled task, and prove the gateway root and all managed worker descendants are absent.
2. Apply reviewed universal policy, static-router, worker-source, worker-config, gateway-source, isolated task-action, and hook-carrier updates while the gateway remains stopped. Render each final worker and gateway runtime identity from those exact bytes.
3. Render, review, and apply the exact worker-runtime BOM containing the gateway and both workers.
4. Build a provisional capability candidate, then render, review, and apply the live routing policy against that candidate.
5. Capture the final authority snapshot, render and review the final manifest candidate, then apply that exact candidate as the terminal authority generation.
6. Start one verified gateway task. Prove its new PID, exact isolated command, process-loaded gateway identity, semantic config digest, and both current worker identities, then execute one newly registered route and durable replay for each worker family.
7. Verify manifest, policy, BOM, hook carriers, route registry, gateway task, and worker admission status before allowing general gateway use.

The final operator manifest promotion is the terminal authority write. Any later static source, live policy, project map, or worker-BOM write requires another reviewed manifest generation before routes or workers can be called.

An operator rebaseline is a two-phase review and apply operation. Snapshot, render, and apply each refresh the current CLI inventories once before the first full authority capture, so an expired catalog cache cannot block capture and no second refresh can rewrite inventory state between stable reads. Rendering creates inert immutable candidate bytes under `capability-routing/operator-rebaseline-reviews/` and does not write a transaction begin, reserve a generation, or change live routing. Applying requires the exact reviewed authority snapshot SHA-256, candidate SHA-256, transaction identifier, candidate path, and an explicit authorization identifier. It validates the candidate receipt, generation predecessor and sequence, transaction binding, freshness, and source hashes against the current authority before it writes the durable begin receipt. The apply phase never invokes the builder.

```powershell
$Recovery = Join-Path $env:USERPROFILE '.codex\hooks\capability_manifest_recovery.py'
$CodexHome = Join-Path $env:USERPROFILE '.codex'
$TransactionId = 'operator-rebaseline-YYYYMMDD-N'
$Snapshot = python -B $Recovery --snapshot --codex-home $CodexHome | ConvertFrom-Json
$Review = python -B $Recovery `
  --render-operator-rebaseline-candidate `
  --transaction-id $TransactionId `
  --expected-authority-snapshot-sha256 $Snapshot.snapshot_sha256 `
  --codex-home $CodexHome | ConvertFrom-Json

# Review the exact file at $Review.reviewed_candidate_path before applying it.
python -B $Recovery `
  --operator-rebaseline `
  --authorization-id 'review-record-identifier' `
  --transaction-id $TransactionId `
  --expected-authority-snapshot-sha256 $Snapshot.snapshot_sha256 `
  --expected-candidate-sha256 $Review.candidate_sha256 `
  --reviewed-candidate $Review.reviewed_candidate_path `
  --codex-home $CodexHome
```

`worker-runtime-bom.json` binds the stability gateway and each gateway-managed worker to exact identity-file bytes, complete Python and dependency trees, release identifiers, startup policies, semantic server configuration, route schema, and registry schema. It is live deployment state, not repository source. Gateway status, worker status, and router admission status are reported separately. The deployed `promote_worker_runtime_bom.py` is its only writer and is itself static manifest authority. Each BOM journal and receipt binds that promoter's exact SHA-256, so promoter-only drift or promoter-plus-BOM drift requires a reviewed static deployment and manifest generation. The promoter derives the gateway binding from the canonical Codex gateway entry and gateway runtime identity, renders worker entries from current configuration and worker identity files, then promotes reviewed canonical bytes through an exact compare-and-swap transaction with a target-local lock, durable journal, immutable receipt, and compensating rollback.

```powershell
$BomTool = Join-Path $env:USERPROFILE '.codex\capability-routing\promote_worker_runtime_bom.py'
$BomSchema = Join-Path $env:USERPROFILE '.codex\capability-routing\worker-runtime-bom.schema.json'
$CodexConfig = Join-Path $env:USERPROFILE '.codex\config.toml'
$BomCandidate = 'C:\path\to\worker-runtime-bom.candidate.json'
$BomTarget = Join-Path $env:USERPROFILE '.codex\capability-routing\worker-runtime-bom.json'

python -B $BomTool render `
  --config $CodexConfig `
  --schema $BomSchema `
  --runtime local-agent-stack=runtime-identity.json `
  --runtime antigravity-adapter=runtime-identity.json `
  --output $BomCandidate

python -B $BomTool apply `
  --candidate $BomCandidate `
  --schema $BomSchema `
  --config $CodexConfig `
  --target $BomTarget `
  --transaction-id worker-runtime-bom-YYYYMMDD-N `
  --expected-target-sha256 CURRENT_LIVE_BOM_SHA256 `
  --expected-candidate-sha256 REVIEWED_CANDIDATE_SHA256
```

The reference runtime uses route-decision schema 3.0 and SQLite registry version 3. Every executable receipt binds the exact active-manifest and routing-policy content hashes. Verification reloads both current authorities and rejects a receipt after either authority changes, even if a human-readable snapshot label was reused. Migration from an older registry version atomically purges its receipts.

Tests must bind all Codex home, manifest, policy, configuration, schema, registry, and project-map paths to temporary directories before importing or invoking the reference runtime.

Repository contract tests install the exact dependency pinned in
`capability-routing/requirements-test.txt`. The dormant runtime is not added to
the installed package and does not add a runtime dependency to Coding OS.
