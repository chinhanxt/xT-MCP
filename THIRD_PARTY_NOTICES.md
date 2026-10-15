# Thành phần bên thứ ba

Ngày kiểm: 2026-10-15. Nguồn: PyPI JSON (`pypi.org/pypi/<gói>/json`), npm registry (`registry.npmjs.org/<gói>/latest`), file LICENSE của repo GitHub, model card Hugging Face. Danh sách lập từ `docs/architecture-v2.md` §3.2 và `docs/tool-contracts-v1.md`. Điều kiện chặn B5: mọi dep phải có dòng ở đây trước khi vào `pyproject.toml`/`package.json`. Hiện `pyproject.toml` chưa có dep runtime nào; bảng dưới là danh sách **kế hoạch đã kiểm giấy phép**. Chỉ dùng giấy phép OSI và rà soát tương thích (chi tiết: `docs/LICENSING.md`).

Chú thích: "phiên bản" là bản pin/mục tiêu theo V2 (hoặc bản mới nhất lúc kiểm nếu V2 ghi "pin"); chốt chính xác trong `uv.lock`/`package-lock.json`.


### A. Phụ thuộc Python (pip, khai báo trong `pyproject.toml`)

| tên | phiên bản | giấy phép (SPDX) | OSI? | cách dùng | url nguồn | ngày kiểm | ghi chú |
| --- | --- | --- | --- | --- | --- | --- | --- |
| mcp | 2.3.0 | MIT | Có | pip dependency (runtime) | https://pypi.org/project/mcp/ | 2026-10-15 | PyPI + GitHub LICENSE đều MIT. Chỉ `adapters/mcp` được import. |
| starlette | 1.8.0 | BSD-3-Clause | Có | pip dependency (runtime) | https://pypi.org/project/starlette/ | 2026-10-15 |  |
| uvicorn | 0.54.0 | BSD-3-Clause | Có | pip dependency (runtime) | https://pypi.org/project/uvicorn/ | 2026-10-15 |  |
| pydantic | 2.14.1 | MIT | Có | pip dependency (runtime) | https://pypi.org/project/pydantic/ | 2026-10-15 |  |
| psycopg | 3.3.6 | LGPL-3.0-only | Có | pip dependency (runtime), chỉ import | https://pypi.org/project/psycopg/ | 2026-10-15 | LGPL: dùng như thư viện, không sửa, không vendor (xem LICENSING.md). |
| psycopg-pool | 3.3.3 | LGPL-3.0-only | Có | pip dependency (runtime), chỉ import | https://pypi.org/project/psycopg-pool/ | 2026-10-15 | Như psycopg. |
| alembic | 1.20.0 | MIT | Có | pip dependency (runtime) | https://pypi.org/project/alembic/ | 2026-10-15 |  |
| SQLAlchemy | 2.1.4 | MIT | Có | pip dependency (runtime; chỉ `compare_metadata` cho `xt doctor`) | https://pypi.org/project/SQLAlchemy/ | 2026-10-15 |  |
| PyYAML | 6.0.3 | MIT | Có | pip dependency (runtime) | https://pypi.org/project/PyYAML/ | 2026-10-15 |  |
| PyJWT | 2.15.1 | MIT | Có | pip dependency (runtime) | https://pypi.org/project/PyJWT/ | 2026-10-15 |  |
| joserfc | 1.7.5 | BSD-3-Clause | Có | pip dependency (runtime, phương án thay PyJWT cho JWK/JWE) | https://pypi.org/project/joserfc/ | 2026-10-15 | Chỉ dùng nếu chọn; metadata PyPI: BSD-3-Clause. |
| SpiffWorkflow | 3.2.0 | LGPL-3.0-only (PyPI ghi `lGPLv3`) | Có | pip dependency (runtime), chỉ import | https://pypi.org/project/SpiffWorkflow/ | 2026-10-15 | GitHub: LGPL-3.0. Không sửa, không vendor. Khớp trường PyPI không chuẩn SPDX; xác nhận bằng LICENSE repo. |
| lxml | 6.1.3 | BSD-3-Clause | Có | pip dependency (runtime, phụ thuộc của SpiffWorkflow) | https://pypi.org/project/lxml/ | 2026-10-15 | Bản wheel gói libxml2/libxslt (MIT) - đi kèm wheel, không do xT vendor. |
| procrastinate | 3.10.0 | MIT | Có | pip dependency (runtime; hoặc pgqueuer hoặc bản tự viết, chọn sau spike v0.0) | https://pypi.org/project/procrastinate/ | 2026-10-15 | PyPI classifier + GitHub LICENSE MIT. Đóng điều kiện `[chưa kiểm chứng]` của V2. |
| pgqueuer | 1.6.0 | MIT | Có | pip dependency (phương án thay procrastinate) | https://pypi.org/project/pgqueuer/ | 2026-10-15 | Chỉ thêm nếu được chọn. |
| python-dateutil | 2.9.0.post0 | Apache-2.0 OR BSD-3-Clause (dual) | Có | pip dependency (runtime) | https://pypi.org/project/python-dateutil/ | 2026-10-15 | Chọn điều khoản Apache-2.0. |
| holidays | 0.106 | MIT | Có | pip dependency (runtime) | https://pypi.org/project/holidays/ | 2026-10-15 | PyPI + GitHub MIT. Đóng điều kiện `[kiểm giấy phép]` của V2. |
| boto3 | 1.43.114 | Apache-2.0 | Có | pip dependency (runtime, S3 tới SeaweedFS) | https://pypi.org/project/boto3/ | 2026-10-15 | Giấy phép thư viện khách; tương thích với SeaweedFS qua giao thức S3 (mạng), không liên quan giấy phép. |
| aiogram | 3.31.0 | MIT | Có | pip dependency (runtime, xt-bot) | https://pypi.org/project/aiogram/ | 2026-10-15 |  |
| httpx | 0.28.1 | BSD-3-Clause | Có | pip dependency (runtime) | https://pypi.org/project/httpx/ | 2026-10-15 |  |
| pyotp | 2.10.0 | MIT | Có | pip dependency (runtime, TOTP cho `/confirm`) | https://pypi.org/project/pyotp/ | 2026-10-15 |  |
| sqlglot | 30.23.0 | MIT | Có | pip dependency (runtime) | https://pypi.org/project/sqlglot/ | 2026-10-15 |  |
| Jinja2 | 3.1.6 | BSD-3-Clause | Có | pip dependency (runtime, server-render `/confirm`) | https://pypi.org/project/Jinja2/ | 2026-10-15 | PyPI classifier BSD. |
| DuckDB | 1.5.6 | MIT | Có | pip dependency (profile full) | https://pypi.org/project/duckdb/ | 2026-10-15 | Extension `postgres`, `httpfs` do DuckDB phát hành (MIT); tải lúc chạy, không vendor. |
| WeasyPrint | 70.0 | BSD-3-Clause | Có | pip dependency (tùy chọn, xuất PDF) | https://pypi.org/project/weasyprint/ | 2026-10-15 | Cần Pango/Cairo hệ thống (LGPL) - gói của hệ điều hành, không đi kèm xT. |
| jsonschema | 4.26.0 | MIT | Có | pip dependency (runtime/test, Draft 2020-12) | https://pypi.org/project/jsonschema/ | 2026-10-15 |  |
| Typer | 0.27.3 | MIT | Có | pip dependency (CLI `xt`, nếu dùng thay argparse) | https://pypi.org/project/typer/ | 2026-10-15 | Chưa chốt dùng; ghi sẵn để không vi phạm B5. |

