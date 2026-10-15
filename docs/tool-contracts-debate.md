<!-- SPDX-License-Identifier: Apache-2.0 -->
# Debate vòng 2: hợp đồng tool MCP của xT (AUTHOR trả lời CRITIC)

- Ngày: 15/10/2026.
- Vai AUTHOR: Opus 5.5, thay codex gpt-6.1-sol vì codex hết quota (đã ghi trong `research/incidents.log`).
- Đầu vào: `research/tool-contracts-draft.md` (bản nháp, 18 tool), `research/tool-contracts-critique.md` (27 phát hiện: 4 blocker, 13 major, 10 minor), `docs/architecture-v1.md`, `docs/demo-storyboard-v0.md`, `research/spike-local-llm.md`, `research/connector-requirements.md`, `research/rea-lessons.md`, `research/spike-mcp-apps.md`.
- Đầu ra: `docs/tool-contracts-v1.md`. Script kiểm chứng chạy ở `/tmp/xt-contracts-v1/`, bản sao lưu ở `research/tool-contracts-v1-build/` (`contracts.py`, `build.py`, `gen_doc.py`, `llm_probe.py`, kết quả `sizes.json`, `llm_probe.json`).

## Tổng kết

| Kết luận | Số lượng | Phát hiện |
|---|---:|---|
| ACCEPT | 9 | F4, F7, F10*, F14, F15, F18, F19, F20, F23 |
| ACCEPT-MODIFIED | 16 | F1, F2, F3, F5, F6, F8, F9, F11, F12, F13, F16, F17, F21, F22, F24, F25 |
| REJECT | 2 | F26, F27 |

\* F10 được chấp nhận phần cốt lõi, nhưng bác ý "chèn nhãn vào đầu text content" (lý do ở mục F10). Phần bác nhỏ nên vẫn xếp ACCEPT.

**Ba chỗ AUTHOR đi xa hơn CRITIC:**
1. Bỏ hẳn `confirmation_ref` khỏi input của tool. Lời gọi bị chặn sẽ được server "giữ chỗ". Khi người bấm trên trang xác nhận, chính trang đó thực thi. Agent chỉ gọi lại với đúng `idempotency_key` để nhận receipt. Nhờ vậy LLM không bao giờ cầm một bằng chứng nào.
2. Bỏ luôn `preview_token`. Đề xuất đổi model chỉ cần `base_revision` làm CAS.
3. Áp **đúng** công thức annotations của REA. Kết quả là v0.1 có 0 tool destructive và 0 tool openWorld, kể cả `decide_request`, vì quyết định này append-only và không xóa dữ liệu.

**Hai chỗ AUTHOR giữ lập trường của bản nháp, ngược ý CRITIC:**
1. `idempotency_key` vẫn **bắt buộc**, không cho server tự sinh.
2. Không tách tool `explain` ở v1.0.

**Bằng chứng mới:** probe `llm_probe.py` chạy trên qwen3.5:4b với catalog thật. Kết quả và giới hạn của probe ở cuối file.

---

## Blocker

### F1: `human_confirmation_ref` không có đường tạo mà LLM không giả được. **ACCEPT-MODIFIED**

CRITIC đúng về bản chất. `spike-mcp-apps.md` §1 và `connector-requirements.md` cho thấy `tools/call` từ iframe đi qua host với cùng token của kết nối, nên server không phân biệt được người bấm nút với model gọi tool. `architecture-v1.md` D8 đã chốt điều này: kênh người phải tách khỏi token agent. AUTHOR làm khác CRITIC ở bước (b).

**CRITIC đề xuất:** agent nhận `confirmation_ref` rồi gửi lại trong lời gọi sau.

**AUTHOR chốt:**
- Lời gọi thiếu xác nhận trả `confirmation_required`. Lỗi này kèm `confirm_url`, `confirmer`, `plan_digest`, `plan_summary`, `expires_at` và `continuation`.
- Server lưu một **pending operation** gồm tool, canonical args, key, digest và TTL. Đây là bản ghi kỹ thuật, không phải mutation nghiệp vụ.
- Khi người bấm "Đồng ý", **chính trang xác nhận thực thi** pending operation trong cùng transaction với việc tiêu thụ xác nhận.
- Agent gọi lại đúng lời gọi cũ, cùng key, và nhận receipt dạng replay.

**Lợi ích của cách này:**
- Agent không bao giờ cầm một chuỗi có giá trị ủy quyền, nên không có gì để rò hay bị injection tái sử dụng.
- Người duyệt không cần còn ngồi trong phiên chat. Ví dụ giám đốc bấm trên Telegram lúc khác vẫn có hiệu lực.

