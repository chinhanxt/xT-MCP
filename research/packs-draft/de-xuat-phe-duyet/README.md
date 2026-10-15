<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Pack `de-xuat-phe-duyet`: Đề xuất và phê duyệt đa cấp

## Mục đích
Một luồng đề xuất chung cho mọi doanh nghiệp, có ba mẫu sẵn: **Tạm ứng**, **Thanh toán**, **Hoàn ứng**. Toàn bộ nghiệp vụ là dữ liệu: model (`models/`), quy trình (`processes/`), chính sách IAM (`policies/`), dữ liệu demo công ty Bình Minh (`seed/`). Không có dòng code nghiệp vụ nào.

Giải PP1 (phê duyệt tắc nghẽn), PP6 (tạm ứng/hoàn ứng rối), PP13 (không dấu vết). Biểu mẫu tham chiếu lấy từ `research/vn-forms-verified.yaml`: 03-TT (tạm ứng), 05-TT (thanh toán), 04-TT (hoàn ứng) của TT 99/2025/TT-BTC. Các trường như "Hình thức nhận tiền", "Số tài khoản", "Số tiền bằng chữ", "Mã phiếu" là mở rộng nội bộ, không phải mẫu chuẩn.

## Vai trò
| Vai trò | Việc được làm |
|---|---|
| `nhan_vien` | Đề nghị; xem đề xuất của chính mình |
| `truong_bo_phan` | Duyệt bước 1 đề xuất của phòng mình |
| `ke_toan_thanh_toan` | Kiểm chứng từ (thanh toán, hoàn ứng) |
| `ke_toan_truong` | Duyệt trên 50 triệu; xem xét nợ tạm ứng quá hạn |
| `giam_doc` | Duyệt cuối khi trên 5 triệu |
| `quan_tri` | Cấu hình `loai_de_xuat` |
| `xt_workflow` | Danh tính dịch vụ, **duy nhất** được ghi vào các bảng đề xuất |
| `agent` | Danh tính agent, chỉ L0-L1, không bao giờ duyệt |

## Model (6)
`loai_de_xuat` (mẫu), `de_xuat` (phiếu chung), `tam_ung` (03-TT), `thanh_toan` (05-TT), `hoan_ung` (04-TT), `buoc_duyet` (nhật ký từng bước duyệt).

## Quy trình `de_xuat_phe_duyet`
Đầu vào: `loai` (`tam_ung`|`thanh_toan`|`hoan_ung`), `amount`, `requester`, `requester_la_tbp`.

| Bước | Vai trò | Điều kiện | SLA, leo thang |
|---|---|---|---|
| `chk_no` (service) | | tạm ứng | gọi `kiem_tra_cong_no` |
| `tbp` | `truong_bo_phan` | người đề nghị không phải trưởng bộ phận | 24 giờ, lên `giam_doc` |
| `kttt` | `ke_toan_thanh_toan` | thanh toán, hoàn ứng | 24 giờ, lên `ke_toan_truong` |
| `ktt_no` | `ke_toan_truong` | tạm ứng và còn nợ quá hạn | 24 giờ, lên `giam_doc` |
| `ktt` | `ke_toan_truong` | trên 50 triệu | 24 giờ, lên `giam_doc` |
| `gd` | `giam_doc` | trên 5 triệu | không |
| `ghiso` (service) | | luôn | ghi sổ |

Kết quả: tối đa 5 triệu chỉ cần trưởng bộ phận; trên 5 đến 50 triệu thêm giám đốc; trên 50 triệu thêm kế toán trưởng. Từ chối ở bất kỳ bước nào kết thúc ở "Bị từ chối", không ghi sổ.

## Quy tắc và nơi thực thi
| Quy tắc | Thực thi tại |
|---|---|
| Hạn mức tạm ứng 30 triệu/lần | model `tam_ung.so_tien` (max) |
| Ngày về >= ngày đi; hạn hoàn ứng >= ngày về | rule của `tam_ung` |
| Chuyển khoản phải có số tài khoản | rule của `tam_ung`, `thanh_toan` |
| Thanh toán từ 20 triệu cần hóa đơn điện tử + MST (ngưỡng cấu hình, sửa theo quy định áp dụng) | rule của `thanh_toan` |
| Chi vượt tạm ứng phải khai "chi thừa"; không vừa chi thừa vừa nộp lại | rule của `hoan_ung` |
| **Người đề nghị khác người duyệt** | 3 lớp: CHECK `de_xuat.nguoi_duyet_cuoi != nguoi_de_nghi`; IAM `self_approval`; bỏ bước TBP khi người đề nghị là TBP |
| **Agent không bao giờ duyệt** | Deny `deny-agent-decide` (mọi mức tự chủ, kể cả có receipt); lane không có role; agent không có quyền ghi DB |
| Người dùng không ghi thẳng được trạng thái | DB: `nhan_vien`/`truong_bo_phan` chỉ có `read`; ghi qua `xt_workflow` |
| Chứng từ không được xóa | Deny `deny-delete-chung-tu` |
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
