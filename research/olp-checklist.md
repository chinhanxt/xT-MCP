# Checklist OLP Phần mềm nguồn mở 2026 — DX-OS Open-Core

Nguồn đối chiếu:

- **Đề**: [de_thi_OLP2026.txt](de_thi_OLP2026.txt), các mục 1–7.
- **Slide**: [Slide_Bao_Cao_DX_OS_OLP_2026.html](/home/chinhan/Downloads/Slide_Bao_Cao_DX_OS_OLP_2026.html), đặc biệt slide 7 “Bảng Tiêu Chí Chấm Điểm & Các Lỗi Trừ Điểm Cần Tránh”. Các tiêu chí điểm lấy từ slide, không phải từ một mục chấm điểm trong đề.

Tổng điểm: **100 = PoF 50 + Sản phẩm 50**. Mức trừ dưới đây chép theo từng lỗi trong bảng slide 7; tài liệu không nêu cách cộng dồn hoặc giới hạn trừ trong mỗi tiêu chí, nên không tự suy diễn. Mọi ô để trống để đội tự kiểm tra, không phải kết quả đánh giá repo hiện tại.

## 1. Điều kiện tham dự và nguồn mở

- [ ] Thành viên là sinh viên các trường đại học/cao đẳng toàn quốc, chuyên hoặc không chuyên CNTT; tối đa 03 thí sinh/đội, có 01 giảng viên của trường dẫn dắt. **Nguồn: Slide 1, Đối tượng & Quy mô Đội thi.**
- [ ] Trường đăng ký không quá 02 đội khối PMNM; đội hoàn tất đăng ký qua trường theo quy định BTC. **Nguồn: Slide 1; Đề §5, cam kết trong thư nộp bài.**
- [ ] Sản phẩm phát hành theo giấy phép nguồn mở chuẩn OSI-approved; đáp ứng điều kiện nguồn mở để được chấm điểm xếp hạng. **Nguồn: Slide 1, Điều kiện Tiên quyết Bắt buộc; Slide 7, I.2; Đề §5, cam kết bản quyền.**
- [ ] Công bố đầy đủ mã nguồn và kết quả sản phẩm trên kho công khai trên Internet, cho phép truy cập tự do. **Nguồn: Slide 1, Hình thức & Phương thức Thi; Đề §5, kho mã nguồn dự thi.**

## 2. PoF — 50 điểm: kiểm tra từng lỗi bị trừ điểm

### I.1. Quản lý mã nguồn Internet — 5 điểm

- [ ] Repo có web viewer để xem mã nguồn trên trình duyệt; thiếu web viewer **−3đ**. **Nguồn: Slide 7, I.1.**
- [ ] Repo cho truy cập mở; không truy cập mở **−3đ**. **Nguồn: Slide 7, I.1.**
- [ ] Repo được sử dụng thực tế trong quá trình phát triển, có lịch sử làm việc; tạo repo nhưng không sử dụng thực tế **−5đ**. **Nguồn: Slide 7, I.1; Slide 2, giai đoạn phát triển.**

### I.2. Giấy phép chuẩn OSI — 10 điểm

- [ ] Ghi giấy phép trong **từng tệp mã nguồn**, bằng license header phù hợp (ghi chú thuyết trình slide nhắc SPDX header); không ghi giấy phép trong từng tệp mã **−5đ**. **Nguồn: Slide 7, I.2 và ghi chú thuyết trình slide 7.**
- [ ] Các giấy phép sử dụng tương thích, không xung đột; xung đột giấy phép **−5đ**. **Nguồn: Slide 7, I.2.**
- [ ] Nêu rõ mục đích giấy phép; “thiếu mục đích giấy phép” **−5đ** (giữ nguyên thuật ngữ của bảng, slide không giải thích thêm). **Nguồn: Slide 7, I.2.**
- [ ] Có tệp **LICENSE chứa toàn văn giấy phép**, không chỉ tên hay đường dẫn; thiếu bản toàn văn LICENSE **−5đ**. **Nguồn: Slide 7, I.2.**

### I.3. Bản phát hành — 5 điểm

- [ ] Tạo ít nhất 01 release **trước thời điểm nộp bài**; không có release trước khi nộp **−5đ**. **Nguồn: Slide 7, I.3; Slide 2, hạn nộp bài.**
- [ ] Release có **số phiên bản** rõ ràng; không đánh số phiên bản **−3đ**. **Nguồn: Slide 7, I.3.**
- [ ] Phát hành gói **`.tar.gz`** theo định dạng mở; không dùng `.rar`, `.arj`; dùng định dạng đóng **−3đ**. **Nguồn: Slide 7, I.3; Slide 2, hạn nộp bài.**

