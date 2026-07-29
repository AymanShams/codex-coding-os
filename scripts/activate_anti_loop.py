#!/usr/bin/env python3
"""Transactionally activate the installed universal anti-loop runtime hooks."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tomllib
from typing import Any, Callable, Mapping
import uuid


ACTIVATION_PROTOCOL = "ccos-anti-loop-activation-v1"
AUTHORITY_PROTOCOL = "ccos-anti-loop-bootstrap-authority-v1"
HOOK_STATUS_MESSAGE = "Enforcing mandatory anti-loop latch"
HOOK_RELATIVE_PATH = "hooks/anti-loop-runtime/anti_loop_runtime.py"
REQUIRED_INSTALLED_PATHS = (
    HOOK_RELATIVE_PATH,
    "scripts/agent/case_state.py",
    "scripts/agent/case_human_disposition_verifier.py",
)
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
FIXED_GIT_WINDOWS_PATHS = (
    Path(r"C:\Program Files\Git\cmd\git.exe"),
    Path(r"C:\Program Files\Git\bin\git.exe"),
)
FIXED_GIT_POSIX_PATHS = (
    Path("/usr/bin/git"),
    Path("/usr/local/bin/git"),
    Path("/opt/homebrew/bin/git"),
    Path("/opt/local/bin/git"),
)
TABLE_RE = re.compile(r"^\s*\[.*\]\s*(?:#.*)?$")
FEATURE_HOOKS_RE = re.compile(
    r"^(?P<indent>\s*)hooks\s*=\s*(?P<value>true|false)(?P<tail>\s*(?:#.*)?)$",
    re.IGNORECASE,
)


class ActivationError(RuntimeError):
    """The universal anti-loop activation precondition or transaction failed."""


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError as exc:
        raise ActivationError(f"cannot hash required file {path}: {exc}") from exc


def _require_hash(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not HASH_RE.fullmatch(normalized):
        raise ActivationError(f"{label} must be one lowercase SHA-256 digest")
    return normalized


def _require_uuid(value: str, label: str, *, version: int | None = None) -> str:
    normalized = str(value or "").strip().lower()
    if not UUID_RE.fullmatch(normalized):
        raise ActivationError(f"{label} must be one canonical UUID")
    parsed = uuid.UUID(normalized)
    if str(parsed) != normalized or (version is not None and parsed.version != version):
        suffix = f"v{version}" if version is not None else ""
        raise ActivationError(f"{label} must be one canonical UUID{suffix}")
    return normalized


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActivationError(f"{label} is not readable strict UTF-8 JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ActivationError(f"{label} must be one JSON object: {path}")
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ActivationError(f"cannot inspect required path {path}: {exc}") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _direct_regular_file(path: Path, label: str) -> Path:
    raw = Path(path)
    if not raw.is_absolute() or any(part in {".", ".."} for part in raw.parts):
        raise ActivationError(f"{label} must be one canonical absolute path")
    current = raw
    while True:
        if _is_link_or_reparse(current):
            raise ActivationError(
                f"{label} must not use a symbolic link or reparse point: {current}"
            )
        parent = current.parent
        if parent == current:
            break
        current = parent
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ActivationError(f"{label} is unavailable: {raw}: {exc}") from exc
    if not resolved.is_file():
        raise ActivationError(f"{label} must be one existing absolute regular file")
    if resolved.stat().st_nlink != 1:
        raise ActivationError(f"{label} must not be a hardlinked executable")
    if os.name != "nt" and not os.access(resolved, os.X_OK):
        raise ActivationError(f"{label} must be executable")
    return resolved


def _fixed_git_candidates() -> tuple[Path, ...]:
    return FIXED_GIT_WINDOWS_PATHS if os.name == "nt" else FIXED_GIT_POSIX_PATHS


def _resolve_git_executable() -> Path:
    for candidate in _fixed_git_candidates():
        try:
            return _direct_regular_file(candidate, "fixed Git executable")
        except ActivationError:
            continue
    raise ActivationError(
        "a fixed direct Git executable is unavailable; ambient PATH is not trusted"
    )


def _git_environment(executable: Path) -> dict[str, str]:
    environment = {
        name: value
        for name in ("SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR")
        if (value := os.environ.get(name))
    }
    environment.update(
        {
            "PATH": str(executable.parent),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
            "LANG": "C",
        }
    )
    return environment


def _run_git(
    root: Path,
    *arguments: str,
    git_executable: Path | None = None,
) -> str:
    executable = _direct_regular_file(
        git_executable or _resolve_git_executable(),
        "fixed Git executable",
    )
    try:
        completed = subprocess.run(
            [str(executable), "-C", str(root), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=30,
            env=_git_environment(executable),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ActivationError(f"Git source verification failed: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ActivationError(f"Git source verification failed: {detail}")
    return completed.stdout.strip()


def _source_identity(
    source_root: Path,
    *,
    expected_commit: str,
    expected_bundle: str,
) -> dict[str, Any]:
    root = source_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ActivationError("source root must be a directory")
    git_executable = _resolve_git_executable()
    git_root_text = _run_git(
        root,
        "rev-parse",
        "--show-toplevel",
        git_executable=git_executable,
    )
    try:
        git_root = Path(git_root_text).resolve(strict=True)
    except OSError as exc:
        raise ActivationError(
            f"source Git repository root is unavailable: {git_root_text}: {exc}"
        ) from exc
    if git_root != root:
        raise ActivationError("source root must be the exact Git repository root")
    actual_commit = _run_git(
        root,
        "rev-parse",
        "HEAD",
        git_executable=git_executable,
    ).lower()
    if actual_commit != expected_commit:
        raise ActivationError("source Git HEAD differs from ExpectedSourceCommit")
    if _run_git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        git_executable=git_executable,
    ):
        raise ActivationError("source Git worktree must be clean for activation")
    repository = _run_git(
        root,
        "config",
        "--get",
        "remote.origin.url",
        git_executable=git_executable,
    )
    if not repository:
        raise ActivationError("source Git origin is required")
    manifest_path = root / "install-bundle.manifest.json"
    manifest = _load_json(manifest_path, "install bundle manifest")
    if manifest.get("aggregate_sha256") != expected_bundle:
        raise ActivationError("install bundle aggregate differs from ExpectedBundleSha256")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ActivationError("install bundle entries must be an array")
    by_path: dict[str, Mapping[str, Any]] = {}
    for item in entries:
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
            raise ActivationError("install bundle contains a malformed entry")
        path = str(item["path"]).replace("\\", "/")
        if path in by_path:
            raise ActivationError("install bundle contains a duplicate entry")
        by_path[path] = item
    for relative in REQUIRED_INSTALLED_PATHS:
        item = by_path.get(relative)
        target = root.joinpath(*relative.split("/"))
        if not isinstance(item, Mapping) or not target.is_file():
            raise ActivationError(f"required activation payload is absent: {relative}")
        expected_sha = _require_hash(str(item.get("sha256", "")), f"bundle hash for {relative}")
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ActivationError(f"bundle size is invalid for {relative}")
        if target.stat().st_size != size or _sha_file(target) != expected_sha:
            raise ActivationError(f"source payload differs from bundle entry: {relative}")
    return {
        "root": root,
        "commit": actual_commit,
        "repository": repository,
        "bundle_sha256": expected_bundle,
        "manifest_sha256": _sha_file(manifest_path),
        "entries": by_path,
    }


def _installed_identity(
    codex_home: Path,
    source: Mapping[str, Any],
) -> dict[str, Any]:
    installed_root = (codex_home / "coding-os").resolve(strict=True)
    if not installed_root.is_dir():
        raise ActivationError("installed Coding OS support root is unavailable")
    manifest = _load_json(installed_root / "install-manifest.json", "installed manifest")
    package = manifest.get("package")
    source_record = manifest.get("source")
    targets = manifest.get("targets")
    if (
        not isinstance(package, Mapping)
        or package.get("name") != "codex-coding-os"
        or package.get("bundle_sha256") != source["bundle_sha256"]
        or not isinstance(source_record, Mapping)
        or source_record.get("git_commit") != source["commit"]
        or source_record.get("kind") != "git"
        or not isinstance(targets, Mapping)
        or str(targets.get("support_root", "")).casefold() != str(installed_root).casefold()
    ):
        raise ActivationError("installed Coding OS manifest is not bound to the exact source commit and bundle")
    for relative in REQUIRED_INSTALLED_PATHS:
        source_path = source["root"].joinpath(*relative.split("/"))
        installed_path = installed_root.joinpath(*relative.split("/"))
        if not installed_path.is_file() or _sha_file(installed_path) != _sha_file(source_path):
            raise ActivationError(f"installed activation payload differs from merged source: {relative}")
    return {
        "root": installed_root,
        "manifest_sha256": _sha_file(installed_root / "install-manifest.json"),
    }


def _newline_for(raw: bytes) -> str:
    return "\r\n" if raw.count(b"\r\n") > raw.count(b"\n") / 2 else "\n"


def _python_identity(python_path: Path, expected_sha256: str) -> dict[str, str]:
    executable = _direct_regular_file(python_path, "PythonWindows")
    expected = _require_hash(expected_sha256, "ExpectedPythonSha256")
    actual = _sha_file(executable)
    if actual != expected:
        raise ActivationError("selected Python differs from ExpectedPythonSha256")
    return {"path": str(executable), "sha256": actual}


def _hook_group(installed_root: Path, python_executable: Path) -> dict[str, Any]:
    runtime_hook = installed_root.joinpath(*HOOK_RELATIVE_PATH.split("/")).resolve(
        strict=True
    )
    if not runtime_hook.is_file() or not runtime_hook.is_absolute():
        raise ActivationError("installed anti-loop runtime hook is unavailable")
    python_text = str(python_executable)
    hook_text = str(runtime_hook)
    if any(character in python_text + hook_text for character in {'"', "\r", "\n"}):
        raise ActivationError("absolute runtime command paths contain unsupported characters")
    command = f'"{python_text}" -B "{hook_text}"'
    return {
        "matcher": ".*",
        "hooks": [
            {
                "type": "command",
                "command": command,
                "commandWindows": command,
                "timeout": 30,
                "statusMessage": HOOK_STATUS_MESSAGE,
            }
        ],
    }


def _handler_mentions_runtime(handler: Any) -> bool:
    if not isinstance(handler, Mapping):
        return False
    return any(
        isinstance(handler.get(field), str)
        and "anti_loop_runtime.py" in str(handler[field]).replace("\\", "/").casefold()
        for field in ("command", "commandWindows")
    )


def _merge_hooks(
    raw: bytes,
    *,
    group: Mapping[str, Any],
) -> tuple[bytes, dict[str, int], bool]:
    try:
        document = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActivationError(f"hooks.json is not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ActivationError("hooks.json must be one object")
    events = document.get("hooks")
    if not isinstance(events, dict):
        raise ActivationError("hooks.json must contain one hooks object")
    indices: dict[str, int] = {}
    changed = False
    for event in ("PreToolUse", "PostToolUse"):
        groups = events.setdefault(event, [])
        if not isinstance(groups, list):
            raise ActivationError(f"hooks.json {event} must be an array")
        exact = [index for index, item in enumerate(groups) if item == group]
        mentions = [
            index
            for index, item in enumerate(groups)
            if isinstance(item, Mapping)
            and isinstance(item.get("hooks"), list)
            and any(_handler_mentions_runtime(handler) for handler in item["hooks"])
        ]
        if len(exact) > 1 or any(index not in exact for index in mentions):
            raise ActivationError(f"hooks.json contains a conflicting or duplicate {event} anti-loop hook")
        if exact:
            indices[event] = exact[0]
        else:
            groups.append(json.loads(json.dumps(group)))
            indices[event] = len(groups) - 1
            changed = True
    if not changed:
        return raw, indices, False
    newline = _newline_for(raw)
    encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    if newline == "\r\n":
        encoded = encoded.replace("\n", "\r\n")
    return encoded.encode("utf-8"), indices, True


def _trust_hash(event_label: str, group: Mapping[str, Any], *, windows: bool) -> str:
    hooks = group.get("hooks")
    if not isinstance(hooks, list) or len(hooks) != 1 or not isinstance(hooks[0], Mapping):
        raise ActivationError("anti-loop hook group must contain one command handler")
    raw_handler = hooks[0]
    command = raw_handler.get("commandWindows") if windows else raw_handler.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ActivationError("anti-loop hook command is unavailable for target platform")
    normalized_handler: dict[str, Any] = {
        "type": "command",
        "command": command,
        "async": bool(raw_handler.get("async", False)),
        "timeout": max(1, int(raw_handler.get("timeout", 600))),
    }
    status = raw_handler.get("statusMessage")
    if status is not None:
        normalized_handler["statusMessage"] = status
    additional = raw_handler.get("additionalContextLimit")
    if additional not in (None, 2500):
        normalized_handler["additionalContextLimit"] = additional
    identity = {
        "event_name": event_label,
        "matcher": group.get("matcher"),
        "hooks": [normalized_handler],
    }
    return "sha256:" + _sha_bytes(_canonical_json(identity))


def _set_hooks_feature(text: str, newline: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.strip().casefold() == "[features]"]
    if len(starts) > 1:
        raise ActivationError("config.toml contains duplicate [features] tables")
    if not starts:
        prefix = "" if not text or text.endswith(("\n", "\r")) else newline
        return text + prefix + f"[features]{newline}hooks = true{newline}", True
    start = starts[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if TABLE_RE.match(lines[index].rstrip("\r\n")):
            end = index
            break
    matches: list[tuple[int, re.Match[str], str]] = []
    for index in range(start + 1, end):
        body = lines[index].rstrip("\r\n")
        match = FEATURE_HOOKS_RE.match(body)
        if match:
            matches.append((index, match, lines[index][len(body) :]))
    if len(matches) > 1:
        raise ActivationError("config.toml contains duplicate features.hooks values")
    if matches:
        index, match, ending = matches[0]
        if match.group("value").casefold() == "true":
            return text, False
        lines[index] = f'{match.group("indent")}hooks = true{match.group("tail")}{ending}'
        return "".join(lines), True
    lines.insert(start + 1, f"hooks = true{newline}")
    return "".join(lines), True


def _trust_key(hooks_path: Path, event_label: str, index: int) -> str:
    return f"{hooks_path}:{event_label}:{index}:0"


def _merge_config(
    raw: bytes,
    *,
    trust_records: Mapping[str, str],
) -> tuple[bytes, bool]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ActivationError(f"config.toml is not strict UTF-8: {exc}") from exc
    newline = _newline_for(raw)
    text, changed = _set_hooks_feature(text, newline)
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ActivationError(f"config.toml is invalid after feature activation: {exc}") from exc
    state = parsed.get("hooks", {}).get("state", {}) if isinstance(parsed.get("hooks"), Mapping) else {}
    if not isinstance(state, Mapping):
        raise ActivationError("config.toml hooks.state must be a table")
    additions: list[tuple[str, str]] = []
    for key, digest in trust_records.items():
        current = state.get(key)
        if current is None:
            additions.append((key, digest))
            continue
        if (
            not isinstance(current, Mapping)
            or current.get("trusted_hash") != digest
            or current.get("enabled") is False
        ):
            raise ActivationError(f"config.toml contains conflicting hook trust state: {key}")
    if additions:
        if text and not text.endswith(("\n", "\r")):
            text += newline
        if text and not text.endswith(newline * 2):
            text += newline
        for offset, (key, digest) in enumerate(additions):
            if "'" in key:
                raise ActivationError("hook trust key cannot contain a single quote")
            if offset:
                text += newline
            text += (
                f"[hooks.state.'{key}']{newline}"
                f'trusted_hash = "{digest}"{newline}'
            )
        changed = True
    try:
        final = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ActivationError(f"config.toml is invalid after trust activation: {exc}") from exc
    if final.get("features", {}).get("hooks") is not True:
        raise ActivationError("config.toml did not activate features.hooks")
    final_state = final.get("hooks", {}).get("state", {})
    for key, digest in trust_records.items():
        if not isinstance(final_state.get(key), Mapping) or final_state[key].get("trusted_hash") != digest:
            raise ActivationError(f"config.toml did not persist exact hook trust state: {key}")
    return text.encode("utf-8"), changed


def _authority(
    *,
    thread_id: str,
    request_id: str,
    reference: str,
) -> dict[str, Any]:
    canonical_thread = _require_uuid(thread_id, "AuthorityThreadId", version=7)
    process_thread = str(os.environ.get("CODEX_THREAD_ID") or "").strip().lower()
    if process_thread != canonical_thread:
        raise ActivationError("AuthorityThreadId must equal this process CODEX_THREAD_ID")
    canonical_request = _require_uuid(request_id, "AuthorityRequestId")
    bounded_reference = str(reference or "").strip()
    if not bounded_reference or len(bounded_reference) > 512:
        raise ActivationError("AuthorityReference must be one nonempty bounded value")
    return {
        "protocol_version": AUTHORITY_PROTOCOL,
        "schema_version": 1,
        "source": "explicit-user-approval",
        "thread_id": canonical_thread,
        "request_id": canonical_request,
        "reference": bounded_reference,
    }


def _preflight_hashes(
    paths: Mapping[str, Path],
    expected: Mapping[str, str],
) -> dict[str, str]:
    actual = {name: _sha_file(path) for name, path in paths.items()}
    for name, digest in expected.items():
        if actual.get(name) != digest:
            raise ActivationError(f"{name} changed after the caller captured its expected hash")
    return actual


def _record_digest(record: Mapping[str, Any]) -> str:
    return _sha_bytes(
        _canonical_json({name: value for name, value in record.items() if name != "record_sha256"})
    )


def _verified_record(
    record_path: Path,
    *,
    source_commit: str,
    bundle_sha256: str,
    python_identity: Mapping[str, str],
) -> dict[str, Any]:
    if not record_path.is_file():
        raise ActivationError("the one-shot anti-loop activation record is absent")
    recorded = _load_json(record_path, "anti-loop activation record")
    source_record = recorded.get("source")
    if (
        recorded.get("protocol_version") != ACTIVATION_PROTOCOL
        or recorded.get("status") != "ACTIVE"
        or not isinstance(source_record, Mapping)
        or source_record.get("commit") != source_commit
        or source_record.get("bundle_sha256") != bundle_sha256
        or recorded.get("python") != dict(python_identity)
        or recorded.get("record_sha256") != _record_digest(recorded)
    ):
        raise ActivationError("the anti-loop activation record is invalid or belongs to another source")
    return recorded


def _activate_locked(
    *,
    mode: str,
    source_root: Path,
    codex_home: Path,
    expected_source_commit: str,
    expected_bundle_sha256: str,
    expected_agents_sha256: str,
    expected_hooks_sha256: str,
    expected_config_sha256: str,
    expected_store_sha256: str,
    python_windows: Path,
    expected_python_sha256: str,
    authority_thread_id: str,
    authority_request_id: str,
    authority_reference: str,
    writer: Callable[[Path, bytes], None] = _atomic_write,
) -> dict[str, Any]:
    normalized_mode = str(mode).casefold()
    if normalized_mode not in {"plan", "apply", "verify"}:
        raise ActivationError("mode must be plan, apply, or verify")
    expected_commit = str(expected_source_commit or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        raise ActivationError("ExpectedSourceCommit must be one lowercase Git SHA")
    expected_bundle = _require_hash(expected_bundle_sha256, "ExpectedBundleSha256")
    expected = {
        "agents": _require_hash(expected_agents_sha256, "ExpectedAgentsSha256"),
        "hooks": _require_hash(expected_hooks_sha256, "ExpectedHooksSha256"),
        "config": _require_hash(expected_config_sha256, "ExpectedConfigSha256"),
        "store": _require_hash(expected_store_sha256, "ExpectedStoreSha256"),
    }
    home = codex_home.expanduser().resolve(strict=True)
    if not home.is_dir():
        raise ActivationError("CodexHome must be an existing directory")
    python = _python_identity(python_windows, expected_python_sha256)
    python_path = Path(python["path"])
    authority = _authority(
        thread_id=authority_thread_id,
        request_id=authority_request_id,
        reference=authority_reference,
    )
    source = _source_identity(
        source_root,
        expected_commit=expected_commit,
        expected_bundle=expected_bundle,
    )
    installed = _installed_identity(home, source)
    paths = {
        "agents": home / "AGENTS.md",
        "hooks": home / "hooks.json",
        "config": home / "config.toml",
        "store": home / "case-state" / "case-state.json",
    }
    before = _preflight_hashes(paths, expected)
    hooks_raw = paths["hooks"].read_bytes()
    config_raw = paths["config"].read_bytes()
    group = _hook_group(installed["root"], python_path)
    hooks_after, indices, hooks_changed = _merge_hooks(hooks_raw, group=group)
    trust_records = {
        _trust_key(paths["hooks"], "pre_tool_use", indices["PreToolUse"]): _trust_hash(
            "pre_tool_use", group, windows=True
        ),
        _trust_key(paths["hooks"], "post_tool_use", indices["PostToolUse"]): _trust_hash(
            "post_tool_use", group, windows=True
        ),
    }
    config_after, config_changed = _merge_config(config_raw, trust_records=trust_records)
    after_hashes = {
        "agents": before["agents"],
        "hooks": _sha_bytes(hooks_after),
        "config": _sha_bytes(config_after),
        "store": before["store"],
    }
    result = {
        "protocol_version": ACTIVATION_PROTOCOL,
        "schema_version": 1,
        "status": "PLANNED" if normalized_mode == "plan" else "ACTIVE",
        "idempotent": not hooks_changed and not config_changed,
        "reconciled": False,
        "source": {
            "repository": source["repository"],
            "commit": source["commit"],
            "bundle_sha256": source["bundle_sha256"],
            "manifest_sha256": source["manifest_sha256"],
        },
        "installed": {
            "root": str(installed["root"]),
            "manifest_sha256": installed["manifest_sha256"],
            "hook_sha256": _sha_file(installed["root"].joinpath(*HOOK_RELATIVE_PATH.split("/"))),
        },
        "python": python,
        "authority": authority,
        "hook_indices": indices,
        "hook_trust": trust_records,
        "before_sha256": before,
        "after_sha256": after_hashes,
    }
    result["record_sha256"] = _record_digest(result)
    record_path = home / ".coding-os-install" / "anti-loop-activation.json"
    if normalized_mode == "plan":
        return result
    if normalized_mode == "verify":
        if hooks_changed or config_changed:
            raise ActivationError("universal anti-loop activation is absent or incomplete")
        _verified_record(
            record_path,
            source_commit=source["commit"],
            bundle_sha256=source["bundle_sha256"],
            python_identity=python,
        )
        return result
    if result["idempotent"]:
        try:
            _verified_record(
                record_path,
                source_commit=source["commit"],
                bundle_sha256=source["bundle_sha256"],
                python_identity=python,
            )
        except ActivationError:
            result["idempotent"] = False
            result["reconciled"] = True
            result["record_sha256"] = _record_digest(result)
        else:
            return result
    old_hooks = hooks_raw
    old_config = config_raw
    old_record = record_path.read_bytes() if record_path.is_file() else None
    surfaces_written = False
    try:
        if hooks_changed or config_changed:
            surfaces_written = True
            writer(paths["hooks"], hooks_after)
            writer(paths["config"], config_after)
        live_after = {
            "agents": _sha_file(paths["agents"]),
            "hooks": _sha_file(paths["hooks"]),
            "config": _sha_file(paths["config"]),
            "store": _sha_file(paths["store"]),
        }
        if live_after != after_hashes:
            raise ActivationError("activation post-write hashes differ from the planned transaction")
        record = dict(result)
        record["status"] = "ACTIVE"
        record["record_sha256"] = _record_digest(record)
        writer(
            record_path,
            (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        persisted = _load_json(record_path, "anti-loop activation record")
        if persisted.get("record_sha256") != _record_digest(persisted):
            raise ActivationError("activation record digest verification failed")
    except Exception as exc:
        rollback_errors: list[str] = []
        if surfaces_written:
            for path, content in ((paths["hooks"], old_hooks), (paths["config"], old_config)):
                try:
                    _atomic_write(path, content)
                except Exception as rollback_exc:
                    rollback_errors.append(f"{path}: {rollback_exc}")
        try:
            if old_record is None:
                if record_path.exists():
                    record_path.unlink()
            else:
                _atomic_write(record_path, old_record)
        except Exception as rollback_exc:
            rollback_errors.append(f"{record_path}: {rollback_exc}")
        detail = f"; rollback failures: {', '.join(rollback_errors)}" if rollback_errors else ""
        if isinstance(exc, ActivationError):
            raise ActivationError(f"{exc}{detail}") from exc
        raise ActivationError(f"activation transaction failed: {exc}{detail}") from exc
    return result


def activate(
    *,
    mode: str,
    source_root: Path,
    codex_home: Path,
    expected_source_commit: str,
    expected_bundle_sha256: str,
    expected_agents_sha256: str,
    expected_hooks_sha256: str,
    expected_config_sha256: str,
    expected_store_sha256: str,
    python_windows: Path,
    expected_python_sha256: str,
    authority_thread_id: str,
    authority_request_id: str,
    authority_reference: str,
    writer: Callable[[Path, bytes], None] = _atomic_write,
) -> dict[str, Any]:
    from install_transaction import TransactionError, exclusive_install_lock

    home = codex_home.expanduser().resolve(strict=True)
    if not home.is_dir():
        raise ActivationError("CodexHome must be an existing directory")
    transaction_id = f"anti-loop-{uuid.uuid4()}"
    try:
        with exclusive_install_lock(
            home / ".coding-os-install",
            transaction_id,
            "anti-loop-activation",
        ):
            return _activate_locked(
                mode=mode,
                source_root=source_root,
                codex_home=home,
                expected_source_commit=expected_source_commit,
                expected_bundle_sha256=expected_bundle_sha256,
                expected_agents_sha256=expected_agents_sha256,
                expected_hooks_sha256=expected_hooks_sha256,
                expected_config_sha256=expected_config_sha256,
                expected_store_sha256=expected_store_sha256,
                python_windows=python_windows,
                expected_python_sha256=expected_python_sha256,
                authority_thread_id=authority_thread_id,
                authority_request_id=authority_request_id,
                authority_reference=authority_reference,
                writer=writer,
            )
    except TransactionError as exc:
        raise ActivationError(f"cannot acquire the universal install transaction lock: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "apply", "verify"))
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--codex-home", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-bundle-sha256", required=True)
    parser.add_argument("--expected-agents-sha256", required=True)
    parser.add_argument("--expected-hooks-sha256", required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--expected-store-sha256", required=True)
    parser.add_argument("--python-windows", required=True)
    parser.add_argument("--expected-python-sha256", required=True)
    parser.add_argument("--authority-thread-id", required=True)
    parser.add_argument("--authority-request-id", required=True)
    parser.add_argument("--authority-reference", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = activate(
            mode=args.mode,
            source_root=Path(args.source_root),
            codex_home=Path(args.codex_home),
            expected_source_commit=args.expected_source_commit,
            expected_bundle_sha256=args.expected_bundle_sha256,
            expected_agents_sha256=args.expected_agents_sha256,
            expected_hooks_sha256=args.expected_hooks_sha256,
            expected_config_sha256=args.expected_config_sha256,
            expected_store_sha256=args.expected_store_sha256,
            python_windows=Path(args.python_windows),
            expected_python_sha256=args.expected_python_sha256,
            authority_thread_id=args.authority_thread_id,
            authority_request_id=args.authority_request_id,
            authority_reference=args.authority_reference,
        )
    except (ActivationError, OSError) as exc:
        print(f"activation error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
