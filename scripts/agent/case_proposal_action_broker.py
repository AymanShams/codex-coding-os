#!/usr/bin/env python3
"""Issue and consume one actorless, exact-byte proposal capability.

The command accepts no target, replacement, role, thread, command, or approval
argument. Every action field must already exist in one broker-protected envelope.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from case_runtime_broker import (  # noqa: E402
    BrokerError,
    collect_proposal_isolation_evidence,
    execute_proposal_grant,
    file_sha256,
    recover_completed_action_grant_cleanup,
    restore_preissue_acl_lockdown,
)
from case_state import (  # noqa: E402
    AuthorizationError,
    CaseStateError,
    CaseStore,
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

GRANT_CORE_FIELDS = {
    "protocol_version",
    "schema_version",
    "evidence_mode",
    "grant_id",
    "authority_id",
    "operation_id",
    "action",
    "operation",
    "repository",
    "branch",
    "worktree",
    "base_head",
    "target_path",
    "baseline_sha256",
    "proposal_artifact_path",
    "proposal_artifact_sha256",
    "proposal_size",
    "replacement_sha256",
    "worker_principal_sid",
    "model_worker_principal_sid",
    "sandbox_group_principal_sid",
    "denied_principal_sids",
    "broker_principal_sid",
    "sandbox_executable_path",
    "sandbox_executable_sha256",
    "sandbox_executable_version",
    "probe_runtime_root",
    "expires_at",
    "authority",
    "authority_sha256",
}


def _load_envelope(state_root: Path, envelope_path: Path) -> dict[str, Any]:
    state_root = state_root.resolve(strict=True)
    expected_root = state_root / "proposal-envelopes"
    expected_root.mkdir(mode=0o700, exist_ok=True)
    expected_root = expected_root.resolve(strict=True)
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
        or grant.get("schema_version") != 2
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


def execute_envelope(state_root: Path, envelope_path: Path) -> dict[str, Any]:
    state_root = state_root.resolve(strict=True)
    store = CaseStore(state_root)
    envelope = _load_envelope(state_root, envelope_path)
    case_id = envelope["case_id"]
    expected_revision = envelope["expected_case_revision"]
    grant_core = envelope["grant"]
    grant_id = require_stable_id(grant_core.get("grant_id"), "grant id")
    case = store.get_case(case_id)
    runtime = case.get("runtime")
    grants = runtime.get("action_grants") if isinstance(runtime, Mapping) else None
    canonical_grant = grants.get(grant_id) if isinstance(grants, Mapping) else None
    if isinstance(canonical_grant, Mapping):
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
        if status not in {"ISSUED", "CLAIMED"}:
            raise AuthorizationError(
                f"canonical proposal grant is {status} and cannot execute"
            )
        execution = execute_proposal_grant(state_root, case_id, grant_id)
        return {
            "status": execution["status"],
            "generator_started": False,
            "broker_mutation_started": True,
            "case_id": case_id,
            "grant_id": grant_id,
            "execution": execution,
            "envelope_sha256": envelope["envelope_sha256"],
        }
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
        issuance = store.issue_proposal_action_grant(
            case_id,
            grant=full_grant,
            request_id=envelope["request_id"],
            expected_revision=expected_revision,
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
    execution = execute_proposal_grant(state_root, case_id, grant_id)
    return {
        "status": execution["status"],
        "generator_started": False,
        "broker_mutation_started": True,
        "case_id": case_id,
        "grant_id": grant_id,
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
        result = execute_envelope(args.state_root, args.envelope)
    except (BrokerError, CaseStateError, OSError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps({"ok": True, "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
