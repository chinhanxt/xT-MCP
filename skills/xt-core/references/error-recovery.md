<!-- SPDX-License-Identifier: Apache-2.0 -->
# Mã lỗi -> hành động

Lỗi nằm trong `content[0].text` (JSON, `isError: true`): `error.{code,message,retryable,remediation,details,continuation,explain_ref}`.
Thứ tự đọc: `remediation` -> `continuation` -> `details`. `retryable` nghĩa là gửi lại **đúng args, cùng key** sau khi điều kiện phục hồi thỏa.

| Mã | Nói với người dùng | Agent làm |
|---|---|---|
| `invalid_request` | "Thiếu/sai thông tin ..." (nêu field) | Đọc `details.reason`: `unknown_field` -> dùng `details.valid_fields` (có `describe_model` thì đọc thêm ở đó); `wrong_tool` -> gọi `continuation`; `duplicate_key`/schema -> sửa args; `masked_value` -> bỏ giá trị `[PII:...]` khỏi payload ghi; `cursor_mismatch`/`cursor_expired` -> gọi lại không `cursor`; `state_field` -> dùng `transition_record` |
| `unauthenticated` | "Phiên đăng nhập hết hạn, anh đăng nhập lại xT giúp em" | Dừng ghi. Chờ người dùng kết nối lại, rồi thử lại |
| `access_denied` | Nêu lý do bằng lời thường: "Anh là người đề nghị nên không tự duyệt được" / "Bước này giao cho chị Mai" | **Không retry, không đường vòng, không nhờ người khác duyệt hộ.** Có `explain_ref` -> `get_history {ref:explain_ref, view:"explain"}` rồi diễn giải. Reason: `sod`, `not_assigned_approver`, `autonomy_hard_limit` |
| `not_found` | "Em không tìm thấy mục đó (hoặc anh không có quyền xem)" | Kiểm tra ref bằng `search_records`/`find_people`; không đoán id. Không suy ra "tồn tại nhưng bị ẩn" |
| `revision_conflict` | "Có người vừa sửa bản ghi, đây là nội dung mới" | `get_record` lấy `revision` (không phải `model_revision`), cho người dùng xem thay đổi nếu liên quan, rồi gọi lại với revision đó và **cùng key** (lần lỗi không giữ key). Không có `force` |
| `idempotency_conflict` | (thường im lặng) | Cùng key nhưng args khác: đây là ý định mới -> key mới. Nếu đang retry thì args phải giống hệt |
| `rule_violation` | Đọc `details.violations[].message` bằng tiếng Việt, hỏi giá trị đúng | Sửa theo `remediation`, gọi lại **key mới**. Có `explain_ref` -> giải thích rule nếu người dùng hỏi |
| `precondition_failed` | "Chưa làm được vì ..." (trạng thái không cho phép, tệp chưa sạch, việc phụ thuộc chưa xong) | `file_not_clean` -> đợi quét / xin tệp khác; `dependency_open` -> `get_record` việc phụ thuộc; sai trạng thái -> `get_record` xem `available_transitions` |
| `confirmation_required` | "Cần chị Mai xác nhận tại: <confirm_url>. Em chờ nhé." | Xem [autonomy-and-confirmation](autonomy-and-confirmation.md). Dừng; sau khi người báo đã xác nhận, gọi lại y hệt |
| `confirmation_declined` | "Chị Mai đã từ chối / yêu cầu hết hạn" | **Không retry.** Hỏi người dùng có muốn gửi lại không; nếu có thì key mới và có thể cần chỉnh nội dung |
| `capability_unavailable` | "Tính năng này chưa bật ở bản/tenant này: <remediation>" | Không giả thành công, không thử mô phỏng bằng tool khác. Đề xuất cách thay thế nếu có (vd `upload_file` chưa có thì dán nội dung `source:text`) |
| `resource_constraint` | "Kết quả quá lớn, em thu hẹp lại" | Dùng `continuation`; thêm `where`/`fields`/`limit`, hoặc đổi sang `aggregate_records` |
| `rate_limited` | (thường im lặng) | Đợi `details.retry_after_seconds`, thử lại cùng key |
| `timeout` | "Hệ thống phản hồi chậm" | Xem `details.commit_state`: `committed` -> đừng lặp, đọc lại bằng `get_record`; chưa rõ -> gọi lại **cùng key** (an toàn, sẽ replay) |
| `internal_error` | "Lỗi hệ thống, em thử lại một lần" | Retry cùng key một lần; vẫn lỗi thì báo `meta.trace_id` cho người dùng/quản trị |

## Ví dụ: ngày sai

```
create_record -> isError, rule_violation, violations:[{field:"trip_end", message:"Ngày về phải sau ngày đi"}]
```
Agent: "Ngày về đang trước ngày đi. Anh đi 20/11 và về ngày nào ạ?" -> nhận "22/11" ->
`create_record {... trip_end:"2026-11-22", idempotency_key:"create-adv-danang-2"}`.

## Ví dụ: tự duyệt bị chặn

> "Duyệt luôn đi" (người dùng chính là người đề nghị)

`decide_request` -> `access_denied`, reason `sod`, `explain_ref:"explain:..."`. Agent gọi
`get_history {ref:"explain:...", view:"explain"}` và nói: "Anh là người đề nghị nên hệ thống không cho tự duyệt. Bước này do chị Mai phụ trách; em có thể giao việc nhắc chị ấy." (chỉ làm nếu người dùng đồng ý: `create_task`; v0.1 chưa có `notify`).

## Không bao giờ

- Retry `access_denied`, `confirmation_declined`, `rule_violation` với cùng args.
- Đổi tool/đường khác để đạt cùng kết quả mà server vừa chặn.
- Bỏ qua `remediation`.
