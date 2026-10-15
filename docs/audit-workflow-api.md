<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Audit REST `/workflow/v1` (16/10/2026, Opus tester)

Phạm vi: `src/xt/adapters/http/workflow_api.py`, `src/xt/entrypoints/workflow_http.py`, test unit và test tích hợp, `docs/workflow-api.md`, route APISIX `api-workflow` (`deploy/apisix/apisix.yaml.tmpl`), `docs/decisions.md` mục "REST /workflow/v1". Không sửa `src/`.

Cách test: gọi HTTP thật vào stack tích hợp gồm Postgres 17 (`postgres:17-alpine`, một container `xt-it-composition-pg-<hex>` chạy `--rm` và đã dọn), composition thật, uvicorn trên loopback, JWT ký thật qua JWKS tĩnh, trang `/desk/confirm` có step-up thật. Có 2 tenant (`wxa`, `wxb`) và 5 người: requester NV-0013, trưởng phòng KD NV-0010, giám đốc NV-0001, kế toán NV-0005, trưởng phòng KT NV-0004. Grant của agent đặt **autonomy 3** để thử xem agent có tự duyệt được không. Riêng phần gateway thì đọc cấu hình, rồi gửi request GET/OPTIONS/POST thật vào APISIX của stack `xt-e2e` đang chạy (`127.0.0.1:9080`, Host `xt-api.freepro.online`). Các request này không mang token và không ghi dữ liệu.

Regression: `tests/integration/workflow_api_audit/` (`conftest.py` + `test_workflow_api_audit.py`). Có 7 test hàng rào ĐẠT và 10 test `xfail(strict=True)`, mỗi test ứng với một lỗi WA-xx.

```bash
uv run pytest tests/integration/workflow_api_audit -m integration --no-cov -rxX
# 7 passed, 10 xfailed in 16.39s
```

## 1. Chạy lại test có sẵn

| Bộ test | Kết quả |
|---|---|
| `tests/unit/http/test_workflow_api.py` | **17 passed** |
| `tests/integration/composition/test_workflow_api.py` (lần chạy đầu) | **4 ERROR**: `ServeConfigError: XT_PII_REVEAL_ORIGINS chưa đặt`. Composition mới (01:27) bắt buộc `XT_PII_REVEAL_ORIGINS/CLIENTS` nhưng fixture chưa đặt hai biến này |
| Cùng 4 test, chạy với env đã bổ sung | 4 passed |
| Sau khi integrator cập nhật fixture (cùng phiên) | **4 passed** |

## 2. Kết quả theo hạng mục

