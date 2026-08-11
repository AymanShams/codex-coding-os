from _common import fail_open, load_input, no_output
from capability_index import ensure_index, load_routing_policy
from capability_manifest_recovery import attempt_recovery


def main():
    load_input()
    attempt_recovery()
    ensure_index(force=True)
    load_routing_policy()
    no_output()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        fail_open("capability_index_session_start", exc)