**Hòa giải với Desk:** `confirm_url` là route `/desk/confirm/{id}` của xT Desk.
- Desk dùng client Keycloak `xt-desk`: client của Desk backend, token người nằm ở server (khớp `architecture-v2.md`). Client này **không bao giờ** nằm trong danh sách DCR của connector.
- Chỉ token có `azp=xt-desk` cùng xác thực tươi (`max_age=0` + TOTP) mới được coi là "người". Mức L3 hoặc vượt ngưỡng thêm `prompt=login` + `acr`.
- Vì mọi connector HTTP (Claude, ChatGPT) cần trang này để làm bất cứ việc gì từ L2 trở lên, `/confirm` phải có ngay khi có transport HTTP. `architecture-v2.md` (viết song song) đã đặt `/confirm` server-render ở v0.1, và TCV1 theo mốc đó.
- Ở chế độ stdio, `confirm_url=null` và lỗi trả `cli_command: "xt confirm cf_…"`, chạy trên TTY như `architecture-v1.md` §5.5.
- **Lệch với `architecture-v2.md` §1.3:** v2 ghi "một tên tham số gate duy nhất `confirmation_ref`". TCV1 dùng 0 tham số gate. Năng lực 1 của v2 vẫn đủ (`confirm_url`, `confirmation_id` pending, `continuation`), nhưng dòng đó trong v2 cần sửa.

**Kênh phụ:**
- Elicitation dạng URL của MCP: chỉ dùng khi client khai capability. Chưa kiểm chứng trên ChatGPT/Claude.
- MCP App: chỉ được mở link bằng `ui/open-link`.

### F2: Thiếu tool chuyển trạng thái Record. **ACCEPT-MODIFIED**

Bằng chứng xác đáng: `update_record` cấm state, ModelDefinition không có state machine, trong khi `pain-points.md` coi lifecycle là một trong ba động cơ lõi. AUTHOR chọn **tool riêng** `transition_record`, đúng như CRITIC nghiêng về.

**Phần mở rộng so với CRITIC:** dùng một cơ chế duy nhất cho mọi primitive có vòng đời.
- Task: bắt đầu, nộp nghiệm thu, nghiệm thu, trả lại, hủy.
- Request: rút lại (`withdrawn`, F21).
- Notification: đánh dấu đã đọc (F13).
- Person: chuyển sang `offboarding`.
- Thiết bị: trả máy.

**Tác động lên catalog:** `update_task` bị **xóa**. Đổi người phụ trách và đổi hạn giờ là field của Task, đi qua `update_record` (field `impact: high` thì là L2). Đổi trạng thái đi qua `transition_record`.

**Quy tắc chung:**
- Mức L do transition khai báo (`level`), không gắn cứng theo tool.
- Model compiler ép mỗi cặp (from, to) chỉ có một transition, nên input chỉ cần `to_state`. Small model không phải chọn transition id.
- Transition có thể `starts_process`. Nhờ đó W3 offboarding chạy được ngay ở v0.1 mà không cần `start_process`.

### F3: Không có upload file. **ACCEPT-MODIFIED**

CRITIC đúng là thiếu. Nhưng ý (i), dùng `content_base64` cho tệp người dùng đính kèm, là ảo tưởng: model trong Claude/ChatGPT không có byte của tệp đính kèm để mã hóa base64.

**AUTHOR chốt `upload_file` với `source` gồm 3 giá trị:**
- `text`: nội dung agent tự soạn, tối đa 256 KiB.
- `base64`: tối đa 1 MiB, dành cho CLI hoặc iframe MCP App đã đọc tệp thật.
- `upload_link`: trả 2 link một lần.
  - `upload_url`: trang Desk để người kéo thả tệp, đăng nhập bằng `xt-desk`.
  - `put_url`: presigned PUT đi qua APISIX tới SeaweedFS, cho client lập trình.

**Trạng thái tệp:** `awaiting_upload` → `scanning` → `clean`/`infected`. Ở v0.1 chưa có ClamAV, nên trạng thái là `not_scanned` và được ghi trung thực, không giả `clean`.

**Đọc nội dung:** `get_record(file_ref, include=["text"])`, có `next_text_cursor`, và text luôn là dữ liệu không tin cậy.

**Không dùng:**
- MCP resource: chiều server → client, không upload được.
- `openai/fileParams`: chỉ có ở ChatGPT, ngoài chuẩn. Để xem xét ở v1.x.

### F4: Không có tổng hợp số liệu phía server. **ACCEPT**

`red-team.md` Q3 và storyboard S6 (`xt_metric`, F-25 "mọi con số do tool trả về") đều cần aggregate. AUTHOR thêm `aggregate_records`. Có 3 điều chỉnh nhỏ:

