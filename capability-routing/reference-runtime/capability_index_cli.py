#!/usr/bin/env python3
"""Validate or query the compact universal capability routing state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from capability_index import (
    ACTIVE_CAPABILITIES_PATH,
    ROUTE_DECISION_REGISTRY_PATH,
    ROUTING_POLICY_PATH,
    ensure_index,
    hook_carrier_status,
    load_routing_policy,
    query_index,
    resolve_route,
    route_execution_ready,
    verify_registered_route,
    worker_runtime_identity_status,
)


def main() -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Validate or query active-capabilities.json and routing-policy.yaml."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Reload and validate the canonical files. No derived catalogue is written.",
    )
    parser.add_argument("--query", help="Resolve the first eligible policy route for this prompt.")
    parser.add_argument(
        "--verify-registered-route",
        metavar="PATH_OR_DASH",
        help=(
            "Read one full route JSON file, or stdin with '-', and verify its exact "
            "unexpired issuance in the Catalogue Router registry without writing."
        ),
    )
    parser.add_argument(
        "--task-text",
        help=(
            "Legacy conservative instruction binding. Worker execution also requires "
            "--task-input-json so full task input is not exposed on the command line."
        ),
    )
    parser.add_argument(
        "--task-input-json",
        metavar="PATH_OR_DASH",
        help=(
            "Read the exact full task_input JSON object from a UTF-8 file, or stdin "
            "with '-'. This is required for an executable worker route."
        ),
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print the unified decision as JSON.")
    parser.add_argument(
        "--status-json",
        action="store_true",
        help="Print component-level router authority status as JSON.",
    )
    parser.add_argument("--project-id", help="Structured Task Gate project identity for scoped memory mapping.")
    parser.add_argument("--cwd", help="Structured working directory for scoped memory mapping.")
    parser.add_argument(
        "--task-type",
        choices=["answer", "transform", "recall", "research", "synthesize", "implement", "review", "extract", "status"],
    )
    parser.add_argument("--complexity", choices=["low", "medium", "high"])
    parser.add_argument("--source-need", choices=["none", "memory", "index", "both"])
    parser.add_argument(
        "--local-stack-purpose",
        choices=[
            "runtime_status",
            "prior_continuity",
            "project_evidence_lookup",
            "retrieval_bundle",
            "literal_structured_extraction",
            "bounded_classification_or_transformation",
            "complex_multi_source_synthesis",
            "focused_coding_assistance",
            "explicit_challenge",
            "read_heavy_support",
        ],
    )
    parser.add_argument("--source-scope", action="append", dest="source_scopes")
    parser.add_argument("--classification-flag", action="append", dest="classification_flags")
    parser.add_argument(
        "--execution-disposition",
        choices=["codex_only", "worker_support"],
        help="Structured Task Gate disposition for generative worker support.",
    )
    parser.add_argument(
        "--eligible-worker-family",
        action="append",
        choices=["local_agent_stack", "terra", "antigravity"],
        dest="eligible_worker_families",
        help="Affirmative Task Gate worker family. At most one family is accepted.",
    )
    parser.add_argument("--exact-evidence", action="store_true")
    parser.add_argument(
        "--memory-mode",
        choices=["none", "recall", "recall_and_capture"],
        help="Structured memory mode. Scope is still derived only from project identity or cwd.",
    )
    parser.add_argument(
        "--persistence-intent",
        choices=["none", "requested"],
        help="Explicit persistence intent. requested requires recall_and_capture.",
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Deprecated compatibility flag. Inactive entries are always rejected.",
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--paths", action="store_true", help="Print the resolved canonical paths.")
    args = parser.parse_args()

    if args.task_input_json and args.task_text is not None:
        print(
            json.dumps(
                {
                    "valid": False,
                    "status": "task_input_invalid",
                    "error": (
                        "--task-text is legacy conservative input and cannot be combined "
                        "with --task-input-json; bind executable text through "
                        "task_input.instruction"
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2

    if args.verify_registered_route:
        try:
            raw = (
                sys.stdin.read()
                if args.verify_registered_route == "-"
                else Path(args.verify_registered_route).read_text(encoding="utf-8-sig")
            )
            route = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            receipt = {
                "valid": False,
                "status": "schema_invalid",
                "error": str(exc),
                "registry_path": str(ROUTE_DECISION_REGISTRY_PATH),
            }
            print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
            return 3
        receipt = verify_registered_route(route)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0 if receipt["valid"] else 3

    task_input = None
    if args.task_input_json:
        try:
            raw_task_input = (
                sys.stdin.read()
                if args.task_input_json == "-"
                else Path(args.task_input_json).read_text(encoding="utf-8-sig")
            )
            task_input = json.loads(raw_task_input)
            if not isinstance(task_input, dict):
                raise TypeError("task_input JSON root must be an object")
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            print(
                json.dumps(
                    {
                        "valid": False,
                        "status": "task_input_invalid",
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 2

    manifest = ensure_index(force=True)
    policy = load_routing_policy()
    if args.paths:
        print(f"active_capabilities={ACTIVE_CAPABILITIES_PATH}")
        print(f"routing_policy={ROUTING_POLICY_PATH}")
        print(f"route_decision_registry={ROUTE_DECISION_REGISTRY_PATH}")

    if args.query:
        classification = {
            key: value
            for key, value in {
                "project_id": args.project_id,
                "cwd": args.cwd,
                "memory_mode": args.memory_mode,
                "persistence_intent": args.persistence_intent,
                "task_type": args.task_type,
                "complexity": args.complexity,
                "source_need": args.source_need,
                "local_stack_purpose": args.local_stack_purpose,
                "requested_source_scopes": (
                    args.source_scopes
                    if args.source_scopes is not None
                    else []
                    if args.execution_disposition is not None
                    else None
                ),
                "flags": args.classification_flags,
                "exact_evidence": True if args.exact_evidence else None,
                "execution_disposition": (
                    {
                        "mode": args.execution_disposition,
                        "eligible_worker_families": args.eligible_worker_families or [],
                    }
                    if args.execution_disposition is not None
                    or args.eligible_worker_families is not None
                    else None
                ),
            }.items()
            if value is not None
        }
        decision = resolve_route(
            args.query,
            manifest=manifest,
            policy=policy,
            classification=classification,
            task_text=args.task_text,
            task_input=task_input,
        )
        exit_code = (
            0
            if route_execution_ready(
                decision,
                task_text=(
                    task_input.get("instruction")
                    if isinstance(task_input, dict)
                    else args.task_text if args.task_text is not None else args.query
                ),
                task_input=(
                    task_input
                    if isinstance(task_input, dict)
                    else {"instruction": args.task_text if args.task_text is not None else args.query}
                ),
            )
            else 4
        )
        if args.json_output:
            print(json.dumps(decision, ensure_ascii=False, sort_keys=True, indent=2))
            return exit_code
        print(
            f"execution | {decision['execution_owner']} | {decision['model']} | "
            f"{decision['reasoning_effort']} | {decision['deadline_seconds']}s"
        )
        if decision.get("primary"):
            print(f"primary | {decision['primary']['id']} | {decision['primary']['name']}")
            for support in decision.get("supports", [])[:2]:
                print(f"skill_support | {support['id']} | {support['name']}")
        else:
            for entry in query_index(args.query, limit=max(1, min(args.limit, 8))):
                print(f"candidate | {entry['kind']} | {entry['id']} | {entry['name']}")
        for worker in decision.get("support_workers", [])[:2]:
            print(
                f"worker_support | {worker['execution_owner']} | {worker['role']} | "
                f"{worker['model']} | required={str(bool(worker.get('required'))).lower()} | "
                f"deadline={worker['deadline_seconds']}s"
            )
        for capability_fallback in decision.get("capability_fallbacks", [])[:1]:
            print(
                "capability_fallback | "
                f"requested={capability_fallback['requested_capability']} | "
                f"chosen={capability_fallback['chosen_fallback']} | "
                f"equivalence={capability_fallback['equivalence']} | "
                "unavailable="
                + ",".join(capability_fallback["unavailable_dependencies"])
            )
        local_execution = decision["local_execution"]
        memory = local_execution["memory"]
        print(
            f"local_execution={local_execution['recipe_id'] or 'none'};"
            f"purpose={local_execution['local_stack_purpose'] or 'none'}"
        )
        print(
            f"memory={memory['mode']};scope={memory['scope'] or 'none'};"
            f"capture_when={memory['capture_when']}"
        )
        print(f"reasons={','.join(decision['reason_codes'])}")
        print(f"fallback={decision['fallback']['on_error']};automatic_retry=false")
        print(f"decision_digest={decision['decision_digest']}")
        print(f"task_text_sha256={decision['task_text_sha256']}")
        print(f"task_input_sha256={decision['task_input_sha256']}")
        print(f"task_input_mode={decision['task_input_mode']}")
        issuance = decision["issuance"]
        print(
            f"issuance={issuance['status']};schema={issuance['registry_schema_version']};"
            f"requested={str(bool(issuance['worker_execution_requested'])).lower()};"
            f"failure={issuance['failure_code'] or 'none'}"
        )
        receipt = verify_registered_route(decision)
        print(f"route_registry={receipt['status']}")
        return exit_code

    if args.status_json:
        dynamic = manifest.get("dynamic_authority")
        dynamic = dynamic if isinstance(dynamic, dict) else {}
        summary = manifest.get("summary")
        summary = summary if isinstance(summary, dict) else {}
        router_ready = (
            manifest.get("source_hashes_verified") is True
            and manifest.get("freshness_status") in {"fresh", "degraded", "current", "live", "valid", "verified"}
        )
        worker_identities = worker_runtime_identity_status(manifest)
        status = {
            "schema_version": "capability-router-status-v1",
            "router_admission_status": (
                "degraded"
                if router_ready and manifest.get("freshness_status") == "degraded"
                else "available"
                if router_ready
                else "unavailable"
            ),
            "freshness_status": manifest.get("freshness_status", "missing"),
            "static_source_hashes_verified": bool(
                manifest.get("static_source_hashes_verified")
            ),
            "dynamic_authority_status": manifest.get(
                "dynamic_authority_status", "unavailable"
            ),
            "worker_runtime_bom_status": manifest.get(
                "worker_runtime_bom_status", "unavailable"
            ),
            "worker_runtime_identities": worker_identities,
            "hook_carrier": hook_carrier_status(),
            "generation_pointer_status": manifest.get(
                "generation_pointer_status", "unknown"
            ),
            "authority_generation_id": str(
                (manifest.get("authority_generation") or {}).get("id") or ""
            ),
            "manifest_authority_sha256": manifest.get("authority_sha256", ""),
            "active_entries": int(summary.get("active_entries", 0)),
            "quarantined_package_count": len(
                dynamic.get("quarantined_packages", [])
            ),
            "quarantined_capability_count": len(
                dynamic.get("quarantined_capability_ids", [])
            ),
            "changed_packages": dynamic.get("changed_packages", []),
            "changed_config_leaves": dynamic.get("changed_config_leaves", []),
            "quarantined_packages": dynamic.get("quarantined_packages", []),
            "reason_code": dynamic.get("reason_code", ""),
        }
        print(json.dumps(status, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if router_ready else 4

    summary = manifest["summary"]
    print(f"manifest_snapshot={manifest.get('snapshot_id', '')}")
    print(f"freshness_status={manifest.get('freshness_status', '')}")
    print(f"active_entries={summary['active_entries']}")
    print(f"rejected_inactive={summary['rejected_inactive']}")
    print(f"rejected_state_artifacts={summary['rejected_state_artifacts']}")
    print(f"policy_rules={len(policy.get('rules', []))}")
    print(f"max_supports={policy.get('max_supports', 2)}")
    print(f"max_worker_supports={policy.get('max_worker_supports', 2)}")
    print(f"worker_rules={len(policy.get('worker_rules', []))}")
    print(f"source_hashes_verified={str(bool(manifest.get('source_hashes_verified'))).lower()}")
    print(
        "static_source_hashes_verified="
        f"{str(bool(manifest.get('static_source_hashes_verified'))).lower()}"
    )
    print(f"dynamic_authority_status={manifest.get('dynamic_authority_status', '')}")
    print(f"worker_runtime_bom_status={manifest.get('worker_runtime_bom_status', '')}")
    print(f"generation_pointer_status={manifest.get('generation_pointer_status', '')}")
    print(
        "authority_generation_id="
        f"{(manifest.get('authority_generation') or {}).get('id', '')}"
    )
    print(f"rejected_quarantined={summary.get('rejected_quarantined', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
