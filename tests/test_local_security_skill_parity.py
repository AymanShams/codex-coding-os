"""Parity and content contracts for local first-party security skills."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARITY_PATH = ROOT / ".agents" / "security-skill-parity.json"
PARITY = json.loads(PARITY_PATH.read_text(encoding="utf-8"))


def tree_evidence(root: Path) -> tuple[int, str, tuple[str, ...]]:
    """Return the deterministic file count, tree digest, and relative file list."""

    files = tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        )
    )
    digest = hashlib.sha256()
    for relative_path in files:
        file_digest = hashlib.sha256((root / relative_path).read_bytes()).hexdigest()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\n")
    return len(files), digest.hexdigest(), files


class LocalSecuritySkillParityTests(unittest.TestCase):
    def test_parity_record_is_narrow_and_portable(self) -> None:
        self.assertEqual("1.0", PARITY["schema_version"])
        self.assertEqual(
            "parity_evidence_not_routing_authority",
            PARITY["record_role"],
        )
        self.assertEqual("${CODEX_HOME}/skills", PARITY["universal_source_root"])
        self.assertNotRegex(PARITY_PATH.read_text(encoding="utf-8"), r"[A-Za-z]:[/\\]")

        exact_names = {entry["name"] for entry in PARITY["exact_mirrors"]}
        self.assertEqual(
            {
                "security-best-practices",
                "security-threat-model",
                "defensive-security-checklist",
                "security-ownership-map",
            },
            exact_names,
        )
        for entry in PARITY["exact_mirrors"]:
            self.assertEqual(entry["name"], entry["source_path"])
            self.assertEqual(
                f".agents/skills/{entry['name']}",
                entry["repository_path"],
            )
        self.assertEqual(
            ["postgres-security-best-practices"],
            [entry["name"] for entry in PARITY["repository_authored"]],
        )

    def test_repository_trees_match_recorded_evidence(self) -> None:
        entries = PARITY["exact_mirrors"] + PARITY["repository_authored"]
        for entry in entries:
            with self.subTest(skill=entry["name"]):
                skill_root = ROOT / entry["repository_path"]
                self.assertTrue(skill_root.is_dir(), skill_root)
                file_count, tree_sha256, files = tree_evidence(skill_root)
                self.assertEqual(entry["file_count"], file_count)
                self.assertEqual(entry["tree_sha256"], tree_sha256)
                for required_path in entry.get("required_paths", []):
                    self.assertIn(required_path, files)

    def test_exact_mirrors_match_live_universal_sources_when_available(self) -> None:
        configured_root = os.environ.get("CODEX_UNIVERSAL_SKILLS_ROOT")
        if not configured_root:
            self.skipTest(
                "set CODEX_UNIVERSAL_SKILLS_ROOT for an explicit live parity check"
            )
        universal_root = Path(configured_root)
        source_roots = [universal_root / entry["name"] for entry in PARITY["exact_mirrors"]]
        missing = [path for path in source_roots if not path.is_dir()]
        if missing:
            self.fail(f"configured universal skill source is incomplete: {missing}")

        for entry, source_root in zip(PARITY["exact_mirrors"], source_roots):
            with self.subTest(skill=entry["name"]):
                repo_root = ROOT / entry["repository_path"]
                self.assertEqual(tree_evidence(source_root), tree_evidence(repo_root))

    def test_postgres_skill_is_complete_and_provider_neutral(self) -> None:
        entry = PARITY["repository_authored"][0]
        skill_root = ROOT / entry["repository_path"]
        skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        corpus = "\n".join(
            path.read_text(encoding="utf-8")
            for path in skill_root.rglob("*")
            if path.is_file() and path.suffix in {".md", ".yaml"}
        )

        self.assertTrue(skill_text.startswith("---\nname: postgres-security-best-practices\n"))
        self.assertNotIn("TODO", corpus)
        self.assertIsNone(re.search(r"\b(?:supabase|neon)\b", corpus, re.IGNORECASE))

        reference_paths = sorted(
            path.relative_to(skill_root).as_posix()
            for path in (skill_root / "references").glob("*.md")
        )
        self.assertEqual(6, len(reference_paths))
        for reference_path in reference_paths:
            self.assertIn(f"`{reference_path}`", skill_text)

        for required_control in (
            "ALTER DEFAULT PRIVILEGES",
            "FORCE ROW LEVEL SECURITY",
            "security_invoker",
            "SECURITY DEFINER",
            "has_table_privilege",
            "has_function_privilege",
        ):
            self.assertIn(required_control, corpus)


if __name__ == "__main__":
    unittest.main()
