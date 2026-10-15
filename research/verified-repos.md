# Kiểm chứng danh sách repo của agyxt (`agyxt_repos_core.md`)

Ngày kiểm: 2026-10-15. Nguồn: GitHub REST API `api.github.com/repos/*` (chưa có token, máy không có `gh`) và README/LICENSE lấy từ `raw.githubusercontent.com`.
`agyxt_repos_auth_ui.md` đến lúc kiểm vẫn rỗng (0 byte), nên chưa có gì để kiểm.

Giải thích cột "Cách dùng" (theo quy tắc OLP: không phát hành kèm code của dự án khác, không sửa code gói đính kèm):
- **dep**: phụ thuộc pip/npm, khai báo trong `pyproject`, không vendoring.
- **docker**: chạy image chính thức thành service riêng.
- **plugin**: cài thành app/extension qua API mở rộng của nền tảng.
- **ref**: chỉ đọc để học pattern, không copy code.

## 1. Bảng kiểm chứng

| # | Repo | Tồn tại | SPDX thật (OSI?) | Sao | Push cuối | Archived | Mô tả của agyxt | Cách dùng |
|---|---|---|---|---|---|---|---|---|
| 1 | modelcontextprotocol/python-sdk | có | MIT (OSI) | 24.6k | 2026-10-13 | không | Đúng | dep `mcp` |
| 2 | PrefectHQ/fastmcp | có | Apache-2.0 (OSI) | 28.1k | 2026-10-14 | không | Đúng | dep `fastmcp` |
| 3 | modelcontextprotocol/servers | có | **NOASSERTION**: đang chuyển MIT sang Apache-2.0, docs CC-BY-4.0 (OSI) | 91.3k | 2026-10-13 | không | **Sai một phần**: Postgres, SQLite không còn ở repo này. Chúng đã chuyển sang `servers-archived` | ref |
| 4 | runekaagaard/mcp-alchemy | có | MPL-2.0 (OSI) | 420 | 2026-10-09 | không | Phóng đại: đọc schema qua SQLAlchemy là đúng, nhưng repo không "tự sinh truy vấn an toàn" (LLM tự viết SQL và repo chạy SQL đó) | dep/ref |
| 5 | axonops/hubspot-mcp | có | **Không có file LICENSE**: GitHub trả `None`, README chỉ ghi "MIT" | **0** | 2026-06-25 | không | FastMCP Python, CRUD theo object-type: đúng (76 tool) | ref (sao 0, license mập mờ) |
| 6 | langchain-ai/langchain-mcp-adapters | có | MIT (OSI) | 3.7k | 2026-09-16 | **CÓ (archived)** | **Lỗi thời**: README ghi "no longer actively maintained", đã chuyển vào `langchain[mcp]` | ref; nếu cần thì dùng `langchain[mcp]` |
| 7 | sartography/SpiffWorkflow | có | LGPL-3.0 (OSI) | 1.9k | 2026-10-12 | không | Đúng (BPMN 2.0 thuần Python, user task) | dep (LGPL: dùng như thư viện, không sửa) |
| 8 | camunda-community-hub/pyzeebe | có | MIT (OSI) | 101 | 2026-10-14 | không | Đúng, nhưng **Camunda 8/Zeebe ≥8.6 không còn OSI** (Camunda License), nên client này vô dụng với xT | loại |
| 9 | cristofp/bpmn-python | có | GPL-3.0 (OSI) | **3** | **2017-11-13** | không | Repo chết 9 năm, chỉ là fork nhỏ. Bản gốc là `KrzyHonk/bpmn-python`. GPL không hợp lõi Apache-2.0 | loại |
| 10 | temporalio/sdk-python | có | MIT (OSI) | 1.2k | 2026-10-14 | không | Đúng (signal/wait cho phê duyệt, saga) | dep + docker `temporalio/server` (MIT) |
| 11 | PrefectHQ/prefect | có (đổi hoa/thường) | Apache-2.0 (OSI) | 24.0k | 2026-10-13 | không | **Sai một phần**: `pause_flow_run` có thật, nhưng multi-tenant (workspace) chỉ có ở Prefect Cloud. Prefect là orchestrator data pipeline, không phải engine phê duyệt nghiệp vụ | ref |
| 12 | pytransitions/transitions | có | MIT (OSI) | 6.6k | 2025-09-11 | không | Đúng (FSM nhẹ) | dep |
| 13 | viewflow/viewflow | có | AGPL-3.0 (OSI) | 2.9k | 2026-09-18 | không | Đúng, nhưng gắn chặt Django và có bản PRO thương mại. AGPL không hợp lõi Apache-2.0 | ref |
| 14 | frappe/frappe | có | MIT (OSI) | 10.9k | 2026-10-14 | không | Đúng về cơ bản, xem mục 3 | nền tảng / docker |
| 15 | baserow/baserow | có | **NOASSERTION**: lõi OSE là MIT, còn `premium/` và `enterprise/` dùng license riêng (không OSI) | 6.2k | 2026-10-14 | không | **Sai một phần**: row history có trong OSE, nhưng **RBAC (`baserow_enterprise/role`) và audit log (`baserow_enterprise/audit_log`) là Enterprise, không OSI** | docker (chỉ bản OSE) |
| 16 | nocodb/nocodb | có | **"Sustainable Use License"** (cập nhật 29/01/2026), **KHÔNG OSI**. Agyxt ghi AGPL-3.0 là **sai** | 65.3k | 2026-10-14 | không | **Vi phạm tiêu chí OSI**, phải loại | loại |
| 17 | gristlabs/grist-core | có | Apache-2.0 (OSI) | 12.0k | 2026-10-14 | không | Đúng: Access Rules theo hàng/cột/bảng, công thức Python. Lưu ý lõi là TypeScript/Node, dữ liệu lưu SQLite theo từng tài liệu | docker |
| 18 | PostgREST/postgrest | có | MIT (OSI) | 27.7k | 2026-10-14 | không | Đúng (REST tự sinh, dùng RLS) | docker |
| 19 | supabase/supabase | có | Apache-2.0 (OSI) | 111.6k | 2026-10-15 | không | Đúng, nhưng stack rất nặng và trùng vai với Keycloak/APISIX | ref |
| 20 | jazzband/django-auditlog | có | MIT (OSI) | 1.4k | 2026-10-12 | không | Đúng, nhưng chỉ dùng được với Django | ref |
| 21 | kvesteri/sqlalchemy-continuum | **đã đổi tên** thành `sqlalchemy-continuum/sqlalchemy-continuum` | BSD-3-Clause (OSI) | 644 | 2026-10-14 | không | Đúng | dep `SQLAlchemy-Continuum` |
| 22 | django-tenants/django-tenants | có | MIT (OSI) | 1.9k | 2026-10-12 | không | Đúng (schema-per-tenant), chỉ dùng với Django | ref |
| 23 | crystaldba/postgres-mcp | có | MIT (OSI) | 3.4k | 2026-08-17 | không | Đúng (EXPLAIN, hypothetical index, `--access-mode` restricted) | ref / docker |
| 24 | gldc/mcp-postgres | có | MIT (OSI) | **15** | 2026-02-18 | không | Đúng: README có Google OAuth 2.0 và mỗi user một kết nối DB. Repo rất nhỏ | ref |
| 25 | aadarshjain/postgres-mcp-server | có | MIT (OSI) | **2** | 2025-06-30 | không | Đúng (SQLAlchemy + pg8000), nhưng là đồ chơi | ref (không đáng) |
| 26 | industream/mcp-odoo | có | **Không có file LICENSE**: GitHub `None`, README ghi "MIT" | 3 | 2025-12-23 | không | Phóng đại: có timesheet, expense, contact, invoice (chỉ đọc), sale (chỉ đọc), employee. **Không có CRM leads.** README bằng tiếng Pháp | ref |
| 27 | parth-unjiya/odoo-mcp-gateway | có | MIT (OSI) | 4 | 2026-05-21 | không | Đúng: Odoo 17/18/19, cấu hình YAML (RBAC, field-level, audit), blocklist | ref (pattern policy YAML rất đáng học) |
| 28 | buildswithpaul/Frappe_Assistant_Core | có | AGPL-3.0 (OSI) | 322 | 2026-10-14 | không | Đúng: 24 tool, OAuth 2.0 đăng nhập như user ERPNext, Assistant Audit Log, Skills/Prompt Templates. Từ bản 3.0 có thêm SaaS "FAC Chat" | plugin (Frappe app riêng), không trộn code |
| 29 | rakeshgangwar/erpnext-mcp-server | có | MIT (OSI) | 122 | 2026-04-25 | không | Đúng về tính năng. Lưu ý repo viết bằng **TypeScript**, không phải Python | ref |

