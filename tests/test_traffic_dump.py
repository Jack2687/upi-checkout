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
    def __init__(self, payload="{}", status=200, headers=None, elapsed=0.0, history=None,
                 cookies=None):
        import datetime
        self.text = payload
        self.content = payload.encode()
        self.status_code = status
        self.headers = headers or {"content-type": "application/json"}
        self.elapsed = datetime.timedelta(seconds=elapsed)
        self.history = history or []
        self.cookies = cookies


class SimpleCookieLike:
    """Giống cookie jar của requests: có name/value + thuộc tính."""

    class _Cookie:
        name = "cf_clearance"
        value = "tok"
        path = "/"
        domain = ".chatgpt.com"
        secure = True

    def items(self):
        return [("cf_clearance", "tok")]

    def __iter__(self):
        return iter([self._Cookie()])


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

    def test_body_is_kept_whole_by_default(self):
        body = "x" * 500_000
        entry = td.build_entry(method="POST", url="https://x/y", request_body=body, response_body=body)
        self.assertEqual(len(entry["request"]["postData"]["text"]), 500_000)
        self.assertEqual(len(entry["response"]["content"]["text"]), 500_000)

    def test_cookies_are_structured(self):
        entry = td.build_entry(
            method="GET", url="https://chatgpt.com/x",
            request_headers={"Cookie": "oai-did=abc; __cf_bm=xyz"},
            response_headers={"content-type": "text/html"},
        )
        self.assertEqual(entry["request"]["cookies"],
                         [{"name": "oai-did", "value": "abc"}, {"name": "__cf_bm", "value": "xyz"}])
        jar = SimpleCookieLike()
        entry2 = td.build_entry(method="GET", url="https://x/y", response_cookie_jar=jar)
        self.assertEqual(entry2["response"]["cookies"][0]["name"], "cf_clearance")
        self.assertEqual(entry2["response"]["cookies"][0]["secure"], True)
        self.assertEqual(entry2["response"]["cookies"][0]["path"], "/")

    def test_redirect_chain_is_kept(self):
        entry = td.build_entry(
            method="GET", url="https://x/final", redirects=[
                {"status": 302, "url": "https://x/a", "location": "https://x/b"},
                {"status": 301, "url": "https://x/b", "location": "https://x/final"},
            ])
        self.assertEqual(entry["redirectCount"], 2)
        self.assertEqual(entry["redirects"][0]["status"], 302)
        self.assertEqual(entry["redirects"][1]["location"], "https://x/final")

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


