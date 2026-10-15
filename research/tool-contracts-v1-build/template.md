<!-- SPDX-License-Identifier: Apache-2.0 -->
# Hợp đồng tool MCP của xT, v1 (đã đối chiếu sau debate)

> **v1.0.1 changelog:** theo quyết định D8 (`architecture-debate.md` vòng 3, `architecture-v2-verdict.md`) không có kênh xác nhận CLI. Bỏ `cli_command` khỏi `confirmation_required.details`; `confirm_url` luôn là string khi `status=pending` (stdio trỏ tới Desk cục bộ). Thêm OpenAPI 3.1 (E9) vào danh sách đầu ra sinh từ contract; CLI chỉ là CLI vận hành. Không breaking: `details` là object mở.

- **Phiên bản contract:** `1.0.0`. Gồm catalog v0.1 (17 tool) và phần bổ sung v1.0 (4 tool).
- **Ngày:** 15/10/2026.
- **Trạng thái:** chốt sau debate vòng 2. Đây là spec, chưa phải code đã chạy.
- **Thay thế:** `research/tool-contracts-draft.md`. Log debate ở `docs/tool-contracts-debate.md`.
- **Nguồn sinh:** mọi JSON Schema trong tài liệu này sinh từ `contracts.py` (chạy ở `/tmp/xt-contracts-v1/`, bản sao ở `research/tool-contracts-v1-build/`). Tất cả đã qua `jsonschema.Draft202012Validator` (`build.py`, 0 lỗi). Không có schema nào viết tay riêng.

## 0. Quyết định chốt

| # | Quyết định | Thay cho (bản nháp) | Lý do |
|---|---|---|---|
| C1 | **17 tool ở v0.1, 21 tool ở v1.0.** Một shape `ToolContract` duy nhất | 18 tool | debate F2, F11, F13, F17 |
| C2 | Payload là **object phẳng** `{field: value}`, validate bằng `values_schema` sinh từ model revision đã pin. Adapter **từ chối khóa JSON trùng** | Mảng `FieldValue {field,value}` | F7 |
| C3 | Input là object đóng. **Không có combinator ở root.** Ràng buộc chéo field kiểm ở server | `allOf/if-then` ở root | F7 |
| C4 | **`cursor` / `next_cursor`** ngay từ v0.1 | `offset` + snapshot | F15 |
| C5 | Output: **envelope đóng, `data` mở** | Tất cả `additionalProperties:false` | F14 |
| C6 | Annotations = `derive(effects)` **đúng công thức REA**. v0.1 có 0 tool destructive và 0 tool openWorld | Có thêm `replaces_state`, `changes_definitions` | F6 |
| C7 | **Xác nhận của người qua `confirm_url`**, trỏ tới trang `/desk/confirm/{id}`, đăng nhập bằng client Keycloak `xt-desk` riêng. Trang xác nhận **tự thực thi** pending operation. Agent không cầm bằng chứng nào | `approval_ref`, `human_confirmation_ref`, `preview_digest`, `preview_token` | F1, F16 |
| C8 | `transition_record` là động cơ vòng đời chung cho Record, Task, Request (rút), Notification (đã đọc) | Không có, cộng `update_task` | F2, F12, F13, F21 |
| C9 | `find_people` là tool theo ý định, cho câu hỏi về tổ chức | Không có | F11 + spike 4B |
| C10 | `aggregate_records` tính count/sum/avg/min/max và group_by phía server, có `lineage_ref` | Không có | F4 |
| C11 | `upload_file` có 3 nguồn: `text`, `base64`, `upload_link` (trang Desk + presigned PUT qua APISIX tới SeaweedFS) | Không có | F3 |
| C12 | Làn PII: field có `pii: true` (và `sensitive`). Output trả `[PII:token]` khi `pii.mode=masked`. Ghi token ngược vào DB bị từ chối | `sensitive: boolean` | F9 |
| C13 | Mã lỗi rút từ 24 xuống **15** | 24 mã | §8 |

## 1. Một shape `ToolContract`

