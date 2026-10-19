#!/usr/bin/env python3
"""Khôi phục kết quả task từ **flow log** khi `results.json` không ghi được (đĩa đầy/disk full).

Vì sao cần: 19/10 đĩa nghẽn (`cs_state` 360k file, `logs/` 3,2 GB) làm desk ghi results thất bại
suốt 35 phút mà không báo gì; process dừng thì mất kết quả của 1.345 task. Nhưng flow log vẫn ghi
đủ từng task (`RESULT ok=… status=… err=…`) — script này dựng lại snapshot từ đó.

Dùng:
  python3 recover_results.py logs/web_<job>.log            # chỉ xem sẽ sửa gì (dry-run)
  python3 recover_results.py logs/web_<job>.log --apply    # ghi lại snapshot + headline

Nguyên tắc:
* Task có `RESULT ok=True status=LINK` thì **thắng** mọi dòng STOPPED/FAIL sau đó (link đã giao).
* Lấy cả `ARTIFACT link=… qr=… amount=…` và trạng thái dò link (`link: waiting -> succeeded`,
  `plus: DA LEN PLUS`) từ log để card hiện đúng.
* Task không có RESULT -> `stopped` với lý do server restart.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

RESULT_RE = re.compile(r"\[(t\d+)\] RESULT ok=(True|False) status=(\w+) (\d+)ms(?: err=(.*))?$")
ARTIFACT_RE = re.compile(r"\[(t\d+)\]\s+ARTIFACT link=(\S+)(?: qr=(\S+))?(?: amount=(\S+))?")
LINK_STATE_RE = re.compile(r"\[(t\d+)\] link: (\w*) -> (\w+)")
PLUS_RE = re.compile(r"\[(t\d+)\] plus: (DA LEN PLUS|chua len Plus)")

STATUS_MAP = {"LINK": "done", "FAIL": "fail", "TIMEOUT": "fail", "STOPPED": "stopped",
              "ERROR": "fail"}


def parse_log(path: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    results: dict[str, dict] = {}
    artifacts: dict[str, dict] = {}
    for line in path.read_text(errors="replace").splitlines():
        note = LINK_STATE_RE.search(line)
        if note:
            task, _old, new = note.groups()
            entry = results.setdefault(task, {})
            entry["link_state"] = new
            continue
        plus = PLUS_RE.search(line)
        if plus:
            task, state = plus.groups()
            results.setdefault(task, {})["plus_state"] = "plus" if state.startswith("DA") else ""
            continue
        art = ARTIFACT_RE.search(line)
        if art:
            task, link, qr, amount = art.groups()
            artifacts[task] = {"upi_link": link, "qr": qr or "", "amount": amount or ""}
            continue
        hit = RESULT_RE.search(line)
        if not hit:
            continue
        task, ok, status, ms, err = hit.groups()
        entry = results.setdefault(task, {})
        # Một task đã RA LINK thì không bị dòng STOPPED/FAIL sau đó (lượt retry bị kill) đè.
        if entry.get("status") == "LINK" and status != "LINK":
            continue
        entry.update({"status": status, "ok": ok == "True", "duration_ms": int(ms),
                      "error": (err or "").strip()})
    return results, artifacts


def main() -> int:
    parser = argparse.ArgumentParser(description="Khôi phục results.json từ flow log")
    parser.add_argument("log", help="đường dẫn logs/web_<job>.log")
    parser.add_argument("--results", help="file results (mặc định suy ra từ tên log)")
    parser.add_argument("--apply", action="store_true", help="ghi đè snapshot (mặc định dry-run)")
    args = parser.parse_args()

    log_path = Path(args.log)
    job_id = log_path.stem.replace("web_", "")
    results_path = Path(args.results) if args.results else log_path.with_name(
        f"web_{job_id}_results.json")
    if not results_path.is_file():
        print(f"không thấy {results_path}", file=sys.stderr)
        return 1

    results, artifacts = parse_log(log_path)
    data = json.loads(results_path.read_text())
    tasks = data.get("tasks") or []
    changed = 0
    for task in tasks:
        tid = str(task.get("task_id"))
        kinds = {"link": 0, "status": 0, "artifact": 0, "link_state": 0, "plus": 0}
        hit = results.get(tid) or {}
        if hit.get("status"):
            want = STATUS_MAP.get(hit["status"], "fail")
            if task.get("status") != want:
                task["status"] = want
                kinds["status"] = 1
            task["raw_status"] = "PASS" if hit["status"] == "LINK" else hit["status"]
            task["duration_ms"] = hit.get("duration_ms", task.get("duration_ms"))
            task["error"] = hit.get("error") or None
            task["done_at"] = task.get("done_at") or int(time.time())
            if hit["status"] == "LINK":
                kinds["link"] = 1
        if tid in artifacts:
            art = dict(task.get("artifact") or {})
            art.update({k: v for k, v in artifacts[tid].items() if v})
            task["artifact"] = art
            kinds["artifact"] = 1
        if hit.get("link_state"):
            task["link_state"] = hit["link_state"]
            kinds["link_state"] = 1
        if "plus_state" in hit:
            task["plus_state"] = hit["plus_state"]
            kinds["plus"] = 1
        if not hit and task.get("status") in ("running", "pending"):
            task["status"] = "stopped"
            task["raw_status"] = "STOPPED"
            task["error"] = "server restarted — kết quả không kịp ghi ra đĩa"
            kinds["status"] = 1
        changed += sum(kinds.values())

    ok_n = sum(1 for t in tasks if t.get("status") == "done")
    fail_n = sum(1 for t in tasks if t.get("status") == "fail")
    stop_n = sum(1 for t in tasks if t.get("status") == "stopped")
    links = [t for t in tasks if (t.get("artifact") or {}).get("upi_link")]
    data.update({"done": ok_n + fail_n + stop_n, "ok": ok_n, "fail": fail_n,
                 "running": 0, "queued": 0})
    print(f"log: {log_path}  | task có RESULT: {len(results)}")
    print(f"sửa {changed} field · sau khôi phục: ok={ok_n} fail={fail_n} stopped={stop_n}"
          f" / total={len(tasks)} · link={len(links)}")
    for t in links[:10]:
        print(f"  LINK {t['task_id']} {t.get('email')} → {(t['artifact'].get('upi_link') or '')[:70]}")
    if not args.apply:
        print("(dry-run — thêm --apply để ghi)")
        return 0

    if data.get("status") == "running":
        data["status"] = "stopped"
        data["stop_reason"] = ("server restarted while results could not be written "
                               "(recovered from flow log)")
    temp = results_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temp.replace(results_path)
    headline = results_path.with_name(f"web_{job_id}_headline.json")
    headline.write_text(json.dumps({"job_id": job_id, "status": data.get("status"),
                                    "total": len(tasks), "done": data["done"],
                                    "ok": ok_n, "fail": fail_n,
                                    "started_at": data.get("started_at")}, ensure_ascii=False),
                        encoding="utf-8")
    print(f"đã ghi {results_path.name} + {headline.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
