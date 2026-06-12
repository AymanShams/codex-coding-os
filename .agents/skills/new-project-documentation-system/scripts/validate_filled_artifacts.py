#!/usr/bin/env python3
"""Reject unresolved placeholders in filled project documentation artifacts."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


PATTERNS = {
    "TODO marker": re.compile(r"\bTODO\b", re.IGNORECASE),
    "TBD marker": re.compile(r"\bTBD\b", re.IGNORECASE),
    "starter draft marker": re.compile(r"\bstarter draft\b", re.IGNORECASE),
    "template token": re.compile(r"\{\{[^{}\r\n]+\}\}"),
    "agent placeholder": re.compile(r"\[Agent must[^\]\r\n]*\]", re.IGNORECASE),
    "vague as-needed wording": re.compile(r"\bas needed\b", re.IGNORECASE),
}
SUPPORTED = {".md", ".txt", ".json", ".yaml", ".yml"}


def scan(path: Path) -> list[str]:
    errors: list[str] = []
    targets = [path] if path.is_file() else sorted(path.rglob("*"))
    for target in targets:
        if not target.is_file() or target.suffix.lower() not in SUPPORTED:
            continue
        text = target.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    errors.append(f"{target}:{line_number}: {label}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path, help="Filled artifact files or directories to validate")
    args = parser.parse_args()
    errors: list[str] = []
    for path in args.paths:
        if not path.exists():
            errors.append(f"{path}: path does not exist")
        else:
            errors.extend(scan(path))
    if errors:
        print("FAIL: filled artifact placeholder validation failed")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS: filled artifacts contain no blocked placeholder patterns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
