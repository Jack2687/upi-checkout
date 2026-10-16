#!/usr/bin/env bash
# Restart desk AN TOÀN — từ chối khi có job đang chạy.
#
# Vì sao có file này: 2026-10-11 20:23 mình restart service trong lúc job
# ea4d2a7551e4 đang chạy -> 77 acc đang xử lý bị giết giữa chừng và mất luôn.
# Job + token chỉ sống trong RAM của process; restart là mất sạch, không lấy lại được.
#
# Dùng:  ./restart_desk.sh            # chỉ restart khi không có job chạy
#        ./restart_desk.sh --check    # chỉ kiểm tra, không restart
#        ./restart_desk.sh --force    # restart dù đang có job (tự chịu)
set -u
PORT="${UPI_PORT:-8099}"
MODE="restart"
case "${1:-}" in
  --check) MODE="check" ;;
  --force) MODE="force" ;;
esac

running=0; ids=""
# Timeout dài hơn 5s: khi job lớn đang chạy, /api/jobs có thể chậm (đo thật 16/10: 5.824 task
# -> /api/jobs mất >60s, timeout 5s trả về rỗng). Rỗng ở đây KHÔNG có nghĩa là không có job.
info=$(curl -s --max-time 20 "http://127.0.0.1:$PORT/api/jobs" 2>/dev/null || true)
if [ -n "$info" ]; then
  parsed=$(printf '%s' "$info" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("? "); raise SystemExit
run = [j for j in d.get("jobs", []) if j.get("status") == "running"]
print(len(run), " ".join(j.get("job_id", "?") for j in run))
' 2>/dev/null || echo "? ")
  running="${parsed%% *}"
  ids="${parsed#* }"
elif [ "$(systemctl --user is-active upi-checkout 2>/dev/null)" = "active" ]; then
  # Desk SỐNG mà không đọc được /api/jobs = thường là job lớn đang chạy (endpoint chậm), chứ
  # không phải "không có job". Trước đây ca này bị coi là an toàn -> restart giết batch.
  echo "Đọc /api/jobs không được nhưng service ĐANG chạy -> coi như KHÔNG AN TOÀN."
  if [ "$MODE" = "check" ]; then
    echo "-> KHÔNG nên restart (không xác nhận được là không có job)."
    exit 1
  fi
  if [ "$MODE" != "force" ]; then
    echo "-> Từ chối restart. Thử lại sau, hoặc ép bằng: $0 --force"
    exit 1
  fi
  echo "-> --force: vẫn restart theo yêu cầu."
else
  echo "Desk không chạy và API không trả lời — restart luôn."
fi

if [ "$running" != "0" ] && [ "$running" != "" ] && [ "$running" != "?" ]; then
  echo "CÓ $running job ĐANG CHẠY: $ids"
  if [ "$MODE" = "check" ]; then
    echo "-> KHÔNG nên restart (sẽ giết batch đang chạy)."
    exit 1
  fi
  if [ "$MODE" != "force" ]; then
    echo "-> Từ chối restart. Đợi job xong, hoặc ép bằng: $0 --force"
    exit 1
  fi
  echo "-> --force: vẫn restart theo yêu cầu."
fi

if [ "$MODE" = "check" ]; then
  echo "Không có job nào đang chạy -> an toàn để restart."
  exit 0
fi

systemctl --user restart upi-checkout
sleep 3
echo "desk: $(systemctl --user is-active upi-checkout) | http=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/)"
