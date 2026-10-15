<!-- SPDX-License-Identifier: Apache-2.0 -->
# Hồ sơ đầy đủ (catalog 18 tool)

Chỉ dùng phần này khi danh sách tool của bạn có các tool bên dưới (server `instructions` liệt kê
"Tools in this session"). Ở hồ sơ `core-10` các tool `inspect_workspace`, `describe_model`,
`describe_process`, `get_history`, `update_record`, `upload_file`, `propose_model_change`,
`start_process` không có:
gọi chúng chỉ nhận lỗi `wrong_tool`. Khi đó field hợp lệ lấy từ `details.valid_fields` của lỗi.

Khi có đủ tool:
- Chưa biết field -> `describe_model(model_id)` trước `create_record`/`update_record`.
- Chưa biết model nào có -> `inspect_workspace` (một lần mỗi phiên).
- Vì sao bị chặn / số này từ đâu -> `get_history(view="explain")` với `explain_ref`/`lineage_ref`.
- Sửa field (số tiền, người nhận, hạn) -> `get_record` (revision) -> `update_record`.

## Định tuyến đầy đủ: ý định -> tool đầu tiên

| Người dùng nói | Tool đầu tiên | Rồi |
|---|---|---|
| Tôi là ai / có loại đề xuất, model, quy trình nào | `inspect_workspace` | `describe_model`, `describe_process` |
| Cần những thông tin gì để tạo X / X có trạng thái nào | `describe_model` | |
| Ai duyệt X / mất bao lâu / trần SLA | `describe_process` (`for_values` để tính tuyến) | |
| Ai báo cáo cho A / sếp của A / tìm anh Bình | `find_people` | lấy `person:<id>` |
| Tìm / liệt kê / các X đang ... | `search_records` (`where` nếu biết model, `query` nếu không) | `get_record` |
| Tổng / đếm / trung bình / theo tháng / theo phòng | `aggregate_records` (ưu tiên `metric` có tên) | `get_history(view=explain)` cho nguồn số |
| Cho xem chi tiết X, đang ở bước nào | `get_record` (`include`: steps...) | |
| Hôm nay tôi phải làm gì / có gì cần duyệt | `get_inbox` | `get_record` |
| Tạo đề xuất / đơn / phiếu | `describe_model` | `create_record` -> `submit_request` |
| Gửi đi duyệt | `submit_request` | |
| Duyệt / từ chối / trả lại | `decide_request` | luồng confirm_url |
| Giao việc cho A | `find_people` | `create_task` |
| Bắt đầu quy trình tiếp nhận / nghỉ việc cho A, chạy quy trình giao việc có nghiệm thu cho việc X | `describe_process` (lấy `parameters`, `trigger.kind` phải là `manual`) | `start_process {process_id, subject_ref, parameters}`; quy trình gắn đề nghị (`trigger.kind=on_submit`) đi `create_record` -> `submit_request`; quy trình định kỳ (`timer`, vd bản tin tuần) do xt-worker tự chạy mỗi kỳ, không khởi động tay |
| Xong / bắt đầu / trả lại / rút / đã đọc | `get_record` (xem `available_transitions`) | `transition_record` |
| Sửa số tiền / đổi người / dời hạn | `get_record` (lấy revision) | `update_record` |
| Đính kèm hóa đơn, hợp đồng | `upload_file` | gán `file:<id>` bằng `update_record` |
| Vì sao bị chặn / ai đã làm / số này từ đâu | `get_history` (`explain_ref`/`lineage_ref`) | |
| Thêm nghiệp vụ / thêm trường / đổi trạng thái | skill `xt-data-modeling` | `propose_model_change` |
| Duyệt đề xuất, hàng chờ duyệt | skill `xt-approvals` | |
| Giao việc, SLA, việc định kỳ | skill `xt-tasks` | |

Tool sai chỗ -> server trả `invalid_request` reason `wrong_tool` kèm `continuation`; gọi theo `continuation`.

## Ví dụ 1: tạo và gửi đề xuất (L1, không hỏi lại)

> Người dùng: "Tạm ứng 8 triệu đi Đà Nẵng 20-22/11."

```
inspect_workspace {include:["models"]}              # thấy advance_request
describe_model {model_id:"advance_request"}         # field: amount, trip_start, trip_end, purpose, rules
create_record {model_id:"advance_request",
  values:{amount:"8000000", trip_start:"2026-11-20", trip_end:"2026-11-22", purpose:"Công tác Đà Nẵng"},
  idempotency_key:"create-adv-danang-1"}            # -> ref advance_request:tu-..., revision 1, suggested_next=submit_request
submit_request {ref:"advance_request:tu-2026-0042", idempotency_key:"submit-adv-0042-1"}
```
Trả lời: "Đã gửi TU-2026-0042 (8.000.000 VND). Tuyến duyệt: Mai -> Long, hạn bước 1 là ..." (lấy từ `route`, không tự nghĩ ra).
Tiền/decimal gửi dạng **chuỗi** (`"8000000"`), ngày `YYYY-MM-DD`.

## Ví dụ 2: số liệu

> "Tổng tạm ứng tháng 11 theo phòng ban?"

```
describe_model {model_id:"advance_request"}         # tìm metric "advance_total", field department
aggregate_records {model_id:"advance_request", metric:"advance_total",
  period:"2026-11", group_by:["department"]}
```
Đọc `summary` (nêu filter đã áp). Hỏi "nguồn số này?" -> `get_history {ref:<lineage_ref>, view:"explain"}`.
Không có `metric` phù hợp: dùng `metrics`/`where` tường minh, và nói rõ filter trạng thái đã dùng.
