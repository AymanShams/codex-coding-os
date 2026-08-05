#!/usr/bin/env python3
"""Validate pull request body control metadata."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


REQUIRED_HEADINGS = (
    "## Requested outcome",
    "## Scope",
    "## Validation",
    "## Review",
    "## Publication authority",
)

REQUIRED_FIELDS = (
    "Campaign ID",
    "Objective",
    "Objective kind",
    "Exact base SHA",
    "Exact candidate head SHA",
    "Specification digest",
    "Changed paths",
    "Acceptance criteria",
    "Explicit non-goals",
    "Frozen candidate diff digest",
    "Required review cohort",
    "Frozen finding IDs",
    "Repair used",
    "Closure result",
    "Allowed effects",
    "Exact operation IDs",
)

VALIDATION_CHECKS = (
    "product-quality",
    "product-tests",
    "product-acceptance",
    "requested-documentation",
    "coding-os-adapter",
    "pr-metadata",
)

OBJECTIVE_KINDS = {"PRODUCT_CODE", "PRODUCT_DOCUMENTATION", "CONTROL_RUNTIME"}
ALLOWED_EFFECTS = {"PUSH", "CREATE_PULL_REQUEST", "UPSERT_COMMENT", "MERGE"}


def field_value(body: str, label: str) -> str:
    escaped_label = re.escape(label)
    match = re.search(rf"(?im)^\s*(?:-\s*)?{escaped_label}:[^\S\r\n]*([^\r\n]+)", body)
    return match.group(1).strip() if match else ""


def valid_sha(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}", value))


def valid_digest(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{64}", value))


def validation_row(body: str, check: str) -> tuple[str, str, str] | None:
    match = re.search(
        rf"(?im)^\|\s*{re.escape(check)}\s*\|\s*([^|]*)\|\s*([^|]*)\|\s*([^|]*)\|\s*$",
        body,
    )
    if match is None:
        return None
    return tuple(item.strip() for item in match.groups())  # type: ignore[return-value]


def validate_body(
    body: str,
    *,
    template_mode: bool = False,
    expected_current_head: str = "",
) -> list[str]:
    failures: list[str] = []

    for heading in REQUIRED_HEADINGS:
        if heading not in body:
            failures.append(f"PR body is missing required section: {heading}")
    for label in REQUIRED_FIELDS:
        if not re.search(rf"(?im)^\s*(?:-\s*)?{re.escape(label)}:", body):
            failures.append(f"PR body is missing required field: {label}")
    for check in VALIDATION_CHECKS:
        if validation_row(body, check) is None:
            failures.append(f"PR body is missing validation row: {check}")

    if template_mode:
        return failures

    values = {label: field_value(body, label) for label in REQUIRED_FIELDS}
    for label, value in values.items():
        if not value:
            failures.append(f"PR body field must not be blank: {label}")

    if values["Objective kind"] not in OBJECTIVE_KINDS:
        failures.append("Objective kind is not one supported campaign objective kind.")
    for label in ("Exact base SHA", "Exact candidate head SHA"):
        if not valid_sha(values[label]):
            failures.append(f"{label} must be one full 40-character Git SHA.")
    candidate_head = values["Exact candidate head SHA"]
    if (
        expected_current_head
        and valid_sha(candidate_head)
        and candidate_head.casefold() != expected_current_head.casefold()
    ):
        failures.append(
            "Exact candidate head SHA must match the live pull request head SHA."
        )
    for label in ("Specification digest", "Frozen candidate diff digest"):
        if not valid_digest(values[label]):
            failures.append(f"{label} must be one full SHA-256 digest.")
    if values["Repair used"] not in {"Yes", "No"}:
        failures.append("Repair used must be exactly Yes or No.")
    effects = {
        item.strip()
        for item in values["Allowed effects"].split(",")
        if item.strip()
    }
    if not effects or not effects.issubset(ALLOWED_EFFECTS):
        failures.append("Allowed effects contains an empty or unsupported effect set.")
    operation_ids = [
        item.strip()
        for item in values["Exact operation IDs"].split(",")
        if item.strip()
    ]
    if not operation_ids or any(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", item) is None
        for item in operation_ids
    ):
        failures.append("Exact operation IDs must contain stable operation identifiers.")
    for check in VALIDATION_CHECKS:
        row = validation_row(body, check)
        if row is not None and any(not item for item in row):
            failures.append(
                f"Validation row must record command, evidence ID, and result: {check}"
            )

    return failures


def fixture_body(current_head: str, *, specification_digest: str | None = None) -> str:
    digest = specification_digest if specification_digest is not None else "b" * 64
    validation = "\n".join(
        f"| {check} | command-{check} | evidence-{check} | PASS |"
        for check in VALIDATION_CHECKS
    )
    return f"""## Requested outcome

