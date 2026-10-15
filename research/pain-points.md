# Pain points chung của doanh nghiệp & các workflow lõi liên ngành cho xT

> Phạm vi: doanh nghiệp Việt Nam 10–200 nhân sự, mọi ngành. Mục tiêu: xác định **nỗi đau dùng chung**, **workflow lõi**, **primitive** để xT (MCP server, agent-native) hiện thực hóa, gắn với đề OLP 2026 (DX-OS, "2 thái cực", H-P-D-I).
> Ngày nghiên cứu: 15/10/2026.

## 0. Ghi chú phương pháp & độ tin cậy nguồn

- **[Nguồn]** = có trích dẫn URL. **[Suy luận]** = nhận định của người nghiên cứu, chưa có số liệu kiểm chứng.
- Nhiều số liệu Việt Nam đã cũ (2020–2022); nhiều số liệu quốc tế đến từ khảo sát do vendor tài trợ. Dùng làm **chỉ báo xu hướng**, không dùng như benchmark tuyệt đối. Khi đưa lên slide thi, ghi rõ năm + đơn vị khảo sát.
- Bối cảnh quy mô: cuối 2024 Việt Nam có khoảng 940.078 DN đang hoạt động, DNNVV chiếm gần 98%; phần lớn là siêu nhỏ (~69%) và nhỏ (~24,5%) — [MISA AMIS dẫn GSO](https://amis.misa.vn/276711/quan-tri-doanh-nghiep-vua-va-nho/), [quanlynhanuoc.vn](https://www.quanlynhanuoc.vn/?p=53555).

### Số liệu nền quan trọng nhất (dùng được cho pitch)

| # | Số liệu | Nguồn |
|---|---|---|
| S1 | 69% DN mới ứng dụng số ở mức cơ bản (email, phần mềm kế toán); >5% chưa bắt đầu | Hiệp hội DNNVV + Vụ KT&XH số (Bộ KH&CN), dẫn bởi [VCCI 08/2025](https://www.vcci.com.vn/tin-tuc/chuyen-doi-so-o-doanh-nghiep-viet-khoang-cach-lon-giua-ky-vong-va-thuc-tien) |
| S2 | Ví dụ thực tế: 1 DN dùng 5 phần mềm không liên thông, dữ liệu khách hàng phải nhập ít nhất 2 lần | [VCCI 08/2025](https://www.vcci.com.vn/tin-tuc/chuyen-doi-so-o-doanh-nghiep-viet-khoang-cach-lon-giua-ky-vong-va-thuc-tien) |
| S3 | 60,1% DN: chi phí công nghệ là rào cản; 52,3%: khó thay đổi thói quen/thiếu nhân lực nội bộ | Cục PTDN (Bộ KH&ĐT) + USAID LinkSME, khảo sát ~1.300 DN, 2021 — [VIR](https://vir.com.vn/annual-report-on-digital-transformation-highlights-barriers-to-overcome-90181.html), [doanhnghiephoinhap.vn](https://doanhnghiephoinhap.vn/nhung-rao-can-khien-cac-doanh-nghiep-chua-the-chuyen-doi-so.html) |
| S4 | >48% DN đã làm CĐS nhưng **dừng lại** vì giải pháp không phù hợp; 56,3% có <3 người phụ trách CĐS; 43,7% có <3 nhân sự IT | Báo cáo CĐS 2022 Cục PTDN + LinkSME — [vietnamnet](https://vietnamnet.vn/en/more-vietnamese-companies-start-digital-transformation-process-2113700.html) |
| S5 | Nhu cầu của DN đang tăng trưởng: phân tích dữ liệu & báo cáo thông minh 63,5%, CRM 60,7%, ERP 57,8%; DN mới bắt đầu: giải pháp vận hành nội bộ 53,7% | [VIR](https://vir.com.vn/annual-report-on-digital-transformation-highlights-barriers-to-overcome-90181.html) |
| S6 | ERP: ~40% DN lớn dùng nhưng chỉ ~14% DNNVV (2020) | [vnbusiness](https://vnbusiness.vn/lo-doanh-nghiep-nho-that-bai-trong-chuyen-doi-so.html) |
| S7 | Gartner: đến 2027, >70% dự án ERP mới khởi động không đạt mục tiêu business case ban đầu | dẫn lại qua [ECI](https://ecisolutions.com/blog/the-2-million-mistake-why-70-of-erp-implementations-fail), [Panorama](https://www.panorama-consulting.com/what-do-the-recent-erp-failures-all-have-in-common/) |
| S8 | Người lao động tri thức mất 60% thời gian cho "work about work" (điều phối, đuổi theo trạng thái, họp) — 13.123 người, 7 nước | [Asana Anatomy of Work 2021](https://www.businesswire.com/news/home/20210114005374/en) |
| S9 | ~25% tuần làm việc (~9 giờ/tuần) dùng để tìm thông tin | [Atlassian State of Teams 2025](https://www.unleash.ai/artificial-intelligence/news/atlassian-ai-could-help-workers-stop-wasting-2-4-billion-hours-searching-for-information-every-year); McKinsey 2012: ~19% — [tổng hợp](https://cottrillresearch.com/various-survey-statistics-workers-spend-too-much-time-searching-for-information/) |
| S10 | SMB (≤500 người) trung bình 162 app SaaS; toàn thị trường ~275 app/công ty, ~7 app mới/tháng; phần lớn app nằm ngoài kiểm soát IT | [Zylo 2025](https://zylo.com/?p=8874), [ChiefMartec](https://chiefmartec.com/2024/02/well-saas-tech-stacks-shrank-from-2023-to-2024-but-only-by-8-you-were-expecting-more/) |
| S11 | Chỉ 12% nhân viên đồng ý mạnh rằng công ty onboarding tốt; nhân sự mới mất ~12 tháng để đạt hiệu suất tối đa | [Gallup](https://www.gallup.com:443/file/workplace/247076/Gallup_Perspective_on_Creating_an_Exceptional_Onboarding_Journey_for_Your_New_Employees.pdf) |
| S12 | Tỷ lệ dự định nghỉ việc ở VN: 19,1% (2017) → 20,5% (2018) → 23,7% (dự báo 2019), cao nhất ở nhân sự <2 năm thâm niên | [Anphabe qua Báo Đầu tư](https://baodautu.vn/anphabe-bao-dong-ty-le-nghi-viec-gia-tang-voi-24-trong-nam-2019-d108436.html) |
| S13 | Nhân viên cũ vẫn còn truy cập dữ liệu: 14% (Cloakware 2009) đến ~1/3 (Centrify 2015) | [DarkReading](https://www.darkreading.com/vulnerabilities-threats/cloakware-survey-many-former-employees-still-have-access-to-data-after-they-leave), [Enterprise Times](https://www.enterprisetimes.co.uk/2015/07/15/centrify-highlights-ex-employees-access-to-data/) |
| S14 | Bảng tính: kiểm toán 54 file thì 49 file (91%) có lỗi; ≥1% ô công thức có lỗi | [Panko qua Computerworld](https://www.computerworld.com/article/1704893/sidebar-oops.html), [Panko arXiv](https://arxiv.org/pdf/0802.3457) |
| S15 | Phê duyệt hóa đơn: ~54% bị trễ "thường xuyên/rất thường xuyên" do quy trình thủ công | [AI in AP Survey 2025 (Fujifilm)](https://betterbusiness-fbnz.fujifilm.com/wp-content/uploads/2025/02/AI-in-AP-Survey-2025.pdf) — vendor |
| S16 | Luật BVDLCN 91/2025/QH15 hiệu lực 01/01/2026, phạt tới 5% doanh thu năm liền trước với vi phạm nghiêm trọng; phải xóa dữ liệu NLĐ sau chấm dứt HĐ | [LSVN](https://lsvn.vn/xam-pham-du-lieu-ca-nhan-co-the-bi-phat-toi-da-den-5-doanh-thu-nam-lien-ke-a160152.html), [KPMG](https://kpmg.com/vn/vi/home/phan-tich-chuyen-sau/2025/06/vietnam-new-personal-data-protection-law.html) |
| S17 | Luật Giao dịch điện tử 20/2023/QH15 hiệu lực 01/07/2024: giao dịch điện tử mọi lĩnh vực, chữ ký số có giá trị pháp lý | [VnEconomy](https://vneconomy.vn/8-diem-moi-co-ban-cua-luat-giao-dich-dien-tu-2023.htm) |
| S18 | NĐ 70/2025: hộ KD doanh thu ≥1 tỷ phải dùng HĐĐT từ máy tính tiền từ 01/06/2025 | [MISA meInvoice](https://www.meinvoice.vn/?p=31627) |

---

## 1–2. Top 14 pain points (kèm không gian H/P/D/I)

Thang "Tần suất": Hằng ngày / Hằng tuần / Hằng tháng / Theo sự kiện. "Chi phí" ghi dạng định tính hoặc ước lượng; ước lượng đánh dấu [Suy luận].

| ID | Pain point | Ai chịu | Bằng chứng | Tần suất | Chi phí | HPDI |
|---|---|---|---|---|---|---|
| **PP1** | **Phê duyệt tắc nghẽn** — đề xuất (mua hàng, tạm ứng, nghỉ phép) đi qua Zalo/giấy/email, không biết đang kẹt ở ai, sếp đi công tác là đứng | Nhân viên đề xuất, quản lý cấp trung, kế toán | S15; Base Request định vị đúng nỗi đau này, có cả "SLA cho từng người duyệt", "ủy quyền duyệt" ([Base help](https://help.base.vn/support/solutions/articles/63000284976-base-request-introduce-base-request-and-table-of-contents)); Lark Approval có auto-escalation ([Lark blog](https://www.larksuite.com/en_us/blog/document-approval-workflow)) | Hằng ngày | Chậm 3–7 ngày/chu kỳ duyệt (ước tính vendor [Peakflo](https://peakflo.co/blog/approval-workflow-bottlenecks-manual-routing)); mất chiết khấu, trễ đơn hàng | **P** |
| **PP2** | **Giao việc rơi rớt** — giao miệng/qua chat, không deadline, không người chịu trách nhiệm rõ, phải hỏi "xong chưa?" liên tục | Quản lý, trưởng nhóm, nhân viên | S8 (60% thời gian là work-about-work); Base Wework, AMIS Công việc, 1Office Workplace đều là module lõi ([Viettimes](https://viettimes.vn/base-work-co-gi-dac-biet-ma-hang-nghin-doanh-nghiep-viet-dang-su-dung-post118507.html), [ITviec 1Office](https://itviec.com/companies/1office-workway)) | Hằng ngày | Lớn nhất về giờ công [Suy luận: DN 50 người mất ~1–2 giờ/người/ngày cho đuổi trạng thái] | **H→P** |
| **PP3** | **Ốc đảo dữ liệu & nhập liệu trùng** — nhiều phần mềm/Excel rời rạc, cùng một khách hàng/nhân viên nhập nhiều nơi | Nhân viên văn phòng, kế toán, sales admin | S2, S10; khảo sát quốc tế: 3/4 tổ chức có ≥50% dữ liệu nằm trong silo ([WANdisco/Datanami](https://www.datanami.com/2022/05/05/data-going-to-waste-wandisco-says/)); đây chính là "thái cực 1" của đề thi | Hằng ngày | Giờ công nhập lại + lỗi lệch số | **D** (gốc ở P) |
| **PP4** | **Không tìm thấy thông tin/tài liệu** — hợp đồng, quy định, mẫu biểu nằm rải trong Zalo, ổ cứng cá nhân, email | Mọi nhân sự, nhất là người mới | S9 (~25% tuần làm việc) | Hằng ngày | ~9 giờ/người/tuần (số Atlassian, thị trường Fortune 1000; ở SME VN [Suy luận] có thể thấp hơn nhưng tỷ lệ tương tự vì ít hệ thống quản lý tri thức) | **H / I** |
| **PP5** | **Báo cáo thủ công, chậm & sai** — cuối tuần/tháng gom Excel từ các phòng, lãnh đạo ra quyết định theo cảm tính | Giám đốc, kế toán, trưởng phòng | S5 (63,5% DN tăng trưởng cần phân tích/báo cáo); S14 (91% file Excel có lỗi) | Hằng tuần/tháng | 1–3 ngày công/kỳ báo cáo [Suy luận]; rủi ro quyết định sai | **D** |
| **PP6** | **Thanh toán/tạm ứng/hoàn ứng rối** — chứng từ giấy, hóa đơn thất lạc, kế toán đuổi hoàn ứng, không biết ngân sách còn bao nhiêu | Nhân viên đi công tác/mua hàng, kế toán, CFO | S15; AMIS/1Office/Base đều có luồng đề nghị thanh toán ([MISA AMIS](https://amis.misa.vn/6469/phan-mem-quan-ly-doanh-nghiep/)); NĐ 70/2025 & HĐĐT làm việc đối soát chứng từ bắt buộc chặt hơn (S18) | Hằng tuần | Sai sót/thất thoát chi phí, phạt thuế khi chứng từ không hợp lệ | **P** (+D) |
| **PP7** | **Onboarding/offboarding chắp vá** — người mới đến không có máy, tài khoản, không biết việc; người nghỉ vẫn còn quyền truy cập, mang theo dữ liệu khách hàng | HR, IT (thường kiêm nhiệm), quản lý trực tiếp | S11, S12, S13; S16 (nghĩa vụ xóa dữ liệu NLĐ) | Theo sự kiện, nhưng thường xuyên do biến động ~20%/năm | Mất ~12 tháng để đạt năng suất; rủi ro rò rỉ dữ liệu, phạt BVDLCN | **H + P** |
| **PP8** | **Pipeline bán hàng nằm trong đầu/điện thoại sales** — sales nghỉ là mất khách, không dự báo được doanh thu, chăm sóc khách không đều | Giám đốc kinh doanh, chủ DN | S5 (60,7% cần CRM); CRM là module lõi ở Odoo, ERPNext, AMIS, 1Office ([Wikipedia ERPNext](https://en.wikipedia.org/wiki/ERPNext)) | Hằng ngày | Mất doanh thu khi sales nghỉ (gắn với S12) [Suy luận] | **P + D** |
| **PP9** | **Soạn – trình ký – ban hành văn bản thủ công** — hợp đồng, quyết định, công văn: soạn Word, in, chạy ký, scan, cấp số bằng sổ tay | Hành chính, pháp chế, lãnh đạo | S17 (luật cho phép ký số mọi giao dịch); e-Office là một năng lực được đề thi nêu đích danh trong không gian [P] | Hằng tuần | Thời gian chạy ký; sai phiên bản; lộ văn bản | **P** (+D lưu trữ pháp lý) |
| **PP10** | **Hỗ trợ nội bộ không có đầu mối** — hỏi IT/HR/hành chính qua chat, câu hỏi lặp lại (quy định nghỉ phép, mật khẩu, máy in) | Nhân viên; bộ phận hỗ trợ kiêm nhiệm | S4 (43,7% DN có <3 nhân sự IT — người IT bị ngắt quãng liên tục) [Suy luận cho phần "câu hỏi lặp lại"] | Hằng ngày | Ngắt quãng công việc của nhân sự then chốt | **H → I** |
| **PP11** | **Phần mềm mua về không ai dùng / bỏ dở** — giải pháp không phù hợp, cồng kềnh, nhân viên quay lại Excel/Zalo | Chủ DN (mất tiền), nhân viên (phải học nhiều UI) | S4 (>48% dừng CĐS vì giải pháp không phù hợp); S7; VIR: nhân viên "không dùng hoặc dùng chiếu lệ" phần mềm được cấp | Theo dự án | Tiền license + chi phí triển khai chìm; "thái cực 2" — con tin nhà cung cấp | **Toàn bộ** (gốc: H) |
| **PP12** | **Phụ thuộc vào cá nhân then chốt, quy trình không thành văn** — chỉ "chị kế toán" biết cách làm, quy trình nằm trong đầu | Chủ DN | [vnbusiness](https://vnbusiness.vn/lo-doanh-nghiep-nho-that-bai-trong-chuyen-doi-so.html) (văn hóa, thói quen); S3 (52,3% khó thay đổi thói quen) [Suy luận về "không thành văn"] | Liên tục | Rủi ro gián đoạn khi người đó nghỉ (S12) | **P** |
| **PP13** | **Không có dấu vết kiểm toán / phân quyền lỏng** — ai sửa số, ai duyệt, ai xem dữ liệu nhạy cảm (lương, khách hàng) không truy được | Chủ DN, kế toán trưởng, kiểm toán | S13, S16; VCCI: lo ngại an ninh mạng, lộ dữ liệu là rào cản (S1) | Liên tục, bùng phát khi có sự cố | Tranh chấp, gian lận, phạt tới 5% doanh thu (S16) | **P + D** |
| **PP14** | **Thiếu nhân lực & ngân sách CNTT để vận hành/tùy biến** — không có người cấu hình workflow, viết báo cáo, tích hợp | Chủ DN | S3 (60,1% chi phí), S4 (56,3% <3 người CĐS) | Liên tục | Chặn mọi bước tiến lên D và I | **Toàn bộ** |

### Ma trận HPDI tóm tắt

| Không gian | Pain points | Ý nghĩa cho xT |
|---|---|---|
| **H** – Con người số | PP2, PP4, PP7, PP10, PP11 | Kênh tương tác = chat với LLM; giảm "học UI mới" về 0 |
| **P** – Quy trình số | PP1, PP6, PP7, PP8, PP9, PP12, PP13 | Lõi của xT: state machine phê duyệt, SLA, rule, audit |
| **D** – Dữ liệu số | PP3, PP5, PP8, PP13 | Một data model thống nhất → báo cáo sinh tự động |
| **I** – Trí tuệ số | PP4, PP10, PP5 (phân tích), PP14 (agent bù nhân lực) | Agent dùng tool MCP, RAG trên tài liệu nội bộ |

Quan sát [Suy luận]: phần lớn nỗi đau nằm ở **P**, nhưng DN 10–200 người thường **chưa xong H** (S1: 69% mới dùng email/kế toán). Chuỗi HPDI tuần tự khiến DN nhỏ mất nhiều năm mới tới I. Luận điểm của xT: **dùng I (agent) làm giao diện để kéo H và P lên cùng lúc**, D là sản phẩm phụ tự nhiên của P được số hóa.

---

## 3. Bộ 8 workflow lõi mà mọi doanh nghiệp đều có

Đối chiếu module "lõi" của các sản phẩm tham chiếu:

| Workflow | Base.vn | 1Office | MISA AMIS | Lark | Odoo / ERPNext |
|---|---|---|---|---|---|
| W1 Đề xuất & phê duyệt | Base Request | Workplace (đề xuất) | Văn phòng số – Quy trình | Approval | Approvals app / Workflow |
| W2 Giao việc & SLA | Base Wework | Workplace (công việc) | Công việc – Dự án | Tasks/Base | Project / Projects |
| W3 Vòng đời nhân sự | Base HRM+ (20+ app) | HRM | AMIS Nhân sự | (qua Base) | Employees, Recruitment / HR |
| W4 Thanh toán – tạm ứng – chi phí | Request + Finance | Workplace/Advance | AMIS Kế toán | Approval | Expenses / Accounting |
| W5 Pipeline bán hàng | Base CRM | CRM | AMIS CRM | Base | CRM / Selling |
| W6 Văn bản – trình ký – ban hành | Base Office/Request | Workplace (văn bản) | Văn phòng số | Docs + Approval | Documents, Sign |
| W7 Helpdesk nội bộ | Base Service | — | — | Helpdesk | Helpdesk / Frappe Helpdesk |
| W8 Báo cáo định kỳ | Báo cáo từng app | Dashboard | Báo cáo hợp nhất | Base dashboard | Reporting |

Nguồn: [Base Request help](https://help.base.vn/support/solutions/articles/63000284976-base-request-introduce-base-request-and-table-of-contents), [Viettimes về Base Wework/Workflow](https://viettimes.vn/base-work-co-gi-dac-biet-ma-hang-nghin-doanh-nghiep-viet-dang-su-dung-post118507.html), [SGGP về Base HRM+](https://www.sggp.org.vn/share573407.html), [ITviec 1Office](https://itviec.com/companies/1office-workway), [FPT Shop 1Office](https://fptshop.com.vn/tin-tuc/danh-gia/1office-la-gi-187983), [MISA AMIS](https://amis.misa.vn/6469/phan-mem-quan-ly-doanh-nghiep/), [Lark blog](https://www.larksuite.com/en_us/blog/document-approval-workflow), [ERPNext docs](https://docs.frappe.io/erpnext/accounting/introduction), [Wikipedia ERPNext](https://en.wikipedia.org/wiki/ERPNext). Một số ô (tên app cụ thể của Base Service, Odoo Approvals/Sign/Helpdesk, Lark Helpdesk) là [Suy luận từ hiểu biết sản phẩm], chưa xác minh trong phiên nghiên cứu này.

Nhận xét: **cả 5 nhà cung cấp đều hội tụ về cùng một bộ lõi** — đây là bằng chứng mạnh rằng 8 workflow trên là "phổ quát". Khác biệt: họ bán chúng thành **nhiều app riêng** (Base 40+ app, AMIS 40+ phần mềm), tức là tái tạo thái cực 1 bên trong một vendor (thái cực 2).

Ký hiệu **[HITL]** = bước bắt buộc con người thực hiện, agent không được tự làm.

### W1. Đề xuất & phê duyệt đa cấp (generic) — giải PP1, PP12, PP13

- **Trigger:** nhân viên có nhu cầu (mua sắm, nghỉ phép, làm thêm giờ, công tác, cấp tài sản, ngoại lệ chính sách...).
- **Bước:** (1) chọn loại đề xuất → (2) điền form (trường theo loại) + đính kèm → (3) hệ thống tính tuyến duyệt từ rule → (4) từng người duyệt: Duyệt / Từ chối / Trả lại bổ sung / Ủy quyền → (5) hết SLA thì nhắc → leo thang → (6) kết quả cuối kích hoạt hành động hạ nguồn (tạo task, tạo bút toán, cập nhật số ngày phép) → (7) lưu vết.
- **Vai trò:** Người đề xuất, Quản lý trực tiếp, Người duyệt theo điều kiện (trưởng phòng, kế toán trưởng, GĐ), Người theo dõi (CC), Admin cấu hình loại đề xuất.
- **Rule điển hình:** ngưỡng giá trị (≤5tr trưởng phòng, ≤50tr GĐ — giống "value-based routing" của Lark); duyệt tuần tự vs song song; SLA mỗi bước (Base có "SLA cho từng người duyệt"); ủy quyền khi vắng mặt; không tự duyệt đề xuất của chính mình; ngân sách còn lại; người duyệt không được sửa nội dung, chỉ trả lại.
- **Agent làm được:** chuyển câu chat ("cho em nghỉ thứ 6 tuần sau") thành đề xuất có cấu trúc; tự điền trường từ ngữ cảnh (số ngày phép còn, mã dự án); kiểm tra thiếu chứng từ trước khi gửi; tóm tắt đề xuất + lịch sử tương tự cho người duyệt ("3 lần mua mực in gần nhất giá 1,2tr, lần này 2,5tr"); nhắc & leo thang; trả lời "đề xuất của tôi đang ở đâu?".
- **[HITL]:** quyết định Duyệt/Từ chối của người có thẩm quyền; cấu hình/sửa rule ngưỡng duyệt.
- **Entities:** RequestType (schema form + policy), Request, ApprovalStep, Decision, Attachment, Comment, Delegation, AuditEvent.

### W2. Giao việc & theo dõi có SLA — giải PP2, PP12

- **Trigger:** quản lý giao việc; việc sinh tự động từ W1/W3/W5/W7; việc định kỳ (lịch).
- **Bước:** tạo task (người làm, deadline, tiêu chí xong) → người nhận xác nhận → cập nhật tiến độ/checklist → báo blocker → nộp kết quả → người giao nghiệm thu / trả lại → đóng.
- **Vai trò:** Người giao, Người thực hiện, Người phối hợp, Người theo dõi.
- **Rule:** mỗi task đúng 1 người chịu trách nhiệm chính; SLA theo loại; quá hạn → nhắc người làm → báo người giao; task định kỳ (RRULE); phụ thuộc giữa task.
- **Agent làm được:** tách một chỉ đạo ("chuẩn bị hội nghị khách hàng 20/11") thành danh sách task + đề xuất người làm theo vai trò/tải; nhắc nhở cá nhân hóa; tổng hợp "standup" mỗi sáng; phát hiện task quá hạn có rủi ro; trả lời "tuần này team Sales đang kẹt gì?".
- **[HITL]:** nghiệm thu kết quả (chấp nhận/không); phân việc cho người khác khi agent chỉ đề xuất.
- **Entities:** Task, Checklist item, Assignment, Schedule (recurrence), Comment, Attachment, Notification.

### W3. Vòng đời nhân sự: onboarding / thay đổi / offboarding — giải PP7, PP13, PP4

- **Trigger:** ứng viên nhận offer (onboarding); điều chuyển/thăng chức; đơn nghỉ việc được duyệt (W1) hoặc quyết định chấm dứt HĐ.
- **Bước onboarding:** tạo hồ sơ nhân sự → sinh checklist liên phòng ban (HR: HĐLĐ, BHXH; IT/hành chính: email, tài khoản, thiết bị; Quản lý: buddy, kế hoạch 30-60-90) → cấp quyền theo vai trò → ngày đầu: gửi tài liệu chào mừng → review cuối thử việc.
- **Bước offboarding:** xác nhận ngày nghỉ → bàn giao công việc (chuyển task, khách hàng, tài liệu) → thu hồi tài sản → **thu hồi toàn bộ quyền truy cập** → quyết toán lương/phép → xóa/ẩn danh dữ liệu cá nhân theo thời hạn (S16) → phỏng vấn nghỉ việc.
- **Vai trò:** HR, Quản lý trực tiếp, IT/Hành chính, Kế toán, Nhân sự mới/nghỉ việc.
- **Rule:** checklist theo template vai trò/phòng ban; quyền cấp theo Role, không cấp lẻ; offboarding không đóng được khi còn quyền chưa thu hồi/tài sản chưa trả; SLA ngày (ví dụ thu hồi quyền trong ngày nghỉ cuối).
- **Agent làm được:** tạo hồ sơ từ CV/offer (OCR); sinh checklist và giao task tự động; trợ lý hỏi-đáp cho người mới trên tài liệu nội bộ (RAG — "xin nghỉ phép thế nào?"); liệt kê mọi thứ người nghỉ đang nắm (task mở, khách hàng, tài liệu) và đề xuất người nhận bàn giao.
- **[HITL]:** quyết định tuyển/chấm dứt; xác nhận bàn giao; quyết toán tiền.
- **Entities:** Person/Employee, Role, Membership (Org unit), Permission grant, Asset, Contract (Document), Task, Schedule.

### W4. Thanh toán – tạm ứng – hoàn ứng – chi phí — giải PP6, PP1, PP13

- **Trigger:** cần chi tiền (mua hàng, công tác); hóa đơn nhà cung cấp về; đến hạn hoàn ứng.
- **Bước:** đề nghị tạm ứng/thanh toán (là một RequestType của W1) → kiểm tra ngân sách → duyệt theo ngưỡng → kế toán kiểm chứng từ → chi (chuyển khoản/tiền mặt) → ghi sổ → (tạm ứng) nộp chứng từ hoàn ứng trong N ngày → đối trừ → đóng.
- **Vai trò:** Người đề nghị, Quản lý, Kế toán thanh toán, Kế toán trưởng, Thủ quỹ, GĐ.
- **Rule:** hóa đơn phải là HĐĐT hợp lệ (MST, ký số) khi vượt ngưỡng; không tạm ứng mới khi còn khoản quá hạn hoàn ứng; tách người duyệt – người chi; ngân sách theo phòng/dự án; chống trùng hóa đơn (cùng số + MST).
- **Agent làm được:** OCR hóa đơn/biên lai → điền đề nghị; kiểm tra trùng, đối chiếu MST; nhắc hoàn ứng; trả lời "ngân sách marketing quý này còn bao nhiêu?"; soạn đề nghị chi hàng loạt.
- **[HITL]:** phê duyệt chi; **lệnh chi tiền thực tế** (agent không được tự chuyển tiền); xác nhận chứng từ hợp lệ khi có nghi vấn.
- **Entities:** Request (PaymentRequest/Advance), Ledger entry (bút toán bất biến), Budget, Invoice (Document + dữ liệu bóc tách), Party (nhà cung cấp), Attachment.

### W5. Pipeline bán hàng (lead → deal → hợp đồng → thu tiền) — giải PP8, PP3, PP5

- **Trigger:** có lead (form web, Zalo OA, giới thiệu, hội chợ).
- **Bước:** ghi nhận lead → phân bổ cho sales → liên hệ/chăm sóc (log hoạt động) → báo giá (có thể cần duyệt chiết khấu qua W1) → chốt → hợp đồng (W6) → giao hàng/dịch vụ → xuất HĐĐT → theo dõi công nợ → chăm sóc sau bán.
- **Vai trò:** Sales, Trưởng nhóm sales, Kế toán công nợ, GĐ.
- **Rule:** lead không được "treo" quá N ngày không có hoạt động; chiết khấu > X% phải duyệt; khách hàng thuộc về công ty, không thuộc về sales (quyền xem theo vai trò); công nợ quá hạn → nhắc.
- **Agent làm được:** tạo/cập nhật lead từ đoạn chat/ghi chú cuộc gọi; gợi ý bước tiếp theo; soạn báo giá theo bảng giá; nhắc follow-up; dự báo doanh thu tháng; tóm tắt lịch sử khách cho sales mới tiếp nhận (gắn W3 offboarding).
- **[HITL]:** cam kết giá/điều khoản với khách; duyệt chiết khấu; ký hợp đồng.
- **Entities:** Party (Customer/Contact), Deal (Record có stage), Activity (Event), Quote/Contract (Document), Invoice, Ledger (công nợ), Task.

### W6. Soạn thảo – trình ký – ban hành – lưu trữ văn bản — giải PP9, PP4, PP13

- **Trigger:** cần hợp đồng, quyết định, công văn, thông báo, biên bản.
- **Bước:** chọn mẫu → trộn dữ liệu (từ Record liên quan) → soạn/chỉnh → rà soát (pháp chế/trưởng phòng) → trình ký (W1 với Decision = ký) → ký số → cấp số văn bản theo sổ → ban hành/gửi → lưu trữ bất biến + phân quyền xem → nhắc hạn (hợp đồng hết hạn).
- **Vai trò:** Người soạn, Người rà soát, Người ký (có thẩm quyền), Văn thư, Người nhận.
- **Rule:** số văn bản liên tục, không trùng, không nhảy (counter theo loại + năm); bản đã ký không sửa được (chỉ phát hành bản thay thế); người ký đúng thẩm quyền; phiên bản có lịch sử.
- **Agent làm được:** soạn nháp từ mẫu + dữ liệu ("soạn HĐLĐ cho Nguyễn Văn A, lương 15tr"); so sánh khác biệt giữa phiên bản; rà điều khoản bất thường so với mẫu chuẩn; tìm kiếm ngữ nghĩa kho văn bản; nhắc hợp đồng sắp hết hạn.
- **[HITL]:** ký (chữ ký số là hành vi pháp lý của con người — S17); duyệt nội dung cuối; ban hành ra bên ngoài.
- **Entities:** Template, Document (version), Signature, Counter/Sequence, Archive record (hash), Request/Decision.

### W7. Helpdesk nội bộ (IT/HR/Hành chính) — giải PP10, PP4

- **Trigger:** nhân viên cần hỗ trợ (quên mật khẩu, máy hỏng, hỏi chính sách, xin văn phòng phẩm).
- **Bước:** gửi yêu cầu (chat) → phân loại → agent thử trả lời từ kho tri thức → nếu không giải quyết: tạo ticket, gán bộ phận → xử lý theo SLA → xác nhận đóng → (tùy) cập nhật kho tri thức.
- **Vai trò:** Nhân viên, Nhân sự hỗ trợ IT/HR/HC, Trưởng bộ phận.
- **Rule:** SLA theo mức ưu tiên; ticket chưa xác nhận đóng tự đóng sau N ngày; câu hỏi lặp lại ≥ K lần → đề xuất viết FAQ.
- **Agent làm được:** trả lời tier-0 bằng RAG trên quy chế, sổ tay; phân loại & định tuyến; tóm tắt ticket; đề xuất bài FAQ mới.
- **[HITL]:** thao tác có tác động hệ thống thật (reset quyền quản trị, cấp tài sản); ban hành câu trả lời chính sách mới vào kho tri thức.
- **Entities:** Ticket (= Request loại hỗ trợ), Task, KnowledgeArticle (Document), Notification.

### W8. Báo cáo định kỳ & điều hành — giải PP5, PP3

- **Trigger:** lịch (sáng thứ 2, ngày 5 hằng tháng); câu hỏi ad-hoc của lãnh đạo.
- **Bước:** chốt số liệu kỳ (snapshot) → tính chỉ số → sinh bản tin + bất thường → gửi người nhận theo quyền → lãnh đạo hỏi đào sâu → ra chỉ đạo → chỉ đạo thành task (W2).
- **Vai trò:** GĐ, Trưởng phòng, Kế toán.
- **Rule:** số liệu kỳ đã chốt là bất biến (đề thi: "đóng băng số liệu thống kê theo chuỗi thời gian"); mỗi người chỉ thấy số trong phạm vi quyền; mỗi con số phải truy được nguồn (lineage).
- **Agent làm được:** soạn bản tin tuần bằng ngôn ngữ tự nhiên từ dữ liệu W1–W7 (số đề xuất tồn, task quá hạn, pipeline, chi phí); trả lời truy vấn ad-hoc (NL → truy vấn có kiểm soát quyền); nêu bất thường; chuyển chỉ đạo thành task.
- **[HITL]:** ra quyết định quản trị dựa trên báo cáo; định nghĩa/sửa công thức KPI.
- **Entities:** Metric definition, Snapshot, Report (Document), Schedule, Notification, Task.

> Quan sát [Suy luận]: W4, W7 thực chất là **chuyên biệt hóa của W1** (Request + Approval) cộng một Ledger hoặc KB; W3, W5 là **chuỗi W1 + W2 trên một Record có vòng đời**; W8 là **sản phẩm của D**. Nghĩa là nếu xT làm thật tốt 3 động cơ (Request/Approval, Task/SLA, Record-lifecycle) thì 8 workflow chỉ là **template cấu hình**, không phải module code riêng.

---

## 4. Primitive phổ quát — kiểm chứng và đề xuất chỉnh

Danh sách ban đầu: Record, Request, Approval, Task, Document, Schedule/Event, Notification, Ledger. Đánh giá:

| Primitive gốc | Đánh giá | Đề xuất |
|---|---|---|
| Record | Đúng, là nền | Giữ. Record có `type` (schema động), `state` (vòng đời), owner, quan hệ. Customer, Deal, Asset, Employee đều là Record. |
| Request | Đúng | Giữ. Request = Record đặc biệt có người yêu cầu + tuyến xử lý. Ticket helpdesk cũng là Request. |
| Approval | Nên tách rõ | Đổi thành **Decision** (một bước trên Request: approve/reject/return/delegate/sign). Ký văn bản = Decision có chữ ký. Tránh nhầm "Approval" là một thực thể đứng riêng. |
| Task | Đúng | Giữ. Phân biệt với Request: Task = "ai đó phải làm", Request = "ai đó phải quyết". |
| Document | Đúng | Giữ, kèm **version** và **hash** khi đã ký/ban hành. Tách **File/Attachment** (blob thuần) khỏi Document (có ngữ nghĩa, mẫu, số hiệu). |
| Schedule/Event | Gộp hai khái niệm khác nhau | Tách: **Schedule** (lịch lặp, deadline, SLA timer) và **Event** (sự kiện miền: "deal chuyển stage", dùng cho trigger + activity log). |
| Notification | Đúng nhưng là đầu ra | Giữ như primitive "kênh" (Zalo/email/chat client MCP). Ghi lại đã gửi/đã đọc để chứng minh "đã thông báo". |
| Ledger | Mơ hồ | Tách 2: **Ledger** (bút toán tiền/số lượng/ngày phép — append-only, cân đối) và **AuditLog** (ai làm gì lúc nào, kể cả agent — bất biến). Đề thi yêu cầu cả hai (sổ cái mật mã bất biến + kiểm toán hành vi). |

**Bổ sung còn thiếu:**

| Primitive mới | Lý do |
|---|---|
| **Party / Actor** (Person, Org unit, Role, AgentIdentity) | Mọi rule đều tham chiếu "ai". Agent phải là một Actor có danh tính riêng, quyền riêng, hành động nhân danh ai (delegated). |
| **Policy / Rule** | Ngưỡng duyệt, SLA, ai được xem gì, ràng buộc ("không tạm ứng khi còn nợ quá hạn"). Đây là chỗ DN tùy biến mà không cần code — và là rào chắn cho agent. |
| **Counter / Sequence** | Số văn bản, mã đề xuất, số chứng từ: yêu cầu pháp lý liên tục, không trùng. Nhỏ nhưng không suy ra được từ primitive khác. |
| **Comment / Thread** | Trao đổi gắn với đúng đối tượng — thay thế Zalo cho ngữ cảnh công việc (PP3, PP4). |
| **Snapshot / Metric** | Chốt số liệu kỳ bất biến cho W8; gốc của tầng D. |

**Bộ primitive khuyến nghị (12):** Actor, Record, Request, Decision, Task, Document, File, Comment, Schedule, Event, Notification, Policy — cộng 2 sổ hạ tầng: **Ledger** và **AuditLog** (+ Counter, Snapshot như tiện ích). Mỗi primitive tương ứng một nhóm MCP tool ổn định (`request.create`, `decision.submit`, `task.assign`...), còn workflow = template + Policy. [Suy luận — cần kiểm chứng khi model hóa W1–W8 thực tế]

---

## 5. Vì sao agent + MCP giải tốt hơn mua thêm SaaS (và rủi ro thật)

### 5.1. Gắn với "2 thái cực" của đề thi

| | Thái cực 1: nhiều SaaS rời rạc | Thái cực 2: ERP nguyên khối | xT: lõi mở + agent qua MCP |
|---|---|---|---|
| Dữ liệu | Ốc đảo, nhập trùng (S2, S10) | Thống nhất nhưng bị khóa trong vendor | Một data model chung (Record/Request/Task...) do DN sở hữu, self-host được |
| Giao diện | Mỗi app một UI, nhân viên học N UI | Một UI rất phức tạp | **Không có UI mới để học**: chat trong ChatGPT/Claude/LLM nội bộ (giải PP11, H) |
| Tùy biến | Phụ thuộc từng vendor | Cần consultant đắt (S7) | Workflow = template + Policy khai báo; agent hỗ trợ cấu hình bằng ngôn ngữ tự nhiên (giải PP14) |
| Lock-in | Nhiều hợp đồng nhỏ | "Con tin" một vendor | Apache-2.0 + MCP là chuẩn mở → đổi LLM/client không đổi lõi (chủ quyền số) |
| Chi phí | Cộng dồn per-seat | Đầu tư ban đầu lớn | Lõi miễn phí; chi phí chính là LLM (có thể chạy local — đúng lớp Ollama/vLLM trong đề) |

Luận điểm [Suy luận, có cơ sở từ S4, S8]:
1. **Agent là "lớp keo" tích hợp ngang**: nhân viên nói một câu, agent gọi nhiều tool (tạo đề xuất + task + nhắc lịch) — việc mà SaaS rời rạc bắt người dùng tự làm qua nhiều app.
2. **Đảo ngược chuỗi HPDI**: DN không cần "xong H rồi mới P rồi mới D" — chat là H, mỗi câu chat sinh dữ liệu có cấu trúc (P), dữ liệu tự tích lũy thành D, agent khai thác (I). Đây là cách vượt rào "khó thay đổi thói quen" (S3: 52,3%).
3. **Đúng kiến trúc đề**: xT là lõi headless Tầng 1 (identity, API, dữ liệu, workflow engine) — MCP chính là "API gateway" cho agent; ứng dụng minh họa Tầng 2 là không gian P (e-Office/phê duyệt) + I (cổng giám sát agent).
4. **Thị trường đã chứng minh nhu cầu**: Base/1Office/AMIS/Lark đều bán đúng 8 workflow lõi; xT không phát minh nhu cầu, chỉ thay cách tiếp cận.

### 5.2. Rủi ro trung thực

| Rủi ro | Mô tả | Giảm thiểu trong xT |
|---|---|---|
| **Prompt injection / tool poisoning** | Nội dung độc trong tài liệu, email, mô tả tool có thể điều khiển agent gọi tool sai; mức chống chịu giữa các MCP client khác nhau rất nhiều | Nguồn: [Trail of Bits](https://blog.trailofbits.com/2025/04/21/jumping-the-line-how-mcp-servers-can-attack-you-before-you-ever-use-them/), [Snyk](https://snyk.io/articles/mcp-security-whats-old-is-new-again/), [arXiv 2603.21642](https://arxiv.org/pdf/2603.21642). Giảm thiểu: quyền thực thi ở server (Policy), không tin vào LLM; hành động rủi ro cao chỉ tạo "đề xuất chờ xác nhận"; agent hành động nhân danh user với đúng quyền user |
| **Agent hallucinate / sai số** | Báo cáo sai, điền sai trường | Số liệu luôn lấy từ tool có kiểu, không để LLM tự tính tiền; trả kèm ID bản ghi để kiểm chứng |
| **Hype & hủy dự án** | Gartner: >40% dự án agentic AI bị hủy trước cuối 2027 vì chi phí, giá trị mơ hồ, quản trị rủi ro yếu; nhiều use case "không cần agent" | [Outlook Business](https://www.outlookbusiness.com/artificial-intelligence/over-40-of-agentic-ai-projects-will-be-scrapped-by-2027-says-gartner). Giảm thiểu: workflow phải chạy được **không cần LLM** (state machine thuần); agent là lớp tăng tốc, không phải điều kiện tồn tại |
| **Dữ liệu đi ra LLM cloud** | Lương, CCCD, khách hàng gửi lên ChatGPT/Claude → rủi ro theo Luật BVDLCN 2025 (chuyển dữ liệu ra nước ngoài, phạt tới 5% doanh thu) | S16. Giảm thiểu: che PII ở server trước khi trả cho LLM (đúng "động cơ che giấu PII" trong đề), hỗ trợ LLM local, ghi log mọi truy xuất dữ liệu nhạy cảm |
| **Chat không phù hợp mọi tác vụ** | Duyệt hàng loạt, xem bảng lớn, kanban vẫn cần giao diện trực quan | Cần một UI tối thiểu ở Tầng 2 (đề thi bắt buộc có ứng dụng minh họa) [Suy luận] |
| **Chi phí token & độ trễ** | Mỗi tương tác tốn tiền, chậm hơn bấm nút | Tool hạt thô (một lệnh làm trọn nghiệp vụ), cache, model nhỏ cho tác vụ đơn giản |
| **Trách nhiệm pháp lý** | Ai chịu trách nhiệm khi agent "duyệt" sai? | HITL cứng ở Decision/ký/chi tiền; AuditLog ghi phân biệt "agent đề xuất" vs "người xác nhận" |
| **Chấp nhận của người dùng** | 52,3% DN khó thay đổi thói quen; chủ DN lớn tuổi có thể chưa quen chat với AI | Kênh quen thuộc (Zalo/Telegram bot qua MCP client) [Suy luận] |

---

## 6. Khuyến nghị ưu tiên: 3 workflow ship trước

Tiêu chí: (a) độ phổ quát/tần suất, (b) chứng minh được kiến trúc lõi (identity, API, dữ liệu, workflow engine — 4 yêu cầu bắt buộc của đề), (c) ấn tượng demo trong 15 phút, (d) tái sử dụng primitive cho các workflow sau.

| Hạng | Workflow | Vì sao | Primitive được kiểm chứng |
|---|---|---|---|
| **1** | **W1 Đề xuất & phê duyệt đa cấp**, với template flagship **Tạm ứng/Thanh toán (W4)** + Nghỉ phép | Nỗi đau hằng ngày số 1 (PP1, PP6); trình diễn được workflow engine, rule ngưỡng, SLA/leo thang, phân quyền, audit bất biến; HITL rõ ràng — đúng thứ giám khảo muốn thấy | Request, Decision, Policy, Actor/Role, Ledger, AuditLog, File (OCR) |
| **2** | **W2 Giao việc & SLA** + **bản tin điều hành tự động (lát cắt mỏng của W8)** | Phủ PP2, PP5; biến kết quả W1 thành task; bản tin sáng thứ 2 chứng minh D và I "miễn phí" từ P | Task, Schedule, Notification, Event, Snapshot |
| **3** | **W3 Onboarding/Offboarding** | Workflow liên phòng ban điển hình (HR–IT–Kế toán–Quản lý) — khó làm bằng SaaS rời rạc nhất; offboarding "thu hồi mọi quyền + bàn giao + xóa PII" gắn trực tiếp Identity/SSO và Luật BVDLCN — điểm cộng về bảo mật & chủ quyền số | Record lifecycle, Role/Permission, Task fan-out, Document |

Không chọn trước: W5 CRM (thị trường đã bão hòa, cần UI nhiều), W6 ký số (phụ thuộc nhà cung cấp CA/HSM, tích hợp tốn thời gian — làm sau với mức "ký nội bộ + hash"), W7 helpdesk (tái sử dụng W1 + RAG, làm nhanh ở vòng 2).

### Kịch bản demo gợi ý (~6 phút trong 15 phút) [Suy luận]

1. Nhân viên chat trong Claude: "Em đi công tác Đà Nẵng 3 ngày, tạm ứng 8 triệu" → agent tạo đề xuất, tự điền dự án, báo "vượt ngưỡng 5tr nên cần GĐ duyệt sau trưởng phòng".
2. Trưởng phòng (client khác, ví dụ LLM local) hỏi "có gì cần tôi duyệt?" → agent tóm tắt + so sánh lịch sử → **người bấm duyệt** (HITL).
3. GĐ quá SLA → hệ thống tự nhắc, leo thang; GĐ duyệt qua chat.
4. Agent thử "tự duyệt" thay GĐ → **bị Policy chặn ở server**, ghi AuditLog (chứng minh zero-trust, không tin LLM).
5. Sau chuyến đi: chụp hóa đơn → OCR → hoàn ứng → Ledger đối trừ.
6. HR: "Anh B nghỉ từ thứ 6" → agent liệt kê task, khách hàng, quyền đang có của B → sinh checklist offboarding cho IT/Kế toán → ngày cuối, quyền bị thu hồi tự động.
7. Sáng thứ 2: GĐ nhận bản tin tự động: đề xuất tồn, task quá hạn, chi phí công tác tháng — mỗi số có link về bản ghi gốc.

Thông điệp chốt: **một lõi, một data model, nhiều LLM client — không thêm app nào cho nhân viên phải học.**