1. **Metric ở dạng chuỗi.** Viết `"sum:amount"` thay cho object `{op, field}`, vì model 4B sinh chuỗi có pattern dễ hơn.
2. **Thêm tham số `metric`** cho metric có tên, khai trong model (ví dụ `advance_total`). Công thức KPI đã duyệt là dữ liệu, không để agent tự chế.
3. **Một `lineage_ref` cho cả truy vấn**, không gắn cho từng ô. Mọi ô cùng sinh từ một truy vấn đã pin `as_of` và `model_revision`, nên lineage theo ô chỉ tốn byte.

Output có thêm `scope: "authorized_records"` để nói thật rằng con số chỉ tính trên phần dữ liệu người gọi được xem.

**Có gộp vào `search_records` thành một `query_records` không?** **Không gộp.**
- Hai tool có shape output khác hẳn nhau (danh sách bản ghi so với hàng số liệu). Gộp lại sẽ buộc output thành union có điều kiện.
- Động từ của người dùng ("tìm" so với "tổng/đếm") đã tách sẵn ý định.
- Probe 4B chọn đúng `aggregate_records` cho câu hỏi "tổng … theo phòng ban" (xem cuối file).

## Major

### F5: Gate L2 quá tay. **ACCEPT-MODIFIED**

CRITIC đúng: theo bản nháp, mỗi hành động cần thêm 3 lời gọi và một người duyệt, và việc đó phá W1. AUTHOR áp các mức L1/L2/L3 như CRITIC đề xuất. Riêng L2 thêm một ngoại lệ: **mức trần tự chủ** (`autonomy_ceiling`) do chính người dùng cấp cho agent trong Desk ("Agent của tôi", `architecture-v1.md` §5.1). Mặc định trần là L1.

| Trường hợp | Cách xử lý |
|---|---|
| `effective_level` ≤ trần | Agent chạy thẳng |
| L2 vượt trần | Một cú bấm của chính người được đại diện |
| L3 | Luôn cần xác nhận. Policy có thể đòi thêm người thứ hai, khi đó trạng thái là `awaiting_second_approver` |
| `human_only` (`decide_request`, ký, lệnh chi, kích hoạt định nghĩa) | Không bao giờ hạ được, kể cả khi trần là L3 |

**Điều chỉnh thêm cho W2:** policy mặc định coi "giao việc cho cấp dưới trực tiếp" là L1. Giao việc ngoài tuyến báo cáo là L2. Đây là policy dữ liệu, tenant đổi được.

### F6: Ánh xạ annotations lệch REA. **ACCEPT-MODIFIED**

Yêu cầu dự án là "annotations suy từ effects **đúng như REA**". AUTHOR áp nguyên văn công thức của REA (`toolEffects.ts:641-658`, `rea-lessons.md` §3.1):
- `destructive = may_discard_data ‖ executes_foreign_code ‖ mutates_device`
- `openWorld = accesses_external_network ‖ launches_process`

**AUTHOR không theo CRITIC** ở ý "`decide_request` và `start_process` vẫn true":
- REA không đưa "không đảo ngược" vào `destructiveHint`.
- Decision là append-only, không làm mất dữ liệu.
- Kích hoạt model ở v0.1 không xóa vật lý field. Dữ liệu cũ nằm trong JSONB và `record_history`.

Vì vậy v0.1 có **0 tool destructive**. Chỉ `delete_record` (v1.0) là destructive.

**Effect `notifies_internal`:** chấp nhận tách riêng, vì gửi tới thành viên của chính tenant qua kênh do tenant cấu hình là closed-world. Kèm một bất biến ghi trong contract test: *không tool MCP nào gây egress ra ngoài tổ chức; egress ngoài chỉ đi qua action handler có xác nhận riêng.* Nếu vi phạm bất biến này thì phải bật `accesses_external_network`, không được lách.

**Lưu ý trung thực:** Claude sẽ hỏi quyền theo cấu hình của chính nó với tool `readOnlyHint:false`. Hint chỉ là UX, biên an toàn nằm ở server.

### F7: Schema không học được với model nhỏ. **ACCEPT**

Chấp nhận cả 5 ý (a) đến (e). `build.py` kiểm tự động 4 điều:
1. Mọi property ở mọi cấp của input đều có `description`.
2. Không có combinator nào ở root (`allOf/anyOf/oneOf/if/then/not/$ref`).
3. Mọi object input đều đóng.
4. Bản compact vẫn nhận mọi example.

**Sửa một nhận định kỹ thuật sai của CRITIC:** "Object JSON tự loại bỏ chuyện trùng field" là **không đúng**. RFC 8259 chỉ nói tên khóa "SHOULD be unique". `json.loads` của Python, và phần lớn parser khác, âm thầm giữ giá trị cuối. `build.py` đã chứng minh: chuỗi `{"amount":"1","amount":"9000000"}` **vẫn qua schema**. Vì vậy adapter phải parse bằng `object_pairs_hook` từ chối khóa trùng, và v1 ghi rõ điều này thành quy tắc. Poka-yoke vẫn giữ được, nhưng ở tầng parse chứ không ở tầng schema.

