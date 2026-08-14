#!/usr/bin/env python3
"""Materialize and transactionally promote a JSON-compatible routing policy."""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import types
from typing import Any, Mapping


OVERLAY_SCHEMA = "catalogue-routing-policy-overlay-v1"
JOURNAL_SCHEMA = "catalogue-routing-policy-materialization-journal-v1"
RECEIPT_SCHEMA = "catalogue-routing-policy-materialization-receipt-v1"
MATERIALIZATION_DOMAIN = b"CATALOGUE-ROUTING-POLICY-MATERIALIZATION-v1\0"
RECEIPT_DOMAIN = b"CATALOGUE-ROUTING-POLICY-MATERIALIZATION-RECEIPT-v1\0"
STATE_DIRECTORY = ".routing-policy-materializer"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
MISSING = "missing"
_POLICY_VALIDATION_MODULE: Any | None = None
_POLICY_VALIDATION_SHA256 = ""


class PolicyMaterializationError(RuntimeError):
    """Base fail-closed policy materialization error."""


class OverlayError(PolicyMaterializationError):
    """The base or overlay is structurally invalid or ambiguous."""


class PreconditionError(PolicyMaterializationError):
    """The live target differs from the explicitly expected state."""


class ReplayError(PolicyMaterializationError):
    """A transaction identifier is bound to other facts or corrupt evidence."""


class LockError(PolicyMaterializationError):
    """The target-local materialization lock could not be acquired."""


class RecoveryError(PolicyMaterializationError):
    """Exact rollback could not be completed without overwriting external drift."""


class InjectedFailure(PolicyMaterializationError):
    """Test-only fault raised at a named transaction boundary."""


@dataclass(frozen=True)
class PolicyApplyOptions:
    base_path: Path | str
    overlay_path: Path | str
    target_policy: Path | str
    transaction_id: str
    expected_target_sha256: str
    expected_materialized_sha256: str
    expected_materialization_digest: str
    capability_manifest_path: Path | str
    policy_schema_path: Path | str | None = None
    overlay_schema_path: Path | str | None = None
    lock_timeout_seconds: float = 30.0
    fault_injection: str | None = None