```text
ToolContract {
  name                       snake_case, ổn định mãi mãi
  since                      "0.1" | "1.0"            (thêm tool = minor, không breaking)
  description                1-3 câu tiếng Anh: dùng khi / không dùng → tool khác / "VD: <câu tiếng Việt>"
  input_schema               JSON Schema 2020-12, object phẳng, đóng, mọi property có description, không có combinator ở root
  output_schema              Schema của `data`, để mở. Generator bọc vào envelope đóng (§3.6)
  effects                    10 cờ, khai cho hợp của mọi input hợp lệ và mọi hệ quả deferred
  annotations                SINH từ effects (§2), không viết tay
  max_autonomy               L0..L3 = trần có thể phát sinh; server tính effective_level theo từng lời gọi
  human_only                 true = luôn cần chính người được chỉ định xác nhận (decide_request)
  iam                        action IAM kiểm thêm (cộng với quyền chuyên biệt của primitive)
  errors                     tập con của §8
  ui_resource                "ui://xt/<view>" | null   → _meta.ui.resourceUri
  returns_untrusted_content  luôn true
  examples                   input hợp lệ; contract test chạy từng cái với schema đầy đủ và bản compact
}
```

Từ contract sinh ra 7 đầu ra như `architecture-v1.md` §4.3: đăng ký MCP, REST `/rest/v1/tools/{name}`, OpenAPI 3.1, CLI vận hành (không phải kênh xác nhận), catalog/docs, metadata skill và contract test. Tool nào thiếu effects thì ứng dụng không khởi động.

**Các bất biến `build.py` đã kiểm cho cả 21 tool:**
1. `check_schema` hợp lệ cho input và output (đã bọc envelope).
2. Mọi example qua schema đầy đủ và qua bản compact.
3. Mỗi sample output qua envelope.
4. Thêm khóa trong `data` vẫn hợp lệ. Thêm khóa ở envelope thì bị từ chối.
5. Tham số lạ (`approved: true`) bị từ chối.
6. Mọi property ở mọi cấp của input đều có `description`.
7. Không có `allOf/anyOf/oneOf/if/then/not/$ref` ở root.
8. Mọi object input đều đóng.
9. v0.1 có không quá 17 tool, 0 tool destructive.
10. Không tool nào openWorld.
11. `delete_record` là destructive.

## 2. Effects → annotations (đúng như REA)

**10 cờ effects:** `mutates_records`, `mutates_definitions`, `enqueues_work`, `notifies_internal`, `accesses_external_network`, `launches_process`, `may_discard_data`, `executes_foreign_code`, `mutates_device`, `idempotent`.

```python
WRITE = [mutates_records, mutates_definitions, enqueues_work, notifies_internal,
         accesses_external_network, launches_process, mutates_device]
readOnlyHint    = not any(WRITE)
destructiveHint = may_discard_data or executes_foreign_code or mutates_device     # REA toolEffects.ts:655
idempotentHint  = idempotent                                                       # mọi tool ghi bắt buộc idempotency_key
openWorldHint   = accesses_external_network or launches_process                    # REA toolEffects.ts:657
```

**Bất biến kèm theo** (contract test bắt buộc):
- Không tool MCP nào gây egress ra ngoài tổ chức, kể cả theo đường deferred. Gửi ra ngoài (email khách hàng, webhook, ngân hàng) chỉ chạy trong action handler của process, sau một xác nhận riêng trên trang xác nhận.
- Thông báo cho thành viên của chính tenant qua kênh do tenant cấu hình (inbox, Telegram đã liên kết) là `notifies_internal`, tức closed-world.
- Ghi đè field là additive, vì giá trị cũ còn trong `record_history`. Kích hoạt model ở v1 không xóa vật lý dữ liệu.

Nhờ các bất biến này, chỉ `delete_record` (v1.0) có `may_discard_data`.

Annotations chỉ là gợi ý cho host. Biên an toàn nằm ở server (`architecture-v1.md` §5.4).

## 3. Quy tắc chung

### 3.1. Ref và định danh

| Loại | Pattern / quy ước |
|---|---|
| Ref ở bề mặt tool | `model:id` (ví dụ `advance_request:tu-2026-0042`, `person:mai`). Input nhận thêm XRN đầy đủ `xrn:{tenant}:{model}:{id}`. Output luôn trả dạng ngắn |
| Tenant | Luôn lấy từ token. Ref của tenant khác trả `not_found` |
| `id` | `[a-z0-9][a-z0-9_-]{0,63}`, nhận được ULID viết thường |
| `model_id` / `field_id` | `^[a-z][a-z0-9_]{0,62}$`. Cộng danh sách từ khóa cấm và `quote_ident` ở compiler (`architecture-v1.md` §6.1) |
| Person | `me` hoặc `person:<id>`. Muốn có ref thì tìm bằng `find_people`. Tên người không phải ref |
| Explain / lineage | `explain:<id>` |

### 3.2. Payload phẳng validate theo runtime model schema

- `values` (cho create/transition) và `set` (cho update) là object `{field_id: value}`.
  - `value` là scalar, `null` (xóa field) hoặc mảng scalar.
  - Object lồng nhau bị chặn ngay ở schema.
