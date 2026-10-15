<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Ghi chú tích hợp

## Process engine (`xt.application.process`, `xt.adapters.workflow.bpmn`)

Chi tiết: `docs/process-engine.md`. API công khai giữ nguyên từ bản port đầu; các thay đổi sau audit (`docs/audit-process.md`) đều là bổ sung tương thích ngược:

| Thay đổi | Ảnh hưởng tới lớp tích hợp |
|---|---|
| `InputSpec.default` (mới, tùy chọn) | Spec cũ vẫn hợp lệ, trừ spec dùng input tùy chọn trong biểu thức mà không có `default` (nay bị `SpecError`) |
| Op tiền tố `xt_` dành riêng (`xt_sla_deadline` do engine tự xử lý) | Không đăng ký op deployment nào bắt đầu bằng `xt_` |
| BPMN có thêm service `slas_X` và boundary trên `ri_ut_X`; boundary dùng `timeDate` | Instance tạo bằng BPMN cũ không nạp được bằng định nghĩa compile lại (đối chiếu digest spec, `reason="spec_mismatch"`): giữ bản compile cũ trong catalog theo `(process_id, version)` |
| `SpiffProcessEngine.load` ném `EngineError(reason="spec_mismatch")` khi `wf` không khớp BPMN | Ánh xạ sang `internal_error` |
| `ProcessService.act/reassign/tick`: khi `EngineError`, record (meta có `incidents`) **vẫn được lưu** trước khi ném lại | `InstanceStore.save` phải chạy trong cùng transaction của use case; transaction vẫn commit phần meta này, rồi trả lỗi cho tool |
| `ProcessService._load/get` kiểm `meta.id == instance_id` | Lệch -> `EngineError(reason="state_mismatch")` |
| `ProcessService.get` trả thêm khóa `incidents` | Có thể hiển thị cho quản trị |
| Sự kiện mới `engine.error`; `assignment.escalated/unresolved` có thể phát lại khi mọi ứng viên nghỉ | Outbox/relay nhận thêm loại sự kiện |
| Quản trị (`admin_role`) được `act(task, admin, "withdraw", lý do)` trên bước sửa `unresolved` | IAM của tool `decide_request` cần cho phép đường này với vai trò quản trị |

Việc còn để lại cho tích hợp (từ audit):
- **I-3**: `TimerPoller` ghi `wf_instance(id, process_key, version, state)`; cần gộp với `InstanceStore` (status, candidates, next_due) để instance tạo bằng timer hiện trong inbox.
- **I-4**: `rt.start` trong poller gọi `on_event` và `scheduler.wake_at` trước khi transaction commit; khi nối thật, `EventSink`/`TimerScheduler` nên ghi outbox trong cùng transaction rồi relay sau commit.
- `TimerScheduler.wake_at(process_id, instance_id, due)`: dedupe theo `(instance_id, due)`; id quy trình/bước giới hạn 41 ký tự nên khóa job luôn ngắn. Job gọi `ProcessService.tick(instance_id)`.

## Job queue (`xt.adapters.queue`) sau audit AQ-01…AQ-10

Ngày: 15/10/2026. Phạm vi: `src/xt/adapters/queue/*`. Không sửa `src/xt/entrypoints/` hay `src/xt/composition/` (codex đang tích hợp). Mục dưới đây là những thay đổi mà phía entrypoint/composition/DB cần biết hoặc cần nối.

### 1. API công khai: tương thích ngược

- `create_runtime(...)` / `QueueRuntime(...)`: thêm 4 tham số **keyword, tùy chọn, có mặc định đã ghi trong `docs/worker-queue.md`**: `statement_timeout_seconds=30`, `lock_timeout_seconds=10` (phải < statement), `idle_in_transaction_timeout_seconds=60`, `max_payload_bytes=65536`. Lời gọi hiện có trong `entrypoints/worker.py` vẫn chạy nguyên trạng.
- `ProcrastinateJobQueue(app, task_names, *, max_payload_bytes=65536)`: tham số mới tùy chọn. `JobQueue` Protocol và `JobRequest` (`application/jobs.py`) **không đổi**.
- Mới: `QueueRuntime.stats` (dict bộ đếm), `QueueRuntime.job_manager`, `runtime.TransactionControlError`, module `xt.adapters.queue.dlq` (`list_dead`, `inspect_dead`, `main`).
- Hàm SQL nội bộ đổi chữ ký (fresh install): `xt_queue.finish_owned(..., reason text)`, `xt_queue.retry_owned(..., reason text)`; thêm `mark_dead`, `lock_delivery`, `job_binding`, bảng `xt_queue.tenant_bindings`, cột `requests.dead_at/last_error`, `schedules.last_error/last_error_at`. **DB dev đã cài schema cũ phải cài lại** (`xt worker --install-schema` trên DB mới); chưa có migration vì chưa có DB production.

### 2. Thay đổi hành vi handler (quan trọng cho workflow/SLA)

- Handler **không còn chạy với `current_user = xt_app`** mà với job role của tenant (`xt_job_t<seq>`), `search_path = xt_t<seq>, pg_temp`. Mọi đối tượng handler cần (hàm `xt.*`, bảng `xt_meta`, bảng workflow, …) phải được GRANT cho nhóm `xt_job` (hoặc cho job role), và policy RLS của bảng tenant phải gồm `TO xt_app, xt_job` (hoặc PUBLIC + RESTRICTIVE).
- Dispatcher đặt ngữ cảnh bằng `xt.set_context(tenant, NULL, …)` (không còn `set_config('xt.tenant_id')`); worker từ chối khởi động nếu xt_app không EXECUTE được hàm này. Handler muốn đọc `xt.cur()`/`xt.tenant()` cần provisioning cấp `USAGE ON SCHEMA xt` + `EXECUTE ON FUNCTION xt.cur(), xt.tenant()` cho nhóm `xt_job`.
- Handler không được đổi role/tenant/search_path qua `SET/RESET ROLE`, `set_config('xt.%'|'role'|'search_path')`, `xt.set_context` (bị `GuardedCursor` chặn); `SET LOCAL search_path` vẫn được, dispatcher ghim lại sau handler.
- `SET LOCAL ROLE xt_workflow` trong handler vẫn hợp lệ (membership xét theo session_user xt_app).
- Handler gọi `queue.enqueue/schedule_at/cancel/periodic(connection, ...)` trên connection của chính nó vẫn được: adapter tự chuyển tạm về xt_app rồi trả lại role/search_path.
- Handler không được chạy `COMMIT/END/ABORT/ROLLBACK/PREPARE TRANSACTION` (bị chặn trước khi gửi, job retry). Savepoint vẫn dùng được.
- Không giữ transaction idle quá `idle_in_transaction_timeout` (60 s) hay chạy câu lệnh quá `statement_timeout` (30 s) mặc định.
- Payload > 64 KiB bị từ chối khi enqueue.

