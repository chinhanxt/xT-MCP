<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Pack `nhan-su-vong-doi`: Tiếp nhận và nghỉ việc liên phòng ban

## Mục đích
Người mới đến có máy, tài khoản, mã lương, kế hoạch; người nghỉ không còn quyền truy cập và không mang theo tài sản. Chuỗi HR, IT, Kế toán, Quản lý. Giải PP7, PP13 và nghĩa vụ xóa/ẩn danh dữ liệu cá nhân sau chấm dứt hợp đồng.

## Vai trò
| Vai trò | Việc được làm |
|---|---|
| `nhan_su` | Hồ sơ, xác nhận ngày nghỉ, đóng hồ sơ |
| `hr_pii` | Duy nhất được đọc/sửa CCCD, SĐT, STK, lương |
| `it_admin` | Cấp và thu hồi tài khoản, thiết bị |
| `ke_toan_thanh_toan`, `ke_toan_truong` | Mã lương, quyết toán, xử lý khoản còn nợ |
| `truong_bo_phan` | Quản lý trực tiếp (chỉ nhân viên phòng mình) |
| `giam_doc` | Nhận leo thang |
| `nhan_vien` | Đọc hồ sơ của chính mình |
| `xt_workflow` | Danh tính dịch vụ |
| `agent` | L0-L1, **bị chặn hoàn toàn với PII** |

## Model (8)
`phong_ban`, `nhan_vien`, `nhan_vien_pii` (tách riêng vì RLS chỉ lọc theo dòng, không theo cột), `ho_so_vong_doi`, `viec_vong_doi` (checklist), `tai_san_cap`, `cap_quyen`, `quyet_toan_nghi_viec`.

## Quy trình
`nv_onboarding`: sinh checklist, rồi HR, IT, Kế toán, Quản lý, rồi kích hoạt nhân viên. SLA 24 giờ (HR, IT, KT), 48 giờ (Quản lý).

`nv_offboarding`: sinh checklist, HR xác nhận, Quản lý xác nhận bàn giao, **IT thu hồi (SLA 4 giờ, "trong ngày")**, kiểm tra tài sản/quyền còn lại, kiểm tra tạm ứng chưa hoàn, Kế toán quyết toán, Kế toán trưởng xử lý nếu còn nợ hoặc còn tài sản, HR ẩn danh dữ liệu và đóng hồ sơ. Bước đóng hồ sơ (`dong_ho_so_vong_doi`) **phải raise** nếu còn tài sản hoặc quyền chưa thu hồi: đây là chốt "không đóng được khi chưa thu hồi".

Các bước IT, Kế toán và Quản lý có thể chạy song song về nghiệp vụ nhưng compiler chưa hỗ trợ cổng song song, nên pack chạy tuần tự (REPORT.md, G1).

## Quy tắc và nơi thực thi
| Quy tắc | Thực thi tại |
|---|---|
| Ngày nghỉ >= ngày vào; nhân viên đang nghỉ/đã nghỉ phải có ngày nghỉ | rule `nhan_vien` |
| Không chi quyết toán khi còn tài sản chưa trả | rule `quyet_toan_nghi_viec` + chốt ở `dong_ho_so_vong_doi` |
| PII chỉ `hr_pii`; agent không thấy | model riêng + Deny `deny-agent-pii` + agent không có quyền DB |
| Người khởi tạo hồ sơ không tự duyệt bước của mình; người nghỉ không tự duyệt | IAM `self_approval` (creator = người khởi tạo, owner = nhân viên) |
| Quản lý chỉ duyệt nhân viên phòng mình | `resource.department eq subject.department` |
| Không xóa hồ sơ nhân sự | Deny `deny-delete-ho-so` |
| Agent không duyệt | Deny `deny-agent-decide` |

## Agent được làm gì theo mức tự chủ
| Mức | Được | Không được |
|---|---|---|
| L0 | Đọc sơ đồ tổ chức, danh bạ (không PII); liệt kê việc, tài sản, quyền người nghỉ đang nắm | Đọc PII |
| L1 | Soạn nháp hồ sơ vòng đời, đề xuất người nhận bàn giao, nhắc bước trễ SLA | PII, duyệt, xóa |
| L2, L3 | Không có gì | |

## Dữ liệu demo
5 phòng, 15 nhân sự (toàn bộ `synthetic=true`), PII giả (Hùng lương 14.500.000), hồ sơ onboarding đã hoàn tất của Trịnh Hải Yến với 4 việc, 5 tài sản, 6 quyền truy cập.

## Giới hạn đã biết
Không có song song; leo thang IT đi lên giám đốc vì spec không có "trưởng phòng IT" theo từng instance; không có nhãn PII trong model spec nên tách model là cách duy nhất hiện tại. Xem `../REPORT.md`.
