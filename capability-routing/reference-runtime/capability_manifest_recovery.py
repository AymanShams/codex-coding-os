#!/usr/bin/env python3
"""Deterministic, fail-closed recovery for the canonical capability manifest."""

from __future__ import annotations

import argparse
import ctypes
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


RECEIPT_SCHEMA = "capability-authority-receipt-v1"
MUTEX_NAME = r"Local\OpenAI.Codex.CapabilityManifestRecovery.v1"
RECOVERABLE_MISMATCHES = frozenset(
    {config_fingerprint.SOURCE_HASH_KEY, "plugin-cache-inventory"}
)
ALLOWED_CONFIG_LEAF_PATHS = frozenset(
    {
        "/mcp_servers/node_repl/command",
        "/mcp_servers/node_repl/env/NODE_REPL_NODE_MODULE_DIRS",
        "/mcp_servers/node_repl/env/NODE_REPL_NODE_PATH",
        "/mcp_servers/node_repl/env/NODE_REPL_TRUSTED_CODE_PATHS",
        "/mcp_servers/node_repl/env/NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
        "/mcp_servers/node_repl/env/BROWSER_USE_CODEX_APP_VERSION",
        "/mcp_servers/node_repl/env/CODEX_CLI_PATH",
        "/shell_environment_policy/set/NODE_REPL_TRUSTED_CODE_PATHS",
        "/shell_environment_policy/set/NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
    }
)
REQUIRED_APP_CONFIG_PATHS = frozenset(
    path for path in ALLOWED_CONFIG_LEAF_PATHS if path.startswith("/mcp_servers/")
)
APP_PLUGIN_NAMES = ("browser", "chrome", "computer-use")
SHA256_PATTERN = re.compile(r"^[A-Fa-f0-9]{64}$")


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


def authority_snapshot_digest(receipt: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in receipt.items()
        if key not in {"snapshot_sha256", "newest_authority_mtime_ns"}
    }
    return _sha256_text(_canonical_json(payload))


def _valid_hash_map(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str)
        and isinstance(digest, str)
        and SHA256_PATTERN.fullmatch(digest) is not None
        for key, digest in value.items()
    )


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
    if not _valid_hash_map(receipt.get("plugin_cache_row_hashes")):
        return False
    optional_sources = receipt.get("required_source_hashes")
    if optional_sources is not None and not _valid_hash_map(optional_sources):
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


def _plugin_delta_allowed(key: str, old_version: str, new_version: str) -> bool:
    try:
        kind, relative = key.split("\t", 1)
    except ValueError:
        return False
    if kind not in {"ROOT", "FILE"}:
        return False
    relative = relative.lower()
    allowed_prefixes = {
        f"openai-bundled/{plugin}/{version}".lower()
        for plugin in APP_PLUGIN_NAMES
        for version in (old_version, new_version)
    }
    allowed_prefixes.add("openai-bundled/chrome/latest")
    return any(
        relative == prefix or relative.startswith(prefix + "/")
        for prefix in allowed_prefixes
    )


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

    previous_identity = previous["app_identity"]
    current_identity = current["app_identity"]
    old_version = previous_identity["app_version"]
    new_version = current_identity["app_version"]
    if (
        old_version == new_version
        or previous_identity["runtime_id"] == current_identity["runtime_id"]
        or previous_identity["cli_id"] == current_identity["cli_id"]
    ):
        return False, "APP_IDENTITY_DID_NOT_ADVANCE"
    if (
        current_identity["trusted_browser_client_sha256"].upper()
        != current_identity["browser_client_sha256"].upper()
    ):
        return False, "APP_IDENTITY_INCOHERENT"

    config_delta = _changed_hash_keys(
        previous["config_leaf_hashes"], current["config_leaf_hashes"]
    )
    if not config_delta:
        return False, "APP_CONFIG_COHORT_INCOMPLETE"
    if not config_delta.issubset(ALLOWED_CONFIG_LEAF_PATHS):
        return False, "UNRECOGNIZED_CONFIG_DELTA"
    if not REQUIRED_APP_CONFIG_PATHS.issubset(config_delta):
        return False, "APP_CONFIG_COHORT_INCOMPLETE"

    plugin_delta = _changed_hash_keys(
        previous["plugin_cache_row_hashes"], current["plugin_cache_row_hashes"]
    )
    if not plugin_delta:
        return False, "PLUGIN_COHORT_INCOMPLETE"
    if any(
        not _plugin_delta_allowed(key, old_version, new_version)
        for key in plugin_delta
    ):
        return False, "UNRECOGNIZED_PLUGIN_DELTA"
    for plugin in APP_PLUGIN_NAMES:
        new_root = f"ROOT\topenai-bundled/{plugin}/{new_version}".lower()
        current_keys = {key.lower() for key in current["plugin_cache_row_hashes"]}
        if new_root not in current_keys:
            return False, "PLUGIN_COHORT_INCOMPLETE"
    return True, "RECOGNIZED_CODEX_DESKTOP_UPDATE"


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
        for plugin in APP_PLUGIN_NAMES
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


