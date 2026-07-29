#!/usr/bin/env python3
"""Issue and consume one actor-bound, exact-byte proposal capability.

The command accepts no target, replacement, role, thread, command, or approval
argument. Every action and controller-bound actor field must already exist in
one broker-protected envelope.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable, Mapping

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from case_runtime_broker import (  # noqa: E402
    BrokerAclRestorationError,
    BrokerError,
    collect_proposal_isolation_evidence,
    execute_proposal_grant,
    file_sha256,
    require_current_broker_principal,
    recover_completed_action_grant_cleanup,
    restore_preissue_acl_lockdown,
)
from case_state import (  # noqa: E402
    AuthorizationError,
    CaseStateError,
    CaseStore,
    PROPOSAL_ACTION_GRANT_CORE_FIELDS,
    PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION,
    RevisionConflict,
    ValidationError,
    canonical_case_id,
    normalize_binding,
    normalized_absolute_path,
    path_contains_link_or_reparse,
    path_is_within,
    regular_file_identity,
    require_request_id,
    require_stable_id,
)


ENVELOPE_PROTOCOL_VERSION = "ccos-proposal-action-envelope-v1"
MAX_ENVELOPE_BYTES = 2 * 1024 * 1024

GRANT_CORE_FIELDS = set(PROPOSAL_ACTION_GRANT_CORE_FIELDS)
ATTEMPT_SECRET_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _load_envelope(state_root: Path, envelope_path: Path) -> dict[str, Any]:
    state_root = state_root.resolve(strict=True)
    expected_root = (state_root / "proposal-envelopes").resolve(strict=True)
    path, normalized = normalized_absolute_path(
        str(envelope_path), "proposal action envelope", reject_links=True
    )
    if (
        not path_is_within(path, expected_root)
        or path.parent.resolve(strict=True) != expected_root
        or not path.is_file()
        or path.is_symlink()
        or path_contains_link_or_reparse(path, stop=state_root)
        or path.stat().st_size <= 0
        or path.stat().st_size > MAX_ENVELOPE_BYTES
    ):
        raise AuthorizationError(
            "proposal envelope must be one bounded direct file under the canonical envelope root"
        )
    identity_before = regular_file_identity(path, stop=state_root)
    digest_before = file_sha256(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"proposal envelope is invalid JSON: {exc}") from exc
    if (
        regular_file_identity(path, stop=state_root) != identity_before
        or file_sha256(path) != digest_before
    ):
        raise AuthorizationError("proposal envelope changed while it was read")
    if not isinstance(payload, Mapping) or set(payload) != {
        "protocol_version",
        "schema_version",
        "case_id",
        "expected_case_revision",
        "request_id",
        "grant",
    }:
        raise ValidationError("proposal envelope uses an unexpected schema")
    if (
        payload.get("protocol_version") != ENVELOPE_PROTOCOL_VERSION
        or payload.get("schema_version") != 1
    ):
        raise ValidationError("proposal envelope protocol is unsupported")
    grant = payload.get("grant")
    if not isinstance(grant, Mapping) or set(grant) != GRANT_CORE_FIELDS:
        raise ValidationError("proposal envelope grant core uses an unexpected schema")
    if (
        grant.get("protocol_version") != PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION
        or grant.get("schema_version") != 3
    ):
        raise ValidationError("proposal envelope grant protocol is unsupported")
    result = dict(payload)
    result["case_id"] = canonical_case_id(str(payload.get("case_id", "")))
    revision = payload.get("expected_case_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValidationError(
            "proposal envelope expected_case_revision must be a nonnegative integer"
        )
    result["request_id"] = require_request_id(payload.get("request_id"))
    result["grant"] = dict(grant)
    result["envelope_path"] = normalized
    result["envelope_sha256"] = digest_before
    result["envelope_identity"] = identity_before
    return result


def _resolve_attempt_secret(
    attempt_secret: str | None,
    attempt_secret_provider: Callable[[], str | None] | None,
) -> str:
    if attempt_secret is not None and attempt_secret_provider is not None:
        raise ValidationError("proposal attempt secret has multiple input sources")
    resolved = (
        attempt_secret_provider()
        if attempt_secret is None and attempt_secret_provider is not None
        else attempt_secret
    )
    if not isinstance(resolved, str) or not ATTEMPT_SECRET_PATTERN.fullmatch(resolved):
        raise AuthorizationError(
            "canonically armed proposal requires its exact 256-bit attempt secret"
        )
    return resolved


def _read_attempt_secret_from_stdin() -> str | None:
    raw = sys.stdin.buffer.read(130)
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        return None


def execute_envelope(
    state_root: Path,
    envelope_path: Path,
    *,
    attempt_secret: str | None = None,
    attempt_secret_provider: Callable[[], str | None] | None = None,
) -> dict[str, Any]:
    state_root = state_root.resolve(strict=True)
    envelope = _load_envelope(state_root, envelope_path)
    require_current_broker_principal(envelope["grant"])
    store = CaseStore(state_root)
    case_id = envelope["case_id"]
    expected_revision = envelope["expected_case_revision"]
    grant_core = envelope["grant"]
    grant_id = require_stable_id(grant_core.get("grant_id"), "grant id")
    case = store.get_case(case_id)
    runtime = case.get("runtime")
    grants = runtime.get("action_grants") if isinstance(runtime, Mapping) else None
    canonical_grant = grants.get(grant_id) if isinstance(grants, Mapping) else None
    if not isinstance(canonical_grant, Mapping):
        if case["revision"] != expected_revision:
            raise RevisionConflict(
                "proposal envelope revision differs from the canonical case"
            )
        if grant_core.get("authority", {}).get("expected_case_revision") != expected_revision:
            raise AuthorizationError(
                "proposal authority revision differs from the envelope"
            )
        if grant_core.get("authority", {}).get("case_id") != case_id:
            raise AuthorizationError("proposal authority names another case")
        raise AuthorizationError(
            "NOT_CANONICALLY_ARMED: proposal envelope has no canonical arm"
        )
    if canonical_grant.get("protocol_version") != PROPOSAL_ACTION_GRANT_PROTOCOL_VERSION:
        raise AuthorizationError(
            "envelope grant id collides with another grant protocol"
        )
    if any(
        canonical_grant.get(field) != grant_core.get(field)
        for field in GRANT_CORE_FIELDS
    ):
        raise AuthorizationError(
            "proposal envelope differs from the existing canonical grant"
        )
    status = canonical_grant.get("status")
    if status == "COMPLETED":
        cleanup = recover_completed_action_grant_cleanup(
            state_root=state_root, case_id=case_id, grant_id=grant_id
        )
        target = Path(str(canonical_grant["worktree"])).joinpath(
            *str(canonical_grant["target_path"]).split("/")
        )
        if (
            not target.is_file()
            or file_sha256(target) != canonical_grant["replacement_sha256"]
        ):
            raise AuthorizationError(
                "completed proposal grant target no longer matches exact bytes"
            )
        return {
            "status": "COMPLETED_VERIFIED",
            "generator_started": False,
            "broker_mutation_started": False,
            "case_id": case_id,
            "grant_id": grant_id,
            "case_revision": case["revision"],
            "grant_sha256": canonical_grant["grant_sha256"],
            "result_sha256": canonical_grant["result"]["result_sha256"],
            "envelope_sha256": envelope["envelope_sha256"],
            "cleanup": cleanup,
        }
    if status in {"ISSUED", "CLAIMED"}:
        raise AuthorizationError(
            "active proposal grant cannot be resumed through a fresh envelope invocation"
        )
    if status != "ARMED":
        raise AuthorizationError(
            f"canonical proposal grant is {status} and cannot execute"
        )
    arm_record = canonical_grant.get("arm")
    if not isinstance(arm_record, Mapping):
        raise AuthorizationError("canonical proposal arm binding is absent")
    if expected_revision != arm_record.get("authority_revision"):
        raise RevisionConflict(
            "proposal envelope revision differs from the canonical arm authority"
        )
    if grant_core.get("authority", {}).get("expected_case_revision") != expected_revision:
        raise AuthorizationError(
            "proposal authority revision differs from the envelope"
        )
    if grant_core.get("authority", {}).get("case_id") != case_id:
        raise AuthorizationError("proposal authority names another case")
    if case["revision"] != arm_record.get("armed_revision"):
        raise RevisionConflict("canonical case revision changed after proposal arm")
    if dt.datetime.now(dt.timezone.utc) >= dt.datetime.fromisoformat(
        str(arm_record.get("lease_expires_at", ""))
    ):
        raise AuthorizationError("canonical proposal arm lease expired")
    resolved_secret = _resolve_attempt_secret(
        attempt_secret, attempt_secret_provider
    )
    execution_nonce_sha256 = hashlib.sha256(
        resolved_secret.encode("ascii")
    ).hexdigest()
    if not hmac.compare_digest(
        execution_nonce_sha256,
        str(arm_record.get("attempt_secret_sha256", "")),
    ):
        raise AuthorizationError(
            "proposal attempt secret differs from the canonical arm"
        )
    evidence: dict[str, Any] | None = None
    try:
        evidence = collect_proposal_isolation_evidence(
            store=store, case_id=case_id, grant_core=grant_core
        )
        full_grant = {
            **grant_core,
            "protected_acl_snapshot": evidence["protected_acl_snapshot"],
            "protected_acl_snapshot_sha256": evidence[
                "protected_acl_snapshot_sha256"
            ],
            "preissue_dacl_evidence": evidence["preissue_dacl_evidence"],
            "preissue_dacl_evidence_sha256": evidence[
                "preissue_dacl_evidence_sha256"
            ],
        }
        issuance = store.issue_armed_proposal_action_grant(
            case_id,
            grant=full_grant,
            expected_arm_sha256=str(arm_record.get("arm_sha256", "")),
            attempt_secret=resolved_secret,
            request_id=envelope["request_id"],
            expected_revision=case["revision"],
        )
    except BaseException:
        if evidence is not None:
            restore_preissue_acl_lockdown(
                state_root=state_root,
                case_id=case_id,
                grant_id=grant_id,
                protected_acl_snapshot=evidence["protected_acl_snapshot"],
                protected_acl_snapshot_sha256=evidence[
                    "protected_acl_snapshot_sha256"
                ],
                preissue_dacl_evidence=evidence["preissue_dacl_evidence"],
                restore_reason="proposal_issue_failure",
            )
        raise
    execution = execute_proposal_grant(
        state_root,
        case_id,
        grant_id,
        execution_nonce=resolved_secret,
    )
    return {
        "status": execution["status"],
        "generator_started": False,
        "broker_mutation_started": True,
        "case_id": case_id,
        "grant_id": grant_id,
        "attempt_id": arm_record["attempt_id"],
        "issuance": issuance,
        "execution": execution,
        "envelope_sha256": envelope["envelope_sha256"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--envelope", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = execute_envelope(
            args.state_root,
            args.envelope,
            attempt_secret_provider=_read_attempt_secret_from_stdin,
        )
    except (BrokerError, CaseStateError, OSError) as exc:
        payload = {
            "ok": False,
            "error": type(exc).__name__,
            "message": str(exc),
        }
        if isinstance(exc, BrokerAclRestorationError):
            payload["acl_restoration_diagnostic"] = dict(exc.diagnostic)
        print(
            json.dumps(
                payload,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps({"ok": True, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
