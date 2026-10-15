<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Tích hợp: nối adapter thật vào 17 tool (`xt serve`, `xt worker`)

Ngày chạy: 16/10/2026. Mọi output dưới đây lấy từ lần chạy thật `make`-độc-lập:

```bash
XT_IT_REPORT=/tmp/xt-it-report.json uv run pytest -m integration tests/integration/composition --no-cov -v
```

Môi trường test: `postgres:17-alpine` thật (container `xt-it-composition-pg-<ngẫu nhiên>`, cổng ngẫu nhiên, tự xóa), uvicorn thật chạy app của `xt serve --transport http`, client `mcp` qua Streamable HTTP, token RS256 ký bằng khóa test và phát qua JWKS HTTP tĩnh (`tests/integration/composition/idp.py`), trang `/desk/confirm` với ID token step-up (`acr=urn:xt:acr:otp`, `amr` có `otp`, `auth_time` tươi). Không verifier giả, không store in-memory.

## 1. Sơ đồ nối

| Port của tool (`xt.application.tools`) | Adapter thật | File |
|---|---|---|
| Danh tính → `Caller` (tenant = `organization`, agent theo `azp`, `on_behalf_of` → `xt_meta.person.subject`, vai trò/tự chủ agent từ `xt_meta.agent_grant`) | `Runtime.resolve` | `entrypoints/composition/runtime.py` |
| `AccessGuard` (PEP) | `ToolAccessGuard` → `application.iam.AccessGuard` trên policy đã duyệt `xt_meta.iam_policy` | `composition/security.py` |
| `ModelCatalog`, `RecordService`, `create_task` | model engine (`PgCatalogReader`, `PgRecordRepository`, `RecordService`) | `composition/records.py` |
| `ProcessService`, `InboxService`, `transition_record(withdrawn)` | `ProcessService` + `SpiffProcessEngine`, định nghĩa theo `(process_id, version)` + `spec_sha256` | `composition/process.py` |
| `ModelChangeService` + duyệt đề xuất (`decide_request` trên `model_proposal:<id>`) | `ModelService.propose` / `approve_and_apply` (qua worker) | `composition/records.py`, `composition/model_changes.py` |
| `OperationStore` (claim-before-execute, receipt, PendingOperation), `TransactionRunner` | `PgOperationStore`, `PgUnitOfWork`: một lời gọi tool = một transaction Postgres | `adapters/db/confirmations.py` |
| `PiiLane` (`inbound`/`outbound`/`scrub_error`) | `PostgresPiiLane` + `ErrorScrubber` dùng chung; vault `xt_meta.pii_token`, nhật ký `xt_meta.pii_egress` | `composition/pii.py` |
| Trang xác nhận | `ConfirmPages` (Starlette, PKCE, `StepUpVerifier`, biên nhận Ed25519 dùng một lần, `ConfirmationService`) | `composition/confirm_web.py` |
| Hẹn giờ quy trình | `PgTimerScheduler` (outbox cùng transaction) → job `workflow.relay_timers` → `JobQueue.schedule_at` → job `workflow.tick` | `composition/worker.py`, `adapters/db/workflow_bridge.py` |
| Áp thay đổi model đã duyệt | outbox `xt_meta.model_change_request` → job `model.apply` (pha 1 DDL + pha 2 `run_jobs`) | `composition/worker.py`, `adapters/db/model_changes.py` |

`XT_MCP_SERVICES=xt.entrypoints.composition:build`. Thiếu biến nào thì `xt serve` dừng với danh sách lỗi (mã 2); không có đường in-memory nào trên đường thật.

## 2. Lệnh vận hành

```bash
xt db migrate                                                     # 0001..0005
xt tenant create --alias acme --name "Bình Minh" --timezone Asia/Ho_Chi_Minh --llm-egress masked
xt org import --tenant acme seed/binh-minh/org.json --links links.json --base-roles employee
xt pack install --tenant acme packages/de-xuat-phe-duyet --proposed-by agent:it --approved-by person:admin
xt pack install --tenant acme packages/de-xuat-phe-duyet --proposed-by agent:it --approved-by person:admin --confirm sha256:…
xt pack install --tenant acme packages/quan-tri-mo-hinh …          # policy duyệt thay đổi model
xt worker --install-schema                                         # XT_QUEUE_MIGRATOR_DATABASE_URL
xt tenant bind-queue --tenant acme                                 # XT_DB_OWNER_URL + biến của xt worker
xt serve --transport http --host 0.0.0.0 --port 8000
xt worker                                                          # XT_WORKER_HANDLERS=xt.entrypoints.composition.worker:handlers
```

