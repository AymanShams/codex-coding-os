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
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.parse
import uuid
from typing import Any, Callable, Iterator, Sequence


TRANSACTION_PROTOCOL = "ccos-install-transaction-v1"
BUNDLE_PROTOCOL = "CCOS-INSTALL-BUNDLE-v1"
CAMPAIGN_PROTOCOL = "ccos-campaign-v1"
SCHEMA_COMPATIBILITY = "campaign-store-v1"
HOST_CAPABILITY_PROBE_VERSION = "native-bind-before-turn-scoped-tools-v3"
HOOKS_CONFIGURATION_PROTOCOL = "ccos-hooks-configuration-v1"
CAMPAIGN_HOOK_EVENT = "PreToolUse"
CAMPAIGN_HOOK_MATCHER = "^(campaign_apply_patch|campaign_commit)$"
CAMPAIGN_HOOK_STATUS = "Verifying exact campaign actor lease"
LEGACY_LIFECYCLE_HOOK_SUFFIX = "/coding-os/hooks/anti-loop-runtime/anti_loop_runtime.py"
RUNTIME_PIN_FIELDS = (
    "source_commit",
    "bundle_digest",
    "install_transaction",
    "protocol_version",
    "schema_compatibility",
    "host_capability_probe_version",
)
BUNDLE_DOMAIN = b"CCOS-INSTALL-BUNDLE-v1\0"
TREE_DOMAIN = b"CCOS-TREE-v1\0"
CODEX_MANAGED_PLUGIN_SKILL_DIRECTORIES = frozenset(
    {
        "attack-path-analysis",
        "deep-security-scan",
        "define-security-policy",
        "finding-discovery",
        "fix-finding",
        "propose-security-hardening",
        "security-diff-scan",
        "security-scan",
        "threat-model",
        "track-findings",
        "triage-finding",
        "validation",
        "vulnerability-writeup",
        "supabase",
        "supabase-postgres-best-practices",
        "neon-postgres",
        "neon-postgres-egress-optimizer",
    }
)
CODEX_MANAGED_PLUGIN_SKILL_DIRECTORY_KEYS = frozenset(
    name.casefold() for name in CODEX_MANAGED_PLUGIN_SKILL_DIRECTORIES
)
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

AGENTS_START = "<!-- BEGIN CODEX CODING OS MANAGED: CAMPAIGN ENGINE POLICY -->"
AGENTS_END = "<!-- END CODEX CODING OS MANAGED: CAMPAIGN ENGINE POLICY -->"
RETIRED_AGENTS_START = "<!-- BEGIN CODEX CODING OS MANAGED: AUTOMATION-PRESERVING CASE POLICY -->"
RETIRED_AGENTS_END = "<!-- END CODEX CODING OS MANAGED: AUTOMATION-PRESERVING CASE POLICY -->"
AGENTS_LEGACY_LINE = (
    "  - Manual Session And Case Isolation Policy: parent-orchestrator mode and automatic session, review, "
    "and review-fix trains are disabled. A human may deliberately start one bounded implementation or review "
    "session, but no session may automatically spawn, authorize, or chain another session."
)
AGENTS_LEGACY_BLOCK_START = (
    "- Instruction: Task primacy and anti-loop rule. The user's requested outcome is the controlling task."
)
AGENTS_LEGACY_BLOCK_SENTINEL = (
    "- Instruction: The user's style bans apply to normal assistant-generated prose only."
)
AGENTS_LEGACY_BLOCK_NORMALIZED_SIZE = 6093
AGENTS_LEGACY_BLOCK_NORMALIZED_SHA256 = (
    "da753c1149c555778d7b4cba6bdb870640bbb484281cb6b0f9f46a1b02a66cd6"
)
AGENTS_LEGACY_AUTHORITY_LINE = (
    "- Global `AGENTS.md` owns instruction precedence, permission boundaries, task primacy, mandatory exclusions, "
    "and the managed case-policy block."
)
AGENTS_CAMPAIGN_AUTHORITY_LINE = (
    "- Global `AGENTS.md` owns instruction precedence, permission boundaries, task primacy, mandatory exclusions, "
    "and the managed campaign-policy bootstrap."
)
AGENTS_LEGACY_ROUTING_START = "## Codex Coding OS routing"
AGENTS_LEGACY_ROUTING_SENTINEL = "## Universal generic workspace"
AGENTS_LEGACY_ROUTING_NORMALIZED_SIZE = 1040
AGENTS_LEGACY_ROUTING_NORMALIZED_SHA256 = (
    "7a583b1d396451457e6d8d3ac8159cba2fae5994bee7350a1b2d849ee8db238b"
)
AGENTS_CAMPAIGN_ROUTING_POINTER = (
    "## Codex Coding OS routing\n"
    "- The installed campaign-engine managed block controls managed campaign lifecycle routing. For manual work "
    "outside a managed campaign, use explicit user instructions and current repository and Git evidence.\n\n"
)
RULES_START = "# BEGIN CODEX CODING OS MANAGED: CAMPAIGN EXTERNAL EFFECTS"
RULES_END = "# END CODEX CODING OS MANAGED: CAMPAIGN EXTERNAL EFFECTS"
RETIRED_RULES_START = "# BEGIN CODEX CODING OS MANAGED: GH PR MERGE AUTHORITY"
RETIRED_RULES_END = "# END CODEX CODING OS MANAGED: GH PR MERGE AUTHORITY"
RULES_LEGACY_LINE = 'prefix_rule(pattern=["gh", "pr", "merge"], decision="allow")'
UNIVERSAL_BUNDLE_ID = "campaign-engine-policy-v1"
UNIVERSAL_BUNDLE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,126}[a-z0-9])?$")
LEGACY_OVERLAP_LAYOUT = "codex-home-skills-v2-to-v3"
LEGACY_V2_PACKAGES = frozenset({"codex-coding-os", "codex-coding-os-starter"})
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,126}[A-Za-z0-9])?$")
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_READONLY = 0x1
WINDOWS_PLATFORM = os.name == "nt"


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
    remove_universal_policy: bool = False
    universal_bundle_id: str = UNIVERSAL_BUNDLE_ID
    policy_authority_source: str | None = None
    policy_authority_reference: str | None = None
    publication_campaign_id: str | None = None
    publication_node_id: str | None = None
    publication_authority_epoch: int | None = None
    publication_cancellation_epoch: int | None = None
    archive_legacy_state: bool = False
    legacy_state_root: Path | str | None = None
    legacy_overlap_migration: bool = False
    archive_mode: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class UninstallOptions:
    skills_root: Path | str
    codex_home: Path | str
    legacy_overlap_migration: bool = False
    dry_run: bool = False


def _universal_policy_mode(options: InstallOptions) -> str:
    if options.install_universal_policy and options.remove_universal_policy:
        raise AuthorityError(
            "--install-universal-policy and --remove-universal-policy are mutually exclusive"
        )
    if options.install_universal_policy:
        return "install"
    if options.remove_universal_policy:
        return "remove"
    return "preserve"


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
    if "capability_refresh_cli" in installation:
        raise BundleError("installation.capability_refresh_cli is retired")
    hook = installation.get("campaign_hook")
    if not isinstance(hook, dict) or set(hook) != {"source", "target"}:
        raise BundleError("installation.campaign_hook must declare only source and target")
    source = _normalize_relative(str(hook.get("source", "")))
    target = _normalize_relative(str(hook.get("target", "")))
    if target != "hooks/campaign-engine":
        raise BundleError("campaign hook target must be hooks/campaign-engine")
    if not _safe_repo_path(root, source).is_dir():
        raise BundleError("campaign hook source must be one real directory")
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
        if name.casefold() in CODEX_MANAGED_PLUGIN_SKILL_DIRECTORY_KEYS:
            raise BundleError(
                f"Codex-managed plugin skill bodies cannot be bundled: {name}"
            )
        skill_names.append(name)
        declared.append(f"{skill_root}/{name}")
    _validate_casefold_collisions(skill_names, "managed skill name")
    support_items = pack.get("support_items")
    if not isinstance(support_items, list) or not support_items:
        raise BundleError("support_items must be a non-empty list")
    runtime_files = installation.get("runtime_files")
    if not isinstance(runtime_files, list) or not runtime_files:
        raise BundleError("installation.runtime_files must be a non-empty list")
    install_declared = [*support_items, *runtime_files]
    retired_router_prefix = "/".join(("hooks", "capability-router")) + "/"
    for item in install_declared:
        normalized = _normalize_relative(str(item))
        normalized_key = normalized.casefold()
        if (
            normalized_key in {"hooks", "capability-index", "capability-routing"}
            or normalized_key.startswith(retired_router_prefix)
            or normalized_key.startswith("capability-index/")
            or normalized_key.startswith("capability-routing/")
        ):
            raise BundleError(
                f"routing reference source cannot be installed as support payload: {normalized}"
            )
    declared.extend(str(value) for value in support_items)
    declared.extend(str(value) for value in runtime_files)
    campaign_hook = installation.get("campaign_hook")
    if isinstance(campaign_hook, dict):
        declared.append(str(campaign_hook["source"]))
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
    retired_markers: Sequence[tuple[str, str]] = (),
    first_migration: Callable[[bytes, bytes], bytes] | None = None,
    append_when_legacy_missing: bool = False,
) -> bytes:
    _strict_utf8(existing, label)
    replacement = _trim_policy_source(replacement, label, start, end)
    start_bytes = start.encode("utf-8")
    end_bytes = end.encode("utf-8")
    start_count = existing.count(start_bytes)
    end_count = existing.count(end_bytes)
    legacy = legacy_line.encode("utf-8")
    active_match: tuple[bytes, bytes] | None = None
    if start_count or end_count:
        if start_count != 1 or end_count != 1:
            raise PolicyMigrationError(f"{label} has a missing, duplicate, or partial managed marker block")
        begin = existing.find(start_bytes)
        finish_start = existing.find(end_bytes)
        if finish_start < begin:
            raise PolicyMigrationError(f"{label} managed marker order is invalid")
        active_match = (start_bytes, end_bytes)
    retired_matches: list[tuple[bytes, bytes]] = []
    for retired_start, retired_end in retired_markers:
        retired_start_bytes = retired_start.encode("utf-8")
        retired_end_bytes = retired_end.encode("utf-8")
        retired_start_count = existing.count(retired_start_bytes)
        retired_end_count = existing.count(retired_end_bytes)
        if retired_start_count or retired_end_count:
            if retired_start_count != 1 or retired_end_count != 1:
                raise PolicyMigrationError(
                    f"{label} has a missing, duplicate, or partial retired managed marker block"
                )
            retired_matches.append((retired_start_bytes, retired_end_bytes))
    if len(retired_matches) > 1:
        raise PolicyMigrationError(f"{label} contains multiple retired managed marker blocks")
    if active_match is not None and retired_matches:
        raise PolicyMigrationError(f"{label} contains active and retired managed marker blocks")
    if active_match is not None:
        active_start_bytes, active_end_bytes = active_match
        begin = existing.find(active_start_bytes)
        finish_start = existing.find(active_end_bytes)
        finish = finish_start + len(active_end_bytes)
        return existing[:begin] + replacement + existing[finish:]
    if retired_matches:
        retired_start_bytes, retired_end_bytes = retired_matches[0]
        begin = existing.find(retired_start_bytes)
        finish_start = existing.find(retired_end_bytes)
        if finish_start < begin:
            raise PolicyMigrationError(f"{label} retired managed marker order is invalid")
        finish = finish_start + len(retired_end_bytes)
        return existing[:begin] + replacement + existing[finish:]
    legacy_count = existing.count(legacy)
    if legacy_count == 0:
        if first_migration is not None:
            return first_migration(existing, replacement)
        if append_when_legacy_missing:
            separator = (
                b"" if not existing or existing.endswith((b"\n", b"\r")) else b"\n"
            )
            return existing + separator + replacement
        raise PolicyMigrationError(f"{label} first migration requires exactly one byte-exact legacy line")
    if legacy_count != 1:
        raise PolicyMigrationError(f"{label} first migration requires exactly one byte-exact legacy line")
    begin = existing.find(legacy)
    before_ok = begin == 0 or existing[begin - 1 : begin] in {b"\n", b"\r"}
    after_index = begin + len(legacy)
    after_ok = after_index == len(existing) or existing[after_index : after_index + 1] in {b"\n", b"\r"}
    if not before_ok or not after_ok:
        raise PolicyMigrationError(f"{label} legacy text is not an exact complete line")
    return existing[:begin] + replacement + existing[after_index:]


