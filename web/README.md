# web/ — UI trích xuất QR UPI (dán nhiều AT)

Web dashboard cho `upi-checkout`: dán nhiều access token → xem **progress từng bước**
theo thời gian thực, biết mỗi task đang chạy **luồng backend nào** (OAICS hay CS).

## Điều phối song song và giao diện

- Mỗi job dùng **một `ThreadPoolExecutor`** chung cho batch ban đầu, thêm task và
  retry. Task chờ nằm trong queue, không tạo thread chờ riêng cho mỗi lần thêm.
- `workers`: 1–300 **mỗi job**; **không giới hạn** số task mỗi batch (đặt `UPI_MAX_BATCH=<n>` nếu muốn chặn).
  Tăng worker dần theo RAM và kết nối; giới hạn 200 không phải khuyến nghị chạy 200
  tiến trình CS trên mọi máy. Nhiều job cùng lúc cộng dồn tài nguyên.
- Không retry trùng task đang chờ/chạy. Chỉ đóng job khi mọi task đã hoàn tất,
  gồm cả phần thêm vào. Stop chặn task chưa bắt đầu; task đang chạy dùng cơ chế
  hủy sẵn có của backend, có thể cần chờ thao tác mạng đang thực hiện kết thúc.
- State backend tách theo `cs_state/<job_id>/`, tránh hai job ghi đè state của nhau.
- Snapshot lưu bằng atomic replace, checkpoint khi task hoàn tất (tối đa mỗi 2 giây)
  và lưu lần cuối khi job kết thúc. Sau restart, task chưa xong hiển thị **đã dừng**,
  không tự chạy lại thanh toán. Token không có trong snapshot.
- SSE gửi theo batch tối đa 128 event, không chiếm thread riêng cho browser đang
  chờ. Client chậm làm đầy queue sẽ nhận lại snapshot đầy đủ.
- Dashboard tiếng Việt, responsive: chỉ số worker/hàng đợi/thời gian/tốc độ,
  tìm email hoặc task ID, bộ lọc trạng thái, danh sách và lưới kết quả. DOM update
  gộp theo nhịp 32ms; chỉ render task thay đổi, giữ chi tiết mở khi đồng bộ lại.
- Tối đa 6 lần dò trạng thái link đồng thời trên mỗi tab; request trùng được gộp.
  Copy AT chỉ bật cho token xác định thuộc job vừa tạo trong phiên hiện tại;
  lịch sử/reload không suy đoán token từ nội dung ô nhập.

Không thay đổi giao thức thanh toán, điều kiện ưu đãi hay trạng thái thành công.

## Kiểm thử offline (không tạo giao dịch)

Chạy từ thư mục gốc `upi-checkout/`:

```bash
python3 -m unittest discover -s tests -v
node tests/frontend_performance.mjs
python3 tests/preview_server.py --port 8101
```

Preview tại `http://127.0.0.1:8101` dùng HTTP/SSE thật, backend giả lập, log/state
trong thư mục tạm và chặn request thanh toán/proxy. Ctrl+C để đóng preview.
Thêm `--demo-tasks 0` nếu muốn bắt đầu với hàng đợi trống.

Test bao gồm batch 1.000 task/16 worker, append/retry/Stop, lỗi chuẩn bị task,
cô lập state, checkpoint, SSE tràn queue và render 5.000 event trên 1.000 task.
Benchmark frontend là fake DOM offline, **không phải** tốc độ thanh toán thực tế.

```bash
cd upi-checkout/web
python3 app.py                 # http://127.0.0.1:8099
python3 app.py --port 9000     # đổi cổng
```

Mở `http://127.0.0.1:8099`, dán token (mỗi dòng 1 JWT), chọn luồng, bấm chạy.

## Cấu trúc

```
web/
├── app.py               FastAPI: HTTP + SSE
├── engine.py            queue/SSE orchestration, phát event
└── static/
    ├── index.html       layout 2 cột (sidebar + danh sách task)
    ├── app.js           EventSource, cập nhật DOM tại chỗ theo task_id
    └── style.css        giao diện tối than chì, điểm nhấn xanh ngọc, responsive
```

