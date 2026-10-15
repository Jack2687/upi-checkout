"""List cứng địa chỉ hoá đơn: mặc định TẮT (task chết sớm thay vì gửi địa chỉ sai IP)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extract_cs  # noqa: E402


def env_of(settings):
    return lambda name, default=None: settings.get(name, default)


class BillingFallbackTests(unittest.TestCase):
    def profile(self, settings, geo):
        # `verify_billing_address` tra India Post + probe exit (mạng thật) -> thay bằng
        # bản giả: test này chỉ kiểm tra công tắc fallback.
        with patch.object(extract_cs, "env_value", env_of(settings)), \
             patch.object(extract_cs, "env_bool",
                          lambda n, d=False: bool(settings.get(n, d))), \
             patch.object(extract_cs, "verify_billing_address", return_value="ok"), \
             patch.object(extract_cs, "geo_billing_from_proxy", return_value=geo):
            return extract_cs.upi_billing_profile("http://p@h:1")

    def test_off_by_default_raises_instead_of_using_the_hardcoded_list(self):
        with self.assertRaises(extract_cs.BillingAddressUnavailable) as err:
            self.profile({}, {})
        self.assertIn("UPI_BILLING_FALLBACK", str(err.exception))

    def test_switch_on_restores_the_hardcoded_list(self):
        got = self.profile({"UPI_BILLING_FALLBACK": "1"}, {})
        self.assertEqual(got["country"], "IN")
        self.assertTrue(got["city"] and got["postal_code"] and got["line1"])

    def test_geo_address_wins_over_the_switch(self):
        geo = {"email": "a@b.c", "name": "A B", "country": "IN", "line1": "1 Road",
               "line2": "", "city": "Patna", "postal_code": "800027", "state": "BR"}
        got = self.profile({}, geo)
        self.assertEqual((got["city"], got["postal_code"]), ("Patna", "800027"))

    def test_env_overrides_still_win(self):
        geo = {"email": "a@b.c", "name": "A B", "country": "IN", "line1": "1 Road",
               "line2": "", "city": "Patna", "postal_code": "800027", "state": "BR"}
        got = self.profile({"UPI_CITY": "Mumbai", "UPI_POSTAL_CODE": "400001"}, geo)
        self.assertEqual((got["city"], got["postal_code"]), ("Mumbai", "400001"))


if __name__ == "__main__":
    unittest.main()
