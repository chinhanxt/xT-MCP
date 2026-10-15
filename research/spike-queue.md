# Spike hàng đợi/timer cho `xt-worker`: procrastinate vs pgqueuer vs tự viết (SKIP LOCKED)

Ngày 2026-10-15. Hạng mục v0.0 "Spike hàng đợi procrastinate và pgqueuer (½ ngày)". Mọi số liệu dưới đây là output thật, chạy trên Postgres 17.11 (`postgres:17-alpine`, cổng 15460, đã xóa container sau khi xong), Python 3.12.3 (import + S1 cũng chạy lại trên 3.13.14), psycopg 3.3.6. Máy dev một node, có nhiễu; chỉ dùng để so sánh tương đối.

Mã: `research/spike-queue/` (SPDX Apache-2.0). Output thô: `research/spike-queue/out/*.txt`. Chạy lại: `python scenarios.py <hand|proc|pgq> s1 s2 s3 s4 s5 s7b s8` (và `s7` cho throughput).

## Kết luận

**Chọn procrastinate 3.10.0 (MIT) làm nền job/timer của `xt-worker`.** Giữ bản tự viết (`hand_queue.py`, 111 dòng) làm phương án dự phòng. Không chọn pgqueuer cho bản v0.x.

Lý do chính:
1. Cả ba đều đạt điểm "sống còn": enqueue chung transaction với ghi nghiệp vụ (rollback thì không có job), retry/dead-letter, hồi phục sau `kill -9`, job trễ, hủy job, cron không nhân đôi khi chạy 2 worker, RLS đúng tenant. Nên quyết định dựa vào phần còn lại.
2. procrastinate có sẵn `connection=` cho defer trong transaction của caller, dùng psycopg3 gốc (khớp stack đã pin, `psycopg[pool]`), chỉ 9 gói / 2,9 MB, dự án lâu đời (2019, 61 contributor, release 3.10.0 ngày 2026-09-23), có bảng `procrastinate_events` lưu lịch sử, có CLI `shell/healthchecks/schema`.
3. pgqueuer nhanh nhất (3131 job/s) nhưng: kéo 22 gói / 33 MB (pydantic, typer, rich, uvloop, anyio...); driver psycopg của nó **bắt buộc `autocommit=True`** và có đuôi trễ NOTIFY 1 đến 2 giây (đo bên dưới), muốn trễ ~15 ms phải thêm `asyncpg`; job thành công bị xóa khỏi bảng (lịch sử chỉ còn trong `pgqueuer_log`).
4. Bản tự viết đủ dùng và có cron theo múi giờ thật, nhưng ta tự gánh mọi lỗi biên (đã phải sửa 2 lỗi trong lúc spike: ngủ quá 5 giây khi chỉ còn job hết lease; tick lịch mỗi vòng làm chậm 2,3 lần), và không có công cụ vận hành.

Điều kiện kèm theo khi dùng procrastinate (phải làm, không có sẵn):
- **Phải tự viết task định kỳ "recover stalled jobs"** (`get_stalled_jobs(seconds_since_heartbeat=...)` rồi `retry_job_by_id_async`). Không viết thì job của worker chết nằm ở `doing` mãi. Mặc định cửa sổ là 30 giây; spike chỉnh 6 giây.
- **Không có lease fencing**: worker bị treo (không chết) mà bị coi là stalled sẽ khiến job chạy 2 nơi. Handler phải idempotent (khóa `job_key` + `INSERT ... ON CONFLICT`, như `effects` trong spike) và kiểm lại quyền lúc chạy, đúng như dòng "Lease fencing" của bảng bảo mật xt-worker. Chưa kiểm chứng: job bị recover có vượt giới hạn retry hay không (`retry_job_by_id_async` không đi qua `RetryStrategy`); cần đặt trần riêng nếu dùng.
- **Cron luôn tính theo UTC** (cả procrastinate lẫn pgqueuer gọi `croniter` không tz). "Mỗi thứ Hai 08:00 Asia/Ho_Chi_Minh" = `0 1 * * 1` UTC. Việt Nam không có DST nên cách này đúng vĩnh viễn; nếu sau này cần tenant ở múi giờ có DST thì lịch theo tz phải do xT tự giữ (bảng `xt_schedules` + `next_fire()` trong `hand_queue.py`, đã chạy thật) rồi defer job bằng `schedule_at`.
- Timer SLA của SpiffWorkflow: một task định kỳ (5 đến 10 giây) chạy poller `next_due <= now()` đã mô tả ở `spike-spiffworkflow.md`, hoặc defer một job `schedule_at = next_due` mỗi khi lưu instance (hủy/đổi khi task xong sớm). Cả hai cách dùng được; cách job `schedule_at` tránh quét bảng.

