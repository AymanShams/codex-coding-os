#!/usr/bin/env python3
"""Canonical capability-control authority for Codex config.toml."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any

import tomllib

PROJECTION_SCHEMA = "capability-config-v2"
SOURCE_HASH_KEY = "config-capability-projection-v2"
PROJECTION_ROOTS = (
    "apps",
    "features",
    "hooks",
    "mcp_servers",
    "plugins",
    "shell_environment_policy",
    "skills",
)
NON_CAPABILITY_ROOTS = frozenset(
    {
        "approval_policy",
        "approvals_reviewer",
        "desktop",
        "marketplaces",
        "model",
        "model_reasoning_effort",
        "notify",
        "personality",
        "projects",
        "sandbox_mode",
        "sandbox_workspace_write",
        "windows",
    }
)
KNOWN_ROOTS = frozenset(PROJECTION_ROOTS) | NON_CAPABILITY_ROOTS
HASH_SCOPE = PROJECTION_SCHEMA
_OMIT = object()
_CAPABILITY_FEATURE_KEYS = frozenset(
    {
        "apps",
        "browser_use",
        "computer_use",
        "connectors",
        "hooks",
        "in_app_browser",
        "js_repl",
        "memories",
        "plugins",
        "remote_plugin",
        "tool_search",
    }
)
_APP_APPROVAL_ONLY_KEYS = frozenset(
    {"approvals_reviewer", "default_tools_approval_mode"}
)
_APP_CAPABILITY_BOOLEAN_KEYS = frozenset(
    {"default_tools_enabled", "destructive_enabled", "open_world_enabled"}
)
_MCP_IGNORED_KEYS = frozenset({"startup_timeout_sec", "tool_timeout_sec"})
_MCP_RUNTIME_KEYS = frozenset({"args", "command", "cwd", "env", "url"})
_PLUGIN_ALLOWED_KEYS = frozenset({"enabled", "mcp_servers"})
_SHELL_ROUTING_KEYS = frozenset(
    {
        "BROWSER_USE_AVAILABLE_BACKENDS",
        "NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
        "NODE_REPL_TRUSTED_CODE_PATHS",
    }
)
_SESSION_PIPE_PATH = (
    "controls",
    "mcp_servers",
    "node_repl",
    "runtime",
    "env",
    "SKY_CUA_NATIVE_PIPE_DIRECTORY",
)
_SESSION_PIPE_PATTERN = re.compile(
    r"^\\\\\.\\pipe\\codex-computer-use-"
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


class CapabilityConfigError(ValueError):
    """The config cannot safely produce a capability authority fingerprint."""


def _normalize_value(value: Any, path: tuple[str, ...]) -> Any:
    if path == _SESSION_PIPE_PATH:
        if not isinstance(value, str) or not _SESSION_PIPE_PATTERN.fullmatch(value):
            raise CapabilityConfigError(
                "node_repl session pipe must use the canonical Codex pipe identity"
            )
        return {
            "type": "volatile-runtime-value",
            "value": "codex-computer-use-session-pipe",
        }

    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str) or not key:
                raise CapabilityConfigError(
                    "TOML mapping keys must be non-empty strings"
                )
            converted = _normalize_value(child, (*path, key))
            if converted is not _OMIT:
                normalized[key] = converted
        return {"type": "table", "value": normalized}
    if isinstance(value, list):
        return {
            "type": "array",
            "value": [
                _normalize_value(child, (*path, str(index)))
                for index, child in enumerate(value)
            ],
        }
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, int):
        return {"type": "integer", "value": value}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CapabilityConfigError("non-finite TOML numbers are not supported")
        return {"type": "float", "value": value}
    if isinstance(value, dt.datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, dt.date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, dt.time):
        return {"type": "time", "value": value.isoformat()}
    raise CapabilityConfigError(f"unsupported TOML value type at {'.'.join(path)}")


def _normalized_control_key(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityConfigError(f"{label} must be a non-empty string")
    normalized = value.strip().casefold()
    if normalized in {".", ".."}:
        raise CapabilityConfigError(f"{label} is invalid")
    return normalized


def _stable_keyed_table(
    value: object, *, label: str
) -> list[tuple[str, dict[str, Any]]]:
    if value is None:
        return []
    if not isinstance(value, dict):
        raise CapabilityConfigError(f"{label} must be a table")
    result: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for raw_key, raw_value in value.items():
        key = _normalized_control_key(raw_key, label=f"{label} key")
        if key in seen:
            raise CapabilityConfigError(f"duplicate normalized {label} key: {key}")
        if not isinstance(raw_value, dict):
            raise CapabilityConfigError(f"{label}.{raw_key} must be a table")
        seen.add(key)
        result.append((key, raw_value))
    return sorted(result, key=lambda item: item[0])


def _project_apps(value: object) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for app_id, config in _stable_keyed_table(value, label="apps"):
        allowed = (
            {"enabled", "tools"}
            | _APP_APPROVAL_ONLY_KEYS
            | _APP_CAPABILITY_BOOLEAN_KEYS
        )
        unknown = set(config) - allowed
        if unknown:
            raise CapabilityConfigError(
                f"unclassified apps.{app_id} keys: {', '.join(sorted(unknown))}"
            )
        enabled = config.get("enabled", True)
        if not isinstance(enabled, bool):
            raise CapabilityConfigError(f"apps.{app_id}.enabled must be Boolean")
        tools: dict[str, Any] = {}
        for tool_id, tool_config in _stable_keyed_table(
            config.get("tools"), label=f"apps.{app_id}.tools"
        ):
            tool_unknown = set(tool_config) - {"approval_mode", "enabled"}
            if tool_unknown:
                raise CapabilityConfigError(
                    f"unclassified apps.{app_id}.tools.{tool_id} keys: "
                    + ", ".join(sorted(tool_unknown))
                )
            if "enabled" in tool_config:
                tool_enabled = tool_config["enabled"]
                if not isinstance(tool_enabled, bool):
                    raise CapabilityConfigError(
                        f"apps.{app_id}.tools.{tool_id}.enabled must be Boolean"
                    )
                tools[tool_id] = {"enabled": tool_enabled}
        projected: dict[str, Any] = {"enabled": enabled, "tools": tools}
        for key in sorted(_APP_CAPABILITY_BOOLEAN_KEYS & set(config)):
            gate = config[key]
            if not isinstance(gate, bool):
                raise CapabilityConfigError(
                    f"apps.{app_id}.{key} must be Boolean"
                )
            projected[key] = gate
        result[app_id] = projected
    return result


def _project_features(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CapabilityConfigError("features must be a table")
    unknown = set(value) - _CAPABILITY_FEATURE_KEYS
    if unknown:
        raise CapabilityConfigError(
            "unclassified capability feature keys: " + ", ".join(sorted(unknown))
        )
    result: dict[str, Any] = {}
    for key in sorted(_CAPABILITY_FEATURE_KEYS & set(value)):
        enabled = value[key]
        if not isinstance(enabled, bool):
            raise CapabilityConfigError(f"features.{key} must be Boolean")
        result[key] = enabled
    return result


def _project_hooks(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CapabilityConfigError("hooks must be a table")
    return {key: child for key, child in value.items() if key != "state"}


def _project_mcp_servers(value: object) -> dict[str, Any]:
    result: dict[str, Any] = {}
    allowed = _MCP_RUNTIME_KEYS | _MCP_IGNORED_KEYS | {
        "enabled",
        "gateway_managed",
        "tools",
    }
    for server_id, config in _stable_keyed_table(value, label="mcp_servers"):
        unknown = set(config) - allowed
        if unknown:
            raise CapabilityConfigError(
                f"unclassified mcp_servers.{server_id} keys: "
                + ", ".join(sorted(unknown))
            )
        enabled = config.get("enabled", True)
        gateway_managed = config.get("gateway_managed", False)
        if not isinstance(enabled, bool):
            raise CapabilityConfigError(
                f"mcp_servers.{server_id}.enabled must be Boolean"
            )
        if not isinstance(gateway_managed, bool):
            raise CapabilityConfigError(
                f"mcp_servers.{server_id}.gateway_managed must be Boolean"
            )
        if gateway_managed and enabled is not False:
            raise CapabilityConfigError(
                f"mcp_servers.{server_id} must set enabled=false when "
                "gateway_managed=true"
            )
        tools: dict[str, Any] = {}
        for tool_id, tool_config in _stable_keyed_table(
            config.get("tools"), label=f"mcp_servers.{server_id}.tools"
        ):
            tool_unknown = set(tool_config) - {"approval_mode", "enabled"}
            if tool_unknown:
                raise CapabilityConfigError(
                    f"unclassified mcp_servers.{server_id}.tools.{tool_id} keys: "
                    + ", ".join(sorted(tool_unknown))
                )
            if "enabled" in tool_config:
                tool_enabled = tool_config["enabled"]
                if not isinstance(tool_enabled, bool):
                    raise CapabilityConfigError(
                        f"mcp_servers.{server_id}.tools.{tool_id}.enabled "
                        "must be Boolean"
                    )
                tools[tool_id] = {"enabled": tool_enabled}
        runtime = {
            key: config[key]
            for key in sorted(_MCP_RUNTIME_KEYS)
            if key in config
        }
        result[server_id] = {
            "enabled": enabled,
            "gateway_managed": gateway_managed,
            "runtime": runtime,
            "tools": tools,
        }
    return result


def _project_plugins(value: object) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for plugin_id, config in _stable_keyed_table(value, label="plugins"):
        unknown = set(config) - _PLUGIN_ALLOWED_KEYS
        if unknown:
            raise CapabilityConfigError(
                f"unclassified plugins.{plugin_id} keys: "
                + ", ".join(sorted(unknown))
            )
        enabled = config.get("enabled", True)
        if not isinstance(enabled, bool):
            raise CapabilityConfigError(
                f"plugins.{plugin_id}.enabled must be Boolean"
            )
        nested: dict[str, Any] = {}
        for server_id, server in _stable_keyed_table(
            config.get("mcp_servers"),
            label=f"plugins.{plugin_id}.mcp_servers",
        ):
            if set(server) != {"enabled"}:
                raise CapabilityConfigError(
                    f"plugins.{plugin_id}.mcp_servers.{server_id} "
                    "may contain only enabled"
                )
            server_enabled = server.get("enabled")
            if not isinstance(server_enabled, bool):
                raise CapabilityConfigError(
                    f"plugins.{plugin_id}.mcp_servers.{server_id}.enabled "
                    "must be Boolean"
                )
            nested[server_id] = {"enabled": server_enabled}
        result[plugin_id] = {"enabled": enabled, "mcp_servers": nested}
    return result


def _canonical_skill_path(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityConfigError("skills.config path must be a non-empty string")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise CapabilityConfigError("skills.config path must be absolute")
    return os.path.normcase(str(path.resolve(strict=False))).casefold()


def _project_skills(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {"config"}:
        raise CapabilityConfigError("skills may contain only the config array")
    config = value.get("config", [])
    if not isinstance(config, list):
        raise CapabilityConfigError("skills.config must be an array of tables")
    result: dict[str, Any] = {}
    for position, item in enumerate(config):
        if not isinstance(item, dict) or set(item) != {"path", "enabled"}:
            raise CapabilityConfigError(
                f"skills.config item {position} must contain path and enabled"
            )
        path = _canonical_skill_path(item.get("path"))
        enabled = item.get("enabled")
        if not isinstance(enabled, bool):
            raise CapabilityConfigError(
                f"skills.config item {position} enabled must be Boolean"
            )
        if path in result:
            raise CapabilityConfigError(f"duplicate normalized skill path: {path}")
        result[path] = {"enabled": enabled}
    return dict(sorted(result.items()))


def _project_shell_environment(value: object) -> dict[str, Any]:
    if value is None:
        return {"set": {}}
    if not isinstance(value, dict):
        raise CapabilityConfigError("shell_environment_policy must be a table")
    raw_set = value.get("set", {})
    if not isinstance(raw_set, dict):
        raise CapabilityConfigError("shell_environment_policy.set must be a table")
    selected: dict[str, str] = {}
    for key in sorted(_SHELL_ROUTING_KEYS & set(raw_set)):
        value = raw_set[key]
        if not isinstance(value, str):
            raise CapabilityConfigError(
                f"shell_environment_policy.set.{key} must be a string"
            )
        selected[key] = value
    return {"set": selected}


def capability_config_projection(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise CapabilityConfigError("config TOML root must be a table")
    unknown_roots = sorted(set(data) - KNOWN_ROOTS)
    if unknown_roots:
        raise CapabilityConfigError(
            "unclassified top-level config keys: " + ", ".join(unknown_roots)
        )

    controls = {
        "apps": _project_apps(data.get("apps")),
        "features": _project_features(data.get("features")),
        "hooks": _project_hooks(data.get("hooks")),
        "mcp_servers": _project_mcp_servers(data.get("mcp_servers")),
        "plugins": _project_plugins(data.get("plugins")),
        "shell_environment_policy": _project_shell_environment(
            data.get("shell_environment_policy")
        ),
        "skills": _project_skills(data.get("skills")),
    }
    return {
        "projection_schema": PROJECTION_SCHEMA,
        "controls": _normalize_value(controls, ("controls",)),
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_pointer_segment(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def capability_config_leaf_hashes(projection: dict[str, Any]) -> dict[str, str]:
    """Return typed, non-reversible hashes for every semantic projection leaf."""

    leaves: dict[str, str] = {}

    def record(path: tuple[str, ...], value: Any) -> None:
        pointer = "/" + "/".join(_json_pointer_segment(item) for item in path)
        leaves[pointer] = hashlib.sha256(
            _canonical_json(value).encode("utf-8")
        ).hexdigest().upper()

    def walk(path: tuple[str, ...], node: Any) -> None:
        if not isinstance(node, dict) or set(node) != {"type", "value"}:
            raise CapabilityConfigError("normalized projection node is malformed")
        node_type = node["type"]
        value = node["value"]
        if node_type == "table":
            if not isinstance(value, dict):
                raise CapabilityConfigError("normalized table value is malformed")
            for key in sorted(value):
                walk((*path, key), value[key])
            return
        if node_type == "array":
            if not isinstance(value, list):
                raise CapabilityConfigError("normalized array value is malformed")
            record((*path, "@type"), {"type": "array", "length": len(value)})
            for index, child in enumerate(value):
                walk((*path, str(index)), child)
            return
        record(path, node)

    controls = projection.get("controls")
    if not isinstance(controls, dict):
        raise CapabilityConfigError("projection controls are malformed")
    walk((), controls)
    return dict(sorted(leaves.items()))


def _json_pointer_parts(value: str) -> tuple[str, ...]:
    if not value.startswith("/"):
        raise CapabilityConfigError("config control pointer is malformed")
    return tuple(
        part.replace("~1", "/").replace("~0", "~")
        for part in value[1:].split("/")
    )


def _normalized_value(node: Any) -> Any:
    if not isinstance(node, dict) or set(node) != {"type", "value"}:
        raise CapabilityConfigError("normalized projection node is malformed")
    node_type = node["type"]
    value = node["value"]
    if node_type == "table":
        if not isinstance(value, dict):
            raise CapabilityConfigError("normalized table value is malformed")
        return {key: _normalized_value(child) for key, child in value.items()}
    if node_type == "array":
        if not isinstance(value, list):
            raise CapabilityConfigError("normalized array value is malformed")
        return [_normalized_value(child) for child in value]
    return value


def capability_config_control_descriptors(
    projection: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Describe every exact routing-affecting leaf without broad prefixes."""

    controls_node = projection.get("controls")
    controls = _normalized_value(controls_node)
    if not isinstance(controls, dict):
        raise CapabilityConfigError("projection controls are malformed")
    descriptors: dict[str, dict[str, Any]] = {}
    for pointer in capability_config_leaf_hashes(projection):
        parts = _json_pointer_parts(pointer)
        descriptor: dict[str, Any] | None = None
        if len(parts) == 3 and parts[0] == "apps" and parts[2] == "enabled":
            enabled = controls["apps"][parts[1]]["enabled"]
            descriptor = {
                "change_class": "availability_toggle",
                "control_kind": "app",
                "control_key": parts[1],
                "enabled": enabled,
            }
        elif (
            len(parts) == 3
            and parts[0] == "apps"
            and parts[2] in _APP_CAPABILITY_BOOLEAN_KEYS
        ):
            descriptor = {
                "change_class": "runtime_identity",
                "control_kind": "app",
                "control_key": parts[1],
            }
        elif (
            len(parts) == 5
            and parts[0] == "apps"
            and parts[2] == "tools"
            and parts[4] == "enabled"
        ):
            descriptor = {
                "change_class": "runtime_identity",
                "control_kind": "app_tool",
                "control_key": f"{parts[1]}/{parts[3]}",
            }
        elif parts == ("features", "js_repl"):
            descriptor = {
                "change_class": "runtime_identity",
                "control_kind": "app_runtime",
                "control_key": "node_repl",
            }
        elif len(parts) == 2 and parts[0] == "features":
            descriptor = {
                "change_class": "runtime_identity",
                "control_kind": "global_runtime",
                "control_key": parts[1],
            }
        elif parts and parts[0] == "hooks":
            descriptor = {
                "change_class": "runtime_identity",
                "control_kind": "global_runtime",
                "control_key": "hooks",
            }
        elif len(parts) >= 3 and parts[0] == "mcp_servers":
            server_id = parts[1]
            if parts[2] == "enabled" and len(parts) == 3:
                descriptor = {
                    "change_class": "availability_toggle",
                    "control_kind": "mcp",
                    "control_key": server_id,
                    "enabled": controls["mcp_servers"][server_id]["enabled"],
                }
            else:
                descriptor = {
                    "change_class": "runtime_identity",
                    "control_kind": "mcp_runtime",
                    "control_key": server_id,
                }
        elif len(parts) >= 3 and parts[0] == "plugins":
            plugin_id = parts[1]
            if parts[2:] == ("enabled",):
                descriptor = {
                    "change_class": "availability_toggle",
                    "control_kind": "plugin",
                    "control_key": plugin_id,
                    "enabled": controls["plugins"][plugin_id]["enabled"],
                }
            elif (
                len(parts) == 5
                and parts[2] == "mcp_servers"
                and parts[4] == "enabled"
            ):
                server_id = parts[3]
                descriptor = {
                    "change_class": "availability_toggle",
                    "control_kind": "plugin_mcp",
                    "control_key": f"{plugin_id}/{server_id}",
                    "enabled": controls["plugins"][plugin_id]["mcp_servers"][
                        server_id
                    ]["enabled"],
                }
        elif (
            len(parts) == 3
            and parts[0] == "shell_environment_policy"
            and parts[1] == "set"
        ):
            descriptor = {
                "change_class": "runtime_identity",
                "control_kind": "app_runtime",
                "control_key": "node_repl",
            }
        elif len(parts) == 3 and parts[0] == "skills" and parts[2] == "enabled":
            skill_path = parts[1]
            descriptor = {
                "change_class": "availability_toggle",
                "control_kind": "skill",
                "control_key": skill_path,
                "enabled": controls["skills"][skill_path]["enabled"],
            }
        if descriptor is None:
            raise CapabilityConfigError(
                f"config projection leaf has no exact control descriptor: {pointer}"
            )
        descriptors[pointer] = descriptor
    return dict(sorted(descriptors.items()))


