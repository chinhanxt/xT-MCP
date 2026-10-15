---
name: xt-data-modeling
description: Design or change business models and lifecycles in xT by chat for any domain - interview the user, propose entities, fields, states, transitions, rules and named metrics as JSON via propose_model_change (dry run first), then hand to a human for approval. Use for "thêm nghiệp vụ", "thêm trường", "quản lý mượn trả thiết bị", "thêm trạng thái", "thêm quy tắc", "tạo loại đề xuất mới", design a data model, add a field, new workflow, change schema. Never active until a human approves.
metadata:
  version: "1"
  tool_count: 17
---

<!-- SPDX-License-Identifier: Apache-2.0 -->
# xT data modeling

xT không cài cứng nghiệp vụ. Doanh nghiệp nào cũng có thể thêm model mới (quản lý thiết bị, hợp đồng, khách hàng, ticket...) bằng cách nói chuyện: agent đề xuất **spec JSON**, con người duyệt, rồi mới có hiệu lực.
Quy tắc chung ở `xt-core`.

## Quy tắc

1. **Luôn đọc trước:** `inspect_workspace` (đã có model gì), `describe_model` của model liên quan. Không tạo trùng, không đoán id.
2. **Chỉ qua `propose_model_change`.** Spec là **JSON** (mảng `operations`), không YAML, không SQL, không code. Biểu thức rule là chuỗi hữu hạn (`trip_end >= trip_start`).
3. **`dry_run:true` trước** (mặc định): trả `classification`, `diff`, `findings`, `affected_records`, **không lưu**. Tóm tắt cho người dùng bằng tiếng Việt, hỏi "áp dụng không?".
4. Người dùng đồng ý -> gọi lại **y hệt với `dry_run:false`** (key mới, cùng `operations`). Đó là *đề xuất* (L1): lưu bất biến và tạo Request quản trị.
5. **Không có tool "áp dụng".** Kích hoạt chỉ sau khi người có thẩm quyền duyệt qua `decide_request` + `confirm_url` (thay đổi phá hủy cần 2 người). Nói rõ: "Chưa có hiệu lực cho tới khi chị X duyệt."
6. Không đổi dữ liệu thật để "khớp" model; không dùng `capability_unavailable` làm cớ giả lập bằng field thừa.
7. Field cá nhân/nhạy cảm (CCCD, lương, sức khỏe, địa chỉ nhà, SĐT) **phải** đặt `pii:true` / `sensitive:true` ngay khi đề xuất, kể cả khi người dùng không nhắc.

## Quy trình

1. **Phỏng vấn ngắn** (tối đa vài câu, mỗi lần 1-2 ý): đối tượng quản lý là gì, ai tạo/ai xử lý, vòng đời (các trạng thái), cần duyệt không (primitive `request`), thông tin bắt buộc, số liệu muốn xem.
2. Chọn primitive: `record` (dữ liệu thường), `request` (cần duyệt, gắn `process` đã có), `task` (việc), `document`.
3. Soạn `operations` theo [operations-catalog](references/operations-catalog.md) và [field-types](references/field-types.md).
4. `propose_model_change {dry_run:true}` -> đọc diff/rủi ro -> trình bày.
5. Sửa theo phản hồi (lặp 4). Có `revision_conflict` thì `describe_model` lại, đặt `base_revision` mới.
6. `dry_run:false` -> báo `proposal`/`request` ref (theo dõi bằng `get_record`) và ai sẽ duyệt.

## Ví dụ: nghiệp vụ mới "mượn trả thiết bị"

> "Công ty tôi cần quản lý mượn trả laptop, máy chiếu."

Hỏi: "Mỗi lần mượn cần ghi: thiết bị, người mượn, ngày mượn, hạn trả? Có cần ai duyệt không? Trạng thái: đang mượn / đã trả / mất?" -> người dùng xác nhận, không cần duyệt.

```
inspect_workspace {include:["models"]}           # chưa có model thiết bị
propose_model_change {model_id:"device_loan", dry_run:true,
  rationale:"Quản lý mượn trả thiết bị",
  operations:[
   {op:"create_model", model:{label:"Mượn trả thiết bị", primitive:"record", title_field:"device"}},
   {op:"add_field", field:{id:"device", label:"Thiết bị", type:"string", required:true}},
   {op:"add_field", field:{id:"borrower", label:"Người mượn", type:"person", required:true}},
   {op:"add_field", field:{id:"due_date", label:"Hạn trả", type:"date", required:true}},
   {op:"add_state", state:{id:"borrowed", label:"Đang mượn", initial:true}},
   {op:"add_state", state:{id:"returned", label:"Đã trả", terminal:true}},
   {op:"add_transition", transition:{from:["borrowed"], to:"returned", label:"Trả thiết bị", level:"L1"}},
   {op:"add_metric", metric:{id:"borrowed_count", label:"Đang mượn", op:"count", where:{state:"borrowed"}}}
  ],
  idempotency_key:"model-device-loan-dry-1"}
```
Agent: "Sẽ tạo model 'Mượn trả thiết bị' với 3 trường, 2 trạng thái, 1 chỉ số. `classification: additive`, không ảnh hưởng dữ liệu cũ. Anh đồng ý gửi duyệt?"
Đồng ý -> cùng `operations`, `dry_run:false`, `idempotency_key:"model-device-loan-1"`.
"Đã gửi đề xuất, chờ quản trị duyệt; chưa tạo được phiếu mượn cho tới lúc đó."

## Ví dụ: thêm trường vào model có sẵn

> "Phiếu tạm ứng thêm trường 'mã dự án'."
```
describe_model {model_id:"advance_request"}      # lấy revision, kiểm tra chưa có project_code
propose_model_change {model_id:"advance_request", base_revision:4, dry_run:true,
  rationale:"Theo dõi chi phí theo dự án",
  operations:[{op:"add_field", field:{id:"project_code", label:"Mã dự án", type:"string", required:false}}],
  idempotency_key:"model-adv-projcode-dry-1"}
```
Thêm field **bắt buộc** vào model đã có dữ liệu: nêu rõ tác động lên bản ghi cũ (từ diff); đề nghị `required:false` trước nếu người dùng chưa quyết.

## Khi bị từ chối / không hỗ trợ

- `rule_violation` (id sai pattern, trùng id, trạng thái không có `initial`, hai transition cùng (from,to)): sửa theo `remediation`, key mới.
- `capability_unavailable` (ví dụ đổi kiểu có backfill): nói đúng giới hạn, đề xuất cách thay (field mới + chuyển dữ liệu).
- `access_denied`: người dùng không có quyền `model.propose`; hướng họ tới quản trị.

## Đọc đúng guide

- Danh sách `op` và hình dạng từng `op`: [references/operations-catalog.md](references/operations-catalog.md)
- Kiểu field, PII, impact, đặt tên, ràng buộc: [references/field-types.md](references/field-types.md)
