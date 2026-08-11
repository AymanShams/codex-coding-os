#!/usr/bin/env python3
"""Canonical capability-control authority for Codex config.toml."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import tomllib

PROJECTION_SCHEMA = "capability-config-v1"
SOURCE_HASH_KEY = "config-capability-projection-v1"
PROJECTION_ROOTS = (
    "apps",
    "features",
    "hooks",
    "marketplaces",
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
_SESSION_PIPE_PATH = (
    "mcp_servers",
    "node_repl",
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
    if len(path) == 3 and path[0] == "marketplaces" and path[2] == "last_updated":
        if not isinstance(value, (str, dt.date, dt.datetime)):
            raise CapabilityConfigError(
                "marketplace last_updated must be a string or TOML date-time"
            )
        return _OMIT

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


def capability_config_projection(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise CapabilityConfigError("config TOML root must be a table")
    unknown_roots = sorted(set(data) - KNOWN_ROOTS)
    if unknown_roots:
        raise CapabilityConfigError(
            "unclassified top-level config keys: " + ", ".join(unknown_roots)
        )

    sections: dict[str, Any] = {}
    for root in PROJECTION_ROOTS:
        present = root in data
        value = data.get(root)
        if present and not isinstance(value, dict):
            raise CapabilityConfigError(f"config section {root} must be a table")
        sections[root] = {
            "present": present,
            "value": _normalize_value(value, (root,)) if present else None,
        }
    return {
        "projection_schema": PROJECTION_SCHEMA,
        "sections": sections,
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
            record((*path, "@type"), "table")
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

    sections = projection.get("sections")
    if not isinstance(sections, dict):
        raise CapabilityConfigError("projection sections are malformed")
    for root in sorted(sections):
        section = sections[root]
        if not isinstance(section, dict) or set(section) != {"present", "value"}:
            raise CapabilityConfigError(f"projection section {root} is malformed")
        present = section["present"]
        if not isinstance(present, bool):
            raise CapabilityConfigError(f"projection section {root} presence is malformed")
        record((root, "@present"), {"type": "boolean", "value": present})
        if present:
            walk((root,), section["value"])
    return dict(sorted(leaves.items()))


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
    disabled_mcp, gateway_managed = _configured_mcp_controls(data)
    return {
        "projection_schema": PROJECTION_SCHEMA,
        "source_hash_key": SOURCE_HASH_KEY,
        "hash_scope": HASH_SCOPE,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest().upper(),
        "raw_sha256": hashlib.sha256(raw).hexdigest().upper(),
        "projection_leaf_hashes": capability_config_leaf_hashes(projection),
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
