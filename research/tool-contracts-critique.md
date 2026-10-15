<!-- SPDX-License-Identifier: Apache-2.0 -->
# Phản biện bản nháp hợp đồng tool MCP của xT

Đối tượng review: `research/tool-contracts-draft.md` (6550 dòng, 18 tool, codex gpt-6.1-sol). Ngày: 15/10/2026. Vai trò: CRITIC.
Căn cứ: `rea-lessons.md`, `pain-points.md`, `connector-requirements.md`, `spike-mcp-apps.md`, `red-team.md`, `sklite-lessons.md`. "Lx" là số dòng trong bản nháp.

Kết luận chung: phần lõi an toàn (CAS, idempotency, receipt, deny > allow, untrusted marker, effects → annotations) chắc tay và nên giữ. Nhưng xét từ góc nhìn **agent thật phải chọn tool và ghép thành workflow**, bản nháp có 4 blocker:
1. Bằng chứng "người thật xác nhận" chưa có đường tạo ra mà LLM không giả được.
2. Không có tool chuyển trạng thái Record (động cơ lifecycle, 1 trong 3 động cơ lõi).
3. Không có upload file.
4. Không có tổng hợp số liệu phía server.

Ngoài ra, việc gate quá tay ở L2 làm agent gần như vô dụng trong demo flagship.

---

## A. Đo kích thước schema

Cách đo: tách 18 khối JSON contract, resolve mọi `$ref` qua registry `$id`, bundle `$defs` đã dùng vào từng schema (đúng như §3.1 L67 mô tả generator), rồi serialize JSON minified (UTF-8). Cột "compact" là bản inline toàn bộ, đã bỏ `$schema`, `$id`, `format`, `uniqueItems`, `minLength`, `default`. Ngân sách REA: ≤ 13.312 byte cho mỗi input schema (`rea-lessons.md` L41).

| Tool | Input bundled (B) | Output bundled (B) | In+Out (B) | Input compact (B) | Input / 13.312 | Mô tả property |
|---|---:|---:|---:|---:|---:|---:|
| inspect_workspace | 658 | 4.069 | 4.727 | 432 | 5% | 0 |
| describe_model | 378 | 3.137 | 3.515 | 264 | 3% | 0 |
| describe_process | 384 | **7.185** | 7.569 | 268 | 3% | 0 |
| search_records | 1.499 | 3.110 | 4.609 | 1.149 | 11% | 0 |
| get_record | 554 | 2.385 | 2.939 | 425 | 4% | 0 |
| create_record | 953 | 4.440 | 5.393 | 686 | 7% | 0 |
| update_record | 1.283 | 4.440 | 5.723 | 1.080 | 10% | 0 |
| delete_record | 664 | 2.084 | 2.748 | 523 | 5% | 0 |
| submit_request | 994 | 4.710 | 5.704 | 866 | 7% | 1 |
| decide_request | 1.320 | 3.707 | 5.027 | 1.157 | 10% | 0 |
| create_task | 1.396 | 5.398 | 6.794 | 1.154 | 10% | 1 |
| update_task | 1.997 | 5.398 | 7.395 | 1.755 | 15% | 0 |
| get_my_inbox | 806 | 2.434 | 3.240 | 531 | 6% | 0 |
| start_process | 1.373 | 4.065 | 5.438 | 1.106 | 10% | 0 |
| get_process_status | 718 | 4.801 | 5.519 | 510 | 5% | 0 |
| propose_model_change | 3.009 | 5.204 | 8.213 | 2.626 | 23% | 0 |
| propose_process_change | **6.882** | 5.206 | **12.088** | 7.728* | 52% | 0 |
| inspect_history | 939 | 4.695 | 5.634 | 715 | 7% | 0 |
| **Tổng** | **25.807** | **76.468** | **102.275** | **22.975** | | **2** |

\* Bản inline của `propose_process_change` lớn hơn bản bundled vì `ProcessStep` bị lặp lại khi inline.

Toàn bộ `tools/list` gồm name, description và input bundled là ≈ **28,6 KB, khoảng 9k token** (ước 3,2 B/token). Nếu host gửi kèm `outputSchema` thì lên ≈ **105 KB, khoảng 33k token**.

Nhận định:
- **Kích thước không phải vấn đề.** Mọi schema đều dưới ngân sách REA, lớn nhất là 52%.
- **Vấn đề thật là ngữ nghĩa.** 18 tool chỉ có 2 property có `description`. Các tool `decide_request` (L3965), `update_task` (L4398) và cả hai tool `propose_*` (L5096, L5336) đều dùng `allOf`/`if-then-else`/`not` ở root.
- **Với LLM local,** 9k token tool đã vượt context mặc định của Ollama ở nhiều phiên bản (thường 2–4k, tùy bản). Khi đó prompt bị cắt âm thầm. Model 4–8B sẽ chọn sai tool trước cả khi kịp hiểu schema.

---

## B. Chạy thử 3 workflow bằng chuỗi tool-call

### B1. W4 Tạm ứng công tác 8 triệu (flagship, `pain-points.md` L239–255)