## 2. Hallucination và lỗi

Cả 29 repo đều tồn tại, không có repo nào bị bịa hoàn toàn. Tuy vậy, câu "100% xác minh, giấy phép OSI" của agyxt là **sai**. Có **14 lỗi**:

**Sai về giấy phép (4)**
1. **nocodb**: agyxt ghi AGPL-3.0. Thực tế là Sustainable Use License, không OSI. Đây là lỗi nghiêm trọng nhất vì vi phạm quy tắc OSI của xT.
2. **baserow**: agyxt ghi MIT thuần. Thực tế chỉ lõi OSE là MIT. RBAC và audit log mà agyxt quảng cáo nằm ở phần Enterprise, không OSI.
3. **axonops/hubspot-mcp**: không có file LICENSE. "MIT" chỉ là một dòng trong README.
4. **industream/mcp-odoo**: giống hubspot-mcp, không có file LICENSE.

**Sai về trạng thái hoặc tên repo (2)**
5. **langchain-mcp-adapters**: đã archived và ngừng bảo trì. Agyxt không nhắc.
6. **kvesteri/sqlalchemy-continuum**: đã đổi sang org `sqlalchemy-continuum`.

**Mô tả sai hoặc phóng đại (5)**
7. **modelcontextprotocol/servers**: Postgres và SQLite không còn ở repo này (đã sang servers-archived). License cũng không còn MIT thuần.
8. **prefect**: "multi-tenant" chỉ có ở Prefect Cloud.
9. **mcp-alchemy**: "sinh truy vấn an toàn" là phóng đại.
10. **industream/mcp-odoo**: không có "CRM leads". Agyxt ghi "HR" cũng là thổi phồng, vì repo chỉ có list/get employee.
11. **rakeshgangwar/erpnext-mcp-server**: được đưa vào bảng "MCP server Python" nhưng repo viết bằng TypeScript.

