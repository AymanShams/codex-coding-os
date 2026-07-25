#!/usr/bin/env python3
"""Proposal-only Codex App Server controller for one canonical runtime action.

The App Server and every model thread are untrusted, read-only proposal workers.
This controller never approves a native mutation and never executes the runtime
broker.  It correlates native parent/child identities, accepts one strict
completed implementation proposal, writes that proposal outside the governed
worktree, and emits the exact actor-binding, receipt, and grant-issuance inputs
that a separately trusted supervisor may submit to the canonical case engine.
"""

from __future__ import annotations

import argparse
import base64
import copy
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Mapping, Protocol


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from case_runtime_broker import (  # noqa: E402
    CONTROLLER_KEY_ENVIRONMENT,
    CONTROLLER_RECEIPT_PROTOCOL_VERSION,
    seal_controller_receipt,
    windows_identity,
)
from case_state import (  # noqa: E402
    ACTION_GRANT_PROTOCOL_VERSION,
    MAX_REPLACEMENT_BYTES,
    RUNTIME_ACTOR_PROTOCOL_VERSION,
    canonical_case_id,
    canonical_json_sha256,
    file_sha256,
    normalize_action_path,
    normalize_binding,
    normalize_repo_url,
    path_contains_link_or_reparse,
    path_is_within,
    require_sha,
    require_snapshot_hash,
    require_stable_id,
    require_utc_timestamp,
    require_windows_sid,
    utc_now,
)


CONTROLLER_RUN_PROTOCOL_VERSION = "ccos-app-server-proposal-controller-v1"
PROPOSAL_PROTOCOL_VERSION = "ccos-completed-implementation-proposal-v1"
IDENTITY_EVIDENCE_PROTOCOL_VERSION = "ccos-native-thread-identity-evidence-v1"
RESTART_CHECKPOINT_PROTOCOL_VERSION = "ccos-app-server-restart-checkpoint-v1"
RESTART_EVIDENCE_PROTOCOL_VERSION = "ccos-app-server-restart-evidence-v1"

CANONICAL_CHILD_PLAN = (
    ("implementation", "implementer_child"),
    ("reviewer_coding_os", "review_child"),
    ("reviewer_leheta", "review_child"),
    ("reviewer_healpath", "review_child"),
    ("closure", "closure_child"),
    ("incomplete", "incomplete_child"),
)
UNKNOWN_CHILD_NAME = "unrecognized_probe"
UNKNOWN_CONTROLLER_ROLE = "unknown_child"

CLIENT_REQUEST_METHODS = frozenset(
    {
        "initialize",
        "mcpServerStatus/list",
        "hooks/list",
        "thread/start",
        "turn/start",
        "thread/read",
        "thread/list",
    }
)
MUTATION_APPROVAL_METHODS = frozenset(
    {"item/fileChange/requestApproval", "item/commandExecution/requestApproval"}
)
MUTATION_ITEM_TYPES = frozenset(
    {
        "commandExecution",
        "fileChange",
        "mcpToolCall",
        "dynamicToolCall",
        "computerToolCall",
    }
)
SECRET_ENVIRONMENT_NAME = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|CREDENTIAL|COOKIE|API[_-]?KEY|AUTH|SESSION)", re.I
)
APP_SERVER_ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "CODEX_HOME", "COMSPEC", "LOCALAPPDATA", "NO_COLOR", "PATH", "PATHEXT",
        "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "WINDIR",
    }
)
APP_SERVER_ENABLED_FEATURES = ("multi_agent",)
APP_SERVER_DISABLED_FEATURES = (
    "apps",
    "auth_elicitation",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "code_mode_host",
    "computer_use",
    "hooks",
    "image_generation",
    "in_app_browser",
    "memories",
    "plugins",
    "remote_plugin",
    "shell_tool",
    "skill_mcp_dependency_install",
    "unified_exec",
    "workspace_dependencies",
)

PARENT_DEVELOPER_INSTRUCTIONS = (
    "You are an administrative, proposal-only parent. Use only the native "
    "collaboration spawn operation when the controller asks for one child. "
    "Never run a command, change a file, call an MCP or dynamic tool, request "
    "permission, expose credentials, or claim mutation authority. A child name "
    "or role claim is not authorization. Spawn exactly the requested direct "
    "child, wait for it as instructed, then stop."
)


class ControllerError(RuntimeError):
    """Base error for proposal-controller failures."""


class ControllerValidationError(ControllerError):
    pass


class ControllerProtocolError(ControllerError):
    pass


class ControllerAuthorizationError(ControllerError):
    pass


class Transport(Protocol):
    events: list[dict[str, Any]]
    audit: list[dict[str, Any]]

    def request(
        self, method: str, params: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]: ...

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None: ...

    def wait_turn_completed(
        self, thread_id: str, turn_id: str, timeout: float | None = None,
    ) -> Mapping[str, Any]: ...


def _plain_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonempty(value: Any, label: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ControllerValidationError(f"{label} must be a non-empty bounded string")
    return value.strip()


def _absolute_path(value: Any, label: str, *, must_exist: bool) -> tuple[Path, str]:
    raw = _nonempty(value, label, 32768)
    path = Path(raw)
    if not path.is_absolute():
        raise ControllerValidationError(f"{label} must be absolute")
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as exc:
        raise ControllerValidationError(f"{label} cannot be resolved: {exc}") from exc
    return resolved, normalize_binding("worktree", str(resolved))


def _decode_controller_key() -> bytes:
    raw = os.environ.get(CONTROLLER_KEY_ENVIRONMENT, "")
    if not raw:
        raise ControllerAuthorizationError(
            f"trusted controller key is absent from {CONTROLLER_KEY_ENVIRONMENT}"
        )
    try:
        key = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError) as exc:
        raise ControllerAuthorizationError("trusted controller key is not canonical base64") from exc
    if len(key) < 32:
        raise ControllerAuthorizationError("trusted controller key must contain at least 256 bits")
    return key