| # | Gọi | Vấn đề |
|---|---|---|
| 1 | `inspect_workspace(kinds=[model,process])` | Ổn. Nhưng không liệt kê được policy và actor (enum `kinds` chỉ có workspace/model/process, L2554). |
| 2 | `describe_model(advance_request)` | Ổn. Cần lấy `revision` để dùng cho bước 3. |
| 3 | `create_record(model_id, expected_model_revision, values=[{field:"amount",value:"8000000"},…])` | Agent nhỏ khó tự biết "tạo đề xuất" thì dùng `create_record` (L79). Mảng `FieldValue` khó sinh hơn object. Agent dễ gửi `8000000` dạng number trong khi tiền phải là string (L67, `Value` cho phép number ở L134). |
| 4 | Đính kèm hóa đơn hoặc kế hoạch công tác | **Không có tool nào.** Document cần `blob_ref` (L6470) nhưng không tool nào tạo được blob. |
| 5 | `submit_request(request_ref, expected_revision, process_ref, expected_process_revision, key)` | Caller là agent nên theo L3844 phải có `approval_ref`. Muốn có thì phải đi "đường bootstrap" qua phiên human của app (L85), mà server không phân biệt được phiên đó (xem F1). Agent còn phải tự chọn `process_ref`, trái tinh thần "requester không chỉ định tuyến". Annotation là destructive và openWorld, nên Claude luôn hỏi xác nhận. |
| 6 | Trưởng phòng: `get_my_inbox` → `get_record` → `inspect_history` | Ổn. |
| 7 | `decide_request(..., human_confirmation_ref)` | Ref bắt buộc (L3961) nhưng **không tool hay resource nào tạo ra nó**. Lỗi `human_confirmation_required` chỉ nói "mở màn hình Tầng 2" (L6406) mà không có URI hay continuation. |
| 8 | Quá SLA thì nhắc và leo thang | Do engine chạy. Ổn. |
| 9 | Chi tiền, ghi Ledger | Do action handler của process. Ổn về nguyên tắc, nhưng thủ quỹ xác nhận "đã chi" ở bước nào thì chưa rõ: Decision chỉ có approve/reject/return/delegate. |
| 10 | Hoàn ứng: OCR hóa đơn → đối trừ | Lại vướng lỗi upload. Không có đường đọc nội dung file cho agent: resource thường không được model đọc trực tiếp trên ChatGPT/Claude. |
| 11 | Nhắc hoàn ứng sau N ngày | Schedule chỉ tạo được ở dạng draft, "chưa được engine kích hoạt" (IAM của `create_record`), và **không có đường kích hoạt**. |

Kết quả: đúng ở bước 6 và 8, **gãy ở bước 4, 5, 7, 10 và 11**. Demo bước 1 và bước 5 (OCR) trong `pain-points.md` không chạy được.

### B2. W2 Giao việc có SLA, nghiệm thu, task định kỳ

| # | Gọi | Vấn đề |
|---|---|---|
| 1 | Tìm "anh Bình bên IT" để lấy `assignee_ref` | **Không có danh bạ actor.** `search_records(model_id="actor")` có hợp lệ hay không thì bản nháp không nói. |
| 2 | `create_task(title, assignee_ref, priority, sla{policy_ref, policy_revision, due_at, timezone}, key)` | `sla` là bắt buộc (L4221) và bắt buộc đủ cả 4 trường (L1529). Không có cách khám phá SLA policy. Không có `parent_ref`/checklist/`depends_on` cho việc tách chỉ đạo thành nhiều task (W2). Annotation openWorld=true và trần L3 chỉ vì *có thể* nhắc ra ngoài. |
| 3 | Người làm: `update_task(transition → in_progress)` | Ổn. Theo L50 thì đây là L2, nên agent cần approval cho cả việc **tự chuyển trạng thái việc của mình**. |
| 4 | Báo blocker, cập nhật tiến độ | Comment phải đi qua `create_record(model "comment")`. Không agent nào tự đoán ra. |
| 5 | Nộp kết quả, người giao nghiệm thu hoặc trả lại | Enum state cứng `todo/in_progress/blocked/done/cancelled` (L4358) **không có review/accepted/returned**, trái với "nghiệp vụ là dữ liệu". |
| 6 | Task định kỳ hằng tuần | Không có `recurrence`. Schedule thì không kích hoạt được. |
| 7 | "Đánh dấu đã đọc" thông báo | Không có tool. Inbox phình mãi (L4583: đọc không mark-read). |

### B3. W3 Offboarding "Anh B nghỉ từ thứ 6"

| # | Gọi | Vấn đề |
|---|---|---|
| 1 | Tìm B | Lại thiếu danh bạ actor. |
| 2 | Liệt kê mọi thứ B đang nắm (task mở, khách hàng, tài liệu) | `get_my_inbox` chỉ xem được của chính mình. `search_records` thì phải gọi riêng từng model và cần biết model nào có `owner_ref`. **Không có tìm kiếm xuyên model.** |
| 3 | `start_process(offboarding, subject_ref=employee)` | L3 nên cần `approval_ref`. Lại vướng đường bootstrap. |
| 4 | Chuyển giao N task bằng `update_task(assign)` × N | N lời gọi L2, mỗi lời gọi một approval. Không có batch. |
| 5 | Chuyển hồ sơ nhân sự sang trạng thái `offboarding`/`terminated` | **Không tool nào chuyển state Record.** `update_record` cấm state (L3506). ModelDefinition (L805–881) cũng không khai báo state machine. |
| 6 | Xóa hoặc ẩn danh PII theo Đ.25 Luật 91/2025 (`red-team.md` Q4) | `delete_record` chỉ xóa mềm, "không hard-delete" (L3575). Không có action ẩn danh. |

