<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Thành phần bên thứ ba

Ngày kiểm: 2026-10-15. Nguồn: PyPI JSON (`pypi.org/pypi/<gói>/json`), npm registry (`registry.npmjs.org/<gói>/latest`), file LICENSE của repo GitHub, model card Hugging Face. Danh sách lập từ `docs/architecture-v2.md` §3.2 và `docs/tool-contracts-v1.md`. Điều kiện chặn B5: mọi dep phải có dòng ở đây trước khi vào `pyproject.toml`/`package.json`. Hiện `pyproject.toml` có dep runtime pydantic (IAM), cryptography, httpx, PyJWT và dev casbin (oracle IAM); bảng dưới là danh sách **kế hoạch đã kiểm giấy phép**. Chỉ dùng giấy phép OSI và rà soát tương thích (chi tiết: `docs/LICENSING.md`).

Chú thích: "phiên bản" là bản pin/mục tiêu theo V2 (hoặc bản mới nhất lúc kiểm nếu V2 ghi "pin"); chốt chính xác trong `uv.lock`/`package-lock.json`.


### A. Phụ thuộc Python (pip, khai báo trong `pyproject.toml`)

| tên | phiên bản | giấy phép (SPDX) | OSI? | cách dùng | url nguồn | ngày kiểm | ghi chú |
| --- | --- | --- | --- | --- | --- | --- | --- |
| mcp | 2.3.0 | MIT | Có | pip dependency (runtime) | https://pypi.org/project/mcp/ | 2026-10-15 | PyPI + GitHub LICENSE đều MIT. Chỉ `adapters/mcp` được import. |
| starlette | 1.8.0 | BSD-3-Clause | Có | pip dependency (runtime) | https://pypi.org/project/starlette/ | 2026-10-15 |  |
| uvicorn | 0.54.0 | BSD-3-Clause | Có | pip dependency (runtime) | https://pypi.org/project/uvicorn/ | 2026-10-15 |  |
| mcp-types | 2.3.0 | MIT | Có | pip dependency (runtime, phụ thuộc của mcp) | https://pypi.org/project/mcp-types/ | 2026-10-15 | METADATA wheel: `License: MIT`. Kiểu giao thức MCP của SDK 2.x. |
| httpx2 | 2.13.1 | BSD-3-Clause | Có | pip dependency (runtime, phụ thuộc của mcp) | https://pypi.org/project/httpx2/ | 2026-10-15 | METADATA wheel: `License-Expression: BSD-3-Clause`. Client HTTP của SDK mcp (transport client, test in-process). |
| httpcore2 | 2.13.1 | BSD-3-Clause | Có | pip dependency (runtime, phụ thuộc của httpx2) | https://pypi.org/project/httpcore2/ | 2026-10-15 | METADATA wheel: `License-Expression: BSD-3-Clause`. |
| sse-starlette | 3.5.0 | BSD-3-Clause | Có | pip dependency (runtime, phụ thuộc của mcp) | https://pypi.org/project/sse-starlette/ | 2026-10-15 | METADATA wheel: `License-Expression: BSD-3-Clause`. |
| python-multipart | 0.0.32 | Apache-2.0 | Có | pip dependency (runtime, phụ thuộc của mcp) | https://pypi.org/project/python-multipart/ | 2026-10-15 | METADATA wheel: `License-Expression: Apache-2.0`. |
| opentelemetry-api | 1.45.1 | Apache-2.0 | Có | pip dependency (runtime, phụ thuộc của mcp; no-op khi chưa cài exporter) | https://pypi.org/project/opentelemetry-api/ | 2026-10-15 | METADATA wheel: `License-Expression: Apache-2.0`. |
| truststore | 0.10.5 | MIT | Có | pip dependency (runtime, phụ thuộc gián tiếp của mcp/httpx2) | https://pypi.org/project/truststore/ | 2026-10-15 | METADATA wheel: `License-Expression: MIT`. |
| click | 8.5.0 | BSD-3-Clause | Có | pip dependency (runtime, phụ thuộc của uvicorn) | https://pypi.org/project/click/ | 2026-10-15 | METADATA wheel: `License-Expression: BSD-3-Clause`. |
| pydantic | 2.14.1 | MIT | Có | pip dependency (runtime) | https://pypi.org/project/pydantic/ | 2026-10-15 |  |
| pydantic-core | 2.50.1 | MIT | Có | pip dependency (runtime, phụ thuộc của pydantic) | https://pypi.org/project/pydantic-core/ | 2026-10-15 | METADATA wheel: `License-Expression: MIT`. |
| annotated-types | 0.8.0 | MIT | Có | pip dependency (runtime, phụ thuộc của pydantic) | https://pypi.org/project/annotated-types/ | 2026-10-15 | METADATA wheel: `License-Expression: MIT`. |
| typing-inspection | 0.4.4 | MIT | Có | pip dependency (runtime, phụ thuộc của pydantic) | https://pypi.org/project/typing-inspection/ | 2026-10-15 | METADATA wheel: `License-Expression: MIT`. |
| psycopg | 3.3.6 | LGPL-3.0-only | Có | pip dependency (runtime), chỉ import; bản thuần Python dùng libpq của hệ điều hành (không dùng wheel `psycopg-binary`) | https://pypi.org/project/psycopg/ | 2026-10-15 | LGPL: dùng như thư viện, không sửa, không vendor (xem LICENSING.md). |
| psycopg-pool | 3.3.3 | LGPL-3.0-only | Có | pip dependency (runtime), chỉ import | https://pypi.org/project/psycopg-pool/ | 2026-10-15 | Như psycopg. |
| alembic | 1.20.0 | MIT | Có | pip dependency (runtime) | https://pypi.org/project/alembic/ | 2026-10-15 |  |
| SQLAlchemy | 2.1.4 | MIT | Có | pip dependency (runtime; chỉ `compare_metadata` cho `xt doctor`) | https://pypi.org/project/SQLAlchemy/ | 2026-10-15 |  |
| PyYAML | 6.0.3 | MIT | Có | pip dependency (runtime) | https://pypi.org/project/PyYAML/ | 2026-10-15 |  |
| PyJWT | 2.15.1 | MIT | Có | pip dependency (runtime) | https://pypi.org/project/PyJWT/ | 2026-10-15 |  |
| joserfc | 1.7.5 | BSD-3-Clause | Có | pip dependency (runtime, phương án thay PyJWT cho JWK/JWE) | https://pypi.org/project/joserfc/ | 2026-10-15 | Chỉ dùng nếu chọn; metadata PyPI: BSD-3-Clause. |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause (dual) | Có | pip dependency (runtime; khóa RSA/EC của JWKS, Ed25519 cho biên nhận duyệt, extra `pyjwt[crypto]`) | https://pypi.org/project/cryptography/ | 2026-10-15 | PyPI `license_expression`: `Apache-2.0 OR BSD-3-Clause`. Chọn Apache-2.0. Wheel gói OpenSSL (Apache-2.0), không do xT vendor. |
| cffi | 2.1.1 | MIT-0 | Có | pip dependency (runtime, phụ thuộc của cryptography) | https://pypi.org/project/cffi/ | 2026-10-15 | PyPI `license_expression`: `MIT-0`. |
| pycparser | 3.11 | BSD-3-Clause | Có | pip dependency (runtime, phụ thuộc của cffi) | https://pypi.org/project/pycparser/ | 2026-10-15 | PyPI `license_expression`: `BSD-3-Clause`. |
| anyio | 4.15.1 | MIT | Có | pip dependency (runtime, phụ thuộc của httpx) | https://pypi.org/project/anyio/ | 2026-10-15 | PyPI `license_expression`: `MIT`. |
| httpcore | 1.0.9 | BSD-3-Clause | Có | pip dependency (runtime, phụ thuộc của httpx) | https://pypi.org/project/httpcore/ | 2026-10-15 | PyPI `license_expression`: `BSD-3-Clause`. |
| h11 | 0.16.0 | MIT | Có | pip dependency (runtime, phụ thuộc của httpcore) | https://pypi.org/project/h11/ | 2026-10-15 | PyPI classifier MIT. |
| idna | 3.20 | BSD-3-Clause | Có | pip dependency (runtime, phụ thuộc của httpx) | https://pypi.org/project/idna/ | 2026-10-15 | PyPI `license_expression`: `BSD-3-Clause`. |
| certifi | 2026.7.22 | MPL-2.0 | Có | pip dependency (runtime, phụ thuộc của httpx; bundle CA) | https://pypi.org/project/certifi/ | 2026-10-15 | PyPI license `MPL-2.0`, classifier OSI MPL 2.0. Copyleft theo file, dùng nguyên bản, không sửa. |
| SpiffWorkflow | 3.2.0 | LGPL-3.0-only (PyPI ghi `lGPLv3`) | Có | pip dependency (runtime), chỉ import + subclass (`PythonScriptEngine`, `BasePythonScriptEngineEnvironment`) trong `xt.adapters.workflow.bpmn` | https://pypi.org/project/SpiffWorkflow/ | 2026-10-15 | GitHub: LGPL-3.0. Thư viện dùng nguyên bản, không sửa, không vendor; người dùng thay được bản cài qua pip. Khớp trường PyPI không chuẩn SPDX; xác nhận bằng LICENSE repo. |
| lxml | 6.1.3 | BSD-3-Clause | Có | pip dependency (runtime trực tiếp: parser BPMN cứng `xt.adapters.workflow.bpmn.hardening`; cũng là phụ thuộc của SpiffWorkflow) | https://pypi.org/project/lxml/ | 2026-10-15 | Bản wheel gói libxml2/libxslt (MIT) - đi kèm wheel, không do xT vendor. |
| procrastinate | 3.10.0 | MIT | Có | pip dependency (runtime, đã chọn cho xt-worker) | https://pypi.org/project/procrastinate/ | 2026-10-15 | PyPI classifier + GitHub LICENSE MIT; kiểm lại JSON bản 3.10.0; xem docs/worker-queue.md. |
| croniter | 6.2.4 | MIT | Có | runtime (lịch UTC, phụ thuộc trực tiếp và của procrastinate) | https://pypi.org/project/croniter/6.2.4/ | 2026-10-15 | PyPI JSON và METADATA: MIT. |
| asgiref | 3.12.1 | BSD-3-Clause | Có | runtime, phụ thuộc của procrastinate | https://pypi.org/project/asgiref/ | 2026-10-15 | METADATA wheel: BSD-3-Clause. |
| attrs | 26.1.0 | MIT | Có | runtime, phụ thuộc của procrastinate | https://pypi.org/project/attrs/ | 2026-10-15 | METADATA: License-Expression MIT. |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | Có | runtime, phụ thuộc của procrastinate | https://pypi.org/project/packaging/ | 2026-10-15 | METADATA wheel; chọn Apache-2.0. |
| six | 1.17.0 | MIT | Có | runtime, phụ thuộc của python-dateutil | https://pypi.org/project/six/ | 2026-10-15 | METADATA wheel: MIT. |
| typing-extensions | 4.16.0 | PSF-2.0 | Có | runtime, phụ thuộc của procrastinate/pydantic | https://pypi.org/project/typing-extensions/ | 2026-10-15 | METADATA wheel: License-Expression PSF-2.0. |
| pgqueuer | 1.6.0 | MIT | Có | pip dependency (phương án thay procrastinate) | https://pypi.org/project/pgqueuer/ | 2026-10-15 | Chỉ thêm nếu được chọn. |
| python-dateutil | 2.9.0.post0 | Apache-2.0 OR BSD-3-Clause (dual) | Có | pip dependency (runtime) | https://pypi.org/project/python-dateutil/ | 2026-10-15 | Chọn điều khoản Apache-2.0. |
| holidays | 0.106 | MIT | Có | pip dependency (runtime) | https://pypi.org/project/holidays/ | 2026-10-15 | PyPI + GitHub MIT. Đóng điều kiện `[kiểm giấy phép]` của V2. |
| boto3 | 1.43.114 | Apache-2.0 | Có | pip dependency (runtime, S3 tới SeaweedFS) | https://pypi.org/project/boto3/ | 2026-10-15 | Giấy phép thư viện khách; tương thích với SeaweedFS qua giao thức S3 (mạng), không liên quan giấy phép. |
| aiogram | 3.31.0 | MIT | Có | pip dependency (runtime, xt-bot) | https://pypi.org/project/aiogram/ | 2026-10-15 |  |
| httpx | 0.28.1 | BSD-3-Clause | Có | pip dependency (runtime) | https://pypi.org/project/httpx/ | 2026-10-15 |  |
| pyotp | 2.10.0 | MIT | Có | pip dependency (runtime, TOTP cho `/confirm`) | https://pypi.org/project/pyotp/ | 2026-10-15 |  |
| sqlglot | 30.23.0 | MIT | Có | dev/test (đếm câu lệnh trong DDL do model engine sinh; không ở đường chạy) | https://pypi.org/project/sqlglot/ | 2026-10-15 | Nhóm `dev` trong `pyproject.toml`. |
| Jinja2 | 3.1.6 | BSD-3-Clause | Có | pip dependency (runtime, server-render `/confirm`) | https://pypi.org/project/Jinja2/ | 2026-10-15 | PyPI classifier BSD. |
| DuckDB | 1.5.6 | MIT | Có | pip dependency (profile full) | https://pypi.org/project/duckdb/ | 2026-10-15 | Extension `postgres`, `httpfs` do DuckDB phát hành (MIT); tải lúc chạy, không vendor. |
| WeasyPrint | 70.0 | BSD-3-Clause | Có | pip dependency (tùy chọn, xuất PDF) | https://pypi.org/project/weasyprint/ | 2026-10-15 | Cần Pango/Cairo hệ thống (LGPL) - gói của hệ điều hành, không đi kèm xT. |
| jsonschema | 4.26.0 | MIT | Có | pip dependency (runtime/test, Draft 2020-12; hiện là dev: kiểm JSON Schema process-spec v2) | https://pypi.org/project/jsonschema/ | 2026-10-15 |  |
| Typer | 0.27.3 | MIT | Có | pip dependency (CLI `xt`, nếu dùng thay argparse) | https://pypi.org/project/typer/ | 2026-10-15 | Chưa chốt dùng; ghi sẵn để không vi phạm B5. |

