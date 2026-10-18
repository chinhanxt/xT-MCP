<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Phục hồi quy trình khi bật lại ứng dụng

`xt pack enable` lập danh sách sự cố đang mở có mã `capability_unavailable`, lý do
`app_disabled`, đúng tenant, tên gói và model của gói. Chỉ instance đang chạy có lệnh
`act` hoặc `tick` được ghi đầy đủ mới được thử lại. Kế hoạch in `instance`, `incident`,
`model` và `eligible=N`; digest bao gồm danh sách này và hash lệnh gốc. Sự cố hoặc
lệnh thay đổi giữa lúc lập kế hoạch và áp dụng làm mã xác nhận không còn khớp.

```sh
xt pack enable --tenant acme kho-hang --proposed-by agent:ops --approved-by person:admin
xt pack enable --tenant acme kho-hang --proposed-by agent:ops --approved-by person:admin \
  --confirm sha256:...
```

Cần `XT_DB_MIGRATOR_URL`; nếu có sự cố để phục hồi, cần thêm `XT_DB_APP_URL` của role
`xt_app` và cấu hình khóa audit như tiến trình API. Không dùng kết nối migrator để
thực thi nghiệp vụ hoặc đặt ngữ cảnh RLS. Thiếu DSN app thì từ chối trước khi bật gói.

Sau khi bật thành công, CLI in `eligible=N retried=N failed=N skipped=N`. Từng sự cố
được khóa dòng và kiểm lại danh sách/lệnh đã duyệt. Lệnh người dùng chạy với người
thực hiện ban đầu, kiểm lại người còn hoạt động, IAM hiện tại trên bản ghi, phân công và tách nhiệm vụ; không
đổi người duyệt bước thành người duyệt bật gói. Khi có, giữ lại sổ quyết định đã xác
nhận và phần sửa bản ghi của lệnh gốc; trạng thái đề nghị gắn với quy trình được đồng
bộ trong cùng transaction. Lệnh duyệt giữ revision ban đầu và kiểm CAS cả trước khi
chạy lẫn lúc ghi trạng thái cuối, để không duyệt dữ liệu đã bị thay đổi.

Mỗi lần thử là một transaction riêng. Thành công giải quyết sự cố và ghi sự kiện
`incident.retry`; sự kiện này vào hash-chain qua trigger audit metadata. Nếu lỗi,
hoàn tác mọi ghi nghiệp vụ của lần thử rồi ghi số lần thử, người duyệt và sự kiện
`incident.retry` có `result=failed`; sự cố gốc vẫn mở. Các sự cố khác tiếp tục được
xử lý, gói vẫn bật và mã thoát CLI là 0 khi vòng đời đã áp thành công. `failed>0`
cần người vận hành xem xét nguyên nhân trước khi thử lại.

Khóa session theo tenant giữ xuyên qua commit bật gói và các transaction phục hồi,
chặn cài/tắt gói xen giữa. Không giữ transaction audit của migrator khi chờ app.
Hành động quy trình bình thường vẫn có thể chạy; khóa dòng và kiểm task ngăn xử lý
trùng. Task đã xong, instance đã đóng, sự cố đã giải quyết hoặc lệnh thay đổi được
bỏ qua. Chạy lại đúng mã xác nhận cũ sau khi thành công và không còn sự cố là no-op,
không UPDATE trạng thái hay phát sinh thêm biến động. Với sự cố còn mở sau lỗi,
chạy lại lệnh không `--confirm` để duyệt kế hoạch mới; gói đang bật cũng lập được
kế hoạch phục hồi, không cần tắt lại.

Giới hạn nâng cấp: sự cố cũ chỉ có chuỗi lỗi, thiếu model/gói/lệnh gốc không thể được
phục hồi tự động an toàn. Không đoán từ thông báo lỗi hoặc tự tạo quyết định người
dùng; các sự cố đó vẫn cần xử lý thủ công. Đường tool bị chặn trước khi vào engine
không tạo sự cố engine để phát lại. Không thêm tool MCP hay migration.

Quy ước quyền đọc ứng dụng đang tắt: [app-disabled-access.md](app-disabled-access.md).
