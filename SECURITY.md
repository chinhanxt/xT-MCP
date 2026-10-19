# Chính sách bảo mật

## Phiên bản được hỗ trợ

xT đang phát triển, chưa có bản phát hành hoặc phiên bản được hỗ trợ bảo mật. Chính sách hỗ trợ sẽ cập nhật trước bản phát hành đầu tiên.

## Báo cáo lỗ hổng

Không đăng chi tiết khai thác, thông tin xác thực hoặc dữ liệu nhạy cảm lên Issues/PR công khai. Nếu repo GitHub đã bật báo cáo riêng tư, dùng **Security → Advisories → Report a vulnerability**.

Kênh liên hệ bảo mật riêng chưa được công bố. Nếu chức năng trên chưa có, chỉ mở Issue yêu cầu maintainer cung cấp kênh riêng, không kèm chi tiết lỗ hổng; chờ kênh riêng trước khi gửi bằng chứng.

Báo cáo riêng nên nêu commit/phiên bản, thành phần ảnh hưởng, tác động và bước tái hiện tối thiểu; loại bỏ dữ liệu nhạy cảm khỏi bằng chứng. Maintainer sẽ phối hợp xác minh, khắc phục và thống nhất thời điểm công bố. Hiện chưa cam kết thời hạn phản hồi.

## Rủi ro đã biết (chấp nhận có điều kiện ở v0.1; mục DPoP đã đóng ở cấu hình triển khai mặc định)

- **Xem PII bằng access token của Desk ngoài trình duyệt.** Kênh người `/pii/reveal` gắn vé với khóa P-256 do khung Desk sinh và kiểm lại quyền đọc bản ghi của người xem lúc đổi vé. Tuy vậy, ai cầm được access token `xt-desk-agent` của một người dùng (ngoài trình duyệt) vẫn tự sinh khóa và xem được giá trị thật của các bản ghi **trong phạm vi người dùng đó được đọc**; không vượt quyền đọc. Biện pháp tạm thời: production không có redirect loopback cho `xt-desk-agent`, CSP chặt ở Desk, quota vé theo người và rate limit theo IP ở gateway, audit mọi lần xin/đổi vé (`xt_meta.pii_egress`).
- **Khắc phục (đã có, bật bằng cờ):** DPoP (RFC 9449) cho `xt-desk-agent` (`docs/decisions.md` "DPoP cho Desk trước v1.0"; `docs/identity.md` mục "DPoP"). Desk giữ khóa ECDSA P-256 non-extractable trong trình duyệt; Keycloak gắn token vào khóa (`cnf.jkt`, refresh token cũng gắn); `/mcp` đòi proof đúng khóa cho token gắn khóa; vé PII gắn `jkt` và `/pii/reveal` phải có proof ký bằng chính khóa đó (không nhận `pubkey_jwk` tùy ý).
  - **Triển khai mặc định `XT_DPOP_REQUIRED=true`** (`deploy/compose.yaml`, `.env.example`, `provision.py` → `dpop.bound.access.tokens=true`; quyết định chủ dự án sau audit DPoP vòng 2): Keycloak không cấp token không gắn khóa cho `xt-desk-agent`, xt-api từ chối Bearer của client này và vé không gắn DPoP. Rủi ro "người cầm token" ở trên **đã đóng**: access token/refresh token/vé bị trích khỏi trình duyệt vô dụng nếu thiếu khóa riêng. Còn lại: mã chạy **trong** trang Desk (XSS) không trích được khóa nhưng ký được proof bằng khóa, kể cả **ký sẵn** proof với `iat` tương lai rồi mang đi dùng sau khi đóng tab (không có `DPoP-Nonce`). Giới hạn là `ath`: mỗi proof `/mcp` ràng vào đúng access token lúc ký, nên giá trị tồn dư kéo dài tới `exp` của token đó (realm 300 s, trần `XT_IDP_MAX_TOKEN_LIFETIME_SECONDS` 900 s). Proof ký sẵn cho token endpoint cùng refresh token lấy được token mới nhưng không có proof `/mcp` cho `ath` của token mới, nên không duy trì được truy cập (chỉ phá phiên nhờ refresh rotation). Vé PII: `ath` = hash vé chưa biết trước, TTL 60 s. CSP chặt vẫn là lớp chính (`docs/archive/audit-dpop.md` DPA-05).
  - Chỉ đặt `XT_DPOP_REQUIRED=false` (tắt tường minh) mới mở lại rủi ro: token Bearer `xt-desk-agent` không gắn khóa và vé khóa khung được nhận lại.
  - Cache phát lại jti dùng chung trên Postgres (`XT_DPOP_REPLAY_STORE=postgres`, bảng `xt_identity.dpop_jti`), sống qua restart và nhiều worker; DB lỗi thì 503 (fail-closed). Đăng xuất Desk xóa khóa DPoP khỏi IndexedDB và kết thúc phiên SSO Keycloak (DPA-02, DPA-03).
