<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Changelog

Các thay đổi đáng chú ý của xT được ghi trong tệp này.
Định dạng theo [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); số phiên bản theo [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Model engine vòng 2: audit thuộc `xt_audit_owner` (cài trigger qua `xt_audit.install`, event trigger chặn tắt/xóa/giả trigger audit và RULE, TRUNCATE có event); khóa HMAC không bao giờ vào Postgres (app tính HMAC, chỉ transaction ghi field sensitive nhận chuỗi HMAC); neo đầu chuỗi audit ra ngoài DB bằng checkpoint ký Ed25519 và lệnh `xt audit checkpoint|verify --against-checkpoint`; apply chặn khi còn job pha 2 chưa xong ở mọi version; worker pha 2 claim theo tenant; regex thu hẹp thêm; hướng dẫn cấu hình log Postgres khi triển khai.
**Lõi và MCP**

- MCP server (`xt serve --transport http|stdio`): 17 `ToolContract` v0.1 dưới dạng dữ liệu, hồ sơ `full` và `core-10` (3.920 token đo trên qwen3.5:4b; catalog 17 tool: 8.159). Pipeline chung: IAM + SoD trước mọi xác nhận, idempotency, mức tự chủ L0-L3 → `PendingOperation` + `confirm_url`, làn che PII, kiểm lại `outputSchema`. `upload_file` và `aggregate_records` trả `capability_unavailable` tới v0.2. Streamable HTTP stateless, chống DNS rebinding, prompts từ `contracts/prompts/*.json`. Tài liệu: `docs/mcp-server.md`.
- Lớp danh tính (`xt.domain.identity`, `xt.adapters.identity`): kiểm access token Keycloak cho MCP resource server (JWKS có TTL, `iss`, `aud`, allowlist `azp`, `exp/nbf/iat`, `typ=Bearer`, chỉ RS256/ES256), Protected Resource Metadata (RFC 9728), step-up `/desk/confirm` (ID token client confirm, `acr` OTP, `auth_time` tươi, nonce, SoD), biên nhận duyệt Ed25519 dùng một lần, introspection cho thao tác L2/L3/`human_only`. Allowlist động cho connector DCR (`XT_MCP_CLIENT_REGISTRY=keycloak`). Tài liệu: `docs/identity.md`.
- IAM (`xt.domain.iam`, `xt.application.iam`): policy JSON (Deny > Allow > implicit deny, wildcard không dùng regex), ranh giới tenant/XRN, `AccessGuard` (tự chủ, SoD, receipt, ủy quyền cho agent), `explain`. Đối chiếu với oracle pycasbin. Tài liệu: `docs/iam.md`.
- Model engine (`xt.domain.model`, `xt.application.model_engine`, `xt.adapters.db`): spec JSON → bảng PostgreSQL thật. Planner SAFE/RISKY/DESTRUCTIVE/BLOCKED với `plan_hash` chống TOCTOU; schema riêng `xt_t<seq>` + RLS FORCE cho từng tenant; audit hash-chain theo tenant với HMAC cho field sensitive; apply hai pha, rollout nhiều tenant có cô lập lỗi; CRUD tổng quát; `describe_model`, `propose_model_change`. Tài liệu: `docs/model-engine.md`.
- Process engine (`xt.domain.process`, `xt.application.process`, `xt.adapters.workflow.bpmn`): ProcessSpec v2 (JSON Schema 2020-12 ở `docs/schema/`) biên dịch sang BPMN 2.0 có sơ đồ; phê duyệt đa cấp, `parallel`, timer, SLA và leo thang, outcome `approve/reject/return_for_revision/delegate/request_info`, SoD theo bước, script engine AST whitelist, parser lxml cứng. Tài liệu: `docs/process-engine.md`.
- Hàng đợi `xt worker` trên PostgreSQL (procrastinate 3.10.0): enqueue cùng transaction nghiệp vụ, timer/SLA có hủy, lịch IANA, idempotency bền vững, FORCE RLS, recover worker chết có fencing. Tài liệu: `docs/worker-queue.md`.
- Dịch vụ dữ liệu headless `/data` (PostgREST đọc view theo tenant, JWT `aud=xt-data`) và `docs/headless-demo.sh`. Trạng thái từng phần: `docs/headless-data.md`.
- Lệnh vận hành `xt db migrate`, `xt tenant create`, `xt tenant bind-queue`, `xt org import`, `xt pack install`, `xt audit checkpoint|verify`.
- 3 gói nghiệp vụ dạng dữ liệu (`packages/`: đề xuất–phê duyệt, giao việc–SLA, nhân sự vòng đời), dữ liệu mẫu `seed/binh-minh`, 4 skill agent (`skills/`) và bộ eval agent (`evals/`, `docs/evals.md`).

**Desk và triển khai**

- xT Desk (`desk/`): chat tiếng Việt với LLM tương thích OpenAI, đăng nhập OIDC PKCE (client `xt-desk-agent`), MCP Apps hiển thị inline trong sandbox khác origin, giới hạn tốc độ lời gọi App, làn PII. Test: 118 vitest, 15 Playwright e2e. Tài liệu: `docs/desk.md`, `docs/e2e-desk-keycloak.md`.
- Triển khai Docker Compose (`deploy/`): profile `minimal`/`full`/`public`, Keycloak `start --optimized`, APISIX standalone, container non-root, `read_only`, `cap_drop ALL`, bí mật tự sinh vào `deploy/.env` và `~/.config/xt/app`, `make provision-realm`. Số đo footprint: `research/footprint.md`. Tài liệu: `docs/deploy.md`, `docs/deployment-public.md`.

**Dự án và phát hành**

- `INSTALL.md` (build từ nguồn trên Ubuntu 24.04), README tiếng Việt kèm tóm tắt tiếng Anh, phần mục đích giấy phép.
- `make release` → `scripts/release.sh`: wheel, `xt-<version>.tar.gz` (git archive), SBOM CycloneDX Python (`cyclonedx-bom`) và Desk (`@cyclonedx/cyclonedx-npm`), `SHA256SUMS`. Workflow `.github/workflows/release.yml` chạy khi đẩy tag `v*`.
- `.gitattributes` và quy tắc loại đề thi VFOSSA khỏi bản phát hành (`docs/decisions.md`).
- Khung dự án: Python (uv, hatchling, `src/xt/` theo kiến trúc), CLI `xt --version` và `xt doctor`, ruff, mypy strict, pytest-cov, import-linter, `reuse lint`, Makefile, GitHub Actions CI (lint, boundaries, reuse, test, Desk), mẫu GitHub Issues/PR, CONTRIBUTING, SECURITY.

### Changed

- `make desk-build` nạp `desk/.env` (hoặc `desk/.env.example`) nên cấu hình được trước khi dịch; `DESK_OIDC_SCOPE` trong `desk/.env.example` đặt trong ngoặc kép để `source` đúng.
- Role `xt_migrator` được tin cậy để đổi schema nhưng không với audit: chủ sở hữu audit tách thành `xt_audit_owner`, khóa HMAC chỉ đưa vào transaction có ghi field sensitive (`docs/decisions.md`).
- Client Desk công khai tối đa ở tier PII masked; tier `local` chỉ cho client confidential (`XT_MCP_CONFIDENTIAL_CLIENTS`).
- Access token MCP không còn `aud=account` (gỡ mapper `audience resolve` khỏi client scope `roles`).
- Contract import-linter: chỉ `xt.adapters.workflow` import SpiffWorkflow/lxml; chỉ `adapters.db` và `adapters.queue` import psycopg.

### Fixed

- Audit model engine (`docs/audit-model-engine.md`, AUD-01…17): trigger audit sinh lại từ catalog, dry-run không bao giờ sửa dữ liệu, backfill theo lô, khóa HMAC ra khỏi DB, ngữ cảnh RBAC chỉ qua `xt.set_context`, chuỗi audit tuần tự, `reconcile`/`retry_failed`, `revision_conflict`, regex chỉ tập con an toàn, nhãn cấm ký tự bidi.
- Audit MCP vòng 1-2 (`docs/audit-mcp.md`): idempotency nguyên tử, envelope lỗi qua làn PII, giới hạn độ sâu JSON, chuẩn hóa Decimal/date, kiểm lại IAM lúc thực thi xác nhận, `ErrorScrubber`, hợp đồng khóa `OperationStore`, log không chứa message/PII.
- Audit danh tính (`docs/audit-identity.md`, A1-A9): một quyết định mỗi confirmation, ID token step-up dùng một lần, digest RFC 8785, trần kích thước token, che secret trong `repr`, refetch JWKS ngoài lock.
- Audit process engine (`docs/audit-process.md`, AUD-1…9): hạn SLA tính một lần, kiểm tham số service lúc compile, ghi sự cố engine, từ chối DOCTYPE trên cây đã parse, giải lại người nhận khi mọi ứng viên nghỉ.

### Security

- Không có bearer tĩnh ở Desk; token chỉ ở bộ nhớ. Sandbox MCP App khác origin, `_meta.ui.csp` chỉ cho phép origin xT.
- APISIX chặn `/admin`, `/realms/master`, `/metrics`, `/health` từ ngoài; rate-limit DCR và token; `deploy/tests/security_smoke.sh` 39/39 PASS (`docs/deploy.md`).
- Chỉ phụ thuộc có giấy phép OSI; danh mục và công cụ SBOM ghi ở `THIRD_PARTY_NOTICES.md`. Không vendor mã của dự án khác.