def _verified_legacy_slice(
    existing: bytes,
    *,
    start: str,
    sentinel: str,
    normalized_size: int,
    normalized_sha256: str,
    label: str,
) -> tuple[int, int]:
    start_bytes = start.encode("utf-8")
    sentinel_bytes = sentinel.encode("utf-8")
    if existing.count(start_bytes) != 1 or existing.count(sentinel_bytes) != 1:
        raise PolicyMigrationError(f"{label} has a missing, duplicate, or partial known legacy section")
    begin = existing.find(start_bytes)
    finish = existing.find(sentinel_bytes)
    if finish <= begin:
        raise PolicyMigrationError(f"{label} known legacy section order is invalid")
    before_ok = begin == 0 or existing[begin - 1 : begin] == b"\n"
    legacy_slice = existing[begin:finish]
    if not before_ok or not legacy_slice.endswith(b"\n"):
        raise PolicyMigrationError(f"{label} known legacy section boundaries are not complete lines")
    normalized = legacy_slice.replace(b"\r\n", b"\n")
    if len(normalized) != normalized_size or hashlib.sha256(normalized).hexdigest() != normalized_sha256:
        raise PolicyMigrationError(f"{label} known legacy section digest does not match the approved layout")
    return begin, finish


def _exact_line_span(existing: bytes, line: str, label: str) -> tuple[int, int]:
    line_bytes = line.encode("utf-8")
    if existing.count(line_bytes) != 1:
        raise PolicyMigrationError(f"{label} requires exactly one byte-exact known legacy authority line")
    begin = existing.find(line_bytes)
    finish = begin + len(line_bytes)
    before_ok = begin == 0 or existing[begin - 1 : begin] == b"\n"
    after_ok = (
        finish == len(existing)
        or existing[finish : finish + 1] == b"\n"
        or existing[finish : finish + 2] == b"\r\n"
    )
    if not before_ok or not after_ok:
        raise PolicyMigrationError(f"{label} known legacy authority text is not an exact complete line")
    return begin, finish


def _migrate_markerless_campaign_agents_layout(
    existing: bytes,
    replacement: bytes,
) -> bytes | None:
    """Restore the managed block after an explicit prior policy removal.

    A policy opt-out removes only the marker block. The campaign authority line
    and compact routing pointer are stable global instructions, so their exact
    markerless layout is sufficient evidence for a later explicit reinstall.
    Any mixed legacy/current or modified routing layout still fails closed.
    """

    forbidden = (
        AGENTS_LEGACY_BLOCK_START,
        AGENTS_LEGACY_AUTHORITY_LINE,
        RETIRED_AGENTS_START,
        RETIRED_AGENTS_END,
    )
    if any(value.encode("utf-8") in existing for value in forbidden):
        return None
    authority = AGENTS_CAMPAIGN_AUTHORITY_LINE.encode("utf-8")
    routing_start = AGENTS_LEGACY_ROUTING_START.encode("utf-8")
    routing_sentinel = AGENTS_LEGACY_ROUTING_SENTINEL.encode("utf-8")
    if existing.count(authority) != 1:
        return None
    if existing.count(routing_start) != 1 or existing.count(routing_sentinel) != 1:
        return None
    authority_span = _exact_line_span(existing, AGENTS_CAMPAIGN_AUTHORITY_LINE, "AGENTS.md")
    begin = existing.find(routing_start)
    finish = existing.find(routing_sentinel)
    if finish <= begin:
        raise PolicyMigrationError("AGENTS.md markerless campaign routing order is invalid")
    if begin > 0 and existing[begin - 1 : begin] != b"\n":
        raise PolicyMigrationError("AGENTS.md markerless campaign routing is not a complete section")
    routing = existing[begin:finish]
    normalized = routing.replace(b"\r\n", b"\n")
    if normalized != AGENTS_CAMPAIGN_ROUTING_POINTER.encode("utf-8"):
        raise PolicyMigrationError("AGENTS.md markerless campaign routing does not match the approved layout")
    if authority_span[0] >= begin and authority_span[0] < finish:
        raise PolicyMigrationError("AGENTS.md campaign authority line overlaps the routing section")
    newline = b"\r\n" if b"\r\n" in routing else b"\n"
    return existing[:begin] + replacement + newline + existing[begin:]


def _migrate_known_agents_layout(existing: bytes, replacement: bytes) -> bytes:
    markerless = _migrate_markerless_campaign_agents_layout(existing, replacement)
    if markerless is not None:
        return markerless
    lifecycle_span = _verified_legacy_slice(
        existing,
        start=AGENTS_LEGACY_BLOCK_START,
        sentinel=AGENTS_LEGACY_BLOCK_SENTINEL,
        normalized_size=AGENTS_LEGACY_BLOCK_NORMALIZED_SIZE,
        normalized_sha256=AGENTS_LEGACY_BLOCK_NORMALIZED_SHA256,
        label="AGENTS.md lifecycle block",
    )
    routing_span = _verified_legacy_slice(
        existing,
        start=AGENTS_LEGACY_ROUTING_START,
        sentinel=AGENTS_LEGACY_ROUTING_SENTINEL,
        normalized_size=AGENTS_LEGACY_ROUTING_NORMALIZED_SIZE,
        normalized_sha256=AGENTS_LEGACY_ROUTING_NORMALIZED_SHA256,
        label="AGENTS.md routing block",
    )
    authority_span = _exact_line_span(existing, AGENTS_LEGACY_AUTHORITY_LINE, "AGENTS.md")
    lifecycle_slice = existing[lifecycle_span[0] : lifecycle_span[1]]
    lifecycle_newline = b"\r\n" if lifecycle_slice.endswith(b"\r\n") else b"\n"
    replacements = sorted(
        (
            (lifecycle_span[0], lifecycle_span[1], replacement + lifecycle_newline),
            (
                authority_span[0],
                authority_span[1],
                AGENTS_CAMPAIGN_AUTHORITY_LINE.encode("utf-8"),
            ),
            (
                routing_span[0],
                routing_span[1],
                AGENTS_CAMPAIGN_ROUTING_POINTER.encode("utf-8"),
            ),
        )
    )
    if any(left[1] > right[0] for left, right in zip(replacements, replacements[1:])):
        raise PolicyMigrationError("AGENTS.md known legacy migration spans overlap")
    output = bytearray()
    cursor = 0
    for begin, finish, new_bytes in replacements:
        output.extend(existing[cursor:begin])
        output.extend(new_bytes)
        cursor = finish
    output.extend(existing[cursor:])
    return bytes(output)


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
        retired_markers=((RETIRED_AGENTS_START, RETIRED_AGENTS_END),),
        first_migration=_migrate_known_agents_layout,
    )


def _command_path_key(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).replace("\\", "/")
    normalized = re.sub(r"/+", "/", normalized).rstrip("/")
    return normalized.casefold()


def _remove_retired_case_state_command_rules(
    existing: bytes,
    codex_home: Path | str,
) -> bytes:
    """Remove only exact one-line allows for the installed legacy tombstone."""

    _strict_utf8(existing, "default.rules")
    target = Path(codex_home).expanduser().resolve(strict=False) / "coding-os" / "scripts" / "agent" / "case_state.py"
    target_key = _command_path_key(str(target))
    retained: list[bytes] = []
    pattern = re.compile(
        r'^prefix_rule\(pattern=(\[[^\r\n]*\]), decision="allow"\)$'
    )
    for raw_line in existing.splitlines(keepends=True):
        line = raw_line.rstrip(b"\r\n").decode("utf-8")
        match = pattern.fullmatch(line)
        remove = False
        if match is not None:
            try:
                command = json.loads(match.group(1))
            except json.JSONDecodeError:
                command = None
            remove = (
                isinstance(command, list)
                and (
                    (
                        len(command) >= 2
                        and command[0] == "python"
                        and isinstance(command[1], str)
                        and _command_path_key(command[1]) == target_key
                    )
                    or command
                    == [
                        "corepack",
                        "pnpm",
                        "run",
                        "agent:case-state",
                        "--",
                        "--help",
                    ]
                )
            )
        if not remove:
            retained.append(raw_line)
    return b"".join(retained)


def migrate_rules_bytes(
    existing: bytes,
    policy_source: bytes,
    codex_home: Path | str | None = None,
) -> bytes:
    migrated_input = (
        _remove_retired_case_state_command_rules(existing, codex_home)
        if codex_home is not None
        else existing
    )
    return _replace_line_or_marker(
        migrated_input,
        policy_source,
        legacy_line=RULES_LEGACY_LINE,
        start=RULES_START,
        end=RULES_END,
        label="default.rules",
        retired_markers=((RETIRED_RULES_START, RETIRED_RULES_END),),
        append_when_legacy_missing=True,
    )


def remove_agents_policy_bytes(existing: bytes) -> bytes:
    return _remove_exact_marker(existing, AGENTS_START, AGENTS_END, "AGENTS.md")


def remove_rules_policy_bytes(existing: bytes) -> bytes:
    return _remove_exact_marker(existing, RULES_START, RULES_END, "default.rules")


def _remove_recorded_policy_bytes(
    existing: bytes,
    record: dict[str, Any],
    *,
    allowed_markers: Sequence[tuple[str, str]],
    label: str,
) -> bytes:
    markers = (str(record.get("marker_start", "")), str(record.get("marker_end", "")))
    if markers not in allowed_markers:
        raise OwnershipError(f"{label} recorded managed markers are not recognized")
    return _remove_exact_marker(existing, markers[0], markers[1], label)


def _validate_recorded_policy_bytes(
    existing: bytes,
    record: dict[str, Any],
    *,
    allowed_markers: Sequence[tuple[str, str]],
    label: str,
) -> None:
    _remove_recorded_policy_bytes(
        existing,
        record,
        allowed_markers=allowed_markers,
        label=label,
    )


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


def _retry_windows_readonly_remove(func: Any, raw_path: str, exc_info: tuple[Any, Any, Any]) -> None:
    exc = exc_info[1]
    if (
        not WINDOWS_PLATFORM
        or not isinstance(exc, PermissionError)
        or getattr(exc, "winerror", None) != 5
        or getattr(func, "__name__", "") not in {"unlink", "remove", "rmdir"}
    ):
        raise exc
    path = Path(raw_path)
    if _is_link_or_reparse(path):
        raise RecoveryError(f"refusing to remove a link or reparse point: {path}")
    try:
        info = path.stat(follow_symlinks=False)
    except OSError:
        raise exc
    readonly = bool(getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_READONLY) or not bool(
        info.st_mode & stat.S_IWRITE
    )
    if not readonly:
        raise exc
    os.chmod(path, info.st_mode | stat.S_IWRITE)
    func(raw_path)