`links.json` của tệp HR: `{"subjects": {"NV-0013": "<sub Keycloak>", …}, "agent_grants": [{"person": "NV-0013", "client_id": "<azp>", "autonomy": 1, "roles": ["agent"]}]}`. Gói chuẩn nằm ở `packages/` (architecture-v2 §4; decisions D-INT-2).

Biến mới so với tài liệu cũ: `XT_MCP_CONFIDENTIAL_CLIENTS` (tier `local` chỉ cho client confidential), `XT_WORKFLOW_RELAY_SECONDS` (chu kỳ relay, ≥ 5), `XT_DB_MIGRATOR_URL` cho `xt worker` (áp model), `XT_DB_OWNER_URL` cho `xt tenant bind-queue`.

## 3. Kết quả kiểm thử

```
tests/integration/composition/test_e2e.py::test_admin_outputs PASSED
tests/integration/composition/test_e2e.py::test_w1_advance_8m_end_to_end[asyncio] PASSED
tests/integration/composition/test_e2e.py::test_poka_yoke_vietnamese_rule[asyncio] PASSED
tests/integration/composition/test_e2e.py::test_rls_two_tenants[asyncio] PASSED
tests/integration/composition/test_e2e.py::test_idempotency_concurrent_double_submit[asyncio] PASSED
tests/integration/composition/test_e2e.py::test_pii_masked_for_cloud_full_for_local[asyncio] PASSED
tests/integration/composition/test_e2e.py::test_human_token_rejected_on_mcp[asyncio] PASSED
tests/integration/composition/test_e2e.py::test_unknown_org_is_rejected[asyncio] PASSED
tests/integration/composition/test_e2e.py::test_worker_relays_process_timers_to_queue[asyncio] PASSED
tests/integration/composition/test_e2e.py::test_w2_model_change_approved_then_applied_by_worker[asyncio] PASSED
tests/integration/composition/test_e2e.py::test_pg_operation_store_contract PASSED
tests/integration/composition/test_fixtures.py (7 test hạ tầng) PASSED
18 passed
```

`make check` (ruff, ruff format, mypy strict, import-linter 4/4 contract, REUSE, 1282 unit test, desk vitest 118 + Playwright 15, SPDX 318 tệp): xanh.

## 4. Output thật

### 4.1. Nạp tổ chức và cài gói (`xt org import`, `xt pack install`)

```
đã nạp vào acme: {'units': 7, 'people': 60, 'agent_grants': 8}
```

```
Gói de-xuat-phe-duyet 0.2.0 — Đề xuất và phê duyệt đa cấp (Tạm ứng, Thanh toán, Hoàn ứng)
Tenant: acme (seq 1)

Model:
KẾ HOẠCH THAY ĐỔI MODEL v0 → v1 (mã kế hoạch f8afa729823f897d)
Tổng: 6 AN TOÀN, 0 RỦI RO, 0 PHÁ HỦY, 0 BỊ CHẶN
  [AN TOÀN] Tạo model mới 'Giấy đề nghị tạm ứng' (advance_request) với 12 trường, RLS theo tenant + quyền ['chief_accountant', 'dept_head', 'director', 'employee', 'payment_accountant', 'xt_workflow']
  …
Quy trình: proposal_approval v3 (f0158db0ffab)
Policy IAM: 17 statement (d8ee3f8c2563)
Ràng buộc: advance_request=request

Mã xác nhận (digest): sha256:6537d454963c39fbe5e815c67df838e4913fd3282149d10255cde3c2e60a8edc

Đã kiểm, CHƯA áp. Người duyệt xem kế hoạch rồi chạy lại với --confirm <mã>.
```

Chạy lại với mã sai → `mã xác nhận không khớp kế hoạch hiện tại (…)` (rc 2). Với đúng mã:

```
đã cài de-xuat-phe-duyet 0.2.0 vào acme (sha256:6537d454963c39fbe5e815c67df838e4913fd3282149d10255cde3c2e60a8edc)
```

### 4.2. W1 "tạm ứng 8 triệu"

