"""Ensure the retired catalogue router cannot return through the install bundle."""

import hashlib
import json
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class CatalogueRouterWrapperTests(unittest.TestCase):
    def test_reference_provenance_preserves_retired_and_current_paths(self) -> None:
        provenance = json.loads(
            (REPO_ROOT / "capability-routing" / "provenance.json").read_text(encoding="utf-8")
        )
        repository_hashes = provenance["repository_port_sha256"]
        retired = {
            ".agents/skills/catalogue-router/SKILL.md",
            ".agents/skills/catalogue-router/scripts/query-catalogue.ps1",
        }
        historical_wrapper_test = "tests/test_catalogue_router_wrapper.py"
        historical_wrapper_hash = "94570606f1432cc23c5a90a3b74650113951e80d710301a01f103c9fa24216db"
        self.assertTrue(retired <= repository_hashes.keys())
        self.assertIn(historical_wrapper_test, repository_hashes)
        self.assertGreater(len(repository_hashes), len(retired) + 1)
        for relative, expected in repository_hashes.items():
            with self.subTest(path=relative):
                if relative in retired:
                    self.assertFalse((REPO_ROOT / relative).exists())
                    self.assertRegex(expected, r"^[0-9a-f]{64}$")
                elif relative == historical_wrapper_test:
                    self.assertEqual(expected, historical_wrapper_hash)
                    self.assertTrue((REPO_ROOT / relative).is_file())
                else:
                    path = (REPO_ROOT / relative).resolve(strict=True)
                    self.assertTrue(path.is_relative_to(REPO_ROOT))
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)

    def test_retired_skill_and_wrapper_are_absent(self) -> None:
        self.assertFalse((REPO_ROOT / ".agents" / "skills" / "catalogue-router").exists())

    def test_retired_skill_is_not_in_pack_or_install_bundle(self) -> None:
        pack = json.loads((REPO_ROOT / "pack.manifest.json").read_text(encoding="utf-8"))
        bundle = json.loads((REPO_ROOT / "install-bundle.manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("catalogue-router", {item["name"] for item in pack["bundled_skills"]})
        prefix = ".agents/skills/catalogue-router"
        for entry in bundle["entries"]:
            path = entry["path"].replace("\\", "/")
            with self.subTest(path=path):
                self.assertFalse(path == prefix or path.startswith(prefix + "/"))


if __name__ == "__main__":
    unittest.main()
