#!/usr/bin/env python3
"""Fail-closed PreToolUse/PostToolUse enforcement for the canonical anti-loop latch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
from typing import Any, Mapping
import uuid


MANAGED_ROOT = Path(__file__).resolve().parents[2]
AGENT_SCRIPTS = MANAGED_ROOT / "scripts" / "agent"
if str(AGENT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(AGENT_SCRIPTS))

import case_state


HOOK_PROTOCOL_VERSION = "ccos-anti-loop-hook-v1"
HOOK_RESULT_PROTOCOL_VERSION = "ccos-anti-loop-hook-result-v1"
HOOK_PHASES = frozenset({"PreToolUse", "PostToolUse"})
PRE_EVENTS = frozenset({"SUPPORT_MUTATION", "SUPPORT_CHAIN_PROPOSED"})
POST_EVENTS = frozenset({"SUPPORT_FAILURE", "PRODUCT_HEAD_ADVANCED"})
MUTATING_TOOL_NAMES = frozenset(
    {
        "apply_patch",
        "bash",
        "edit",
        "exec_command",
        "shell",
        "shell_command",
        "write",
        "write_file",
    }
)
UNIVERSAL_STATE_FILE_PREFIXES = (
    "active-slice",
    "current-state",
    "handoff",
    "session-decision",
)
READ_ONLY_SHELL_PATTERNS = (
    re.compile(r"^(?:git\s+)?(?:status|diff|log|show|rev-parse|ls-files)\b", re.I),
    re.compile(r"^(?:rg|grep|findstr|select-string|get-content|get-childitem|ls|dir)\b", re.I),
)
READ_ONLY_TOOL_VERBS = frozenset(
    {"diff", "find", "get", "list", "open", "read", "search", "show", "status", "view"}
)
MUTATING_TOOL_VERBS = frozenset(
    {
        "apply", "commit", "copy", "create", "delete", "deploy", "edit", "merge",
        "move", "patch", "pr", "publish", "push", "remove", "rename", "replace",
        "update", "write",
    }
)
CONTROL_COMMAND_PATTERNS = (
    re.compile(r"^(?:python(?:3)?|py)(?:\.exe)?\s+.*(?:case_state|session_continuity|handoff)", re.I),
    re.compile(r"^(?:pwsh|powershell|bash|sh)\b.*(?:install(?:\.ps1|\.sh)?|handoff)", re.I),
    re.compile(r"^(?:npm|pnpm|yarn|pip|pip3)\s+install\b", re.I),
)
GIT_COMMIT_PATTERN = re.compile(r"^git(?:\s+-C\s+\S+)?\s+commit\b", re.I)
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
HOOK_EVENT_NAMESPACE = uuid.UUID("7fe1f0f8-43fb-5c45-a319-6034252812b7")


class HookError(RuntimeError):
    """The hook cannot safely authorize the current tool event."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def _result(
    *,
    phase: str,
    decision: str,
    reason_code: str,
    reason: str,
    case_id: str | None = None,
    revision: int | None = None,
    event: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "protocol_version": HOOK_RESULT_PROTOCOL_VERSION,
        "schema_version": 1,
        "phase": phase,
        "ccos_decision": decision,
        "reason_code": reason_code,
        "reason": reason,
        "case_id": case_id,
        "revision": revision,
        "event": dict(event) if isinstance(event, Mapping) else None,
    }
    return payload


def _normalize_path(value: str) -> str:
    raw = value.strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    return re.sub(r"/+", "/", raw).casefold()


def _repository_relative_path(value: str, root: str | None) -> str | None:
    raw = value.strip()
    if not raw:
        return None
    if root is not None:
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                relative = candidate.expanduser().resolve(strict=False).relative_to(
                    Path(root).resolve(strict=True)
                )
            except (OSError, ValueError):
                return None
            raw = relative.as_posix()
    raw = raw.replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    raw = re.sub(r"/+", "/", raw)
    if not raw or raw.startswith("/") or raw == ".." or raw.startswith("../"):
        return None
    return raw


def _fallback_state_path(path: str) -> bool:
    normalized = path.casefold()
    if normalized == case_state.ANTI_LOOP_SUPPORT_SCOPE_PATH.casefold():
        return True
    name = normalized.rsplit("/", 1)[-1]
    return any(name.startswith(prefix) for prefix in UNIVERSAL_STATE_FILE_PREFIXES)


def _is_control_path(value: str, cwd: str | None = None) -> bool:
    root = _exact_git_root(cwd) if cwd else None
    path = _repository_relative_path(value, root)
    if path is None:
        return False
    if root is not None:
        try:
            head = _git(root, "rev-parse", "HEAD").casefold()
            scope = case_state._anti_loop_support_scope(Path(root), head)
            return case_state._anti_loop_path_is_support_only(
                path, scope["support_only_patterns"]
            )
        except (HookError, case_state.CaseStateError, OSError):
            pass
    return _fallback_state_path(path)


def _patch_paths(tool_input: Mapping[str, Any]) -> list[str]:
    raw = tool_input.get("command")
    if not isinstance(raw, str):
        raw = tool_input.get("patch")
    if not isinstance(raw, str):
        raw = tool_input.get("input")
    if not isinstance(raw, str):
        return []
    paths: list[str] = []
    update_open = False
    for line in raw.splitlines():
        match = re.match(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", line)
        if match:
            paths.append(match.group(1))
            update_open = line.startswith("*** Update File:")
            continue
        move = re.match(r"^\*\*\* Move to:\s*(.+?)\s*$", line)
        if move:
            if not update_open:
                raise HookError("apply_patch Move to requires one immediately preceding Update File")
            paths.append(move.group(1))
            update_open = False
    normalized: list[str] = []
    seen: set[str] = set()
    for path in paths:
        relative = _repository_relative_path(path, None)
        if relative is None:
            raise HookError("apply_patch contains an absolute or traversal path")
        key = relative.casefold()
        if key in seen:
            raise HookError("apply_patch contains a duplicate source or destination path")
        seen.add(key)
        normalized.append(relative)
    return normalized


def _shell_command(tool_input: Mapping[str, Any]) -> str:
    for field in ("command", "cmd"):
        value = tool_input.get(field)
        if isinstance(value, str):
            return value.strip()
    return ""


def _command_segments(command: str) -> list[str]:
    """Split only at unquoted shell separators; quoted prose never becomes a command."""

    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command):
        character = command[index]
        if quote is not None:
            current.append(character)
            if character == quote:
                quote = None
            elif character == "\\" and index + 1 < len(command):
                index += 1
                current.append(command[index])
            index += 1
            continue
        if character in {'"', "'"}:
            quote = character
            current.append(character)
            index += 1
            continue
        two = command[index : index + 2]
        if character in {";", "|"} or two in {"&&", "||"}:
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            index += 2 if two in {"&&", "||"} else 1
            continue
        current.append(character)
        index += 1
    segment = "".join(current).strip()
    if segment:
        segments.append(segment)
    return segments


def _has_unquoted_shell_control(command: str) -> bool:
    quote: str | None = None
    index = 0
    while index < len(command):
        character = command[index]
        if quote is not None:
            if quote == "'" and character == "'":
                quote = None
                index += 1
                continue
            if quote == "'":
                index += 1
                continue
            if (
                character == chr(96)
                or command[index : index + 2]
                in {"$" + "(", "$" + "{", "@" + "("}
            ):
                return True
            if character == quote:
                quote = None
            elif character == "\\" and index + 1 < len(command):
                index += 1
            index += 1
            continue
        if character in {'"', "'"}:
            quote = character
            index += 1
            continue
        if character in {";", "|", "&", ">", "<", "\r", "\n", "(", ")"} or character == chr(96):
            return True
        if command[index : index + 2] in {"$" + "(", "$" + "{", "@" + "("}:
            return True
        index += 1
    return quote is not None


def _simple_command_tokens(command: str) -> list[str] | None:
    if not command or _has_unquoted_shell_control(command):
        return None
    if len(_command_segments(command)) != 1:
        return None
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    return tokens or None


def _single_command_text(command: str) -> str | None:
    return _command_text(command) if _simple_command_tokens(command) is not None else None


def _is_self_reporting_handoff(command: str) -> bool:
    tokens = _simple_command_tokens(command)
    if tokens is None:
        return False
    tokens = list(tokens)
    executable = tokens.pop(0).replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if executable not in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
        return False
    if executable in {"py", "py.exe"} and tokens and tokens[0] == "-3":
        tokens.pop(0)
    if len(tokens) < 2:
        return False
    script = tokens.pop(0).replace("\\", "/").casefold()
    if script != "scripts/agent/session_continuity.py" or tokens.pop(0).casefold() != "handoff":
        return False
    values: dict[str, str] = {}
    write = False
    while tokens:
        flag = tokens.pop(0)
        if flag == "--write":
            if write:
                return False
            write = True
            continue
        if flag not in {"--topic", "--reason", "--next"} or flag in values or not tokens:
            return False
        value = tokens.pop(0)
        if not value.strip() or value.startswith("--") or len(value) > 4096:
            return False
        values[flag] = value
    if not write or set(values) != {"--topic", "--reason", "--next"}:
        return False
    topic = values["--topic"]
    return bool(
        len(topic) <= 128
        and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", topic)
    )


