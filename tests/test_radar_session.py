"""Radar Session: tín hiệu thiết bị mà tích hợp Direct API thường thiếu (docs: +36% model).

Nguồn: https://docs.stripe.com/radar/optimize-risk-factors — "Advanced risk factors
(device characteristics… captured by Radar Sessions)" là nhóm tín hiệu đáng giá nhất;
email/name/địa chỉ chỉ ~15% và luồng này đã gửi đủ.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extract_cs as ex  # noqa: E402


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


class RadarSessionTests(unittest.TestCase):
    def setUp(self):
        ex._log_file = Path(__import__("tempfile").mkdtemp(prefix="upi-test-")) / "flow.log"

    def test_off_by_default_does_not_call_stripe(self):
        with patch.object(ex, "env_bool", lambda n, d=False: False), \
             patch("requests.post", side_effect=AssertionError("must not call")):
            self.assertEqual(ex.stripe_radar_session(SimpleNamespace(), "pk_test"), "")

    def test_creates_and_caches_the_session(self):
        calls = []

        def post(url, **kw):
            calls.append((url, kw.get("data")))
            return FakeResp({"id": "rse_test123"})

        with patch.object(ex, "env_bool", lambda n, d=False: True), \
             patch.object(ex, "stripe_telemetry_ids",
                          return_value={"muid": "m", "sid": "s", "guid": "g"}), \
             ex.execution_context({}):
            session = ex.stripe_radar_session(SimpleNamespace(post=post), "pk_test")
            again = ex.stripe_radar_session(SimpleNamespace(post=post), "pk_test")
        self.assertEqual(session, "rse_test123")
        self.assertEqual(again, "rse_test123")
        self.assertEqual(len(calls), 1)                       # lần 2 lấy từ context
        url, data = calls[0]
        self.assertEqual(url, "https://api.stripe.com/v1/radar/session")
        self.assertEqual(data["key"], "pk_test")
        self.assertEqual((data["muid"], data["sid"], data["guid"]), ("m", "s", "g"))

    def test_failure_is_swallowed(self):
        def post(url, **kw):
            raise RuntimeError("curl: (35) TLS connect error")

        with patch.object(ex, "env_bool", lambda n, d=False: True), \
             patch.object(ex, "stripe_telemetry_ids", return_value={}), \
             ex.execution_context({}):
            self.assertEqual(ex.stripe_radar_session(SimpleNamespace(post=post), "pk"), "")

    def test_upi_payment_method_carries_radar_options(self):
        sent = {}

        class Stripe:
            def post(self, url, data=None, **kw):
                sent["url"] = url
                sent["body"] = dict(data or {})
                return FakeResp({"id": "pm_123"})

        billing = {"name": "A B", "email": "a@b.c", "country": "IN", "line1": "1 X",
                   "city": "Patna", "postal_code": "800027", "state": "BR"}
        with patch.object(ex, "stripe_radar_session", return_value="rse_abc"), \
             patch.object(ex, "dump_http", lambda *a, **k: None):
            pm = ex.stripe_create_upi_pm(Stripe(), "cs_1", "pk", billing, {})
        self.assertEqual(pm, "pm_123")
        self.assertEqual(sent["body"]["radar_options[session]"], "rse_abc")

    def test_no_radar_key_when_session_unavailable(self):
        sent = {}

        class Stripe:
            def post(self, url, data=None, **kw):
                sent["body"] = dict(data or {})
                return FakeResp({"id": "pm_9"})

        billing = {"name": "A B", "email": "a@b.c", "country": "IN", "line1": "1 X",
                   "city": "Patna", "postal_code": "800027"}
        with patch.object(ex, "stripe_radar_session", return_value=""), \
             patch.object(ex, "dump_http", lambda *a, **k: None):
            ex.stripe_create_upi_pm(Stripe(), "cs_1", "pk", billing, {})
        self.assertNotIn("radar_options[session]", sent["body"])


if __name__ == "__main__":
    unittest.main()
