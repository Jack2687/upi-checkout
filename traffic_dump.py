"""Ghi lại TOÀN BỘ traffic HTTP của flow thành **HAR entry** (JSONL), không phải log tay.

Vì sao có file này: `extract_cs.dump_http()` chỉ ghi ở ~24 điểm được gắn tay trong 315 chỗ
gọi HTTP, và **không ghi header** — thiếu đúng phần "giống browser" (UA, `sec-ch-ua`, cookie,
`Origin`/`Referer`, `x-openai-*`, `cf-ray`). Ở đây ghi ở **tầng transport** (`Session.request`,
xem `extract_cs._counting_request`) nên phủ mọi call của luồng Python, kèm cả traffic của
`sentinel_runner.js` (process Node ghi vào cùng file).

Định dạng: mỗi dòng 1 **HAR 1.2 entry** (JSON) -> `dumps/<label>/<label>.jsonl`.
Ghi kiểu JSONL (không phải 1 file .har) để process bị kill giữa chừng vẫn giữ được phần đã ghi;
đổi sang `.har` để mở bằng Chrome DevTools/Charles bằng `dumps_to_har.py`.

Header được ghi **nguyên văn** (không che) — cần đủ để so/replay với browser thật; file nằm
trong `dumps/` đã bị .gitignore. Body đi qua hàm `redact` của caller (che Bearer/token/proxy)
và bị cắt theo `body_limit`.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qsl, urlsplit

_JSON_MIME = re.compile(r"json|javascript|text|xml|html|x-www-form-urlencoded", re.I)
_lock = threading.Lock()


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def header_pairs(headers: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """Header dạng mảng HAR 1.2 `[{name, value}]`, giá trị NGUYÊN VĂN."""
    return [{"name": str(key), "value": _as_text(value)} for key, value in (headers or {}).items()]


def _mime(headers: Mapping[str, Any] | None) -> str:
    for key, value in (headers or {}).items():
        if str(key).lower() == "content-type":
            return str(value).split(";")[0].strip()
    return ""


def build_entry(
    *,
    method: str,
    url: str,
    request_headers: Mapping[str, Any] | None = None,
    request_body: Any = None,
    status: int = 0,
    response_headers: Mapping[str, Any] | None = None,
    response_body: Any = None,
    error: str = "",
    elapsed_ms: float = 0.0,
    started: float | None = None,
    body_limit: int = 200_000,
    redact: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Một entry: ĐỦ cả 2 chiều (request + response) của đúng 1 request."""
    now = datetime.fromtimestamp(started if started else datetime.now().timestamp(),
                                tz=timezone.utc)
    req_text = _as_text(request_body)
    res_text = _as_text(response_body)
    if redact is not None:
        req_text, res_text = redact(req_text), redact(res_text)
    if body_limit > 0:
        req_text, res_text = req_text[:body_limit], res_text[:body_limit]
    parts = urlsplit(url)
    return {
        "startedDateTime": now.isoformat(),
        "time": round(elapsed_ms, 1),
        "request": {
            "method": str(method or "GET").upper(),
            "url": url,
            "httpVersion": "HTTP/2" if parts.scheme == "https" else "HTTP/1.1",
            "cookies": [],
            "queryString": [{"name": k, "value": v} for k, v in parse_qsl(parts.query)],
            "headers": header_pairs(request_headers),
            "headersSize": -1,
            "bodySize": len(req_text),
            "postData": ({"mimeType": _mime(request_headers) or "application/x-www-form-urlencoded",
                          "text": req_text} if req_text else None),
        },
        "response": {
            "status": int(status or 0),
            "statusText": "" if not error else error[:300],
            "httpVersion": "HTTP/2" if parts.scheme == "https" else "HTTP/1.1",
            "cookies": [],
            "headers": header_pairs(response_headers),
            "content": {"size": len(res_text), "mimeType": _mime(response_headers), "text": res_text},
            "redirectURL": str((response_headers or {}).get("Location") or ""),
            "headersSize": -1,
            "bodySize": len(res_text),
        },
        # Hai field bat buoc cua HAR 1.2 (DevTools doc); gia tri 0 vi flow khong do tach tang.
        "cache": {},
        "timings": {"send": 0, "wait": round(elapsed_ms, 1), "receive": 0},
        "host": parts.netloc,
        "path": parts.path,
        "error": error,
    }


def dump_path(root: str | Path, label: str) -> Path:
    """`dumps/<label>/<label>.jsonl` — mỗi task 1 file, tên an toàn cho filesystem."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label or "-")).strip("_") or "-"
    folder = Path(root) / safe
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{safe}.jsonl"


# ---------------------------------------------------------------------------
# "Đích dump" hiện tại: extract_cs đặt khi mỗi execution bắt đầu, để cả process
# con (sentinel_runner.js qua `child_env()`) ghi vào CÙNG file của task đó.
# ---------------------------------------------------------------------------
_target: dict[str, Any] = {"enabled": False, "file": None, "body_limit": 200_000}
_target_lock = threading.Lock()


def set_target(*, enabled: bool, root: str | Path, label: str, body_limit: int = 200_000) -> None:
    path = dump_path(root, label) if enabled else None
    with _target_lock:
        _target.update({"enabled": bool(enabled), "file": str(path) if path else None,
                        "body_limit": int(body_limit)})


def entry_path() -> Path | None:
    with _target_lock:
        return Path(_target["file"]) if _target["file"] else None


def body_limit() -> int:
    with _target_lock:
        return int(_target["body_limit"])


def child_env() -> dict[str, str]:
    """Env truyền cho process con (Node) để nó ghi cùng file dump của task."""
    with _target_lock:
        if not _target["enabled"] or not _target["file"]:
            return {}
        return {"UPI_DUMP": "1", "UPI_DUMP_FILE": str(_target["file"])}


def append_entry(path: str | Path, entry: dict[str, Any]) -> None:
    line = json.dumps(entry, ensure_ascii=False)
    with _lock:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return entries


def to_har(entries: Iterable[dict[str, Any]], *, creator: str = "upi-checkout") -> dict[str, Any]:
    """Gộp các entry JSONL thành 1 file HAR 1.2 hợp lệ (mở được bằng DevTools/Charles)."""
    items = list(entries)
    items.sort(key=lambda e: str(e.get("startedDateTime") or ""))
    hosts = []
    for entry in items:
        host = str(entry.get("host") or "")
        if host and host not in hosts:
            hosts.append(host)
    return {
        "log": {
            "version": "1.2",
            "creator": {"name": creator, "version": "1.0"},
            "pages": [{"startedDateTime": items[0]["startedDateTime"] if items else "",
                       "id": "run", "title": "upi-checkout run",
                       "pageTimings": {"onContentLoad": -1, "onLoad": -1}}],
            "entries": items,
            "_hosts": hosts,
        }
    }
