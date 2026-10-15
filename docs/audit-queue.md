<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Audit job queue / xt-worker (procrastinate 3.10)

Ngày: 15/10/2026. Người kiểm: Opus (tester/verifier). Phạm vi: `src/xt/application/jobs.py`, `src/xt/adapters/queue/*`, `src/xt/entrypoints/worker*.py`, `xt worker`, test queue unit/integration, `docs/worker-queue.md`, `make check-worker`. Đối chiếu: `docs/architecture-v2.md` v2.2 (D5/D6, §5.3), `research/spike-queue.md`, `research/sklite-lessons.md` §2. Không sửa `src`.

Môi trường: Python 3.13.14, psycopg 3.3.6, procrastinate 3.10.0, image cục bộ `postgres:17-alpine` (không pull). `df -h /`: 55G, đã dùng 48G, còn 4.6G (92%). Fixture audit dùng container riêng `xt-audit-queue-*` trên `127.0.0.1:20100`, xóa bằng `docker rm -f -v`. Sau khi chạy, `docker ps -a --filter name=xt-audit` và `--filter name=xt-it-worker` đều rỗng.

## 1. Chạy lại test có sẵn

| Lệnh | Kết quả |
| --- | --- |
| `make check-worker` | ruff, format, mypy, import-linter (4 kept), **111 passed**, reuse lint-file: xanh |
| `make test-worker-integration` | **22 passed in 35.00s** |
| `uv run pytest -m integration tests/integration/queue_audit` | **4 xfailed** (strict); chạy `--runxfail` thì cả 4 fail đúng assertion mô tả bên dưới |

## 2. Bảng PASS/FAIL

