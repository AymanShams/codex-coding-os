#!/usr/bin/env python3
"""Render and transactionally promote the live worker-runtime BOM."""

from __future__ import annotations

import argparse
import ast
import csv
from dataclasses import dataclass
import datetime as dt
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib
from typing import Any, Mapping

from jsonschema import Draft202012Validator


BOM_SCHEMA_VERSION = "capability-worker-runtime-bom-v1"
JOURNAL_SCHEMA_VERSION = "capability-worker-runtime-bom-journal-v2"
RECEIPT_SCHEMA_VERSION = "capability-worker-runtime-bom-receipt-v2"
STATE_DIRECTORY = ".worker-runtime-bom-promoter"
MISSING = "missing"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
TRANSACTION_ID_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$"
)
RECEIPT_DOMAIN = b"CAPABILITY-WORKER-RUNTIME-BOM-RECEIPT-v2\0"
REQUIRED_RUNTIME_SPECS = {
    "antigravity-adapter": "runtime-identity.json",
    "local-agent-stack": "runtime-identity.json",
}
WORKER_SERVER_SPECS = {
    "antigravity-adapter": {
        "module": "antigravity_adapter.server",
        "pycache_relative_path": "state/python-cache/antigravity-adapter-2.1.1",
        "env": {
            "ANTIGRAVITY_ADAPTER_ROOT",
            "ANTIGRAVITY_AGY_EXECUTABLE",
        },
    },
    "local-agent-stack": {
        "module": "local_agent_stack.server",
        "pycache_relative_path": "run/python-cache/local-agent-stack-v5",
        "env": {"LOCAL_AGENT_STACK_ROOT"},
    },
}
WORKER_EXECUTION_KEYS = {
    "args",
    "command",
    "cwd",
    "enabled",
    "env",
    "gateway_managed",
    "startup_timeout_sec",
    "tool_timeout_sec",
}
WORKER_PRESENTATION_KEYS = {"description", "display_name", "name"}
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
PYTHON_EXECUTION_CLOSURE_SCHEMA = "python-venv-execution-closure-v1"
WORKER_IMPORT_PACKAGES = {
    "antigravity-adapter": "antigravity_adapter",
    "local-agent-stack": "local_agent_stack",
}
PYTHON_FORBIDDEN_ENVIRONMENT_VARIABLES = [
    "PYTHONHOME",
    "PYTHONINSPECT",
    "PYTHONPATH",
    "PYTHONPYCACHEPREFIX",
    "PYTHONSTARTUP",
    "PYTHONUSERBASE",
]
WORKER_CHILD_ENVIRONMENT_POLICY_ID = "codex-stability-child-env-v1"
GATEWAY_CONFIG_SERVER_ID = "codex-stability-gateway"
GATEWAY_CONFIG_URL = "http://127.0.0.1:8765/mcp"
GATEWAY_RUNTIME_IDENTITY_RELATIVE_PATH = (
    "tools/codex-stability/runtime-identity.json"
)
GATEWAY_RUNTIME_IDENTITY_SCHEMA = "codex-stability-gateway-runtime-identity-v1"
GATEWAY_COMPONENT = "codex-stability-gateway"
GATEWAY_RELEASE_ID = "codex-stability-gateway-1.0.1"
GATEWAY_STARTUP_ENVIRONMENT_POLICY_ID = "codex-stability-gateway-startup-v1"
GATEWAY_REQUIRED_PYTHON_FLAGS = {
    "dont_write_bytecode": 1,
    "isolated": 1,
    "no_site": 1,
    "no_user_site": 1,
}
GATEWAY_SOURCE_DOMAIN = b"CODEX-STABILITY-GATEWAY-SOURCE-v1\0"
GATEWAY_SITE_PACKAGES_DOMAIN = b"CODEX-STABILITY-GATEWAY-SITE-PACKAGES-v1\0"
GATEWAY_PYTHON_BASE_RUNTIME_DOMAIN = (
    b"CODEX-STABILITY-GATEWAY-PYTHON-BASE-RUNTIME-v1\0"
)
GATEWAY_SOURCE_RELATIVE_PATHS = (
    "codex_mcp_gateway.py",
    "gateway_bootstrap.py",
)
GATEWAY_RUNTIME_BINDING_KEYS = {
    "config_server_id",
    "identity_relative_path",
    "identity_sha256",
    "runtime_identity",
    "server_config_sha256",
}
GATEWAY_RUNTIME_IDENTITY_KEYS = {
    "child_environment_policy_id",
    "component",
    "gateway_startup_environment_policy_id",
    "gateway_startup_python_flags",
    "python_bytecode_cache",
    "python_injection_environment_keys",
    "python_runtime",
    "release_id",
    "schema_version",
    "source_files",
    "source_sha256",
}
GATEWAY_PYTHON_RUNTIME_KEYS = {
    "base_root",
    "base_runtime_file_count",
    "base_runtime_sha256",
    "console_executable_path",
    "console_executable_sha256",
    "dependency_lock_path",
    "dependency_lock_sha256",
    "site_packages_file_count",
    "site_packages_path",
    "site_packages_sha256",
    "version",
    "windowless_executable_path",
    "windowless_executable_sha256",
}
PYTHON_EXECUTION_CLOSURE_KEYS = {
    "schema_version",
    "venv_python_path",
    "venv_python_sha256",
    "pyvenv_config_path",
    "pyvenv_config_sha256",
    "include_system_site_packages",
    "base_interpreter_path",
    "base_interpreter_version",
    "base_interpreter_sha256",
    "base_runtime_tree_path",
    "base_runtime_tree_file_count",
    "base_runtime_tree_sha256",
    "editable_pth_path",
    "editable_pth_sha256",
    "editable_source_root",
    "import_package",
    "import_origin",
    "isolated_mode",
    "user_site_enabled",
    "dont_write_bytecode",
    "pycache_prefix_path",
    "pycache_prefix_empty",
    "forbidden_environment_variables",
    "child_environment_policy_id",
    "site_packages_path",
    "site_packages_tree_file_count",
    "site_packages_tree_sha256",
    "installed_distributions_count",
    "installed_distributions_sha256",
    "pth_files_count",
    "pth_files_sha256",
    "pth_imports_count",
    "pth_imports_sha256",
}


class BomPromotionError(RuntimeError):
    """Base fail-closed BOM deployment error."""


class BomValidationError(BomPromotionError):
    """Candidate, schema, configuration, or runtime identity is invalid."""


class PreconditionError(BomPromotionError):
    """The live target differs from the reviewed precondition."""


class ReplayError(BomPromotionError):
    """A transaction identifier is bound to other or corrupt facts."""


class LockError(BomPromotionError):
    """The target-local promotion lock is unavailable."""


class RecoveryError(BomPromotionError):
    """Exact rollback cannot be completed without overwriting external drift."""


class InjectedFailure(BomPromotionError):
    """Test-only failure at a named transaction boundary."""


@dataclass(frozen=True)
class ApplyOptions:
    candidate_path: Path | str
    schema_path: Path | str
    config_path: Path | str
    target_path: Path | str
    transaction_id: str
    expected_target_sha256: str
    expected_candidate_sha256: str
    lock_timeout_seconds: float = 30.0
    fault_injection: str | None = None


def _canonical_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        rendered = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
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
            raise ReplayError(f"immutable BOM transaction artifact changed: {path}")
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
                raise ReplayError(
                    f"immutable BOM transaction artifact changed: {path}"
                )
        except OSError:
            if path.exists():
                if path.read_bytes() != payload:
                    raise ReplayError(
                        f"immutable BOM transaction artifact changed: {path}"
                    )
            else:
                os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_stable_regular_file(path: Path, label: str) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        if path.is_symlink() or not resolved.is_file():
            raise OSError("not one regular file")
        first_stat = resolved.stat()
        first = resolved.read_bytes()
        second = resolved.read_bytes()
        second_stat = resolved.stat()
    except OSError as exc:
        raise BomValidationError(f"{label} is unavailable: {path}") from exc
    if (
        first != second
        or first_stat.st_size != second_stat.st_size
        or first_stat.st_mtime_ns != second_stat.st_mtime_ns
    ):
        raise BomValidationError(f"{label} changed during verification: {resolved}")
    return first


def _promoter_sha256() -> str:
    """Bind each transaction to the exact automatic writer bytes."""

    return _sha256_bytes(
        _read_stable_regular_file(
            Path(__file__), "worker-runtime BOM promoter"
        )
    )


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0) & 0x400
    )


def _load_json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BomValidationError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise BomValidationError(f"{label} is not a JSON object")
    return value