**Không dùng được (3)**
12. **cristofp/bpmn-python**: fork chết từ 2017, chỉ 3 sao, GPL.
13. **pyzeebe**: client đúng, nhưng engine Zeebe/Camunda 8 hiện nay không còn OSI.
14. **aadarshjain/postgres-mcp-server**: chỉ 2 sao, không có giá trị.

Ghi chú của agyxt về Directus (đã đổi sang license không OSI) là **đúng**.

## 3. Đánh giá sâu: Frappe Framework có nên làm NỀN cho xT?

### 3.1 Bằng chứng (nhánh `develop` và release v16.52.0, ngày 2026-10-14)

| Tiêu chí | Kết quả | Bằng chứng |
|---|---|---|
| License | **MIT** (OSI, hợp với Apache-2.0) | API trả `MIT`; README có badge MIT |
| DocType động tạo lúc runtime | **Có**. `POST /api/resource/DocType` với `custom=1` tạo bảng thật mà không cần developer_mode. DocType chuẩn thì bắt buộc bật developer_mode | `frappe/core/doctype/doctype/doctype.py` `check_developer_mode()`: *"Set in site_config.json or make 'Custom' DocType"*. Có thêm Custom Field và Property Setter |
| Workflow (state, transition, phê duyệt) | **Có sẵn**. Transition có `allowed` (role), `condition` (biểu thức), `allow_self_approval`, `send_email_to_creator`; có Workflow Builder | `frappe/workflow/doctype/workflow_transition/*.json`, `frappe/model/workflow.py` (`apply_workflow`, `has_approval_access`) |
| Phân quyền | Role × DocType × permlevel (đến cấp field), **User Permission** (giới hạn đến cấp row theo giá trị link), "if owner", Sharing | `frappe/core/doctype/user_permission/` |
| Multi-tenant | **Multi-site**: mỗi site có DB riêng trên cùng một bench, cách ly ở cấp database | kiến trúc bench/sites |
| Version / audit | DocType `Version` lưu diff mỗi lần lưu (track_changes), cùng Activity Log và Access Log | `frappe/core/doctype/version/` |
| REST API | `/api/resource/*` (v1) và `/api/v2` | `frappe/api/v2.py` |
| SSO | Social Login Key (OAuth2/OIDC), nối được Keycloak. Frappe còn tự làm OAuth2 provider | `frappe/integrations/doctype/social_login_key/` |
| Postgres | **Hỗ trợ ở framework, nhưng là backend hạng hai**. Có `frappe/database/postgres`, CI có job postgres:18 nhưng **chỉ chạy có điều kiện** (`run_postgres`). Mặc định và tài liệu đều là MariaDB 11.8. Hệ sinh thái app (ERPNext...) gần như chỉ test trên MariaDB. Bản develop còn có backend sqlite/duckdb | `.github/workflows/server-tests.yml`, `frappe/database/` |
| Python | **Bắt buộc `>=3.14,<3.15`** (cả v16 lẫn develop) | `pyproject.toml` |
| Docker | **Nặng**: `frappe_docker/pwd.yml` có khoảng 10 service (backend, frontend/nginx, websocket/socketio Node, queue-short, queue-long, scheduler, redis-queue, redis-cache, db MariaDB, configurator/create-site). Phải build custom image cho từng app | `frappe/frappe_docker` |
| Độ sống | 10.9k sao, release gần như hằng ngày, mỗi tuần có minor của cả v15 và v16 | API releases |

