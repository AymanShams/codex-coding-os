"""Offline checks for the opt-in native journey's independent acceptance."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.native_product_journey import business_outputs, require_review_reads


class BusinessAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.program = Path(temporary.name) / "orders.py"

    def write_program(self, count, total="sum(amounts)"):
        self.program.write_text(
            "import json, sys\n"
            "amounts = [order['amount'] for order in json.load(sys.stdin)['orders']]\n"
            f"print(json.dumps({{'order_count': {count}, 'total': {total}}}))\n",
            encoding="utf-8",
        )

    def test_accepts_every_entry_and_preserves_integer_cents(self):
        self.write_program("len(amounts)")
        observations = business_outputs(self.program)
        self.assertEqual(len(observations), 6)
        self.assertEqual(observations[1]["actual"], {"order_count": 3, "total": 1000})
        self.assertEqual(observations[4]["actual"], {"order_count": 2, "total": -100})

    def test_rejects_seeded_positive_only_count(self):
        self.write_program("sum(amount > 0 for amount in amounts)")
        with self.assertRaisesRegex(AssertionError, "zero and refund entries"):
            business_outputs(self.program)

    def test_rejects_amount_and_json_type_regressions(self):
        for count, total in (("len(amounts)", "sum(abs(amount) for amount in amounts)"),
                             ("float(len(amounts))", "sum(amounts)")):
            with self.subTest(count=count, total=total):
                self.write_program(count, total)
                with self.assertRaises(AssertionError):
                    business_outputs(self.program)


class ReviewObservationTests(unittest.TestCase):
    def read(self, actor="reviewer", start=1, end=2, text="first\nsecond"):
        return {"actor_id": actor, "tool": "campaign_read_file",
                "result": {"path": "requirements.md", "start_line": start, "end_line": end, "text": text}}

    def test_complete_or_split_native_reads_cover_the_fixed_source(self):
        sources = {"requirements.md": "first\nsecond\n"}
        require_review_reads([self.read()], "reviewer", sources)
        require_review_reads([self.read(start=1, end=1, text="first"),
                              self.read(start=2, end=2, text="second")], "reviewer", sources)

    def test_other_actor_partial_failed_and_changed_reads_cannot_prove_review(self):
        sources = {"requirements.md": "first\nsecond\n"}
        for calls in ([self.read(actor="implementer")],
                      [self.read(start=1, end=1, text="first")],
                      [{"actor_id": "reviewer", "tool": "campaign_read_file", "error": "denied"}],
                      [self.read(text="first\nchanged")]):
            with self.subTest(calls=calls), self.assertRaises(AssertionError):
                require_review_reads(calls, "reviewer", sources)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux account namespace contract")
class NativeAuthenticationMountTests(unittest.TestCase):
    def setUp(self):
        from tests import supported_install_journey
        self.journey = supported_install_journey
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profile = Path(temporary.name)
        self.codex = self.profile / ".codex"
        self.codex.mkdir()
        (self.codex / "auth.json").write_text("fixture", encoding="utf-8")

    def check_account(self, *, auth_mode="ro", native=True):
        mounts = ("1 0 0:1 / / ro - ext4 mock ro\n"
                  f"2 1 0:2 / {self.profile} rw - tmpfs tmpfs rw\n"
                  "3 1 0:3 / /tmp rw - tmpfs tmpfs rw\n"
                  f"4 2 0:4 / {self.codex / 'auth.json'} {auth_mode} - ext4 mock {auth_mode}\n")
        original_read = Path.read_text

        def read(path, *args, **kwargs):
            if str(path) == "/proc/self/uid_map":
                return "1000 0 1\n"
            if str(path) == "/proc/self/mountinfo":
                return mounts
            return original_read(path, *args, **kwargs)

        with patch.object(self.journey, "ACCOUNT_PROFILE", self.profile), \
             patch.object(self.journey, "CODEX", self.codex), \
             patch.object(self.journey.os, "getuid", return_value=1000), \
             patch.dict(self.journey.os.environ, {"HOME": str(self.profile)}, clear=True), \
             patch.object(Path, "read_text", read):
            return self.journey.disposable_account(native=native)

    def test_explicit_native_mode_accepts_only_readonly_auth_file(self):
        self.assertEqual(self.check_account()["os_profile"], str(self.profile))
        with self.assertRaisesRegex(AssertionError, "read-only file mount"):
            self.check_account(auth_mode="rw")

    def test_native_mode_rejects_preexisting_engine_or_configuration(self):
        (self.codex / "config.toml").write_text("fixture", encoding="utf-8")
        with self.assertRaisesRegex(AssertionError, "only read-only auth.json"):
            self.check_account()

    def test_default_scripted_mode_does_not_accept_authentication(self):
        with self.assertRaisesRegex(AssertionError, "must start empty"):
            self.check_account(native=False)


if __name__ == "__main__":
    unittest.main()
