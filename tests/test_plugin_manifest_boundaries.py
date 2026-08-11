"""Contract tests for Codex-managed third-party plugin boundaries."""

from __future__ import annotations

import json
import unittest
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
PLUGINS_PATH = ROOT / "codex-capabilities" / "plugins.manifest.json"
TOOLS_PATH = ROOT / "codex-capabilities" / "tools.manifest.json"

CODEX_SECURITY_SKILLS = {
    "codex-security:attack-path-analysis",
    "codex-security:deep-security-scan",
    "codex-security:define-security-policy",
    "codex-security:finding-discovery",
    "codex-security:fix-finding",
    "codex-security:propose-security-hardening",
    "codex-security:security-diff-scan",
    "codex-security:security-scan",
    "codex-security:threat-model",
    "codex-security:track-findings",
    "codex-security:triage-finding",
    "codex-security:validation",
    "codex-security:vulnerability-writeup",
}
SUPABASE_SKILLS = {
    "supabase:supabase",
    "supabase:supabase-postgres-best-practices",
}
NEON_SKILLS = {
    "neon-postgres:neon-postgres",
    "neon-postgres:neon-postgres-egress-optimizer",
}
PLUGIN_SKILLS = CODEX_SECURITY_SKILLS | SUPABASE_SKILLS | NEON_SKILLS


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _plugin(manifest: dict, name: str) -> dict:
    return next(item for item in manifest["recommended_plugins"] if item["name"] == name)


def _named(items: list[dict], name: str) -> dict:
    return next(item for item in items if item["name"] == name)


def test_manifest_data_versions_and_review_dates_are_current() -> None:
    plugins = _load(PLUGINS_PATH)
    tools = _load(TOOLS_PATH)

    assert plugins["version"] == "1.2.0"
    assert tools["version"] == "1.1.0"
    assert plugins["last_reviewed"] == "2026-08-11"
    assert tools["last_reviewed"] == "2026-08-11"


def test_codex_security_declares_complete_managed_surface() -> None:
    plugin = _plugin(_load(PLUGINS_PATH), "Codex Security")

    assert plugin["plugin_id"] == "codex-security@openai-curated-remote"
    assert plugin["plugin_version"] == "0.1.18"
    assert plugin["management"] == "third-party-codex-managed"
    assert plugin["repo_bundled"] is False
    assert set(plugin["managed_skills"]) == CODEX_SECURITY_SKILLS

    dependency = plugin["mcp_dependency"]
    assert dependency["server"] == "codex-security"
    assert set(dependency["required_for_live_workflows"]) == {
        "codex-security:deep-security-scan",
        "codex-security:security-scan",
        "codex-security:security-diff-scan",
    }
    assert dependency["non_equivalent_without"] == ["codex-security:deep-security-scan"]

    fallbacks = plugin["capability_fallbacks"]
    deep = next(item for item in fallbacks if "codex-security:deep-security-scan" in item["skills"])
    standard = next(item for item in fallbacks if "codex-security:security-scan" in item["skills"])
    diff = next(item for item in fallbacks if "codex-security:security-diff-scan" in item["skills"])
    assert deep["equivalence"] == "non_equivalent"
    assert deep["chosen_fallback"] == "workflow:codex-parent-bounded-three-pass-repository-review"
    assert deep["max_passes"] == 3
    assert deep["deadline_seconds"] == 3600
    assert deep["not_reproduced"]
    assert standard["chosen_fallback"] == "workflow:codex-security-prompt-only-standard"
    assert standard["equivalence"] == "equivalent"
    assert standard["max_passes"] == 1
    assert diff["chosen_fallback"] == "workflow:codex-security-terminal-chat-diff"
    assert diff["equivalence"] == "equivalent"
    assert diff["max_passes"] == 1

    writes = plugin["write_integrations"]
    assert writes["skill"] == "codex-security:track-findings"
    assert {item["name"] for item in writes["apps"]} == {"github", "linear", "atlassian"}
    assert all("write" in item["access"] for item in writes["apps"])
    assert any("explicit user approval" in gate for gate in writes["required_gates"])
    assert any("read back" in gate for gate in writes["required_gates"])


def test_database_plugins_are_connector_managed_and_not_bundled() -> None:
    plugins = _load(PLUGINS_PATH)

    expectations = {
        "Supabase": (
            "supabase@openai-curated-remote",
            "1.0.0",
            SUPABASE_SKILLS,
            "supabase",
        ),
        "Neon Postgres": (
            "neon-postgres@openai-curated-remote",
            "1.0.0",
            NEON_SKILLS,
            "neon-postgres",
        ),
    }
    for name, (plugin_id, version, skills, connector_name) in expectations.items():
        plugin = _plugin(plugins, name)
        assert plugin["plugin_id"] == plugin_id
        assert plugin["plugin_version"] == version
        assert plugin["management"] == "third-party-codex-managed"
        assert plugin["repo_bundled"] is False
        assert set(plugin["managed_skills"]) == skills
        assert plugin["app_connector"] == {
            "name": connector_name,
            "required": True,
            "access": ["read", "write"],
            "auth_and_state": "Codex-managed and never stored in this repository",
        }

    neon = _plugin(plugins, "Neon Postgres")
    assert neon["optional_mcp_guidance"]["server"] == "neon"
    assert "required plugin app connector" in neon["optional_mcp_guidance"]["use"]