## Giấy phép, bảo trì, độ nặng

| | procrastinate | pgqueuer | tự viết |
|---|---|---|---|
| Phiên bản | 3.10.0 (2026-09-23) | 1.6.0 (2026-10-11) | 111 dòng |
| Giấy phép | MIT (classifier PyPI + LICENSE "Copyright 2019-2021 PeopleDoc" + GitHub API `MIT`) | MIT (LICENSE "Copyright 2024 janbjorge" + GitHub API `MIT`; trường `License` PyPI cũng ghi MIT) | Apache-2.0 (của ta) |
| Tuổi / người đóng góp | tạo 2019-02, 61 người, 1420 sao | tạo 2024-04, 19 người, 1538 sao | - |
| Commit 90 ngày gần nhất (GitHub API) | 98 | 100 (đạt trần 100 của trang) | - |
| Release gần đây | 3.10.0 (09-23), 3.9.0 (06-20), 3.8.1 (04-08) | 1.6.0 (10-11), 1.5.0 (10-04), 1.4.0 (09-14), 1.3.2 (07-27) | - |
| Issue mở | 97 | 7 | - |
| Python | >=3.10; chạy 3.12.3 và 3.13.14 | >=3.10; chạy 3.12.3 và 3.13.14 | idem |
| Driver | psycopg3 async + sync (`PsycopgConnector`, `SyncPsycopgConnector`) | asyncpg hoặc psycopg3 (chỉ một connection, bắt buộc autocommit) | psycopg3 |
| Phụ thuộc cài thêm | 9 gói, 2,9 MB (asgiref, attrs, croniter, packaging, psycopg, dateutil, six, typing-extensions) | 22 gói, 33,3 MB (pydantic, pydantic-settings, typer, rich, anyio, uvloop, tabulate, croniter...) | croniter |
| Bảng trong DB | 4 (`procrastinate_jobs/workers/periodic_defers/events`) + hàm plpgsql | `pgqueuer`, `_log`, `_statistics`, `_schedules` + trigger NOTIFY | 2 (`xt_jobs`, `xt_schedules`) |

`THIRD_PARTY_NOTICES.md` đã có sẵn hai dòng procrastinate 3.10.0 / pgqueuer 1.6.0 đúng MIT, khớp kết quả kiểm lại ở đây. Nếu chọn procrastinate thì dòng pgqueuer có thể bỏ; `croniter` (MIT) là phụ thuộc bắc cầu cần thêm vào bảng.

## Kết quả theo kịch bản

Cấu hình thử: job `max 3 lần`, lease/heartbeat 6 giây, handler chạy bằng role `xt_app` (NOSUPERUSER NOBYPASSRLS), 2 tenant A/B với bảng `orders` có RLS `tenant_id = current_setting('xt.tenant_id', true)::uuid`.

### S1. Outbox cùng transaction + RLS (điểm quan trọng nhất)

Cả ba: mở `conn.transaction()`, `set_config('xt.tenant_id', ..., true)`, `INSERT orders`, rồi enqueue bằng chính connection đó.

```
[hand] sau commit/rollback, trạng thái job: {'s1-commit-A': ('queued',0,None), 's1-commit-B': ('queued',0,None), 's1-rollback': None}
[proc] ...                                  {'s1-commit-A': ('todo',0,...),   's1-commit-B': ('todo',0,...),   's1-rollback': None}
[pgq]  ...                                  {'s1-commit-A': ('queued',0,None), 's1-commit-B': ('queued',0,None), 's1-rollback': None}
orders 'rollback' tồn tại: 0   (cả ba)
job s1-rollback có chạy: 0     (cả ba)
```
- Đạt cả ba: commit thì có job, rollback thì không có job lẫn không có dòng nghiệp vụ.
- procrastinate: `job.configure(connection=conn, queueing_lock=..., schedule_in=...).defer_async(**kw)`; connection phải là `psycopg.AsyncConnection`, transaction do ta quản lý. Chạy tốt với connection `autocommit=True` + `async with conn.transaction()`.
- pgqueuer: `Queries(PsycopgDriver(conn)).enqueue(...)`. `PsycopgDriver.__init__` ném `RuntimeError` nếu connection không `autocommit=True`, nên chỉ dùng được bằng khối `conn.transaction()` tường minh (đã chạy đúng). Hàm sync tương ứng là `SyncPsycopgDriver`.
- Cô lập tenant (handler `SET LOCAL` bằng `set_config(..., true)` trong transaction riêng, role không bypass RLS):

