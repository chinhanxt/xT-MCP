<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Pack `tai-san`: Tài sản, bảo trì, sự kiện cảm biến (mô phỏng)

Ứng dụng mẫu của nhóm Tầng 3 **Tài sản & Không gian** (D-UNI-T3-1). Toàn bộ nghiệp vụ là dữ liệu: model, quy trình, chính sách IAM, dữ liệu mẫu. Không có dòng code nghiệp vụ nào, không khai op riêng.

## Model (3)
| Model | Việc |
|---|---|
| `tai_san` | Danh mục: `code` duy nhất, tên, loại, `location`, `status` (hoạt động, đang bảo trì, ngừng sử dụng, đã thanh lý) |
| `phieu_bao_tri` | Phiếu bảo trì tham chiếu `tai_san`, gắn primitive `request`; `state` do workflow ghi |
| `su_kien_cam_bien` | Sự kiện đo: `source`, `metric`, `value`, `unit`, `observed_at`. **Chỉ là dữ liệu** |

## Nguồn mô phỏng
`source` có ba giá trị: `cam_bien` (thiết bị thật), `nhap_tay`, `mo_phong`. Nhãn của `mo_phong` là "MÔ PHỎNG: dữ liệu giả lập, không phải thiết bị thật", hiển thị nguyên văn ở mọi giao diện sinh từ `describe_model`. Dữ liệu mẫu chỉ dùng `mo_phong` và `nhap_tay`; gói không kết nối thiết bị nào.

## Sự kiện không tự ghi nghiệp vụ
Không có quy trình, op hay trigger nào đọc `su_kien_cam_bien` để đổi `tai_san.status` hoặc tạo phiếu. Việc này được chặn ở dữ liệu:
- `agent` bị Deny `ts.deny-agent-asset-write` trên `tai_san` (chỉ đọc), và model chỉ cấp `read` cho agent.
- Chỉ `facility_manager`, `admin` ghi được `tai_san`; sự kiện được tạo nhưng không ai sửa lại (`ts.deny-event-update`).
- Người xem sự kiện bất thường tự lập `phieu_bao_tri`; phiếu đi qua phê duyệt như mọi đề nghị.

## Quy trình `duyet_bao_tri`
Đầu vào `est_cost` lấy từ phiếu. Quản lý tài sản (`facility_manager`) duyệt mọi phiếu; **trên 20 triệu đồng** thêm giám đốc (`director`). Người lập phiếu không tự duyệt (SoD của lõi); agent không tự duyệt: `decide_request` qua agent chỉ thay người duyệt được đại diện và luôn cần người đó xác nhận trên Desk; `record.approve` bị Deny (`ts.deny-agent-approve`, D-UNI-AGENT-1).

## Vai trò
| Vai trò | Việc được làm |
|---|---|
| `employee` | Đọc tài sản và sự kiện; lập phiếu bảo trì của mình |
| `technician` | Ghi sự kiện cảm biến; lập phiếu bảo trì |
| `facility_manager` | Tạo, sửa tài sản; ghi sự kiện; duyệt phiếu |
| `director` | Đọc toàn bộ; duyệt phiếu trên 20 triệu |
| `admin` | Quản trị danh mục |
| `agent` | Đọc; ghi sự kiện (ví dụ nạp dữ liệu mô phỏng); soạn nháp phiếu |
| `xt_workflow` | Danh tính dịch vụ duy nhất ghi `state` của phiếu |

Không ai xóa được bản ghi (`ts.deny-delete`).

## Dữ liệu mẫu (`seed/`)
3 tài sản, 3 sự kiện (2 `mo_phong`, 1 `nhap_tay`). Tenant mẫu `binhminh` trong IAM được đổi sang tenant đích lúc cài.

## Cài, tắt, bật
Cài như mọi gói: `plan_install` rồi `install(confirm=digest)`. Gói không phụ thuộc gói khác.

Ứng dụng nhóm `tai-san-khong-gian` (khối `app` trong `pack.json`, giao diện generic, bộ tool đọc/ghi bản ghi và đề nghị).

## Giới hạn đã biết
Phiếu bảo trì được duyệt chưa tự đổi `tai_san.status` sang "đang bảo trì"; người quản lý đổi tay. Chưa có model vị trí theo không gian (tọa độ, sơ đồ); `location` là chuỗi.
