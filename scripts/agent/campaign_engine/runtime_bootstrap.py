"""Minimal trusted bootstrap for the installed Coding OS runtime.

This module contains no lifecycle logic.  It derives the one production
installation and state location from the operating-system account profile and
verifies every installed bundle byte before the lifecycle package is imported.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


BUNDLE_PROTOCOL = "CCOS-INSTALL-BUNDLE-v1"
BUNDLE_DOMAIN = b"CCOS-INSTALL-BUNDLE-v1\0"
RUNTIME_PROTOCOL_VERSION = "ccos-campaign-v1"
SCHEMA_COMPATIBILITY = "campaign-store-v1"
HOST_CAPABILITY_PROBE_VERSION = "native-bind-before-turn-scoped-tools-v3"
RUNTIME_PIN_FIELDS = frozenset(
    {
        "source_commit",
        "bundle_digest",
        "install_transaction",
        "protocol_version",
        "schema_compatibility",
        "host_capability_probe_version",
    }
)
REQUIRED_CANCEL_FILES = frozenset(
    f"scripts/agent/campaign_engine/{name}"
    for name in (
        "__init__.py",
        "admission.py",
        "cli.py",
        "ed25519.py",
        "effects.py",
        "evidence.py",
        "host.py",
        "legacy.py",
        "model.py",
        "reducer.py",
        "runtime_bootstrap.py",
        "store.py",
        "supervisor.py",
    )
)
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
TRANSACTION_ID = re.compile(r"^[0-9a-f]{32}$")


class RuntimeBootstrapError(RuntimeError):
    """The canonical runtime cannot be trusted enough to launch the engine."""


@dataclass(frozen=True, slots=True)
class RuntimeLayout:
    profile: Path
    codex_home: Path
    installed_root: Path
    skills_root: Path
    state_db: Path
    install_manifest: Path
    bundle_manifest: Path


@dataclass(frozen=True, slots=True)
class BootstrapEvidence:
    source_commit: str
    bundle_digest: str
    install_transaction: str
    protocol_version: str
    schema_compatibility: str
    host_capability_probe_version: str
    bundle_manifest_sha256: str
    verified_file_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_commit": self.source_commit,
            "bundle_digest": self.bundle_digest,
            "install_transaction": self.install_transaction,
            "protocol_version": self.protocol_version,
            "schema_compatibility": self.schema_compatibility,
            "host_capability_probe_version": self.host_capability_probe_version,
            "bundle_manifest_sha256": self.bundle_manifest_sha256,
            "verified_file_count": self.verified_file_count,
        }


def _is_reparse_point(metadata: os.stat_result) -> bool:
    if stat.S_ISLNK(metadata.st_mode):
        return True
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and getattr(metadata, "st_file_attributes", 0) & flag)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(
        os.path.abspath(os.fspath(right))
    )


def _direct_path(path: Path, label: str, *, must_exist: bool) -> Path:
    if not path.is_absolute():
        raise RuntimeBootstrapError(f"{label} must be absolute")
    direct = Path(os.path.abspath(os.fspath(path)))
    try:
        resolved = direct.resolve(strict=must_exist)
    except OSError as exc:
        raise RuntimeBootstrapError(f"{label} is unavailable at {direct}") from exc
    current = Path(direct.anchor)
    for part in direct.parts[1:]:
        current /= part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            if must_exist:
                raise RuntimeBootstrapError(f"{label} is unavailable at {direct}")
            break
        except OSError as exc:
            raise RuntimeBootstrapError(f"{label} cannot be inspected at {current}") from exc
        if _is_reparse_point(metadata):
            raise RuntimeBootstrapError(
                f"{label} must not traverse a symbolic link or reparse point"
            )
    return resolved


def trusted_account_profile() -> Path:
    """Resolve the account profile without consulting caller environment data."""

    if os.name == "nt":
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        result = ctypes.windll.shell32.SHGetFolderPathW(
            None, 0x0028, None, 0, buffer
        )
        raw = buffer.value if result == 0 else ""
    else:
        import pwd

        try:
            raw = pwd.getpwuid(os.getuid()).pw_dir
        except (KeyError, OSError, AttributeError) as exc:
            raise RuntimeBootstrapError(
                "operating-system account profile is unavailable"
            ) from exc
    if not raw:
        raise RuntimeBootstrapError("operating-system account profile is unavailable")
    return _direct_path(Path(raw), "operating-system account profile", must_exist=True)


def runtime_layout(*, profile: Path | None = None) -> RuntimeLayout:
    """Build canonical paths. ``profile`` is an in-process test seam only."""

    account_profile = _direct_path(
        profile if profile is not None else trusted_account_profile(),
        "operating-system account profile",
        must_exist=True,
    )
    codex_home = _direct_path(
        account_profile / ".codex", "canonical Codex home", must_exist=True
    )
    installed_root = _direct_path(
        codex_home / "coding-os", "installed Coding OS root", must_exist=True
    )
    skills_root = _direct_path(
        codex_home / "skills", "installed Coding OS skills root", must_exist=True
    )
    state_db = _direct_path(
        codex_home / "coding-os-state" / "campaigns.sqlite3",
        "canonical Coding OS state database",
        must_exist=False,
    )
    return RuntimeLayout(
        profile=account_profile,
        codex_home=codex_home,
        installed_root=installed_root,
        skills_root=skills_root,
        state_db=state_db,
        install_manifest=installed_root / "install-manifest.json",
        bundle_manifest=installed_root / "install-bundle.manifest.json",
    )


def _regular_file(path: Path, label: str) -> Path:
    direct = _direct_path(path, label, must_exist=True)
    metadata = os.lstat(direct)
    if not stat.S_ISREG(metadata.st_mode) or _is_reparse_point(metadata):
        raise RuntimeBootstrapError(f"{label} must be an exact regular file")
    return direct


def verify_launcher(layout: RuntimeLayout, launcher: Path) -> None:
    """Require production execution to originate at the canonical CLI file."""

    actual = _regular_file(launcher, "campaign engine launcher")
    expected = _regular_file(
        layout.installed_root / "scripts" / "agent" / "campaign_engine" / "cli.py",
        "installed campaign engine launcher",
    )
    if not _same_path(actual, expected):
        raise RuntimeBootstrapError(
            "campaign engine must launch from the canonical installed runtime"
        )


def _load_object(path: Path, label: str) -> dict[str, Any]:
    source = _regular_file(path, label)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeBootstrapError(f"{label} is unreadable or invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeBootstrapError(f"{label} must be a JSON object")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RuntimeBootstrapError("installed bundle contains an invalid path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeBootstrapError(f"installed bundle contains an unsafe path: {value!r}")
    return path.as_posix()


def _aggregate_entries(entries: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update(BUNDLE_DOMAIN)
    for entry in sorted(entries, key=lambda item: str(item.get("path", "")).encode("utf-8")):
        relative = _relative_path(entry.get("path"))
        size = entry.get("size")
        file_digest = entry.get("sha256")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(file_digest, str)
            or not SHA256.fullmatch(file_digest)
        ):
            raise RuntimeBootstrapError(f"invalid installed bundle entry: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_digest))
    return digest.hexdigest()


def _entry_path(relative: str, layout: RuntimeLayout) -> Path:
    parts = PurePosixPath(relative).parts
    if len(parts) >= 3 and parts[:2] == (".agents", "skills"):
        return layout.skills_root.joinpath(*parts[2:])
    return layout.installed_root.joinpath(*parts)


def verify_installed_bundle(layout: RuntimeLayout) -> BootstrapEvidence:
    """Verify the runtime pin, aggregate, inventory, and every installed file."""

    manifest = _load_object(layout.install_manifest, "install manifest")
    bundle = _load_object(layout.bundle_manifest, "installed bundle manifest")
    pin = manifest.get("runtime_pin")
    source = manifest.get("source")
    package = manifest.get("package")
    transaction = manifest.get("transaction")
    targets = manifest.get("targets")
    if not isinstance(pin, dict) or set(pin) != RUNTIME_PIN_FIELDS:
        raise RuntimeBootstrapError("runtime_pin must contain exactly six fields")
    if not all(isinstance(value, dict) for value in (source, package, transaction, targets)):
        raise RuntimeBootstrapError("install manifest identity sections are malformed")

    source_commit = str(pin.get("source_commit", ""))
    bundle_digest = str(pin.get("bundle_digest", ""))
    install_transaction = str(pin.get("install_transaction", ""))
    protocol_version = str(pin.get("protocol_version", ""))
    schema_compatibility = str(pin.get("schema_compatibility", ""))
    host_probe = str(pin.get("host_capability_probe_version", ""))
    if not SHA40.fullmatch(source_commit):
        raise RuntimeBootstrapError("runtime_pin.source_commit is invalid")
    if not SHA256.fullmatch(bundle_digest):
        raise RuntimeBootstrapError("runtime_pin.bundle_digest is invalid")
    if not TRANSACTION_ID.fullmatch(install_transaction):
        raise RuntimeBootstrapError("runtime_pin.install_transaction is invalid")
    if protocol_version != RUNTIME_PROTOCOL_VERSION:
        raise RuntimeBootstrapError("runtime_pin.protocol_version is incompatible")
    if schema_compatibility != SCHEMA_COMPATIBILITY:
        raise RuntimeBootstrapError("runtime_pin.schema_compatibility is incompatible")
    if host_probe != HOST_CAPABILITY_PROBE_VERSION:
        raise RuntimeBootstrapError("runtime_pin.host_capability_probe_version is incompatible")
    if (
        source.get("git_commit") != source_commit
        or package.get("bundle_sha256") != bundle_digest
        or transaction.get("id") != install_transaction
    ):
        raise RuntimeBootstrapError(
            "runtime pin conflicts with source, package, or transaction record"
        )
    if not _same_path(Path(str(targets.get("support_root", ""))), layout.installed_root):
        raise RuntimeBootstrapError("install manifest support_root is not canonical")
    if not _same_path(Path(str(targets.get("skills_root", ""))), layout.skills_root):
        raise RuntimeBootstrapError("install manifest skills_root is not canonical")

    if bundle.get("protocol") != BUNDLE_PROTOCOL:
        raise RuntimeBootstrapError("installed bundle protocol is incompatible")
    raw_entries = bundle.get("entries")
    if not isinstance(raw_entries, list) or not all(
        isinstance(entry, dict) for entry in raw_entries
    ):
        raise RuntimeBootstrapError("installed bundle entries are malformed")
    paths = [_relative_path(entry.get("path")) for entry in raw_entries]
    if len({path.casefold() for path in paths}) != len(paths):
        raise RuntimeBootstrapError("installed bundle contains duplicate or colliding paths")
    required_bootstrap_paths = {
        "scripts/agent/campaign_engine/cli.py",
        "scripts/agent/campaign_engine/runtime_bootstrap.py",
    }
    if not required_bootstrap_paths.issubset(paths):
        raise RuntimeBootstrapError(
            "installed bundle inventory omits the mandatory runtime bootstrap"
        )
    aggregate = _aggregate_entries(raw_entries)
    if bundle.get("aggregate_sha256") != aggregate or bundle_digest != aggregate:
        raise RuntimeBootstrapError("installed bundle aggregate differs from its runtime pin")

    for entry, relative in zip(raw_entries, paths, strict=True):
        target = _regular_file(_entry_path(relative, layout), f"installed file {relative}")
        metadata = target.stat()
        if metadata.st_size != entry.get("size") or _sha256_file(target) != entry.get("sha256"):
            raise RuntimeBootstrapError(f"installed bundle file differs from its pin: {relative}")

    bundle_manifest_sha256 = _sha256_file(layout.bundle_manifest)
    if source.get("bundle_manifest_sha256") != bundle_manifest_sha256:
        raise RuntimeBootstrapError("installed bundle manifest differs from its source pin")
    return BootstrapEvidence(
        source_commit=source_commit,
        bundle_digest=bundle_digest,
        install_transaction=install_transaction,
        protocol_version=protocol_version,
        schema_compatibility=schema_compatibility,
        host_capability_probe_version=host_probe,
        bundle_manifest_sha256=bundle_manifest_sha256,
        verified_file_count=len(raw_entries),
    )


def verify_cancel_dependency_closure(layout: RuntimeLayout) -> BootstrapEvidence:
    """Verify the exact pinned dependency closure needed to execute STOP.

    This is the only bundle-drift exception.  It validates the pinned manifests,
    aggregate, and every module imported by the cancellation path, while allowing
    drift in an unrelated installed asset to remain non-blocking for STOP.
    """

    manifest = _load_object(layout.install_manifest, "install manifest")
    bundle = _load_object(layout.bundle_manifest, "installed bundle manifest")
    pin = manifest.get("runtime_pin")
    source = manifest.get("source")
    package = manifest.get("package")
    transaction = manifest.get("transaction")
    targets = manifest.get("targets")
    if not isinstance(pin, dict) or set(pin) != RUNTIME_PIN_FIELDS:
        raise RuntimeBootstrapError("runtime_pin must contain exactly six fields")
    if not all(isinstance(value, dict) for value in (source, package, transaction, targets)):
        raise RuntimeBootstrapError("install manifest identity sections are malformed")
    if bundle.get("protocol") != BUNDLE_PROTOCOL:
        raise RuntimeBootstrapError("installed bundle protocol is incompatible")
    entries = bundle.get("entries")
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise RuntimeBootstrapError("installed bundle entries are malformed")
    paths = [_relative_path(entry.get("path")) for entry in entries]
    if len({item.casefold() for item in paths}) != len(paths):
        raise RuntimeBootstrapError("installed bundle contains duplicate or colliding paths")
    if not REQUIRED_CANCEL_FILES.issubset(paths):
        raise RuntimeBootstrapError("installed bundle omits the pinned cancellation closure")
    aggregate = _aggregate_entries(entries)
    source_commit = str(pin.get("source_commit", ""))
    bundle_digest = str(pin.get("bundle_digest", ""))
    install_transaction = str(pin.get("install_transaction", ""))
    protocol_version = str(pin.get("protocol_version", ""))
    schema_compatibility = str(pin.get("schema_compatibility", ""))
    host_probe = str(pin.get("host_capability_probe_version", ""))
    if (
        not SHA40.fullmatch(source_commit)
        or not SHA256.fullmatch(bundle_digest)
        or not TRANSACTION_ID.fullmatch(install_transaction)
        or protocol_version != RUNTIME_PROTOCOL_VERSION
        or schema_compatibility != SCHEMA_COMPATIBILITY
        or host_probe != HOST_CAPABILITY_PROBE_VERSION
        or bundle.get("aggregate_sha256") != aggregate
        or bundle_digest != aggregate
        or source.get("git_commit") != source_commit
        or package.get("bundle_sha256") != bundle_digest
        or transaction.get("id") != install_transaction
        or not _same_path(Path(str(targets.get("support_root", ""))), layout.installed_root)
        or not _same_path(Path(str(targets.get("skills_root", ""))), layout.skills_root)
        or source.get("bundle_manifest_sha256") != _sha256_file(layout.bundle_manifest)
    ):
        raise RuntimeBootstrapError("pinned cancellation runtime identity is invalid")
    entries_by_path = dict(zip(paths, entries, strict=True))
    for relative in sorted(REQUIRED_CANCEL_FILES):
        entry = entries_by_path[relative]
        target = _regular_file(_entry_path(relative, layout), f"cancellation file {relative}")
        if target.stat().st_size != entry.get("size") or _sha256_file(target) != entry.get("sha256"):
            raise RuntimeBootstrapError(
                f"pinned cancellation file differs from its pin: {relative}"
            )
    return BootstrapEvidence(
        source_commit=source_commit,
        bundle_digest=bundle_digest,
        install_transaction=install_transaction,
        protocol_version=protocol_version,
        schema_compatibility=schema_compatibility,
        host_capability_probe_version=host_probe,
        bundle_manifest_sha256=_sha256_file(layout.bundle_manifest),
        verified_file_count=len(REQUIRED_CANCEL_FILES),
    )
