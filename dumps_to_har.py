#!/usr/bin/env python3
"""Đổi dump JSONL (hoặc cả thư mục) thành file **.har 1.2** mở được bằng Chrome DevTools/Charles.

Dùng:
  python3 dumps_to_har.py dumps/t12              # 1 task  -> dumps/t12/t12.har
  python3 dumps_to_har.py dumps --merge job.har  # cả thư mục -> 1 file gộp
  python3 dumps_to_har.py dumps --summary        # chỉ in thống kê, không ghi file

Mỗi dòng trong `.jsonl` là 1 HAR entry (xem `traffic_dump.py`), gồm cả entry của
`sentinel_runner.js` (process Node) — nên file gộp là **toàn bộ API** của một task/job.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import traffic_dump  # noqa: E402


def jsonl_files(targets: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in targets:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.jsonl")))
        elif path.suffix == ".har":
            continue
        elif path.exists():
            files.append(path)
        else:
            print(f"bỏ qua (không tồn tại): {path}", file=sys.stderr)
    return files


def summarise(entries: list[dict]) -> str:
    hosts = Counter(str(e.get("host") or "?") for e in entries)
    status = Counter(str((e.get("response") or {}).get("status") or 0) for e in entries)
    errors = [e for e in entries if e.get("error")]
    lines = [f"entries: {len(entries)}", "", "theo host:"]
    lines += [f"  {n:5d}  {h}" for h, n in hosts.most_common()]
    lines += ["", "theo status:"]
    lines += [f"  {n:5d}  {s}" for s, n in status.most_common()]
    if errors:
        lines += ["", f"lỗi ({len(errors)}):"]
        lines += [f"  {str(e.get('error'))[:120]}" for e in errors[:10]]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="JSONL dump -> HAR 1.2")
    parser.add_argument("targets", nargs="+", help="file .jsonl hoặc thư mục dump")
    parser.add_argument("--merge", metavar="OUT.har", help="gộp tất cả vào 1 file HAR")
    parser.add_argument("--summary", action="store_true", help="chỉ in thống kê")
    args = parser.parse_args()

    files = jsonl_files(args.targets)
    if not files:
        print("không tìm thấy file .jsonl nào", file=sys.stderr)
        return 1

    total = 0
    all_entries: list[dict] = []
    for path in files:
        entries = traffic_dump.read_jsonl(path)
        total += len(entries)
        if args.summary:
            all_entries.extend(entries)
            continue
        out = path.with_suffix(".har")
        out.write_text(json.dumps(traffic_dump.to_har(entries), ensure_ascii=False, indent=1),
                       encoding="utf-8")
        print(f"{path} -> {out}  ({len(entries)} entries)")

    if args.summary:
        print(summarise(all_entries))
        return 0
    if args.merge:
        out = Path(args.merge)
        out.write_text(json.dumps(traffic_dump.to_har(all_entries), ensure_ascii=False, indent=1),
                       encoding="utf-8")
        print(f"gộp {len(files)} file ({total} entries) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
