#!/usr/bin/env python3
"""Validate inert, typed evidence against one live repository checkout.

The validator reads recorded observations. It never executes their commands,
assesses the truth of prose claims, or grants review or lifecycle authority.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


PROTOCOL_VERSION = 1
PROOF_TYPES = {
    "COMMAND_EXIT",
    "TEST_REPORT",
    "STATIC_ANALYSIS",
    "BUILD_ARTIFACT",
    "MANUAL_OBSERVATION",
    "GITHUB_REQUIRED_CHECK",
    "GITHUB_REVIEW_RECORD",
    "CANONICAL_SNAPSHOT",
}
RESULTS = {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}
SCOPE_KINDS = {"REPOSITORY", "FILE_SET", "COMMAND", "CHECK", "REVIEW", "RUNTIME_BEHAVIOR"}
AUTHORITY_SOURCE_TYPES = {
    "USER_INSTRUCTION",
    "REPOSITORY_RULE",
    "RUN_ENVELOPE",
    "CI_WORKFLOW",
    "REVIEW_CONTRACT",
}
WORKING_TREE_STATES = {"clean", "dirty"}
FULL_LOWER_SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_TOP_LEVEL = {
    "schema_version",
    "evidence_id",
    "proof_type",
    "result",
    "scope",
    "repository",
    "exact_head",
    "environment",
    "authority",
    "observations",
    "proves",
    "does_not_prove",
}


def run_git(repo_root: Path, *args: str) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_text_list(value: object, field: str, errors: list[str], *, allow_empty: bool = False) -> None:
    if not isinstance(value, list):
        errors.append(f"{field} must be an array")
        return
    if not value and not allow_empty:
        errors.append(f"{field} must contain at least one item")
    for index, item in enumerate(value):
        if not nonempty_text(item):
            errors.append(f"{field}[{index}] must be non-empty text")


def repository_has_credentials(value: str) -> bool:
    if "://" in value:
        parsed = urlsplit(value)
        return parsed.username is not None or parsed.password is not None
    return "@" in value.split(":", 1)[0]


def canonical_repository(value: str) -> str:
    value = value.strip()
    if "://" in value:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        port = f":{parsed.port}" if parsed.port else ""
        path = parsed.path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        if hostname:
            return f"{hostname}{port}/{path}".rstrip("/")
        return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))
    scp_match = re.fullmatch(r"(?:[^@/:\s]+@)?([^/:\s]+):(.+)", value)
    if scp_match:
        host = scp_match.group(1).lower()
        path = scp_match.group(2).strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return f"{host}/{path}".rstrip("/")
    normalized = value.rstrip("/")
    return normalized[:-4] if normalized.endswith(".git") else normalized


def validate_shape(data: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["evidence root must be an object"]

    missing = sorted(REQUIRED_TOP_LEVEL - set(data))
    extra = sorted(set(data) - REQUIRED_TOP_LEVEL)
    for field in missing:
        errors.append(f"missing required field: {field}")
    for field in extra:
        errors.append(f"unknown top-level field: {field}")

    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not nonempty_text(data.get("evidence_id")):
        errors.append("evidence_id must be non-empty text")
    if data.get("proof_type") not in PROOF_TYPES:
        errors.append(f"proof_type must be one of: {', '.join(sorted(PROOF_TYPES))}")
    if data.get("result") not in RESULTS:
        errors.append(f"result must be one of: {', '.join(sorted(RESULTS))}")
    if not nonempty_text(data.get("repository")):
        errors.append("repository must be non-empty text")
    elif repository_has_credentials(str(data["repository"])):
        errors.append("repository must not contain credentials")
    exact_head = data.get("exact_head")
    if not isinstance(exact_head, str) or FULL_LOWER_SHA.fullmatch(exact_head) is None:
        errors.append("exact_head must be a full lowercase 40-character Git SHA")

    scope = data.get("scope")
    if not isinstance(scope, dict):
        errors.append("scope must be an object")
    else:
        if set(scope) != {"kind", "targets"}:
            errors.append("scope must contain only kind and targets")
        if scope.get("kind") not in SCOPE_KINDS:
            errors.append(f"scope.kind must be one of: {', '.join(sorted(SCOPE_KINDS))}")
        validate_text_list(scope.get("targets"), "scope.targets", errors)

    environment = data.get("environment")
    if not isinstance(environment, dict):
        errors.append("environment must be an object")
    else:
        if set(environment) != {"platform", "runtimes", "working_tree"}:
            errors.append("environment must contain only platform, runtimes, and working_tree")
        if not nonempty_text(environment.get("platform")):
            errors.append("environment.platform must be non-empty text")
        validate_text_list(environment.get("runtimes"), "environment.runtimes", errors)
        if environment.get("working_tree") not in WORKING_TREE_STATES:
            errors.append("environment.working_tree must be clean or dirty")

    authority = data.get("authority")
    if not isinstance(authority, dict):
        errors.append("authority must be an object")
    else:
        if set(authority) != {"source_type", "reference", "scope"}:
            errors.append("authority must contain only source_type, reference, and scope")
        if authority.get("source_type") not in AUTHORITY_SOURCE_TYPES:
            errors.append(f"authority.source_type must be one of: {', '.join(sorted(AUTHORITY_SOURCE_TYPES))}")
        for field in ("reference", "scope"):
            if not nonempty_text(authority.get(field)):
                errors.append(f"authority.{field} must be non-empty text")

    observations = data.get("observations")
    if not isinstance(observations, list):
        errors.append("observations must be an array")
    else:
        for index, observation in enumerate(observations):
            if not isinstance(observation, dict):
                errors.append(f"observations[{index}] must be an object")
                continue
            if set(observation) != {"command", "exit_code", "fact"}:
                errors.append(f"observations[{index}] must contain only command, exit_code, and fact")
            if not nonempty_text(observation.get("command")):
                errors.append(f"observations[{index}].command must be non-empty text")
            if isinstance(observation.get("exit_code"), bool) or not isinstance(observation.get("exit_code"), int):
                errors.append(f"observations[{index}].exit_code must be an integer")
            if not nonempty_text(observation.get("fact")):
                errors.append(f"observations[{index}].fact must be non-empty text")

    validate_text_list(data.get("proves"), "proves", errors)
    validate_text_list(data.get("does_not_prove"), "does_not_prove", errors)
    return errors


def validate_evidence(path: Path, repo_root: Path) -> dict[str, Any]:
    errors: list[str] = []
    data: object = None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"evidence file does not exist: {path}")
    except json.JSONDecodeError as exc:
        errors.append(f"evidence file is malformed JSON: line {exc.lineno}, column {exc.colno}")
    except OSError as exc:
        errors.append(f"cannot read evidence file: {exc}")

    if data is not None:
        errors.extend(validate_shape(data))

    identity_match = False
    head_match = False
    working_tree_match = False
    live_repository = "unavailable"
    live_head = "unavailable"
    live_working_tree = "unavailable"

    if not repo_root.is_dir():
        errors.append(f"repo root is not a directory: {repo_root}")
    else:
        code, live_repository, detail = run_git(repo_root, "remote", "get-url", "origin")
        if code != 0 or not live_repository:
            errors.append(f"cannot read repository identity from origin: {detail or 'origin is missing'}")
            live_repository = "unavailable"
        else:
            live_repository = canonical_repository(live_repository)
        code, live_head, detail = run_git(repo_root, "rev-parse", "--verify", "HEAD")
        if code != 0 or FULL_LOWER_SHA.fullmatch(live_head) is None:
            errors.append(f"cannot read full live HEAD: {detail or live_head or 'HEAD is missing'}")
            live_head = "unavailable"
        code, status, detail = run_git(repo_root, "status", "--porcelain=v1", "-uall")
        if code != 0:
            errors.append(f"cannot read live working tree: {detail or 'git status failed'}")
        else:
            live_working_tree = "dirty" if status else "clean"

    if isinstance(data, dict):
        repository = data.get("repository")
        if isinstance(repository, str) and live_repository != "unavailable" and not repository_has_credentials(repository):
            identity_match = canonical_repository(repository) == canonical_repository(live_repository)
            if not identity_match:
                errors.append("repository identity does not match the live origin")
        exact_head = data.get("exact_head")
        if isinstance(exact_head, str) and FULL_LOWER_SHA.fullmatch(exact_head) and live_head != "unavailable":
            head_match = exact_head == live_head
            if not head_match:
                errors.append("exact_head does not match the full live HEAD")
        environment = data.get("environment")
        if isinstance(environment, dict) and live_working_tree != "unavailable":
            recorded_tree = environment.get("working_tree")
            working_tree_match = recorded_tree == live_working_tree
            if recorded_tree in WORKING_TREE_STATES and not working_tree_match:
                errors.append("environment.working_tree does not match the live working tree")
            if data.get("result") == "PASS" and live_working_tree != "clean":
                errors.append("PASS evidence requires an actually clean working tree")
            if data.get("result") == "PASS" and recorded_tree != "clean":
                errors.append("PASS evidence must record environment.working_tree as clean")

    return {
        "protocol_version": PROTOCOL_VERSION,
        "valid": not errors,
        "identity_match": identity_match,
        "head_match": head_match,
        "working_tree_match": working_tree_match,
        "claim_truth_assessed": False,
        "lifecycle_authority": False,
        "live_repository": live_repository,
        "live_head": live_head,
        "live_working_tree": live_working_tree,
        "errors": errors,
    }


def command_validate(args: argparse.Namespace) -> int:
    output = validate_evidence(Path(args.file), Path(args.repo_root).resolve())
    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print("VALIDATION EVIDENCE: PASS" if output["valid"] else "VALIDATION EVIDENCE: FAIL")
        print(f"Repository identity match: {output['identity_match']}")
        print(f"Exact head match: {output['head_match']}")
        print(f"Working tree match: {output['working_tree_match']}")
        print("Claim truth assessed: false")
        print("Lifecycle authority: false")
        for error in output["errors"]:
            print(f"- {error}")
    return 0 if output["valid"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate inert typed validation evidence")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--file", required=True)
    validate.add_argument("--repo-root", required=True)
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=command_validate)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