**Tổng kết độ phủ:** W1/W4/W7 gãy ở bước xác nhận và đính kèm. W2 gãy ở actor, SLA policy, nghiệm thu và định kỳ. W3/W5 gãy ở chuyển trạng thái Record. W6 gãy ở upload và soạn từ mẫu. W8 gãy vì không có aggregation (LLM tự cộng số, vi phạm "không tự chế số", L6513). Lịch chạy báo cáo cũng không có trigger timer cho process (ProcessDefinition không có `trigger`).

---

## C. Các phát hiện

### Blocker

**F1 — `human_confirmation_ref` và "phiên human của app" chưa có đường tạo ra mà LLM không giả được.** *Blocker. Thuộc an toàn, thiếu MCP Apps.*
- **Bằng chứng:**
  - L83 và L85: bằng chứng do "UI Tầng 2" tạo ra, bootstrap qua "phiên human của app".
  - `spike-mcp-apps.md` §1: UI gọi `tools/call` qua host, chung kết nối và token với model. `visibility:["app"]` do **host** áp dụng, server không kiểm được.
  - `connector-requirements.md`: tool call của app được proxy qua backend Claude.
  - Vì vậy, một tool "confirm" gọi từ iframe là không thể phân biệt với lời gọi của model đã bị prompt injection. Câu hỏi mở 14 (L6548) tự thừa nhận điểm này.
- **Đề xuất:**
  - (a) Confirmation chỉ được tạo trên **trang xác nhận do xT sở hữu**, ví dụ `https://<xt>/confirm/{nonce}` sau APISIX. Trang đăng nhập Keycloak bằng **client trình duyệt riêng**, khác `azp` của connector, và tùy chọn step-up WebAuthn cho L3. Trang hiển thị đúng plan, digest và effects. Server mint `confirmation_ref` gắn session trình duyệt.
  - (b) Mọi lỗi `approval_required` hoặc `human_confirmation_required` trả về `details.confirm_url` và `details.confirmation_ref` ở trạng thái pending. Đi kèm continuation để agent gọi lại đúng tool đó với cùng `idempotency_key`.
  - (c) Tận dụng các kênh có sẵn: nếu host hỗ trợ elicitation dạng URL của MCP thì dùng (hỗ trợ trên ChatGPT/Claude chưa xác nhận, phải test). MCP App chỉ hiển thị thẻ có nút `ui/open-link` tới URL đó. Host nào cũng còn link dạng text.
  - (d) "Human session" = token có `azp` thuộc client trình duyệt của xT (xT Desk/web). Mọi token có `azp` là ChatGPT/Claude/Goose được coi là `invoking_actor=agent(azp)` và `on_behalf_of=sub`.
  - Cách này còn cho Tầng 2 một UI do xT sở hữu, đúng khuyến nghị 1 của `red-team.md`.

**F2 — Thiếu tool chuyển trạng thái Record (động cơ lifecycle).** *Blocker. Thuộc tính phổ quát.*
- **Bằng chứng:**
  - `update_record` cấm state (IAM L3506). ModelDefinition không có states/transitions (L805–881).
  - `pain-points.md` L175: 3 động cơ lõi là Request, Task và **Record lifecycle**. W3/W5 phụ thuộc trực tiếp.
- **Đề xuất:**
  - Thêm `states[]`, `transitions[{from,to,guard_policy_ref,required_fields,human_gate}]` vào ModelDefinition.
  - Thêm `transition` (tham số `to_state` + `reason`) vào `update_record`, loại trừ lẫn nhau với `patch`. Hoặc tách thành tool riêng `transition_record`, nên tách vì outcome và authority khác nhau, đúng tiêu chí của REA.
  - Mức L do transition khai báo, không cố định theo tool.

**F3 — Không có upload file và không đọc được nội dung file.** *Blocker. Thuộc W4/W6/W7, demo OCR.*
- **Bằng chứng:** Document cần `blob_ref` (L6470). §8 nói Blob là resource object storage nhưng không tool nào tạo. `get_record` không trả nội dung.
- **Đề xuất:**
  - Thêm tool `upload_file`, L1, gồm 2 mode:
    - (i) `content_text`/`content_base64` ≤ giới hạn công bố trong `inspect_workspace.limits`, dành cho văn bản agent soạn.
    - (ii) `mode=upload_url`: trả URL tải lên một lần qua APISIX tới SeaweedFS, dành cho người dùng đính kèm.
  - Kết quả: `blob_ref`, `content_hash`, `scan_status`, có quét ClamAV.
  - Đọc nội dung: `get_record(ref=document|file, include=["text"])` trả text đã trích hoặc OCR, có phân trang và gắn untrusted.

