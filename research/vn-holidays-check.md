<!-- SPDX-License-Identifier: Apache-2.0 -->
# Kiểm chứng lịch nghỉ lễ Việt Nam 2026–2027 (cho SLA ngày làm việc)

Ngày kiểm: 15/10/2026. agyxt (Gemini 3.8 Flash) lập danh sách, đối chiếu với thư viện `holidays` 0.106 (MIT, `holidays.VN`).

| Năm | agyxt | `holidays` | agyxt thiếu |
|---|---|---|---|
| 2026 | 11 ngày | 14 ngày | 27/04 (nghỉ bù Giỗ Tổ), 31/08 (nghỉ hoán đổi từ 22/08), 24/11 (Ngày Văn hóa Việt Nam) |
| 2027 | 11 ngày | 15 ngày | 04/02 (29 Tết), 10/02 (mùng 5 Tết), 03/05 (nghỉ bù 1/5), 24/11 (Ngày Văn hóa Việt Nam) |

agyxt không đưa ra ngày sai, nhưng **thiếu ngày nghỉ bù, ngày hoán đổi và ngày lễ mới**. Thực tế, ngày nghỉ hoán đổi do Chính phủ/Bộ LĐ-TB-XH công bố hằng năm.

## Quyết định cho xT
- **Không hard-code danh sách ngày nghỉ.** Lịch làm việc lấy từ `holidays.VN` (dependency MIT, cập nhật theo bản phát hành của thư viện).
- **Mỗi tenant được ghi đè bằng dữ liệu** (thêm hoặc bỏ ngày, ví dụ ngày hoán đổi hoặc nghỉ riêng của công ty). Việc ghi đè là thay đổi model/policy, theo luồng đề xuất → duyệt.
- **SLA tính theo ngày làm việc** dùng lịch đã hợp nhất. Mỗi lần tính lưu kèm phiên bản lịch vào audit.
- Đã thêm `holidays` vào THIRD_PARTY_NOTICES (MIT, xem `docs/LICENSING.md`).
