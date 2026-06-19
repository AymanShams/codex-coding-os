#!/usr/bin/env python3
"""Build or query the optional capability index."""

from __future__ import annotations

import argparse
import sys

from capability_index import ensure_index, is_session_only_candidate, query_index


def candidate_visibility_for_query(query: str) -> str:
    prompt = query.lower()
    if "reference-only" in prompt or "reference only" in prompt or "reference material" in prompt:
        return "include_reference"
    if "project-local" in prompt or "project local" in prompt or "pilot" in prompt:
        return "include_project_local_pilots"
    if any(term in prompt for term in ("install", "installable", "available plugin", "available plugins", "candidate")):
        return "include_install_candidates"
    return "include_reference"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Build or query the Codex capability index.")
    parser.add_argument("--refresh", action="store_true", help="Rebuild the index before returning.")
    parser.add_argument("--query", help="Return the highest-scoring capabilities for this task.")
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Also append gated session-only candidate/reference entries after active matches.",
    )
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    index = ensure_index(force=args.refresh)
    if args.query:
        for entry in query_index(
            args.query,
            limit=max(1, min(args.limit, 8)),
            include_candidates=args.include_inactive,
            candidate_visibility=candidate_visibility_for_query(args.query) if args.include_inactive else "active_only",
        ):
            description = entry.get("description", "").replace("\n", " ")[:220]
            role = "session-only-support" if is_session_only_candidate(entry) else "active"
            print(
                f"{role} | {entry.get('kind')} | {entry.get('status')} | "
                f"{entry.get('name')} | {description}"
            )
        return 0

    summary = index["summary"]
    for key in (
        "skill_files",
        "configured_mcps",
        "disabled_skills",
    ):
        print(f"{key}={summary[key]}")
    print(f"active_exact_local_duplicates={len(summary['active_exact_local_duplicate_names'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