def test_obsolete_build_web_apps_supabase_alias_is_suppressed() -> None:
    plugins = _load(PLUGINS_PATH)
    obsolete = "build-web-apps:supabase-postgres-best-practices"

    all_managed = {
        skill
        for plugin in plugins["recommended_plugins"]
        for skill in plugin.get("managed_skills", [])
    }
    assert obsolete not in all_managed
    assert plugins["suppressed_managed_skills"] == [
        {
            "skill": obsolete,
            "replacement": "supabase:supabase-postgres-best-practices",
            "reason": "The current Build Web Apps plugin does not provide this skill. Supabase owns the current provider-specific Postgres guidance.",
        }
    ]


def test_tool_manifest_records_connectors_and_mcp_fallbacks() -> None:
    tools = _load(TOOLS_PATH)

    connectors = tools["plugin_app_connectors"]
    supabase = _named(connectors, "supabase")
    neon_connector = _named(connectors, "neon-postgres")
    assert supabase["plugin_id"] == "supabase@openai-curated-remote"
    assert neon_connector["plugin_id"] == "neon-postgres@openai-curated-remote"
    assert supabase["required_by_plugin"] is True
    assert neon_connector["required_by_plugin"] is True
    assert supabase["repo_bundled"] is False
    assert neon_connector["repo_bundled"] is False

    codex_security = _named(tools["mcp_servers"], "codex-security")
    neon_mcp = _named(tools["mcp_servers"], "neon")
    assert codex_security["required_for"] == ["codex-security:deep-security-scan"]
    assert codex_security["fallback"]["equivalence"] == "non_equivalent"
    assert codex_security["repo_bundled"] is False
    assert neon_mcp["priority"] == "optional-for-neon-projects"
    assert neon_mcp["repo_bundled"] is False
    assert not any(item["name"] == "supabase" for item in tools["mcp_servers"])


def test_plugin_manifest_mcp_recommendations_match_managed_boundaries() -> None:
    plugins = _load(PLUGINS_PATH)

    codex_security = _named(plugins["recommended_mcps"], "codex-security")
    neon = _named(plugins["recommended_mcps"], "neon")
    assert codex_security["priority"] == "required-for-codex-security-deep-scans"
    assert codex_security["repo_bundled"] is False
    assert neon["priority"] == "optional-for-neon-projects"
    assert neon["repo_bundled"] is False
    assert not any(item["name"] == "supabase" for item in plugins["recommended_mcps"])


def test_plugin_payloads_are_absent_from_repo_owned_and_install_surfaces() -> None:
    skill_names = {skill.split(":", 1)[1].casefold() for skill in PLUGIN_SKILLS}
    plugin_roots = {"codex-security", "supabase", "neon-postgres"}
    repo_skill_names = {
        path.name.casefold()
        for path in (ROOT / ".agents" / "skills").iterdir()
        if path.is_dir()
    }
    assert skill_names.isdisjoint(repo_skill_names)

    install_bundle = _load(ROOT / "install-bundle.manifest.json")
    bundle_paths = {entry["path"].replace("\\", "/") for entry in install_bundle["entries"]}
    for path in bundle_paths:
        parts = {part.casefold() for part in PurePosixPath(path).parts}
        assert plugin_roots.isdisjoint(parts)
        assert not {".codex-plugin", ".app.json", ".mcp.json"}.intersection(parts)
        assert not {"plugins", "cache"}.issubset(parts)
    for skill_name in skill_names:
        prefix = f".agents/skills/{skill_name}/"
        assert not any(
            path.casefold() == prefix[:-1] or path.casefold().startswith(prefix)
            for path in bundle_paths
        )

    inspected_roots = [
        ROOT / ".agents",
        ROOT / "patches" / "external-skills",
    ]
    forbidden_metadata = {".codex-plugin", ".app.json", ".mcp.json"}
    for inspected_root in inspected_roots:
        for path in inspected_root.rglob("*"):
            relative_parts = {
                part.casefold() for part in path.relative_to(inspected_root).parts
            }
            assert not forbidden_metadata.intersection(relative_parts)
            assert plugin_roots.isdisjoint(relative_parts)
            if path.is_dir():
                assert path.name.casefold() not in skill_names


def load_tests(
    loader: unittest.TestLoader,
    standard_tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    """Expose the dependency-free function contracts to unittest and CI."""

    del loader, standard_tests, pattern
    suite = unittest.TestSuite()
    for name, candidate in sorted(globals().items()):
        if name.startswith("test_") and callable(candidate):
            suite.addTest(unittest.FunctionTestCase(candidate, description=name))
    return suite


if __name__ == "__main__":
    unittest.main()
