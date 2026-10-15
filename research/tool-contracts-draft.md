<!-- SPDX-License-Identifier: Apache-2.0 -->
# Bản nháp hợp đồng tool MCP cho xT

Phiên bản: `0.1.0-draft` · Ngày: 15/10/2026 · Trạng thái: đề xuất để debate, chưa phải contract đã triển khai hay nền tảng đã được chọn.

## 1. Phạm vi, căn cứ và quyết định thiết kế

Bộ **18 tool cố định**, dùng chung mọi ngành. Model, field, policy, loại Request và graph process là dữ liệu có phiên bản; thêm mô hình “tài sản” hay quy trình “tiếp nhận nhân sự” không thêm tool, không đổi `tools/list`. Không dùng tên hàm hoặc API của nền tảng nghiệp vụ cụ thể. Agent ghép các primitive để giải nghiệp vụ; prompt/skill hướng dẫn cách ghép, không thay enforcement.

Căn cứ đã đọc:

- [CLAUDE.md](../CLAUDE.md): Python, Apache-2.0, primitive ổn định, agent đề xuất và người duyệt.
- [Bài học REA](rea-lessons.md), nhất là §1–3, §5, §7: một ToolContract, effects khai thật, lỗi có remediation, unknown khác absence. Đối chiếu thêm `src/contracts/toolContractTypes.ts` trong checkout REA ở `/home/chinhan/projects/reclaw/repos/rea`.
- [Pain points](pain-points.md), §3–6: Request/Decision, Task/SLA, vòng đời Record tạo nền cho W1–W8; không đưa số liệu thị trường hoặc nhận định pháp lý của tài liệu thành ràng buộc contract.
- [Checklist OLP](olp-checklist.md), §3–5: đủ SSO, API Gateway, Data, Workflow ở Tầng 1 headless và ít nhất một app có UI ở Tầng 2. MCP Apps hiển thị form/preview/inbox/decision từ cùng contract; MCP không thay vai trò API Gateway.
- Học pattern IAM action/resource, deny thắng allow và durable queue/outbox qua tài liệu `docs/03-auth-iam.md`, `docs/07-queue-worker.md` của sklite tại `/tmp/sklite_src/sklite-main`. Không sao chép mã: nguồn này chưa có LICENSE. Auth đa tenant/approval của xT là thiết kế mới, không phải khả năng đã có của REA local-only.

Ba hướng đã cân nhắc: tool theo từng loại nghiệp vụ gây phình catalog; một `execute(mode, payload)` làm lẫn authority; **18 primitive có outcome rõ ràng** giữ được catalog nhỏ và guardrail riêng. `inspect_history` gộp các facet đọc cùng dossier; `update_task` có ba thao tác thuộc một lifecycle, quyền phân nhánh rõ. Đây là các điểm cần kiểm nghiệm bằng agent eval, không dùng mục tiêu 18 để biện hộ việc gộp thêm authority không liên quan.

### 1.1. ToolContract là nguồn sự thật duy nhất

Mỗi ToolContract có `name`, `title` sinh từ tên, `description`, `inputSchema`, `outputSchema`, `effects`, `max_autonomy`, `iam`, `errors`, `examples`, `returns_untrusted_content=true`. `annotations` là trường **suy ra**, chỉ hiển thị trong bản nháp để kiểm toán. Từ contract sinh đăng ký MCP, adapter CLI, catalog/docs, schema resource, bảng quyền, metadata skill và contract tests. Handler được gắn bằng tên contract, không theo thứ tự catalog. Không duy trì schema/quyền/annotations viết tay thứ hai.

`tools/list` luôn chứa đủ 18 tool. Khả năng của principal/tenant/client hiện tại được trả qua `inspect_workspace.tool_availability`; thay đổi model/process hay đăng nhập không làm đổi catalog. Chỉ thay phiên bản contract mới có thể đổi catalog/digest. Catalog thật phải được kiểm tra sau SDK conversion bằng validator Draft 2020-12 và các MCP client mục tiêu; profile compact không được bỏ nghĩa, example hay kiểm tra runtime.

### 1.2. Identity, XRN và IAM

- Resource chuẩn: `xrn:{tenant}:{model}:{id}`, ví dụ `xrn:t_demo:work_item:rec_a`. Model hệ thống cũng dùng vị trí `{model}` như `request`, `task`, `process`, `process_instance`, `actor`, `workspace`, `model`.
- Với Record thường, `{model}` trong XRN là model nghiệp vụ; với primitive chuyên biệt, namespace lõi là `request`, `task`, `document`, v.v. `model_id`/`type_model_ref` chỉ schema payload cụ thể. IAM collection phải dùng namespace của tài nguyên sắp được tạo, không suy trực tiếp từ chuỗi model_id. Mọi ref còn được runtime kiểm đúng loại (Request/Task/Process/Actor), không chỉ đúng regex.
- `{tenant}` lấy từ token đã xác thực; actor và `on_behalf_of` lấy từ subject/delegation đã được server kiểm chứng, không nhận `actor_id`, `tenant_id`, role, scope hoặc `approved=true` trong tool arguments. `workspace_id` là phạm vi dữ liệu, server kiểm tra thuộc tenant. Ref sai tenant luôn bị chặn trước truy vấn nghiệp vụ.
- Action có dạng `record.create`, `request.approve`, `task.sla_override`. Tạo mới kiểm tra collection `xrn:{tenant}:{model}:*` trước khi cấp ID; wildcard chỉ ở IAM policy, không chấp nhận wildcard trong XRN input. Sau đó kiểm tra row/field/quan hệ và ownership của bản ghi dự kiến.
- Effective permission là giao của quyền actor, quyền người được đại diện, grant client, tenant/workspace, RLS/field rules và policy hành động. Deny tường minh thắng Allow, mặc định Deny. Quyền service worker không mở rộng quyền người đã duyệt.
- Tất cả công cụ đọc chung (`get_record`, `search_records`, completion, resources/read) dùng cùng cổng quyền chuyên biệt của primitive; `record.read/search` không lách được `audit.read`, `decision.read`, `ledger.read` hay PII. Khi không được tiết lộ sự tồn tại, dùng `not_found` thống nhất, không trả count/actual_revision để dò dữ liệu.

## 2. Effects, annotations và mức tự chủ

Effects khai **hợp của mọi input hợp lệ và mọi hệ quả trực tiếp/deferred** của tool. Ghi AuditLog nghiệp vụ hoặc ghi session bền vững cũng là mutation; truy cập DB/nội bộ tin cậy và MCP transport không tự làm tool thành open-world. Tool cho phép email/webhook/connector ngoài khai `accesses_external_network=true`, kể cả chỉ enqueue bây giờ và worker gửi sau. Không chạy code do model hay agent gửi lên; handler chỉ từ registry đã kiểm toán.

Ánh xạ bắt buộc, không suy từ tên tool:

- `readOnlyHint = !(mutates_records || mutates_audit || enqueues_work || changes_definitions)`.
- `destructiveHint = replaces_state || may_discard_data || changes_definitions || executes_foreign_code`. Bản nháp chọn bảo thủ cho sửa/thay trạng thái, không hạ cờ vì có thể undo.
- `idempotentHint = effects.idempotent`; mutation chỉ có cờ này khi receipt/dedupe thực sự được bảo đảm như §3.
- `openWorldHint = accesses_external_network || executes_foreign_code`. Nếu triển khai bổ sung launch process không đóng kín thì phải thêm effect và mở cờ, không âm thầm thay nghĩa.

| Mức | Quyền tự chủ và enforcement |
|---|---|
| L0 | Đọc theo IAM, không đổi nghiệp vụ/session/AuditLog; technical access log không được làm thay đổi trạng thái thấy được bởi tool. |
| L1 | Thêm draft/comment/proposal an toàn hoặc việc của chính mình theo policy; agent được làm trong grant có hạn, bắt buộc key và audit khi commit. |
| L2 | Chuyển trách nhiệm/trạng thái hoặc sửa field có tác động; agent cần human approval đã bind cho hành động đó. |
| L3 | Xóa, đổi định nghĩa/migration, hành động ngoài hoặc không đảo ngược; bắt buộc preview/plan và human-in-the-loop, thực thi trong phạm vi đã duyệt. |

Mức tự chủ là **trần tác động được phép**, không phải cấp đặc quyền cho agent. Agent ở L3 vẫn không được tự duyệt Request, tự ký hay tự ra lệnh chi tiền. `max_autonomy` trong contract là mức cao nhất có thể phát sinh; server tính `effective_level` từ các effects thực tế và policy đã pin, chỉ được nâng độ chặt, không bỏ human gate. Ví dụ `create_task` gửi nhắc ngoài có trần L3; task chỉ trong inbox nội bộ và tự giao có thể là L1. Client luôn thấy annotations tĩnh bảo thủ của toàn tool. `_meta.xt` trả `max_autonomy`, `effective_level`, `required_permissions`, `requires_approval`.

Hai tool proposal có `dry_run` vì preview và gửi đề xuất là cùng một ý định, **không có nhánh apply**. `dry_run=true` không ghi gì, nhưng annotations tĩnh vẫn `readOnlyHint=false` vì nhánh gửi có ghi. Đây là lựa chọn bản nháp sau debate; không quảng bá thành hai tool có cùng tên nhưng schema/effects khác nhau.

## 3. Quy tắc chung về dữ liệu, CAS, dry-run và kết quả

### 3.1. Poka-yoke và optimistic concurrency

Mọi mutation theo chuỗi: authenticate/tenant → IAM cho action và quyền xem kết quả → tra receipt cùng key → validate schema/model → CAS → policy/rule → human evidence khi cần → transaction ghi dữ liệu + AuditLog + Event/outbox + receipt. Việc tra receipt chỉ xảy ra sau kiểm tra quyền hiện tại; không trả dữ liệu đã bị thu hồi quyền. Validation có thể chạy sớm nhưng không gây side effect. Input khác cùng key bị `idempotency_conflict` trước CAS. Replay hợp lệ không chạy lại rule/cấp số/enqueue. Trước receipt chỉ kiểm tenant/principal/client và quyền action/disclosure hiện tại; eligibility theo trạng thái (step membership, state, CAS, confirmation nonce còn dùng được) chỉ kiểm khi chưa có receipt. Vì vậy approve/delegate đã đổi membership không làm replay cùng key thất bại; quyền đọc bị thu hồi vẫn chặn trả kết quả.

`expected_revision` là revision đối tượng cần sửa; cập nhật/xóa/submit/decision/task bắt buộc, không có `force`. Decision kiểm tra thêm `expected_step_revision`. Definition dùng `expected_model_revision`/`expected_process_revision`; proposal dùng `expected_revision=0` để thêm mới hoặc revision active để thay đổi. Subject dùng `expected_subject_revision`. Revision không khớp → `revision_conflict`, không auto-merge và không retry mù.

Poka-yoke kiểm tra required/type/enum/range, uniqueness, quan hệ cùng tenant/workspace hợp lệ, state transition, SoD/cấm tự duyệt, ownership, chứng từ, ràng buộc nghiệp vụ và số dư/khả dụng trong transaction thích hợp. Rule bị vi phạm không ghi từng phần. Trả field/rule/message/remediation an toàn; không có `skip_rules`. Rule, threshold, approver và SLA là dữ liệu versioned, không hard-code theo ngành. Lưu nguồn fact và policy revision để explain lại.

Envelope và mọi object input/output đều `additionalProperties:false`. Dữ liệu model dùng mảng `FieldValue={field,value}` đóng schema thay vì dict tự do. `value` là scalar hoặc mảng scalar; JSON object/array-of-object được chuẩn hóa thành Record con + quan hệ XRN trong phạm vi bản nháp này. Decimal/tiền dùng chuỗi thập phân chuẩn hóa, không float; ngày ISO, timestamp RFC3339 UTC; relation/document là XRN. `describe_model` chỉ rõ type/multiple/nullability; server dựng object dữ liệu từ `Values` rồi validate schema động **đã pin revision**, có `additionalProperties:false`. Tên field trùng phải bị chặn dù value khác (`uniqueItems` chưa đủ); không chấp nhận field không có trong model. Cần hỗ trợ object lồng sau này thì phải debate và version hóa, không âm thầm mở `value={}`.

Các schema ở §5–6 dùng registry `$id` để tránh lặp. Đây là schema tài liệu có thể resolve bằng registry, không phải liên kết HTTP cần tải. Trước khi quảng bá, generator **bundle các `$defs` đã dùng vào mỗi inputSchema/outputSchema**, đổi `$ref` sang local `#/$defs/...`, gắn `$id` content hash và kiểm tra không recursive/unresolved refs. Schema dữ liệu model trả bằng `data_schema_uri` là MCP resource read-only có ACL, không phải một tool thứ 19. Timestamp/URI phải được validator kiểm tra format thật, không chỉ annotation. `ProcessStep.config` có cấu hình typed cho task/decision/rule/wait/action; bindings chỉ lấy literal, subject field, process parameter hoặc output bước được xác định. Handler XRN+revision phải thuộc registry được duyệt, không nhận endpoint/code tùy ý. Effects/human gate do server suy từ handler+policy; effect_names do agent khai phải khớp, thiếu/khai thấp bị configuration_invalid. ProcessDefinition trả entry_step; PendingStep trả step_revision và Request ref/revision để agent có dữ liệu CAS cho decide_request.

### 3.2. Idempotency, commit và hủy

Mọi tool ghi yêu cầu `idempotency_key`. Namespace gồm tenant + effective actor + tool + key; fingerprint gồm canonical input, workspace, resource, revision và semantic payload, **không gồm ref bằng chứng approval được bổ sung sau; preview_token là bằng chứng transport cũng được loại, còn preview_digest và toàn bộ plan semantic vẫn được bind**. Human action/resource/plan digest vẫn được kiểm riêng, nên loại ref không mở quyền thay payload. Proposal dry-run là pure read, không lưu key, gửi thật dùng key riêng hoặc cùng key chưa commit. Cùng key/cùng fingerprint trả lại cùng business result/receipt, kể cả revision hiện tại đã tăng; metadata thời điểm replay có thể mới. Không dedupe theo toàn payload để nuốt hai ý định hợp lệ khác nhau.

Receipt và uniqueness claim bền vững gắn vòng đời mutation/audit; `inspect_workspace.limits` công bố cửa sổ giữ nguyên response và cơ chế phục hồi operation. Sau cửa sổ giữ response phải phục hồi receipt hoặc từ chối trước mutation nếu key đã dùng, không quên key rồi ghi lại. Worker dùng execution_id/outbox dedupe, không hứa exactly-once với hệ thống ngoài nếu connector không có cơ chế tương ứng. Timeout/hủy sau commit không rollback lời hứa: trả `commit_state`, `operation_ref`/partial observation khi biết; khi chưa biết thì nói `unknown`, replay cùng key. Kết quả enqueue chỉ `queued`, không giả `completed`.

### 3.3. Request, Decision và activation của proposal

Draft Request được tạo qua `create_record` trên model có primitive=Request, server gán trạng thái draft. `submit_request` pin payload + subject + definition + policy + route, tạo instance và bước chờ; requester không chỉ định tùy tiện approver. `return` chuyển Request sang returned, người đề nghị sửa bằng CAS rồi submit lại; snapshot mới khiến quyết định cũ không còn hiệu lực mặc định. `reject` kết thúc đợt duyệt; `approve` chỉ hoàn tất bước phù hợp, không đồng nghĩa toàn Request đã được duyệt. Decision append-only; quorum/parallel/sequence do process định nghĩa.

`delegate` cần người nhận có quyền cùng tenant, scope không rộng hơn, thời hạn và chống vòng/độ sâu theo policy; append Decision ủy quyền và chuyển trách nhiệm bước trong cùng transaction. Không được dùng agent trung gian để né cấm tự duyệt hoặc SoD. Người duyệt chỉ quyết định, không sửa payload Request.

`human_confirmation_ref` là ref opaque đến chứng cứ do UI Tầng 2/luồng xác thực tương đương tạo **sau thao tác thật của con người**. Server bind tenant, human actor, agent/effective principal, action, exact resources, canonical plan/input digest, expected revisions, model/process/policy revisions, effect summary, expiry và nonce. Client/LLM không thể tạo bằng `true`, chuỗi tên người hoặc một Comment. Xác nhận approve không được dùng cho reject/delegate hoặc tài nguyên khác. Consume confirmation và append Decision nguyên tử; retry cùng key trả receipt cũ.

`approval_ref` trên tool L2/L3 là ref Request đã duyệt gắn cùng ràng buộc. Thiếu thì `approval_required` kèm ref hiện có nếu có; lỗi **không tự tạo Request hay side effect**. Agent tạo draft Request loại action-authorization bằng `create_record`, lấy plan/digest do server preview. **Đường bootstrap:** người có quyền dùng phiên human của app gọi `submit_request` để tạo bước duyệt, rồi xác nhận bằng `decide_request`; human submit kiểm IAM/rule nhưng không đòi approval_ref dành cho automation của agent. Nhờ vậy không phát sinh vòng “xin duyệt để được xin duyệt”. Agent không được giả phiên human. Preview cho hành động chưa có tool dry-run riêng lấy từ resource preview của Request theo `resources/read`, cùng ACL và schema của hành động, không thực thi và không ghi. `decide_request` chỉ chứng nhận authorization Request; agent retry mutation gốc cùng key + `approval_ref`. Không dùng chính Request đó để tự cấp quyền quyết định chính nó.

