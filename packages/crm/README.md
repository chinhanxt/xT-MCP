<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Pack `crm`: CRM nhẹ

Ứng dụng mẫu của nhóm Tầng 3 **Trải nghiệm & Thuê bao** (D-UNI-T3-1). Toàn bộ nghiệp vụ là dữ liệu: model, quy trình, chính sách IAM, dữ liệu mẫu. Không có dòng code nghiệp vụ nào, không khai op riêng.

## Model (4)
| Model | Việc |
|---|---|
| `khach_hang` | Hồ sơ khách (mã duy nhất, loại, trạng thái chăm sóc, người phụ trách). Điện thoại, email gắn nhãn DLCN `basic` |
| `co_hoi` | Cơ hội: `stage` (mới, tiếp cận, báo giá, đàm phán, thắng, thua), `value_milli` (1 đồng = 1000), `owner_person` |
| `cham_soc` | Nhật ký chăm sóc, tham chiếu `khach_hang` (và tùy chọn `co_hoi`) |
| `de_nghi_giam_gia` | Đề nghị giảm giá, gắn primitive `request`; `state` do workflow ghi |

## Quy trình `duyet_giam_gia`
Đầu vào `discount_percent` lấy từ trường cùng tên của đề nghị. Quản lý kinh doanh (`sales_manager`) duyệt mọi đề nghị; **trên 20%** thêm giám đốc (`director`). Người đề nghị không tự duyệt (SoD của lõi). Ngưỡng 20% nằm trong `when` của quy trình, sửa ngưỡng bằng cách sửa dữ liệu quy trình.

## Vai trò
| Vai trò | Việc được làm |
|---|---|
| `sales` | Khách hàng (ghi chung), cơ hội và chăm sóc của chính mình, đề nghị giảm giá của mình |
| `sales_manager` | Ghi toàn bộ khách, cơ hội, chăm sóc; duyệt giảm giá |
| `director` | Đọc toàn bộ; duyệt giảm giá trên 20% |
| `admin` | Quản trị danh mục |
| `agent` | Đọc; soạn nháp đề nghị giảm giá. Deny `crm.deny-agent-approve`: không bao giờ duyệt |
| `xt_workflow` | Danh tính dịch vụ duy nhất ghi `state` của đề nghị |

Không ai xóa được bản ghi (`crm.deny-delete`).

## Dữ liệu mẫu (`seed/`)
3 khách hàng, 2 cơ hội, 2 lần chăm sóc, mã người dùng giữ chỗ `NV-0006`, `NV-0007` (lấy từ `seed/binh-minh/org.json`). Tenant mẫu `binhminh` trong IAM được đổi sang tenant đích lúc cài.

## Cài, tắt, bật
Cài như mọi gói: `plan_install` rồi `install(confirm=digest)`. Gói không phụ thuộc gói khác.

Ứng dụng nhóm `trai-nghiem-thue-bao` (khối `app` trong `pack.json`, giao diện generic, bộ tool đọc/ghi bản ghi và đề nghị).

## Giới hạn đã biết
Giảm giá được duyệt chưa tự cập nhật báo giá hay cơ hội (chưa có model báo giá). Chuyển `stage` do người dùng tự ghi, chưa có quy trình riêng.