### 3. Việc cần nối ở entrypoint (codex)

1. **Env cho timeout/payload** (AQ-04/AQ-07), trong `WorkerConfig.from_env` rồi truyền vào `create_runtime`:
   `XT_WORKER_STATEMENT_TIMEOUT_SECONDS`, `XT_WORKER_LOCK_TIMEOUT_SECONDS`, `XT_WORKER_IDLE_IN_TRANSACTION_TIMEOUT_SECONDS`, `XT_QUEUE_MAX_PAYLOAD_BYTES` (số nguyên ≥1). Đề xuất: bắt buộc như các biến `XT_WORKER_*` khác để giữ nguyên tắc “không default ngầm”; giá trị gợi ý = mặc định ở trên.
2. **`xt worker dlq`** (AQ-08): **đã nối** (16/10/2026) trong `entrypoints/worker.py main(argv)`: `list|show|requeue|purge`. Đoạn gốc:
   ```python
   if argv and argv[0] == "dlq":
       from xt.adapters.queue.dlq import main as dlq_main
       return dlq_main(argv[1:])
   ```
   Lệnh chỉ cần `XT_QUEUE_DATABASE_URL` (login xt_control); rc 2 = cấu hình, 1 = lỗi vận hành, không in DSN. Trước khi nối: `python -m xt.adapters.queue.dlq list|inspect <job_id>`.
3. **Mức log** (AQ-06): `logging.basicConfig(level=logging.DEBUG)` nên lấy từ env (ví dụ `XT_LOG_LEVEL`). Payload đã được adapter redact ở mọi level, nên đây chỉ còn là chuyện độ ồn log.

### 4. Việc cần nối ở provisioning tenant (agent DB / model engine)

Mỗi `provision_tenant` (chạy bằng role có CREATEROLE: bootstrap/DBA, vì xt_migrator không có CREATEROLE):

```sql
CREATE ROLE xt_job NOLOGIN NOBYPASSRLS;                         -- một lần, ở bootstrap
CREATE ROLE xt_job_t<seq> NOLOGIN NOBYPASSRLS IN ROLE xt_job;
GRANT xt_job_t<seq> TO xt_app WITH INHERIT FALSE, SET TRUE;
GRANT USAGE ON SCHEMA xt_t<seq> TO xt_job_t<seq>;
-- model engine: GRANT DML trên từng bảng xt_t<seq>.* cho xt_job_t<seq>; policy TO xt_app, xt_job
INSERT INTO xt_queue.tenant_bindings(tenant_id, schema_name, job_role)
VALUES ('<uuid>', 'xt_t<seq>', 'xt_job_t<seq>');
```

`drop_tenant` xóa dòng binding và `DROP ROLE xt_job_t<seq>`. Không được cấp cho nhóm `xt_job` USAGE trên bất kỳ `xt_t*`: `xt_queue.job_binding` sẽ từ chối mọi job của tenant đó. Khuyến nghị thêm của audit vẫn giữ: model engine thêm `CHECK (tenant_id = '<uuid>')` trên bảng `xt_t<seq>.*`.

### 5. Kiểm chứng

`make check-worker`, `make test-worker-integration`, `uv run pytest -m integration tests/integration/queue_audit --no-cov`; kết quả ở `docs/audit-queue.md` §6.

## MCP / tool pipeline (`xt.application.tools`), sau audit `docs/audit-mcp.md`

Thay đổi hợp đồng port (**breaking**, adapter thật phải theo):

| Port | Hợp đồng mới |
|---|---|
| `OperationStore.claim(key, fingerprint, now)` (mới) | Gọi **bên trong** `TransactionRunner.run`, trước khi plan/commit. Insert-or-get theo UNIQUE(tenant, actor, tool, idempotency_key): chưa có -> chèn hàng `in_progress` và trả `None` (người gọi giữ claim tới hết transaction); có receipt/PendingOperation đã commit -> trả nó; hàng `in_progress` của transaction khác -> **chờ** (Postgres: INSERT chặn trên unique index tới khi transaction kia commit/rollback) rồi trả kết quả; chờ quá `lock_timeout` -> trả `IN_PROGRESS` (dispatcher trả `rate_limited`, `details.reason="idempotency_in_progress"`). Rollback transaction = nhả claim |
| `OperationStore.save_receipt(receipt)` | Hoàn tất hàng đã claim trong **cùng transaction** với mutation; không còn ngữ nghĩa "đã có thì trả bản cũ". Trả `None` |
| `OperationStore.save_pending(op)` | Chuyển hàng đã claim thành PendingOperation, cùng transaction |
| `OperationStore.lock_pending(confirmation_id)` (mới) | `SELECT … FOR UPDATE` trong transaction của `ConfirmationService`; thay `get_pending` ở đường thực thi |
| `PiiLane.scrub_error(ctx, tool, error) -> dict` (mới, bắt buộc) | Quét sâu mọi chuỗi của object `error` (message, details, plan_summary, continuation) theo lớp client trước khi trả agent; `plan_summary` thật vẫn lưu trong PendingOperation cho Desk |
| `TransactionRunner.run(fn)` | Exception trong `fn` -> rollback (mutation + claim). Dispatcher chạy cả claim -> plan -> gate -> commit -> receipt trong một lần `run` |
| Dữ liệu trả về từ port | Được chuẩn hóa: `Decimal` nguyên -> chuỗi số nguyên (tiền VND, như JCS của identity), `Decimal` lẻ/float NaN -> `internal_error`; `date` -> `YYYY-MM-DD`; `datetime` có tz -> RFC 3339 UTC `Z` (naive -> `internal_error`); `UUID` -> chuỗi. Port nên tự trả kiểu JSON gốc |
| `ConfirmationService.confirm` | Kiểm lại `AccessGuard.check` cho **cả** principal agent gốc và người xác nhận (actor `human`, client `XT_CONFIRM_CLIENT_ID`) lúc thực thi |