### 3.2 Frappe_Assistant_Core (FAC)
- **AGPL-3.0** (OSI), 322 sao, push 2026-10-14, có CI, có bản trên PyPI và Frappe Cloud Marketplace.
- Chất lượng ổn: 24 tool (CRUD, search, report, workflow, analytics), OAuth 2.0 đăng nhập như user thật nên tôn trọng ACL của Frappe, có audit log, có hook để app khác bổ sung tool, có khái niệm "Skills" (khớp triết lý "viết skill thay vì hard-code" của xT).
- Rủi ro:
  - AGPL nên **không được copy hay trộn code vào lõi Apache-2.0**. Chỉ cài thành Frappe app riêng (plugin).
  - Từ bản 3.0 có kèm SaaS "FAC Chat" (đang tắt mặc định), nên hướng đi thương mại hoá rõ.
  - Hợp nhất làm **tham chiếu thiết kế tool** (danh sách tool, cách ánh xạ quyền), hoặc làm add-on tùy chọn.

### 3.3 So sánh nhanh các ứng viên làm nền

| | Frappe | Baserow (OSE) | Grist-core |
|---|---|---|---|
| Ngôn ngữ lõi | Python | Python (Django) + Nuxt | **TypeScript/Node**, Python chỉ cho công thức |
| Schema động qua API | Có (DocType custom) | Có (table/field API) | Có (doc/table/column API) |
| Workflow và phê duyệt | **Có sẵn** | Không (chỉ automation/webhook) | Không |
| Phân quyền chi tiết | Role/DocType/field/row | **RBAC nằm ở Enterprise (không OSI)** | Access Rules hàng/cột (OSI) |
| Audit | Version diff | Row history (OSE), audit log ở Enterprise | Lịch sử action theo tài liệu |
| Multi-tenant | Site, DB riêng | Workspace | Org/Team site |
| Postgres + RLS | MariaDB là chính, không dùng RLS | Postgres (không dùng RLS) | SQLite theo tài liệu, Postgres chỉ cho home DB |
| Độ khớp với xT | Cao nhất về nghiệp vụ | Trung bình | Thấp (không có workflow, lệch ngôn ngữ) |

