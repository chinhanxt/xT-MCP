<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Pack `giao-viec-sla`: Giao việc có SLA, leo thang, bản tin tuần

## Mục đích
Mỗi việc có đúng một người chịu trách nhiệm, có hạn, có tiêu chí hoàn thành; quá hạn thì tự nhắc và leo thang; mỗi tuần có bản tin tổng hợp. Giải PP2 (giao việc rơi rớt), PP5 (báo cáo thủ công).

## Vai trò
| Vai trò | Việc được làm |
|---|---|
| `employee` | Người thực hiện: xem việc của mình (`owner_id` = người làm) |
| `dept_head` | Giao việc, nghiệm thu, nhận leo thang cấp 2 (trong phòng mình) |
| `director` | Đọc toàn bộ, nhận bản tin |
| `admin` | Cấu hình `task_type` (SLA) |
| `xt_workflow` | Danh tính dịch vụ ghi trạng thái, tiến độ, nhật ký SLA, bản tin |
| `agent` | L0-L1 |

Quy ước dữ liệu: `owner_id` của `task` là **người thực hiện**, còn `assigner` là trường riêng. Nhờ vậy người làm xem được việc của mình qua RLS "own" mà không cần quyền phòng ban.

## Model (5)
`task_type` (SLA theo loại), `task`, `task_item` (checklist), `sla_log`, `weekly_digest`.

## Quy trình
`task_normal` (SLA 4 giờ nhận, 72 giờ làm, 48 giờ nghiệm thu) và `task_urgent` (1 / 8 / 4 giờ). Chọn theo `task.sla_class`; rule model bắt việc ưu tiên Khẩn phải đi lớp Khẩn.

Từ 0.3.2 (quy trình v3; 0.3.3/v4: chỉ ghi kèm được `result` ở bước nộp kết quả) mỗi bước người hoàn thành bằng `transition_record` trên chính `task` (`complete_via`): `accepted` = nhận việc, `awaiting_acceptance` (kèm `values.result`) = nộp kết quả — agent làm được ở L1; nghiệm thu `done` / `in_progress` (trả làm lại, cần `reason`) / `not_accepted` là chỉ-người (người giao xác nhận). IAM: `tsk.step-transition`.

| Bước | Lane | Quá hạn thì |
|---|---|---|
| `accept` | `assignee` | chuyển cho `assigner` (kèm thông báo) |
| `work` | `assignee` | chuyển cho `dept_head` |
| `acceptance` | `assigner` | chuyển cho `dept_head` |
| `close` (service) | | đóng việc |

Từ chối nhận việc, hoặc không chấp nhận kết quả, đều kết thúc quy trình ở "Bị từ chối".

`weekly_digest` (v3): quy trình hai service (`weekly_rollup` ghi bản ghi `weekly_digest`, `send_weekly_digest` gửi giám đốc + trưởng phòng và đánh dấu `sent`) chạy 8:00 thứ Hai theo khối `trigger` trong spec. **Khối `trigger` do compiler bỏ qua**; lịch phải do `xt-worker` đọc (xem REPORT.md).

## Quy tắc và nơi thực thi
| Quy tắc | Thực thi tại |
|---|---|
| Một người chịu trách nhiệm chính | `assignee` là trường bắt buộc duy nhất |
| Có hạn chót và tiêu chí hoàn thành | trường bắt buộc |
| Hoàn thành phải có kết quả; bị chặn phải có lý do; hoàn thành phải có thời điểm | rule `task` |
| Tiến độ 0-100 | min/max |
| Việc phụ thuộc việc khác | `task.depends_on_task` (ref tự tham chiếu) |
| **Người làm không tự nghiệm thu** | IAM `self_approval` + Deny `deny-assignee-self-accept` + lane `assigner` |
| **Agent không nghiệm thu** | Deny `deny-agent-acceptance` |
| Người dùng không ghi thẳng trạng thái | DB: `employee` chỉ `read`; ghi qua `xt_workflow` |

## Agent được làm gì theo mức tự chủ
| Mức | Được | Không được |
|---|---|---|
| L0 | Đọc, tìm việc quá hạn/bị chặn, trả lời "tuần này team kẹt gì", đọc bản tin | Ghi |
| L1 | Tách một chỉ đạo thành danh sách việc (nháp), đề xuất người làm theo tải, nhắc việc, soạn bản tin | Nghiệm thu, xóa |
| L2, L3 | Không có gì | Nghiệm thu bị chặn ở mọi mức |

## Dữ liệu demo
3 loại việc, 4 công việc (đang làm, quá hạn đã leo thang, bị chặn, hoàn thành), 4 mục checklist, 2 dòng nhật ký SLA, bản tin tuần 41.

## Giới hạn đã biết
Thời hạn SLA là hằng số trong spec nên phải có hai file quy trình (thường, khẩn) thay vì đọc từ `task_type`; leo thang **thay thế** người làm thay vì chỉ nhắc; không có vòng làm lại; lane `assignee`, `assigner` phải được runtime gán từ dữ liệu từng việc. Xem `../REPORT.md`.