Quyết định M6 (chủ dự án): bề mặt MCP **không bao giờ** nhận principal `human`. Token `kind=human` (client trong `XT_MCP_HUMAN_CLIENTS`) bị 401 ở `/mcp`. Desk chat dùng client agent (`xt-desk-agent`: `kind=agent`, `on_behalf_of=user`), đặt vào `XT_MCP_LOCAL_CLIENTS` (tier `local` -> PII `full`). Chỉ trang `/desk/confirm` là người, và chỉ đi qua `ConfirmationService`. Trong dispatcher, actor người cũng không được làm L2+ trực tiếp (luôn qua xác nhận).

## Model engine (`xt.adapters.db`, `xt.application.model_engine`) sau audit `docs/audit-model-engine.md`

| Thay đổi | Ảnh hưởng tới lớp tích hợp |
|---|---|
| **Ngữ cảnh RBAC (AUD-13, quyết định chủ dự án)**: policy RLS không đọc GUC `xt.*` nữa. Ngữ cảnh đặt qua `SELECT xt.set_context(tenant, actor, roles[], teams[], depts[], effective_actor, azp, trace)` (SECURITY DEFINER, chỉ `xt_app` EXECUTE), lưu ở `xt_meta.session_ctx` khóa theo `(pg_backend_pid(), pg_current_xact_id())` → hết hiệu lực khi transaction kết thúc | Mọi đường ghi/đọc bảng `xt_t<seq>.*` phải dùng `Database.tenant_tx` hoặc `set_tenant_context(cur, actor)`. `GUC_SQL` vẫn export (cùng 8 tham số chuỗi) nhưng nay gọi `xt.set_context` → `adapters/db/confirmations.py` không phải sửa. `xt.set_context` vẫn đặt GUC `xt.tenant_id` để tương thích policy hàng đợi (`xt_queue`) |
| Login BI: `bind_bi_login(m, login, tenant, *, roles=(), actor_id=None, team_ids=(), department_ids=())` — role/phạm vi cố định theo `session_user`; cấm tên `xt_*` | Không có role = không thấy gì. `xt.login_tenant()` đã bỏ, dùng `xt.cur()` |
| PostgREST (`xt_web`): không được gọi `xt.set_context` | Pre-request `xt.pgrst_pre` (chưa làm) phải là SECURITY DEFINER đọc claim JWT đã verify rồi ghi `session_ctx`; hiện view `api_t*` qua `xt_web` trả 0 dòng (fail-closed) |
| **Khóa HMAC audit (AUD-07/15)**: DB chỉ giữ `sha256(khóa)` (`xt_audit.pepper(kid, fingerprint, active)`); `xt_audit.set_pepper` đã bỏ. Khóa đưa vào từng transaction ghi qua GUC `xt.audit_key` | Composition phải tạo `AuditKey(kid, key)` từ secret và truyền `Database(..., audit_key=key)` (xt_app) và `PgMigrator(..., audit_key=key)` (backfill). Thiếu/sai khóa → ghi field sensitive bị từ chối. Xoay khóa: `rotate_audit_key(superuser_conn, kid, key)` rồi đổi cấu hình. `bootstrap(..., pepper=...)` giữ chữ ký, chỉ lưu dấu vân tay |
| Pha 2 có loại job `backfill` (theo lô 1.000 dòng, `apply_job.progress`), dry-run/diễn tập không bao giờ sửa dữ liệu (AUD-02) | xt-worker phải gọi định kỳ `PgMigrator.run_jobs()`; lúc khởi động gọi `PgMigrator.reconcile()` |
| Version mới nhất `post_state='failed'` → `apply` trả `precondition_failed` (`details.reason="post_failed"`) (AUD-06) | Quản trị sửa dữ liệu rồi `PgMigrator.retry_failed(tenant)`; `ModelService.propose` thêm finding `blocker`. Port `Migrator` thêm `post_state(tenant)` |
| `ModelService.rollout(..., allow=frozenset({SAFE}), confirm=frozenset())` — mặc định chỉ tự áp SAFE, không tự xác nhận PHÁ HỦY (INFO-2) | Muốn áp RISKY phải truyền `allow` tường minh; mã PHÁ HỦY phải nằm trong `confirm` |
| Đề xuất cũ khi catalog đã đổi → `revision_conflict` (AUD-08); `xt_workflow` không đổi được `created_by` (AUD-09) | — |
| Spec: trần 1 MiB JSON, 3.000 field/catalog; nhãn cấm ký tự Cc/Cf/Zl/Zp; regex chỉ escape ký tự đặc biệt, cấm lượng từ lồng | Gói hiện có vẫn hợp lệ |
| DB dev đã bootstrap bản cũ phải bootstrap lại (chưa có migration nền tảng) | — |

## PostgREST `/data` — API tích hợp (16/10/2026)

Đã kiểm bằng 28 test PostgreSQL17 + PostgREST16.4 thật; không suy ra e2e workflow/tunnel đã xanh. Quyết định: docs/decisions.md PG-1…PG-7.

