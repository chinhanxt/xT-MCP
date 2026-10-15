# xT — Đánh giá các khối xây dựng (building blocks)

Ngày khảo sát: 2026-10-15. Nguồn số liệu: GitHub REST API (`api.github.com/repos/...`, `/license`, `/releases`) gọi trực tiếp, PyPI JSON API, tài liệu chính thức. Số sao/ngày push lấy tại thời điểm khảo sát.
Quy ước: **[ĐKC]** = đã kiểm chứng trực tiếp từ file LICENSE/API/tài liệu gốc; **chưa kiểm chứng** = chỉ thấy ở nguồn thứ ba hoặc suy luận.

Ràng buộc đề OLP 2026 (đã đọc `de_thi_OLP2026.txt`): Tầng 1 headless PaaS bắt buộc có Identity/SSO, API Gateway, quản trị dữ liệu cấu trúc + phi cấu trúc, Workflow (BPMN); đề nhắc "tự động tạo API từ CSDL có phân quyền tới cấp dòng", "kiểm toán bất biến", "sổ cái mật mã bất biến"; toàn bộ phải tuân thủ phần mềm tự do nguồn mở. Bảng tham chiếu của đề (APISIX, Keycloak, Hasura, Flowable, n8n, Kestra, Redpanda, MinIO, ImmuDB...) chỉ là ví dụ — **trong đó n8n (Sustainable Use), Redpanda (BSL), ImmuDB (BSL từ 2023) và MinIO (đã archive) không dùng được** cho xT.

---

## Phát hiện quan trọng (đọc trước)

1. **immudb KHÔNG còn là OSI** [ĐKC]: LICENSE hiện tại là Business Source License 1.1, áp dụng cho "immudb Version 1.9DOM.2 or later" (phát hành 2023-12-29), Change Date 4 năm → Apache-2.0. Bản Apache-2.0 cuối là v1.9DOM.1 (2023-11-16) — cũ, không nên dùng. Đề thi liệt kê ImmuDB nhưng thực tế vi phạm ràng buộc OSI.
2. **MinIO community đã chết** [ĐKC]: repo `minio/minio` `archived=true`, push cuối 2026-04-24; README: "THIS REPOSITORY IS NO LONGER MAINTAINED", "distributed as source code only", chuyển sang AIStor (giấy phép thương mại/free không OSI).
3. **Directus không OSI** [ĐKC]: LICENSE hiện là "Monospace Sustainable Core License 1.0 (MSCL-1.0-GPL)" (commit 2026-04-17), cấm "Competing Use". Trước đó là BSL 1.1 từ v10 (chưa kiểm chứng trong phiên này, theo hiểu biết chung).
4. **NocoDB không OSI** [ĐKC]: chuyển sang "Sustainable Use License" (commit 2026-01-08, cập nhật 2026-01-29) — cùng loại với n8n.
5. **Camunda 7 CE đã archive** [ĐKC]: `camunda/camunda-bpm-platform` archived, push cuối 2025-11-04. Fork Apache-2.0 là **Operaton**.
6. **APISIX `mcp-bridge` đã deprecated** [ĐKC theo docs APISIX]: chỉ bridge stdio→SSE (transport cũ), docs khuyến cáo không dùng cho triển khai mới.
7. **Kong OSS dừng ở 3.9.x** [ĐKC phần release: bản mới nhất trên GitHub là 3.9.4, 2026-10-12]; việc Kong ngừng phát hành OSS từ 3.10 chỉ thấy trên forum/Reddit — **chưa kiểm chứng** từ thông báo chính thức.
8. **Zitadel đã là AGPL-3.0** [ĐKC], không còn Apache-2.0.
9. **MCP Python SDK v2** (mcp 2.3.0, MIT) [ĐKC] đã đổi `FastMCP` → `MCPServer` (`mcp.server.mcpserver`), nhắm spec 2026-07-28, có extension **MCP Apps** tích hợp sẵn. **FastMCP 4.x** (PrefectHQ, Apache-2.0) được xây trên SDK v2.

---

## A. MCP framework

