#!/usr/bin/env python3
"""Derive a review completion receipt from one native Codex child rollout.

The canonical engine supplies only the frozen case and reviewer assignment.
The verifier resolves the operating-system Codex profile, authenticates the
native child identity and completed turn, and treats the task_complete
last_agent_message as the only completion payload.
"""

from __future__ import annotations

import datetime as dt
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping
import uuid


REVIEW_COMPLETION_PROTOCOL_VERSION = "ccos-review-completion-v2"
NATIVE_VERIFICATION_PROTOCOL_VERSION = "ccos-review-native-verification-v1"
MAX_ROLLOUT_BYTES = 64 * 1024 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
DANGEROUS_WRITE_MASK = (
    0x0002  # FILE_WRITE_DATA or FILE_ADD_FILE
    | 0x0004  # FILE_APPEND_DATA or FILE_ADD_SUBDIRECTORY
    | 0x0010  # FILE_WRITE_EA
    | 0x0040  # FILE_DELETE_CHILD
    | 0x0100  # FILE_WRITE_ATTRIBUTES
    | 0x00010000  # DELETE
    | 0x00040000  # WRITE_DAC
    | 0x00080000  # WRITE_OWNER
    | 0x10000000  # GENERIC_ALL
    | 0x40000000  # GENERIC_WRITE
)
SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
FINDING_CLASSES = {
    "CURRENT_BLOCKER",
    "NON_BLOCKING",
    "INVALID_OR_STALE",
    "REDESIGN_REQUIRED",
    "CONTROL_FAILURE",
}
FINDING_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class NativeReviewVerificationError(RuntimeError):
    """The native rollout cannot prove the declared reviewer completion."""


def _require_supported_acl_platform() -> None:
    if os.name != "nt":
        raise NativeReviewVerificationError(
            "native review completion verification requires authenticated Windows ACL evidence"
        )


def _canonical_json_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _canonical_uuid(value: object, label: str) -> str:
    raw = str(value or "").strip().lower()
    try:
        parsed = uuid.UUID(raw)
        normalized = str(parsed)
    except (ValueError, AttributeError) as exc:
        raise NativeReviewVerificationError(f"{label} must be a canonical UUID") from exc
    if (
        raw != normalized
        or not UUID_PATTERN.fullmatch(raw)
        or parsed.version != 7
        or parsed.variant != uuid.RFC_4122
    ):
        raise NativeReviewVerificationError(f"{label} must be a canonical UUIDv7")
    return raw


