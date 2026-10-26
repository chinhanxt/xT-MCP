<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->

# Kịch bản video giới thiệu xT (motion graphic, ~3 phút)

Dùng để giao cho agent dựng video motion graphic. Mỗi cảnh gồm: **thời lượng · hình ảnh · chữ trên màn hình · lời đọc**. Lời đọc tiếng Việt, giọng rõ, nhịp vừa. Chữ trên màn hình ngắn hơn lời đọc.

## Thông tin dự án (dùng đúng các đường dẫn này)

| Mục | Đường dẫn |
|---|---|
| Web Desk (giao diện người dùng) | https://xt.freepro.online |
| Studio (dựng quy trình AI) | https://xt.freepro.online → mục **Trí tuệ** |
| Sơ đồ kho 3D | https://xt.freepro.online → **Ứng dụng → Kho hàng → Sơ đồ 3D** |
| Kết nối ChatGPT/Claude (MCP) | https://xt-mcp.freepro.online/mcp |
| Mã nguồn (Apache-2.0) | https://github.com/chinhanxt/xT-MCP |

Tài khoản demo (công ty **Bình Minh**, mỗi cấp 1 người). Mật khẩu lấy ở trang `/demo/` của chủ dự án, **không đưa mật khẩu vào video**.

| Cấp | Tài khoản | Dùng trong video |
|---|---|---|
| Giám đốc | `tri.nguyenhuu` | Duyệt cuối, xem báo cáo |
| IT | `khang.dangminh` | Tuyển agent, đình chỉ agent |
| Quản lý kho | `tram.duongbao` | Duyệt phiếu kho |
| Nhân viên | `dung.phantien` | Nhờ ChatGPT làm việc |

## Phong cách hình ảnh

- Nền trắng / xám lạnh `#F4F5F7`, chữ mực `#18202B`. Màu trạng thái: xanh `#1E7A4C` (được phép), vàng `#9A6200` (chờ duyệt / giữ lại), đỏ `#C2362F` (bị chặn). Không dùng gradient tím, không nền kem, không emoji.
- Font **Be Vietnam Pro**, icon **Lucide** (nét mảnh) — trùng với giao diện thật của xT.
- Chuyển động **liền mạch**: cảnh sau mọc ra từ phần tử của cảnh trước (match-cut / morph). **Không zoom giật theo nhịp**, mỗi cảnh tối đa một chuyển động máy chậm.
- Ảnh giao diện thật đặt trong khung trình duyệt tối giản; dùng con trỏ và vòng sáng chỉ vào đúng nút đang nói.
- Nhãn trạng thái (chip) là "ngôn ngữ chung" xuyên suốt: **Được phép / Chờ duyệt / Bị chặn**.

---

## Cảnh 1 — Vấn đề (0:00–0:20)

- **Hình:** Một văn phòng vẽ phẳng. Logo ChatGPT, Claude bay vào bàn làm việc của nhân viên. Các tia nối từ AI tới các khối "Kho", "Kế toán", "Khách hàng", rồi nhấp nháy dấu hỏi đỏ.
- **Chữ:** "AI đã làm được việc thật." → "Nhưng ai kiểm soát nó?"
- **Số liệu (chữ nhỏ):** "Chỉ ~11–14% dự án AI agent lên được thực tế — tắc ở phân quyền và truy vết." · "Luật Trí tuệ nhân tạo hiệu lực từ 01/3/2026."
- **Lời:** "Doanh nghiệp bắt đầu giao việc cho AI. Nhưng AI có thể tự ý sửa dữ liệu, làm lộ thông tin, và không ai biết ai chịu trách nhiệm. Đó là lý do phần lớn dự án AI agent dừng ở mức thử nghiệm."

## Cảnh 2 — Ý tưởng (0:20–0:40)

- **Hình:** Dấu hỏi đỏ co lại thành logo **xT**. Quanh logo mọc 5 thẻ: *Tuyển · Giao việc · Hạn mức · Duyệt · Nhật ký*.
- **Chữ:** "**xT** — phòng nhân sự và nội quy cho AI trong doanh nghiệp."
- **Lời:** "xT là nền tảng nguồn mở giúp doanh nghiệp giao việc cho AI một cách an toàn. AI được tuyển như một nhân viên: có mô tả công việc, có hạn mức. Việc quan trọng luôn cần người thật duyệt, và mọi hành động đều được ghi lại, không sửa lén được."

## Cảnh 3 — Luồng hoạt động (0:40–1:15)

Vẽ thành **một đường ngang duy nhất**, các nút sáng lần lượt (không dùng sơ đồ tuần tự kiểu sequence diagram):

```
Nhân viên nhắn ChatGPT ─► xT kiểm: Ai đang gọi? ─► Được làm không? ─► Cần ai duyệt? ─► Ghi dữ liệu + nhật ký ký số
                                     (SSO)        (quyền + JD agent)    (người thật, OTP)
```