def _remove_owned_path(path: Path) -> None:
    if not path.exists():
        return
    if _is_link_or_reparse(path):
        raise RecoveryError(f"refusing to remove a link or reparse point: {path}")
    if path.is_dir():
        shutil.rmtree(path, onerror=_retry_windows_readonly_remove)
    elif path.is_file():
        try:
            os.unlink(path)
        except PermissionError:
            _retry_windows_readonly_remove(os.unlink, str(path), sys.exc_info())
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
    pause = os.environ.get("CCOS_INSTALL_TEST_PAUSE_AFTER")
    if not point and not hard and not pause:
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
    if os.environ.get("CCOS_INSTALL_TEST_PAUSE_AFTER") == "1":
        ready_file = Path(str(os.environ.get("CCOS_INSTALL_TEST_READY_FILE") or "")).resolve(
            strict=False
        )
        temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
        if not str(ready_file) or not _path_is_within(ready_file, temp_root):
            raise TransactionError(
                "forced-termination pause requires CCOS_INSTALL_TEST_READY_FILE under the OS temp root"
            )
        _atomic_write_bytes(ready_file, (point + "\n").encode("utf-8"))
        while True:
            time.sleep(0.05)
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
    expected_canonical_skills = (codex / "skills").resolve(strict=False)
    if overlaps and skills != expected_canonical_skills:
        raise TransactionError(
            "SkillsRoot and CodexHome may overlap only in the canonical CodexHome/skills layout"
        )
    if legacy_overlap_migration and skills != expected_canonical_skills:
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
    """Return the legacy OS-temp workspace path used by already-written journals."""
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
    return workspace


def _transaction_workspace_paths(
    transaction_id: str,
    skills_root: Path,
    codex_home: Path,
) -> dict[str, Path]:
    if not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
        raise TransactionError("transaction workspaces require one generated 32-character transaction identifier")
    skills_parent = codex_home if _legacy_overlap_layout(skills_root, codex_home) else skills_root
    paths = {
        "skills": (skills_parent / f".coding-os-transaction-{transaction_id}-skills").resolve(strict=False),
        "codex_home": (codex_home / f".coding-os-transaction-{transaction_id}-codex-home").resolve(strict=False),
    }
    if _path_is_within(paths["skills"], skills_root) and _legacy_overlap_layout(skills_root, codex_home):
        raise TransactionError("legacy-overlap transaction workspace must remain outside SkillsRoot")
    for role, workspace in paths.items():
        expected_parent = skills_parent if role == "skills" else codex_home
        if workspace.parent != expected_parent or not _path_is_within(workspace, expected_parent):
            raise TransactionError(f"{role} transaction workspace escaped its target root")
        if workspace in {skills_root, codex_home}:
            raise TransactionError("transaction workspace must not replace an install root")
    return paths


def _device_id(path: Path) -> int:
    return int(_existing_anchor(path).stat().st_dev)


def _assert_workspace_devices(
    workspaces: dict[str, Path],
    skills_root: Path,
    codex_home: Path,
) -> None:
    expected = {"skills": skills_root, "codex_home": codex_home}
    for role, root in expected.items():
        if _device_id(workspaces[role]) != _device_id(root):
            raise TransactionError(f"{role} transaction workspace must share a filesystem with its target root")


def _create_transaction_workspaces(workspaces: dict[str, Path]) -> None:
    for workspace in workspaces.values():
        _assert_no_link_components(workspace.parent)
        workspace.mkdir(parents=True, exist_ok=False)
        _assert_no_link_components(workspace)


def _validate_journal_workspace(journal: Journal, skills_root: Path, codex_home: Path) -> tuple[Path, ...]:
    raw_workspaces = journal.data.get("transaction_workspaces")
    if raw_workspaces is not None:
        if not isinstance(raw_workspaces, dict) or set(raw_workspaces) != {"skills", "codex_home"}:
            raise RecoveryError("transaction journal workspace mapping is invalid")
        expected = _transaction_workspace_paths(
            str(journal.data.get("transaction_id", "")),
            skills_root,
            codex_home,
        )
        workspaces = {
            role: Path(str(raw_workspaces[role])).expanduser().resolve(strict=False)
            for role in expected
        }
        if workspaces != expected:
            raise RecoveryError("transaction journal workspace mapping does not match the requested roots")
        expected_roots = {
            "stage_roots": [workspaces["skills"] / "stage", workspaces["codex_home"] / "stage"],
            "rollback_roots": [workspaces["skills"] / "rollback", workspaces["codex_home"] / "rollback"],
        }
        for root_key, expected_values in expected_roots.items():
            values = journal.data.get(root_key, [])
            if not isinstance(values, list):
                raise RecoveryError(f"transaction journal {root_key} is invalid")
            resolved = [Path(str(value)).expanduser().resolve(strict=False) for value in values]
            if resolved != expected_values:
                raise RecoveryError(f"transaction journal {root_key} does not match its target-local workspaces")
        return tuple(dict.fromkeys(workspaces.values()))

    raw = journal.data.get("transaction_workspace")
    if raw is None:
        return ()
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
    return (workspace,)


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


def _canonical_overlap_marker(
    previous: dict[str, Any] | None,
    skills_root: Path,
    codex_home: Path,
    *,
    explicit_legacy_migration: bool,
) -> dict[str, Any] | None:
    """Classify the canonical nested layout without weakening v2 migration proof."""

    if not _legacy_overlap_layout(skills_root, codex_home):
        if explicit_legacy_migration:
            raise OwnershipError(
                "legacy-overlap migration is valid only for the canonical CodexHome/skills layout"
            )
        return None
    if previous is None:
        if explicit_legacy_migration:
            raise OwnershipError(
                "legacy-overlap migration requires an existing strict v2 ownership manifest"
            )
        return None
    version = previous.get("manifest_version")
    if version == 2:
        if not explicit_legacy_migration:
            raise OwnershipError(
                "an existing strict v2 CodexHome/skills install requires --legacy-overlap-migration"
            )
        return _legacy_overlap_manifest_marker(previous, skills_root, codex_home)
    if version != 3:
        raise OwnershipError("canonical nested install has an unsupported ownership manifest")

    targets = previous.get("targets")
    if not isinstance(targets, dict):
        raise OwnershipError("canonical nested v3 install has no target inventory")
    if Path(str(targets.get("skills_root", ""))).expanduser().resolve(strict=False) != skills_root:
        raise OwnershipError("canonical nested v3 SkillsRoot does not match the requested root")
    if Path(str(targets.get("support_root", ""))).expanduser().resolve(strict=False) != (
        codex_home / "coding-os"
    ).resolve(strict=False):
        raise OwnershipError("canonical nested v3 support root is outside CodexHome")
    marker = previous.get("legacy_overlap_migration")
    if marker is not None:
        return _legacy_overlap_manifest_marker(previous, skills_root, codex_home)
    if explicit_legacy_migration:
        raise OwnershipError(
            "legacy-overlap migration applies only to an existing strict v2 install"
        )
    return None


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


def _validate_legacy_v2_skill_descendants(
    previous: dict[str, Any] | None,
    previous_records: Sequence[dict[str, Any]],
    source_root: Path,
    pack: dict[str, Any],
) -> None:
    """Fail closed when v2 root-only ownership cannot prove a safe replacement."""
    def descendant_paths(root: Path) -> set[str]:
        paths = {entry["path"] for entry in _tree_entries(root)}
        if root.is_dir():
            for directory, _, _ in os.walk(root, followlinks=False):
                directory_path = Path(directory)
                if directory_path != root:
                    paths.add(unicodedata.normalize("NFC", directory_path.relative_to(root).as_posix()))
        return paths

    if previous is None or previous.get("manifest_version") != 2:
        return
    installation = pack["installation"]
    managed_skill_root = _safe_repo_path(source_root, str(installation["managed_skill_root"]))
    incoming_by_name = {
        str(record["name"]).casefold(): str(record["name"])
        for record in pack["bundled_skills"]
    }
    for record in previous_records:
        name = str(record["name"])
        incoming_name = incoming_by_name.get(name.casefold())
        if incoming_name is None:
            raise OwnershipError(
                f"legacy v2 skill is no longer bundled; refusing to delete unproven descendants: {name}"
            )
        source_skill = managed_skill_root / incoming_name
        if not source_skill.is_dir():
            raise BundleError(f"incoming managed skill is not a directory: {incoming_name}")
        live = Path(str(record["path"])).resolve(strict=False)
        if not live.exists():
            continue
        if not live.is_dir():
            raise OwnershipError(f"legacy v2 managed skill is not a directory: {name}")
        incoming_paths = descendant_paths(source_skill)
        unrecorded = sorted(
            descendant_paths(live) - incoming_paths,
            key=lambda value: value.encode("utf-8"),
        )
        if unrecorded:
            raise OwnershipError(
                f"legacy v2 skill contains an unrecorded descendant; refusing to replace {name}: {unrecorded[0]}"
            )


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
    expected_commit = str(options.expected_source_commit or "").lower()
    if not COMMIT_RE.fullmatch(expected_commit):
        raise SourceVerificationError("ExpectedSourceCommit must be one full 40-character lowercase Git commit")
    if options.archive_mode:
        if _universal_policy_mode(options) != "preserve":
            raise AuthorityError("archive mode cannot modify universal policy")
        return {
            "kind": "archive",
            "repo_root": str(source_root),
            "git_commit": expected_commit,
            "git_tree": None,
            "working_tree_clean": None,
            "bundle_manifest_sha256": bundle.manifest_sha256,
        }
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
    raw = unicodedata.normalize("NFC", str(value).strip()).replace("\\", "/")
    if not raw or len(raw) > 2048:
        raise SourceVerificationError("origin remote must be one bounded HTTPS or git@ repository URL")
    scp_match = re.fullmatch(r"(?:[^@/:\s]+@)?([^:/\s@]+):(.+)", raw)
    if scp_match and "://" not in raw:
        host, path = scp_match.groups()
        port = None
        if "?" in path or "#" in path:
            raise SourceVerificationError("origin remote repository URL must not include a query or fragment")
    else:
        try:
            parsed = urllib.parse.urlsplit(raw)
            if parsed.scheme.lower() != "https" or not parsed.netloc:
                raise SourceVerificationError("origin remote must be one normalized HTTPS or git@ repository URL")
            if parsed.query or parsed.fragment:
                raise SourceVerificationError("origin remote repository URL must not include a query or fragment")
            if parsed.password is not None:
                raise SourceVerificationError("origin remote HTTPS URL must not include a password")
            authority = parsed.netloc.rsplit("@", 1)[-1]
            host = parsed.hostname or ""
            path = parsed.path
            port = parsed.port
            if port not in {None, 443} or authority.endswith(":"):
                raise SourceVerificationError("origin remote HTTPS URL may use only port 443")
        except ValueError as exc:
            raise SourceVerificationError("origin remote URL is malformed") from exc
    host = host.lower().rstrip(".")
    path = urllib.parse.unquote(path).strip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    path = re.sub(r"/+", "/", path).casefold()
    parts = path.split("/") if path else []
    if not host or len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
        raise SourceVerificationError("origin remote repository path is invalid")
    return f"https://{host}/{path}"


def _validated_universal_bundle_id(value: str | None) -> str:
    bundle_id = str(value or "")
    if not UNIVERSAL_BUNDLE_ID_RE.fullmatch(bundle_id):
        raise AuthorityError(
            "UniversalBundleId must be a lowercase hyphenated identifier of 1 to 128 characters"
        )
    return bundle_id


def _validated_identifier(value: str | None, label: str) -> str:
    normalized = str(value or "").strip()
    if not IDENTIFIER_RE.fullmatch(normalized):
        raise AuthorityError(f"{label} must be one bounded canonical identifier")
    return normalized


def _campaign_state_db(codex_home: Path) -> Path:
    return codex_home / "coding-os-state" / "campaigns.sqlite3"


