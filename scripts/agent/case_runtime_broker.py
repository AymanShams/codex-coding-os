#!/usr/bin/env python3
"""One-shot Windows broker for a presealed canonical single-file action grant.

The production v2 boundary consumes an actorless exact-action capability. A
proposal generator supplies bytes but has no mutation authority. Legacy v1
receipt verification remains only for already-issued grant recovery.
"""

from __future__ import annotations

import argparse
import base64
import copy
import csv
from contextlib import contextmanager
import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from typing import Any, Mapping

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from case_state import (  # noqa: E402
    ACTION_GRANT_CLAIM_PROTOCOL_VERSION,
    ACTION_GRANT_PROTOCOL_VERSION,
    ACTION_GRANT_RESULT_PROTOCOL_VERSION,
    EMPTY_SHA256,
    FileLock,
    PROTECTED_ROOT_KINDS,
    PROPOSAL_DACL_EVIDENCE_MODE,
    PROPOSAL_DACL_EVIDENCE_PROTOCOL_VERSION,
    PROPOSAL_ACTION_CLAIM_PROTOCOL_VERSION,
    PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION,
    PROPOSAL_ACTION_RESULT_PROTOCOL_VERSION,
    STORE_FILENAME,
    TRUSTED_WRITE_PROBE_PROTOCOL_VERSION,
    WINDOWS_DACL_EVIDENCE_PROTOCOL_VERSION,
    WINDOWS_GROUP_MEMBERSHIP_PROTOCOL_VERSION,
    WINDOWS_ISOLATION_EVIDENCE_PROTOCOL_VERSION,
    WINDOWS_PRINCIPAL_PROBE_PROTOCOL_VERSION,
    WINDOWS_REQUIRED_DENY_RIGHTS_MASK,
    AuthorizationError,
    CaseStateError,
    CaseStore,
    SnapshotError,
    StoreCorruptionError,
    ValidationError,
    _assert_git_worktree_clean,
    _git_branch,
    _git_head,
    _git_origin,
    _git_repository_root,
    _git_status_paths,
    _git_tracked_mode,
    _run_git,
    _windows_regular_file_identity,
    canonical_case_id,
    canonical_json_bytes,
    canonical_json_sha256,
    controller_source_pins,
    file_sha256,
    normalize_action_path,
    normalize_binding,
    normalize_repo_url,
    normalized_absolute_path,
    path_contains_link_or_reparse,
    path_is_within,
    proposal_broker_source_pins,
    require_request_id,
    regular_file_identity,
    require_sha,
    require_snapshot_hash,
    require_stable_id,
    require_utc_timestamp,
    require_windows_sid,
    resolved_executable,
    safe_subprocess_environment,
    utc_now,
)

CONTROLLER_RECEIPT_PROTOCOL_VERSION = "ccos-controller-turn-receipt-v2"
WORKER_PROBE_REQUEST_PROTOCOL_VERSION = "ccos-worker-isolation-probe-request-v1"
BROKER_JOURNAL_PROTOCOL_VERSION = "ccos-runtime-broker-journal-v1"
CONTROLLER_KEY_ENVIRONMENT = "CCOS_BROKER_CONTROLLER_KEY_B64"
BROKER_JOURNAL_DIRECTORY = "broker-journal"
POST_REPLACEMENT_EVIDENCE_PROTOCOL_VERSION = (
    "ccos-post-replacement-isolation-evidence-v1"
)


class BrokerError(RuntimeError):
    pass


class BrokerAuthorizationError(BrokerError):
    pass


class BrokerPreflightError(BrokerError):
    pass


def _json_value(raw: str, label: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BrokerPreflightError(f"{label} must be valid JSON: {exc}") from exc


def _controller_key() -> bytes:
    raw = os.environ.get(CONTROLLER_KEY_ENVIRONMENT, "")
    if not raw:
        raise BrokerAuthorizationError(
            f"trusted controller key is absent from {CONTROLLER_KEY_ENVIRONMENT}"
        )
    try:
        key = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError) as exc:
        raise BrokerAuthorizationError("trusted controller key is not canonical base64") from exc
    if len(key) < 32:
        raise BrokerAuthorizationError("trusted controller key must contain at least 256 bits")
    return key


def seal_controller_receipt(body: Mapping[str, Any], key: bytes) -> dict[str, Any]:
    """Controller-library helper. It is deliberately not exposed as a CLI command."""
    expected_fields = {
        "protocol_version", "schema_version", "case_id", "grant_id", "actor_thread_id",
        "actor_turn_id", "action", "operation_id", "base_head", "target_path",
        "proposal_sha256", "proposal_size", "completion_state",
        "native_turn_evidence_sha256", "repository", "branch", "worktree",
        "baseline_sha256", "worker_runtime_root", "worker_online_principal_sid",
        "worker_offline_principal_sid", "sandbox_group_principal_sid",
        "broker_principal_sid", "app_server_sha256", "app_server_version",
        "app_server_executable_path",
        "schema_file_count", "schema_tree_sha256", "sandbox_profile_sha256",
        "app_server_environment_sha256", "live_controller_evidence_sha256",
        "issued_at", "nonce",
    }
    if not isinstance(body, Mapping) or set(body) != expected_fields:
        raise BrokerAuthorizationError("controller receipt body uses an unexpected schema")
    if (
        body.get("protocol_version") != CONTROLLER_RECEIPT_PROTOCOL_VERSION
        or body.get("schema_version") != 2
    ):
        raise BrokerAuthorizationError("controller receipt protocol or schema is unsupported")
    normalized = {
        "protocol_version": CONTROLLER_RECEIPT_PROTOCOL_VERSION,
        "schema_version": 2,
        "case_id": canonical_case_id(str(body.get("case_id", ""))),
        "grant_id": require_stable_id(body.get("grant_id"), "grant id"),
        "actor_thread_id": normalize_binding("thread", str(body.get("actor_thread_id", ""))),
        "actor_turn_id": require_stable_id(body.get("actor_turn_id"), "actor turn id"),
        "action": str(body.get("action", "")),
        "operation_id": require_stable_id(body.get("operation_id"), "operation id"),
        "base_head": require_sha(str(body.get("base_head", "")), "receipt base head"),
        "target_path": normalize_action_path(body.get("target_path")),
        "proposal_sha256": require_snapshot_hash(str(body.get("proposal_sha256", ""))),
        "proposal_size": body.get("proposal_size"),
        "completion_state": str(body.get("completion_state", "")),
        "native_turn_evidence_sha256": require_snapshot_hash(
            str(body.get("native_turn_evidence_sha256", ""))
        ),
        "repository": normalize_repo_url(str(body.get("repository", ""))),
        "branch": normalize_binding("branch", str(body.get("branch", ""))),
        "worktree": normalize_binding("worktree", str(body.get("worktree", ""))),
        "baseline_sha256": require_snapshot_hash(str(body.get("baseline_sha256", ""))),
        "worker_runtime_root": normalized_absolute_path(
            body.get("worker_runtime_root"),
            "worker runtime root",
            reject_links=True,
        )[1],
        "worker_online_principal_sid": require_windows_sid(
            body.get("worker_online_principal_sid"), "Online worker SID"
        ),
        "worker_offline_principal_sid": require_windows_sid(
            body.get("worker_offline_principal_sid"), "Offline worker SID"
        ),
        "sandbox_group_principal_sid": require_windows_sid(
            body.get("sandbox_group_principal_sid"), "sandbox group SID"
        ),
        "broker_principal_sid": require_windows_sid(
            body.get("broker_principal_sid"), "broker SID"
        ),
        "app_server_sha256": require_snapshot_hash(str(body.get("app_server_sha256", ""))),
        "app_server_executable_path": normalized_absolute_path(
            body.get("app_server_executable_path"),
            "App Server executable",
            reject_links=True,
        )[1],
        "app_server_version": require_stable_id(
            body.get("app_server_version"), "App Server version"
        ),
        "schema_file_count": body.get("schema_file_count"),
        "schema_tree_sha256": require_snapshot_hash(str(body.get("schema_tree_sha256", ""))),
        "sandbox_profile_sha256": require_snapshot_hash(
            str(body.get("sandbox_profile_sha256", ""))
        ),
        "app_server_environment_sha256": require_snapshot_hash(
            str(body.get("app_server_environment_sha256", ""))
        ),
        "live_controller_evidence_sha256": require_snapshot_hash(
            str(body.get("live_controller_evidence_sha256", ""))
        ),
        "issued_at": require_utc_timestamp(body.get("issued_at"), "receipt issued_at"),
        "nonce": require_stable_id(body.get("nonce"), "receipt nonce"),
    }
    if normalized["action"] != "implementation":
        raise BrokerAuthorizationError("controller receipt action must be implementation")
    if (isinstance(normalized["proposal_size"], bool)
            or not isinstance(normalized["proposal_size"], int)
            or normalized["proposal_size"] < 0):
        raise BrokerAuthorizationError("controller receipt proposal_size must be a byte count")
    if (isinstance(normalized["schema_file_count"], bool)
            or not isinstance(normalized["schema_file_count"], int)
            or normalized["schema_file_count"] <= 0):
        raise BrokerAuthorizationError("controller receipt schema_file_count must be positive")
    if len({
        normalized["worker_online_principal_sid"],
        normalized["worker_offline_principal_sid"],
        normalized["sandbox_group_principal_sid"],
        normalized["broker_principal_sid"],
    }) != 4:
        raise BrokerAuthorizationError("controller receipt principals must be distinct")
    if normalized["completion_state"] != "COMPLETED":
        raise BrokerAuthorizationError("controller receipt must bind a COMPLETED proposal turn")
    signature = hmac.new(key, canonical_json_bytes(normalized), hashlib.sha256).hexdigest()
    return {**normalized, "signature_hmac_sha256": signature}


def verify_controller_receipt(
    receipt: Mapping[str, Any], grant: Mapping[str, Any], case_id: str, key: bytes
) -> tuple[dict[str, Any], str]:
    if not isinstance(receipt, Mapping) or set(receipt) != {
        "protocol_version", "schema_version", "case_id", "grant_id", "actor_thread_id",
        "actor_turn_id", "action", "operation_id", "base_head", "target_path",
        "proposal_sha256", "proposal_size", "completion_state",
        "native_turn_evidence_sha256", "repository", "branch", "worktree",
        "baseline_sha256", "worker_runtime_root", "worker_online_principal_sid",
        "worker_offline_principal_sid", "sandbox_group_principal_sid",
        "broker_principal_sid", "app_server_sha256", "app_server_version",
        "app_server_executable_path",
        "schema_file_count", "schema_tree_sha256", "sandbox_profile_sha256",
        "app_server_environment_sha256", "live_controller_evidence_sha256",
        "issued_at", "nonce", "signature_hmac_sha256",
    }:
        raise BrokerAuthorizationError("controller receipt uses an unexpected schema")
    signature = require_snapshot_hash(str(receipt.get("signature_hmac_sha256", "")))
    body = {name: value for name, value in receipt.items() if name != "signature_hmac_sha256"}
    normalized = seal_controller_receipt(body, key)
    if not hmac.compare_digest(signature, normalized["signature_hmac_sha256"]):
        raise BrokerAuthorizationError("controller receipt HMAC is invalid")
    if (
        normalized["case_id"] != case_id
        or normalized["grant_id"] != grant["grant_id"]
        or normalized["actor_thread_id"] != grant["actor_thread_id"]
        or normalized["actor_turn_id"] != grant["actor_turn_id"]
        or normalized["action"] != grant["action"]
        or normalized["operation_id"] != grant["operation_id"]
        or normalized["base_head"] != grant["base_head"]
        or normalized["target_path"] != grant["target_path"]
        or normalized["proposal_sha256"] != grant["replacement_sha256"]
        or normalized["proposal_size"] != grant["proposal_size"]
        or normalized["completion_state"] != "COMPLETED"
        or normalized["native_turn_evidence_sha256"] != grant["native_turn_evidence_sha256"]
        or normalized["repository"] != grant["repository"]
        or normalized["branch"] != grant["branch"]
        or normalized["worktree"] != grant["worktree"]
        or normalized["baseline_sha256"] != grant["baseline_sha256"]
        or normalized["worker_runtime_root"] != grant["worker_runtime_root"]
        or normalized["worker_online_principal_sid"] != grant["worker_principal_sid"]
        or normalized["worker_offline_principal_sid"] != grant["model_worker_principal_sid"]
        or normalized["sandbox_group_principal_sid"] != grant["sandbox_group_principal_sid"]
        or normalized["broker_principal_sid"] != grant["broker_principal_sid"]
        or normalized["app_server_sha256"] != grant["app_server_sha256"]
        or normalized["app_server_executable_path"] != grant["app_server_executable_path"]
        or normalized["app_server_version"] != grant["app_server_version"]
        or normalized["schema_file_count"] != grant["schema_file_count"]
        or normalized["schema_tree_sha256"] != grant["schema_tree_sha256"]
        or normalized["sandbox_profile_sha256"] != grant["sandbox_profile_sha256"]
        or normalized["app_server_environment_sha256"] != grant["app_server_environment_sha256"]
        or normalized["live_controller_evidence_sha256"]
        != grant["live_controller_evidence_sha256"]
    ):
        raise BrokerAuthorizationError("controller receipt differs from the canonical action grant")
    digest = canonical_json_sha256(normalized)
    if digest != grant["controller_receipt_sha256"]:
        raise BrokerAuthorizationError("controller receipt digest differs from the canonical grant")
    return normalized, digest


