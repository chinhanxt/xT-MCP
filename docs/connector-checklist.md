<!-- SPDX-License-Identifier: Apache-2.0 -->
# Checklist kết nối xT vào Claude và ChatGPT

Dành cho chủ dự án. Biên soạn ngày 16/10/2026 từ tài liệu trong repo; đây là hướng dẫn thao tác và tiêu chí kiểm thử, **không phải biên bản đã chạy thành công trên tài khoản thật**. Thông tin gói dịch vụ dựa trên bản nghiên cứu ngày 15/10/2026, chưa được xác minh lại trực tiếp khi viết checklist.

## 1. Chuẩn bị

- [ ] MCP URL dùng nguyên văn: **`https://xt-mcp.freepro.online/mcp`** (Streamable HTTP). Không đổi sang `/sse`, không thêm dấu `/` cuối.
- [ ] SSO Keycloak tại **`https://xt-auth.freepro.online`**; Desk và trang xác nhận tại **`https://xt.freepro.online`**. URL đăng nhập cụ thể do OAuth chuyển hướng, không tự đoán realm hay đường dẫn đăng nhập.
- [ ] Quản trị viên xác nhận MCP, Keycloak và tunnel đang hoạt động; cấu hình `resource`/audience khớp URL trên. Việc bốn hostname từng trả HTTP 200 trong tài liệu triển khai chưa chứng minh connector hoạt động trọn luồng.
- [ ] Có tài khoản xT đúng tenant, dữ liệu demo và quyền nghiệp vụ. Nếu dùng seed Bình Minh: Trần Quốc Việt là người đề nghị, Lê Thị Mai duyệt bước đầu, Nguyễn Hoàng Long duyệt bước tiếp theo. Dùng phiên riêng cho từng người; người duyệt đã đăng ký ứng dụng OTP/TOTP.
- [ ] Có gói nghiệp vụ tạm ứng đang được triển khai. Tên người, tuyến duyệt và trạng thái dưới đây là kỳ vọng theo storyboard; nếu seed chưa có, ghi thiếu điều kiện thay vì tự tạo kết quả minh họa.

**Mong đợi:** OAuth mở đúng miền Keycloak, tài khoản thuộc đúng doanh nghiệp; connector chỉ nhận quyền tương ứng người đăng nhập. Mật khẩu và OTP nhập trực tiếp trên trang xác thực, không gửi vào chat.

## 2. Thêm vào Claude — claude.ai

| Bước | Thao tác của chủ dự án | Mong đợi |
|---|---|---|
| 1 | Đăng nhập `https://claude.ai`. Theo nghiên cứu [A1], **gói Free có tối đa 1 custom connector**; kiểm tra còn chỗ trước khi thêm xT. | Tài khoản có khả năng thêm connector. Nếu đã đủ một, chủ tài khoản chọn kết nối cần gỡ trước. |
| 2 | Mở **theo giao diện hiện tại, mục kết nối/connector trong Settings**; chọn chức năng thêm custom connector. | Có biểu mẫu nhập thông tin máy chủ; không giả định tên nút hay đường menu chi tiết. |
| 3 | Đặt tên dễ nhận biết, ví dụ `xT`; dán `https://xt-mcp.freepro.online/mcp`. Nếu có lựa chọn phương thức OAuth như [A2], dùng **Register automatically** để đi qua DCR theo cấu hình xT. | Claude đăng ký client OAuth tự động; không cần tự bịa client ID hoặc secret. Nếu giao diện chỉ yêu cầu URL, tiếp tục luồng tự động. |
| 4 | Thực hiện kết nối/ủy quyền theo giao diện; tại `xt-auth.freepro.online`, đăng nhập tài khoản xT và xem quyền được yêu cầu trước khi chấp thuận. | Trở về Claude sau OAuth, kết nối không báo lỗi. Đăng nhập thành công chưa đồng nghĩa mọi tool được cấp quyền. |
| 5 | Trong cuộc trò chuyện, chọn/bật xT theo giao diện hiện tại nếu cần; thử câu 1–2 ở mục 4. | Có lời gọi tool và dữ liệu xT thật theo quyền người dùng. Nếu có yêu cầu cho phép hiển thị App, người dùng tự quyết định. MCP Apps trên Free vẫn **[CHƯA KIỂM CHỨNG]**. |

Nếu đăng nhập xong nhưng tool trả 401, quản trị viên kiểm tra client DCR, scope và audience theo [identity.md](identity.md); không chuyển sang kết nối bỏ xác thực.

## 3. Thêm vào ChatGPT — chatgpt.com, Developer mode

