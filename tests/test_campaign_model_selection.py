"""Approved model choices must survive admission, binding and dispatch."""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from scripts.agent.campaign_engine import host
from scripts.agent.campaign_engine.model import CampaignSpec, ModelValidationError
from tests.test_campaign_model_reducer import spec_dict
from tests.test_campaign_runtime_components import _FakeTransport


POLICY = {
    "roles": {
        "PARENT": {"model": "gpt-6.1-sol", "reasoning_effort": "high"},
        "IMPLEMENTER": {"model": "gpt-6.1-terra", "reasoning_effort": "high"},
        "REPAIRER": {"model": "gpt-6.1-terra", "reasoning_effort": "high"},
        "REVIEWER": {"model": "gpt-6.1-sol", "reasoning_effort": "high"},
        "CLOSURE_REVIEWER": {"model": "gpt-6.1-sol", "reasoning_effort": "high"},
    },
    "reviewers": {},
}


class ModelPolicyTests(unittest.TestCase):
    def test_policy_is_immutable_digest_bound_and_legacy_serialization_unchanged(self):
        raw = spec_dict()
        legacy = CampaignSpec.from_dict(raw)
        self.assertNotIn("model_policy", legacy.to_dict())
        raw["model_policy"] = copy.deepcopy(POLICY)
        configured = CampaignSpec.from_dict(raw)
        self.assertEqual(configured.to_dict()["model_policy"], POLICY)
        self.assertNotEqual(legacy.specification_digest, configured.specification_digest)
        changed = configured.to_dict()
        changed["model_policy"]["roles"]["IMPLEMENTER"]["reasoning_effort"] = "medium"
        with self.assertRaises(ModelValidationError):
            CampaignSpec.from_dict(changed)
        with self.assertRaises(TypeError):
            configured.model_policy["roles"]["IMPLEMENTER"]["model"] = "other"

    def test_reviewer_override_and_unknown_cohort_rejection(self):
        raw = spec_dict()
        reviewer = raw["required_review_cohort"][0]
        raw["model_policy"] = copy.deepcopy(POLICY)
        selected = {"model": "gpt-6-astra", "reasoning_effort": "xhigh"}
        raw["model_policy"]["reviewers"][reviewer] = selected
        spec = CampaignSpec.from_dict(raw)
        self.assertEqual(spec.actor_model("REVIEWER", reviewer), selected)
        self.assertEqual(spec.actor_model("CLOSURE_REVIEWER", reviewer), selected)
        self.assertEqual(spec.actor_model("IMPLEMENTER", reviewer), POLICY["roles"]["IMPLEMENTER"])
        raw["model_policy"]["reviewers"]["not-in-cohort"] = selected
        with self.assertRaises(ModelValidationError):
            CampaignSpec.from_dict(raw)

    def test_invalid_policy_never_silently_falls_back(self):
        for policy in [None, [], {"role": {}}, {"roles": {"VALIDATOR": POLICY["roles"]["PARENT"]}},
                       {"roles": {"IMPLEMENTER": {"model": "gpt-6.1-terra", "reasoning_effort": "typo"}}},
                       {"roles": {"IMPLEMENTER": {"model": " bad model ", "reasoning_effort": "high"}}},
                       {"roles": {"IMPLEMENTER": {"model": "gpt-6.1-terra", "effort": "high"}}}]:
            with self.subTest(policy=policy), self.assertRaises(ModelValidationError):
                CampaignSpec.from_dict({**spec_dict(), "model_policy": policy})


class ModelTransport(_FakeTransport):
    def request(self, method, params=None, timeout=None):
        if method == "model/list":
            self.calls.append((method, params))
            return {"data": [{"model": model, "supportedReasoningEfforts": [
                {"reasoningEffort": effort} for effort in efforts]}
                for model, efforts in [("gpt-6.1-terra", ["high"]), ("gpt-6.1-sol", ["high"]),
                                       ("gpt-6-astra", ["xhigh"]), ("gpt-6-luna", ["medium"])]],
                "nextCursor": None}
        return super().request(method, params, timeout)


