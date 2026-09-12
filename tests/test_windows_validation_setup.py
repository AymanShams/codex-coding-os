"""Native setup completion evidence, without provisioning or starting a model."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import prepare_windows_validation_boundary as setup


class WindowsValidationSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ccos-native-setup-contract-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.executable = self.root / "package" / "bin" / "codex.exe"
        self.helper = self.root / "package" / "codex-resources" / "codex-windows-sandbox-setup.exe"
        for path in (self.executable, self.helper):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(path.name.encode())
        self.profile = self.root / "account"
        self.logs = self.profile / ".codex" / ".sandbox"
        self.logs.mkdir(parents=True)
        self.log = self.logs / f"sandbox.{datetime.now(timezone.utc).date()}.log"
        self.output = self.root / "receipt.json"
        self.clock = 0.0
        self.calls = []
        self.readiness = "ready"
        self.on_setup = lambda: self.append(self.complete_log())
        self.on_pump = lambda: None
        self.notification = {"mode": "elevated", "success": True, "error": None}
        self.close_error = None
        test = self

        class Transport:
            def __init__(self, executable, *, cwd, timeout):
                test.assertEqual(executable, test.executable)
                test.assertEqual(cwd, test.root)
                self.command = [str(executable), "app-server"]
                self.events = []
                self.closed = False
                test.transport = self

            def start(self):
                test.calls.append(("start", list(self.command)))

            def request(self, method, params=None, *, timeout):
                test.calls.append((method, params))
                if method == "windowsSandbox/readiness":
                    return {"status": test.readiness}
                if method == "windowsSandbox/setupStart":
                    test.on_setup()
                    if test.notification is not None:
                        self.events.append({"method": "windowsSandbox/setupCompleted", "params": test.notification})
                    return {"started": True}
                test.assertEqual(method, "initialize")
                return {}

            def notify(self, method, params):
                test.calls.append((method, params))

            def _pump(self, timeout):
                test.clock += timeout
                test.on_pump()

            def diagnostic_snapshot(self):
                return {"event_count": len(self.events)}

            def close(self):
                self.closed = True
                if test.close_error:
                    raise RuntimeError(test.close_error)

        self.addCleanup(patch.stopall)
        patch.object(setup, "AppServerTransport", Transport).start()
        patch.object(setup.sys, "platform", "win32").start()
        patch.object(setup.time, "monotonic", lambda: self.clock).start()
        patch.dict(os.environ, {"USERPROFILE": str(self.profile), "CODEX_HOME": str(self.profile / ".codex")}).start()

    def complete_log(self, *, cwd=None, helper=None):
        return (f"setup refresh: spawning {helper or self.helper} (cwd={cwd or self.root}, payload_len=123)\n"
                "read-acl-only mode: applying read ACLs\n"
                "read ACL run completed\n")

    def append(self, text):
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(text)

    def prepare(self):
        return setup.prepare(self.executable, self.root, setup.digest(self.executable.read_bytes()), self.output, timeout=1)

    def persisted(self):
        return json.loads(self.output.read_text(encoding="utf-8"))

    def test_one_setup_request_waits_for_fresh_source_and_cwd_bound_completion(self):
        self.append("old native setup log\n")
        prior_bytes = self.log.stat().st_size
        # Historical days do not become completion evidence or prevent setup.
        for day in range(1, 5):
            (self.logs / f"sandbox.2000-01-0{day}.log").write_text(self.complete_log(), encoding="utf-8")
        result = self.prepare()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result, self.persisted())
        self.assertEqual(result["setup_request"], {"mode": "elevated", "cwd": str(self.root)})
        self.assertEqual(result["sources"][str(self.helper)], setup.digest(self.helper.read_bytes()))
        self.assertEqual(result["log_generation"][str(self.log)]["bytes"], prior_bytes)
        self.assertNotIn("old native", result["native_log"])
        self.assertTrue(self.transport.closed)
        self.assertNotIn("-P", self.calls[0][1])
        self.assertIn('default_permissions=":read-only"', self.calls[0][1])
        self.assertIn('windows.sandbox="elevated"', self.calls[0][1])
        self.assertEqual([method for method, _ in self.calls], ["start", "initialize", "initialized",
            "windowsSandbox/readiness", "windowsSandbox/setupStart"])

    def test_foreground_setup_success_waits_for_background_read_completion(self):
        self.on_setup = lambda: self.append(self.complete_log().replace("read ACL run completed\n", ""))
        self.on_pump = lambda: self.append("read ACL run completed\n")
        self.assertEqual(self.prepare()["status"], "ready")
        self.assertEqual(self.clock, .25)
        self.assertEqual(sum(method == "windowsSandbox/setupStart" for method, _ in self.calls), 1)

    def test_success_notification_cannot_reuse_completion_before_request(self):
        self.append(self.complete_log())
        self.on_setup = lambda: None
        with self.assertRaisesRegex(RuntimeError, "deadline"):
            self.prepare()
        result = self.persisted()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["native_log"], "")
        self.assertTrue(result["setup_completed"]["success"])
        self.assertTrue(self.transport.closed)
        self.assertEqual(sum(method == "windowsSandbox/setupStart" for method, _ in self.calls), 1)

    def test_fresh_read_log_alone_cannot_replace_setup_completion_notification(self):
        self.notification = None
        with self.assertRaisesRegex(RuntimeError, "deadline"):
            self.prepare()
        self.assertEqual(self.persisted()["status"], "failed")
        self.assertIn("read ACL run completed", self.persisted()["native_log"])

    def test_wrong_source_or_cwd_and_ambiguous_helpers_cannot_pass(self):
        for text in (self.complete_log(cwd=self.profile), self.complete_log(helper=self.executable),
                     self.complete_log() + self.complete_log(), "read ACL run completed\n",
                     "read-acl-only mode: applying read ACLs\nread ACL run completed\n",
                     self.complete_log().replace("read ACL run completed", "read ACL run completed with errors: denied"),
                     self.complete_log().replace("read ACL run completed", "read ACL helper already running")):
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                setup.native_read_setup_complete(text, self.root, self.helper)

    def test_log_truncation_or_replacement_cannot_supply_fresh_completion(self):
        self.append("prior generation bytes\n")
        self.on_setup = lambda: self.log.write_text(self.complete_log(), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "generation changed"):
            self.prepare()
        self.assertEqual(self.persisted()["status"], "failed")

    def test_native_source_change_after_request_is_rejected(self):
        def changed_source():
            self.append(self.complete_log())
            self.helper.write_bytes(b"different native helper")
        self.on_setup = changed_source
        with self.assertRaisesRegex(RuntimeError, "executable changed"):
            self.prepare()
        self.assertEqual(self.persisted()["status"], "failed")

    def test_failed_native_notification_preserves_failure_and_log(self):
        self.notification = {"mode": "elevated", "success": False, "error": "read setup denied"}
        with self.assertRaisesRegex(RuntimeError, "reported failure"):
            self.prepare()
        self.assertEqual(self.persisted()["setup_completed"]["error"], "read setup denied")
        self.assertIn("read ACL run completed", self.persisted()["native_log"])

    def test_missing_backend_stops_before_setup_request(self):
        self.readiness = "updateRequired"
        with self.assertRaisesRegex(RuntimeError, "Provision"):
            self.prepare()
        self.assertEqual(self.persisted()["backend_readiness"]["status"], "updateRequired")
        self.assertFalse(any(method == "windowsSandbox/setupStart" for method, _ in self.calls))

    def test_wrong_expected_package_hash_stops_before_native_process(self):
        with self.assertRaisesRegex(RuntimeError, "expected package identity"):
            setup.prepare(self.executable, self.root, "0" * 64, self.output)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.persisted()["status"], "failed")

    def test_close_failure_cannot_discard_receipt_or_return_ready(self):
        self.close_error = "close fixture failure"
        with self.assertRaisesRegex(RuntimeError, "did not close"):
            self.prepare()
        self.assertEqual(self.persisted()["status"], "failed")
        self.assertEqual(self.persisted()["close_error"], self.close_error)


if __name__ == "__main__":
    unittest.main()
