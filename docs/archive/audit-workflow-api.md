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

## 5. Phản hồi tác giả (16/10/2026)

Mọi WA đã sửa và đã gỡ `xfail`. Kết quả: `tests/integration/workflow_api_audit` 17 passed; `tests/integration/composition` (gồm `test_workflow_api.py`, 5 test) xanh; unit xanh.

| # | Sửa | Ghi chú |
|---|---|---|
| WA-01 | `compose.yaml` xt-api: `XT_WORKFLOW_ALLOWED_CLIENTS: ${XT_WORKFLOW_CLIENTS:-xt-cli}` (cùng nguồn `provision_data.py`), `XT_WORKFLOW_AUDIENCE`, `XT_WORKFLOW_PUBLIC_URL`; `.env.example` ghi rõ | Test offline `tests/unit/http/test_workflow_deploy.py` nội suy env thật của service: `XT_MCP_ALLOWED_CLIENTS` rỗng + registry Keycloak vẫn dựng được cả hai cấu hình. Mức tối thiểu (ít nhất 1 client workflow) được kiểm và ghi trong `docs/integration-notes.md` |
| WA-02 | Ở service dùng chung (MCP + REST): `plan_decide` kiểm PEP `request.decide` của người nhận trên đúng ref (`ToolAccessGuard.person_allowed`) → 403 `delegate_not_permitted` trước khi tạo pending | Đường gỡ bước kẹt: người đề nghị rút đề nghị `in_review` (`transition_record` / `POST /instances/{id}/withdraw`, engine `ProcessService.withdraw`). Test audit WA-02 được siết: phải là 403 `delegate_not_permitted` |
| WA-03 | `ProcessService.check_delegate` (không đổi state) + kiểm người tồn tại/active trong tenant ở plan | 400 `invalid_delegate` / 403 `sod` |
| WA-04 | `tasks[].task_id` = ref đề nghị; id engine không còn trả ra | |
| WA-05 | Route catch-all trong Mount: 404/405 envelope (+`Allow`) | |
| WA-06 | So sánh yếu RFC 9110 (danh sách, `W/`, `*`); 304 có `Cache-Control` | ETag giữ dạng mạnh (bytes BPMN tất định) |
| WA-07 | `get_instance`: `get_record` 403/404 → `_not_found()` tĩnh | |
| WA-08 | Verifier workflow từ chối token có aud = resource MCP | **Không đồng ý một phần:** chiều ngược lại (token có thêm `xt-workflow` dùng ở `/mcp`) thuộc verifier MCP/`serve.IdentityHttpAuth`, chưa sửa ở đây vì đụng mọi connector; Keycloak không phát token như vậy (scope optional chỉ cho `xt-cli`, bị cấm với client MCP trong `provision_data.py`) |
| WA-09 | Schema: `definition_id` XOR `model_id` → 400 | |
| WA-10 | OpenAPI khai 304/405/413/422/428/429/501 | |
| Tài liệu | `docs/workflow-api.md`: trạng thái ghép serve, 403 cho ref tenant khác ở quyết định/gửi duyệt, 429/413 HTML của APISIX, `continuation`/remediation dùng chung với MCP (cố ý) | Thêm phát hiện: `transition_record(withdrawn)` trước đây trả `internal_error` vì output thiếu `model_revision`/`available_transitions` (vi phạm `RECORD_FULL`); đã sửa |

## 6. Vòng 2 (16/10/2026, Opus tester)

Cách test vẫn như vòng 1: gọi HTTP thật vào Postgres 17 (một container, cổng ngẫu nhiên, đã dọn) + composition thật + `/desk/confirm` có step-up thật. Để đổi trạng thái người giữa lúc plan và lúc xác nhận, test sửa thẳng `xt_meta.person` bằng tài khoản owner. Regression mới nằm ở `tests/integration/workflow_api_audit/test_workflow_api_audit_r2.py`: 5 test hàng rào và 2 test `xfail(strict=True, raises=AssertionError)`. Fixture `wstack` chuyển sang scope session để hai module dùng chung tenant `wxa`/`wxb`.

```bash
uv run pytest tests/integration/workflow_api_audit tests/integration/composition/test_workflow_api.py -m integration --no-cov
# 27 passed, 2 xfailed in 47.60s (chạy 3 lần liên tiếp đều xanh)
uv run pytest tests/unit/http --no-cov   # 26 passed
```

### 6.1 Kiểm lại WA-01…WA-10

Cả 10 test cũ, sau khi đã gỡ `xfail`, đều xanh. Tôi đọc lại các test tác giả đã sửa và thấy không có test nào bị nới (riêng WA-02 còn chặt hơn: bắt buộc 403 `delegate_not_permitted`). `compose.yaml` của `xt-api` đã có `XT_WORKFLOW_ALLOWED_CLIENTS: ${XT_WORKFLOW_CLIENTS:-xt-cli}`. **10/10 ĐẠT.**

