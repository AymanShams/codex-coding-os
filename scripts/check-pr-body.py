#!/usr/bin/env python3
"""Check PR metadata consistency. Publication authority stays with its caller."""

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

COMMON_FIELDS = (
    "Work mode",
    "Objective",
    "Exact base SHA",
    "Exact candidate head SHA",
    "Changed paths",
    "Acceptance criteria",
    "Explicit non-goals",
    "Allowed effects",
)

MANUAL_FIELDS = (
    "Requested authority",
    "Review result",
)

CAMPAIGN_FIELDS = (
    "Campaign ID",
    "Objective kind",
    "Specification digest",
    "Frozen candidate diff digest",
    "Required review cohort",
    "Frozen finding IDs",
    "Repair used",
    "Closure result",
    "Exact operation IDs",
)

REQUIRED_FIELDS = COMMON_FIELDS + MANUAL_FIELDS + CAMPAIGN_FIELDS

VALIDATION_CHECKS = (
    "product-quality",
    "product-tests",
    "product-acceptance",
    "requested-documentation",
    "coding-os-adapter",
    "pr-metadata",
)

OBJECTIVE_KINDS = {"PRODUCT_CODE", "PRODUCT_DOCUMENTATION", "CONTROL_RUNTIME"}
ALLOWED_EFFECTS = {"PUSH", "CREATE_PULL_REQUEST", "UPSERT_COMMENT", "MERGE", "EXACT_FILE_REPLACE"}
WORK_MODES = {"MANUAL", "CAMPAIGN"}


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


def validation_rows(body: str) -> list[tuple[str, str, str, str]]:
    """Read only the validation section, excluding the header and separator."""
    section = re.search(r"(?ms)^## Validation\s*\n(.*?)(?=^## |\Z)", body)
    if section is None:
        return []
    rows = []
    for line in section.group(1).splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = tuple(item.strip() for item in line.strip().strip("|").split("|"))
        if len(cells) == 4 and cells[0].lower() != "check" and not all(
            re.fullmatch(r"[-: ]+", cell) for cell in cells
        ):
            rows.append(cells)
    return rows


def validate_body(
    body: str,
    *,
    template_mode: bool = False,
    expected_current_head: str = "",
    expected_base_sha: str = "",
    expected_work_mode: str = "",
    expected_campaign_id: str = "",
    expected_specification_digest: str = "",
) -> list[str]:
    failures: list[str] = []

    for heading in REQUIRED_HEADINGS:
        if heading not in body:
            failures.append(f"PR body is missing required section: {heading}")
    values = {label: field_value(body, label) for label in REQUIRED_FIELDS}
    work_mode = values["Work mode"]
    required = COMMON_FIELDS + (CAMPAIGN_FIELDS if work_mode == "CAMPAIGN" else MANUAL_FIELDS)
    if template_mode:
        required = REQUIRED_FIELDS
    for label in REQUIRED_FIELDS:
        count = len(re.findall(rf"(?im)^\s*(?:-\s*)?{re.escape(label)}:", body))
        if count == 0 and label in required:
            failures.append(f"PR body is missing required field: {label}")
        if count > 1:
            failures.append(f"PR body has duplicate field: {label}")
    if template_mode:
        for check in VALIDATION_CHECKS:
            if validation_row(body, check) is None:
                failures.append(f"PR body is missing validation row: {check}")
        return failures
    for label in required:
        if not values[label]:
            failures.append(f"PR body field must not be blank: {label}")

    if work_mode not in WORK_MODES:
        failures.append("Work mode must be exactly MANUAL or CAMPAIGN.")
    if expected_work_mode and expected_work_mode not in WORK_MODES:
        failures.append("Expected work mode is unsupported.")
    if expected_work_mode and work_mode != expected_work_mode:
        failures.append("Work mode must match the independently supplied execution mode.")
    if expected_campaign_id or expected_specification_digest:
        if work_mode != "CAMPAIGN":
            failures.append("Campaign-bound work cannot use MANUAL metadata.")
        for label, expected in (
            ("Campaign ID", expected_campaign_id),
            ("Specification digest", expected_specification_digest),
        ):
            if expected and values[label] != expected:
                failures.append(f"{label} must match the admitted campaign.")
    if work_mode == "MANUAL":
        for label in CAMPAIGN_FIELDS:
            if values[label]:
                failures.append(f"MANUAL metadata must omit campaign field: {label}")
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
    if expected_base_sha and values["Exact base SHA"].casefold() != expected_base_sha.casefold():
        failures.append("Exact base SHA must match the independently supplied base SHA.")
    if work_mode == "CAMPAIGN":
        if values["Objective kind"] not in OBJECTIVE_KINDS:
            failures.append("Objective kind is not one supported campaign objective kind.")
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
    if work_mode == "CAMPAIGN":
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
            if validation_row(body, check) is None:
                failures.append(f"PR body is missing validation row: {check}")
    rows = validation_rows(body)
    if not rows:
        failures.append("Validation must record at least one relevant check.")
    for row in rows:
        if any(not item for item in row):
            failures.append(
                f"Validation row must record check, command, evidence, and result: {row[0]}"
            )

    return failures