- Server validate object này bằng `values_schema`, sinh từ **model revision đang active**. Agent đọc được schema đó qua `describe_model(include_values_schema=true)`. Schema này có `additionalProperties:false`, type, enum, min/max, required (khi create) và kiểm tra ref đúng model.
- Rule chéo field (`trip_end >= trip_start`), uniqueness và policy chạy trong transaction. Lỗi trả `rule_violation` với `details.violations[{field, rule, message}]`.
- **Khóa trùng:** JSON cho phép khóa trùng và parser giữ giá trị cuối. `build.py` đã chứng minh chuỗi `{"amount":"1","amount":"9000000"}` vẫn qua schema. Vì vậy adapter phải parse bằng `object_pairs_hook` từ chối khóa trùng. Vi phạm trả `invalid_request`, reason `duplicate_key`.
- **Tiền và decimal:** dạng chuỗi thập phân. Số nguyên JSON chính xác (|n| ≤ 2^53) được nhận rồi chuẩn hóa. Số có phần lẻ dạng float bị từ chối, với remediation "gửi dạng chuỗi".
- **Ngày giờ:** ngày là `YYYY-MM-DD`; timestamp theo RFC 3339. Tenant timezone áp cho ngày không kèm giờ.
- Field `pii` không nhận giá trị dạng `[PII:…]` (§3.8).

### 3.3. Lọc, sắp xếp, phân trang

- **`where`** là object phẳng. Giá trị thường nghĩa là bằng. Muốn dùng toán tử thì viết object toán tử: `eq ne gt gte lt lte in contains is_null`. Các khóa nối với nhau bằng AND.
  - Khóa lõi dùng được: `state`, `owner`, `created_at`, `updated_at`.
  - `"me"` là alias của người gọi.
  - OR và NOT chưa hỗ trợ ở v1. Cần thì gọi 2 lần.
- **`sort`:** `["-created_at", "title"]`, tối đa 3 field. Server luôn thêm `id` làm tie-breaker.
- **`limit`:** 1..200, mặc định 20. Mức này chọn theo context 8k–16k của model local (spike) và trần response của transport.
- **`cursor`:** opaque, có MAC. Cursor bind với principal, digest của query và `as_of`. Server dùng keyset bên trong.
  - Đổi tham số khác giữa các trang → `invalid_request` reason `cursor_mismatch`.
  - Cursor hết hạn → `cursor_expired`.
  - Remediation chung: "gọi lại không có cursor".
- **`next_cursor: null`** nghĩa là hết dữ liệu. `total` là số bản ghi người gọi được thấy, hoặc `null` nếu không biết. Không bao giờ đếm trên toàn tenant rồi mới lọc.

### 3.4. Idempotency và CAS

- **`idempotency_key` bắt buộc ở mọi tool ghi.** Lý do ở debate F8.
  - Namespace của key: tenant + effective actor + tool.
  - Fingerprint: canonical args.
  - Cùng key, cùng args → trả lại receipt cũ (`meta.receipt.replayed=true`).
  - Cùng key, args khác → `idempotency_conflict`.
  - Retention công bố ở `inspect_workspace.limits`.
- **CAS bắt buộc** (`expected_revision`) ở `update_record`, `transition_record`, `decide_request` và `delete_record`. **Tùy chọn** ở `submit_request`, và `base_revision` ở `propose_*` cũng tùy chọn.
- Revision lệch → `revision_conflict`, kèm `details.current_revision`. Không auto-merge và không có `force`.
- **`create_record` / `submit_request` không đòi revision của model hay process.** Server pin bản active và trả `model_revision` trong output.

### 3.5. Mức tự chủ và xác nhận của người

| Mức | Ví dụ | Agent làm được khi |
|---|---|---|
| L0 | Mọi tool đọc | Có quyền IAM |
| L1 | Tạo bản ghi/draft/comment, gửi đề xuất **của chính người được đại diện**, tự chuyển trạng thái việc của mình, giao việc cho cấp dưới trực tiếp (policy mặc định), upload, đề xuất đổi model | Có quyền IAM. Ghi audit |
| L2 | Giao việc ngoài tuyến báo cáo, sửa field `impact: high`, transition khai `level: L2` | `effective_level` ≤ `autonomy_ceiling` của agent (người cấp trong Desk, mặc định L1). Ngược lại thì **một xác nhận của chính người được đại diện** |
| L3 | Transition khai L3 (ví dụ chấm dứt hợp đồng), `delete_record` bản ghi không phải draft của mình, `start_process` L3 | Luôn cần xác nhận. Policy có thể đòi thêm người thứ hai, khi đó `status=awaiting_second_approver` |
| `human_only` | `decide_request`; ký, lệnh chi, kích hoạt định nghĩa (đều nằm trong process) | Luôn cần **chính người được giao bước đó** xác nhận, bất kể trần của agent |