### 6.2 Thử vượt các phần mới

| # | Tấn công | Kết quả | Bằng chứng |
|---|---|---|---|
| R1 | Đích ủy quyền hợp lệ lúc plan (phó phòng KD được cấp tạm `dept_head`), sau đó bị **vô hiệu hóa** trước khi người duyệt xác nhận | PASS | Trang confirm 409, replay 409 `confirmation_declined`, instance vẫn ở bước `dept_head`. Lúc xác nhận hệ thống kiểm lại, không có cửa sổ race |
| R2 | Như R1 nhưng **bị gỡ vai trò** `dept_head` trước khi xác nhận | PASS | Như trên. Đối chứng (không đổi gì): 200 `delegated` |
| R3 | Người không phải người đề nghị gọi withdraw (trưởng phòng duyệt, giám đốc, kế toán) | PASS* | 403, không rút được. *Nhưng phân biệt được với id ma (404): **WA-12** |
| R4 | Withdraw instance của tenant khác | PASS | 404, `error` giống hệt id ma |
| R5 | Withdraw khi thiếu khóa, sai revision, thêm trường `to_state`, replay, rút lần hai | PASS | Lần lượt 400 / 409 / 400 / 200 (replay) / 409 `invalid_state`. Instance `withdrawn`, `tasks=[]`, không còn `links.withdraw` |
| R6 | Withdraw sau khi đã duyệt xong (`approved`) | PASS | 409 (`revision_conflict` hoặc `invalid_state`) |
| R7 | Quyết định đang chờ 428, người đề nghị rút, sau đó người duyệt bấm xác nhận | PASS | Trang confirm 409, replay 409, instance vẫn `withdrawn`. Quyết định sau khi rút → 403, không có confirm_url |
| R8 | Bấm xác nhận quyết định **đồng thời** với withdraw (18 lượt) | **FAIL (vừa)** | Không lượt nào cả hai cùng thành công, trạng thái cuối luôn nhất quán. Nhưng có 2/18 lượt withdraw trả **500** `internal_error` do `DeadlockDetected` tại `workflow_bridge.get_for_update`: hai thao tác khóa bản ghi đề nghị và `workflow_instance` theo thứ tự ngược nhau. Xem **WA-11** |
| R9 | `If-None-Match` với `*`, `W/"…"`, danh sách có khoảng trắng, HEAD | PASS | 304 có `ETag` + `Cache-Control: private, no-cache` và body rỗng. `*` trên định nghĩa không tồn tại vẫn ra 404 (kiểm IAM trước) |
| R10 | ETag không dấu nháy, cắt cụt, viết HOA, `w/` thường | PASS | 200 (so sánh phân biệt hoa thường, đúng RFC) |
| R11 | Hai header `If-None-Match` riêng biệt | Ghi nhận | Chỉ header đầu được xét (`headers.get`). RFC cho phép gộp thành danh sách. Không ảnh hưởng an toàn, không tính là lỗi |
| R12 | ETag của tenant A gửi bởi tenant B | PASS | 304, vì cùng pack nên bytes BPMN trùng nhau. B không tự có được ETag của A, nên không rò |
| R13 | Envelope cho route lạ, không token | PASS | 404/405 JSON + `Allow`. `OPTIONS` → 405 `Allow: GET`. `/workflow/v1` → 307 về `/workflow/v1/` |
| R14 | Thời gian phản hồi 404 (trung vị của 40 lần) | Ghi nhận | Khác tenant: thật 15.9 ms, ma 12.0 ms (lệch nhỏ, cả hai đều dừng ở RLS). **Cùng tenant**, không có quyền: thật 55.8 ms, ma 17.3 ms (body đã giống hệt nhưng thời gian vẫn lộ). Xếp chung lớp với WA-07/WA-12. Rủi ro thấp vì UUID ngẫu nhiên và người đo phải là thành viên tenant |
| R15 | OpenAPI so với hành vi | PASS | `POST /instances/{id}/withdraw` có khai, mọi body thật (200/400/403/404/409) khớp schema. Các mã 304/405/413/422/428/429/501 đã được khai |

### 6.3 Lỗi mới

- **WA-11 (VỪA): deadlock giữa xác nhận quyết định và withdraw.**
  - Cái thua bị Postgres hủy sau `deadlock_timeout` và trả 500 với remediation "Retry the same call once". Dữ liệu không hỏng và không có trường hợp cả hai cùng thành công, nhưng client nhận 500 kèm độ trễ khoảng 1 s.
  - Sửa: hai đường `_commit_decide` và `_commit_withdraw` khóa cùng thứ tự. Chẳng hạn khóa `workflow_instance FOR UPDATE` trước, rồi mới khóa hoặc UPDATE bản ghi đề nghị. Thêm vào đó, map `DeadlockDetected`/`SerializationFailure` thành 409 `revision_conflict` hoặc 503 có thể thử lại, thay vì 500.
  - Test: `test_wa11_…`, lặp tối đa 60 lượt, dừng ngay ở lượt đầu dính 5xx.
