<!-- SPDX-License-Identifier: Apache-2.0 -->
# Mức tự chủ, confirm_url, idempotency, CAS

## Mức tự chủ

| Mức | Ví dụ | Agent |
|---|---|---|
| L0 | Mọi tool đọc | Làm thẳng |
| L1 | Tạo draft/comment, gửi đề xuất của chính người được đại diện, tự chuyển trạng thái việc của mình, giao việc cho cấp dưới trực tiếp, upload, `propose_model_change` | Làm thẳng, không hỏi lại |
| L2 | Giao việc ngoài tuyến báo cáo, sửa field `impact: high`, transition khai L2 | Làm nếu `effective_level` <= trần agent; nếu không: `confirmation_required` |
| L3 | Transition khai L3, xóa, thu hồi quyền | Luôn cần người xác nhận (có thể cần người thứ hai: `awaiting_second_approver`) |
| human_only | `decide_request`, ký, lệnh chi, kích hoạt định nghĩa | Luôn cần chính người được giao bước đó xác nhận |

Không tự nâng mức; trần của agent do người dùng cấp trong Desk. Hỏi "vì sao phải xác nhận" -> `meta.effective_level` và `plan_summary`.

## Luồng confirmation_required

1. Gọi tool như bình thường (có `idempotency_key`).
2. Nhận `confirmation_required`, `details`: `confirm_url`, `confirmer.{ref,name}`, `plan_summary`, `expires_at` (TTL ~10 phút), `status` (`pending` | `awaiting_second_approver`), `channels`.
3. Nói với người dùng: ai phải bấm, làm gì (`plan_summary`), link, hạn. Ví dụ:
   > "Cần chị Nguyễn Thị Mai xác nhận: Duyệt bước 'Trưởng bộ phận' cho TU-2026-0042 (8.000.000 VND). Chị mở https://xt.example.vn/desk/confirm/cf_01jd30 (có xác thực OTP), hoặc bấm nút trên Telegram nếu đã liên kết. Hết hạn 10:15."
4. **Dừng.** Không mở link, không chạy trình duyệt, không đoán OTP, không gọi tool khác để "xác nhận hộ". Link chỉ có tác dụng với đúng người `confirmer` sau xác thực tươi.
5. Người báo "xong" (hoặc agent được nhắc sau một lúc): gọi lại **y hệt cùng args, cùng key** (hoặc `continuation`). Thành công sẽ có `meta.receipt.replayed: true`.
6. Vẫn `confirmation_required` -> chưa xác nhận, báo lại, đừng spam. `confirmation_declined` -> dừng, hỏi người dùng.
7. `awaiting_second_approver`: cần thêm người thứ hai; nói rõ ai (model change phá hủy, xóa PII).

Người xác nhận phải là người nêu trong `confirmer`. Nếu chính người dùng đang chat không phải `confirmer`,
nói họ chuyển link cho người đó; đừng đề nghị họ dùng tài khoản của người khác.

## Idempotency

- `idempotency_key` bắt buộc ở mọi tool ghi. Đặt có nghĩa: `<tool>-<đối tượng>-<số lần>`, ví dụ `create-adv-danang-1`.
- Retry hoặc gọi lại sau xác nhận: **cùng key, cùng args** -> server trả receipt cũ.
- Ý định mới hoặc sau khi sửa lỗi `rule_violation`: key mới.
- Cùng key khác args -> `idempotency_conflict`.

## CAS (`expected_revision`)

Bắt buộc ở `update_record`, `transition_record`, `decide_request`, `delete_record`. Lấy `revision` của bản ghi từ `get_record` ngay trước khi ghi (không phải `model_revision`, không đoán).
`revision_conflict` -> đọc lại, nếu thay đổi chạm tới ý của người dùng thì hỏi, rồi ghi lại với revision mới và **cùng key** (lần lỗi không giữ key). Không có `force`, không auto-merge.
`create_record`/`submit_request` không cần revision của model/process; server tự pin bản đang active và trả `model_revision`.
