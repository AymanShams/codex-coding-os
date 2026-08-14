#!/usr/bin/env python3
"""Explicit transaction for deploying Catalogue Router authority source.

This is deliberately separate from the Coding OS package installer. It copies
only the reviewed static authority files named by the deployment spec. Live
manifests, route registries, receipts, project maps, worker BOMs, overlays, and
other runtime state are outside this transaction.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import time
from typing import Any, Mapping


SPEC_SCHEMA = "catalogue-router-deployment-spec-v1"
BUNDLE_SCHEMA = "catalogue-router-deployment-bundle-v1"
JOURNAL_SCHEMA = "catalogue-router-deployment-journal-v1"
RECEIPT_SCHEMA = "catalogue-router-deployment-receipt-v1"
BUNDLE_DOMAIN = b"CATALOGUE-ROUTER-DEPLOYMENT-BUNDLE-v1\0"
RECEIPT_DOMAIN = b"CATALOGUE-ROUTER-DEPLOYMENT-RECEIPT-v1\0"
STATE_DIRECTORY = ".catalogue-router-deploy"
TRANSACTION_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


EXPECTED_DEPLOYMENT_MAP = {
    "capability-routing/active-capabilities.schema.json":
        "capability-routing/active-capabilities.schema.json",
    "capability-routing/authority-receipt.schema.json":
        "capability-routing/authority-receipt.schema.json",
    "capability-routing/builder/build_canonical_capability_manifest.ps1":
        "capability-routing/builder/build_canonical_capability_manifest.ps1",
    "capability-routing/deployment/materialize_routing_policy.py":
        "capability-routing/materialize_routing_policy.py",
    "capability-routing/deployment/promote_worker_runtime_bom.py":
        "capability-routing/promote_worker_runtime_bom.py",
    "capability-routing/project-scope-map.schema.json":
        "capability-routing/project-scope-map.schema.json",
    "capability-routing/reference-runtime/_hook_io.py": "hooks/_hook_io.py",
    "capability-routing/reference-runtime/capability_config_fingerprint.py":
        "hooks/capability_config_fingerprint.py",
    "capability-routing/reference-runtime/capability_index.py": "hooks/capability_index.py",
    "capability-routing/reference-runtime/capability_index_cli.py":
        "hooks/capability_index_cli.py",
    "capability-routing/reference-runtime/capability_index_session_start.py":
        "hooks/capability_index_session_start.py",
    "capability-routing/reference-runtime/capability_manifest_recovery.py":
        "hooks/capability_manifest_recovery.py",
    "capability-routing/reference-runtime/routing_policy_validation.py":
        "hooks/routing_policy_validation.py",
    "capability-routing/reference-runtime/user_prompt_skill_router.py":
        "hooks/user_prompt_skill_router.py",
    "capability-routing/route-decision.schema.json":
        "capability-routing/route-decision.schema.json",
    "capability-routing/routing-policy.yaml":
        "capability-routing/policy-base/routing-policy.yaml",
    "capability-routing/routing-policy-overlay.schema.json":
        "capability-routing/routing-policy-overlay.schema.json",
    "capability-routing/routing-policy.schema.json":
        "capability-routing/routing-policy.schema.json",
    "capability-routing/worker-runtime-bom.schema.json":
        "capability-routing/worker-runtime-bom.schema.json",
    ".agents/skills/catalogue-router/SKILL.md":
        "skills/catalogue-router/SKILL.md",
    ".agents/skills/catalogue-router/references/capability-catalogue.md":
        "skills/catalogue-router/references/capability-catalogue.md",
    ".agents/skills/catalogue-router/scripts/query-catalogue.ps1":
        "skills/catalogue-router/scripts/query-catalogue.ps1",
}
EXPECTED_LIVE_PRECONDITIONS: dict[str, dict[str, str]] = {}

RETIRED_ROUTER_DIRECTORIES = (
    "hooks/capability-router",
    "coding-os/hooks/capability-router",
    "capability-index",
)

RUNTIME_STATE_PATHS = (
    "capability-routing/active-capabilities.json",
    "capability-routing/current-generation.json",
    "capability-routing/generations",
    "capability-routing/project-scope-map.json",
    "capability-routing/routing-policy.yaml",
    "capability-routing/routing-policy.deployment-overlay.json",
    "capability-routing/.routing-policy-materializer",
    "capability-routing/.worker-runtime-bom-promoter",
    "capability-routing/authority-quarantine",
    "capability-routing/authority-transactions",
    "capability-routing/quarantine-observations",
    "capability-routing/recovery-receipts",
    "capability-routing/route-decisions.sqlite3",
    "capability-routing/route-decisions.sqlite3-shm",
    "capability-routing/route-decisions.sqlite3-wal",
    "capability-routing/update-transactions",
    "capability-routing/worker-runtime-bom.json",
    "capability-routing/local-agent-overlay.json",
)


class DeploymentError(RuntimeError):
    """Base fail-closed deployment error."""


class BundleError(DeploymentError):
    """The source bundle or deployment spec is invalid."""


class LockError(DeploymentError):
    """The exclusive target lock could not be acquired."""


class PreconditionError(DeploymentError):
    """A compare-and-swap precondition no longer matches live state."""


class ReplayError(DeploymentError):
    """A transaction identifier was replayed against different facts."""


class RecoveryError(DeploymentError):
    """An interrupted transaction cannot be recovered without data loss."""


class InjectedFailure(DeploymentError):
    """Test-only fault raised at an explicit transaction boundary."""


@dataclass(frozen=True)
class DeploymentOptions:
    source_root: Path | str
    codex_home: Path | str
    transaction_id: str
    expected_bundle_sha256: str
    spec_path: Path | str | None = None
    lock_timeout_seconds: float = 30.0
    fault_injection: str | None = None


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


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


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
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


def _write_json(path: Path, value: Any) -> None:
    _atomic_write_bytes(path, _canonical_json_bytes(value))


def _write_once(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise ReplayError(f"immutable transaction artifact changed: {path}")
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
                raise ReplayError(f"immutable transaction artifact changed: {path}")
        except OSError:
            if path.exists():
                if path.read_bytes() != payload:
                    raise ReplayError(f"immutable transaction artifact changed: {path}")
            else:
                os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _normalize_relative(raw: str) -> str:
    if not raw or "\\" in raw or "\x00" in raw:
        raise BundleError(f"invalid relative path: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BundleError(f"invalid relative path: {raw!r}")
    if any(":" in part for part in path.parts):
        raise BundleError(f"invalid relative path: {raw!r}")
    return path.as_posix()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_join(root: Path, relative: str) -> Path:
    normalized = _normalize_relative(relative)
    candidate = (root / Path(*PurePosixPath(normalized).parts)).resolve(strict=False)
    if not _is_within(candidate, root):
        raise BundleError(f"path escaped its declared root: {relative}")
    return candidate


def _reject_symlink_chain(root: Path, relative: str, *, include_leaf: bool = True) -> None:
    current = root
    parts = PurePosixPath(_normalize_relative(relative)).parts
    checked = parts if include_leaf else parts[:-1]
    for part in checked:
        current = current / part
        if current.exists() and current.is_symlink():
            raise BundleError(f"symbolic links are not allowed in deployment paths: {relative}")


def _stable_read(path: Path) -> bytes:
    before = path.stat()
    if not path.is_file() or path.is_symlink():
        raise BundleError(f"deployment source is not one regular file: {path}")
    payload = path.read_bytes()
    after = path.stat()
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or len(payload) != before.st_size:
        raise BundleError(f"deployment source changed while it was read: {path}")
    return payload


def _default_spec_path(source_root: Path) -> Path:
    return source_root / "capability-routing" / "deployment" / "router-authority.bundle.json"


def _load_spec(
    source_root: Path,
    spec_path: Path | str | None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    path = Path(spec_path).expanduser().resolve() if spec_path else _default_spec_path(source_root)
    if not _is_within(path, source_root):
        raise BundleError("deployment spec must remain inside the declared source root")
    if not path.is_file() or path.is_symlink():
        raise BundleError(f"deployment spec is unavailable: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"deployment spec is unreadable: {exc}") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"entries", "preconditions", "schema_version"}
        or value.get("schema_version") != SPEC_SCHEMA
    ):
        raise BundleError("deployment spec schema is invalid")
    raw_entries = value.get("entries")
    raw_preconditions = value.get("preconditions")
    if not isinstance(raw_entries, list) or not isinstance(raw_preconditions, list):
        raise BundleError("deployment spec entries and preconditions must be arrays")
    entries: list[dict[str, str]] = []
    seen_sources: set[str] = set()
    seen_targets: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict) or set(raw) != {"source", "target"}:
            raise BundleError("each deployment spec entry must declare only source and target")
        source = _normalize_relative(str(raw["source"]))
        target = _normalize_relative(str(raw["target"]))
        if source in seen_sources or target.casefold() in seen_targets:
            raise BundleError("deployment spec contains a duplicate source or target")
        seen_sources.add(source)
        seen_targets.add(target.casefold())
        entries.append({"source": source, "target": target})
    actual = {entry["source"]: entry["target"] for entry in entries}
    if actual != EXPECTED_DEPLOYMENT_MAP:
        added = sorted(set(actual.items()) - set(EXPECTED_DEPLOYMENT_MAP.items()))
        removed = sorted(set(EXPECTED_DEPLOYMENT_MAP.items()) - set(actual.items()))
        raise BundleError(
            f"deployment spec differs from the exact static allowlist: added={added}, removed={removed}"
        )
    for target in actual.values():
        _reject_runtime_state_target(target)
    preconditions: list[dict[str, str]] = []
    seen_precondition_sources: set[str] = set()
    seen_precondition_targets: set[str] = set()
    for raw in raw_preconditions:
        if not isinstance(raw, dict) or set(raw) != {"sha256", "source", "target"}:
            raise BundleError(
                "each live precondition must declare only source, target, and sha256"
            )
        source = _normalize_relative(str(raw["source"]))
        target = _normalize_relative(str(raw["target"]))
        sha256 = str(raw["sha256"]).lower()
        if (
            SHA256_RE.fullmatch(sha256) is None
            or source in seen_precondition_sources
            or target.casefold() in seen_precondition_targets
            or source in actual
            or target.casefold() in {item.casefold() for item in actual.values()}
        ):
            raise BundleError("deployment spec contains an invalid live precondition")
        seen_precondition_sources.add(source)
        seen_precondition_targets.add(target.casefold())
        preconditions.append(
            {"sha256": sha256, "source": source, "target": target}
        )
    expected_preconditions = {
        source: {
            "sha256": values["sha256"],
            "target": values["target"],
        }
        for source, values in EXPECTED_LIVE_PRECONDITIONS.items()
    }
    actual_preconditions = {
        item["source"]: {
            "sha256": item["sha256"],
            "target": item["target"],
        }
        for item in preconditions
    }
    if actual_preconditions != expected_preconditions:
        raise BundleError(
            "deployment spec differs from the exact live-precondition allowlist"
        )
    return (
        sorted(entries, key=lambda item: item["target"].encode("utf-8")),
        sorted(preconditions, key=lambda item: item["target"].encode("utf-8")),
    )


def _reject_runtime_state_target(target: str) -> None:
    key = target.casefold().rstrip("/")
    for denied in RUNTIME_STATE_PATHS:
        denied_key = denied.casefold().rstrip("/")
        if key == denied_key or key.startswith(f"{denied_key}/"):
            raise BundleError(f"runtime state cannot be deployed from repository source: {target}")
    for retired in RETIRED_ROUTER_DIRECTORIES:
        retired_key = retired.casefold().rstrip("/")
        if key == retired_key or key.startswith(f"{retired_key}/"):
            raise BundleError(f"retired router paths cannot be deployed: {target}")


def build_bundle_manifest(
    source_root: Path | str,
    *,
    spec_path: Path | str | None = None,
) -> dict[str, Any]:
    root = Path(source_root).expanduser().resolve()
    if not root.is_dir():
        raise BundleError(f"source root is unavailable: {root}")
    entries, preconditions = _load_spec(root, spec_path)
    manifest_entries: list[dict[str, Any]] = []
    for entry in entries:
        _reject_symlink_chain(root, entry["source"])
        source = _safe_join(root, entry["source"])
        payload = _stable_read(source)
        manifest_entries.append(
            {
                "sha256": _sha256_bytes(payload),
                "size": len(payload),
                "source": entry["source"],
                "target": entry["target"],
            }
        )
    manifest_preconditions: list[dict[str, Any]] = []
    for precondition in preconditions:
        _reject_symlink_chain(root, precondition["source"])
        source = _safe_join(root, precondition["source"])
        payload = _stable_read(source)
        actual_sha256 = _sha256_bytes(payload)
        if actual_sha256 != precondition["sha256"]:
            raise BundleError(
                "reviewed live-precondition source hash changed: "
                f"{precondition['source']}"
            )
        manifest_preconditions.append(
            {
                "sha256": actual_sha256,
                "size": len(payload),
                "source": precondition["source"],
                "target": precondition["target"],
            }
        )
    body = {
        "entries": manifest_entries,
        "preconditions": manifest_preconditions,
        "schema_version": BUNDLE_SCHEMA,
    }
    digest = _sha256_bytes(BUNDLE_DOMAIN + _canonical_json_bytes(body))
    return {**body, "bundle_sha256": digest}


def _manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return _canonical_json_bytes(dict(manifest))


def _path_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"state": "missing", "sha256": None, "size": 0}
    if path.is_symlink() or not path.is_file():
        raise PreconditionError(f"live deployment target is not one regular file: {path}")
    payload = _stable_read(path)
    return {"state": "present", "sha256": _sha256_bytes(payload), "size": len(payload)}


def _verify_live_preconditions(
    codex_home: Path,
    manifest: Mapping[str, Any],
) -> None:
    mismatches: list[str] = []
    for precondition in manifest.get("preconditions", []):
        target_name = str(precondition["target"])
        _reject_symlink_chain(codex_home, target_name)
        target = _safe_join(codex_home, target_name)
        try:
            payload = _stable_read(target)
        except (BundleError, OSError):
            mismatches.append(target_name)
            continue
        if (
            len(payload) != precondition["size"]
            or _sha256_bytes(payload) != precondition["sha256"]
        ):
            mismatches.append(target_name)
    if mismatches:
        raise PreconditionError(
            "shared live authority preconditions do not match reviewed repository "
            f"sources: {sorted(mismatches)}"
        )


def _state_matches(path: Path, state: Mapping[str, Any]) -> bool:
    try:
        current = _path_state(path)
    except (BundleError, PreconditionError):
        return False
    return (
        current["state"] == state.get("state")
        and current["sha256"] == state.get("sha256")
        and current["size"] == state.get("size")
    )


def _validate_transaction_id(value: str) -> str:
    if not TRANSACTION_ID_RE.fullmatch(value):
        raise DeploymentError("transaction identifier is invalid")
    return value


def _resolve_codex_home(value: Path | str) -> Path:
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raise DeploymentError("CodexHome must be an explicit absolute path")
    if raw.is_symlink():
        raise DeploymentError(f"CodexHome cannot be a symbolic link: {raw}")
    root = raw.resolve()
    if not root.is_dir() or root.is_symlink():
        raise DeploymentError(f"CodexHome is unavailable or is a symbolic link: {root}")
    return root


def _refuse_retired_router_directories(codex_home: Path) -> None:
    for relative in RETIRED_ROUTER_DIRECTORIES:
        candidate = _safe_join(codex_home, relative)
        if candidate.exists():
            raise DeploymentError(
                f"retired router directory must be removed or independently archived first: {candidate}"
            )


class _ExclusiveLock:
    def __init__(self, path: Path, timeout_seconds: float) -> None:
        self.path = path
        self.timeout_seconds = max(0.0, timeout_seconds)
        self.stream: Any = None

    def __enter__(self) -> "_ExclusiveLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.is_symlink():
            raise LockError(f"Catalogue Router deployment lock cannot be a symbolic link: {self.path}")
        self.stream = self.path.open("a+b")
        self.stream.seek(0, os.SEEK_END)
        if self.stream.tell() == 0:
            self.stream.write(b"\0")
            self.stream.flush()
            os.fsync(self.stream.fileno())
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self._acquire()
                return self
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    self.stream = None
                    raise LockError(f"Catalogue Router deployment lock is busy: {self.path}") from exc
                time.sleep(0.05)

    def _acquire(self) -> None:
        self.stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

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


def _journal_path(transaction_root: Path) -> Path:
    return transaction_root / "journal.json"


def _receipt_path(transaction_root: Path) -> Path:
    return transaction_root / "receipt.json"


def _save_journal(transaction_root: Path, journal: dict[str, Any]) -> None:
    journal["updated_at"] = _utc_now()
    _write_json(_journal_path(transaction_root), journal)


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReplayError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise ReplayError(f"{label} is not a JSON object")
    return value


def _verify_transaction_binding(
    value: Mapping[str, Any],
    *,
    transaction_id: str,
    codex_home: Path,
    bundle_sha256: str,
) -> None:
    if (
        value.get("transaction_id") != transaction_id
        or value.get("codex_home") != str(codex_home)
        or value.get("bundle_sha256") != bundle_sha256
    ):
        raise ReplayError("transaction identifier is already bound to different deployment facts")


def _receipt_body(journal: Mapping[str, Any], outcome: str) -> dict[str, Any]:
    targets = []
    for target in journal.get("targets", []):
        targets.append(
            {
                "new_sha256": target["new_sha256"],
                "prior_sha256": target["prior"]["sha256"],
                "prior_state": target["prior"]["state"],
                "target": target["target"],
            }
        )
    return {
        "bundle_sha256": journal["bundle_sha256"],
        "codex_home": journal["codex_home"],
        "completed_at": journal["updated_at"],
        "error": journal.get("error"),
        "journal_sha256": _sha256_file(Path(journal["journal_path"])),
        "outcome": outcome,
        "schema_version": RECEIPT_SCHEMA,
        "targets": targets,
        "transaction_id": journal["transaction_id"],
    }


def _write_terminal_receipt(
    transaction_root: Path,
    journal: dict[str, Any],
    outcome: str,
) -> dict[str, Any]:
    body = _receipt_body(journal, outcome)
    digest = _sha256_bytes(RECEIPT_DOMAIN + _canonical_json_bytes(body))
    receipt = {**body, "receipt_sha256": digest}
    _write_once(_receipt_path(transaction_root), _canonical_json_bytes(receipt))
    return receipt


def _verify_receipt_digest(receipt: Mapping[str, Any]) -> None:
    body = dict(receipt)
    claimed = body.pop("receipt_sha256", None)
    actual = _sha256_bytes(RECEIPT_DOMAIN + _canonical_json_bytes(body))
    if claimed != actual:
        raise ReplayError("terminal deployment receipt digest is invalid")


def _verify_replay_state(codex_home: Path, receipt: Mapping[str, Any]) -> None:
    outcome = receipt.get("outcome")
    if outcome not in {"deployed", "rolled_back", "recovered_rolled_back"}:
        return
    for target in receipt.get("targets", []):
        live = _safe_join(codex_home, str(target["target"]))
        if outcome == "deployed":
            expected = {
                "state": "present",
                "sha256": target["new_sha256"],
                "size": live.stat().st_size if live.is_file() else -1,
            }
            if not live.is_file() or live.is_symlink() or _sha256_file(live) != expected["sha256"]:
                raise ReplayError(f"completed deployment target has drifted: {live}")
        elif target["prior_state"] == "missing":
            if live.exists():
                raise ReplayError(f"rolled-back target no longer matches its missing baseline: {live}")
        elif not live.is_file() or live.is_symlink() or _sha256_file(live) != target["prior_sha256"]:
            raise ReplayError(f"rolled-back deployment target has drifted: {live}")


def _existing_receipt(
    transaction_root: Path,
    *,
    transaction_id: str,
    codex_home: Path,
    bundle_sha256: str,
    manifest: Mapping[str, Any],
) -> dict[str, Any] | None:
    path = _receipt_path(transaction_root)
    if not path.exists():
        return None
    receipt = _load_json_object(path, "terminal deployment receipt")
    if receipt.get("schema_version") != RECEIPT_SCHEMA:
        raise ReplayError("terminal deployment receipt schema is invalid")
    _verify_transaction_binding(
        receipt,
        transaction_id=transaction_id,
        codex_home=codex_home,
        bundle_sha256=bundle_sha256,
    )
    _verify_receipt_digest(receipt)
    journal_path = _journal_path(transaction_root)
    if (
        not journal_path.is_file()
        or _sha256_file(journal_path) != receipt.get("journal_sha256")
    ):
        raise ReplayError("terminal receipt no longer matches its durable journal")
    journal = _load_json_object(journal_path, "deployment journal")
    _validate_recovery_journal(transaction_root, journal, manifest)
    manifest_path = transaction_root / "bundle-manifest.json"
    if (
        not manifest_path.is_file()
        or _sha256_file(manifest_path) != journal.get("bundle_manifest_sha256")
    ):
        raise ReplayError("durable deployment bundle manifest no longer matches its journal")
    _verify_replay_state(codex_home, receipt)
    return receipt


def _valid_prior_state(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"state", "sha256", "size"}:
        return False
    if value["state"] == "missing":
        return value["sha256"] is None and value["size"] == 0
    return (
        value["state"] == "present"
        and isinstance(value["sha256"], str)
        and SHA256_RE.fullmatch(value["sha256"]) is not None
        and isinstance(value["size"], int)
        and not isinstance(value["size"], bool)
        and value["size"] >= 0
    )


def _validate_recovery_journal(
    transaction_root: Path,
    journal: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    if journal.get("journal_path") != str(_journal_path(transaction_root)):
        raise ReplayError("deployment journal path binding is invalid")
    records = journal.get("targets")
    if not isinstance(records, list):
        raise ReplayError("deployment journal target records are invalid")
    if not records:
        if journal.get("phase") not in {
            "FAILED_BEFORE_BUNDLE",
            "LOCK_ACQUIRED",
            "ROLLED_BACK",
        }:
            raise ReplayError("deployment journal lost its target records")
        return
    expected_entries = list(manifest["entries"])
    if len(records) != len(expected_entries):
        raise ReplayError("deployment journal target count differs from the exact bundle")
    for record, entry in zip(records, expected_entries):
        if not isinstance(record, dict) or not _valid_prior_state(record.get("prior")):
            raise ReplayError("deployment journal contains an invalid target baseline")
        expected = {
            "new_sha256": entry["sha256"],
            "new_size": entry["size"],
            "source": entry["source"],
            "stage": f"stage/{entry['target']}",
            "target": entry["target"],
        }
        if any(record.get(key) != value for key, value in expected.items()):
            raise ReplayError("deployment journal differs from the exact source bundle")
        expected_backup = (
            f"rollback/{entry['target']}"
            if record["prior"]["state"] == "present"
            else None
        )
        if (
            record.get("backup") != expected_backup
            or not isinstance(record.get("promotion_started"), bool)
            or not isinstance(record.get("promoted"), bool)
        ):
            raise ReplayError("deployment journal contains an invalid rollback binding")
    allowed_directories = set(_missing_target_directories_for_validation(expected_entries))
    created = journal.get("created_directories", [])
    if (
        not isinstance(created, list)
        or len(created) != len(set(created))
        or any(not isinstance(item, str) or item not in allowed_directories for item in created)
    ):
        raise ReplayError("deployment journal contains invalid created-directory state")
    manifest_path = transaction_root / "bundle-manifest.json"
    expected_payload = _manifest_bytes(manifest)
    if (
        not manifest_path.is_file()
        or manifest_path.read_bytes() != expected_payload
        or journal.get("bundle_manifest_sha256") != _sha256_bytes(expected_payload)
    ):
        raise ReplayError("deployment journal bundle artifact differs from the exact source bundle")


def _missing_target_directories_for_validation(
    entries: list[Mapping[str, Any]],
) -> list[str]:
    values: set[str] = set()
    for entry in entries:
        parts = PurePosixPath(str(entry["target"])).parent.parts
        for index in range(1, len(parts) + 1):
            values.add(PurePosixPath(*parts[:index]).as_posix())
    return sorted(values)


def _target_records(
    codex_home: Path,
    transaction_root: Path,
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    rollback_root = transaction_root / "rollback"
    for entry in manifest["entries"]:
        target = _safe_join(codex_home, entry["target"])
        _reject_symlink_chain(codex_home, entry["target"], include_leaf=False)
        prior = _path_state(target)
        backup_relative = f"rollback/{entry['target']}"
        backup = _safe_join(transaction_root, backup_relative)
        if prior["state"] == "present":
            payload = _stable_read(target)
            if _sha256_bytes(payload) != prior["sha256"]:
                raise PreconditionError(f"live target changed while its baseline was captured: {target}")
            _atomic_write_bytes(backup, payload)
        records.append(
            {
                "backup": backup_relative if prior["state"] == "present" else None,
                "new_sha256": entry["sha256"],
                "new_size": entry["size"],
                "prior": prior,
                "promotion_started": False,
                "promoted": False,
                "source": entry["source"],
                "stage": f"stage/{entry['target']}",
                "target": entry["target"],
            }
        )
    if rollback_root.exists():
        _fsync_directory(rollback_root)
    return records


def _stage_sources(
    source_root: Path,
    transaction_root: Path,
    records: list[dict[str, Any]],
) -> None:
    for record in records:
        source = _safe_join(source_root, record["source"])
        payload = _stable_read(source)
        if len(payload) != record["new_size"] or _sha256_bytes(payload) != record["new_sha256"]:
            raise BundleError(f"source changed after its bundle manifest was built: {source}")
        stage = _safe_join(transaction_root, record["stage"])
        _atomic_write_bytes(stage, payload)
        if _sha256_file(stage) != record["new_sha256"]:
            raise DeploymentError(f"staged authority file failed hash verification: {stage}")


def _verify_preconditions(codex_home: Path, records: list[dict[str, Any]]) -> None:
    mismatches = []
    for record in records:
        live = _safe_join(codex_home, record["target"])
        if not _state_matches(live, record["prior"]):
            mismatches.append(record["target"])
    if mismatches:
        raise PreconditionError(
            f"live targets changed after baseline capture: {sorted(mismatches)}"
        )


def _missing_target_directories(codex_home: Path, records: list[dict[str, Any]]) -> list[str]:
    missing: set[str] = set()
    for record in records:
        parent = PurePosixPath(record["target"]).parent
        parts = parent.parts
        for index in range(1, len(parts) + 1):
            relative = PurePosixPath(*parts[:index]).as_posix()
            path = _safe_join(codex_home, relative)
            if path.exists():
                if path.is_symlink() or not path.is_dir():
                    raise PreconditionError(f"target parent is not one real directory: {path}")
            else:
                missing.add(relative)
    return sorted(missing, key=lambda item: (len(PurePosixPath(item).parts), item.encode("utf-8")))


def _create_target_directories(codex_home: Path, relatives: list[str]) -> None:
    for relative in relatives:
        path = _safe_join(codex_home, relative)
        path.mkdir(exist_ok=True)
        _fsync_directory(path.parent)


def _fault(name: str, configured: str | None) -> None:
    if configured == name:
        raise InjectedFailure(f"injected deployment failure at {name}")


def _promote_targets(
    codex_home: Path,
    transaction_root: Path,
    journal: dict[str, Any],
    configured_fault: str | None,
) -> None:
    for index, record in enumerate(journal["targets"], start=1):
        live = _safe_join(codex_home, record["target"])
        if not _state_matches(live, record["prior"]):
            raise PreconditionError(f"live target changed immediately before promotion: {live}")
        stage = _safe_join(transaction_root, record["stage"])
        if not stage.is_file() or _sha256_file(stage) != record["new_sha256"]:
            raise DeploymentError(f"staged target is unavailable or changed: {stage}")
        if stage.stat().st_dev != live.parent.stat().st_dev:
            raise DeploymentError(f"staging is not target-local for atomic promotion: {stage}")
        record["promotion_started"] = True
        journal["phase"] = "PROMOTING"
        _save_journal(transaction_root, journal)
        if not _state_matches(live, record["prior"]):
            raise PreconditionError(f"live target changed at the promotion boundary: {live}")
        os.replace(stage, live)
        _fsync_directory(live.parent)
        if _sha256_file(live) != record["new_sha256"]:
            raise DeploymentError(f"promoted target failed exact hash verification: {live}")
        record["promoted"] = True
        journal["phase"] = "PROMOTING"
        _save_journal(transaction_root, journal)
        _fault(f"after-promote:{index}", configured_fault)


def _remove_created_directories(codex_home: Path, relatives: list[str]) -> None:
    ordered = sorted(
        relatives,
        key=lambda item: (len(PurePosixPath(item).parts), item.encode("utf-8")),
        reverse=True,
    )
    for relative in ordered:
        path = _safe_join(codex_home, relative)
        try:
            path.rmdir()
            _fsync_directory(path.parent)
        except OSError:
            pass


def _rollback_targets(
    codex_home: Path,
    transaction_root: Path,
    journal: dict[str, Any],
) -> None:
    failures: list[str] = []
    for record in reversed(journal.get("targets", [])):
        live = _safe_join(codex_home, record["target"])
        current = _path_state(live)
        prior = record["prior"]
        current_is_new = (
            current["state"] == "present"
            and current["sha256"] == record["new_sha256"]
            and current["size"] == record["new_size"]
        )
        if _state_matches(live, prior):
            record["promotion_started"] = False
            record["promoted"] = False
            continue
        if not record.get("promotion_started") and not record.get("promoted"):
            # This transaction never owned the drift. Preserve the external
            # writer's bytes and let a precondition-failure receipt record the
            # failed compare-and-swap.
            continue
        if not current_is_new:
            failures.append(record["target"])
            continue
        if prior["state"] == "missing":
            live.unlink()
            _fsync_directory(live.parent)
        else:
            backup = _safe_join(transaction_root, str(record["backup"]))
            if not backup.is_file() or _sha256_file(backup) != prior["sha256"]:
                failures.append(record["target"])
                continue
            _atomic_write_bytes(live, backup.read_bytes())
        if not _state_matches(live, prior):
            failures.append(record["target"])
        else:
            record["promotion_started"] = False
            record["promoted"] = False
    _remove_created_directories(codex_home, list(journal.get("created_directories", [])))
    if failures:
        raise RecoveryError(
            f"rollback refused to overwrite drifted targets or lacked exact backups: {sorted(failures)}"
        )


def _recover_interrupted(
    codex_home: Path,
    transaction_root: Path,
    journal: dict[str, Any],
) -> dict[str, Any]:
    try:
        _rollback_targets(codex_home, transaction_root, journal)
    except Exception as exc:
        journal["error"] = f"interrupted recovery failed: {type(exc).__name__}: {exc}"
        journal["phase"] = "ROLLBACK_FAILED"
        _save_journal(transaction_root, journal)
        _write_terminal_receipt(transaction_root, journal, "rollback_failed")
        raise
    journal["error"] = "interrupted transaction recovered to its exact baseline"
    journal["phase"] = "ROLLED_BACK"
    _save_journal(transaction_root, journal)
    return _write_terminal_receipt(transaction_root, journal, "recovered_rolled_back")


def _new_journal(
    transaction_root: Path,
    transaction_id: str,
    codex_home: Path,
    bundle_sha256: str,
) -> dict[str, Any]:
    now = _utc_now()
    return {
        "bundle_sha256": bundle_sha256,
        "codex_home": str(codex_home),
        "created_directories": [],
        "journal_path": str(_journal_path(transaction_root)),
        "phase": "LOCK_ACQUIRED",
        "schema_version": JOURNAL_SCHEMA,
        "started_at": now,
        "targets": [],
        "transaction_id": transaction_id,
        "updated_at": now,
    }


def deploy_router_authority(options: DeploymentOptions) -> dict[str, Any]:
    source_root = Path(options.source_root).expanduser().resolve()
    codex_home = _resolve_codex_home(options.codex_home)
    transaction_id = _validate_transaction_id(options.transaction_id)
    if not SHA256_RE.fullmatch(options.expected_bundle_sha256):
        raise BundleError("expected bundle SHA-256 is invalid")
    _refuse_retired_router_directories(codex_home)
    manifest = build_bundle_manifest(source_root, spec_path=options.spec_path)
    if manifest["bundle_sha256"] != options.expected_bundle_sha256:
        raise BundleError("source bundle does not match the explicitly expected SHA-256")
    _verify_live_preconditions(codex_home, manifest)

    state_root = codex_home / STATE_DIRECTORY
    transactions_root = state_root / "transactions"
    transaction_root = transactions_root / transaction_id
    lock_path = state_root / "deployment.lock"
    state_root.mkdir(parents=True, exist_ok=True)
    if state_root.is_symlink() or not _is_within(state_root.resolve(), codex_home):
        raise DeploymentError("deployment state root is not target-local")

    with _ExclusiveLock(lock_path, options.lock_timeout_seconds):
        if transactions_root.exists() and (
            transactions_root.is_symlink() or not transactions_root.is_dir()
        ):
            raise DeploymentError("deployment transactions root is not one real directory")
        transactions_root.mkdir(exist_ok=True)
        if transaction_root.exists() and (
            transaction_root.is_symlink() or not transaction_root.is_dir()
        ):
            raise DeploymentError("deployment transaction root is not one real directory")
        existing = _existing_receipt(
            transaction_root,
            transaction_id=transaction_id,
            codex_home=codex_home,
            bundle_sha256=manifest["bundle_sha256"],
            manifest=manifest,
        )
        if existing is not None:
            return existing

        journal_file = _journal_path(transaction_root)
        if journal_file.exists():
            journal = _load_json_object(journal_file, "deployment journal")
            if journal.get("schema_version") != JOURNAL_SCHEMA:
                raise ReplayError("deployment journal schema is invalid")
            _verify_transaction_binding(
                journal,
                transaction_id=transaction_id,
                codex_home=codex_home,
                bundle_sha256=manifest["bundle_sha256"],
            )
            _validate_recovery_journal(transaction_root, journal, manifest)
            if not journal["targets"]:
                manifest_payload = _manifest_bytes(manifest)
                _write_once(transaction_root / "bundle-manifest.json", manifest_payload)
                journal["bundle_manifest_sha256"] = _sha256_bytes(manifest_payload)
                _save_journal(transaction_root, journal)
            return _recover_interrupted(codex_home, transaction_root, journal)

        if transaction_root.exists() and any(transaction_root.iterdir()):
            raise ReplayError("transaction workspace exists without a valid durable journal")
        transaction_root.mkdir(parents=True, exist_ok=True)
        journal = _new_journal(
            transaction_root,
            transaction_id,
            codex_home,
            manifest["bundle_sha256"],
        )
        _save_journal(transaction_root, journal)

        try:
            manifest_payload = _manifest_bytes(manifest)
            _write_once(transaction_root / "bundle-manifest.json", manifest_payload)
            journal["bundle_manifest_sha256"] = _sha256_bytes(manifest_payload)
            journal["targets"] = _target_records(codex_home, transaction_root, manifest)
            journal["phase"] = "PREFLIGHT_VERIFIED"
            _save_journal(transaction_root, journal)

            _stage_sources(source_root, transaction_root, journal["targets"])
            journal["phase"] = "STAGE_VERIFIED"
            _save_journal(transaction_root, journal)
            _fault("after-stage", options.fault_injection)

            _verify_preconditions(codex_home, journal["targets"])
            _verify_live_preconditions(codex_home, manifest)
            journal["created_directories"] = _missing_target_directories(
                codex_home, journal["targets"]
            )
            journal["phase"] = "PROMOTION_PREPARED"
            _save_journal(transaction_root, journal)
            _fault("after-prepare", options.fault_injection)

            _create_target_directories(codex_home, journal["created_directories"])
            _promote_targets(
                codex_home,
                transaction_root,
                journal,
                options.fault_injection,
            )
            for record in journal["targets"]:
                live = _safe_join(codex_home, record["target"])
                if not live.is_file() or _sha256_file(live) != record["new_sha256"]:
                    raise DeploymentError(f"live target verification failed: {live}")
            _verify_live_preconditions(codex_home, manifest)
            journal["phase"] = "LIVE_TARGETS_VERIFIED"
            _save_journal(transaction_root, journal)
            _fault("after-verify", options.fault_injection)

            journal["phase"] = "COMPLETED"
            journal.pop("error", None)
            _save_journal(transaction_root, journal)
            return _write_terminal_receipt(transaction_root, journal, "deployed")
        except Exception as exc:
            journal["error"] = f"{type(exc).__name__}: {exc}"
            if "bundle_manifest_sha256" not in journal:
                journal["phase"] = "FAILED_BEFORE_BUNDLE"
                _save_journal(transaction_root, journal)
                raise DeploymentError(journal["error"]) from exc
            try:
                _rollback_targets(codex_home, transaction_root, journal)
            except Exception as rollback_exc:
                journal["error"] += (
                    f" | rollback={type(rollback_exc).__name__}: {rollback_exc}"
                )
                journal["phase"] = "ROLLBACK_FAILED"
                _save_journal(transaction_root, journal)
                _write_terminal_receipt(transaction_root, journal, "rollback_failed")
                raise RecoveryError(journal["error"]) from rollback_exc
            journal["phase"] = "ROLLED_BACK"
            _save_journal(transaction_root, journal)
            outcome = (
                "precondition_failed"
                if isinstance(exc, PreconditionError)
                else "rolled_back"
            )
            return _write_terminal_receipt(transaction_root, journal, outcome)


def _write_manifest_output(path: str | None, manifest: Mapping[str, Any]) -> None:
    payload = _manifest_bytes(manifest)
    if path:
        _atomic_write_bytes(Path(path).expanduser().resolve(), payload)
    else:
        sys.stdout.buffer.write(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or deploy the exact static Catalogue Router authority bundle."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    manifest = subparsers.add_parser("manifest", help="emit the deterministic source bundle")
    manifest.add_argument("--source-root", required=True)
    manifest.add_argument("--spec")
    manifest.add_argument("--output")

    deploy = subparsers.add_parser("deploy", help="run one explicit target-local transaction")
    deploy.add_argument("--source-root", required=True)
    deploy.add_argument("--codex-home", required=True)
    deploy.add_argument("--transaction-id", required=True)
    deploy.add_argument("--expected-bundle-sha256", required=True)
    deploy.add_argument("--spec")
    deploy.add_argument("--lock-timeout-seconds", type=float, default=30.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "manifest":
            value = build_bundle_manifest(arguments.source_root, spec_path=arguments.spec)
            _write_manifest_output(arguments.output, value)
            return 0
        receipt = deploy_router_authority(
            DeploymentOptions(
                source_root=arguments.source_root,
                codex_home=arguments.codex_home,
                transaction_id=arguments.transaction_id,
                expected_bundle_sha256=arguments.expected_bundle_sha256,
                spec_path=arguments.spec,
                lock_timeout_seconds=arguments.lock_timeout_seconds,
            )
        )
        sys.stdout.buffer.write(_canonical_json_bytes(receipt))
        return 0 if receipt.get("outcome") == "deployed" else 2
    except DeploymentError as exc:
        print(f"ROUTER_AUTHORITY_DEPLOYMENT_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