| Tên | URL | License (ĐKC) | Hoạt động | Ưu | Nhược | Kết luận |
|---|---|---|---|---|---|---|
| MCP Python SDK v2 (`mcp`) | github.com/modelcontextprotocol/python-sdk | MIT | 24.6k★, push 2026-10-13, PyPI 2.3.0 (2026-10-02) | SDK chính thức; Streamable HTTP + stdio + SSE; spec 2026-07-28 + tương thích ngược; resource-server auth (`TokenVerifier`, `AuthSettings(resource_server_url=, validate_token_resource=)`); extension MCP Apps có sẵn; package `mcp-types` tách riêng | Không có provider Keycloak dựng sẵn; ít tiện ích (middleware, DI) hơn FastMCP; Tasks extension (SEP-2663) chưa được SDK hiện thực | Nền tảng bắt buộc (dependency gián tiếp) |
| FastMCP 4 (`fastmcp`) | github.com/PrefectHQ/fastmcp (cũ: jlowin/fastmcp, redirect) | Apache-2.0 | 28.1k★, push 2026-10-14, PyPI 4.1.0 (2026-10-08); 4.0.0 stable 2026-08-31 | Xây trên SDK v2; **`KeycloakAuthProvider`** (từ 3.2.4, yêu cầu Keycloak ≥ 26.6.0) [ĐKC docs]; `RemoteAuthProvider` + `JWTVerifier`/`IntrospectionTokenVerifier`, phục vụ `/.well-known/oauth-protected-resource`; OAuthProxy; MCP Apps (`AppConfig`, `ui://`, CSP; `FastMCPApp` từ 3.2); interactive tools (yêu cầu phê duyệt/điền thiếu), background tasks (`fastmcp-tasks`), identity assertion SEP-990 (beta), client-credentials, role checks, middleware, DI | Thêm 1 lớp trừu tượng; tốc độ phát hành cao (4.0.4→4.1.0 trong 3 tuần, nhiều bản vá bảo mật) → phải pin version; tài liệu Remote OAuth không nói về CIMD (chưa kiểm chứng hỗ trợ CIMD phía server — thực ra CIMD là việc của AS) | **Chọn** |

Ghi chú: Server MCP chỉ là *resource server*; DCR/CIMD là việc của Authorization Server (Keycloak). FastMCP lo Protected Resource Metadata (RFC 9728) và xác thực `aud`.

## B. Workflow / BPMN engine

Tiêu chí: human task phê duyệt, timer/SLA, định nghĩa quy trình là DỮ LIỆU do agent sinh + version, nhúng được trong Python, Docker nhẹ, chuẩn BPMN (đề yêu cầu).