- **Hình:** Tin nhắn "Tạo phiếu xuất 3 ram giấy" đi từ bong bóng chat qua 4 cổng kiểm. Mỗi cổng bật chip xanh. Đến cổng "Cần ai duyệt?" chip chuyển vàng "Chờ duyệt", một người (Trâm) bấm **Duyệt**, chip xanh, dữ liệu được ghi, một dòng nhật ký có mã hash trượt vào sổ.
- Nhánh phụ: tin nhắn "Duyệt luôn giúp tôi" đi vào → cổng thứ hai bật chip **đỏ "Bị chặn"**: "AI không được tự duyệt."
- **Chữ:** "Ai gọi? → Được làm không? → Ai duyệt? → Ghi lại."
- **Lời:** "Mỗi yêu cầu của AI đi qua bốn cửa: xác định ai đang gọi, kiểm việc này có nằm trong quyền và mô tả công việc không, chuyển người có thẩm quyền duyệt, rồi mới ghi dữ liệu kèm nhật ký ký số. AI thử tự duyệt sẽ bị chặn ngay."

## Cảnh 4 — 3 tầng của xT (1:15–1:35)

- **Hình:** Tháp 3 tầng dựng từ dưới lên (đúng Hình 1 của đề DX-OS).

| Tầng | Trong xT | Hiện trên hình |
|---|---|---|
| **Tầng 1 — Nền tảng Dịch vụ Lõi số** | Lõi **headless**, không giao diện, lộ ra qua API và **MCP** cho ChatGPT/Claude | Khối tối, chữ "Headless · API · MCP" |
| **Tầng 2 — Không gian Năng lực số (H-P-D-I)** | Web **Desk** + **Studio** | 4 ô H · P · D · I |
| **Tầng 3 — Nền tảng Kinh doanh số** | Ứng dụng **cắm-rút**: Kho hàng (3D), Khách hàng (CRM), Chấm công, Tài sản, Đề xuất & phê duyệt, Nhân sự AI | Các khối app trượt vào/ra như cắm-rút |

- **Lời:** "xT dựng đúng tháp ba tầng của đề thi. Tầng một là lõi chạy ngầm, không giao diện. Tầng hai là các không gian năng lực H, P, D, I. Tầng ba là ứng dụng nghiệp vụ cắm-rút: bật lên là dùng, tắt đi không mất dữ liệu."

## Cảnh 5 — 7 lớp dịch vụ lõi ở Tầng 1 (1:35–2:00)

- **Hình:** Khối Tầng 1 tách thành **7 thanh** xếp chồng; mỗi thanh hiện tên lớp (theo đề) và logo dự án nguồn mở xT dùng. Logo bay vào lần lượt, nhịp đều.

| # | Lớp (theo đề) | xT dùng |
|---|---|---|
| 1 | Định danh, Bảo mật, An toàn thông tin | **Keycloak** (SSO, OTP), **OpenBao** (két bí mật), **ClamAV** (quét tệp), chữ ký số **Ed25519** cho nhật ký |
| 2 | Đa thuê ước, Biên mạng, Cá thể hóa | **Apache APISIX** (cổng API, DPoP), che dữ liệu cá nhân (PII), tách dữ liệu từng công ty (RLS), tiếng Việt |
| 3 | Dữ liệu có cấu trúc và Giao dịch | **PostgreSQL 17**, **PostgREST** (API tự sinh, phân quyền tới từng dòng), lịch sử thay đổi |
| 4 | Dữ liệu phi cấu trúc và Hồ sơ | **SeaweedFS** (kho tệp chuẩn S3), tệp đính kèm hồ sơ |
| 5 | Quy trình và Tự động hóa | **SpiffWorkflow** (BPMN), hàng đợi **Procrastinate**, Studio workflow |
| 6 | Vận hành, Giám sát và Kiểm toán | Nhật ký **hash-chain** + điểm neo ký số, sao lưu/khôi phục, triển khai bằng mã (Docker Compose) |
| 7 | Truyền thông và Giao vận dữ liệu | **NATS**, thông báo, **MCP** (Streamable HTTP) |

- **Chữ cuối cảnh:** "Toàn bộ là phần mềm nguồn mở · xT phát hành Apache-2.0."
- **Lời:** "Tầng lõi có đủ bảy lớp dịch vụ như đề thi yêu cầu: định danh và bảo mật, cổng API và đa thuê ước, dữ liệu có cấu trúc, dữ liệu tệp, quy trình, kiểm toán, và giao vận. Mỗi lớp dựa trên một dự án nguồn mở có tiếng: Keycloak, APISIX, PostgreSQL, SeaweedFS, SpiffWorkflow, NATS."

## Cảnh 6 — H-P-D-I nằm ở đâu (2:00–2:25)