### I.4. Cài đặt, dịch từ mã nguồn — 10 điểm

- [ ] Có hướng dẫn build/cài đặt từ mã nguồn đủ để BGK thực hiện; không có hướng dẫn build **−5đ**. **Nguồn: Slide 7, I.4; Slide 2, chấm kho mã nguồn.**
- [ ] Quy trình build không yêu cầu sửa thủ công tệp header; sửa thủ công vào tệp header **−5đ**. **Nguồn: Slide 7, I.4.**
- [ ] Có thể cấu hình trước khi biên dịch; không cấu hình được trước khi dịch **−5đ**. **Nguồn: Slide 7, I.4.**
- [ ] Không dùng công cụ đóng trong quy trình cài đặt/biên dịch; dùng công cụ đóng **−5đ**. **Nguồn: Slide 7, I.4.**

### I.5. Thư viện & Gói đính kèm — 10 điểm

- [ ] Làm rõ các thư viện sử dụng, có danh mục để đối chiếu nguồn gốc và giấy phép; không làm rõ thư viện sử dụng **−5đ**. **Nguồn: Slide 7, I.5; đối chiếu giấy phép tại I.2.**
- [ ] Không phát hành kèm gói đính kèm của dự án khác; phát hành kèm các gói này **−5đ**. **Nguồn: Slide 7, I.5.**
- [ ] Không tự ý sửa code gói đính kèm; tự ý sửa code gói đính kèm **−5đ**. **Nguồn: Slide 7, I.5.**

### I.6. Tài liệu & Giao tiếp — 10 điểm

- [ ] Có **Bug Tracker** để tiếp nhận/theo dõi lỗi; không có **−5đ**. **Nguồn: Slide 7, I.6.**
- [ ] Có **Changelog** ghi thay đổi; không có **−5đ**. **Nguồn: Slide 7, I.6.**
- [ ] Có **README và hướng dẫn**; không có Readme và hướng dẫn **−5đ**. **Nguồn: Slide 7, I.6.**

## 3. Phạm vi sản phẩm bắt buộc — Tầng 1 và Tầng 2

- [ ] Tích hợp các sản phẩm lõi công nghệ mở thành mô hình thực hành **DX-OS Open-Core**, phân tách nền tảng dịch vụ và ứng dụng. **Nguồn: Đề §2, kiến trúc phân tách mối quan tâm; §3, Mục đích yêu cầu đề thi.**
- [ ] **Tầng 1 chạy hoàn toàn ngầm (headless)**, không cung cấp UI, hoạt động như **PaaS** cung cấp dịch vụ cho ứng dụng tầng trên. Đề §3 ghi “hoàn toàn không cung cấp giao diện UI”; slide mô tả là không có UI người dùng cuối. **Nguồn: Đề §3, đoạn cuối; §2, Tầng Nền tảng Dịch vụ Lõi số; Slide 5.**
- [ ] Tầng 1 có dịch vụ **Định danh bảo mật (Identity/SSO)**. **Nguồn: Đề §3, bốn dịch vụ bắt buộc; Slide 5.**
- [ ] Tầng 1 có **Cổng API (API Gateway)**. **Nguồn: Đề §3, bốn dịch vụ bắt buộc; Slide 5.**
- [ ] Tầng 1 có **Quản trị dữ liệu cấu trúc/phi cấu trúc**, thể hiện khả năng quản trị cả hai loại dữ liệu. **Nguồn: Đề §3, bốn dịch vụ bắt buộc; §2, các nhóm quản trị dữ liệu; Slide 5.**
- [ ] Tầng 1 có **Động cơ luồng công việc (Workflow)**. **Nguồn: Đề §3, bốn dịch vụ bắt buộc; Slide 5.**
- [ ] **Tầng 2 có ít nhất 01 ứng dụng minh họa có giao diện**, thuộc không gian H-P-D-I và sử dụng nền tảng Tầng 1 cho bài toán minh họa. **Nguồn: Đề §3; Slide 5, yêu cầu bắt buộc và giải đáp được dẫn là từ BTC.**
- [ ] Xác định rõ ứng dụng thuộc H (Con người), P (Quy trình), D (Dữ liệu) hoặc I (Trí tuệ); **một không gian là đủ**, không cần phủ cả bốn. **Nguồn: Đề §1–§2, định nghĩa H-P-D-I; Slide 5, giải đáp được dẫn là từ BTC.**