### 3.4 Kết luận
**Frappe là nền mạnh nhất về chức năng**: DocType runtime, Workflow phê duyệt, permission đến cấp row, multi-site, audit, REST, MIT. Gần như toàn bộ phần "nghiệp vụ là dữ liệu, model và process do agent đề xuất rồi người duyệt" đã có sẵn. Baserow và Grist đều thiếu workflow. Riêng Baserow còn khóa RBAC và audit sau Enterprise.

**Nhưng không nên dùng Frappe làm LÕI Tầng 1 thay cho kiến trúc đã chốt.** Nên chạy Frappe thành **một service "Data + Workflow engine" bằng docker**, sau Keycloak và APISIX. MCP server xT (Python, Apache-2.0) gọi Frappe qua REST, và xT không sửa code Frappe. Nghiệp vụ được đóng gói thành một **Frappe app riêng của xT** (MIT/Apache, do mình viết) cài qua cơ chế app/hook. Cách này đúng quy tắc OLP "không sửa gói đính kèm".

Rủi ro:
1. **Lệch kiến trúc đã chốt (Postgres + RLS)**: Frappe chạy tốt nhất trên MariaDB và không dùng RLS. Chạy Postgres thì là đường hạng hai, CI chỉ chạy có điều kiện. Phải chấp nhận MariaDB, hoặc tự test kỹ trên Postgres.
2. **Nặng**: khoảng 10 container, cộng Keycloak, APISIX, MinIO, nên rất khó demo gọn và làm release `.tar.gz`. Python 3.14 cũng bắt buộc.
3. **Hai hệ phân quyền và auth**: phải đồng bộ user/role từ Keycloak sang Frappe (Social Login Key và mapping role). Nếu không thì sẽ có hai nguồn sự thật.
4. **Giám khảo có thể coi đây là "ERPNext lai"**: giá trị sáng tạo của xT phải nằm ở lớp MCP, quy trình agent đề xuất rồi người duyệt, và MCP Apps.
5. **Bề mặt API rộng và nhanh thay đổi** (v15/v16 release hằng tuần), nên phải pin version.

Phương án thay thế nhẹ hơn, khớp kiến trúc đã chốt: lõi tự viết trên Postgres+RLS (schema động bằng JSONB hoặc DDL qua SQLAlchemy), audit bằng `SQLAlchemy-Continuum`, FSM bằng `transitions` hoặc BPMN bằng `SpiffWorkflow`, chỉ dùng Frappe làm **tham chiếu thiết kế** (DocType, Workflow Transition, User Permission). Chọn cách nào tuỳ đội: muốn nhanh có đủ nghiệp vụ thì dùng Frappe (docker), muốn bám đề, gọn và kiểm soát được thì tự viết lõi theo mô hình Frappe.

## 4. Top repo thực sự hữu ích cho xT
1. modelcontextprotocol/python-sdk (dep)
2. PrefectHQ/fastmcp (dep)
3. frappe/frappe (docker service hoặc tham chiếu mô hình DocType/Workflow/Permission)
4. sartography/SpiffWorkflow (dep, BPMN + user task)
5. sqlalchemy-continuum/sqlalchemy-continuum (dep, audit/version)
6. PostgREST/postgrest (docker, REST trên RLS)
7. crystaldba/postgres-mcp (ref: safe SQL, access-mode)
8. parth-unjiya/odoo-mcp-gateway (ref: policy YAML, blocklist, RBAC cho MCP). Có thể thay bằng buildswithpaul/Frappe_Assistant_Core (ref/plugin) nếu chọn Frappe.

Phụ trợ: temporalio/sdk-python, pytransitions/transitions, gristlabs/grist-core (ref Access Rules).
**Loại**: nocodb, pyzeebe, cristofp/bpmn-python, aadarshjain/postgres-mcp-server, langchain-mcp-adapters (archived).