| # | Hạng mục | Kết quả | Bằng chứng (HTTP thật) |
|---|---|---|---|
| 1 | Token MCP (aud `/mcp`) dùng ở `/workflow` | PASS | 401, `error_description="The access token was not issued for the workflow API"` |
| 2 | Token workflow dùng ở `/mcp` | PASS | 401 (test gốc + audit) |
| 3 | Token `/data` (aud `xt-data`), sai `iss`, hết hạn, `alg=none`, Basic, Bearer rác | PASS | 401 kèm `WWW-Authenticate: Bearer realm="xt-workflow"` |
| 4 | `azp` lạ, `azp=xt-desk-agent` | PASS | 401 `client_not_allowed` |
| 5 | Token người (client Desk xác nhận) | PASS | 401 "Human session tokens are not accepted" |
| 6 | Đúng aud nhưng thiếu scope `xt-workflow` | PASS | 403 `insufficient_scope` |
| 7 | Không có org → 401; hai org → 403 `tenant_ambiguous`; `sub` của B mang org A, `sub` chưa liên kết | PASS | 403 `invalid_behalf` |
| 8 | Token có **hai aud** (`/mcp` + `xt-workflow`) | **FAIL (thấp)** | Được nhận ở cả `/workflow` (200) và `/mcp` (200). Xem WA-08 |
| 9 | Thiếu `Idempotency-Key`, sai định dạng, ngắn hơn 8 ký tự | PASS | 400 `idempotency_key_required` / `_format` |
| 10 | Dùng lại khóa với body khác (values, note, chế độ `ref`) | PASS | 409 `idempotency_conflict`; gửi lại đúng body ban đầu → 200 replay |
| 11 | 10 request song song cùng khóa | PASS | 1×201 + 9×200, đều trỏ về một instance, không có 500 |
| 12 | Cùng khóa nhưng người khác (cùng tenant) hoặc tenant khác | PASS | Mỗi người được 201 với instance riêng. Khóa tách theo `(tenant, actor, tool, key)` |
| 13 | Agent tự duyệt khi autonomy = 3 | PASS | 428 `confirmation_required` + `Link: <confirm_url>; rel="confirm"`, level `human_only` |
| 14 | SoD: người đề nghị tự duyệt; trưởng phòng tự đề nghị | PASS | 403 `self_approval`. Đề nghị của trưởng phòng được chuyển lên giám đốc duyệt |
| 15 | Duyệt task không có trong inbox (kế toán, trưởng phòng khác, giám đốc duyệt sớm) | PASS | 403 `behalf_permission` / `not_assigned_approver`, không có confirm_url |
| 16 | `confirm_url` bị mở bởi người khác, người đề nghị, hoặc người tenant B | PASS | Trang confirm trả 403; replay vẫn 428; chỉ đúng người duyệt mới làm được → 200 |
| 17 | Sau 428, dùng lại khóa nhưng đổi body | PASS | 409 `idempotency_conflict` |
| 18 | Người đề nghị dùng lại khóa của trưởng phòng | PASS | 403 `self_approval`, không nhận được receipt của người khác |
| 19 | Ref cũ sau khi đã ủy quyền | PASS | Người ủy quyền dùng revision cũ → 409 `revision_conflict`; dùng revision mới → 403 `not_assigned_approver` |
| 20 | **Ủy quyền** cho người không đủ quyền | **FAIL (cao)** | Bước duyệt bị kẹt. Xem WA-02 |
| 21 | Ủy quyền cho người không hợp lệ (UUID ma, tenant khác, `me`, người đề nghị) | **FAIL (vừa)** | Bước plan vẫn ra 428; đến lúc người duyệt bấm xác nhận mới bị chặn (409, operation `invalidated`). Xem WA-03 |
| 22 | Quyết định trên instance đã `completed` | PASS | 409 `revision_conflict` / 403, không có confirm_url |
| 23 | `request_info` | PASS | 400 `decision_not_supported`, đúng như decisions.md |
| 24 | Tenant B đọc instance của A | PASS | 404 giống hệt UUID ngẫu nhiên (`error` bằng nhau) |
| 25 | Tenant B quyết định hoặc gửi đề nghị bằng ref của A | PASS* | B không phân biệt được ref thật với ref ma. *Nhưng mã trả về là **403** chứ không phải 404 như docs ghi |
| 26 | Tenant B dùng `xrn:wxa:` trong task_id hoặc `delegate_to` | PASS | 403 `bad_reference` / 404 |
| 27 | Tải BPMN | PASS | 65 200 byte, BPMNDI đầy đủ. Không chứa đường dẫn `/home`, `/tmp`, `/app`, UUID, email, xrn, alias/ID tenant, tên người, `<script>`, `DOCTYPE`. BPMN của B trùng byte với A vì cùng pack, không có dữ liệu riêng của tenant. Id lạ hoặc chữ hoa → 404; `..%2f` → 404 (do router) |
| 28 | Định nghĩa không tồn tại | PASS | 404 `not_found`, `details.ref="process:<id>"` (chỉ là id client đã gửi) |
| 29 | Cùng tenant, không có quyền đọc instance | **FAIL (thấp)** | 403 hoặc 404 kèm `details.ref`, nên khác với UUID ma. Xem WA-07 |
| 30 | Envelope lỗi đã scrub PII | PASS | CCCD/SĐT/email trong query và body: lỗi schema của dispatcher thay bằng `[PII:…]`; lỗi của adapter chỉ trả `path` + `keyword`; khóa trùng chỉ trả tên khóa |
| 31 | Envelope cho route hoặc method lạ | **FAIL (thấp)** | `text/plain` "Not Found" / "Method Not Allowed". Xem WA-05 |
| 32 | Body > 64 KiB (có Content-Length hoặc chunked), JSON hỏng, sâu 30 000 cấp, số 5 000 chữ số, `NaN` | PASS | 413 envelope / 400. Không có 500 |
| 33 | Rate-limit | PASS* | App không tự giới hạn (60 GET liên tiếp đều 200). APISIX thật với 90 request song song: 46–48×200, 42–44×429. *Nhưng 429 và 413 của gateway là **HTML**, không có `Retry-After`, không theo envelope như bảng mã trong docs |
| 34 | CORS | PASS | Không có header ACAO. Preflight: app trả 405, APISIX trả 404 (route chỉ mở GET/POST). Đúng với API headless; trình duyệt không gọi được |
| 35 | OpenAPI so với hành vi | PASS* | Body thật của 200/201/400/403/409/428 (definitions, instance, inbox, decision, started) khớp schema trong `openapi.json` (kiểm bằng jsonschema 2020-12). *Thiếu các mã 413, 429, 304, 422 (WA-10) |
| 36 | Triển khai thật | **FAIL (cao)** | Không client nào lấy được token dùng được. Xem WA-01 |