def _canonical_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    else:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    return rendered.encode("utf-8") + b"\n"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except (AttributeError, OSError):
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_once(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise ReplayError(f"immutable materialization artifact changed: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ReplayError(f"immutable materialization artifact changed: {path}")
        except OSError:
            if path.exists():
                if path.read_bytes() != payload:
                    raise ReplayError(f"immutable materialization artifact changed: {path}")
            else:
                os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _reject_nonfinite_number(token: str) -> None:
    raise ValueError(f"non-finite number is not valid JSON: {token}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object member: {key}")
        value[key] = item
    return value


def _load_json(path: Path, label: str) -> Any:
    if not path.is_file() or path.is_symlink():
        raise OverlayError(f"{label} is not one regular file: {path}")
    before = path.stat()
    try:
        payload = path.read_bytes()
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_nonfinite_number,
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise OverlayError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    after = path.stat()
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise OverlayError(f"{label} changed while it was read")
    return value, payload


def _schema_paths(
    base_path: Path,
    policy_schema_path: Path | str | None,
    overlay_schema_path: Path | str | None,
) -> tuple[Path, Path]:
    routing_root = (
        base_path.parent.parent
        if base_path.parent.name.casefold() == "policy-base"
        else base_path.parent
    )
    policy_schema = (
        Path(policy_schema_path).expanduser().resolve()
        if policy_schema_path is not None
        else routing_root / "routing-policy.schema.json"
    )
    overlay_schema = (
        Path(overlay_schema_path).expanduser().resolve()
        if overlay_schema_path is not None
        else routing_root / "routing-policy-overlay.schema.json"
    )
    return policy_schema, overlay_schema


def _shared_policy_validation() -> Any:
    global _POLICY_VALIDATION_MODULE, _POLICY_VALIDATION_SHA256
    if _POLICY_VALIDATION_MODULE is not None:
        return _POLICY_VALIDATION_MODULE
    script_path = Path(__file__).resolve()
    routing_root = (
        script_path.parent.parent
        if script_path.parent.name.casefold() == "deployment"
        else script_path.parent
    )
    candidates = (
        routing_root / "reference-runtime" / "routing_policy_validation.py",
        routing_root.parent / "hooks" / "routing_policy_validation.py",
    )
    source = next((path for path in candidates if path.is_file()), None)
    if source is None or source.is_symlink():
        raise OverlayError("shared routing policy validator is unavailable")
    try:
        before = source.stat()
        payload = source.read_bytes()
        after = source.stat()
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or len(payload) != before.st_size
        ):
            raise OverlayError("shared routing policy validator changed while loading")
        module = types.ModuleType("_catalogue_router_policy_validation")
        module.__file__ = str(source)
        exec(compile(payload.decode("utf-8-sig"), str(source), "exec"), module.__dict__)
    except Exception as exc:
        if isinstance(exc, OverlayError):
            raise
        raise OverlayError(
            f"shared routing policy validator failed to load: {exc}"
        ) from exc
    _POLICY_VALIDATION_MODULE = module
    _POLICY_VALIDATION_SHA256 = _sha256_bytes(payload)
    return module


def _policy_validation_source_path() -> Path:
    module = _shared_policy_validation()
    source = Path(str(module.__file__)).resolve()
    if not source.is_file() or source.is_symlink():
        raise OverlayError("shared routing policy validator source is unavailable")
    return source


def _policy_validation_source_sha256() -> str:
    _shared_policy_validation()
    if not SHA256_RE.fullmatch(_POLICY_VALIDATION_SHA256):
        raise OverlayError("shared routing policy validator hash is unavailable")
    return _POLICY_VALIDATION_SHA256


def _validate_against_schema(instance: Any, schema: Any, label: str) -> None:
    module = _shared_policy_validation()
    try:
        module.validate_against_schema(instance, schema, label)
    except module.RoutingPolicyValidationError as exc:
        raise OverlayError(str(exc)) from exc


def _load_capability_manifest(
    path: Path | str,
) -> tuple[
    dict[str, Any],
    bytes,
    frozenset[str],
    frozenset[str],
    frozenset[str],
]:
    manifest, payload = _load_json(
        Path(path).expanduser().resolve(), "capability validation manifest"
    )
    module = _shared_policy_validation()
    try:
        active, suppressed, declared = module.capability_identifier_sets(manifest)
    except module.RoutingPolicyValidationError as exc:
        raise OverlayError(str(exc)) from exc
    return manifest, payload, active, suppressed, declared


def _validate_policy_semantics(
    policy: Mapping[str, Any],
    active_capabilities: frozenset[str],
    declared_capabilities: frozenset[str] | None = None,
) -> None:
    module = _shared_policy_validation()
    declared = declared_capabilities or active_capabilities
    try:
        module.validate_policy_semantics(
            policy,
            active_capabilities,
            declared,
        )
    except module.RoutingPolicyValidationError as exc:
        raise OverlayError(str(exc)) from exc


def _pointer_tokens(pointer: str) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/") or pointer == "/":
        raise OverlayError(f"invalid JSON pointer: {pointer!r}")
    values = []
    for raw in pointer[1:].split("/"):
        if not raw or re.search(r"~(?:[^01]|$)", raw):
            raise OverlayError(f"invalid JSON pointer: {pointer!r}")
        values.append(raw.replace("~1", "/").replace("~0", "~"))
    return values


def _resolve_pointer(root: Any, pointer: str) -> Any:
    current = root
    for token in _pointer_tokens(pointer):
        if not isinstance(current, dict) or token not in current:
            raise OverlayError(f"JSON pointer does not resolve to an existing object member: {pointer}")
        current = current[token]
    return current


def _set_existing(root: Any, pointer: str, value: Any) -> None:
    tokens = _pointer_tokens(pointer)
    current = root
    for token in tokens[:-1]:
        if not isinstance(current, dict) or token not in current:
            raise OverlayError(f"set path does not resolve: {pointer}")
        current = current[token]
    leaf = tokens[-1]
    if not isinstance(current, dict) or leaf not in current:
        raise OverlayError(f"set path does not resolve: {pointer}")
    if current[leaf] == value:
        raise OverlayError(f"set operation is already present in the base policy: {pointer}")
    current[leaf] = copy.deepcopy(value)


def _require_exact_keys(value: Mapping[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(allowed):
        raise OverlayError(
            f"{label} fields are invalid: missing={sorted(required - keys)}, extra={sorted(keys - allowed)}"
        )


def _scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) and value is not None


def _validate_overlay(value: Any) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        raise OverlayError("overlay must be a JSON object")
    _require_exact_keys(
        value,
        {"schema_version", "overlay_id", "operations"},
        {"schema_version", "overlay_id", "operations"},
        "overlay",
    )
    if value["schema_version"] != OVERLAY_SCHEMA:
        raise OverlayError("overlay schema version is unsupported")
    overlay_id = value["overlay_id"]
    if not isinstance(overlay_id, str) or not IDENTIFIER_RE.fullmatch(overlay_id):
        raise OverlayError("overlay identifier is invalid")
    operations = value["operations"]
    if not isinstance(operations, list) or not operations:
        raise OverlayError("overlay operations must be a non-empty array")
    signatures: set[bytes] = set()
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(operations):
        if not isinstance(raw, dict):
            raise OverlayError(f"overlay operation {index} is not an object")
        operation = copy.deepcopy(raw)
        op = operation.get("op")
        if op == "set":
            _require_exact_keys(operation, {"op", "path", "value"}, {"op", "path", "value"}, f"operation {index}")
            _pointer_tokens(operation["path"])
            signature_value = [op, operation["path"]]
        elif op == "insert_unique":
            _require_exact_keys(
                operation,
                {"op", "path", "select", "member_path", "value", "before", "after"},
                {"op", "path", "select", "member_path", "value"},
                f"operation {index}",
            )
            if "before" in operation and "after" in operation:
                raise OverlayError("insert_unique cannot declare both before and after")
            _pointer_tokens(operation["path"])
            _pointer_tokens(operation["member_path"])
            selector = operation["select"]
            if not isinstance(selector, dict):
                raise OverlayError("insert_unique selector must be an object")
            _require_exact_keys(selector, {"key", "equals"}, {"key", "equals"}, "insert_unique selector")
            if not isinstance(selector["key"], str) or not selector["key"] or not _scalar(selector["equals"]):
                raise OverlayError("insert_unique selector is invalid")
            signature_value = [
                op,
                operation["path"],
                selector["key"],
                selector["equals"],
                operation["member_path"],
                operation["value"],
            ]
        elif op == "insert_object_unique":
            _require_exact_keys(
                operation,
                {"op", "path", "unique_key", "value", "before_key_value", "after_key_value"},
                {"op", "path", "unique_key", "value"},
                f"operation {index}",
            )
            if "before_key_value" in operation and "after_key_value" in operation:
                raise OverlayError(
                    "insert_object_unique cannot declare both before_key_value and after_key_value"
                )
            _pointer_tokens(operation["path"])
            if not isinstance(operation["unique_key"], str) or not operation["unique_key"]:
                raise OverlayError("insert_object_unique unique_key is invalid")
            if not isinstance(operation["value"], dict) or operation["unique_key"] not in operation["value"]:
                raise OverlayError("insert_object_unique value lacks its unique key")
            signature_value = [
                op,
                operation["path"],
                operation["unique_key"],
                operation["value"][operation["unique_key"]],
            ]
        else:
            raise OverlayError(f"unsupported overlay operation: {op!r}")
        signature = _canonical_json_bytes(signature_value)
        if signature in signatures:
            raise OverlayError(f"duplicate overlay edit at operation {index}")
        signatures.add(signature)
        normalized.append(operation)
    return overlay_id, normalized


def _unique_anchor_index(values: list[Any], anchor: Any, label: str) -> int:
    positions = [index for index, value in enumerate(values) if value == anchor]
    if len(positions) != 1:
        raise OverlayError(f"{label} must resolve exactly once")
    return positions[0]


def _apply_insert_unique(policy: dict[str, Any], operation: Mapping[str, Any]) -> None:
    collection = _resolve_pointer(policy, operation["path"])
    if not isinstance(collection, list) or not all(isinstance(item, dict) for item in collection):
        raise OverlayError("insert_unique selection path must resolve to an object array")
    selector = operation["select"]
    matches = [item for item in collection if item.get(selector["key"]) == selector["equals"]]
    if len(matches) != 1:
        raise OverlayError("insert_unique selector must resolve exactly once")
    target = _resolve_pointer(matches[0], operation["member_path"])
    if not isinstance(target, list):
        raise OverlayError("insert_unique member path must resolve to an array")
    if any(item == operation["value"] for item in target):
        raise OverlayError("insert_unique value already exists in the base policy")
    if "before" in operation:
        index = _unique_anchor_index(target, operation["before"], "insert_unique before anchor")
    elif "after" in operation:
        index = _unique_anchor_index(target, operation["after"], "insert_unique after anchor") + 1
    else:
        index = len(target)
    target.insert(index, copy.deepcopy(operation["value"]))


def _apply_insert_object_unique(policy: dict[str, Any], operation: Mapping[str, Any]) -> None:
    collection = _resolve_pointer(policy, operation["path"])
    unique_key = operation["unique_key"]
    if not isinstance(collection, list) or not all(
        isinstance(item, dict) and unique_key in item for item in collection
    ):
        raise OverlayError("insert_object_unique path must resolve to an object array with unique keys")
    keys = [item[unique_key] for item in collection]
    if len({_canonical_json_bytes(item) for item in keys}) != len(keys):
        raise OverlayError("insert_object_unique base collection already has duplicate keys")
    new_key = operation["value"][unique_key]
    if any(item == new_key for item in keys):
        raise OverlayError("insert_object_unique key already exists in the base policy")
    if "before_key_value" in operation:
        index = _unique_anchor_index(
            keys,
            operation["before_key_value"],
            "insert_object_unique before anchor",
        )
    elif "after_key_value" in operation:
        index = _unique_anchor_index(
            keys,
            operation["after_key_value"],
            "insert_object_unique after anchor",
        ) + 1
    else:
        index = len(collection)
    collection.insert(index, copy.deepcopy(operation["value"]))


def materialize_policy(
    base_path: Path | str,
    overlay_path: Path | str,
    *,
    capability_manifest_path: Path | str,
    policy_schema_path: Path | str | None = None,
    overlay_schema_path: Path | str | None = None,
) -> dict[str, Any]:
    base_resolved = Path(base_path).expanduser().resolve()
    base, base_bytes = _load_json(base_resolved, "base routing policy")
    overlay, overlay_bytes = _load_json(
        Path(overlay_path).expanduser().resolve(), "routing policy deployment overlay"
    )
    policy_schema_resolved, overlay_schema_resolved = _schema_paths(
        base_resolved,
        policy_schema_path,
        overlay_schema_path,
    )
    policy_schema, policy_schema_bytes = _load_json(
        policy_schema_resolved, "routing policy schema"
    )
    overlay_schema, overlay_schema_bytes = _load_json(
        overlay_schema_resolved, "routing policy overlay schema"
    )
    (
        _,
        capability_manifest_bytes,
        active_capabilities,
        suppressed_capabilities,
        declared_capabilities,
    ) = _load_capability_manifest(capability_manifest_path)
    policy_validator_sha256 = _policy_validation_source_sha256()
    if not isinstance(base, dict):
        raise OverlayError("base routing policy must be a JSON object")
    _validate_against_schema(base, policy_schema, "base routing policy")
    _validate_policy_semantics(base, active_capabilities, declared_capabilities)
    _validate_against_schema(overlay, overlay_schema, "routing policy deployment overlay")
    overlay_id, operations = _validate_overlay(overlay)
    result = copy.deepcopy(base)
    for operation in operations:
        if operation["op"] == "set":
            _set_existing(result, operation["path"], operation["value"])
        elif operation["op"] == "insert_unique":
            _apply_insert_unique(result, operation)
        else:
            _apply_insert_object_unique(result, operation)
    _validate_against_schema(result, policy_schema, "materialized routing policy")
    _validate_policy_semantics(result, active_capabilities, declared_capabilities)
    materialized = _canonical_json_bytes(result, pretty=True)
    facts = {
        "base_sha256": _sha256_bytes(base_bytes),
        "capability_manifest_sha256": _sha256_bytes(capability_manifest_bytes),
        "materialized_sha256": _sha256_bytes(materialized),
        "overlay_id": overlay_id,
        "overlay_schema_sha256": _sha256_bytes(overlay_schema_bytes),
        "overlay_sha256": _sha256_bytes(overlay_bytes),
        "policy_schema_sha256": _sha256_bytes(policy_schema_bytes),
        "policy_validator_sha256": policy_validator_sha256,
        "schema_version": "catalogue-routing-policy-materialization-v1",
    }
    facts["materialization_digest"] = _sha256_bytes(
        MATERIALIZATION_DOMAIN + _canonical_json_bytes(facts)
    )
    return {
        "facts": facts,
        "policy": result,
        "policy_bytes": materialized,
        "validation_context": {
            "active_capabilities": active_capabilities,
            "declared_capabilities": declared_capabilities,
            "policy_schema": policy_schema,
            "suppressed_capabilities": suppressed_capabilities,
        },
    }


def _target_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"state": MISSING, "sha256": MISSING, "size": 0}
    if path.is_symlink() or not path.is_file():
        raise PreconditionError(f"routing policy target is not one regular file: {path}")
    payload = path.read_bytes()
    return {"state": "present", "sha256": _sha256_bytes(payload), "size": len(payload)}


def _state_matches(path: Path, expected_sha256: str) -> bool:
    state = _target_state(path)
    return state["sha256"] == expected_sha256


class _ExclusiveLock:
    def __init__(self, path: Path, timeout_seconds: float) -> None:
        self.path = path
        self.timeout_seconds = max(0.0, timeout_seconds)
        self.stream: Any = None

    def __enter__(self) -> "_ExclusiveLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.is_symlink():
            raise LockError("materialization lock cannot be a symbolic link")
        self.stream = self.path.open("a+b")
        self.stream.seek(0, os.SEEK_END)
        if self.stream.tell() == 0:
            self.stream.write(b"\0")
            self.stream.flush()
            os.fsync(self.stream.fileno())
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self.stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (OSError, BlockingIOError) as exc:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    self.stream = None
                    raise LockError("routing policy materialization lock is busy") from exc
                time.sleep(0.05)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.stream is None:
            return
        try:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None


def _save_journal(path: Path, journal: dict[str, Any]) -> None:
    journal["updated_at"] = _utc_now()
    _atomic_write(path, _canonical_json_bytes(journal))


def _receipt_from_journal(journal_path: Path, journal: Mapping[str, Any], outcome: str) -> dict[str, Any]:
    body = {
        "base_sha256": journal["base_sha256"],
        "capability_manifest_sha256": journal["capability_manifest_sha256"],
        "completed_at": journal["updated_at"],
        "error": journal.get("error"),
        "expected_target_sha256": journal["expected_target_sha256"],
        "journal_sha256": _sha256_file(journal_path),
        "materialization_digest": journal["materialization_digest"],
        "materialized_sha256": journal["materialized_sha256"],
        "outcome": outcome,
        "overlay_id": journal["overlay_id"],
        "overlay_schema_sha256": journal["overlay_schema_sha256"],
        "overlay_sha256": journal["overlay_sha256"],
        "policy_schema_sha256": journal["policy_schema_sha256"],
        "policy_validator_sha256": journal["policy_validator_sha256"],
        "schema_version": RECEIPT_SCHEMA,
        "target_policy": journal["target_policy"],
        "transaction_id": journal["transaction_id"],
    }
    body["receipt_sha256"] = _sha256_bytes(
        RECEIPT_DOMAIN + _canonical_json_bytes(body)
    )
    return body


def _verify_receipt(receipt: Mapping[str, Any], journal_path: Path) -> None:
    body = dict(receipt)
    claimed = body.pop("receipt_sha256", None)
    if claimed != _sha256_bytes(RECEIPT_DOMAIN + _canonical_json_bytes(body)):
        raise ReplayError("materialization receipt digest is invalid")
    if not journal_path.is_file() or receipt.get("journal_sha256") != _sha256_file(journal_path):
        raise ReplayError("materialization receipt no longer matches its durable journal")


def _binding(facts: Mapping[str, Any], target: Path, options: PolicyApplyOptions) -> dict[str, Any]:
    return {
        "base_sha256": facts["base_sha256"],
        "capability_manifest_sha256": facts["capability_manifest_sha256"],
        "expected_target_sha256": options.expected_target_sha256,
        "materialization_digest": facts["materialization_digest"],
        "materialized_sha256": facts["materialized_sha256"],
        "overlay_id": facts["overlay_id"],
        "overlay_schema_sha256": facts["overlay_schema_sha256"],
        "overlay_sha256": facts["overlay_sha256"],
        "policy_schema_sha256": facts["policy_schema_sha256"],
        "policy_validator_sha256": facts["policy_validator_sha256"],
        "target_policy": str(target),
        "transaction_id": options.transaction_id,
    }


def _verify_binding(value: Mapping[str, Any], binding: Mapping[str, Any]) -> None:
    if any(value.get(key) != expected for key, expected in binding.items()):
        raise ReplayError("materialization transaction is bound to different facts")


def _fault(name: str, configured: str | None) -> None:
    if name == configured:
        raise InjectedFailure(f"injected materialization failure at {name}")


def _rollback(target: Path, transaction_root: Path, journal: Mapping[str, Any]) -> None:
    current = _target_state(target)
    if current["sha256"] == journal["expected_target_sha256"]:
        return
    if current["sha256"] != journal["materialized_sha256"]:
        raise RecoveryError("rollback refused to overwrite external target drift")
    if journal["expected_target_sha256"] == MISSING:
        target.unlink()
        _fsync_directory(target.parent)
        return
    backup = transaction_root / "rollback" / "routing-policy.yaml"
    if not backup.is_file() or _sha256_file(backup) != journal["expected_target_sha256"]:
        raise RecoveryError("rollback backup is unavailable or changed")
    _atomic_write(target, backup.read_bytes())
    if not _state_matches(target, journal["expected_target_sha256"]):
        raise RecoveryError("rollback target did not return to its exact baseline")


def apply_materialized_policy(options: PolicyApplyOptions) -> dict[str, Any]:
    transaction_id = options.transaction_id
    if not isinstance(transaction_id, str) or not IDENTIFIER_RE.fullmatch(transaction_id):
        raise PolicyMaterializationError("transaction identifier is invalid")
    if options.expected_target_sha256 != MISSING and not SHA256_RE.fullmatch(
        options.expected_target_sha256
    ):
        raise PolicyMaterializationError("expected target SHA-256 is invalid")
    if not SHA256_RE.fullmatch(options.expected_materialized_sha256):
        raise PolicyMaterializationError("expected materialized SHA-256 is invalid")
    if not SHA256_RE.fullmatch(options.expected_materialization_digest):
        raise PolicyMaterializationError("expected materialization digest is invalid")
    target_raw = Path(options.target_policy).expanduser()
    if not target_raw.is_absolute() or target_raw.name != "routing-policy.yaml":
        raise PolicyMaterializationError(
            "target policy must be an explicit absolute routing-policy.yaml path"
        )
    if target_raw.is_symlink():
        raise PolicyMaterializationError("target policy cannot be a symbolic link")
    target = target_raw.resolve(strict=False)
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise PolicyMaterializationError("target policy parent must be one real directory")

    materialized = materialize_policy(
        options.base_path,
        options.overlay_path,
        capability_manifest_path=options.capability_manifest_path,
        policy_schema_path=options.policy_schema_path,
        overlay_schema_path=options.overlay_schema_path,
    )
    base_resolved = Path(options.base_path).expanduser().resolve()
    overlay_resolved = Path(options.overlay_path).expanduser().resolve()
    if target in {base_resolved, overlay_resolved} or base_resolved == overlay_resolved:
        raise PolicyMaterializationError(
            "base, overlay, and live target must be three distinct files"
        )
    facts = materialized["facts"]
    if facts["materialized_sha256"] != options.expected_materialized_sha256:
        raise PolicyMaterializationError(
            "materialized policy does not match the explicitly expected SHA-256"
        )
    if facts["materialization_digest"] != options.expected_materialization_digest:
        raise PolicyMaterializationError(
            "materialization inputs do not match the explicitly expected digest"
        )
    binding = _binding(facts, target, options)
    state_root = target.parent / STATE_DIRECTORY
    if state_root.exists() and (state_root.is_symlink() or not state_root.is_dir()):
        raise PolicyMaterializationError("materialization state root is not one real directory")
    state_root.mkdir(exist_ok=True)
    transactions_root = state_root / "transactions"
    if transactions_root.exists() and (
        transactions_root.is_symlink() or not transactions_root.is_dir()
    ):
        raise PolicyMaterializationError(
            "materialization transactions root is not one real directory"
        )
    transactions_root.mkdir(exist_ok=True)
    transaction_root = transactions_root / transaction_id
    lock_path = state_root / "materialization.lock"

    with _ExclusiveLock(lock_path, options.lock_timeout_seconds):
        if transaction_root.exists() and (
            transaction_root.is_symlink() or not transaction_root.is_dir()
        ):
            raise ReplayError("materialization transaction root is not one real directory")
        receipt_path = transaction_root / "receipt.json"
        journal_path = transaction_root / "journal.json"
        if receipt_path.exists():
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ReplayError(f"materialization receipt is unreadable: {exc}") from exc
            if not isinstance(receipt, dict) or receipt.get("schema_version") != RECEIPT_SCHEMA:
                raise ReplayError("materialization receipt schema is invalid")
            _verify_binding(receipt, binding)
            _verify_receipt(receipt, journal_path)
            if receipt["outcome"] == "applied" and not _state_matches(
                target, facts["materialized_sha256"]
            ):
                raise ReplayError("applied routing policy target has drifted")
            if receipt["outcome"] in {"rolled_back", "recovered_rolled_back"} and not _state_matches(
                target, options.expected_target_sha256
            ):
                raise ReplayError("rolled-back routing policy target has drifted")
            return receipt

        if journal_path.exists():
            try:
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ReplayError(f"materialization journal is unreadable: {exc}") from exc
            if not isinstance(journal, dict) or journal.get("schema_version") != JOURNAL_SCHEMA:
                raise ReplayError("materialization journal schema is invalid")
            _verify_binding(journal, binding)
            current_sha256 = _target_state(target)["sha256"]
            if (
                journal.get("phase")
                in {"LOCK_ACQUIRED", "STAGE_VERIFIED", "PROMOTION_PREPARED"}
                and current_sha256 != journal["expected_target_sha256"]
            ):
                journal["error"] = (
                    "interrupted pre-promotion transaction observed external target drift"
                )
                journal["phase"] = "PRECONDITION_FAILED"
                _save_journal(journal_path, journal)
                receipt = _receipt_from_journal(
                    journal_path, journal, "precondition_failed"
                )
                _write_once(receipt_path, _canonical_json_bytes(receipt))
                return receipt
            try:
                _rollback(target, transaction_root, journal)
            except Exception as exc:
                journal["error"] = f"{type(exc).__name__}: {exc}"
                journal["phase"] = "ROLLBACK_FAILED"
                _save_journal(journal_path, journal)
                receipt = _receipt_from_journal(journal_path, journal, "rollback_failed")
                _write_once(receipt_path, _canonical_json_bytes(receipt))
                raise
            journal["error"] = "interrupted materialization recovered to its exact baseline"
            journal["phase"] = "ROLLED_BACK"
            _save_journal(journal_path, journal)
            receipt = _receipt_from_journal(journal_path, journal, "recovered_rolled_back")
            _write_once(receipt_path, _canonical_json_bytes(receipt))
            return receipt

        if transaction_root.exists() and any(transaction_root.iterdir()):
            raise ReplayError("materialization workspace exists without a valid journal")
        transaction_root.mkdir(parents=True, exist_ok=True)
        prior = _target_state(target)
        journal = {
            **binding,
            "error": None,
            "phase": "LOCK_ACQUIRED",
            "prior_size": prior["size"],
            "schema_version": JOURNAL_SCHEMA,
            "started_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        _save_journal(journal_path, journal)
        if prior["sha256"] != options.expected_target_sha256:
            journal["error"] = "live routing policy differs from the explicit compare-and-swap precondition"
            journal["phase"] = "PRECONDITION_FAILED"
            _save_journal(journal_path, journal)
            receipt = _receipt_from_journal(journal_path, journal, "precondition_failed")
            _write_once(receipt_path, _canonical_json_bytes(receipt))
            return receipt

        try:
            if prior["state"] == "present":
                backup = transaction_root / "rollback" / "routing-policy.yaml"
                _atomic_write(backup, target.read_bytes())
                if _sha256_file(backup) != options.expected_target_sha256:
                    raise PreconditionError("routing policy changed during backup capture")
            stage = transaction_root / "stage" / "routing-policy.yaml"
            _atomic_write(stage, materialized["policy_bytes"])
            if _sha256_file(stage) != facts["materialized_sha256"]:
                raise PolicyMaterializationError("staged materialized policy hash is invalid")
            journal["phase"] = "STAGE_VERIFIED"
            _save_journal(journal_path, journal)
            _fault("after-stage", options.fault_injection)
            journal["phase"] = "PROMOTION_PREPARED"
            _save_journal(journal_path, journal)
            if not _state_matches(target, options.expected_target_sha256):
                raise PreconditionError("routing policy changed after staging")
            if stage.stat().st_dev != target.parent.stat().st_dev:
                raise PolicyMaterializationError("policy staging is not target-local")
            journal["phase"] = "PROMOTING"
            _save_journal(journal_path, journal)
            os.replace(stage, target)
            _fsync_directory(target.parent)
            if not _state_matches(target, facts["materialized_sha256"]):
                raise PolicyMaterializationError("promoted routing policy hash is invalid")
            journal["phase"] = "PROMOTED"
            _save_journal(journal_path, journal)
            _fault("after-promote", options.fault_injection)
            _validate_against_schema(
                materialized["policy"],
                materialized["validation_context"]["policy_schema"],
                "pre-receipt materialized routing policy",
            )
            _validate_policy_semantics(
                materialized["policy"],
                materialized["validation_context"]["active_capabilities"],
                materialized["validation_context"]["declared_capabilities"],
            )
            if (
                _policy_validation_source_sha256()
                != facts["policy_validator_sha256"]
                or _sha256_file(_policy_validation_source_path())
                != facts["policy_validator_sha256"]
            ):
                raise PolicyMaterializationError(
                    "shared routing policy validator changed before receipt"
                )
            journal["phase"] = "LIVE_TARGET_VERIFIED"
            _save_journal(journal_path, journal)
            _fault("after-verify", options.fault_injection)
            journal["phase"] = "COMPLETED"
            _save_journal(journal_path, journal)
            receipt = _receipt_from_journal(journal_path, journal, "applied")
            _write_once(receipt_path, _canonical_json_bytes(receipt))
            return receipt
        except Exception as exc:
            journal["error"] = f"{type(exc).__name__}: {exc}"
            if (
                isinstance(exc, PreconditionError)
                and journal.get("phase")
                in {"LOCK_ACQUIRED", "STAGE_VERIFIED", "PROMOTION_PREPARED"}
                and _target_state(target)["sha256"]
                != options.expected_target_sha256
            ):
                journal["phase"] = "PRECONDITION_FAILED"
                _save_journal(journal_path, journal)
                receipt = _receipt_from_journal(
                    journal_path, journal, "precondition_failed"
                )
                _write_once(receipt_path, _canonical_json_bytes(receipt))
                return receipt
            try:
                _rollback(target, transaction_root, journal)
            except Exception as rollback_exc:
                journal["error"] += (
                    f" | rollback={type(rollback_exc).__name__}: {rollback_exc}"
                )
                journal["phase"] = "ROLLBACK_FAILED"
                _save_journal(journal_path, journal)
                receipt = _receipt_from_journal(journal_path, journal, "rollback_failed")
                _write_once(receipt_path, _canonical_json_bytes(receipt))
                raise RecoveryError(journal["error"]) from rollback_exc
            journal["phase"] = "ROLLED_BACK"
            _save_journal(journal_path, journal)
            receipt = _receipt_from_journal(journal_path, journal, "rolled_back")
            _write_once(receipt_path, _canonical_json_bytes(receipt))
            return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Materialize a base routing policy plus one deployment overlay."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render")
    render.add_argument("--base", required=True)
    render.add_argument("--overlay", required=True)
    render.add_argument("--capability-manifest", required=True)
    render.add_argument("--policy-schema", required=True)
    render.add_argument("--overlay-schema", required=True)
    render.add_argument("--output", required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("--base", required=True)
    apply.add_argument("--overlay", required=True)
    apply.add_argument("--capability-manifest", required=True)
    apply.add_argument("--policy-schema", required=True)
    apply.add_argument("--overlay-schema", required=True)
    apply.add_argument("--target-policy", required=True)
    apply.add_argument("--transaction-id", required=True)
    apply.add_argument("--expected-target-sha256", required=True)
    apply.add_argument("--expected-materialized-sha256", required=True)
    apply.add_argument("--expected-materialization-digest", required=True)
    apply.add_argument("--lock-timeout-seconds", type=float, default=30.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "render":
            result = materialize_policy(
                arguments.base,
                arguments.overlay,
                capability_manifest_path=arguments.capability_manifest,
                policy_schema_path=arguments.policy_schema,
                overlay_schema_path=arguments.overlay_schema,
            )
            _atomic_write(Path(arguments.output).expanduser().resolve(), result["policy_bytes"])
            sys.stdout.buffer.write(_canonical_json_bytes(result["facts"]))
            return 0
        receipt = apply_materialized_policy(
            PolicyApplyOptions(
                base_path=arguments.base,
                overlay_path=arguments.overlay,
                target_policy=arguments.target_policy,
                transaction_id=arguments.transaction_id,
                expected_target_sha256=arguments.expected_target_sha256,
                expected_materialized_sha256=arguments.expected_materialized_sha256,
                expected_materialization_digest=arguments.expected_materialization_digest,
                capability_manifest_path=arguments.capability_manifest,
                policy_schema_path=arguments.policy_schema,
                overlay_schema_path=arguments.overlay_schema,
                lock_timeout_seconds=arguments.lock_timeout_seconds,
            )
        )
        sys.stdout.buffer.write(_canonical_json_bytes(receipt))
        return 0 if receipt["outcome"] == "applied" else 2
    except PolicyMaterializationError as exc:
        print(f"ROUTING_POLICY_MATERIALIZATION_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