```
effect ('s1-commit-A', <A>, seen_rows=3, seen_tenants=<A>, seen_noctx=0) | tenant đúng: True
effect ('s1-commit-B', <B>, seen_rows=3, seen_tenants=<B>, seen_noctx=0) | tenant đúng: True
```
A (2 dòng + 1 dòng vừa commit) và B (3 dòng) chỉ thấy đúng dòng của mình; không đặt ngữ cảnh thì thấy 0 dòng. Cả ba cùng kết quả vì RLS nằm ở handler, không phụ thuộc thư viện hàng đợi. Lưu ý: bảng hàng đợi của cả ba **không có RLS** và `tenant_id` chỉ là dữ liệu trong payload (procrastinate: `args`; pgqueuer: `payload` bytea; hand: cột `tenant_id`), nên job chỉ nên mang `tenant_id` + id nhỏ, không mang dữ liệu nhạy cảm.

### S2. Retry, backoff, dead-letter

Handler luôn ném lỗi.

| | số lần chạy | khoảng cách giữa các lần (s) | trạng thái cuối |
|---|---|---|---|
| hand (max_attempts=3, base 1 s, jitter 0,8 đến 1,2) | 3 | 1,25 / 2,02 | `dead` + `last_error` |
| procrastinate (`RetryStrategy(max_attempts=3, wait=1, exponential_wait=1)`) | **4** | 2,38 / 2,98 / 3,96 | `failed` (còn trong bảng) |
| pgqueuer (psycopg, `DatabaseRetryEntrypointExecutor(max_attempts=3)`, `on_failure="hold"`) | **4** | 3,01 / 5,03 / 5,09 | `failed` (còn trong bảng) |
| pgqueuer (asyncpg, cùng cấu hình) | 4 | 5,02 / 5,02 / 5,01 | `failed` |

- Với procrastinate và pgqueuer, `max_attempts=N` nghĩa là N lần *retry* sau lần chạy đầu (tổng N+1). Phải ghi vào tài liệu để khỏi lệch với kỳ vọng.
- pgqueuer: backoff chính xác khi chạy riêng (`retry_probe.py` với `dequeue_timeout=30s`: khoảng cách 1,01 / 2,01 / 4,01 s với `initial_delay=1`; 8,01 / 16,01 s với `initial_delay=8`). Trong worker của spike (`dequeue_timeout=5s`, `batch_size=1`, `max_concurrent_tasks=2`, `heartbeat_timeout=6s`) khoảng cách bị làm tròn lên 5 giây. Chưa rõ nguyên nhân; cần chỉnh/kiểm nếu chọn pgqueuer.
- Dead-letter: hand dùng `status='dead'`; procrastinate và pgqueuer giữ dòng ở `failed` (pgqueuer chỉ khi `on_failure="hold"`, mặc định `delete` là xóa mất). Cả hai đều retry lại thủ công được (`pgq` có `retry_job`/`list_failed_jobs`; procrastinate có `retry_failed_job` qua `JobManager`; chưa chạy thử).

### S3. `kill -9` worker giữa job

Job `slow` (4 giây) → kill -9 worker ngay sau khi nhận → khởi worker mới. `attempt_log` ghi mọi lần giao.

| | thời điểm job được giao lại (giây sau khi kill) | cơ chế | ghi chú |
|---|---|---|---|
| hand | 6,0 | `lease_until < now()` bị `claim` nhặt lại; quá `max_attempts` thì `dead` | fencing bằng `(locked_by, attempts)` ở bước ack |
| procrastinate | 6,3 (sau chỉnh `seconds_since_heartbeat=6`); **31,2 với mặc định 30 s** | heartbeat worker mỗi 2 s (`stalled_worker_timeout=6`) + task recover tự viết chạy mỗi 2 s | không có task đó thì không bao giờ hồi phục. Output ban đầu (31,2 s) nằm trong `out/proc.txt`, bản chỉnh trong `out/proc_s3_fix.txt` |
| pgqueuer | 10,7 (asyncpg) / 7,5 (psycopg) | job `picked` có `heartbeat` quá cũ bị dequeue lại, không cần task phụ | hồi phục tự động, lệch nhiễu do chu kỳ polling 5 s |

Cả ba đều giao lại đúng 1 lần cho pid mới và `effects` chỉ ghi 1 dòng (`runs=1`). Đây là at-least-once: lần giao đầu đã bắt đầu (có trong `attempt_log`) rồi bị mất, handler phải idempotent (xem điều kiện ở phần kết luận).

### S4. Job trễ (timer SLA) và hủy

