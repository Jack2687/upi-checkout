"""Địa chỉ/PIN hoá đơn: tách dòng đúng + kiểm tra PIN có thật và khớp vùng IP."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import engine  # noqa: E402
import extract_cs as ex  # noqa: E402

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
        with patch("geobilling._pin_record", side_effect=lambda pin, timeout=0: DB.get(pin, {})), \
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