| API | Hợp đồng |
|---|---|
| `bootstrap_sql()` | SQL gốc + sql/postgrest.sql; cài mới trong transaction bootstrap |
| `install_postgrest_role(superuser, *, authenticator_password, issuer, clients)` | Cài/nâng cấp idempotent; đặt password, issuer và thay allowlist data-client; reload config + schema. DB cũ phải gọi rõ |
| `xt.backend_cur()` | Clone **nguyên thân** cur gốc; private, cùng owner/search_path. Không rename hàm gốc |
| `xt.cur()` | Wrapper giữ OID/ACL. Ngoài session xt_authenticator gọi lại backend_cur; session data đọc ctx local. Login BI có từ trước không mất quyền |
| `xt.set_context(8 tham số)` | Giữ nguyên thân/chữ ký/quyền đã audit |
| `xt.set_context(jsonb)` | Overload private mới, chỉ gate do migrator sở hữu gọi được khi session_user là authenticator. Đặt ctx local, không ghi session_ctx và không đặt GUC tenant của queue |
| `xt.pgrst_pre()` | SECURITY DEFINER, chỉ tên đủ schema; lấy profile từ search_path thô và role từ GUC do PostgREST đặt. Không dùng current_schema dưới quyền migrator. Gọi gate private với search_path cố định |
| `xt.pgrst_config()` | Tính schema từ tenant + pg_namespace; chỉ authenticator EXECUTE |
| `notify_postgrest(conn)` | Cùng transaction DDL: reload config + schema. Đã kiểm rollback không phát, commit phát cả hai |

Không có migration 0004. Các tên cấu hình thật: user xt_authenticator; role xt_web; schema nền xt_api_none; `.data_role`; không anon-role; aud xt-data; JWKS @file; extra-search-path rỗng. APISIX /data chỉ GET/HEAD, strip prefix và giữ Authorization/Accept-Profile. `XT_DATA_CLIENTS` của Keycloak và `clients` trong installer phải khớp; rỗng là fail-closed. Vai trò lấy từ person active trong xT, không từ JWT. Phòng ban = person.unit; team_ids rỗng. sensitive bị bỏ khỏi view; basic theo chính sách hiện có vẫn nguyên văn.

Thân backend_cur và setter gốc được so với bootstrap.sql trong integration; kiểm nâng cấp DB cũ cùng container, reinstall giữ OID/ACL và BI có từ trước. Wrapper mất backend thì cài lại RAISE; core vừa đổi được clone lại, không quay lui logic cũ. Credentials authenticator là ranh giới tin cậy: giữ ngoài repo, hạn chế pg_hba/mạng; GUC không bảo vệ được khi credentials đó bị chiếm.

## Desk ↔ /pii/reveal (16/10/2026, `decisions.md` "Tier PII của Desk chat")

Desk (`desk/src/pii.ts`) dựa vào hợp đồng sau (khớp `research/spike-pii-lane/xtpii/{reveal,server}.py`). Server composition phải đáp ứng; Desk test bằng mock (`desk/test/pii.test.ts`, `desk/e2e/mock-pii.mjs`).

1. **Token**: `⟨MÃ.trường⟩` khớp `[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.[a-z][a-z0-9_]{0,63}`. Desk gom token duy nhất trong `structuredContent ?? content` của kết quả tool, tối đa 50.
2. **Xin vé**: tool MCP `pii_reveal_ticket(tokens: string[], pubkey_jwk: JWK EC P-256 công khai)`, `_meta.ui.visibility=["app"]` (không bao giờ vào tool list của LLM), gọi bằng token Bearer của người dùng như mọi tool. Server kiểm mọi token thuộc phiên (`tenant|azp|sub|sid`), trả vé ngẫu nhiên, dùng 1 lần, TTL 60 s, gắn principal + tập token + khóa. Vé **chỉ** nằm ở `result._meta["xt/reveal"] = {ticket, url}`, không trong `content`/`structuredContent`. Lỗi: `isError=true` (vd `PII_TOKEN_UNKNOWN`).
3. **Đổi vé**: `POST {DESK_XT_ORIGIN}/pii/reveal`, `Content-Type: application/json`, body `{ticket: string, ts: int (giây Unix), sig: base64url(ECDSA-P256-SHA256 P1363 64 byte trên UTF-8 "{ticket}.{ts}")}`. Không cookie, không `Authorization` (`credentials: omit`, `Referrer-Policy: no-referrer`). Desk chỉ gửi tới `DESK_XT_ORIGIN`; nếu `_meta.url` khác URL đó, Desk bỏ vé.
4. **Server phải**: CORS cho đúng origin Desk (preflight `OPTIONS` 204, `Access-Control-Allow-Headers: content-type`; `/pii/*` tự xử lý CORS, không để middleware toàn cục chặn); Origin Desk nằm trong allowlist; đánh dấu vé đã dùng nguyên tử; lệch `ts` <= 30 s; kiểm chữ ký bằng khóa đã đăng ký; kiểm quyền theo `reveal_roles`; `Cache-Control: no-store`; audit `tier=human`.
5. **Response 200**: `{"values": {token: string}, "denied": [token]}`. Lỗi: 403 `{"error": "TICKET_INVALID"|"BAD_ORIGIN"|...}`, 400 `{"error": "BAD_REQUEST"}`. Desk chỉ đọc `error` làm mã lỗi hiển thị, không hiện thông điệp tự do.
6. **Phía Desk**: mỗi lần bấm "Hiện" là khóa + vé mới, không retry/cache vé; giá trị chỉ ở state component (không history LLM, không log, không storage), xóa khi điều hướng/tải lại/60 s. Tool `pii_reveal_ticket` vẫn gọi được từ MCP App qua bridge (không nằm trong `APP_DENYLIST`) vì thẻ App tự xem là kênh người; model không thấy tool này.
7. **Việc của server agent**: hiện `.env.example`/`compose.yaml` còn `XT_MCP_LOCAL_CLIENTS=xt-desk-agent` (tier local cho client public); theo quyết định phải bỏ. Origin Desk phải có trong allowlist của `/pii/reveal`.

