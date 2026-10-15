<!-- SPDX-License-Identifier: Apache-2.0 -->
# Danh mục `operations` của `propose_model_change`

Đầu vào: `{model_id, operations[1..50], rationale, dry_run?, base_revision?, idempotency_key}`. Mỗi phần tử có `op` và **chỉ điền thuộc tính tương ứng**.
`model_id` khớp `^[a-z][a-z0-9_]{0,62}$`. Luôn đối chiếu với schema thật trong tool list của server.

| `op` | Thuộc tính | Ghi chú |
|---|---|---|
| `create_model` | `model:{label, primitive, description?, title_field?, process?}` | `primitive`: `record`/`request`/`task`/`document`. `request` gắn `process` đã tồn tại |
| `add_field` | `field:{id,label,type,required?,multiple?,enum_values?,ref_model?,min?,max?,max_length?,unique?,pii?,sensitive?,impact?,description?}` | |
| `update_field` | `field_id`, `changes:{label,required,enum_values,min,max,max_length,unique,pii,sensitive,impact,description}` | Không đổi `type`/`id` (đổi kiểu có backfill có thể `capability_unavailable`) |
| `remove_field` | `field_id` | Phá hủy: rủi ro cao, có thể cần 2 người duyệt |
| `add_state` | `state:{id,label,initial?,terminal?,pauses_sla?}` | Đúng **một** state `initial` |
| `add_transition` | `transition:{from[],to,label,level?,allowed?,required_fields?,guard_rule?,starts_process?}` | `from`,`to`,`label` bắt buộc. Mỗi cặp (from,to) một transition; `level` L1/L2/L3 là mức agent cần; `allowed`: owner/assignee/requester/role:<id>/any |
| `remove_transition` | `transition:{from[],to,label}` (định danh bằng from,to) | |
| `add_rule` | `rule:{id,expr,message}` | `expr` hữu hạn trên field (vd `trip_end >= trip_start`); `message` tiếng Việt, người dùng sẽ thấy |
| `remove_rule` | `rule_id` | |
| `add_metric` | `metric:{id,label,op,field?,where?}` | `op`: count/sum/avg/min/max. `where` phẳng, ví dụ `{"state":"submitted","amount":{"gte":"5000000"}}`. Về sau `aggregate_records(metric=id)` |

Kết quả: `classification` (`additive`/`tightening`/`destructive`; destructive cần 2 người duyệt), `diff[{op,path,before,after}]`, `findings[{severity,message,affected_count}]` (`blocker` thì phải sửa), `affected_records` (null = chưa biết), `approvals_required`; khi `dry_run:false` có thêm `proposal`, `request` (theo dõi bằng `get_record`). Dùng để giải thích, không tự đánh giá lại.

## Nguyên tắc soạn

- Model đã có -> thêm bằng `add_*`, không tạo lại. Nhiều thay đổi liên quan gửi **một** proposal (tối đa 50 op).
- Mỗi lần chỉ sửa trong phạm vi người dùng yêu cầu.
- Rule thể hiện ràng buộc thật (ngày, hạn mức); ngưỡng duyệt/người duyệt thuộc **process**, không đặt vào model.
- Đặt tên `snake_case`, tiếng Anh không dấu cho `id`; `label` có thể tiếng Việt.
- Metric có tên cho mọi con số người dùng hỏi lặp lại (tổng, đang mở, quá hạn) để `aggregate_records` trả nhất quán.
