#!/usr/bin/env python3
"""Build or query the optional capability index."""

from __future__ import annotations

import argparse

from capability_index import ensure_index, query_index


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or query the Codex capability index.")
    parser.add_argument("--refresh", action="store_true", help="Rebuild the index before returning.")
    parser.add_argument("--query", help="Return the highest-scoring capabilities for this task.")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    index = ensure_index(force=args.refresh)
    if args.query:
        for entry in query_index(args.query, limit=max(1, min(args.limit, 8))):
            description = entry.get("description", "").replace("\n", " ")[:220]
            print(
                f"{entry.get('kind')} | {entry.get('status')} | "
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
