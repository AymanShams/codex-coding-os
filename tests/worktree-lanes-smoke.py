#!/usr/bin/env python3
"""Smoke tests for fail-closed parallel worktree lane orchestration."""

from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agent" / "worktree_lanes.py"
TEMPLATE = ROOT / ".agents" / "skills" / "new-project-documentation-system" / "assets" / "project-documentation-manifest.template.json"


def run(args: list[str], cwd: Path, expected: int) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode != expected:
        raise AssertionError(
            f"Expected exit {expected}, got {result.returncode}: {' '.join(args)}\n{result.stdout}"
        )
    return result


def git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project, check=True, stdout=subprocess.DEVNULL)


def git_output(project: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=project,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def create_directory_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def load_lane_module():
    spec = importlib.util.spec_from_file_location("worktree_lanes_under_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load worktree lane module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ready_manifest(project: Path) -> None:
    manifest = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    manifest["code_allowed"] = True
    manifest["next_action"] = "code"
    manifest["approvals"] = {
        "source_authority": True,
        "material_decisions": True,
        "controlled_docs": True,
        "tdd": True,
        "coding_start": True,
    }
    for phase in manifest["phases"].values():
        phase["status"] = "completed"
    (project / "project-documentation-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def assert_no_local_paths(path: Path, project: Path) -> None:
    forbidden = {str(project), project.as_posix()}
    for file_path in path.rglob("*"):
        if not file_path.is_file():
            continue
        text = file_path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker and marker in text:
                raise AssertionError(f"Commit-safe audit file contains local path {marker}: {file_path}")


def write_lane_handoff(
    path: Path,
    manifest: dict[str, object],
    lane: dict[str, object],
    worktree: Path,
    *,
    tested_head: str | None = None,
    evidence_reference: str = "File: `src/feature.py`",
    repository: str | None = None,
    branch: str | None = None,
    base_commit: str | None = None,
    proves: str = "The local lane contract and fixture validation passed for this exact working tree.",
    merge_authorized: bool = False,
    merge_authority_source: str = "Not authorized for this fixture",
) -> None:
    tested_head = tested_head or git_output(worktree, "rev-parse", "HEAD")
    dirty_state = "dirty" if git_output(worktree, "status", "--porcelain", "-uall") else "clean"
    repository = repository or str(manifest["repository_identity"])
    branch = branch or str(lane["branch"])
    base_commit = base_commit or str(manifest["base_commit"])
    merge_authorized_text = "Yes" if merge_authorized else "No"
    path.write_text(
        f"""# Parallel Lane Handoff

## Lane Identity
- Repository: `{repository}`
- Run ID: `{manifest['run_id']}`
- Lane: `{lane['slug']}`
- Branch: `{branch}`
- Base commit: `{base_commit}`
- Tested head: `{tested_head}`
- Dirty state: {dirty_state}

## Environment And Authority Boundaries
- Platform: Windows test environment
- Runtime versions: Python {sys.version_info.major}.{sys.version_info.minor}
- Tool versions: Git test fixture
- Accounts or credentials boundary: local fixture with no accounts or credentials
- Secrets recorded: No
- Production mutation: No
- Deploy authorized: No
- Merge authorized: {merge_authorized_text}
- Merge authority source: {merge_authority_source}
- Release authorized: No

## Work Completed
- Updated the bounded feature fixture.

## Changed Files
- `src/feature.py`

## Acceptance Criteria
- [x] The feature fixture contains the expected changed behavior.

## Commands And Actions Performed
- Command: `python -m pytest` | Result: Pass | Evidence: exit 0 and the fixture test passed

## Evidence References
- {evidence_reference}

## Retest Result
- Result: Pass
- Evidence: Current tested head and dirty-state fixture were checked after the final edit.

## What This Proves
- {proves}

## What This Does Not Prove
- Local checks do not prove browser or UI behavior, deployment success, production behavior, or complete security.

## Remaining Risks Or Blockers
- None identified after the bounded fixture retest.

## Exact Next Action
- Parent orchestrator reviews the exact lane diff before any integration decision.
""",
        encoding="utf-8",
    )


def main() -> int:
    python = sys.executable
    lane_module = load_lane_module()
    for assignment_name in (
        "MYSQL_PASSWORD",
        "DATABASE_PASSWORD",
        "SERVICE_PASSWD",
        "SERVICE_PWD",
        "SERVICE_TOKEN",
        "SERVICE_SECRET",
        "SERVICE_API_KEY",
        "SERVICE_ACCESS_KEY",
        "SERVICE_PRIVATE_KEY",
        "SERVICE_CLIENT_SECRET",
    ):
        if not lane_module.contains_potential_secret(
            assignment_name + "=" + "C0mpl3x" + "Credential!Value"
        ):
            raise AssertionError(f"Sensitive assignment suffix was not detected: {assignment_name}")
    for safe_assignment in (
        "MYSQL_PASSWORD=[REDACTED]",
        "DATABASE_PASSWORD=***",
        "PASSWORD_POLICY=minimum-length",
        "password=minimum-length",
    ):
        if lane_module.contains_potential_secret(safe_assignment):
            raise AssertionError(f"Safe assignment was misclassified: {safe_assignment}")
    local_remote_fixtures = (
        r"C:\Users\private-user\source\repository.git?token=ignored#fragment",
        "/home/private-user/source/repository.git?token=ignored#fragment",
        "file://fixture-user:fixture-credential@localhost/C:/private/repository.git?token=ignored#fragment",
    )
    for remote in local_remote_fixtures:
        identity = lane_module.sanitize_remote_identity(remote)
        if not identity.startswith("local-repository:"):
            raise AssertionError(f"Filesystem remote was not fingerprinted: {identity}")
        if any(marker.lower() in identity.lower() for marker in (
            "private-user", "private", "fixture-user", "fixture-credential", "token", "fragment", "c:", "/home/"
        )):
            raise AssertionError(f"Filesystem remote identity leaked local metadata: {identity}")
    if lane_module.sanitize_remote_identity("git@github.com:public/example.git") != "github.com:public/example.git":
        raise AssertionError("SSH remote identity sanitization regressed")

    with tempfile.TemporaryDirectory(prefix="coding-os-worktree-lanes-") as temp:
        project = Path(temp) / "project"
        project.mkdir()
        git(project, "init")
        git(project, "config", "user.email", "codex@example.invalid")
        git(project, "config", "user.name", "Codex Smoke Test")
        remote_with_metadata = (
            "https://" + "fixture-user" + ":" + "fixture-credential" +
            "@example.invalid/public/project.git?private=ignored#fragment"
        )
        git(project, "remote", "add", "origin", remote_with_metadata)

        shutil.copyfile(TEMPLATE, project / "project-documentation-manifest.json")
        (project / "src").mkdir()
        (project / "tests").mkdir()
        (project / "src" / "feature.py").write_text("print('feature')\n", encoding="utf-8")
        (project / "tests" / "test_feature.py").write_text("def test_feature():\n    assert True\n", encoding="utf-8")
        git(project, "add", ".")
        git(project, "commit", "-m", "initial")

        blocked = run(
            [python, str(SCRIPT), "evaluate", "--task", "parallel auth feature", "--risk", "high"],
            project,
            2,
        )
        if "workflow manifest" not in blocked.stdout:
            raise AssertionError(blocked.stdout)

        ready_manifest(project)
        (project / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        dirty = run(
            [python, str(SCRIPT), "evaluate", "--task", "parallel auth feature", "--risk", "high"],
            project,
            2,
        )
        if "dirty" not in dirty.stdout:
            raise AssertionError(dirty.stdout)
        (project / "dirty.txt").unlink()
        git(project, "add", "project-documentation-manifest.json")
        git(project, "commit", "-m", "allow coding")

        missing_approval = run(
            [
                python,
                str(SCRIPT),
                "create",
                "--task",
                "parallel auth feature",
                "--risk",
                "high",
                "--lane",
                "code|Implement code|src/feature.py||fresh-context|python -m pytest",
                "--lane",
                "tests|Harden tests|tests/test_feature.py||fresh-context|python -m pytest",
            ],
            project,
            2,
        )
        if "user approval" not in missing_approval.stdout:
            raise AssertionError(missing_approval.stdout)

        overlap = run(
            [
                python,
                str(SCRIPT),
                "create",
                "--task",
                "parallel auth feature",
                "--risk",
                "high",
                "--user-approved",
                "--lane",
                "code-a|Implement code A|src/feature.py||fresh-context|python -m pytest",
                "--lane",
                "code-b|Implement code B|src/feature.py||fresh-context|python -m pytest",
            ],
            project,
            2,
        )
        if "overlap" not in overlap.stdout:
            raise AssertionError(overlap.stdout)

        auto_without_ack = run(
            [
                python,
                str(SCRIPT),
                "create",
                "--task",
                "parallel auth feature",
                "--risk",
                "high",
                "--user-approved",
                "--thread-mode",
                "auto",
                "--lane",
                "code|Implement code|src/feature.py||fresh-context|python -m pytest",
                "--lane",
                "tests|Harden tests|tests/test_feature.py||fresh-context|python -m pytest",
            ],
            project,
            2,
        )
        if "auto thread mode" not in auto_without_ack.stdout:
            raise AssertionError(auto_without_ack.stdout)

        planned = run(
            [
                python,
                str(SCRIPT),
                "plan",
                "--task",
                "parallel auth feature",
                "--risk",
                "high",
                "--run-id",
                "smoke",
                "--lane",
                "code|Implement code|src/feature.py||fresh-context|python -m pytest",
                "--lane",
                "tests|Harden tests|tests/test_feature.py||fresh-context|python -m pytest",
            ],
            project,
            0,
        )
        if "create --from-run smoke" not in planned.stdout:
            raise AssertionError(planned.stdout)

        created = run(
            [
                python,
                str(SCRIPT),
                "create",
                "--from-run",
                "smoke",
                "--user-approved",
            ],
            project,
            0,
        )
        if "Created parallel worktree run" not in created.stdout:
            raise AssertionError(created.stdout)

        audit_root = project / "docs" / "delivery" / "parallel-worktrees" / "smoke"
        assert_no_local_paths(audit_root, project)

        lane_manifest = project / ".codex" / "parallel-worktrees" / "smoke" / "lane-manifest.json"
        manifest = json.loads(lane_manifest.read_text(encoding="utf-8"))
        if manifest["repository_identity"] != "https://example.invalid/public/project.git":
            raise AssertionError("Runtime manifest did not sanitize the live remote identity")
        if not manifest.get("base_branch"):
            raise AssertionError("Runtime manifest did not record the live target branch")
        code_lane = next(lane for lane in manifest["lanes"] if lane["slug"] == "code")
        code_tree = Path(code_lane["worktree_path"])
        (code_tree / "src" / "feature.py").write_text("print('changed')\n", encoding="utf-8")
        validated = run([python, str(SCRIPT), "validate", "--run-id", "smoke"], project, 0)
        if "CONTRACT PASS" not in validated.stdout:
            raise AssertionError(validated.stdout)

        handoff_root = project / ".codex" / "parallel-worktrees" / "smoke" / "handoffs"

        original_repository = str(manifest["repository_identity"])
        manifest["repository_identity"] = "spoofed-repository"
        lane_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        spoofed_repository = handoff_root / "code-spoofed-repository.md"
        write_lane_handoff(spoofed_repository, manifest, code_lane, code_tree)
        rejected_repository = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "ready",
                "--handoff",
                str(spoofed_repository),
            ],
            project,
            2,
        )
        if "does not match the live repository" not in rejected_repository.stdout:
            raise AssertionError(rejected_repository.stdout)
        manifest["repository_identity"] = original_repository
        lane_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        original_branch = str(code_lane["branch"])
        code_lane["branch"] = "lane/spoofed-branch"
        lane_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        spoofed_branch = handoff_root / "code-spoofed-branch.md"
        write_lane_handoff(spoofed_branch, manifest, code_lane, code_tree)
        rejected_branch = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "ready",
                "--handoff",
                str(spoofed_branch),
            ],
            project,
            2,
        )
        if "does not match the live worktree branch" not in rejected_branch.stdout:
            raise AssertionError(rejected_branch.stdout)
        code_lane["branch"] = original_branch
        lane_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        original_base = str(manifest["base_commit"])
        manifest["base_commit"] = "HEAD"
        lane_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        abbreviated_base = handoff_root / "code-abbreviated-base.md"
        write_lane_handoff(abbreviated_base, manifest, code_lane, code_tree)
        rejected_base = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "ready",
                "--handoff",
                str(abbreviated_base),
            ],
            project,
            2,
        )
        if "full 40-character SHA" not in rejected_base.stdout:
            raise AssertionError(rejected_base.stdout)
        manifest["base_commit"] = original_base
        lane_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        bad_evidence = handoff_root / "code-bad-evidence.md"
        write_lane_handoff(
            bad_evidence,
            manifest,
            code_lane,
            code_tree,
            evidence_reference="File: `{{artifact}}`",
        )
        rejected_evidence = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "ready",
                "--handoff",
                str(bad_evidence),
            ],
            project,
            2,
        )
        if "placeholder" not in rejected_evidence.stdout:
            raise AssertionError(rejected_evidence.stdout)

        outside_root = Path(temp) / "outside-evidence"
        outside_root.mkdir()
        outside_file = outside_root / "result.txt"
        outside_file.write_text("outside repository evidence\n", encoding="utf-8")

        absolute_evidence = handoff_root / "code-absolute-evidence.md"
        write_lane_handoff(
            absolute_evidence,
            manifest,
            code_lane,
            code_tree,
            evidence_reference=f"File: `{outside_file}`",
        )
        rejected_absolute = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "ready",
                "--handoff",
                str(absolute_evidence),
            ],
            project,
            2,
        )
        if "repository-relative path" not in rejected_absolute.stdout:
            raise AssertionError(rejected_absolute.stdout)

        escaped_reference = Path(os.path.relpath(outside_file, code_tree)).as_posix()
        relative_escape = handoff_root / "code-relative-escape.md"
        write_lane_handoff(
            relative_escape,
            manifest,
            code_lane,
            code_tree,
            evidence_reference=f"Artifact: `{escaped_reference}`",
        )
        rejected_relative = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "ready",
                "--handoff",
                str(relative_escape),
            ],
            project,
            2,
        )
        if "escapes the lane worktree" not in rejected_relative.stdout:
            raise AssertionError(rejected_relative.stdout)

        evidence_link = code_tree / "evidence-link"
        create_directory_link(evidence_link, outside_root)
        symlink_escape = handoff_root / "code-symlink-escape.md"
        write_lane_handoff(
            symlink_escape,
            manifest,
            code_lane,
            code_tree,
            evidence_reference="File: `evidence-link/result.txt`",
        )
        rejected_symlink = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "ready",
                "--handoff",
                str(symlink_escape),
            ],
            project,
            2,
        )
        if "escapes the lane worktree" not in rejected_symlink.stdout:
            raise AssertionError(rejected_symlink.stdout)
        if evidence_link.is_symlink():
            evidence_link.unlink()
        else:
            evidence_link.rmdir()

        missing_section = handoff_root / "code-missing-section.md"
        write_lane_handoff(missing_section, manifest, code_lane, code_tree)
        missing_section.write_text(
            missing_section.read_text(encoding="utf-8").replace(
                "## Retest Result", "## Retest Result Missing"
            ),
            encoding="utf-8",
        )
        rejected_missing = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "ready",
                "--handoff",
                str(missing_section),
            ],
            project,
            2,
        )
        if "missing required section: retest result" not in rejected_missing.stdout:
            raise AssertionError(rejected_missing.stdout)

        unredacted_secret = handoff_root / "code-unredacted-secret.md"
        secret_value = "FixtureSecret" + "-" + "7x9Q2m"
        secret_marker = "password" + "=" + secret_value
        write_lane_handoff(
            unredacted_secret,
            manifest,
            code_lane,
            code_tree,
            evidence_reference=(
                "Command: `python -m pytest` | Result: Pass | Evidence: " + secret_marker
            ),
        )
        rejected_secret = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "ready",
                "--handoff",
                str(unredacted_secret),
            ],
            project,
            2,
        )
        if "unredacted secret or credential" not in rejected_secret.stdout:
            raise AssertionError(rejected_secret.stdout)
        if secret_value in rejected_secret.stdout:
            raise AssertionError("Secret value was echoed by the validator")

        for assignment_name in ("MYSQL_PASSWORD", "DATABASE_PASSWORD"):
            prefixed_secret = handoff_root / f"code-{assignment_name.lower()}-secret.md"
            prefixed_secret_value = "C0mpl3x" + "Credential!Value"
            write_lane_handoff(
                prefixed_secret,
                manifest,
                code_lane,
                code_tree,
                evidence_reference=(
                    "Command: `python -m pytest` | Result: Pass | Evidence: "
                    + assignment_name
                    + "="
                    + prefixed_secret_value
                ),
            )
            rejected_prefixed_secret = run(
                [
                    python,
                    str(SCRIPT),
                    "close",
                    "--run-id",
                    "smoke",
                    "--lane",
                    "code",
                    "--status",
                    "ready",
                    "--handoff",
                    str(prefixed_secret),
                ],
                project,
                2,
            )
            if "unredacted secret or credential" not in rejected_prefixed_secret.stdout:
                raise AssertionError(
                    f"Prefixed secret assignment was accepted: {assignment_name}"
                )
            if prefixed_secret_value in rejected_prefixed_secret.stdout:
                raise AssertionError("Prefixed secret value was echoed by the validator")

        for protocol in ("mysql", "https"):
            credential_url_handoff = handoff_root / f"code-{protocol}-credential-url.md"
            credential_url_value = "fixture" + "-credential-value"
            credential_url = (
                f"{protocol}://fixture-user:{credential_url_value}@example.invalid/repository"
            )
            write_lane_handoff(
                credential_url_handoff,
                manifest,
                code_lane,
                code_tree,
                evidence_reference=(
                    "Command: `python -m pytest` | Result: Pass | Evidence: "
                    + credential_url
                ),
            )
            rejected_credential_url = run(
                [
                    python,
                    str(SCRIPT),
                    "close",
                    "--run-id",
                    "smoke",
                    "--lane",
                    "code",
                    "--status",
                    "ready",
                    "--handoff",
                    str(credential_url_handoff),
                ],
                project,
                2,
            )
            if "unredacted secret or credential" not in rejected_credential_url.stdout:
                raise AssertionError(f"Credential-bearing {protocol} URL was accepted")
            if credential_url_value in rejected_credential_url.stdout:
                raise AssertionError("Credential URL value was echoed by the validator")

        token_secret = handoff_root / "code-token-secret.md"
        token_value = "".join(("sk", "-", "A1b2C3d4E5f6G7h8I9j0K1l2"))
        write_lane_handoff(
            token_secret,
            manifest,
            code_lane,
            code_tree,
            evidence_reference=(
                "Command: `python -m pytest` | Result: Pass | Evidence: token " + token_value
            ),
        )
        rejected_token = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "ready",
                "--handoff",
                str(token_secret),
            ],
            project,
            2,
        )
        if "unredacted secret or credential" not in rejected_token.stdout:
            raise AssertionError(rejected_token.stdout)
        if token_value in rejected_token.stdout:
            raise AssertionError("Credential token was echoed by the validator")

        bad_identity = handoff_root / "code-bad-identity.md"
        write_lane_handoff(
            bad_identity,
            manifest,
            code_lane,
            code_tree,
            tested_head="0" * 40,
        )
        rejected_identity = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "ready",
                "--handoff",
                str(bad_identity),
            ],
            project,
            2,
        )
        if "identity mismatch" not in rejected_identity.stdout:
            raise AssertionError(rejected_identity.stdout)

        for index, overclaim in enumerate(
            (
                "Production behavior verified and the system is fully secure",
                "Production behavior verified for this release",
                "Production is safe; local tests passed",
                "Deployment is secure; local tests passed",
            ),
            start=1,
        ):
            overclaim_handoff = handoff_root / f"code-overclaim-{index}.md"
            write_lane_handoff(
                overclaim_handoff,
                manifest,
                code_lane,
                code_tree,
                proves=overclaim,
            )
            rejected_overclaim = run(
                [
                    python,
                    str(SCRIPT),
                    "close",
                    "--run-id",
                    "smoke",
                    "--lane",
                    "code",
                    "--status",
                    "ready",
                    "--handoff",
                    str(overclaim_handoff),
                ],
                project,
                2,
            )
            if "overclaims deployment, production, or security assurance" not in rejected_overclaim.stdout:
                raise AssertionError(rejected_overclaim.stdout)

        for index, bounded_claim in enumerate(
            (
                "Local security:check passed",
                "The local security check passed",
            ),
            start=1,
        ):
            bounded_handoff = handoff_root / f"code-bounded-local-security-{index}.md"
            write_lane_handoff(
                bounded_handoff,
                manifest,
                code_lane,
                code_tree,
                proves=bounded_claim,
            )
            bounded_result = run(
                [
                    python,
                    str(SCRIPT),
                    "close",
                    "--run-id",
                    "smoke",
                    "--lane",
                    "code",
                    "--status",
                    "ready",
                    "--handoff",
                    str(bounded_handoff),
                ],
                project,
                0,
            )
            if "Updated code to ready" not in bounded_result.stdout:
                raise AssertionError(bounded_result.stdout)

        unsupported_browser = handoff_root / "code-unsupported-browser.md"
        write_lane_handoff(
            unsupported_browser,
            manifest,
            code_lane,
            code_tree,
            proves="The browser UI flow was verified locally for this fixture.",
        )
        rejected_browser = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "ready",
                "--handoff",
                str(unsupported_browser),
            ],
            project,
            2,
        )
        if "requires supporting typed evidence" not in rejected_browser.stdout:
            raise AssertionError(rejected_browser.stdout)

        supported_browser = handoff_root / "code-supported-browser.md"
        write_lane_handoff(
            supported_browser,
            manifest,
            code_lane,
            code_tree,
            evidence_reference=(
                "Manual action: `Browser fixture flow` | Result: Pass | "
                "Evidence: observed the expected UI fixture state"
            ),
            proves="The browser UI fixture flow was verified locally.",
        )
        accepted_browser = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "ready",
                "--handoff",
                str(supported_browser),
            ],
            project,
            0,
        )
        if "Updated code to ready" not in accepted_browser.stdout:
            raise AssertionError(accepted_browser.stdout)

        valid_handoff = handoff_root / "code.md"
        write_lane_handoff(
            valid_handoff,
            manifest,
            code_lane,
            code_tree,
            proves="The local deployment configuration schema test passed for this fixture.",
        )
        redacted_marker = "password" + "=" + "[REDACTED]."
        valid_handoff.write_text(
            valid_handoff.read_text(encoding="utf-8").replace(
                "local fixture with no accounts or credentials",
                "local fixture with no accounts or credentials; redaction fixture "
                + redacted_marker
                + "; policy fixture password=minimum-length"
                + "; prefixed redactions MYSQL_PASSWORD=[REDACTED] and DATABASE_PASSWORD=***"
                + "; variable policy PASSWORD_POLICY=minimum-length"
                + "; redacted URLs mysql://fixture-user:[REDACTED]@example.invalid/schema"
                + " and https://fixture-user:***@example.invalid/repository",
            ),
            encoding="utf-8",
        )
        closed = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "ready",
                "--handoff",
                str(valid_handoff),
            ],
            project,
            0,
        )
        if "Updated code to ready" not in closed.stdout:
            raise AssertionError(closed.stdout)
        validated_ready = run([python, str(SCRIPT), "validate", "--run-id", "smoke"], project, 0)
        if "CONTRACT PASS" not in validated_ready.stdout:
            raise AssertionError(validated_ready.stdout)

        git(code_tree, "add", "src/feature.py")
        git(code_tree, "commit", "-m", "complete code lane fixture")

        unmerged_handoff = handoff_root / "code-unmerged.md"
        write_lane_handoff(
            unmerged_handoff,
            manifest,
            code_lane,
            code_tree,
            merge_authorized=True,
            merge_authority_source="Merge authorized by the independent fixture owner",
        )
        rejected_unmerged = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "merged",
                "--handoff",
                str(unmerged_handoff),
            ],
            project,
            2,
        )
        if "not merged into the live target branch" not in rejected_unmerged.stdout:
            raise AssertionError(rejected_unmerged.stdout)

        original_base_branch = str(manifest["base_branch"])
        manifest["base_branch"] = str(code_lane["branch"])
        lane_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        spoofed_target = handoff_root / "code-spoofed-target.md"
        write_lane_handoff(
            spoofed_target,
            manifest,
            code_lane,
            code_tree,
            merge_authorized=True,
            merge_authority_source="Merge authorized by the independent fixture owner",
        )
        rejected_target = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "merged",
                "--handoff",
                str(spoofed_target),
            ],
            project,
            2,
        )
        if "does not match the live parent target branch" not in rejected_target.stdout:
            raise AssertionError(rejected_target.stdout)
        manifest["base_branch"] = original_base_branch
        lane_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        git(project, "merge", "--ff-only", str(code_lane["branch"]))

        rejected_authority_sources = (
            "Not authorized for this lane",
            "The independent owner approved the merge, but that approval was later revoked.",
            "The reviewer authorized the merge before the approval was withdrawn.",
            "The maintainer approved the merge, then rescinded it by formal rescission.",
            "The release manager authorized the merge, but the authorization expired on review expiry.",
            "The owner approved the merge before that approval was canceled.",
            "The owner approved the merge before that approval was cancelled.",
            "The pull request reviewer approved the merge, but a later decision superseded that approval.",
        )
        for index, authority_source in enumerate(rejected_authority_sources, start=1):
            rejected_authority_handoff = handoff_root / f"code-rejected-authority-{index}.md"
            write_lane_handoff(
                rejected_authority_handoff,
                manifest,
                code_lane,
                code_tree,
                merge_authorized=True,
                merge_authority_source=authority_source,
            )
            rejected_authority = run(
                [
                    python,
                    str(SCRIPT),
                    "close",
                    "--run-id",
                    "smoke",
                    "--lane",
                    "code",
                    "--status",
                    "merged",
                    "--handoff",
                    str(rejected_authority_handoff),
                ],
                project,
                2,
            )
            if "affirmative, non-negated independent merge authority" not in rejected_authority.stdout:
                raise AssertionError(rejected_authority.stdout)

        merged_handoff = handoff_root / "code-merged.md"
        write_lane_handoff(
            merged_handoff,
            manifest,
            code_lane,
            code_tree,
            merge_authorized=True,
            merge_authority_source="Merge authorized by the independent fixture owner",
        )
        merged = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "smoke",
                "--lane",
                "code",
                "--status",
                "merged",
                "--handoff",
                str(merged_handoff),
            ],
            project,
            0,
        )
        if "Updated code to merged" not in merged.stdout:
            raise AssertionError(merged.stdout)

        (code_tree / "project-documentation-manifest.json").write_text("{}", encoding="utf-8")
        controlled = run([python, str(SCRIPT), "validate", "--run-id", "smoke"], project, 1)
        if "controlled source" not in controlled.stdout:
            raise AssertionError(controlled.stdout)

        no_origin = Path(temp) / "no-origin-project"
        no_origin.mkdir()
        git(no_origin, "init")
        git(no_origin, "config", "user.email", "codex@example.invalid")
        git(no_origin, "config", "user.name", "Codex Smoke Test")
        shutil.copyfile(TEMPLATE, no_origin / "project-documentation-manifest.json")
        (no_origin / "src").mkdir()
        (no_origin / "tests").mkdir()
        (no_origin / "src" / "feature.py").write_text("print('feature')\n", encoding="utf-8")
        (no_origin / "tests" / "test_feature.py").write_text(
            "def test_feature():\n    assert True\n", encoding="utf-8"
        )
        git(no_origin, "add", ".")
        git(no_origin, "commit", "-m", "initial")
        ready_manifest(no_origin)
        git(no_origin, "add", "project-documentation-manifest.json")
        git(no_origin, "commit", "-m", "allow coding")
        created_no_origin = run(
            [
                python,
                str(SCRIPT),
                "create",
                "--task",
                "parallel local fixture",
                "--risk",
                "high",
                "--run-id",
                "no-origin",
                "--user-approved",
                "--lane",
                "code|Implement code|src/feature.py||fresh-context|python -m pytest",
                "--lane",
                "tests|Harden tests|tests/test_feature.py||fresh-context|python -m pytest",
            ],
            no_origin,
            0,
        )
        if "Created parallel worktree run" not in created_no_origin.stdout:
            raise AssertionError(created_no_origin.stdout)
        no_origin_manifest_path = (
            no_origin / ".codex" / "parallel-worktrees" / "no-origin" / "lane-manifest.json"
        )
        no_origin_manifest = json.loads(no_origin_manifest_path.read_text(encoding="utf-8"))
        no_origin_identity = str(no_origin_manifest["repository_identity"])
        if not no_origin_identity.startswith("local-repository:"):
            raise AssertionError("No-origin repository did not use a privacy-safe local identity")
        if str(no_origin).lower() in no_origin_identity.lower() or no_origin.name.lower() in no_origin_identity.lower():
            raise AssertionError("No-origin repository identity leaked its local path or basename")
        no_origin_lane = next(
            lane for lane in no_origin_manifest["lanes"] if lane["slug"] == "code"
        )
        no_origin_tree = Path(no_origin_lane["worktree_path"])
        (no_origin_tree / "src" / "feature.py").write_text("print('changed')\n", encoding="utf-8")
        no_origin_handoff = (
            no_origin / ".codex" / "parallel-worktrees" / "no-origin" / "handoffs" / "code.md"
        )
        write_lane_handoff(
            no_origin_handoff,
            no_origin_manifest,
            no_origin_lane,
            no_origin_tree,
        )
        closed_no_origin = run(
            [
                python,
                str(SCRIPT),
                "close",
                "--run-id",
                "no-origin",
                "--lane",
                "code",
                "--status",
                "ready",
                "--handoff",
                str(no_origin_handoff),
            ],
            no_origin,
            0,
        )
        if "Updated code to ready" not in closed_no_origin.stdout:
            raise AssertionError(closed_no_origin.stdout)

    print("Worktree lane smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