## 3. Lỗi

### WA-01 (CAO, deploy): REST không dùng được trên stack công khai
- `deploy/keycloak/provision_data.py` chỉ cấp scope `xt-workflow` cho `XT_WORKFLOW_CLIENTS`, mặc định là `xt-cli`. Script này còn cấm client MCP/Desk nhận scope đó.
- Ở `xt-api`, `XT_WORKFLOW_ALLOWED_CLIENTS` mặc định bằng `XT_MCP_ALLOWED_CLIENTS`. `deploy/compose.yaml` không truyền bất kỳ biến `XT_WORKFLOW_*` nào. Container `xt-e2e-xt-api-1` đang chạy có `XT_MCP_ALLOWED_CLIENTS=xt-desk-agent`.
- Hệ quả: token của `xt-cli` bị 401 `client_not_allowed`, còn `xt-desk-agent` thì không bao giờ có scope.
- Rủi ro kèm theo: compose để `XT_MCP_ALLOWED_CLIENTS: ${XT_MCP_ALLOWED_CLIENTS:-}`, tức có thể rỗng. `serve.Prepared.http_app()` luôn gọi `with_workflow_api`, mà `workflow_resource_settings` sẽ raise khi danh sách client rỗng. Một deploy chỉ dùng connector DCR vì thế sẽ không khởi động được `xt-api`.
- Sửa: thêm vào service `xt-api` dòng `XT_WORKFLOW_ALLOWED_CLIENTS: ${XT_WORKFLOW_CLIENTS:-xt-cli}`, cùng nguồn với provision. Có thể thêm `XT_WORKFLOW_PUBLIC_URL: https://${XT_DOMAIN_API}`.
- Test: `test_wa01_…`.

### WA-02 (CAO, core decide_request, lộ ra qua REST): ủy quyền làm kẹt quy trình
- Trưởng phòng KD ủy quyền bước `dept_head` cho trưởng phòng Kế toán (`person:<uuid>` hợp lệ). Plan ra 428, xác nhận xong → 200 `delegated`. Inbox của người được ủy quyền có mục này (revision 3).
- Người được ủy quyền gọi approve → 403 `behalf_permission` (`owner_reason: implicit_deny`), vì policy `request.decide` của dept_head đòi `resource.department == subject.department`. Người ủy quyền và giám đốc cũng bị 403 `not_assigned_approver`.
- Không ai quyết định được bước này nữa, và REST không có endpoint rút hoặc hủy đề nghị.
- Sửa: lúc plan phải kiểm người nhận ủy quyền có qua PEP `request.decide` trên đúng ref (và không vi phạm SoD). Nếu không qua → 403 hoặc 422 trước khi tạo pending.
- Test: `test_wa02_…`.

### WA-03 (VỪA): đích ủy quyền sai chỉ bị chặn sau khi người duyệt đã step-up
- `delegate_to` là UUID không tồn tại, người của tenant khác (UUID trần), `me`, hoặc chính người đề nghị: cả 4 trường hợp đều ra **428**.
- Người duyệt phải đăng nhập OTP rồi mới thấy "Không thực hiện được" (409, pending `invalidated`). Không có rò dữ liệu, nhưng người duyệt bị bắt step-up cho thao tác chắc chắn thất bại.
- Sửa: kiểm đích (cùng tenant, tồn tại, khác người gọi, khác người đề nghị) ngay ở `plan`.
- Test: `test_wa03_…`.

### WA-04 (VỪA, hợp đồng API): `instance.tasks[].task_id` là UUID nội bộ của Spiff
- `GET /instances/{id}` trả `task_id` là UUID task của engine. Trong khi đó `POST /tasks/{task_id}/decision` cần `task_id` là ref đề nghị.
- Client làm theo tên trường sẽ gửi UUID → 404 do regex `TASK_ID`.
- Điều này trái decisions.md câu 4 ("Không lộ id nội bộ của engine ra làm khóa thao tác"). Giảm nhẹ: có `links.decision` đúng.
- Sửa: bỏ trường đó hoặc đặt `task_id = ref`. Nếu vẫn muốn trả id engine thì đổi tên thành `engine_task_id`.
- Test: `test_wa04_…`.