Root backend modules: `backend.py` (shared seam), `cs_backend.py` (in-process CS adapter), `extract_cs.py` (CS protocol), and `cli.py` (OAICS/detection + CLI compatibility).

`engine.py` **không chứa logic protocol** — nó gọi `../backend.py` qua một
request và hooks nhỏ (`step`, `on_log`, `on_geo`, `should_stop`).

Backend CS có 2 chế độ, chọn bằng `UPI_WEB_CS_MODE`:

| giá trị | việc | song song |
|---|---|---|
| `subprocess` (**mặc định**) | mỗi task một tiến trình `extract_cs.py` (`cli.cs_subprocess_one`), cô lập bộ nhớ | theo giới hạn worker của job |
| `inprocess` | chạy qua `cs_backend.run_in_process` và `extract_cs.execution_context` | dùng thread và context riêng; không còn `_RUN_LOCK` trong mã hiện tại |

Giữ nguyên mặc định `subprocess`. Bộ test điều phối không đo hiệu suất của hai
backend với giao dịch thật; mức RAM và throughput còn phụ thuộc luồng và mạng.

## Luồng backend hiển thị

| `flow` | Nhãn UI | Step |
|---|---|---|
| `oaics` | **LUỒNG OAICS** | Warmup → Create checkout → Apply promotion → Proxy egress → Update tax region → Create payment method → Confirm checkout → Confirm intent → Extract QR artifact |
| `cs` | **LUỒNG CS** | 10 bước: Initialize CS flow → Create checkout → Apply promotion → Initialize payment page → Update tax region → Create payment method → Confirm payment → Approve payment → Poll payment artifact → Extract payment artifact |
| `null` | ĐANG DÒ FLOW / — | Warmup → Detect provider |

Chế độ `auto` chạy bước **Detect provider** trước; khi biết kết quả, server gửi
`task_flow` và UI **thay danh sách step** bằng bộ step của luồng thật.
Nếu detect fail (sentinel lỗi / token 401) thì **không gán nhãn luồng nào** —
tránh hiện sai "LUỒNG CS" khi thực tế chưa chạy luồng nào.

## API

| Method | Path | Việc |
|---|---|---|
| POST | `/api/run` | `{tokens, mode, country, promo, workers, retries, proxies}` → `{job_id}`. `retries` mặc định 3 (0..5), điều khiển số lần chạy lại task lỗi và các giới hạn retry nội bộ của CS; CS vẫn chạy tối thiểu một attempt để thực hiện task. |
| GET | `/api/stream/{job_id}` | SSE. Frame đầu là `state` (snapshot đầy đủ), sau đó là event live |
| GET | `/api/state/{job_id}` | snapshot đầy đủ (dùng khi reconnect) |
| GET | `/api/jobs` | danh sách job gần đây |
| POST | `/api/retry/{job_id}/{task_id}` | chạy lại 1 task |
| POST | `/api/append/{job_id}` | thêm token vào queue chung, bỏ token trùng, kiểm tra trần batch |
| POST | `/api/stop/{job_id}` | chặn task chờ và yêu cầu backend hủy task đang chạy |

`tokens` nhận: nhiều dòng JWT, mảng JSON `[{...}]`, hoặc `{accessToken: ...}`.
Tối đa 1.000 token / batch. Run và Append dùng cùng parser token.

### Event SSE

`job_start`, `task_init`, `task_flow`, `step`, `egress`, `log`, `task_artifact`,
`task_done`, `job_done`, `error`, và 3 event nội bộ: `state` (snapshot đầu kết nối),
`task_start` (bắt đầu 1 run), `job_progress` (cập nhật bộ đếm).

`task_done.status` ∈ `LINK | FAIL | TIMEOUT | APPROVE_OK_NO_LINK | UNKNOWN | error | no_promo | …`.
Chỉ `LINK` được coi là thành công; `APPROVE_OK_NO_LINK` không có link để giao.

## Ghi chú

- Job hoạt động lưu trong RAM; giữ 20 job gần đây, không đẩy job đang chạy khỏi RAM.
  Snapshot lịch sử lưu trên đĩa, đọc lại được sau restart; không tự tiếp tục task.
