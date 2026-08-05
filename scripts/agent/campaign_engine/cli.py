#!/usr/bin/env python3
"""Executable interface for the single Coding OS campaign engine."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from runtime_bootstrap import (  # type: ignore[import-not-found]
        BootstrapEvidence,
        RuntimeBootstrapError,
        RuntimeLayout,
        runtime_layout,
        verify_cancel_dependency_closure,
        verify_installed_bundle,
        verify_launcher,
    )
else:
    from .runtime_bootstrap import (
        BootstrapEvidence,
        RuntimeBootstrapError,
        RuntimeLayout,
        runtime_layout,
        verify_cancel_dependency_closure,
        verify_installed_bundle,
        verify_launcher,
    )


EXIT_USAGE = 2
EXIT_DENIED = 77
EXIT_RETIRED = 78
EXIT_FAILED = 1
_RUNTIME_LAYOUT: RuntimeLayout | None = None
_BOOTSTRAP_EVIDENCE: BootstrapEvidence | None = None
_BOOTSTRAP_FAILURE: str | None = None
_ENGINE_LOADED = False


def _load_engine() -> None:
    """Import lifecycle code only after the bootstrap has verified the bundle."""

    global _ENGINE_LOADED
    if _ENGINE_LOADED:
        return
    if __package__ in {None, ""}:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from campaign_engine.admission import admit_campaign_spec, verify_installed_runtime
        from campaign_engine.effects import ExternalEffectDriver, GitHubBackend
        from campaign_engine.host import probe_native_host_capability
        from campaign_engine.legacy import (
            inspect_legacy_case,
            inspect_legacy_root,
            verify_legacy_archive,
        )
        from campaign_engine.model import (
            CampaignSpec,
            CampaignState,
            EffectState,
            Event,
            EventType,
            Evidence,
            EvidenceKind,
        )
        from campaign_engine.store import CampaignStore
        from campaign_engine.supervisor import DeterministicSupervisor, SupervisorDecision
    else:
        from .admission import admit_campaign_spec, verify_installed_runtime
        from .effects import ExternalEffectDriver, GitHubBackend
        from .host import probe_native_host_capability
        from .legacy import inspect_legacy_case, inspect_legacy_root, verify_legacy_archive
        from .model import (
            CampaignSpec,
            CampaignState,
            EffectState,
            Event,
            EventType,
            Evidence,
            EvidenceKind,
        )
        from .store import CampaignStore
        from .supervisor import DeterministicSupervisor, SupervisorDecision
    globals().update(locals())
    _ENGINE_LOADED = True


def _layout() -> RuntimeLayout:
    if _RUNTIME_LAYOUT is None:
        raise RuntimeError("runtime bootstrap has not established canonical paths")
    return _RUNTIME_LAYOUT


def _state_path() -> Path:
    return _layout().state_db


def _installed_root() -> Path:
    return _layout().installed_root


def _json_object(path: str | Path, label: str) -> dict[str, Any]:
    source = Path(path).expanduser().resolve(strict=True)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _emit(value: Any, *, json_output: bool) -> None:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    elif hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    if json_output:
        print(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(item, (dict, list, tuple)):
                print(f"{key}: {json.dumps(item, sort_keys=True, ensure_ascii=False)}")
            else:
                print(f"{key}: {item}")
    else:
        print(value)


def _store(args: argparse.Namespace) -> CampaignStore:
    del args
    return CampaignStore(_state_path())


def _driver(store: CampaignStore) -> ExternalEffectDriver:
    return ExternalEffectDriver(store, GitHubBackend())


def _runtime_pin(root: Path) -> dict[str, str]:
    manifest = _json_object(root / "install-manifest.json", "install manifest")
    pin = manifest.get("runtime_pin")
    if not isinstance(pin, dict):
        raise ValueError("install manifest has no complete runtime_pin")
    required = (
        "source_commit",
        "bundle_digest",
        "install_transaction",
        "protocol_version",
        "schema_compatibility",
        "host_capability_probe_version",
    )
    missing = [key for key in required if not pin.get(key)]
    if missing:
        raise ValueError(f"runtime pin is missing fields: {missing}")
    return {key: str(pin[key]) for key in required}


def _verify_campaign_runtime(spec: CampaignSpec):
    """Bind every lifecycle mutation to the campaign's exact installed runtime."""

    return verify_installed_runtime(
        _installed_root(),
        expected_source_commit=spec.installed_source_commit,
        expected_bundle_digest=spec.installed_bundle_digest,
        expected_install_transaction=spec.install_transaction,
        expected_protocol_version=spec.protocol_version,
        expected_schema_compatibility=spec.schema_compatibility,
        expected_host_capability_probe_version=spec.host_capability_probe_version,
    )


