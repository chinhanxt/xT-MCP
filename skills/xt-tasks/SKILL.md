---
name: xt-tasks
description: Assign and track work in xT for any team - create tasks with owner, deadline, subtasks, dependencies and recurrence, change assignee or due date, move tasks through their lifecycle, and report on workload. Use for "giao việc cho ...", "nhắc hạn", "việc của tôi hôm nay", "dời hạn", "xong rồi", "trả lại việc", "ai đang làm gì", assign/task/deadline/subtask/SLA/overdue.
metadata:
  version: "1"
  tool_count: 17
---

<!-- SPDX-License-Identifier: Apache-2.0 -->
# xT tasks

Giao và theo dõi việc. Mỗi loại việc có state machine riêng; tên trạng thái lấy từ `get_record.available_transitions`, đừng đoán.
Quy tắc chung ở `xt-core`. Chỉ gọi tool có trong danh sách tool của bạn.

## Quy tắc

1. **Giao việc:** `find_people` lấy ref -> `create_task`. Nhiều người trùng tên -> hỏi lại. Không thấy ai -> nói không có trong danh bạ, không đoán ref.
2. Tạo việc chỉ bằng `create_task` (không `create_record`).
3. Giao ngoài tuyến báo cáo có thể ra `confirmation_required`: chép nguyên `confirm_url`, nói ai phải bấm, dừng, không gọi lại.
4. **Báo xong / bắt đầu / bị chặn:** `get_record` (lấy `revision`, `available_transitions`) -> `transition_record` với `to_state` có trong danh sách đó. Không có `done` thì dùng bước kế (ví dụ chờ nghiệm thu) và nói rõ.
5. Hạn `due_at`: `YYYY-MM-DD`. "Thứ 6 này" -> đổi thành ngày cụ thể và nói lại ngày đã hiểu.
6. "Hằng tuần / hằng tháng" -> **luôn đặt `recurrence`** (RRULE). Thứ Hai `MO`, Ba `TU`, Tư `WE`, Năm `TH`, **Sáu `FR`**, Bảy `SA`, Chủ nhật `SU`. Thứ Sáu hằng tuần = `FREQ=WEEKLY;BYDAY=FR` (không phải `SA`).
7. **Liệt kê việc quá hạn / bị chặn:** `search_records {model_id:"task", where:{...}}` theo field hạn và trạng thái (không biết tên field: lỗi trả `valid_fields`); quá hạn = hạn < hôm nay và chưa xong. **Đếm** (bao nhiêu) -> `aggregate_records`. `get_inbox` chỉ là việc của chính tôi.
8. Chưa gọi `transition_record`/`create_task` thành công thì không nói "đã cập nhật / đã giao".

## Chuỗi tool

| Người dùng nói | Chuỗi |
|---|---|
| Giao anh Bình làm X trước thứ 6 | `find_people {query:"Bình"}` -> `create_task` |
| Việc của tôi hôm nay | `get_inbox` |
| Việc nào quá hạn / bị chặn | `search_records {model_id:"task", where:{...}}` (loại việc đã xong) |
| Bao nhiêu việc bị chặn / theo trạng thái | `aggregate_records {model_id:"task", where:{state:"blocked"}}` hoặc `group_by:["state"]` |
| Tôi xong việc X | `get_record` -> `transition_record` |
| Vì sao việc bị chặn | `get_record` (đọc lý do trong bản ghi) |

## Ví dụ

> "Giao anh Bình báo giá cho khách ABC, hạn 20/11, ưu tiên cao."

```
find_people {query:"Bình"}                                  # một kết quả: person:binh
create_task {title:"Báo giá khách ABC", assignee:"person:binh", due_at:"2026-11-20",
  priority:"high", idempotency_key:"task-baogia-abc"}
```
Trả lời: "Đã giao anh Bình, hạn 20/11, ưu tiên cao (task:...)."

Trường, trạng thái, phụ thuộc, hồ sơ đầy đủ: [task-lifecycle](references/task-lifecycle.md).
