<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Dịch vụ Workflow headless: REST `/workflow/v1`

Tầng 1 của đề thi bắt buộc dịch vụ Workflow chạy được mà không cần MCP. `/workflow/v1` là API REST trên `xt-api.freepro.online` (qua APISIX), dùng **cùng pipeline** với 17 tool MCP: IAM + SoD → idempotency → plan/CAS → mức tự chủ/xác nhận của người → commit → làn PII. Không có đường tắt vào engine. Mã: `src/xt/adapters/http/workflow_api.py`, nối ở `src/xt/entrypoints/workflow_http.py`. Quyết định: `docs/decisions.md` "REST /workflow/v1". Tài liệu máy đọc: `GET /workflow/v1/openapi.json` (OpenAPI 3.1 sinh từ code).

## Trạng thái (đọc trước)

| Thành phần | Trạng thái |
|---|---|
| 8 endpoint + OpenAPI 3.1 | Có; 17 unit test (`tests/unit/http/test_workflow_api.py`, dispatcher thật + port giả, JWT ký thật qua JWKS tĩnh) |
| Postgres 17 thật + composition thật | 4 test tích hợp (`tests/integration/composition/test_workflow_api.py`, marker `integration`): W1 chạy hết bằng HTTP, cô lập tenant, BPMN hợp lệ, `headless-demo.sh` chạy curl thật |
| Ghép vào `xt serve` | **Chưa**: lớp tích hợp thêm 1 dòng vào `serve.Prepared.http_app()` (`docs/integration-notes.md`, mục REST `/workflow/v1`) |
| Route APISIX `api-workflow` | Có trong `deploy/apisix/apisix.yaml.tmpl`; chưa render/nạp vào stack công khai |
| Client scope Keycloak `xt-workflow` | Chưa provision trên Keycloak thật |

## Endpoint

Mọi đường dẫn bắt đầu bằng `/workflow/v1`. Cột "Tool" là tool MCP mà endpoint gọi qua dispatcher.

| Method + path | Tool | Ghi chú |
|---|---|---|
| `GET /definitions` | `inspect_workspace(include=[processes])` | Quy trình đang kích hoạt + link `self`, `bpmn` |
| `GET /definitions/{id}` | `describe_process` | Bước, người duyệt, SLA, trigger (`model_id` đề nghị) |
| `GET /definitions/{id}/bpmn` | `describe_process` rồi đọc bản compile | BPMN 2.0 XML có BPMNDI, `Content-Disposition: attachment; filename="<id>-v<rev>.bpmn"`, `ETag` (hỗ trợ `If-None-Match` → 304). Mở được bằng bpmn.io / Camunda Modeler |
| `POST /instances` | `describe_process` → `create_record` → `submit_request` | **Bắt buộc `Idempotency-Key`**. Body: `{definition_id \| model_id, values, note?}` (tạo nháp rồi gửi duyệt) hoặc `{ref, note?, expected_revision?}` (gửi nháp có sẵn). 201 + `Location`; gửi lại cùng khóa → 200 cùng kết quả |
| `GET /instances/{id}` | engine (RLS tenant) rồi `get_record` của đề nghị | Trạng thái (`running/completed/rejected/withdrawn`), task đang mở (bước, người được giao dạng `person:<id>`), bản ghi đề nghị đã qua làn PII. Biến/comment của engine không trả ra |
| `GET /inbox` | `get_inbox` | Query: `kinds` (phẩy), `status`, `due_before`, `limit`, `cursor`. Mục `approval` có `task_id` + `links.decision` |
| `POST /tasks/{task_id}/decision` | `decide_request` | **Bắt buộc `Idempotency-Key`**. `task_id` = `ref` trong inbox. Body: `{decision, expected_revision, reason?, delegate_to?}` |
| `GET /openapi.json` | (không) | Công khai, không cần token |

`decision` nhận tên outcome của engine: `approve`, `reject`, `return_for_revision` (= `return` của tool), `delegate`, `request_info`. Riêng `request_info` trả **400 `decision_not_supported`** vì ToolContract `decide_request` v0.1 chưa có giá trị này (không giả thành công).

## Xác thực