| Tên | URL | License (ĐKC) | Hoạt động | Ưu | Nhược | Kết luận |
|---|---|---|---|---|---|---|
| SpiffWorkflow | github.com/sartography/SpiffWorkflow | LGPL-3.0 | 1.9k★, push 2026-10-12, v3.2.0 (2026-08-10) | Thư viện Python thuần, nhúng in-process (không thêm container); BPMN 2.0 + DMN; UserTask, timer/boundary events, message/signal; serialize workflow dở dang ra JSON → lưu Postgres; định nghĩa BPMN XML là dữ liệu, version dễ | Không có scheduler/persistence/inbox sẵn → tự viết (worker poll timer, bảng task); script/gateway expression chạy Python (`exec`) → **phải sandbox** script engine, cấm script tự do từ agent; cộng đồng nhỏ; LGPL: dùng như thư viện import là ổn với dự án Apache-2.0 nhưng sửa đổi SpiffWorkflow phải công bố theo LGPL | **Chọn** (lõi) |
| spiff-arena | github.com/sartography/spiff-arena | LGPL-2.1 | 147★, push 2026-10-14 | Ứng dụng hoàn chỉnh (Flask backend + React) trên SpiffWorkflow: process model, human task, permission | Có UI (trái nguyên tắc headless), nặng, ít sao; đáng **đọc mã** để học cách làm inbox/timer/persistence | Tham khảo |
| Flowable | github.com/flowable/flowable-engine | Apache-2.0 | 9.6k★, push 2026-10-13, 8.0.0 (2026-02-27) | BPMN/CMMN/DMN chuẩn mực, REST API đầy đủ (image `flowable/flowable-rest`), human task, timer, versioning deployment tốt; nằm trong bảng tham chiếu của đề | Java/JVM (~0.5–1 GB RAM, chưa kiểm chứng số đo), thêm 1 service + DB schema; tích hợp qua REST, không nhúng Python; designer free là cloud đóng | Phương án B nếu cần BPMN "chuẩn công nghiệp" |
| Operaton (fork Camunda 7) | github.com/operaton/operaton | Apache-2.0 | 491★, push 2026-10-15, v2.2.0-RC1 (2026-09-18) | Tương thích Camunda 7 (REST, external task); thay thế CE đã archive | Cộng đồng còn nhỏ, Java | Dự phòng |
| Camunda 7 CE | github.com/camunda/camunda-bpm-platform | Apache-2.0 | **archived** (2025-11-04) | — | Ngừng phát triển; Camunda 8 dùng Camunda License (không OSI — chưa kiểm chứng trong phiên này) | Loại |
| Temporal | github.com/temporalio/temporal + sdk-python | MIT / MIT | 23.7k★ / 1.2k★, push 2026-10-15, temporalio 1.34.0 | Durable execution rất mạnh, timer bền, signal/update cho human-in-the-loop | Không BPMN; workflow là **code**, không phải dữ liệu → muốn agent thiết kế phải viết interpreter riêng; server + DB nặng; không đáp ứng chữ "BPMN" của đề | Loại cho lõi (có thể dùng cho job nền sau này) |
| Conductor OSS | github.com/conductor-oss/conductor | Apache-2.0 | 32.3k★, push 2026-10-15 | Workflow định nghĩa bằng JSON (đúng kiểu "dữ liệu"), HUMAN/WAIT task, timeout, versioning | Java + Redis/ES, không BPMN | Tham khảo |
| Kestra | github.com/kestra-io/kestra | Apache-2.0 | 29.6k★, push 2026-10-14 | YAML flow, Pause task, có trong đề | Định hướng data orchestration; Java nặng; tính năng doanh nghiệp (RBAC...) ở bản EE (chưa kiểm chứng) | Loại cho lõi |
| Prefect | github.com/PrefectHQ/prefect | Apache-2.0 | 24.0k★, 3.8.8 | Python, `pause_flow_run` chờ input | Không BPMN, định hướng data pipeline | Loại |
| Windmill | github.com/windmill-labs/windmill | **Trộn**: AGPLv3 + Apache-2.0 + **proprietary** (code dưới cờ `enterprise`) [ĐKC] | 18.2k★ | Script runner/approval tốt | Giấy phép hỗn hợp có phần độc quyền → rủi ro tuân thủ | Loại |

## C. Lớp dữ liệu động (model là dữ liệu, agent đề xuất thay đổi)

