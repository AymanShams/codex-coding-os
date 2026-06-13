#!/usr/bin/env python3
"""Optional Git pre-push hook for active parallel worktree lanes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def active_lane_marker_exists() -> bool:
    cwd = Path.cwd().resolve()
    return any((base / ".codex" / "parallel-lane.json").exists() for base in [cwd, *cwd.parents])


def main() -> int:
    script = Path("scripts/agent/worktree_lanes.py")
    if not script.exists():
        if active_lane_marker_exists():
            print("parallel lane marker found, but scripts/agent/worktree_lanes.py is missing")
            return 1
        return 0
    return subprocess.call([sys.executable, str(script), "validate", "--current"])


if __name__ == "__main__":
    raise SystemExit(main())