- Campaign ID: campaign-1
- Objective: verify the exact campaign candidate
- Objective kind: CONTROL_RUNTIME
- Exact base SHA: {'a' * 40}
- Exact candidate head SHA: {current_head}
- Specification digest: {digest}

## Scope

- Changed paths: scripts/agent/campaign_engine/**
- Acceptance criteria: exact-head replacement passes
- Explicit non-goals: no unrelated product changes

## Validation

| Check | Command | Evidence ID | Result |
|---|---|---|---|
{validation}

## Review

- Frozen candidate diff digest: {'c' * 64}
- Required review cohort: reviewer-a, reviewer-b
- Frozen finding IDs: None
- Repair used: No
- Closure result: PASS

## Publication authority

- Allowed effects: PUSH, CREATE_PULL_REQUEST, MERGE
- Exact operation IDs: push-1, pr-1, merge-1
"""


def run_self_test(template_path: Path | None = None) -> None:
    head = "a" * 40
    valid_failures = validate_body(
        fixture_body(head),
        expected_current_head=head,
    )
    if valid_failures:
        raise AssertionError(f"valid PR body fixture failed: {'; '.join(valid_failures)}")

    stale_head_failures = validate_body(
        fixture_body(head),
        expected_current_head="b" * 40,
    )
    if not any("live pull request head SHA" in failure for failure in stale_head_failures):
        raise AssertionError("stale candidate head fixture did not fail closed")

    digest_failures = validate_body(fixture_body(head, specification_digest="short"))
    if not any("Specification digest" in failure for failure in digest_failures):
        raise AssertionError("abbreviated specification digest fixture did not fail closed")

    incomplete_validation = fixture_body(head).replace(
        "| product-tests | command-product-tests | evidence-product-tests | PASS |",
        "| product-tests | command-product-tests |  | PASS |",
    )
    validation_failures = validate_body(incomplete_validation)
    if not any("product-tests" in failure for failure in validation_failures):
        raise AssertionError("blank validation evidence fixture did not fail closed")

    if template_path is not None:
        template_failures = validate_body(
            template_path.read_text(encoding="utf-8"), template_mode=True
        )
        if template_failures:
            raise AssertionError(f"PR template failed template-mode validation: {'; '.join(template_failures)}")


def read_github_event_body(event_path: Path, *, enforce_draft: bool) -> tuple[str, str, str | None]:
    event = json.loads(event_path.read_text(encoding="utf-8"))
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, dict):
        return "", "", "pr-body check skipped because this is not a pull_request event."
    if pull_request.get("draft") is True and not enforce_draft:
        return "", "", "pr-body check skipped because this pull request is still a draft."
    head = pull_request.get("head")
    head_sha = str(head.get("sha") or "") if isinstance(head, dict) else ""
    return str(pull_request.get("body") or ""), head_sha, None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--body-file")
    parser.add_argument("--enforce-draft", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    template_path = repo_root / ".github" / "pull_request_template.md"

    if args.self_test:
        run_self_test(template_path)
        print("pr-body check self-test passed")
        return 0

    source = ""
    expected_current_head = ""
    if args.body_file:
        source = args.body_file
        body = Path(args.body_file).read_text(encoding="utf-8")
    elif os.environ.get("GITHUB_EVENT_PATH"):
        source = "GitHub pull request event"
        body, expected_current_head, skipped = read_github_event_body(
            Path(os.environ["GITHUB_EVENT_PATH"]),
            enforce_draft=args.enforce_draft,
        )
        if skipped:
            print(skipped)
            return 0
    else:
        print("pr-body check skipped outside GitHub Actions.")
        return 0

    failures = validate_body(
        body,
        expected_current_head=expected_current_head,
    )
    if failures:
        print(f"pr-body check failed for {source}", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"pr-body check passed for {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
