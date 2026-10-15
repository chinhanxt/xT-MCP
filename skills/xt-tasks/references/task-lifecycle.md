<!-- SPDX-License-Identifier: Apache-2.0 -->
# Vòng đời task

## Field lõi của `create_task`

| Tham số | Bắt buộc | Ghi chú |
|---|---|---|
| `title` | có | Ngắn, hành động |
| `assignee` | có | Ref `person:<id>` hoặc `me`; lấy từ `find_people` |
| `idempotency_key` | có | Mỗi việc mới một key |
| `due_at` | không | RFC 3339 hoặc `YYYY-MM-DD`; nói lại ngày đã hiểu |
| `task_type` | không | Mặc định theo tenant; quyết định state machine và SLA. Xem `inspect_workspace` |
| `priority` | không | `low`, `normal`, `high`, `urgent` |
| `parent_ref` | không | Việc cha (`task:<id>`) |
| `depends_on` | không | Danh sách ref; việc này không `done` khi phụ thuộc còn mở |
| `recurrence` | không | Quy tắc lặp; chỉ khi người dùng nói rõ |
| `source_ref` | không | Bản ghi gây ra việc (đề xuất, ticket) |
| `watchers` | không | Người theo dõi |
| `values` | không | Field riêng của task_type, phẳng; kiểm bằng `describe_model` |

## State machine mặc định

`todo -> in_progress -> review -> done`; thêm `returned` (trả lại làm tiếp), `blocked` (dừng đồng hồ SLA), `cancelled`.
Tenant có thể đổi. Luôn lấy `available_transitions` từ `get_record` thay vì nhớ. Mỗi transition có `level` (L1/L2/L3), `allowed` (owner/assignee/...), `required_fields` (bắt buộc truyền qua `values` của `transition_record`).

- Tự chuyển trạng thái việc của chính mình: L1.
- Transition khai L2/L3 hoặc người khác thực hiện: có thể `confirmation_required`.
- `blocked`: nên kèm `reason` (lời người dùng) để người sau hiểu.

## SLA

SLA tính từ loại việc; `get_inbox` và `get_record` cho thấy trạng thái SLA và hạn. Đừng tự tính giờ làm việc; đọc `due_at`/SLA từ server. Quá hạn có thể kích hoạt leo thang ở process; agent chỉ báo.

## Lỗi hay gặp

- `invalid_request` `wrong_tool`: dùng `create_task` cho việc.
- `invalid_request` `state_field`: đổi state qua `transition_record`.
- `precondition_failed` `dependency_open`: nêu việc phụ thuộc chưa xong (`get_record` từng ref).
- `access_denied`: giao ngoài quyền hoặc autonomy hard limit; giải thích bằng `get_history(view:"explain")`.
- `rule_violation`: hạn trong quá khứ, thiếu field bắt buộc của task_type; hỏi người dùng giá trị đúng.

## Hồ sơ đầy đủ (chỉ khi các tool này có trong danh sách tool)

| Người dùng nói | Chuỗi tool |
|---|---|
| "Dời hạn sang 20/11 / chuyển cho Lan / đổi ưu tiên" | `get_record` (revision) -> `update_record {set:{due_at:...}}`; đổi `assignee` có thể ra `confirmation_required`. Không nhét `state` vào `set` (lỗi `state_field`) |
| "Việc bị kẹt vì sao / ai đã làm gì" | `get_record` rồi `get_history {ref, view:"timeline"}` |
| "Loại việc có những trạng thái nào" | `describe_model {model_id:<task model>}` hoặc `inspect_workspace` để biết các loại việc |
| "Chạy quy trình giao việc có nghiệm thu cho việc X" (vd `task_normal`, `task_urgent`) | `describe_process {process_id}` (tham số) -> `start_process {process_id, subject_ref:"task:<id>", parameters:{assignee_user:"person:<id>", assigner_user:"me"}, idempotency_key}`. Input `<model>_id` (vd `task_id`) tự nhận `subject_ref`. Trả `process_instance` + `first_steps` (bước đầu, người nhận) |
| "Bản tin tuần đâu" | Quy trình định kỳ: xt-worker tự tạo mỗi kỳ (thứ Hai 8:00); đọc bằng `search_records {model_id:"weekly_digest"}`, không `start_process` |