- `Authorization: Bearer <access token Keycloak>`, **`aud` = `xt-workflow`**, scope `xt-workflow`, `azp` thuộc `XT_WORKFLOW_ALLOWED_CLIENTS` (mặc định = `XT_MCP_ALLOWED_CLIENTS`). Token MCP (aud là URL `/mcp`) hay token `/data` (aud `xt-data`) đều bị 401; token workflow cũng bị `/mcp` từ chối.
- Lấy token: client `xt-cli` (public, auth-code + PKCE), `scope=openid xt-workflow` (scope optional do `provision_data.py` cấp cho `XT_WORKFLOW_CLIENTS`, mặc định `xt-cli`; mapper phát đúng `aud=xt-workflow`). `bash docs/headless-demo.sh --get-token workflow`. Token `xt-data` và token MCP không chứa aud này; `make -C deploy provision-data` tự kiểm `mcp-xt` sạch.
- Token phải là của **agent đại diện một người** (auth-code, `sub` liên kết với `xt_meta.person`). Service account không đại diện ai bị `access_denied invalid_behalf` (như MCP).
- **Phiên người (client Desk xác nhận) bị 401**: người chỉ quyết định trên trang `/desk/confirm/{id}` có step-up OTP (M6).
- Thiếu/sai token: 401 + `WWW-Authenticate: Bearer realm="xt-workflow", error="invalid_token", error_description="…", scope="xt-workflow"`. Thiếu scope: 403 `error="insufficient_scope"`. IdP không tới được: 503 + `Retry-After: 5`.
- Tenant lấy từ claim Organization (một giá trị); không có → 401, nhiều → 403 `tenant_ambiguous`.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `XT_WORKFLOW_AUDIENCE` | `xt-workflow` | `aud` bắt buộc; phải khác `XT_MCP_RESOURCE` (kiểm lúc khởi động) |
| `XT_WORKFLOW_REQUIRED_SCOPES` | `xt-workflow` | Scope bắt buộc (không được là `offline_access`) |
| `XT_WORKFLOW_ALLOWED_CLIENTS` | = `XT_MCP_ALLOWED_CLIENTS` | `azp` được gọi; không gồm client người; connector DCR không được |
| `XT_WORKFLOW_PUBLIC_URL` | (trống) | `servers[0].url` của OpenAPI |

Kiểm thu hồi token (introspection, `XT_IDP_INTROSPECTION=high_risk`) chạy ở bước commit rủi ro cao, giống MCP.

## Envelope và mã HTTP

Thành công và lỗi dùng **đúng envelope của tool** (`ok`, `untrusted:true`, `data`/`error`, `meta.trace_id`, `meta.receipt`). Lỗi do dispatcher dựng đã qua làn PII của tenant; lỗi do adapter dựng (header, JSON, schema) có thông điệp tĩnh và vẫn chạy qua `ErrorScrubber` (CCCD, số điện thoại, email). Lỗi schema chỉ trả `path` + `keyword`, không echo giá trị.

| `error.code` | HTTP |
|---|---|
| `invalid_request` | 400 |
| `unauthenticated` | 401 |
| `access_denied` | 403 |
| `not_found` | 404 (tenant khác cho đúng cùng câu trả lời) |
| `revision_conflict`, `idempotency_conflict`, `precondition_failed`, `confirmation_declined` | 409 |
| `resource_constraint` | 413 (body > 64 KiB) |
| `rule_violation` | 422 |
| `confirmation_required` | **428** + `Link: <confirm_url>; rel="confirm"` |
| `rate_limited` | 429 + `Retry-After` |
| `internal_error` | 500 |
| `capability_unavailable` | 501 |
| `timeout` | 503 + `Retry-After` |

`428` nghĩa là hành động **chưa chạy**: đưa `error.details.confirm_url` cho người duyệt, người bấm xác nhận (step-up), rồi gửi lại **y hệt** request với **cùng** `Idempotency-Key` → 200, `meta.receipt.replayed = true`.

## Ví dụ curl