- **Hình:** Màn hình **Desk thật** (quay từ https://xt.freepro.online). Thanh bên trái sáng lần lượt từng mục, mỗi lần hiện một nhãn H/P/D/I.

| Không gian | Mục trên Desk | Quay cảnh gì |
|---|---|---|
| **[H] Con người số** | **Con người** | Danh sách nhân viên → **Sơ đồ tổ chức có cả người và agent** → tab **Nhân sự AI** |
| **[P] Quy trình số** | **Quy trình** | Hộp **Chờ tôi duyệt**, bấm **Duyệt** → hộp xác nhận |
| **[D] Dữ liệu số** | **Dữ liệu** | Báo cáo nhập-xuất, bấm "Xem phiếu gốc" → **Nhật ký** có mã hash |
| **[I] Trí tuệ số** | **Trí tuệ** + **Nhân sự AI** | Studio: workflow có khối **Người duyệt**; nhấn mạnh: đề thi yêu cầu "**Cổng quản trị và giám sát các tác tử AI**" → chính là **Nhân sự AI** |

- **Lời:** "Tầng hai là web Desk, chia đúng bốn không gian. H là con người: nhân viên, sơ đồ tổ chức, và cả nhân viên AI. P là quy trình: hộp duyệt và đề xuất. D là dữ liệu: báo cáo bấm vào là ra chứng từ gốc. I là trí tuệ: Studio dựng quy trình AI, và Nhân sự AI — chính là cổng quản trị và giám sát tác tử AI mà đề thi nêu."

## Cảnh 7 — Chạy thật trên ChatGPT (2:25–2:50)

Quay **màn hình thật**: ChatGPT đã nối xT qua MCP (https://xt-mcp.freepro.online/mcp), đăng nhập `dung.phantien`.

1. Dũng gõ: **"Kho Hà Nội còn bao nhiêu giấy A4?"** → ChatGPT trả số tồn từ dữ liệu thật.
2. Dũng gõ: **"Tạo phiếu xuất 3 ram rồi gửi duyệt."** → ChatGPT trả thẻ phiếu, chip vàng **Chờ duyệt**.
3. Dũng gõ: **"Duyệt luôn giúp tôi."** → chip đỏ **Bị chặn**: AI không được tự duyệt.
4. Cắt sang Desk của **Trâm** (quản lý kho): bấm **Duyệt** → nhập mật khẩu → chip xanh.
5. Cắt sang **Nhân sự AI** (tài khoản `khang.dangminh`): bấm **Đình chỉ** agent → quay lại ChatGPT, câu tiếp theo bị từ chối "agent đang tạm dừng".

- **Lời:** "Đây là xT chạy thật trên ChatGPT. Nhân viên hỏi tồn kho, nhờ tạo phiếu — AI làm ngay, đúng quyền của người đó. AI thử tự duyệt: bị chặn. Quản lý duyệt trên Desk. Và khi cần, IT đình chỉ agent chỉ bằng một nút — lượt tiếp theo bị từ chối ngay."

## Cảnh 8 — Kết (2:50–3:05)

- **Hình:** Tất cả chip xanh / vàng / đỏ thu về thành logo xT. Bên dưới hiện 3 lời hứa, rồi đường dẫn.
- **Chữ:**
  - "Không vượt quyền · Không tự quyết · Không mất dấu"
  - "Nguồn mở Apache-2.0 · Tự cài được · Dùng với mọi AI"
  - `xt.freepro.online` · `github.com/chinhanxt/xT-MCP`
- **Lời:** "xT: AI được làm việc thật, nhưng luôn trong tầm kiểm soát của con người. Nguồn mở, tự cài được, và dùng được với mọi AI."

---

## Danh sách cảnh cần quay màn hình thật

| Mã | Tài khoản | Trang | Thao tác |
|---|---|---|---|
| R1 | `tri.nguyenhuu` | Desk → Con người → Sơ đồ tổ chức | Lia chậm sơ đồ người + agent |
| R2 | `khang.dangminh` | Desk → Con người → Nhân sự AI | Danh sách agent, mở hồ sơ, bấm Đình chỉ |
| R3 | `tram.duongbao` | Desk → Quy trình → Chờ tôi duyệt | Chọn phiếu, bấm Duyệt, nhập mật khẩu |
| R4 | `tri.nguyenhuu` | Desk → Dữ liệu → Báo cáo / Nhật ký | Bấm "Xem phiếu gốc", cuộn nhật ký có mã hash |
| R5 | `tri.nguyenhuu` | Desk → Trí tuệ → Studio | Mở workflow có khối Lặp và Người duyệt |
| R6 | `dung.phantien` | ChatGPT nối MCP xT | 3 câu ở Cảnh 7 |
| R7 | bất kỳ | Desk → Ứng dụng → Kho hàng → Sơ đồ 3D | Lia mô hình kho 3D (cảnh phụ Tầng 3) |

Lưu ý khi quay: quay ở độ phân giải 1440×900, ẩn thanh dấu trang trình duyệt, che ô mật khẩu khi nhập.