def _disposition_command(command: str) -> dict[str, Any] | None:
    tokens = _simple_command_tokens(command)
    if tokens is None:
        return None
    tokens = list(tokens)
    executable = tokens.pop(0).replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if executable not in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
        return None
    if executable in {"py", "py.exe"} and tokens and tokens[0] == "-3":
        tokens.pop(0)
    if not tokens:
        return None
    engine_path = Path(tokens.pop(0))
    if not engine_path.is_absolute():
        return None
    try:
        if engine_path.resolve(strict=True) != Path(case_state.__file__).resolve(strict=True):
            return None
    except OSError:
        return None
    state_root: str | None = None
    json_seen = False
    while tokens and tokens[0] not in {
        "anti-loop-stop-case",
        "anti-loop-ship-product-with-control-quarantined",
    }:
        flag = tokens.pop(0)
        if flag == "--json" and not json_seen:
            json_seen = True
            continue
        if flag == "--state-root" and state_root is None and tokens:
            state_root = tokens.pop(0)
            continue
        return None
    if not tokens or state_root is None:
        return None
    operation = tokens.pop(0)
    values: dict[str, str] = {}
    expected_flags = {
        "--case-id",
        "--native-thread-id",
        "--native-turn-id",
        "--request-id",
        "--expected-revision",
    }
    while tokens:
        flag = tokens.pop(0)
        if flag not in expected_flags or flag in values or not tokens:
            return None
        value = tokens.pop(0)
        if not value or value.startswith("--"):
            return None
        values[flag] = value
    if set(values) != expected_flags:
        return None
    try:
        canonical_root = case_state.default_state_root().resolve(strict=False)
        submitted_root = Path(state_root).resolve(strict=False)
        case_id = case_state.canonical_case_id(values["--case-id"])
        native_thread_id = case_state.require_native_uuid7(
            values["--native-thread-id"], "anti-loop native human thread id"
        )
        native_turn_id = case_state.require_native_uuid7(
            values["--native-turn-id"], "anti-loop native human turn id"
        )
        request_id = case_state.require_request_id(values["--request-id"])
        expected_revision = int(values["--expected-revision"])
    except (ValueError, OSError, case_state.CaseStateError):
        return None
    if submitted_root != canonical_root or expected_revision < 0:
        return None
    return {
        "operation": operation,
        "case_id": case_id,
        "native_thread_id": native_thread_id,
        "native_turn_id": native_turn_id,
        "request_id": request_id,
        "expected_revision": expected_revision,
        "state_root": str(canonical_root),
    }