@contextlib.contextmanager
def _campaign_runtime_modules(
    runtime_root: Path | str, *, include_effects: bool = False
) -> Iterator[tuple[Any, ...]]:
    """Load the exact verified campaign runtime without retaining module aliases."""

    root = Path(runtime_root).expanduser().resolve(strict=True)
    prefixes = ("scripts", "scripts.agent", "scripts.agent.campaign_engine")
    saved = {
        name: module
        for name, module in list(sys.modules.items())
        if name in prefixes or name.startswith("scripts.agent.campaign_engine.")
    }
    saved_dont_write_bytecode = sys.dont_write_bytecode
    for name in saved:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(root))
    sys.dont_write_bytecode = True
    importlib.invalidate_caches()
    try:
        store_module = importlib.import_module("scripts.agent.campaign_engine.store")
        legacy_module = importlib.import_module("scripts.agent.campaign_engine.legacy")
        modules: list[tuple[str, Any]] = [
            ("CampaignStore", store_module),
            ("legacy archive", legacy_module),
        ]
        effects_module = None
        if include_effects:
            effects_module = importlib.import_module(
                "scripts.agent.campaign_engine.effects"
            )
            modules.append(("exact-file effect driver", effects_module))
        for label, module in modules:
            raw = getattr(module, "__file__", None)
            if not raw:
                raise TransactionError(f"{label} module has no direct file identity")
            module_path = Path(raw).resolve(strict=True)
            if not _path_is_within(module_path, root):
                raise TransactionError(f"{label} module escaped the verified runtime root")
            _assert_no_link_components(module_path, root)
        if include_effects:
            yield store_module, legacy_module, effects_module
        else:
            yield store_module, legacy_module
    except TransactionError:
        raise
    except Exception as exc:
        raise TransactionError(f"campaign runtime load failed: {exc}") from exc
    finally:
        sys.dont_write_bytecode = saved_dont_write_bytecode
        for name in list(sys.modules):
            if name in prefixes or name.startswith("scripts.agent.campaign_engine."):
                sys.modules.pop(name, None)
        try:
            sys.path.remove(str(root))
        except ValueError:
            pass
        sys.modules.update(saved)
        importlib.invalidate_caches()


def _check_universal_authority(
    options: InstallOptions,
    source: dict[str, Any],
    bundle: BundleInfo,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bundle_id = _validated_universal_bundle_id(options.universal_bundle_id)
    policy_mode = _universal_policy_mode(options)
    authority_inputs = (
        options.policy_authority_source,
        options.policy_authority_reference,
        options.publication_campaign_id,
        options.publication_node_id,
        options.publication_authority_epoch,
        options.publication_cancellation_epoch,
    )
    if policy_mode != "install":
        if any(value is not None for value in authority_inputs):
            raise AuthorityError(
                "policy authority inputs require --install-universal-policy"
            )
        if policy_mode == "preserve" and previous is not None:
            managed = _previous_managed_policy_targets(
                previous, Path(options.codex_home).expanduser().resolve(strict=False)
            )
            if any(managed):
                if managed != (True, True):
                    raise OwnershipError(
                        "previous universal policy ownership must manage both policy targets"
                    )
                previous_authority = previous.get("authority")
                if not isinstance(previous_authority, dict):
                    raise OwnershipError(
                        "previous managed universal policy authority is unavailable"
                    )
                return dict(previous_authority)
        return {
            "source": None,
            "reference": None,
            "effect_kind": None,
            "campaign": None,
            "universal_bundle": None,
        }

    authority_source = str(options.policy_authority_source or "")
    if authority_source not in {
        "explicit-user-approval",
        "campaign-publication-authority",
    }:
        raise AuthorityError(
            "PolicyAuthoritySource must be explicit-user-approval or "
            "campaign-publication-authority"
        )
    authority_reference = str(options.policy_authority_reference or "").strip()
    if not authority_reference or len(authority_reference) > 512:
        raise AuthorityError(
            "PolicyAuthorityReference must be a nonempty bounded authority reference"
        )

    campaign_fields = (
        options.publication_campaign_id,
        options.publication_node_id,
        options.publication_authority_epoch,
        options.publication_cancellation_epoch,
    )
    if authority_source == "explicit-user-approval":
        if any(value is not None for value in campaign_fields):
            raise AuthorityError(
                "explicit-user-approval must not include campaign authority fields"
            )
        return {
            "source": authority_source,
            "reference": authority_reference,
            "effect_kind": "EXACT_FILE_REPLACE",
            "campaign": None,
            "universal_bundle": bundle_id,
        }

    if source.get("kind") != "git":
        raise AuthorityError(
            "campaign publication authority requires a clean exact Git source"
        )
    campaign_id = _validated_identifier(
        options.publication_campaign_id, "PublicationCampaignId"
    )
    node_id = _validated_identifier(options.publication_node_id, "PublicationNodeId")
    authority_epoch = options.publication_authority_epoch
    cancellation_epoch = options.publication_cancellation_epoch
    if (
        isinstance(authority_epoch, bool)
        or not isinstance(authority_epoch, int)
        or authority_epoch < 1
    ):
        raise AuthorityError("PublicationAuthorityEpoch must be a positive integer")
    if (
        isinstance(cancellation_epoch, bool)
        or not isinstance(cancellation_epoch, int)
        or cancellation_epoch < 0
    ):
        raise AuthorityError(
            "PublicationCancellationEpoch must be a nonnegative integer"
        )

    source_root = Path(str(source.get("repo_root", ""))).resolve(strict=True)
    codex_home = Path(options.codex_home).expanduser().resolve(strict=False)
    with _campaign_runtime_modules(source_root) as (store_module, _):
        try:
            store = store_module.CampaignStore(_campaign_state_db(codex_home))
            verification = store.verify_publication_authority(
                campaign_id,
                "EXACT_FILE_REPLACE",
                authority_epoch=authority_epoch,
                cancellation_epoch=cancellation_epoch,
                node_id=node_id,
                candidate_head=str(source.get("git_commit") or ""),
            )
        except Exception as exc:
            raise AuthorityError(
                f"campaign publication authority verification failed: {exc}"
            ) from exc
    if (
        not isinstance(verification, dict)
        or verification.get("authorized") is not True
        or verification.get("campaign_id") != campaign_id
        or verification.get("node_id") != node_id
        or verification.get("candidate_head") != source.get("git_commit")
        or verification.get("effect_kind") != "EXACT_FILE_REPLACE"
        or verification.get("authority_epoch") != authority_epoch
        or verification.get("cancellation_epoch") != cancellation_epoch
    ):
        raise AuthorityError(
            "CampaignStore did not verify the exact publication authority tuple"
        )
    return {
        "source": authority_source,
        "reference": authority_reference,
        "effect_kind": "EXACT_FILE_REPLACE",
        "campaign": verification,
        "universal_bundle": bundle_id,
        "bundle_sha256": bundle.aggregate_sha256,
    }


def _campaign_hook_contract(pack: dict[str, Any]) -> dict[str, str] | None:
    raw = pack["installation"].get("campaign_hook")
    if raw is None:
        return None
    return {
        "source": _normalize_relative(str(raw["source"])),
        "target": _normalize_relative(str(raw["target"])),
    }


def _canonical_json_digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha_bytes(payload)


def _hooks_configuration_path(codex_home: Path) -> Path:
    return (codex_home / "hooks.json").resolve(strict=False)


def _load_hooks_document(raw: bytes | None) -> dict[str, Any]:
    if raw is None:
        return {"hooks": {}}
    try:
        parsed = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyMigrationError("hooks.json must be valid UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise PolicyMigrationError("hooks.json root must be one object")
    hooks = parsed.get("hooks")
    if hooks is None:
        parsed["hooks"] = {}
        hooks = parsed["hooks"]
    if not isinstance(hooks, dict):
        raise PolicyMigrationError("hooks.json hooks field must be one object")
    for event, groups in hooks.items():
        if not isinstance(event, str) or not isinstance(groups, list):
            raise PolicyMigrationError("hooks.json event inventories must be arrays")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise PolicyMigrationError("hooks.json hook groups must contain hook arrays")
            if any(not isinstance(command, dict) for command in group["hooks"]):
                raise PolicyMigrationError("hooks.json hook commands must be objects")
    return parsed


def _hook_command_points_to(command: dict[str, Any], suffix: str) -> bool:
    normalized_suffix = suffix.replace("\\", "/").casefold()
    for key in ("command", "commandWindows"):
        value = command.get(key)
        if isinstance(value, str) and normalized_suffix in value.replace("\\", "/").casefold():
            return True
    return False


def _legacy_lifecycle_hook(command: dict[str, Any]) -> bool:
    return _hook_command_points_to(command, LEGACY_LIFECYCLE_HOOK_SUFFIX)


def _campaign_hook_command(command: dict[str, Any]) -> bool:
    return _hook_command_points_to(
        command, "/hooks/campaign-engine/campaign_hook.py"
    )


def _strip_hook_commands(
    document: dict[str, Any], predicate: Callable[[dict[str, Any]], bool]
) -> list[dict[str, Any]]:
    removed: list[dict[str, Any]] = []
    hooks = document["hooks"]
    for event in list(hooks):
        replacement_groups: list[dict[str, Any]] = []
        event_removed = False
        for group in hooks[event]:
            replacement_commands: list[dict[str, Any]] = []
            for command in group["hooks"]:
                if predicate(command):
                    event_removed = True
                    removed.append(
                        {
                            "event": event,
                            "matcher": group.get("matcher"),
                            "digest": _canonical_json_digest(command),
                        }
                    )
                else:
                    replacement_commands.append(command)
            if replacement_commands:
                group["hooks"] = replacement_commands
                replacement_groups.append(group)
        if replacement_groups or not event_removed:
            hooks[event] = replacement_groups
        else:
            del hooks[event]
    return removed


def _campaign_hook_group(codex_home: Path) -> dict[str, Any]:
    hook = codex_home / "hooks" / "campaign-engine" / "campaign_hook.py"
    return {
        "matcher": CAMPAIGN_HOOK_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": (
                    f"{shlex.quote(sys.executable)} -B {shlex.quote(str(hook))}"
                ),
                "commandWindows": subprocess.list2cmdline(
                    [sys.executable, "-B", str(hook)]
                ),
                "timeout": 30,
                "statusMessage": CAMPAIGN_HOOK_STATUS,
            }
        ],
    }


def _campaign_hook_group_locations(document: dict[str, Any]) -> list[tuple[str, int]]:
    locations: list[tuple[str, int]] = []
    for event, groups in document["hooks"].items():
        for index, group in enumerate(groups):
            if any(_campaign_hook_command(command) for command in group["hooks"]):
                locations.append((event, index))
    return locations


def _previous_hooks_configuration(
    previous: dict[str, Any] | None, codex_home: Path
) -> dict[str, Any] | None:
    if previous is None or previous.get("manifest_version") != MANIFEST_VERSION:
        return None
    targets = previous.get("targets")
    if not isinstance(targets, dict):
        raise OwnershipError("previous install target inventory is invalid")
    record = targets.get("hooks_configuration")
    if record is None:
        return None
    if not isinstance(record, dict):
        raise OwnershipError("previous hooks.json ownership record is invalid")
    expected = _hooks_configuration_path(codex_home)
    actual = Path(str(record.get("path", ""))).expanduser().resolve(strict=False)
    owned = record.get("owned_entries")
    valid_entry = (
        isinstance(owned, list)
        and len(owned) == 1
        and isinstance(owned[0], dict)
        and owned[0].get("event") == CAMPAIGN_HOOK_EVENT
        and owned[0].get("matcher") == CAMPAIGN_HOOK_MATCHER
        and HASH_RE.fullmatch(str(owned[0].get("digest", ""))) is not None
    )
    preinstall_state = record.get("preinstall_state")
    preinstall_sha = record.get("preinstall_sha256")
    valid_preinstall = preinstall_state in {"present", "absent"} and (
        (preinstall_state == "absent" and preinstall_sha is None)
        or (preinstall_state == "present" and HASH_RE.fullmatch(str(preinstall_sha or "")) is not None)
    )
    if (
        record.get("managed") is not True
        or record.get("ownership_protocol") != HOOKS_CONFIGURATION_PROTOCOL
        or actual != expected
        or not valid_entry
        or not valid_preinstall
        or HASH_RE.fullmatch(str(record.get("installed_sha256", ""))) is None
        or HASH_RE.fullmatch(str(record.get("preserved_unrelated_digest", ""))) is None
    ):
        raise OwnershipError("previous hooks.json ownership identity is invalid")
    return record


