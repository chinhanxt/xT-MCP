<!-- SPDX-License-Identifier: Apache-2.0 -->
# xT — Đề xuất kiến trúc v1 (để debate)

Phiên bản tài liệu: `arch-v1.0-draft` · Ngày: 15/10/2026 · Tác giả: vai ARCHITECT · Trạng thái: **đề xuất có quyết định**, chờ critic (codex) và verifier phản biện.

Mỗi quyết định có dạng **Quyết định → Lý do → Bằng chứng → Phương án bị loại**. Không có bằng chứng thì ghi **[chưa kiểm chứng]**. Tài liệu nghiên cứu chưa có thì ghi **[chờ kết quả]**.

### Viết tắt nguồn bằng chứng (tất cả nằm trong `research/`)

| Mã | Tệp | Mã | Tệp |
|---|---|---|---|
| DE | `de_thi_OLP2026.txt` | CL | `olp-checklist.md` |
| PP | `pain-points.md` | RT | `red-team.md` |
| BB | `building-blocks.md` | VR | `verified-repos.md` |
| RA | `repos-auth-ui-audit.md` | REA | `rea-lessons.md` |
| SK | `sklite-lessons.md` | TC | `tool-contracts-draft.md` (bản nháp 18 tool) |
| SAUTH | `spike-mcp-auth.md` | SAPPS | `spike-mcp-apps.md` |
| SSPIFF | `spike-spiffworkflow.md` | SFR | `spike-frappe.md` |
| CR | `connector-requirements.md` | VS | `verification-spikes.md` |

**Đang chờ, chưa có trong repo lúc viết:** `spike-model-engine.md`, `spike-local-llm.md` **[chờ kết quả]**. Các quyết định phụ thuộc hai tệp này được đánh dấu **(phụ thuộc: …)** và có tiêu chí xem lại ghi rõ.

---

## 0. Mười quyết định chốt (tóm tắt để phản biện nhanh)

| # | Quyết định | Bị loại | Nguồn |
|---|---|---|---|
| D1 | **18 tool cố định**, sinh từ ToolContract duy nhất. Nghiệp vụ (model, process, policy) là dữ liệu có phiên bản | Tool sinh theo model; `execute(mode,…)`; Code Mode | TC §1, REA §1.7, RT §0.2 |
| D2 | **Bỏ Code Mode (search/execute)** khỏi mọi bản trước v1.0. Không chạy code do agent gửi | Sandbox V8/WASM; `exec` Python | RT §0.2, SK §7, SSPIFF §4 |
| D3 | **SDK chính thức `mcp` 2.3.0** (MIT) bọc sau adapter mỏng. Không dùng `fastmcp` | FastMCP 4.1 | SAUTH §3, RT Q15, VS B |
| D4 | **SpiffWorkflow 3.2.0 nhúng** trong `xt-worker` tách tiến trình. Agent chỉ nộp *process-spec YAML*, compiler sinh BPMN. Script engine whitelist | Flowable (giữ làm phương án B sau port `ProcessEngine`), FSM Frappe, Temporal | SSPIFF, BB §B, SFR §5 |
| D5 | **Postgres 17 + RLS FORCE**, một DB, shared schema, `tenant_id` mọi bảng, role runtime không BYPASSRLS | Schema-per-tenant, DB-per-tenant (Frappe multi-site) | SK §4, SFR §8 |
| D6 | **Model engine tự viết**: lưu record JSONB có kiểu theo revision model, sinh view `api.*` + index biểu thức (phụ thuộc: spike-model-engine) | Bảng vật lý theo model (DDL thật); Frappe DocType | BB §C, SFR kết luận |
| D7 | **Tầng 2 = MCP Apps + xT Desk** (host MCP Apps mỏng, tự host, đăng nhập Keycloak, kiêm *bề mặt xác nhận của con người*) + Telegram 1 chạm | Chỉ chat trong ChatGPT/Claude; frontend Vue/Nuxt truyền thống | RT Q1/Q2/Q5, SAPPS |
| D8 | **Xác nhận L2/L3 chỉ qua kênh con người độc lập với token của agent** (trang xác nhận của Desk, nút Telegram, CLI TTY ở chế độ stdio). Lời gọi `tools/call` từ iframe trong host bên thứ ba bị coi như lời gọi của agent | Tin nút bấm trong MCP App ở host ngoài; `approved=true` | TC §3.3, SAPPS §3.3, CR §1 |
| D9 | **Một realm Keycloak `xt` dành riêng cho xT**, tenant = Keycloak Organization → claim `tenant_id`; scope `mcp-xt` là *optional* (không default) + kiểm `azp` | Realm-per-tenant; scope default như spike | CR §1 (PRM chỉ dùng 1 AS), VS B lỗ hổng #1 |
| D10 | **Compose 2 profile**: `minimal` (không LLM, mục tiêu < 4 GB) và `full`. Postgres outbox là nguồn sự kiện, NATS JetStream chỉ ở `full` | Redpanda (BSL), Kafka, NATS bắt buộc | RT Q8, RT §4.1, BB "Phát hiện quan trọng" |

---

## 1. Định nghĩa sản phẩm

### 1.1. Một đoạn

xT là **lõi vận hành số headless, đa năng, tự host được** cho doanh nghiệp Việt Nam 10–200 người. Lõi gồm SSO, API Gateway, dữ liệu có cấu trúc và phi cấu trúc, workflow BPMN. Lõi lộ ra qua **ba kênh dùng chung một hợp đồng**: MCP (Streamable HTTP và stdio), REST/OpenAPI, và API đọc tự sinh (PostgREST). Người dùng làm việc qua agent mình chọn (ChatGPT, Claude, Goose + LLM local), qua MCP Apps hiển thị ngay trong chat, qua xT Desk, hoặc qua nút Telegram. Doanh nghiệp không cài thêm app nào. **Nghiệp vụ là dữ liệu**: model, quy trình, ngưỡng duyệt, SLA đều là định nghĩa có phiên bản. Agent đề xuất thay đổi, con người xem diff và duyệt, server áp dụng nguyên tử. Agent là một chủ thể bảo mật hạng nhất: có danh tính riêng, mức tự chủ L0–L3, bị chặn ở server và để lại audit hash-chain. Lõi không gọi LLM nào, nên đổi LLM không phải đổi lõi (chủ quyền số, DE §1, RT Q2).

### 1.2. Personas

| Persona | Việc chính | Kênh chính | Nỗi đau (PP) |
|---|---|---|---|
| Nhân viên (người đề xuất) | Xin tạm ứng, nghỉ phép, nhận việc | Chat + form MCP App, Telegram | PP1, PP2, PP6 |
| Quản lý / người duyệt | Duyệt, giao việc, theo dõi SLA | Thẻ duyệt MCP App, Telegram 1 chạm, Desk | PP1, PP2 |
| Kế toán / HR | Kiểm chứng từ, onboarding/offboarding | Chat + bảng việc | PP6, PP7 |
| Giám đốc | Bản tin điều hành, duyệt ngưỡng cao (MFA) | Desk, Telegram | PP5, PP13 |
| Quản trị nghiệp vụ (product owner nội bộ) | Mô tả nghiệp vụ bằng lời, agent sinh model/process, duyệt diff | Chat + diff viewer | PP12, PP14 |
| Vận hành IT (self-host) | Cài, sao lưu, cấp/thu hồi agent | CLI `xt`, compose | PP14, PP13 |
| **Agent** (chủ thể không phải người) | Gọi tool trong trần tự chủ | MCP | — |
| Nhà phát triển tích hợp | Gọi REST/PostgREST, viết gói nghiệp vụ | REST, OpenAPI | PP3 |
| *Ban giám khảo* | Build từ nguồn trên máy riêng, chấm PoF | README, `make`, CI | CL §2 |

### 1.3. Nguyên tắc "bộ tool cố định, nghiệp vụ là dữ liệu"

- **Quyết định:** `tools/list` luôn đúng 18 tool của TC §6: `inspect_workspace, describe_model, describe_process, search_records, get_record, create_record, update_record, delete_record, submit_request, decide_request, create_task, update_task, get_my_inbox, start_process, get_process_status, propose_model_change, propose_process_change, inspect_history`. Thêm model "Tài sản" hay quy trình "Onboarding" **không** đổi catalog.
- **Lý do:** catalog ổn định thì cache được, schema không phình (REA §1.4: 172 tool ≈ 496 KB), model nhỏ local chọn tool đúng hơn (RT Q13), annotations kiểm toán được một lần (REA §3).
- **Lệch với CLAUDE.md** ("~15 tool"): TC §1 lập luận 18 primitive có outcome và authority tách biệt. Gộp xuống 15 sẽ làm lẫn authority (REA §1.2: "không gộp list/execute/mutate vào 1 tool"). Đưa thành **câu hỏi mở Q1** cho chủ dự án.
- **MCP Apps không thêm tool:** UI gắn vào tool có sẵn qua `_meta.ui.resourceUri` (ví dụ `get_my_inbox` → thẻ duyệt, `propose_model_change` → diff viewer). Không dùng tool `visibility:["app"]` riêng, vì quyết định D8 khiến UI không cần quyền gì hơn agent.
- **Phương án bị loại:** (a) sinh tool theo model (drawio hiện ghi "Tool tự sinh từ model"), loại vì phình catalog và đổi `tools/list` theo dữ liệu. (b) Code Mode `search + execute`, loại theo D2.

### 1.4. Workflow theo phiên bản (theo thứ hạng PP §6)

PP §3 kết luận 8 workflow quy về 3 động cơ: Request/Decision, Task/SLA, Record lifecycle. Vì vậy mỗi phiên bản làm **động cơ** trước, workflow sau là gói dữ liệu.

| Phiên bản | Workflow | Gói dữ liệu đi kèm | Lý do chọn |
|---|---|---|---|
| v0.1 | **W1** Đề xuất & phê duyệt đa cấp (mẫu Tạm ứng + Nghỉ phép); **W2** Giao việc + SLA | `packages/office` | Hạng 1–2 PP §6; chứng minh workflow engine, Poka-yoke, HITL |
| v0.2 | **W4** Thu–chi tối thiểu (tạm ứng → ghi Ledger khi duyệt, nhắc hoàn ứng) | `packages/finance` | Tái dùng W1, thêm Ledger append-only |
| v0.3 | **W3** Onboarding/Offboarding (thu hồi quyền Keycloak là action có human gate); lát mỏng **W8** (bản tin tuần) | `packages/hr` | Hạng 3 PP §6; gắn Identity + Luật BVDLCN |
| v1.0 | **W7** Helpdesk (W1 + FTS); **W6** văn bản rút gọn (mẫu → PDF → hash → ký Ed25519 nội bộ); **W8** đầy đủ có lineage; gói **Thiết bị** (IT/OT) tạo live khi demo | `packages/helpdesk`, `packages/docs`, `packages/equipment` | Phủ §4 của đề (RT §4) |
| Sau OLP | W5 CRM (chỉ là gói dữ liệu, không code mới) | — | PP §6: thị trường bão hòa |

---

## 2. Ánh xạ vào đề thi

### 2.1. Tầng 1 — 4 dịch vụ bắt buộc (DE §3)

