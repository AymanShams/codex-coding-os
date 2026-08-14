#!/usr/bin/env python3
"""Deterministic, fail-closed recovery for the canonical capability manifest."""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import tomllib
import uuid
from pathlib import Path
from typing import Any, Callable

import capability_config_fingerprint as config_fingerprint
import capability_index as index


RECEIPT_SCHEMA = "capability-authority-receipt-v3"
SESSION_START_RECEIPT_SCHEMA = "capability-session-start-recovery-v1"
AUTHORITY_POINTER_SCHEMA = "capability-authority-pointer-v1"
AUTHORITY_TRANSACTION_SCHEMA = "capability-authority-transaction-v2"
OPERATOR_REVIEW_SCHEMA = "capability-operator-rebaseline-review-v1"
QUARANTINE_OBSERVATION_SCHEMA = "capability-quarantine-observation-v1"
SESSION_START_RECEIPT_LIMIT = 24
CURATED_PLUGIN_CACHE_MARKETPLACE = "openai-curated-remote"
CURATED_PLUGIN_CONFIG_MARKETPLACE_ALIASES = (
    CURATED_PLUGIN_CACHE_MARKETPLACE,
    "openai-curated",
)
REMOTE_PLUGIN_INSTALL_RECEIPT = ".codex-remote-plugin-install.json"
REMOTE_PLUGIN_INSTALL_RECEIPT_SCHEMAS = frozenset({1, 2})
REMOTE_PLUGIN_INSTALL_RECEIPT_SCHEMA = 2
REMOTE_PLUGIN_CATALOG_ENTRY_SCHEMA = "remote-plugin-catalog-entry-v1"
REMOTE_PLUGIN_CATALOG_RELATIVE = Path("cache/remote_plugin_catalog")
REMOTE_PLUGIN_CATALOG_MAX_AGE = dt.timedelta(hours=48)
PLUGIN_ORIGIN_ATTESTATION_MODES = frozenset(
    {"plugin-root-v1", "version-package-v2"}
)
PLUGIN_INSTALL_STAGING_PREFIX = "plugin-install-"
PRIMARY_RUNTIME_MARKETPLACE = "openai-primary-runtime"
PRIMARY_RUNTIME_BUNDLE_SCHEMA = "primary-runtime-bundle-origin-v1"
PRIMARY_RUNTIME_BUNDLED_PLUGIN = "plugins/openai-primary-runtime"
PRIMARY_RUNTIME_PACKAGES_RELATIVE = Path(
    "plugins/openai-primary-runtime/plugins"
)
PRIMARY_RUNTIME_DEFAULT_RELATIVE = Path(
    ".cache/codex-runtimes/codex-primary-runtime"
)
PRIMARY_RUNTIME_REQUIRED_PACKAGES = frozenset(
    {
        "documents",
        "pdf",
        "presentations",
        "spreadsheets",
        "template-creator",
    }
)
BUNDLED_MARKETPLACE = "openai-bundled"
BUNDLED_MARKETPLACE_SCHEMA = "bundled-marketplace-origin-v1"
BUNDLED_MARKETPLACE_RELATIVE = Path(
    ".tmp/bundled-marketplaces/openai-bundled"
)
BUNDLED_MARKETPLACE_REQUIRED_ACTIVE_PACKAGES = frozenset(
    {"browser", "chrome", "computer-use"}
)
WORKER_BOM_PROMOTER_STATE_DIRECTORY = ".worker-runtime-bom-promoter"
WORKER_BOM_PROMOTION_RECEIPT_SCHEMA = "capability-worker-runtime-bom-receipt-v2"
WORKER_BOM_PROMOTION_JOURNAL_SCHEMA = "capability-worker-runtime-bom-journal-v2"
WORKER_BOM_PROMOTION_RECEIPT_DOMAIN = (
    b"CAPABILITY-WORKER-RUNTIME-BOM-RECEIPT-v2\0"
)
MUTEX_NAME = r"Local\OpenAI.Codex.CapabilityManifestRecovery.v1"
TRANSACTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
RECOVERABLE_MISMATCHES = frozenset(
    {
        config_fingerprint.SOURCE_HASH_KEY,
        "plugin-cache-inventory",
        index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY,
    }
)
ALLOWED_CONFIG_LEAF_PATHS = frozenset(
    {
        "/mcp_servers/node_repl/runtime/command",
        "/mcp_servers/node_repl/runtime/env/NODE_REPL_NODE_MODULE_DIRS",
        "/mcp_servers/node_repl/runtime/env/NODE_REPL_NODE_PATH",
        "/mcp_servers/node_repl/runtime/env/NODE_REPL_TRUSTED_CODE_PATHS",
        "/mcp_servers/node_repl/runtime/env/NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
        "/mcp_servers/node_repl/runtime/env/BROWSER_USE_CODEX_APP_VERSION",
        "/mcp_servers/node_repl/runtime/env/CODEX_CLI_PATH",
        "/shell_environment_policy/set/NODE_REPL_TRUSTED_CODE_PATHS",
        "/shell_environment_policy/set/NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
    }
)
REQUIRED_APP_CONFIG_PATHS = frozenset(
    path for path in ALLOWED_CONFIG_LEAF_PATHS if path.startswith("/mcp_servers/")
)
APP_RUNTIME_PLUGIN_NAMES = ("browser", "chrome", "computer-use")
SHA256_PATTERN = re.compile(r"^[A-Fa-f0-9]{64}$")
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
REQUIRED_SOURCE_HASH_KEYS = frozenset(
    {
        "catalogue-router.SKILL.md",
        "capability_index.py",
        "routing_policy_validation.py",
        "capability_config_fingerprint.py",
        "capability_index_cli.py",
        "user_prompt_skill_router.py",
        "capability_index_session_start.py",
        "_hook_io.py",
        "query-catalogue.ps1",
        "routing-policy.yaml",
        "routing-policy.schema.json",
        "active-capabilities.schema.json",
        "project-scope-map.json",
        "project-scope-map.schema.json",
        "route-decision.schema.json",
        "capability_manifest_recovery.py",
        "capability-manifest-builder.ps1",
        "authority-receipt.schema.json",
        "worker-runtime-bom.json",
        "worker-runtime-bom.schema.json",
        "promote_worker_runtime_bom.py",
    }
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def _sha256_file(path: Path) -> str:
    try:
        return index._sha256_file(path) if path.is_file() else ""
    except OSError:
        return ""


def _normalized_sha256(value: Any) -> str:
    text = str(value or "").strip()
    return text.lower() if SHA256_PATTERN.fullmatch(text) is not None else ""


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical_json(payload) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_immutable_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.read_bytes() != content:
            raise RuntimeError("immutable authority artifact already exists with different bytes")
        return
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _read_generation_pointer(routing_dir: Path) -> dict[str, Any] | None:
    path = routing_dir / "current-generation.json"
    if not path.is_file():
        return None
    if path.is_symlink():
        raise RuntimeError("authority generation pointer must not be a symlink")
    value = _read_manifest(path)
    generation_id = str(value.get("generation_id") or "").lower()
    manifest_sha256 = str(value.get("manifest_sha256") or "").lower()
    sequence = value.get("sequence")
    previous_generation_id = value.get("previous_generation_id")
    transaction_id = str(value.get("transaction_id") or "")
    expected_manifest_path = (
        Path("generations") / f"generation-{generation_id}.json"
    ).as_posix()
    if (
        value.get("schema_version") != AUTHORITY_POINTER_SCHEMA
        or index.AUTHORITY_GENERATION_ID_PATTERN.fullmatch(generation_id) is None
        or index.SHA256_PATTERN.fullmatch(manifest_sha256) is None
        or not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or sequence < 1
        or value.get("manifest_path") != expected_manifest_path
        or (
            previous_generation_id is not None
            and index.AUTHORITY_GENERATION_ID_PATTERN.fullmatch(
                str(previous_generation_id).lower()
            )
            is None
        )
        or TRANSACTION_ID_PATTERN.fullmatch(transaction_id) is None
    ):
        raise RuntimeError("authority generation pointer is invalid")
    try:
        resolved_root = routing_dir.resolve(strict=True)
        generations_root = (routing_dir / "generations").resolve(strict=True)
        generation_source = routing_dir / expected_manifest_path
        generation_path = generation_source.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("authority generation artifact is unavailable") from exc
    if (
        (routing_dir / "generations").is_symlink()
        or generation_source.is_symlink()
        or generation_path.parent != generations_root
        or not generation_path.is_relative_to(resolved_root)
        or not generation_path.is_file()
    ):
        raise RuntimeError("authority generation artifact path is invalid")
    generation_bytes = generation_path.read_bytes()
    if hashlib.sha256(generation_bytes).hexdigest() != manifest_sha256:
        raise RuntimeError("authority generation artifact hash mismatch")
    generation_manifest = _read_manifest(generation_path)
    generation = generation_manifest.get("authority_generation")
    if (
        not isinstance(generation, dict)
        or str(generation.get("id") or "").lower() != generation_id
        or index.authority_generation_id(generation) != generation_id
        or generation.get("sequence") != sequence
        or generation.get("previous_id") != previous_generation_id
        or generation.get("transaction_id") != transaction_id
        or generation_manifest.get("snapshot_id")
        != f"authority-generation:{generation_id}"
    ):
        raise RuntimeError("authority generation artifact identity mismatch")
    return value


def _generation_context(routing_dir: Path) -> tuple[str | None, int]:
    pointer = _read_generation_pointer(routing_dir)
    if pointer is None:
        return None, 1
    return str(pointer["generation_id"]), int(pointer["sequence"]) + 1


def _committed_generation_matches_transaction(
    routing_dir: Path,
    *,
    begin: dict[str, Any],
    transaction_id: str,
    terminal_generation_id: str = "",
) -> tuple[bool, str]:
    """Prove a replayed commit still names one installed, fresh generation."""

    try:
        pointer = _read_generation_pointer(routing_dir)
        if pointer is None or pointer.get("transaction_id") != transaction_id:
            return False, ""
        generation_id = str(pointer.get("generation_id") or "")
        if terminal_generation_id and terminal_generation_id != generation_id:
            return False, ""
        if pointer.get("previous_generation_id") != begin.get(
            "previous_generation_id"
        ):
            return False, ""
        generation_path = routing_dir / str(pointer["manifest_path"])
        generation_bytes = _stable_regular_bytes(
            generation_path, "committed authority generation"
        )
        if hashlib.sha256(generation_bytes).hexdigest() != str(
            pointer.get("manifest_sha256") or ""
        ).lower():
            return False, ""
        manifest = json.loads(generation_bytes.decode("utf-8"))
        receipt = manifest.get("authority_receipt")
        generation = manifest.get("authority_generation")
        if (
            not isinstance(receipt, dict)
            or _normalized_sha256(receipt.get("snapshot_sha256"))
            != begin.get("expected_authority_snapshot_sha256")
            or _normalized_sha256(pointer.get("manifest_sha256"))
            != begin.get("expected_candidate_sha256")
            or not isinstance(generation, dict)
            or generation.get("promotion_reason") != "operator_rebaseline"
        ):
            return False, ""
        state = index.load_active_capabilities(generation_path)
        if (
            state.get("freshness_status") != "fresh"
            or state.get("source_hashes_verified") is not True
            or state.get("generation_pointer_status") not in {"legacy", "current"}
        ):
            return False, ""
        try:
            _repair_manifest_compatibility_copy(
                routing_dir / "active-capabilities.json", generation_bytes
            )
        except OSError:
            pass
        return True, generation_id
    except (
        OSError,
        RuntimeError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
        index.CapabilityDataError,
    ):
        return False, ""


def _transaction_path(
    routing_dir: Path, transaction_id: str, phase: str
) -> Path:
    if TRANSACTION_ID_PATTERN.fullmatch(transaction_id) is None:
        raise ValueError("authority transaction identifier is invalid")
    if phase not in {"begin", "terminal"}:
        raise ValueError("authority transaction phase is invalid")
    return routing_dir / "authority-transactions" / f"{transaction_id}.{phase}.json"


def _write_idempotent_transaction_record(
    path: Path, payload: dict[str, Any]
) -> Path:
    """Write one immutable receipt or accept an exact stable-field replay."""

    if path.is_file():
        existing = _read_manifest(path)
        expected_stable = {
            key: value for key, value in payload.items() if key != "recorded_at"
        }
        existing_stable = {
            key: value for key, value in existing.items() if key != "recorded_at"
        }
        if existing_stable != expected_stable:
            raise RuntimeError(
                "authority transaction replay conflicts with immutable receipt"
            )
        return path
    _write_immutable_bytes(path, (_canonical_json(payload) + "\n").encode("utf-8"))
    return path


def write_authority_transaction_begin(
    routing_dir: Path,
    *,
    transaction_id: str,
    lane: str,
    previous_generation_id: str | None,
    expected_authority_snapshot_sha256: str,
    expected_candidate_sha256: str = "",
    authorization_id: str = "",
) -> Path:
    if lane not in {
        "coherent_app_update",
        "compound_app_primary_runtime_update",
        "primary_runtime_bundle_update",
        "worker_runtime_bom_update",
        "scoped_config_update",
        "updater_bound_plugin_update",
        "operator_rebaseline",
    }:
        raise ValueError("authority transaction lane is invalid")
    expected_authority = _normalized_sha256(expected_authority_snapshot_sha256)
    expected_candidate = _normalized_sha256(expected_candidate_sha256)
    if not expected_authority:
        raise ValueError("expected authority snapshot SHA-256 is invalid")
    if lane == "operator_rebaseline" and not expected_candidate:
        raise ValueError("expected operator candidate SHA-256 is invalid")
    if lane != "operator_rebaseline" and expected_candidate_sha256:
        raise ValueError("automatic authority lanes cannot bind an operator candidate")
    path = _transaction_path(routing_dir, transaction_id, "begin")
    payload = {
        "schema_version": AUTHORITY_TRANSACTION_SCHEMA,
        "phase": "begin",
        "transaction_id": transaction_id,
        "lane": lane,
        "previous_generation_id": previous_generation_id,
        "expected_authority_snapshot_sha256": expected_authority,
        "expected_candidate_sha256": expected_candidate,
        "authorization_sha256": (
            hashlib.sha256(authorization_id.encode("utf-8")).hexdigest()
            if authorization_id
            else ""
        ),
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    return _write_idempotent_transaction_record(path, payload)


def write_authority_transaction_terminal(
    routing_dir: Path,
    *,
    transaction_id: str,
    lane: str,
    status: str,
    reason_code: str,
    generation_id: str = "",
    expected_authority_snapshot_sha256: str = "",
    expected_candidate_sha256: str = "",
    observed_authority_snapshot_sha256: str = "",
    observed_candidate_sha256: str = "",
) -> Path:
    if status not in {"committed", "denied", "error"}:
        raise ValueError("authority transaction terminal status is invalid")
    expected_authority = _normalized_sha256(expected_authority_snapshot_sha256)
    expected_candidate = _normalized_sha256(expected_candidate_sha256)
    observed_authority = _normalized_sha256(observed_authority_snapshot_sha256)
    observed_candidate = _normalized_sha256(observed_candidate_sha256)
    if lane == "operator_rebaseline":
        if not expected_authority or not expected_candidate:
            raise ValueError("operator terminal is missing reviewed CAS hashes")
        if observed_authority_snapshot_sha256 and not observed_authority:
            raise ValueError("observed authority snapshot SHA-256 is invalid")
        if observed_candidate_sha256 and not observed_candidate:
            raise ValueError("observed candidate SHA-256 is invalid")
        if status == "committed" and (
            observed_authority != expected_authority
            or observed_candidate != expected_candidate
        ):
            raise ValueError("committed operator terminal violates reviewed CAS hashes")
    elif any(
        (
            expected_authority_snapshot_sha256,
            expected_candidate_sha256,
            observed_authority_snapshot_sha256,
            observed_candidate_sha256,
        )
    ):
        raise ValueError("automatic authority terminal cannot bind operator CAS hashes")
    path = _transaction_path(routing_dir, transaction_id, "terminal")
    payload = {
        "schema_version": AUTHORITY_TRANSACTION_SCHEMA,
        "phase": "terminal",
        "transaction_id": transaction_id,
        "lane": lane,
        "status": status,
        "reason_code": str(reason_code)[:96],
        "generation_id": generation_id,
        "expected_authority_snapshot_sha256": expected_authority,
        "expected_candidate_sha256": expected_candidate,
        "observed_authority_snapshot_sha256": observed_authority,
        "observed_candidate_sha256": observed_candidate,
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    return _write_idempotent_transaction_record(path, payload)


def replay_operator_transaction(
    routing_dir: Path,
    *,
    transaction_id: str,
    authorization_id: str,
    expected_authority_snapshot_sha256: str,
    expected_candidate_sha256: str,
) -> dict[str, Any] | None:
    """Return a durable prior outcome or close a crash-interrupted transaction."""

    begin_path = _transaction_path(routing_dir, transaction_id, "begin")
    terminal_path = _transaction_path(routing_dir, transaction_id, "terminal")
    if not begin_path.is_file():
        return None
    try:
        begin = _read_manifest(begin_path)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return {
            "status": "error",
            "reason_code": "AUTHORITY_TRANSACTION_BEGIN_INVALID",
            "transaction_id": transaction_id,
            "replayed": True,
        }
    authorization_sha256 = hashlib.sha256(
        authorization_id.encode("utf-8")
    ).hexdigest()
    expected_authority = _normalized_sha256(expected_authority_snapshot_sha256)
    expected_candidate = _normalized_sha256(expected_candidate_sha256)
    if (
        begin.get("schema_version") != AUTHORITY_TRANSACTION_SCHEMA
        or begin.get("phase") != "begin"
        or begin.get("transaction_id") != transaction_id
        or begin.get("lane") != "operator_rebaseline"
        or begin.get("authorization_sha256") != authorization_sha256
        or begin.get("expected_authority_snapshot_sha256") != expected_authority
        or begin.get("expected_candidate_sha256") != expected_candidate
    ):
        return {
            "status": "denied",
            "reason_code": "AUTHORITY_TRANSACTION_REPLAY_CONFLICT",
            "transaction_id": transaction_id,
            "replayed": True,
        }
    if terminal_path.is_file():
        try:
            terminal = _read_manifest(terminal_path)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            return {
                "status": "error",
                "reason_code": "AUTHORITY_TRANSACTION_TERMINAL_INVALID",
                "transaction_id": transaction_id,
                "replayed": True,
            }
        if (
            terminal.get("schema_version") != AUTHORITY_TRANSACTION_SCHEMA
            or terminal.get("phase") != "terminal"
            or terminal.get("transaction_id") != transaction_id
            or terminal.get("lane") != "operator_rebaseline"
            or terminal.get("expected_authority_snapshot_sha256")
            != expected_authority
            or terminal.get("expected_candidate_sha256") != expected_candidate
        ):
            return {
                "status": "denied",
                "reason_code": "AUTHORITY_TRANSACTION_REPLAY_CONFLICT",
                "transaction_id": transaction_id,
                "replayed": True,
            }
        terminal_status = str(terminal.get("status") or "error")
        if terminal_status not in {"committed", "denied", "error"}:
            return {
                "status": "error",
                "reason_code": "AUTHORITY_TRANSACTION_TERMINAL_INVALID",
                "transaction_id": transaction_id,
                "replayed": True,
            }
        reconciled_commit, reconciled_generation_id = (
            _committed_generation_matches_transaction(
                routing_dir,
                begin=begin,
                transaction_id=transaction_id,
                terminal_generation_id=str(terminal.get("generation_id") or ""),
            )
        )
        if terminal_status != "committed" and reconciled_commit:
            return {
                "status": "rebuilt",
                "reason_code": "OPERATOR_REBASELINE_COMMIT_RECONCILED",
                "transaction_id": transaction_id,
                "generation_id": reconciled_generation_id,
                "replayed": True,
                "recorded_terminal_status": terminal_status,
            }
        if terminal_status == "committed":
            if (
                terminal.get("observed_authority_snapshot_sha256")
                != expected_authority
                or terminal.get("observed_candidate_sha256") != expected_candidate
            ):
                return {
                    "status": "denied",
                    "reason_code": "AUTHORITY_TRANSACTION_REPLAY_CONFLICT",
                    "transaction_id": transaction_id,
                    "replayed": True,
                }
            valid_commit, generation_id = reconciled_commit, reconciled_generation_id
            if not valid_commit:
                return {
                    "status": "error",
                    "reason_code": "AUTHORITY_TRANSACTION_COMMIT_UNPROVEN",
                    "transaction_id": transaction_id,
                    "replayed": True,
                }
        else:
            generation_id = str(terminal.get("generation_id") or "")
        return {
            "status": "rebuilt" if terminal_status == "committed" else terminal_status,
            "reason_code": str(terminal.get("reason_code") or ""),
            "transaction_id": transaction_id,
            "generation_id": generation_id,
            "replayed": True,
        }
    try:
        pointer = _read_generation_pointer(routing_dir)
    except (OSError, RuntimeError, ValueError):
        pointer = None
    if pointer is not None and pointer.get("transaction_id") == transaction_id:
        valid_commit, generation_id = _committed_generation_matches_transaction(
            routing_dir,
            begin=begin,
            transaction_id=transaction_id,
        )
        if not valid_commit:
            write_authority_transaction_terminal(
                routing_dir,
                transaction_id=transaction_id,
                lane="operator_rebaseline",
                status="error",
                reason_code="AUTHORITY_TRANSACTION_COMMIT_UNPROVEN",
                expected_authority_snapshot_sha256=expected_authority,
                expected_candidate_sha256=expected_candidate,
                observed_authority_snapshot_sha256=expected_authority,
                observed_candidate_sha256=expected_candidate,
            )
            return {
                "status": "error",
                "reason_code": "AUTHORITY_TRANSACTION_COMMIT_UNPROVEN",
                "transaction_id": transaction_id,
                "replayed": True,
            }
        write_authority_transaction_terminal(
            routing_dir,
            transaction_id=transaction_id,
            lane="operator_rebaseline",
            status="committed",
            reason_code="OPERATOR_REBASELINE_COMMIT_RECOVERED",
            generation_id=generation_id,
            expected_authority_snapshot_sha256=expected_authority,
            expected_candidate_sha256=expected_candidate,
            observed_authority_snapshot_sha256=expected_authority,
            observed_candidate_sha256=expected_candidate,
        )
        return {
            "status": "rebuilt",
            "reason_code": "OPERATOR_REBASELINE_COMMIT_RECOVERED",
            "transaction_id": transaction_id,
            "generation_id": generation_id,
            "replayed": True,
        }
    write_authority_transaction_terminal(
        routing_dir,
        transaction_id=transaction_id,
        lane="operator_rebaseline",
        status="error",
        reason_code="AUTHORITY_TRANSACTION_INTERRUPTED_BEFORE_COMMIT",
        expected_authority_snapshot_sha256=expected_authority,
        expected_candidate_sha256=expected_candidate,
    )
    return {
        "status": "error",
        "reason_code": "AUTHORITY_TRANSACTION_INTERRUPTED_BEFORE_COMMIT",
        "transaction_id": transaction_id,
        "replayed": True,
    }


def promote_manifest_generation(
    candidate: Path,
    target: Path,
    *,
    transaction_id: str,
    expected_previous_generation_id: str | None,
    expected_candidate_sha256: str = "",
) -> dict[str, Any]:
    """Promote one validated immutable generation and commit via pointer swap."""

    try:
        content = candidate.read_bytes()
        manifest = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("candidate manifest is unavailable or invalid") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("candidate manifest root is invalid")
    reviewed_candidate = _normalized_sha256(expected_candidate_sha256)
    manifest_sha256 = hashlib.sha256(content).hexdigest()
    if expected_candidate_sha256 and not reviewed_candidate:
        raise RuntimeError("candidate compare-and-swap digest is invalid")
    if reviewed_candidate and manifest_sha256 != reviewed_candidate:
        raise RuntimeError("candidate compare-and-swap precondition failed")
    generation = manifest.get("authority_generation")
    if not isinstance(generation, dict):
        raise RuntimeError("candidate has no authority generation")
    generation_id = str(generation.get("id") or "").lower()
    if (
        index.AUTHORITY_GENERATION_ID_PATTERN.fullmatch(generation_id) is None
        or index.authority_generation_id(generation) != generation_id
        or generation.get("transaction_id") != transaction_id
        or str(manifest.get("snapshot_id") or "")
        != f"authority-generation:{generation_id}"
    ):
        raise RuntimeError("candidate authority generation binding is invalid")
    routing_dir = target.parent
    current_pointer = _read_generation_pointer(routing_dir)
    current_generation_id = (
        str(current_pointer["generation_id"]) if current_pointer is not None else None
    )
    if (
        current_pointer is not None
        and current_generation_id == generation_id
        and current_pointer.get("transaction_id") == transaction_id
        and current_pointer.get("previous_generation_id")
        == expected_previous_generation_id
    ):
        relative_manifest = Path(str(current_pointer.get("manifest_path") or ""))
        resolved_root = routing_dir.resolve(strict=True)
        replay_path = (resolved_root / relative_manifest).resolve(strict=True)
        if (
            replay_path.is_relative_to(resolved_root)
            and replay_path.is_file()
            and replay_path.read_bytes() == content
            and str(current_pointer.get("manifest_sha256") or "").lower()
            == manifest_sha256
        ):
            return _generation_promotion_result_with_compatibility_repair(
                current_pointer, target, content
            )
        raise RuntimeError("authority generation replay bytes do not match")
    if current_generation_id != expected_previous_generation_id:
        raise RuntimeError("authority generation compare-and-swap precondition failed")
    expected_sequence = 1 if current_pointer is None else int(current_pointer["sequence"]) + 1
    if generation.get("sequence") != expected_sequence or generation.get(
        "previous_id"
    ) != expected_previous_generation_id:
        raise RuntimeError("candidate authority generation sequence is invalid")

    relative_manifest = Path("generations") / f"generation-{generation_id}.json"
    generation_path = routing_dir / relative_manifest
    _write_immutable_bytes(generation_path, content)

    pointer = {
        "schema_version": AUTHORITY_POINTER_SCHEMA,
        "generation_id": generation_id,
        "sequence": expected_sequence,
        "previous_generation_id": expected_previous_generation_id,
        "manifest_path": relative_manifest.as_posix(),
        "manifest_sha256": manifest_sha256,
        "transaction_id": transaction_id,
        "promoted_at": str(generation.get("promoted_at") or ""),
    }
    if _read_generation_pointer(routing_dir) != current_pointer:
        raise RuntimeError("authority generation pointer changed before commit")
    _atomic_write_json(routing_dir / "current-generation.json", pointer)

    # The pointer swap above is the only authority commit point.  This flat file
    # exists solely for pre-generation readers and is deliberately repaired only
    # after commit, so a process death always leaves either the old pointer or the
    # new pointer authoritative.  Exact replay repairs a missing/stale copy.
    return _generation_promotion_result_with_compatibility_repair(
        pointer, target, content
    )


def _repair_manifest_compatibility_copy(target: Path, content: bytes) -> None:
    if target.is_file() and not target.is_symlink() and target.read_bytes() == content:
        return
    compatibility_temporary = target.with_name(
        f"{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
    )
    try:
        compatibility_temporary.write_bytes(content)
        os.replace(compatibility_temporary, target)
    finally:
        compatibility_temporary.unlink(missing_ok=True)


def _generation_promotion_result_with_compatibility_repair(
    pointer: dict[str, Any], target: Path, content: bytes
) -> dict[str, Any]:
    result = dict(pointer)
    try:
        _repair_manifest_compatibility_copy(target, content)
    except OSError as exc:
        # Compatibility bytes are explicitly outside the authority commit.  A
        # failed best-effort copy must never turn an already committed pointer
        # into an error terminal; exact transaction replay retries this repair.
        result["compatibility_copy_status"] = "repair_pending"
        result["repair_warning"] = type(exc).__name__
    else:
        result["compatibility_copy_status"] = "current"
        result["repair_warning"] = ""
    return result


def record_quarantine_observation(
    state: dict[str, Any], *, routing_dir: Path | None = None
) -> Path | None:
    dynamic = state.get("dynamic_authority")
    if not isinstance(dynamic, dict) or dynamic.get("status") != "degraded":
        return None
    digest = str(dynamic.get("assessment_digest") or "").lower()
    if index.SHA256_PATTERN.fullmatch(digest) is None:
        return None
    root = routing_dir or index.ROUTING_DIR
    path = root / "authority-quarantine" / f"observation-{digest}.json"
    if path.is_file():
        return path
    payload = {
        "schema_version": QUARANTINE_OBSERVATION_SCHEMA,
        "assessment_digest": digest,
        "generation_id": str((state.get("authority_generation") or {}).get("id") or ""),
        "observed_inventory_sha256": str(
            dynamic.get("observed_inventory_sha256") or ""
        ).lower(),
        "observed_config_sha256": str(
            dynamic.get("observed_config_sha256") or ""
        ).lower(),
        "worker_runtime_bom_status": str(
            dynamic.get("worker_runtime_bom_status") or "unavailable"
        ),
        "changed_config_leaves": list(dynamic.get("changed_config_leaves", [])),
        "changed_packages": list(dynamic.get("changed_packages", [])),
        "quarantined_packages": list(dynamic.get("quarantined_packages", [])),
        "quarantined_capability_ids": list(
            dynamic.get("quarantined_capability_ids", [])
        ),
        "reason_code": str(dynamic.get("reason_code") or "")[:96],
        "first_observed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    _write_immutable_bytes(path, (_canonical_json(payload) + "\n").encode("utf-8"))
    return path


def authority_snapshot_digest(receipt: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in receipt.items()
        if key not in {"snapshot_sha256", "newest_authority_mtime_ns"}
    }
    catalog_entries = payload.get("remote_plugin_catalog_entries")
    if isinstance(catalog_entries, dict):
        payload["remote_plugin_catalog_entries"] = {
            package: {
                key: item
                for key, item in binding.items()
                if key != "fetched_at"
            }
            if isinstance(binding, dict)
            else binding
            for package, binding in catalog_entries.items()
        }
    return _sha256_text(_canonical_json(payload))


def _operator_authority_receipts_equivalent(
    reviewed: Any, current: Any
) -> bool:
    """Ignore only catalog fetch time while preserving full authority equality."""

    if not isinstance(reviewed, dict) or not isinstance(current, dict):
        return False

    def projection(receipt: dict[str, Any]) -> dict[str, Any]:
        value = dict(receipt)
        catalog_entries = value.get("remote_plugin_catalog_entries")
        if isinstance(catalog_entries, dict):
            value["remote_plugin_catalog_entries"] = {
                package: {
                    key: item
                    for key, item in binding.items()
                    if key != "fetched_at"
                }
                if isinstance(binding, dict)
                else binding
                for package, binding in catalog_entries.items()
            }
        return value

    for receipt in (reviewed, current):
        supplied = _normalized_sha256(receipt.get("snapshot_sha256"))
        recomputed = _normalized_sha256(authority_snapshot_digest(receipt))
        if not supplied or supplied != recomputed:
            return False
    return projection(reviewed) == projection(current)


def _valid_hash_map(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str)
        and isinstance(digest, str)
        and SHA256_PATTERN.fullmatch(digest) is not None
        for key, digest in value.items()
    )


def _valid_config_capability_surfaces(
    value: Any, config_leaf_hashes: dict[str, str]
) -> bool:
    if not isinstance(value, dict) or set(value) != set(config_leaf_hashes):
        return False
    for pointer, binding in value.items():
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            return False
        if not isinstance(binding, dict):
            return False
        required = {
            "change_class",
            "control_kind",
            "control_key",
            "capability_ids",
            "required_capability_ids",
        }
        allowed = required | {"enabled"}
        if set(binding) != required and set(binding) != allowed:
            return False
        if binding.get("change_class") not in {
            "availability_toggle",
            "runtime_identity",
        }:
            return False
        if binding.get("control_kind") not in {
            "app",
            "app_tool",
            "app_runtime",
            "global_runtime",
            "mcp",
            "mcp_runtime",
            "plugin",
            "plugin_mcp",
            "skill",
        }:
            return False
        if not isinstance(binding.get("control_key"), str) or not binding[
            "control_key"
        ]:
            return False
        if "enabled" in binding:
            if (
                binding["change_class"] != "availability_toggle"
                or not isinstance(binding["enabled"], bool)
            ):
                return False
        elif binding["change_class"] == "availability_toggle":
            return False
        for field in ("capability_ids", "required_capability_ids"):
            identifiers = binding.get(field)
            if (
                not isinstance(identifiers, list)
                or not identifiers
                or len(identifiers) != len(set(identifiers))
                or any(not isinstance(item, str) or not item for item in identifiers)
            ):
                return False
        if not set(binding["required_capability_ids"]).issubset(
            binding["capability_ids"]
        ):
            return False
    return True


def _valid_plugin_capability_surfaces(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for package, capabilities in value.items():
        if not isinstance(package, str) or not package:
            return False
        if not isinstance(capabilities, list) or not capabilities:
            return False
        seen: set[tuple[str, str]] = set()
        for capability in capabilities:
            if not isinstance(capability, dict) or set(capability) != {"id", "kind"}:
                return False
            identifier = capability.get("id")
            kind = capability.get("kind")
            if (
                not isinstance(identifier, str)
                or not identifier
                or kind not in {"plugin", "skill", "tool-family", "mcp"}
                or (kind, identifier) in seen
            ):
                return False
            seen.add((kind, identifier))
    return True


def _valid_plugin_package_manifests(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for package, identity in value.items():
        if _normalized_plugin_package(package) is None or not isinstance(identity, dict):
            return False
        if set(identity) != {"name", "version", "manifest_sha256"}:
            return False
        if not isinstance(identity.get("name"), str) or not identity["name"]:
            return False
        if not isinstance(identity.get("version"), str) or not identity["version"]:
            return False
        if not isinstance(identity.get("manifest_sha256"), str) or not SHA256_PATTERN.fullmatch(
            identity["manifest_sha256"]
        ):
            return False
    return True


def _valid_plugin_package_origins(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for package, origin in value.items():
        normalized = _normalized_plugin_package(package)
        if normalized is None or not normalized.startswith(
            CURATED_PLUGIN_CACHE_MARKETPLACE + "/"
        ):
            return False
        if not isinstance(origin, dict) or set(origin) != {
            "attestation_mode",
            "receipt_schema_version",
            "remote_plugin_id",
            "marketplace",
            "plugin_name",
            "plugin_version",
            "plugin_manifest_sha256",
            "package_sha256",
            "install_receipt_sha256",
        }:
            return False
        marketplace, plugin_name, plugin_version = normalized.split("/")
        if (
            origin.get("receipt_schema_version")
            not in REMOTE_PLUGIN_INSTALL_RECEIPT_SCHEMAS
            or origin.get("attestation_mode") not in PLUGIN_ORIGIN_ATTESTATION_MODES
            or (
                origin.get("receipt_schema_version") == 1
                and origin.get("attestation_mode") != "plugin-root-v1"
            )
            or (
                origin.get("receipt_schema_version") == 2
                and origin.get("attestation_mode") != "version-package-v2"
            )
            or origin.get("marketplace") != marketplace
            or not isinstance(origin.get("plugin_name"), str)
            or origin["plugin_name"].casefold() != plugin_name
            or origin.get("plugin_version") != plugin_version
        ):
            return False
        remote_plugin_id = origin.get("remote_plugin_id")
        if (
            not isinstance(remote_plugin_id, str)
            or not re.fullmatch(r"[-A-Za-z0-9._:~]{8,256}", remote_plugin_id)
        ):
            return False
        for name in (
            "plugin_manifest_sha256",
            "package_sha256",
            "install_receipt_sha256",
        ):
            digest = origin.get(name)
            if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
                return False
    return True


def _catalog_entry_projection(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "remote_plugin_id": value.get("remote_plugin_id"),
        "plugin_name": value.get("plugin_name"),
        "plugin_version": value.get("plugin_version"),
        "status": value.get("status"),
        "installation_policy": value.get("installation_policy"),
        "apps": value.get("apps"),
        "local_apps": value.get("local_apps"),
        "skill_names": value.get("skill_names"),
    }


def _parse_utc_timestamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def _canonical_app_identity(value: str) -> str:
    match = re.fullmatch(
        r"(?:asdk_app_|connector_)([a-f0-9]{32})", value.casefold()
    )
    return match.group(1) if match else value.casefold()


def _valid_remote_plugin_catalog_entries(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for package, binding in value.items():
        normalized = _normalized_plugin_package(package)
        if (
            normalized != package
            or not package.startswith(CURATED_PLUGIN_CACHE_MARKETPLACE + "/")
            or not isinstance(binding, dict)
            or set(binding) != {
                "schema_version",
                "remote_plugin_id",
                "plugin_name",
                "plugin_version",
                "status",
                "installation_policy",
                "apps",
                "local_apps",
                "skill_names",
                "entry_projection_sha256",
                "fetched_at",
            }
        ):
            return False
        _, plugin_name, plugin_version = normalized.split("/")
        remote_plugin_id = binding.get("remote_plugin_id")
        apps = binding.get("apps")
        local_apps = binding.get("local_apps")
        skill_names = binding.get("skill_names")
        if (
            binding.get("schema_version") != REMOTE_PLUGIN_CATALOG_ENTRY_SCHEMA
            or not isinstance(remote_plugin_id, str)
            or re.fullmatch(r"[-A-Za-z0-9._:~]{8,256}", remote_plugin_id) is None
            or binding.get("plugin_name") != plugin_name
            or binding.get("plugin_version") != plugin_version
            or binding.get("status") != "AVAILABLE"
            or binding.get("installation_policy")
            not in {"AVAILABLE", "INSTALLED_BY_DEFAULT"}
            or not isinstance(apps, dict)
            or list(apps) != sorted(apps)
            or any(
                not isinstance(name, str)
                or not name
                or not isinstance(app_id, str)
                or not app_id
                for name, app_id in apps.items()
            )
            or not isinstance(local_apps, dict)
            or list(local_apps) != sorted(local_apps)
            or set(local_apps) != set(apps)
            or any(
                not isinstance(app_id, str)
                or not app_id
                or _canonical_app_identity(app_id)
                != _canonical_app_identity(apps[name])
                for name, app_id in local_apps.items()
            )
            or not isinstance(skill_names, list)
            or skill_names != sorted(set(skill_names))
            or any(not isinstance(item, str) or not item for item in skill_names)
            or _parse_utc_timestamp(binding.get("fetched_at")) is None
            or not isinstance(binding.get("entry_projection_sha256"), str)
            or binding["entry_projection_sha256"]
            != _sha256_text(_canonical_json(_catalog_entry_projection(binding)))
        ):
            return False
    return True


def _catalog_entries_authority(value: Any) -> dict[str, str] | None:
    if not _valid_remote_plugin_catalog_entries(value):
        return None
    return {
        package: binding["entry_projection_sha256"]
        for package, binding in sorted(value.items())
    }


def _valid_bounded_authority_file_hashes(value: Any) -> bool:
    if not _valid_hash_map(value) or ".codex-plugin/plugin.json" not in value:
        return False
    for relative in value:
        normalized = relative.casefold()
        if (
            relative != normalized
            or "\\" in relative
            or relative.startswith("/")
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or (
                relative
                not in {
                    ".codex-plugin/plugin.json",
                    ".app.json",
                    ".mcp.json",
                }
                and re.fullmatch(r"skills/[a-z0-9._-]+/skill\.md", relative)
                is None
            )
        ):
            return False
    return True


def _bundled_marketplace_projection(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "materialization_version": value.get("materialization_version"),
        "app_version": value.get("app_version"),
        "bundle_id": value.get("bundle_id"),
        "materialization_key_sha256": value.get("materialization_key_sha256"),
        "marketplace_projection_sha256": value.get(
            "marketplace_projection_sha256"
        ),
        "packages": value.get("packages"),
    }


def _valid_bundled_marketplace_origin(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "materialization_version",
        "app_version",
        "bundle_id",
        "materialization_key_sha256",
        "marketplace_projection_sha256",
        "authority_projection_sha256",
        "packages",
    }:
        return False
    materialization_version = value.get("materialization_version")
    app_version = value.get("app_version")
    bundle_id = value.get("bundle_id")
    packages = value.get("packages")
    try:
        normalized_bundle_id = str(uuid.UUID(str(bundle_id)))
    except (ValueError, AttributeError):
        return False
    if (
        value.get("schema_version") != BUNDLED_MARKETPLACE_SCHEMA
        or materialization_version != 1
        or not isinstance(app_version, str)
        or _semver_key(app_version) is None
        or not isinstance(bundle_id, str)
        or bundle_id != normalized_bundle_id
        or any(
            not isinstance(value.get(name), str)
            or SHA256_PATTERN.fullmatch(value[name]) is None
            for name in (
                "materialization_key_sha256",
                "marketplace_projection_sha256",
                "authority_projection_sha256",
            )
        )
        or not isinstance(packages, dict)
        or not BUNDLED_MARKETPLACE_REQUIRED_ACTIVE_PACKAGES.issubset(packages)
    ):
        return False

    for package_key, package in packages.items():
        if (
            not isinstance(package_key, str)
            or package_key != package_key.casefold()
            or re.fullmatch(r"[a-z0-9._-]+", package_key) is None
            or not isinstance(package, dict)
            or set(package) != {
                "plugin_name",
                "plugin_version",
                "bundle_package",
                "cache_packages",
                "authority_file_hashes",
                "bundle_authority_sha256",
                "cache_authority_sha256s",
            }
        ):
            return False
        plugin_version = package.get("plugin_version")
        cache_packages = package.get("cache_packages")
        authority_files = package.get("authority_file_hashes")
        cache_digests = package.get("cache_authority_sha256s")
        if (
            package.get("plugin_name") != package_key
            or not isinstance(plugin_version, str)
            or _semver_key(plugin_version) is None
            or package.get("bundle_package") != f"plugins/{package_key}"
            or not isinstance(cache_packages, list)
            or cache_packages != sorted(set(cache_packages))
            or any(not isinstance(item, str) for item in cache_packages)
            or not _valid_bounded_authority_file_hashes(authority_files)
            or not isinstance(cache_digests, dict)
            or set(cache_digests) != set(cache_packages)
        ):
            return False
        authority_digest = _sha256_text(_canonical_json(authority_files))
        if package.get("bundle_authority_sha256") != authority_digest:
            return False
        for cache_package in cache_packages:
            normalized = _normalized_plugin_package(cache_package)
            if normalized != cache_package:
                return False
            marketplace, plugin_name, cache_version = normalized.split("/")
            if (
                marketplace != BUNDLED_MARKETPLACE
                or plugin_name != package_key
                or (
                    cache_version != plugin_version.casefold()
                    and not (package_key == "chrome" and cache_version == "latest")
                )
                or cache_digests.get(cache_package) != authority_digest
            ):
                return False

    for package_key in BUNDLED_MARKETPLACE_REQUIRED_ACTIVE_PACKAGES:
        package = packages[package_key]
        expected_cache_packages = [
            f"{BUNDLED_MARKETPLACE}/{package_key}/{app_version}".casefold()
        ]
        if package_key == "chrome":
            expected_cache_packages.append(
                f"{BUNDLED_MARKETPLACE}/chrome/latest"
            )
        if (
            package["plugin_version"] != app_version
            or package["cache_packages"] != sorted(expected_cache_packages)
        ):
            return False
    return value["authority_projection_sha256"] == _sha256_text(
        _canonical_json(_bundled_marketplace_projection(value))
    )


def _valid_primary_runtime_bundle_origin(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "bundle_format_version",
        "bundle_version",
        "runtime_json_sha256",
        "authority_projection_sha256",
        "packages",
    }:
        return False
    bundle_format_version = value.get("bundle_format_version")
    bundle_version = value.get("bundle_version")
    packages = value.get("packages")
    if (
        value.get("schema_version") != PRIMARY_RUNTIME_BUNDLE_SCHEMA
        or not isinstance(bundle_format_version, int)
        or isinstance(bundle_format_version, bool)
        or bundle_format_version < 1
        or not isinstance(bundle_version, str)
        or _semver_key(bundle_version) is None
        or not isinstance(value.get("runtime_json_sha256"), str)
        or SHA256_PATTERN.fullmatch(value["runtime_json_sha256"]) is None
        or not isinstance(value.get("authority_projection_sha256"), str)
        or SHA256_PATTERN.fullmatch(value["authority_projection_sha256"]) is None
        or not isinstance(packages, dict)
        or not packages
        or set(packages) != PRIMARY_RUNTIME_REQUIRED_PACKAGES
    ):
        return False
    for package_key, package in packages.items():
        if (
            not isinstance(package_key, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]+", package_key)
            or package_key != package_key.casefold()
            or not isinstance(package, dict)
            or set(package) != {
                "plugin_name",
                "plugin_version",
                "bundle_package",
                "cache_package",
                "authority_file_hashes",
                "bundle_authority_sha256",
                "cache_authority_sha256",
            }
        ):
            return False
        plugin_name = package.get("plugin_name")
        plugin_version = package.get("plugin_version")
        authority_files = package.get("authority_file_hashes")
        if (
            not isinstance(plugin_name, str)
            or plugin_name.casefold() != package_key.casefold()
            or not isinstance(plugin_version, str)
            or plugin_version != bundle_version
            or package.get("bundle_package")
            != f"{PRIMARY_RUNTIME_PACKAGES_RELATIVE.as_posix()}/{package_key}"
            or package.get("cache_package")
            != f"{PRIMARY_RUNTIME_MARKETPLACE}/{package_key}/{bundle_version}".casefold()
            or not _valid_bounded_authority_file_hashes(authority_files)
        ):
            return False
        authority_digest = _sha256_text(_canonical_json(authority_files))
        if (
            package.get("bundle_authority_sha256") != authority_digest
            or package.get("cache_authority_sha256") != authority_digest
        ):
            return False
    return value["authority_projection_sha256"] == _sha256_text(
        _canonical_json(packages)
    )


def _normalized_plugin_package(value: str) -> str | None:
    normalized = value.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    if (
        len(parts) != 3
        or any(not part or part in {".", ".."} for part in parts)
    ):
        return None
    return normalized.casefold()


def _plugin_cache_rows_match_surfaces(
    rows: dict[str, str], surfaces: dict[str, list[dict[str, str]]]
) -> bool:
    """Prove every cache row belongs to exactly one declared package surface."""

    roots: set[str] = set()
    parsed_rows: list[tuple[str, str]] = []
    for key in rows:
        try:
            kind, relative = key.split("\t", 1)
        except ValueError:
            return False
        if kind not in {"ROOT", "FILE"}:
            return False
        normalized_relative = relative.replace("\\", "/").strip("/").casefold()
        if not normalized_relative:
            return False
        parsed_rows.append((kind, normalized_relative))
        if kind == "ROOT":
            package = _normalized_plugin_package(relative)
            if package is None or package in roots:
                return False
            roots.add(package)

    normalized_surfaces: set[str] = set()
    for package in surfaces:
        normalized = _normalized_plugin_package(package)
        if normalized is None or normalized in normalized_surfaces:
            return False
        normalized_surfaces.add(normalized)
    if roots != normalized_surfaces:
        return False

    for kind, relative in parsed_rows:
        owners = {
            package
            for package in roots
            if relative == package or relative.startswith(package + "/")
        }
        if len(owners) != 1:
            return False
        if kind == "ROOT" and relative not in roots:
            return False
    return True


def _valid_receipt(receipt: Any) -> bool:
    if not isinstance(receipt, dict) or receipt.get("schema_version") != RECEIPT_SCHEMA:
        return False
    for name in ("config_projection_sha256", "plugin_cache_inventory_sha256"):
        if not isinstance(receipt.get(name), str) or not SHA256_PATTERN.fullmatch(
            receipt[name]
        ):
            return False
    if not _valid_hash_map(receipt.get("config_leaf_hashes")):
        return False
    if not _valid_config_capability_surfaces(
        receipt.get("config_capability_surfaces"),
        receipt["config_leaf_hashes"],
    ):
        return False
    if not _valid_hash_map(receipt.get("plugin_cache_row_hashes")):
        return False
    if not _valid_plugin_capability_surfaces(
        receipt.get("plugin_capability_surfaces")
    ):
        return False
    if not _valid_plugin_package_manifests(
        receipt.get("plugin_package_manifests")
    ):
        return False
    if not _valid_plugin_package_origins(receipt.get("plugin_package_origins")):
        return False
    catalog_entries = receipt.get("remote_plugin_catalog_entries")
    if not _valid_remote_plugin_catalog_entries(catalog_entries):
        return False
    bundled_origin = receipt.get("bundled_marketplace_origin")
    if not _valid_bundled_marketplace_origin(bundled_origin):
        return False
    primary_runtime_origin = receipt.get("primary_runtime_bundle_origin")
    if not _valid_primary_runtime_bundle_origin(primary_runtime_origin):
        return False
    surface_packages = set(receipt["plugin_capability_surfaces"])
    if not set(receipt["plugin_package_manifests"]).issubset(surface_packages):
        return False
    if not set(receipt["plugin_package_origins"]).issubset(surface_packages):
        return False
    for package, origin in receipt["plugin_package_origins"].items():
        manifest = receipt["plugin_package_manifests"].get(package)
        if (
            not isinstance(manifest, dict)
            or origin["plugin_name"] != manifest.get("name")
            or origin["plugin_version"] != manifest.get("version")
            or origin["plugin_manifest_sha256"].upper()
            != str(manifest.get("manifest_sha256", "")).upper()
        ):
            return False
    expected_catalog_packages = {
        package
        for package, origin in receipt["plugin_package_origins"].items()
        if origin["receipt_schema_version"] == 1
    }
    if set(catalog_entries) != expected_catalog_packages:
        return False
    for package, binding in catalog_entries.items():
        origin = receipt["plugin_package_origins"].get(package)
        surface = receipt["plugin_capability_surfaces"].get(package)
        if not isinstance(origin, dict) or not isinstance(surface, list):
            return False
        plugin_name = str(binding["plugin_name"])
        local_plugin_ids = {
            str(item["id"])
            for item in surface
            if item["kind"] == "plugin"
        }
        local_skill_names = sorted(
            str(item["id"])[len(f"skill:{plugin_name}:") :]
            for item in surface
            if item["kind"] == "skill"
            and str(item["id"]).startswith(f"skill:{plugin_name}:")
        )
        local_app_ids = {
            str(item["id"])
            for item in surface
            if item["kind"] == "tool-family"
        }
        expected_app_ids = {
            f"tool-family:app:{_capability_safe_name(name)}"
            for name in binding["apps"]
        }
        if (
            binding["remote_plugin_id"] != origin["remote_plugin_id"]
            or plugin_name.casefold() != str(origin["plugin_name"]).casefold()
            or binding["plugin_version"] != origin["plugin_version"]
            or local_plugin_ids != {f"plugin:{plugin_name}"}
            or local_skill_names != binding["skill_names"]
            or local_app_ids != expected_app_ids
            or any(
                item["kind"] == "skill"
                and not str(item["id"]).startswith(f"skill:{plugin_name}:")
                for item in surface
            )
        ):
            return False
    bundled_packages = {
        cache_package: package
        for package in bundled_origin["packages"].values()
        for cache_package in package["cache_packages"]
    }
    observed_bundled_packages = {
        package
        for package in surface_packages
        if package.casefold().startswith(BUNDLED_MARKETPLACE + "/")
    }
    if set(bundled_packages) != observed_bundled_packages:
        return False
    for package, origin in bundled_packages.items():
        manifest = receipt["plugin_package_manifests"].get(package)
        authority_files = origin["authority_file_hashes"]
        expected_shape = {"ROOT\t."} | {
            f"FILE\t{relative}" for relative in authority_files
        }
        if (
            not isinstance(manifest, dict)
            or manifest.get("name") != origin["plugin_name"]
            or manifest.get("version") != origin["plugin_version"]
            or str(manifest.get("manifest_sha256") or "").upper()
            != authority_files[".codex-plugin/plugin.json"].upper()
            or _package_row_suffixes(
                receipt["plugin_cache_row_hashes"], package
            )
            != expected_shape
        ):
            return False
    primary_packages = {
        str(package["cache_package"]): package
        for package in primary_runtime_origin["packages"].values()
    }
    observed_primary_packages = {
        package
        for package in surface_packages
        if package.casefold().startswith(PRIMARY_RUNTIME_MARKETPLACE + "/")
    }
    if set(primary_packages) != observed_primary_packages:
        return False
    for package, origin in primary_packages.items():
        manifest = receipt["plugin_package_manifests"].get(package)
        authority_files = origin["authority_file_hashes"]
        if (
            not isinstance(manifest, dict)
            or manifest.get("name") != origin["plugin_name"]
            or manifest.get("version") != origin["plugin_version"]
            or str(manifest.get("manifest_sha256") or "").upper()
            != authority_files[".codex-plugin/plugin.json"].upper()
        ):
            return False
    if not _plugin_cache_rows_match_surfaces(
        receipt["plugin_cache_row_hashes"],
        receipt["plugin_capability_surfaces"],
    ):
        return False
    required_sources = receipt.get("required_source_hashes")
    if (
        not _valid_hash_map(required_sources)
        or set(required_sources) != REQUIRED_SOURCE_HASH_KEYS
    ):
        return False
    identity = receipt.get("app_identity")
    if not isinstance(identity, dict) or identity.get("coherent") is not True:
        return False
    for name in ("app_version", "runtime_id", "cli_id"):
        if not isinstance(identity.get(name), str) or not identity[name]:
            return False
    for name in (
        "trusted_browser_client_sha256",
        "browser_client_sha256",
        "runtime_executable_sha256",
        "cli_executable_sha256",
    ):
        if not isinstance(identity.get(name), str) or not SHA256_PATTERN.fullmatch(
            identity[name]
        ):
            return False
    if identity["app_version"] != bundled_origin["app_version"]:
        return False
    supplied = receipt.get("snapshot_sha256")
    return (
        isinstance(supplied, str)
        and SHA256_PATTERN.fullmatch(supplied) is not None
        and supplied.upper() == authority_snapshot_digest(receipt)
    )


def _changed_hash_keys(previous: dict[str, str], current: dict[str, str]) -> set[str]:
    return {
        key
        for key in set(previous) | set(current)
        if previous.get(key) != current.get(key)
    }


def _bundled_cache_packages(origin: dict[str, Any]) -> set[str]:
    return {
        str(cache_package)
        for package in origin["packages"].values()
        for cache_package in package["cache_packages"]
    }


def _bundled_cache_roles(package: dict[str, Any]) -> dict[str, str] | None:
    result: dict[str, str] = {}
    for cache_package in package["cache_packages"]:
        normalized = _normalized_plugin_package(cache_package)
        if normalized is None:
            return None
        cache_version = normalized.split("/")[2]
        role = "latest" if cache_version == "latest" else "version"
        if role in result:
            return None
        result[role] = cache_package
    return result


def _receipt_surface(
    receipt: dict[str, Any], package: str
) -> set[tuple[str, str]] | None:
    raw = receipt["plugin_capability_surfaces"].get(package)
    if not isinstance(raw, list):
        return None
    return {(str(item["kind"]), str(item["id"])) for item in raw}


def classify_app_update(
    previous: dict[str, Any], current: dict[str, Any]
) -> tuple[bool, str]:
    """Accept only the exact coherent Codex Desktop app-update cohort."""

    if not _valid_receipt(previous):
        return False, "INVALID_BASELINE_AUTHORITY_RECEIPT"
    if not _valid_receipt(current):
        return False, "INVALID_CURRENT_AUTHORITY_RECEIPT"

    previous_sources = previous.get("required_source_hashes")
    current_sources = current.get("required_source_hashes")
    if previous_sources is not None or current_sources is not None:
        if previous_sources != current_sources:
            return False, "UNRECOGNIZED_SOURCE_DELTA"
    if (
        previous.get("primary_runtime_bundle_origin")
        != current.get("primary_runtime_bundle_origin")
    ):
        return False, "UNRELATED_PRIMARY_RUNTIME_BUNDLE_DELTA"
    if _catalog_entries_authority(
        previous.get("remote_plugin_catalog_entries")
    ) != _catalog_entries_authority(current.get("remote_plugin_catalog_entries")):
        return False, "UNRELATED_REMOTE_PLUGIN_CATALOG_DELTA"

    previous_identity = previous["app_identity"]
    current_identity = current["app_identity"]
    old_version = previous_identity["app_version"]
    new_version = current_identity["app_version"]
    old_app_semver = _semver_key(old_version)
    new_app_semver = _semver_key(new_version)
    if (
        old_app_semver is None
        or new_app_semver is None
        or new_app_semver <= old_app_semver
        or previous_identity["runtime_id"] == current_identity["runtime_id"]
        or previous_identity["cli_id"] == current_identity["cli_id"]
    ):
        return False, "APP_IDENTITY_DID_NOT_ADVANCE"
    if (
        current_identity["trusted_browser_client_sha256"].upper()
        != current_identity["browser_client_sha256"].upper()
    ):
        return False, "APP_IDENTITY_INCOHERENT"

    if previous["plugin_package_origins"] != current["plugin_package_origins"]:
        return False, "UNRELATED_PLUGIN_ORIGIN_DELTA"
    old_bundle = previous["bundled_marketplace_origin"]
    new_bundle = current["bundled_marketplace_origin"]
    if (
        old_bundle["materialization_version"]
        != new_bundle["materialization_version"]
        or old_bundle["marketplace_projection_sha256"]
        != new_bundle["marketplace_projection_sha256"]
        or set(old_bundle["packages"]) != set(new_bundle["packages"])
    ):
        return False, "APP_BUNDLE_COHORT_CHANGED"
    if (
        old_bundle["materialization_key_sha256"]
        == new_bundle["materialization_key_sha256"]
    ):
        return False, "APP_BUNDLE_DID_NOT_ADVANCE"
    if old_bundle["bundle_id"] == new_bundle["bundle_id"]:
        return False, "APP_BUNDLE_ID_DID_NOT_ADVANCE"

    old_cache_packages = _bundled_cache_packages(old_bundle)
    new_cache_packages = _bundled_cache_packages(new_bundle)
    allowed_packages = old_cache_packages | new_cache_packages
    for field, reason in (
        ("plugin_package_manifests", "UNRELATED_PLUGIN_AUTHORITY_DELTA"),
        ("plugin_capability_surfaces", "UNRELATED_PLUGIN_CAPABILITY_DELTA"),
    ):
        previous_values = previous[field]
        current_values = current[field]
        for package in set(previous_values) | set(current_values):
            if package in allowed_packages:
                continue
            if previous_values.get(package) != current_values.get(package):
                return False, reason

    for package_key in sorted(old_bundle["packages"]):
        old_package = old_bundle["packages"][package_key]
        new_package = new_bundle["packages"][package_key]
        old_plugin_version = str(old_package["plugin_version"])
        new_plugin_version = str(new_package["plugin_version"])
        old_roles = _bundled_cache_roles(old_package)
        new_roles = _bundled_cache_roles(new_package)
        if (
            old_package["plugin_name"] != new_package["plugin_name"]
            or old_package["bundle_package"] != new_package["bundle_package"]
            or old_roles is None
            or new_roles is None
            or set(old_roles) != set(new_roles)
        ):
            return False, "APP_BUNDLE_PACKAGE_IDENTITY_CHANGED"
        if package_key in BUNDLED_MARKETPLACE_REQUIRED_ACTIVE_PACKAGES and (
            old_plugin_version != old_version
            or new_plugin_version != new_version
        ):
            return False, "APP_BUNDLE_RUNTIME_COHORT_INCOMPLETE"
        if old_plugin_version == new_plugin_version:
            if old_package != new_package:
                return False, "APP_BUNDLE_VERSION_MUTATED_IN_PLACE"
            for cache_package in old_package["cache_packages"]:
                old_rows = {
                    key: value
                    for key, value in previous["plugin_cache_row_hashes"].items()
                    if _cache_row_belongs_to_package(key, cache_package)
                }
                new_rows = {
                    key: value
                    for key, value in current["plugin_cache_row_hashes"].items()
                    if _cache_row_belongs_to_package(key, cache_package)
                }
                if old_rows != new_rows:
                    return False, "APP_BUNDLE_VERSION_MUTATED_IN_PLACE"
        else:
            old_package_semver = _semver_key(old_plugin_version)
            new_package_semver = _semver_key(new_plugin_version)
            if (
                old_package_semver is None
                or new_package_semver is None
                or new_package_semver <= old_package_semver
            ):
                return False, "APP_BUNDLE_PACKAGE_VERSION_NOT_NEWER"
        for role in sorted(old_roles):
            old_cache_package = old_roles[role]
            new_cache_package = new_roles[role]
            old_surface = _receipt_surface(previous, old_cache_package)
            new_surface = _receipt_surface(current, new_cache_package)
            if (
                old_surface is None
                or new_surface is None
                or old_surface != new_surface
                or ("plugin", f"plugin:{package_key}") not in old_surface
            ):
                return False, "APP_BUNDLE_CAPABILITY_SURFACE_CHANGED"
            if role == "version" and old_plugin_version != new_plugin_version:
                if (
                    old_cache_package in current["plugin_capability_surfaces"]
                    or new_cache_package in previous["plugin_capability_surfaces"]
                ):
                    return False, "APP_BUNDLE_COHORT_DID_NOT_REPLACE"

    config_delta = _changed_hash_keys(
        previous["config_leaf_hashes"], current["config_leaf_hashes"]
    )
    if not config_delta:
        return False, "APP_CONFIG_COHORT_INCOMPLETE"
    if not config_delta.issubset(ALLOWED_CONFIG_LEAF_PATHS):
        return False, "UNRECOGNIZED_CONFIG_DELTA"
    if not REQUIRED_APP_CONFIG_PATHS.issubset(config_delta):
        return False, "APP_CONFIG_COHORT_INCOMPLETE"
    if previous["config_capability_surfaces"] != current[
        "config_capability_surfaces"
    ]:
        return False, "APP_CONFIG_CAPABILITY_SURFACE_CHANGED"

    plugin_delta = _changed_hash_keys(
        previous["plugin_cache_row_hashes"], current["plugin_cache_row_hashes"]
    )
    if not plugin_delta:
        return False, "PLUGIN_COHORT_INCOMPLETE"
    if any(
        not any(
            _cache_row_belongs_to_package(key, package)
            for package in allowed_packages
        )
        for key in plugin_delta
    ):
        return False, "UNRECOGNIZED_PLUGIN_DELTA"
    if previous["plugin_cache_inventory_sha256"] == current[
        "plugin_cache_inventory_sha256"
    ]:
        return False, "PLUGIN_COHORT_INCOMPLETE"
    return True, "RECOGNIZED_CODEX_DESKTOP_UPDATE"


def _semver_key(value: str) -> tuple[int, int, int, tuple[tuple[int, Any], ...]] | None:
    match = SEMVER_PATTERN.fullmatch(value)
    if not match:
        return None
    prerelease = match.group(4)
    if prerelease is None:
        prerelease_key: tuple[tuple[int, Any], ...] = ((2, ""),)
    else:
        parts: list[tuple[int, Any]] = []
        for item in prerelease.split("."):
            if item.isdigit() and len(item) > 1 and item.startswith("0"):
                return None
            parts.append((0, int(item)) if item.isdigit() else (1, item.casefold()))
        prerelease_key = tuple(parts)
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease_key


def _plugin_package_parts(package: str) -> tuple[str, str] | None:
    parts = package.replace("\\", "/").strip("/").split("/")
    if len(parts) != 3 or parts[0].casefold() != CURATED_PLUGIN_CACHE_MARKETPLACE:
        return None
    plugin_name, version = parts[1:]
    if not re.fullmatch(r"[A-Za-z0-9._-]+", plugin_name) or not version:
        return None
    return plugin_name.casefold(), version


def _package_row_suffixes(rows: dict[str, str], package: str) -> set[str]:
    prefix = package.casefold() + "/"
    result: set[str] = set()
    for key in rows:
        try:
            kind, relative = key.split("\t", 1)
        except ValueError:
            continue
        relative_folded = relative.replace("\\", "/").casefold()
        if relative_folded == package.casefold():
            result.add(f"{kind.upper()}\t.")
        elif relative_folded.startswith(prefix):
            result.add(f"{kind.upper()}\t{relative_folded[len(prefix):]}")
    return result


def _plugin_is_enabled(config_leaf_hashes: dict[str, str], plugin_name: str) -> bool:
    """Use the exact remote marketplace control, then one legacy alias."""

    expected = _sha256_text(_canonical_json({"type": "boolean", "value": True}))
    for marketplace in CURATED_PLUGIN_CONFIG_MARKETPLACE_ALIASES:
        pointer = f"/plugins/{plugin_name}@{marketplace}/enabled"
        supplied = config_leaf_hashes.get(pointer)
        if supplied is not None:
            return supplied == expected
    return False


def classify_enabled_plugin_version_replacement(
    previous: dict[str, Any], current: dict[str, Any]
) -> tuple[bool, str]:
    """Accept only one exact updater-bound curated-plugin replacement.

    The first-party updater writes one stable schema-v1 receipt at the plugin
    root. Its unchanged bytes bind the old and new package observations to the
    same remote plugin identity. A version-local schema-v2 receipt remains a
    stronger supported form because it also carries the package digests.
    """

    if not _valid_receipt(previous):
        return False, "INVALID_BASELINE_AUTHORITY_RECEIPT"
    if not _valid_receipt(current):
        return False, "INVALID_CURRENT_AUTHORITY_RECEIPT"
    if previous.get("required_source_hashes") != current.get("required_source_hashes"):
        return False, "UNRECOGNIZED_SOURCE_DELTA"
    if (
        previous.get("primary_runtime_bundle_origin")
        != current.get("primary_runtime_bundle_origin")
    ):
        return False, "UNRELATED_PRIMARY_RUNTIME_BUNDLE_DELTA"
    if (
        previous.get("bundled_marketplace_origin")
        != current.get("bundled_marketplace_origin")
    ):
        return False, "UNRELATED_APP_BUNDLE_DELTA"
    if (
        previous.get("config_projection_sha256")
        != current.get("config_projection_sha256")
        or previous.get("config_leaf_hashes") != current.get("config_leaf_hashes")
    ):
        return False, "PLUGIN_CONFIG_CHANGED"
    if previous.get("app_identity") != current.get("app_identity"):
        return False, "PLUGIN_APP_IDENTITY_CHANGED"

    previous_surfaces = previous["plugin_capability_surfaces"]
    current_surfaces = current["plugin_capability_surfaces"]
    removed_packages = sorted(set(previous_surfaces) - set(current_surfaces))
    added_packages = sorted(set(current_surfaces) - set(previous_surfaces))
    if len(added_packages) != 1 or len(removed_packages) > 1:
        return False, "PLUGIN_VERSION_REPLACEMENT_INCOMPLETE"
    new_package = added_packages[0]
    new_parts = _plugin_package_parts(new_package)
    if new_parts is None:
        return False, "PLUGIN_IDENTITY_CHANGED"
    plugin_name = new_parts[0]
    prior_candidates: list[tuple[tuple[int, int, int, tuple[tuple[int, Any], ...]], str]] = []
    for package in previous_surfaces:
        parts = _plugin_package_parts(package)
        if parts is None or parts[0] != plugin_name:
            continue
        parsed = _semver_key(parts[1])
        if parsed is None:
            return False, "PLUGIN_VERSION_UNPARSEABLE"
        prior_candidates.append((parsed, package))
    if not prior_candidates:
        return False, "PLUGIN_VERSION_REPLACEMENT_INCOMPLETE"
    old_version, old_package = max(prior_candidates, key=lambda item: item[0])
    old_parts = _plugin_package_parts(old_package)
    if old_parts is None:
        return False, "PLUGIN_IDENTITY_CHANGED"
    new_version = _semver_key(new_parts[1])
    if old_version is None or new_version is None:
        return False, "PLUGIN_VERSION_UNPARSEABLE"
    if new_version <= old_version:
        return False, "PLUGIN_VERSION_NOT_NEWER"
    if removed_packages and removed_packages != [old_package]:
        return False, "UNRELATED_PLUGIN_CAPABILITY_DELTA"

    if not _plugin_is_enabled(previous["config_leaf_hashes"], plugin_name):
        return False, "PLUGIN_NOT_ENABLED"

    previous_manifests = previous["plugin_package_manifests"]
    current_manifests = current["plugin_package_manifests"]
    old_manifest = previous_manifests.get(old_package)
    new_manifest = current_manifests.get(new_package)
    if old_manifest is None or new_manifest is None:
        return False, "PLUGIN_MANIFEST_IDENTITY_UNPROVEN"
    if (
        old_manifest["name"].casefold() != plugin_name
        or new_manifest["name"].casefold() != plugin_name
        or old_manifest["version"] != old_parts[1]
        or new_manifest["version"] != new_parts[1]
    ):
        return False, "PLUGIN_MANIFEST_IDENTITY_MISMATCH"

    previous_origins = previous["plugin_package_origins"]
    current_origins = current["plugin_package_origins"]
    old_origin = previous_origins.get(old_package)
    new_origin = current_origins.get(new_package)
    if old_origin is None or new_origin is None:
        return False, "PLUGIN_ORIGIN_UNPROVEN"
    if (
        old_origin["receipt_schema_version"]
        not in REMOTE_PLUGIN_INSTALL_RECEIPT_SCHEMAS
        or new_origin["receipt_schema_version"]
        not in REMOTE_PLUGIN_INSTALL_RECEIPT_SCHEMAS
        or old_origin["attestation_mode"] not in PLUGIN_ORIGIN_ATTESTATION_MODES
        or new_origin["attestation_mode"] not in PLUGIN_ORIGIN_ATTESTATION_MODES
        or old_origin["marketplace"] != CURATED_PLUGIN_CACHE_MARKETPLACE
        or new_origin["marketplace"] != CURATED_PLUGIN_CACHE_MARKETPLACE
        or old_origin["plugin_name"] != old_manifest["name"]
        or new_origin["plugin_name"] != new_manifest["name"]
        or old_origin["plugin_version"] != old_manifest["version"]
        or new_origin["plugin_version"] != new_manifest["version"]
        or old_origin["plugin_manifest_sha256"].upper()
        != old_manifest["manifest_sha256"].upper()
        or new_origin["plugin_manifest_sha256"].upper()
        != new_manifest["manifest_sha256"].upper()
    ):
        return False, "PLUGIN_ORIGIN_BINDING_MISMATCH"
    if (
        old_origin["remote_plugin_id"] != new_origin["remote_plugin_id"]
        or old_origin["marketplace"] != new_origin["marketplace"]
        or old_origin["plugin_name"] != new_origin["plugin_name"]
        or old_origin["receipt_schema_version"]
        != new_origin["receipt_schema_version"]
        or old_origin["attestation_mode"] != new_origin["attestation_mode"]
    ):
        return False, "PLUGIN_ORIGIN_CHANGED"
    if (
        old_origin["attestation_mode"] == "plugin-root-v1"
        and old_origin["install_receipt_sha256"]
        != new_origin["install_receipt_sha256"]
    ):
        return False, "PLUGIN_ORIGIN_CHANGED"

    previous_catalog = previous["remote_plugin_catalog_entries"]
    current_catalog = current["remote_plugin_catalog_entries"]
    for package in set(previous_catalog) | set(current_catalog):
        if package in {old_package, new_package}:
            continue
        previous_binding = previous_catalog.get(package)
        current_binding = current_catalog.get(package)
        if (
            previous_binding is None
            or current_binding is None
            or previous_binding["entry_projection_sha256"]
            != current_binding["entry_projection_sha256"]
        ):
            return False, "UNRELATED_REMOTE_PLUGIN_CATALOG_DELTA"
    old_catalog = previous_catalog.get(old_package)
    new_catalog = current_catalog.get(new_package)
    if old_origin["receipt_schema_version"] == 1:
        if not isinstance(old_catalog, dict) or not isinstance(new_catalog, dict):
            return False, "PLUGIN_CATALOG_BINDING_UNPROVEN"
        for name in (
            "remote_plugin_id",
            "plugin_name",
            "status",
            "installation_policy",
            "apps",
            "local_apps",
            "skill_names",
        ):
            if old_catalog.get(name) != new_catalog.get(name):
                return False, "PLUGIN_CATALOG_SURFACE_CHANGED"
        if (
            old_catalog["plugin_version"] != old_origin["plugin_version"]
            or new_catalog["plugin_version"] != new_origin["plugin_version"]
        ):
            return False, "PLUGIN_CATALOG_VERSION_MISMATCH"
    elif old_catalog is not None or new_catalog is not None:
        return False, "PLUGIN_CATALOG_BINDING_UNEXPECTED"

    old_surface = {
        (item["kind"], item["id"]) for item in previous_surfaces[old_package]
    }
    new_surface = {
        (item["kind"], item["id"]) for item in current_surfaces[new_package]
    }
    if old_surface != new_surface or (
        "plugin", f"plugin:{old_manifest['name']}"
    ) not in old_surface:
        return False, "PLUGIN_CAPABILITY_SURFACE_CHANGED"
    for package in set(previous_surfaces) & set(current_surfaces):
        if previous_surfaces[package] != current_surfaces[package]:
            return False, "UNRELATED_PLUGIN_CAPABILITY_DELTA"
        if previous_manifests.get(package) != current_manifests.get(package):
            return False, "UNRELATED_PLUGIN_AUTHORITY_DELTA"
        if previous_origins.get(package) != current_origins.get(package):
            return False, "UNRELATED_PLUGIN_AUTHORITY_DELTA"

    previous_rows = previous["plugin_cache_row_hashes"]
    current_rows = current["plugin_cache_row_hashes"]
    removed_rows = set(previous_rows) - set(current_rows)
    added_rows = set(current_rows) - set(previous_rows)
    changed_rows = {
        key
        for key in set(previous_rows) & set(current_rows)
        if previous_rows[key] != current_rows[key]
    }
    old_prefix = old_package.casefold()
    new_prefix = new_package.casefold()

    def belongs(key: str, package_prefix: str) -> bool:
        try:
            _, relative = key.split("\t", 1)
        except ValueError:
            return False
        value = relative.replace("\\", "/").casefold()
        return value == package_prefix or value.startswith(package_prefix + "/")

    previous_old_rows = {key for key in previous_rows if belongs(key, old_prefix)}
    current_new_rows = {
        key for key in current_rows if belongs(key, new_prefix)
    }

    if (
        changed_rows
        or not added_rows
        or any(belongs(key, new_prefix) for key in previous_rows)
        or added_rows != current_new_rows
        or any(not belongs(key, new_prefix) for key in added_rows)
    ):
        return False, "UNRELATED_PLUGIN_CACHE_DELTA"
    if removed_packages:
        if (
            any(belongs(key, old_prefix) for key in current_rows)
            or removed_rows != previous_old_rows
            or any(not belongs(key, old_prefix) for key in removed_rows)
        ):
            return False, "UNRELATED_PLUGIN_CACHE_DELTA"
    elif removed_rows:
        return False, "UNRELATED_PLUGIN_CACHE_DELTA"
    old_suffixes = _package_row_suffixes(previous_rows, old_package)
    new_suffixes = _package_row_suffixes(current_rows, new_package)
    required_suffixes = {"ROOT\t.", "FILE\t.codex-plugin/plugin.json"}
    if old_suffixes != new_suffixes or not required_suffixes.issubset(old_suffixes):
        return False, "PLUGIN_AUTHORITY_SHAPE_CHANGED"
    return True, "RECOGNIZED_ENABLED_PLUGIN_VERSION_REPLACEMENT"


def classify_primary_runtime_bundle_update(
    previous: dict[str, Any], current: dict[str, Any]
) -> tuple[bool, str]:
    """Accept one detached, byte-matched, strictly newer primary-runtime cohort."""

    if not _valid_receipt(previous):
        return False, "INVALID_BASELINE_AUTHORITY_RECEIPT"
    if not _valid_receipt(current):
        return False, "INVALID_CURRENT_AUTHORITY_RECEIPT"
    for field, reason in (
        ("config_projection_sha256", "PRIMARY_RUNTIME_CONFIG_CHANGED"),
        ("config_leaf_hashes", "PRIMARY_RUNTIME_CONFIG_CHANGED"),
        ("config_capability_surfaces", "PRIMARY_RUNTIME_CONFIG_CHANGED"),
        ("required_source_hashes", "UNRECOGNIZED_SOURCE_DELTA"),
        ("app_identity", "PRIMARY_RUNTIME_APP_IDENTITY_CHANGED"),
        ("plugin_package_origins", "UNRELATED_PLUGIN_ORIGIN_DELTA"),
        ("bundled_marketplace_origin", "UNRELATED_APP_BUNDLE_DELTA"),
    ):
        if previous.get(field) != current.get(field):
            return False, reason
    if _catalog_entries_authority(
        previous.get("remote_plugin_catalog_entries")
    ) != _catalog_entries_authority(current.get("remote_plugin_catalog_entries")):
        return False, "UNRELATED_REMOTE_PLUGIN_CATALOG_DELTA"

    old_origin = previous["primary_runtime_bundle_origin"]
    new_origin = current["primary_runtime_bundle_origin"]
    old_version = str(old_origin["bundle_version"])
    new_version = str(new_origin["bundle_version"])
    old_semver = _semver_key(old_version)
    new_semver = _semver_key(new_version)
    if old_semver is None or new_semver is None:
        return False, "PRIMARY_RUNTIME_BUNDLE_VERSION_UNPARSEABLE"
    if new_semver <= old_semver:
        return False, "PRIMARY_RUNTIME_BUNDLE_VERSION_NOT_NEWER"
    if old_origin["bundle_format_version"] != new_origin["bundle_format_version"]:
        return False, "PRIMARY_RUNTIME_BUNDLE_FORMAT_CHANGED"
    if old_origin["runtime_json_sha256"] == new_origin["runtime_json_sha256"]:
        return False, "PRIMARY_RUNTIME_BUNDLE_METADATA_DID_NOT_ADVANCE"

    old_packages = old_origin["packages"]
    new_packages = new_origin["packages"]
    if set(old_packages) != set(new_packages) or not old_packages:
        return False, "PRIMARY_RUNTIME_BUNDLE_COHORT_CHANGED"
    old_cache_packages = {
        str(package["cache_package"]) for package in old_packages.values()
    }
    new_cache_packages = {
        str(package["cache_package"]) for package in new_packages.values()
    }
    if old_cache_packages & new_cache_packages:
        return False, "PRIMARY_RUNTIME_BUNDLE_COHORT_DID_NOT_REPLACE"

    previous_surfaces = previous["plugin_capability_surfaces"]
    current_surfaces = current["plugin_capability_surfaces"]
    previous_manifests = previous["plugin_package_manifests"]
    current_manifests = current["plugin_package_manifests"]
    allowed_packages = old_cache_packages | new_cache_packages
    for field_values, reason in (
        ((previous_surfaces, current_surfaces), "UNRELATED_PLUGIN_CAPABILITY_DELTA"),
        ((previous_manifests, current_manifests), "UNRELATED_PLUGIN_AUTHORITY_DELTA"),
    ):
        old_values, new_values = field_values
        for package in set(old_values) | set(new_values):
            if package in allowed_packages:
                continue
            if old_values.get(package) != new_values.get(package):
                return False, reason

    previous_rows = previous["plugin_cache_row_hashes"]
    current_rows = current["plugin_cache_row_hashes"]

    def belongs(key: str, package: str) -> bool:
        try:
            _, relative = key.split("\t", 1)
        except ValueError:
            return False
        normalized = relative.replace("\\", "/").strip("/").casefold()
        prefix = package.casefold()
        return normalized == prefix or normalized.startswith(prefix + "/")

    for package_key in sorted(old_packages):
        old_package = old_packages[package_key]
        new_package = new_packages[package_key]
        old_cache_package = str(old_package["cache_package"])
        new_cache_package = str(new_package["cache_package"])
        if (
            old_package["plugin_name"] != new_package["plugin_name"]
            or str(old_package["plugin_name"]).casefold() != package_key
            or old_package["plugin_version"] != old_version
            or new_package["plugin_version"] != new_version
            or old_cache_package not in previous_surfaces
            or old_cache_package in current_surfaces
            or new_cache_package not in current_surfaces
            or new_cache_package in previous_surfaces
        ):
            return False, "PRIMARY_RUNTIME_BUNDLE_COHORT_INCOMPLETE"
        old_surface = {
            (str(item["kind"]), str(item["id"]))
            for item in previous_surfaces[old_cache_package]
        }
        new_surface = {
            (str(item["kind"]), str(item["id"]))
            for item in current_surfaces[new_cache_package]
        }
        if old_surface != new_surface or (
            "plugin", f"plugin:{old_package['plugin_name']}"
        ) not in old_surface:
            return False, "PRIMARY_RUNTIME_CAPABILITY_SURFACE_CHANGED"
        old_manifest = previous_manifests.get(old_cache_package)
        new_manifest = current_manifests.get(new_cache_package)
        if (
            not isinstance(old_manifest, dict)
            or not isinstance(new_manifest, dict)
            or old_manifest.get("name") != old_package["plugin_name"]
            or new_manifest.get("name") != new_package["plugin_name"]
            or old_manifest.get("version") != old_version
            or new_manifest.get("version") != new_version
        ):
            return False, "PRIMARY_RUNTIME_MANIFEST_IDENTITY_MISMATCH"
        expected_old_shape = {"ROOT\t."} | {
            f"FILE\t{path}" for path in old_package["authority_file_hashes"]
        }
        expected_new_shape = {"ROOT\t."} | {
            f"FILE\t{path}" for path in new_package["authority_file_hashes"]
        }
        if (
            _package_row_suffixes(previous_rows, old_cache_package)
            != expected_old_shape
            or _package_row_suffixes(current_rows, new_cache_package)
            != expected_new_shape
        ):
            return False, "PRIMARY_RUNTIME_AUTHORITY_SHAPE_CHANGED"

    previous_primary_rows = {
        key
        for key in previous_rows
        if any(belongs(key, package) for package in old_cache_packages)
    }
    current_primary_rows = {
        key
        for key in current_rows
        if any(belongs(key, package) for package in new_cache_packages)
    }
    removed_rows = set(previous_rows) - set(current_rows)
    added_rows = set(current_rows) - set(previous_rows)
    changed_rows = {
        key
        for key in set(previous_rows) & set(current_rows)
        if previous_rows[key] != current_rows[key]
    }
    if (
        not removed_rows
        or not added_rows
        or changed_rows
        or removed_rows != previous_primary_rows
        or added_rows != current_primary_rows
        or any(
            any(belongs(key, package) for package in new_cache_packages)
            for key in previous_rows
        )
        or any(
            any(belongs(key, package) for package in old_cache_packages)
            for key in current_rows
        )
        or previous["plugin_cache_inventory_sha256"]
        == current["plugin_cache_inventory_sha256"]
    ):
        return False, "UNRELATED_PRIMARY_RUNTIME_CACHE_DELTA"
    return True, "RECOGNIZED_PRIMARY_RUNTIME_BUNDLE_UPDATE"


def _cache_row_belongs_to_package(key: str, package: str) -> bool:
    try:
        _, relative = key.split("\t", 1)
    except ValueError:
        return False
    normalized = relative.replace("\\", "/").strip("/").casefold()
    prefix = package.casefold()
    return normalized == prefix or normalized.startswith(prefix + "/")


def _replace_receipt_package_authority(
    target: dict[str, Any],
    source: dict[str, Any],
    packages: set[str],
) -> None:
    normalized_packages = {package.casefold() for package in packages}

    def selected(package: str) -> bool:
        return package.casefold() in normalized_packages

    for field in ("plugin_capability_surfaces", "plugin_package_manifests"):
        target_values = target[field]
        source_values = source[field]
        for package in list(target_values):
            if selected(package):
                target_values.pop(package)
        for package, value in source_values.items():
            if selected(package):
                target_values[package] = json.loads(_canonical_json(value))

    target_rows = target["plugin_cache_row_hashes"]
    for key in list(target_rows):
        if any(
            _cache_row_belongs_to_package(key, package)
            for package in normalized_packages
        ):
            target_rows.pop(key)
    for key, value in source["plugin_cache_row_hashes"].items():
        if any(
            _cache_row_belongs_to_package(key, package)
            for package in normalized_packages
        ):
            target_rows[key] = value


def _compound_app_primary_runtime_receipts(
    previous: dict[str, Any], current: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Decompose one observed snapshot into exact app and runtime transitions."""

    app_packages = _bundled_cache_packages(
        previous["bundled_marketplace_origin"]
    ) | _bundled_cache_packages(current["bundled_marketplace_origin"])
    primary_packages = {
        str(package["cache_package"])
        for origin in (
            previous["primary_runtime_bundle_origin"],
            current["primary_runtime_bundle_origin"],
        )
        for package in origin["packages"].values()
    }
    if app_packages & primary_packages:
        raise ValueError("compound authority package scopes overlap")

    app_only = json.loads(_canonical_json(previous))
    for field in (
        "config_projection_sha256",
        "config_leaf_hashes",
        "config_capability_surfaces",
        "app_identity",
        "bundled_marketplace_origin",
        "remote_plugin_catalog_entries",
    ):
        app_only[field] = json.loads(_canonical_json(current[field]))
    _replace_receipt_package_authority(app_only, current, app_packages)
    app_only["plugin_cache_inventory_sha256"] = current[
        "plugin_cache_inventory_sha256"
    ]
    app_only["snapshot_sha256"] = authority_snapshot_digest(app_only)

    primary_only = json.loads(_canonical_json(previous))
    primary_only["primary_runtime_bundle_origin"] = json.loads(
        _canonical_json(current["primary_runtime_bundle_origin"])
    )
    primary_only["remote_plugin_catalog_entries"] = json.loads(
        _canonical_json(current["remote_plugin_catalog_entries"])
    )
    _replace_receipt_package_authority(
        primary_only, current, primary_packages
    )
    primary_only["plugin_cache_inventory_sha256"] = current[
        "plugin_cache_inventory_sha256"
    ]
    primary_only["snapshot_sha256"] = authority_snapshot_digest(primary_only)

    composed = json.loads(_canonical_json(app_only))
    composed["primary_runtime_bundle_origin"] = json.loads(
        _canonical_json(current["primary_runtime_bundle_origin"])
    )
    composed["remote_plugin_catalog_entries"] = json.loads(
        _canonical_json(current["remote_plugin_catalog_entries"])
    )
    _replace_receipt_package_authority(composed, current, primary_packages)
    composed["plugin_cache_inventory_sha256"] = current[
        "plugin_cache_inventory_sha256"
    ]
    composed["snapshot_sha256"] = authority_snapshot_digest(composed)
    return app_only, primary_only, composed


def classify_compound_app_primary_runtime_update(
    previous: dict[str, Any], current: dict[str, Any]
) -> tuple[bool, str]:
    """Accept one atomic snapshot containing only both proven update cohorts."""

    if not _valid_receipt(previous):
        return False, "INVALID_BASELINE_AUTHORITY_RECEIPT"
    if not _valid_receipt(current):
        return False, "INVALID_CURRENT_AUTHORITY_RECEIPT"
    if previous["required_source_hashes"] != current["required_source_hashes"]:
        return False, "UNRECOGNIZED_SOURCE_DELTA"
    if previous["plugin_package_origins"] != current["plugin_package_origins"]:
        return False, "UNRELATED_PLUGIN_ORIGIN_DELTA"
    if _catalog_entries_authority(
        previous.get("remote_plugin_catalog_entries")
    ) != _catalog_entries_authority(current.get("remote_plugin_catalog_entries")):
        return False, "UNRELATED_REMOTE_PLUGIN_CATALOG_DELTA"
    try:
        app_only, primary_only, composed = (
            _compound_app_primary_runtime_receipts(previous, current)
        )
    except (KeyError, TypeError, ValueError):
        return False, "COMPOUND_UPDATE_DECOMPOSITION_FAILED"
    if composed != current:
        return False, "COMPOUND_UPDATE_HAS_UNRELATED_AUTHORITY_DELTA"
    app_allowed, app_reason = classify_app_update(previous, app_only)
    if not app_allowed:
        return False, f"COMPOUND_APP_{app_reason}"
    primary_allowed, primary_reason = classify_primary_runtime_bundle_update(
        previous, primary_only
    )
    if not primary_allowed:
        return False, f"COMPOUND_PRIMARY_{primary_reason}"
    return True, "RECOGNIZED_COMPOUND_APP_PRIMARY_RUNTIME_UPDATE"


def _config_control_default_enabled(kind: str, *, present: bool) -> bool:
    if present:
        raise ValueError("present controls carry an explicit enabled value")
    if kind in {"app", "plugin_mcp", "skill"}:
        return True
    if kind in {"mcp", "plugin"}:
        return False
    raise ValueError("config control has no absence default")


def _scoped_config_update_plan(
    previous: dict[str, Any], current: dict[str, Any]
) -> tuple[dict[str, Any] | None, str]:
    if not _valid_receipt(previous):
        return None, "INVALID_BASELINE_AUTHORITY_RECEIPT"
    if not _valid_receipt(current):
        return None, "INVALID_CURRENT_AUTHORITY_RECEIPT"
    unchanged_fields = (
        "plugin_cache_inventory_sha256",
        "plugin_cache_row_hashes",
        "plugin_capability_surfaces",
        "plugin_package_manifests",
        "plugin_package_origins",
        "bundled_marketplace_origin",
        "primary_runtime_bundle_origin",
        "required_source_hashes",
        "app_identity",
    )
    if any(previous.get(field) != current.get(field) for field in unchanged_fields):
        return None, "CONFIG_UPDATE_HAS_UNRELATED_AUTHORITY_DELTA"
    if _catalog_entries_authority(
        previous.get("remote_plugin_catalog_entries")
    ) != _catalog_entries_authority(current.get("remote_plugin_catalog_entries")):
        return None, "CONFIG_UPDATE_HAS_UNRELATED_AUTHORITY_DELTA"
    changed = _changed_hash_keys(
        previous["config_leaf_hashes"], current["config_leaf_hashes"]
    )
    if not changed:
        return None, "CONFIG_AUTHORITY_DID_NOT_CHANGE"
    previous_bindings = previous["config_capability_surfaces"]
    current_bindings = current["config_capability_surfaces"]
    affected: set[str] = set()
    expected_enabled_surface: set[str] = set()
    expected_disabled_surface: set[str] = set()
    required_enabled: set[str] = set()
    required_disabled: set[str] = set()
    changes: list[dict[str, Any]] = []
    for pointer in sorted(changed):
        for binding in (
            previous_bindings.get(pointer),
            current_bindings.get(pointer),
        ):
            if binding is not None and binding.get("change_class") != "availability_toggle":
                return None, "CONFIG_RUNTIME_IDENTITY_REQUIRES_OPERATOR_REBASELINE"
    for pointer in sorted(changed):
        old = previous_bindings.get(pointer)
        new = current_bindings.get(pointer)
        if old is None and new is None:
            return None, "CONFIG_CONTROL_SURFACE_UNPROVEN"
        for binding in (old, new):
            if binding is not None and binding.get("change_class") != "availability_toggle":
                return None, "CONFIG_RUNTIME_IDENTITY_REQUIRES_OPERATOR_REBASELINE"
        reference = new or old
        assert isinstance(reference, dict)
        kind = str(reference["control_kind"])
        key = str(reference["control_key"])
        for binding in (old, new):
            if binding is None:
                continue
            if (
                binding["control_kind"] != kind
                or binding["control_key"] != key
                or set(binding["capability_ids"])
                != set(reference["capability_ids"])
                or set(binding["required_capability_ids"])
                != set(reference["required_capability_ids"])
            ):
                return None, "CONFIG_CONTROL_IDENTITY_CHANGED"
        try:
            old_enabled = (
                bool(old["enabled"])
                if old is not None
                else _config_control_default_enabled(kind, present=False)
            )
            new_enabled = (
                bool(new["enabled"])
                if new is not None
                else _config_control_default_enabled(kind, present=False)
            )
        except (KeyError, ValueError):
            return None, "CONFIG_CONTROL_DIRECTION_UNPROVEN"
        if old_enabled == new_enabled:
            return None, "CONFIG_CONTROL_HAS_NO_CAPABILITY_EFFECT"
        if not old_enabled and new_enabled:
            if kind == "skill":
                return None, "LOCAL_SKILL_ENABLEMENT_REQUIRES_OPERATOR_REBASELINE"
            return None, "CONFIG_ENABLEMENT_REQUIRES_OPERATOR_REBASELINE"
        capability_ids = set(reference["capability_ids"])
        required_ids = set(reference["required_capability_ids"])
        affected.update(capability_ids)
        if new_enabled:
            expected_enabled_surface.update(capability_ids)
            required_enabled.update(required_ids)
        else:
            expected_disabled_surface.update(capability_ids)
            required_disabled.update(
                capability_ids if kind == "plugin" else required_ids
            )
        changes.append(
            {
                "pointer": pointer,
                "control_kind": kind,
                "control_key": key,
                "previous_enabled": old_enabled,
                "current_enabled": new_enabled,
                "capability_ids": sorted(capability_ids),
                "required_capability_ids": sorted(required_ids),
            }
        )
    if (
        not affected
        or required_enabled & required_disabled
        or not (required_enabled or required_disabled)
    ):
        return None, "CONFIG_CONTROL_EFFECT_CONTRADICTORY"
    return {
        "changed_leaves": sorted(changed),
        "affected_capability_ids": sorted(affected),
        "expected_enabled_surface_ids": sorted(expected_enabled_surface),
        "expected_disabled_surface_ids": sorted(expected_disabled_surface),
        "required_enabled_capability_ids": sorted(required_enabled),
        "required_disabled_capability_ids": sorted(required_disabled),
        "changes": changes,
    }, "RECOGNIZED_SCOPED_CONFIG_UPDATE"


def classify_scoped_config_update(
    previous: dict[str, Any], current: dict[str, Any]
) -> tuple[bool, str]:
    plan, reason = _scoped_config_update_plan(previous, current)
    return plan is not None, reason


def _manifest_active_capability_ids(manifest: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for entry in manifest.get("entries", []):
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("id")
        if (
            isinstance(identifier, str)
            and identifier
            and index.is_active_state(entry.get("state"))
            and not index.is_state_artifact(entry)
        ):
            result.add(identifier)
    return result


def _manifest_suppressed_capability_ids(manifest: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for item in manifest.get("suppressed_capabilities", []):
        if not isinstance(item, dict):
            continue
        identifier = item.get("id")
        reason_code = item.get("reason_code")
        if (
            isinstance(identifier, str)
            and identifier
            and isinstance(reason_code, str)
            and reason_code
        ):
            result.add(identifier)
    return result


def _valid_builder_suppression(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    identifier = item.get("id")
    reason = item.get("reason_code")
    if not isinstance(identifier, str) or not identifier:
        return False
    if reason in {
        "LIVE_MCP_TRANSPORT_UNRESOLVABLE",
        "HARD_DEPENDENCY_DISABLED_OR_UNCALLABLE",
    }:
        if set(item) != {
            "id",
            "reason_code",
            "missing_capability",
            "fallback_capabilities",
        }:
            return False
        missing = item.get("missing_capability")
        fallbacks = item.get("fallback_capabilities")
        return (
            (missing is None if reason == "LIVE_MCP_TRANSPORT_UNRESOLVABLE" else isinstance(missing, str) and bool(missing))
            and isinstance(fallbacks, list)
            and fallbacks == sorted(set(fallbacks))
            and all(isinstance(value, str) and value for value in fallbacks)
        )
    if reason == "PASSIVE_PLUGIN_VERSION_AMBIGUOUS":
        if set(item) != {"id", "reason_code", "source_path", "candidates"}:
            return False
        candidates = item.get("candidates")
        return (
            isinstance(item.get("source_path"), str)
            and bool(item["source_path"])
            and isinstance(candidates, list)
            and len(candidates) >= 2
            and candidates == sorted(set(candidates))
            and all(
                isinstance(value, str) and _semver_key(value) is not None
                for value in candidates
            )
        )
    return False


def _suppression_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    raw = manifest.get("suppressed_capabilities", [])
    if not isinstance(raw, list):
        return None
    result: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not _valid_builder_suppression(item):
            return None
        identifier = str(item["id"])
        if identifier in result:
            return None
        result[identifier] = item
    return result


def _unique_active_ids(manifest: dict[str, Any]) -> set[str] | None:
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        return None
    result: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        identifier = entry.get("id")
        kind = entry.get("kind")
        state = entry.get("state")
        if (
            not isinstance(identifier, str)
            or not identifier
            or kind not in {"plugin", "skill", "tool-family", "mcp"}
            or not isinstance(state, str)
            or not state
        ):
            return None
        if index.is_active_state(state) and not index.is_state_artifact(entry):
            if identifier in result:
                return None
            result.add(identifier)
    return result


def _config_candidate_surface_matches_plan(
    baseline_manifest: dict[str, Any],
    candidate_manifest: dict[str, Any],
    plan: dict[str, Any],
) -> bool:
    baseline_ids = _unique_active_ids(baseline_manifest)
    candidate_ids = _unique_active_ids(candidate_manifest)
    baseline_suppressed = _suppression_map(baseline_manifest)
    candidate_suppressed = _suppression_map(candidate_manifest)
    baseline_entries = _manifest_active_entry_map(baseline_manifest)
    candidate_entries = _manifest_active_entry_map(candidate_manifest)
    if (
        baseline_ids is None
        or candidate_ids is None
        or baseline_suppressed is None
        or candidate_suppressed is None
        or baseline_entries is None
        or candidate_entries is None
    ):
        return False
    candidate_suppressed_ids = set(candidate_suppressed)
    if candidate_ids & candidate_suppressed_ids:
        return False
    affected = set(plan.get("affected_capability_ids", []))
    expected_enabled_surface = set(
        plan.get("expected_enabled_surface_ids", [])
    )
    expected_disabled_surface = set(
        plan.get("expected_disabled_surface_ids", [])
    )
    required_enabled = set(plan.get("required_enabled_capability_ids", []))
    required_disabled = set(plan.get("required_disabled_capability_ids", []))
    if required_enabled or expected_enabled_surface:
        return False
    unaffected_baseline_suppressed = {
        identifier: item
        for identifier, item in baseline_suppressed.items()
        if identifier not in affected
    }
    unaffected_candidate_suppressed = {
        identifier: item
        for identifier, item in candidate_suppressed.items()
        if identifier not in affected
    }
    allowed_optional_suppressed = expected_enabled_surface - required_enabled
    delta = baseline_ids ^ candidate_ids
    common_ids = baseline_ids & candidate_ids
    if any(
        baseline_entries[identifier] != candidate_entries[identifier]
        for identifier in common_ids
    ):
        return False
    return (
        bool(delta)
        and delta.issubset(affected)
        and expected_enabled_surface.issubset(
            candidate_ids | candidate_suppressed_ids
        )
        and expected_disabled_surface.isdisjoint(candidate_ids)
        and required_enabled.issubset(candidate_ids)
        and required_disabled.isdisjoint(candidate_ids)
        and unaffected_candidate_suppressed == unaffected_baseline_suppressed
        and (candidate_suppressed_ids & affected).issubset(
            allowed_optional_suppressed
        )
    )


def classify_worker_runtime_bom_update(
    previous: dict[str, Any], current: dict[str, Any]
) -> tuple[bool, str]:
    """Accept only a new BOM hash with every other authority fact unchanged."""

    if not _valid_receipt(previous):
        return False, "INVALID_BASELINE_AUTHORITY_RECEIPT"
    if not _valid_receipt(current):
        return False, "INVALID_CURRENT_AUTHORITY_RECEIPT"
    previous_sources = previous["required_source_hashes"]
    current_sources = current["required_source_hashes"]
    changed = _changed_hash_keys(previous_sources, current_sources)
    if changed != {index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY}:
        return False, "WORKER_RUNTIME_BOM_DELTA_NOT_ISOLATED"
    expected = json.loads(_canonical_json(previous))
    expected["required_source_hashes"] = json.loads(
        _canonical_json(current_sources)
    )
    # fetched_at is audit-only. A byte-identical catalogue entry refreshed in
    # parallel must not become a second authority delta.
    if _catalog_entries_authority(
        previous.get("remote_plugin_catalog_entries")
    ) != _catalog_entries_authority(current.get("remote_plugin_catalog_entries")):
        return False, "WORKER_RUNTIME_BOM_HAS_UNRELATED_AUTHORITY_DELTA"
    expected["remote_plugin_catalog_entries"] = json.loads(
        _canonical_json(current["remote_plugin_catalog_entries"])
    )
    expected["snapshot_sha256"] = authority_snapshot_digest(expected)
    if expected != current:
        return False, "WORKER_RUNTIME_BOM_HAS_UNRELATED_AUTHORITY_DELTA"
    return True, "RECOGNIZED_WORKER_RUNTIME_BOM_UPDATE"


def classify_authority_update(
    previous: dict[str, Any], current: dict[str, Any]
) -> tuple[bool, str]:
    app_allowed, app_reason = classify_app_update(previous, current)
    if app_allowed:
        return True, app_reason
    primary_allowed, primary_reason = classify_primary_runtime_bundle_update(
        previous, current
    )
    if primary_allowed:
        return True, primary_reason
    worker_allowed, worker_reason = classify_worker_runtime_bom_update(
        previous, current
    )
    if worker_allowed:
        return True, worker_reason
    compound_allowed, compound_reason = (
        classify_compound_app_primary_runtime_update(previous, current)
    )
    if compound_allowed:
        return True, compound_reason
    plugin_allowed, plugin_reason = classify_enabled_plugin_version_replacement(
        previous, current
    )
    if plugin_allowed:
        return True, plugin_reason
    config_allowed, config_reason = classify_scoped_config_update(previous, current)
    if config_allowed:
        return True, config_reason
    if (
        previous.get("app_identity") != current.get("app_identity")
        and previous.get("primary_runtime_bundle_origin")
        != current.get("primary_runtime_bundle_origin")
    ):
        return False, compound_reason
    if app_reason == "APP_IDENTITY_DID_NOT_ADVANCE":
        if (
            _changed_hash_keys(
                previous.get("required_source_hashes", {}),
                current.get("required_source_hashes", {}),
            )
            == {index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY}
        ):
            return False, worker_reason
        if (
            previous.get("primary_runtime_bundle_origin")
            != current.get("primary_runtime_bundle_origin")
            or any(
                PRIMARY_RUNTIME_MARKETPLACE in str(key).casefold()
                for key in _changed_hash_keys(
                    previous.get("plugin_cache_row_hashes", {}),
                    current.get("plugin_cache_row_hashes", {}),
                )
            )
        ):
            return False, primary_reason
        if plugin_reason in {
            "PLUGIN_CONFIG_CHANGED",
            "PLUGIN_APP_IDENTITY_CHANGED",
        }:
            return False, config_reason
        return False, plugin_reason
    return False, app_reason


def _component_after(path_value: str, marker: str) -> str:
    parts = Path(path_value).parts
    for position, part in enumerate(parts[:-1]):
        if part.casefold() == marker.casefold():
            return parts[position + 1]
    return ""


def _nested_string(data: dict[str, Any], *path: str) -> str:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return current if isinstance(current, str) else ""


def _read_plugin_version(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    value = data.get("version") if isinstance(data, dict) else None
    return value if isinstance(value, str) else ""


def _app_identity(codex_home: Path, config_path: Path) -> dict[str, Any]:
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return {"coherent": False}
    command = _nested_string(data, "mcp_servers", "node_repl", "command")
    env = (
        data.get("mcp_servers", {}).get("node_repl", {}).get("env", {})
        if isinstance(data.get("mcp_servers"), dict)
        else {}
    )
    if not isinstance(env, dict):
        return {"coherent": False}
    runtime_id = _component_after(command, "cua_node")
    app_version = env.get("BROWSER_USE_CODEX_APP_VERSION", "")
    cli_path_text = env.get("CODEX_CLI_PATH", "")
    cli_id = _component_after(cli_path_text, "bin")
    trusted_hash = env.get("NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S", "")
    runtime_path = Path(command) if command else Path()
    cli_path = Path(cli_path_text) if cli_path_text else Path()
    browser_root = codex_home / "plugins" / "cache" / "openai-bundled" / "browser"
    browser_client = browser_root / str(app_version) / "scripts" / "browser-client.mjs"
    browser_hash = _sha256_file(browser_client)
    runtime_values = (
        command,
        str(env.get("NODE_REPL_NODE_MODULE_DIRS", "")),
        str(env.get("NODE_REPL_NODE_PATH", "")),
        str(env.get("NODE_REPL_TRUSTED_CODE_PATHS", "")),
    )
    plugin_versions = {
        plugin: _read_plugin_version(
            codex_home
            / "plugins"
            / "cache"
            / "openai-bundled"
            / plugin
            / str(app_version)
            / ".codex-plugin"
            / "plugin.json"
        )
        for plugin in APP_RUNTIME_PLUGIN_NAMES
    }
    latest_version = _read_plugin_version(
        codex_home
        / "plugins"
        / "cache"
        / "openai-bundled"
        / "chrome"
        / "latest"
        / ".codex-plugin"
        / "plugin.json"
    )
    coherent = bool(
        app_version
        and runtime_id
        and cli_id
        and all(runtime_id.casefold() in value.casefold() for value in runtime_values)
        and runtime_path.is_file()
        and cli_path.is_file()
        and SHA256_PATTERN.fullmatch(str(trusted_hash))
        and browser_hash
        and browser_hash.upper() == str(trusted_hash).upper()
        and all(version == app_version for version in plugin_versions.values())
        and latest_version == app_version
    )
    return {
        "app_version": str(app_version),
        "runtime_id": runtime_id,
        "cli_id": cli_id,
        "trusted_browser_client_sha256": str(trusted_hash).upper(),
        "browser_client_sha256": browser_hash,
        "runtime_executable_sha256": _sha256_file(runtime_path),
        "cli_executable_sha256": _sha256_file(cli_path),
        "coherent": coherent,
    }


def _manifest_active_entry_map(
    manifest: dict[str, Any],
) -> dict[str, dict[str, Any]] | None:
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        return None
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not index.is_active_state(entry.get("state")):
            continue
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in result:
            return None
        result[identifier] = entry
    return result


def _changed_receipt_capability_ids(
    previous: dict[str, Any], current: dict[str, Any]
) -> set[str]:
    previous_surfaces = previous.get("plugin_capability_surfaces", {})
    current_surfaces = current.get("plugin_capability_surfaces", {})
    affected: set[str] = set()
    for package in set(previous_surfaces) | set(current_surfaces):
        if previous_surfaces.get(package) == current_surfaces.get(package):
            continue
        for surface in (
            previous_surfaces.get(package, []),
            current_surfaces.get(package, []),
        ):
            if isinstance(surface, list):
                affected.update(
                    str(item["id"])
                    for item in surface
                    if isinstance(item, dict)
                    and isinstance(item.get("id"), str)
                    and item["id"]
                )
    changed_config = _changed_hash_keys(
        previous.get("config_leaf_hashes", {}),
        current.get("config_leaf_hashes", {}),
    )
    for pointer in changed_config:
        for bindings in (
            previous.get("config_capability_surfaces", {}),
            current.get("config_capability_surfaces", {}),
        ):
            binding = bindings.get(pointer) if isinstance(bindings, dict) else None
            if isinstance(binding, dict) and isinstance(
                binding.get("capability_ids"), list
            ):
                affected.update(str(value) for value in binding["capability_ids"])
    return affected


def _routing_semantic_entry_projection(entry: dict[str, Any]) -> dict[str, Any]:
    # These four fields are receipt-proven package/runtime evidence. Every
    # other field is routing semantics or builder policy and must remain exact.
    return {
        key: value
        for key, value in entry.items()
        if key not in {"hash_scope", "sha256", "source_path", "version"}
    }


def _automatic_candidate_semantics_match(
    baseline_manifest: dict[str, Any],
    candidate_manifest: dict[str, Any],
    previous_receipt: dict[str, Any],
    current_receipt: dict[str, Any],
) -> bool:
    baseline_entries = _manifest_active_entry_map(baseline_manifest)
    candidate_entries = _manifest_active_entry_map(candidate_manifest)
    baseline_suppressed = _suppression_map(baseline_manifest)
    candidate_suppressed = _suppression_map(candidate_manifest)
    if (
        baseline_entries is None
        or candidate_entries is None
        or set(baseline_entries) != set(candidate_entries)
        or baseline_suppressed is None
        or candidate_suppressed is None
        or baseline_suppressed != candidate_suppressed
    ):
        return False
    affected = _changed_receipt_capability_ids(previous_receipt, current_receipt)
    for identifier in baseline_entries:
        baseline = baseline_entries[identifier]
        candidate = candidate_entries[identifier]
        if identifier in affected:
            if _routing_semantic_entry_projection(
                baseline
            ) != _routing_semantic_entry_projection(candidate):
                return False
        elif baseline != candidate:
            return False
    return True


def _required_source_paths(codex_home: Path) -> dict[str, Path]:
    return {
        "catalogue-router.SKILL.md": codex_home
        / "skills"
        / "catalogue-router"
        / "SKILL.md",
        "capability_index.py": codex_home / "hooks" / "capability_index.py",
        "routing_policy_validation.py": codex_home
        / "hooks"
        / "routing_policy_validation.py",
        "capability_config_fingerprint.py": codex_home
        / "hooks"
        / "capability_config_fingerprint.py",
        "capability_index_cli.py": codex_home / "hooks" / "capability_index_cli.py",
        "user_prompt_skill_router.py": codex_home
        / "hooks"
        / "user_prompt_skill_router.py",
        "capability_index_session_start.py": codex_home
        / "hooks"
        / "capability_index_session_start.py",
        "_hook_io.py": codex_home / "hooks" / "_hook_io.py",
        "query-catalogue.ps1": codex_home
        / "skills"
        / "catalogue-router"
        / "scripts"
        / "query-catalogue.ps1",
        "routing-policy.yaml": codex_home / "capability-routing" / "routing-policy.yaml",
        "routing-policy.schema.json": codex_home
        / "capability-routing"
        / "routing-policy.schema.json",
        "active-capabilities.schema.json": codex_home
        / "capability-routing"
        / "active-capabilities.schema.json",
        "project-scope-map.json": codex_home
        / "capability-routing"
        / "project-scope-map.json",
        "project-scope-map.schema.json": codex_home
        / "capability-routing"
        / "project-scope-map.schema.json",
        "route-decision.schema.json": codex_home
        / "capability-routing"
        / "route-decision.schema.json",
        "capability_manifest_recovery.py": codex_home
        / "hooks"
        / "capability_manifest_recovery.py",
        "capability-manifest-builder.ps1": codex_home
        / "capability-routing"
        / "builder"
        / "build_canonical_capability_manifest.ps1",
        "authority-receipt.schema.json": codex_home
        / "capability-routing"
        / "authority-receipt.schema.json",
        "worker-runtime-bom.json": codex_home
        / "capability-routing"
        / "worker-runtime-bom.json",
        "worker-runtime-bom.schema.json": codex_home
        / "capability-routing"
        / "worker-runtime-bom.schema.json",
        "promote_worker_runtime_bom.py": codex_home
        / "capability-routing"
        / "promote_worker_runtime_bom.py",
    }


def _required_source_hashes(codex_home: Path) -> dict[str, str]:
    paths = _required_source_paths(codex_home)
    if set(paths) != REQUIRED_SOURCE_HASH_KEYS:
        raise RuntimeError("required recovery source contract is inconsistent")
    hashes: dict[str, str] = {}
    for name, path in paths.items():
        digest = _sha256_file(path)
        if not digest:
            raise RuntimeError(f"required recovery source is unavailable: {name}")
        hashes[name] = digest
    return hashes


def _plugin_row_hashes(rows: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        parts = row.split("\t", 3)
        if len(parts) != 4 or parts[0] not in {"ROOT", "FILE"}:
            raise ValueError("plugin cache authority row is malformed")
        result[f"{parts[0]}\t{parts[1]}"] = _sha256_text(row)
    return dict(sorted(result.items()))


def _capability_safe_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", value.casefold()).strip("-.")
    if not normalized:
        raise ValueError("plugin capability name is empty after normalization")
    return normalized


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"plugin authority JSON is unreadable: {path.name}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"plugin authority JSON is not an object: {path.name}")
    return value


def _effective_skill_name(skill_file: Path, fallback_name: str) -> str:
    """Match the builder's bounded frontmatter name extraction."""

    try:
        lines = skill_file.read_text(encoding="utf-8").splitlines()[:80]
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("plugin skill metadata is unreadable") from exc
    name = fallback_name
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            match = re.fullmatch(r"name:\s*(.+?)\s*", line)
            if match:
                name = match.group(1).strip().strip('"').strip("'")
    if not name:
        raise ValueError("plugin skill name is empty")
    return name


def _filter_plugin_cache_inventory_rows(
    rows: tuple[str, ...], packages: set[str]
) -> tuple[str, ...]:
    """Keep only manifest-backed packages represented by the bounded surface scan."""

    normalized_packages = {
        normalized
        for package in packages
        if (normalized := _normalized_plugin_package(package)) is not None
    }
    filtered: list[str] = []
    for row in rows:
        parts = row.split("\t", 3)
        if len(parts) != 4 or parts[0] not in {"ROOT", "FILE"}:
            raise ValueError("plugin cache authority row is malformed")
        relative_parts = parts[1].replace("\\", "/").strip("/").split("/")
        if len(relative_parts) < 3:
            raise ValueError("plugin cache authority row has no package root")
        package = "/".join(relative_parts[:3]).casefold()
        if package in normalized_packages:
            filtered.append(row)
    return tuple(sorted(filtered))


def _plugin_package_manifests(
    codex_home: Path, packages: set[str]
) -> dict[str, dict[str, str]]:
    cache_root = codex_home / "plugins" / "cache"
    result: dict[str, dict[str, str]] = {}
    for package in sorted(packages):
        manifest_path = cache_root / Path(package) / ".codex-plugin" / "plugin.json"
        if not manifest_path.is_file():
            continue
        manifest = _read_json_object(manifest_path)
        name = manifest.get("name")
        version = manifest.get("version")
        digest = _sha256_file(manifest_path)
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise RuntimeError("plugin manifest identity is incomplete")
        if not digest:
            raise RuntimeError("plugin manifest digest is unavailable")
        remote_parts = _plugin_package_parts(package)
        if remote_parts is not None and (
            name.casefold() != remote_parts[0] or version != remote_parts[1]
        ):
            raise RuntimeError("remote plugin manifest identity does not match its cache directory")
        result[package] = {
            "name": name,
            "version": version,
            "manifest_sha256": digest,
        }
    return result


def _plugin_package_sha256(package_root: Path, cache_root: Path) -> str:
    """Hash canonical path/content rows, excluding only the self-referential receipt."""

    try:
        resolved_cache_root = cache_root.resolve(strict=True)
        resolved_package_root = package_root.resolve(strict=True)
        if (
            package_root.is_symlink()
            or not resolved_cache_root.is_dir()
            or not resolved_package_root.is_dir()
            or not resolved_package_root.is_relative_to(resolved_cache_root)
        ):
            return ""
        rows: list[dict[str, str]] = []
        paths = sorted(
            package_root.rglob("*"),
            key=lambda path: path.relative_to(package_root).as_posix(),
        )
        for path in paths:
            relative = path.relative_to(package_root).as_posix()
            if relative == REMOTE_PLUGIN_INSTALL_RECEIPT:
                continue
            if path.is_symlink():
                return ""
            if path.is_dir():
                continue
            if not path.is_file():
                return ""
            resolved_path = path.resolve(strict=True)
            if not resolved_path.is_relative_to(resolved_package_root):
                return ""
            digest = _sha256_file(path)
            if not digest:
                return ""
            rows.append({"path": relative, "sha256": digest})
        return _sha256_text(_canonical_json(rows))
    except (OSError, RuntimeError, ValueError):
        return ""


def _bounded_package_authority_file_hashes(
    package_root: Path, containment_root: Path
) -> dict[str, str]:
    """Hash only the exact package files that can change routing authority."""

    try:
        resolved_containment = containment_root.resolve(strict=True)
        resolved_package = package_root.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("package authority is unavailable") from exc
    if (
        containment_root.is_symlink()
        or package_root.is_symlink()
        or not resolved_containment.is_dir()
        or not resolved_package.is_dir()
        or not resolved_package.is_relative_to(resolved_containment)
    ):
        raise RuntimeError("package authority path is invalid")

    authority_files = [
        package_root / ".codex-plugin" / "plugin.json",
        package_root / ".app.json",
        package_root / ".mcp.json",
    ]
    skills_root = package_root / "skills"
    if skills_root.exists():
        if skills_root.is_symlink() or not skills_root.is_dir():
            raise RuntimeError("package skill authority path is invalid")
        for skill_directory in sorted(
            skills_root.iterdir(), key=lambda path: path.name.casefold()
        ):
            if skill_directory.is_symlink():
                raise RuntimeError("package skill authority path is invalid")
            if skill_directory.is_dir():
                authority_files.append(skill_directory / "SKILL.md")

    if not authority_files[0].is_file():
        raise RuntimeError("package plugin manifest is unavailable")
    result: dict[str, str] = {}
    for authority_file in authority_files:
        if not authority_file.exists():
            continue
        if authority_file.is_symlink() or not authority_file.is_file():
            raise RuntimeError("package authority file is invalid")
        try:
            resolved_file = authority_file.resolve(strict=True)
            if not resolved_file.is_relative_to(resolved_package):
                raise RuntimeError("authority file escapes its package")
            before = authority_file.stat()
            digest = _sha256_file(authority_file)
            after = authority_file.stat()
        except OSError as exc:
            raise RuntimeError("package authority file is unreadable") from exc
        if (
            not digest
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise RuntimeError("package authority file changed during capture")
        relative = authority_file.relative_to(package_root).as_posix().casefold()
        if relative in result:
            raise RuntimeError("package authority file identity is ambiguous")
        result[relative] = digest
    return dict(sorted(result.items()))


def _bundled_marketplace_origin(codex_home: Path) -> dict[str, Any]:
    """Capture the complete materialized first-party bundle and cache closure.

    This is byte-coherent dual-tree evidence. Both trees are locally writable,
    so it must not be described as authenticated updater provenance.
    """

    bundle_root = codex_home / BUNDLED_MARKETPLACE_RELATIVE
    plugins_root = bundle_root / "plugins"
    materialization_key_path = bundle_root / ".materialization-key"
    marketplace_path = bundle_root / ".agents" / "plugins" / "marketplace.json"
    cache_root = codex_home / "plugins" / "cache"
    cache_marketplace_root = cache_root / BUNDLED_MARKETPLACE
    try:
        resolved_bundle_root = bundle_root.resolve(strict=True)
        resolved_plugins_root = plugins_root.resolve(strict=True)
        resolved_cache_root = cache_root.resolve(strict=True)
        resolved_cache_marketplace = cache_marketplace_root.resolve(strict=True)
        if (
            bundle_root.is_symlink()
            or plugins_root.is_symlink()
            or cache_root.is_symlink()
            or cache_marketplace_root.is_symlink()
            or not resolved_bundle_root.is_dir()
            or not resolved_plugins_root.is_dir()
            or not resolved_plugins_root.is_relative_to(resolved_bundle_root)
            or not resolved_cache_marketplace.is_dir()
            or not resolved_cache_marketplace.is_relative_to(resolved_cache_root)
            or materialization_key_path.is_symlink()
            or marketplace_path.is_symlink()
            or not materialization_key_path.is_file()
            or not marketplace_path.is_file()
        ):
            raise RuntimeError("bundled marketplace authority path is invalid")
        materialization_bytes = materialization_key_path.read_bytes()
        marketplace_bytes = marketplace_path.read_bytes()
        materialization = json.loads(materialization_bytes.decode("utf-8-sig"))
        marketplace = json.loads(marketplace_bytes.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("bundled marketplace authority is unreadable") from exc

    expected_materialization_keys = {
        "version",
        "appVersion",
        "bundleId",
        "marketplaceName",
        "computerUseSkillVariant",
        "liveVisualizationSkillVariant",
        "visualizeSkillContentHash",
        "plugins",
    }
    if (
        not isinstance(materialization, dict)
        or set(materialization) != expected_materialization_keys
        or materialization.get("version") != 1
        or materialization.get("marketplaceName") != BUNDLED_MARKETPLACE
        or not isinstance(materialization.get("appVersion"), str)
        or _semver_key(materialization["appVersion"]) is None
        or not isinstance(materialization.get("bundleId"), str)
        or str(uuid.UUID(materialization["bundleId"]))
        != materialization["bundleId"]
        or not isinstance(materialization.get("visualizeSkillContentHash"), str)
        or SHA256_PATTERN.fullmatch(materialization["visualizeSkillContentHash"])
        is None
        or any(
            value is not None and not isinstance(value, str)
            for value in (
                materialization.get("computerUseSkillVariant"),
                materialization.get("liveVisualizationSkillVariant"),
            )
        )
        or not isinstance(materialization.get("plugins"), list)
    ):
        raise RuntimeError("bundled marketplace materialization key is invalid")

    materialized_versions: dict[str, str] = {}
    for item in materialization["plugins"]:
        if not isinstance(item, dict) or set(item) != {"name", "version"}:
            raise RuntimeError("bundled marketplace package key is malformed")
        name = item.get("name")
        version = item.get("version")
        if (
            not isinstance(name, str)
            or name != name.casefold()
            or re.fullmatch(r"[a-z0-9._-]+", name) is None
            or name in materialized_versions
            or not isinstance(version, str)
            or _semver_key(version) is None
        ):
            raise RuntimeError("bundled marketplace package key is invalid")
        materialized_versions[name] = version
    if not BUNDLED_MARKETPLACE_REQUIRED_ACTIVE_PACKAGES.issubset(
        materialized_versions
    ):
        raise RuntimeError("bundled marketplace runtime cohort is incomplete")

    if (
        not isinstance(marketplace, dict)
        or marketplace.get("name") != BUNDLED_MARKETPLACE
        or not isinstance(marketplace.get("plugins"), list)
    ):
        raise RuntimeError("bundled marketplace descriptor is invalid")
    marketplace_projection: list[dict[str, Any]] = []
    marketplace_names: set[str] = set()
    for entry in marketplace["plugins"]:
        if not isinstance(entry, dict):
            raise RuntimeError("bundled marketplace descriptor entry is malformed")
        name = entry.get("name")
        source = entry.get("source")
        policy = entry.get("policy")
        if (
            not isinstance(name, str)
            or name in marketplace_names
            or not isinstance(source, dict)
            or source.get("source") != "local"
            or source.get("path") != f"./plugins/{name}"
            or not isinstance(policy, dict)
            or policy.get("installation") != "AVAILABLE"
            or policy.get("authentication") not in {"ON_INSTALL", "ON_USE"}
        ):
            raise RuntimeError("bundled marketplace descriptor entry is invalid")
        marketplace_names.add(name)
        marketplace_projection.append(
            {
                "name": name,
                "source": {"source": "local", "path": f"./plugins/{name}"},
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": policy["authentication"],
                },
            }
        )
    if marketplace_names != set(materialized_versions):
        raise RuntimeError("bundled marketplace descriptor cohort is incomplete")
    marketplace_projection.sort(key=lambda item: item["name"])

    try:
        source_entries = sorted(
            plugins_root.iterdir(), key=lambda path: path.name.casefold()
        )
    except OSError as exc:
        raise RuntimeError("bundled marketplace package cohort is unreadable") from exc
    if (
        any(path.is_symlink() or not path.is_dir() for path in source_entries)
        or {path.name.casefold() for path in source_entries}
        != set(materialized_versions)
    ):
        raise RuntimeError("bundled marketplace package cohort is malformed")

    packages: dict[str, dict[str, Any]] = {}
    cache_listing_before: dict[str, tuple[str, ...]] = {}
    for source_package in source_entries:
        package_key = source_package.name.casefold()
        plugin_version = materialized_versions[package_key]
        manifest = _read_json_object(
            source_package / ".codex-plugin" / "plugin.json"
        )
        if (
            manifest.get("name") != package_key
            or manifest.get("version") != plugin_version
        ):
            raise RuntimeError("bundled marketplace package identity is incoherent")
        authority_files = _bounded_package_authority_file_hashes(
            source_package, plugins_root
        )
        authority_digest = _sha256_text(_canonical_json(authority_files))
        cache_plugin_root = cache_marketplace_root / package_key
        cache_packages: list[str] = []
        cache_digests: dict[str, str] = {}
        if cache_plugin_root.exists():
            try:
                resolved_cache_plugin_root = cache_plugin_root.resolve(strict=True)
                if (
                    cache_plugin_root.is_symlink()
                    or not resolved_cache_plugin_root.is_dir()
                    or not resolved_cache_plugin_root.is_relative_to(
                        resolved_cache_marketplace
                    )
                ):
                    raise RuntimeError("bundled cache package root is invalid")
                cache_entries = sorted(
                    cache_plugin_root.iterdir(), key=lambda path: path.name.casefold()
                )
            except OSError as exc:
                raise RuntimeError("bundled cache package cohort is unreadable") from exc
            cache_listing_before[package_key] = tuple(
                path.name for path in cache_entries
            )
            for cache_package_path in cache_entries:
                cache_version = cache_package_path.name.casefold()
                if not cache_package_path.is_dir():
                    raise RuntimeError("bundled cache package cohort is malformed")
                resolved_cache_package = cache_package_path.resolve(strict=True)
                expected_version_path = (
                    cache_plugin_root / plugin_version
                ).resolve(strict=False)
                if cache_package_path.is_symlink():
                    if (
                        package_key != "chrome"
                        or cache_version != "latest"
                        or resolved_cache_package != expected_version_path
                    ):
                        raise RuntimeError("bundled cache alias is invalid")
                elif not resolved_cache_package.is_relative_to(
                    resolved_cache_plugin_root
                ):
                    raise RuntimeError("bundled cache package escapes its root")
                cache_manifest = _read_json_object(
                    cache_package_path / ".codex-plugin" / "plugin.json"
                )
                if (
                    cache_manifest.get("name") != package_key
                    or cache_manifest.get("version") != plugin_version
                    or (
                        cache_version != plugin_version.casefold()
                        and not (package_key == "chrome" and cache_version == "latest")
                    )
                ):
                    raise RuntimeError("bundled cache package identity is incoherent")
                cache_files = _bounded_package_authority_file_hashes(
                    resolved_cache_package, cache_marketplace_root
                )
                if cache_files != authority_files:
                    raise RuntimeError(
                        "bundled marketplace and cache authority content differ"
                    )
                cache_package = (
                    f"{BUNDLED_MARKETPLACE}/{package_key}/{cache_version}"
                )
                cache_packages.append(cache_package)
                cache_digests[cache_package] = authority_digest
        else:
            cache_listing_before[package_key] = ()
        packages[package_key] = {
            "plugin_name": package_key,
            "plugin_version": plugin_version,
            "bundle_package": f"plugins/{package_key}",
            "cache_packages": sorted(cache_packages),
            "authority_file_hashes": authority_files,
            "bundle_authority_sha256": authority_digest,
            "cache_authority_sha256s": dict(sorted(cache_digests.items())),
        }

    try:
        if (
            materialization_key_path.read_bytes() != materialization_bytes
            or marketplace_path.read_bytes() != marketplace_bytes
            or tuple(
                path.name
                for path in sorted(
                    plugins_root.iterdir(), key=lambda path: path.name.casefold()
                )
            )
            != tuple(path.name for path in source_entries)
        ):
            raise RuntimeError("bundled marketplace changed during capture")
        for package_key, before in cache_listing_before.items():
            cache_plugin_root = cache_marketplace_root / package_key
            after = (
                tuple(
                    path.name
                    for path in sorted(
                        cache_plugin_root.iterdir(),
                        key=lambda path: path.name.casefold(),
                    )
                )
                if cache_plugin_root.exists()
                else ()
            )
            if after != before:
                raise RuntimeError("bundled cache changed during capture")
    except OSError as exc:
        raise RuntimeError("bundled marketplace changed during capture") from exc

    packages = dict(sorted(packages.items()))
    result = {
        "schema_version": BUNDLED_MARKETPLACE_SCHEMA,
        "materialization_version": 1,
        "app_version": materialization["appVersion"],
        "bundle_id": materialization["bundleId"],
        "materialization_key_sha256": hashlib.sha256(
            materialization_bytes
        ).hexdigest().upper(),
        "marketplace_projection_sha256": _sha256_text(
            _canonical_json(marketplace_projection)
        ),
        "packages": packages,
    }
    result["authority_projection_sha256"] = _sha256_text(
        _canonical_json(_bundled_marketplace_projection(result))
    )
    if not _valid_bundled_marketplace_origin(result):
        raise RuntimeError("bundled marketplace authority projection is invalid")
    return result


def _primary_runtime_root() -> Path:
    return Path.home() / PRIMARY_RUNTIME_DEFAULT_RELATIVE


def _primary_runtime_bundle_origin(
    codex_home: Path, *, runtime_root: Path | None = None
) -> dict[str, Any]:
    """Record byte-coherent authority from the canonical detached runtime tree."""

    bundle_root = runtime_root or _primary_runtime_root()
    runtime_json_path = bundle_root / "runtime.json"
    bundle_packages_root = bundle_root / PRIMARY_RUNTIME_PACKAGES_RELATIVE
    cache_root = codex_home / "plugins" / "cache"
    cache_marketplace_root = cache_root / PRIMARY_RUNTIME_MARKETPLACE
    try:
        resolved_bundle_root = bundle_root.resolve(strict=True)
        resolved_packages_root = bundle_packages_root.resolve(strict=True)
        resolved_cache_root = cache_root.resolve(strict=True)
        resolved_cache_marketplace = cache_marketplace_root.resolve(strict=True)
        if (
            bundle_root.is_symlink()
            or bundle_packages_root.is_symlink()
            or cache_root.is_symlink()
            or cache_marketplace_root.is_symlink()
            or not resolved_bundle_root.is_dir()
            or not resolved_packages_root.is_dir()
            or not resolved_packages_root.is_relative_to(resolved_bundle_root)
            or not resolved_cache_marketplace.is_dir()
            or not resolved_cache_marketplace.is_relative_to(resolved_cache_root)
            or runtime_json_path.is_symlink()
            or not runtime_json_path.is_file()
        ):
            raise RuntimeError("primary runtime bundle path is invalid")
        runtime_bytes_before = runtime_json_path.read_bytes()
        runtime_data = json.loads(runtime_bytes_before.decode("utf-8-sig"))
        runtime_bytes_after = runtime_json_path.read_bytes()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("primary runtime bundle metadata is unreadable") from exc
    if runtime_bytes_before != runtime_bytes_after or not isinstance(runtime_data, dict):
        raise RuntimeError("primary runtime bundle metadata changed during capture")
    bundle_version = runtime_data.get("bundleVersion")
    bundle_format_version = runtime_data.get("bundleFormatVersion")
    bundled_plugins = runtime_data.get("bundledPlugins")
    if (
        not isinstance(bundle_version, str)
        or _semver_key(bundle_version) is None
        or not isinstance(bundle_format_version, int)
        or isinstance(bundle_format_version, bool)
        or bundle_format_version < 1
        or not isinstance(bundled_plugins, list)
        or any(not isinstance(item, str) or not item for item in bundled_plugins)
        or len(bundled_plugins) != len(set(bundled_plugins))
        or PRIMARY_RUNTIME_BUNDLED_PLUGIN not in bundled_plugins
    ):
        raise RuntimeError("primary runtime bundle metadata is invalid")

    try:
        bundle_entries = sorted(
            bundle_packages_root.iterdir(), key=lambda path: path.name.casefold()
        )
    except OSError as exc:
        raise RuntimeError("primary runtime bundle package cohort is unreadable") from exc
    if not bundle_entries:
        raise RuntimeError("primary runtime bundle package cohort is empty")
    packages: dict[str, dict[str, Any]] = {}
    for bundle_package_root in bundle_entries:
        if bundle_package_root.is_symlink() or not bundle_package_root.is_dir():
            raise RuntimeError("primary runtime bundle package cohort is malformed")
        package_key = bundle_package_root.name.casefold()
        if (
            not re.fullmatch(r"[a-z0-9._-]+", package_key)
            or package_key in packages
        ):
            raise RuntimeError("primary runtime bundle package identity is invalid")
        manifest_path = bundle_package_root / ".codex-plugin" / "plugin.json"
        manifest = _read_json_object(manifest_path)
        plugin_name = manifest.get("name")
        plugin_version = manifest.get("version")
        if (
            not isinstance(plugin_name, str)
            or plugin_name.casefold() != package_key
            or not isinstance(plugin_version, str)
            or plugin_version != bundle_version
        ):
            raise RuntimeError("primary runtime bundle package version is incoherent")
        cache_package = (
            cache_marketplace_root / package_key / bundle_version
        )
        bundle_files = _bounded_package_authority_file_hashes(
            bundle_package_root, bundle_packages_root
        )
        cache_files = _bounded_package_authority_file_hashes(
            cache_package, cache_marketplace_root
        )
        if bundle_files != cache_files:
            raise RuntimeError(
                "primary runtime bundle and cache authority content differ"
            )
        authority_sha256 = _sha256_text(_canonical_json(bundle_files))
        packages[package_key] = {
            "plugin_name": plugin_name,
            "plugin_version": plugin_version,
            "bundle_package": (
                f"{PRIMARY_RUNTIME_PACKAGES_RELATIVE.as_posix()}/{package_key}"
            ),
            "cache_package": (
                f"{PRIMARY_RUNTIME_MARKETPLACE}/{package_key}/{bundle_version}"
            ),
            "authority_file_hashes": bundle_files,
            "bundle_authority_sha256": authority_sha256,
            "cache_authority_sha256": authority_sha256,
        }
    packages = dict(sorted(packages.items()))
    if set(packages) != PRIMARY_RUNTIME_REQUIRED_PACKAGES:
        raise RuntimeError("primary runtime bundle package cohort is incomplete")
    return {
        "schema_version": PRIMARY_RUNTIME_BUNDLE_SCHEMA,
        "bundle_format_version": bundle_format_version,
        "bundle_version": bundle_version,
        "runtime_json_sha256": hashlib.sha256(runtime_bytes_before).hexdigest().upper(),
        "authority_projection_sha256": _sha256_text(_canonical_json(packages)),
        "packages": packages,
    }


def _plugin_package_origins(
    codex_home: Path, packages: set[str]
) -> dict[str, dict[str, Any]]:
    """Project updater-bound origin plus exact observed package byte identities."""

    cache_root = codex_home / "plugins" / "cache"
    result: dict[str, dict[str, Any]] = {}
    for package in sorted(packages):
        normalized = _normalized_plugin_package(package)
        if normalized is None or not normalized.startswith(
            CURATED_PLUGIN_CACHE_MARKETPLACE + "/"
        ):
            continue
        parts = normalized.split("/")
        package_root = cache_root / Path(package)
        version_receipt_path = package_root / REMOTE_PLUGIN_INSTALL_RECEIPT
        plugin_root = cache_root / parts[0] / parts[1]
        root_receipt_path = plugin_root / REMOTE_PLUGIN_INSTALL_RECEIPT
        if version_receipt_path.is_file() and not version_receipt_path.is_symlink():
            receipt_path = version_receipt_path
            expected_schema = 2
            attestation_mode = "version-package-v2"
        elif root_receipt_path.is_file() and not root_receipt_path.is_symlink():
            receipt_path = root_receipt_path
            expected_schema = 1
            attestation_mode = "plugin-root-v1"
        else:
            continue
        try:
            resolved_cache_root = cache_root.resolve(strict=True)
            resolved_package_root = package_root.resolve(strict=True)
            resolved_plugin_root = plugin_root.resolve(strict=True)
            resolved_receipt = receipt_path.resolve(strict=True)
            expected_parent = (
                resolved_package_root
                if attestation_mode == "version-package-v2"
                else resolved_plugin_root
            )
            if (
                package_root.is_symlink()
                or plugin_root.is_symlink()
                or not resolved_package_root.is_relative_to(resolved_plugin_root)
                or not resolved_plugin_root.is_relative_to(resolved_cache_root)
                or resolved_receipt.parent != expected_parent
            ):
                continue
            receipt_bytes_before = receipt_path.read_bytes()
            receipt = json.loads(receipt_bytes_before.decode("utf-8-sig"))
            if not isinstance(receipt, dict):
                continue
        except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError):
            continue
        remote_plugin_id = receipt.get("remote_plugin_id")
        marketplace = receipt.get("marketplace") if expected_schema == 2 else parts[0]
        plugin_name = receipt.get("plugin_name") if expected_schema == 2 else parts[1]
        plugin_version = receipt.get("plugin_version") if expected_schema == 2 else parts[2]
        supplied_manifest_digest = (
            receipt.get("plugin_manifest_sha256") if expected_schema == 2 else None
        )
        supplied_package_digest = (
            receipt.get("package_sha256") if expected_schema == 2 else None
        )
        manifest_path = package_root / ".codex-plugin" / "plugin.json"
        try:
            manifest = _read_json_object(manifest_path)
        except RuntimeError:
            continue
        manifest_name = manifest.get("name")
        manifest_version = manifest.get("version")
        manifest_digest = _sha256_file(manifest_path)
        package_digest = _plugin_package_sha256(package_root, cache_root)
        try:
            receipt_bytes_after = receipt_path.read_bytes()
        except OSError:
            continue
        if receipt_bytes_before != receipt_bytes_after:
            continue
        receipt_digest = hashlib.sha256(receipt_bytes_before).hexdigest().upper()
        if (
            receipt.get("schema_version") != expected_schema
            or not isinstance(remote_plugin_id, str)
            or not re.fullmatch(r"[-A-Za-z0-9._:~]{8,256}", remote_plugin_id)
            or marketplace != parts[0]
            or not isinstance(plugin_name, str)
            or plugin_name != manifest_name
            or plugin_name.casefold() != parts[1]
            or not isinstance(plugin_version, str)
            or plugin_version != manifest_version
            or plugin_version != parts[2]
            or (
                expected_schema == 2
                and (
                    not isinstance(supplied_manifest_digest, str)
                    or not SHA256_PATTERN.fullmatch(supplied_manifest_digest)
                    or supplied_manifest_digest.upper() != manifest_digest.upper()
                    or not isinstance(supplied_package_digest, str)
                    or not SHA256_PATTERN.fullmatch(supplied_package_digest)
                    or supplied_package_digest.upper() != package_digest.upper()
                )
            )
            or not manifest_digest
            or not package_digest
            or not receipt_digest
        ):
            continue
        result[package] = {
            "attestation_mode": attestation_mode,
            "receipt_schema_version": expected_schema,
            "remote_plugin_id": remote_plugin_id,
            "marketplace": marketplace,
            "plugin_name": plugin_name,
            "plugin_version": plugin_version,
            "plugin_manifest_sha256": manifest_digest,
            "package_sha256": package_digest,
            "install_receipt_sha256": receipt_digest,
        }
    return result


def _remote_plugin_catalog_entries(
    codex_home: Path,
    origins: dict[str, dict[str, Any]],
    *,
    now: dt.datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Bind schema-v1 install receipts to one fresh catalog entry projection.

    The catalog is operational control-plane coherence, not package-content
    attestation. Schema-v2 install receipts remain the only supported content
    attestation. Snapshot filenames, whole-file hashes, and fetched timestamps
    do not become durable authority identity.
    """

    required = {
        package: origin
        for package, origin in origins.items()
        if origin.get("receipt_schema_version") == 1
    }
    if not required:
        return {}
    catalog_root = codex_home / REMOTE_PLUGIN_CATALOG_RELATIVE
    try:
        resolved_catalog_root = catalog_root.resolve(strict=True)
        if catalog_root.is_symlink() or not resolved_catalog_root.is_dir():
            raise RuntimeError("remote plugin catalog root is invalid")
        catalog_files = sorted(
            (
                path
                for path in catalog_root.iterdir()
                if path.suffix.casefold() == ".json"
            ),
            key=lambda path: path.name.casefold(),
        )
    except OSError as exc:
        raise RuntimeError("remote plugin catalog is unavailable") from exc
    if not catalog_files:
        raise RuntimeError("remote plugin catalog is empty")

    observed_at = (now or dt.datetime.now(dt.timezone.utc)).astimezone(
        dt.timezone.utc
    )
    snapshots: list[tuple[dt.datetime, str, list[Any]]] = []
    for path in catalog_files:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("remote plugin catalog snapshot path is invalid")
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(resolved_catalog_root):
                raise RuntimeError("remote plugin catalog snapshot escapes its root")
            before = path.read_bytes()
            payload = json.loads(before.decode("utf-8-sig"))
            after = path.read_bytes()
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("remote plugin catalog snapshot is unreadable") from exc
        if before != after:
            raise RuntimeError("remote plugin catalog changed during capture")
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or not isinstance(payload.get("plugins"), list)
        ):
            raise RuntimeError("remote plugin catalog snapshot is invalid")
        fetched = _parse_utc_timestamp(payload.get("fetched_at"))
        if fetched is None or fetched > observed_at + dt.timedelta(minutes=5):
            raise RuntimeError("remote plugin catalog timestamp is invalid")
        snapshots.append((fetched, str(payload["fetched_at"]), payload["plugins"]))

    newest = max(item[0] for item in snapshots)
    if observed_at - newest > REMOTE_PLUGIN_CATALOG_MAX_AGE:
        raise RuntimeError("remote plugin catalog snapshot is stale")
    current_snapshots = [item for item in snapshots if item[0] == newest]
    result: dict[str, dict[str, Any]] = {}
    for package, origin in sorted(required.items()):
        remote_plugin_id = str(origin["remote_plugin_id"])
        plugin_name = str(origin["plugin_name"]).casefold()
        candidates: list[tuple[str, dict[str, Any]]] = []
        for _, fetched_text, plugins in current_snapshots:
            for entry in plugins:
                if not isinstance(entry, dict):
                    raise RuntimeError("remote plugin catalog entry is malformed")
                entry_id = entry.get("id")
                entry_name = entry.get("name")
                if entry_id == remote_plugin_id or (
                    isinstance(entry_name, str)
                    and entry_name.casefold() == plugin_name
                ):
                    candidates.append((fetched_text, entry))
        if len(candidates) != 1:
            raise RuntimeError(
                "remote plugin catalog entry is absent or conflicting"
            )
        fetched_text, entry = candidates[0]
        release = entry.get("release")
        if not isinstance(release, dict):
            raise RuntimeError("remote plugin catalog release is unavailable")
        catalog_skills = release.get("skills")
        if catalog_skills is None:
            catalog_skills = []
        if not isinstance(catalog_skills, list):
            raise RuntimeError("remote plugin catalog skill surface is invalid")
        skill_names: list[str] = []
        for skill in catalog_skills:
            if not isinstance(skill, dict) or not isinstance(skill.get("name"), str):
                raise RuntimeError("remote plugin catalog skill entry is invalid")
            skill_names.append(skill["name"])
        if len(skill_names) != len(set(skill_names)):
            raise RuntimeError("remote plugin catalog skill surface is ambiguous")

        catalog_app_manifest = release.get("app_manifest")
        if catalog_app_manifest is None:
            catalog_apps: dict[str, Any] = {}
        elif (
            not isinstance(catalog_app_manifest, dict)
            or not isinstance(catalog_app_manifest.get("apps"), dict)
        ):
            raise RuntimeError("remote plugin catalog app surface is invalid")
        else:
            catalog_apps = catalog_app_manifest["apps"]
        apps: dict[str, str] = {}
        for app_name, app_config in catalog_apps.items():
            if (
                not isinstance(app_name, str)
                or not app_name
                or not isinstance(app_config, dict)
                or not isinstance(app_config.get("id"), str)
                or not app_config["id"]
            ):
                raise RuntimeError("remote plugin catalog app entry is invalid")
            apps[app_name] = app_config["id"]
        declared_app_ids = release.get("app_ids")
        if declared_app_ids is None:
            declared_app_ids = []
        if (
            not isinstance(declared_app_ids, list)
            or any(not isinstance(item, str) or not item for item in declared_app_ids)
            or sorted(set(declared_app_ids)) != sorted(set(apps.values()))
        ):
            raise RuntimeError("remote plugin catalog app identities are incoherent")

        package_root = codex_home / "plugins" / "cache" / Path(package)
        local_app_path = package_root / ".app.json"
        if local_app_path.is_file():
            local_apps_raw = _read_json_object(local_app_path).get("apps")
            if not isinstance(local_apps_raw, dict):
                raise RuntimeError("local plugin app surface is invalid")
            local_apps = {
                name: config.get("id") if isinstance(config, dict) else None
                for name, config in local_apps_raw.items()
            }
        else:
            local_apps = {}
        if set(local_apps) != set(apps) or any(
            not isinstance(local_id, str)
            or _canonical_app_identity(local_id)
            != _canonical_app_identity(apps[name])
            for name, local_id in local_apps.items()
        ):
            raise RuntimeError("remote catalog and local app identities differ")

        binding = {
            "schema_version": REMOTE_PLUGIN_CATALOG_ENTRY_SCHEMA,
            "remote_plugin_id": entry.get("id"),
            "plugin_name": str(entry.get("name") or "").casefold(),
            "plugin_version": release.get("version"),
            "status": entry.get("status"),
            "installation_policy": entry.get("installation_policy"),
            "apps": dict(sorted(apps.items())),
            "local_apps": dict(sorted(local_apps.items())),
            "skill_names": sorted(skill_names),
            "fetched_at": fetched_text,
        }
        binding["entry_projection_sha256"] = _sha256_text(
            _canonical_json(_catalog_entry_projection(binding))
        )
        if (
            binding["remote_plugin_id"] != remote_plugin_id
            or binding["plugin_name"] != plugin_name
            or binding["plugin_version"] != origin["plugin_version"]
            or binding["status"] != "AVAILABLE"
            or not _valid_remote_plugin_catalog_entries({package: binding})
        ):
            raise RuntimeError("remote plugin catalog entry binding is incoherent")
        result[package] = binding
    return result


def _plugin_capability_surfaces(codex_home: Path) -> dict[str, list[dict[str, str]]]:
    """Derive the bounded capability ID/kind surface of every cached package."""

    cache_root = codex_home / "plugins" / "cache"
    try:
        resolved_cache_root = cache_root.resolve(strict=True)
        if not resolved_cache_root.is_dir():
            raise RuntimeError("plugin cache root is unavailable")
        result: dict[str, list[dict[str, str]]] = {}
        for marketplace in sorted(cache_root.iterdir(), key=lambda path: path.name.casefold()):
            if not marketplace.is_dir():
                continue
            for plugin in sorted(marketplace.iterdir(), key=lambda path: path.name.casefold()):
                if not plugin.is_dir():
                    continue
                if plugin.name.casefold().startswith(PLUGIN_INSTALL_STAGING_PREFIX):
                    continue
                for version in sorted(plugin.iterdir(), key=lambda path: path.name.casefold()):
                    if not version.is_dir():
                        continue
                    resolved_version = version.resolve(strict=True)
                    if not resolved_version.is_relative_to(resolved_cache_root):
                        raise RuntimeError("plugin package escapes the configured cache root")
                    manifest_path = version / ".codex-plugin" / "plugin.json"
                    if not manifest_path.is_file():
                        continue
                    resolved_manifest = manifest_path.resolve(strict=True)
                    if not resolved_manifest.is_relative_to(resolved_cache_root):
                        raise RuntimeError("plugin manifest escapes the cache root")
                    manifest = _read_json_object(manifest_path)
                    plugin_name = manifest.get("name")
                    if not isinstance(plugin_name, str) or not plugin_name:
                        raise RuntimeError("plugin manifest identity is incomplete")
                    package = version.relative_to(cache_root).as_posix().casefold()
                    surface: set[tuple[str, str]] = {
                        ("plugin", f"plugin:{plugin_name}")
                    }

                    skills_root = version / "skills"
                    if skills_root.is_dir():
                        resolved_skills = skills_root.resolve(strict=True)
                        if not resolved_skills.is_relative_to(resolved_cache_root):
                            raise RuntimeError("plugin skills root escapes the cache root")
                        for skill in sorted(
                            skills_root.iterdir(), key=lambda path: path.name.casefold()
                        ):
                            skill_file = skill / "SKILL.md"
                            if not skill.is_dir() or not skill_file.is_file():
                                continue
                            resolved_skill = skill_file.resolve(strict=True)
                            if not resolved_skill.is_relative_to(resolved_cache_root):
                                raise RuntimeError("plugin skill escapes the cache root")
                            skill_name = _effective_skill_name(skill_file, skill.name)
                            surface.add(
                                ("skill", f"skill:{plugin_name}:{skill_name}")
                            )

                    app_path = version / ".app.json"
                    if app_path.is_file():
                        resolved_app = app_path.resolve(strict=True)
                        if not resolved_app.is_relative_to(resolved_cache_root):
                            raise RuntimeError("plugin app manifest escapes the cache root")
                        apps = _read_json_object(app_path).get("apps")
                        if not isinstance(apps, dict):
                            raise RuntimeError("plugin app manifest has no apps object")
                        for app_name in apps:
                            if not isinstance(app_name, str):
                                raise RuntimeError("plugin app name is not a string")
                            surface.add(
                                (
                                    "tool-family",
                                    f"tool-family:app:{_capability_safe_name(app_name)}",
                                )
                            )

                    mcp_path = version / ".mcp.json"
                    if mcp_path.is_file():
                        resolved_mcp = mcp_path.resolve(strict=True)
                        if not resolved_mcp.is_relative_to(resolved_cache_root):
                            raise RuntimeError("plugin MCP manifest escapes the cache root")
                        servers = _read_json_object(mcp_path).get("mcpServers")
                        if not isinstance(servers, dict):
                            raise RuntimeError("plugin MCP manifest has no mcpServers object")
                        for server_name in servers:
                            if not isinstance(server_name, str):
                                raise RuntimeError("plugin MCP name is not a string")
                            surface.add(
                                ("mcp", f"mcp:{_capability_safe_name(server_name)}")
                            )

                    result[package] = [
                        {"id": identifier, "kind": kind}
                        for kind, identifier in sorted(surface)
                    ]
        return dict(sorted(result.items()))
    except (OSError, RuntimeError, ValueError):
        raise


def _plugin_name_from_package(package: str) -> str:
    normalized = _normalized_plugin_package(package)
    if normalized is None:
        raise RuntimeError("plugin package identity is malformed")
    return normalized.split("/")[1]


def _plugin_app_control_surfaces(
    codex_home: Path,
    plugin_surfaces: dict[str, list[dict[str, str]]],
) -> dict[str, list[str]]:
    cache_root = (codex_home / "plugins" / "cache").resolve(strict=True)
    result: dict[str, set[str]] = {}
    for package in plugin_surfaces:
        app_path = cache_root / Path(package) / ".app.json"
        if not app_path.is_file():
            continue
        resolved = app_path.resolve(strict=True)
        if not resolved.is_relative_to(cache_root):
            raise RuntimeError("plugin app manifest escapes the cache root")
        apps = _read_json_object(resolved).get("apps")
        if not isinstance(apps, dict):
            raise RuntimeError("plugin app manifest has no apps object")
        for app_name, app_config in apps.items():
            if not isinstance(app_name, str) or not isinstance(app_config, dict):
                raise RuntimeError("plugin app control is malformed")
            app_id = app_config.get("id")
            if not isinstance(app_id, str) or not app_id:
                raise RuntimeError("plugin app control has no stable id")
            result.setdefault(app_id.casefold(), set()).add(
                f"tool-family:app:{_capability_safe_name(app_name)}"
            )
            identity_match = re.search(r"([a-f0-9]{32})$", app_id.casefold())
            if identity_match:
                suffix = identity_match.group(1)
                for alias in (f"asdk_app_{suffix}", f"connector_{suffix}"):
                    result.setdefault(alias, set()).add(
                        f"tool-family:app:{_capability_safe_name(app_name)}"
                    )
    return {key: sorted(value) for key, value in sorted(result.items())}


def _skill_control_capability_id(codex_home: Path, control_key: str) -> str:
    path = Path(control_key)
    fallback = path.parent.name if path.name.casefold() == "skill.md" else path.name
    skill_name = _effective_skill_name(path, fallback)
    cache_root = (codex_home / "plugins" / "cache").resolve(strict=False)
    resolved = path.resolve(strict=False)
    try:
        relative = resolved.relative_to(cache_root)
    except ValueError:
        return f"skill:{skill_name}"
    parts = relative.parts
    if len(parts) < 6 or parts[3].casefold() != "skills":
        raise RuntimeError("configured plugin skill path has an invalid package shape")
    manifest_path = cache_root.joinpath(*parts[:3], ".codex-plugin", "plugin.json")
    manifest = _read_json_object(manifest_path)
    plugin_name = manifest.get("name")
    if not isinstance(plugin_name, str) or not plugin_name:
        raise RuntimeError("configured plugin skill has no plugin identity")
    return f"skill:{plugin_name}:{skill_name}"


def _config_capability_surfaces(
    codex_home: Path,
    authority: dict[str, Any],
    plugin_surfaces: dict[str, list[dict[str, str]]],
) -> dict[str, dict[str, Any]]:
    descriptors = authority.get("control_descriptors")
    leaves = authority.get("projection_leaf_hashes")
    if not isinstance(descriptors, dict) or set(descriptors) != set(leaves or {}):
        raise RuntimeError("config control descriptors do not close over the projection")

    plugin_ids: dict[str, set[str]] = {}
    for package, capabilities in plugin_surfaces.items():
        plugin_name = _plugin_name_from_package(package)
        plugin_ids.setdefault(plugin_name, set()).update(
            str(item["id"]) for item in capabilities
        )
    app_ids = _plugin_app_control_surfaces(codex_home, plugin_surfaces)
    app_runtime_ids: set[str] = {"mcp:node_repl"}
    for plugin_name in APP_RUNTIME_PLUGIN_NAMES:
        app_runtime_ids.update(plugin_ids.get(plugin_name, set()))

    result: dict[str, dict[str, Any]] = {}
    for pointer, raw_descriptor in descriptors.items():
        if not isinstance(raw_descriptor, dict):
            raise RuntimeError("config control descriptor is malformed")
        descriptor = dict(raw_descriptor)
        kind = str(descriptor.get("control_kind") or "")
        key = str(descriptor.get("control_key") or "")
        capability_ids: set[str]
        required_ids: set[str]
        if kind == "plugin":
            plugin_name = key.split("@", 1)[0].casefold()
            capability_ids = set(plugin_ids.get(plugin_name, set()))
            required_ids = {f"plugin:{plugin_name}"} & capability_ids
        elif kind == "plugin_mcp":
            server_name = key.rsplit("/", 1)[-1]
            capability_ids = {f"mcp:{_capability_safe_name(server_name)}"}
            required_ids = set(capability_ids)
        elif kind in {"mcp", "mcp_runtime"}:
            capability_ids = {f"mcp:{_capability_safe_name(key)}"}
            required_ids = set(capability_ids)
        elif kind == "skill":
            capability_ids = {_skill_control_capability_id(codex_home, key)}
            required_ids = set(capability_ids)
        elif kind in {"app", "app_tool"}:
            app_key = key.split("/", 1)[0]
            capability_ids = set(app_ids.get(app_key.casefold(), []))
            required_ids = set(capability_ids)
        elif kind == "app_runtime":
            capability_ids = set(app_runtime_ids)
            required_ids = {"mcp:node_repl"}
        elif kind == "global_runtime":
            capability_ids = {"router:all"}
            required_ids = set(capability_ids)
        else:
            raise RuntimeError("config control kind is unsupported")
        if not capability_ids or not required_ids:
            raise RuntimeError(
                f"config control has no exact capability surface: {pointer}"
            )
        binding = {
            "change_class": descriptor["change_class"],
            "control_kind": kind,
            "control_key": key,
            "capability_ids": sorted(capability_ids),
            "required_capability_ids": sorted(required_ids),
        }
        if "enabled" in descriptor:
            binding["enabled"] = descriptor["enabled"]
        result[str(pointer)] = binding
    return dict(sorted(result.items()))


def capture_authority_receipt(codex_home: Path | None = None) -> dict[str, Any]:
    home = (codex_home or index.CODEX_HOME).resolve(strict=True)
    config_path = home / "config.toml"
    index._load_worker_runtime_bom(
        home / "capability-routing" / "worker-runtime-bom.json"
    )
    authority = config_fingerprint.capability_config_authority(config_path)
    rows = index._plugin_cache_inventory_rows(home)
    if rows is None:
        raise RuntimeError("plugin cache authority inventory is unavailable")
    surfaces = _plugin_capability_surfaces(home)
    config_surfaces = _config_capability_surfaces(home, authority, surfaces)
    packages = set(surfaces)
    rows = _filter_plugin_cache_inventory_rows(rows, packages)
    plugin_origins = _plugin_package_origins(home, packages)
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "config_projection_sha256": authority["sha256"],
        "config_leaf_hashes": authority["projection_leaf_hashes"],
        "config_capability_surfaces": config_surfaces,
        "plugin_cache_inventory_sha256": _sha256_text("\n".join(rows)),
        "plugin_cache_row_hashes": _plugin_row_hashes(rows),
        "plugin_capability_surfaces": surfaces,
        "plugin_package_manifests": _plugin_package_manifests(home, packages),
        "plugin_package_origins": plugin_origins,
        "remote_plugin_catalog_entries": _remote_plugin_catalog_entries(
            home, plugin_origins
        ),
        "bundled_marketplace_origin": _bundled_marketplace_origin(home),
        "primary_runtime_bundle_origin": _primary_runtime_bundle_origin(home),
        "required_source_hashes": _required_source_hashes(home),
        "app_identity": _app_identity(home, config_path),
    }
    receipt["snapshot_sha256"] = authority_snapshot_digest(receipt)
    if not _valid_receipt(receipt):
        raise RuntimeError("captured authority receipt is invalid")
    return receipt


def _bounded_recovery_state(state: dict[str, Any] | None) -> dict[str, Any]:
    value = state if isinstance(state, dict) else {}
    mismatches = value.get("source_hash_mismatches")
    mismatch_values = mismatches if isinstance(mismatches, list) else []
    dynamic = value.get("dynamic_authority")
    dynamic = dynamic if isinstance(dynamic, dict) else {}
    generation = value.get("authority_generation")
    generation = generation if isinstance(generation, dict) else {}
    return {
        "freshness_status": str(value.get("freshness_status") or "unknown")[:64],
        "source_hashes_verified": value.get("source_hashes_verified") is True,
        "static_source_hashes_verified": value.get("static_source_hashes_verified")
        is True,
        "dynamic_authority_status": str(
            value.get("dynamic_authority_status") or "unknown"
        )[:64],
        "worker_runtime_bom_status": str(
            value.get("worker_runtime_bom_status") or "unknown"
        )[:64],
        "generation_pointer_status": str(
            value.get("generation_pointer_status") or "unknown"
        )[:64],
        "authority_generation_id": str(generation.get("id") or "")[:64],
        "quarantined_package_count": len(dynamic.get("quarantined_packages", [])),
        "quarantined_capability_count": len(
            dynamic.get("quarantined_capability_ids", [])
        ),
        "source_hash_mismatches": [str(item)[:160] for item in mismatch_values[:32]],
        "entry_count": len(value.get("entries", []))
        if isinstance(value.get("entries"), list)
        else 0,
    }


def _bounded_hash(value: str) -> str:
    return value.upper() if SHA256_PATTERN.fullmatch(str(value or "")) else ""


def write_session_start_recovery_receipt(
    result: dict[str, Any],
    *,
    before_state: dict[str, Any] | None,
    after_state: dict[str, Any] | None,
    before_manifest_sha256: str,
    after_manifest_sha256: str,
    receipt_dir: Path | None = None,
    max_receipts: int = SESSION_START_RECEIPT_LIMIT,
    event_id: str | None = None,
    recorded_at: dt.datetime | None = None,
) -> Path:
    """Atomically persist one bounded SessionStart recovery outcome."""

    directory = receipt_dir or (
        index.CODEX_HOME / "capability-routing" / "recovery-receipts"
    )
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = recorded_at or dt.datetime.now(dt.timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
    timestamp = timestamp.astimezone(dt.timezone.utc)
    identifier = re.sub(r"[^A-Za-z0-9._-]+", "-", event_id or uuid.uuid4().hex)
    identifier = identifier.strip("-.")[:96] or uuid.uuid4().hex
    stamp = timestamp.strftime("%Y%m%dT%H%M%S.%fZ")
    destination = directory / f"session-start-recovery-{stamp}-{identifier}.json"
    bounded_result = result if isinstance(result, dict) else {}
    payload: dict[str, Any] = {
        "schema_version": SESSION_START_RECEIPT_SCHEMA,
        "recorded_at_utc": timestamp.isoformat().replace("+00:00", "Z"),
        "event_id": identifier,
        "status": str(bounded_result.get("status") or "error")[:32],
        "reason_code": str(
            bounded_result.get("reason_code") or "RECOVERY_RESULT_INVALID"
        )[:128],
        "before_manifest_sha256": _bounded_hash(before_manifest_sha256),
        "after_manifest_sha256": _bounded_hash(after_manifest_sha256),
        "before": _bounded_recovery_state(before_state),
        "after": _bounded_recovery_state(after_state),
    }
    for key, limit in (("snapshot_sha256", 64), ("error_type", 128)):
        value = bounded_result.get(key)
        if isinstance(value, str) and value:
            payload[key] = value[:limit]

    temporary = directory / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical_json(payload) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    retained = max(1, min(int(max_receipts), 256))
    receipts = sorted(directory.glob("session-start-recovery-*.json"))
    for expired in receipts[:-retained]:
        if expired != destination:
            expired.unlink(missing_ok=True)
    return destination


def stable_two_read(
    reader: Callable[[], dict[str, Any]],
    *,
    settle_seconds: float = 2.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any] | None, str]:
    try:
        first = reader()
        sleeper(settle_seconds)
        second = reader()
    except Exception:
        return None, "AUTHORITY_SNAPSHOT_CAPTURE_FAILED"
    if _canonical_json(first) != _canonical_json(second):
        return None, "AUTHORITY_SNAPSHOT_UNSTABLE"
    if not _valid_receipt(second):
        return None, "INVALID_CURRENT_AUTHORITY_RECEIPT"
    return second, "AUTHORITY_SNAPSHOT_STABLE"


def authority_is_quiet(codex_home: Path, quiet_seconds: float = 15.0) -> bool:
    paths = [codex_home / "config.toml"]
    cache_root = codex_home / "plugins" / "cache"
    if cache_root.is_dir():
        paths.extend(
            path
            for path in cache_root.rglob("*")
            if path.is_file()
            and path.name.casefold() != ".codex-remote-plugin-install.json"
        )
    catalog_root = codex_home / REMOTE_PLUGIN_CATALOG_RELATIVE
    if catalog_root.is_dir():
        paths.extend(path for path in catalog_root.rglob("*") if path.is_file())
    newest = 0
    try:
        for path in paths:
            newest = max(newest, path.stat().st_mtime_ns)
    except OSError:
        return False
    return time.time_ns() - newest >= int(quiet_seconds * 1_000_000_000)


def wait_for_quiet(
    checker: Callable[[], bool],
    *,
    timeout_seconds: float = 45.0,
    poll_seconds: float = 2.0,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> bool:
    deadline = clock() + max(0.0, timeout_seconds)
    while True:
        if checker():
            return True
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        sleeper(min(max(0.01, poll_seconds), remaining))


class StaticMutex:
    def __init__(self, *, acquired: bool, abandoned: bool = False) -> None:
        self.acquired = acquired
        self.abandoned = abandoned

    def __enter__(self) -> "StaticMutex":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None


def null_mutex() -> StaticMutex:
    return StaticMutex(acquired=True)


class WindowsNamedMutex:
    WAIT_OBJECT_0 = 0x00000000
    WAIT_ABANDONED = 0x00000080
    WAIT_TIMEOUT = 0x00000102

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self.name = name
        self.handle: int | None = None
        self.acquired = False
        self.abandoned = False
        self._owned = False

    def __enter__(self) -> "WindowsNamedMutex":
        if os.name != "nt":
            return self
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            return self
        self.handle = int(handle)
        result = kernel32.WaitForSingleObject(handle, 0)
        if result == self.WAIT_OBJECT_0:
            self.acquired = True
            self._owned = True
        elif result == self.WAIT_ABANDONED:
            self.abandoned = True
            self._owned = True
        elif result != self.WAIT_TIMEOUT:
            self.abandoned = True
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is None or os.name != "nt":
            return None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        try:
            if self._owned:
                kernel32.ReleaseMutex(ctypes.c_void_p(self.handle))
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(self.handle))
            self.handle = None
        return None


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def resolve_receipt_codex_exe(
    receipt: dict[str, Any], *, local_app_data: Path | None = None
) -> Path:
    if not _valid_receipt(receipt):
        raise RuntimeError("authority receipt is invalid")
    return resolve_app_identity_codex_exe(
        receipt["app_identity"], local_app_data=local_app_data
    )


def resolve_app_identity_codex_exe(
    app_identity: dict[str, Any], *, local_app_data: Path | None = None
) -> Path:
    if (
        not isinstance(app_identity, dict)
        or app_identity.get("coherent") is not True
        or not isinstance(app_identity.get("cli_id"), str)
        or not app_identity["cli_id"]
        or SHA256_PATTERN.fullmatch(
            str(app_identity.get("cli_executable_sha256") or "")
        )
        is None
    ):
        raise RuntimeError("current Codex CLI identity is invalid")
    base = local_app_data or Path(os.environ.get("LOCALAPPDATA", ""))
    if not str(base):
        raise RuntimeError("LOCALAPPDATA is unavailable")
    cli_id = app_identity["cli_id"]
    bin_root = (base / "OpenAI" / "Codex" / "bin").resolve(strict=True)
    candidate = (bin_root / cli_id / "codex.exe").resolve(strict=True)
    if not candidate.is_relative_to(bin_root) or not candidate.is_file():
        raise RuntimeError("receipt-bound Codex CLI path is invalid")
    expected = str(app_identity["cli_executable_sha256"]).upper()
    if _sha256_file(candidate) != expected:
        raise RuntimeError("receipt-bound Codex CLI hash does not match")
    return candidate


def _run_json_command(command: list[str], timeout_seconds: float) -> str:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        raise TimeoutError("live capability inventory timed out") from exc
    if process.returncode != 0:
        raise RuntimeError(
            f"live capability inventory failed ({process.returncode}): {stderr.strip()}"
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("live capability inventory returned invalid JSON") from exc
    if not isinstance(payload, (list, dict)):
        raise RuntimeError("live capability inventory returned an invalid JSON root")
    return stdout


def prepare_live_inventories(
    receipt: dict[str, Any],
    *,
    local_app_data: Path | None = None,
    timeout_seconds: float = 20.0,
    command_runner: Callable[[list[str], float], str] = _run_json_command,
) -> None:
    codex_exe = resolve_receipt_codex_exe(
        receipt, local_app_data=local_app_data
    )
    for arguments in (("mcp", "list", "--json"), ("plugin", "list", "--json")):
        raw = command_runner([str(codex_exe), *arguments], timeout_seconds)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("live capability inventory returned invalid JSON") from exc
        if not isinstance(payload, (list, dict)):
            raise RuntimeError("live capability inventory returned an invalid JSON root")


def prepare_current_live_inventories(
    codex_home: Path,
    *,
    timeout_seconds: float = 20.0,
    command_runner: Callable[[list[str], float], str] = _run_json_command,
) -> None:
    """Refresh inventories from the catalog-independent exact current CLI."""

    identity = _app_identity(codex_home, codex_home / "config.toml")
    codex_exe = resolve_app_identity_codex_exe(identity)
    for arguments in (("mcp", "list", "--json"), ("plugin", "list", "--json")):
        raw = command_runner([str(codex_exe), *arguments], timeout_seconds)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("live capability inventory returned invalid JSON") from exc
        if not isinstance(payload, (list, dict)):
            raise RuntimeError("live capability inventory returned an invalid JSON root")


def run_builder_candidate(
    expected_snapshot_sha256: str,
    manifest_path: Path,
    *,
    authority_receipt: dict[str, Any],
    previous_generation_id: str | None,
    generation_sequence: int,
    authority_transaction_id: str,
    promotion_reason: str,
    codex_home: Path | None = None,
    timeout_seconds: float = 100.0,
) -> Path:
    home = (codex_home or index.CODEX_HOME).resolve(strict=True)
    builder = (
        home
        / "capability-routing"
        / "builder"
        / "build_canonical_capability_manifest.ps1"
    )
    references_root = builder.parent / "references"
    inventory_paths = {
        "SkillsCsvPath": references_root / "universal-skills-2026-07-25.csv",
        "PluginsCsvPath": references_root / "universal-plugins-2026-07-25.csv",
        "ToolsCsvPath": (
            references_root
            / "universal-tool-families-and-mcps-2026-07-25.csv"
        ),
    }
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell or not builder.is_file():
        raise RuntimeError("stable capability manifest builder is unavailable")
    try:
        resolved_references_root = references_root.resolve(strict=True)
        if references_root.is_symlink() or not resolved_references_root.is_dir():
            raise RuntimeError("builder inventory reference root is invalid")
        for label, path in inventory_paths.items():
            resolved = path.resolve(strict=True)
            if (
                path.is_symlink()
                or not resolved.is_file()
                or resolved.parent != resolved_references_root
            ):
                raise RuntimeError(
                    f"builder inventory reference is invalid: {label}"
                )
    except OSError as exc:
        raise RuntimeError("builder inventory references are unavailable") from exc
    codex_exe = resolve_receipt_codex_exe(authority_receipt)
    candidate = manifest_path.with_name(
        f".{manifest_path.name}.recovery-{os.getpid()}-{uuid.uuid4().hex}.json"
    )
    command = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(builder),
        "-CodexHome",
        str(home),
        "-CodexExe",
        str(codex_exe),
        "-SkillsCsvPath",
        str(inventory_paths["SkillsCsvPath"]),
        "-PluginsCsvPath",
        str(inventory_paths["PluginsCsvPath"]),
        "-ToolsCsvPath",
        str(inventory_paths["ToolsCsvPath"]),
        "-ManifestPath",
        str(candidate),
        "-ExpectedAuthoritySnapshotSha256",
        expected_snapshot_sha256,
        "-GenerationSequence",
        str(generation_sequence),
        "-AuthorityTransactionId",
        authority_transaction_id,
        "-PromotionReason",
        promotion_reason,
    ]
    if previous_generation_id:
        command.extend(["-PreviousGenerationId", previous_generation_id])
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        _best_effort_unlink(candidate)
        raise TimeoutError("capability manifest builder timed out") from exc
    if process.returncode != 0:
        _best_effort_unlink(candidate)
        raise RuntimeError(
            f"capability manifest builder failed ({process.returncode}): {stderr.strip()}"
        )
    if not candidate.is_file() or not stdout.strip():
        _best_effort_unlink(candidate)
        raise RuntimeError("capability manifest builder returned no candidate receipt")
    return candidate


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _best_effort_unlink(path: Path | None) -> bool:
    """Remove a disposable candidate without changing the transaction outcome."""

    if path is None:
        return True
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _stable_regular_bytes(path: Path, label: str) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        if path.is_symlink() or not resolved.is_file():
            raise OSError("not a regular file")
        before = resolved.stat()
        first = resolved.read_bytes()
        second = resolved.read_bytes()
        after = resolved.stat()
    except OSError as exc:
        raise RuntimeError(f"{label} is unavailable") from exc
    if (
        first != second
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise RuntimeError(f"{label} changed during verification")
    return first


def _worker_bom_promotion_receipt(
    codex_home: Path,
    previous: dict[str, Any],
    current: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """Verify one immutable promoter receipt and the complete live worker closure."""

    target = codex_home / "capability-routing" / "worker-runtime-bom.json"
    schema_path = codex_home / "capability-routing" / "worker-runtime-bom.schema.json"
    promoter_path = (
        codex_home / "capability-routing" / "promote_worker_runtime_bom.py"
    )
    config_path = codex_home / "config.toml"
    old_sha = str(
        previous["required_source_hashes"][index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY]
    ).lower()
    current_sha = str(
        current["required_source_hashes"][index.WORKER_RUNTIME_BOM_SOURCE_HASH_KEY]
    ).lower()
    try:
        target_raw = _stable_regular_bytes(target, "worker-runtime BOM")
        schema_raw = _stable_regular_bytes(schema_path, "worker-runtime BOM schema")
        promoter_raw = _stable_regular_bytes(
            promoter_path, "worker-runtime BOM promoter"
        )
        config_raw = _stable_regular_bytes(config_path, "Codex configuration")
        config = tomllib.loads(config_raw.decode("utf-8-sig"))
        bom, actual_sha = index._load_worker_runtime_bom(
            target, expected_sha256=current_sha
        )
        config_projection = {
            server_id: index._worker_server_projection(config, server_id)[0]
            for server_id in sorted(index.REQUIRED_WORKER_RUNTIME_SERVER_IDS)
        }
        servers = config.get("mcp_servers")
        gateway_server = (
            servers.get(index.GATEWAY_CONFIG_SERVER_ID)
            if isinstance(servers, dict)
            else None
        )
        if gateway_server != {"url": index.GATEWAY_CONFIG_URL}:
            raise RuntimeError("Codex stability gateway configuration is invalid")
        config_projection[index.GATEWAY_CONFIG_SERVER_ID] = gateway_server
    except (
        OSError,
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
        RuntimeError,
        index.CapabilityDataError,
    ):
        return None, "WORKER_RUNTIME_BOM_CURRENT_CLOSURE_INVALID"
    if (
        hashlib.sha256(target_raw).hexdigest() != current_sha
        or actual_sha != current_sha
        or set(bom.get("runtimes", {}))
        != index.REQUIRED_WORKER_RUNTIME_SERVER_IDS
    ):
        return None, "WORKER_RUNTIME_BOM_CURRENT_CLOSURE_INVALID"
    expected_config_sha = index._worker_projection_sha256(config_projection)
    expected_schema_sha = hashlib.sha256(schema_raw).hexdigest()
    expected_promoter_sha = str(
        current["required_source_hashes"][
            index.WORKER_RUNTIME_BOM_PROMOTER_SOURCE_HASH_KEY
        ]
    ).lower()
    if hashlib.sha256(promoter_raw).hexdigest() != expected_promoter_sha:
        return None, "WORKER_RUNTIME_BOM_PROMOTION_RECEIPT_MISMATCH"
    state_root = target.parent / WORKER_BOM_PROMOTER_STATE_DIRECTORY
    transactions = state_root / "transactions"
    if (
        not transactions.is_dir()
        or state_root.is_symlink()
        or transactions.is_symlink()
    ):
        return None, "WORKER_RUNTIME_BOM_PROMOTION_RECEIPT_MISSING"
    matching: list[dict[str, Any]] = []
    corrupt = False
    candidate_seen = False
    receipt_keys = {
        "candidate_sha256",
        "completed_at",
        "config_sha256",
        "error",
        "expected_target_sha256",
        "journal_sha256",
        "outcome",
        "promoter_sha256",
        "receipt_sha256",
        "schema_sha256",
        "schema_version",
        "target",
        "transaction_id",
    }
    journal_keys = {
        "candidate_sha256",
        "config_sha256",
        "error",
        "expected_target_sha256",
        "phase",
        "promoter_sha256",
        "schema_sha256",
        "schema_version",
        "target",
        "transaction_id",
        "updated_at",
    }
    try:
        transaction_dirs = sorted(
            transactions.iterdir(), key=lambda path: path.name.casefold()
        )
    except OSError:
        return None, "WORKER_RUNTIME_BOM_PROMOTION_RECEIPT_CORRUPT"
    for transaction_dir in transaction_dirs:
        receipt_path = transaction_dir / "receipt.json"
        if (
            transaction_dir.is_symlink()
            or not transaction_dir.is_dir()
            or not receipt_path.exists()
        ):
            continue
        try:
            receipt_raw = _stable_regular_bytes(
                receipt_path, "worker-runtime BOM promotion receipt"
            )
            receipt = json.loads(receipt_raw.decode("utf-8-sig"))
            if (
                not isinstance(receipt, dict)
                or set(receipt) != receipt_keys
                or receipt_raw
                != (
                    _canonical_json(receipt).encode("utf-8") + b"\n"
                )
            ):
                raise RuntimeError("receipt shape is invalid")
        except (RuntimeError, UnicodeDecodeError, json.JSONDecodeError):
            corrupt = True
            continue
        if str(receipt.get("candidate_sha256") or "").lower() != current_sha:
            continue
        candidate_seen = True
        try:
            journal_path = transaction_dir / "journal.json"
            journal_raw = _stable_regular_bytes(
                journal_path, "worker-runtime BOM promotion journal"
            )
            journal = json.loads(journal_raw.decode("utf-8-sig"))
            receipt_body = dict(receipt)
            claimed_receipt_sha = receipt_body.pop("receipt_sha256")
            calculated_receipt_sha = hashlib.sha256(
                WORKER_BOM_PROMOTION_RECEIPT_DOMAIN
                + _canonical_json(receipt_body).encode("utf-8")
                + b"\n"
            ).hexdigest()
            receipt_target = Path(str(receipt.get("target") or "")).resolve(
                strict=True
            )
            if (
                not isinstance(journal, dict)
                or set(journal) != journal_keys
                or journal_raw != _canonical_json(journal).encode("utf-8") + b"\n"
                or transaction_dir.name != receipt.get("transaction_id")
                or journal.get("transaction_id") != receipt.get("transaction_id")
                or receipt.get("schema_version")
                != WORKER_BOM_PROMOTION_RECEIPT_SCHEMA
                or journal.get("schema_version")
                != WORKER_BOM_PROMOTION_JOURNAL_SCHEMA
                or receipt.get("outcome") != "completed"
                or receipt.get("error") is not None
                or journal.get("phase") != "completed"
                or journal.get("error") is not None
                or receipt.get("completed_at") != journal.get("updated_at")
                or receipt_target != target.resolve(strict=True)
                or receipt.get("expected_target_sha256") != old_sha
                or receipt.get("config_sha256") != expected_config_sha
                or receipt.get("promoter_sha256") != expected_promoter_sha
                or receipt.get("schema_sha256") != expected_schema_sha
                or receipt.get("journal_sha256")
                != hashlib.sha256(journal_raw).hexdigest()
                or claimed_receipt_sha != calculated_receipt_sha
                or any(
                    journal.get(key) != receipt.get(key)
                    for key in (
                        "candidate_sha256",
                        "config_sha256",
                        "expected_target_sha256",
                        "promoter_sha256",
                        "schema_sha256",
                        "target",
                    )
                )
            ):
                raise RuntimeError("receipt binding is invalid")
        except (
            OSError,
            RuntimeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            corrupt = True
            continue
        matching.append(receipt)
    if len(matching) != 1:
        if corrupt:
            return None, "WORKER_RUNTIME_BOM_PROMOTION_RECEIPT_CORRUPT"
        if candidate_seen:
            return None, "WORKER_RUNTIME_BOM_PROMOTION_RECEIPT_MISMATCH"
        return None, "WORKER_RUNTIME_BOM_PROMOTION_RECEIPT_MISSING"
    gateway_runtime_verified = False
    for server_id in sorted(index.REQUIRED_WORKER_RUNTIME_SERVER_IDS):
        if not index._gateway_managed_upstream_configured(
            server_id,
            expected_bom_sha256=current_sha,
            verify_current_bytes=True,
            gateway_runtime_verified=gateway_runtime_verified,
            require_process_generation=False,
        ):
            return None, "WORKER_RUNTIME_BOM_CURRENT_CLOSURE_INVALID"
        gateway_runtime_verified = True
    return matching[0], "WORKER_RUNTIME_BOM_PROMOTION_RECEIPT_CURRENT"


def attempt_recovery(
    *,
    manifest_path: Path | None = None,
    codex_home: Path | None = None,
    current_state: dict[str, Any] | None = None,
    snapshot_reader: Callable[[], dict[str, Any]] | None = None,
    builder_runner: Callable[[str, Path], Path] | None = None,
    inventory_preparer: Callable[[dict[str, Any]], None] | None = None,
    mutex_factory: Callable[[], Any] | None = None,
    quiet_checker: Callable[[], bool] | None = None,
    quiet_timeout_seconds: float = 45.0,
    quiet_poll_seconds: float = 2.0,
    prebuild_timeout_seconds: float = 60.0,
    settle_seconds: float = 2.0,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    home = (codex_home or index.CODEX_HOME).resolve(strict=False)
    target = manifest_path or (home / "capability-routing" / "active-capabilities.json")
    factory = mutex_factory or WindowsNamedMutex
    with factory() as mutex:
        if getattr(mutex, "abandoned", False):
            return {"status": "denied", "reason_code": "RECOVERY_MUTEX_ABANDONED"}
        if not getattr(mutex, "acquired", False):
            return {"status": "noop", "reason_code": "RECOVERY_MUTEX_BUSY"}
        state = current_state or index.load_active_capabilities(target)
        if (
            state.get("freshness_status") in index.FRESH_STATES
            and state.get("freshness_status") != "degraded"
            and state.get("source_hashes_verified") is True
        ):
            return {"status": "noop", "reason_code": "MANIFEST_ALREADY_FRESH"}
        mismatches = {str(item) for item in state.get("source_hash_mismatches", [])}
        if not mismatches or not mismatches.issubset(RECOVERABLE_MISMATCHES):
            return {"status": "denied", "reason_code": "UNRECOVERABLE_SOURCE_DRIFT"}
        state_source = str(state.get("source") or "").strip()
        manifest_source = Path(state_source) if state_source else target
        try:
            resolved_routing_dir = target.parent.resolve(strict=True)
            resolved_manifest_source = manifest_source.resolve(strict=True)
        except OSError:
            return {"status": "denied", "reason_code": "AUTHORITY_BASELINE_MISSING"}
        if (
            resolved_manifest_source != target.resolve(strict=False)
            and not resolved_manifest_source.is_relative_to(resolved_routing_dir)
        ):
            return {"status": "denied", "reason_code": "AUTHORITY_BASELINE_UNTRUSTED"}
        manifest = _read_manifest(resolved_manifest_source)
        baseline = manifest.get("authority_receipt")
        if not isinstance(baseline, dict):
            return {"status": "denied", "reason_code": "AUTHORITY_BASELINE_MISSING"}
        if not _valid_receipt(baseline):
            return {"status": "denied", "reason_code": "AUTHORITY_BASELINE_INVALID"}
        deadline = clock() + max(0.0, prebuild_timeout_seconds)

        def remaining(limit: float) -> float:
            return max(0.0, min(limit, deadline - clock()))

        quiet = quiet_checker or (lambda: authority_is_quiet(home))
        if not wait_for_quiet(
            quiet,
            timeout_seconds=remaining(quiet_timeout_seconds),
            poll_seconds=quiet_poll_seconds,
            clock=clock,
            sleeper=sleeper,
        ):
            return {"status": "denied", "reason_code": "AUTHORITY_NOT_QUIET"}
        try:
            if inventory_preparer is not None:
                inventory_preparer(baseline)
            else:
                prepare_current_live_inventories(
                    home, timeout_seconds=max(1.0, remaining(20.0))
                )
        except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
            return {
                "status": "error",
                "reason_code": "RECOVERY_INVENTORY_PREFLIGHT_FAILED",
                "error_type": type(exc).__name__,
            }
        if not wait_for_quiet(
            quiet,
            timeout_seconds=remaining(quiet_timeout_seconds),
            poll_seconds=quiet_poll_seconds,
            clock=clock,
            sleeper=sleeper,
        ):
            return {"status": "denied", "reason_code": "AUTHORITY_NOT_QUIET"}
        reader = snapshot_reader or (lambda: capture_authority_receipt(home))
        preliminary, stable_reason = stable_two_read(
            reader, settle_seconds=settle_seconds, sleeper=sleeper
        )
        if preliminary is None:
            return {"status": "denied", "reason_code": stable_reason}
        recognized, reason = classify_authority_update(baseline, preliminary)
        if not recognized:
            return {"status": "denied", "reason_code": reason}
        if not wait_for_quiet(
            quiet,
            timeout_seconds=remaining(quiet_timeout_seconds),
            poll_seconds=quiet_poll_seconds,
            clock=clock,
            sleeper=sleeper,
        ):
            return {"status": "denied", "reason_code": "AUTHORITY_NOT_QUIET"}
        current, stable_reason = stable_two_read(
            reader, settle_seconds=settle_seconds, sleeper=sleeper
        )
        if current is None:
            return {"status": "denied", "reason_code": stable_reason}
        recognized, reason = classify_authority_update(baseline, current)
        if not recognized:
            return {"status": "denied", "reason_code": reason}
        promoter_receipt: dict[str, Any] | None = None
        if reason == "RECOGNIZED_WORKER_RUNTIME_BOM_UPDATE":
            promoter_receipt, promoter_reason = _worker_bom_promotion_receipt(
                home, baseline, current
            )
            if promoter_receipt is None:
                return {"status": "denied", "reason_code": promoter_reason}
        config_update_plan: dict[str, Any] | None = None
        if reason == "RECOGNIZED_SCOPED_CONFIG_UPDATE":
            config_update_plan, plan_reason = _scoped_config_update_plan(
                baseline, current
            )
            if config_update_plan is None:
                return {"status": "denied", "reason_code": plan_reason}
        lane = {
            "RECOGNIZED_CODEX_DESKTOP_UPDATE": "coherent_app_update",
            "RECOGNIZED_COMPOUND_APP_PRIMARY_RUNTIME_UPDATE": (
                "compound_app_primary_runtime_update"
            ),
            "RECOGNIZED_PRIMARY_RUNTIME_BUNDLE_UPDATE": (
                "primary_runtime_bundle_update"
            ),
            "RECOGNIZED_ENABLED_PLUGIN_VERSION_REPLACEMENT": (
                "updater_bound_plugin_update"
            ),
            "RECOGNIZED_SCOPED_CONFIG_UPDATE": "scoped_config_update",
            "RECOGNIZED_WORKER_RUNTIME_BOM_UPDATE": "worker_runtime_bom_update",
        }.get(reason)
        if lane is None:
            return {"status": "denied", "reason_code": "RECOVERY_LANE_UNPROVEN"}
        routing_dir = target.parent
        try:
            previous_generation_id, generation_sequence = _generation_context(
                routing_dir
            )
        except (OSError, RuntimeError, ValueError):
            return {
                "status": "error",
                "reason_code": "GENERATION_POINTER_INVALID",
            }
        transaction_id = f"authority-{uuid.uuid4().hex}"
        try:
            write_authority_transaction_begin(
                routing_dir,
                transaction_id=transaction_id,
                lane=lane,
                previous_generation_id=previous_generation_id,
                expected_authority_snapshot_sha256=current["snapshot_sha256"],
            )
        except (OSError, RuntimeError, ValueError):
            return {
                "status": "error",
                "reason_code": "AUTHORITY_TRANSACTION_BEGIN_FAILED",
            }
        runner = builder_runner or (
            lambda expected, path: run_builder_candidate(
                expected,
                path,
                authority_receipt=current,
                previous_generation_id=previous_generation_id,
                generation_sequence=generation_sequence,
                authority_transaction_id=transaction_id,
                promotion_reason=lane,
                codex_home=home,
            )
        )
        candidate: Path | None = None
        terminal_status = "error"
        terminal_reason = "RECOVERY_ABORTED"
        terminal_generation_id = ""
        pointer_committed = False
        compatibility_copy_status = "not_attempted"
        repair_warning = ""
        outcome: dict[str, Any] | None = None

        def conclude(value: dict[str, Any]) -> dict[str, Any]:
            nonlocal outcome
            outcome = value
            return value

        try:
            candidate = runner(current["snapshot_sha256"], target)
            final_snapshot, final_reason = stable_two_read(
                reader, settle_seconds=settle_seconds, sleeper=sleeper
            )
            if final_snapshot is None:
                terminal_status = "denied"
                terminal_reason = final_reason
                return conclude({"status": "denied", "reason_code": final_reason})
            if (
                final_snapshot["snapshot_sha256"] != current["snapshot_sha256"]
            ):
                terminal_status = "denied"
                terminal_reason = "AUTHORITY_CHANGED_AFTER_BUILD"
                return conclude({
                    "status": "denied",
                    "reason_code": "AUTHORITY_CHANGED_AFTER_BUILD",
                })
            if lane == "worker_runtime_bom_update":
                final_promoter_receipt, promoter_reason = (
                    _worker_bom_promotion_receipt(home, baseline, final_snapshot)
                )
                if (
                    final_promoter_receipt is None
                    or final_promoter_receipt != promoter_receipt
                ):
                    if final_promoter_receipt is not None:
                        promoter_reason = (
                            "WORKER_RUNTIME_BOM_PROMOTION_RECEIPT_CHANGED"
                        )
                    terminal_status = "denied"
                    terminal_reason = promoter_reason
                    return conclude({
                        "status": "denied",
                        "reason_code": promoter_reason,
                    })
            candidate_manifest = _read_manifest(candidate)
            if candidate_manifest.get("authority_receipt") != current:
                terminal_status = "denied"
                terminal_reason = "BUILDER_RECEIPT_MISMATCH"
                return conclude({
                    "status": "denied",
                    "reason_code": "BUILDER_RECEIPT_MISMATCH",
                })
            candidate_state = index.load_active_capabilities(candidate)
            if (
                candidate_state.get("freshness_status") != "fresh"
                or candidate_state.get("source_hashes_verified") is not True
                or not candidate_state.get("entries")
            ):
                terminal_status = "denied"
                terminal_reason = "BUILDER_CANDIDATE_INVALID"
                return conclude({
                    "status": "denied",
                    "reason_code": "BUILDER_CANDIDATE_INVALID",
                })
            if config_update_plan is not None and not _config_candidate_surface_matches_plan(
                manifest,
                candidate_manifest,
                config_update_plan,
            ):
                terminal_status = "denied"
                terminal_reason = "CONFIG_CANDIDATE_SURFACE_MISMATCH"
                return conclude({
                    "status": "denied",
                    "reason_code": "CONFIG_CANDIDATE_SURFACE_MISMATCH",
                })
            if (
                lane
                in {
                    "coherent_app_update",
                    "compound_app_primary_runtime_update",
                    "primary_runtime_bundle_update",
                    "updater_bound_plugin_update",
                    "worker_runtime_bom_update",
                }
                and _manifest_active_capability_ids(manifest)
                != _manifest_active_capability_ids(candidate_manifest)
            ):
                terminal_status = "denied"
                terminal_reason = "RECOVERY_CANDIDATE_ACTIVE_SURFACE_MISMATCH"
                return conclude({
                    "status": "denied",
                    "reason_code": terminal_reason,
                })
            if (
                lane
                in {
                    "coherent_app_update",
                    "compound_app_primary_runtime_update",
                    "primary_runtime_bundle_update",
                    "updater_bound_plugin_update",
                    "worker_runtime_bom_update",
                }
                and not _automatic_candidate_semantics_match(
                    manifest, candidate_manifest, baseline, current
                )
            ):
                terminal_status = "denied"
                terminal_reason = "RECOVERY_CANDIDATE_SEMANTIC_SURFACE_MISMATCH"
                return conclude({
                    "status": "denied",
                    "reason_code": terminal_reason,
                })
            pointer = promote_manifest_generation(
                candidate,
                target,
                transaction_id=transaction_id,
                expected_previous_generation_id=previous_generation_id,
            )
            terminal_generation_id = str(pointer["generation_id"])
            pointer_committed = True
            compatibility_copy_status = str(
                pointer.get("compatibility_copy_status") or "unknown"
            )
            repair_warning = str(pointer.get("repair_warning") or "")
            _best_effort_unlink(candidate)
            candidate = None
            installed_state = index.load_active_capabilities(target)
            if (
                installed_state.get("freshness_status") != "fresh"
                or installed_state.get("source_hashes_verified") is not True
            ):
                terminal_status = "committed"
                terminal_reason = "COMMITTED_MANIFEST_POSTVALIDATION_DEGRADED"
                return conclude({
                    "status": "rebuilt",
                    "reason_code": terminal_reason,
                    "postcommit_failure": "INSTALLED_MANIFEST_VALIDATION_FAILED",
                    "repair_required": True,
                    "transaction_id": transaction_id,
                    "generation_id": terminal_generation_id,
                    "compatibility_copy_status": compatibility_copy_status,
                    "repair_warning": repair_warning,
                })
            if lane == "worker_runtime_bom_update":
                worker_status = index.worker_runtime_identity_status(
                    candidate_manifest
                )
                components = worker_status.get("components")
                gateway = worker_status.get("gateway")
                process_generation_status = (
                    gateway.get("process_generation_status")
                    if isinstance(gateway, dict)
                    else None
                )
                if (
                    worker_status.get("bom_status") != "current"
                    or not isinstance(gateway, dict)
                    or gateway.get("identity_binding_status") != "current"
                    or not isinstance(components, dict)
                    or set(components)
                    != index.REQUIRED_WORKER_RUNTIME_SERVER_IDS
                    or any(
                        component.get("identity_binding_status") != "current"
                        for component in components.values()
                        if isinstance(component, dict)
                    )
                    or any(
                        not isinstance(component, dict)
                        for component in components.values()
                    )
                ):
                    terminal_status = "committed"
                    terminal_reason = "COMMITTED_WORKER_RUNTIME_POSTVALIDATION_DEGRADED"
                    return conclude({
                        "status": "rebuilt",
                        "reason_code": terminal_reason,
                        "postcommit_failure": (
                            "INSTALLED_WORKER_RUNTIME_CLOSURE_INVALID"
                        ),
                        "repair_required": True,
                        "transaction_id": transaction_id,
                        "generation_id": terminal_generation_id,
                        "compatibility_copy_status": compatibility_copy_status,
                        "repair_warning": repair_warning,
                    })
                if process_generation_status != "current":
                    terminal_status = "committed"
                    terminal_reason = "COMMITTED_WORKER_RUNTIME_RESTART_REQUIRED"
                    return conclude({
                        "status": "rebuilt",
                        "reason_code": terminal_reason,
                        "postcommit_failure": (
                            "GATEWAY_PROCESS_GENERATION_RESTART_REQUIRED"
                        ),
                        "repair_required": True,
                        "restart_required": True,
                        "transaction_id": transaction_id,
                        "generation_id": terminal_generation_id,
                        "compatibility_copy_status": compatibility_copy_status,
                        "repair_warning": repair_warning,
                    })
            terminal_status = "committed"
            terminal_reason = reason
            return conclude({
                "status": "rebuilt",
                "reason_code": reason,
                "snapshot_sha256": current["snapshot_sha256"],
                "transaction_id": transaction_id,
                "generation_id": terminal_generation_id,
                "compatibility_copy_status": compatibility_copy_status,
                "repair_warning": repair_warning,
            })
        except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
            if pointer_committed:
                terminal_status = "committed"
                terminal_reason = "COMMITTED_POSTVALIDATION_EXCEPTION"
                return conclude({
                    "status": "rebuilt",
                    "reason_code": terminal_reason,
                    "postcommit_failure": type(exc).__name__,
                    "repair_required": True,
                    "transaction_id": transaction_id,
                    "generation_id": terminal_generation_id,
                    "compatibility_copy_status": compatibility_copy_status,
                    "repair_warning": repair_warning,
                })
            terminal_status = "error"
            terminal_reason = "RECOVERY_BUILDER_FAILED"
            return conclude({
                "status": "error",
                "reason_code": "RECOVERY_BUILDER_FAILED",
                "error_type": type(exc).__name__,
                "transaction_id": transaction_id,
            })
        finally:
            if candidate is not None:
                _best_effort_unlink(candidate)
            try:
                write_authority_transaction_terminal(
                    routing_dir,
                    transaction_id=transaction_id,
                    lane=lane,
                    status=terminal_status,
                    reason_code=terminal_reason,
                    generation_id=terminal_generation_id,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                if outcome is not None:
                    outcome.clear()
                    if pointer_committed:
                        outcome.update({
                            "status": "rebuilt",
                            "reason_code": (
                                "AUTHORITY_TRANSACTION_TERMINAL_REPAIR_PENDING"
                            ),
                            "postcommit_failure": type(exc).__name__,
                            "repair_required": True,
                            "transaction_id": transaction_id,
                            "generation_id": terminal_generation_id,
                            "compatibility_copy_status": compatibility_copy_status,
                            "repair_warning": repair_warning,
                        })
                    else:
                        outcome.update({
                            "status": "error",
                            "reason_code": "AUTHORITY_TRANSACTION_TERMINAL_FAILED",
                            "error_type": type(exc).__name__,
                            "transaction_id": transaction_id,
                        })


def _operator_review_paths(
    routing_dir: Path, transaction_id: str
) -> tuple[Path, Path]:
    if TRANSACTION_ID_PATTERN.fullmatch(transaction_id) is None:
        raise ValueError("authority transaction identifier is invalid")
    review_dir = routing_dir / "operator-rebaseline-reviews" / transaction_id
    return review_dir / "candidate.json", review_dir / "review.json"


def _ensure_operator_review_directory(
    routing_dir: Path, transaction_id: str
) -> Path:
    candidate_path, _ = _operator_review_paths(routing_dir, transaction_id)
    review_root = candidate_path.parent.parent
    review_dir = candidate_path.parent
    resolved_routing = routing_dir.resolve(strict=True)
    if review_root.exists() and (
        review_root.is_symlink() or not review_root.is_dir()
    ):
        raise RuntimeError("operator review root is invalid")
    review_root.mkdir(exist_ok=True)
    if review_root.resolve(strict=True).parent != resolved_routing:
        raise RuntimeError("operator review root escapes routing authority")
    if review_dir.exists() and (review_dir.is_symlink() or not review_dir.is_dir()):
        raise RuntimeError("operator review transaction directory is invalid")
    review_dir.mkdir(exist_ok=True)
    if review_dir.resolve(strict=True).parent != review_root.resolve(strict=True):
        raise RuntimeError("operator review transaction directory escapes its root")
    return review_dir


def _operator_candidate_manifest(
    content: bytes,
    *,
    current: dict[str, Any],
    transaction_id: str,
    previous_generation_id: str | None,
    generation_sequence: int,
) -> tuple[dict[str, Any] | None, str]:
    try:
        candidate_manifest = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None, "OPERATOR_REVIEWED_CANDIDATE_INVALID"
    if not isinstance(candidate_manifest, dict):
        return None, "OPERATOR_REVIEWED_CANDIDATE_INVALID"
    generation = candidate_manifest.get("authority_generation")
    if not isinstance(generation, dict):
        return None, "OPERATOR_REVIEWED_CANDIDATE_BINDING_MISMATCH"
    generation_id = str(generation.get("id") or "").lower()
    if (
        candidate_manifest.get("schema_version") != "1.3"
        or candidate_manifest.get("freshness_status") != "fresh"
        or not _operator_authority_receipts_equivalent(
            candidate_manifest.get("authority_receipt"), current
        )
        or index.AUTHORITY_GENERATION_ID_PATTERN.fullmatch(generation_id) is None
        or index.authority_generation_id(generation) != generation_id
        or candidate_manifest.get("snapshot_id")
        != f"authority-generation:{generation_id}"
        or generation.get("transaction_id") != transaction_id
        or generation.get("promotion_reason") != "operator_rebaseline"
        or generation.get("previous_id") != previous_generation_id
        or generation.get("sequence") != generation_sequence
        or _normalized_sha256(generation.get("authority_snapshot_sha256"))
        != _normalized_sha256(current.get("snapshot_sha256"))
    ):
        return None, "OPERATOR_REVIEWED_CANDIDATE_BINDING_MISMATCH"
    return candidate_manifest, ""


def _operator_candidate_state_is_valid(content: bytes, target: Path) -> bool:
    verification = target.with_name(
        f".{target.name}.operator-verify-{os.getpid()}-{uuid.uuid4().hex}.json"
    )
    try:
        _write_immutable_bytes(verification, content)
        state = index.load_active_capabilities(verification)
        return (
            state.get("freshness_status") == "fresh"
            and state.get("source_hashes_verified") is True
            and bool(state.get("entries"))
        )
    except (OSError, RuntimeError, ValueError, index.CapabilityDataError):
        return False
    finally:
        verification.unlink(missing_ok=True)


def _operator_review_payload(
    *,
    transaction_id: str,
    expected_authority_snapshot_sha256: str,
    candidate_sha256: str,
    previous_generation_id: str | None,
    generation_sequence: int,
) -> dict[str, Any]:
    return {
        "schema_version": OPERATOR_REVIEW_SCHEMA,
        "transaction_id": transaction_id,
        "expected_authority_snapshot_sha256": expected_authority_snapshot_sha256,
        "candidate_sha256": candidate_sha256,
        "candidate_file": "candidate.json",
        "previous_generation_id": previous_generation_id,
        "generation_sequence": generation_sequence,
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def _validate_operator_review(
    *,
    routing_dir: Path,
    reviewed_candidate_path: Path,
    transaction_id: str,
    expected_authority_snapshot_sha256: str,
    expected_candidate_sha256: str,
    current: dict[str, Any],
    previous_generation_id: str | None,
    generation_sequence: int,
) -> tuple[bytes | None, str, str]:
    expected_path, review_path = _operator_review_paths(routing_dir, transaction_id)
    try:
        supplied = reviewed_candidate_path.resolve(strict=True)
        canonical = expected_path.resolve(strict=True)
        resolved_routing = routing_dir.resolve(strict=True)
        resolved_review_root = expected_path.parent.parent.resolve(strict=True)
        resolved_review_dir = expected_path.parent.resolve(strict=True)
    except OSError:
        return None, "", "OPERATOR_REVIEWED_CANDIDATE_UNAVAILABLE"
    if (
        reviewed_candidate_path.is_symlink()
        or expected_path.is_symlink()
        or supplied != canonical
        or expected_path.parent.is_symlink()
        or expected_path.parent.parent.is_symlink()
        or resolved_review_root.parent != resolved_routing
        or resolved_review_dir.parent != resolved_review_root
        or not review_path.is_file()
        or review_path.is_symlink()
    ):
        return None, "", "OPERATOR_REVIEW_ARTIFACT_INVALID"
    try:
        content = _stable_regular_bytes(expected_path, "operator reviewed candidate")
        review = _read_manifest(review_path)
    except (OSError, RuntimeError, ValueError):
        return None, "", "OPERATOR_REVIEW_ARTIFACT_INVALID"
    observed_candidate = hashlib.sha256(content).hexdigest()
    expected_review = _operator_review_payload(
        transaction_id=transaction_id,
        expected_authority_snapshot_sha256=expected_authority_snapshot_sha256,
        candidate_sha256=expected_candidate_sha256,
        previous_generation_id=previous_generation_id,
        generation_sequence=generation_sequence,
    )
    for key, value in expected_review.items():
        if key != "recorded_at" and review.get(key) != value:
            return None, observed_candidate, "OPERATOR_REVIEW_ARTIFACT_BINDING_MISMATCH"
    if observed_candidate != expected_candidate_sha256:
        return None, observed_candidate, "OPERATOR_CANDIDATE_SHA256_CAS_MISMATCH"
    candidate_manifest, reason = _operator_candidate_manifest(
        content,
        current=current,
        transaction_id=transaction_id,
        previous_generation_id=previous_generation_id,
        generation_sequence=generation_sequence,
    )
    if candidate_manifest is None:
        return None, observed_candidate, reason
    if not _operator_candidate_state_is_valid(content, expected_path):
        return None, observed_candidate, "OPERATOR_REVIEWED_CANDIDATE_INVALID"
    return content, observed_candidate, ""


def render_operator_rebaseline_candidate(
    *,
    expected_authority_snapshot_sha256: str,
    transaction_id: str,
    manifest_path: Path | None = None,
    codex_home: Path | None = None,
    snapshot_reader: Callable[[], dict[str, Any]] | None = None,
    builder_runner: Callable[[str, Path], Path] | None = None,
    current_inventory_preparer: Callable[[], None] | None = None,
    mutex_factory: Callable[[], Any] | None = None,
    quiet_checker: Callable[[], bool] | None = None,
    settle_seconds: float = 2.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Render inert immutable bytes for human review. Never begins a transaction."""

    expected_authority = _normalized_sha256(expected_authority_snapshot_sha256)
    if not expected_authority:
        return {
            "status": "denied",
            "reason_code": "OPERATOR_EXPECTED_AUTHORITY_SNAPSHOT_SHA256_REQUIRED",
        }
    if TRANSACTION_ID_PATTERN.fullmatch(str(transaction_id or "")) is None:
        return {"status": "denied", "reason_code": "AUTHORITY_TRANSACTION_ID_INVALID"}
    home = (codex_home or index.CODEX_HOME).resolve(strict=False)
    target = manifest_path or (home / "capability-routing" / "active-capabilities.json")
    routing_dir = target.parent
    factory = mutex_factory or WindowsNamedMutex
    with factory() as mutex:
        if getattr(mutex, "abandoned", False):
            return {"status": "denied", "reason_code": "RECOVERY_MUTEX_ABANDONED"}
        if not getattr(mutex, "acquired", False):
            return {"status": "noop", "reason_code": "RECOVERY_MUTEX_BUSY"}
        quiet = quiet_checker or (lambda: authority_is_quiet(home))
        if not wait_for_quiet(
            quiet, timeout_seconds=45.0, poll_seconds=2.0, sleeper=sleeper
        ):
            return {"status": "denied", "reason_code": "AUTHORITY_NOT_QUIET"}
        try:
            if current_inventory_preparer is not None:
                current_inventory_preparer()
            else:
                prepare_current_live_inventories(home, timeout_seconds=20.0)
        except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
            return {
                "status": "error",
                "reason_code": "RECOVERY_INVENTORY_PREFLIGHT_FAILED",
                "error_type": type(exc).__name__,
            }
        reader = snapshot_reader or (lambda: capture_authority_receipt(home))
        preliminary, stable_reason = stable_two_read(
            reader, settle_seconds=settle_seconds, sleeper=sleeper
        )
        if preliminary is None:
            return {"status": "denied", "reason_code": stable_reason}
        observed_authority = _normalized_sha256(preliminary.get("snapshot_sha256"))
        if observed_authority != expected_authority:
            return {
                "status": "denied",
                "reason_code": "OPERATOR_AUTHORITY_SNAPSHOT_CAS_MISMATCH",
                "expected_authority_snapshot_sha256": expected_authority,
                "observed_authority_snapshot_sha256": observed_authority,
            }
        current, stable_reason = stable_two_read(
            reader, settle_seconds=settle_seconds, sleeper=sleeper
        )
        if current is None:
            return {"status": "denied", "reason_code": stable_reason}
        if _normalized_sha256(current.get("snapshot_sha256")) != expected_authority:
            return {
                "status": "denied",
                "reason_code": "OPERATOR_AUTHORITY_SNAPSHOT_CAS_MISMATCH",
            }
        try:
            previous_generation_id, generation_sequence = _generation_context(routing_dir)
        except (OSError, RuntimeError, ValueError):
            return {"status": "error", "reason_code": "GENERATION_POINTER_INVALID"}
        review_candidate, review_receipt = _operator_review_paths(
            routing_dir, transaction_id
        )
        if review_candidate.exists() or review_receipt.exists():
            if not review_candidate.is_file() or not review_receipt.is_file():
                return {
                    "status": "error",
                    "reason_code": "OPERATOR_REVIEW_ARTIFACT_INCOMPLETE",
                }
            review = _read_manifest(review_receipt)
            candidate_sha256 = _normalized_sha256(review.get("candidate_sha256"))
            content, observed_candidate, reason = _validate_operator_review(
                routing_dir=routing_dir,
                reviewed_candidate_path=review_candidate,
                transaction_id=transaction_id,
                expected_authority_snapshot_sha256=expected_authority,
                expected_candidate_sha256=candidate_sha256,
                current=current,
                previous_generation_id=previous_generation_id,
                generation_sequence=generation_sequence,
            )
            if content is None:
                return {"status": "error", "reason_code": reason}
            return {
                "status": "rendered",
                "reason_code": "OPERATOR_REBASELINE_CANDIDATE_ALREADY_RENDERED",
                "transaction_id": transaction_id,
                "expected_authority_snapshot_sha256": expected_authority,
                "candidate_sha256": observed_candidate,
                "reviewed_candidate_path": str(review_candidate.resolve()),
                "replayed": True,
            }
        runner = builder_runner or (
            lambda expected, path: run_builder_candidate(
                expected,
                path,
                authority_receipt=current,
                previous_generation_id=previous_generation_id,
                generation_sequence=generation_sequence,
                authority_transaction_id=transaction_id,
                promotion_reason="operator_rebaseline",
                codex_home=home,
            )
        )
        candidate: Path | None = None
        try:
            candidate = runner(current["snapshot_sha256"], target)
            content = _stable_regular_bytes(candidate, "operator rendered candidate")
            candidate_manifest, reason = _operator_candidate_manifest(
                content,
                current=current,
                transaction_id=transaction_id,
                previous_generation_id=previous_generation_id,
                generation_sequence=generation_sequence,
            )
            if candidate_manifest is None or not _operator_candidate_state_is_valid(
                content, target
            ):
                return {
                    "status": "denied",
                    "reason_code": reason or "OPERATOR_REVIEWED_CANDIDATE_INVALID",
                }
            final_snapshot, final_reason = stable_two_read(
                reader, settle_seconds=settle_seconds, sleeper=sleeper
            )
            if final_snapshot is None:
                return {"status": "denied", "reason_code": final_reason}
            if _normalized_sha256(final_snapshot.get("snapshot_sha256")) != expected_authority:
                return {
                    "status": "denied",
                    "reason_code": "AUTHORITY_CHANGED_AFTER_BUILD",
                }
            candidate_sha256 = hashlib.sha256(content).hexdigest()
            _ensure_operator_review_directory(routing_dir, transaction_id)
            _write_immutable_bytes(review_candidate, content)
            _write_idempotent_transaction_record(
                review_receipt,
                _operator_review_payload(
                    transaction_id=transaction_id,
                    expected_authority_snapshot_sha256=expected_authority,
                    candidate_sha256=candidate_sha256,
                    previous_generation_id=previous_generation_id,
                    generation_sequence=generation_sequence,
                ),
            )
            return {
                "status": "rendered",
                "reason_code": "OPERATOR_REBASELINE_CANDIDATE_RENDERED",
                "transaction_id": transaction_id,
                "expected_authority_snapshot_sha256": expected_authority,
                "candidate_sha256": candidate_sha256,
                "reviewed_candidate_path": str(review_candidate.resolve()),
                "replayed": False,
            }
        except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
            return {
                "status": "error",
                "reason_code": "OPERATOR_CANDIDATE_RENDER_FAILED",
                "error_type": type(exc).__name__,
            }
        finally:
            if candidate is not None:
                _best_effort_unlink(candidate)


def operator_rebaseline(
    *,
    authorization_id: str,
    expected_authority_snapshot_sha256: str = "",
    expected_candidate_sha256: str = "",
    reviewed_candidate_path: Path | None = None,
    transaction_id: str | None = None,
    manifest_path: Path | None = None,
    codex_home: Path | None = None,
    snapshot_reader: Callable[[], dict[str, Any]] | None = None,
    current_inventory_preparer: Callable[[], None] | None = None,
    mutex_factory: Callable[[], Any] | None = None,
    quiet_checker: Callable[[], bool] | None = None,
    settle_seconds: float = 2.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Apply exact reviewed candidate bytes under snapshot and candidate CAS."""

    if not authorization_id.strip():
        return {"status": "denied", "reason_code": "OPERATOR_AUTHORIZATION_REQUIRED"}
    expected_authority = _normalized_sha256(expected_authority_snapshot_sha256)
    if not expected_authority:
        return {
            "status": "denied",
            "reason_code": "OPERATOR_EXPECTED_AUTHORITY_SNAPSHOT_SHA256_REQUIRED",
        }
    expected_candidate = _normalized_sha256(expected_candidate_sha256)
    if not expected_candidate:
        return {
            "status": "denied",
            "reason_code": "OPERATOR_EXPECTED_CANDIDATE_SHA256_REQUIRED",
        }
    if TRANSACTION_ID_PATTERN.fullmatch(str(transaction_id or "")) is None:
        return {"status": "denied", "reason_code": "AUTHORITY_TRANSACTION_ID_INVALID"}
    if reviewed_candidate_path is None:
        return {
            "status": "denied",
            "reason_code": "OPERATOR_REVIEWED_CANDIDATE_REQUIRED",
        }
    operation_id = str(transaction_id)
    home = (codex_home or index.CODEX_HOME).resolve(strict=False)
    target = manifest_path or (home / "capability-routing" / "active-capabilities.json")
    routing_dir = target.parent
    factory = mutex_factory or WindowsNamedMutex
    with factory() as mutex:
        if getattr(mutex, "abandoned", False):
            return {"status": "denied", "reason_code": "RECOVERY_MUTEX_ABANDONED"}
        if not getattr(mutex, "acquired", False):
            return {"status": "noop", "reason_code": "RECOVERY_MUTEX_BUSY"}
        replay = replay_operator_transaction(
            routing_dir,
            transaction_id=operation_id,
            authorization_id=authorization_id,
            expected_authority_snapshot_sha256=expected_authority,
            expected_candidate_sha256=expected_candidate,
        )
        if replay is not None:
            return replay
        quiet = quiet_checker or (lambda: authority_is_quiet(home))
        if not wait_for_quiet(
            quiet, timeout_seconds=45.0, poll_seconds=2.0, sleeper=sleeper
        ):
            return {"status": "denied", "reason_code": "AUTHORITY_NOT_QUIET"}
        try:
            if current_inventory_preparer is not None:
                current_inventory_preparer()
            else:
                prepare_current_live_inventories(home, timeout_seconds=20.0)
        except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
            return {
                "status": "error",
                "reason_code": "RECOVERY_INVENTORY_PREFLIGHT_FAILED",
                "error_type": type(exc).__name__,
            }
        reader = snapshot_reader or (lambda: capture_authority_receipt(home))
        preliminary, stable_reason = stable_two_read(
            reader, settle_seconds=settle_seconds, sleeper=sleeper
        )
        if preliminary is None:
            return {"status": "denied", "reason_code": stable_reason}
        observed_authority = _normalized_sha256(preliminary.get("snapshot_sha256"))
        if observed_authority != expected_authority:
            return {
                "status": "denied",
                "reason_code": "OPERATOR_AUTHORITY_SNAPSHOT_CAS_MISMATCH",
                "expected_authority_snapshot_sha256": expected_authority,
                "observed_authority_snapshot_sha256": observed_authority,
            }
        current, stable_reason = stable_two_read(
            reader, settle_seconds=settle_seconds, sleeper=sleeper
        )
        if current is None:
            return {"status": "denied", "reason_code": stable_reason}
        observed_authority = _normalized_sha256(current.get("snapshot_sha256"))
        if observed_authority != expected_authority:
            return {
                "status": "denied",
                "reason_code": "OPERATOR_AUTHORITY_SNAPSHOT_CAS_MISMATCH",
                "expected_authority_snapshot_sha256": expected_authority,
                "observed_authority_snapshot_sha256": observed_authority,
            }
        try:
            previous_generation_id, generation_sequence = _generation_context(routing_dir)
        except (OSError, RuntimeError, ValueError):
            return {"status": "error", "reason_code": "GENERATION_POINTER_INVALID"}
        content, observed_candidate, review_reason = _validate_operator_review(
            routing_dir=routing_dir,
            reviewed_candidate_path=reviewed_candidate_path,
            transaction_id=operation_id,
            expected_authority_snapshot_sha256=expected_authority,
            expected_candidate_sha256=expected_candidate,
            current=current,
            previous_generation_id=previous_generation_id,
            generation_sequence=generation_sequence,
        )
        if content is None:
            return {
                "status": "denied",
                "reason_code": review_reason,
                "expected_candidate_sha256": expected_candidate,
                "observed_candidate_sha256": observed_candidate,
            }
        transaction_candidate = (
            routing_dir / "authority-transactions" / f"{operation_id}.candidate.json"
        )
        try:
            _write_immutable_bytes(transaction_candidate, content)
            write_authority_transaction_begin(
                routing_dir,
                transaction_id=operation_id,
                lane="operator_rebaseline",
                previous_generation_id=previous_generation_id,
                expected_authority_snapshot_sha256=expected_authority,
                expected_candidate_sha256=expected_candidate,
                authorization_id=authorization_id,
            )
        except (OSError, RuntimeError, ValueError):
            return {
                "status": "error",
                "reason_code": "AUTHORITY_TRANSACTION_BEGIN_FAILED",
            }
        terminal_status = "error"
        terminal_reason = "OPERATOR_REBASELINE_ABORTED"
        generation_id = ""
        pointer_committed = False
        compatibility_copy_status = "not_attempted"
        repair_warning = ""
        outcome: dict[str, Any] | None = None

        def conclude(value: dict[str, Any]) -> dict[str, Any]:
            nonlocal outcome
            outcome = value
            return value

        try:
            final_snapshot, final_reason = stable_two_read(
                reader, settle_seconds=settle_seconds, sleeper=sleeper
            )
            if final_snapshot is None:
                terminal_status = "denied"
                terminal_reason = final_reason
                return conclude({"status": "denied", "reason_code": final_reason})
            observed_authority = _normalized_sha256(final_snapshot.get("snapshot_sha256"))
            if observed_authority != expected_authority:
                terminal_status = "denied"
                terminal_reason = "AUTHORITY_CHANGED_AFTER_REVIEW_VALIDATION"
                return conclude({"status": "denied", "reason_code": terminal_reason})
            pointer = promote_manifest_generation(
                transaction_candidate,
                target,
                transaction_id=operation_id,
                expected_previous_generation_id=previous_generation_id,
                expected_candidate_sha256=expected_candidate,
            )
            generation_id = str(pointer["generation_id"])
            pointer_committed = True
            compatibility_copy_status = str(
                pointer.get("compatibility_copy_status") or "unknown"
            )
            repair_warning = str(pointer.get("repair_warning") or "")
            installed_state = index.load_active_capabilities(target)
            if (
                installed_state.get("freshness_status") != "fresh"
                or installed_state.get("source_hashes_verified") is not True
            ):
                terminal_status = "committed"
                terminal_reason = "COMMITTED_MANIFEST_POSTVALIDATION_DEGRADED"
                return conclude({
                    "status": "rebuilt",
                    "reason_code": terminal_reason,
                    "postcommit_failure": "INSTALLED_MANIFEST_VALIDATION_FAILED",
                    "repair_required": True,
                    "transaction_id": operation_id,
                    "generation_id": generation_id,
                    "compatibility_copy_status": compatibility_copy_status,
                    "repair_warning": repair_warning,
                })
            terminal_status = "committed"
            terminal_reason = "OPERATOR_REBASELINE_COMMITTED"
            return conclude(
                {
                    "status": "rebuilt",
                    "reason_code": terminal_reason,
                    "snapshot_sha256": expected_authority,
                    "candidate_sha256": expected_candidate,
                    "transaction_id": operation_id,
                    "generation_id": generation_id,
                    "compatibility_copy_status": compatibility_copy_status,
                    "repair_warning": repair_warning,
                }
            )
        except (OSError, RuntimeError, ValueError, index.CapabilityDataError) as exc:
            if pointer_committed:
                terminal_status = "committed"
                terminal_reason = "COMMITTED_POSTVALIDATION_EXCEPTION"
                return conclude({
                    "status": "rebuilt",
                    "reason_code": terminal_reason,
                    "postcommit_failure": type(exc).__name__,
                    "repair_required": True,
                    "transaction_id": operation_id,
                    "generation_id": generation_id,
                    "compatibility_copy_status": compatibility_copy_status,
                    "repair_warning": repair_warning,
                })
            terminal_status = "error"
            terminal_reason = "OPERATOR_REBASELINE_APPLY_FAILED"
            return conclude(
                {
                    "status": "error",
                    "reason_code": terminal_reason,
                    "error_type": type(exc).__name__,
                    "transaction_id": operation_id,
                }
            )
        finally:
            try:
                write_authority_transaction_terminal(
                    routing_dir,
                    transaction_id=operation_id,
                    lane="operator_rebaseline",
                    status=terminal_status,
                    reason_code=terminal_reason,
                    generation_id=generation_id,
                    expected_authority_snapshot_sha256=expected_authority,
                    expected_candidate_sha256=expected_candidate,
                    observed_authority_snapshot_sha256=observed_authority,
                    observed_candidate_sha256=observed_candidate,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                if outcome is not None:
                    outcome.clear()
                    if pointer_committed:
                        outcome.update({
                            "status": "rebuilt",
                            "reason_code": (
                                "AUTHORITY_TRANSACTION_TERMINAL_REPAIR_PENDING"
                            ),
                            "postcommit_failure": type(exc).__name__,
                            "repair_required": True,
                            "transaction_id": operation_id,
                            "generation_id": generation_id,
                            "compatibility_copy_status": compatibility_copy_status,
                            "repair_warning": repair_warning,
                        })
                    else:
                        outcome.update({
                            "status": "error",
                            "reason_code": "AUTHORITY_TRANSACTION_TERMINAL_FAILED",
                            "error_type": type(exc).__name__,
                            "transaction_id": operation_id,
                        })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--render-operator-rebaseline-candidate", action="store_true")
    parser.add_argument("--operator-rebaseline", action="store_true")
    parser.add_argument("--authorization-id", default="")
    parser.add_argument("--transaction-id", default="")
    parser.add_argument("--expected-authority-snapshot-sha256", default="")
    parser.add_argument("--expected-candidate-sha256", default="")
    parser.add_argument("--reviewed-candidate", type=Path)
    parser.add_argument("--codex-home", type=Path, default=index.CODEX_HOME)
    args = parser.parse_args()
    selected_modes = sum(
        bool(value)
        for value in (
            args.snapshot,
            args.render_operator_rebaseline_candidate,
            args.operator_rebaseline,
        )
    )
    if selected_modes != 1:
        parser.error(
            "select exactly one of --snapshot, "
            "--render-operator-rebaseline-candidate, or --operator-rebaseline"
        )
    if args.snapshot:
        try:
            prepare_current_live_inventories(args.codex_home, timeout_seconds=20.0)
            receipt = capture_authority_receipt(args.codex_home)
        except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
            print(
                _canonical_json(
                    {
                        "status": "error",
                        "reason_code": "RECOVERY_INVENTORY_PREFLIGHT_FAILED",
                        "error_type": type(exc).__name__,
                    }
                )
            )
            return 2
        print(_canonical_json(receipt))
        return 0 if _valid_receipt(receipt) else 2
    if not args.expected_authority_snapshot_sha256:
        parser.error(
            "operator render/apply requires --expected-authority-snapshot-sha256"
        )
    if not args.transaction_id:
        parser.error("operator render/apply requires --transaction-id")
    if args.render_operator_rebaseline_candidate:
        result = render_operator_rebaseline_candidate(
            expected_authority_snapshot_sha256=(
                args.expected_authority_snapshot_sha256
            ),
            transaction_id=args.transaction_id,
            codex_home=args.codex_home,
        )
        print(_canonical_json(result))
        return 0 if result.get("status") == "rendered" else 2
    if not args.expected_candidate_sha256:
        parser.error("--operator-rebaseline requires --expected-candidate-sha256")
    if args.reviewed_candidate is None:
        parser.error("--operator-rebaseline requires --reviewed-candidate")
    result = operator_rebaseline(
        authorization_id=args.authorization_id,
        expected_authority_snapshot_sha256=args.expected_authority_snapshot_sha256,
        expected_candidate_sha256=args.expected_candidate_sha256,
        reviewed_candidate_path=args.reviewed_candidate,
        transaction_id=args.transaction_id or None,
        codex_home=args.codex_home,
    )
    print(_canonical_json(result))
    return 0 if result.get("status") == "rebuilt" else 2


if __name__ == "__main__":
    raise SystemExit(main())