**F4 — Không có tổng hợp số liệu phía server.** *Blocker cho W8, demo bản tin.*
- **Bằng chứng:** toán tử của `Filter` (L1349) không có count/sum/group. Prompt `weekly_business_report` (L6501) bắt agent tự gom dữ liệu bằng `search_records`. LLM cộng tiền là đúng thứ `red-team.md` Q3 cảnh báo.
- **Đề xuất:** thêm tool `aggregate_records`:
  - Input: `model_id`, `filters`, `group_by[] ≤ 3`, `metrics[{op: count|sum|avg|min|max, field}]`, `as_of`/`snapshot_ref`.
  - Output: các ô số liệu kèm `lineage_ref` (truy vấn và revision) để `explain` truy ngược. Dùng decimal string.
  - Snapshot kỳ đã chốt đọc qua `get_record`.

### Major

**F5 — Gate L2 quá tay khiến agent vô dụng và người duyệt kiệt sức.** *Major. Thuộc tự chủ.*
- **Bằng chứng:** L50 yêu cầu agent có human approval cho mọi L2. Các lời gọi bị ảnh hưởng: `submit_request` (L3844), `update_task` mọi operation, `start_process`, `create_task` giao cho người khác. `approval_ref` lại là một Request đã duyệt (L85), tức thêm 3 tool (`create_record`, `submit_request`, `decide_request`) và một người duyệt **cho mỗi hành động**. Trong khi W1 nói rõ "agent làm được: chuyển câu chat thành đề xuất".
- **Đề xuất:** định nghĩa lại các mức:
  - L1: agent tự làm trong phạm vi quyền của chính người được đại diện, có audit. Gồm tạo/sửa draft, submit đề xuất *của chính mình*, tự chuyển trạng thái việc của mình, comment.
  - L2: cần `confirmation_ref` của **chính người được đại diện** (một lần bấm qua F1). Gồm giao việc cho người khác, start process ảnh hưởng người khác, sửa field có tác động.
  - L3: cần confirmation, cộng người duyệt thứ hai theo policy. Gồm xóa, đổi định nghĩa, chi tiền, ký, gửi ra ngoài, thu hồi quyền.
  - Request-approval chỉ dùng khi policy nghiệp vụ đòi đa bên.
  - Hard gate duy nhất không bao giờ hạ được: `decide_request` (chỉ người duyệt), ký, lệnh chi, activate định nghĩa.

**F6 — Ánh xạ annotations lệch REA, phần lớn tool bị gắn destructive/openWorld.** *Major. Thuộc REA alignment, UX của host.*
- **Bằng chứng:**
  - L42: `destructiveHint` có thêm `replaces_state || changes_definitions`.
  - REA chỉ dùng `mayDiscardData || executesForeignCode || mutatesDevice` (`rea-lessons.md` L106).
  - Kết quả đo: 6/18 tool destructive (`update_record`, `delete_record`, `submit_request`, `decide_request`, `update_task`, `start_process`) và 5/18 openWorld (thêm `create_task`). Claude "destructive tools always prompt" (`connector-requirements.md` A5).
  - `create_task` mang openWorld=true và trần L3 chỉ vì *có thể* nhắc ra ngoài (L4257).
- **Đề xuất:**
  - xT giữ mọi revision theo kiểu append-only, nên sửa có lịch sử là additive. Dùng `destructiveHint = may_discard_data || executes_foreign_code`. Các tool `delete_record`, `decide_request` (quyết định không đảo ngược) và `start_process` (có thể thu hồi quyền) vẫn true theo effects.
  - Tách "kênh nội bộ" (inbox xT, kênh nhân viên đã cấu hình bởi tenant) thành effect `notifies_internal`, closed-world.
  - "Gửi ra ngoài tổ chức" chỉ tồn tại trong action handler của process có human gate. Nhờ vậy `create_task`, `update_task`, `submit_request` có openWorld=false.
  - Ghi trong contract test rằng chỗ lệch REA (nếu còn) là quyết định có chủ ý.

**F7 — Schema không học được với model nhỏ.** *Major. Thuộc khả năng học của catalog.*
- **Bằng chứng:**
  - 0/18 tool có mô tả property (phần A).
  - Description 63–103 ký tự, không có "dùng khi / không dùng khi". REA bắt buộc có mục này cho skill (`rea-lessons.md` L137).
  - Có `allOf`/`if-then-else`/`not` ở root (L3965, L4398, L5096, L5336). Một số API LLM từ chối hoặc bỏ qua combinator ở root; grammar của llama.cpp/Ollama chỉ hỗ trợ một phần.
  - Mảng `values: [{field,value}]` (L157) và `patch: [{operation,field,value}]` khó sinh hơn object.
  - Yêu cầu `uniqueItems` (L179) không chặn được trùng field, chính bản nháp thừa nhận ở L67.