def capability_config_fingerprint(path: Path) -> str:
    return str(capability_config_authority(path)["sha256"])


def _configured_disabled_skill_paths(data: dict[str, Any]) -> list[str]:
    skills = data.get("skills")
    if skills is None:
        return []
    if not isinstance(skills, dict):
        raise CapabilityConfigError("skills must be a table")
    config = skills.get("config", [])
    if not isinstance(config, list):
        raise CapabilityConfigError("skills.config must be an array of tables")
    disabled: list[str] = []
    for position, item in enumerate(config):
        if not isinstance(item, dict):
            raise CapabilityConfigError(
                f"skills.config item {position} must be a table"
            )
        item_path = item.get("path")
        enabled = item.get("enabled")
        if not isinstance(item_path, str) or not item_path.strip():
            raise CapabilityConfigError(
                f"skills.config item {position} must have a non-empty path"
            )
        if not Path(item_path).is_absolute():
            raise CapabilityConfigError(
                f"skills.config item {position} path must be absolute"
            )
        if not isinstance(enabled, bool):
            raise CapabilityConfigError(
                f"skills.config item {position} must have a Boolean enabled value"
            )
        if not enabled:
            disabled.append(str(Path(item_path).expanduser().resolve(strict=False)))
    return sorted(set(disabled), key=str.casefold)