`effective_level` do server tính từ effects thực, policy và field bị chạm. Giá trị này trả về ở `meta.effective_level`. `max_autonomy` trong contract là trần tĩnh.

**Thứ tự kiểm tra trong một mutation:**
1. Xác thực, tenant.
2. IAM và SoD (người đề nghị không tự duyệt). Vi phạm trả `access_denied` kèm `explain_ref`, **trước** mọi xác nhận.
3. Tra receipt theo key.
4. Validate schema và model.
5. CAS.
6. Rule và policy.
7. Mức tự chủ: nếu thiếu thì tạo pending operation và trả `confirmation_required`.
8. Một transaction gồm mutation, audit hash-chain, outbox và receipt.

Storyboard S2a ("Việt bảo agent tự duyệt") vì thế dừng ở bước 2 với `access_denied`, không ra link xác nhận.

**Luồng `confirm_url`** (giải quyết F1, khớp D7/D8 của `architecture-v1.md`):

```text
agent ──tools/call decide_request(args, key)──▶ xt-api
xt-api: IAM/SoD ok, cần human ─▶ lưu PendingOperation{tool, canonical_args, key, plan_digest, confirmer, ttl=10'}
       ◀── isError: confirmation_required {confirm_url, confirmer, plan_summary, plan_digest, expires_at, continuation}
agent: đưa link cho người dùng (MCP App: nút "Mở trang xác nhận" = ui/open-link; host không có Apps: link text)
người ─▶ https://<xt>/desk/confirm/{id}  (APISIX → Desk backend; OIDC code flow client `xt-desk`; L3/vượt ngưỡng thêm prompt=login + acr)
Desk hiển thị plan, digest, effects, nhãn "do agent soạn" cho text tự do ─▶ người bấm Đồng ý / Từ chối
xt-api (nhận lời gọi từ Desk backend, kiểm azp=xt-desk ∧ sub=confirmer ∧ xác thực tươi (max_age=0 + TOTP) ∧ nonce, CSRF):
       revalidate CAS/rule ─▶ thực thi PendingOperation trong 1 transaction, ghi Confirmation + receipt + audit
agent ──gọi lại y hệt (cùng key)──▶ nhận success với meta.receipt.replayed=true
       (từ chối/hết hạn ─▶ confirmation_declined; vẫn chờ ─▶ confirmation_required như cũ)
```

**Các quyết định hòa giải với `architecture-v1.md`:**

| Điểm | Chốt |
|---|---|
| Ai sở hữu trang | xT Desk (`desk/`), route `/desk/confirm/{id}`. Không cần app riêng |
| Client Keycloak | `xt-desk`: client của Desk backend (OIDC code flow, token người nằm ở server, không ở trình duyệt; theo `architecture-v2.md`), redirect cố định về domain xT. **Không bao giờ** là client DCR. `/confirm` đòi xác thực tươi `max_age=0` + TOTP. xt-api chỉ coi token `azp=xt-desk` là phiên người. Token của connector (Claude, ChatGPT, Goose, `xt-desk-agent`) luôn là `invoking_actor=agent(azp)`, `on_behalf_of=sub` |
| Mốc | Theo `architecture-v2.md`: `/confirm` server-render có ngay ở **v0.1** (cùng Streamable HTTP); `/desk/upload` đi cùng SeaweedFS ở v0.2; phần còn lại của Desk ở v0.3. Lý do giữ sớm: thiếu `/confirm` thì connector HTTP không làm được gì từ L2 trở lên |
| Chế độ stdio | `confirm_url` trỏ tới Desk cục bộ (`http://<host>/desk/confirm/{id}`); không có `cli_command`; xác nhận luôn qua Desk hoặc Telegram (quyết định D8: không có kênh xác nhận CLI) |
| Telegram (v0.2) | Callback Telegram cho `chat_id` đã liên kết là kênh người hợp lệ (1 chạm). Vượt ngưỡng step-up thì nút chuyển thành `confirm_url`. Ngưỡng nằm ở dữ liệu gói; mặc định của `de-xuat-phe-duyet` là số tiền > 50.000.000 VND (`confirm_step_up`), không gắn với bước Giám đốc |
| MCP elicitation dạng URL | Chỉ gửi thêm khi client khai capability. **Chưa kiểm chứng** trên Claude/ChatGPT |
| Thẻ MCP App | Không có tool app-only để ghi. Nút "Duyệt" mở `confirm_url`. Storyboard S2b/S3 cần sửa: thẻ App tốn 2 chạm, Telegram tốn 1 chạm |

