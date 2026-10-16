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

## Skip Apply promotion khi account không đủ điều kiện

Đo trên job 7.302 task (`380a86aad3a0`):

| Trường hợp ngay sau `create_checkout` | Số task | Xử lý |
|---|---|---|
| `amount` đã = 0 | 789 | **đã skip** checkout/update từ trước (`checkout already 0 after create_checkout; skipping Apply promotion`) |
| `amount` khác 0 | 1.687 | trước đây vẫn gọi checkout/update — nhưng chỉ **1/1.687 (0,06%)** kéo được về 0 |

Nên giờ mặc định **bỏ luôn checkout/update** khi `amount` khác 0 và fail nhanh với
`promo_not_eligible` (đỡ 1 request checkout/update + 1 lần `stripe_init` ≈ 10–20s mỗi task,
trên batch 7k là ~7–9 giờ-worker). Bật lại để thử bằng `UPI_TRY_PROMO_UPDATE=1`.

## Radar Session — đã thử, Stripe không nhận, đã bỏ

Docs Stripe ([radar/optimize-risk-factors](https://docs.stripe.com/radar/optimize-risk-factors))
nói với tích hợp gọi API trực tiếp (không dùng Checkout/Elements) thì Stripe không tự thu tín
hiệu thiết bị, và **advanced risk factors** thu qua **Radar Session** là nhóm đáng giá nhất
(+36% model). Vì vậy đã thử: tạo `rse_…` bằng `POST /v1/radar/session` rồi đính
`radar_options[session]` khi tạo UPI PaymentMethod.

**Đo trên job thật — bỏ vì Stripe không nhận tham số này:**

| Ngày | Job | Kết quả |
|---|---|---|
| 16/10 | `4f9dd5274076` | 77/77 task không tạo được session (dạng body `key+mids[0]` bị `parameter_unknown`) |
| 16/10 | `b3971ef1e10b` | `rse_…` tạo được **ngay** ở dạng `key+muid+sid+guid` (89/89), nhưng `radar_options[session]` khi tạo PM trả `400 parameter_unknown — Received unknown parameter: radar_options` ⇒ **89/100 task chết** |
| 16/10 | `5f19288946e2` | bản tự hạ cấp chạy đúng: task tạo PM xong, nhưng 2/2 acc vào tới Stripe vẫn `generic_decline` |
| 16/10 | probe trực tiếp | **ghim header `Stripe-Version` cũng không cứu được**: `2025-03-31.basil` và `2025-06-30.basil` đều trả `400 parameter_unknown — Received unknown parameter: radar_options` (probe gọi thẳng `POST /v1/payment_methods` bằng `pk_live_…` thật + `rse_…` thật) |

Kết luận: `radar_options` **không phải** chuyện API version (đã thử ghim cả `2025-03-31.basil`
lẫn `2025-06-30.basil` — vẫn `parameter_unknown`); tham số này không khả dụng cho publishable
key/tài khoản này, nên Radar Session không thể
gắn vào luồng này; giữ lại chỉ tốn 1 request/task và có nguy cơ giết task nếu Stripe đổi lỗi.
⇒ **đã xoá** `stripe_radar_session()` và đoạn đính `radar_options`. Giữ lại
`stripe_telemetry_ids()` (m.stripe.com) vì nó còn dùng để điền `guid/muid` **thật** vào payload
init — đó là thứ duy nhất của nhóm tín hiệu này mà luồng gửi được.

Hai điểm khác từ docs vẫn đúng và không cần code: (1) Radar chỉ quét SetupIntent khi bật
*"Use Radar on payment methods saved for future use"* trong Radar settings — luồng này đang BẬT
(vì ta nhận `generic_decline` từ SetupIntent); (2) email/name/địa chỉ đã gửi đủ qua
`billing_details` / `customer_data[...]`.

## Dump toàn bộ API (HAR) — không phải bản ghi tay

Trước đây `extract_cs.dump_http()` chỉ ghi ở **24 điểm gắn tay trong 315 chỗ gọi HTTP** và
**không ghi header** — tức là mất đúng phần "giống browser" (UA, `sec-ch-ua`, cookie,
`Origin`/`Referer`, `x-openai-*`), và traffic của `sentinel_runner.js` (process Node) không
được ghi gì. Nay bật công tắc **"Dump all API traffic"** (hoặc `UPI_DUMP=1`) là ghi ở **tầng
transport**, phủ mọi call:

* `traffic_dump.py` nhận entry từ `extract_cs._counting_request` (bọc `Session.request` của cả
  `requests` lẫn `curl_cffi`) — mọi request của luồng Python;
* header lấy từ **request đã chuẩn hoá** (`response.request.headers`) nên có cả `cookie` do
  cookie jar chèn vào, `authorization`, `openai-sentinel-token`, `oai-*`, `sec-ch-ua`…;
* `sentinel_runner.js` ghi vào **cùng file** qua `UPI_DUMP_FILE` (env do
  `traffic_dump.child_env()` truyền xuống `check_upi_eligibility._run_node`), gồm cả nhánh
  python fallback bên trong nó.

Định dạng: `dumps/<task>/<task>.jsonl`, mỗi dòng 1 **HAR 1.2 entry**, gồm:

* request + response đầy đủ: `status`, header **nguyên văn** (không che), body **không cắt**
  (`UPI_DUMP_BODY_LIMIT=0` mặc định; đặt số nếu muốn chặn dung lượng),
* `cookies` **dạng cấu trúc** cho cả 2 chiều (request: từ cookie jar/Cookie header; response:
  `Set-Cookie` kèm `path`/`domain`/`secure`/`expires`),
* `time` (đo quanh call) và `serverMs` (`response.elapsed` — thời gian server+network),
* `redirects` + `redirectCount`: chuỗi redirect thật (`response.history`) mà HAR chuẩn không giữ.

Cả dump tay (`.txt`, 24 điểm `dump_http`) lẫn dump HAR (`.jsonl`) đều nằm trong **cùng một
thư mục theo task** `dumps/<task>/` — không rải file ra `dumps/` gốc; chạy trực tiếp
`extract_cs.py` mà không có nhãn thì vào `dumps/manual/`.

Traffic của **tiến trình cha** `cli.py` (xếp hạng pool, kiểm tra eligibility…) ghi riêng vào
`dumps/cli/cli.jsonl`; mỗi task con vẫn có file riêng `dumps/t<idx>/` (CLI truyền
`UPI_TASK_LABEL=t<idx>` xuống con) nên không trộn lẫn.

Body vẫn đi qua `_redact_text` (che Bearer/token **trong body**); muốn body cũng nguyên văn
(replay y nguyên request) thì đặt `UPI_DUMP_RAW_BODY=1`.

Đổi sang `.har` để mở bằng Chrome DevTools/Charles:

```bash
python3 dumps_to_har.py dumps/t12                  # 1 task -> dumps/t12/t12.har
python3 dumps_to_har.py dumps --merge job.har     # gộp cả job -> 1 file
python3 dumps_to_har.py dumps --summary           # thống kê theo host/status, không ghi file
```

**Cảnh báo:** vì header không bị che nên file dump chứa **access token thật**. `dumps/` đã nằm
trong `.gitignore` — đừng commit, đừng copy ra ngoài repo.

## Lỗi `400 Billing country must match request country`

OpenAI từ chối tạo checkout vì "nước billing phải khớp nước của request" (dù proxy exit là IN
theo ipwho). Đo trên 4.607 account từng dính: chỉ **22 (0,5%)** từng ra được mã ở job khác,
so với ~3% chung ⇒ đây là chuyện **account/region**, không phải proxy hỏng. Nên:

* trong flow: gặp lỗi này thì **break**, không thử proxy khác (trước đây thử thêm 2 lượt vô ích);
* ở engine: đánh `no_retry` (`account region does not match the IN billing country`).

Riêng số **proxy ứng viên cho chặng checkout** giờ **không buộc theo `retries`**: trước đây
`_env_values()` ghi đè `UPI_CHECKOUT_RETRY_MAX=3` bằng `retries` nên log hiện
`checkout-retry=1` — nay là `max(3, retries)`.

## Chính sách retry theo loại lỗi (audit)

Đo trên **3.965 task có ≥2 lượt chạy** trong log còn giữ: **0 lần retry nào ra được link**, và
các nhóm lỗi lặp lại y hệt sau khi chạy lại:

| Lỗi ở lượt trước | Số lần thử lại | Lượt sau OK | Lặp y hệt lỗi cũ |
|---|---|---|---|
| checkout 403 | 1.521 | 0 | 44,3% |
| lỗi vận chuyển proxy | 1.030 | 0 | 11,2% |
| timeout | 876 | 0 | 1,6% |
| no link at approve (chung) | 343 | 0 | **82,2%** |
| chỉ có hosted link (không phải QR) | 248 | 0 | 1,2% |
| user already paid | 8 | 0 | 62,5% |

Nên engine **bỏ retry** cho các nhóm chắc chắn là lỗi cấp account (đổi proxy cũng vô ích):
`risk decline` (theo cả cờ lẫn nội dung lỗi) · `promo not eligible` · `checkout 403` ·
`user already paid` · `chỉ có hosted link` · **`flow stopped at step 'approve'`** (bật lại bằng
`UPI_RETRY_APPROVE_STALL=1`).

Vẫn **giữ retry** cho lỗi tạm thời: lỗi vận chuyển proxy, timeout, không dựng được địa chỉ
(nhóm này có lượt chạy lại rẻ hoặc do chọn nhầm proxy). Lưu ý `retries=1` = không có lượt nào.

## Kiểm tra địa chỉ ngay lúc CHỌN PROXY (không đợi tới bước billing)

Proxy không dựng được địa chỉ hoá đơn (IP exit không định vị được, ipwho bị rate-limit,
PIN không tra được) thì bị **loại ngay ở tầng chọn proxy** như proxy chết —
`engine._address_usable()` gọi `extract_cs.billing_address_ok()` (có cache 15 phút theo proxy).
Tắt bằng `UPI_ADDRESS_PRECHECK=0`.

Vì sao không để tới bước điền billing: lúc đó task đã chạy hết checkout, và retry nghĩa là
chạy lại **cả flow** — đắt ngang dính risk decline. Kiểm ở tầng chọn proxy chỉ tốn ~1–3s
(đã cache) và worker đổi ngay sang proxy khác.

Nếu vẫn lọt tới bước billing (ví dụ geo chết đúng lúc đó), engine cho **thêm 1 lượt với proxy
mới** kể cả khi `retries = 1` (`_is_address_error`), và trong flow `billing_for_round()` cũng
thử các proxy khác của cùng round trước khi bỏ task.

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

## Lỗi vận chuyển proxy (`TLS connect error`) và cách chống

Triệu chứng trên UI: `no link — flow stopped at step 'approve' | last log: … promotion stage
failed: Failed to perform, curl: (35) TLS connect error …`. Đo trực tiếp trên pool hiện tại:

| Phép đo | Kết quả |
|---|---|
| 30 proxy, gọi **lần lượt** (1 kết nối/lúc) | **11/30 chết hẳn** (37%) — `ProxyError` |
| 19 proxy còn sống, 20 kết nối song song | 0% lỗi |
| 19 proxy còn sống, 60 kết nối song song | 0% lỗi |
| 19 proxy còn sống, **120 kết nối song song** | **4,2%** `TLS connect error` |

Tức có HAI nguồn: (1) proxy chết hẳn trong pool — lọc bằng **Proxy quality filter** hoặc
`region_pool.py --check`; (2) handshake rơi khi quá nhiều kết nối đồng thời.

Cách chống trong code: `extract_cs.with_transport_retry()` bọc các chặng dễ rơi (tạo checkout,
checkout/update của promotion) — gặp lỗi vận chuyển thì thử lại **3 lượt cách 1,5s**
(`UPI_TRANSPORT_RETRY`, `UPI_TRANSPORT_RETRY_DELAY`), còn lỗi nghiệp vụ (403 chặn checkout,
decline, promo không hợp lệ) thì **ném ra ngay**, không đốt proxy. Kết quả task cũng ghi rõ
nguyên nhân: `proxy transport error at 'promotion' stage (TLS/connection) — no link`.

## Tiến độ bước (step) trong card task

Tiến độ phải **đơn điệu**: bước nào đứng trước một bước đã xong thì chỉ có thể là
`done`/`skip`, không bao giờ còn `pending`. Lỗi cũ (chỉ ở chế độ **in-process** mà desk
đang dùng): luồng subprocess có `cli.make_step_advancer()` riêng để lấp các bước bị nhảy
qua, còn in-process thì không — nên card hiện ✓ ở giữa mà phía trên vẫn là ô trống, nhìn
như "step hiện tùm lùm, không theo kịp log".

Giờ `web/engine.py::_mk_hooks.step()` tự đánh dấu `skip` cho mọi bước **phía trước** bước
vừa tới mà còn `pending` (kèm event `step` để UI cập nhật ngay, không cần F5), và bỏ qua
bước đầu `runner` (là cả luồng, không phải bị bỏ). Retry vẫn reset toàn bộ về `pending`.

## Dọn log (`clean_logs.sh`)

`logs/` phình ~1 GB mỗi tuần nếu để nguyên. Dùng:

```bash
./clean_logs.sh                    # xem trước, giữ 3 ngày (DRY-RUN, không xoá gì)
./clean_logs.sh --days 7           # xem trước với mốc 7 ngày
./clean_logs.sh --apply            # xoá thật
```

* **Không bao giờ** xoá `logs/web_*_results.json` trừ khi bạn tự đặt `--results-days N > 0`
  — chúng là nguồn của bảng thống kê mã ₹0 và mọi phân tích lịch sử.
* Giữ nguyên cache tra cứu trong `cs_state/` (`in_address_ledger.json`,
  `in_pincode_store.json`, `in_state_seeds.json`).
* Từ chối chạy khi đang có job (`--force` để bỏ qua) — xoá state của task đang chạy là hỏng task.

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