def command_admit(args: argparse.Namespace) -> dict[str, Any]:
    raw = _json_object(args.spec, "campaign specification")
    spec = CampaignSpec.from_dict(raw)
    installed = _installed_root()
    evidence = admit_campaign_spec(spec.to_dict(), installed_root=installed)
    with _store(args) as store:
        snapshot = store.create_campaign(spec)
        store.record_evidence(
            Evidence(
                evidence_id=f"admission:{spec.campaign_id}:{evidence['admission_sha256']}",
                campaign_id=spec.campaign_id,
                node_id=None,
                kind=EvidenceKind.REPOSITORY,
                digest=str(evidence["admission_sha256"]),
                payload=evidence,
                candidate_head=spec.base_sha,
            )
        )
        return {
            "ok": True,
            "campaign_id": spec.campaign_id,
            "state": snapshot.state.value,
            "specification_digest": spec.specification_digest,
            "revision": snapshot.revision,
            "admission": evidence,
        }


def command_approve(args: argparse.Namespace) -> dict[str, Any]:
    with _store(args) as store:
        snapshot = store.get_snapshot(args.campaign_id)
        runtime = _verify_campaign_runtime(snapshot.spec)
        if snapshot.spec.specification_digest != args.specification_digest:
            raise ValueError("approval digest differs from the immutable specification")
        event = Event(
            event_id=args.request_id
            or f"approve:{args.campaign_id}:{args.specification_digest}",
            campaign_id=args.campaign_id,
            event_type=EventType.APPROVE,
            expected_revision=snapshot.revision,
            authority_epoch=snapshot.authority_epoch,
            cancellation_epoch=snapshot.cancellation_epoch,
            payload={"specification_digest": args.specification_digest},
        )
        approved, _ = store.apply_event(event)
        return {
            "ok": True,
            "campaign_id": args.campaign_id,
            "state": approved.state.value,
            "revision": approved.revision,
            "specification_digest": approved.spec.specification_digest,
            "runtime_pin": runtime.to_dict(),
        }


def _handle_event(
    supervisor: DeterministicSupervisor,
    campaign_id: str,
    event: Mapping[str, Any],
) -> SupervisorDecision:
    kind = str(event.get("type", ""))
    node_id = str(event.get("node_id", ""))
    if kind == "review_complete":
        return supervisor.freeze_review(
            campaign_id,
            node_id,
            receipts=event.get("receipts", []),
            findings=event.get("findings", []),
        )
    if kind == "repair_authorized":
        return supervisor.authorize_repair(
            campaign_id,
            node_id,
            authorization_receipt=event.get("authorization_receipt", {}),
        )
    if kind == "closure_complete":
        return supervisor.complete_closure(
            campaign_id,
            node_id,
            receipts=event.get("receipts", []),
            resolved_finding_ids=event.get("resolved_finding_ids", []),
            findings=event.get("findings", []),
        )
    if kind == "publication_authorized":
        return supervisor.authorize_publication(
            campaign_id,
            node_id,
            authorization_receipt=event.get("authorization_receipt", {}),
        )
    if kind == "hosted_checks":
        request_id = str(event.get("request_id", "")).strip()
        if not request_id:
            raise ValueError("hosted_checks event requires a unique request_id")
        return supervisor.start_publication(
            campaign_id,
            node_id,
            hosted_wakeup_id=request_id,
        )
    raise ValueError(f"unknown named external event: {kind}")