- **Đề xuất:**
  - (a) Mỗi property có `description` ≤ 120 ký tự. Mỗi tool có 1–2 câu "Dùng khi… Không dùng cho… → dùng X".
  - (b) Bỏ combinator ở root. Thay bằng enum `operation` cộng kiểm tra ở server, trả `invalid_request` có `remediation` chỉ đúng trường.
  - (c) `values` và `set` dạng **object** `{field: Value}` có `propertyNames.pattern`. Server vẫn validate theo schema động đã pin. Object JSON tự loại bỏ chuyện trùng field. Poka-yoke không mất vì enforcement nằm ở server.
  - (d) Profile compact: inline `$ref`, bỏ `$schema`/`$id`/`format`/`uniqueItems`, test pass mọi example (`rea-lessons.md` L239).
  - (e) Description bằng tiếng Anh ngắn để định tuyến tool, example bằng tiếng Việt. Chốt bằng eval, không chốt theo cảm tính.

**F8 — Quá nhiều "plumbing" bắt buộc: workspace_id, expected_*_revision, XRN đầy đủ.** *Major. Thuộc khả năng học.*
- **Bằng chứng:**
  - `workspace_id` bắt buộc ở 17/18 tool.
  - `create_record` bắt buộc `expected_model_revision` (L3300). `submit_request` bắt buộc `process_ref` và `expected_process_revision`.
  - `decide_request` có 9 trường bắt buộc. Hai tool `propose_*` mỗi tool 9 trường, kể cả `migration_plan`/`rollback_plan` dạng prose (L5090).
  - Mỗi ref phải mang tenant (`xrn:t_demo:…`) dù tenant lấy từ token.
- **Đề xuất:**
  - `workspace_id` tùy chọn, mặc định theo phiên.
  - `expected_model_revision` và `expected_process_revision` tùy chọn: server pin bản active và trả về revision đã pin.
  - Chỉ giữ CAS bắt buộc nơi có tranh chấp thật: `expected_revision` của update/decide/task.
  - `process_ref` suy từ loại Request.
  - Input chấp nhận ref rút gọn `{model}:{id}`. Output luôn trả XRN đầy đủ.
  - `migration_plan`/`rollback_plan` tùy chọn vì server tự sinh plan (L87).
  - Đổi bắt buộc sang tùy chọn là không breaking. Làm ngay ở v0.1.

**F9 — Làn PII và `llm_egress` vắng mặt trong contract.** *Major. Thuộc an toàn, pháp lý.*
- **Bằng chứng:** `red-team.md` Q4 coi đây là rủi ro "Rất cao" và là điểm nguyên gốc. Bản nháp chỉ có `sensitive: boolean` trong FieldDefinition (L742). Output không có dấu che.
- **Đề xuất:**
  - `FieldDefinition.pii: none|basic|sensitive`.
  - Policy tenant `llm_egress: full|masked|local_only` áp theo `azp`, hiển thị trong `inspect_workspace`.
  - Output field có `redaction: masked|tokenized|omitted` cùng `token` (ví dụ `NV-0042`).
  - **Input phải từ chối giá trị đã che hoặc token** khi ghi lại, nếu không agent sẽ ghi đè "Nguyễn V*** A" vào DB.
  - MCP App lấy giá trị thật bằng `connectDomains` tới REST xT (host có tôn trọng hay không thì chưa kiểm chứng).

**F10 — Marker untrusted nằm ở `_meta`, model thường không thấy.** *Major. Thuộc an toàn.*
- **Bằng chứng:** L93 đặt `_meta.provenance.untrusted_content`. Host thường không đưa `_meta` vào ngữ cảnh model. Nội dung do agent viết (`reason`, `rationale`, comment) hiển thị cho người duyệt cũng có thể là kênh thao túng người duyệt (ví dụ "GĐ đã đồng ý miệng").
- **Đề xuất:**
  - Đặt dữ liệu doanh nghiệp dưới khóa `data` trong `structuredContent`, có `"untrusted": true` ở mức result. Text content mở đầu bằng nhãn ngắn. Giữ `_meta` để tương thích REA.
  - Mọi text tự do trong Request/Decision/Comment lưu `authored_by_kind: human|agent`. UI duyệt bắt buộc hiển thị nhãn "do agent soạn".

**F11 — Thiếu danh bạ actor/policy và tìm kiếm xuyên model.** *Major. Thuộc tính phổ quát.*
- **Bằng chứng:** `inspect_workspace.kinds` (L2554) không có actor/policy. `search_records` bắt buộc `model_id`. B2.1, B3.1 và B3.2 gãy.
- **Đề xuất:**
  - `search_records.model_id` tùy chọn, thêm `query` full-text trên các model được phép, gồm actor, task, request và document.
  - Ghi rõ rằng primitive `task`, `request`, `actor`, `policy` search được bằng `model_id` (IAM chuyên biệt vẫn áp dụng).
  - Thêm `inspect_workspace.kinds += policy, actor_directory` trả tóm tắt.

**F12 — Task state cứng, thiếu nghiệm thu, cấp cha–con, định kỳ, phụ thuộc.** *Major. Thuộc W2.*
- **Bằng chứng:** enum ở L4358. `create_task` không có parent/recurrence. `sla` bắt buộc đủ 4 trường (L1529, L4221).
- **Đề xuất:**
  - State lấy từ policy loại task (`describe_model(task_type)`), có `review`.
  - Thêm `parent_ref`, `depends_on[]`, `recurrence` (RRULE, kích hoạt qua engine).
  - `sla` tùy chọn, mặc định theo loại task. Agent không cần biết `policy_revision`.
  - Thêm operation `pause`/`resume` hoặc đưa pause về transition.