```bash
API=https://xt-api.freepro.online/workflow/v1
# Token trong tệp header (không lên argv/ps):
printf 'Authorization: Bearer %s\n' "$(cat ~/.config/xt/wf_requester_token)" > /tmp/req.hdr
printf 'Authorization: Bearer %s\n' "$(cat ~/.config/xt/wf_approver_token)" > /tmp/apr.hdr
chmod 600 /tmp/req.hdr /tmp/apr.hdr

# 1. Định nghĩa + BPMN
curl -sS -H @/tmp/req.hdr "$API/definitions"
curl -sS -H @/tmp/req.hdr -o proposal_approval.bpmn "$API/definitions/proposal_approval/bpmn"

# 2. Khởi động W1: tạm ứng 8 triệu (tạo nháp + gửi duyệt)
curl -sS -H @/tmp/req.hdr -H 'Content-Type: application/json' \
  -H "Idempotency-Key: w1-$(date +%Y%m%d)-0001" \
  -d '{"definition_id":"proposal_approval","note":"Công tác Đà Nẵng",
       "values":{"prepared_on":"2026-10-16","amount":8000000,"trip_start":"2026-10-20",
                 "trip_end":"2026-10-22","destination":"Đà Nẵng","settlement_due":"2026-10-31",
                 "payout_method":"cash","settled":false}}' \
  "$API/instances"
#   -> 201, data.instance.id, data.request.ref, data.route = [dept_head, director]

# 3. Trạng thái instance
curl -sS -H @/tmp/req.hdr "$API/instances/<instance-id>"

# 4. Inbox người duyệt rồi quyết định -> 428 + confirm_url
curl -sS -H @/tmp/apr.hdr "$API/inbox?kinds=approval"
curl -sS -H @/tmp/apr.hdr -H 'Content-Type: application/json' -H 'Idempotency-Key: w1-dh-0001' \
  -d '{"decision":"approve","expected_revision":2}' \
  "$API/tasks/advance_request:<uuid>/decision"
#   -> 428, error.details.confirm_url = https://xt.freepro.online/desk/confirm/cf_…
#   Người duyệt mở URL, đăng nhập step-up, bấm Đồng ý; rồi gửi lại đúng lệnh trên -> 200
```

Toàn bộ luồng trên có trong `docs/headless-demo.sh` bước 4 (`XT_WORKFLOW_TOKEN_FILE`, `XT_WORKFLOW_START_FILE`, `XT_WORKFLOW_APPROVER_TOKEN_FILE`; xem `docs/headless-data.md`).

## Kết quả chạy thật (16/10/2026)

`test_headless_demo_script_workflow_section` chạy `bash docs/headless-demo.sh` (curl thật) vào stack tích hợp: Postgres 17 (`postgres:17-alpine`), composition thật, uvicorn trên loopback, token ký bằng khóa RSA test qua JWKS tĩnh. Bước 1-3 báo "chưa triển khai" vì stack test không có Keycloak/PostgREST/APISIX; bước 4 là stdout thật:

```text
[4/4] Workflow (http://127.0.0.1:57759/workflow/v1): dùng token workflow riêng, không dùng token data
    4a không token: 401 (đúng)
    4b definitions: 200, có proposal_approval
    4c BPMN: 200, 65200 byte, có BPMNDI (lưu /tmp/pytest-of-chinhan/pytest-85/xt-wf-demo0/out.bpmn)
    4d POST /instances (Idempotency-Key demo-1792088645-21330): 201, instance 9eebae49-b3a8-4954-88fb-4ca76e88bc9d, đề nghị advance_request:decdd6c5-f190-45b1-9a00-8aaed60a7828
    4e GET /instances/9eebae49-b3a8-4954-88fb-4ca76e88bc9d: 200, trạng thái running, bước dept_head
    4f inbox người duyệt: 200, việc duyệt advance_request:decdd6c5-f190-45b1-9a00-8aaed60a7828 (revision 2)
    4g quyết định qua REST: 428 confirmation_required (agent KHÔNG tự duyệt được)
       người duyệt mở: http://127.0.0.1:57759/desk/confirm/cf_1a4f5a503cba9a9aececa0557d0fa0d6
       sau khi xác nhận, gửi lại đúng request với Idempotency-Key: demo-dec-1792088645-12168
  => Workflow: OK
```

`test_w1_advance_over_rest_without_mcp` đi tiếp: người duyệt xác nhận trên `/desk/confirm` (step-up OTP thật qua IdP cục bộ), agent gửi lại → 200 `step_completed`, bước kế là "Giám đốc duyệt"; instance chuyển sang task `director`. Cùng test: tenant `wfb` đọc instance của `wfa` nhận 404 giống hệt id ngẫu nhiên. `test_definitions_and_bpmn_download`: mọi flow node có `BPMNShape`, mọi `sequenceFlow` có `BPMNEdge`, và XML tải về nạp lại được bằng parser BPMN đã cứng hóa của engine (`load_specs`).

Chạy lại: `uv run pytest tests/integration/composition/test_workflow_api.py -m integration` (cần image `postgres:17-alpine` có sẵn; `free -g` ≥ 4 GB). Ghi transcript: `XT_IT_WF_DEMO=/tmp/wf-demo.txt`.

## Giới hạn

- `request_info` chưa mở (xem trên). Hủy/rút đề nghị vẫn qua tool `transition_record` (MCP); chưa có endpoint REST.
- JWT chưa kiểm ở APISIX (W7) và rate-limit theo IP, chưa theo `azp`.
- `GET /definitions` chưa trả `revision` (theo output của `inspect_workspace`); dùng `GET /definitions/{id}`.
