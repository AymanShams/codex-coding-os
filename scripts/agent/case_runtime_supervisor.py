#!/usr/bin/env python3
"""Trusted lifecycle supervisor for one proposal-only App Server action.

The supervisor is the only component that may coordinate canonical case
mutations.  App Server/model threads only produce a proposal and native
identity evidence.  This process then binds those identities, proves restart
continuity, obtains fresh operating-system isolation evidence, rejects every
non-implementer identity at the real grant boundary, issues one exact grant,
and invokes the fixed broker in a separate process.
"""

from __future__ import annotations

import argparse
import base64
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from typing import Any, Callable, Mapping, Protocol


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

import case_runtime_broker as broker  # noqa: E402
from case_app_server_controller import (  # noqa: E402
    CONTROLLER_RUN_PROTOCOL_VERSION,
    AppServerTransport,
    ProposalController,
    _normalize_run_spec,
    _prepare_worker_environment,
    _transport_for,
    app_server_binary_evidence,
    build_app_server_environment,
    finalize_controller_bundle,
    verify_restart_continuity,
)
from case_state import (  # noqa: E402
    ACTION_GRANT_RESULT_PROTOCOL_VERSION,
    LIVE_CONTROLLER_EVIDENCE_PROTOCOL_VERSION,
    RUNTIME_GENERATION_ATTEMPT_PROTOCOL_VERSION,
    AuthorizationError,
    CaseStateError,
    CaseStore,
    RevisionConflict,
    canonical_case_id,
    canonical_json_sha256,
    controller_source_pins,
    file_sha256,
    normalize_binding,
    normalize_live_controller_evidence,
    require_snapshot_hash,
    require_stable_id,
    require_windows_sid,
    utc_now,
)


SUPERVISOR_RUN_PROTOCOL_VERSION = "ccos-runtime-supervisor-v1"
SCHEMA_EVIDENCE_PROTOCOL_VERSION = "ccos-app-server-schema-compatibility-v1"
SUPERVISOR_RESULT_PROTOCOL_VERSION = "ccos-runtime-supervisor-result-v1"
MAX_GRANT_LIFETIME_SECONDS = 15 * 60
DEFAULT_GRANT_LIFETIME_SECONDS = 10 * 60
REQUIRED_SCHEMA_FILES = frozenset(
    {
        "ClientRequest.json",
        "ServerRequest.json",
        "PermissionsRequestApprovalResponse.json",
        "codex_app_server_protocol.v2.schemas.json",
        "v2/ThreadStartParams.json",
        "v2/ThreadStartResponse.json",
        "v2/TurnStartParams.json",
        "v2/ThreadReadParams.json",
        "v2/ThreadListParams.json",
    }
)
REQUIRED_SCHEMA_TOKENS = (
    '"thread/start"',
    '"turn/start"',
    '"thread/read"',
    '"thread/list"',
    '"collabAgentToolCall"',
    '"senderThreadId"',
    '"receiverThreadIds"',
    '"thread_spawn"',
    '"parent_thread_id"',
    '"agent_path"',
    '"runtimeWorkspaceRoots"',
    '"instructionSources"',
    '"approvalsReviewer"',
)
CONTROLLER_SCHEMA_PROPERTIES = {
    "v2/ThreadStartParams.json": {
        "cwd", "sandbox", "approvalPolicy", "approvalsReviewer", "dynamicTools",
        "ephemeral", "model", "config", "runtimeWorkspaceRoots",
        "selectedCapabilityRoots", "environments", "developerInstructions",
    },
    "v2/TurnStartParams.json": {
        "threadId", "input", "sandboxPolicy", "approvalPolicy", "approvalsReviewer",
        "environments", "runtimeWorkspaceRoots", "model", "effort", "collaborationMode",
    },
    "v2/ThreadReadParams.json": {"threadId", "includeTurns"},
    "v2/ThreadListParams.json": {
        "parentThreadId", "sourceKinds", "limit", "sortDirection",
    },
}


class SupervisorError(RuntimeError):
    pass


class SupervisorValidationError(SupervisorError):
    pass


class SupervisorAuthorizationError(SupervisorError):
    pass


class StoreProtocol(Protocol):
    def get_case(self, case_id: str) -> dict[str, Any]: ...
    def bind(self, case_id: str, **kwargs: Any) -> dict[str, Any]: ...
    def bind_runtime_actor(self, case_id: str, **kwargs: Any) -> dict[str, Any]: ...
    def claim_runtime_generation_attempt(self, case_id: str, **kwargs: Any) -> dict[str, Any]: ...
    def abort_runtime_generation_attempt(self, case_id: str, **kwargs: Any) -> dict[str, Any]: ...
    def issue_action_grant(self, case_id: str, **kwargs: Any) -> dict[str, Any]: ...
    def fail_action_grant(self, case_id: str, **kwargs: Any) -> dict[str, Any]: ...
    def check_action(self, case_id: str, action: str, **kwargs: Any) -> dict[str, Any]: ...
    def abort_runtime_generation(self, case_id: str, **kwargs: Any) -> dict[str, Any]: ...


ControllerRunner = Callable[[Mapping[str, Any], bytearray], dict[str, Any]]
RestartRunner = Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]
IsolationCollector = Callable[[StoreProtocol, Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]
BrokerRunner = Callable[[Path, str, str, Mapping[str, Any], bytearray], dict[str, Any]]
SchemaChecker = Callable[[Mapping[str, Any]], dict[str, Any]]
FinalVerifier = Callable[[Mapping[str, Any]], dict[str, Any]]
CompletedRecoveryVerifier = Callable[
    [Path, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], dict[str, Any]
]
AclRestoreRunner = Callable[
    [Path, str, str, Any, str, Mapping[str, Any], str], dict[str, Any]
]
PendingAclRecovery = Callable[
    [Path, str, Mapping[str, Any]], list[dict[str, Any]]
]
OrphanGrantRecovery = Callable[[Path, str, str], dict[str, Any]]


@dataclass(frozen=True)
class SupervisorDependencies:
    controller_runner: ControllerRunner
    restart_runner: RestartRunner
    isolation_collector: IsolationCollector
    broker_runner: BrokerRunner
    schema_checker: SchemaChecker
    final_verifier: FinalVerifier
    completed_recovery_verifier: CompletedRecoveryVerifier
    acl_restore_runner: AclRestoreRunner
    pending_acl_recovery: PendingAclRecovery = (
        lambda _state_root, _case_id, _spec: []
    )
    orphan_grant_recovery: OrphanGrantRecovery = (
        lambda _state_root, _case_id, _grant_id: {
            "status": "test_dependency_not_configured"
        }
    )
    identity_resolver: Callable[[], tuple[str, str]] = broker.windows_identity
    clock: Callable[[], str] = utc_now
    nonce_factory: Callable[[], str] = lambda: secrets.token_hex(16)


def _nonempty(value: Any, label: str, maximum: int = 32768) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise SupervisorValidationError(f"{label} must be a non-empty bounded string")
    return value.strip()


def normalize_supervisor_spec(raw: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "protocol_version", "schema_version", "state_root", "controller_spec",
        "worker_offline_principal_sid", "sandbox_users_group_sid",
        "expected_schema_file_count", "expected_schema_tree_sha256",
    }
    if not isinstance(raw, Mapping) or set(raw) != fields:
        raise SupervisorValidationError("supervisor spec must use the fixed v1 schema")
    if (
        raw.get("protocol_version") != SUPERVISOR_RUN_PROTOCOL_VERSION
        or raw.get("schema_version") != 1
    ):
        raise SupervisorValidationError("supervisor protocol or schema version is unsupported")
    state_root = Path(_nonempty(raw.get("state_root"), "state root")).resolve(strict=True)
    if not state_root.is_dir():
        raise SupervisorValidationError("state root must be an existing directory")
    controller_spec = _normalize_run_spec(
        raw.get("controller_spec"),
        proposal_may_exist=True,
        target_may_differ_from_baseline=True,
    )
    worker_root = Path(controller_spec["worker_codex_home"]).resolve(strict=True)
    protected_roots = (
        Path(controller_spec["worktree"]).resolve(strict=True),
        state_root,
        Path(controller_spec["proposal_artifact_path"]).parent.resolve(strict=True),
        Path(__file__).resolve().parents[2],
    )
    for protected in protected_roots:
        protected_parent = protected.parent
        if (
            worker_root in {protected, protected_parent}
            or worker_root.is_relative_to(protected)
            or protected.is_relative_to(worker_root)
            or worker_root.is_relative_to(protected_parent)
        ):
            raise SupervisorAuthorizationError(
                "worker runtime root overlaps a protected root or its immediate parent"
            )
    online_sid = controller_spec["worker_principal_sid"]
    offline_sid = require_windows_sid(
        raw.get("worker_offline_principal_sid"), "offline worker principal SID"
    )
    group_sid = require_windows_sid(raw.get("sandbox_users_group_sid"), "sandbox group SID")
    broker_sid = controller_spec["broker_principal_sid"]
    if len({online_sid, offline_sid, group_sid, broker_sid}) != 4:
        raise SupervisorAuthorizationError("broker, Online, Offline, and sandbox-group SIDs must differ")
    if (
        offline_sid != controller_spec["worker_offline_principal_sid"]
        or group_sid != controller_spec["sandbox_group_principal_sid"]
    ):
        raise SupervisorAuthorizationError(
            "supervisor principal pins differ from the signed controller context"
        )
    expected_schema_count = raw.get("expected_schema_file_count")
    if (
        not isinstance(expected_schema_count, int)
        or isinstance(expected_schema_count, bool)
        or expected_schema_count <= 0
    ):
        raise SupervisorValidationError("expected schema file count must be a positive integer")
    expected_schema_sha256 = require_snapshot_hash(
        str(raw.get("expected_schema_tree_sha256", ""))
    )
    if (
        expected_schema_count != controller_spec["expected_schema_file_count"]
        or expected_schema_sha256 != controller_spec["expected_schema_tree_sha256"]
    ):
        raise SupervisorAuthorizationError(
            "supervisor schema pins differ from the signed controller context"
        )
    return {
        "protocol_version": SUPERVISOR_RUN_PROTOCOL_VERSION,
        "schema_version": 1,
        "state_root": normalize_binding("worktree", str(state_root)),
        "controller_spec": controller_spec,
        "worker_offline_principal_sid": offline_sid,
        "sandbox_users_group_sid": group_sid,
        "expected_schema_file_count": expected_schema_count,
        "expected_schema_tree_sha256": expected_schema_sha256,
    }


def _schema_tree_digest(root: Path) -> tuple[str, int, int]:
    records: list[str] = []
    for path in sorted(root.rglob("*.json"), key=lambda item: item.relative_to(root).as_posix()):
        try:
            value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SupervisorAuthorizationError(
                f"generated schema is not canonical JSON: {path.name}"
            ) from exc
        canonical = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        records.append(
            f"{path.relative_to(root).as_posix()}:{len(canonical)}:"
            f"{hashlib.sha256(canonical).hexdigest()}"
        )
    raw = "\n".join(records).encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), len(records), len(raw)


