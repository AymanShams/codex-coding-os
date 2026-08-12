#!/usr/bin/env python3
"""Read-only capability manifest and routing-policy consumer.

The runtime owns no capability catalogue and no pairwise overlap map. It reads
the compact global snapshot produced by the universal catalogue workflow, then
applies the ordered routing policy to active entries only.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import tomllib

from capability_config_fingerprint import (
    HASH_SCOPE as CONFIG_CAPABILITY_HASH_SCOPE,
)
from capability_config_fingerprint import (
    SOURCE_HASH_KEY as CONFIG_CAPABILITY_SOURCE_HASH_KEY,
)
from capability_config_fingerprint import (
    CapabilityConfigError,
)
from capability_config_fingerprint import (
    capability_config_fingerprint as _capability_config_fingerprint,
)


class CapabilityDataError(ValueError):
    """Raised when a present manifest or policy is structurally invalid."""


def capability_config_fingerprint(path: Path) -> str:
    """Return the shared semantic config fingerprint using router error semantics."""

    try:
        return _capability_config_fingerprint(path)
    except CapabilityConfigError as exc:
        raise CapabilityDataError(str(exc)) from exc


class RouteRegistryError(RuntimeError):
    """Raised when a schema-valid route cannot be durably registered."""


CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
ROUTING_DIR = Path(
    os.environ.get(
        "CODEX_CAPABILITY_ROUTING_DIR",
        str(CODEX_HOME / "capability-routing"),
    )
)
ACTIVE_CAPABILITIES_PATH = Path(
    os.environ.get(
        "CODEX_ACTIVE_CAPABILITIES_PATH",
        os.environ.get(
            "CODEX_ACTIVE_CAPABILITIES",
            os.environ.get(
                "CODEX_CAPABILITY_MANIFEST",
                str(ROUTING_DIR / "active-capabilities.json"),
            ),
        ),
    )
)
ROUTING_POLICY_PATH = Path(
    os.environ.get(
        "CODEX_ROUTING_POLICY_PATH",
        os.environ.get(
            "CODEX_ROUTING_POLICY",
            str(ROUTING_DIR / "routing-policy.yaml"),
        ),
    )
)
CONFIG_PATH = Path(os.environ.get("CODEX_CONFIG_PATH", str(CODEX_HOME / "config.toml")))
ROUTE_DECISION_SCHEMA_PATH = Path(
    os.environ.get(
        "CODEX_ROUTE_DECISION_SCHEMA_PATH",
        str(ROUTING_DIR / "route-decision.schema.json"),
    )
)
ROUTE_DECISION_REGISTRY_PATH = Path(
    os.environ.get(
        "CODEX_ROUTE_DECISION_REGISTRY_PATH",
        str(ROUTING_DIR / "route-decisions.sqlite3"),
    )
)
PROJECT_SCOPE_MAP_PATH = Path(
    os.environ.get(
        "CODEX_PROJECT_SCOPE_MAP_PATH",
        str(ROUTING_DIR / "project-scope-map.json"),
    )
)

ACTIVE_STATES = {
    "active",
    "enabled",
    "exposed",
    "installed-active",
    "runtime-active",
    "verified-active",
}
FRESH_STATES = {"current", "fresh", "live", "valid", "verified"}
STATE_ARTIFACT_KINDS = {
    "routing-state",
    "snapshot",
    "state",
    "state-artifact",
    "stateartifact",
}
SUPPRESS_ACTIONS = {"block", "disable", "remove", "suppress"}
TACTICAL_ACTIONS = {"tactical", "tactical-only"}
DEFAULT_MAX_SUPPORTS = 2
ABSOLUTE_MAX_SUPPORTS = 2
DEFAULT_MAX_WORKER_SUPPORTS = 2
ABSOLUTE_MAX_WORKER_SUPPORTS = 2
ROUTE_REGISTRY_SCHEMA_VERSION = 3
DEFAULT_ROUTE_TTL_SECONDS = 86400
EXPIRED_ROUTE_AUDIT_RETENTION_SECONDS = 86400
MAX_REGISTERED_ROUTES = 10000
MAX_LOCAL_INSTRUCTION_CHARACTERS = 50000
WORKER_FAMILIES = {"local_agent_stack", "terra", "antigravity"}
EXECUTION_DISPOSITION_MODES = {"codex_only", "worker_support"}
CODEX_ONLY_EXECUTION_DISPOSITION = {
    "mode": "codex_only",
    "eligible_worker_families": [],
}
EXECUTION_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
ROUTE_REGISTRY_COLUMNS = (
    "decision_id",
    "decision_digest",
    "task_text_sha256",
    "task_input_sha256",
    "route_json",
    "route_json_sha256",
    "schema_version",
    "manifest_snapshot",
    "decision_snapshot",
    "manifest_authority_sha256",
    "policy_authority_sha256",
    "issued_at",
    "expires_at",
)
DEFAULT_FALLBACK = {
    "on_unavailable": "return_to_codex",
    "on_timeout": "return_to_codex",
    "on_error": "return_to_codex",
    "automatic_retry": False,
}
DEFAULT_EXECUTION_PROFILE = {
    "execution_owner": "codex_parent",
    "model": "gpt-5.6-sol",
    "reasoning_effort": "high",
    "deadline_seconds": 1800,
    "fallback": DEFAULT_FALLBACK,
}
APPROVED_WORKER_CONTRACTS = {
    ("codex_child", "read_heavy"): ("gpt-5.6-terra", "medium"),
    ("codex_child", "independent_challenger"): ("gemini-3.1-pro-high", "high"),
    ("local_agent_stack", "fast"): ("qwen3.5:2b-q8_0", None),
    ("local_agent_stack", "coding"): ("qwen2.5-coder:7b-instruct-q6_K", None),
    ("local_agent_stack", "critic"): ("deepseek-r1:7b-qwen-distill-q4_K_M", None),
}
APPROVED_LOCAL_EXECUTION_CONTRACTS = {
    "runtime_status": ("runtime_status", "status", "none", False),
    "memory_recall": ("prior_continuity", "recall", "memory", False),
    "source_lookup": ("project_evidence_lookup", "research", "index", False),
    "retrieval_bundle": ("retrieval_bundle", "research", "both", False),
    "literal_extraction": ("literal_structured_extraction", "extract", "none", True),
}
WORKER_TASK_GATE_RECIPES = (
    {
        "family": "antigravity",
        "flags": frozenset({"antigravity_eligible", "antigravity_support_required"}),
        "roles": ("independent_challenger",),
        "task_type": "review",
        "complexity": "high",
        "purpose": "explicit_challenge",
        "source_needs": frozenset({"none"}),
    },
    {
        "family": "terra",
        "flags": frozenset({"terra_read_heavy", "terra_support_required"}),
        "roles": ("read_heavy",),
        "task_type": "review",
        "complexity": "high",
        "purpose": "read_heavy_support",
        "source_needs": frozenset({"none"}),
    },
    {
        "family": "local_agent_stack",
        "flags": frozenset(
            {
                "local_fast_eligible",
                "bounded_classification_or_transformation",
                "local_support_required",
            }
        ),
        "roles": ("fast",),
        "task_type": "transform",
        "complexity": "low",
        "purpose": "bounded_classification_or_transformation",
        "source_needs": frozenset({"none", "memory", "index", "both"}),
    },
    {
        "family": "local_agent_stack",
        "flags": frozenset(
            {
                "local_fast_eligible",
                "local_critic_eligible",
                "complex_multi_source_synthesis",
                "local_support_required",
            }
        ),
        "roles": ("fast", "critic"),
        "task_type": "synthesize",
        "complexity": "high",
        "purpose": "complex_multi_source_synthesis",
        "source_needs": frozenset({"none", "memory", "index", "both"}),
    },
    {
        "family": "local_agent_stack",
        "flags": frozenset(
            {
                "local_coding_eligible",
                "focused_coding_assistance",
                "local_support_required",
            }
        ),
        "roles": ("coding", "critic"),
        "task_type": "implement",
        "complexity": "medium",
        "purpose": "focused_coding_assistance",
        "source_needs": frozenset({"none", "index"}),
    },
    {
        "family": "local_agent_stack",
        "flags": frozenset(
            {
                "local_critic_eligible",
                "explicit_challenge",
                "local_support_required",
            }
        ),
        "roles": ("critic",),
        "task_type": "review",
        "complexity": "medium",
        "purpose": "explicit_challenge",
        "source_needs": frozenset({"none", "memory", "index", "both"}),
    },
)
LOCAL_OPERATION_TASK_GATE_RECIPES = {
    "runtime_status": {
        "flags": frozenset({"local_runtime_status", "runtime_status"}),
        "task_type": "status",
        "complexity": "low",
        "purpose": "runtime_status",
        "source_need": "none",
    },
    "memory_recall": {
        "flags": frozenset({"prior_continuity", "memory_recall"}),
        "task_type": "recall",
        "complexity": "medium",
        "purpose": "prior_continuity",
        "source_need": "memory",
    },
    "source_lookup": {
        "flags": frozenset({"project_evidence_lookup", "source_lookup"}),
        "task_type": "research",
        "complexity": "medium",
        "purpose": "project_evidence_lookup",
        "source_need": "index",
    },
    "retrieval_bundle": {
        "flags": frozenset(
            {
                "prior_continuity",
                "memory_recall",
                "project_evidence_lookup",
                "source_lookup",
            }
        ),
        "task_type": "research",
        "complexity": "medium",
        "purpose": "retrieval_bundle",
        "source_need": "both",
    },
    "literal_extraction": {
        "flags": frozenset({"literal_structured_extraction"}),
        "task_type": "extract",
        "complexity": "low",
        "purpose": "literal_structured_extraction",
        "source_need": "none",
    },
}
TASK_GATE_POSITIVE_FLAGS = frozenset(
    flag
    for recipe in (*WORKER_TASK_GATE_RECIPES, *LOCAL_OPERATION_TASK_GATE_RECIPES.values())
    for flag in recipe["flags"]
)


def normalize(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def tokenize(value: object) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9+.-]*", str(value or "").lower())
        if len(token) > 1 and not token.isdigit()
    }


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,|]", value) if item.strip()]
    if isinstance(value, dict):
        flattened: list[str] = []
        for nested in value.values():
            flattened.extend(_as_list(nested))
        return flattened
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


_DOTNET_UNIX_EPOCH_TICKS = 621355968000000000


def _plugin_cache_inventory_rows(
    codex_home: Path | None = None,
) -> tuple[str, ...] | None:
    """Return the bounded passive plugin authority inventory without subprocesses."""

    cache_root = (codex_home or CODEX_HOME) / "plugins" / "cache"
    try:
        resolved_cache_root = cache_root.resolve(strict=True)
        if not resolved_cache_root.is_dir():
            return None
        rows: list[str] = []
        for marketplace in cache_root.iterdir():
            if not marketplace.is_dir():
                continue
            for plugin in marketplace.iterdir():
                if not plugin.is_dir():
                    continue
                if plugin.name.casefold().startswith("plugin-install-"):
                    continue
                for version in plugin.iterdir():
                    if not version.is_dir():
                        continue
                    resolved_version = version.resolve(strict=True)
                    if not resolved_version.is_relative_to(resolved_cache_root):
                        return None
                    plugin_manifest = version / ".codex-plugin" / "plugin.json"
                    if not plugin_manifest.is_file():
                        continue
                    relative_root = version.relative_to(cache_root).as_posix().lower()
                    root_ticks = (
                        version.lstat().st_mtime_ns // 100 + _DOTNET_UNIX_EPOCH_TICKS
                    )
                    rows.append(f"ROOT\t{relative_root}\t0\t{root_ticks}")

                    authority_files = [
                        plugin_manifest,
                        version / ".app.json",
                        version / ".mcp.json",
                    ]
                    skills_root = version / "skills"
                    if skills_root.is_dir():
                        for skill_directory in skills_root.iterdir():
                            if skill_directory.is_dir():
                                authority_files.append(skill_directory / "SKILL.md")
                    for authority_file in authority_files:
                        if not authority_file.is_file():
                            continue
                        resolved_file = authority_file.resolve(strict=True)
                        if not resolved_file.is_relative_to(resolved_cache_root):
                            return None
                        stat = authority_file.stat()
                        relative_file = (
                            authority_file.relative_to(cache_root).as_posix().lower()
                        )
                        file_ticks = (
                            stat.st_mtime_ns // 100 + _DOTNET_UNIX_EPOCH_TICKS
                        )
                        rows.append(
                            f"FILE\t{relative_file}\t{stat.st_size}\t{file_ticks}"
                        )
    except (OSError, RuntimeError, ValueError):
        return None

    return tuple(sorted(rows))


def _plugin_cache_inventory_hash(codex_home: Path | None = None) -> str:
    """Hash the bounded passive plugin authority inventory without subprocesses."""

    rows = _plugin_cache_inventory_rows(codex_home)
    if rows is None:
        return ""
    canonical = "\n".join(rows)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest().upper()


def _source_hash_path(name: str) -> Path | None:
    known = {
        "config.toml": CONFIG_PATH,
        CONFIG_CAPABILITY_SOURCE_HASH_KEY: CONFIG_PATH,
        "hooks.json": CODEX_HOME / "hooks.json",
        "AGENTS.md": CODEX_HOME / "AGENTS.md",
        "task-routing-gate.md": CODEX_HOME / "docs" / "context" / "task-routing-gate.md",
        "catalogue-router.SKILL.md": CODEX_HOME / "skills" / "catalogue-router" / "SKILL.md",
        "capability_index.py": CODEX_HOME / "hooks" / "capability_index.py",
        "capability_config_fingerprint.py": CODEX_HOME
        / "hooks"
        / "capability_config_fingerprint.py",
        "capability_index_cli.py": CODEX_HOME / "hooks" / "capability_index_cli.py",
        "user_prompt_skill_router.py": CODEX_HOME / "hooks" / "user_prompt_skill_router.py",
        "capability_manifest_recovery.py": CODEX_HOME
        / "hooks"
        / "capability_manifest_recovery.py",
        "capability_index_session_start.py": COïÍ8ÒÚ$z{-®éÜj×6µ²'&V6öåö6öFR%ÒÀ¢Ð¢¢6&–Æ—G•öfÆÆ&6µ÷&V6öç2æW‡FVæB€¢°¢$4$”Ä•E•ôDUTäDTä5•ôdÄÄ$4²"À¢FWVæFVæ7•öfÆÆ&6µ²'&V6öåö6öFR%ÒÀ¢Ð¢¢–bfÆÆ&6µ÷&VfW&Væ6RæBæ÷BfÆÆ&6µ÷W6&ÆS ¢6&–Æ—G•öfÆÆ&6µ÷&V6öç2æVæB€¢$4$”Ä•E•ôdÄÄ$4µõTäd”Ä$ÄUõ$UEU$äTEõDõô4ôDU‚ ¢¢'&V° ¢–bæ÷B&–Ö'•÷W6&ÆS ¢6öçF–çVP¢6VÆV7FVE÷'VÆRÒ'VÆP¢6VÆV7FVE÷&–Ö'’Ò&–Ö'¢6VÆV7FVE÷7W÷'G2Ò÷&W6öÇfU÷7W÷'G2€¢'VÆRævWB‚'7W÷'G2"ÂµÒ’À¢&ö×C×&ö×BÀ¢&–Ö'“×&–Ö'’À¢Ö…÷7W÷'G3ÖÖ…÷7W÷'G2À¢öÆ–7“×öÆ–7’À¢'VÆS×'VÆRÀ¢'•ö–CÖ'•ö–BÀ¢'•öÆ–3Ö'•öÆ–2À¢¢'&V° ¢W†V7WF–öåöF—7÷6—F–öâÂF—7÷6—F–öåöW'&÷"Ò÷&W6öÇfUöW†V7WF–öåöF—7÷6—F–öâ€¢6Æ76–f–6F–öâÂW†7Eö–çWBÂ–çWEöÖöFP¢¢VÆ–v–&ÆU÷v÷&¶W%öfÖ–Æ–W2Ò6WB€¢W†V7WF–öåöF—7÷6—F–öå²&VÆ–v–&ÆU÷v÷&¶W%öfÖ–Æ–W2%Ð¢–bW†V7WF–öåöF—7÷6—F–öå²&ÖöFR%ÒÓÒ'v÷&¶W%÷7W÷'B ¢VÇ6RµÐ¢¢v÷&¶W%öGFV×FVBÒ÷v÷&¶W%÷'VÆUöGFV×FVB€¢&ö×EöÆ÷vW"Â6Æ76–f–6F–öâÂöÆ–7’ævWB‚'v÷&¶W%÷'VÆW2"ÂµÒ¢¢Æö6Åö÷W&F–öåöGFV×FVBÒ÷v÷&¶W%÷'VÆUöGFV×FVB€¢&ö×EöÆ÷vW"Â6Æ76–f–6F–öâÂöÆ–7’ævWB‚&Æö6ÅöW†V7WF–öå÷'VÆW2"ÂµÒ¢¢7W÷'E÷v÷&¶W'2Âv÷&¶W%÷&V6öç2Ò÷6VÆV7E÷7W÷'E÷v÷&¶W'2€¢&ö×EöÆ÷vW"À¢6Æ76–f–6F–öâÀ¢VÆ–v–&ÆU÷v÷&¶W%öfÖ–Æ–W2À¢öÆ–7’À¢'•ö–BÀ¢'•öÆ–2À¢¢F6µövFU÷v÷&¶W%öVÆ–v–&ÆRÒ&ööÂ†VÆ–v–&ÆU÷v÷&¶W%öfÖ–Æ–W2’æBÆÂ€¢÷fÆ–FFVE÷v÷&¶W%÷&öÆW2†6Æ76–f–6F–öâÂfÖ–Ç’’—2æ÷BæöæP¢f÷"fÖ–Ç’–âVÆ–v–&ÆU÷v÷&¶W%öfÖ–Æ–W0¢¢–b€¢W†V7WF–öåöF—7÷6—F–öå²&ÖöFR%ÒÓÒ'v÷&¶W%÷7W÷'B ¢æBv÷&¶W%öGFV×FV@¢æBæ÷BF6µövFU÷v÷&¶W%öVÆ–v–&ÆP¢æB%tõ$´U%õD4µôtDUõEUÄUô”ådÄ”B"æ÷B–âv÷&¶W%÷&V6öç0¢“ ¢v÷&¶W%÷&V6öç2æVæB‚%tõ$´U%ôTÄ”t”$”Ä•E•õ$UT•$TB"¢VÆ–b€¢W†V7WF–öåöF—7÷6—F–öå²&ÖöFR%ÒÓÒ&6öFW…ööæÇ’ ¢æBF—7÷6—F–öåöW'&÷"—2æöæP¢æBv÷&¶W%öGFV×FV@¢“ ¢v÷&¶W%÷&V6öç2æVæB‚$4ôDU…ôôäÅ•ôD•5õ4•D”ôâ"¢Æö6ÅöW†V7WF–öâÂÆö6Å÷&V6öç2ÒöFW&—fUöÆö6ÅöW†V7WF–öâ€¢7W÷'E÷v÷&¶W'2À¢6Æ76–f–6F–öâÀ¢W†7Eö–çWBÀ¢&ö×EöÆ÷vW"À¢öÆ–7’À¢'•ö–BÀ¢'•öÆ–2À¢¢Æö6Åö÷W&F–öåöVÆ–v–&ÆRÒ€¢÷fÆ–FFVEöÆö6Åö÷W&F–öå÷&V6—R†6Æ76–f–6F–öâÂW†7Eö–çWB’—2æ÷BæöæP¢¢–b€¢Æö6Åö÷W&F–öåöGFV×FV@¢æBæ÷BÆö6ÅöW†V7WF–öâævWB‚&FÖ—GFVB"¢æBæ÷BÆö6Åö÷W&F–öåöVÆ–v–&ÆP¢æB$Äô4ÅõD4µôtDUõEUÄUô”ådÄ”B"æ÷B–âÆö6Å÷&V6öç0¢“ ¢Æö6Å÷&V6öç2æVæB‚$Äô4ÅôõU$D”ôåôTÄ”t”$”Ä•E•õ$UT•$TB"¢6ö×ÆWFUö–ç7G'V7F–öâÒW†7Eö–çWBævWB‚&–ç7G'V7F–öâ"¢Æö6Åö–çWE÷FöõöÆ&vRÒ€¢–çWEöÖöFRÓÒ&6ö×ÆWFR ¢æB—6–ç7Fæ6R†6ö×ÆWFUö–ç7G'V7F–öâÂ7G"¢æBÆVâ†6ö×ÆWFUö–ç7G'V7F–öâ’âÔ…ôÄô4Åô”å5E%T5D”ôåô4„$5DU%0¢æB€¢Æö6ÅöW†V7WF–öâævWB‚&FÖ—GFVB"¢÷"ç’€¢v÷&¶W"ævWB‚&W†V7WF–öåö÷væW""’ÓÒ&Æö6ÅövVçE÷7F6² ¢f÷"v÷&¶W"–â7W÷'E÷v÷&¶W'0¢¢¢¢–bÆö6Åö–çWE÷FöõöÆ&vS ¢&V¦V7FVEöÆö6Å÷v÷&¶W%÷&V6öç2Ò°¢7G"‡v÷&¶W"ævWB‚'&V6öåö6öFR"’÷"""¢f÷"v÷&¶W"–â7W÷'E÷v÷&¶W'0¢–bv÷&¶W"ævWB‚&W†V7WF–öåö÷væW""’ÓÒ&Æö6ÅövVçE÷7F6² ¢Ð¢7W÷'E÷v÷&¶W'2Ò°¢v÷&¶W ¢f÷"v÷&¶W"–â7W÷'E÷v÷&¶W'0¢–bv÷&¶W"ævWB‚&W†V7WF–öåö÷væW""’Ò&Æö6ÅövVçE÷7F6² ¢Ð¢v÷&¶W%÷&V6öç2Ò°¢&V6öà¢f÷"&V6öâ–âv÷&¶W%÷&V6öç0¢–b&V6öâæ÷B–â&V¦V7FVEöÆö6Å÷v÷&¶W%÷&V6öç0¢æB&V6öâÒ%tõ$´U%õ5Uõ%EôÄ”Ô•EôÄ”TB ¢Ð¢Æö6ÅöW†V7WF–öâÒöf–Åö6Æ÷6VEöÆö6ÅöW†V7WF–öâ†Æö6ÅöW†V7WF–öâ¢Æö6Å÷&V6öç2Ò°¢&V6öà¢f÷"&V6öâ–âÆö6Å÷&V6öç0¢–bæ÷B&V6öâç7F'G7v—F‚‚$Äô4Åõ$T4•Uò"¢æB&V6öâæ÷B–â²$ÔTÔõ%•õ44õUôÔTB"Â$ÔTÔõ%•õ44õUôäôäR'Ð¢Ð¢Æö6Å÷&V6öç2æVæB‚$Äô4Åô”åUEõDôõôÄ$tUõ$UEU$äTEõDõô4ôDU‚"¢çF–w&f—G•÷6VÆV7FVBÒç’€¢÷v÷&¶W%öfÖ–Ç’‡v÷&¶W"’ÓÒ&çF–w&f—G’"f÷"v÷&¶W"–â7W÷'E÷v÷&¶W'0¢¢&÷VæE÷v÷&·76U÷&ö÷BÒö6æöæ–6ÅöW†—7F–æu÷v÷&·76U÷&ö÷B€¢W†7Eö–çWBævWB‚'v÷&·76U÷&ö÷B"¢¢÷WGWE÷66†VÖ÷6†#SbÒW†7Eö–çWBævWB‚&÷WGWE÷66†VÖ÷6†#Sb"¢çF–w&f—G•ö&–æF–æw5÷fÆ–BÒ&ööÂ€¢&÷VæE÷v÷&·76U÷&ö÷@¢æB—6–ç7Fæ6R†÷WGWE÷66†VÖ÷6†#SbÂ7G"¢æB4„#SeõEDU$âægVÆÆÖF6‚†÷WGWE÷66†VÖ÷6†#Sb¢¢&uöW†V7WF–öå÷&WVW7Eö–BÒW†7Eö–çWBævWB‚&W†V7WF–öå÷&WVW7Eö–B"¢W†V7WF–öå÷&WVW7Eö–BÒ€¢&uöW†V7WF–öå÷&WVW7Eö–@¢–b—6–ç7Fæ6R‡&uöW†V7WF–öå÷&WVW7Eö–BÂ7G"¢æBU„T5UD”ôåõ$UTU5Eô”EõEDU$âægVÆÆÖF6‚‡&uöW†V7WF–öå÷&WVW7Eö–B¢VÇ6RæöæP¢¢W†V7WF–öå÷&WVW7FVBÒ&ööÂ‡7W÷'E÷v÷&¶W'2’÷"&ööÂ†Æö6ÅöW†V7WF–öâævWB‚&FÖ—GFVB"’¢W†V7WF–öå÷&WVW7FVBÒW†V7WF–öå÷&WVW7FVB÷"ç’€¢&V6öà¢–â°¢$åD”u$d•E•õ5Uõ%EõTäd”Ä$ÄR"À¢%tõ$´U%õ5Uõ%EõTäd”Ä$ÄR"À¢$Äô4Åõ5Uõ%EõTäd”Ä$ÄR"À¢$Äô4Åõ$T4•UõTå$U4ôÅdTB"À¢$Äô4ÅôU„5EôUd”DTä4Uõ$UT•$TB"À¢$Äô4ÅôU„T5UD”ôåõ44õUõTäd”Ä$ÄR"À¢%$ô¤T5Eô5tEô4ôädÄ”5B"À¢%4õU$4Uõ44õUõTäUD„õ$•¤TB"À¢%tõ$´U%ôTÄ”t”$”Ä•E•õ$UT•$TB"À¢$Äô4ÅôõU$D”ôåôTÄ”t”$”Ä•E•õ$UT•$TB"À¢%tõ$´U%õD4µôtDUõEUÄUô”ådÄ”B"À¢$Äô4ÅõD4µôtDUõEUÄUô”ådÄ”B"À¢Ð¢f÷"&V6öâ–â²§v÷&¶W%÷&V6öç2Â¦Æö6Å÷&V6öç5Ð¢¢W†V7WF–öå÷&WVW7FVBÒW†V7WF–öå÷&WVW7FVB÷"&ööÂ€¢F—7÷6—F–öåöW'&÷"æB‡v÷&¶W%öGFV×FVB÷"Æö6Åö÷W&F–öåöGFV×FVB¢¢&WV—&VE÷F6µöf–VÆG2ÒÆ—7B†Æö6ÅöW†V7WF–öâævWB‚'F6µö–çWE÷&WV—&VÖVçG2"ÂµÒ’¢&V¦V7FVEöÆö6Å÷&V6öåö6öFW3¢6WE·7G%ÒÒ6WB‚¢–bæ÷BÆö6ÅöW†V7WF–öâævWB‚&FÖ—GFVB"“ ¢&V¦V7FVEöÆö6Å÷&V6öåö6öFW2Ò°¢7G"‡v÷&¶W"ævWB‚'&V6öåö6öFR"’÷"""¢f÷"v÷&¶W"–â7W÷'E÷v÷&¶W'0¢–bv÷&¶W"ævWB‚&W†V7WF–öåö÷væW""’ÓÒ&Æö6ÅövVçE÷7F6² ¢Ð¢–b&V¦V7FVEöÆö6Å÷&V6öåö6öFW3 ¢7W÷'E÷v÷&¶W'2Ò°¢v÷&¶W ¢f÷"v÷&¶W"–â7W÷'E÷v÷&¶W'0¢–bv÷&¶W"ævWB‚&W†V7WF–öåö÷væW""’Ò&Æö6ÅövVçE÷7F6² ¢Ð¢v÷&¶W%÷&V6öç2Ò°¢&V6öà¢f÷"&V6öâ–âv÷&¶W%÷&V6öç0¢–b&V6öâæ÷B–â&V¦V7FVEöÆö6Å÷&V6öåö6öFW0¢æB&V6öâÒ%tõ$´U%õ5Uõ%EôÄ”Ô•EôÄ”TB ¢Ð¢Æö6Å÷&V6öç2æVæB‚$Äô4Åõtõ$´U%5õ$UEU$äTEõDõô4ôDU‚"¢&Æö6¶–æuö6öFRÒæW‡B€¢€¢&V6öà¢f÷"&V6öâ–â‚%$ô¤T5Eô5tEô4ôädÄ”5B"Â%4õU$4Uõ44õUõTäUD„õ$•¤TB"¢–b&V6öâ–âÆö6Å÷&V6öç0¢’À¢æöæRÀ¢¢–b&Æö6¶–æuö6öFR—2æöæS ¢&Æö6¶–æuö6öFRÒæW‡B€¢€¢&V6öà¢f÷"&V6öâ–â€¢%tõ$´U%ôTÄ”t”$”Ä•E•õ$UT•$TB"À¢$Äô4ÅôõU$D”ôåôTÄ”t”$”Ä•E•õ$UT•$TB"À¢%tõ$´U%õD4µôtDUõEUÄUô”ådÄ”B"À¢$Äô4ÅõD4µôtDUõEUÄUô”ådÄ”B"À¢¢–b&V6öâ–â²§v÷&¶W%÷&V6öç2Â¦Æö6Å÷&V6öç5Ð¢’À¢æöæRÀ¢¢–b–çWEöÖöFRÓÒ&6ö×ÆWFR"æBæ÷B–ç7G'V7F–öåöw&VW2æB&Æö6¶–æuö6öFR—2æöæS ¢&Æö6¶–æuö6öFRÒ%D4µô”åUEô”å5E%T5D”ôåôÔ•4ÔD4‚ ¢–bW†V7WF–öå÷&WVW7FVBæB&Æö6¶–æuö6öFR—2æöæRæBF—7÷6—F–öåöW'&÷# ¢&Æö6¶–æuö6öFRÒF—7÷6—F–öåöW'&÷ ¢–b€¢W†V7WF–öå÷&WVW7FV@¢æB&Æö6¶–æuö6öFR—2æöæP¢æBW†V7WF–öå÷&WVW7Eö–B—2æöæP¢“ ¢&Æö6¶–æuö6öFRÒ$U„T5UD”ôåõ$UTU5Eô”Eõ$UT•$TB ¢–bçF–w&f—G•÷6VÆV7FVBæB&Æö6¶–æuö6öFR—2æöæRæBæ÷BçF–w&f—G•ö&–æF–æw5÷fÆ–C ¢&Æö6¶–æuö6öFRÒ$åD”u$d•E•ô$”äD”äu5ô”ådÄ”B ¢&÷f–FVEö–ç7G'V7F–öâÒW†7Eö–çWBævWB‚&–ç7G'V7F–öâ"¢&WV—&VÖVçG5ö6ö×ÆWFRÒ÷F6µö–çWE÷&WV—&VÖVçG5ö6ö×ÆWFR€¢W†7Eö–çWBÂ&WV—&VE÷F6µöf–VÆG0¢¢–bW†V7WF–öå÷&WVW7FVBæB&Æö6¶–æuö6öFR—2æöæS ¢–b–çWEöÖöFRÒ&6ö×ÆWFR# ¢&Æö6¶–æuö6öFRÒ%D4µô”åUEõ$UT•$TB ¢VÆ–bæ÷B—6–ç7Fæ6R‡&÷f–FVEö–ç7G'V7F–öâÂ7G"’÷"æ÷B&÷f–FVEö–ç7G'V7F–öâç7G&—‚“ ¢&Æö6¶–æuö6öFRÒ%D4µô”åUEôd”TÄE5ô”ä4ôÕÄUDR ¢VÆ–bæ÷B–ç7G'V7F–öåöw&VW3 ¢&Æö6¶–æuö6öFRÒ%D4µô”åUEô”å5E%T5D”ôåôÔ•4ÔD4‚ ¢VÆ–bæ÷B&WV—&VÖVçG5ö6ö×ÆWFS ¢&Æö6¶–æuö6öFRÒ%D4µô”åUEôd”TÄE5ô”ä4ôÕÄUDR ¢&W6öÇfVEö–çWEöÖöFRÒ€¢&6ö×ÆWFR ¢–b–çWEöÖöFRÓÒ&6ö×ÆWFR ¢æB–ç7G'V7F–öåöw&VW0¢æB&WV—&VÖVçG5ö6ö×ÆWFP¢VÇ6R&6öç6W'fF—fUö–ç7G'V7F–öåööæÇ’ ¢¢–bW†V7WF–öå÷&WVW7FVBæB&Æö6¶–æuö6öFS ¢7W÷'E÷v÷&¶W'2ÒµÐ¢Æö6ÅöW†V7WF–öâÒöf–Åö6Æ÷6VEöÆö6ÅöW†V7WF–öâ†Æö6ÅöW†V7WF–öâ¢&V6öç2Ò°¢$4ôDU…õ4ôÅôõ$4„U5E$Dõ" ¢–b7W÷'E÷v÷&¶W'2÷"Æö6ÅöW†V7WF–öâævWB‚&FÖ—GFVB"¢VÇ6R$4ôDU…õ4ôÅôDTdTÅB ¢Ð¢–b6VÆV7FVE÷'VÆS ¢&V6öç2æVæB‚$4$”Ä•E•õ%TÄUôÔD4‚"¢&V6öç2æW‡FVæB‡6VÆV7FVE÷'VÆRævWB‚'&V6öåö6öFW2"ÂµÒ’¢VÇ6S ¢&V6öç2æVæB‚$äõôU„5Eô4$”Ä•E•ôÔD4‚"¢–bæ÷&ÖÆ—¦R†Öæ–fW7BævWB‚&g&W6†æW75÷7FGW2"’’æ÷B–âe$U4…õ5DDU3 ¢&V6öç2æVæB‚$4$”Ä•E•õ4ä4„õEõ5DÄR"¢&V6öç2æW‡FVæB‡v÷&¶W%÷&V6öç2¢&V6öç2æW‡FVæB†Æö6Å÷&V6öç2¢&V6öç2æW‡FVæB‡&÷WFUöwV&E÷&V6öç2¢&V6öç2æW‡FVæB†6&–Æ—G•öfÆÆ&6µ÷&V6öç2¢–b&Æö6¶–æuö6öFRæB&Æö6¶–æuö6öFRæ÷B–â&V6öç3 ¢&V6öç2æVæB†&Æö6¶–æuö6öFR¢'V–ÆEö&w2Ò°¢''VÆR#¢6VÆV7FVE÷'VÆRÀ¢'&–Ö'’#¢6VÆV7FVE÷&–Ö'’À¢'7W÷'G2#¢6VÆV7FVE÷7W÷'G2À¢'7W÷'E÷v÷&¶W'2#¢7W÷'E÷v÷&¶W'2À¢&Æö6ÅöW†V7WF–öâ#¢Æö6ÅöW†V7WF–öâÀ¢&W†V7WF–öåöF—7÷6—F–öâ#¢W†V7WF–öåöF—7÷6—F–öâÀ¢&W†V7WF–öå÷&WVW7Eö–B#¢€¢W†V7WF–öå÷&WVW7Eö–B–bW†V7WF–öå÷&WVW7FVBVÇ6RæöæP¢’À¢'F6µ÷FW‡E÷6†#Sb#¢6ö×WFU÷F6µ÷FW‡E÷6†#Sb†&÷VæFVE÷FW‡B’À¢'F6µö–çWE÷6†#Sb#¢6ö×WFU÷F6µö–çWE÷6†#Sb†W†7Eö–çWB’À¢'F6µö–çWEöÖöFR#¢&W6öÇfVEö–çWEöÖöFRÀ¢'F6µöf–ævW'&–çB#¢÷F6µöf–ævW'&–çB‡&ö×BÂ6Æ76–f–6F–öâ’À¢'v÷&¶W%öW†V7WF–öå÷&WVW7FVB#¢W†V7WF–öå÷&WVW7FVBÀ¢'&V6öåö6öFW2#¢&V6öç2À¢&6&–Æ—G•öfÆÆ&6·2#¢6&–Æ—G•öfÆÆ&6·2À¢&Öæ–fW7B#¢Öæ–fW7BÀ¢'öÆ–7’#¢öÆ–7’À¢Ð¢WF†÷&—G•ö—77V&ÆRÒ÷&÷WFUöWF†÷&—G•ö—77V&ÆR†Öæ–fW7BÂöÆ–7’¢7–çF†WF–5öWF†÷&—G’Ò÷7–çF†WF–5öWF†÷&—G•ö–çWB†Öæ–fW7B’æB÷7–çF†WF–5öWF†÷&—G•ö–çWB€¢öÆ–7¢¢–bæ÷BWF†÷&—G•ö—77V&ÆRæBæ÷B7–çF†WF–5öWF†÷&—G“ ¢f–ÆVEö&w2ÒF–7B†'V–ÆEö&w2¢f–ÆVEö&w5²'7W÷'E÷v÷&¶W'2%ÒÒµÐ¢f–ÆVEö&w5²&Æö6ÅöW†V7WF–öâ%ÒÒöf–Åö6Æ÷6VEöÆö6ÅöW†V7WF–öâ†Æö6ÅöW†V7WF–öâ¢f–ÆVEö&w5²'&V6öåö6öFW2%ÒÒÆ—7B€¢F–7Bæg&öÖ¶W—2€¢°¢$4ôDU…õ4ôÅôDTdTÅB"À¢¢‡&V6öâf÷"&V6öâ–â&V6öç2–b&V6öâÒ$4ôDU…õ4ôÅôõ$4„U5E$Dõ""’À¢$UD„õ$•E•õTäd”Ä$ÄR"À¢Ð¢¢¢&WGW&âö'V–ÆEöFV6—6–öâ€¢¢¦f–ÆVEö&w2À¢—77Væ6U÷7FGW3Ò&f–ÆVB"À¢—77Væ6Uöf–ÇW&Uö6öFSÒ$UD„õ$•E•õTäd”Ä$ÄR"À¢¢FV6—6–öâÒö'V–ÆEöFV6—6–öâ€¢¢¦'V–ÆEö&w2À¢—77Væ6U÷7FGW3Ò'&Vv—7FW&VB"À¢—77Væ6Uöf–ÇW&Uö6öFSÖ&Æö6¶–æuö6öFRÀ¢¢G'“ ¢ö—77VU÷&÷WFUöFV6—6–öâ†FV6—6–öâ¢&WGW&âFV6—6–öà¢W†6WB&÷WFU&Vv—7G'”W'&÷# ¢f–ÆVEö&w2ÒF–7B†'V–ÆEö&w2¢f–ÆVEö&w5²'7W÷'E÷v÷&¶W'2%ÒÒµÐ¢f–ÆVEö&w5²&Æö6ÅöW†V7WF–öâ%ÒÒöf–Åö6Æ÷6VEöÆö6ÅöW†V7WF–öâ†Æö6ÅöW†V7WF–öâ¢f–ÆVE÷&V6öç2Ò°¢&V6öà¢f÷"&V6öâ–â&V6öç0¢–b&V6öâÒ$4ôDU…õ4ôÅôõ$4„U5E$Dõ" ¢Ð¢f–ÆVEö&w5²'&V6öåö6öFW2%ÒÒ°¢$4ôDU…õ4ôÅôDTdTÅB"À¢¦f–ÆVE÷&V6öç2À¢%$õUDUõ$Tt•5E%•õTäd”Ä$ÄR"À¢Ð¢&WGW&âö'V–ÆEöFV6—6–öâ€¢¢¦f–ÆVEö&w2À¢—77Væ6U÷7FGW3Ò&f–ÆVB"À¢—77Væ6Uöf–ÇW&Uö6öFSÒ%$õUDUõ$Tt•5E%•õTäd”Ä$ÄR"À¢  ¦FVbVç7W&Uö–æFW‚†f÷&6S¢&ööÂÒfÇ6RÂÖ…övUö†÷W'3¢–çBÒ#B’ÓâF–7E·7G"Âç•Ó ¢""$6ö×F–&–Æ—G’’âF†R6æöæ–6ÂÖæ–fW7B—2&VBF—&V7FÇ’ÂæWfW"66†VBâ""  ¢FVÂf÷&6RÂÖ…övUö†÷W'0¢&WGW&âÆöEö7F—fUö6&–Æ—F–W2‚  ¦FVb'V–ÆEö–æFW‚‚’ÓâF–7E·7G"Âç•Ó ¢&WGW&âÆöEö7F—fUö6&–Æ—F–W2‚  ¦FVb—5÷6W76–öåööæÇ•ö6æF–FFR†VçG'“¢F–7E·7G"Âç•Ò’Óâ&ööÃ ¢FVÂVçG'¢&WGW&âfÇ6P  ¦FVbVW'•ö–æFW‚€¢&ö×C¢7G"À¢Æ–Ö—C¢–çBÒRÀ¢–æ6ÇVFUö6æF–FFW3¢&ööÂÂæöæRÒæöæRÀ¢&–Ö'•öfÖ–Æ–W3¢ö&¦V7BÒæöæRÀ¢7W÷'F–æuöfÖ–Æ–W3¢ö&¦V7BÒæöæRÀ¢6÷W&6U÷FööÅ÷&WV—&VÖVçG3¢ö&¦V7BÒæöæRÀ¢FVæ–VEöfÖ–Æ–W3¢ö&¦V7BÒæöæRÀ¢6æF–FFU÷f—6–&–Æ—G“¢7G"Ò&7F—fUööæÇ’"À¢’ÓâÆ—7E¶F–7E·7G"Âç•ÕÓ ¢""%6V&6‚7F—fRVçG&–W2öæÇ’âÆVv7’6æF–FFR7v—F6†W2&R–çFVçF–öæÆÇ’–væ÷&VBâ""  ¢FVÂ–æ6ÇVFUö6æF–FFW2Â6æF–FFU÷f—6–&–Æ—G¢Öæ–fW7BÒÆöEö7F—fUö6&–Æ—F–W2‚¢&ö×E÷Fö¶Vç2ÒFö¶Væ—¦R‡&ö×B¢&WVW7FVEöfÖ–Æ–W2Ò°¢æ÷&ÖÆ—¦R†—FVÒ’ç&WÆ6R‚"Ò"Â%ò"¢f÷"—FVÒ–âö5öÆ—7B‡&–Ö'•öfÖ–Æ–W2’²ö5öÆ—7B‡7W÷'F–æuöfÖ–Æ–W2¢Ð¢FVæ–VBÒ°¢æ÷&ÖÆ—¦R†—FVÒ’ç&WÆ6R‚"Ò"Â%ò"’f÷"—FVÒ–âö5öÆ—7B†FVæ–VEöfÖ–Æ–W2¢Ð¢&WVW7FVE÷FööÇ2Ò°¢æ÷&ÖÆ—¦R†—FVÒ’f÷"—FVÒ–âö5öÆ—7B‡6÷W&6U÷FööÅ÷&WV—&VÖVçG2¢Ð¢66÷&VC¢Æ—7E·GWÆU¶–çBÂF–7E·7G"Âç•ÕÕÒÒµÐ¢f÷"VçG'’–âÖæ–fW7BævWB‚&VçG&–W2"ÂµÒ“ ¢fÖ–Æ–W2Ò6WB†VçG'’ævWB‚&fÖ–Æ–W2"ÂµÒ’¢–bFVæ–VBæBfÖ–Æ–W2bFVæ–VC ¢6öçF–çVP¢VçG'•÷Fö¶Vç2ÒFö¶Væ—¦R€¢""æ¦ö–â€¢°¢7G"†VçG'’ævWB‚&–B"Â""’’À¢7G"†VçG'’ævWB‚&æÖR"Â""’’À¢7G"†VçG'’ævWB‚'&÷f–FW""Â""’’À¢7G"†VçG'’ævWB‚&¶–æB"Â""’’À¢""æ¦ö–â†fÖ–Æ–W2’À¢7G"†VçG'’ævWB‚&FW67&—F–öâ"Â""’’À¢Ð¢¢¢66÷&RÒÆVâ‡&ö×E÷Fö¶Vç2bVçG'•÷Fö¶Vç2’¢@¢66÷&R³ÒÆVâ‡&WVW7FVEöfÖ–Æ–W2bfÖ–Æ–W2’¢ ¢–bæ÷&ÖÆ—¦R†VçG'’ævWB‚&æÖR"’’–â&WVW7FVE÷FööÇ2÷"æ÷&ÖÆ—¦R€¢VçG'’ævWB‚&–B"¢’–â&WVW7FVE÷FööÇ3 ¢66÷&R³Ò# ¢–b66÷&S ¢66÷&VBæVæB‚‡66÷&RÂVçG'’’¢66÷&VBç6÷'B†¶W“ÖÆÖ&F—FVÓ¢‚Ö—FVÕ³ÒÂæ÷&ÖÆ—¦R†—FVÕ³ÒævWB‚&–B"’’’¢&WGW&â°¢VçG'’f÷"òÂVçG'’–â66÷&VE³¢Ö‚ƒÂÖ–â†–çB†Æ–Ö—B’Â‚’•Ð¢Ð 