### B. Công cụ build và kiểm thử (dev, không phân phối)

| tên | phiên bản | giấy phép (SPDX) | OSI? | cách dùng | url nguồn | ngày kiểm | ghi chú |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CPython | 3.13 (>=3.12) | PSF-2.0 | Có | Runtime, người dùng cài | https://docs.python.org/3/license.html | 2026-10-15 |  |
| uv | 0.13.1 | MIT OR Apache-2.0 | Có | Công cụ build/dev | https://pypi.org/project/uv/ | 2026-10-15 |  |
| hatchling | 1.32.4 | MIT | Có | build-backend | https://pypi.org/project/hatchling/ | 2026-10-15 |  |
| ruff | 0.17.0 | MIT | Có | dev | https://pypi.org/project/ruff/ | 2026-10-15 |  |
| mypy | 2.4.0 | MIT | Có | dev | https://pypi.org/project/mypy/ | 2026-10-15 |  |
| pytest | 9.1.1 | MIT | Có | dev | https://pypi.org/project/pytest/ | 2026-10-15 |  |
| pytest-cov | 7.1.0 | MIT | Có | dev | https://pypi.org/project/pytest-cov/ | 2026-10-15 |  |
| pytest-asyncio | 1.4.0 | Apache-2.0 | Có | dev | https://pypi.org/project/pytest-asyncio/ | 2026-10-15 |  |
| import-linter | 2.15 | BSD-2-Clause | Có | dev | https://pypi.org/project/import-linter/ | 2026-10-15 |  |
| reuse | 6.2.0 | Apache-2.0 (mã); tài liệu CC-BY-SA-4.0, GPL-3.0-or-later cho phần phụ trợ | Có | dev (công cụ CLI, không import, không phân phối) | https://pypi.org/project/reuse/ | 2026-10-15 | Metadata hỗn hợp, chỉ chạy như công cụ ngoài nên không ảnh hưởng giấy phép xT. |
| pycasbin (gói `casbin`) | 1.43.0 (V2 ghi 2.8.0; gói PyPI `pycasbin` khác tên) | Apache-2.0 | Có | dev (oracle IAM trong CI) | https://pypi.org/project/casbin/ | 2026-10-15 | Phiên bản V2 (2.8.0) cần đối chiếu lại khi pin; giấy phép không đổi. |
| Playwright (Python) | 1.64.0 | Apache-2.0 | Có | dev (test GUI headless) | https://pypi.org/project/playwright/ | 2026-10-15 | Trình duyệt do Playwright tải: Chromium (BSD-3), Firefox (MPL-2.0), WebKit (LGPL/BSD); chỉ tải lúc dev, không phân phối. |
| MCP Inspector CLI (`@modelcontextprotocol/inspector`) | 2.11.0 | Apache-2.0 / MIT (chuyển đổi, xem ghi chú); npm ghi `SEE LICENSE IN LICENSE` | Có | npx lúc dev | https://github.com/modelcontextprotocol/inspector | 2026-10-15 | LICENSE repo: dự án MCP đang chuyển MIT sang Apache-2.0; cả hai là OSI. |