Hai tool `propose_*` chạy simulation trên snapshot thật, trả diff, findings, coverage, affected count (null nếu không biết), effects và kế hoạch migration/rollback. `dry_run=true` không tạo Proposal, Request, AuditLog nghiệp vụ, outbox, Counter hay thay đổi definition; chỉ technical log. Preview digest SHA256 tính trên normalized plan và context đã pin; `preview_token` riêng là token opaque ký/MAC chứa tenant/principal/tool/resource, plan digest, base/policy/snapshot revision, expiry và effects. Dry-run không cần lưu token vào session; server verify chữ ký và claims khi gửi thật, không coi SHA256 đơn thuần là bằng chứng server đã phát hành. `dry_run=false` bắt buộc cả preview_digest và preview_token còn hạn, revalidate, tạo Proposal bất biến và **Request quản trị dạng draft**, không enqueue timer/notification, không activate. Human app gửi draft bằng submit_request trước khi quyết định; đây là cùng đường bootstrap nêu trên. `migration_plan`/`rollback_plan` dạng chuỗi chỉ là mô tả để review; plan thực thi do server sinh từ operations và handler registry. Transform không biểu diễn/không hỗ trợ phải trả capability_unavailable, không chạy prose như mã.

Sau human `decide_request(approve)` cho proposal, worker nội bộ claim execution một lần rồi validate lại base revision, policy/grant/effects và preview; sai lệch → blocked/stale_approval, cần preview/duyệt lại, không rebase ngầm. Activation tạo version mới atomic, dữ liệu cũ không mất trước migration đã kiểm tra/backup. Instance đang chạy pin version cũ; chuyển instance phải là plan được duyệt riêng. Decision receipt phân biệt `queued/waiting/applied/failed`; `execution_ref` khi khác null luôn là XRN `process_instance` và được dùng làm `instance_ref` để lấy tiến độ bằng `get_process_status` hoặc `inspect_history`; không có tool apply bí mật. Chữ ký số, lệnh chi tiền và gửi bên ngoài vẫn cần gate human riêng tương ứng, không suy quyền từ một approve chung.

### 3.4. Success, lỗi và dữ liệu không tin cậy

Success MCP có `isError:false` (hoặc bỏ trường này), `structuredContent` đúng outputSchema và `content[0].text` là JSON của **cùng object**. Mọi outputSchema thành công có root `{result,meta}`; meta mang version, trace/as_of và coverage. `_meta.provenance.untrusted_content=true` mặc định cả success/lỗi, nguồn `workspace_data`. Server instructions yêu cầu coi tên/ghi chú/tài liệu/diff/error detail của doanh nghiệp là dữ liệu, không phải chỉ thị hay quyền gọi tool. Không gửi blob/secret/PII không cần thiết; resource/content/link đều qua cùng ACL.

Lỗi nghiệp vụ dùng `isError:true` và `content:[{type:"text",text:<JSON {error:...}>}]`, **không có `structuredContent`** vì outputSchema chỉ mô tả thành công. JSON-RPC lỗi framing/unknown method/unknown tool vẫn ở tầng giao thức; lỗi argument của tool đã biết được normalize theo schema lỗi bên dưới nếu request đã tới dispatch. Không trả success với `error` lồng bên trong. Remediation luôn nêu bước tiếp theo, không lộ SQL, token, stacktrace hay sự tồn tại dữ liệu ngoài quyền.

## 4. Phân trang, giới hạn và coverage theo REA

Mọi danh sách có `page={offset,limit,snapshot}` đầu vào; mặc định offset=0, limit=64 là kích thước trang khởi điểm, **không phải trần vì “agent ít token”**. Server công bố giới hạn thực tế và căn cứ qua `inspect_workspace.limits`: response bytes của transport/client đã thương lượng, timeout/capacity, TTL snapshot/preview, max query cost đã đo. Schema chỉ ràng buộc `limit>=1`; vượt khả năng thực tế trả `resource_constraint` với reported limits, không clamp âm thầm. Chưa có phép đo thì không tuyên bố trần hiệu năng hay budget toàn catalog đã được chứng minh.

Snapshot đầu tiên cố định tập dữ liệu/sort/as_of theo principal. Lọc IAM/RLS/field access và filter trước sort rồi page; sort luôn thêm ID ổn định. Snapshot tiếp theo phải khớp tenant/actor/workspace/filter/sort/fields/detail, không tự tái sử dụng cho query khác. `limit` có thể đổi nhưng offset theo tập snapshot; snapshot hết hạn trả `snapshot_expired`, không tự trộn trang dữ liệu mới. Thu hồi quyền giữa các trang làm snapshot mất hiệu lực; không phục vụ quyền cũ.

`page.returned` bằng số item thực tế; `next_offset=null` khi hết phần tập snapshot quan sát được. `total` chỉ đếm tập caller được phép thấy sau filter, hoặc null khi nguồn không biết đủ; không lấy total toàn tenant rồi lọc kết quả. `coverage.scope=authorized_snapshot`; `source_exhaustive` nói nguồn có đầy đủ không, `exhaustive` nói đã trả hết tập quan sát được và đủ facet hay chưa. `exhaustive=true` chỉ khi source_exhaustive=true, trang cuối và không còn unknown. Một trang chưa cuối có exhaustive=false nhưng truncated=false: phân trang có chủ ý khác với mất dữ liệu.

`truncated` chỉ true khi nguồn thực sự đã cắt/mất dữ liệu; đi kèm residual_unknowns và continuation khi có. Không cắt chuỗi hay lược record âm thầm để vừa response. `detail=summary` trả field tối thiểu + ref/revision/omitted_fields; full đọc bằng get hoặc page sau, không ghi “full evidence” vào session ở L0. Field bị che được đánh dấu omission theo policy, không dùng null để giả fact.

Kết quả quá lớn: `resource_constraint`, `details.resource="transport"`, reported limits và continuation `{tool,arguments}` gọi được ngay (có snapshot/offset/field hợp lệ). Nếu một record không vừa, continuation chuyển `get_record` với field subset; blob qua resource có ACL. Với mutation đã commit mà response quá lớn, lỗi kèm operation_ref, commit_state=committed và continuation đọc record/audit theo operation_id và ACL, **không yêu cầu tạo lại bằng key mới**. Progress chỉ phản ánh tiến độ đo được, đơn điệu, không bịa phần trăm; cancel khác timeout, partial observation có provenance.

## 5. Schema dùng chung — JSON Schema 2020-12

