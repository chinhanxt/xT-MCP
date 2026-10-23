<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Pack `de-xuat-phe-duyet`: Đề xuất và phê duyệt đa cấp

## Mục đích
Một luồng đề xuất chung cho mọi doanh nghiệp, có ba mẫu sẵn: **Tạm ứng**, **Thanh toán**, **Hoàn ứng**. Toàn bộ nghiệp vụ là dữ liệu: model (`models/`), quy trình (`processes/`), chính sách IAM (`policies/`), dữ liệu demo công ty Bình Minh (`seed/`). Không có dòng code nghiệp vụ nào.

Giải PP1 (phê duyệt tắc nghẽn), PP6 (tạm ứng/hoàn ứng rối), PP13 (không dấu vết). Biểu mẫu tham chiếu lấy từ `research/vn-forms-verified.yaml`: 03-TT (tạm ứng), 05-TT (thanh toán), 04-TT (hoàn ứng) của TT 99/2025/TT-BTC. Các trường như "Hình thức nhận tiền", "Số tài khoản", "Số tiền bằng chữ", "Mã phiếu" là mở rộng nội bộ, không phải mẫu chuẩn.

## Vai trò
| Vai trò | Việc được làm |
|---|---|
| `employee` | Đề nghị; xem đề xuất của chính mình |
| `dept_head` | Duyệt bước 1 đề xuất của phòng mình |
| `payment_accountant` | Kiểm chứng từ (thanh toán, hoàn ứng) |
| `chief_accountant` | Duyệt trên 50 triệu; xem xét nợ tạm ứng quá hạn |
| `director` | Duyệt cuối khi trên 5 triệu |
| `admin` | Cấu hình `proposal_type` |
| `xt_workflow` | Danh tính dịch vụ, **duy nhất** được ghi vào các bảng đề xuất |
| `agent` | Danh tính agent, chỉ L0-L1, không bao giờ duyệt |

## Model (7)
`proposal_type` (mẫu), `proposal` (phiếu chung), `advance_request` (03-TT), `payment_request` (05-TT), `advance_settlement` (04-TT), `approval_step` (nhật ký từng bước duyệt), `approval_ticket` (Phiếu xin duyệt chung, 0.3.0).

## Phiếu xin duyệt `approval_ticket` (0.3.0)
Request chung cho node "Người duyệt" của Studio và mọi việc cần một người ký: `title`, `content`, `amount` (tùy chọn, VND). Tạo bằng `create_record`, gửi bằng `submit_request`, quyết định bằng `decide_request` (`human_only`: người được giao xác nhận trên Desk bằng mật khẩu + OTP; người lập không tự duyệt). Quy trình `approval_ticket_approval` một bước: trưởng bộ phận của người lập, không có thì quản lý trực tiếp, cuối cùng là giám đốc (`fallback_assignee`). IAM: `apv.ticket-*` (người lập chỉ phiếu của mình; trưởng bộ phận cùng phòng và giám đốc quyết định; agent làm thay đúng người, không xóa).

## Quy trình `proposal_approval`
Đầu vào: `kind` (`advance_request`|`payment_request`|`advance_settlement`), `amount`, `requester`, `requester_is_dept_head`.

| Bước | Vai trò | Điều kiện | SLA, leo thang |
|---|---|---|---|
| `chk_overdue` (service) | | tạm ứng | gọi `check_overdue_advances` |
| `dept_head` | `dept_head` | người đề nghị không phải trưởng bộ phận | 24 giờ, lên `director` |
| `pay_acct` | `payment_accountant` | thanh toán, hoàn ứng | 24 giờ, lên `chief_accountant` |
| `chief_acct_overdue` | `chief_accountant` | tạm ứng và còn nợ quá hạn | 24 giờ, lên `director` |
| `chief_acct` | `chief_accountant` | trên 50 triệu | 24 giờ, lên `director` |
| `director` | `director` | trên 5 triệu | không |
| `post` (service) | | luôn | ghi sổ |

Kết quả: tối đa 5 triệu chỉ cần trưởng bộ phận; trên 5 đến 50 triệu thêm giám đốc; trên 50 triệu thêm kế toán trưởng. Từ chối ở bất kỳ bước nào kết thúc ở "Bị từ chối", không ghi sổ.

## Quy tắc và nơi thực thi
| Quy tắc | Thực thi tại |
|---|---|
| Hạn mức tạm ứng 30 triệu/lần | model `advance_request.amount` (max) |
| Ngày về >= ngày đi; hạn hoàn ứng >= ngày về | rule của `advance_request` |
| Chuyển khoản phải có số tài khoản | rule của `advance_request`, `payment_request` |
| Thanh toán từ 20 triệu cần hóa đơn điện tử + MST (ngưỡng cấu hình, sửa theo quy định áp dụng) | rule của `payment_request` |
| Chi vượt tạm ứng phải khai "chi thừa"; không vừa chi thừa vừa nộp lại | rule của `advance_settlement` |
| **Người đề nghị khác người duyệt** | 3 lớp: CHECK `proposal.final_approver != requester`; IAM `self_approval`; bỏ bước TBP khi người đề nghị là TBP |
| **Agent không bao giờ duyệt** | Deny `deny-agent-decide` (mọi mức tự chủ, kể cả có receipt); lane không có role; agent không có quyền ghi DB |
| Người dùng không ghi thẳng được trạng thái | DB: `employee`/`dept_head` chỉ có `read`; ghi qua `xt_workflow` |
| Chứng từ không được xóa | Deny `deny-delete-vouchers` |
| Trưởng phòng khác phòng không duyệt được | điều kiện `resource.department eq subject.department` |

## Agent được làm gì theo mức tự chủ
| Mức | Được | Không được |
|---|---|---|
| L0 | Đọc, tìm, giải thích đề xuất; tóm tắt cho người duyệt; trả lời "đề xuất của tôi đang ở đâu" | Ghi |
| L1 | Tạo nháp đề xuất, điền trường, kiểm thiếu chứng từ (thay người dùng, nhận quyền giao với người đó); nhắc người duyệt | Duyệt, từ chối, xóa |
| L2, L3 | Không có gì trong pack này | **Duyệt bị chặn ở mọi mức**, kể cả có biên nhận (receipt) của người |

Quyền của agent = giao của quyền agent và quyền người được đại diện (đã test: agent thay Việt đọc đề xuất của người khác bị `behalf_permission`).

## Dữ liệu demo (`seed/`)
Công ty Bình Minh (dữ liệu hư cấu): 3 loại đề xuất, 3 phiếu tạm ứng đã hoàn tất của Việt (6,5 / 7,0 / 8,1 triệu) kèm 6 bước duyệt. Người dùng demo ở `seed/00_nguoi_dung.json`.

## Giới hạn đã biết
Không có vòng "trả lại bổ sung"; không có ủy quyền khi vắng mặt; bước giám đốc có thể phải duyệt hai lần khi bước trưởng bộ phận bị leo thang lên chính giám đốc; chặn nợ quá hạn là cross-row nên chỉ là bước xem xét, chưa phải chặn cứng. Xem `../REPORT.md`.
