<!-- SPDX-License-Identifier: Apache-2.0 -->
# Trạng thái đề xuất và quyết định

Model `request` có `process`. Các trạng thái do engine điều khiển; agent chỉ chạm qua tool nêu dưới.

| Trạng thái | Nghĩa | Agent làm được |
|---|---|---|
| `draft` | Mới tạo | `update_record` (sửa), `submit_request`, `delete`: (v1.0) |
| `submitted` / `in_review` | Đang ở một bước duyệt | `transition_record -> withdrawn` (người đề nghị). Không sửa field trừ khi model cho phép |
| `returned` | Người duyệt trả lại | `update_record` theo góp ý, rồi `submit_request` lại |
| `approved` | Qua hết bước | Chỉ đọc; action sau duyệt chạy ở process |
| `rejected` | Bị từ chối | Chỉ đọc; tạo đề xuất mới nếu cần (key mới) |
| `withdrawn` | Đã rút | Chỉ đọc |

Tên trạng thái thật lấy từ `describe_model`/`get_record.available_transitions`; bảng trên là mặc định của primitive.

## `decide_request`

| `decision` | Bắt buộc thêm | Ghi chú |
|---|---|---|
| `approve` | `expected_revision` | Bước kế tiếp hoặc `approved` nếu hết tuyến |
| `reject` | `reason` | Kết thúc đề xuất |
| `return` | `reason` | Quay về người đề nghị |
| `delegate` | `delegate_to` (ref `person:`) | Người nhận phải là người khác người đề nghị; vi phạm -> `access_denied`/`rule_violation` |

Kết quả thành công có `outcome` (ví dụ `step_completed`, `request_approved`). Đọc `outcome`, không suy từ im lặng.

## Lỗi hay gặp

- `precondition_failed`: đề xuất không còn ở bước của người này (đã có người quyết). `get_record` rồi báo trạng thái hiện tại.
- `revision_conflict`: bản ghi đổi từ lúc đọc. Đọc lại, tóm tắt thay đổi cho người duyệt trước khi quyết lại (họ phải thấy nội dung mới).
- Vượt ngưỡng (ví dụ số tiền lớn): tuyến dài hơn do policy; trang xác nhận có thể đòi xác thực mạnh hơn. Không phải lỗi.
- `awaiting_second_approver`: policy đòi người thứ hai; nêu tên từ `details`.

## Ủy quyền dài hạn

Agent chỉ thực hiện `delegate` từng đề xuất. Ủy quyền theo khoảng thời gian là cấu hình do quản trị (v0.1 không có tool).
