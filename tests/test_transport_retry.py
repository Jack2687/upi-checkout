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


class ConnectRetryAtRequestLayerTests(unittest.TestCase):
    """Thử lại ngay ở tầng request: phủ MỌI chặng mà không phải bọc từng call site."""

    def setUp(self):
        import tempfile
        ex._log_file = Path(tempfile.mkdtemp(prefix="upi-test-")) / "flow.log"

    class Resp:
        headers = {}
        content = b"ok"

    def call(self, fn):
        with patch.object(ex, "env_value", lambda n, d=None: d), \
             patch("time.sleep", lambda *_: None), \
             ex.execution_context({}):
            return ex._request_with_connect_retry(fn, object(), ("GET", "https://x"), {})

    def test_connect_error_is_retried(self):
        seen = []

        def flaky(session, *a, **kw):
            seen.append(1)
            if len(seen) < 3:
                raise RuntimeError("Failed to perform, curl: (35) TLS connect error: x")
            return self.Resp()

        self.assertIsInstance(self.call(flaky), self.Resp)
        self.assertEqual(len(seen), 3)

    def test_gives_up_after_three_attempts(self):
        seen = []

        def always(session, *a, **kw):
            seen.append(1)
            raise RuntimeError("curl: (7) Couldn't connect to server")

        with self.assertRaises(RuntimeError):
            self.call(always)
        self.assertEqual(len(seen), 3)

    def test_timeout_is_not_retried_here(self):
        # timeout giữa request có thể đã tới server -> không tự thử lại ở tầng này
        seen = []

        def slow(session, *a, **kw):
            seen.append(1)
            raise RuntimeError("Timeout: Failed to perform, curl: (28) Connection timed out")

        with self.assertRaises(RuntimeError):
            self.call(slow)
        self.assertEqual(len(seen), 1)

    def test_http_error_is_not_retried(self):
        seen = []

        def blocked(session, *a, **kw):
            seen.append(1)
            raise RuntimeError("checkout creation failed HTTP 403")

        with self.assertRaises(RuntimeError):
            self.call(blocked)
        self.assertEqual(len(seen), 1)


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
