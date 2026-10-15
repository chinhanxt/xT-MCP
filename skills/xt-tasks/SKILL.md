---
name: xt-tasks
description: Assign and track work in xT for any team - create tasks with owner, deadline, subtasks, dependencies and recurrence, change assignee or due date, move tasks through their lifecycle, and report on workload. Use for "giao việc cho ...", "nhắc hạn", "việc của tôi hôm nay", "dời hạn", "xong rồi", "trả lại việc", "ai đang làm gì", assign/task/deadline/subtask/SLA/overdue.
metadata:
  version: "1"
  tool_count: 17
---

<!-- SPDX-License-Identifier: Apache-2.0 -->
# xT tasks

Task là một **model thuộc primitive `task`**; mỗi loại việc có state machine riêng (mặc định `todo -> in_progress -> review -> done`, thêm `returned`, `blocked` dừng SLA, `cancelled`). SLA lấy mặc định từ loại việc. Đừng giả định tên trạng thái: xem `describe_model` hoặc `available_transitions` của `get_record`.
Quy tắc chung (key, CAS, lỗi, PII) ở `xt-core`.

## Quy tắc

1. Tạo việc **chỉ bằng `create_task`** (không `create_record`; sai tool -> `wrong_tool` + `continuation`).
2. `assignee` phải là ref thật: `find_people` trước, không đoán `person:` từ tên. Nhiều người trùng tên -> hỏi lại.
3. Đổi người/hạn/ưu tiên = `update_record` (cần `expected_revision`). Đổi trạng thái = `transition_record`. Không nhét `state` vào `set` (lỗi `state_field`).
4. Giao cho cấp dưới trực tiếp: L1, làm thẳng. Ngoài tuyến báo cáo hoặc đổi `assignee` (field `impact: high`): có thể ra `confirmation_required` (xem `xt-core`) - đưa link, chờ, gọi lại cùng key.
5. Hạn dạng `due_at` RFC 3339, hoặc `YYYY-MM-DD` (áp múi giờ tenant). Quy đổi "thứ 6 này" thành ngày cụ thể **và nói lại ngày đã hiểu**; không đoán nếu mơ hồ.

## Định tuyến

| Người dùng nói | Chuỗi tool |
|---|---|
| "Giao anh Bình làm báo cáo trước thứ 6" | `find_people {query:"Bình"}` -> `create_task` |
| "Việc của tôi hôm nay / quá hạn" | `get_inbox {kinds:["task"], due_before:<ngày>}` |
| "Anh Bình đang có việc gì" | `search_records {model_id:<task model>, where:{assignee:"person:binh", state:{ne:"done"}}}` (xem model qua `inspect_workspace`) |
| "Tôi bắt đầu / xong / trả lại việc X" | `get_record` (revision, transitions) -> `transition_record` |
| "Dời hạn sang 20/11 / chuyển cho Lan" | `get_record` -> `update_record {set:{due_at:...}}` |
| "Chia nhỏ việc này" | `create_task {parent_ref}` cho từng việc con |
| "Việc B chỉ làm sau việc A" | `create_task {depends_on:["task:a"]}` |
| "Hàng tuần/hàng tháng ..." | `create_task {recurrence:"<RRULE>"}` (chỉ dùng chuỗi lặp người dùng nói rõ) |
| "Việc bị kẹt vì sao" | `get_record` rồi `get_history` |
| "Bao nhiêu việc quá hạn theo người" | `aggregate_records` (metric nếu có; không tự đếm) |

Chi tiết trường, trạng thái, phụ thuộc: [task-lifecycle](references/task-lifecycle.md).

## Ví dụ: giao việc

> "Giao anh Bình báo giá cho khách ABC, hạn thứ 6 (20/11), ưu tiên cao."

```
find_people {query:"Bình"}                       # -> person:binh (Lê Bình, IT) - một kết quả duy nhất
create_task {title:"Báo giá khách ABC", assignee:"person:binh", due_at:"2026-11-20",
  priority:"high", idempotency_key:"task-baogia-abc-1"}
```
Trả lời: "Đã giao việc cho anh Bình, hạn 20/11, ưu tiên cao (task:...)." Nếu thấy hai anh Bình: "Có anh Bình (IT) và anh Bình (Kinh doanh), anh muốn ai?"
Nếu ra `confirmation_required` (người nhận ngoài tuyến báo cáo): đưa `confirm_url` cho `confirmer`, chờ, gọi lại cùng key.

## Ví dụ: hoàn thành việc

> "Báo cáo tuần xong rồi."

```
search_records {model_id:"task", where:{assignee:"me", state:{ne:"done"}}}   # tìm đúng việc
get_record {ref:"task:bc-tuan-47"}               # revision 3, available_transitions: [review, blocked]
transition_record {ref:"task:bc-tuan-47", to_state:"review", expected_revision:3, idempotency_key:"tr-bc47-review-1"}
```
Task này không cho `done` trực tiếp (cần `review`): nói đúng điều đó, đừng cố ép sang `done`. Lỗi `precondition_failed` reason `dependency_open` -> nêu việc phụ thuộc chưa xong.

## Báo cáo tiến độ

Số lượng/quá hạn: `aggregate_records` (cho `period`, `group_by` assignee/state). Danh sách cụ thể: `search_records`.
Nói rõ phạm vi: "Tính trên các việc chị được xem; `total` có thể ít hơn toàn công ty."
