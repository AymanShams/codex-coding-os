#!/usr/bin/env python3
"""Thin local hook adapter for the installed campaign engine.

No lifecycle logic is duplicated here.  The hook delegates only when the host
supplies the complete exact campaign identity tuple.  Manual work and partial
or inherited environments are no-ops.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    names = (
        "CCOS_CAMPAIGN_ID",
        "CCOS_ACTOR_ID",
        "CCOS_LEASE_ID",
        "CCOS_AUTHORITY_EPOCH",
        "CCOS_CANCELLATION_EPOCH",
        "CCOS_FENCING_EPOCH",
        "CCOS_REPOSITORY_ROOT",
        "CCOS_HOOK_ACTION",
    )
    identity = {name: os.environ.get(name, "").strip() for name in names}
    if not all(identity.values()):
        return 0
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    cli = codex_home / "coding-os" / "scripts" / "agent" / "campaign_engine" / "cli.py"
    if not cli.is_file():
        print("CODING_OS_ENGINE_UNAVAILABLE", file=sys.stderr)
        return 78
    completed = subprocess.run(
        (
            sys.executable,
            "-B",
            str(cli),
            "--json",
            "authorize-action",
            "--campaign-id",
            identity["CCOS_CAMPAIGN_ID"],
            "--actor-id",
            identity["CCOS_ACTOR_ID"],
            "--lease-id",
            identity["CCOS_LEASE_ID"],
            "--authority-epoch",
            identity["CCOS_AUTHORITY_EPOCH"],
            "--cancellation-epoch",
            identity["CCOS_CANCELLATION_EPOCH"],
            "--fencing-epoch",
            identity["CCOS_FENCING_EPOCH"],
            "--action",
            identity["CCOS_HOOK_ACTION"],
            "--repository-root",
            identity["CCOS_REPOSITORY_ROOT"],
            "--path",
            os.environ.get("CCOS_HOOK_PATH", ""),
        ),
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
