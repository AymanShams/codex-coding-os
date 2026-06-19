#!/usr/bin/env python3
"""Portable capability index for optional Codex prompt-routing hints.

The index is intentionally conservative. It can suggest likely capabilities, but
the catalogue-router skill remains the source that decides whether a capability
is actually relevant to the user's task.
"""

from __future__ import annotations

import hashlib
import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None


PACK_ROOT = Path(
    os.environ.get("CODEX_CODING_OS_ROOT", Path(__file__).resolve().parents[2])
)
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
AGENTS_HOME = Path(os.environ.get("AGENTS_HOME", Path.home() / ".agents"))
CONFIG_PATH = CODEX_HOME / "config.toml"
CATALOGUE_PATH = Path(
    os.environ.get(
        "CODEX_CAPABILITY_CATALOGUE",
        str(
            PACK_ROOT
            / ".agents"
            / "skills"
            / "catalogue-router"
            / "references"
            / "capability-catalogue.md"
        ),
    )
)
CACHE_DIR = Path(
    os.environ.get(
        "CODEX_CAPABILITY_INDEX_DIR",
        str(CODEX_HOME / "coding-os" / "capability-index"),
    )
)
INDEX_PATH = CACHE_DIR / "capability-index.json"
SUMMARY_PATH = CACHE_DIR / "capability-index-summary.md"
REGISTRY_PATH = Path(
    os.environ.get("CODEX_CAPABILITY_REGISTRY", str(CACHE_DIR / "canonical-registry.csv"))
)
PACK_REGISTRY_PATH = PACK_ROOT / "capability-index" / "canonical-registry.sample.csv"

STOP_WORDS = {
    "a",
    "about",
    "all",
    "an",
    "and",
    "are",
    "can",
    "candidate",
    "capability",
    "check",
    "do",
    "for",
    "from",
    "how",
    "i",
    "in",
    "instead",
    "is",
    "it",
    "me",
    "missing",
    "my",
    "new",
    "of",
    "on",
    "or",
    "our",
    "please",
    "recommend",
    "that",
    "the",
    "these",
    "those",
    "this",
    "to",
    "use",
    "we",
    "when",
    "where",
    "what",
    "why",
    "while",
    "with",
    "you",
}

GENERIC_MATCH_TERMS = STOP_WORDS | {
    "after",
    "approach",
    "before",
    "capabilities",
    "created",
    "creating",
    "edit",
    "editing",
    "edits",
    "file",
    "files",
    "fix",
    "issue",
    "issues",
    "changed",
    "keep",
    "keeping",
    "log",
    "made",
    "make",
    "plugin",
    "plugins",
    "problem",
    "problems",
    "previous",
    "read",
    "selection",
    "skill",
    "skills",
    "solution",
    "summarize",
    "summary",
    "system",
    "thing",
    "tool",
    "tools",
    "workflow",
    "workflows",
}

CAPABILITY_ROUTING_TERMS = {
    "capability",
    "catalog",
    "catalogue",
    "choose",
    "mcp",
    "plugin",
    "plugins",
    "route",
    "router",
    "routing",
    "skill",
    "skills",
    "tool",
    "tools",
}
SKILL_AUTHORING_TERMS = {
    "benchmark",
    "create",
    "creating",
    "edit",
    "editing",
    "evaluate",
    "existing",
    "improve",
    "modify",
    "skill",
    "skills",
    "test",
    "verify",
}
SKILL_AUTHORING_ACTION_TERMS = SKILL_AUTHORING_TERMS - {"skill", "skills"}

