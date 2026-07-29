#!/usr/bin/env python3
"""Test-only builder for controller-sealed native runtime actor assignments."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import uuid


def bind_controller_actor(
    engine: Any,
    store: Any,
    case_id: str,
    *,
    thread_id: str,
    role: str,
    parent_thread_id: str | None,
    agent_path: str,
    cwd: str | Path,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Exercise the same sealed assignment route used by the trusted supervisor."""

    revision = store.get_case(case_id)["revision"]
    request = request_id or str(uuid.uuid4())
    stable_identity = {
        "protocol_version": engine.NATIVE_THREAD_IDENTITY_PROTOCOL_VERSION,
        "schema_version": 1,
        "thread_id": thread_id,
        "parent_thread_id": parent_thread_id,
        "agent_path": agent_path,
        "depth": 0 if parent_thread_id is None else 1,
        "cwd": engine.normalize_binding("worktree", str(cwd)),
        "source_sha256": engine.canonical_json_sha256(
            {"thread_id": thread_id, "agent_path": agent_path}
        ),
        "created_at": "2026-07-29T00:00:00+00:00",
        "cli_version": "test",
        "model_provider": "test",
    }
    identity = {
        **stable_identity,
        "identity_evidence_sha256": engine.canonical_json_sha256(stable_identity),
    }
    actor = {
        "protocol_version": engine.RUNTIME_ACTOR_PROTOCOL_VERSION,
        "schema_version": 2,
        "thread_id": thread_id,
        "controller_assigned_role": role,
        "parent_thread_id": parent_thread_id,
        "agent_path": agent_path,
        "identity_evidence_sha256": identity["identity_evidence_sha256"],
        "binding_source": "controller_verified_native_thread_read",
    }
    assignment = engine._seal_runtime_actor_assignment(
        case_id=case_id,
        actor=actor,
        native_identity=identity,
        request_id=request,
        expected_revision=revision,
    )
    return store.bind_runtime_actor(
        case_id,
        assignment=assignment,
        request_id=request,
        expected_revision=revision,
    )
