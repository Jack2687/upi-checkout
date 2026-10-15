#!/usr/bin/env bash
# clean_logs.sh — dọn log/state cũ để logs/ không phình vô hạn.
#
# MẶC ĐỊNH LÀ DRY-RUN: chỉ in ra sẽ xoá gì. Thêm --apply mới xoá thật.
#
#   ./clean_logs.sh                 # xem trước, giữ 3 ngày
#   ./clean_logs.sh --days 7        # xem trước, giữ 7 ngày
#   ./clean_logs.sh --apply         # xoá thật (giữ 3 ngày)
#   ./clean_logs.sh --apply --results-days 10   # xoá cả results.json cũ hơn 10 ngày
#
# KHÔNG BAO GIỜ xoá `logs/web_*_results.json` trừ khi bạn tự đặt --results-days > 0:
# chúng là nguồn của bảng thống kê mã ₹0 và của mọi phân tích lịch sử.
# Đang có job chạy thì script từ chối (dùng --force nếu bạn thật sự muốn).
set -uo pipefail
cd "$(dirname "$0")" || exit 1

DAYS=3
RESULTS_DAYS=0
APPLY=0
FORCE=0
PORT="${UPI_PORT:-8099}"

while [ $# -gt 0 ]; do
  case "$1" in
    --days) DAYS="$2"; shift 2 ;;
    --results-days) RESULTS_DAYS="$2"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "tham so la: $1"; exit 2 ;;
  esac
done

case "$DAYS" in ''|*[!0-9]*) echo "--days phải là số nguyên >= 0 (nhận: $DAYS)"; exit 2 ;; esac
case "$RESULTS_DAYS" in ''|*[!0-9]*) echo "--results-days phải là số nguyên >= 0"; exit 2 ;; esac

# 1) Đừng dọn khi đang chạy job: file state/seed của task đang chạy mà bị xoá là hỏng task.
if [ "$FORCE" -eq 0 ]; then
  running=$(curl -s --max-time 5 "http://127.0.0.1:$PORT/api/jobs" 2>/dev/null | python3 -c "
import json,sys
try:
    jobs=json.load(sys.stdin).get('jobs',[])
except Exception:
    print(''); raise SystemExit
print(' '.join(j['job_id'] for j in jobs if j.get('status')=='running'))
" 2>/dev/null)
  if [ -n "${running:-}" ]; then
    echo "ĐANG CÓ JOB CHẠY: $running"
    echo "-> không dọn (dùng --force nếu chắc chắn)."
    exit 1
  fi
fi

human() { awk -v b="$1" 'BEGIN{printf (b>=1073741824)? "%.2f GB" : (b>=1048576)? "%.1f MB" : "%.0f KB", (b>=1073741824)? b/1073741824 : (b>=1048576)? b/1048576 : b/1024}'; }
TOTAL=0

report_or_delete() { # $1 = mo ta, con lai la danh sach file
  local label="$1"; shift
  local n=0 bytes=0 f
  for f in "$@"; do
    [ -e "$f" ] || continue
    local sz
    sz=$(du -sb "$f" 2>/dev/null | cut -f1)
    n=$((n+1)); bytes=$((bytes+${sz:-0}))
    [ "$APPLY" -eq 1 ] && rm -rf -- "$f"
  done
  TOTAL=$((TOTAL+bytes))
  printf "  %-42s %4d muc  %s%s\n" "$label" "$n" "$(human "$bytes")" \
    "$([ "$APPLY" -eq 1 ] && echo '  (đã xoá)' || echo '')"
}

echo "== dọn log (giữ ${DAYS} ngày$( [ "$RESULTS_DAYS" -gt 0 ] && echo ", results giữ ${RESULTS_DAYS} ngày")) | $([ "$APPLY" -eq 1 ] && echo 'XOÁ THẬT' || echo 'DRY-RUN') =="

# 2) log flow + log job cũ. `! -newermt` = cũ hơn mốc.
mapfile -d '' OLD_FLOW < <(find logs -name 'upi_2026*.log' ! -newermt "-${DAYS} days" -print0 2>/dev/null)
report_or_delete "logs/upi_*.log cũ hơn ${DAYS} ngày" "${OLD_FLOW[@]:-}"

mapfile -d '' OLD_WEB < <(find logs -name 'web_*.log' ! -newermt "-${DAYS} days" -print0 2>/dev/null)
report_or_delete "logs/web_*.log cũ hơn ${DAYS} ngày" "${OLD_WEB[@]:-}"

# 3) results.json — chỉ khi người dùng chủ động đặt --results-days
if [ "$RESULTS_DAYS" -gt 0 ]; then
  mapfile -d '' OLD_RES < <(find logs -name 'web_*_results.json' ! -newermt "-${RESULTS_DAYS} days" -print0 2>/dev/null)
  report_or_delete "logs/web_*_results.json cũ hơn ${RESULTS_DAYS} ngày" "${OLD_RES[@]:-}"
fi

# 4) cs_state: seed rác của task đã xong + state/proxy file cũ.
#    Giữ nguyên cache tra cứu (address ledger, pincode store, danh sách seed theo bang).
mapfile -d '' SEEDS < <(find cs_state -maxdepth 1 -name 'web_seed_*' ! -newermt "-${DAYS} days" -print0 2>/dev/null)
report_or_delete "cs_state/web_seed_* (rác của task đã xong)" "${SEEDS[@]:-}"

mapfile -d '' PROXYF < <(find cs_state -maxdepth 1 -name 'web_proxies_*.txt' ! -newermt "-${DAYS} days" -print0 2>/dev/null)
report_or_delete "cs_state/web_proxies_*.txt cũ" "${PROXYF[@]:-}"

mapfile -d '' JOBDIRS < <(find cs_state -maxdepth 1 -type d ! -newermt "-${DAYS} days" ! -name cs_state -print0 2>/dev/null)
report_or_delete "cs_state/<job>/ (session của job cũ)" "${JOBDIRS[@]:-}"

# 5) stdout của desk: cắt khi lớn hơn 5 MB (systemd ghi tiếp từ đầu file).
if [ -f logs/desk_out.log ]; then
  sz=$(stat -c%s logs/desk_out.log)
  if [ "$sz" -gt 5242880 ]; then
    [ "$APPLY" -eq 1 ] && : > logs/desk_out.log
    printf "  %-42s %s%s\n" "logs/desk_out.log (>5 MB)" "$(human "$sz")" \
      "$([ "$APPLY" -eq 1 ] && echo '  (đã cắt)' || echo '')"
  fi
fi

echo "== tổng $([ "$APPLY" -eq 1 ] && echo 'đã xoá' || echo 'sẽ xoá'): $(human "$TOTAL") =="
[ "$APPLY" -eq 1 ] || echo "(chạy lại với --apply để xoá thật)"
du -sh logs cs_state 2>/dev/null
