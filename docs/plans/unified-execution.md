<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->

# Nhật ký thực thi kế hoạch hợp nhất

## Thay module 3D theo yêu cầu mới

Chủ dự án yêu cầu thay `imports/waretrack` bằng `imports/waretrack-3d` và xóa thư mục cũ. Build/test chung đã chuyển sang bản mới, giữ URL `/waretrack/`. Các kết quả test mô phỏng của bản WareTrack cũ không áp dụng cho WareTrack 3D; bản mới chưa có test script. Mục tiêu hợp nhất tổng thể đang tạm dừng, lần thay module này là yêu cầu riêng.

Kiểm chứng lần thay module: `make unified-build` thành công; `make unified-test` có 48 test Studio và 167 test Desk pass, WareTrack 3D production build pass. Browser headless mở `/waretrack/` trả HTTP 200, tạo canvas và giao diện điều hành, không có JavaScript page error. Thư mục cũ đã xóa; bundle tại `desk/dist/waretrack` khớp bản build WareTrack 3D.

## Quyết định phạm vi và quyền thực hiện

- Căn cứ: chủ dự án yêu cầu **thực thi** kế hoạch `2026-10-17-unified-xt-studio-waretrack.md`.
- Quyết định: cho phép triển khai và kiểm thử local G0–G7; không commit/push/release hoặc thay stack public trong bước này.
- Main giữ thay đổi đã tồn tại, chỉ tích hợp các đường dẫn agent bàn giao. Ba worktree riêng: `xt-unified-core-20261017`, `xt-unified-studio-20261017`, `xt-unified-portal-20261017` dưới `~/.cache/codex-worktrees`.
- API/proxy/build/deploy template/tài liệu do điều phối quản lý; lõi kho, Studio và portal do ba writer riêng quản lý.
- Các agent đầu phiên dừng do quota; đã xác nhận không còn agent chạy trước khi giao lại worktree. Không coi kết quả chưa trả về là đã hoàn tất.

## Hợp đồng tích hợp đã chọn

- Studio được phục vụ tại `/studio/`, WareTrack tại `/waretrack/` trong bản build cổng. Đây là module tin cậy cùng origin; HTML từ MCP vẫn ở sandbox origin riêng.
- Browser bridge trả `Response` để giữ streaming, dùng phiên xT và DPoP cho từng request `/studio-api/*`. Đăng xuất hủy request đang chạy.
- Python xác minh token, client, tenant, trạng thái thu hồi và người đang hoạt động; chuyển tiếp tới upstream Studio cố định.
- Không chuyển cookie, token người dùng, DPoP hoặc header danh tính do browser tự gửi sang Studio.
- Assertion nội bộ ký HMAC SHA256 với secret file chung (ít nhất 32 byte), ràng tenant/person/roles/method/path/body hash/thời gian/nonce. Node từ chối giả mạo và replay.
- Quyền Studio: `studio_admin`/`xt_admin`/`director` quản trị; `studio_editor` biên tập; `studio_user` dùng ứng dụng đã publish. Quyền lấy từ dữ liệu người dùng xT, không từ request body.
- Workspace cũ không tự nhận tenant mới. Public share/API key của chế độ độc lập chưa được bật trong chế độ tích hợp.
- Kho: số lượng nguyên theo phần nghìn đơn vị; phiếu gồm các dòng cùng transaction. Terminal state `approved` nghĩa là đã ghi nhận, nếu ghi kho lỗi thì phê duyệt phải rollback. Người tạo không tự duyệt.
- Ràng buộc kho: không âm, không ghi trùng, dữ liệu hệ thống balance/movement không được ghi qua công cụ generic; đảo/điều chỉnh có lịch sử riêng.

## Bằng chứng hiện có

- Python: 94 test pass cho proxy Studio mới và baseline desk relay/model spec/pack ops; chưa phải test liên thông Node hay SSO thật.
- Proxy mới đã qua Ruff và mypy riêng.
- Build/test chung đang được triển khai. Các test của nhánh chưa tích hợp không dùng làm bằng chứng hoàn tất toàn hệ thống.

## Tiến độ

| Chặng | Trạng thái | Còn lại |
|---|---|---|
| G0 | Đang làm | Ma trận endpoint/role đầy đủ, provenance WareTrack và snapshot cuối |
| G1 | Đang làm | Build chung, launcher local, kiểm thử route và smoke tầng 1 |
| G2 | Đang làm | Tích hợp proxy/Studio, kiểm thử replay đồng thời, viewer UI, mapping legacy |
| G3 | Đang làm | Form/contract đúng, xác nhận thao tác, lựa chọn danh mục thân thiện |
| G4–G7 | Chưa nghiệm thu | Giao dịch kho, báo cáo, AI, e2e, backup/restore và hồ sơ bàn giao |

Không có chặng nào được đánh dấu hoàn tất chỉ từ việc tồn tại mã nguồn.
