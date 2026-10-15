# Chính sách bảo mật

## Phiên bản được hỗ trợ

xT đang phát triển, chưa có bản phát hành hoặc phiên bản được hỗ trợ bảo mật. Chính sách hỗ trợ sẽ cập nhật trước bản phát hành đầu tiên.

## Báo cáo lỗ hổng

Không đăng chi tiết khai thác, thông tin xác thực hoặc dữ liệu nhạy cảm lên Issues/PR công khai. Nếu repo GitHub đã bật báo cáo riêng tư, dùng **Security → Advisories → Report a vulnerability**.

Kênh liên hệ bảo mật riêng chưa được công bố. Nếu chức năng trên chưa có, chỉ mở Issue yêu cầu maintainer cung cấp kênh riêng, không kèm chi tiết lỗ hổng; chờ kênh riêng trước khi gửi bằng chứng.

Báo cáo riêng nên nêu commit/phiên bản, thành phần ảnh hưởng, tác động và bước tái hiện tối thiểu; loại bỏ dữ liệu nhạy cảm khỏi bằng chứng. Maintainer sẽ phối hợp xác minh, khắc phục và thống nhất thời điểm công bố. Hiện chưa cam kết thời hạn phản hồi.

## Rủi ro đã biết (chấp nhận có điều kiện ở v0.1)

- **Xem PII bằng access token của Desk ngoài trình duyệt.** Kênh người `/pii/reveal` gắn vé với khóa P-256 do khung Desk sinh và kiểm lại quyền đọc bản ghi của người xem lúc đổi vé. Tuy vậy, ai cầm được access token `xt-desk-agent` của một người dùng (ngoài trình duyệt) vẫn tự sinh khóa và xem được giá trị thật của các bản ghi **trong phạm vi người dùng đó được đọc**; không vượt quyền đọc. Biện pháp tạm thời: production không có redirect loopback cho `xt-desk-agent`, CSP chặt ở Desk, quota vé theo người và rate limit theo IP ở gateway, audit mọi lần xin/đổi vé (`xt_meta.pii_egress`).
- **Khắc phục:** DPoP (RFC 9449) cho `xt-desk-agent`, token gắn `cnf.jkt`, vé PII gắn `jkt`, trước khi tag v1.0.0 (`docs/decisions.md` "DPoP cho Desk trước v1.0"; `docs/audit-pii-reveal.md`).