| Tên | URL | License (ĐKC) | Hoạt động | Ưu | Nhược | Kết luận |
|---|---|---|---|---|---|---|
| PostgreSQL + model engine tự viết | — | PostgreSQL License | — | Toàn quyền: model (bảng/trường/rule/quyền) lưu trong catalog → sinh DDL trong transaction (Postgres DDL transactional), sinh RLS policy theo tenant/policy, diff + dry-run + phê duyệt người, lịch sử version; khớp `propose_model_change` | Phải tự viết migration planner (rename/đổi kiểu/backfill), cẩn thận lock khi ALTER bảng lớn | **Chọn** (lõi) |
| PostgREST | github.com/PostgREST/postgrest | MIT | 27.7k★, push 2026-10-14 | Tự sinh REST API từ schema, tôn trọng RLS qua JWT claim/role — đáp ứng đúng câu "tự động tạo API từ CSDL có phân quyền cấp dòng" của đề; binary Haskell nhẹ; xác thực JWT Keycloak | Phải reload schema cache sau DDL (NOTIFY); mô hình phân quyền dựa role Postgres | **Chọn** (API auto cho app Tầng 2) |
| Hasura CE v2 | github.com/hasura/graphql-engine | Apache-2.0 | 32.1k★, push 2026-10-14 | GraphQL auto, permission row-level, có trong đề | Haskell, metadata riêng (song song với catalog của ta → 2 nguồn sự thật); trọng tâm công ty chuyển sang v3/DDN, một phần công cụ v3 không mở (chưa kiểm chứng) | Phương án thay thế PostgREST |
| Supabase (self-host) | github.com/supabase/supabase | Apache-2.0 (umbrella); auth MIT, storage Apache-2.0 [ĐKC] | 111.6k★ | Mẫu "headless" đề nhắc; PostgREST + Realtime + Storage + RLS | Stack ~10 container, có Studio UI; trùng vai với Keycloak; không cần cả bộ | Học kiến trúc, lấy từng thành phần |
| Directus | github.com/directus/directus | **MSCL-1.0 (không OSI)** [ĐKC] | 38.5k★ | Headless CMS dynamic schema | Vi phạm ràng buộc OSI | Loại |
| NocoDB | github.com/nocodb/nocodb | **Sustainable Use License (không OSI)** [ĐKC] | 65.3k★ | — | Vi phạm ràng buộc OSI | Loại |
| Baserow | github.com/baserow/baserow | Lõi MIT; `premium/`, `enterprise/` giấy phép riêng [ĐKC] | 6.2k★, push 2026-10-14 | Django/Python, tạo bảng động thật trong Postgres → **mã đáng học** cho model engine | Ứng dụng có UI, không multi-tenant RLS; tính năng quyền nâng cao ở premium/enterprise | Tham khảo mã |
| Grist-core | github.com/gristlabs/grist-core | Apache-2.0 | 12.0k★, push 2026-10-14 | Spreadsheet-DB, công thức Python | Lưu SQLite theo document, không phải Postgres RLS | Loại cho lõi |

## D. Identity / OAuth 2.1 cho MCP

| Tên | URL | License (ĐKC) | Hoạt động | Hỗ trợ MCP | Kết luận |
|---|---|---|---|---|---|
| Keycloak | github.com/keycloak/keycloak | Apache-2.0 | 37.4k★, 26.8.1 (2026-10-13) | Docs chính thức "Integrating with MCP" [ĐKC]: OAuth 2.1 ✔, RFC 8414 ✔, RFC 9207 ✔, **DCR (RFC 7591) ✔** (cần client registration policy cho anonymous), **CIMD: experimental** (`--features=cimd`), **RFC 8707 Resource Indicators: experimental** (`--features=resource-indicators`); không bật thì dùng Audience mapper theo scope. Bảng tuân thủ: spec 2025-03-26 "Supported", 2025-06-18/2025-11-25/2026-07-28 "Experimental". PKCE: thuộc OAuth 2.1, docs nói Claude/VS Code dùng PKCE. FastMCP yêu cầu ≥ 26.6.0 vì lỗi DCR được sửa ở PR #45309 | **Chọn** |
| Authentik | github.com/goauthentik/authentik | MIT cho phần lớn; `authentik/enterprise/` giấy phép riêng [ĐKC] | 26.0k★ | chưa kiểm chứng | So sánh |
| Zitadel | github.com/zitadel/zitadel | AGPL-3.0 [ĐKC] | 15.3k★ | chưa kiểm chứng | So sánh (AGPL) |
| Ory Hydra | github.com/ory/hydra | Apache-2.0 | 17.6k★, push 2026-07-29 (chậm hơn) | Chỉ OAuth server, không có quản lý user/SSO/MFA → cần Kratos | So sánh |

Việc xT phải làm: realm per-tenant hoặc 1 realm + claim `tenant_id`; Audience mapper `aud = https://<host>/mcp`; bật DCR có trusted-host policy; bật thử `cimd` và `resource-indicators` (experimental) cho client mới. Việc ChatGPT connector cần DCR hay CIMD — **chưa kiểm chứng**.

## E. API Gateway

