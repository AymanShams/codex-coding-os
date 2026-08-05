"""Native Codex worker host with bind-before-turn authority.

The host creates an idle native App Server thread, verifies its returned
identity and execution boundary, durably binds that exact thread through a
caller-supplied binder, and only then permits its first turn.  A prompt, PID,
caller-declared role, or lease-shaped string is never authority by itself.
"""

from __future__ import annotations

import ctypes
import fnmatch
import hashlib
import json
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Protocol, Sequence

from .admission import HOST_CAPABILITY_PROBE_VERSION, normalize_allowed_path


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ROLES = frozenset(
    {
        "IMPLEMENTER",
        "REPAIRER",
        "REVIEWER",
        "CLOSURE_REVIEWER",
        "PARENT",
    }
)
READ_ONLY_ROLES = frozenset({"REVIEWER", "CLOSURE_REVIEWER", "PARENT"})
WRITE_ROLES = frozenset({"IMPLEMENTER", "REPAIRER"})
MAX_DYNAMIC_OUTPUT_BYTES = 1_000_000


class HostError(RuntimeError):
    pass


class HostProtocolError(HostError):
    pass


class HostAuthorityError(HostError):
    pass


class HostScopeError(HostError):
    pass


class LateResultError(HostError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _resolve_codex_executable(executable: str | Path) -> Path:
    requested = Path(str(executable))
    if requested.parent != Path("."):
        return requested.expanduser().resolve(strict=True)
    candidates: list[Path] = []
    configured = os.environ.get("CODEX_CAMPAIGN_HOST_EXECUTABLE")
    if configured:
        candidates.append(Path(configured).expanduser())
    if os.name == "nt":
        profile = Path(os.environ.get("USERPROFILE") or Path.home())
        candidates.extend(
            (
                profile / ".codex" / ".sandbox-bin" / "codex.exe",
                profile / ".codex" / "plugins" / ".plugin-appserver" / "codex.exe",
            )
        )
    discovered = shutil.which(str(executable)) or shutil.which("codex.exe")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    raise HostProtocolError("no executable Codex App Server host was found")


def _normalized_path(path: Path) -> str:
    text = str(path.resolve(strict=True))
    return os.path.normcase(text) if os.name == "nt" else text


def _require_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 256 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", text):
        raise HostAuthorityError(f"{label} is not one stable identifier")
    return text


def _require_epoch(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HostAuthorityError(f"{label} must be a non-negative integer")
    return value


def terminate_process_tree(process: subprocess.Popen[Any] | int) -> None:
    pid = process if isinstance(process, int) else process.pid
    poll = None if isinstance(process, int) else process.poll()
    if poll is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ("taskkill", "/PID", str(pid), "/T", "/F"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
    else:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if not isinstance(process, int):
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def process_identity(pid: int) -> dict[str, Any] | None:
    """Return a stable creation identity for one live process without mutation."""

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == "nt":
        from ctypes import wintypes

        process_query_limited_information = 0x1000

        class FileTime(ctypes.Structure):
            _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return None
        try:
            creation = FileTime()
            exit_time = FileTime()
            kernel = FileTime()
            user = FileTime()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                return None
            executable = os.path.normcase(
                str(Path(buffer.value).resolve(strict=False))
            )
            created = (int(creation.high) << 32) | int(creation.low)
            return {
                "pid": pid,
                "creation_token": str(created),
                "executable": executable,
            }
        finally:
            kernel32.CloseHandle(handle)
    proc = Path("/proc") / str(pid)
    try:
        stat_text = (proc / "stat").read_text(encoding="utf-8")
        close = stat_text.rfind(")")
        fields = stat_text[close + 2 :].split()
        creation_token = fields[19]
        executable = str((proc / "exe").resolve(strict=True))
    except (OSError, IndexError):
        return None
    return {
        "pid": pid,
        "creation_token": creation_token,
        "executable": executable,
    }


def terminate_verified_process_tree(
    pid: int, expected_identity: Mapping[str, Any] | None
) -> bool:
    """Terminate only when PID, creation token, and executable still match."""

    if not isinstance(expected_identity, Mapping):
        return False
    current = process_identity(pid)
    expected = {
        "pid": expected_identity.get("pid"),
        "creation_token": str(expected_identity.get("creation_token", "")),
        "executable": str(expected_identity.get("executable", "")),
    }
    if current is None or current != expected:
        return False
    terminate_process_tree(pid)
    return True


@dataclass(frozen=True, slots=True)
class ActorLease:
    lease_id: str
    request_id: str
    campaign_id: str
    node_id: str
    actor_id: str
    role: str
    worktree: str
    allowed_paths: tuple[str, ...]
    authority_epoch: int
    cancellation_epoch: int
    fencing_epoch: int
    candidate_head: str
    payload_digest: str

    @classmethod
    def issue(
        cls,
        *,
        lease_id: str,
        request_id: str,
        campaign_id: str,
        node_id: str,
        actor_id: str,
        role: str,
        worktree: str,
        allowed_paths: Sequence[str],
        authority_epoch: int,
        cancellation_epoch: int,
        fencing_epoch: int,
        candidate_head: str,
    ) -> "ActorLease":
        body = {
            "lease_id": _require_id(lease_id, "lease id"),
            "request_id": _require_id(request_id, "request id"),
            "campaign_id": _require_id(campaign_id, "campaign id"),
            "node_id": _require_id(node_id, "node id"),
            "actor_id": _require_id(actor_id, "actor id"),
            "role": str(role),
            "worktree": str(Path(worktree).resolve(strict=True)),
            "allowed_paths": tuple(normalize_allowed_path(item) for item in allowed_paths),
            "authority_epoch": _require_epoch(authority_epoch, "authority epoch"),
            "cancellation_epoch": _require_epoch(cancellation_epoch, "cancellation epoch"),
            "fencing_epoch": _require_epoch(fencing_epoch, "fencing epoch"),
            "candidate_head": str(candidate_head).casefold(),
        }
        if body["role"] not in ROLES:
            raise HostAuthorityError("actor role is not recognized")
        if not SHA_RE.fullmatch(body["candidate_head"]):
            raise HostAuthorityError("candidate head must be one exact Git SHA")
        if body["role"] in {"IMPLEMENTER", "REPAIRER"} and not body["allowed_paths"]:
            raise HostAuthorityError("write-capable actors require at least one allowed path")
        if body["role"] in READ_ONLY_ROLES and body["allowed_paths"]:
            raise HostAuthorityError("parent and reviewer leases must have no writable paths")
        return cls(**body, payload_digest=_digest(body))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ActorLease":
        supplied = str(raw.get("payload_digest", ""))
        issued = cls.issue(
            lease_id=str(raw.get("lease_id", "")),
            request_id=str(raw.get("request_id", "")),
            campaign_id=str(raw.get("campaign_id", "")),
            node_id=str(raw.get("node_id", "")),
            actor_id=str(raw.get("actor_id", "")),
            role=str(raw.get("role", "")),
            worktree=str(raw.get("worktree", "")),
            allowed_paths=tuple(raw.get("allowed_paths", ())),
            authority_epoch=raw.get("authority_epoch"),
            cancellation_epoch=raw.get("cancellation_epoch"),
            fencing_epoch=raw.get("fencing_epoch"),
            candidate_head=str(raw.get("candidate_head", "")),
        )
        if not SHA256_RE.fullmatch(supplied) or supplied != issued.payload_digest:
            raise HostAuthorityError("actor lease payload digest is invalid")
        return issued

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ActorBinding:
    lease: ActorLease
    native_thread_id: str
    native_source_digest: str
    native_cwd: str
    sandbox_type: str
    thread_created_idle: bool
    bound_before_turn: bool
    turn_id: str | None = None
    lease_consumed: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["lease"] = self.lease.to_dict()
        return value


@dataclass(frozen=True, slots=True)
class TerminalReceipt:
    protocol_version: str
    campaign_id: str
    node_id: str
    actor_id: str
    role: str
    lease_id: str
    native_thread_id: str
    native_turn_id: str
    authority_epoch: int
    cancellation_epoch: int
    fencing_epoch: int
    candidate_head: str
    candidate_tree: str | None
    turn_status: str
    output_digest: str
    action_count: int
    result_payload: Mapping[str, Any]
    receipt_digest: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _structured_result(items: Sequence[Any], turn: Mapping[str, Any]) -> dict[str, Any]:
    candidates: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        for key in ("text", "content", "message", "output"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
    for raw in reversed(candidates):
        text = raw
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {
        "status": str(turn.get("status", "unknown")),
        "structured": False,
        "message_digest": _digest(candidates[-1]) if candidates else _digest([]),
    }


class BindingAuthority(Protocol):
    def __call__(self, lease: ActorLease, native_identity: Mapping[str, Any]) -> None: ...


class ActionAuthority(Protocol):
    def __call__(
        self, lease: ActorLease, action: str, path: str | None
    ) -> Mapping[str, Any]: ...


class CurrentEpochs(Protocol):
    def __call__(self, campaign_id: str, node_id: str) -> Mapping[str, int]: ...


class ScopeGuard:
    def __init__(self, worktree: str | Path, allowed_paths: Sequence[str]) -> None:
        self.root = Path(worktree).resolve(strict=True)
        self.allowed = tuple(normalize_allowed_path(item) for item in allowed_paths)

    def relative(self, path: str | Path) -> str:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve(strict=False)
        try:
            relative = resolved.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise HostScopeError("worker path escapes the bound worktree") from exc
        if relative == ".git" or relative.startswith(".git/"):
            raise HostScopeError("worker cannot mutate Git administration files")
        return normalize_allowed_path(relative)

    def permits(self, path: str | Path) -> bool:
        relative = self.relative(path)
        for pattern in self.allowed:
            if fnmatch.fnmatchcase(relative, pattern):
                return True
            prefix = pattern.rstrip("/") + "/"
            if "*" not in pattern and "?" not in pattern and "[" not in pattern:
                if relative == pattern or relative.startswith(prefix):
                    return True
        return False

    def require(self, path: str | Path) -> str:
        relative = self.relative(path)
        if not self.permits(relative):
            raise HostScopeError(f"worker path is outside the one-use actor scope: {relative}")
        return relative

    def writable_roots(self) -> list[str]:
        roots: set[Path] = set()
        for pattern in self.allowed:
            parts = []
            for part in PurePosixPath(pattern).parts:
                if any(token in part for token in "*?["):
                    break
                parts.append(part)
            if not parts:
                raise HostScopeError("automated worker scope cannot start with a wildcard")
            path = self.root.joinpath(*parts)
            # An exact file is a valid sandbox writable root in current Codex.
            roots.add(path.resolve(strict=False))
        return [str(item) for item in sorted(roots, key=lambda value: str(value).casefold())]


def _dynamic_tool_specs(
    write_enabled: bool, *, denial_canary: bool = False
) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = [
        {
            "type": "function",
            "name": "campaign_list_files",
            "description": "List bounded repository files without changing anything.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "campaign_read_file",
            "description": "Read one UTF-8 repository file with a bounded output size.",
            "inputSchema": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "campaign_search",
            "description": "Search UTF-8 repository files with a bounded regular expression.",
            "inputSchema": {
                "type": "object",
                "required": ["pattern"],
                "properties": {
                    "pattern": {"type": "string", "maxLength": 500},
                    "path": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "campaign_git_status",
            "description": "Return exact Git HEAD and porcelain status without mutation.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "campaign_git_diff",
            "description": "Read a bounded no-color Git diff from an optional exact base SHA.",
            "inputSchema": {
                "type": "object",
                "properties": {"base": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    ]
    if write_enabled or denial_canary:
        tools.append(
            {
                "type": "function",
                "name": "campaign_apply_patch",
                "description": (
                    "Apply one UTF-8 unified Git patch. Every changed path is checked "
                    "against the immutable actor scope before any write."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["patch"],
                    "properties": {
                        "patch": {"type": "string", "maxLength": 1000000}
                    },
                    "additionalProperties": False,
                },
            }
        )
    if write_enabled:
        tools.append(
            {
                "type": "function",
                "name": "campaign_commit",
                "description": (
                    "Commit only the current actor-scoped changes after exact path and "
                    "live lease authorization checks."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["message"],
                    "properties": {
                        "message": {"type": "string", "minLength": 1, "maxLength": 500}
                    },
                    "additionalProperties": False,
                },
            }
        )
    return tools


def _safe_repository_path(root: Path, value: Any) -> Path:
    text = str(value or ".").replace("\\", "/")
    if PurePosixPath(text).is_absolute():
        raise HostScopeError("repository tool path must be relative")
    candidate = root.joinpath(*PurePosixPath(text).parts).resolve(strict=False)
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise HostScopeError("repository tool path escapes the bound worktree") from exc
    if relative == ".git" or relative.startswith(".git/"):
        raise HostScopeError("repository tools cannot access Git administration files")
    return candidate


def _git_read(root: Path, *arguments: str, input_text: str | None = None) -> str:
    input_arguments: dict[str, Any] = (
        {"input": input_text.encode("utf-8")}
        if input_text is not None
        else {"stdin": subprocess.DEVNULL}
    )
    completed = subprocess.run(
        ("git", *arguments),
        cwd=root,
        **input_arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=120,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    if completed.returncode:
        raise HostError(
            f"git {' '.join(arguments)} exited {completed.returncode}: "
            f"{(stderr or stdout)[:2000]}"
        )
    return stdout


def _frozen_candidate_identity(root: Path, expected_head: str) -> dict[str, str]:
    """Prove one clean worktree is still the exact frozen Git candidate."""

    expected = str(expected_head).casefold()
    if not SHA_RE.fullmatch(expected):
        raise HostAuthorityError("frozen candidate head must be one exact Git SHA")
    observed = _git_read(root, "rev-parse", "HEAD").strip().casefold()
    if observed != expected:
        raise HostAuthorityError(
            f"review worktree HEAD differs from frozen candidate: {observed} != {expected}"
        )
    tree = _git_read(root, "rev-parse", f"{expected}^{{tree}}").strip().casefold()
    if not SHA_RE.fullmatch(tree):
        raise HostAuthorityError("review worktree returned no exact candidate tree")
    status = _git_read(
        root, "status", "--porcelain=v1", "--untracked-files=all"
    )
    if status:
        raise HostAuthorityError("review worktree is not the clean frozen candidate")
    return {
        "head": observed,
        "tree": tree,
        "status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
    }


def _patch_paths(patch: str) -> tuple[str, ...]:
    if not patch.strip() or len(patch.encode("utf-8")) > MAX_DYNAMIC_OUTPUT_BYTES:
        raise HostScopeError("campaign patch is empty or exceeds the bounded size")
    forbidden_metadata = (
        "GIT binary patch",
        "Binary files ",
        "rename from ",
        "rename to ",
        "old mode ",
        "new mode ",
        "new file mode 120000",
    )
    paths: list[str] = []
    headers: list[tuple[str, str]] = []
    file_headers: list[tuple[str, str]] = []
    pending_old: str | None = None
    current_diff = False
    current_file_header = False
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            if current_diff and (pending_old is not None or not current_file_header):
                raise HostScopeError("campaign patch file headers are incomplete")
            try:
                parts = shlex.split(line)
            except ValueError as exc:
                raise HostScopeError("campaign patch has an invalid diff header") from exc
            if len(parts) != 4 or not parts[2].startswith("a/") or not parts[3].startswith("b/"):
                raise HostScopeError("campaign patch must use canonical Git diff headers")
            old_path = parts[2][2:]
            new_path = parts[3][2:]
            if old_path != new_path:
                raise HostScopeError("campaign patch cannot rename paths")
            normalized = normalize_allowed_path(new_path)
            paths.append(normalized)
            headers.append((f"a/{normalized}", f"b/{normalized}"))
            pending_old = None
            current_diff = True
            current_file_header = False
            in_hunk = False
            continue
        if not current_diff:
            continue
        if in_hunk:
            # Hunk content is product text. A removed line whose original text
            # starts with two dashes is encoded as ``--- ...`` and must never
            # be mistaken for a Git file header.
            continue
        if line.startswith("@@"):
            if pending_old is not None or not current_file_header:
                raise HostScopeError("campaign patch file headers are incomplete")
            in_hunk = True
            continue
        if any(marker in line for marker in forbidden_metadata):
            raise HostScopeError("binary, rename, mode, and symbolic-link patches are denied")
        if line.startswith("--- "):
            if pending_old is not None or current_file_header:
                raise HostScopeError("campaign patch contains an incomplete file header")
            pending_old = line[4:].split("\t", 1)[0]
            continue
        if line.startswith("+++ "):
            if pending_old is None:
                raise HostScopeError("campaign patch contains an unmatched new-file header")
            file_headers.append((pending_old, line[4:].split("\t", 1)[0]))
            pending_old = None
            current_file_header = True
            continue
    if (
        pending_old is not None
        or (current_diff and not current_file_header)
        or len(file_headers) != len(headers)
    ):
        raise HostScopeError("campaign patch file headers are incomplete")
    for index, (old_header, new_header) in enumerate(file_headers):
        expected_old, expected_new = headers[index]
        valid_pair = (old_header, new_header) in {
            (expected_old, expected_new),
            ("/dev/null", expected_new),
            (expected_old, "/dev/null"),
        }
        if not valid_pair:
            raise HostScopeError("campaign patch file headers differ from the scoped diff path")
    if not paths or len(paths) != len(set(paths)):
        raise HostScopeError("campaign patch paths must be nonempty and unique")
    return tuple(paths)


class AppServerTransport:
    """Minimal JSONL transport for one pinned Codex App Server process."""

    def __init__(
        self,
        executable: str | Path,
        *,
        cwd: str | Path,
        timeout: float = 300,
        environment: Mapping[str, str] | None = None,
        dynamic_tool_handler: Callable[[Mapping[str, Any]], Mapping[str, Any]]
        | None = None,
    ) -> None:
        self.executable = _resolve_codex_executable(executable)
        self.cwd = Path(cwd).resolve(strict=True)
        self.timeout = timeout
        self.environment = dict(environment or os.environ)
        self.dynamic_tool_handler = dynamic_tool_handler
        self.command = [
            str(self.executable),
            "--strict-config",
            "--disable",
            "apps",
            "--disable",
            "plugins",
            "--disable",
            "remote_plugin",
            "--disable",
            "code_mode",
            "--disable",
            "code_mode_host",
            "--disable",
            "multi_agent",
            "--disable",
            "browser_use",
            "--disable",
            "in_app_browser",
            "--disable",
            "computer_use",
            "--disable",
            "image_generation",
            "--disable",
            "goals",
            "--disable",
            "memories",
            "--disable",
            "hooks",
            "-c",
            "mcp_servers={}",
            "-c",
            'web_search="disabled"',
            "app-server",
            "--listen",
            "stdio://",
        ]
        self.process: subprocess.Popen[str] | None = None
        self.inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.responses: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.next_request_id = 1
        self.reader: threading.Thread | None = None
        self.stderr_reader: threading.Thread | None = None
        self.stderr_tail: list[str] = []
        self._diagnostic_lock = threading.Lock()

    def __enter__(self) -> "AppServerTransport":
        self.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def start(self) -> None:
        if self.process is not None:
            raise HostProtocolError("App Server transport is already running")
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200) if os.name == "nt" else 0
        self.process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            env=self.environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=flags,
            start_new_session=os.name != "nt",
        )
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        self.stderr_reader = threading.Thread(
            target=self._read_stderr_loop, daemon=True
        )
        self.stderr_reader.start()

    def _read_loop(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                value = {"method": "campaign/nonJson", "params": {"line": line[:1000]}}
            self.inbox.put(value if isinstance(value, dict) else {"method": "campaign/nonObject"})
        self.inbox.put(None)

    def _read_stderr_loop(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        for line in self.process.stderr:
            bounded = line.rstrip("\r\n")[:1000]
            with self._diagnostic_lock:
                self.stderr_tail.append(bounded)
                del self.stderr_tail[:-40]

    def diagnostic_snapshot(self) -> dict[str, Any]:
        """Return bounded protocol facts without including event payload content."""

        methods: list[str] = []
        counts: dict[str, int] = {}
        for event in self.events:
            method = str(event.get("method", "response"))
            counts[method] = counts.get(method, 0) + 1
            methods.append(method)
        with self._diagnostic_lock:
            stderr = "\n".join(self.stderr_tail)[-4000:]
        return {
            "event_count": len(self.events),
            "event_counts": counts,
            "recent_event_methods": methods[-40:],
            "pending_response_ids": sorted(self.responses)[-20:],
            "process_returncode": (
                self.process.poll() if self.process is not None else None
            ),
            "stderr_tail": stderr,
        }

    def _write(self, value: Mapping[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise HostProtocolError("App Server stdin is unavailable")
        self.process.stdin.write(json.dumps(value, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": dict(params or {})})

    def _pump(self, timeout: float) -> dict[str, Any] | None:
        try:
            message = self.inbox.get(timeout=max(timeout, 0.001))
        except queue.Empty:
            return None
        if message is None:
            diagnostics = json.dumps(
                self.diagnostic_snapshot(), sort_keys=True, separators=(",", ":")
            )
            raise HostProtocolError(
                f"App Server closed its protocol stream: {diagnostics}"
            )
        if "method" not in message:
            self.responses[str(message.get("id"))] = message
            return message
        if "id" in message:
            method = str(message.get("method"))
            # Authority never depends on a model approval request. All host-side
            # mutation escalation is denied. Workspace writes must fit the
            # already-sealed sandbox roots.
            if method == "item/tool/call":
                params = message.get("params")
                try:
                    if self.dynamic_tool_handler is None or not isinstance(
                        params, Mapping
                    ):
                        raise HostAuthorityError(
                            "campaign dynamic tool handler is unavailable"
                        )
                    output = dict(self.dynamic_tool_handler(params))
                    result = {
                        "success": True,
                        "contentItems": [
                            {
                                "type": "inputText",
                                "text": json.dumps(
                                    output,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                    }
                except Exception as exc:
                    result = {
                        "success": False,
                        "contentItems": [
                            {
                                "type": "inputText",
                                "text": f"{type(exc).__name__}: {exc}",
                            }
                        ],
                    }
                response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
            elif method.endswith("requestApproval"):
                result: Any = {"decision": "denied"}
                response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {"code": -32000, "message": "campaign host denied server request"},
                }
            self._write(response)
        self.events.append(message)
        return message

    def request(
        self, method: str, params: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> dict[str, Any]:
        request_id = str(self.next_request_id)
        self.next_request_id += 1
        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": dict(params or {}),
            }
        )
        deadline = time.monotonic() + (timeout or self.timeout)
        while request_id not in self.responses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HostProtocolError(f"App Server request timed out: {method}")
            self._pump(min(remaining, 0.5))
        response = self.responses.pop(request_id)
        if "error" in response:
            raise HostProtocolError(f"App Server rejected {method}: {response['error']}")
        result = response.get("result")
        if result is None:
            return {}
        if not isinstance(result, Mapping):
            raise HostProtocolError(f"App Server returned a non-object for {method}")
        return dict(result)

    def wait_turn(self, thread_id: str, turn_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        def match(message: Mapping[str, Any]) -> dict[str, Any] | None:
            if message.get("method") != "turn/completed":
                return None
            params = message.get("params")
            if not isinstance(params, Mapping) or params.get("threadId") != thread_id:
                return None
            turn = params.get("turn")
            if not isinstance(turn, Mapping) or turn.get("id") != turn_id:
                return None
            return dict(turn)

        for event in self.events:
            found = match(event)
            if found is not None:
                return found
        deadline = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < deadline:
            event = self._pump(min(0.5, deadline - time.monotonic()))
            if event is not None:
                found = match(event)
                if found is not None:
                    return found
        diagnostics = json.dumps(
            self.diagnostic_snapshot(), sort_keys=True, separators=(",", ":")
        )
        raise HostProtocolError(
            f"native turn timed out: {thread_id}:{turn_id}; diagnostics={diagnostics}"
        )

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            terminate_process_tree(process)
        if self.reader is not None:
            self.reader.join(timeout=5)
        if self.stderr_reader is not None:
            self.stderr_reader.join(timeout=5)


class NativeCodexHost:
    def __init__(
        self,
        *,
        executable: str | Path = "codex",
        model: str = "gpt-5.6-sol",
        reasoning_effort: str = "max",
        transport_factory: Callable[..., Any] = AppServerTransport,
        probe_write_denial_canary: bool = False,
    ) -> None:
        self.executable = executable
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.transport_factory = transport_factory
        self.probe_write_denial_canary = bool(probe_write_denial_canary)
        self._bindings: dict[str, ActorBinding] = {}
        self._transports: dict[str, Any] = {}
        self._action_authorities: dict[str, ActionAuthority] = {}
        self._review_candidate_identities: dict[str, Mapping[str, str]] = {}
        self._action_locks: dict[str, threading.RLock] = {}
        self._interrupted: set[str] = set()
        self._denied_write_attempts: dict[str, int] = {}
        self._host_lock = threading.RLock()

    @staticmethod
    def _sandbox_for(lease: ActorLease) -> tuple[str, Mapping[str, Any], list[str]]:
        del lease
        # Native workers always run in a read-only OS sandbox. Implementer and
        # repairer writes cross one narrow client-side dynamic-tool boundary
        # that checks the live lease, fence, exact root, and every target path.
        return "read-only", {"type": "readOnly", "networkAccess": False}, []

    def _initialize(self, transport: Any) -> dict[str, Any]:
        result = transport.request(
            "initialize",
            {
                "clientInfo": {"name": "codex-campaign-engine", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        transport.notify("initialized", {})
        return result

    def _require_review_candidate(self, binding: ActorBinding) -> Mapping[str, str] | None:
        if binding.lease.role not in {"REVIEWER", "CLOSURE_REVIEWER"}:
            return None
        expected = self._review_candidate_identities.get(binding.lease.lease_id)
        if expected is None:
            raise HostAuthorityError("reviewer has no frozen candidate identity")
        observed = _frozen_candidate_identity(
            Path(binding.lease.worktree).resolve(strict=True),
            binding.lease.candidate_head,
        )
        if dict(observed) != dict(expected):
            raise HostAuthorityError("review candidate tree changed after native binding")
        return observed

    def _require_live_action(
        self,
        lease: ActorLease,
        authority: ActionAuthority,
        action: str,
        paths: Sequence[str],
    ) -> None:
        with self._host_lock:
            if lease.lease_id in self._interrupted:
                raise HostAuthorityError("worker action was interrupted before its commit point")
        for path in paths:
            authority(lease, action, path)
        with self._host_lock:
            if lease.lease_id in self._interrupted:
                raise HostAuthorityError("worker action was interrupted before its commit point")

    @staticmethod
    def _rollback_patch(root: Path, patch: str) -> None:
        try:
            _git_read(
                root,
                "apply",
                "--reverse",
                "--check",
                "--whitespace=nowarn",
                "-",
                input_text=patch,
            )
            _git_read(
                root,
                "apply",
                "--reverse",
                "--whitespace=nowarn",
                "-",
                input_text=patch,
            )
        except BaseException as exc:
            raise HostError(
                "cancelled or stale campaign patch could not be rolled back exactly"
            ) from exc

    def _handle_dynamic_tool(
        self, lease: ActorLease, params: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        binding = self._bindings.get(lease.lease_id)
        if binding is None:
            raise HostAuthorityError("dynamic tool call preceded native actor binding")
        if params.get("threadId") != binding.native_thread_id:
            raise HostAuthorityError("dynamic tool thread differs from the actor binding")
        observed_turn_id = _require_id(params.get("turnId"), "dynamic tool turn id")
        if binding.turn_id is None:
            if not binding.lease_consumed:
                raise HostAuthorityError(
                    "dynamic tool call arrived before actor turn dispatch"
                )
            # App Server can issue the first dynamic-tool request while the
            # turn/start response is still in flight.  Bind that server-owned
            # exact turn id before authorizing the tool, then require the
            # eventual turn/start response to match it.
            binding = replace(binding, turn_id=observed_turn_id)
            self._bindings[lease.lease_id] = binding
        if observed_turn_id != binding.turn_id:
            raise HostAuthorityError("dynamic tool turn differs from the one-use actor turn")
        arguments = params.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise HostProtocolError("dynamic tool arguments are invalid JSON") from exc
        if not isinstance(arguments, Mapping):
            raise HostProtocolError("dynamic tool arguments must be an object")
        tool = str(params.get("tool", ""))
        root = Path(lease.worktree).resolve(strict=True)
        guard = ScopeGuard(root, lease.allowed_paths) if lease.role in WRITE_ROLES else None

        if tool == "campaign_list_files":
            start = _safe_repository_path(root, arguments.get("path", "."))
            maximum = min(max(int(arguments.get("max_results", 500)), 1), 1000)
            if not start.exists():
                raise HostError("repository list path does not exist")
            candidates = [start] if start.is_file() else start.rglob("*")
            files: list[str] = []
            for candidate in candidates:
                if len(files) >= maximum:
                    break
                try:
                    relative = candidate.relative_to(root).as_posix()
                    resolved = _safe_repository_path(root, relative)
                except (HostScopeError, ValueError, OSError):
                    continue
                if resolved.is_file():
                    files.append(relative)
            return {"files": files, "truncated": len(files) >= maximum}

        if tool == "campaign_read_file":
            target = _safe_repository_path(root, arguments.get("path"))
            if not target.is_file() or target.stat().st_size > MAX_DYNAMIC_OUTPUT_BYTES:
                raise HostError("repository file is missing or exceeds the read bound")
            try:
                lines = target.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError as exc:
                raise HostError("repository file is not UTF-8 text") from exc
            start_line = max(int(arguments.get("start_line", 1)), 1)
            end_line = min(int(arguments.get("end_line", len(lines))), len(lines))
            if end_line < start_line:
                raise HostError("read line range is inverted")
            return {
                "path": target.relative_to(root).as_posix(),
                "start_line": start_line,
                "end_line": end_line,
                "text": "\n".join(lines[start_line - 1 : end_line]),
            }

        if tool == "campaign_search":
            pattern = str(arguments.get("pattern", ""))
            if not pattern or len(pattern) > 500:
                raise HostError("search pattern is empty or exceeds the bound")
            try:
                expression = re.compile(pattern)
            except re.error as exc:
                raise HostError(f"search pattern is invalid: {exc}") from exc
            start = _safe_repository_path(root, arguments.get("path", "."))
            maximum = min(max(int(arguments.get("max_results", 100)), 1), 200)
            candidates = [start] if start.is_file() else start.rglob("*")
            matches: list[dict[str, Any]] = []
            for candidate in candidates:
                if len(matches) >= maximum:
                    break
                try:
                    relative = candidate.relative_to(root).as_posix()
                    target = _safe_repository_path(root, relative)
                    if not target.is_file() or target.stat().st_size > MAX_DYNAMIC_OUTPUT_BYTES:
                        continue
                    lines = target.read_text(encoding="utf-8").splitlines()
                except (HostScopeError, OSError, UnicodeDecodeError, ValueError):
                    continue
                for line_number, line in enumerate(lines, 1):
                    if expression.search(line):
                        matches.append(
                            {
                                "path": relative,
                                "line": line_number,
                                "text": line[:1000],
                            }
                        )
                        if len(matches) >= maximum:
                            break
            return {"matches": matches, "truncated": len(matches) >= maximum}

        if tool == "campaign_git_status":
            return {
                "head": _git_read(root, "rev-parse", "HEAD").strip().casefold(),
                "status": _git_read(
                    root, "status", "--porcelain=v1", "--untracked-files=all"
                )[:MAX_DYNAMIC_OUTPUT_BYTES],
            }

        if tool == "campaign_git_diff":
            base = str(arguments.get("base", "")).casefold()
            if base and not SHA_RE.fullmatch(base):
                raise HostError("diff base must be one exact Git SHA")
            diff_args = ["diff", "--no-ext-diff", "--no-color"]
            if base:
                diff_args.append(base)
            output = _git_read(root, *diff_args)
            encoded = output.encode("utf-8")
            return {
                "diff": encoded[:MAX_DYNAMIC_OUTPUT_BYTES].decode(
                    "utf-8", errors="replace"
                ),
                "truncated": len(encoded) > MAX_DYNAMIC_OUTPUT_BYTES,
            }

        if tool == "campaign_apply_patch":
            if lease.role not in WRITE_ROLES or guard is None:
                with self._host_lock:
                    self._denied_write_attempts[lease.lease_id] = (
                        self._denied_write_attempts.get(lease.lease_id, 0) + 1
                    )
                raise HostAuthorityError("parent and reviewer actors have no write tool")
            authority = self._action_authorities.get(lease.lease_id)
            if authority is None:
                raise HostAuthorityError("scoped write authority is unavailable")
            patch = str(arguments.get("patch", ""))
            paths = _patch_paths(patch)
            scoped_paths = tuple(guard.require(path) for path in paths)
            _git_read(root, "apply", "--check", "--whitespace=nowarn", "-", input_text=patch)
            action_lock = self._action_locks.setdefault(lease.lease_id, threading.RLock())
            with action_lock:
                self._require_live_action(
                    lease, authority, "APPLY_PATCH", scoped_paths
                )
                _git_read(root, "apply", "--whitespace=nowarn", "-", input_text=patch)
                try:
                    # The post-mutation authority check is the linearization
                    # point. If STOP or a fence change won the race, reverse
                    # the exact patch before returning any success.
                    self._require_live_action(
                        lease, authority, "APPLY_PATCH", scoped_paths
                    )
                except BaseException:
                    self._rollback_patch(root, patch)
                    raise
            return {"applied": True, "paths": list(paths)}

        if tool == "campaign_commit":
            if lease.role not in WRITE_ROLES or guard is None:
                with self._host_lock:
                    self._denied_write_attempts[lease.lease_id] = (
                        self._denied_write_attempts.get(lease.lease_id, 0) + 1
                    )
                raise HostAuthorityError("parent and reviewer actors have no commit tool")
            authority = self._action_authorities.get(lease.lease_id)
            if authority is None:
                raise HostAuthorityError("scoped write authority is unavailable")
            message = str(arguments.get("message", "")).strip()
            if not message or len(message) > 500 or "\x00" in message:
                raise HostError("commit message is empty or exceeds the bound")
            tracked = _git_read(root, "diff", "--name-only", "-z", "HEAD").split("\x00")
            untracked = _git_read(
                root, "ls-files", "--others", "--exclude-standard", "-z"
            ).split("\x00")
            paths = sorted({item for item in tracked + untracked if item})
            if not paths:
                raise HostError("there are no worker changes to commit")
            scoped_paths = tuple(guard.require(path) for path in paths)
            action_lock = self._action_locks.setdefault(lease.lease_id, threading.RLock())
            with action_lock:
                with tempfile.TemporaryDirectory(
                    prefix="ccos-disabled-git-hooks-"
                ) as hooks_root:
                    hooks_config = f"core.hooksPath={Path(hooks_root).as_posix()}"

                    def sealed_git(
                        *arguments: str, input_text: str | None = None
                    ) -> str:
                        return _git_read(
                            root,
                            "-c",
                            hooks_config,
                            *arguments,
                            input_text=input_text,
                        )

                    if sealed_git(
                        "diff", "--cached", "--name-only", "-z"
                    ).strip("\x00"):
                        raise HostScopeError(
                            "campaign commit refuses an index that was staged outside this action"
                        )
                    old_head = sealed_git("rev-parse", "HEAD").strip().casefold()
                    staged_applied = False
                    ref_updated = False
                    new_head: str | None = None
                    try:
                        self._require_live_action(lease, authority, "COMMIT", scoped_paths)
                        sealed_git("add", "-A", "--", *paths)
                        staged_applied = True
                        staged = {
                            item
                            for item in sealed_git(
                                "diff", "--cached", "--name-only", "-z"
                            ).split("\x00")
                            if item
                        }
                        if staged != set(paths):
                            raise HostScopeError(
                                "staged paths differ from the authorized worker scope"
                            )
                        self._require_live_action(lease, authority, "COMMIT", scoped_paths)
                        tree = sealed_git("write-tree").strip().casefold()
                        if not SHA_RE.fullmatch(tree):
                            raise HostError("campaign commit produced no exact tree")
                        new_head = sealed_git(
                            "commit-tree",
                            tree,
                            "-p",
                            old_head,
                            "-F",
                            "-",
                            input_text=message + "\n",
                        ).strip().casefold()
                        if not SHA_RE.fullmatch(new_head):
                            raise HostError("campaign commit produced no exact commit")
                        self._require_live_action(lease, authority, "COMMIT", scoped_paths)
                        sealed_git("update-ref", "HEAD", new_head, old_head)
                        ref_updated = True
                        # The post-ref authority check is the commit point. A
                        # concurrent STOP rolls HEAD and the index back while
                        # preserving the worker's product bytes for inspection.
                        self._require_live_action(lease, authority, "COMMIT", scoped_paths)
                        status = sealed_git(
                            "status", "--porcelain=v1", "--untracked-files=all"
                        )
                        if status.strip():
                            raise HostError(
                                "worker commit did not leave the exact worktree clean"
                            )
                    except BaseException:
                        if ref_updated and new_head is not None:
                            try:
                                sealed_git("update-ref", "HEAD", old_head, new_head)
                            finally:
                                sealed_git("reset", "-q", old_head, "--", *paths)
                        elif staged_applied:
                            sealed_git("reset", "-q", old_head, "--", *paths)
                        raise
                    return {"committed": True, "head": str(new_head), "paths": paths}

        raise HostAuthorityError(f"unknown campaign dynamic tool: {tool}")

    def create_idle_actor(
        self,
        lease: ActorLease | Mapping[str, Any],
        *,
        bind_authority: BindingAuthority,
        authorize_action: ActionAuthority | None = None,
        ephemeral: bool = False,
    ) -> ActorBinding:
        if not isinstance(lease, ActorLease):
            lease = ActorLease.from_dict(lease)
        if lease.lease_id in self._bindings:
            raise HostAuthorityError("one-use actor lease was already bound")
        worktree = Path(lease.worktree).resolve(strict=True)
        frozen_candidate = (
            _frozen_candidate_identity(worktree, lease.candidate_head)
            if lease.role in {"REVIEWER", "CLOSURE_REVIEWER"}
            else None
        )
        sandbox_name, sandbox_policy, writable_roots = self._sandbox_for(lease)
        denial_canary = (
            self.probe_write_denial_canary and lease.role in READ_ONLY_ROLES
        )
        dynamic_tools = _dynamic_tool_specs(
            lease.role in WRITE_ROLES, denial_canary=denial_canary
        )
        if lease.role in WRITE_ROLES and authorize_action is None:
            raise HostAuthorityError(
                "write actor requires a live scoped action-authority callback"
            )
        transport = self.transport_factory(self.executable, cwd=worktree)
        if hasattr(transport, "dynamic_tool_handler"):
            transport.dynamic_tool_handler = lambda params: self._handle_dynamic_tool(
                lease, params
            )
        transport.start()
        try:
            self._initialize(transport)
            response = transport.request(
                "thread/start",
                {
                    "cwd": str(worktree),
                    "sandbox": sandbox_name,
                    "approvalPolicy": "never",
                    "approvalsReviewer": "user",
                    "dynamicTools": dynamic_tools,
                    "ephemeral": ephemeral,
                    "model": self.model,
                    "config": {"model_reasoning_effort": self.reasoning_effort},
                    "runtimeWorkspaceRoots": [str(worktree)],
                    "selectedCapabilityRoots": [],
                    "environments": [],
                    "developerInstructions": (
                        "Execute only the exact campaign node. Lifecycle authority remains in the "
                        "campaign reducer. Do not broaden scope, create tasks, publish, or claim authority."
                    ),
                },
            )
            thread = response.get("thread")
            if not isinstance(thread, Mapping):
                raise HostProtocolError("thread/start returned no native thread object")
            thread_id = _require_id(thread.get("id"), "native thread id")
            status = thread.get("status")
            if not isinstance(status, Mapping) or status.get("type") != "idle":
                raise HostAuthorityError("native actor was not created in idle state")
            turns = thread.get("turns")
            if turns is not None and turns != () and turns != []:
                raise HostAuthorityError("new native actor already contains a turn")
            if response.get("approvalPolicy") not in {None, "never"}:
                raise HostAuthorityError("native thread did not preserve approvalPolicy=never")
            response_cwd = response.get("cwd", thread.get("cwd"))
            if _normalized_path(Path(str(response_cwd))) != _normalized_path(worktree):
                raise HostAuthorityError("native thread cwd differs from the actor lease")
            observed_sandbox = response.get("sandbox", thread.get("sandbox"))
            if observed_sandbox is None:
                raise HostAuthorityError("native thread returned no effective sandbox evidence")
            if isinstance(observed_sandbox, Mapping):
                observed_type = str(observed_sandbox.get("type", ""))
            else:
                observed_type = str(observed_sandbox)
            normalized_type = observed_type.replace("_", "-").casefold()
            if normalized_type not in {
                "readonly",
                "read-only",
            }:
                raise HostAuthorityError("native actor effective sandbox is not read-only")
            native_identity = {
                "thread_id": thread_id,
                "host_pid": (
                    int(transport.process.pid)
                    if getattr(transport, "process", None) is not None
                    else None
                ),
                "host_process_identity": (
                    process_identity(int(transport.process.pid))
                    if getattr(transport, "process", None) is not None
                    else None
                ),
                "cwd": str(worktree),
                "source_digest": _digest(thread.get("source")),
                "sandbox_type": sandbox_name,
                "writable_roots": writable_roots,
                "mediated_write_scope": (
                    list(lease.allowed_paths) if lease.role in WRITE_ROLES else []
                ),
                "dynamic_tool_digest": _digest(dynamic_tools),
                "native_write_mode": (
                    "scoped-dynamic-tools" if lease.role in WRITE_ROLES else "denied"
                ),
                "role": lease.role,
                "lease_digest": lease.payload_digest,
                "thread_created_idle": True,
                "candidate_head": (
                    frozen_candidate["head"] if frozen_candidate is not None else None
                ),
                "candidate_tree": (
                    frozen_candidate["tree"] if frozen_candidate is not None else None
                ),
            }
            # This callback must commit the exact actor/thread/lease binding. It
            # happens before any turn/start request is even constructed.
            bind_authority(lease, native_identity)
            binding = ActorBinding(
                lease=lease,
                native_thread_id=thread_id,
                native_source_digest=native_identity["source_digest"],
                native_cwd=str(worktree),
                sandbox_type=sandbox_name,
                thread_created_idle=True,
                bound_before_turn=True,
            )
            self._bindings[lease.lease_id] = binding
            self._transports[lease.lease_id] = transport
            with self._host_lock:
                self._interrupted.discard(lease.lease_id)
                self._action_locks[lease.lease_id] = threading.RLock()
            if frozen_candidate is not None:
                self._review_candidate_identities[lease.lease_id] = frozen_candidate
            if authorize_action is not None:
                self._action_authorities[lease.lease_id] = authorize_action
            return binding
        except BaseException:
            transport.close()
            raise

    def start_actor_turn(self, lease_id: str, prompt: str) -> ActorBinding:
        binding = self._bindings.get(lease_id)
        transport = self._transports.get(lease_id)
        if binding is None or transport is None:
            raise HostAuthorityError("actor has no native bind-before-turn record")
        if binding.turn_id is not None or binding.lease_consumed:
            raise HostAuthorityError("one-use actor lease cannot start another turn")
        if not binding.bound_before_turn or not binding.thread_created_idle:
            raise HostAuthorityError("actor was not bound while idle")
        self._require_review_candidate(binding)
        _sandbox_name, sandbox_policy, _roots = self._sandbox_for(binding.lease)
        # Consume the one-use lease before dispatch.  This also lets the
        # dynamic-tool handler safely bind an early server-owned turn id while
        # the turn/start response is in flight.
        self._bindings[lease_id] = replace(binding, lease_consumed=True)
        try:
            result = transport.request(
                "turn/start",
                {
                    "threadId": binding.native_thread_id,
                    "input": [{"type": "text", "text": str(prompt)}],
                    "sandboxPolicy": sandbox_policy,
                    "approvalPolicy": "never",
                    "approvalsReviewer": "user",
                    "environments": [],
                    "runtimeWorkspaceRoots": [binding.native_cwd],
                    "model": self.model,
                    "effort": self.reasoning_effort,
                },
            )
        except BaseException:
            self.interrupt(lease_id)
            raise
        turn = result.get("turn")
        if not isinstance(turn, Mapping):
            raise HostProtocolError("turn/start returned no native turn")
        turn_id = _require_id(turn.get("id"), "native turn id")
        current = self._bindings.get(lease_id)
        if current is None:
            raise HostAuthorityError("actor binding disappeared during turn dispatch")
        if current.turn_id is not None and current.turn_id != turn_id:
            self.interrupt(lease_id)
            raise HostAuthorityError(
                "turn/start response differs from the early dynamic-tool turn binding"
            )
        updated = replace(current, turn_id=turn_id, lease_consumed=True)
        self._bindings[lease_id] = updated
        return updated

    def collect_terminal_receipt(
        self,
        lease_id: str,
        *,
        current_epochs: CurrentEpochs,
        timeout: float | None = None,
    ) -> TerminalReceipt:
        binding = self._bindings.get(lease_id)
        transport = self._transports.get(lease_id)
        if binding is None or transport is None or binding.turn_id is None:
            raise HostAuthorityError("actor has no started native turn")
        turn = transport.wait_turn(binding.native_thread_id, binding.turn_id, timeout=timeout)
        frozen_candidate = self._require_review_candidate(binding)
        epochs = current_epochs(binding.lease.campaign_id, binding.lease.node_id)
        expected = {
            "authority_epoch": binding.lease.authority_epoch,
            "cancellation_epoch": binding.lease.cancellation_epoch,
            "fencing_epoch": binding.lease.fencing_epoch,
        }
        if any(epochs.get(key) != value for key, value in expected.items()):
            raise LateResultError("native worker result is stale after authority/cancellation/fencing change")
        items = turn.get("items") if isinstance(turn.get("items"), list) else []
        actions = [
            item
            for item in items
            if isinstance(item, Mapping)
            and item.get("type") in {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall"}
        ]
        body = {
            "protocol_version": "ccos-native-terminal-receipt-v1",
            "campaign_id": binding.lease.campaign_id,
            "node_id": binding.lease.node_id,
            "actor_id": binding.lease.actor_id,
            "role": binding.lease.role,
            "lease_id": binding.lease.lease_id,
            "native_thread_id": binding.native_thread_id,
            "native_turn_id": binding.turn_id,
            "authority_epoch": binding.lease.authority_epoch,
            "cancellation_epoch": binding.lease.cancellation_epoch,
            "fencing_epoch": binding.lease.fencing_epoch,
            "candidate_head": binding.lease.candidate_head,
            "candidate_tree": (
                frozen_candidate["tree"] if frozen_candidate is not None else None
            ),
            "turn_status": str(turn.get("status", "unknown")),
            "output_digest": _digest(items),
            "action_count": len(actions),
            "result_payload": _structured_result(items, turn),
        }
        body["receipt_digest"] = _digest(body)
        return TerminalReceipt(**body)

    def interrupt(self, lease_id: str) -> None:
        with self._host_lock:
            self._interrupted.add(lease_id)
        binding = self._bindings.get(lease_id)
        transport = self._transports.get(lease_id)
        if binding is None or transport is None:
            return
        if binding.turn_id is not None:
            try:
                transport.request(
                    "turn/interrupt",
                    {"threadId": binding.native_thread_id, "turnId": binding.turn_id},
                    timeout=15,
                )
            except HostError:
                pass
        transport.close()
        self._transports.pop(lease_id, None)
        self._action_authorities.pop(lease_id, None)

    def close(self) -> None:
        for lease_id in list(self._transports):
            self.interrupt(lease_id)


class FakeHost:
    """Deterministic host used by reducer/supervisor/end-to-end tests."""

    def __init__(self) -> None:
        self.bindings: dict[str, ActorBinding] = {}
        self.turns: list[str] = []
        self.results: dict[str, Mapping[str, Any]] = {}
        self.interrupted: set[str] = set()
        self.action_authorities: dict[str, ActionAuthority] = {}

    def create_idle_actor(
        self,
        lease: ActorLease | Mapping[str, Any],
        *,
        bind_authority: BindingAuthority,
        authorize_action: ActionAuthority | None = None,
        ephemeral: bool = False,
    ) -> ActorBinding:
        del ephemeral
        if not isinstance(lease, ActorLease):
            lease = ActorLease.from_dict(lease)
        if lease.lease_id in self.bindings:
            raise HostAuthorityError("one-use actor lease was already bound")
        if lease.role in WRITE_ROLES and authorize_action is None:
            raise HostAuthorityError(
                "write actor requires a live scoped action-authority callback"
            )
        native_id = f"native-{lease.actor_id}"
        identity = {
            "thread_id": native_id,
            "host_pid": None,
            "host_process_identity": None,
            "cwd": lease.worktree,
            "source_digest": _digest({"fake": True, "actor": lease.actor_id}),
            "sandbox_type": (
                "read-only"
            ),
            "writable_roots": [],
            "mediated_write_scope": (
                list(lease.allowed_paths) if lease.role in WRITE_ROLES else []
            ),
            "dynamic_tool_digest": _digest(
                _dynamic_tool_specs(lease.role in WRITE_ROLES)
            ),
            "native_write_mode": (
                "scoped-dynamic-tools" if lease.role in WRITE_ROLES else "denied"
            ),
            "role": lease.role,
            "lease_digest": lease.payload_digest,
            "thread_created_idle": True,
        }
        bind_authority(lease, identity)
        binding = ActorBinding(
            lease=lease,
            native_thread_id=native_id,
            native_source_digest=identity["source_digest"],
            native_cwd=lease.worktree,
            sandbox_type=identity["sandbox_type"],
            thread_created_idle=True,
            bound_before_turn=True,
        )
        self.bindings[lease.lease_id] = binding
        if authorize_action is not None:
            self.action_authorities[lease.lease_id] = authorize_action
        return binding

    def start_actor_turn(self, lease_id: str, prompt: str) -> ActorBinding:
        binding = self.bindings[lease_id]
        if binding.lease_consumed:
            raise HostAuthorityError("one-use actor lease cannot start another turn")
        updated = replace(binding, turn_id=f"turn-{lease_id}", lease_consumed=True)
        self.bindings[lease_id] = updated
        self.turns.append(prompt)
        return updated

    def write_file(self, lease_id: str, relative: str, data: bytes) -> None:
        binding = self.bindings[lease_id]
        if binding.lease.role not in {"IMPLEMENTER", "REPAIRER"}:
            raise PermissionError("parent and reviewer actors are read-only")
        guard = ScopeGuard(binding.lease.worktree, binding.lease.allowed_paths)
        allowed = guard.require(relative)
        authority = self.action_authorities.get(lease_id)
        if authority is None:
            raise HostAuthorityError("scoped write authority is unavailable")
        authority(binding.lease, "WRITE_FILE", allowed)
        target = Path(binding.lease.worktree, *PurePosixPath(allowed).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def set_result(self, lease_id: str, result: Mapping[str, Any]) -> None:
        self.results[lease_id] = dict(result)

    def collect_terminal_receipt(
        self, lease_id: str, *, current_epochs: CurrentEpochs, timeout: float | None = None
    ) -> TerminalReceipt:
        del timeout
        binding = self.bindings[lease_id]
        epochs = current_epochs(binding.lease.campaign_id, binding.lease.node_id)
        for key in ("authority_epoch", "cancellation_epoch", "fencing_epoch"):
            if epochs.get(key) != getattr(binding.lease, key):
                raise LateResultError("fake worker result is stale")
        result = self.results.get(lease_id, {"status": "completed"})
        body = {
            "protocol_version": "ccos-native-terminal-receipt-v1",
            "campaign_id": binding.lease.campaign_id,
            "node_id": binding.lease.node_id,
            "actor_id": binding.lease.actor_id,
            "role": binding.lease.role,
            "lease_id": binding.lease.lease_id,
            "native_thread_id": binding.native_thread_id,
            "native_turn_id": str(binding.turn_id),
            "authority_epoch": binding.lease.authority_epoch,
            "cancellation_epoch": binding.lease.cancellation_epoch,
            "fencing_epoch": binding.lease.fencing_epoch,
            "candidate_head": binding.lease.candidate_head,
            "candidate_tree": (
                str(result["candidate_tree"])
                if result.get("candidate_tree") is not None
                else None
            ),
            "turn_status": str(result.get("status", "completed")),
            "output_digest": _digest(result),
            "action_count": int(result.get("action_count", 0)),
            "result_payload": dict(result),
        }
        body["receipt_digest"] = _digest(body)
        return TerminalReceipt(**body)

    def interrupt(self, lease_id: str) -> None:
        self.interrupted.add(lease_id)
        self.action_authorities.pop(lease_id, None)


def probe_native_host_capability(
    *,
    executable: str | Path = "codex",
    cwd: str | Path,
    transport_factory: Callable[..., Any] = AppServerTransport,
    turn_timeout_seconds: float = 120,
) -> dict[str, Any]:
    """Prove native bind-before-turn writes and read-only denial end to end.

    All native turns are rooted in a disposable Git repository.  Merely
    creating an idle thread or advertising dynamic tools is not capability
    evidence: the implementer must invoke the mediated patch and commit tools,
    and both a parent and reviewer must invoke a write canary that the host
    denies.
    """

    requested_root = Path(cwd).resolve(strict=True)
    resolved_executable = _resolve_codex_executable(executable)
    timeout = float(turn_timeout_seconds)
    if timeout <= 0 or timeout > 600:
        raise HostProtocolError("native host probe timeout must be in (0, 600]")

    def bounded_transport_factory(
        executable_value: str | Path, *, cwd: str | Path
    ) -> Any:
        return transport_factory(
            executable_value,
            cwd=cwd,
            timeout=timeout,
        )

    def epochs(*_args: Any) -> Mapping[str, int]:
        return {
            "authority_epoch": 1,
            "cancellation_epoch": 0,
            "fencing_epoch": 1,
        }

    with tempfile.TemporaryDirectory(prefix="ccos-native-host-probe-") as raw:
        repository = Path(raw) / "repo"
        repository.mkdir()
        _git_read(repository, "init", "-q", "-b", "main")
        _git_read(repository, "config", "user.email", "ccos-probe@example.invalid")
        _git_read(repository, "config", "user.name", "Coding OS Host Probe")
        (repository / "probe.txt").write_text(
            "PROBE_OLD\n", encoding="utf-8", newline="\n"
        )
        _git_read(repository, "add", "--", "probe.txt")
        _git_read(repository, "commit", "-q", "-m", "probe baseline")
        base_head = _git_read(repository, "rev-parse", "HEAD").strip().casefold()
        if not SHA_RE.fullmatch(base_head):
            raise HostProtocolError("native host probe created no exact baseline head")

        implementer_lease = ActorLease.issue(
            lease_id="probe-implementer-lease",
            request_id="probe-implementer-request",
            campaign_id="native-host-capability-probe",
            node_id="implementer-canary",
            actor_id="native-probe-implementer",
            role="IMPLEMENTER",
            worktree=str(repository),
            allowed_paths=("probe.txt",),
            authority_epoch=1,
            cancellation_epoch=0,
            fencing_epoch=1,
            candidate_head=base_head,
        )
        implementer_bindings: list[Mapping[str, Any]] = []
        authorized_actions: list[tuple[str, str]] = []

        def authorize_probe_action(
            _lease: ActorLease, action: str, path: str | None
        ) -> Mapping[str, Any]:
            if path != "probe.txt" or action not in {"APPLY_PATCH", "COMMIT"}:
                raise HostAuthorityError("probe action escaped its exact canary scope")
            authorized_actions.append((action, path))
            return {"authorized": True, "probe": True}

        implementer = NativeCodexHost(
            executable=resolved_executable,
            model="gpt-5.6-sol",
            reasoning_effort="low",
            transport_factory=bounded_transport_factory,
        )
        try:
            implementer_binding = implementer.create_idle_actor(
                implementer_lease,
                bind_authority=lambda _lease, identity: implementer_bindings.append(
                    dict(identity)
                ),
                authorize_action=authorize_probe_action,
                ephemeral=True,
            )
            implementer_started = implementer.start_actor_turn(
                implementer_lease.lease_id,
                (
                    "CCOS_NATIVE_CAPABILITY_PROBE_IMPLEMENTER. Use campaign_read_file "
                    "on probe.txt. Then invoke campaign_apply_patch with exactly this "
                    "patch:\n"
                    "diff --git a/probe.txt b/probe.txt\n"
                    "--- a/probe.txt\n"
                    "+++ b/probe.txt\n"
                    "@@ -1 +1 @@\n"
                    "-PROBE_OLD\n"
                    "+PROBE_NEW\n"
                    "Then invoke campaign_commit with message 'native probe commit'. "
                    "Do not use shell or any other write mechanism. Return JSON only "
                    "after both mediated calls finish."
                ),
            )
            implementer_receipt = implementer.collect_terminal_receipt(
                implementer_lease.lease_id,
                current_epochs=epochs,
                timeout=timeout,
            )
        finally:
            implementer.close()

        required_actions = {"APPLY_PATCH", "COMMIT"}
        observed_actions = {item[0] for item in authorized_actions}
        if implementer_receipt.turn_status != "completed":
            raise HostProtocolError(
                "native implementer probe turn did not complete: "
                f"{implementer_receipt.turn_status}"
            )
        if observed_actions != required_actions:
            raise HostProtocolError(
                "native implementer turn completed without proving mediated patch and "
                f"commit calls: observed={sorted(observed_actions)}"
            )
        candidate_head = _git_read(repository, "rev-parse", "HEAD").strip().casefold()
        candidate_tree = _git_read(
            repository, "rev-parse", f"{candidate_head}^{{tree}}"
        ).strip().casefold()
        candidate_status = _git_read(
            repository, "status", "--porcelain=v1", "--untracked-files=all"
        )
        candidate_content = (repository / "probe.txt").read_text(encoding="utf-8")
        if (
            candidate_head == base_head
            or not SHA_RE.fullmatch(candidate_head)
            or not SHA_RE.fullmatch(candidate_tree)
            or candidate_status
            or candidate_content != "PROBE_NEW\n"
        ):
            raise HostProtocolError(
                "native implementer probe did not produce one clean exact commit"
            )

        parent_lease = ActorLease.issue(
            lease_id="probe-parent-lease",
            request_id="probe-parent-request",
            campaign_id="native-host-capability-probe",
            node_id="parent-denial-canary",
            actor_id="native-probe-parent",
            role="PARENT",
            worktree=str(repository),
            allowed_paths=(),
            authority_epoch=1,
            cancellation_epoch=0,
            fencing_epoch=1,
            candidate_head=candidate_head,
        )
        parent = NativeCodexHost(
            executable=resolved_executable,
            model="gpt-5.6-sol",
            reasoning_effort="low",
            transport_factory=bounded_transport_factory,
            probe_write_denial_canary=True,
        )
        parent_bindings: list[Mapping[str, Any]] = []
        try:
            parent_binding = parent.create_idle_actor(
                parent_lease,
                bind_authority=lambda _lease, identity: parent_bindings.append(
                    dict(identity)
                ),
                ephemeral=True,
            )
            parent_started = parent.start_actor_turn(
                parent_lease.lease_id,
                (
                    "CCOS_NATIVE_CAPABILITY_PROBE_PARENT_DENIAL. This is a host "
                    "denial canary. Invoke campaign_apply_patch exactly once with this "
                    "patch and observe the expected denial:\n"
                    "diff --git a/probe.txt b/probe.txt\n"
                    "--- a/probe.txt\n"
                    "+++ b/probe.txt\n"
                    "@@ -1 +1 @@\n"
                    "-PROBE_NEW\n"
                    "+PROBE_DENIED\n"
                    "Do not use shell or another write mechanism. Return JSON only after "
                    "the mediated call is denied."
                ),
            )
            parent_receipt = parent.collect_terminal_receipt(
                parent_lease.lease_id,
                current_epochs=epochs,
                timeout=timeout,
            )
            parent_denied_attempts = parent._denied_write_attempts.get(
                parent_lease.lease_id, 0
            )
        finally:
            parent.close()

        parent_head = _git_read(repository, "rev-parse", "HEAD").strip().casefold()
        parent_tree = _git_read(
            repository, "rev-parse", f"{parent_head}^{{tree}}"
        ).strip().casefold()
        parent_status = _git_read(
            repository, "status", "--porcelain=v1", "--untracked-files=all"
        )
        parent_content = (repository / "probe.txt").read_text(encoding="utf-8")
        if (
            parent_receipt.turn_status != "completed"
            or parent_receipt.candidate_head != candidate_head
            or parent_denied_attempts != 1
        ):
            raise HostProtocolError(
                "native parent turn did not prove exactly one mediated write denial"
            )
        if (
            parent_head != candidate_head
            or parent_tree != candidate_tree
            or parent_status != candidate_status
            or parent_content != candidate_content
        ):
            raise HostProtocolError("parent denial canary mutated the candidate")

        reviewer_lease = ActorLease.issue(
            lease_id="probe-reviewer-lease",
            request_id="probe-reviewer-request",
            campaign_id="native-host-capability-probe",
            node_id="reviewer-denial-canary",
            actor_id="native-probe-reviewer",
            role="REVIEWER",
            worktree=str(repository),
            allowed_paths=(),
            authority_epoch=1,
            cancellation_epoch=0,
            fencing_epoch=1,
            candidate_head=candidate_head,
        )
        reviewer = NativeCodexHost(
            executable=resolved_executable,
            model="gpt-5.6-sol",
            reasoning_effort="low",
            transport_factory=bounded_transport_factory,
            probe_write_denial_canary=True,
        )
        reviewer_bindings: list[Mapping[str, Any]] = []
        try:
            reviewer_binding = reviewer.create_idle_actor(
                reviewer_lease,
                bind_authority=lambda _lease, identity: reviewer_bindings.append(
                    dict(identity)
                ),
                ephemeral=True,
            )
            reviewer_started = reviewer.start_actor_turn(
                reviewer_lease.lease_id,
                (
                    "CCOS_NATIVE_CAPABILITY_PROBE_REVIEWER_DENIAL. This is a host "
                    "denial canary. Invoke campaign_apply_patch once with exactly this "
                    "patch and observe the expected denial:\n"
                    "diff --git a/probe.txt b/probe.txt\n"
                    "--- a/probe.txt\n"
                    "+++ b/probe.txt\n"
                    "@@ -1 +1 @@\n"
                    "-PROBE_NEW\n"
                    "+PROBE_DENIED\n"
                    "Do not use shell or another write mechanism. Return JSON only after "
                    "the mediated call is denied."
                ),
            )
            reviewer_receipt = reviewer.collect_terminal_receipt(
                reviewer_lease.lease_id,
                current_epochs=epochs,
                timeout=timeout,
            )
            reviewer_denied_attempts = reviewer._denied_write_attempts.get(
                reviewer_lease.lease_id, 0
            )
        finally:
            reviewer.close()

        reviewer_head = _git_read(repository, "rev-parse", "HEAD").strip().casefold()
        reviewer_tree = _git_read(
            repository, "rev-parse", f"{reviewer_head}^{{tree}}"
        ).strip().casefold()
        reviewer_status = _git_read(
            repository, "status", "--porcelain=v1", "--untracked-files=all"
        )
        reviewer_content = (repository / "probe.txt").read_text(encoding="utf-8")
        if (
            reviewer_receipt.turn_status != "completed"
            or reviewer_receipt.candidate_head != candidate_head
            or reviewer_receipt.candidate_tree != candidate_tree
            or reviewer_denied_attempts != 1
        ):
            raise HostProtocolError(
                "native reviewer turn did not prove exactly one mediated write denial"
            )
        if (
            reviewer_head != candidate_head
            or reviewer_tree != candidate_tree
            or reviewer_status != candidate_status
            or reviewer_content != candidate_content
        ):
            raise HostProtocolError("reviewer denial canary mutated the candidate")

        if not implementer_bindings or not parent_bindings or not reviewer_bindings:
            raise HostProtocolError("native probe produced no bind-before-turn evidence")
        if (
            parent_bindings[0].get("role") != "PARENT"
            or reviewer_bindings[0].get("role") != "REVIEWER"
        ):
            raise HostProtocolError("native probe bound an unexpected read-only role")
        dynamic_tools = _dynamic_tool_specs(True)
        denial_canary_tools = _dynamic_tool_specs(False, denial_canary=True)
        denial_canary_write_tool_names = [
            item["name"]
            for item in denial_canary_tools
            if item["name"] in {"campaign_apply_patch", "campaign_commit"}
        ]
        if denial_canary_write_tool_names != ["campaign_apply_patch"]:
            raise HostProtocolError("read-only probe exposed more than the denial canary")
        evidence = {
            "probe_version": HOST_CAPABILITY_PROBE_VERSION,
            "native_thread_start": True,
            "idle_before_turn": bool(
                implementer_binding.thread_created_idle
                and parent_binding.thread_created_idle
                and reviewer_binding.thread_created_idle
            ),
            "bind_before_turn": bool(
                implementer_binding.bound_before_turn
                and parent_binding.bound_before_turn
                and reviewer_binding.bound_before_turn
            ),
            "implementer_turn_started": bool(
                implementer_started.lease_consumed and implementer_started.turn_id
            ),
            "implementer_turn_completed": True,
            "scoped_dynamic_tools": observed_actions == required_actions,
            "mediated_action_counts": {
                action: sum(1 for observed, _path in authorized_actions if observed == action)
                for action in sorted(required_actions)
            },
            "dynamic_tool_digest": _digest(dynamic_tools),
            "write_tool_names": [
                item["name"]
                for item in dynamic_tools
                if item["name"] in {"campaign_apply_patch", "campaign_commit"}
            ],
            "denial_canary_dynamic_tool_digest": _digest(denial_canary_tools),
            "denial_canary_write_tool_names": denial_canary_write_tool_names,
            "read_only_denial_attempts": (
                parent_denied_attempts + reviewer_denied_attempts
            ),
            "read_only_denial_proven": bool(
                parent_denied_attempts == 1 and reviewer_denied_attempts == 1
            ),
            "parent_role": str(parent_bindings[0].get("role")),
            "parent_idle_before_turn": parent_binding.thread_created_idle,
            "parent_bound_before_turn": parent_binding.bound_before_turn,
            "parent_turn_started": bool(
                parent_started.lease_consumed and parent_started.turn_id
            ),
            "parent_turn_completed": parent_receipt.turn_status == "completed",
            "parent_write_attempts": parent_denied_attempts,
            "parent_write_denied": parent_denied_attempts == 1,
            "parent_receipt_digest": parent_receipt.receipt_digest,
            "parent_head": parent_head,
            "parent_tree": parent_tree,
            "parent_status": "CLEAN" if not parent_status else parent_status,
            "parent_content": parent_content,
            "reviewer_role": str(reviewer_bindings[0].get("role")),
            "reviewer_idle_before_turn": reviewer_binding.thread_created_idle,
            "reviewer_bound_before_turn": reviewer_binding.bound_before_turn,
            "reviewer_turn_started": bool(
                reviewer_started.lease_consumed and reviewer_started.turn_id
            ),
            "reviewer_turn_completed": reviewer_receipt.turn_status == "completed",
            "reviewer_write_attempts": reviewer_denied_attempts,
            "reviewer_write_denied": reviewer_denied_attempts == 1,
            "reviewer_candidate_head": reviewer_receipt.candidate_head,
            "reviewer_candidate_tree": reviewer_receipt.candidate_tree,
            "reviewer_head": reviewer_head,
            "reviewer_tree": reviewer_tree,
            "reviewer_status": "CLEAN" if not reviewer_status else reviewer_status,
            "reviewer_content": reviewer_content,
            "base_head": base_head,
            "candidate_head": candidate_head,
            "candidate_tree": candidate_tree,
            "candidate_status": "CLEAN" if not candidate_status else candidate_status,
            "candidate_content": candidate_content,
            "implementer_receipt_digest": implementer_receipt.receipt_digest,
            "reviewer_receipt_digest": reviewer_receipt.receipt_digest,
            "disposable_repository": True,
            "actors_bound_only_to_disposable_repository": all(
                Path(str(item["cwd"])).resolve() == repository.resolve()
                for item in (
                    implementer_bindings + parent_bindings + reviewer_bindings
                )
            ),
            "requested_root": str(requested_root),
            "requested_root_used_as_actor_worktree": False,
            "product_state_mutated": False,
            "host_executable": str(resolved_executable),
            "host_executable_sha256": hashlib.sha256(
                resolved_executable.read_bytes()
            ).hexdigest(),
        }
        if not evidence["actors_bound_only_to_disposable_repository"]:
            raise HostProtocolError("native probe actor escaped the disposable repository")
        evidence["evidence_digest"] = _digest(evidence)
        return evidence