class ModelHostTests(unittest.TestCase):
    def lease(self, root, **selection):
        return host.ActorLease.issue(lease_id="lease", request_id="request", campaign_id="campaign",
            node_id="node", actor_id="actor", role="PARENT", worktree=str(root), allowed_paths=(),
            authority_epoch=1, cancellation_epoch=0, fencing_epoch=1, candidate_head="a" * 40,
            **selection)

    def test_lease_digest_and_turn_use_verified_selection_not_mutable_host_default(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            lease = self.lease(root, model="gpt-6.1-terra", reasoning_effort="high")
            self.assertNotEqual(lease.payload_digest, self.lease(root).payload_digest)
            self.assertEqual(host.ActorLease.from_dict(lease.to_dict()), lease)
            tampered = lease.to_dict(); tampered["reasoning_effort"] = "medium"
            with self.assertRaises(host.HostAuthorityError):
                host.ActorLease.from_dict(tampered)
            transport = ModelTransport()
            native = host.NativeCodexHost(transport_factory=lambda *a, **k: transport)
            observed = []
            native.create_idle_actor(lease, bind_authority=lambda l, i: observed.append(i))
            native.model = "gpt-6-luna"
            native.reasoning_effort = "low"
            native.start_actor_turn(lease.lease_id, "Read only")
            params = next(p for m, p in transport.calls if m == "turn/start")
            self.assertEqual((params["model"], params["effort"]), (lease.model, lease.reasoning_effort))
            self.assertEqual(observed[0]["model"], lease.model)
            self.assertEqual(observed[0]["reasoning_effort"], lease.reasoning_effort)
            native.close()

    def test_unavailable_pair_fails_before_thread_or_turn_and_closes_transport(self):
        for model, effort in [("missing", "high"), ("gpt-6-luna", "ultra")]:
            with self.subTest(model=model), tempfile.TemporaryDirectory() as raw:
                transport = ModelTransport()
                native = host.NativeCodexHost(transport_factory=lambda *a, **k: transport)
                with self.assertRaises(host.HostAuthorityError):
                    native.create_idle_actor(self.lease(Path(raw), model=model, reasoning_effort=effort),
                                             bind_authority=lambda *a: self.fail("must not bind"))
                self.assertFalse(any(m in {"thread/start", "turn/start"} for m, p in transport.calls))
                self.assertTrue(transport.closed)

    def test_native_model_or_effort_substitution_fails_before_binding(self):
        for field, value in [("model", "gpt-6-luna"), ("reasoningEffort", "low"), ("model", None)]:
            class Substitution(ModelTransport):
                def request(self, method, params=None, timeout=None):
                    response = super().request(method, params, timeout)
                    if method == "thread/start": response[field] = value
                    return response
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as raw:
                transport = Substitution()
                native = host.NativeCodexHost(transport_factory=lambda *a, **k: transport)
                with self.assertRaises(host.HostAuthorityError):
                    native.create_idle_actor(self.lease(Path(raw), model="gpt-6.1-sol", reasoning_effort="high"),
                                             bind_authority=lambda *a: self.fail("must not bind"))
                self.assertFalse(any(m == "turn/start" for m, p in transport.calls))
                self.assertTrue(transport.closed)

    def test_catalogue_pagination_and_invalid_cursor_are_bounded(self):
        class Paged(ModelTransport):
            def request(self, method, params=None, timeout=None):
                if method == "model/list":
                    self.calls.append((method, params))
                    return {"data": [{"model": "gpt-6.1-sol" if "cursor" not in params else "gpt-6.1-terra",
                                     "supportedReasoningEfforts": [{"reasoningEffort": "high"}]}],
                            "nextCursor": "page-2" if "cursor" not in params else None}
                return super().request(method, params, timeout)
        transport = Paged()
        self.assertEqual(set(host.read_model_catalogue(transport)), {"gpt-6.1-sol", "gpt-6.1-terra"})
        self.assertEqual(transport.calls[1][1]["cursor"], "page-2")

        class Loop(ModelTransport):
            def request(self, method, params=None, timeout=None):
                return {"data": [], "nextCursor": "same"}
        with self.assertRaises(host.HostProtocolError):
            host.read_model_catalogue(Loop())

    def test_idle_policy_inspection_starts_no_turn(self):
        import subprocess
        transports = []
        def factory(*args, **kwargs):
            transport = ModelTransport()
            transports.append(transport)
            return transport
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for args in [("init", "-q"), ("-c", "user.name=Probe", "-c", "user.email=probe@example.invalid", "commit", "--allow-empty", "-q", "-m", "base")]:
                subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
            result = host.inspect_model_policy(cwd=root, policy=POLICY, verify_bindings=True, transport_factory=factory)
            self.assertEqual(result["turns_started"], 0)
            self.assertFalse(result["campaign_created"])
            self.assertEqual({(x["model"], x["reasoning_effort"]) for x in result["verified_bindings"]},
                             {("gpt-6.1-sol", "high"), ("gpt-6.1-terra", "high")})
            self.assertTrue(all(t.closed for t in transports))
            self.assertFalse(any(method == "turn/start" for t in transports for method, params in t.calls))