def _remove_owned_campaign_hook(
    document: dict[str, Any], record: dict[str, Any]
) -> None:
    ownership = record["owned_entries"][0]
    event = str(ownership["event"])
    expected_digest = str(ownership["digest"])
    groups = document["hooks"].get(event, [])
    matching = [
        index
        for index, group in enumerate(groups)
        if _canonical_json_digest(group) == expected_digest
    ]
    if len(matching) != 1:
        raise OwnershipError(
            "managed campaign hook entry changed or duplicated since installation"
        )
    del groups[matching[0]]
    if not groups:
        del document["hooks"][event]


def _merge_retired_hook_evidence(
    previous: dict[str, Any] | None, removed: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    combined: list[dict[str, Any]] = []
    if previous is not None:
        prior = previous.get("retired_legacy_entries", [])
        if not isinstance(prior, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("event"), str)
            or HASH_RE.fullmatch(str(item.get("digest", ""))) is None
            for item in prior
        ):
            raise OwnershipError("previous legacy hook retirement evidence is invalid")
        combined.extend(dict(item) for item in prior)
    combined.extend(dict(item) for item in removed)
    unique = {
        (str(item["event"]), str(item["digest"])): item for item in combined
    }
    return [unique[key] for key in sorted(unique)]


def _prepare_install_hooks_configuration(
    previous: dict[str, Any] | None, codex_home: Path
) -> tuple[bytes, dict[str, Any]]:
    live = _hooks_configuration_path(codex_home)
    if live.exists():
        _assert_no_link_components(live, codex_home)
        if not live.is_file() or _is_link_or_reparse(live):
            raise OwnershipError("hooks.json cannot be a link, reparse point, or directory")
        raw: bytes | None = live.read_bytes()
    else:
        raw = None
    document = _load_hooks_document(raw)
    previous_record = _previous_hooks_configuration(previous, codex_home)
    if previous_record is not None:
        if raw is None:
            raise OwnershipError("previously managed hooks.json is missing")
        _remove_owned_campaign_hook(document, previous_record)
    elif _campaign_hook_group_locations(document):
        raise OwnershipError("unowned campaign engine hook entry already exists")

    removed_legacy = _strip_hook_commands(document, _legacy_lifecycle_hook)
    if _campaign_hook_group_locations(document):
        raise OwnershipError("duplicate or unowned campaign engine hook entry exists")
    unrelated_digest = _canonical_json_digest(document)
    owned_group = _campaign_hook_group(codex_home)
    document["hooks"].setdefault(CAMPAIGN_HOOK_EVENT, []).append(owned_group)
    installed = _json_bytes(document)
    if previous_record is None:
        preinstall_state = "present" if raw is not None else "absent"
        preinstall_sha = _sha_bytes(raw) if raw is not None else None
    else:
        preinstall_state = previous_record["preinstall_state"]
        preinstall_sha = previous_record["preinstall_sha256"]
    record = {
        "path": str(live),
        "managed": True,
        "ownership_protocol": HOOKS_CONFIGURATION_PROTOCOL,
        "preinstall_state": preinstall_state,
        "preinstall_sha256": preinstall_sha,
        "transaction_prior_state": "present" if raw is not None else "absent",
        "transaction_prior_sha256": _sha_bytes(raw) if raw is not None else None,
        "installed_sha256": _sha_bytes(installed),
        "preserved_unrelated_digest": unrelated_digest,
        "owned_entries": [
            {
                "event": CAMPAIGN_HOOK_EVENT,
                "matcher": CAMPAIGN_HOOK_MATCHER,
                "digest": _canonical_json_digest(owned_group),
            }
        ],
        "retired_legacy_entries": _merge_retired_hook_evidence(
            previous_record, removed_legacy
        ),
    }
    return installed, record


def _prepare_uninstall_hooks_configuration(
    manifest: dict[str, Any], codex_home: Path
) -> bytes | None:
    record = _previous_hooks_configuration(manifest, codex_home)
    if record is None:
        return None
    live = _hooks_configuration_path(codex_home)
    if not live.is_file() or _is_link_or_reparse(live):
        raise OwnershipError("managed hooks.json is unavailable for uninstall")
    _assert_no_link_components(live, codex_home)
    document = _load_hooks_document(live.read_bytes())
    _remove_owned_campaign_hook(document, record)
    _strip_hook_commands(document, _legacy_lifecycle_hook)
    if _campaign_hook_group_locations(document):
        raise OwnershipError("unowned campaign engine hook entry blocks uninstall")
    if record["preinstall_state"] == "absent" and document == {"hooks": {}}:
        return None
    return _json_bytes(document)


def _payload_layout(bundle: BundleInfo) -> dict[str, Any]:
    skill_prefix = _normalize_relative(
        str(bundle.pack["installation"]["managed_skill_root"])
    ) + "/"
    skill_entries = [
        str(entry["path"])
        for entry in bundle.entries
        if str(entry["path"]).startswith(skill_prefix)
    ]
    support_entries = [
        str(entry["path"])
        for entry in bundle.entries
        if not str(entry["path"]).startswith(skill_prefix)
    ]
    if not skill_entries or not support_entries:
        raise BundleError("verified bundle must contain both skill and support payloads")
    return {
        "skill_source_root": skill_prefix.rstrip("/"),
        "skill_entry_paths": skill_entries,
        "support_entry_paths": support_entries,
    }


def _verify_split_payload_layout(
    install_manifest: dict[str, Any], skills_root: Path, support_root: Path
) -> None:
    targets = install_manifest.get("targets")
    source = install_manifest.get("source")
    package = install_manifest.get("package")
    if not isinstance(targets, dict) or not isinstance(source, dict) or not isinstance(package, dict):
        raise OwnershipError("installed payload provenance is malformed")
    layout = targets.get("payload_layout")
    if not isinstance(layout, dict) or set(layout) != {
        "skill_source_root",
        "skill_entry_paths",
        "support_entry_paths",
    }:
        raise OwnershipError("installed split payload layout is malformed")
    skill_prefix = _normalize_relative(str(layout["skill_source_root"])) + "/"
    bundle_manifest_path = support_root / "install-bundle.manifest.json"
    bundle_manifest = _load_json(bundle_manifest_path, "installed bundle manifest")
    if (
        bundle_manifest.get("protocol") != BUNDLE_PROTOCOL
        or bundle_manifest.get("aggregate_sha256") != package.get("bundle_sha256")
        or source.get("bundle_manifest_sha256") != _sha_file(bundle_manifest_path)
    ):
        raise OwnershipError("installed bundle manifest provenance is invalid")
    entries = bundle_manifest.get("entries")
    if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
        raise OwnershipError("installed bundle entry inventory is malformed")
    skill_paths = [str(item["path"]) for item in entries if str(item.get("path", "")).startswith(skill_prefix)]
    support_paths = [str(item["path"]) for item in entries if not str(item.get("path", "")).startswith(skill_prefix)]
    if layout["skill_entry_paths"] != skill_paths or layout["support_entry_paths"] != support_paths:
        raise OwnershipError("installed split payload inventory differs from the verified bundle")
    for entry in entries:
        relative = _normalize_relative(str(entry.get("path", "")))
        if relative.startswith(skill_prefix):
            projected = relative[len(skill_prefix) :]
            target = skills_root.joinpath(*PurePosixPath(projected).parts)
        else:
            target = support_root.joinpath(*PurePosixPath(relative).parts)
        if (
            not target.is_file()
            or target.stat().st_size != entry.get("size")
            or _sha_file(target) != entry.get("sha256")
        ):
            raise OwnershipError(f"installed split payload entry differs: {relative}")


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


def _validate_runtime_pin(
    value: Any,
    *,
    source_commit: str | None = None,
    bundle_digest: str | None = None,
    install_transaction: str | None = None,
) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(RUNTIME_PIN_FIELDS):
        raise TransactionError("runtime_pin must contain exactly the six required fields")
    pin = {name: str(value.get(name, "")) for name in RUNTIME_PIN_FIELDS}
    if not COMMIT_RE.fullmatch(pin["source_commit"]):
        raise TransactionError("runtime pin source_commit is invalid")
    if not HASH_RE.fullmatch(pin["bundle_digest"]):
        raise TransactionError("runtime pin bundle_digest is invalid")
    if not re.fullmatch(r"[0-9a-f]{32}", pin["install_transaction"]):
        raise TransactionError("runtime pin install_transaction is invalid")
    expected_constants = {
        "protocol_version": CAMPAIGN_PROTOCOL,
        "schema_compatibility": SCHEMA_COMPATIBILITY,
        "host_capability_probe_version": HOST_CAPABILITY_PROBE_VERSION,
    }
    for name, expected in expected_constants.items():
        if pin[name] != expected:
            raise TransactionError(f"runtime pin {name} is incompatible")
    comparisons = {
        "source_commit": source_commit,
        "bundle_digest": bundle_digest,
        "install_transaction": install_transaction,
    }
    for name, expected in comparisons.items():
        if expected is not None and pin[name] != expected:
            raise TransactionError(f"runtime pin {name} does not match install provenance")
    return pin


