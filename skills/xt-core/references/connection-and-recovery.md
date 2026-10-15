<!-- SPDX-License-Identifier: Apache-2.0 -->
# Kết nối và phục hồi

Chỉ đọc khi không có tool xT hoặc có lỗi về phiên. Mỗi triệu chứng một cách chữa; đừng lặp cả chuỗi.

| Quan sát | Ý nghĩa | Bước tiếp |
|---|---|---|
| Không có tool nào tên `inspect_workspace`, `search_records`... | Chưa đăng ký connector xT ở client này, hoặc chưa tải lại | Nói người dùng thêm xT MCP server (URL Streamable HTTP của tenant, đăng nhập SSO) và mở phiên mới. Không tự cài gì khác |
| Tool có nhưng `unauthenticated` | Token hết hạn/chưa đăng nhập | Người dùng đăng nhập lại; sau đó thử lại cùng args/key |
| `access_denied` ngay ở `inspect_workspace` | Tài khoản chưa có quyền vào tenant | Báo quản trị cấp quyền; gửi `trace_id` |
| `inspect_workspace` không thấy model người dùng nói | Sai workspace/tenant, hoặc model chưa được duyệt | Hỏi lại tên nghiệp vụ; thử `search_records {query}`; nếu là nghiệp vụ mới, chuyển sang skill `xt-data-modeling` |
| `capability_unavailable` | Tính năng chưa có ở bản/tenant (vd `upload_file`, `aggregate_records` ở v0.1) | Đọc `remediation`, nói rõ giới hạn, đề xuất cách khác; không giả thành công |
| `confirm_url` trỏ `localhost`/máy cục bộ | Chế độ stdio, Desk chạy cục bộ | Vẫn đưa link cho người dùng mở trên máy đó |
| `timeout` / mạng đứt giữa lúc ghi | Có thể đã commit | Gọi lại cùng key (replay an toàn), hoặc `get_record` kiểm tra |

`inspect_workspace` trả danh sách tool khả dụng và `limits` (retention idempotency, kích thước upload). Dùng nó khi cần biết giới hạn thật, không đoán.