Người đề nghị Phan Tiến Dũng (NV-0013, Kinh doanh), agent `it-agent` (tier cloud, trần L1). `create_record` → `submit_request` (L1, chạy ngay):

```json
"request": {"ref": "advance_request:15f2b102-…", "state": "in_review", "revision": 2},
"route": [
  {"step": "dept_head", "label": "Trưởng bộ phận duyệt",
   "approver": {"ref": "person:6a42d741-…", "name": "Hoàng Quốc Bảo"}, "approver_rule": "department_head"},
  {"step": "director", "label": "Giám đốc duyệt", "approver": null, "approver_rule": "role(director)"}
],
"meta": {"effective_level": "L1", "pii": {"mode": "masked"},
         "receipt": {"idempotency_key": "it-key-w1-submit", "replayed": false, "commit_state": "committed"}}
```

Bước `chief_acct_overdue` không có trong route vì điều kiện tính trên biến thật của instance (kết quả `check_overdue_advances` = 0). Gửi lại đúng `idempotency_key` → cùng `data`, không chạy lần hai. Gửi với key khác:

```json
{"code": "precondition_failed",
 "message": "Đề nghị đang ở trạng thái 'in_review', chỉ gửi được khi là nháp hoặc bị trả lại.",
 "details": {"reason": "invalid_state", "details": {"state": "in_review"}}}
```

Agent của trưởng phòng gọi `decide_request` → luôn cần xác nhận (human-only):

```json
{"code": "confirmation_required",
 "message": "Cần Hoàng Quốc Bảo xác nhận thao tác này trên trang xác nhận của xT.",
 "details": {"confirmation_id": "cf_c393496c…", "status": "pending",
   "confirm_url": "http://127.0.0.1:48963/desk/confirm/cf_c393496c9f64b85e285e03ad623ea5ef",
   "confirmer": {"ref": "person:6a42d741-…", "name": "Hoàng Quốc Bảo"}, "level": "human_only",
   "plan_digest": "sha256:ecb54ba3…",
   "plan_summary": "Hoàng Quốc Bảo approve bước 'Trưởng bộ phận duyệt' của advance_request:15f2b102-…"}}
```

Trên `/desk/confirm`: GET → 302 tới `…/auth` với `client_id=xt-desk`, `code_challenge_method=S256`, `acr_values=urn:xt:acr:otp`, `prompt=login`. ID token thiếu `otp` → 403 "Bằng chứng xác thực không đạt"; người đề nghị tự duyệt → 403 (SoD); đúng người + OTP + `auth_time` tươi → trang kế hoạch (nhãn "do agent … soạn") → Đồng ý → "Đã thực hiện". Agent gọi lại đúng lời gọi:

```json
"outcome": "step_completed",
"request": {"state": "in_review", "revision": 3},
"decision": {"step": "dept_head", "decision": "approve", "decided_by": {"name": "Hoàng Quốc Bảo"}, "confirmed_via": "desk"},
"next_step": {"label": "Giám đốc duyệt", "approver": {"name": "Nguyễn Hữu Trí"}},
"meta": {"effective_level": "L3", "receipt": {"replayed": true, "commit_state": "committed"}}
```

Giám đốc duyệt tương tự → `"outcome": "request_approved"`, `"state": "approved"`, `"revision": 4`. `get_history(view="audit")` → `"chain_verified": true`.

### 4.3. Poka-yoke (rule của model, thông điệp tiếng Việt)

```json
{"code": "rule_violation", "message": "'Ngày về' phải sau hoặc bằng 'Ngày đi'",
 "remediation": "Sửa giá trị theo thông điệp của từng vi phạm rồi gọi lại.",
 "details": {"violations": [{"field": "trip_end", "label": "Ngày về", "rule": "r1", …}]}}
```

### 4.4. RLS hai tenant

Hai tenant `acme`, `beta` nạp cùng tệp seed (cùng mã nhân sự, id người khác nhau theo tenant). Người của `beta` đọc bản ghi của `acme` (cả `model:id` lẫn `xrn:acme:…`) nhận lỗi giống hệt một id không tồn tại trong `beta` (không lộ tồn tại, decisions D-INT-4); `search_records` của `beta` không thấy bản ghi đó:

```json
{"code": "access_denied", "message": "Không có quyền thực hiện thao tác này.",
 "details": {"reason": "behalf_permission", "owner_reason": "implicit_deny"}}
```

