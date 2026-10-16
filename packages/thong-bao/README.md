<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Thông báo nội bộ

Gói policy cho tool `notify` v1.0. Cài bằng `xt pack install --tenant <alias> packages/thong-bao`; manifest không thêm model hay quy trình. Cho nhân viên và agent đại diện gửi thông báo nội bộ, giao quyền thực tế vẫn kiểm cả hai phía. `notify` mức L2: agent dưới trần phải được người dùng xác nhận trên Desk. Người nhận và kênh Telegram được kiểm từ dữ liệu tenant; không có quyền gửi ra ngoài tổ chức.

Xem `docs/notifications.md` cho liên kết Telegram và worker. Không có duyệt qua Telegram.