### MCP vòng 2 (audit R2, 16/10/2026)

**Hợp đồng khóa của `OperationStore` (R2-M3). Adapter Postgres `src/xt/adapters/db/confirmations.py` phải theo:**

1. `claim(key)`:
   - Chưa có hàng: `INSERT … ON CONFLICT DO NOTHING`.
   - Đã có hàng: **`SELECT … FOR UPDATE`**, để chờ transaction đang giữ `lock_pending` của `ConfirmationService` rồi mới đọc. Kết quả sau khi chờ là receipt.
   - Không được trả bản `pending` đã cũ.
2. `update_pending(op) -> bool`:
   - `UPDATE … WHERE pending_status IN ('pending','awaiting_second_approver') AND receipt IS NULL`, ngoại lệ duy nhất là chuyển sang `confirmed` trong cùng transaction với receipt.
   - Trả `rowcount == 1`.
   - Không bao giờ ghi đè hàng đã đóng hoặc đã có receipt.
3. `save_receipt` của `ConfirmationService` chạy trong transaction đang giữ `lock_pending`.
4. **Contract test bắt buộc:** `tests/unit/tools/operation_store_contract.py`.
   - Bản in-memory chạy ở `tests/unit/tools/test_operation_store_contract.py`.
   - Bản Postgres phải gọi `run_operation_store_contract(lambda: (PgOperationStore(...), PgTransactionRunner(...)))` (hoặc parametrize theo `CHECKS`) trong `tests/integration`, dùng ≥ 2 connection thật.
   - Ca `claim_waits_for_confirmation_lock` chính là kịch bản race của R2-M3.

**Lưới PII cho lỗi (R2-M1/M2):**
- Dùng `xt.application.tools.pii_scrub.ErrorScrubber(mask, pii_fields=…, known_values=…)`. `PostgresPiiLane.scrub_error` đã chuyển sang dùng nó, với `pii_fields` lấy từ catalog (field `pii != none`).
- `continuation` không bao giờ được đưa vào `scrub_error`: dispatcher tách nó ra trước, vì đó là args do agent tự gửi.
- Tiền, revision và các khóa số nghiệp vụ được giữ nguyên.
- Nếu làn có danh sách giá trị PII đã đọc trong lời gọi thì truyền vào `known_values`.

**Tier PII (R2-M4, `docs/decisions.md`):**
- `xt serve` từ chối cấu hình khi `XT_MCP_LOCAL_CLIENTS` chứa client không nằm trong `XT_MCP_CONFIDENTIAL_CLIENTS` (client confidential phía server), không thuộc `XT_MCP_ALLOWED_CLIENTS`, hoặc là client người. Chỉ được nới khi đặt `XT_MCP_DEV_LOCAL_TIER=true` (profile dev).
- **`deploy/compose.yaml` và `deploy/.env.example` đang đặt `XT_MCP_LOCAL_CLIENTS=xt-desk-agent`, phải bỏ** (`xt-desk-agent` là client public, nên chỉ được tier masked), nếu không server sẽ dừng lúc khởi động.
- `ConfirmPages` nên truyền `XT_CONFIRM_CLIENT_ID` vào `ConfirmationService(dispatcher, confirm_client_id=…)` (R2-I1).

### API cài schema nền cho data (16/10/2026)

`xt.adapters.db.data_setup.install_data_prerequisites(superuser) -> list[str]` là đường cài đặt quản trị tường minh, được `deploy/scripts/configure_data.py` (`make -C deploy pgrst-db`) gọi trước `install_postgrest_role`. Nó gọi **migration nguyên bản** bằng `current_user=xt_migrator`, trong một transaction trên cùng kết nối superuser đang IDLE/autocommit. Chỉ khi migrator chưa có EXECUTE `xt_audit.log()` mới cấp tạm, rồi thu hồi trước commit; session khác không quan sát được quyền tạm. Advisory lock `xt:migrate` giữ thứ tự; lỗi rollback toàn bộ DDL/ledger/quyền. Ownership của đối tượng vẫn là xt_migrator. Test so ACL audit trước/sau cài đặt.

Lý do: bootstrap audit đang được gia cố đã thu quyền hàm trigger khỏi migrator, nhưng migration nền 0001/0003 còn `CREATE TRIGGER ... xt_audit.log`. Không sửa nội bộ module audit/migration để tích hợp `/data`, không cấp quyền audit thường trực. API này **không sửa** hợp đồng tên trigger của metadata cũ; e2e workflow/metadata audit vẫn thuộc integrator/audit. Không suy kết quả `/data` thành toàn bộ nền tảng đã xanh. Kết nối superuser chỉ dùng cho cài đặt, không đưa vào PostgREST/runtime.

### Model engine vòng 2 (16/10/2026, audit "Vòng 2" + quyết định `xt_migrator` với audit)

