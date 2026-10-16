<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Thông báo và Telegram

`notify` là tool v1.0 (ngoài core-10): `recipients`, `message`, `subject_ref?`, `idempotency_key`. Cần policy IAM `notify.send` trên `xrn:<tenant>:notification:notification`. Agent dưới L2 nhận `confirmation_required`; người mở Desk để xác nhận. Cùng key và tham số trả receipt cũ. Người nhận phải đang hoạt động trong tenant; giới hạn 30 thông báo/người/phút.

Thông báo xuất hiện qua `get_inbox(kinds=["notification"])`. DB cô lập tenant và người nhận bằng RLS. Worker relay hiện có đọc assignment/escalation và pending confirmation, checkpoint cùng transaction ghi inbox. Nếu không bật Telegram, inbox vẫn hoạt động.

## Bật kênh Telegram

Trong `.env` riêng của deployment (không commit), đặt `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` ngẫu nhiên và `XT_DESK_URL` HTTPS. `deploy/compose.yaml` truyền cấu hình cho xt-api/xt-worker trong profile full. `xt-bot` chạy trong hai tiến trình này nên không cần container mới. Chạy migration và provision lại client confirm để thêm redirect URI cố định `/desk/telegram/callback`, rồi render lại APISIX.

Theo [setWebhook của Telegram](https://core.telegram.org/bots/api#setwebhook), đăng ký URL `https://<domain-api>/telegram/webhook`, `secret_token` bằng secret đã đặt, `allowed_updates=["message"]`. Không đưa token vào shell history/log; không chạy thao tác này trong test. API không tự đăng ký webhook khi khởi động.

Người dùng tạo liên kết từ Desk hoặc CLI: POST `/telegram/link-code` bằng token cá nhân cho audience MCP (allowlist mặc định `xt-cli,xt-desk-agent`; token DPoP phải có proof). Endpoint chỉ trả `authorize_url`, chưa trả link code. Mở URL trong cùng trình duyệt có cookie khởi tạo, đăng nhập tươi + OTP; trang Desk hiển thị `/link <code>`. Nhập lệnh đó trong private chat với bot trong 10 phút. Mã chỉ dùng một lần, DB chỉ giữ hash. `/start <code>` cũng được nhận. CLI cần mở luồng bằng trình duyệt, không có xác nhận CLI.

Bot chỉ xử lý liên kết và `/summary <notification-uuid hoặc mã-đối-chiếu>`. Chỉ người đã liên kết đúng chat riêng, còn hoạt động và là người nhận mới xem được. Group/forward/callback bị bỏ qua. Secret header không phải bằng chứng duyệt.

Tin ra Telegram luôn bỏ text tự do và tên người để tránh PII; chỉ gồm thông báo chung, mã opaque và link Desk đã kiểm origin/path. `pii_egress` được commit trước network. Tin duyệt có mã `confirmation_id#8-hex-digest`; Desk hiện cùng mã. Không có nút Duyệt/Từ chối trong Telegram.

## Kiểm tra và xử lý lỗi

`make check-notify` chạy lint/type/import/license + unit; `make test-notify-integration` dùng Postgres thật riêng và Telegram giả (MockTransport), tự dọn container/volume. Không dùng token Telegram thật cho test.

Outbox có `queued`, `sending`, `sent`, `failed`. Không có Telegram link thì vẫn `queued`, inbox đọc bình thường. Trước network, worker dành riêng delivery rồi commit audit. Khi timeout/crash không rõ bên Telegram đã nhận chưa, delivery không tự gửi lại; người vận hành đối soát trước khi retry thủ công. Điều này tránh tin trùng nhưng có thể mất push Telegram; inbox là bản thông báo bền. Không tự đổi trạng thái qua SQL khi chưa đối soát.