**Ý (e):** description viết tiếng Anh ngắn, kèm câu mẫu tiếng Việt "VD: …". Spike cũng khuyến nghị dùng ngôn ngữ người dùng. Hai cách được kết hợp, eval sẽ quyết bản cuối.

### F8: Quá nhiều plumbing. **ACCEPT-MODIFIED**

Chấp nhận các điểm sau:
- `workspace` tùy chọn.
- `expected_model_revision` và `expected_process_revision` bị bỏ khỏi `create_record`/`submit_request`. Server pin bản active và trả về.
- `process_ref` suy ra từ loại Request.
- `migration_plan`/`rollback_plan` bị bỏ khỏi input.
- CAS chỉ bắt buộc ở `update_record`, `transition_record`, `decide_request`. Ở `submit_request` thì tùy chọn.

**Hai chỗ khác CRITIC:**

1. **Output dùng ref ngắn `model:id`, không dùng XRN đầy đủ.** Tenant luôn suy từ token, nên viết `xrn:t_demo:` vào mọi ref chỉ tốn token và tăng rủi ro chép sai ở model nhỏ. XRN đầy đủ vẫn là định danh trong audit và export. Input nhận cả hai dạng.
2. **`idempotency_key` vẫn bắt buộc.** Ở mục "Giữ nguyên", CRITIC cho phép server tự sinh key khi thiếu. AUTHOR từ chối điều đó vì hai lý do:
   - Nếu key tùy chọn thì "hành vi trên mọi input hợp lệ" không còn idempotent. Theo luật REA, `idempotentHint` phải là false cho cả 9 tool ghi.
   - Luồng xác nhận của F1 dựa trên việc "gọi lại đúng lời gọi cũ". Nếu agent quên key thì lần gọi lại sẽ tạo pending operation mới.

   Description dạy model cách đặt key: "chuỗi bạn tự đặt, chỉ dùng lại khi thử lại cùng hành động". Probe đo tỷ lệ model 4B điền đủ field bắt buộc (xem cuối file).

### F9: Làn PII vắng mặt. **ACCEPT-MODIFIED**

**Cách đánh dấu field:** dùng `pii: true` theo yêu cầu dự án, cộng `sensitive: true` cho dữ liệu nhạy cảm (đã có trong bản nháp). Không dùng enum `none|basic|sensitive` của `architecture-v1.md` §5.7, vì hai boolean dễ đọc hơn và tương đương về nghĩa.

**Hòa giải từ vựng.** `architecture-v1.md` và storyboard S5 đang dùng `local_only` theo hai nghĩa khác nhau. V1 tách thành hai tầng:

| Tầng | Giá trị |
|---|---|
| Policy tenant `llm_egress` | `masked` (mặc định), `local_only`. Bỏ `full`, vì `architecture-v2.md` chốt "`cloud` luôn masked" |
| Chế độ hiệu lực của mỗi lời gọi `pii.mode` | `full`, `masked`, `omitted` |

Chế độ hiệu lực tính theo lớp client:
- `human` (Desk) luôn là `full`.
- `local` luôn là `full`.
- `cloud`: `masked` khi policy là `masked`; `omitted` khi policy là `local_only`. Không bao giờ là `full`.

Như vậy S5 ("Goose local trả số thật") và S4 ("Claude chỉ thấy token") cùng đúng.

**Định dạng token:** `[PII:<token>]`.
- Token ổn định theo (tenant, giá trị, epoch), nên agent vẫn so sánh hay group được.
- Filter `where` chấp nhận token để so sánh bằng.
- Ghi token vào field trả `invalid_request`, reason `masked_value`.

**Bác một ý của CRITIC:** "MCP App lấy giá trị thật bằng `connectDomains` tới REST". Lý do:
- Iframe trong host bên thứ ba không có token người (`architecture-v1.md` §5.7 đã ghi đây là điều chưa kiểm chứng).
- Phương án dự phòng của storyboard F-22, "app-only tool, server quyết theo người gọi", cũng sai, vì app và model dùng chung một token.

Kết luận: **giá trị thật chỉ hiện trong Desk.** Storyboard S4 phải chuyển panel lương sang Desk.

### F10: Marker untrusted nằm ở `_meta`. **ACCEPT** (bác một ý nhỏ)

**Chấp nhận:**
- `untrusted: true` đặt ở gốc `structuredContent`. Text content là JSON của cùng object nên model thấy marker. `_meta.provenance` vẫn giữ để tương thích REA.
- `authored_by_kind: human|agent|system` cho mọi text tự do. UI duyệt hiện nhãn "do agent soạn".