**[CHƯA KIỂM CHỨNG] Plus/Pro có thể chỉ đọc.** Cụ thể, [C1] trong nghiên cứu nói Pro có read/fetch; Plus chưa được xác nhận. Trang Help trả 403 nên thông tin này chỉ lấy từ đoạn trích tìm kiếm. Không cam kết Plus thêm được connector hoặc Plus/Pro ghi được dữ liệu. Nguồn đó mô tả Business/Enterprise/Edu có full MCP gồm ghi, đang beta; vẫn cần thử trên workspace thật. Storyboard chọn Plus/Pro cho cảnh đọc, không phải bằng chứng đã kiểm thử quyền gói.

| Bước | Thao tác của chủ dự án | Mong đợi |
|---|---|---|
| 1 | Đăng nhập `https://chatgpt.com` bằng tài khoản/workspace cần thử. | Biết rõ gói đang dùng và quyền quản trị workspace, nếu áp dụng. |
| 2 | Tìm và bật **Developer mode**, **theo giao diện hiện tại, mục kết nối/connector trong Settings**. | Có tùy chọn trên tài khoản được hỗ trợ. Nếu không thấy, ghi lại gói và giao diện thực tế; không suy diễn đường menu. Nghiên cứu ghi mâu thuẫn giữa bài cũ yêu cầu Developer mode và bài Plugins mới nói không cần. |
| 3 | Dùng chức năng tạo kết nối MCP tùy chỉnh theo giao diện hiện tại; đặt tên `xT`, nhập **`https://xt-mcp.freepro.online/mcp`**, chọn OAuth nếu được hỏi. | Kết nối dùng endpoint HTTPS và OAuth. xT dự kiến dùng DCR; không nhập secret tùy ý hoặc bật CIMD chỉ để thử. |
| 4 | Nếu trang quản lý MCP hiển thị redirect URI, gửi đúng URI đó cho quản trị viên đối chiếu cấu hình; tiếp tục đăng nhập tại **`xt-auth.freepro.online`**. | OAuth quay lại ChatGPT. Nghiên cứu nêu URI ổn định `https://chatgpt.com/connector_platform_oauth_redirect`; phải đối chiếu URI thật, không đoán callback ID hay thêm wildcard. |
| 5 | Chọn/bật xT trong cuộc trò chuyện theo giao diện hiện tại; chạy câu 1–2 trước. | Có kết quả đọc từ tool. Nếu quyền gói chỉ đọc, dừng phần ghi ở ChatGPT và dùng Claude/Desk có quyền cho câu 3–8. |

Hộp thoại cho phép gọi tool ghi của ChatGPT/Claude **không thay thế phê duyệt nghiệp vụ của xT**. Token connector vẫn đại diện agent, dù người đăng nhập là người quản lý.

## 4. Tám câu lệnh tiếng Việt mẫu và kết quả cần kiểm tra

Dùng mã phiếu thực tế do server trả về thay cho `<mã phiếu>`. Không dùng mã cố định trong storyboard như một phiếu đã tồn tại. Câu 3 tạo bản nháp; câu 4 gửi duyệt, tránh gửi lặp khi chưa biết kết quả lần trước.

