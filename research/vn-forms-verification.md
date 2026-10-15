# Kiểm chứng agyxt_vn_forms.yaml (ngày 2026-10-15)

Kết quả: `vn-forms-verified.yaml` (20 biểu mẫu, 16 verified=true, 4 verified=false). Một số trang thuvienphapluat trả 403 nên chữ ký/field chuẩn của mẫu 03/04/05-TT, 01-VT, 02-TT lấy từ kiến thức, ghi rõ trong `verification_note`.

## Nguồn
- Danh mục 33 mẫu Phụ lục I TT 99/2025: https://luatvietnam.vn/linh-vuc-khac/danh-muc-33-bieu-mau-chung-tu-ke-toan-theo-thong-tu-99-883-105817-article.html
- TT 99 có thay TT 133 không: https://thuvienphapluat.vn/ma-so-thue/phap-luat-thue/thong-tu-992025-co-thay-the-thong-tu-1332016-che-do-ke-toan-doanh-nghiep-nho-va-vua-213873.html
- Nghị định 30/2020: https://thuvienphapluat.vn/van-ban/bo-may-hanh-chinh/Nghi-dinh-30-2020-ND-CP-cong-tac-van-thu-436532.aspx
- BLLĐ 2019 (tên điều): hethongphapluat.com/bo-luat-lao-dong-2019/

## Sự thật pháp lý
- TT 99/2025/TT-BTC ban hành 27/10/2025, hiệu lực 01/01/2026 (năm tài chính bắt đầu từ 01/01/2026), thay TT 200/2014 (cùng TT 75/2015, 53/2016, 195/2012). Không áp dụng sớm.
- Chứng từ: Phụ lục I có 33 mẫu, Điều 9 cho phép chỉ tham khảo, tự thiết kế/sửa/bổ sung (phải theo Điều 16 Luật Kế toán và ban hành quy chế hạch toán nêu lý do). Tức là KHÔNG bắt buộc.
- Số hiệu mẫu TT 99: 01-TT Phiếu thu, 02-TT Phiếu chi, 03-TT Giấy đề nghị tạm ứng, 04-TT Giấy thanh toán tiền tạm ứng, 05-TT Giấy đề nghị thanh toán, 06-TT Biên lai thu tiền, 08a/08b-TT Bảng kiểm kê quỹ, 09-TT Bảng kê chi tiền; 01-VT Phiếu nhập kho, 02-VT Phiếu xuất kho, 03-VT Biên bản kiểm nghiệm. Số hiệu 01-05-TT và 01-VT trong file gốc ĐÚNG.
- TT 133/2016/TT-BTC: KHÔNG bị TT 99 thay thế. DNNVV được tiếp tục dùng TT 133 hoặc tự nguyện chuyển sang TT 99 (Điều 31). Có dự thảo thông tư thay thế TT 133 (tin cuối 9/2026); chưa xác nhận đã ban hành -> "chưa rõ".
- Nghị định 30/2020/NĐ-CP (hiệu lực 05/03/2020): áp dụng cho cơ quan, tổ chức nhà nước và doanh nghiệp nhà nước; tổ chức chính trị/xã hội "vận dụng"; doanh nghiệp tư nhân chỉ tham khảo. Chưa kiểm tra văn bản sửa đổi sau 2025.
- BLLĐ 2019: Đ35 quyền đơn phương chấm dứt HĐLĐ của NLĐ (đúng); Đ48 trách nhiệm khi chấm dứt HĐLĐ (đúng); Đ112 nghỉ lễ, tết (SAI chủ đề cho đơn nghỉ phép); Đ113 nghỉ hằng năm; Đ114 ngày nghỉ tăng thêm theo thâm niên; Đ115 nghỉ việc riêng, nghỉ không hưởng lương.

## Danh sách sửa
| # | Biểu mẫu | Sửa |
|---|---|---|
| 1-5 | Tạm ứng, Phiếu chi, Đề nghị thanh toán, Thanh toán tạm ứng, Phiếu thu | legal_basis cũ "TT 200 hoặc TT 133" -> TT 99/2025 Phụ lục I (số hiệu giữ nguyên), nêu tính chất tham khảo; TT 133 vẫn dùng được cho DNNVV; TT 200 đã hết hiệu lực. |
| 1 | Giấy đề nghị tạm ứng | Field "Hình thức nhận tiền", "Số tài khoản ngân hàng thụ hưởng", "Mã phiếu" không có trong mẫu 03-TT (mở rộng nội bộ). Chữ ký chuẩn: Giám đốc, Kế toán trưởng, Phụ trách bộ phận, Người đề nghị; "Kế toán thanh toán" không có trong mẫu chuẩn. Giữ field/approver để dùng nội bộ nhưng đánh dấu. |
| 3 | Giấy đề nghị thanh toán | "Hình thức thanh toán", "Thông tin tài khoản nhận tiền" là mở rộng nội bộ. |
| 6 | Đơn xin nghỉ phép | Bỏ Điều 112 (nghỉ lễ, tết). Giữ 113, 114, 115. Làm rõ không có mẫu luật định. |
| 7 | Giấy đi đường | SAI: "06-LĐTL theo TT 133" -> TT 133 cũng là 04-LĐTL. TT 99 không còn mẫu giấy đi đường trong 33 mẫu. C16-HD (TT 107/2017) chỉ cho hành chính sự nghiệp. verified=false. |
| 10 | Biên bản giao nhận, nghiệm thu | "BLDS 2015" quá chung, thêm Luật Thương mại 2005 và mẫu 03-VT tham khảo; điều khoản cụ thể "chưa rõ". verified=false. |
| 11 | Phiếu nhập kho | 01-VT đúng; chuyển sang TT 99. "Kế toán kho" không có trong chữ ký mẫu chuẩn. |
| 13 | Đánh giá ứng viên | Thêm tham chiếu Đ24-26 BLLĐ (thử việc) theo kiến thức, chưa xác minh trong phiên. verified=false. |
| 14 | Bàn giao tài sản NV mới | Bỏ "BLLĐ 2019" chung chung (không có điều cụ thể); thêm 01-TSCĐ tham khảo. |
| 15 | Đơn xin thôi việc | Đ35 đúng; bổ sung thời hạn báo trước 45/30/3 ngày (theo kiến thức). |
| 16 | Biên bản bàn giao khi thôi việc | Đ48 đúng; thêm Đ113 khoản 3 cho ngày phép tồn. |
| 17-18 | Công văn đến/đi | Làm rõ Nghị định 30/2020 áp dụng trực tiếp cho cơ quan nhà nước và DNNN, doanh nghiệp tư nhân chỉ vận dụng. |
| 8,9,12,19,20 | Mua sắm, tuyển dụng, IT | Không có cơ sở luật cụ thể, ghi là quy chế nội bộ. Biểu mẫu 20: tham chiếu bảo vệ dữ liệu cá nhân "chưa rõ" (chưa tra); verified=false. |

## Chưa xác minh được
- Toàn văn các mẫu 03/04/05-TT (thuvienphapluat bị chặn 403): danh sách field/chữ ký chuẩn theo kiến thức.
- Thời hạn báo trước Đ35, 14 ngày Đ48 (nguồn chỉ xác nhận tên điều).
- Việc có thông tư thay TT 133 chính thức ban hành chưa; có bài nhắc "TT 58/2026/TT-BTC" nhưng không rõ nội dung.
- BLLĐ 2019 có bị sửa/thay năm 2026 hay chưa: không thấy bằng chứng.