def _required_source_hashes(codex_home: Path) -> dict[str, str]:
    paths = {
        "hooks.json": codex_home / "hooks.json",
        "AGENTS.md": codex_home / "AGENTS.md",
        "task-routing-gate.md": codex_home / "docs" / "context" / "task-routing-gate.md",
        "catalogue-router.SKILL.md": codex_home
        / "skills"
        / "catalogue-router"
        / "SKILL.md",
        "capability_index.py": codex_home / "hooks" / "capability_index.py",
        "capability_config_fingerprint.py": codex_home
        / "hooks"
        / "capability_config_fingerprint.py",
        "capability_index_cli.py": codex_home / "hooks" / "capability_index_cli.py",
        "user_prompt_skill_router.py": codex_home
        / "hooks"
        / "user_prompt_skill_router.py",
        "query-catalogue.ps1": codex_home
        / "skills"
        / "catalogue-router"
        / "scripts"
        / "query-catalogue.ps1",
        "routing-policy.yaml": codex_home / "capability-routing" / "routing-policy.yaml",
        "route-decision.schema.json": codex_home
        / "capability-routing"
        / "route-decision.schema.json",
        "ensure-node-dependencies.ps1": codex_home
        / "tools"
        / "dependency-readiness"
        / "ensure-node-dependencies.ps1",
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
    }
    return {
        name: digest
        for name, path in paths.items()
        if (digest := _sha256_file(path))
    }


def _plugin_row_hashes(rows: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        parts = row.split("\t", 3)
        if len(parts) != 4 or parts[0] not in {"ROOT", "FILE"}:
            raise ValueError("plugin cache authority row is malformed")
        result[f"{parts[0]}\t{parts[1]}"] = _sha256_text(row)
    return dict(sorted(result.items()))


def capture_authority_receipt(codex_home: Path | None = None) -> dict[str, Any]:
    home = (codex_home or index.CODEX_HOME).resolve(strict=True)
    config_path = home / "config.toml"
    authority = config_fingerprint.capability_config_authority(config_path)
    rows = index._plugin_cache_inventory_rows(home)
    if rows is None:
        raise RuntimeError("plugin cache authority inventory is unavailable")
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "config_projection_sha256": authority["sha256"],
        "config_leaf_hashes": authority["projection_leaf_hashes"],
        "plugin_cache_inventory_sha256": _sha256_text("\n".join(rows)),
        "plugin_cache_row_hashes": _plugin_row_hashes(rows),
        "required_source_hashes": _required_source_hashes(home),
        "app_identity": _app_identity(home, config_path),
    }
    receipt["snapshot_sha256"] = authority_snapshot_digest(receipt)
    return receipt


def stable_two_read(
    reader: Callable[[], dict[str, Any]],
    *,
    settle_seconds: float = 2.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any] | None, str]:
    first = reader()
    sleeper(settle_seconds)
    second = reader()
    if _canonical_json(first) != _canonical_json(second):
        return None, "AUTHORITY_SNAPSHOT_UNSTABLE"
    if not _valid_receipt(second):
        return None, "INVALID_CURRENT_AUTHORITY_RECEIPT"
    return second, "AUTHORITY_SNAPSHOT_STABLE"


def authority_is_quiet(codex_home: Path, quiet_seconds: float = 15.0) -> bool:
    paths = [codex_home / "config.toml"]
    cache_root = codex_home / "plugins" / "cache"
    if cache_root.is_dir():
        paths.extend(cache_root.rglob("*"))
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
    base = local_app_data or Path(os.environ.get("LOCALAPPDATA", ""))
    if not str(base):
        raise RuntimeError("LOCALAPPDATA is unavailable")
    cli_id = receipt["app_identity"]["cli_id"]
    bin_root = (base / "OpenAI" / "Codex" / "bin").resolve(strict=True)
    candidate = (bin_root / cli_id / "codex.exe").resolve(strict=True)
    if not candidate.is_relative_to(bin_root) or not candidate.is_file():
        raise RuntimeError("receipt-bound Codex CLI path is invalid")
    expected = receipt["app_identity"]["cli_executable_sha256"].upper()
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