| # | Hạng mục tấn công | KQ | Bằng chứng |
| --- | --- | --- | --- |
| 1 | Outbox: commit/rollback cùng business write | PASS | test có sẵn `test_business_request_and_queue_job_commit_or_roll_back_together` |
| 2 | Enqueue trong savepoint rồi rollback savepoint, outer commit | PASS | `xt_queue.requests`=0, `procrastinate_jobs`=0 cho key savepoint; job outer=1; enqueue lại key đó ở tx mới OK |
| 3 | Enqueue sau một statement lỗi (tx INERROR) | PASS | `ValueError: queue operation requires an active business transaction` |
| 4 | Autocommit sai: `autocommit=False` / autocommit không mở tx | PASS | lần lượt `requires autocommit=True and an explicit transaction` / `requires an active business transaction`; 0 dòng request |
| 5 | Envelope/payload giả tenant, đổi key, đổi payload | PASS | test review: envelope hash, ràng `job_id`↔request, RLS `procrastinate_jobs` theo `args->>'tenant_id'` |
| 6 | Handler chạy sai role | PASS | `_check_role` lúc start (NOSUPERUSER/NOBYPASSRLS), `SET LOCAL ROLE xt_app`; effects ghi `observed_role=xt_app` |
| 7 | `SET LOCAL` rò sang job kế tiếp trên cùng connection pool | PASS | sau job tenant A, cùng pid: `current_setting('xt.tenant_id', true)=''` |
| 8 | Trạng thái **session** (search_path, temp table) rò giữa tenant trên cùng connection pool | **FAIL (AQ-03)** | cùng pid 76: job tenant B thấy `search_path='xt_t1, public'`, đọc được `pg_temp.leak = 'secret-of-A'`, INSERT không ghi schema rơi vào `xt_t1.effects` với `tenant_id=B` |
| 9 | Job tenant A ghi vào schema `xt_t2` | **CẢNH BÁO (AQ-05)** | INSERT `xt_t2.effects` với `tenant_id=A` thành công; RLS chỉ chặn dòng tenant khác, không chặn schema |
| 10 | `kill -9` giữa handler rồi recovery | PASS | test có sẵn; effects=1 |
| 11 | Zombie: SIGSTOP worker1 giữa handler, worker2 recover, SIGCONT worker1 | PASS (an toàn) / **FAIL liveness (AQ-04)** | recover + claim lại sau 10.4 s; effects lúc zombie dừng=0, cuối cùng=1; job `succeeded`, attempts=2, worker_id=worker2. Nhưng worker2 kẹt chờ lock suốt thời gian worker1 dừng (`pg_stat_activity wait_event_type='Lock'`=1), không có timeout |
| 12 | Idempotency key trùng giữa tenant / giữa task | PASS | PK `(tenant_id,key)`; cùng tenant khác task → `ValueError` (test review) |
| 13 | Cancel vs run (timer SLA) | PASS | handler đang giữ khóa request: cancel bị chặn, sau commit trả `False`; cancel trước: `True`, handler chạy 0 lần |
| 14 | Chuyển cron VN (không DST) | PASS | `0 8 * * 1`→`0 1 * * 1`; `30 6 * * 1,3`→`30 23 * * 0,2`; `0 0 * * 0`→`0 17 * * 6` |
| 15 | Timezone sai/DST/path | PASS | `Asia/Nope`, `../etc/passwd`, `/etc/localtime` bị từ chối; `America/New_York` báo DST unsupported |
| 16 | Next-fire khi session TimeZone ≠ UTC | **FAIL (AQ-01)** | `ALTER ROLE xt_app SET timezone='Asia/Ho_Chi_Minh'`: sau occurrence 12/10, `next_at=2026-10-18 18:00Z` (Chủ nhật, = 01:00 VN), đúng phải là `2026-10-19 01:00Z` |
| 17 | Hai worker cùng materialize lịch | PASS | 8 vòng × 2 runtime `gather`, lịch trễ 5 ngày: 6 occurrence, 6 key khác nhau, không trùng |
| 18 | Lịch "độc" (task không có handler ở worker) | **FAIL (AQ-02)** | `maintenance()` ném `ValueError: No registered job handler: business.finish`; `run()` kết thúc với lỗi đó → toàn bộ worker chết, recovery và lịch của mọi tenant khác dừng |
| 19 | Payload không serialize được / NaN / key không phải str / vòng | PASS | unit `test_jobs.py` |
| 20 | Payload khổng lồ | CẢNH BÁO (AQ-07) | payload 20 MB được nhận trong 1.43 s (args TOAST 240 KB); không có trần |
| 21 | Handler lỗi mãi → dead-letter, off-by-one của spike | PASS | `max_attempts=1` → 1 lần chạy, `failed`, attempts=1; `max_attempts=3` → 3 lần chạy, `failed`, attempts=3, events `deferred,started,(scheduled,deferred_for_retry,started)×2,failed`. Lệch N+1 của `RetryStrategy` upstream đã được `BoundedRetry` khử |
| 22 | Quan sát DLQ | CẢNH BÁO (AQ-08) | chỉ có `status='failed'` + `procrastinate_events` + log ERROR; `xt_queue.requests` vẫn `completed=false,cancelled=false`; không có lệnh/metric/truy vấn DLQ trong tài liệu |
| 23 | Thiếu env | PASS | rc=2, `xt worker: XT_WORKER_MAX_ATTEMPTS is required` |
| 24 | Secret trong log/repr | PASS (mật khẩu) / CẢNH BÁO payload (AQ-06) | DSN sai host: rc=1, không lộ mật khẩu; log worker 2689 byte không chứa mật khẩu; nhưng DEBUG cứng in nguyên payload: `Loaded job info, about to start job xt.dispatch[19](payload={'note': 'PAYLOAD-MARKER-xyz', ...` |
| 25 | xt_app sửa dòng queue của chính tenant | CẢNH BÁO (AQ-09) | `UPDATE procrastinate_jobs SET priority=2147483647` (1 dòng); `SET status='succeeded'` (1 dòng, request vẫn `completed=false`); INSERT job `procrastinate.builtin_tasks.remove_old_jobs` vào queue `xt` thành công, chỉ bị chặn tình cờ do `TypeError: unexpected keyword argument 'tenant_id'` |
| 26 | Handler tự `COMMIT` | CẢNH BÁO (AQ-10) | guard báo `job handler must leave its transaction open` nhưng hiệu ứng đã commit: 2 lần giao → 2 effect |

## 3. Lỗi

### AQ-01 — High — next-fire của periodic phụ thuộc TimeZone của session

- **Vị trí:** `runtime.py` `dispatch_periodic`: `croniter(row["cron_utc"], row["next_at"]).get_next(datetime)`. psycopg trả `timestamptz` theo TimeZone của session; croniter tính cron theo tzinfo của mốc bắt đầu. Cron UTC bị hiểu thành giờ địa phương.
- **Hậu quả:** chỉ cần `PGTZ`, `ALTER DATABASE/ROLE ... SET timezone` hoặc `postgresql.conf timezone='Asia/Ho_Chi_Minh'`, mọi lần sau lần đầu đều lệch 7 giờ: "thứ Hai 08:00 VN" thành "thứ Hai 01:00 VN". Lỗi im lặng, test hiện có không bắt vì container mặc định UTC.
- **Repro:** `test_aq01_periodic_next_fire_is_independent_of_session_timezone`.
- **Sửa:** `croniter(row["cron_utc"], row["next_at"].astimezone(UTC))`; thêm `SET LOCAL TimeZone='UTC'` trong transaction maintenance để phòng hờ.