### B. Công cụ build và kiểm thử (dev, không phân phối)

| tên | phiên bản | giấy phép (SPDX) | OSI? | cách dùng | url nguồn | ngày kiểm | ghi chú |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CPython | 3.13 (>=3.12) | PSF-2.0 | Có | Runtime, người dùng cài | https://docs.python.org/3/license.html | 2026-10-15 |  |
| uv | 0.13.1 | MIT OR Apache-2.0 | Có | Công cụ build/dev | https://pypi.org/project/uv/ | 2026-10-15 |  |
| hatchling | 1.32.4 | MIT | Có | build-backend | https://pypi.org/project/hatchling/ | 2026-10-15 |  |
| ruff | 0.17.0 | MIT | Có | dev | https://pypi.org/project/ruff/ | 2026-10-15 |  |
| mypy | 2.4.0 | MIT | Có | dev | https://pypi.org/project/mypy/ | 2026-10-15 |  |
| types-croniter | 6.2.4.20261006 | Apache-2.0 | Có | dev, stubs cho mypy strict | https://pypi.org/project/types-croniter/ | 2026-10-15 | METADATA wheel: License-Expression Apache-2.0. |
| types-jsonschema | 4.26.0.20261006 | Apache-2.0 | Có | dev (stub mypy cho jsonschema) | https://pypi.org/project/types-jsonschema/ | 2026-10-15 | Typeshed, METADATA `License-Expression: Apache-2.0`. |
| types-python-dateutil | 2.9.0.20260807 | Apache-2.0 | Có | dev, stubs cho mypy strict (`xt.domain.process.schedule`) | https://pypi.org/project/types-python-dateutil/ | 2026-10-15 | METADATA wheel: License-Expression Apache-2.0. |
| lxml-stubs | 0.5.1 | Apache-2.0 | Có | dev, stubs cho mypy strict (parser BPMN cứng) | https://pypi.org/project/lxml-stubs/ | 2026-10-15 | METADATA wheel: classifier OSI Apache Software License. |
| jsonschema-specifications | 2025.9.1 | MIT | Có | dev (phụ thuộc của jsonschema; test JSON Schema process-spec) | https://pypi.org/project/jsonschema-specifications/ | 2026-10-15 | METADATA wheel: License-Expression MIT. |
| referencing | 0.37.0 | MIT | Có | dev (phụ thuộc của jsonschema) | https://pypi.org/project/referencing/ | 2026-10-15 | METADATA wheel: License-Expression MIT. |
| rpds-py | 2026.9.1 | MIT | Có | dev (phụ thuộc của referencing) | https://pypi.org/project/rpds-py/ | 2026-10-15 | METADATA wheel: License-Expression MIT. |
| bpmn-moddle (+ moddle, moddle-xml, saxen, min-dash) | 9.0.4 (7.2.0, 11.0.0, 10.0.0, 4.2.3) | MIT | Có | công cụ test tích hợp tùy chọn (`tests/integration/process/bpmn_viewer`: kiểm cấu trúc BPMN 2.0 + DI), cài ngoài repo, không phân phối | https://www.npmjs.com/package/bpmn-moddle | 2026-10-15 | `license` trong package.json cài thật (5 gói, đều MIT). bpmn-js (không OSI) đã bỏ theo quyết định chủ dự án. |
| pytest | 9.1.1 | MIT | Có | dev | https://pypi.org/project/pytest/ | 2026-10-15 |  |
| pytest-cov | 7.1.0 | MIT | Có | dev | https://pypi.org/project/pytest-cov/ | 2026-10-15 |  |
| pytest-asyncio | 1.4.0 | Apache-2.0 | Có | dev | https://pypi.org/project/pytest-asyncio/ | 2026-10-15 |  |
| import-linter | 2.15 | BSD-2-Clause | Có | dev | https://pypi.org/project/import-linter/ | 2026-10-15 |  |
| reuse | 6.2.0 | Apache-2.0 (mã); tài liệu CC-BY-SA-4.0, GPL-3.0-or-later cho phần phụ trợ | Có | dev (công cụ CLI, không import, không phân phối) | https://pypi.org/project/reuse/ | 2026-10-15 | Metadata hỗn hợp, chỉ chạy như công cụ ngoài nên không ảnh hưởng giấy phép xT. |
| cyclonedx-bom (`cyclonedx-py`) | 7.5.0 | Apache-2.0 | Có | dev, chỉ chạy qua `uvx` trong `scripts/release.sh` để sinh SBOM CycloneDX Python; không import, không phân phối, không nằm trong `pyproject.toml` | https://pypi.org/project/cyclonedx-bom/ | 2026-10-16 | PyPI JSON: `license` Apache-2.0; LICENSE repo CycloneDX/cyclonedx-python là Apache-2.0. |
| @cyclonedx/cyclonedx-npm | 6.0.1 | Apache-2.0 | Có | dev, chỉ chạy qua `npx` trong `scripts/release.sh` để sinh SBOM CycloneDX của Desk; không phải phụ thuộc của Desk, không phân phối | https://www.npmjs.com/package/@cyclonedx/cyclonedx-npm | 2026-10-16 | npm registry: `license` Apache-2.0. |
| pycasbin (gói `casbin`) | 1.43.0 (V2 ghi 2.8.0; gói PyPI `pycasbin` khác tên) | Apache-2.0 | Có | dev (oracle IAM trong CI: `tests/unit/iam`, đã pin trong `pyproject.toml` nhóm dev) | https://pypi.org/project/casbin/ | 2026-10-15 | Phiên bản V2 (2.8.0) cần đối chiếu lại khi pin; giấy phép không đổi. |
| simpleeval | 1.0.8 | MIT | Có | dev (phụ thuộc của casbin; chỉ test IAM) | https://pypi.org/project/simpleeval/ | 2026-10-15 | METADATA wheel: `License: MIT`, classifier OSI MIT. |
| hypothesis | 6.169.3 | MPL-2.0 | Có | dev, tùy chọn (property test IAM `tests/unit/iam/audit/test_property_oracle.py`, tự skip nếu chưa cài; chạy: `uv run --with hypothesis pytest tests/unit/iam/audit`) | https://pypi.org/project/hypothesis/ | 2026-10-15 | METADATA `License-Expression: MPL-2.0`; chỉ dùng để chạy test, không phân phối, không sửa mã hypothesis. |
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
| @modelcontextprotocol/ext-apps | 2.0.3 | MIT (đang chuyển sang Apache-2.0) | Có | npm dependency | https://www.npmjs.com/package/@modelcontextprotocol/ext-apps | 2026-10-15 | Như trên. Desk KHÔNG phụ thuộc trực tiếp (chỉ `@mcp-ui/client` kéo bản `^1.x` lồng bên trong); gói này không phát hành sandbox proxy nên Desk có `desk/public/sandbox.html` viết theo spec. V2 ghi `^1.2.0` do mcp-ui yêu cầu; bản latest là 2.0.3, phiên bản pin phải khớp peer của @mcp-ui/client. |
| ajv | 8.20.0 | MIT | Có | npm dependency (Desk: kiểm tham số tool call từ MCP App theo JSON Schema 2020-12) | https://www.npmjs.com/package/ajv | 2026-10-15 | `license` trong package.json cài thật. |
| marked | 18.1.1 | MIT | Có | npm dependency (Desk: markdown -> HTML) | https://www.npmjs.com/package/marked | 2026-10-15 | `license` trong package.json cài thật (desk/node_modules). |
| dompurify | 3.4.17 | MPL-2.0 OR Apache-2.0 (dual) | Có | npm dependency (Desk: sanitize HTML do markdown sinh ra) | https://www.npmjs.com/package/dompurify | 2026-10-15 | Chọn điều khoản Apache-2.0. package.json cài thật ghi `(MPL-2.0 OR Apache-2.0)`. |
| vitest | 5.0.4 | MIT | Có | npm devDependency (test đơn vị Desk) | https://www.npmjs.com/package/vitest | 2026-10-15 | |
| jsdom | 29.1.1 | MIT | Có | npm devDependency (test sanitize markdown) | https://www.npmjs.com/package/jsdom | 2026-10-15 | |
| @playwright/test | 1.64.1 | Apache-2.0 | Có | npm devDependency (e2e Desk, headless) | https://www.npmjs.com/package/@playwright/test | 2026-10-15 | Chromium tải lúc dev (`playwright install chromium`), không phân phối. |
| @types/react, @types/react-dom, @types/node | 19.3.0 / 19.3.0 / 26.6.5 | MIT | Có | npm devDependency (kiểu TypeScript) | https://www.npmjs.com/package/@types/react | 2026-10-15 | DefinitelyTyped, MIT. |

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
- Copyleft: SpiffWorkflow, psycopg, psycopg-pool (LGPL-3.0, chỉ import); certifi (MPL-2.0, phụ thuộc gián tiếp của httpx, dùng nguyên bản); ClamAV (GPL-2.0) và OpenBao (MPL-2.0) chỉ chạy thành container riêng. Metabase (AGPL-3.0) chỉ nằm trên roadmap, chưa dùng.
- Dự án MCP (`mcp` Python, SDK/ext-apps/inspector TypeScript) đang chuyển giấy phép MIT sang Apache-2.0 theo từng đóng góp; cả hai OSI, tương thích Apache-2.0. Theo dõi khi nâng phiên bản.
- Qwen3.5: Apache-2.0 theo model card, nhưng bản Ollama Q4 cần đối chiếu lại khi pin.
- Lệch phiên bản cần xử lý khi pin: Ollama (V2 0.30.10, latest 0.40.4), `casbin` (V2 2.8.0, PyPI 1.43.0), `@modelcontextprotocol/ext-apps` (V2 `^1.2.0`, latest 2.0.3).

Không vendor code dự án khác. Sử dụng thành phần qua dependency hoặc Docker; không đưa mã nguồn/gói của dự án khác vào bản phân phối xT. Ghi phiên bản cụ thể, giấy phép tương ứng và URL nguồn chính thức khi tích hợp.