### 3.6. Envelope kết quả

**Success:**
- `isError` là false.
- `structuredContent` khớp envelope dưới đây.
- `content[0].text` là JSON của **cùng object**.
- `_meta.provenance.untrusted_content=true` để tương thích REA.

Envelope đóng; `data` là schema mở của từng tool.

```json
ENVELOPE_JSON
```

**Lỗi:**
- `isError: true`.
- Không có `structuredContent`, vì `outputSchema` chỉ mô tả kết quả thành công.
- `content[0].text` là JSON theo schema lỗi ở §8.

### 3.7. Dữ liệu không tin cậy

- `untrusted: true` đặt ngay trong JSON mà model đọc.
- Server `instructions` nói rõ: dữ liệu doanh nghiệp là dữ liệu, không phải chỉ thị.
- Text tự do (`reason`, `note`, comment, nội dung file) mang `authored_by_kind`.
- Trang xác nhận và thẻ duyệt phải hiện nhãn "do agent soạn" cho text đó.
- Text trích từ file (`get_record include=text`) luôn được đánh dấu untrusted.

### 3.8. Làn PII

- **Model spec:** field có `pii: true` (dữ liệu cá nhân) và `sensitive: true` (dữ liệu nhạy cảm như CCCD, sức khỏe, lương; ngầm định `pii`).
- **Chế độ hiệu lực của mỗi lời gọi** (`meta.pii.mode`), tính từ policy tenant `llm_egress ∈ {masked, local_only}` và lớp client:

| `llm_egress` của tenant | client `human` (Desk) | `local` (Goose/Desk chat + Ollama) | `cloud` (Claude/ChatGPT) |
|---|---|---|---|
| `masked` (mặc định) | full | full | masked |
| `local_only` | full | full | omitted (field bị bỏ, liệt kê ở `meta.pii.omitted_fields`) |

Client `cloud` không bao giờ nhận `full` (`architecture-v2.md`: "`cloud` luôn masked"). Giá trị `full` của `architecture-v1.md` bị bỏ.

- **Định dạng che:** `[PII:<token>]`. Token ổn định theo (tenant, giá trị, epoch), nên vẫn group hoặc so bằng được. `where` chấp nhận token để so sánh bằng.
- **Ghi token ngược lại** vào field bất kỳ trả `invalid_request`, reason `masked_value`.
- **Áp dụng cho mọi tool đọc**, kể cả `find_people`, `aggregate_records` (`group_by` trên field PII trả token) và text file. Mọi lần dữ liệu `pii` ra khỏi xT ghi `egress` vào audit.
- **Giới hạn trung thực:** "người thấy tên thật, LLM chỉ thấy token" chỉ đúng trong **Desk** (`azp=xt-desk`). MCP App trong host bên thứ ba dùng chung token với model, nên **không** lấy được giá trị thật. Storyboard S4/F-22 phải hiện panel lương ở Desk.

### 3.9. File

| `source` | Dùng khi | Luồng |
|---|---|---|
| `text` | Agent tự soạn nội dung (biên bản, báo cáo, nháp văn bản), tối đa 256 KiB | Ghi trực tiếp, `status=not_scanned` (v0.1) hoặc `clean` (v0.2, profile `full` có ClamAV; theo `architecture-v2.md` §3) |
| `base64` | CLI hoặc iframe MCP App đã đọc tệp thật, tối đa `limits.max_inline_upload_bytes` (1 MiB) | Như trên |
| `upload_link` | **Người dùng** cần đính kèm tệp (hóa đơn, hợp đồng). Model không có byte của tệp đính kèm trong chat | Trả về `upload_url` (trang Desk `/desk/upload/{id}`, đăng nhập `xt-desk`) và `put_url` (presigned PUT `https://<xt>/blob/upload/{id}?sig=…`; APISIX kiểm chữ ký, giới hạn kích thước `limits.max_upload_bytes`, proxy S3 tới SeaweedFS bucket `tenant-<id>`). TTL bằng `limits.upload_link_ttl_seconds` (900 giây) |

