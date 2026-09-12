"""Opt-in native acceptance using the installed engine and a disposable target.

The first native turn applies a declared faulty proposal. The correction and
review results come from real native turns. No corrected source is supplied.
Authentication and the actual account-profile namespace belong to the caller.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone


BUSINESS_CASES = (
    ("positive orders", [1250, 750], {"order_count": 2, "total": 2000}),
    ("zero and refund entries", [1250, 0, -250], {"order_count": 3, "total": 1000}),
    ("empty input", [], {"order_count": 0, "total": 0}),
    ("zero entries only", [0, 0], {"order_count": 2, "total": 0}),
    ("refunds only", [-99, -1], {"order_count": 2, "total": -100}),
    ("offsetting amounts", [9900, -9900, 0], {"order_count": 3, "total": 0}),
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(root, *arguments, binary=False):
    result = subprocess.run(["git", *arguments], cwd=root, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    require(result.returncode == 0, f"Git command failed: {result.stderr.decode(errors='replace')}")
    return result.stdout if binary else result.stdout.decode().strip()


def business_outputs(program):
    """Measure the delivered program against explicit business expectations."""
    observations = []
    for name, amounts, expected in BUSINESS_CASES:
        request = {"orders": [{"amount": amount} for amount in amounts]}
        result = subprocess.run([sys.executable, "-B", str(program)], input=json.dumps(request),
                                cwd=program.parent, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=10)
        require(result.returncode == 0, f"{name}: program exited {result.returncode}: {result.stderr}")
        actual = json.loads(result.stdout)
        require(actual == expected and all(type(actual[key]) is int for key in expected),
                f"{name}: expected {expected!r}, received {actual!r}")
        observations.append({"case": name, "input": request, "expected": expected,
                             "actual": actual, "exit_code": result.returncode,
                             "stdout": result.stdout, "stderr": result.stderr})
    return observations


def require_review_reads(calls, actor_id, sources):
    """Require successful native reads covering every line of the fixed sources."""
    for path, text in sources.items():
        expected_lines = text.splitlines()
        observed = set()
        for call in calls:
            result = call.get("result", {})
            if (call["actor_id"] != actor_id or call["tool"] != "campaign_read_file"
                    or result.get("path") != path):
                continue
            start, end = result["start_line"], result["end_line"]
            require(result["text"].splitlines() == expected_lines[start - 1:end],
                    f"Native review read differs from the accepted source: {path}")
            observed.update(range(start, end + 1))
        require(set(range(1, len(expected_lines) + 1)) <= observed,
                f"Native reviewer did not inspect the complete source: {path}")


def close_native_run(host, supervisor, store, campaign_id, native):
    """Preserve the original result and cancel only this unfinished owned run."""
    try:
        host.close()
    except Exception as exc:
        native["host_close_failure"] = {"type": type(exc).__name__, "message": str(exc)}
    if native["status"] == "failed":
        state = store.get_snapshot(campaign_id).state.value
        native["state_before_failure_cleanup"] = state
        if state not in {"COMPLETED", "FAILED", "CANCELLED"}:
            try:
                native["failure_cleanup"] = supervisor.cancel(
                    campaign_id, reason="native acceptance ended without an accepted result").to_dict()
            except Exception as exc:
                native["cleanup_failure"] = {"type": type(exc).__name__, "message": str(exc)}


def run_native_product_journey(source, installed_root, invoke_cli, report, *,
                               model, reasoning_effort, worker_timeout=180, artifact_directory=None):
    require(0 < worker_timeout <= 600, "Native worker timeout must be in (0, 600]")
    require(not os.environ.get("CODEX_CAMPAIGN_HOST_EXECUTABLE"),
            "Native acceptance must resolve the installed host through PATH")
    sys.path[:0] = [str(installed_root), str(source["root"])]
    from scripts.agent.campaign_engine.effects import ExternalEffectDriver, GitHubBackend
    from scripts.agent.campaign_engine.host import AppServerTransport, NativeCodexHost
    from scripts.agent.campaign_engine.runtime_bootstrap import runtime_layout
    from scripts.agent.campaign_engine.store import CampaignStore
    from scripts.agent.campaign_engine.supervisor import DeterministicSupervisor
    from tests.test_campaign_bounded_continuation import (
        ORDER_SUMMARY_ACCEPTANCE_BODY, ORDER_SUMMARY_BASE_SOURCE,
        ORDER_SUMMARY_COUNT_DEFECT_SOURCE, ORDER_SUMMARY_REQUIREMENT,
    )

    layout = runtime_layout()
    require(layout.installed_root == installed_root, "Native journey must use the canonical installed engine")
    artifacts = Path(artifact_directory or "/var/tmp").resolve(strict=True)
    require(artifacts == Path("/var/tmp"), "Native artifacts require the dedicated /var/tmp mount")
    native = {"status": "running", "model": model, "reasoning_effort": reasoning_effort,
              "runner_sha256": sha256(__file__),
              "model_turns": "native App Server", "terminal_receipts": [], "tool_calls": [],
              "decisions": [], "review_receipts": [], "findings": [], "prompts": [], "transports": [],
              "publication_calls": [],
              "harness_parent_model_calls": 0,
              "usage_scope": "Only bound campaign actors. This assistant conversation is outside this receipt.",
              "seed_provenance": "First native turn applies supplied faulty proposal. No correction is supplied."}
    report["native_journey"] = native
    started = time.monotonic()
    campaign_id = "native-order-summary"
    deadline = datetime.now(timezone.utc) + timedelta(seconds=worker_timeout * 4 + 120)
    deadline_text = deadline.isoformat(timespec="seconds").replace("+00:00", "Z")
    with tempfile.TemporaryDirectory(prefix="native-product-", dir="/tmp") as temporary:
        root = Path(temporary)
        repo, remote = root / "repo", root / "delivery.git"
        (repo / "src").mkdir(parents=True)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.name", "Native Product Acceptance")
        git(repo, "config", "user.email", "native-acceptance@example.invalid")
        git(root, "init", "--bare", "-q", str(remote))
        git(repo, "remote", "add", "origin", remote.as_uri())
        product = repo / "src/order_summary.py"
        product.write_text(ORDER_SUMMARY_BASE_SOURCE, encoding="utf-8", newline="\n")
        (repo / "requirements.md").write_text(ORDER_SUMMARY_REQUIREMENT, encoding="utf-8", newline="\n")
        acceptance = ("import unittest\nfrom pathlib import Path\nclass Acceptance(unittest.TestCase):\n"
                      "    def test_contract(self):\n"
                      + "".join("        " + line + "\n" for line in ORDER_SUMMARY_ACCEPTANCE_BODY.splitlines()))
        (repo / "test_acceptance.py").write_text(acceptance, encoding="utf-8", newline="\n")
        proposal = "diff --git a/src/order_summary.py b/src/order_summary.py\n" + "".join(
            difflib.unified_diff(ORDER_SUMMARY_BASE_SOURCE.splitlines(keepends=True),
                                 ORDER_SUMMARY_COUNT_DEFECT_SOURCE.splitlines(keepends=True),
                                 fromfile="a/src/order_summary.py", tofile="b/src/order_summary.py"))
        (repo / "proposed-change.patch").write_text(proposal, encoding="utf-8", newline="\n")
        fixed = {name: sha256(repo / name)
                 for name in ("requirements.md", "test_acceptance.py", "proposed-change.patch")}
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "existing order summary and fixed acceptance conditions")
        native["base_head"] = git(repo, "rev-parse", "HEAD")
        native["fixed_sources"] = fixed
        native["source_contents"] = {"base_program": ORDER_SUMMARY_BASE_SOURCE,
            **{name: (repo / name).read_text() for name in fixed}}
        raw = json.loads((source["root"] / "templates/campaign.example.json").read_text())
        for key in ("git_root", "worktree", "repository_remote", "branch", "base_sha",
                    "installed_source_commit", "installed_bundle_digest", "install_transaction",
                    "protocol_version", "schema_compatibility", "host_capability_probe_version"):
            raw.pop(key, None)
        limits = {"CHILD_CREATION": 4, "CHILD_START": 4, "VALIDATION_EXECUTION": 3,
                  "REVIEW_DISPATCH": 1, "PUSH": 1}
        for budget in raw["attempt_budgets"]:
            budget["limit"] = limits.get(budget["token"], 0)
        raw.update(campaign_id=campaign_id, mode="AUTOMATED", deadline_utc=deadline_text,
                   objective="Count every order, including zero amounts and refunds, while preserving the total in cents",
                   allowed_paths=["src/order_summary.py"], autonomous_rank=sum(limits.values()))
        raw["nodes"] = [{"node_id": "order-summary", "objective": raw["objective"],
                         "allowed_paths": raw["allowed_paths"], "validation_command_ids": ["unit"],
                         "required_tools": ["campaign_read_file", "campaign_apply_patch", "campaign_commit",
                                            "campaign_git_status", "campaign_git_diff"],
                         "deadline_utc": deadline_text, "acceptance_scenarios": [{
                             "scenario_id": "order-count", "expectation": raw["objective"],
                             "validation_command_id": "unit", "sources": [
                                 {"path": name, "requirement_id": name}
                                 for name in ("requirements.md", "test_acceptance.py")]}]}]
        raw["required_validation_commands"] = [{
            "command_id": "unit", "executable": sys.executable,
            "arguments": ["-B", "-m", "unittest", "-v", "test_acceptance.Acceptance.test_contract"],
            "environment_allowlist": ["PATH"], "timeout_seconds": 30, "output_limit_bytes": 100000,
            "expected_worktree_condition": "CLEAN", "required_exit_code": 0,
            "correction_policy": {"adapter": "unittest", "expectation_id": "order-count",
                                  "test_id": "test_acceptance.Acceptance.test_contract"}}]
        authority = raw["publication_authority"]
        raw["publication_authority"] = {"authorized_by": "native-acceptance-operator",
            "human_authorization": authority["human_authorization"], "automated": True,
            "allowed_effects": ["PUSH"], "required_effects": ["PUSH"], "required_hosted_checks": []}
        proposed, prepared = root / "proposed.json", root / "prepared.json"
        proposed.write_text(json.dumps(raw), encoding="utf-8")
        report["prepare"] = invoke_cli("prepare", "--spec", str(proposed), "--repository", str(repo),
                                       "--output", str(prepared))
        require(report["prepare"]["state"] == "PROPOSED", "Prepare did not remain a proposal")
        require(invoke_cli("status")["campaign_count"] == 0, "Prepare admitted a campaign")
        report["admit"] = invoke_cli("admit", "--spec", str(prepared))
        report["approve"] = invoke_cli("approve", "--campaign-id", campaign_id,
                                       "--specification-digest", report["prepare"]["specification_digest"])
        store = CampaignStore(layout.state_db)

        class ObservedTransport(AppServerTransport):
            """Record fixture protocol payloads, excluding account/configuration APIs."""

            def __init__(self, executable, **kwargs):
                super().__init__(executable, timeout=worker_timeout, **kwargs)
                require(self.executable == Path(shutil.which("codex") or "").resolve(strict=True),
                        "Native host differs from the executable available through PATH")
                if "runtime_host" not in native:
                    version = subprocess.run([str(self.executable), "--version"], check=True,
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, timeout=10)
                    native["runtime_host"] = {"path": str(self.executable),
                        "sha256": sha256(self.executable), "version": version.stdout.strip(),
                        "resolution": "PATH"}
                self.record = {"executable": str(self.executable), "executable_sha256": sha256(self.executable),
                               "cwd": str(self.cwd), "messages": [], "capture_overflow": False}
                self.recorded_bytes = 0
                self.recorded_requests = set()
                native["transports"].append(self.record)
                transport = self

                class ObservedInbox(queue.Queue):
                    def put(self, message, *args, **kwargs):
                        if isinstance(message, dict):
                            method = str(message.get("method", ""))
                            if (method.startswith(("item/", "turn/", "thread/tokenUsage/"))
                                    or method in {"error", "warning", "thread/started", "thread/status/changed"}
                                    or (not method and str(message.get("id")) in transport.recorded_requests)):
                                transport.capture("received", message)
                        return super().put(message, *args, **kwargs)

                self.inbox = ObservedInbox()

            def capture(self, direction, message):
                encoded = json.dumps(message, ensure_ascii=False)
                self.recorded_bytes += len(encoded.encode("utf-8"))
                if self.recorded_bytes <= 5_000_000:
                    self.record["messages"].append({"direction": direction, "message": json.loads(encoded)})
                else:
                    self.record["capture_overflow"] = True

            def _write(self, value):
                if value.get("method") in {"thread/start", "turn/start", "turn/interrupt"}:
                    self.recorded_requests.add(str(value.get("id")))
                    self.capture("sent", value)
                elif "method" not in value and "result" in value:
                    # Client responses are scoped dynamic tool results or explicit denials.
                    self.capture("sent", value)
                return super()._write(value)

            def request(self, method, params=None, **kwargs):
                result = super().request(method, params, **kwargs)
                if method == "thread/start":
                    self.record["reported_model"] = result.get("model")
                    require(result.get("model") == model,
                            f"Native thread reports a different model: {result.get('model')}")
                return result

            def close(self):
                process = self.process
                super().close()
                self.record["diagnostics"] = self.diagnostic_snapshot()
                if process is not None:
                    self.record["diagnostics"]["process_returncode"] = process.poll()

        class ObservedNativeHost(NativeCodexHost):
            def start_actor_turn(self, lease_id, prompt):
                lease = self._bindings[lease_id].lease
                snapshot = store.get_snapshot(campaign_id)
                if lease.role == "IMPLEMENTER" and snapshot.node(lease.node_id).validation_corrections == 0:
                    prompt = ("This acceptance run starts from a supplied proposed change. Read requirements.md, "
                              "test_acceptance.py and proposed-change.patch. Apply the supplied patch exactly through "
                              "campaign_apply_patch and commit it with campaign_commit. Do not improve or correct this "
                              "proposal in this turn. Its independent validation runs next and any correction requires "
                              "a new engine lease. Do not edit other files, use shell, or publish. Return JSON only.")
                elif lease.role in {"REVIEWER", "CLOSURE_REVIEWER"}:
                    prompt += ("\nRead the complete requirements.md, test_acceptance.py, and src/order_summary.py "
                               "through campaign_read_file before deciding. Assess actual integer-cent behavior for "
                               "positive orders, zero entries, refunds, empty input, and offsetting amounts. "
                               "Choose your own verdict from that evidence.")
                native["prompts"].append({"actor_id": lease.actor_id, "lease_id": lease_id,
                                          "role": lease.role, "prompt": prompt})
                return super().start_actor_turn(lease_id, prompt)

            def _handle_dynamic_tool(self, lease, params):
                arguments = params.get("arguments", {})
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                call = {"actor_id": lease.actor_id, "role": lease.role,
                    "native_thread_id": params.get("threadId"), "tool": params.get("tool"),
                    "path": arguments.get("path"), "arguments": arguments}
                native["tool_calls"].append(call)
                try:
                    result = super()._handle_dynamic_tool(lease, params)
                except Exception as exc:
                    call["error"] = {"type": type(exc).__name__, "message": str(exc)}
                    raise
                call["result"] = result
                return result

            def collect_terminal_receipt(self, *args, **kwargs):
                receipt = super().collect_terminal_receipt(*args, **kwargs)
                native["terminal_receipts"].append(receipt.to_dict())
                return receipt

        class ObservedDelivery(GitHubBackend):
            executions = 0

            def execute(self, kind, payload):
                self.executions += 1
                call = {"kind": kind, "payload": dict(payload)}
                native["publication_calls"].append(call)
                result = super().execute(kind, payload)
                call["result"] = result
                return result

        host = ObservedNativeHost(model=model, reasoning_effort=reasoning_effort,
            usage_recorder=store.record_native_usage,
            transport_factory=ObservedTransport)
        delivery = ObservedDelivery()
        supervisor = DeterministicSupervisor(store, host=host,
            effect_driver=ExternalEffectDriver(store, delivery))
        try:
            for _ in range(32):
                decision = supervisor.step(campaign_id)
                native["decisions"].append(decision.to_dict())
                if decision.action in {"IMPLEMENTER_DISPATCHED", "VALIDATION_CORRECTION_DISPATCHED"}:
                    supervisor.complete_worker(decision.details["lease_id"], timeout=worker_timeout)
                    if decision.action == "IMPLEMENTER_DISPATCHED":
                        require(product.read_text() == ORDER_SUMMARY_COUNT_DEFECT_SOURCE,
                                "Native initial turn did not apply the declared faulty proposal exactly")
                        native["seeded_candidate_head"] = git(repo, "rev-parse", "HEAD")
                        native["source_contents"]["seeded_program"] = product.read_text()
                    continue
                if decision.action == "REVIEW_DISPATCHED":
                    before = store.get_snapshot(campaign_id)
                    bindings = {key: host._bindings[key].to_dict() for key in decision.details["leases"]}
                    dispatched_prompts = len(native["prompts"])
                    supervisor = DeterministicSupervisor(store, host=host,
                        effect_driver=ExternalEffectDriver(store, delivery))
                    recovery = supervisor.recover()
                    require(recovery["reattached_read_only_leases"] == sorted(decision.details["leases"])
                            and recovery["invalidated_leases"] == 0 and not recovery["failed_nodes"],
                            f"Original native review connections did not recover: {recovery}")
                    require(store.get_snapshot(campaign_id) == before
                            and bindings == {key: host._bindings[key].to_dict() for key in bindings}
                            and len(native["prompts"]) == dispatched_prompts,
                            "Supervisor recovery changed original turns, state, epochs, or budgets")
                    native["supervisor_restart"] = {"result": recovery, "original_bindings": bindings,
                        "original_budgets": [item.to_dict() for item in before.budgets],
                        "state_epochs_budgets_unchanged": True, "additional_turns": 0,
                        "scope": "Supervisor instance restarted. Original App Server connections survived."}
                    receipts, findings = supervisor.collect_review_cohort(decision.details["leases"],
                                                                           timeout=worker_timeout)
                    native["review_receipts"], native["findings"] = receipts, findings
                    frozen = supervisor.freeze_review(campaign_id, decision.node_id,
                                                       receipts=receipts, findings=findings)
                    native["decisions"].append(frozen.to_dict())
                    continue
                if decision.action == "TERMINAL":
                    break
                require(not decision.wait_event, f"Native journey stopped: {decision.to_dict()}")
            else:
                raise AssertionError("Native journey reached its 32-decision observation bound")
            snapshot = store.get_snapshot(campaign_id)
            require(snapshot.state.value == "COMPLETED", f"Native journey ended in {snapshot.state.value}")
            require(snapshot.node("order-summary").validation_corrections == 1, "Expected one bounded correction")
            require({name: sha256(repo / name) for name in fixed} == fixed, "Fixed acceptance sources changed")
            accepted_head = git(remote, "rev-parse", "refs/heads/main")
            require(accepted_head == git(repo, "rev-parse", "HEAD"), "Delivered Git head differs")
            require(git(remote, "show", f"{accepted_head}:src/order_summary.py", binary=True) == product.read_bytes(),
                    "Delivered program bytes differ")
            require(delivery.executions == 1, "Delivery was not one actual push")
            pushes = [item for item in store.list_outbox(campaign_id=campaign_id) if item["kind"] == "PUSH"]
            require(len(pushes) == 1 and pushes[0]["state"] == "CONFIRMED",
                    "Production backend lacks one confirmed durable push receipt")
            for receipt in native["terminal_receipts"]:
                if receipt["role"] == "REVIEWER":
                    require_review_reads(native["tool_calls"], receipt["actor_id"],
                        {name: (repo / name).read_text() for name in
                         ("requirements.md", "test_acceptance.py", "src/order_summary.py")})
            require(not any(item["capture_overflow"] for item in native["transports"]),
                    "Native transcript exceeded its bounded artifact capacity")
            usage = store.usage_summary(campaign_id)
            require(usage["totals"] is not None
                    and usage["coverage"]["measured_actor_count"] == len(native["terminal_receipts"]) == 4,
                    "Native spend telemetry is incomplete for the four actual turns")
            native.update(accepted_head=accepted_head, accepted_tree=git(repo, "rev-parse", "HEAD^{tree}"),
                          artifact_sha256=sha256(product), business_outputs=business_outputs(product), status="passed")
        except Exception as exc:
            native["status"] = "failed"
            native["failure"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            close_native_run(host, supervisor, store, campaign_id, native)
            native["elapsed_seconds"] = round(time.monotonic() - started, 3)
            native["model_usage"] = store.usage_summary(campaign_id)
            native["actor_identities"] = store.list_actor_identities(campaign_id=campaign_id, active_only=False)
            native["publication_outbox"] = store.list_outbox(campaign_id=campaign_id)
            with sqlite3.connect(layout.state_db) as database:
                native["validation_receipts"] = [json.loads(row[0]) for row in database.execute(
                    "SELECT payload_json FROM evidence WHERE campaign_id=? AND kind='VALIDATION'", (campaign_id,))]
            native["final_state"] = store.get_snapshot(campaign_id).state.value
            native["source_contents"]["final_program"] = product.read_text()
            native["final_diff"] = git(repo, "diff", native["base_head"], "HEAD")
            native["final_head"] = git(repo, "rev-parse", "HEAD")
            bundle_path = artifacts / "native-product.git.bundle"
            git(repo, "bundle", "create", str(bundle_path), "--all")
            database_path = artifacts / "native-campaigns.sqlite3"
            with sqlite3.connect(layout.state_db) as database, sqlite3.connect(database_path) as backup:
                database.backup(backup)
            native["retained_artifacts"] = {name: {"path": str(path), "sha256": sha256(path)}
                for name, path in (("git_bundle", bundle_path), ("campaign_database", database_path))}
            store.close()
        report["completed_status"] = invoke_cli("status", "--campaign-id", campaign_id)