**Bác:** ý "Text content mở đầu bằng nhãn ngắn". Lý do:
- MCP spec khuyến nghị text là JSON serialize của `structuredContent`.
- REA (`toolResult.ts:155-173`) cũng làm đúng như vậy.
- Thêm tiền tố sẽ làm hỏng tính "cùng một object" mà client và test dựa vào. Marker trong JSON đã đủ để model nhìn thấy.

### F11: Thiếu danh bạ actor/policy và tìm kiếm xuyên model. **ACCEPT-MODIFIED**

**Chấp nhận:**
- `search_records.model_id` tùy chọn, kèm `query` full-text xuyên model.
- `inspect_workspace` có thêm mục `policies`.

**Khác CRITIC:** không dùng `search_records(model_id="actor")` hay "actor_directory" làm danh bạ. Thay vào đó thêm tool theo ý định **`find_people`**, có các tham số `direct_reports_of`, `manager_of`, `org_unit`, `role`, `query`.

Bằng chứng nằm ở `spike-local-llm.md` §3a:
- qwen3.5:4b trả lời **ngược chiều quan hệ 3/3 lần** khi chỉ có tool tìm nhân viên chung. Đổi tên field cho rõ nghĩa cũng không cứu được.
- Thêm tool đúng ý định `list_direct_reports` thì đúng 3/3 lần.

Output của `find_people` có `relation`, `anchor` và một câu `summary` do server sinh ("1 người báo cáo trực tiếp cho Lê Minh: Phạm Tuấn."). Model không phải tự suy chiều quan hệ. Đây là tool duy nhất AUTHOR thêm vì lý do "model nhỏ".

### F12: Task state cứng, thiếu nghiệm thu, cha–con, định kỳ. **ACCEPT-MODIFIED**

**Chấp nhận:**
- State lấy theo loại task. Mặc định: `todo → in_progress → review → done`, thêm `returned`, `blocked`, `cancelled`.
- Có `parent_ref`, `depends_on[]`, `recurrence` (RRULE, có pattern), `sla` tùy chọn theo loại task.

**Khác CRITIC:** không thêm operation `pause`/`resume`. Thay vào đó, state khai `pauses_sla: true` (ví dụ `blocked`), và việc tạm dừng chỉ là một transition. Cách này ít khái niệm hơn.

### F13: Schedule/Notification không có đường hoạt động. **ACCEPT-MODIFIED**

**Chấp nhận:**
- Bỏ CRUD Schedule.
- Process có `trigger.kind: manual|on_submit|on_transition|timer|event`.
- Lặp lại ở mức task dùng `recurrence`.

**Mark-read:** không thêm `ack_notifications`. Notification là primitive có state, nên "đánh dấu đã đọc" là `transition_record(notification, "read")`, L1. Cách này tiết kiệm một tool và dùng lại cơ chế của F2.

**`notify`:** đưa vào v1.0 như CRITIC đề xuất.

### F14: Output đóng. **ACCEPT**

`data` để mở (additionalProperties được phép). Envelope (`ok`, `untrusted`, `data`, `meta`) thì đóng. `build.py` kiểm cả hai chiều cho mọi tool: thêm khóa trong `data` vẫn hợp lệ, thêm khóa ở envelope bị từ chối.

Một hệ quả CRITIC chưa nêu: enum trong output (ví dụ `status`) cũng là bẫy tương thích. V1 quy định giá trị enum output mới chỉ được thêm khi tăng minor version, và client phải chịu được giá trị lạ.

### F15: Phân trang offset + snapshot. **ACCEPT**

Dùng `cursor` (opaque) ở input và `next_cursor` ở output, có ngay từ v0.1. Cursor bind với principal, query digest và `as_of`. Server bên trong dùng keyset. Cursor không khớp hoặc hết hạn thì trả `invalid_request` với reason `cursor_mismatch|cursor_expired`, kèm remediation "gọi lại không có cursor". Không cần mã lỗi `snapshot_expired` riêng.

### F16: Ba loại hiện vật ủy quyền chồng nhau. **ACCEPT-MODIFIED**

AUTHOR đi xa hơn CRITIC: **không còn hiện vật nào trong input.**
- `approval_ref` bị bỏ.
- `confirmation_ref` bị bỏ (theo F1).
- `preview_digest` và `preview_token` bị bỏ. `propose_model_change` chỉ còn `base_revision` lấy từ dry run.

Tính an toàn của preview không mất:
- Người duyệt xem diff **đã lưu** trên trang xác nhận.
- Lúc kích hoạt, server revalidate base revision.

### F17: `delete_record` bắt buộc approval. **ACCEPT-MODIFIED**