def _aware_timestamp(value: object, label: str) -> tuple[str, dt.datetime]:
    """Accept an aware ISO timestamp and normalize it to UTC."""
    raw = str(value or "").strip()
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NativeReviewVerificationError(
            f"{label} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NativeReviewVerificationError(f"{label} must include a timezone")
    utc = parsed.astimezone(dt.timezone.utc)
    return utc.isoformat().replace("+00:00", "Z"), utc


def _epoch_timestamp(value: object, label: str) -> tuple[str, dt.datetime]:
    """Normalize the native numeric Unix timestamp to UTC ISO-8601."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise NativeReviewVerificationError(f"{label} must be an integer Unix timestamp")
    numeric = float(value)
    try:
        parsed = dt.datetime.fromtimestamp(numeric, tz=dt.timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise NativeReviewVerificationError(f"{label} is outside the supported range") from exc
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z"), parsed


def _validate_envelope_timestamp(
    record: Mapping[str, Any], event_time: dt.datetime, label: str
) -> None:
    _, envelope_time = _aware_timestamp(record.get("timestamp"), f"{label} envelope timestamp")
    if int(envelope_time.timestamp()) != int(event_time.timestamp()):
        raise NativeReviewVerificationError(
            f"{label} envelope and payload timestamps are inconsistent"
        )


def canonical_codex_home(state_root: Path) -> Path:
    """Bind native sessions to the Codex home owning the opened case store."""
    raw_state_root = Path(os.path.abspath(state_root))
    if (
        raw_state_root.name.casefold() != "case-state"
        or raw_state_root.parent.name.casefold() != ".codex"
    ):
        raise NativeReviewVerificationError(
            "native review verification requires the canonical .codex/case-state root"
        )
    codex_home = raw_state_root.parent
    _assert_direct_path(raw_state_root, stop=codex_home)
    return codex_home.resolve(strict=True)


def _is_reparse(stat_result: os.stat_result) -> bool:
    return bool(
        getattr(stat_result, "st_file_attributes", 0)
        & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _assert_direct_path(path: Path, *, stop: Path) -> None:
    current = path
    resolved_stop = stop.resolve(strict=True)
    while True:
        try:
            stat_result = current.lstat()
        except OSError as exc:
            raise NativeReviewVerificationError(
                f"native rollout path cannot be inspected: {current}"
            ) from exc
        if current.is_symlink() or _is_reparse(stat_result):
            raise NativeReviewVerificationError(
                "native rollout path must not traverse a link or reparse point"
            )
        resolved_current = current.resolve(strict=True)
        if resolved_current == resolved_stop:
            break
        if resolved_stop not in resolved_current.parents:
            raise NativeReviewVerificationError(
                "native rollout path escapes the canonical Codex home"
            )
        current = current.parent


def _assert_rollout_acl_readonly(path: Path) -> None:
    if os.name != "nt":
        return
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if length <= 0 or length >= len(buffer):
        raise NativeReviewVerificationError(
            "cannot resolve the trusted Windows system directory"
        )
    icacls_path = (Path(buffer.value) / "icacls.exe").resolve(strict=True)
    try:
        result = subprocess.run(
            [str(icacls_path), str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NativeReviewVerificationError(
            "cannot execute the trusted Windows ACL inspector"
        ) from exc
    if result.returncode != 0:
        raise NativeReviewVerificationError(
            "cannot verify native rollout access controls"
        )
    sandbox_lines = [
        line for line in result.stdout.splitlines()
        if "codexsandboxusers:" in line.casefold()
    ]
    if len(sandbox_lines) != 1:
        raise NativeReviewVerificationError(
            "native rollout must have one visible CodexSandboxUsers access entry"
        )
    line = sandbox_lines[0]
    marker = "codexsandboxusers:"
    suffix = line[line.casefold().index(marker) + len(marker):].strip().upper()
    tokens = re.findall(r"\(([^()]+)\)", suffix)
    if (
        "".join(f"({token})" for token in tokens) != suffix
        or tokens.count("RX") != 1
        or any(token not in {"I", "OI", "CI", "RX"} for token in tokens)
    ):
        raise NativeReviewVerificationError(
            "native rollout ACL must grant CodexSandboxUsers read-execute only"
        )


def _assert_acl_writer_allowlist(paths: list[Path]) -> None:
    if os.name != "nt":
        return
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if length <= 0 or length >= len(buffer):
        raise NativeReviewVerificationError(
            "cannot resolve the trusted Windows system directory"
        )
    powershell = (
        Path(buffer.value) / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    ).resolve(strict=True)
    windows_root = Path(buffer.value).parent.resolve(strict=True)
    paths_json = json.dumps(
        [str(path) for path in paths],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    child_environment = {
        "SystemRoot": str(windows_root),
        "WINDIR": str(windows_root),
        "ComSpec": str(Path(buffer.value) / "cmd.exe"),
        "PATH": str(Path(buffer.value)),
        "PSModulePath": str(powershell.parent / "Modules"),
        "CCOS_ACL_PATHS_JSON": paths_json,
    }
    script = (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        "$items=@(ConvertFrom-Json -InputObject $env:CCOS_ACL_PATHS_JSON|"
        "ForEach-Object{$_});"
        "$records=@(foreach($item in $items){"
        "$acl=Microsoft.PowerShell.Security\\Get-Acl -LiteralPath $item -ErrorAction Stop;"
        "$owner=([Security.Principal.NTAccount]$acl.Owner).Translate("
        "[Security.Principal.SecurityIdentifier]).Value;"
        "$entries=@($acl.Access|ForEach-Object{"
        "$sid=$_.IdentityReference.Translate("
        "[Security.Principal.SecurityIdentifier]).Value;"
        "$mask=([int64]$_.FileSystemRights)-band 0xffffffffL;"
        "[pscustomobject]@{Sid=$sid;Type=[string]$_.AccessControlType;"
        "Mask=$mask}});"
        "[pscustomobject]@{Path=[IO.Path]::GetFullPath($item);"
        "OwnerSid=$owner;Entries=$entries}});"
        "$records|ConvertTo-Json -Compress -Depth 6"
    )
    try:
        result = subprocess.run(
            [
                str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
                "-Command", script,
            ],
            env=child_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise NativeReviewVerificationError(
            "cannot inspect native rollout writer access"
        ) from exc
    if result.returncode != 0:
        raise NativeReviewVerificationError(
            "native rollout writer access inspection failed"
        )
    try:
        records = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise NativeReviewVerificationError(
            "native rollout writer access inspection returned invalid evidence"
        ) from exc
    if not isinstance(records, list) or len(records) != len(paths):
        raise NativeReviewVerificationError(
            "native rollout writer access inspection is incomplete"
        )
    expected_paths = {str(path.resolve(strict=True)).casefold() for path in paths}
    records_by_path: dict[str, tuple[str, list[Mapping[str, Any]]]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise NativeReviewVerificationError(
                "native rollout writer access record is malformed"
            )
        returned_path = str(record.get("Path") or "").casefold()
        owner_sid = str(record.get("OwnerSid") or "").upper()
        entries = record.get("Entries")
        if not owner_sid.startswith("S-") or not isinstance(entries, list):
            raise NativeReviewVerificationError(
                "native rollout owner or access entries are unavailable"
            )
        if returned_path in records_by_path:
            raise NativeReviewVerificationError(
                "native rollout writer access path is duplicated"
            )
        records_by_path[returned_path] = (owner_sid, entries)
    if set(records_by_path) != expected_paths:
        raise NativeReviewVerificationError(
            "native rollout writer access paths differ from the requested chain"
        )
    canonical_root_path = str(paths[-1].resolve(strict=True)).casefold()
    canonical_owner_sid = records_by_path[canonical_root_path][0]
    if any(
        owner_sid != canonical_owner_sid
        for owner_sid, _ in records_by_path.values()
    ):
        raise NativeReviewVerificationError(
            "native rollout ownership differs from the canonical Codex home owner"
        )
    allowed_writers = {
        canonical_owner_sid,
        SYSTEM_SID,
        ADMINISTRATORS_SID,
    }
    for _, entries in records_by_path.values():
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise NativeReviewVerificationError(
                    "native rollout access entry is malformed"
                )
            sid = str(entry.get("Sid") or "").upper()
            entry_type = str(entry.get("Type") or "")
            mask = entry.get("Mask")
            if not sid.startswith("S-") or not isinstance(mask, int):
                raise NativeReviewVerificationError(
                    "native rollout access entry cannot be authenticated"
                )
            if (
                entry_type == "Allow"
                and mask & DANGEROUS_WRITE_MASK
                and sid not in allowed_writers
            ):
                raise NativeReviewVerificationError(
                    f"native rollout grants dangerous writer access to {sid}"
                )


def _assert_acl_chain_readonly(path: Path, *, stop: Path) -> None:
    chain: list[Path] = []
    current = path
    while True:
        chain.append(current)
        _assert_rollout_acl_readonly(current)
        if current == stop:
            break
        if stop not in current.parents:
            raise NativeReviewVerificationError(
                "native rollout ACL chain escapes the canonical Codex home"
            )
        current = current.parent
    _assert_acl_writer_allowlist(chain)


def _rollout_files(sessions_root: Path) -> list[Path]:
    return sorted(
        path
        for path in sessions_root.rglob("rollout-*.jsonl")
        if path.name.startswith("rollout-")
    )


def _read_session_meta(path: Path) -> Mapping[str, Any] | None:
    try:
        with path.open("rb") as stream:
            line = stream.readline(1024 * 1024)
    except OSError:
        return None
    if not line.endswith(b"\n"):
        return None
    try:
        record = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, Mapping) or record.get("type") != "session_meta":
        return None
    payload = record.get("payload")
    return payload if isinstance(payload, Mapping) else None


def _file_contains_completed_turn(path: Path, completed_turn_id: str) -> bool:
    try:
        with path.open("rb") as stream:
            for line in stream:
                if completed_turn_id.encode("ascii") not in line:
                    continue
                try:
                    record = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                payload = record.get("payload") if isinstance(record, Mapping) else None
                if (
                    record.get("type") == "event_msg"
                    and isinstance(payload, Mapping)
                    and payload.get("type") == "task_complete"
                    and payload.get("turn_id") == completed_turn_id
                ):
                    return True
    except OSError:
        return False
    return False


def _discover_rollout(
    sessions_root: Path,
    assignment: Mapping[str, Any],
    *,
    legacy_completed_turn_id: str | None,
) -> Path:
    files = _rollout_files(sessions_root)
    metadata = [(path, _read_session_meta(path)) for path in files]
    if legacy_completed_turn_id is not None:
        turn_id = _canonical_uuid(
            legacy_completed_turn_id, "legacy receipt completed_turn_id"
        )
        expected_path = str(assignment.get("thread_id", ""))
        matches = [
            path
            for path, meta in metadata
            if isinstance(meta, Mapping)
            and meta.get("agent_path") == expected_path
            and _file_contains_completed_turn(path, turn_id)
        ]
        label = f"legacy completed turn {turn_id} and agent_path {expected_path}"
    else:
        native_id = _canonical_uuid(
            assignment.get("native_thread_id"), "reviewer native_thread_id"
        )
        filename_matches = [
            path for path in files if path.name.endswith(f"-{native_id}.jsonl")
        ]
        metadata_matches = [
            path
            for path, meta in metadata
            if isinstance(meta, Mapping) and meta.get("id") == native_id
        ]
        if (
            len(filename_matches) != 1
            or len(metadata_matches) != 1
            or filename_matches[0] != metadata_matches[0]
        ):
            raise NativeReviewVerificationError(
                "native thread identity must resolve to one matching filename and session_meta"
            )
        matches = metadata_matches
        label = f"native thread {native_id}"
    if len(matches) != 1:
        raise NativeReviewVerificationError(
            f"expected one canonical native rollout for {label}; found {len(matches)}"
        )
    selected = matches[0]
    selected_meta = _read_session_meta(selected)
    selected_id = _canonical_uuid(
        selected_meta.get("id") if isinstance(selected_meta, Mapping) else None,
        "selected native thread id",
    )
    duplicate_ids = [
        path
        for path, meta in metadata
        if isinstance(meta, Mapping) and meta.get("id") == selected_id
    ]
    if len(duplicate_ids) != 1:
        raise NativeReviewVerificationError(
            "native session metadata ID is duplicated across rollout files"
        )
    return selected


def _validate_session_meta(
    meta: Mapping[str, Any], assignment: Mapping[str, Any], *, legacy_attestation: bool
) -> tuple[str, str, str]:
    native_thread_id = _canonical_uuid(meta.get("id"), "session_meta native thread id")
    native_parent_thread_id = _canonical_uuid(
        meta.get("parent_thread_id"), "session_meta native parent thread id"
    )
    if meta.get("session_id") != native_parent_thread_id:
        raise NativeReviewVerificationError(
            "session_meta session_id differs from the native parent thread"
        )
    source = meta.get("source")
    subagent = source.get("subagent") if isinstance(source, Mapping) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, Mapping) else None
    if not isinstance(spawn, Mapping):
        raise NativeReviewVerificationError(
            "session_meta lacks native subagent thread_spawn evidence"
        )
    agent_path = str(meta.get("agent_path") or "")
    if (
        not agent_path
        or spawn.get("agent_path") != agent_path
        or spawn.get("parent_thread_id") != native_parent_thread_id
        or spawn.get("depth") != 1
    ):
        raise NativeReviewVerificationError(
            "session_meta child source parent, depth, or agent_path is inconsistent"
        )
    expected_agent_path = str(
        assignment.get("thread_id")
        if legacy_attestation
        else assignment.get("agent_path")
    )
    if agent_path != expected_agent_path:
        raise NativeReviewVerificationError(
            "native reviewer agent_path differs from the frozen assignment"
        )
    if not legacy_attestation and (
        native_thread_id != assignment.get("native_thread_id")
        or native_parent_thread_id != assignment.get("native_parent_thread_id")
    ):
        raise NativeReviewVerificationError(
            "native reviewer parent or child identity differs from the frozen assignment"
        )
    return native_thread_id, native_parent_thread_id, agent_path


def _validate_findings(
    payload: Mapping[str, Any], assignment: Mapping[str, Any]
) -> list[dict[str, Any]]:
    findings = payload.get("findings")
    finding_ids = payload.get("finding_ids")
    if not isinstance(findings, list) or not isinstance(finding_ids, list):
        raise NativeReviewVerificationError(
            "native completion findings and finding_ids must be arrays"
        )
    normalized: list[dict[str, Any]] = []
    expected_fields = {
        "id", "candidate", "repo", "reviewed_sha", "source", "description",
        "classification",
    }
    for finding in findings:
        if not isinstance(finding, Mapping) or set(finding) != expected_fields:
            raise NativeReviewVerificationError(
                "native completion finding must use the fixed finding schema"
            )
        finding_id = str(finding.get("id") or "")
        classification = str(finding.get("classification") or "").upper()
        if (
            not FINDING_ID_PATTERN.fullmatch(finding_id)
            or classification not in FINDING_CLASSES
            or finding.get("repo") != assignment.get("repository")
            or finding.get("reviewed_sha") != assignment.get("reviewed_head")
            or finding.get("source") != assignment.get("reviewer_id")
        ):
            raise NativeReviewVerificationError(
                "native completion finding differs from the frozen reviewer assignment"
            )
        normalized.append({**dict(finding), "classification": classification})
    normalized_ids = [str(item) for item in finding_ids]
    if (
        normalized_ids != sorted(normalized_ids)
        or len(normalized_ids) != len(set(normalized_ids))
        or normalized_ids != sorted(item["id"] for item in normalized)
        or [item["id"] for item in normalized] != normalized_ids
    ):
        raise NativeReviewVerificationError(
            "native completion findings and finding_ids must be sorted, unique, and exact"
        )
    if str(payload.get("completion_state", "")).upper() != "COMPLETED" and normalized:
        raise NativeReviewVerificationError(
            "failed or incomplete native completion cannot report findings"
        )
    return normalized


def _validate_payload(
    payload: Mapping[str, Any],
    *,
    case_id: str,
    cohort_id: str,
    assignment: Mapping[str, Any],
    native_thread_id: str,
    native_parent_thread_id: str,
    agent_path: str,
    expected_findings: list[dict[str, Any]] | None,
    expected_completion_state: str | None,
) -> dict[str, Any]:
    expected_fields = {
        "protocol_version", "schema_version", "case_id", "cohort_id",
        "reviewer_id", "reviewer_role", "thread_id", "native_thread_id",
        "native_parent_thread_id", "agent_path", "repository", "reviewed_head",
        "snapshot", "scope", "scope_sha256", "completion_state", "findings",
        "finding_ids",
    }
    if set(payload) != expected_fields:
        raise NativeReviewVerificationError(
            "native completion must use the fixed ccos-review-completion-v2 schema"
        )
    expected_values = {
        "protocol_version": REVIEW_COMPLETION_PROTOCOL_VERSION,
        "schema_version": 2,
        "case_id": case_id,
        "cohort_id": cohort_id,
        "reviewer_id": assignment.get("reviewer_id"),
        "reviewer_role": "review_child",
        "thread_id": assignment.get("thread_id"),
        "native_thread_id": native_thread_id,
        "native_parent_thread_id": native_parent_thread_id,
        "agent_path": agent_path,
        "repository": assignment.get("repository"),
        "reviewed_head": assignment.get("reviewed_head"),
        "snapshot": assignment.get("snapshot"),
        "scope": assignment.get("scope"),
        "scope_sha256": assignment.get("scope_sha256"),
    }
    for field, expected in expected_values.items():
        if payload.get(field) != expected:
            raise NativeReviewVerificationError(
                f"native completion {field} differs from the frozen assignment"
            )
    scope = str(payload["scope"])
    if hashlib.sha256(scope.encode("utf-8")).hexdigest() != payload["scope_sha256"]:
        raise NativeReviewVerificationError(
            "native completion scope digest is invalid"
        )
    state = str(payload.get("completion_state") or "").upper()
    if state not in {"COMPLETED", "FAILED", "INCOMPLETE"}:
        raise NativeReviewVerificationError(
            "native completion state must be COMPLETED, FAILED, or INCOMPLETE"
        )
    if expected_completion_state is not None and state != expected_completion_state:
        raise NativeReviewVerificationError(
            "native completion state differs from the frozen legacy receipt"
        )
    findings = _validate_findings(payload, assignment)
    if expected_findings is not None and findings != expected_findings:
        raise NativeReviewVerificationError(
            "native completion findings differ from the frozen legacy receipt"
        )
    return {**dict(payload), "completion_state": state, "findings": findings}


def _read_rollout(path: Path) -> tuple[bytes, list[bytes], list[Mapping[str, Any]]]:
    before = path.stat()
    if (
        not path.is_file()
        or before.st_nlink != 1
        or before.st_size <= 0
        or before.st_size > MAX_ROLLOUT_BYTES
    ):
        raise NativeReviewVerificationError(
            "native rollout must be one bounded direct regular file"
        )
    raw = path.read_bytes()
    after = path.stat()
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if any(getattr(before, name) != getattr(after, name) for name in stable_fields):
        raise NativeReviewVerificationError("native rollout changed while being verified")
    if not raw.endswith(b"\n") or len(raw) != before.st_size:
        raise NativeReviewVerificationError("native rollout is truncated")
    raw_lines = raw.splitlines(keepends=True)
    records: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(raw_lines, start=1):
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NativeReviewVerificationError(
                f"native rollout line {line_number} is invalid JSON"
            ) from exc
        if not isinstance(record, Mapping):
            raise NativeReviewVerificationError(
                f"native rollout line {line_number} is not an object"
            )
        records.append(record)
    return raw, raw_lines, records


def _turn_records(
    records: list[Mapping[str, Any]], turn_id: str
) -> tuple[
    list[tuple[int, Mapping[str, Any]]],
    list[tuple[int, Mapping[str, Any]]],
    list[tuple[int, Mapping[str, Any]]],
]:
    started: list[tuple[int, Mapping[str, Any]]] = []
    completed: list[tuple[int, Mapping[str, Any]]] = []
    contexts: list[tuple[int, Mapping[str, Any]]] = []
    for index, record in enumerate(records):
        payload = record.get("payload")
        if not isinstance(payload, Mapping) or payload.get("turn_id") != turn_id:
            continue
        if record.get("type") == "event_msg" and payload.get("type") == "task_started":
            started.append((index, record))
        elif record.get("type") == "event_msg" and payload.get("type") == "task_complete":
            completed.append((index, record))
        elif record.get("type") == "turn_context":
            contexts.append((index, record))
    return started, completed, contexts


def verify_review_completion(
    *,
    case_id: str,
    cohort_id: str,
    cohort_declared_at: str,
    assignment: Mapping[str, Any],
    state_root: Path,
    expected_findings: list[dict[str, Any]] | None = None,
    expected_completion_state: str | None = None,
    legacy_completed_turn_id: str | None = None,
) -> dict[str, Any]:
    _require_supported_acl_platform()
    legacy_attestation = legacy_completed_turn_id is not None
    _, cohort_declared_time = _aware_timestamp(
        cohort_declared_at, "review cohort declared_at"
    )
    codex_home = canonical_codex_home(state_root)
    sessions_root = (codex_home / "sessions").resolve(strict=True)
    _assert_direct_path(sessions_root, stop=codex_home)
    rollout_path = _discover_rollout(
        sessions_root,
        assignment,
        legacy_completed_turn_id=legacy_completed_turn_id,
    )
    _assert_direct_path(rollout_path, stop=codex_home)
    _assert_acl_chain_readonly(rollout_path, stop=codex_home)
    _, raw_lines, records = _read_rollout(rollout_path)
    _assert_acl_chain_readonly(rollout_path, stop=codex_home)
    if not records or records[0].get("type") != "session_meta":
        raise NativeReviewVerificationError(
            "native rollout lacks one leading session_meta record"
        )
    if sum(record.get("type") == "session_meta" for record in records) != 1:
        raise NativeReviewVerificationError(
            "native rollout contains duplicate session_meta records"
        )
    meta = records[0].get("payload")
    if not isinstance(meta, Mapping):
        raise NativeReviewVerificationError("native rollout session_meta is malformed")
    native_thread_id, native_parent_thread_id, agent_path = _validate_session_meta(
        meta, assignment, legacy_attestation=legacy_attestation
    )
    if not rollout_path.name.endswith(f"-{native_thread_id}.jsonl"):
        raise NativeReviewVerificationError(
            "native rollout filename differs from its session_meta identity"
        )

    legacy_complete_index: int | None = None
    if legacy_attestation:
        legacy_turn = _canonical_uuid(
            legacy_completed_turn_id, "legacy receipt completed_turn_id"
        )
        legacy_started, legacy_completed, legacy_contexts = _turn_records(
            records, legacy_turn
        )
        if (
            len(legacy_started) != 1
            or len(legacy_completed) != 1
            or not legacy_contexts
            or legacy_started[0][0] >= legacy_completed[0][0]
            or not all(
                legacy_started[0][0] < index < legacy_completed[0][0]
                for index, _ in legacy_contexts
            )
        ):
            raise NativeReviewVerificationError(
                "legacy receipt turn is not one complete native reviewer turn"
            )
        legacy_complete_index = legacy_completed[0][0]
        legacy_start_record = legacy_started[0][1]
        legacy_complete_record = legacy_completed[0][1]
        legacy_start_event = legacy_start_record["payload"]
        legacy_complete_event = legacy_complete_record["payload"]
        legacy_started_at, legacy_started_time = _epoch_timestamp(
            legacy_start_event.get("started_at"),
            "legacy native turn started_at",
        )
        legacy_completed_at, legacy_completed_time = _epoch_timestamp(
            legacy_complete_event.get("completed_at"),
            "legacy native turn completed_at",
        )
        legacy_complete_started_at, _ = _epoch_timestamp(
            legacy_complete_event.get("started_at"),
            "legacy task_complete started_at",
        )
        _validate_envelope_timestamp(
            legacy_start_record, legacy_started_time, "legacy task_started"
        )
        _validate_envelope_timestamp(
            legacy_complete_record, legacy_completed_time, "legacy task_complete"
        )
        if (
            legacy_complete_started_at != legacy_started_at
            or legacy_completed_time < legacy_started_time
            or legacy_started_time < cohort_declared_time
            or legacy_completed_time < cohort_declared_time
        ):
            raise NativeReviewVerificationError(
                "legacy completed turn predates or conflicts with its frozen cohort"
            )

    v2_completions: list[tuple[int, Mapping[str, Any], str]] = []
    for index, record in enumerate(records):
        payload = record.get("payload")
        if (
            record.get("type") != "event_msg"
            or not isinstance(payload, Mapping)
            or payload.get("type") != "task_complete"
        ):
            continue
        message = payload.get("last_agent_message")
        if not isinstance(message, str) or not message.strip():
            continue
        try:
            parsed = json.loads(message)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(parsed, Mapping)
            and parsed.get("protocol_version") == REVIEW_COMPLETION_PROTOCOL_VERSION
        ):
            v2_completions.append((index, parsed, message))
    if len(v2_completions) != 1:
        raise NativeReviewVerificationError(
            "native rollout must contain exactly one raw ccos-review-completion-v2 final"
        )
    complete_index, completion_payload, last_agent_message = v2_completions[0]
    if legacy_complete_index is not None and complete_index <= legacy_complete_index:
        raise NativeReviewVerificationError(
            "native attestation must follow the legacy completed turn"
        )
    complete_record = records[complete_index]
    complete_event = complete_record["payload"]
    turn_id = _canonical_uuid(
        complete_event.get("turn_id"), "native completion turn id"
    )
    task_started, task_complete, turn_contexts = _turn_records(records, turn_id)
    if (
        len(task_started) != 1
        or len(task_complete) != 1
        or not turn_contexts
        or task_started[0][0] >= task_complete[0][0]
        or task_complete[0][0] != complete_index
        or not all(
            task_started[0][0] < index < task_complete[0][0]
            for index, _ in turn_contexts
        )
    ):
        raise NativeReviewVerificationError(
            "native completion turn requires exactly one ordered task_started, one task_complete, and at least one turn_context"
        )
    start_record = task_started[0][1]
    start_event = start_record["payload"]
    started_at, started_time = _epoch_timestamp(
        start_event.get("started_at"), "native completion started_at"
    )
    completed_at, completed_time = _epoch_timestamp(
        complete_event.get("completed_at"), "native completion completed_at"
    )
    complete_started_at, _ = _epoch_timestamp(
        complete_event.get("started_at"),
        "native task_complete started_at",
    )
    if complete_started_at != started_at:
        raise NativeReviewVerificationError(
            "task_complete started_at differs from task_started"
        )
    _validate_envelope_timestamp(start_record, started_time, "task_started")
    _validate_envelope_timestamp(complete_record, completed_time, "task_complete")
    if completed_time < started_time:
        raise NativeReviewVerificationError(
            "native completion precedes its task start"
        )
    if started_time < cohort_declared_time or completed_time < cohort_declared_time:
        raise NativeReviewVerificationError(
            "native completion predates its frozen review cohort"
        )
    normalized_payload = _validate_payload(
        completion_payload,
        case_id=case_id,
        cohort_id=cohort_id,
        assignment=assignment,
        native_thread_id=native_thread_id,
        native_parent_thread_id=native_parent_thread_id,
        agent_path=agent_path,
        expected_findings=expected_findings,
        expected_completion_state=expected_completion_state,
    )
    prefix = b"".join(raw_lines[: complete_index + 1])
    prefix_sha256 = hashlib.sha256(prefix).hexdigest()
    message_sha256 = hashlib.sha256(last_agent_message.encode("utf-8")).hexdigest()
    evidence_sha256 = hashlib.sha256(
        b"ccos-native-review-evidence-v1\0"
        + prefix_sha256.encode("ascii")
        + b"\0"
        + message_sha256.encode("ascii")
    ).hexdigest()
    verification: dict[str, Any] = {
        "protocol_version": NATIVE_VERIFICATION_PROTOCOL_VERSION,
        "schema_version": 1,
        "status": "VERIFIED",
        "mode": "legacy_attestation" if legacy_attestation else "native_submission",
        "native_thread_id": native_thread_id,
        "native_parent_thread_id": native_parent_thread_id,
        "agent_path": agent_path,
        "rollout_relative_path": rollout_path.relative_to(codex_home).as_posix(),
        "attestation_turn_id": turn_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "log_prefix_sha256": prefix_sha256,
        "last_agent_message_sha256": message_sha256,
        "evidence_sha256": evidence_sha256,
    }
    if legacy_attestation:
        verification["legacy_completed_turn_id"] = legacy_completed_turn_id
    verification["verification_sha256"] = _canonical_json_sha256(verification)
    return {
        "payload": normalized_payload,
        "completed_turn_id": turn_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "native_completion_evidence_sha256": evidence_sha256,
        "native_verification": verification,
    }