def windows_identity() -> tuple[str, str]:
    if os.name != "nt":
        raise BrokerPreflightError("runtime broker activation is supported only on Windows")
    try:
        executable = resolved_executable("whoami.exe", "whoami")
    except SnapshotError as exc:
        raise BrokerPreflightError("whoami is unavailable") from exc
    result = subprocess.run(
        [executable, "/user", "/fo", "csv", "/nh"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        env=safe_subprocess_environment(executable),
        timeout=30,
    )
    if result.returncode != 0:
        raise BrokerPreflightError("cannot resolve the current Windows principal")
    try:
        rows = list(csv.reader(result.stdout.decode("utf-8", errors="strict").splitlines()))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise BrokerPreflightError("whoami returned malformed identity output") from exc
    if len(rows) != 1 or len(rows[0]) != 2:
        raise BrokerPreflightError("whoami did not return exactly one principal")
    name = rows[0][0].strip()
    sid = require_windows_sid(rows[0][1], "current Windows principal SID")
    if not name:
        raise BrokerPreflightError("current Windows principal name is empty")
    return name, sid


def windows_group_sids() -> list[str]:
    """Return the current token's enabled/mandatory group SIDs without names."""
    if os.name != "nt":
        raise BrokerPreflightError("Windows group evidence is supported only on Windows")
    try:
        executable = resolved_executable("whoami.exe", "whoami")
    except SnapshotError as exc:
        raise BrokerPreflightError("whoami is unavailable") from exc
    result = subprocess.run(
        [executable, "/groups", "/fo", "csv", "/nh"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        env=safe_subprocess_environment(executable),
        timeout=30,
    )
    if result.returncode != 0:
        raise BrokerPreflightError("cannot resolve current Windows token groups")
    try:
        rows = list(csv.reader(result.stdout.decode("utf-8", errors="strict").splitlines()))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise BrokerPreflightError("whoami returned malformed group output") from exc
    group_sids: list[str] = []
    for row in rows:
        if len(row) < 4:
            raise BrokerPreflightError("whoami returned an incomplete group record")
        attributes = row[3].casefold()
        if "enabled" not in attributes and "mandatory" not in attributes:
            continue
        group_sids.append(require_windows_sid(row[2], "current token group SID"))
    normalized = sorted(set(group_sids))
    if not normalized:
        raise BrokerPreflightError("current Windows token has no enabled group evidence")
    return normalized


def _powershell_executable() -> str:
    try:
        return resolved_executable("powershell.exe", "pwsh.exe", "pwsh")
    except SnapshotError as exc:
        raise BrokerPreflightError("PowerShell is unavailable for fixed Windows ACL checks") from exc


def _run_powershell(
    script: str,
    environment: Mapping[str, str],
    *,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    executable = _powershell_executable()
    merged = safe_subprocess_environment(executable, extra=environment)
    run_kwargs: dict[str, Any] = {
        "capture_output": True,
        "check": False,
        "env": merged,
    }
    if input_bytes is None:
        run_kwargs["stdin"] = subprocess.DEVNULL
    else:
        run_kwargs["input"] = input_bytes
    return subprocess.run(
        [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        **run_kwargs,
    )


def inspect_protected_dacls(
    roots: Mapping[str, str],
    denied_principal_sids: list[str],
    broker_sid: str,
    membership_evidence_sha256: str,
) -> dict[str, Any]:
    if set(roots) != set(PROTECTED_ROOT_KINDS):
        raise BrokerPreflightError("DACL inspection roots are incomplete")
    if (not isinstance(denied_principal_sids, list) or len(denied_principal_sids) != 3
            or len(set(denied_principal_sids)) != 3):
        raise BrokerPreflightError("DACL inspection requires Online, Offline, and group SIDs")
    denied_principal_sids = [
        require_windows_sid(item, "denied principal SID") for item in denied_principal_sids
    ]
    broker_sid = require_windows_sid(broker_sid, "broker SID")
    _verify_protected_object_dacls(
        roots, denied_principal_sids, broker_sid
    )
    sandbox_group_sid = denied_principal_sids[2]
    membership_evidence_sha256 = require_snapshot_hash(membership_evidence_sha256)
    script = r"""
$ErrorActionPreference = 'Stop'
$roots = ConvertFrom-Json -InputObject $env:CCOS_ACL_ROOTS_JSON
$principals = @(ConvertFrom-Json -InputObject $env:CCOS_DENIED_PRINCIPALS_JSON | ForEach-Object { $_ })
$sandboxGroup = $env:CCOS_SANDBOX_GROUP_SID
$output = @()
foreach ($item in $roots) {
  $acl = Get-Acl -LiteralPath $item.path
  $owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value.ToUpperInvariant()
  $rootSddl = $acl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::All)
  $parentPath = [System.IO.Directory]::GetParent([string]$item.path).FullName
  $parentAcl = Get-Acl -LiteralPath $parentPath
  $parentOwner = $parentAcl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value.ToUpperInvariant()
  $parentSddl = $parentAcl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::All)
  foreach ($principal in $principals) {
    $sources = @($principal, $sandboxGroup) | Sort-Object -Unique
    $observedSources = @()
    [Int64]$mask = 0
    [Int64]$inheritableMask = 0
    $propagation = @()
    foreach ($rule in $acl.Access) {
      $sid = $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value.ToUpperInvariant()
      if ($sources -contains $sid -and $rule.AccessControlType.ToString() -eq 'Deny' -and -not $rule.IsInherited) {
        $observedSources += $sid
        $mask = $mask -bor [Int64]$rule.FileSystemRights
        $flags = $rule.InheritanceFlags.ToString()
        $propagationFlags = $rule.PropagationFlags.ToString()
        if ($flags -match 'ContainerInherit' -and $flags -match 'ObjectInherit') {
          $propagation += $propagationFlags.ToUpperInvariant()
          if ($rule.PropagationFlags -eq [System.Security.AccessControl.PropagationFlags]::None) {
            $inheritableMask = $inheritableMask -bor [Int64]$rule.FileSystemRights
          }
        }
      }
    }
    [Int64]$parentMask = 0
    foreach ($rule in $parentAcl.Access) {
      $sid = $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value.ToUpperInvariant()
      if ($sources -contains $sid -and $rule.AccessControlType.ToString() -eq 'Deny' -and -not $rule.IsInherited) {
        $observedSources += $sid
        $parentMask = $parentMask -bor [Int64]$rule.FileSystemRights
      }
    }
    $output += [PSCustomObject]@{
      root_kind = $item.root_kind
      principal_sid = $principal
      deny_source_sids = @($observedSources | Sort-Object -Unique)
      path = $item.path
      owner_sid = $owner
      parent_path = $parentPath
      parent_owner_sid = $parentOwner
      root_sddl = $rootSddl
      parent_sddl = $parentSddl
      rights_mask = $mask
      inheritable_rights_mask = $inheritableMask
      parent_rights_mask = $parentMask
      propagation_flags = @($propagation | Sort-Object -Unique)
    }
  }
}
@($output) | ConvertTo-Json -Compress -Depth 5
"""
    root_array = [
        {"root_kind": kind, "path": roots[kind]} for kind in PROTECTED_ROOT_KINDS
    ]
    result = _run_powershell(
        script,
        {
            "CCOS_ACL_ROOTS_JSON": json.dumps(root_array, separators=(",", ":")),
            "CCOS_DENIED_PRINCIPALS_JSON": json.dumps(
                denied_principal_sids, separators=(",", ":")
            ),
            "CCOS_SANDBOX_GROUP_SID": sandbox_group_sid,
        },
    )
    if result.returncode != 0:
        raise BrokerPreflightError("fixed DACL inspection failed")
    try:
        records = json.loads(result.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrokerPreflightError("fixed DACL inspection returned malformed JSON") from exc
    if (not isinstance(records, list)
            or len(records) != len(PROTECTED_ROOT_KINDS) * len(denied_principal_sids)):
        raise BrokerPreflightError("fixed DACL inspection returned incomplete evidence")
    rules: list[dict[str, Any]] = []
    observed: set[tuple[str, str]] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise BrokerPreflightError("fixed DACL inspection returned an invalid record")
        kind = str(record.get("root_kind", ""))
        principal_sid = require_windows_sid(
            record.get("principal_sid"), f"{kind} denied principal SID"
        )
        pair = (kind, principal_sid)
        if (kind not in PROTECTED_ROOT_KINDS
                or principal_sid not in denied_principal_sids or pair in observed):
            raise BrokerPreflightError("fixed DACL inspection duplicated or omitted a root")
        raw_sources = record.get("deny_source_sids")
        if not isinstance(raw_sources, list) or not raw_sources:
            raise BrokerAuthorizationError(f"{kind} effective denial has no source SID")
        deny_sources = sorted(
            {require_windows_sid(item, "deny source SID") for item in raw_sources}
        )
        if not {sandbox_group_sid, principal_sid}.issubset(deny_sources):
            raise BrokerAuthorizationError(
                f"{kind} effective denial for {principal_sid} lacks explicit principal and sandbox-group sources"
            )
        path = normalize_binding("worktree", str(record.get("path", "")))
        owner = require_windows_sid(record.get("owner_sid"), f"{kind} owner SID")
        parent_path = normalize_binding("worktree", str(record.get("parent_path", "")))
        parent_owner = require_windows_sid(
            record.get("parent_owner_sid"), f"{kind} parent owner SID"
        )
        mask = record.get("rights_mask")
        if path != roots[kind]:
            raise BrokerAuthorizationError(f"{kind} DACL path differs from the protected root")
        if owner != broker_sid or owner in denied_principal_sids:
            raise BrokerAuthorizationError(f"{kind} is not owned by the distinct broker principal")
        if parent_path != normalize_binding("worktree", str(Path(path).parent)):
            raise BrokerAuthorizationError(f"{kind} DACL parent path differs")
        if parent_owner != broker_sid:
            raise BrokerAuthorizationError(f"{kind} parent is not owned by the distinct broker principal")
        if isinstance(mask, bool) or not isinstance(mask, int):
            raise BrokerPreflightError(f"{kind} DACL mask is invalid")
        if mask & WINDOWS_REQUIRED_DENY_RIGHTS_MASK != WINDOWS_REQUIRED_DENY_RIGHTS_MASK:
            raise BrokerAuthorizationError(
                f"{kind} lacks explicit worker deny for write, delete, WRITE_DAC, or WRITE_OWNER"
            )
        inheritable_mask = record.get("inheritable_rights_mask")
        if isinstance(inheritable_mask, bool) or not isinstance(inheritable_mask, int):
            raise BrokerPreflightError(f"{kind} inheritable DACL mask is invalid")
        if (inheritable_mask & WINDOWS_REQUIRED_DENY_RIGHTS_MASK
                != WINDOWS_REQUIRED_DENY_RIGHTS_MASK):
            raise BrokerAuthorizationError(
                f"{kind} lacks one full recursive worker DENY with CI, OI, and no propagation escape"
            )
        propagation_flags = record.get("propagation_flags")
        if propagation_flags != ["NONE"]:
            raise BrokerAuthorizationError(
                f"{kind} worker DENY lacks a non-InheritOnly, non-NoPropagate recursive rule"
            )
        parent_mask = record.get("parent_rights_mask")
        if isinstance(parent_mask, bool) or not isinstance(parent_mask, int):
            raise BrokerPreflightError(f"{kind} parent DACL mask is invalid")
        if parent_mask & WINDOWS_REQUIRED_DENY_RIGHTS_MASK != WINDOWS_REQUIRED_DENY_RIGHTS_MASK:
            raise BrokerAuthorizationError(
                f"{kind} parent lacks worker deny for delete-child or ACL takeover"
            )
        rules.append(
            {
                "root_kind": kind,
                "principal_sid": principal_sid,
                "deny_source_sids": deny_sources,
                "path": path,
                "owner_sid": owner,
                "parent_path": parent_path,
                "parent_owner_sid": parent_owner,
                "root_sddl_sha256": hashlib.sha256(
                    str(record.get("root_sddl", "")).encode("utf-8")
                ).hexdigest(),
                "parent_sddl_sha256": hashlib.sha256(
                    str(record.get("parent_sddl", "")).encode("utf-8")
                ).hexdigest(),
                "access_type": "DENY",
                "is_inherited": False,
                "inheritance_flags": ["CONTAINER_INHERIT", "OBJECT_INHERIT"],
                "propagation_flags": ["NONE"],
                "rights_mask": mask,
                "inheritable_rights_mask": inheritable_mask,
                "parent_rights_mask": parent_mask,
            }
        )
        observed.add(pair)
    rules.sort(key=lambda item: (
        PROTECTED_ROOT_KINDS.index(item["root_kind"]),
        denied_principal_sids.index(item["principal_sid"]),
    ))
    return {
        "protocol_version": WINDOWS_DACL_EVIDENCE_PROTOCOL_VERSION,
        "schema_version": 2,
        "denied_principal_sids": denied_principal_sids,
        "membership_evidence_sha256": membership_evidence_sha256,
        "broker_principal_sid": broker_sid,
        "rules": rules,
        "observed_at": utc_now(),
    }


def inspect_proposal_dacls(
    roots: Mapping[str, str],
    denied_principal_sids: list[str],
    broker_sid: str,
) -> dict[str, Any]:
    """Inspect the proposal boundary without depending on worker launch evidence."""
    legacy = inspect_protected_dacls(
        roots,
        denied_principal_sids,
        broker_sid,
        "0" * 64,
    )
    return {
        "protocol_version": PROPOSAL_DACL_EVIDENCE_PROTOCOL_VERSION,
        "schema_version": 1,
        "denied_principal_sids": legacy["denied_principal_sids"],
        "broker_principal_sid": legacy["broker_principal_sid"],
        "rules": legacy["rules"],
        "observed_at": legacy["observed_at"],
    }


def _anchor_digest(root: Path, relative: str) -> str:
    relative = normalize_action_path(relative)
    anchor = root.joinpath(*PurePosixPath(relative).parts)
    if not path_is_within(anchor, root) or not anchor.is_file() or anchor.is_symlink():
        raise BrokerPreflightError("protected-root anchor must be a regular direct file")
    if path_contains_link_or_reparse(anchor, stop=root):
        raise BrokerPreflightError("protected-root anchor traverses a link or reparse point")
    return file_sha256(anchor)


def _permission_denial_code(exc: PermissionError) -> int:
    if os.name == "nt":
        return 5
    return int(getattr(exc, "errno", 0) or 0)


def _attempt_denied_write(root: Path, relative: str) -> tuple[str, int, bool, bool]:
    probe = root.joinpath(*PurePosixPath(normalize_action_path(relative)).parts)
    absent_before = not probe.exists()
    error = "WRITE_SUCCEEDED"
    code = 0
    try:
        descriptor = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, b"CCOS-WORKER-PROBE")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except PermissionError as exc:
        error = "ACCESS_DENIED"
        code = _permission_denial_code(exc)
    except OSError as exc:
        error = type(exc).__name__.upper()
        code = int(getattr(exc, "winerror", None) or getattr(exc, "errno", 0) or 0)
    finally:
        if probe.exists():
            try:
                probe.unlink()
            except OSError:
                pass
    return error, code, absent_before, not probe.exists()


def _attempt_denied_overwrite(anchor: Path) -> tuple[str, int]:
    """Request real write access and rewrite one identical byte if granted."""
    original = anchor.read_bytes()
    error = "OVERWRITE_CAPABILITY_GRANTED"
    code = 0
    try:
        descriptor = os.open(anchor, os.O_WRONLY)
        try:
            if original:
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.write(descriptor, original[:1])
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except PermissionError as exc:
        error = "ACCESS_DENIED"
        code = _permission_denial_code(exc)
    except OSError as exc:
        error = type(exc).__name__.upper()
        code = int(getattr(exc, "winerror", None) or getattr(exc, "errno", 0) or 0)
    return error, code


def _acl_sddl_sha256(root: Path) -> str:
    script = r"""
$ErrorActionPreference = 'Stop'
$acl = Get-Acl -LiteralPath $env:CCOS_PROBE_ROOT
$acl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::All)
"""
    result = _run_powershell(script, {"CCOS_PROBE_ROOT": str(root)})
    if result.returncode != 0:
        raise BrokerPreflightError("cannot read protected-root SDDL")
    return hashlib.sha256(result.stdout.strip()).hexdigest()


def _attempt_denied_acl_operation(
    root: Path, operation: str, worker_sid: str, nonce: str
) -> tuple[str, int]:
    if operation not in {"change_permissions", "take_ownership"}:
        raise AssertionError(operation)
    script = r"""
$ErrorActionPreference = 'Stop'
try {
  $acl = Get-Acl -LiteralPath $env:CCOS_PROBE_ROOT
  if ($env:CCOS_PROBE_OPERATION -eq 'take_ownership') {
    $owner = [System.Security.Principal.SecurityIdentifier]::new($env:CCOS_WORKER_SID)
    $acl.SetOwner($owner)
  } else {
    $sid = [System.Security.Principal.SecurityIdentifier]::new($env:CCOS_WORKER_SID)
    $selector = [Convert]::ToInt32($env:CCOS_PROBE_NONCE.Substring(0, 1), 16) % 2
    $right = if ($selector -eq 0) {
      [System.Security.AccessControl.FileSystemRights]::WriteData
    } else {
      [System.Security.AccessControl.FileSystemRights]::AppendData
    }
    $allow = [System.Security.AccessControl.FileSystemAccessRule]::new(
      $sid,
      $right,
      [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',
      [System.Security.AccessControl.PropagationFlags]::None,
      [System.Security.AccessControl.AccessControlType]::Allow)
    [void]$acl.AddAccessRule($allow)
  }
  [System.IO.DirectoryInfo]::new($env:CCOS_PROBE_ROOT).SetAccessControl($acl)
  exit 0
} catch [System.UnauthorizedAccessException] {
  exit 5
} catch {
  if ($_.Exception.HResult -eq -2147024891) { exit 5 }
  exit 6
}
"""
    result = _run_powershell(
        script,
        {
            "CCOS_PROBE_ROOT": str(root),
            "CCOS_PROBE_OPERATION": operation,
            "CCOS_WORKER_SID": worker_sid,
            "CCOS_PROBE_NONCE": nonce,
        },
    )
    if result.returncode == 5:
        return "ACCESS_DENIED", 5
    if result.returncode == 0:
        return "ACL_OPERATION_SUCCEEDED", 0
    return "ACL_OPERATION_FAILED_OTHER", int(result.returncode)


def _native_operation_probe_paths(
    root: Path, nested_parent_path: str, challenge: str, root_kind: str
) -> dict[str, Path]:
    """Derive the broker-created sacrificial paths used by kernel probes."""
    nested_parent = root.joinpath(
        *PurePosixPath(normalize_action_path(nested_parent_path)).parts
    )
    token = hashlib.sha256(
        f"{challenge}:{root_kind}:native-operations".encode("utf-8")
    ).hexdigest()[:20]
    directory = nested_parent / f".ccos-native-operation-probes-{token}"
    paths = {
        "directory": directory,
        "delete": directory / "delete-anchor.bin",
        "rename_source": directory / "rename-source.bin",
        "rename_destination": directory / "rename-destination.bin",
        "replace_target": directory / "replace-target.bin",
        "replace_source": directory / "replace-source.bin",
        "replace_backup": directory / "replace-backup.bin",
    }
    if any(not path_is_within(path, root) for path in paths.values()):
        raise BrokerPreflightError("native operation probe path escapes its protected root")
    return paths


def _native_operation_probe_bytes(
    challenge: str, root_kind: str, label: str
) -> bytes:
    return (
        b"CCOS-NATIVE-OPERATION-PROBE\0"
        + challenge.encode("ascii")
        + b"\0"
        + root_kind.encode("ascii")
        + b"\0"
        + label.encode("ascii")
    )


def _create_native_operation_probe_directory(directory: Path) -> None:
    if os.name == "nt":
        # Windows gives mode 0o700 an owner-only DACL. The protected parent
        # already supplies the exact inherited DACL that the worker must read.
        directory.mkdir()
        return
    directory.mkdir(mode=0o700)


def _prepare_native_operation_anchors(
    root: Path, nested_parent_path: str, challenge: str, root_kind: str
) -> dict[str, Path]:
    paths = _native_operation_probe_paths(
        root, nested_parent_path, challenge, root_kind
    )
    directory = paths["directory"]
    if os.path.lexists(directory):
        raise BrokerPreflightError("native operation probe directory already exists")
    _create_native_operation_probe_directory(directory)
    try:
        for label in ("delete", "rename_source", "replace_target", "replace_source"):
            path = paths[label]
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as stream:
                    stream.write(_native_operation_probe_bytes(challenge, root_kind, label))
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                os.close(descriptor)
        for label in ("rename_destination", "replace_backup"):
            if os.path.lexists(paths[label]):
                raise BrokerPreflightError("native operation probe destination is not absent")
        return paths
    except BaseException:
        _cleanup_native_operation_anchors(paths)
        raise


def _cleanup_native_operation_anchors(paths: Mapping[str, Path]) -> None:
    expected = {
        "directory", "delete", "rename_source", "rename_destination",
        "replace_target", "replace_source", "replace_backup",
    }
    if set(paths) != expected:
        raise BrokerPreflightError("native operation cleanup path set is invalid")
    directory = paths["directory"]
    for label in (
        "delete", "rename_source", "rename_destination", "replace_target",
        "replace_source", "replace_backup",
    ):
        path = paths[label]
        if os.path.lexists(path):
            if _acl_object_type(path) != "file" or path.parent != directory:
                raise BrokerPreflightError("native operation cleanup encountered an unsafe path")
            _remove_native_operation_probe_path(path, directory=False)
    if os.path.lexists(directory):
        if _acl_object_type(directory) != "directory":
            raise BrokerPreflightError("native operation cleanup directory is unsafe")
        _remove_native_operation_probe_path(directory, directory=True)


def _remove_native_operation_probe_path(path: Path, *, directory: bool) -> None:
    remove = path.rmdir if directory else path.unlink
    try:
        remove()
        return
    except PermissionError:
        if os.name != "nt":
            raise
        attributes = int(getattr(path.stat(), "st_file_attributes", 0))
        readonly = int(getattr(stat, "FILE_ATTRIBUTE_READONLY", 1))
        if attributes & readonly == 0:
            raise
    # Windows commonly marks repository directories read-only. Clear only
    # that attribute on this already-validated sacrificial path and retry once.
    path.chmod(stat.S_IREAD | stat.S_IWRITE)
    remove()


def _verify_native_operation_anchors(
    paths: Mapping[str, Path], challenge: str, root_kind: str
) -> None:
    for label in ("delete", "rename_source", "replace_target", "replace_source"):
        path = paths[label]
        if (
            not os.path.lexists(path)
            or _acl_object_type(path) != "file"
            or path.read_bytes()
            != _native_operation_probe_bytes(challenge, root_kind, label)
        ):
            raise BrokerAuthorizationError(
                f"{root_kind} {label} sacrificial anchor changed during denial probe"
            )
    for label in ("rename_destination", "replace_backup"):
        if os.path.lexists(paths[label]):
            raise BrokerAuthorizationError(
                f"{root_kind} {label} appeared during denial probe"
            )


@contextmanager
def _native_operation_probe_anchors(
    root_requests: list[Mapping[str, Any]], challenge: str
):
    prepared: list[tuple[dict[str, Path], str]] = []
    try:
        for root_request in root_requests:
            root_kind = str(root_request["root_kind"])
            root = Path(str(root_request["path"])).resolve(strict=True)
            paths = _prepare_native_operation_anchors(
                root,
                str(root_request["nested_probe_parent_path"]),
                challenge,
                root_kind,
            )
            prepared.append((paths, root_kind))
        yield
    finally:
        errors: list[str] = []
        for paths, root_kind in reversed(prepared):
            try:
                _cleanup_native_operation_anchors(paths)
            except (OSError, BrokerError, CaseStateError) as exc:
                errors.append(f"{root_kind}:{type(exc).__name__}")
        if errors:
            raise BrokerPreflightError(
                "native operation sacrificial cleanup failed: " + ",".join(errors)
            )


def _attempt_denied_file_capability(
    root: Path,
    nested_parent_path: str,
    challenge: str,
    root_kind: str,
    operation: str,
) -> tuple[str, int]:
    """Call the exact destructive Windows API against sacrificial anchors."""
    if operation not in {"rename", "delete", "replace"}:
        raise AssertionError(operation)
    paths = _native_operation_probe_paths(
        root, nested_parent_path, challenge, root_kind
    )
    _verify_native_operation_anchors(paths, challenge, root_kind)
    script = r"""
$ErrorActionPreference = 'Stop'
$source = @'
using System;
using System.Runtime.InteropServices;
public static class CcosNativeOperationProbe {
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern bool DeleteFileW(string path);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern bool MoveFileExW(string source, string destination, uint flags);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern bool ReplaceFileW(
    string replaced, string replacement, string backup, uint flags,
    IntPtr exclude, IntPtr reserved);
}
'@
Add-Type -TypeDefinition $source
$ok = $false
if ($env:CCOS_PROBE_OPERATION -eq 'delete') {
  $ok = [CcosNativeOperationProbe]::DeleteFileW($env:CCOS_DELETE_ANCHOR)
} elseif ($env:CCOS_PROBE_OPERATION -eq 'rename') {
  $ok = [CcosNativeOperationProbe]::MoveFileExW(
    $env:CCOS_RENAME_SOURCE, $env:CCOS_RENAME_DESTINATION, 1)
} elseif ($env:CCOS_PROBE_OPERATION -eq 'replace') {
  $ok = [CcosNativeOperationProbe]::ReplaceFileW(
    $env:CCOS_REPLACE_TARGET, $env:CCOS_REPLACE_SOURCE,
    $env:CCOS_REPLACE_BACKUP, 0, [IntPtr]::Zero, [IntPtr]::Zero)
} else {
  exit 7
}
if (-not $ok) {
  $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
  if ($code -eq 5) { exit 5 }
  exit 6
}
exit 0
"""
    result = _run_powershell(
        script,
        {
            "CCOS_PROBE_OPERATION": operation,
            "CCOS_DELETE_ANCHOR": str(paths["delete"]),
            "CCOS_RENAME_SOURCE": str(paths["rename_source"]),
            "CCOS_RENAME_DESTINATION": str(paths["rename_destination"]),
            "CCOS_REPLACE_TARGET": str(paths["replace_target"]),
            "CCOS_REPLACE_SOURCE": str(paths["replace_source"]),
            "CCOS_REPLACE_BACKUP": str(paths["replace_backup"]),
        },
    )
    if result.returncode == 5:
        _verify_native_operation_anchors(paths, challenge, root_kind)
        return "ACCESS_DENIED", 5
    if result.returncode == 0:
        return f"{operation.upper()}_CAPABILITY_GRANTED", 0
    return f"{operation.upper()}_CAPABILITY_PROBE_FAILED_OTHER", int(result.returncode)


def _attempt_denied_hard_link(
    anchor: Path, root: Path, relative: str
) -> tuple[str, int, bool, bool]:
    """Attempt the actual hard-link operation at a challenge-derived path."""
    link = root.joinpath(*PurePosixPath(normalize_action_path(relative)).parts)
    absent_before = not link.exists()
    error = "HARD_LINK_SUCCEEDED"
    code = 0
    try:
        os.link(anchor, link)
    except PermissionError as exc:
        error = "ACCESS_DENIED"
        code = _permission_denial_code(exc)
    except OSError as exc:
        error = type(exc).__name__.upper()
        code = int(getattr(exc, "winerror", None) or getattr(exc, "errno", 0) or 0)
    finally:
        if link.exists():
            try:
                link.unlink()
            except OSError:
                pass
    return error, code, absent_before, not link.exists()


_PROBE_TRANSPORT_HARDLINK = re.compile(
    r"\\work\\\.tmp\.driveupload\\[0-9]+\Z", re.IGNORECASE
)


def _windows_hardlink_paths(path: Path) -> list[str]:
    try:
        executable = resolved_executable("fsutil.exe", "fsutil")
    except SnapshotError as exc:
        raise BrokerPreflightError("fsutil is unavailable for probe-only hardlink evidence") from exc
    try:
        result = subprocess.run(
            [executable, "hardlink", "list", str(path.resolve(strict=True))],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            env=safe_subprocess_environment(executable),
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise BrokerPreflightError(
            "probe-only hardlink enumeration timed out"
        ) from exc
    if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
        raise BrokerPreflightError("cannot enumerate probe-only hardlink paths")
    try:
        paths = sorted({
            line.strip()
            for line in result.stdout.decode("utf-8", errors="strict").splitlines()
            if line.strip()
        })
    except UnicodeDecodeError as exc:
        raise BrokerPreflightError("probe-only hardlink paths are not valid UTF-8") from exc
    if not paths:
        raise BrokerPreflightError("probe-only hardlink enumeration is empty")
    return paths


def _probe_regular_file_identity(
    path: Path, *, stop: Path | None = None
) -> dict[str, Any]:
    """Validate an anchor for the read-only worker probe only.

    The action boundary continues to use strict ``regular_file_identity``. This
    helper permits one observed Windows sandbox transport alias only while the
    denied-principal probe is alive. The parent must prove canonical-only links
    again after that process exits and before any broker action.
    """
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValidationError(f"cannot inspect probe-only file identity: {exc}") from exc
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or attributes & reparse_flag
        or path_contains_link_or_reparse(path, stop=stop)
    ):
        raise BrokerAuthorizationError(
            "probe-only file must be regular and must not traverse a reparse point"
        )
    if os.name != "nt":
        strict = regular_file_identity(path, stop=stop)
        return {
            **strict,
            "hardlink_paths": [],
            "transport_hardlink_paths": [],
        }
    first = _windows_regular_file_identity(path)
    paths = _windows_hardlink_paths(path)
    second = _windows_regular_file_identity(path)
    if first != second:
        raise BrokerAuthorizationError(
            "probe-only file identity changed during hardlink inspection"
        )
    volume_id, file_id, link_count, file_size = first
    resolved = str(path.resolve(strict=True))
    if len(resolved) < 3 or resolved[1:3] != ":\\":
        raise BrokerAuthorizationError("probe-only file is not an exact Windows drive path")
    canonical = resolved[2:].casefold()
    normalized = {item.casefold() for item in paths}
    if len(normalized) != len(paths) or canonical not in normalized:
        raise BrokerAuthorizationError(
            "probe-only hardlinks omit or duplicate the canonical file path"
        )
    extras = [item for item in paths if item.casefold() != canonical]
    if (
        link_count != len(paths)
        or link_count not in {1, 2}
        or len(extras) > 1
        or any(not _PROBE_TRANSPORT_HARDLINK.fullmatch(item) for item in extras)
    ):
        raise BrokerAuthorizationError(
            "probe-only file has an unauthorized hardlink path"
        )
    if file_id <= 0:
        raise BrokerAuthorizationError(
            "probe-only filesystem identity is not stable and nonzero"
        )
    stable = {
        "volume_id": volume_id,
        "file_id": file_id,
        "size": file_size,
    }
    absolute_paths = sorted(
        normalize_binding("worktree", resolved[:2] + item) for item in paths
    )
    absolute_extras = sorted(
        normalize_binding("worktree", resolved[:2] + item) for item in extras
    )
    return {
        **stable,
        "number_of_links": link_count,
        "hardlink_paths": absolute_paths,
        "transport_hardlink_paths": absolute_extras,
        "identity_sha256": canonical_json_sha256(stable),
    }


def worker_isolation_probe(request: Mapping[str, Any]) -> dict[str, Any]:
    expected_fields = {
        "protocol_version", "schema_version", "challenge_id", "worker_principal_sid",
        "sandbox_group_principal_sid", "broker_principal_sid", "protected_roots",
        "base_head", "target_path", "expected_status_sha256",
    }
    if not isinstance(request, Mapping) or set(request) != expected_fields:
        raise BrokerPreflightError("worker probe request uses an unexpected schema")
    if request.get("protocol_version") != WORKER_PROBE_REQUEST_PROTOCOL_VERSION or request.get("schema_version") != 1:
        raise BrokerPreflightError("worker probe request protocol or schema version is unsupported")
    challenge = require_stable_id(request.get("challenge_id"), "isolation challenge id")
    expected_worker = require_windows_sid(request.get("worker_principal_sid"), "worker SID")
    sandbox_group_sid = require_windows_sid(
        request.get("sandbox_group_principal_sid"), "sandbox group SID"
    )
    broker_sid = require_windows_sid(request.get("broker_principal_sid"), "broker SID")
    identity_name, observed_worker = windows_identity()
    if observed_worker != expected_worker or observed_worker == broker_sid:
        raise BrokerAuthorizationError("worker probe did not run as the exact distinct worker principal")
    observed_groups = windows_group_sids()
    if sandbox_group_sid not in observed_groups:
        raise BrokerAuthorizationError("worker probe token lacks the exact sandbox group")
    raw_roots = request.get("protected_roots")
    if not isinstance(raw_roots, list) or len(raw_roots) != len(PROTECTED_ROOT_KINDS):
        raise BrokerPreflightError("worker probe request must cover every protected root")
    roots: dict[str, dict[str, str]] = {}
    for raw_root in raw_roots:
        if not isinstance(raw_root, Mapping) or set(raw_root) != {
            "root_kind", "path", "owner_sid", "parent_path", "parent_owner_sid",
            "anchor_path", "anchor_sha256", "nested_probe_parent_path"
        }:
            raise BrokerPreflightError("worker protected-root request uses an unexpected schema")
        kind = str(raw_root.get("root_kind", ""))
        if kind not in PROTECTED_ROOT_KINDS or kind in roots:
            raise BrokerPreflightError("worker protected-root request duplicates or omits a root")
        root = Path(str(raw_root.get("path", ""))).resolve(strict=True)
        roots[kind] = {
            "path": normalize_binding("worktree", str(root)),
            "owner_sid": require_windows_sid(raw_root.get("owner_sid"), f"{kind} owner SID"),
            "parent_path": normalize_binding("worktree", str(raw_root.get("parent_path", ""))),
            "parent_owner_sid": require_windows_sid(
                raw_root.get("parent_owner_sid"), f"{kind} parent owner SID"
            ),
            "anchor_path": normalize_action_path(raw_root.get("anchor_path")),
            "anchor_sha256": require_snapshot_hash(str(raw_root.get("anchor_sha256", ""))),
            "nested_probe_parent_path": normalize_action_path(
                raw_root.get("nested_probe_parent_path")
            ),
        }
        if roots[kind]["owner_sid"] != broker_sid:
            raise BrokerAuthorizationError(f"{kind} request does not name the broker as owner")
        if (roots[kind]["parent_path"] != normalize_binding(
                "worktree", str(Path(roots[kind]["path"]).parent)
            ) or roots[kind]["parent_owner_sid"] != broker_sid):
            raise BrokerAuthorizationError(f"{kind} parent request is not exact and broker-owned")
        nested_parent = root.joinpath(
            *PurePosixPath(roots[kind]["nested_probe_parent_path"]).parts
        )
        if (not path_is_within(nested_parent, root) or not nested_parent.is_dir()
                or nested_parent == root
                or path_contains_link_or_reparse(nested_parent, stop=root)):
            raise BrokerPreflightError(
                f"{kind} nested probe parent must be an existing direct descendant directory"
            )
    target_root = Path(roots["target_root"]["path"])
    base_head = require_sha(str(request.get("base_head", "")), "worker probe base head")
    target_path = normalize_action_path(request.get("target_path"))
    expected_status_sha256 = require_snapshot_hash(
        str(request.get("expected_status_sha256", ""))
    )
    if _git_repository_root(target_root) != target_root or _git_head(target_root) != base_head:
        raise BrokerPreflightError("worker probe target repository or HEAD differs")
    target_anchor = target_root.joinpath(*PurePosixPath(target_path).parts)
    if file_sha256(target_anchor) != roots["target_root"]["anchor_sha256"]:
        raise BrokerPreflightError("worker probe target baseline differs")
    status_before_raw = _run_git(
        target_root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if hashlib.sha256(status_before_raw).hexdigest() != expected_status_sha256:
        raise BrokerPreflightError("worker probe target status differs from the exact request")
    head_before = _git_head(target_root)
    result_roots: list[dict[str, Any]] = []
    for kind in PROTECTED_ROOT_KINDS:
        root_record = roots[kind]
        root = Path(root_record["path"])
        before = _anchor_digest(root, root_record["anchor_path"])
        if before != root_record["anchor_sha256"]:
            raise BrokerPreflightError(f"{kind} anchor differs before worker probe")
        probe_relative = (
            f".ccos-worker-{kind.replace('_root', '')}-probe-"
            + hashlib.sha256(challenge.encode("utf-8")).hexdigest()[:20]
        )
        write_error, write_code, absent_before, absent_after = _attempt_denied_write(
            root, probe_relative
        )
        if (
            write_error != "ACCESS_DENIED"
            or write_code != 5
            or not absent_before
            or not absent_after
        ):
            raise BrokerAuthorizationError(
                f"{kind} root write capability was not denied: "
                f"error={write_error}, code={write_code}, "
                f"absent_before={absent_before}, absent_after={absent_after}"
            )
        nested_probe_relative = (
            root_record["nested_probe_parent_path"]
            + "/.ccos-worker-nested-probe-"
            + hashlib.sha256((challenge + ":" + kind).encode("utf-8")).hexdigest()[:20]
        )
        nested_error, nested_code, nested_absent_before, nested_absent_after = (
            _attempt_denied_write(root, nested_probe_relative)
        )
        if (
            nested_error != "ACCESS_DENIED"
            or nested_code != 5
            or not nested_absent_before
            or not nested_absent_after
        ):
            raise BrokerAuthorizationError(
                f"{kind} nested write capability was not denied"
            )
        anchor = root.joinpath(*PurePosixPath(root_record["anchor_path"]).parts)
        anchor_identity_before_evidence = _probe_regular_file_identity(
            anchor, stop=root
        )
        anchor_identity_before = anchor_identity_before_evidence["identity_sha256"]
        overwrite_error, overwrite_code = _attempt_denied_overwrite(anchor)
        if overwrite_error != "ACCESS_DENIED" or overwrite_code != 5:
            raise BrokerAuthorizationError(
                f"{kind} overwrite capability was not denied"
            )
        replace_error, replace_code = _attempt_denied_file_capability(
            root,
            root_record["nested_probe_parent_path"],
            challenge,
            kind,
            "replace",
        )
        if replace_error != "ACCESS_DENIED" or replace_code != 5:
            raise BrokerAuthorizationError(
                f"{kind} replace capability was not denied"
            )
        rename_error, rename_code = _attempt_denied_file_capability(
            root,
            root_record["nested_probe_parent_path"],
            challenge,
            kind,
            "rename",
        )
        if rename_error != "ACCESS_DENIED" or rename_code != 5:
            raise BrokerAuthorizationError(
                f"{kind} rename capability was not denied"
            )
        delete_error, delete_code = _attempt_denied_file_capability(
            root,
            root_record["nested_probe_parent_path"],
            challenge,
            kind,
            "delete",
        )
        if delete_error != "ACCESS_DENIED" or delete_code != 5:
            raise BrokerAuthorizationError(
                f"{kind} delete capability was not denied"
            )
        hard_link_relative = (
            f".ccos-worker-{kind.replace('_root', '')}-hard-link-"
            + hashlib.sha256((challenge + ":" + kind + ":link").encode("utf-8")).hexdigest()[:20]
        )
        hard_link_error, hard_link_code, hard_link_absent_before, hard_link_absent_after = (
            _attempt_denied_hard_link(anchor, root, hard_link_relative)
        )
        if (
            hard_link_error != "ACCESS_DENIED"
            or hard_link_code != 5
            or not hard_link_absent_before
            or not hard_link_absent_after
        ):
            raise BrokerAuthorizationError(
                f"{kind} hard-link capability was not denied"
            )
        acl_nonce = hashlib.sha256(
            (challenge + ":" + kind + ":acl").encode("utf-8")
        ).hexdigest()
        sddl_before = _acl_sddl_sha256(root)
        dac_error, dac_code = _attempt_denied_acl_operation(
            root, "change_permissions", observed_worker, acl_nonce
        )
        owner_error, owner_code = _attempt_denied_acl_operation(
            root, "take_ownership", observed_worker, acl_nonce
        )
        if dac_error != "ACCESS_DENIED" or dac_code != 5:
            raise BrokerAuthorizationError(
                f"{kind} permission-change capability was not denied"
            )
        if owner_error != "ACCESS_DENIED" or owner_code != 5:
            raise BrokerAuthorizationError(
                f"{kind} ownership-change capability was not denied"
            )
        sddl_after = _acl_sddl_sha256(root)
        after = _anchor_digest(root, root_record["anchor_path"])
        anchor_identity_after_evidence = _probe_regular_file_identity(
            anchor, stop=root
        )
        anchor_identity_after = anchor_identity_after_evidence["identity_sha256"]
        if (
            after != before
            or anchor_identity_after != anchor_identity_before
            or sddl_after != sddl_before
        ):
            raise BrokerAuthorizationError(
                f"{kind} protected identity or DACL changed during denial probe"
            )
        result_roots.append(
            {
                "root_kind": kind,
                "path": root_record["path"],
                "owner_sid": root_record["owner_sid"],
                "parent_path": root_record["parent_path"],
                "parent_owner_sid": root_record["parent_owner_sid"],
                "anchor_path": root_record["anchor_path"],
                "anchor_sha256_before": before,
                "anchor_sha256_after": after,
                "probe_relative_path": probe_relative,
                "write_denial_error": write_error,
                "write_denial_native_code": write_code,
                "probe_absent_before": absent_before,
                "probe_absent_after": absent_after,
                "nested_probe_parent_path": root_record["nested_probe_parent_path"],
                "nested_probe_relative_path": nested_probe_relative,
                "nested_write_denial_error": nested_error,
                "nested_write_denial_native_code": nested_code,
                "nested_probe_absent_before": nested_absent_before,
                "nested_probe_absent_after": nested_absent_after,
                "overwrite_denial_error": overwrite_error,
                "overwrite_denial_native_code": overwrite_code,
                "replace_capability_denial_error": replace_error,
                "replace_capability_denial_native_code": replace_code,
                "rename_capability_denial_error": rename_error,
                "rename_capability_denial_native_code": rename_code,
                "hard_link_relative_path": hard_link_relative,
                "hard_link_denial_error": hard_link_error,
                "hard_link_denial_native_code": hard_link_code,
                "hard_link_absent_before": hard_link_absent_before,
                "hard_link_absent_after": hard_link_absent_after,
                "anchor_identity_sha256_before": anchor_identity_before,
                "anchor_identity_sha256_after": anchor_identity_after,
                "anchor_hardlink_paths_before": anchor_identity_before_evidence[
                    "hardlink_paths"
                ],
                "anchor_hardlink_paths_after": anchor_identity_after_evidence[
                    "hardlink_paths"
                ],
                "anchor_transport_hardlink_paths_before": (
                    anchor_identity_before_evidence["transport_hardlink_paths"]
                ),
                "anchor_transport_hardlink_paths_after": (
                    anchor_identity_after_evidence["transport_hardlink_paths"]
                ),
                "acl_change_nonce": acl_nonce,
                "acl_sddl_sha256_before": sddl_before,
                "acl_sddl_sha256_after": sddl_after,
                "change_permissions_denial_error": dac_error,
                "change_permissions_denial_native_code": dac_code,
                "take_ownership_denial_error": owner_error,
                "take_ownership_denial_native_code": owner_code,
                "delete_capability_denial_error": delete_error,
                "delete_capability_denial_native_code": delete_code,
            }
        )
    status_after_raw = _run_git(
        target_root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if hashlib.sha256(status_after_raw).hexdigest() != expected_status_sha256:
        raise BrokerAuthorizationError("worker probe changed the exact requested Git status")
    return {
        "protocol_version": WINDOWS_PRINCIPAL_PROBE_PROTOCOL_VERSION,
        "schema_version": 1,
        "challenge_id": challenge,
        "worker_principal_sid": observed_worker,
        "worker_identity_name": identity_name,
        "worker_group_sids": observed_groups,
        "protected_roots": result_roots,
        "head_before": head_before,
        "head_after": _git_head(target_root),
        "status_sha256_before": hashlib.sha256(status_before_raw).hexdigest(),
        "status_sha256_after": hashlib.sha256(status_after_raw).hexdigest(),
        "observed_at": utc_now(),
    }


MAX_PROTECTED_ACL_OBJECTS = 20000


def _acl_object_type(path: Path) -> str:
    try:
        details = os.lstat(path)
    except OSError as exc:
        raise BrokerPreflightError("protected ACL object cannot be inspected") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(details.st_mode) or (
        getattr(details, "st_file_attributes", 0) & reparse_flag
    ):
        raise BrokerAuthorizationError(
            "protected ACL inventory cannot traverse a link or reparse point"
        )
    if stat.S_ISDIR(details.st_mode):
        return "directory"
    if stat.S_ISREG(details.st_mode):
        return "file"
    raise BrokerAuthorizationError(
        "protected ACL inventory contains a non-file, non-directory object"
    )


def _protected_acl_inventory(roots: Mapping[str, str]) -> list[dict[str, str]]:
    if set(roots) != set(PROTECTED_ROOT_KINDS):
        raise BrokerPreflightError("protected ACL roots are incomplete")
    priority = {"parent": 0, "descendant": 1, "root": 2}
    inventory: dict[str, dict[str, str]] = {}

    def add(path: Path, scope: str) -> None:
        normalized = normalize_binding("worktree", str(path.resolve(strict=True)))
        object_type = _acl_object_type(Path(normalized))
        if scope in {"root", "parent"} and object_type != "directory":
            raise BrokerAuthorizationError(
                "protected ACL root and parent objects must be directories"
            )
        current = inventory.get(normalized)
        if current is None or priority[scope] > priority[current["scope"]]:
            inventory[normalized] = {
                "path": normalized,
                "object_type": object_type,
                "scope": scope,
            }
        if len(inventory) > MAX_PROTECTED_ACL_OBJECTS:
            raise BrokerPreflightError(
                f"protected ACL inventory exceeds {MAX_PROTECTED_ACL_OBJECTS} objects"
            )

    for kind in PROTECTED_ROOT_KINDS:
        root = Path(str(roots[kind])).resolve(strict=True)
        add(root.parent, "parent")
        pending = [root]
        while pending:
            current = pending.pop()
            add(current, "root" if current == root else "descendant")
            if _acl_object_type(current) != "directory":
                continue
            try:
                with os.scandir(current) as entries:
                    children = sorted(
                        (Path(entry.path) for entry in entries),
                        key=lambda item: item.name.casefold(),
                        reverse=True,
                    )
            except OSError as exc:
                raise BrokerPreflightError(
                    "protected ACL directory cannot be enumerated"
                ) from exc
            for child in children:
                child_type = _acl_object_type(child)
                add(child, "descendant")
                if child_type == "directory":
                    pending.append(child)
    return sorted(
        inventory.values(),
        key=lambda item: (len(Path(item["path"]).parts), item["path"].casefold()),
    )


def _verify_protected_object_dacls(
    roots: Mapping[str, str], denied_principal_sids: list[str], broker_sid: str
) -> None:
    denied = [
        require_windows_sid(item, "denied principal SID")
        for item in denied_principal_sids
    ]
    if len(denied) != 3 or len(set(denied)) != 3:
        raise BrokerPreflightError(
            "protected-object DACL verification principal set is invalid"
        )
    items = _protected_acl_inventory(roots)
    payload = {
        "items": items,
        "denied": denied,
        "broker_sid": require_windows_sid(broker_sid, "broker SID"),
        "deny_mask": WINDOWS_REQUIRED_DENY_RIGHTS_MASK,
    }
    script = r"""
$ErrorActionPreference = 'Stop'
$payload = ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd())
$items = @($payload.items | ForEach-Object { $_ })
$denied = @($payload.denied | ForEach-Object { $_ })
[Int64]$required = [Int64]$payload.deny_mask
$broker = ([string]$payload.broker_sid).ToUpperInvariant()
foreach ($item in $items) {
  $acl = Get-Acl -LiteralPath ([string]$item.path)
  $owner = $acl.GetOwner(
    [System.Security.Principal.SecurityIdentifier]
  ).Value.ToUpperInvariant()
  if ($owner -ne $broker -or -not $acl.AreAccessRulesProtected) {
    throw "protected object owner or inheritance boundary differs"
  }
  foreach ($principal in $denied) {
    [Int64]$mask = 0
    [Int64]$recursiveMask = 0
    [Int64]$nonInheritableMask = 0
    foreach ($rule in $acl.Access) {
      $sid = $rule.IdentityReference.Translate(
        [System.Security.Principal.SecurityIdentifier]
      ).Value.ToUpperInvariant()
      if ($sid -ne ([string]$principal).ToUpperInvariant() -or
          $rule.AccessControlType -ne
            [System.Security.AccessControl.AccessControlType]::Deny -or
          $rule.IsInherited) {
        continue
      }
      [Int64]$rights = [Int64]$rule.FileSystemRights
      $mask = $mask -bor $rights
      if ($rule.InheritanceFlags -eq
            [System.Security.AccessControl.InheritanceFlags]::None) {
        $nonInheritableMask = $nonInheritableMask -bor $rights
      }
      $flags = $rule.InheritanceFlags.ToString()
      if ($flags -match 'ContainerInherit' -and
          $flags -match 'ObjectInherit' -and
          $rule.PropagationFlags -eq
            [System.Security.AccessControl.PropagationFlags]::None) {
        $recursiveMask = $recursiveMask -bor $rights
      }
    }
    if (($mask -band $required) -ne $required) {
      throw "protected object lacks the exact mutation DENY"
    }
    if ($item.object_type -eq 'directory' -and $item.scope -ne 'parent') {
      if (($recursiveMask -band $required) -ne $required) {
        throw "protected directory lacks an exact inheritable mutation DENY"
      }
    } elseif (($nonInheritableMask -band $required) -ne $required) {
      throw "protected file or parent lacks an exact non-inheritable mutation DENY"
    }
  }
}
[PSCustomObject]@{ object_count = $items.Count } | ConvertTo-Json -Compress
"""
    result = _run_powershell(
        script,
        {},
        input_bytes=canonical_json_bytes(payload),
    )
    if result.returncode != 0:
        raise BrokerAuthorizationError(
            "an existing protected object lacks the exact mutation denial"
        )
    try:
        observed = json.loads(result.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrokerPreflightError(
            "protected-object DACL verification returned malformed JSON"
        ) from exc
    if (
        not isinstance(observed, Mapping)
        or observed.get("object_count") != len(items)
    ):
        raise BrokerAuthorizationError(
            "protected-object DACL verification is incomplete"
        )


def _configure_protected_dacls(
    roots: Mapping[str, str], denied_principal_sids: list[str], broker_sid: str
) -> None:
    """Install explicit mutation DENYs on every existing protected object."""
    denied = [require_windows_sid(item, "denied principal SID") for item in denied_principal_sids]
    if len(denied) != 3 or len(set(denied)) != 3 or broker_sid in denied:
        raise BrokerAuthorizationError("DACL configuration principal set is invalid")
    items = _protected_acl_inventory(roots)
    script = r"""
$ErrorActionPreference = 'Stop'
$payload = ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd())
$items = @($payload.items | ForEach-Object { $_ })
$denied = @($payload.denied | ForEach-Object { $_ })
$broker = [System.Security.Principal.SecurityIdentifier]::new([string]$payload.broker_sid)
$rights = [System.Security.AccessControl.FileSystemRights][Int64]$payload.deny_mask
$sandboxGroup = [System.Security.Principal.SecurityIdentifier]::new(
  [string]$denied[2]
)
$system = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
foreach ($item in $items) {
  $currentAcl = Get-Acl -LiteralPath ([string]$item.path)
  $currentOwner = $currentAcl.GetOwner(
    [System.Security.Principal.SecurityIdentifier]
  ).Value.ToUpperInvariant()
  if ($currentOwner -ne $broker.Value.ToUpperInvariant()) {
    throw "protected ACL object is not already broker-owned: $($item.path)"
  }
}
foreach ($item in $items) {
  $path = [string]$item.path
  $acl = Get-Acl -LiteralPath $path
  $acl.SetAccessRuleProtection($true, $true)
  $allowInheritance = [System.Security.AccessControl.InheritanceFlags]::None
  if ($item.object_type -eq 'directory' -and $item.scope -ne 'parent') {
    $allowInheritance = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
  }
  foreach ($principal in @($broker, $system)) {
    [void]$acl.PurgeAccessRules($principal)
    $allowRule = [System.Security.AccessControl.FileSystemAccessRule]::new(
      $principal,
      [System.Security.AccessControl.FileSystemRights]::FullControl,
      $allowInheritance,
      [System.Security.AccessControl.PropagationFlags]::None,
      [System.Security.AccessControl.AccessControlType]::Allow)
    [void]$acl.AddAccessRule($allowRule)
  }
  foreach ($principal in $denied) {
    $sid = [System.Security.Principal.SecurityIdentifier]::new($principal)
    [void]$acl.PurgeAccessRules($sid)
    $inheritance = [System.Security.AccessControl.InheritanceFlags]::None
    if ($item.object_type -eq 'directory' -and $item.scope -ne 'parent') {
      $inheritance = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    }
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
      $sid,
      $rights,
      $inheritance,
      [System.Security.AccessControl.PropagationFlags]::None,
      [System.Security.AccessControl.AccessControlType]::Deny)
    [void]$acl.AddAccessRule($rule)
  }
  $readInheritance = [System.Security.AccessControl.InheritanceFlags]::None
  if ($item.object_type -eq 'directory' -and $item.scope -ne 'parent') {
    $readInheritance = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
  }
  $readRule = [System.Security.AccessControl.FileSystemAccessRule]::new(
    $sandboxGroup,
    [System.Security.AccessControl.FileSystemRights]::ReadAndExecute,
    $readInheritance,
    [System.Security.AccessControl.PropagationFlags]::None,
    [System.Security.AccessControl.AccessControlType]::Allow)
  [void]$acl.AddAccessRule($readRule)
  try {
    if ($item.object_type -eq 'directory') {
      [System.IO.DirectoryInfo]::new($path).SetAccessControl($acl)
    } elseif ($item.object_type -eq 'file') {
      [System.IO.FileInfo]::new($path).SetAccessControl($acl)
    } else {
      throw "unsupported ACL object type"
    }
  } catch {
    throw "protected ACL configuration failed at $path`: $($_.Exception.Message)"
  }
}
"""
    payload = {
        "items": items,
        "denied": denied,
        "broker_sid": require_windows_sid(broker_sid, "broker SID"),
        "deny_mask": WINDOWS_REQUIRED_DENY_RIGHTS_MASK,
    }
    result = _run_powershell(
        script,
        {},
        input_bytes=canonical_json_bytes(payload),
    )
    if result.returncode != 0:
        raise BrokerPreflightError(
            "fixed protected-object DACL configuration failed"
        )


def _protected_acl_paths(roots: Mapping[str, str]) -> list[str]:
    return sorted(
        {item["path"] for item in _protected_acl_inventory(roots)},
        key=lambda item: (len(Path(item).parts), item.casefold()),
        reverse=True,
    )


_DACL_CONTROL_FLAGS = re.compile(r"D:((?:P|AR|AI)*)")


def _canonical_restorable_sddl(value: str) -> str:
    """Drop only protected-DACL auto-inheritance history, not access semantics."""

    def canonicalize(match: re.Match[str]) -> str:
        tokens = re.findall(r"P|AR|AI", match.group(1))
        if "P" in tokens and "AI" in tokens:
            tokens = [token for token in tokens if token != "AI"]
        return "D:" + "".join(tokens)

    return _DACL_CONTROL_FLAGS.sub(canonicalize, value, count=1)


def _snapshot_protected_acls(roots: Mapping[str, str]) -> list[dict[str, str]]:
    paths = _protected_acl_paths(roots)
    script = r"""
$ErrorActionPreference = 'Stop'
$paths = @(ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd()) | ForEach-Object { $_ })
$output = @()
foreach ($path in $paths) {
  $acl = Get-Acl -LiteralPath $path
  $output += [PSCustomObject]@{
    path = [System.IO.Path]::GetFullPath($path)
    owner_sid = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value.ToUpperInvariant()
    sddl = $acl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::All)
  }
}
@($output) | ConvertTo-Json -Compress -Depth 4
"""
    result = _run_powershell(
        script,
        {},
        input_bytes=canonical_json_bytes(paths),
    )
    if result.returncode != 0:
        raise BrokerPreflightError("protected ACL snapshot failed")
    try:
        raw = json.loads(result.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrokerPreflightError("protected ACL snapshot returned malformed JSON") from exc
    if not isinstance(raw, list) or len(raw) != len(paths):
        raise BrokerPreflightError("protected ACL snapshot is incomplete")
    by_path: dict[str, dict[str, str]] = {}
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {"path", "owner_sid", "sddl"}:
            raise BrokerPreflightError("protected ACL snapshot record is invalid")
        path = normalize_binding("worktree", str(item["path"]))
        if path not in paths or path in by_path:
            raise BrokerPreflightError("protected ACL snapshot path is unexpected")
        sddl = _canonical_restorable_sddl(str(item["sddl"]))
        if not sddl or len(sddl) > 262144:
            raise BrokerPreflightError("protected ACL snapshot SDDL is invalid")
        entry = {
            "path": path,
            "owner_sid": require_windows_sid(item["owner_sid"], "ACL owner SID"),
            "sddl": sddl,
            "sddl_sha256": hashlib.sha256(sddl.encode("utf-8")).hexdigest(),
        }
        entry["entry_sha256"] = canonical_json_sha256(entry)
        by_path[path] = entry
    return [by_path[path] for path in paths]


def _normalize_acl_snapshot(snapshot: Any) -> list[dict[str, str]]:
    if not isinstance(snapshot, list) or not snapshot:
        raise BrokerPreflightError("protected ACL snapshot must be a nonempty array")
    normalized: list[dict[str, str]] = []
    observed: set[str] = set()
    for item in snapshot:
        if not isinstance(item, Mapping) or set(item) != {
            "path", "owner_sid", "sddl", "sddl_sha256", "entry_sha256"
        }:
            raise BrokerPreflightError("protected ACL snapshot record is invalid")
        path = normalize_binding("worktree", str(item["path"]))
        if path in observed:
            raise BrokerPreflightError("protected ACL snapshot duplicates a path")
        sddl = str(item["sddl"])
        digest = require_snapshot_hash(str(item["sddl_sha256"]))
        if not sddl or hashlib.sha256(sddl.encode("utf-8")).hexdigest() != digest:
            raise BrokerPreflightError("protected ACL snapshot SDDL digest is invalid")
        entry = {
            "path": path,
            "owner_sid": require_windows_sid(item["owner_sid"], "ACL owner SID"),
            "sddl": sddl,
            "sddl_sha256": digest,
        }
        entry_sha256 = require_snapshot_hash(str(item["entry_sha256"]))
        if entry_sha256 != canonical_json_sha256(entry):
            raise BrokerPreflightError("protected ACL snapshot entry digest is invalid")
        entry["entry_sha256"] = entry_sha256
        normalized.append(entry)
        observed.add(path)
    normalized.sort(
        key=lambda item: (len(Path(item["path"]).parts), item["path"].casefold()),
        reverse=True,
    )
    return normalized


def _restore_protected_acls(snapshot: Any) -> None:
    normalized = _normalize_acl_snapshot(snapshot)
    script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class CcosNativeAclRestore {
  [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
  public static extern bool SetFileSecurity(
    string path,
    uint securityInformation,
    byte[] securityDescriptor);
}
'@
$items = @(ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd()) | ForEach-Object { $_ })
foreach ($item in $items) {
  $acl = Get-Acl -LiteralPath $item.path
  $currentSddl = $acl.GetSecurityDescriptorSddlForm(
    [System.Security.AccessControl.AccessControlSections]::All
  )
  if ($currentSddl -ceq [string]$item.sddl) {
    continue
  }
  $raw = [System.Security.AccessControl.RawSecurityDescriptor]::new([string]$item.sddl)
  $sections = [System.Security.AccessControl.AccessControlSections]::Access
  $currentOwner = $acl.GetOwner(
    [System.Security.Principal.SecurityIdentifier]
  ).Value.ToUpperInvariant()
  $currentGroup = $acl.GetGroup(
    [System.Security.Principal.SecurityIdentifier]
  ).Value.ToUpperInvariant()
  if ($currentOwner -ne $raw.Owner.Value.ToUpperInvariant()) {
    $sections = $sections -bor
      [System.Security.AccessControl.AccessControlSections]::Owner
  }
  if ($currentGroup -ne $raw.Group.Value.ToUpperInvariant()) {
    $sections = $sections -bor
      [System.Security.AccessControl.AccessControlSections]::Group
  }
  $acl.SetSecurityDescriptorSddlForm($item.sddl, $sections)
  try {
    if ([System.IO.Directory]::Exists([string]$item.path)) {
      [System.IO.DirectoryInfo]::new([string]$item.path).SetAccessControl($acl)
    } elseif ([System.IO.File]::Exists([string]$item.path)) {
      [System.IO.FileInfo]::new([string]$item.path).SetAccessControl($acl)
    } else {
      throw "protected ACL restoration path is absent"
    }
  } catch {
    throw "protected ACL restoration failed at $($item.path)`: $($_.Exception.Message)"
  }

  # A DACL-only native write reapplies the signed restorable descriptor without
  # requiring SACL privileges.
  $bytes = New-Object byte[] $raw.BinaryLength
  $raw.GetBinaryForm($bytes, 0)
  [uint32]$daclSecurityInformation = 4
  $daclIsProtected = (($raw.ControlFlags -band
      [System.Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -ne 0)
  if ($daclIsProtected) {
    [uint32]$protectedDaclSecurityInformation =
      [Convert]::ToUInt32('80000000', 16)
    $daclSecurityInformation =
      $daclSecurityInformation -bor $protectedDaclSecurityInformation
  } else {
    $daclSecurityInformation = $daclSecurityInformation -bor [uint32]0x20000000
  }
  if (-not [CcosNativeAclRestore]::SetFileSecurity(
      [string]$item.path,
      $daclSecurityInformation,
      $bytes)) {
    $errorCode = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
    throw "native DACL restoration failed with Win32 error $errorCode"
  }
}
"""
    payload = [
        {
            "path": item["path"],
            "sddl": _canonical_restorable_sddl(item["sddl"]),
        }
        for item in sorted(
            normalized,
            key=lambda entry: (
                len(Path(entry["path"]).parts), entry["path"].casefold()
            ),
        )
    ]
    result = _run_powershell(
        script,
        {},
        input_bytes=canonical_json_bytes(payload),
    )
    if result.returncode != 0:
        raise BrokerPreflightError("protected ACL restoration failed")


def _verify_protected_acl_restore(snapshot: Any) -> None:
    normalized = _normalize_acl_snapshot(snapshot)
    paths = [item["path"] for item in normalized]
    script = r"""
$ErrorActionPreference = 'Stop'
$paths = @(ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd()) | ForEach-Object { $_ })
$output = @()
foreach ($path in $paths) {
  $acl = Get-Acl -LiteralPath $path
  $output += [PSCustomObject]@{
    path = [System.IO.Path]::GetFullPath($path)
    owner_sid = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value.ToUpperInvariant()
    sddl = $acl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::All)
  }
}
@($output) | ConvertTo-Json -Compress -Depth 4
"""
    result = _run_powershell(
        script,
        {},
        input_bytes=canonical_json_bytes(paths),
    )
    if result.returncode != 0:
        raise BrokerPreflightError("protected ACL restore verification failed")
    try:
        raw = json.loads(result.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrokerPreflightError(
            "protected ACL restore verification returned malformed JSON"
        ) from exc
    expected = {item["path"]: item for item in normalized}
    if not isinstance(raw, list) or len(raw) != len(expected):
        raise BrokerAuthorizationError("protected ACL restore verification is incomplete")
    observed: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {"path", "owner_sid", "sddl"}:
            raise BrokerPreflightError("protected ACL restore record is invalid")
        path = normalize_binding("worktree", str(item["path"]))
        reference = expected.get(path)
        if reference is None or path in observed:
            raise BrokerAuthorizationError("protected ACL restore path differs")
        if require_windows_sid(
            item["owner_sid"], "restored ACL owner SID"
        ) != reference["owner_sid"]:
            raise BrokerAuthorizationError(
                f"protected ACL owner was not restored at {path}"
            )
        expected_sddl = _canonical_restorable_sddl(reference["sddl"])
        observed_sddl = _canonical_restorable_sddl(str(item["sddl"]))
        expected_sddl_sha256 = hashlib.sha256(
            expected_sddl.encode("utf-8")
        ).hexdigest()
        observed_sddl_sha256 = hashlib.sha256(
            observed_sddl.encode("utf-8")
        ).hexdigest()
        if (
            observed_sddl != expected_sddl
            or observed_sddl_sha256 != expected_sddl_sha256
        ):
            raise BrokerAuthorizationError(
                "protected ACL exact SDDL was not restored at "
                f"{path}: expected={expected_sddl_sha256}, "
                f"observed={observed_sddl_sha256}"
            )
        observed.add(path)


@contextmanager
def _temporary_protected_dacls(
    roots: Mapping[str, str], denied_principal_sids: list[str], broker_sid: str
):
    snapshot = _snapshot_protected_acls(roots)
    try:
        _configure_protected_dacls(roots, denied_principal_sids, broker_sid)
        yield snapshot
    finally:
        _restore_protected_acls(snapshot)
        _verify_protected_acl_restore(snapshot)


def _probe_environment(
    *, executable: Path, worker_root: Path, extra_path_entries: list[Path]
) -> dict[str, str]:
    system_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")).resolve(strict=False)
    system32 = system_root / "System32"
    worker_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp_root = worker_root / "temp"
    local_root = worker_root / "local-app-data"
    temp_root.mkdir(mode=0o700, exist_ok=True)
    local_root.mkdir(mode=0o700, exist_ok=True)
    entries = [system32, executable.parent, Path(sys.executable).resolve(strict=True).parent]
    entries.extend(extra_path_entries)
    path_value = os.pathsep.join(dict.fromkeys(str(item.resolve(strict=True)) for item in entries))
    return {
        "CODEX_HOME": str(worker_root.resolve(strict=True)),
        "COMSPEC": str(system32 / "cmd.exe"),
        "LOCALAPPDATA": str(local_root.resolve(strict=True)),
        "NO_COLOR": "1",
        "PATH": path_value,
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "SYSTEMDRIVE": system_root.drive or "C:",
        "SYSTEMROOT": str(system_root),
        "TEMP": str(temp_root.resolve(strict=True)),
        "TMP": str(temp_root.resolve(strict=True)),
        "WINDIR": str(system_root),
    }


def _run_fixed_sandbox_probe(
    *,
    executable: Path,
    controller_spec: Mapping[str, Any],
    request: Mapping[str, Any],
    expected_sid: str,
    online: bool,
    readable_roots: list[Path],
    worker_root: Path,
) -> dict[str, Any]:
    git_executable = Path(resolved_executable("git.exe", "git"))
    powershell = Path(resolved_executable("powershell.exe", "pwsh.exe", "pwsh"))
    python_executable = Path(sys.executable).resolve(strict=True)
    environment = _probe_environment(
        executable=executable,
        worker_root=worker_root,
        extra_path_entries=[git_executable.parent, powershell.parent],
    )
    system_root = Path(environment["SYSTEMROOT"])
    fixed_read_roots = [
        *readable_roots,
        executable.parent,
        python_executable.parent,
        git_executable.parent,
        powershell.parent,
        system_root,
        worker_root,
    ]
    state = {
        "permissionProfile": {
            "network": {"enabled": online},
            "file_system": {
                "read": sorted({
                    str(path.resolve(strict=True)) for path in fixed_read_roots
                }),
                "write": [str(worker_root.resolve(strict=True))],
            },
        },
        "codexLinuxSandboxExe": None,
        "sandboxCwd": Path(__file__).resolve().parents[2].as_uri(),
        "useLegacyLandlock": False,
    }
    command = [
        str(executable),
        "sandbox",
        "--sandbox-state-json",
        json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    ]
    if not online:
        command.append("--sandbox-state-disable-network")
    command.extend([
        "--",
        str(python_executable),
        str(Path(__file__).resolve(strict=True)),
        "--json",
        "worker-probe",
        "--request-json",
        json.dumps(dict(request), ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    ])
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        env=environment,
        timeout=180,
    )
    try:
        payload = json.loads(result.stdout.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrokerPreflightError("fixed sandbox worker probe returned malformed JSON") from exc
    if result.returncode != 0 or not isinstance(payload, Mapping) or payload.get("ok") is not True:
        raise BrokerAuthorizationError("fixed sandbox worker probe was denied or failed")
    evidence = payload.get("result")
    if not isinstance(evidence, Mapping) or evidence.get("worker_principal_sid") != expected_sid:
        raise BrokerAuthorizationError("fixed sandbox worker probe returned a different principal")
    return dict(evidence)


def _collect_preissue_dual_probes(
    *,
    roots: Mapping[str, str],
    grant_id: str,
    denied: list[str],
    broker_sid: str,
    root_requests: list[dict[str, Any]],
    base_request: Mapping[str, Any],
    executable: Path,
    controller_spec: Mapping[str, Any],
    online_sid: str,
    offline_sid: str,
    group_sid: str,
    readable_roots: list[Path],
    worker_home: Path,
    journal: "BrokerJournal",
    run_id: str,
    online_role: str = "app_server_host",
    offline_role: str = "model_sandbox",
) -> dict[str, Any]:
    journal.ensure_file()
    snapshot = _snapshot_protected_acls(roots)
    snapshot_sha256 = canonical_json_sha256(snapshot)
    journal.append(
        "ACL_SNAPSHOT",
        run_id,
        protected_acl_snapshot=snapshot,
        protected_acl_snapshot_sha256=snapshot_sha256,
    )
    lockdown_intent = {
        "roots": {kind: roots[kind] for kind in PROTECTED_ROOT_KINDS},
        "denied_principal_sids": denied,
        "broker_principal_sid": broker_sid,
    }
    journal.append(
        "ACL_LOCKDOWN_INTENT",
        run_id,
        protected_acl_snapshot_sha256=snapshot_sha256,
        lockdown_intent=lockdown_intent,
        lockdown_intent_sha256=canonical_json_sha256(lockdown_intent),
    )
    probes: list[dict[str, Any]] = []
    try:
        _configure_protected_dacls(roots, denied, broker_sid)
        for role, principal_sid, online in (
            (require_stable_id(online_role, "online principal role"), online_sid, True),
            (require_stable_id(offline_role, "offline principal role"), offline_sid, False),
        ):
            request = {
                **base_request,
                "challenge_id": require_stable_id(
                    f"{grant_id}-pre-{role}",
                    "worker challenge id",
                ),
                "worker_principal_sid": principal_sid,
            }
            with _native_operation_probe_anchors(
                root_requests, request["challenge_id"]
            ):
                probe = _run_fixed_sandbox_probe(
                    executable=executable,
                    controller_spec=controller_spec,
                    request=request,
                    expected_sid=principal_sid,
                    online=online,
                    readable_roots=readable_roots,
                    worker_root=worker_home / f"isolation-{role}",
                )
            probes.append({
                "principal_role": role,
                "principal_sid": principal_sid,
                "probe": probe,
            })
        membership = {
            "protocol_version": WINDOWS_GROUP_MEMBERSHIP_PROTOCOL_VERSION,
            "schema_version": 1,
            "sandbox_group_sid": group_sid,
            "members": [
                {
                    "principal_role": item["principal_role"],
                    "principal_sid": item["principal_sid"],
                    "group_sids": item["probe"]["worker_group_sids"],
                }
                for item in probes
            ],
            "observed_at": utc_now(),
        }
        membership_sha256 = canonical_json_sha256(membership)
        combined = {
            "denied_principal_sids": denied,
            "membership_evidence_sha256": membership_sha256,
            "principal_probes": probes,
        }
        isolation = {
            "protocol_version": WINDOWS_ISOLATION_EVIDENCE_PROTOCOL_VERSION,
            "schema_version": 2,
            **combined,
            "combined_probe_sha256": canonical_json_sha256(combined),
        }
        dacl_evidence = inspect_protected_dacls(
            roots, denied, broker_sid, membership_sha256
        )
        dacl_sha256 = canonical_json_sha256(dacl_evidence)
        journal.append(
            "ACL_LOCKDOWN_VERIFIED",
            run_id,
            protected_acl_snapshot_sha256=snapshot_sha256,
            preissue_dacl_evidence=dacl_evidence,
            preissue_dacl_evidence_sha256=dacl_sha256,
        )
    except BaseException:
        _restore_protected_acls(snapshot)
        _verify_protected_acl_restore(snapshot)
        journal.append(
            "ACL_RESTORED",
            run_id,
            protected_acl_snapshot_sha256=snapshot_sha256,
            restore_reason="preissue_failure",
        )
        raise
    return {
        "group_membership_evidence": membership,
        "isolation_evidence": isolation,
        "protected_acl_snapshot": snapshot,
        "protected_acl_snapshot_sha256": snapshot_sha256,
        "preissue_dacl_evidence": dacl_evidence,
        "preissue_dacl_evidence_sha256": dacl_sha256,
    }


def collect_dual_profile_isolation_evidence(
    *,
    store: Any,
    controller_spec: Mapping[str, Any],
    grant_core: Mapping[str, Any],
    offline_principal_sid: str,
    sandbox_users_group_sid: str,
) -> dict[str, Any]:
    """Collect the fixed Online+Offline pre-issue evidence bundle.

    This is the sole production collector used by the trusted supervisor.  It
    exposes no command, path, role, or content selector beyond the sealed
    controller spec and grant core.
    """
    executable = Path(str(controller_spec["app_server_executable"])).resolve(strict=True)
    expected_executable_sha256 = require_snapshot_hash(
        str(controller_spec["expected_app_server_sha256"])
    )
    if file_sha256(executable) != expected_executable_sha256:
        raise BrokerAuthorizationError("sandbox launcher differs from its sealed binary digest")
    expected_version = require_stable_id(
        controller_spec["expected_app_server_version"], "App Server version"
    )
    version_result = subprocess.run(
        [str(executable), "--version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        env=_probe_environment(
            executable=executable,
            worker_root=Path(controller_spec["worker_codex_home"]) / "version-probe",
            extra_path_entries=[],
        ),
        timeout=30,
    )
    version_text = version_result.stdout.decode("utf-8", errors="replace").strip()
    if version_result.returncode != 0 or expected_version not in version_text:
        raise BrokerAuthorizationError("sandbox launcher version differs from the sealed version")
    broker_name, broker_sid = windows_identity()
    expected_broker_sid = require_windows_sid(
        controller_spec["broker_principal_sid"], "broker principal SID"
    )
    if broker_sid != expected_broker_sid:
        raise BrokerAuthorizationError("isolation collector is not running as the sealed broker")
    online_sid = require_windows_sid(
        controller_spec["worker_principal_sid"], "Online worker SID"
    )
    offline_sid = require_windows_sid(offline_principal_sid, "Offline worker SID")
    group_sid = require_windows_sid(sandbox_users_group_sid, "sandbox group SID")
    denied = [online_sid, offline_sid, group_sid]
    if len({*denied, broker_sid}) != 4:
        raise BrokerAuthorizationError("isolation collector principals must be distinct")

    target_root = Path(str(grant_core["worktree"])).resolve(strict=True)
    state_root = Path(str(store.state_root)).resolve(strict=True)
    source_root = Path(__file__).resolve().parents[2]
    proposal = Path(str(grant_core["proposal_artifact_path"])).resolve(strict=True)
    proposal_root = proposal.parent
    state_nested = state_root / "probe-descendant"
    proposal_nested = proposal_root / "probe-descendant"
    state_nested.mkdir(mode=0o700, exist_ok=True)
    proposal_nested.mkdir(mode=0o700, exist_ok=True)
    source_pins = controller_source_pins(source_root)
    roots = {
        "target_root": normalize_binding("worktree", str(target_root)),
        "state_root": normalize_binding("worktree", str(state_root)),
        "broker_source_root": normalize_binding("worktree", str(source_root)),
        "proposal_root": normalize_binding("worktree", str(proposal_root)),
    }
    root_requests = [
        {
            "root_kind": "target_root",
            "path": roots["target_root"],
            "owner_sid": broker_sid,
            "parent_path": normalize_binding("worktree", str(target_root.parent)),
            "parent_owner_sid": broker_sid,
            "anchor_path": normalize_action_path(grant_core["target_path"]),
            "anchor_sha256": require_snapshot_hash(str(grant_core["baseline_sha256"])),
            "nested_probe_parent_path": ".git",
        },
        {
            "root_kind": "state_root",
            "path": roots["state_root"],
            "owner_sid": broker_sid,
            "parent_path": normalize_binding("worktree", str(state_root.parent)),
            "parent_owner_sid": broker_sid,
            "anchor_path": STORE_FILENAME,
            "anchor_sha256": file_sha256(Path(store.path)),
            "nested_probe_parent_path": state_nested.name,
        },
        {
            "root_kind": "broker_source_root",
            "path": roots["broker_source_root"],
            "owner_sid": broker_sid,
            "parent_path": normalize_binding("worktree", str(source_root.parent)),
            "parent_owner_sid": broker_sid,
            "anchor_path": source_pins["manifest_path"],
            "anchor_sha256": source_pins["manifest_sha256"],
            "nested_probe_parent_path": "scripts/agent",
        },
        {
            "root_kind": "proposal_root",
            "path": roots["proposal_root"],
            "owner_sid": broker_sid,
            "parent_path": normalize_binding("worktree", str(proposal_root.parent)),
            "parent_owner_sid": broker_sid,
            "anchor_path": normalize_action_path(proposal.name),
            "anchor_sha256": require_snapshot_hash(str(grant_core["replacement_sha256"])),
            "nested_probe_parent_path": proposal_nested.name,
        },
    ]
    base_request = {
        "protocol_version": WORKER_PROBE_REQUEST_PROTOCOL_VERSION,
        "schema_version": 1,
        "worker_principal_sid": online_sid,
        "sandbox_group_principal_sid": group_sid,
        "broker_principal_sid": broker_sid,
        "protected_roots": root_requests,
        "base_head": require_sha(str(grant_core["base_head"]), "grant base head"),
        "target_path": normalize_action_path(grant_core["target_path"]),
        "expected_status_sha256": EMPTY_SHA256,
    }
    readable_roots = [
        target_root, state_root, source_root, proposal_root,
        executable.parent, Path(sys.executable).resolve(strict=True).parent,
    ]
    worker_home = Path(str(controller_spec["worker_codex_home"])).resolve(strict=True)
    if (
        normalize_binding("worktree", str(worker_home))
        != normalize_binding("worktree", str(grant_core["worker_runtime_root"]))
        or not worker_home.is_dir()
        or path_contains_link_or_reparse(worker_home)
    ):
        raise BrokerAuthorizationError(
            "worker runtime root differs from the sealed controller grant"
        )
    protected_and_parents = {
        *(Path(path).resolve(strict=True) for path in roots.values()),
        *(Path(path).resolve(strict=True).parent for path in roots.values()),
    }
    if any(
        worker_home == path
        or path_is_within(worker_home, path)
        or path_is_within(path, worker_home)
        for path in protected_and_parents
    ):
        raise BrokerAuthorizationError(
            "worker runtime root overlaps a protected root or parent"
        )
    grant_id = require_stable_id(grant_core["grant_id"], "grant id")
    case_id = canonical_case_id(str(controller_spec["case_id"]))
    journal = BrokerJournal(Path(store.state_root), case_id, grant_id)
    run_id = f"preissue-{secrets.token_hex(16)}"
    with FileLock(journal.lock_path, timeout=30.0):
        collected = _collect_preissue_dual_probes(
            roots=roots,
            grant_id=grant_id,
            denied=denied,
            broker_sid=broker_sid,
            root_requests=root_requests,
            base_request=base_request,
            executable=executable,
            controller_spec=controller_spec,
            online_sid=online_sid,
            offline_sid=offline_sid,
            group_sid=group_sid,
            readable_roots=readable_roots,
            worker_home=worker_home,
            journal=journal,
            run_id=run_id,
        )
    return {
        "model_worker_principal_sid": offline_sid,
        "sandbox_group_principal_sid": group_sid,
        "denied_principal_sids": denied,
        **collected,
        "collector_broker_identity_sha256": canonical_json_sha256(
            {"broker_name": broker_name, "broker_sid": broker_sid}
        ),
    }


def collect_proposal_isolation_evidence(
    *, store: CaseStore, case_id: str, grant_core: Mapping[str, Any]
) -> dict[str, Any]:
    """Lock proposal roots and prove the broker-owned DACL action boundary."""
    executable = Path(str(grant_core["sandbox_executable_path"])).resolve(
        strict=True
    )
    expected_executable_sha256 = require_snapshot_hash(
        str(grant_core["sandbox_executable_sha256"])
    )
    if (
        not executable.is_file()
        or executable.is_symlink()
        or file_sha256(executable) != expected_executable_sha256
    ):
        raise BrokerAuthorizationError(
            "sandbox launcher differs from the proposal grant binary digest"
        )
    expected_version = require_stable_id(
        grant_core["sandbox_executable_version"], "sandbox executable version"
    )
    version_environment = _probe_environment(
        executable=executable,
        worker_root=Path(str(grant_core["probe_runtime_root"])) / "version-probe",
        extra_path_entries=[],
    )
    version_result = subprocess.run(
        [str(executable), "--version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        env=version_environment,
        timeout=30,
    )
    version_text = (
        version_result.stdout + b"\n" + version_result.stderr
    ).decode("utf-8", errors="replace")
    if version_result.returncode != 0 or expected_version not in version_text:
        raise BrokerAuthorizationError(
            "sandbox launcher version differs from the proposal grant"
        )
    broker_name, broker_sid = windows_identity()
    expected_broker_sid = require_windows_sid(
        grant_core["broker_principal_sid"], "broker principal SID"
    )
    if broker_sid != expected_broker_sid:
        raise BrokerAuthorizationError(
            "proposal isolation collector is not the exact broker principal"
        )
    online_sid = require_windows_sid(
        grant_core["worker_principal_sid"], "Online worker SID"
    )
    offline_sid = require_windows_sid(
        grant_core["model_worker_principal_sid"], "Offline worker SID"
    )
    group_sid = require_windows_sid(
        grant_core["sandbox_group_principal_sid"], "sandbox group SID"
    )
    denied = [online_sid, offline_sid, group_sid]
    if grant_core.get("denied_principal_sids") != denied:
        raise BrokerAuthorizationError(
            "proposal grant denied principals are not exact"
        )
    if len({*denied, broker_sid}) != 4:
        raise BrokerAuthorizationError(
            "proposal isolation principals must be distinct"
        )
    target_root = Path(str(grant_core["worktree"])).resolve(strict=True)
    state_root = Path(str(store.state_root)).resolve(strict=True)
    source_root = Path(__file__).resolve().parents[2]
    proposal = Path(str(grant_core["proposal_artifact_path"])).resolve(
        strict=True
    )
    proposal_root = proposal.parent
    probe_runtime_root = Path(str(grant_core["probe_runtime_root"])).resolve(
        strict=True
    )
    if not probe_runtime_root.is_dir() or path_contains_link_or_reparse(
        probe_runtime_root
    ):
        raise BrokerAuthorizationError(
            "proposal probe runtime root is not an exact regular directory"
        )
    roots = {
        "target_root": normalize_binding("worktree", str(target_root)),
        "state_root": normalize_binding("worktree", str(state_root)),
        "broker_source_root": normalize_binding("worktree", str(source_root)),
        "proposal_root": normalize_binding("worktree", str(proposal_root)),
    }
    protected_and_parents = {
        *(Path(path).resolve(strict=True) for path in roots.values()),
        *(Path(path).resolve(strict=True).parent for path in roots.values()),
    }
    if any(
        probe_runtime_root == path
        or path_is_within(probe_runtime_root, path)
        or path_is_within(path, probe_runtime_root)
        for path in protected_and_parents
    ):
        raise BrokerAuthorizationError(
            "proposal probe runtime root overlaps a protected root or parent"
        )
    grant_id = require_stable_id(grant_core["grant_id"], "grant id")
    canonical_id = canonical_case_id(case_id)
    journal = BrokerJournal(Path(store.state_root), canonical_id, grant_id)
    run_id = f"preissue-{secrets.token_hex(16)}"
    with FileLock(journal.lock_path, timeout=30.0):
        journal.ensure_file()
        snapshot = _snapshot_protected_acls(roots)
        snapshot_sha256 = canonical_json_sha256(snapshot)
        journal.append(
            "ACL_SNAPSHOT",
            run_id,
            protected_acl_snapshot=snapshot,
            protected_acl_snapshot_sha256=snapshot_sha256,
        )
        lockdown_intent = {
            "roots": {kind: roots[kind] for kind in PROTECTED_ROOT_KINDS},
            "denied_principal_sids": denied,
            "broker_principal_sid": broker_sid,
        }
        journal.append(
            "ACL_LOCKDOWN_INTENT",
            run_id,
            protected_acl_snapshot_sha256=snapshot_sha256,
            lockdown_intent=lockdown_intent,
            lockdown_intent_sha256=canonical_json_sha256(lockdown_intent),
        )
        try:
            _configure_protected_dacls(roots, denied, broker_sid)
            dacl_evidence = inspect_proposal_dacls(roots, denied, broker_sid)
            dacl_sha256 = canonical_json_sha256(dacl_evidence)
            journal.append(
                "ACL_LOCKDOWN_VERIFIED",
                run_id,
                protected_acl_snapshot_sha256=snapshot_sha256,
                preissue_dacl_evidence=dacl_evidence,
                preissue_dacl_evidence_sha256=dacl_sha256,
            )
        except BaseException:
            _restore_protected_acls(snapshot)
            _verify_protected_acl_restore(snapshot)
            journal.append(
                "ACL_RESTORED",
                run_id,
                protected_acl_snapshot_sha256=snapshot_sha256,
                restore_reason="preissue_failure",
            )
            raise
    return {
        "protected_acl_snapshot": snapshot,
        "protected_acl_snapshot_sha256": snapshot_sha256,
        "preissue_dacl_evidence": dacl_evidence,
        "preissue_dacl_evidence_sha256": dacl_sha256,
        "collector_broker_identity_sha256": canonical_json_sha256(
            {"broker_name": broker_name, "broker_sid": broker_sid}
        ),
    }


def _collect_post_replacement_isolation_evidence(
    store: CaseStore,
    grant: Mapping[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    """Repeat both fixed worker probes while the replacement and DENYs are live."""
    proposal_protocol = (
        grant.get("protocol_version") == PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION
    )
    executable_field = (
        "sandbox_executable_path" if proposal_protocol else "app_server_executable_path"
    )
    executable_hash_field = (
        "sandbox_executable_sha256" if proposal_protocol else "app_server_sha256"
    )
    worker_root_field = "probe_runtime_root" if proposal_protocol else "worker_runtime_root"
    online_role = "proposal_generator" if proposal_protocol else "app_server_host"
    offline_role = "offline_sandbox" if proposal_protocol else "model_sandbox"
    executable = Path(str(grant[executable_field])).resolve(strict=True)
    if (
        not executable.is_file()
        or executable.is_symlink()
        or file_sha256(executable) != grant[executable_hash_field]
    ):
        raise BrokerAuthorizationError(
            "post-probe sandbox executable differs from the grant"
        )
    root = Path(str(grant["worktree"])).resolve(strict=True)
    state_root = Path(str(grant["state_root"])).resolve(strict=True)
    source_root = Path(str(grant["broker_source_root"])).resolve(strict=True)
    proposal_root = Path(str(grant["proposal_root"])).resolve(strict=True)
    worker_root = Path(str(grant[worker_root_field])).resolve(strict=True)
    target = root.joinpath(*PurePosixPath(grant["target_path"]).parts)
    if file_sha256(target) != grant["replacement_sha256"]:
        raise BrokerAuthorizationError("post-probe target does not contain replacement bytes")
    status_raw = _run_git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    status_sha256 = hashlib.sha256(status_raw).hexdigest()
    if _git_status_paths(root) != grant["allowed_paths"]:
        raise BrokerAuthorizationError("post-probe Git status exceeds the action grant")
    if proposal_protocol:
        dacl_evidence = inspect_proposal_dacls(
            _protected_roots(grant),
            grant["denied_principal_sids"],
            grant["broker_principal_sid"],
        )
        normalized_dacl = CaseStore._normalize_dacl_evidence(dacl_evidence, grant)
        body = {
            "protocol_version": POST_REPLACEMENT_EVIDENCE_PROTOCOL_VERSION,
            "schema_version": 2,
            "evidence_mode": PROPOSAL_DACL_EVIDENCE_MODE,
            "grant_id": grant["grant_id"],
            "run_id": require_stable_id(run_id, "broker run id"),
            "target_sha256": grant["replacement_sha256"],
            "status_sha256": status_sha256,
            "observed_status_paths": grant["allowed_paths"],
            "dacl_evidence": normalized_dacl,
            "dacl_evidence_sha256": canonical_json_sha256(normalized_dacl),
            "protected_acl_snapshot_sha256": grant[
                "protected_acl_snapshot_sha256"
            ],
            "observed_at": utc_now(),
        }
        return {
            **body,
            "post_replacement_evidence_sha256": canonical_json_sha256(body),
        }
    pre_roots = {
        item["root_kind"]: item
        for item in grant["isolation_evidence"]["principal_probes"][0]["probe"][
            "protected_roots"
        ]
    }
    if set(pre_roots) != set(PROTECTED_ROOT_KINDS):
        raise BrokerAuthorizationError("preissue protected-root evidence is incomplete")
    root_requests = [
        {
            "root_kind": "target_root",
            "path": normalize_binding("worktree", str(root)),
            "owner_sid": grant["broker_principal_sid"],
            "parent_path": normalize_binding("worktree", str(root.parent)),
            "parent_owner_sid": grant["broker_principal_sid"],
            "anchor_path": grant["target_path"],
            "anchor_sha256": grant["replacement_sha256"],
            "nested_probe_parent_path": pre_roots["target_root"][
                "nested_probe_parent_path"
            ],
        },
        {
            "root_kind": "state_root",
            "path": normalize_binding("worktree", str(state_root)),
            "owner_sid": grant["broker_principal_sid"],
            "parent_path": normalize_binding("worktree", str(state_root.parent)),
            "parent_owner_sid": grant["broker_principal_sid"],
            "anchor_path": STORE_FILENAME,
            "anchor_sha256": file_sha256(store.path),
            "nested_probe_parent_path": pre_roots["state_root"][
                "nested_probe_parent_path"
            ],
        },
        {
            "root_kind": "broker_source_root",
            "path": normalize_binding("worktree", str(source_root)),
            "owner_sid": grant["broker_principal_sid"],
            "parent_path": normalize_binding("worktree", str(source_root.parent)),
            "parent_owner_sid": grant["broker_principal_sid"],
            "anchor_path": pre_roots["broker_source_root"]["anchor_path"],
            "anchor_sha256": pre_roots["broker_source_root"][
                "anchor_sha256_after"
            ],
            "nested_probe_parent_path": pre_roots["broker_source_root"][
                "nested_probe_parent_path"
            ],
        },
        {
            "root_kind": "proposal_root",
            "path": normalize_binding("worktree", str(proposal_root)),
            "owner_sid": grant["broker_principal_sid"],
            "parent_path": normalize_binding("worktree", str(proposal_root.parent)),
            "parent_owner_sid": grant["broker_principal_sid"],
            "anchor_path": pre_roots["proposal_root"]["anchor_path"],
            "anchor_sha256": grant["replacement_sha256"],
            "nested_probe_parent_path": pre_roots["proposal_root"][
                "nested_probe_parent_path"
            ],
        },
    ]
    base_request = {
        "protocol_version": WORKER_PROBE_REQUEST_PROTOCOL_VERSION,
        "schema_version": 1,
        "worker_principal_sid": grant["worker_principal_sid"],
        "sandbox_group_principal_sid": grant["sandbox_group_principal_sid"],
        "broker_principal_sid": grant["broker_principal_sid"],
        "protected_roots": root_requests,
        "base_head": grant["base_head"],
        "target_path": grant["target_path"],
        "expected_status_sha256": status_sha256,
    }
    readable_roots = [
        root,
        state_root,
        source_root,
        proposal_root,
        executable.parent,
        Path(sys.executable).resolve(strict=True).parent,
    ]
    probes: list[dict[str, Any]] = []
    for role, principal_sid, online in (
        (online_role, grant["worker_principal_sid"], True),
        (offline_role, grant["model_worker_principal_sid"], False),
    ):
        challenge = require_stable_id(
            f"{grant['grant_id']}-post-{role}", "post worker challenge id"
        )
        request = {
            **base_request,
            "challenge_id": challenge,
            "worker_principal_sid": principal_sid,
        }
        with _native_operation_probe_anchors(root_requests, challenge):
            probe = _run_fixed_sandbox_probe(
                executable=executable,
                controller_spec={},
                request=request,
                expected_sid=principal_sid,
                online=online,
                readable_roots=readable_roots,
                worker_root=worker_root / f"post-{grant['grant_id']}-{role}",
            )
        probes.append({
            "principal_role": role,
            "principal_sid": principal_sid,
            "probe": probe,
        })
    fresh_membership = {
        "protocol_version": WINDOWS_GROUP_MEMBERSHIP_PROTOCOL_VERSION,
        "schema_version": 1,
        "sandbox_group_sid": grant["sandbox_group_principal_sid"],
        "members": [
            {
                "principal_role": item["principal_role"],
                "principal_sid": item["principal_sid"],
                "group_sids": item["probe"]["worker_group_sids"],
            }
            for item in probes
        ],
        "observed_at": utc_now(),
    }
    normalized_membership = CaseStore._normalize_sandbox_membership_evidence(
        fresh_membership,
        app_server_sid=grant["worker_principal_sid"],
        model_sandbox_sid=grant["model_worker_principal_sid"],
        sandbox_group_sid=grant["sandbox_group_principal_sid"],
        online_role=online_role,
        offline_role=offline_role,
    )
    canonical_groups = {
        item["principal_role"]: item["group_sids"]
        for item in grant["group_membership_evidence"]["members"]
    }
    fresh_groups = {
        item["principal_role"]: item["group_sids"]
        for item in normalized_membership["members"]
    }
    if fresh_groups != canonical_groups:
        raise BrokerAuthorizationError("post-probe Windows group memberships drifted")
    membership_sha256 = canonical_json_sha256(normalized_membership)
    combined = {
        "denied_principal_sids": grant["denied_principal_sids"],
        "membership_evidence_sha256": membership_sha256,
        "principal_probes": probes,
    }
    raw_isolation = {
        "protocol_version": WINDOWS_ISOLATION_EVIDENCE_PROTOCOL_VERSION,
        "schema_version": 2,
        **combined,
        "combined_probe_sha256": canonical_json_sha256(combined),
    }
    protected_roots = {
        item["root_kind"]: (
            item["path"], item["anchor_path"], item["anchor_sha256"]
        )
        for item in root_requests
    }
    normalized_isolation = CaseStore._normalize_windows_isolation_evidence(
        raw_isolation,
        worktree=grant["worktree"],
        app_server_sid=grant["worker_principal_sid"],
        model_sandbox_sid=grant["model_worker_principal_sid"],
        sandbox_group_sid=grant["sandbox_group_principal_sid"],
        denied_principal_sids=grant["denied_principal_sids"],
        broker_sid=grant["broker_principal_sid"],
        base_head=grant["base_head"],
        protected_roots=protected_roots,
        membership_sha256=membership_sha256,
        membership_evidence=normalized_membership,
        expected_status_sha256=status_sha256,
        online_role=online_role,
        offline_role=offline_role,
    )
    dacl_evidence = inspect_protected_dacls(
        _protected_roots(grant),
        grant["denied_principal_sids"],
        grant["broker_principal_sid"],
        grant["group_membership_evidence_sha256"],
    )
    normalized_dacl = CaseStore._normalize_dacl_evidence(dacl_evidence, grant)
    body = {
        "protocol_version": POST_REPLACEMENT_EVIDENCE_PROTOCOL_VERSION,
        "schema_version": 1,
        "grant_id": grant["grant_id"],
        "run_id": require_stable_id(run_id, "broker run id"),
        "target_sha256": grant["replacement_sha256"],
        "status_sha256": status_sha256,
        "observed_status_paths": grant["allowed_paths"],
        "membership_evidence": normalized_membership,
        "membership_evidence_sha256": membership_sha256,
        "isolation_evidence": normalized_isolation,
        "isolation_evidence_sha256": canonical_json_sha256(normalized_isolation),
        "dacl_evidence": normalized_dacl,
        "dacl_evidence_sha256": canonical_json_sha256(normalized_dacl),
        "protected_acl_snapshot_sha256": grant[
            "protected_acl_snapshot_sha256"
        ],
        "observed_at": utc_now(),
    }
    return {
        **body,
        "post_replacement_evidence_sha256": canonical_json_sha256(body),
    }


def _protected_roots(grant: Mapping[str, Any]) -> dict[str, str]:
    return {
        "target_root": normalize_binding("worktree", grant["worktree"]),
        "state_root": normalize_binding("worktree", grant["state_root"]),
        "broker_source_root": normalize_binding("worktree", grant["broker_source_root"]),
        "proposal_root": normalize_binding("worktree", grant["proposal_root"]),
    }


def _verify_source_pins(grant: Mapping[str, Any]) -> None:
    managed_root = Path(__file__).resolve().parents[2]
    if normalize_binding("worktree", str(managed_root)) != grant["broker_source_root"]:
        raise BrokerAuthorizationError("running broker source root differs from the canonical grant")
    if grant.get("protocol_version") == PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION:
        observed = proposal_broker_source_pins(managed_root)
        expected = grant.get("proposal_broker_source_pins")
        expected_sha256 = grant.get("proposal_broker_source_pins_sha256")
        label = "proposal broker"
    else:
        observed = controller_source_pins(managed_root)
        expected = grant.get("controller_source_pins")
        expected_sha256 = grant.get("controller_source_pins_sha256")
        label = "controller"
    if observed != expected:
        raise BrokerAuthorizationError(
            f"running {label} sources differ from the pinned bundle manifest"
        )
    if canonical_json_sha256(observed) != expected_sha256:
        raise BrokerAuthorizationError(
            f"{label} source pin digest differs from the canonical grant"
        )


def _verify_original_proposal(grant: Mapping[str, Any]) -> None:
    proposal = Path(grant["proposal_artifact_path"])
    if (
        not proposal.is_file()
        or proposal.is_symlink()
        or file_sha256(proposal) != grant["replacement_sha256"]
        or regular_file_identity(proposal) != grant["proposal_file_identity"]
    ):
        raise BrokerAuthorizationError("original proposal identity or bytes changed after sealing")


def _sealed_path(
    state_root: Path,
    relative: str,
    expected_sha256: str,
    expected_identity: Mapping[str, Any],
) -> Path:
    relative = normalize_action_path(relative)
    path = state_root.joinpath(*PurePosixPath(relative).parts)
    if (
        not path_is_within(path, state_root)
        or not path.is_file()
        or path.is_symlink()
        or path_contains_link_or_reparse(path, stop=state_root)
        or file_sha256(path) != expected_sha256
    ):
        raise BrokerAuthorizationError("sealed action artifact differs from the canonical grant")
    if regular_file_identity(path, stop=state_root) != expected_identity:
        raise BrokerAuthorizationError("sealed action artifact identity differs from the canonical grant")
    return path


def _trusted_write_probe(
    grant: Mapping[str, Any], broker_name: str, broker_sid: str, state_store_path: Path
) -> dict[str, Any]:
    roots = _protected_roots(grant)
    proposal_mode = (
        grant.get("protocol_version") == PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION
    )
    if proposal_mode:
        source_pins = grant.get("proposal_broker_source_pins")
        if not isinstance(source_pins, Mapping):
            raise BrokerAuthorizationError(
                "proposal grant lacks sealed broker source pins"
            )
        broker_anchor = normalize_action_path(source_pins["manifest_path"])
        proposal_anchor = normalize_action_path(
            Path(grant["proposal_artifact_path"]).name
        )
    else:
        isolation_roots = {
            item["root_kind"]: item
            for item in grant["isolation_evidence"]["principal_probes"][0][
                "probe"
            ]["protected_roots"]
        }
        broker_anchor = isolation_roots["broker_source_root"]["anchor_path"]
        proposal_anchor = isolation_roots["proposal_root"]["anchor_path"]
    anchor_paths = {
        "target_root": grant["target_path"],
        "state_root": STORE_FILENAME,
        "broker_source_root": broker_anchor,
        "proposal_root": proposal_anchor,
    }
    root_records: list[dict[str, Any]] = []
    nonce_digest = hashlib.sha256(grant["authorization_nonce"].encode("utf-8")).hexdigest()[:20]
    target_root = Path(roots["target_root"])
    head_before = _git_head(target_root)
    status_before_raw = _run_git(
        target_root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
        "--ignore-submodules=none",
    )
    for kind in PROTECTED_ROOT_KINDS:
        root = Path(roots[kind]).resolve(strict=True)
        before = _anchor_digest(root, anchor_paths[kind])
        probe_relative = f".ccos-broker-{kind.replace('_root', '')}-probe-{nonce_digest}"
        probe = root / probe_relative
        if probe.exists():
            raise BrokerPreflightError(f"trusted {kind} probe path already exists")
        content = b"CCOS-TRUSTED-WRITE-PROBE\0" + grant["authorization_nonce"].encode("ascii") + kind.encode("ascii")
        descriptor = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        if file_sha256(probe) != hashlib.sha256(content).hexdigest():
            raise BrokerPreflightError(f"trusted {kind} write probe content verification failed")
        probe.unlink()
        after = _anchor_digest(root, anchor_paths[kind])
        root_records.append(
            {
                "root_kind": kind,
                "path": roots[kind],
                "anchor_path": anchor_paths[kind],
                "anchor_sha256_before": before,
                "anchor_sha256_after": after,
                "probe_relative_path": probe_relative,
                "probe_content_sha256": hashlib.sha256(content).hexdigest(),
                "probe_absent_after": not probe.exists(),
            }
        )
    status_after_raw = _run_git(
        target_root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
        "--ignore-submodules=none",
    )
    return {
        "protocol_version": TRUSTED_WRITE_PROBE_PROTOCOL_VERSION,
        "schema_version": 1,
        "broker_principal_sid": broker_sid,
        "broker_identity_name": broker_name,
        "protected_roots": root_records,
        "head_before": head_before,
        "head_after": _git_head(target_root),
        "status_sha256_before": hashlib.sha256(status_before_raw).hexdigest(),
        "status_sha256_after": hashlib.sha256(status_after_raw).hexdigest(),
        "observed_at": utc_now(),
    }


class BrokerJournal:
    def __init__(self, state_root: Path, case_id: str, grant_id: str) -> None:
        token = hashlib.sha256(f"{case_id}\0{grant_id}".encode("utf-8")).hexdigest()
        self.directory = state_root / BROKER_JOURNAL_DIRECTORY / case_id
        self.directory.mkdir(parents=True, exist_ok=True)
        if path_contains_link_or_reparse(self.directory, stop=state_root):
            raise BrokerPreflightError("broker journal directory traverses a link or reparse point")
        self.path = self.directory / f"{token}.jsonl"
        self.lock_path = self.directory / f"{token}.lock"
        self.case_id = case_id
        self.grant_id = grant_id

    def ensure_file(self) -> None:
        if self.path.exists():
            if self.path.is_symlink() or not self.path.is_file():
                raise BrokerPreflightError("broker journal must be a regular file")
            return
        descriptor = os.open(
            self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
        os.close(descriptor)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        if self.path.is_symlink() or not self.path.is_file():
            raise BrokerPreflightError("broker journal must be a regular file")
        records: list[dict[str, Any]] = []
        previous = "0" * 64
        for sequence, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BrokerPreflightError("broker journal contains malformed JSON") from exc
            if (
                not isinstance(record, dict)
                or record.get("protocol_version") != BROKER_JOURNAL_PROTOCOL_VERSION
                or record.get("sequence") != sequence
                or record.get("previous_event_sha256") != previous
                or record.get("case_id") != self.case_id
                or record.get("grant_id") != self.grant_id
            ):
                raise BrokerPreflightError("broker journal chain or identity is invalid")
            body = {name: value for name, value in record.items() if name != "event_sha256"}
            if record.get("event_sha256") != canonical_json_sha256(body):
                raise BrokerPreflightError("broker journal digest is invalid")
            previous = record["event_sha256"]
            records.append(record)
        return records

    def append(self, event: str, run_id: str, **details: Any) -> dict[str, Any]:
        records = self.records()
        body = {
            "protocol_version": BROKER_JOURNAL_PROTOCOL_VERSION,
            "sequence": len(records) + 1,
            "previous_event_sha256": records[-1]["event_sha256"] if records else "0" * 64,
            "case_id": self.case_id,
            "grant_id": self.grant_id,
            "event": event,
            "run_id": run_id,
            "recorded_at": utc_now(),
            **details,
        }
        record = {**body, "event_sha256": canonical_json_sha256(body)}
        descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, "ab", closefd=False) as stream:
                stream.write(canonical_json_bytes(record) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        return record


def _dacl_roots(evidence: Mapping[str, Any]) -> dict[str, str]:
    raw_rules = evidence.get("rules")
    if not isinstance(raw_rules, list):
        raise BrokerPreflightError("lockdown DACL evidence has no rules")
    roots: dict[str, str] = {}
    for rule in raw_rules:
        if not isinstance(rule, Mapping):
            raise BrokerPreflightError("lockdown DACL rule is invalid")
        kind = str(rule.get("root_kind", ""))
        path = normalize_binding("worktree", str(rule.get("path", "")))
        if kind not in PROTECTED_ROOT_KINDS:
            raise BrokerPreflightError("lockdown DACL rule names an unknown root")
        if kind in roots and roots[kind] != path:
            raise BrokerPreflightError("lockdown DACL evidence splits one root")
        roots[kind] = path
    if set(roots) != set(PROTECTED_ROOT_KINDS):
        raise BrokerPreflightError("lockdown DACL evidence omits a protected root")
    return roots


def _restore_acl_snapshot_after_lockdown(
    journal: BrokerJournal,
    *,
    run_id: str,
    snapshot: Any,
    snapshot_sha256: str,
    lockdown_dacl_evidence: Mapping[str, Any] | None,
    restore_reason: str,
) -> dict[str, Any]:
    normalized_snapshot = _normalize_acl_snapshot(snapshot)
    expected_snapshot_sha256 = require_snapshot_hash(snapshot_sha256)
    if canonical_json_sha256(normalized_snapshot) != expected_snapshot_sha256:
        raise BrokerAuthorizationError("ACL recovery snapshot digest is invalid")
    records = journal.records()
    matching_snapshots = [
        record
        for record in records
        if record.get("event") == "ACL_SNAPSHOT"
        and record.get("protected_acl_snapshot_sha256") == expected_snapshot_sha256
        and record.get("protected_acl_snapshot") == normalized_snapshot
    ]
    if len(matching_snapshots) != 1:
        raise BrokerAuthorizationError(
            "ACL recovery snapshot is absent, duplicated, or differs from the journal"
        )
    intents = [
        record
        for record in records
        if record.get("event") == "ACL_LOCKDOWN_INTENT"
        and record.get("protected_acl_snapshot_sha256") == expected_snapshot_sha256
        and isinstance(record.get("lockdown_intent"), Mapping)
        and record.get("lockdown_intent_sha256")
        == canonical_json_sha256(record.get("lockdown_intent"))
    ]
    if len(intents) != 1:
        raise BrokerAuthorizationError("ACL recovery lacks one exact lockdown intent")
    intent = intents[0]["lockdown_intent"]
    if set(intent) != {"roots", "denied_principal_sids", "broker_principal_sid"}:
        raise BrokerAuthorizationError("ACL recovery lockdown intent schema is invalid")
    raw_roots = intent.get("roots")
    if not isinstance(raw_roots, Mapping) or set(raw_roots) != set(PROTECTED_ROOT_KINDS):
        raise BrokerAuthorizationError("ACL recovery lockdown roots are incomplete")
    intent_roots = {
        kind: normalize_binding("worktree", str(raw_roots[kind]))
        for kind in PROTECTED_ROOT_KINDS
    }
    denied_intent = intent.get("denied_principal_sids")
    if (
        not isinstance(denied_intent, list)
        or len(denied_intent) != 3
        or len(set(denied_intent)) != 3
    ):
        raise BrokerAuthorizationError("ACL recovery denied principal set is invalid")
    denied_intent = [
        require_windows_sid(item, "ACL recovery denied principal SID")
        for item in denied_intent
    ]
    intent_broker_sid = require_windows_sid(
        intent.get("broker_principal_sid"), "ACL recovery broker SID"
    )
    if intent_broker_sid in denied_intent:
        raise BrokerAuthorizationError("ACL recovery broker is a denied principal")
    expected_snapshot_paths = {
        path
        for root_path in intent_roots.values()
        for path in (
            root_path,
            normalize_binding("worktree", str(Path(root_path).parent)),
        )
    }
    if {item["path"] for item in normalized_snapshot} != expected_snapshot_paths:
        raise BrokerAuthorizationError(
            "ACL recovery snapshot paths differ from the exact intent roots and parents"
        )
    if any(
        record.get("event") == "ACL_RESTORED"
        and record.get("protected_acl_snapshot_sha256") == expected_snapshot_sha256
        for record in records
    ):
        _verify_protected_acl_restore(normalized_snapshot)
        return {
            "restored": True,
            "already_restored": True,
            "protected_acl_snapshot_sha256": expected_snapshot_sha256,
        }
    verified_lockdowns = [
        record
        for record in records
        if record.get("event") == "ACL_LOCKDOWN_VERIFIED"
        and record.get("protected_acl_snapshot_sha256") == expected_snapshot_sha256
    ]
    if len(verified_lockdowns) > 1:
        raise BrokerAuthorizationError("ACL recovery has duplicate lockdown verification")
    try:
        _verify_protected_acl_restore(normalized_snapshot)
        already_original = True
    except (BrokerError, CaseStateError, OSError):
        already_original = False
    if not already_original and verified_lockdowns:
        if not isinstance(lockdown_dacl_evidence, Mapping):
            lockdown_dacl_evidence = verified_lockdowns[0].get(
                "preissue_dacl_evidence"
            )
        if (
            not isinstance(lockdown_dacl_evidence, Mapping)
            or canonical_json_sha256(lockdown_dacl_evidence)
            != verified_lockdowns[0].get("preissue_dacl_evidence_sha256")
        ):
            raise BrokerAuthorizationError("verified lockdown DACL evidence is invalid")
        roots = _dacl_roots(lockdown_dacl_evidence)
        denied = lockdown_dacl_evidence.get("denied_principal_sids")
        broker_sid = require_windows_sid(
            lockdown_dacl_evidence.get("broker_principal_sid"), "lockdown broker SID"
        )
        dacl_protocol = lockdown_dacl_evidence.get("protocol_version")
        if dacl_protocol == WINDOWS_DACL_EVIDENCE_PROTOCOL_VERSION:
            require_snapshot_hash(
                str(lockdown_dacl_evidence.get("membership_evidence_sha256", ""))
            )
        elif dacl_protocol != PROPOSAL_DACL_EVIDENCE_PROTOCOL_VERSION:
            raise BrokerAuthorizationError(
                "verified lockdown DACL protocol is unsupported"
            )
        if (
            roots != intent_roots
            or denied != denied_intent
            or broker_sid != intent_broker_sid
        ):
            raise BrokerAuthorizationError(
                "verified lockdown evidence differs from the exact ACL intent"
            )
        expected_lockdown: dict[str, tuple[str, str]] = {}
        for rule in lockdown_dacl_evidence.get("rules", []):
            if not isinstance(rule, Mapping):
                raise BrokerAuthorizationError("lockdown DACL rule is invalid")
            for path_name, digest_name, owner_name in (
                ("path", "root_sddl_sha256", "owner_sid"),
                ("parent_path", "parent_sddl_sha256", "parent_owner_sid"),
            ):
                path = normalize_binding("worktree", str(rule.get(path_name, "")))
                descriptor = (
                    require_windows_sid(rule.get(owner_name), "lockdown ACL owner SID"),
                    require_snapshot_hash(str(rule.get(digest_name, ""))),
                )
                if path in expected_lockdown and expected_lockdown[path] != descriptor:
                    raise BrokerAuthorizationError(
                        "lockdown DACL evidence splits one descriptor"
                    )
                expected_lockdown[path] = descriptor
        if set(expected_lockdown) != expected_snapshot_paths:
            raise BrokerAuthorizationError(
                "lockdown DACL descriptor set differs from the exact snapshot"
            )
        current = {
            item["path"]: item for item in _snapshot_protected_acls(intent_roots)
        }
        original = {item["path"]: item for item in normalized_snapshot}
        for path in expected_snapshot_paths:
            observed_entry = current[path]
            original_entry = original[path]
            is_original = (
                observed_entry["owner_sid"] == original_entry["owner_sid"]
                and observed_entry["sddl"] == original_entry["sddl"]
                and observed_entry["sddl_sha256"] == original_entry["sddl_sha256"]
            )
            lockdown_owner, lockdown_sddl_sha256 = expected_lockdown[path]
            is_lockdown = (
                observed_entry["owner_sid"] == lockdown_owner
                and observed_entry["sddl_sha256"] == lockdown_sddl_sha256
            )
            if not is_original and not is_lockdown:
                raise BrokerAuthorizationError(
                    "current ACL path is neither signed original nor intended lockdown"
                )
    if not already_original:
        _restore_protected_acls(normalized_snapshot)
        _verify_protected_acl_restore(normalized_snapshot)
    event = journal.append(
        "ACL_RESTORED",
        run_id,
        protected_acl_snapshot_sha256=expected_snapshot_sha256,
        restore_reason=require_stable_id(restore_reason, "ACL restore reason"),
        already_original=already_original,
    )
    return {
        "restored": True,
        "already_restored": already_original,
        "protected_acl_snapshot_sha256": expected_snapshot_sha256,
        "journal_event_sha256": event["event_sha256"],
    }


def restore_preissue_acl_lockdown(
    *,
    state_root: Path,
    case_id: str,
    grant_id: str,
    protected_acl_snapshot: Any,
    protected_acl_snapshot_sha256: str,
    preissue_dacl_evidence: Mapping[str, Any] | None,
    restore_reason: str,
) -> dict[str, Any]:
    """Trusted supervisor recovery for a lockdown that outlived grant issuance."""
    case_id = canonical_case_id(case_id)
    grant_id = require_stable_id(grant_id, "grant id")
    journal = BrokerJournal(Path(state_root), case_id, grant_id)
    with FileLock(journal.lock_path, timeout=30.0):
        return _restore_acl_snapshot_after_lockdown(
            journal,
            run_id=f"restore-{secrets.token_hex(16)}",
            snapshot=protected_acl_snapshot,
            snapshot_sha256=protected_acl_snapshot_sha256,
            lockdown_dacl_evidence=preissue_dacl_evidence,
            restore_reason=restore_reason,
        )


def recover_pending_preissue_acl_lockdowns(
    *,
    state_root: Path,
    case_id: str,
    grant_id: str,
    expected_roots: Mapping[str, str],
    expected_denied_principal_sids: list[str],
    expected_broker_principal_sid: str,
) -> list[dict[str, Any]]:
    """Restore exact journal-bound ACL snapshots, including partial ACL-write crashes."""
    state_root = Path(state_root).resolve(strict=True)
    case_id = canonical_case_id(case_id)
    grant_id = require_stable_id(grant_id, "grant id")
    normalized_roots = {
        kind: normalize_binding("worktree", str(expected_roots.get(kind, "")))
        for kind in PROTECTED_ROOT_KINDS
    }
    if set(expected_roots) != set(PROTECTED_ROOT_KINDS):
        raise BrokerAuthorizationError("pending ACL recovery expected roots are incomplete")
    expected_denied = [
        require_windows_sid(item, "pending ACL recovery denied principal SID")
        for item in expected_denied_principal_sids
    ]
    expected_broker = require_windows_sid(
        expected_broker_principal_sid, "pending ACL recovery broker SID"
    )
    journal = BrokerJournal(state_root, case_id, grant_id)
    if not journal.path.exists():
        return []
    with FileLock(journal.lock_path, timeout=30.0):
        records = journal.records()
        snapshots = [record for record in records if record.get("event") == "ACL_SNAPSHOT"]
        if len(snapshots) != 1:
            raise BrokerAuthorizationError("journal lacks one exact ACL snapshot")
        intents = [record for record in records if record.get("event") == "ACL_LOCKDOWN_INTENT"]
        if len(intents) != 1:
            raise BrokerAuthorizationError("journal lacks one exact ACL lockdown intent")
        expected_intent = {
            "roots": normalized_roots,
            "denied_principal_sids": expected_denied,
            "broker_principal_sid": expected_broker,
        }
        if (
            intents[0].get("lockdown_intent") != expected_intent
            or intents[0].get("lockdown_intent_sha256")
            != canonical_json_sha256(expected_intent)
        ):
            raise BrokerAuthorizationError(
                "pending ACL journal intent differs from the sealed supervisor spec"
            )
        snapshot_record = snapshots[0]
        snapshot_sha256 = require_snapshot_hash(
            str(snapshot_record.get("protected_acl_snapshot_sha256", ""))
        )
        restored_records = [
            record
            for record in records
            if record.get("event") == "ACL_RESTORED"
            and record.get("protected_acl_snapshot_sha256") == snapshot_sha256
        ]
        if len(restored_records) > 1:
            raise BrokerAuthorizationError("journal duplicates ACL restoration")
        if restored_records:
            _verify_protected_acl_restore(snapshot_record.get("protected_acl_snapshot"))
            return [{
                "restored": True,
                "already_restored": True,
                "protected_acl_snapshot_sha256": snapshot_sha256,
                "journal_event_sha256": restored_records[0]["event_sha256"],
                "grant_id": grant_id,
                "snapshot_event_sha256": snapshot_record["event_sha256"],
                "lockdown_intent_event_sha256": intents[0]["event_sha256"],
                "acl_restored_event_sha256": restored_records[0]["event_sha256"],
            }]
        verified = [
            record for record in records if record.get("event") == "ACL_LOCKDOWN_VERIFIED"
        ]
        if len(verified) > 1:
            raise BrokerAuthorizationError("journal duplicates lockdown verification")
        dacl_evidence = (
            verified[0].get("preissue_dacl_evidence") if verified else None
        )
        restored = _restore_acl_snapshot_after_lockdown(
            journal,
            run_id=f"startup-restore-{secrets.token_hex(16)}",
            snapshot=snapshot_record.get("protected_acl_snapshot"),
            snapshot_sha256=snapshot_sha256,
            lockdown_dacl_evidence=dacl_evidence,
            restore_reason="startup_recovery",
        )
        restored.update(
            grant_id=grant_id,
            snapshot_event_sha256=snapshot_record["event_sha256"],
            lockdown_intent_event_sha256=intents[0]["event_sha256"],
            acl_restored_event_sha256=restored["journal_event_sha256"],
        )
        return [restored]


def recover_completed_action_grant_cleanup(
    *, state_root: Path, case_id: str, grant_id: str
) -> dict[str, Any]:
    """Close the one crash gap after canonical completion but before journal cleanup."""
    case_id = canonical_case_id(case_id)
    grant_id = require_stable_id(grant_id, "grant id")
    store = CaseStore(Path(state_root))
    journal = BrokerJournal(store.state_root, case_id, grant_id)
    with FileLock(journal.lock_path, timeout=30.0):
        grant = _get_grant(store.get_case(case_id), grant_id)
        if grant.get("status") != "COMPLETED" or not isinstance(grant.get("result"), Mapping):
            raise BrokerAuthorizationError("canonical action grant is not completed")
        _broker_name, broker_sid = windows_identity()
        if broker_sid != grant["broker_principal_sid"]:
            raise BrokerAuthorizationError(
                "completed cleanup is not running as the sealed broker principal"
            )
        _verify_source_pins(grant)
        root, target = _repository_target(grant)
        if (
            file_sha256(target) != grant["replacement_sha256"]
            or _git_status_paths(root) != grant["allowed_paths"]
        ):
            raise BrokerAuthorizationError(
                "completed cleanup target or Git status differs from the grant"
            )
        result = grant["result"]
        if result.get("result_sha256") != canonical_json_sha256(
            {name: value for name, value in result.items() if name != "result_sha256"}
        ):
            raise BrokerAuthorizationError("canonical completion result digest is invalid")
        post_sha256 = require_snapshot_hash(
            str(result.get("post_replacement_evidence_sha256", ""))
        )
        records = journal.records()
        post_records = [
            record
            for record in records
            if record.get("event") == "POST_ISOLATION_VERIFIED"
            and record.get("post_replacement_evidence_sha256") == post_sha256
        ]
        if len(post_records) != 1:
            raise BrokerAuthorizationError(
                "completed recovery lacks one exact post-isolation journal record"
            )
        post_evidence = post_records[0].get("post_replacement_evidence")
        if not isinstance(post_evidence, Mapping):
            raise BrokerAuthorizationError("post-isolation journal evidence is missing")
        post_body = {
            name: value
            for name, value in post_evidence.items()
            if name != "post_replacement_evidence_sha256"
        }
        invalid_post = (
            post_evidence.get("post_replacement_evidence_sha256") != post_sha256
            or canonical_json_sha256(post_body) != post_sha256
            or canonical_json_sha256(post_evidence.get("dacl_evidence"))
            != post_evidence.get("dacl_evidence_sha256")
        )
        if grant.get("protocol_version") == PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION:
            expected_post_fields = {
                "protocol_version", "schema_version", "evidence_mode", "grant_id",
                "run_id", "target_sha256", "status_sha256",
                "observed_status_paths", "dacl_evidence", "dacl_evidence_sha256",
                "protected_acl_snapshot_sha256", "observed_at",
                "post_replacement_evidence_sha256",
            }
            invalid_post = (
                invalid_post
                or set(post_evidence) != expected_post_fields
                or post_evidence.get("schema_version") != 2
                or post_evidence.get("evidence_mode")
                != PROPOSAL_DACL_EVIDENCE_MODE
            )
        else:
            invalid_post = (
                invalid_post
                or canonical_json_sha256(post_evidence.get("isolation_evidence"))
                != post_evidence.get("isolation_evidence_sha256")
                or canonical_json_sha256(post_evidence.get("membership_evidence"))
                != post_evidence.get("membership_evidence_sha256")
            )
        if invalid_post:
            raise BrokerAuthorizationError("post-isolation journal evidence is invalid")
        completed_records = [
            record for record in records if record.get("event") == "COMPLETED"
        ]
        if len(completed_records) > 1:
            raise BrokerAuthorizationError("broker journal has duplicate completion records")
        if not completed_records:
            if any(record.get("event") == "ACL_RESTORED" for record in records):
                raise BrokerAuthorizationError(
                    "broker journal restored ACLs before reconstructing completion"
                )
            claim = grant.get("claim")
            if not isinstance(claim, Mapping):
                raise BrokerAuthorizationError("completed grant lacks its exact claim")
            journal.append(
                "COMPLETED",
                f"completed-recovery-{secrets.token_hex(16)}",
                recovered_after_canonical_commit=True,
                **_journal_action_details(
                    grant,
                    grant["broker_principal_sid"],
                    target_sha256_before=grant["baseline_sha256"],
                    target_sha256_after=grant["replacement_sha256"],
                    changed_path=grant["target_path"],
                    claim_sha256=claim["claim_sha256"],
                    result_sha256=result["result_sha256"],
                ),
            )
        else:
            terminal = completed_records[0]
            if terminal.get("result_sha256") != result["result_sha256"]:
                raise BrokerAuthorizationError(
                    "broker completion journal differs from the canonical result"
                )
        restore = _restore_acl_snapshot_after_lockdown(
            journal,
            run_id=f"completed-cleanup-{secrets.token_hex(16)}",
            snapshot=grant["protected_acl_snapshot"],
            snapshot_sha256=grant["protected_acl_snapshot_sha256"],
            lockdown_dacl_evidence=post_evidence["dacl_evidence"],
            restore_reason="completed_recovery",
        )
        return {
            "status": "recovered_completed",
            "grant_id": grant_id,
            "result_sha256": result["result_sha256"],
            "post_replacement_evidence_sha256": post_sha256,
            "acl_restore": restore,
        }


def recover_orphaned_action_grant(
    *, state_root: Path, case_id: str, grant_id: str
) -> dict[str, Any]:
    """Trusted startup recovery for ISSUED, CLAIMED, FAILED, or COMPLETED grants."""
    case_id = canonical_case_id(case_id)
    grant_id = require_stable_id(grant_id, "grant id")
    store = CaseStore(Path(state_root))
    journal = BrokerJournal(store.state_root, case_id, grant_id)
    with FileLock(journal.lock_path, timeout=30.0):
        grant = _get_grant(store.get_case(case_id), grant_id)
        _broker_name, broker_sid = windows_identity()
        if broker_sid != grant["broker_principal_sid"]:
            raise BrokerAuthorizationError(
                "orphan recovery is not running as the sealed broker principal"
            )
        status = grant["status"]
        if status == "COMPLETED":
            # Release the lock before entering the public completed recovery lock.
            pass
        elif status == "FAILED":
            restore = _restore_acl_snapshot_after_lockdown(
                journal,
                run_id=f"failed-cleanup-{secrets.token_hex(16)}",
                snapshot=grant["protected_acl_snapshot"],
                snapshot_sha256=grant["protected_acl_snapshot_sha256"],
                lockdown_dacl_evidence=grant["preissue_dacl_evidence"],
                restore_reason="failed_startup",
            )
            return {"status": "failed_stable", "acl_restore": restore}
        elif status in {"ISSUED", "CLAIMED"}:
            return _rollback_and_fail(
                store,
                case_id,
                grant,
                broker_sid,
                journal,
                f"orphan-recovery-{secrets.token_hex(16)}",
                stage="supervisor_context",
                code="SUPERVISOR_CONTEXT_LOST",
            )
        else:
            raise BrokerAuthorizationError("orphan recovery found an unknown grant status")
    return recover_completed_action_grant_cleanup(
        state_root=state_root, case_id=case_id, grant_id=grant_id
    )


def _journal_action_details(
    grant: Mapping[str, Any],
    broker_sid: str,
    *,
    target_sha256_before: str,
    target_sha256_after: str,
    changed_path: str | None,
    claim_sha256: str | None = None,
    result_sha256: str | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "broker_principal_sid": broker_sid,
        "broker_process_id": os.getpid(),
        "target_path": grant["target_path"],
        "target_sha256_before": require_snapshot_hash(target_sha256_before),
        "target_sha256_after": require_snapshot_hash(target_sha256_after),
        "changed_path": changed_path,
        "grant_sha256": require_snapshot_hash(grant["grant_sha256"]),
        "claim_sha256": claim_sha256,
        "result_sha256": result_sha256,
    }
    if grant.get("protocol_version") == PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION:
        details.update(
            {
                "authority_id": require_stable_id(
                    grant["authority_id"], "authority id"
                ),
                "authority_sha256": require_snapshot_hash(
                    grant["authority_sha256"]
                ),
                "proposal_artifact_sha256": require_snapshot_hash(
                    grant["proposal_artifact_sha256"]
                ),
            }
        )
    else:
        details.update(
            {
                "live_controller_evidence_sha256": require_snapshot_hash(
                    grant["live_controller_evidence_sha256"]
                ),
                "controller_receipt_sha256": require_snapshot_hash(
                    grant["controller_receipt_sha256"]
                ),
            }
        )
    return details


def _get_grant(case: Mapping[str, Any], grant_id: str) -> dict[str, Any]:
    runtime = case.get("runtime")
    grants = runtime.get("action_grants") if isinstance(runtime, Mapping) else None
    grant = grants.get(grant_id) if isinstance(grants, Mapping) else None
    if not isinstance(grant, dict):
        raise BrokerAuthorizationError("canonical action grant does not exist")
    return copy.deepcopy(grant)


def _verify_static_grant(
    store: CaseStore,
    case: Mapping[str, Any],
    grant: Mapping[str, Any],
    receipt: Mapping[str, Any] | None,
    *,
    broker_identity: tuple[str, str] | None = None,
) -> tuple[str, str, str]:
    broker_name, broker_sid = broker_identity or windows_identity()
    if (broker_sid != grant["broker_principal_sid"]
            or broker_sid in grant["denied_principal_sids"]):
        raise BrokerAuthorizationError("broker did not run as the exact distinct trusted principal")
    if normalize_binding("worktree", str(store.state_root)) != grant["state_root"]:
        raise BrokerAuthorizationError("broker state root differs from the exact grant")
    _verify_source_pins(grant)
    _verify_original_proposal(grant)
    if grant.get("protocol_version") == PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION:
        authority = grant.get("authority")
        authority_sha256 = require_snapshot_hash(
            str(grant.get("authority_sha256", ""))
        )
        if (
            not isinstance(authority, Mapping)
            or authority.get("case_id") != case["case_id"]
            or authority.get("grant_id") != grant.get("grant_id")
            or authority.get("expected_case_revision")
            != grant.get("issued_revision", 0) - 1
            or canonical_json_sha256(authority) != authority_sha256
        ):
            raise BrokerAuthorizationError(
                "canonical proposal authority is missing, stale, or invalid"
            )
        authorization_sha256 = authority_sha256
    else:
        if not isinstance(receipt, Mapping):
            raise BrokerAuthorizationError(
                "legacy action grant requires its controller receipt"
            )
        _, authorization_sha256 = verify_controller_receipt(
            receipt, grant, case["case_id"], _controller_key()
        )
    roots = _protected_roots(grant)
    if grant.get("protocol_version") == PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION:
        observed_dacl = inspect_proposal_dacls(
            roots,
            grant["denied_principal_sids"],
            broker_sid,
        )
    else:
        observed_dacl = inspect_protected_dacls(
            roots,
            grant["denied_principal_sids"],
            broker_sid,
            grant["group_membership_evidence_sha256"],
        )
    normalized_dacl = CaseStore._normalize_dacl_evidence(observed_dacl, grant)
    expected_dacl = grant.get("preissue_dacl_evidence")
    if (
        not isinstance(expected_dacl, Mapping)
        or normalized_dacl.get("rules") != expected_dacl.get("rules")
        or normalized_dacl.get("denied_principal_sids")
        != expected_dacl.get("denied_principal_sids")
        or normalized_dacl.get("broker_principal_sid")
        != expected_dacl.get("broker_principal_sid")
    ):
        raise BrokerAuthorizationError(
            "current protected ACLs differ from the preissue lockdown"
        )
    state_root = Path(grant["state_root"])
    _sealed_path(
        state_root, grant["sealed_artifact_path"], grant["sealed_artifact_sha256"],
        grant["sealed_artifact_identity"],
    )
    _sealed_path(
        state_root, grant["sealed_baseline_path"], grant["sealed_baseline_sha256"],
        grant["sealed_baseline_identity"],
    )
    return broker_name, broker_sid, authorization_sha256


def _verify_lockdown_journal_binding(
    grant: Mapping[str, Any], journal: BrokerJournal
) -> None:
    snapshot = _normalize_acl_snapshot(grant.get("protected_acl_snapshot"))
    snapshot_sha256 = require_snapshot_hash(
        str(grant.get("protected_acl_snapshot_sha256", ""))
    )
    if canonical_json_sha256(snapshot) != snapshot_sha256:
        raise BrokerAuthorizationError("grant ACL snapshot digest is invalid")
    preissue_dacl_sha256 = require_snapshot_hash(
        str(grant.get("preissue_dacl_evidence_sha256", ""))
    )
    if canonical_json_sha256(grant.get("preissue_dacl_evidence")) != preissue_dacl_sha256:
        raise BrokerAuthorizationError("grant preissue DACL evidence digest is invalid")
    records = journal.records()
    snapshots = [
        record
        for record in records
        if record.get("event") == "ACL_SNAPSHOT"
        and record.get("protected_acl_snapshot_sha256") == snapshot_sha256
        and record.get("protected_acl_snapshot") == snapshot
    ]
    intents = [
        record
        for record in records
        if record.get("event") == "ACL_LOCKDOWN_INTENT"
        and record.get("protected_acl_snapshot_sha256") == snapshot_sha256
        and isinstance(record.get("lockdown_intent"), Mapping)
        and record.get("lockdown_intent_sha256")
        == canonical_json_sha256(record.get("lockdown_intent"))
    ]
    lockdowns = [
        record
        for record in records
        if record.get("event") == "ACL_LOCKDOWN_VERIFIED"
        and record.get("protected_acl_snapshot_sha256") == snapshot_sha256
        and record.get("preissue_dacl_evidence")
        == grant.get("preissue_dacl_evidence")
        and record.get("preissue_dacl_evidence_sha256") == preissue_dacl_sha256
    ]
    if len(snapshots) != 1 or len(intents) != 1 or len(lockdowns) != 1:
        raise BrokerAuthorizationError(
            "protected ACL snapshot, intent, or lockdown proof is missing from the journal"
        )
    if any(record.get("event") == "ACL_RESTORED" for record in records):
        raise BrokerAuthorizationError(
            "protected ACL lockdown was restored before the authorized action"
        )


def _repository_target(grant: Mapping[str, Any]) -> tuple[Path, Path]:
    root = Path(grant["worktree"]).resolve(strict=True)
    if _git_repository_root(root) != root or path_contains_link_or_reparse(root):
        raise BrokerPreflightError("action worktree is not an exact direct Git root")
    if _git_origin(root) != grant["repository"] or _git_branch(root) != grant["branch"]:
        raise BrokerAuthorizationError("repository origin or branch differs from the grant")
    if _git_head(root) != grant["base_head"]:
        raise BrokerAuthorizationError("repository HEAD differs from the grant")
    target = root.joinpath(*PurePosixPath(grant["target_path"]).parts)
    if (
        not target.is_file()
        or not path_is_within(target, root)
        or path_contains_link_or_reparse(target, stop=root)
        or _git_tracked_mode(root, grant["target_path"]) != grant["target_mode"]
    ):
        raise BrokerAuthorizationError("action target differs from the exact regular tracked file")
    observed_identity = regular_file_identity(target, stop=root)
    if file_sha256(target) == grant["baseline_sha256"] and observed_identity != grant["target_file_identity"]:
        raise BrokerAuthorizationError("baseline target file identity changed after grant issuance")
    return root, target


def _atomic_replace(
    source: Path, target: Path, temp_relative: str, *, fallback_mode: int = 0o644
) -> str:
    source_identity = regular_file_identity(source)
    raw = source.read_bytes()
    expected = file_sha256(source)
    if regular_file_identity(source) != source_identity:
        raise BrokerAuthorizationError("sealed source identity changed while bytes were held")
    temp_path = target.parent / normalize_action_path(temp_relative)
    if temp_path.exists():
        raise BrokerPreflightError("broker replacement temp path already exists")
    mode = target.stat().st_mode if target.exists() and target.is_file() else fallback_mode
    descriptor = os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode & 0o777)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    os.chmod(temp_path, mode & 0o777)
    if file_sha256(temp_path) != expected:
        raise BrokerPreflightError("broker temp content digest verification failed")
    regular_file_identity(temp_path, stop=target.parent)
    os.replace(temp_path, target)
    if file_sha256(target) != expected:
        raise BrokerPreflightError("atomic replacement digest verification failed")
    regular_file_identity(target, stop=target.parent)
    return expected


def _cleanup_journal_temps(root: Path, journal_records: list[dict[str, Any]]) -> None:
    for record in journal_records:
        relative = record.get("temp_relative_path")
        if record.get("event") not in {"WRITE_STARTED", "ROLLBACK_STARTED"} or not isinstance(relative, str):
            continue
        normalized = normalize_action_path(relative)
        if not normalized.startswith(".ccos-action-") or not normalized.endswith(".tmp"):
            raise BrokerPreflightError("journal contains an unexpected temp path")
        path = root / normalized
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise BrokerPreflightError("journal temp path is not a regular file")
            path.unlink()


def _record_failure(
    store: CaseStore,
    case_id: str,
    grant: Mapping[str, Any],
    broker_sid: str,
    journal: BrokerJournal,
    run_id: str,
    *,
    stage: str,
    code: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    case = store.get_case(case_id)
    canonical_grant = _get_grant(case, grant["grant_id"])
    target = Path(canonical_grant["worktree"]).joinpath(
        *PurePosixPath(canonical_grant["target_path"]).parts
    )
    target_after = (
        file_sha256(target)
        if target.is_file() and not target.is_symlink()
        else canonical_grant["baseline_sha256"]
    )
    raw_before = evidence.get("observed_before")
    target_before = (
        raw_before
        if isinstance(raw_before, str) and len(raw_before) == 64
        else target_after
    )
    proposal_protocol = (
        canonical_grant.get("protocol_version")
        == PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION
    )
    failure_payload: dict[str, Any] = {
        "protocol_version": (
            PROPOSAL_ACTION_RESULT_PROTOCOL_VERSION
            if proposal_protocol
            else ACTION_GRANT_RESULT_PROTOCOL_VERSION
        ),
        "schema_version": 2 if proposal_protocol else 1,
        "grant_id": grant["grant_id"],
        "broker_principal_sid": broker_sid,
        "failure_stage": require_stable_id(stage, "failure stage"),
        "failure_code": require_stable_id(code, "failure code"),
        "failure_evidence_sha256": canonical_json_sha256(evidence),
        "observed_at": utc_now(),
    }
    if proposal_protocol:
        failure_payload["authority_sha256"] = canonical_grant[
            "authority_sha256"
        ]
    result = store.fail_action_grant(
        case_id,
        failure=failure_payload,
        request_id=f"broker-fail-{grant['grant_id']}-{run_id}",
        expected_revision=case["revision"],
    )
    claim_record = canonical_grant.get("claim")
    claim_sha256 = (
        claim_record.get("claim_sha256") if isinstance(claim_record, Mapping) else None
    )
    journal.append(
        "FAILED",
        run_id,
        failure_stage=stage,
        failure_code=code,
        failure_result_sha256=result["result_sha256"],
        **_journal_action_details(
            canonical_grant,
            broker_sid,
            target_sha256_before=target_before,
            target_sha256_after=target_after,
            changed_path=(
                canonical_grant["target_path"] if target_before != target_after else None
            ),
            claim_sha256=claim_sha256,
            result_sha256=result["result_sha256"],
        ),
    )
    if (
        "protected_acl_snapshot" in canonical_grant
        and "preissue_dacl_evidence" in canonical_grant
    ):
        _restore_acl_snapshot_after_lockdown(
            journal,
            run_id=run_id,
            snapshot=canonical_grant["protected_acl_snapshot"],
            snapshot_sha256=canonical_grant["protected_acl_snapshot_sha256"],
            lockdown_dacl_evidence=canonical_grant["preissue_dacl_evidence"],
            restore_reason="failed",
        )
    return result


def _rollback_and_fail(
    store: CaseStore,
    case_id: str,
    grant: Mapping[str, Any],
    broker_sid: str,
    journal: BrokerJournal,
    run_id: str,
    *,
    stage: str,
    code: str,
) -> dict[str, Any]:
    root = Path(grant["worktree"]).resolve(strict=True)
    target = root.joinpath(*PurePosixPath(grant["target_path"]).parts)
    observed_before: str | None = None
    restored = False
    status_paths: list[str] | None = None
    rollback_error: str | None = None
    temp_relative = f".ccos-action-{hashlib.sha256((grant['grant_id'] + run_id + 'rollback').encode()).hexdigest()[:24]}.tmp"
    try:
        if target.is_file() and not target.is_symlink():
            observed_before = file_sha256(target)
        baseline = _sealed_path(
            Path(grant["state_root"]), grant["sealed_baseline_path"], grant["baseline_sha256"],
            grant["sealed_baseline_identity"],
        )
        journal.append(
            "ROLLBACK_STARTED", run_id, temp_relative_path=temp_relative,
            observed_target_sha256=observed_before,
        )
        if observed_before != grant["baseline_sha256"]:
            fallback_mode = 0o755 if grant["target_mode"] == "100755" else 0o644
            _atomic_replace(baseline, target, temp_relative, fallback_mode=fallback_mode)
        _cleanup_journal_temps(root, journal.records())
        restored = target.is_file() and file_sha256(target) == grant["baseline_sha256"]
        status_paths = _git_status_paths(root)
    except (OSError, CaseStateError, BrokerError) as exc:
        rollback_error = type(exc).__name__
        try:
            status_paths = _git_status_paths(root)
        except (OSError, CaseStateError):
            status_paths = None
    journal.append(
        "ROLLED_BACK", run_id, baseline_restored=restored,
        observed_status_paths=status_paths, rollback_error=rollback_error,
    )
    evidence = {
        "stage": stage,
        "code": code,
        "observed_before": observed_before,
        "baseline_restored": restored,
        "status_paths": status_paths,
        "rollback_error": rollback_error,
        "journal_event_sha256": journal.records()[-1]["event_sha256"],
    }
    return _record_failure(
        store, case_id, grant, broker_sid, journal, run_id,
        stage=stage, code=code, evidence=evidence,
    )


def _complete(
    store: CaseStore,
    case_id: str,
    grant: Mapping[str, Any],
    authorization_sha256: str,
    broker_sid: str,
    journal: BrokerJournal,
    run_id: str,
    post_replacement_evidence_sha256: str,
) -> dict[str, Any]:
    case = store.get_case(case_id)
    canonical_grant = _get_grant(case, grant["grant_id"])
    proposal_protocol = (
        canonical_grant.get("protocol_version")
        == PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION
    )
    result_protocol = (
        PROPOSAL_ACTION_RESULT_PROTOCOL_VERSION
        if proposal_protocol
        else ACTION_GRANT_RESULT_PROTOCOL_VERSION
    )
    result_schema = 2 if proposal_protocol else 1
    authority_field = (
        "authority_sha256" if proposal_protocol else "controller_receipt_sha256"
    )
    result = store.complete_action_grant(
        case_id,
        completion={
            "protocol_version": result_protocol,
            "schema_version": result_schema,
            "grant_id": grant["grant_id"],
            authority_field: authorization_sha256,
            "broker_principal_sid": broker_sid,
            "post_replacement_evidence_sha256": require_snapshot_hash(
                post_replacement_evidence_sha256
            ),
            "completed_at": utc_now(),
        },
        request_id=f"broker-complete-{grant['grant_id']}-{run_id}",
        expected_revision=case["revision"],
    )
    if result.get("idempotent") is not False:
        raise BrokerAuthorizationError("idempotent completion cannot certify this broker run")
    claim_record = canonical_grant.get("claim")
    claim_sha256 = (
        claim_record.get("claim_sha256") if isinstance(claim_record, Mapping) else None
    )
    journal.append(
        "COMPLETED",
        run_id,
        **_journal_action_details(
            canonical_grant,
            broker_sid,
            target_sha256_before=canonical_grant["baseline_sha256"],
            target_sha256_after=canonical_grant["replacement_sha256"],
            changed_path=canonical_grant["target_path"],
            claim_sha256=claim_sha256,
            result_sha256=result["result_sha256"],
        ),
    )
    return result


def _post_probe_complete_and_restore(
    store: CaseStore,
    case_id: str,
    grant: Mapping[str, Any],
    authorization_sha256: str,
    broker_sid: str,
    journal: BrokerJournal,
    run_id: str,
) -> dict[str, Any]:
    post_evidence = _collect_post_replacement_isolation_evidence(
        store, grant, run_id=run_id
    )
    post_sha256 = require_snapshot_hash(
        post_evidence["post_replacement_evidence_sha256"]
    )
    journal.append(
        "POST_ISOLATION_VERIFIED",
        run_id,
        post_replacement_evidence=post_evidence,
        post_replacement_evidence_sha256=post_sha256,
        protected_acl_snapshot_sha256=grant[
            "protected_acl_snapshot_sha256"
        ],
        **_journal_action_details(
            grant,
            broker_sid,
            target_sha256_before=grant["baseline_sha256"],
            target_sha256_after=grant["replacement_sha256"],
            changed_path=grant["target_path"],
            claim_sha256=grant["claim"]["claim_sha256"],
        ),
    )
    result = _complete(
        store,
        case_id,
        grant,
        authorization_sha256,
        broker_sid,
        journal,
        run_id,
        post_sha256,
    )
    _restore_acl_snapshot_after_lockdown(
        journal,
        run_id=run_id,
        snapshot=grant["protected_acl_snapshot"],
        snapshot_sha256=grant["protected_acl_snapshot_sha256"],
        lockdown_dacl_evidence=post_evidence["dacl_evidence"],
        restore_reason="completed",
    )
    return result


def _execute_grant(
    state_root: Path,
    case_id: str,
    grant_id: str,
    controller_receipt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    case_id = canonical_case_id(case_id)
    grant_id = require_stable_id(grant_id, "grant id")
    store = CaseStore(state_root)
    journal = BrokerJournal(store.state_root, case_id, grant_id)
    run_id = secrets.token_hex(16)
    with FileLock(journal.lock_path, timeout=30.0):
        case = store.get_case(case_id)
        grant = _get_grant(case, grant_id)
        proposal_protocol = (
            grant.get("protocol_version")
            == PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION
        )
        if proposal_protocol and controller_receipt is not None:
            raise BrokerAuthorizationError(
                "actorless proposal grants do not accept controller receipts"
            )
        if not proposal_protocol and controller_receipt is None:
            raise BrokerAuthorizationError(
                "legacy action grants require a controller receipt"
            )
        broker_name, broker_sid = windows_identity()
        if (broker_sid != grant["broker_principal_sid"]
                or broker_sid in grant["denied_principal_sids"]):
            raise BrokerAuthorizationError(
                "broker did not run as the exact distinct trusted principal"
            )
        status = grant["status"]
        if status == "COMPLETED":
            _verify_protected_acl_restore(grant["protected_acl_snapshot"])
            raise BrokerAuthorizationError(
                "action grant is already completed; no second action is allowed"
            )
        if status == "FAILED":
            raise BrokerAuthorizationError("action grant failed and its case is locked")
        try:
            _verify_lockdown_journal_binding(grant, journal)
            broker_name, broker_sid, authorization_sha256 = _verify_static_grant(
                store,
                case,
                grant,
                controller_receipt,
                broker_identity=(broker_name, broker_sid),
            )
            roots = _protected_roots(grant)
            if proposal_protocol:
                dacl_evidence = inspect_proposal_dacls(
                    roots,
                    grant["denied_principal_sids"],
                    broker_sid,
                )
            else:
                dacl_evidence = inspect_protected_dacls(
                    roots,
                    grant["denied_principal_sids"],
                    broker_sid,
                    grant["group_membership_evidence_sha256"],
                )
            root, target = _repository_target(grant)
            _cleanup_journal_temps(root, journal.records())
        except (OSError, CaseStateError, BrokerError) as exc:
            if grant.get("status") != "ISSUED":
                raise
            target = Path(grant["worktree"]).joinpath(
                *PurePosixPath(grant["target_path"]).parts
            )
            observed = (
                file_sha256(target)
                if target.is_file() and not target.is_symlink()
                else grant["baseline_sha256"]
            )
            return _record_failure(
                store,
                case_id,
                grant,
                broker_sid,
                journal,
                run_id,
                stage="preclaim",
                code="PRECLAIM_VERIFICATION_FAILED",
                evidence={
                    "observed_before": observed,
                    "error_type": type(exc).__name__,
                    "error_fingerprint": hashlib.sha256(
                        f"{type(exc).__name__}:{exc}".encode("utf-8")
                    ).hexdigest(),
                },
            )
        target_sha256 = file_sha256(target)
        if status == "ISSUED":
            if target_sha256 != grant["baseline_sha256"]:
                raise BrokerAuthorizationError("action target differs before grant claim")
            _assert_git_worktree_clean(root, "before broker claim")
            trusted_probe = _trusted_write_probe(grant, broker_name, broker_sid, store.path)
            claim_request_id = f"broker-claim-{grant_id}-{run_id}"
            journal.append(
                "PRECLAIM",
                run_id,
                claim_request_id=claim_request_id,
                **_journal_action_details(
                    grant,
                    broker_sid,
                    target_sha256_before=target_sha256,
                    target_sha256_after=target_sha256,
                    changed_path=None,
                ),
            )
            claim_protocol = (
                PROPOSAL_ACTION_CLAIM_PROTOCOL_VERSION
                if proposal_protocol
                else ACTION_GRANT_CLAIM_PROTOCOL_VERSION
            )
            claim_schema = 2 if proposal_protocol else 1
            authority_field = (
                "authority_sha256"
                if proposal_protocol
                else "controller_receipt_sha256"
            )
            claim_result = store.claim_action_grant(
                case_id,
                claim={
                    "protocol_version": claim_protocol,
                    "schema_version": claim_schema,
                    "grant_id": grant_id,
                    authority_field: authorization_sha256,
                    "broker_principal_sid": broker_sid,
                    "dacl_evidence": dacl_evidence,
                    "trusted_write_probe": trusted_probe,
                },
                request_id=claim_request_id,
                expected_revision=case["revision"],
            )
            if claim_result.get("idempotent") is not False:
                journal.append("CLAIM_REJECTED_IDEMPOTENT", run_id)
                raise BrokerAuthorizationError(
                    "idempotent claim replay is not write authority for a broker run"
                )
            claimed_grant = _get_grant(store.get_case(case_id), grant_id)
            journal.append(
                "CLAIMED",
                run_id,
                claim_request_id=claim_request_id,
                recovery_attempted=False,
                **_journal_action_details(
                    claimed_grant,
                    broker_sid,
                    target_sha256_before=target_sha256,
                    target_sha256_after=target_sha256,
                    changed_path=None,
                    claim_sha256=claim_result["claim_sha256"],
                ),
            )
            grant = claimed_grant
        elif status == "CLAIMED":
            records = journal.records()
            if not any(record["event"] in {"PRECLAIM", "CLAIMED"} for record in records):
                return _rollback_and_fail(
                    store, case_id, grant, broker_sid, journal, run_id,
                    stage="recovery", code="CLAIMED_WITHOUT_PROTECTED_JOURNAL",
                )
            target_sha256 = file_sha256(target)
            if target_sha256 == grant["replacement_sha256"] and _git_status_paths(root) == grant["allowed_paths"]:
                try:
                    return _post_probe_complete_and_restore(
                        store, case_id, grant, authorization_sha256, broker_sid, journal, run_id
                    )
                except BaseException as exc:
                    if _get_grant(store.get_case(case_id), grant_id)["status"] == "COMPLETED":
                        raise
                    try:
                        return _rollback_and_fail(
                            store, case_id, grant, broker_sid, journal, run_id,
                            stage="recovery", code="POST_REPLACEMENT_PROBE_FAILED",
                        )
                    except BaseException:
                        raise BrokerPreflightError(
                            "recovered replacement failed post-probes and rollback did not complete"
                        ) from exc
            recovery_already = any(record["event"] == "RECOVERY_STARTED" for record in records)
            if target_sha256 != grant["baseline_sha256"] or _git_status_paths(root):
                return _rollback_and_fail(
                    store, case_id, grant, broker_sid, journal, run_id,
                    stage="recovery", code="CLAIMED_TARGET_STATE_INVALID",
                )
            if recovery_already:
                return _rollback_and_fail(
                    store, case_id, grant, broker_sid, journal, run_id,
                    stage="recovery", code="RECOVERY_ALREADY_CONSUMED",
                )
            journal.append("RECOVERY_STARTED", run_id, target_sha256=target_sha256)
        else:
            raise BrokerAuthorizationError("action grant has an unknown status")

        _verify_original_proposal(grant)
        if file_sha256(target) != grant["baseline_sha256"] or regular_file_identity(
            target, stop=root
        ) != grant["target_file_identity"]:
            return _rollback_and_fail(
                store, case_id, grant, broker_sid, journal, run_id,
                stage="post_claim", code="TARGET_IDENTITY_CHANGED_AFTER_CLAIM",
            )
        replacement = _sealed_path(
            Path(grant["state_root"]), grant["sealed_artifact_path"], grant["replacement_sha256"],
            grant["sealed_artifact_identity"],
        )
        temp_relative = f".ccos-action-{hashlib.sha256((grant_id + run_id + 'write').encode()).hexdigest()[:24]}.tmp"
        journal.append("WRITE_STARTED", run_id, temp_relative_path=temp_relative)
        try:
            observed = _atomic_replace(replacement, target, temp_relative)
            if observed != grant["replacement_sha256"] or _git_status_paths(root) != grant["allowed_paths"]:
                raise BrokerPreflightError("replacement produced a path or digest outside the exact grant")
            journal.append(
                "REPLACED",
                run_id,
                **_journal_action_details(
                    grant,
                    broker_sid,
                    target_sha256_before=grant["baseline_sha256"],
                    target_sha256_after=observed,
                    changed_path=grant["target_path"],
                    claim_sha256=grant["claim"]["claim_sha256"],
                ),
            )
            return _post_probe_complete_and_restore(
                store, case_id, grant, authorization_sha256, broker_sid, journal, run_id
            )
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if _get_grant(store.get_case(case_id), grant_id)["status"] == "COMPLETED":
                raise
            code = "POST_CLAIM_ACTION_FAILED"
            try:
                return _rollback_and_fail(
                    store, case_id, grant, broker_sid, journal, run_id,
                    stage="post_claim", code=code,
                )
            except BaseException:
                raise BrokerPreflightError(
                    "post-claim action failed and rollback/failure recording did not complete"
                ) from exc


def execute_grant(
    state_root: Path,
    case_id: str,
    grant_id: str,
    controller_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Recover or consume an already-issued legacy v1 grant."""
    return _execute_grant(state_root, case_id, grant_id, controller_receipt)


def execute_proposal_grant(
    state_root: Path, case_id: str, grant_id: str
) -> dict[str, Any]:
    """Consume one actorless v2 exact-action capability."""
    return _execute_grant(state_root, case_id, grant_id, None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    command = sub.add_parser("worker-probe")
    command.add_argument("--request-json", required=True)

    command = sub.add_parser("execute")
    command.add_argument("--state-root", required=True, type=Path)
    command.add_argument("--case-id", required=True)
    command.add_argument("--grant-id", required=True)
    command.add_argument("--controller-receipt-json", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "worker-probe":
            result = worker_isolation_probe(_json_value(args.request_json, "request-json"))
        elif args.command == "execute":
            result = execute_grant(
                args.state_root,
                args.case_id,
                args.grant_id,
                _json_value(args.controller_receipt_json, "controller-receipt-json"),
            )
        else:
            raise AssertionError(args.command)
    except (BrokerError, CaseStateError, OSError) as exc:
        payload = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"BROKER ERROR [{type(exc).__name__}]: {exc}", file=sys.stderr)
        return 2
    payload = {"ok": True, "result": result}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
