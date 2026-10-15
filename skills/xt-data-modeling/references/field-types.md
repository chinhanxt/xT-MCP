<!-- SPDX-License-Identifier: Apache-2.0 -->
# Kiểu field và cờ

| `type` | Giá trị khi ghi | Ghi chú |
|---|---|---|
| `string` / `text` | chuỗi | `max_length` tùy chọn; `text` cho nội dung dài |
| `integer` | số nguyên | |
| `decimal` / `money` | **chuỗi thập phân** (`"8000000"`) | Float có phần lẻ bị từ chối; gửi chuỗi |
| `boolean` | true/false | |
| `date` | `YYYY-MM-DD` | |
| `datetime` | RFC 3339 | Ngày không giờ áp múi giờ tenant |
| `enum` | một trong `enum_values` | Giá trị `snake_case`; nhãn hiển thị ở `label` |
| `ref` | `model:id` | Cần `ref_model` |
| `person` | `me` hoặc `person:<id>` | Lấy từ `find_people` |
| `file` | `file:<id>` | Từ `upload_file` |

`multiple:true` -> mảng scalar. Payload luôn **phẳng** `{field: value}`; không lồng object. Xóa giá trị: `null`.

## Cờ

- `required`: bắt buộc khi tạo. Thêm vào model đã có dữ liệu: cân nhắc.
- `unique`: trùng giá trị -> `rule_violation`.
- `pii`: dữ liệu cá nhân; client cloud thấy `[PII:token]`. `sensitive`: nhạy cảm (CCCD, sức khỏe, lương), ngầm định pii.
- `impact: high`: sửa field này ảnh hưởng người khác (ví dụ `assignee`); sửa cần L2 -> có thể ra `confirmation_required`.
- `min`/`max`: số hoặc ngày; ràng buộc chéo nhiều field dùng `add_rule`.

## Hỏi để đặt cờ đúng

Dữ liệu này có định danh một người không? Lộ ra thì hại gì? Nếu có -> `pii`/`sensitive`. Ai sửa được, sửa có ảnh hưởng người khác không? Nếu có -> `impact: high`.

## Ghi nhớ khi dùng dữ liệu sau đó

Sau khi model được duyệt, đọc lại `describe_model(model_id, include_values_schema:true)` để biết schema ghi chính xác trước `create_record`.
