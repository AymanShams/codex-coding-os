#!/usr/bin/env python3
"""Finite, case-scoped state engine for automated implementation and review.

The engine deliberately uses only the Python standard library.  Its canonical
store lives outside a managed Coding OS tree by default and combines the case
records and binding registry in one atomically replaced JSON file.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.parse
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping


SCHEMA_VERSION = 2
ACTION_PROTOCOL_VERSION = "ccos-case-action-v1"
REVIEW_COHORT_PROTOCOL_VERSION = "ccos-review-cohort-v1"
REVIEW_COMPLETION_PROTOCOL_VERSION = "ccos-review-completion-v1"
RUNTIME_ACTOR_PROTOCOL_VERSION = "ccos-runtime-actor-v1"
ACTION_GRANT_PROTOCOL_VERSION = "ccos-runtime-action-grant-v1"
ACTION_GRANT_CLAIM_PROTOCOL_VERSION = "ccos-runtime-action-claim-v1"
ACTION_GRANT_RESULT_PROTOCOL_VERSION = "ccos-runtime-action-result-v1"
PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION = "ccos-proposal-action-grant-v2"
PROPOSAL_ACTION_CLAIM_PROTOCOL_VERSION = "ccos-proposal-action-claim-v2"
PROPOSAL_ACTION_RESULT_PROTOCOL_VERSION = "ccos-proposal-action-result-v2"
PROPOSAL_ACTION_AUTHORITY_PROTOCOL_VERSION = "ccos-proposal-action-authority-v1"
WINDOWS_PRINCIPAL_PROBE_PROTOCOL_VERSION_V1 = "ccos-windows-principal-probe-v1"
WINDOWS_PRINCIPAL_PROBE_PROTOCOL_VERSION = "ccos-windows-principal-probe-v2"
WINDOWS_ISOLATION_EVIDENCE_PROTOCOL_VERSION = "ccos-windows-isolation-evidence-v2"
WINDOWS_GROUP_MEMBERSHIP_PROTOCOL_VERSION = "ccos-windows-sandbox-membership-v1"
WINDOWS_DACL_EVIDENCE_PROTOCOL_VERSION = "ccos-windows-dacl-evidence-v2"
PROPOSAL_DACL_EVIDENCE_PROTOCOL_VERSION = "ccos-proposal-dacl-evidence-v1"
PROPOSAL_DACL_EVIDENCE_MODE = "broker_dacl_v1"
TRUSTED_WRITE_PROBE_PROTOCOL_VERSION = "ccos-trusted-write-probe-v1"
TERMINAL_QUARANTINE_PROTOCOL_VERSION = "ccos-terminal-quarantine-v1"
RUNTIME_GENERATION_ABORT_PROTOCOL_VERSION = "ccos-preissue-generation-abort-v1"
RUNTIME_GENERATION_ATTEMPT_PROTOCOL_VERSION = "ccos-runtime-generation-attempt-v1"
LIVE_CONTROLLER_EVIDENCE_PROTOCOL_VERSION = "ccos-live-controller-evidence-v1"
QUARANTINE_AUDIT_PROTOCOL_VERSION = "ccos-quarantine-audit-v1"
STORE_FILENAME = "case-state.json"
LOCK_FILENAME = ".case-state.lock"
QUARANTINE_AUDIT_FILENAME = "quarantine-audit.jsonl"
QUARANTINE_BACKUP_DIRECTORY = "quarantine-backups"
ACTION_ARTIFACT_DIRECTORY = "action-artifacts"
SNAPSHOT_CONTRACT = "ccos-git-snapshot-v1"
GIT_SNAPSHOT_MAGIC = b"CCOS-GIT-SNAPSHOT\0"
LEGACY_FILESYSTEM_SNAPSHOT_CONTRACT = "ccos-snapshot-v1"
LEGACY_FILESYSTEM_SNAPSHOT_MAGIC = b"CCOS-CASE-SNAPSHOT\0"

LIVE_CONTROLLER_CLIENT_METHODS = frozenset(
    {
        "initialize",
        "mcpServerStatus/list",
        "hooks/list",
        "thread/start",
        "turn/start",
        "thread/read",
        "thread/list",
    }
)
LIVE_CONTROLLER_INITIAL_REQUIRED_METHODS = LIVE_CONTROLLER_CLIENT_METHODS
LIVE_CONTROLLER_RESTART_REQUIRED_METHODS = frozenset(
    {"initialize", "mcpServerStatus/list", "hooks/list", "thread/read", "thread/list"}
)
LIVE_CONTROLLER_MUTATION_APPROVAL_METHODS = frozenset(
    {"item/fileChange/requestApproval", "item/commandExecution/requestApproval"}
)
LIVE_CONTROLLER_ENVIRONMENT_NAMES = frozenset(
    {
        "CODEX_HOME", "COMSPEC", "LOCALAPPDATA", "NO_COLOR", "PATH", "PATHEXT",
        "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "WINDIR",
    }
)

CASE_STATES = {
    "REGISTERED",
    "IMPLEMENTING",
    "CANDIDATE_FROZEN",
    "REVIEW_COLLECTING",
    "FINDINGS_FROZEN",
    "REPAIR_AUTHORIZED",
    "REPAIR_COMPLETE",
    "CLOSURE_PREFLIGHT",
    "CLOSURE_CHECK",
    "CLOSED_SUCCESS",
    "CASE_LOCKED",
    "CONTROL_FAILURE",
}
FINDING_CLASSES = {
    "CURRENT_BLOCKER",
    "NON_BLOCKING",
    "INVALID_OR_STALE",
    "REDESIGN_REQUIRED",
    "CONTROL_FAILURE",
}
BINDING_KINDS = {"repo_url", "branch", "worktree", "pr", "thread", "universal_bundle"}
EXCLUSIVE_BINDING_KINDS = BINDING_KINDS - {"repo_url"}
SEPARATE_AUTHORITY_ACTIONS = {
    "merge",
    "deployment",
    "release",
    "credential_change",
    "universal_sync",
}
ROLE_ACTIONS = {
    "parent": {"case_administration"},
    "implementer_child": {"implementation", "product_work"},
    "review_child": {"review_collection", "closure_check"},
    "closure_child": {"closure_check"},
    "incomplete_child": set(),
    "closure_child": {"closure_check"},
    "incomplete_child": set(),
    "fix_child": {"repair"},
    "publication_child": {"publication", *SEPARATE_AUTHORITY_ACTIONS},
}
# Read-only eligibility predicates. Lifecycle transitions remain solely in the
# mutation methods below and are not duplicated by the action guard.
ACTION_ELIGIBLE_STATES = {
    "implementation": {"REGISTERED", "IMPLEMENTING"},
    "product_work": {"REGISTERED", "IMPLEMENTING"},
    "review_collection": {"REVIEW_COLLECTING"},
    "repair": {"REPAIR_AUTHORIZED"},
    "closure_check": {"CLOSURE_CHECK"},
    "publication": {"CLOSED_SUCCESS"},
    "merge": {"CLOSED_SUCCESS"},
    "deployment": {"CLOSED_SUCCESS"},
    "release": {"CLOSED_SUCCESS"},
    "credential_change": {"CLOSED_SUCCESS"},
    "universal_sync": {"CLOSED_SUCCESS"},
}
HEAD_REQUIRED_ACTIONS = {
    "review_collection",
    "repair",
    "closure_check",
    "publication",
    *SEPARATE_AUTHORITY_ACTIONS,
}
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
FINDING_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
WINDOWS_SID_PATTERN = re.compile(r"^S-\d(?:-\d+)+$", re.IGNORECASE)
RUNTIME_ACTOR_ROLES = frozenset(
    {"parent", "implementer_child", "review_child", "closure_child", "incomplete_child"}
)
REVIEW_COMPLETION_STATES = frozenset({"COMPLETED", "FAILED", "INCOMPLETE"})
ACTION_GRANT_STATUSES = frozenset({"ISSUED", "CLAIMED", "COMPLETED", "FAILED"})
MAX_REPLACEMENT_BYTES = 8 * 1024 * 1024
MAX_ACTION_GRANT_LIFETIME_SECONDS = 15 * 60
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
WINDOWS_REQUIRED_DENY_RIGHTS_MASK = 278 | 65536 | 64 | 262144 | 524288
PROTECTED_ROOT_KINDS = (
    "target_root",
    "state_root",
    "broker_source_root",
    "proposal_root",
)


class CaseStateError(RuntimeError):
    """Base error for case-state operations."""


class ValidationError(CaseStateError):
    pass


class StoreCorruptionError(CaseStateError):
    pass


class ConflictError(CaseStateError):
    pass


class RevisionConflict(ConflictError):
    pass


class TransitionError(CaseStateError):
    pass


class LimitError(CaseStateError):
    pass


class AuthorizationError(CaseStateError):
    pass


class PreflightError(CaseStateError):
    pass


class ControlFailureError(CaseStateError):
    pass


class SnapshotError(CaseStateError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def default_state_root() -> Path:
    return Path.home() / ".codex" / "case-state"


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.expanduser().resolve(strict=False).relative_to(parent.expanduser().resolve(strict=False))
        return True
    except ValueError:
        return False


def canonical_case_id(value: str) -> str:
    raw = str(value).strip()
    try:
        parsed = uuid.UUID(raw)
    except (ValueError, AttributeError) as exc:
        raise ValidationError("case_id must be a lowercase canonical UUID") from exc
    canonical = str(parsed)
    if raw != canonical:
        raise ValidationError("case_id must be a lowercase canonical UUID")
    return canonical


def require_request_id(value: str) -> str:
    raw = str(value).strip()
    if not raw or len(raw) > 200:
        raise ValidationError("request_id must be a non-empty identifier of at most 200 characters")
    return raw


def require_sha(value: str, label: str = "SHA") -> str:
    raw = str(value).strip().lower()
    if not SHA_PATTERN.fullmatch(raw):
        raise ValidationError(f"{label} must be a full lowercase 40-character hexadecimal commit SHA")
    return raw


def require_snapshot_hash(value: str) -> str:
    raw = str(value).strip().lower()
    if not HASH_PATTERN.fullmatch(raw):
        raise ValidationError("snapshot sha256 must be 64 lowercase hexadecimal characters")
    return raw


def _nonempty(value: Any, label: str, limit: int = 4096) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be a string")
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized:
        raise ValidationError(f"{label} must not be empty")
    if len(normalized) > limit:
        raise ValidationError(f"{label} exceeds {limit} characters")
    return normalized


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def serialized_store_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_SUBPROCESS_ENVIRONMENT_ALLOWLIST = (
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "PATHEXT",
)
_SECRET_ENVIRONMENT_MARKERS = (
    "AUTH",
    "CREDENTIAL",
    "KEY",
    "PASSWORD",
    "SECRET",
    "TOKEN",
)


def resolved_executable(*names: str) -> str:
    """Resolve a helper once to an absolute file path before execution."""
    for name in names:
        located = shutil.which(name)
        if located:
            path = Path(located).resolve(strict=True)
            if path.is_file():
                return str(path)
    raise SnapshotError(f"required executable is unavailable: {', '.join(names)}")


def safe_subprocess_environment(
    executable: str, *, extra: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Build a minimal helper environment with no inherited credentials."""
    environment = {
        name: os.environ[name]
        for name in _SUBPROCESS_ENVIRONMENT_ALLOWLIST
        if os.environ.get(name)
    }
    environment["PATH"] = str(Path(executable).resolve(strict=True).parent)
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    for name, value in (extra or {}).items():
        upper = name.upper()
        if any(marker in upper for marker in _SECRET_ENVIRONMENT_MARKERS):
            raise ValidationError(f"secret-like environment variable is forbidden for helpers: {name}")
        environment[name] = value
    return environment


def controller_source_pins(managed_root: Path) -> dict[str, Any]:
    manifest_path = managed_root / "install-bundle.manifest.json"
    required_paths = (
        "scripts/agent/case_state.py",
        "scripts/agent/case_app_server_controller.py",
        "scripts/agent/case_runtime_supervisor.py",
        "scripts/agent/case_runtime_broker.py",
    )
    if (not manifest_path.is_file() or manifest_path.is_symlink()
            or path_contains_link_or_reparse(manifest_path, stop=managed_root)):
        raise StoreCorruptionError("controller install bundle manifest must be a regular direct file")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StoreCorruptionError(f"controller install bundle manifest is invalid: {exc}") from exc
    entries = manifest.get("entries") if isinstance(manifest, Mapping) else None
    if not isinstance(entries, list):
        raise StoreCorruptionError("controller install bundle manifest entries are unavailable")
    indexed = {
        entry.get("path"): entry
        for entry in entries
        if isinstance(entry, Mapping) and isinstance(entry.get("path"), str)
    }
    pins: list[dict[str, Any]] = []
    for relative in required_paths:
        entry = indexed.get(relative)
        source_path = managed_root.joinpath(*PurePosixPath(relative).parts)
        if not isinstance(entry, Mapping) or not source_path.is_file() or source_path.is_symlink():
            raise StoreCorruptionError(f"controller source is absent from bundle manifest: {relative}")
        observed_sha256 = file_sha256(source_path)
        observed_size = source_path.stat().st_size
        if (entry.get("sha256") != observed_sha256 or entry.get("size") != observed_size):
            raise StoreCorruptionError(f"controller source differs from bundle manifest: {relative}")
        pins.append({"path": relative, "sha256": observed_sha256, "size": observed_size})
    return {
        "manifest_path": "install-bundle.manifest.json",
        "manifest_sha256": file_sha256(manifest_path),
        "files": pins,
    }


def proposal_broker_source_pins(managed_root: Path) -> dict[str, Any]:
    """Pin only the actorless proposal boundary and its canonical engine."""
    manifest_path = managed_root / "install-bundle.manifest.json"
    required_paths = (
        "scripts/agent/case_state.py",
        "scripts/agent/case_runtime_broker.py",
        "scripts/agent/case_proposal_action_broker.py",
    )
    if (
        not manifest_path.is_file()
        or manifest_path.is_symlink()
        or path_contains_link_or_reparse(manifest_path, stop=managed_root)
    ):
        raise StoreCorruptionError(
            "proposal broker install bundle manifest must be a regular direct file"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StoreCorruptionError(
            f"proposal broker install bundle manifest is invalid: {exc}"
        ) from exc
    entries = manifest.get("entries") if isinstance(manifest, Mapping) else None
    if not isinstance(entries, list):
        raise StoreCorruptionError(
            "proposal broker install bundle manifest entries are unavailable"
        )
    indexed = {
        entry.get("path"): entry
        for entry in entries
        if isinstance(entry, Mapping) and isinstance(entry.get("path"), str)
    }
    pins: list[dict[str, Any]] = []
    for relative in required_paths:
        entry = indexed.get(relative)
        source_path = managed_root.joinpath(*PurePosixPath(relative).parts)
        if (
            not isinstance(entry, Mapping)
            or not source_path.is_file()
            or source_path.is_symlink()
        ):
            raise StoreCorruptionError(
                f"proposal broker source is absent from bundle manifest: {relative}"
            )
        observed_sha256 = file_sha256(source_path)
        observed_size = source_path.stat().st_size
        if (
            entry.get("sha256") != observed_sha256
            or entry.get("size") != observed_size
        ):
            raise StoreCorruptionError(
                f"proposal broker source differs from bundle manifest: {relative}"
            )
        pins.append(
            {"path": relative, "sha256": observed_sha256, "size": observed_size}
        )
    return {
        "manifest_path": "install-bundle.manifest.json",
        "manifest_sha256": file_sha256(manifest_path),
        "files": pins,
    }


def case_record_sha256(case: Mapping[str, Any]) -> str:
    return canonical_json_sha256(case)


def require_utc_timestamp(value: Any, label: str) -> str:
    raw = _nonempty(value, label, 64)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise ValidationError(f"{label} must use UTC")
    return parsed.replace(microsecond=0).isoformat()


def require_windows_sid(value: Any, label: str) -> str:
    raw = _nonempty(value, label, 184).upper()
    if not WINDOWS_SID_PATTERN.fullmatch(raw):
        raise ValidationError(f"{label} must be a canonical Windows SID")
    return raw


def normalize_action_path(value: Any) -> str:
    raw = unicodedata.normalize("NFC", _nonempty(value, "action target path", 1024)).replace("\\", "/")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or raw != pure.as_posix() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValidationError("action target path must be a canonical repository-relative path")
    if ":" in pure.parts[0] or raw.startswith("//"):
        raise ValidationError("action target path must not contain a drive or UNC prefix")
    return pure.as_posix()


def require_stable_id(value: Any, label: str) -> str:
    raw = _nonempty(value, label, 128)
    if not FINDING_ID_PATTERN.fullmatch(raw):
        raise ValidationError(
            f"{label} must use letters, numbers, dot, underscore, colon, or hyphen"
        )
    return raw


def _normalize_live_controller_capability(raw: Any) -> dict[str, Any]:
    expected = {
        "client_capabilities", "mcp_server_count", "hook_count", "dynamic_tools"
    }
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise ValidationError("live controller capability evidence uses an unexpected schema")
    normalized = {
        "client_capabilities": copy.deepcopy(raw.get("client_capabilities")),
        "mcp_server_count": raw.get("mcp_server_count"),
        "hook_count": raw.get("hook_count"),
        "dynamic_tools": copy.deepcopy(raw.get("dynamic_tools")),
    }
    if normalized != {
        "client_capabilities": {"experimentalApi": True},
        "mcp_server_count": 0,
        "hook_count": 0,
        "dynamic_tools": [],
    }:
        raise AuthorizationError("live controller reported an enabled capability bypass")
    return normalized


def _normalize_live_controller_transport_audit(
    raw: Any,
    *,
    phase: str,
    worker_sid: str,
    broker_sid: str,
    app_server_sha256: str,
    app_server_version: str,
    environment_sha256: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not 3 <= len(raw) <= 4096:
        raise ValidationError("live controller transport audit has an invalid record count")
    normalized: list[dict[str, Any]] = []
    launch_count = identity_count = closure_count = 0
    process_instance_sha256: str | None = None
    observed_client_methods: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValidationError("live controller transport audit record is not an object")
        event = item.get("event")
        if event == "app_server_launch":
            expected = {
                "event", "sandbox_launcher_command_sha256", "app_server_command_sha256",
                "environment_evidence", "controller_key_exposed",
                "controller_principal_sid", "controller_principal_matches_broker",
                "mcp_override", "shell_environment_inherit",
            }
            if set(item) != expected:
                raise ValidationError("live controller launch audit uses an unexpected schema")
            environment = item.get("environment_evidence")
            environment_fields = {
                "environment_names", "environment_values_sha256", "mutable_paths_sha256",
                "path_entries_sha256", "controller_key_exposed",
                "secret_like_name_count", "mutable_paths_within_worker_root",
                "evidence_sha256",
            }
            if not isinstance(environment, Mapping) or set(environment) != environment_fields:
                raise ValidationError("live controller environment evidence is malformed")
            environment_body = {
                name: copy.deepcopy(value)
                for name, value in environment.items()
                if name != "evidence_sha256"
            }
            if (
                sorted(environment.get("environment_names", []))
                != sorted(LIVE_CONTROLLER_ENVIRONMENT_NAMES)
                or environment.get("environment_values_sha256") != environment_sha256
                or environment.get("controller_key_exposed") is not False
                or environment.get("secret_like_name_count") != 0
                or environment.get("mutable_paths_within_worker_root") is not True
                or require_snapshot_hash(str(environment.get("evidence_sha256", "")))
                != canonical_json_sha256(environment_body)
            ):
                raise AuthorizationError("live controller environment evidence permits a bypass")
            require_snapshot_hash(str(environment.get("mutable_paths_sha256", "")))
            require_snapshot_hash(str(environment.get("path_entries_sha256", "")))
            if (
                item.get("controller_key_exposed") is not False
                or require_windows_sid(
                    item.get("controller_principal_sid"),
                    "live controller principal SID",
                )
                != broker_sid
                or item.get("controller_principal_matches_broker") is not True
                or item.get("mcp_override") != "empty"
                or item.get("shell_environment_inherit") != "none"
            ):
                raise AuthorizationError("live controller launch boundary differs from the sealed run")
            require_snapshot_hash(str(item.get("sandbox_launcher_command_sha256", "")))
            require_snapshot_hash(str(item.get("app_server_command_sha256", "")))
            launch_count += 1
        elif event == "restricted_app_server_identity":
            expected = {
                "event", "process_instance_sha256", "worker_principal_sid",
                "principal_distinct_from_broker", "app_server_sha256",
                "app_server_version", "command_line_sha256", "argv_sha256",
                "argv_matches_sealed_command", "worker_environment_acl_evidence",
                "kill_on_job_close",
            }
            if set(item) != expected:
                raise ValidationError("live controller identity audit uses an unexpected schema")
            acl_evidence = item.get("worker_environment_acl_evidence")
            if (
                require_windows_sid(
                    item.get("worker_principal_sid"),
                    "live App Server worker SID",
                )
                != worker_sid
                or item.get("principal_distinct_from_broker") is not True
                or item.get("app_server_sha256") != app_server_sha256
                or item.get("app_server_version") != app_server_version
                or item.get("argv_matches_sealed_command") is not True
                or item.get("kill_on_job_close") is not True
                or not isinstance(acl_evidence, Mapping)
                or not acl_evidence
                or len(canonical_json_bytes(acl_evidence)) > 262144
            ):
                raise AuthorizationError("live App Server process identity evidence is invalid")
            require_snapshot_hash(str(item.get("command_line_sha256", "")))
            require_snapshot_hash(str(item.get("argv_sha256", "")))
            process_instance_sha256 = require_snapshot_hash(
                str(item.get("process_instance_sha256", ""))
            )
            identity_count += 1
        elif event == "client_request":
            expected = {
                "event", "method", "allowlisted", "shell_command_requested",
            }
            if set(item) != expected:
                raise ValidationError("live controller client request audit is malformed")
            method = str(item.get("method", ""))
            if (
                method not in LIVE_CONTROLLER_CLIENT_METHODS
                or item.get("allowlisted") is not True
                or item.get("shell_command_requested") is not False
            ):
                raise AuthorizationError("live controller client request exposes a bypass")
            observed_client_methods.add(method)
        elif event == "server_request":
            if set(item) != {"event", "method", "outcome"}:
                raise ValidationError("live controller server request audit is malformed")
            method = str(item.get("method", ""))
            outcome = str(item.get("outcome", ""))
            if method in LIVE_CONTROLLER_MUTATION_APPROVAL_METHODS:
                expected_outcome = "DECLINED"
            elif method == "item/permissions/requestApproval":
                expected_outcome = "NO_PERMISSIONS_TURN_ONLY"
            else:
                raise AuthorizationError(
                    "successful live controller evidence contains a fatal server request"
                )
            if outcome != expected_outcome:
                raise AuthorizationError("live controller server request was not denied exactly")
        elif event == "app_server_process_tree_closed":
            if set(item) != {
                "event", "process_instance_sha256", "kill_on_job_close",
                "descendant_exit_verified"
            }:
                raise ValidationError("live controller closure audit is malformed")
            if (
                item.get("kill_on_job_close") is not True
                or item.get("descendant_exit_verified") is not True
            ):
                raise AuthorizationError("live App Server process tree did not close")
            closure_process_sha256 = require_snapshot_hash(
                str(item.get("process_instance_sha256", ""))
            )
            if (
                process_instance_sha256 is not None
                and closure_process_sha256 != process_instance_sha256
            ):
                raise AuthorizationError("live App Server closure PID differs from launch")
            closure_count += 1
        else:
            raise AuthorizationError("live controller audit contains an unknown event")
        normalized.append(copy.deepcopy(dict(item)))
    required_methods = (
        LIVE_CONTROLLER_INITIAL_REQUIRED_METHODS
        if phase == "initial"
        else LIVE_CONTROLLER_RESTART_REQUIRED_METHODS
    )
    if (
        phase not in {"initial", "restart"}
        or launch_count != 1
        or identity_count != 1
        or closure_count != 1
        or normalized[-1].get("event") != "app_server_process_tree_closed"
        or not required_methods.issubset(observed_client_methods)
        or b"acceptForSession" in canonical_json_bytes(normalized)
    ):
        raise AuthorizationError("live controller audit is incomplete or enables session approval")
    return normalized


def normalize_live_controller_evidence(
    raw: Any,
    *,
    worker_sid: str,
    broker_sid: str,
    app_server_sha256: str,
    app_server_version: str,
    environment_sha256: str,
    expected_case_id: str | None = None,
) -> dict[str, Any]:
    expected = {
        "protocol_version", "schema_version", "case_id", "initial_run",
        "restart_run", "incomplete_child_evidence", "stale_revision_denial",
        "all_mutation_surfaces_disabled", "both_process_trees_closed",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise ValidationError("live controller evidence uses an unexpected schema")
    if (
        raw.get("protocol_version") != LIVE_CONTROLLER_EVIDENCE_PROTOCOL_VERSION
        or raw.get("schema_version") != 1
        or raw.get("all_mutation_surfaces_disabled") is not True
        or raw.get("both_process_trees_closed") is not True
    ):
        raise AuthorizationError("live controller evidence does not prove the fixed boundary")
    case_id = canonical_case_id(str(raw.get("case_id", "")))
    if expected_case_id is not None and case_id != canonical_case_id(expected_case_id):
        raise AuthorizationError("live controller evidence is bound to another case")
    normalized_runs: dict[str, dict[str, Any]] = {}
    for field, phase in (("initial_run", "initial"), ("restart_run", "restart")):
        run = raw.get(field)
        if not isinstance(run, Mapping) or set(run) != {
            "capability_evidence", "capability_evidence_sha256",
            "transport_audit", "transport_audit_sha256", "process_tree_closed",
            "server_request_count", "server_request_policy_status",
        }:
            raise ValidationError("live controller run evidence uses an unexpected schema")
        capability = _normalize_live_controller_capability(
            run.get("capability_evidence")
        )
        if require_snapshot_hash(str(run.get("capability_evidence_sha256", ""))) != canonical_json_sha256(capability):
            raise AuthorizationError("live controller capability digest is invalid")
        audit = _normalize_live_controller_transport_audit(
            run.get("transport_audit"),
            phase=phase,
            worker_sid=worker_sid,
            broker_sid=broker_sid,
            app_server_sha256=app_server_sha256,
            app_server_version=app_server_version,
            environment_sha256=environment_sha256,
        )
        audit_sha256 = require_snapshot_hash(str(run.get("transport_audit_sha256", "")))
        server_request_count = sum(
            item.get("event") == "server_request" for item in audit
        )
        expected_policy_status = (
            "EXERCISED_NON_AUTHORIZING"
            if server_request_count
            else "UNEXERCISED"
        )
        if (
            audit_sha256 != canonical_json_sha256(audit)
            or run.get("process_tree_closed") is not True
            or run.get("server_request_count") != server_request_count
            or run.get("server_request_policy_status") != expected_policy_status
        ):
            raise AuthorizationError("live controller transport digest or closure is invalid")
        normalized_runs[field] = {
            "capability_evidence": capability,
            "capability_evidence_sha256": canonical_json_sha256(capability),
            "transport_audit": audit,
            "transport_audit_sha256": audit_sha256,
            "process_tree_closed": True,
            "server_request_count": server_request_count,
            "server_request_policy_status": expected_policy_status,
        }
    incomplete = raw.get("incomplete_child_evidence")
    incomplete_fields = {
        "thread_id", "turn_id", "completion_state", "proposal_count",
        "action_count", "result_sha256", "evidence_sha256",
    }
    if not isinstance(incomplete, Mapping) or set(incomplete) != incomplete_fields:
        raise ValidationError("incomplete child evidence uses an unexpected schema")
    incomplete_body = {
        name: copy.deepcopy(value)
        for name, value in incomplete.items()
        if name != "evidence_sha256"
    }
    if (
        incomplete.get("completion_state") != "INCOMPLETE"
        or incomplete.get("proposal_count") != 0
        or incomplete.get("action_count") != 0
        or require_snapshot_hash(str(incomplete.get("result_sha256", ""))) == EMPTY_SHA256
        or require_snapshot_hash(str(incomplete.get("evidence_sha256", "")))
        != canonical_json_sha256(incomplete_body)
    ):
        raise AuthorizationError("incomplete child evidence is not exact")
    incomplete_body["thread_id"] = normalize_binding(
        "thread", str(incomplete.get("thread_id", ""))
    )
    incomplete_body["turn_id"] = require_stable_id(
        incomplete.get("turn_id"), "incomplete child turn id"
    )
    normalized_incomplete = {
        **incomplete_body,
        "evidence_sha256": canonical_json_sha256(incomplete_body),
    }
    stale = raw.get("stale_revision_denial")
    stale_fields = {
        "denial", "attempted_revision", "current_revision",
        "authority_state_sha256_before", "authority_state_sha256_after",
        "state_unchanged",
    }
    if not isinstance(stale, Mapping) or set(stale) != stale_fields:
        raise ValidationError("stale revision denial evidence uses an unexpected schema")
    attempted = stale.get("attempted_revision")
    current = stale.get("current_revision")
    if (
        stale.get("denial") != "RevisionConflict"
        or isinstance(attempted, bool)
        or not isinstance(attempted, int)
        or attempted < 0
        or isinstance(current, bool)
        or not isinstance(current, int)
        or current != attempted + 1
        or require_snapshot_hash(str(stale.get("authority_state_sha256_before", "")))
        != require_snapshot_hash(str(stale.get("authority_state_sha256_after", "")))
        or stale.get("state_unchanged") is not True
    ):
        raise AuthorizationError("stale revision denial evidence is invalid")
    return {
        "protocol_version": LIVE_CONTROLLER_EVIDENCE_PROTOCOL_VERSION,
        "schema_version": 1,
        "case_id": case_id,
        **normalized_runs,
        "incomplete_child_evidence": normalized_incomplete,
        "stale_revision_denial": copy.deepcopy(dict(stale)),
        "all_mutation_surfaces_disabled": True,
        "both_process_trees_closed": True,
    }


def normalized_absolute_path(
    value: Any,
    label: str,
    *,
    must_exist: bool = True,
    reject_links: bool = False,
) -> tuple[Path, str]:
    raw = _nonempty(value, label, 4096)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValidationError(f"{label} must be absolute")
    if reject_links and path_contains_link_or_reparse(candidate):
        raise AuthorizationError(f"{label} must not traverse a link or reparse point")
    try:
        resolved = candidate.resolve(strict=must_exist)
    except OSError as exc:
        raise ValidationError(f"{label} cannot be resolved: {exc}") from exc
    normalized = normalize_binding("worktree", str(resolved))
    return resolved, normalized


def path_contains_link_or_reparse(path: Path, *, stop: Path | None = None) -> bool:
    """Return true if an existing path component is a symlink or Windows reparse point."""
    current = path
    floor = stop.resolve(strict=False) if stop is not None else None
    components: list[Path] = []
    while True:
        components.append(current)
        if floor is not None and current == floor:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    for component in reversed(components):
        try:
            metadata = component.lstat()
        except OSError:
            continue
        if component.is_symlink():
            return True
        attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if attributes & reparse_flag:
            return True
    return False


def _windows_regular_file_identity(path: Path) -> tuple[int, int, int, int]:
    import ctypes
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(ByHandleFileInformation)
    ]
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    native_path = str(path.resolve(strict=True))
    if native_path.startswith("\\\\"):
        native_path = "\\\\?\\UNC\\" + native_path[2:]
    elif not native_path.startswith("\\\\?\\"):
        native_path = "\\\\?\\" + native_path
    handle = create_file(
        native_path,
        0,
        0x1 | 0x2 | 0x4,
        None,
        3,
        0x80,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise ValidationError(
            f"cannot open stable Windows file identity: {ctypes.get_last_error()}"
        )
    try:
        information = ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(information)):
            raise ValidationError(
                "cannot inspect stable Windows file identity: "
                f"{ctypes.get_last_error()}"
            )
    finally:
        close_handle(handle)
    file_id = (
        int(information.file_index_high) << 32
    ) | int(information.file_index_low)
    file_size = (
        int(information.file_size_high) << 32
    ) | int(information.file_size_low)
    return (
        int(information.volume_serial_number),
        file_id,
        int(information.number_of_links),
        file_size,
    )


def regular_file_identity(path: Path, *, stop: Path | None = None) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValidationError(f"cannot inspect stable file identity: {exc}") from exc
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or attributes & reparse_flag
        or path_contains_link_or_reparse(path, stop=stop)
    ):
        raise AuthorizationError("authorized file must be regular and must not traverse a reparse point")
    if os.name == "nt":
        volume_id, file_id, link_count, file_size = (
            _windows_regular_file_identity(path)
        )
    else:
        link_count = int(metadata.st_nlink)
        file_id = int(metadata.st_ino)
        volume_id = int(metadata.st_dev)
        file_size = int(metadata.st_size)
    if link_count != 1:
        raise AuthorizationError("authorized file must have exactly one hard link")
    if file_id <= 0:
        raise AuthorizationError("filesystem did not provide a stable nonzero file identity")
    identity = {
        "volume_id": volume_id,
        "file_id": file_id,
        "number_of_links": link_count,
        "size": file_size,
    }
    return {**identity, "identity_sha256": canonical_json_sha256(identity)}


def normalize_repo_url(value: str) -> str:
    raw = unicodedata.normalize("NFC", _nonempty(value, "repo_url", 2048)).replace("\\", "/")
    scp = re.fullmatch(r"(?:[^@/]+@)?([^:/]+):(.+)", raw)
    if scp and "://" not in raw:
        host, path = scp.groups()
        port = None
    else:
        candidate = raw if "://" in raw else f"https://{raw}"
        parsed = urllib.parse.urlsplit(candidate)
        host = parsed.hostname or ""
        path = parsed.path
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValidationError("repo_url contains an invalid port") from exc
    host = host.lower().rstrip(".")
    if port is not None and port not in {22, 80, 443}:
        host = f"{host}:{port}"
    path = urllib.parse.unquote(path).strip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    path = re.sub(r"/+", "/", path).casefold()
    if not host or not path or "/" not in path:
        raise ValidationError("repo_url must identify a host and repository path")
    return f"https://{host}/{path}"


def normalize_binding(kind: str, value: str) -> str:
    if kind not in BINDING_KINDS:
        raise ValidationError(f"binding kind must be one of: {', '.join(sorted(BINDING_KINDS))}")
    raw = unicodedata.normalize("NFC", _nonempty(value, kind, 4096))
    if kind == "repo_url":
        return normalize_repo_url(raw)
    if kind == "branch":
        while raw.startswith("refs/heads/"):
            raw = raw[len("refs/heads/") :]
        if raw.startswith("/") or raw.endswith("/") or ".." in raw.split("/"):
            raise ValidationError("branch binding is malformed")
        return raw
    if kind == "worktree":
        resolved = Path(raw).expanduser().resolve(strict=False)
        normalized = unicodedata.normalize("NFC", resolved.as_posix())
        if re.match(r"^[A-Z]:/", normalized):
            normalized = normalized[0].lower() + normalized[1:]
        return os.path.normcase(normalized).replace("\\", "/")
    if kind == "pr":
        match = re.fullmatch(r"(.+?)#([1-9][0-9]*)", raw)
        if not match:
            raise ValidationError("pr binding must use <repo-url-or-host/path>#<positive-number>")
        return f"{normalize_repo_url(match.group(1))}#{int(match.group(2))}"
    if kind == "thread":
        return raw.casefold()
    return raw.casefold()


def _binding_key(kind: str, value: str, *, repository: str | None = None) -> str:
    normalized = normalize_binding(kind, value)
    if kind == "repo_url":
        raise ValidationError("repository associations are nonexclusive and do not have one binding owner")
    if kind == "branch":
        if repository is None:
            raise ValidationError("branch binding requires repository")
        return f"{kind}\0{normalize_repo_url(repository)}\0{normalized}"
    if repository is not None:
        raise ValidationError(f"repository qualifier is valid only for branch bindings, not {kind}")
    return f"{kind}\0{normalized}"


def _binding_record(kind: str, value: str, *, repository: str | None = None) -> dict[str, str]:
    normalized = normalize_binding(kind, value)
    record = {"kind": kind, "value": normalized}
    if kind == "branch":
        if repository is None:
            raise ValidationError("branch binding requires repository")
        record["repository"] = normalize_repo_url(repository)
    elif repository is not None:
        raise ValidationError(f"repository qualifier is valid only for branch bindings, not {kind}")
    return record


def _fingerprint(operation: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"operation": operation, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class FileLock:
    """Exclusive process lock using the native standard-library OS primitive."""

    def __init__(self, path: Path, timeout: float = 30.0) -> None:
        self.path = path
        self.timeout = timeout
        self.handle: Any = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
            os.fsync(self.handle.fileno())
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt

            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.handle.close()
                        raise TimeoutError(f"timed out acquiring case-state lock: {self.path}")
                    time.sleep(0.05)
        elif os.name == "posix":
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        else:
            self.handle.close()
            raise RuntimeError(f"unsupported locking platform: {os.name}")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        assert self.handle is not None
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def _initial_store() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "cases": {},
        "bindings": {},
        "store_events": {},
    }


def _new_case(case_id: str, objective: str) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "revision": 1,
        "state": "REGISTERED",
        "resumable_state": None,
        "objective": _nonempty(objective, "objective"),
        "created_at": now,
        "updated_at": now,
        "bindings": {kind: [] for kind in sorted(BINDING_KINDS)},
        "limits": {
            "implementation_generations": 0,
            "review_cohorts": 0,
            "combined_repairs": 0,
            "closure_checks": 0,
            "operational_retries": 0,
        },
        "candidate": {
            "review_heads": {},
            "review_snapshots": {},
            "current_heads": {},
            "repaired_heads": {},
            "repaired_snapshots": {},
            "observed_heads": {},
        },
        "review": {"cohort": None, "receipts": {}},
        "findings": {"items": [], "late": [], "frozen": False, "frozen_ids": []},
        "repair": {"authorized_ids": [], "authority": None, "addressed_ids": []},
        "closure": {"preflight": None, "resolutions": None},
        "control": {"active_failure": None, "history": []},
        "runtime": {"actors": {}, "action_grants": {}},
        "runtime_generation_attempt": None,
        "runtime_generation_abort": None,
        "terminal_quarantine": None,
        "lock_reason": None,
        "events": {},
    }


