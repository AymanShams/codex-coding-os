#!/usr/bin/env python3
"""Derive an anti-loop disposition from one protected native user turn."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from case_review_completion_verifier import (
    NativeReviewVerificationError,
    _assert_acl_chain_readonly,
    _assert_direct_path,
    _aware_timestamp,
    _canonical_uuid,
    _read_rollout,
    _read_session_meta,
    _rollout_files,
    canonical_codex_home,
)


HUMAN_DISPOSITION_PROTOCOL_VERSION = "ccos-anti-loop-human-disposition-v1"
NATIVE_HUMAN_VERIFICATION_PROTOCOL_VERSION = (
    "ccos-anti-loop-native-human-verification-v1"
)


class NativeHumanDispositionVerificationError(RuntimeError):
    """The protected native rollout does not prove the requested user decision."""


def _canonical_json_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _translate_native_error(exc: NativeReviewVerificationError) -> None:
    raise NativeHumanDispositionVerificationError(str(exc)) from exc


def verify_human_disposition(
    *,
    case_id: str,
    decision: str,
    product_heads: Mapping[str, str],
    native_thread_id: str,
    native_turn_id: str,
    state_root: Path,
) -> dict[str, Any]:
    """Verify one exact JSON user message and derive the sealed authority record."""

    try:
        canonical_thread = _canonical_uuid(
            native_thread_id, "human disposition native thread id"
        )
        canonical_turn = _canonical_uuid(
            native_turn_id, "human disposition native turn id"
        )
        codex_home = canonical_codex_home(state_root)
        sessions_root = (codex_home / "sessions").resolve(strict=True)
        _assert_direct_path(sessions_root, stop=codex_home)
        candidates = []
        for path in _rollout_files(sessions_root):
            meta = _read_session_meta(path)
            if isinstance(meta, Mapping) and str(meta.get("id", "")).lower() == canonical_thread:
                candidates.append(path)
        if len(candidates) != 1:
            raise NativeHumanDispositionVerificationError(
                "native human thread must resolve to exactly one rollout"
            )
        rollout_path = candidates[0]
        _assert_direct_path(rollout_path, stop=codex_home)
        _assert_acl_chain_readonly(rollout_path, stop=codex_home)
        _, raw_lines, records = _read_rollout(rollout_path)
        _assert_acl_chain_readonly(rollout_path, stop=codex_home)
    except NativeReviewVerificationError as exc:
        _translate_native_error(exc)

    if (
        not records
        or records[0].get("type") != "session_meta"
        or not rollout_path.name.endswith(f"-{canonical_thread}.jsonl")
    ):
        raise NativeHumanDispositionVerificationError(
            "native human rollout identity is invalid"
        )
    session_identities: list[tuple[str, str, str | None, str | None]] = []
    try:
        for record in records:
            if record.get("type") != "session_meta":
                continue
            payload = record.get("payload")
            if not isinstance(payload, Mapping):
                raise NativeHumanDispositionVerificationError(
                    "native human rollout session_meta is malformed"
                )
            meta_thread = _canonical_uuid(
                payload.get("id"), "human disposition session_meta thread id"
            )
            raw_session = payload.get("session_id")
            meta_session = (
                _canonical_uuid(
                    raw_session, "human disposition session_meta session id"
                )
                if raw_session is not None
                else meta_thread
            )
            raw_parent = payload.get("parent_thread_id")
            meta_parent = (
                _canonical_uuid(
                    raw_parent, "human disposition session_meta parent thread id"
                )
                if raw_parent is not None
                else None
            )
            raw_agent_path = payload.get("agent_path")
            meta_agent_path = (
                str(raw_agent_path) if raw_agent_path is not None else None
            )
            session_identities.append(
                (meta_thread, meta_session, meta_parent, meta_agent_path)
            )
    except NativeReviewVerificationError as exc:
        _translate_native_error(exc)
    if (
        not session_identities
        or session_identities[0][0] != canonical_thread
        or any(identity != session_identities[0] for identity in session_identities[1:])
    ):
        raise NativeHumanDispositionVerificationError(
            "native human rollout has absent or conflicting session identities"
        )

    expected_payload = {
        "protocol_version": HUMAN_DISPOSITION_PROTOCOL_VERSION,
        "schema_version": 1,
        "case_id": case_id,
        "decision": decision,
        "product_heads": dict(product_heads),
    }
    matches: list[tuple[int, Mapping[str, Any], str]] = []
    for index, record in enumerate(records):
        payload = record.get("payload")
        if (
            record.get("type") != "response_item"
            or not isinstance(payload, Mapping)
            or payload.get("type") != "message"
            or payload.get("role") != "user"
        ):
            continue
        metadata = payload.get("internal_chat_message_metadata_passthrough")
        if not isinstance(metadata, Mapping) or metadata.get("turn_id") != canonical_turn:
            continue
        content = payload.get("content")
        if (
            not isinstance(content, list)
            or len(content) != 1
            or not isinstance(content[0], Mapping)
            or content[0].get("type") != "input_text"
            or not isinstance(content[0].get("text"), str)
        ):
            continue
        raw_message = content[0]["text"]
        try:
            parsed = json.loads(raw_message)
        except json.JSONDecodeError:
            continue
        if parsed == expected_payload:
            matches.append((index, record, raw_message))
    if len(matches) != 1:
        raise NativeHumanDispositionVerificationError(
            "native human turn must contain exactly one exact disposition JSON message"
        )

    index, record, raw_message = matches[0]
    try:
        decided_at, _ = _aware_timestamp(
            record.get("timestamp"), "human disposition message timestamp"
        )
    except NativeReviewVerificationError as exc:
        _translate_native_error(exc)
    prefix_sha256 = hashlib.sha256(b"".join(raw_lines[: index + 1])).hexdigest()
    message_sha256 = hashlib.sha256(raw_message.encode("utf-8")).hexdigest()
    evidence_sha256 = hashlib.sha256(
        b"ccos-native-human-disposition-v1\0"
        + prefix_sha256.encode("ascii")
        + b"\0"
        + message_sha256.encode("ascii")
    ).hexdigest()
    authority = {
        "protocol_version": HUMAN_DISPOSITION_PROTOCOL_VERSION,
        "schema_version": 2,
        "authority_id": f"native-user:{canonical_thread}:{canonical_turn}",
        "case_id": case_id,
        "decision": decision,
        "product_heads": dict(product_heads),
        "native_thread_id": canonical_thread,
        "native_turn_id": canonical_turn,
        "rollout_relative_path": rollout_path.relative_to(codex_home).as_posix(),
        "decided_at": decided_at,
        "message_sha256": message_sha256,
        "log_prefix_sha256": prefix_sha256,
        "evidence_sha256": evidence_sha256,
        "native_verification_protocol": NATIVE_HUMAN_VERIFICATION_PROTOCOL_VERSION,
    }
    authority["authority_sha256"] = _canonical_json_sha256(authority)
    return authority
