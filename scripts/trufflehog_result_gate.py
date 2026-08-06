#!/usr/bin/env python3
"""Fail-closed, redacted evaluation for TruffleHog JSONL output."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, BinaryIO, Iterable, Mapping

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
PATH_RE = re.compile(r"^tests/[0-9A-Za-z._/-]+$")


class GateError(ValueError):
    """The scanner output or allowlist violates the release contract."""


def _entry_key(
    detector: str,
    path: str,
    commit: str,
    verified: bool,
    raw_sha256: str,
) -> tuple[str, str, str, bool, str]:
    return detector, path, commit, verified, raw_sha256


def load_history_allowlist(path: Path) -> dict[tuple[str, str, str, bool, str], str]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError("history allowlist is unavailable or invalid JSON") from exc
    if not isinstance(document, Mapping):
        raise GateError("history allowlist must be an object")
    if (
        document.get("schema_version") != 1
        or document.get("scope") != "immutable-git-history-only"
    ):
        raise GateError("history allowlist has an unsupported contract")
    entries = document.get("entries")
    if not isinstance(entries, list) or not entries:
        raise GateError("history allowlist must contain at least one exact entry")
    lookup: dict[tuple[str, str, str, bool, str], str] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise GateError("history allowlist entry must be an object")
        detector = str(entry.get("detector", "")).strip()
        relative_path = str(entry.get("path", "")).replace("\\", "/").strip()
        commit = str(entry.get("commit", "")).casefold()
        raw_sha256 = str(entry.get("raw_sha256", "")).casefold()
        verified = entry.get("expected_verified")
        reason = str(entry.get("reason", "")).strip()
        if (
            not detector
            or not PATH_RE.fullmatch(relative_path)
            or ".." in relative_path
            or "*" in relative_path
            or not SHA_RE.fullmatch(commit)
            or not DIGEST_RE.fullmatch(raw_sha256)
            or not isinstance(verified, bool)
            or len(reason) < 40
        ):
            raise GateError("history allowlist contains an invalid or broad entry")
        key = _entry_key(detector, relative_path, commit, verified, raw_sha256)
        if key in lookup:
            raise GateError("history allowlist contains a duplicate entry")
        lookup[key] = reason
    return lookup


def _parse_findings(lines: Iterable[str]) -> tuple[list[Mapping[str, Any]], list[str]]:
    findings: list[Mapping[str, Any]] = []
    errors: list[str] = []
    for index, line in enumerate(lines, start=1):
        if index == 1:
            if line.startswith("\ufeff"):
                line = line[1:]
            elif line.startswith("\xef\xbb\xbf"):
                # Windows PowerShell 5.1 writes a UTF-8 BOM to native stdin,
                # which a legacy locale can decode as these three characters.
                line = line[3:]
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"scanner output line {index} is not JSON")
            continue
        if not isinstance(value, Mapping):
            errors.append(f"scanner output line {index} is not an object")
            continue
        findings.append(value)
    return findings, errors


def decode_scanner_output(stream: BinaryIO) -> list[str]:
    """Decode scanner JSONL as UTF-8 while accepting one stream-start BOM."""

    try:
        return stream.read().decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise GateError("scanner output is not valid UTF-8") from exc


def evaluate(
    lines: Iterable[str],
    *,
    mode: str,
    allowlist: Mapping[tuple[str, str, str, bool, str], str] | None = None,
) -> dict[str, Any]:
    findings, errors = _parse_findings(lines)
    matched: set[tuple[str, str, str, bool, str]] = set()
    summaries: list[dict[str, Any]] = []
    for index, finding in enumerate(findings, start=1):
        detector = str(finding.get("DetectorName", "")).strip()
        raw = finding.get("Raw")
        verified = finding.get("Verified")
        metadata = finding.get("SourceMetadata")
        data = metadata.get("Data") if isinstance(metadata, Mapping) else None
        source = data.get("Git" if mode == "history" else "Filesystem") if isinstance(data, Mapping) else None
        if (
            not detector
            or not isinstance(raw, str)
            or not isinstance(verified, bool)
            or not isinstance(source, Mapping)
        ):
            errors.append(f"scanner finding {index} has an invalid result shape")
            continue
        relative_path = str(source.get("file", "")).replace("\\", "/").strip()
        line_number = source.get("line")
        if not relative_path:
            errors.append(f"scanner finding {index} has no source path")
            continue
        summary = {
            "detector": detector,
            "path": relative_path,
            "line": line_number,
            "verified": verified,
        }
        summaries.append(summary)
        if mode == "current":
            errors.append(
                f"current source has a {detector} finding at {relative_path}:{line_number}"
            )
            continue
        commit = str(source.get("commit", "")).casefold()
        raw_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        key = _entry_key(detector, relative_path, commit, verified, raw_sha256)
        if allowlist is None or key not in allowlist:
            errors.append(
                f"history has an unreviewed {detector} finding at "
                f"{relative_path}:{line_number} commit={commit or '<missing>'}"
            )
            continue
        matched.add(key)
    if mode == "history" and allowlist is not None:
        for key in sorted(set(allowlist) - matched):
            detector, relative_path, commit, _, _ = key
            errors.append(
                f"history allowlist entry was not observed: "
                f"detector={detector} path={relative_path} commit={commit}"
            )
    return {
        "ok": not errors,
        "mode": mode,
        "finding_count": len(findings),
        "allowlisted_count": len(matched),
        "findings": summaries,
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("current", "history"), required=True)
    parser.add_argument("--allowlist")
    parser.add_argument("--candidate-head")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "history":
        candidate_head = str(args.candidate_head or "").casefold()
        if not SHA_RE.fullmatch(candidate_head):
            result = {"ok": False, "errors": ["history gate requires one exact candidate head"]}
            print(json.dumps(result, sort_keys=True))
            return 1
        if not args.allowlist:
            result = {"ok": False, "errors": ["history gate requires an exact allowlist"]}
            print(json.dumps(result, sort_keys=True))
            return 1
        try:
            allowlist = load_history_allowlist(Path(args.allowlist))
        except GateError as exc:
            print(json.dumps({"ok": False, "errors": [str(exc)]}, sort_keys=True))
            return 1
    else:
        if args.allowlist or args.candidate_head:
            print(
                json.dumps(
                    {"ok": False, "errors": ["current gate does not accept history authority"]},
                    sort_keys=True,
                )
            )
            return 1
        allowlist = None
    try:
        scanner_lines = decode_scanner_output(sys.stdin.buffer)
    except GateError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, sort_keys=True))
        return 1
    result = evaluate(scanner_lines, mode=args.mode, allowlist=allowlist)
    if args.mode == "history":
        result["candidate_head"] = str(args.candidate_head).casefold()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