def command_run(args: argparse.Namespace) -> dict[str, Any]:
    with _store(args) as store:
        initial = store.get_snapshot(args.campaign_id)
        runtime = _verify_campaign_runtime(initial.spec)
        supervisor = DeterministicSupervisor(
            store,
            effect_driver=_driver(store) if not args.no_external_effects else None,
        )
        decisions: list[dict[str, Any]] = []
        if args.event_file:
            decision = _handle_event(
                supervisor,
                args.campaign_id,
                _json_object(args.event_file, "named external event"),
            )
            decisions.append(decision.to_dict())
        for _ in range(args.max_actions):
            decision = supervisor.step(args.campaign_id)
            decisions.append(decision.to_dict())
            if args.once:
                break
            action = decision.action
            details = dict(decision.details or {})
            if action in {"IMPLEMENTER_DISPATCHED", "REPAIRER_DISPATCHED"}:
                supervisor.complete_worker(
                    str(details["lease_id"]), timeout=args.worker_timeout
                )
                continue
            if action == "REVIEW_DISPATCHED":
                receipts, findings = supervisor.collect_review_cohort(
                    [str(item) for item in details.get("leases", [])],
                    timeout=args.worker_timeout,
                )
                frozen = supervisor.freeze_review(
                    args.campaign_id,
                    str(decision.node_id),
                    receipts=receipts,
                    findings=findings,
                )
                decisions.append(frozen.to_dict())
                if frozen.wait_event:
                    break
                continue
            if action == "CLOSURE_DISPATCHED":
                receipts, findings = supervisor.collect_review_cohort(
                    [str(item) for item in details.get("leases", [])],
                    timeout=args.worker_timeout,
                )
                original = store.get_snapshot(args.campaign_id).node(
                    str(decision.node_id)
                ).findings
                resolved_sets = []
                for receipt in receipts:
                    raw = receipt.get("resolved_finding_ids", [])
                    resolved_sets.append(set(str(item) for item in raw))
                resolved = (
                    sorted(set.intersection(*resolved_sets))
                    if resolved_sets
                    else []
                )
                closure = supervisor.complete_closure(
                    args.campaign_id,
                    str(decision.node_id),
                    receipts=receipts,
                    resolved_finding_ids=resolved,
                    findings=findings,
                )
                decisions.append(closure.to_dict())
                continue
            if action in {"PUBLICATION_PREPARED", "PUBLICATION_CONFIRMED"}:
                continue
            if decision.wait_event or action in {"YIELD", "TERMINAL"}:
                break
        else:
            raise RuntimeError("run reached the approved max-actions bound")
        snapshot = store.get_snapshot(args.campaign_id)
        return {
            "ok": snapshot.state not in {CampaignState.FAILED},
            "campaign_id": args.campaign_id,
            "state": snapshot.state.value,
            "revision": snapshot.revision,
            "decisions": decisions,
            "telemetry": store.telemetry_counts(args.campaign_id),
            "runtime_pin": runtime.to_dict(),
        }


def command_status(args: argparse.Namespace) -> dict[str, Any]:
    with _store(args) as store:
        if args.campaign_id:
            snapshots = [store.get_snapshot(args.campaign_id)]
        else:
            snapshots = store.list_campaigns(repository_root=args.repository_root)
        values = []
        for snapshot in snapshots:
            values.append(
                {
                    "campaign": snapshot.to_dict(),
                    "active_leases": [
                        item.to_dict()
                        for item in store.list_active_leases(snapshot.spec.campaign_id)
                    ],
                    "outbox": store.list_outbox(campaign_id=snapshot.spec.campaign_id),
                    "telemetry": store.telemetry_counts(snapshot.spec.campaign_id),
                }
            )
        return {
            "ok": True,
            "state_db": str(store.path),
            "campaign_count": len(values),
            "campaigns": values,
        }


def command_cancel(args: argparse.Namespace) -> dict[str, Any]:
    with _store(args) as store:
        decision = DeterministicSupervisor(
            store, effect_driver=_driver(store)
        ).cancel(args.campaign_id, reason=args.reason)
        result = {"ok": True, **decision.to_dict()}
        if _BOOTSTRAP_FAILURE is not None:
            result["runtime_verification"] = {
                "verified": False,
                "cancel_exception": True,
                "message": _BOOTSTRAP_FAILURE,
            }
        return result


def command_reconcile(args: argparse.Namespace) -> dict[str, Any]:
    with _store(args) as store:
        operations = (
            [store.get_effect(args.operation_id)]
            if args.operation_id
            else store.list_outbox(state=EffectState.AMBIGUOUS)
        )
        verified_campaigns: set[str] = set()
        for operation in operations:
            campaign_id = str(operation["campaign_id"])
            if campaign_id not in verified_campaigns:
                _verify_campaign_runtime(store.get_snapshot(campaign_id).spec)
                verified_campaigns.add(campaign_id)
        supervisor = DeterministicSupervisor(store, effect_driver=_driver(store))
        results = [
            supervisor.reconcile(str(item["operation_id"])) for item in operations
        ]
        return {"ok": True, "reconciled": results}


