#!/usr/bin/env python3
"""Thin project client for the installed campaign engine."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence


LEGACY_ENGINE_RETIRED = "LEGACY_ENGINE_RETIRED"
PUBLIC_COMMANDS = frozenset(
    {"admit", "approve", "run", "status", "cancel", "reconcile", "doctor", "legacy"}
)
READ_ONLY_LEGACY_ALIASES = frozenset({"start", "decide", "summary", "validate"})


def _cli_path() -> Path:
    override = os.environ.get("CCOS_ENGINE_CLI")
    if override:
        return Path(override).expanduser().resolve(strict=True)
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    return (
        codex_home / "coding-os" / "scripts" / "agent" / "campaign_engine" / "cli.py"
    ).resolve(strict=True)


def _retired(command: str, json_output: bool) -> int:
    payload = {
        "ok": False,
        "code": LEGACY_ENGINE_RETIRED,
        "command": command,
        "message": "Repository session files no longer own lifecycle state.",
        "replacement": "campaign_engine.cli status",
    }
    print(json.dumps(payload, sort_keys=True) if json_output else f"{LEGACY_ENGINE_RETIRED}: {command}")
    return 78


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    json_output = "--json" in arguments
    positional = [item for item in arguments if not item.startswith("-")]
    command = positional[0] if positional else "status"
    try:
        cli = _cli_path()
    except OSError as exc:
        payload = {"ok": False, "code": "CODING_OS_ENGINE_UNAVAILABLE", "message": str(exc)}
        print(json.dumps(payload, sort_keys=True) if json_output else f"CODING_OS_ENGINE_UNAVAILABLE: {exc}")
        return 78
    if command in READ_ONLY_LEGACY_ALIASES:
        forwarded = ["--json"] if json_output else []
        forwarded.extend(["status", "--repository-root", str(Path.cwd().resolve())])
    elif command in PUBLIC_COMMANDS:
        forwarded = arguments
    else:
        return _retired(command, json_output)
    completed = subprocess.run(
        (sys.executable, "-B", str(cli), *forwarded),
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