Cắt `delete_record` khỏi v0.1. Draft bị bỏ thì chuyển sang `cancelled`/`withdrawn` bằng `transition_record`. Ở v1.0, `delete_record` là L1 với draft của chính mình, L3 với bản ghi khác. Xóa hoặc ẩn danh PII theo Đ.25 Luật 91/2025 chạy qua process (`transition_record(person, "erasure_requested")` → process có human gate), giữ tombstone kèm hash.

## Minor

### F18: Định dạng ID quá chặt. **ACCEPT**

Segment id dùng `[a-z0-9][a-z0-9_-]{0,63}`, nhận được ULID viết thường. Model id và field id giữ `^[a-z][a-z0-9_]{0,62}$`, vì chúng thành tên view/index, đúng validator của `architecture-v1.md` §6.1.

### F19: Tiền nhận cả number. **ACCEPT**

Field `money`/`decimal` nhận số nguyên JSON chính xác (|n| ≤ 2^53) rồi chuẩn hóa thành chuỗi. Số có phần lẻ dạng float bị từ chối, với remediation "gửi dạng chuỗi". Output luôn là chuỗi.

### F20: Ủy quyền. **ACCEPT**

`delegate_to` phải là person `kind=human`, không được là người đề nghị. SoD so theo effective human. Pattern `PERSON` chỉ nhận `person:` hoặc `me`.

### F21: Thiếu rút lại Request. **ACCEPT-MODIFIED**

Chấp nhận là thiếu. Bác cơ chế `submit_request.action: submit|withdraw`. Gộp hai ý định ngược nhau vào một enum chính là mẫu `update_task` mà v1 đang gỡ bỏ. Rút lại là `transition_record(to_state="withdrawn")`, L1 cho requester.

### F22: Tên tool chưa nhất quán. **ACCEPT-MODIFIED**

- Đổi tên: `get_inbox`, `get_history`.
- Giữ nguyên: `inspect_workspace`, `describe_*`.
- Thêm `find_*` cho tra cứu theo ý định (`find_people`).

### F23: Định tuyến sai giữa `create_record` và `create_task`. **ACCEPT**

Gọi `create_record(model_id="task")` trả `invalid_request` với reason `wrong_tool`, kèm `continuation` sang `create_task`. Mẫu này có trong `build.py` và đã được validate.

### F24: Chưa có `explain`. **ACCEPT-MODIFIED**

**Chấp nhận:** `explain_ref` có trong mọi lỗi bị chặn, và `lineage_ref` có trong mọi kết quả aggregate. Hai ref này được đọc bằng `get_history(ref, view="explain")`.

**Bác:** tách tool `explain` ở v1.0. Lý do:
- Câu hỏi "vì sao bị chặn" và "con số từ đâu" đều trả về một dossier trên cùng nguồn, là audit cộng trace. REA cũng chỉ tách tool khi outcome khác nhau.
- Thêm tool thì tốn ngân sách catalog, và theo đo của probe, model nhỏ trả giá theo từng tool.

Chỉ tách khi eval cho thấy model không tìm ra `view="explain"`.

### F25: Chưa liên kết MCP Apps. **ACCEPT-MODIFIED**

- ToolContract có `ui_resource`. Map: `find_people` → `ui://xt/org-chart`, `aggregate_records` → `ui://xt/report`, `get_record` → `ui://xt/record-card`, `get_inbox` → `ui://xt/inbox`, `submit_request`/`decide_request` → `ui://xt/request-card`, `create_task` → `ui://xt/task-board`, `propose_*` → `ui://xt/diff-preview`.
- Không có tool app-only để ghi.

**Điểm sửa cho storyboard:** S2b và S3 mô tả "UI gọi tool bằng token của Mai" và "nút Áp dụng". Theo D8, các nút này phải **mở `confirm_url`** bằng `ui/open-link`. Riêng Telegram vẫn là 1 chạm, vì callback Telegram là kênh người. Thẻ MCP App vì vậy tốn 2 chạm (mở rồi xác nhận). Đây là cái giá đúng của an toàn.

## Bị bác

### F26: `decide_request` gánh activation nên mang effects L3. **REJECT**

**Tiền đề của CRITIC:** chấp nhận `decide_request` destructive/L3, rồi vá UI bằng `effective_level`.

**Vì sao sai:**
- Theo công thức REA, `decide_request` không destructive.
- `decide_request` không nằm trên thang tự chủ của agent. Nó là `human_only`: luôn cần người được giao duyệt tự xác nhận, bất kể mức của Request là nghỉ phép hay kích hoạt model.

**Cách v1 xử lý:**
- Hậu quả deferred, tức kích hoạt định nghĩa, được khai bằng `mutates_definitions: true`. Effect này chỉ làm `readOnlyHint=false` và không đổi gì khác.
- Mức nghiêm trọng thật hiện trên trang xác nhận (`plan_summary`, `level`) cho người đọc, không hiện cho host.

