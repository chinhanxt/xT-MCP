<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Pack `giao-viec-sla`: Giao việc có SLA, leo thang, bản tin tuần

## Mục đích
Mỗi việc có đúng một người chịu trách nhiệm, có hạn, có tiêu chí hoàn thành; quá hạn thì tự nhắc và leo thang; mỗi tuần có bản tin tổng hợp. Giải PP2 (giao việc rơi rớt), PP5 (báo cáo thủ công).

## Vai trò
| Vai trò | Việc được làm |
|---|---|
| `nhan_vien` | Người thực hiện: xem việc của mình (`owner_id` = người làm) |
| `truong_bo_phan` | Giao việc, nghiệm thu, nhận leo thang cấp 2 (trong phòng mình) |
| `giam_doc` | Đọc toàn bộ, nhận bản tin |
| `quan_tri` | Cấu hình `loai_cong_viec` (SLA) |
| `xt_workflow` | Danh tính dịch vụ ghi trạng thái, tiến độ, nhật ký SLA, bản tin |
| `agent` | L0-L1 |

Quy ước dữ liệu: `owner_id` của `cong_viec` là **người thực hiện**, còn `nguoi_giao` là trường riêng. Nhờ vậy người làm xem được việc của mình qua RLS "own" mà không cần quyền phòng ban.

## Model (5)
`loai_cong_viec` (SLA theo loại), `cong_viec`, `cong_viec_muc` (checklist), `nhat_ky_sla`, `ban_tin_tuan`.

## Quy trình
`giao_viec_thuong` (SLA 4 giờ nhận, 72 giờ làm, 48 giờ nghiệm thu) và `giao_viec_khan` (1 / 8 / 4 giờ). Chọn theo `cong_viec.lop_sla`; rule model bắt việc ưu tiên Khẩn phải đi lớp Khẩn.

| Bước | Lane | Quá hạn thì |
|---|---|---|
| `nhan` | `nguoi_thuc_hien` | chuyển cho `nguoi_giao` (kèm thông báo) |
| `lam` | `nguoi_thuc_hien` | chuyển cho `truong_bo_phan` |
| `nghiem_thu` | `nguoi_giao` | chuyển cho `truong_bo_phan` |
| `dong` (service) | | đóng việc |

Từ chối nhận việc, hoặc không chấp nhận kết quả, đều kết thúc quy trình ở "Bị từ chối".

`ban_tin_tuan`: quy trình hai service (`tong_hop_tuan`, `notify`) chạy 8:00 thứ Hai theo khối `trigger` trong spec. **Khối `trigger` do compiler bỏ qua**; lịch phải do `xt-worker` đọc (xem REPORT.md).

## Quy tắc và nơi thực thi
| Quy tắc | Thực thi tại |
|---|---|
| Một người chịu trách nhiệm chính | `nguoi_thuc_hien` là trường bắt buộc duy nhất |
| Có hạn chót và tiêu chí hoàn thành | trường bắt buộc |
| Hoàn thành phải có kết quả; bị chặn phải có lý do; hoàn thành phải có thời điểm | rule `cong_viec` |
| Tiến độ 0-100 | min/max |
| Việc phụ thuộc việc khác | `cong_viec.phu_thuoc` (ref tự tham chiếu) |
| **Người làm không tự nghiệm thu** | IAM `self_approval` + Deny `deny-nguoi-lam-tu-nghiem-thu` + lane `nguoi_giao` |
| **Agent không nghiệm thu** | Deny `deny-agent-nghiem-thu` |
| Người dùng không ghi thẳng trạng thái | DB: `nhan_vien` chỉ `read`; ghi qua `xt_workflow` |

## Agent được làm gì theo mức tự chủ
| Mức | Được | Không được |
|---|---|---|
| L0 | Đọc, tìm việc quá hạn/bị chặn, trả lời "tuần này team kẹt gì", đọc bản tin | Ghi |
| L1 | Tách một chỉ đạo thành danh sách việc (nháp), đề xuất người làm theo tải, nhắc việc, soạn bản tin | Nghiệm thu, xóa |
| L2, L3 | Không có gì | Nghiệm thu bị chặn ở mọi mức |

## Dữ liệu demo
3 loại việc, 4 công việc (đang làm, quá hạn đã leo thang, bị chặn, hoàn thành), 4 mục checklist, 2 dòng nhật ký SLA, bản tin tuần 41.

## Giới hạn đã biết
Thời hạn SLA là hằng số trong spec nên phải có hai file quy trình (thường, khẩn) thay vì đọc từ `loai_cong_viec`; leo thang **thay thế** người làm thay vì chỉ nhắc; không có vòng làm lại; lane `nguoi_thuc_hien`, `nguoi_giao` phải được runtime gán từ dữ liệu từng việc. Xem `../REPORT.md`.