def _load_schema(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_stable_regular_file(path, "worker-runtime BOM schema")
    schema = _load_json_bytes(raw, "worker-runtime BOM schema")
    return schema, raw


def _validate_schema(value: dict[str, Any], schema: dict[str, Any]) -> None:
    first_error = next(Draft202012Validator(schema).iter_errors(value), None)
    if first_error is not None:
        location = "/".join(str(part) for part in first_error.absolute_path)
        raise BomValidationError(
            f"worker-runtime BOM is schema-invalid at {location or '<root>'}: "
            f"{first_error.message}"
        )


def _load_config(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_stable_regular_file(path, "Codex configuration")
    try:
        value = tomllib.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise BomValidationError("Codex configuration is invalid") from exc
    return value, raw


def _worker_server_projection(
    config: Mapping[str, Any], server_id: str
) -> tuple[dict[str, Any], Path, Path]:
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        raise BomValidationError("Codex configuration has no MCP server table")
    server = servers.get(server_id)
    spec = WORKER_SERVER_SPECS.get(server_id)
    if not isinstance(server, dict) or spec is None:
        raise BomValidationError(f"configured worker is unavailable: {server_id}")
    unknown = set(server) - WORKER_EXECUTION_KEYS - WORKER_PRESENTATION_KEYS
    if unknown or not WORKER_EXECUTION_KEYS.issubset(server):
        raise BomValidationError(
            f"configured worker stanza is not bounded: {server_id}"
        )
    if server.get("enabled") is not False or server.get("gateway_managed") is not True:
        raise BomValidationError(
            f"gateway-owned worker flags are invalid: {server_id}"
        )
    args = server.get("args")
    env = server.get("env")
    if not isinstance(env, dict) or set(env) != spec["env"]:
        raise BomValidationError(
            f"configured worker module or environment is invalid: {server_id}"
        )
    if any(
        not isinstance(key, str)
        or not isinstance(value, str)
        or not value
        or len(value) > 4096
        or any(ord(character) < 32 for character in value)
        for key, value in env.items()
    ):
        raise BomValidationError(f"configured worker environment is invalid: {server_id}")
    for timeout_key in ("startup_timeout_sec", "tool_timeout_sec"):
        value = server.get(timeout_key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 < float(value) <= 3600
        ):
            raise BomValidationError(
                f"configured worker timeout is invalid: {server_id}"
            )
    command = Path(str(server.get("command") or ""))
    cwd = Path(str(server.get("cwd") or ""))
    if not command.is_absolute() or not cwd.is_absolute():
        raise BomValidationError(f"server paths are not absolute: {server_id}")
    try:
        command_resolved = command.resolve(strict=True)
        root = cwd.resolve(strict=True)
    except OSError as exc:
        raise BomValidationError(f"server runtime path is unavailable: {server_id}") from exc
    if (
        not command_resolved.is_file()
        or not root.is_dir()
        or not command_resolved.is_relative_to(root)
    ):
        raise BomValidationError(
            f"configured worker command escaped its root: {server_id}"
        )
    try:
        pycache_prefix = (
            root.joinpath(*str(spec["pycache_relative_path"]).split("/"))
        ).resolve(strict=True)
        pycache_is_empty = not any(pycache_prefix.iterdir())
    except OSError as exc:
        raise BomValidationError(
            f"configured worker Python cache path is unavailable: {server_id}"
        ) from exc
    expected_args = [
        "-I",
        "-B",
        "-X",
        f"pycache_prefix={pycache_prefix}",
        "-m",
        str(spec["module"]),
    ]
    if (
        args != expected_args
        or _is_link_or_reparse(pycache_prefix)
        or not pycache_prefix.is_dir()
        or not pycache_is_empty
    ):
        raise BomValidationError(
            f"configured worker Python cache contract is invalid: {server_id}"
        )
    root_env_key = (
        "LOCAL_AGENT_STACK_ROOT"
        if server_id == "local-agent-stack"
        else "ANTIGRAVITY_ADAPTER_ROOT"
    )
    try:
        if Path(env[root_env_key]).resolve(strict=True) != root:
            raise BomValidationError(
                f"configured worker root environment is invalid: {server_id}"
            )
        if server_id == "antigravity-adapter":
            agy = Path(env["ANTIGRAVITY_AGY_EXECUTABLE"]).resolve(strict=True)
            if not agy.is_file():
                raise OSError("Antigravity executable is not a file")
    except OSError as exc:
        raise BomValidationError(
            f"configured worker environment path is unavailable: {server_id}"
        ) from exc
    projection = {key: server[key] for key in sorted(WORKER_EXECUTION_KEYS)}
    return projection, root, command_resolved


def _config_projection_sha256(
    config: Mapping[str, Any], server_ids: set[str]
) -> str:
    projection = {
        server_id: _worker_server_projection(config, server_id)[0]
        for server_id in sorted(server_ids)
    }
    servers = config.get("mcp_servers")
    gateway_server = (
        servers.get(GATEWAY_CONFIG_SERVER_ID) if isinstance(servers, dict) else None
    )
    if gateway_server != {"url": GATEWAY_CONFIG_URL}:
        raise BomValidationError("Codex stability gateway configuration is invalid")
    projection[GATEWAY_CONFIG_SERVER_ID] = gateway_server
    return _sha256_bytes(_canonical_json_bytes(projection))


def _source_inventory_sha256(root: Path, paths: list[Path]) -> str:
    rows: list[dict[str, str]] = []
    seen: set[Path] = set()
    for path in paths:
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise BomValidationError("worker source identity is incomplete") from exc
        if (
            resolved in seen
            or not resolved.is_relative_to(root)
            or not resolved.is_file()
        ):
            raise BomValidationError("worker source identity escaped its root")
        seen.add(resolved)
        raw = _read_stable_regular_file(resolved, "worker source identity file")
        rows.append(
            {
                "path": resolved.relative_to(root).as_posix(),
                "sha256": _sha256_bytes(raw),
            }
        )
    return _sha256_bytes(
        json.dumps(
            rows,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def _expand_identity_path(value: Any, root: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise BomValidationError("worker dependency path is invalid")
    expanded = value.replace("${RUNTIME_ROOT}", str(root))
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if "${LOCALAPPDATA}" in expanded:
        if not local_app_data:
            raise BomValidationError("LOCALAPPDATA is unavailable")
        expanded = expanded.replace("${LOCALAPPDATA}", local_app_data)
    path = Path(expanded)
    if not path.is_absolute():
        path = root / path
    return path


def _same_resolved_path(left: Path | str, right: Path | str) -> bool:
    try:
        left_value = os.path.normcase(str(Path(left).resolve(strict=True)))
        right_value = os.path.normcase(str(Path(right).resolve(strict=True)))
    except OSError:
        return False
    return left_value == right_value


def _parse_pyvenv_config(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BomValidationError("worker pyvenv.cfg is not UTF-8") from exc
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key, separator, value = line.partition("=")
        normalized_key = key.strip().casefold()
        normalized_value = value.strip()
        if (
            not separator
            or not normalized_key
            or not normalized_value
            or normalized_key in values
        ):
            raise BomValidationError("worker pyvenv.cfg is ambiguous")
        values[normalized_key] = normalized_value
    if (
        not values.get("home")
        or values.get("implementation") != "CPython"
        or not values.get("version_info")
        or values.get("include-system-site-packages", "").casefold() != "false"
    ):
        raise BomValidationError("worker pyvenv.cfg isolation is invalid")
    return values


def _probe_python_execution(
    command: Path, package: str, pycache_prefix: Path
) -> dict[str, Any]:
    script = (
        "import importlib.util,json,platform,site,sys;"
        "s=importlib.util.find_spec(sys.argv[1]);"
        "print(json.dumps({'executable':sys.executable,'base_prefix':sys.base_prefix,"
        "'version':platform.python_version(),'origin':getattr(s,'origin',None),"
        "'locations':list(getattr(s,'submodule_search_locations',[]) or []),"
        "'isolated':sys.flags.isolated,'no_user_site':sys.flags.no_user_site,"
        "'user_site_enabled':site.ENABLE_USER_SITE,"
        "'dont_write_bytecode':sys.dont_write_bytecode,"
        "'pycache_prefix':sys.pycache_prefix},"
        "sort_keys=True,separators=(',',':')))"
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            [
                str(command),
                "-I",
                "-B",
                "-X",
                f"pycache_prefix={pycache_prefix}",
                "-c",
                script,
                package,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=5.0,
            check=False,
            creationflags=creationflags,
        )
        value = json.loads(completed.stdout)
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise BomValidationError("worker Python import probe failed") from exc
    if (
        completed.returncode != 0
        or completed.stderr
        or not isinstance(value, dict)
        or set(value) != {
            "base_prefix",
            "dont_write_bytecode",
            "executable",
            "locations",
            "isolated",
            "no_user_site",
            "origin",
            "pycache_prefix",
            "user_site_enabled",
            "version",
        }
        or not isinstance(value.get("locations"), list)
    ):
        raise BomValidationError("worker Python import probe is invalid")
    return value


def _validate_python_execution_closure(
    server_id: str,
    identity: Mapping[str, Any],
    root: Path,
    command: Path,
) -> dict[str, Any]:
    closure = identity.get("python_execution_closure")
    package = WORKER_IMPORT_PACKAGES.get(server_id)
    if (
        not isinstance(closure, dict)
        or set(closure) != PYTHON_EXECUTION_CLOSURE_KEYS
        or closure.get("schema_version") != PYTHON_EXECUTION_CLOSURE_SCHEMA
        or package is None
        or closure.get("import_package") != package
        or closure.get("include_system_site_packages") is not False
        or closure.get("isolated_mode") is not True
        or closure.get("user_site_enabled") is not False
        or closure.get("dont_write_bytecode") is not True
        or closure.get("pycache_prefix_empty") is not True
        or not isinstance(closure.get("pycache_prefix_path"), str)
        or not Path(closure["pycache_prefix_path"]).is_absolute()
        or closure.get("forbidden_environment_variables")
        != PYTHON_FORBIDDEN_ENVIRONMENT_VARIABLES
        or closure.get("child_environment_policy_id")
        != WORKER_CHILD_ENVIRONMENT_POLICY_ID
        or SEMVER_RE.fullmatch(str(closure.get("base_interpreter_version") or ""))
        is None
        or any(
            SHA256_RE.fullmatch(str(closure.get(key) or "")) is None
            for key in (
                "venv_python_sha256",
                "pyvenv_config_sha256",
                "base_interpreter_sha256",
                "editable_pth_sha256",
                "base_runtime_tree_sha256",
                "site_packages_tree_sha256",
                "installed_distributions_sha256",
                "pth_files_sha256",
                "pth_imports_sha256",
            )
        )
        or any(
            isinstance(closure.get(key), bool)
            or not isinstance(closure.get(key), int)
            or closure[key] < 1
            for key in (
                "base_runtime_tree_file_count",
                "site_packages_tree_file_count",
                "installed_distributions_count",
                "pth_files_count",
                "pth_imports_count",
            )
        )
        or any(
            not isinstance(closure.get(key), str)
            or not Path(closure[key]).is_absolute()
            for key in ("base_runtime_tree_path", "site_packages_path")
        )
    ):
        raise BomValidationError("worker Python execution closure is invalid")

    command = command.resolve(strict=True)
    venv_root = command.parent.parent
    expected_venv_root = (root / ".venv").resolve(strict=True)
    pyvenv_path = (venv_root / "pyvenv.cfg").resolve(strict=True)
    spec = WORKER_SERVER_SPECS[server_id]
    expected_pycache_prefix = root.joinpath(
        *str(spec["pycache_relative_path"]).split("/")
    ).resolve(strict=True)
    try:
        pycache_is_empty = not any(expected_pycache_prefix.iterdir())
    except OSError as exc:
        raise BomValidationError("worker Python cache prefix is unavailable") from exc
    if (
        command.name.casefold() != "python.exe"
        or command.parent.name.casefold() != "scripts"
        or not _same_resolved_path(venv_root, expected_venv_root)
        or not _same_resolved_path(closure["venv_python_path"], command)
        or not _same_resolved_path(closure["pyvenv_config_path"], pyvenv_path)
        or not _same_resolved_path(
            closure["pycache_prefix_path"], expected_pycache_prefix
        )
        or _is_link_or_reparse(expected_pycache_prefix)
        or not expected_pycache_prefix.is_dir()
        or not pycache_is_empty
    ):
        raise BomValidationError("worker Python virtual environment path is invalid")
    command_raw = _read_stable_regular_file(command, "worker venv Python launcher")
    pyvenv_raw = _read_stable_regular_file(pyvenv_path, "worker pyvenv.cfg")
    pyvenv = _parse_pyvenv_config(pyvenv_raw)
    expected_version_info = ".".join(
        str(closure["base_interpreter_version"]).split(".")[:2]
    )
    if pyvenv.get("version_info") != expected_version_info:
        raise BomValidationError("worker pyvenv.cfg version is invalid")
    try:
        base_interpreter = (Path(pyvenv["home"]) / "python.exe").resolve(strict=True)
        base_runtime_root = Path(str(closure["base_runtime_tree_path"])).resolve(
            strict=True
        )
    except OSError as exc:
        raise BomValidationError("worker base interpreter is unavailable") from exc
    if (
        not base_interpreter.is_file()
        or not _same_resolved_path(
            closure["base_interpreter_path"], base_interpreter
        )
        or not _same_resolved_path(base_runtime_root, base_interpreter.parent)
        or _sha256_bytes(command_raw) != closure["venv_python_sha256"]
        or _sha256_bytes(pyvenv_raw) != closure["pyvenv_config_sha256"]
        or _sha256_bytes(
            _read_stable_regular_file(base_interpreter, "worker base interpreter")
        )
        != closure["base_interpreter_sha256"]
    ):
        raise BomValidationError("worker Python interpreter bytes do not match identity")

    site_packages = (venv_root / "Lib" / "site-packages").resolve(strict=True)
    editable_path = Path(str(closure["editable_pth_path"])).resolve(strict=True)
    source_root = (root / "src").resolve(strict=True)
    import_origin = (source_root / package / "__init__.py").resolve(strict=True)
    if (
        editable_path.is_symlink()
        or not editable_path.is_file()
        or editable_path.parent != site_packages
        or not editable_path.name.casefold().startswith("__editable__.")
        or editable_path.suffix.casefold() != ".pth"
        or not _same_resolved_path(closure["editable_source_root"], source_root)
        or not _same_resolved_path(closure["import_origin"], import_origin)
        or not _same_resolved_path(closure["site_packages_path"], site_packages)
        or not import_origin.is_file()
    ):
        raise BomValidationError("worker editable import path is invalid")
    editable_raw = _read_stable_regular_file(editable_path, "worker editable .pth")
    try:
        editable_lines = [
            line.strip()
            for line in editable_raw.decode("utf-8-sig").splitlines()
            if line.strip()
        ]
    except UnicodeDecodeError as exc:
        raise BomValidationError("worker editable .pth is not UTF-8") from exc
    if (
        len(editable_lines) != 1
        or editable_lines[0].casefold().startswith("import ")
        or not Path(editable_lines[0]).is_absolute()
        or not _same_resolved_path(editable_lines[0], source_root)
        or _sha256_bytes(editable_raw) != closure["editable_pth_sha256"]
    ):
        raise BomValidationError("worker editable .pth target does not match identity")

    distribution_identity = _installed_distributions_identity(
        site_packages,
        venv_root,
        source_root,
        command,
        expected_pycache_prefix,
    )
    base_runtime_identity = _worker_base_runtime_tree_identity(base_runtime_root)
    site_packages_tree_identity = _worker_site_packages_tree_identity(site_packages)
    if any(
        closure.get(key) != value
        for key, value in {
            **distribution_identity,
            **base_runtime_identity,
            **site_packages_tree_identity,
        }.items()
    ):
        raise BomValidationError(
            "worker Python dependency closure does not match current bytes"
        )

    probe = _probe_python_execution(command, package, expected_pycache_prefix)
    locations = probe.get("locations")
    package_root = (source_root / package).resolve(strict=True)
    if (
        not _same_resolved_path(probe.get("executable", ""), command)
        or not _same_resolved_path(
            Path(str(probe.get("base_prefix") or "")) / "python.exe",
            base_interpreter,
        )
        or probe.get("version") != closure["base_interpreter_version"]
        or probe.get("isolated") != 1
        or probe.get("no_user_site") != 1
        or probe.get("user_site_enabled") is not False
        or probe.get("dont_write_bytecode") is not True
        or not _same_resolved_path(
            probe.get("pycache_prefix", ""), expected_pycache_prefix
        )
        or not _same_resolved_path(probe.get("origin", ""), import_origin)
        or not isinstance(locations, list)
        or len(locations) != 1
        or not _same_resolved_path(locations[0], package_root)
    ):
        raise BomValidationError("worker Python import resolution does not match identity")
    return dict(closure)


def _antigravity_source_paths(root: Path) -> list[Path]:
    package_root = root / "src" / "antigravity_adapter"
    try:
        resolved_package = package_root.resolve(strict=True)
        if package_root.is_symlink() or not resolved_package.is_dir():
            raise OSError("package root is invalid")
        entries = sorted(
            package_root.rglob("*"),
            key=lambda path: path.relative_to(package_root).as_posix().casefold(),
        )
    except OSError as exc:
        raise BomValidationError("Antigravity source inventory is unavailable") from exc
    seen: set[str] = set()
    sources: list[Path] = []
    for path in entries:
        if path.is_symlink():
            raise BomValidationError("Antigravity source inventory contains a link")
        if not path.is_file() or path.suffix.casefold() != ".py":
            continue
        relative = path.relative_to(package_root).as_posix().casefold()
        if relative in seen:
            raise BomValidationError("Antigravity source inventory is ambiguous")
        seen.add(relative)
        sources.append(path)
    if not sources or not {
        "__init__.py",
        "server.py",
        "source_integrity.py",
    }.issubset(seen):
        raise BomValidationError("Antigravity source inventory is incomplete")
    return sources


def _compact_json_sha256(value: Any) -> str:
    return _sha256_bytes(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def _probe_pth_import_origins(
    command: Path,
    modules: list[str],
    pycache_prefix: Path,
) -> dict[str, str]:
    script = (
        "import importlib.util,json,sys;"
        "print(json.dumps({n:getattr(importlib.util.find_spec(n),'origin',None) "
        "for n in sys.argv[1:]},sort_keys=True,separators=(',',':')))"
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            [
                str(command),
                "-I",
                "-B",
                "-X",
                f"pycache_prefix={pycache_prefix}",
                "-c",
                script,
                *modules,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=5.0,
            check=False,
            creationflags=creationflags,
        )
        value = json.loads(completed.stdout)
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise BomValidationError("worker .pth import probe failed") from exc
    if (
        completed.returncode != 0
        or completed.stderr
        or not isinstance(value, dict)
        or set(value) != set(modules)
        or any(not isinstance(item, str) or not item for item in value.values())
    ):
        raise BomValidationError("worker .pth import probe is invalid")
    return value


def _installed_distributions_identity(
    site_packages: Path,
    venv_root: Path,
    source_root: Path,
    command: Path,
    pycache_prefix: Path,
) -> dict[str, Any]:
    try:
        resolved_site = site_packages.resolve(strict=True)
        resolved_venv = venv_root.resolve(strict=True)
        resolved_source = source_root.resolve(strict=True)
    except OSError as exc:
        raise BomValidationError("worker site-packages is unavailable") from exc
    if (
        _is_link_or_reparse(site_packages)
        or not resolved_site.is_dir()
        or not resolved_site.is_relative_to(resolved_venv)
        or os.path.normcase(str(Path(os.path.abspath(site_packages))))
        != os.path.normcase(str(resolved_site))
    ):
        raise BomValidationError("worker site-packages path is invalid")

    distributions: list[dict[str, Any]] = []
    names: set[str] = set()
    all_owned_paths: set[str] = set()
    try:
        discovered = list(importlib.metadata.distributions(path=[str(resolved_site)]))
    except (OSError, ValueError) as exc:
        raise BomValidationError("worker distribution inventory is unavailable") from exc
    for distribution in discovered:
        name_value = distribution.metadata.get("Name")
        normalized_name = (
            re.sub(r"[-_.]+", "-", name_value).lower()
            if isinstance(name_value, str)
            else ""
        )
        version = distribution.version
        distribution_input = Path(str(getattr(distribution, "_path", "")))
        try:
            distribution_path = distribution_input.resolve(strict=True)
        except OSError as exc:
            raise BomValidationError("worker distribution metadata is unavailable") from exc
        if (
            not normalized_name
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", normalized_name)
            is None
            or normalized_name in names
            or not isinstance(version, str)
            or not version
            or _is_link_or_reparse(distribution_input)
            or not distribution_path.is_dir()
            or distribution_path.parent != resolved_site
            or distribution_path.suffix.casefold() != ".dist-info"
            or os.path.normcase(str(Path(os.path.abspath(distribution_input))))
            != os.path.normcase(str(distribution_path))
        ):
            raise BomValidationError("worker distribution identity is invalid")
        names.add(normalized_name)
        record_path = distribution_path / "RECORD"
        record_raw = _read_stable_regular_file(
            record_path, f"{normalized_name} distribution RECORD"
        )
        try:
            record_text = record_raw.decode("utf-8")
            record_rows = csv.reader(io.StringIO(record_text, newline=""))
            owned_paths: set[str] = set()
            record_row_count = 0
            for record_row in record_rows:
                if len(record_row) != 3 or not record_row[0]:
                    raise BomValidationError("worker distribution RECORD row is invalid")
                located = Path(
                    os.path.abspath(str(distribution.locate_file(record_row[0])))
                )
                relative = located.relative_to(resolved_venv).as_posix()
                path_key = relative.casefold()
                if path_key in owned_paths:
                    raise BomValidationError(
                        "worker distribution RECORD contains a duplicate path"
                    )
                owned_paths.add(path_key)
                all_owned_paths.add(path_key)
                record_row_count += 1
        except (UnicodeDecodeError, csv.Error, ValueError) as exc:
            raise BomValidationError("worker distribution RECORD is invalid") from exc
        distributions.append(
            {
                "name": normalized_name,
                "version": version,
                "record_path": record_path.relative_to(resolved_venv).as_posix(),
                "record_sha256": _sha256_bytes(record_raw),
                "record_row_count": record_row_count,
            }
        )
    distributions.sort(key=lambda row: row["name"])
    if not distributions:
        raise BomValidationError("worker distribution inventory is empty")

    pth_rows: list[dict[str, Any]] = []
    pth_import_modules: set[str] = set()
    for pth_input in resolved_site.glob("*.pth"):
        try:
            pth = pth_input.resolve(strict=True)
        except OSError as exc:
            raise BomValidationError("worker .pth inventory is unavailable") from exc
        relative = pth.relative_to(resolved_venv).as_posix()
        record_owned = relative.casefold() in all_owned_paths
        if (
            _is_link_or_reparse(pth_input)
            or os.path.normcase(str(Path(os.path.abspath(pth_input))))
            != os.path.normcase(str(pth))
            or not pth.is_file()
            or pth.parent != resolved_site
            or (not record_owned and pth.name.casefold() != "_virtualenv.pth")
        ):
            raise BomValidationError("worker .pth inventory is unowned or ambiguous")
        pth_raw = _read_stable_regular_file(pth, "worker .pth file")
        try:
            pth_text = pth_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BomValidationError("worker .pth file is not UTF-8") from exc
        for line in pth_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("import "):
                try:
                    parsed = ast.parse(stripped, mode="exec")
                except SyntaxError as exc:
                    raise BomValidationError("worker .pth import row is invalid") from exc
                for node in parsed.body:
                    if not isinstance(node, ast.Import):
                        raise BomValidationError(
                            "worker .pth executable row is not a plain import"
                        )
                    pth_import_modules.update(alias.name for alias in node.names)
                continue
            target_input = Path(stripped)
            target = (
                target_input if target_input.is_absolute() else resolved_site / target_input
            ).resolve(strict=True)
            if not target.is_dir() or (
                not target.is_relative_to(resolved_site)
                and not _same_resolved_path(target, resolved_source)
            ):
                raise BomValidationError("worker .pth target escaped its authority")
        pth_rows.append(
            {
                "path": relative,
                "sha256": _sha256_bytes(pth_raw),
                "record_owned": record_owned,
            }
        )
    pth_rows.sort(key=lambda row: row["path"].casefold())
    if not pth_rows or "_virtualenv" not in pth_import_modules:
        raise BomValidationError("worker .pth startup closure is incomplete")
    modules = sorted(pth_import_modules)
    origins = _probe_pth_import_origins(command, modules, pycache_prefix)
    pth_import_rows: list[dict[str, str]] = []
    for module_name in modules:
        try:
            origin = Path(origins[module_name]).resolve(strict=True)
            relative_origin = origin.relative_to(resolved_venv).as_posix()
        except (OSError, ValueError) as exc:
            raise BomValidationError("worker .pth import origin escaped its venv") from exc
        pth_import_rows.append(
            {
                "module": module_name,
                "origin": relative_origin,
                "sha256": _sha256_bytes(
                    _read_stable_regular_file(origin, "worker .pth import module")
                ),
            }
        )
    return {
        "installed_distributions_count": len(distributions),
        "installed_distributions_sha256": _compact_json_sha256(distributions),
        "pth_files_count": len(pth_rows),
        "pth_files_sha256": _compact_json_sha256(pth_rows),
        "pth_imports_count": len(pth_import_rows),
        "pth_imports_sha256": _compact_json_sha256(pth_import_rows),
    }


def _worker_base_runtime_tree_identity(root: Path) -> dict[str, Any]:
    try:
        resolved_root = root.resolve(strict=True)
        if _is_link_or_reparse(root) or not resolved_root.is_dir():
            raise OSError("base runtime root is invalid")
        paths = list(resolved_root.rglob("*"))
    except OSError as exc:
        raise BomValidationError("worker base runtime tree is unavailable") from exc
    rows: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for path in paths:
        relative = path.relative_to(resolved_root)
        key = relative.as_posix().casefold()
        if key in seen_paths:
            raise BomValidationError(
                "worker base runtime tree has a case-insensitive duplicate"
            )
        seen_paths.add(key)
        if _is_link_or_reparse(path):
            raise BomValidationError("worker base runtime tree contains a link")
        if "__pycache__" in relative.parts:
            continue
        if path.is_file():
            rows.append(
                {
                    "path": relative.as_posix(),
                    "sha256": _sha256_bytes(
                        _read_stable_regular_file(path, "worker base runtime file")
                    ),
                }
            )
        elif not path.is_dir():
            raise BomValidationError(
                "worker base runtime tree contains an unsupported entry"
            )
    rows.sort(key=lambda row: row["path"].casefold())
    if not rows:
        raise BomValidationError("worker base runtime tree is empty")
    return {
        "base_runtime_tree_file_count": len(rows),
        "base_runtime_tree_sha256": _compact_json_sha256(rows),
    }


def _worker_site_packages_tree_identity(root: Path) -> dict[str, Any]:
    """Bind every non-cache byte under one worker's effective site-packages."""
    try:
        resolved_root = root.resolve(strict=True)
        if (
            _is_link_or_reparse(root)
            or not resolved_root.is_dir()
            or os.path.normcase(str(Path(os.path.abspath(root))))
            != os.path.normcase(str(resolved_root))
        ):
            raise OSError("site-packages root is invalid")
    except OSError as exc:
        raise BomValidationError("worker site-packages tree is unavailable") from exc

    rows: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    pending = [resolved_root]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(
                directory.iterdir(),
                key=lambda path: path.name.casefold(),
            )
        except OSError as exc:
            raise BomValidationError(
                "worker site-packages tree is unreadable"
            ) from exc
        for path in children:
            try:
                relative = path.relative_to(resolved_root)
            except ValueError as exc:
                raise BomValidationError(
                    "worker site-packages entry escaped its root"
                ) from exc
            key = relative.as_posix().casefold()
            if key in seen_paths:
                raise BomValidationError(
                    "worker site-packages tree has a case-insensitive duplicate"
                )
            seen_paths.add(key)
            if _is_link_or_reparse(path):
                raise BomValidationError(
                    "worker site-packages tree contains a link or reparse point"
                )
            if path.is_dir():
                if relative.name != "__pycache__":
                    pending.append(path)
                continue
            if not path.is_file():
                raise BomValidationError(
                    "worker site-packages tree contains an unsupported entry"
                )
            if "__pycache__" in relative.parts:
                continue
            rows.append(
                {
                    "path": relative.as_posix(),
                    "sha256": _sha256_bytes(
                        _read_stable_regular_file(
                            path, "worker site-packages tree file"
                        )
                    ),
                }
            )
    rows.sort(key=lambda row: row["path"].casefold())
    if not rows:
        raise BomValidationError("worker site-packages tree is empty")
    return {
        "site_packages_tree_file_count": len(rows),
        "site_packages_tree_sha256": _compact_json_sha256(rows),
    }


def _validate_las_artifacts(root: Path, identity: Mapping[str, Any]) -> None:
    try:
        paths = [
            root / "pyproject.toml",
            root / "uv.lock",
            root / "runtime-dependencies.lock.json",
            root / "vendor" / "versions.json",
        ]
        paths.extend(sorted((root / "config").glob("*.json")))
        paths.extend(sorted((root / "config").glob("*.yaml")))
        paths.extend(sorted((root / "config" / "schemas").glob("*.json")))
        paths.extend(
            sorted(
                path
                for path in (root / "scripts").iterdir()
                if path.is_file() and path.suffix.lower() in {".ps1", ".py", ".vbs"}
            )
        )
        paths.extend(sorted((root / "src" / "local_agent_stack").rglob("*.py")))
    except OSError as exc:
        raise BomValidationError("local-agent-stack source inventory is unavailable") from exc
    if _source_inventory_sha256(root, paths) != identity.get("source_sha256"):
        raise BomValidationError("local-agent-stack source identity does not match bytes")
    lock_path = root / "runtime-dependencies.lock.json"
    lock = _load_json_bytes(
        _read_stable_regular_file(lock_path, "LAS runtime dependency lock"),
        "LAS runtime dependency lock",
    )
    if (
        set(lock)
        != {
            "agent_memory",
            "executables",
            "files",
            "hermes",
            "ollama",
            "python_execution_closure",
            "release_id",
            "scheduler_contract",
            "schema_version",
            "startup_receipts",
        }
        or lock.get("schema_version")
        != "local-agent-stack-runtime-dependencies-v2"
        or lock.get("release_id") != identity.get("release_id")
        or lock.get("python_execution_closure")
        != identity.get("python_execution_closure")
    ):
        raise BomValidationError("local-agent-stack dependency lock is invalid")
    hermes = lock.get("hermes")
    identity_hermes = identity["nested_dependencies"]["hermes"]
    if not isinstance(hermes, dict) or any(
        hermes.get(key) != identity_hermes.get(key)
        for key in ("distribution_version", "overlay_id", "api_source_sha256")
    ):
        raise BomValidationError("local-agent-stack Hermes lock does not match identity")
    for path_key, hash_key in (
        ("api_source_path", "api_source_sha256"),
        ("distribution_metadata_path", "distribution_metadata_sha256"),
    ):
        dependency_path = _expand_identity_path(hermes.get(path_key), root)
        raw = _read_stable_regular_file(dependency_path, f"LAS Hermes {path_key}")
        if _sha256_bytes(raw) != hermes.get(hash_key):
            raise BomValidationError("local-agent-stack Hermes artifact hash is invalid")


def _validate_antigravity_artifacts(
    root: Path, identity: Mapping[str, Any], projection: Mapping[str, Any]
) -> None:
    source_paths = _antigravity_source_paths(root)
    source_paths.extend([root / "dependency-lock.json", root / "pyproject.toml"])
    if _source_inventory_sha256(root, source_paths) != identity.get("source_sha256"):
        raise BomValidationError("Antigravity adapter source identity does not match bytes")
    lock_raw = _read_stable_regular_file(
        root / "dependency-lock.json", "Antigravity dependency lock"
    )
    lock = _load_json_bytes(lock_raw, "Antigravity dependency lock")
    agy = lock.get("agy")
    if (
        set(lock) != {"schema_version", "python_execution_closure", "agy"}
        or lock.get("schema_version") != "antigravity-adapter-dependency-lock-v2"
        or lock.get("python_execution_closure")
        != identity.get("python_execution_closure")
        or not isinstance(agy, dict)
        or set(agy) != {"version", "executable_sha256", "model_efforts"}
        or not isinstance(agy.get("model_efforts"), dict)
        or not agy["model_efforts"]
    ):
        raise BomValidationError("Antigravity dependency lock is invalid")
    env = projection["env"]
    agy_raw = _read_stable_regular_file(
        Path(env["ANTIGRAVITY_AGY_EXECUTABLE"]), "Antigravity executable"
    )
    model_hash = _sha256_bytes(
        json.dumps(
            dict(sorted(agy["model_efforts"].items())),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
    if (
        identity.get("dependency_lock_sha256") != _sha256_bytes(lock_raw)
        or identity.get("agy_version") != agy.get("version")
        or identity.get("agy_executable_sha256") != _sha256_bytes(agy_raw)
        or identity.get("agy_executable_sha256") != agy.get("executable_sha256")
        or identity.get("agy_model_contract_sha256") != model_hash
    ):
        raise BomValidationError("Antigravity dependency artifacts do not match identity")


def _validate_family_identity(
    server_id: str,
    identity: Mapping[str, Any],
    root: Path,
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    compatibility = identity.get("catalogue_router_compatibility")
    if server_id == "local-agent-stack":
        required = {
            "schema_version",
            "component",
            "runtime_version",
            "release_id",
            "catalogue_router_compatibility",
            "nested_dependencies",
            "python_execution_closure",
            "source_sha256",
        }
        if set(identity) != required or not isinstance(compatibility, dict):
            raise BomValidationError("local-agent-stack identity shape is invalid")
        if set(compatibility) != {
            "route_schema_version",
            "route_registry_schema_version",
            "authority_pointer_schema_version",
            "manifest_schema_versions",
        }:
            raise BomValidationError("local-agent-stack compatibility is invalid")
        nested = identity.get("nested_dependencies")
        hermes = nested.get("hermes") if isinstance(nested, dict) else None
        if not isinstance(nested, dict) or set(nested) != {"hermes"} or not isinstance(hermes, dict) or set(hermes) != {
            "distribution_version",
            "overlay_id",
            "api_source_sha256",
        }:
            raise BomValidationError("local-agent-stack Hermes identity is invalid")
        if (
            identity.get("schema_version") != "local-agent-stack-runtime-identity-v2"
            or identity.get("component") != server_id
            or SEMVER_RE.fullmatch(str(identity.get("runtime_version") or "")) is None
            or SEMVER_RE.fullmatch(str(hermes.get("distribution_version") or "")) is None
            or not isinstance(hermes.get("overlay_id"), str)
            or not hermes["overlay_id"]
            or SHA256_RE.fullmatch(str(hermes.get("api_source_sha256") or "")) is None
        ):
            raise BomValidationError("local-agent-stack identity values are invalid")
        manifests = compatibility.get("manifest_schema_versions")
    elif server_id == "antigravity-adapter":
        required = {
            "agy_executable_sha256",
            "agy_model_contract_sha256",
            "agy_version",
            "authority_pointer_schema_version",
            "component",
            "dependency_lock_schema_version",
            "dependency_lock_sha256",
            "release_id",
            "route_registry_schema_version",
            "route_schema_version",
            "runtime_version",
            "schema_version",
            "python_execution_closure",
            "source_sha256",
            "supported_manifest_schema_versions",
        }
        if set(identity) != required:
            raise BomValidationError("Antigravity adapter identity shape is invalid")
        compatibility = identity
        if (
            identity.get("schema_version") != "antigravity-adapter-runtime-identity-v3"
            or identity.get("component") != server_id
            or SEMVER_RE.fullmatch(str(identity.get("runtime_version") or "")) is None
            or SEMVER_RE.fullmatch(str(identity.get("agy_version") or "")) is None
            or identity.get("dependency_lock_schema_version")
            != "antigravity-adapter-dependency-lock-v2"
            or any(
                SHA256_RE.fullmatch(str(identity.get(key) or "")) is None
                for key in (
                    "agy_executable_sha256",
                    "agy_model_contract_sha256",
                    "dependency_lock_sha256",
                )
            )
        ):
            raise BomValidationError("Antigravity adapter identity values are invalid")
        manifests = identity.get("supported_manifest_schema_versions")
    else:
        raise BomValidationError("worker identity family is unsupported")
    if (
        SHA256_RE.fullmatch(str(identity.get("source_sha256") or "")) is None
        or not isinstance(identity.get("release_id"), str)
        or RELEASE_ID_RE.fullmatch(str(identity.get("release_id") or "")) is None
        or not str(identity["release_id"]).startswith(server_id + "-")
        or compatibility.get("route_schema_version") != "3.0"
        or compatibility.get("route_registry_schema_version") != 3
        or compatibility.get("authority_pointer_schema_version")
        != "capability-authority-pointer-v1"
        or not isinstance(manifests, list)
        or len(manifests) != len(set(manifests))
        or "1.3" not in manifests
        or any(not isinstance(item, str) or not item for item in manifests)
    ):
        raise BomValidationError(f"server runtime compatibility is invalid: {server_id}")
    if server_id == "local-agent-stack":
        _validate_las_artifacts(root, identity)
    else:
        _validate_antigravity_artifacts(root, identity, projection)
    return _validate_python_execution_closure(
        server_id,
        identity,
        root,
        Path(str(projection["command"])),
    )


def _parse_runtime_spec(spec: str) -> tuple[str, str]:
    if "=" not in spec:
        raise BomValidationError("runtime spec must be SERVER_ID=IDENTITY_RELATIVE_PATH")
    server_id, relative = (part.strip() for part in spec.split("=", 1))
    relative_path = Path(relative)
    if (
        SERVER_ID_RE.fullmatch(server_id) is None
        or not relative
        or len(relative) > 160
        or relative_path.is_absolute()
        or re.match(r"^[A-Za-z]:", relative)
        or any(part == ".." for part in relative_path.parts)
    ):
        raise BomValidationError("runtime spec is invalid")
    return server_id, relative


def _gateway_inventory_digest(
    root: Path,
    relative_paths: list[str] | tuple[str, ...],
    *,
    domain: bytes,
) -> tuple[str, list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    for relative in sorted(relative_paths, key=lambda item: item.encode("utf-8")):
        if (
            not relative
            or "\\" in relative
            or Path(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in relative.split("/"))
        ):
            raise BomValidationError("gateway runtime inventory path is invalid")
        path = root.joinpath(*relative.split("/"))
        if _is_link_or_reparse(path):
            raise BomValidationError("gateway runtime inventory contains a link")
        payload = _read_stable_regular_file(path, "gateway runtime inventory file")
        records.append(
            {
                "path": relative,
                "sha256": _sha256_bytes(payload),
                "size": len(payload),
            }
        )
    digest = hashlib.sha256(domain)
    for record in records:
        digest.update(record["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record["size"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(record["sha256"]))
    return digest.hexdigest(), records


def _gateway_runtime_tree_identity(root: Path, *, domain: bytes) -> dict[str, Any]:
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise BomValidationError("gateway runtime tree is unavailable") from exc
    if _is_link_or_reparse(root) or not resolved_root.is_dir():
        raise BomValidationError("gateway runtime tree root is invalid")
    relative_paths: list[str] = []
    seen_paths: set[str] = set()
    try:
        entries = list(resolved_root.rglob("*"))
    except OSError as exc:
        raise BomValidationError("gateway runtime tree is unreadable") from exc
    for path in entries:
        relative = path.relative_to(resolved_root)
        key = relative.as_posix().casefold()
        if key in seen_paths:
            raise BomValidationError(
                "gateway runtime tree has a case-insensitive duplicate"
            )
        seen_paths.add(key)
        if _is_link_or_reparse(path):
            raise BomValidationError("gateway runtime tree contains a link")
        if "__pycache__" in relative.parts:
            continue
        if path.is_file():
            relative_paths.append(relative.as_posix())
        elif not path.is_dir():
            raise BomValidationError("gateway runtime tree contains an unsupported entry")
    digest, records = _gateway_inventory_digest(
        resolved_root,
        relative_paths,
        domain=domain,
    )
    return {"file_count": len(records), "sha256": digest}


def _gateway_runtime_identity(
    config: Mapping[str, Any], config_path: Path
) -> tuple[dict[str, Any], bytes]:
    servers = config.get("mcp_servers")
    gateway_server = (
        servers.get(GATEWAY_CONFIG_SERVER_ID) if isinstance(servers, dict) else None
    )
    if gateway_server != {"url": GATEWAY_CONFIG_URL}:
        raise BomValidationError("Codex stability gateway configuration is invalid")
    codex_home = config_path.resolve(strict=True).parent
    gateway_root = (codex_home / "tools" / "codex-stability").resolve(strict=True)
    identity_path = (
        codex_home.joinpath(*GATEWAY_RUNTIME_IDENTITY_RELATIVE_PATH.split("/"))
    ).resolve(strict=True)
    if (
        _is_link_or_reparse(gateway_root)
        or not gateway_root.is_dir()
        or not identity_path.is_relative_to(gateway_root)
        or _is_link_or_reparse(identity_path)
    ):
        raise BomValidationError("gateway runtime identity path is invalid")
    raw = _read_stable_regular_file(identity_path, "gateway runtime identity")
    identity = _load_json_bytes(raw, "gateway runtime identity")
    python_runtime = identity.get("python_runtime")
    bytecode_cache = identity.get("python_bytecode_cache")
    source_files = identity.get("source_files")
    if (
        set(identity) != GATEWAY_RUNTIME_IDENTITY_KEYS
        or identity.get("schema_version") != GATEWAY_RUNTIME_IDENTITY_SCHEMA
        or identity.get("component") != GATEWAY_COMPONENT
        or identity.get("release_id") != GATEWAY_RELEASE_ID
        or identity.get("child_environment_policy_id")
        != WORKER_CHILD_ENVIRONMENT_POLICY_ID
        or identity.get("gateway_startup_environment_policy_id")
        != GATEWAY_STARTUP_ENVIRONMENT_POLICY_ID
        or identity.get("gateway_startup_python_flags")
        != GATEWAY_REQUIRED_PYTHON_FLAGS
        or not isinstance(bytecode_cache, dict)
        or set(bytecode_cache) != {"must_be_empty", "prefix_path"}
        or bytecode_cache.get("must_be_empty") is not True
        or not isinstance(bytecode_cache.get("prefix_path"), str)
        or not Path(bytecode_cache["prefix_path"]).is_absolute()
        or identity.get("python_injection_environment_keys")
        != PYTHON_FORBIDDEN_ENVIRONMENT_VARIABLES
        or not isinstance(python_runtime, dict)
        or set(python_runtime) != GATEWAY_PYTHON_RUNTIME_KEYS
        or not isinstance(source_files, list)
        or SEMVER_RE.fullmatch(str(python_runtime.get("version") or "")) is None
        or any(
            SHA256_RE.fullmatch(str(identity.get(key) or "")) is None
            for key in ("source_sha256",)
        )
        or any(
            SHA256_RE.fullmatch(str(python_runtime.get(key) or "")) is None
            for key in (
                "base_runtime_sha256",
                "console_executable_sha256",
                "dependency_lock_sha256",
                "site_packages_sha256",
                "windowless_executable_sha256",
            )
        )
        or any(
            isinstance(python_runtime.get(key), bool)
            or not isinstance(python_runtime.get(key), int)
            or python_runtime[key] < 1
            for key in ("base_runtime_file_count", "site_packages_file_count")
        )
    ):
        raise BomValidationError("gateway runtime identity shape is invalid")

    source_sha256, expected_source_files = _gateway_inventory_digest(
        gateway_root,
        GATEWAY_SOURCE_RELATIVE_PATHS,
        domain=GATEWAY_SOURCE_DOMAIN,
    )
    try:
        local_app_data = Path(os.environ["LOCALAPPDATA"]).resolve(strict=True)
        pycache_prefix = Path(str(bytecode_cache["prefix_path"])).resolve(
            strict=True
        )
        expected_pycache_prefix = (
            local_app_data / "Codex" / "stability" / "pycache" / "gateway"
        ).resolve(strict=True)
        pycache_is_empty = not any(pycache_prefix.iterdir())
        base_root = Path(str(python_runtime["base_root"])).resolve(strict=True)
        console = Path(str(python_runtime["console_executable_path"])).resolve(
            strict=True
        )
        windowless = Path(
            str(python_runtime["windowless_executable_path"])
        ).resolve(strict=True)
        dependency_lock = Path(
            str(python_runtime["dependency_lock_path"])
        ).resolve(strict=True)
        site_packages = Path(str(python_runtime["site_packages_path"])).resolve(
            strict=True
        )
    except (OSError, KeyError, TypeError) as exc:
        raise BomValidationError("gateway Python runtime path is invalid") from exc
    expected_site_packages = (
        gateway_root / ".venv" / "Lib" / "site-packages"
    ).resolve(strict=True)
    expected_dependency_lock = (gateway_root / "uv.lock").resolve(strict=True)
    if (
        not _same_resolved_path(console, base_root / "python.exe")
        or not _same_resolved_path(windowless, base_root / "pythonw.exe")
        or not _same_resolved_path(pycache_prefix, expected_pycache_prefix)
        or _is_link_or_reparse(pycache_prefix)
        or not pycache_prefix.is_dir()
        or not pycache_is_empty
        or not _same_resolved_path(site_packages, expected_site_packages)
        or not _same_resolved_path(dependency_lock, expected_dependency_lock)
    ):
        raise BomValidationError("gateway Python runtime closure escaped its authority")
    base_identity = _gateway_runtime_tree_identity(
        base_root,
        domain=GATEWAY_PYTHON_BASE_RUNTIME_DOMAIN,
    )
    site_identity = _gateway_runtime_tree_identity(
        site_packages,
        domain=GATEWAY_SITE_PACKAGES_DOMAIN,
    )
    expected_runtime = {
        "base_root": str(base_root),
        "base_runtime_file_count": base_identity["file_count"],
        "base_runtime_sha256": base_identity["sha256"],
        "console_executable_path": str(console),
        "console_executable_sha256": _sha256_bytes(
            _read_stable_regular_file(console, "gateway console Python")
        ),
        "dependency_lock_path": str(dependency_lock),
        "dependency_lock_sha256": _sha256_bytes(
            _read_stable_regular_file(dependency_lock, "gateway dependency lock")
        ),
        "site_packages_file_count": site_identity["file_count"],
        "site_packages_path": str(site_packages),
        "site_packages_sha256": site_identity["sha256"],
        "version": python_runtime["version"],
        "windowless_executable_path": str(windowless),
        "windowless_executable_sha256": _sha256_bytes(
            _read_stable_regular_file(windowless, "gateway windowless Python")
        ),
    }
    expected_identity = {
        "child_environment_policy_id": WORKER_CHILD_ENVIRONMENT_POLICY_ID,
        "component": GATEWAY_COMPONENT,
        "gateway_startup_environment_policy_id": (
            GATEWAY_STARTUP_ENVIRONMENT_POLICY_ID
        ),
        "gateway_startup_python_flags": dict(GATEWAY_REQUIRED_PYTHON_FLAGS),
        "python_bytecode_cache": {
            "must_be_empty": True,
            "prefix_path": str(pycache_prefix),
        },
        "python_injection_environment_keys": list(
            PYTHON_FORBIDDEN_ENVIRONMENT_VARIABLES
        ),
        "python_runtime": expected_runtime,
        "release_id": GATEWAY_RELEASE_ID,
        "schema_version": GATEWAY_RUNTIME_IDENTITY_SCHEMA,
        "source_files": expected_source_files,
        "source_sha256": source_sha256,
    }
    if identity != expected_identity:
        raise BomValidationError(
            "gateway runtime identity does not match current executable closure"
        )
    return identity, raw


def _gateway_runtime_binding(
    config: Mapping[str, Any], config_path: Path
) -> dict[str, Any]:
    identity, raw = _gateway_runtime_identity(config, config_path)
    gateway_server = config["mcp_servers"][GATEWAY_CONFIG_SERVER_ID]
    return {
        "config_server_id": GATEWAY_CONFIG_SERVER_ID,
        "identity_relative_path": GATEWAY_RUNTIME_IDENTITY_RELATIVE_PATH,
        "identity_sha256": _sha256_bytes(raw),
        "runtime_identity": identity,
        "server_config_sha256": _sha256_bytes(
            _canonical_json_bytes(gateway_server)
        ),
    }


def _runtime_binding(
    config: Mapping[str, Any], server_id: str, relative_identity: str
) -> dict[str, Any]:
    servers = config.get("mcp_servers")
    server = servers.get(server_id) if isinstance(servers, dict) else None
    if not isinstance(server, dict):
        raise BomValidationError(f"gateway-managed server is unavailable: {server_id}")
    projection, root, command_resolved = _worker_server_projection(config, server_id)
    try:
        identity_path = (root / relative_identity).resolve(strict=True)
    except OSError as exc:
        raise BomValidationError(f"server runtime path is unavailable: {server_id}") from exc
    if (
        not identity_path.is_relative_to(root)
        or not identity_path.is_file()
        or identity_path.is_symlink()
    ):
        raise BomValidationError(f"server runtime identity escaped its root: {server_id}")
    identity_raw = _read_stable_regular_file(identity_path, f"{server_id} identity")
    identity = _load_json_bytes(identity_raw, f"{server_id} identity")
    python_execution_closure = _validate_family_identity(
        server_id, identity, root, projection
    )
    compatibility = identity.get("catalogue_router_compatibility")
    if not isinstance(compatibility, dict):
        compatibility = identity
    release_id = identity.get("release_id")
    if (
        not isinstance(release_id, str)
        or RELEASE_ID_RE.fullmatch(release_id) is None
        or compatibility.get("route_schema_version") != "3.0"
        or compatibility.get("route_registry_schema_version") != 3
    ):
        raise BomValidationError(f"server runtime compatibility is invalid: {server_id}")
    return {
        "config_server_id": server_id,
        "identity_relative_path": relative_identity.replace("\\", "/"),
        "identity_sha256": _sha256_bytes(identity_raw),
        "command_sha256": _sha256_bytes(
            _read_stable_regular_file(command_resolved, f"{server_id} command")
        ),
        "python_execution_closure": python_execution_closure,
        "server_config_sha256": _sha256_bytes(_canonical_json_bytes(projection)),
        "release_id": release_id,
        "route_schema_version": "3.0",
        "route_registry_schema_version": 3,
    }


def render_bom(
    config_path: Path | str,
    schema_path: Path | str,
    runtime_specs: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    resolved_config_path = Path(config_path).resolve(strict=True)
    config, _ = _load_config(resolved_config_path)
    schema, schema_raw = _load_schema(Path(schema_path))
    parsed = [_parse_runtime_spec(spec) for spec in runtime_specs]
    if not parsed or len({server_id for server_id, _ in parsed}) != len(parsed):
        raise BomValidationError("runtime specs are empty or duplicate a server")
    if dict(parsed) != REQUIRED_RUNTIME_SPECS:
        raise BomValidationError(
            "runtime specs must contain the exact LAS and Antigravity worker closure"
        )
    runtimes = {
        server_id: _runtime_binding(config, server_id, relative)
        for server_id, relative in sorted(parsed)
    }
    value = {
        "schema_version": BOM_SCHEMA_VERSION,
        "gateway_runtime": _gateway_runtime_binding(config, resolved_config_path),
        "runtimes": runtimes,
    }
    _validate_schema(value, schema)
    payload = _canonical_json_bytes(value, pretty=True)
    return {
        "bom": value,
        "bom_bytes": payload,
        "bom_sha256": _sha256_bytes(payload),
        "config_sha256": _config_projection_sha256(
            config, set(REQUIRED_RUNTIME_SPECS)
        ),
        "schema_sha256": _sha256_bytes(schema_raw),
    }


def _validate_candidate(
    candidate_path: Path,
    schema_path: Path,
    config_path: Path,
) -> dict[str, Any]:
    raw = _read_stable_regular_file(candidate_path, "worker-runtime BOM candidate")
    value = _load_json_bytes(raw, "worker-runtime BOM candidate")
    schema, schema_raw = _load_schema(schema_path)
    _validate_schema(value, schema)
    if raw != _canonical_json_bytes(value, pretty=True):
        raise BomValidationError("worker-runtime BOM candidate is not canonical")
    config, _ = _load_config(config_path)
    gateway_runtime = value.get("gateway_runtime")
    if (
        not isinstance(gateway_runtime, dict)
        or set(gateway_runtime) != GATEWAY_RUNTIME_BINDING_KEYS
        or _gateway_runtime_binding(config, config_path) != gateway_runtime
    ):
        raise BomValidationError(
            "gateway runtime identity changed after candidate review"
        )
    runtimes = value.get("runtimes")
    if (
        not isinstance(runtimes, dict)
        or set(runtimes) != set(REQUIRED_RUNTIME_SPECS)
    ):
        raise BomValidationError(
            "worker-runtime BOM must contain the exact LAS and Antigravity worker closure"
        )
    for server_id, binding in runtimes.items():
        if not isinstance(binding, dict):
            raise BomValidationError("worker-runtime BOM binding is invalid")
        if binding.get("identity_relative_path") != REQUIRED_RUNTIME_SPECS[server_id]:
            raise BomValidationError(
                f"worker-runtime identity path is not canonical: {server_id}"
            )
        current = _runtime_binding(
            config,
            server_id,
            str(binding.get("identity_relative_path") or ""),
        )
        if current != binding:
            raise BomValidationError(
                f"worker-runtime identity changed after candidate review: {server_id}"
            )
    return {
        "bom": value,
        "bom_bytes": raw,
        "bom_sha256": _sha256_bytes(raw),
        "config_sha256": _config_projection_sha256(config, set(runtimes)),
        "schema_sha256": _sha256_bytes(schema_raw),
    }


def _target_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"sha256": MISSING, "size": 0}
    if path.is_symlink() or not path.is_file():
        raise PreconditionError(f"worker-runtime BOM target is not a regular file: {path}")
    payload = path.read_bytes()
    return {"sha256": _sha256_bytes(payload), "size": len(payload)}


class _ExclusiveLock:
    def __init__(self, path: Path, timeout_seconds: float) -> None:
        self.path = path
        self.timeout_seconds = max(0.0, timeout_seconds)
        self.stream: Any = None

    def __enter__(self) -> "_ExclusiveLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.is_symlink():
            raise LockError("worker-runtime BOM lock cannot be a symbolic link")
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
                    raise LockError("worker-runtime BOM lock is busy") from exc
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


def _binding(
    verified: Mapping[str, Any], target: Path, options: ApplyOptions
) -> dict[str, Any]:
    return {
        "candidate_sha256": verified["bom_sha256"],
        "config_sha256": verified["config_sha256"],
        "expected_target_sha256": options.expected_target_sha256,
        "promoter_sha256": _promoter_sha256(),
        "schema_sha256": verified["schema_sha256"],
        "target": str(target),
        "transaction_id": options.transaction_id,
    }


def _receipt(journal_path: Path, journal: Mapping[str, Any], outcome: str) -> dict[str, Any]:
    body = {
        "candidate_sha256": journal["candidate_sha256"],
        "completed_at": journal["updated_at"],
        "config_sha256": journal["config_sha256"],
        "error": journal.get("error"),
        "expected_target_sha256": journal["expected_target_sha256"],
        "journal_sha256": _sha256_file(journal_path),
        "outcome": outcome,
        "promoter_sha256": journal["promoter_sha256"],
        "schema_sha256": journal["schema_sha256"],
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "target": journal["target"],
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
        raise ReplayError("worker-runtime BOM receipt digest is invalid")
    if receipt.get("journal_sha256") != _sha256_file(journal_path):
        raise ReplayError("worker-runtime BOM receipt no longer matches its journal")


def _rollback(target: Path, transaction_root: Path, journal: Mapping[str, Any]) -> None:
    current = _target_state(target)["sha256"]
    if current == journal["expected_target_sha256"]:
        return
    if current != journal["candidate_sha256"]:
        raise RecoveryError("rollback refused to overwrite external target drift")
    if journal["expected_target_sha256"] == MISSING:
        target.unlink()
        _fsync_directory(target.parent)
        return
    backup = transaction_root / "rollback" / "worker-runtime-bom.json"
    if (
        not backup.is_file()
        or _sha256_file(backup) != journal["expected_target_sha256"]
    ):
        raise RecoveryError("worker-runtime BOM rollback backup is unavailable")
    _atomic_write(target, backup.read_bytes())
    if _target_state(target)["sha256"] != journal["expected_target_sha256"]:
        raise RecoveryError("worker-runtime BOM rollback did not restore the baseline")


def _fault(name: str, configured: str | None) -> None:
    if name == configured:
        raise InjectedFailure(f"injected worker-runtime BOM failure at {name}")


def apply_bom(options: ApplyOptions) -> dict[str, Any]:
    if TRANSACTION_ID_RE.fullmatch(options.transaction_id) is None:
        raise BomPromotionError("transaction identifier is invalid")
    for label, digest in (
        ("expected target", options.expected_target_sha256),
        ("expected candidate", options.expected_candidate_sha256),
    ):
        if digest != MISSING and SHA256_RE.fullmatch(digest) is None:
            raise BomPromotionError(f"{label} digest is invalid")

    candidate = Path(options.candidate_path).resolve(strict=True)
    schema = Path(options.schema_path).resolve(strict=True)
    config = Path(options.config_path).resolve(strict=True)
    target = Path(options.target_path).resolve(strict=False)
    if not target.is_absolute():
        raise BomPromotionError("worker-runtime BOM target must be absolute")
    verified = _validate_candidate(candidate, schema, config)
    if verified["bom_sha256"] != options.expected_candidate_sha256:
        raise PreconditionError("reviewed worker-runtime BOM candidate changed")
    binding = _binding(verified, target, options)
    state_root = target.parent / STATE_DIRECTORY
    transactions_root = state_root / "transactions"
    if state_root.exists() and (state_root.is_symlink() or not state_root.is_dir()):
        raise ReplayError("worker-runtime BOM state root is invalid")
    state_root.mkdir(parents=True, exist_ok=True)
    transactions_root.mkdir(exist_ok=True)

    with _ExclusiveLock(state_root / "promotion.lock", options.lock_timeout_seconds):
        verified = _validate_candidate(candidate, schema, config)
        if _binding(verified, target, options) != binding:
            raise PreconditionError("worker-runtime BOM inputs changed after lock")
        transaction_root = transactions_root / options.transaction_id
        journal_path = transaction_root / "journal.json"
        receipt_path = transaction_root / "receipt.json"
        if transaction_root.exists() and (
            transaction_root.is_symlink() or not transaction_root.is_dir()
        ):
            raise ReplayError("worker-runtime BOM transaction root is invalid")
        if receipt_path.exists():
            receipt = _load_json_bytes(
                _read_stable_regular_file(receipt_path, "worker-runtime BOM receipt"),
                "worker-runtime BOM receipt",
            )
            _verify_receipt(receipt, journal_path)
            if any(receipt.get(key) != value for key, value in binding.items()):
                raise ReplayError("worker-runtime BOM transaction is rebound")
            if receipt.get("outcome") != "completed":
                raise ReplayError("worker-runtime BOM transaction did not complete")
            if _target_state(target)["sha256"] != verified["bom_sha256"]:
                raise ReplayError("completed BOM transaction target changed")
            return receipt
        if transaction_root.exists() and any(transaction_root.iterdir()):
            if not journal_path.is_file():
                raise ReplayError("interrupted BOM transaction has no journal")
            journal = _load_json_bytes(journal_path.read_bytes(), "BOM journal")
            if any(journal.get(key) != value for key, value in binding.items()):
                raise ReplayError("interrupted BOM transaction is rebound")
            _rollback(target, transaction_root, journal)
            journal["phase"] = "rolled_back"
            journal["error"] = "interrupted transaction recovered"
            _save_journal(journal_path, journal)
            _write_once(
                receipt_path,
                _canonical_json_bytes(_receipt(journal_path, journal, "rolled_back")),
            )
            raise RecoveryError("interrupted worker-runtime BOM transaction rolled back")

        current = _target_state(target)
        if current["sha256"] != options.expected_target_sha256:
            raise PreconditionError("live worker-runtime BOM precondition changed")
        transaction_root.mkdir(parents=True, exist_ok=False)
        if options.expected_target_sha256 != MISSING:
            backup = transaction_root / "rollback" / "worker-runtime-bom.json"
            _write_once(backup, target.read_bytes())
        stage = transaction_root / "stage" / "worker-runtime-bom.json"
        _write_once(stage, verified["bom_bytes"])
        journal = {
            **binding,
            "error": None,
            "phase": "prepared",
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "updated_at": _utc_now(),
        }
        _save_journal(journal_path, journal)
        promoted = False
        try:
            _fault("before_promote", options.fault_injection)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage, target)
            _fsync_directory(target.parent)
            promoted = True
            journal["phase"] = "promoted"
            _save_journal(journal_path, journal)
            _fault("after_promote", options.fault_injection)
            if _target_state(target)["sha256"] != verified["bom_sha256"]:
                raise PreconditionError("promoted worker-runtime BOM bytes changed")
            _validate_candidate(target, schema, config)
            journal["phase"] = "completed"
            _save_journal(journal_path, journal)
            receipt = _receipt(journal_path, journal, "completed")
            _write_once(receipt_path, _canonical_json_bytes(receipt))
            return receipt
        except Exception as exc:
            journal["error"] = f"{type(exc).__name__}: {exc}"
            if promoted:
                _rollback(target, transaction_root, journal)
            journal["phase"] = "rolled_back"
            _save_journal(journal_path, journal)
            _write_once(
                receipt_path,
                _canonical_json_bytes(_receipt(journal_path, journal, "rolled_back")),
            )
            raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    render = subparsers.add_parser("render")
    render.add_argument("--config", required=True)
    render.add_argument("--schema", required=True)
    render.add_argument("--runtime", action="append", required=True)
    render.add_argument("--output", required=True)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--candidate", required=True)
    apply.add_argument("--schema", required=True)
    apply.add_argument("--config", required=True)
    apply.add_argument("--target", required=True)
    apply.add_argument("--transaction-id", required=True)
    apply.add_argument("--expected-target-sha256", required=True)
    apply.add_argument("--expected-candidate-sha256", required=True)
    apply.add_argument("--lock-timeout-seconds", type=float, default=30.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "render":
            rendered = render_bom(
                arguments.config,
                arguments.schema,
                arguments.runtime,
            )
            output = Path(arguments.output).resolve(strict=False)
            _atomic_write(output, rendered["bom_bytes"])
            result = {
                key: value
                for key, value in rendered.items()
                if key not in {"bom", "bom_bytes"}
            }
            result["output"] = str(output)
        else:
            result = apply_bom(
                ApplyOptions(
                    candidate_path=arguments.candidate,
                    schema_path=arguments.schema,
                    config_path=arguments.config,
                    target_path=arguments.target,
                    transaction_id=arguments.transaction_id,
                    expected_target_sha256=arguments.expected_target_sha256,
                    expected_candidate_sha256=arguments.expected_candidate_sha256,
                    lock_timeout_seconds=arguments.lock_timeout_seconds,
                )
            )
    except BomPromotionError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"status": "ok", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