`meta.effective_level` vẫn có trong mọi kết quả, nhưng không phải để vá lỗi này.

### F27: Prompt/skill nhiều hơn mức cần cho v0.1. **REJECT** (giữ một phần)

**Bác mức cắt "3 prompt, 2 skill":** cắt như vậy làm gãy demo v0.1 trong `architecture-v1.md` §10.
- Demo (4) "hết SLA → leo thang" cần `xt-tasks`.
- Demo (5) "agent đề xuất thêm field → diff → duyệt" cần `xt-data-modeling` và prompt `change_model_safely`.

**V0.1 giữ:**
- 4 skill: `xt-core`, `xt-approvals`, `xt-tasks`, `xt-data-modeling`.
- 4 prompt: `prepare_request`, `review_pending_requests`, `triage_my_work`, `change_model_safely`.

Các prompt và skill còn lại giữ lịch theo `architecture-v1.md` §7.

**Giữ phần đúng của CRITIC:** có hồ sơ client cho model nhỏ, gồm 10 tool như bảng kích thước. Catalog phía server vẫn đầy đủ.

---

## Probe trên qwen3.5:4b (bằng chứng cho các quyết định trên)

Kết quả đầy đủ ở `docs/tool-contracts-v1.md` §10. Script `/tmp/xt-contracts-v1/llm_probe.py` gọi ollama `/api/chat` bằng `think:false` và `temperature:0`, với tool lấy từ chính `contracts.py`.

Đây là **smoke test 8 câu**, không phải eval 30 kịch bản (eval đó vẫn là việc phải làm, xem `tool-contracts-v1.md` §12). Probe chỉ đo lời gọi đầu tiên, không chạy đa lượt.

**Token thật** (qwen3.5:4b, ollama 0.30.10): core-10 = **3916**, v0.1-17 = **8155**, v1.0-21 = **9635** token cho phần tool. Hệ quả: với `num_ctx 8192` của spike, catalog v0.1 đầy đủ chiếm gần hết context; **client local phải đặt `num_ctx ≥ 16384`** (probe chạy 16384, VRAM vẫn vừa 6 GB) hoặc dùng core-10.

**Smoke test chọn tool** (`think:false`, `temperature:0`, 11 câu tiếng Việt × 2 catalog, một lời gọi đầu tiên; lần chạy đầu 8 câu × 3 lặp cho 3 lần giống hệt nhau, nên ở temperature 0 mỗi câu coi như 1 mẫu):