def _old_engine_retirement(root: Path) -> dict[str, Any]:
    old = root / "scripts" / "agent" / "case_state.py"
    if not old.is_file():
        raise ValueError("retired legacy command stub is missing")
    completed = subprocess.run(
        (sys.executable, "-B", str(old), "show"),
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    output = completed.stdout + completed.stderr
    if completed.returncode != EXIT_RETIRED or "LEGACY_ENGINE_RETIRED" not in output:
        raise ValueError("legacy engine command is not deterministically retired")
    reducers = []
    for path in (root / "scripts" / "agent").rglob("*.py"):
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "reduce"
            for node in ast.walk(module)
        ):
            reducers.append(path.relative_to(root).as_posix())
    reducers.sort()
    if reducers != ["scripts/agent/campaign_engine/reducer.py"]:
        raise ValueError(f"single reducer proof failed: {reducers}")
    return {
        "legacy_exit_code": completed.returncode,
        "legacy_marker": "LEGACY_ENGINE_RETIRED",
        "lifecycle_reducers": reducers,
        "single_engine": True,
    }


def command_doctor(args: argparse.Namespace) -> dict[str, Any]:
    installed = _installed_root()
    pin = _runtime_pin(installed)
    runtime = verify_installed_runtime(
        installed,
        expected_source_commit=pin["source_commit"],
        expected_bundle_digest=pin["bundle_digest"],
        expected_install_transaction=pin["install_transaction"],
        expected_protocol_version=pin["protocol_version"],
        expected_schema_compatibility=pin["schema_compatibility"],
        expected_host_capability_probe_version=pin["host_capability_probe_version"],
    )
    with _store(args) as store:
        integrity = store.integrity_check()
        recovery = (
            DeterministicSupervisor(store, effect_driver=_driver(store)).recover()
            if args.recover
            else None
        )
        installations = store.list_runtime_installations()
    host_probe: Mapping[str, Any] | None = None
    if args.live_host_probe:
        host_probe = probe_native_host_capability(cwd=args.probe_root or installed)
        if host_probe.get("probe_version") != pin["host_capability_probe_version"]:
            raise ValueError("live host capability probe version differs from runtime pin")
    retirement = _old_engine_retirement(installed)
    return {
        "ok": True,
        "state_db": str(_state_path()),
        "integrity": integrity,
        "restart_recovery": recovery,
        "runtime_pin": runtime.to_dict(),
        "recorded_installations": installations,
        "host_capability": host_probe
        or {
            "probe_version": pin["host_capability_probe_version"],
            "live": False,
        },
        "retirement": retirement,
    }


def command_legacy_inspect(args: argparse.Namespace) -> dict[str, Any]:
    if args.archive:
        return verify_legacy_archive(args.source)
    if args.case_id:
        return inspect_legacy_case(args.source, args.case_id)
    return inspect_legacy_root(args.source)