def verify_generated_schema_directory(
    root: Path,
    *,
    expected_file_count: int,
    expected_tree_sha256: str,
) -> dict[str, Any]:
    """Fail closed if the selected App Server boundary schema has drifted."""
    missing = sorted(
        relative for relative in REQUIRED_SCHEMA_FILES
        if not (root / relative).is_file()
    )
    if missing:
        raise SupervisorAuthorizationError(
            "generated App Server schema lacks required files: " + ",".join(missing)
        )
    aggregate = (root / "codex_app_server_protocol.v2.schemas.json").read_text(
        encoding="utf-8", errors="strict"
    )
    absent = [token for token in REQUIRED_SCHEMA_TOKENS if token not in aggregate]
    if absent:
        raise SupervisorAuthorizationError(
            "generated App Server schema lacks required boundary tokens: " + ",".join(absent)
        )
    permissions = json.loads(
        (root / "PermissionsRequestApprovalResponse.json").read_text(encoding="utf-8")
    )
    properties = permissions.get("properties") if isinstance(permissions, Mapping) else None
    if (
        permissions.get("type") != "object"
        or set(permissions.get("required") or []) != {"permissions"}
        or not isinstance(properties, Mapping)
        or not {"permissions", "scope", "strictAutoReview"}.issubset(properties)
    ):
        raise SupervisorAuthorizationError("permissions response schema cannot express turn-only denial")
    structural_evidence: dict[str, Any] = {}
    for relative, used_properties in CONTROLLER_SCHEMA_PROPERTIES.items():
        schema = json.loads((root / relative).read_text(encoding="utf-8"))
        properties = schema.get("properties") if isinstance(schema, Mapping) else None
        if not isinstance(properties, Mapping) or not used_properties.issubset(properties):
            raise SupervisorAuthorizationError(
                f"generated schema cannot express fixed controller request: {relative}"
            )
        structural_evidence[relative] = {
            "used_properties": sorted(used_properties),
            "required": sorted(schema.get("required") or []),
        }
    turn_required = structural_evidence["v2/TurnStartParams.json"]["required"]
    read_required = structural_evidence["v2/ThreadReadParams.json"]["required"]
    response = json.loads((root / "v2/ThreadStartResponse.json").read_text(encoding="utf-8"))
    response_required = set(response.get("required") or []) if isinstance(response, Mapping) else set()
    response_properties = response.get("properties") if isinstance(response, Mapping) else None
    if (
        turn_required != ["input", "threadId"]
        or read_required != ["threadId"]
        or response_required
        != {"approvalPolicy", "approvalsReviewer", "cwd", "model", "modelProvider", "sandbox", "thread"}
        or not isinstance(response_properties, Mapping)
        or not {
            "activePermissionProfile", "instructionSources", "reasoningEffort",
            "runtimeWorkspaceRoots",
        }.issubset(response_properties)
    ):
        raise SupervisorAuthorizationError("App Server start/read response structure drifted")
    structural_evidence["v2/ThreadStartResponse.json"] = {
        "required": sorted(response_required),
        "used_properties": sorted(
            {
                "activePermissionProfile", "instructionSources", "reasoningEffort",
                "runtimeWorkspaceRoots",
            }
        ),
    }
    tree_digest, count, record_bytes = _schema_tree_digest(root)
    if count != expected_file_count or tree_digest != expected_tree_sha256:
        raise SupervisorAuthorizationError(
            "generated recursive schema file set differs from the sealed binary schema snapshot"
        )
    evidence = {
        "protocol_version": SCHEMA_EVIDENCE_PROTOCOL_VERSION,
        "schema_version": 1,
        "file_count": count,
        "tree_sha256": tree_digest,
        "record_bytes": record_bytes,
        "required_files": sorted(REQUIRED_SCHEMA_FILES),
        "required_tokens_sha256": canonical_json_sha256(list(REQUIRED_SCHEMA_TOKENS)),
        "permissions_turn_only_shape": True,
        "controller_structure_sha256": canonical_json_sha256(structural_evidence),
    }
    evidence["evidence_sha256"] = canonical_json_sha256(evidence)
    return evidence


