from pathlib import Path

from _common import fail_open, load_input, no_output
from capability_index import (
    ACTIVE_CAPABILITIES_PATH,
    ensure_index,
    load_active_capabilities,
    load_routing_policy,
)
from capability_manifest_recovery import (
    _sha256_file,
    attempt_recovery,
    write_session_start_recovery_receipt,
)


def run_session_start(*, receipt_dir: Path | None = None) -> Path:
    before_state = load_active_capabilities(ACTIVE_CAPABILITIES_PATH)
    before_manifest_sha256 = _sha256_file(ACTIVE_CAPABILITIES_PATH)
    try:
        result = attempt_recovery(current_state=before_state)
    except Exception as exc:
        result = {
            "status": "error",
            "reason_code": "RECOVERY_UNHANDLED_EXCEPTION",
            "error_type": type(exc).__name__,
        }

    try:
        after_state = ensure_index(force=True)
        load_routing_policy()
    except Exception as exc:
        after_state = load_active_capabilities(ACTIVE_CAPABILITIES_PATH)
        if result.get("status") != "error":
            result = {
                "status": "error",
                "reason_code": "SESSION_START_REFRESH_FAILED",
                "error_type": type(exc).__name__,
            }
    after_manifest_sha256 = _sha256_file(ACTIVE_CAPABILITIES_PATH)
    return write_session_start_recovery_receipt(
        result,
        before_state=before_state,
        after_state=after_state,
        before_manifest_sha256=before_manifest_sha256,
        after_manifest_sha256=after_manifest_sha256,
        receipt_dir=receipt_dir,
    )


def main():
    load_input()
    run_session_start()
    no_output()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        fail_open("capability_index_session_start", exc)
