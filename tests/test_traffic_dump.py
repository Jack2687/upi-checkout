"""Dump toàn bộ API ở tầng transport: HAR entry cho mọi request, kèm cả traffic của Node.

Khác `dump_http()` (ghi tay ở ~24 điểm, không có header), recorder này bọc `Session.request`
nên phủ mọi call của flow Python, và `sentinel_runner.js` ghi vào cùng file qua `UPI_DUMP_FILE`.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import traffic_dump as td  # noqa: E402


class FakeResp:
    def __init__(self, payload="{}", status=200, headers=None):
        self.text = payload
        self.content = payload.encode()
        self.status_code = status
        self.headers = headers or {"content-type": "application/json"}


class BuildEntryTests(unittest.TestCase):
    def test_keeps_both_directions_with_raw_headers(self):
        entry = td.build_entry(
            method="post", url="https://api.stripe.com/v1/payment_methods?x=1",
            request_headers={"Authorization": "Bearer sk_live_abc", "Cookie": "a=b",
                             "Content-Type": "application/json", "User-Agent": "Chrome/146"},
            request_body={"key": "pk_live_1"},
            status=400, response_headers={"content-type": "application/json"},
            response_body='{"error": {"code": "parameter_unknown"}}',
            elapsed_ms=12.5,
        )
        self.assertEqual(entry["request"]["method"], "POST")
        headers = {h["name"]: h["value"] for h in entry["request"]["headers"]}
        self.assertEqual(headers["Authorization"], "Bearer sk_live_abc")   # ghi nguyên văn
        self.assertEqual(headers["Cookie"], "a=b")
        self.assertEqual(headers["User-Agent"], "Chrome/146")
        self.assertEqual(entry["request"]["queryString"], [{"name": "x", "value": "1"}])
        self.assertIn("pk_live_1", entry["request"]["postData"]["text"])
        self.assertEqual(entry["response"]["status"], 400)
        self.assertIn("parameter_unknown", entry["response"]["content"]["text"])
        self.assertEqual(entry["host"], "api.stripe.com")
        self.assertEqual(entry["path"], "/v1/payment_methods")
        self.assertEqual(entry["time"], 12.5)

    def test_error_is_recorded(self):
        entry = td.build_entry(method="GET", url="https://chatgpt.com/x", error="ProxyError: boom")
        self.assertEqual(entry["response"]["status"], 0)
        self.assertIn("ProxyError", entry["error"])

    def test_body_limit_truncates(self):
        entry = td.build_entry(method="POST", url="https://x/y", request_body="a" * 500,
                               response_body="b" * 500, body_limit=100)
        self.assertEqual(len(entry["request"]["postData"]["text"]), 100)
        self.assertEqual(len(entry["response"]["content"]["text"]), 100)


class JsonlRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp(prefix="upi-har-"))

    def test_append_read_and_convert_to_har(self):
        path = td.dump_path(self.tmp, "t3")
        self.assertTrue(str(path).endswith("t3/t3.jsonl"))
        td.append_entry(path, td.build_entry(method="GET", url="https://chatgpt.com/backend-api/a"))
        td.append_entry(path, td.build_entry(method="POST", url="https://api.stripe.com/v1/payment_methods"))
        entries = td.read_jsonl(path)
        self.assertEqual(len(entries), 2)
        har = td.to_har(entries)
        self.assertEqual(har["log"]["version"], "1.2")
        self.assertEqual(len(har["log"]["entries"]), 2)
        self.assertEqual(har["log"]["_hosts"], ["chatgpt.com", "api.stripe.com"])

    def test_broken_line_is_skipped(self):
        path = td.dump_path(self.tmp, "t4")
        path.write_text('{"request": {"url": "https://a/b"}}\nnot json\n', encoding="utf-8")
        self.assertEqual(len(td.read_jsonl(path)), 1)


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp(prefix="upi-har-"))

    def test_target_and_child_env(self):
        td.set_target(enabled=False, root=self.tmp, label="t9")
        self.assertIsNone(td.entry_path())
        self.assertEqual(td.child_env(), {})
        td.set_target(enabled=True, root=self.tmp, label="t9", body_limit=1234)
        self.assertEqual(td.entry_path(), self.tmp / "t9" / "t9.jsonl")
        self.assertEqual(td.body_limit(), 1234)
        self.assertEqual(td.child_env(), {"UPI_DUMP": "1",
                                          "UPI_DUMP_FILE": str(self.tmp / "t9" / "t9.jsonl")})
        td.set_target(enabled=False, root=self.tmp, label="t9")


class TransportRecorderTests(unittest.TestCase):
    """Recorder phải chạy trong chính wrapper transport của extract_cs."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="upi-har-"))
        import extract_cs as ex
        self.ex = ex
        ex._log_file = self.tmp / "flow.log"

    def test_session_request_is_recorded_with_headers(self):
        ex = self.ex
        calls = []

        def fake_original(session, *args, **kwargs):
            calls.append(args)
            return FakeResp('{"id": "pm_1"}', 200, {"content-type": "application/json"})

        wrapped = ex._counting_request(fake_original)

        class Session:
            headers = {"User-Agent": "Chrome/146"}

        with ex.execution_context({"UPI_DUMP": "1", "UPI_DUMP_DIR": str(self.tmp),
                                   "UPI_TASK_LABEL": "t7"}):
            wrapped(Session(), "POST", "https://api.stripe.com/v1/payment_methods",
                    data={"key": "pk_live_x"},
                    headers={"Authorization": "Bearer eyJhbGciOi", "Origin": "https://js.stripe.com"})

        path = self.tmp / "t7" / "t7.jsonl"
        self.assertTrue(path.exists())
        entry = json.loads(path.read_text().splitlines()[0])
        headers = {h["name"]: h["value"] for h in entry["request"]["headers"]}
        self.assertEqual(entry["request"]["url"], "https://api.stripe.com/v1/payment_methods")
        self.assertEqual(headers["User-Agent"], "Chrome/146")
        self.assertEqual(headers["Authorization"], "Bearer eyJhbGciOi")
        self.assertEqual(headers["Origin"], "https://js.stripe.com")
        self.assertEqual(entry["response"]["status"], 200)
        self.assertEqual(len(calls), 1)

    def test_nothing_is_written_when_flag_is_off(self):
        ex = self.ex
        wrapped = ex._counting_request(lambda session, *a, **k: FakeResp())
        with ex.execution_context({"UPI_DUMP_DIR": str(self.tmp), "UPI_TASK_LABEL": "t8"}):
            wrapped(object(), "GET", "https://chatgpt.com/x")
        self.assertEqual(list(self.tmp.glob("t8/*")), [])

    def test_transport_error_is_recorded_and_reraised(self):
        ex = self.ex

        def boom(session, *args, **kwargs):
            raise RuntimeError("curl: (35) TLS connect error")

        wrapped = ex._counting_request(boom)
        with ex.execution_context({"UPI_DUMP": "1", "UPI_DUMP_DIR": str(self.tmp),
                                   "UPI_TASK_LABEL": "t9", "UPI_CONNECT_RETRY": "1"}):
            with self.assertRaises(RuntimeError):
                wrapped(object(), "GET", "https://api.stripe.com/v1/x")
        entry = json.loads((self.tmp / "t9" / "t9.jsonl").read_text().splitlines()[0])
        self.assertIn("TLS connect error", entry["error"])


if __name__ == "__main__":
    unittest.main()
