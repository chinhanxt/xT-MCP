<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Thông báo (catalog full v1.0)

Khi người dùng nhờ nhắc đồng nghiệp: tìm người bằng `find_people`, rồi gọi `notify` với `recipients` là person refs, `message` tối đa 1000 ký tự, `subject_ref` nếu có và `idempotency_key` mới. Không truyền chat_id, kênh gửi, địa chỉ ngoài tổ chức hay cờ duyệt. Không có `notify` trong core-10: chỉ dùng khi host thực sự công bố tool.

Tool cần IAM `notify.send`, mức L2. Khi nhận `confirmation_required`, chuyển nguyên link Desk cho người dùng. Sau xác nhận, gọi lại nguyên tham số cùng key. Không dùng Telegram callback hay tin nhắn làm bằng chứng duyệt.

`get_inbox(kinds=["notification"])` đọc thông báo của chính người dùng. Telegram chỉ hiện tóm tắt chung + mã đối chiếu + link; nội dung đầy đủ ở Desk. Không hứa Telegram đã gửi khi trạng thái chỉ là `queued`.

Liên kết Telegram là việc người dùng tự làm qua Desk/CLI và đăng nhập tươi + OTP. Không xin, tạo hộ, lưu hay gửi link code tới người khác. Xem `docs/notifications.md` cho người vận hành.