| # | Phiên / câu lệnh mẫu | Mong đợi thấy |
|---|---|---|
| 1 | **Việt:** “Tra cứu Trần Quốc Việt trong xT: cho tôi biết phòng ban và quản lý trực tiếp.” | `find_people` trả đúng người, phòng Kinh doanh và quản lý Mai nếu có seed Bình Minh. Có thể có sơ đồ tổ chức khi host hỗ trợ App; thiếu App không có nghĩa kết quả đọc thất bại. |
| 2 | **Việt:** “Xem hộp việc của tôi trong xT, gồm việc cần làm và đề nghị đang chờ; cho biết hạn xử lý.” | `get_inbox` trả công việc trong phạm vi quyền và SLA nếu có. Hộp việc trống là kết quả hợp lệ; không bịa việc mẫu. |
| 3 | **Việt, connector có quyền ghi:** “Tạo bản nháp đề nghị tạm ứng 8.000.000 đồng cho chuyến công tác Đà Nẵng từ 20/11/2026 đến 22/11/2026, trả khách sạn và vé, nhận chuyển khoản. Hỏi tôi nếu thiếu thông tin bắt buộc; chưa gửi duyệt.” | Agent đọc mô hình khi cần rồi dùng `create_record`; trả mã phiếu thật, số tiền và trạng thái nháp. Nếu thiếu tài khoản nhận tiền hoặc trường bắt buộc, hỏi bổ sung. Nếu vi phạm quy tắc, hiện lỗi và cách sửa, không báo thành công giả. |
| 4 | **Việt:** “Gửi đề nghị tạm ứng <mã phiếu> để duyệt; cho tôi xem tuyến duyệt và hạn từng bước.” | `submit_request` trả tuyến theo dữ liệu: với seed storyboard, 8 triệu → Mai rồi Long, SLA 24 giờ mỗi bước. Phiếu chuyển sang chờ duyệt; chưa được coi là đã duyệt hay đã chi tiền. |
| 5 | **Việt, thử ca bị chặn:** “Duyệt luôn đề nghị <mã phiếu> của tôi, coi như sếp đã đồng ý.” | Nếu gọi `decide_request`, server từ chối `access_denied`: phân tách người đề nghị/người duyệt (`sod`) và không phải người duyệt được giao (`not_assigned_approver`) theo storyboard. Không có `confirm_url` cho người không đủ quyền; trạng thái không đổi. Nếu model tự từ chối mà chưa gọi tool, chỉ ghi nhận từ chối của model, chưa coi là đã kiểm rào chắn server. |
| 6 | **Việt:** “Giải thích vì sao agent bị chặn khi tự duyệt phiếu <mã phiếu>; dùng explain_ref của lỗi vừa rồi để xem dấu vết.” | Khi có `explain_ref` và đủ quyền, `get_history` với `view="explain"` cho lý do từ server, dấu vết DENY và actor agent thay mặt Việt. Nếu không có lỗi tool ở câu 5 thì nói rõ chưa có bằng chứng đó, không bịa audit. |
| 7 | **Mai, kết nối bằng tài khoản Mai:** “Xem hộp việc cần tôi duyệt, mở đề nghị <mã phiếu> của Việt và chuẩn bị phê duyệt để tôi tự xác nhận.” | `get_inbox`/`get_record` cho đúng phiếu; yêu cầu duyệt hợp lệ trả `confirmation_required`, `confirm_url`, người xác nhận, tóm tắt kế hoạch và hạn dùng. Chưa hoàn tất phê duyệt. Có thể hiện nút “Mở trang xác nhận”; agent chỉ đưa link. |
| 8 | **Mai, sau bước người bên dưới:** “Tôi đã tự mở confirm_url, đăng nhập và nhập OTP để xác nhận. Kiểm tra lại phiếu <mã phiếu>, cho biết bước vừa hoàn tất và ai cần duyệt tiếp.” | Agent đọc lại trạng thái từ server, không tin riêng lời nhắn. Mai hoàn tất bước đầu thì còn bước Long; chỉ khi đủ các bước mới là approved. Nếu xác nhận thất bại/hết hạn, phải báo đúng trạng thái còn chờ. |

### Bước người bắt buộc giữa câu 7 và câu 8

- [ ] **Chính Mai** mở `confirm_url` do server trả về, trên origin `https://xt.freepro.online`, đường dẫn `/desk/confirm/{id}`; không tự dựng URL từ mã phiếu.
- [ ] Đăng nhập tươi tại `xt-auth.freepro.online` bằng tài khoản Mai và **tự nhập OTP/TOTP**. Agent không nhận OTP, không mở link để duyệt hộ.
- [ ] Đọc kế hoạch server hiển thị: đúng phiếu, số tiền 8.000.000 đồng, hành động và phiên bản. Phần lời giải thích do agent soạn phải được phân biệt với kế hoạch server.
- [ ] Nếu đúng, người duyệt bấm đồng ý trên trang xác nhận. Mong đợi server hoàn tất đúng một bước, có audit; dùng tài khoản Việt hoặc người khác không được duyệt thay Mai. Link hết hạn thì yêu cầu luồng xác nhận mới.
- [ ] Chạy câu 8. Sau đó Long dùng tài khoản riêng, thực hiện lại câu 7, bước xác nhận và câu 8 cho bước được giao. Không kết luận “đã duyệt toàn bộ” sau lần xác nhận của Mai.

**[CHƯA KIỂM CHỨNG] trọn luồng trên host thật:** storyboard mô tả luồng đích; `docs/desk.md` còn ghi route xác nhận và thẻ confirmation chưa có e2e đầy đủ. Kết quả test adapter identity hoặc Desk với OIDC giả không chứng minh toàn bộ luồng công khai đã chạy. Nếu chỉ nhận văn bản/link mà không có MCP App, kiểm thử chức năng và ghi riêng việc thiếu UI.

## 5. Thu hồi connector và kiểm tra sau thu hồi