| Dịch vụ bắt buộc | Thành phần | Bằng chứng chạy được | Cách giám khảo kiểm |
|---|---|---|---|
| Identity/SSO | Keycloak 26.8.1: OIDC, OAuth 2.1 + PKCE S256, DCR, MFA TOTP cho người duyệt/admin | SAUTH §6 e2e PASS, VS B | Đăng nhập Desk; `curl` token; `xt doctor --auth` |
| API Gateway | Apache APISIX 3.19 (standalone YAML): TLS, `openid-connect`/JWT, `limit-count` theo `azp`, allowlist route | BB §E (chưa chạy với Streamable HTTP, **[chưa kiểm chứng]** buffering SSE) | `curl` không token → 401 ở gateway |
| Dữ liệu có cấu trúc + phi cấu trúc | Postgres 17 + RLS + PostgREST (đọc) + model engine; SeaweedFS 4.48 (S3) | BB §C, §F; SFR so sánh | `curl /data/v1/...` qua APISIX; 2 tenant cho 2 kết quả |
| Workflow | SpiffWorkflow 3.2.0 trong `xt-worker` + bảng `proc_instance` + poller timer | SSPIFF 7/7 test, VS A | Xuất BPMN XML; script `curl` 20 dòng chạy trọn W1 không qua MCP (RT Q7c) |

**Quyết định về ranh giới:** MCP server, REST và worker đều là **Tầng 1 (xT Core)**, không có UI. Tầng 2 gồm gói nghiệp vụ (YAML/BPMN), MCP Apps, skills, xT Desk, bot Telegram. `xt-api` và `xt-worker` chạy container riêng (RT Q7a). SpiffWorkflow là thư viện bên trong dịch vụ workflow, giống Flowable nhúng trong Spring (RT Q7).

### 2.2. Tầng 1 — hạng mục tùy chọn phủ tối thiểu (RT §4.1)

Nguyên tắc: mỗi ô có **một bằng chứng chạy được**, ≤ 1–2 ngày công, không thêm container vào `minimal`.

| Hạng mục (DE §2) | Phủ tối thiểu | Profile | Mốc |
|---|---|---|---|
| MFA, step-up | TOTP bắt buộc cho role `approver`, `admin`; step-up khi duyệt trên ngưỡng (ACR) **[chưa kiểm chứng ACR trên KC 26.8]** | minimal | v0.3 |
| Két sắt bí mật | OpenBao (MPL-2.0) cấp credential DB; `minimal` dùng Compose secrets (file), ghi rõ trong INSTALL | full | v1.0 |
| Kiểm dịch tệp | Upload vào bucket `quarantine/` → `clamd` quét → chuyển `files/`; demo EICAR bị chặn và ghi audit | full (minimal: tệp ở trạng thái `unscanned`, không cho tải về) | v1.0 |
| Chữ ký số/PKI/HSM | Ký Ed25519 bản PDF đã duyệt bằng thư viện `cryptography`; khóa ở OpenBao Transit (full) hoặc tệp khóa (minimal); `xt verify-doc` | full | v1.0 |
| Zero-trust biên | JWT ở APISIX + kiểm lại ở xT Core (defense in depth); rate-limit theo `azp`; WAF Coraza + CRS trên `/rest` **[chưa kiểm chứng tương thích APISIX 3.19]** | WAF ở full | v0.2 / v1.0 |
| Che PII | Nhãn `pii` trên field + policy `llm_egress` + bộ nhận dạng VN (CCCD, SĐT, STK) (mục 5.7) | minimal | v1.0 |
| Multi-tenancy | RLS + 2 tenant seed + test xuyên tenant trong CI | minimal | v0.1 |
| Bản địa hóa | Thông điệp lỗi/remediation vi/en (Babel, BSD-3); nhãn field đa ngôn ngữ trong model YAML | minimal | v0.2 |
| API tự sinh + RLS | PostgREST chỉ đọc sau APISIX | minimal | v0.2 |
| Phiên bản/lịch sử dữ liệu | Bảng `record_history` (trigger) + `inspect_history` | minimal | v0.1 |
| Đóng băng số liệu | Primitive Snapshot "khóa sổ kỳ", băm vào audit | minimal | v1.0 |
| Lớp ngữ nghĩa | `metrics:` trong model YAML, truy vấn qua `search_records` chế độ metric (không SQL tự do) **[cần debate: có cần tool thứ 19?]** | minimal | v1.0 |
| Chỉ mục toàn văn/véc-tơ | Postgres FTS + `unaccent`; pgvector ở full | minimal / full | v1.0 |
| Trộn mẫu sinh biểu mẫu | Jinja2 → HTML → PDF (WeasyPrint, BSD-3) | minimal | v1.0 |
| Serverless script | **Cố ý không có**: chỉ service task gọi action đã đăng ký + biểu thức whitelist. Giải thích bằng zero-trust | — | v0.1 |
| Nạp theo lô | Import CSV/XLSX (openpyxl, MIT) dry-run báo lỗi từng dòng, đi qua `create_record` từng dòng có idempotency | minimal | v0.3 |
| Audit bất biến + trace | Hash-chain + WORM trigger + neo Merkle root; OpenTelemetry trace id trong mọi tool call | minimal | v0.1 |
| Streaming + hàng đợi chống chối bỏ | Outbox Postgres (nguồn sự thật) → NATS JetStream có ack; cổng MQTT của NATS cho OT | full | v1.0 |
| GitOps + tự phục hồi | `xt gitops sync` reconcile cấu hình nền (route APISIX, realm, gói) từ git; healthcheck + `restart: unless-stopped`; demo `docker kill` | minimal | v1.0 |
| Sao lưu/khôi phục | `xt backup` / `xt restore` (pg_dump + bucket + realm export); CI chạy restore-test | minimal | v1.0 |
| Đa kênh / realtime | Notification router: Telegram (aiogram, MIT), email (Mailpit khi demo), SSE của Streamable HTTP | minimal | v0.2 |

### 2.3. Tầng 2 — MCP Apps + xT Desk: đánh giá trung thực

**Mâu thuẫn cần giải:** chủ dự án theo triết lý "agent chính là giao diện". Red team nói nếu demo chỉ là ô chat của ChatGPT thì Tầng 2 có thể bị coi là không có, II.9 còn khoảng 4/10 (RT Q1, §3.2), và host đóng gây câu hỏi chủ quyền số (RT Q2).

**Đánh giá:**
1. Red team đúng ở hai điểm có bằng chứng. (a) DE §3 ghi "ứng dụng minh họa **giao diện**". (b) Mọi host MCP Apps đã kiểm chứng ngoài basic-host đều là sản phẩm đóng (Claude, ChatGPT). Goose là Apache-2.0 nhưng hỗ trợ MCP Apps ở mức "experimental" (RT Q2). Open WebUI không còn OSI thuần (RT Q2).
2. Triết lý "agent là giao diện" vẫn đứng được nếu giao diện **không phải màn hình nghiệp vụ viết tay**. Thứ cần có là một *host chung* render các MCP App (mã của xT, Apache-2.0) và một *bề mặt xác nhận* mà LLM không giả được.
3. Lý do kỹ thuật mạnh nhất cho Desk là **bảo mật**, không phải thẩm mỹ. Trong host bên thứ ba, `tools/call` từ iframe được host proxy bằng **cùng token OAuth của kết nối** (SAPPS §3.3, CR §1 dòng "UI gọi tool"). Server không phân biệt được người bấm nút với model gọi tool. Muốn chứng minh "chỉ người mới duyệt được" (RT Q3, Q10) thì cần một kênh có phiên đăng nhập của chính con người.

**Quyết định (D7, D8) — dạng tối thiểu:**

| Thành phần | Có gì | Không có gì |
|---|---|---|
| **MCP Apps** (`apps/`) | 5 app ở v0.3: (1) Hộp việc/thẻ duyệt, (2) Form sinh từ `describe_model`, (3) Bảng Kanban việc + SLA, (4) Sơ đồ tổ chức, (5) Diff viewer cho proposal. v1.0 thêm (6) Bản tin điều hành có phả hệ, (7) Bản sao số thiết bị | Không có app chỉ hiển thị tĩnh (RT Q1a) |
| **xT Desk** (`desk/`) | Trang đăng nhập Keycloak (OIDC PKCE); renderer MCP Apps dựa trên gói npm `@modelcontextprotocol/ext-apps` (MIT/Apache-2.0, SAPPS §4); **trang xác nhận** `/confirm/{id}` hiển thị plan, digest, effects và nút Duyệt/Từ chối, có step-up MFA; trang "Agent của tôi" (cấp/thu hồi agent, mức L0–L3). v1.0 thêm ô chat nối LLM qua API kiểu OpenAI (Ollama/vLLM) | **Không có màn hình nghiệp vụ riêng**. Mọi hình ảnh nghiệp vụ là MCP App chạy được cả trong Claude/ChatGPT/Goose. Không có logic nghiệp vụ; chỉ gọi API công khai của Tầng 1 qua APISIX |
| **Bot Telegram** (`xt-bot`) | Thông báo + nút inline Duyệt/Từ chối (aiogram, MIT, RA §4). Liên kết `chat_id` ↔ người dùng Keycloak bằng mã một lần hiển thị trong Desk. Vượt ngưỡng MFA thì nút chuyển thành link `/confirm/{id}` | Không nhận lệnh tự do |

**Quy tắc chống "trượt thành frontend truyền thống"** (đưa vào CONTRIBUTING): một PR thêm route/màn hình vào `desk/` mà màn đó không phải host, xác nhận, đăng nhập hay quản trị agent thì bị từ chối. Tính năng cần màn hình phải là MCP App.

**Ánh xạ H-P-D-I của ứng dụng Tầng 2:** chính là **[P] e-Office** (đề xuất–duyệt, giao việc) cùng **[I] "Cổng quản trị và giám sát tác tử AI"** (trang Agent của Desk + audit). DE §2 nêu đích danh cả hai. [H] là sơ đồ tổ chức và hộp việc, [D] là bản tin điều hành. CL §3: một không gian là đủ, nên [P] là trục chính.