Enqueue trong một transaction: job trễ 3 s, job trễ 3 s sẽ bị hủy, job trễ 120 s (đại diện `run_at = now()+PT2M`).

| | job trễ 3 s chạy sau (s) | job bị hủy có chạy? | job 120 s | cách hủy |
|---|---|---|---|---|
| hand | 3,01 | 0, trạng thái `cancelled` | còn `queued` | `UPDATE ... SET status='cancelled' WHERE dedupe_key=? AND status='queued'` |
| procrastinate | 3,86 | 0, trạng thái `cancelled` | còn `todo` | `job_manager.cancel_job_by_id_async(id)` (tìm id theo `queueing_lock`) |
| pgqueuer (asyncpg) | 3,06 | 0, dòng bị xóa | còn `queued` | `Queries.mark_job_as_cancelled([id])` (tìm id theo `dedupe_key`) |
| pgqueuer (psycopg) | 4,11 | 0 | còn `queued` | idem |

Hủy job *đang chạy* không thử (procrastinate có `abort=True`, pgqueuer có cancel qua NOTIFY, hand không có). Với SLA nên hủy khi job còn `todo`/`queued`, còn nếu đã chạy thì handler tự kiểm trạng thái task trước khi leo thang.

### S5. Cron / định kỳ (2 worker cùng chạy, cron mỗi 5 giây, 22 giây)

| | số lần bắn | khoảng cách (s) | nhân đôi khi 2 worker? |
|---|---|---|---|
| hand (`xt_schedules`, `SKIP LOCKED` + `dedupe_key=name@next_run`) | 4 | 5,01 / 5,02 / 5,01 | không |
| procrastinate (`@app.periodic`, bảng `periodic_defers`) | 5 | 3,77 / 5,00 / 5,01 / 4,99 | không |
| pgqueuer (`@pgq.schedule`) | 4 | 5,00 / 5,01 / 4,98 | không |

- Cú pháp 6 trường của croniter: **giây ở trường cuối** (`* * * * * */5`). Lần đầu tôi viết nhầm `*/5 * * * * *` (= mỗi giây của các phút chia hết 5) làm bản tự viết bắn 21 lần; đã sửa. Cron 5 trường dùng trong sản phẩm không bị ảnh hưởng.
- Cron "thứ Hai 08:00 Asia/Ho_Chi_Minh", tính từ 2026-10-15T12:00Z:
```
'0 8 * * 1' theo thư viện (UTC)     : 2026-10-19T08:00:00+00:00   (= 15:00 giờ VN, SAI nếu ý là 08:00 VN)
'0 8 * * 1' theo múi giờ HCM (hand) : 2026-10-19T01:00:00+00:00   (= 08:00 giờ VN)
cron UTC tương đương '0 1 * * 1'    : 2026-10-19T01:00:00+00:00
```
  Chỉ phần tính toán được kiểm (không chờ đến thứ Hai thật). Cả hai thư viện đều không có tham số múi giờ; người viết skill/pack phải ghi cron theo UTC hoặc đi qua lớp lịch của xT.

### S7. Thông lượng: 10.000 job `noop`, 2 worker (2 tiến trình)

| | enqueue 10k (mỗi job một transaction) | `conc=1`/worker: xong hết | `conc=8`/worker: xong hết |
|---|---|---|---|
| hand | 6,7 s (1495/s)* | 30,2 s (~334 job/s) | 9,1 s (~1110 job/s) |
| procrastinate | 8,7 s (1154/s) | 18,7 s (~542 job/s) | 15,7 s (~648 job/s) |
| pgqueuer (asyncpg, `batch_size=conc`, `max_concurrent_tasks=2*conc`) | 13,8 s (723/s) | 20,2 s (~507 job/s) | **3,7 s (~3131 job/s)** |

\* Bản tự viết đo hai lần: lần đầu 681 enqueue/s và 147 job/s ở `conc=1` (còn tick lịch mỗi vòng); sau khi giới hạn tick 1 lần/giây thì số trên. Số `enqueue` dao động theo nhiễu máy (hand: 6,7 s đến 23,8 s giữa các lần chạy), không kết luận hơn kém giữa các thư viện ở cột này.
Với quy mô xT (workflow doanh nghiệp, không phải luồng sự kiện) cả ba dư: hàng trăm job/s là quá đủ. Throughput không nên là tiêu chí quyết định.

### S7b. Độ trễ LISTEN/NOTIFY

Worker rảnh, polling dự phòng 5 s, 20 job cách nhau 0,7 s; đo từ trước khi enqueue (gồm cả commit) đến lúc handler chạy.