def _validate_additive_case_records(case_id: str, case: Mapping[str, Any]) -> None:
    """Validate v2 extension records without requiring or backfilling legacy cases."""
    review = case.get("review")
    if review is not None:
        if not isinstance(review, dict) or set(review) != {"cohort", "receipts"}:
            raise StoreCorruptionError(f"case {case_id} review record has invalid fields")
        cohort = review["cohort"]
        if cohort is not None:
            required = {
                "protocol_version", "schema_version", "cohort_id", "required_receipt",
                "reviewers", "declared_at", "cohort_sha256",
            }
            if not isinstance(cohort, dict) or set(cohort) != required:
                raise StoreCorruptionError(f"case {case_id} review cohort has invalid fields")
            body = {name: cohort[name] for name in required - {"declared_at", "cohort_sha256"}}
            if (cohort.get("protocol_version") != REVIEW_COHORT_PROTOCOL_VERSION
                    or cohort.get("schema_version") != 1
                    or not isinstance(cohort.get("reviewers"), list)
                    or not cohort["reviewers"]
                    or cohort.get("cohort_sha256") != canonical_json_sha256(body)):
                raise StoreCorruptionError(f"case {case_id} review cohort is invalid")
            try:
                require_utc_timestamp(cohort["declared_at"], "declared_at")
            except ValidationError as exc:
                raise StoreCorruptionError(f"case {case_id} review cohort timestamp is invalid") from exc
        receipts = review["receipts"]
        if not isinstance(receipts, dict):
            raise StoreCorruptionError(f"case {case_id} review receipts must be an object")
        for reviewer_id, receipt in receipts.items():
            if not isinstance(receipt, dict) or receipt.get("reviewer_id") != reviewer_id:
                raise StoreCorruptionError(f"case {case_id} review receipt identity is invalid")
            digest = receipt.get("receipt_sha256")
            body = {name: value for name, value in receipt.items() if name != "receipt_sha256"}
            if digest != canonical_json_sha256(body):
                raise StoreCorruptionError(f"case {case_id} review receipt digest is invalid")
    runtime = case.get("runtime")
    if runtime is not None:
        if not isinstance(runtime, dict) or set(runtime) != {"actors", "action_grants"}:
            raise StoreCorruptionError(f"case {case_id} runtime record has invalid fields")
        if not isinstance(runtime["actors"], dict) or not isinstance(runtime["action_grants"], dict):
            raise StoreCorruptionError(f"case {case_id} runtime maps must be objects")
        if len(runtime["action_grants"]) > 1:
            raise StoreCorruptionError(f"case {case_id} exceeds its one runtime action grant limit")
        for thread_id, actor in runtime["actors"].items():
            if (not isinstance(actor, dict) or actor.get("thread_id") != thread_id
                    or actor.get("role") not in RUNTIME_ACTOR_ROLES):
                raise StoreCorruptionError(f"case {case_id} runtime actor is invalid")
            digest = actor.get("actor_sha256")
            body = {name: value for name, value in actor.items() if name != "actor_sha256"}
            if digest != canonical_json_sha256(body):
                raise StoreCorruptionError(f"case {case_id} runtime actor digest is invalid")
        for grant_id, grant in runtime["action_grants"].items():
            if (not isinstance(grant, dict) or grant.get("grant_id") != grant_id
                    or grant.get("status") not in ACTION_GRANT_STATUSES):
                raise StoreCorruptionError(f"case {case_id} action grant is invalid")
            digest = grant.get("grant_sha256")
            body = {name: value for name, value in grant.items() if name != "grant_sha256"}
            if digest != canonical_json_sha256(body):
                raise StoreCorruptionError(f"case {case_id} action grant digest is invalid")
    quarantine = case.get("terminal_quarantine")
    if quarantine is not None:
        if not isinstance(quarantine, dict) or quarantine.get("protocol_version") != TERMINAL_QUARANTINE_PROTOCOL_VERSION:
            raise StoreCorruptionError(f"case {case_id} terminal quarantine is invalid")
        digest = quarantine.get("record_sha256")
        body = {name: value for name, value in quarantine.items() if name != "record_sha256"}
        if digest != canonical_json_sha256(body):
            raise StoreCorruptionError(f"case {case_id} terminal quarantine digest is invalid")
    generation_abort = case.get("runtime_generation_abort")
    if generation_abort is not None:
        if (
            not isinstance(generation_abort, dict)
            or generation_abort.get("protocol_version")
            != RUNTIME_GENERATION_ABORT_PROTOCOL_VERSION
        ):
            raise StoreCorruptionError(f"case {case_id} runtime generation abort is invalid")
        digest = generation_abort.get("record_sha256")
        body = {
            name: value
            for name, value in generation_abort.items()
            if name != "record_sha256"
        }
        if digest != canonical_json_sha256(body):
            raise StoreCorruptionError(
                f"case {case_id} runtime generation abort digest is invalid"
            )
    generation_attempt = case.get("runtime_generation_attempt")
    if generation_attempt is not None:
        expected = {
            "protocol_version", "schema_version", "attempt_id", "grant_id",
            "controller_spec_sha256", "status", "claimed_at", "finalized_at",
            "abort_reason_code", "record_sha256",
        }
        if not isinstance(generation_attempt, dict) or set(generation_attempt) != expected:
            raise StoreCorruptionError(
                f"case {case_id} runtime generation attempt has invalid fields"
            )
        if (
            generation_attempt.get("protocol_version")
            != RUNTIME_GENERATION_ATTEMPT_PROTOCOL_VERSION
            or generation_attempt.get("schema_version") != 1
            or generation_attempt.get("status")
            not in {"CLAIMED", "GRANT_ISSUED", "ABORTED"}
        ):
            raise StoreCorruptionError(
                f"case {case_id} runtime generation attempt is invalid"
            )
        try:
            require_stable_id(generation_attempt.get("attempt_id"), "attempt id")
            require_stable_id(generation_attempt.get("grant_id"), "grant id")
            require_snapshot_hash(str(generation_attempt.get("controller_spec_sha256", "")))
            require_utc_timestamp(generation_attempt.get("claimed_at"), "claimed_at")
            status = generation_attempt["status"]
            if status == "CLAIMED":
                if (
                    generation_attempt.get("finalized_at") is not None
                    or generation_attempt.get("abort_reason_code") is not None
                ):
                    raise ValidationError("claimed runtime generation attempt is finalized")
            else:
                require_utc_timestamp(generation_attempt.get("finalized_at"), "finalized_at")
                expected_reason = (
                    "CONTROLLER_GENERATION_ABANDONED" if status == "ABORTED" else None
                )
                if generation_attempt.get("abort_reason_code") != expected_reason:
                    raise ValidationError("runtime generation attempt final reason is invalid")
        except ValidationError as exc:
            raise StoreCorruptionError(
                f"case {case_id} runtime generation attempt is invalid"
            ) from exc
        digest = generation_attempt.get("record_sha256")
        body = {
            name: value
            for name, value in generation_attempt.items()
            if name != "record_sha256"
        }
        if digest != canonical_json_sha256(body):
            raise StoreCorruptionError(
                f"case {case_id} runtime generation attempt digest is invalid"
            )


def _validate_store(data: Any) -> None:
    if not isinstance(data, dict):
        raise StoreCorruptionError("case-state store must be a JSON object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise StoreCorruptionError(
            f"unsupported case-state schema_version: {data.get('schema_version')!r}; expected {SCHEMA_VERSION}"
        )
    if not isinstance(data.get("revision"), int) or data["revision"] < 0:
        raise StoreCorruptionError("store revision must be a non-negative integer")
    for key in ("cases", "bindings", "store_events"):
        if not isinstance(data.get(key), dict):
            raise StoreCorruptionError(f"store {key} must be an object")
    for case_id, case in data["cases"].items():
        try:
            canonical_case_id(case_id)
        except ValidationError as exc:
            raise StoreCorruptionError(str(exc)) from exc
        if not isinstance(case, dict) or case.get("case_id") != case_id:
            raise StoreCorruptionError(f"case record identity mismatch: {case_id}")
        if case.get("schema_version") != SCHEMA_VERSION:
            raise StoreCorruptionError(f"case {case_id} has unsupported schema_version")
        if case.get("state") not in CASE_STATES:
            raise StoreCorruptionError(f"case {case_id} has invalid state")
        if not isinstance(case.get("revision"), int) or case["revision"] < 1:
            raise StoreCorruptionError(f"case {case_id} revision must be a positive integer")
        limits = case.get("limits")
        if not isinstance(limits, dict):
            raise StoreCorruptionError(f"case {case_id} limits must be an object")
        for name in (
            "implementation_generations",
            "review_cohorts",
            "combined_repairs",
            "closure_checks",
            "operational_retries",
        ):
            if not isinstance(limits.get(name), int) or limits[name] < 0:
                raise StoreCorruptionError(f"case {case_id} limit {name} must be a non-negative integer")
        if limits["implementation_generations"] > 1:
            raise StoreCorruptionError(f"case {case_id} exceeds the implementation generation limit")
        if limits["review_cohorts"] > 1:
            raise StoreCorruptionError(f"case {case_id} exceeds the review cohort limit")
        if limits["combined_repairs"] > 1:
            raise StoreCorruptionError(f"case {case_id} exceeds the combined repair limit")
        if limits["closure_checks"] > 1:
            raise StoreCorruptionError(f"case {case_id} exceeds the closure check limit")
        if limits["operational_retries"] > 1:
            raise StoreCorruptionError(f"case {case_id} exceeds the operational retry limit")
        for required in ("candidate", "findings", "repair", "closure", "control", "events", "bindings"):
            if not isinstance(case.get(required), dict):
                raise StoreCorruptionError(f"case {case_id} {required} must be an object")
        _validate_additive_case_records(case_id, case)
        bindings = case["bindings"]
        if set(bindings) != BINDING_KINDS:
            raise StoreCorruptionError(f"case {case_id} bindings must contain every canonical binding kind")
        for kind, values in bindings.items():
            if not isinstance(values, list):
                raise StoreCorruptionError(f"case {case_id} binding list {kind} must be an array")
            if kind == "branch":
                for record in values:
                    if not isinstance(record, dict) or set(record) != {"repository", "value"}:
                        raise StoreCorruptionError(
                            f"case {case_id} branch bindings must contain repository and value"
                        )
                    try:
                        repository = normalize_repo_url(record["repository"])
                        branch = normalize_binding("branch", record["value"])
                    except (ValidationError, TypeError) as exc:
                        raise StoreCorruptionError(f"case {case_id} has an invalid branch binding") from exc
                    if record != {"repository": repository, "value": branch}:
                        raise StoreCorruptionError(f"case {case_id} has a noncanonical branch binding")
                branch_keys = [(item["repository"], item["value"]) for item in values]
                if len(branch_keys) != len(set(branch_keys)):
                    raise StoreCorruptionError(f"case {case_id} branch bindings must be unique")
                if values != sorted(values, key=lambda item: (item["repository"], item["value"])):
                    raise StoreCorruptionError(f"case {case_id} branch bindings are not sorted")
                continue
            for value in values:
                try:
                    normalized = normalize_binding(kind, value)
                except (ValidationError, TypeError) as exc:
                    raise StoreCorruptionError(f"case {case_id} has an invalid {kind} binding") from exc
                if value != normalized:
                    raise StoreCorruptionError(f"case {case_id} has a noncanonical {kind} binding")
            if values != sorted(set(values)):
                raise StoreCorruptionError(f"case {case_id} {kind} bindings must be sorted and unique")
        for record in bindings["branch"]:
            key = _binding_key(
                "branch", record["value"], repository=record["repository"]
            )
            if data["bindings"].get(key) != case_id:
                raise StoreCorruptionError(
                    f"case {case_id} branch binding is missing from the exclusive registry"
                )
        for kind in EXCLUSIVE_BINDING_KINDS - {"branch"}:
            for value in bindings[kind]:
                if data["bindings"].get(_binding_key(kind, value)) != case_id:
                    raise StoreCorruptionError(
                        f"case {case_id} {kind} binding is missing from the exclusive registry"
                    )
        candidate = case["candidate"]
        candidate_fields = {
            "review_heads",
            "review_snapshots",
            "current_heads",
            "repaired_heads",
            "repaired_snapshots",
            "observed_heads",
        }
        if set(candidate) != candidate_fields:
            raise StoreCorruptionError(f"case {case_id} candidate record has invalid fields")
        for field in ("review_heads", "current_heads", "repaired_heads", "observed_heads"):
            heads = candidate[field]
            if not isinstance(heads, dict):
                raise StoreCorruptionError(f"case {case_id} candidate {field} must be an object")
            for repository, head in heads.items():
                try:
                    normalized_repo = normalize_repo_url(repository)
                    normalized_head = require_sha(head)
                except (ValidationError, TypeError) as exc:
                    raise StoreCorruptionError(f"case {case_id} candidate {field} is invalid") from exc
                if repository != normalized_repo or head != normalized_head:
                    raise StoreCorruptionError(f"case {case_id} candidate {field} is noncanonical")
        for field, heads_field in (
            ("review_snapshots", "review_heads"),
            ("repaired_snapshots", "repaired_heads"),
        ):
            snapshots = candidate[field]
            expected_heads = candidate[heads_field]
            if not isinstance(snapshots, dict):
                raise StoreCorruptionError(f"case {case_id} candidate {field} must be an object")
            if set(snapshots) != set(expected_heads):
                raise StoreCorruptionError(
                    f"case {case_id} candidate {field} repositories must match {heads_field}"
                )
            for repository, record in snapshots.items():
                if not isinstance(record, Mapping):
                    raise StoreCorruptionError(
                        f"case {case_id} candidate {field} record must be an object"
                    )
                try:
                    contract = _nonempty(record.get("contract"), "snapshot contract", 100)
                    digest = require_snapshot_hash(str(record.get("sha256", "")))
                except (ValidationError, TypeError) as exc:
                    raise StoreCorruptionError(
                        f"case {case_id} candidate {field} record is invalid"
                    ) from exc
                if record.get("contract") != contract or record.get("sha256") != digest:
                    raise StoreCorruptionError(
                        f"case {case_id} candidate {field} record is noncanonical"
                    )
                if "head" not in record:
                    if field != "review_snapshots" or set(record) != {"contract", "sha256"}:
                        raise StoreCorruptionError(
                            f"case {case_id} candidate {field} snapshot head is required"
                        )
                    continue
                if set(record) != {"contract", "sha256", "head"}:
                    raise StoreCorruptionError(
                        f"case {case_id} candidate {field} record has invalid fields"
                    )
                try:
                    snapshot_head = require_sha(str(record["head"]), "snapshot head")
                except (ValidationError, TypeError) as exc:
                    raise StoreCorruptionError(
                        f"case {case_id} candidate {field} snapshot head is invalid"
                    ) from exc
                if record["head"] != snapshot_head or snapshot_head != expected_heads[repository]:
                    raise StoreCorruptionError(
                        f"case {case_id} candidate {field} snapshot head does not match {heads_field}"
                    )
    for key, case_id in data["bindings"].items():
        if not isinstance(key, str) or case_id not in data["cases"]:
            raise StoreCorruptionError("binding registry contains an invalid entry")
        parts = key.split("\0")
        kind = parts[0]
        if kind not in EXCLUSIVE_BINDING_KINDS:
            raise StoreCorruptionError("binding registry contains a nonexclusive or unknown binding kind")
        case_bindings = data["cases"][case_id]["bindings"]
        if kind == "branch":
            if len(parts) != 3:
                raise StoreCorruptionError("branch registry key must be repository-qualified")
            record = {"repository": parts[1], "value": parts[2]}
            if record not in case_bindings["branch"]:
                raise StoreCorruptionError("branch registry entry does not match its owning case")
        else:
            if len(parts) != 2 or parts[1] not in case_bindings[kind]:
                raise StoreCorruptionError("binding registry entry does not match its owning case")


