#!/usr/bin/env python3
"""Transactional installer and uninstaller for the Codex Coding OS bundle.

Only Python's standard library is used. PowerShell and Bash are deliberately
thin argument adapters around this file so both platforms execute one state
machine and one recovery contract.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unicodedata
import uuid
from typing import Any, Iterator, Sequence


TRANSACTION_PROTOCOL = "ccos-install-transaction-v1"
BUNDLE_PROTOCOL = "CCOS-INSTALL-BUNDLE-v1"
BUNDLE_DOMAIN = b"CCOS-INSTALL-BUNDLE-v1\0"
TREE_DOMAIN = b"CCOS-TREE-v1\0"
MANIFEST_VERSION = 3
JOURNAL_VERSION = 1
PHASES = (
    "LOCK_ACQUIRED",
    "PREFLIGHT_VERIFIED",
    "SOURCE_VERIFIED",
    "STAGE_VERIFIED",
    "PROMOTION_PREPARED",
    "PROMOTING",
    "LIVE_TARGETS_VERIFIED",
    "CURRENT_POINTER_COMMITTED",
    "CLEANUP_COMPLETE",
)

AGENTS_START = "<!-- BEGIN CODEX CODING OS MANAGED: AUTOMATION-PRESERVING CASE POLICY -->"
AGENTS_END = "<!-- END CODEX CODING OS MANAGED: AUTOMATION-PRESERVING CASE POLICY -->"
AGENTS_LEGACY_LINE = (
    "  - Manual Session And Case Isolation Policy: parent-orchestrator mode and automatic session, review, "
    "and review-fix trains are disabled. A human may deliberately start one bounded implementation or review "
    "session, but no session may automatically spawn, authorize, or chain another session."
)
RULES_START = "# BEGIN CODEX CODING OS MANAGED: GH PR MERGE AUTHORITY"
RULES_END = "# END CODEX CODING OS MANAGED: GH PR MERGE AUTHORITY"
RULES_LEGACY_LINE = 'prefix_rule(pattern=["gh", "pr", "merge"], decision="allow")'
UNIVERSAL_BUNDLE_ID = "automation-preserving-case-state-recovery-v1"
UNIVERSAL_BUNDLE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,126}[a-z0-9])?$")
LEGACY_OVERLAP_LAYOUT = "codex-home-skills-v2-to-v3"
LEGACY_V2_PACKAGES = frozenset({"codex-coding-os", "codex-coding-os-starter"})
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
CASE_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class TransactionError(RuntimeError):
    """Base fail-closed error for install and uninstall operations."""


class BundleError(TransactionError):
    pass


class PolicyMigrationError(TransactionError):
    pass


class SourceVerificationError(TransactionError):
    pass


class AuthorityError(TransactionError):
    pass


class OwnershipError(TransactionError):
    pass


class LockError(TransactionError):
    pass


class RecoveryError(TransactionError):
    pass


class InjectedFailure(TransactionError):
    pass


@dataclass(frozen=True)
class BundleInfo:
    aggregate_sha256: str
    manifest_sha256: str
    entries: tuple[dict[str, Any], ...]
    pack: dict[str, Any]
    manifest: dict[str, Any]


@dataclass(frozen=True)
class InstallOptions:
    source_root: Path | str
    skills_root: Path | str
    codex_home: Path | str
    expected_bundle_sha256: str
    expected_source_commit: str | None = None
    install_universal_policy: bool = False
    universal_bundle_id: str = UNIVERSAL_BUNDLE_ID
    refresh_capability_index: bool = False
    authority_case_id: str | None = None
    authority_source: str | None = None
    authority_reference: str | None = None
    case_state_engine: Path | str | None = None
    case_state_root: Path | str | None = None
    legacy_overlap_migration: bool = False
    archive_mode: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class UninstallOptions:
    skills_root: Path | str
    codex_home: Path | str
    legacy_overlap_migration: bool = False
    dry_run: bool = False


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_relative(value: str) -> str:
    normalized = unicodedata.normalize("NFC", str(value).replace("\\", "/"))
    pure = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise BundleError(f"unsafe repository-relative path: {value!r}")
    return pure.as_posix()


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _assert_no_link_components(path: Path, boundary: Path | None = None) -> None:
    candidate = path.resolve(strict=False) if not path.exists() else path.absolute()
    stop = boundary.resolve(strict=False) if boundary is not None else None
    chain: list[Path] = []
    current = candidate
    while True:
        chain.append(current)
        if stop is not None and current == stop:
            break
        if current.parent == current:
            break
        current = current.parent
    for item in reversed(chain):
        if item.exists() and _is_link_or_reparse(item):
            raise TransactionError(f"links and reparse points are not allowed: {item}")


def _safe_repo_path(root: Path, relative: str) -> Path:
    normalized = _normalize_relative(relative)
    target = root.joinpath(*PurePosixPath(normalized).parts)
    if not _path_is_within(target, root):
        raise BundleError(f"bundle path escapes repository root: {relative}")
    return target


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransactionError(f"{label} is not readable strict UTF-8 JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TransactionError(f"{label} must be a JSON object: {path}")
    return value


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_link_components(path.parent)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write_bytes(path, encoded)


def _validate_casefold_collisions(paths: Sequence[str], label: str) -> None:
    seen: dict[str, str] = {}
    for path in paths:
        key = unicodedata.normalize("NFC", path).casefold()
        prior = seen.get(key)
        if prior is not None and prior != path:
            raise BundleError(f"case-insensitive {label} collision: {prior!r} and {path!r}")
        if prior is not None:
            raise BundleError(f"duplicate {label} entry: {path!r}")
        seen[key] = path


def _is_git_worktree(root: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0 and completed.stdout.strip().lower() == "true"


def _git_ignored_paths(root: Path, paths: Sequence[str]) -> set[str]:
    if not paths or not _is_git_worktree(root):
        return set()
    payload = b"".join(_normalize_relative(path).encode("utf-8") + b"\0" for path in paths)
    completed = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-z", "--stdin"],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode not in (0, 1):
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise BundleError(f"unable to identify ignored bundle artifacts: {detail or completed.returncode}")
    ignored = {
        _normalize_relative(value.decode("utf-8", errors="strict"))
        for value in completed.stdout.split(b"\0")
        if value
    }
    unexpected = ignored.difference(paths)
    if unexpected:
        raise BundleError("Git reported an unexpected ignored bundle path")
    return ignored


def _reject_untracked_bundle_paths(root: Path, paths: Sequence[str]) -> None:
    if not paths or not _is_git_worktree(root):
        return
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise BundleError(f"unable to identify tracked bundle paths: {detail or completed.returncode}")
    tracked = {
        _normalize_relative(value.decode("utf-8", errors="strict"))
        for value in completed.stdout.split(b"\0")
        if value
    }
    untracked = sorted(set(paths).difference(tracked), key=lambda value: value.encode("utf-8"))
    if untracked:
        raise BundleError(
            "refusing to build a public bundle from untracked pack-owned paths: " + ", ".join(untracked)
        )


def _enumerate_files(root: Path, relative: str) -> list[str]:
    normalized = _normalize_relative(relative)
    target = _safe_repo_path(root, normalized)
    if normalized == "install-bundle.manifest.json" and not target.exists():
        return []
    if not target.exists():
        raise BundleError(f"declared bundle path does not exist: {normalized}")
    _assert_no_link_components(target, root)
    if _is_link_or_reparse(target):
        raise BundleError(f"bundle source cannot be a link or reparse point: {normalized}")
    if target.is_file():
        return [] if normalized == "install-bundle.manifest.json" else [normalized]
    if not target.is_dir():
        raise BundleError(f"bundle source must be a file or directory: {normalized}")
    found: list[str] = []
    for directory, dir_names, file_names in os.walk(target, followlinks=False):
        directory_path = Path(directory)
        # Python bytecode is local runtime state, not pack-owned source. Ignoring it
        # keeps a manifest built after local tests valid in a clean release checkout.
        dir_names[:] = [name for name in dir_names if name.casefold() != "__pycache__"]
        for name in list(dir_names):
            child = directory_path / name
            if _is_link_or_reparse(child):
                raise BundleError(f"bundle source cannot contain links or reparse points: {child.relative_to(root)}")
        for name in file_names:
            if name.casefold().endswith(".pyc"):
                continue
            child = directory_path / name
            if _is_link_or_reparse(child) or not child.is_file():
                raise BundleError(f"bundle source cannot contain special files: {child.relative_to(root)}")
            child_relative = unicodedata.normalize("NFC", child.relative_to(root).as_posix())
            if child_relative != "install-bundle.manifest.json":
                found.append(_normalize_relative(child_relative))
    return found


def _load_pack(root: Path) -> dict[str, Any]:
    pack_path = root / "pack.manifest.json"
    pack = _load_json(pack_path, "pack manifest")
    if pack.get("package_name") != "codex-coding-os":
        raise BundleError("pack manifest package_name must be codex-coding-os")
    if not isinstance(pack.get("version"), str):
        raise BundleError("pack manifest version is required")
    installation = pack.get("installation")
    if not isinstance(installation, dict):
        raise BundleError("pack manifest installation contract is required")
    if installation.get("transaction_protocol") != TRANSACTION_PROTOCOL:
        raise BundleError("pack transaction protocol does not match installer")
    if installation.get("bundle_protocol") != BUNDLE_PROTOCOL:
        raise BundleError("pack bundle protocol does not match installer")
    if installation.get("bundle_manifest") != "install-bundle.manifest.json":
        raise BundleError("pack bundle manifest path must be install-bundle.manifest.json")
    if installation.get("external_skills_staged") is not False:
        raise BundleError("this public package must declare external_skills_staged=false")
    return pack


def _inventory_paths(root: Path, pack: dict[str, Any]) -> list[str]:
    installation = pack["installation"]
    skill_root = _normalize_relative(str(installation.get("managed_skill_root", "")))
    declared: list[str] = ["pack.manifest.json"]
    skill_names: list[str] = []
    for skill in pack.get("bundled_skills", []):
        if not isinstance(skill, dict) or not isinstance(skill.get("name"), str):
            raise BundleError("every bundled skill requires a string name")
        name = _normalize_relative(skill["name"])
        if "/" in name:
            raise BundleError(f"bundled skill name must be one path segment: {name}")
        skill_names.append(name)
        declared.append(f"{skill_root}/{name}")
    _validate_casefold_collisions(skill_names, "managed skill name")
    support_items = pack.get("support_items")
    if not isinstance(support_items, list) or not support_items:
        raise BundleError("support_items must be a non-empty list")
    declared.extend(str(value) for value in support_items)
    runtime_files = installation.get("runtime_files")
    if not isinstance(runtime_files, list) or not runtime_files:
        raise BundleError("installation.runtime_files must be a non-empty list")
    declared.extend(str(value) for value in runtime_files)
    policies = installation.get("universal_policy_sources")
    if not isinstance(policies, dict) or set(policies) != {"global_agents", "default_rules"}:
        raise BundleError("installation.universal_policy_sources must declare global_agents and default_rules")
    declared.extend(str(value) for value in policies.values())
    files: set[str] = set()
    for item in declared:
        files.update(_enumerate_files(root, item))
    files.difference_update(_git_ignored_paths(root, tuple(files)))
    ordered = sorted(files, key=lambda value: value.encode("utf-8"))
    _validate_casefold_collisions(ordered, "bundle path")
    return ordered


def _entry_for(root: Path, relative: str) -> dict[str, Any]:
    path = _safe_repo_path(root, relative)
    before = path.stat()
    digest = _sha_file(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise BundleError(f"bundle file changed while hashing: {relative}")
    return {"path": relative, "size": before.st_size, "sha256": digest}


def _aggregate_entries(entries: Sequence[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update(BUNDLE_DOMAIN)
    ordered = sorted(entries, key=lambda entry: str(entry["path"]).encode("utf-8"))
    for entry in ordered:
        path = _normalize_relative(str(entry.get("path", "")))
        size = entry.get("size")
        file_hash = str(entry.get("sha256", ""))
        if not isinstance(size, int) or size < 0 or not HASH_RE.fullmatch(file_hash):
            raise BundleError(f"invalid bundle entry metadata for {path}")
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_hash))
    return digest.hexdigest()


def build_bundle_manifest(repo_root: Path | str) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve(strict=True)
    pack = _load_pack(root)
    paths = _inventory_paths(root, pack)
    _reject_untracked_bundle_paths(root, paths)
    entries = [_entry_for(root, relative) for relative in paths]
    manifest = {
        "protocol": BUNDLE_PROTOCOL,
        "package": {"name": pack["package_name"], "version": pack["version"]},
        "aggregate_sha256": _aggregate_entries(entries),
        "entries": entries,
    }
    _atomic_write_json(root / "install-bundle.manifest.json", manifest)
    return manifest


def verify_bundle(repo_root: Path | str, expected_aggregate: str | None = None) -> BundleInfo:
    root = Path(repo_root).expanduser().resolve(strict=True)
    pack = _load_pack(root)
    manifest_path = root / "install-bundle.manifest.json"
    manifest = _load_json(manifest_path, "install bundle manifest")
    if manifest.get("protocol") != BUNDLE_PROTOCOL:
        raise BundleError("install bundle protocol mismatch")
    package = manifest.get("package")
    if not isinstance(package, dict) or package.get("name") != pack["package_name"] or package.get("version") != pack["version"]:
        raise BundleError("install bundle package metadata does not match pack manifest")
    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list):
        raise BundleError("install bundle entries must be a list")
    paths = [_normalize_relative(str(entry.get("path", ""))) for entry in raw_entries if isinstance(entry, dict)]
    if len(paths) != len(raw_entries):
        raise BundleError("every install bundle entry must be an object")
    _validate_casefold_collisions(paths, "bundle manifest path")
    actual_paths = _inventory_paths(root, pack)
    if paths != actual_paths:
        raise BundleError("install bundle manifest inventory does not exactly match the pack-owned inventory")
    actual_entries = [_entry_for(root, relative) for relative in actual_paths]
    if raw_entries != actual_entries:
        raise BundleError("install bundle file size or SHA256 does not match live source bytes")
    aggregate = _aggregate_entries(raw_entries)
    if manifest.get("aggregate_sha256") != aggregate:
        raise BundleError("install bundle aggregate SHA256 is invalid")
    if expected_aggregate is not None:
        expected = expected_aggregate.lower()
        if not HASH_RE.fullmatch(expected) or aggregate != expected:
            raise BundleError(f"expected bundle SHA256 {expected_aggregate} does not match {aggregate}")
    return BundleInfo(
        aggregate_sha256=aggregate,
        manifest_sha256=_sha_file(manifest_path),
        entries=tuple(actual_entries),
        pack=pack,
        manifest=manifest,
    )


def _strict_utf8(value: bytes, label: str) -> None:
    try:
        value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PolicyMigrationError(f"{label} must be strict UTF-8: {exc}") from exc


def _trim_policy_source(value: bytes, label: str, start: str, end: str) -> bytes:
    _strict_utf8(value, label)
    trimmed = value.rstrip(b"\r\n")
    if not trimmed.startswith(start.encode("utf-8")) or not trimmed.endswith(end.encode("utf-8")):
        raise PolicyMigrationError(f"{label} source does not contain the exact managed marker block")
    if trimmed.count(start.encode("utf-8")) != 1 or trimmed.count(end.encode("utf-8")) != 1:
        raise PolicyMigrationError(f"{label} source must contain exactly one marker pair")
    return trimmed


def _replace_line_or_marker(
    existing: bytes,
    replacement: bytes,
    *,
    legacy_line: str,
    start: str,
    end: str,
    label: str,
) -> bytes:
    _strict_utf8(existing, label)
    replacement = _trim_policy_source(replacement, label, start, end)
    start_bytes = start.encode("utf-8")
    end_bytes = end.encode("utf-8")
    start_count = existing.count(start_bytes)
    end_count = existing.count(end_bytes)
    legacy = legacy_line.encode("utf-8")
    if start_count or end_count:
        if start_count != 1 or end_count != 1:
            raise PolicyMigrationError(f"{label} has a missing, duplicate, or partial managed marker block")
        begin = existing.find(start_bytes)
        finish_start = existing.find(end_bytes)
        if finish_start < begin:
            raise PolicyMigrationError(f"{label} managed marker order is invalid")
        finish = finish_start + len(end_bytes)
        return existing[:begin] + replacement + existing[finish:]
    if existing.count(legacy) != 1:
        raise PolicyMigrationError(f"{label} first migration requires exactly one byte-exact legacy line")
    begin = existing.find(legacy)
    before_ok = begin == 0 or existing[begin - 1 : begin] in {b"\n", b"\r"}
    after_index = begin + len(legacy)
    after_ok = after_index == len(existing) or existing[after_index : after_index + 1] in {b"\n", b"\r"}
    if not before_ok or not after_ok:
        raise PolicyMigrationError(f"{label} legacy text is not an exact complete line")
    return existing[:begin] + replacement + existing[after_index:]


def _remove_exact_marker(existing: bytes, start: str, end: str, label: str) -> bytes:
    _strict_utf8(existing, label)
    start_bytes = start.encode("utf-8")
    end_bytes = end.encode("utf-8")
    if existing.count(start_bytes) != 1 or existing.count(end_bytes) != 1:
        raise PolicyMigrationError(f"{label} uninstall requires exactly one complete managed marker pair")
    begin = existing.find(start_bytes)
    finish_start = existing.find(end_bytes)
    if finish_start < begin:
        raise PolicyMigrationError(f"{label} managed marker order is invalid")
    return existing[:begin] + existing[finish_start + len(end_bytes) :]


def migrate_agents_bytes(existing: bytes, policy_source: bytes) -> bytes:
    return _replace_line_or_marker(
        existing,
        policy_source,
        legacy_line=AGENTS_LEGACY_LINE,
        start=AGENTS_START,
        end=AGENTS_END,
        label="AGENTS.md",
    )


def migrate_rules_bytes(existing: bytes, policy_source: bytes) -> bytes:
    return _replace_line_or_marker(
        existing,
        policy_source,
        legacy_line=RULES_LEGACY_LINE,
        start=RULES_START,
        end=RULES_END,
        label="default.rules",
    )


def remove_agents_policy_bytes(existing: bytes) -> bytes:
    return _remove_exact_marker(existing, AGENTS_START, AGENTS_END, "AGENTS.md")


def remove_rules_policy_bytes(existing: bytes) -> bytes:
    return _remove_exact_marker(existing, RULES_START, RULES_END, "default.rules")


@contextlib.contextmanager
def exclusive_install_lock(state_root: Path | str, transaction_id: str, operation: str) -> Iterator[None]:
    root = Path(state_root).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    _assert_no_link_components(root)
    lock_path = root / "install.lock"
    payload = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "transaction_id": transaction_id,
        "operation": operation,
        "started_at": utc_now(),
    }
    handle: Any = None
    try:
        if os.name == "nt":
            import ctypes
            import msvcrt

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create_file = kernel32.CreateFileW
            create_file.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
            ]
            create_file.restype = ctypes.c_void_p
            raw_handle = create_file(str(lock_path), 0xC0000000, 0, None, 4, 0x80, None)
            invalid = ctypes.c_void_p(-1).value
            if raw_handle == invalid:
                error = ctypes.get_last_error()
                raise LockError(f"another install transaction holds the exclusive lock (Windows error {error})")
            fd = msvcrt.open_osfhandle(int(raw_handle), os.O_RDWR)
            handle = os.fdopen(fd, "r+b", buffering=0)
        else:
            import fcntl

            handle = lock_path.open("a+b", buffering=0)
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise LockError("another install transaction holds the exclusive lock") from exc
        encoded = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        handle.seek(0)
        handle.truncate()
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
        yield
    finally:
        if handle is not None:
            if os.name != "nt":
                try:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()


def _tree_entries(root: Path, excluded: set[str] | None = None) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    if _is_link_or_reparse(root):
        raise TransactionError(f"managed target cannot be a link or reparse point: {root}")
    excluded = excluded or set()
    entries: list[dict[str, Any]] = []
    if root.is_file():
        return [{"path": root.name, "size": root.stat().st_size, "sha256": _sha_file(root)}]
    for directory, dir_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in list(dir_names):
            child = directory_path / name
            if _is_link_or_reparse(child):
                raise TransactionError(f"managed target cannot contain links or reparse points: {child}")
        for name in file_names:
            child = directory_path / name
            if _is_link_or_reparse(child) or not child.is_file():
                raise TransactionError(f"managed target cannot contain special files: {child}")
            relative = unicodedata.normalize("NFC", child.relative_to(root).as_posix())
            if relative in excluded:
                continue
            entries.append({"path": relative, "size": child.stat().st_size, "sha256": _sha_file(child)})
    entries.sort(key=lambda entry: entry["path"].encode("utf-8"))
    _validate_casefold_collisions([entry["path"] for entry in entries], "managed target path")
    return entries


def _tree_hash(root: Path, excluded: set[str] | None = None) -> str | None:
    if not root.exists():
        return None
    if root.is_file():
        return _sha_file(root)
    digest = hashlib.sha256()
    digest.update(TREE_DOMAIN)
    for entry in _tree_entries(root, excluded):
        digest.update(entry["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry["size"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(entry["sha256"]))
    return digest.hexdigest()


def _copy_path(source: Path, target: Path) -> None:
    if _is_link_or_reparse(source):
        raise TransactionError(f"refusing to copy link or reparse point: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if target.exists():
            raise TransactionError(f"stage target already exists: {target}")
        shutil.copytree(source, target, symlinks=False)
    elif source.is_file():
        shutil.copy2(source, target)
    else:
        raise TransactionError(f"source is not a regular file or directory: {source}")


def _remove_owned_path(path: Path) -> None:
    if not path.exists():
        return
    if _is_link_or_reparse(path):
        raise RecoveryError(f"refusing to remove a link or reparse point: {path}")
    if path.is_dir():
        shutil.rmtree(path)
    elif path.is_file():
        path.unlink()
    else:
        raise RecoveryError(f"refusing to remove a special file: {path}")


def _snapshot_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"present": False, "sha256": None}
    if _is_link_or_reparse(path) or not path.is_file():
        raise TransactionError(f"preserved path must be a regular file: {path}")
    return {"present": True, "sha256": _sha_file(path)}


def _same_file_snapshot(path: Path, snapshot: dict[str, Any]) -> bool:
    current = _snapshot_file(path)
    return current == snapshot


class Journal:
    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self.data = data

    def save(self) -> None:
        self.data["updated_at"] = utc_now()
        _atomic_write_json(self.path, self.data)

    def phase(self, phase: str, skills_root: Path, codex_home: Path) -> None:
        if phase not in PHASES:
            raise TransactionError(f"unknown transaction phase: {phase}")
        self.data["phase"] = phase
        self.save()
        _fault_after(phase, skills_root, codex_home)


def _fault_configuration(skills_root: Path, codex_home: Path) -> str | None:
    point = os.environ.get("CCOS_INSTALL_TEST_FAIL_AFTER")
    hard = os.environ.get("CCOS_INSTALL_TEST_HARD_CRASH")
    if not point and not hard:
        return None
    if not point or os.environ.get("CCOS_INSTALL_TEST_MODE") != "1":
        raise TransactionError(
            "fault injection requires both CCOS_INSTALL_TEST_MODE=1 and CCOS_INSTALL_TEST_FAIL_AFTER"
        )
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    if not _path_is_within(skills_root, temp_root) or not _path_is_within(codex_home, temp_root):
        raise TransactionError("fault injection is restricted to synthetic SkillsRoot and CodexHome under the OS temp root")
    return point


def _fault_after(point: str, skills_root: Path, codex_home: Path) -> None:
    configured = _fault_configuration(skills_root, codex_home)
    if configured != point:
        return
    if os.environ.get("CCOS_INSTALL_TEST_HARD_CRASH") == "1":
        os._exit(86)
    raise InjectedFailure(f"synthetic fault after {point}")


def _canonical_roots(
    skills_root: Path | str,
    codex_home: Path | str,
    *,
    legacy_overlap_migration: bool = False,
) -> tuple[Path, Path]:
    skills = Path(skills_root).expanduser().resolve(strict=False)
    codex = Path(codex_home).expanduser().resolve(strict=False)
    overlaps = skills == codex or _path_is_within(skills, codex) or _path_is_within(codex, skills)
    expected_legacy_skills = (codex / "skills").resolve(strict=False)
    if overlaps:
        if not legacy_overlap_migration:
            raise TransactionError(
                "SkillsRoot and CodexHome must be separate, non-overlapping roots unless legacy-overlap migration is explicit"
            )
        if skills != expected_legacy_skills:
            raise TransactionError(
                "legacy-overlap migration requires SkillsRoot to be exactly CodexHome/skills"
            )
    elif legacy_overlap_migration:
        raise TransactionError(
            "legacy-overlap migration is valid only when SkillsRoot is exactly CodexHome/skills"
        )
    for root in (skills, codex):
        existing = root
        while not existing.exists() and existing.parent != existing:
            existing = existing.parent
        _assert_no_link_components(existing)
        if root.exists() and _is_link_or_reparse(root):
            raise TransactionError(f"install root cannot be a link or reparse point: {root}")
    return skills, codex


def _legacy_overlap_layout(skills_root: Path, codex_home: Path) -> bool:
    return skills_root == (codex_home / "skills").resolve(strict=False)


def _existing_anchor(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate


def _transaction_workspace_path(transaction_id: str, skills_root: Path, codex_home: Path) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
        raise TransactionError("transaction workspace requires one generated 32-character transaction identifier")
    temp_root = Path(tempfile.gettempdir()).expanduser().resolve(strict=True)
    _assert_no_link_components(temp_root)
    workspace_root_candidate = temp_root / "codex-coding-os-transactions"
    _assert_no_link_components(workspace_root_candidate, temp_root)
    workspace_root = workspace_root_candidate.resolve(strict=False)
    workspace = (workspace_root / transaction_id).resolve(strict=False)
    if workspace.parent != workspace_root or not _path_is_within(workspace, workspace_root):
        raise TransactionError("transaction workspace escaped its OS-temp containment root")
    if _path_is_within(workspace, skills_root) or _path_is_within(workspace, codex_home):
        raise TransactionError("transaction workspace must be outside SkillsRoot and CodexHome")
    devices = {_existing_anchor(path).stat().st_dev for path in (temp_root, skills_root, codex_home)}
    if len(devices) != 1:
        raise TransactionError(
            "transaction workspace must share a filesystem with SkillsRoot and CodexHome for atomic promotion"
        )
    return workspace


def _create_transaction_workspace(transaction_id: str, skills_root: Path, codex_home: Path) -> Path:
    workspace = _transaction_workspace_path(transaction_id, skills_root, codex_home)
    workspace.mkdir(parents=True, exist_ok=False)
    _assert_no_link_components(workspace)
    return workspace


def _validate_journal_workspace(journal: Journal, skills_root: Path, codex_home: Path) -> Path | None:
    raw = journal.data.get("transaction_workspace")
    if raw is None:
        return None
    expected = _transaction_workspace_path(str(journal.data.get("transaction_id", "")), skills_root, codex_home)
    workspace = Path(str(raw)).expanduser().resolve(strict=False)
    if workspace != expected:
        raise RecoveryError("transaction journal workspace is outside the expected OS-temp transaction path")
    for root_key in ("stage_roots", "rollback_roots"):
        values = journal.data.get(root_key, [])
        if not isinstance(values, list):
            raise RecoveryError(f"transaction journal {root_key} is invalid")
        for value in values:
            path = Path(str(value)).expanduser().resolve(strict=False)
            if not _path_is_within(path, workspace):
                raise RecoveryError(f"transaction journal {root_key} escaped the transaction workspace")
    return workspace


def _read_previous_current(state_root: Path) -> tuple[bytes | None, dict[str, Any] | None]:
    path = state_root / "current.json"
    if not path.exists():
        return None, None
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"current install pointer is invalid: {exc}") from exc
    if not isinstance(value, dict) or value.get("protocol") != TRANSACTION_PROTOCOL:
        raise RecoveryError("current install pointer has an unsupported protocol")
    return raw, value


def _parse_v2_text(path: Path) -> dict[str, Any]:
    values: dict[str, str] = {}
    skill_paths: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="strict").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise OwnershipError(f"legacy v2 install manifest is unreadable: {path}: {exc}") from exc
    for line in lines:
        if "=" not in line:
            raise OwnershipError("legacy v2 install manifest contains a malformed line")
        key, value = line.split("=", 1)
        if key == "SkillPath":
            skill_paths.append(value)
        elif key in values:
            raise OwnershipError(f"legacy v2 install manifest duplicates {key}")
        else:
            values[key] = value
    if values.get("ManifestVersion") != "2" or values.get("Package") not in LEGACY_V2_PACKAGES:
        raise OwnershipError("legacy text manifest is not a strict Codex Coding OS v2 manifest")
    return {
        "manifest_version": 2,
        "package": values.get("Package"),
        "skills_root": values.get("SkillsRoot"),
        "codex_home": values.get("CodexHome"),
        "support_root": values.get("SupportRoot"),
        "skills": [{"name": Path(value).name, "path": value} for value in skill_paths],
        "installed_global_agents": values.get("InstalledGlobalAgents", "False").lower() in {"1", "true"},
        "global_agents_path": values.get("GlobalAgentsPath"),
    }


def _load_previous_install(skills_root: Path, codex_home: Path) -> dict[str, Any] | None:
    state_root = codex_home / ".coding-os-install"
    _, pointer = _read_previous_current(state_root) if state_root.exists() else (None, None)
    if pointer is not None:
        if pointer.get("status") == "uninstalled":
            return None
        if pointer.get("status") != "committed":
            raise RecoveryError("current install pointer is not in a committed state")
        manifest_path = Path(str(pointer.get("install_manifest_path", ""))).expanduser().resolve(strict=False)
        support_root = (codex_home / "coding-os").resolve(strict=False)
        if manifest_path != support_root / "install-manifest.json":
            raise OwnershipError("current pointer manifest path is outside the canonical support root")
        if not manifest_path.is_file() or _sha_file(manifest_path) != pointer.get("install_manifest_sha256"):
            raise RecoveryError("current pointer does not match the installed manifest bytes")
        manifest = _load_json(manifest_path, "v3 install manifest")
        if manifest.get("manifest_version") != 3 or manifest.get("transaction_protocol") != TRANSACTION_PROTOCOL:
            raise OwnershipError("current pointer does not reference a supported v3 install manifest")
        return manifest
    support_root = codex_home / "coding-os"
    if not support_root.exists():
        legacy_root = codex_home / "coding-os-starter"
        if not legacy_root.exists():
            return None
        support_root = legacy_root
    json_path = support_root / "install-manifest.json"
    text_path = support_root / "install-manifest.txt"
    if json_path.is_file():
        manifest = _load_json(json_path, "legacy install manifest")
        if manifest.get("manifest_version") == 3:
            return manifest
        if manifest.get("package") not in LEGACY_V2_PACKAGES:
            raise OwnershipError("legacy JSON manifest is not a strict Codex Coding OS v2 manifest")
        manifest["manifest_version"] = 2
        return manifest
    if text_path.is_file():
        return _parse_v2_text(text_path)
    raise OwnershipError("an existing support root has no readable owned install manifest")


def _legacy_overlap_manifest_marker(
    previous: dict[str, Any] | None,
    skills_root: Path,
    codex_home: Path,
) -> dict[str, Any]:
    if not _legacy_overlap_layout(skills_root, codex_home):
        raise OwnershipError("legacy-overlap migration requires the exact CodexHome/skills layout")
    if previous is None:
        raise OwnershipError("legacy-overlap migration requires an existing strict v2 ownership manifest")
    if previous.get("manifest_version") == 3:
        targets = previous.get("targets")
        marker = previous.get("legacy_overlap_migration")
        if not isinstance(targets, dict) or not isinstance(marker, dict):
            raise OwnershipError("overlapping v3 install requires a recorded legacy-overlap migration marker")
        if Path(str(targets.get("skills_root", ""))).expanduser().resolve(strict=False) != skills_root:
            raise OwnershipError("overlapping v3 manifest SkillsRoot does not match the requested root")
        if Path(str(targets.get("support_root", ""))).expanduser().resolve(strict=False) != (
            codex_home / "coding-os"
        ).resolve(strict=False):
            raise OwnershipError("overlapping v3 manifest support root is outside the requested CodexHome")
        if (
            marker.get("layout") != LEGACY_OVERLAP_LAYOUT
            or marker.get("source_manifest_version") != 2
            or marker.get("source_package") not in LEGACY_V2_PACKAGES
            or Path(str(marker.get("skills_root", ""))).expanduser().resolve(strict=False) != skills_root
            or Path(str(marker.get("source_support_root", ""))).expanduser().resolve(strict=False)
            not in {
                (codex_home / "coding-os").resolve(strict=False),
                (codex_home / "coding-os-starter").resolve(strict=False),
            }
        ):
            raise OwnershipError("overlapping v3 manifest has an invalid legacy-overlap migration marker")
        _previous_skill_records(previous, skills_root)
        return dict(marker)
    if previous.get("manifest_version") != 2:
        raise OwnershipError("legacy-overlap migration requires a strict v2 ownership manifest")
    package = previous.get("package")
    recorded_skills = Path(str(previous.get("skills_root", ""))).expanduser().resolve(strict=False)
    recorded_codex = Path(str(previous.get("codex_home", ""))).expanduser().resolve(strict=False)
    support_root = Path(str(previous.get("support_root", ""))).expanduser().resolve(strict=False)
    allowed_support_roots = {
        (codex_home / "coding-os").resolve(strict=False),
        (codex_home / "coding-os-starter").resolve(strict=False),
    }
    if (
        package not in LEGACY_V2_PACKAGES
        or recorded_skills != skills_root
        or recorded_codex != codex_home
        or support_root not in allowed_support_roots
    ):
        raise OwnershipError("legacy-overlap migration requires exact v2 package, roots, and support ownership")
    records = _previous_skill_records(previous, skills_root)
    if not records:
        raise OwnershipError("legacy-overlap migration requires a nonempty v2 managed skill inventory")
    normal_support = (codex_home / "coding-os").resolve(strict=False)
    if support_root != normal_support and normal_support.exists():
        raise OwnershipError("legacy-overlap migration refuses to replace an unowned current support root")
    return {
        "layout": LEGACY_OVERLAP_LAYOUT,
        "source_manifest_version": 2,
        "source_package": package,
        "source_support_root": str(support_root),
        "skills_root": str(skills_root),
    }


def _previous_skill_records(previous: dict[str, Any] | None, skills_root: Path) -> list[dict[str, Any]]:
    if previous is None:
        return []
    if previous.get("manifest_version") == 3:
        records = previous.get("targets", {}).get("managed_skills", [])
    else:
        recorded_root = Path(str(previous.get("skills_root", ""))).expanduser().resolve(strict=False)
        if recorded_root != skills_root:
            raise OwnershipError("legacy manifest SkillsRoot does not match the requested SkillsRoot")
        records = previous.get("skills", [])
    if not isinstance(records, list):
        raise OwnershipError("previous install managed skill inventory is invalid")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise OwnershipError("previous install skill record must be an object")
        name = str(record.get("name", ""))
        path = Path(str(record.get("path", ""))).expanduser().resolve(strict=False)
        expected = (skills_root / name).resolve(strict=False)
        if not name or "/" in name or "\\" in name or path != expected or not _path_is_within(path, skills_root):
            raise OwnershipError(f"previous install skill path is outside the requested SkillsRoot: {path}")
        key = name.casefold()
        if key in seen:
            raise OwnershipError(f"previous install duplicates managed skill name: {name}")
        seen.add(key)
        normalized.append({**record, "name": name, "path": str(path)})
    return normalized


def _validate_unowned_collisions(
    skills_root: Path,
    support_root: Path,
    new_skill_names: Sequence[str],
    previous: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    previous_records = _previous_skill_records(previous, skills_root)
    owned = {str(record["name"]).casefold(): Path(str(record["path"])).resolve(strict=False) for record in previous_records}
    if skills_root.exists():
        for child in skills_root.iterdir():
            if child.name in {".coding-os-stage", ".coding-os-rollback"}:
                continue
            if _is_link_or_reparse(child):
                raise OwnershipError(f"SkillsRoot contains a link or reparse point: {child}")
            key = child.name.casefold()
            if key in {name.casefold() for name in new_skill_names} or key in owned:
                if key not in owned or child.resolve(strict=False) != owned[key]:
                    raise OwnershipError(f"refusing unowned case-insensitive skill collision: {child}")
    if support_root.exists() and previous is None:
        raise OwnershipError("refusing to replace an unowned existing support root")
    return previous_records


def _git_output(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise SourceVerificationError(f"Git source verification failed: {detail.strip()}") from exc
    return completed.stdout.strip()


def _verify_source(options: InstallOptions, source_root: Path, bundle: BundleInfo) -> dict[str, Any]:
    expected = str(options.expected_bundle_sha256).lower()
    if not HASH_RE.fullmatch(expected) or expected != bundle.aggregate_sha256:
        raise BundleError("ExpectedBundleSha256 must exactly match the verified bundle aggregate")
    if options.archive_mode:
        if options.install_universal_policy:
            raise AuthorityError("archive mode cannot synchronize universal policy")
        return {
            "kind": "archive",
            "repo_root": str(source_root),
            "git_commit": None,
            "git_tree": None,
            "working_tree_clean": None,
            "bundle_manifest_sha256": bundle.manifest_sha256,
        }
    expected_commit = str(options.expected_source_commit or "").lower()
    if not COMMIT_RE.fullmatch(expected_commit):
        raise SourceVerificationError("ExpectedSourceCommit must be one full 40-character lowercase Git commit")
    head = _git_output(source_root, "rev-parse", "HEAD").lower()
    if head != expected_commit:
        raise SourceVerificationError(f"source HEAD {head} does not match expected commit {expected_commit}")
    status = _git_output(source_root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise SourceVerificationError("source worktree must be completely clean, including untracked files")
    tree = _git_output(source_root, "rev-parse", "HEAD^{tree}").lower()
    remote = _git_output(source_root, "remote", "get-url", "origin")
    repository = _normalize_repository(remote)
    return {
        "kind": "git",
        "repo_root": str(source_root),
        "repository": repository,
        "git_commit": head,
        "git_tree": tree,
        "working_tree_clean": True,
        "bundle_manifest_sha256": bundle.manifest_sha256,
    }


def _normalize_repository(value: str) -> str:
    raw = str(value).strip()
    ssh_match = re.fullmatch(r"git@([^:]+):(.+)", raw)
    if ssh_match:
        raw = f"https://{ssh_match.group(1)}/{ssh_match.group(2)}"
    match = re.fullmatch(r"https?://([^/]+)/(.+)", raw, flags=re.IGNORECASE)
    if not match:
        raise SourceVerificationError("origin remote must be one normalized HTTPS or git@ repository URL")
    host = match.group(1).lower()
    path = match.group(2).rstrip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    if not path or ".." in PurePosixPath(path).parts:
        raise SourceVerificationError("origin remote repository path is invalid")
    return f"https://{host}/{path}"


def _validated_universal_bundle_id(value: str | None) -> str:
    bundle_id = str(value or "")
    if not UNIVERSAL_BUNDLE_ID_RE.fullmatch(bundle_id):
        raise AuthorityError(
            "UniversalBundleId must be a lowercase hyphenated identifier of 1 to 128 characters"
        )
    return bundle_id


def _check_universal_authority(options: InstallOptions, source: dict[str, Any]) -> dict[str, Any]:
    bundle_id = _validated_universal_bundle_id(options.universal_bundle_id)
    if not options.install_universal_policy:
        return {
            "case_id": None,
            "action": None,
            "action_protocol": None,
            "approved_head": None,
            "source": None,
            "reference": None,
            "boundary_reason": None,
        }
    case_id = str(options.authority_case_id or "").lower()
    if not CASE_ID_RE.fullmatch(case_id):
        raise AuthorityError("AuthorityCaseId must be one canonical UUID")
    authority_source = str(options.authority_source or "")
    if authority_source not in {"preauthorized-run-envelope", "explicit-user-approval"}:
        raise AuthorityError(
            "AuthoritySource must be preauthorized-run-envelope or explicit-user-approval for universal policy sync"
        )
    authority_reference = str(options.authority_reference or "").strip()
    if not authority_reference or len(authority_reference) > 512:
        raise AuthorityError("AuthorityReference must be a nonempty bounded reference to the external authority")
    engine = Path(options.case_state_engine or "").expanduser().resolve(strict=False)
    state_root = Path(options.case_state_root or "").expanduser().resolve(strict=False)
    if not engine.is_file() or not state_root.is_dir():
        raise AuthorityError("case-state engine and state root are required for universal policy synchronization")
    base = [sys.executable, "-B", str(engine), "--state-root", str(state_root), "--json"]
    shown = subprocess.run(
        [*base, "show", "--case-id", case_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if shown.returncode != 0:
        raise AuthorityError(f"case-state show failed: {(shown.stderr or shown.stdout).strip()}")
    try:
        shown_case = json.loads(shown.stdout)
    except json.JSONDecodeError as exc:
        raise AuthorityError("case-state show did not return JSON") from exc
    if not isinstance(shown_case, dict) or shown_case.get("case_id") != case_id or shown_case.get("state") != "CLOSED_SUCCESS":
        raise AuthorityError("universal policy sync requires the exact case to be CLOSED_SUCCESS")
    repository = str(source.get("repository") or "")
    head = str(source.get("git_commit") or "")
    command = [
        *base,
        "action-check",
        "--case-id",
        case_id,
        "--action",
        "universal_sync",
        "--actor-role",
        "publication_child",
        "--repository",
        repository,
        "--universal-bundle",
        bundle_id,
        "--head",
        head,
    ]
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if completed.returncode != 0:
        raise AuthorityError(f"case-state action-check failed: {(completed.stderr or completed.stdout).strip()}")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AuthorityError("case-state action-check did not return JSON") from exc
    if not isinstance(response, dict):
        raise AuthorityError("case-state action-check response must be an object")
    context = response.get("context") if isinstance(response.get("context"), dict) else {}
    required_context_keys = {
        "actor_role",
        "repository",
        "branch",
        "worktree",
        "pr",
        "thread",
        "universal_bundle",
        "head",
    }
    if (
        response.get("protocol_version") != "ccos-case-action-v1"
        or response.get("case_id") != case_id
        or response.get("state") != "CLOSED_SUCCESS"
        or response.get("action") != "universal_sync"
        or response.get("actor_role") != "publication_child"
        or response.get("repository") != repository
        or response.get("head") != head
        or set(context) != required_context_keys
        or context.get("actor_role") != "publication_child"
        or context.get("repository") != repository
        or context.get("universal_bundle") != bundle_id
        or context.get("head") != head
        or response.get("allowed") is not False
        or response.get("reason_codes") != ["SEPARATE_AUTHORITY_REQUIRED"]
        or response.get("separate_authority_required") is not True
        or response.get("blocked_case_id") is not None
    ):
        raise AuthorityError(
            "case-state engine did not validate the exact closed case, publication role, repository, head, and bundle boundary"
        )
    return {
        "case_id": case_id,
        "action": "universal_sync",
        "action_protocol": "ccos-case-action-v1",
        "approved_head": head,
        "repository": repository,
        "universal_bundle": bundle_id,
        "source": authority_source,
        "reference": authority_reference,
        "boundary_reason": "SEPARATE_AUTHORITY_REQUIRED",
    }


def _support_payload_hash(root: Path, generated_files: Sequence[dict[str, Any]] | Sequence[str]) -> str | None:
    excluded: set[str] = set()
    for item in generated_files:
        if isinstance(item, dict):
            path = item.get("path")
        else:
            path = item
        if isinstance(path, str):
            excluded.add(_normalize_relative(path))
    return _tree_hash(root, excluded)


def _stage_bundle(
    options: InstallOptions,
    source_root: Path,
    skills_root: Path,
    codex_home: Path,
    transaction_workspace: Path,
    bundle: BundleInfo,
    transaction_id: str,
    previous: dict[str, Any] | None,
    legacy_overlap_marker: dict[str, Any] | None,
    source: dict[str, Any],
    authority: dict[str, Any],
    config_snapshot: dict[str, Any],
) -> dict[str, Any]:
    installation = bundle.pack["installation"]
    managed_skill_source = _safe_repo_path(source_root, str(installation["managed_skill_root"]))
    skill_stage_root = transaction_workspace / "stage" / "skills"
    codex_stage_home = transaction_workspace / "stage" / "codex-home"
    support_stage = codex_stage_home / "coding-os"
    policy_stage = codex_stage_home / "policy"
    skill_stage_root.mkdir(parents=True, exist_ok=False)
    support_stage.mkdir(parents=True, exist_ok=False)
    managed_skills: list[dict[str, Any]] = []
    skill_prefix = _normalize_relative(str(installation["managed_skill_root"])) + "/"
    for skill in bundle.pack["bundled_skills"]:
        name = str(skill["name"])
        skill_source = managed_skill_source / name
        staged = skill_stage_root / name
        _copy_path(skill_source, staged)
        source_hash = _tree_hash(skill_source)
        staged_hash = _tree_hash(staged)
        if source_hash != staged_hash:
            raise TransactionError(f"staged skill hash mismatch: {name}")
        managed_skills.append(
            {
                "name": name,
                "path": str((skills_root / name).resolve(strict=False)),
                "sha256": staged_hash,
            }
        )
    for entry in bundle.entries:
        relative = str(entry["path"])
        if relative.startswith(skill_prefix):
            continue
        source_file = _safe_repo_path(source_root, relative)
        staged_file = support_stage.joinpath(*PurePosixPath(relative).parts)
        staged_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, staged_file)
        if staged_file.stat().st_size != entry["size"] or _sha_file(staged_file) != entry["sha256"]:
            raise TransactionError(f"staged support file hash mismatch: {relative}")
    bundle_manifest_source = source_root / "install-bundle.manifest.json"
    bundle_manifest_stage = support_stage / "install-bundle.manifest.json"
    shutil.copy2(bundle_manifest_source, bundle_manifest_stage)
    if _sha_file(bundle_manifest_stage) != bundle.manifest_sha256:
        raise TransactionError("staged install bundle manifest hash mismatch")

    generated_records: list[dict[str, Any]] = []
    before_refresh = {entry["path"]: entry for entry in _tree_entries(support_stage)}
    if options.refresh_capability_index:
        relative_cli = _normalize_relative(str(installation.get("capability_refresh_cli", "")))
        cli = support_stage.joinpath(*PurePosixPath(relative_cli).parts)
        if not cli.is_file():
            raise TransactionError(f"capability refresh CLI was not staged: {relative_cli}")
        environment = os.environ.copy()
        environment.update(
            {
                "CODEX_HOME": str(codex_stage_home),
                "AGENTS_HOME": str(skills_root / ".coding-os-stage" / transaction_id),
                "CODEX_CODING_OS_ROOT": str(support_stage),
            }
        )
        completed = subprocess.run(
            [sys.executable, "-B", str(cli), "--refresh"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            check=False,
        )
        if completed.returncode != 0:
            raise TransactionError(f"capability refresh failed in the staged bundle: {(completed.stderr or completed.stdout).strip()}")
        after_refresh = {entry["path"]: entry for entry in _tree_entries(support_stage)}
        for path, prior in before_refresh.items():
            if after_refresh.get(path) != prior:
                raise TransactionError(f"capability refresh modified immutable staged payload: {path}")
        for path, record in after_refresh.items():
            if path not in before_refresh:
                generated_records.append(record)

    global_agents_path = codex_home / "AGENTS.md"
    default_rules_path = codex_home / "rules" / "default.rules"
    global_target: dict[str, Any] = {"path": str(global_agents_path), "managed": False}
    rules_target: dict[str, Any] = {"path": str(default_rules_path), "managed": False}
    staged_agents: Path | None = None
    staged_rules: Path | None = None
    previous_agents_managed, previous_rules_managed = _previous_managed_policy_targets(previous, codex_home)
    if options.install_universal_policy:
        if not global_agents_path.is_file() or not default_rules_path.is_file():
            raise PolicyMigrationError("universal policy migration requires existing AGENTS.md and default.rules")
        _assert_no_link_components(global_agents_path, codex_home)
        _assert_no_link_components(default_rules_path, codex_home)
        policy_sources = installation["universal_policy_sources"]
        agents_source = _safe_repo_path(source_root, str(policy_sources["global_agents"])).read_bytes()
        rules_source = _safe_repo_path(source_root, str(policy_sources["default_rules"])).read_bytes()
        agents_before = global_agents_path.read_bytes()
        rules_before = default_rules_path.read_bytes()
        agents_after = migrate_agents_bytes(agents_before, agents_source)
        rules_after = migrate_rules_bytes(rules_before, rules_source)
        staged_agents = policy_stage / "AGENTS.md"
        staged_rules = policy_stage / "rules" / "default.rules"
        _atomic_write_bytes(staged_agents, agents_after)
        _atomic_write_bytes(staged_rules, rules_after)
        global_target = {
            "path": str(global_agents_path),
            "managed": True,
            "prior_sha256": _sha_bytes(agents_before),
            "installed_sha256": _sha_bytes(agents_after),
            "marker_start": AGENTS_START,
            "marker_end": AGENTS_END,
        }
        rules_target = {
            "path": str(default_rules_path),
            "managed": True,
            "prior_sha256": _sha_bytes(rules_before),
            "installed_sha256": _sha_bytes(rules_after),
            "marker_start": RULES_START,
            "marker_end": RULES_END,
        }
    else:
        if previous_agents_managed:
            if not global_agents_path.is_file() or _is_link_or_reparse(global_agents_path):
                raise OwnershipError("previously managed AGENTS.md is unavailable for opt-out removal")
            staged_agents = policy_stage / "AGENTS.md"
            _atomic_write_bytes(staged_agents, remove_agents_policy_bytes(global_agents_path.read_bytes()))
        if previous_rules_managed:
            if not default_rules_path.is_file() or _is_link_or_reparse(default_rules_path):
                raise OwnershipError("previously managed default.rules is unavailable for opt-out removal")
            staged_rules = policy_stage / "rules" / "default.rules"
            _atomic_write_bytes(staged_rules, remove_rules_policy_bytes(default_rules_path.read_bytes()))

    generated_paths = {str(record["path"]) for record in generated_records}
    generated_paths.update({"install-manifest.json", "install-manifest.txt"})
    support_payload_sha = _support_payload_hash(
        support_stage,
        [{"path": path} for path in sorted(generated_paths)],
    )
    previous_summary: dict[str, Any] | None = None
    if previous is not None:
        previous_bytes = json.dumps(previous, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        previous_summary = {
            "manifest_version": previous.get("manifest_version", 2),
            "manifest_sha256": _sha_bytes(previous_bytes),
            "bundle_sha256": (
                previous.get("package", {}).get("bundle_sha256")
                if isinstance(previous.get("package"), dict)
                else None
            ),
        }
    install_manifest = {
        "manifest_version": MANIFEST_VERSION,
        "transaction_protocol": TRANSACTION_PROTOCOL,
        "package": {
            "name": bundle.pack["package_name"],
            "version": bundle.pack["version"],
            "bundle_sha256": bundle.aggregate_sha256,
        },
        "source": source,
        "authority": authority,
        "transaction": {"id": transaction_id, "committed_at": utc_now()},
        "targets": {
            "skills_root": str(skills_root),
            "support_root": str((codex_home / "coding-os").resolve(strict=False)),
            "managed_skills": managed_skills,
            "support_payload_sha256": support_payload_sha,
            "global_agents": global_target,
            "default_rules": rules_target,
        },
        "preserved_paths": {
            "config_toml": {
                "path": str(codex_home / "config.toml"),
                "managed": False,
                "present": config_snapshot["present"],
                "sha256": config_snapshot["sha256"],
            },
            "case_state": {"path": str(codex_home / "case-state"), "managed": False},
            "plugins": {"path": str(codex_home / "plugins"), "managed": False},
            "non_managed_skills": {"root": str(skills_root), "managed": False},
        },
        "generated_files": [],
        "previous_install": previous_summary,
    }
    if legacy_overlap_marker is not None:
        install_manifest["legacy_overlap_migration"] = legacy_overlap_marker
    manifest_path = support_stage / "install-manifest.json"
    _atomic_write_json(manifest_path, install_manifest)
    text_lines = [
        "ManifestVersion=3",
        "Package=codex-coding-os",
        f"TransactionProtocol={TRANSACTION_PROTOCOL}",
        f"BundleSha256={bundle.aggregate_sha256}",
        f"SkillsRoot={skills_root}",
        f"CodexHome={codex_home}",
        f"SupportRoot={codex_home / 'coding-os'}",
    ]
    if legacy_overlap_marker is not None:
        text_lines.extend(
            [
                "LegacyOverlapMigration=True",
                f"LegacyOverlapLayout={legacy_overlap_marker['layout']}",
            ]
        )
    text_lines.extend(f"SkillPath={record['path']}" for record in managed_skills)
    text_path = support_stage / "install-manifest.txt"
    _atomic_write_bytes(text_path, ("\n".join(text_lines) + "\n").encode("utf-8"))
    generated_records.extend(
        [
            {"path": "install-manifest.json", "size": manifest_path.stat().st_size, "sha256": _sha_file(manifest_path)},
            {"path": "install-manifest.txt", "size": text_path.stat().st_size, "sha256": _sha_file(text_path)},
        ]
    )
    generated_records.sort(key=lambda record: str(record["path"]).encode("utf-8"))
    install_manifest["generated_files"] = generated_records
    _atomic_write_json(manifest_path, install_manifest)
    for record in generated_records:
        if record["path"] == "install-manifest.json":
            record["size"] = manifest_path.stat().st_size
            record["sha256"] = _sha_file(manifest_path)
    # The self-referential manifest is identified by the external pointer hash.
    # Its generated-file entry is intentionally omitted from hash verification.
    install_manifest["generated_files"] = [
        record for record in generated_records if record["path"] != "install-manifest.json"
    ] + [{"path": "install-manifest.json", "hash_authority": "current-pointer"}]
    _atomic_write_json(manifest_path, install_manifest)
    support_tree_sha = _tree_hash(support_stage)
    return {
        "skill_stage_root": skill_stage_root,
        "codex_stage_home": codex_stage_home,
        "support_stage": support_stage,
        "staged_agents": staged_agents,
        "staged_rules": staged_rules,
        "managed_skills": managed_skills,
        "support_payload_sha256": support_payload_sha,
        "support_tree_sha256": support_tree_sha,
        "install_manifest": install_manifest,
        "install_manifest_sha256": _sha_file(manifest_path),
        "generated_files": install_manifest["generated_files"],
        "legacy_overlap_migration": legacy_overlap_marker,
    }


def _is_idempotent(
    options: InstallOptions,
    previous: dict[str, Any] | None,
    staged: dict[str, Any],
    source: dict[str, Any],
    authority: dict[str, Any],
    skills_root: Path,
    codex_home: Path,
) -> bool:
    if previous is None or previous.get("manifest_version") != 3 or options.refresh_capability_index:
        return False
    if previous.get("legacy_overlap_migration") != staged.get("legacy_overlap_migration"):
        return False
    package = previous.get("package")
    prior_source = previous.get("source")
    prior_authority = previous.get("authority")
    if not isinstance(package, dict) or package.get("bundle_sha256") != options.expected_bundle_sha256.lower():
        return False
    if not isinstance(prior_source, dict) or prior_source.get("kind") != source.get("kind") or prior_source.get("git_commit") != source.get("git_commit"):
        return False
    if options.install_universal_policy and prior_authority != authority:
        return False
    if not options.install_universal_policy and any(_previous_managed_policy_targets(previous, codex_home)):
        return False
    targets = previous.get("targets")
    if not isinstance(targets, dict):
        return False
    prior_skills = targets.get("managed_skills")
    if not isinstance(prior_skills, list):
        return False
    prior_by_name = {str(item.get("name")): item for item in prior_skills if isinstance(item, dict)}
    staged_by_name = {str(item["name"]): item for item in staged["managed_skills"]}
    if set(prior_by_name) != set(staged_by_name):
        return False
    for name, record in staged_by_name.items():
        live = skills_root / name
        if not live.exists() or _tree_hash(live) != record["sha256"]:
            return False
    generated = previous.get("generated_files") if isinstance(previous.get("generated_files"), list) else []
    if _support_payload_hash(codex_home / "coding-os", generated) != staged["support_payload_sha256"]:
        return False
    if options.install_universal_policy:
        if staged["staged_agents"] is None or staged["staged_rules"] is None:
            return False
        if _sha_file(codex_home / "AGENTS.md") != _sha_file(staged["staged_agents"]):
            return False
        if _sha_file(codex_home / "rules/default.rules") != _sha_file(staged["staged_rules"]):
            return False
    return True


def _make_target(
    target_id: str,
    live: Path,
    staged: Path | None,
    rollback: Path,
) -> dict[str, Any]:
    if live.exists() and _is_link_or_reparse(live):
        raise OwnershipError(f"managed live target cannot be a link or reparse point: {live}")
    if rollback.exists():
        raise RecoveryError(f"rollback slot already exists before promotion: {rollback}")
    prior_hash = _tree_hash(live)
    new_hash = _tree_hash(staged) if staged is not None else None
    return {
        "target_id": target_id,
        "live_path": str(live),
        "staged_path": str(staged) if staged is not None else None,
        "rollback_path": str(rollback),
        "prior_state": "present" if live.exists() else "absent",
        "step": "not_started",
        "prior_sha256": prior_hash,
        "new_sha256": new_hash,
    }


def _prepare_install_targets(
    skills_root: Path,
    codex_home: Path,
    transaction_workspace: Path,
    previous_records: Sequence[dict[str, Any]],
    staged: dict[str, Any],
) -> list[dict[str, Any]]:
    skill_rollback = transaction_workspace / "rollback" / "skills"
    codex_rollback = transaction_workspace / "rollback" / "codex-home"
    new_names = {record["name"] for record in staged["managed_skills"]}
    old_names = {str(record["name"]) for record in previous_records}
    targets: list[dict[str, Any]] = []
    for name in sorted(new_names | old_names, key=lambda value: value.casefold()):
        staged_path = staged["skill_stage_root"] / name if name in new_names else None
        targets.append(_make_target(f"skill:{name}", skills_root / name, staged_path, skill_rollback / name))
    targets.append(
        _make_target(
            "support",
            codex_home / "coding-os",
            staged["support_stage"],
            codex_rollback / "coding-os",
        )
    )
    if staged["staged_agents"] is not None:
        targets.append(
            _make_target(
                "global_agents",
                codex_home / "AGENTS.md",
                staged["staged_agents"],
                codex_rollback / "AGENTS.md",
            )
        )
    if staged["staged_rules"] is not None:
        targets.append(
            _make_target(
                "default_rules",
                codex_home / "rules/default.rules",
                staged["staged_rules"],
                codex_rollback / "rules/default.rules",
            )
        )
    for target in targets:
        if Path(target["live_path"]).resolve(strict=False) == codex_home:
            raise TransactionError("transaction targets must never rename, move, or replace CodexHome itself")
    return targets


def _verify_target_state(target: dict[str, Any], use_new: bool) -> bool:
    live = Path(target["live_path"])
    expected_state = "present" if (target["new_sha256"] is not None if use_new else target["prior_state"] == "present") else "absent"
    expected_hash = target["new_sha256"] if use_new else target["prior_sha256"]
    if expected_state == "absent":
        return not live.exists()
    return live.exists() and not _is_link_or_reparse(live) and _tree_hash(live) == expected_hash


def _promote_targets(journal: Journal, skills_root: Path, codex_home: Path) -> None:
    targets = journal.data["targets"]
    count = len(targets)
    for index, target in enumerate(targets):
        live = Path(target["live_path"])
        staged = Path(target["staged_path"]) if target.get("staged_path") else None
        rollback = Path(target["rollback_path"])
        live.parent.mkdir(parents=True, exist_ok=True)
        rollback.parent.mkdir(parents=True, exist_ok=True)
        if target["prior_state"] == "present":
            if not live.exists() or _tree_hash(live) != target["prior_sha256"]:
                raise RecoveryError(f"live target drifted before promotion: {target['target_id']}")
            os.replace(live, rollback)
        elif live.exists():
            raise OwnershipError(f"an unowned target appeared during promotion: {live}")
        target["step"] = "old_moved"
        journal.save()
        if staged is not None:
            if not staged.exists() or _tree_hash(staged) != target["new_sha256"]:
                raise RecoveryError(f"staged target drifted before promotion: {target['target_id']}")
            os.replace(staged, live)
        target["step"] = "new_moved"
        journal.save()
        if not _verify_target_state(target, True):
            raise RecoveryError(f"promoted target failed exact hash verification: {target['target_id']}")
        target["step"] = "verified"
        journal.save()
        labels: list[str] = []
        if index == 0:
            labels.append("first")
        if index == count // 2:
            labels.append("middle")
        if index == count - 1:
            labels.append("last")
        for label in labels:
            _fault_after(f"PROMOTION:{label}", skills_root, codex_home)


def _pointer_matches_new(journal: Journal, state_root: Path) -> bool:
    current = state_root / "current.json"
    if not current.is_file() or not journal.data.get("new_pointer_sha256"):
        return False
    return _sha_file(current) == journal.data["new_pointer_sha256"]


def _restore_previous_pointer(journal: Journal, state_root: Path) -> None:
    current = state_root / "current.json"
    previous_encoded = journal.data.get("previous_current_base64")
    previous_hash = journal.data.get("previous_current_sha256")
    new_hash = journal.data.get("new_pointer_sha256")
    if current.exists():
        live_hash = _sha_file(current)
        if live_hash not in {value for value in (previous_hash, new_hash) if value}:
            raise RecoveryError("current pointer changed outside the transaction; refusing to overwrite it")
    if previous_encoded is None:
        if current.exists():
            current.unlink()
        return
    previous = base64.b64decode(previous_encoded.encode("ascii"), validate=True)
    if _sha_bytes(previous) != previous_hash:
        raise RecoveryError("journal previous pointer backup hash mismatch")
    _atomic_write_bytes(current, previous)


def _rollback_targets(journal: Journal, state_root: Path) -> None:
    try:
        for target in reversed(journal.data.get("targets", [])):
            live = Path(target["live_path"])
            rollback = Path(target["rollback_path"])
            prior_present = target["prior_state"] == "present"
            prior_hash = target["prior_sha256"]
            new_hash = target["new_sha256"]
            live_hash = _tree_hash(live)
            rollback_hash = _tree_hash(rollback)
            if prior_present:
                if rollback.exists():
                    if rollback_hash != prior_hash:
                        raise RecoveryError(f"rollback hash mismatch for {target['target_id']}")
                    if live.exists():
                        if live_hash not in {new_hash, prior_hash}:
                            raise RecoveryError(f"unexpected live bytes during rollback: {target['target_id']}")
                        _remove_owned_path(live)
                    live.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(rollback, live)
                elif not live.exists() or live_hash != prior_hash:
                    raise RecoveryError(f"prior target is unavailable for rollback: {target['target_id']}")
            else:
                if rollback.exists():
                    raise RecoveryError(f"rollback slot exists for originally absent target: {target['target_id']}")
                if live.exists():
                    if live_hash != new_hash:
                        raise RecoveryError(f"unexpected unowned live bytes during rollback: {target['target_id']}")
                    _remove_owned_path(live)
            if not _verify_target_state(target, False):
                raise RecoveryError(f"restored target hash mismatch: {target['target_id']}")
        _restore_previous_pointer(journal, state_root)
        journal.data["outcome"] = "rolled_back"
        journal.data["status"] = "ROLLED_BACK"
        journal.save()
    except Exception as exc:
        journal.data["status"] = "RECOVERY_FAILED"
        journal.data["recovery_error"] = str(exc)
        journal.save()
        if isinstance(exc, RecoveryError):
            raise
        raise RecoveryError(str(exc)) from exc


def _retain_and_cleanup(
    journal: Journal,
    state_root: Path,
    committed: bool,
    skills_root: Path,
    codex_home: Path,
) -> None:
    workspace = _validate_journal_workspace(journal, skills_root, codex_home)
    transaction_id = str(journal.data["transaction_id"])
    if committed:
        retained_root = state_root / "retained-backups" / transaction_id
        for target in journal.data.get("targets", []):
            rollback = Path(target["rollback_path"])
            if not rollback.exists():
                continue
            retained = retained_root / hashlib.sha256(str(target["target_id"]).encode("utf-8")).hexdigest()[:16]
            if retained.exists():
                if _tree_hash(retained) != target["prior_sha256"]:
                    raise RecoveryError(f"retained backup mismatch: {target['target_id']}")
            else:
                _copy_path(rollback, retained)
                if _tree_hash(retained) != target["prior_sha256"]:
                    raise RecoveryError(f"retained backup verification failed: {target['target_id']}")
            target["retained_backup_path"] = str(retained)
            journal.save()
    for raw in journal.data.get("stage_roots", []):
        path = Path(raw)
        if path.exists():
            _remove_owned_path(path)
    for raw in journal.data.get("rollback_roots", []):
        path = Path(raw)
        if path.exists():
            _remove_owned_path(path)
    if workspace is not None and workspace.exists():
        _remove_owned_path(workspace)
    journal.data["status"] = "COMMITTED" if committed else journal.data.get("status", "ROLLED_BACK")
    journal.data["phase"] = "CLEANUP_COMPLETE"
    journal.save()


def _recover_pending(state_root: Path, skills_root: Path, codex_home: Path) -> None:
    transactions = state_root / "transactions"
    if not transactions.exists():
        return
    pending: list[Journal] = []
    failed: list[Path] = []
    for path in sorted(transactions.glob("*/journal.json")):
        data = _load_json(path, "transaction journal")
        status = data.get("status")
        if status == "RECOVERY_FAILED":
            failed.append(path)
        elif status == "IN_PROGRESS":
            pending.append(Journal(path, data))
    if failed:
        raise RecoveryError(f"a prior transaction is in RECOVERY_FAILED and requires human inspection: {failed[0]}")
    if len(pending) > 1:
        raise RecoveryError("multiple incomplete transactions exist; refusing ambiguous recovery")
    if not pending:
        return
    journal = pending[0]
    if journal.data.get("recovery_attempted"):
        journal.data["status"] = "RECOVERY_FAILED"
        journal.data["recovery_error"] = "one recovery attempt was already consumed"
        journal.save()
        raise RecoveryError("incomplete transaction already consumed its single recovery attempt")
    journal.data["recovery_attempted"] = True
    journal.save()
    _validate_journal_workspace(journal, skills_root, codex_home)
    if _pointer_matches_new(journal, state_root):
        for target in journal.data.get("targets", []):
            if not _verify_target_state(target, True):
                journal.data["status"] = "RECOVERY_FAILED"
                journal.data["recovery_error"] = f"committed target mismatch: {target['target_id']}"
                journal.save()
                raise RecoveryError(f"committed target mismatch during recovery: {target['target_id']}")
        journal.data["outcome"] = "committed_recovered"
        _retain_and_cleanup(journal, state_root, committed=True, skills_root=skills_root, codex_home=codex_home)
    else:
        _rollback_targets(journal, state_root)
        _retain_and_cleanup(journal, state_root, committed=False, skills_root=skills_root, codex_home=codex_home)


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _preflight_policy_sources(options: InstallOptions, source_root: Path, pack: dict[str, Any], codex_home: Path) -> None:
    if not options.install_universal_policy:
        return
    policies = pack["installation"]["universal_policy_sources"]
    agents_source = _safe_repo_path(source_root, str(policies["global_agents"])).read_bytes()
    rules_source = _safe_repo_path(source_root, str(policies["default_rules"])).read_bytes()
    agents = codex_home / "AGENTS.md"
    rules = codex_home / "rules" / "default.rules"
    if not agents.is_file() or not rules.is_file():
        raise PolicyMigrationError("universal policy migration requires existing AGENTS.md and default.rules")
    migrate_agents_bytes(agents.read_bytes(), agents_source)
    migrate_rules_bytes(rules.read_bytes(), rules_source)


def _previous_managed_policy_targets(previous: dict[str, Any] | None, codex_home: Path) -> tuple[bool, bool]:
    if previous is None or previous.get("manifest_version") != 3:
        return False, False
    targets = previous.get("targets")
    if not isinstance(targets, dict):
        raise OwnershipError("previous v3 install policy inventory is invalid")

    def is_managed(name: str, path: Path, start: str, end: str) -> bool:
        record = targets.get(name)
        if record is None:
            return False
        if not isinstance(record, dict):
            raise OwnershipError(f"previous v3 install policy record is invalid: {name}")
        if record.get("managed") is False:
            return False
        if record.get("managed") is not True:
            raise OwnershipError(f"previous v3 install policy ownership is invalid: {name}")
        recorded_path = Path(str(record.get("path", ""))).expanduser().resolve(strict=False)
        if recorded_path != path.resolve(strict=False):
            raise OwnershipError(f"previous v3 install policy path is outside the requested CodexHome: {name}")
        if record.get("marker_start") != start or record.get("marker_end") != end:
            raise OwnershipError(f"previous v3 install policy markers are invalid: {name}")
        return True

    return (
        is_managed("global_agents", codex_home / "AGENTS.md", AGENTS_START, AGENTS_END),
        is_managed("default_rules", codex_home / "rules" / "default.rules", RULES_START, RULES_END),
    )


def _install_preflight(
    options: InstallOptions,
    source_root: Path,
    skills_root: Path,
    codex_home: Path,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
    pack = _load_pack(source_root)
    names = [str(record["name"]) for record in pack["bundled_skills"]]
    previous = _load_previous_install(skills_root, codex_home)
    legacy_overlap_marker = (
        _legacy_overlap_manifest_marker(previous, skills_root, codex_home)
        if options.legacy_overlap_migration
        else None
    )
    previous_records = _validate_unowned_collisions(
        skills_root,
        codex_home / "coding-os",
        names,
        previous,
    )
    _preflight_policy_sources(options, source_root, pack, codex_home)
    config = codex_home / "config.toml"
    if config.exists():
        _assert_no_link_components(config, codex_home)
    return previous, previous_records, pack, legacy_overlap_marker


def _dry_run_install(options: InstallOptions, source_root: Path, skills_root: Path, codex_home: Path) -> dict[str, Any]:
    state_root = codex_home / ".coding-os-install"
    transactions = state_root / "transactions"
    if transactions.exists():
        pending = []
        for path in transactions.glob("*/journal.json"):
            data = _load_json(path, "transaction journal")
            if data.get("status") in {"IN_PROGRESS", "RECOVERY_FAILED"}:
                pending.append(path)
        if pending:
            raise RecoveryError("dry run refuses to bypass pending recovery state")
    _install_preflight(options, source_root, skills_root, codex_home)
    bundle = verify_bundle(source_root, options.expected_bundle_sha256)
    source = _verify_source(options, source_root, bundle)
    authority = _check_universal_authority(options, source)
    return {
        "status": "dry_run",
        "operation": "install",
        "bundle_sha256": bundle.aggregate_sha256,
        "source_commit": source.get("git_commit"),
        "authority_case_id": authority.get("case_id"),
        "skills_root": str(skills_root),
        "support_root": str(codex_home / "coding-os"),
    }


def install(options: InstallOptions) -> dict[str, Any]:
    source_root = Path(options.source_root).expanduser().resolve(strict=True)
    skills_root, codex_home = _canonical_roots(
        options.skills_root,
        options.codex_home,
        legacy_overlap_migration=options.legacy_overlap_migration,
    )
    _fault_configuration(skills_root, codex_home)
    if options.dry_run:
        return _dry_run_install(options, source_root, skills_root, codex_home)
    transaction_id = uuid.uuid4().hex
    transaction_workspace = _transaction_workspace_path(transaction_id, skills_root, codex_home)
    state_root = codex_home / ".coding-os-install"
    with exclusive_install_lock(state_root, transaction_id, "install"):
        _recover_pending(state_root, skills_root, codex_home)
        previous_current, _ = _read_previous_current(state_root)
        transaction_dir = state_root / "transactions" / transaction_id
        transaction_dir.mkdir(parents=True, exist_ok=False)
        config_path = codex_home / "config.toml"
        config_snapshot = _snapshot_file(config_path)
        journal = Journal(
            transaction_dir / "journal.json",
            {
                "journal_version": JOURNAL_VERSION,
                "transaction_protocol": TRANSACTION_PROTOCOL,
                "transaction_id": transaction_id,
                "transaction_workspace": str(transaction_workspace),
                "operation": "install",
                "status": "IN_PROGRESS",
                "phase": None,
                "created_at": utc_now(),
                "recovery_attempted": False,
                "skills_root": str(skills_root),
                "codex_home": str(codex_home),
                "config_snapshot": config_snapshot,
                "previous_current_base64": (
                    base64.b64encode(previous_current).decode("ascii") if previous_current is not None else None
                ),
                "previous_current_sha256": _sha_bytes(previous_current) if previous_current is not None else None,
                "new_pointer_sha256": None,
                "targets": [],
                "stage_roots": [
                    str(transaction_workspace / "stage"),
                ],
                "rollback_roots": [
                    str(transaction_workspace / "rollback"),
                ],
            },
        )
        journal.save()
        try:
            _create_transaction_workspace(transaction_id, skills_root, codex_home)
            journal.phase("LOCK_ACQUIRED", skills_root, codex_home)
            previous, previous_records, _, legacy_overlap_marker = _install_preflight(
                options, source_root, skills_root, codex_home
            )
            journal.phase("PREFLIGHT_VERIFIED", skills_root, codex_home)
            bundle = verify_bundle(source_root, options.expected_bundle_sha256)
            source = _verify_source(options, source_root, bundle)
            authority = _check_universal_authority(options, source)
            journal.data["bundle_sha256"] = bundle.aggregate_sha256
            journal.data["source_commit"] = source.get("git_commit")
            journal.save()
            journal.phase("SOURCE_VERIFIED", skills_root, codex_home)
            staged = _stage_bundle(
                options,
                source_root,
                skills_root,
                codex_home,
                transaction_workspace,
                bundle,
                transaction_id,
                previous,
                legacy_overlap_marker,
                source,
                authority,
                config_snapshot,
            )
            journal.phase("STAGE_VERIFIED", skills_root, codex_home)
            if _is_idempotent(options, previous, staged, source, authority, skills_root, codex_home):
                journal.data["status"] = "NOOP"
                journal.data["outcome"] = "already_committed"
                journal.save()
                _retain_and_cleanup(
                    journal, state_root, committed=False, skills_root=skills_root, codex_home=codex_home
                )
                return {
                    "status": "already_committed",
                    "operation": "install",
                    "bundle_sha256": bundle.aggregate_sha256,
                }
            targets = _prepare_install_targets(
                skills_root,
                codex_home,
                transaction_workspace,
                previous_records,
                staged,
            )
            journal.data["targets"] = targets
            install_manifest_path = codex_home / "coding-os" / "install-manifest.json"
            pointer = {
                "protocol": TRANSACTION_PROTOCOL,
                "status": "committed",
                "transaction_id": transaction_id,
                "install_manifest_path": str(install_manifest_path),
                "install_manifest_sha256": staged["install_manifest_sha256"],
                "bundle_sha256": bundle.aggregate_sha256,
            }
            pointer_bytes = _json_bytes(pointer)
            journal.data["new_pointer"] = pointer
            journal.data["new_pointer_sha256"] = _sha_bytes(pointer_bytes)
            journal.save()
            journal.phase("PROMOTION_PREPARED", skills_root, codex_home)
            journal.phase("PROMOTING", skills_root, codex_home)
            _promote_targets(journal, skills_root, codex_home)
            for target in targets:
                if not _verify_target_state(target, True):
                    raise RecoveryError(f"live target verification failed: {target['target_id']}")
            journal.phase("LIVE_TARGETS_VERIFIED", skills_root, codex_home)
            if not _same_file_snapshot(config_path, config_snapshot):
                raise TransactionError("config.toml changed during the transaction; it was not overwritten")
            live_manifest = codex_home / "coding-os" / "install-manifest.json"
            if _sha_file(live_manifest) != staged["install_manifest_sha256"]:
                raise RecoveryError("live install manifest hash differs from staged provenance")
            _atomic_write_bytes(state_root / "current.json", pointer_bytes)
            journal.phase("CURRENT_POINTER_COMMITTED", skills_root, codex_home)
            journal.data["outcome"] = "committed"
            journal.save()
            _retain_and_cleanup(
                journal, state_root, committed=True, skills_root=skills_root, codex_home=codex_home
            )
            _fault_after("CLEANUP_COMPLETE", skills_root, codex_home)
            return {
                "status": "committed",
                "operation": "install",
                "transaction_id": transaction_id,
                "bundle_sha256": bundle.aggregate_sha256,
                "install_manifest_sha256": staged["install_manifest_sha256"],
            }
        except Exception as exc:
            if _pointer_matches_new(journal, state_root):
                for target in journal.data.get("targets", []):
                    if not _verify_target_state(target, True):
                        journal.data["status"] = "RECOVERY_FAILED"
                        journal.data["recovery_error"] = f"post-pointer target mismatch: {target['target_id']}"
                        journal.save()
                        raise RecoveryError(journal.data["recovery_error"]) from exc
                journal.data["outcome"] = "committed_recovered"
                _retain_and_cleanup(
                    journal, state_root, committed=True, skills_root=skills_root, codex_home=codex_home
                )
                return {
                    "status": "committed_recovered",
                    "operation": "install",
                    "transaction_id": transaction_id,
                    "bundle_sha256": journal.data.get("bundle_sha256"),
                }
            _rollback_targets(journal, state_root)
            _retain_and_cleanup(
                journal, state_root, committed=False, skills_root=skills_root, codex_home=codex_home
            )
            raise


def _validate_v3_for_uninstall(
    manifest: dict[str, Any],
    skills_root: Path,
    codex_home: Path,
    *,
    legacy_overlap_migration: bool = False,
) -> tuple[list[dict[str, Any]], Path]:
    if manifest.get("manifest_version") != 3 or manifest.get("transaction_protocol") != TRANSACTION_PROTOCOL:
        raise TransactionError("uninstall requires a readable v3 manifest or strict v2 migration")
    targets = manifest.get("targets")
    if not isinstance(targets, dict):
        raise TransactionError("v3 target inventory is invalid")
    if Path(str(targets.get("skills_root", ""))).resolve(strict=False) != skills_root:
        raise TransactionError("v3 SkillsRoot does not match the requested uninstall root")
    support_root = Path(str(targets.get("support_root", ""))).resolve(strict=False)
    if support_root != (codex_home / "coding-os").resolve(strict=False):
        raise TransactionError("v3 support root is outside the requested CodexHome")
    if legacy_overlap_migration:
        _legacy_overlap_manifest_marker(manifest, skills_root, codex_home)
    records = _previous_skill_records(manifest, skills_root)
    for record in records:
        live = Path(record["path"])
        if not live.exists() or _tree_hash(live) != record.get("sha256"):
            raise OwnershipError(f"managed skill changed since install; refusing uninstall: {record['name']}")
    generated = manifest.get("generated_files") if isinstance(manifest.get("generated_files"), list) else []
    if _support_payload_hash(support_root, generated) != targets.get("support_payload_sha256"):
        raise OwnershipError("managed support payload changed since install; refusing uninstall")
    for record in generated:
        if not isinstance(record, dict) or record.get("path") == "install-manifest.json":
            continue
        relative = _normalize_relative(str(record.get("path", "")))
        path = support_root.joinpath(*PurePosixPath(relative).parts)
        if not path.is_file() or _sha_file(path) != record.get("sha256"):
            raise OwnershipError(f"generated support file changed since install: {relative}")
    return records, support_root


def _dry_run_uninstall(options: UninstallOptions, skills_root: Path, codex_home: Path) -> dict[str, Any]:
    state_root = codex_home / ".coding-os-install"
    _, pointer = _read_previous_current(state_root)
    if pointer is not None and pointer.get("status") == "uninstalled":
        return {"status": "already_uninstalled", "operation": "uninstall"}
    manifest = _load_previous_install(skills_root, codex_home)
    if manifest is None:
        raise TransactionError("no owned install manifest is available for uninstall")
    if manifest.get("manifest_version") == 3:
        records, support = _validate_v3_for_uninstall(
            manifest,
            skills_root,
            codex_home,
            legacy_overlap_migration=options.legacy_overlap_migration,
        )
    else:
        if options.legacy_overlap_migration:
            raise OwnershipError("legacy-overlap uninstall requires the prior v2 install to be migrated to v3 first")
        records = _previous_skill_records(manifest, skills_root)
        support = Path(str(manifest.get("support_root", ""))).resolve(strict=False)
        if support not in {
            (codex_home / "coding-os").resolve(strict=False),
            (codex_home / "coding-os-starter").resolve(strict=False),
        }:
            raise TransactionError("legacy support root is outside the requested CodexHome")
    return {
        "status": "dry_run",
        "operation": "uninstall",
        "managed_skills": [record["name"] for record in records],
        "support_root": str(support),
    }


def uninstall(options: UninstallOptions) -> dict[str, Any]:
    skills_root, codex_home = _canonical_roots(
        options.skills_root,
        options.codex_home,
        legacy_overlap_migration=options.legacy_overlap_migration,
    )
    _fault_configuration(skills_root, codex_home)
    if options.dry_run:
        return _dry_run_uninstall(options, skills_root, codex_home)
    transaction_id = uuid.uuid4().hex
    transaction_workspace = _transaction_workspace_path(transaction_id, skills_root, codex_home)
    state_root = codex_home / ".coding-os-install"
    with exclusive_install_lock(state_root, transaction_id, "uninstall"):
        _recover_pending(state_root, skills_root, codex_home)
        previous_current, pointer = _read_previous_current(state_root)
        if pointer is not None and pointer.get("status") == "uninstalled":
            return {"status": "already_uninstalled", "operation": "uninstall"}
        manifest = _load_previous_install(skills_root, codex_home)
        if manifest is None:
            raise TransactionError("no owned install manifest is available for uninstall")
        transaction_dir = state_root / "transactions" / transaction_id
        transaction_dir.mkdir(parents=True, exist_ok=False)
        config_path = codex_home / "config.toml"
        config_snapshot = _snapshot_file(config_path)
        journal = Journal(
            transaction_dir / "journal.json",
            {
                "journal_version": JOURNAL_VERSION,
                "transaction_protocol": TRANSACTION_PROTOCOL,
                "transaction_id": transaction_id,
                "transaction_workspace": str(transaction_workspace),
                "operation": "uninstall",
                "status": "IN_PROGRESS",
                "phase": None,
                "created_at": utc_now(),
                "recovery_attempted": False,
                "skills_root": str(skills_root),
                "codex_home": str(codex_home),
                "config_snapshot": config_snapshot,
                "previous_current_base64": (
                    base64.b64encode(previous_current).decode("ascii") if previous_current is not None else None
                ),
                "previous_current_sha256": _sha_bytes(previous_current) if previous_current is not None else None,
                "new_pointer_sha256": None,
                "targets": [],
                "stage_roots": [
                    str(transaction_workspace / "stage"),
                ],
                "rollback_roots": [
                    str(transaction_workspace / "rollback"),
                ],
            },
        )
        journal.save()
        try:
            _create_transaction_workspace(transaction_id, skills_root, codex_home)
            journal.phase("LOCK_ACQUIRED", skills_root, codex_home)
            if manifest.get("manifest_version") == 3:
                records, support_root = _validate_v3_for_uninstall(
                    manifest,
                    skills_root,
                    codex_home,
                    legacy_overlap_migration=options.legacy_overlap_migration,
                )
            else:
                if options.legacy_overlap_migration:
                    raise OwnershipError("legacy-overlap uninstall requires the prior v2 install to be migrated to v3 first")
                records = _previous_skill_records(manifest, skills_root)
                support_root = Path(str(manifest.get("support_root", ""))).resolve(strict=False)
                if support_root not in {
                    (codex_home / "coding-os").resolve(strict=False),
                    (codex_home / "coding-os-starter").resolve(strict=False),
                }:
                    raise TransactionError("legacy support root is outside the requested CodexHome")
            journal.phase("PREFLIGHT_VERIFIED", skills_root, codex_home)
            journal.phase("SOURCE_VERIFIED", skills_root, codex_home)
            skill_rollback = transaction_workspace / "rollback" / "skills"
            codex_rollback = transaction_workspace / "rollback" / "codex-home"
            codex_stage_home = transaction_workspace / "stage" / "codex-home"
            codex_stage_home.mkdir(parents=True, exist_ok=False)
            targets: list[dict[str, Any]] = []
            for record in sorted(records, key=lambda value: str(value["name"]).casefold()):
                targets.append(
                    _make_target(
                        f"skill:{record['name']}",
                        Path(record["path"]),
                        None,
                        skill_rollback / str(record["name"]),
                    )
                )
            targets.append(_make_target("support", support_root, None, codex_rollback / support_root.name))
            if manifest.get("manifest_version") == 3:
                target_info = manifest["targets"]
                agents_record = target_info.get("global_agents", {})
                rules_record = target_info.get("default_rules", {})
                if isinstance(agents_record, dict) and agents_record.get("managed") is True:
                    live_agents = codex_home / "AGENTS.md"
                    staged_agents = codex_stage_home / "policy" / "AGENTS.md"
                    _atomic_write_bytes(staged_agents, remove_agents_policy_bytes(live_agents.read_bytes()))
                    targets.append(_make_target("global_agents", live_agents, staged_agents, codex_rollback / "AGENTS.md"))
                if isinstance(rules_record, dict) and rules_record.get("managed") is True:
                    live_rules = codex_home / "rules" / "default.rules"
                    staged_rules = codex_stage_home / "policy" / "rules" / "default.rules"
                    _atomic_write_bytes(staged_rules, remove_rules_policy_bytes(live_rules.read_bytes()))
                    targets.append(_make_target("default_rules", live_rules, staged_rules, codex_rollback / "rules/default.rules"))
            for target in targets:
                if Path(target["live_path"]).resolve(strict=False) == codex_home:
                    raise TransactionError("transaction targets must never rename, move, or replace CodexHome itself")
            journal.data["targets"] = targets
            journal.save()
            journal.phase("STAGE_VERIFIED", skills_root, codex_home)
            bundle_hash = (
                manifest.get("package", {}).get("bundle_sha256")
                if isinstance(manifest.get("package"), dict)
                else None
            )
            new_pointer = {
                "protocol": TRANSACTION_PROTOCOL,
                "status": "uninstalled",
                "transaction_id": transaction_id,
                "uninstalled_at": utc_now(),
                "previous_install_manifest_sha256": (
                    pointer.get("install_manifest_sha256") if isinstance(pointer, dict) else None
                ),
                "bundle_sha256": bundle_hash,
            }
            pointer_bytes = _json_bytes(new_pointer)
            journal.data["new_pointer"] = new_pointer
            journal.data["new_pointer_sha256"] = _sha_bytes(pointer_bytes)
            journal.save()
            journal.phase("PROMOTION_PREPARED", skills_root, codex_home)
            journal.phase("PROMOTING", skills_root, codex_home)
            _promote_targets(journal, skills_root, codex_home)
            for target in targets:
                if not _verify_target_state(target, True):
                    raise RecoveryError(f"uninstall target verification failed: {target['target_id']}")
            journal.phase("LIVE_TARGETS_VERIFIED", skills_root, codex_home)
            if not _same_file_snapshot(config_path, config_snapshot):
                raise TransactionError("config.toml changed during uninstall; it was not overwritten")
            _atomic_write_bytes(state_root / "current.json", pointer_bytes)
            journal.phase("CURRENT_POINTER_COMMITTED", skills_root, codex_home)
            journal.data["outcome"] = "uninstalled"
            journal.save()
            _retain_and_cleanup(journal, state_root, committed=True, skills_root=skills_root, codex_home=codex_home)
            _fault_after("CLEANUP_COMPLETE", skills_root, codex_home)
            return {"status": "uninstalled", "operation": "uninstall", "transaction_id": transaction_id}
        except Exception as exc:
            if _pointer_matches_new(journal, state_root):
                for target in journal.data.get("targets", []):
                    if not _verify_target_state(target, True):
                        journal.data["status"] = "RECOVERY_FAILED"
                        journal.data["recovery_error"] = f"post-pointer target mismatch: {target['target_id']}"
                        journal.save()
                        raise RecoveryError(journal.data["recovery_error"]) from exc
                journal.data["outcome"] = "uninstalled_recovered"
                _retain_and_cleanup(
                    journal, state_root, committed=True, skills_root=skills_root, codex_home=codex_home
                )
                return {"status": "uninstalled_recovered", "operation": "uninstall", "transaction_id": transaction_id}
            _rollback_targets(journal, state_root)
            _retain_and_cleanup(journal, state_root, committed=False, skills_root=skills_root, codex_home=codex_home)
            raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transactional Codex Coding OS bundle installer and uninstaller."
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build-bundle", help="Build the public relative-path bundle manifest.")
    build.add_argument("--repo-root", required=True)

    verify = commands.add_parser("verify-bundle", help="Verify bundle inventory and aggregate hash.")
    verify.add_argument("--repo-root", required=True)
    verify.add_argument("--expected-bundle-sha256")

    install_parser = commands.add_parser("install", help="Install one fully verified bundle transaction.")
    install_parser.add_argument("--source-root", required=True)
    install_parser.add_argument("--skills-root", required=True)
    install_parser.add_argument("--codex-home", required=True)
    install_parser.add_argument("--expected-bundle-sha256", required=True)
    install_parser.add_argument("--expected-source-commit")
    install_parser.add_argument("--install-universal-policy", action="store_true")
    install_parser.add_argument("--universal-bundle-id", default=UNIVERSAL_BUNDLE_ID)
    install_parser.add_argument("--refresh-capability-index", action="store_true")
    install_parser.add_argument("--authority-case-id")
    install_parser.add_argument(
        "--authority-source",
        choices=("preauthorized-run-envelope", "explicit-user-approval"),
    )
    install_parser.add_argument("--authority-reference")
    install_parser.add_argument("--case-state-engine")
    install_parser.add_argument("--case-state-root")
    install_parser.add_argument("--legacy-overlap-migration", action="store_true")
    install_parser.add_argument("--archive-mode", action="store_true")
    install_parser.add_argument("--dry-run", action="store_true")

    uninstall_parser = commands.add_parser("uninstall", help="Transactionally remove recorded managed targets.")
    uninstall_parser.add_argument("--skills-root", required=True)
    uninstall_parser.add_argument("--codex-home", required=True)
    uninstall_parser.add_argument("--legacy-overlap-migration", action="store_true")
    uninstall_parser.add_argument("--dry-run", action="store_true")
    return parser


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "build-bundle":
        manifest = build_bundle_manifest(args.repo_root)
        return {
            "status": "built",
            "bundle_manifest": str(Path(args.repo_root).resolve() / "install-bundle.manifest.json"),
            "aggregate_sha256": manifest["aggregate_sha256"],
            "entry_count": len(manifest["entries"]),
        }
    if args.command == "verify-bundle":
        bundle = verify_bundle(args.repo_root, args.expected_bundle_sha256)
        return {
            "status": "verified",
            "aggregate_sha256": bundle.aggregate_sha256,
            "bundle_manifest_sha256": bundle.manifest_sha256,
            "entry_count": len(bundle.entries),
        }
    if args.command == "install":
        return install(
            InstallOptions(
                source_root=args.source_root,
                skills_root=args.skills_root,
                codex_home=args.codex_home,
                expected_bundle_sha256=args.expected_bundle_sha256,
                expected_source_commit=args.expected_source_commit,
                install_universal_policy=args.install_universal_policy,
                universal_bundle_id=args.universal_bundle_id,
                refresh_capability_index=args.refresh_capability_index,
                authority_case_id=args.authority_case_id,
                authority_source=args.authority_source,
                authority_reference=args.authority_reference,
                case_state_engine=args.case_state_engine,
                case_state_root=args.case_state_root,
                legacy_overlap_migration=args.legacy_overlap_migration,
                archive_mode=args.archive_mode,
                dry_run=args.dry_run,
            )
        )
    if args.command == "uninstall":
        return uninstall(
            UninstallOptions(
                skills_root=args.skills_root,
                codex_home=args.codex_home,
                legacy_overlap_migration=args.legacy_overlap_migration,
                dry_run=args.dry_run,
            )
        )
    raise AssertionError(f"unhandled command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = execute(args)
    except TransactionError as exc:
        payload = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(f"INSTALL TRANSACTION ERROR [{type(exc).__name__}]: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, sort_keys=True))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