| | median | p95 | max |
|---|---|---|---|
| hand | 11 ms | 21 ms | 28 ms |
| procrastinate (psycopg) | 18 ms | 31 ms | 33 ms |
| pgqueuer (asyncpg) | 16 ms | 47 ms | 69 ms |
| **pgqueuer (psycopg driver)** | **751 ms** | 848 ms | **1077 ms** |

`lat_probe.py` (psycopg) cho ra chuỗi `[1049, 2023, 19, 12, 1022, 2019, 11, 13, 1013, 2012]` ms: NOTIFY đến muộn 1 đến 2 giây theo chu kỳ; `lat_probe2.py` (asyncpg) cho `[114, 12, 6, 11, 12, 11, 13, 10, 10, 12]`. Nguyên nhân gốc chưa tìm (nghi do đọc `notifies()` chung một connection với truy vấn của worker); kết luận thực dụng: pgqueuer + psycopg3 không đạt độ trễ mong muốn với bản 1.6.0 + psycopg 3.3.6.

### S8 / vận hành

- procrastinate: CLI `procrastinate worker|defer|schema|healthchecks|shell`; `shell` có `list_jobs`, `retry`, `cancel`; bảng `procrastinate_events` lưu toàn bộ vòng đời (`deferred`, `started`, `succeeded`, `failed`, `cancelled`, ...); `get_stalled_jobs`, `remove_old_jobs` (task dọn có sẵn, cần lên lịch). Migration có phiên bản (`sql/migrations`). Cảnh báo "App is instantiated in the main Python module" khi chạy `python impl_proc.py worker`: trong sản phẩm tạo `App` ở module riêng.
- pgqueuer: CLI `pgq install|verify|upgrade|dashboard|web|listen|...`, thống kê hàng đợi (`queue_size`, `queue_age`, `job_duration_percentiles`, `throughput_summary`), có dashboard trong terminal và web (cần extra `web`), Prometheus. Vận hành tốt nhất trong ba, nhưng job thành công bị xóa khỏi bảng chính; schema đặt được `--schema/--prefix`.
- hand: chỉ có SQL (`SELECT status, count(*) FROM xt_jobs GROUP BY 1`), không có CLI, không dashboard, không dọn job cũ; phải tự viết.
- Truy vấn kiểm tra trạng thái chạy được ngay (đã dùng trong `impl_*.py:status/job_state`).

## So sánh tóm tắt

| Tiêu chí | hand | procrastinate | pgqueuer |
|---|---|---|---|
| Outbox cùng transaction | đạt | đạt (`connection=`) | đạt (autocommit + `transaction()`) |
| RLS/tenant | đạt (do handler) | đạt | đạt |
| Retry/backoff/dead-letter | đạt | đạt (N+1 lần) | đạt (cần `hold`; làm tròn 5 s trong cấu hình spike) |
| Hồi phục kill -9 | tự động (lease) | cần task recover tự viết | tự động (heartbeat) |
| Fencing | có (locked_by+attempts) | không | không |
| Job trễ / hủy | đạt | đạt | đạt |
| Cron theo múi giờ | **có** | UTC | UTC |
| NOTIFY | 11 ms | 18 ms | 16 ms (asyncpg) / 751 ms (psycopg) |
| Throughput (`conc=8`, 2 worker) | ~1110/s | ~648/s | ~3131/s |
| Phụ thuộc | croniter | 9 gói / 2,9 MB | 22 gói / 33 MB (+asyncpg) |
| Công cụ vận hành | không | CLI + events | CLI + dashboard + Prometheus |
| Rủi ro bảo trì | ta tự gánh | thấp | trung bình (dự án mới, đổi nhanh: 4 release trong 3 tháng) |

## Việc kế tiếp (nếu chốt procrastinate)

1. Đặt `procrastinate` vào `pyproject` (pin 3.10.x), thêm `croniter` vào `THIRD_PARTY_NOTICES.md`, bỏ dòng pgqueuer hoặc đánh dấu "đã loại sau spike".
2. `adapters/queue/` bọc `defer(connection=...)` thành hàm `enqueue_in_tx(conn, job, tenant_id, ...)` để mọi use case đi qua một cửa; payload chỉ có id + `tenant_id`.
3. Viết task `recover_stalled` (có trần số lần giao lại) và `remove_old_jobs`; kiểm thử `kill -9` trong CI bằng `scenarios.py proc s3`.
4. Handler bắt buộc idempotent theo `job_key`; ghi vào skill/tài liệu pack.
5. Kiểm chứng thêm trước v0.1: abort job đang chạy; recover vượt trần retry; hành vi khi Postgres restart giữa lúc worker chạy; hai worker khác phiên bản schema.
