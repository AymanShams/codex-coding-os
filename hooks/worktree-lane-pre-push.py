#!/usr/bin/env python3
"""Optional Git pre-push hook for active parallel worktree lanes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    script = Path("scripts/agent/worktree_lanes.py")
    if not script.exists():
        return 0
    return subprocess.call([sys.executable, str(script), "validate", "--current"])


if __name__ == "__main__":
    raise SystemExit(main())
