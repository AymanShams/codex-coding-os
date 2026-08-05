#!/usr/bin/env python3
"""Retired legacy entry point.

The lifecycle implementation that formerly lived here was removed.  This file
exists only so stale callers fail deterministically instead of importing or
reactivating a second authority.  Read-only legacy evidence is available from
``campaign_engine.cli legacy inspect``.
"""

from __future__ import annotations

import json
import sys
from typing import Sequence


LEGACY_ENGINE_RETIRED = "LEGACY_ENGINE_RETIRED"


def retired_payload(argv: Sequence[str] | None = None) -> dict[str, object]:
    return {
        "ok": False,
        "code": LEGACY_ENGINE_RETIRED,
        "message": "The case-state lifecycle engine is retired and cannot mutate or authorize work.",
        "requested_arguments": list(argv or ()),
        "read_only_legacy_command": "campaign_engine.cli legacy inspect",
        "lifecycle_authority": "campaign_engine.reducer.reduce",
    }


def main(argv: Sequence[str] | None = None) -> int:
    print(json.dumps(retired_payload(argv if argv is not None else sys.argv[1:]), sort_keys=True))
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
