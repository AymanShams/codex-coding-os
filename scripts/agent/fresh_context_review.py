#!/usr/bin/env python3
"""Create a detached fresh-context review worktree and print a review prompt."""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path


DEFAULT_CONTEXT_FILES = (
    "AGENTS.md",
    "README.md",
    "project-documentation-manifest.json",
    "docs/delivery/current-state.md",
    "docs/delivery/active-slice-manifest.json",
    "templates/fresh-context-review.md",
)


def run_git(args: list[str], cwd: Path | None = None, check: bool = False) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed"
        raise RuntimeError(message)
    return result.stdout.strip()


def repo_root() -> Path:
    root = run_git(["rev-parse", "--show-toplevel"], check=True)
    return Path(root).resolve()


def slug(value: str) -> str:
    safe = []
    for char in value.lower():
        safe.append(char if char.isalnum() else "-")
    normalized = "".join(safe).strip("-")
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    return normalized or "review"


def is_dirty(root: Path) -> bool:
    return bool(run_git(["status", "--porcelain"], cwd=root))


def resolve_commit(root: Path, ref: str) -> str:
    return run_git(["rev-parse", ref], cwd=root, check=True)


def default_worktree_path(root: Path, label: str, commit: str) -> Path:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    return root / ".codex" / "fresh-context-reviews" / f"{stamp}-{slug(label)}-{commit[:8]}"


def existing_context_files(root: Path, extra_files: list[str]) -> list[str]:
    candidates = [*DEFAULT_CONTEXT_FILES, *extra_files]
    seen: set[str] = set()
    existing = []
    for raw in candidates:
        value = raw.replace("\\", "/").strip()
        if not value or value in seen:
            continue
        if (root / value).exists():
            existing.append(value)
            seen.add(value)
    return existing


def create_worktree(root: Path, path: Path, commit: str) -> None:
    if path.exists():
        raise RuntimeError(f"review worktree path already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    run_git(["worktree", "add", "--detach", str(path), commit], cwd=root, check=True)


def build_prompt(root: Path, worktree: Path, commit: str, label: str, context_files: list[str]) -> str:
    context_lines = "\n".join(f"- `{path}`" for path in context_files) or "- No standard context files found."
    return f"""# Fresh-Context Review Request

Review label: {label}
Review worktree: {worktree}
Reviewed commit: {commit}
Source repo: {root}

## Required Setup
1. Open the review worktree above.
2. Read the context files that exist:
{context_lines}
3. Run `git status --short`.
4. Review the actual diff and behavior against the controlling sources.

## Review Rules
- Treat this as review-only unless the user explicitly authorizes fixes.
- Do not merge, push, rewrite history, or mutate the source worktree.
- Separate blocking defects from improvement suggestions.
- Challenge assumptions, missing tests, source drift, scope creep, and validation gaps.

## Required Output Marker
End the review with:

```text
[CODEX-REVIEW: COMPLETE]
Reviewed commit: {commit}
Verdict: APPROVED | CHANGES_REQUIRED | BLOCKED
```
"""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD", help="Git ref to review. Default: HEAD.")
    parser.add_argument("--label", default="fresh-context-review", help="Short review label.")
    parser.add_argument("--worktree", help="Explicit review worktree path.")
    parser.add_argument("--context-file", action="append", default=[], help="Additional context file to list.")
    parser.add_argument("--print-only", action="store_true", help="Print the prompt without creating a worktree.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    root = repo_root()
    commit = resolve_commit(root, args.ref)
    worktree = Path(args.worktree).resolve() if args.worktree else default_worktree_path(root, args.label, commit)
    context_files = existing_context_files(root, args.context_file)

    if is_dirty(root) and not args.print_only:
        raise RuntimeError(
            "source worktree has uncommitted changes. Commit, stash, or rerun with --print-only so the review scope is explicit."
        )
    if not args.print_only:
        create_worktree(root, worktree, commit)

    print(build_prompt(root, worktree, commit, args.label, context_files))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"fresh_context_review failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