### WA-05 (THẤP): 404/405 của router không theo envelope
- `GET /workflow/v1/nope`, `DELETE /inbox`, path có `/` thừa: Starlette trả `text/plain`.
- Sửa: thêm route catch-all hoặc exception handler trong `Mount` để trả `error_envelope`.
- Test: `test_wa05_…`.

### WA-06 (THẤP): `If-None-Match` không theo RFC 9110
- Chỉ so khớp chuỗi tuyệt đối. Với `W/"…"` hoặc danh sách `"x", "<etag>"` thì trả 200 thay vì 304. Response 304 cũng thiếu `Cache-Control`.
- Test: `test_wa06_…`.

### WA-07 (THẤP): oracle tồn tại trong cùng tenant
- Với instance có thật, kế toán nhận 403 `behalf_permission` (kèm `principal`). Trưởng phòng KT nhận 404 kèm `details.ref="advance_request:<uuid>"`. Còn UUID ma chỉ trả 404 `details:{}`.
- Như vậy người không có quyền vẫn biết instance tồn tại và còn biết ref đề nghị của nó. UUID ngẫu nhiên nên rủi ro thấp. Giữa các tenant thì không bị (mục 24).
- Sửa: ở `get_instance`, khi `get_record` lỗi 403/404 thì trả `_not_found()` tĩnh.
- Test: `test_wa07_…`.

### WA-08 (THẤP, phòng thủ chiều sâu): token nhiều audience
- Token có `aud=[<mcp>, "xt-workflow"]` và đủ scope thì dùng được ở cả hai bề mặt. Hiện chỉ dựa vào provisioning Keycloak để không bao giờ phát loại token này.
- Điều này trái tinh thần decisions.md: "token bị lộ ở một bề mặt không phát lại được sang bề mặt kia".
- Sửa: verifier của workflow từ chối token có aud là resource MCP (hoặc đòi aud đúng một giá trị).
- Test: `test_wa08_…`.

### WA-09 (THẤP): `definition_id` và `model_id` cùng gửi
- Schema cho phép gửi cả hai. Khi đó adapter dùng `model_id` và bỏ qua `definition_id` mà không báo gì.
- Sửa: dùng `oneOf` (chỉ một trong hai), hoặc kiểm `trigger.model_id == model_id`.
- Test: `test_wa09_…`.

### WA-10 (THẤP, OpenAPI): thiếu mã trạng thái thực tế
- Thiếu `413` (POST), `429` (`idempotency_in_progress`), `304` (BPMN), `422` (decide). Hai mã `405` và `501` cũng không được khai.
- Test: `test_wa10_…`.

### Sai lệch tài liệu (không có test)
- `docs/workflow-api.md` ghi "Ghép vào `xt serve`: **Chưa**", nhưng `serve.py:352-356` đã gọi `with_workflow_api`.
- Bảng mã ghi tenant khác → 404 `not_found`. Thực tế `decision` và `POST /instances {ref}` trả **403** `access_denied` (vẫn không phân biệt được ref thật với ref ma).
- Bảng ghi 429 + `Retry-After`. Thực tế 429/413 ở APISIX là HTML, không có `Retry-After`, không theo envelope.
- Remediation trong lỗi REST vẫn mang giọng MCP ("call get_history(ref=explain_ref…)", `error.continuation.tool = decide_request`), kể cả với 403 `insufficient_scope` vốn không có `explain_ref`.
- Test tích hợp gốc đỏ trong khoảng thời gian composition đổi env bắt buộc (mục 1). Integrator đã sửa.

## 4. Kết luận

**Lõi bảo mật của API: ĐẠT.** Các phần sau đều vững qua HTTP thật trên PG17:
- Tách audience giữa MCP, data và workflow.
- Token người bị 401; thiếu scope bị 403.
- Idempotency theo actor và tenant, kể cả khi gọi đồng thời.
- Agent không tự duyệt được, kể cả với autonomy 3.
- SoD, xác nhận đúng người, 428 + confirm_url.
- Cô lập tenant: 404 giống hệt id ma cho instance và BPMN.
- Lỗi đã scrub PII; giới hạn kích thước body hoạt động.

**Bàn giao: CHƯA ĐẠT (FAIL có điều kiện)**, vì 2 lỗi mức cao:
- **WA-01**: REST không dùng được ở deploy công khai, có thể làm `xt-api` không khởi động.
- **WA-02**: ủy quyền làm kẹt quy trình, không có đường gỡ qua REST.

Sửa WA-01, WA-02 (nên làm luôn WA-03, WA-04) rồi gỡ `xfail` tương ứng thì có thể chuyển sang ĐẠT. Các lỗi WA-05…WA-10 ở mức thấp, sửa sau mốc nộp bài vẫn được.
