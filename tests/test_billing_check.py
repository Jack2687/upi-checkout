"""Địa chỉ/PIN hoá đơn: tách dòng đúng + kiểm tra PIN có thật và khớp vùng IP."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import engine  # noqa: E402
import extract_cs as ex  # noqa: E402

# Các test này gọi code có `log()` -> phải trỏ log sang file tạm, nếu không mỗi lần chạy
# test lại đẻ ra một file logs/upi_<ts>.log rác trong repo.
ex._log_file = Path(tempfile.mkdtemp(prefix="upi-test-")) / "flow.log"

DB = {"800027": {"pin": "800027", "state": "Bihar", "district": "Patna", "areas": ["Patna GPO"]},
      "411001": {"pin": "411001", "state": "Maharashtra", "district": "Pune", "areas": ["Pune City"]},
      "800001": {"pin": "800001", "state": "Bihar", "district": "Patna", "areas": ["Patna City"]}}


class BillingLineParseTests(unittest.TestCase):
    def parse(self, line):
        t = engine.Task(task_id="t1", index=0, token="tok", email="a@b.c")
        engine._absorb_billing(t, line)
        return t

    def test_address_containing_slash_is_not_split_wrong(self):
        # 12,8% bản ghi cũ bị tách sai vì địa chỉ chứa " / ".
        t = self.parse("[PM=IN] IN details filled into PM billing_details: "
                       "Neha Chaudhary / 90, Golden Residency, Pipola / Tehri Garhwal 249001")
        self.assertEqual((t.billing_name, t.billing_city, t.billing_pin),
                         ("Neha Chaudhary", "Tehri Garhwal", "249001"))

    def test_plain_line_still_parses(self):
        t = self.parse("[PM=IN] IN details filled into PM billing_details: "
                       "Vivaan Joshi / 122 Park Street / Kolkata 700016")
        self.assertEqual((t.billing_city, t.billing_pin), ("Kolkata", "700016"))

    def test_check_line_is_recorded(self):
        t = self.parse("billing check: mismatch-exit | PIN 800027 -> Bihar / Patna | exit MH")
        self.assertEqual(t.billing_check, "mismatch-exit")
        self.assertEqual(t.to_dict()["billing_check"], "mismatch-exit")


class AddressVerifyTests(unittest.TestCase):
    def verify(self, billing, exit_region="Bihar", exit_code="BR", seeds=("800001",)):
        with patch("geobilling._pin_record", side_effect=lambda pin, timeout=0, proxy="": DB.get(pin, {})), \
             patch("geobilling._seed_pins", return_value=list(seeds)), \
             patch("geobilling.probe_exit", return_value={"region": exit_region, "region_code": exit_code}):
            out = dict(billing)
            verdict = ex.verify_billing_address(out, "http://u:p@h:1")
        return verdict, out

    def test_ok_when_everything_matches(self):
        verdict, out = self.verify({"postal_code": "800027", "city": "Patna", "state": "BR"})
        self.assertEqual(verdict, "ok")

    def test_city_and_state_are_corrected_from_the_pin(self):
        verdict, out = self.verify({"postal_code": "800027", "city": "Patna Sahib", "state": "MH"})
        self.assertEqual(verdict, "fixed")
        self.assertEqual((out["city"], out["state"]), ("Patna", "BR"))

    def test_exit_region_mismatch_is_flagged(self):
        verdict, _ = self.verify({"postal_code": "411001", "city": "Pune", "state": "MH"})
        self.assertEqual(verdict, "mismatch-exit")

    def test_bogus_pin_is_replaced_by_a_verified_one(self):
        verdict, out = self.verify({"postal_code": "999999", "city": "X", "state": "BR",
                                    "line1": "12 Old Road"})
        self.assertEqual(verdict, "fixed-pin")
        self.assertEqual((out["postal_code"], out["city"]), ("800001", "Patna"))
        self.assertTrue(out["line1"].startswith("12 "))


if __name__ == "__main__":
    unittest.main()


class GeoCacheTests(unittest.TestCase):
    """Cache tra cứu mức 1 theo proxy: pool 500 session dùng lại -> không cache thì mỗi task
    gọi lại ipwho.is và bị rate-limit ('Max retries exceeded') rồi mất địa chỉ."""

    class Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def test_second_call_for_same_proxy_hits_the_cache(self):
        calls = []

        def fake_get(url, **kw):
            calls.append(url)
            return self.Resp({"success": True, "country_code": "IN", "city": "Patna",
                              "region": "Bihar", "postal": "800001",
                              "latitude": 25.6, "longitude": 85.1, "ip": "1.2.3.4"})

        with patch.object(ex, "requests", SimpleNamespace(get=fake_get)), \
             patch.object(ex, "geo_billing_via_module", return_value={}), \
             patch.object(ex, "log", lambda *a, **k: None):
            ex._GEO_TIER1_CACHE.clear()
            first = ex.geo_billing_from_proxy("http://u:p@h:1")
            before = len(calls)
            second = ex.geo_billing_from_proxy("http://u:p@h:1")
        self.assertEqual(first, second)
        self.assertEqual(len(calls), before)          # lần 2 không gọi mạng

    def test_failure_is_cached_only_briefly(self):
        with patch.object(ex, "env_value", lambda n, d=None: d):
            self.assertEqual(ex._geo_tier1_ttl(True), 21600.0)
            self.assertEqual(ex._geo_tier1_ttl(False), 300.0)
