"""Lỗi vận chuyển (TLS/kết nối) thì thử lại; lỗi nghiệp vụ thì ném ngay.

Đo thực tế: 200-300 worker cùng mở kết nối qua proxy -> ~12% handshake TLS rơi
(`curl: (35) TLS connect error`), nhưng chỉ 20 kết nối song song thì 0%. Đây là lỗi tạm
thời do quá tải phía proxy, nên thử lại vài lần là qua — trước đây mỗi lần rơi là mất
luôn cả task ("no link — flow stopped at step 'approve'").
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extract_cs as ex  # noqa: E402

TLS = "Failed to perform, curl: (35) TLS connect error: error:00000000:invalid library"


class TransportRetryTests(unittest.TestCase):
    def setUp(self):
        # log() ghi vào logs/ -> trỏ sang file tạm cho sạch repo
        import tempfile
        ex._log_file = Path(tempfile.mkdtemp(prefix="upi-test-")) / "flow.log"

    def test_classification(self):
        self.assertTrue(ex.is_transport_error(TLS))
        self.assertTrue(ex.is_transport_error("Timeout: Failed to perform"))
        self.assertFalse(ex.is_transport_error("checkout creation failed HTTP 403"))
        self.assertFalse(ex.is_transport_error("Stripe risk decline (generic_decline)"))
        self.assertFalse(ex.is_transport_error(""))

    def test_retries_then_succeeds(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError(TLS)
            return "ok"

        with patch.object(ex, "env_value", lambda n, d=None: d), \
             patch("time.sleep", lambda *_: None):
            self.assertEqual(ex.with_transport_retry(flaky, label="t"), "ok")
        self.assertEqual(len(calls), 3)

    def test_gives_up_after_the_limit(self):
        calls = []

        def always():
            calls.append(1)
            raise RuntimeError(TLS)

        with patch.object(ex, "env_value", lambda n, d=None: d), \
             patch("time.sleep", lambda *_: None), \
             self.assertRaises(RuntimeError):
            ex.with_transport_retry(always, label="t")
        self.assertEqual(len(calls), 3)          # mặc định 3 lượt

    def test_business_error_is_not_retried(self):
        calls = []

        def blocked():
            calls.append(1)
            raise RuntimeError("checkout creation failed HTTP 403: blocked")

        with patch("time.sleep", lambda *_: None), self.assertRaises(RuntimeError):
            ex.with_transport_retry(blocked, label="t")
        self.assertEqual(len(calls), 1)

    def test_attempts_are_configurable(self):
        calls = []

        def always():
            calls.append(1)
            raise RuntimeError(TLS)

        with patch.object(ex, "env_value", lambda n, d=None: "1" if n == "UPI_TRANSPORT_RETRY" else d), \
             patch("time.sleep", lambda *_: None), self.assertRaises(RuntimeError):
            ex.with_transport_retry(always, label="t")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