DOCUMENT_TERMS = {
    "docx",
    "word",
    "word document",
    "document file",
    "uploaded document",
    "attached document",
}
PRESENTATION_TERMS = {
    "deck",
    "ppt",
    "pptx",
    "powerpoint",
    "presentation",
    "presentations",
    "slide",
    "slides",
}
SPREADSHEET_TERMS = {
    "excel",
    "sheet",
    "sheets",
    "spreadsheet",
    "spreadsheets",
    "workbook",
    "xlsx",
}
PDF_TERMS = {"pdf", "acrobat"}
BROWSER_TERMS = {
    "browser",
    "console",
    "devserver",
    "dom",
    "localhost",
    "screenshot",
    "visual",
    "webpage",
}
REACT_NATIVE_TERMS = {"android", "expo", "ios", "mobile", "native", "react-native"}
ZOOM_TERMS = {"meeting", "recording", "transcript", "webinar", "zoom"}
PREMORTEM_TERMS = {
    "failure",
    "failure-first",
    "premortem",
    "pre-mortem",
    "risk",
    "scenario",
    "stress-test",
}
FINANCE_TERMS = {
    "banking",
    "burn",
    "cash",
    "cfo",
    "equity",
    "finance",
    "forecast",
    "investment",
    "market",
    "model",
    "runway",
    "startup",
    "valuation",
}
CLI_TERMS = {
    "argparse",
    "bash",
    "cli",
    "command-line",
    "powershell",
    "script",
    "shell",
    "terminal",
}
GRILL_TERMS = {
    "ask me",
    "grill",
    "hard questions",
    "interview",
    "questions",
}
NEW_PROJECT_DOC_TERMS = {
    "app flow",
    "brief",
    "first project",
    "implementation plan",
    "new project",
    "prd",
    "project documentation",
    "project kickoff",
    "repo docs",
    "tdd",
    "tech stack",
}
TECHNICAL_DOC_TERMS = {
    "agents.md",
    "architecture doc",
    "documentation",
    "docs",
    "handoff",
    "readme",
    "repo docs",
    "technical documentation",
}
PRODUCT_DOC_TERMS = NEW_PROJECT_DOC_TERMS | {
    "customer journey",
    "feature",
    "product",
    "product strategy",
    "requirements",
    "spec",
    "user journey",
    "working backwards",
}
BUSINESS_STRATEGY_TERMS = {
    "board",
    "business",
    "competitor",
    "go-to-market",
    "growth",
    "gtm",
    "market",
    "pricing",
    "sales",
    "stakeholder",
    "startup",
    "strategy",
}
SALES_TERMS = {
    "account",
    "buyer",
    "deal",
    "lead",
    "pipeline",
    "prospect",
    "sales",
}
CONTRACT_TERMS = {
    "agreement",
    "clause",
    "contract",
    "handover",
    "legal",
    "msa",
    "sla",
    "termination",
}
QUANT_TERMS = {
    "calculate",
    "forecast",
    "formula",
    "model",
    "numbers",
    "roi",
    "sensitivity",
    "statistic",
    "unit economics",
}
ARTIFACT_SYSTEM_TERMS = {
    "artifact",
    "controlled doc",
    "documentation",
    "docs",
    "governance",
    "policy",
    "sop",
    "template",
    "validate",
    "validation",
}
DOSSIER_TERMS = {
    "briefing file",
    "dossier",
    "due diligence",
    "evidence pack",
    "fact file",
}
DESIGN_ARTIFACT_TERMS = {
    "artifact",
    "design",
    "figma",
    "flow",
    "image",
    "prototype",
    "screen",
    "ui",
    "ux",
    "visual",
    "wireframe",
}
IMAGE_GENERATION_TERMS = {
    "generate image",
    "image",
    "logo",
    "picture",
    "render",
    "visual",
}
CODING_DISCIPLINE_TERMS = {
    "blocked",
    "bug",
    "bugfix",
    "build",
    "check",
    "checks",
    "code",
    "coding",
    "commit",
    "debug",
    "error",
    "existing",
    "failing",
    "feature",
    "fix",
    "generated",
    "health",
    "healthcheck",
    "implementation",
    "merge",
    "pr",
    "pull",
    "push",
    "repo",
    "repository",
    "refactor",
    "run",
    "scheduled",
    "test",
    "tests",
    "winerror",
}
PROJECT_CONTINUITY_TERMS = {
    "active-slice",
    "blocked",
    "blocker",
    "current-state",
    "handoff",
    "manifest",
    "resume",
    "session",
}
GITHUB_TERMS = {
    "admin merge",
    "admin-merge",
    "branch protection",
    "checks",
    "ci",
    "github",
    "main",
    "merge",
    "merge rules",
    "pr",
    "pull request",
    "review protection",
    "status check",
}
WORKTREE_TERMS = {
    "agent",
    "agents",
    "branch",
    "lane",
    "lanes",
    "parallel",
    "worktree",
    "worktrees",
}
REACT_WEB_TERMS = {
    "component",
    "frontend",
    "jsx",
    "next",
    "next.js",
    "react",
    "tsx",
    "ui",
}
SECURITY_TERMS = {
    "auth",
    "authorization",
    "bypass",
    "privacy",
    "secret",
    "secrets",
    "security",
    "server-side",
    "threat",
    "vulnerability",
}
FORMAL_REVIEW_TERMS = {
    "audit the",
    "audit this",
    "challenge",
    "critique",
    "review",
    "review this",
    "stress test",
}
NEGATED_FORMAL_REVIEW_PHRASES = {
    "do not critique",
    "do not review",
    "do not validate",
    "don't critique",
    "don't review",
    "don't validate",
    "not critique",
    "not review",
}
CANDIDATE_TERMS = {
    "add",
    "candidate",
    "capability",
    "install",
    "mcp",
    "plugin",
    "recommend",
    "repo",
    "repository",
    "tool",
}
CANDIDATE_ACTION_TERMS = {"add", "candidate", "install", "recommend"}
CANDIDATE_OBJECT_TERMS = {"capability", "mcp", "plugin", "repo", "repository", "tool", "tools"}
BROAD_CANDIDATE_DOMAIN_TERMS = {"automation", "material", "session", "support"}