### AQ-02 — High — một lịch lỗi làm chết toàn bộ worker

- **Vị trí:** `dispatch_periodic` → `queue._enqueue` ném `ValueError` khi `task_name` không có trong handler của worker (lệch phiên bản API/worker, handler bị gỡ). `_maintenance_loop` không bắt lỗi. `run()` dùng `FIRST_COMPLETED` nên worker thoát.
- **Hậu quả:** supervisor restart → lại chết ở tick đầu (crash loop). Recover stalled và lịch của **mọi tenant** dừng theo. Lỗi DB thoáng qua trong maintenance cũng giết worker.
- **Repro:** `test_aq02_poison_schedule_does_not_stop_other_tenants_maintenance`.
- **Sửa:** bọc từng schedule trong try/except: log `schedule_id`/tenant, lùi `next_at` hoặc thêm cột `last_error`/`active=false`, rồi đi tiếp. `_maintenance_loop` bắt lỗi thoáng qua và backoff, không để task maintenance kết thúc.

### AQ-03 — High — tenant pool không reset trạng thái session

- **Vị trí:** `AsyncConnectionPool(...)` trong `QueueRuntime.__init__` không có `reset=`. `SET LOCAL`/`set_config(...,true)` được dọn, nhưng thứ gì handler đặt ở mức session thì sống sang job kế tiếp của tenant khác: `SET search_path`, `set_config(...,false)`, temp table `ON COMMIT PRESERVE ROWS`, prepared statement, `LISTEN`, advisory lock session.
- **Hậu quả:** với schema-per-tenant (D6), một `SET search_path TO xt_t<A>` không LOCAL trong handler là đủ: tenant B đọc temp data của A, và ghi không ghi schema rơi vào schema của A. Policy RLS chấp nhận vì `tenant_id=B`, nên `pg_dump -n xt_t<A>` mang dữ liệu B.
- **Repro:** `test_aq03_pooled_session_state_does_not_leak_between_tenants`.
- **Sửa:** `AsyncConnectionPool(..., reset=_reset)` với `await conn.execute("DISCARD ALL")` (connection autocommit, đang idle); hoặc sau mỗi job chạy `RESET ALL; DISCARD TEMP; DISCARD PLANS` và `pg_advisory_unlock_all()`. Thêm test cùng pid.

### AQ-04 — Medium — zombie giữ khóa không có giới hạn thời gian

- Worker bị dừng (SIGSTOP, GC pause, VM freeze) vẫn giữ transaction tenant và `FOR UPDATE` trên `xt_queue.requests`. Fencing giữ an toàn: không có hiệu ứng đôi. Nhưng delivery đã recover chờ khóa vô hạn và chiếm một slot concurrency. Heartbeat của worker2 vẫn chạy nên job không bao giờ được recover lại. Zombie treo vĩnh viễn thì job treo vĩnh viễn.
- **Repro:** probe SIGSTOP ở mục 11; `test_aq04_tenant_sessions_bound_zombie_lock_holding` (cả hai timeout đều là `0`).
- **Sửa:** đặt `idle_in_transaction_session_timeout` (server tự ngắt session zombie đang idle-in-tx, giải phóng khóa) và/hoặc `lock_timeout` cho pool tenant, qua env bắt buộc mới (ví dụ `XT_WORKER_LOCK_TIMEOUT_SECONDS`) để giữ nguyên tắc "không default ngầm". Truyền bằng `options=-c ...` trong `kwargs` của pool.

### AQ-05 — Medium — job không bị ràng vào schema của tenant

- Dispatcher chỉ đặt `xt.tenant_id`, không ràng schema. Test fixture cấp USAGE `xt_t1` và `xt_t2` cho cùng `xt_app`, còn handler mẫu lấy schema **từ payload**. Payload hợp lệ của A có `"schema":"xt_t2"` vẫn ghi dòng `tenant_id=A` vào schema của B (mục 9). Đây không phải đọc chéo, nhưng phá bất biến "schema = tenant" mà D6 dựa vào để backup/xóa theo tenant.
- **Sửa:** dispatcher tra `tenant_id → xt_t<seq>` từ catalog tin cậy và `SET LOCAL search_path`. Cấm handler lấy schema từ payload. Model engine nên thêm `CHECK (tenant_id = '<uuid>')` trên mỗi bảng `xt_t<seq>.*` (cần phối hợp agent DB).

