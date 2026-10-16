<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Changelog

Các thay đổi đáng chú ý của xT được ghi trong tệp này.
Định dạng theo [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); số phiên bản theo [Semantic Versioning](https://semver.org/).

## [Unreleased]

Trạng thái kiểm: mục nào chưa chạy e2e trên stack compose thật ghi "chờ e2e" (`docs/e2e.md`: 6/12 PASS ở lần chạy đầy đủ gần nhất).

### Added

**Tầng 1 (headless)**

- Dịch vụ dữ liệu `/data` (PostgREST đọc view theo tenant, JWT `aud=xt-data`, `db-schemas` động) và `docs/headless-demo.sh`. Trạng thái: `docs/headless-data.md`.
- REST `/workflow/v1` (OpenAPI 3.1, `aud=xt-workflow`, 428 + `confirm_url` cho agent, `POST /instances` dùng chung pipeline với MCP, `GET /instances/{id}` cho instance không gắn bản ghi, `POST /tasks/{ref}/decision`). Chờ e2e đầy đủ.
- REST `/files/v1` trên SeaweedFS (link ký sẵn, sha256, magic byte, hook ClamAV, RLS + audit, quota theo người/tenant, quyền đọc đi theo bản ghi gắn kèm; migration `0009`, `0011`), tool `upload_file` thật, scope `xt-files`, gói `tep-dinh-kem`. Chờ e2e.
- Scope `xt-workflow`/`xt-data` (audience riêng, không mặc định) và client `xt-cli` PKCE cho demo headless.

**MCP và nghiệp vụ**

- MCP server (`xt serve --transport http|stdio`): 17 `ToolContract` v0.1 dưới dạng dữ liệu, hồ sơ `full` và `core-10` (3.920 token đo trên qwen3.5:4b), pipeline chung (IAM + SoD trước xác nhận, idempotency, mức tự chủ L0-L3, làn che PII, kiểm lại `outputSchema`). Tài liệu: `docs/mcp-server.md`.
- Tool `start_process` (tool thứ 18; job `workflow.timer_starts` idempotent theo kỳ, migration `0012`). Tool `notify` (tool thứ 19) và migration `0014`/`0015`: **đang làm**, chưa gộp vào bàn giao (`docs/notifications.md`).
- Bước người của quy trình không gắn đề nghị (giao việc, tiếp nhận/nghỉ việc) hoàn thành bằng `transition_record` (`complete_via` trong ProcessSpec: người được giao + IAM + SoD + mức tự chủ của bước), hiện trong `get_inbox` (có `to_states`), cùng service với REST decision. Gói `giao-viec-sla`, `nhan-su-vong-doi` 0.3.2.
- 7 op an toàn cho gói giao việc/nhân sự (0.3.0); trigger SoD tầng DB chặn `decider = requester` kể cả SQL trực tiếp (migration `0010`).
- 3 MCP App (`orgchart`, `approval-card`, `record-table`) đăng ký qua ToolContract, tool app-only, CSP chặt; e2e trong Desk với server thật.
- Kênh PII `/pii/reveal`: vé một lần gắn khóa khung Desk, IAM `pii.reveal`, audit `egress` (migration `0007`, route `desk-pii`).
- Duyệt đổi model chạy đến cùng: `propose_model_change` -> người duyệt -> worker áp pha 1+2; gói `quan-tri-mo-hinh` (agent đề xuất, chỉ director/it duyệt).
- Lớp danh tính, IAM, model engine, process engine (ProcessSpec v2 -> BPMN 2.0 có sơ đồ), hàng đợi `xt worker` trên PostgreSQL (procrastinate 3.10.0): xem `docs/identity.md`, `docs/iam.md`, `docs/model-engine.md`, `docs/process-engine.md`, `docs/worker-queue.md`.
- Audit metadata `xt_meta.*` vào hash-chain của tenant (`xt_audit.install_meta`, migration `0006`), checkpoint Ed25519 ngoài DB và lệnh `xt audit checkpoint|verify --against-checkpoint`.
- Lệnh vận hành `xt db migrate`, `xt tenant create|bind-queue`, `xt org import [--mapping]`, `xt pack install`, `xt audit ...`.
- 3 gói lõi dạng dữ liệu (`de-xuat-phe-duyet`, `giao-viec-sla`, `nhan-su-vong-doi`) cùng `kenh-pii`, `thong-bao`; seed `seed/binh-minh` và tenant thứ hai `seed/hoa-mai`; `mapping.json` ánh xạ vai trò/phòng của seed sang từ vựng gói; 4 skill agent.
- Bộ eval agent (`evals/`, 73 scenario, qwen3.5:4b core-10 x3: 62% không skill / 59% có skill, đo trên mock), `docs/evals.md`.

**Bảo mật**

- DPoP (RFC 9449) cho `xt-desk-agent`: khóa non-extractable trong Desk, token gắn `cnf.jkt`, vé PII gắn `jkt`, cache jti dùng chung trên Postgres (migration `0013`); `XT_DPOP_REQUIRED=true` mặc định trong deploy.
- Quyền tối thiểu cho job role (nhóm `xt_job`, RLS restrictive theo tenant, migration `0008`); `GuardedCursor` chặn SQL động/đổi session trong job.

**Desk, triển khai, kiểm thử**

- xT Desk (`desk/`): chat tiếng Việt với LLM tương thích OpenAI, OIDC PKCE, MCP Apps trong sandbox khác origin, giới hạn tốc độ lời gọi App, làn PII, trang `/desk/confirm`. Test: 144 vitest + 19 Playwright e2e. Tài liệu: `docs/desk.md`, `docs/e2e-desk-keycloak.md`.
- Triển khai Docker Compose (`deploy/`): profile `minimal`/`full`/`public`, Keycloak `start --optimized`, APISIX standalone, container non-root, `read_only`, `cap_drop ALL`, ảnh pin digest, bí mật tự sinh, `make provision-realm`, log Postgres an toàn. Tài liệu: `docs/deploy.md`, `docs/deployment-public.md`.
- Harness e2e `make e2e` / `make demo-seed` / `make e2e-llm` (E0-E11 trên stack `minimal` thật, 2 tenant, TOTP; `docs/e2e.md`).
- `make verify-clean` (clone HEAD, chạy `make setup`, `make check`, `make release`, kiểm tarball) và `make test-integration` (một lượt: 376 passed, 15 xfailed trên clone sạch).

**Dự án và phát hành**

- `INSTALL.md` (build từ nguồn trên Ubuntu 24.04), README tiếng Việt kèm tóm tắt tiếng Anh, deck chung kết `docs/slides/xt-olp2026.html`.
- `make release` -> `scripts/release.sh`: wheel, `xt-<version>.tar.gz`, SBOM CycloneDX (Python, Desk), `SHA256SUMS`; workflow `.github/workflows/release.yml` khi đẩy tag `v*`; `.gitattributes` và quy tắc loại đề thi khỏi bản phát hành.
- Khung dự án: Python (uv, hatchling, `src/xt/`), CLI `xt`, ruff, mypy strict, pytest-cov, import-linter, `reuse lint`, Makefile, GitHub Actions CI, mẫu Issues/PR, CONTRIBUTING, SECURITY.

### Changed

- Desk: `make desk-build` nạp `desk/.env`; client public tối đa ở tier PII masked, tier `local` chỉ cho client confidential (`XT_MCP_CONFIDENTIAL_CLIENTS`); nginx có SPA fallback `/auth/callback` và header bảo mật.
- Role `xt_migrator` đổi schema nhưng không với audit: chủ sở hữu audit tách thành `xt_audit_owner`, khóa HMAC không vào Postgres (`docs/decisions.md`).
- Access token MCP không còn `aud=account`; `/mcp` từ chối token mang thêm audience `xt-workflow`/`xt-data`/`xt-files`; `xt-cli` bỏ scope `mcp-xt`.
- `XT_WORKFLOW_RELAY_SECONDS` mặc định 15 s (từ 60 s) để SLA ngắn leo thang đúng hạn; `xt doctor` và healthcheck `xt-api` đọc đúng env trên compose.
- Contract import-linter: chỉ `xt.adapters.workflow` import SpiffWorkflow/lxml; chỉ `adapters.db` và `adapters.queue` import psycopg.

### Fixed

Kiểm thử đối kháng nội bộ do agent độc lập với tác giả thực hiện; mỗi lỗi có test hồi quy. Tổng hợp theo thành phần ở `docs/gap-review.md` mục 0.3 (13 báo cáo `docs/audit-*.md`).

- model-engine (AUD-01..17, R2-01..05): trigger audit sinh lại từ catalog, dry-run không sửa dữ liệu, khóa HMAC ra khỏi DB, ngữ cảnh RBAC chỉ qua `xt.set_context`, chuỗi audit tuần tự, event trigger chặn tắt/xóa trigger audit, `apply` chặn khi còn job chưa xong, regex chỉ tập con an toàn.
- mcp (vòng 1-2): idempotency nguyên tử, `ErrorScrubber` cho envelope lỗi, giới hạn độ sâu JSON và khóa trùng, kiểm lại IAM lúc xác nhận, log không chứa message/PII.
- identity (A1-A9): một quyết định mỗi confirmation, ID token step-up dùng một lần, digest RFC 8785, che secret trong `repr`, refetch JWKS ngoài lock. iam (B1-B7): chỉ `kind == "human"` là người, giới hạn số nguyên, loader JSON từ chối NaN.
- process (AUD-1..9): hạn SLA tính một lần, kiểm tham số service lúc compile, từ chối DOCTYPE, giải lại người nhận khi mọi ứng viên nghỉ.
- queue (AQ-01..10, B12..B16): lịch không handler không còn làm hỏng vòng lặp, `GuardedCursor` chặn `DO`, `CREATE FUNCTION`, đổi `search_path`, tokenizer một lượt.
- workflow-api (WA-01..12): allowlist client deploy, kiểm đích ủy quyền, thứ tự khóa (chuỗi audit trước) tránh deadlock, 404 đồng nhất, ETag.
- pii-reveal (PIIR-01..08, R2-01..02): route `desk-pii`, token gắn bản ghi nguồn + người nhận, audit cả lần thất bại, rate limit, quota vé dùng advisory lock, thumbprint RFC 7638.
- dpop (DPA-01..08): `iat` hữu hạn, cache jti dùng chung, logout xóa khóa + `end_session`, timeout pool jti (503 < 5 s), thế hệ khóa khi logout.
- files (FB-01..10, R2-F1..F6, R2-W1, R3-C1): quota, SeaweedFS mạng nội bộ + JWT, HMAC tên tệp, khóa `(tenant, sha256)` giữa purge/finalize, `files.maintain` trong worker, retry deadlock tối đa 3 lần rồi 503.
- desk (BUG-1..9, 5 Low vòng 2): visibility tool app-only, CSP, `ui/open-link` cần user activation, kiểm `confirm_url`, markdown sanitize, không còn bearer tĩnh.
- deploy (BUG-D1/D2, C0..C4): policy DCR ẩn danh (PKCE S256), rate-limit DCR/token, `client-control` theo route, pin digest; APISIX chưa `read_only`.
- data: 1 Low (OPTIONS bỏ qua gate) giảm thiểu ở gateway.
- Desk: khóa mục tool theo lượt, nhận `confirmation_required` từ kết quả lỗi thật.

### Security

- Không có bearer tĩnh ở Desk; token ở bộ nhớ, bind DPoP. Sandbox MCP App khác origin, `_meta.ui.csp` chỉ cho phép origin xT.
- APISIX chặn `/admin`, `/realms/master`, `/metrics`, `/health` từ ngoài; rate-limit DCR và token; `deploy/tests/security_smoke.sh` 46/47 ở lần chạy e2e (mục còn lại đã sửa, chờ chạy lại; trước đó 39/39).
- Chỉ phụ thuộc có giấy phép OSI; danh mục và SBOM ở `THIRD_PARTY_NOTICES.md`. Không vendor mã của dự án khác.