| Thay đổi | Ảnh hưởng tới lớp tích hợp |
|---|---|
| **Khóa HMAC không bao giờ vào Postgres.** `Database(..., audit_key=AuditKey(kid, key))` giữ nguyên chữ ký, nhưng không còn đặt GUC `xt.audit_key`. `PgRecordRepository` tự tính HMAC trong tiến trình và chỉ đặt `xt.audit_hmac` ở transaction có ghi field sensitive (giá trị cũ đọc `FOR UPDATE` cùng transaction) | Mã nào ghi bảng `xt_t<seq>.*` có field sensitive mà không qua `PgRecordRepository`/`RecordService` phải tự gọi `set_audit_hmacs(cur, sensitive_hmacs(key, kid, cols, new=…, old=…))` (`xt.domain.model.audit`), nếu không trigger từ chối. `set_audit_key` đã bỏ. `Database.tenant_tx(..., audit_hmacs=None)` có thêm tham số tùy chọn |
| `PgMigrator(..., audit_key=…)`: chỉ dùng để cấp HMAC cho backfill field sensitive (lưu ở `apply_job.audit_hmac`) | Thiếu khóa mà có backfill field sensitive → `precondition_failed` (`audit_key_missing`) |
| **Audit thuộc `xt_audit_owner`.** `xt_audit.log()`/`append()` không còn GRANT cho `xt_migrator`/`xt_app`. Trigger cài bằng `SELECT xt_audit.install(schema, bảng, cột_sensitive[])`, và chỉ nhận schema `xt_t<seq>`. Event trigger chặn DISABLE/DROP trigger audit, trigger lạ và RULE trên `xt_t<seq>.*` | Migration nền tảng **không được** `CREATE TRIGGER … xt_audit.log` trên bảng `xt_meta.*` (sẽ `permission denied`). Audit cho `xt_meta.*`, nếu cần, phải thêm hàm riêng của `xt_audit_owner` (mục I-AUD-META còn mở) |
| Neo ngoài DB: `xt audit checkpoint` / `xt audit verify --against-checkpoint latest\|<tệp>` (`src/xt/entrypoints/audit_cli.py`, đã nối trong `entrypoints/cli.py`) | Env: `XT_DB_APP_URL`, `XT_AUDIT_CHECKPOINT_DIR` (ngoài Postgres, WORM), `XT_AUDIT_SIGNING_KEY_FILE` + `XT_AUDIT_SIGNING_KEY_ID` (Ed25519 riêng, chỉ ở máy chạy checkpoint), `XT_AUDIT_VERIFY_KEY_FILE` (khóa công khai). xt-worker gọi `checkpoint` định kỳ (đề xuất mỗi giờ). API: `take_checkpoint/sign_checkpoint/write_checkpoint/load_checkpoint/verify_against_checkpoint` trong `xt.adapters.db.audit` |
| `apply` từ chối khi còn BẤT KỲ job pha 2 nào chưa `done` (mọi version): `details.reason = post_pending \| post_failed` | Sau mỗi `apply` phải chạy `run_jobs` trước khi áp version kế tiếp. `pack install` nhiều bước phải chờ worker |
| `run_jobs` claim theo tenant (`pg_try_advisory_lock`), nhiều worker chạy song song an toàn | — |
| Regex spec: cấm `[` lồng, `&&`/`--`/`~~` trong lớp ký tự, `.`, lặp nhóm có lượng từ (kể cả có cận) | Gói dùng `.` phải viết lớp tường minh |
| **Deploy Postgres** (agent deploy áp, bắt buộc): xem `docs/model-engine.md` §8 | `ALTER SYSTEM SET log_statement='none'; log_parameter_max_length=0; log_parameter_max_length_on_error=0; log_error_verbosity='terse'; log_min_duration_statement=-1; log_min_duration_sample=-1; log_transaction_sample_rate=0;` không nạp `auto_explain`; `ALTER ROLE xt_app/xt_migrator SET` 4 tham số `log_statement='none'`, `log_parameter_max_length=0`, `log_parameter_max_length_on_error=0`, `log_error_verbosity='terse'` |
| DB dev phải bootstrap lại (bootstrap.sql đổi) | — |

## Tích hợp (composition root), 16/10/2026

Phạm vi: `src/xt/entrypoints/composition/*`, `src/xt/entrypoints/{serve,admin}.py`, `src/xt/adapters/db/{integration,confirmations}.py`, migration `0001/0003/0004`. Kết quả chạy thật: `docs/integration.md`.

| Thay đổi | Lý do / ai cần biết |
|---|---|
| `serve.prepare(env, args) -> Prepared` (+ `Prepared.http_app()`) | `main()` và test e2e dùng cùng một đường nối. `http_app()` ghép route phụ của composition (`/desk/confirm/*`) vào app MCP bằng `Starlette(routes=[*extra, *app.routes], lifespan=app.router.lifespan_context)`; **không** sửa `adapters/mcp/http_app.py` |
| `PgOperationStore.claim`: hàng đã có được đọc `SELECT … FOR UPDATE` trong cùng savepoint có `lock_timeout` | Hợp đồng R2-M3: chờ `ConfirmationService.lock_pending` commit rồi mới thấy receipt; quá hạn -> `IN_PROGRESS` |
| `PgOperationStore.update_pending -> bool`: `WHERE pending_status IN ('pending','awaiting_second_approver') AND (receipt IS NULL OR status mới = 'confirmed')` | Không bao giờ đè trạng thái đã đóng; khớp `tests/unit/tools/operation_store_contract.py` (chạy trên Postgres thật, 2 connection: `test_pg_operation_store_contract`) |
| `PgUnitOfWork.audit_key` (property, ủy quyền `Database.audit_key`) | `PgRecordRepository._hmacs` (R2-01) đọc `self.db.audit_key`; repository nhận UoW thay cho `Database` |
| `ConfirmPages` truyền `XT_CONFIRM_CLIENT_ID` vào `ConfirmationService(dispatcher, client_id)` | Trước đó dùng mặc định cứng `"xt-desk"` |
| Migration `0004_confirmation_session_lock.sql`: `GRANT UPDATE ON xt_meta.confirmation_session TO xt_app` | `PgConfirmationSessionStore.get` dùng `FOR UPDATE` (state dùng một lần) |
| **I-AUD-META**: bỏ `CREATE TRIGGER … xt_audit.log()` trên bảng `xt_meta.*` khỏi 0001/0003 (fresh install) | Bootstrap mới (R2-04): `xt_audit.log` chỉ nhận trigger tên `xt_audit` trên schema tenant `xt_t<seq>` và migrator không còn EXECUTE. **Cần từ model engine**: API audit cho bảng metadata có `tenant_id` (vd `xt_audit.install_meta(tbl name, cols text[])` SECURITY DEFINER, cho `iam_policy`, `process_definition`, `pack_install`, `process_event`, `pii_egress`, `tool_operation(pending, receipt → HMAC)`). Trong lúc chờ: các bảng này vẫn ghi người đề xuất/duyệt + thời điểm nhưng chưa vào hash-chain. `data_setup.install_data_prerequisites` (cấp tạm EXECUTE) không còn cần cho 0001/0003 |
| `xt org import` nhận tệp HR (`seed/binh-minh/org.json`) + `--links` + `--base-roles` | `entrypoints/composition/org_seed.py`; decisions D-INT-3 |
| `xt tenant bind-queue --tenant <alias>` (`XT_DB_OWNER_URL` + biến `xt worker`) | Mục "Job queue" §4: tạo `xt_job_t<seq>`, GRANT, `xt_queue.tenant_bindings`; khởi động chuỗi relay. `adapters/db/integration.bind_queue_tenant` |
| Relay hẹn giờ: job `workflow.relay_timers` tự hẹn lần sau bằng `schedule_at` (khóa `workflow.relay:<mốc>`), chu kỳ `XT_WORKFLOW_RELAY_SECONDS` (bắt buộc, ≥ 5) | `JobQueue.periodic` chỉ nhận cron phút/giờ cố định (`cron_to_utc`), không có "mỗi phút". Nếu chuỗi chết (DLQ) thì chạy lại `bind-queue` (idempotent) |
| `submit_request.route` lấy biến thật của instance (`ProcessInstance.data()`, gồm kết quả bước dịch vụ) | Bước `chief_acct_overdue` (điều kiện `chk_overdue_result`) không còn bị hiển thị sai |
| PII: tier `local` chỉ cho client trong `XT_MCP_CONFIDENTIAL_CLIENTS` (validation ở `serve`); `xt-desk-agent` = masked | decisions "Tier PII của Desk chat". `/pii/reveal` (kênh người) **chưa có** trong 17 tool v0.1: theo `research/spike-pii-lane.md` cần tool app-only `pii_reveal_ticket` + route; để v0.2 |
| Đọc bản ghi tenant khác: `access_denied` của PEP, giống hệt id không tồn tại | decisions D-INT-4 |
| Import-linter: contract SpiffWorkflow/lxml thêm `allow_indirect_imports` | decisions D-INT-1; composition chỉ import `xt.adapters.workflow.bpmn.spiff` |