### AQ-06 — Low — `logging.DEBUG` cứng in payload job

- `worker.py main()` gọi `basicConfig(level=DEBUG)`, procrastinate log nguyên kwargs. Docs đã ghi nhận, nhưng production sẽ lộ PII/định danh nghiệp vụ ra log. **Sửa:** mức log bắt buộc lấy từ env (`XT_LOG_LEVEL`), hoặc đặt riêng logger `procrastinate` ở INFO.

### AQ-07 — Low — không có trần kích thước payload

- 20 MB vẫn được nhận, lưu ở `procrastinate_jobs.args` (và `schedules.payload` với periodic), đồng thời đi qua log DEBUG. **Sửa:** giới hạn trong `JobRequest`, ví dụ 64 KiB, khớp hướng dẫn "chỉ truyền định danh".

### AQ-08 — Low — DLQ khó quan sát

- Khi hết lượt, request không có dấu "dead" và không có metric hay lệnh liệt kê. Một SLA timer chết thì không ai biết, trừ khi đọc log. Re-enqueue cùng key trả về job `failed` cũ. **Sửa:** ghi `xt_queue.requests.dead_at/last_error` trong `retry_owned`/`recover_stalled`; tài liệu cần truy vấn DLQ và quy trình replay (key mới hoặc lệnh retry có fencing).

### AQ-09 — Low — xt_app có UPDATE toàn cột trên `procrastinate_jobs`

- Trong tenant của mình, xt_app có thể đẩy `priority` lên tối đa (fetch sắp theo priority nên starve tenant khác, đúng bẫy fairness ở sklite §2), đánh dấu `succeeded` giả, và INSERT task tùy ý vào queue `xt`. **Sửa:** `WITH CHECK` cho xt_app thêm `task_name='xt.dispatch' AND queue_name='xt' AND priority=0`. Thu hẹp UPDATE về đúng các cột hàm cancel cần (`status`, `abort_requested`).

### AQ-10 — Low — handler tự COMMIT phá exactly-once DB

- Guard sau handler chỉ phát hiện, không ngăn. Đây là hợp đồng với handler tin cậy, nhưng cần ghi rõ trong `docs/worker-queue.md`. Có thể bọc bằng savepoint hoặc dùng một proxy connection chặn `commit`/`COMMIT`.

Ghi chú khác (không tính lỗi): catch-up không có misfire policy (đã nêu trong docs). Không cài `tzdata` nên kết quả phụ thuộc zoneinfo của host: image slim không có `/usr/share/zoneinfo` sẽ báo mọi timezone là "unknown". Key đã bị hủy không re-arm được với giờ khác (`ValueError`), muốn re-arm phải dùng key mới.

## 4. Test hồi quy đã thêm (không sửa src)

- `tests/integration/queue_audit/conftest.py`: fixture `audit_db`, PG riêng cổng 20100.
- `tests/integration/queue_audit/test_queue_audit_regressions.py`: `test_aq01_*`…`test_aq04_*`, đều `xfail(strict=True)`. Khi sửa xong, test chuyển XPASS và làm suite đỏ, lúc đó bỏ marker.

```sh
uv run pytest -m integration tests/integration/queue_audit --no-cov -rxX
uv run pytest -m integration tests/integration/queue_audit --no-cov --runxfail   # xem lỗi thật
```

## 5. Kết luận

Phần lõi vững: outbox chung transaction (kể cả savepoint, statement lỗi, autocommit sai), idempotency theo tenant, envelope hash, RLS trên bảng upstream, fencing ACK/retry theo worker+generation, ngân sách attempts đúng N (đã khử off-by-one của spike), không hiệu ứng DB đôi khi zombie SIGSTOP/SIGCONT. Cấu hình không lộ mật khẩu.

**Verdict: ĐẠT CÓ ĐIỀU KIỆN, chặn merge cho tới khi sửa AQ-01, AQ-02, AQ-03 (High) và AQ-04 (Medium).** Cả bốn sửa nhỏ, khu trú trong `runtime.py`. AQ-05 cần thống nhất với agent model engine trước v0.1 (demo SLA PT2M). AQ-06…AQ-10 đưa vào backlog hardening.

## 6. Phản hồi tác giả

Ngày 15/10/2026. Tác giả tiếp quản: Opus (thay codex đang bận tích hợp). Chỉ sửa `src/xt/adapters/queue/*` và test/tài liệu queue; không đụng `src/xt/entrypoints/`, `src/xt/composition/`. Việc phía entrypoint/provisioning cần nối ghi ở `docs/integration-notes.md` mục *Job queue*.

