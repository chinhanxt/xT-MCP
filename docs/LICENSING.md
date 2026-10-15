<!-- SPDX-License-Identifier: Apache-2.0 -->
# Chiến lược giấy phép của xT

Phục vụ tiêu chí PoF 2 (giấy phép nguồn mở, header, LICENSE) và 5 (tương thích, không vendor). Bảng đầy đủ từng thành phần: `THIRD_PARTY_NOTICES.md` (kiểm ngày 2026-10-15).

## 1. Giấy phép của xT
- Toàn bộ mã xT: **Apache-2.0**. Toàn văn ở `LICENSE`, thông báo ở `NOTICE`.
- Mỗi file có header SPDX (`SPDX-License-Identifier: Apache-2.0`); `REUSE.toml` gán cho tài liệu và file sinh. Kiểm bằng `uv run reuse lint`.
- Chỉ dùng phụ thuộc có giấy phép OSI. Loại: n8n (fair-code), Redpanda (BSL), mọi thứ SSPL/BSL.

## 2. Quy tắc "không vendor"
- Không chép mã, gói hay binary của dự án khác vào repo hoặc tarball `xt-<version>.tar.gz`.
- Thành phần bên thứ ba vào hệ thống theo đúng ba đường:
  1. **pip/npm dependency**: khai báo trong `pyproject.toml`/`package.json`, khóa bằng `uv.lock`/`package-lock.json`, người dùng cài lúc build.
  2. **Docker image**: kéo lúc cài, pin bằng digest, chạy như tiến trình riêng, nói chuyện với xT qua mạng.
  3. **Trọng số mô hình**: người dùng tự tải (`ollama pull qwen3.5:4b`), không nằm trong bản phát hành.
- Repo tham khảo không có LICENSE (sklite) chỉ để học pattern, không chép mã.
- Mọi dep phải có dòng trong `THIRD_PARTY_NOTICES.md` (tên, phiên bản, SPDX, OSI, cách dùng, URL, ngày kiểm) **trước** khi vào manifest (điều kiện B5). CI fail nếu dep runtime thiếu trong bảng.

## 3. Ma trận tương thích với Apache-2.0
| Nhóm | Ví dụ | Kết luận |
|---|---|---|
| Cho phép (MIT, BSD, Apache-2.0, PSF, PostgreSQL License) | mcp, pydantic, starlette, httpx, React, Keycloak, APISIX, PostgreSQL | Tương thích trực tiếp. Giữ nguyên thông báo bản quyền của họ khi phân phối lại (chúng ta không phân phối lại, chỉ khai báo dep). |
| Dual (Apache-2.0 OR BSD-3) | python-dateutil | Chọn Apache-2.0. |
| LGPL-3.0 dùng như thư viện | SpiffWorkflow, psycopg, psycopg-pool | Xem 3.1. |
| GPL-2.0 / MPL-2.0 chạy như dịch vụ | ClamAV, OpenBao | Xem 3.2. |
| Đang chuyển MIT sang Apache-2.0 | `mcp`, SDK/ext-apps TypeScript | Cả hai tương thích; theo dõi khi nâng bản. |
| Trọng số mô hình | Qwen3.5-4B (Apache-2.0) | Người dùng tải; không phân phối. |

### 3.1. LGPL-3.0 (SpiffWorkflow, psycopg)
LGPL cho phép phần mềm có giấy phép khác (kể cả Apache-2.0) **liên kết/import** thư viện mà không bị buộc đổi giấy phép của phần mềm đó, với điều kiện: (a) không sửa thư viện (hoặc công bố bản sửa theo LGPL), (b) người dùng có thể thay thế thư viện bằng bản khác. xT chỉ `import` bản gốc từ PyPI, không sửa, không vendor; người dùng đổi phiên bản bằng cách chạy lại `uv sync`. Vì vậy mã xT giữ Apache-2.0. Giữ nguyên tắc: không vá SpiffWorkflow/psycopg trong repo; nếu cần sửa, đóng góp ngược upstream.

### 3.2. GPL/MPL/AGPL chạy như dịch vụ (docker, qua mạng)
- ClamAV (GPL-2.0) và OpenBao (MPL-2.0) chạy trong container riêng; xT chỉ gọi qua socket/HTTP. Đây là "tập hợp" các chương trình độc lập, không phải tác phẩm phái sinh, nên copyleft không lan sang xT. xT không phân phối image của họ, chỉ ghi tên image để người dùng kéo.
- Nếu sau này thêm dịch vụ AGPL-3.0 (Metabase, đang ở roadmap): chỉ chạy nguyên bản, không sửa, không link; nghĩa vụ AGPL (cung cấp nguồn cho người dùng qua mạng) chỉ phát sinh khi sửa chính dịch vụ đó. Phải thêm dòng vào `THIRD_PARTY_NOTICES.md` trước.
- Cloudflare Tunnel: client `cloudflared` là Apache-2.0 nhưng dịch vụ Cloudflare là đóng; chỉ ở profile `public` tùy chọn, không nằm trên đường build/cài mặc định (có phương án APISIX TLS + certbot).

## 4. Điểm cần theo dõi (chưa đóng hết)
- Qwen3.5-4B: model card Hugging Face ghi Apache-2.0, nhưng bản quantize Q4 trong thư viện Ollama cần đối chiếu lại khi pin. Trọng số mở không đồng nghĩa dữ liệu huấn luyện mở (khác OSAID); xT không đóng gói trọng số.
- Lệch phiên bản giữa V2 và registry: Ollama, `casbin`, `@modelcontextprotocol/ext-apps`. Chốt khi scaffold và cập nhật bảng.
- Wheel `lxml` kèm libxml2/libxslt (MIT); WeasyPrint cần thư viện hệ thống (LGPL) do hệ điều hành cung cấp.
- Chuyển giấy phép của dự án MCP: kiểm lại LICENSE mỗi lần nâng phiên bản.

## 5. Quy trình kiểm
1. Trước khi thêm dep: tra PyPI JSON hoặc npm registry, đối chiếu LICENSE trong repo, ghi dòng vào `THIRD_PARTY_NOTICES.md`.
2. Giấy phép không OSI, không rõ hoặc copyleft mạnh nhúng vào tiến trình xT: không thêm; báo để quyết định.
3. Chạy `uv run reuse lint` trước mỗi lần phát hành.