| Catalog | Câu | Tool | Args (rút gọn) | Đúng tool | Args hợp schema | Đúng ngữ nghĩa |
|---|---|---|---|---|---|---|
| core10 | Ai báo cáo trực tiếp cho Minh? | `find_people` | `{"direct_reports_of": "Minh"}` | có | có | có |
| core10 | Tổng tiền tạm ứng đã duyệt tháng 11/2026 theo phòng ban là bao nhiêu? | `aggregate_records` | `{"model_id": "advance_request", "metrics": ["sum:amount"], "where": {"state": "approved"}, "period": "2026-…` | có | có | có |
| core10 | Có gì cần tôi duyệt không? | `get_inbox` | `{"status": "open"}` | có | có | có |
| core10 | Giao anh Bình bên IT làm báo cáo doanh số tuần, hạn 20/11/2026. | `find_people` | `{"query": "Bình IT"}` | có | có | có |
| core10 | Tạo đề nghị tạm ứng (model advance_request) 8 triệu đi công tác Đà Nẵng từ 20/11 đến 22/11/2026. | `create_record` | `{"model_id": "advance_request", "values": {"amount": "8000000", "destination": "Đà Nẵng", "trip_start": "20…` | có | có | chưa: tự chế field `destination`, thiếu `trip_end`/`purpose` → server trả `unknown_field` + danh sách field |
| core10 | Duyệt đề xuất advance_request:tu-2026-0042 giúp tôi, revision hiện tại là 2. | `decide_request` | `{"ref": "advance_request:tu-2026-0042", "decision": "approve", "expected_revision": 2, "idempotency_key": "…` | có | có | có |
| core10 | Đánh dấu việc task:01jd2q đã xong, revision 5. | `transition_record` | `{"ref": "task:01jd2q", "to_state": "done", "expected_revision": 5, "idempotency_key": "trans-task-01jd2q-do…` | có | có | có |
| core10 | Tìm giúp tôi hóa đơn khách sạn Đà Nẵng. | `search_records` | `{"query": "hóa đơn khách sạn Đà Nẵng"}` | có | có | có |
| core10 | Sếp trực tiếp của Tuấn là ai? | `find_people` | `{"manager_of": "Tuấn"}` | có | có | có |
| core10 | Rút lại đề xuất advance_request:tu-2026-0042 của tôi, revision 2. | `transition_record` | `{"ref": "advance_request:tu-2026-0042", "to_state": "withdrawn", "expected_revision": 2, "idempotency_key":…` | có | có | có |
| core10 | Tổng tạm ứng (advance_request) đã duyệt theo từng tháng trong năm 2026? | `aggregate_records` | `{"model_id": "advance_request", "metrics": ["count", "sum:amount"], "period": "2026-11", "time_bucket": "mo…` | có | có | sai: `period=2026-11`, `date_field=trip_start`, thiếu `state=approved` |
| v0.1-17 | Ai báo cáo trực tiếp cho Minh? | `find_people` | `{"direct_reports_of": "Minh"}` | có | có | có |
| v0.1-17 | Tổng tiền tạm ứng đã duyệt tháng 11/2026 theo phòng ban là bao nhiêu? | `aggregate_records` | `{"model_id": "advance_request", "metrics": ["sum:amount"], "group_by": ["department"], "period": "2026-11"}` | có | có | thiếu filter `state=approved` |
| v0.1-17 | Có gì cần tôi duyệt không? | `get_inbox` | `{"status": "open"}` | có | có | có |
| v0.1-17 | Giao anh Bình bên IT làm báo cáo doanh số tuần, hạn 20/11/2026. | `find_people` | `{"query": "Bình IT"}` | có | có | có |
| v0.1-17 | Tạo đề nghị tạm ứng (model advance_request) 8 triệu đi công tác Đà Nẵng từ 20/11 đến 22/11/2026. | `describe_model` | `{"model_id": "advance_request"}` | có | có | có |
| v0.1-17 | Duyệt đề xuất advance_request:tu-2026-0042 giúp tôi, revision hiện tại là 2. | `decide_request` | `{"ref": "advance_request:tu-2026-0042", "decision": "approve", "expected_revision": 2, "idempotency_key": "…` | có | có | có |
| v0.1-17 | Đánh dấu việc task:01jd2q đã xong, revision 5. | `transition_record` | `{"ref": "task:01jd2q", "to_state": "done", "expected_revision": 5, "idempotency_key": "trans-task-01jd2q-do…` | có | có | có |
| v0.1-17 | Tìm giúp tôi hóa đơn khách sạn Đà Nẵng. | `search_records` | `{"query": "hóa đơn khách sạn Đà Nẵng"}` | có | có | có |
| v0.1-17 | Sếp trực tiếp của Tuấn là ai? | `find_people` | `{"manager_of": "Tuấn"}` | có | có | có |
| v0.1-17 | Rút lại đề xuất advance_request:tu-2026-0042 của tôi, revision 2. | `transition_record` | `{"ref": "advance_request:tu-2026-0042", "to_state": "withdrawn", "expected_revision": 2, "idempotency_key":…` | có | có | có |
| v0.1-17 | Tổng tạm ứng (advance_request) đã duyệt theo từng tháng trong năm 2026? | `aggregate_records` | `{"model_id": "advance_request", "metrics": ["sum:amount"], "group_by": ["month"], "period": "2026", "time_b…` | có | có | thiếu `state=approved`; `group_by=[month]` không phải field → `unknown_field` |

Tổng: đúng tool **22/22**, args hợp schema **22/22**, đúng ngữ nghĩa **18/22**. Lời gọi đầu mất 13-16 s vì nạp model; các lời gọi sau 3-7 s.

**Đọc kết quả cho thiết kế:**
- `find_people` được gọi với đúng tham số theo chiều quan hệ ở cả 2 câu (`direct_reports_of` cho “ai báo cáo cho”, `manager_of` cho “sếp của”); việc đọc kết quả không bị suy luận ngược vì output đã nêu `relation` + `summary` (chưa đo đa lượt).
- Tham số phẳng, enum và pattern được sinh đúng; model tự đặt `idempotency_key` hợp lệ 100%; ghi nhận cho quyết định giữ key bắt buộc (F8).
- Lỗi còn lại là **ngữ nghĩa của aggregate** (bỏ filter trạng thái) và **tự chế field** khi chưa gọi `describe_model`. Thiết kế đầu tiên `group_by: ["created_at:month"]` khiến model sinh `department:month` ở 3/3 lần; đã tách thành `period` + `time_bucket` + `date_field` và đo lại. Biện pháp tiếp: metric có tên (`advance_total` đã gói filter), `summary` nêu filter, lỗi `unknown_field` liệt kê field hợp lệ.
- Đây chưa phải eval: chưa đa lượt, chưa đo câu âm/injection, chưa đo Claude/GPT.
