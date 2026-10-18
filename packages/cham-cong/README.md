<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Pack `cham-cong`: Chấm công dự án (Timesheet nhẹ)

Ứng dụng mẫu của nhóm Tầng 3 **Thời gian & Dự án** (D-UNI-T3-1). Toàn bộ nghiệp vụ là dữ liệu: model, quy trình, chính sách IAM, dữ liệu mẫu. Không có dòng code nghiệp vụ nào, không khai op riêng.

## Model (3)
| Model | Việc |
|---|---|
| `du_an` | Danh mục dự án: mã duy nhất, quản lý, ngày bắt đầu và kết thúc, trạng thái |
| `cham_cong` | Một dòng mỗi người, dự án, ngày: `person`, `project`, `date`, `hours_milli` (1 giờ = 1000), `note`; tùy chọn tham chiếu `timesheet` |
| `bang_cong_tuan` | Bảng công tuần, gắn primitive `request`; `state` do workflow ghi |

## Quy tắc
| Quy tắc | Thực thi tại |
|---|---|
| Số giờ một ngày không quá 24 | rule `r1` và `max` của `cham_cong.hours_milli` |
| Mỗi người, dự án, ngày một dòng | index unique `i1` của `cham_cong` |
| Tuần kết thúc >= tuần bắt đầu; ngày kết thúc dự án >= ngày bắt đầu | rule của `bang_cong_tuan`, `du_an` |
| Tổng giờ tuần tối đa 168 | `max` của `bang_cong_tuan.total_hours_milli` |
| Người nộp không tự duyệt; agent chỉ chuyển quyết định của người duyệt được đại diện, luôn qua xác nhận Desk | SoD của lõi; `cc.agent-on-behalf` + kiểm lại quyền người được đại diện; Deny `cc.deny-agent-approve` (`record.approve`); D-UNI-AGENT-1 |

## Quy trình `duyet_bang_cong`
Nộp bằng `submit_request` trên `bang_cong_tuan`. Đầu vào `total_hours_milli` lấy từ chính bảng công. Quản lý dự án (`project_manager`) duyệt mọi bảng; **trên 60 giờ/tuần** thêm giám đốc (`director`). Duyệt, từ chối hoặc trả lại bổ sung; bị trả thì người nộp sửa rồi nộp lại.

## Vai trò
| Vai trò | Việc được làm |
|---|---|
| `employee` | Chấm công và bảng công tuần của chính mình; đọc danh mục dự án |
| `project_manager` | Tạo, sửa dự án; đọc mọi chấm công; duyệt bảng công |
| `director` | Đọc toàn bộ; duyệt bảng công trên 60 giờ |
| `admin` | Quản trị dự án |
| `agent` | Đọc; soạn nháp bảng công của người được đại diện; không bao giờ duyệt |
| `xt_workflow` | Danh tính dịch vụ duy nhất ghi `state` của bảng công |

Không ai xóa được bản ghi (`cc.deny-delete`).

## Dữ liệu mẫu (`seed/`)
2 dự án, 3 dòng chấm công; mã người dùng giữ chỗ `NV-0005`, `NV-0006` (từ `seed/binh-minh/org.json`). Tenant mẫu `binhminh` trong IAM được đổi sang tenant đích lúc cài.

## Cài, tắt, bật
Cài như mọi gói: `plan_install` rồi `install(confirm=digest)`. Gói không phụ thuộc gói khác.

Ứng dụng nhóm `thoi-gian-du-an` (khối `app` trong `pack.json`, giao diện generic, bộ tool đọc/ghi bản ghi và đề nghị).

## Giới hạn đã biết
`total_hours_milli` do người nộp khai, chưa tự cộng từ `cham_cong`. Bảng công được duyệt chưa khóa các dòng `cham_cong` của tuần đó (khóa theo trạng thái là việc của lõi).