class TextDumpFolderTests(unittest.TestCase):
    """Dump tay (.txt) phai nam trong thu muc cua task, khong rai ra dumps/ goc."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="upi-txt-"))
        import extract_cs as ex
        import shutil
        self.ex = ex
        self.keep = ex._dump_counter
        ex._log_file = self.tmp / "flow.log"

    def test_written_into_task_folder(self):
        ex = self.ex
        with ex.execution_context({"UPI_DUMP": "1", "UPI_DUMP_DIR": str(self.tmp),
                                   "UPI_TASK_LABEL": "t4"}):
            ex.dump_http(None, "checkout", {"a": 1}, "POST", "https://chatgpt.com/x")
        folder = self.tmp / "t4"
        self.assertTrue(folder.is_dir())
        files = list(folder.glob("*_checkout.txt"))
        self.assertEqual(len(files), 1)
        self.assertIn("stage: checkout", files[0].read_text())
        # khong co file nao roi ra ngoai
        self.assertEqual(list(self.tmp.glob("*.txt")), [])

    def test_falls_back_to_manual_folder_without_label(self):
        ex = self.ex
        with ex.execution_context({"UPI_DUMP": "1", "UPI_DUMP_DIR": str(self.tmp)}):
            ex.dump_http(None, "checkout", {}, "POST", "https://chatgpt.com/x")
        self.assertTrue((self.tmp / "manual").is_dir())


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

    def test_cookie_jar_elapsed_and_redirects_are_recorded(self):
        ex = self.ex

        class Hop:
            status_code = 302
            url = "https://api.stripe.com/old"
            headers = {"Location": "https://api.stripe.com/new"}

        def fake_original(session, *args, **kwargs):
            return FakeResp('{"id": "pm_1"}', 200, {"content-type": "application/json"},
                            elapsed=0.25, history=[Hop()])

        wrapped = ex._counting_request(fake_original)

        class Jar:                                        # giống cookie jar của requests
            def items(self):
                return [("oai-did", "abc")]

        class Session:
            headers = {"User-Agent": "Chrome/146"}
            cookies = Jar()

        with ex.execution_context({"UPI_DUMP": "1", "UPI_DUMP_DIR": str(self.tmp),
                                   "UPI_TASK_LABEL": "t5"}):
            wrapped(Session(), "POST", "https://api.stripe.com/v1/payment_methods", data={})

        entry = json.loads((self.tmp / "t5" / "t5.jsonl").read_text().splitlines()[0])
        self.assertEqual(entry["request"]["cookies"],
                         [{"name": "oai-did", "value": "abc", "path": "/"}])
        self.assertEqual(entry["serverMs"], 250.0)
        self.assertEqual(entry["redirectCount"], 1)
        self.assertEqual(entry["redirects"][0]["location"], "https://api.stripe.com/new")

    def test_raw_body_env_keeps_tokens(self):
        ex = self.ex

        def fake_original(session, *args, **kwargs):
            return FakeResp('{"token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9"}')

        wrapped = ex._counting_request(fake_original)
        with ex.execution_context({"UPI_DUMP": "1", "UPI_DUMP_DIR": str(self.tmp),
                                   "UPI_TASK_LABEL": "t6", "UPI_DUMP_RAW_BODY": "1"}):
            wrapped(object(), "POST", "https://chatgpt.com/x", data="token=eyJhbGciOiJSUzI1NiJ9")
        entry = json.loads((self.tmp / "t6" / "t6.jsonl").read_text().splitlines()[0])
        self.assertIn("eyJhbGciOiJSUzI1NiJ9", entry["request"]["postData"]["text"])
        self.assertIn("eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9", entry["response"]["content"]["text"])

    def test_body_tokens_are_masked_by_default(self):
        ex = self.ex

        def fake_original(session, *args, **kwargs):
            return FakeResp('{"token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9"}')

        wrapped = ex._counting_request(fake_original)
        with ex.execution_context({"UPI_DUMP": "1", "UPI_DUMP_DIR": str(self.tmp),
                                   "UPI_TASK_LABEL": "t6b"}):
            wrapped(object(), "POST", "https://chatgpt.com/x", data="token=eyJhbGciOiJSUzI1NiJ9")
        entry = json.loads((self.tmp / "t6b" / "t6b.jsonl").read_text().splitlines()[0])
        self.assertNotIn("eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9", entry["response"]["content"]["text"])

    def test_proxied_flag_marks_direct_calls(self):
        """Cờ `proxied` để soi request nào đi thẳng (lộ IP thật) — vd geobilling trước đây."""
        ex = self.ex
        wrapped = ex._counting_request(lambda session, *a, **k: FakeResp())

        class NoProxy:
            headers = {}

        class WithProxy:
            headers = {}
            proxies = {"http": "http://user:pass@1.2.3.4:8080",
                       "https": "http://user:pass@1.2.3.4:8080"}

        with ex.execution_context({"UPI_DUMP": "1", "UPI_DUMP_DIR": str(self.tmp),
                                   "UPI_TASK_LABEL": "t7b"}):
            wrapped(NoProxy(), "GET", "https://nominatim.openstreetmap.org/reverse")
            wrapped(WithProxy(), "GET", "https://chatgpt.com/api/auth/csrf")
        entries = [json.loads(l) for l in (self.tmp / "t7b" / "t7b.jsonl").read_text().splitlines()]
        self.assertIs(entries[0]["proxied"], False)
        self.assertIs(entries[1]["proxied"], True)

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


class GeoProxyTests(unittest.TestCase):
    """geobilling KHÔNG được gọi thẳng ra ngoài (đã từng lộ IP thật + bị ban)."""

    def test_every_geo_session_uses_the_task_proxy(self):
        import geobilling
        calls = []

        class FakeSession:
            headers = {}
            proxies: dict = {}

            def close(self):
                pass

        def fake_make_session(proxy="", **kwargs):
            calls.append(proxy)
            return FakeSession()

        with patch.object(geobilling, "make_session", fake_make_session), \
             patch.object(geobilling, "_get_json", lambda *a, **k: {}), \
             patch.object(geobilling, "_nominatim_wait", lambda: None):
            geobilling._STORE["reverse"] = {}
            geobilling._reverse_geocode(12.97, 77.59, timeout=5, proxy="http://px:1")
            geobilling._STORE["pins"] = {}
            geobilling._pin_record("560001", timeout=5, proxy="http://px:2")
        self.assertEqual(calls, ["http://px:1", "http://px:2"])
        self.assertNotIn("", calls)

    def test_bigdatacloud_client_endpoint_is_gone(self):
        import geobilling
        import inspect
        src = inspect.getsource(geobilling._reverse_bigdatacloud)
        self.assertIn("UPI_BIGDATACLOUD_KEY", src)              # chỉ dùng khi có key
        self.assertIn('/data/reverse-geocode"', src)            # endpoint server-side
        for line in src.splitlines():
            if line.strip().startswith(("url =", "\"https://api.bigdatacloud")):
                self.assertNotIn("reverse-geocode-client", line)


if __name__ == "__main__":
    unittest.main()
