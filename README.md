# xT — Agent-native DX-OS

**Trạng thái: đang phát triển.** Repo hiện có khung tài liệu, chưa có mã nguồn sản phẩm hay bản phát hành chạy được.

## Giới thiệu

xT hướng tới nền tảng vận hành số đa năng cho doanh nghiệp: lõi headless được cung cấp qua Model Context Protocol (MCP), agent là giao diện tương tác. Nghiệp vụ là dữ liệu; model và process do agent đề xuất, con người duyệt trước khi áp dụng.

Kiến trúc dự kiến gồm Tầng 1 với SSO, API Gateway, Data và Workflow; Tầng 2 có ứng dụng minh họa bằng MCP Apps trong chat. Ngôn ngữ lõi là Python. Các nghiệp vụ hướng tới gồm đề xuất–phê duyệt, giao việc và SLA, nhân sự, thu–chi, bán hàng, văn bản và helpdesk.

## Giấy phép

xT chọn **Apache-2.0** nhằm cho phép cộng đồng và doanh nghiệp sử dụng, sửa đổi, phân phối và đóng góp vào nền tảng nguồn mở, kể cả trong sản phẩm thương mại, theo điều kiện giấy phép. Lý do chọn là khả năng tái sử dụng rộng rãi và điều khoản cấp phép bằng sáng chế rõ ràng.

Xem toàn văn [LICENSE](LICENSE) và [NOTICE](NOTICE). Phụ thuộc phải có giấy phép OSI và được rà soát tương thích; danh mục nằm tại [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Yêu cầu hệ thống

Chưa chốt phiên bản Python, hệ điều hành hỗ trợ hoặc cấu hình CPU/RAM/dung lượng. Yêu cầu cụ thể sẽ cập nhật cùng mã nguồn và được kiểm chứng bằng cài đặt thực tế.

## Build/cài đặt từ mã nguồn

Sẽ cập nhật khi có mã nguồn triển khai.

## Báo lỗi

Dùng tab **Issues** của repo GitHub, chọn mẫu **Báo lỗi** và cung cấp môi trường, bước tái hiện, kết quả mong đợi và thực tế. Đề xuất tính năng dùng mẫu tương ứng. Báo cáo lỗ hổng theo [SECURITY.md](SECURITY.md); không đăng bí mật hay dữ liệu cá nhân.

## Đóng góp

Xem [CONTRIBUTING.md](CONTRIBUTING.md) về SPDX, kiểm tra và DCO sign-off; tuân thủ [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Thay đổi được ghi tại [CHANGELOG.md](CHANGELOG.md).