**Phạm vi:** Đề §2 mô tả kiến trúc đầy đủ, còn §3 quy định phần bắt buộc là Tầng 1 và ít nhất một ứng dụng Tầng 2. Không coi mọi dịch vụ liệt kê tại §2 hay ứng dụng Tầng 3 là hạng mục bắt buộc phải triển khai. Supabase/Strapi/Directus chỉ là ví dụ tham khảo nguyên lý (Đề §3).

## 4. Đối chiếu thiết kế kỹ thuật — định hướng, không tự nâng thành điều kiện loại trừ

Đề §4 dùng cách diễn đạt “cần được xây dựng dựa trên” bốn nguyên lý; giải đáp được slide 5 dẫn là từ BTC nói các nguyên lý **chỉ mang tính định hướng**. Các ô dưới đây phục vụ rà soát/thuyết minh thiết kế, không khẳng định phải triển khai toàn bộ tính năng. Bảng bảy lớp công nghệ là ví dụ; đội có toàn quyền lựa chọn công nghệ (Đề §4; Slide 6).

- [ ] Đối chiếu nguyên lý phân tách theo vòng đời/tốc độ dữ liệu: giao dịch thời gian thực và tự động chuyển dữ liệu lịch sử xuống hồ dữ liệu để phân tích, tránh nghẽn lõi. **Nguồn: Đề §4(i); Slide 6.**
- [ ] Đối chiếu nguyên lý hội tụ IT/OT: kết nối quy trình nghiệp vụ với dữ liệu cảm biến hiện trường, hướng tới bản sao số thời gian thực. **Nguồn: Đề §4(ii); Slide 6.**
- [ ] Đối chiếu nguyên lý quản trị dữ liệu linh hoạt: tự động hóa dòng chảy/phân tích, kiểm soát phả hệ, nguồn gốc và chất lượng thông tin báo cáo. **Nguồn: Đề §4(iii); Slide 6.**
- [ ] Đối chiếu Zero-Trust kết hợp GitOps: kiểm dịch mã độc tại biên, cấu hình hạ tầng bằng mã, khả năng tự phục hồi khi có sự cố. **Nguồn: Đề §4(iv); Slide 6.**
- [ ] Rà soát giấy phép công nghệ thực tế lựa chọn; không mặc định mọi tên trong bảng tham chiếu đều đáp ứng điều kiện OSI. Slide lưu ý tránh n8n vì giấy phép Fair-code phi OSI, dù đề có nêu n8n làm ví dụ. **Nguồn: Đề §4, bảng lớp 4; Slide 6, lớp 4 và ghi chú thuyết trình; Slide 1, điều kiện giấy phép.**

## 5. Sản phẩm — 50 điểm tại chung kết

Đây là các tiêu chí đánh giá, không có bảng mức trừ cố định theo từng lỗi như phần PoF.

- [ ] **II.7 — Tính nguyên gốc kỹ thuật (10đ):** thể hiện sự sáng tạo trong thiết kế và giải pháp của đội. **Nguồn: Slide 7, II.7; Đề §6, giải pháp thiết kế và lựa chọn công nghệ.**
- [ ] **II.8 — Mức độ hoàn thiện (10đ):** sản phẩm chạy và trình diễn được trong thực tế. **Nguồn: Slide 7, II.8; Đề §6, hệ thống chạy thử nghiệm và demo.**
- [ ] **II.9 — Mức độ sử dụng thân thiện (10đ):** có tiện ích trực quan phục vụ người dùng cuối. **Nguồn: Slide 7, II.9; Đề §3, ứng dụng minh họa giao diện Tầng 2.**
- [ ] **II.10 — Phát triển bền vững (10đ):** công bố kèm tài liệu kỹ thuật và công cụ hỗ trợ. **Nguồn: Slide 7, II.10.**
- [ ] **II.11 — Trình diễn & Cộng đồng (10đ):** chuẩn bị showcase 15 phút và thể hiện khả năng thu hút cộng đồng. **Nguồn: Slide 7, II.11; Đề §6.**

## 6. Buổi chung kết — nội dung 15 phút