| # | Trạng thái | Cách sửa | Test hồi quy |
| --- | --- | --- | --- |
| AQ-01 | Đã sửa | `croniter(cron_utc, next_at.astimezone(UTC))` + startup option `-c TimeZone=UTC` cho mọi connection worker (thắng `ALTER ROLE/DATABASE SET`, `PGTZ`) | `test_aq01_*` ×2: có/không pin TimeZone; bản không pin fail nếu bỏ `astimezone` |
| AQ-02 | Đã sửa | lịch không có handler bị bỏ qua (giữ `next_at`), ghi `schedules.last_error`, log ERROR một lần, đếm `stats["schedules_skipped"]`; try/except từng lịch; vòng maintenance bắt lỗi và thử lại | `test_aq02_*` ×2 (lịch tenant khác vẫn materialize; lịch độc hiện trong `xt worker dlq list`); unit `test_maintenance_loop_survives_failing_ticks` |
| AQ-03 | Đã sửa | `AsyncConnectionPool(reset=DISCARD ALL)`; connection lấy ra luôn mới hoặc đã reset | `test_aq03_pooled_*` và `test_aq03_tenant_a_then_b_on_one_connection_*` (pool size 1, cùng pid: GUC `xt.*`, search_path, role, advisory lock, prepared, temp table, LISTEN đều sạch; TimeZone/timeout vẫn còn) |
| AQ-04 | Đã sửa | `statement_timeout`/`lock_timeout`/`idle_in_transaction_session_timeout` qua startup option; tham số `create_runtime` mặc định 30/10/60 s, ≥1, `lock < statement` (L6), ghi trong `docs/worker-queue.md` | `test_aq04_*` ×3 (giá trị trong session, cấu hình tường minh, delivery chờ khóa zombie lỗi `LockNotAvailable` sau 1 s) |
| AQ-05 | Đã sửa (cần provisioning) | catalog tin cậy `xt_queue.tenant_bindings` + `xt_queue.job_binding()` (SECURITY DEFINER, kiểm GUC tenant, role NOLOGIN/NOBYPASSRLS, không USAGE schema tenant khác); handler chạy `SET LOCAL ROLE xt_job_t<seq>` + `search_path` ghim | `test_aq05_*` ×3 (payload trỏ `xt_t2` → `permission denied`, 0 dòng; tenant chưa bind / role có USAGE chéo → fail closed; handler vẫn enqueue tiếp được) |
| AQ-06 | Đã sửa trong adapter | filter `RedactJobPayload` trên mọi logger `procrastinate*` (message + extra `job`/`jobs`) | unit redaction ×2; log subprocess integration hiện `payload=[redacted]` |
| AQ-07 | Đã sửa | `max_payload_bytes` (mặc định 64 KiB, UTF-8) ở `enqueue/schedule_at/periodic` | `test_aq07_*`, unit trước khi chạm DB |
| AQ-08 | Đã sửa | `requests.dead_at/last_error` (tên lớp exception) ghi trong `finish_owned`/`retry_owned`/recover; `xt.adapters.queue.dlq` list/inspect (payload chỉ key + kích thước) | `test_aq08_*` (kể cả subprocess `python -m xt.adapters.queue.dlq inspect`, không lộ marker payload) |
| AQ-09 | Đã sửa | xt_app: INSERT theo cột + policy `xt.dispatch`/`xt`/priority 0/không lock; UPDATE chỉ `status` từ `todo` → `cancelled`; không UPDATE events; khóa ownership qua `xt_queue.lock_delivery` | `test_aq09_*`; hai test cũ đổi kỳ vọng từ “0 dòng” sang `InsufficientPrivilege` (chặt hơn) |
| AQ-10 | Đã sửa | `GuardedCursor` chặn `COMMIT/END/ABORT/ROLLBACK/PREPARE TRANSACTION` trước khi gửi; savepoint vẫn dùng được | `test_aq10_*` ×8 + unit phân tích câu lệnh ×20 |

Không đồng ý / khác đề xuất:

1. **AQ-02, không lùi `next_at` hay tắt lịch.** Lùi `next_at` làm mất occurrence; `active=false` làm lịch không tự hồi phục khi worker đúng phiên bản lên. Lịch chỉ bị worker *thiếu handler* bỏ qua; worker có handler vẫn chạy đúng giờ (catch-up như cũ). Tín hiệu là `last_error` + DLQ + bộ đếm.
2. **AQ-05, dùng role theo tenant thay vì chỉ `search_path`.** `search_path` không chặn tên schema tường minh (`xt_t2.effects`), nên chỉ ghim path là chưa đủ; quyền schema mới chặn được. Giới hạn đã biết: session_user vẫn là xt_app nên code handler cố ý có thể `SET ROLE xt_app`/`xt_workflow` (cả hai có USAGE mọi schema tenant). Biên này chặn lỗi chọn schema theo payload hoặc lỗi lập trình, không chặn handler độc hại. Handler là code tin cậy do operator nạp. `CHECK (tenant_id = '<uuid>')` của model engine vẫn nên thêm.
3. **AQ-06, mức log.** `basicConfig(DEBUG)` nằm ở `entrypoints/worker.py` (ngoài phạm vi được sửa). Thay bằng redaction trong adapter: chặt hơn đề xuất, vì INFO `Starting job …` của upstream cũng in payload. Việc chọn mức log qua env đã chuyển cho entrypoint.
4. **AQ-09, chỉ cấp `UPDATE(status)`, không cấp `abort_requested`.** Adapter gọi `cancel_job_by_id(abort=False)`; cấp thêm cột abort sẽ mở đường dừng job đang chạy mà chưa có use case.
5. **AQ-10, savepoint không giải quyết được.** `COMMIT` trong savepoint vẫn commit toàn bộ transaction ngoài, nên phải chặn ở cursor. Lối `connection.pgconn.exec_` vẫn còn (code tin cậy, đã ghi docs). Guard sau handler giữ lại làm lớp thứ hai.
6. **AQ-04, đã đổi theo review L6:** bản đầu đặt `lock_timeout` 90 s > `idle_in_transaction` 60 s để zombie bị ngắt trước. Reviewer chỉ ra 90 s > `statement_timeout` 30 s nên `lock_timeout` không bao giờ kích hoạt (statement_timeout cắt trước, lỗi mơ hồ). Nay mặc định 10 s và bắt buộc `lock_timeout < statement_timeout`; cái giá là delivery chờ zombie có thể đốt một attempt (retry có backoff).
7. **AQ-08, chưa có lệnh replay.** Replay có fencing cần thiết kế riêng; hiện replay bằng key mới (ghi docs).

### 6.1. Review nội bộ (8 phát hiện) và ngữ cảnh `xt.set_context`

Ngày 16/10/2026. Tất cả đã sửa, có test; không còn `xfail` trong `tests/integration/queue_audit`.

| # | Cách sửa | Test |
| --- | --- | --- |
| M1 | `_redact_payload` dùng regex `(xt\.dispatch\[\d*\]\()(?:.*\)\|.*)` (re.S): thay **toàn bộ** đối số, kể cả khi payload repr có dấu nháy/ngoặc lệch hoặc call string bị cắt; `exc_info`/`exc_text`/`stack_info` bị bỏ, chỉ nối `[TênLớpException]` | unit `test_redaction_regex_covers_the_whole_argument_list` ×5, `test_redaction_keeps_only_exception_class_of_chained_psycopg_errors`, `test_job_payload_is_redacted_*` |
| M2 | Ghi rõ (docstring `guard.py`, `docs/worker-queue.md`): role theo tenant là defense-in-depth, handler first-party. `GuardedCursor` chặn thêm `set_config('search_path', …)`, tên GUC không phải literal thường (`E'…'`, dollar-quote, biểu thức, `"set_config"`) và `xt.set_context(...)`; `SET/RESET ROLE`, `set_config('xt.%')` đã chặn | unit `test_session_context_detection` ×24; integration `test_m2_handler_cannot_change_pinned_role_or_tenant` ×6, `test_m2_workflow_elevation_remains_allowed` |
| L3 | Ngay sau `SET LOCAL ROLE xt_app` + `xt.set_context` hậu-handler: `SET LOCAL search_path TO pg_catalog, public` | `test_l3_bookkeeping_runs_with_repinned_role_tenant_and_search_path` (trigger ghi role/tenant/search_path/`xt.cur()` lúc đánh dấu completed; đã kiểm bỏ dòng sửa thì test fail) |
| L4 | E-string/dollar-quote chỉ nhận khi trước đó không phải `[\w$]` (`(?<![\w$])`) | unit: `SELECT 1 AS a$b$; COMMIT; SELECT 1 AS c$b$` và `a$$…c$$` → bị chặn |
| L5 | `maintenance()` chạy `recover_stalled` và `dispatch_periodic` trong try riêng | unit `test_maintenance_steps_fail_independently` |
| L6 | Mặc định `lock_timeout` 10 s; `QueueRuntime` từ chối `lock_timeout >= statement_timeout` | unit `test_session_options_pin_utc_and_timeouts_and_keep_dsn_options` |
| L7 | Lịch thiếu handler chỉ ghi `last_error` khi trễ quá `SCHEDULE_ERROR_GRACE_TICKS` (3) × `recover_seconds` | `test_aq02_failing_schedule_is_surfaced_in_dlq` (trễ 1 s → không ghi, `schedules_skipped=0`; trễ 1 phút → ghi) |
| L8 | `install_schema` kiểm owner `rolsuper OR rolbypassrls` trước khi tạo gì | `test_l8_install_schema_requires_rls_bypassing_owner` |
| Ngữ cảnh | Dispatcher, `_fire_schedule`, `_record_schedule_error` đặt tenant bằng `xt.set_context(tenant, NULL, …)` (SECURITY DEFINER, cách duy nhất model engine hỗ trợ, AUD-13) thay cho `set_config('xt.tenant_id')`; hàm này vẫn đặt GUC nên policy `xt_queue` không đổi. Startup kiểm xt_app có EXECUTE trên `xt.set_context(...)` | `test_worker_sets_model_engine_context_via_set_context` (handler thấy `xt.tenant()` = tenant job, role `xt_job_t1`), `test_worker_startup_requires_set_context`; fixture PG nay chạy `bootstrap()` thật |