STATUS_PRIORITY = {
    "active-pack": 11,
    "active-local": 10,
    "active-user": 9,
    "active-plugin": 8,
    "active-mcp": 7,
    "catalogue-route": 6,
    "project-local-pilot": 2,
    "install-or-run-candidate": 2,
    "conditional-future-skill": 1,
    "reference-only": 0,
}


SESSION_ONLY_CANDIDATE_STATUSES = {
    "catalogue-plugin-available",
    "conditional-future-skill",
    "install-or-run-candidate",
    "project-local-pilot",
    "reference-only",
}
SESSION_ONLY_CANDIDATE_ROLES = {
    "project-local-only",
    "reference-only",
}
ACTIVE_AUTOMATIC_STATUSES = {
    "active-local",
    "active-mcp",
    "active-pack",
    "active-plugin",
    "active-user",
    "catalogue-plugin-active",
    "catalogue-route",
    "session-available-plugin",
}


def is_session_only_candidate(entry: dict) -> bool:
    if entry.get("kind") != "candidate" and entry.get("status", "") in ACTIVE_AUTOMATIC_STATUSES:
        return False
    return (
        entry.get("kind") == "candidate"
        or entry.get("status", "") in SESSION_ONLY_CANDIDATE_STATUSES
        or entry.get("routing_role", "") in SESSION_ONLY_CANDIDATE_ROLES
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def normalize_family(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def normalize_family_values(value) -> set[str]:
    if not value:
        return set()
    if isinstance(value, (frozenset, list, tuple, set)):
        parts = value
    else:
        parts = re.split(r"[,|]+", str(value))
    return {normalize_family(str(part).strip()) for part in parts if normalize_family(str(part).strip())}


def join_family_values(values: set[str]) -> str:
    return ", ".join(sorted(value for value in values if value))


def registry_key(kind: str, name: str) -> tuple[str, str]:
    return (kind or "", normalize(name or ""))


def load_registry_file(path: Path) -> dict[tuple[str, str], dict]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return {}
    registry = {}
    for row in rows:
        key = registry_key(row.get("kind", ""), row.get("name", ""))
        if key[1]:
            registry[key] = row
    return registry


def load_canonical_registry() -> dict[tuple[str, str], dict]:
    registry = load_registry_file(PACK_REGISTRY_PATH)
    registry.update(load_registry_file(REGISTRY_PATH))
    return registry


def apply_registry_metadata(entries: list[dict], registry: dict[tuple[str, str], dict]) -> None:
    for entry in entries:
        row = registry.get(registry_key(entry.get("kind", ""), entry.get("name", "")))
        if not row:
            continue
        primary = row.get("primary_families", "")
        support = row.get("support_families", "")
        all_families = row.get("all_families", "") or join_family_values(
            normalize_family_values(primary) | normalize_family_values(support)
        )
        entry["primary_families"] = primary
        entry["support_families"] = support
        entry["all_families"] = all_families
        entry["routing_role"] = row.get("routing_role", "")
        entry["support_bias"] = row.get("support_bias", "")
        entry["installed_state"] = row.get("installed_state", "")
        entry["registry_decision"] = row.get("decision", "")


def primary_families_for_entry(entry: dict) -> set[str]:
    explicit = normalize_family_values(entry.get("primary_families"))
    if explicit:
        return explicit
    return normalize_family_values(entry.get("family") or entry.get("families"))


def support_families_for_entry(entry: dict) -> set[str]:
    return normalize_family_values(entry.get("support_families"))


def all_families_for_entry(entry: dict) -> set[str]:
    return (
        normalize_family_values(entry.get("all_families"))
        | primary_families_for_entry(entry)
        | support_families_for_entry(entry)
    )


def load_text(path: Path, max_chars: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text if max_chars is None else text[:max_chars]


def clean_scalar(value: str) -> str:
    return value.strip().strip("\"'").strip()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def load_config() -> dict:
    if tomllib is None or not CONFIG_PATH.exists():
        return {}
    try:
        with CONFIG_PATH.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def configured_state(config: dict) -> tuple[dict[str, bool], set[str], dict[str, bool]]:
    plugin_states = {}
    for plugin_id, plugin_config in config.get("plugins", {}).items():
        if isinstance(plugin_config, dict):
            plugin_states[plugin_id] = bool(plugin_config.get("enabled", True))

    disabled_skill_paths = set()
    for item in config.get("skills", {}).get("config", []):
        if isinstance(item, dict) and item.get("enabled") is False and item.get("path"):
            disabled_skill_paths.add(str(Path(item["path"]).resolve()).lower())

    mcp_states = {}
    for name, mcp_config in config.get("mcp_servers", {}).items():
        if isinstance(mcp_config, dict):
            mcp_states[name] = bool(mcp_config.get("enabled", True))
    return plugin_states, disabled_skill_paths, mcp_states


def parse_skill_frontmatter(path: Path) -> dict | None:
    text = load_text(path, max_chars=12000)
    name_match = re.search(r"(?m)^name:\s*(.+)$", text)
    desc_match = re.search(r"(?m)^description:\s*(.+)$", text)
    if not name_match:
        return None
    description = clean_scalar(desc_match.group(1)) if desc_match else ""
    if description in {"|", ">"} and desc_match:
        block = []
        for line in text[desc_match.end() :].splitlines():
            if line.strip() == "---":
                break
            if line and not line[0].isspace():
                break
            if line.strip():
                block.append(line.strip())
        description = " ".join(block)
    return {"name": clean_scalar(name_match.group(1)), "description": description}


def plugin_identity(path: Path) -> tuple[str | None, str | None]:
    parts = list(path.parts)
    lowered = [part.lower() for part in parts]
    if "cache" not in lowered:
        return None, None
    index = lowered.index("cache")
    if len(parts) <= index + 2:
        return None, None
    return parts[index + 2], parts[index + 1]


def classify_skill(
    path: Path, plugin_states: dict[str, bool], disabled_skill_paths: set[str]
) -> str:
    resolved = str(path.resolve()).lower()
    lowered = resolved.replace("/", "\\")
    if resolved in disabled_skill_paths:
        return "disabled-skill"
    if "\\plugin-install-" in lowered:
        return "stale-cache"

    pack_skill_root = str((PACK_ROOT / ".agents" / "skills").resolve()).lower()
    if resolved.startswith(pack_skill_root):
        return "active-pack"

    plugin, marketplace = plugin_identity(path)
    if plugin and marketplace:
        plugin_id = f"{plugin}@{marketplace}"
        if plugin_id in plugin_states:
            return "active-plugin" if plugin_states[plugin_id] else "disabled-plugin"
        return "cached-unconfigured"

    if "\\.agents\\skills\\" in lowered:
        return "active-user"
    if "\\.codex\\skills\\" in lowered:
        return "active-local"
    return "unknown"


def parse_table_rows(section: str) -> list[list[str]]:
    rows = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if not cells or cells[0].lower() in {"task", "skill", "plugin", "candidate"}:
            continue
        if cells[0].startswith("---"):
            continue
        rows.append(cells)
    return rows


def markdown_section(text: str, heading: str, level: int = 2) -> str:
    marker = "#" * level + " " + heading
    start = text.find(marker)
    if start < 0:
        return ""
    pattern = re.compile(rf"(?m)^#{{1,{level}}} ")
    match = pattern.search(text, start + len(marker))
    end = match.start() if match else len(text)
    return text[start:end]


def parse_catalogue() -> list[dict]:
    text = load_text(CATALOGUE_PATH)
    entries = []

    fast_router = markdown_section(text, "Fast Router", 2)
    for cells in parse_table_rows(fast_router):
        if len(cells) >= 3:
            entries.append(
                {
                    "kind": "route",
                    "name": cells[2],
                    "description": cells[0],
                    "status": "catalogue-route",
                    "source": str(CATALOGUE_PATH),
                }
            )

    candidate_text = markdown_section(text, "Candidate Backlog", 2)
    for cells in parse_table_rows(candidate_text):
        if len(cells) >= 2:
            entries.append(
                {
                    "kind": "candidate",
                    "name": cells[0],
                    "description": " ".join(cells[1:]),
                    "status": "reference-only",
                    "source": str(CATALOGUE_PATH),
                }
            )
    return entries


def skill_roots() -> list[Path]:
    return [
        PACK_ROOT / ".agents" / "skills",
        AGENTS_HOME / "skills",
        CODEX_HOME / "skills",
        CODEX_HOME / "plugins" / "cache",
    ]


def build_index() -> dict:
    plugin_states, disabled_skill_paths, mcp_states = configured_state(load_config())
    entries = []
    local_groups = defaultdict(list)

    for root in skill_roots():
        if not root.exists():
            continue
        for path in root.rglob("SKILL.md"):
            metadata = parse_skill_frontmatter(path)
            if not metadata:
                continue
            status = classify_skill(path, plugin_states, disabled_skill_paths)
            entry = {
                "kind": "skill",
                "name": metadata["name"],
                "description": metadata["description"],
                "status": status,
                "source": str(path),
                "sha256": file_hash(path),
            }
            entries.append(entry)
            if status in {"active-pack", "active-local", "active-user"}:
                local_groups[metadata["name"]].append(entry)

    entries.extend(parse_catalogue())

    for name, enabled in mcp_states.items():
        entries.append(
            {
                "kind": "mcp",
                "name": name,
                "description": f"Configured Model Context Protocol server: {name}",
                "status": "active-mcp" if enabled else "disabled-mcp",
                "source": str(CONFIG_PATH),
            }
        )

    registry = load_canonical_registry()
    apply_registry_metadata(entries, registry)

    exact_local_duplicates = []
    active_exact_local_duplicates = []
    for name, group in local_groups.items():
        hashes = {item["sha256"] for item in group if item["sha256"]}
        if len(group) > 1 and len(hashes) == 1:
            exact_local_duplicates.append(name)
            active_group = [item for item in group if item["status"] != "disabled-skill"]
            if len(active_group) > 1:
                active_exact_local_duplicates.append(name)

    counts = defaultdict(int)
    for entry in entries:
        counts[entry["status"]] += 1

    summary = {
        "skill_files": sum(1 for entry in entries if entry["kind"] == "skill"),
        "configured_mcps": len(mcp_states),
        "disabled_skills": len(disabled_skill_paths),
        "exact_local_duplicate_names": sorted(exact_local_duplicates),
        "active_exact_local_duplicate_names": sorted(active_exact_local_duplicates),
        "status_counts": dict(sorted(counts.items())),
    }
    return {
        "generated_at": utc_now(),
        "sources": {
            "config": str(CONFIG_PATH),
            "catalogue": str(CATALOGUE_PATH),
            "canonical_registry": str(REGISTRY_PATH),
            "pack_registry": str(PACK_REGISTRY_PATH),
            "skill_roots": [str(root) for root in skill_roots()],
        },
        "summary": summary,
        "entries": entries,
    }


def load_index() -> dict:
    try:
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_index(index: dict) -> dict:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        previous = load_index()
        index["previous_summary"] = previous.get("summary", {}) if previous else {}

        temp_path = INDEX_PATH.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(index, indent=2, ensure_ascii=True), encoding="utf-8")
        temp_path.replace(INDEX_PATH)

        summary = index["summary"]
        lines = [
            "# Capability Index Summary",
            "",
            f"Generated: {index['generated_at']}",
            "",
            f"- Skill files indexed: {summary['skill_files']}",
            f"- Configured MCP servers: {summary['configured_mcps']}",
            f"- Disabled skill paths: {summary['disabled_skills']}",
            f"- Exact local duplicate names: {len(summary['exact_local_duplicate_names'])}",
            f"- Active exact local duplicate names: {len(summary['active_exact_local_duplicate_names'])}",
        ]
        SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        index["previous_summary"] = {}
        index["cache_error"] = f"{type(exc).__name__}: {exc}"
    return index


def ensure_index(force: bool = False, max_age_hours: int = 24) -> dict:
    if not force and INDEX_PATH.exists():
        age_seconds = datetime.now().timestamp() - INDEX_PATH.stat().st_mtime
        if age_seconds <= max_age_hours * 3600:
            index = load_index()
            if index:
                return index
    return write_index(build_index())


def tokenize(text: str) -> set[str]:
    normalized = text.lower().replace("-", " ").replace("_", " ").replace("/", " ")
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9+.]*", normalized)
        if len(token) > 1 and not token.isdigit() and token not in STOP_WORDS
    }


