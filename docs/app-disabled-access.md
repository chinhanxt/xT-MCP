<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Quyền đọc ứng dụng đang tắt

Theo D-UNI-T3-1, tắt ứng dụng giữ lại model và dữ liệu. Ngoại lệ đọc dành cho quản trị hiện
dùng **đúng tên vai trò `admin`** (`APP_ADMIN_ROLE` trong
`src/xt/application/model_engine/service.py`), trong tập vai trò hiệu lực của người gọi ở
tenant đó. `it`, `director`, `administrator`, `tenant_admin` không có ngoại lệ này, kể cả khi
policy IAM và quyền model cho phép họ đọc lúc ứng dụng đang bật. Không suy vai trò từ tên
người, quyền Keycloak quản trị realm, hay tham số tool. Tên vai trò hợp lệ là chữ thường.

Hành vi cụ thể:

- `get_record`, `search_records`, `aggregate_records` và `describe_model` bỏ qua cổng
  `app_disabled` khi người gọi có `admin`. Đọc bản ghi vẫn phải qua IAM, quyền `read` của model
  và phạm vi RLS. `describe_model` vẫn yêu cầu ít nhất một vai trò của người gọi có trong
  `permissions` của model. Ngoại lệ này không tự cấp quyền model hay ghi đè IAM Deny.
- Mọi thao tác ghi bản ghi của ứng dụng đang tắt, kể cả ghi từ workflow và từ người có `admin`,
  trả `capability_unavailable` với `details.reason=app_disabled`.
- Khi IAM từ chối đọc, `ToolAccessGuard` giữ nguyên lý do IAM cho `admin`; với vai trò khác,
  model thuộc ứng dụng đang tắt của tenant trả lý do `app_disabled`.
- `inspect_workspace` luôn ẩn model của ứng dụng đang tắt, kể cả với `admin`. Trong mục
  `apps`, chỉ người có `admin` thấy ứng dụng đang tắt cùng `status=disabled` để đối soát và
  yêu cầu bật lại; vai trò khác không thấy ứng dụng đó.
- `/data` (PostgREST) áp cùng tên vai trò `admin`: gate không lọc model đang tắt cho người
  có vai trò này, nhưng quyền đọc RLS của model vẫn áp dụng. Vai trò khác nhận 0 dòng; xem
  [hợp đồng dữ liệu headless](headless-data.md).

Chưa có cấu hình tên vai trò này theo tenant. `PolicyDocument` hiện chỉ nhận `version`,
`tenant`, `statements`; `TenantProfile` không có thuộc tính vai trò quản trị ứng dụng.
Tham số `admin_role` của process engine phục vụ quyền quản trị bước workflow, không thay
đổi ngoại lệ đọc ứng dụng. Thêm tùy chọn chỉ ở Python sẽ làm lệch hành vi với gate PostgREST;
cấu hình sau này cần một chính sách đã duyệt dùng chung cho cả hai đường.

Các kiểm thử đơn vị ghim tên vai trò thật, không lấy tên kỳ vọng từ `APP_ADMIN_ROLE`:
`tests/unit/model/test_app_gate.py`, `tests/unit/test_app_disabled_gate.py`,
`tests/unit/composition/test_workspace_apps.py`.
