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

- 18/10/2026 00:27: `make e2e` trên `staging` (commit c453771 + docs) với Keycloak/APISIX/PostgreSQL/SeaweedFS thật: 24 passed (E0–E11, gồm E09 đăng nhập OIDC Desk trong container và E10 security smoke). Stack tự dọn.
- 17/10/2026: toàn bộ integration suite (gồm r3) trên `staging` a74be4c: 563 passed; 4 lỗi còn lại đã sửa (#81 r3 inventory; dpop do thiếu node_modules) và chạy lại pass.
- Browser e2e trên bản build hợp nhất (unified-preview, backend giả lập): 5/5 passed (#68).

Điều phối đã chạy trên nhánh `staging` (17/10/2026), trên PostgreSQL thật:

- `test_warehouse.py` 8/8 (gồm tổng hợp nhập/xuất theo kho và theo tháng).
- `test_app_lifecycle.py` (U15) 2/2.
- `test_role_grants_pg.py` 1/1.
- `test_pack_ops.py` 14/14.
- `test_backup_restore.py` 1/1 với client PG17.
- Bộ audit: r4 29/29, audit_meta 6/6, r3 upgrade-path 1/1.

Test không cần stack: unit Python 2000 passed; Desk vitest 235, Studio 65, WareTrack 37.

**Chưa chạy:** ChatGPT thật trên bản hợp nhất; nghiệm thu đổi doanh nghiệp (#7) trên Keycloak thật với ≥2 tổ chức; deploy public; neo lại checkpoint audit v2 khi deploy. Vì vậy chưa chặng nào được nghiệm thu đầy đủ.

## Tiến độ

| Chặng | Trạng thái | Đã merge vào staging | Còn lại |
|---|---|---|---|
| G0 | Đã merge | Ma trận quyền endpoint/tool × vai trò, provenance WareTrack, quyết định D-UNI-*, sơ đồ kiến trúc (#37, #11) | Không còn mục mở đã biết |
| G1 | Đã merge, chưa e2e | Build/test chung, `unified-smoke.mjs` headless với bằng chứng JSON, backup/restore bản tích hợp (#42, #30) | Playwright e2e cổng hợp nhất; deploy public (chưa chạy) |
| G2 | Đã merge, chưa SSO thật | Proxy Studio có correlation request_id, replay song song và thu hồi giữa chừng, công cụ mapping workspace cũ, runs list ẩn dữ liệu người khác, vai trò hiệu lực từ access_grant (#28, #19, #32, #49, #45, #46) | Đổi tenant qua gợi ý org (#7); UI quản lý thành viên H dùng luồng access_grant; Keycloak SSO thật trên bản hợp nhất; PostgREST `/data` chưa áp dụng ứng dụng bị tắt và `person_role_grant` |
| G3 | Đã merge, chưa e2e | Shell H/P/D/I: trạng thái cấm/hết hạn, danh sách tài liệu H, phòng ban từ `org_unit`, form P từ `describe_model`, báo cáo D có as-of/lineage/bản ghi nguồn, điều hướng quay lại, bridge theo từng frame (#15, #23, #29, #34, #36, #38, #47, #50) | UI quản lý thành viên H; Playwright e2e |
| G4 | Đã merge | Kho: biến động gắn ref kho, phiếu đảo qua duyệt, `stock_level`, sửa tồn và backfill sổ cũ, WareTrack chế độ Mô phỏng/Dữ liệu nghiệp vụ chỉ đọc (#27, #22, #33, #54, #58, #26, #31) | Không còn mục mở đã biết |
| G5 | Đã merge phần lõi | `aggregate_records` với `doc_date`, báo cáo D (#54, #34) | `get_history` chưa giải thích `lineage_ref` |
| G6 | Đã merge, chưa live | RAG kiểm lại từng nguồn xT dưới quyền người gọi, fail-closed khi tài liệu không có nguồn xT (#39, #61); `inspect_workspace` trả `me.roles` (#66) | ChatGPT thật trên bản hợp nhất |
| G7 | Một phần | Tài liệu vận hành, backup/restore PG17, checkpoint audit ổn định sau restore, mã commit nền r3/r4 (#40, #30, #44, #62, #63) | Neo lại checkpoint audit v2 khi deploy; e2e, SSO thật, ChatGPT thật, deploy public |
| G8 | Đã merge | Manifest ứng dụng và `inspect_workspace` apps, tắt/bật lại ứng dụng theo tenant qua đề xuất + duyệt (U15), launcher 4 nhóm và màn hình ứng dụng chung, ứng dụng mẫu: Kho (nhóm 1), Chấm công (nhóm 2), CRM (nhóm 3), Tài sản và bảo trì (nhóm 4) (#59, #60, #55, #56, #51, #52, #53, #64) | PostgREST `/data` chưa áp dụng ứng dụng bị tắt và `person_role_grant`; tiến trình đang chạy cần thử lại sau khi bật lại; e2e trình duyệt |

Không có chặng nào được đánh dấu hoàn tất chỉ từ việc tồn tại mã nguồn. "Đã merge" nghĩa là mã đã vào `staging` và qua các test ở mục bằng chứng, chưa phải nghiệm thu e2e.