- **Vòng đời:** `awaiting_upload → scanning → clean | infected`. `not_scanned` dùng khi profile chưa có ClamAV. `expired` khi link hết hạn mà chưa có tệp.
- **Gắn tệp vào bản ghi:** ghi `file:<id>` vào field kiểu `file` bằng `create_record` hoặc `update_record`.
- `submit_request` và transition có `required_fields` chứa file sẽ trả `precondition_failed` nếu tệp chưa `clean`. Với profile không có ClamAV thì điều kiện là `clean|not_scanned`.
- **Đọc nội dung:** `get_record(file_ref, include=["text"])` trả `content` (trích native ở v0.1, OCR ở v1.0) cùng `next_text_cursor`.
- MCP resource không dùng để upload, vì resource chỉ đi chiều server → client.

### 3.10. Bổ sung cho model spec (`describe_model`)

- `fields[]` có thêm `pii`, `sensitive`, `impact: normal|high`, `unique`, kiểu `person` và `file`.
- `states[]` gồm `{id, label, initial, terminal, pauses_sla}` và `transitions[]` gồm `{from[], to, label, level, allowed, required_fields, guard_rule, starts_process}`.
  - Compiler ép: có đúng một state `initial`, và mỗi cặp (from, to) chỉ có một transition.
  - State được đổi **chỉ** qua `transition_record`.
- `metrics[]` là metric có tên đã được duyệt, gồm `{id, label, op, field, where}`. `aggregate_records(metric=…)` dùng metric này.
- **Task** là model thuộc primitive `task`. Mỗi loại task là một model với state machine riêng, mặc định `todo → in_progress → review → done`, cộng `returned`, `blocked` (`pauses_sla`), `cancelled`. Field lõi: `assignee` (`impact: high`), `due_at`, `priority`, `parent`, `depends_on`, `recurrence`.
- **Request** là model thuộc primitive `request`, có `process`. Các state `submitted/in_review/approved/rejected/returned` do engine điều khiển. `transition_record` chỉ được tới `withdrawn` (requester, L1).
- **Notification**: `unread → read`, L1, chỉ người nhận được chuyển.
- **ProcessDefinition** có `trigger.kind ∈ {manual, on_submit, on_transition, timer(rrule), event}`. Không có CRUD Schedule.

## 4. Catalog

### 4.1. v0.1: 17 tool

| # | Tool | Ý định chính | Trần | RO | D | I | OW | UI |
|---|---|---|---|---|---|---|---|---|
CATALOG_TABLE_V01

`update_task`, `delete_record`, `start_process`, `get_process_status` và `propose_process_change` của bản nháp không còn trong v0.1:
- `update_task` đi qua `update_record` cộng `transition_record`.
- `get_process_status` được gộp vào `get_record(process_instance)`.
- Ba tool còn lại xem §6.

### 4.2. v1.0: thêm 4 tool (tổng 21)

| # | Tool | Ý định chính | Trần | RO | D | I | OW | UI |
|---|---|---|---|---|---|---|---|---|
CATALOG_TABLE_V10

Thêm tool là thay đổi minor, không breaking. `explain`, `add_comment`, `ack_notifications` **không** thêm. Chỉ cân nhắc lại khi eval cho thấy agent không tìm ra đường `get_history(view="explain")`, `create_record(comment)` hay `transition_record(notification)`.

### 4.3. Định tuyến theo ý định (dùng cho skill `xt-core`)

| Người dùng nói | Tool đầu tiên |
|---|---|
| "Ai báo cáo cho X / X là ai / sếp của X" | `find_people` |
| "Tổng / đếm / trung bình / theo tháng / theo phòng" | `aggregate_records` |
| "Tìm / liệt kê / các … đang …" | `search_records` |
| "Có gì cần tôi xử lý / duyệt" | `get_inbox` |
| "Tạo đề xuất / đơn / phiếu" | `describe_model`, rồi `create_record`, rồi `submit_request` |
| "Giao việc cho …" | `find_people`, rồi `create_task` |
| "Xong / bắt đầu / trả lại / rút / đã đọc" | `transition_record` |
| "Sửa / đổi người / dời hạn" | `update_record` |
| "Duyệt / từ chối / trả lại đề xuất" | `decide_request` (sẽ ra `confirm_url`) |
| "Đính kèm" | `upload_file` |
| "Vì sao / ai đã làm / con số này từ đâu" | `get_history` |
| "Thêm nghiệp vụ / thêm trường" | `propose_model_change` (dry run), rồi gửi |

## 5. Contract chi tiết v0.1

TOOLS_V01

## 6. Contract chi tiết v1.0

TOOLS_V10

## 7. Ví dụ ghép tool: W4 tạm ứng (storyboard S1, S2)