Các object đều đóng. `Resource.fields` chứa representation có model/schema revision, không phải dict không kiểm soát. `state`/`fields` có thể rỗng theo primitive; với primitive bất biến, `revision=1`, `updated_at=null`. Core domain fields đầy đủ hơn được nêu ở §8. Các ID/XRN trong ví dụ chỉ là fixture giả định đã được tạo, **không phải fallback của runtime**.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:xt:tool-contracts:draft:common",
  "$defs": {
    "Id": {
      "type": "string",
      "pattern": "^[a-z][a-z0-9_]*$"
    },
    "Revision": {
      "type": "integer",
      "minimum": 1
    },
    "DateTime": {
      "type": "string",
      "format": "date-time"
    },
    "Xrn": {
      "type": "string",
      "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
    },
    "Value": {
      "anyOf": [
        {
          "type": [
            "string",
            "number",
            "boolean",
            "null"
          ]
        },
        {
          "type": "array",
          "items": {
            "type": [
              "string",
              "number",
              "boolean",
              "null"
            ]
          }
        }
      ]
    },
    "FieldValue": {
      "type": "object",
      "properties": {
        "field": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "value": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Value"
        }
      },
      "required": [
        "field",
        "value"
      ],
      "additionalProperties": false
    },
    "Values": {
      "type": "array",
      "items": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/FieldValue"
      },
      "uniqueItems": true
    },
    "PageInput": {
      "type": "object",
      "properties": {
        "offset": {
          "type": "integer",
          "minimum": 0,
          "default": 0
        },
        "limit": {
          "type": "integer",
          "minimum": 1,
          "default": 64
        },
        "snapshot": {
          "type": "string",
          "minLength": 1
        }
      },
      "required": [],
      "additionalProperties": false
    },
    "Page": {
      "type": "object",
      "properties": {
        "offset": {
          "type": "integer",
          "minimum": 0
        },
        "limit": {
          "type": "integer",
          "minimum": 1
        },
        "returned": {
          "type": "integer",
          "minimum": 0
        },
        "next_offset": {
          "anyOf": [
            {
              "type": "integer",
              "minimum": 0
            },
            {
              "type": "null"
            }
          ]
        },
        "total": {
          "anyOf": [
            {
              "type": "integer",
              "minimum": 0
            },
            {
              "type": "null"
            }
          ]
        },
        "snapshot": {
          "type": "string",
          "minLength": 1
        },
        "as_of": {
          "type": "string",
          "format": "date-time"
        },
        "expires_at": {
          "type": "string",
          "format": "date-time"
        }
      },
      "required": [
        "offset",
        "limit",
        "returned",
        "next_offset",
        "total",
        "snapshot",
        "as_of",
        "expires_at"
      ],
      "additionalProperties": false
    },
    "Coverage": {
      "type": "object",
      "properties": {
        "scope": {
          "type": "string",
          "enum": [
            "authorized_snapshot"
          ]
        },
        "source_exhaustive": {
          "type": "boolean"
        },
        "exhaustive": {
          "type": "boolean"
        },
        "truncated": {
          "type": "boolean"
        },
        "residual_unknowns": {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          }
        }
      },
      "required": [
        "scope",
        "source_exhaustive",
        "exhaustive",
        "truncated",
        "residual_unknowns"
      ],
      "additionalProperties": false
    },
    "Meta": {
      "type": "object",
      "properties": {
        "contract_version": {
          "const": "0.1.0-draft"
        },
        "trace_id": {
          "type": "string",
          "minLength": 1
        },
        "as_of": {
          "type": "string",
          "format": "date-time"
        },
        "coverage": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Coverage"
        }
      },
      "required": [
        "contract_version",
        "trace_id",
        "as_of",
        "coverage"
      ],
      "additionalProperties": false
    },
    "Permission": {
      "type": "object",
      "properties": {
        "action": {
          "type": "string",
          "pattern": "^[a-z_]+\\.[a-z_]+$"
        },
        "resource": {
          "type": "string",
          "pattern": "^xrn:"
        },
        "condition": {
          "type": "string",
          "minLength": 1
        }
      },
      "required": [
        "action",
        "resource",
        "condition"
      ],
      "additionalProperties": false
    },
    "Resource": {
      "type": "object",
      "properties": {
        "ref": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "workspace_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "primitive": {
          "type": "string",
          "enum": [
            "Actor",
            "Record",
            "Request",
            "Decision",
            "Task",
            "Document",
            "Schedule",
            "Event",
            "Notification",
            "Policy",
            "Counter",
            "Comment",
            "AuditLog",
            "Ledger"
          ]
        },
        "model_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "model_revision": {
          "type": "integer",
          "minimum": 1
        },
        "revision": {
          "type": "integer",
          "minimum": 1
        },
        "state": {
          "type": "string",
          "minLength": 1
        },
        "fields": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Values"
        },
        "omitted_fields": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_]*$"
          }
        },
        "created_at": {
          "type": "string",
          "format": "date-time"
        },
        "updated_at": {
          "anyOf": [
            {
              "type": "string",
              "format": "date-time"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "ref",
        "workspace_id",
        "primitive",
        "model_id",
        "model_revision",
        "revision",
        "state",
        "fields",
        "omitted_fields",
        "created_at",
        "updated_at"
      ],
      "additionalProperties": false
    },
    "RuleFinding": {
      "type": "object",
      "properties": {
        "rule_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "policy_revision": {
          "type": "integer",
          "minimum": 1
        },
        "severity": {
          "type": "string",
          "enum": [
            "error",
            "warning"
          ]
        },
        "fields": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_]*$"
          }
        },
        "message": {
          "type": "string",
          "minLength": 1
        },
        "remediation": {
          "type": "string",
          "minLength": 1
        }
      },
      "required": [
        "rule_id",
        "policy_revision",
        "severity",
        "fields",
        "message",
        "remediation"
      ],
      "additionalProperties": false
    },
    "Checks": {
      "type": "object",
      "properties": {
        "status": {
          "type": "string",
          "enum": [
            "passed",
            "warning"
          ]
        },
        "policy_revisions": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "policy_ref": {
                "type": "string",
                "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
              },
              "revision": {
                "type": "integer",
                "minimum": 1
              }
            },
            "required": [
              "policy_ref",
              "revision"
            ],
            "additionalProperties": false
          }
        },
        "findings": {
          "type": "array",
          "items": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/RuleFinding"
          }
        }
      },
      "required": [
        "status",
        "policy_revisions",
        "findings"
      ],
      "additionalProperties": false
    },
    "Receipt": {
      "type": "object",
      "properties": {
        "operation_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "idempotency_key": {
          "type": "string",
          "minLength": 1
        },
        "committed_at": {
          "type": "string",
          "format": "date-time"
        },
        "audit_ref": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "event_refs": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
          }
        },
        "execution_status": {
          "type": "string",
          "enum": [
            "committed",
            "queued",
            "waiting",
            "completed"
          ]
        },
        "execution_ref": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^xrn:[a-z][a-z0-9_]*:process_instance:[a-z][a-z0-9_]*$"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "operation_id",
        "idempotency_key",
        "committed_at",
        "audit_ref",
        "event_refs",
        "execution_status",
        "execution_ref"
      ],
      "additionalProperties": false
    },
    "Availability": {
      "type": "object",
      "properties": {
        "tool": {
          "type": "string",
          "minLength": 1
        },
        "available": {
          "type": "boolean"
        },
        "reason": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "remediation": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "required_permissions": {
          "type": "array",
          "items": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Permission"
          }
        },
        "client_requirements": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "max_autonomy": {
          "type": "string",
          "enum": [
            "L0",
            "L1",
            "L2",
            "L3"
          ]
        }
      },
      "required": [
        "tool",
        "available",
        "reason",
        "remediation",
        "required_permissions",
        "client_requirements",
        "max_autonomy"
      ],
      "additionalProperties": false
    },
    "InventoryItem": {
      "type": "object",
      "properties": {
        "kind": {
          "type": "string",
          "enum": [
            "workspace",
            "model",
            "process"
          ]
        },
        "ref": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "workspace_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "name": {
          "type": "string",
          "minLength": 1
        },
        "revision": {
          "type": "integer",
          "minimum": 1
        },
        "status": {
          "type": "string",
          "enum": [
            "draft",
            "active",
            "retired"
          ]
        }
      },
      "required": [
        "kind",
        "ref",
        "workspace_id",
        "name",
        "revision",
        "status"
      ],
      "additionalProperties": false
    },
    "FieldDefinition": {
      "type": "object",
      "properties": {
        "name": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "type": {
          "type": "string",
          "enum": [
            "string",
            "integer",
            "number",
            "boolean",
            "date",
            "datetime",
            "decimal",
            "enum",
            "relation",
            "document"
          ]
        },
        "required": {
          "type": "boolean"
        },
        "nullable": {
          "type": "boolean"
        },
        "multiple": {
          "type": "boolean"
        },
        "enum_values": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "relation_model": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^[a-z][a-z0-9_]*$"
            },
            {
              "type": "null"
            }
          ]
        },
        "read_only": {
          "type": "boolean"
        },
        "sensitive": {
          "type": "boolean"
        },
        "unique": {
          "type": "boolean"
        }
      },
      "required": [
        "name",
        "type",
        "required",
        "nullable",
        "multiple",
        "enum_values",
        "relation_model",
        "read_only",
        "sensitive",
        "unique"
      ],
      "additionalProperties": false
    },
    "RuleDefinition": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "phase": {
          "type": "string",
          "enum": [
            "create",
            "update",
            "delete",
            "submit",
            "transition"
          ]
        },
        "expression": {
          "type": "string",
          "minLength": 1
        },
        "message": {
          "type": "string",
          "minLength": 1
        },
        "severity": {
          "type": "string",
          "enum": [
            "error",
            "warning"
          ]
        }
      },
      "required": [
        "id",
        "phase",
        "expression",
        "message",
        "severity"
      ],
      "additionalProperties": false
    },
    "ModelDefinition": {
      "type": "object",
      "properties": {
        "ref": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "workspace_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "revision": {
          "type": "integer",
          "minimum": 1
        },
        "status": {
          "type": "string",
          "enum": [
            "draft",
            "active",
            "retired"
          ]
        },
        "primitive": {
          "type": "string",
          "minLength": 1
        },
        "fields": {
          "type": "array",
          "items": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/FieldDefinition"
          }
        },
        "rules": {
          "type": "array",
          "items": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/RuleDefinition"
          }
        },
        "protected_fields": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_]*$"
          }
        },
        "policy_refs": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
          }
        },
        "data_schema_uri": {
          "type": "string",
          "format": "uri"
        },
        "data_schema_digest": {
          "type": "string",
          "pattern": "^sha256:[0-9a-f]{64}$"
        }
      },
      "required": [
        "ref",
        "workspace_id",
        "revision",
        "status",
        "primitive",
        "fields",
        "rules",
        "protected_fields",
        "policy_refs",
        "data_schema_uri",
        "data_schema_digest"
      ],
      "additionalProperties": false
    },
    "ProcessStep": {
      "type": "object",
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "kind": {
          "type": "string",
          "enum": [
            "task",
            "decision",
            "rule",
            "wait",
            "internal_action",
            "external_action"
          ]
        },
        "actor_policy_ref": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
            },
            {
              "type": "null"
            }
          ]
        },
        "sla_policy_ref": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
            },
            {
              "type": "null"
            }
          ]
        },
        "effect_names": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "human_required": {
          "type": "boolean"
        },
        "config": {
          "oneOf": [
            {
              "type": "object",
              "properties": {
                "type": {
                  "const": "task"
                },
                "title_template": {
                  "type": "string",
                  "minLength": 1
                },
                "assignee_policy_ref": {
                  "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Xrn"
                },
                "priority": {
                  "enum": [
                    "low",
                    "normal",
                    "high",
                    "urgent"
                  ]
                },
                "bindings": {
                  "type": "array",
                  "items": {
                    "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Binding"
                  }
                }
              },
              "required": [
                "type",
                "title_template",
                "assignee_policy_ref",
                "priority",
                "bindings"
              ],
              "additionalProperties": false
            },
            {
              "type": "object",
              "properties": {
                "type": {
                  "const": "decision"
                },
                "request_model_id": {
                  "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Id"
                },
                "decision_policy_ref": {
                  "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Xrn"
                },
                "quorum": {
                  "type": "integer",
                  "minimum": 1
                }
              },
              "required": [
                "type",
                "request_model_id",
                "decision_policy_ref",
                "quorum"
              ],
              "additionalProperties": false
            },
            {
              "type": "object",
              "properties": {
                "type": {
                  "const": "rule"
                },
                "policy_ref": {
                  "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Xrn"
                },
                "policy_revision": {
                  "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Revision"
                }
              },
              "required": [
                "type",
                "policy_ref",
                "policy_revision"
              ],
              "additionalProperties": false
            },
            {
              "type": "object",
              "properties": {
                "type": {
                  "const": "wait"
                },
                "event_type": {
                  "type": "string",
                  "minLength": 1
                },
                "schedule_ref": {
                  "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Xrn"
                },
                "timeout_policy_ref": {
                  "anyOf": [
                    {
                      "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Xrn"
                    },
                    {
                      "type": "null"
                    }
                  ]
                }
              },
              "required": [
                "type",
                "timeout_policy_ref"
              ],
              "additionalProperties": false,
              "oneOf": [
                {
                  "required": [
                    "event_type"
                  ],
                  "not": {
                    "required": [
                      "schedule_ref"
                    ]
                  }
                },
                {
                  "required": [
                    "schedule_ref"
                  ],
                  "not": {
                    "required": [
                      "event_type"
                    ]
                  }
                }
              ]
            },
            {
              "type": "object",
              "properties": {
                "type": {
                  "const": "action"
                },
                "handler_ref": {
                  "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Xrn"
                },
                "handler_revision": {
                  "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Revision"
                },
                "bindings": {
                  "type": "array",
                  "items": {
                    "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Binding"
                  }
                }
              },
              "required": [
                "type",
                "handler_ref",
                "handler_revision",
                "bindings"
              ],
              "additionalProperties": false
            }
          ]
        }
      },
      "required": [
        "id",
        "kind",
        "actor_policy_ref",
        "sla_policy_ref",
        "effect_names",
        "human_required",
        "config"
      ],
      "additionalProperties": false,
      "allOf": [
        {
          "if": {
            "properties": {
              "kind": {
                "const": "task"
              }
            }
          },
          "then": {
            "properties": {
              "config": {
                "properties": {
                  "type": {
                    "const": "task"
                  }
                }
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "kind": {
                "const": "decision"
              }
            }
          },
          "then": {
            "properties": {
              "config": {
                "properties": {
                  "type": {
                    "const": "decision"
                  }
                }
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "kind": {
                "const": "rule"
              }
            }
          },
          "then": {
            "properties": {
              "config": {
                "properties": {
                  "type": {
                    "const": "rule"
                  }
                }
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "kind": {
                "const": "wait"
              }
            }
          },
          "then": {
            "properties": {
              "config": {
                "properties": {
                  "type": {
                    "const": "wait"
                  }
                }
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "kind": {
                "const": "internal_action"
              }
            }
          },
          "then": {
            "properties": {
              "config": {
                "properties": {
                  "type": {
                    "const": "action"
                  }
                }
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "kind": {
                "const": "external_action"
              }
            }
          },
          "then": {
            "properties": {
              "config": {
                "properties": {
                  "type": {
                    "const": "action"
                  }
                }
              }
            }
          }
        }
      ]
    },
    "ProcessEdge": {
      "type": "object",
      "properties": {
        "from": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "to": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "condition": {
          "anyOf": [
            {
              "type": "string",
              "minLength": 1
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "from",
        "to",
        "condition"
      ],
      "additionalProperties": false
    },
    "ProcessDefinition": {
      "type": "object",
      "properties": {
        "ref": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "workspace_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "revision": {
          "type": "integer",
          "minimum": 1
        },
        "status": {
          "type": "string",
          "enum": [
            "draft",
            "active",
            "retired"
          ]
        },
        "steps": {
          "type": "array",
          "items": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/ProcessStep"
          }
        },
        "edges": {
          "type": "array",
          "items": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/ProcessEdge"
          }
        },
        "input_fields": {
          "type": "array",
          "items": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/FieldDefinition"
          }
        },
        "policy_refs": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
          }
        },
        "effect_names": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "max_autonomy": {
          "type": "string",
          "enum": [
            "L0",
            "L1",
            "L2",
            "L3"
          ]
        },
        "entry_step": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Id"
        }
      },
      "required": [
        "ref",
        "workspace_id",
        "revision",
        "status",
        "steps",
        "edges",
        "input_fields",
        "policy_refs",
        "effect_names",
        "max_autonomy",
        "entry_step"
      ],
      "additionalProperties": false
    },
    "Filter": {
      "type": "object",
      "properties": {
        "field": {
          "type": "string",
          "minLength": 1
        },
        "operator": {
          "type": "string",
          "enum": [
            "eq",
            "ne",
            "lt",
            "lte",
            "gt",
            "gte",
            "in",
            "contains",
            "is_null"
          ]
        },
        "value": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Value"
        }
      },
      "required": [
        "field",
        "operator",
        "value"
      ],
      "additionalProperties": false
    },
    "Sort": {
      "type": "object",
      "properties": {
        "field": {
          "type": "string",
          "minLength": 1
        },
        "direction": {
          "type": "string",
          "enum": [
            "asc",
            "desc"
          ]
        }
      },
      "required": [
        "field",
        "direction"
      ],
      "additionalProperties": false
    },
    "Patch": {
      "type": "object",
      "properties": {
        "operation": {
          "type": "string",
          "enum": [
            "set",
            "unset"
          ]
        },
        "field": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "value": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Value"
        }
      },
      "required": [
        "operation",
        "field"
      ],
      "additionalProperties": false,
      "allOf": [
        {
          "if": {
            "properties": {
              "operation": {
                "const": "set"
              }
            }
          },
          "then": {
            "required": [
              "value"
            ]
          },
          "else": {
            "not": {
              "required": [
                "value"
              ]
            }
          }
        }
      ]
    },
    "MutationResult": {
      "type": "object",
      "properties": {
        "record": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Resource"
        },
        "checks": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Checks"
        },
        "receipt": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Receipt"
        }
      },
      "required": [
        "record",
        "checks",
        "receipt"
      ],
      "additionalProperties": false
    },
    "RequestResult": {
      "type": "object",
      "properties": {
        "request": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Resource"
        },
        "process_instance_ref": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
            },
            {
              "type": "null"
            }
          ]
        },
        "pending_step_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_]*$"
          }
        },
        "checks": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Checks"
        },
        "receipt": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Receipt"
        }
      },
      "required": [
        "request",
        "process_instance_ref",
        "pending_step_ids",
        "checks",
        "receipt"
      ],
      "additionalProperties": false
    },
    "SlaInput": {
      "type": "object",
      "properties": {
        "policy_ref": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "policy_revision": {
          "type": "integer",
          "minimum": 1
        },
        "due_at": {
          "anyOf": [
            {
              "type": "string",
              "format": "date-time"
            },
            {
              "type": "null"
            }
          ]
        },
        "timezone": {
          "type": "string",
          "minLength": 1
        }
      },
      "required": [
        "policy_ref",
        "policy_revision",
        "due_at",
        "timezone"
      ],
      "additionalProperties": false
    },
    "SlaView": {
      "type": "object",
      "properties": {
        "policy_ref": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "policy_revision": {
          "type": "integer",
          "minimum": 1
        },
        "calendar_ref": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
            },
            {
              "type": "null"
            }
          ]
        },
        "calendar_revision": {
          "anyOf": [
            {
              "type": "integer",
              "minimum": 1
            },
            {
              "type": "null"
            }
          ]
        },
        "timezone": {
          "type": "string",
          "minLength": 1
        },
        "started_at": {
          "type": "string",
          "format": "date-time"
        },
        "due_at": {
          "type": "string",
          "format": "date-time"
        },
        "paused_seconds": {
          "type": "integer",
          "minimum": 0
        },
        "status": {
          "type": "string",
          "enum": [
            "within",
            "paused",
            "breached",
            "met"
          ]
        },
        "next_escalation_at": {
          "anyOf": [
            {
              "type": "string",
              "format": "date-time"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "policy_ref",
        "policy_revision",
        "calendar_ref",
        "calendar_revision",
        "timezone",
        "started_at",
        "due_at",
        "paused_seconds",
        "status",
        "next_escalation_at"
      ],
      "additionalProperties": false
    },
    "TaskResult": {
      "type": "object",
      "properties": {
        "task": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Resource"
        },
        "sla": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/SlaView"
        },
        "checks": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Checks"
        },
        "receipt": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Receipt"
        }
      },
      "required": [
        "task",
        "sla",
        "checks",
        "receipt"
      ],
      "additionalProperties": false
    },
    "InboxItem": {
      "type": "object",
      "properties": {
        "kind": {
          "type": "string",
          "enum": [
            "request",
            "task",
            "notification"
          ]
        },
        "ref": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "revision": {
          "type": "integer",
          "minimum": 1
        },
        "title": {
          "type": "string"
        },
        "state": {
          "type": "string"
        },
        "due_at": {
          "anyOf": [
            {
              "type": "string",
              "format": "date-time"
            },
            {
              "type": "null"
            }
          ]
        },
        "priority": {
          "type": "string",
          "enum": [
            "low",
            "normal",
            "high",
            "urgent"
          ]
        },
        "available_actions": {
          "type": "array",
          "items": {
            "type": "string"
          }
        }
      },
      "required": [
        "kind",
        "ref",
        "revision",
        "title",
        "state",
        "due_at",
        "priority",
        "available_actions"
      ],
      "additionalProperties": false
    },
    "PendingStep": {
      "type": "object",
      "properties": {
        "step_id": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "status": {
          "type": "string",
          "enum": [
            "ready",
            "running",
            "waiting",
            "blocked",
            "failed"
          ]
        },
        "subject_ref": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
            },
            {
              "type": "null"
            }
          ]
        },
        "waiting_on": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
          }
        },
        "next_actions": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "sla": {
          "anyOf": [
            {
              "$ref": "urn:xt:tool-contracts:draft:common#/$defs/SlaView"
            },
            {
              "type": "null"
            }
          ]
        },
        "step_revision": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Revision"
        },
        "request_ref": {
          "anyOf": [
            {
              "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Xrn"
            },
            {
              "type": "null"
            }
          ]
        },
        "request_revision": {
          "anyOf": [
            {
              "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Revision"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "step_id",
        "status",
        "subject_ref",
        "waiting_on",
        "next_actions",
        "sla",
        "step_revision",
        "request_ref",
        "request_revision"
      ],
      "additionalProperties": false
    },
    "ProcessInstance": {
      "type": "object",
      "properties": {
        "ref": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "revision": {
          "type": "integer",
          "minimum": 1
        },
        "process_ref": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "process_revision": {
          "type": "integer",
          "minimum": 1
        },
        "subject_ref": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
            },
            {
              "type": "null"
            }
          ]
        },
        "status": {
          "type": "string",
          "enum": [
            "queued",
            "running",
            "waiting",
            "blocked",
            "completed",
            "failed",
            "cancelled"
          ]
        },
        "started_at": {
          "type": "string",
          "format": "date-time"
        },
        "completed_at": {
          "anyOf": [
            {
              "type": "string",
              "format": "date-time"
            },
            {
              "type": "null"
            }
          ]
        },
        "pending_external_effects": {
          "type": "integer",
          "minimum": 0
        },
        "last_event_ref": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "ref",
        "revision",
        "process_ref",
        "process_revision",
        "subject_ref",
        "status",
        "started_at",
        "completed_at",
        "pending_external_effects",
        "last_event_ref"
      ],
      "additionalProperties": false
    },
    "Preview": {
      "type": "object",
      "properties": {
        "digest": {
          "type": "string",
          "pattern": "^sha256:[0-9a-f]{64}$"
        },
        "base_revision": {
          "type": "integer",
          "minimum": 0
        },
        "policy_revisions": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "ref": {
                "type": "string",
                "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
              },
              "revision": {
                "type": "integer",
                "minimum": 1
              }
            },
            "required": [
              "ref",
              "revision"
            ],
            "additionalProperties": false
          }
        },
        "expires_at": {
          "type": "string",
          "format": "date-time"
        },
        "breaking": {
          "type": "boolean"
        },
        "affected_records": {
          "anyOf": [
            {
              "type": "integer",
              "minimum": 0
            },
            {
              "type": "null"
            }
          ]
        },
        "coverage": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Coverage"
        },
        "diff": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "path": {
                "type": "string"
              },
              "before": {
                "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Value"
              },
              "after": {
                "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Value"
              }
            },
            "required": [
              "path",
              "before",
              "after"
            ],
            "additionalProperties": false
          }
        },
        "findings": {
          "type": "array",
          "items": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/RuleFinding"
          }
        },
        "required_permissions": {
          "type": "array",
          "items": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Permission"
          }
        },
        "migration_plan": {
          "type": "string"
        },
        "rollback_plan": {
          "type": "string"
        },
        "effect_names": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "preview_token": {
          "type": "string",
          "minLength": 1
        }
      },
      "required": [
        "digest",
        "base_revision",
        "policy_revisions",
        "expires_at",
        "breaking",
        "affected_records",
        "coverage",
        "diff",
        "findings",
        "required_permissions",
        "migration_plan",
        "rollback_plan",
        "effect_names",
        "preview_token"
      ],
      "additionalProperties": false
    },
    "ProposalResult": {
      "type": "object",
      "properties": {
        "dry_run": {
          "type": "boolean"
        },
        "preview": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Preview"
        },
        "proposal_ref": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
            },
            {
              "type": "null"
            }
          ]
        },
        "request_ref": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
            },
            {
              "type": "null"
            }
          ]
        },
        "receipt": {
          "anyOf": [
            {
              "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Receipt"
            },
            {
              "type": "null"
            }
          ]
        },
        "request_state": {
          "anyOf": [
            {
              "enum": [
                "draft"
              ]
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "dry_run",
        "preview",
        "proposal_ref",
        "request_ref",
        "receipt",
        "request_state"
      ],
      "additionalProperties": false,
      "allOf": [
        {
          "if": {
            "properties": {
              "dry_run": {
                "const": true
              }
            }
          },
          "then": {
            "properties": {
              "proposal_ref": {
                "type": "null"
              },
              "request_ref": {
                "type": "null"
              },
              "receipt": {
                "type": "null"
              },
              "request_state": {
                "type": "null"
              }
            }
          },
          "else": {
            "properties": {
              "proposal_ref": {
                "type": "string",
                "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
              },
              "request_ref": {
                "type": "string",
                "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
              },
              "receipt": {
                "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Receipt"
              },
              "request_state": {
                "const": "draft"
              }
            }
          }
        }
      ]
    },
    "ModelOperation": {
      "type": "object",
      "properties": {
        "operation": {
          "type": "string",
          "enum": [
            "add_field",
            "change_field",
            "remove_field",
            "add_rule",
            "remove_rule"
          ]
        },
        "name": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "field_definition": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/FieldDefinition"
        },
        "rule_definition": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/RuleDefinition"
        }
      },
      "required": [
        "operation",
        "name"
      ],
      "additionalProperties": false,
      "allOf": [
        {
          "if": {
            "properties": {
              "operation": {
                "enum": [
                  "add_field",
                  "change_field"
                ]
              }
            }
          },
          "then": {
            "required": [
              "field_definition"
            ],
            "not": {
              "required": [
                "rule_definition"
              ]
            }
          },
          "else": {
            "not": {
              "required": [
                "field_definition"
              ]
            }
          }
        },
        {
          "if": {
            "properties": {
              "operation": {
                "const": "add_rule"
              }
            }
          },
          "then": {
            "required": [
              "rule_definition"
            ]
          },
          "else": {
            "not": {
              "required": [
                "rule_definition"
              ]
            }
          }
        }
      ]
    },
    "ProcessChange": {
      "type": "object",
      "properties": {
        "steps": {
          "type": "array",
          "items": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/ProcessStep"
          },
          "minItems": 1
        },
        "edges": {
          "type": "array",
          "items": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/ProcessEdge"
          }
        },
        "input_fields": {
          "type": "array",
          "items": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/FieldDefinition"
          }
        },
        "policy_refs": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
          }
        },
        "entry_step": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        }
      },
      "required": [
        "steps",
        "edges",
        "input_fields",
        "policy_refs",
        "entry_step"
      ],
      "additionalProperties": false
    },
    "HistoryEntry": {
      "type": "object",
      "properties": {
        "ref": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "at": {
          "type": "string",
          "format": "date-time"
        },
        "actor_ref": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "on_behalf_of": {
          "anyOf": [
            {
              "type": "string",
              "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
            },
            {
              "type": "null"
            }
          ]
        },
        "action": {
          "type": "string",
          "minLength": 1
        },
        "resource_ref": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "revision": {
          "anyOf": [
            {
              "type": "integer",
              "minimum": 1
            },
            {
              "type": "null"
            }
          ]
        },
        "outcome": {
          "type": "string",
          "enum": [
            "allowed",
            "denied",
            "failed"
          ]
        },
        "before_hash": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "after_hash": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        },
        "reason": {
          "type": "string"
        },
        "evidence_refs": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
          }
        }
      },
      "required": [
        "ref",
        "at",
        "actor_ref",
        "on_behalf_of",
        "action",
        "resource_ref",
        "revision",
        "outcome",
        "before_hash",
        "after_hash",
        "reason",
        "evidence_refs"
      ],
      "additionalProperties": false
    },
    "FacetStatus": {
      "type": "object",
      "properties": {
        "facet": {
          "type": "string",
          "enum": [
            "history",
            "audit",
            "explain"
          ]
        },
        "status": {
          "type": "string",
          "enum": [
            "available",
            "denied",
            "unsupported",
            "partial"
          ]
        },
        "reason": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ]
        }
      },
      "required": [
        "facet",
        "status",
        "reason"
      ],
      "additionalProperties": false
    },
    "Explanation": {
      "type": "object",
      "properties": {
        "subject_ref": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "policy_refs": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
          }
        },
        "policy_revisions": {
          "type": "array",
          "items": {
            "type": "integer",
            "minimum": 1
          }
        },
        "rule_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_]*$"
          }
        },
        "facts": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Values"
        },
        "inferences": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "unknowns": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "evidence_refs": {
          "type": "array",
          "items": {
            "type": "string",
            "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
          }
        }
      },
      "required": [
        "subject_ref",
        "policy_refs",
        "policy_revisions",
        "rule_ids",
        "facts",
        "inferences",
        "unknowns",
        "evidence_refs"
      ],
      "additionalProperties": false
    },
    "Binding": {
      "type": "object",
      "properties": {
        "parameter": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Id"
        },
        "source": {
          "oneOf": [
            {
              "type": "object",
              "properties": {
                "kind": {
                  "const": "literal"
                },
                "value": {
                  "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Value"
                }
              },
              "required": [
                "kind",
                "value"
              ],
              "additionalProperties": false
            },
            {
              "type": "object",
              "properties": {
                "kind": {
                  "const": "subject_field"
                },
                "field": {
                  "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Id"
                }
              },
              "required": [
                "kind",
                "field"
              ],
              "additionalProperties": false
            },
            {
              "type": "object",
              "properties": {
                "kind": {
                  "const": "process_parameter"
                },
                "name": {
                  "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Id"
                }
              },
              "required": [
                "kind",
                "name"
              ],
              "additionalProperties": false
            },
            {
              "type": "object",
              "properties": {
                "kind": {
                  "const": "step_output"
                },
                "step_id": {
                  "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Id"
                },
                "field": {
                  "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Id"
                }
              },
              "required": [
                "kind",
                "step_id",
                "field"
              ],
              "additionalProperties": false
            }
          ]
        }
      },
      "required": [
        "parameter",
        "source"
      ],
      "additionalProperties": false
    }
  }
}
```

## 6. Catalog 18 ToolContract

Mỗi khối gồm inputSchema, outputSchema của structuredContent, effects, annotations suy ra, quyền IAM, toàn bộ mã lỗi có thể trả và ví dụ gọi (phần `examples` chính là arguments của `tools/call`). `iam.condition` quy định nhánh AND/OR; không yêu cầu mọi action của mọi nhánh cho một lời gọi. Ngoài các mã liệt kê, lỗi framing là lỗi MCP/JSON-RPC ở tầng giao thức.

### 6.1. `inspect_workspace`

Khám phá workspace, danh mục model/process và khả năng của phiên; catalog tool luôn đầy đủ.

Danh mục không có phần tử chỉ có nghĩa không có phần tử hiển thị trong snapshot. Availability không thay thế kiểm tra quyền lúc gọi.

```json
{
  "name": "inspect_workspace",
  "description": "Khám phá workspace, danh mục model/process và khả năng của phiên; catalog tool luôn đầy đủ.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:inspect_workspace:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "kinds": {
        "type": "array",
        "items": {
          "type": "string",
          "enum": [
            "workspace",
            "model",
            "process"
          ]
        },
        "minItems": 1,
        "uniqueItems": true
      },
      "page": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/PageInput"
      }
    },
    "required": [],
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:inspect_workspace:output",
    "type": "object",
    "properties": {
      "result": {
        "type": "object",
        "properties": {
          "principal": {
            "type": "object",
            "properties": {
              "actor_ref": {
                "type": "string",
                "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
              },
              "kind": {
                "type": "string",
                "enum": [
                  "human",
                  "agent"
                ]
              },
              "on_behalf_of": {
                "anyOf": [
                  {
                    "type": "string",
                    "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
                  },
                  {
                    "type": "null"
                  }
                ]
              },
              "tenant_id": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_]*$"
              },
              "scope_names": {
                "type": "array",
                "items": {
                  "type": "string"
                }
              }
            },
            "required": [
              "actor_ref",
              "kind",
              "on_behalf_of",
              "tenant_id",
              "scope_names"
            ],
            "additionalProperties": false
          },
          "catalog_digest": {
            "type": "string"
          },
          "tool_count": {
            "const": 18
          },
          "tool_availability": {
            "type": "array",
            "items": {
              "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Availability"
            }
          },
          "items": {
            "type": "array",
            "items": {
              "$ref": "urn:xt:tool-contracts:draft:common#/$defs/InventoryItem"
            }
          },
          "page": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Page"
          },
          "limits": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "name": {
                  "type": "string"
                },
                "value": {
                  "type": "integer",
                  "minimum": 1
                },
                "unit": {
                  "type": "string"
                },
                "basis": {
                  "type": "string"
                }
              },
              "required": [
                "name",
                "value",
                "unit",
                "basis"
              ],
              "additionalProperties": false
            }
          }
        },
        "required": [
          "principal",
          "catalog_digest",
          "tool_count",
          "tool_availability",
          "items",
          "page",
          "limits"
        ],
        "additionalProperties": false
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": false,
    "mutates_audit": false,
    "enqueues_work": false,
    "replaces_state": false,
    "may_discard_data": false,
    "changes_definitions": false,
    "affects_others": false,
    "accesses_external_network": false,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  },
  "max_autonomy": "L0",
  "iam": [
    {
      "action": "workspace.discover",
      "resource": "xrn:{tenant}:workspace:{workspace_id}",
      "condition": "Chỉ các workspace được phép thấy; bỏ workspace_id không cho quyền xuyên tenant."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "snapshot_expired"
  ],
  "examples": [
    {
      "kinds": [
        "workspace",
        "model",
        "process"
      ],
      "page": {
        "limit": 64
      }
    }
  ],
  "title": "Inspect Workspace",
  "returns_untrusted_content": true
}
```

### 6.2. `describe_model`

Đọc phiên bản model, các field/rule/field bảo vệ và tham chiếu schema dữ liệu.

data_schema_uri là MCP resource JSON Schema 2020-12 content-addressed, đọc qua resources/read cùng model.read; không fetch URL tùy ý. Schema động là dữ liệu, không sửa schema tools/list.

```json
{
  "name": "describe_model",
  "description": "Đọc phiên bản model, các field/rule/field bảo vệ và tham chiếu schema dữ liệu.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:describe_model:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "model_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "revision": {
        "type": "integer",
        "minimum": 1
      }
    },
    "required": [
      "workspace_id",
      "model_id"
    ],
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:describe_model:output",
    "type": "object",
    "properties": {
      "result": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/ModelDefinition"
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": false,
    "mutates_audit": false,
    "enqueues_work": false,
    "replaces_state": false,
    "may_discard_data": false,
    "changes_definitions": false,
    "affects_others": false,
    "accesses_external_network": false,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  },
  "max_autonomy": "L0",
  "iam": [
    {
      "action": "model.read",
      "resource": "xrn:{tenant}:model:{model_id}",
      "condition": "revision bỏ trống chọn bản active tại as_of; luôn trả revision đã chọn."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "model_id": "work_item"
    }
  ],
  "title": "Describe Model",
  "returns_untrusted_content": true
}
```

### 6.3. `describe_process`

Đọc graph, tham số, SLA, human gates và effects của một định nghĩa quy trình.

Definition khai external_action phải công khai effect; loại step không hỗ trợ trả capability_unavailable, không giả thực thi.

```json
{
  "name": "describe_process",
  "description": "Đọc graph, tham số, SLA, human gates và effects của một định nghĩa quy trình.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:describe_process:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "process_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "revision": {
        "type": "integer",
        "minimum": 1
      }
    },
    "required": [
      "workspace_id",
      "process_id"
    ],
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:describe_process:output",
    "type": "object",
    "properties": {
      "result": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/ProcessDefinition"
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": false,
    "mutates_audit": false,
    "enqueues_work": false,
    "replaces_state": false,
    "may_discard_data": false,
    "changes_definitions": false,
    "affects_others": false,
    "accesses_external_network": false,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  },
  "max_autonomy": "L0",
  "iam": [
    {
      "action": "process.read",
      "resource": "xrn:{tenant}:process:{process_id}",
      "condition": "Chỉ đọc định nghĩa được cấp quyền."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "process_id": "review_cycle"
    }
  ],
  "title": "Describe Process",
  "returns_untrusted_content": true
}
```

### 6.4. `search_records`

Tìm bản ghi bằng bộ lọc có kiểu, phạm vi quyền và thứ tự ổn định.

Không SQL/regex/code tự do. Tên field, kiểu toán tử và giá trị phải hợp model_revision snapshot. ID là tie-breaker cuối. summary không bỏ cả record hoặc giả null cho field bị che.

```json
{
  "name": "search_records",
  "description": "Tìm bản ghi bằng bộ lọc có kiểu, phạm vi quyền và thứ tự ổn định.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:search_records:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "model_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "filters": {
        "type": "array",
        "items": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Filter"
        }
      },
      "sort": {
        "type": "array",
        "items": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Sort"
        }
      },
      "fields": {
        "type": "array",
        "items": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "uniqueItems": true
      },
      "detail": {
        "type": "string",
        "enum": [
          "summary",
          "full"
        ]
      },
      "page": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/PageInput"
      }
    },
    "required": [
      "workspace_id",
      "model_id"
    ],
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:search_records:output",
    "type": "object",
    "properties": {
      "result": {
        "type": "object",
        "properties": {
          "items": {
            "type": "array",
            "items": {
              "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Resource"
            }
          },
          "page": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Page"
          }
        },
        "required": [
          "items",
          "page"
        ],
        "additionalProperties": false
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": false,
    "mutates_audit": false,
    "enqueues_work": false,
    "replaces_state": false,
    "may_discard_data": false,
    "changes_definitions": false,
    "affects_others": false,
    "accesses_external_network": false,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  },
  "max_autonomy": "L0",
  "iam": [
    {
      "action": "record.search",
      "resource": "xrn:{tenant}:{model}:*",
      "condition": "Lọc quyền hàng và field trước khi lọc nghiệp vụ/sort/page; primitive nhạy cảm cần quyền đọc chuyên biệt."
    },
    {
      "action": "decision.read",
      "resource": "xrn:{tenant}:{model}:*",
      "condition": "Khi primitive=Decision; lọc ACL trước page."
    },
    {
      "action": "audit.read",
      "resource": "xrn:{tenant}:{model}:*",
      "condition": "Khi primitive=AuditLog; lọc ACL trước page."
    },
    {
      "action": "ledger.read",
      "resource": "xrn:{tenant}:{model}:*",
      "condition": "Khi primitive=Ledger; lọc ACL trước page."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found",
    "snapshot_expired"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "model_id": "work_item",
      "filters": [
        {
          "field": "state",
          "operator": "eq",
          "value": "open"
        }
      ],
      "detail": "summary",
      "page": {
        "limit": 20
      }
    }
  ],
  "title": "Search Records",
  "returns_untrusted_content": true
}
```

### 6.5. `get_record`

Đọc một primitive/bản ghi theo XRN và revision, có thể chọn field.

revision bỏ trống đọc hiện hành; revision chỉ định đọc lịch sử nếu có history.read. Resource.fields là dữ liệu đã lọc quyền; omitted_fields và coverage nói phần không trả, không lộ giá trị.

```json
{
  "name": "get_record",
  "description": "Đọc một primitive/bản ghi theo XRN và revision, có thể chọn field.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:get_record:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "revision": {
        "type": "integer",
        "minimum": 1
      },
      "fields": {
        "type": "array",
        "items": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "uniqueItems": true
      },
      "detail": {
        "type": "string",
        "enum": [
          "summary",
          "full"
        ]
      }
    },
    "required": [
      "workspace_id",
      "ref"
    ],
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:get_record:output",
    "type": "object",
    "properties": {
      "result": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Resource"
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": false,
    "mutates_audit": false,
    "enqueues_work": false,
    "replaces_state": false,
    "may_discard_data": false,
    "changes_definitions": false,
    "affects_others": false,
    "accesses_external_network": false,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  },
  "max_autonomy": "L0",
  "iam": [
    {
      "action": "record.read",
      "resource": "xrn:{tenant}:{model}:{id}",
      "condition": "Quyền hàng/field; Decision, AuditLog, Ledger còn cần decision.read, audit.read, ledger.read tương ứng."
    },
    {
      "action": "decision.read",
      "resource": "xrn:{tenant}:{model}:{id}",
      "condition": "Khi primitive=Decision, cùng record.read."
    },
    {
      "action": "audit.read",
      "resource": "xrn:{tenant}:{model}:{id}",
      "condition": "Khi primitive=AuditLog, cùng record.read."
    },
    {
      "action": "ledger.read",
      "resource": "xrn:{tenant}:{model}:{id}",
      "condition": "Khi primitive=Ledger, cùng record.read."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "ref": "xrn:t_demo:work_item:rec_a",
      "detail": "full"
    }
  ],
  "title": "Get Record",
  "returns_untrusted_content": true
}
```

### 6.6. `create_record`

Tạo Record hoặc bản nháp Request/Document/Schedule, hoặc Comment theo model đang active.

Server gán ID/revision=1/state=draft hoặc initial safe state; cấm input field hệ thống. Không tạo Task, Decision, Policy, Actor, Event, Notification, Counter, AuditLog, Ledger hoặc định nghĩa model/process bằng đường CRUD chung. Comment cần comment.create trên subject.

```json
{
  "name": "create_record",
  "description": "Tạo Record hoặc bản nháp Request/Document/Schedule, hoặc Comment theo model đang active.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:create_record:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "model_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "expected_model_revision": {
        "type": "integer",
        "minimum": 1
      },
      "values": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Values"
      },
      "idempotency_key": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "workspace_id",
      "model_id",
      "expected_model_revision",
      "values",
      "idempotency_key"
    ],
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:create_record:output",
    "type": "object",
    "properties": {
      "result": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/MutationResult"
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": true,
    "mutates_audit": true,
    "enqueues_work": false,
    "replaces_state": false,
    "may_discard_data": false,
    "changes_definitions": false,
    "affects_others": false,
    "accesses_external_network": false,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  },
  "max_autonomy": "L1",
  "iam": [
    {
      "action": "record.create",
      "resource": "xrn:{tenant}:{model}:*",
      "condition": "Resource collection trước cấp ID; kiểm tra ownership và liên kết đến từng XRN; Request draft cần request.create."
    },
    {
      "action": "request.create",
      "resource": "xrn:{tenant}:{model}:*",
      "condition": "Đồng thời record.create khi model primitive=Request; chỉ draft (hoặc returned khi update), không đổi route/quyết định."
    },
    {
      "action": "document.create",
      "resource": "xrn:{tenant}:{model}:*",
      "condition": "Đồng thời record.create khi model primitive=Document; chỉ bản nháp, không ký/ban hành."
    },
    {
      "action": "schedule.create",
      "resource": "xrn:{tenant}:{model}:*",
      "condition": "Đồng thời record.create khi model primitive=Schedule; chỉ lịch draft chưa được engine kích hoạt."
    },
    {
      "action": "comment.create",
      "resource": "xrn:{tenant}:{model}:*",
      "condition": "Đồng thời record.create khi model primitive=Comment; kiểm author/visibility; action xét thêm trên subject XRN, không chỉ Comment."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found",
    "revision_conflict",
    "rule_violation",
    "precondition_failed",
    "idempotency_conflict",
    "integrity_mismatch",
    "cleanup_incomplete"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "model_id": "work_item",
      "expected_model_revision": 1,
      "values": [
        {
          "field": "title",
          "value": "Chuẩn bị tài liệu"
        }
      ],
      "idempotency_key": "create-work-a"
    }
  ],
  "title": "Create Record",
  "returns_untrusted_content": true
}
```

### 6.7. `update_record`

Sửa các field được phép của Record/bản nháp, có CAS và kiểm tra Poka-yoke.

Bản nháp Request chỉ sửa khi draft/returned, người đề nghị đủ quyền; submit lại luôn route lại. Document đã ký/ban hành không sửa. Patch trùng field, unset field required, sửa field protected đều bị từ chối; không có force/bypass_rules.

```json
{
  "name": "update_record",
  "description": "Sửa các field được phép của Record/bản nháp, có CAS và kiểm tra Poka-yoke.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:update_record:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "expected_revision": {
        "type": "integer",
        "minimum": 1
      },
      "expected_model_revision": {
        "type": "integer",
        "minimum": 1
      },
      "patch": {
        "type": "array",
        "items": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Patch"
        },
        "minItems": 1
      },
      "idempotency_key": {
        "type": "string",
        "minLength": 1
      },
      "approval_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      }
    },
    "required": [
      "workspace_id",
      "ref",
      "expected_revision",
      "expected_model_revision",
      "patch",
      "idempotency_key"
    ],
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:update_record:output",
    "type": "object",
    "properties": {
      "result": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/MutationResult"
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": true,
    "mutates_audit": true,
    "enqueues_work": false,
    "replaces_state": true,
    "may_discard_data": false,
    "changes_definitions": false,
    "affects_others": false,
    "accesses_external_network": false,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": true,
    "idempotentHint": true,
    "openWorldHint": false
  },
  "max_autonomy": "L2",
  "iam": [
    {
      "action": "record.update",
      "resource": "xrn:{tenant}:{model}:{id}",
      "condition": "Field allowlist + CAS; field có tác động L2 cần human approval_ref; state do engine quản lý luôn cấm."
    },
    {
      "action": "request.update",
      "resource": "xrn:{tenant}:request:{id}",
      "condition": "Đồng thời record.update khi model primitive=Request; chỉ draft (hoặc returned khi update), không đổi route/quyết định."
    },
    {
      "action": "document.update",
      "resource": "xrn:{tenant}:{model}:{id}",
      "condition": "Đồng thời record.update khi model primitive=Document; chỉ bản nháp, không ký/ban hành."
    },
    {
      "action": "schedule.update",
      "resource": "xrn:{tenant}:{model}:{id}",
      "condition": "Đồng thời record.update khi model primitive=Schedule; chỉ lịch draft chưa được engine kích hoạt."
    },
    {
      "action": "comment.update",
      "resource": "xrn:{tenant}:{model}:{id}",
      "condition": "Đồng thời record.update khi model primitive=Comment; kiểm author/visibility; action xét thêm trên subject XRN, không chỉ Comment."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found",
    "revision_conflict",
    "rule_violation",
    "precondition_failed",
    "approval_required",
    "stale_approval",
    "idempotency_conflict",
    "integrity_mismatch",
    "cleanup_incomplete"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "ref": "xrn:t_demo:work_item:rec_a",
      "expected_revision": 3,
      "expected_model_revision": 1,
      "patch": [
        {
          "operation": "set",
          "field": "title",
          "value": "Tài liệu đã rà soát"
        }
      ],
      "idempotency_key": "rename-work-a"
    }
  ],
  "title": "Update Record",
  "returns_untrusted_content": true
}
```

### 6.8. `delete_record`

Xóa mềm bản ghi được phép xóa; giữ tombstone và lịch sử, không cascade ngầm.

Không hard-delete, không xóa sổ bất biến, Task/Request đang chạy, Document đã ký hoặc bản ghi còn ràng buộc. Soft-delete vẫn destructive vì làm mất khả năng truy cập nghiệp vụ. get với quyền lịch sử có thể đọc tombstone.

```json
{
  "name": "delete_record",
  "description": "Xóa mềm bản ghi được phép xóa; giữ tombstone và lịch sử, không cascade ngầm.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:delete_record:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "expected_revision": {
        "type": "integer",
        "minimum": 1
      },
      "reason": {
        "type": "string",
        "minLength": 1
      },
      "idempotency_key": {
        "type": "string",
        "minLength": 1
      },
      "approval_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      }
    },
    "required": [
      "workspace_id",
      "ref",
      "expected_revision",
      "reason",
      "idempotency_key",
      "approval_ref"
    ],
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:delete_record:output",
    "type": "object",
    "properties": {
      "result": {
        "type": "object",
        "properties": {
          "ref": {
            "type": "string",
            "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
          },
          "revision": {
            "type": "integer",
            "minimum": 1
          },
          "deleted_at": {
            "type": "string",
            "format": "date-time"
          },
          "receipt": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Receipt"
          }
        },
        "required": [
          "ref",
          "revision",
          "deleted_at",
          "receipt"
        ],
        "additionalProperties": false
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": true,
    "mutates_audit": true,
    "enqueues_work": false,
    "replaces_state": true,
    "may_discard_data": true,
    "changes_definitions": false,
    "affects_others": false,
    "accesses_external_network": false,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": true,
    "idempotentHint": true,
    "openWorldHint": false
  },
  "max_autonomy": "L3",
  "iam": [
    {
      "action": "record.delete",
      "resource": "xrn:{tenant}:{model}:{id}",
      "condition": "Human approval gắn đúng revision/reason; retention/legal hold và references có thể chặn."
    },
    {
      "action": "request.delete",
      "resource": "xrn:{tenant}:request:{id}",
      "condition": "Đồng thời record.delete khi model primitive=Request; chỉ draft (hoặc returned khi update), không đổi route/quyết định."
    },
    {
      "action": "document.delete",
      "resource": "xrn:{tenant}:{model}:{id}",
      "condition": "Đồng thời record.delete khi model primitive=Document; chỉ bản nháp, không ký/ban hành."
    },
    {
      "action": "schedule.delete",
      "resource": "xrn:{tenant}:{model}:{id}",
      "condition": "Đồng thời record.delete khi model primitive=Schedule; chỉ lịch draft chưa được engine kích hoạt."
    },
    {
      "action": "comment.delete",
      "resource": "xrn:{tenant}:{model}:{id}",
      "condition": "Đồng thời record.delete khi model primitive=Comment; kiểm author/visibility; action xét thêm trên subject XRN, không chỉ Comment."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found",
    "revision_conflict",
    "precondition_failed",
    "approval_required",
    "stale_approval",
    "idempotency_conflict",
    "integrity_mismatch",
    "cleanup_incomplete"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "ref": "xrn:t_demo:work_item:rec_a",
      "expected_revision": 4,
      "reason": "Bản nháp trùng đã được kiểm tra",
      "idempotency_key": "delete-work-a",
      "approval_ref": "xrn:t_demo:request:approval_a"
    }
  ],
  "title": "Delete Record",
  "returns_untrusted_content": true
}
```

### 6.9. `submit_request`

Gửi hoặc gửi lại Request draft/returned để engine chốt snapshot và tuyến quyết định.

CAS Request rồi pin payload/subject revision, process revision, policy revision và approval route trong cùng transaction. Nếu draft có subject_ref, expected_subject_revision là bắt buộc ở runtime, CAS subject cùng transaction; không chốt âm thầm subject đã đổi. Không giữ Decision approve cũ mặc định sau khi sửa và submit lại. Nhánh process nội bộ thường L2; tool có thể gửi notification ngoài nên max L3, cần duyệt đúng effects thực tế.

```json
{
  "name": "submit_request",
  "description": "Gửi hoặc gửi lại Request draft/returned để engine chốt snapshot và tuyến quyết định.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:submit_request:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "request_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "expected_revision": {
        "type": "integer",
        "minimum": 1
      },
      "process_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "expected_process_revision": {
        "type": "integer",
        "minimum": 1
      },
      "idempotency_key": {
        "type": "string",
        "minLength": 1
      },
      "approval_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "expected_subject_revision": {
        "type": "integer",
        "minimum": 1,
        "description": "Bắt buộc ở runtime nếu draft Request có subject_ref; CAS subject trước khi chốt snapshot."
      }
    },
    "required": [
      "workspace_id",
      "request_ref",
      "expected_revision",
      "process_ref",
      "expected_process_revision",
      "idempotency_key"
    ],
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:submit_request:output",
    "type": "object",
    "properties": {
      "result": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/RequestResult"
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": true,
    "mutates_audit": true,
    "enqueues_work": true,
    "replaces_state": true,
    "may_discard_data": false,
    "changes_definitions": false,
    "affects_others": true,
    "accesses_external_network": true,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": true,
    "idempotentHint": true,
    "openWorldHint": true
  },
  "max_autonomy": "L3",
  "iam": [
    {
      "action": "request.submit",
      "resource": "xrn:{tenant}:request:{id}",
      "condition": "Chỉ requester/delegation hợp lệ; approval_ref bắt buộc nếu caller là agent theo policy L2."
    },
    {
      "action": "process.start",
      "resource": "xrn:{tenant}:process:{process_id}",
      "condition": "Định nghĩa active đã duyệt và loại Request phù hợp."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found",
    "revision_conflict",
    "rule_violation",
    "precondition_failed",
    "approval_required",
    "stale_approval",
    "idempotency_conflict",
    "integrity_mismatch",
    "cleanup_incomplete"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "request_ref": "xrn:t_demo:request:req_a",
      "expected_revision": 2,
      "process_ref": "xrn:t_demo:process:review_cycle",
      "expected_process_revision": 1,
      "idempotency_key": "submit-req-a"
    }
  ],
  "title": "Submit Request",
  "returns_untrusted_content": true
}
```

### 6.10. `decide_request`

Append quyết định approve/reject/return/delegate tại một bước, từ xác nhận có thẩm quyền của con người.

Human confirmation không phải boolean hay actor_id tự khai; server lấy người duyệt từ phiên xác thực và tra ref opaque. approve có thể enqueue activation/migration hoặc hệ quả ngoài: effects tính cả hậu quả deferred. Agent chỉ chuyển lời gọi có chứng cứ human; không thể ký/chi tiền thay người. CAS cả Request và step; append Decision một lần.

```json
{
  "name": "decide_request",
  "description": "Append quyết định approve/reject/return/delegate tại một bước, từ xác nhận có thẩm quyền của con người.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:decide_request:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "request_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "step_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "expected_revision": {
        "type": "integer",
        "minimum": 1
      },
      "expected_step_revision": {
        "type": "integer",
        "minimum": 1
      },
      "decision": {
        "type": "string",
        "enum": [
          "approve",
          "reject",
          "return",
          "delegate"
        ]
      },
      "reason": {
        "type": "string",
        "minLength": 1
      },
      "delegate_to": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "delegation_expires_at": {
        "type": "string",
        "format": "date-time"
      },
      "human_confirmation_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "idempotency_key": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "workspace_id",
      "request_ref",
      "step_id",
      "expected_revision",
      "expected_step_revision",
      "decision",
      "reason",
      "human_confirmation_ref",
      "idempotency_key"
    ],
    "additionalProperties": false,
    "allOf": [
      {
        "if": {
          "properties": {
            "decision": {
              "const": "delegate"
            }
          }
        },
        "then": {
          "required": [
            "delegate_to",
            "delegation_expires_at"
          ]
        },
        "else": {
          "not": {
            "anyOf": [
              {
                "required": [
                  "delegate_to"
                ]
              },
              {
                "required": [
                  "delegation_expires_at"
                ]
              }
            ]
          }
        }
      }
    ]
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:decide_request:output",
    "type": "object",
    "properties": {
      "result": {
        "type": "object",
        "properties": {
          "decision": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Resource"
          },
          "request": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Resource"
          },
          "step_revision": {
            "type": "integer",
            "minimum": 1
          },
          "activation_status": {
            "type": "string",
            "enum": [
              "not_applicable",
              "queued",
              "waiting",
              "applied",
              "failed"
            ]
          },
          "execution_ref": {
            "anyOf": [
              {
                "type": "string",
                "pattern": "^xrn:[a-z][a-z0-9_]*:process_instance:[a-z][a-z0-9_]*$"
              },
              {
                "type": "null"
              }
            ]
          },
          "receipt": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Receipt"
          }
        },
        "required": [
          "decision",
          "request",
          "step_revision",
          "activation_status",
          "execution_ref",
          "receipt"
        ],
        "additionalProperties": false
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": true,
    "mutates_audit": true,
    "enqueues_work": true,
    "replaces_state": true,
    "may_discard_data": true,
    "changes_definitions": true,
    "affects_others": true,
    "accesses_external_network": true,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": true,
    "idempotentHint": true,
    "openWorldHint": true
  },
  "max_autonomy": "L3",
  "iam": [
    {
      "action": "request.decide",
      "resource": "xrn:{tenant}:request:{id}",
      "condition": "Đồng thời cần request.approve/request.reject/request.return/request.delegate theo decision, trên cùng Request; đúng step membership và bằng chứng human."
    },
    {
      "action": "model.activate",
      "resource": "xrn:{tenant}:model:{model_id}",
      "condition": "Chỉ khi duyệt proposal model; worker kiểm tra quyền approver và plan đã bind."
    },
    {
      "action": "process.activate",
      "resource": "xrn:{tenant}:process:{process_id}",
      "condition": "Chỉ khi duyệt proposal process; không phải quyền agent tự áp định nghĩa."
    },
    {
      "action": "request.approve",
      "resource": "xrn:{tenant}:request:{id}",
      "condition": "Bắt buộc cùng request.decide khi decision=approve; kiểm membership/state ở đường thực thi mới, không chặn replay receipt hợp lệ."
    },
    {
      "action": "request.reject",
      "resource": "xrn:{tenant}:request:{id}",
      "condition": "Bắt buộc cùng request.decide khi decision=reject; kiểm membership/state ở đường thực thi mới, không chặn replay receipt hợp lệ."
    },
    {
      "action": "request.return",
      "resource": "xrn:{tenant}:request:{id}",
      "condition": "Bắt buộc cùng request.decide khi decision=return; kiểm membership/state ở đường thực thi mới, không chặn replay receipt hợp lệ."
    },
    {
      "action": "request.delegate",
      "resource": "xrn:{tenant}:request:{id}",
      "condition": "Bắt buộc cùng request.decide khi decision=delegate; kiểm membership/state ở đường thực thi mới, không chặn replay receipt hợp lệ."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found",
    "revision_conflict",
    "precondition_failed",
    "human_confirmation_required",
    "invalid_delegation",
    "stale_approval",
    "rule_violation",
    "idempotency_conflict",
    "integrity_mismatch",
    "cleanup_incomplete"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "request_ref": "xrn:t_demo:request:req_a",
      "step_id": "review_a",
      "expected_revision": 3,
      "expected_step_revision": 1,
      "decision": "return",
      "reason": "Cần bổ sung chứng từ",
      "human_confirmation_ref": "xrn:t_demo:confirmation:confirm_a",
      "idempotency_key": "return-req-a"
    }
  ],
  "title": "Decide Request",
  "returns_untrusted_content": true
}
```

### 6.11. `create_task`

Giao một việc có người chịu trách nhiệm, SLA và liên kết nguồn.

due_at=null để server tính bằng lịch làm việc versioned. Ghi Schedule, Event, Notification outbox cùng transaction. Nhắc ngoài có thể có nên max L3; gán việc cho mình và chỉ inbox nội bộ có thể được policy xếp L1. Không báo notification đã gửi chỉ vì enqueue thành công.

```json
{
  "name": "create_task",
  "description": "Giao một việc có người chịu trách nhiệm, SLA và liên kết nguồn.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:create_task:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "title": {
        "type": "string",
        "minLength": 1
      },
      "description": {
        "type": "string"
      },
      "assignee_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "watcher_refs": {
        "type": "array",
        "items": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "uniqueItems": true
      },
      "source_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "priority": {
        "type": "string",
        "enum": [
          "low",
          "normal",
          "high",
          "urgent"
        ]
      },
      "sla": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/SlaInput"
      },
      "idempotency_key": {
        "type": "string",
        "minLength": 1
      },
      "approval_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      }
    },
    "required": [
      "workspace_id",
      "title",
      "assignee_ref",
      "priority",
      "sla",
      "idempotency_key"
    ],
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:create_task:output",
    "type": "object",
    "properties": {
      "result": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/TaskResult"
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": true,
    "mutates_audit": true,
    "enqueues_work": true,
    "replaces_state": false,
    "may_discard_data": false,
    "changes_definitions": false,
    "affects_others": true,
    "accesses_external_network": true,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": true
  },
  "max_autonomy": "L3",
  "iam": [
    {
      "action": "task.create",
      "resource": "xrn:{tenant}:task:*",
      "condition": "Phải được giao cho assignee này; task.assign trên scope assignee nếu khác chính mình; agent L2/L3 cần approval_ref."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found",
    "rule_violation",
    "precondition_failed",
    "approval_required",
    "stale_approval",
    "revision_conflict",
    "idempotency_conflict",
    "integrity_mismatch",
    "cleanup_incomplete"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "title": "Rà soát tài liệu",
      "assignee_ref": "xrn:t_demo:actor:human_a",
      "priority": "normal",
      "sla": {
        "policy_ref": "xrn:t_demo:policy:sla_standard",
        "policy_revision": 1,
        "due_at": null,
        "timezone": "Asia/Ho_Chi_Minh"
      },
      "idempotency_key": "task-review-a"
    }
  ],
  "title": "Create Task",
  "returns_untrusted_content": true
}
```

### 6.12. `update_task`

Thực hiện một thay đổi trách nhiệm/trạng thái/SLA có kiểm soát trên Task.

Ba thao tác cùng mục đích quản lý lifecycle Task; chính xác một operation, IAM riêng từng nhánh. Không tự ghi elapsed/breached hoặc xóa dấu quá hạn bằng đổi due_at. done cần evidence nếu policy yêu cầu; worker xử lý pause/escalation đúng lịch.

```json
{
  "name": "update_task",
  "description": "Thực hiện một thay đổi trách nhiệm/trạng thái/SLA có kiểm soát trên Task.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:update_task:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "task_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "expected_revision": {
        "type": "integer",
        "minimum": 1
      },
      "operation": {
        "type": "string",
        "enum": [
          "assign",
          "transition",
          "adjust_sla"
        ]
      },
      "assignee_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "state": {
        "type": "string",
        "enum": [
          "todo",
          "in_progress",
          "blocked",
          "done",
          "cancelled"
        ]
      },
      "sla": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/SlaInput"
      },
      "evidence_refs": {
        "type": "array",
        "items": {
          "type": "string",
          "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
        },
        "uniqueItems": true
      },
      "reason": {
        "type": "string",
        "minLength": 1
      },
      "idempotency_key": {
        "type": "string",
        "minLength": 1
      },
      "approval_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      }
    },
    "required": [
      "workspace_id",
      "task_ref",
      "expected_revision",
      "operation",
      "reason",
      "idempotency_key"
    ],
    "additionalProperties": false,
    "allOf": [
      {
        "if": {
          "properties": {
            "operation": {
              "const": "assign"
            }
          }
        },
        "then": {
          "required": [
            "assignee_ref"
          ],
          "not": {
            "anyOf": [
              {
                "required": [
                  "state"
                ]
              },
              {
                "required": [
                  "sla"
                ]
              }
            ]
          }
        }
      },
      {
        "if": {
          "properties": {
            "operation": {
              "const": "transition"
            }
          }
        },
        "then": {
          "required": [
            "state"
          ],
          "not": {
            "anyOf": [
              {
                "required": [
                  "assignee_ref"
                ]
              },
              {
                "required": [
                  "sla"
                ]
              }
            ]
          }
        }
      },
      {
        "if": {
          "properties": {
            "operation": {
              "const": "adjust_sla"
            }
          }
        },
        "then": {
          "required": [
            "sla"
          ],
          "not": {
            "anyOf": [
              {
                "required": [
                  "assignee_ref"
                ]
              },
              {
                "required": [
                  "state"
                ]
              }
            ]
          }
        }
      }
    ]
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:update_task:output",
    "type": "object",
    "properties": {
      "result": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/TaskResult"
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": true,
    "mutates_audit": true,
    "enqueues_work": true,
    "replaces_state": true,
    "may_discard_data": false,
    "changes_definitions": false,
    "affects_others": true,
    "accesses_external_network": true,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": true,
    "idempotentHint": true,
    "openWorldHint": true
  },
  "max_autonomy": "L3",
  "iam": [
    {
      "action": "task.assign",
      "resource": "xrn:{tenant}:task:{id}",
      "condition": "operation=assign; đồng thời kiểm tra quyền giao cho người nhận."
    },
    {
      "action": "task.transition",
      "resource": "xrn:{tenant}:task:{id}",
      "condition": "operation=transition; chỉ trạng thái được state machine cho phép."
    },
    {
      "action": "task.sla_override",
      "resource": "xrn:{tenant}:task:{id}",
      "condition": "operation=adjust_sla; human approval bắt buộc, giữ SLA cũ trong audit."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found",
    "revision_conflict",
    "precondition_failed",
    "rule_violation",
    "approval_required",
    "stale_approval",
    "idempotency_conflict",
    "integrity_mismatch",
    "cleanup_incomplete"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "task_ref": "xrn:t_demo:task:task_a",
      "expected_revision": 2,
      "operation": "transition",
      "state": "done",
      "evidence_refs": [
        "xrn:t_demo:document:doc_a"
      ],
      "reason": "Đã rà soát và lưu kết quả",
      "idempotency_key": "complete-task-a"
    }
  ],
  "title": "Update Task",
  "returns_untrusted_content": true
}
```

### 6.13. `get_my_inbox`

Đọc các Request cần tôi quyết, Task cần tôi làm và Notification gửi cho tôi.

Không nhận assignee/actor bất kỳ; không mark-as-read khi đọc. Quyền xem inbox không cho phép đọc payload hay quyết định của mọi item; available_actions chỉ là gợi ý tại as_of.

```json
{
  "name": "get_my_inbox",
  "description": "Đọc các Request cần tôi quyết, Task cần tôi làm và Notification gửi cho tôi.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:get_my_inbox:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "kinds": {
        "type": "array",
        "items": {
          "type": "string",
          "enum": [
            "request",
            "task",
            "notification"
          ]
        },
        "minItems": 1,
        "uniqueItems": true
      },
      "states": {
        "type": "array",
        "items": {
          "type": "string",
          "minLength": 1
        },
        "uniqueItems": true
      },
      "due_before": {
        "type": "string",
        "format": "date-time"
      },
      "page": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/PageInput"
      }
    },
    "required": [
      "workspace_id"
    ],
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:get_my_inbox:output",
    "type": "object",
    "properties": {
      "result": {
        "type": "object",
        "properties": {
          "actor_ref": {
            "type": "string",
            "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
          },
          "items": {
            "type": "array",
            "items": {
              "$ref": "urn:xt:tool-contracts:draft:common#/$defs/InboxItem"
            }
          },
          "page": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Page"
          }
        },
        "required": [
          "actor_ref",
          "items",
          "page"
        ],
        "additionalProperties": false
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": false,
    "mutates_audit": false,
    "enqueues_work": false,
    "replaces_state": false,
    "may_discard_data": false,
    "changes_definitions": false,
    "affects_others": false,
    "accesses_external_network": false,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  },
  "max_autonomy": "L0",
  "iam": [
    {
      "action": "inbox.read",
      "resource": "xrn:{tenant}:actor:{effective_actor_id}",
      "condition": "Chỉ effective principal trong token; agent xem inbox human phải có delegation inbox.read giới hạn."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "snapshot_expired"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "kinds": [
        "request",
        "task"
      ],
      "page": {
        "limit": 20
      }
    }
  ],
  "title": "Get My Inbox",
  "returns_untrusted_content": true
}
```

### 6.14. `start_process`

Khởi chạy một instance từ định nghĩa đã duyệt, pin revision và subject.

Khai worst-case của process đã cho phép: có thể đổi record, thu hồi quyền/xóa theo policy hoặc gửi ngoài. Không nhận code, endpoint hay tên handler từ agent. Mỗi step thực thi kiểm tra lại grant/policy; không dùng quyền service account để nâng quyền. Instance queued không có nghĩa nghiệp vụ hoàn tất.

```json
{
  "name": "start_process",
  "description": "Khởi chạy một instance từ định nghĩa đã duyệt, pin revision và subject.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:start_process:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "process_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "expected_process_revision": {
        "type": "integer",
        "minimum": 1
      },
      "subject_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "expected_subject_revision": {
        "type": "integer",
        "minimum": 1
      },
      "parameters": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Values"
      },
      "idempotency_key": {
        "type": "string",
        "minLength": 1
      },
      "approval_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      }
    },
    "required": [
      "workspace_id",
      "process_ref",
      "expected_process_revision",
      "parameters",
      "idempotency_key"
    ],
    "additionalProperties": false,
    "dependentRequired": {
      "subject_ref": [
        "expected_subject_revision"
      ],
      "expected_subject_revision": [
        "subject_ref"
      ]
    }
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:start_process:output",
    "type": "object",
    "properties": {
      "result": {
        "type": "object",
        "properties": {
          "instance": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/ProcessInstance"
          },
          "checks": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Checks"
          },
          "receipt": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Receipt"
          }
        },
        "required": [
          "instance",
          "checks",
          "receipt"
        ],
        "additionalProperties": false
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": true,
    "mutates_audit": true,
    "enqueues_work": true,
    "replaces_state": true,
    "may_discard_data": true,
    "changes_definitions": false,
    "affects_others": true,
    "accesses_external_network": true,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": true,
    "idempotentHint": true,
    "openWorldHint": true
  },
  "max_autonomy": "L3",
  "iam": [
    {
      "action": "process.start",
      "resource": "xrn:{tenant}:process:{process_id}",
      "condition": "Đồng thời record.read/record.transition trên subject và quyền cho từng effect plan; agent L2/L3 cần approval_ref."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found",
    "revision_conflict",
    "precondition_failed",
    "rule_violation",
    "approval_required",
    "stale_approval",
    "idempotency_conflict",
    "integrity_mismatch",
    "cleanup_incomplete"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "process_ref": "xrn:t_demo:process:review_cycle",
      "expected_process_revision": 1,
      "subject_ref": "xrn:t_demo:work_item:rec_a",
      "expected_subject_revision": 3,
      "parameters": [],
      "idempotency_key": "start-review-a"
    }
  ],
  "title": "Start Process",
  "returns_untrusted_content": true
}
```

### 6.15. `get_process_status`

Đọc trạng thái thật của instance và trang các bước đang chờ/bị chặn.

steps phân trang; pending_external_effects là số đã biết trong scope, coverage nói nguồn chưa quan sát. Không suy completed khi còn outbox chưa xử lý theo completion policy.

```json
{
  "name": "get_process_status",
  "description": "Đọc trạng thái thật của instance và trang các bước đang chờ/bị chặn.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:get_process_status:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "instance_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "detail": {
        "type": "string",
        "enum": [
          "summary",
          "full"
        ]
      },
      "page": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/PageInput"
      }
    },
    "required": [
      "workspace_id",
      "instance_ref"
    ],
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:get_process_status:output",
    "type": "object",
    "properties": {
      "result": {
        "type": "object",
        "properties": {
          "instance": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/ProcessInstance"
          },
          "steps": {
            "type": "array",
            "items": {
              "$ref": "urn:xt:tool-contracts:draft:common#/$defs/PendingStep"
            }
          },
          "page": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Page"
          }
        },
        "required": [
          "instance",
          "steps",
          "page"
        ],
        "additionalProperties": false
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": false,
    "mutates_audit": false,
    "enqueues_work": false,
    "replaces_state": false,
    "may_discard_data": false,
    "changes_definitions": false,
    "affects_others": false,
    "accesses_external_network": false,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  },
  "max_autonomy": "L0",
  "iam": [
    {
      "action": "process_instance.read",
      "resource": "xrn:{tenant}:process_instance:{id}",
      "condition": "Không tự resume/retry/advance instance khi đọc status."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found",
    "snapshot_expired"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "instance_ref": "xrn:t_demo:process_instance:run_a",
      "detail": "summary",
      "page": {
        "limit": 20
      }
    }
  ],
  "title": "Get Process Status",
  "returns_untrusted_content": true
}
```

### 6.16. `propose_model_change`

Preview hoặc gửi đề xuất thay model; không sửa định nghĩa active trong lời gọi này.

dry_run=true tuyệt đối không ghi Proposal/Request/AuditLog nghiệp vụ/outbox/Counter; key chỉ dùng khi gửi false. dry_run=false bắt buộc preview_digest + preview_token còn hạn, chạy lại kiểm tra và lưu Request draft để human app gửi duyệt. Không có apply=true. expected_revision=0 chỉ dùng tạo model mới chưa tồn tại; reserved primitive bị khóa.

```json
{
  "name": "propose_model_change",
  "description": "Preview hoặc gửi đề xuất thay model; không sửa định nghĩa active trong lời gọi này.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:propose_model_change:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "expected_revision": {
        "type": "integer",
        "minimum": 0
      },
      "rationale": {
        "type": "string",
        "minLength": 1
      },
      "migration_plan": {
        "type": "string",
        "minLength": 1
      },
      "rollback_plan": {
        "type": "string",
        "minLength": 1
      },
      "dry_run": {
        "type": "boolean"
      },
      "preview_digest": {
        "type": "string",
        "pattern": "^sha256:[0-9a-f]{64}$"
      },
      "idempotency_key": {
        "type": "string",
        "minLength": 1
      },
      "model_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "operations": {
        "type": "array",
        "items": {
          "$ref": "urn:xt:tool-contracts:draft:common#/$defs/ModelOperation"
        },
        "minItems": 1
      },
      "preview_token": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "workspace_id",
      "model_id",
      "expected_revision",
      "operations",
      "rationale",
      "migration_plan",
      "rollback_plan",
      "dry_run",
      "idempotency_key"
    ],
    "additionalProperties": false,
    "allOf": [
      {
        "if": {
          "properties": {
            "dry_run": {
              "const": false
            }
          }
        },
        "then": {
          "required": [
            "preview_digest",
            "preview_token"
          ]
        },
        "else": {
          "not": {
            "anyOf": [
              {
                "required": [
                  "preview_digest"
                ]
              },
              {
                "required": [
                  "preview_token"
                ]
              }
            ]
          }
        }
      }
    ]
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:propose_model_change:output",
    "type": "object",
    "properties": {
      "result": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/ProposalResult"
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": true,
    "mutates_audit": true,
    "enqueues_work": false,
    "replaces_state": false,
    "may_discard_data": false,
    "changes_definitions": false,
    "affects_others": false,
    "accesses_external_network": false,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  },
  "max_autonomy": "L1",
  "iam": [
    {
      "action": "model.propose",
      "resource": "xrn:{tenant}:model:{model_id}",
      "condition": "Không bao hàm model.activate; dry_run=true cũng cần quyền đọc schema và tác động dữ liệu."
    },
    {
      "action": "request.create",
      "resource": "xrn:{tenant}:request:*",
      "condition": "Chỉ nhánh dry_run=false; tạo Request quản trị ở trạng thái draft. Human app gửi bằng submit_request trước khi quyết định; không tự tạo timer/outbox/notification."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found",
    "revision_conflict",
    "rule_violation",
    "precondition_failed",
    "preview_expired",
    "stale_approval",
    "configuration_invalid",
    "idempotency_conflict",
    "integrity_mismatch",
    "cleanup_incomplete"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "model_id": "work_item",
      "expected_revision": 1,
      "operations": [
        {
          "operation": "add_field",
          "name": "reference_code",
          "field_definition": {
            "name": "reference_code",
            "type": "string",
            "required": false,
            "nullable": true,
            "multiple": false,
            "enum_values": [],
            "relation_model": null,
            "read_only": false,
            "sensitive": false,
            "unique": false
          }
        }
      ],
      "rationale": "Theo dõi tham chiếu từ nguồn",
      "migration_plan": "Thêm trường nullable, giữ dữ liệu cũ",
      "rollback_plan": "Dừng dùng trường; chỉ bỏ sau khi duyệt migration",
      "dry_run": true,
      "idempotency_key": "preview-model-a"
    },
    {
      "workspace_id": "ws_demo",
      "model_id": "work_item",
      "expected_revision": 1,
      "operations": [
        {
          "operation": "add_field",
          "name": "reference_code",
          "field_definition": {
            "name": "reference_code",
            "type": "string",
            "required": false,
            "nullable": true,
            "multiple": false,
            "enum_values": [],
            "relation_model": null,
            "read_only": false,
            "sensitive": false,
            "unique": false
          }
        }
      ],
      "rationale": "Theo dõi tham chiếu từ nguồn",
      "migration_plan": "Thêm trường nullable, giữ dữ liệu cũ",
      "rollback_plan": "Dừng dùng trường; chỉ bỏ sau khi duyệt migration",
      "dry_run": false,
      "idempotency_key": "submit-model-a",
      "preview_digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "preview_token": "signed_preview_fixture"
    }
  ],
  "title": "Propose Model Change",
  "returns_untrusted_content": true
}
```

### 6.17. `propose_process_change`

Preview hoặc gửi đề xuất graph/process, rule và SLA; không kích hoạt định nghĩa.

Preview kiểm tra graph reachability, chu trình có điểm chờ/bounds được chứng minh, step handler OSI đã đăng ký, IAM/effects/SLA/SoD; không chạy script/webhook trong simulation. Thay đổi process không được làm yếu policy nền hoặc migration instance cũ ngầm.

```json
{
  "name": "propose_process_change",
  "description": "Preview hoặc gửi đề xuất graph/process, rule và SLA; không kích hoạt định nghĩa.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:propose_process_change:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "expected_revision": {
        "type": "integer",
        "minimum": 0
      },
      "rationale": {
        "type": "string",
        "minLength": 1
      },
      "migration_plan": {
        "type": "string",
        "minLength": 1
      },
      "rollback_plan": {
        "type": "string",
        "minLength": 1
      },
      "dry_run": {
        "type": "boolean"
      },
      "preview_digest": {
        "type": "string",
        "pattern": "^sha256:[0-9a-f]{64}$"
      },
      "idempotency_key": {
        "type": "string",
        "minLength": 1
      },
      "process_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "definition": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/ProcessChange"
      },
      "preview_token": {
        "type": "string",
        "minLength": 1
      }
    },
    "required": [
      "workspace_id",
      "process_id",
      "expected_revision",
      "definition",
      "rationale",
      "migration_plan",
      "rollback_plan",
      "dry_run",
      "idempotency_key"
    ],
    "additionalProperties": false,
    "allOf": [
      {
        "if": {
          "properties": {
            "dry_run": {
              "const": false
            }
          }
        },
        "then": {
          "required": [
            "preview_digest",
            "preview_token"
          ]
        },
        "else": {
          "not": {
            "anyOf": [
              {
                "required": [
                  "preview_digest"
                ]
              },
              {
                "required": [
                  "preview_token"
                ]
              }
            ]
          }
        }
      }
    ]
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:propose_process_change:output",
    "type": "object",
    "properties": {
      "result": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/ProposalResult"
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": true,
    "mutates_audit": true,
    "enqueues_work": false,
    "replaces_state": false,
    "may_discard_data": false,
    "changes_definitions": false,
    "affects_others": false,
    "accesses_external_network": false,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": false,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  },
  "max_autonomy": "L1",
  "iam": [
    {
      "action": "process.propose",
      "resource": "xrn:{tenant}:process:{process_id}",
      "condition": "Không bao hàm process.activate; các policy được tham chiếu phải được phép đọc."
    },
    {
      "action": "request.create",
      "resource": "xrn:{tenant}:request:*",
      "condition": "Chỉ nhánh dry_run=false; tạo Request quản trị ở trạng thái draft. Human app gửi bằng submit_request trước khi quyết định; không tự tạo timer/outbox/notification."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found",
    "revision_conflict",
    "rule_violation",
    "precondition_failed",
    "preview_expired",
    "stale_approval",
    "configuration_invalid",
    "idempotency_conflict",
    "integrity_mismatch",
    "cleanup_incomplete"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "process_id": "review_cycle",
      "expected_revision": 1,
      "definition": {
        "steps": [
          {
            "id": "review_a",
            "kind": "decision",
            "actor_policy_ref": "xrn:t_demo:policy:reviewers",
            "sla_policy_ref": "xrn:t_demo:policy:sla_standard",
            "effect_names": [],
            "human_required": true,
            "config": {
              "type": "decision",
              "request_model_id": "request",
              "decision_policy_ref": "xrn:t_demo:policy:reviewers",
              "quorum": 1
            }
          }
        ],
        "edges": [],
        "input_fields": [],
        "policy_refs": [
          "xrn:t_demo:policy:reviewers"
        ],
        "entry_step": "review_a"
      },
      "rationale": "Chuẩn hóa bước rà soát",
      "migration_plan": "Instance cũ giữ version cũ; chỉ áp cho instance mới",
      "rollback_plan": "Ngừng start phiên bản mới, dùng lại bản cũ đã duyệt",
      "dry_run": true,
      "idempotency_key": "preview-process-a"
    },
    {
      "workspace_id": "ws_demo",
      "process_id": "review_cycle",
      "expected_revision": 1,
      "definition": {
        "steps": [
          {
            "id": "review_a",
            "kind": "decision",
            "actor_policy_ref": "xrn:t_demo:policy:reviewers",
            "sla_policy_ref": "xrn:t_demo:policy:sla_standard",
            "effect_names": [],
            "human_required": true,
            "config": {
              "type": "decision",
              "request_model_id": "request",
              "decision_policy_ref": "xrn:t_demo:policy:reviewers",
              "quorum": 1
            }
          }
        ],
        "edges": [],
        "input_fields": [],
        "policy_refs": [
          "xrn:t_demo:policy:reviewers"
        ],
        "entry_step": "review_a"
      },
      "rationale": "Chuẩn hóa bước rà soát",
      "migration_plan": "Instance cũ giữ version cũ; chỉ áp cho instance mới",
      "rollback_plan": "Ngừng start phiên bản mới, dùng lại bản cũ đã duyệt",
      "dry_run": false,
      "idempotency_key": "submit-process-a",
      "preview_digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "preview_token": "signed_preview_fixture"
    }
  ],
  "title": "Propose Process Change",
  "returns_untrusted_content": true
}
```

### 6.18. `inspect_history`

Đọc timeline lịch sử, audit được phép và giải thích quyết định/rule theo evidence.

Facets là các mặt của cùng dossier read-only; quyền độc lập. Nếu một facet bị chặn, trả status=denied và coverage partial, không giả entries rỗng; nếu tất cả đều bị chặn thì access_denied. explain là kết quả rule/evidence versioned, không bịa chain-of-thought của LLM.

```json
{
  "name": "inspect_history",
  "description": "Đọc timeline lịch sử, audit được phép và giải thích quyết định/rule theo evidence.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:inspect_history:input",
    "type": "object",
    "properties": {
      "workspace_id": {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]*$"
      },
      "subject_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "facets": {
        "type": "array",
        "items": {
          "type": "string",
          "enum": [
            "history",
            "audit",
            "explain"
          ]
        },
        "minItems": 1,
        "uniqueItems": true
      },
      "event_ref": {
        "type": "string",
        "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
      },
      "detail": {
        "type": "string",
        "enum": [
          "summary",
          "full"
        ]
      },
      "page": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/PageInput"
      }
    },
    "required": [
      "workspace_id",
      "subject_ref",
      "facets"
    ],
    "additionalProperties": false
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:xt:tool-contracts:draft:inspect_history:output",
    "type": "object",
    "properties": {
      "result": {
        "type": "object",
        "properties": {
          "subject_ref": {
            "type": "string",
            "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
          },
          "facet_status": {
            "type": "array",
            "items": {
              "$ref": "urn:xt:tool-contracts:draft:common#/$defs/FacetStatus"
            }
          },
          "entries": {
            "type": "array",
            "items": {
              "$ref": "urn:xt:tool-contracts:draft:common#/$defs/HistoryEntry"
            }
          },
          "explanation": {
            "anyOf": [
              {
                "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Explanation"
              },
              {
                "type": "null"
              }
            ]
          },
          "page": {
            "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Page"
          }
        },
        "required": [
          "subject_ref",
          "facet_status",
          "entries",
          "explanation",
          "page"
        ],
        "additionalProperties": false
      },
      "meta": {
        "$ref": "urn:xt:tool-contracts:draft:common#/$defs/Meta"
      }
    },
    "required": [
      "result",
      "meta"
    ],
    "additionalProperties": false
  },
  "effects": {
    "mutates_records": false,
    "mutates_audit": false,
    "enqueues_work": false,
    "replaces_state": false,
    "may_discard_data": false,
    "changes_definitions": false,
    "affects_others": false,
    "accesses_external_network": false,
    "executes_foreign_code": false,
    "idempotent": true
  },
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  },
  "max_autonomy": "L0",
  "iam": [
    {
      "action": "history.read",
      "resource": "xrn:{tenant}:{model}:{id}",
      "condition": "facet=history; xét trên subject."
    },
    {
      "action": "audit.read",
      "resource": "xrn:{tenant}:{model}:{id}",
      "condition": "facet=audit; không suy từ record.read."
    },
    {
      "action": "explain.read",
      "resource": "xrn:{tenant}:{model}:{id}",
      "condition": "facet=explain; facts của rule vẫn lọc field/PII."
    }
  ],
  "errors": [
    "invalid_request",
    "unauthenticated",
    "access_denied",
    "tenant_mismatch",
    "capability_unavailable",
    "rate_limited",
    "resource_constraint",
    "cancelled",
    "timeout",
    "execution_failure",
    "not_found",
    "snapshot_expired"
  ],
  "examples": [
    {
      "workspace_id": "ws_demo",
      "subject_ref": "xrn:t_demo:request:req_a",
      "facets": [
        "history",
        "explain"
      ],
      "detail": "summary",
      "page": {
        "limit": 20
      }
    }
  ],
  "title": "Inspect History",
  "returns_untrusted_content": true
}
```

## 7. Contract lỗi chung và mã lỗi

Giữ mô hình REA `{error:{code,category,message,retryable,remediation:{action},details}}`; vocabulary code/category là của xT, không sao chép các mã phân tích binary không liên quan. `details` luôn có, có thể là `{}`, chỉ chứa trường typed bên dưới đã lọc quyền. `category` được ràng buộc theo `code`, không để handler tự chọn tùy ý. `retryable` có nghĩa **gửi lại nguyên semantic input sau điều kiện phục hồi có thể có ích**, không nghĩa được tự vượt quyền hay tạo key mới.


```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:xt:tool-contracts:draft:error",
  "type": "object",
  "properties": {
    "error": {
      "type": "object",
      "properties": {
        "code": {
          "enum": [
            "invalid_request",
            "unauthenticated",
            "access_denied",
            "tenant_mismatch",
            "not_found",
            "revision_conflict",
            "idempotency_conflict",
            "rule_violation",
            "precondition_failed",
            "approval_required",
            "human_confirmation_required",
            "stale_approval",
            "invalid_delegation",
            "capability_unavailable",
            "snapshot_expired",
            "preview_expired",
            "rate_limited",
            "resource_constraint",
            "cancelled",
            "timeout",
            "integrity_mismatch",
            "execution_failure",
            "cleanup_incomplete",
            "configuration_invalid"
          ]
        },
        "category": {
          "enum": [
            "authentication",
            "authorization",
            "cancelled",
            "conflict",
            "execution_failure",
            "integrity_mismatch",
            "invalid_input",
            "not_found",
            "precondition",
            "resource_constraint",
            "timeout",
            "unavailable"
          ]
        },
        "message": {
          "type": "string",
          "minLength": 1
        },
        "retryable": {
          "type": "boolean"
        },
        "remediation": {
          "type": "object",
          "properties": {
            "action": {
              "type": "string",
              "minLength": 1
            }
          },
          "required": [
            "action"
          ],
          "additionalProperties": false
        },
        "details": {
          "type": "object",
          "properties": {
            "trace_id": {
              "type": "string"
            },
            "fields": {
              "type": "array",
              "items": {
                "type": "string"
              }
            },
            "rule_ids": {
              "type": "array",
              "items": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_]*$"
              }
            },
            "expected_revision": {
              "type": "integer",
              "minimum": 0
            },
            "actual_revision": {
              "type": "integer",
              "minimum": 0
            },
            "approval_ref": {
              "type": "string",
              "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
            },
            "preview_digest": {
              "type": "string"
            },
            "operation_ref": {
              "type": "string",
              "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
            },
            "commit_state": {
              "type": "string",
              "enum": [
                "not_committed",
                "committed",
                "unknown"
              ]
            },
            "retry_after_ms": {
              "type": "integer",
              "minimum": 0
            },
            "resource": {
              "type": "string",
              "enum": [
                "capacity",
                "transport",
                "storage",
                "compute"
              ]
            },
            "reported_limits": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "name": {
                    "type": "string"
                  },
                  "value": {
                    "type": "integer",
                    "minimum": 0
                  },
                  "unit": {
                    "type": "string"
                  }
                },
                "required": [
                  "name",
                  "value",
                  "unit"
                ],
                "additionalProperties": false
              }
            },
            "continuation": {
              "oneOf": [
                {
                  "type": "object",
                  "properties": {
                    "tool": {
                      "const": "inspect_workspace"
                    },
                    "arguments": {
                      "$ref": "urn:xt:tool-contracts:draft:inspect_workspace:input"
                    }
                  },
                  "required": [
                    "tool",
                    "arguments"
                  ],
                  "additionalProperties": false
                },
                {
                  "type": "object",
                  "properties": {
                    "tool": {
                      "const": "search_records"
                    },
                    "arguments": {
                      "$ref": "urn:xt:tool-contracts:draft:search_records:input"
                    }
                  },
                  "required": [
                    "tool",
                    "arguments"
                  ],
                  "additionalProperties": false
                },
                {
                  "type": "object",
                  "properties": {
                    "tool": {
                      "const": "get_my_inbox"
                    },
                    "arguments": {
                      "$ref": "urn:xt:tool-contracts:draft:get_my_inbox:input"
                    }
                  },
                  "required": [
                    "tool",
                    "arguments"
                  ],
                  "additionalProperties": false
                },
                {
                  "type": "object",
                  "properties": {
                    "tool": {
                      "const": "get_process_status"
                    },
                    "arguments": {
                      "$ref": "urn:xt:tool-contracts:draft:get_process_status:input"
                    }
                  },
                  "required": [
                    "tool",
                    "arguments"
                  ],
                  "additionalProperties": false
                },
                {
                  "type": "object",
                  "properties": {
                    "tool": {
                      "const": "inspect_history"
                    },
                    "arguments": {
                      "$ref": "urn:xt:tool-contracts:draft:inspect_history:input"
                    }
                  },
                  "required": [
                    "tool",
                    "arguments"
                  ],
                  "additionalProperties": false
                },
                {
                  "type": "object",
                  "properties": {
                    "tool": {
                      "const": "get_record"
                    },
                    "arguments": {
                      "$ref": "urn:xt:tool-contracts:draft:get_record:input"
                    }
                  },
                  "required": [
                    "tool",
                    "arguments"
                  ],
                  "additionalProperties": false
                }
              ]
            },
            "partial_observation": {
              "type": "string",
              "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
            },
            "residual_resources": {
              "type": "array",
              "items": {
                "type": "string",
                "pattern": "^xrn:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$"
              }
            }
          },
          "required": [],
          "additionalProperties": false
        }
      },
      "required": [
        "code",
        "category",
        "message",
        "retryable",
        "remediation",
        "details"
      ],
      "additionalProperties": false,
      "allOf": [
        {
          "if": {
            "properties": {
              "code": {
                "const": "invalid_request"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "invalid_input"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "unauthenticated"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "authentication"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "access_denied"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "authorization"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "tenant_mismatch"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "authorization"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "not_found"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "not_found"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "revision_conflict"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "conflict"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "idempotency_conflict"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "conflict"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "rule_violation"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "precondition"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "precondition_failed"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "precondition"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "approval_required"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "authorization"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "human_confirmation_required"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "authorization"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "stale_approval"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "conflict"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "invalid_delegation"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "authorization"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "capability_unavailable"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "unavailable"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "snapshot_expired"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "conflict"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "preview_expired"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "conflict"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "rate_limited"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "resource_constraint"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "resource_constraint"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "resource_constraint"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "cancelled"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "cancelled"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "timeout"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "timeout"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "integrity_mismatch"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "integrity_mismatch"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "execution_failure"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "execution_failure"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "cleanup_incomplete"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "execution_failure"
              }
            }
          }
        },
        {
          "if": {
            "properties": {
              "code": {
                "const": "configuration_invalid"
              }
            }
          },
          "then": {
            "properties": {
              "category": {
                "const": "invalid_input"
              }
            }
          }
        }
      ]
    }
  },
  "required": [
    "error"
  ],
  "additionalProperties": false
}
```

| Code | Category | Retryable mặc định | Remediation bắt buộc theo ngữ cảnh |
|---|---|---|---|

| `invalid_request` | `invalid_input` | `false` | Sửa trường theo input schema; không gửi lại nguyên input. |

| `unauthenticated` | `authentication` | `false` | Đăng nhập lại qua client; không gửi token trong tool arguments. |

| `access_denied` | `authorization` | `false` | Yêu cầu người quản trị cấp đúng action/resource; không thử lách bằng tool khác. |

| `tenant_mismatch` | `authorization` | `false` | Chọn phiên tenant hợp lệ rồi khám phá lại. |

| `not_found` | `not_found` | `false` | Tra cứu lại đối tượng trong phạm vi được cấp quyền. |

| `revision_conflict` | `conflict` | `false` | Đọc revision mới, so sánh và quyết định lại; dùng key mới khi ý định đã đổi. |

| `idempotency_conflict` | `conflict` | `false` | Không dùng lại key cho nội dung khác; tra receipt cũ hoặc tạo key cho ý định mới. |

| `rule_violation` | `precondition` | `false` | Bổ sung/sửa dữ liệu theo rule_id và fields được phép thấy. |

| `precondition_failed` | `precondition` | `false` | Đọc trạng thái và policy hiện hành; không ép chuyển trạng thái. |

| `approval_required` | `authorization` | `false` | Đề nghị người có thẩm quyền duyệt plan đã bind; retry cùng key và approval_ref hợp lệ. |

| `human_confirmation_required` | `authorization` | `false` | Mở màn hình quyết định Tầng 2 để con người xác nhận nội dung cụ thể. |

| `stale_approval` | `conflict` | `false` | Preview và xin duyệt lại vì dữ liệu/định nghĩa/policy đã đổi hoặc hết hạn. |

| `invalid_delegation` | `authorization` | `false` | Chọn người đủ quyền, cùng tenant, không tạo vòng và còn trong thời hạn. |

| `capability_unavailable` | `unavailable` | `true` | Đọc availability/remediation; thử lại sau khi capability được khôi phục. |

| `snapshot_expired` | `conflict` | `false` | Bắt đầu trang đầu bằng snapshot mới; nêu rõ dữ liệu có thể đã thay đổi. |

| `preview_expired` | `conflict` | `false` | Chạy lại dry-run và rà diff mới trước khi gửi proposal. |

| `rate_limited` | `resource_constraint` | `true` | Chờ retry_after_ms rồi gửi lại với cùng idempotency_key nếu có. |

| `resource_constraint` | `resource_constraint` | `false` | Dùng continuation hoặc giảm page/fields theo reported limits; không chia mutation tùy tiện. |

| `cancelled` | `cancelled` | `false` | Đọc commit_state/operation_ref trước khi quyết định gọi lại. |

| `timeout` | `timeout` | `true` | Đọc commit_state; replay cùng key nếu kết quả chưa biết, không tạo ý định mới. |

| `integrity_mismatch` | `integrity_mismatch` | `false` | Dừng áp dụng; kiểm chứng hash/chữ ký/snapshot từ nguồn được cấp quyền. |

| `execution_failure` | `execution_failure` | `false` | Tra trace_id qua kênh vận hành được cấp quyền và đọc remediation. |

| `cleanup_incomplete` | `execution_failure` | `false` | Tra operation_ref và residual_resources; cần khôi phục có kiểm soát. |

| `configuration_invalid` | `invalid_input` | `false` | Yêu cầu quản trị sửa cấu hình rồi preview lại. |


`capability_unavailable.retryable` chỉ true khi biết capability tạm lỗi; chưa cài/không hỗ trợ là false. `timeout.retryable=true` với mutation chỉ vì replay cùng key có dedupe, không cho chạy ý định mới. `resource_constraint.retryable=true` chỉ khi resource=capacity tạm thời và có hướng chờ rõ; transport/format không tự khắc phục bằng retry. `cleanup_incomplete` ưu tiên báo tài nguyên còn lại, chỉ vận hành được phép mới khôi phục; không giấu lỗi bằng fallback. `revision_conflict` không retry nguyên input vì CAS sẽ lại thất bại. Mặc định retry trong bảng là chính sách projection, schema bool cho phép biến thể được mô tả này; contract tests phải kiểm tra mapper.

Ví dụ lỗi từ `update_record`, chưa commit:

```json
{
  "error": {
    "code": "revision_conflict",
    "category": "conflict",
    "message": "Bản ghi đã đổi sau lần đọc của bạn.",
    "retryable": false,
    "remediation": {
      "action": "Gọi get_record, so sánh thay đổi rồi gửi patch phù hợp với revision mới và key mới."
    },
    "details": {
      "expected_revision": 3,
      "actual_revision": 4,
      "commit_state": "not_committed",
      "trace_id": "trace_demo"
    }
  }
}
```

## 8. Primitive dữ liệu và trường cốt lõi

Mọi primitive có `id`, `tenant_id`, `workspace_id`, `created_at`, `created_by` và XRN. Actor hệ thống vẫn thuộc tenant; actor agent có subject riêng, không dùng chung subject con người. Mutable primitive có `revision>=1`, `updated_at`, `updated_by`; immutable primitive chỉ revision=1 và sửa bằng sự kiện/bản ghi mới. Quan hệ đều XRN được kiểm quyền/tenant; chuỗi nhãn hiển thị không phải identity. Trường business động là dữ liệu model versioned, khác với các core fields được bảo vệ dưới đây.

| Primitive | Trường core ngoài envelope chung | Bất biến/guardrail |
|---|---|---|
| **Actor** (người + agent) | `kind:human|agent`, `subject_id`, `display_name`, `status`, `org_unit_refs[]`, `role_refs[]`, `delegation_refs[]`, `owner_human_ref?` cho agent | Role/grant/delegation do IAM quản trị, CRUD chung không sửa. Audit luôn phân biệt agent, effective human và delegation. |
| **Record** | `model_id`, `model_revision`, `state`, `owner_ref`, `values:FieldValue[]`, `relation_refs[]`, `classification`, `deleted_at?` | Khách hàng, tài sản, hồ sơ, giao dịch là cấu hình Record; state có engine quản lý không patch trực tiếp. |
| **Request** | `type_model_ref`, `requester_ref`, `subject_ref?`, `payload_snapshot`, `payload_revision`, `subject_revision?`, `process_instance_ref`, `process_revision`, `policy_revisions[]`, `route_snapshot`, `current_step_ids[]`, `status`, `submitted_at?` | Draft → submitted/in_review → approved/rejected/returned; route/quorum versioned. Sửa payload chỉ draft/returned; resubmit tạo snapshot mới. |
| **Decision** | `request_ref`, `step_id`, `step_revision`, `human_actor_ref`, `invoking_actor_ref`, `on_behalf_of?`, `action:approve|reject|return|delegate`, `reason`, `delegate_to?`, `delegation_expires_at?`, `confirmation_ref`, `request_revision`, `policy_revisions[]`, `decided_at`, `evidence_refs[]` | Append-only; không sửa/xóa bằng CRUD; có CAS bước, cấm tự duyệt; approve không đồng nghĩa chữ ký pháp lý. |
| **Task** | `title`, `description`, `assignee_ref`, `watcher_refs[]`, `source_ref?`, `status`, `priority`, `sla_policy_ref`, `sla_policy_revision`, `calendar_ref`, `calendar_revision`, `timezone`, `sla_started_at`, `due_at`, `paused_seconds`, `pause_intervals[]`, `next_escalation_at`, `completion_evidence_refs[]`, `completed_at?` | Một người chịu trách nhiệm chính; deadline tính server; pause/override/escalation có lịch sử, không xóa breach cũ. |
| **Document** | `title`, `document_type`, `blob_ref`, `version`, `content_hash`, `state:draft|review|signed|issued|superseded`, `number?`, `related_refs[]`, `signature_refs[]`, `signed_at?`, `supersedes_ref?` | Blob/File là resource object storage có ACL/hash/quét mã độc, không phải Document. Bản đã ký/ban hành bất biến; đổi bằng version mới. |
| **Schedule** | `kind:once|recurring|sla`, `run_at?`, `rrule?`, `timezone`, `target_ref`, `policy_revision`, `next_run_at?`, `status`, `dedupe_key` | Đúng một run_at/rrule theo kind; lịch SLA do engine quản lý; không nhận script tùy ý. |
| **Event** | `event_type`, `subject_ref`, `occurred_at`, `actor_ref`, `correlation_id`, `causation_id?`, `schema_revision`, `payload:FieldValue[]`, `dedupe_key` | Domain event append-only; không đồng nhất với audit và notification; outbox transactional, consumer idempotent. |
| **Notification** | `recipient_ref`, `subject_ref`, `channel`, `template_ref`, `status:queued|sent|delivered|read|failed`, `dedupe_key`, `queued_at`, `sent_at?`, `delivered_at?`, `read_at?`, `failure_code?` | Chỉ đánh dấu delivered/read khi có tín hiệu thật; queued không chứng minh đã gửi; recipient/payload qua policy. |
| **Policy** | `kind:iam|validation|routing|sla|autonomy|retention`, `version`, `status`, `effective_at`, `scope_refs[]`, `expression`, `expression_language_version`, `enforcement`, `message`, `approver_policy_ref` | DSL khai báo có kiểu và bounded, không eval Python/SQL/code của agent. Thay policy hiệu lực cần quy trình quản trị L3; model/process proposal không sửa policy nền. |
| **Counter** | `scope`, `period`, `prefix`, `next_value`, `revision`, `allocation_policy`, `allocation_refs[]` | Allocate atomic/dedupe; đảm bảo không trùng. “Không nhảy số” cần policy void/reconciliation riêng, không hứa vô điều kiện khi rollback/phân tán; preview không tiêu số. |
| **Comment** | `subject_ref`, `thread_id`, `parent_ref?`, `author_ref`, `body`, `visibility`, `edited_at?`, `supersedes_ref?` | Chứa dữ liệu không tin cậy; author từ token; chỉnh sửa có revision/audit, không dùng comment làm bằng chứng human approval. |
| **AuditLog** | `at`, `actor_ref`, `effective_actor_ref`, `on_behalf_of?`, `action`, `resource_ref`, `outcome`, `before_hash?`, `after_hash?`, `policy_revisions[]`, `trace_id`, `operation_id`, `prev_hash?`, `entry_hash`, `reason?` | Append-only, retention riêng; hash chain cần neo/kiểm chứng để phát hiện sửa, không tự gọi “không thể bị can thiệp”. before/after nhạy cảm có ACL riêng. |
| **Ledger** | `book_id`, `transaction_id`, `line_id`, `account_ref`, `subject_ref`, `unit`, `currency?`, `debit`, `credit`, `quantity?`, `occurred_at`, `posted_at`, `reversal_of?`, `entry_hash` | Append-only, cân đối theo book/đơn vị; decimal string hoặc minor-unit integer; sửa bằng reversal. Post nguyên tử bởi action engine đã duyệt, không record.update/delete. |

`ModelDefinition`, `ProcessDefinition`, `ProcessInstance`, `ApprovalStep`, `Proposal`, `Confirmation`, `File/BlobReference`, `Calendar`, `Snapshot/Metric` là kiểu hỗ trợ cần lưu/định danh, **không thêm tool riêng**. Model/process definition là metadata kiểm soát version; chúng không được tạo/sửa bằng Record CRUD. Snapshot báo cáo chốt là bất biến, công thức KPI là policy/model được duyệt. Các primitive Actor/Policy/Counter/Event/Notification/AuditLog/Ledger chỉ đọc qua tool chung với quyền riêng; ghi do dịch vụ IAM/engine có audit theo hành động đã duyệt, không mở cổng CRUD generic.

SLA engine dùng server clock, timezone IANA và calendar revision; policy định nghĩa thời gian lịch/giờ làm việc, pause reason, escalation target và điều kiện stop. Scheduler claim timer bền vững, ghi Event/outbox dedupe; outage phải báo overdue/late processing thật, không lùi thời gian để làm đẹp SLA.

## 9. MCP prompts và skills đi kèm

Prompts chỉ sinh hướng dẫn/context, không tự gọi tool, không cấp quyền, không là nguồn logic nghiệp vụ. Argument là dữ liệu không tin cậy; completion chỉ từ phiên theo IAM và không rò tên model/record tenant khác. Bước gợi ý là tùy chọn, không ép orchestration khi một tool đã đủ. PromptContract kiểm tra mọi tên tool tham chiếu có trong catalog; `completion/complete` phục vụ argument prompt, không tuyên bố autocomplete mọi argument tool.

| MCP prompt | Mục đích và tool tham chiếu chính |
|---|---|
| `triage_my_work` | Ưu tiên việc/đề xuất của tôi theo SLA; `get_my_inbox`, `get_process_status`. |
| `review_pending_requests` | Đọc payload và lịch sử, chỉ ra thiếu chứng từ, soạn khuyến nghị để người quyết; `get_record`, `inspect_history`, không tự approve. |
| `prepare_request` | Thu đủ field theo model, tạo draft và đề nghị gửi; `describe_model`, `create_record`, `submit_request`. |
| `plan_task_with_sla` | Giao việc rõ trách nhiệm/đầu ra/SLA, nêu gate; `create_task`, `update_task`. |
| `start_lifecycle_process` | Khám phá template, kiểm subject và chạy quy trình đã duyệt; `inspect_workspace`, `describe_process`, `start_process`. |
| `design_data_model` | Chuyển nhu cầu thành model generic, tìm khả năng tái dùng trước; `describe_model`, `propose_model_change`. |
| `change_model_safely` | Preview diff/migration/rollback, nêu ảnh hưởng và xin duyệt; `propose_model_change`, `inspect_history`. |
| `change_process_safely` | Rà graph, quyền, SLA và human gates trước đề xuất; `describe_process`, `propose_process_change`. |
| `explain_record_history` | Timeline có evidence, tách fact/inference/unknown; `inspect_history`, `get_record`. |
| `weekly_business_report` | Tổng hợp trên snapshot theo quyền, trích ref/revision/coverage cho từng số; `search_records`, `get_my_inbox`. |
| `diagnose_session` | Giải thích thiếu quyền/capability/client requirement bằng dữ liệu phiên; `inspect_workspace`. |

| Skill | Mục đích |
|---|---|
| `xt-core` | Router theo ý định, catalog/version, XRN, untrusted data, L0–L3 và trường hợp cần hỏi lại đối tượng. |
| `xt-records` | Query có kiểu, summary/full, snapshot pagination, CAS và phục hồi revision/idempotency; không thêm bulk tool ngầm. |
| `xt-approvals` | Draft/submit/Decision/delegation, cấm tự duyệt, xác nhận human và xử lý approval_required. |
| `xt-tasks` | Giao việc, transition, pause/escalation và giải thích SLA từ clock/calendar thật. |
| `xt-data-modeling` | Tái dùng model, preview change, migration/rollback, submit proposal và theo dõi activation. |
| `xt-process-design` | Thiết kế process bằng primitive và registry action, human gate, effects, pinned versions. |
| `xt-onboarding` | Hướng dẫn compose process vòng đời nhân sự/đối tác/khách hàng; template là dữ liệu tenant đã duyệt. |
| `xt-reporting` | Báo cáo có lineage/ref/revision, coverage và unknown; không tự chế số hoặc công thức tiền. |
| `xt-admin-diagnose` | Phân biệt authentication, tenant, IAM, capability unavailable và lỗi client; remediation không xin secret trong chat. |

Mỗi skill có `SKILL.md` ngắn và `references/` chỉ mở khi cần; có “dùng khi/bỏ qua khi”. `tool_count=18`, version và catalog_digest sinh từ ToolContract, không hard-code độc lập trong bản phát hành. Skill không đăng ký thêm tool và không thay threshold/schema/state machine bằng văn bản prompt. Các tên ở đây là danh sách đề xuất; tác vụ này chỉ tạo bản spec, chưa tạo/cài các skill hoặc prompts thật.

## 10. Độ phủ và điều kiện nghiệm thu contract

| Nhu cầu | Primitive/tool phục vụ |
|---|---|
| Khám phá workspace/model/process | inspect_workspace, describe_model, describe_process |
| CRUD + rule + CAS | search_records, get_record, create_record, update_record, delete_record |
| Request + Decision | create_record(Request draft), submit_request, decide_request, get_my_inbox |
| Task + SLA | create_task, update_task, get_my_inbox, inspect_history |
| Start/status process | start_process, get_process_status |
| Agent đề xuất, người duyệt thay đổi | propose_model_change, propose_process_change, decide_request; worker activate có quyền/gate |
| History/audit/explain | inspect_history, get_record cho evidence/ref theo quyền |
| W1/W4/W7 | Request/Decision + Task + Document; W4 thêm Ledger do action đã duyệt post |
| W2/W8 | Task/Schedule/Notification, search theo snapshot, Document báo cáo và lineage |
| W3/W5/W6 | Record lifecycle + process/task/decision; ký/cấp quyền/chi tiền là action có human gate, không mặc định đã tích hợp |

Kiểm thử triển khai sau này phải chứng minh: đủ 18 tool/không sinh thêm theo model; mọi schema advertised sau SDK conversion hợp Draft 2020-12; mọi example pass schema; unknown fields/duplicate field/discriminator sai bị chặn; effects sinh đúng annotations; mọi prompt tham chiếu tool thật; không lách quyền qua CRUD/read/completion/resources; RLS tenant/field; CAS đồng thời; replay trước CAS; key khác payload; human evidence/action/resource/digest/expiry; SoD/delegation vòng; proposal dry-run không ghi; stale preview/activation bị chặn; timer/outbox dedupe; coverage/total không rò quyền; oversize/cancel/timeout nêu commit thật. Chạy MCP boundary/client thật và agent eval trực tiếp/rộng/âm; schema pass không chứng minh routing hay sản phẩm đã chạy.

## 11. Câu hỏi mở cần debate

1. **Giữ đúng 18 tool hay tách authority hơn nữa?** `inspect_workspace` inventory + availability, `inspect_history` nhiều facet, `update_task` ba operation có dễ chọn đúng bằng agent thật không? Bản nháp giữ cùng outcome, kiểm quyền riêng; nếu eval cho thấy lẫn thì tăng/tái phân bổ tool bằng phiên bản contract, không thêm `execute_anything`.
2. **Proposal preview-only hay luôn persist?** Đã debate: một hướng luôn lưu Proposal kèm simulation để giữ intent đơn; bản nháp chọn dry_run=true không ghi và false lưu proposal + Request draft để có preview thuần. Cần kiểm chứng client hiểu annotations tĩnh readOnly=false dù dry-run an toàn; có nên tách preview ra resources hoàn toàn?
3. **Approve và activate có nên tách tool?** Hiện human Decision chỉ enqueue worker có revalidation. Điều này khiến decide_request mang effects L3 lớn; có cần tool activate riêng và chuyển chỗ trong ngân sách 18? Mọi phương án phải khai cả hậu quả deferred và tránh “approve xong nhưng chưa áp” bị hiểu nhầm.
4. **Plan/confirmation qua resource có đủ rõ?** Chọn schema/version của action-authorization Request, resource URI/digest TTL, cách app lấy preview không ghi và tính tương thích client không có MCP Apps. Không được thay bằng checkbox LLM hoặc tự tin vào chuỗi approval_ref.
5. **Log đọc nhạy cảm nằm đâu?** Hiện L0 chỉ technical access log. Nếu bắt buộc audit đọc vào chính AuditLog nghiệp vụ có thể thấy qua tool, effects/annotations của các read tool phải đổi; cần quyết định một lần ở ranh giới dữ liệu.
6. **Dữ liệu lồng nhau có cần thiết ngay?** Mảng FieldValue giúp envelope đóng nhưng buộc normalize object thành record con. Nếu cần JSON lồng phải có model-specific bounded schema và kế hoạch tương thích catalog, không dùng arbitrary JSON để mất Poka-yoke.
7. **Sửa Request sau return giữ quyết định cũ đến đâu?** Hiện mặc định invalidate snapshot trước; policy nào cho phép giữ approve của bước không bị ảnh hưởng và chứng minh dependency thế nào?
8. **Delegation chuyển trách nhiệm hay chỉ cấp quyền?** Bản nháp chuyển trách nhiệm một bước; cần chốt depth, expiry/thu hồi, người duyệt vắng mặt và bảo toàn SLA của bước cũ.
9. **L2 tự chủ theo policy đến mức nào?** Hiện agent cần human evidence cho L2; task tự giao/additive có thể L1. Cần phân ranh tự phục vụ và cam kết với người khác bằng effects thực, không theo tên model/ngành.
10. **Migration model/process và rollback:** dữ liệu lớn, unique constraint mới, đổi kiểu/PII, không đủ coverage thì cấm activate hay yêu cầu kiểm tra bổ sung? Instance cũ mặc định pin version cũ; hủy/quyền bị thu hồi giữa migration xử lý sao?
11. **Giới hạn thật và snapshot backend:** retention của key/receipt, response bytes, query cost, offset snapshot TTL, preview TTL và giá thành snapshot phải được đo trên nền được chọn. Chưa chọn nền nên không hứa SLA hiệu năng hay isolation không thể thực hiện.
12. **Sổ cái/counter có cần tách domain không?** Ledger generic tiền/ngày phép/số lượng cần balance rule riêng theo unit; Counter ưu tiên không trùng và void có vết. Yêu cầu không nhảy số hoặc nghĩa vụ lưu/xóa cần xác minh theo bối cảnh, không tự coi là quy định chung.
13. **Ký/chi tiền/connector ngoài và giấy phép:** registry action nào được ship đầu tiên, idempotency/receipt của hệ thống ngoài ra sao, action nào chỉ demo nội bộ? Không giả chữ ký/chuyển khoản thành công và không kéo phụ thuộc phi OSI.
14. **MCP Apps Tầng 2:** form/inbox/preview/decision tối thiểu phải hiển thị gì để người duyệt hiểu action, tài nguyên, revision, effects và unknown? Chọn cơ chế xác thực human có thể kiểm chứng độc lập LLM, đồng thời giữ Tầng 1 headless.

Bản nháp này là tài liệu để review thiết kế; không khẳng định đã có adapter, schema registry, IAM engine, worker, prompts/skills hay UI chạy thật.