| Tên | URL | License (ĐKC) | Hoạt động | Ưu | Nhược | Kết luận |
|---|---|---|---|---|---|---|
| Apache APISIX | github.com/apache/apisix | Apache-2.0 | 17.2k★, 3.19.0 (2026-09-28) | Dự án ASF, có trong đề; plugin `openid-connect` (Keycloak), `jwt-auth`, `limit-*`, Coraza WAF (chưa kiểm chứng phiên bản), cấu hình bằng YAML (standalone) → GitOps | `mcp-bridge` deprecated, chỉ stdio→SSE → **không dùng**; với MCP chỉ làm reverse proxy Streamable HTTP (phải tắt buffering cho SSE stream, timeout dài — chưa kiểm chứng cấu hình cụ thể); cần etcd nếu không chạy standalone | **Chọn** |
| Kong Gateway OSS | github.com/Kong/kong | Apache-2.0 | 44.3k★, bản mới nhất 3.9.4 | Hệ sinh thái plugin lớn | OSS dường như dừng ở 3.9 (chưa kiểm chứng chính thức); plugin AI/MCP thuộc Enterprise (chưa kiểm chứng) | Loại |
| Envoy | github.com/envoyproxy/envoy | Apache-2.0 | 29.1k★ | Hiệu năng, CNCF | Cấu hình phức tạp cho đội nhỏ | Loại |
| agentgateway | github.com/agentgateway/agentgateway | Apache-2.0 | 5.4k★, push 2026-10-15 | Gateway thuần MCP/A2A | Thêm 1 thành phần; không trong đề | Theo dõi |
| IBM mcp-context-forge | github.com/IBM/mcp-context-forge | Apache-2.0 | 4.6k★ | MCP gateway/registry Python | Chồng chéo với chính xT | Tham khảo |

## F. Object storage (S3)

| Tên | URL | License (ĐKC) | Hoạt động | Ưu | Nhược | Kết luận |
|---|---|---|---|---|---|---|
| SeaweedFS | github.com/seaweedfs/seaweedfs | Apache-2.0 | 35.5k★, 4.48 (2026-09-28), phát hành ~2 tuần/lần | S3 API, single binary Go, nhẹ, có filer, replication | Giao diện S3 chưa đầy đủ 100% (chi tiết chưa kiểm chứng) | **Chọn** |
| MinIO | github.com/minio/minio | AGPL-3.0 | **archived** (push cuối 2026-04-24) | — | Không còn bảo trì; không binary community; AIStor không OSI | Loại |
| Garage | github.com/deuxfleurs-org/garage | AGPL-3.0 | 4.7k★, push 2026-10-12 | Nhẹ, geo-distributed | AGPL; thiếu một số API S3 (chưa kiểm chứng) | Dự phòng |

## G. MCP server ERP/business để tái sử dụng / học

| Tên | URL | License (ĐKC) | Hoạt động | Học được gì | Kết luận |
|---|---|---|---|---|---|
| frappe/mcp | github.com/frappe/mcp | MIT | 169★, push 2026-05-29 | Của chính tổ chức Frappe; cách phơi DocType (model-as-data) qua MCP | Học mã |
| mcp-server-odoo | github.com/ivnvxd/mcp-server-odoo | MPL-2.0 | 405★, push 2026-10-11 | Tool CRUD/search generic trên model Odoo; phân quyền theo model | Học mã |
| mcp-odoo | github.com/erpipe-org/mcp-odoo (cũ: tuanle96) | MIT | 423★, push 2026-08-25 | Tương tự | Học mã |
| Supabase MCP | github.com/supabase/mcp (cũ: supabase-community/supabase-mcp) | Apache-2.0 | 3.0k★, push 2026-10-14 | Tool `apply_migration`, `list_tables`, read-only mode → mẫu cho `propose_model_change` | Học mã |
| postgres-mcp (Crystal DBA) | github.com/crystaldba/postgres-mcp | MIT | 3.4k★, push 2026-08-17 | Chế độ restricted/read-only, phân tích SQL an toàn | Học mã |
| Odoo MCP Pro (Pantalytics) | mcpservers.org | Elastic License 2.0 (theo trang giới thiệu, chưa kiểm chứng repo) | — | — | Loại (không OSI) |
| AnythingMCP | anythingmcp.com | AGPL-3.0 (theo trang chủ, chưa kiểm chứng repo) | — | Adapter đa ERP, audit mỗi lời gọi | Tham khảo |

"Agent-native ERP" OSI thực thụ (model + process là dữ liệu, agent thiết kế, người duyệt): **không tìm thấy** dự án tương đương — đây là khoảng trống xT nhắm tới (kết luận từ tìm kiếm hạn chế, chưa kiểm chứng toàn diện).