def generate_and_verify_app_server_schema(spec: Mapping[str, Any]) -> dict[str, Any]:
    controller_spec = spec["controller_spec"]
    executable = Path(controller_spec["app_server_executable"])
    _prepare_worker_environment(Path(controller_spec["worker_codex_home"]))
    environment = build_app_server_environment(
        Path(controller_spec["worker_codex_home"]), executable
    )
    app_server_binary_evidence(
        executable,
        expected_sha256=controller_spec["expected_app_server_sha256"],
        expected_version=controller_spec["expected_app_server_version"],
        environment=environment,
    )
    with tempfile.TemporaryDirectory(prefix="ccos-app-server-schema-") as temporary:
        root = Path(temporary) / "schema"
        result = subprocess.run(
            [
                str(executable), "app-server", "generate-json-schema",
                "--experimental", "--out", str(root),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=environment,
            timeout=120,
        )
        if result.returncode != 0:
            raise SupervisorAuthorizationError("App Server schema generation failed")
        evidence = verify_generated_schema_directory(
            root,
            expected_file_count=spec["expected_schema_file_count"],
            expected_tree_sha256=spec["expected_schema_tree_sha256"],
        )
        evidence["app_server_sha256"] = controller_spec["expected_app_server_sha256"]
        evidence["app_server_version"] = controller_spec["expected_app_server_version"]
        evidence["evidence_sha256"] = canonical_json_sha256(
            {name: value for name, value in evidence.items() if name != "evidence_sha256"}
        )
        return evidence


def _production_controller_runner(
    spec: Mapping[str, Any], key: bytearray,
) -> dict[str, Any]:
    transport = _transport_for(spec)
    with transport:
        bundle = ProposalController(spec).run(transport, key)
    bundle["transport_audit"] = copy.deepcopy(transport.audit)
    return bundle


def _production_restart_runner(
    spec: Mapping[str, Any], checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    transport = _transport_for(spec)
    with transport:
        evidence = verify_restart_continuity(transport, spec, checkpoint)
    evidence["transport_audit"] = copy.deepcopy(transport.audit)
    evidence["evidence_sha256"] = canonical_json_sha256(
        {name: value for name, value in evidence.items() if name != "evidence_sha256"}
    )
    return evidence


def _production_isolation_collector(
    store: StoreProtocol, spec: Mapping[str, Any], bundle: Mapping[str, Any],
) -> dict[str, Any]:
    collector = getattr(broker, "collect_dual_profile_isolation_evidence", None)
    if not callable(collector):
        raise SupervisorAuthorizationError(
            "broker does not expose the fixed dual-profile isolation collector"
        )
    return collector(
        store=store,
        controller_spec=spec["controller_spec"],
        grant_core=bundle["grant_core"],
        offline_principal_sid=spec["worker_offline_principal_sid"],
        sandbox_users_group_sid=spec["sandbox_users_group_sid"],
    )


def _broker_environment(state_root: Path, key: bytearray) -> tuple[dict[str, str], str]:
    system_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")).resolve(strict=False)
    git_executable = Path(broker.resolved_executable("git.exe", "git")).resolve(strict=True)
    powershell = Path(broker.resolved_executable("powershell.exe", "pwsh.exe", "pwsh")).resolve(
        strict=True
    )
    temp_root = state_root / "supervisor-broker-temp"
    temp_root.mkdir(mode=0o700, exist_ok=True)
    encoded_key = base64.b64encode(memoryview(key)).decode("ascii")
    path_entries = [Path(sys.executable).resolve(strict=True).parent, git_executable.parent, powershell.parent]
    environment = {
        "COMSPEC": str(system_root / "System32" / "cmd.exe"),
        "PATH": os.pathsep.join(dict.fromkeys(str(item) for item in path_entries)),
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "SYSTEMDRIVE": system_root.drive or "C:",
        "SYSTEMROOT": str(system_root),
        "TEMP": str(temp_root.resolve(strict=True)),
        "TMP": str(temp_root.resolve(strict=True)),
        "WINDIR": str(system_root),
        broker.CONTROLLER_KEY_ENVIRONMENT: encoded_key,
    }
    return environment, encoded_key


def run_broker_process(
    state_root: Path,
    case_id: str,
    grant_id: str,
    controller_receipt: Mapping[str, Any],
    key: bytearray,
) -> dict[str, Any]:
    """Invoke only the fixed broker execute command with sealed identifiers."""
    environment, encoded_key = _broker_environment(state_root, key)
    command = [
        str(Path(sys.executable).resolve(strict=True)),
        str((SCRIPT_DIRECTORY / "case_runtime_broker.py").resolve(strict=True)),
        "--json", "execute",
        "--state-root", str(state_root.resolve(strict=True)),
        "--case-id", canonical_case_id(case_id),
        "--grant-id", require_stable_id(grant_id, "grant id"),
        "--controller-receipt-json",
        json.dumps(dict(controller_receipt), separators=(",", ":"), sort_keys=True),
    ]
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=environment,
            timeout=180,
        )
    finally:
        environment.pop(broker.CONTROLLER_KEY_ENVIRONMENT, None)
        encoded_key = "0" * len(encoded_key)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SupervisorAuthorizationError("fixed broker returned malformed JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        raise SupervisorAuthorizationError("fixed broker response lacks an exact outcome")
    return payload


def _production_final_verifier(grant: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(grant["worktree"]).resolve(strict=True)
    head = broker._git_head(root)
    status_paths = broker._git_status_paths(root)
    target = root.joinpath(*str(grant["target_path"]).split("/"))
    target_sha256 = file_sha256(target)
    if (
        head != grant["base_head"]
        or status_paths != [grant["target_path"]]
        or target_sha256 != grant["replacement_sha256"]
    ):
        raise SupervisorAuthorizationError(
            "final repository evidence differs from one exact uncommitted replacement"
        )
    evidence = {
        "head": head,
        "status_paths": status_paths,
        "target_sha256": target_sha256,
        "exactly_one_changed_path": True,
    }
    evidence["evidence_sha256"] = canonical_json_sha256(evidence)
    return evidence


def _production_acl_restore_runner(
    state_root: Path,
    case_id: str,
    grant_id: str,
    protected_acl_snapshot: Any,
    protected_acl_snapshot_sha256: str,
    preissue_dacl_evidence: Mapping[str, Any],
    restore_reason: str,
) -> dict[str, Any]:
    return broker.restore_preissue_acl_lockdown(
        state_root=state_root,
        case_id=case_id,
        grant_id=grant_id,
        protected_acl_snapshot=protected_acl_snapshot,
        protected_acl_snapshot_sha256=protected_acl_snapshot_sha256,
        preissue_dacl_evidence=preissue_dacl_evidence,
        restore_reason=restore_reason,
    )


def _production_pending_acl_recovery(
    state_root: Path, case_id: str, spec: Mapping[str, Any]
) -> list[dict[str, Any]]:
    controller_spec = spec["controller_spec"]
    roots = {
        "target_root": normalize_binding(
            "worktree", str(controller_spec["worktree"])
        ),
        "state_root": normalize_binding("worktree", str(state_root)),
        "broker_source_root": normalize_binding(
            "worktree", str(Path(broker.__file__).resolve().parents[2])
        ),
        "proposal_root": normalize_binding(
            "worktree",
            str(Path(controller_spec["proposal_artifact_path"]).resolve().parent),
        ),
    }
    return broker.recover_pending_preissue_acl_lockdowns(
        state_root=state_root,
        case_id=case_id,
        grant_id=str(controller_spec["grant_id"]),
        expected_roots=roots,
        expected_denied_principal_sids=[
            controller_spec["worker_principal_sid"],
            controller_spec["worker_offline_principal_sid"],
            controller_spec["sandbox_group_principal_sid"],
        ],
        expected_broker_principal_sid=controller_spec["broker_principal_sid"],
    )


def _production_orphan_grant_recovery(
    state_root: Path, case_id: str, grant_id: str
) -> dict[str, Any]:
    return broker.recover_orphaned_action_grant(
        state_root=state_root, case_id=case_id, grant_id=grant_id
    )


def _production_completed_recovery_verifier(
    state_root: Path,
    case: Mapping[str, Any],
    grant: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    controller_spec = spec["controller_spec"]
    exact = {
        "grant_id": controller_spec["grant_id"],
        "operation_id": controller_spec["operation_id"],
        "repository": controller_spec["repository"],
        "branch": controller_spec["branch"],
        "worktree": controller_spec["worktree"],
        "base_head": controller_spec["base_head"],
        "target_path": controller_spec["target_path"],
        "baseline_sha256": controller_spec["baseline_sha256"],
        "proposal_artifact_path": controller_spec["proposal_artifact_path"],
        "worker_principal_sid": controller_spec["worker_principal_sid"],
        "model_worker_principal_sid": controller_spec["worker_offline_principal_sid"],
        "sandbox_group_principal_sid": controller_spec["sandbox_group_principal_sid"],
        "broker_principal_sid": controller_spec["broker_principal_sid"],
        "app_server_executable_path": controller_spec["app_server_executable"],
        "worker_runtime_root": controller_spec["worker_codex_home"],
        "app_server_sha256": controller_spec["expected_app_server_sha256"],
        "app_server_version": controller_spec["expected_app_server_version"],
        "schema_file_count": controller_spec["expected_schema_file_count"],
        "schema_tree_sha256": controller_spec["expected_schema_tree_sha256"],
        "sandbox_profile_sha256": controller_spec["sandbox_profile_sha256"],
        "app_server_environment_sha256": controller_spec["app_server_environment_sha256"],
    }
    if any(grant.get(name) != value for name, value in exact.items()):
        raise SupervisorAuthorizationError("completed grant differs from the exact supervisor spec")
    app_server_path = Path(grant["app_server_executable_path"]).resolve(strict=True)
    if file_sha256(app_server_path) != grant["app_server_sha256"]:
        raise SupervisorAuthorizationError("completed grant App Server binary bytes changed")
    source_root = Path(str(grant.get("broker_source_root", ""))).resolve(strict=True)
    current_source_pins = controller_source_pins(source_root)
    if (
        current_source_pins != grant.get("controller_source_pins")
        or canonical_json_sha256(current_source_pins)
        != grant.get("controller_source_pins_sha256")
    ):
        raise SupervisorAuthorizationError("completed grant controller source pins changed")
    for value_name, digest_name in (
        ("group_membership_evidence", "group_membership_evidence_sha256"),
        ("isolation_evidence", "isolation_evidence_sha256"),
        ("protected_acl_snapshot", "protected_acl_snapshot_sha256"),
        ("preissue_dacl_evidence", "preissue_dacl_evidence_sha256"),
    ):
        value = grant.get(value_name)
        expected_type = list if value_name == "protected_acl_snapshot" else Mapping
        if not isinstance(value, expected_type) or canonical_json_sha256(value) != grant.get(digest_name):
            raise SupervisorAuthorizationError(
                f"completed grant {value_name} digest is invalid"
            )
    completed_cleanup = broker.recover_completed_action_grant_cleanup(
        state_root=state_root,
        case_id=str(case["case_id"]),
        grant_id=str(grant["grant_id"]),
    )
    acl_restore = completed_cleanup.get("acl_restore")
    if not isinstance(acl_restore, Mapping) or acl_restore.get("restored") is not True:
        raise SupervisorAuthorizationError("completed recovery did not restore protected ACLs")
    result = grant.get("result")
    if (
        not isinstance(result, Mapping)
        or not isinstance(result.get("result_sha256"), str)
        or require_snapshot_hash(
            str(result.get("post_replacement_evidence_sha256", ""))
        ) != result.get("post_replacement_evidence_sha256")
    ):
        raise SupervisorAuthorizationError("completed grant lacks a canonical result record")
    result_body = {name: value for name, value in result.items() if name != "result_sha256"}
    if canonical_json_sha256(result_body) != result["result_sha256"]:
        raise SupervisorAuthorizationError("completed grant result digest is invalid")
    grant_body = {name: value for name, value in grant.items() if name != "grant_sha256"}
    if grant.get("grant_sha256") != canonical_json_sha256(grant_body):
        raise SupervisorAuthorizationError("completed canonical grant digest is invalid")
    claim = grant.get("claim")
    if (
        not isinstance(claim, Mapping)
        or not isinstance(claim.get("dacl_evidence_sha256"), str)
        or not isinstance(claim.get("trusted_write_probe_sha256"), str)
        or not isinstance(grant.get("isolation_evidence"), Mapping)
        or not isinstance(grant.get("group_membership_evidence"), Mapping)
    ):
        raise SupervisorAuthorizationError("completed grant lacks source/DACL/isolation evidence")
    for value_name, digest_name in (
        ("dacl_evidence", "dacl_evidence_sha256"),
        ("trusted_write_probe", "trusted_write_probe_sha256"),
    ):
        value = claim.get(value_name)
        if not isinstance(value, Mapping) or canonical_json_sha256(value) != claim.get(digest_name):
            raise SupervisorAuthorizationError(
                f"completed claim {value_name} digest is invalid"
            )
    claim_body = {name: value for name, value in claim.items() if name != "claim_sha256"}
    if canonical_json_sha256(claim_body) != claim.get("claim_sha256"):
        raise SupervisorAuthorizationError("completed claim digest is invalid")
    records = broker.BrokerJournal(
        state_root, str(case["case_id"]), str(grant["grant_id"])
    ).records()
    if (
        len(records) < 3
        or records[-3].get("event") != "POST_ISOLATION_VERIFIED"
        or records[-2].get("event") != "COMPLETED"
        or records[-1].get("event") != "ACL_RESTORED"
    ):
        raise SupervisorAuthorizationError(
            "protected broker journal does not end in COMPLETED then ACL_RESTORED"
        )
    if (
        records[-3].get("post_replacement_evidence_sha256")
        != result["post_replacement_evidence_sha256"]
    ):
        raise SupervisorAuthorizationError(
            "post-replacement isolation digest differs between journal and result"
        )
    if records[-2].get("result_sha256") != result["result_sha256"]:
        raise SupervisorAuthorizationError("broker journal result differs from the canonical result")
    final = _production_final_verifier(grant)
    evidence = {
        "grant_sha256": grant["grant_sha256"],
        "result_sha256": result["result_sha256"],
        "post_replacement_evidence_sha256": result[
            "post_replacement_evidence_sha256"
        ],
        "journal_event_sha256": records[-1]["event_sha256"],
        "journal_record_count": len(records),
        "acl_restore_evidence": acl_restore,
        "completed_cleanup_evidence": completed_cleanup,
        "final_repository_evidence": final,
    }
    evidence["evidence_sha256"] = canonical_json_sha256(evidence)
    return evidence


def production_dependencies() -> SupervisorDependencies:
    return SupervisorDependencies(
        controller_runner=_production_controller_runner,
        restart_runner=_production_restart_runner,
        isolation_collector=_production_isolation_collector,
        broker_runner=run_broker_process,
        schema_checker=generate_and_verify_app_server_schema,
        final_verifier=_production_final_verifier,
        completed_recovery_verifier=_production_completed_recovery_verifier,
        acl_restore_runner=_production_acl_restore_runner,
        pending_acl_recovery=_production_pending_acl_recovery,
        orphan_grant_recovery=_production_orphan_grant_recovery,
    )


def _active_grants(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    runtime = case.get("runtime")
    grants = runtime.get("action_grants", {}) if isinstance(runtime, Mapping) else {}
    if not isinstance(grants, Mapping):
        raise SupervisorAuthorizationError("canonical runtime grant map is malformed")
    return [
        copy.deepcopy(dict(grant))
        for grant in grants.values()
        if isinstance(grant, Mapping) and grant.get("status") in {"ISSUED", "CLAIMED"}
    ]


def _capture_worker_auth(controller_spec: Mapping[str, Any]) -> dict[str, Any]:
    """Bind an optional configured worker auth artifact without exposing its content."""
    root = Path(controller_spec["worker_codex_home"]).resolve(strict=True)
    path = root / "auth.json"
    if not path.exists():
        return {
            "present": False,
            "path": path,
            "ownership": "absent_at_start",
        }
    stat_before = path.stat()
    if (
        not path.is_file()
        or path.is_symlink()
        or path.resolve(strict=True).parent != root
        or stat_before.st_nlink != 1
        or stat_before.st_size > 1024 * 1024
    ):
        raise SupervisorAuthorizationError(
            "worker auth artifact must be one bounded direct regular file"
        )
    return {
        "present": True,
        "path": path,
        "ownership": "pre_existing_configured_worker_auth",
        "file_identity_before": {
            "device": stat_before.st_dev,
            "inode": stat_before.st_ino,
        },
        "sha256_before": file_sha256(path),
        "size_before": stat_before.st_size,
        "link_count_before": stat_before.st_nlink,
        "content_logged": False,
        "model_tool_access": False,
    }


def _cleanup_worker_auth(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Verify configured auth continuity.

    This supervisor does not create an auth artifact. Therefore an artifact that
    existed at startup is configuration owned by the operator and must be left
    in place. A future staging implementation may delete only an artifact for
    which it carries separate, creation-time ownership proof.
    """
    path = snapshot["path"]
    if snapshot.get("present") is not True:
        if not isinstance(path, Path) or path.exists():
            raise SupervisorAuthorizationError(
                "worker auth artifact appeared after being absent at startup"
            )
        return {
            "present": False,
            "ownership": "absent_at_start",
            "preservation_verified": True,
            "content_logged": False,
        }
    stat_after = path.stat() if isinstance(path, Path) and path.exists() else None
    if (
        not isinstance(path, Path)
        or snapshot.get("ownership") != "pre_existing_configured_worker_auth"
        or stat_after is None
        or not path.is_file()
        or path.is_symlink()
        or {
            "device": stat_after.st_dev,
            "inode": stat_after.st_ino,
        }
        != snapshot["file_identity_before"]
        or file_sha256(path) != snapshot["sha256_before"]
        or stat_after.st_size != snapshot["size_before"]
        or stat_after.st_nlink != snapshot["link_count_before"]
    ):
        raise SupervisorAuthorizationError("worker auth artifact changed during acceptance")
    sha256_after = file_sha256(path)
    return {
        "present": True,
        "ownership": "pre_existing_configured_worker_auth",
        "file_identity_before": snapshot["file_identity_before"],
        "file_identity_after": {
            "device": stat_after.st_dev,
            "inode": stat_after.st_ino,
        },
        "size_before": snapshot["size_before"],
        "size_after": stat_after.st_size,
        "sha256_before": snapshot["sha256_before"],
        "sha256_after": sha256_after,
        "unchanged": True,
        "retained": True,
        "preservation_verified": True,
        "content_logged": False,
    }


def fail_orphaned_active_grant(
    store: StoreProtocol,
    case_id: str,
    broker_sid: str,
    *,
    clock: Callable[[], str] = utc_now,
) -> dict[str, Any] | None:
    """Lock an active grant that survived without its in-memory supervisor context."""
    case = store.get_case(case_id)
    active = _active_grants(case)
    if not active:
        return None
    if len(active) != 1:
        raise SupervisorAuthorizationError("canonical case contains multiple orphaned active grants")
    grant = active[0]
    evidence = {
        "case_id": case_id,
        "grant_id": grant.get("grant_id"),
        "status": grant.get("status"),
        "reason": "trusted supervisor process context is absent after startup",
    }
    return store.fail_action_grant(
        case_id,
        failure={
            "protocol_version": ACTION_GRANT_RESULT_PROTOCOL_VERSION,
            "schema_version": 1,
            "grant_id": grant["grant_id"],
            "broker_principal_sid": broker_sid,
            "failure_stage": "supervisor_context",
            "failure_code": "SUPERVISOR_CONTEXT_LOST",
            "failure_evidence_sha256": canonical_json_sha256(evidence),
            "observed_at": clock(),
        },
        request_id=f"supervisor-orphan-{grant['grant_id']}",
        expected_revision=case["revision"],
    )


def _startup_case_gate(
    store: StoreProtocol,
    spec: Mapping[str, Any],
    broker_sid: str,
    dependencies: SupervisorDependencies,
) -> dict[str, Any] | None:
    """Resolve every durable runtime state before starting App Server."""
    case_id = spec["controller_spec"]["case_id"]
    case = store.get_case(case_id)
    runtime = case.get("runtime")
    grants = runtime.get("action_grants", {}) if isinstance(runtime, Mapping) else {}
    if not isinstance(grants, Mapping):
        raise SupervisorAuthorizationError("canonical runtime grant map is malformed")
    if not grants:
        abort_record = case.get("runtime_generation_abort")
        if abort_record is not None:
            if (
                case.get("state") != "CASE_LOCKED"
                or abort_record.get("reason_code")
                != "PREISSUE_GENERATION_ABANDONED"
            ):
                raise SupervisorAuthorizationError(
                    "preissue generation abort is not terminally locked"
                )
            result = {
                "protocol_version": SUPERVISOR_RESULT_PROTOCOL_VERSION,
                "schema_version": 1,
                "case_id": case_id,
                "recovered_preissue_generation_abort": True,
                "final_grant_status": "NOT_ISSUED",
                "case_state": "CASE_LOCKED",
                "controller_started": False,
                "abort_record_sha256": abort_record["record_sha256"],
            }
            result["result_sha256"] = canonical_json_sha256(result)
            return result
        generation_attempt = case.get("runtime_generation_attempt")
        if (
            isinstance(generation_attempt, Mapping)
            and generation_attempt.get("status") == "ABORTED"
        ):
            if case.get("state") != "CASE_LOCKED":
                raise SupervisorAuthorizationError(
                    "aborted runtime generation attempt is not terminally locked"
                )
            result = {
                "protocol_version": SUPERVISOR_RESULT_PROTOCOL_VERSION,
                "schema_version": 1,
                "case_id": case_id,
                "recovered_generation_attempt_abort": True,
                "final_grant_status": "NOT_ISSUED",
                "case_state": "CASE_LOCKED",
                "controller_started": False,
                "attempt_record_sha256": generation_attempt["record_sha256"],
            }
            result["result_sha256"] = canonical_json_sha256(result)
            return result
        if case.get("state") != "IMPLEMENTING":
            raise SupervisorAuthorizationError(
                "no-grant runtime start requires canonical state IMPLEMENTING"
            )
        recovered = dependencies.pending_acl_recovery(
            Path(spec["state_root"]), case_id, spec
        )
        if any(item.get("restored") is not True for item in recovered):
            raise SupervisorAuthorizationError(
                "pre-grant ACL recovery did not restore every pending snapshot"
            )
        if len(recovered) > 1:
            raise SupervisorAuthorizationError(
                "pre-grant ACL recovery returned multiple generation records"
            )
        current = store.get_case(case_id)
        abort_record = current.get("runtime_generation_abort")
        if (
            recovered
            and abort_record is None
            and current.get("runtime_generation_attempt") is None
        ):
            recovery = recovered[0]
            store.abort_runtime_generation(
                case_id,
                evidence={
                    "protocol_version": "ccos-preissue-generation-abort-v1",
                    "schema_version": 1,
                    "grant_id": recovery["grant_id"],
                    "snapshot_event_sha256": recovery["snapshot_event_sha256"],
                    "lockdown_intent_event_sha256": recovery[
                        "lockdown_intent_event_sha256"
                    ],
                    "acl_restored_event_sha256": recovery[
                        "acl_restored_event_sha256"
                    ],
                    "reason_code": "PREISSUE_GENERATION_ABANDONED",
                },
                request_id=f"supervisor-preissue-abort-{recovery['grant_id']}",
                expected_revision=current["revision"],
            )
            current = store.get_case(case_id)
            abort_record = current.get("runtime_generation_abort")
        generation_attempt = current.get("runtime_generation_attempt")
        if generation_attempt is not None:
            status = generation_attempt.get("status")
            if status == "CLAIMED":
                store.abort_runtime_generation_attempt(
                    case_id,
                    attempt_id=generation_attempt["attempt_id"],
                    request_id=(
                        f"supervisor-generation-abort-{generation_attempt['attempt_id']}"
                    ),
                    expected_revision=current["revision"],
                )
                current = store.get_case(case_id)
                generation_attempt = current.get("runtime_generation_attempt")
                status = generation_attempt.get("status")
            if status != "ABORTED" or current.get("state") != "CASE_LOCKED":
                raise SupervisorAuthorizationError(
                    "durable runtime generation attempt is not terminally locked"
                )
            result = {
                "protocol_version": SUPERVISOR_RESULT_PROTOCOL_VERSION,
                "schema_version": 1,
                "case_id": case_id,
                "recovered_generation_attempt_abort": True,
                "final_grant_status": "NOT_ISSUED",
                "case_state": "CASE_LOCKED",
                "controller_started": False,
                "attempt_record_sha256": generation_attempt["record_sha256"],
            }
            result["result_sha256"] = canonical_json_sha256(result)
            return result
        if abort_record is not None:
            if (
                current.get("state") != "CASE_LOCKED"
                or abort_record.get("reason_code")
                != "PREISSUE_GENERATION_ABANDONED"
            ):
                raise SupervisorAuthorizationError(
                    "preissue generation abort is not terminally locked"
                )
            result = {
                "protocol_version": SUPERVISOR_RESULT_PROTOCOL_VERSION,
                "schema_version": 1,
                "case_id": case_id,
                "recovered_preissue_generation_abort": True,
                "final_grant_status": "NOT_ISSUED",
                "case_state": "CASE_LOCKED",
                "controller_started": False,
                "abort_record_sha256": abort_record["record_sha256"],
            }
            result["result_sha256"] = canonical_json_sha256(result)
            return result
        return None
    if len(grants) != 1:
        raise SupervisorAuthorizationError("canonical case contains multiple runtime grants")
    grant = copy.deepcopy(dict(next(iter(grants.values()))))
    status = grant.get("status")
    if status in {"ISSUED", "CLAIMED"}:
        orphan = dependencies.orphan_grant_recovery(
            Path(spec["state_root"]), case_id, str(grant["grant_id"])
        )
        if orphan.get("status") != "FAILED":
            raise SupervisorAuthorizationError(
                "orphaned active grant recovery did not fail and lock the grant"
            )
        raise SupervisorAuthorizationError("orphaned active grant was failed and the case locked")
    if status == "FAILED":
        cleanup = dependencies.orphan_grant_recovery(
            Path(spec["state_root"]), case_id, str(grant["grant_id"])
        )
        if cleanup.get("status") != "failed_stable":
            raise SupervisorAuthorizationError("failed grant ACL cleanup was not verified")
        if case.get("state") != "CASE_LOCKED":
            raise SupervisorAuthorizationError("failed grant is not paired with CASE_LOCKED")
        result = {
            "protocol_version": SUPERVISOR_RESULT_PROTOCOL_VERSION,
            "schema_version": 1,
            "case_id": case_id,
            "recovered_terminal_failure": True,
            "final_grant_status": "FAILED",
            "case_state": "CASE_LOCKED",
            "controller_started": False,
        }
        result["result_sha256"] = canonical_json_sha256(result)
        return result
    if status == "COMPLETED":
        recovery = dependencies.completed_recovery_verifier(
            Path(spec["state_root"]), case, grant, spec
        )
        result = {
            "protocol_version": SUPERVISOR_RESULT_PROTOCOL_VERSION,
            "schema_version": 1,
            "case_id": case_id,
            "recovered_completed": True,
            "final_grant_status": "COMPLETED",
            "controller_started": False,
            "recovery_evidence": recovery,
        }
        result["result_sha256"] = canonical_json_sha256(result)
        return result
    raise SupervisorAuthorizationError("canonical runtime grant has an unknown durable status")


def _bind_native_identities(
    store: StoreProtocol,
    case_id: str,
    bundle: Mapping[str, Any],
    nonce_factory: Callable[[], str],
) -> list[dict[str, Any]]:
    checkpoint = bundle.get("restart_checkpoint")
    identities = checkpoint.get("identities") if isinstance(checkpoint, Mapping) else None
    actors = bundle.get("actor_binding_requests")
    if not isinstance(identities, list) or len(identities) != 8 or not isinstance(actors, list):
        raise SupervisorAuthorizationError("controller did not return the exact parent/child cohort")
    actor_threads = {
        normalize_binding("thread", str(actor.get("thread_id", "")))
        for actor in actors
        if isinstance(actor, Mapping)
    }
    if len(actor_threads) != 7:
        raise SupervisorAuthorizationError("controller actor cohort must contain seven unique threads")
    observed_threads: set[str] = set()
    results: list[dict[str, Any]] = []
    for identity in identities:
        thread_id = normalize_binding("thread", str(identity.get("thread_id", "")))
        if thread_id in observed_threads:
            raise SupervisorAuthorizationError("controller identity cohort duplicates a thread")
        observed_threads.add(thread_id)
        if thread_id not in actor_threads:
            continue
        case = store.get_case(case_id)
        results.append(
            store.bind(
                case_id,
                kind="thread",
                value=thread_id,
                request_id=f"supervisor-bind-thread-{nonce_factory()}",
                expected_revision=case["revision"],
            )
        )
    if len(actors) != 7 or len(observed_threads - actor_threads) != 1:
        raise SupervisorAuthorizationError("controller cohort must contain one entirely unbound unknown child")
    for actor in actors:
        case = store.get_case(case_id)
        results.append(
            store.bind_runtime_actor(
                case_id,
                actor=actor,
                request_id=f"supervisor-bind-actor-{nonce_factory()}",
                expected_revision=case["revision"],
            )
        )
    return results


def _snapshot_authority_state(case: Mapping[str, Any], target: Path) -> dict[str, Any]:
    runtime = case.get("runtime")
    grants = runtime.get("action_grants", {}) if isinstance(runtime, Mapping) else {}
    return {
        "revision": case.get("revision"),
        "state": case.get("state"),
        "grant_sha256": canonical_json_sha256(grants if isinstance(grants, Mapping) else {}),
        "target_sha256": file_sha256(target),
    }


def _compose_live_controller_evidence(
    *,
    bundle: Mapping[str, Any],
    restart_evidence: Mapping[str, Any],
    controller_spec: Mapping[str, Any],
    stale_revision_denial: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    def run_evidence(
        capability: Any, transport_audit: Any,
    ) -> dict[str, Any]:
        capability_copy = copy.deepcopy(capability)
        audit_copy = copy.deepcopy(transport_audit)
        server_request_count = sum(
            isinstance(item, Mapping) and item.get("event") == "server_request"
            for item in audit_copy
        ) if isinstance(audit_copy, list) else 0
        return {
            "capability_evidence": capability_copy,
            "capability_evidence_sha256": canonical_json_sha256(capability_copy),
            "transport_audit": audit_copy,
            "transport_audit_sha256": canonical_json_sha256(audit_copy),
            "process_tree_closed": True,
            "server_request_count": server_request_count,
            "server_request_policy_status": (
                "EXERCISED_NON_AUTHORIZING"
                if server_request_count
                else "UNEXERCISED"
            ),
        }

    raw = {
        "protocol_version": LIVE_CONTROLLER_EVIDENCE_PROTOCOL_VERSION,
        "schema_version": 1,
        "case_id": controller_spec["case_id"],
        "initial_run": run_evidence(
            bundle.get("capability_evidence"), bundle.get("transport_audit")
        ),
        "restart_run": run_evidence(
            restart_evidence.get("capability_evidence"),
            restart_evidence.get("transport_audit"),
        ),
        "incomplete_child_evidence": copy.deepcopy(
            bundle.get("incomplete_child_evidence")
        ),
        "stale_revision_denial": copy.deepcopy(dict(stale_revision_denial)),
        "all_mutation_surfaces_disabled": True,
        "both_process_trees_closed": True,
    }
    normalized = normalize_live_controller_evidence(
        raw,
        worker_sid=controller_spec["worker_principal_sid"],
        broker_sid=controller_spec["broker_principal_sid"],
        app_server_sha256=controller_spec["expected_app_server_sha256"],
        app_server_version=controller_spec["expected_app_server_version"],
        environment_sha256=controller_spec["app_server_environment_sha256"],
        expected_case_id=controller_spec["case_id"],
    )
    return normalized, canonical_json_sha256(normalized)


def _runtime_action_check_evidence(
    store: StoreProtocol,
    case_id: str,
    bundle: Mapping[str, Any],
    grant: Mapping[str, Any],
) -> list[dict[str, Any]]:
    actors = bundle["actor_binding_requests"]
    implementation = [
        actor for actor in actors
        if actor["controller_assigned_role"] == "implementer_child"
    ]
    if len(implementation) != 1:
        raise SupervisorAuthorizationError("runtime check requires one implementation actor")
    common = {
        "repository": grant["repository"],
        "branch": grant["branch"],
        "worktree": grant["worktree"],
        "head": grant["base_head"],
    }
    evidence: list[dict[str, Any]] = []
    for identity in bundle["authorization_evidence"][
        "nonimplementation_identities_for_supervisor_probe"
    ]:
        role = identity["controller_assigned_role"]
        declared = "implementer_child" if role == "unknown_child" else role
        response = store.check_action(
            case_id,
            "implementation",
            actor_role=declared,
            actor_thread_id=identity["thread_id"],
            thread=identity["thread_id"],
            **common,
        )
        if response.get("allowed") is not False:
            raise SupervisorAuthorizationError("non-implementer passed runtime-aware action check")
        evidence.append(
            {
                "thread_id": identity["thread_id"],
                "probe": "nonimplementation",
                "reason_codes": copy.deepcopy(response.get("reason_codes")),
                "allowed": False,
            }
        )
        forged = store.check_action(
            case_id,
            "implementation",
            actor_role="implementer_child",
            actor_thread_id=identity["thread_id"],
            thread=identity["thread_id"],
            **common,
        )
        expected_reason = (
            "RUNTIME_ACTOR_UNBOUND" if role == "unknown_child" else "ACTOR_ROLE_MISMATCH"
        )
        if forged.get("allowed") is not False or expected_reason not in (
            forged.get("reason_codes") or []
        ):
            raise SupervisorAuthorizationError(
                "non-implementer forged-role probe did not fail at the runtime boundary"
            )
        evidence.append(
            {
                "thread_id": identity["thread_id"],
                "probe": "forged_implementer_role",
                "reason_codes": copy.deepcopy(forged.get("reason_codes")),
                "allowed": False,
            }
        )
    implementer = implementation[0]
    allowed = store.check_action(
        case_id,
        "implementation",
        actor_role="implementer_child",
        actor_thread_id=implementer["thread_id"],
        thread=implementer["thread_id"],
        **common,
    )
    if allowed.get("allowed") is not True:
        raise SupervisorAuthorizationError("canonical implementer failed runtime-aware action check")
    forged = store.check_action(
        case_id,
        "implementation",
        actor_role="parent",
        actor_thread_id=implementer["thread_id"],
        thread=implementer["thread_id"],
        **common,
    )
    if forged.get("allowed") is not False or "ACTOR_ROLE_MISMATCH" not in (
        forged.get("reason_codes") or []
    ):
        raise SupervisorAuthorizationError("forged actor role was not rejected as a mismatch")
    evidence.extend(
        [
            {
                "thread_id": implementer["thread_id"],
                "probe": "canonical_implementer",
                "reason_codes": copy.deepcopy(allowed.get("reason_codes")),
                "allowed": True,
            },
            {
                "thread_id": implementer["thread_id"],
                "probe": "forged_parent_role",
                "reason_codes": copy.deepcopy(forged.get("reason_codes")),
                "allowed": False,
            },
        ]
    )
    return evidence


def _assert_real_grant_denials(
    store: StoreProtocol,
    case_id: str,
    grant: Mapping[str, Any],
    denied_identities: list[Mapping[str, Any]],
    target: Path,
    nonce_factory: Callable[[], str],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for identity in denied_identities:
        probe = copy.deepcopy(dict(grant))
        probe["actor_thread_id"] = normalize_binding("thread", str(identity["thread_id"]))
        before_case = store.get_case(case_id)
        before = _snapshot_authority_state(before_case, target)
        try:
            store.issue_action_grant(
                case_id,
                grant=probe,
                request_id=f"supervisor-denial-{nonce_factory()}",
                expected_revision=before_case["revision"],
            )
        except AuthorizationError as exc:
            after = _snapshot_authority_state(store.get_case(case_id), target)
            if after != before:
                raise SupervisorAuthorizationError("denied grant probe changed canonical or target state")
            evidence.append(
                {
                    "thread_id": probe["actor_thread_id"],
                    "controller_assigned_role": identity["controller_assigned_role"],
                    "denial": type(exc).__name__,
                    "state_unchanged": True,
                }
            )
        else:
            raise SupervisorAuthorizationError("non-implementation identity obtained a real grant")
    return evidence


class RuntimeSupervisor:
    def __init__(
        self,
        spec: Mapping[str, Any],
        *,
        store: StoreProtocol | None = None,
        dependencies: SupervisorDependencies | None = None,
    ) -> None:
        self.spec = normalize_supervisor_spec(spec)
        self.store = store or CaseStore(Path(self.spec["state_root"]))
        self.dependencies = dependencies or production_dependencies()
        self.key_zeroized = False

    def run(self) -> dict[str, Any]:
        spec = self.spec
        controller_spec = spec["controller_spec"]
        case_id = controller_spec["case_id"]
        _broker_name, current_sid = self.dependencies.identity_resolver()
        if current_sid != controller_spec["broker_principal_sid"]:
            raise SupervisorAuthorizationError("supervisor principal differs from the sealed broker SID")
        terminal = _startup_case_gate(
            self.store, spec, current_sid, self.dependencies
        )
        if terminal is not None:
            return terminal
        target = Path(controller_spec["worktree"]).joinpath(
            *controller_spec["target_path"].split("/")
        )
        if file_sha256(target) != controller_spec["baseline_sha256"]:
            raise SupervisorAuthorizationError(
                "fresh runtime generation requires the exact target baseline"
            )
        attempt_id = require_stable_id(
            f"controller-{controller_spec['grant_id']}", "generation attempt id"
        )
        attempt = {
            "protocol_version": RUNTIME_GENERATION_ATTEMPT_PROTOCOL_VERSION,
            "schema_version": 1,
            "attempt_id": attempt_id,
            "grant_id": controller_spec["grant_id"],
            "controller_spec_sha256": canonical_json_sha256(controller_spec),
        }
        if Path(controller_spec["proposal_artifact_path"]).exists():
            raise SupervisorAuthorizationError(
                "fresh supervisor run requires an absent proposal artifact path"
            )
        auth_snapshot: Mapping[str, Any] | None = _capture_worker_auth(controller_spec)
        try:
            schema_evidence = self.dependencies.schema_checker(spec)
            key = bytearray(secrets.token_bytes(32))
        except BaseException:
            _cleanup_worker_auth(auth_snapshot)
            raise
        current_case = self.store.get_case(case_id)
        try:
            self.store.claim_runtime_generation_attempt(
                case_id,
                attempt=attempt,
                request_id=f"supervisor-generation-claim-{attempt_id}",
                expected_revision=current_case["revision"],
            )
        except BaseException:
            for index in range(len(key)):
                key[index] = 0
            self.key_zeroized = all(value == 0 for value in key)
            _cleanup_worker_auth(auth_snapshot)
            raise
        result_payload: dict[str, Any] | None = None
        acl_lockdown_context: dict[str, Any] | None = None
        try:
            bundle = self.dependencies.controller_runner(controller_spec, key)
            if bundle.get("case_id") != case_id or "canonical_issue_request" in bundle:
                raise SupervisorAuthorizationError("controller bundle bypasses post-proposal supervision")
            restart_evidence = self.dependencies.restart_runner(
                controller_spec, bundle["restart_checkpoint"]
            )
            if restart_evidence.get("continuity_verified") is not True:
                raise SupervisorAuthorizationError("App Server restart continuity was not proven")
            isolation = self.dependencies.isolation_collector(self.store, spec, bundle)
            required_isolation_fields = {
                "model_worker_principal_sid", "sandbox_group_principal_sid",
                "denied_principal_sids", "group_membership_evidence", "isolation_evidence",
                "protected_acl_snapshot", "protected_acl_snapshot_sha256",
                "preissue_dacl_evidence", "preissue_dacl_evidence_sha256",
            }
            if not isinstance(isolation, Mapping) or not required_isolation_fields.issubset(isolation):
                raise SupervisorAuthorizationError("dual-profile isolation collector is incomplete")
            grant = copy.deepcopy(dict(bundle["grant_core"]))
            if (
                isolation["model_worker_principal_sid"] != grant["model_worker_principal_sid"]
                or isolation["sandbox_group_principal_sid"]
                != grant["sandbox_group_principal_sid"]
                or isolation["denied_principal_sids"] != grant["denied_principal_sids"]
            ):
                raise SupervisorAuthorizationError(
                    "dual-profile isolation identities differ from the signed controller context"
                )
            grant["group_membership_evidence"] = copy.deepcopy(
                isolation["group_membership_evidence"]
            )
            grant["isolation_evidence"] = copy.deepcopy(isolation["isolation_evidence"])
            for name in (
                "protected_acl_snapshot", "protected_acl_snapshot_sha256",
                "preissue_dacl_evidence", "preissue_dacl_evidence_sha256",
            ):
                grant[name] = copy.deepcopy(isolation[name])
            acl_lockdown_context = {
                "grant_id": grant["grant_id"],
                "protected_acl_snapshot": grant["protected_acl_snapshot"],
                "protected_acl_snapshot_sha256": grant[
                    "protected_acl_snapshot_sha256"
                ],
                "preissue_dacl_evidence": grant["preissue_dacl_evidence"],
            }
            issued_at = dt.datetime.fromisoformat(self.dependencies.clock())
            expires_at = issued_at + dt.timedelta(seconds=DEFAULT_GRANT_LIFETIME_SECONDS)
            if (expires_at - issued_at).total_seconds() > MAX_GRANT_LIFETIME_SECONDS:
                raise AssertionError("supervisor grant lifetime exceeds the fixed maximum")
            grant["expires_at"] = expires_at.isoformat()
            target = Path(grant["worktree"]).joinpath(*grant["target_path"].split("/"))
            current_case = self.store.get_case(case_id)
            stale_revision = max(0, int(current_case["revision"]) - 1)
            before_stale = _snapshot_authority_state(current_case, target)
            stale_revision_denial = {
                "denial": "RevisionConflict",
                "attempted_revision": stale_revision,
                "current_revision": current_case["revision"],
                "authority_state_sha256_before": canonical_json_sha256(before_stale),
                "authority_state_sha256_after": canonical_json_sha256(before_stale),
                "state_unchanged": True,
            }
            live_controller_evidence, live_controller_evidence_sha256 = (
                _compose_live_controller_evidence(
                    bundle=bundle,
                    restart_evidence=restart_evidence,
                    controller_spec=controller_spec,
                    stale_revision_denial=stale_revision_denial,
                )
            )
            bundle = finalize_controller_bundle(
                bundle,
                live_controller_evidence,
                live_controller_evidence_sha256,
                bytes(key),
            )
            finalized_core = bundle["grant_core"]
            for name, value in finalized_core.items():
                if name in {
                    "controller_receipt_sha256",
                    "live_controller_evidence",
                    "live_controller_evidence_sha256",
                }:
                    grant[name] = copy.deepcopy(value)
                elif grant.get(name) != value:
                    raise SupervisorAuthorizationError(
                        "finalized controller grant differs from the proposal draft"
                    )
            try:
                self.store.issue_action_grant(
                    case_id,
                    grant=grant,
                    request_id=f"supervisor-stale-{self.dependencies.nonce_factory()}",
                    expected_revision=stale_revision,
                )
            except RevisionConflict:
                after_stale = _snapshot_authority_state(
                    self.store.get_case(case_id), target
                )
                if (
                    after_stale != before_stale
                    or canonical_json_sha256(after_stale)
                    != stale_revision_denial["authority_state_sha256_after"]
                ):
                    raise SupervisorAuthorizationError(
                        "stale revision probe changed canonical state"
                    )
            else:
                raise SupervisorAuthorizationError(
                    "stale case revision issued an action grant"
                )
            bindings = _bind_native_identities(
                self.store, case_id, bundle, self.dependencies.nonce_factory
            )
            action_check_evidence = _runtime_action_check_evidence(
                self.store, case_id, bundle, grant
            )
            denied = bundle["authorization_evidence"][
                "nonimplementation_identities_for_supervisor_probe"
            ]
            denial_evidence = _assert_real_grant_denials(
                self.store, case_id, grant, denied, target, self.dependencies.nonce_factory
            )
            issued_at = dt.datetime.fromisoformat(self.dependencies.clock())
            grant["expires_at"] = (
                issued_at + dt.timedelta(seconds=DEFAULT_GRANT_LIFETIME_SECONDS)
            ).isoformat()
            current_case = self.store.get_case(case_id)
            issue = self.store.issue_action_grant(
                case_id,
                grant=grant,
                request_id=f"supervisor-issue-{self.dependencies.nonce_factory()}",
                expected_revision=current_case["revision"],
            )
            if issue.get("idempotent") is not False:
                raise SupervisorAuthorizationError("real action grant issuance was idempotent")
            broker_arguments = (
                Path(spec["state_root"]), case_id, grant["grant_id"],
                bundle["controller_receipt"], key,
            )
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="ccos-broker-race") as pool:
                race_results = [
                    future.result()
                    for future in [
                        pool.submit(self.dependencies.broker_runner, *broker_arguments),
                        pool.submit(self.dependencies.broker_runner, *broker_arguments),
                    ]
                ]
            if [bool(item.get("ok")) for item in race_results].count(True) != 1:
                raise SupervisorAuthorizationError(
                    "concurrent broker race did not yield exactly one success and one denial"
                )
            broker_result = next(item for item in race_results if item.get("ok") is True)
            concurrent_denial = next(item for item in race_results if item.get("ok") is False)
            replay_result = self.dependencies.broker_runner(
                Path(spec["state_root"]), case_id, grant["grant_id"],
                bundle["controller_receipt"], key,
            )
            if replay_result.get("ok") is not False:
                raise SupervisorAuthorizationError("post-restart broker replay was not denied")
            final_case = self.store.get_case(case_id)
            canonical_grant = final_case["runtime"]["action_grants"][grant["grant_id"]]
            if canonical_grant.get("status") != "COMPLETED":
                raise SupervisorAuthorizationError("canonical grant did not complete")
            if file_sha256(target) != grant["replacement_sha256"]:
                raise SupervisorAuthorizationError("target differs from the exact replacement")
            final_repository_evidence = self.dependencies.final_verifier(grant)
            before_second = _snapshot_authority_state(final_case, target)
            try:
                self.store.issue_action_grant(
                    case_id,
                    grant=grant,
                    request_id=f"supervisor-second-{self.dependencies.nonce_factory()}",
                    expected_revision=final_case["revision"],
                )
            except CaseStateError as exc:
                if _snapshot_authority_state(self.store.get_case(case_id), target) != before_second:
                    raise SupervisorAuthorizationError("second action denial changed canonical state")
                second_action_denial = type(exc).__name__
            else:
                raise SupervisorAuthorizationError("a second action grant was issued")
            result = {
                "protocol_version": SUPERVISOR_RESULT_PROTOCOL_VERSION,
                "schema_version": 1,
                "case_id": case_id,
                "schema_evidence": schema_evidence,
                "live_controller_evidence_sha256": live_controller_evidence_sha256,
                "binding_count": len(bindings),
                "restart_evidence": restart_evidence,
                "runtime_action_check_evidence": action_check_evidence,
                "canonical_denial_evidence": denial_evidence,
                "grant_issue_result": issue,
                "broker_result": broker_result,
                "concurrent_broker_denial": concurrent_denial,
                "replay_denial": replay_result,
                "second_action_denial": second_action_denial,
                "final_repository_evidence": final_repository_evidence,
                "final_grant_status": "COMPLETED",
                "accepted_action_count": 1,
            }
            result["result_sha256"] = canonical_json_sha256(result)
            result_payload = result
            return result
        finally:
            try:
                for index in range(len(key)):
                    key[index] = 0
                self.key_zeroized = all(value == 0 for value in key)
                try:
                    acl_restore_evidence = None
                    if acl_lockdown_context is not None:
                        acl_restore_evidence = self.dependencies.acl_restore_runner(
                            Path(spec["state_root"]),
                            case_id,
                            str(acl_lockdown_context["grant_id"]),
                            acl_lockdown_context["protected_acl_snapshot"],
                            str(acl_lockdown_context["protected_acl_snapshot_sha256"]),
                            acl_lockdown_context["preissue_dacl_evidence"],
                            "supervisor_finally",
                        )
                        if acl_restore_evidence.get("restored") is not True:
                            raise SupervisorAuthorizationError(
                                "supervisor finalizer did not restore protected ACLs"
                            )
                finally:
                    auth_evidence = (
                        _cleanup_worker_auth(auth_snapshot)
                        if auth_snapshot is not None
                        else None
                    )
                if result_payload is not None:
                    result_payload["auth_artifact_evidence"] = auth_evidence
                    result_payload["acl_restore_evidence"] = acl_restore_evidence
                    result_payload["result_sha256"] = canonical_json_sha256(
                        {
                            name: value
                            for name, value in result_payload.items()
                            if name != "result_sha256"
                        }
                    )
            finally:
                current_case = self.store.get_case(case_id)
                generation_attempt = current_case.get("runtime_generation_attempt")
                if (
                    isinstance(generation_attempt, Mapping)
                    and generation_attempt.get("status") == "CLAIMED"
                ):
                    self.store.abort_runtime_generation_attempt(
                        case_id,
                        attempt_id=attempt_id,
                        request_id=f"supervisor-generation-abort-{attempt_id}",
                        expected_revision=current_case["revision"],
                    )


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SupervisorValidationError("supervisor spec file must contain a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--spec-file", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command != "run":
            raise AssertionError(args.command)
        result = RuntimeSupervisor(_load_json(args.spec_file)).run()
    except (OSError, ValueError, CaseStateError, broker.BrokerError, SupervisorError) as exc:
        payload = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"{payload['error']}: {payload['message']}", file=sys.stderr)
        return 2
    payload = {"ok": True, "result": result}
    print(json.dumps(payload, sort_keys=True) if args.json else json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