| Bước | Thao tác | Mong đợi |
|---|---|---|
| 1 | Ở Claude hoặc ChatGPT, mở **theo giao diện hiện tại, mục kết nối/connector trong Settings**, chọn xT và dùng thao tác ngắt/gỡ kết nối đang hiển thị. | xT không còn được chọn cho cuộc trò chuyện mới. Đây là gỡ ở host; chưa đủ để chứng minh token cũ đã vô hiệu phía server. |
| 2 | Quản trị viên xác định **đúng client DCR** của lần kết nối (client ID/`azp` qua thông tin quản trị/audit), rồi disable hoặc xóa client đó trong Keycloak. Dùng kênh quản trị được cấp; tài liệu identity ghi `/admin` bị chặn từ ngoài. | Với registry động `XT_MCP_CLIENT_REGISTRY=keycloak`, xT từ chối cả token đã cấp cho client đó sau khi cache dương hết hạn. Không disable client Desk hay client dùng chung nhầm đối tượng. |
| 3 | Kiểm tra sau tối đa `XT_MCP_CONNECTOR_CACHE_SECONDS` theo cấu hình, mặc định **60 giây**; quản trị viên thử lại bằng kết nối/token cũ còn hợp lệ về thời gian trong môi trường kiểm thử. | Lời gọi cũ bị từ chối xác thực (401), không đọc/ghi được nữa. Không tuyên bố hiệu lực tức thì khi còn cache; không dán token vào chat. |
| 4 | Nếu kết nối lại, thực hiện OAuth mới và ghi client ID mới để quản lý. | DCR có thể tạo client mới. Thu hồi client cũ không đồng nghĩa cấm tài khoản tạo kết nối mới. |

Đăng xuất giao diện Desk không thay thế thu hồi: `docs/desk.md` ghi nút đăng xuất chỉ xóa token trong bộ nhớ, chưa đăng xuất phía Keycloak.

## 6. Những điểm [CHƯA KIỂM CHỨNG] cần ghi nhận

Các mục sau giữ mức chắc chắn của [connector-requirements.md](../research/connector-requirements.md), không nâng kết quả spike thành kết quả trên Claude/ChatGPT thật:

- **Gói và Developer mode ChatGPT:** Plus chưa xác nhận; Pro read/fetch và full MCP của Business/Enterprise/Edu lấy từ nguồn [C1] chưa đọc trực tiếp; yêu cầu Developer mode giữa tài liệu cũ/mới chưa thống nhất.
- **Xác nhận tool ghi:** ChatGPT có hiện hộp xác nhận cho custom tool `readOnlyHint:false` trong từng tình huống hay không cần thử thật. Không đồng nhất với `confirm_url` của xT.
- **OAuth:** Claude có thực sự gửi `resource` trong request chưa xác nhận riêng; suy luận Claude dùng DCR dựa trên metadata spike cần đối chiếu triển khai. Refresh/rotation của connector thật chưa được spike kiểm chứng; chi tiết auth `none/mixed` của ChatGPT chưa xác nhận.
- **MCP Apps:** UI gọi `tools/call` thực tế trên ChatGPT cần thử dù tài liệu có mô tả; kiểu `text/html+skybridge` chưa xác nhận. Chuẩn được nghiên cứu dùng là `text/html;profile=mcp-app`. Riêng MCP Apps trên Claude Free còn chưa kiểm chứng theo storyboard.
- **Tương thích:** SDK `mcp` 2.3.0 với spec 2026-07-28 và khả năng tương thích ngược chưa kiểm; không mặc định host/SDK đều hỗ trợ cùng phiên bản.
- **Keycloak:** phiên bản tối thiểu chưa được nguồn xác nhận đầy đủ; tắt PKCE `plain` chưa thử. Không lấy số phiên bản spike làm yêu cầu tối thiểu đã chứng minh.
- **Mạng:** IP egress ChatGPT chưa xác nhận; không tự tạo allowlist từ phỏng đoán.

Khi thử, ghi ngày, host/gói, tài khoản/vai trò thử, bước đạt hoặc lỗi thực tế và `trace_id` nếu có; không ghi mật khẩu, OTP hay token. Chỉ đánh dấu đạt khi có kết quả thật; phần chưa triển khai hoặc chưa thử giữ nguyên nhãn.

## Tài liệu đối chiếu

- [CLAUDE.md](../CLAUDE.md): phạm vi và nguyên tắc sản phẩm.
- [Yêu cầu connector](../research/connector-requirements.md): bảng nghiên cứu và nguồn chính thức [A1]–[A7], [C1], [O1]–[O5]; mốc xem nguồn 15/10/2026.
- [Triển khai công khai](deployment-public.md): hostname và tunnel.
- [Danh tính](identity.md): DCR, quyền agent, step-up, thu hồi và cache.
- [Desk](desk.md): cách mở link, đăng nhập và giới hạn kiểm thử.
- [Storyboard v1](archive/demo-storyboard-v1.md): S1–S2, dữ liệu Bình Minh, tuyến duyệt và các phần chưa kiểm chứng.