Deploy (`deploy/compose.yaml`) cần thêm cho `xt-worker`: `XT_WORKER_HANDLERS=xt.entrypoints.composition.worker:handlers`, `XT_WORKFLOW_RELAY_SECONDS`, `XT_DB_MIGRATOR_URL`, `XT_AUDIT_KEY_FILE/ID`; và một bước một lần `xt tenant bind-queue --tenant <alias>` (cần `XT_DB_OWNER_URL`) sau khi tạo tenant.

Còn mở cho tích hợp: I-3 (instance do `TimerPoller` tạo chưa vào inbox: chưa có quy trình `trigger=schedule` nào được bind trong gói v0.1), I-AUD-META ở trên, `/pii/reveal`.

## MCP Apps (`ui://xt/*`), 16/10/2026

Phạm vi: `src/xt/adapters/mcp/apps/`, `xt.contracts.tools.apps`, `PersonDetails` trong `ports.py`. Không sửa `src/xt/entrypoints/composition`; integrator cần nối:

1. Hiện thực cổng `xt.application.tools.ports.PersonDetails.person_details(ctx, ref) -> Data | None` (một phần tử dạng `people[]` của `find_people` + `direct_reports: [{ref,name}]`; email/điện thoại là giá trị thật, làn PII che). Gợi ý: thêm vào adapter thư mục đang dùng cho `Directory` (`composition/security.py`).
2. Khi dựng `XtMcpServer` (cả HTTP lẫn stdio), gọi:

```python
from xt.adapters.mcp import register_apps

apps = register_apps(xt_origin=<XT_DESK_URL>, dispatcher=dispatcher, people=<PersonDetails>)
XtMcpServer(..., ui_resources=apps.ui_resources, app_tools=apps.app_tools)
```

   `xt_origin` phải cùng origin với `confirm_base_url` (`XT_DESK_URL`) và `DESK_XT_ORIGIN` của Desk; `register_apps` ném `ValueError` nếu không phải origin hợp lệ.
3. Catalog đổi tên view: `ui://xt/org-chart` thành `ui://xt/orgchart`, `ui://xt/request-card` thành `ui://xt/approval-card`; `search_records` nay có `ui://xt/record-table` (đã cập nhật `tool-contracts-v1.md`). Digest catalog đổi theo.
4. Tool app-only (`org_person_details`, `records_table_page`) không nằm trong `CATALOG` và không đổi số 17 tool; chúng được liệt kê trong `tools/list` chỉ khi view tương ứng đã đăng ký. Ngân sách token của core-10 không đổi khi chưa gọi `register_apps`.
5. IAM: thêm quyền `person.read` cho `org_person_details` (đã dùng action đầu của contract) và `record.search` cho `records_table_page`; `AccessGuard.check` nhận tên tool app-only.
6. Desk: `confirmInfo` (`desk/src/confirm.ts`) chưa đọc `content[0].text` khi lỗi không có `structuredContent`; thẻ `approval-card` đã tự đọc nên không phụ thuộc.

## REST `/workflow/v1` (16/10/2026)

Mã: `src/xt/adapters/http/workflow_api.py` (Starlette, chỉ phụ thuộc application/contracts/domain), nối ở `src/xt/entrypoints/workflow_http.py` (ngoài `composition/`, chỉ dùng API public của composition). Tài liệu: `docs/workflow-api.md`; quyết định: `docs/decisions.md` "REST /workflow/v1".