## H. Audit / sổ cái bất biến

| Tên | URL | License (ĐKC) | Hoạt động | Ưu | Nhược | Kết luận |
|---|---|---|---|---|---|---|
| Bảng audit hash-chain trong Postgres (tự viết) | — | — | — | `audit_event(seq, tenant_id, actor{human/agent}, action, payload, prev_hash, hash=sha256(prev_hash‖canonical_json))`; trigger + `REVOKE UPDATE, DELETE`; tool `get_history`/`explain` đọc trực tiếp; kiểm chứng chuỗi bằng 1 truy vấn; định kỳ "neo" hash gốc (Merkle root) ra SeaweedFS/bên ngoài | Superuser DB vẫn có thể ghi đè → cần neo hash ra ngoài + tách quyền | **Chọn** |
| pgaudit | github.com/pgaudit/pgaudit | PostgreSQL License [ĐKC] | 1.7k★, push 2026-09-26 | Log mức câu lệnh/đối tượng cho kiểm toán DB | Chỉ ghi log, không chống sửa; dữ liệu log lớn | **Chọn** (bổ sung) |
| immudb | github.com/codenotary/immudb | **BSL 1.1 từ v1.9DOM.2** [ĐKC] | 9.0k★, v1.11.2 (2026-09-03) | Sổ cái Merkle có bằng chứng | Không OSI; bản Apache cuối (v1.9DOM.1, 2023-11) đã cũ | **Loại** |

---

## Stack khuyến nghị

| Vai trò (đề OLP) | Thành phần | License |
|---|---|---|
| MCP server (lõi xT) | Python 3.12+, **FastMCP 4.1.x** (trên `mcp` SDK v2), Streamable HTTP, MCP Apps | Apache-2.0 / MIT |
| Identity/SSO, OAuth 2.1 | **Keycloak 26.8.x** (DCR bật; thử `cimd`, `resource-indicators`; Audience mapper) + `KeycloakAuthProvider` | Apache-2.0 |
| API Gateway | **Apache APISIX 3.19** (standalone YAML, `openid-connect`), proxy thẳng `/mcp` và `/rest` | Apache-2.0 |
| Dữ liệu cấu trúc | **PostgreSQL 17/18** + RLS theo `tenant_id` + **model engine của xT** (catalog → DDL/RLS có phê duyệt) | PostgreSQL |
| API tự sinh cho Tầng 2 | **PostgREST** (JWT từ Keycloak, tôn trọng RLS) | MIT |
| Dữ liệu phi cấu trúc | **SeaweedFS** (S3) | Apache-2.0 |
| Workflow BPMN | **SpiffWorkflow 3.2** nhúng in-process + bảng task/timer trong Postgres + worker timer/SLA | LGPL-3.0 |
| Audit | Bảng hash-chain append-only + **pgaudit** + neo hash định kỳ | — / PostgreSQL |

Lý do:
- **Một ngôn ngữ (Python), ít container** (Postgres, Keycloak, APISIX, PostgREST, SeaweedFS, xT) → build từ mã nguồn bằng công cụ mở, chạy được bằng một `docker compose`.
- **Model và process đều là dữ liệu trong Postgres**: model catalog + BPMN XML có version, nằm cùng transaction với dữ liệu nghiệp vụ và audit → `propose_*_change` → người duyệt → áp dụng nguyên tử.
- **Đáp ứng đúng chữ trong đề**: SSO/MFA (Keycloak), API Gateway (APISIX), dữ liệu cấu trúc + auto API + RLS (Postgres + PostgREST), phi cấu trúc (SeaweedFS), BPMN (SpiffWorkflow), audit bất biến (hash-chain + pgaudit). Tất cả OSI, không dùng thứ đã archive.
- **Agent và người dùng chung một mô hình quyền**: token Keycloak → claim (tenant, roles, `actor_type`) → policy engine của xT → `SET LOCAL` cho RLS.
- Phương án B nếu SpiffWorkflow không đủ: Flowable 8 REST (Apache-2.0) sau APISIX; giữ interface `ProcessEngine` để thay được.

## Rủi ro (thẳng thắn)