**F13 — Schedule/Notification không có đường hoạt động.** *Major. Thuộc W2/W8.*
- **Bằng chứng:** Schedule tạo bằng `create_record` chỉ ở dạng draft, không có cách kích hoạt. Không có mark-read (L4583). ProcessDefinition không có `trigger`.
- **Đề xuất:**
  - Việc định kỳ là **trigger của process** (`trigger: manual|timer(rrule)|event(event_type)`), kích hoạt cùng activation của proposal. Không cần CRUD Schedule.
  - Mark-read không gộp vào `get_inbox`, vì sẽ làm tool L0 có ghi. v1.0 thêm tool `ack_notifications`, L1.
  - Nhắc ad-hoc ("nhắc anh B") ở v1.0 dùng `notify` nội bộ, L1/L2.

**F14 — Output đóng `additionalProperties:false` làm mọi thay đổi additive đều thành breaking.** *Major. Thuộc khả năng phát triển contract.*
- **Bằng chứng:** L67 "mọi object input/output đều additionalProperties:false". Client MCP được khuyến nghị validate `structuredContent` theo `outputSchema`, nên thêm một field output ở v0.2 sẽ làm client strict bị lỗi.
- **Đề xuất:** input đóng, **output mở** (bỏ `additionalProperties:false` ở output, giữ `required`). Contract test kiểm tra server không phát field ngoài schema của phiên bản hiện tại. Đây là điều kiện để "ship v0.1 nhanh mà không phá contract sau".

**F15 — Phân trang offset + snapshot vật chất hóa là đắt và khó đổi về sau.** *Major. Thuộc over-engineering.*
- **Bằng chứng:** L99–103: snapshot cố định tập dữ liệu, sort, as_of theo principal, có TTL và thu hồi khi đổi quyền.
- **Đề xuất:** contract chỉ dùng `cursor` opaque (input) và `next_cursor` (output). Server tự quyết nhét keyset, as_of hay snapshot vào cursor. v0.1 dùng keyset + `as_of`. Đổi `{offset,snapshot}` sang cursor về sau là breaking, nên phải chốt ngay.

**F16 — Ba loại hiện vật ủy quyền chồng nhau: `approval_ref`, `human_confirmation_ref`, `preview_digest` + `preview_token`.** *Major. Thuộc khả năng học, over-engineering.*
- **Bằng chứng:** L83–87. Hai tool `propose_*` bắt buộc **cả** digest lẫn token (L5096).
- **Đề xuất:**
  - Chỉ giữ hai khái niệm:
    - `confirmation_ref`: người X xác nhận đúng plan digest D (qua F1).
    - Request-approval: quy trình nghiệp vụ đa bên.
  - Preview chỉ cần một `preview_token` đã ký, digest nằm trong token.
  - Chỉ còn một tên tham số cho mọi tool có gate là `confirmation_ref`. Bỏ `approval_ref` khỏi input; server tự tìm Request đã duyệt gắn với cùng plan digest.

**F17 — `delete_record` bắt buộc `approval_ref`, kể cả khi xóa draft của chính mình; không có xóa thật hay ẩn danh.** *Major. Thuộc W3, pháp lý.*
- **Bằng chứng:** required ở L3611. L3575.
- **Đề xuất:**
  - `approval_ref`/`confirmation_ref` tùy chọn, policy quyết định. Xóa draft của mình là L1.
  - Erase/anonymize theo retention là action của process (offboarding), có human gate và giữ tombstone với hash.

### Minor

**F18 — Định dạng ID/XRN quá chặt.** *Minor, nhưng breaking nếu để sau.*
- **Bằng chứng:** pattern ở L130 là `^[a-z][a-z0-9_]*$` cho mọi segment, không nhận UUID, ULID hay chữ hoa.
- **Đề xuất:** chốt ngay định dạng `{prefix}_{ulid lowercase}`, hoặc nới pattern thành `[a-z0-9][a-z0-9_-]*`.

**F19 — Tiền/decimal nhận cả number.** *Minor.*
- **Bằng chứng:** `Value` cho phép number (L134), trong khi L67 yêu cầu decimal là string.
- **Đề xuất:** server chấp nhận number nguyên chính xác cho field decimal rồi chuẩn hóa. Từ chối float có phần lẻ nhị phân, kèm remediation "gửi dạng chuỗi".

**F20 — Ủy quyền.** *Minor.*
- **Đề xuất:** ghi rõ `delegate_to` phải là actor `kind=human` và không được là người đề nghị. SoD so sánh theo **effective human**, không theo invoking actor.

**F21 — Thiếu rút lại Request.** *Minor.*
- **Bằng chứng:** vòng đời Request ở L6469 không có `withdrawn`.
- **Đề xuất:** `submit_request.action: submit|withdraw` (mặc định submit). Rút lại là L1 cho requester.