def run_builder_candidate(
    expected_snapshot_sha256: str,
    manifest_path: Path,
    *,
    authority_receipt: dict[str, Any],
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
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell or not builder.is_file():
        raise RuntimeError("stable capability manifest builder is unavailable")
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
        "-ManifestPath",
        str(candidate),
        "-ExpectedAuthoritySnapshotSha256",
        expected_snapshot_sha256,
    ]
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
        candidate.unlink(missing_ok=True)
        raise TimeoutError("capability manifest builder timed out") from exc
    if process.returncode != 0:
        candidate.unlink(missing_ok=True)
        raise RuntimeError(
            f"capability manifest builder failed ({process.returncode}): {stderr.strip()}"
        )
    if not candidate.is_file() or not stdout.strip():
        candidate.unlink(missing_ok=True)
        raise RuntimeError("capability manifest builder returned no candidate receipt")
    return candidate


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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
        if state.get("freshness_status") == "fresh" and state.get(
            "source_hashes_verified"
        ) is True:
            return {"status": "noop", "reason_code": "MANIFEST_ALREADY_FRESH"}
        mismatches = {str(item) for item in state.get("source_hash_mismatches", [])}
        if not mismatches or not mismatches.issubset(RECOVERABLE_MISMATCHES):
            return {"status": "denied", "reason_code": "UNRECOVERABLE_SOURCE_DRIFT"}
        manifest = _read_manifest(target)
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
        reader = snapshot_reader or (lambda: capture_authority_receipt(home))
        preliminary, stable_reason = stable_two_read(
            reader, settle_seconds=settle_seconds, sleeper=sleeper
        )
        if preliminary is None:
            return {"status": "denied", "reason_code": stable_reason}
        recognized, reason = classify_app_update(baseline, preliminary)
        if not recognized:
            return {"status": "denied", "reason_code": reason}
        preparer = inventory_preparer or (
            lambda receipt: prepare_live_inventories(
                receipt, timeout_seconds=max(1.0, remaining(20.0))
            )
        )
        try:
            preparer(preliminary)
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
        current, stable_reason = stable_two_read(
            reader, settle_seconds=settle_seconds, sleeper=sleeper
        )
        if current is None:
            return {"status": "denied", "reason_code": stable_reason}
        recognized, reason = classify_app_update(baseline, current)
        if not recognized:
            return {"status": "denied", "reason_code": reason}
        runner = builder_runner or (
            lambda expected, path: run_builder_candidate(
                expected,
                path,
                authority_receipt=current,
                codex_home=home,
            )
        )
        candidate: Path | None = None
        try:
            candidate = runner(current["snapshot_sha256"], target)
            final_snapshot = reader()
            if (
                not _valid_receipt(final_snapshot)
                or final_snapshot["snapshot_sha256"] != current["snapshot_sha256"]
            ):
                return {
                    "status": "denied",
                    "reason_code": "AUTHORITY_CHANGED_AFTER_BUILD",
                }
            candidate_manifest = _read_manifest(candidate)
            if candidate_manifest.get("authority_receipt") != current:
                return {
                    "status": "denied",
                    "reason_code": "BUILDER_RECEIPT_MISMATCH",
                }
            candidate_state = index.load_active_capabilities(candidate)
            if (
                candidate_state.get("freshness_status") != "fresh"
                or candidate_state.get("source_hashes_verified") is not True
                or not candidate_state.get("entries")
            ):
                return {
                    "status": "denied",
                    "reason_code": "BUILDER_CANDIDATE_INVALID",
                }
            os.replace(candidate, target)
            candidate = None
            installed_state = index.load_active_capabilities(target)
            if (
                installed_state.get("freshness_status") != "fresh"
                or installed_state.get("source_hashes_verified") is not True
            ):
                return {
                    "status": "error",
                    "reason_code": "INSTALLED_MANIFEST_VALIDATION_FAILED",
                }
            return {
                "status": "rebuilt",
                "reason_code": "RECOGNIZED_CODEX_DESKTOP_UPDATE",
                "snapshot_sha256": current["snapshot_sha256"],
            }
        except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
            return {
                "status": "error",
                "reason_code": "RECOVERY_BUILDER_FAILED",
                "error_type": type(exc).__name__,
            }
        finally:
            if candidate is not None:
                candidate.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--codex-home", type=Path, default=index.CODEX_HOME)
    args = parser.parse_args()
    if not args.snapshot:
        parser.error("--snapshot is required")
    receipt = capture_authority_receipt(args.codex_home)
    print(_canonical_json(receipt))
    return 0 if _valid_receipt(receipt) else 2


if __name__ == "__main__":
    raise SystemExit(main())