def _stage_bundle(
    options: InstallOptions,
    source_root: Path,
    skills_root: Path,
    codex_home: Path,
    transaction_workspaces: dict[str, Path],
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
    skill_stage_root = transaction_workspaces["skills"] / "stage" / "skills"
    codex_stage_home = transaction_workspaces["codex_home"] / "stage" / "codex-home"
    support_stage = codex_stage_home / "coding-os"
    policy_stage = codex_stage_home / "policy"
    hook_stage: Path | None = None
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

    hook_contract = _campaign_hook_contract(bundle.pack)
    hook_target: dict[str, Any] = {
        "path": str((codex_home / "hooks" / "campaign-engine").resolve(strict=False)),
        "managed": False,
    }
    if hook_contract is not None:
        hook_source = _safe_repo_path(source_root, hook_contract["source"])
        hook_stage = codex_stage_home.joinpath(*PurePosixPath(hook_contract["target"]).parts)
        _copy_path(hook_source, hook_stage)
        hook_sha = _tree_hash(hook_stage)
        if hook_sha is None or hook_sha != _tree_hash(hook_source):
            raise TransactionError("staged campaign hook hash mismatch")
        hook_target = {
            "path": str(
                codex_home.joinpath(*PurePosixPath(hook_contract["target"]).parts).resolve(
                    strict=False
                )
            ),
            "managed": True,
            "source": hook_contract["source"],
            "sha256": hook_sha,
        }

    hooks_config_bytes, hooks_config_target = _prepare_install_hooks_configuration(
        previous, codex_home
    )
    staged_hooks_config = policy_stage / "hooks.json"
    _atomic_write_bytes(staged_hooks_config, hooks_config_bytes)

    generated_records: list[dict[str, Any]] = []

    global_agents_path = codex_home / "AGENTS.md"
    default_rules_path = codex_home / "rules" / "default.rules"
    global_target: dict[str, Any] = {"path": str(global_agents_path), "managed": False}
    rules_target: dict[str, Any] = {"path": str(default_rules_path), "managed": False}
    staged_agents: Path | None = None
    staged_rules: Path | None = None
    previous_agents_managed, previous_rules_managed = _previous_managed_policy_targets(previous, codex_home)
    policy_mode = _universal_policy_mode(options)
    if previous_agents_managed != previous_rules_managed:
        raise OwnershipError(
            "previous universal policy ownership must manage both policy targets"
        )
    if policy_mode == "install":
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
        rules_after = migrate_rules_bytes(rules_before, rules_source, codex_home)
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
    elif policy_mode == "remove":
        if previous_agents_managed:
            if not global_agents_path.is_file() or _is_link_or_reparse(global_agents_path):
                raise OwnershipError("previously managed AGENTS.md is unavailable for opt-out removal")
            staged_agents = policy_stage / "AGENTS.md"
            previous_agents = previous["targets"]["global_agents"]
            _atomic_write_bytes(
                staged_agents,
                _remove_recorded_policy_bytes(
                    global_agents_path.read_bytes(),
                    previous_agents,
                    allowed_markers=(
                        (AGENTS_START, AGENTS_END),
                        (RETIRED_AGENTS_START, RETIRED_AGENTS_END),
                    ),
                    label="AGENTS.md",
                ),
            )
        if previous_rules_managed:
            if not default_rules_path.is_file() or _is_link_or_reparse(default_rules_path):
                raise OwnershipError("previously managed default.rules is unavailable for opt-out removal")
            staged_rules = policy_stage / "rules" / "default.rules"
            previous_rules = previous["targets"]["default_rules"]
            _atomic_write_bytes(
                staged_rules,
                _remove_recorded_policy_bytes(
                    default_rules_path.read_bytes(),
                    previous_rules,
                    allowed_markers=(
                        (RULES_START, RULES_END),
                        (RETIRED_RULES_START, RETIRED_RULES_END),
                    ),
                    label="default.rules",
                ),
            )
    elif previous_agents_managed:
        if previous is None:
            raise OwnershipError("previous managed universal policy manifest is unavailable")
        global_target = dict(previous["targets"]["global_agents"])
        rules_target = dict(previous["targets"]["default_rules"])

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
    runtime_pin = {
        "source_commit": source["git_commit"],
        "bundle_digest": bundle.aggregate_sha256,
        "install_transaction": transaction_id,
        "protocol_version": CAMPAIGN_PROTOCOL,
        "schema_compatibility": SCHEMA_COMPATIBILITY,
        "host_capability_probe_version": HOST_CAPABILITY_PROBE_VERSION,
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
        "runtime_pin": runtime_pin,
        "authority": authority,
        "transaction": {"id": transaction_id, "committed_at": utc_now()},
        "targets": {
            "skills_root": str(skills_root),
            "support_root": str((codex_home / "coding-os").resolve(strict=False)),
            "managed_skills": managed_skills,
            "support_payload_sha256": support_payload_sha,
            "payload_layout": _payload_layout(bundle),
            "campaign_hook": hook_target,
            "hooks_configuration": hooks_config_target,
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
            "legacy_state": {"path": str(codex_home / "case-state"), "managed": False},
            "campaign_state": {
                "path": str(_campaign_state_db(codex_home)),
                "managed": False,
            },
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
        f"SourceCommit={runtime_pin['source_commit']}",
        f"InstallTransaction={runtime_pin['install_transaction']}",
        f"CampaignProtocol={runtime_pin['protocol_version']}",
        f"SchemaCompatibility={runtime_pin['schema_compatibility']}",
        f"HostCapabilityProbeVersion={runtime_pin['host_capability_probe_version']}",
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
        "hook_stage": hook_stage,
        "staged_hooks_config": staged_hooks_config,
        "hooks_config_target": hooks_config_target,
        "staged_agents": staged_agents,
        "staged_rules": staged_rules,
        "managed_skills": managed_skills,
        "support_payload_sha256": support_payload_sha,
        "support_tree_sha256": support_tree_sha,
        "install_manifest": install_manifest,
        "runtime_pin": runtime_pin,
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
    if previous is None or previous.get("manifest_version") != 3:
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
    try:
        _validate_runtime_pin(
            previous.get("runtime_pin"),
            source_commit=str(prior_source.get("git_commit") or ""),
            bundle_digest=str(package.get("bundle_sha256") or "") if isinstance(package, dict) else "",
            install_transaction=str(previous.get("transaction", {}).get("id") or "")
            if isinstance(previous.get("transaction"), dict)
            else "",
        )
    except TransactionError:
        return False
    policy_mode = _universal_policy_mode(options)
    if policy_mode in {"install", "preserve"} and prior_authority != authority:
        return False
    if policy_mode == "remove" and any(
        _previous_managed_policy_targets(previous, codex_home)
    ):
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
    previous_hook = _previous_campaign_hook(previous, codex_home)
    if staged["hook_stage"] is not None:
        live_hook = codex_home / "hooks" / "campaign-engine"
        if not live_hook.exists() or _tree_hash(live_hook) != _tree_hash(staged["hook_stage"]):
            return False
    elif previous_hook is not None:
        return False
    previous_hooks_config = _previous_hooks_configuration(previous, codex_home)
    live_hooks_config = _hooks_configuration_path(codex_home)
    if (
        previous_hooks_config is None
        or not live_hooks_config.is_file()
        or _sha_file(live_hooks_config)
        != staged["hooks_config_target"]["installed_sha256"]
    ):
        return False
    if policy_mode == "install":
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
    transaction_workspaces: dict[str, Path],
    previous_records: Sequence[dict[str, Any]],
    previous: dict[str, Any] | None,
    staged: dict[str, Any],
) -> list[dict[str, Any]]:
    skill_rollback = transaction_workspaces["skills"] / "rollback" / "skills"
    codex_rollback = transaction_workspaces["codex_home"] / "rollback" / "codex-home"
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
    previous_hook = _previous_campaign_hook(previous, codex_home)
    if staged["hook_stage"] is not None or previous_hook is not None:
        targets.append(
            _make_target(
                "campaign_hook",
                codex_home / "hooks" / "campaign-engine",
                staged["hook_stage"],
                codex_rollback / "hooks" / "campaign-engine",
            )
        )
    live_hooks_config = _hooks_configuration_path(codex_home)
    hooks_record = staged["hooks_config_target"]
    expected_present = hooks_record["transaction_prior_state"] == "present"
    if live_hooks_config.exists() != expected_present or (
        expected_present
        and _tree_hash(live_hooks_config)
        != hooks_record["transaction_prior_sha256"]
    ):
        raise RecoveryError("hooks.json drifted after migration staging")
    targets.append(
        _make_target(
            "hooks_configuration",
            live_hooks_config,
            staged["staged_hooks_config"],
            codex_rollback / "hooks.json",
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


def _assert_atomic_target_layout(
    targets: Sequence[dict[str, Any]],
    transaction_workspaces: dict[str, Path],
) -> None:
    for target in targets:
        live = Path(str(target["live_path"])).resolve(strict=False)
        staged = Path(str(target["staged_path"])).resolve(strict=False) if target.get("staged_path") else None
        rollback = Path(str(target["rollback_path"])).resolve(strict=False)
        role = "skills" if str(target["target_id"]).startswith("skill:") else "codex_home"
        workspace = transaction_workspaces[role]
        if not _path_is_within(rollback, workspace):
            raise TransactionError(f"rollback path escaped its target-local workspace: {target['target_id']}")
        if staged is not None and not _path_is_within(staged, workspace):
            raise TransactionError(f"staged path escaped its target-local workspace: {target['target_id']}")
        for candidate in transaction_workspaces.values():
            if _path_is_within(candidate, live) or _path_is_within(live, candidate):
                raise TransactionError(f"transaction workspace overlaps a live target: {target['target_id']}")

        live_parent_device = _device_id(live.parent)
        rollback_device = _device_id(rollback)
        if rollback_device != live_parent_device:
            raise TransactionError(f"rollback path is not on the live target filesystem: {target['target_id']}")
        if target["prior_state"] == "present" and _device_id(live) != rollback_device:
            raise TransactionError(f"existing live target cannot be atomically moved to rollback: {target['target_id']}")
        if staged is not None and _device_id(staged) != live_parent_device:
            raise TransactionError(f"staged path cannot be atomically promoted to live: {target['target_id']}")


def _verify_target_state(target: dict[str, Any], use_new: bool) -> bool:
    live = Path(target["live_path"])
    expected_state = "present" if (target["new_sha256"] is not None if use_new else target["prior_state"] == "present") else "absent"
    expected_hash = target["new_sha256"] if use_new else target["prior_sha256"]
    if expected_state == "absent":
        return not live.exists()
    return live.exists() and not _is_link_or_reparse(live) and _tree_hash(live) == expected_hash


def _promote_targets(
    journal: Journal,
    skills_root: Path,
    codex_home: Path,
    *,
    exact_file_driver: Any | None = None,
) -> None:
    targets = journal.data["targets"]
    count = len(targets)
    for index, target in enumerate(targets):
        live = Path(target["live_path"])
        staged = Path(target["staged_path"]) if target.get("staged_path") else None
        rollback = Path(target["rollback_path"])
        live.parent.mkdir(parents=True, exist_ok=True)
        rollback.parent.mkdir(parents=True, exist_ok=True)
        exact_file_replacement = (
            exact_file_driver is not None
            and target["prior_state"] == "present"
            and live.is_file()
            and staged is not None
            and staged.is_file()
        )
        if target["prior_state"] == "present":
            if not live.exists() or _tree_hash(live) != target["prior_sha256"]:
                raise RecoveryError(f"live target drifted before promotion: {target['target_id']}")
            if exact_file_replacement:
                _copy_path(live, rollback)
                if _tree_hash(rollback) != target["prior_sha256"]:
                    raise RecoveryError(
                        f"exact-file rollback copy failed verification: {target['target_id']}"
                    )
            else:
                os.replace(live, rollback)
        elif live.exists():
            raise OwnershipError(f"an unowned target appeared during promotion: {live}")
        target["step"] = "old_moved"
        journal.save()
        if staged is not None:
            if not staged.exists() or _tree_hash(staged) != target["new_sha256"]:
                raise RecoveryError(f"staged target drifted before promotion: {target['target_id']}")
            if exact_file_replacement:
                replacement = staged.read_bytes()
                receipt = exact_file_driver.replace(
                    operation_id=(
                        f"install:{journal.data['transaction_id']}:{index}"
                    ),
                    target=live,
                    expected_baseline_sha256=str(target["prior_sha256"]),
                    replacement=replacement,
                    expected_replacement_sha256=str(target["new_sha256"]),
                )
                if receipt.get("state") != "CONFIRMED":
                    raise RecoveryError(
                        f"exact-file effect did not confirm: {target['target_id']}"
                    )
                target["exact_file_effect"] = receipt
                staged.unlink()
            else:
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
    workspaces = _validate_journal_workspace(journal, skills_root, codex_home)
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
    for workspace in workspaces:
        if workspace.exists():
            _remove_owned_path(workspace)
    journal.data["status"] = "COMMITTED" if committed else journal.data.get("status", "ROLLED_BACK")
    journal.data["phase"] = "CLEANUP_COMPLETE"
    journal.save()


def _finalize_campaign_runtime(
    *,
    codex_home: Path,
    runtime_pin: dict[str, Any],
    legacy_state_root: str | None,
    legacy_source_digest: str | None,
) -> dict[str, Any]:
    pin = _validate_runtime_pin(runtime_pin)
    support_root = (codex_home / "coding-os").resolve(strict=True)
    state_root = (codex_home / "coding-os-state").resolve(strict=False)
    with _campaign_runtime_modules(support_root) as (store_module, legacy_module):
        try:
            store = store_module.CampaignStore(_campaign_state_db(codex_home))
            integrity = store.integrity_check()
            if (
                not isinstance(integrity, dict)
                or integrity.get("status") != "ok"
                or integrity.get("foreign_keys") != 1
                or integrity.get("journal_mode") != "wal"
                or integrity.get("synchronous") != 2
            ):
                raise TransactionError("CampaignStore startup integrity contract failed")
            store.record_runtime_installation(
                {"installation_id": pin["install_transaction"], **pin}
            )
            archive_receipt: dict[str, Any] | None = None
            if legacy_state_root is not None:
                current = legacy_module.inspect_legacy_root(legacy_state_root)
                if current.get("source_digest") != legacy_source_digest:
                    raise TransactionError(
                        "legacy source changed after the verified install preflight"
                    )
                result = legacy_module.archive_legacy_root(
                    legacy_state_root,
                    state_root=state_root,
                    store=store,
                )
                if result.source_digest != legacy_source_digest:
                    raise TransactionError(
                        "legacy archive digest differs from the verified preflight"
                    )
                archive_receipt = result.to_dict()
        except TransactionError:
            raise
        except Exception as exc:
            raise TransactionError(f"campaign runtime finalization failed: {exc}") from exc
    return {
        "state_db": str(_campaign_state_db(codex_home)),
        "integrity": integrity,
        "runtime_installation": pin,
        "legacy_archive": archive_receipt,
    }


def _finalize_journal_campaign_runtime(journal: Journal, codex_home: Path) -> None:
    if journal.data.get("operation") != "install" or not isinstance(
        journal.data.get("runtime_pin"), dict
    ):
        return
    if isinstance(journal.data.get("campaign_runtime_finalization"), dict):
        return
    receipt = _finalize_campaign_runtime(
        codex_home=codex_home,
        runtime_pin=journal.data["runtime_pin"],
        legacy_state_root=journal.data.get("legacy_state_root"),
        legacy_source_digest=journal.data.get("legacy_source_digest"),
    )
    journal.data["campaign_runtime_finalization"] = receipt
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
        _finalize_journal_campaign_runtime(journal, codex_home)
        journal.data["outcome"] = "committed_recovered"
        _retain_and_cleanup(journal, state_root, committed=True, skills_root=skills_root, codex_home=codex_home)
    else:
        _rollback_targets(journal, state_root)
        _retain_and_cleanup(journal, state_root, committed=False, skills_root=skills_root, codex_home=codex_home)


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _preflight_policy_sources(
    options: InstallOptions,
    source_root: Path,
    pack: dict[str, Any],
    codex_home: Path,
    previous: dict[str, Any] | None,
) -> None:
    policy_mode = _universal_policy_mode(options)
    previous_agents_managed, previous_rules_managed = _previous_managed_policy_targets(
        previous, codex_home
    )
    if previous_agents_managed != previous_rules_managed:
        raise OwnershipError(
            "previous universal policy ownership must manage both policy targets"
        )
    if policy_mode != "install":
        if not previous_agents_managed:
            return
        agents = codex_home / "AGENTS.md"
        rules = codex_home / "rules" / "default.rules"
        _assert_no_link_components(agents, codex_home)
        _assert_no_link_components(rules, codex_home)
        if not agents.is_file() or not rules.is_file():
            raise OwnershipError("previously managed universal policy target is unavailable")
        targets = previous["targets"]
        _validate_recorded_policy_bytes(
            agents.read_bytes(),
            targets["global_agents"],
            allowed_markers=(
                (AGENTS_START, AGENTS_END),
                (RETIRED_AGENTS_START, RETIRED_AGENTS_END),
            ),
            label="AGENTS.md",
        )
        _validate_recorded_policy_bytes(
            rules.read_bytes(),
            targets["default_rules"],
            allowed_markers=(
                (RULES_START, RULES_END),
                (RETIRED_RULES_START, RETIRED_RULES_END),
            ),
            label="default.rules",
        )
        return
    policies = pack["installation"]["universal_policy_sources"]
    agents_source = _safe_repo_path(source_root, str(policies["global_agents"])).read_bytes()
    rules_source = _safe_repo_path(source_root, str(policies["default_rules"])).read_bytes()
    agents = codex_home / "AGENTS.md"
    rules = codex_home / "rules" / "default.rules"
    if not agents.is_file() or not rules.is_file():
        raise PolicyMigrationError("universal policy migration requires existing AGENTS.md and default.rules")
    migrate_agents_bytes(agents.read_bytes(), agents_source)
    migrate_rules_bytes(rules.read_bytes(), rules_source, codex_home)


def _previous_managed_policy_targets(previous: dict[str, Any] | None, codex_home: Path) -> tuple[bool, bool]:
    if previous is None or previous.get("manifest_version") != 3:
        return False, False
    targets = previous.get("targets")
    if not isinstance(targets, dict):
        raise OwnershipError("previous v3 install policy inventory is invalid")

    def is_managed(
        name: str,
        path: Path,
        allowed_markers: Sequence[tuple[str, str]],
    ) -> bool:
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
        if (record.get("marker_start"), record.get("marker_end")) not in allowed_markers:
            raise OwnershipError(f"previous v3 install policy markers are invalid: {name}")
        return True

    return (
        is_managed(
            "global_agents",
            codex_home / "AGENTS.md",
            ((AGENTS_START, AGENTS_END), (RETIRED_AGENTS_START, RETIRED_AGENTS_END)),
        ),
        is_managed(
            "default_rules",
            codex_home / "rules" / "default.rules",
            ((RULES_START, RULES_END), (RETIRED_RULES_START, RETIRED_RULES_END)),
        ),
    )


def _previous_campaign_hook(
    previous: dict[str, Any] | None, codex_home: Path
) -> dict[str, Any] | None:
    if previous is None or previous.get("manifest_version") != MANIFEST_VERSION:
        return None
    targets = previous.get("targets")
    if not isinstance(targets, dict):
        raise OwnershipError("previous install target inventory is invalid")
    record = targets.get("campaign_hook")
    if record is None:
        return None
    if not isinstance(record, dict):
        raise OwnershipError("previous campaign hook ownership record is invalid")
    if record.get("managed") is False:
        return None
    expected = (codex_home / "hooks" / "campaign-engine").resolve(strict=False)
    actual = Path(str(record.get("path", ""))).resolve(strict=False)
    digest = str(record.get("sha256", ""))
    if record.get("managed") is not True or actual != expected or not HASH_RE.fullmatch(digest):
        raise OwnershipError("previous campaign hook ownership identity is invalid")
    return record


def _preflight_campaign_hook(
    pack: dict[str, Any],
    previous: dict[str, Any] | None,
    source_root: Path,
    codex_home: Path,
) -> None:
    contract = _campaign_hook_contract(pack)
    previous_record = _previous_campaign_hook(previous, codex_home)
    live = codex_home / "hooks" / "campaign-engine"
    if live.exists():
        _assert_no_link_components(live, codex_home)
        if _is_link_or_reparse(live):
            raise OwnershipError("campaign hook target cannot be a link or reparse point")
        if previous_record is None:
            raise OwnershipError("unowned campaign hook target already exists")
        if _tree_hash(live) != previous_record["sha256"]:
            raise OwnershipError("managed campaign hook changed since installation")
    elif previous_record is not None:
        raise OwnershipError("previously managed campaign hook is missing")
    if contract is not None:
        source = _safe_repo_path(source_root, contract["source"])
        if not source.is_dir() or _tree_hash(source) is None:
            raise BundleError("campaign hook source is unavailable")


def _legacy_archive_source(options: InstallOptions, codex_home: Path) -> str | None:
    if not options.archive_legacy_state:
        if options.legacy_state_root is not None:
            raise TransactionError(
                "LegacyStateRoot requires the explicit archive-legacy-state option"
            )
        return None
    expected = (codex_home / "case-state").resolve(strict=False)
    requested = Path(options.legacy_state_root or expected).expanduser().resolve(strict=False)
    if requested != expected:
        raise TransactionError("legacy state archive source must be exactly CodexHome/case-state")
    if not requested.is_dir() or _is_link_or_reparse(requested):
        raise TransactionError("requested legacy state archive source is unavailable")
    _assert_no_link_components(requested, codex_home)
    return str(requested)


def _inspect_legacy_archive_source(
    source_root: Path, legacy_state_root: str | None
) -> dict[str, Any] | None:
    if legacy_state_root is None:
        return None
    with _campaign_runtime_modules(source_root) as (_, legacy_module):
        try:
            inspection = legacy_module.inspect_legacy_root(legacy_state_root)
        except Exception as exc:
            raise TransactionError(f"legacy archive preflight failed: {exc}") from exc
    if (
        not isinstance(inspection, dict)
        or inspection.get("source_root") != legacy_state_root
        or not HASH_RE.fullmatch(str(inspection.get("source_digest", "")))
    ):
        raise TransactionError("legacy archive preflight returned invalid evidence")
    return inspection


def _install_preflight(
    options: InstallOptions,
    source_root: Path,
    skills_root: Path,
    codex_home: Path,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
    pack = _load_pack(source_root)
    names = [str(record["name"]) for record in pack["bundled_skills"]]
    previous = _load_previous_install(skills_root, codex_home)
    legacy_overlap_marker = _canonical_overlap_marker(
        previous,
        skills_root,
        codex_home,
        explicit_legacy_migration=options.legacy_overlap_migration,
    )
    previous_records = _validate_unowned_collisions(
        skills_root,
        codex_home / "coding-os",
        names,
        previous,
    )
    _validate_legacy_v2_skill_descendants(previous, previous_records, source_root, pack)
    _preflight_campaign_hook(pack, previous, source_root, codex_home)
    _prepare_install_hooks_configuration(previous, codex_home)
    _legacy_archive_source(options, codex_home)
    _preflight_policy_sources(options, source_root, pack, codex_home, previous)
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
    previous, _, _, _ = _install_preflight(
        options, source_root, skills_root, codex_home
    )
    bundle = verify_bundle(source_root, options.expected_bundle_sha256)
    source = _verify_source(options, source_root, bundle)
    authority = _check_universal_authority(options, source, bundle, previous)
    return {
        "status": "dry_run",
        "operation": "install",
        "bundle_sha256": bundle.aggregate_sha256,
        "source_commit": source.get("git_commit"),
        "policy_authority_source": authority.get("source"),
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
    if _legacy_overlap_layout(skills_root, codex_home):
        _canonical_overlap_marker(
            _load_previous_install(skills_root, codex_home),
            skills_root,
            codex_home,
            explicit_legacy_migration=options.legacy_overlap_migration,
        )
    transaction_id = uuid.uuid4().hex
    transaction_workspaces = _transaction_workspace_paths(transaction_id, skills_root, codex_home)
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
                "transaction_workspaces": {
                    role: str(path) for role, path in transaction_workspaces.items()
                },
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
                    str(transaction_workspaces["skills"] / "stage"),
                    str(transaction_workspaces["codex_home"] / "stage"),
                ],
                "rollback_roots": [
                    str(transaction_workspaces["skills"] / "rollback"),
                    str(transaction_workspaces["codex_home"] / "rollback"),
                ],
            },
        )
        journal.save()
        try:
            journal.phase("LOCK_ACQUIRED", skills_root, codex_home)
            previous, previous_records, _, legacy_overlap_marker = _install_preflight(
                options, source_root, skills_root, codex_home
            )
            journal.phase("PREFLIGHT_VERIFIED", skills_root, codex_home)
            bundle = verify_bundle(source_root, options.expected_bundle_sha256)
            source = _verify_source(options, source_root, bundle)
            legacy_state_root = _legacy_archive_source(options, codex_home)
            legacy_inspection = _inspect_legacy_archive_source(
                source_root, legacy_state_root
            )
            authority = _check_universal_authority(options, source, bundle, previous)
            journal.data["bundle_sha256"] = bundle.aggregate_sha256
            journal.data["source_commit"] = source.get("git_commit")
            journal.save()
            journal.phase("SOURCE_VERIFIED", skills_root, codex_home)
            _assert_workspace_devices(transaction_workspaces, skills_root, codex_home)
            _create_transaction_workspaces(transaction_workspaces)
            staged = _stage_bundle(
                options,
                source_root,
                skills_root,
                codex_home,
                transaction_workspaces,
                bundle,
                transaction_id,
                previous,
                legacy_overlap_marker,
                source,
                authority,
                config_snapshot,
            )
            journal.data["runtime_pin"] = staged["runtime_pin"]
            journal.data["legacy_state_root"] = legacy_state_root
            journal.data["legacy_source_digest"] = (
                legacy_inspection["source_digest"]
                if legacy_inspection is not None
                else None
            )
            journal.save()
            journal.phase("STAGE_VERIFIED", skills_root, codex_home)
            if _is_idempotent(options, previous, staged, source, authority, skills_root, codex_home):
                runtime_receipt = _finalize_campaign_runtime(
                    codex_home=codex_home,
                    runtime_pin=previous["runtime_pin"],
                    legacy_state_root=journal.data["legacy_state_root"],
                    legacy_source_digest=journal.data["legacy_source_digest"],
                )
                journal.data["status"] = "NOOP"
                journal.data["outcome"] = "already_committed"
                journal.data["campaign_runtime_finalization"] = runtime_receipt
                journal.save()
                _retain_and_cleanup(
                    journal, state_root, committed=False, skills_root=skills_root, codex_home=codex_home
                )
                return {
                    "status": "already_committed",
                    "operation": "install",
                    "bundle_sha256": bundle.aggregate_sha256,
                    "runtime_pin": previous["runtime_pin"],
                }
            targets = _prepare_install_targets(
                skills_root,
                codex_home,
                transaction_workspaces,
                previous_records,
                previous,
                staged,
            )
            _assert_atomic_target_layout(targets, transaction_workspaces)
            journal.data["targets"] = targets
            install_manifest_path = codex_home / "coding-os" / "install-manifest.json"
            pointer = {
                "protocol": TRANSACTION_PROTOCOL,
                "status": "committed",
                "transaction_id": transaction_id,
                "install_manifest_path": str(install_manifest_path),
                "install_manifest_sha256": staged["install_manifest_sha256"],
                "bundle_sha256": bundle.aggregate_sha256,
                "runtime_pin": staged["runtime_pin"],
            }
            pointer_bytes = _json_bytes(pointer)
            journal.data["new_pointer"] = pointer
            journal.data["new_pointer_sha256"] = _sha_bytes(pointer_bytes)
            journal.save()
            journal.phase("PROMOTION_PREPARED", skills_root, codex_home)
            journal.phase("PROMOTING", skills_root, codex_home)
            with _campaign_runtime_modules(
                source_root, include_effects=True
            ) as (_, _, effects_module):
                exact_file_driver = effects_module.ExactFileEffectDriver(
                    transaction_dir / "exact-file-effects"
                )
                _promote_targets(
                    journal,
                    skills_root,
                    codex_home,
                    exact_file_driver=exact_file_driver,
                )
            for target in targets:
                if not _verify_target_state(target, True):
                    raise RecoveryError(f"live target verification failed: {target['target_id']}")
            journal.phase("LIVE_TARGETS_VERIFIED", skills_root, codex_home)
            if not _same_file_snapshot(config_path, config_snapshot):
                raise TransactionError("config.toml changed during the transaction; it was not overwritten")
            live_manifest = codex_home / "coding-os" / "install-manifest.json"
            if _sha_file(live_manifest) != staged["install_manifest_sha256"]:
                raise RecoveryError("live install manifest hash differs from staged provenance")
            live_manifest_data = _load_json(live_manifest, "live install manifest")
            _validate_runtime_pin(
                live_manifest_data.get("runtime_pin"),
                source_commit=str(live_manifest_data.get("source", {}).get("git_commit") or "")
                if isinstance(live_manifest_data.get("source"), dict)
                else "",
                bundle_digest=bundle.aggregate_sha256,
                install_transaction=transaction_id,
            )
            _verify_split_payload_layout(
                live_manifest_data, skills_root, codex_home / "coding-os"
            )
            _atomic_write_bytes(state_root / "current.json", pointer_bytes)
            journal.phase("CURRENT_POINTER_COMMITTED", skills_root, codex_home)
            _finalize_journal_campaign_runtime(journal, codex_home)
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
                "runtime_pin": staged["runtime_pin"],
            }
        except Exception as exc:
            if _pointer_matches_new(journal, state_root):
                for target in journal.data.get("targets", []):
                    if not _verify_target_state(target, True):
                        journal.data["status"] = "RECOVERY_FAILED"
                        journal.data["recovery_error"] = f"post-pointer target mismatch: {target['target_id']}"
                        journal.save()
                        raise RecoveryError(journal.data["recovery_error"]) from exc
                _finalize_journal_campaign_runtime(journal, codex_home)
                journal.data["outcome"] = "committed_recovered"
                _retain_and_cleanup(
                    journal, state_root, committed=True, skills_root=skills_root, codex_home=codex_home
                )
                return {
                    "status": "committed_recovered",
                    "operation": "install",
                    "transaction_id": transaction_id,
                    "bundle_sha256": journal.data.get("bundle_sha256"),
                    "runtime_pin": journal.data.get("runtime_pin"),
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
    if _legacy_overlap_layout(skills_root, codex_home):
        marker = manifest.get("legacy_overlap_migration")
        if marker is not None:
            _legacy_overlap_manifest_marker(manifest, skills_root, codex_home)
        elif legacy_overlap_migration:
            raise OwnershipError(
                "legacy-overlap migration applies only to an existing strict v2 install"
            )
    package = manifest.get("package")
    source = manifest.get("source")
    transaction = manifest.get("transaction")
    if isinstance(package, dict) and isinstance(source, dict) and isinstance(transaction, dict):
        _validate_runtime_pin(
            manifest.get("runtime_pin"),
            source_commit=str(source.get("git_commit") or ""),
            bundle_digest=str(package.get("bundle_sha256") or ""),
            install_transaction=str(transaction.get("id") or ""),
        )
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
    _verify_split_payload_layout(manifest, skills_root, support_root)
    hook = _previous_campaign_hook(manifest, codex_home)
    if hook is not None:
        live_hook = Path(str(hook["path"]))
        if not live_hook.is_dir() or _tree_hash(live_hook) != hook["sha256"]:
            raise OwnershipError("managed campaign hook changed since install")
    if _previous_hooks_configuration(manifest, codex_home) is not None:
        _prepare_uninstall_hooks_configuration(manifest, codex_home)
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
        if _legacy_overlap_layout(skills_root, codex_home):
            raise OwnershipError(
                "strict v2 CodexHome/skills installs must be migrated to v3 before uninstall"
            )
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
    transaction_workspaces = _transaction_workspace_paths(transaction_id, skills_root, codex_home)
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
                "transaction_workspaces": {
                    role: str(path) for role, path in transaction_workspaces.items()
                },
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
                    str(transaction_workspaces["skills"] / "stage"),
                    str(transaction_workspaces["codex_home"] / "stage"),
                ],
                "rollback_roots": [
                    str(transaction_workspaces["skills"] / "rollback"),
                    str(transaction_workspaces["codex_home"] / "rollback"),
                ],
            },
        )
        journal.save()
        try:
            journal.phase("LOCK_ACQUIRED", skills_root, codex_home)
            if manifest.get("manifest_version") == 3:
                records, support_root = _validate_v3_for_uninstall(
                    manifest,
                    skills_root,
                    codex_home,
                    legacy_overlap_migration=options.legacy_overlap_migration,
                )
            else:
                if _legacy_overlap_layout(skills_root, codex_home):
                    raise OwnershipError(
                        "strict v2 CodexHome/skills installs must be migrated to v3 before uninstall"
                    )
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
            _assert_workspace_devices(transaction_workspaces, skills_root, codex_home)
            _create_transaction_workspaces(transaction_workspaces)
            skill_rollback = transaction_workspaces["skills"] / "rollback" / "skills"
            codex_rollback = transaction_workspaces["codex_home"] / "rollback" / "codex-home"
            codex_stage_home = transaction_workspaces["codex_home"] / "stage" / "codex-home"
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
                hook_record = _previous_campaign_hook(manifest, codex_home)
                if hook_record is not None:
                    live_hook = Path(str(hook_record["path"]))
                    targets.append(
                        _make_target(
                            "campaign_hook",
                            live_hook,
                            None,
                            codex_rollback / "hooks" / "campaign-engine",
                        )
                    )
                hooks_record = _previous_hooks_configuration(manifest, codex_home)
                if hooks_record is not None:
                    live_hooks = _hooks_configuration_path(codex_home)
                    hooks_baseline = _sha_file(live_hooks)
                    hooks_after = _prepare_uninstall_hooks_configuration(
                        manifest, codex_home
                    )
                    if _sha_file(live_hooks) != hooks_baseline:
                        raise RecoveryError(
                            "hooks.json drifted after uninstall migration staging"
                        )
                    staged_hooks: Path | None = None
                    if hooks_after is not None:
                        staged_hooks = codex_stage_home / "policy" / "hooks.json"
                        _atomic_write_bytes(staged_hooks, hooks_after)
                    targets.append(
                        _make_target(
                            "hooks_configuration",
                            live_hooks,
                            staged_hooks,
                            codex_rollback / "hooks.json",
                        )
                    )
                agents_record = target_info.get("global_agents", {})
                rules_record = target_info.get("default_rules", {})
                if isinstance(agents_record, dict) and agents_record.get("managed") is True:
                    live_agents = codex_home / "AGENTS.md"
                    staged_agents = codex_stage_home / "policy" / "AGENTS.md"
                    _atomic_write_bytes(
                        staged_agents,
                        _remove_recorded_policy_bytes(
                            live_agents.read_bytes(),
                            agents_record,
                            allowed_markers=(
                                (AGENTS_START, AGENTS_END),
                                (RETIRED_AGENTS_START, RETIRED_AGENTS_END),
                            ),
                            label="AGENTS.md",
                        ),
                    )
                    targets.append(_make_target("global_agents", live_agents, staged_agents, codex_rollback / "AGENTS.md"))
                if isinstance(rules_record, dict) and rules_record.get("managed") is True:
                    live_rules = codex_home / "rules" / "default.rules"
                    staged_rules = codex_stage_home / "policy" / "rules" / "default.rules"
                    _atomic_write_bytes(
                        staged_rules,
                        _remove_recorded_policy_bytes(
                            live_rules.read_bytes(),
                            rules_record,
                            allowed_markers=(
                                (RULES_START, RULES_END),
                                (RETIRED_RULES_START, RETIRED_RULES_END),
                            ),
                            label="default.rules",
                        ),
                    )
                    targets.append(_make_target("default_rules", live_rules, staged_rules, codex_rollback / "rules/default.rules"))
            for target in targets:
                if Path(target["live_path"]).resolve(strict=False) == codex_home:
                    raise TransactionError("transaction targets must never rename, move, or replace CodexHome itself")
            _assert_atomic_target_layout(targets, transaction_workspaces)
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
    install_parser.add_argument("--expected-source-commit", required=True)
    policy_action = install_parser.add_mutually_exclusive_group()
    policy_action.add_argument("--install-universal-policy", action="store_true")
    policy_action.add_argument("--remove-universal-policy", action="store_true")
    install_parser.add_argument("--universal-bundle-id", default=UNIVERSAL_BUNDLE_ID)
    install_parser.add_argument(
        "--policy-authority-source",
        choices=("explicit-user-approval", "campaign-publication-authority"),
    )
    install_parser.add_argument("--policy-authority-reference")
    install_parser.add_argument("--publication-campaign-id")
    install_parser.add_argument("--publication-node-id")
    install_parser.add_argument("--publication-authority-epoch", type=int)
    install_parser.add_argument("--publication-cancellation-epoch", type=int)
    install_parser.add_argument("--archive-legacy-state", action="store_true")
    install_parser.add_argument("--legacy-state-root")
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
                remove_universal_policy=args.remove_universal_policy,
                universal_bundle_id=args.universal_bundle_id,
                policy_authority_source=args.policy_authority_source,
                policy_authority_reference=args.policy_authority_reference,
                publication_campaign_id=args.publication_campaign_id,
                publication_node_id=args.publication_node_id,
                publication_authority_epoch=args.publication_authority_epoch,
                publication_cancellation_epoch=args.publication_cancellation_epoch,
                archive_legacy_state=args.archive_legacy_state,
                legacy_state_root=args.legacy_state_root,
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
