<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Changelog

Các thay đổi đáng chú ý của xT được ghi trong tệp này.
Định dạng theo [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); số phiên bản theo [Semantic Versioning](https://semver.org/).

## [Unreleased]

Cập nhật hợp nhất sau bản ghi ngày 17/10/2026 (`1882811`), đến `c453771` trên `staging`: các mục dưới đây chỉ ghi thay đổi từ PR đã gộp. Đây là ghi nhận mã nguồn và bộ kiểm thử đã bổ sung, không phải xác nhận toàn bộ e2e của bản hợp nhất đã đạt.

### Tầng 1 lõi

- Vai trò hiệu lực lấy thêm từ `access_grant` đã duyệt; quy trình nhân sự áp quyền Keycloak sau bước IT và thu hồi theo `revoked_on`. `inspect_workspace` trả vai trò hiệu lực của chính người gọi trong `me.roles` (#45, #46, #66).
- PostgREST `/data` áp dụng vai trò được cấp và ẩn model của ứng dụng đã tắt; giữ FORCE RLS và quyền view khi nâng cấp. Sửa đường migration qua hàm `pgrst_gate` được bảo vệ từ 0019 đến 0021 (#69, #79).
- Model engine tương thích DB chưa có bảng `pack_install`; bổ sung `aggregate_explain` vào danh sách metadata miễn audit và đồng bộ kiểm thử r3 (#77, #81).
- Mở được `lineage_ref` dạng `explain:<trace_id>` qua `get_history`: lưu định nghĩa truy vấn 24 giờ, kiểm lại quyền và trả bộ lọc để đọc bản ghi nguồn. Đọc lại dùng dữ liệu hiện tại, không phải ảnh chụp lịch sử (#72).

### Tầng 2 H·P·D·I

- H có danh sách tài liệu tìm kiếm/phân trang, phòng ban từ `org_unit`, quản lý tiếp nhận nhân sự và vòng đời cấp/thu hồi quyền qua các tool hiện có (#23, #38, #71).
- P dựng form yêu cầu từ `describe_model`, tạo và gửi với khóa idempotency; D có báo cáo tổng hợp kèm `as_of`, nguồn gốc và bản ghi nguồn (#29, #34).
- Đồng nhất trạng thái cấm/hết phiên, ranh giới lỗi từng module và lý do từ chối; bỏ nút đếm trùng ở D. Bổ sung điều hướng quay lại theo hash, chuyển focus khi đổi không gian và mở trợ lý Desk từ I (#15, #47, #36).
- Đổi doanh nghiệp qua đăng xuất đầy đủ và đăng nhập lại bằng PKCE, tránh dùng tiếp phiên của doanh nghiệp cũ (#75).

### Tầng 3 cắm-rút

- Manifest khai báo ứng dụng; `inspect_workspace.apps` phản ánh ứng dụng theo tenant. Tắt/bật lại ứng dụng qua đề xuất và người duyệt, không xóa dữ liệu nghiệp vụ (#59, #60).
- Launcher chia bốn nhóm, đặt Kho ở nhóm 1; màn hình ứng dụng chung dùng manifest và schema nghiệp vụ (#55, #56).
- Bổ sung CRM nhẹ, chấm công dự án theo bảng công tuần có duyệt, tài sản và bảo trì; khai báo khối ứng dụng cho ba gói. Sự kiện cảm biến của gói tài sản chỉ là dữ liệu mô phỏng (#51, #52, #53, #64).
- Bổ sung e2e trình duyệt cho ứng dụng Tầng 3 trên bundle hợp nhất đã build (#68).

### Studio/AI

- Proxy và Studio dùng chung `request_id`; có công cụ vận hành ánh xạ một workspace cũ sang tenant xT (#28, #32).
- RAG kiểm lại từng nguồn xT dưới quyền người gọi trước khi trích dẫn; chế độ tích hợp loại tài liệu không có nguồn xT và ẩn tiêu đề nguồn bị từ chối (#39, #61).
- Danh sách lượt chạy ẩn input/kết quả của người khác với người không phải quản trị; bổ sung test endpoint chéo tenant, replay assertion song song trên POST và thu hồi quyền giữa lượt chạy (#49, #19).
- Bridge phân quyền theo từng frame; WareTrack không gọi được proxy Studio (#50).

### Kho

- Tồn và biến động gắn tham chiếu kho; phiếu đảo phải qua phê duyệt và ghi biến động ngược dấu. Model `stock_level` cho phép tìm hàng dưới ngưỡng bằng `search_records` (#27, #22, #33).
- Hiện thực `aggregate_records` phía server và ngày nghiệp vụ `doc_date`/`occurred_on`; sửa tồn bằng đọc phân trang, điền kho cho sổ cũ theo lô và làm mới `stock_level` cho hàng chưa nhập khi ghi kho tiếp theo. Sửa binding input lấy cột hệ thống `id` khi cài gói (#58, #48).
- WareTrack tách chế độ Mô phỏng/Dữ liệu nghiệp vụ, đọc qua adapter xT; nhúng cùng origin trong Kho với bridge chỉ đọc. Sửa thanh chế độ bị panel chi tiết che ở màn hình hẹp (#26, #31, #80).
- Bổ sung test lớp dữ liệu WareTrack, gỡ workflow CI upstream và ghi nguồn gốc mã (#11).

### Vận hành/bảo mật

- Backup/restore bản hợp nhất bao gồm PostgreSQL, tệp, Studio và khóa; bổ sung test phục hồi trên PostgreSQL và sửa docstring phản ánh lượt chạy. Digest checkpoint audit v2 chuẩn hóa catalog để kiểm sau `pg_dump`/`pg_restore`; checkpoint cũ cần neo lại sau nâng cấp (#30, #44, #83, #62).
- Tài liệu vận hành bao phủ backup/restore, health và trace; bổ sung ma trận quyền endpoint/tool, nguồn gốc mã, tiến độ G0–G8 và sơ đồ kiến trúc; script smoke headless ghi bằng chứng JSON (#40, #37, #67, #42).
- Kiểm SPDX mở rộng sang `.tsx`/`.mjs`/`.css`, bổ sung header và khai báo REUSE; sửa kiểm thử SPDX và bỏ ảnh tham chiếu chưa rõ quyền (#41, #57, #65).
- Giữ nguyên định danh hệ thống trong lỗi xác nhận sau khi che PII; hiển thị chi tiết từ chối cấp quyền an toàn. `demo-seed` dùng `xt org import --if-empty` để không ghi đè tenant đã có dữ liệu (#76, #74, #82).
- CI dùng Node 24 cho test launcher; fixture integration chia sẻ tenant theo session và cập nhật commit nền kiểm thử audit sau khi gỡ đề thi khỏi lịch sử (#70, #78, #63).

**Ghi chú lịch sử trước đợt cập nhật này:**

- 17/10/2026: sửa thuộc tính phòng ban/quyền đọc của người duyệt, bổ sung metadata `withdrawn` cho chủ giấy, binding `create_task` và ánh xạ priority trên gói `giao-viec-sla` 0.3.4. Kiểm lại bằng ChatGPT thật, xác nhận Desk password/TOTP, 19 test tích hợp tập trung và `make check` (1.789 Python, 154 Vitest, 19 Playwright). [Báo cáo và giới hạn](docs/mcp-chatgpt-20261017.md); mô tả tool đã làm gọn nhưng host vẫn có lúc cảnh báo “Suspicious Instruction”.

- Kiểm thử MCP thật trên Claude/ChatGPT và SDK: [báo cáo kết quả, lỗi và giới hạn](docs/mcp-live-20261016.md). Sửa APISIX response header buffer để reconnect OAuth có `id_token_hint` không trả 502; xác minh URL lỗi trước đó trả 200 sau sửa, 53 test triển khai offline đạt.

Trạng thái kiểm và giới hạn bằng chứng: `docs/verification-20261016.md` và `docs/mcp-chatgpt-20261017.md`. Catalog hiện có 19 model tool + 3 app-only, gồm `notify` với inbox nội bộ. E0–E11 đã đạt ở hai chế độ DPoP; các số liệu và ghi chú “chờ e2e”, “đang làm” bên dưới là lịch sử trước các lượt kiểm này.

### Added

- Model engine vòng 4 (mô hình đe dọa `xt_migrator`: phòng ngừa + phát hiện): chặn trigger/RULE lạ trên mọi bảng `xt_meta` (kể cả `session_ctx`, `login_tenant`), chặn đổi bảng ngữ cảnh; chặn DROP/ALTER `tenant_id` và mọi đường (kể cả CASCADE) gỡ policy RESTRICTIVE theo tenant; mọi hàm `xt`/`xt_audit`/`xt_meta` (gồm `xt.row_touch()`) chỉ superuser đổi được (chạy lại định nghĩa y hệt vẫn được); CHECK/DEFAULT/index/cột trên bảng model chỉ dùng hàm `pg_catalog` + `xt.tenant()`/`xt.actor()`, cấm domain; `xt.set_context` chỉ nhận lời gọi trực tiếp từ kết nối app; `set_policies` ghi event `POLICY` vào hash-chain; checkpoint Ed25519 mang digest toàn bộ catalog bảo mật `xt*` và `xt audit verify --against-checkpoint` in đúng đối tượng đổi/thêm/mất (exit 1). Migration `0010` dùng `CREATE OR REPLACE TRIGGER`.
- Ngữ cảnh RLS bất biến với `xt_migrator`: hàm `xt.cur()`/`xt.tenant()`/`xt.has_role()`… và mọi hàm policy tham chiếu chỉ superuser đổi được (theo oid, có trong digest checkpoint); policy bảng model chỉ qua `xt_audit.set_policies` (mẫu cố định, luôn RESTRICTIVE theo tenant), RLS + FORCE luôn bật; policy bảng `xt_meta` có audit chỉ được siết.
- Model engine vòng 3 (audit R3-01…07): mọi bảng trong schema tenant luôn có trigger audit (tự gắn), chặn SET SCHEMA/ALTER SCHEMA/INHERITS/partition/RULE; actor của audit lấy từ ngữ cảnh do `xt_audit_owner` giữ; registry cột sensitive chỉ mở rộng (hạ nhãn cần superuser `declassify`); ràng record ↔ bảng chống event giả; checkpoint kiểm chuỗi ở client + digest hàm audit; audit cho bảng quyền/danh tính/catalog (migration 0016, `install_meta2`); regex cấm lượng từ không toán hạng; fixture test xóa container kèm volume.
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

- Desk production có proxy LLM xác thực bằng token/DPoP thật; không chuyển credential người dùng tới model, có hạn payload/token/concurrency/thời gian tuyệt đối và giải phóng request khi hủy.
- Desk chạy dưới CSP không cần unsafe-eval, sandbox có header ancestor riêng; profile standard khám phá schema, dùng skill chuẩn lúc build và phản hồi validation có metadata kiểu để phục hồi lỗi.
- Model audit R4 ràng bảng ngữ cảnh theo OID, kiểm đầy đủ trigger SoD; checkpoint bao phủ định nghĩa/thuộc tính hàm, view, trigger và domain/constraint.
- Harness E2E giữ lỗi dựng/dọn stack, bảo vệ bí mật khỏi argv, kiểm thư mục secret riêng tư; fixture integration dùng tài nguyên riêng và dọn cả volume. Lỗi PII có identifier gắn tiền tố được che đồng nhất.

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