### C. Phụ thuộc npm (xT Desk, bundle lúc build)

| tên | phiên bản | giấy phép (SPDX) | OSI? | cách dùng | url nguồn | ngày kiểm | ghi chú |
| --- | --- | --- | --- | --- | --- | --- | --- |
| react / react-dom | 19.3.1 | MIT | Có | npm dependency | https://www.npmjs.com/package/react | 2026-10-15 |  |
| vite | 8.3.4 | MIT | Có | npm devDependency (build) | https://www.npmjs.com/package/vite | 2026-10-15 |  |
| @vitejs/plugin-react | 6.1.2 | MIT | Có | npm devDependency (build) | https://www.npmjs.com/package/@vitejs/plugin-react | 2026-10-15 |  |
| typescript | 7.0.2 | Apache-2.0 | Có | npm devDependency (build) | https://www.npmjs.com/package/typescript | 2026-10-15 |  |
| @mcp-ui/client | 7.1.1 | Apache-2.0 | Có | npm dependency | https://www.npmjs.com/package/@mcp-ui/client | 2026-10-15 | npm + GitHub MCP-UI-Org/mcp-ui: Apache-2.0. |
| @modelcontextprotocol/sdk | 1.32.1 | MIT (đang chuyển sang Apache-2.0) | Có | npm dependency | https://www.npmjs.com/package/@modelcontextprotocol/sdk | 2026-10-15 | LICENSE repo: đóng góp cũ MIT, đóng góp mới Apache-2.0; cả hai OSI, tương thích Apache-2.0. |
| @modelcontextprotocol/ext-apps | 2.0.3 | MIT (đang chuyển sang Apache-2.0) | Có | npm dependency | https://www.npmjs.com/package/@modelcontextprotocol/ext-apps | 2026-10-15 | Như trên. V2 ghi `^1.2.0` do mcp-ui yêu cầu; bản latest là 2.0.3, phiên bản pin phải khớp peer của @mcp-ui/client. |

### D. Dịch vụ docker (kéo lúc cài, chạy qua mạng, không vendor)

