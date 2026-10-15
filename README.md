<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# xT — Agent-native DX-OS

xT là một **DX-OS Open-Core** cho doanh nghiệp: lõi headless (SSO, API Gateway, dữ liệu, workflow) được lộ ra qua **MCP (Model Context Protocol)**, còn **agent AI (ChatGPT, Claude, LLM chạy local) chính là giao diện**. Bộ tool cố định, **nghiệp vụ là dữ liệu**: model và quy trình do agent đề xuất, con người duyệt rồi mới áp dụng. Không gắn với một ngành cụ thể.

> **English summary.** xT is an agent-native, open-source (Apache-2.0) digital-operations platform for the OLP 2026 Open Source Software contest. A headless Tier 1 (Keycloak SSO, APISIX gateway, PostgreSQL with row-level security plus a model engine, a SpiffWorkflow-based process engine and a PostgreSQL job worker) is exposed through an MCP server (17 fixed tools, business logic as data). Tier 2 is *xT Desk*, a chat front end that renders MCP Apps inline. Build from source with `uv`, Node (nvm), `make` and Docker Engine/Podman only: see [INSTALL.md](INSTALL.md). Planned items are explicitly marked "dự kiến". Issues: GitHub Issues. Changes: [CHANGELOG.md](CHANGELOG.md).

**Trạng thái:** phiên bản `0.1.0.dev0`, đang phát triển. Mục nào chưa có mã và test được ghi **"dự kiến"**.

## Ảnh chụp màn hình

| | |
|---|---|
| ![Desk: màn hình bắt đầu](docs/img/desk-1-start.png) | ![Desk: sơ đồ tổ chức hiển thị ngay trong chat](docs/img/desk-2-orgchart-inline.png) |
| xT Desk: màn hình bắt đầu | MCP App (sơ đồ tổ chức) hiển thị inline trong chat |
| ![Desk: gọi tool từ giao diện MCP App](docs/img/desk-3-ui-tool-call.png) | ![Desk: chạy với LLM local](docs/img/desk-4-local-llm.png) |
| App gọi tool qua Desk | Chạy với LLM local (Ollama, qwen3.5:4b) |

## Kiến trúc: 3 tầng, 7 lớp, H-P-D-I

Chi tiết và lý do từng quyết định: [docs/architecture-v2.md](docs/architecture-v2.md) (§2).

**3 tầng (đề thi §2–§3)**

| Tầng | Nội dung trong xT |
|---|---|
| 1. Lõi dịch vụ số, headless (không UI) | 4 dịch vụ bắt buộc: **Identity/SSO** (Keycloak, `/auth`), **API Gateway** (APISIX), **Dữ liệu** (PostgreSQL 17 + RLS + model engine, PostgREST `/data`), **Workflow** (SpiffWorkflow + `xt-worker`). Cả hai kênh MCP `/mcp` và REST dùng chung một bộ use case |
| 2. Ứng dụng minh họa có giao diện | **xT Desk** (React/Vite): chat với LLM, MCP Apps hiển thị inline, trang xác nhận `/desk/confirm`. Gói nghiệp vụ và skills nằm ở tầng này |
| 3. Ứng dụng của doanh nghiệp | Gói nghiệp vụ là dữ liệu (`packages/`), do cộng đồng/doanh nghiệp viết thêm |

**7 lớp công nghệ (đề thi §4, bảng tham chiếu; công nghệ thực chọn)**

| Lớp | xT chọn | Trạng thái |
|---|---|---|
| 1 Trải nghiệm | xT Desk, MCP Apps | Có (Desk); bot Telegram dự kiến |
| 2 Biên / an toàn thông tin | APISIX, Keycloak (OIDC, PKCE, DCR, TOTP step-up), làn che PII | Có; ClamAV, OpenBao có trong profile `full`, tích hợp mức ứng dụng dự kiến |
| 3 Tác nghiệp | PostgreSQL 17, model engine (bảng thật, RLS, audit hash-chain), PostgREST | Có |
| 4 Quy trình / DevSecOps | SpiffWorkflow (BPMN 2.0), `xt-worker` (procrastinate), CI, cấu hình khai báo | Có |
| 5 Hội tụ / sổ cái | NATS JetStream, SeaweedFS, Parquet, chữ ký sự kiện | Dự kiến (NATS/SeaweedFS chỉ có container trong profile `full`) |
| 6 Phân tích / trí tuệ | Ollama + qwen3.5:4b (agent local), DuckDB | Ollama có; DuckDB dự kiến |
| 7 Giám sát / tìm kiếm | OpenTelemetry, `/metrics`, FTS/pgvector | Dự kiến |

**H-P-D-I (đề thi §1–§2).** Trục chính của ứng dụng minh họa: **P (Quy trình)**, e-Office: đề xuất–phê duyệt, giao việc có SLA; và **I (Trí tuệ)**: agent là giao diện, có quản trị quyền tự chủ L0–L3 và audit. Phụ: **H (Con người)** với danh tính cho cả người và agent, sơ đồ tổ chức; **D (Dữ liệu)** với bảng thật có ràng buộc, audit, lineage.

## Tính năng (chỉ liệt kê phần đã có mã và test)

Số test: `uv run pytest` 1523 passed (test tích hợp nằm ngoài bộ mặc định: `make test-integration` 346 passed trong một lần chạy, cần Docker); Desk `vitest` 144 passed. Kiểm như giám khảo: `make verify-clean`. Số đo ngày 16/10/2026.