1. `describe_model(advance_request)`: agent đọc field và rule.
2. `create_record(advance_request, values{amount:"80000000", trip_start:"2026-11-20", trip_end:"2026-11-18"…}, key)` bị từ chối với `rule_violation`, có 2 violations kèm remediation. Agent hỏi lại người dùng.
3. `create_record(…amount:"8000000", trip_end:"2026-11-22", key mới)` thành công. Kết quả có `suggested_next=submit_request`.
4. Người dùng đính kèm hóa đơn qua `upload_file(source=upload_link)`. Agent đưa `upload_url` cho người dùng, rồi gọi `update_record(set{invoice:"file:…"})`.
5. `submit_request(ref, key)` ở mức L1 (đề xuất của chính mình). Kết quả có `route=[Mai, Long]`, mỗi bước kèm `due_at`.
6. Việt nói "duyệt luôn đi": `decide_request` trả `access_denied` (sod, not_assigned_approver) kèm `explain_ref`. Audit ghi đỏ.
7. Mai dùng Claude: `get_inbox(kinds=[approval])`, rồi `get_record(ref, include=[steps])`, rồi `decide_request(approve)`. Kết quả là `confirmation_required` với `confirm_url`. Mai bấm trên Desk (hoặc Telegram), rồi agent gọi lại và nhận `outcome=step_completed`.
8. Long nhận Telegram và bấm 1 chạm, `request_approved`. Action của process post Ledger.
9. Báo cáo: `aggregate_records(advance_request, metric="advance_total", group_by=["created_at:month"])` trả về con số, `summary` và `lineage_ref`. Muốn truy nguồn thì gọi `get_history(lineage_ref, view=explain)`.

## 8. Contract lỗi chung

**Hình dạng lỗi:**
- Lỗi luôn nằm trong `content[0].text` (JSON), kèm `isError: true`.
- `retryable` nghĩa là: gửi lại **đúng args và cùng key** sau khi điều kiện phục hồi đã thỏa thì có thể thành công.
- `category` của bản nháp bị bỏ, vì nó suy được từ `code`.
- `continuation` là lời gọi gọi được ngay (mẫu REA `resource_constraint`).
- `explain_ref` có ở mọi lỗi `access_denied` và `rule_violation`.

```json
ERROR_ENVELOPE_JSON
```

`details` của `confirmation_required` (mở, các trường dưới đây là bắt buộc):

```json
CONFIRMATION_JSON
```

**15 mã lỗi cho v0.1:**

| Mã | retryable | Khi nào / agent làm gì |
|---|---|---|
ERROR_TABLE

**Mã bị bỏ so với bản nháp, kèm chỗ thay thế:**

| Mã cũ | Thay bằng |
|---|---|
| `tenant_mismatch` | `not_found` (không lộ tenant) |
| `approval_required`, `human_confirmation_required` | `confirmation_required` |
| `stale_approval`, `preview_expired` | `revision_conflict` hoặc `confirmation_declined` |
| `snapshot_expired` | `invalid_request` reason `cursor_expired` |
| `invalid_delegation` | `access_denied` / `rule_violation` |
| `cancelled` | Hủy ở MCP không có response |
| `integrity_mismatch`, `cleanup_incomplete`, `configuration_invalid`, `execution_failure` | `internal_error` hoặc `capability_unavailable`. Lỗi định nghĩa trong proposal là `rule_violation` |

**Các `details.reason` chuẩn hóa** (mở rộng được, không breaking): `wrong_tool`, `duplicate_key`, `masked_value`, `cursor_mismatch`, `cursor_expired`, `unknown_field`, `state_field`, `not_assigned_approver`, `sod`, `autonomy_hard_limit`, `file_not_clean`, `dependency_open`.

**Ví dụ (đã validate):**

```json
SAMPLE_ERRORS_JSON
```

## 9. Kích thước schema (đo thật)

**Cách đo** (`build.py`): JSON minified UTF-8. Các cột:
- `in`: inputSchema đầy đủ.
- `compact`: bỏ `$schema`, `default` (profile compact theo REA).
- `data`: schema `data`.
- `out`: outputSchema đã bọc envelope.
- `tools/list`: một entry gồm name, title, description, inputSchema compact và annotations.
- `+out`: entry đó cộng thêm outputSchema.

Ngân sách REA là ≤ 13.312 B mỗi input.

| Tool | since | trần | in | compact | data | out | tools/list | +out | desc | props/req | RO | D | I | OW |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|---|
SIZE_ROWS

SIZE_TOTALS