class CaseStore:
    def __init__(self, state_root: Path | str | None = None) -> None:
        self.state_root = Path(state_root) if state_root is not None else default_state_root()
        self.state_root = self.state_root.expanduser().resolve(strict=False)
        managed_tree = Path(__file__).resolve().parents[2]
        if path_is_within(self.state_root, managed_tree):
            raise ValidationError(
                f"case-state data root must not be inside the managed Coding OS tree: {managed_tree}"
            )
        self.path = self.state_root / STORE_FILENAME
        self.lock_path = self.state_root / LOCK_FILENAME
        self.quarantine_audit_path = self.state_root / QUARANTINE_AUDIT_FILENAME
        self.quarantine_backup_root = self.state_root / QUARANTINE_BACKUP_DIRECTORY
        self.action_artifact_root = self.state_root / ACTION_ARTIFACT_DIRECTORY

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return _initial_store()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreCorruptionError(f"cannot read valid case-state store {self.path}: {exc}") from exc
        _validate_store(data)
        return data

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        _validate_store(data)
        self.state_root.mkdir(parents=True, exist_ok=True)
        handle, raw_path = tempfile.mkstemp(prefix=f"{STORE_FILENAME}.", suffix=".tmp", dir=self.state_root)
        temp_path = Path(raw_path)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(serialized_store_bytes(data))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
            if os.name == "posix":
                directory_fd = os.open(self.state_root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _quarantine_audit_records_unlocked(self) -> list[dict[str, Any]]:
        if not self.quarantine_audit_path.exists():
            return []
        if (self.quarantine_audit_path.is_symlink()
                or not self.quarantine_audit_path.is_file()
                or path_contains_link_or_reparse(
                    self.quarantine_audit_path, stop=self.state_root
                )):
            raise StoreCorruptionError("quarantine audit path must be a regular file")
        records: list[dict[str, Any]] = []
        previous = "0" * 64
        try:
            lines = self.quarantine_audit_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise StoreCorruptionError(f"cannot read quarantine audit: {exc}") from exc
        for sequence, line in enumerate(lines, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise StoreCorruptionError(f"quarantine audit line {sequence} is invalid JSON") from exc
            if (not isinstance(record, dict)
                    or record.get("protocol_version") != QUARANTINE_AUDIT_PROTOCOL_VERSION
                    or record.get("sequence") != sequence
                    or record.get("previous_event_sha256") != previous):
                raise StoreCorruptionError(f"quarantine audit chain mismatch at line {sequence}")
            body = {name: value for name, value in record.items() if name != "event_sha256"}
            if record.get("event_sha256") != canonical_json_sha256(body):
                raise StoreCorruptionError(f"quarantine audit digest mismatch at line {sequence}")
            previous = record["event_sha256"]
            records.append(record)
        return records

    def _append_quarantine_audit_unlocked(
        self,
        event: Mapping[str, Any],
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.state_root.mkdir(parents=True, exist_ok=True)
        if path_contains_link_or_reparse(self.quarantine_audit_path, stop=self.state_root):
            raise StoreCorruptionError("quarantine audit path must not traverse a link or reparse point")
        if self.quarantine_audit_path.exists() and (
            self.quarantine_audit_path.is_symlink() or not self.quarantine_audit_path.is_file()
        ):
            raise StoreCorruptionError("quarantine audit path must be a regular file")
        body = {
            "protocol_version": QUARANTINE_AUDIT_PROTOCOL_VERSION,
            "sequence": len(records) + 1,
            "previous_event_sha256": records[-1]["event_sha256"] if records else "0" * 64,
            **dict(event),
        }
        record = {**body, "event_sha256": canonical_json_sha256(body)}
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        descriptor = os.open(self.quarantine_audit_path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "ab", closefd=False) as stream:
                stream.write(canonical_json_bytes(record) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        records.append(record)
        return record

    def _seal_action_artifact_unlocked(
        self,
        case_id: str,
        grant_id: str,
        artifact_kind: str,
        proposal_path: Path,
        *,
        expected_size: int,
        expected_sha256: str,
    ) -> tuple[str, str, dict[str, Any]]:
        if self.state_root.exists() and path_contains_link_or_reparse(self.state_root):
            raise StoreCorruptionError("state root must not traverse a link or reparse point")
        artifact_directory = self.action_artifact_root / case_id
        artifact_directory.mkdir(parents=True, exist_ok=True)
        if path_contains_link_or_reparse(artifact_directory, stop=self.state_root):
            raise StoreCorruptionError("action artifact directory must not traverse a link or reparse point")
        raw = proposal_path.read_bytes()
        if len(raw) != expected_size or hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise AuthorizationError("proposal artifact changed before it could be sealed")
        artifact_name = hashlib.sha256(
            f"{grant_id}\0{require_stable_id(artifact_kind, 'artifact kind')}".encode("utf-8")
        ).hexdigest() + ".bin"
        artifact_path = artifact_directory / artifact_name
        if artifact_path.exists():
            if (artifact_path.is_symlink() or not artifact_path.is_file()
                    or artifact_path.read_bytes() != raw):
                raise StoreCorruptionError("existing sealed action artifact differs from the exact proposal")
        else:
            descriptor = os.open(artifact_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                os.close(descriptor)
        if file_sha256(artifact_path) != expected_sha256:
            raise StoreCorruptionError("sealed action artifact digest verification failed")
        identity = regular_file_identity(artifact_path, stop=self.state_root)
        return artifact_path.relative_to(self.state_root).as_posix(), expected_sha256, identity

    def _read(self) -> dict[str, Any]:
        return self._read_unlocked()

    def store_revision(self) -> int:
        return int(self._read()["revision"])

    def status(self) -> dict[str, Any]:
        data = self._read()
        return {
            "schema_version": data["schema_version"],
            "store_revision": data["revision"],
            "case_count": len(data["cases"]),
            "binding_count": len(data["bindings"]),
            "state_root": self.state_root.as_posix(),
        }

    def list_cases(self) -> list[dict[str, Any]]:
        data = self._read()
        return [copy.deepcopy(data["cases"][key]) for key in sorted(data["cases"])]

    def get_case(self, case_id: str) -> dict[str, Any]:
        case_id = canonical_case_id(case_id)
        data = self._read()
        if case_id not in data["cases"]:
            raise ValidationError(f"case not found: {case_id}")
        return copy.deepcopy(data["cases"][case_id])

    def register_case(
        self,
        case_id: str,
        *,
        objective: str,
        request_id: str,
        expected_store_revision: int,
    ) -> dict[str, Any]:
        case_id = canonical_case_id(case_id)
        request_id = require_request_id(request_id)
        payload = {"case_id": case_id, "objective": objective}
        fingerprint = _fingerprint("register_case", payload)
        with FileLock(self.lock_path):
            data = self._read_unlocked()
            prior = data["store_events"].get(request_id)
            if prior:
                if prior.get("fingerprint") != fingerprint:
                    raise ConflictError("request_id was already used with a different operation payload")
                result = copy.deepcopy(prior["result"])
                result["idempotent"] = True
                return result
            if expected_store_revision != data["revision"]:
                raise RevisionConflict(
                    f"expected store revision {expected_store_revision}, found {data['revision']}"
                )
            if case_id in data["cases"]:
                raise ConflictError(f"case already exists: {case_id}")
            case = _new_case(case_id, objective)
            data["cases"][case_id] = case
            data["revision"] += 1
            data["updated_at"] = utc_now()
            result = {
                "case_id": case_id,
                "state": case["state"],
                "revision": case["revision"],
                "store_revision": data["revision"],
                "idempotent": False,
            }
            data["store_events"][request_id] = {
                "operation": "register_case",
                "fingerprint": fingerprint,
                "result": result,
            }
            self._write_unlocked(data)
            return copy.deepcopy(result)

    def _mutate(
        self,
        case_id: str,
        *,
        operation: str,
        payload: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
        callback: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        case_id = canonical_case_id(case_id)
        request_id = require_request_id(request_id)
        fingerprint = _fingerprint(operation, payload)
        with FileLock(self.lock_path):
            data = self._read_unlocked()
            if case_id not in data["cases"]:
                raise ValidationError(f"case not found: {case_id}")
            original = data["cases"][case_id]
            prior = original["events"].get(request_id)
            if prior:
                if prior.get("fingerprint") != fingerprint:
                    raise ConflictError("request_id was already used with a different operation payload")
                result = copy.deepcopy(prior["result"])
                result["idempotent"] = True
                return result
            runtime = original.get("runtime")
            action_grants = runtime.get("action_grants", {}) if isinstance(runtime, dict) else {}
            active_grants = [
                grant
                for grant in action_grants.values()
                if isinstance(grant, dict) and grant.get("status") in {"ISSUED", "CLAIMED"}
            ]
            if active_grants:
                if len(active_grants) != 1:
                    raise StoreCorruptionError("canonical case contains multiple active action grants")
                active_status = active_grants[0]["status"]
                allowed = (
                    {"claim_action_grant", "fail_action_grant"}
                    if active_status == "ISSUED"
                    else {"complete_action_grant", "fail_action_grant"}
                )
                if operation not in allowed:
                    raise AuthorizationError(
                        f"case mutation {operation} is blocked while an action grant is {active_status}"
                    )
            if expected_revision != original["revision"]:
                raise RevisionConflict(
                    f"case {case_id} expected revision {expected_revision}, found {original['revision']}"
                )
            case = copy.deepcopy(original)
            result = callback(case, data)
            case["revision"] += 1
            case["updated_at"] = utc_now()
            result = {
                **result,
                "case_id": case_id,
                "state": case["state"],
                "revision": case["revision"],
                "idempotent": False,
            }
            case["events"][request_id] = {
                "operation": operation,
                "fingerprint": fingerprint,
                "result": copy.deepcopy(result),
            }
            data["cases"][case_id] = case
            data["revision"] += 1
            data["updated_at"] = utc_now()
            result["store_revision"] = data["revision"]
            case["events"][request_id]["result"]["store_revision"] = data["revision"]
            self._write_unlocked(data)
            return copy.deepcopy(result)

    @staticmethod
    def _require_state(case: Mapping[str, Any], expected: str, operation: str) -> None:
        if case["state"] != expected:
            raise TransitionError(f"{operation} requires {expected}; case is {case['state']}")

    def bind(
        self,
        case_id: str,
        *,
        kind: str,
        value: str,
        repository: str | None = None,
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        record = _binding_record(kind, value, repository=repository)
        normalized = record["value"]
        payload = copy.deepcopy(record)

        def change(case: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
            if kind == "repo_url":
                if normalized not in case["bindings"][kind]:
                    case["bindings"][kind].append(normalized)
                    case["bindings"][kind].sort()
                return {"binding": record}
            if kind == "branch":
                repo = record["repository"]
                if repo not in case["bindings"]["repo_url"]:
                    raise ValidationError("branch repository must first be associated with the case")
                key = _binding_key(kind, normalized, repository=repo)
                stored_binding: Any = {"repository": repo, "value": normalized}
            else:
                key = _binding_key(kind, normalized)
                stored_binding = normalized
            owner = data["bindings"].get(key)
            if owner and owner != case["case_id"]:
                raise ConflictError(f"{kind} identifier is already bound to case {owner}")
            data["bindings"][key] = case["case_id"]
            if stored_binding not in case["bindings"][kind]:
                case["bindings"][kind].append(stored_binding)
                if kind == "branch":
                    case["bindings"][kind].sort(key=lambda item: (item["repository"], item["value"]))
                else:
                    case["bindings"][kind].sort()
            return {"binding": record}

        return self._mutate(
            case_id,
            operation="bind",
            payload=payload,
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    def resolve_bindings(
        self, kind: str, value: str, *, repository: str | None = None
    ) -> list[str]:
        record = _binding_record(kind, value, repository=repository)
        data = self._read()
        if kind == "repo_url":
            return sorted(
                case_id
                for case_id, case in data["cases"].items()
                if record["value"] in case["bindings"]["repo_url"]
            )
        key = _binding_key(kind, record["value"], repository=record.get("repository"))
        owner = data["bindings"].get(key)
        return [] if owner is None else [owner]

    def resolve_binding(
        self, kind: str, value: str, *, repository: str | None = None
    ) -> str | None:
        owners = self.resolve_bindings(kind, value, repository=repository)
        if len(owners) > 1:
            raise ConflictError(
                f"{kind} identifier is associated with multiple cases; use resolve_bindings"
            )
        return owners[0] if owners else None

    def start_implementation(self, case_id: str, *, request_id: str, expected_revision: int) -> dict[str, Any]:
        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            if case["limits"]["implementation_generations"] >= 1:
                raise LimitError("case already consumed its one implementation generation")
            self._require_state(case, "REGISTERED", "start_implementation")
            case["limits"]["implementation_generations"] += 1
            case["state"] = "IMPLEMENTING"
            return {}

        return self._mutate(
            case_id,
            operation="start_implementation",
            payload={},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    @staticmethod
    def _normalize_heads(heads: Mapping[str, str]) -> dict[str, str]:
        if not isinstance(heads, Mapping) or not heads:
            raise ValidationError("candidate heads must be a non-empty object")
        result: dict[str, str] = {}
        for repo, sha in heads.items():
            normalized_repo = normalize_repo_url(str(repo))
            if normalized_repo in result:
                raise ValidationError(f"duplicate normalized repository in heads: {normalized_repo}")
            result[normalized_repo] = require_sha(str(sha), f"head for {normalized_repo}")
        return dict(sorted(result.items()))

    @staticmethod
    def _normalize_snapshots(
        snapshots: Mapping[str, Any], expected_heads: Mapping[str, str]
    ) -> dict[str, Any]:
        if not isinstance(snapshots, Mapping):
            raise ValidationError("snapshots must be an object")
        expected_repos = set(expected_heads)
        result: dict[str, Any] = {}
        for repo, record in snapshots.items():
            normalized_repo = normalize_repo_url(str(repo))
            if not isinstance(record, Mapping):
                raise ValidationError(f"snapshot record for {normalized_repo} must be an object")
            contract = _nonempty(record.get("contract"), "snapshot contract", 100)
            digest = require_snapshot_hash(str(record.get("sha256", "")))
            head = require_sha(str(record.get("head", "")), f"snapshot head for {normalized_repo}")
            if normalized_repo not in expected_heads or head != expected_heads[normalized_repo]:
                raise ValidationError(f"snapshot head for {normalized_repo} must match the candidate head")
            result[normalized_repo] = {"contract": contract, "sha256": digest, "head": head}
        if set(result) != expected_repos:
            raise ValidationError("snapshot repositories must exactly match candidate head repositories")
        return dict(sorted(result.items()))

    def freeze_candidate(
        self,
        case_id: str,
        *,
        heads: Mapping[str, str],
        snapshots: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        normalized_heads = self._normalize_heads(heads)
        normalized_snapshots = self._normalize_snapshots(snapshots, normalized_heads)
        for record in normalized_snapshots.values():
            if record["contract"] != SNAPSHOT_CONTRACT:
                raise ValidationError(f"snapshot contract must be {SNAPSHOT_CONTRACT}")

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "IMPLEMENTING", "freeze_candidate")
            case["candidate"]["review_heads"] = normalized_heads
            case["candidate"]["current_heads"] = normalized_heads
            case["candidate"]["review_snapshots"] = normalized_snapshots
            case["state"] = "CANDIDATE_FROZEN"
            return {"heads": normalized_heads, "snapshots": normalized_snapshots}

        return self._mutate(
            case_id,
            operation="freeze_candidate",
            payload={"heads": normalized_heads, "snapshots": normalized_snapshots},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    @staticmethod
    def _normalize_review_cohort(case: Mapping[str, Any], cohort: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(cohort, Mapping):
            raise ValidationError("review cohort must be an object")
        required_fields = {
            "protocol_version", "schema_version", "cohort_id", "required_receipt", "reviewers",
        }
        if set(cohort) != required_fields:
            raise ValidationError("review cohort must use the fixed ccos-review-cohort-v1 schema")
        if cohort.get("protocol_version") != REVIEW_COHORT_PROTOCOL_VERSION or cohort.get("schema_version") != 1:
            raise ValidationError("review cohort protocol or schema version is unsupported")
        cohort_id = _nonempty(cohort.get("cohort_id"), "review cohort id", 128)
        if not FINDING_ID_PATTERN.fullmatch(cohort_id):
            raise ValidationError("review cohort id must be a stable identifier")
        receipt_contract = cohort.get("required_receipt")
        if receipt_contract != {
            "protocol_version": REVIEW_COMPLETION_PROTOCOL_VERSION,
            "schema_version": 1,
        }:
            raise ValidationError("review cohort requires the fixed ccos-review-completion-v1 receipt")
        reviewers = cohort.get("reviewers")
        if not isinstance(reviewers, list) or not reviewers:
            raise ValidationError("review cohort must contain at least one required reviewer")
        normalized_reviewers: list[dict[str, Any]] = []
        reviewer_ids: set[str] = set()
        thread_ids: set[str] = set()
        assigned_repositories: set[str] = set()
        expected_fields = {
            "reviewer_id", "reviewer_role", "thread_id", "repository", "reviewed_head",
            "snapshot", "scope", "scope_sha256", "required",
        }
        for raw in reviewers:
            if not isinstance(raw, Mapping) or set(raw) != expected_fields:
                raise ValidationError("reviewer assignment must use the fixed cohort schema")
            reviewer_id = _nonempty(raw.get("reviewer_id"), "reviewer id", 128)
            if not FINDING_ID_PATTERN.fullmatch(reviewer_id) or reviewer_id in reviewer_ids:
                raise ValidationError("reviewer ids must be stable and unique")
            if raw.get("reviewer_role") != "review_child":
                raise ValidationError("declared reviewer role must be review_child")
            thread_id = normalize_binding("thread", str(raw.get("thread_id", "")))
            if thread_id in thread_ids:
                raise ValidationError("reviewer thread ids must be unique")
            if thread_id not in case["bindings"]["thread"]:
                raise AuthorizationError("declared reviewer thread is not canonically bound to the case")
            runtime = case.get("runtime")
            if isinstance(runtime, Mapping) and thread_id in runtime.get("actors", {}):
                if runtime["actors"][thread_id].get("role") != "review_child":
                    raise AuthorizationError("declared reviewer thread has a different canonical runtime role")
            repository = normalize_repo_url(str(raw.get("repository", "")))
            head = require_sha(str(raw.get("reviewed_head", "")), "reviewed head")
            if case["candidate"]["review_heads"].get(repository) != head:
                raise ValidationError("reviewer assignment head must match the frozen candidate head")
            snapshot = raw.get("snapshot")
            normalized_snapshot = CaseStore._normalize_snapshots(
                {repository: snapshot}, {repository: head}
            )[repository]
            frozen_snapshot = case["candidate"]["review_snapshots"].get(repository)
            comparable_frozen = (
                {**frozen_snapshot, "head": head}
                if isinstance(frozen_snapshot, Mapping) and "head" not in frozen_snapshot
                else frozen_snapshot
            )
            if normalized_snapshot != comparable_frozen:
                raise ValidationError("reviewer assignment snapshot must match the frozen candidate snapshot")
            scope = _nonempty(raw.get("scope"), "reviewer scope", 4096)
            scope_sha256 = require_snapshot_hash(str(raw.get("scope_sha256", "")))
            if scope_sha256 != hashlib.sha256(scope.encode("utf-8")).hexdigest():
                raise ValidationError("reviewer scope_sha256 must match the exact assigned scope")
            if raw.get("required") is not True:
                raise ValidationError("every declared cohort reviewer must be required")
            normalized_reviewers.append({
                "reviewer_id": reviewer_id,
                "reviewer_role": "review_child",
                "thread_id": thread_id,
                "repository": repository,
                "reviewed_head": head,
                "snapshot": normalized_snapshot,
                "scope": scope,
                "scope_sha256": scope_sha256,
                "required": True,
            })
            reviewer_ids.add(reviewer_id)
            thread_ids.add(thread_id)
            assigned_repositories.add(repository)
        if assigned_repositories != set(case["candidate"]["review_heads"]):
            raise ValidationError("review cohort must cover every frozen candidate repository")
        normalized_reviewers.sort(key=lambda item: item["reviewer_id"])
        return {
            "protocol_version": REVIEW_COHORT_PROTOCOL_VERSION,
            "schema_version": 1,
            "cohort_id": cohort_id,
            "required_receipt": {
                "protocol_version": REVIEW_COMPLETION_PROTOCOL_VERSION,
                "schema_version": 1,
            },
            "reviewers": normalized_reviewers,
        }

    def start_review(
        self,
        case_id: str,
        *,
        cohort: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "CANDIDATE_FROZEN", "start_review")
            if case["limits"]["review_cohorts"] >= 1:
                raise LimitError("case already consumed its one review cohort")
            normalized = self._normalize_review_cohort(case, cohort)
            declared = {**normalized, "declared_at": utc_now()}
            declared["cohort_sha256"] = canonical_json_sha256(normalized)
            case["review"] = {"cohort": declared, "receipts": {}}
            case["limits"]["review_cohorts"] += 1
            case["state"] = "REVIEW_COLLECTING"
            return {
                "cohort_id": declared["cohort_id"],
                "cohort_sha256": declared["cohort_sha256"],
                "required_reviewer_ids": [item["reviewer_id"] for item in declared["reviewers"]],
            }

        return self._mutate(
            case_id,
            operation="start_review",
            payload={"cohort": cohort},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    @staticmethod
    def _normalize_finding(finding: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(finding, Mapping):
            raise ValidationError("finding must be an object")
        finding_id = _nonempty(finding.get("id"), "finding id", 128)
        if not FINDING_ID_PATTERN.fullmatch(finding_id):
            raise ValidationError("finding id must be stable and use letters, numbers, dot, underscore, colon, or hyphen")
        classification = _nonempty(finding.get("classification"), "finding classification", 64).upper()
        if classification not in FINDING_CLASSES:
            raise ValidationError(f"finding classification must be one of: {', '.join(sorted(FINDING_CLASSES))}")
        return {
            "id": finding_id,
            "candidate": _nonempty(finding.get("candidate"), "finding candidate", 256),
            "repo": normalize_repo_url(str(finding.get("repo", ""))),
            "reviewed_sha": require_sha(str(finding.get("reviewed_sha", "")), "finding reviewed_sha"),
            "source": _nonempty(finding.get("source"), "finding source", 512),
            "description": _nonempty(finding.get("description"), "finding description"),
            "classification": classification,
        }

    def add_finding(
        self,
        case_id: str,
        *,
        finding: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        normalized = self._normalize_finding(finding)

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            existing_ids = {item["id"] for item in case["findings"]["items"] + case["findings"]["late"]}
            if normalized["id"] in existing_ids:
                raise ConflictError(f"finding id already exists in case: {normalized['id']}")
            item = copy.deepcopy(normalized)
            if case["state"] == "REVIEW_COLLECTING":
                review = case.get("review")
                cohort = review.get("cohort") if isinstance(review, Mapping) else None
                if cohort is not None:
                    assignment = next(
                        (record for record in cohort["reviewers"] if record["reviewer_id"] == item["source"]),
                        None,
                    )
                    if assignment is None:
                        raise AuthorizationError("finding source is not a declared reviewer")
                    if item["source"] in review["receipts"]:
                        raise ConflictError("reviewer already submitted a completion receipt")
                    if item["repo"] != assignment["repository"]:
                        raise ValidationError("finding repository differs from the reviewer assignment")
                expected_head = case["candidate"]["review_heads"].get(item["repo"])
                if expected_head != item["reviewed_sha"]:
                    item["reported_classification"] = item["classification"]
                    item["classification"] = "INVALID_OR_STALE"
                    item["stale_reason"] = (
                        f"reviewed_sha {item['reviewed_sha']} does not match frozen head {expected_head or 'missing'}"
                    )
                item["authorizing"] = item["classification"] == "CURRENT_BLOCKER"
                item["late"] = False
                case["findings"]["items"].append(item)
                return {"finding": item, "late": False}
            if case["findings"]["frozen"]:
                item["authorizing"] = False
                item["late"] = True
                item["late_reason"] = "finding arrived after the finding set was frozen"
                case["findings"]["late"].append(item)
                return {"finding": item, "late": True}
            raise TransitionError(
                f"add_finding requires REVIEW_COLLECTING or an already frozen finding set; case is {case['state']}"
            )

        return self._mutate(
            case_id,
            operation="add_finding",
            payload={"finding": normalized},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    def _normalize_review_completion(
        self,
        case: Mapping[str, Any],
        completion: Mapping[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if not isinstance(completion, Mapping):
            raise ValidationError("review completion must be an object")
        expected_fields = {
            "protocol_version", "schema_version", "case_id", "cohort_id", "reviewer_id",
            "reviewer_role", "thread_id", "completed_turn_id", "repository", "reviewed_head",
            "snapshot", "scope", "scope_sha256", "completion_state", "findings", "finding_ids",
            "completed_at", "native_completion_evidence_sha256",
        }
        if set(completion) != expected_fields:
            raise ValidationError("review completion must use the fixed ccos-review-completion-v1 schema")
        if (completion.get("protocol_version") != REVIEW_COMPLETION_PROTOCOL_VERSION
                or completion.get("schema_version") != 1):
            raise ValidationError("review completion protocol or schema version is unsupported")
        if canonical_case_id(str(completion.get("case_id", ""))) != case["case_id"]:
            raise ValidationError("review completion case_id differs from the canonical case")
        review = case.get("review")
        cohort = review.get("cohort") if isinstance(review, Mapping) else None
        if not isinstance(cohort, Mapping):
            raise AuthorizationError("no review cohort was declared")
        cohort_id = _nonempty(completion.get("cohort_id"), "review cohort id", 128)
        if cohort_id != cohort["cohort_id"]:
            raise ValidationError("review completion cohort_id differs from the frozen cohort")
        reviewer_id = _nonempty(completion.get("reviewer_id"), "reviewer id", 128)
        assignment = next(
            (record for record in cohort["reviewers"] if record["reviewer_id"] == reviewer_id),
            None,
        )
        if assignment is None:
            raise AuthorizationError("review completion reviewer is not in the frozen cohort")
        if completion.get("reviewer_role") != assignment["reviewer_role"]:
            raise AuthorizationError("review completion role differs from the frozen assignment")
        thread_id = normalize_binding("thread", str(completion.get("thread_id", "")))
        if thread_id != assignment["thread_id"]:
            raise AuthorizationError("review completion thread differs from the frozen assignment")
        completed_turn_id = _nonempty(completion.get("completed_turn_id"), "completed turn id", 256)
        repository = normalize_repo_url(str(completion.get("repository", "")))
        if repository != assignment["repository"]:
            raise ValidationError("review completion repository differs from the frozen assignment")
        reviewed_head = require_sha(str(completion.get("reviewed_head", "")), "reviewed head")
        if reviewed_head != assignment["reviewed_head"]:
            raise ValidationError("review completion head differs from the frozen assignment")
        snapshot = self._normalize_snapshots(
            {repository: completion.get("snapshot")}, {repository: reviewed_head}
        )[repository]
        if snapshot != assignment["snapshot"]:
            raise ValidationError("review completion snapshot differs from the frozen assignment")
        scope = _nonempty(completion.get("scope"), "review scope", 4096)
        if scope != assignment["scope"]:
            raise ValidationError("review completion scope differs from the frozen assignment")
        scope_sha256 = require_snapshot_hash(str(completion.get("scope_sha256", "")))
        if scope_sha256 != assignment["scope_sha256"] or scope_sha256 != hashlib.sha256(
            scope.encode("utf-8")
        ).hexdigest():
            raise ValidationError("review completion scope digest differs from the frozen assignment")
        completion_state = _nonempty(completion.get("completion_state"), "completion state", 32).upper()
        if completion_state not in REVIEW_COMPLETION_STATES:
            raise ValidationError("review completion state must be COMPLETED, FAILED, or INCOMPLETE")
        raw_findings = completion.get("findings")
        raw_ids = completion.get("finding_ids")
        if not isinstance(raw_findings, list) or not isinstance(raw_ids, list):
            raise ValidationError("review completion findings and finding_ids must be arrays")
        normalized_findings = [self._normalize_finding(item) for item in raw_findings]
        finding_ids = sorted({_nonempty(item, "finding id", 128) for item in raw_ids})
        if len(finding_ids) != len(raw_ids):
            raise ValidationError("review completion finding_ids must be unique and sorted")
        if completion_state != "COMPLETED" and (normalized_findings or finding_ids):
            raise ValidationError("failed or incomplete review completion cannot report findings")
        for finding in normalized_findings:
            if (finding["source"] != reviewer_id or finding["repo"] != repository
                    or finding["reviewed_sha"] != reviewed_head):
                raise ValidationError("completion findings must match the exact reviewer assignment")
        normalized = {
            "protocol_version": REVIEW_COMPLETION_PROTOCOL_VERSION,
            "schema_version": 1,
            "case_id": case["case_id"],
            "cohort_id": cohort_id,
            "reviewer_id": reviewer_id,
            "reviewer_role": assignment["reviewer_role"],
            "thread_id": thread_id,
            "completed_turn_id": completed_turn_id,
            "repository": repository,
            "reviewed_head": reviewed_head,
            "snapshot": snapshot,
            "scope": scope,
            "scope_sha256": scope_sha256,
            "completion_state": completion_state,
            "finding_ids": finding_ids,
            "completed_at": require_utc_timestamp(completion.get("completed_at"), "completed_at"),
            "native_completion_evidence_sha256": require_snapshot_hash(
                str(completion.get("native_completion_evidence_sha256", ""))
            ),
        }
        return normalized, normalized_findings

    def submit_review_completion(
        self,
        case_id: str,
        *,
        completion: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        request_id = require_request_id(request_id)

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "REVIEW_COLLECTING", "submit_review_completion")
            normalized, new_findings = self._normalize_review_completion(case, completion)
            reviewer_id = normalized["reviewer_id"]
            review = case["review"]
            if reviewer_id in review["receipts"]:
                raise ConflictError("reviewer already submitted a completion receipt")
            existing_ids = {
                item["id"] for item in case["findings"]["items"] + case["findings"]["late"]
            }
            new_ids = [item["id"] for item in new_findings]
            if len(new_ids) != len(set(new_ids)) or existing_ids.intersection(new_ids):
                raise ConflictError("review completion contains a duplicate finding id")
            attributed = sorted(
                item["id"] for item in case["findings"]["items"] if item["source"] == reviewer_id
            )
            expected_ids = sorted([*attributed, *new_ids])
            if normalized["finding_ids"] != expected_ids:
                raise ValidationError(
                    "review completion finding_ids must exactly cover every finding attributed to that reviewer"
                )
            for finding in new_findings:
                item = copy.deepcopy(finding)
                expected_head = case["candidate"]["review_heads"].get(item["repo"])
                if expected_head != item["reviewed_sha"]:
                    item["reported_classification"] = item["classification"]
                    item["classification"] = "INVALID_OR_STALE"
                    item["stale_reason"] = (
                        f"reviewed_sha {item['reviewed_sha']} does not match frozen head {expected_head or 'missing'}"
                    )
                item["authorizing"] = item["classification"] == "CURRENT_BLOCKER"
                item["late"] = False
                case["findings"]["items"].append(item)
            recorded = {
                **normalized,
                "request_id": request_id,
                "recorded_at": utc_now(),
            }
            recorded["receipt_sha256"] = canonical_json_sha256(recorded)
            review["receipts"][reviewer_id] = recorded
            return {
                "cohort_id": normalized["cohort_id"],
                "reviewer_id": reviewer_id,
                "completion_state": normalized["completion_state"],
                "finding_ids": normalized["finding_ids"],
                "receipt_sha256": recorded["receipt_sha256"],
            }

        return self._mutate(
            case_id,
            operation="submit_review_completion",
            payload={"completion": completion},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    def freeze_findings(self, case_id: str, *, request_id: str, expected_revision: int) -> dict[str, Any]:
        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "REVIEW_COLLECTING", "freeze_findings")
            if case["findings"]["frozen"]:
                raise LimitError("findings have already been frozen")
            review = case.get("review")
            cohort = review.get("cohort") if isinstance(review, Mapping) else None
            if not isinstance(cohort, Mapping) or not cohort.get("reviewers"):
                raise AuthorizationError("findings cannot be frozen without a declared nonempty review cohort")
            if case["control"].get("active_failure") is not None:
                raise ControlFailureError("findings cannot be frozen while a control failure remains active")
            required_ids = [item["reviewer_id"] for item in cohort["reviewers"]]
            receipts = review.get("receipts")
            if not isinstance(receipts, Mapping):
                raise StoreCorruptionError("review receipts are unavailable")
            missing = sorted(set(required_ids) - set(receipts))
            unexpected = sorted(set(receipts) - set(required_ids))
            if missing:
                raise AuthorizationError("missing required review receipts: " + ", ".join(missing))
            if unexpected:
                raise AuthorizationError("unexpected review receipts: " + ", ".join(unexpected))
            unsuccessful = sorted(
                reviewer_id
                for reviewer_id, receipt in receipts.items()
                if receipt.get("completion_state") != "COMPLETED"
            )
            if unsuccessful:
                raise AuthorizationError("review cohort is incomplete or failed: " + ", ".join(unsuccessful))
            receipt_finding_ids: list[str] = []
            for reviewer_id in required_ids:
                receipt_finding_ids.extend(receipts[reviewer_id]["finding_ids"])
            if len(receipt_finding_ids) != len(set(receipt_finding_ids)):
                raise ConflictError("a finding id appears in more than one completion receipt")
            recorded_ids = sorted(item["id"] for item in case["findings"]["items"])
            if sorted(receipt_finding_ids) != recorded_ids:
                raise AuthorizationError("completion receipts do not exactly cover the recorded current findings")
            case["findings"]["frozen"] = True
            case["findings"]["frozen_ids"] = [item["id"] for item in case["findings"]["items"]]
            case["state"] = "FINDINGS_FROZEN"
            blockers = [
                item["id"]
                for item in case["findings"]["items"]
                if item["classification"] == "CURRENT_BLOCKER" and item["authorizing"]
            ]
            return {
                "cohort_id": cohort["cohort_id"],
                "cohort_sha256": cohort["cohort_sha256"],
                "receipt_sha256s": {
                    reviewer_id: receipts[reviewer_id]["receipt_sha256"] for reviewer_id in sorted(receipts)
                },
                "frozen_ids": case["findings"]["frozen_ids"],
                "current_blocker_ids": blockers,
            }

        return self._mutate(
            case_id,
            operation="freeze_findings",
            payload={},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    @staticmethod
    def _frozen_blockers(case: Mapping[str, Any]) -> list[str]:
        return sorted(
            item["id"]
            for item in case["findings"]["items"]
            if item["classification"] == "CURRENT_BLOCKER" and item.get("authorizing") is True
        )

    def close_without_blockers(
        self, case_id: str, *, request_id: str, expected_revision: int
    ) -> dict[str, Any]:
        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "FINDINGS_FROZEN", "close_without_blockers")
            if self._frozen_blockers(case):
                raise AuthorizationError("case has frozen CURRENT_BLOCKER findings and requires authorized repair")
            redesign_ids = sorted(
                item["id"]
                for item in case["findings"]["items"]
                if item["classification"] == "REDESIGN_REQUIRED"
            )
            if redesign_ids:
                case["state"] = "CASE_LOCKED"
                case["lock_reason"] = (
                    "redesign is materially outside the one bounded repair: " + ", ".join(redesign_ids)
                )
                return {"lock_reason": case["lock_reason"]}
            control_ids = sorted(
                item["id"]
                for item in case["findings"]["items"]
                if item["classification"] == "CONTROL_FAILURE"
            )
            if control_ids:
                return self._apply_control_failure(
                    case,
                    category="review_control_finding",
                    fingerprint="review-control-findings:" + hashlib.sha256(
                        json.dumps(control_ids).encode("utf-8")
                    ).hexdigest(),
                    description="the frozen review cohort reported a control failure: " + ", ".join(control_ids),
                )
            case["state"] = "CLOSED_SUCCESS"
            return {"publication_eligible": True}

        return self._mutate(
            case_id,
            operation="close_without_blockers",
            payload={},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    @staticmethod
    def _normalize_authority(authority: Mapping[str, Any]) -> dict[str, str]:
        if not isinstance(authority, Mapping):
            raise AuthorizationError("repair authorization requires an authority record")
        try:
            return {
                "authority_id": _nonempty(authority.get("authority_id"), "authority_id", 256),
                "source": _nonempty(authority.get("source"), "authority source", 512),
                "authorized_by": _nonempty(authority.get("authorized_by"), "authorized_by", 256),
                "scope": _nonempty(authority.get("scope"), "authority scope", 2048),
            }
        except ValidationError as exc:
            raise AuthorizationError(str(exc)) from exc

    def authorize_repair(
        self,
        case_id: str,
        *,
        finding_ids: Iterable[str],
        authority: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        ids = sorted({_nonempty(item, "finding id", 128) for item in finding_ids})
        normalized_authority = self._normalize_authority(authority)

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "FINDINGS_FROZEN", "authorize_repair")
            blockers = self._frozen_blockers(case)
            if not blockers:
                raise AuthorizationError("case has no frozen CURRENT_BLOCKER findings to authorize")
            if ids != blockers:
                raise AuthorizationError(
                    f"repair authorization must contain the exact frozen CURRENT_BLOCKER set: {blockers}"
                )
            if case["limits"]["combined_repairs"] >= 1:
                raise LimitError("case already consumed its one combined repair")
            case["repair"]["authorized_ids"] = blockers
            case["repair"]["authority"] = normalized_authority
            case["state"] = "REPAIR_AUTHORIZED"
            return {"authorized_ids": blockers, "authority": normalized_authority}

        return self._mutate(
            case_id,
            operation="authorize_repair",
            payload={"finding_ids": ids, "authority": normalized_authority},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    def complete_repair(
        self,
        case_id: str,
        *,
        heads: Mapping[str, str],
        snapshots: Mapping[str, Any],
        addressed_ids: Iterable[str],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        normalized_heads = self._normalize_heads(heads)
        normalized_snapshots = self._normalize_snapshots(snapshots, normalized_heads)
        addressed = sorted({_nonempty(item, "addressed finding id", 128) for item in addressed_ids})

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "REPAIR_AUTHORIZED", "complete_repair")
            authorized = sorted(case["repair"]["authorized_ids"])
            if addressed != authorized:
                raise AuthorizationError(f"repair must address the exact authorized blocker set: {authorized}")
            if set(normalized_heads) != set(case["candidate"]["review_heads"]):
                raise ValidationError("repaired head repositories must exactly match frozen review repositories")
            if normalized_heads == case["candidate"]["review_heads"]:
                raise ValidationError("combined repair must advance at least one candidate head")
            for record in normalized_snapshots.values():
                if record["contract"] != SNAPSHOT_CONTRACT:
                    raise ValidationError(f"snapshot contract must be {SNAPSHOT_CONTRACT}")
            if case["limits"]["combined_repairs"] >= 1:
                raise LimitError("case already consumed its one combined repair")
            case["limits"]["combined_repairs"] += 1
            case["repair"]["addressed_ids"] = addressed
            case["candidate"]["repaired_heads"] = normalized_heads
            case["candidate"]["repaired_snapshots"] = normalized_snapshots
            case["candidate"]["current_heads"] = normalized_heads
            case["state"] = "REPAIR_COMPLETE"
            return {"heads": normalized_heads, "addressed_ids": addressed}

        return self._mutate(
            case_id,
            operation="complete_repair",
            payload={"heads": normalized_heads, "snapshots": normalized_snapshots, "addressed_ids": addressed},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    def observe_heads(
        self,
        case_id: str,
        *,
        heads: Mapping[str, str],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        normalized_heads = self._normalize_heads(heads)

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            expected = case["candidate"]["current_heads"]
            if not expected:
                raise TransitionError("observe_heads requires a frozen candidate")
            case["candidate"]["observed_heads"] = normalized_heads
            if normalized_heads != expected:
                case["state"] = "CASE_LOCKED"
                case["lock_reason"] = "unexpected head drift outside the one authorized combined repair"
                return {"lock_reason": case["lock_reason"], "expected_heads": expected, "observed_heads": normalized_heads}
            return {"heads_match": True}

        return self._mutate(
            case_id,
            operation="observe_heads",
            payload={"heads": normalized_heads},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    def start_closure_preflight(
        self, case_id: str, *, request_id: str, expected_revision: int
    ) -> dict[str, Any]:
        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "REPAIR_COMPLETE", "start_closure_preflight")
            case["state"] = "CLOSURE_PREFLIGHT"
            return {}

        return self._mutate(
            case_id,
            operation="start_closure_preflight",
            payload={},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    def verify_closure_preflight(
        self,
        case_id: str,
        *,
        review_heads: Mapping[str, str],
        repaired_heads: Mapping[str, str],
        authorized_ids: Iterable[str],
        snapshots: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        normalized_review = self._normalize_heads(review_heads)
        normalized_repaired = self._normalize_heads(repaired_heads)
        normalized_snapshots = self._normalize_snapshots(snapshots, normalized_repaired)
        ids = sorted({_nonempty(item, "authorized finding id", 128) for item in authorized_ids})

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "CLOSURE_PREFLIGHT", "verify_closure_preflight")
            errors: list[str] = []
            if normalized_review != case["candidate"]["review_heads"]:
                errors.append("review heads do not match the frozen reviewed heads")
            if normalized_repaired != case["candidate"]["repaired_heads"]:
                errors.append("repaired heads do not match the single authorized repair heads")
            if ids != sorted(case["repair"]["authorized_ids"]):
                errors.append("authorized blocker IDs do not match the frozen repair authority")
            if ids != self._frozen_blockers(case):
                errors.append("unapproved findings or missing frozen blockers are present")
            if normalized_snapshots != case["candidate"]["repaired_snapshots"]:
                errors.append("snapshot contract or digest does not match the repaired candidate")
            if any(record["contract"] != SNAPSHOT_CONTRACT for record in normalized_snapshots.values()):
                errors.append(f"snapshot contract must be {SNAPSHOT_CONTRACT}")
            if errors:
                raise PreflightError("closure preflight failed: " + "; ".join(errors))
            if case["limits"]["closure_checks"] >= 1:
                raise LimitError("case already consumed its one substantive closure check")
            case["limits"]["closure_checks"] += 1
            case["closure"]["preflight"] = {
                "review_heads": normalized_review,
                "repaired_heads": normalized_repaired,
                "authorized_ids": ids,
                "snapshots": normalized_snapshots,
                "verified_at": utc_now(),
            }
            case["state"] = "CLOSURE_CHECK"
            return {"preflight": "verified"}

        return self._mutate(
            case_id,
            operation="verify_closure_preflight",
            payload={
                "review_heads": normalized_review,
                "repaired_heads": normalized_repaired,
                "authorized_ids": ids,
                "snapshots": normalized_snapshots,
            },
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    @staticmethod
    def _apply_control_failure(
        case: dict[str, Any], *, category: str, fingerprint: str, description: str
    ) -> dict[str, Any]:
        category = _nonempty(category, "control failure category", 128)
        fingerprint = _nonempty(fingerprint, "control failure fingerprint", 256)
        description = _nonempty(description, "control failure description", 4096)
        if case["state"] in {"CLOSED_SUCCESS", "CASE_LOCKED"}:
            raise TransitionError(f"cannot record control failure from {case['state']}")
        if case["state"] == "CONTROL_FAILURE":
            raise ControlFailureError(
                "case already has an active control failure; retry the identical fingerprint or stop this case"
            )
        failure = {
            "category": category,
            "fingerprint": fingerprint,
            "description": description,
            "product_defect": False,
            "recorded_at": utc_now(),
        }
        case["control"]["history"].append(failure)
        if case["limits"]["operational_retries"] >= 1:
            case["state"] = "CASE_LOCKED"
            case["resumable_state"] = None
            case["control"]["active_failure"] = failure
            case["lock_reason"] = "control failure recurred after the one identical operational retry"
            return {"failure": failure, "lock_reason": case["lock_reason"]}
        case["resumable_state"] = case["state"]
        case["state"] = "CONTROL_FAILURE"
        case["control"]["active_failure"] = failure
        return {"failure": failure, "resumable_state": case["resumable_state"]}

    def complete_closure_check(
        self,
        case_id: str,
        *,
        resolutions: Mapping[str, str],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        if not isinstance(resolutions, Mapping):
            raise ValidationError("closure resolutions must be an object")
        normalized = {str(key): str(value).upper() for key, value in resolutions.items()}

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "CLOSURE_CHECK", "complete_closure_check")
            authorized = sorted(case["repair"]["authorized_ids"])
            if sorted(normalized) != authorized:
                return self._apply_control_failure(
                    case,
                    category="closure_protocol",
                    fingerprint="unexpected-closure-finding-set:" + hashlib.sha256(
                        json.dumps(sorted(normalized)).encode("utf-8")
                    ).hexdigest(),
                    description="closure check attempted to add or omit findings instead of resolving only authorized blockers",
                )
            allowed = {"RESOLVED", "REMAINING", "REGRESSION"}
            invalid = sorted({value for value in normalized.values() if value not in allowed})
            if invalid:
                return self._apply_control_failure(
                    case,
                    category="closure_protocol",
                    fingerprint="invalid-closure-resolution:" + ",".join(invalid),
                    description="closure check returned an invalid resolution status",
                )
            case["closure"]["resolutions"] = dict(sorted(normalized.items()))
            unresolved = [key for key, value in normalized.items() if value != "RESOLVED"]
            if unresolved:
                case["state"] = "CASE_LOCKED"
                case["lock_reason"] = "remaining blocker or repair regression in the one closure check: " + ", ".join(
                    sorted(unresolved)
                )
                return {"resolutions": normalized, "lock_reason": case["lock_reason"]}
            case["state"] = "CLOSED_SUCCESS"
            return {"resolutions": normalized, "publication_eligible": True}

        return self._mutate(
            case_id,
            operation="complete_closure_check",
            payload={"resolutions": normalized},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    @staticmethod
    def _runtime_record(case: dict[str, Any], *, create: bool) -> dict[str, Any]:
        runtime = case.get("runtime")
        if runtime is None and create:
            runtime = {"actors": {}, "action_grants": {}}
            case["runtime"] = runtime
        if not isinstance(runtime, dict) or set(runtime) != {"actors", "action_grants"}:
            raise StoreCorruptionError("case runtime record is unavailable or invalid")
        return runtime

    def claim_runtime_generation_attempt(
        self,
        case_id: str,
        *,
        attempt: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        expected_fields = {
            "protocol_version", "schema_version", "attempt_id", "grant_id",
            "controller_spec_sha256",
        }
        if not isinstance(attempt, Mapping) or set(attempt) != expected_fields:
            raise ValidationError("runtime generation attempt must use the fixed schema")
        if (
            attempt.get("protocol_version")
            != RUNTIME_GENERATION_ATTEMPT_PROTOCOL_VERSION
            or attempt.get("schema_version") != 1
        ):
            raise AuthorizationError("runtime generation attempt protocol is unauthorized")
        normalized = {
            "protocol_version": RUNTIME_GENERATION_ATTEMPT_PROTOCOL_VERSION,
            "schema_version": 1,
            "attempt_id": require_stable_id(attempt.get("attempt_id"), "attempt id"),
            "grant_id": require_stable_id(attempt.get("grant_id"), "grant id"),
            "controller_spec_sha256": require_snapshot_hash(
                str(attempt.get("controller_spec_sha256", ""))
            ),
        }

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "IMPLEMENTING", "claim_runtime_generation_attempt")
            runtime = self._runtime_record(case, create=True)
            if runtime["action_grants"]:
                raise LimitError("runtime generation cannot start after a grant exists")
            if case.get("runtime_generation_abort") is not None:
                raise LimitError("runtime generation was already terminally aborted")
            if case.get("runtime_generation_attempt") is not None:
                raise LimitError("runtime generation attempt was already consumed")
            record = {
                **normalized,
                "status": "CLAIMED",
                "claimed_at": utc_now(),
                "finalized_at": None,
                "abort_reason_code": None,
            }
            record["record_sha256"] = canonical_json_sha256(record)
            case["runtime_generation_attempt"] = record
            return {
                "attempt_id": record["attempt_id"],
                "grant_id": record["grant_id"],
                "status": "CLAIMED",
                "record_sha256": record["record_sha256"],
            }

        return self._mutate(
            case_id,
            operation="claim_runtime_generation_attempt",
            payload={"attempt": normalized},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    def abort_runtime_generation_attempt(
        self,
        case_id: str,
        *,
        attempt_id: str,
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        normalized_attempt_id = require_stable_id(attempt_id, "attempt id")

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "IMPLEMENTING", "abort_runtime_generation_attempt")
            runtime = self._runtime_record(case, create=False)
            if runtime["action_grants"]:
                raise AuthorizationError(
                    "runtime generation attempt abort requires no canonical action grant"
                )
            attempt = case.get("runtime_generation_attempt")
            if not isinstance(attempt, dict) or attempt.get("status") != "CLAIMED":
                raise LimitError("runtime generation attempt is not claimable for abort")
            if attempt.get("attempt_id") != normalized_attempt_id:
                raise AuthorizationError("runtime generation attempt identity differs")
            attempt["status"] = "ABORTED"
            attempt["finalized_at"] = utc_now()
            attempt["abort_reason_code"] = "CONTROLLER_GENERATION_ABANDONED"
            attempt["record_sha256"] = canonical_json_sha256(
                {name: value for name, value in attempt.items() if name != "record_sha256"}
            )
            case["state"] = "CASE_LOCKED"
            case["resumable_state"] = None
            case["lock_reason"] = "CONTROLLER_GENERATION_ABANDONED"
            return {
                "attempt_id": normalized_attempt_id,
                "status": "ABORTED",
                "record_sha256": attempt["record_sha256"],
            }

        return self._mutate(
            case_id,
            operation="abort_runtime_generation_attempt",
            payload={"attempt_id": normalized_attempt_id},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    def abort_runtime_generation(
        self,
        case_id: str,
        *,
        evidence: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        expected_fields = {
            "protocol_version", "schema_version", "grant_id",
            "snapshot_event_sha256", "lockdown_intent_event_sha256",
            "acl_restored_event_sha256", "reason_code",
        }
        if not isinstance(evidence, Mapping) or set(evidence) != expected_fields:
            raise ValidationError(
                "runtime generation abort evidence must use the fixed schema"
            )
        if (
            evidence.get("protocol_version")
            != RUNTIME_GENERATION_ABORT_PROTOCOL_VERSION
            or evidence.get("schema_version") != 1
            or evidence.get("reason_code") != "PREISSUE_GENERATION_ABANDONED"
        ):
            raise AuthorizationError(
                "runtime generation abort protocol or reason is unauthorized"
            )
        normalized = {
            "protocol_version": RUNTIME_GENERATION_ABORT_PROTOCOL_VERSION,
            "schema_version": 1,
            "grant_id": require_stable_id(evidence.get("grant_id"), "grant id"),
            "snapshot_event_sha256": require_snapshot_hash(
                str(evidence.get("snapshot_event_sha256", ""))
            ),
            "lockdown_intent_event_sha256": require_snapshot_hash(
                str(evidence.get("lockdown_intent_event_sha256", ""))
            ),
            "acl_restored_event_sha256": require_snapshot_hash(
                str(evidence.get("acl_restored_event_sha256", ""))
            ),
            "reason_code": "PREISSUE_GENERATION_ABANDONED",
        }

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "IMPLEMENTING", "abort_runtime_generation")
            runtime = self._runtime_record(case, create=False)
            if runtime["action_grants"]:
                raise AuthorizationError(
                    "preissue generation abort requires no canonical action grant"
                )
            if case.get("runtime_generation_abort") is not None:
                raise LimitError("runtime generation was already terminally consumed")
            record = {
                **normalized,
                "aborted_at": utc_now(),
            }
            record["record_sha256"] = canonical_json_sha256(record)
            case["runtime_generation_abort"] = record
            case["state"] = "CASE_LOCKED"
            case["resumable_state"] = None
            case["lock_reason"] = "PREISSUE_GENERATION_ABANDONED"
            return {
                "grant_id": normalized["grant_id"],
                "reason_code": "PREISSUE_GENERATION_ABANDONED",
                "record_sha256": record["record_sha256"],
            }

        return self._mutate(
            case_id,
            operation="abort_runtime_generation",
            payload={"evidence": normalized},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    @staticmethod
    def _normalize_runtime_actor(actor: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(actor, Mapping) or set(actor) != {
            "protocol_version",
            "schema_version",
            "thread_id",
            "controller_assigned_role",
            "parent_thread_id",
            "agent_path",
            "identity_evidence_sha256",
            "binding_source",
        }:
            raise ValidationError("runtime actor must use the fixed ccos-runtime-actor-v1 schema")
        if actor.get("protocol_version") != RUNTIME_ACTOR_PROTOCOL_VERSION or actor.get("schema_version") != 1:
            raise ValidationError("runtime actor protocol or schema version is unsupported")
        thread_id = normalize_binding("thread", str(actor.get("thread_id", "")))
        role = _nonempty(actor.get("controller_assigned_role"), "controller assigned role", 64)
        if role not in RUNTIME_ACTOR_ROLES:
            raise ValidationError("controller assigned role is not a supported runtime role")
        raw_parent = actor.get("parent_thread_id")
        parent_thread_id = (
            None if raw_parent is None else normalize_binding("thread", str(raw_parent))
        )
        if role == "parent" and parent_thread_id is not None:
            raise ValidationError("runtime parent must not name a parent thread")
        if role != "parent" and parent_thread_id is None:
            raise ValidationError("runtime child must name its canonical parent thread")
        if actor.get("binding_source") != "native_thread_read":
            raise ValidationError("runtime actor binding source must be native_thread_read")
        return {
            "protocol_version": RUNTIME_ACTOR_PROTOCOL_VERSION,
            "schema_version": 1,
            "thread_id": thread_id,
            "role": role,
            "parent_thread_id": parent_thread_id,
            "agent_path": _nonempty(actor.get("agent_path"), "native agent path", 1024),
            "identity_evidence_sha256": require_snapshot_hash(
                str(actor.get("identity_evidence_sha256", ""))
            ),
            "binding_source": "native_thread_read",
        }

    def bind_runtime_actor(
        self,
        case_id: str,
        *,
        actor: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        normalized = self._normalize_runtime_actor(actor)

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            if case["state"] in {"CLOSED_SUCCESS", "CASE_LOCKED", "CONTROL_FAILURE"}:
                raise TransitionError(f"runtime actors cannot be bound while case is {case['state']}")
            if normalized["thread_id"] not in case["bindings"]["thread"]:
                raise AuthorizationError("runtime actor thread is not canonically bound to this case")
            runtime = self._runtime_record(case, create=True)
            actors = runtime["actors"]
            if normalized["thread_id"] in actors:
                raise ConflictError("runtime actor thread is already bound")
            if any(record.get("agent_path") == normalized["agent_path"] for record in actors.values()):
                raise ConflictError("native agent path is already bound to another runtime actor")
            role = normalized["role"]
            if role == "parent":
                if any(record.get("role") == "parent" for record in actors.values()):
                    raise LimitError("case already has its canonical runtime parent")
            else:
                parent = actors.get(normalized["parent_thread_id"])
                if not isinstance(parent, Mapping) or parent.get("role") != "parent":
                    raise AuthorizationError("runtime child parent is not the canonical bound parent")
                if role in {"implementer_child", "closure_child", "incomplete_child"} and any(
                    record.get("role") == role for record in actors.values()
                ):
                    raise LimitError(f"case already has its canonical {role}")
            recorded = {**normalized, "bound_at": utc_now()}
            recorded["actor_sha256"] = canonical_json_sha256(recorded)
            actors[normalized["thread_id"]] = recorded
            return {
                "thread_id": normalized["thread_id"],
                "controller_assigned_role": role,
                "actor_sha256": recorded["actor_sha256"],
            }

        return self._mutate(
            case_id,
            operation="bind_runtime_actor",
            payload={"actor": normalized},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    @staticmethod
    def _normalize_windows_principal_probe(
        evidence: Mapping[str, Any],
        *,
        worktree: str,
        worker_sid: str,
        sandbox_group_sid: str,
        broker_sid: str,
        base_head: str,
        protected_roots: Mapping[str, tuple[str, str, str]],
        expected_status_sha256: str = EMPTY_SHA256,
    ) -> dict[str, Any]:
        expected_fields = {
            "protocol_version", "schema_version", "challenge_id", "worker_principal_sid",
            "worker_identity_name", "worker_group_sids", "protected_roots", "head_before", "head_after",
            "status_sha256_before", "status_sha256_after", "observed_at",
        }
        if not isinstance(evidence, Mapping) or set(evidence) != expected_fields:
            raise ValidationError(
                "principal probe must use a fixed supported schema"
            )
        protocol_version = evidence.get("protocol_version")
        if (
            protocol_version
            not in {
                WINDOWS_PRINCIPAL_PROBE_PROTOCOL_VERSION_V1,
                WINDOWS_PRINCIPAL_PROBE_PROTOCOL_VERSION,
            }
            or evidence.get("schema_version") != 1
        ):
            raise ValidationError("principal probe protocol or schema version is unsupported")
        observed_worker = require_windows_sid(
            evidence.get("worker_principal_sid"), "isolation worker principal SID"
        )
        if observed_worker != worker_sid:
            raise AuthorizationError("isolation evidence names a different worker principal")
        raw_group_sids = evidence.get("worker_group_sids")
        if not isinstance(raw_group_sids, list) or not raw_group_sids:
            raise ValidationError("principal probe worker_group_sids must be a nonempty array")
        worker_group_sids = sorted(
            {require_windows_sid(item, "worker group SID") for item in raw_group_sids}
        )
        if len(worker_group_sids) != len(raw_group_sids):
            raise ValidationError("principal probe worker_group_sids must be unique")
        if sandbox_group_sid not in worker_group_sids:
            raise AuthorizationError("principal probe does not prove sandbox-group membership")
        challenge_id = require_stable_id(evidence.get("challenge_id"), "isolation challenge id")
        raw_roots = evidence.get("protected_roots")
        if not isinstance(raw_roots, list) or len(raw_roots) != len(PROTECTED_ROOT_KINDS):
            raise ValidationError("worker isolation evidence must cover every protected root")
        normalized_roots: list[dict[str, Any]] = []
        observed_kinds: set[str] = set()
        root_fields = {
            "root_kind", "path", "owner_sid", "parent_path", "parent_owner_sid",
            "anchor_path", "anchor_sha256_before",
            "anchor_sha256_after", "probe_relative_path", "write_denial_error",
            "write_denial_native_code", "probe_absent_before", "probe_absent_after",
            "nested_probe_parent_path", "nested_probe_relative_path",
            "nested_write_denial_error", "nested_write_denial_native_code",
            "nested_probe_absent_before", "nested_probe_absent_after",
            "overwrite_denial_error", "overwrite_denial_native_code",
            "replace_capability_denial_error", "replace_capability_denial_native_code",
            "rename_capability_denial_error", "rename_capability_denial_native_code",
            "hard_link_relative_path", "hard_link_denial_error",
            "hard_link_denial_native_code", "hard_link_absent_before",
            "hard_link_absent_after", "anchor_identity_sha256_before",
            "anchor_identity_sha256_after",
            "acl_change_nonce", "acl_sddl_sha256_before", "acl_sddl_sha256_after",
            "change_permissions_denial_error", "change_permissions_denial_native_code",
            "take_ownership_denial_error", "take_ownership_denial_native_code",
            "delete_capability_denial_error", "delete_capability_denial_native_code",
        }
        hardlink_evidence_fields = {
            "anchor_hardlink_paths_before", "anchor_hardlink_paths_after",
            "anchor_transport_hardlink_paths_before",
            "anchor_transport_hardlink_paths_after",
        }
        if protocol_version == WINDOWS_PRINCIPAL_PROBE_PROTOCOL_VERSION:
            root_fields |= hardlink_evidence_fields
        for raw_root in raw_roots:
            if not isinstance(raw_root, Mapping) or set(raw_root) != root_fields:
                raise ValidationError("protected-root evidence must use the fixed schema")
            root_kind = str(raw_root.get("root_kind", ""))
            if root_kind not in PROTECTED_ROOT_KINDS or root_kind in observed_kinds:
                raise ValidationError("protected-root evidence kind is missing, duplicated, or unknown")
            expected_path, expected_anchor, expected_sha256 = protected_roots[root_kind]
            path = normalize_binding("worktree", str(raw_root.get("path", "")))
            anchor_path = normalize_action_path(raw_root.get("anchor_path"))
            if path != expected_path or anchor_path != expected_anchor:
                raise ValidationError(f"{root_kind} evidence differs from the controller-selected root")
            owner_sid = require_windows_sid(raw_root.get("owner_sid"), f"{root_kind} owner SID")
            if owner_sid != broker_sid or owner_sid == worker_sid:
                raise AuthorizationError(f"{root_kind} must be owned by the distinct broker principal")
            parent_path = normalize_binding("worktree", str(raw_root.get("parent_path", "")))
            parent_owner_sid = require_windows_sid(
                raw_root.get("parent_owner_sid"), f"{root_kind} parent owner SID"
            )
            expected_parent = normalize_binding("worktree", str(Path(expected_path).parent))
            if parent_path != expected_parent or parent_owner_sid != broker_sid:
                raise AuthorizationError(f"{root_kind} parent must be broker-owned and exact")
            before = require_snapshot_hash(str(raw_root.get("anchor_sha256_before", "")))
            after = require_snapshot_hash(str(raw_root.get("anchor_sha256_after", "")))
            if before != expected_sha256 or after != expected_sha256:
                raise AuthorizationError(f"worker probe changed the {root_kind} anchor")
            expected_probe = (
                f".ccos-worker-{root_kind.replace('_root', '')}-probe-"
                + hashlib.sha256(challenge_id.encode("utf-8")).hexdigest()[:20]
            )
            probe_path = normalize_action_path(raw_root.get("probe_relative_path"))
            if probe_path != expected_probe:
                raise ValidationError(f"{root_kind} probe path is not challenge-derived")
            nested_parent_path = normalize_action_path(raw_root.get("nested_probe_parent_path"))
            root_path = Path(expected_path)
            nested_parent = root_path.joinpath(*PurePosixPath(nested_parent_path).parts)
            if (not path_is_within(nested_parent, root_path) or not nested_parent.is_dir()
                    or nested_parent == root_path
                    or path_contains_link_or_reparse(nested_parent, stop=root_path)):
                raise AuthorizationError(
                    f"{root_kind} nested write probe parent is not an exact direct descendant"
                )
            expected_nested_probe = (
                nested_parent_path
                + "/.ccos-worker-nested-probe-"
                + hashlib.sha256((challenge_id + ":" + root_kind).encode("utf-8")).hexdigest()[:20]
            )
            nested_probe_path = normalize_action_path(raw_root.get("nested_probe_relative_path"))
            if nested_probe_path != expected_nested_probe:
                raise ValidationError(f"{root_kind} nested probe path is not challenge-derived")
            expected_acl_nonce = hashlib.sha256(
                (challenge_id + ":" + root_kind + ":acl").encode("utf-8")
            ).hexdigest()
            if raw_root.get("acl_change_nonce") != expected_acl_nonce:
                raise ValidationError(f"{root_kind} ACL probe nonce is not challenge-derived")
            expected_hard_link = (
                f".ccos-worker-{root_kind.replace('_root', '')}-hard-link-"
                + hashlib.sha256(
                    (challenge_id + ":" + root_kind + ":link").encode("utf-8")
                ).hexdigest()[:20]
            )
            hard_link_path = normalize_action_path(raw_root.get("hard_link_relative_path"))
            if hard_link_path != expected_hard_link:
                raise ValidationError(f"{root_kind} hard-link path is not challenge-derived")
            identity_before = require_snapshot_hash(
                str(raw_root.get("anchor_identity_sha256_before", ""))
            )
            identity_after = require_snapshot_hash(
                str(raw_root.get("anchor_identity_sha256_after", ""))
            )
            if identity_before != identity_after:
                raise AuthorizationError(
                    f"worker operation probes changed the {root_kind} anchor identity"
                )
            normalized_hardlink_evidence: dict[str, list[str]] = {}
            if protocol_version == WINDOWS_PRINCIPAL_PROBE_PROTOCOL_VERSION:
                canonical_anchor = normalize_binding(
                    "worktree",
                    str(root_path.joinpath(*PurePosixPath(anchor_path).parts)),
                )

                def normalize_hardlink_paths(field: str, maximum: int) -> list[str]:
                    raw_paths = raw_root.get(field)
                    if (
                        not isinstance(raw_paths, list)
                        or not 0 <= len(raw_paths) <= maximum
                        or any(not isinstance(item, str) for item in raw_paths)
                    ):
                        raise ValidationError(
                            f"{root_kind} {field} must be a bounded path array"
                        )
                    paths = [normalize_binding("worktree", item) for item in raw_paths]
                    if len(set(paths)) != len(paths):
                        raise ValidationError(
                            f"{root_kind} {field} must contain unique exact paths"
                        )
                    return sorted(paths)

                hardlinks_before = normalize_hardlink_paths(
                    "anchor_hardlink_paths_before", 2
                )
                hardlinks_after = normalize_hardlink_paths(
                    "anchor_hardlink_paths_after", 2
                )
                transport_before = normalize_hardlink_paths(
                    "anchor_transport_hardlink_paths_before", 1
                )
                transport_after = normalize_hardlink_paths(
                    "anchor_transport_hardlink_paths_after", 1
                )
                for timing, hardlinks, transports in (
                    ("before", hardlinks_before, transport_before),
                    ("after", hardlinks_after, transport_after),
                ):
                    if canonical_anchor not in hardlinks or len(hardlinks) not in {1, 2}:
                        raise AuthorizationError(
                            f"{root_kind} {timing} hardlinks omit the canonical anchor"
                        )
                    extras = sorted(
                        path for path in hardlinks if path != canonical_anchor
                    )
                    if transports != extras:
                        raise AuthorizationError(
                            f"{root_kind} {timing} transport hardlinks are not exact"
                        )
                    if any(
                        path[:2].casefold() != canonical_anchor[:2].casefold()
                        or not re.fullmatch(
                            r"[a-z]:/work/\.tmp\.driveupload/[0-9]+",
                            path,
                            re.IGNORECASE,
                        )
                        for path in transports
                    ):
                        raise AuthorizationError(
                            f"{root_kind} {timing} hardlink escaped the exact transport root"
                        )
                normalized_hardlink_evidence = {
                    "anchor_hardlink_paths_before": hardlinks_before,
                    "anchor_hardlink_paths_after": hardlinks_after,
                    "anchor_transport_hardlink_paths_before": transport_before,
                    "anchor_transport_hardlink_paths_after": transport_after,
                }
            sddl_before = require_snapshot_hash(
                str(raw_root.get("acl_sddl_sha256_before", ""))
            )
            sddl_after = require_snapshot_hash(
                str(raw_root.get("acl_sddl_sha256_after", ""))
            )
            if sddl_before != sddl_after:
                raise AuthorizationError(f"worker ACL probes changed the {root_kind} security descriptor")
            denial_pairs = (
                ("write_denial_error", "write_denial_native_code"),
                ("nested_write_denial_error", "nested_write_denial_native_code"),
                ("overwrite_denial_error", "overwrite_denial_native_code"),
                ("replace_capability_denial_error", "replace_capability_denial_native_code"),
                ("rename_capability_denial_error", "rename_capability_denial_native_code"),
                ("hard_link_denial_error", "hard_link_denial_native_code"),
                ("change_permissions_denial_error", "change_permissions_denial_native_code"),
                ("take_ownership_denial_error", "take_ownership_denial_native_code"),
                ("delete_capability_denial_error", "delete_capability_denial_native_code"),
            )
            if any(raw_root.get(error_name) != "ACCESS_DENIED" or raw_root.get(code_name) != 5
                   for error_name, code_name in denial_pairs):
                raise AuthorizationError(
                    f"{root_kind} create, nested-create, overwrite, replace, rename, hard-link, permission, ownership, and delete probes must all be denied"
                )
            if (raw_root.get("probe_absent_before") is not True
                    or raw_root.get("probe_absent_after") is not True
                    or raw_root.get("nested_probe_absent_before") is not True
                    or raw_root.get("nested_probe_absent_after") is not True
                    or raw_root.get("hard_link_absent_before") is not True
                    or raw_root.get("hard_link_absent_after") is not True):
                raise AuthorizationError(f"worker write probe changed {root_kind}")
            normalized_roots.append({
                "root_kind": root_kind,
                "path": path,
                "owner_sid": owner_sid,
                "parent_path": parent_path,
                "parent_owner_sid": parent_owner_sid,
                "anchor_path": anchor_path,
                "anchor_sha256_before": before,
                "anchor_sha256_after": after,
                "probe_relative_path": probe_path,
                "write_denial_error": "ACCESS_DENIED",
                "write_denial_native_code": 5,
                "probe_absent_before": True,
                "probe_absent_after": True,
                "nested_probe_parent_path": nested_parent_path,
                "nested_probe_relative_path": nested_probe_path,
                "nested_write_denial_error": "ACCESS_DENIED",
                "nested_write_denial_native_code": 5,
                "nested_probe_absent_before": True,
                "nested_probe_absent_after": True,
                "overwrite_denial_error": "ACCESS_DENIED",
                "overwrite_denial_native_code": 5,
                "replace_capability_denial_error": "ACCESS_DENIED",
                "replace_capability_denial_native_code": 5,
                "rename_capability_denial_error": "ACCESS_DENIED",
                "rename_capability_denial_native_code": 5,
                "hard_link_relative_path": hard_link_path,
                "hard_link_denial_error": "ACCESS_DENIED",
                "hard_link_denial_native_code": 5,
                "hard_link_absent_before": True,
                "hard_link_absent_after": True,
                "anchor_identity_sha256_before": identity_before,
                "anchor_identity_sha256_after": identity_after,
                **normalized_hardlink_evidence,
                "acl_change_nonce": expected_acl_nonce,
                "acl_sddl_sha256_before": sddl_before,
                "acl_sddl_sha256_after": sddl_after,
                "change_permissions_denial_error": "ACCESS_DENIED",
                "change_permissions_denial_native_code": 5,
                "take_ownership_denial_error": "ACCESS_DENIED",
                "take_ownership_denial_native_code": 5,
                "delete_capability_denial_error": "ACCESS_DENIED",
                "delete_capability_denial_native_code": 5,
            })
            observed_kinds.add(root_kind)
        normalized_roots.sort(key=lambda item: PROTECTED_ROOT_KINDS.index(item["root_kind"]))
        head_before = require_sha(str(evidence.get("head_before", "")), "probe head before")
        head_after = require_sha(str(evidence.get("head_after", "")), "probe head after")
        if head_before != base_head or head_after != base_head:
            raise AuthorizationError("worker write probe observed Git HEAD drift")
        expected_status_sha256 = require_snapshot_hash(expected_status_sha256)
        status_before = require_snapshot_hash(str(evidence.get("status_sha256_before", "")))
        status_after = require_snapshot_hash(str(evidence.get("status_sha256_after", "")))
        if status_before != expected_status_sha256 or status_after != expected_status_sha256:
            raise AuthorizationError(
                "worker write probe changed the exact expected Git status"
            )
        return {
            "protocol_version": protocol_version,
            "schema_version": 1,
            "challenge_id": challenge_id,
            "worker_principal_sid": observed_worker,
            "worker_identity_name": _nonempty(
                evidence.get("worker_identity_name"), "worker identity name", 256
            ),
            "worker_group_sids": worker_group_sids,
            "protected_roots": normalized_roots,
            "head_before": head_before,
            "head_after": head_after,
            "status_sha256_before": status_before,
            "status_sha256_after": status_after,
            "observed_at": require_utc_timestamp(evidence.get("observed_at"), "probe observed_at"),
        }

    @staticmethod
    def _normalize_sandbox_membership_evidence(
        evidence: Mapping[str, Any],
        *,
        app_server_sid: str,
        model_sandbox_sid: str,
        sandbox_group_sid: str,
        online_role: str = "app_server_host",
        offline_role: str = "model_sandbox",
    ) -> dict[str, Any]:
        expected_fields = {
            "protocol_version", "schema_version", "sandbox_group_sid", "members",
            "observed_at",
        }
        if not isinstance(evidence, Mapping) or set(evidence) != expected_fields:
            raise ValidationError(
                "sandbox membership evidence must use the fixed ccos-windows-sandbox-membership-v1 schema"
            )
        if (evidence.get("protocol_version") != WINDOWS_GROUP_MEMBERSHIP_PROTOCOL_VERSION
                or evidence.get("schema_version") != 1):
            raise ValidationError("sandbox membership protocol or schema version is unsupported")
        observed_group = require_windows_sid(
            evidence.get("sandbox_group_sid"), "sandbox group SID"
        )
        if observed_group != sandbox_group_sid:
            raise AuthorizationError("membership evidence names a different sandbox group")
        raw_members = evidence.get("members")
        if not isinstance(raw_members, list) or len(raw_members) != 2:
            raise ValidationError("membership evidence must contain the Online and Offline principals")
        online_role = require_stable_id(online_role, "online principal role")
        offline_role = require_stable_id(offline_role, "offline principal role")
        if online_role == offline_role:
            raise ValidationError("Online and Offline principal roles must differ")
        role_order = (online_role, offline_role)
        expected_members = {
            online_role: app_server_sid,
            offline_role: model_sandbox_sid,
        }
        normalized_members: list[dict[str, Any]] = []
        observed_roles: set[str] = set()
        for raw_member in raw_members:
            if not isinstance(raw_member, Mapping) or set(raw_member) != {
                "principal_role", "principal_sid", "group_sids"
            }:
                raise ValidationError("sandbox membership member uses an unexpected schema")
            role = str(raw_member.get("principal_role", ""))
            if role not in expected_members or role in observed_roles:
                raise ValidationError("sandbox membership role is missing, duplicated, or unknown")
            principal_sid = require_windows_sid(
                raw_member.get("principal_sid"), f"{role} principal SID"
            )
            if principal_sid != expected_members[role]:
                raise AuthorizationError(f"membership evidence names a different {role} principal")
            raw_groups = raw_member.get("group_sids")
            if not isinstance(raw_groups, list) or not raw_groups:
                raise ValidationError(f"{role} membership group_sids must be a nonempty array")
            group_sids = sorted(
                {require_windows_sid(item, f"{role} group SID") for item in raw_groups}
            )
            if len(group_sids) != len(raw_groups):
                raise ValidationError(f"{role} membership group_sids must be unique")
            if sandbox_group_sid not in group_sids:
                raise AuthorizationError(f"{role} is not proven as a sandbox-group member")
            normalized_members.append({
                "principal_role": role,
                "principal_sid": principal_sid,
                "group_sids": group_sids,
            })
            observed_roles.add(role)
        normalized_members.sort(
            key=lambda item: role_order.index(item["principal_role"])
        )
        return {
            "protocol_version": WINDOWS_GROUP_MEMBERSHIP_PROTOCOL_VERSION,
            "schema_version": 1,
            "sandbox_group_sid": sandbox_group_sid,
            "members": normalized_members,
            "observed_at": require_utc_timestamp(
                evidence.get("observed_at"), "membership observed_at"
            ),
        }

    @classmethod
    def _normalize_windows_isolation_evidence(
        cls,
        evidence: Mapping[str, Any],
        *,
        worktree: str,
        app_server_sid: str,
        model_sandbox_sid: str,
        sandbox_group_sid: str,
        denied_principal_sids: list[str],
        broker_sid: str,
        base_head: str,
        protected_roots: Mapping[str, tuple[str, str, str]],
        membership_sha256: str,
        membership_evidence: Mapping[str, Any],
        expected_status_sha256: str = EMPTY_SHA256,
        online_role: str = "app_server_host",
        offline_role: str = "model_sandbox",
    ) -> dict[str, Any]:
        expected_fields = {
            "protocol_version", "schema_version", "denied_principal_sids",
            "membership_evidence_sha256", "principal_probes", "combined_probe_sha256",
        }
        if not isinstance(evidence, Mapping) or set(evidence) != expected_fields:
            raise ValidationError(
                "isolation evidence must use the fixed ccos-windows-isolation-evidence-v2 schema"
            )
        if (evidence.get("protocol_version") != WINDOWS_ISOLATION_EVIDENCE_PROTOCOL_VERSION
                or evidence.get("schema_version") != 2):
            raise ValidationError("isolation evidence protocol or schema version is unsupported")
        if evidence.get("denied_principal_sids") != denied_principal_sids:
            raise AuthorizationError("isolation evidence denied principals differ from the grant")
        if evidence.get("membership_evidence_sha256") != membership_sha256:
            raise AuthorizationError("isolation evidence membership digest differs from the grant")
        raw_probes = evidence.get("principal_probes")
        if not isinstance(raw_probes, list) or len(raw_probes) != 2:
            raise ValidationError("isolation evidence must contain exactly Online and Offline probes")
        online_role = require_stable_id(online_role, "online principal role")
        offline_role = require_stable_id(offline_role, "offline principal role")
        if online_role == offline_role:
            raise ValidationError("Online and Offline principal roles must differ")
        role_order = (online_role, offline_role)
        expected = {
            online_role: app_server_sid,
            offline_role: model_sandbox_sid,
        }
        normalized_probes: list[dict[str, Any]] = []
        observed: set[str] = set()
        membership_groups = {
            item["principal_role"]: item["group_sids"]
            for item in membership_evidence["members"]
        }
        for raw in raw_probes:
            if not isinstance(raw, Mapping) or set(raw) != {
                "principal_role", "principal_sid", "probe"
            }:
                raise ValidationError("principal probe envelope uses an unexpected schema")
            role = str(raw.get("principal_role", ""))
            if role not in expected or role in observed:
                raise ValidationError("principal probe role is missing, duplicated, or unknown")
            principal_sid = require_windows_sid(
                raw.get("principal_sid"), f"{role} probe SID"
            )
            if principal_sid != expected[role]:
                raise AuthorizationError(f"{role} probe envelope names a different principal")
            normalized_probe = cls._normalize_windows_principal_probe(
                raw.get("probe"),
                worktree=worktree,
                worker_sid=principal_sid,
                sandbox_group_sid=sandbox_group_sid,
                broker_sid=broker_sid,
                base_head=base_head,
                protected_roots=protected_roots,
                expected_status_sha256=expected_status_sha256,
            )
            if normalized_probe["worker_group_sids"] != membership_groups[role]:
                raise AuthorizationError(
                    f"{role} probe token groups differ from membership evidence"
                )
            normalized_probes.append({
                "principal_role": role,
                "principal_sid": principal_sid,
                "probe": normalized_probe,
            })
            observed.add(role)
        normalized_probes.sort(
            key=lambda item: role_order.index(item["principal_role"])
        )
        if len({
            item["probe"]["protocol_version"] for item in normalized_probes
        }) != 1:
            raise ValidationError(
                "Online and Offline principal probes must use one protocol version"
            )
        combined_body = {
            "denied_principal_sids": denied_principal_sids,
            "membership_evidence_sha256": membership_sha256,
            "principal_probes": normalized_probes,
        }
        combined_sha256 = require_snapshot_hash(
            str(evidence.get("combined_probe_sha256", ""))
        )
        if combined_sha256 != canonical_json_sha256(combined_body):
            raise AuthorizationError("isolation evidence combined probe digest is invalid")
        return {
            "protocol_version": WINDOWS_ISOLATION_EVIDENCE_PROTOCOL_VERSION,
            "schema_version": 2,
            **combined_body,
            "combined_probe_sha256": combined_sha256,
        }

    @staticmethod
    def _normalize_protected_acl_snapshot(
        snapshot: Any, *, expected_paths: set[str]
    ) -> list[dict[str, str]]:
        if not isinstance(snapshot, list) or len(snapshot) != len(expected_paths):
            raise ValidationError(
                "protected ACL snapshot must cover every unique root and parent"
            )
        normalized: list[dict[str, str]] = []
        observed: set[str] = set()
        for raw in snapshot:
            if not isinstance(raw, Mapping) or set(raw) != {
                "path", "owner_sid", "sddl", "sddl_sha256", "entry_sha256"
            }:
                raise ValidationError("protected ACL snapshot entry uses an unexpected schema")
            path = normalize_binding("worktree", str(raw.get("path", "")))
            if path not in expected_paths or path in observed:
                raise ValidationError("protected ACL snapshot path is missing, duplicated, or unknown")
            sddl = str(raw.get("sddl", ""))
            if not sddl or len(sddl) > 262144:
                raise ValidationError("protected ACL snapshot SDDL is invalid")
            sddl_sha256 = require_snapshot_hash(str(raw.get("sddl_sha256", "")))
            if hashlib.sha256(sddl.encode("utf-8")).hexdigest() != sddl_sha256:
                raise AuthorizationError("protected ACL snapshot SDDL digest is invalid")
            entry = {
                "path": path,
                "owner_sid": require_windows_sid(raw.get("owner_sid"), "ACL owner SID"),
                "sddl": sddl,
                "sddl_sha256": sddl_sha256,
            }
            entry_sha256 = require_snapshot_hash(str(raw.get("entry_sha256", "")))
            if entry_sha256 != canonical_json_sha256(entry):
                raise AuthorizationError("protected ACL snapshot entry digest is invalid")
            entry["entry_sha256"] = entry_sha256
            normalized.append(entry)
            observed.add(path)
        normalized.sort(
            key=lambda item: (len(Path(item["path"]).parts), item["path"].casefold()),
            reverse=True,
        )
        return normalized

    @staticmethod
    def _normalize_action_grant_request(grant: Mapping[str, Any]) -> dict[str, Any]:
        expected_fields = {
            "protocol_version", "schema_version", "grant_id", "actor_thread_id",
            "actor_turn_id", "native_turn_evidence_sha256", "controller_receipt_sha256",
            "operation_id", "action",
            "operation", "repository", "branch", "worktree", "base_head", "target_path",
            "baseline_sha256", "replacement_sha256", "proposal_artifact_path",
            "proposal_size", "worker_principal_sid", "model_worker_principal_sid",
            "sandbox_group_principal_sid", "denied_principal_sids",
            "broker_principal_sid", "group_membership_evidence",
            "app_server_sha256", "app_server_version", "schema_file_count",
            "app_server_executable_path",
            "schema_tree_sha256", "sandbox_profile_sha256",
            "app_server_environment_sha256",
            "live_controller_evidence", "live_controller_evidence_sha256",
            "worker_runtime_root", "protected_acl_snapshot",
            "protected_acl_snapshot_sha256", "preissue_dacl_evidence",
            "preissue_dacl_evidence_sha256", "isolation_evidence", "expires_at",
        }
        if not isinstance(grant, Mapping) or set(grant) != expected_fields:
            raise ValidationError(
                "action grant must use the fixed ccos-runtime-action-grant-v1 request schema"
            )
        if grant.get("protocol_version") != ACTION_GRANT_PROTOCOL_VERSION or grant.get("schema_version") != 1:
            raise ValidationError("action grant protocol or schema version is unsupported")
        if grant.get("action") != "implementation" or grant.get("operation") != "replace_existing_file_v1":
            raise AuthorizationError("only the exact implementation file-replacement operation is supported")
        worktree_path, worktree = normalized_absolute_path(
            grant.get("worktree"), "grant worktree", reject_links=True
        )
        proposal_path, proposal = normalized_absolute_path(
            grant.get("proposal_artifact_path"),
            "proposal artifact path",
            reject_links=True,
        )
        worker_runtime_path, worker_runtime_root = normalized_absolute_path(
            grant.get("worker_runtime_root"),
            "worker runtime root",
            reject_links=True,
        )
        if not worker_runtime_path.is_dir() or path_contains_link_or_reparse(worker_runtime_path):
            raise AuthorizationError("worker runtime root must be an exact direct directory")
        if path_is_within(proposal_path, worktree_path):
            raise AuthorizationError("proposal artifact must be outside the authorized worktree")
        if path_contains_link_or_reparse(proposal_path):
            raise AuthorizationError("proposal artifact path must not traverse a link or reparse point")
        raw_size = grant.get("proposal_size")
        if isinstance(raw_size, bool) or not isinstance(raw_size, int):
            raise ValidationError("proposal_size must be an integer byte count")
        if raw_size < 0 or raw_size > MAX_REPLACEMENT_BYTES:
            raise ValidationError(
                f"proposal_size must be between 0 and {MAX_REPLACEMENT_BYTES} bytes"
            )
        worker_sid = require_windows_sid(grant.get("worker_principal_sid"), "worker principal SID")
        model_worker_sid = require_windows_sid(
            grant.get("model_worker_principal_sid"), "model worker principal SID"
        )
        sandbox_group_sid = require_windows_sid(
            grant.get("sandbox_group_principal_sid"), "sandbox group principal SID"
        )
        broker_sid = require_windows_sid(grant.get("broker_principal_sid"), "broker principal SID")
        denied_principal_sids = grant.get("denied_principal_sids")
        expected_denied_sids = [worker_sid, model_worker_sid, sandbox_group_sid]
        if denied_principal_sids != expected_denied_sids or len(set(expected_denied_sids)) != 3:
            raise AuthorizationError(
                "denied_principal_sids must be the exact ordered Online, Offline, and sandbox-group SIDs"
            )
        if broker_sid in expected_denied_sids:
            raise AuthorizationError("sandbox workers, group, and broker must be distinct principals")
        baseline_sha256 = require_snapshot_hash(str(grant.get("baseline_sha256", "")))
        replacement_sha256 = require_snapshot_hash(str(grant.get("replacement_sha256", "")))
        if baseline_sha256 == replacement_sha256:
            raise AuthorizationError("action grant replacement must differ from the exact baseline")
        schema_file_count = grant.get("schema_file_count")
        if (isinstance(schema_file_count, bool) or not isinstance(schema_file_count, int)
                or schema_file_count <= 0):
            raise ValidationError("schema_file_count must be a positive integer")
        app_server_sha256 = require_snapshot_hash(
            str(grant.get("app_server_sha256", ""))
        )
        app_server_version = require_stable_id(
            grant.get("app_server_version"), "App Server version"
        )
        app_server_environment_sha256 = require_snapshot_hash(
            str(grant.get("app_server_environment_sha256", ""))
        )
        live_controller_evidence = normalize_live_controller_evidence(
            grant.get("live_controller_evidence"),
            worker_sid=worker_sid,
            broker_sid=broker_sid,
            app_server_sha256=app_server_sha256,
            app_server_version=app_server_version,
            environment_sha256=app_server_environment_sha256,
        )
        live_controller_evidence_sha256 = require_snapshot_hash(
            str(grant.get("live_controller_evidence_sha256", ""))
        )
        if live_controller_evidence_sha256 != canonical_json_sha256(
            live_controller_evidence
        ):
            raise AuthorizationError("live controller evidence digest differs")
        return {
            "protocol_version": ACTION_GRANT_PROTOCOL_VERSION,
            "schema_version": 1,
            "grant_id": require_stable_id(grant.get("grant_id"), "grant id"),
            "actor_thread_id": normalize_binding("thread", str(grant.get("actor_thread_id", ""))),
            "actor_turn_id": require_stable_id(grant.get("actor_turn_id"), "actor turn id"),
            "native_turn_evidence_sha256": require_snapshot_hash(
                str(grant.get("native_turn_evidence_sha256", ""))
            ),
            "controller_receipt_sha256": require_snapshot_hash(
                str(grant.get("controller_receipt_sha256", ""))
            ),
            "operation_id": require_stable_id(grant.get("operation_id"), "operation id"),
            "action": "implementation",
            "operation": "replace_existing_file_v1",
            "repository": normalize_repo_url(str(grant.get("repository", ""))),
            "branch": normalize_binding("branch", str(grant.get("branch", ""))),
            "worktree": worktree,
            "base_head": require_sha(str(grant.get("base_head", "")), "grant base head"),
            "target_path": normalize_action_path(grant.get("target_path")),
            "baseline_sha256": baseline_sha256,
            "replacement_sha256": replacement_sha256,
            "proposal_artifact_path": proposal,
            "proposal_size": raw_size,
            "worker_runtime_root": worker_runtime_root,
            "worker_principal_sid": worker_sid,
            "model_worker_principal_sid": model_worker_sid,
            "sandbox_group_principal_sid": sandbox_group_sid,
            "denied_principal_sids": expected_denied_sids,
            "broker_principal_sid": broker_sid,
            "app_server_sha256": app_server_sha256,
            "app_server_executable_path": normalized_absolute_path(
                grant.get("app_server_executable_path"),
                "App Server executable",
                reject_links=True,
            )[1],
            "app_server_version": app_server_version,
            "schema_file_count": schema_file_count,
            "schema_tree_sha256": require_snapshot_hash(
                str(grant.get("schema_tree_sha256", ""))
            ),
            "sandbox_profile_sha256": require_snapshot_hash(
                str(grant.get("sandbox_profile_sha256", ""))
            ),
            "app_server_environment_sha256": app_server_environment_sha256,
            "live_controller_evidence": live_controller_evidence,
            "live_controller_evidence_sha256": live_controller_evidence_sha256,
            "group_membership_evidence": copy.deepcopy(
                grant.get("group_membership_evidence")
            ),
            "protected_acl_snapshot": copy.deepcopy(
                grant.get("protected_acl_snapshot")
            ),
            "protected_acl_snapshot_sha256": require_snapshot_hash(
                str(grant.get("protected_acl_snapshot_sha256", ""))
            ),
            "preissue_dacl_evidence": copy.deepcopy(
                grant.get("preissue_dacl_evidence")
            ),
            "preissue_dacl_evidence_sha256": require_snapshot_hash(
                str(grant.get("preissue_dacl_evidence_sha256", ""))
            ),
            "isolation_evidence": copy.deepcopy(grant.get("isolation_evidence")),
            "expires_at": require_utc_timestamp(grant.get("expires_at"), "grant expires_at"),
        }

    def issue_action_grant(
        self,
        case_id: str,
        *,
        grant: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
        allow_legacy_v1: bool = False,
    ) -> dict[str, Any]:
        if allow_legacy_v1 is not True:
            raise AuthorizationError(
                "new ccos-runtime-action-grant-v1 issuance is disabled; use the actorless proposal action grant"
            )
        normalized = self._normalize_action_grant_request(grant)

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "IMPLEMENTING", "issue_action_grant")
            runtime = self._runtime_record(case, create=True)
            if runtime["action_grants"]:
                raise LimitError("case already contains its one exact runtime action grant")
            generation_attempt = case.get("runtime_generation_attempt")
            if (
                not isinstance(generation_attempt, dict)
                or generation_attempt.get("status") != "CLAIMED"
                or generation_attempt.get("grant_id") != normalized["grant_id"]
            ):
                raise AuthorizationError(
                    "action grant requires the exact durable runtime generation attempt"
                )
            actor = runtime["actors"].get(normalized["actor_thread_id"])
            if not isinstance(actor, Mapping) or actor.get("role") != "implementer_child":
                raise AuthorizationError(
                    "action grant actor must be the controller-bound implementation child"
                )
            live_evidence = normalized["live_controller_evidence"]
            if live_evidence["case_id"] != case["case_id"]:
                raise AuthorizationError("live controller evidence belongs to another case")
            incomplete_thread = live_evidence["incomplete_child_evidence"]["thread_id"]
            incomplete_actor = runtime["actors"].get(incomplete_thread)
            if (
                not isinstance(incomplete_actor, Mapping)
                or incomplete_actor.get("role") != "incomplete_child"
            ):
                raise AuthorizationError(
                    "live controller incomplete evidence is not bound to the incomplete child"
                )
            repository = normalized["repository"]
            branch = normalized["branch"]
            worktree_path = Path(normalized["worktree"])
            if repository not in case["bindings"]["repo_url"]:
                raise AuthorizationError("action grant repository is not canonically bound to this case")
            if {"repository": repository, "value": branch} not in case["bindings"]["branch"]:
                raise AuthorizationError("action grant branch is not canonically bound to this case")
            if normalized["worktree"] not in case["bindings"]["worktree"]:
                raise AuthorizationError("action grant worktree is not canonically bound to this case")
            try:
                exact_root = worktree_path.resolve(strict=True)
            except OSError as exc:
                raise ValidationError(f"grant worktree cannot be resolved: {exc}") from exc
            if not exact_root.is_dir() or _git_repository_root(exact_root) != exact_root:
                raise AuthorizationError("action grant worktree must be the exact Git repository root")
            if path_contains_link_or_reparse(exact_root):
                raise AuthorizationError("action grant worktree must not traverse a link or reparse point")
            if _git_origin(exact_root) != repository:
                raise AuthorizationError("Git origin differs from the canonical repository binding")
            if _git_branch(exact_root) != branch:
                raise AuthorizationError("Git branch differs from the canonical branch binding")
            if _git_head(exact_root) != normalized["base_head"]:
                raise AuthorizationError("Git HEAD differs from the exact action base head")
            app_server_executable = Path(normalized["app_server_executable_path"])
            if (not app_server_executable.is_file() or app_server_executable.is_symlink()
                    or path_contains_link_or_reparse(app_server_executable)
                    or file_sha256(app_server_executable) != normalized["app_server_sha256"]):
                raise AuthorizationError(
                    "App Server executable path or bytes differ from the signed grant"
                )
            _assert_git_worktree_clean(exact_root, "before action grant issuance")
            target = exact_root.joinpath(*PurePosixPath(normalized["target_path"]).parts)
            if not path_is_within(target, exact_root) or not target.is_file():
                raise AuthorizationError("action target must be an existing regular file in the worktree")
            if path_contains_link_or_reparse(target, stop=exact_root):
                raise AuthorizationError("action target must not traverse a link or reparse point")
            target_identity = regular_file_identity(target, stop=exact_root)
            target_mode = _git_tracked_mode(exact_root, normalized["target_path"])
            if file_sha256(target) != normalized["baseline_sha256"]:
                raise AuthorizationError("action target baseline digest differs from the exact grant")
            if target.stat().st_size > MAX_REPLACEMENT_BYTES:
                raise AuthorizationError("action target exceeds the bounded single-file primitive")
            proposal = Path(normalized["proposal_artifact_path"])
            if not proposal.is_file() or proposal.is_symlink():
                raise AuthorizationError("proposal artifact must remain a regular non-link file")
            proposal_identity = regular_file_identity(proposal)
            if proposal.stat().st_size != normalized["proposal_size"]:
                raise AuthorizationError("proposal artifact size differs from the exact grant")
            if file_sha256(proposal) != normalized["replacement_sha256"]:
                raise AuthorizationError("proposal artifact digest differs from the exact grant")
            if not self.path.is_file() or self.path.is_symlink():
                raise StoreCorruptionError("action grant issuance requires a regular canonical store")
            state_root_path = self.state_root.resolve(strict=True)
            broker_source_root_path = Path(__file__).resolve().parents[2]
            proposal_root_path = proposal.parent.resolve(strict=True)
            if not proposal_root_path.is_dir() or path_contains_link_or_reparse(proposal_root_path):
                raise AuthorizationError("proposal root must be an exact regular directory")
            protected_control_roots = {
                "target_root": exact_root,
                "state_root": state_root_path,
                "broker_source_root": broker_source_root_path,
                "proposal_root": proposal_root_path,
            }
            overlapping_roots = [
                (left_kind, right_kind)
                for index, (left_kind, left_path) in enumerate(
                    protected_control_roots.items()
                )
                for right_kind, right_path in list(protected_control_roots.items())[index + 1:]
                if (
                    left_path == right_path
                    or path_is_within(left_path, right_path)
                    or path_is_within(right_path, left_path)
                )
            ]
            if overlapping_roots:
                raise AuthorizationError(
                    "target, state, broker-source, and proposal roots must be pairwise "
                    "dedicated and nonoverlapping"
                )
            worker_runtime_path = Path(normalized["worker_runtime_root"]).resolve(strict=True)
            protected_roots_and_parents = {
                *protected_control_roots.values(),
                *(path.parent for path in protected_control_roots.values()),
            }
            if any(
                worker_runtime_path == protected_path
                or path_is_within(worker_runtime_path, protected_path)
                or path_is_within(protected_path, worker_runtime_path)
                for protected_path in protected_roots_and_parents
            ):
                raise AuthorizationError(
                    "worker runtime root must be dedicated and nonoverlapping with protected roots and parents"
                )
            proposal_anchor = normalize_action_path(proposal.name)
            broker_source_path = broker_source_root_path / "scripts" / "agent" / "case_runtime_broker.py"
            if not broker_source_path.is_file() or broker_source_path.is_symlink():
                raise StoreCorruptionError("runtime broker source must be a regular sealed controller file")
            state_root = normalize_binding("worktree", str(state_root_path))
            broker_source_root = normalize_binding("worktree", str(broker_source_root_path))
            proposal_root = normalize_binding("worktree", str(proposal_root_path))
            source_pins = controller_source_pins(broker_source_root_path)
            protected_roots = {
                "target_root": (
                    normalized["worktree"], normalized["target_path"], normalized["baseline_sha256"]
                ),
                "state_root": (state_root, STORE_FILENAME, file_sha256(self.path)),
                "broker_source_root": (
                    broker_source_root,
                    source_pins["manifest_path"],
                    source_pins["manifest_sha256"],
                ),
                "proposal_root": (
                    proposal_root,
                    proposal_anchor,
                    normalized["replacement_sha256"],
                ),
            }
            now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
            expires = dt.datetime.fromisoformat(normalized["expires_at"])
            remaining = (expires - now).total_seconds()
            if remaining <= 0 or remaining > MAX_ACTION_GRANT_LIFETIME_SECONDS:
                raise AuthorizationError(
                    "action grant expiry must be in the future and no more than 15 minutes away"
                )
            membership = self._normalize_sandbox_membership_evidence(
                normalized["group_membership_evidence"],
                app_server_sid=normalized["worker_principal_sid"],
                model_sandbox_sid=normalized["model_worker_principal_sid"],
                sandbox_group_sid=normalized["sandbox_group_principal_sid"],
            )
            membership_sha256 = canonical_json_sha256(membership)
            isolation = self._normalize_windows_isolation_evidence(
                normalized["isolation_evidence"],
                worktree=normalized["worktree"],
                app_server_sid=normalized["worker_principal_sid"],
                model_sandbox_sid=normalized["model_worker_principal_sid"],
                sandbox_group_sid=normalized["sandbox_group_principal_sid"],
                denied_principal_sids=normalized["denied_principal_sids"],
                broker_sid=normalized["broker_principal_sid"],
                base_head=normalized["base_head"],
                protected_roots=protected_roots,
                membership_sha256=membership_sha256,
                membership_evidence=membership,
            )
            isolation_sha256 = canonical_json_sha256(isolation)
            expected_acl_paths = {
                path
                for protected_path, _anchor, _digest in protected_roots.values()
                for path in (
                    protected_path,
                    normalize_binding("worktree", str(Path(protected_path).parent)),
                )
            }
            protected_acl_snapshot = self._normalize_protected_acl_snapshot(
                normalized["protected_acl_snapshot"],
                expected_paths=expected_acl_paths,
            )
            protected_acl_snapshot_sha256 = canonical_json_sha256(
                protected_acl_snapshot
            )
            if (
                protected_acl_snapshot_sha256
                != normalized["protected_acl_snapshot_sha256"]
            ):
                raise AuthorizationError("protected ACL snapshot digest differs")
            sealed_artifact_path, sealed_artifact_sha256, sealed_artifact_identity = self._seal_action_artifact_unlocked(
                case["case_id"],
                normalized["grant_id"],
                "replacement",
                proposal,
                expected_size=normalized["proposal_size"],
                expected_sha256=normalized["replacement_sha256"],
            )
            sealed_baseline_path, sealed_baseline_sha256, sealed_baseline_identity = self._seal_action_artifact_unlocked(
                case["case_id"],
                normalized["grant_id"],
                "baseline",
                target,
                expected_size=target.stat().st_size,
                expected_sha256=normalized["baseline_sha256"],
            )
            if regular_file_identity(proposal) != proposal_identity:
                raise AuthorizationError("proposal artifact identity changed while it was sealed")
            if regular_file_identity(target, stop=exact_root) != target_identity:
                raise AuthorizationError("action target identity changed while baseline bytes were sealed")
            allowed_paths = [normalized["target_path"]]
            issued_at = utc_now()
            recorded = {
                **{
                    name: value
                    for name, value in normalized.items()
                    if name not in {
                        "isolation_evidence", "group_membership_evidence",
                        "protected_acl_snapshot", "preissue_dacl_evidence",
                    }
                },
                "target_mode": target_mode,
                "target_file_identity": target_identity,
                "proposal_file_identity": proposal_identity,
                "state_root": state_root,
                "broker_source_root": broker_source_root,
                "proposal_root": proposal_root,
                "controller_source_pins": source_pins,
                "controller_source_pins_sha256": canonical_json_sha256(source_pins),
                "sealed_artifact_path": sealed_artifact_path,
                "sealed_artifact_sha256": sealed_artifact_sha256,
                "sealed_artifact_identity": sealed_artifact_identity,
                "sealed_baseline_path": sealed_baseline_path,
                "sealed_baseline_sha256": sealed_baseline_sha256,
                "sealed_baseline_identity": sealed_baseline_identity,
                "allowed_paths": allowed_paths,
                "allowed_paths_sha256": canonical_json_sha256(allowed_paths),
                "group_membership_evidence": membership,
                "group_membership_evidence_sha256": membership_sha256,
                "isolation_evidence": isolation,
                "isolation_evidence_sha256": isolation_sha256,
                "protected_acl_snapshot": protected_acl_snapshot,
                "protected_acl_snapshot_sha256": protected_acl_snapshot_sha256,
                "status": "ISSUED",
                "authorization_nonce": secrets.token_hex(32),
                "issued_at": issued_at,
                "issued_revision": case["revision"] + 1,
                "claim": None,
                "result": None,
            }
            preissue_dacl_evidence = self._normalize_dacl_evidence(
                normalized["preissue_dacl_evidence"], recorded
            )
            preissue_dacl_evidence_sha256 = canonical_json_sha256(
                preissue_dacl_evidence
            )
            if (
                preissue_dacl_evidence_sha256
                != normalized["preissue_dacl_evidence_sha256"]
            ):
                raise AuthorizationError("preissue DACL evidence digest differs")
            recorded["preissue_dacl_evidence"] = preissue_dacl_evidence
            recorded["preissue_dacl_evidence_sha256"] = preissue_dacl_evidence_sha256
            recorded["grant_sha256"] = canonical_json_sha256(recorded)
            runtime["action_grants"][normalized["grant_id"]] = recorded
            generation_attempt["status"] = "GRANT_ISSUED"
            generation_attempt["finalized_at"] = issued_at
            generation_attempt["record_sha256"] = canonical_json_sha256(
                {
                    name: value
                    for name, value in generation_attempt.items()
                    if name != "record_sha256"
                }
            )
            return {
                "grant_id": normalized["grant_id"],
                "actor_thread_id": normalized["actor_thread_id"],
                "status": "ISSUED",
                "issued_revision": recorded["issued_revision"],
                "grant_sha256": recorded["grant_sha256"],
                "allowed_paths_sha256": recorded["allowed_paths_sha256"],
                "isolation_evidence_sha256": isolation_sha256,
            }

        return self._mutate(
            case_id,
            operation="issue_action_grant",
            payload={"grant": normalized},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    @staticmethod
    def _normalize_proposal_action_grant_request(
        case_id: str, grant: Mapping[str, Any]
    ) -> dict[str, Any]:
        expected_fields = {
            "protocol_version", "schema_version", "evidence_mode", "grant_id", "authority_id",
            "operation_id", "action", "operation", "repository", "branch",
            "worktree", "base_head", "target_path", "baseline_sha256",
            "proposal_artifact_path", "proposal_artifact_sha256", "proposal_size",
            "replacement_sha256", "worker_principal_sid",
            "model_worker_principal_sid", "sandbox_group_principal_sid",
            "denied_principal_sids", "broker_principal_sid",
            "sandbox_executable_path", "sandbox_executable_sha256",
            "sandbox_executable_version", "probe_runtime_root",
            "protected_acl_snapshot",
            "protected_acl_snapshot_sha256", "preissue_dacl_evidence",
            "preissue_dacl_evidence_sha256", "expires_at",
            "authority", "authority_sha256",
        }
        if not isinstance(grant, Mapping) or set(grant) != expected_fields:
            raise ValidationError(
                "proposal action grant must use the fixed ccos-proposal-action-grant-v2 request schema"
            )
        if (
            grant.get("protocol_version") != PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION
            or grant.get("schema_version") != 2
        ):
            raise ValidationError(
                "proposal action grant protocol or schema version is unsupported"
            )
        if (
            grant.get("action") != "implementation"
            or grant.get("operation") != "replace_existing_file_v1"
        ):
            raise AuthorizationError(
                "proposal grants support only the exact implementation file-replacement operation"
            )
        if grant.get("evidence_mode") != PROPOSAL_DACL_EVIDENCE_MODE:
            raise AuthorizationError(
                "proposal grants require the exact broker_dacl_v1 evidence mode"
            )
        canonical_id = canonical_case_id(case_id)
        worktree_path, worktree = normalized_absolute_path(
            grant.get("worktree"), "proposal grant worktree", reject_links=True
        )
        proposal_path, proposal_artifact_path = normalized_absolute_path(
            grant.get("proposal_artifact_path"),
            "proposal artifact path",
            reject_links=True,
        )
        executable_path, sandbox_executable_path = normalized_absolute_path(
            grant.get("sandbox_executable_path"),
            "sandbox executable path",
            reject_links=True,
        )
        probe_runtime_path, probe_runtime_root = normalized_absolute_path(
            grant.get("probe_runtime_root"),
            "probe runtime root",
            reject_links=True,
        )
        if not probe_runtime_path.is_dir() or path_contains_link_or_reparse(
            probe_runtime_path
        ):
            raise AuthorizationError(
                "probe runtime root must be an exact regular directory"
            )
        if path_is_within(proposal_path, worktree_path):
            raise AuthorizationError(
                "proposal artifact must be outside the authorized worktree"
            )
        if path_contains_link_or_reparse(proposal_path):
            raise AuthorizationError(
                "proposal artifact path must not traverse a link or reparse point"
            )
        raw_size = grant.get("proposal_size")
        if isinstance(raw_size, bool) or not isinstance(raw_size, int):
            raise ValidationError("proposal_size must be an integer byte count")
        if raw_size < 0 or raw_size > MAX_REPLACEMENT_BYTES:
            raise ValidationError(
                f"proposal_size must be between 0 and {MAX_REPLACEMENT_BYTES} bytes"
            )
        worker_sid = require_windows_sid(
            grant.get("worker_principal_sid"), "Online worker principal SID"
        )
        model_worker_sid = require_windows_sid(
            grant.get("model_worker_principal_sid"),
            "Offline worker principal SID",
        )
        sandbox_group_sid = require_windows_sid(
            grant.get("sandbox_group_principal_sid"), "sandbox group principal SID"
        )
        broker_sid = require_windows_sid(
            grant.get("broker_principal_sid"), "broker principal SID"
        )
        denied_principal_sids = grant.get("denied_principal_sids")
        expected_denied_sids = [worker_sid, model_worker_sid, sandbox_group_sid]
        if (
            denied_principal_sids != expected_denied_sids
            or len(set(expected_denied_sids)) != 3
        ):
            raise AuthorizationError(
                "denied_principal_sids must be the exact ordered Online, Offline, and sandbox-group SIDs"
            )
        if broker_sid in expected_denied_sids:
            raise AuthorizationError(
                "sandbox workers, group, and broker must be distinct principals"
            )
        baseline_sha256 = require_snapshot_hash(
            str(grant.get("baseline_sha256", ""))
        )
        proposal_sha256 = require_snapshot_hash(
            str(grant.get("proposal_artifact_sha256", ""))
        )
        replacement_sha256 = require_snapshot_hash(
            str(grant.get("replacement_sha256", ""))
        )
        if proposal_sha256 != replacement_sha256:
            raise AuthorizationError(
                "proposal artifact and replacement digests must identify the same exact bytes"
            )
        if baseline_sha256 == replacement_sha256:
            raise AuthorizationError(
                "proposal action replacement must differ from the exact baseline"
            )
        normalized = {
            "protocol_version": PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION,
            "schema_version": 2,
            "evidence_mode": PROPOSAL_DACL_EVIDENCE_MODE,
            "grant_id": require_stable_id(grant.get("grant_id"), "grant id"),
            "authority_id": require_stable_id(
                grant.get("authority_id"), "authority id"
            ),
            "operation_id": require_stable_id(
                grant.get("operation_id"), "operation id"
            ),
            "action": "implementation",
            "operation": "replace_existing_file_v1",
            "repository": normalize_repo_url(str(grant.get("repository", ""))),
            "branch": normalize_binding("branch", str(grant.get("branch", ""))),
            "worktree": worktree,
            "base_head": require_sha(
                str(grant.get("base_head", "")), "proposal grant base head"
            ),
            "target_path": normalize_action_path(grant.get("target_path")),
            "baseline_sha256": baseline_sha256,
            "proposal_artifact_path": proposal_artifact_path,
            "proposal_artifact_sha256": proposal_sha256,
            "proposal_size": raw_size,
            "replacement_sha256": replacement_sha256,
            "worker_principal_sid": worker_sid,
            "model_worker_principal_sid": model_worker_sid,
            "sandbox_group_principal_sid": sandbox_group_sid,
            "denied_principal_sids": expected_denied_sids,
            "broker_principal_sid": broker_sid,
            "sandbox_executable_path": sandbox_executable_path,
            "sandbox_executable_sha256": require_snapshot_hash(
                str(grant.get("sandbox_executable_sha256", ""))
            ),
            "sandbox_executable_version": require_stable_id(
                grant.get("sandbox_executable_version"),
                "sandbox executable version",
            ),
            "probe_runtime_root": probe_runtime_root,
            "protected_acl_snapshot": copy.deepcopy(
                grant.get("protected_acl_snapshot")
            ),
            "protected_acl_snapshot_sha256": require_snapshot_hash(
                str(grant.get("protected_acl_snapshot_sha256", ""))
            ),
            "preissue_dacl_evidence": copy.deepcopy(
                grant.get("preissue_dacl_evidence")
            ),
            "preissue_dacl_evidence_sha256": require_snapshot_hash(
                str(grant.get("preissue_dacl_evidence_sha256", ""))
            ),
            "expires_at": require_utc_timestamp(
                grant.get("expires_at"), "grant expires_at"
            ),
        }
        if not executable_path.is_file() or executable_path.is_symlink():
            raise AuthorizationError(
                "sandbox executable must be an existing regular non-link file"
            )
        authority = grant.get("authority")
        authority_fields = {
            "protocol_version", "schema_version", "evidence_mode", "authority_id", "case_id",
            "expected_case_revision", "grant_id", "operation_id", "action",
            "operation", "repository", "branch", "worktree", "base_head",
            "target_path", "baseline_sha256", "proposal_artifact_path",
            "proposal_artifact_sha256", "proposal_size", "replacement_sha256",
            "broker_principal_sid", "denied_principal_sids", "probe_runtime_root",
            "expires_at",
        }
        if not isinstance(authority, Mapping) or set(authority) != authority_fields:
            raise ValidationError(
                "proposal action authority must use the fixed actorless authority schema"
            )
        revision = authority.get("expected_case_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValidationError(
                "proposal action authority expected_case_revision must be a nonnegative integer"
            )
        expected_authority = {
            "protocol_version": PROPOSAL_ACTION_AUTHORITY_PROTOCOL_VERSION,
            "schema_version": 1,
            "evidence_mode": PROPOSAL_DACL_EVIDENCE_MODE,
            "authority_id": normalized["authority_id"],
            "case_id": canonical_id,
            "expected_case_revision": revision,
            "grant_id": normalized["grant_id"],
            "operation_id": normalized["operation_id"],
            "action": normalized["action"],
            "operation": normalized["operation"],
            "repository": normalized["repository"],
            "branch": normalized["branch"],
            "worktree": normalized["worktree"],
            "base_head": normalized["base_head"],
            "target_path": normalized["target_path"],
            "baseline_sha256": normalized["baseline_sha256"],
            "proposal_artifact_path": normalized["proposal_artifact_path"],
            "proposal_artifact_sha256": normalized["proposal_artifact_sha256"],
            "proposal_size": normalized["proposal_size"],
            "replacement_sha256": normalized["replacement_sha256"],
            "broker_principal_sid": normalized["broker_principal_sid"],
            "denied_principal_sids": normalized["denied_principal_sids"],
            "probe_runtime_root": normalized["probe_runtime_root"],
            "expires_at": normalized["expires_at"],
        }
        if dict(authority) != expected_authority:
            raise AuthorizationError(
                "proposal action authority differs from the exact normalized operation"
            )
        authority_sha256 = require_snapshot_hash(
            str(grant.get("authority_sha256", ""))
        )
        if authority_sha256 != canonical_json_sha256(expected_authority):
            raise AuthorizationError("proposal action authority digest is invalid")
        normalized["authority"] = expected_authority
        normalized["authority_sha256"] = authority_sha256
        return normalized

    def issue_proposal_action_grant(
        self,
        case_id: str,
        *,
        grant: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        normalized = self._normalize_proposal_action_grant_request(case_id, grant)

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(
                case, "IMPLEMENTING", "issue_proposal_action_grant"
            )
            runtime = self._runtime_record(case, create=True)
            if runtime["action_grants"]:
                raise LimitError(
                    "case already contains its one exact runtime action grant"
                )
            authority = normalized["authority"]
            if authority["case_id"] != case["case_id"]:
                raise AuthorizationError("proposal authority belongs to another case")
            if authority["expected_case_revision"] != case["revision"]:
                raise RevisionConflict(
                    "proposal authority revision differs from the canonical case"
                )
            repository = normalized["repository"]
            branch = normalized["branch"]
            worktree_path = Path(normalized["worktree"])
            if repository not in case["bindings"]["repo_url"]:
                raise AuthorizationError(
                    "proposal repository is not canonically bound to this case"
                )
            if {"repository": repository, "value": branch} not in case["bindings"][
                "branch"
            ]:
                raise AuthorizationError(
                    "proposal branch is not canonically bound to this case"
                )
            if normalized["worktree"] not in case["bindings"]["worktree"]:
                raise AuthorizationError(
                    "proposal worktree is not canonically bound to this case"
                )
            try:
                exact_root = worktree_path.resolve(strict=True)
            except OSError as exc:
                raise ValidationError(
                    f"proposal worktree cannot be resolved: {exc}"
                ) from exc
            if not exact_root.is_dir() or _git_repository_root(exact_root) != exact_root:
                raise AuthorizationError(
                    "proposal worktree must be the exact Git repository root"
                )
            if path_contains_link_or_reparse(exact_root):
                raise AuthorizationError(
                    "proposal worktree must not traverse a link or reparse point"
                )
            if _git_origin(exact_root) != repository:
                raise AuthorizationError(
                    "Git origin differs from the proposal authority"
                )
            if _git_branch(exact_root) != branch:
                raise AuthorizationError(
                    "Git branch differs from the proposal authority"
                )
            if _git_head(exact_root) != normalized["base_head"]:
                raise AuthorizationError(
                    "Git HEAD differs from the exact proposal base head"
                )
            _assert_git_worktree_clean(
                exact_root, "before proposal action grant issuance"
            )
            target = exact_root.joinpath(
                *PurePosixPath(normalized["target_path"]).parts
            )
            if not path_is_within(target, exact_root) or not target.is_file():
                raise AuthorizationError(
                    "proposal target must be an existing regular file in the worktree"
                )
            if path_contains_link_or_reparse(target, stop=exact_root):
                raise AuthorizationError(
                    "proposal target must not traverse a link or reparse point"
                )
            target_identity = regular_file_identity(target, stop=exact_root)
            target_mode = _git_tracked_mode(exact_root, normalized["target_path"])
            if file_sha256(target) != normalized["baseline_sha256"]:
                raise AuthorizationError(
                    "proposal target baseline differs from the exact authority"
                )
            if target.stat().st_size > MAX_REPLACEMENT_BYTES:
                raise AuthorizationError(
                    "proposal target exceeds the bounded single-file primitive"
                )
            proposal = Path(normalized["proposal_artifact_path"])
            if not proposal.is_file() or proposal.is_symlink():
                raise AuthorizationError(
                    "proposal artifact must remain a regular non-link file"
                )
            proposal_identity = regular_file_identity(proposal)
            if proposal.stat().st_size != normalized["proposal_size"]:
                raise AuthorizationError(
                    "proposal artifact size differs from the exact authority"
                )
            if file_sha256(proposal) != normalized["proposal_artifact_sha256"]:
                raise AuthorizationError(
                    "proposal artifact digest differs from the exact authority"
                )
            executable = Path(normalized["sandbox_executable_path"])
            if (
                not executable.is_file()
                or executable.is_symlink()
                or path_contains_link_or_reparse(executable)
                or file_sha256(executable)
                != normalized["sandbox_executable_sha256"]
            ):
                raise AuthorizationError(
                    "sandbox executable path or bytes differ from the proposal grant"
                )
            if not self.path.is_file() or self.path.is_symlink():
                raise StoreCorruptionError(
                    "proposal grant issuance requires a regular canonical store"
                )
            state_root_path = self.state_root.resolve(strict=True)
            broker_source_root_path = Path(__file__).resolve().parents[2]
            proposal_root_path = proposal.parent.resolve(strict=True)
            if not proposal_root_path.is_dir() or path_contains_link_or_reparse(
                proposal_root_path
            ):
                raise AuthorizationError(
                    "proposal root must be an exact regular directory"
                )
            protected_control_roots = {
                "target_root": exact_root,
                "state_root": state_root_path,
                "broker_source_root": broker_source_root_path,
                "proposal_root": proposal_root_path,
            }
            root_items = list(protected_control_roots.items())
            if any(
                left_path == right_path
                or path_is_within(left_path, right_path)
                or path_is_within(right_path, left_path)
                for index, (_left_kind, left_path) in enumerate(root_items)
                for _right_kind, right_path in root_items[index + 1 :]
            ):
                raise AuthorizationError(
                    "target, state, broker-source, and proposal roots must be pairwise dedicated and nonoverlapping"
                )
            probe_runtime_path = Path(normalized["probe_runtime_root"]).resolve(
                strict=True
            )
            protected_roots_and_parents = {
                *protected_control_roots.values(),
                *(path.parent for path in protected_control_roots.values()),
            }
            if any(
                probe_runtime_path == protected_path
                or path_is_within(probe_runtime_path, protected_path)
                or path_is_within(protected_path, probe_runtime_path)
                for protected_path in protected_roots_and_parents
            ):
                raise AuthorizationError(
                    "probe runtime root must be dedicated and nonoverlapping with protected roots and parents"
                )
            proposal_anchor = normalize_action_path(proposal.name)
            proposal_broker_path = (
                broker_source_root_path
                / "scripts"
                / "agent"
                / "case_proposal_action_broker.py"
            )
            if (
                not proposal_broker_path.is_file()
                or proposal_broker_path.is_symlink()
            ):
                raise StoreCorruptionError(
                    "proposal broker source must be a regular sealed file"
                )
            state_root = normalize_binding("worktree", str(state_root_path))
            broker_source_root = normalize_binding(
                "worktree", str(broker_source_root_path)
            )
            proposal_root = normalize_binding(
                "worktree", str(proposal_root_path)
            )
            source_pins = proposal_broker_source_pins(broker_source_root_path)
            protected_roots = {
                "target_root": (
                    normalized["worktree"],
                    normalized["target_path"],
                    normalized["baseline_sha256"],
                ),
                "state_root": (state_root, STORE_FILENAME, file_sha256(self.path)),
                "broker_source_root": (
                    broker_source_root,
                    source_pins["manifest_path"],
                    source_pins["manifest_sha256"],
                ),
                "proposal_root": (
                    proposal_root,
                    proposal_anchor,
                    normalized["proposal_artifact_sha256"],
                ),
            }
            now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
            expires = dt.datetime.fromisoformat(normalized["expires_at"])
            remaining = (expires - now).total_seconds()
            if remaining <= 0 or remaining > MAX_ACTION_GRANT_LIFETIME_SECONDS:
                raise AuthorizationError(
                    "proposal grant expiry must be in the future and no more than 15 minutes away"
                )
            expected_acl_paths = {
                path
                for protected_path, _anchor, _digest in protected_roots.values()
                for path in (
                    protected_path,
                    normalize_binding(
                        "worktree", str(Path(protected_path).parent)
                    ),
                )
            }
            protected_acl_snapshot = self._normalize_protected_acl_snapshot(
                normalized["protected_acl_snapshot"],
                expected_paths=expected_acl_paths,
            )
            protected_acl_snapshot_sha256 = canonical_json_sha256(
                protected_acl_snapshot
            )
            if (
                protected_acl_snapshot_sha256
                != normalized["protected_acl_snapshot_sha256"]
            ):
                raise AuthorizationError("protected ACL snapshot digest differs")
            sealed_artifact_path, sealed_artifact_sha256, sealed_artifact_identity = (
                self._seal_action_artifact_unlocked(
                    case["case_id"],
                    normalized["grant_id"],
                    "replacement",
                    proposal,
                    expected_size=normalized["proposal_size"],
                    expected_sha256=normalized["replacement_sha256"],
                )
            )
            sealed_baseline_path, sealed_baseline_sha256, sealed_baseline_identity = (
                self._seal_action_artifact_unlocked(
                    case["case_id"],
                    normalized["grant_id"],
                    "baseline",
                    target,
                    expected_size=target.stat().st_size,
                    expected_sha256=normalized["baseline_sha256"],
                )
            )
            if regular_file_identity(proposal) != proposal_identity:
                raise AuthorizationError(
                    "proposal artifact identity changed while it was sealed"
                )
            if regular_file_identity(target, stop=exact_root) != target_identity:
                raise AuthorizationError(
                    "proposal target identity changed while baseline bytes were sealed"
                )
            allowed_paths = [normalized["target_path"]]
            issued_at = utc_now()
            recorded = {
                **{
                    name: value
                    for name, value in normalized.items()
                    if name
                    not in {"protected_acl_snapshot", "preissue_dacl_evidence"}
                },
                "target_mode": target_mode,
                "target_file_identity": target_identity,
                "proposal_file_identity": proposal_identity,
                "state_root": state_root,
                "broker_source_root": broker_source_root,
                "proposal_root": proposal_root,
                "proposal_broker_source_pins": source_pins,
                "proposal_broker_source_pins_sha256": canonical_json_sha256(
                    source_pins
                ),
                "sealed_artifact_path": sealed_artifact_path,
                "sealed_artifact_sha256": sealed_artifact_sha256,
                "sealed_artifact_identity": sealed_artifact_identity,
                "sealed_baseline_path": sealed_baseline_path,
                "sealed_baseline_sha256": sealed_baseline_sha256,
                "sealed_baseline_identity": sealed_baseline_identity,
                "allowed_paths": allowed_paths,
                "allowed_paths_sha256": canonical_json_sha256(allowed_paths),
                "protected_acl_snapshot": protected_acl_snapshot,
                "protected_acl_snapshot_sha256": protected_acl_snapshot_sha256,
                "status": "ISSUED",
                "authorization_nonce": secrets.token_hex(32),
                "issued_at": issued_at,
                "issued_revision": case["revision"] + 1,
                "claim": None,
                "result": None,
            }
            preissue_dacl_evidence = self._normalize_dacl_evidence(
                normalized["preissue_dacl_evidence"], recorded
            )
            preissue_dacl_evidence_sha256 = canonical_json_sha256(
                preissue_dacl_evidence
            )
            if (
                preissue_dacl_evidence_sha256
                != normalized["preissue_dacl_evidence_sha256"]
            ):
                raise AuthorizationError("preissue DACL evidence digest differs")
            recorded["preissue_dacl_evidence"] = preissue_dacl_evidence
            recorded[
                "preissue_dacl_evidence_sha256"
            ] = preissue_dacl_evidence_sha256
            recorded["grant_sha256"] = canonical_json_sha256(recorded)
            runtime["action_grants"][normalized["grant_id"]] = recorded
            return {
                "grant_id": normalized["grant_id"],
                "authority_id": normalized["authority_id"],
                "authority_sha256": normalized["authority_sha256"],
                "status": "ISSUED",
                "issued_revision": recorded["issued_revision"],
                "grant_sha256": recorded["grant_sha256"],
                "allowed_paths_sha256": recorded["allowed_paths_sha256"],
                "preissue_dacl_evidence_sha256": preissue_dacl_evidence_sha256,
            }

        return self._mutate(
            case_id,
            operation="issue_proposal_action_grant",
            payload={"grant": normalized},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    @staticmethod
    def _normalize_dacl_evidence(
        evidence: Mapping[str, Any], grant: Mapping[str, Any]
    ) -> dict[str, Any]:
        proposal_mode = (
            grant.get("protocol_version") == PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION
        )
        expected_fields = {
            "protocol_version", "schema_version", "denied_principal_sids",
            "broker_principal_sid", "rules", "observed_at",
        }
        if not proposal_mode:
            expected_fields.add("membership_evidence_sha256")
        if not isinstance(evidence, Mapping) or set(evidence) != expected_fields:
            protocol = (
                PROPOSAL_DACL_EVIDENCE_PROTOCOL_VERSION
                if proposal_mode
                else WINDOWS_DACL_EVIDENCE_PROTOCOL_VERSION
            )
            raise ValidationError(f"DACL evidence must use the fixed {protocol} schema")
        expected_protocol = (
            PROPOSAL_DACL_EVIDENCE_PROTOCOL_VERSION
            if proposal_mode
            else WINDOWS_DACL_EVIDENCE_PROTOCOL_VERSION
        )
        expected_schema = 1 if proposal_mode else 2
        if (
            evidence.get("protocol_version") != expected_protocol
            or evidence.get("schema_version") != expected_schema
        ):
            raise ValidationError("DACL evidence protocol or schema version is unsupported")
        denied_principal_sids = evidence.get("denied_principal_sids")
        if denied_principal_sids != grant["denied_principal_sids"]:
            raise AuthorizationError("DACL denied principals differ from the exact action grant")
        membership_sha256: str | None = None
        if not proposal_mode:
            membership_sha256 = require_snapshot_hash(
                str(evidence.get("membership_evidence_sha256", ""))
            )
            if membership_sha256 != grant["group_membership_evidence_sha256"]:
                raise AuthorizationError(
                    "DACL membership digest differs from the exact action grant"
                )
        broker_sid = require_windows_sid(evidence.get("broker_principal_sid"), "DACL broker SID")
        if broker_sid != grant["broker_principal_sid"]:
            raise AuthorizationError("DACL evidence differs from the exact action grant")
        expected_paths = {
            "target_root": grant["worktree"],
            "state_root": grant["state_root"],
            "broker_source_root": grant["broker_source_root"],
            "proposal_root": grant["proposal_root"],
        }
        raw_rules = evidence.get("rules")
        expected_rule_count = len(PROTECTED_ROOT_KINDS) * len(denied_principal_sids)
        if not isinstance(raw_rules, list) or len(raw_rules) != expected_rule_count:
            raise ValidationError(
                "DACL evidence must contain one effective rule per protected root and denied principal"
            )
        normalized_rules: list[dict[str, Any]] = []
        observed: set[tuple[str, str]] = set()
        rule_fields = {
            "root_kind", "principal_sid", "deny_source_sids", "path", "owner_sid",
            "parent_path", "parent_owner_sid", "root_sddl_sha256", "parent_sddl_sha256",
            "access_type", "is_inherited", "inheritance_flags", "rights_mask",
            "inheritable_rights_mask", "propagation_flags", "parent_rights_mask",
        }
        for raw_rule in raw_rules:
            if not isinstance(raw_rule, Mapping) or set(raw_rule) != rule_fields:
                raise ValidationError("DACL protected-root rule must use the fixed schema")
            root_kind = str(raw_rule.get("root_kind", ""))
            principal_sid = require_windows_sid(
                raw_rule.get("principal_sid"), f"{root_kind} denied principal SID"
            )
            pair = (root_kind, principal_sid)
            if (root_kind not in PROTECTED_ROOT_KINDS
                    or principal_sid not in denied_principal_sids or pair in observed):
                raise ValidationError(
                    "DACL root/principal rule is missing, duplicated, or unknown"
                )
            raw_sources = raw_rule.get("deny_source_sids")
            if not isinstance(raw_sources, list) or not raw_sources:
                raise ValidationError("DACL deny_source_sids must be a nonempty array")
            deny_sources = sorted(
                {require_windows_sid(item, "DACL deny source SID") for item in raw_sources}
            )
            if len(deny_sources) != len(raw_sources):
                raise ValidationError("DACL deny_source_sids must be unique")
            sandbox_group_sid = grant["sandbox_group_principal_sid"]
            required_sources = {sandbox_group_sid, principal_sid}
            if not required_sources.issubset(deny_sources) or any(
                item not in denied_principal_sids for item in deny_sources
            ):
                raise AuthorizationError(
                    f"{root_kind} effective denial for {principal_sid} lacks its explicit principal and sealed sandbox-group sources"
                )
            path = normalize_binding("worktree", str(raw_rule.get("path", "")))
            owner_sid = require_windows_sid(raw_rule.get("owner_sid"), f"{root_kind} owner SID")
            parent_path = normalize_binding("worktree", str(raw_rule.get("parent_path", "")))
            parent_owner_sid = require_windows_sid(
                raw_rule.get("parent_owner_sid"), f"{root_kind} parent owner SID"
            )
            if (path != expected_paths[root_kind] or owner_sid != broker_sid
                    or owner_sid in denied_principal_sids
                    or parent_path != normalize_binding("worktree", str(Path(path).parent))
                    or parent_owner_sid != broker_sid):
                raise AuthorizationError(f"{root_kind} owner or path differs from the exact grant")
            root_sddl_sha256 = require_snapshot_hash(
                str(raw_rule.get("root_sddl_sha256", ""))
            )
            parent_sddl_sha256 = require_snapshot_hash(
                str(raw_rule.get("parent_sddl_sha256", ""))
            )
            if raw_rule.get("access_type") != "DENY" or raw_rule.get("is_inherited") is not False:
                raise AuthorizationError(f"{root_kind} worker denial must be an explicit DENY rule")
            raw_flags = raw_rule.get("inheritance_flags")
            if not isinstance(raw_flags, list) or sorted(raw_flags) != ["CONTAINER_INHERIT", "OBJECT_INHERIT"]:
                raise AuthorizationError(f"{root_kind} worker denial must inherit to files and directories")
            propagation_flags = raw_rule.get("propagation_flags")
            if propagation_flags != ["NONE"]:
                raise AuthorizationError(
                    f"{root_kind} worker denial must have no InheritOnly or NoPropagate escape"
                )
            rights_mask = raw_rule.get("rights_mask")
            if isinstance(rights_mask, bool) or not isinstance(rights_mask, int):
                raise ValidationError("DACL rights_mask must be an integer")
            if rights_mask & WINDOWS_REQUIRED_DENY_RIGHTS_MASK != WINDOWS_REQUIRED_DENY_RIGHTS_MASK:
                raise AuthorizationError(
                    f"{root_kind} DENY rule lacks write, delete, permission, or ownership protection"
                )
            inheritable_rights_mask = raw_rule.get("inheritable_rights_mask")
            if (isinstance(inheritable_rights_mask, bool)
                    or not isinstance(inheritable_rights_mask, int)):
                raise ValidationError("DACL inheritable_rights_mask must be an integer")
            if (inheritable_rights_mask & WINDOWS_REQUIRED_DENY_RIGHTS_MASK
                    != WINDOWS_REQUIRED_DENY_RIGHTS_MASK):
                raise AuthorizationError(
                    f"{root_kind} recursive DENY lacks full write, delete, permission, or ownership protection"
                )
            parent_rights_mask = raw_rule.get("parent_rights_mask")
            if isinstance(parent_rights_mask, bool) or not isinstance(parent_rights_mask, int):
                raise ValidationError("parent DACL rights mask must be an integer")
            if parent_rights_mask & WINDOWS_REQUIRED_DENY_RIGHTS_MASK != WINDOWS_REQUIRED_DENY_RIGHTS_MASK:
                raise AuthorizationError(
                    f"{root_kind} parent can grant delete-child or ACL takeover capability"
                )
            normalized_rules.append({
                "root_kind": root_kind,
                "principal_sid": principal_sid,
                "deny_source_sids": deny_sources,
                "path": path,
                "owner_sid": owner_sid,
                "parent_path": parent_path,
                "parent_owner_sid": parent_owner_sid,
                "root_sddl_sha256": root_sddl_sha256,
                "parent_sddl_sha256": parent_sddl_sha256,
                "access_type": "DENY",
                "is_inherited": False,
                "inheritance_flags": ["CONTAINER_INHERIT", "OBJECT_INHERIT"],
                "propagation_flags": ["NONE"],
                "rights_mask": rights_mask,
                "inheritable_rights_mask": inheritable_rights_mask,
                "parent_rights_mask": parent_rights_mask,
            })
            observed.add(pair)
        normalized_rules.sort(key=lambda item: (
            PROTECTED_ROOT_KINDS.index(item["root_kind"]),
            denied_principal_sids.index(item["principal_sid"]),
        ))
        normalized = {
            "protocol_version": expected_protocol,
            "schema_version": expected_schema,
            "denied_principal_sids": denied_principal_sids,
            "broker_principal_sid": broker_sid,
            "rules": normalized_rules,
            "observed_at": require_utc_timestamp(evidence.get("observed_at"), "DACL observed_at"),
        }
        if membership_sha256 is not None:
            normalized["membership_evidence_sha256"] = membership_sha256
        return normalized

    def _normalize_trusted_write_probe(
        self, evidence: Mapping[str, Any], grant: Mapping[str, Any]
    ) -> dict[str, Any]:
        expected_fields = {
            "protocol_version", "schema_version", "broker_principal_sid",
            "broker_identity_name", "protected_roots", "head_before", "head_after",
            "status_sha256_before", "status_sha256_after", "observed_at",
        }
        if not isinstance(evidence, Mapping) or set(evidence) != expected_fields:
            raise ValidationError(
                "trusted write probe must use the fixed ccos-trusted-write-probe-v1 schema"
            )
        if evidence.get("protocol_version") != TRUSTED_WRITE_PROBE_PROTOCOL_VERSION or evidence.get("schema_version") != 1:
            raise ValidationError("trusted write probe protocol or schema version is unsupported")
        broker_sid = require_windows_sid(evidence.get("broker_principal_sid"), "probe broker SID")
        if broker_sid != grant["broker_principal_sid"]:
            raise AuthorizationError("trusted write probe differs from the exact action grant")
        expected_paths = {
            "target_root": grant["worktree"],
            "state_root": grant["state_root"],
            "broker_source_root": grant["broker_source_root"],
            "proposal_root": grant["proposal_root"],
        }
        proposal_mode = (
            grant.get("protocol_version") == PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION
        )
        if proposal_mode:
            source_pins = grant.get("proposal_broker_source_pins")
            if not isinstance(source_pins, Mapping):
                raise AuthorizationError("proposal grant lacks sealed broker source pins")
            broker_anchor = (
                normalize_action_path(source_pins.get("manifest_path")),
                require_snapshot_hash(str(source_pins.get("manifest_sha256", ""))),
            )
            proposal_anchor = (
                normalize_action_path(Path(grant["proposal_artifact_path"]).name),
                require_snapshot_hash(grant["proposal_artifact_sha256"]),
            )
        else:
            isolation_roots = {
                item["root_kind"]: item
                for item in grant["isolation_evidence"]["principal_probes"][0][
                    "probe"
                ]["protected_roots"]
            }
            broker_anchor = (
                isolation_roots["broker_source_root"]["anchor_path"],
                isolation_roots["broker_source_root"]["anchor_sha256_after"],
            )
            proposal_anchor = (
                isolation_roots["proposal_root"]["anchor_path"],
                isolation_roots["proposal_root"]["anchor_sha256_after"],
            )
        expected_anchors = {
            "target_root": (
                grant["target_path"], grant["baseline_sha256"]
            ),
            "state_root": (STORE_FILENAME, file_sha256(self.path)),
            "broker_source_root": broker_anchor,
            "proposal_root": proposal_anchor,
        }
        raw_roots = evidence.get("protected_roots")
        if not isinstance(raw_roots, list) or len(raw_roots) != len(PROTECTED_ROOT_KINDS):
            raise ValidationError("trusted write probe must cover every protected root")
        normalized_roots: list[dict[str, Any]] = []
        observed: set[str] = set()
        root_fields = {
            "root_kind", "path", "anchor_path", "anchor_sha256_before",
            "anchor_sha256_after", "probe_relative_path", "probe_content_sha256",
            "probe_absent_after",
        }
        for raw_root in raw_roots:
            if not isinstance(raw_root, Mapping) or set(raw_root) != root_fields:
                raise ValidationError("trusted protected-root probe must use the fixed schema")
            root_kind = str(raw_root.get("root_kind", ""))
            if root_kind not in PROTECTED_ROOT_KINDS or root_kind in observed:
                raise ValidationError("trusted protected-root kind is missing, duplicated, or unknown")
            path = normalize_binding("worktree", str(raw_root.get("path", "")))
            anchor_path = normalize_action_path(raw_root.get("anchor_path"))
            expected_anchor_path, expected_anchor_sha256 = expected_anchors[root_kind]
            before = require_snapshot_hash(str(raw_root.get("anchor_sha256_before", "")))
            after = require_snapshot_hash(str(raw_root.get("anchor_sha256_after", "")))
            if (path != expected_paths[root_kind] or anchor_path != expected_anchor_path
                    or before != expected_anchor_sha256 or after != expected_anchor_sha256):
                raise AuthorizationError(f"trusted probe changed or misidentified {root_kind}")
            expected_probe = (
                f".ccos-broker-{root_kind.replace('_root', '')}-probe-"
                + hashlib.sha256(grant["authorization_nonce"].encode("utf-8")).hexdigest()[:20]
            )
            probe_path = normalize_action_path(raw_root.get("probe_relative_path"))
            if probe_path != expected_probe or raw_root.get("probe_absent_after") is not True:
                raise AuthorizationError(f"trusted write probe did not cleanly prove {root_kind} write access")
            normalized_roots.append({
                "root_kind": root_kind,
                "path": path,
                "anchor_path": anchor_path,
                "anchor_sha256_before": before,
                "anchor_sha256_after": after,
                "probe_relative_path": probe_path,
                "probe_content_sha256": require_snapshot_hash(
                    str(raw_root.get("probe_content_sha256", ""))
                ),
                "probe_absent_after": True,
            })
            observed.add(root_kind)
        normalized_roots.sort(key=lambda item: PROTECTED_ROOT_KINDS.index(item["root_kind"]))
        head_before = require_sha(str(evidence.get("head_before", "")), "trusted probe head before")
        head_after = require_sha(str(evidence.get("head_after", "")), "trusted probe head after")
        if head_before != grant["base_head"] or head_after != grant["base_head"]:
            raise AuthorizationError("trusted write probe observed Git HEAD drift")
        status_before = require_snapshot_hash(str(evidence.get("status_sha256_before", "")))
        status_after = require_snapshot_hash(str(evidence.get("status_sha256_after", "")))
        if status_before != EMPTY_SHA256 or status_after != EMPTY_SHA256:
            raise AuthorizationError("trusted write probe did not preserve a clean Git status")
        return {
            "protocol_version": TRUSTED_WRITE_PROBE_PROTOCOL_VERSION,
            "schema_version": 1,
            "broker_principal_sid": broker_sid,
            "broker_identity_name": _nonempty(
                evidence.get("broker_identity_name"), "broker identity name", 256
            ),
            "protected_roots": normalized_roots,
            "head_before": head_before,
            "head_after": head_after,
            "status_sha256_before": status_before,
            "status_sha256_after": status_after,
            "observed_at": require_utc_timestamp(evidence.get("observed_at"), "trusted probe observed_at"),
        }

    def claim_action_grant(
        self,
        case_id: str,
        *,
        claim: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        if not isinstance(claim, Mapping):
            raise ValidationError("action claim must be an object")
        protocol = claim.get("protocol_version")
        if protocol == ACTION_GRANT_CLAIM_PROTOCOL_VERSION:
            schema_version = 1
            authority_field = "controller_receipt_sha256"
        elif protocol == PROPOSAL_ACTION_CLAIM_PROTOCOL_VERSION:
            schema_version = 2
            authority_field = "authority_sha256"
        else:
            raise ValidationError("action claim protocol is unsupported")
        expected_fields = {
            "protocol_version", "schema_version", "grant_id", authority_field,
            "broker_principal_sid", "dacl_evidence", "trusted_write_probe",
        }
        if set(claim) != expected_fields or claim.get("schema_version") != schema_version:
            raise ValidationError("action claim does not use its fixed protocol schema")
        normalized_header = {
            "protocol_version": protocol,
            "schema_version": schema_version,
            "grant_id": require_stable_id(claim.get("grant_id"), "grant id"),
            authority_field: require_snapshot_hash(
                str(claim.get(authority_field, ""))
            ),
            "broker_principal_sid": require_windows_sid(
                claim.get("broker_principal_sid"), "broker principal SID"
            ),
        }

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "IMPLEMENTING", "claim_action_grant")
            runtime = self._runtime_record(case, create=False)
            grant = runtime["action_grants"].get(normalized_header["grant_id"])
            if not isinstance(grant, dict):
                raise AuthorizationError("action grant does not exist in this canonical case")
            if grant["status"] != "ISSUED":
                raise LimitError(f"action grant is already {grant['status']} and cannot be claimed")
            if case["revision"] != grant["issued_revision"]:
                raise RevisionConflict("case revision changed after action grant issuance")
            expected_grant_protocol = (
                ACTION_GRANT_PROTOCOL_VERSION
                if protocol == ACTION_GRANT_CLAIM_PROTOCOL_VERSION
                else PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION
            )
            if grant.get("protocol_version") != expected_grant_protocol:
                raise AuthorizationError(
                    "claim protocol differs from the canonical action grant"
                )
            if normalized_header[authority_field] != grant[authority_field]:
                raise AuthorizationError(
                    "claim authority digest differs from the exact action grant"
                )
            if normalized_header["broker_principal_sid"] != grant["broker_principal_sid"]:
                raise AuthorizationError("claiming principal differs from the trusted broker principal")
            expires = dt.datetime.fromisoformat(grant["expires_at"])
            if dt.datetime.now(dt.timezone.utc) >= expires:
                raise AuthorizationError("action grant expired before claim")
            dacl = self._normalize_dacl_evidence(claim.get("dacl_evidence"), grant)
            trusted_probe = self._normalize_trusted_write_probe(
                claim.get("trusted_write_probe"), grant
            )
            claim_record = {
                **normalized_header,
                "dacl_evidence": dacl,
                "dacl_evidence_sha256": canonical_json_sha256(dacl),
                "trusted_write_probe": trusted_probe,
                "trusted_write_probe_sha256": canonical_json_sha256(trusted_probe),
                "claimed_at": utc_now(),
                "claimed_revision": case["revision"] + 1,
            }
            claim_record["claim_sha256"] = canonical_json_sha256(claim_record)
            grant["status"] = "CLAIMED"
            grant["claim"] = claim_record
            grant["grant_sha256"] = canonical_json_sha256(
                {name: value for name, value in grant.items() if name != "grant_sha256"}
            )
            return {
                "grant_id": grant["grant_id"],
                "status": "CLAIMED",
                "claim_sha256": claim_record["claim_sha256"],
                "grant_sha256": grant["grant_sha256"],
            }

        return self._mutate(
            case_id,
            operation="claim_action_grant",
            payload={"claim": {**normalized_header, "dacl_evidence": claim.get("dacl_evidence"), "trusted_write_probe": claim.get("trusted_write_probe")}},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    def complete_action_grant(
        self,
        case_id: str,
        *,
        completion: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        if not isinstance(completion, Mapping):
            raise ValidationError("action completion must be an object")
        protocol = completion.get("protocol_version")
        if protocol == ACTION_GRANT_RESULT_PROTOCOL_VERSION:
            schema_version = 1
            authority_field = "controller_receipt_sha256"
        elif protocol == PROPOSAL_ACTION_RESULT_PROTOCOL_VERSION:
            schema_version = 2
            authority_field = "authority_sha256"
        else:
            raise ValidationError("action completion protocol is unsupported")
        expected_fields = {
            "protocol_version", "schema_version", "grant_id", authority_field,
            "broker_principal_sid", "post_replacement_evidence_sha256", "completed_at",
        }
        if set(completion) != expected_fields or completion.get("schema_version") != schema_version:
            raise ValidationError("action completion does not use its fixed protocol schema")
        normalized = {
            "protocol_version": protocol,
            "schema_version": schema_version,
            "grant_id": require_stable_id(completion.get("grant_id"), "grant id"),
            authority_field: require_snapshot_hash(
                str(completion.get(authority_field, ""))
            ),
            "broker_principal_sid": require_windows_sid(
                completion.get("broker_principal_sid"), "broker principal SID"
            ),
            "post_replacement_evidence_sha256": require_snapshot_hash(
                str(completion.get("post_replacement_evidence_sha256", ""))
            ),
            "completed_at": require_utc_timestamp(completion.get("completed_at"), "completed_at"),
        }

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "IMPLEMENTING", "complete_action_grant")
            runtime = self._runtime_record(case, create=False)
            grant = runtime["action_grants"].get(normalized["grant_id"])
            if not isinstance(grant, dict):
                raise AuthorizationError("action grant does not exist in this canonical case")
            if grant["status"] != "CLAIMED":
                raise LimitError(f"action grant is {grant['status']} and cannot be completed")
            expected_grant_protocol = (
                ACTION_GRANT_PROTOCOL_VERSION
                if protocol == ACTION_GRANT_RESULT_PROTOCOL_VERSION
                else PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION
            )
            if grant.get("protocol_version") != expected_grant_protocol:
                raise AuthorizationError(
                    "completion protocol differs from the canonical action grant"
                )
            if normalized[authority_field] != grant[authority_field]:
                raise AuthorizationError(
                    "completion authority digest differs from the exact action grant"
                )
            if normalized["broker_principal_sid"] != grant["broker_principal_sid"]:
                raise AuthorizationError("completion principal differs from the trusted broker")
            if case["revision"] != grant["claim"]["claimed_revision"]:
                raise RevisionConflict("case revision changed after action grant claim")
            root = Path(grant["worktree"]).resolve(strict=True)
            if _git_repository_root(root) != root or _git_origin(root) != grant["repository"]:
                raise AuthorizationError("repository identity changed during the authorized action")
            if _git_branch(root) != grant["branch"] or _git_head(root) != grant["base_head"]:
                raise AuthorizationError("branch or HEAD changed during the authorized action")
            target = root.joinpath(*PurePosixPath(grant["target_path"]).parts)
            if not target.is_file() or path_contains_link_or_reparse(target, stop=root):
                raise AuthorizationError("authorized target is no longer a regular direct file")
            replacement_identity = regular_file_identity(target, stop=root)
            if _git_tracked_mode(root, grant["target_path"]) != grant["target_mode"]:
                raise AuthorizationError("authorized target mode changed")
            observed_sha256 = file_sha256(target)
            if observed_sha256 != grant["replacement_sha256"]:
                raise AuthorizationError("authorized target does not contain the exact replacement bytes")
            status_paths = _git_status_paths(root)
            if status_paths != grant["allowed_paths"]:
                raise AuthorizationError("Git status contains a path outside the exact action grant")
            result_record: dict[str, Any] = {
                **normalized,
                "operation_id": grant["operation_id"],
                "operation": grant["operation"],
                "repository": grant["repository"],
                "branch": grant["branch"],
                "worktree": grant["worktree"],
                "base_head": grant["base_head"],
                "target_path": grant["target_path"],
                "baseline_sha256": grant["baseline_sha256"],
                "replacement_sha256": observed_sha256,
                "replacement_file_identity": replacement_identity,
                "observed_status_paths": status_paths,
                "post_replacement_evidence_sha256": normalized[
                    "post_replacement_evidence_sha256"
                ],
                "completed_revision": case["revision"] + 1,
            }
            if grant["protocol_version"] == ACTION_GRANT_PROTOCOL_VERSION:
                result_record.update(
                    {
                        "actor_thread_id": grant["actor_thread_id"],
                        "actor_turn_id": grant["actor_turn_id"],
                        "live_controller_evidence_sha256": grant[
                            "live_controller_evidence_sha256"
                        ],
                    }
                )
            else:
                result_record.update(
                    {
                        "authority_id": grant["authority_id"],
                        "authority_sha256": grant["authority_sha256"],
                        "proposal_artifact_sha256": grant[
                            "proposal_artifact_sha256"
                        ],
                    }
                )
            result_record["result_sha256"] = canonical_json_sha256(result_record)
            grant["status"] = "COMPLETED"
            grant["result"] = result_record
            grant["grant_sha256"] = canonical_json_sha256(
                {name: value for name, value in grant.items() if name != "grant_sha256"}
            )
            return {
                "grant_id": grant["grant_id"],
                "status": "COMPLETED",
                "result_sha256": result_record["result_sha256"],
                "grant_sha256": grant["grant_sha256"],
                "changed_paths": status_paths,
            }

        return self._mutate(
            case_id,
            operation="complete_action_grant",
            payload={"completion": normalized},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    def fail_action_grant(
        self,
        case_id: str,
        *,
        failure: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        if not isinstance(failure, Mapping):
            raise ValidationError("action failure must be an object")
        protocol = failure.get("protocol_version")
        if protocol == ACTION_GRANT_RESULT_PROTOCOL_VERSION:
            schema_version = 1
            authority_field = None
        elif protocol == PROPOSAL_ACTION_RESULT_PROTOCOL_VERSION:
            schema_version = 2
            authority_field = "authority_sha256"
        else:
            raise ValidationError("action failure protocol is unsupported")
        expected_fields = {
            "protocol_version", "schema_version", "grant_id", "broker_principal_sid",
            "failure_stage", "failure_code", "failure_evidence_sha256", "observed_at",
        }
        if authority_field:
            expected_fields.add(authority_field)
        if set(failure) != expected_fields or failure.get("schema_version") != schema_version:
            raise ValidationError("action failure does not use its fixed protocol schema")
        normalized = {
            "protocol_version": protocol,
            "schema_version": schema_version,
            "grant_id": require_stable_id(failure.get("grant_id"), "grant id"),
            "broker_principal_sid": require_windows_sid(
                failure.get("broker_principal_sid"), "broker principal SID"
            ),
            "failure_stage": require_stable_id(failure.get("failure_stage"), "failure stage"),
            "failure_code": require_stable_id(failure.get("failure_code"), "failure code"),
            "failure_evidence_sha256": require_snapshot_hash(
                str(failure.get("failure_evidence_sha256", ""))
            ),
            "observed_at": require_utc_timestamp(failure.get("observed_at"), "failure observed_at"),
        }
        if authority_field:
            normalized[authority_field] = require_snapshot_hash(
                str(failure.get(authority_field, ""))
            )

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "IMPLEMENTING", "fail_action_grant")
            runtime = self._runtime_record(case, create=False)
            grant = runtime["action_grants"].get(normalized["grant_id"])
            if not isinstance(grant, dict):
                raise AuthorizationError("action grant does not exist in this canonical case")
            expected_grant_protocol = (
                ACTION_GRANT_PROTOCOL_VERSION
                if protocol == ACTION_GRANT_RESULT_PROTOCOL_VERSION
                else PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION
            )
            if grant.get("protocol_version") != expected_grant_protocol:
                raise AuthorizationError(
                    "failure protocol differs from the canonical action grant"
                )
            if authority_field and normalized[authority_field] != grant[authority_field]:
                raise AuthorizationError(
                    "failure authority digest differs from the exact action grant"
                )
            if grant["status"] not in {"ISSUED", "CLAIMED"}:
                raise LimitError(f"only an active action grant can be failed; grant is {grant['status']}")
            if grant["status"] == "ISSUED" and normalized["failure_stage"] not in {
                "preclaim", "supervisor_context"
            }:
                raise AuthorizationError(
                    "an unclaimed grant can only terminate at the trusted preclaim or supervisor-context boundary"
                )
            if (grant["status"] == "ISSUED"
                    and normalized["failure_stage"] == "supervisor_context"
                    and normalized["failure_code"] != "SUPERVISOR_CONTEXT_LOST"):
                raise AuthorizationError("trusted supervisor-context loss code must be exact")
            if normalized["broker_principal_sid"] != grant["broker_principal_sid"]:
                raise AuthorizationError("failure principal differs from the trusted broker")
            observed_target_sha256: str | None = None
            observed_status_paths: list[str] | None = None
            observation_error: str | None = None
            try:
                root = Path(grant["worktree"]).resolve(strict=True)
                target = root.joinpath(*PurePosixPath(grant["target_path"]).parts)
                if target.is_file() and not path_contains_link_or_reparse(target, stop=root):
                    observed_target_sha256 = file_sha256(target)
                observed_status_paths = _git_status_paths(root)
            except (OSError, CaseStateError) as exc:
                observation_error = type(exc).__name__
            failure_record = {
                **normalized,
                "observed_target_sha256": observed_target_sha256,
                "observed_status_paths": observed_status_paths,
                "baseline_restored": (
                    observed_target_sha256 == grant["baseline_sha256"]
                    and observed_status_paths == []
                ),
                "observation_error": observation_error,
                "failed_revision": case["revision"] + 1,
            }
            failure_record["result_sha256"] = canonical_json_sha256(failure_record)
            grant["status"] = "FAILED"
            grant["result"] = failure_record
            grant["grant_sha256"] = canonical_json_sha256(
                {name: value for name, value in grant.items() if name != "grant_sha256"}
            )
            case["state"] = "CASE_LOCKED"
            case["resumable_state"] = None
            case["lock_reason"] = (
                "one-use action did not complete exactly: " + normalized["failure_code"]
            )
            return {
                "grant_id": grant["grant_id"],
                "status": "FAILED",
                "result_sha256": failure_record["result_sha256"],
                "grant_sha256": grant["grant_sha256"],
                "lock_reason": case["lock_reason"],
            }

        return self._mutate(
            case_id,
            operation="fail_action_grant",
            payload={"failure": normalized},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    @staticmethod
    def _normalize_quarantine_authority(
        case_id: str,
        authority: Mapping[str, Any],
    ) -> dict[str, str]:
        if not isinstance(authority, Mapping):
            raise AuthorizationError("terminal quarantine requires structured human authority")
        expected_fields = {
            "authority_id", "source", "authorized_by", "scope", "case_id", "operation", "expected_state",
        }
        if set(authority) != expected_fields:
            raise AuthorizationError("terminal quarantine authority must use the fixed exact-case schema")
        normalized = CaseStore._normalize_authority(authority)
        if canonical_case_id(str(authority.get("case_id", ""))) != case_id:
            raise AuthorizationError("terminal quarantine authority names a different case")
        if authority.get("operation") != "quarantine-terminal":
            raise AuthorizationError("terminal quarantine authority names a different operation")
        if authority.get("expected_state") != "CLOSED_SUCCESS":
            raise AuthorizationError("terminal quarantine authority must name CLOSED_SUCCESS")
        return {
            **normalized,
            "case_id": case_id,
            "operation": "quarantine-terminal",
            "expected_state": "CLOSED_SUCCESS",
        }

    @staticmethod
    def _normalize_quarantine_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(evidence, Mapping) or set(evidence) != {
            "reason_code", "evidence_reference", "evidence_sha256", "externally_published",
        }:
            raise ValidationError("terminal quarantine evidence must use the fixed schema")
        reason_code = _nonempty(evidence.get("reason_code"), "quarantine reason code", 128).upper()
        if not FINDING_ID_PATTERN.fullmatch(reason_code):
            raise ValidationError("quarantine reason code must be a stable identifier")
        if not isinstance(evidence.get("externally_published"), bool):
            raise ValidationError("externally_published must be a boolean")
        return {
            "reason_code": reason_code,
            "evidence_reference": _nonempty(
                evidence.get("evidence_reference"), "quarantine evidence reference", 2048
            ),
            "evidence_sha256": require_snapshot_hash(str(evidence.get("evidence_sha256", ""))),
            "externally_published": evidence["externally_published"],
        }

    def quarantine_terminal(
        self,
        case_id: str,
        *,
        expected_state: str,
        expected_record_sha256: str,
        authority: Mapping[str, Any],
        evidence: Mapping[str, Any],
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        case_id = canonical_case_id(case_id)
        request_id = require_request_id(request_id)
        if expected_state != "CLOSED_SUCCESS":
            raise AuthorizationError("quarantine-terminal is restricted to expected state CLOSED_SUCCESS")
        expected_record_sha256 = require_snapshot_hash(expected_record_sha256)
        normalized_authority = self._normalize_quarantine_authority(case_id, authority)
        normalized_evidence = self._normalize_quarantine_evidence(evidence)
        payload = {
            "expected_state": expected_state,
            "expected_record_sha256": expected_record_sha256,
            "authority": normalized_authority,
            "evidence": normalized_evidence,
        }
        fingerprint = _fingerprint("quarantine_terminal", payload)
        with FileLock(self.lock_path):
            data = self._read_unlocked()
            if case_id not in data["cases"]:
                raise ValidationError(f"case not found: {case_id}")
            original = data["cases"][case_id]
            audit_records = self._quarantine_audit_records_unlocked()
            matching_prepared = [
                record
                for record in audit_records
                if record.get("case_id") == case_id
                and record.get("request_id") == request_id
                and record.get("phase") == "PREPARED"
            ]
            matching_committed = [
                record
                for record in audit_records
                if record.get("case_id") == case_id
                and record.get("request_id") == request_id
                and record.get("phase") == "COMMITTED"
            ]
            if len(matching_prepared) > 1 or len(matching_committed) > 1:
                raise StoreCorruptionError(
                    "terminal quarantine audit contains duplicate transaction phases"
                )
            prior = original["events"].get(request_id)
            if prior:
                if prior.get("fingerprint") != fingerprint:
                    raise ConflictError("request_id was already used with a different operation payload")
                committed = next(
                    (
                        record for record in audit_records
                        if record.get("case_id") == case_id
                        and record.get("request_id") == request_id
                        and record.get("phase") == "COMMITTED"
                    ),
                    None,
                )
                prepared = next(
                    (
                        record for record in audit_records
                        if record.get("case_id") == case_id
                        and record.get("request_id") == request_id
                        and record.get("phase") == "PREPARED"
                    ),
                    None,
                )
                if committed is None:
                    if (prepared is None or not isinstance(original.get("terminal_quarantine"), Mapping)
                            or original["terminal_quarantine"].get("request_id") != request_id):
                        raise StoreCorruptionError("terminal quarantine lacks recoverable audit evidence")
                    committed = self._append_quarantine_audit_unlocked(
                        {
                            "phase": "COMMITTED",
                            "case_id": case_id,
                            "request_id": request_id,
                            "fingerprint": fingerprint,
                            "pre_store_sha256": prepared["pre_store_sha256"],
                            "post_store_sha256": prepared["expected_post_store_sha256"],
                            "backup_sha256": prepared["backup_sha256"],
                            "prepared_event_sha256": prepared["event_sha256"],
                            "recorded_at": utc_now(),
                            "recovered": True,
                        },
                        audit_records,
                    )
                result = copy.deepcopy(prior["result"])
                result.update(
                    idempotent=True,
                    pre_store_sha256=committed["pre_store_sha256"],
                    post_store_sha256=committed["post_store_sha256"],
                    committed_event_sha256=committed["event_sha256"],
                )
                return result
            pending_prepared = matching_prepared[0] if matching_prepared else None
            if matching_committed:
                raise StoreCorruptionError(
                    "terminal quarantine COMMITTED audit exists without its canonical event"
                )
            if pending_prepared is not None:
                if (
                    pending_prepared.get("fingerprint") != fingerprint
                    or pending_prepared.get("expected_revision") != expected_revision
                    or pending_prepared.get("expected_state") != expected_state
                    or pending_prepared.get("expected_record_sha256")
                    != expected_record_sha256
                    or file_sha256(self.path) != pending_prepared.get("pre_store_sha256")
                    or pending_prepared.get("authority_sha256")
                    != canonical_json_sha256(normalized_authority)
                    or pending_prepared.get("evidence_sha256")
                    != canonical_json_sha256(normalized_evidence)
                ):
                    raise StoreCorruptionError(
                        "pending terminal quarantine PREPARED audit differs from the retry"
                    )
            if expected_revision != original["revision"]:
                raise RevisionConflict(
                    f"case {case_id} expected revision {expected_revision}, found {original['revision']}"
                )
            if original["state"] != "CLOSED_SUCCESS":
                raise TransitionError(
                    f"quarantine_terminal requires CLOSED_SUCCESS; case is {original['state']}"
                )
            if original.get("terminal_quarantine") is not None:
                raise LimitError("case already consumed its one terminal quarantine")
            actual_record_sha256 = case_record_sha256(original)
            if actual_record_sha256 != expected_record_sha256:
                raise RevisionConflict("terminal case record hash differs from the authorized record")
            if not self.path.is_file() or self.path.is_symlink():
                raise StoreCorruptionError("terminal quarantine requires a regular persisted case-state store")
            pre_store_bytes = self.path.read_bytes()
            pre_store_sha256 = hashlib.sha256(pre_store_bytes).hexdigest()
            backup_directory = self.quarantine_backup_root / case_id
            backup_directory.mkdir(parents=True, exist_ok=True)
            if path_contains_link_or_reparse(backup_directory, stop=self.state_root):
                raise StoreCorruptionError(
                    "quarantine backup directory must not traverse a link or reparse point"
                )
            backup_name = hashlib.sha256(request_id.encode("utf-8")).hexdigest() + ".json"
            backup_path = backup_directory / backup_name
            if backup_path.exists():
                if backup_path.is_symlink() or backup_path.read_bytes() != pre_store_bytes:
                    raise StoreCorruptionError("existing quarantine backup does not match the pre-mutation store")
            else:
                descriptor = os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    with os.fdopen(descriptor, "wb", closefd=False) as stream:
                        stream.write(pre_store_bytes)
                        stream.flush()
                        os.fsync(stream.fileno())
                finally:
                    os.close(descriptor)
            backup_sha256 = file_sha256(backup_path)
            if backup_sha256 != pre_store_sha256:
                raise StoreCorruptionError("quarantine backup hash verification failed")
            case = copy.deepcopy(original)
            quarantined_at = (
                require_utc_timestamp(
                    pending_prepared.get("recorded_at"), "prepared quarantine timestamp"
                )
                if pending_prepared is not None
                else utc_now()
            )
            quarantine_record = {
                "protocol_version": TERMINAL_QUARANTINE_PROTOCOL_VERSION,
                "schema_version": 1,
                "request_id": request_id,
                "prior_state": "CLOSED_SUCCESS",
                "prior_revision": original["revision"],
                "prior_record_sha256": actual_record_sha256,
                "pre_store_sha256": pre_store_sha256,
                "backup_path": backup_path.relative_to(self.state_root).as_posix(),
                "backup_sha256": backup_sha256,
                "authority": normalized_authority,
                "evidence": normalized_evidence,
                "quarantined_at": quarantined_at,
            }
            quarantine_record["record_sha256"] = canonical_json_sha256(quarantine_record)
            case["terminal_quarantine"] = quarantine_record
            case["state"] = "CASE_LOCKED"
            case["resumable_state"] = None
            case["lock_reason"] = (
                "erroneous terminal closure quarantined by exact human authority: "
                + normalized_evidence["reason_code"]
            )
            case["revision"] += 1
            case["updated_at"] = quarantined_at
            result = {
                "case_id": case_id,
                "state": case["state"],
                "revision": case["revision"],
                "idempotent": False,
                "prior_state": "CLOSED_SUCCESS",
                "record_sha256": quarantine_record["record_sha256"],
                "backup_path": quarantine_record["backup_path"],
                "backup_sha256": backup_sha256,
                "external_reconciliation_required": normalized_evidence["externally_published"],
            }
            case["events"][request_id] = {
                "operation": "quarantine_terminal",
                "fingerprint": fingerprint,
                "result": copy.deepcopy(result),
            }
            data["cases"][case_id] = case
            data["revision"] += 1
            data["updated_at"] = quarantined_at
            result["store_revision"] = data["revision"]
            case["events"][request_id]["result"]["store_revision"] = data["revision"]
            _validate_store(data)
            expected_post_store_sha256 = hashlib.sha256(serialized_store_bytes(data)).hexdigest()
            prepared_body = {
                    "phase": "PREPARED",
                    "case_id": case_id,
                    "request_id": request_id,
                    "fingerprint": fingerprint,
                    "expected_revision": expected_revision,
                    "expected_state": expected_state,
                    "expected_record_sha256": expected_record_sha256,
                    "pre_store_sha256": pre_store_sha256,
                    "expected_post_store_sha256": expected_post_store_sha256,
                    "backup_path": quarantine_record["backup_path"],
                    "backup_sha256": backup_sha256,
                    "authority_sha256": canonical_json_sha256(normalized_authority),
                    "evidence_sha256": canonical_json_sha256(normalized_evidence),
                    "recorded_at": quarantined_at,
                }
            if pending_prepared is not None:
                if (
                    pending_prepared.get("expected_post_store_sha256")
                    != expected_post_store_sha256
                    or pending_prepared.get("backup_path")
                    != quarantine_record["backup_path"]
                    or pending_prepared.get("backup_sha256") != backup_sha256
                ):
                    raise StoreCorruptionError(
                        "reconstructed quarantine store differs from PREPARED audit"
                    )
                prepared = pending_prepared
            else:
                prepared = self._append_quarantine_audit_unlocked(
                    prepared_body,
                    audit_records,
                )
            self._write_unlocked(data)
            post_store_sha256 = file_sha256(self.path)
            if post_store_sha256 != expected_post_store_sha256:
                raise StoreCorruptionError("post-quarantine store hash verification failed")
            committed = self._append_quarantine_audit_unlocked(
                {
                    "phase": "COMMITTED",
                    "case_id": case_id,
                    "request_id": request_id,
                    "fingerprint": fingerprint,
                    "pre_store_sha256": pre_store_sha256,
                    "post_store_sha256": post_store_sha256,
                    "backup_sha256": backup_sha256,
                    "prepared_event_sha256": prepared["event_sha256"],
                    "recorded_at": utc_now(),
                    "recovered": False,
                },
                audit_records,
            )
            return {
                **result,
                "pre_store_sha256": pre_store_sha256,
                "post_store_sha256": post_store_sha256,
                "prepared_event_sha256": prepared["event_sha256"],
                "committed_event_sha256": committed["event_sha256"],
            }

    def record_control_failure(
        self,
        case_id: str,
        *,
        category: str,
        fingerprint: str,
        description: str,
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        payload = {"category": category, "fingerprint": fingerprint, "description": description}

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            return self._apply_control_failure(
                case, category=category, fingerprint=fingerprint, description=description
            )

        return self._mutate(
            case_id,
            operation="record_control_failure",
            payload=payload,
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    def retry_control_failure(
        self,
        case_id: str,
        *,
        fingerprint: str,
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        fingerprint = _nonempty(fingerprint, "control failure fingerprint", 256)

        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "CONTROL_FAILURE", "retry_control_failure")
            failure = case["control"]["active_failure"]
            if not isinstance(failure, dict) or failure.get("fingerprint") != fingerprint:
                raise ControlFailureError("operational retry requires the identical fingerprint")
            if case["limits"]["operational_retries"] >= 1:
                raise LimitError("case already consumed its one identical operational retry")
            resume = case["resumable_state"]
            if resume not in CASE_STATES or resume in {"CONTROL_FAILURE", "CLOSED_SUCCESS", "CASE_LOCKED"}:
                raise StoreCorruptionError("control failure does not contain a valid resumable state")
            case["limits"]["operational_retries"] += 1
            case["state"] = resume
            case["resumable_state"] = None
            case["control"]["active_failure"] = None
            return {"retried_fingerprint": fingerprint}

        return self._mutate(
            case_id,
            operation="retry_control_failure",
            payload={"fingerprint": fingerprint},
            request_id=request_id,
            expected_revision=expected_revision,
            callback=change,
        )

    def record_start_helper_preflight(
        self,
        case_id: str,
        *,
        repo_root: Path | str,
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        root = Path(repo_root).expanduser().resolve(strict=False)
        current_state = root / "docs" / "delivery" / "current-state.md"
        if current_state.is_file():
            case = self.get_case(case_id)
            return {
                "case_id": case["case_id"],
                "state": case["state"],
                "revision": case["revision"],
                "control_failure": False,
                "current_state": current_state.as_posix(),
                "idempotent": True,
            }
        root_fingerprint = hashlib.sha256(root.as_posix().encode("utf-8")).hexdigest()
        return self.record_control_failure(
            case_id,
            category="start_helper_missing_current_state",
            fingerprint=f"start-helper-missing-current-state:{root_fingerprint}",
            description=(
                "session start helper expected docs/delivery/current-state.md, but the clean repository does not "
                "provide it; this is a control-protocol failure, not evidence of a product defect"
            ),
            request_id=request_id,
            expected_revision=expected_revision,
        )

    @staticmethod
    def _action_context(
        *,
        actor_role: str,
        actor_thread_id: str | None,
        repository: str | None,
        branch: str | None,
        worktree: str | None,
        pr: str | None,
        thread: str | None,
        universal_bundle: str | None,
        head: str | None,
    ) -> dict[str, str | None]:
        role = _nonempty(actor_role, "actor_role", 128).casefold()
        normalized_repo = normalize_repo_url(repository) if repository is not None else None
        if branch is not None and normalized_repo is None:
            raise ValidationError("action context branch requires repository")
        context: dict[str, str | None] = {
            "actor_role": role,
            "repository": normalized_repo,
            "branch": normalize_binding("branch", branch) if branch is not None else None,
            "worktree": normalize_binding("worktree", worktree) if worktree is not None else None,
            "pr": normalize_binding("pr", pr) if pr is not None else None,
            "thread": normalize_binding("thread", thread) if thread is not None else None,
            "universal_bundle": (
                normalize_binding("universal_bundle", universal_bundle)
                if universal_bundle is not None
                else None
            ),
            "head": require_sha(head, "action head") if head is not None else None,
        }
        # Preserve the schema-2 response shape for legacy callers.  Native
        # runtime identity is an additive boundary check and is emitted only
        # when the caller supplies it.
        if actor_thread_id is not None:
            context["actor_thread_id"] = normalize_binding("thread", actor_thread_id)
        if context["pr"] is not None and normalized_repo is not None:
            pr_repository = str(context["pr"]).rsplit("#", 1)[0]
            if pr_repository != normalized_repo:
                raise ValidationError("action context PR repository must match repository")
        return context

    @staticmethod
    def _case_exclusive_keys(case: Mapping[str, Any]) -> set[str]:
        keys: set[str] = set()
        for record in case["bindings"]["branch"]:
            keys.add(
                _binding_key(
                    "branch", record["value"], repository=record["repository"]
                )
            )
        for kind in EXCLUSIVE_BINDING_KINDS - {"branch"}:
            for value in case["bindings"][kind]:
                keys.add(_binding_key(kind, value))
        return keys

    @staticmethod
    def _context_exclusive_keys(context: Mapping[str, str | None]) -> set[str]:
        keys: set[str] = set()
        repository = context.get("repository")
        branch = context.get("branch")
        if repository is not None and branch is not None:
            keys.add(_binding_key("branch", branch, repository=repository))
        for kind in EXCLUSIVE_BINDING_KINDS - {"branch"}:
            value = context.get(kind)
            if value is not None:
                keys.add(_binding_key(kind, value))
        return keys

    @classmethod
    def _locked_scope_overlap(
        cls,
        blocked: Mapping[str, Any],
        target: Mapping[str, Any],
        context: Mapping[str, str | None],
    ) -> bool:
        blocked_keys = cls._case_exclusive_keys(blocked)
        if blocked_keys & (cls._case_exclusive_keys(target) | cls._context_exclusive_keys(context)):
            return True
        repository = context.get("repository")
        head = context.get("head")
        if repository is not None and head is not None:
            for field in ("review_heads", "current_heads", "repaired_heads", "observed_heads"):
                if blocked["candidate"][field].get(repository) == head:
                    return True
        for field in ("review_heads", "current_heads", "repaired_heads", "observed_heads"):
            for repo, target_head in target["candidate"][field].items():
                for blocked_field in ("review_heads", "current_heads", "repaired_heads", "observed_heads"):
                    if blocked["candidate"][blocked_field].get(repo) == target_head:
                        return True
        return False

    @staticmethod
    def _context_matches_case(
        case: Mapping[str, Any], context: Mapping[str, str | None]
    ) -> bool:
        repository = context.get("repository")
        if repository is not None and repository not in case["bindings"]["repo_url"]:
            return False
        branch = context.get("branch")
        if branch is not None:
            record = {"repository": repository, "value": branch}
            if record not in case["bindings"]["branch"]:
                return False
        for kind in EXCLUSIVE_BINDING_KINDS - {"branch"}:
            value = context.get(kind)
            if value is not None and value not in case["bindings"][kind]:
                return False
        return True

    @staticmethod
    def _expected_action_head(
        case: Mapping[str, Any], action: str, repository: str | None
    ) -> str | None:
        if repository is None:
            return None
        if action == "review_collection":
            return case["candidate"]["review_heads"].get(repository)
        if action in {"repair", "closure_check", "publication", *SEPARATE_AUTHORITY_ACTIONS}:
            return case["candidate"]["current_heads"].get(repository)
        return None

    @staticmethod
    def _action_response(
        case: Mapping[str, Any],
        action: str,
        context: Mapping[str, str | None],
        *,
        allowed: bool,
        reason_code: str,
        reason: str,
        separate_authority_required: bool = False,
        blocked_case_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "protocol_version": ACTION_PROTOCOL_VERSION,
            "schema_version": SCHEMA_VERSION,
            "case_id": case["case_id"],
            "state": case["state"],
            "action": action,
            "actor_role": context["actor_role"],
            "repository": context["repository"],
            "head": context["head"],
            "context": dict(context),
            "limits": copy.deepcopy(case["limits"]),
            "allowed": allowed,
            "reason_codes": [reason_code],
            "reason": reason,
            "separate_authority_required": separate_authority_required,
            "blocked_case_id": blocked_case_id,
        }

    def check_action(
        self,
        case_id: str,
        action: str,
        *,
        actor_role: str,
        actor_thread_id: str | None = None,
        repository: str | None = None,
        branch: str | None = None,
        worktree: str | None = None,
        pr: str | None = None,
        thread: str | None = None,
        universal_bundle: str | None = None,
        head: str | None = None,
        blocked_case_id: str | None = None,
    ) -> dict[str, Any]:
        case_id = canonical_case_id(case_id)
        action = _nonempty(action, "action", 128).casefold()
        context = self._action_context(
            actor_role=actor_role,
            actor_thread_id=actor_thread_id,
            repository=repository,
            branch=branch,
            worktree=worktree,
            pr=pr,
            thread=thread,
            universal_bundle=universal_bundle,
            head=head,
        )
        data = self._read()
        if case_id not in data["cases"]:
            raise ValidationError(f"case not found: {case_id}")
        case = data["cases"][case_id]
        unrelated_locked_case = False
        normalized_blocked_id: str | None = None
        if blocked_case_id is not None:
            normalized_blocked_id = canonical_case_id(blocked_case_id)
            if normalized_blocked_id not in data["cases"]:
                raise ValidationError(f"blocked case not found: {normalized_blocked_id}")
            blocked = data["cases"][normalized_blocked_id]
            if normalized_blocked_id != case_id and blocked["state"] == "CASE_LOCKED":
                if self._locked_scope_overlap(blocked, case, context):
                    return self._action_response(
                        case,
                        action,
                        context,
                        allowed=False,
                        reason_code="LOCKED_CASE_SCOPE_OVERLAP",
                        reason="the requested scope overlaps the exact locked case",
                        blocked_case_id=normalized_blocked_id,
                    )
                unrelated_locked_case = True

        role = str(context["actor_role"])
        if role not in ROLE_ACTIONS:
            return self._action_response(
                case,
                action,
                context,
                allowed=False,
                reason_code="UNKNOWN_ACTOR_ROLE",
                reason="actor_role is not part of the case action protocol",
                blocked_case_id=normalized_blocked_id,
            )
        actor_thread_id = context.get("actor_thread_id")
        if actor_thread_id is not None:
            runtime = case.get("runtime")
            actors = runtime.get("actors") if isinstance(runtime, Mapping) else None
            actor = actors.get(actor_thread_id) if isinstance(actors, Mapping) else None
            if not isinstance(actor, Mapping):
                return self._action_response(
                    case,
                    action,
                    context,
                    allowed=False,
                    reason_code="RUNTIME_ACTOR_UNBOUND",
                    reason="actor thread is not bound by native controller evidence",
                    blocked_case_id=normalized_blocked_id,
                )
            controller_role = str(actor.get("role", ""))
            if controller_role != role:
                response = self._action_response(
                    case,
                    action,
                    context,
                    allowed=False,
                    reason_code="ACTOR_ROLE_MISMATCH",
                    reason="caller role differs from the controller-bound native actor role",
                    blocked_case_id=normalized_blocked_id,
                )
                response["controller_bound_actor_role"] = controller_role
                return response
            context_thread = context.get("thread")
            if context_thread is not None and context_thread != actor_thread_id:
                return self._action_response(
                    case,
                    action,
                    context,
                    allowed=False,
                    reason_code="ACTOR_THREAD_CONTEXT_MISMATCH",
                    reason="thread context differs from the controller-bound native actor thread",
                    blocked_case_id=normalized_blocked_id,
                )
            if context_thread is None:
                context["thread"] = actor_thread_id
        known_actions = set().union(*ROLE_ACTIONS.values())
        if action not in known_actions:
            return self._action_response(
                case,
                action,
                context,
                allowed=False,
                reason_code="UNKNOWN_ACTION",
                reason="action is not part of the case action protocol",
                blocked_case_id=normalized_blocked_id,
            )
        if action not in ROLE_ACTIONS[role]:
            return self._action_response(
                case,
                action,
                context,
                allowed=False,
                reason_code="ROLE_ACTION_DENIED",
                reason=f"{role} is not authorized for {action}",
                blocked_case_id=normalized_blocked_id,
            )
        if action != "case_administration" and context["repository"] is None:
            return self._action_response(
                case,
                action,
                context,
                allowed=False,
                reason_code="REPOSITORY_REQUIRED",
                reason=f"{action} requires a repository context",
                blocked_case_id=normalized_blocked_id,
            )
        repository_mismatch = (
            context["repository"] is not None
            and context["repository"] not in case["bindings"]["repo_url"]
        )
        if repository_mismatch:
            return self._action_response(
                case,
                action,
                context,
                allowed=False,
                reason_code="REPOSITORY_MISMATCH",
                reason="repository is not associated with this case",
                blocked_case_id=normalized_blocked_id,
            )
        if action == "universal_sync" and context["universal_bundle"] is None:
            return self._action_response(
                case,
                action,
                context,
                allowed=False,
                reason_code="EXECUTION_CONTEXT_REQUIRED",
                reason="universal_sync requires the exact bound universal bundle",
                blocked_case_id=normalized_blocked_id,
            )
        if action != "case_administration" and not self._context_exclusive_keys(context):
            return self._action_response(
                case,
                action,
                context,
                allowed=False,
                reason_code="EXECUTION_CONTEXT_REQUIRED",
                reason=f"{action} requires an exact branch, worktree, PR, thread, or universal bundle binding",
                blocked_case_id=normalized_blocked_id,
            )
        if not self._context_matches_case(case, context):
            return self._action_response(
                case,
                action,
                context,
                allowed=False,
                reason_code="CASE_BINDING_MISMATCH",
                reason="an exclusive action context binding does not belong to this case",
                blocked_case_id=normalized_blocked_id,
            )
        if case["state"] in {"CASE_LOCKED", "CONTROL_FAILURE"}:
            return self._action_response(
                case,
                action,
                context,
                allowed=False,
                reason_code="CASE_NOT_ACTIONABLE",
                reason="this exact case is locked or in control failure",
                blocked_case_id=normalized_blocked_id,
            )
        eligible_states = ACTION_ELIGIBLE_STATES.get(action)
        if eligible_states is not None and case["state"] not in eligible_states:
            code = "PUBLICATION_REQUIRES_CLOSED_SUCCESS" if action == "publication" else "ACTION_STATE_DENIED"
            return self._action_response(
                case,
                action,
                context,
                allowed=False,
                reason_code=code,
                reason=f"{action} is not eligible from state {case['state']}",
                blocked_case_id=normalized_blocked_id,
            )
        expected_head = self._expected_action_head(case, action, context["repository"])
        if action in HEAD_REQUIRED_ACTIONS and context["head"] is None:
            return self._action_response(
                case,
                action,
                context,
                allowed=False,
                reason_code="HEAD_REQUIRED",
                reason=f"{action} requires the exact frozen or repaired head",
                blocked_case_id=normalized_blocked_id,
            )
        if action in HEAD_REQUIRED_ACTIONS and expected_head is None:
            return self._action_response(
                case,
                action,
                context,
                allowed=False,
                reason_code="EXPECTED_HEAD_MISSING",
                reason="the case has no canonical frozen or repaired head for this repository",
                blocked_case_id=normalized_blocked_id,
            )
        if expected_head is not None and context["head"] != expected_head:
            return self._action_response(
                case,
                action,
                context,
                allowed=False,
                reason_code="HEAD_DRIFT",
                reason="action head does not match the exact frozen or repaired head",
                blocked_case_id=normalized_blocked_id,
            )
        if action in SEPARATE_AUTHORITY_ACTIONS:
            return self._action_response(
                case,
                action,
                context,
                allowed=False,
                reason_code="SEPARATE_AUTHORITY_REQUIRED",
                reason=f"{action} requires separate authority outside case closure",
                separate_authority_required=True,
                blocked_case_id=normalized_blocked_id,
            )
        if unrelated_locked_case:
            return self._action_response(
                case,
                action,
                context,
                allowed=True,
                reason_code="UNRELATED_CASE_ALLOWED",
                reason="the locked case is case-scoped; this target is unrelated work",
                blocked_case_id=normalized_blocked_id,
            )
        return self._action_response(
            case,
            action,
            context,
            allowed=True,
            reason_code="ACTION_ALLOWED",
            reason="actor, case state, repository, bindings, and head satisfy the action contract",
            blocked_case_id=normalized_blocked_id,
        )


def _normalize_snapshot_path(value: str) -> str:
    raw = unicodedata.normalize("NFC", str(value).replace("\\", "/"))
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SnapshotError(f"snapshot path must be a safe repo-relative POSIX path: {value!r}")
    return path.as_posix()


def _length_prefix(value: int) -> bytes:
    if value < 0:
        raise SnapshotError("snapshot length cannot be negative")
    return value.to_bytes(8, "big")


def canonical_snapshot_hash_from_entries(entries: Iterable[tuple[str, bytes]]) -> str:
    """Hash explicit bytes with the legacy filesystem-entry contract.

    This helper is retained for compatibility and deterministic unit tests. It
    is not an accepted candidate lifecycle contract.
    """
    normalized: dict[str, bytes] = {}
    for raw_path, content in entries:
        path = _normalize_snapshot_path(raw_path)
        if ".git" in PurePosixPath(path).parts:
            continue
        if not isinstance(content, bytes):
            raise SnapshotError(f"snapshot content for {path} must be bytes")
        if path in normalized:
            raise SnapshotError(f"snapshot path normalization collision: {path}")
        normalized[path] = content
    ordered = sorted(normalized.items(), key=lambda item: item[0].encode("utf-8"))
    digest = hashlib.sha256()
    digest.update(LEGACY_FILESYSTEM_SNAPSHOT_MAGIC)
    version = LEGACY_FILESYSTEM_SNAPSHOT_CONTRACT.encode("utf-8")
    digest.update(_length_prefix(len(version)))
    digest.update(version)
    digest.update(_length_prefix(len(ordered)))
    for path, content in ordered:
        encoded_path = path.encode("utf-8")
        digest.update(_length_prefix(len(encoded_path)))
        digest.update(encoded_path)
        digest.update(_length_prefix(len(content)))
        digest.update(content)
    return digest.hexdigest()


def canonical_snapshot_hash(root: Path | str, *, state_root: Path | str | None = None) -> str:
    """Hash mutable filesystem bytes with the legacy, non-lifecycle contract."""
    root_path = Path(root).expanduser().resolve(strict=True)
    if not root_path.is_dir():
        raise SnapshotError(f"snapshot root is not a directory: {root_path}")
    excluded_state = Path(state_root).expanduser().resolve(strict=False) if state_root is not None else None
    entries: list[tuple[str, bytes]] = []
    for path in root_path.rglob("*"):
        if path.is_symlink():
            raise SnapshotError(f"snapshot does not follow symbolic links: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root_path)
        if ".git" in relative.parts:
            continue
        if excluded_state is not None and path_is_within(path, excluded_state):
            continue
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise SnapshotError(f"file changed while snapshot was read: {relative.as_posix()}")
        entries.append((relative.as_posix(), content))
    return canonical_snapshot_hash_from_entries(entries)


def _normalize_git_snapshot_path(value: str) -> str:
    if "\\" in str(value):
        raise SnapshotError(f"Git snapshot path must be a safe repo-relative POSIX path: {value!r}")
    path = _normalize_snapshot_path(value)
    if ".git" in PurePosixPath(path).parts:
        raise SnapshotError(f"Git snapshot path cannot address repository metadata: {value!r}")
    return path


def canonical_git_snapshot_hash_from_entries(
    entries: Iterable[tuple[str, str, bytes]],
) -> str:
    """Hash committed regular-file modes, paths, and Git object bytes."""
    normalized: dict[str, tuple[str, bytes]] = {}
    for raw_path, raw_mode, content in entries:
        path = _normalize_git_snapshot_path(raw_path)
        mode = str(raw_mode)
        if mode not in {"100644", "100755"}:
            raise SnapshotError(f"unsupported Git entry mode {mode!r} at {path}")
        if not isinstance(content, bytes):
            raise SnapshotError(f"Git object content for {path} must be bytes")
        if path in normalized:
            raise SnapshotError(f"snapshot path normalization collision: {path}")
        normalized[path] = (mode, content)

    ordered = sorted(normalized.items(), key=lambda item: item[0].encode("utf-8"))
    digest = hashlib.sha256()
    digest.update(GIT_SNAPSHOT_MAGIC)
    version = SNAPSHOT_CONTRACT.encode("utf-8")
    digest.update(_length_prefix(len(version)))
    digest.update(version)
    digest.update(_length_prefix(len(ordered)))
    for path, (mode, content) in ordered:
        encoded_path = path.encode("utf-8")
        encoded_mode = mode.encode("ascii")
        digest.update(_length_prefix(len(encoded_path)))
        digest.update(encoded_path)
        digest.update(_length_prefix(len(encoded_mode)))
        digest.update(encoded_mode)
        digest.update(_length_prefix(len(content)))
        digest.update(content)
    return digest.hexdigest()


def _run_git(root: Path, *arguments: str) -> bytes:
    executable = resolved_executable("git.exe", "git")
    exact_root = Path(root).resolve(strict=True)
    environment = safe_subprocess_environment(
        executable,
        extra={"GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0"},
    )
    try:
        result = subprocess.run(
            [
                executable,
                "--no-replace-objects",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-c",
                f"safe.directory={exact_root}",
                "-C",
                str(exact_root),
                *arguments,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            env=environment,
        )
    except OSError as exc:
        raise SnapshotError(f"Git could not be executed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if len(detail) > 1000:
            detail = detail[:1000] + "..."
        raise SnapshotError(
            f"Git command failed ({' '.join(arguments)}): {detail or 'no diagnostic output'}"
        )
    return result.stdout


def _single_git_line(value: bytes, label: str) -> str:
    if value.endswith(b"\r\n"):
        value = value[:-2]
    elif value.endswith(b"\n"):
        value = value[:-1]
    if not value or b"\r" in value or b"\n" in value or b"\0" in value:
        raise SnapshotError(f"Git returned a malformed {label}")
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SnapshotError(f"Git returned a non-UTF-8 {label}") from exc


def _git_repository_root(root: Path) -> Path:
    reported = _single_git_line(_run_git(root, "rev-parse", "--show-toplevel"), "repository root")
    try:
        return Path(reported).resolve(strict=True)
    except OSError as exc:
        raise SnapshotError(f"Git repository root cannot be resolved: {exc}") from exc


def _git_head(root: Path) -> str:
    raw = _single_git_line(_run_git(root, "rev-parse", "--verify", "HEAD"), "HEAD")
    try:
        return require_sha(raw, "Git HEAD")
    except ValidationError as exc:
        raise SnapshotError(str(exc)) from exc


def _git_branch(root: Path) -> str:
    raw = _single_git_line(
        _run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD"), "branch"
    )
    try:
        return normalize_binding("branch", raw)
    except ValidationError as exc:
        raise SnapshotError(str(exc)) from exc


def _git_origin(root: Path) -> str:
    raw = _single_git_line(_run_git(root, "remote", "get-url", "origin"), "origin URL")
    try:
        return normalize_repo_url(raw)
    except ValidationError as exc:
        raise SnapshotError(str(exc)) from exc


def _git_tracked_mode(root: Path, relative_path: str) -> str:
    raw = _run_git(root, "ls-files", "--stage", "--error-unmatch", "--", relative_path)
    lines = raw.splitlines()
    if len(lines) != 1:
        raise SnapshotError("authorized action target must identify exactly one tracked file")
    try:
        header, observed_path = lines[0].split(b"\t", 1)
        mode, oid, stage = header.split(b" ")
        decoded_path = observed_path.decode("utf-8", errors="strict").replace("\\", "/")
        decoded_mode = mode.decode("ascii", errors="strict")
    except (ValueError, UnicodeDecodeError) as exc:
        raise SnapshotError("Git returned a malformed tracked-file record") from exc
    if decoded_path != relative_path or stage != b"0" or decoded_mode not in {"100644", "100755"}:
        raise SnapshotError("authorized action target must be a stage-zero regular tracked file")
    if not SHA_PATTERN.fullmatch(oid.decode("ascii", errors="strict")):
        raise SnapshotError("Git returned a malformed target object identifier")
    return decoded_mode


def _git_status_paths(root: Path) -> list[str]:
    raw = _run_git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    records = raw.split(b"\0")
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            raise SnapshotError("Git returned malformed porcelain status")
        status = record[:2]
        raw_path = record[3:]
        try:
            path = raw_path.decode("utf-8", errors="strict").replace("\\", "/")
        except UnicodeDecodeError as exc:
            raise SnapshotError("Git returned a non-UTF-8 status path") from exc
        if status[:1] in {b"R", b"C"} or status[1:2] in {b"R", b"C"}:
            if index >= len(records) or not records[index]:
                raise SnapshotError("Git returned malformed rename status")
            index += 1
        paths.append(normalize_action_path(path))
    return sorted(set(paths))


def _assert_git_worktree_clean(root: Path, phase: str) -> None:
    status = _run_git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if status:
        raise SnapshotError(
            f"candidate Git worktree is not clean {phase}; tracked changes and nonignored untracked files are forbidden"
        )


def _parse_git_tree_entries(raw: bytes) -> list[tuple[str, str, str]]:
    if raw and not raw.endswith(b"\0"):
        raise SnapshotError("Git tree enumeration is not NUL terminated")
    result: list[tuple[str, str, str]] = []
    normalized_paths: set[str] = set()
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            raw_mode, raw_type, raw_oid = header.split(b" ")
            mode = raw_mode.decode("ascii", errors="strict")
            object_type = raw_type.decode("ascii", errors="strict")
            oid = raw_oid.decode("ascii", errors="strict")
            decoded_path = raw_path.decode("utf-8", errors="strict")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SnapshotError("Git returned a malformed tree entry") from exc
        path = _normalize_git_snapshot_path(decoded_path)
        if path in normalized_paths:
            raise SnapshotError(f"snapshot path normalization collision: {path}")
        normalized_paths.add(path)
        if object_type != "blob" or mode not in {"100644", "100755"}:
            raise SnapshotError(
                f"unsupported Git entry type {object_type!r} mode {mode!r} at {path}"
            )
        if not SHA_PATTERN.fullmatch(oid):
            raise SnapshotError(f"Git returned a malformed object identifier for {path}")
        result.append((path, mode, oid))
    return result


def git_object_snapshot(root: Path | str, head: str) -> dict[str, Any]:
    """Return the canonical snapshot of tracked Git objects at exact HEAD."""
    try:
        expected_head = require_sha(head, "snapshot head")
    except ValidationError as exc:
        raise SnapshotError(str(exc)) from exc
    try:
        root_path = Path(root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise SnapshotError(f"snapshot repository root cannot be resolved: {exc}") from exc
    if not root_path.is_dir():
        raise SnapshotError(f"snapshot repository root is not a directory: {root_path}")
    if _git_repository_root(root_path) != root_path:
        raise SnapshotError("snapshot --root must be the exact Git repository root")
    if _single_git_line(
        _run_git(root_path, "rev-parse", "--is-inside-work-tree"), "worktree status"
    ) != "true":
        raise SnapshotError("snapshot repository root is not a Git worktree")

    before_head = _git_head(root_path)
    if before_head != expected_head:
        raise SnapshotError(
            f"Git HEAD drift before snapshot: expected {expected_head}, observed {before_head}"
        )
    _assert_git_worktree_clean(root_path, "before snapshot")

    tree = _parse_git_tree_entries(
        _run_git(root_path, "ls-tree", "-rz", "--full-tree", expected_head)
    )
    entries: list[tuple[str, str, bytes]] = []
    for path, mode, oid in tree:
        entries.append((path, mode, _run_git(root_path, "cat-file", "blob", oid)))
    digest = canonical_git_snapshot_hash_from_entries(entries)

    after_head = _git_head(root_path)
    if after_head != expected_head:
        raise SnapshotError(
            f"Git HEAD drift during snapshot: expected {expected_head}, observed {after_head}"
        )
    _assert_git_worktree_clean(root_path, "after snapshot")
    final_head = _git_head(root_path)
    if final_head != expected_head:
        raise SnapshotError(
            f"Git HEAD drift after snapshot: expected {expected_head}, observed {final_head}"
        )
    return {
        "contract": SNAPSHOT_CONTRACT,
        "head": expected_head,
        "sha256": digest,
        "file_count": len(entries),
    }


def _json_value(value: str, label: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{label} must be valid JSON: {exc}") from exc


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _add_mutation_identity(parser: argparse.ArgumentParser, *, store_revision: bool = False) -> None:
    parser.add_argument("--request-id", required=True)
    if store_revision:
        parser.add_argument("--expected-store-revision", required=True, type=int)
    else:
        parser.add_argument("--expected-revision", required=True, type=int)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=default_state_root())
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    sub = parser.add_subparsers(dest="command", required=True)

    command = sub.add_parser("register")
    command.add_argument("--case-id", required=True)
    command.add_argument("--objective", required=True)
    _add_mutation_identity(command, store_revision=True)

    command = sub.add_parser("show")
    command.add_argument("--case-id", required=True)

    command = sub.add_parser("record-hash")
    command.add_argument("--case-id", required=True)

    sub.add_parser("store-status")

    command = sub.add_parser("list")

    command = sub.add_parser("bind")
    command.add_argument("--case-id", required=True)
    command.add_argument("--kind", required=True, choices=sorted(BINDING_KINDS))
    command.add_argument("--value", required=True)
    command.add_argument("--repository")
    _add_mutation_identity(command)

    command = sub.add_parser("resolve")
    command.add_argument("--kind", required=True, choices=sorted(BINDING_KINDS))
    command.add_argument("--value", required=True)
    command.add_argument("--repository")

    for name in (
        "start-implementation",
        "freeze-findings",
        "close-without-blockers",
        "start-closure-preflight",
    ):
        command = sub.add_parser(name)
        command.add_argument("--case-id", required=True)
        _add_mutation_identity(command)

    command = sub.add_parser("start-review")
    command.add_argument("--case-id", required=True)
    command.add_argument("--cohort-json", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("freeze-candidate")
    command.add_argument("--case-id", required=True)
    command.add_argument("--heads-json", required=True)
    command.add_argument("--snapshots-json", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("add-finding")
    command.add_argument("--case-id", required=True)
    command.add_argument("--finding-json", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("submit-review-completion")
    command.add_argument("--case-id", required=True)
    command.add_argument("--completion-json", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("bind-runtime-actor")
    command.add_argument("--case-id", required=True)
    command.add_argument("--actor-json", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("issue-action-grant")
    command.add_argument("--case-id", required=True)
    command.add_argument("--grant-json", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("claim-action-grant")
    command.add_argument("--case-id", required=True)
    command.add_argument("--claim-json", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("complete-action-grant")
    command.add_argument("--case-id", required=True)
    command.add_argument("--completion-json", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("fail-action-grant")
    command.add_argument("--case-id", required=True)
    command.add_argument("--failure-json", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("quarantine-terminal")
    command.add_argument("--case-id", required=True)
    command.add_argument("--expected-state", required=True, choices=["CLOSED_SUCCESS"])
    command.add_argument("--expected-record-sha256", required=True)
    command.add_argument("--authority-json", required=True)
    command.add_argument("--evidence-json", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("authorize-repair")
    command.add_argument("--case-id", required=True)
    command.add_argument("--finding-ids", required=True)
    command.add_argument("--authority-json", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("complete-repair")
    command.add_argument("--case-id", required=True)
    command.add_argument("--heads-json", required=True)
    command.add_argument("--snapshots-json", required=True)
    command.add_argument("--addressed-ids", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("observe-heads")
    command.add_argument("--case-id", required=True)
    command.add_argument("--heads-json", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("verify-closure-preflight")
    command.add_argument("--case-id", required=True)
    command.add_argument("--review-heads-json", required=True)
    command.add_argument("--repaired-heads-json", required=True)
    command.add_argument("--authorized-ids", required=True)
    command.add_argument("--snapshots-json", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("complete-closure-check")
    command.add_argument("--case-id", required=True)
    command.add_argument("--resolutions-json", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("control-failure")
    command.add_argument("--case-id", required=True)
    command.add_argument("--category", required=True)
    command.add_argument("--fingerprint", required=True)
    command.add_argument("--description", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("retry-control")
    command.add_argument("--case-id", required=True)
    command.add_argument("--fingerprint", required=True)
    _add_mutation_identity(command)

    command = sub.add_parser("start-helper-check")
    command.add_argument("--case-id", required=True)
    command.add_argument("--repo-root", required=True, type=Path)
    _add_mutation_identity(command)

    command = sub.add_parser("action-check")
    command.add_argument("--case-id", required=True)
    command.add_argument("--action", required=True)
    command.add_argument("--actor-role", required=True)
    command.add_argument("--actor-thread-id")
    command.add_argument("--repository")
    command.add_argument("--branch")
    command.add_argument("--worktree")
    command.add_argument("--pr")
    command.add_argument("--thread")
    command.add_argument("--universal-bundle")
    command.add_argument("--head")
    command.add_argument("--blocked-case-id")

    command = sub.add_parser("snapshot")
    command.add_argument("--root", required=True, type=Path)
    command.add_argument("--head", required=True)
    return parser


def execute(args: argparse.Namespace) -> dict[str, Any] | list[dict[str, Any]]:
    store = CaseStore(args.state_root)
    common = {
        "request_id": getattr(args, "request_id", None),
        "expected_revision": getattr(args, "expected_revision", None),
    }
    if args.command == "register":
        return store.register_case(
            args.case_id,
            objective=args.objective,
            request_id=args.request_id,
            expected_store_revision=args.expected_store_revision,
        )
    if args.command == "show":
        return store.get_case(args.case_id)
    if args.command == "record-hash":
        case = store.get_case(args.case_id)
        return {
            "case_id": case["case_id"],
            "state": case["state"],
            "revision": case["revision"],
            "record_sha256": case_record_sha256(case),
        }
    if args.command == "store-status":
        return store.status()
    if args.command == "list":
        return store.list_cases()
    if args.command == "bind":
        return store.bind(
            args.case_id,
            kind=args.kind,
            value=args.value,
            repository=args.repository,
            **common,
        )
    if args.command == "resolve":
        owners = store.resolve_bindings(
            args.kind, args.value, repository=args.repository
        )
        return {
            "case_id": owners[0] if len(owners) == 1 else None,
            "case_ids": owners,
            "ambiguous": len(owners) > 1,
        }
    if args.command == "start-implementation":
        return store.start_implementation(args.case_id, **common)
    if args.command == "freeze-candidate":
        return store.freeze_candidate(
            args.case_id,
            heads=_json_value(args.heads_json, "heads-json"),
            snapshots=_json_value(args.snapshots_json, "snapshots-json"),
            **common,
        )
    if args.command == "start-review":
        return store.start_review(
            args.case_id,
            cohort=_json_value(args.cohort_json, "cohort-json"),
            **common,
        )
    if args.command == "add-finding":
        return store.add_finding(
            args.case_id, finding=_json_value(args.finding_json, "finding-json"), **common
        )
    if args.command == "submit-review-completion":
        return store.submit_review_completion(
            args.case_id,
            completion=_json_value(args.completion_json, "completion-json"),
            **common,
        )
    if args.command == "bind-runtime-actor":
        return store.bind_runtime_actor(
            args.case_id,
            actor=_json_value(args.actor_json, "actor-json"),
            **common,
        )
    if args.command == "issue-action-grant":
        return store.issue_action_grant(
            args.case_id,
            grant=_json_value(args.grant_json, "grant-json"),
            **common,
        )
    if args.command == "claim-action-grant":
        return store.claim_action_grant(
            args.case_id,
            claim=_json_value(args.claim_json, "claim-json"),
            **common,
        )
    if args.command == "complete-action-grant":
        return store.complete_action_grant(
            args.case_id,
            completion=_json_value(args.completion_json, "completion-json"),
            **common,
        )
    if args.command == "fail-action-grant":
        return store.fail_action_grant(
            args.case_id,
            failure=_json_value(args.failure_json, "failure-json"),
            **common,
        )
    if args.command == "quarantine-terminal":
        return store.quarantine_terminal(
            args.case_id,
            expected_state=args.expected_state,
            expected_record_sha256=args.expected_record_sha256,
            authority=_json_value(args.authority_json, "authority-json"),
            evidence=_json_value(args.evidence_json, "evidence-json"),
            **common,
        )
    if args.command == "freeze-findings":
        return store.freeze_findings(args.case_id, **common)
    if args.command == "close-without-blockers":
        return store.close_without_blockers(args.case_id, **common)
    if args.command == "authorize-repair":
        return store.authorize_repair(
            args.case_id,
            finding_ids=_csv(args.finding_ids),
            authority=_json_value(args.authority_json, "authority-json"),
            **common,
        )
    if args.command == "complete-repair":
        return store.complete_repair(
            args.case_id,
            heads=_json_value(args.heads_json, "heads-json"),
            snapshots=_json_value(args.snapshots_json, "snapshots-json"),
            addressed_ids=_csv(args.addressed_ids),
            **common,
        )
    if args.command == "observe-heads":
        return store.observe_heads(
            args.case_id, heads=_json_value(args.heads_json, "heads-json"), **common
        )
    if args.command == "start-closure-preflight":
        return store.start_closure_preflight(args.case_id, **common)
    if args.command == "verify-closure-preflight":
        return store.verify_closure_preflight(
            args.case_id,
            review_heads=_json_value(args.review_heads_json, "review-heads-json"),
            repaired_heads=_json_value(args.repaired_heads_json, "repaired-heads-json"),
            authorized_ids=_csv(args.authorized_ids),
            snapshots=_json_value(args.snapshots_json, "snapshots-json"),
            **common,
        )
    if args.command == "complete-closure-check":
        return store.complete_closure_check(
            args.case_id,
            resolutions=_json_value(args.resolutions_json, "resolutions-json"),
            **common,
        )
    if args.command == "control-failure":
        return store.record_control_failure(
            args.case_id,
            category=args.category,
            fingerprint=args.fingerprint,
            description=args.description,
            **common,
        )
    if args.command == "retry-control":
        return store.retry_control_failure(args.case_id, fingerprint=args.fingerprint, **common)
    if args.command == "start-helper-check":
        return store.record_start_helper_preflight(args.case_id, repo_root=args.repo_root, **common)
    if args.command == "action-check":
        return store.check_action(
            args.case_id,
            args.action,
            actor_role=args.actor_role,
            actor_thread_id=args.actor_thread_id,
            repository=args.repository,
            branch=args.branch,
            worktree=args.worktree,
            pr=args.pr,
            thread=args.thread,
            universal_bundle=args.universal_bundle,
            head=args.head,
            blocked_case_id=args.blocked_case_id,
        )
    if args.command == "snapshot":
        return git_object_snapshot(args.root, args.head)
    raise AssertionError(f"unhandled command: {args.command}")


def _human_output(result: Any) -> str:
    if isinstance(result, dict) and "case_id" in result and "objective" in result:
        return "\n".join(
            [
                f"Case: {result['case_id']}",
                f"State: {result['state']}",
                f"Revision: {result['revision']}",
                f"Objective: {result['objective']}",
            ]
        )
    if isinstance(result, dict):
        lines = []
        for key, value in result.items():
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
            lines.append(f"{key.replace('_', ' ').title()}: {rendered}")
        return "\n".join(lines)
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = execute(args)
    except CaseStateError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, sort_keys=True))
        else:
            print(f"CASE STATE ERROR [{type(exc).__name__}]: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_human_output(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
