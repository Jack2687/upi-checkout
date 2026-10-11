"""Cờ `UPI_STRIPE_TELEMETRY`: id phiên Stripe lấy từ m.stripe.com thay vì tự bịa.

Offline: không gọi mạng, `requests.post` bị thay bằng fixture.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extract_cs  # noqa: E402

REAL_IDS = {"muid": "muid-from-stripe", "guid": "guid-from-stripe", "sid": "sid-from-stripe"}


def enabled(name, default=False):
    return True if name == "UPI_STRIPE_TELEMETRY" else default


def reply(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    return resp


class StripeTelemetryTests(unittest.TestCase):
    def test_off_by_default_never_calls_the_endpoint(self):
        with patch("requests.post", side_effect=AssertionError("must not call m.stripe.com")):
            ctx = extract_cs.build_ctx({"currency": "inr"}, {})
        self.assertNotIn(ctx["muid"], REAL_IDS.values())
        self.assertGreater(len(ctx["muid"]), 20)

    def test_on_uses_the_ids_stripe_issues(self):
        with patch.object(extract_cs, "env_bool", enabled), \
             patch("requests.post", return_value=reply(REAL_IDS)) as post:
            ctx = extract_cs.build_ctx({"currency": "inr"}, {})
        self.assertEqual([ctx["muid"], ctx["guid"], ctx["sid"]],
                         [REAL_IDS["muid"], REAL_IDS["guid"], REAL_IDS["sid"]])
        self.assertEqual(post.call_args.args[0], "https://m.stripe.com/6")

    def test_network_error_falls_back_to_generated_ids(self):
        with patch.object(extract_cs, "env_bool", enabled), \
             patch("requests.post", side_effect=RuntimeError("boom")):
            ctx = extract_cs.build_ctx({"currency": "inr"}, {})
        self.assertNotIn(ctx["muid"], REAL_IDS.values())
        self.assertGreater(len(ctx["sid"]), 20)

    def test_incomplete_response_falls_back_to_generated_ids(self):
        with patch.object(extract_cs, "env_bool", enabled), \
             patch("requests.post", return_value=reply({"muid": "only-one"})):
            ctx = extract_cs.build_ctx({"currency": "inr"}, {})
        self.assertNotEqual(ctx["muid"], "only-one")
        self.assertGreater(len(ctx["guid"]), 20)


if __name__ == "__main__":
    unittest.main()