- **WA-12 (THẤP): `POST /instances/{id}/withdraw` lộ instance tồn tại trong cùng tenant.**
  - Người không phải người đề nghị nhận 403 `access_denied`, còn id ma ra 404. Đây là cùng lớp với WA-07 nhưng trên endpoint mới.
  - Sửa: như `get_instance`, khi `transition_record` trả 403 hoặc 404 cho người không được đọc bản ghi thì trả `_not_found()` tĩnh.
  - Test: `test_wa12_…`.

### 6.4 Đánh giá WA-08 phía MCP (token có thêm aud `xt-workflow` dùng ở `/mcp`)

**Chấp nhận rủi ro cho mốc nộp bài, không chặn bàn giao**, với các điều kiện sau:
- Kẻ tấn công phải có token mang **cả** aud `/mcp` lẫn `xt-workflow`, `azp` thuộc allowlist MCP, và có scope `mcp-xt`.
  - Keycloak chỉ phát token như vậy khi cấu hình sai.
  - `provision_data.py` cấm client MCP/Desk nhận `xt-workflow`, và kiểm "token MCP sạch".
  - `xt-cli` không có `mcp-xt` và không nằm trong allowlist MCP.
- Token REST thông thường (aud chỉ `xt-workflow`) bị `/mcp` từ chối (401, test gốc). Token MCP thông thường bị `/workflow` từ chối, và WA-08 phía workflow đã sửa. Như vậy khi token bị lộ ở một bề mặt thì vẫn không phát lại được sang bề mặt kia. Phần còn hở chỉ là lớp phòng thủ thứ hai khi provisioning hỏng.
- Khuyến nghị sau mốc: verifier MCP từ chối token có aud thuộc `{xt-workflow, xt-data}`. Việc này chỉ cần một dòng ở `serve.IdentityHttpAuth` và không ảnh hưởng connector, vì connector không bao giờ có hai aud đó. Trước mắt, giữ bước kiểm "token MCP sạch" của `deploy/tests/security_smoke.sh` trong checklist deploy.

### 6.5 Kết luận vòng 2

**ĐẠT (có điều kiện nhẹ).**
- WA-01…WA-10 đã sửa và kiểm lại bằng HTTP thật.
- Các phần mới đứng vững trước các tấn công chính: race đổi đích ủy quyền, withdraw bởi người khác hoặc từ tenant khác, withdraw sau quyết định, quyết định sau withdraw, ETag.
- Còn **WA-11 (vừa)**: 500 do deadlock khi xác nhận và withdraw xảy ra cùng lúc. Nên sửa trước ngày nộp, vì có thể làm hỏng một bước demo nếu hai người thao tác cùng lúc.
- Còn **WA-12 (thấp)**: oracle tồn tại trong cùng tenant.
- WA-08 phía MCP: chấp nhận rủi ro như mục 6.4.

### 6.6 Phản hồi tác giả (vòng 2)

- **WA-11:** nguyên nhân là chuỗi audit theo tenant. `xt_audit.append` lấy advisory lock `xt_audit:<tenant>` ở lần ghi có audit đầu tiên và giữ tới commit. Withdraw giữ khóa này (do INSERT claim `tool_operation`) rồi chờ `workflow_instance`; xác nhận thì giữ `workflow_instance` (do re-plan) rồi chờ chuỗi audit.
  - Sửa: `xt.adapters.db.audit.lock_audit_chain` lấy đúng khóa đó **trước** mọi khóa dòng dễ tranh chấp: `PgWorkflowStore.get_for_update`, `PgOperationStore.claim` (trước savepoint/`lock_timeout`) và `lock_pending`. Thứ tự khóa giờ thống nhất: chuỗi audit, rồi tới dòng.
  - Kết quả: test r2 chạy 3 lần, mỗi lần 60 lượt, không có 5xx.
- **WA-12:** `POST /instances/{id}/withdraw` chỉ đọc ref (`WorkflowBackend.instance_ref`, một truy vấn, không nạp engine) và luôn gọi đúng một `transition_record`. Với id ma thì gọi bằng ref không thể tồn tại. Mọi `access_denied`/`not_found` và mọi id ma đều trả `_not_found()` tĩnh.
- **WA-08 (chiều MCP):** `ResourceServerSettings.foreign_audiences`. `load_resource_server_settings` mặc định chặn `{xt-workflow, xt-data}`. `AccessTokenVerifier` trả 401 `audience` khi token mang thêm aud của bề mặt khác. Phía workflow chặn `{resource MCP, xt-data}` qua cùng trường này. Unit test: `tests/unit/identity/test_access_token.py::test_mcp_rejects_tokens_also_issued_for_workflow_or_data`.