- Log của luồng `cs` được stream live, lưu tối đa 200 dòng/task.
- Client chậm: queue SSE giới hạn 5000 event; khi đầy server gửi snapshot mới qua SSE.
- Thao tác `oaics`/`cs`/`auto` tạo **PaymentIntent thật** (không tự thanh toán UPI).
- Test nhanh không tạo giao dịch: dùng token giả — job sẽ fail ở bước checkout 401.

## Smoke test bằng browser

Có smoke test chạy **browser thật** (Playwright + Chromium) — bắt được lỗi runtime
mà đọc code hay curl không thấy (row không render, asset 404, status sai):

Script mặc định tự khởi động backend giả lập `tests/preview_server.py`, dùng state
tạm và không tạo giao dịch. `--url` chỉ dành cho server kiểm thử riêng.

```bash
python3 smoke_ui.py --tokens 6 --shot /tmp/ui.png
```

Không cần khởi động server trước. Cần Playwright và Chromium đã cài trên máy.

## Lịch sử lỗi đã sửa (đều lọt qua kiểm tra bằng curl)

| Lỗi | Triệu chứng | Nguyên nhân |
|---|---|---|
| `createTaskEl` trả `wrap` thay vì object refs | **Không row nào render** — list trống, không thấy email/step | `t.el = createTaskEl(t)` → `t.el.wrap` undefined → `appendChild` throw; bị `try/catch` trong `es.onmessage` nuốt mất |
| `normalizeStatus` chỉ khớp status chữ HOA | Refresh xong mọi task hiện "chờ", progress 0% | Backend gửi `done`/`fail`/`error`/`no_promo`, hàm chỉ biết `LINK`/`FAIL`/`ERROR` |
| `index.html` dùng đường dẫn asset tương đối | Trang mất cả CSS lẫn JS | `href="style.css"` resolve thành `/style.css`, nhưng static mount ở `/static` |

## Proxy theo từng bước (luồng cs)

`extract_cs` rewrite region của proxy theo chặng, dùng cùng một sticky session:

| Bước | Country | Ghi chú |
|---|---|---|
| Create checkout | `UPI_BOOTSTRAP_COUNTRY` = IN | |
| Apply promotion | `UPI_PROMOTION_COUNTRY` = **IN** | đổi từ VN — promo phải cùng country với billing |
| Tax / PM / approve | `UPI_PROVIDER_COUNTRY` = IN | |

Log in ra `derived proxy chain: ... IN checkout=proxy#x; IN promotion=proxy#y; IN provider/approve=proxy#x`.
Proxy bị **redact** thành `proxy#hash` (`register_proxy_for_redaction`) nên log không lộ credential —
muốn xem region thì đọc chain line hoặc derive bằng `extract_cs.proxy_for_country()`.

Override qua env: `UPI_PROMOTION_COUNTRY`, `UPI_BOOTSTRAP_COUNTRY`, `UPI_PROVIDER_COUNTRY`.

## Step sáng theo tiến độ

Bước đang chạy được tô sáng: nền xanh + viền sáng + vạch accent trái + nhịp glow
(`@keyframes stepGlow`). Bước xong = xanh lá, thất bại = đỏ. Tiến độ **đơn điệu**
(chỉ tiến, không lùi) — các vòng retry lặp lại marker nhưng bước đã xong không bị tụt.

## Thử nghiệm vượt `generic_decline` (id phiên Stripe)

`extract_cs.stripe_browser_id()` chỉ sinh chuỗi **cùng định dạng** (`uuid4` + 8 hex) chứ
chưa từng đăng ký với Stripe, trong khi Stripe.js thật đăng ký phiên tại
`https://m.stripe.com/6` rồi dùng id do Stripe cấp. Endpoint đó trả id cho cả HTTP client
thuần, nên có thể thử "đăng ký phiên" trước khi tạo SetupIntent:

```bash
UPI_STRIPE_TELEMETRY=1 python3 -c "import extract_cs; print(extract_cs.stripe_telemetry_ids())"
```

Bật `UPI_STRIPE_TELEMETRY=1` → `build_ctx()` dùng `muid/guid/sid` do Stripe cấp (gọi qua
đúng proxy của task), lỗi mạng thì tự rơi về id ngẫu nhiên như cũ. **Mặc định TẮT** để còn
A/B: chạy 2 batch account cùng nguồn (1 bật / 1 tắt), so tỉ lệ `generic_decline` mỗi lần chạy.