def _is_mutating_tool(tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    name = tool_name.casefold()
    tokens = {token for token in re.split(r"[^a-z0-9]+", name) if token}
    if name not in MUTATING_TOOL_NAMES:
        if name in {"web__run", "view_image"}:
            return False
        if tokens & MUTATING_TOOL_VERBS:
            return True
        if tokens & READ_ONLY_TOOL_VERBS and not (tokens & MUTATING_TOOL_VERBS):
            return False
        serialized = json.dumps(tool_input, ensure_ascii=False, sort_keys=True).casefold()
        input_tokens = {token for token in re.split(r"[^a-z0-9]+", serialized) if token}
        if input_tokens & MUTATING_TOOL_VERBS:
            return True
        return True
    if name not in {"bash", "exec_command", "shell", "shell_command"}:
        return True
    command = _shell_command(tool_input)
    if not command:
        return True
    if _is_proven_read_only_support_check(command):
        return False
    segments = _command_segments(command)
    return not segments or any(
        not any(pattern.match(segment) for pattern in READ_ONLY_SHELL_PATTERNS)
        for segment in segments
    )


def _tool_target_paths(value: Any) -> list[str]:
    targets: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if (
                str(key).casefold() in {"path", "file_path", "target", "destination"}
                and isinstance(item, str)
                and item.strip()
            ):
                targets.append(item)
            elif isinstance(item, (Mapping, list)):
                targets.extend(_tool_target_paths(item))
    elif isinstance(value, list):
        for item in value:
            targets.extend(_tool_target_paths(item))
    return targets


def _is_known_product_shell_command(command: str) -> bool:
    patterns = (
        re.compile(r"^git(?:\s+-c\s+\S+)*\s+(?:add|commit)\b", re.I),
    )
    return bool(command) and all(
        any(pattern.match(_command_text(segment)) for pattern in patterns)
        for segment in _command_segments(command)
    )


def _control_surface(
    tool_name: str, tool_input: Mapping[str, Any], cwd: str | None
) -> str | None:
    name = tool_name.casefold()
    if name == "apply_patch":
        paths = _patch_paths(tool_input)
        if any(_is_control_path(path, cwd) for path in paths):
            return "CONTROL_FILE_PATCH"
        return None
    if name in {"edit", "write", "write_file"} or (
        name not in {"bash", "exec_command", "shell", "shell_command"}
        and _is_mutating_tool(tool_name, tool_input)
    ):
        targets = _tool_target_paths(tool_input)
        if any(_is_control_path(path, cwd) for path in targets):
            return "CONTROL_FILE_WRITE"
        return None
    if name not in {"bash", "exec_command", "shell", "shell_command"}:
        return None
    command = _shell_command(tool_input)
    if _looks_like_bootstrap_carrier(command):
        return "CONTROL_COMMAND"
    if _is_explicit_exclusion(command):
        return None
    helper = _support_helper_command(command)
    if isinstance(helper, Mapping) and helper.get("mode") == "support_mutation":
        return "HOOK_OWNED_SUPPORT_MUTATION"
    if _is_support_check(command):
        return "CONTROL_CHECK"
    if _looks_like_control_patch_carrier(command):
        if _control_patch_command(command) is None:
            raise HookError("guarded control-patch carrier has incomplete or invalid arguments")
        return "GUARDED_CONTROL_PATCH"
    if _is_self_reporting_handoff(command):
        return "SELF_REPORTING_SUPPORT_HELPER"
    if _is_support_chain_broker(command):
        return "SUPPORT_CHAIN_COMMAND"
    for segment in _command_segments(command):
        if any(pattern.match(segment) for pattern in CONTROL_COMMAND_PATTERNS):
            return "CONTROL_COMMAND"
        tokens = [token.strip("'\"`()[]{};,>") for token in re.split(r"\s+", segment)]
        if any(_is_control_path(token, cwd) for token in tokens if token):
            return "CONTROL_PATH_COMMAND"
    return None


def _case_matches_thread(case: Mapping[str, Any], thread_id: str) -> bool:
    bindings = case.get("bindings")
    runtime = case.get("runtime")
    threads = bindings.get("thread") if isinstance(bindings, Mapping) else None
    actors = runtime.get("actors") if isinstance(runtime, Mapping) else None
    return (
        isinstance(threads, list)
        and thread_id in threads
        and isinstance(actors, Mapping)
        and isinstance(actors.get(thread_id), Mapping)
    )


def _command_text(command: str) -> str:
    return re.sub(r"\s+", " ", command.replace("\\", "/").strip()).casefold()


def _pinned_python_script_tokens(command: str, expected_script: Path) -> list[str] | None:
    tokens = _simple_command_tokens(command)
    if tokens is None or len(tokens) < 2:
        return None
    values = list(tokens)
    executable = Path(values.pop(0)).expanduser()
    script = Path(values.pop(0)).expanduser()
    if not executable.is_absolute() or not script.is_absolute():
        return None
    try:
        case_state.regular_file_identity(executable, stop=Path(executable.anchor))
        case_state.regular_file_identity(script, stop=Path(script.anchor))
        if executable.resolve(strict=True) != Path(sys.executable).resolve(strict=True):
            return None
        if script.resolve(strict=True) != expected_script.resolve(strict=True):
            return None
    except (OSError, case_state.CaseStateError):
        return None
    return values


def _exact_flag_values(
    tokens: list[str],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, str] | None:
    allowed = required | (optional or set())
    values: dict[str, str] = {}
    remaining = list(tokens)
    while remaining:
        flag = remaining.pop(0)
        if flag not in allowed or flag in values or not remaining:
            return None
        value = remaining.pop(0)
        if not value or value.startswith("--") or len(value) > 4096:
            return None
        values[flag] = value
    if not required.issubset(values):
        return None
    return values


def _bootstrap_command(
    command: str, store: case_state.CaseStore
) -> dict[str, Any] | None:
    tokens = _pinned_python_script_tokens(command, Path(case_state.__file__))
    if tokens is None:
        return None
    state_root: str | None = None
    json_seen = False
    values = list(tokens)
    while values and values[0] not in {"register", "bind"}:
        flag = values.pop(0)
        if flag == "--json" and not json_seen:
            json_seen = True
            continue
        if flag == "--state-root" and state_root is None and values:
            state_root = values.pop(0)
            continue
        return None
    if not values or state_root is None:
        return None
    operation = values.pop(0)
    if operation == "register":
        parsed = _exact_flag_values(
            values,
            required={
                "--case-id",
                "--objective",
                "--request-id",
                "--expected-store-revision",
            },
        )
    else:
        parsed = _exact_flag_values(
            values,
            required={
                "--case-id",
                "--kind",
                "--value",
                "--request-id",
                "--expected-revision",
            },
            optional={"--repository"},
        )
    if parsed is None:
        return None
    try:
        submitted_root = Path(state_root).expanduser().resolve(strict=False)
        if submitted_root != store.state_root.resolve(strict=False):
            return None
        case_id = case_state.canonical_case_id(parsed["--case-id"])
        request_id = case_state.require_request_id(parsed["--request-id"])
        revision_flag = (
            "--expected-store-revision"
            if operation == "register"
            else "--expected-revision"
        )
        expected_revision = int(parsed[revision_flag])
        if expected_revision < 0:
            return None
        if operation == "register":
            case_state._nonempty(parsed["--objective"], "objective", 4096)
        else:
            kind = parsed["--kind"].casefold()
            if kind not in case_state.BINDING_KINDS:
                return None
            case_state.normalize_binding(kind, parsed["--value"])
            if "--repository" in parsed:
                case_state.normalize_repo_url(parsed["--repository"])
    except (ValueError, OSError, case_state.CaseStateError):
        return None
    return {
        "operation": operation,
        "case_id": case_id,
        "request_id": request_id,
        "expected_revision": expected_revision,
    }


def _bootstrap_carrier(command: str) -> bool:
    tokens = _pinned_python_script_tokens(command, Path(case_state.__file__))
    return bool(tokens and any(token.casefold() in {"register", "bind"} for token in tokens))


def _untrusted_python_script_tokens(command: str) -> tuple[str, list[str]] | None:
    tokens = _simple_command_tokens(command)
    if tokens is None or len(tokens) < 2:
        return None
    values = list(tokens)
    executable = Path(values.pop(0)).name.casefold()
    if executable not in {
        "python",
        "python.exe",
        "python3",
        "python3.exe",
        "py",
        "py.exe",
    }:
        return None
    if executable in {"py", "py.exe"} and values and values[0] == "-3":
        values.pop(0)
    if not values:
        return None
    return values.pop(0).replace("\\", "/").casefold(), values


def _looks_like_bootstrap_carrier(command: str) -> bool:
    parsed = _untrusted_python_script_tokens(command)
    if parsed is None:
        return False
    script, tokens = parsed
    return bool(
        script.endswith("/scripts/agent/case_state.py")
        and any(token.casefold() in {"register", "bind"} for token in tokens)
    )


def _control_patch_carrier(command: str) -> bool:
    tokens = _pinned_python_script_tokens(command, Path(__file__))
    return bool(tokens and tokens[0].casefold() == "control-patch")


def _looks_like_control_patch_carrier(command: str) -> bool:
    parsed = _untrusted_python_script_tokens(command)
    if parsed is None:
        return False
    script, tokens = parsed
    return bool(
        script.endswith("/anti_loop_runtime.py")
        and tokens
        and tokens[0].casefold() == "control-patch"
    )


def _control_patch_command(command: str) -> dict[str, Any] | None:
    tokens = _pinned_python_script_tokens(command, Path(__file__))
    if tokens is None or not tokens or tokens.pop(0).casefold() != "control-patch":
        return None
    parsed = _exact_flag_values(
        tokens,
        required={
            "--repository-root",
            "--patch-file",
            "--sha256",
            "--state-root",
            "--case-id",
            "--actor-thread-id",
            "--actor-role",
            "--repository",
            "--product-head",
            "--support-action",
            "--request-id",
            "--expected-revision",
        },
    )
    if parsed is None:
        return None
    try:
        repository_root = case_state.normalize_binding(
            "worktree", parsed["--repository-root"]
        )
        patch_file = Path(parsed["--patch-file"]).expanduser()
        state_root = Path(parsed["--state-root"]).expanduser().resolve(strict=False)
        case_id = case_state.canonical_case_id(parsed["--case-id"])
        actor_thread_id = case_state.normalize_binding(
            "thread", parsed["--actor-thread-id"]
        )
        actor_role = case_state._nonempty(
            parsed["--actor-role"], "control-patch actor role", 64
        ).casefold()
        repository = case_state.normalize_repo_url(parsed["--repository"])
        product_head = case_state.require_sha(
            parsed["--product-head"], "control-patch product head"
        )
        support_action = case_state._nonempty(
            parsed["--support-action"], "control-patch support action", 256
        )
        request_id = case_state.require_request_id(parsed["--request-id"])
        expected_revision = int(parsed["--expected-revision"])
        sha256 = parsed["--sha256"].casefold()
        if (
            not patch_file.is_absolute()
            or not re.fullmatch(r"[0-9a-f]{64}", sha256)
            or expected_revision < 1
        ):
            return None
    except (ValueError, OSError, case_state.CaseStateError):
        return None
    return {
        "repository_root": repository_root,
        "patch_file": str(patch_file),
        "sha256": sha256,
        "state_root": str(state_root),
        "case_id": case_id,
        "actor_thread_id": actor_thread_id,
        "actor_role": actor_role,
        "repository": repository,
        "product_head": product_head,
        "support_action": support_action,
        "request_id": request_id,
        "expected_revision": expected_revision,
    }


def _is_handoff_mutation(command: str) -> bool:
    return _is_self_reporting_handoff(command)


def _is_support_chain_broker(command: str) -> bool:
    return _support_chain_command(command) is not None


def _support_chain_command(command: str) -> dict[str, Any] | None:
    tokens = _simple_command_tokens(command)
    if tokens is None:
        return None
    tokens = list(tokens)
    executable = tokens.pop(0).replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if executable not in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
        return None
    if executable in {"py", "py.exe"} and tokens and tokens[0] == "-3":
        tokens.pop(0)
    if len(tokens) < 2:
        return None
    script = tokens.pop(0).replace("\\", "/").casefold()
    if not script.endswith("hooks/anti-loop-runtime/anti_loop_runtime.py"):
        return None
    if tokens.pop(0).casefold() != "support-chain-proposed":
        return None
    flags = {
        "--repository-root",
        "--state-root",
        "--case-id",
        "--actor-thread-id",
        "--actor-role",
        "--repository",
        "--product-head",
        "--parent-event-id",
        "--support-action",
        "--request-id",
        "--expected-revision",
    }
    values: dict[str, str] = {}
    while tokens:
        flag = tokens.pop(0)
        if flag not in flags or flag in values or not tokens:
            return None
        value = tokens.pop(0)
        if not value or value.startswith("--") or len(value) > 4096:
            return None
        values[flag] = value
    if set(values) != flags:
        return None
    try:
        case_id = case_state.canonical_case_id(values["--case-id"])
        actor_thread_id = case_state.normalize_binding(
            "thread", values["--actor-thread-id"]
        )
        repository = case_state.normalize_repo_url(values["--repository"])
        worktree = case_state.normalize_binding(
            "worktree", values["--repository-root"]
        )
        product_head = case_state.require_sha(
            values["--product-head"], "support-chain product head"
        )
        parent_event_id = case_state.require_request_id(
            values["--parent-event-id"]
        )
        request_id = case_state.require_request_id(values["--request-id"])
        expected_revision = int(values["--expected-revision"])
        state_root = str(Path(values["--state-root"]).resolve(strict=False))
    except (ValueError, OSError, case_state.CaseStateError):
        return None
    actor_role = values["--actor-role"].strip().casefold()
    support_action = values["--support-action"].strip()
    if (
        expected_revision < 1
        or not actor_role
        or len(actor_role) > 64
        or not support_action
        or len(support_action) > 128
    ):
        return None
    return {
        "case_id": case_id,
        "actor_thread_id": actor_thread_id,
        "actor_role": actor_role,
        "repository": repository,
        "worktree": worktree,
        "product_head": product_head,
        "parent_event_id": parent_event_id,
        "support_action": support_action,
        "request_id": request_id,
        "expected_revision": expected_revision,
        "state_root": state_root,
    }


def _verify_support_chain_parent(
    case: Mapping[str, Any],
    details: Mapping[str, Any],
    *,
    cwd: str,
    state_root: Path,
) -> str:
    normalized_cwd = case_state.normalize_binding("worktree", cwd)
    if (
        details.get("case_id") != case.get("case_id")
        or details.get("expected_revision") != case.get("revision")
        or details.get("worktree") != normalized_cwd
        or Path(str(details.get("state_root"))).resolve(strict=False)
        != state_root.resolve(strict=False)
        or _exact_git_root(normalized_cwd) != normalized_cwd
    ):
        raise HookError("support-chain reporter is not bound to this exact case revision and store")
    live_head = _git(normalized_cwd, "rev-parse", "HEAD").casefold()
    if live_head != details.get("product_head"):
        raise HookError("support-chain reporter product head differs from live Git HEAD")
    events = case.get("events")
    parent = (
        events.get(details.get("parent_event_id"))
        if isinstance(events, Mapping)
        else None
    )
    result = parent.get("result") if isinstance(parent, Mapping) else None
    if (
        not isinstance(parent, Mapping)
        or parent.get("operation") != "record_anti_loop_event"
        or not isinstance(result, Mapping)
        or result.get("event_id") != details.get("parent_event_id")
        or result.get("event_type") not in {"SUPPORT_MUTATION", "SUPPORT_FAILURE"}
        or result.get("actor_thread_id") != details.get("actor_thread_id")
        or result.get("actor_role") != details.get("actor_role")
        or result.get("repository") != details.get("repository")
        or result.get("worktree") != details.get("worktree")
        or result.get("product_head") != details.get("product_head")
        or result.get("revision") != case.get("revision")
    ):
        raise HookError(
            "support-chain reporter requires the immediately prior exact stored support event"
        )
    return (
        f"parent_event={details['parent_event_id']}; "
        f"next={details['support_action']}"
    )


def _is_support_check(command: str) -> bool:
    normalized = _single_command_text(command)
    if normalized is None:
        return False
    patterns = (
        re.compile(r"^(?:python(?:3)?|py(?: -3)?)(?:\.exe)? scripts/agent/session_continuity\.py validate\b"),
        re.compile(r"^node scripts/checks/(?:automation-helpers-check|docs-check|pr-body-check|session-continuity-check)\.mjs\b"),
        re.compile(r"^node --test scripts/agent/tests/case-state\.test\.mjs\b"),
        re.compile(r"^corepack pnpm run (?:agent:state-check|agent:case-state:test|pr:check)\b"),
    )
    return any(pattern.match(normalized) for pattern in patterns)


def _is_proven_read_only_support_check(command: str) -> bool:
    normalized = _single_command_text(command)
    return bool(
        normalized
        and re.fullmatch(
            r"(?:python(?:3)?|py(?: -3)?)(?:\.exe)? "
            r"scripts/agent/session_continuity\.py validate",
            normalized,
        )
    )


def _is_explicit_exclusion(command: str) -> bool:
    classified = _support_helper_command(command)
    return isinstance(classified, Mapping) and classified.get("mode") == "read"


def _python_review_worktree_command(command: str) -> dict[str, Any] | None:
    tokens = _simple_command_tokens(command)
    if tokens is None:
        return None
    tokens = list(tokens)
    executable = tokens.pop(0).replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if executable not in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
        return None
    if executable in {"py", "py.exe"} and tokens and tokens[0] == "-3":
        tokens.pop(0)
    if len(tokens) < 2:
        return None
    script = tokens.pop(0).replace("\\", "/").casefold()
    if script != "scripts/agent/review_worktree.py":
        return None
    subcommand = tokens.pop(0).casefold()
    if subcommand in {"status", "list"}:
        return (
            {
                "mode": "read",
                "helper_paths": ("scripts/agent/review_worktree.py",),
            }
            if not tokens
            else None
        )
    if subcommand != "create":
        return None
    value_flags = {"--case-id", "--ref", "--path", "--metadata-root"}
    switch_flags = {"--fetch", "--dry-run", "--allow-dirty-parent"}
    values: dict[str, str] = {}
    switches: set[str] = set()
    while tokens:
        flag = tokens.pop(0)
        if flag in switch_flags:
            if flag in switches:
                return None
            switches.add(flag)
            continue
        if flag not in value_flags or flag in values or not tokens:
            return None
        value = tokens.pop(0)
        if not value or value.startswith("--") or len(value) > 4096:
            return None
        values[flag] = value
    if set(values) < {"--case-id", "--ref"}:
        return None
    try:
        case_id = case_state.canonical_case_id(values["--case-id"])
        case_state.require_sha(values["--ref"], "review-worktree ref")
    except case_state.CaseStateError:
        return None
    read_only = "--dry-run" in switches and "--fetch" not in switches
    return {
        "mode": "read" if read_only else "support_mutation",
        "case_id": case_id,
        "support_action": "repository_review_worktree_create",
        "helper_paths": ("scripts/agent/review_worktree.py",),
    }


def _package_script_command(command: str) -> tuple[str, list[str]] | None:
    tokens = _simple_command_tokens(command)
    if tokens is None:
        return None
    values = list(tokens)
    executable = values.pop(0).replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if executable in {"corepack", "corepack.exe"}:
        if len(values) < 3 or values.pop(0).casefold() not in {"pnpm", "pnpm.exe"}:
            return None
        if values.pop(0).casefold() != "run":
            return None
    elif executable in {"pnpm", "pnpm.exe"}:
        if not values or values.pop(0).casefold() != "run":
            return None
    else:
        return None
    if not values:
        return None
    script = values.pop(0).casefold()
    if values and values[0] == "--":
        values.pop(0)
    return script, values


def _codex_review_worktree_command(command: str) -> dict[str, Any] | None:
    parsed = _package_script_command(command)
    if parsed is None or parsed[0] != "agent:codex-review-worktree":
        return None
    tokens = parsed[1]
    value_flags = {
        "--case-id",
        "--actor-thread-id",
        "--request-id",
        "--expected-revision",
        "--product-event-id",
        "--path",
        "--base",
    }
    switch_flags = {"--print-only", "--help", "-h"}
    values: dict[str, str] = {}
    switches: set[str] = set()
    while tokens:
        flag = tokens.pop(0)
        if flag in switch_flags:
            if flag in switches:
                return None
            switches.add(flag)
            continue
        if flag not in value_flags or flag in values or not tokens:
            return None
        value = tokens.pop(0)
        if not value or value.startswith("--") or len(value) > 4096:
            return None
        values[flag] = value
    if switches & {"--help", "-h"}:
        if values or switches - {"--help", "-h"}:
            return None
        return {
            "mode": "read",
            "helper_paths": (
                "package.json",
                "scripts/agent/codex-review-worktree.mjs",
            ),
        }
    if "--case-id" not in values:
        return None
    try:
        case_id = case_state.canonical_case_id(values["--case-id"])
        for flag in ("--request-id", "--product-event-id"):
            if flag in values:
                case_state.require_request_id(values[flag])
        if "--actor-thread-id" in values:
            case_state.require_native_uuid7(
                values["--actor-thread-id"], "review-worktree actor thread"
            )
        if "--expected-revision" in values and int(values["--expected-revision"]) < 1:
            return None
    except (ValueError, case_state.CaseStateError):
        return None
    return {
        "mode": "read" if "--print-only" in switches else "support_mutation",
        "case_id": case_id,
        "support_action": "repository_review_worktree_create",
        "helper_paths": (
            "package.json",
            "scripts/agent/codex-review-worktree.mjs",
        ),
    }


def _pr_body_command(command: str) -> dict[str, Any] | None:
    tokens = _simple_command_tokens(command)
    if tokens is None:
        return None
    values = list(tokens)
    helper_paths: tuple[str, ...]
    executable = values.pop(0).replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if executable in {"node", "node.exe"}:
        if not values:
            return None
        script = values.pop(0).replace("\\", "/").casefold()
        if script != "scripts/agent/pr-body.mjs":
            return None
        helper_paths = ("scripts/agent/pr-body.mjs",)
    else:
        parsed = _package_script_command(command)
        if parsed is None or parsed[0] != "agent:pr-body":
            return None
        values = parsed[1]
        helper_paths = ("package.json", "scripts/agent/pr-body.mjs")
    value_flags = {
        "--risk",
        "--case-id",
        "--case-action",
        "--decision-reason",
        "--candidate-head",
        "--current-pr-head",
        "--base-head",
        "--reviewed-head",
        "--review-source",
        "--out",
    }
    switch_flags = {"--codex-reviewed", "--validate", "--help", "-h"}
    parsed_values: dict[str, str] = {}
    switches: set[str] = set()
    while values:
        flag = values.pop(0)
        if flag in switch_flags:
            if flag in switches:
                return None
            switches.add(flag)
            continue
        if flag not in value_flags or flag in parsed_values or not values:
            return None
        value = values.pop(0)
        if not value or value.startswith("--") or len(value) > 4096:
            return None
        parsed_values[flag] = value
    if parsed_values.get("--risk") not in {None, "routine", "material", "high"}:
        return None
    if "--case-id" in parsed_values:
        try:
            case_state.canonical_case_id(parsed_values["--case-id"])
        except case_state.CaseStateError:
            return None
    if switches & {"--help", "-h"} and (
        parsed_values or switches - {"--help", "-h"}
    ):
        return None
    return {
        "mode": (
            "support_mutation"
            if "--out" in parsed_values
            else "read"
        ),
        "case_id": parsed_values.get("--case-id"),
        "support_action": "repository_pr_body_write",
        "helper_paths": helper_paths,
    }


def _support_helper_command(command: str) -> dict[str, Any] | None:
    for classifier in (
        _python_review_worktree_command,
        _codex_review_worktree_command,
        _pr_body_command,
    ):
        result = classifier(command)
        if result is not None:
            return result
    return None


def _git_context(cwd: str) -> tuple[str | None, str | None]:
    try:
        origin = _git(cwd, "config", "--get", "remote.origin.url")
        repository = case_state.normalize_repo_url(origin) if origin else None
    except (HookError, case_state.CaseStateError):
        repository = None
    try:
        branch = _git(cwd, "symbolic-ref", "--quiet", "--short", "HEAD") or None
    except HookError:
        branch = None
    return repository, branch


def _exact_git_root(cwd: str) -> str | None:
    try:
        reported = _git(cwd, "rev-parse", "--show-toplevel")
        return case_state.normalize_binding("worktree", reported)
    except (HookError, case_state.CaseStateError):
        return None


def _helper_path(command: str) -> str | None:
    normalized = _single_command_text(command)
    if normalized is None:
        return None
    direct = (
        (re.compile(r"^(?:python(?:3)?|py(?: -3)?)(?:\.exe)? scripts/agent/session_continuity\.py\b"), "scripts/agent/session_continuity.py"),
        (re.compile(r"^node scripts/agent/handoff-create\.mjs\b"), "scripts/agent/handoff-create.mjs"),
        (re.compile(r"^node scripts/checks/automation-helpers-check\.mjs\b"), "scripts/checks/automation-helpers-check.mjs"),
        (re.compile(r"^node scripts/checks/docs-check\.mjs\b"), "scripts/checks/docs-check.mjs"),
        (re.compile(r"^node scripts/checks/pr-body-check\.mjs\b"), "scripts/checks/pr-body-check.mjs"),
        (re.compile(r"^node scripts/checks/session-continuity-check\.mjs\b"), "scripts/checks/session-continuity-check.mjs"),
        (re.compile(r"^node --test scripts/agent/tests/case-state\.test\.mjs\b"), "scripts/agent/tests/case-state.test.mjs"),
        (re.compile(r"^corepack pnpm run (?:agent:handoff-create|agent:state-check|agent:case-state:test|pr:check)\b"), "package.json"),
    )
    matches = {path for pattern, path in direct if pattern.match(normalized)}
    return next(iter(matches)) if len(matches) == 1 else None


def _require_trusted_helper(cwd: str, command: str) -> None:
    path = _helper_path(command)
    if path is None:
        raise HookError("recognized support command does not resolve to one exact helper")
    _require_committed_helpers(cwd, (path,))


def _require_committed_helpers(cwd: str, paths: tuple[str, ...]) -> None:
    if not paths:
        raise HookError("recognized support command has no helper identity")
    root = _exact_git_root(cwd)
    normalized_cwd = case_state.normalize_binding("worktree", cwd)
    if root != normalized_cwd:
        raise HookError("trusted support helper must execute from the exact Git root")
    for path in paths:
        committed = _git(cwd, "rev-parse", f"HEAD:{path}").casefold()
        working = _git(cwd, "hash-object", "--", path).casefold()
        if not committed or committed != working:
            raise HookError(
                f"recognized support helper differs from its committed HEAD blob: {path}"
            )


def _require_self_reporting_handoff_helper(cwd: str, command: str) -> None:
    if not _is_self_reporting_handoff(command):
        raise HookError("handoff command is not the exact self-reporting invocation")
    _require_trusted_helper(cwd, command)
    source = _git(cwd, "show", "HEAD:scripts/agent/session_continuity.py")
    markers = (
        "record_anti_loop_event",
        "SUPPORT_MUTATION",
        "repository_handoff_write",
    )
    if any(marker not in source for marker in markers):
        raise HookError(
            "trusted continuity helper does not contain the committed self-reporting contract"
        )


def resolve_case(
    store: case_state.CaseStore, thread_id: str, cwd: str | None
) -> tuple[Mapping[str, Any] | None, str]:
    thread_matches = [
        case for case in store.list_cases() if _case_matches_thread(case, thread_id)
    ]
    if len(thread_matches) > 1:
        raise HookError("CODEX_THREAD_ID resolves to multiple canonical cases")
    if thread_matches:
        candidate = thread_matches[0]
        if not cwd:
            return candidate, "thread"
        normalized_cwd = case_state.normalize_binding("worktree", cwd)
        bindings = candidate.get("bindings")
        bound_worktrees = bindings.get("worktree", []) if isinstance(bindings, Mapping) else []
        if normalized_cwd in bound_worktrees:
            return candidate, "thread"
        exact_root = _exact_git_root(cwd)
        repository, _ = _git_context(cwd)
        bound_repositories = bindings.get("repo_url", []) if isinstance(bindings, Mapping) else []
        if exact_root == normalized_cwd and repository is not None and repository not in bound_repositories:
            return None, "unrelated"
        raise HookError(
            "trusted actor thread is paired with an unbound or ambiguous cwd/repository"
        )
    if not cwd:
        return None, "none"
    try:
        worktree = case_state.normalize_binding("worktree", cwd)
    except case_state.CaseStateError:
        return None, "none"
    repository, branch = _git_context(worktree)
    matches: list[Mapping[str, Any]] = []
    for candidate in store.list_cases():
        bindings = candidate.get("bindings")
        if not isinstance(bindings, Mapping) or worktree not in bindings.get("worktree", []):
            continue
        if repository is not None and repository not in bindings.get("repo_url", []):
            continue
        branch_bindings = bindings.get("branch", [])
        if repository is not None and branch is not None and branch_bindings:
            if {"repository": repository, "value": branch} not in branch_bindings:
                continue
        matches.append(candidate)
    if len(matches) > 1:
        raise HookError("hook cwd origin/worktree/branch resolves to multiple canonical cases")
    return (matches[0], "worktree") if matches else (None, "none")


def _event_kind(
    phase: str,
    command: str,
    response: Any,
    cwd: str | None,
    *,
    case: Mapping[str, Any],
    store: case_state.CaseStore,
) -> tuple[str, str | None] | None:
    if phase == "PreToolUse":
        helper = _support_helper_command(command)
        if isinstance(helper, Mapping) and helper.get("mode") == "support_mutation":
            if cwd is None:
                raise HookError("hook-owned support mutation requires exact hook cwd")
            helper_case_id = helper.get("case_id")
            if helper_case_id is not None and helper_case_id != case.get("case_id"):
                raise HookError(
                    "hook-owned support mutation case differs from the exact bound case"
                )
            raw_paths = helper.get("helper_paths")
            if not isinstance(raw_paths, tuple) or not all(
                isinstance(path, str) for path in raw_paths
            ):
                raise HookError("hook-owned support helper identity is invalid")
            _require_committed_helpers(cwd, raw_paths)
            return "SUPPORT_MUTATION", str(helper["support_action"])
    if phase == "PreToolUse":
        details = _support_chain_command(command)
        if details is not None:
            if cwd is None:
                raise HookError("support-chain reporter requires exact hook cwd")
            action = _verify_support_chain_parent(
                case,
                details,
                cwd=cwd,
                state_root=store.state_root,
            )
            return "SUPPORT_CHAIN_PROPOSED", action
    if phase == "PostToolUse" and _tool_failed(response):
        if _is_support_check(command):
            if cwd is None:
                raise HookError("support failure requires exact hook cwd")
            _require_trusted_helper(cwd, command)
            return "SUPPORT_FAILURE", None
        if _control_patch_command(command) is not None:
            return "SUPPORT_FAILURE", None
    if phase == "PostToolUse" and not _tool_failed(response):
        if any(GIT_COMMIT_PATTERN.match(_command_text(segment)) for segment in _command_segments(command)):
            return "PRODUCT_HEAD_ADVANCED", None
    return None


def _derived_event(
    *,
    case: Mapping[str, Any],
    thread_id: str,
    cwd: str,
    event_type: str,
    support_action: str | None,
    tool_name: str,
    command: str,
    response: Any,
    phase: str,
    turn_id: str,
    tool_use_id: str,
) -> dict[str, Any]:
    runtime = case.get("runtime")
    actors = runtime.get("actors") if isinstance(runtime, Mapping) else None
    actor = actors.get(thread_id) if isinstance(actors, Mapping) else None
    if not isinstance(actor, Mapping):
        raise HookError("event recording requires the exact controller-bound CODEX_THREAD_ID")
    bindings = case.get("bindings")
    worktree = case_state.normalize_binding("worktree", cwd)
    if not isinstance(bindings, Mapping) or worktree not in bindings.get("worktree", []):
        raise HookError("hook cwd is not an exact worktree binding for this case")
    repository, _ = _git_context(worktree)
    repositories = bindings.get("repo_url", [])
    if repository is None:
        if not isinstance(repositories, list) or len(repositories) != 1:
            raise HookError("hook cannot derive one exact bound repository")
        repository = str(repositories[0])
    if repository not in repositories:
        raise HookError("hook Git origin is not bound to the exact case")
    actual_head = _git(worktree, "rev-parse", "HEAD").casefold()
    if not SHA_PATTERN.fullmatch(actual_head):
        raise HookError("hook cannot derive one exact Git product head")
    fingerprint = (
        _failure_fingerprint(tool_name, command, response)
        if event_type == "SUPPORT_FAILURE"
        else None
    )
    request_id = str(
        uuid.uuid5(
            HOOK_EVENT_NAMESPACE,
            json.dumps(
                {
                    "event_type": event_type,
                    "phase": phase,
                    "session_id": thread_id,
                    "tool_use_id": tool_use_id,
                    "turn_id": turn_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    expected_revision = int(case["revision"])
    events = case.get("events")
    prior = events.get(request_id) if isinstance(events, Mapping) else None
    prior_result = prior.get("result") if isinstance(prior, Mapping) else None
    if (
        isinstance(prior, Mapping)
        and prior.get("operation") == "record_anti_loop_event"
        and isinstance(prior_result, Mapping)
        and isinstance(prior_result.get("expected_revision"), int)
    ):
        expected_revision = int(prior_result["expected_revision"])
    return {
        "protocol_version": HOOK_PROTOCOL_VERSION,
        "schema_version": 1,
        "case_id": str(case["case_id"]),
        "event_type": event_type,
        "actor_thread_id": thread_id,
        "actor_role": str(actor.get("role", "")),
        "repository": repository,
        "worktree": worktree,
        "product_head": actual_head,
        "support_action": support_action,
        "failure_fingerprint": fingerprint,
        "request_id": request_id,
        "expected_revision": expected_revision,
    }


def _hook_tool_identity(data: Mapping[str, Any], phase: str) -> tuple[str, str]:
    turn_id = str(data.get("turn_id") or data.get("turnId") or "").strip()
    tool_use_id = str(
        data.get("tool_use_id") or data.get("toolUseId") or ""
    ).strip()
    if (
        not turn_id
        or not tool_use_id
        or len(turn_id) > 256
        or len(tool_use_id) > 256
    ):
        raise HookError(
            "anti-loop event recording requires exact native turn_id and tool invocation id"
        )
    return turn_id, tool_use_id


def _tool_failed(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("isError") is True or value.get("is_error") is True:
            return True
        for field in ("exit_code", "returncode"):
            code = value.get(field)
            if isinstance(code, int) and not isinstance(code, bool) and code != 0:
                return True
        status = str(value.get("status") or "").casefold()
        if status in {"error", "failed", "failure"}:
            return True
        return any(_tool_failed(item) for item in value.values())
    if isinstance(value, list):
        return any(_tool_failed(item) for item in value)
    return False


def _stable_failure_text(value: str) -> str:
    normalized = value.replace("\\", "/").casefold()
    normalized = re.sub(
        r"\b\d{4}-\d{2}-\d{2}[t ][0-9:.+-]+z?\b", "<timestamp>", normalized
    )
    normalized = re.sub(r"\b\d+(?:\.\d+)?\s*(?:ms|milliseconds?|s|seconds?)\b", "<duration>", normalized)
    normalized = re.sub(r"\b[a-z]:/(?:[^\s:'\"]+/)+", "<path>/", normalized)
    normalized = re.sub(r"/(?:tmp|var/tmp|private/tmp)/(?:[^\s:'\"]+/)*", "<tmp>/", normalized)
    lines = {
        re.sub(r"\s+", " ", line).strip()
        for line in normalized.splitlines()
        if re.sub(r"\s+", " ", line).strip()
    }
    return "\n".join(sorted(lines))


def _stable_failure_identity(value: Any) -> Any:
    volatile = {
        "cwd", "duration", "duration_ms", "elapsed", "elapsed_ms", "ended_at",
        "path", "started_at", "timestamp", "tool_use_id",
    }
    diagnostic = {"error", "error_class", "exception", "message", "reason", "stderr", "stdout"}
    status = {"exit_code", "isError", "is_error", "returncode", "status", "type"}
    if isinstance(value, Mapping):
        stable: dict[str, Any] = {}
        for key in sorted(value, key=str):
            name = str(key)
            folded = name.casefold()
            if folded in volatile:
                continue
            if folded in diagnostic or folded in status:
                stable[name] = _stable_failure_identity(value[key])
            elif isinstance(value[key], (Mapping, list)):
                nested = _stable_failure_identity(value[key])
                if nested not in ({}, [], None, ""):
                    stable[name] = nested
        return stable
    if isinstance(value, list):
        stable_items = [_stable_failure_identity(item) for item in value]
        return sorted(stable_items, key=lambda item: _canonical_json(item))
    if isinstance(value, str):
        return _stable_failure_text(value)
    if isinstance(value, (bool, int)) or value is None:
        return value
    return _stable_failure_text(str(value))


def _failure_fingerprint(tool_name: str, command: str, response: Any) -> str:
    evidence = {
        "tool_name": tool_name.casefold(),
        "command": _command_text(command),
        "failure": _stable_failure_identity(response),
    }
    return "sha256:" + hashlib.sha256(_canonical_json(evidence)).hexdigest()


def _git(worktree: str, *arguments: str) -> str:
    executable = case_state.resolved_executable("git.exe", "git")
    completed = subprocess.run(
        [executable, "-C", worktree, *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env=case_state.safe_subprocess_environment(
            executable,
            extra={"GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0"},
        ),
        timeout=20,
    )
    if completed.returncode != 0:
        raise HookError(f"Git verification failed: {(completed.stderr or completed.stdout).strip()}")
    return completed.stdout.strip()


def _verify_product_head(case: Mapping[str, Any], envelope: Mapping[str, Any]) -> None:
    worktree = str(envelope["worktree"])
    actual = _git(worktree, "rev-parse", "HEAD").casefold()
    if actual != envelope["product_head"]:
        raise HookError("declared product head differs from verified Git HEAD")
    latch = case.get("anti_loop_latch")
    product_heads = latch.get("product_heads") if isinstance(latch, Mapping) else None
    previous = product_heads.get(envelope["repository"]) if isinstance(product_heads, Mapping) else None
    if previous == actual:
        raise HookError("PRODUCT_HEAD_ADVANCED cannot reuse the unchanged verified head")
    base = previous or _git(worktree, "rev-parse", f"{actual}^").casefold()
    changed = [line for line in _git(worktree, "diff", "--name-only", base, actual).splitlines() if line]
    if not changed or not any(not _is_control_path(path, worktree) for path in changed):
        raise HookError("PRODUCT_HEAD_ADVANCED requires a verified product-path diff")


def _trusted_actor_identity(data: Mapping[str, Any]) -> tuple[str, str | None]:
    session_id = str(data.get("session_id") or "").strip().casefold()
    if "subagent" in data:
        raise HookError("nested subagent identity is not part of the native hook schema")
    has_agent_id = "agent_id" in data
    has_agent_type = "agent_type" in data
    if not has_agent_id and not has_agent_type:
        return session_id, None
    if not has_agent_id or not has_agent_type:
        raise HookError("native child identity requires both top-level agent_id and agent_type")
    agent_id = str(data.get("agent_id") or "").strip().casefold()
    agent_type = str(data.get("agent_type") or "").strip().casefold()
    if not agent_id or not agent_type:
        raise HookError("native top-level child identity is incomplete")
    return agent_id, agent_type


def evaluate(
    data: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    store: case_state.CaseStore | None = None,
) -> dict[str, Any]:
    environment = os.environ if environ is None else environ
    phase = str(data.get("hook_event_name") or data.get("hookEventName") or "")
    if phase not in HOOK_PHASES:
        return _result(
            phase=phase or "UNKNOWN",
            decision="DENY",
            reason_code="HOOK_PHASE_INVALID",
            reason="anti-loop runtime accepts only PreToolUse or PostToolUse",
        )
    tool_name = str(data.get("tool_name") or data.get("toolName") or "")
    raw_input = data.get("tool_input") or data.get("toolInput") or {}
    tool_input = raw_input if isinstance(raw_input, Mapping) else {}
    cwd_value = data.get("cwd")
    cwd = str(cwd_value).strip() if isinstance(cwd_value, str) and cwd_value.strip() else None
    mutating = _is_mutating_tool(tool_name, tool_input)
    try:
        surface = _control_surface(tool_name, tool_input, cwd)
    except HookError as exc:
        return _result(
            phase=phase,
            decision="DENY" if mutating else "ALLOW",
            reason_code="TOOL_TARGET_INVALID",
            reason=str(exc),
        )
    try:
        thread_id, trusted_actor_role = _trusted_actor_identity(data)
    except HookError as exc:
        return _result(
            phase=phase,
            decision="DENY" if mutating else "ALLOW",
            reason_code="HOOK_ACTOR_IDENTITY_INVALID",
            reason=str(exc),
        )
    environment_thread = str(environment.get("CODEX_THREAD_ID") or "").strip().casefold()
    if trusted_actor_role is None and environment_thread and environment_thread != thread_id:
        return _result(
            phase=phase,
            decision="DENY" if mutating else "ALLOW",
            reason_code="HOOK_THREAD_IDENTITY_CONTRADICTION",
            reason="CODEX_THREAD_ID differs from the trusted hook session_id",
        )
    if not thread_id:
        if mutating and surface is not None:
            return _result(
                phase=phase,
                decision="DENY",
                reason_code="CASE_THREAD_REQUIRED",
                reason="known control/support mutation requires trusted hook session_id",
            )
        return _result(
            phase=phase,
            decision="ALLOW",
            reason_code="UNBOUND_PRODUCT_SCOPE",
            reason="unbound threads may not mutate known control surfaces; this input is not classified as control",
        )
    if store is None:
        store = case_state.CaseStore()
    command = _shell_command(tool_input)
    response = data.get("tool_response") or data.get("toolResponse")
    bootstrap = _bootstrap_command(command, store) if phase == "PreToolUse" else None
    if bootstrap is not None:
        try:
            bootstrap_case = store.get_case(str(bootstrap["case_id"]))
        except case_state.ValidationError:
            bootstrap_case = None
        latch = (
            bootstrap_case.get("anti_loop_latch")
            if isinstance(bootstrap_case, Mapping)
            else None
        )
        if isinstance(latch, Mapping) and latch.get("status") == "LATCHED":
            return _result(
                phase=phase,
                decision="DENY",
                reason_code="ANTI_LOOP_LATCH_ACTIVE",
                reason="the exact bootstrap target case has an active mandatory anti-loop latch",
                case_id=str(bootstrap["case_id"]),
                revision=int(bootstrap_case["revision"]),
            )
        return _result(
            phase=phase,
            decision="ALLOW",
            reason_code="CASE_BOOTSTRAP_ALLOWED",
            reason="the fully parsed and pinned canonical register or bind command is allowed",
            case_id=(
                str(bootstrap_case["case_id"])
                if isinstance(bootstrap_case, Mapping)
                else None
            ),
            revision=(
                int(bootstrap_case["revision"])
                if isinstance(bootstrap_case, Mapping)
                else None
            ),
        )
    try:
        case, resolution = resolve_case(store, thread_id, cwd)
    except HookError as exc:
        return _result(
            phase=phase,
            decision="DENY" if mutating else "ALLOW",
            reason_code="CASE_RESOLUTION_AMBIGUOUS",
            reason=str(exc),
        )
    if case is None:
        if mutating and surface is not None:
            return _result(
                phase=phase,
                decision="DENY",
                reason_code="CASE_BINDING_REQUIRED",
                reason="known control/support mutation requires one exact canonical case binding",
            )
        return _result(
            phase=phase,
            decision="ALLOW",
            reason_code="UNRELATED_OR_UNBOUND_PRODUCT_SCOPE",
            reason="no exact bound case exists and this input is not a known control/support mutation",
        )
    case_id = str(case["case_id"])
    revision = int(case["revision"])
    runtime = case.get("runtime")
    actors = runtime.get("actors") if isinstance(runtime, Mapping) else None
    bound_actor = actors.get(thread_id) if isinstance(actors, Mapping) else None
    if (
        trusted_actor_role is not None
        and (
            not isinstance(bound_actor, Mapping)
            or bound_actor.get("role") != trusted_actor_role
        )
    ):
        return _result(
            phase=phase,
            decision="DENY" if mutating else "ALLOW",
            reason_code="HOOK_SUBAGENT_ROLE_CONTRADICTION",
            reason="trusted subagent agent_type differs from the controller-bound runtime actor role",
            case_id=case_id,
            revision=revision,
        )
    latch = case.get("anti_loop_latch")
    disposition = _disposition_command(command) if phase == "PreToolUse" else None
    if (
        phase == "PreToolUse"
        and mutating
        and isinstance(latch, Mapping)
        and latch.get("status") == "LATCHED"
    ):
        if (
            isinstance(disposition, Mapping)
            and disposition.get("case_id") == case_id
            and disposition.get("expected_revision") == revision
            and Path(store.state_root).resolve(strict=False)
            == case_state.default_state_root().resolve(strict=False)
        ):
            return _result(
                phase=phase,
                decision="ALLOW",
                reason_code="ANTI_LOOP_HUMAN_DISPOSITION_ALLOWED",
                reason=(
                    "the exact canonical-state disposition command may run; "
                    "the engine's native human evidence verifier remains final authority"
                ),
                case_id=case_id,
                revision=revision,
            )
        return _result(
            phase=phase,
            decision="DENY",
            reason_code="ANTI_LOOP_LATCH_ACTIVE",
            reason="the exact bound case has an active mandatory anti-loop latch",
            case_id=case_id,
            revision=revision,
        )
    control_patch = (
        _control_patch_command(command)
        if phase == "PreToolUse" and _control_patch_carrier(command)
        else None
    )
    if phase == "PreToolUse" and control_patch is not None:
        try:
            if (
                control_patch["case_id"] != case_id
                or control_patch["expected_revision"] != revision
                or control_patch["actor_thread_id"] != thread_id
                or not isinstance(bound_actor, Mapping)
                or control_patch["actor_role"] != bound_actor.get("role")
                or Path(control_patch["state_root"]).resolve(strict=False)
                != store.state_root.resolve(strict=False)
                or cwd is None
                or control_patch["repository_root"]
                != case_state.normalize_binding("worktree", cwd)
                or control_patch["repository"] not in case["bindings"]["repo_url"]
                or control_patch["repository_root"] not in case["bindings"]["worktree"]
                or control_patch["product_head"]
                != _git(control_patch["repository_root"], "rev-parse", "HEAD").casefold()
            ):
                raise HookError(
                    "guarded control-patch arguments are not pinned to the exact case, actor, store, worktree, and head"
                )
        except (OSError, case_state.CaseStateError, HookError) as exc:
            return _result(
                phase=phase,
                decision="DENY",
                reason_code="UNTRUSTED_CONTROL_BROKER",
                reason=str(exc),
                case_id=case_id,
                revision=revision,
            )
        return _result(
            phase=phase,
            decision="ALLOW",
            reason_code="GUARDED_CONTROL_BROKER_ALLOWED",
            reason="the exact broker must record and authorize the support mutation before applying the patch",
            case_id=case_id,
            revision=revision,
        )
    if phase == "PreToolUse" and _is_self_reporting_handoff(command):
        try:
            if resolution != "thread":
                raise HookError(
                    "self-reporting handoff requires the exact native thread binding"
                )
            if not isinstance(bound_actor, Mapping) or bound_actor.get("role") != "parent":
                raise HookError(
                    "self-reporting handoff requires the controller-bound parent actor"
                )
            if cwd is None:
                raise HookError("self-reporting handoff requires exact hook cwd")
            _require_self_reporting_handoff_helper(cwd, command)
        except (HookError, case_state.CaseStateError, OSError, subprocess.SubprocessError) as exc:
            return _result(
                phase=phase,
                decision="DENY",
                reason_code="UNTRUSTED_SUPPORT_HELPER",
                reason=str(exc),
                case_id=case_id,
                revision=revision,
            )
        return _result(
            phase=phase,
            decision="ALLOW",
            reason_code="SELF_REPORTING_SUPPORT_HELPER_ALLOWED",
            reason="the exact committed continuity helper owns one support event before its write",
            case_id=case_id,
            revision=revision,
        )
    try:
        kind = _event_kind(
            phase,
            command,
            response,
            cwd,
            case=case,
            store=store,
        )
    except HookError as exc:
        return _result(
            phase=phase,
            decision="DENY" if mutating or surface is not None else "ALLOW",
            reason_code="UNTRUSTED_SUPPORT_HELPER",
            reason=str(exc),
            case_id=case_id,
            revision=revision,
        )
    if phase == "PreToolUse" and mutating and surface is not None and kind is None:
        return _result(
            phase=phase,
            decision="DENY",
            reason_code=(
                "CONTROL_BROKER_REQUIRED"
                if tool_name.casefold() in {"apply_patch", "edit", "write", "write_file"}
                else "GUARDED_COMMAND_REQUIRED"
            ),
            reason=f"known control/support mutation {surface} must use an exact trusted helper or guarded broker",
            case_id=case_id,
            revision=revision,
        )
    if kind is None:
        return _result(
            phase=phase,
            decision="ALLOW",
            reason_code="NO_ANTI_LOOP_EVENT",
            reason="no independently classified anti-loop event applies to this tool phase",
            case_id=case_id,
            revision=revision,
        )
    try:
        if resolution != "thread":
            raise HookError(
                "event recording requires trusted session_id runtime binding; cwd fallback is read/deny scope only"
            )
        if cwd is None:
            raise HookError("event recording requires trusted hook cwd")
        turn_id, tool_use_id = _hook_tool_identity(data, phase)
        event_type, support_action = kind
        normalized = _derived_event(
            case=case,
            thread_id=thread_id,
            cwd=cwd,
            event_type=event_type,
            support_action=support_action,
            tool_name=tool_name,
            command=command,
            response=response,
            phase=phase,
            turn_id=turn_id,
            tool_use_id=tool_use_id,
        )
        event = store.record_anti_loop_event(
            case_id,
            event_type=event_type,
            actor_thread_id=str(normalized["actor_thread_id"]),
            actor_role=str(normalized["actor_role"]),
            repository=str(normalized["repository"]),
            worktree=str(normalized["worktree"]),
            product_head=str(normalized["product_head"]),
            support_action=normalized.get("support_action"),
            failure_fingerprint=normalized.get("failure_fingerprint"),
            request_id=str(normalized["request_id"]),
            expected_revision=int(normalized["expected_revision"]),
        )
    except (HookError, case_state.CaseStateError, OSError, subprocess.SubprocessError) as exc:
        return _result(
            phase=phase,
            decision="DENY",
            reason_code="ANTI_LOOP_EVENT_REJECTED",
            reason=str(exc),
            case_id=case_id,
            revision=revision,
        )
    event_latch = event.get("anti_loop_latch")
    latched = isinstance(event_latch, Mapping) and event_latch.get("status") == "LATCHED"
    return _result(
        phase=phase,
        decision="DENY" if latched else "ALLOW",
        reason_code="ANTI_LOOP_LATCH_ACTIVE" if latched else "ANTI_LOOP_EVENT_RECORDED",
        reason=(
            "the recorded event activated the mandatory anti-loop latch"
            if latched
            else "the independently derived anti-loop event was recorded"
        ),
        case_id=case_id,
        revision=int(event["revision"]),
        event=event,
    )


def _bounded_regular_file(path: Path, *, maximum: int) -> bytes:
    resolved = path.expanduser().resolve(strict=True)
    if (
        not resolved.is_file()
        or resolved.is_symlink()
        or case_state.path_contains_link_or_reparse(resolved, stop=resolved.parent)
    ):
        raise HookError("broker input must be one regular direct file")
    if resolved.stat().st_size > maximum:
        raise HookError("broker input exceeds the size limit")
    return resolved.read_bytes()


def _unified_diff_paths(raw: bytes, *, cwd: str) -> list[str]:
    try:
        text_value = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise HookError("control patch must be UTF-8") from exc
    paths: set[str] = set()
    for line in text_value.splitlines():
        match = re.match(r"^diff --git a/(.+) b/(.+)$", line)
        if match:
            paths.update(match.groups())
            continue
        match = re.match(r"^(?:--- a/|\+\+\+ b/)(.+)$", line)
        if match and match.group(1) != "/dev/null":
            paths.add(match.group(1))
    normalized = sorted(
        {
            relative
            for path in paths
            if (relative := _repository_relative_path(path, None)) is not None
        }
    )
    if not normalized or any(not _is_control_path(path, cwd) for path in normalized):
        raise HookError("guarded control patch may contain only classified control-surface paths")
    return normalized


def apply_control_patch(arguments: argparse.Namespace) -> dict[str, Any]:
    root = Path(arguments.repository_root).expanduser().resolve(strict=True)
    normalized_root = case_state.normalize_binding("worktree", str(root))
    if _exact_git_root(normalized_root) != normalized_root:
        raise HookError("repository-root must be the exact Git root")
    patch_path = Path(arguments.patch_file)
    raw = _bounded_regular_file(patch_path, maximum=2 * 1024 * 1024)
    expected_sha256 = str(arguments.sha256).strip().casefold()
    observed_sha256 = hashlib.sha256(raw).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or not secrets.compare_digest(
        observed_sha256, expected_sha256
    ):
        raise HookError("control patch SHA-256 differs from the exact expected digest")
    paths = _unified_diff_paths(raw, cwd=normalized_root)
    store = case_state.CaseStore(Path(arguments.state_root))
    executable = case_state.resolved_executable("git.exe", "git")
    environment = case_state.safe_subprocess_environment(
        executable, extra={"GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0"}
    )
    base = [
        executable,
        "--no-replace-objects",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"safe.directory={root}",
        "-C",
        str(root),
        "apply",
    ]

    def apply_after_record(event_result: Mapping[str, Any]) -> Mapping[str, Any]:
        latch = event_result.get("anti_loop_latch")
        if (
            event_result.get("triggered") is True
            or not isinstance(latch, Mapping)
            or latch.get("status") != "CLEAR"
        ):
            raise HookError("ANTI_LOOP_LATCH_ACTIVE: control patch was not applied")
        for check in (True, False):
            command = [*base, "--check"] if check else list(base)
            completed = subprocess.run(
                command,
                input=raw,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=environment,
                timeout=30,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).decode(
                    "utf-8", errors="replace"
                ).strip()
                raise HookError("guarded git apply failed: " + detail)
        return {"atomic_post_record_status": "APPLIED"}

    event = store.record_anti_loop_event(
        arguments.case_id,
        event_type="SUPPORT_MUTATION",
        actor_thread_id=arguments.actor_thread_id,
        actor_role=arguments.actor_role,
        repository=arguments.repository,
        worktree=normalized_root,
        product_head=arguments.product_head,
        support_action=arguments.support_action,
        failure_fingerprint=None,
        request_id=arguments.request_id,
        expected_revision=arguments.expected_revision,
        atomic_post_record=apply_after_record,
    )
    if event.get("atomic_post_record_status") != "APPLIED":
        raise HookError("guarded control patch did not complete its one-use atomic apply")
    return {
        "protocol_version": "ccos-guarded-control-patch-v1",
        "schema_version": 1,
        "status": "APPLIED",
        "case_id": arguments.case_id,
        "event_id": event["event_id"],
        "revision": event["revision"],
        "patch_sha256": observed_sha256,
        "paths": paths,
    }


def report_support_chain(arguments: argparse.Namespace) -> dict[str, Any]:
    root = Path(arguments.repository_root).expanduser().resolve(strict=True)
    normalized_root = case_state.normalize_binding("worktree", str(root))
    if _exact_git_root(normalized_root) != normalized_root:
        raise HookError("repository-root must be the exact Git root")
    store = case_state.CaseStore(Path(arguments.state_root))
    case = store.get_case(arguments.case_id)
    details = {
        "case_id": case_state.canonical_case_id(arguments.case_id),
        "actor_thread_id": case_state.normalize_binding(
            "thread", arguments.actor_thread_id
        ),
        "actor_role": str(arguments.actor_role).strip().casefold(),
        "repository": case_state.normalize_repo_url(arguments.repository),
        "worktree": normalized_root,
        "product_head": case_state.require_sha(
            arguments.product_head, "support-chain product head"
        ),
        "parent_event_id": case_state.require_request_id(
            arguments.parent_event_id
        ),
        "support_action": str(arguments.support_action).strip(),
        "request_id": case_state.require_request_id(arguments.request_id),
        "expected_revision": arguments.expected_revision,
        "state_root": str(store.state_root.resolve(strict=False)),
    }
    action = _verify_support_chain_parent(
        case,
        details,
        cwd=normalized_root,
        state_root=store.state_root,
    )
    event = store.record_anti_loop_event(
        details["case_id"],
        event_type="SUPPORT_CHAIN_PROPOSED",
        actor_thread_id=details["actor_thread_id"],
        actor_role=details["actor_role"],
        repository=details["repository"],
        worktree=details["worktree"],
        product_head=details["product_head"],
        support_action=action,
        failure_fingerprint=None,
        request_id=details["request_id"],
        expected_revision=details["expected_revision"],
    )
    latch = event.get("anti_loop_latch")
    if (
        event.get("triggered") is not True
        or not isinstance(latch, Mapping)
        or latch.get("status") != "LATCHED"
    ):
        raise HookError("support-chain report did not activate the mandatory latch")
    return {
        "protocol_version": "ccos-support-chain-report-v1",
        "schema_version": 1,
        "status": "LATCHED",
        "case_id": details["case_id"],
        "parent_event_id": details["parent_event_id"],
        "event_id": event["event_id"],
        "revision": event["revision"],
        "trigger_reason": event["trigger_reason"],
    }


def _control_patch_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply one anti-loop guarded control patch")
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--patch-file", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--actor-thread-id", required=True)
    parser.add_argument("--actor-role", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--product-head", required=True)
    parser.add_argument("--support-action", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--expected-revision", required=True, type=int)
    return parser


def _support_chain_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Latch one case when a support event proposes another support action"
    )
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--actor-thread-id", required=True)
    parser.add_argument("--actor-role", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--product-head", required=True)
    parser.add_argument("--parent-event-id", required=True)
    parser.add_argument("--support-action", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--expected-revision", required=True, type=int)
    return parser


def _require_canonical_broker_state_root(arguments: argparse.Namespace) -> None:
    submitted = Path(arguments.state_root)
    if not submitted.is_absolute():
        raise HookError("broker state-root must be the absolute canonical account store")
    if submitted.resolve(strict=False) != case_state.default_state_root().resolve(
        strict=False
    ):
        raise HookError("broker state-root differs from the canonical OS-account store")


def _native_hook_output(output: Mapping[str, Any]) -> dict[str, Any] | None:
    if output.get("ccos_decision") != "DENY":
        return None
    phase = output.get("phase")
    reason_code = str(output.get("reason_code") or "ANTI_LOOP_HOOK_FAILURE")
    reason = str(output.get("reason") or "anti-loop hook denied the operation")
    if phase == "PreToolUse":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"{reason_code}: {reason}",
            }
        }
    if phase == "PostToolUse":
        return {"decision": "block", "reason": f"{reason_code}: {reason}"}
    raise HookError("native hook output phase is unavailable or invalid")


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "support-chain-proposed":
        try:
            parsed = _support_chain_parser().parse_args(arguments[1:])
            _require_canonical_broker_state_root(parsed)
            result = report_support_chain(parsed)
        except Exception as exc:
            sys.stderr.write(
                f"ANTI LOOP SUPPORT CHAIN ERROR [{type(exc).__name__}]: {exc}\n"
            )
            return 2
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
        return 0
    if arguments and arguments[0] == "control-patch":
        try:
            parsed = _control_patch_parser().parse_args(arguments[1:])
            _require_canonical_broker_state_root(parsed)
            result = apply_control_patch(parsed)
        except Exception as exc:
            sys.stderr.write(f"ANTI LOOP CONTROL PATCH ERROR [{type(exc).__name__}]: {exc}\n")
            return 2
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="Read hook JSON from this file instead of stdin")
    args = parser.parse_args(arguments)
    try:
        raw = Path(args.input).read_text(encoding="utf-8") if args.input else sys.stdin.read()
        data = json.loads(raw)
        if not isinstance(data, Mapping):
            raise HookError("hook input must be one JSON object")
        output = evaluate(data)
        native = _native_hook_output(output)
    except Exception as exc:
        sys.stderr.write(
            f"ANTI_LOOP_HOOK_FAILURE: {type(exc).__name__}: {exc}\n"
        )
        return 2
    if native is not None:
        sys.stdout.write(json.dumps(native, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