def build_app_server_environment(
    worker_codex_home: Path,
    executable: Path | None = None,
    inherited: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return fixed values for the untrusted App Server environment.

    Values such as PATH, TEMP, TMP, LOCALAPPDATA, and CODEX_HOME are never
    inherited from the trusted user.  All mutable locations are descendants of
    the one worker-writable root.  The only executable locations in PATH are
    the fixed Windows system directory and the directory of the pinned Codex
    binary.
    """
    source = os.environ if inherited is None else inherited
    binary = (executable or Path(sys.executable)).resolve(strict=True)
    home = worker_codex_home.resolve(strict=True)
    raw_system_root = source.get("SYSTEMROOT", source.get("WINDIR", r"C:\Windows"))
    system_root = (
        Path(raw_system_root)
        if os.name == "nt"
        else PureWindowsPath(raw_system_root)
    )
    if not system_root.is_absolute():
        raise ControllerValidationError("fixed Windows system root must be absolute")
    if os.name == "nt":
        system_root = system_root.resolve(strict=False)
    system32 = system_root / "System32"
    mutable = {
        "CODEX_HOME": home,
        "LOCALAPPDATA": home / "local-app-data",
        "TEMP": home / "temp",
        "TMP": home / "temp",
    }
    for label, path in mutable.items():
        if not path_is_within(path, home):
            raise ControllerAuthorizationError(f"{label} escapes the worker-writable root")
    environment = {
        "CODEX_HOME": str(mutable["CODEX_HOME"]),
        "COMSPEC": str(system32 / "cmd.exe"),
        "LOCALAPPDATA": str(mutable["LOCALAPPDATA"]),
        "NO_COLOR": "1",
        "PATH": ";".join((str(system32), str(binary.parent))),
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "SYSTEMDRIVE": system_root.drive or "C:",
        "SYSTEMROOT": str(system_root),
        "TEMP": str(mutable["TEMP"]),
        "TMP": str(mutable["TMP"]),
        "WINDIR": str(system_root),
    }
    if set(environment) != APP_SERVER_ENVIRONMENT_ALLOWLIST or any(
        SECRET_ENVIRONMENT_NAME.search(name) for name in environment
    ) or CONTROLLER_KEY_ENVIRONMENT in environment:
        raise ControllerAuthorizationError("App Server environment retained a secret-like variable")
    return environment


def _prepare_worker_environment(worker_codex_home: Path) -> None:
    """Create only the dedicated mutable directories required by App Server."""
    home = worker_codex_home.resolve(strict=True)
    if not home.is_dir() or home.is_symlink() or path_contains_link_or_reparse(home):
        raise ControllerAuthorizationError("worker Codex home is unavailable or linked")
    for child in (home / "local-app-data", home / "temp"):
        child.mkdir(mode=0o700, parents=False, exist_ok=True)
        if not child.is_dir() or child.is_symlink() or path_contains_link_or_reparse(child, stop=home):
            raise ControllerAuthorizationError("worker environment directory is linked or invalid")


def _worker_environment_evidence(
    environment: Mapping[str, str], worker_codex_home: Path,
) -> dict[str, Any]:
    """Validate and hash resolved environment values without exposing them."""
    if set(environment) != APP_SERVER_ENVIRONMENT_ALLOWLIST:
        raise ControllerAuthorizationError("App Server environment names differ from the fixed set")
    home = worker_codex_home.resolve(strict=True)
    mutable_names = ("CODEX_HOME", "LOCALAPPDATA", "TEMP", "TMP")
    resolved_mutable: dict[str, str] = {}
    for name in mutable_names:
        path = Path(environment[name]).resolve(strict=True)
        if not path.is_dir() or not path_is_within(path, home):
            raise ControllerAuthorizationError(f"App Server {name} is outside the worker root")
        resolved_mutable[name] = normalize_binding("worktree", str(path))
    path_entries = [Path(item).resolve(strict=True) for item in environment["PATH"].split(os.pathsep)]
    if len(path_entries) != 2 or path_entries[0].name.casefold() != "system32":
        raise ControllerAuthorizationError("App Server PATH differs from the fixed two-entry path")
    evidence = {
        "environment_names": sorted(environment),
        "environment_values_sha256": canonical_json_sha256(dict(environment)),
        "mutable_paths_sha256": canonical_json_sha256(resolved_mutable),
        "path_entries_sha256": canonical_json_sha256(
            [normalize_binding("worktree", str(item)) for item in path_entries]
        ),
        "controller_key_exposed": False,
        "secret_like_name_count": 0,
        "mutable_paths_within_worker_root": True,
    }
    evidence["evidence_sha256"] = canonical_json_sha256(evidence)
    return evidence


def build_app_server_command(executable: Path) -> list[str]:
    """Build the fixed App Server command with no configurable tool surface."""
    command = [
        str(executable),
        "--strict-config",
        "--enable",
        "multi_agent",
        "-c",
        "mcp_servers={}",
        "-c",
        'shell_environment_policy.inherit="none"',
        "-c",
        'web_search="disabled"',
    ]
    for feature in APP_SERVER_DISABLED_FEATURES:
        command.extend(["--disable", feature])
    command.extend([
        "app-server",
        "--listen",
        "stdio://",
    ])
    return command


def _path_uri(path: Path) -> str:
    try:
        return path.resolve(strict=True).as_uri()
    except (OSError, ValueError) as exc:
        raise ControllerValidationError(f"sandbox path cannot be represented as a file URI: {exc}") from exc


def build_sandboxed_app_server_command(
    executable: Path,
    *,
    cwd: Path,
    worker_codex_home: Path,
) -> tuple[list[str], dict[str, Any]]:
    """Build one fixed outer-sandbox plus inner-App-Server argv.

    Both the launcher and inner binary are the same pinned Codex executable. No
    arbitrary command enters this function. The outer process creates the
    ``CodexSandboxOnline`` restricted token, grants write access only to the
    dedicated worker state root, and proxies the inner App Server JSONL stdio.
    Network remains available to the host process for model-service transport.
    Every model turn is independently fixed to read-only with network disabled.
    """
    state = {
        "permissionProfile": {
            "network": {"enabled": True},
            "file_system": {
                "read": [str(cwd), str(executable.parent), str(worker_codex_home)],
                "write": [str(worker_codex_home)],
            },
        },
        "codexLinuxSandboxExe": None,
        "sandboxCwd": _path_uri(cwd),
        "useLegacyLandlock": False,
    }
    inner = build_app_server_command(executable)
    command = [
        str(executable),
        "sandbox",
        "--sandbox-state-json",
        json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        "--",
        *inner,
    ]
    return command, state


def inspect_app_server_process_identity(
    outer_pid: int,
    *,
    executable: Path,
    expected_worker_sid: str,
    expected_broker_sid: str,
    expected_inner_command: list[str],
    worker_codex_home: Path,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Resolve the one inner App Server PID and verify its actual Windows token SID."""
    if os.name != "nt":
        raise ControllerAuthorizationError("restricted App Server PID inspection requires Windows")
    powershell = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        raise ControllerProtocolError("fixed Windows PowerShell path is unavailable")
    script = r"""
$ErrorActionPreference = 'Stop'
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class CcosNativeArgv {
  [DllImport("shell32.dll", SetLastError=true)]
  public static extern IntPtr CommandLineToArgvW(
      [MarshalAs(UnmanagedType.LPWStr)] string commandLine, out int argc);
  [DllImport("kernel32.dll")]
  public static extern IntPtr LocalFree(IntPtr pointer);
  public static string[] Parse(string commandLine) {
    int argc;
    IntPtr argv = CommandLineToArgvW(commandLine, out argc);
    if (argv == IntPtr.Zero) { throw new System.ComponentModel.Win32Exception(); }
    try {
      string[] result = new string[argc];
      for (int index = 0; index < argc; index++) {
        IntPtr item = Marshal.ReadIntPtr(argv, index * IntPtr.Size);
        result[index] = Marshal.PtrToStringUni(item);
      }
      return result;
    } finally { LocalFree(argv); }
  }
}
'@
$rootPid = [int]$env:CCOS_OUTER_PID
$expected = [System.IO.Path]::GetFullPath($env:CCOS_APP_SERVER_EXE)
$deadline = [DateTime]::UtcNow.AddSeconds([double]$env:CCOS_INSPECTION_TIMEOUT)
do {
  $all = @(Get-CimInstance Win32_Process)
  $ids = [System.Collections.Generic.HashSet[int]]::new()
  [void]$ids.Add($rootPid)
  $changed = $true
  while ($changed) {
    $changed = $false
    foreach ($process in $all) {
      if ($ids.Contains([int]$process.ParentProcessId) -and -not $ids.Contains([int]$process.ProcessId)) {
        [void]$ids.Add([int]$process.ProcessId)
        $changed = $true
      }
    }
  }
  $matches = @($all | Where-Object {
    $_.ProcessId -ne $rootPid -and $ids.Contains([int]$_.ProcessId) -and
    $_.ExecutablePath -and
    [System.IO.Path]::GetFullPath($_.ExecutablePath).Equals($expected, [StringComparison]::OrdinalIgnoreCase) -and
    $_.CommandLine -match '(?:^|\s)app-server(?:\s|$)'
  })
  if ($matches.Count -eq 1) { break }
  Start-Sleep -Milliseconds 50
} while ([DateTime]::UtcNow -lt $deadline)
if ($matches.Count -ne 1) { throw "expected exactly one descendant App Server process" }
$owner = Invoke-CimMethod -InputObject $matches[0] -MethodName GetOwnerSid
[PSCustomObject]@{
  pid = [int]$matches[0].ProcessId
  parent_pid = [int]$matches[0].ParentProcessId
  sid = [string]$owner.Sid
  executable_path = [string]$matches[0].ExecutablePath
  argv = @([CcosNativeArgv]::Parse([string]$matches[0].CommandLine))
  command_line_sha256 = [Convert]::ToHexString(
    [Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes([string]$matches[0].CommandLine))
  ).ToLowerInvariant()
} | ConvertTo-Json -Compress
"""
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        environment = build_app_server_environment(worker_codex_home, executable)
        environment.update(
            {
                "CCOS_OUTER_PID": str(outer_pid),
                "CCOS_APP_SERVER_EXE": str(executable),
                "CCOS_INSPECTION_TIMEOUT": str(min(timeout, 30.0)),
            }
        )
        result = subprocess.run(
            [
                str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", script,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=environment,
            timeout=10,
        )
        if result.returncode == 0:
            try:
                evidence = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise ControllerProtocolError("App Server PID inspection returned malformed JSON") from exc
            sid = require_windows_sid(evidence.get("sid"), "actual App Server process SID")
            if sid != expected_worker_sid or sid == expected_broker_sid:
                raise ControllerAuthorizationError(
                    "actual App Server process is not the distinct restricted worker principal"
                )
            if normalize_binding("worktree", str(evidence.get("executable_path", ""))) != normalize_binding(
                "worktree", str(executable)
            ):
                raise ControllerAuthorizationError("inner App Server executable path differs")
            if not _plain_integer(evidence.get("pid")) or evidence["pid"] <= 0:
                raise ControllerProtocolError("inner App Server PID is invalid")
            argv = evidence.get("argv")
            if not isinstance(argv, list) or argv != expected_inner_command:
                raise ControllerAuthorizationError(
                    "observed inner App Server argv differs from the sealed fixed command"
                )
            evidence["argv_sha256"] = canonical_json_sha256(argv)
            evidence["sid"] = sid
            return evidence
        last_error = result.stderr.strip()[:300]
        time.sleep(0.05)
    raise ControllerProtocolError(f"cannot prove inner App Server PID/SID: {last_error}")


def inspect_worker_environment_acls(
    paths: Mapping[str, Path], *, expected_owner_sid: str, executable: Path,
) -> dict[str, Any]:
    """Bind the resolved worker-local directories to their actual Windows DACLs."""
    if os.name != "nt":
        raise ControllerAuthorizationError("worker environment DACL inspection requires Windows")
    if set(paths) != {"codex_home", "local_app_data", "temp"}:
        raise ControllerValidationError("worker environment ACL paths are incomplete")
    powershell = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        raise ControllerProtocolError("fixed Windows PowerShell path is unavailable")
    requested = [
        {"kind": kind, "path": str(path.resolve(strict=True))}
        for kind, path in sorted(paths.items())
    ]
    script = r"""
$ErrorActionPreference = 'Stop'
$items = @(ConvertFrom-Json -InputObject $env:CCOS_WORKER_ACL_PATHS_JSON)
$result = @()
foreach ($item in $items) {
  $resolved = [System.IO.Path]::GetFullPath([string]$item.path)
  $acl = Get-Acl -LiteralPath $resolved
  $owner = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value.ToUpperInvariant()
  $result += [PSCustomObject]@{
    kind = [string]$item.kind
    path = $resolved
    owner_sid = $owner
    sddl = $acl.GetSecurityDescriptorSddlForm(
      [System.Security.AccessControl.AccessControlSections]::All
    )
  }
}
@($result) | ConvertTo-Json -Compress -Depth 4
"""
    environment = build_app_server_environment(paths["codex_home"], executable)
    environment["CCOS_WORKER_ACL_PATHS_JSON"] = json.dumps(
        requested, separators=(",", ":"), sort_keys=True
    )
    result = subprocess.run(
        [
            str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-Command", script,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=environment,
        timeout=20,
    )
    if result.returncode != 0:
        raise ControllerProtocolError("fixed worker environment DACL inspection failed")
    try:
        records = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ControllerProtocolError("worker environment DACL inspection returned malformed JSON") from exc
    if not isinstance(records, list) or len(records) != len(requested):
        raise ControllerProtocolError("worker environment DACL evidence is incomplete")
    normalized: list[dict[str, str]] = []
    expected = {item["kind"]: normalize_binding("worktree", item["path"]) for item in requested}
    for record in records:
        if not isinstance(record, Mapping) or set(record) != {"kind", "path", "owner_sid", "sddl"}:
            raise ControllerProtocolError("worker environment DACL record is malformed")
        kind = str(record["kind"])
        if kind not in expected or normalize_binding("worktree", str(record["path"])) != expected[kind]:
            raise ControllerAuthorizationError("worker environment DACL path changed")
        owner = require_windows_sid(record["owner_sid"], f"{kind} owner SID")
        if owner != expected_owner_sid:
            raise ControllerAuthorizationError("worker environment root is not broker-owned")
        sddl = _nonempty(record["sddl"], f"{kind} SDDL", 65536)
        normalized.append(
            {
                "kind": kind,
                "path_sha256": hashlib.sha256(expected[kind].encode("utf-8")).hexdigest(),
                "owner_sid": owner,
                "sddl_sha256": hashlib.sha256(sddl.encode("utf-8")).hexdigest(),
            }
        )
    normalized.sort(key=lambda item: item["kind"])
    evidence = {"records": normalized, "owner_sid": expected_owner_sid}
    evidence["evidence_sha256"] = canonical_json_sha256(evidence)
    return evidence


class WindowsKillOnCloseJob:
    """Own a Windows Job Object that kills the complete wrapper tree on close."""

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        pass

    _EXTENDED_LIMIT_INFORMATION._fields_ = [
        ("BasicLimitInformation", _BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]

    def __init__(self) -> None:
        if os.name != "nt":
            raise ControllerAuthorizationError("Windows Job Object isolation requires Windows")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        self.kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise ControllerProtocolError("cannot create kill-on-close Windows Job Object")
        limits = self._EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel32.SetInformationJobObject(
            self.handle,
            self.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            self.close()
            raise ControllerProtocolError("cannot configure kill-on-close Windows Job Object")

    def assign(self, process: subprocess.Popen[str]) -> None:
        if not self.handle or not self.kernel32.AssignProcessToJobObject(
            self.handle, wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
        ):
            raise ControllerProtocolError("cannot assign App Server wrapper to the Job Object")

    @staticmethod
    def resume(process: subprocess.Popen[str]) -> None:
        """Resume a wrapper created suspended only after Job Object assignment."""
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        status = ntdll.NtResumeProcess(
            wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
        )
        if status != 0:
            raise ControllerProtocolError("cannot resume the Job-contained App Server wrapper")

    def close(self) -> None:
        handle, self.handle = getattr(self, "handle", None), None
        if handle:
            self.kernel32.CloseHandle(handle)


def _windows_process_is_running(pid: int) -> bool:
    if os.name != "nt":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def app_server_binary_evidence(
    executable: Path,
    *,
    expected_sha256: str,
    expected_version: str,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    observed_sha256 = file_sha256(executable)
    if observed_sha256 != require_snapshot_hash(expected_sha256):
        raise ControllerAuthorizationError("App Server binary digest differs from the sealed run")
    result = subprocess.run(
        [str(executable), "--version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="strict",
        env=dict(environment),
        timeout=30,
    )
    rendered = (result.stdout + "\n" + result.stderr).strip()
    versions = re.findall(r"\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.-]+)?", rendered)
    if result.returncode != 0 or len(versions) != 1:
        raise ControllerProtocolError("App Server binary did not return one parseable version")
    observed_version = versions[0]
    if observed_version != _nonempty(expected_version, "expected App Server version", 128):
        raise ControllerAuthorizationError("App Server version differs from the sealed run")
    return {
        "sha256": observed_sha256,
        "version": observed_version,
        "strict_config": True,
        "enabled_features": list(APP_SERVER_ENABLED_FEATURES),
        "disabled_features": list(APP_SERVER_DISABLED_FEATURES),
    }


def server_request_decision(method: str) -> tuple[dict[str, Any], bool, str]:
    """Return a fail-closed response, fatal flag, and stable audit decision."""
    if method in MUTATION_APPROVAL_METHODS:
        return {"result": {"decision": "decline"}}, False, "DECLINED"
    if method == "mcpServer/elicitation/request":
        return {"result": {"action": "decline"}}, True, "MCP_ELICITATION_DECLINED_FATAL"
    if method == "item/tool/call":
        return {
            "result": {
                "success": False,
                "contentItems": [
                    {"type": "inputText", "text": "proposal controller denies dynamic tools"}
                ],
            }
        }, True, "DYNAMIC_TOOL_REJECTED_FATAL"
    if method == "item/permissions/requestApproval":
        return {
            "result": {
                "permissions": {
                    "fileSystem": {"entries": [], "read": [], "write": []},
                    "network": {"enabled": False},
                },
                "scope": "turn",
                "strictAutoReview": True,
            }
        }, False, "NO_PERMISSIONS_TURN_ONLY"
    return {
        "error": {"code": -32000, "message": "request denied by proposal controller"}
    }, True, "UNKNOWN_SERVER_REQUEST_REJECTED_FATAL"


class AppServerTransport:
    """Exclusive JSONL stdio owner for one minimal App Server subprocess."""

    def __init__(
        self,
        *,
        executable: Path,
        expected_worker_sid: str,
        expected_broker_sid: str,
        expected_app_server_sha256: str,
        expected_app_server_version: str,
        expected_sandbox_profile_sha256: str,
        expected_environment_sha256: str,
        worker_codex_home: Path,
        cwd: Path,
        timeout: float = 180.0,
        inherited_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.executable = executable
        self.expected_worker_sid = expected_worker_sid
        self.expected_broker_sid = expected_broker_sid
        self.expected_app_server_sha256 = expected_app_server_sha256
        self.expected_app_server_version = expected_app_server_version
        self.expected_sandbox_profile_sha256 = require_snapshot_hash(
            expected_sandbox_profile_sha256
        )
        self.expected_environment_sha256 = require_snapshot_hash(expected_environment_sha256)
        self.worker_codex_home = worker_codex_home
        self.cwd = cwd
        self.timeout = timeout
        self.environment = build_app_server_environment(
            worker_codex_home, executable, inherited_environment
        )
        self.app_server_command = build_app_server_command(executable)
        self.app_server_command_sha256 = canonical_json_sha256(self.app_server_command)
        self.command, self.sandbox_state = build_sandboxed_app_server_command(
            executable, cwd=cwd, worker_codex_home=worker_codex_home
        )
        if canonical_json_sha256(self.sandbox_state) != self.expected_sandbox_profile_sha256:
            raise ControllerAuthorizationError("sandbox profile differs from the signed run context")
        if canonical_json_sha256(self.environment) != self.expected_environment_sha256:
            raise ControllerAuthorizationError("App Server environment differs from the signed run context")
        self.events: list[dict[str, Any]] = []
        self.audit: list[dict[str, Any]] = []
        self.responses: dict[str, dict[str, Any]] = {}
        self.inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.job: WindowsKillOnCloseJob | None = None
        self.inner_pid: int | None = None
        self.process_instance_sha256: str | None = None
        self.next_id = 1
        self.fatal_error: ControllerProtocolError | None = None

    def __enter__(self) -> "AppServerTransport":
        self.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def start(self) -> None:
        if self.process is not None:
            raise ControllerProtocolError("App Server transport is already started")
        _principal_name, current_sid = windows_identity()
        if current_sid != self.expected_broker_sid:
            raise ControllerAuthorizationError(
                "controller process principal differs from the sealed broker principal"
            )
        _prepare_worker_environment(self.worker_codex_home)
        self.environment = build_app_server_environment(
            self.worker_codex_home, self.executable, self.environment
        )
        environment_evidence = _worker_environment_evidence(
            self.environment, self.worker_codex_home
        )
        if environment_evidence["environment_values_sha256"] != self.expected_environment_sha256:
            raise ControllerAuthorizationError("resolved App Server environment digest changed")
        self.audit.append(
            {
                "event": "app_server_launch",
                "sandbox_launcher_command_sha256": canonical_json_sha256(self.command),
                "app_server_command_sha256": self.app_server_command_sha256,
                "environment_evidence": environment_evidence,
                "controller_key_exposed": False,
                "controller_principal_sid": current_sid,
                "controller_principal_matches_broker": True,
                "mcp_override": "empty",
                "shell_environment_inherit": "none",
            }
        )
        self.job = WindowsKillOnCloseJob()
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=self.environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_SUSPENDED", 0x00000004),
            )
            try:
                self.job.assign(self.process)
            except BaseException:
                self.process.kill()
                self.process.wait(timeout=10)
                raise
            self.job.resume(self.process)
            threading.Thread(target=self._reader, name="ccos-app-server-reader", daemon=True).start()
            identity = inspect_app_server_process_identity(
                self.process.pid,
                executable=self.executable,
                expected_worker_sid=self.expected_worker_sid,
                expected_broker_sid=self.expected_broker_sid,
                expected_inner_command=self.app_server_command,
                worker_codex_home=self.worker_codex_home,
            )
        except BaseException:
            self.close()
            raise
        self.inner_pid = int(identity["pid"])
        acl_evidence = inspect_worker_environment_acls(
            {
                "codex_home": self.worker_codex_home,
                "local_app_data": self.worker_codex_home / "local-app-data",
                "temp": self.worker_codex_home / "temp",
            },
            expected_owner_sid=self.expected_broker_sid,
            executable=self.executable,
        )
        self.process_instance_sha256 = canonical_json_sha256(
            {
                "outer_pid": self.process.pid,
                "app_server_pid": identity["pid"],
                "worker_principal_sid": identity["sid"],
                "app_server_sha256": self.expected_app_server_sha256,
                "command_line_sha256": identity["command_line_sha256"],
                "argv_sha256": identity["argv_sha256"],
            }
        )
        self.audit.append(
            {
                "event": "restricted_app_server_identity",
                "process_instance_sha256": self.process_instance_sha256,
                "worker_principal_sid": identity["sid"],
                "principal_distinct_from_broker": True,
                "app_server_sha256": self.expected_app_server_sha256,
                "app_server_version": self.expected_app_server_version,
                "command_line_sha256": identity["command_line_sha256"],
                "argv_sha256": identity["argv_sha256"],
                "argv_matches_sealed_command": True,
                "worker_environment_acl_evidence": acl_evidence,
                "kill_on_job_close": True,
            }
        )

    def _reader(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                message = {
                    "jsonrpc": "2.0",
                    "method": "controller/nonJsonOutput",
                    "params": {},
                }
            self.inbox.put(message)
        self.inbox.put(None)

    def _write(self, message: Mapping[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise ControllerProtocolError("App Server stdio is unavailable")
        self.process.stdin.write(
            json.dumps(dict(message), ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        self.process.stdin.flush()

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        if method != "initialized":
            raise ControllerAuthorizationError(f"client notification is not allowlisted: {method}")
        self._write({"jsonrpc": "2.0", "method": method, "params": dict(params or {})})

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if method not in CLIENT_REQUEST_METHODS:
            raise ControllerAuthorizationError(f"client request is not allowlisted: {method}")
        if self.fatal_error is not None:
            raise self.fatal_error
        request_id = str(self.next_id)
        self.next_id += 1
        self.audit.append(
            {
                "event": "client_request",
                "method": method,
                "allowlisted": True,
                "shell_command_requested": False,
            }
        )
        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": dict(params or {}),
            }
        )
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while request_id not in self.responses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ControllerProtocolError(f"App Server request timed out: {method}")
            self.pump_once(min(remaining, 1.0))
        response = self.responses.pop(request_id)
        if "error" in response:
            error = response.get("error")
            if not isinstance(error, Mapping):
                error = {"code": "unknown", "message": "malformed error"}
            raise ControllerProtocolError(
                f"App Server response error for {method}: "
                f"{error.get('code')}:{str(error.get('message', ''))[:200]}"
            )
        result = response.get("result")
        if result is None:
            return {}
        if not isinstance(result, Mapping):
            raise ControllerProtocolError(f"App Server result is not an object: {method}")
        return dict(result)

    def pump_once(self, timeout: float = 1.0) -> dict[str, Any] | None:
        if self.fatal_error is not None:
            raise self.fatal_error
        try:
            message = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return None
        if message is None:
            raise ControllerProtocolError("App Server stdout closed")
        if not isinstance(message, Mapping):
            raise ControllerProtocolError("App Server emitted a non-object message")
        if "method" not in message:
            self.responses[str(message.get("id"))] = dict(message)
            return dict(message)
        method = str(message.get("method"))
        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            if "id" in message:
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32602, "message": "invalid request parameters"},
                    }
                )
            raise ControllerProtocolError(f"App Server parameters are malformed: {method}")
        if "id" in message:
            decision, fatal, outcome = server_request_decision(method)
            response = {"jsonrpc": "2.0", "id": message["id"], **decision}
            self._write(response)
            self.audit.append(
                {"event": "server_request", "method": method, "outcome": outcome}
            )
            if fatal:
                self.fatal_error = ControllerProtocolError(
                    f"forbidden App Server request crossed the proposal boundary: {method}"
                )
                raise self.fatal_error
        else:
            event = {"method": method, "params": copy.deepcopy(dict(params))}
            self.events.append(event)
            item = params.get("item")
            if method == "item/fileChange/patchUpdated" or (
                method in {"item/started", "item/completed"}
                and isinstance(item, Mapping)
                and item.get("type") not in {
                    "userMessage", "reasoning", "agentMessage", "collabAgentToolCall"
                }
            ):
                self.fatal_error = ControllerProtocolError(
                    f"non-allowlisted item crossed the proposal-only boundary: {method}"
                )
                raise self.fatal_error
            if method == "controller/nonJsonOutput":
                self.fatal_error = ControllerProtocolError(
                    "App Server emitted non-JSON output on its protocol channel"
                )
                raise self.fatal_error
        return dict(message)

    def wait_turn_completed(
        self, thread_id: str, turn_id: str, timeout: float | None = None,
    ) -> Mapping[str, Any]:
        def match(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
            if event.get("method") != "turn/completed":
                return None
            params = event.get("params")
            if not isinstance(params, Mapping) or not isinstance(params.get("turn"), Mapping):
                raise ControllerProtocolError("turn/completed lacks a turn object")
            turn = params["turn"]
            if str(params.get("threadId")) != thread_id or str(turn.get("id")) != turn_id:
                return None
            if turn.get("status") != "completed":
                raise ControllerProtocolError(
                    f"native turn did not complete: {thread_id}:{turn_id}:{turn.get('status')}"
                )
            return turn

        for event in self.events:
            turn = match(event)
            if turn is not None:
                return turn
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            event = self.pump_once(min(deadline - time.monotonic(), 1.0))
            if event is None:
                continue
            turn = match(event)
            if turn is not None:
                return turn
        raise ControllerProtocolError(f"native turn timed out: {thread_id}:{turn_id}")

    def close(self) -> None:
        process = self.process
        self.process = None
        job, self.job = self.job, None
        inner_pid, self.inner_pid = self.inner_pid, None
        process_instance_sha256 = self.process_instance_sha256
        self.process_instance_sha256 = None
        try:
            if process is not None and process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if process is not None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if job is not None:
                job.close()
        if process is not None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                raise ControllerProtocolError("App Server wrapper survived Job Object closure") from exc
        if inner_pid is not None:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and _windows_process_is_running(inner_pid):
                time.sleep(0.05)
            if _windows_process_is_running(inner_pid):
                raise ControllerProtocolError("inner App Server survived Job Object closure")
            self.audit.append(
                {
                    "event": "app_server_process_tree_closed",
                    "process_instance_sha256": require_snapshot_hash(
                        str(process_instance_sha256 or "")
                    ),
                    "kill_on_job_close": True,
                    "descendant_exit_verified": True,
                }
            )


def _normalize_run_spec(
    raw: Mapping[str, Any],
    *,
    proposal_may_exist: bool = False,
    target_may_differ_from_baseline: bool = False,
) -> dict[str, Any]:
    fields = {
        "protocol_version",
        "schema_version",
        "case_id",
        "app_server_executable",
        "expected_app_server_sha256",
        "expected_app_server_version",
        "worker_codex_home",
        "runtime_working_directory",
        "model",
        "reasoning_effort",
        "implementation_instruction",
        "instruction_source_pins",
        "repository",
        "branch",
        "worktree",
        "base_head",
        "target_path",
        "baseline_sha256",
        "proposal_artifact_path",
        "worker_principal_sid",
        "worker_offline_principal_sid",
        "sandbox_group_principal_sid",
        "broker_principal_sid",
        "expected_schema_file_count",
        "expected_schema_tree_sha256",
        "grant_id",
        "operation_id",
    }
    if not isinstance(raw, Mapping) or set(raw) != fields:
        raise ControllerValidationError("run spec must use the fixed controller-v1 schema")
    if (
        raw.get("protocol_version") != CONTROLLER_RUN_PROTOCOL_VERSION
        or raw.get("schema_version") != 1
    ):
        raise ControllerValidationError("run spec protocol or schema version is unsupported")
    executable, executable_text = _absolute_path(
        raw.get("app_server_executable"), "App Server executable", must_exist=True
    )
    if not executable.is_file():
        raise ControllerValidationError("App Server executable must be a regular file")
    codex_home, codex_home_text = _absolute_path(
        raw.get("worker_codex_home"), "worker Codex home", must_exist=True
    )
    runtime_root, runtime_root_text = _absolute_path(
        raw.get("runtime_working_directory"), "runtime working directory", must_exist=True
    )
    worktree, worktree_text = _absolute_path(raw.get("worktree"), "worktree", must_exist=True)
    if not codex_home.is_dir() or not runtime_root.is_dir() or not worktree.is_dir():
        raise ControllerValidationError("Codex home, runtime root, and worktree must be directories")
    if runtime_root != worktree:
        raise ControllerValidationError("proposal runtime must be the exact authorized worktree")
    if path_contains_link_or_reparse(worktree):
        raise ControllerAuthorizationError("proposal worktree traverses a link or reparse point")
    target_path = normalize_action_path(raw.get("target_path"))
    target = worktree.joinpath(*PurePosixPath(target_path).parts)
    if not path_is_within(target, worktree) or not target.is_file() or target.is_symlink():
        raise ControllerAuthorizationError("proposal target must be an existing direct regular file")
    if path_contains_link_or_reparse(target, stop=worktree):
        raise ControllerAuthorizationError("proposal target traverses a link or reparse point")
    baseline_sha256 = require_snapshot_hash(str(raw.get("baseline_sha256", "")))
    if not target_may_differ_from_baseline and file_sha256(target) != baseline_sha256:
        raise ControllerAuthorizationError("proposal target differs from the exact baseline")
    proposal, proposal_text = _absolute_path(
        raw.get("proposal_artifact_path"), "proposal artifact path", must_exist=False
    )
    if path_is_within(proposal, worktree):
        raise ControllerAuthorizationError("proposal artifact must be outside the governed worktree")
    if not proposal.parent.is_dir() or path_contains_link_or_reparse(proposal.parent):
        raise ControllerAuthorizationError("proposal artifact parent is unavailable or linked")
    if proposal.exists() and not proposal_may_exist:
        raise ControllerAuthorizationError("proposal artifact path must be absent before the run")
    protected_paths = (worktree, proposal.parent, executable.parent)
    if any(
        path_is_within(codex_home, protected) or path_is_within(protected, codex_home)
        for protected in protected_paths
    ):
        raise ControllerAuthorizationError(
            "worker-writable Codex home overlaps a protected or executable root"
        )
    if path_contains_link_or_reparse(codex_home):
        raise ControllerAuthorizationError("worker Codex home traverses a link or reparse point")
    worker_sid = require_windows_sid(raw.get("worker_principal_sid"), "worker principal SID")
    offline_sid = require_windows_sid(
        raw.get("worker_offline_principal_sid"), "offline worker principal SID"
    )
    sandbox_group_sid = require_windows_sid(
        raw.get("sandbox_group_principal_sid"), "sandbox group principal SID"
    )
    broker_sid = require_windows_sid(raw.get("broker_principal_sid"), "broker principal SID")
    if len({worker_sid, offline_sid, sandbox_group_sid, broker_sid}) != 4:
        raise ControllerAuthorizationError(
            "Online, Offline, sandbox-group, and broker principals must be distinct"
        )
    schema_file_count = raw.get("expected_schema_file_count")
    if (
        not _plain_integer(schema_file_count)
        or schema_file_count <= 0
    ):
        raise ControllerValidationError("expected schema file count must be positive")
    schema_tree_sha256 = require_snapshot_hash(
        str(raw.get("expected_schema_tree_sha256", ""))
    )
    raw_instruction_pins = raw.get("instruction_source_pins")
    if not isinstance(raw_instruction_pins, list):
        raise ControllerValidationError("instruction_source_pins must be an array")
    instruction_pins: list[dict[str, str]] = []
    observed_instruction_paths: set[str] = set()
    for item in raw_instruction_pins:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
            raise ControllerValidationError("instruction source pin uses an unexpected schema")
        path, path_text = _absolute_path(item.get("path"), "instruction source path", must_exist=True)
        if not path.is_file() or not path_is_within(path, worktree):
            raise ControllerAuthorizationError("instruction source must be a file inside the worktree")
        digest = require_snapshot_hash(str(item.get("sha256", "")))
        if file_sha256(path) != digest or path_text in observed_instruction_paths:
            raise ControllerAuthorizationError("instruction source pin is stale or duplicated")
        instruction_pins.append({"path": path_text, "sha256": digest})
        observed_instruction_paths.add(path_text)
    instruction_pins.sort(key=lambda item: item["path"])
    launch_environment = build_app_server_environment(codex_home, executable)
    binary_evidence = app_server_binary_evidence(
        executable,
        expected_sha256=str(raw.get("expected_app_server_sha256", "")),
        expected_version=str(raw.get("expected_app_server_version", "")),
        environment=launch_environment,
    )
    _sandbox_command, sandbox_state = build_sandboxed_app_server_command(
        executable, cwd=runtime_root, worker_codex_home=codex_home
    )
    app_server_environment_sha256 = canonical_json_sha256(launch_environment)
    return {
        "protocol_version": CONTROLLER_RUN_PROTOCOL_VERSION,
        "schema_version": 1,
        "case_id": canonical_case_id(str(raw.get("case_id", ""))),
        "app_server_executable": executable_text,
        "expected_app_server_sha256": binary_evidence["sha256"],
        "expected_app_server_version": binary_evidence["version"],
        "app_server_binary_evidence": binary_evidence,
        "worker_codex_home": codex_home_text,
        "runtime_working_directory": runtime_root_text,
        "model": _nonempty(raw.get("model"), "model", 256),
        "reasoning_effort": _nonempty(raw.get("reasoning_effort"), "reasoning effort", 64),
        "implementation_instruction": _nonempty(
            raw.get("implementation_instruction"), "implementation instruction", 65536
        ),
        "instruction_source_pins": instruction_pins,
        "repository": normalize_repo_url(str(raw.get("repository", ""))),
        "branch": normalize_binding("branch", str(raw.get("branch", ""))),
        "worktree": worktree_text,
        "base_head": require_sha(str(raw.get("base_head", "")), "base head"),
        "target_path": target_path,
        "baseline_sha256": baseline_sha256,
        "proposal_artifact_path": proposal_text,
        "worker_principal_sid": worker_sid,
        "worker_offline_principal_sid": offline_sid,
        "sandbox_group_principal_sid": sandbox_group_sid,
        "broker_principal_sid": broker_sid,
        "expected_schema_file_count": schema_file_count,
        "expected_schema_tree_sha256": schema_tree_sha256,
        "sandbox_profile_sha256": canonical_json_sha256(sandbox_state),
        "app_server_environment_sha256": app_server_environment_sha256,
        "grant_id": require_stable_id(raw.get("grant_id"), "grant id"),
        "operation_id": require_stable_id(raw.get("operation_id"), "operation id"),
    }


def _thread_from_response(response: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    if not isinstance(response, Mapping) or set(response) != {"thread"}:
        raise ControllerProtocolError(f"{label} must contain exactly one thread")
    thread = response.get("thread")
    required = {"id", "cwd", "source", "turns"}
    if not isinstance(thread, Mapping) or not required.issubset(thread):
        raise ControllerProtocolError(f"{label} thread lacks required identity fields")
    if (
        not isinstance(thread.get("id"), str)
        or not thread.get("id")
        or not isinstance(thread.get("cwd"), str)
        or not isinstance(thread.get("turns"), list)
    ):
        raise ControllerProtocolError(f"{label} thread identity fields are invalid")
    return thread


def _thread_list(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if not isinstance(response, Mapping) or not isinstance(response.get("data"), list):
        raise ControllerProtocolError("thread/list response lacks a data array")
    if response.get("nextCursor") not in {None, ""}:
        raise ControllerProtocolError("thread/list pagination is not accepted for identity binding")
    result: list[Mapping[str, Any]] = []
    for item in response["data"]:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str) or not item["id"]:
            raise ControllerProtocolError("thread/list returned an invalid thread identity")
        result.append(item)
    return result


def _native_children(transport: Transport, parent_thread_id: str) -> dict[str, Mapping[str, Any]]:
    response = transport.request(
        "thread/list",
        {
            "parentThreadId": parent_thread_id,
            "sourceKinds": ["subAgentThreadSpawn"],
            "limit": 100,
            "sortDirection": "asc",
        },
    )
    children = _thread_list(response)
    result = {str(item["id"]): item for item in children}
    if len(result) != len(children):
        raise ControllerProtocolError("thread/list duplicated a child identity")
    return result


def _turn_id(response: Mapping[str, Any]) -> str:
    if not isinstance(response, Mapping) or set(response) != {"turn"}:
        raise ControllerProtocolError("turn/start response must contain exactly one turn")
    turn = response.get("turn")
    if (
        not isinstance(turn, Mapping)
        or not isinstance(turn.get("id"), str)
        or not turn["id"]
    ):
        raise ControllerProtocolError("turn/start response lacks a native turn ID")
    return str(turn["id"])


def _collaboration_receivers(
    events: list[dict[str, Any]], parent_thread_id: str, parent_turn_id: str,
) -> set[str]:
    records: dict[tuple[str, str], Mapping[str, Any]] = {}

    def inspect(item: Any, event_thread: Any, event_turn: Any) -> None:
        if not isinstance(item, Mapping) or item.get("type") != "collabAgentToolCall":
            return
        if item.get("tool") != "spawnAgent":
            raise ControllerProtocolError("parent used a non-spawn collaboration operation")
        if event_thread != parent_thread_id or event_turn != parent_turn_id:
            raise ControllerProtocolError("native spawn event escaped the exact parent turn")
        if item.get("senderThreadId") != parent_thread_id:
            raise ControllerProtocolError("native spawn sender differs from the exact parent")
        receivers = item.get("receiverThreadIds")
        if not isinstance(receivers, list) or len(receivers) != 1:
            raise ControllerProtocolError("native spawn must expose exactly one receiver")
        receiver = receivers[0]
        item_id = item.get("id")
        if not isinstance(receiver, str) or not receiver or not isinstance(item_id, str) or not item_id:
            raise ControllerProtocolError("native spawn identity fields are invalid")
        key = (item_id, receiver)
        existing = records.get(key)
        if existing is not None and existing.get("senderThreadId") != item.get("senderThreadId"):
            raise ControllerProtocolError("native spawn lifecycle records conflict")
        records[key] = item

    for event in events:
        params = event.get("params")
        if not isinstance(params, Mapping):
            continue
        if event.get("method") in {"item/started", "item/completed"}:
            inspect(params.get("item"), params.get("threadId"), params.get("turnId"))
        elif event.get("method") == "turn/completed" and isinstance(params.get("turn"), Mapping):
            turn = params["turn"]
            for item in turn.get("items") or []:
                inspect(item, params.get("threadId"), turn.get("id"))
    receivers = {receiver for _, receiver in records}
    if len(receivers) != 1 or len({item_id for item_id, _ in records}) != 1:
        raise ControllerProtocolError("exactly one native collaboration spawn must be observed")
    return receivers


def _identity_record(
    thread: Mapping[str, Any], *, parent_thread_id: str | None, expected_agent_path: str,
) -> dict[str, Any]:
    thread_id = normalize_binding("thread", str(thread.get("id", "")))
    source = thread.get("source")
    if parent_thread_id is None:
        if (
            isinstance(source, Mapping)
            and isinstance(source.get("subAgent"), Mapping)
            and isinstance(source["subAgent"].get("thread_spawn"), Mapping)
        ):
            raise ControllerProtocolError("canonical parent is itself a spawned child")
        spawn = None
        agent_path = expected_agent_path
        depth = 0
    else:
        sub_agent = source.get("subAgent") if isinstance(source, Mapping) else None
        spawn = sub_agent.get("thread_spawn") if isinstance(sub_agent, Mapping) else None
        if not isinstance(spawn, Mapping):
            raise ControllerProtocolError("child lacks source.subAgent.thread_spawn evidence")
        if (
            spawn.get("parent_thread_id") != parent_thread_id
            or spawn.get("agent_path") != expected_agent_path
            or spawn.get("depth") != 1
        ):
            raise ControllerProtocolError("native child source differs from parent, path, or depth")
        agent_path = str(spawn["agent_path"])
        depth = 1
    stable = {
        "protocol_version": IDENTITY_EVIDENCE_PROTOCOL_VERSION,
        "schema_version": 1,
        "thread_id": thread_id,
        "parent_thread_id": parent_thread_id,
        "agent_path": agent_path,
        "depth": depth,
        "cwd": normalize_binding("worktree", str(thread.get("cwd", ""))),
        "source_sha256": canonical_json_sha256(source),
        "created_at": thread.get("createdAt"),
        "cli_version": thread.get("cliVersion"),
        "model_provider": thread.get("modelProvider"),
    }
    stable["identity_evidence_sha256"] = canonical_json_sha256(stable)
    return stable


def _assert_no_mutation_items(
    turn: Mapping[str, Any], label: str, *, allow_parent_spawn: bool = False,
) -> None:
    items = turn.get("items")
    if not isinstance(items, list):
        raise ControllerProtocolError(f"{label} turn lacks an item list")
    agent_message_count = 0
    collaboration_count = 0
    for item in items:
        if not isinstance(item, Mapping):
            raise ControllerProtocolError(f"{label} emitted a malformed turn item")
        item_type = item.get("type")
        if item_type in {"userMessage", "reasoning", "agentMessage"}:
            if item_type == "agentMessage":
                agent_message_count += 1
            continue
        if item_type == "collabAgentToolCall" and allow_parent_spawn:
            collaboration_count += 1
            if item.get("tool") != "spawnAgent" or item.get("status") != "completed":
                raise ControllerAuthorizationError(
                    f"{label} collaboration item is not one completed spawnAgent"
                )
            continue
        raise ControllerAuthorizationError(
            f"{label} produced a non-allowlisted turn item: {item_type}"
        )
    if allow_parent_spawn:
        if collaboration_count != 1:
            raise ControllerAuthorizationError(
                f"{label} must contain exactly one completed spawnAgent item"
            )
    elif collaboration_count or agent_message_count != 1:
        raise ControllerAuthorizationError(
            f"{label} child turn must contain exactly one final agent message"
        )


def _agent_message(turn: Mapping[str, Any]) -> str:
    _assert_no_mutation_items(turn, "implementation")
    messages = [
        item.get("text")
        for item in turn.get("items") or []
        if isinstance(item, Mapping)
        and item.get("type") == "agentMessage"
        and isinstance(item.get("text"), str)
    ]
    if len(messages) != 1:
        raise ControllerProtocolError("implementation turn must contain exactly one agent message")
    return messages[0]


def _completed_proposal(
    text: str, spec: Mapping[str, Any], thread_id: str, turn_id: str,
) -> tuple[dict[str, Any], bytes, str]:
    if text != text.strip() or text.startswith("```"):
        raise ControllerAuthorizationError("implementation final output must be raw strict JSON")
    try:
        proposal = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ControllerAuthorizationError("implementation final output is not strict JSON") from exc
    fields = {
        "protocol_version",
        "schema_version",
        "completion_state",
        "case_id",
        "operation_id",
        "base_head",
        "target_path",
        "replacement_text",
    }
    if not isinstance(proposal, Mapping) or set(proposal) != fields:
        raise ControllerAuthorizationError("implementation proposal uses an unexpected schema")
    if (
        proposal.get("protocol_version") != PROPOSAL_PROTOCOL_VERSION
        or proposal.get("schema_version") != 1
        or proposal.get("completion_state") != "COMPLETED"
        or proposal.get("case_id") != spec["case_id"]
        or proposal.get("operation_id") != spec["operation_id"]
        or proposal.get("base_head") != spec["base_head"]
        or proposal.get("target_path") != spec["target_path"]
    ):
        raise ControllerAuthorizationError("implementation proposal differs from the exact action context")
    replacement = proposal.get("replacement_text")
    if not isinstance(replacement, str) or "\x00" in replacement:
        raise ControllerAuthorizationError("replacement_text must be NUL-free UTF-8 text")
    replacement_bytes = replacement.encode("utf-8", errors="strict")
    if len(replacement_bytes) > MAX_REPLACEMENT_BYTES:
        raise ControllerAuthorizationError("implementation proposal exceeds the bounded primitive")
    evidence = {
        "protocol_version": "ccos-native-completed-turn-evidence-v1",
        "schema_version": 1,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "completion_state": "COMPLETED",
        "proposal_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "proposal_text_size": len(text.encode("utf-8")),
        "replacement_sha256": hashlib.sha256(replacement_bytes).hexdigest(),
        "replacement_size": len(replacement_bytes),
    }
    return dict(proposal), replacement_bytes, canonical_json_sha256(evidence)


def _write_new_proposal(path: Path, content: bytes) -> dict[str, Any]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("proposal artifact write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
        raise ControllerAuthorizationError("proposal artifact identity is not a single regular file")
    return {
        "path": normalize_binding("worktree", str(path.resolve(strict=True))),
        "sha256": file_sha256(path),
        "size": path.stat().st_size,
        "nlink": path.stat().st_nlink,
    }


class ProposalController:
    """Deterministic policy and evidence logic over an App Server transport."""

    def __init__(
        self,
        spec: Mapping[str, Any],
        *,
        clock: Callable[[], str] = utc_now,
        nonce_factory: Callable[[], str] = lambda: secrets.token_hex(24),
    ) -> None:
        self.spec = _normalize_run_spec(spec)
        self.clock = clock
        self.nonce_factory = nonce_factory
        self.grant_emitted = False

    def initialize(self, transport: Transport) -> dict[str, Any]:
        result = transport.request(
            "initialize",
            {
                "clientInfo": {"name": "ccos-proposal-controller", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        if not isinstance(result, Mapping):
            raise ControllerProtocolError("initialize returned an invalid response")
        transport.notify("initialized", {})
        mcp = transport.request("mcpServerStatus/list", {})
        if (
            not isinstance(mcp.get("data"), list)
            or mcp["data"]
            or mcp.get("nextCursor") not in {None, ""}
        ):
            raise ControllerAuthorizationError("App Server reported an enabled MCP surface")
        hooks = transport.request("hooks/list", {})
        entries = hooks.get("data")
        if not isinstance(entries, list) or hooks.get("nextCursor") not in {None, ""}:
            raise ControllerProtocolError("hooks/list returned an invalid response")
        for entry in entries:
            if (
                not isinstance(entry, Mapping)
                or entry.get("hooks") not in (None, [])
                or entry.get("errors") not in (None, [])
                or entry.get("warnings") not in (None, [])
            ):
                raise ControllerAuthorizationError("App Server reported hooks or hook errors")
        return {
            "client_capabilities": {"experimentalApi": True},
            "mcp_server_count": 0,
            "hook_count": 0,
            "dynamic_tools": [],
        }

    def _thread_start_params(self) -> dict[str, Any]:
        spec = self.spec
        return {
            "cwd": spec["runtime_working_directory"],
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "approvalsReviewer": "user",
            "dynamicTools": [],
            "ephemeral": False,
            "model": spec["model"],
            "config": {"model_reasoning_effort": spec["reasoning_effort"]},
            "runtimeWorkspaceRoots": [spec["runtime_working_directory"]],
            "selectedCapabilityRoots": [],
            "environments": [],
            "developerInstructions": PARENT_DEVELOPER_INSTRUCTIONS,
        }

    def _turn_params(self, thread_id: str, prompt: str) -> dict[str, Any]:
        spec = self.spec
        return {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
            "approvalPolicy": "never",
            "approvalsReviewer": "user",
            "environments": [],
            "runtimeWorkspaceRoots": [spec["runtime_working_directory"]],
            "model": spec["model"],
            "effort": spec["reasoning_effort"],
            "collaborationMode": {
                "mode": "default",
                "settings": {
                    "model": spec["model"],
                    "reasoning_effort": spec["reasoning_effort"],
                    "developer_instructions": PARENT_DEVELOPER_INSTRUCTIONS,
                },
            },
        }

    def start_parent(self, transport: Transport) -> tuple[str, dict[str, Any]]:
        response = transport.request("thread/start", self._thread_start_params())
        required = {
            "thread", "approvalPolicy", "approvalsReviewer", "sandbox", "cwd", "model",
            "modelProvider", "reasoningEffort", "runtimeWorkspaceRoots",
            "instructionSources", "activePermissionProfile",
        }
        if not required.issubset(response):
            raise ControllerProtocolError("thread/start response lacks the sealed runtime boundary")
        sandbox = response.get("sandbox")
        if (
            response.get("approvalPolicy") != "never"
            or response.get("approvalsReviewer") != "user"
            or response.get("model") != self.spec["model"]
            or response.get("reasoningEffort") != self.spec["reasoning_effort"]
            or not isinstance(response.get("modelProvider"), str)
            or not response.get("modelProvider")
            or normalize_binding("worktree", str(response.get("cwd", "")))
            != self.spec["runtime_working_directory"]
            or response.get("runtimeWorkspaceRoots")
            != [self.spec["runtime_working_directory"]]
            or not isinstance(sandbox, Mapping)
            or sandbox.get("type") != "readOnly"
            or sandbox.get("networkAccess", False) is not False
        ):
            raise ControllerAuthorizationError("thread/start did not preserve the sealed boundary")
        active_profile = response.get("activePermissionProfile")
        if (
            active_profile is not None
            and (
                not isinstance(active_profile, Mapping)
                or active_profile.get("id") not in {":read-only", "read-only"}
            )
        ):
            raise ControllerAuthorizationError("thread/start active permission profile is not read-only")
        instruction_sources = response.get("instructionSources")
        if not isinstance(instruction_sources, list) or any(
            not isinstance(item, str) for item in instruction_sources
        ):
            raise ControllerProtocolError("thread/start instructionSources is malformed")
        observed_sources = []
        for item in instruction_sources:
            source, source_text = _absolute_path(item, "active instruction source", must_exist=True)
            observed_sources.append({"path": source_text, "sha256": file_sha256(source)})
        observed_sources.sort(key=lambda item: item["path"])
        if observed_sources != self.spec["instruction_source_pins"]:
            raise ControllerAuthorizationError("thread/start loaded an unsealed instruction source")
        thread = response.get("thread")
        if not isinstance(thread, Mapping) or not isinstance(thread.get("id"), str) or not thread["id"]:
            raise ControllerProtocolError("thread/start did not return a native parent ID")
        parent_id = str(thread["id"])
        read = _thread_from_response(
            transport.request("thread/read", {"threadId": parent_id, "includeTurns": True}),
            "parent thread/read",
        )
        if read["id"] != parent_id:
            raise ControllerProtocolError("parent thread/start and thread/read identities conflict")
        identity = _identity_record(read, parent_thread_id=None, expected_agent_path="/root")
        return parent_id, identity

    def _implementation_task(self) -> str:
        spec = self.spec
        baseline = Path(spec["worktree"]).joinpath(
            *PurePosixPath(spec["target_path"]).parts
        ).read_text(encoding="utf-8", errors="strict")
        schema = {
            "protocol_version": PROPOSAL_PROTOCOL_VERSION,
            "schema_version": 1,
            "completion_state": "COMPLETED",
            "case_id": spec["case_id"],
            "operation_id": spec["operation_id"],
            "base_head": spec["base_head"],
            "target_path": spec["target_path"],
            "replacement_text": "<complete replacement UTF-8 text>",
        }
        return (
            "You are a proposal-only implementation child. Do not call any tool, run any command, "
            "change any file, request approval, spawn a child, or claim runtime authority. Produce "
            "one complete replacement proposal for the exact existing file. Your final turn must be "
            "COMPLETED and contain exactly one agent message consisting only of a raw JSON object "
            "with this exact field set and fixed context: "
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            + "\nUser-authorized implementation instruction:\n"
            + spec["implementation_instruction"]
            + "\nExact baseline text follows between markers. Preserve bytes intentionally.\n"
            + "<CCOS_BASELINE>\n"
            + baseline
            + "\n</CCOS_BASELINE>"
        )

    def _role_task(self, name: str) -> str:
        if name == "incomplete":
            incomplete = {
                "protocol_version": "ccos-incomplete-child-result-v1",
                "schema_version": 1,
                "completion_state": "INCOMPLETE",
                "case_id": self.spec["case_id"],
                "proposal": None,
                "action": None,
            }
            outcome = (
                "Return exactly this raw JSON object with no markdown or extra text: "
                + json.dumps(incomplete, separators=(",", ":"), sort_keys=True)
            )
        elif name == UNKNOWN_CHILD_NAME:
            outcome = "Return exactly UNKNOWN_COMPLETE. You have no canonical role."
        else:
            outcome = f"Return exactly READ_ONLY_{name.upper()}_COMPLETE."
        return (
            "You are a proposal-only identity probe. Do not call tools, run commands, change files, "
            "request approval, spawn descendants, or claim action authority. " + outcome
        )

    def _incomplete_result_evidence(
        self, turn: Mapping[str, Any], thread_id: str,
    ) -> dict[str, Any]:
        text = _agent_message(turn)
        if text != text.strip() or text.startswith("```"):
            raise ControllerAuthorizationError("incomplete child result must be raw JSON")
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ControllerAuthorizationError("incomplete child result is not strict JSON") from exc
        expected = {
            "protocol_version": "ccos-incomplete-child-result-v1",
            "schema_version": 1,
            "completion_state": "INCOMPLETE",
            "case_id": self.spec["case_id"],
            "proposal": None,
            "action": None,
        }
        if result != expected:
            raise ControllerAuthorizationError("incomplete child result differs from the fixed contract")
        evidence = {
            "thread_id": thread_id,
            "turn_id": str(turn["id"]),
            "completion_state": "INCOMPLETE",
            "proposal_count": 0,
            "action_count": 0,
            "result_sha256": canonical_json_sha256(result),
        }
        evidence["evidence_sha256"] = canonical_json_sha256(evidence)
        return evidence

    def _spawn_child(
        self, transport: Transport, parent_id: str, name: str, task: str,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
        before = set(_native_children(transport, parent_id))
        event_index = len(transport.events)
        prompt = (
            f"Spawn exactly one direct child named {name}. Give it only the following read-only "
            f"proposal task. Do not spawn any other child and do not call any non-collaboration tool.\n{task}"
        )
        turn_id = _turn_id(transport.request("turn/start", self._turn_params(parent_id, prompt)))
        parent_turn = transport.wait_turn_completed(parent_id, turn_id)
        _assert_no_mutation_items(parent_turn, "parent", allow_parent_spawn=True)
        receivers = _collaboration_receivers(
            transport.events[event_index:], parent_id, turn_id
        )
        receiver = next(iter(receivers))
        after = _native_children(transport, parent_id)
        if set(after) - before != {receiver} or before - set(after):
            raise ControllerProtocolError("thread/list delta differs from the one native receiver")
        read = _thread_from_response(
            transport.request("thread/read", {"threadId": receiver, "includeTurns": True}),
            f"{name} thread/read",
        )
        if read["id"] != receiver:
            raise ControllerProtocolError("collaboration receiver and thread/read identity conflict")
        identity = _identity_record(
            read, parent_thread_id=parent_id, expected_agent_path=f"/root/{name}"
        )
        return read, identity, turn_id

    @staticmethod
    def _one_child_turn(thread: Mapping[str, Any], label: str) -> Mapping[str, Any]:
        turns = thread.get("turns")
        if not isinstance(turns, list) or len(turns) != 1 or not isinstance(turns[0], Mapping):
            raise ControllerProtocolError(f"{label} must contain exactly one native turn")
        turn = turns[0]
        if not isinstance(turn.get("id"), str) or not turn["id"]:
            raise ControllerProtocolError(f"{label} native turn lacks an ID")
        return turn

    def _wait_child_terminal(
        self,
        transport: Transport,
        thread_id: str,
        label: str,
        *,
        timeout: float = 180.0,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            thread = _thread_from_response(
                transport.request(
                    "thread/read", {"threadId": thread_id, "includeTurns": True}
                ),
                f"{label} terminal thread/read",
            )
            turns = thread.get("turns")
            if not turns:
                time.sleep(0.05)
                continue
            turn = self._one_child_turn(thread, label)
            status = turn.get("status")
            if status == "completed":
                _assert_no_mutation_items(turn, label)
                return thread, turn
            if status in {"failed", "cancelled", "canceled", "interrupted"}:
                raise ControllerAuthorizationError(
                    f"{label} native turn terminated without completion: {status}"
                )
            if status not in {"inProgress", "in_progress", "running"}:
                raise ControllerProtocolError(f"{label} native turn has unknown status: {status}")
            time.sleep(0.05)
        raise ControllerProtocolError(f"{label} native child turn timed out")

    @staticmethod
    def _require_completed_child_turn(
        turn: Mapping[str, Any], label: str,
    ) -> Mapping[str, Any]:
        if turn.get("status") != "completed":
            raise ControllerAuthorizationError(
                f"{label} native turn is not COMPLETED: {turn.get('status')}"
            )
        _assert_no_mutation_items(turn, label)
        return turn

    def _build_bundle(
        self,
        *,
        parent_identity: Mapping[str, Any],
        child_records: list[dict[str, Any]],
        proposal_bytes: bytes,
        native_turn_evidence_sha256: str,
        implementation_turn_id: str,
    ) -> dict[str, Any]:
        if self.grant_emitted:
            raise ControllerAuthorizationError("controller permits only one grant bundle per run")
        self.grant_emitted = True
        spec = self.spec
        proposal_path = Path(spec["proposal_artifact_path"])
        artifact = _write_new_proposal(proposal_path, proposal_bytes)
        replacement_sha256 = artifact["sha256"]
        implementation = [
            item for item in child_records if item["controller_assigned_role"] == "implementer_child"
        ]
        if len(implementation) != 1:
            raise ControllerAuthorizationError("exactly one canonical implementation child is required")
        actor = implementation[0]
        issued_at = self.clock()
        receipt_body = {
            "protocol_version": CONTROLLER_RECEIPT_PROTOCOL_VERSION,
            "schema_version": 2,
            "case_id": spec["case_id"],
            "grant_id": spec["grant_id"],
            "actor_thread_id": actor["thread_id"],
            "actor_turn_id": implementation_turn_id,
            "action": "implementation",
            "operation_id": spec["operation_id"],
            "base_head": spec["base_head"],
            "target_path": spec["target_path"],
            "proposal_sha256": replacement_sha256,
            "proposal_size": artifact["size"],
            "completion_state": "COMPLETED",
            "native_turn_evidence_sha256": native_turn_evidence_sha256,
            "repository": spec["repository"],
            "branch": spec["branch"],
            "worktree": spec["worktree"],
            "baseline_sha256": spec["baseline_sha256"],
            "worker_online_principal_sid": spec["worker_principal_sid"],
            "worker_offline_principal_sid": spec["worker_offline_principal_sid"],
            "sandbox_group_principal_sid": spec["sandbox_group_principal_sid"],
            "broker_principal_sid": spec["broker_principal_sid"],
            "app_server_sha256": spec["expected_app_server_sha256"],
            "app_server_version": spec["expected_app_server_version"],
            "app_server_executable_path": spec["app_server_executable"],
            "worker_runtime_root": spec["worker_codex_home"],
            "schema_file_count": spec["expected_schema_file_count"],
            "schema_tree_sha256": spec["expected_schema_tree_sha256"],
            "sandbox_profile_sha256": spec["sandbox_profile_sha256"],
            "app_server_environment_sha256": spec["app_server_environment_sha256"],
            "issued_at": issued_at,
            "nonce": require_stable_id(self.nonce_factory(), "controller receipt nonce"),
        }
        grant_core = {
            "protocol_version": ACTION_GRANT_PROTOCOL_VERSION,
            "schema_version": 1,
            "grant_id": spec["grant_id"],
            "actor_thread_id": actor["thread_id"],
            "actor_turn_id": implementation_turn_id,
            "native_turn_evidence_sha256": native_turn_evidence_sha256,
            "operation_id": spec["operation_id"],
            "action": "implementation",
            "operation": "replace_existing_file_v1",
            "repository": spec["repository"],
            "branch": spec["branch"],
            "worktree": spec["worktree"],
            "base_head": spec["base_head"],
            "target_path": spec["target_path"],
            "baseline_sha256": spec["baseline_sha256"],
            "replacement_sha256": replacement_sha256,
            "proposal_artifact_path": artifact["path"],
            "proposal_size": artifact["size"],
            "worker_principal_sid": spec["worker_principal_sid"],
            "model_worker_principal_sid": spec["worker_offline_principal_sid"],
            "sandbox_group_principal_sid": spec["sandbox_group_principal_sid"],
            "denied_principal_sids": [
                spec["worker_principal_sid"],
                spec["worker_offline_principal_sid"],
                spec["sandbox_group_principal_sid"],
            ],
            "broker_principal_sid": spec["broker_principal_sid"],
            "app_server_sha256": spec["expected_app_server_sha256"],
            "app_server_version": spec["expected_app_server_version"],
            "app_server_executable_path": spec["app_server_executable"],
            "worker_runtime_root": spec["worker_codex_home"],
            "schema_file_count": spec["expected_schema_file_count"],
            "schema_tree_sha256": spec["expected_schema_tree_sha256"],
            "sandbox_profile_sha256": spec["sandbox_profile_sha256"],
            "app_server_environment_sha256": spec["app_server_environment_sha256"],
        }
        actor_bindings = []
        for identity, role in [(parent_identity, "parent")] + [
            (item["identity"], item["controller_assigned_role"])
            for item in child_records
            if item["controller_assigned_role"] != UNKNOWN_CONTROLLER_ROLE
        ]:
            actor_bindings.append(
                {
                    "protocol_version": RUNTIME_ACTOR_PROTOCOL_VERSION,
                    "schema_version": 1,
                    "thread_id": identity["thread_id"],
                    "controller_assigned_role": role,
                    "parent_thread_id": identity["parent_thread_id"],
                    "agent_path": identity["agent_path"],
                    "identity_evidence_sha256": identity["identity_evidence_sha256"],
                    "binding_source": "native_thread_read",
                }
            )
        identities = [dict(parent_identity)] + [dict(item["identity"]) for item in child_records]
        checkpoint = {
            "protocol_version": RESTART_CHECKPOINT_PROTOCOL_VERSION,
            "schema_version": 1,
            "case_id": spec["case_id"],
            "parent_thread_id": parent_identity["thread_id"],
            "identities": identities,
        }
        checkpoint["checkpoint_sha256"] = canonical_json_sha256(checkpoint)
        supervisor_probe_identities = [
            {
                "thread_id": parent_identity["thread_id"],
                "controller_assigned_role": "parent",
            }
        ]
        supervisor_probe_identities.extend(
            {
                "thread_id": item["identity"]["thread_id"],
                "controller_assigned_role": item["controller_assigned_role"],
            }
            for item in child_records
            if item["controller_assigned_role"] != "implementer_child"
        )
        return {
            "protocol_version": CONTROLLER_RUN_PROTOCOL_VERSION,
            "schema_version": 1,
            "case_id": spec["case_id"],
            "proposal_artifact": artifact,
            "controller_receipt_body": receipt_body,
            "actor_binding_requests": actor_bindings,
            "grant_core": grant_core,
            "supervisor_completion_required": {
                "grant_fields": ["isolation_evidence", "expires_at"],
                "issue_fields": ["request_id", "expected_revision"],
                "ordering": "post_proposal_restart_and_isolation_only",
            },
            "authorization_evidence": {
                "app_server_mutation_authority": False,
                "native_approval_policy": "never",
                "dynamic_tools": [],
                "mcp_server_count": 0,
                "hook_count": 0,
                "parent_permit_request_channel": False,
                "preissued_parent_permits": 0,
                "authorized_thread_id": actor["thread_id"],
                "authorized_action_count": 1,
                "second_action_authorized": False,
                "nonimplementation_identities_for_supervisor_probe": supervisor_probe_identities,
                "canonical_denial_evidence_complete": False,
            },
            "restart_checkpoint": checkpoint,
            "restart_verification_required": True,
            "broker_execute_invoked": False,
        }

    def run(self, transport: Transport, controller_key: bytes) -> dict[str, Any]:
        del controller_key
        capabilities = self.initialize(transport)
        parent_id, parent_identity = self.start_parent(transport)
        child_records: list[dict[str, Any]] = []
        implementation_turn: Mapping[str, Any] | None = None
        proposal_bytes: bytes | None = None
        native_turn_digest: str | None = None
        incomplete_evidence: dict[str, Any] | None = None

        plan = list(CANONICAL_CHILD_PLAN) + [(UNKNOWN_CHILD_NAME, UNKNOWN_CONTROLLER_ROLE)]
        for name, role in plan:
            task = self._implementation_task() if role == "implementer_child" else self._role_task(name)
            thread, identity, _parent_turn_id = self._spawn_child(
                transport, parent_id, name, task
            )
            _thread, observed_turn = self._wait_child_terminal(
                transport, identity["thread_id"], name
            )
            turn = self._require_completed_child_turn(observed_turn, name)
            if _native_children(transport, identity["thread_id"]):
                raise ControllerAuthorizationError(
                    f"{name} child created an unauthorized descendant thread"
                )
            if role == "incomplete_child":
                incomplete_evidence = self._incomplete_result_evidence(
                    turn, identity["thread_id"]
                )
            if role == "implementer_child" and turn is not None:
                _proposal, proposal_bytes, native_turn_digest = _completed_proposal(
                    _agent_message(turn), self.spec, identity["thread_id"], str(turn["id"])
                )
                implementation_turn = turn
            child_records.append(
                {
                    "controller_assigned_role": role,
                    "identity": identity,
                    "thread_id": identity["thread_id"],
                }
            )

        listed = set(_native_children(transport, parent_id))
        expected = {item["identity"]["thread_id"] for item in child_records}
        if listed != expected:
            raise ControllerProtocolError("final native child set differs from the exact plan")
        if implementation_turn is None or proposal_bytes is None or native_turn_digest is None:
            raise ControllerAuthorizationError("completed implementation proposal was not observed")
        if incomplete_evidence is None:
            raise ControllerAuthorizationError("incomplete child contract was not proven")
        target = Path(self.spec["worktree"]).joinpath(
            *PurePosixPath(self.spec["target_path"]).parts
        )
        if file_sha256(target) != self.spec["baseline_sha256"]:
            raise ControllerAuthorizationError("governed target changed during proposal collection")
        bundle = self._build_bundle(
            parent_identity=parent_identity,
            child_records=child_records,
            proposal_bytes=proposal_bytes,
            native_turn_evidence_sha256=native_turn_digest,
            implementation_turn_id=str(implementation_turn["id"]),
        )
        bundle["capability_evidence"] = capabilities
        bundle["incomplete_child_evidence"] = incomplete_evidence
        bundle["transport_audit"] = copy.deepcopy(transport.audit)
        return bundle


def finalize_controller_bundle(
    draft: Mapping[str, Any],
    live_controller_evidence: Mapping[str, Any],
    live_controller_evidence_sha256: str,
    controller_key: bytes,
) -> dict[str, Any]:
    """Seal the receipt only after both App Server process trees have closed."""
    if (
        not isinstance(draft, Mapping)
        or "controller_receipt_body" not in draft
        or "controller_receipt" in draft
        or not isinstance(draft.get("grant_core"), Mapping)
    ):
        raise ControllerValidationError("controller draft is not finalizable")
    evidence = copy.deepcopy(dict(live_controller_evidence))
    evidence_sha256 = require_snapshot_hash(live_controller_evidence_sha256)
    if canonical_json_sha256(evidence) != evidence_sha256:
        raise ControllerAuthorizationError("live controller evidence digest is invalid")
    receipt_body = copy.deepcopy(dict(draft["controller_receipt_body"]))
    if "live_controller_evidence_sha256" in receipt_body:
        raise ControllerAuthorizationError("controller draft already contains live evidence")
    receipt_body["live_controller_evidence_sha256"] = evidence_sha256
    receipt = seal_controller_receipt(receipt_body, controller_key)
    grant_core = copy.deepcopy(dict(draft["grant_core"]))
    if {
        "controller_receipt_sha256",
        "live_controller_evidence",
        "live_controller_evidence_sha256",
    }.intersection(grant_core):
        raise ControllerAuthorizationError("controller draft grant was already finalized")
    grant_core.update(
        controller_receipt_sha256=canonical_json_sha256(receipt),
        live_controller_evidence=evidence,
        live_controller_evidence_sha256=evidence_sha256,
    )
    finalized = copy.deepcopy(dict(draft))
    finalized.pop("controller_receipt_body", None)
    finalized["controller_receipt"] = receipt
    finalized["grant_core"] = grant_core
    finalized["live_controller_evidence_sha256"] = evidence_sha256
    return finalized


def verify_restart_continuity(
    transport: Transport, spec: Mapping[str, Any], checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = _normalize_run_spec(spec, proposal_may_exist=True)
    expected_fields = {
        "protocol_version",
        "schema_version",
        "case_id",
        "parent_thread_id",
        "identities",
        "checkpoint_sha256",
    }
    if not isinstance(checkpoint, Mapping) or set(checkpoint) != expected_fields:
        raise ControllerValidationError("restart checkpoint uses an unexpected schema")
    body = {name: value for name, value in checkpoint.items() if name != "checkpoint_sha256"}
    if (
        checkpoint.get("protocol_version") != RESTART_CHECKPOINT_PROTOCOL_VERSION
        or checkpoint.get("schema_version") != 1
        or checkpoint.get("case_id") != normalized["case_id"]
        or require_snapshot_hash(str(checkpoint.get("checkpoint_sha256", "")))
        != canonical_json_sha256(body)
    ):
        raise ControllerAuthorizationError("restart checkpoint digest or case context is invalid")
    controller = ProposalController.__new__(ProposalController)
    controller.spec = normalized
    capabilities = ProposalController.initialize(controller, transport)
    identities = checkpoint.get("identities")
    expected_identity_count = 1 + len(CANONICAL_CHILD_PLAN) + 1
    if not isinstance(identities, list) or len(identities) != expected_identity_count:
        raise ControllerValidationError(
            "restart checkpoint must contain the parent and the complete fixed child plan"
        )
    expected_by_thread = {}
    for identity in identities:
        if not isinstance(identity, Mapping) or not isinstance(identity.get("thread_id"), str):
            raise ControllerValidationError("restart identity record is malformed")
        expected_by_thread[identity["thread_id"]] = identity
    if len(expected_by_thread) != len(identities):
        raise ControllerValidationError("restart checkpoint duplicates a thread")
    parent_id = str(checkpoint.get("parent_thread_id", ""))
    observed: list[dict[str, Any]] = []
    for thread_id, expected in expected_by_thread.items():
        read = _thread_from_response(
            transport.request("thread/read", {"threadId": thread_id, "includeTurns": True}),
            "restart thread/read",
        )
        parent = expected.get("parent_thread_id")
        reconstructed = _identity_record(
            read,
            parent_thread_id=parent,
            expected_agent_path=str(expected.get("agent_path", "")),
        )
        if reconstructed != expected:
            raise ControllerAuthorizationError("native thread identity changed across restart")
        if thread_id != parent_id and _native_children(transport, thread_id):
            raise ControllerAuthorizationError(
                "native child acquired a descendant across App Server restart"
            )
        observed.append(reconstructed)
    children = set(_native_children(transport, parent_id))
    if children != set(expected_by_thread) - {parent_id}:
        raise ControllerAuthorizationError("native child set changed across restart")
    evidence = {
        "protocol_version": RESTART_EVIDENCE_PROTOCOL_VERSION,
        "schema_version": 1,
        "case_id": normalized["case_id"],
        "checkpoint_sha256": checkpoint["checkpoint_sha256"],
        "parent_thread_id": parent_id,
        "verified_thread_ids": sorted(expected_by_thread),
        "continuity_verified": True,
        "capability_evidence": capabilities,
    }
    evidence["evidence_sha256"] = canonical_json_sha256(evidence)
    return evidence


def _load_json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ControllerValidationError(f"{label} must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ControllerValidationError(f"{label} must be a JSON object")
    return value


def _transport_for(spec: Mapping[str, Any]) -> AppServerTransport:
    return AppServerTransport(
        executable=Path(spec["app_server_executable"]),
        expected_worker_sid=spec["worker_principal_sid"],
        expected_broker_sid=spec["broker_principal_sid"],
        expected_app_server_sha256=spec["expected_app_server_sha256"],
        expected_app_server_version=spec["expected_app_server_version"],
        expected_sandbox_profile_sha256=spec["sandbox_profile_sha256"],
        expected_environment_sha256=spec["app_server_environment_sha256"],
        worker_codex_home=Path(spec["worker_codex_home"]),
        cwd=Path(spec["runtime_working_directory"]),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    command = sub.add_parser("run")
    command.add_argument("--spec-json", required=True)

    command = sub.add_parser("verify-restart")
    command.add_argument("--spec-json", required=True)
    command.add_argument("--checkpoint-json", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        raw_spec = _load_json_object(args.spec_json, "spec-json")
        if args.command == "run":
            controller = ProposalController(raw_spec)
            with _transport_for(controller.spec) as transport:
                result = controller.run(transport, _decode_controller_key())
        elif args.command == "verify-restart":
            spec = _normalize_run_spec(raw_spec, proposal_may_exist=True)
            checkpoint = _load_json_object(args.checkpoint_json, "checkpoint-json")
            with _transport_for(spec) as transport:
                result = verify_restart_continuity(transport, spec, checkpoint)
        else:
            raise AssertionError(args.command)
    except (ControllerError, OSError, UnicodeError, ValueError) as exc:
        payload = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print(f"CONTROLLER ERROR [{type(exc).__name__}]: {exc}", file=sys.stderr)
        return 2
    payload = {"ok": True, "result": result}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
