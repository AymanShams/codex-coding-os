#!/usr/bin/env python3
"""Run the pinned fork-based mutation gate for critical reducer invariants."""

from __future__ import annotations

import argparse
import fnmatch
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Iterable, Mapping


MUTMUT_VERSION = "3.7.0"
CRITICAL_RUN_PATTERNS = (
    "scripts.agent.campaign_engine.reducer.x__check_fence__mutmut_*",
    "scripts.agent.campaign_engine.reducer.x__consume_budget__mutmut_*",
    "scripts.agent.campaign_engine.reducer.x__consume_automated__mutmut_*",
    "scripts.agent.campaign_engine.reducer.x__finish_revision__mutmut_*",
)
CRITICAL_RESULT_PATTERNS = (
    "scripts.agent.campaign_engine.reducer.x__check_fence__mutmut_*",
    "scripts.agent.campaign_engine.reducer.x__consume_budget__mutmut_*",
    "scripts.agent.campaign_engine.reducer.x__consume_automated__mutmut_*",
    "scripts.agent.campaign_engine.reducer.x__finish_revision__mutmut_*",
)

if CRITICAL_RUN_PATTERNS != CRITICAL_RESULT_PATTERNS:
    raise RuntimeError("mutation run and receipt patterns must remain identical")


EQUIVALENT_MUTANTS = {
    "scripts.agent.campaign_engine.reducer.x__consume_budget__mutmut_12": (
        "Changes the private found sentinel from False to None. Both values remain "
        "false until a matching budget sets the sentinel to True, so every return "
        "value and exception is identical."
    ),
    "scripts.agent.campaign_engine.reducer.x__finish_revision__mutmut_8": (
        "Changes only the message in the digest guard. For valid CampaignSpec values, "
        "a digest difference already makes the preceding full-spec equality guard fail."
    ),
    "scripts.agent.campaign_engine.reducer.x__finish_revision__mutmut_9": (
        "Changes only the message in the digest guard. For valid CampaignSpec values, "
        "a digest difference already makes the preceding full-spec equality guard fail."
    ),
    "scripts.agent.campaign_engine.reducer.x__finish_revision__mutmut_10": (
        "Changes only the message in the digest guard. For valid CampaignSpec values, "
        "a digest difference already makes the preceding full-spec equality guard fail."
    ),
}


def parse_results(raw: str) -> dict[str, str]:
    results: dict[str, str] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if ": " not in line:
            continue
        name, status = line.rsplit(": ", 1)
        if name and status:
            results[name] = status
    return results


def evaluate_results(
    results: dict[str, str],
    patterns: Iterable[str] = CRITICAL_RESULT_PATTERNS,
    equivalent_mutants: Mapping[str, str] | None = None,
) -> dict[str, object]:
    patterns = tuple(patterns)
    if equivalent_mutants is None:
        equivalent_mutants = (
            EQUIVALENT_MUTANTS
            if patterns == CRITICAL_RESULT_PATTERNS
            else {}
        )
    selected: dict[str, str] = {}
    missing: list[str] = []
    for pattern in patterns:
        matches = {
            name: status
            for name, status in results.items()
            if fnmatch.fnmatch(name, pattern)
        }
        if not matches:
            missing.append(pattern)
        selected.update(matches)
    equivalent_survivors = {
        name: equivalent_mutants[name]
        for name, status in selected.items()
        if status == "survived" and name in equivalent_mutants
    }
    unexpected_non_killed = {
        name: status
        for name, status in selected.items()
        if status != "killed" and name not in equivalent_survivors
    }
    missing_equivalent_ids = sorted(set(equivalent_mutants) - set(selected))
    return {
        "selected_mutants": len(selected),
        "killed_mutants": sum(status == "killed" for status in selected.values()),
        "equivalent_survivors": equivalent_survivors,
        "missing_patterns": missing,
        "missing_equivalent_ids": missing_equivalent_ids,
        "non_killed": unexpected_non_killed,
        "passed": (
            bool(selected)
            and not missing
            and not missing_equivalent_ids
            and not unexpected_non_killed
        ),
    }


def _run(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    sys.stdout.write(completed.stdout)
    return completed


def run_gate(root: Path, max_children: int) -> dict[str, object]:
    if os.name != "posix" or not hasattr(os, "fork"):
        raise RuntimeError("mutmut 3.7.0 requires a POSIX host with fork support")
    installed = metadata.version("mutmut")
    if installed != MUTMUT_VERSION:
        raise RuntimeError(
            f"mutmut version mismatch: expected {MUTMUT_VERSION}, found {installed}"
        )
    executable = shutil.which("mutmut")
    if executable is None:
        raise RuntimeError("the mutmut executable is unavailable")
    run = _run(
        [
            executable,
            "run",
            "--max-children",
            str(max_children),
            *CRITICAL_RUN_PATTERNS,
        ],
        root,
    )
    if run.returncode != 0:
        raise RuntimeError(f"mutmut run failed with exit code {run.returncode}")
    results = _run([executable, "results", "--all", "true"], root)
    if results.returncode != 0:
        raise RuntimeError(
            f"mutmut results failed with exit code {results.returncode}"
        )
    receipt = evaluate_results(parse_results(results.stdout))
    if receipt["passed"] is not True:
        raise RuntimeError(
            "critical reducer mutation gate failed: "
            + json.dumps(receipt, sort_keys=True)
        )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run real mutation testing for critical campaign reducer invariants."
    )
    parser.add_argument("--max-children", type=int, default=2)
    args = parser.parse_args(argv)
    if args.max_children < 1:
        parser.error("--max-children must be positive")
    root = Path(__file__).resolve().parents[1]
    try:
        receipt = run_gate(root, args.max_children)
    except (metadata.PackageNotFoundError, RuntimeError) as exc:
        print(f"MUTATION_GATE_FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