def _configured_mcp_controls(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    disabled: set[str] = set()
    gateway_managed: set[str] = set()
    servers = data.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise CapabilityConfigError("mcp_servers must be a table")
    for name, server in servers.items():
        if not isinstance(server, dict):
            raise CapabilityConfigError(f"mcp_servers.{name} must be a table")
        if server.get("enabled") is False:
            disabled.add(str(name))
            if server.get("gateway_managed") is True:
                gateway_managed.add(str(name))

    plugins = data.get("plugins", {})
    if not isinstance(plugins, dict):
        raise CapabilityConfigError("plugins must be a table")
    for plugin_name, plugin in plugins.items():
        if not isinstance(plugin, dict):
            raise CapabilityConfigError(f"plugins.{plugin_name} must be a table")
        plugin_servers = plugin.get("mcp_servers", {})
        if not isinstance(plugin_servers, dict):
            raise CapabilityConfigError(
                f"plugins.{plugin_name}.mcp_servers must be a table"
            )
        for name, server in plugin_servers.items():
            if not isinstance(server, dict):
                raise CapabilityConfigError(
                    f"plugins.{plugin_name}.mcp_servers.{name} must be a table"
                )
            if server.get("enabled") is False:
                disabled.add(str(name))
    return sorted(disabled, key=str.casefold), sorted(gateway_managed, key=str.casefold)


def capability_config_authority(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        data = tomllib.loads(text)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise CapabilityConfigError(f"cannot parse capability config: {exc}") from exc

    projection = capability_config_projection(data)
    canonical = _canonical_json(projection)
    control_descriptors = capability_config_control_descriptors(projection)
    disabled_mcp, gateway_managed = _configured_mcp_controls(data)
    return {
        "projection_schema": PROJECTION_SCHEMA,
        "source_hash_key": SOURCE_HASH_KEY,
        "hash_scope": HASH_SCOPE,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest().upper(),
        "raw_sha256": hashlib.sha256(raw).hexdigest().upper(),
        "projection_leaf_hashes": capability_config_leaf_hashes(projection),
        "control_descriptors": control_descriptors,
        "disabled_skill_paths": _configured_disabled_skill_paths(data),
        "explicitly_disabled_mcp_names": disabled_mcp,
        "gateway_managed_mcp_names": gateway_managed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute the versioned semantic capability authority for config.toml."
    )
    parser.add_argument("config_path", type=Path)
    args = parser.parse_args()
    try:
        authority = capability_config_authority(args.config_path)
    except CapabilityConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(_canonical_json(authority))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