def has_term(prompt_lower: str, prompt_tokens: set[str], terms: set[str]) -> bool:
    for term in terms:
        if " " in term or "-" in term:
            if term in prompt_lower:
                return True
            continue
        if term in prompt_tokens:
            return True
    return False


def is_audit_log_context(prompt_lower: str) -> bool:
    return "audit log" in prompt_lower or "audit trail" in prompt_lower


def has_formal_review_request(prompt_lower: str, prompt_tokens: set[str]) -> bool:
    if any(phrase in prompt_lower for phrase in NEGATED_FORMAL_REVIEW_PHRASES):
        return False
    return has_term(prompt_lower, prompt_tokens, FORMAL_REVIEW_TERMS)


def wants_candidate_results(prompt_lower: str, prompt_tokens: set[str]) -> bool:
    if "candidate" in prompt_tokens or "install" in prompt_tokens:
        return True
    if prompt_tokens & CANDIDATE_ACTION_TERMS and prompt_tokens & CANDIDATE_OBJECT_TERMS:
        return True
    return False


def is_plugin_availability_context(prompt_lower: str, prompt_tokens: set[str]) -> bool:
    if not ({"plugin", "plugins"} & prompt_tokens):
        return False
    return (
        "available plugins" in prompt_lower
        or "plugins available" in prompt_lower
        or "available to install" in prompt_lower
        or "installable plugins" in prompt_lower
        or bool({"available", "installable", "list", "what", "which"} & prompt_tokens)
    )