Ảnh hưởng test: bootstrap thu hồi TEMP từ PUBLIC nên fixture audit cấp lại TEMP cho `xt_job`, `xt_workflow` (kịch bản xấu nhất của AQ-03); hai test AQ-03 đổi `set_config('search_path', …, false)` (nay bị guard chặn) sang `SET SESSION search_path`, vẫn chứng minh `DISCARD ALL` dọn được.

Kết quả 16/10/2026: unit worker 174 passed; `make test-worker-integration` 22 passed; `tests/integration/queue_audit` 34 passed. `make check-worker`: ruff/format/mypy xanh, `lint-imports` vỡ 1 hợp đồng (SpiffWorkflow/lxml qua `entrypoints/composition/*`, ngoài phạm vi queue); các bước còn lại (pytest, reuse) chạy tay đều xanh.

Ghi chú: `xt worker dlq` cần nối ở entrypoint (đoạn mã ở `docs/integration-notes.md`); trước khi nối, chạy `python -m xt.adapters.queue.dlq`. Biến env cho timeout/payload cũng do entrypoint nối. Schema đổi theo kiểu fresh-install: DB dev cũ phải cài lại.

## Vòng 2 (tester Opus, 16/10/2026)

Phạm vi: recover từng job (SKIP LOCKED), backoff/công bằng lịch (`failures/retry_at`, trần 10/tenant/tick), `xt worker dlq list|show|requeue|purge`, ngữ cảnh `xt.set_context` trên pool. Không sửa `src`. PG thật `postgres:17-alpine` (fixture `xt-audit-queue-*`, cổng 20100, `docker rm -f -v`). `free -g` available 6–7 GB trước khi chạy.

Fixture đã chạy lại được: `bootstrap.sql` không còn `AmbiguousFunction` trong `xt_audit.ddl_guard`, 3 test `test_queue_round2.py` trước đây chờ nay xanh.

### Số liệu

| Lệnh | Kết quả |
| --- | --- |
| unit worker (`WORKER_UNIT` + `tests/unit/test_cli.py`) | **185 passed** |
| `make test-worker-integration` | **22 passed** (41 s) |
| `pytest -m integration tests/integration/queue_audit` (trước khi thêm file mới) | **37 passed** (34 cũ + 3 round2), 4 lần chạy sạch liên tiếp |
| như trên, kèm `test_queue_round2_attack.py` (6 test mới) | **43 passed**, 2/2 lần |

Ghi chú hạ tầng: một session khác chạy cùng fixture trên cổng cố định 20100, nên nhiều lần chạy lỗi `docker run` exit 125 (trùng cổng). Một lần ra "1 failed, 36 passed" giữa lúc tranh cổng; 6 lần chạy sạch sau đó không tái hiện, nên ghi là nhiễu môi trường. Khuyến nghị: fixture lấy cổng ngẫu nhiên.

### Rà test vòng 1/2 có bị nới không