def fixture_body(current_head: str, *, specification_digest: str | None = None) -> str:
    digest = specification_digest if specification_digest is not None else "b" * 64
    validation = "\n".join(
        f"| {check} | command-{check} | evidence-{check} | PASS |"
        for check in VALIDATION_CHECKS
    )
    return f"""## Requested outcome

- Work mode: CAMPAIGN
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


def manual_fixture_body(current_head: str) -> str:
    return f"""## Requested outcome

- Work mode: MANUAL
- Requested authority: User requested the local metadata correction and a draft pull request.
- Objective: Allow an authorized manual change to report its actual evidence.
- Exact base SHA: {'a' * 40}
- Exact candidate head SHA: {current_head}

## Scope

- Changed paths: scripts/check-pr-body.py, tests/test_pr_body.py
- Acceptance criteria: Manual metadata passes and cannot replace campaign evidence.
- Explicit non-goals: No merge or release.

## Validation

| Check | Command | Evidence | Result |
|---|---|---|---|
| metadata tests | python -B -m unittest tests.test_pr_body | local command output | PASS |

## Review

- Review result: Exact diff reviewed. No unresolved findings.

## Publication authority

- Allowed effects: PUSH, CREATE_PULL_REQUEST
"""


def run_self_test(template_path: Path | None = None) -> None:
    head = "a" * 40
    valid_failures = validate_body(
        fixture_body(head),
        expected_current_head=head,
    )
    if valid_failures:
        raise AssertionError(f"valid PR body fixture failed: {'; '.join(valid_failures)}")
    if validate_body(manual_fixture_body(head), expected_current_head=head):
        raise AssertionError("valid manual PR body fixture failed")
    if not validate_body(manual_fixture_body(head), expected_work_mode="CAMPAIGN"):
        raise AssertionError("manual relabeling bypassed independently bound campaign mode")

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
    parser.add_argument("--expected-work-mode", choices=sorted(WORK_MODES))
    parser.add_argument("--expected-head", default="")
    parser.add_argument("--expected-base-sha", default="")
    parser.add_argument("--expected-campaign-id", default="")
    parser.add_argument("--expected-specification-digest", default="")
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
    expected_current_head = args.expected_head
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
        if any((args.expected_work_mode, args.expected_head, args.expected_base_sha,
                args.expected_campaign_id, args.expected_specification_digest)):
            print("pr-body check requires a body when independent bindings are supplied", file=sys.stderr)
            return 1
        print("pr-body check skipped outside GitHub Actions.")
        return 0

    failures = validate_body(
        body,
        expected_current_head=expected_current_head,
        expected_base_sha=args.expected_base_sha,
        expected_work_mode=args.expected_work_mode or "",
        expected_campaign_id=args.expected_campaign_id,
        expected_specification_digest=args.expected_specification_digest,
    )
    if failures:
        print(f"pr-body check failed for {source}", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"pr-body metadata consistency check passed for {source}; this is not publication approval")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
