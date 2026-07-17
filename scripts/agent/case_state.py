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
STORE_FILENAME = "case-state.json"
LOCK_FILENAME = ".case-state.lock"
SNAPSHOT_CONTRACT = "ccos-git-snapshot-v1"
GIT_SNAPSHOT_MAGIC = b"CCOS-GIT-SNAPSHOT\0"
LEGACY_FILESYSTEM_SNAPSHOT_CONTRACT = "ccos-snapshot-v1"
LEGACY_FILESYSTEM_SNAPSHOT_MAGIC = b"CCOS-CASE-SNAPSHOT\0"

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
        "findings": {"items": [], "late": [], "frozen": False, "frozen_ids": []},
        "repair": {"authorized_ids": [], "authority": None, "addressed_ids": []},
        "closure": {"preflight": None, "resolutions": None},
        "control": {"active_failure": None, "history": []},
        "lock_reason": None,
        "events": {},
    }


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
        for field in ("review_snapshots", "repaired_snapshots"):
            if not isinstance(candidate[field], dict):
                raise StoreCorruptionError(f"case {case_id} candidate {field} must be an object")
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
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
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
    def _normalize_snapshots(snapshots: Mapping[str, Any], expected_repos: set[str]) -> dict[str, Any]:
        if not isinstance(snapshots, Mapping):
            raise ValidationError("snapshots must be an object")
        result: dict[str, Any] = {}
        for repo, record in snapshots.items():
            normalized_repo = normalize_repo_url(str(repo))
            if not isinstance(record, Mapping):
                raise ValidationError(f"snapshot record for {normalized_repo} must be an object")
            contract = _nonempty(record.get("contract"), "snapshot contract", 100)
            digest = require_snapshot_hash(str(record.get("sha256", "")))
            result[normalized_repo] = {"contract": contract, "sha256": digest}
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
        normalized_snapshots = self._normalize_snapshots(snapshots, set(normalized_heads))
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

    def start_review(self, case_id: str, *, request_id: str, expected_revision: int) -> dict[str, Any]:
        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "CANDIDATE_FROZEN", "start_review")
            if case["limits"]["review_cohorts"] >= 1:
                raise LimitError("case already consumed its one review cohort")
            case["limits"]["review_cohorts"] += 1
            case["state"] = "REVIEW_COLLECTING"
            return {}

        return self._mutate(
            case_id,
            operation="start_review",
            payload={},
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

    def freeze_findings(self, case_id: str, *, request_id: str, expected_revision: int) -> dict[str, Any]:
        def change(case: dict[str, Any], _data: dict[str, Any]) -> dict[str, Any]:
            self._require_state(case, "REVIEW_COLLECTING", "freeze_findings")
            if case["findings"]["frozen"]:
                raise LimitError("findings have already been frozen")
            case["findings"]["frozen"] = True
            case["findings"]["frozen_ids"] = [item["id"] for item in case["findings"]["items"]]
            case["state"] = "FINDINGS_FROZEN"
            blockers = [
                item["id"]
                for item in case["findings"]["items"]
                if item["classification"] == "CURRENT_BLOCKER" and item["authorizing"]
            ]
            return {"frozen_ids": case["findings"]["frozen_ids"], "current_blocker_ids": blockers}

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
        normalized_snapshots = self._normalize_snapshots(snapshots, set(normalized_heads))
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
        normalized_snapshots = self._normalize_snapshots(snapshots, set(normalized_repaired))
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
    environment = os.environ.copy()
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_PREFIX",
    ):
        environment.pop(name, None)
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-C",
                str(root),
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
        "start-review",
        "freeze-findings",
        "close-without-blockers",
        "start-closure-preflight",
    ):
        command = sub.add_parser(name)
        command.add_argument("--case-id", required=True)
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
        return store.start_review(args.case_id, **common)
    if args.command == "add-finding":
        return store.add_finding(
            args.case_id, finding=_json_value(args.finding_json, "finding-json"), **common
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