| tên | phiên bản | giấy phép (SPDX) | OSI? | cách dùng | url nguồn | ngày kiểm | ghi chú |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PostgreSQL | 17 | PostgreSQL License | Có | docker image `postgres` (pull lúc cài) | https://www.postgresql.org/about/licence/ | 2026-10-15 |  |
| Keycloak | 26.8.1 | Apache-2.0 | Có | docker image (pull lúc cài) | https://github.com/keycloak/keycloak | 2026-10-15 | Release latest 26.8.1 đối chiếu GitHub. |
| Apache APISIX | 3.19.0 | Apache-2.0 | Có | docker image `apache/apisix` | https://github.com/apache/apisix | 2026-10-15 | Release latest 3.19.0. |
| SeaweedFS | 4.48 | Apache-2.0 | Có | docker image `chrislusf/seaweedfs` | https://github.com/seaweedfs/seaweedfs | 2026-10-15 | Release latest 4.48. Đóng điều kiện `[chưa kiểm với SeaweedFS]` về giấy phép. |
| PostgREST | v16.4 (target) | MIT | Có | docker image `postgrest/postgrest` | https://github.com/PostgREST/postgrest | 2026-10-15 | Phiên bản chốt khi scaffold, pin digest. |
| NATS server | v2.15.1 (target) | Apache-2.0 | Có | docker image `nats` | https://github.com/nats-io/nats-server | 2026-10-15 | Đóng điều kiện `[chưa kiểm chứng]`. |
| Ollama | 0.30.10 theo V2 (latest GitHub 0.40.4) | MIT | Có | docker image `ollama/ollama` hoặc cài trên host | https://github.com/ollama/ollama | 2026-10-15 | Cần quyết định phiên bản khi pin. |
| ClamAV | pin lúc scaffold | GPL-2.0 | Có | docker image `clamav/clamav`, chỉ qua mạng (clamd) | https://github.com/Cisco-Talos/clamav | 2026-10-15 | Copyleft mạnh nhưng xT chỉ nói chuyện qua socket TCP, không link, không phân phối. |
| OpenBao | pin lúc scaffold | MPL-2.0 | Có | docker image `openbao/openbao` | https://github.com/openbao/openbao | 2026-10-15 | MPL-2.0 copyleft theo file; dùng nguyên bản. |
| Mailpit | pin lúc scaffold | MIT | Có | docker image `axllent/mailpit` (dev/demo) | https://github.com/axllent/mailpit | 2026-10-15 |  |
| cloudflared | pin lúc scaffold | Apache-2.0 (client) | Có (client); dịch vụ Cloudflare là đóng | docker image, chỉ profile `public`, tùy chọn | https://github.com/cloudflare/cloudflared | 2026-10-15 | Không nằm trên đường build/cài mặc định (PoF I.4); thay bằng APISIX TLS + certbot. |

### E. Trọng số mô hình (người dùng tải, không vendor)

| tên | phiên bản | giấy phép (SPDX) | OSI? | cách dùng | url nguồn | ngày kiểm | ghi chú |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Qwen3.5-4B (`qwen3.5:4b`, Q4) | Hugging Face sha 851bf6e (Qwen/Qwen3.5-4B) | Apache-2.0 | Có (Apache-2.0) | Model tải bởi người dùng qua `ollama pull qwen3.5:4b` | https://huggingface.co/Qwen/Qwen3.5-4B | 2026-10-15 | Model card + file LICENSE trên HF là Apache-2.0. Bản quantize Q4 của thư viện Ollama kế thừa từ bản gốc; đối chiếu lại trang https://ollama.com/library/qwen3.5 khi pin (không truy xuất được giấy phép ở trang này lúc kiểm). Không phân phối trọng số trong tarball. Lưu ý: trọng số mở theo Apache-2.0 nhưng không phải 'open source AI' theo định nghĩa OSAID vì dữ liệu huấn luyện không công bố. |

## Cần chú ý

- Không có thành phần phi-OSI. Đã loại n8n (fair-code), Redpanda (BSL), SSPL/BSL.
- Copyleft: SpiffWorkflow, psycopg, psycopg-pool (LGPL-3.0, chỉ import); ClamAV (GPL-2.0) và OpenBao (MPL-2.0) chỉ chạy thành container riêng. Metabase (AGPL-3.0) chỉ nằm trên roadmap, chưa dùng.
- Dự án MCP (`mcp` Python, SDK/ext-apps/inspector TypeScript) đang chuyển giấy phép MIT sang Apache-2.0 theo từng đóng góp; cả hai OSI, tương thích Apache-2.0. Theo dõi khi nâng phiên bản.
- Qwen3.5: Apache-2.0 theo model card, nhưng bản Ollama Q4 cần đối chiếu lại khi pin.
- Lệch phiên bản cần xử lý khi pin: Ollama (V2 0.30.10, latest 0.40.4), `casbin` (V2 2.8.0, PyPI 1.43.0), `@modelcontextprotocol/ext-apps` (V2 `^1.2.0`, latest 2.0.3).

Không vendor code dự án khác. Sử dụng thành phần qua dependency hoặc Docker; không đưa mã nguồn/gói của dự án khác vào bản phân phối xT. Ghi phiên bản cụ thể, giấy phép tương ứng và URL nguồn chính thức khi tích hợp.
