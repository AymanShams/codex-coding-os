#!/usr/bin/env python3
"""Public installer journey, restricted to a disposable Linux account namespace.

Run through bubblewrap with a read-only host root, tmpfs at the actual account
profile and /tmp, and one writable /out artifact directory. Preserve the host
UID/GID so the installed validation boundary can create its own namespace.
Inputs must be exact git archive ZIPs.
No runtime path injection, native model call, or canonical router activation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile


ACCOUNT_PROFILE = Path(pwd.getpwuid(os.getuid()).pw_dir)
CODEX = ACCOUNT_PROFILE / ".codex"
INSTALLED = CODEX / "coding-os"
LAUNCHER = INSTALLED / "scripts/agent/campaign_engine/cli.py"
STATE = CODEX / "coding-os-state/campaigns.sqlite3"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def disposable_account():
    require(sys.platform.startswith("linux"), "Linux account namespace required")
    mappings = [tuple(map(int, line.split())) for line in Path("/proc/self/uid_map").read_text().splitlines()]
    require(os.getuid() != 0 and len(mappings) == 1
            and mappings[0][0] == os.getuid() and mappings[0][2] == 1,
            "A single-user disposable namespace preserving the non-root host UID is required")
    mounts = {}
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        left, right = line.split(" - ", 1)
        fields = left.split()
        mounts[fields[4]] = (fields[5].split(","), right.split()[0])
    require("ro" in mounts["/"][0], "The host root must be mounted read-only")
    require(all(mounts.get(path, ([], ""))[1] == "tmpfs" for path in (str(ACCOUNT_PROFILE), "/tmp")),
            "The actual account profile and /tmp must be disposable tmpfs mounts")
    require(os.environ.get("HOME") == str(ACCOUNT_PROFILE), "HOME must match the actual OS profile")
    require(os.environ.get("CODEX_HOME", str(CODEX)) == str(CODEX), "Unexpected CODEX_HOME")
    require(not os.environ.get("SKILLS_ROOT"), "SKILLS_ROOT override is not allowed")
    require(not CODEX.exists() or not any(CODEX.iterdir()), "Canonical Codex home must start empty")
    return {"uid": os.getuid(), "uid_map": mappings, "os_profile": str(ACCOUNT_PROFILE),
            "codex_home": str(CODEX), "state_db": str(STATE), "root_read_only": True,
            "profile_and_temp_filesystem": "tmpfs"}


def run_json(argv, *, cwd=None, expected_exit=0):
    result = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=180)
    require(result.returncode == expected_exit,
            f"Command failed ({result.returncode}): {argv}\n{result.stdout}\n{result.stderr}")
    value = json.loads(result.stdout)
    require(isinstance(value, dict), "Expected one JSON object")
    return value


def cli(*args):
    return run_json([sys.executable, "-B", str(LAUNCHER), "--json", *args], cwd=ACCOUNT_PROFILE)


def unpack(archive, commit, destination):
    require(re.fullmatch(r"[0-9a-f]{40}", commit), "Exact source commit required")
    with zipfile.ZipFile(archive) as package:
        require(package.comment.decode("ascii") == commit, "Git archive commit comment differs")
        for member in package.infolist():
            path = PurePosixPath(member.filename)
            require(not path.is_absolute() and ".." not in path.parts and "\\" not in member.filename,
                    "Archive contains a path outside its source root")
            require((member.external_attr >> 16) & 0o170000 != 0o120000,
                    "Archive symlinks are not supported by this fixture")
        package.extractall(destination)
    bundle = json.loads((destination / "install-bundle.manifest.json").read_text())
    return {"commit": commit, "archive_sha256": sha256(archive),
            "bundle_sha256": bundle["aggregate_sha256"], "root": destination}


def install(source):
    return run_json(["bash", str(source["root"] / "scripts/install.sh"),
                     "--archive-mode", "--expected-source-commit", source["commit"],
                     "--expected-bundle-sha256", source["bundle_sha256"]], cwd=ACCOUNT_PROFILE)


def installed_delivery(source, report):
    # Namespace-package search order puts the actual installed scripts first.
    # Test expectations come from the exact candidate archive. Assert every
    # loaded engine module's physical path before and after executing fixtures.
    sys.path[:0] = [str(INSTALLED), str(source["root"])]
    from scripts.agent.campaign_engine.model import CampaignSpec
    from scripts.agent.campaign_engine.runtime_bootstrap import runtime_layout
    from scripts.agent.campaign_engine.store import CampaignStore
    from tests.test_campaign_bounded_continuation import BoundedContinuationTests

    def module_sources():
        result = {}
        for name, module in tuple(sys.modules.items()):
            if "campaign_engine" in name and getattr(module, "__file__", None):
                path = Path(module.__file__).resolve()
                require(path.is_relative_to(INSTALLED), f"Engine import escaped installed runtime: {name}: {path}")
                result[name] = {"path": str(path), "sha256": sha256(path)}
        return result

    module_sources()
    pin = json.loads((INSTALLED / "install-manifest.json").read_text())["runtime_pin"]

    class InstalledDelivery(BoundedContinuationTests):
        def setUp(self):
            super().setUp()
            self.store.close()
            layout = runtime_layout()
            require(layout.state_db == STATE, "Canonical state resolution changed")
            self.store = CampaignStore(layout.state_db)
            self.addCleanup(self.store.close)

        def make_spec(self, *args, **kwargs):
            kwargs.update(campaign_deadline="2099-01-01T00:00:00Z", node_deadline="2099-01-01T00:00:00Z")
            raw = super().make_spec(*args, **kwargs).to_dict()
            raw.pop("specification_digest")
            raw.update(installed_source_commit=pin["source_commit"], installed_bundle_digest=pin["bundle_digest"],
                       install_transaction=pin["install_transaction"], protocol_version=pin["protocol_version"],
                       schema_compatibility=pin["schema_compatibility"],
                       host_capability_probe_version=pin["host_capability_probe_version"])
            return CampaignSpec.from_dict(raw)

        def create_approved(self, spec):
            proposed = spec.to_dict()
            proposed.pop("specification_digest")
            for key in ("git_root", "worktree", "repository_remote", "branch", "base_sha",
                        "installed_source_commit", "installed_bundle_digest", "install_transaction",
                        "protocol_version", "schema_compatibility", "host_capability_probe_version"):
                proposed.pop(key)
            input_path, prepared_path = self.root / "proposed.json", self.root / "prepared.json"
            input_path.write_text(json.dumps(proposed), encoding="utf-8")
            report["prepare"] = cli("prepare", "--spec", str(input_path), "--repository", str(self.repo),
                                    "--output", str(prepared_path))
            require(report["prepare"]["state"] == "PROPOSED", "Prepare did not remain a proposal")
            require(cli("status")["campaign_count"] == 0, "Prepare unexpectedly admitted a campaign")
            report["admit"] = cli("admit", "--spec", str(prepared_path))
            report["approve"] = cli("approve", "--campaign-id", spec.campaign_id,
                                    "--specification-digest", report["prepare"]["specification_digest"])
            return self.store.get_snapshot(spec.campaign_id)

        def deliver(self, supervisor, host, head, artifact_path):
            super().deliver(supervisor, host, head, artifact_path)
            snapshot = self.store.get_snapshot("campaign")
            report["delivery"] = {
                "state": snapshot.state.value, "candidate_head": head,
                "delivered_head": head, "artifact_path": artifact_path,
                "artifact_sha256": sha256(self.repo / artifact_path),
                "validation_receipts": self.validation_receipts(),
                "validation_corrections": snapshot.node("node-1").validation_corrections,
                "operation_budgets": {item.token.value: item.consumed for item in snapshot.budgets},
                "model_usage": self.store.usage_summary("campaign"),
                "model_turns": "scripted FakeHost implementation and review results",
                "clock": "existing fixture clock",
                "measured_effects": ["Python acceptance failed then passed", "local Git commit", "local bare Git push and readback"],
            }

    fixture = InstalledDelivery("test_product_journey_corrects_validates_reviews_and_delivers_exact_local_git_head")
    result = unittest.TestResult()
    fixture.run(result)
    require(result.wasSuccessful(), "Installed delivery fixture failed:\n" + "\n".join(error for _, error in result.errors + result.failures))
    report["installed_engine_modules"] = module_sources()
    report["completed_status"] = cli("status", "--campaign-id", "campaign")
    require(report["completed_status"]["campaigns"][0]["campaign"]["state"] == "COMPLETED",
            "Installed CLI did not observe completed delivery")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-archive", type=Path, required=True)
    parser.add_argument("--baseline-commit", required=True)
    parser.add_argument("--candidate-archive", type=Path, required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--output", type=Path, default=Path("/out/supported-install-journey.json"))
    args = parser.parse_args()
    require(args.output.parent.resolve() == Path("/out"), "Receipt must go to the dedicated /out mount")
    account = disposable_account()  # Reject non-disposable execution before any write.
    report = {"protocol": "ccos-supported-install-journey-v1", "status": "failed", "account": account}
    try:
        require(args.baseline_commit != args.candidate_commit, "Upgrade requires distinct committed sources")
        with tempfile.TemporaryDirectory(prefix="supported-install-", dir="/tmp") as temp:
            base = Path(temp)
            baseline = unpack(args.baseline_archive, args.baseline_commit, base / "baseline")
            candidate = unpack(args.candidate_archive, args.candidate_commit, base / "candidate")
            report["sources"] = {name: {key: value for key, value in source.items() if key != "root"}
                                 for name, source in (("baseline", baseline), ("candidate", candidate))}
            report["initial_install"] = install(baseline)
            report["initial_doctor"] = cli("doctor")
            require(report["initial_doctor"]["integrity"]["status"] == "ok", "Initial store integrity failed")
            require(cli("status")["campaign_count"] == 0, "Fresh installation contains a campaign")
            report["upgrade"] = install(candidate)
            require(report["upgrade"]["runtime_pin"]["source_commit"] == args.candidate_commit,
                    "Upgrade did not activate candidate source")
            report["upgraded_doctor"] = cli("doctor")
            require(report["upgraded_doctor"]["integrity"]["status"] == "ok", "Upgraded integrity failed")
            require(len(report["upgraded_doctor"]["recorded_installations"]) == 2,
                    "Upgrade did not preserve both canonical installation records")
            prerequisite = report["upgraded_doctor"]["entry_prerequisites"]["canonical_router"]
            require(prerequisite["available"] is False, "Fixture unexpectedly has a canonical router")
            report["routed_skill_entry"] = {"status": "prerequisite_blocked", "prerequisite": prerequisite}
            installed_delivery(candidate, report)
            pointer = CODEX / ".coding-os-install/current.json"
            before = sha256(pointer)
            report["reinstall"] = install(candidate)
            require(sha256(pointer) == before, "Same-source reinstall changed committed transaction")
            report["final_doctor"] = cli("doctor")
            report["uninstall"] = run_json(["bash", str(candidate["root"] / "scripts/uninstall.sh")], cwd=ACCOUNT_PROFILE)
            require(not INSTALLED.exists() and STATE.is_file(), "Removal did not preserve state and remove installed support")
            require(not (CODEX / "skills/codex-coding-os-master").exists(), "Managed skill survived uninstall")
            require(not (CODEX / "capability-routing").exists(), "Journey activated a router")
            with sqlite3.connect(STATE) as database:
                require(database.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "Retained state failed integrity check")
            report["removal_diagnostics"] = {"installed_launcher_exists": LAUNCHER.exists(),
                                             "retained_state_integrity": "ok",
                                             "pointer_status": json.loads(pointer.read_text())["status"]}
            require(report["removal_diagnostics"]["pointer_status"] == "uninstalled", "Removal pointer is not terminal")
            report["status"] = "passed"
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "receipt": str(args.output),
                      "routed_skill_entry": "prerequisite_blocked", "native_model_usage": "not_measured"}))


if __name__ == "__main__":
    main()