def guarded_out(entry: dict, prompt_lower: str, prompt_tokens: set[str], allowed_families=None) -> bool:
    name = entry.get("name", "")
    normalized_name = normalize(name)
    name_lower = name.lower()
    kind = entry.get("kind", "")

    if is_audit_log_context(prompt_lower) and normalized_name in {
        "artifact-validation-workflow",
        "deep-critic",
        "ssot-auditor",
    }:
        return not has_formal_review_request(prompt_lower, prompt_tokens)
    if normalized_name == "deep-critic":
        return not has_formal_review_request(prompt_lower, prompt_tokens)
    if normalized_name == "cli-creator":
        return not has_term(prompt_lower, prompt_tokens, CLI_TERMS)
    if normalized_name in {"grill-me", "grill-with-docs"}:
        return not has_term(prompt_lower, prompt_tokens, GRILL_TERMS)
    if normalized_name == "new-project-documentation-system":
        if allowed_families and "new_project_doc" in allowed_families:
            return False
        return not has_term(prompt_lower, prompt_tokens, NEW_PROJECT_DOC_TERMS)
    if normalized_name == "technical-docs-pack":
        return not has_term(prompt_lower, prompt_tokens, TECHNICAL_DOC_TERMS)
    if normalized_name in {
        "create-prd",
        "customer-journey-map",
        "product-strategy",
        "working-backwards",
    }:
        return not has_term(prompt_lower, prompt_tokens, PRODUCT_DOC_TERMS)
    if normalized_name in {
        "board-update",
        "competitor-analysis",
        "gtm-strategy",
        "hormozi-growth-system",
        "investment-banking",
        "market-sizing",
        "pricing-strategy",
        "public-equity-investing",
        "stakeholder-map",
        "startup-context",
    }:
        return not has_term(prompt_lower, prompt_tokens, BUSINESS_STRATEGY_TERMS)
    if normalized_name in {"contract-review", "contract-reviewer"}:
        return not has_term(prompt_lower, prompt_tokens, CONTRACT_TERMS)
    if normalized_name == "quant-review":
        return not has_term(prompt_lower, prompt_tokens, QUANT_TERMS)
    if normalized_name == "suggest-sales-next-step":
        return not has_term(prompt_lower, prompt_tokens, SALES_TERMS)
    if normalized_name in {
        "artifact-system-designer",
        "artifact-validation-workflow",
        "ssot-auditor",
        "ssot-drafter",
    }:
        return not has_term(prompt_lower, prompt_tokens, ARTIFACT_SYSTEM_TERMS)
    if normalized_name == "dossier-builder":
        return not has_term(prompt_lower, prompt_tokens, DOSSIER_TERMS)
    if normalized_name == "codex-design-artifacts":
        return not has_term(prompt_lower, prompt_tokens, DESIGN_ARTIFACT_TERMS)
    if normalized_name == "imagegen":
        return not has_term(prompt_lower, prompt_tokens, IMAGE_GENERATION_TERMS)
    if normalized_name in {"doc", "docx", "documents", "document-skills"}:
        return not has_term(prompt_lower, prompt_tokens, DOCUMENT_TERMS)
    if "document-skills" in name_lower or name_lower == "document skills":
        return not has_term(prompt_lower, prompt_tokens, DOCUMENT_TERMS)
    if normalized_name in {"pptx", "presentations"} or "presentation" in name_lower:
        return not has_term(prompt_lower, prompt_tokens, PRESENTATION_TERMS)
    if normalized_name in {"xlsx", "spreadsheets"} or "spreadsheet" in name_lower:
        return not has_term(prompt_lower, prompt_tokens, SPREADSHEET_TERMS)
    if normalized_name == "pdf" or name_lower == "pdf":
        return not has_term(prompt_lower, prompt_tokens, PDF_TERMS)
    if normalized_name in {"agent-browser-verify", "browser-control-in-app-browser"}:
        return not has_term(prompt_lower, prompt_tokens, BROWSER_TERMS)
    if "react-native" in normalized_name:
        return not has_term(prompt_lower, prompt_tokens, REACT_NATIVE_TERMS)
    if normalized_name in {"start", "zoom", "choose-zoom-approach"} or "zoom" in normalized_name:
        return not has_term(prompt_lower, prompt_tokens, ZOOM_TERMS)
    if normalized_name in {"pre-mortem", "premortem"}:
        return not has_term(prompt_lower, prompt_tokens, PREMORTEM_TERMS)
    if normalized_name in {"charlie", "scenario-sensitivity-generator"}:
        return not has_term(prompt_lower, prompt_tokens, FINANCE_TERMS)
    if kind == "plugin" and name_lower in {"presentations", "document skills", "spreadsheets", "pdf"}:
        relevant_terms = DOCUMENT_TERMS | PRESENTATION_TERMS | SPREADSHEET_TERMS | PDF_TERMS
        return not has_term(prompt_lower, prompt_tokens, relevant_terms)
    return False