**Phương án bị loại:**
- *Chỉ chat trong host đóng*: rủi ro Tầng 2 bị đánh giá không đạt, điểm II.9 thấp (RT §3.3 rủi ro #1).
- *Frontend Nuxt/Vue như đội kiểu A* (RT §2.1): trái triết lý, tốn công, mất điểm nguyên gốc.
- *Desk fork nguyên `basic-host`*: dễ bị bắt lỗi "phát hành kèm/sửa code dự án khác" (CL I.5, RT §3.1). Dùng gói npm làm dependency, tự viết phần còn lại.

**Kênh demo chính** (RT khuyến nghị #1): Desk + LLM local và Goose + LLM local. ChatGPT/Claude là màn "cũng chạy được". Model LLM cụ thể (phụ thuộc: spike-local-llm **[chờ kết quả]**).

### 2.4. Bảng 7 lớp (DE §4)

| Lớp | Đề gợi ý | xT chọn (giấy phép) | Ghi chú loại bỏ |
|---|---|---|---|
| 1 Trải nghiệm & đa kênh | Nuxt/Vue, Flutter, Centrifugo, Tailwind | MCP Apps (HTML/TS, Apache-2.0), xT Desk, Telegram (aiogram MIT), host MCP bất kỳ; realtime qua SSE của Streamable HTTP | Không cần Centrifugo: SSE đủ cho thông báo |
| 2 Biên mạng & ATTT | APISIX, Keycloak, Vault, ClamAV, Coraza | APISIX (Apache-2.0), Keycloak (Apache-2.0), **OpenBao** (MPL-2.0), ClamAV (GPL-2.0, container riêng, gọi qua socket), Coraza (Apache-2.0) | Vault đã BSL (RT Q12; **[chưa kiểm chứng trong phiên]**) |
| 3 Tác nghiệp & thực thể | Hasura, Postgres(+PostGIS), MongoDB, Orion | Postgres 17 (PostgreSQL Lic.), PostgREST (MIT), model engine xT; xuất NGSI-LD qua định dạng của `get_record` (v1.0) | Hasura: thêm nguồn metadata thứ hai (BB §C); MongoDB/Orion: không cần |
| 4 Tự động hóa & DevSecOps | ArgoCD, GitLab CI, Flowable, n8n, Kestra, Velero | SpiffWorkflow (LGPL-3.0), `xt-worker`, `xt gitops sync`, CI Forgejo Actions/GitHub Actions, `xt backup` | n8n Sustainable Use; Flowable là phương án B |
| 5 Dữ liệu hội tụ, truyền phát, sổ cái | Redpanda, Iceberg, MinIO, ImmuDB | NATS JetStream (Apache-2.0), SeaweedFS (Apache-2.0), Parquet trên SeaweedFS (v1.0), audit hash-chain trên Postgres | Redpanda BSL; MinIO archived; ImmuDB BSL từ 1.9DOM.2 (BB "Phát hiện quan trọng" 1–2) |
| 6 Phân tích, không gian, trí tuệ | Trino, DuckDB, GeoServer, Metabase, Cube, Ollama/vLLM | DuckDB (MIT) trên Parquet, lớp metric trong model YAML, Ollama (MIT)/vLLM (Apache-2.0) | Metabase bỏ: thêm UI truyền thống và JVM; giấy phép **[chưa kiểm chứng]** |
| 7 Quản trị, tìm kiếm, giám sát | Vector, Parseable, OpenMetadata, Meilisearch, Qdrant, Prometheus/Grafana | OpenTelemetry SDK (Apache-2.0), endpoint `/metrics` + Prometheus (Apache-2.0, full), Postgres FTS/pgvector, sự kiện OpenLineage (spec Apache-2.0) | Meilisearch/Qdrant: Postgres đủ ở quy mô mục tiêu |

### 2.5. Bốn nguyên lý thiết kế (DE §4; BTC nói chỉ mang tính định hướng, CL §4)

| Nguyên lý | xT làm gì | Demo | Mốc |
|---|---|---|---|
| (i) Tách theo vòng đời/tốc độ dữ liệu | OLTP nóng ở Postgres; job `xt archive` xuất bản ghi đã đóng quá N ngày ra Parquet `s3://lake/`, DuckDB truy vấn | Metric 2 năm chạy trên lake, không chạm Postgres | v1.0 |
| (ii) Hội tụ IT/OT, bản sao số | Gói Thiết bị: cảm biến giả lập → MQTT (NATS) → cập nhật Record Asset → rule vượt ngưỡng tự mở phiếu W1/W7 | Nhiệt độ tăng → phiếu sửa → duyệt Telegram → app bản sao số đổi màu | v1.0 |
| (iii) Phả hệ và chất lượng dữ liệu | Mọi kết quả metric có `lineage` (model revision, bản ghi nguồn, snapshot); phát sự kiện OpenLineage; điểm chất lượng = tỷ lệ bản ghi qua ràng buộc | Bấm một con số trong bản tin → sơ đồ phả hệ | v1.0 |
| (iv) Zero-trust + GitOps + kiểm dịch biên | Mục 5 + ClamAV + reconcile | EICAR bị chặn; agent vượt quyền bị chặn; container chết tự hồi phục | v0.2 → v1.0 |

### 2.6. H-P-D-I trong xT

| Không gian | Cơ chế xT | Bằng chứng trong sản phẩm |
|---|---|---|
| [H] Con người số | Mỗi người **và mỗi agent** có danh tính Keycloak; hộp việc, sơ đồ tổ chức, Telegram | `inspect_workspace` trả danh tính và quyền của phiên |
| [P] Quy trình số | Request/Decision + BPMN + Poka-yoke trong schema và DB; SLA, leo thang | Agent nhập sai thì bị từ chối kèm remediation (RT Q3) |
| [D] Dữ liệu số | Một nguồn sự thật Postgres; Snapshot; metric có lineage | Bản tin điều hành |
| [I] Trí tuệ số | Agent là giao diện; cổng quản trị agent; RAG trên tài liệu nội bộ (v1.0) | Agent bị chặn khi tự duyệt hoặc bị prompt injection (RT Q10) |

Luận điểm (PP §5.1): xT **không bắt doanh nghiệp đi tuần tự H→P→D→I nhiều năm**. Chat là H, mỗi câu chat sinh dữ liệu có cấu trúc (P), dữ liệu tự tích lũy thành D, agent khai thác (I). Đồng thời vẫn tôn trọng "không nhảy cóc" của đề: [I] chỉ được hành động trong rào chắn Poka-yoke của [P].

---

## 3. Thành phần và stack

### 3.1. Sơ đồ thành phần

```mermaid
flowchart TB
  subgraph T2["TẦNG 2 — Năng lực số (có UI, không chứa logic lõi)"]
    direction LR
    HOSTS["Host MCP bên ngoài<br/>Claude · ChatGPT · Goose · Claude Code"]
    DESK["xT Desk<br/>host MCP Apps + trang xác nhận + quản trị agent<br/>(v1.0: chat LLM local)"]
    BOT["xt-bot Telegram<br/>thông báo + nút 1 chạm"]
    APPS["MCP Apps ui://xt/*<br/>thẻ duyệt · form · kanban · orgchart · diff · bản tin"]
    PKG["Gói nghiệp vụ packages/*<br/>model YAML · process-spec · policy · seed"]
    SKL["Skills + Prompts"]
  end

  subgraph EDGE["Biên"]
    APISIX["Apache APISIX 3.19<br/>TLS · JWT · rate-limit theo azp · WAF(full)"]
  end

  subgraph T1["TẦNG 1 — xT Core headless (không UI)"]
    direction TB
    KC["Keycloak 26.8.1<br/>realm xt · DCR · PKCE · MFA · Organizations"]
    subgraph API["xt-api (Python 3.13)"]
      MCPA["Adapter MCP<br/>Streamable HTTP /mcp · stdio"]
      RESTA["Adapter REST /rest/v1<br/>+ OpenAPI 3.1 sinh từ contract"]
      APP["Application: 18 use case<br/>IAM · autonomy · approvals · Poka-yoke · idempotency"]
      ME["Model engine"]
      PC["Process compiler<br/>spec YAML → BPMN"]
    end
    subgraph WK["xt-worker"]
      SPIFF["SpiffWorkflow 3.2 + SafeEngine"]
      SCHED["Scheduler: timer/SLA/outbox<br/>SKIP LOCKED + lease"]
      ACT["Action registry<br/>notify · ledger.post · iam.deprovision · ..."]
    end
    PG[("PostgreSQL 17<br/>RLS FORCE · audit hash-chain · jobs · outbox")]
    PGRST["PostgREST<br/>chỉ đọc view api.*"]
    S3[("SeaweedFS 4.48 S3<br/>quarantine/ · files/ · lake/ · anchors/")]
    NATS["NATS JetStream + MQTT<br/>(profile full)"]
    AV["ClamAV · OpenBao<br/>(profile full)"]
  end

  LLM["LLM local Ollama/vLLM<br/>(ngoài lõi, profile full)"]

  HOSTS -- "MCP + OAuth 2.1" --> APISIX
  DESK -- "OIDC người dùng + MCP/REST" --> APISIX
  BOT -- "REST (service account + liên kết người dùng)" --> APISIX
  APPS -. "render trong host/Desk" .- HOSTS
  APPS -. "render" .- DESK
  DESK -. "chat" .- LLM
  APISIX --> MCPA & RESTA & PGRST
  APISIX -- "/realms, /.well-known" --> KC
  MCPA & RESTA --> APP
  APP --> ME & PC
  APP -- "tenant_transaction (SET LOCAL)" --> PG
  APP -- "presigned PUT/GET" --> S3
  APP -. "verify JWT (JWKS)" .-> KC
  PGRST --> PG
  SCHED --> PG
  SPIFF --> PG
  ACT -- "Admin API (thu hồi quyền)" --> KC
  ACT --> S3
  SCHED -- "relay outbox" --> NATS
  S3 -- "quét" --> AV
  PKG -. "cài qua propose_* + duyệt" .-> APP
```

### 3.2. Stack và phiên bản

Quy tắc: mọi image pin **digest**, không dùng `:latest` (RT Q8). Phiên bản dưới đây là bản đã chạy trong spike hoặc đã đọc từ API GitHub/PyPI ngày 15/10/2026. Ô "pin lúc scaffold" nghĩa là research chưa ghi phiên bản cụ thể.

| Vai trò | Thành phần | Phiên bản | Giấy phép | Cách dùng | Profile | Nguồn |
|---|---|---|---|---|---|---|
| Ngôn ngữ lõi | CPython | 3.13 | PSF-2.0 | runtime | — | SAUTH §1, SSPIFF |
| Quản lý gói/build | uv + hatchling | pin lúc scaffold | Apache-2.0/MIT; MIT | công cụ | — | — |
| MCP | `mcp` (SDK chính thức) | 2.3.0 | MIT | dependency | minimal | SAUTH §1–3 |
| HTTP | Starlette + uvicorn | uvicorn 0.54.0 | BSD-3 | dependency | minimal | SAUTH §1 |
| Hợp đồng/validate | Pydantic v2; `jsonschema` (test) | pin lúc scaffold | MIT | dependency | minimal | — |
| JWT | PyJWT | 2.15.1 | MIT | dependency | minimal | SAUTH §1 |
| DB driver | psycopg 3 + psycopg_pool | 3.3 | **LGPL-3.0** (import, không sửa) | dependency | minimal | SSPIFF |
| Migration lõi | Alembic (chỉ `op.execute` SQL, không autogenerate) | pin lúc scaffold | MIT | dependency | minimal | — |
| Workflow | SpiffWorkflow (+ lxml) | 3.2.0 (lxml 6.1.3) | **LGPL-3.0**; lxml BSD-3 | dependency | minimal | SSPIFF, VS A |
| S3 client | boto3 | pin lúc scaffold | Apache-2.0 | dependency | minimal | **[chưa kiểm chứng với SeaweedFS]** |
| Telegram | aiogram | pin lúc scaffold | MIT | dependency | minimal | RA §4 |
| SSO | Keycloak | 26.8.1 | Apache-2.0 | docker | minimal | SAUTH, BB §D |
| Gateway | Apache APISIX (standalone, không etcd) | 3.19.0 | Apache-2.0 | docker | minimal | BB §E |
| CSDL | PostgreSQL | 17 (spike Frappe 17.11; spike Spiff 16) | PostgreSQL | docker | minimal | SFR, SSPIFF |
| API đọc tự sinh | PostgREST | pin lúc scaffold | MIT | docker | minimal | BB §C |
| Object storage | SeaweedFS | 4.48 | Apache-2.0 | docker | minimal | BB §F |
| Event bus/MQTT | NATS server (JetStream) | pin lúc scaffold **[chưa kiểm chứng]** | Apache-2.0 | docker | full | RT §4.1 |
| Kiểm dịch | ClamAV | pin lúc scaffold | GPL-2.0 (tiến trình riêng) | docker | full | RT §4.1 |
| Bí mật | OpenBao | pin lúc scaffold | MPL-2.0 | docker | full | RT §4.1 |
| LLM local | Ollama / vLLM | (phụ thuộc: spike-local-llm) | MIT / Apache-2.0 | docker | full | RT Q2 |
| Client OSS demo | Goose | pin lúc demo | Apache-2.0 | công cụ ngoài | — | RT Q2 |
| UI host lib | `@modelcontextprotocol/ext-apps` | 2.0.3 (repo) | MIT/Apache-2.0 | npm dep, bundle lúc build | — | SAPPS §1, §4 |
| Kiểm thử | pytest, Playwright, MCP Inspector CLI 2.11.0 | — | MIT, Apache-2.0, MIT→Apache-2.0 | dev | — | SAUTH, SAPPS |
| Giấy phép CI | `reuse` lint, SBOM CycloneDX | pin lúc scaffold | **[chưa kiểm chứng giấy phép công cụ]** | dev | — | RT §3.1 |

**LGPL-3.0 (SpiffWorkflow, psycopg) trong dự án Apache-2.0:** dùng như thư viện import, không sửa, không vendor. Ghi ma trận tương thích trong `LICENSING.md` (RT Q12, VR §1 #7).

### 3.3. Quyết định chi tiết có tranh luận

**(a) `mcp` 2.3.0 thay vì FastMCP 4.1 — D3.**
- Lý do: SDK chính thức bám spec; MCP Apps có sẵn trong core (`mcp.server.apps`, đã chạy e2e trong SAPPS); verifier Keycloak chỉ ~25 dòng và tự kiểm được `iss/aud/exp` (SAUTH §3, §5). FastMCP ra 4.0.4 → 4.1.0 trong 3 tuần, nhiều bản vá bảo mật (BB §A). VS B tìm ra 5 lỗ hổng ở verifier tự viết (`azp`, thu hồi, khóa xóa, `typ`/`sub`, cooldown xoay khóa). Cả 5 sửa được trong ~100 dòng do mình kiểm soát. Nếu dùng FastMCP thì vẫn phải tự kiểm lại chừng ấy điểm trên mã của người khác.
- Kiểm soát rủi ro: mọi import `mcp.*` chỉ nằm trong `xt.adapters.mcp`. Lớp ứng dụng không biết SDK. Nếu SDK v2 vỡ thì đổi adapter.
- Rủi ro mở: SDK 2.3.0 nhắm spec 2026-07-28, còn Claude hỗ trợ tới 2025-11-25 (CR §2). Khả năng tương thích ngược phải kiểm thật ở v0.2. Nếu fail, ghim bản SDK tương thích hoặc xem lại FastMCP.
- Bị loại: FastMCP. Nó vẫn là phương án dự phòng nếu cần `OIDCProxy` (SAUTH §3).

**(b) Code Mode — D2: BỎ, có điều kiện để mở lại sau v1.0.**
- Lý do: cho agent gửi code để server chạy là RCE chờ sẵn (RT §0.2). SSPIFF §4 tự tay chứng minh mọi biểu thức Python trong BPMN là điểm RCE. SK §7 cho thấy template Code Mode của sklite dựa vào V8 isolate (`globalOutbound:null`), thứ Python không có; "không thay V8 isolate bằng `exec`/`eval` Python". 18 tool có kiểu là đủ cho W1–W8 (TC §10).
- Điều kiện mở lại sau OLP: executor cô lập ở tiến trình riêng (WASM/isolate), không mạng, không secret; mỗi RPC qua đúng dispatcher IAM của 18 tool; giới hạn số call/CPU/RAM/byte. Code Mode chỉ là *cách gọi* các operation đã có, không phải authority mới (SK §7 "Port ý tưởng").
- Phần "search" của Code Mode được thay bằng `inspect_workspace` + `describe_model` + `describe_process`.

**(c) PostgREST chỉ đọc.**
- Lý do: đáp ứng đúng chữ "tự động tạo lập API từ CSDL tích hợp phân quyền tới cấp dòng" (DE §2). Binary nhẹ (BB §C). Chỉ đọc vì PostgREST dựa hoàn toàn vào RLS, không đi qua Poka-yoke, approval, idempotency và audit của xT Core. Cho ghi qua PostgREST sẽ tạo đường vòng quanh human gate.
- Thực thi: role DB `xt_pgrst` chỉ `SELECT` trên schema `api` (view sinh từ model engine, đã lọc field PII theo policy). APISIX chỉ cho `GET` tới `/data/v1/*`. Model đổi thì model engine `NOTIFY pgrst, 'reload schema'`.
- Bị loại: Hasura (metadata riêng tạo nguồn sự thật thứ hai, BB §C); không có API tự sinh (mất một ô của đề).

**(d) NATS chỉ ở profile full.**
- Lý do: outbox Postgres đủ cho đảm bảo nguyên tử ở v0.1–v0.3 (SK §6 "Outbox"). NATS phủ 3 ô của đề (streaming, hàng đợi có ack, MQTT cho OT) bằng một thành phần (RT khuyến nghị #7), nhưng không được làm nặng `minimal`.
- Bị loại: Redpanda (BSL), Kafka (nặng), bắt buộc NATS ngay v0.1.

**(e) Không ORM, migration bằng SQL thuần qua Alembic.**
- Lý do: schema lõi tĩnh, viết tay để kiểm soát RLS/trigger. Thay đổi động của doanh nghiệp đi qua model engine, không đi qua migration. Alembic cho `downgrade`, cần cho demo rollback (RT Q9).
- Bị loại: SQLAlchemy ORM, Django (kéo theo khung lớn).

---

## 4. Bố cục repo và ranh giới module

### 4.1. Cây thư mục

```text
xT/
├── pyproject.toml            # uv + hatchling; package `xt`; scripts: xt, xt-api, xt-worker, xt-bot
├── uv.lock
├── Makefile                  # config · build · up · demo-seed · test · dist · reuse
├── src/xt/
│   ├── domain/               # THUẦN: không I/O, không framework
│   │   ├── xrn.py            # xrn:{tenant}:{model}:{id}, validator định danh
│   │   ├── iam.py            # policy, evaluator Deny>Allow, wildcard fullmatch
│   │   ├── autonomy.py       # L0–L3, effective_level
│   │   ├── model_spec.py     # kiểu field, ràng buộc, pii, state machine của Record
│   │   ├── process_spec.py   # process-spec YAML (step approve/task/service/...)
│   │   ├── expr.py           # biểu thức whitelist (AST) dùng chung rule + gateway
│   │   ├── primitives/       # Actor, Record, Request, Decision, Task, Document, Ledger, ...
│   │   └── errors.py         # mã lỗi miền (không phụ thuộc MCP)
│   ├── contracts/            # NGUỒN SỰ THẬT của bề mặt
│   │   ├── tools/            # 18 ToolContract (Pydantic input/output + effects + iam + examples)
│   │   ├── effects.py        # Effects → annotations (suy ra, không viết tay)
│   │   ├── errors.py         # error envelope + JSON Schema
│   │   ├── prompts.py        # PromptContract (kiểm tên tool khi import)
│   │   └── catalog.py        # bundle $defs, digest, kiểm budget
│   ├── application/
│   │   ├── ports.py          # Protocol: UnitOfWork, Repos, ProcessEngine, BlobStore, IdentityAdmin, Notifier, Clock
│   │   ├── dispatcher.py     # authenticate→tenant→IAM→receipt→validate→CAS→rule→gate→commit+audit+outbox
│   │   ├── usecases/         # 1 module / tool: get_record.py, decide_request.py, ...
│   │   ├── model_engine/     # compile, diff, dry-run, activate
│   │   ├── process/          # compiler spec→BPMN, start/complete step
│   │   ├── approvals.py      # confirmation, action-authorization Request
│   │   ├── sla.py            # tính due/escalation theo calendar
│   │   ├── privacy.py        # projection PII theo llm_egress
│   │   └── audit.py          # append hash-chain, verify
│   ├── adapters/
│   │   ├── db/               # psycopg UoW (tenant_transaction), repos, queue, alembic/
│   │   ├── workflow/         # SafeEnvironment, serializer (_reorder), BpmnEngine: ProcessEngine
│   │   ├── blob/             # S3 SeaweedFS
│   │   ├── identity/         # KeycloakVerifier (JWKS TTL), Admin client
│   │   ├── notify/           # telegram, email
│   │   ├── mcp/              # MCPServer, đăng ký từ contracts, apps resources, prompts, instructions
│   │   ├── http/             # REST /rest/v1/tools/{name}, OpenAPI, /confirm API, health, metrics
│   │   └── cli/              # `xt` (argparse): doctor, setup, migrate, seed, audit verify, tool call
│   └── entrypoints/          # api.py, worker.py, bot.py (ghép adapter, không logic)
├── apps/                     # MCP Apps (HTML/TS), build → dist/apps/*.html lúc build, KHÔNG commit bundle
├── desk/                     # xT Desk (TS), build lúc build
├── packages/                 # gói nghiệp vụ = DỮ LIỆU: office/, finance/, hr/, helpdesk/, equipment/
│   └── office/{models/*.yaml, processes/*.yaml, policies/*.json, seed/*.yaml, README.md}
├── skills/                   # skills ship kèm sản phẩm (mục 7)
├── deploy/
│   ├── compose.yaml          # profiles: minimal, full, public
│   ├── apisix/apisix.yaml    # route standalone (GitOps)
│   ├── keycloak/realm-xt.json
│   └── env.example           # sinh bởi `make config`
├── tests/{unit,contract,integration,boundary,e2e,evals}/
├── docs/                     # architecture, INSTALL.md, LICENSING.md, tool-catalog (sinh)
└── research/                 # (đã có)
```

### 4.2. Quy tắc phụ thuộc hướng vào trong (REA §7.1 `verify:module-boundaries`)

```text
adapters (mcp, http, cli, db, workflow, blob, identity, notify)
        ↓ chỉ được import
application  ──→  contracts  ──→  domain
```

- `domain` không import gì của xT ngoài chính nó, không I/O.
- `contracts` chỉ import `domain` (kiểu dữ liệu) và Pydantic.
- `application` import `contracts` + `domain`; mọi I/O qua `ports.py`.
- `adapters` cài đặt port; **chỉ `adapters/mcp` được import `mcp.*`**, chỉ `adapters/workflow` được import `SpiffWorkflow`, chỉ `adapters/db` được import `psycopg`.
- Thực thi bằng `import-linter` trong CI (giấy phép **[chưa kiểm chứng]**; nếu không OSI thì dùng test AST tự viết ~40 dòng).
- **Khác REA có chủ ý:** thứ tự yêu cầu là "domain → contracts → application". Ở đây `contracts` phụ thuộc `domain` (input dùng kiểu XRN/FieldValue), còn `application` phụ thuộc `contracts` (use case nhận input đã validate). Hướng vẫn là vào trong.

### 4.3. ToolContract — một nguồn, sáu đầu ra

Mỗi contract (REA §8.1; TC §1.1) gồm `name, description, input_model, output_model, effects, max_autonomy, iam, errors, examples, returns_untrusted_content=True, ui_resource?`. Từ đó sinh ra:

1. Đăng ký MCP (`title` sinh từ `name`, annotations suy từ effects).
2. Route REST `POST /rest/v1/tools/{name}` cùng schema. OpenAPI 3.1 sinh trực tiếp từ JSON Schema 2020-12, nên không có schema thứ hai kiểu FastAPI.
3. Lệnh CLI `xt tool call <name> --json …`.
4. `docs/tool-catalog.json` + trang docs (CI `--check` báo drift).
5. Metadata skill (`tool_count`, `catalog_digest`).
6. Contract test (example hợp lệ, annotations == derive(effects), lỗi đúng shape).

Thiếu effects cho một tool thì **ứng dụng không khởi động và test fail** (REA §3.1, `toolEffects.ts:690`).

---

## 5. Mô hình bảo mật

### 5.1. Danh tính

| Chủ thể | Nguồn | Trong token | Ghi chú |
|---|---|---|---|
| Người dùng | Keycloak realm `xt`, user thuộc một Organization (= tenant) | `sub`, `tenant_id` (mapper từ Organization) **[chưa kiểm chứng mapper Organizations trên 26.8]**, nhóm/role thô | MFA TOTP bắt buộc với role `approver`, `admin` |
| Agent qua connector (Claude/ChatGPT/Goose) | Client DCR (public, PKCE S256) | `azp` = client_id; `sub` = người đăng nhập | xT tự đăng ký **Actor kind=agent** ở lần gọi đầu, `owner_human_ref=sub`, trần mặc định **L1**, chưa có grant thì chỉ L0 |
| Agent máy chủ (Desk chat, automation) | Keycloak service account (client credentials) | `azp` = `xt-desk-agent`, không có `sub` người | Hành động nhân danh người chỉ qua **Delegation** do chính người đó cấp trong Desk (có hạn, có trần L), ghi audit. Token exchange của Keycloak là phương án thay thế **[chưa kiểm chứng]** |
| Bot Telegram | Service account `xt-bot` + bảng liên kết `chat_id` ↔ `sub` | — | Liên kết bằng mã một lần do Desk hoặc CLI sinh |
| Chế độ stdio (v0.1) | Tệp danh tính cục bộ do `xt admin bootstrap` tạo, gắn actor trong DB | — | Chỉ cho máy tin cậy, giống peer-auth của Postgres; bị từ chối nếu chạy HTTP |

**Sửa lỗ hổng VS B (bắt buộc trước v0.2):**
- #1 `azp`: `mcp-xt` là **optional client scope**, quảng bá trong PRM `scopes_supported` và trong `scope="openid mcp-xt"` của 401. Client tự xin thì mới có `aud` MCP (CR §1 dòng Scopes). Realm `xt` dành riêng cho xT. Server từ chối `azp` thuộc danh sách client nội bộ không phải MCP (ví dụ `xt-pgrst`).
- #2 thu hồi: `accessTokenLifespan` = 5 phút; tool L2/L3 kiểm thêm trạng thái người dùng (cache sự kiện admin hoặc introspection).
- #3 khóa đã xóa: cache JWKS có TTL ngắn, tự viết, bỏ `cache_keys=True` của PyJWKClient; có test xoay khóa.
- #4: bắt buộc `sub` (với token người), `iat`, `typ=Bearer`, trần `exp−iat`.
- #5: xoay khóa theo kiểu passive → active.

### 5.2. Đánh giá quyền (học sklite, viết lại)

- **Ngôn ngữ policy:** JSON `{version:"1", statements:[{effect, action[], resource[]}]}`. Parser **strict**: từ chối field lạ, version lạ, `condition` (chưa hỗ trợ thì không âm thầm thành Allow vô điều kiện), chuỗi rỗng (SK §1).
- **Thuật toán:** gom mọi statement khớp. Có Deny thì `explicit_deny`. Có Allow thì `allow`. Còn lại `implicit_deny`. Wildcard `*` khớp **toàn chuỗi** (`re.fullmatch`), sửa lỗi literal-prefix/suffix của sklite (SK §1 "Wildcard"). Giới hạn số statement, độ dài pattern, số `*`. Cache theo `(tenant, principal, policy_revision)`.
- **Ranh giới tenant kiểm trước evaluator:** `principal.tenant_id == xrn.tenant`, nên kể cả `resource:["*"]` cũng không vượt tenant (SK §1).
- **Quyền hiệu lực** = giao của quyền actor ∩ quyền người được đại diện ∩ grant của client ∩ RLS/field rules ∩ policy hành động (TC §1.2).
- **Keycloak lo xác thực và nhóm; xT lo phân quyền.** Policy xT gắn vào nhóm/role theo tên, có phiên bản trong DB. Bị loại: Keycloak Authorization Services (khó version hóa như dữ liệu, tạo nguồn sự thật thứ hai).

### 5.3. RLS và vai trò DB

| Role | Quyền | Dùng bởi |
|---|---|---|
| `xt_owner` | Chủ bảng, chạy migration | Job migrate |
| `xt_app` | DML theo RLS, **NOBYPASSRLS, không sở hữu bảng** | xt-api, xt-worker (phần tenant) |
| `xt_control` | Chỉ claim `jobs`, đọc `proc_instance.next_due`, không đọc dữ liệu tenant | Scheduler/dispatcher |
| `xt_pgrst` | `SELECT` schema `api` | PostgREST |
| `xt_audit_writer` | `INSERT` vào `audit_log` qua hàm `SECURITY DEFINER` | Hàm append audit |

- Mọi bảng tenant: `ENABLE` + **`FORCE ROW LEVEL SECURITY`**, policy có cả `USING` và `WITH CHECK` theo `tenant_id = NULLIF(current_setting('app.tenant_id', true),'')::uuid`. FK ghép `(tenant_id, id)` để chặn liên kết chéo tenant (SK §4).
- **Đơn vị công việc bắt buộc:** `tenant_transaction(pool, tenant)` mở transaction và chạy `set_config('app.tenant_id', $1, true)`. Repository chỉ nhận `conn` của block đó. Cấm SET cấp session. Lỗi rollback/reset thì loại connection (SK §4, P0 đầu tiên của SK §8).
- PostgREST: JWT Keycloak được map qua `pre-request` function đặt `app.tenant_id` từ claim.

### 5.4. Mức tự chủ L0–L3 (TC §2)

| Mức | Nghĩa | Ví dụ | Thực thi |
|---|---|---|---|
| L0 | Đọc | `search_records`, `get_my_inbox` | IAM; không ghi audit nghiệp vụ |
| L1 | Thêm draft/comment/việc của chính mình, đảo ngược được | `create_record` draft, `propose_*` | IAM + `idempotency_key` + audit |
| L2 | Chuyển trạng thái/trách nhiệm, ảnh hưởng người khác | `update_record` field quan trọng, `submit_request`, giao việc cho người khác | Agent cần `approval_ref` hoặc `human_confirmation_ref` |
| L3 | Xóa, đổi định nghĩa, ra ngoài, không đảo ngược | `delete_record`, kích hoạt proposal, gửi ngoài | Preview + xác nhận của người; L3 phá hủy cần **2 người** (RT Q9) |

- Mức tự chủ là **trần**, không phải đặc quyền. Agent ở L3 vẫn không được tự duyệt, tự ký hay tự chi tiền.
- `effective_level` do server tính từ effects thực tế của lời gọi; client chỉ thấy annotations tĩnh bảo thủ.
- Annotations là gợi ý cho host (Claude tự chạy tool `readOnlyHint:true`, luôn hỏi khi `destructiveHint:true`, CR §1). **Không bao giờ là biên bảo mật.**

### 5.5. Phê duyệt và xác nhận (D8)

Có hai loại Request dùng chung động cơ:
1. **Request nghiệp vụ** (tạm ứng, nghỉ phép) do BPMN định tuyến.
2. **Request ủy quyền hành động**: khi agent gọi tool L2/L3 thiếu bằng chứng, server trả `approval_required` (TC §3.3, REA §3.2 #4). Agent tạo draft loại `action_authorization` gắn digest plan do server preview.

Bằng chứng con người (`Confirmation`) chỉ sinh từ **kênh có phiên con người tách khỏi token agent**:

| Kênh | Cách chứng minh là người | Mốc |
|---|---|---|
| CLI `xt decide` trên TTY (stdio) | Tiến trình riêng, người gõ; danh tính cục bộ | v0.1 |
| Trang `/confirm/{id}` của Desk | Phiên OIDC của người (`azp=xt-desk`), step-up MFA khi vượt ngưỡng | v0.3 |
| Nút Telegram | Callback server-to-server từ Telegram, `chat_id` đã liên kết; vượt ngưỡng thì chuyển link `/confirm` | v0.2 |

Confirmation bind: tenant, người, agent, action, tài nguyên chính xác, digest plan, các revision, hạn, nonce. Dùng một lần, tiêu thụ cùng transaction với Decision (TC §3.3). `tools/call` từ MCP App trong host ngoài **chỉ được làm việc agent được làm**. Nút "Duyệt" trong thẻ MCP App mở `ui/open-link` tới `/confirm/{id}`.

### 5.6. Audit hash-chain

- Bảng `audit_log(tenant_id, seq, at, actor_ref, effective_actor_ref, on_behalf_of, client_azp, action, resource_ref, outcome, decision_reason, policy_revisions, trace_id, operation_id, egress, prev_hash, entry_hash)`.
- `entry_hash = sha256(prev_hash ‖ canonical_json(row))`. **Chuỗi theo từng tenant**, khóa `pg_advisory_xact_lock(hash(tenant))` để không nghẽn toàn cục.
- Trigger WORM chặn `UPDATE/DELETE/TRUNCATE`; `REVOKE` với `xt_app`. Ghi cùng transaction với mutation: audit lỗi thì rollback. Sự kiện bị từ chối ghi qua đường độc lập vì transaction nghiệp vụ đã rollback (SK §3).
- **Neo:** mỗi ngày tính Merkle root theo tenant, ghi vào `s3://anchors/` và (tùy chọn) commit vào một repo git công khai (RT Q12). `xt audit verify [--tenant]` báo `OK` hoặc `đứt chuỗi tại seq N`.
- Nói đúng mức: hash-chain **phát hiện** sửa đổi, không "chống can thiệp tuyệt đối" trước superuser (BB §H, TC §8). `pgaudit` bổ sung ở profile full.

### 5.7. Làn PII (RT Q4)

- Field trong model YAML có `pii: none|basic|sensitive`.
- Tenant policy `llm_egress` gán theo **lớp client**: `cloud` (ChatGPT/Claude), `local` (Goose/Desk + LLM nội bộ), `human` (phiên Desk của người). Giá trị: `full | masked | local_only`.
- `application/privacy.py` chiếu kết quả **trước** adapter MCP: `masked` trả token hóa (`NV-0042`, `Nguyễn V*** A`, `CCCD ****1234`). Với `local_only`, field `sensitive` bị loại và đánh dấu `omitted_fields`.
- Mọi lần dữ liệu `pii≠none` ra ngoài đều ghi `egress` vào audit. Báo cáo tác động chuyển dữ liệu (Đ.20 Luật 91/2025) sinh thành Document bởi job, đọc bằng `get_record`, **không thêm tool**.
- "Người xem thấy tên thật, LLM chỉ thấy token": làm chắc chắn trong **Desk** (phiên người). Trong host ngoài, cách iframe tự fetch REST bằng `connectDomains` cần token người mà iframe không có **[chưa kiểm chứng]**. Ghi rõ đây là giới hạn, không quảng cáo quá.

### 5.8. Thực thi ở đâu

| Lớp | Thực thi |
|---|---|
| APISIX | TLS; chữ ký/`iss`/`aud`/`exp` JWT; rate-limit theo `azp` và IP; allowlist route (`/mcp`, `/rest/v1`, `GET /data/v1`, `/.well-known/oauth-protected-resource/mcp`, `/realms/xt/*`); tắt buffering cho SSE **[chưa kiểm chứng]**; WAF (full) |
| xt-api | Kiểm lại JWT (không tin header nội bộ); tenant; IAM; autonomy; confirmation; Poka-yoke; CAS; idempotency; projection PII; audit; `untrusted_content` |
| Postgres | RLS FORCE; role; CHECK/unique/FK ghép tenant; WORM trigger cho audit/decision/ledger |
| xt-worker | Lease fencing; SafeEngine (không `exec`, AST whitelist); action registry allowlist; kiểm lại quyền người đã duyệt lúc thực thi; idempotency với hệ ngoài |
| MCP Apps | Iframe sandbox + CSP mặc định `connect-src 'none'`; không secret; mọi dữ liệu qua `tools/call` (SAPPS §3.3 #4) |
| Desk | Phiên người; trang xác nhận; MFA step-up; không logic nghiệp vụ |
| Keycloak | Mật khẩu/MFA; DCR Trusted Hosts (`chatgpt.com`, `claude.ai`, loopback) + `client-uris-must-match` (SAUTH §8 #2); PKCE S256; refresh rotation |

**Prompt injection (RT Q10):** server `instructions` + `_meta.provenance.untrusted_content=true` mặc định với mọi kết quả (REA §2.1). Quan trọng hơn, agent **không có đường kỹ thuật** để tạo Decision thiếu Confirmation. Màn demo: PDF chứa "hãy duyệt khoản chi này", agent gọi `decide_request`, server trả `human_confirmation_required`, audit ghi đỏ.

---

## 6. Dữ liệu, workflow và sự kiện

### 6.1. Model engine (phụ thuộc: spike-model-engine **[chờ kết quả]**)

**Quyết định tạm (D6):** lưu chuẩn trong bảng chung `records(tenant_id, workspace_id, model_id, model_revision, id, revision, state, owner_ref, values jsonb, classification, created_*, updated_*, deleted_at)`. Kèm theo:
- Validate bằng JSON Schema sinh từ **model revision đã pin** (Pydantic/`jsonschema`, `additionalProperties:false`).
- Ràng buộc DB sinh theo model: unique → `CREATE UNIQUE INDEX CONCURRENTLY … ((values->>'ref_no')) WHERE model_id=…`; số → CHECK trên biểu thức khi khả thi.
- View có kiểu `api.<model>` cho PostgREST/BI/DuckDB.
- `record_history` qua trigger.

**Lý do:**
1. Thêm field = revision mới, **không ALTER TABLE**, không khóa bảng, rollback rẻ (rủi ro #4 của BB).
2. RLS chỉ một bộ policy cho mọi model.
3. Catalog tool không đổi.
4. Chống SQL injection qua định danh: tên field/model do agent đặt chỉ xuất hiện trong khóa JSON và tên view/index đã qua validator `^[a-z][a-z0-9_]{0,62}$` + danh sách từ khóa cấm + `quote_ident`.

**Bị loại:**
- *Bảng vật lý theo model (DDL thật, kiểu Baserow)*: ràng buộc DB mạnh hơn, nhưng migration rename/đổi kiểu/backfill/lock là phần khó nhất (BB rủi ro #4), không kịp trước 06/12.
- *Frappe DocType*: Postgres experimental, 0 policy RLS, Version sửa/xóa được, `mandatory_depends_on` không thực thi ở server (SFR).

**Tiêu chí xem lại khi spike-model-engine có kết quả:** nếu tìm kiếm/aggregate trên 100k bản ghi/tenant qua view JSONB có p95 > 300 ms, hoặc ràng buộc cần thiết không biểu diễn được bằng index/CHECK biểu thức, thì cho phép "vật chất hóa" một model thành bảng thật ở v1.x. Interface `RecordRepo` không đổi.

**Trách nhiệm của model engine:**

| Bước | Việc | Tool |
|---|---|---|
| Parse | Model YAML: field (kiểu, required, enum, range, unique, relation XRN, `pii`, nhãn vi/en), state machine của Record, metrics | — |
| Compile | JSON Schema đã pin; kế hoạch artefact DB (index, view, CHECK) | — |
| Diff + phân loại | `additive` (thêm field optional, thêm model) / `tightening` (thêm required, unique: cần đếm vi phạm) / `destructive` (bỏ field, đổi kiểu, rename: **L3, 2 người duyệt**, có kế hoạch backfill + rollback) | `propose_model_change` dry-run |
| Dry-run | Chạy trên snapshot thật: đếm bản ghi bị ảnh hưởng/vi phạm, `affected_count=null` nếu không biết | idem |
| Gửi | Tạo Proposal bất biến + Request quản trị, **không activate** | idem `dry_run=false` |
| Activate | Worker, sau Decision người: revalidate base revision → một transaction tạo revision mới + artefact DB → `NOTIFY pgrst` → audit → event | (sau `decide_request`) |
| Giải thích | Lịch sử revision, ai đề xuất/duyệt | `inspect_history`, `describe_model` |

**Định nghĩa là dữ liệu trong DB, git là bản sao.** Theo tenant, định nghĩa sống trong `model_definitions` / `process_definitions` (có revision, sha256). Gói trong `packages/` cài vào tenant **bằng chính luồng proposal + duyệt** (`xt package install office` sinh proposal), nên không có đường cài cứng. Git reconcile chỉ áp cho cấu hình nền (route APISIX, realm, danh mục gói). Bị loại: "mỗi thay đổi nghiệp vụ là một commit git" như drawio. Với đa tenant, một repo git cho mỗi tenant là không khả thi. Xuất định nghĩa tenant ra git là tùy chọn.

Ví dụ model (rút gọn):

```yaml
id: advance_request          # tạm ứng
primitive: request
label: {vi: "Đề nghị tạm ứng", en: "Advance request"}
fields:
  - {id: amount, type: money, currency: VND, required: true, min: "1", max: "1000000000"}
  - {id: purpose, type: text, required: true, max_length: 500}
  - {id: trip_start, type: date, required: true}
  - {id: trip_end, type: date, required: true}
  - {id: national_id, type: string, pii: sensitive}
rules:
  - {id: dates_ordered, expr: "trip_end >= trip_start", message: {vi: "Ngày về phải sau ngày đi"}}
process: advance_approval     # process-spec, xem 6.2
```

### 6.2. Workflow: SpiffWorkflow + hàng đợi Postgres + poller timer

**Luồng định nghĩa:**
1. Agent/gói nộp **process-spec YAML**, không bao giờ nộp XML thô (SSPIFF §6).
2. Compiler kiểm role ∈ roles, `op` ∈ action registry, mọi `when/params` qua `expr.check` (AST whitelist), SLA là ISO-8601 duration.
3. Compiler sinh BPMN 2.0 XML: lane = role, gateway quyết định reject → `end_rejected`, boundary timer + leo thang.
4. Lưu bất biến `process_definitions(key, version, spec_yaml, bpmn_xml, sha256)`.
5. Upload BPMN ngoài: từ chối `scriptTask/preScript/postScript`, kiểm `conditionExpression/timeDuration`, parser tắt entity (chống XXE, VS "Việc còn mở"). Sửa lỗi role không escape trong compiler (VS #6).

**Luồng chạy:**
- `start_process` / `submit_request` tạo `proc_instance(key, version, state json, next_due, done)` và job đầu tiên **cùng transaction** (SK §2 #1).
- **Lưu state bằng cột `json`, không `jsonb`.** `json` giữ thứ tự key, loại hẳn bẫy mất dữ liệu đã tái hiện (SSPIFF §3, VS A). Vẫn giữ `_reorder` như lớp thứ hai và có test reload bắt buộc. State không cần truy vấn bằng JSONB, vì hộp việc được vật chất hóa vào bảng `approval_steps` / `tasks`.
- Script engine = **SafeEnvironment** mặc định: `execute` luôn raise; `evaluate` theo AST whitelist; `call_service` tra action registry; sửa bug `call_service` của 3.2.0 bằng subclass (SSPIFF §4). Test regression RCE (t4 + bộ tấn công mở rộng của VS) chạy trong CI.
- Người hoàn tất user task: use case kiểm quyền theo lane/role và Confirmation, khóa instance (`SELECT … FOR UPDATE`), `task.run()` → `do_engine_steps()` → lưu → audit → enqueue bước kế. Tất cả **một transaction**.
- Instance pin version. Deploy v2 không ảnh hưởng instance v1 (SSPIFF §5). Chuyển instance sang version mới là plan được duyệt riêng (không có ở v1.0).

**Hàng đợi `jobs` (SK §2, sửa các P0 của SK §8):**
- Cột: `id, tenant_id NOT NULL (job platform có kind riêng), kind, payload, status CHECK, attempts, max_attempts, run_after, lease_token, lease_expires_at, locked_by, dedupe_key UNIQUE, last_error`.
- Claim: `FOR UPDATE SKIP LOCKED LIMIT 1` + `attempts < max_attempts`, trả trạng thái **sau** cập nhật.
- Complete/Fail: update có điều kiện theo `(id, lease_token, status='running')`, kiểm row count. Worker mất lease không ghi đè được.
- Stale reset: hết attempts thì vào `dead` và đồng bộ trạng thái run/step.
- Backoff `min(3600, 2^attempts × U[0.8,1.2])` giây; phân biệt lỗi transient, lỗi vĩnh viễn và kết quả "chưa rõ".
- Runner 3 pha: (A) transaction ngắn kiểm lease/quyền; (B) gọi ngoài **ngoài transaction** với idempotency key `(tenant, run, step, generation)`; (C) transaction lưu output + chuyển bước + enqueue bước kế + ack. Không tuyên bố exactly-once với hệ ngoài (SK §6).

**Scheduler** (một tiến trình logic trong `xt-worker`): tick mỗi 5 giây, lấy `pg_try_advisory_lock` trên **connection riêng** (SK §2 "Scheduler"). Mỗi tick:
- quét `proc_instance.next_due <= now()` → enqueue `workflow.timer` (dedupe theo `instance:next_due`);
- quét `tasks.next_escalation_at`, `schedules.next_run_at`;
- relay outbox;
- reset job stale.

Dùng đồng hồ DB.

**Task/SLA không dùng BPMN.** Vòng đời Task là state machine trong domain (`open → in_progress → blocked/paused → done/cancelled`). SLA tính từ calendar revision + timezone IANA, có pause interval. Breach không bao giờ bị xóa (TC §8). Lý do: SSPIFF §7 khuyên vòng đời đơn giản không cần BPMN.

### 6.3. Đa tenant

- Một DB, shared schema, `tenant_id` mọi bảng, RLS FORCE (D5).
- Tenant mới tạo tức thì: một dòng `tenants` + Organization Keycloak + gói mặc định qua proposal.
- Hai tenant seed `demo_a`, `demo_b`. Test CI: tenant A quét mọi bảng qua mọi tool → 0 dòng của B, kể cả với pool size 1 (SK §4 "Test bắt buộc").
- Bị loại:
  - *Schema-per-tenant*: migration nhân N lần, khó với PostgREST.
  - *DB-per-tenant*: Frappe tạo site mất ~45 s, 271 bảng mỗi site (SFR §8).
  - *Realm-per-tenant*: PRM chỉ được dùng phần tử đầu của `authorization_servers` (CR §1 Discovery), nên một URL MCP chỉ có một issuer.

### 6.4. Sự kiện

- `outbox_events(tenant_id, id, event_type, subject_ref, payload, correlation_id, causation_id, dedupe_key, created_at, relayed_at)` ghi **cùng transaction** với mutation.
- Relay theo SK §6 đã sửa: khóa event → insert job/publish trong cùng transaction → đánh dấu đã relay → commit; unique dedupe theo event id.
- Consumer nội bộ: notification router, SLA, IT/OT rules.
- Profile full: relay sang NATS JetStream subject `xt.<tenant>.<event_type>`, consumer có ack. Ứng dụng ngoài subscribe được, nhưng **nguồn sự thật vẫn là Postgres**.

---

## 7. Skills và prompts ship kèm sản phẩm (REA §4–5, TC §9)

**Nguyên tắc:** skill dạy *cách ghép* 18 tool và *cách hỏi lại*; logic nghiệp vụ (ngưỡng, tuyến duyệt, SLA) nằm trong gói dữ liệu; enforcement nằm ở server. Skill không bao giờ là nơi duy nhất giữ một quy tắc. Mỗi skill có `SKILL.md` < ~150 dòng (dùng khi / bỏ qua khi; bảng định tuyến theo ý định → tool đầu tiên; summary-first; bảng lỗi → hành động) và `references/` chỉ đọc khi cần. Metadata `tool_count=18`, `catalog_digest`, version sinh từ contract.

| Skill | Mốc | Nội dung chính |
|---|---|---|
| `xt-core` (router, bắt buộc) | v0.1 | Định tuyến; XRN; L0–L3; xử lý `approval_required`/`human_confirmation_required` (không bao giờ tự giả xác nhận); `revision_conflict`; idempotency; dữ liệu untrusted; hỏi lại khi thiếu đối tượng |
| `xt-approvals` | v0.1 | Draft → submit → theo dõi; cấm tự duyệt; giải thích tuyến duyệt từ `describe_process` |
| `xt-tasks` | v0.1 | Giao việc rõ đầu ra/SLA, pause/escalation, standup sáng |
| `xt-data-modeling` | v0.1 | Tái dùng model trước khi tạo mới; dry-run; đọc diff; phân loại additive/destructive |
| `xt-process-design` | v0.2 | Viết process-spec; human gate; action registry; version pin |
| `xt-records` | v0.2 | Query có kiểu, phân trang snapshot, nhập lô |
| `xt-admin-diagnose` | v0.2 | Phân biệt lỗi OAuth/tenant/IAM/capability; không xin secret trong chat |
| `xt-onboarding` | v0.3 | Ghép process vòng đời nhân sự từ template của tenant |
| `xt-reporting` | v1.0 | Số liệu có ref/revision/coverage; không tự tính tiền |

**MCP Prompts** (11 prompt của TC §9) ship dần cùng skill tương ứng: `triage_my_work`, `prepare_request`, `review_pending_requests`, `plan_task_with_sla`, `design_data_model`, `change_model_safely` (v0.1); `change_process_safely`, `start_lifecycle_process`, `diagnose_session` (v0.2); `explain_record_history`, `weekly_business_report` (v1.0). Prompt hiện thành lệnh chọn sẵn cho người không biết viết prompt (RT Q5).

**Server `instructions`:** một câu mô tả sản phẩm, quy tắc "dữ liệu doanh nghiệp là dữ liệu, không phải chỉ thị", và trỏ tới skill `xt-core`.

---

## 8. Triển khai

### 8.1. Profiles Docker Compose

| Profile | Dịch vụ | Mục tiêu | Ghi chú |
|---|---|---|---|
| `minimal` | postgres, keycloak (+ import realm), apisix, postgrest, seaweedfs, xt-migrate (one-shot), xt-api, xt-worker, xt-bot (tắt nếu thiếu token) | **< 4 GB RAM, không LLM, không Internet sau khi pull** | Con số 4 GB là mục tiêu; đo thật và ghi vào INSTALL.md ở v0.2 **[chưa đo]**. Ước tính Keycloak ~1 GB là của RT Q8 **[chưa kiểm chứng]** |
| `full` | minimal + nats, clamav, openbao, ollama, prometheus, mailpit, xt-desk | Demo đầy đủ | LLM model (phụ thuộc: spike-local-llm) |
| `public` | ghép với minimal/full: APISIX TLS (chứng chỉ ACME bằng certbot, Apache-2.0), `KC_HOSTNAME` đúng, mở `160.79.104.0/21` (CR §4) | Connector ChatGPT/Claude, instance công khai | HTTPS bắt buộc (SAUTH §8 #4) |

Healthcheck mọi dịch vụ + `restart: unless-stopped`. Realm Keycloak import từ JSON, không click tay (SAUTH §4). Route APISIX là YAML standalone trong git.

### 8.2. Build từ nguồn không dùng công cụ đóng (CL I.4)

```bash
make config      # sinh deploy/.env + xt.toml có chú thích (cấu hình trước khi build)
make build       # docker/podman build image xT từ mã nguồn (uv sync --frozen; pip install .), build apps/desk bằng Node
make up          # PROFILE=minimal mặc định
make demo-seed   # 2 tenant, người dùng mẫu, cài gói office qua proposal + duyệt
xt doctor        # kiểm OAuth discovery, JWKS, RLS, worker, PostgREST, budget tools/list
```

- Hỗ trợ **Docker Engine hoặc Podman** + `podman compose`/`docker compose`. Không yêu cầu Docker Desktop (RT Q8 #4).
- CI chạy đúng các lệnh README trên runner sạch, gắn badge (RT Q8 #5).
- Không có bước sửa header thủ công. `make config` là bước cấu hình trước build (CL I.4).

### 8.3. Phát hành

- `make dist` → `xt-<semver>.tar.gz` **chỉ chứa mã nguồn** (dùng `git archive`). Không có `node_modules`, không có bundle JS đã build, không vendor (RT §3.1 I.5). Kèm `SHA256SUMS`, SBOM CycloneDX, `THIRD_PARTY_NOTICES.md` sinh tự động.
- Mỗi release có tag `vX.Y.Z`, CHANGELOG (Keep a Changelog), GitHub/Forgejo Release đính kèm tarball.
- PoF tự động trong CI: `reuse lint` (bao cả JSON/BPMN/HTML qua `REUSE.toml`; thay hoặc bổ sung `scripts/check_spdx.sh`, vốn đang loại `research/` và nhiều đuôi file, RT §3.1 I.2), `LICENSING.md` (mục đích giấy phép + ma trận tương thích LGPL/GPL tách tiến trình).
- Release đầu tiên **v0.1.0 trước 27/10** để không dính lỗi "không có release" (CL I.3).

---

## 9. Chiến lược kiểm thử và Definition of Done

### 9.1. Các tầng kiểm thử

| Tầng | Nội dung | Chạy ở |
|---|---|---|
| Unit (`tests/unit`) | IAM matcher đối kháng (đảo thứ tự policy, literal prefix/suffix, wildcard, field/version lạ, broad Allow không vượt tenant); `expr` whitelist (bộ payload RCE/DoS của SSPIFF + VS); compiler (spec độc, XML injection qua role/name); SLA/calendar; hash-chain; projection PII | mọi commit |
| Contract (`tests/contract`) | `tools/list` đúng 18 tool, snapshot digest; schema sau SDK conversion hợp Draft 2020-12; mọi example pass input và output thật; `annotations == derive(effects)`; lỗi `isError:true`, text-only, đúng error schema; budget kích thước `tools/list`; prompt tham chiếu tool thật; OpenAPI sinh khớp | mọi commit |
| Integration, Postgres thật (`tests/integration`) | RLS bằng role `xt_app` thật: SELECT/INSERT/UPDATE/DELETE sai tenant, thiếu tenant, A→pool→B, pool size 1, rollback/cancel; CAS đồng thời; idempotency (cùng key khác payload); queue: 100 job × 3 worker đếm side effect, mất lease, stale; outbox 2 relay; Spiff reload ở tiến trình mới + timer qua reload; activation proposal stale | mọi PR (service container) |
| Integration, Keycloak thật | Tái dùng `e2e.py`/`negative.py` của SAUTH + toàn bộ ca của VS B (azp, user bị disable, khóa xóa, `typ`, `alg=none`, HS256 với public key) phải ra **đúng kỳ vọng sau khi sửa** | mọi PR |
| Boundary MCP (`tests/boundary`) | SDK client in-memory + Streamable HTTP qua APISIX: 401 + PRM, kết quả lớn → `resource_constraint` + continuation, cancel ≠ timeout | mọi PR |
| E2E UI (`tests/e2e`) | Playwright: MCP Apps trên `basic-host` (như SAPPS) và trên Desk; thẻ duyệt → `/confirm` → Decision; Telegram bằng bot test | nightly |
| Agent evals (`tests/evals`) | Kịch bản dương (tạo tạm ứng, giao việc, thiết kế gói Thiết bị); **âm** (thiếu đối tượng → 0 call + hỏi lại; tự duyệt → dừng ở `human_confirmation_required`; PDF injection; xóa hàng loạt → `approval_required`); chấm routing, số lần lặp, lỗi validation, token; đáp án JSON manifest đối chiếu oracle DB; lưu transcript; ghi host/model/catalog digest (REA §7.1). Model: LLM local (phụ thuộc: spike-local-llm) + Claude + GPT. Công bố bảng tỷ lệ pass thật (RT Q3, Q13) | trước mỗi release |
| Client matrix | Claude web/Desktop, Claude Code, ChatGPT (Business/Enterprise để có write, CR §1), Goose: OAuth, `tools/list`, một L0, một L1, một `approval_required`, MCP App render, `tools/call` từ UI. Ghi rõ đã/chưa kiểm chứng | v0.2, v0.3, v1.0 |
| Vận hành | `make` trên runner sạch; `xt backup` → `xt restore` → `xt audit verify`; `docker kill xt-api` tự hồi phục; đo RAM profile minimal | tuần / release |

### 9.2. Definition of Done (mỗi thay đổi)

1. Contract cập nhật trước, catalog/docs/skill metadata sinh lại, CI `--check` không drift.
2. Test đúng tầng: thay đổi có quyền hoặc tenant phải có test âm ở tầng integration với role thật.
3. Tool L2/L3 mới: effects đã kiểm toán, có test `approval_required` hoặc `human_confirmation_required`, quyết định fail-closed (REA §8.20).
4. SPDX header mọi tệp (kể cả JSON/BPMN/HTML qua REUSE), `reuse lint` xanh.
5. CHANGELOG có mục; không `TODO`, không code giả, không fallback giả.
6. Ranh giới module xanh; không import SDK ngoài adapter.
7. Nếu đổi hành vi agent nhìn thấy: chạy lại eval liên quan, ghi số.

---

## 10. Mốc phát hành

Hôm nay: 15/10/2026. Nộp bài 17:00 06/12/2026. Chung kết 10/12/2026. Đề ghi năm 2025 ở hạn nộp, slide ghi 2026, cần xác nhận với BTC (CL §8).

| Mốc | Ngày mục tiêu | Phạm vi | Tiêu chí nghiệm thu (demo được) |
|---|---|---|---|
| **v0.1** — dùng được sớm nhất | 26/10 (tag v0.1.0 ≤ 27/10) | stdio MCP + CLI; Postgres 17 + RLS; model engine (model additive, dry-run, proposal, activate sau duyệt); Request/Decision trên SpiffWorkflow + worker + timer; Task + SLA; audit hash-chain; 18 tool (một số trả `capability_unavailable` có remediation nếu chưa làm, **không giả thành công**); skills v0.1; release tarball | (1) Claude Code/Goose qua stdio: "tạm ứng 8 triệu đi Đà Nẵng 3 ngày" → draft → submit → `xt decide` trên TTY của trưởng phòng → vượt 5 tr thì sang GĐ. (2) Nhập "ngày về trước ngày đi" → `rule_violation` kèm remediation, agent tự sửa. (3) Agent gọi `decide_request` → `human_confirmation_required`. (4) Hết SLA (PT2M trong demo) → leo thang. (5) Agent đề xuất thêm field → diff → duyệt → field có hiệu lực. (6) `xt audit verify` OK; sửa một dòng bằng psql (superuser) → báo đứt tại seq N. (7) Test RLS 2 tenant xanh |
| **v0.2** — HTTP + OAuth + ChatGPT/Claude | 09/11 | Streamable HTTP qua APISIX; Keycloak realm `xt` (DCR, PKCE, Organizations, MFA); sửa 5 lỗ hổng VS B; REST `/rest/v1` + OpenAPI; PostgREST đọc; SeaweedFS + upload presigned; Telegram 1 chạm; W4 Ledger; profile `minimal` + `public`; instance `demo.<domain>` mở từ đây (RT Q14) | (1) Thêm connector trong Claude và ChatGPT bằng URL HTTPS → OAuth → cùng đơn tạm ứng ở cả hai. (2) Script `curl` ~20 dòng chạy trọn W1 không qua MCP (RT Q7c). (3) Duyệt bằng nút Telegram. (4) Token client lạ có `aud` đúng vẫn bị 401/403 (sửa VS #1). (5) INSTALL.md có số RAM/thời gian đo thật của `minimal` |
| **v0.3** — MCP Apps, Desk, LLM local | 23/11 | 5 MCP App; xT Desk (đăng nhập, host Apps, `/confirm` + MFA step-up, trang Agent); W3 onboarding/offboarding (thu hồi quyền Keycloak có human gate); W8 lát mỏng; profile `full` với Ollama/vLLM; Goose + LLM local | (1) "1 nghiệp vụ, 3 client": tạo trong Claude, duyệt trong Desk/Goose + LLM local, thông báo qua Telegram (RT Q2). (2) Rút mạng Internet: LLM local và workflow vẫn chạy. (3) Người không biết prompt tạo đơn nghỉ phép bằng form MCP App, duyệt bằng 1 chạm (RT Q5). (4) Offboarding: quyền Keycloak bị thu hồi sau khi HR xác nhận |
| **v1.0** — bản nộp OLP | RC 30/11, **v1.0.0 03/12**, nộp ≤ 06/12 | W7, W6 rút gọn (PDF + ký Ed25519), W8 có lineage; làn PII; gói Thiết bị + IT/OT (NATS MQTT); archive Parquet + DuckDB; ClamAV + OpenBao; GitOps reconcile; backup/restore; bộ eval công bố; Desk chat (nếu Q2 = có); MCP Registry `server.json`; tài liệu VN/EN | 6 màn demo cố định có video dự phòng (RT khuyến nghị #6): tạo/duyệt trên 3 client; agent thiết kế gói Thiết bị live < 120 s; prompt injection bị chặn; EICAR bị chặn + container tự hồi phục; cảm biến → phiếu sửa; bản tin có phả hệ. PoF tự kiểm theo CL §2 đạt toàn bộ |

**Đường găng:** model engine (phụ thuộc spike-model-engine) và luồng OAuth với host thật (tương thích spec SDK 2.3.0, CR §2). Nếu v0.2 trễ quá 3 ngày, cắt Desk chat và IT/OT khỏi v1.0 trước, giữ nguyên PoF và 4 dịch vụ bắt buộc.

---

## 11. Câu hỏi mở cần chủ dự án quyết

1. **Số tool: 18 (TC) hay ~15 (CLAUDE.md)?** (a) Chấp nhận 18, cập nhật CLAUDE.md. (b) Gộp còn 15, ví dụ `inspect_workspace` + `describe_*` thành một, chấp nhận authority lẫn hơn. (c) 18 + tool thứ 19 `query_metrics` cho lớp ngữ nghĩa/bản tin. **Khuyến nghị: (a)**, xét lại (c) sau eval v0.3.
2. **xT Desk có ô chat LLM ở v1.0 không?** (a) Có: một URL cho giám khảo, không cần tài khoản ngoài, nhưng thêm vòng lặp agent ở Tầng 2. (b) Không: chat chỉ qua Goose/host ngoài, Desk chỉ là host Apps + xác nhận. **Khuyến nghị: (a)**, làm sau cùng, cắt đầu tiên nếu trễ.
3. **Phần cứng LLM cho demo và instance công khai:** (a) laptop/mini PC có GPU tại hội trường; (b) GPU thuê chạy vLLM model mở (vẫn giữ chủ quyền vì model mở và tự host); (c) chỉ CPU với model nhỏ. Phụ thuộc spike-local-llm **[chờ kết quả]**.
4. **Tài khoản và hạ tầng demo:** cần domain + VPS cho `demo.<domain>` từ v0.2; ChatGPT Business/Enterprise (Pro chỉ read/fetch, CR §1) và Claude Pro/Max. Ai cấp, ngân sách bao nhiêu? Hay demo host đóng chỉ bằng video?
5. **Repo công khai và đội thi:** repo `xT` **chưa có commit nào** (`git log` trống), trong khi PoF I.1 trừ 5đ nếu không thấy repo được dùng thực tế. Đề nghị push công khai ngay lên GitHub và mirror Codeberg/Forgejo (RT §3.1). Cần chủ dự án chốt nơi host, tên đội và 3 thành viên sinh viên + giảng viên hướng dẫn (điều kiện tham dự, CL §1).

---

## 12. Sửa cần làm trong `/home/chinhan/xT_DXOS_OLP2026.drawio` (không sửa ở bước này)

Trang 2 "xT – ánh xạ vào DX-OS" (id ô theo tệp):

| Ô | Hiện tại | Sửa thành | Lý do |
|---|---|---|---|
| `p2_21` | "MCP Server xT · Code Mode: search + execute · Tool tự sinh từ model" | "xT Core API · 18 tool cố định (ToolContract) · MCP Streamable HTTP + stdio · REST/OpenAPI cùng contract" | D1, D2; RT §0.1–0.2 |
| `p2_22` | "models/*.yaml → bảng + API + **tool** + … · Tool đặc biệt: de_xuat_mo_hinh" | "Model engine: model YAML → JSON Schema + view `api.*` + index + Poka-yoke · `propose_model_change` / `propose_process_change` (dry-run → người duyệt → activate)" | Không sinh tool; tên tool theo TC |
| `p2_24` | "MinIO" | "SeaweedFS (Apache-2.0) · quarantine → ClamAV (full)" | MinIO archived (BB §F) |
| `p2_25` | "Flowable (BPMN)" | "SpiffWorkflow 3.2 (LGPL-3.0, nhúng) trong xt-worker · timer/SLA · xuất BPMN 2.0 XML" | D4; SSPIFF |
| `p2_28` | "docker compose up (dev) … giấy phép OSI (vd. Apache-2.0 / AGPL-3.0)" | "Compose profile minimal (<4 GB, không LLM) / full / public · Docker Engine hoặc Podman · **Apache-2.0 duy nhất**" | RT §0.1; D10 |
| `p2_44` | "Dữ liệu → PostgreSQL + MinIO; Workflow → Flowable; Tầng 2 → Nhân sự [P] + MCP Apps" | "Dữ liệu → PostgreSQL + RLS + PostgREST + SeaweedFS; Workflow → SpiffWorkflow; Tầng 2 → e-Office [P] + Cổng quản trị agent [I] qua MCP Apps + xT Desk" | Đồng bộ mục 2 |
| `p2_6` | "LibreChat + Ollama" | "Goose (Apache-2.0) + Ollama/vLLM" | LibreChat chưa kiểm chứng MCP Apps (RT Q2) |
| `p2_8` | "CLI dx" | "CLI `xt` (doctor, decide, audit verify, backup)" | Tên thật |
| `p2_15` | "Dashboard [D] · Metabase" | "Bản tin điều hành [D] · MCP App + metric có lineage · DuckDB/Parquet" | Bỏ Metabase (UI truyền thống, JVM) |
| `p2_16` | "Agent [I] · RAG trên SOP/wiki" | "Cổng quản trị agent [I] · danh tính, L0–L3, thu hồi, audit · RAG FTS/pgvector (v1.0)" | DE §2 [I]; điểm nguyên gốc RT Q6 |
| `p2_19` | "Zero-trust cho từng tool call, rate-limit" | "JWT + TLS + rate-limit theo azp + WAF (full); phân quyền từng tool ở xT Core" | APISIX không thấy ngữ nghĩa tool |
| `p2_20` | "OAuth 2.1 cho MCP, mỗi agent 1 danh tính" | "Realm xt · DCR + PKCE · Organizations = tenant · MFA người duyệt · agent = client azp có trần L0–L3" | D9; mục 5.1 |
| `p2_26` | "Audit log bất biến" | "Audit hash-chain theo tenant + WORM trigger + neo Merkle root · `xt audit verify`" | Nói đúng mức (BB §H) |
| `p2_27` | "Git repo models/ — mỗi thay đổi nghiệp vụ là 1 commit" | "Định nghĩa có phiên bản trong DB (nguồn sự thật) · git cho cấu hình nền + gói · `xt gitops sync`" | Mục 6.1 |
| `p2_14` | "Orgchart · Thẻ duyệt · Bảng/biểu đồ" | "Thẻ duyệt/hộp việc · Form sinh từ model · Kanban + SLA · Orgchart · Diff proposal" | Mục 2.3 |
| `p2_12` | "Nhân sự [P] — làm sâu" | "e-Office [P]: Đề xuất & duyệt + Giao việc/SLA (trục chính); Nhân sự là gói dữ liệu" | PP §6 thứ hạng |
| Cạnh `p2_42` "commit", `p2_43` "migrate" | Model engine → git, → Postgres | "activate (transaction)" tới Postgres; bỏ hoặc đổi "commit" thành "export (tùy chọn)" | Mục 6.1 |
| **Thêm ô** | — | `xt-worker` (tách tiến trình), xT Desk (Tầng 2), PostgREST (đọc), NATS JetStream + MQTT (full), OpenBao + ClamAV (full), làn PII `llm_egress`, kênh REST `/rest/v1` | RT Q7a, §4 |

Trang 1 "Đề thi – 3 tầng & 7 lớp" (giữ nguyên nội dung đề, chỉ chú thích):
- `p1_30` (lớp 5): thêm nhãn cảnh báo "Redpanda BSL · MinIO archived · ImmuDB BSL từ 1.9DOM.2 → xT dùng NATS/SeaweedFS/hash-chain" (BB phát hiện 1–2).
- `p1_29` (lớp 4): đã ghi "tránh n8n"; thêm "Flowable: phương án B của xT".
- `p1_31` (lớp 6): ghi chú Metabase không dùng.
- `p1_44`: ghi chú hạn nộp trong đề là "06/12/2025", trên slide là "06/12/2026", cần xác nhận BTC (CL §8).
