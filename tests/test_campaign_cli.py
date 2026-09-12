#!/usr/bin/env python3
"""Command-level tests for the executable campaign-engine CLI."""

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts.agent.campaign_engine import admission, cli, runtime_bootstrap
from scripts.agent.campaign_engine.model import BudgetToken, CampaignSpec
from scripts.agent.campaign_engine.store import CampaignStore
from scripts.agent.campaign_engine.runtime_bootstrap import runtime_layout


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RuntimeBootstrapTests(unittest.TestCase):
    def test_posix_account_profile_lookup_failures_use_bootstrap_error(self) -> None:
        for failure in (
            KeyError("missing account"),
            OSError("account database unavailable"),
            AttributeError("malformed account record"),
        ):
            fake_pwd = mock.Mock()
            fake_pwd.getpwuid.side_effect = failure
            with (
                self.subTest(failure=type(failure).__name__),
                mock.patch.object(runtime_bootstrap.os, "name", "posix"),
                mock.patch.object(runtime_bootstrap.os, "getuid", return_value=1000, create=True),
                mock.patch.dict(sys.modules, {"pwd": fake_pwd}),
                self.assertRaises(runtime_bootstrap.RuntimeBootstrapError) as raised,
            ):
                runtime_bootstrap.trusted_account_profile()
            self.assertEqual(
                str(raised.exception),
                "operating-system account profile is unavailable",
            )
            self.assertIs(raised.exception.__cause__, failure)


class CampaignCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "tests@example.invalid")
        git(self.repo, "config", "user.name", "Campaign CLI Tests")
        git(
            self.repo,
            "remote",
            "add",
            "origin",
            "https://example.invalid/acme/cli.git",
        )
        (self.repo / "src").mkdir()
        (self.repo / "src" / "app.txt").write_text("base\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base_sha = git(self.repo, "rev-parse", "HEAD").casefold()
        self.profile = self.root / "profile"
        self.runtime, self.runtime_pin = self.make_installed_runtime()
        self.layout = runtime_layout(profile=self.profile)
        self.state_db = self.layout.state_db
        self.spec = self.make_spec()
        self.spec_path = self.root / "campaign.json"
        self.spec_path.write_text(
            json.dumps(self.spec.to_dict(), sort_keys=True), encoding="utf-8"
        )

    def make_installed_runtime(self) -> tuple[Path, dict[str, str]]:
        support = self.profile / ".codex" / "coding-os"
        skills = self.profile / ".codex" / "skills"
        (support / "scripts" / "agent" / "campaign_engine").mkdir(parents=True)
        skills.mkdir(parents=True)
        legacy = support / "scripts" / "agent" / "case_state.py"
        reducer = support / "scripts" / "agent" / "campaign_engine" / "reducer.py"
        cli_entry = support / "scripts" / "agent" / "campaign_engine" / "cli.py"
        bootstrap_entry = (
            support / "scripts" / "agent" / "campaign_engine" / "runtime_bootstrap.py"
        )
        legacy.write_text(
            "import sys\nprint('LEGACY_ENGINE_RETIRED')\nraise SystemExit(78)\n",
            encoding="utf-8",
        )
        reducer.write_text("def reduce():\n    return None\n", encoding="utf-8")
        cli_entry.write_text("# installed CLI test entry\n", encoding="utf-8")
        bootstrap_entry.write_text("# installed bootstrap test entry\n", encoding="utf-8")
        engine_dir = reducer.parent
        for name in (
            "__init__.py",
            "admission.py",
            "ed25519.py",
            "effects.py",
            "evidence.py",
            "host.py",
            "legacy.py",
            "model.py",
            "store.py",
            "supervisor.py",
        ):
            (engine_dir / name).write_text(f"# installed {name} test entry\n", encoding="utf-8")
        entries = []
        installed_files = [("scripts/agent/case_state.py", legacy)] + [
            (f"scripts/agent/campaign_engine/{path.name}", path)
            for path in sorted(engine_dir.glob("*.py"), key=lambda item: item.name)
        ]
        for relative, path in installed_files:
            entries.append(
                {
                    "path": relative,
                    "size": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
        aggregate = admission._aggregate_entries(entries)
        bundle = {
            "protocol": admission.BUNDLE_PROTOCOL,
            "package": {"name": "codex-coding-os", "version": "test"},
            "aggregate_sha256": aggregate,
            "entries": entries,
        }
        bundle_path = support / "install-bundle.manifest.json"
        bundle_path.write_text(
            json.dumps(bundle, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        pin = {
            "source_commit": "b" * 40,
            "bundle_digest": aggregate,
            "install_transaction": "c" * 32,
            "protocol_version": admission.RUNTIME_PROTOCOL_VERSION,
            "schema_compatibility": admission.SCHEMA_COMPATIBILITY,
            "host_capability_probe_version": admission.HOST_CAPABILITY_PROBE_VERSION,
        }
        manifest = {
            "package": {"bundle_sha256": aggregate},
            "source": {
                "git_commit": pin["source_commit"],
                "bundle_manifest_sha256": sha256(bundle_path),
            },
            "transaction": {"id": pin["install_transaction"]},
            "targets": {
                "support_root": str(support.resolve(strict=True)),
                "skills_root": str(skills.resolve(strict=True)),
            },
            "runtime_pin": pin,
        }
        (support / "install-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        return support, pin

    def make_spec(self) -> CampaignSpec:
        budgets = [{"token": token.value, "limit": 2} for token in BudgetToken]
        repo_path = str(self.repo.resolve(strict=True))
        return CampaignSpec.from_dict(
            {
                "campaign_id": "cli-campaign",
                "specification_revision": 1,
                "authority_epoch": 3,
                "cancellation_epoch": 0,
                "mode": "MANUAL",
                "objective": "exercise the campaign command surface",
                "objective_kind": "CONTROL_RUNTIME",
                "repository_remote": "https://example.invalid/acme/cli.git",
                "git_root": repo_path,
                "worktree": repo_path,
                "branch": "main",
                "base_sha": self.base_sha,
                "allowed_paths": ["src/**"],
                "nodes": [
                    {
                        "node_id": "node-1",
                        "objective": "change the approved file",
                        "allowed_paths": ["src/**"],
                        "validation_command_ids": ["unit"],
                        "requires_review": True,
                    }
                ],
                "required_validation_commands": [
                    {
                        "command_id": "unit",
                        "executable": sys.executable,
                        "arguments": ["-B", "-c", "print('validated')"],
                        "working_directory": repo_path,
                        "environment_allowlist": ["PATH", "SYSTEMROOT"],
                        "timeout_seconds": 30,
                        "output_limit_bytes": 100_000,
                        "expected_worktree_condition": "CLEAN",
                        "required_exit_code": 0,
                    }
                ],
                "required_review_cohort": ["reviewer-a"],
                "publication_authority": {
                    "authorized_by": "test-owner",
                    "human_authorization": {
                        "algorithm": "ED25519",
                        "public_key_base64": "iojj3XQJ8ZX9UtstPLpdcspnCb8dlBIb83SIAbQPb1w=",
                    },
                    "automated": False,
                    "allowed_effects": ["PUSH"],
                    "required_effects": ["PUSH"],
                },
                "attempt_budgets": budgets,
                "stop_conditions": ["STOP", "budget exhausted"],
                "installed_source_commit": self.runtime_pin["source_commit"],
                "installed_bundle_digest": self.runtime_pin["bundle_digest"],
                "install_transaction": self.runtime_pin["install_transaction"],
                "protocol_version": self.runtime_pin["protocol_version"],
                "schema_compatibility": self.runtime_pin["schema_compatibility"],
                "host_capability_probe_version": self.runtime_pin[
                    "host_capability_probe_version"
                ],
                "autonomous_rank": sum(item["limit"] for item in budgets),
                "deadline_utc": "2099-01-01T00:00:00Z",
            }
        )

    def run_cli(self, *arguments: str) -> tuple[int, dict]:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(
                [*arguments, "--json"], injected_runtime=self.layout
            )
        lines = [line for line in output.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, output.getvalue())
        payload = json.loads(lines[0])
        self.assertIsInstance(payload, dict)
        return exit_code, payload

    def admit(self) -> dict:
        exit_code, payload = self.run_cli(
            "admit",
            "--spec",
            str(self.spec_path),
        )
        self.assertEqual(exit_code, 0, payload)
        self.assertTrue(payload["ok"])
        return payload

    def test_admit_approve_status_cancel_and_doctor_use_real_temp_state(self) -> None:
        admitted = self.admit()
        self.assertEqual(admitted["campaign_id"], "cli-campaign")
        self.assertEqual(admitted["state"], "DRAFT")
        self.assertEqual(
            admitted["specification_digest"], self.spec.specification_digest
        )
        self.assertEqual(
            admitted["admission"]["repository"]["head_sha"], self.base_sha
        )
        self.assertEqual(
            admitted["admission"]["installed_runtime"]["verified_file_count"], 14
        )
        self.assertEqual(
            admitted["admission"]["human_authorization_verifier"]["algorithm"],
            "ED25519",
        )
        self.assertEqual(
            len(
                admitted["admission"]["human_authorization_verifier"][
                    "public_key_sha256"
                ]
            ),
            64,
        )

        exit_code, approved = self.run_cli(
            "approve",
            "--campaign-id",
            "cli-campaign",
            "--specification-digest",
            self.spec.specification_digest,
            "--request-id",
            "approve-cli-campaign",
        )
        self.assertEqual(exit_code, 0, approved)
        self.assertEqual(approved["state"], "APPROVED")

        exit_code, status = self.run_cli(
            "status",
            "--campaign-id",
            "cli-campaign",
        )
        self.assertEqual(exit_code, 0, status)
        self.assertEqual(status["campaign_count"], 1)
        campaign = status["campaigns"][0]
        self.assertEqual(campaign["campaign"]["state"], "APPROVED")
        self.assertEqual(campaign["active_leases"], [])
        self.assertEqual(campaign["outbox"], [])

        exit_code, cancelled = self.run_cli(
            "cancel",
            "--campaign-id",
            "cli-campaign",
            "--reason",
            "STOP",
        )
        self.assertEqual(exit_code, 0, cancelled)
        self.assertEqual(cancelled["action"], "CANCELLED")
        self.assertEqual(cancelled["campaign_state"], "CANCELLED")

        exit_code, filtered = self.run_cli(
            "status",
            "--repository-root",
            str(self.repo.resolve(strict=True)),
        )
        self.assertEqual(exit_code, 0, filtered)
        self.assertEqual(filtered["campaign_count"], 1)
        self.assertEqual(
            filtered["campaigns"][0]["campaign"]["failure_reason"], "STOP"
        )

        exit_code, doctor = self.run_cli(
            "doctor",
            "--recover",
        )
        self.assertEqual(exit_code, 0, doctor)
        self.assertTrue(doctor["ok"])
        self.assertEqual(doctor["integrity"]["status"], "ok")
        self.assertEqual(doctor["restart_recovery"]["ambiguous_effects"], 0)
        self.assertEqual(doctor["restart_recovery"]["invalidated_leases"], 0)
        self.assertEqual(doctor["runtime_pin"]["verified_file_count"], 14)
        self.assertFalse(doctor["host_capability"]["live"])
        self.assertTrue(doctor["retirement"]["single_engine"])
        self.assertEqual(
            doctor["retirement"]["lifecycle_reducers"],
            ["scripts/agent/campaign_engine/reducer.py"],
        )

    def test_run_rejects_runtime_file_drift_after_admission(self) -> None:
        self.admit()
        exit_code, approved = self.run_cli(
            "approve",
            "--campaign-id",
            "cli-campaign",
            "--specification-digest",
            self.spec.specification_digest,
        )
        self.assertEqual(exit_code, 0, approved)
        reducer = self.runtime / "scripts" / "agent" / "campaign_engine" / "reducer.py"
        reducer.write_text("# drifted runtime\n", encoding="utf-8")
        exit_code, denied = self.run_cli(
            "run",
            "--campaign-id",
            "cli-campaign",
            "--once",
        )
        self.assertEqual(exit_code, cli.EXIT_FAILED)
        self.assertIn("differs from its pin", denied["message"])

    def test_run_without_external_effects_yields_after_publication_prepared(self) -> None:
        self.admit()
        step_calls: list[str] = []
        test_case = self

        class NoExternalEffectsSupervisor:
            def __init__(self, store: CampaignStore, *, effect_driver=None) -> None:
                del store
                test_case.assertIsNone(effect_driver)

            def step(self, campaign_id: str):
                step_calls.append(campaign_id)
                if len(step_calls) > 1:
                    raise AssertionError("run attempted to execute the prepared publication")
                return cli.SupervisorDecision(
                    campaign_id,
                    1,
                    "DRAFT",
                    "PUBLICATION_PREPARED",
                    "node-1",
                    details={"operation_id": "publish-operation", "effect_kind": "PUSH"},
                )

        with mock.patch.object(
            cli, "DeterministicSupervisor", NoExternalEffectsSupervisor
        ):
            exit_code, yielded = self.run_cli(
                "run",
                "--campaign-id",
                "cli-campaign",
                "--no-external-effects",
            )

        self.assertEqual(exit_code, 0, yielded)
        self.assertEqual(step_calls, ["cli-campaign"])
        self.assertEqual(
            [decision["action"] for decision in yielded["decisions"]],
            ["PUBLICATION_PREPARED"],
        )

    def test_cancel_remains_available_on_canonical_db_during_runtime_drift(self) -> None:
        self.admit()
        unrelated = self.runtime / "scripts" / "agent" / "case_state.py"
        unrelated.write_text("# unrelated retired-command drift\n", encoding="utf-8")
        exit_code, cancelled = self.run_cli(
            "cancel",
            "--campaign-id",
            "cli-campaign",
            "--reason",
            "STOP despite runtime drift",
        )
        self.assertEqual(exit_code, 0, cancelled)
        self.assertEqual(cancelled["campaign_state"], "CANCELLED")
        self.assertTrue(cancelled["runtime_verification"]["cancel_exception"])
        with CampaignStore(self.state_db) as store:
            self.assertEqual(store.get_snapshot("cli-campaign").state.value, "CANCELLED")

    def test_cancel_denies_drift_in_its_pinned_dependency_closure(self) -> None:
        self.admit()
        model = self.runtime / "scripts" / "agent" / "campaign_engine" / "model.py"
        model.write_text("# cancel-critical drift\n", encoding="utf-8")
        exit_code, denied = self.run_cli(
            "cancel", "--campaign-id", "cli-campaign", "--reason", "STOP"
        )
        self.assertEqual(exit_code, cli.EXIT_FAILED)
        self.assertEqual(denied["code"], "RUNTIME_BOOTSTRAP_FAILED")
        self.assertIn("cancellation file differs", denied["message"])
        with CampaignStore(self.state_db) as store:
            self.assertEqual(store.get_snapshot("cli-campaign").state.value, "DRAFT")

    def test_cancel_denies_ed25519_drift_in_its_pinned_dependency_closure(self) -> None:
        self.admit()
        verifier = self.runtime / "scripts" / "agent" / "campaign_engine" / "ed25519.py"
        verifier.write_text("# drifted authorization verifier\n", encoding="utf-8")
        exit_code, denied = self.run_cli(
            "cancel", "--campaign-id", "cli-campaign", "--reason", "STOP"
        )
        self.assertEqual(exit_code, cli.EXIT_FAILED)
        self.assertEqual(denied["code"], "RUNTIME_BOOTSTRAP_FAILED")
        self.assertIn("cancellation file differs", denied["message"])
        with CampaignStore(self.state_db) as store:
            self.assertEqual(store.get_snapshot("cli-campaign").state.value, "DRAFT")

    def test_cancel_denies_missing_ed25519_in_its_pinned_dependency_closure(self) -> None:
        self.admit()
        verifier = self.runtime / "scripts" / "agent" / "campaign_engine" / "ed25519.py"
        verifier.unlink()
        exit_code, denied = self.run_cli(
            "cancel", "--campaign-id", "cli-campaign", "--reason", "STOP"
        )
        self.assertEqual(exit_code, cli.EXIT_FAILED)
        self.assertEqual(denied["code"], "RUNTIME_BOOTSTRAP_FAILED")
        self.assertIn("cancellation file", denied["message"])
        with CampaignStore(self.state_db) as store:
            self.assertEqual(store.get_snapshot("cli-campaign").state.value, "DRAFT")

    def test_public_cli_exposes_no_root_or_state_override(self) -> None:
        help_text = cli.build_parser().format_help()
        source = Path(cli.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "--state-db",
            "--installed-root",
            "CCOS_STATE_DB",
            "CODEX_HOME",
        ):
            self.assertNotIn(forbidden, help_text)
            self.assertNotIn(forbidden, source)

    def test_environment_cannot_redirect_injected_canonical_state(self) -> None:
        prior_state = os.environ.get("CCOS_STATE_DB")
        prior_home = os.environ.get("CODEX_HOME")
        try:
            os.environ["CCOS_STATE_DB"] = str(self.root / "hostile.sqlite3")
            os.environ["CODEX_HOME"] = str(self.root / "hostile-codex-home")
            self.admit()
        finally:
            if prior_state is None:
                os.environ.pop("CCOS_STATE_DB", None)
            else:
                os.environ["CCOS_STATE_DB"] = prior_state
            if prior_home is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = prior_home
        self.assertTrue(self.state_db.is_file())
        self.assertFalse((self.root / "hostile.sqlite3").exists())

    def test_approve_rejects_a_digest_other_than_the_admitted_specification(self) -> None:
        self.admit()
        exit_code, denied = self.run_cli(
            "approve",
            "--campaign-id",
            "cli-campaign",
            "--specification-digest",
            "0" * 64,
        )
        self.assertEqual(exit_code, cli.EXIT_FAILED)
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["code"], "ValueError")
        self.assertIn("immutable specification", denied["message"])
        _, status = self.run_cli(
            "status",
            "--campaign-id",
            "cli-campaign",
        )
        self.assertEqual(status["campaigns"][0]["campaign"]["state"], "DRAFT")

    def test_admission_rejects_public_self_approval_without_signature_verifier(self) -> None:
        raw = self.spec.to_dict()
        raw.pop("specification_digest", None)
        raw["publication_authority"].pop("human_authorization")
        unsigned = CampaignSpec.from_dict(raw)
        unsigned_path = self.root / "unsigned-campaign.json"
        unsigned_path.write_text(
            json.dumps(unsigned.to_dict(), sort_keys=True), encoding="utf-8"
        )
        exit_code, denied = self.run_cli(
            "admit", "--spec", str(unsigned_path)
        )
        self.assertEqual(exit_code, cli.EXIT_FAILED)
        self.assertEqual(denied["code"], "AdmissionError")
        self.assertIn("signature verifier", denied["message"])

    def proposed_product_change(self) -> dict:
        (self.repo / "requirements.md").write_text(
            "# Product\n\n## REQ-EXPORT\nExport the complete order total.\n\n"
            "## REQ-COLOR\nUse the existing colors.\n", encoding="utf-8"
        )
        raw = self.spec.to_dict()
        raw.pop("specification_digest")
        raw["nodes"][0]["acceptance_scenarios"] = [{
            "scenario_id": "export-total", "expectation": "Export the complete order total",
            "validation_command_id": "unit", "sources": [{
                "path": "requirements.md", "requirement_id": "REQ-EXPORT", "section": "REQ-EXPORT",
            }],
        }]
        for key in ("git_root", "worktree", "repository_remote", "branch", "base_sha",
                    "installed_source_commit", "installed_bundle_digest", "install_transaction"):
            raw.pop(key)
        raw["required_validation_commands"][0].pop("working_directory")
        return raw

    def test_prepare_binds_existing_sources_without_starting_or_overwriting(self) -> None:
        raw = self.proposed_product_change()
        draft = self.root / "proposed.json"
        output = self.root / "prepared.json"
        draft.write_text(json.dumps(raw), encoding="utf-8")
        code, result = self.run_cli("prepare", "--spec", str(draft),
                                    "--repository", str(self.repo), "--output", str(output))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["state"], "PROPOSED")
        self.assertFalse(self.state_db.exists())
        prepared = CampaignSpec.from_dict(json.loads(output.read_text(encoding="utf-8")))
        source = prepared.nodes[0].acceptance_scenarios[0]["sources"][0]
        self.assertEqual(source["sha256"], hashlib.sha256(
            admission.acceptance_source_content(self.repo, source)).hexdigest())
        self.assertEqual(prepared.base_sha, self.base_sha)
        before = output.read_bytes()
        code, _ = self.run_cli("prepare", "--spec", str(draft),
                               "--repository", str(self.repo), "--output", str(output))
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertEqual(output.read_bytes(), before)

    def test_prepare_rejects_conflicting_identity_and_changed_declared_source(self) -> None:
        raw = self.proposed_product_change()
        raw["branch"] = "another-branch"
        draft = self.root / "proposed.json"
        output = self.root / "prepared.json"
        draft.write_text(json.dumps(raw), encoding="utf-8")
        code, result = self.run_cli("prepare", "--spec", str(draft),
                                    "--repository", str(self.repo), "--output", str(output))
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertIn("conflicts", result["message"])
        raw.pop("branch")
        raw["nodes"][0]["acceptance_scenarios"][0]["sources"][0]["sha256"] = "0" * 64
        draft.write_text(json.dumps(raw), encoding="utf-8")
        code, result = self.run_cli("prepare", "--spec", str(draft),
                                    "--repository", str(self.repo), "--output", str(output))
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertIn("source changed", result["message"])
        self.assertFalse(output.exists())

    def test_source_drift_is_scoped_and_approval_rechecks_relevant_meaning(self) -> None:
        raw = self.proposed_product_change()
        draft = self.root / "proposed.json"
        output = self.root / "prepared.json"
        draft.write_text(json.dumps(raw), encoding="utf-8")
        code, result = self.run_cli("prepare", "--spec", str(draft),
                                    "--repository", str(self.repo), "--output", str(output))
        self.assertEqual(code, 0, result)
        prepared = json.loads(output.read_text(encoding="utf-8"))
        path = self.repo / "requirements.md"
        path.write_text(path.read_text(encoding="utf-8").replace("existing colors", "blue colors"), encoding="utf-8")
        self.assertEqual(len(admission.verify_acceptance_sources(prepared)), 1)
        code, result = self.run_cli("admit", "--spec", str(output))
        self.assertEqual(code, 0, result)
        path.write_text(path.read_text(encoding="utf-8").replace("complete order total", "subtotal only"), encoding="utf-8")
        with self.assertRaises(admission.SourceDriftError):
            admission.verify_acceptance_sources(prepared)
        code, result = self.run_cli("approve", "--campaign-id", "cli-campaign",
                                    "--specification-digest", prepared["specification_digest"])
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertIn("REQ-EXPORT", result["message"])

    def test_source_sections_ignore_code_examples_and_reject_ambiguous_headings(self) -> None:
        path = self.repo / "requirements.md"
        path.write_text("```md\n## Rule\nExample only\n```\n## Rule\nActual\n## Next\nOther\n", encoding="utf-8", newline="\n")
        source = {"path": "requirements.md", "section": "Rule"}
        self.assertEqual(admission.acceptance_source_content(self.repo, source), b"## Rule\nActual\n")
        path.write_text(path.read_text(encoding="utf-8") + "## Rule\nConflicting\n", encoding="utf-8")
        with self.assertRaises(admission.SourceDriftError):
            admission.acceptance_source_content(self.repo, source)

    def test_status_renders_current_outcome_without_a_repository_change(self) -> None:
        self.admit()
        before = git(self.repo, "status", "--porcelain")
        _, first = self.run_cli("status", "--campaign-id", "cli-campaign")
        _, second = self.run_cli("status", "--campaign-id", "cli-campaign")
        self.assertEqual(first["campaigns"], second["campaigns"])
        self.assertEqual(first["campaigns"][0]["summary"]["status"], "Awaiting approval")
        self.assertEqual(git(self.repo, "status", "--porcelain"), before)


    def test_required_browser_is_rejected_before_campaign_creation(self):
        raw = self.proposed_product_change()
        proposal = self.root / "proposed.json"
        raw["nodes"][0]["required_tools"] = ["browser_navigate"]
        proposal.write_text(json.dumps(raw), encoding="utf-8")
        code, result = self.run_cli("prepare", "--spec", str(proposal),
                                    "--repository", str(self.repo), "--output", str(self.root / "prepared.json"))
        self.assertNotEqual(code, 0)
        self.assertIn("required host tools unavailable", result["message"])
        self.assertFalse((self.root / "prepared.json").exists())

    def test_required_supported_tools_ignore_unrelated_missing_plugins(self):
        raw = self.proposed_product_change()
        proposal = self.root / "proposed.json"
        raw["nodes"][0]["required_tools"] = ["campaign_read_file", "campaign_apply_patch", "campaign_commit"]
        proposal.write_text(json.dumps(raw), encoding="utf-8")
        code, result = self.run_cli("prepare", "--spec", str(proposal),
                                    "--repository", str(self.repo), "--output", str(self.root / "prepared.json"))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["state"], "PROPOSED")

    def test_doctor_distinguishes_missing_entry_router_from_verified_engine(self):
        code, result = self.run_cli("doctor")
        self.assertEqual(code, 0, result)
        prerequisite = result["entry_prerequisites"]["canonical_router"]
        self.assertFalse(prerequisite["available"])
        self.assertEqual(prerequisite["required_for"], "routed skill entry")
        self.assertEqual(prerequisite["route_admission"], "not_checked")
        self.assertTrue(result["runtime_pin"])


if __name__ == "__main__":
    unittest.main()
