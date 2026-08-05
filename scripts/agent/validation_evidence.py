#!/usr/bin/env python3
"""Validate trusted-runner evidence shape without executing a command.

Execution belongs exclusively to ``campaign_engine.evidence``.  This retained
entry point can only validate a previously recorded receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


PROTOCOL = "ccos-validation-execution-v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class EvidenceShapeError(ValueError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def validate_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "protocol_version",
        "executable",
        "executable_sha256",
        "arguments",
        "working_directory",
        "environment_names",
        "timeout_seconds",
        "output_limit_bytes",
        "candidate_head",
        "head_after",
        "status_before_sha256",
        "status_after_sha256",
        "exit_code",
        "timed_out",
        "output_limited",
        "stdout_sha256",
        "stderr_sha256",
        "stdout",
        "stderr",
        "required_exit_code",
        "passed",
        "duration_ms",
        "evidence_sha256",
    }
    if set(raw) != required:
        raise EvidenceShapeError(
            f"validation evidence fields differ; missing={sorted(required-set(raw))}, extra={sorted(set(raw)-required)}"
        )
    if raw["protocol_version"] != PROTOCOL:
        raise EvidenceShapeError("validation evidence protocol is unsupported")
    for key in (
        "executable_sha256",
        "status_before_sha256",
        "status_after_sha256",
        "stdout_sha256",
        "stderr_sha256",
        "evidence_sha256",
    ):
        if not isinstance(raw[key], str) or not SHA256_RE.fullmatch(raw[key]):
            raise EvidenceShapeError(f"{key} must be one lowercase SHA256")
    for key in ("candidate_head", "head_after"):
        if not isinstance(raw[key], str) or not SHA_RE.fullmatch(raw[key]):
            raise EvidenceShapeError(f"{key} must be one lowercase Git SHA")
    if raw["candidate_head"] != raw["head_after"]:
        raise EvidenceShapeError("validation evidence contains a head race")
    if not isinstance(raw["arguments"], list) or not all(
        isinstance(item, str) for item in raw["arguments"]
    ):
        raise EvidenceShapeError("arguments must be a string array")
    if not isinstance(raw["environment_names"], list) or not all(
        isinstance(item, str) for item in raw["environment_names"]
    ):
        raise EvidenceShapeError("environment_names must be a string array")
    for key in (
        "exit_code",
        "required_exit_code",
        "duration_ms",
        "timeout_seconds",
        "output_limit_bytes",
    ):
        if isinstance(raw[key], bool) or not isinstance(raw[key], int):
            raise EvidenceShapeError(f"{key} must be an integer")
    if raw["timeout_seconds"] <= 0 or raw["output_limit_bytes"] <= 0:
        raise EvidenceShapeError("timeout_seconds and output_limit_bytes must be positive")
    if raw["duration_ms"] < 0:
        raise EvidenceShapeError("duration_ms must not be negative")
    for key in ("timed_out", "output_limited", "passed"):
        if not isinstance(raw[key], bool):
            raise EvidenceShapeError(f"{key} must be boolean")
    for key in ("executable", "working_directory", "stdout", "stderr"):
        if not isinstance(raw[key], str):
            raise EvidenceShapeError(f"{key} must be a string")
    for stream in ("stdout", "stderr"):
        digest = hashlib.sha256(raw[stream].encode("utf-8")).hexdigest()
        if digest != raw[f"{stream}_sha256"]:
            raise EvidenceShapeError(f"{stream}_sha256 differs from the retained output")
    actual_pass = (
        raw["exit_code"] == raw["required_exit_code"]
        and not raw["timed_out"]
        and not raw["output_limited"]
        and raw["candidate_head"] == raw["head_after"]
        and raw["status_before_sha256"] == raw["status_after_sha256"]
    )
    if raw["passed"] is not actual_pass:
        raise EvidenceShapeError("passed conflicts with process exit or execution invariants")
    body = dict(raw)
    supplied = body.pop("evidence_sha256")
    if hashlib.sha256(_canonical_json(body)).hexdigest() != supplied:
        raise EvidenceShapeError("validation evidence digest is invalid")
    return dict(raw)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate recorded campaign validation evidence.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        value = json.loads(args.path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise EvidenceShapeError("validation evidence file must contain an object")
        validated = validate_record(value)
    except (OSError, UnicodeError, json.JSONDecodeError, EvidenceShapeError) as exc:
        payload = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        print(json.dumps(payload, sort_keys=True) if args.json else f"INVALID: {exc}")
        return 2
    payload = {"ok": True, "protocol_version": validated["protocol_version"], "passed": validated["passed"]}
    print(json.dumps(payload, sort_keys=True) if args.json else "VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