- [ ] Chuẩn bị hệ thống chạy thử nghiệm để trình diễn sản phẩm. **Nguồn: Đề §6, đoạn đầu.**
- [ ] Tổ chức toàn bộ bài trình bày và demo trong **15 phút/đội**. **Nguồn: Đề §6; Slide 7, nội dung trình bày.**
- [ ] Trình bày **giải pháp thiết kế và lựa chọn công nghệ**. **Nguồn: Đề §6, gạch đầu dòng 1.**
- [ ] Trình bày **các tính năng nền tảng và bài toán ứng dụng minh họa**. **Nguồn: Đề §6, gạch đầu dòng 2.**
- [ ] Trình bày **kết quả sản phẩm và chạy demo**. **Nguồn: Đề §6, gạch đầu dòng 3.**
- [ ] Trình bày **định hướng phát triển tiếp theo nếu có** — nội dung có điều kiện, không bắt buộc phải có định hướng mới. **Nguồn: Đề §6, gạch đầu dòng 4.**
- [ ] **Khuyến khích:** triển khai thử nghiệm trên Internet để người dùng truy cập, sử dụng và phản hồi chức năng. **Nguồn: Đề §6, đoạn đầu.**

## 7. Hồ sơ nộp bài

- [ ] Gửi kết quả dự thi bằng email đến **cả `olpvietnam@vaip.vn` và `thuky@vfossa.vn`**. **Nguồn: Đề §5, hình thức và địa chỉ nhận bài.**
- [ ] Đặt tiêu đề: **`BÀI THI PMNM 2026 - [Tên Đội] - [Tên Trường]`**. **Nguồn: Đề §5, Tiêu đề thư.**
- [ ] Nội dung có lời gửi BTC, tên đội, tên trường và thông báo đã hoàn thành bài thi. **Nguồn: Đề §5, mẫu Nội dung thư.**
- [ ] Liệt kê thành viên với **họ tên, năm sinh, ngành đào tạo** (mẫu đề có 03 dòng; quy mô tối đa 03 thí sinh theo slide). **Nguồn: Đề §5, Thành viên đội; Slide 1.**
- [ ] Ghi **tên người hướng dẫn và Khoa/Viện/Trường**. **Nguồn: Đề §5, Người hướng dẫn.**
- [ ] Ghi **đường dẫn kho mã nguồn chứa đầy đủ nội dung kết quả dự thi**. **Nguồn: Đề §5, Kho mã nguồn dự thi.**
- [ ] Ghi xác nhận hoàn thành đăng ký qua trường và cam kết toàn bộ bản quyền sử dụng sản phẩm tuân thủ quy tắc phần mềm tự do nguồn mở. **Nguồn: Đề §5, đoạn cam kết.**

## 8. Mốc thời gian và điểm cần xác nhận

**Mâu thuẫn cần giữ nguyên để đối chiếu:** Đề §5 ghi “17:00 Chủ Nhật ngày 06/12/2025”; slide 2 ghi “17:00 · CN 06/12/2026”. Không tự sửa năm trong đề hoặc coi slide là văn bản đính chính. Lịch dưới đây là lịch ghi trong slide.

- [ ] Xác nhận lại với BTC năm và hạn nộp chính xác do mâu thuẫn trên; kênh trao đổi trong đề: https://t.me/+YUkj6aM3bnYyOWQ1. **Nguồn: Đề §5, Thời hạn gửi bài; §7, Thông tin trao đổi; Slide 2.**
- [ ] Ghi nhận giai đoạn công bố chủ đề **07–09/2026**, đề chính thức được slide ghi công bố **30/09/2026**. **Nguồn: Slide 2, Công bố Chủ đề & Đề thi chính thức.**
- [ ] **10–11/2026:** đăng ký đội và phát triển sản phẩm, đẩy mã thường xuyên lên repo công khai. **Nguồn: Slide 2, Đăng ký Đội tuyển & Phát triển Sản phẩm.**
- [ ] **Trước khi nộp:** đã tạo ít nhất một release định dạng mở `.tar.gz`, có số phiên bản. **Nguồn: Slide 2, hạn nộp; Slide 7, I.3.**
- [ ] Hoàn tất email theo hạn slide ghi **17:00, Chủ Nhật 06/12/2026**, sau khi đối chiếu/xác nhận mâu thuẫn với năm **2025** trong đề. **Nguồn: Slide 2, Hạn chót gửi bài; Đề §5.**
- [ ] **07–09/12/2026:** sẵn sàng cho BGK chấm kho mã nguồn PoF 50đ và kiểm thử cài đặt từ mã nguồn. **Nguồn: Slide 2, Chấm thi Kho mã nguồn; Slide 7, phần I.**
- [ ] **10/12/2026:** trình diễn trực tiếp chung kết, chấm Sản phẩm 50đ, 15 phút/đội. **Nguồn: Slide 2, Trình diễn Sản phẩm & Chấm Chung kết; Slide 7, phần II; Đề §6 quy định thời lượng.**
- [ ] **11/12/2026:** theo dõi công bố kết quả và lễ trao giải. **Nguồn: Slide 2, Công bố Kết quả & Lễ Trao giải.**
