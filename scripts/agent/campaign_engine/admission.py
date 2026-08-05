"""Exact-source admission for the campaign engine.

Admission is deliberately independent from lifecycle transition authority.  It
collects and verifies facts, then returns immutable evidence for the reducer.
It never mutates Git, campaign state, or product files.
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit


RUNTIME_PROTOCOL_VERSION = "ccos-campaign-v1"
SCHEMA_COMPATIBILITY = "campaign-store-v1"
HOST_CAPABILITY_PROBE_VERSION = "native-bind-before-turn-scoped-tools-v3"
BUNDLE_PROTOCOL = "CCOS-INSTALL-BUNDLE-v1"
BUNDLE_DOMAIN = b"CCOS-INSTALL-BUNDLE-v1\0"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AdmissionError(RuntimeError):
    """A source, repository, scope, or runtime identity did not match."""


class GitCommandError(AdmissionError):
    pass


class ScopeOverlapError(AdmissionError):
    pass


class RuntimePinError(AdmissionError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_remote_url(raw: str) -> str:
    """Normalize Git remotes without retaining credentials.

    HTTPS, SSH URLs, and SCP-style Git URLs converge on
    ``host/owner/repository``.  Local filesystem remotes stay exact after path
    resolution.  This intentionally incorporates the useful part of the stale
    credential-normalization change without importing its lifecycle code.
    """

    value = str(raw or "").strip()
    if not value:
        raise AdmissionError("repository remote is empty")
    scp = re.fullmatch(r"(?:[^@/\s]+@)?([^:/\s]+):(.+)", value)
    if scp and "://" not in value and not re.match(r"^[A-Za-z]:[\\/]", value):
        host, remote_path = scp.groups()
        path = "/" + remote_path.replace("\\", "/").strip("/")
        if path.casefold().endswith(".git"):
            path = path[:-4]
        return f"https://{host.casefold()}{path}".rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme:
        if parsed.scheme.casefold() == "file":
            local = Path(parsed.path).expanduser().resolve(strict=False)
            return local.as_posix().casefold() if os.name == "nt" else local.as_posix()
        host = (parsed.hostname or "").casefold()
        if not host:
            raise AdmissionError("repository remote URL has no host")
        port = f":{parsed.port}" if parsed.port else ""
        path = parsed.path.replace("\\", "/").rstrip("/")
        if path.casefold().endswith(".git"):
            path = path[:-4]
        # Scheme and credentials are transport details, not repository identity.
        return urlunsplit(("https", host + port, path, "", ""))
    local = Path(value).expanduser().resolve(strict=False)
    return local.as_posix().casefold() if os.name == "nt" else local.as_posix()


def _resolved_text(path: Path, *, strict: bool = True) -> str:
    resolved = path.expanduser().resolve(strict=strict)
    text = str(resolved)
    return os.path.normcase(text) if os.name == "nt" else text


def _run(
    argv: Sequence[str], *, cwd: Path, timeout: float = 30.0
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitCommandError(f"command failed to execute: {argv[0]}: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise GitCommandError(
            f"command exited {result.returncode}: {' '.join(argv)}: {message[:1000]}"
        )
    return result


def run_git(root: Path, *args: str, timeout: float = 30.0) -> str:
    return _run(("git", *args), cwd=root, timeout=timeout).stdout.strip()


def _windows_file_identity(path: Path) -> tuple[int, int]:
    """Return volume serial and 64-bit file index without following a reparse."""

    from ctypes import wintypes

    FILE_READ_ATTRIBUTES = 0x80
    FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
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
    handle = create_file(
        str(path),
        FILE_READ_ATTRIBUTES,
        FILE_SHARE_ALL,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise AdmissionError(
            f"cannot open filesystem identity handle: {path}: {ctypes.get_last_error()}"
        )
    try:
        info = BY_HANDLE_FILE_INFORMATION()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise AdmissionError(
                f"cannot read filesystem identity: {path}: {ctypes.get_last_error()}"
            )
        index = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
        return int(info.dwVolumeSerialNumber), index
    finally:
        kernel32.CloseHandle(handle)


def filesystem_identity(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve(strict=True)
    metadata = os.lstat(resolved)
    if stat.S_ISLNK(metadata.st_mode) or resolved.is_symlink():
        raise AdmissionError(f"filesystem identity cannot be a symbolic link: {resolved}")
    if os.name == "nt":
        volume, index = _windows_file_identity(resolved)
        return {
            "kind": "windows-file-id",
            "volume_serial": volume,
            "file_index": index,
            "resolved_path": _resolved_text(resolved),
        }
    return {
        "kind": "posix-inode",
        "device": int(metadata.st_dev),
        "inode": int(metadata.st_ino),
        "resolved_path": _resolved_text(resolved),
    }


def normalize_allowed_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise AdmissionError(f"allowed path must be repository-relative: {value!r}")
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise AdmissionError(f"allowed path is not canonical: {value!r}")
    if path.parts[0].casefold() == ".git":
        raise AdmissionError(".git is never an allowed product path")
    return path.as_posix()


def _scope_prefix(value: str) -> tuple[str, ...]:
    normalized = normalize_allowed_path(value)
    result: list[str] = []
    for part in PurePosixPath(normalized).parts:
        wildcard = min(
            (index for token in "*?[" if (index := part.find(token)) >= 0),
            default=-1,
        )
        if wildcard >= 0:
            if wildcard:
                result.append(part[:wildcard].casefold())
            break
        result.append(part.casefold())
    return tuple(result)


def scopes_overlap(left: Iterable[str], right: Iterable[str]) -> bool:
    for first in left:
        a = _scope_prefix(first)
        for second in right:
            b = _scope_prefix(second)
            if not a or not b or a[: len(b)] == b or b[: len(a)] == a:
                return True
    return False


def assert_no_scope_overlap(
    proposed: Iterable[str], active_scopes: Iterable[Mapping[str, Any]]
) -> None:
    normalized = tuple(normalize_allowed_path(item) for item in proposed)
    for record in active_scopes:
        paths = record.get("allowed_paths")
        if isinstance(paths, str):
            paths = json.loads(paths)
        if not isinstance(paths, (list, tuple)):
            raise ScopeOverlapError("active scope record has no valid allowed_paths")
        if scopes_overlap(normalized, (str(item) for item in paths)):
            owner = record.get("campaign_id") or record.get("resource") or "unknown"
            raise ScopeOverlapError(f"allowed scope overlaps active campaign/resource: {owner}")


def _worktree_records(root: Path) -> list[dict[str, str]]:
    output = run_git(root, "worktree", "list", "--porcelain")
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines() + [""]:
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key in current:
            raise AdmissionError(f"duplicate worktree record field: {key}")
        current[key] = value
    return records


@dataclass(frozen=True, slots=True)
class RepositoryEvidence:
    git_root: str
    worktree: str
    git_directory: str
    common_directory: str
    filesystem_identity: Mapping[str, Any]
    remote: str
    normalized_remote: str
    branch: str
    base_sha: str
    head_sha: str
    tree_sha: str
    status_porcelain_sha256: str
    clean: bool
    mode: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_repository(
    *,
    git_root: str | Path,
    worktree: str | Path,
    expected_remote: str,
    expected_branch: str,
    expected_base_sha: str,
    mode: str,
) -> RepositoryEvidence:
    requested_root = Path(git_root).expanduser().resolve(strict=True)
    requested_worktree = Path(worktree).expanduser().resolve(strict=True)
    if mode not in {"MANUAL", "AUTOMATED"}:
        raise AdmissionError("mode must be MANUAL or AUTOMATED")
    if not SHA_RE.fullmatch(expected_base_sha):
        raise AdmissionError("base SHA must be one exact lowercase 40-character SHA")
    observed_root = Path(run_git(requested_worktree, "rev-parse", "--show-toplevel")).resolve(
        strict=True
    )
    if _resolved_text(observed_root) != _resolved_text(requested_root):
        raise AdmissionError("Git root differs from the approved exact root")
    if _resolved_text(requested_worktree) != _resolved_text(requested_root):
        raise AdmissionError("approved worktree must be the exact Git root")
    git_directory = Path(
        run_git(requested_root, "rev-parse", "--absolute-git-dir")
    ).resolve(strict=True)
    common_raw = run_git(requested_root, "rev-parse", "--git-common-dir")
    common_directory = Path(common_raw)
    if not common_directory.is_absolute():
        common_directory = requested_root / common_directory
    common_directory = common_directory.resolve(strict=True)
    if not git_directory.is_dir() or not common_directory.is_dir():
        raise AdmissionError("Git directory identity is not a real directory")
    matches = [
        record
        for record in _worktree_records(requested_root)
        if record.get("worktree")
        and _resolved_text(Path(record["worktree"]), strict=True)
        == _resolved_text(requested_root)
    ]
    if len(matches) != 1:
        raise AdmissionError("exact worktree is not registered exactly once")
    branch = run_git(requested_root, "branch", "--show-current")
    if branch != expected_branch or not branch:
        raise AdmissionError(f"branch mismatch: expected {expected_branch!r}, observed {branch!r}")
    head = run_git(requested_root, "rev-parse", "HEAD").casefold()
    if head != expected_base_sha:
        raise AdmissionError(f"base/head mismatch: expected {expected_base_sha}, observed {head}")
    tree = run_git(requested_root, "rev-parse", "HEAD^{tree}").casefold()
    remote = run_git(requested_root, "remote", "get-url", "origin")
    normalized_remote = normalize_remote_url(remote)
    if normalized_remote != normalize_remote_url(expected_remote):
        raise AdmissionError("origin remote differs from the approved repository")
    status_text = run_git(
        requested_root, "status", "--porcelain=v2", "--untracked-files=all"
    )
    clean = not status_text
    if mode == "AUTOMATED" and not clean:
        raise AdmissionError("automated admission requires an exactly clean worktree")
    evidence = RepositoryEvidence(
        git_root=str(requested_root),
        worktree=str(requested_worktree),
        git_directory=str(git_directory),
        common_directory=str(common_directory),
        filesystem_identity=filesystem_identity(requested_root),
        remote=remote,
        normalized_remote=normalized_remote,
        branch=branch,
        base_sha=expected_base_sha,
        head_sha=head,
        tree_sha=tree,
        status_porcelain_sha256=_sha256_bytes(status_text.encode("utf-8")),
        clean=clean,
        mode=mode,
    )
    return evidence


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimePinError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimePinError(f"{label} must be a JSON object")
    return value


def _aggregate_entries(entries: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update(BUNDLE_DOMAIN)
    for entry in sorted(entries, key=lambda item: str(item.get("path", "")).encode("utf-8")):
        path = normalize_allowed_path(str(entry.get("path", "")))
        size = entry.get("size")
        file_digest = str(entry.get("sha256", "")).casefold()
        if not isinstance(size, int) or size < 0 or not SHA256_RE.fullmatch(file_digest):
            raise RuntimePinError(f"invalid installed bundle entry: {path}")
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_digest))
    return digest.hexdigest()


def _installed_path(
    relative: str, *, support_root: Path, skills_root: Path
) -> Path:
    path = PurePosixPath(relative)
    parts = path.parts
    if len(parts) >= 4 and parts[:2] == (".agents", "skills"):
        return skills_root.joinpath(*parts[2:])
    return support_root.joinpath(*parts)


@dataclass(frozen=True, slots=True)
class RuntimePinEvidence:
    install_manifest: str
    source_commit: str
    bundle_digest: str
    install_transaction: str
    protocol_version: str
    schema_compatibility: str
    host_capability_probe_version: str
    bundle_manifest_sha256: str
    verified_file_count: int
    runtime_pin_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_installed_runtime(
    installed_root: str | Path,
    *,
    expected_source_commit: str,
    expected_bundle_digest: str,
    expected_install_transaction: str | None = None,
    expected_protocol_version: str = RUNTIME_PROTOCOL_VERSION,
    expected_schema_compatibility: str = SCHEMA_COMPATIBILITY,
    expected_host_capability_probe_version: str = HOST_CAPABILITY_PROBE_VERSION,
    expected_skills_root: str | Path | None = None,
) -> RuntimePinEvidence:
    root = Path(installed_root).expanduser().resolve(strict=True)
    manifest_path = root / "install-manifest.json"
    bundle_path = root / "install-bundle.manifest.json"
    if manifest_path.is_symlink() or bundle_path.is_symlink():
        raise RuntimePinError("installed runtime manifests must be exact local files")
    manifest = _load_json_object(manifest_path, "install manifest")
    bundle = _load_json_object(bundle_path, "installed bundle manifest")
    pin = manifest.get("runtime_pin")
    if not isinstance(pin, dict):
        raise RuntimePinError("install manifest has no complete runtime_pin")
    transaction = manifest.get("transaction")
    package = manifest.get("package")
    source = manifest.get("source")
    targets = manifest.get("targets")
    if not all(isinstance(item, dict) for item in (transaction, package, source, targets)):
        raise RuntimePinError("install manifest identity sections are malformed")
    expected_transaction = expected_install_transaction or str(transaction.get("id", ""))
    required = {
        "source_commit": expected_source_commit,
        "bundle_digest": expected_bundle_digest,
        "install_transaction": expected_transaction,
        "protocol_version": expected_protocol_version,
        "schema_compatibility": expected_schema_compatibility,
        "host_capability_probe_version": expected_host_capability_probe_version,
    }
    for key, expected in required.items():
        if pin.get(key) != expected:
            raise RuntimePinError(
                f"runtime pin field {key} mismatch: expected {expected!r}, observed {pin.get(key)!r}"
            )
    if (
        source.get("git_commit") != expected_source_commit
        or package.get("bundle_sha256") != expected_bundle_digest
        or transaction.get("id") != expected_transaction
    ):
        raise RuntimePinError("runtime pin conflicts with source, package, or transaction record")
    if bundle.get("protocol") != BUNDLE_PROTOCOL:
        raise RuntimePinError("installed bundle protocol mismatch")
    entries = bundle.get("entries")
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise RuntimePinError("installed bundle entries are malformed")
    aggregate = _aggregate_entries(entries)
    if aggregate != expected_bundle_digest or bundle.get("aggregate_sha256") != aggregate:
        raise RuntimePinError("installed bundle aggregate does not match the runtime pin")
    try:
        support_root = Path(str(targets.get("support_root", root))).resolve(strict=True)
    except OSError as exc:
        raise RuntimePinError("install manifest support_root is unavailable") from exc
    try:
        required_skills_root = Path(
            expected_skills_root if expected_skills_root is not None else root.parent / "skills"
        ).expanduser().resolve(strict=True)
    except OSError as exc:
        raise RuntimePinError("admitted skills_root is unavailable") from exc
    try:
        skills_root = Path(
            str(targets.get("skills_root", required_skills_root))
        ).expanduser().resolve(strict=True)
    except OSError as exc:
        raise RuntimePinError("install manifest skills_root is unavailable") from exc
    if support_root != root:
        raise RuntimePinError(
            "install manifest support_root differs from the admitted installed root"
        )
    if skills_root != required_skills_root:
        raise RuntimePinError(
            "install manifest skills_root differs from the admitted skills root"
        )
    for entry in entries:
        relative = str(entry["path"])
        path = _installed_path(relative, support_root=support_root, skills_root=skills_root)
        try:
            resolved_path = path.resolve(strict=True)
            expected_parent = (
                skills_root
                if PurePosixPath(relative).parts[:2] == (".agents", "skills")
                else support_root
            )
            resolved_path.relative_to(expected_parent)
            metadata = resolved_path.stat()
        except OSError as exc:
            raise RuntimePinError(f"installed bundle file is missing: {relative}") from exc
        except ValueError as exc:
            raise RuntimePinError(
                f"installed bundle file escapes its exact runtime root: {relative}"
            ) from exc
        if (
            path.is_symlink()
            or not resolved_path.is_file()
            or metadata.st_size != entry["size"]
            or file_sha256(resolved_path) != entry["sha256"]
        ):
            raise RuntimePinError(f"installed bundle file differs from its pin: {relative}")
    bundle_manifest_sha256 = file_sha256(bundle_path)
    recorded_manifest_hash = source.get("bundle_manifest_sha256")
    if recorded_manifest_hash != bundle_manifest_sha256:
        raise RuntimePinError("installed bundle manifest file differs from source pin")
    return RuntimePinEvidence(
        install_manifest=str(manifest_path),
        source_commit=expected_source_commit,
        bundle_digest=expected_bundle_digest,
        install_transaction=expected_transaction,
        protocol_version=expected_protocol_version,
        schema_compatibility=expected_schema_compatibility,
        host_capability_probe_version=expected_host_capability_probe_version,
        bundle_manifest_sha256=bundle_manifest_sha256,
        verified_file_count=len(entries),
        runtime_pin_sha256=_sha256_bytes(_canonical_json(pin)),
    )


def admit_campaign_spec(
    spec: Mapping[str, Any], *, installed_root: str | Path
) -> dict[str, Any]:
    """Verify the exact repository and installed runtime fields of one spec."""

    repository = spec.get("repository")
    if not isinstance(repository, Mapping):
        # CampaignSpec.to_dict uses flat immutable-contract fields.
        repository = {
            "remote": spec.get("repository_remote"),
            "git_root": spec.get("git_root"),
            "worktree": spec.get("worktree"),
            "branch": spec.get("branch"),
            "base_sha": spec.get("base_sha"),
        }
    repo_evidence = resolve_repository(
        git_root=str(repository.get("git_root", "")),
        worktree=str(repository.get("worktree", "")),
        expected_remote=str(repository.get("remote", "")),
        expected_branch=str(repository.get("branch", "")),
        expected_base_sha=str(repository.get("base_sha", "")),
        mode=str(spec.get("mode", "")),
    )
    install = spec.get("installed_runtime")
    if not isinstance(install, Mapping):
        install = {
            "source_commit": spec.get("installed_source_commit"),
            "bundle_digest": spec.get("installed_bundle_digest"),
            "install_transaction": spec.get("install_transaction"),
            "protocol_version": spec.get("protocol_version"),
            "schema_compatibility": spec.get("schema_compatibility", SCHEMA_COMPATIBILITY),
            "host_capability_probe_version": spec.get(
                "host_capability_probe_version", HOST_CAPABILITY_PROBE_VERSION
            ),
        }
    runtime_evidence = verify_installed_runtime(
        installed_root,
        expected_source_commit=str(install.get("source_commit", "")),
        expected_bundle_digest=str(install.get("bundle_digest", "")),
        expected_install_transaction=str(install.get("install_transaction", "")) or None,
        expected_protocol_version=str(install.get("protocol_version", RUNTIME_PROTOCOL_VERSION)),
        expected_schema_compatibility=str(
            install.get("schema_compatibility", SCHEMA_COMPATIBILITY)
        ),
        expected_host_capability_probe_version=str(
            install.get("host_capability_probe_version", HOST_CAPABILITY_PROBE_VERSION)
        ),
    )
    allowed_paths = spec.get("allowed_paths")
    if not isinstance(allowed_paths, (list, tuple)) or not allowed_paths:
        raise AdmissionError("campaign has no finite allowed_paths")
    normalized_paths = [normalize_allowed_path(str(item)) for item in allowed_paths]
    exact_worktree = Path(str(repository.get("worktree", ""))).expanduser().resolve(
        strict=True
    )
    commands = spec.get("required_validation_commands")
    if not isinstance(commands, (list, tuple)) or not commands:
        raise AdmissionError("campaign has no admitted validation commands")
    admitted_commands: list[dict[str, Any]] = []
    command_ids: set[str] = set()
    for raw_command in commands:
        if not isinstance(raw_command, Mapping):
            raise AdmissionError("validation command must be an object")
        command_id = str(raw_command.get("command_id", "")).strip()
        if not command_id or command_id in command_ids:
            raise AdmissionError(
                "validation command identifiers must be unique and nonempty"
            )
        command_ids.add(command_id)
        working = Path(
            str(raw_command.get("working_directory", ""))
        ).expanduser().resolve(strict=True)
        if os.path.normcase(str(working)) != os.path.normcase(str(exact_worktree)):
            raise AdmissionError(
                f"validation command {command_id} working_directory differs "
                "from the exact admitted worktree"
            )
        condition = str(raw_command.get("expected_worktree_condition", ""))
        if condition not in {"CLEAN", "EXACT_STATUS"}:
            raise AdmissionError(
                f"validation command {command_id} has a non-executable "
                "worktree condition"
            )
        executable_text = str(raw_command.get("executable", "")).strip()
        executable_path = Path(executable_text).expanduser()
        if executable_path.is_absolute() or executable_path.parent != Path("."):
            if not executable_path.is_absolute():
                executable_path = exact_worktree / executable_path
            try:
                executable_path = executable_path.resolve(strict=True)
            except OSError as exc:
                raise AdmissionError(
                    f"validation executable is unavailable: {executable_text}"
                ) from exc
        else:
            found = shutil.which(executable_text)
            if not found:
                raise AdmissionError(
                    f"validation executable is unavailable: {executable_text}"
                )
            executable_path = Path(found).resolve(strict=True)
        if not executable_path.is_file():
            raise AdmissionError(
                f"validation executable is not a regular file: {executable_path}"
            )
        admitted_commands.append(
            {
                "command_id": command_id,
                "executable": str(executable_path),
                "working_directory": str(working),
                "expected_worktree_condition": condition,
                "expected_status_sha256": raw_command.get("expected_status_sha256"),
            }
        )
    nodes = spec.get("nodes")
    if not isinstance(nodes, (list, tuple)) or not nodes:
        raise AdmissionError("campaign has no finite node graph")
    for raw_node in nodes:
        if not isinstance(raw_node, Mapping):
            raise AdmissionError("campaign node must be an object")
        node_id = str(raw_node.get("node_id", "")).strip()
        required = tuple(
            str(item) for item in raw_node.get("validation_command_ids", ())
        )
        if not required:
            raise AdmissionError(
                f"node {node_id or '<unknown>'} has no admitted validation command"
            )
        unknown = set(required) - command_ids
        if unknown:
            raise AdmissionError(
                f"node {node_id or '<unknown>'} references unadmitted validation "
                f"commands: {sorted(unknown)}"
            )
    publication_authority = spec.get("publication_authority")
    human_authorization = (
        publication_authority.get("human_authorization")
        if isinstance(publication_authority, Mapping)
        else None
    )
    if not isinstance(human_authorization, Mapping):
        raise AdmissionError(
            "campaign has no immutable human authorization signature verifier"
        )
    if human_authorization.get("algorithm") != "ED25519":
        raise AdmissionError("human authorization verifier must use ED25519")
    encoded_public_key = str(
        human_authorization.get("public_key_base64", "")
    )
    try:
        authorization_public_key = base64.b64decode(
            encoded_public_key, validate=True
        )
    except (ValueError, binascii.Error) as exc:
        raise AdmissionError(
            "human authorization public key is not canonical base64"
        ) from exc
    if (
        len(authorization_public_key) != 32
        or base64.b64encode(authorization_public_key).decode("ascii")
        != encoded_public_key
    ):
        raise AdmissionError(
            "human authorization ED25519 public key is not canonical"
        )
    authorization_verifier = {
        "algorithm": "ED25519",
        "public_key_sha256": _sha256_bytes(authorization_public_key),
    }
    return {
        "protocol_version": RUNTIME_PROTOCOL_VERSION,
        "repository": repo_evidence.to_dict(),
        "installed_runtime": runtime_evidence.to_dict(),
        "allowed_paths": normalized_paths,
        "validation_commands": admitted_commands,
        "human_authorization_verifier": authorization_verifier,
        "admission_sha256": _sha256_bytes(
            _canonical_json(
                {
                    "repository": repo_evidence.to_dict(),
                    "installed_runtime": runtime_evidence.to_dict(),
                    "allowed_paths": normalized_paths,
                    "validation_commands": admitted_commands,
                    "human_authorization_verifier": authorization_verifier,
                }
            )
        ),
    }