**F22 — Tên tool chưa nhất quán.** *Minor. Thuộc naming, REA.*
- **Bằng chứng:** dùng 4 động từ đọc là `inspect_`, `describe_`, `get_`, `search_`. Riêng `get_my_inbox` có đại từ.
- **Đề xuất:**
  - `get_*` cho đọc theo ref, `search_*`/`aggregate_*` cho tập hợp, `describe_*` cho định nghĩa.
  - Đổi `inspect_history` thành `get_history`, `get_my_inbox` thành `get_inbox` (luôn là của chính mình).
  - Giữ `inspect_workspace`, tương đương `binary_session` của REA.

**F23 — Routing sai giữa `create_record` và `create_task`.** *Minor.*
- **Bằng chứng:** L3266 cấm tạo Task bằng `create_record`.
- **Đề xuất:** lỗi định tuyến sai phải trả `continuation` gọi được ngay sang đúng tool, giống mẫu `resource_constraint` của REA. Description của `create_record` nêu rõ "Task → create_task, gửi đề xuất → submit_request".

**F24 — Chưa có `explain` cho lỗi bị từ chối hoặc bị chặn.** *Minor, nâng lên Major nếu demo Q3.*
- **Bằng chứng:** facet `explain` (L5524) chỉ nhận `subject_ref`. Lỗi `access_denied`/`rule_violation` không có ref để truy vết.
- **Đề xuất:**
  - Mọi lỗi và mọi ô số liệu có `explain_ref` (trace hoặc lineage).
  - v0.1 dùng `inspect_history(subject_ref=explain_ref, facets=[explain])`.
  - v1.0 tách thành tool `explain(ref)` riêng: lý do deny, rule đã chạy, policy revision, lineage của con số. Câu hỏi "vì sao" là outcome khác "lịch sử", đúng tiêu chí REA.

**F25 — Chưa liên kết MCP Apps với `ui://`.** *Minor về contract, Major về điểm Tầng 2.*
- **Bằng chứng:** ToolContract (L22) không có trường UI. `spike-mcp-apps.md` §1 yêu cầu `_meta.ui.resourceUri`.
- **Đề xuất:**
  - Thêm `ui_resource?: "ui://xt/<view>"` vào ToolContract, sinh ra `_meta.ui`.
  - Các view: `get_inbox → ui://xt/inbox`, `get_record` (Request) → `ui://xt/request-card` (có nút mở `confirm_url`), `get_process_status`/`get_record` (instance) → `ui://xt/process`, `aggregate_records → ui://xt/report`, `create_task`/`update_task → ui://xt/task-board`, `propose_* → ui://xt/diff-preview`.
  - Không thêm tool app-only cho thao tác ghi. UI gọi đúng tool public, còn confirmation đi theo F1.
  - Test có `ui://` cho mọi URI đã đăng ký, mỗi URI có `csp`.

**F26 — `decide_request` gánh hệ quả activation nên mọi duyệt nghỉ phép đều mang effects L3.** *Minor.*
- **Bằng chứng:** L4068 có `changes_definitions:true`. Câu hỏi mở 3 (L6539).
- **Đề xuất:** chấp nhận, vì `decide_request` luôn cần người nên prompt là đúng. Nhưng `_meta.xt.effective_level` phải hiển thị mức thực (L2 cho Request thường) để UI không cảnh báo sai.

**F27 — Prompt và skill nhiều hơn mức cần cho v0.1.** *Minor. Thuộc over-engineering.*
- **Bằng chứng:** 11 prompt và 9 skill (L6491–6512).
- **Đề xuất:** v0.1 chỉ cần 3 prompt (`prepare_request`, `review_pending_requests`, `triage_my_work`) và 2 skill (`xt-core`, `xt-approvals`), cộng một **hồ sơ "core-8" cho model nhỏ**: danh sách tool nên bật ở client local. Catalog server vẫn đầy đủ theo REA. Kèm hướng dẫn `num_ctx ≥ 16k`.

---

## D. Cắt gì cho v0.1 mà không phá contract về sau

Nguyên tắc: **tên tool và ngữ nghĩa phải chốt ngay. Input tối giản và phần lớn tùy chọn. Output mở. Phân trang dùng cursor.** Thêm tool hoặc thêm trường tùy chọn về sau đều không breaking.

| Cắt hoặc hoãn | Lý do |
|---|---|
| Snapshot pagination, coverage 5 trường (`source_exhaustive`, `residual_unknowns`…) | Giữ `next_cursor`, `total|null`, `truncated`. Output mở thì thêm sau được (F14, F15). |
| `preview_digest` song song với token | Giữ 1 token đã ký (F16). |
| `migration_plan`/`rollback_plan` bắt buộc | Tùy chọn, vì server sinh plan. |
| `propose_process_change` dạng graph đầy đủ (12 KB) | v0.1 ship process mẫu từ repo. Agent chỉ đề xuất model. v1.0 mở lại với `operations` có giới hạn (`set_threshold`, `set_approver_policy`, `set_sla`, `add_decision_step`) trước khi mở cả graph. |
| `delete_record` | v0.1 chưa cần cho demo. Draft hết hạn hoặc bị rút lại. |
| `get_process_status` | Gộp vĩnh viễn vào `get_record(ref=process_instance)`, với `detail=steps`. |
| Receipt recovery sau cửa sổ giữ | v0.1 công bố retention cố định, ví dụ 7 ngày, và từ chối key cũ. |
| Bộ prompt/skill đầy đủ | F27. |