| Thay đổi | Ai cần làm gì |
|---|---|
| **Lớp tích hợp phải nối 1 dòng** trong `serve.Prepared.http_app()` (chưa nối, file của integrator): `from xt.entrypoints.workflow_http import with_workflow_api` rồi `return with_workflow_api(self, self.cfg.env, Starlette(routes=[*extra, *app.routes], lifespan=app.router.lifespan_context))`. Hoặc tự ghép `workflow_mount(prepared, env)` (một `Mount("/workflow/v1")`) vào đầu danh sách route | Integrator. Test tích hợp `tests/integration/composition/test_workflow_api.py` ghép đúng như vậy |
| API mount cấp thấp: `workflow_routes(dispatcher, auth, backend, *, classify, ensure_active, clock, max_body_bytes, server_url) -> Mount` | Port: `WorkflowAuth` (bearer/verify/challenge), `WorkflowBackend` (`bpmn`, `instance`), dispatcher = **cùng** `ScopedDispatcher` của MCP (`Prepared.dispatcher`) |
| Biến mới: `XT_WORKFLOW_AUDIENCE` (mặc định `xt-workflow`, phải khác `XT_MCP_RESOURCE`), `XT_WORKFLOW_REQUIRED_SCOPES` (mặc định `xt-workflow`), `XT_WORKFLOW_ALLOWED_CLIENTS` (mặc định = `XT_MCP_ALLOWED_CLIENTS`), `XT_WORKFLOW_PUBLIC_URL` (tùy chọn, `servers` của OpenAPI) | Deploy: thêm vào `deploy/.env`/compose của `xt-api` |
| Keycloak: client scope optional `xt-workflow` có audience mapper `xt-workflow` (giống `provision_data.py` cho `xt-data`), gán cho client headless (vd `xt-cli`) | Agent Keycloak (chưa làm trong phiên này) |
| APISIX `api-workflow`: chỉ `/workflow/v1/*`, `GET`/`POST`, body ≤ 64 KiB, `real-ip` + `limit-req` theo IP; không chèn/bỏ header (JWT kiểm ở xt-api) | Cần render lại `deploy/apisix/rendered/apisix.yaml` khi triển khai (chưa render để không đụng stack đang chạy) |
| Idempotency: `POST /instances` dùng khóa con `<Idempotency-Key>.create` / `.submit`; `POST /tasks/{ref}/decision` dùng nguyên khóa. Cùng namespace actor+tool với MCP | Gửi cùng khóa qua MCP và REST cho hai hành động khác nhau → `idempotency_conflict` (đúng thiết kế) |
| `request_info` chưa có trong `decide_request` → REST trả 400 `decision_not_supported` | Khi mở rộng contract: thêm vào enum + `DECISIONS` của composition, REST tự hết chặn (bỏ `None` trong `workflow_api.DECISIONS`) |
| Chưa có: JWT ở gateway (W7), rate-limit theo `azp`, `ToolContract.rest` | Theo `docs/gap-review.md` mục 8 |

### I-AUD-META: audit cho bảng `xt_meta.*` (16/10/2026, model engine)

API được hỗ trợ: `xt_audit.install_meta(tbl name, key_col name DEFAULT 'id', redact text[] DEFAULT '{}')`. Hàm là SECURITY DEFINER, thuộc `xt_audit_owner`; `xt_migrator` được EXECUTE.

**Cách hoạt động**
- Trigger `xt_audit_meta` (`AFTER INSERT/UPDATE/DELETE`) và `xt_audit_meta_truncate` thuộc `xt_audit_owner`.
- Event nối vào **chuỗi của tenant** theo cột `tenant_id`, `model = 'xt_meta.<bảng>'`. Vì vậy `xt_audit.verify`, `xt_audit.heads()` và checkpoint Ed25519 (`xt audit checkpoint|verify`) phủ luôn metadata.
- `TRUNCATE` không biết tenant nên nối vào chuỗi nền tảng `tenant_id = 00000000-0000-0000-0000-000000000000`.
- Không dùng HMAC. Cột trong `redact` chỉ lưu `redacted:sha256:<hex>`; chỉ dùng cho blob entropy cao. PII entropy thấp phải nằm ở bảng model (HMAC).
- `UPDATE` không đổi giá trị nào thì không sinh event. `drop_tenant` vẫn ghi DELETE vào chuỗi.
- Event trigger chặn `DISABLE`/`DROP` trigger `xt_audit_meta*` và `CREATE RULE` trên bảng đã gắn. Các trigger nghiệp vụ khác của bảng không bị đụng.

**Cài đặt**

| Trường hợp | Làm gì |
|---|---|
| CSDL mới | `bootstrap()` chạy `sql/bootstrap.sql` rồi `sql/audit_meta.sql`. Sau đó `xt db migrate` áp `0006_audit_meta.sql`, file này `GRANT TRIGGER … TO xt_audit_owner` và gắn audit cho `iam_policy`, `process_definition`, `pack_install`, `model_binding`, `process_event`, `pii_egress`, `model_change_request` (khóa `proposal_id`), `tool_operation` (redact `pending`, `receipt`). Bảng chưa có thì bỏ qua |
| CSDL đã bootstrap trước đó | Superuser gọi `xt.adapters.db.tenancy.install_meta_audit(conn)` (idempotent, cũng cập nhật event trigger bảo vệ) rồi `xt db migrate`. Nếu migrate trước khi cài thì `0006` dừng với thông báo rõ và không ghi vào ledger |
| Bảng `xt_meta` mới (migration của module khác) | Trong chính migration đó: `GRANT TRIGGER ON xt_meta.<bảng> TO xt_audit_owner; SELECT xt_audit.install_meta('<bảng>', 'id', '{}');`. Bảng phải có `tenant_id uuid` và một cột khóa uuid |

**Việc cho integrator**
- Bỏ đoạn `data_setup.install_data_prerequisites` cấp tạm `EXECUTE xt_audit.log()` cho `xt_migrator`: không còn cần, và đoạn này mở lại R2-04.
- Nối `install_meta_audit` vào bước superuser của `xt db migrate` hoặc của deploy.

Test: `tests/integration/model_audit/test_audit_meta_pg.py`, 6 test, PG17 thật, container `xt-audit-meta-pg`.
