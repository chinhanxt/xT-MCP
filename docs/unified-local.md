<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->

# Chạy bản tích hợp xT, Studio và WareTrack trên máy phát triển

Bản tích hợp dùng xT làm lõi, Studio cho AI và `imports/waretrack-3d` cho mô phỏng kho tại `/waretrack/`. Dữ liệu doanh nghiệp và quyền nghiệp vụ đi qua xT; module 3D hiện dùng dữ liệu giả lập.

## Điều kiện

- Python/uv theo `pyproject.toml`, Node >=24, dependency từ ba lockfile hiện có.
- PostgreSQL và Keycloak xT đã được provision, truy cập được từ host. Launcher không tạo tenant, không tự cấp quyền và không seed database đang có.
- Client `xt-desk-agent` cho phép redirect `http://localhost:5173/auth/callback` và origin `http://localhost:5173` trong môi trường local; nếu đổi cổng phải cập nhật client tương ứng.
- Runtime env riêng, quyền file 0600, chứa cấu hình xT hợp lệ (DB role `xt_app`, issuer/JWKS, audience/client/scopes, introspection, khóa receipt/cursor/PII và cấu hình confirmation nếu dùng phê duyệt).
- Không lấy URI Docker như `postgres:5432` cho tiến trình host khi host không phân giải được tên đó. Dùng endpoint host đã provision riêng.

## Lệnh chung

```bash
cd /home/chinhan/xT
make unified-setup
make unified-build
make unified-test
XT_UNIFIED_ENV_FILE="$HOME/.config/xt/unified.env" make unified-start
```

`unified-start` build lại bằng đúng cấu hình runtime rồi khởi động ba tiến trình: core loopback 8010, Studio loopback 4327 và portal `http://localhost:5173`. Dừng bằng Ctrl+C. Cổng đã có dịch vụ sẽ làm launcher dừng trước khi khởi động; không dừng tiến trình khác.

File runtime có thể đặt `XT_UNIFIED_CORE_PORT`, `XT_UNIFIED_STUDIO_PORT`, `DESK_PORT`, `DESK_OIDC_ISSUER`, `DESK_OIDC_CLIENT_ID`, `DESK_OIDC_SCOPE`, `XT_DESK_LLM_URL`, `DESK_LLM_MODEL`. Xem [cấu hình Desk](../desk/.env.example) cho tên biến và [danh tính](identity.md) cho cấu hình core. Launcher luôn phục vụ local, không sửa APISIX hoặc tunnel công khai.

Dữ liệu AI mặc định ở `~/.local/state/xt-unified/studio`; secret ký nội bộ ở `~/.local/state/xt-unified/studio.key`. Đổi gốc bằng `XT_UNIFIED_STATE_DIR`; đổi secret bằng `XT_STUDIO_SECRET_FILE`. Giữ dữ liệu Studio độc lập khỏi `.data` của bản cũ; mapping legacy phải do người vận hành chọn rõ ràng.

## Quyền và nghiệp vụ

- Studio: `studio_admin`/`xt_admin`/`director` quản trị; `studio_editor` biên tập; `studio_user` chạy ứng dụng đã publish. Gán vai trò vào hồ sơ nhân sự xT, không dựa vào claim role tùy ý từ frontend.
- Kho: cài gói `packages/kho-hang` qua CLI `xt pack install` có dry-run/digest theo [Model Engine](model-engine.md). Người có `warehouse_manager` quản lý danh mục và duyệt; người tạo phiếu phải khác người duyệt.
- Tạo kho → vị trí → hàng hóa → phiếu nhập/xuất → gửi duyệt → người có quyền xác nhận. Số lượng hiển thị theo đơn vị, backend dùng số nguyên phần nghìn.
- Sửa phiếu đã ghi nhận bằng phiếu đảo: `kind=reversal`, `reverses` trỏ phiếu nhập/xuất gốc đã ghi nhận, dòng hàng trùng toàn bộ phiếu gốc. Phiếu đảo đi qua đúng quy trình duyệt, ghi biến động ngược dấu với khóa `<phiếu gốc>:<dòng>:rev`; mỗi phiếu gốc chỉ đảo được một lần, không đảo phiếu đảo, không làm âm kho. Biến động cũ không bị sửa hay xóa.
- `stock_balance` và `stock_movement` mang ref `warehouse` do quy trình ghi kho điền, nên tồn theo kho và nhập/xuất theo kho lọc được bằng `search_records` (`where` trên `warehouse`). Tồn cũ chưa có kho được điền ở lần ghi kế tiếp.
- `stock_level` là tồn theo hàng × kho, cập nhật trong cùng transaction ghi kho: `on_hand_milli`, `reorder_level_milli`, `below_reorder`, `last_movement` (biến động gần nhất). Hàng dưới ngưỡng là `search_records` với `where` `below_reorder = true`. Ngưỡng là giá trị của hàng tại lần ghi kho gần nhất; đổi `reorder_level_milli` của hàng chỉ có hiệu lực ở lần ghi kế tiếp.
- `approved` của phiếu kho là đã ghi nhận tồn và sổ biến động trong cùng giao dịch. Khi ghi kho lỗi, quyết định và biến động phải rollback để thử lại được.
- Dữ liệu mô phỏng 3D nằm trong trình duyệt, không sửa tồn kho nghiệp vụ. Tải lại mô phỏng sẽ đặt lại hoạt cảnh.

## Studio và công cụ xT

Trong ứng dụng loại agent, chọn công cụ ở mục **Công cụ doanh nghiệp xT** rồi lưu/publish. Mỗi lần chạy có capability tạm thời, theo người dùng hiện tại; không lưu bearer người dùng trong Studio. xT vẫn kiểm IAM, tenant, revision, idempotency, phân loại dữ liệu cloud và xác nhận khi cần. Studio không được cấp công cụ quyết định phê duyệt.

Public share và API key độc lập đang tắt trong chế độ tích hợp. Workspace legacy chưa map sẽ không tự xuất hiện. Model chưa cấu hình hoặc upstream lỗi được báo rõ, không thay bằng nội dung giả.

## Kiểm chứng và giới hạn

- `make unified-build`: kiểm tra TypeScript của Studio/Desk, build cả ba frontend rồi gộp Studio/WareTrack 3D vào `desk/dist`.
- `make unified-test`: test của Studio và Desk; WareTrack 3D chưa có test script nên kiểm tra production build và báo rõ giới hạn này. Test backend chạy riêng bằng `uv run pytest tests/unit --no-cov`.
- `uv run pytest tests/integration/composition/test_warehouse.py -m integration --no-cov`: PostgreSQL thật, container fixture riêng, không dùng tenant thực. Chỉ chạy khi không có bài test stack nặng khác.
- `uv run pytest tests/unit/http/test_studio_node.py --no-cov`: proxy Python gọi Node/SQLite thật, gồm danh tính tenant và callback công cụ; identity đầu vào là fixture, không thay thế nghiệm thu Keycloak thật.

Sao lưu/khôi phục, health check và truy vết lỗi theo `trace_id`: [unified-ops.md](unified-ops.md). Ma trận quyền: [unified-role-matrix.md](unified-role-matrix.md).

Tiến độ đầy đủ, bằng chứng và các mục chưa đạt nằm trong [nhật ký thực thi](plans/unified-execution.md). Build/test với fixture không phải bằng chứng đã cập nhật server public hoặc đã nghiệm thu ChatGPT live cho bản hợp nhất.