Không thấy nới. `test_aq03_tenant_a_then_b_*` vẫn kiểm cùng pid (pool size 1) và đủ 12 thuộc tính session. Việc đổi `set_config(...,false)` sang `SET SESSION search_path` là do guard chặn cách cũ, còn bất biến cần chứng minh (`DISCARD ALL`) vẫn giữ. Hai test AQ-09 chuyển từ "0 dòng" sang `InsufficientPrivilege`, tức chặt hơn. Test round2 recovery dùng zombie thật giữ `FOR UPDATE`. Hai chỗ còn thiếu, đã bù ở file mới: (a) requeue chưa kiểm delivery cũ bị dispatcher từ chối, chỉ mới kiểm requeue lần 2; (b) fairness mới thử 14 lịch, chưa vượt `LIMIT 100`.

### Bảng PASS/FAIL

| # | Hạng mục | KQ | Bằng chứng |
| --- | --- | --- | --- |
| R1 | Pool size 1: job A lỗi giữa tx trong savepoint, job A nuốt savepoint lỗi, rồi job B cùng pid | PASS | `test_r2_handler_errors_and_savepoints_*`: B thấy `xt.tenant()`=B, GUC=B, `xt_job_t2`, `xt_t2, pg_temp`, 0 dòng effects. Effect của job A lỗi đã rollback; job nuốt lỗi ghi đúng 1 dòng `observed_tenant=A` |
| R2 | Ngữ cảnh `session_ctx` khóa theo `(pid, xid)` sau commit | PASS (đọc code + R1) | `xt.cur()` so `pg_current_xact_id_if_assigned()`, nên dòng của tx A đã commit không khớp tx B |
| R3 | `lock_timeout` < `statement_timeout` khi tranh chấp trong handler | PASS | lock 1 s / statement 4 s, handler chờ dòng nghiệp vụ bị giữ: `LockNotAvailable` sau 0.8–3 s, 0 effect. Test cũ đã phủ khóa request |
| R4 | Log worker thật (`runtime.run`): payload có `)`, `(`, nháy, xuống dòng; message exception có PII; `DETAIL` UniqueViolation có PII | PASS | `test_r2_real_worker_logs_*`: log có `xt.dispatch[<id>]` nhưng không có marker nào, cả message lẫn `record.__dict__`. Đột biến (tắt `RedactJobPayload.filter`) thì test đỏ |
| R5 | Liveness recover: job bị khóa + job hết lượt có request bị khóa + job retry được, trong cùng 1 tick | PASS | tick đầu trả 1 trong < 1 s, `recover_errors=0`, hai job bị khóa vẫn `doing`, job còn lại `todo`. Nhả khóa, tick sau: `todo` / `failed` |
| R6 | Fairness: 130 lịch lỗi / 13 tenant (vượt `LIMIT 100`) + 1 lịch lành A + 1 lịch lành B | PASS (có ghi chú) | B bắn ở tick 1. Mọi lịch lỗi vào backoff với `failures=1`, không hot-loop. Trần backoff 3600 s. **Ghi chú:** trong cùng tenant, lịch *vừa* bắt đầu lỗi (`failures=0`, `next_at` cũ hơn) chiếm 10 slot trước lịch lành, nên lịch lành A trễ 1 tick (tổng quát là ceil(N/10) tick lúc khởi phát). Trễ có giới hạn, không phải starvation |
| R7 | DLQ requeue: delivery cũ bị từ chối | PASS | sau requeue, `dispatch(old_context)` báo `job id does not match`, 0 effect. ACK cũ không đổi job mới (`todo`) |
| R8 | DLQ purge id đã bị thay thế / job sống | PASS | purge id cũ chỉ xóa dòng job cũ, request vẫn trỏ job mới, `cancelled=false`. purge/requeue job `todo` trả `false`/`null` |
| R9 | GuardedCursor: các dạng câu lệnh vòng tránh | KHÔNG KIỂM VÒNG NÀY | ngoài phạm vi lượt này; cần review riêng. Theo docs (M2), guard là defense-in-depth, không phải sandbox |
| R10 | Extra log `exception=repr(error)` của `poll_jobs_to_abort` (procrastinate) không bị filter xóa | QUAN SÁT (Low) | chỉ đọc code: lỗi control-plane, không mang payload. Nên xóa key `exception` trong `RedactJobPayload` cho đồng bộ |

### Test mới

`tests/integration/queue_audit/test_queue_round2_attack.py` có 6 test, đều PASS nên không có `xfail`.

### Verdict vòng 2

**ĐẠT.** Ba tính năng vòng 2 (recover từng job, backoff/công bằng lịch, `dlq requeue|purge`) đúng trên PG17 thật và không có hồi quy (185 / 22 / 43). Còn mở: R6 (trễ khởi phát trong tenant, có giới hạn, chấp nhận được), R10 (Low), R9 (chưa kiểm), và cổng fixture cố định gây nhiễu khi chạy song song.