1. **Keycloak cho MCP còn experimental** ở CIMD và RFC 8707; bảng tuân thủ chỉ "Supported" cho spec 2025-03-26. Client mới có thể yêu cầu CIMD; cờ experimental có thể đổi API. Cách giảm: dùng DCR + Audience mapper làm mặc định, kiểm thử thật với Claude và ChatGPT (chưa kiểm chứng hành vi ChatGPT).
2. **FastMCP thay đổi nhanh** (major 3→4 trong 2026, nhiều bản vá bảo mật) → pin version, theo dõi changelog; giới hạn dùng API đặc thù để có thể lùi về SDK v2 thuần.
3. **SpiffWorkflow**: cộng đồng nhỏ (1.9k★); thiếu persistence/scheduler sẵn → xT tự viết inbox, timer, SLA (khối lượng đáng kể); script engine chạy Python → **rủi ro bảo mật lớn nhất** khi agent sinh BPMN: phải cấm scriptTask tự do, chỉ cho expression whitelist/sandbox và service task gọi tool đã đăng ký. LGPL: nếu sửa thư viện phải công bố bản sửa.
4. **Model engine tự viết** là phần khó nhất: migration an toàn (rename, đổi kiểu, backfill, lock bảng lớn), sinh RLS đúng, tránh SQL injection qua tên định danh do agent đặt. Cần validator nghiêm ngặt + dry-run trên schema bản sao.
5. **RLS + connection pool**: phải `SET LOCAL` claim trong mỗi transaction; quên một chỗ là rò dữ liệu liên tenant. PostgREST và xT phải dùng cùng quy ước claim.
6. **APISIX với Streamable HTTP**: chưa kiểm chứng cấu hình chống buffering/timeout cho stream SSE dài; `mcp-bridge` không dùng được.
7. **Audit tự viết** không chống được superuser DB tuyệt đối → cần neo hash ra ngoài và tách quyền vận hành; giám khảo có thể hỏi "sao không dùng immudb như đề" → trả lời: immudb đã BSL, vi phạm ràng buộc OSI.
8. **SeaweedFS**: độ tương thích S3 chưa kiểm chứng đầy đủ cho mọi SDK; chạy cluster/replication cần thử nghiệm.
9. **Phạm vi đề rất rộng** (PKI/HSM, Vault, ClamAV, streaming, lakehouse, GitOps...): stack trên chỉ phủ phần *bắt buộc* của Tầng 1; các khối khác (Vault/OpenBao, ClamAV, Prometheus/Grafana) bổ sung sau, cũng cần kiểm tra license (vd. HashiCorp Vault đã BSL — dùng **OpenBao**; chưa kiểm chứng trong phiên này).
10. Số liệu sao/hoạt động là ảnh chụp ngày 2026-10-15; các nhận định từ nguồn thứ ba đã được đánh dấu "chưa kiểm chứng".

## Nguồn chính

- GitHub REST API: `api.github.com/repos/<owner>/<repo>`, `/license`, `/releases`, `/commits?path=LICENSE` (gọi ngày 2026-10-15)
- PyPI: pypi.org/pypi/{fastmcp,mcp,SpiffWorkflow,temporalio,prefect}/json
- Keycloak MCP: https://www.keycloak.org/nightly/securing-apps/mcp-authz-server
- FastMCP Keycloak: https://gofastmcp.com/integrations/keycloak ; Remote OAuth: https://gofastmcp.com/v3/servers/auth/remote-oauth ; Apps: https://gofastmcp.com/apps/overview ; Updates: https://gofastmcp.com/updates
- FastMCP 4.0.0 release: https://github.com/PrefectHQ/fastmcp/releases/tag/v4.0.0
- MCP Python SDK v2: https://py.sdk.modelcontextprotocol.io/v2/
- APISIX mcp-bridge: https://apisix.apache.org/docs/apisix/3.18/plugins/mcp-bridge/
- MinIO README: https://github.com/minio/minio
- Kong OSS thảo luận (không chính thức): https://github.com/Kong/kong/discussions/14405 , https://discuss.konghq.com/t/is-opensource-support-for-kong-ceased/13529
- SpiffWorkflow docs: https://spiffworkflow.readthedocs.io/
- Flowable: https://github.com/flowable/flowable-engine