def query_index(
    prompt: str,
    limit: int = 5,
    include_candidates: bool | None = None,
    primary_families=None,
    supporting_families=None,
    source_tool_requirements=None,
    denied_families=None,
    candidate_visibility: str = "active_only",
) -> list[dict]:
    index = ensure_index()
    prompt_lower = prompt.lower()
    prompt_tokens = tokenize(prompt)
    plugin_availability_context = is_plugin_availability_context(prompt_lower, prompt_tokens)
    primary_families = normalize_family_values(primary_families)
    supporting_families = normalize_family_values(supporting_families)
    denied_families = normalize_family_values(denied_families)
    selected_families = set(primary_families or set()) | set(supporting_families or set())
    source_tool_names = {normalize(str(tool)) for tool in (source_tool_requirements or set())}
    if is_audit_log_context(prompt_lower):
        if not has_formal_review_request(prompt_lower, prompt_tokens):
            return []
        prompt_tokens = prompt_tokens - {"audit", "log", "trail"}
    if include_candidates is None:
        include_candidates = wants_candidate_results(prompt_lower, prompt_tokens)

    excluded_statuses = {
        "cached-unconfigured",
        "disabled-mcp",
        "disabled-plugin",
        "disabled-skill",
        "shadowed-cache",
        "skip-avoid",
        "stale-cache",
        "unknown",
    }
    excluded_roles = {"disabled", "remove-candidate"}
    scored = []
    for entry in index.get("entries", []):
        status = entry.get("status", "")
        kind = entry.get("kind", "")
        routing_role = entry.get("routing_role", "")
        session_only_candidate = is_session_only_candidate(entry)
        if status in excluded_statuses:
            continue
        if routing_role in excluded_roles or entry.get("support_bias") == "never-active":
            continue
        if session_only_candidate and not include_candidates:
            continue
        if kind == "catalogue-plugin" and not plugin_availability_context:
            continue
        if plugin_availability_context and kind == "skill" and normalize(entry.get("name", "")) != "catalogue-router":
            continue
        if candidate_visibility == "active_only" and routing_role in {"reference-only", "project-local-only"}:
            continue

        name = entry.get("name", "")
        description = entry.get("description", "")
        if denied_families and {"code", "code_orchestration"} & denied_families:
            if normalize(name) in {"gh-fix-ci", "github-gh-fix-ci", "github-yeet", "yeet"}:
                continue
        if guarded_out(entry, prompt_lower, prompt_tokens, allowed_families=selected_families):
            continue
        entry_primary_families = primary_families_for_entry(entry)
        entry_support_families = support_families_for_entry(entry)
        entry_all_families = all_families_for_entry(entry)
        if denied_families and entry_primary_families & denied_families and not (
            primary_families and entry_primary_families & (primary_families - denied_families)
        ):
            continue
        source_tool_match = kind == "mcp" and normalize(name) in source_tool_names
        primary_family_routing_match = bool(primary_families and entry_primary_families & primary_families)
        support_family_routing_match = bool(supporting_families and entry_support_families & supporting_families)
        fallback_support_match = bool(supporting_families and entry_primary_families & supporting_families)
        family_routing_match = primary_family_routing_match or support_family_routing_match or fallback_support_match
        default_support_match = bool(
            entry.get("support_bias") == "default-for-noncoding"
            and supporting_families
            and entry_all_families
            and entry_all_families & supporting_families
        )
        search_text = f"{name} {description} {kind} {status}".lower()
        entry_tokens = tokenize(search_text)
        overlap = prompt_tokens & entry_tokens
        strong_overlap = overlap - GENERIC_MATCH_TERMS
        name_in_prompt = bool(name and name.lower() in prompt_lower)
        normalized_name_tokens = tokenize(name.replace("-", " "))
        name_overlap = prompt_tokens & normalized_name_tokens
        full_name_match = bool(normalized_name_tokens and normalized_name_tokens <= prompt_tokens)
        if plugin_availability_context and kind in {"candidate", "catalogue-plugin", "plugin"}:
            domain_terms = (
                prompt_tokens
                - CAPABILITY_ROUTING_TERMS
                - CANDIDATE_TERMS
                - GENERIC_MATCH_TERMS
                - {"available", "install", "installable", "one", "should", "which"}
            )
            specific_domain_terms = domain_terms - BROAD_CANDIDATE_DOMAIN_TERMS
            required_domain_terms = specific_domain_terms or domain_terms
            if required_domain_terms and not (overlap & required_domain_terms) and not name_in_prompt:
                continue
        capability_routing_match = normalize(name) == "catalogue-router" and (
            len(prompt_tokens & CAPABILITY_ROUTING_TERMS) >= 2 or plugin_availability_context
        )
        coding_discipline_match = normalize(name) == "ai-coding-discipline" and len(
            prompt_tokens & CODING_DISCIPLINE_TERMS
        ) >= 2
        code_orchestration_match = (
            normalize(name) == "codex-coding-os-master"
            and primary_families
            and "code_orchestration" in primary_families
        )
        project_continuity_match = normalize(name) == "project-session-continuity" and (
            len(prompt_tokens & PROJECT_CONTINUITY_TERMS) >= 1
            or "health check" in prompt_lower
            or "stop if" in prompt_lower
        )
        github_match = normalize(name) in {
            "github",
            "gh-address-comments",
            "gh-fix-ci",
            "yeet",
        } and has_term(prompt_lower, prompt_tokens, GITHUB_TERMS)
        worktree_match = normalize(name) in {
            "using-git-worktrees",
            "dispatching-parallel-agents",
            "subagent-driven-development",
        } and has_term(prompt_lower, prompt_tokens, WORKTREE_TERMS)
        react_web_match = normalize(name) in {
            "frontend-app-builder",
            "frontend-testing-debugging",
            "react-best-practices",
        } and has_term(prompt_lower, prompt_tokens, REACT_WEB_TERMS)
        security_match = normalize(name) in {
            "defensive-security-checklist",
            "security-best-practices",
            "security-diff-scan",
            "security-threat-model",
        } and has_term(prompt_lower, prompt_tokens, SECURITY_TERMS)
        skill_authoring_match = normalize(name) in {"writing-skills", "skill-creator"} and has_term(
            prompt_lower, prompt_tokens, SKILL_AUTHORING_ACTION_TERMS
        ) and {"skill", "skills"} & prompt_tokens

        if (
            not strong_overlap
            and not name_in_prompt
            and not full_name_match
            and not capability_routing_match
            and not coding_discipline_match
            and not code_orchestration_match
            and not project_continuity_match
            and not github_match
            and not worktree_match
            and not react_web_match
            and not security_match
            and not skill_authoring_match
            and not source_tool_match
            and not family_routing_match
            and not default_support_match
        ):
            continue

        if session_only_candidate:
            domain_overlap = overlap - CANDIDATE_TERMS - {"new", "missing", "server"}
            meaningful_name_overlap = name_overlap - CANDIDATE_TERMS - {"app", "server", "skill"}
            score = len(domain_overlap) * 5 + len(overlap)
            score += len(meaningful_name_overlap) * 8
        else:
            score = len(strong_overlap) * 6 + len(overlap) * 2
            score += len(name_overlap) * 8

        if capability_routing_match:
            score += 35
        if coding_discipline_match:
            score += 35
        if code_orchestration_match:
            score += 50
        if project_continuity_match:
            score += 28
        if github_match:
            score += 40
        if worktree_match:
            score += 30
        if react_web_match:
            score += 25
        if security_match:
            score += 35
        if skill_authoring_match:
            score += 30
        if source_tool_match:
            score += 50
        if primary_family_routing_match:
            score += 45
        if support_family_routing_match:
            score += 24
        if fallback_support_match:
            score += 10
        if default_support_match:
            score += 12
        if name_in_prompt:
            score += 20
        if full_name_match:
            score += 10
        if session_only_candidate and candidate_visibility == "include_reference":
            score += 20 if status == "reference-only" else -10
        if session_only_candidate and candidate_visibility == "include_install_candidates":
            score += 10 if status != "reference-only" else -10
        score += STATUS_PRIORITY.get(status, 0)
        if session_only_candidate:
            score -= 3

        minimum_score = 4 if session_only_candidate else 8
        if score >= minimum_score:
            scored.append((score, entry))

    best_by_name = {}
    for score, entry in sorted(scored, key=lambda item: item[0], reverse=True):
        key = normalize(entry.get("name", ""))
        current = best_by_name.get(key)
        if current is None or score > current[0]:
            best_by_name[key] = (score, entry)

    results = sorted(best_by_name.values(), key=lambda item: item[0], reverse=True)
    active = [item for item in results if not is_session_only_candidate(item[1])][:limit]
    candidates = [item for item in results if is_session_only_candidate(item[1])][:1]
    if not include_candidates:
        return [entry for _, entry in active]
    if not active and include_candidates:
        return [entry for _, entry in candidates]
    return [entry for _, entry in active] + [entry for _, entry in candidates]