Lưu ý: TLS fingerprint **đã** là Chrome thật (`curl_cffi` + `impersonate=chrome146` trong
`new_session`), nên hướng "đổi JA3" không cần làm nữa; đòn còn lại là telemetry id, uy tín
account, và (đắt nhất) chạy payment element bằng browser thật.

## Địa chỉ hoá đơn: không dùng list cứng nữa

Mặc định `UPI_BILLING_FALLBACK=0`: nếu không dựng được địa chỉ từ IP exit (mức 2 = PIN
xác thực, mức 1 = ipwho + Nominatim) thì **task fail ngay ở đầu `run_once()`** — trước cả
bước tạo checkout — chứ không gửi một địa chỉ thành phố metro + PIN cố định không khớp IP.
Bật lại để so sánh bằng `UPI_BILLING_FALLBACK=1`.

Lý do: đo trong log thấy fallback xảy ra ~2,1% số lần dựng profile (14.279/683.350), phần
lớn vì proxy thoát ở nước khác (602 ca Philippines, 35 ca Myanmar) hoặc không định vị được
bang (5.810 ca) — địa chỉ Ấn Độ cứng + IP nước ngoài là tín hiệu xấu với Stripe/OpenAI.
Trong số đơn ra mã, nhóm dùng list cứng chiếm ~6,5% (bỏ ngày 10-04 là artifact của bản code
chưa có geo-IP).

### Suy bang khi API geo không trả `region`

Trong log có **34.908 ca `cannot canonicalise state '?'`** — kiểm lại thì **100% là `'?'`**,
tức API **không trả về region**, chứ không phải tên bang viết khác, nên bảng alias tên bang
không cứu được ca nào. Cách vá:

* `geobilling._CITY_STATES` — bảng **510 thành phố → bang** (mọi bang, gồm cả tên cũ
  Bangalore/Mysore/Belgaum và biến thể "Bengaluru Urban", "Mumbai Suburban District").
* `geobilling.state_from_city()` dùng làm mắt cuối trong chuỗi suy bang của
  `address_for_proxy()` (sau reverse geocode, `state_for_pin`, region của IP) và trong mức 1
  của `extract_cs` khi `state_code_in()` thất bại.
* Mức 1 cũng lấy **quận/huyện thật của PIN** (`pin_district()`) làm city khi reverse geocode
  không trả city.
* `canonical_state()` bỏ thêm tiền tố/hậu tố hay gặp: `State of Kerala`, `Kerala state`,
  `Bihar, India`.

### Kiểm tra địa chỉ trước khi gửi (`billing check`)

`extract_cs.verify_billing_address()` chạy ngay trong `upi_billing_profile()` (đầu mỗi round)
và trả về một verdict, ghi vào `billing_check` của task + hiện ở panel dòng **Address check**:

| verdict | Nghĩa | Có sửa gì không |
|---|---|---|
| `ok` | PIN có thật, city/state khớp PIN, cùng bang với IP exit | không |
| `fixed` | city/state lệch PIN | **sửa** city/state theo India Post |
| `fixed-pin` | PIN không tồn tại thật (hay gặp ở mức 1) | **thay** bằng PIN đã xác thực cùng bang + đổi city/locality theo PIN đó |
| `mismatch-exit` | PIN thuộc bang khác IP exit | không (phải giữ địa chỉ khớp IP — đây là cảnh báo proxy/geo) |
| `bad-pin` | không tra được PIN (và không vá được `_seed_pins`) | không |
| `unknown` | không có PIN | không |

Fallback mức 1 cũng không còn mượn tên đường của thành phố khác: khi Nominatim không trả
`road` thì lấy tên bưu cục thật của chính PIN đó, không thì chỉ `"<số nhà>, <thành phố>"`.

Kết quả mỗi task có thêm `billing_tier`: `tier2` (IP + PIN xác thực) · `tier1` (IP + reverse
geocode) · `hardcoded-list`, hiện trong panel ở dòng **Address source**.

## Trộn pool proxy (Mix pool order)