### 4.5. Idempotency (hai `submit_request` đồng thời, cùng key)

Hai lời gọi song song qua MCP cùng nhận một kết quả, một instance quy trình:

```
["process_instance:8b0fad95-0ef9-4756-a944-6d3bdc7e3507", "process_instance:8b0fad95-0ef9-4756-a944-6d3bdc7e3507"]
```

`test_pg_operation_store_contract` chạy `tests/unit/tools/operation_store_contract.py` trên Postgres thật (pool 2–4 connection, mỗi thread một transaction): claim chờ hàng đang giữ, chờ `lock_pending` của ConfirmationService rồi thấy receipt, `update_pending` không đè trạng thái đã đóng.

### 4.6. PII (tenant `llm_egress=masked`, client cloud)

```json
"values": {"amount": 4000000, "payout_method": "bank_transfer", "bank_account": "[PII:k5jkkvvmaxb2spjp]", …},
"meta": {"pii": {"mode": "masked", "masked_fields": ["advance_request.bank_account"]}}
```

Client confidential khai trong `XT_MCP_CONFIDENTIAL_CLIENTS` + `XT_MCP_LOCAL_CLIENTS` thấy giá trị thật; `xt-desk-agent` (public) luôn masked (decisions "Tier PII của Desk chat"). Token dùng được trong `where` (so bằng); lỗi echo số tài khoản 22 chữ số cũng bị che. Token `kind=human` (client xác nhận) gọi `/mcp` → 401.

### 4.7. Worker: hẹn giờ SLA và áp thay đổi model

```
tenant acme: job role xt_job_t1, relay hẹn giờ quy trình đã đăng ký
due=2026-10-16T18:16:24.906651+00:00 scheduled_at=2026-10-16T18:16:24.906651+00:00
```

SLA `PT24H` của bước trưởng phòng ghi outbox trong transaction của `submit_request`; relay chuyển thành job `workflow.tick` có `scheduled_at` đúng hạn và tự hẹn lần relay sau; tick trước hạn không leo thang. `xt worker` thật nạp handler composition và chạy.

W2 (gap-review #3): agent của trưởng phòng `propose_model_change` thêm `ma_du_an`:

```
KẾ HOẠCH THAY ĐỔI MODEL v1 → v2 (mã kế hoạch 12dc47e1fc1b3302)
Tổng: 2 AN TOÀN, 0 RỦI RO, 0 PHÁ HỦY, 0 BỊ CHẶN
  [AN TOÀN] Thêm trường 'Mã dự án' (ma_du_an, text) vào 'Giấy đề nghị tạm ứng' — cho phép trống
```

Người đề xuất tự duyệt → `access_denied`; chưa duyệt → worker không áp; giám đốc duyệt qua `/desk/confirm` → `request_approved` → relay + `model.apply`:

```
[{"proposal": "8004644e-9748-404c-8388-25e6e36c5d34", "state": "applied", "version": 2, "error": null}]
```

Cột `ma_du_an` có trong `information_schema`, `create_record` với field mới thành công, `get_record("model_proposal:<id>")` → `state=applied`. Xóa field (PHÁ HỦY): một người duyệt → trang báo "đang chờ người xác nhận thứ hai", worker không áp; người thứ hai (vai trò `it`, chọn theo D-INT-6) duyệt → áp, cột biến mất, `decided_by` có 2 người.

## 5. Còn mở

- **I-AUD-META**: bảng cấu hình `xt_meta.*` (policy, định nghĩa quy trình, gói, sự kiện quy trình, PII egress, receipt) chưa vào hash-chain vì `xt_audit.log` mới chỉ nhận bảng model của schema tenant; cần API audit metadata từ model engine (`docs/integration-notes.md`).
- `/pii/reveal` (kênh xem giá trị thật cho người) chưa có trong 17 tool v0.1.
- I-3: instance do `TimerPoller` tạo (quy trình `trigger=schedule`) chưa vào inbox; gói v0.1 chưa bind quy trình nào loại này.
- Mount REST `/workflow/v1` và đăng ký MCP Apps: chờ module của agent tương ứng (chưa có hàm đăng ký khi viết tài liệu này).
- `tests/integration/queue_audit/test_queue_round2.py::test_dlq_requeue_defers_a_new_fenced_job_and_purge_abandons` đang đỏ trong phần việc DLQ của tác giả queue (không thuộc composition).
