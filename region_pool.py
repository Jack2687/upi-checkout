#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""region_pool.py — sinh pool proxy ĐÚNG VÙNG: đổi sid -> dò IP exit -> giữ cái khớp.

Vì sao KHÔNG ghim vùng bằng tham số `-st-<bang>` (đã thử rồi bỏ):
    Đo thực tế: cùng một `sid`, đổi `-st-` sang 4 bang khác nhau (Bihar / Kerala /
    Tamil Nadu / không ghim) thì provider luôn trả về CÙNG một IP exit
    (157.34.94.58 · Ashta, Madhya Pradesh). Tham số bang chỉ được xét lúc TẠO session
    mới, mà session thì provider gắn chặt với IP -> ghim bằng tham số gần như vô hiệu.

Cách ở đây chắc chắn hơn:
    1. Lấy 1 dòng proxy làm mẫu, CHỈ đổi `sid-` -> proxy mới (đúng như panel làm khi
       bấm Generate).
    2. Dò vị trí IP exit thật của proxy đó qua API (ipwho.is).
    3. Khớp đúng bang cần thì giữ, không thì bỏ và sinh tiếp.
    -> Pool cuối toàn proxy đã kiểm chứng nằm đúng bang mong muốn.

Dùng:
    python3 region_pool.py --from-file proxy.txt --total 500 --out proxy_region.txt
    python3 region_pool.py --template "<1 dòng proxy>" --states "Bihar=45,Kerala=26"
    python3 region_pool.py --from-file proxy.txt --check --workers 3