def command_authorize_action(args: argparse.Namespace) -> dict[str, Any]:
    required = {
        "actor_id": args.actor_id,
        "lease_id": args.lease_id,
        "authority_epoch": args.authority_epoch,
        "cancellation_epoch": args.cancellation_epoch,
        "fencing_epoch": args.fencing_epoch,
    }
    if any(value is None or value == "" for value in required.values()):
        raise PermissionError("automated action lacks exact bound actor and lease epochs")
    with _store(args) as store:
        snapshot = store.get_snapshot(args.campaign_id)
        _verify_campaign_runtime(snapshot.spec)
        authorization = store.verify_actor_action(
            args.campaign_id,
            actor_id=args.actor_id,
            lease_id=args.lease_id,
            authority_epoch=args.authority_epoch,
            cancellation_epoch=args.cancellation_epoch,
            fencing_epoch=args.fencing_epoch,
            repository_root=str(Path(args.repository_root).resolve(strict=True)),
            action=args.action,
            path=args.path,
        )
        return {"ok": True, **authorization}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coding-os")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    admit = sub.add_parser("admit")
    admit.add_argument("--spec", required=True)
    admit.set_defaults(handler=command_admit)

    approve = sub.add_parser("approve")
    approve.add_argument("--campaign-id", required=True)
    approve.add_argument("--specification-digest", required=True)
    approve.add_argument("--request-id")
    approve.set_defaults(handler=command_approve)

    run = sub.add_parser("run")
    run.add_argument("--campaign-id", required=True)
    run.add_argument("--event-file")
    run.add_argument("--max-actions", type=int, default=100)
    run.add_argument("--worker-timeout", type=float, default=3600)
    run.add_argument("--once", action="store_true")
    run.add_argument("--no-external-effects", action="store_true")
    run.set_defaults(handler=command_run)

    status = sub.add_parser("status")
    status.add_argument("--campaign-id")
    status.add_argument("--repository-root")
    status.set_defaults(handler=command_status)

    cancel = sub.add_parser("cancel")
    cancel.add_argument("--campaign-id", required=True)
    cancel.add_argument("--reason", default="STOP")
    cancel.set_defaults(handler=command_cancel)

    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--operation-id")
    reconcile.set_defaults(handler=command_reconcile)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--recover", action="store_true")
    doctor.add_argument("--live-host-probe", action="store_true")
    doctor.add_argument("--probe-root")
    doctor.set_defaults(handler=command_doctor)

    legacy = sub.add_parser("legacy")
    legacy_sub = legacy.add_subparsers(dest="legacy_command", required=True)
    inspect = legacy_sub.add_parser("inspect")
    inspect.add_argument("--source", required=True)
    inspect.add_argument("--case-id")
    inspect.add_argument("--archive", action="store_true")
    inspect.set_defaults(handler=command_legacy_inspect)

    authorize = sub.add_parser("authorize-action", help=argparse.SUPPRESS)
    authorize.add_argument("--campaign-id", required=True)
    authorize.add_argument("--actor-id")
    authorize.add_argument("--lease-id")
    authorize.add_argument("--authority-epoch", type=int)
    authorize.add_argument("--cancellation-epoch", type=int)
    authorize.add_argument("--fencing-epoch", type=int)
    authorize.add_argument("--repository-root", required=True)
    authorize.add_argument("--action", required=True)
    authorize.add_argument("--path")
    authorize.set_defaults(handler=command_authorize_action)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    injected_runtime: RuntimeLayout | None = None,
) -> int:
    """Run against canonical production paths or an in-process test layout."""

    global _BOOTSTRAP_EVIDENCE, _BOOTSTRAP_FAILURE, _RUNTIME_LAYOUT
    arguments = list(argv if argv is not None else sys.argv[1:])
    json_anywhere = "--json" in arguments
    if json_anywhere:
        arguments = [item for item in arguments if item != "--json"]
        arguments.insert(0, "--json")
    command = next((item for item in arguments if item != "--json"), "")
    _RUNTIME_LAYOUT = None
    try:
        _RUNTIME_LAYOUT = injected_runtime or runtime_layout()
        if injected_runtime is None:
            verify_launcher(_RUNTIME_LAYOUT, Path(__file__))
        _BOOTSTRAP_EVIDENCE = verify_installed_bundle(_RUNTIME_LAYOUT)
        _BOOTSTRAP_FAILURE = None
    except RuntimeBootstrapError as exc:
        _BOOTSTRAP_EVIDENCE = None
        _BOOTSTRAP_FAILURE = str(exc)
        if command == "cancel" and _RUNTIME_LAYOUT is not None:
            try:
                _BOOTSTRAP_EVIDENCE = verify_cancel_dependency_closure(_RUNTIME_LAYOUT)
            except RuntimeBootstrapError as cancel_exc:
                _BOOTSTRAP_FAILURE = str(cancel_exc)
        if command != "cancel" or _RUNTIME_LAYOUT is None or _BOOTSTRAP_EVIDENCE is None:
            _emit(
                {
                    "ok": False,
                    "code": "RUNTIME_BOOTSTRAP_FAILED",
                    "message": _BOOTSTRAP_FAILURE,
                },
                json_output=json_anywhere,
            )
            return EXIT_FAILED
    _load_engine()
    parser = build_parser()
    args = parser.parse_args(arguments)
    try:
        result = args.handler(args)
        _emit(result, json_output=bool(args.json))
        return 0
    except PermissionError as exc:
        _emit(
            {"ok": False, "code": "ACTION_DENIED", "message": str(exc)},
            json_output=bool(args.json),
        )
        return EXIT_DENIED
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        _emit(
            {
                "ok": False,
                "code": type(exc).__name__,
                "message": str(exc),
            },
            json_output=bool(args.json),
        )
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
