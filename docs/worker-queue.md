<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Job và timer cho xt-worker

Ngày kiểm chứng: 15/10/2026. Nền: **procrastinate 3.10.0**, được chọn theo `research/spike-queue.md`; dùng thư viện nguyên bản, không vendor. [Metadata PyPI](https://pypi.org/pypi/procrastinate/3.10.0/json) xác nhận MIT; [croniter 6.2.4](https://pypi.org/pypi/croniter/6.2.4/json) cũng MIT. Các phụ thuộc trực tiếp/gián tiếp mới có dòng trong `THIRD_PARTY_NOTICES.md`.

## Ranh giới và cách ghép vào nghiệp vụ

- `application/jobs.py`: `JobQueue` Protocol và `JobRequest` bất biến, payload là snapshot JSON; application không import driver/thư viện queue.
- `adapters/queue/`: adapter duy nhất dùng procrastinate, SQL và pool riêng của queue. Đã thêm vào contract import-linter **Các adapter độc lập với nhau**; không import `adapters/db`, workflow hoặc identity.
- `entrypoints/cli.py`: composition root cho console `xt`, giữ CLI doctor/version hiện có và thêm `xt worker`. `entrypoints/worker.py` nạp factory handler do người vận hành chỉ định.

Đây là ngoại lệ hẹp đối với câu “chỉ adapters/db import psycopg” ở architecture-v2 §4.2: queue tự dùng psycopg cho bảng của mình và quản lý transaction tenant, đúng phạm vi yêu cầu tích hợp adapter queue; không nhập ngược adapter DB. Không sửa code model engine/DB/IAM/identity/Desk của các agent khác.

`JobQueue` có `enqueue(transaction, job)`, `schedule_at(transaction, job, when)`, `cancel(transaction, tenant_id=..., idempotency_key=...)`, `periodic(transaction, job, cron=..., timezone=...)`. Adapter nhận **chính psycopg AsyncConnection của transaction nghiệp vụ**. Kết nối phải `autocommit=True`, đang trong `connection.transaction()`, role hiện hành `xt_app` và ngữ cảnh tenant khớp `job.tenant_id` (đặt bằng `xt.set_context`, hàm này cũng đặt GUC `xt.tenant_id` mà policy `xt_queue` đọc); sai thì báo lỗi. Adapter không commit hay mở transaction nghiệp vụ thay caller.

```python
from datetime import UTC, datetime
from uuid import UUID
from xt.application.jobs import JobRequest

# connection là connection của UnitOfWork, autocommit=True.
async with connection.transaction():
    await connection.execute("SET LOCAL ROLE xt_app")
    # Cách được hỗ trợ (model engine AUD-13): SECURITY DEFINER, chỉ xt_app.
    await connection.execute(
        "SELECT xt.set_context(%s::uuid, NULL, '{}'::text[], '{}'::uuid[],"
        " '{}'::uuid[], NULL, NULL, NULL)",
        (str(tenant_id),),
    )
    # Ghi nghiệp vụ + audit/outbox bằng connection này trước/sau enqueue.
    timer = JobRequest(
        UUID(str(tenant_id)), "workflow.sla", f"sla:{task_id}:{revision}",
        {"task_id": str(task_id), "revision": revision},
    )
    job_id = await queue.schedule_at(
        connection, timer, datetime(2026, 10, 19, 1, tzinfo=UTC)
    )
```

Không có handler nghiệp vụ giả mặc định. Factory tin cậy của sản phẩm trả mapping `{task_name: async_handler}`. Handler nhận `(connection, job: JobRequest)` và phải kiểm lại IAM/quyền, revision và trạng thái task trước khi tạo hiệu ứng. Timer hoàn tất sớm thì mutation hoàn tất task và `cancel(...)` nằm trong cùng transaction. Nếu cancel thắng trước khi handler lấy khóa, handler sẽ bỏ qua; nếu handler đã hoàn tất, cancel không đảo ngược hiệu ứng đã commit. Luôn khóa/kiểm tra trạng thái nghiệp vụ để quyết định leo thang SLA.

Pool tenant đăng nhập **xt_app NOSUPERUSER NOBYPASSRLS**, `autocommit=True`, kiểm IDLE trước khi mở transaction. Dispatcher đặt `SET LOCAL ROLE xt_app` + `xt.set_context(tenant, NULL, …)` (cách đặt ngữ cảnh duy nhất được model engine hỗ trợ: ghi `xt_meta.session_ctx` cho RLS `xt.cur()` của bảng `xt_t<seq>.*`, đồng thời đặt GUC `xt.tenant_id` transaction-local cho policy `xt_queue`/`procrastinate_jobs`), khóa request, rồi tra **(schema, role) của tenant từ catalog tin cậy** `xt_queue.job_binding(tenant_id)` (AQ-05) và chạy handler với `SET LOCAL ROLE <job role>` + `SET LOCAL search_path TO xt_t<seq>, pg_temp`. Job role là `NOLOGIN NOSUPERUSER NOBYPASSRLS`, chỉ có USAGE trên đúng schema của tenant; hàm binding từ chối role có USAGE trên schema tenant khác hoặc tenant chưa có binding (fail closed, job vào DLQ). Vì vậy handler lấy nhầm schema từ payload sẽ bị `permission denied`, không ghi chéo được. Không chuyển vai trò từ payload. Chỉ đường workflow transition của adapter workflow được nâng `SET LOCAL ROLE xt_workflow` theo §5.3 (membership tính theo session_user xt_app). Sau handler, dispatcher đặt lại `SET LOCAL ROLE xt_app`, gọi lại `xt.set_context` và **ngay sau đó** `SET LOCAL search_path TO pg_catalog, public` (L3) trước khi kiểm ownership và đánh dấu `completed`; `search_path`/role mà handler đổi không lọt vào bookkeeping. Worker kiểm lúc khởi động rằng xt_app có EXECUTE trên `xt.set_context(...)`, thiếu thì từ chối chạy. Handler cần đọc `xt.cur()`/`xt.tenant()` thì provisioning phải cấp EXECUTE các hàm đó (và USAGE schema `xt`) cho nhóm `xt_job`.

Role theo tenant là **defense-in-depth, không phải sandbox** (M2). Handler là code first-party do operator nạp: role `xt_job_t<seq>` + `GuardedCursor` chặn lỗi lập trình (lấy schema từ payload, quên LOCAL), không chặn handler cố ý độc hại (SQL động trong `DO`/`EXECUTE`, `connection.pgconn`). Khi handler chạy, `GuardedCursor` từ chối trước khi gửi: `SET [SESSION|LOCAL] ROLE` (trừ `SET LOCAL ROLE xt_workflow`), `RESET ROLE`/`RESET ALL`, `SET SESSION AUTHORIZATION`, `SET … xt.*`, `set_config('xt.%'|'role'|'session_authorization'|'search_path', …)`, mọi `set_config` có tên GUC không phải literal thường (tham số, `E'…'`, dollar-quote, biểu thức) và `xt.set_context(...)`; lỗi `SessionControlError`, job retry. `SET LOCAL search_path` vẫn được phép (đã được L3 ghim lại sau handler). Handler gọi `enqueue/schedule_at/cancel/periodic` trên chính connection: adapter tạm chuyển về xt_app + `search_path=public` rồi trả lại role/path của handler. Job A/B không dùng chung transaction; bảng thật vẫn là `xt_t<seq>.<model>` do model engine quản lý, có FORCE RLS và FK tenant. Adapter queue không tạo bảng model hay role tenant; provisioning ghi binding (xem `docs/integration-notes.md`).

Hợp đồng handler (AQ-10): transaction thuộc dispatcher. Cursor của pool tenant (`GuardedCursor`) từ chối mọi câu `COMMIT`/`END`/`ABORT`/`ROLLBACK` (trừ `ROLLBACK TO SAVEPOINT`)/`PREPARE TRANSACTION`, kể cả trong chuỗi nhiều câu, **trước khi gửi tới server**: lỗi `TransactionControlError`, toàn bộ hiệu ứng rollback, job retry. Savepoint (`connection.transaction()` lồng, `SAVEPOINT`/`ROLLBACK TO`) vẫn dùng được. `CALL`/`DO` có COMMIT bị PostgreSQL chặn vì đang trong transaction block. Bộ tách câu bỏ qua comment, string thường, `E'…'` và dollar-quote; dollar-quote/E-string chỉ được nhận khi ký tự trước không phải chữ/số/`_`/`$` (`(?<![\w$])`, L4), nên `SELECT 1 AS a$b$; COMMIT; SELECT 1 AS c$b$` vẫn bị chặn. Lối tắt `connection.pgconn` không được guard; handler là code tin cậy do operator nạp.

Trạng thái session (AQ-03): pool tenant chạy `DISCARD ALL` mỗi lần trả connection (RESET ALL gồm search_path/role/GUC `xt.*`, temp table, prepared statement, LISTEN, advisory lock session). Connection lấy ra luôn là mới hoặc đã reset. TimeZone và timeout đặt bằng startup option nên vẫn giữ sau reset.

## Idempotency, retry và recovery

`xt_queue.requests` giữ khóa `(tenant_id, idempotency_key)`, fingerprint của loại tác vụ/payload/instant và envelope hash riêng. Replay cùng nội dung trả cùng `job_id` cả sau khi thành công; đổi nội dung hoặc giờ hẹn báo lỗi. JSON được canonicalize cho fingerprint; thời gian được chuẩn hóa UTC. Khóa `periodic:` dành riêng cho lần phát sinh từ lịch; caller không được chiếm namespace này. Advisory transaction lock giữ cùng namespace giữa job một lần và đăng ký periodic: không thể đăng ký hai loại bằng cùng key. FK occurrence–schedule ghép `(tenant_id, schedule_id)`, chặn liên kết lịch của tenant khác.

Handler khóa request `FOR UPDATE`, ràng `job_id` của delivery với chính request đó, rồi kiểm envelope hash. Không thể đổi sang key của request khác dù payload giống hệt. Dấu `completed` commit cùng mutation nghiệp vụ. Hai lần giao cùng key chỉ tạo một hiệu ứng DB; chết sau business commit nhưng trước ACK thì lần giao lại thấy `completed` và bỏ qua. Bảng request không tự hết hạn: muốn dọn phải đặt chính sách retention phù hợp cửa sổ replay, không xóa chỉ vì job đã succeeded.

Hàng đợi upstream `public.procrastinate_jobs/events` được bật **ENABLE + FORCE RLS**, policy tenant cho xt_app, policy control cho xt_control. Không có tenant thì xt_app không thấy dòng; xt_control không được đọc bảng nghiệp vụ. `xt_queue.requests/schedules` cũng FORCE RLS; control chỉ SELECT danh sách schedule. Job luôn mang `tenant_id`, task name, idempotency key và payload. Chỉ truyền định danh/tham số nhỏ; tránh PII và secret, vì queue control plane nhìn được envelope. Quyền của xt_app trên `procrastinate_jobs` bị thu hẹp theo cột (AQ-09): INSERT chỉ các cột defer, policy INSERT bắt `task_name='xt.dispatch' AND queue_name='xt' AND priority=0` không lock; UPDATE chỉ cột `status`, chỉ từ `todo` sang `cancelled` (đúng đường cancel). xt_app không đổi được priority/args/attempts, không đánh dấu `succeeded`, không UPDATE `procrastinate_events` (trigger chỉ INSERT). Khóa ownership sau handler đi qua hàm `xt_queue.lock_delivery` (SECURITY DEFINER, kiểm tenant GUC).

`XT_WORKER_MAX_ATTEMPTS=N` nghĩa là **tối đa N lần chạy tổng cộng**, gồm lần đầu. Đây khác `RetryStrategy(max_attempts=N)` upstream (N retry + lần đầu). Backoff là `min(3600, XT_WORKER_RETRY_SECONDS * 2**attempts)` giây; recover dùng cùng trần/backoff. Khi hết trần, trạng thái là `failed` — dead-letter giữ lại trong bảng cùng `procrastinate_events`; không giả thành công, không tự xóa.

### Dead-letter (AQ-08)

Hết lượt (handler lỗi ở lần cuối, `retry_owned` chạm trần, hoặc recover stalled hết lượt) thì `xt_queue.mark_dead` ghi `xt_queue.requests.dead_at` và `last_error`. `last_error` chỉ là **tên lớp exception** (ví dụ `RuntimeError`) hoặc `stalled: attempts exhausted`; không lưu message vì message driver/handler có thể chứa dữ liệu. Lịch không chạy được ghi `xt_queue.schedules.last_error/last_error_at`.

```sh
XT_QUEUE_DATABASE_URL=... xt worker dlq list --limit 20        # job failed + lịch có lỗi (JSON)
XT_QUEUE_DATABASE_URL=... xt worker dlq show 123               # (= inspect) job, request, events; payload chỉ key + kích thước
XT_QUEUE_DATABASE_URL=... xt worker dlq requeue 123 124        # defer job MỚI cho cùng request, ngân sách attempts mới
XT_QUEUE_DATABASE_URL=... xt worker dlq purge 123              # bỏ dead letter: request cancelled, xóa job + events
XT_QUEUE_DATABASE_URL=... xt worker dlq purge --older-than-days 30
```

`xt worker dlq` được `entrypoints/worker.py` chuyển thẳng cho `xt.adapters.queue.dlq.main` (cũng chạy được bằng `python -m xt.adapters.queue.dlq`). Chỉ cần login xt_control; rc 2 = cấu hình/tham số, 1 = lỗi vận hành (chỉ in tên lớp lỗi). `requeue`/`purge` gọi `xt_queue.requeue_dead(job_id)`/`purge_dead(job_id)` (SECURITY DEFINER, chỉ xt_control EXECUTE), mỗi job một transaction. **Requeue** chỉ nhận job `failed` có request chưa `completed`/`cancelled`: tạo job mới (cùng `args`, nên envelope hash khớp), trỏ `requests.job_id` sang job mới, xóa `dead_at/last_error`. Delivery còn sót của job cũ bị dispatcher từ chối vì `job_id` không khớp request (fencing theo job id), và job cũ không còn hiện trong `list`. **Purge** chỉ nhận job `failed`: request thành `cancelled` (replay cùng key là no-op, giữ idempotency), job và events bị xóa.

Truy vấn tương đương: `SELECT j.id, j.args->>'tenant_id', r.dead_at, r.last_error FROM procrastinate_jobs j JOIN xt_queue.requests r ON r.job_id=j.id WHERE j.status='failed'`. Replay: sửa nguyên nhân rồi `xt worker dlq requeue <job_id>`; enqueue lại cùng key không replay (trả về job failed cũ theo thiết kế idempotency). `QueueRuntime.stats` có bộ đếm `schedules_skipped`, `schedule_errors`, `maintenance_errors`, `recover_errors`, `stalled_dead_lettered` cho exporter metric.

Một coroutine maintenance chạy định kỳ ngoài hàng đợi để recover vẫn sống ngay cả khi concurrency=1 đang bận. Nó đọc danh sách job `doing` của worker hết heartbeat (không khóa, tối đa `RECOVER_BATCH`=100/tick), rồi xử lý **từng job trong transaction riêng**: khóa lại job `FOR UPDATE SKIP LOCKED`, kiểm lại điều kiện, rồi retry bằng hàm upstream hoặc dead-letter. Nhánh dead-letter khóa request `FOR UPDATE SKIP LOCKED`; request đang bị handler (zombie) giữ thì bỏ qua job đó, tick sau thử lại. Một job lỗi/bị khóa/hết lượt chỉ rollback chính nó (đếm `recover_errors`), không kéo cả lô. Worker mới không được retry vô hạn job độc. Lịch maintenance không phải job thiếu tenant; mọi job trong queue `xt` đều là `xt.dispatch` có tenant.

Procrastinate 3.10 không có fencing đủ ở ACK/retry by-id. `FencedJobManager` bọc hai phương thức public `finish_job(job)` / `retry_job(job)`; hàm SQL `xt_queue.finish_owned/retry_owned` khóa dòng rồi so `worker_id` và `attempts` của delivery. ACK cũ không hoàn tất delivery mới; retry cũ không hồi sinh failed. Trước handler có kiểm generation; sau handler kiểm lại bằng `FOR UPDATE` đến commit. Mất ownership thì rollback toàn bộ hiệu ứng DB. Không giữ khóa queue suốt handler, nên recover vẫn tiến được.

Bảo đảm này áp dụng cho **hiệu ứng trong transaction DB**. Gửi webhook/mail không rollback được: handler phải ghi outbox cùng transaction và receiver phải nhận idempotency key. Công việc CPU/sync dài cần chạy ngoài event loop để heartbeat không bị nghẽn. Handler còn sống và vẫn gửi heartbeat sẽ không bị coi là stalled; deadline cho service task phải do handler/nghiệp vụ đặt. Mốc stalled trong test là 6 giây; production phải chọn theo độ trễ/heartbeat quan sát được, ví dụ 30 giây.

## Periodic và timezone

`periodic(..., cron="0 8 * * 1", timezone="Asia/Ho_Chi_Minh")` lưu cron gốc và cron UTC **`0 1 * * 1`**. Next-fire kiểm chứng từ `2026-10-15T12:00Z` là **`2026-10-19T01:00:00Z` = thứ Hai 08:00 Việt Nam**; test tính thời điểm, không chờ đồng hồ đến thứ Hai thật.

Bộ chuyển chỉ nhận 5 trường, phút/giờ số cố định, ngày-tháng và tháng là `*`, thứ là `*` hoặc danh sách số (0/7 Chủ nhật). Có xử lý lệch phút và đổi thứ khi qua nửa đêm. DST, offset tương lai thay đổi, offset có giây và biểu thức không thể biểu diễn an toàn đều **báo lỗi**, không đoán offset hiện tại.

Bằng chứng offset tương lai cố định lấy từ transition 64-bit + POSIX footer của TZif v2 trở lên ([RFC 9636 §3](https://www.rfc-editor.org/rfc/rfc9636.html#section-3)), đối chiếu offset hiện tại và mọi transition tương lai trong chính dữ liệu dùng chuyển đổi. Không chỉ lấy mẫu vài tháng. Ngữ nghĩa là từ lúc đăng ký trở đi theo bản tzdata đã cài; không áp vào lịch sử. Sau cập nhật tzdata phải rà soát và đăng ký lại lịch với key/version mới. Không thể dự đoán luật múi giờ tương lai.

Maintenance lấy schedule đến hạn, rồi mở **một transaction tenant riêng** để khóa schedule `SKIP LOCKED`, enqueue một occurrence và tăng `next_at` nguyên tử. Hai worker không tạo hai occurrence cho cùng thời điểm. Catch-up mỗi schedule một lần/tick, tối đa `SCHEDULES_PER_TICK`=100 schedule/tick và `SCHEDULES_PER_TENANT_PER_TICK`=10 mỗi tenant (`row_number() OVER (PARTITION BY tenant_id …)`), lịch lành (`failures=0`) xếp trước; không bỏ qua các thời điểm đã lỡ khi downtime. Lịch materialize lỗi (exception) bị **backoff có trần**: `failures+1`, `retry_at = now() + min(3600, recover_seconds × 2^failures)` giây, `last_error` = tên lớp lỗi; lịch đang backoff không được chọn nên không chiếm `LIMIT`, không hot-loop. Lần thành công kế tiếp xóa `failures/retry_at/last_error` và tiến `next_at` như thường (không mất occurrence). `xt worker dlq list` hiện `failures`, `retry_at`. Hủy lịch cũng đánh dấu các occurrence đã tạo còn pending/đang chờ khóa request; không chỉ ngừng các lần tương lai.

Next-fire luôn tính theo UTC (AQ-01): `croniter(cron_utc, next_at.astimezone(UTC))`, đồng thời mọi connection worker có startup option `TimeZone=UTC`, nên `PGTZ`, `ALTER ROLE/DATABASE ... SET timezone` hay `postgresql.conf` không làm lệch lịch.

Lịch độc (AQ-02): worker chỉ materialize lịch có handler trong chính worker đó. Lịch có `task_name` không có handler (lệch phiên bản, handler bị gỡ) **giữ nguyên `next_at`** để worker có handler xử lý, được ghi `last_error='no registered job handler: <task>'`, log ERROR một lần và đếm `schedules_skipped`, nhưng **chỉ khi đã trễ quá `SCHEDULE_ERROR_GRACE_TICKS` (3) × `recover_seconds`** (L7): khi rolling deploy, worker có handler thường bắn lịch trước, nên lịch vừa đến hạn không bị ghi lỗi giả. Lỗi khi materialize một lịch được bắt riêng từng lịch (ghi `last_error` = tên lớp exception, đếm `schedule_errors`), các lịch/tenant khác vẫn chạy. Mỗi bước maintenance (`recover_stalled`, `dispatch_periodic`) chạy trong try riêng (L5): recovery lỗi không chặn lịch và ngược lại. Vòng maintenance bắt lỗi thoáng qua, log, đếm `maintenance_errors` và thử lại ở tick sau; task maintenance không bao giờ tự kết thúc nên không kéo worker chết.

## Cấu hình và cài schema

Mọi biến vận hành dưới đây bắt buộc, không lấy DSN chung hay tự chuyển sang queue in-memory:

| Biến | Ý nghĩa |
| --- | --- |
| `XT_QUEUE_DATABASE_URL` | URL PostgreSQL login **xt_control**, chỉ control plane |
| `XT_WORKER_DATABASE_URL` | URL cùng database, login **xt_app**, phần tenant |
| `XT_WORKER_CONCURRENCY` | Số job đồng thời, số nguyên ≥1 |
| `XT_WORKER_STALLED_SECONDS` | Hết heartbeat sau số giây này, ≥3 |
| `XT_WORKER_RECOVER_SECONDS` | Chu kỳ maintenance, ≥1 và nhỏ hơn stalled/2 |
| `XT_WORKER_MAX_ATTEMPTS` | Tổng số lần chạy tối đa, ≥1 |
| `XT_WORKER_RETRY_SECONDS` | Backoff ban đầu, ≥1 |
| `XT_WORKER_HANDLERS` | Danh sách `module:factory` phân cách bằng dấu phẩy; factory trả dict không rỗng, handler async `(connection, job)` |
| `XT_QUEUE_MIGRATOR_DATABASE_URL` | Chỉ bắt buộc khi `--install-schema`; credential DDL riêng, không xt_app/xt_control |

Tham số runtime có **mặc định đã ghi rõ** (truyền tường minh qua `create_runtime(...)`; biến env tương ứng do entrypoint nối, xem `docs/integration-notes.md`). Không có giá trị ngầm nào khác:

| Tham số `create_runtime` | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `statement_timeout_seconds` | 30 | `statement_timeout` của mọi connection worker (control + tenant) |
| `lock_timeout_seconds` | 10 | `lock_timeout`; delivery chờ khóa của zombie sẽ lỗi rồi retry thay vì treo vô hạn. **Phải nhỏ hơn `statement_timeout`** (L6), không thì `ValueError` |
| `idle_in_transaction_timeout_seconds` | 60 | `idle_in_transaction_session_timeout`; server ngắt session zombie (SIGSTOP/GC pause/VM freeze) đang giữ khóa |
| `max_payload_bytes` | 65536 | Trần payload JSON (UTF-8) cho `enqueue/schedule_at/periodic`; vượt thì `ValueError` |

Giá trị phải ≥1 và `lock_timeout < statement_timeout` (L6): chờ khóa luôn báo `LockNotAvailable` (rõ nguyên nhân) thay vì bị `statement_timeout` cắt ngang mơ hồ. Zombie giữ khóa lâu hơn `lock_timeout` thì delivery chờ đốt một attempt và retry với backoff; `idle_in_transaction_timeout` vẫn để server ngắt zombie và nhả khóa. Hệ quả cho handler: không chờ I/O ngoài DB lâu hơn `idle_in_transaction_timeout` trong transaction, không chạy câu lệnh dài hơn `statement_timeout`. Các option được nối vào `options` có sẵn trong DSN (startup option, ưu tiên hơn `ALTER ROLE/DATABASE SET`).

Ví dụ giá trị vận hành có thể dùng: concurrency=4, stalled=30, recover=5, max_attempts=3, retry=2. URL chứa mật khẩu phải đến từ secret manager/Compose secret; tài liệu không cung cấp tài khoản hay mật khẩu dùng chung. Factories là Python được operator tin cậy, không nhận từ agent/người dùng nghiệp vụ.

Provision role trước bằng bootstrap quản trị: xt_control và xt_app LOGIN NOSUPERUSER NOBYPASSRLS; xt_app không sở hữu bảng. Cấp CONNECT database cho xt_control (bootstrap model hiện có thu hồi CONNECT từ PUBLIC), và giữ xt_workflow NOLOGIN với membership `INHERIT FALSE, SET TRUE` chỉ cho xt_app. Credential DDL phải có quyền CREATE schema trong database, CREATE + quyền cấp USAGE trên `public`, cùng quyền grant cho các role trên. Có thể dùng bootstrap superuser cho lần cài đầu; nếu dùng xt_migrator thì DBA phải cấp rõ CREATE/USAGE trên public và các quyền grant cần thiết. Không dùng credential DDL để chạy worker. Worker đối chiếu database/server-start identity thực tế của hai kết nối để từ chối DSN trỏ hai database khác nhau. Mỗi tenant thêm một job role `xt_job_t<seq>` NOLOGIN NOBYPASSRLS (thành viên nhóm `xt_job`, nhóm này được liệt kê trong policy tenant), `GRANT xt_job_t<seq> TO xt_app WITH INHERIT FALSE, SET TRUE`, USAGE + DML chỉ trên `xt_t<seq>`, và một dòng `xt_queue.tenant_bindings(tenant_id, schema_name, job_role)` do installer/migrator ghi. Thiếu binding thì job của tenant đó fail closed.

```sh
uv sync
# Đặt tất cả env bắt buộc ở trên qua môi trường/secret của deployment.
xt worker --install-schema
xt worker --name worker-01
```

`--install-schema` chạy schema upstream pin 3.10 + schema/policy/fencing xT trong một transaction, cài mới rõ ràng; credential DDL phải là SUPERUSER hoặc BYPASSRLS (L8), vì hàm SECURITY DEFINER `job_binding`/`lock_delivery` đọc bảng FORCE RLS bằng quyền chủ sở hữu, sai thì lỗi trước khi tạo gì; không chạy ngầm khi startup. Đã tồn tại thì lỗi thay vì dùng IF NOT EXISTS che drift. Lần nâng phiên bản sau phải review migration upstream và SQL xT có phiên bản; không chạy lại fresh installer trên DB đang có dữ liệu. Upstream procrastinate log nguyên kwargs của job (`call_string`, extra `job`/`jobs`); adapter gắn filter `RedactJobPayload` vào mọi logger `procrastinate*` (kể cả logger con theo tên worker) nên toàn bộ danh sách đối số của mọi `xt.dispatch[<id>](...)` bị thay bằng `[redacted]` bằng regex (tham lam tới `)` cuối, hoặc tới hết dòng nếu call string bị cắt), extra `task_kwargs` bị xóa, và traceback/`exc_info` chỉ còn **tên lớp exception** (không message handler, không `DETAIL` của psycopg) ở mọi level (AQ-06, M1). Mức log do entrypoint quyết định. CLI lỗi cấu hình chỉ tên biến; runtime báo loại lỗi và hướng xử lý, không in raw DSN/exception chứa secret.

## Kiểm chứng thật

Môi trường: Linux, Python 3.13.14, psycopg 3.3.6, procrastinate 3.10.0, croniter 6.2.4, image cục bộ `postgres:17-alpine`. Test kiểm `df -h /` trước Docker; không pull image. Lúc bắt đầu: `/dev/nvme0n1p7 55G 45G 7.4G 86% /`. Postgres dùng `127.0.0.1:19800`, role thật, bảng nghiệp vụ `xt_t1/xt_t2`, FORCE RLS. Fixture dùng mật khẩu random và `docker rm -f -v` trong finally. Không đụng display thật, không commit.

Các lệnh tái chạy:

```sh
make check-worker
make test-worker-integration
make check
```

Unit bao gồm port/input, defensive JSON snapshot, fingerprint tenant/content/instant, tổng attempt/backoff, timezone sai/DST/weekday rollover, transition giả ở năm 2400, tương đương lịch 400 năm, cấu hình thiếu và CLI/plugin thật.

Integration bao gồm commit/rollback enqueue + business/request/job; dedupe khác tenant và replay sau completed; không tenant/role sai; tenant A/B và truy cập chéo schema; queue/events RLS; worker subprocess `xt worker`; `kill -9` giữa handler rồi worker mới recover; lỗi handler rollback + retry đến failed; hủy SLA cùng completion và rollback cancellation; cron thứ Hai HCM + materialization; cancel occurrence đã materialize; hai dispatch song song; payload bị sửa; ACK/retry cũ; budget bền; mất ownership trước commit; prefix occurrence dành riêng, namespace one-shot/periodic và FK schedule chéo tenant.

Output thật của lần xác minh cuối:

Lần chạy 16/10/2026 (sau review nội bộ M1–L8 và chuyển sang `xt.set_context`). Fixture PG nay chạy `bootstrap()` thật của model engine trước schema queue.

```text
$ make check-worker
All checks passed!                          # ruff check
Success: no issues found in 13 source files # mypy strict
Contracts: 3 kept, 1 broken.                # import-linter: hợp đồng SpiffWorkflow/lxml
                                            # vỡ ở entrypoints/composition (ngoài queue)
$ uv run pytest <WORKER_UNIT> tests/unit/test_cli.py --no-cov
174 passed
$ uv run reuse lint-file ... docs/worker-queue.md   # exit 0

$ make test-worker-integration
22 passed in 33.64s

$ uv run pytest -m integration tests/integration/queue_audit --no-cov
34 passed in 11.10s
```

Vòng audit 2 (recover từng job, backoff/công bằng lịch, `dlq requeue|purge`): unit worker **185 passed**; integration mới ở `tests/integration/queue_audit/test_queue_round2.py` (3 test) **đang chờ** vì fixture lỗi `AmbiguousFunction: text || "char"` trong `xt_audit.ddl_guard()` của `bootstrap.sql` (model engine đang sửa). Chạy lại suite queue_audit khi bootstrap ổn định.

`make check-worker` kiểm riêng phần này, không thay thế kiểm tra toàn repo. Lần chạy `make check` cùng thời điểm dừng ở Ruff: **45 lỗi ngoài queue**, trong `application/model_engine/service.py`, `application/tools/dispatcher.py`, các test model/process mà agent khác đang bổ sung. Không sửa hoặc loại trừ các file đó. `reuse lint` toàn repo ở lần kiểm trước cũng quét `desk/node_modules` chưa được ignore và báo thiếu copyright/license; target riêng dùng `reuse lint-file` cho toàn bộ file queue và tài liệu này, không thay đổi target REUSE toàn repo. Vì vậy **không khẳng định make check toàn repo xanh**.

Sau test, `docker ps -a --filter name=xt-it-worker --format '{{.Names}}'` không có dòng nào; fixture xóa cả anonymous volume bằng `docker rm -f -v`. Không pull hay xóa image PostgreSQL có sẵn. `git diff --check` không báo lỗi. Các file Python mới đều có hai header SPDX; tài liệu có header SPDX dạng HTML comment.

Phản biện đã xử lý: reviewer phát hiện cancel chỉ dừng lịch tương lai, envelope có thể bị thay đổi, raw queue chưa RLS, ACK/retry upstream chỉ theo id có thể hồi sinh dead-letter. Các lỗi đó đều có test hồi quy trên PG và đã sửa bằng fanout cancel, envelope hash, FORCE RLS và fencing generation. Opus review spike chỉ ra thêm cần giữ dấu completed cùng effect; adapter đã làm đúng transaction đó. Lượt audit CLI Opus cuối timeout 240 giây, output rỗng; task được giao lại Sonnet, chạy 106 unit queue thành công và kiểm tra CAS/post-handler lock, trong khi test PG tiếp tục độc lập. Không dùng kết luận của reviewer thay output integration thật.

## Bài học từ nguồn tham khảo

Đã đọc spike và pattern từ REA (`SetupRegistrationEnvironment.ts`: cấu hình rõ ràng không phụ thuộc trạng thái readiness) cùng sklite (`internal/queue/queue.go`, `internal/fulfillment/outbox/processor.go`). Không sao chép code sklite vì không có LICENSE. Bẫy outbox của sklite nhả khóa trước enqueue rồi đánh dấu processed ở transaction khác được tránh bằng port nhận transaction của caller; bẫy Complete/Fail chỉ theo job id được tránh bằng fencing worker/generation. Đây là hạ tầng job/timer cho các handler sản phẩm; không tự cài logic quy trình hay gửi thông báo giả.