"""
from __future__ import annotations

import argparse
import json
import random
import re
import string
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

# Tỉ lệ theo bảng người dùng đưa (phần bố của 243 mã ₹0 đã định vị được IP exit).
DEFAULT_SHARES: dict[str, float] = {
    "Uttar Pradesh": 0.156, "Maharashtra": 0.091, "Bihar": 0.091, "Rajasthan": 0.074,
    "Gujarat": 0.070, "Delhi": 0.066, "Haryana": 0.066, "Punjab": 0.058, "Kerala": 0.053,
    "Jammu and Kashmir": 0.045, "Tamil Nadu": 0.041, "Telangana": 0.033, "Assam": 0.033,
    "Madhya Pradesh": 0.029, "Karnataka": 0.025,
    "Andhra Pradesh": 0.010, "Uttarakhand": 0.010, "Chandigarh": 0.010,
    "Jharkhand": 0.010, "Odisha": 0.010, "West Bengal": 0.010,
    "Mizoram": 0.010, "Goa": 0.010,
}

GEO_URL = "https://ipwho.is/"
_SID_RE = re.compile(r"(?i)\b(sid|session|sessid|sessionid)[-_]([A-Za-z0-9]{4,})")


def split_proxy(line: str) -> tuple[str, str, str, str]:
    """Tách `host:port:user:pass` hoặc `scheme://user:pass@host:port`."""
    raw = str(line or "").strip()
    if not raw or raw.startswith("#"):
        raise ValueError("empty")
    if "://" in raw:
        _scheme, _, rest = raw.partition("://")
        creds, _, hostport = rest.rpartition("@")
        user, _, pw = creds.partition(":")
        host, _, port = hostport.partition(":")
        return host, port, urllib.parse.unquote(user), urllib.parse.unquote(pw)
    parts = raw.split(":")
    if len(parts) != 4:
        raise ValueError("cần host:port:user:pass")
    return parts[0], parts[1], parts[2], parts[3]


def build_proxy(host: str, port: str, user: str, pw: str, colon: bool) -> str:
    if colon:
        return f"{host}:{port}:{user}:{pw}"
    q = urllib.parse.quote
    return f"http://{q(user, safe='-_.~')}:{q(pw, safe='-_.~')}@{host}:{port}"


def _new_id(like: str, rng: random.Random) -> str:
    """Sinh id mới CÙNG DẠNG với id cũ (giữ kiểu ký tự + độ dài)."""
    if like.isdigit():
        return "".join(rng.choice(string.digits) for _ in range(len(like)))
    if like.isalpha() and like.isupper():
        return "".join(rng.choice(string.ascii_uppercase) for _ in range(len(like)))
    return "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(len(like)))



_STATE_PARAM_RE = re.compile(r"(?i)-st-[^-]+(?: [A-Za-z]+)?")
_REGION_PAIR_RE = re.compile(r"(?i)(-region-[A-Za-z]{2})(?=-|$)")


def set_state_pin(line: str, state: str) -> str:
    """Ghim `-st-<bang>` vào username (bỏ tham số bang cũ nếu có).

    Vì sao vẫn cần ghim dù đã dò kiểm chứng: đo thực tế trên pool KHÔNG ghim, tỉ lệ
    ra đúng 1 bang chỉ ~4% (3/72) vì IP gần như ngẫu nhiên khắp Ấn Độ; còn pool CÓ
    ghim thì đo được ~50-62%. Ghim trước rồi dò sau -> đỡ phải thử nhiều lần.
    """
    try:
        host, port, user, pw = split_proxy(line)
    except ValueError:
        return line
    user = _STATE_PARAM_RE.sub("", user)          # bỏ pin cũ
    if not _REGION_PAIR_RE.search(user):
        return line
    user = _REGION_PAIR_RE.sub(lambda m: "%s-st-%s" % (m.group(1), state), user, count=1)
    return build_proxy(host, port, user, pw, "://" not in line)


def reroll_sid(line: str, rng: random.Random | None = None, colon: bool = True) -> str | None:
    """Đổi `sid-<cũ>` thành sid mới -> proxy mới. None nếu mẫu không có sid."""
    rng = rng or random.Random()
    try:
        host, port, user, pw = split_proxy(line)
    except ValueError:
        return None
    hit = _SID_RE.search(user)
    if not hit:
        return None
    new_user = user[:hit.start(2)] + _new_id(hit.group(2), rng) + user[hit.end(2):]
    return build_proxy(host, port, new_user, pw, colon)


def probe(line: str, timeout: float = 25.0) -> dict:
    """Dò vị trí IP exit của 1 proxy. {'ok': False, 'error': ...} nếu không gọi được."""
    try:
        host, port, user, pw = split_proxy(line)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    q = urllib.parse.quote
    url = f"http://{q(user, safe='-_.~')}:{q(pw, safe='-_.~')}@{host}:{port}"
    try:
        resp = requests.get(GEO_URL, proxies={"http": url, "https": url},
                            timeout=timeout, headers={"accept": "application/json"})
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:110]}
    if not isinstance(data, dict) or not data.get("success"):
        return {"ok": False, "error": "geo api: bad payload"}
    return {"ok": True, "ip": data.get("ip"), "country": data.get("country_code"),
            "region": data.get("region") or "", "city": data.get("city") or ""}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z]", "", str(text or "").lower())


def matches(geo: dict, state: str) -> bool:
    return bool(geo.get("ok")) and geo.get("country") == "IN" and _norm(geo.get("region")) == _norm(state)


def build_pool(template: str, quotas: dict, *, workers: int = 3, timeout: float = 25.0,
               budget_factor: int = 8, log=print):
    """Sinh proxy đúng vùng cho từng bang trong `quotas`."""
    colon = "://" not in template
    rng = random.Random()
    keep: list = []
    stats: dict = {}
    t0 = time.time()

    for state, want in quotas.items():
        kept: list = []
        tried = 0
        budget = max(want * budget_factor, want + 4)
        tpl = set_state_pin(template, state)      # ghim bang -> tang ti le khớp
        while len(kept) < want and tried < budget:
            batch = []
            for _ in range(min(workers * 2, budget - tried)):
                cand = reroll_sid(tpl, rng, colon)
                if cand:
                    batch.append(cand)
            tried += len(batch)
            if not batch:
                break
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for cand, geo in zip(batch, ex.map(lambda c: probe(c, timeout), batch)):
                    if len(kept) < want and matches(geo, state):
                        kept.append(cand)
        keep.extend(kept)
        rate = (len(kept) / tried * 100) if tried else 0
        stats[state] = {"got": len(kept), "want": want, "tried": tried, "hit_rate": round(rate, 1)}
        log(f"  {state:20s} {len(kept):4d}/{want:<4d}  thử {tried:4d}  khớp {rate:4.1f}%")

    stats["_total"] = {"kept": len(keep), "seconds": round(time.time() - t0, 1)}
    return keep, stats


def quotas_from(total: int) -> dict:
    """Chia `total` theo tỉ lệ DEFAULT_SHARES (làm tròn rồi bù cho đủ)."""
    alloc = {s: int(round(total * w)) for s, w in DEFAULT_SHARES.items()}
    diff = total - sum(alloc.values())
    order = sorted(alloc, key=lambda s: -DEFAULT_SHARES[s])
    i = 0
    while diff != 0 and order:
        s = order[i % len(order)]
        alloc[s] += 1 if diff > 0 else -1
        diff += -1 if diff > 0 else 1
        i += 1
    return {s: n for s, n in alloc.items() if n > 0}


def first_line(path) -> str:
    for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    raise ValueError(f"{path} không có dòng proxy")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sinh pool proxy đúng vùng (đổi sid + dò IP exit)")
    ap.add_argument("--template", default="", help="1 dòng proxy mẫu")
    ap.add_argument("--from-file", default="", help="lấy dòng đầu file làm mẫu")
    ap.add_argument("--total", type=int, default=0, help="tổng proxy cần, chia theo tỉ lệ bảng")
    ap.add_argument("--states", default="", help='tự chọn: "Bihar=45,Kerala=26"')
    ap.add_argument("--check", action="store_true", help="chỉ dò pool có sẵn, không sinh")
    ap.add_argument("--workers", type=int, default=3, help="kết nối song song (3-4 an toàn)")
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--budget-factor", type=int, default=8,
                    help="sinh dư tối đa mấy lần chỉ tiêu mỗi bang")
    ap.add_argument("--out", default="", help="ghi pool đã kiểm chứng ra file")
    args = ap.parse_args(argv)

    template = args.template or (first_line(args.from_file) if args.from_file else "")
    if not template:
        print(json.dumps({"error": "cần --template hoặc --from-file"}, ensure_ascii=False))
        return 2

    if args.check:
        lines = [l.strip() for l in Path(args.from_file).read_text(encoding="utf-8", errors="ignore").splitlines()
                 if l.strip() and not l.startswith("#")]
        print(f"dò {len(lines)} proxy (workers={args.workers})...", flush=True)
        counts: dict = {}
        okc = errc = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for geo in ex.map(lambda l: probe(l, args.timeout), lines):
                if not geo.get("ok"):
                    errc += 1
                    continue
                okc += 1
                key = f"{geo.get('region')} ({geo.get('country')})"
                counts[key] = counts.get(key, 0) + 1
        print(json.dumps({"ok": okc, "error": errc,
                          "by_region": dict(sorted(counts.items(), key=lambda x: -x[1])[:15])},
                         ensure_ascii=False, indent=1))
        return 0

    if args.states:
        quotas: dict = {}
        for part in args.states.split(","):
            if "=" not in part:
                continue
            st, n = part.split("=", 1)
            quotas[st.strip()] = int(n.strip())
    else:
        quotas = quotas_from(args.total or 100)

    print(f"mẫu: {re.sub(r':[^:@/]+@', ':***@', template)[:90]}")
    print(f"mục tiêu: {sum(quotas.values())} proxy đúng vùng ở {len(quotas)} bang")
    keep, stats = build_pool(template, quotas, workers=args.workers, timeout=args.timeout, log=print)
    if args.out:
        Path(args.out).write_text("\n".join(keep) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0 if keep else 1


if __name__ == "__main__":
    sys.exit(main())