Giữ nguyên và không cắt: CAS cho update/decide/task, `idempotency_key` (cho phép server tự sinh nếu thiếu ở L1 và trả lại trong receipt), deny > allow, RLS, untrusted marker, effects → annotations (sinh tự động), lỗi có remediation và continuation.

---

## E. Catalog đề xuất

### v0.1: 17 tool, đủ cho 6 màn demo trong `red-team.md` khuyến nghị 6 (trừ IT/OT)

| # | Tool | Mục đích một dòng | L tối đa |
|---|---|---|---|
| 1 | `inspect_workspace` | Phiên: tôi là ai, tenant, egress mode, danh mục model/process/policy, tool availability, limits. | L0 |
| 2 | `describe_model` | Field, kiểu, PII, rule, **state machine** của một model hoặc loại Request/Task. | L0 |
| 3 | `describe_process` | Bước, tham số, SLA, human gate, trigger của một quy trình. | L0 |
| 4 | `search_records` | Lọc có kiểu trong một model, **hoặc** full-text xuyên model (gồm actor, task, request, document). | L0 |
| 5 | `aggregate_records` | count/sum/avg theo group_by phía server, kèm lineage. | L0 |
| 6 | `get_record` | Đọc mọi XRN (record, request, task, process instance, document text). | L0 |
| 7 | `create_record` | Tạo Record, draft Request/Document, Comment. | L1 |
| 8 | `update_record` | Sửa field (`set`) **hoặc** chuyển trạng thái (`transition`) theo state machine. | L2 |
| 9 | `upload_file` | Tạo blob từ text/base64 nhỏ hoặc URL tải lên một lần, trả `blob_ref` + hash + kết quả quét. | L1 |
| 10 | `submit_request` | Gửi hoặc rút đề xuất; tuyến duyệt do policy tính. | L1 |
| 11 | `decide_request` | Duyệt/từ chối/trả lại/ủy quyền, **bắt buộc `confirmation_ref` của người duyệt**. | L3 |
| 12 | `create_task` | Giao việc (parent, depends_on, recurrence, SLA mặc định theo loại). | L1/L2 |
| 13 | `update_task` | assign/transition/adjust_sla trên một task. | L2 |
| 14 | `get_inbox` | Việc, đề xuất cần tôi xử lý và thông báo của tôi. | L0 |
| 15 | `start_process` | Chạy quy trình đã duyệt trên một subject. | L2/L3 |
| 16 | `get_history` | Timeline, audit, facet `explain` (nhận `explain_ref` từ lỗi hoặc số liệu). | L0 |
| 17 | `propose_model_change` | Preview hoặc gửi đề xuất đổi model, kích hoạt sau khi người duyệt. | L1 |

Hồ sơ "core-8" cho model 4–8B (bật ở client): 1, 4, 6, 7, 10, 12, 13, 14. Ước ≤ 4k token với description đầy đủ, phải đo lại.

### v1.0: 22 tool

v0.1 cộng thêm:

| # | Tool | Mục đích |
|---|---|---|
| 18 | `explain` | Vì sao bị chặn hoặc từ chối, rule nào đã chạy, con số này từ đâu (tách khỏi `get_history`). |
| 19 | `propose_process_change` | Đề xuất đổi quy trình: operations có giới hạn, sau đó mở graph. |
| 20 | `delete_record` | Xóa mềm theo policy; erase/anonymize đi qua process. |
| 21 | `notify` | Nhắc hoặc thông báo nội bộ ad-hoc cho actor (closed-world). Gửi ra ngoài chỉ qua process. |
| 22 | `ack_notifications` | Đánh dấu đã đọc hoặc đã xử lý thông báo trong inbox. |

Cân nhắc qua eval, không thêm mặc định: `add_comment` riêng (nếu eval cho thấy agent không tìm ra đường `create_record(comment)`), và `transition_record` tách khỏi `update_record` (nếu nhầm lẫn giữa `set` và `transition` cao).

Các thứ **không thêm**: tool app-only cho thao tác ghi, `execute`/Code Mode (`red-team.md` §0.2), tool sinh theo model.

---

## F. Việc phải làm trước khi chốt contract

1. Prototype **trang xác nhận ngoài host** (F1). Không có nó thì không có demo "agent tự duyệt bị chặn, người bấm duyệt" đáng tin.
2. Eval chọn tool: 30 kịch bản (dương, âm, injection) × {Claude, GPT, Qwen 7B/8B qua Ollama với num_ctx 16k} × {catalog đầy đủ, core-8}. Đo tỷ lệ chọn đúng tool và tỷ lệ args hợp lệ ngay lần đầu, rồi dùng số đó để quyết F7/F8/F22.
3. Sửa generator: bundle + compact profile + **output mở**. Test kích thước và test mọi example pass compact schema (theo REA).
4. Sửa §8: ModelDefinition có states/transitions; ProcessDefinition có trigger; Task state lấy theo loại task.