`_pick_proxy` chia proxy theo **thứ tự** trong pool (round-robin, để 100 task trên 100
proxy thì không trùng nhau). Pool dán tay thường được xếp theo từng khối vùng —
200 dòng `-st-Maharashtra`, rồi 200 dòng `-st-Bihar`… — nên nếu không trộn thì 200 task
đầu chỉ nhận đúng **một** vùng. Tick **"Mix pool order"** (mặc định BẬT) để server trộn
pool một lần cho mỗi job (`engine.mix_pool`), nhờ đó mỗi account rút từ một nhóm vùng khác
nhau. Bỏ tick = giữ nguyên thứ tự dán vào.

Trộn bằng RNG gieo theo `job_id`: mỗi job một thứ tự mới, cùng job thì tái lập được. Pool
sau khi trộn vẫn được ghi ra `cs_state/web_proxies_<job>.txt` (đúng thứ tự đã dùng) và
nhãn job hiện thêm `· mixed` để biết lần chạy đó có trộn hay không.

## Tắt bộ lọc proxy (Proxy quality filter)

Tick **"Proxy quality filter"** (mặc định BẬT): mỗi worker tự probe proxy của mình, loại con
không kết nối được / sai nước / tor / malicious rồi thử con kế tiếp; hết pool thì sinh thêm
1 session mới. Đây là đường `UPI_PROXY_QUALITY=lazy`.

Bỏ tick → `Job.filter_proxies = False`: dùng pool **nguyên trạng**, chia theo thứ tự (hoặc
thứ tự đã trộn nếu bật Mix pool order), **không probe, không loại, không sinh bù**. Nhãn job
hiện thêm `· no filter` để biết lần chạy đó có lọc hay không — dùng để so 2 chiều xem bộ lọc
có thật sự giúp gì.

Lưu ý: bộ lọc chỉ loại proxy *hỏng/sai nước*, **không** loại theo `risk`/verdict. Số liệu
74.276 lần thử cho thấy tỉ lệ ra mã ₹0 gần như không đổi theo chất lượng IP (clean 0,47% ·
medium 0,32% · dirty 0,37%), nên tắt lọc chủ yếu để tiết kiệm 2–5s probe mỗi task.

## Lọc proxy sạch / risk thấp trước khi chạy

Bật **"Chỉ dùng proxy sạch (risk thấp)"** + đặt **điểm tối thiểu** → server quét cả pool
trước, chỉ proxy đạt ngưỡng mới được đưa vào chạy.

Nút **"Quét proxy"** quét riêng để xem bảng xếp hạng (không chạy task).

### Cách chấm điểm (`cli.score_proxy`)

| Tiêu chí | Điểm | Ghi chú |
|---|---|---|
| Kết nối được | +45 | không được → 0 điểm, cờ `unreachable` |
| Đúng country | +25 | sai → **chặn trần 25 điểm**, cờ `wrong-country` |
| ISP không phải datacenter | +15 | khớp keyword cloud/hosting/vps → cờ `datacenter-isp` |
| IPv4 | +8 | IPv6 → cờ `ipv6` |
| Độ trễ < 2500ms | +7 | > 6000ms → cờ `slow` |
| Lịch sử thành công | +6/lần (tối đa +18) | đọc từ `proxy_state.json` |
| Lịch sử thất bại | −10/lần (tối đa −30) | cờ `history-fail` |

Xếp hạng: **A** ≥75 · **B** ≥55 · **C** ≥35 · **F** <35.

Lịch sử lấy từ `proxy_state.json` do chính `extract_cs` ghi lại (`record_proxy_result`),
tra theo `proxy_short()` — nên proxy từng fail vì "unusual activity" sẽ bị trừ điểm.

### API

```bash
curl -X POST localhost:8099/api/scan-proxies \
  -d '{"proxies":"host:port:user:pass","country":"IN","workers":12}' \
  -H 'Content-Type: application/json'
# -> {total, grades:{A,B,C,F}, usable, results:[{score,grade,ip,country,ip_type,org,latency_ms,flags,history}]}
```

`POST /api/run` nhận thêm `min_score` (0..100). `min_score > 0` → quét & lọc trước khi chạy;
không proxy nào đạt → HTTP 400 kèm điểm cao nhất đạt được.