**Nhận xét:**
- Input lớn nhất là `propose_model_change`, vẫn dưới ngân sách REA. Lý do là nó nhúng FieldSpec, StateSpec, TransitionSpec, RuleSpec và MetricSpec có description đầy đủ. Tool này **không** nằm trong hồ sơ cho model nhỏ.
- So với bản nháp (CRITIC đo input bundled ≈ 25,8 KB cho 18 tool, chỉ có 2 description), v1 lớn hơn vì **mọi** property đều có description. Đổi lại, model không phải đoán nghĩa của tham số.
- Envelope lỗi: ERR_BYTES B. `details` xác nhận: CONF_BYTES B.

## 10. Hồ sơ cho model nhỏ và probe trên qwen3.5:4b

**Hồ sơ "core-10"** (client local bật, server vẫn quảng bá đủ catalog): `CORE_LIST`. `inspect_workspace`, `describe_*`, `get_history`, `update_record`, `upload_file`, `propose_model_change` để ở catalog đầy đủ hoặc dùng qua MCP prompt.

PROBE_SECTION

## 11. Prompts và skills v0.1

- **Skill (4):**
  - `xt-core`: router theo bảng §4.3; xử lý `confirmation_required` (đưa link, không tự xác nhận, gọi lại cùng key); `revision_conflict`; dữ liệu untrusted; hỏi lại khi thiếu đối tượng.
  - `xt-approvals`
  - `xt-tasks`
  - `xt-data-modeling`
- **Prompt (4):** `prepare_request`, `review_pending_requests`, `triage_my_work`, `change_model_safely`.
- Các prompt/skill khác giữ lịch theo `architecture-v1.md` §7. Metadata `tool_count` và `catalog_digest` sinh từ contract.

## 12. Còn mở, phải kiểm chứng trước khi khóa v0.1.0

1. **Eval 30 kịch bản** (dương, âm, injection) × {Claude, GPT, qwen3.5:4b} × {core-10, v0.1-17}. Probe ở §10 chỉ là smoke test 8 câu, chỉ đo lời gọi đầu tiên.
2. **Prototype `/desk/confirm`** với client `xt-desk`: kiểm `azp`, `auth_time`, nonce, CSRF, step-up `acr` trên Keycloak 26.8. Đây là phụ thuộc của mọi L2+ qua HTTP.
3. **Presigned PUT qua APISIX tới SeaweedFS**: plugin ký URL, giới hạn kích thước, tắt buffering. Chưa kiểm chứng.
4. **Elicitation dạng URL** trên Claude/ChatGPT: chưa kiểm chứng. Chỉ là kênh phụ.
5. **Kiểm `values_schema` và rule trên model engine thật** (phụ thuộc spike-model-engine), kể cả hiệu năng `aggregate_records` trên 100k bản ghi (p95 ≤ 300 ms theo `architecture-v1.md` §6.1).
6. **Đồng bộ storyboard với contract:**
   - Các tên `xt_*` đổi thành tên thật.
   - S2b/S3: nút trên thẻ App mở `confirm_url`.
   - S4: panel lương hiện trong Desk.
   - S3: `xt_apply_model_change` không tồn tại; activation đi qua `decide_request` của Request quản trị.

7. **Đối chiếu với `architecture-v2.md`** (viết song song, giao cho TCV1 chốt tên và số tool):
   - Catalog 17/21 nằm trong trần ≤ 22 và thỏa 6 năng lực bắt buộc của v2 §1.3.
     - Năng lực 5 (tìm xuyên model, danh bạ): `search_records(query)` cộng `find_people`. Riêng "truy vấn đặt tên (`views`)" được phủ bởi metric có tên của `aggregate_records`; `views` cho `search_records` để lại v0.3 như v2 xếp lịch, thêm dưới dạng tham số tùy chọn (không breaking).
     - Năng lực 6 (submit/withdraw L1): `submit_request` cộng `transition_record(withdrawn)`.
   - **Chỗ lệch có chủ ý:** v2 §1.3 ghi "một tên tham số gate duy nhất `confirmation_ref`". TCV1 dùng **0 tham số gate**: trang `/confirm` thực thi pending operation, agent gọi lại cùng key. Năng lực 1 vẫn đủ (`confirm_url`, `details.confirmation_id` ở trạng thái pending, `continuation`). Cần sửa dòng đó trong v2 cho khớp.
   - **Lịch khả dụng theo mốc của v2:** tên và schema của 17 tool có từ v0.1. `upload_file` và `aggregate_records` trả `capability_unavailable` (có remediation, không giả thành công) cho tới v0.2; làn PII ở v0.3. Trước v0.3, `meta.pii.mode=full` chỉ được dùng với dữ liệu demo không có PII thật.
