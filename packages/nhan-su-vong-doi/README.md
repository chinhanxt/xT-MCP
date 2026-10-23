<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Pack `nhan-su-vong-doi`: Tiếp nhận và nghỉ việc liên phòng ban

## Mục đích
Người mới đến có máy, tài khoản, mã lương, kế hoạch; người nghỉ không còn quyền truy cập và không mang theo tài sản. Chuỗi HR, IT, Kế toán, Quản lý. Giải PP7, PP13 và nghĩa vụ xóa/ẩn danh dữ liệu cá nhân sau chấm dứt hợp đồng.

## Vai trò
| Vai trò | Việc được làm |
|---|---|
| `hr` | Hồ sơ, xác nhận ngày nghỉ, đóng hồ sơ |
| `hr_pii` | Duy nhất được đọc/sửa CCCD, SĐT, STK, lương |
| `it_admin` | Cấp và thu hồi tài khoản, thiết bị |
| `payment_accountant`, `chief_accountant` | Mã lương, quyết toán, xử lý khoản còn nợ |
| `dept_head` | Quản lý trực tiếp (chỉ nhân viên phòng mình) |
| `director` | Nhận leo thang |
| `employee` | Đọc hồ sơ của chính mình |
| `xt_workflow` | Danh tính dịch vụ |
| `agent` | L0-L1; đọc `employee_pii` qua làn PII (client `cloud`: masked, `local`: full, theo `tool-contracts-v1.md` §3.8), không có Deny riêng cho PII |

## Model (8)
`department`, `employee`, `employee_pii` (tách riêng vì RLS chỉ lọc theo dòng, không theo cột), `lifecycle_case`, `lifecycle_task` (checklist), `asset_issue`, `access_grant`, `offboarding_settlement`.

## Quy trình
`employee_onboarding`: sinh checklist, rồi HR, IT, Kế toán, Quản lý, rồi kích hoạt nhân viên. SLA 24 giờ (HR, IT, KT), 48 giờ (Quản lý).

Bước IT áp các `access_grant` (system `keycloak`) của nhân viên thành vai trò xT (`sync_access_grants`). Allow-list (0.4.2, quy trình v7): `employee, hr, it_admin, payment_accountant, dept_head` và vai trò của các gói nghiệp vụ `warehouse_manager` (kho-hang), `sales`, `sales_manager` (crm), `project_manager` (cham-cong), `facility_manager`, `technician` (tai-san). `it_admin` tạo `access_grant` (chọn vai trò); người hoàn thành bước IT phải khác người khởi tạo hồ sơ, người tạo `access_grant` và người được cấp. Từ 0.4.3 (quy trình v8): giám đốc là người duyệt thay thế của bước IT (`alternates: ["role(director)"]`), để công ty chỉ có MỘT IT vẫn hoàn tất được (IT tạo grant, giám đốc hoàn thành bước); mọi SoD trên giữ nguyên (D-DEMO-LEVELS-1).

`employee_offboarding`: sinh checklist, HR xác nhận, Quản lý xác nhận bàn giao, **IT thu hồi (SLA 4 giờ, "trong ngày")**, kiểm tra tài sản/quyền còn lại, kiểm tra tạm ứng chưa hoàn, Kế toán quyết toán, Kế toán trưởng xử lý nếu còn nợ hoặc còn tài sản, HR ẩn danh dữ liệu và đóng hồ sơ. Bước đóng hồ sơ (`close_lifecycle_case`) **phải raise** nếu còn tài sản hoặc quyền chưa thu hồi: đây là chốt "không đóng được khi chưa thu hồi".

Các bước IT, Kế toán và Quản lý có thể chạy song song về nghiệp vụ nhưng compiler chưa hỗ trợ cổng song song, nên pack chạy tuần tự (REPORT.md, G1).

## Quy tắc và nơi thực thi
| Quy tắc | Thực thi tại |
|---|---|
| Ngày nghỉ >= ngày vào; nhân viên đang nghỉ/đã nghỉ phải có ngày nghỉ | rule `employee` |
| Không chi quyết toán khi còn tài sản chưa trả | rule `offboarding_settlement` + chốt ở `close_lifecycle_case` |
| PII chỉ `hr_pii` đọc được; agent đi qua làn PII | model riêng + IAM (`hr_pii` hoặc chủ hồ sơ) giao với quyền của người được đại diện; che/bỏ theo lớp client ở tầng tool (`cloud` = masked, `local` = full); agent không có quyền DB |
| Người khởi tạo hồ sơ không tự duyệt bước của mình; người nghỉ không tự duyệt | IAM `self_approval` (creator = người khởi tạo, owner = nhân viên) |
| Quản lý chỉ duyệt nhân viên phòng mình | `resource.department eq subject.department` |
| Không xóa hồ sơ nhân sự | Deny `deny-delete-case` |
| Agent không duyệt | Deny `deny-agent-decide` |

## Agent được làm gì theo mức tự chủ
| Mức | Được | Không được |
|---|---|---|
| L0 | Đọc sơ đồ tổ chức, danh bạ; liệt kê việc, tài sản, quyền người nghỉ đang nắm; đọc PII theo làn PII (masked với client cloud) nếu người được đại diện có `hr_pii` | Ghi token `[PII:…]` ngược vào DB |
| L1 | Soạn nháp hồ sơ vòng đời, đề xuất người nhận bàn giao, nhắc bước trễ SLA | Duyệt, xóa |
| L2, L3 | Không có gì | |

## Dữ liệu demo
5 phòng, 15 nhân sự (toàn bộ `synthetic=true`), PII giả (Hùng lương 14.500.000), hồ sơ onboarding đã hoàn tất của Trịnh Hải Yến với 4 việc, 5 tài sản, 6 quyền truy cập.

## Giới hạn đã biết
Không có song song; leo thang IT đi lên giám đốc vì spec không có "trưởng phòng IT" theo từng instance; không có nhãn PII trong model spec nên tách model là cách duy nhất hiện tại. Xem `../REPORT.md`.

## Hoàn thành bước (0.3.2, 0.3.3: chỉ ghi kèm được `note`)

Mỗi bước người có một dòng checklist `lifecycle_task` (trường `step` = id bước, sinh lúc `init`, kể cả bước có điều kiện). Người được giao bước hoàn thành bằng `transition_record` trên dòng đó (`done`, hoặc `skipped` kèm `values.note`); mọi bước là chỉ-người (`human_only`): agent nhận `confirmation_required`, chính người được giao xác nhận. Bước có điều kiện không chạy -> `skipped` khi đóng hồ sơ. IAM: `hr.step-transition`.