| Tính năng | Mô tả | Tài liệu |
|---|---|---|
| MCP server | `xt serve` (Streamable HTTP, stdio): 17 tool cố định, mô tả dưới dạng dữ liệu (ToolContract), hồ sơ `core-10` (3.920 token đo trên qwen3.5:4b) và `full`; idempotency, mức tự chủ L0–L3, xác nhận hai pha | [mcp-server.md](docs/mcp-server.md), [tool-contracts-v1.md](docs/tool-contracts-v1.md) |
| Danh tính | Xác thực token Keycloak (JWKS, `iss`, `aud`, `azp`), Protected Resource Metadata (RFC 9728), step-up OTP, biên nhận Ed25519 dùng một lần, allowlist client DCR cho connector | [identity.md](docs/identity.md) |
| Phân quyền (IAM) | Policy JSON: Deny > Allow, ranh giới tenant, SoD, ủy quyền cho agent, `explain` | [iam.md](docs/iam.md) |
| Model engine | Spec JSON → bảng PostgreSQL thật; planner SAFE/RISKY/DESTRUCTIVE, schema riêng và RLS FORCE cho từng tenant, audit hash-chain, CRUD tổng quát | [model-engine.md](docs/model-engine.md) |
| Process engine | ProcessSpec JSON → BPMN 2.0 (có sơ đồ), phê duyệt đa cấp, SLA, leo thang, song song, timer, SoD theo bước | [process-engine.md](docs/process-engine.md) |
| Hàng đợi / timer | `xt worker` trên PostgreSQL: enqueue cùng transaction, lịch IANA, recover worker chết | [worker-queue.md](docs/worker-queue.md) |
| Dữ liệu headless | PostgREST đọc view theo tenant, JWT `aud=xt-data` | [headless-data.md](docs/headless-data.md) (mục "Trạng thái" ghi rõ phần chưa chạy thật) |
| xT Desk | Chat tiếng Việt, đăng nhập OIDC PKCE, MCP Apps trong sandbox khác origin, giới hạn tốc độ lời gọi App, làn PII | [desk.md](docs/desk.md), [e2e-desk-keycloak.md](docs/e2e-desk-keycloak.md) |
| Gói nghiệp vụ | 3 gói: `de-xuat-phe-duyet`, `giao-viec-sla`, `nhan-su-vong-doi` (dữ liệu JSON, không mã) + 4 skill agent trong `skills/` | `packages/*/README.md` |
| Triển khai | Docker Compose, profile `minimal`/`full`/`public`, container non-root, `read_only`, `cap_drop ALL` | [deploy.md](docs/deploy.md) |
| Eval agent | Bộ scenario đo agent thật (có/không skill) | [evals.md](docs/evals.md) |

**Dự kiến (chưa có mã hoặc chưa chạy thật):** 4 tool còn lại để đủ 21 tool (v1.0), `upload_file` và `aggregate_records` (hiện trả `capability_unavailable`), bot Telegram, kiểm dịch tệp ClamAV và két sắt OpenBao ở mức ứng dụng, luồng NATS/Parquet/DuckDB, `xt backup`/`xt restore`, route `/workflow/v1` công khai, `xt-desk` trong Compose chưa chạy thật cùng PostgREST (xem `docs/deploy.md`).

## Bắt đầu nhanh

Cần Docker Engine (hoặc Podman), `uv`, Node (nvm), `make`. Từ bản sạch Ubuntu 24.04: xem [INSTALL.md](INSTALL.md).

```sh
make setup            # uv sync
make check            # lint, mypy, import-linter, reuse lint, pytest (+ Desk nếu đã desk-setup)
make desk-setup && make desk-build   # Desk (cấu hình trước khi dịch: desk/.env)
make up-minimal && make provision-realm   # stack tối thiểu bằng Docker Engine
make release          # dist/xt-<version>.tar.gz + SBOM + SHA256SUMS
```

## Giấy phép và mục đích giấy phép

xT dùng **Apache-2.0** (OSI-approved). **Mục đích:** cho phép sinh viên, cộng đồng và doanh nghiệp dùng, sửa, phân phối lại và đóng góp, kể cả trong sản phẩm thương mại, với điều khoản cấp quyền sáng chế rõ ràng và yêu cầu giữ thông báo bản quyền; đồng thời tương thích với các phụ thuộc MIT/BSD/Apache và với thư viện LGPL chỉ được import không sửa (SpiffWorkflow, psycopg). Mỗi tệp mã có header `SPDX-License-Identifier: Apache-2.0` (kiểm bằng `reuse lint` và `scripts/check_spdx.sh`).

- Toàn văn: [LICENSE](LICENSE), thông báo: [NOTICE](NOTICE).
- Chiến lược và ma trận tương thích: [docs/LICENSING.md](docs/LICENSING.md).
- Danh mục thư viện, giấy phép, nguồn: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Không vendor mã của dự án khác; SBOM CycloneDX đi kèm mỗi bản phát hành.

## Tài liệu

[INSTALL.md](INSTALL.md) · [docs/dev-setup.md](docs/dev-setup.md) · [docs/architecture-v2.md](docs/architecture-v2.md) · [docs/deploy.md](docs/deploy.md) · [docs/decisions.md](docs/decisions.md) · [docs/glossary.md](docs/glossary.md) · [SECURITY.md](SECURITY.md)

## Bug tracker, changelog, đóng góp

- **Bug tracker:** GitHub Issues của repo (mẫu **Báo lỗi** và **Đề xuất tính năng** trong `.github/ISSUE_TEMPLATE/`). Không đăng bí mật hay dữ liệu cá nhân; lỗ hổng bảo mật báo theo [SECURITY.md](SECURITY.md).
- **Changelog:** [CHANGELOG.md](CHANGELOG.md) (Keep a Changelog).
- **Đóng góp:** [CONTRIBUTING.md](CONTRIBUTING.md) (SPDX, kiểm tra, DCO sign-off), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), mẫu PR trong `.github/`.
