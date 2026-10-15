<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Nhật ký quyết định

Các quyết định thay chủ dự án do agent Opus 5.5 "decision" đưa ra trong chế độ tự vận hành. Mỗi mục ghi: ngày, câu hỏi, các phương án, lựa chọn, lý do.

| Ngày | Câu hỏi | Lựa chọn | Lý do |
|---|---|---|---|
| 16/10/2026 | Desk production gọi `/mcp` cùng origin (`xt.freepro.online/mcp`) nhưng APISIX đẩy host Desk sang nginx tĩnh (404). Sửa ở đâu? (a) đổi Desk gọi thẳng `https://xt-mcp.freepro.online/mcp` (cần CORS), (b) thêm route APISIX `/mcp` trên host Desk tới `xt-api:8000`, (c) nginx proxy trong ảnh Desk | **(b)**: thêm route `desk-mcp` (hosts Desk, uris `/mcp`, priority > desk-static) tới `xt-api:8000`, KHÔNG chèn bearer | Cùng origin nên không cần CORS và giữ nguyên token trong bộ nhớ; không đổi code Desk; MCP vẫn tự xác thực (gateway không xác thực, D12); `XT_MCP_ALLOWED_HOSTS` cần thêm host Desk. Đã áp dụng 16/10/2026 (`deploy/apisix/apisix.yaml.tmpl`, route `desk-mcp`, kèm `proxy-buffering` tắt, `limit-req`, timeout 3600 s; compose thêm host+origin Desk vào `XT_MCP_ALLOWED_HOSTS/ORIGINS`). Không thêm `/.well-known/*` trên host Desk: Desk dùng issuer/client cấu hình lúc build, PRM chỉ phục vụ connector ở host MCP. |
| 16/10/2026 | Chạy E2E https trên APISIX http cục bộ: nới `KC_HOSTNAME`/cho http, hay giả lập? | Giả lập bằng Playwright route (https giữ nguyên) | Không đổi cấu hình production; kiểm đúng issuer/redirect thật. |
| 16/10/2026 | PostgREST GET chạy trong transaction `READ ONLY`; pre-request có ghi ngữ cảnh vào `xt_meta.session_ctx` qua `xt.set_context` được không? | **Không.** Pre-request ghi ngữ cảnh vào GUC cục bộ transaction `xt.pgrst_ctx` qua overload mới `xt.set_context(jsonb)`; `xt.cur()` thành wrapper, logic audit được giữ nguyên trong `xt.backend_cur()` (PG-1, PG-6). *Bản đầu (`CREATE OR REPLACE xt.cur`, migration 0004) đã bị thay, xem dòng follow-up bên dưới.* | Đã chạy thật trên PG 16 cục bộ (`initdb` tạm, đã xóa): `INSERT` vào bảng unlogged qua hàm SECURITY DEFINER trong `BEGIN READ ONLY` → `cannot execute INSERT in a read-only transaction`; `CREATE TEMP TABLE` cũng bị chặn; chỉ `set_config(…, true)` chạy được. Các session khác giữ nguyên hành vi đã audit. |
| 16/10/2026 | Role PostgREST: tạo ở đâu, quyền gì? | `xt_authenticator` LOGIN NOINHERIT, chỉ là thành viên `xt_web` (`SET TRUE, INHERIT FALSE`); mật khẩu từ secret ngoài repo; tạo bằng bước superuser riêng (PG-2) | `xt_migrator` không có CREATEROLE. Không cấp `xt_app`/`xt_workflow`/`xt_api_owner` cho `xt_authenticator`/`xt_web` (SMT §5.2). Claim role trỏ tới role khác → SET ROLE thất bại. |
| 16/10/2026 | `db-schemas` động: `ALTER ROLE … SET pgrst.db_schemas` (spike) hay `db-pre-config`? | `db-pre-config = xt.pgrst_config` (SECURITY DEFINER, suy ra từ `xt_meta.tenant` + schema thật), schema đầu là placeholder rỗng `xt_api_none` (PG-3) | Không giữ trạng thái thừa, migrator không cần quyền ALTER ROLE; registry rỗng vẫn khởi động được; thiếu `Accept-Profile` rơi vào schema rỗng và bị pre-request từ chối. |
| 16/10/2026 | Hook sau khi tạo/xóa tenant, tạo/xóa model | `pg_notify('pgrst','reload config')` + `'reload schema'` **trong cùng transaction** của `provision_tenant`/`drop_tenant`; model đã có `reload schema` trong `PgMigrator.apply` (PG-4) | NOTIFY chỉ phát khi commit → rollback không gây tác dụng phụ; không có listener thì là no-op ⇒ không đổi hành vi đã audit. |
| 16/10/2026 | DB đã bootstrap áp phần PostgREST thế nào? | **Không có migration 0004.** `sql/postgrest.sql` cộng thêm, idempotent; `bootstrap()` nạp kèm (`tenancy.bootstrap_sql()`); DB cũ và cả DB mới đều phải gọi `tenancy.install_postgrest_role(superuser, *, authenticator_password, issuer, clients)` (PG-5) | Migration chạy bằng `xt_migrator` không `CREATE ROLE` được; bắt `migrate` RAISE khi thiếu role làm hỏng fixture bootstrap → migrate hiện có. `install_postgrest_role` chạy lại `postgrest.sql` nên DB bootstrap trước đó vẫn nhận. |
| 16/10/2026 (follow-up) | Nối ngữ cảnh READ ONLY mà giữ logic RBAC đã audit thế nào? | Clone thân `xt.cur()` gốc sang hàm private `xt.backend_cur()`, thay `xt.cur()` bằng wrapper giữ nguyên OID/ACL; thêm overload private `xt.set_context(jsonb)` (PG-6) | Không rename nên dependency và quyền BI hiện có vẫn gắn đúng OID. Session ngoài PostgREST chỉ gọi lại logic gốc. Đã kiểm trên PG17 thật: source/OID/ACL, reinstall và BI có từ trước. |

## Chi tiết PostgREST runtime (đã triển khai 16/10/2026)

### PG-1. Ngữ cảnh READ ONLY

PostgREST chạy GET/HEAD trong transaction READ ONLY; hàm `xt.set_context` 8 tham số ghi bảng nên không dùng trực tiếp được. `xt.pgrst_pre()` là SECURITY DEFINER, thân chỉ dùng tên đủ schema. Nó đọc profile từ GUC `search_path` do PostgREST đặt (không dùng `current_schema()` vì quyền USAGE đổi khi vào SECURITY DEFINER), đọc GUC `role`, rồi gọi gate private `xt.pgrst_gate(text,text)` có `search_path = pg_catalog, pg_temp`.

Gate kiểm session authenticator/role xt_web, token Bearer, sub, aud xt-data, exp, issuer đã cấu hình, allowlist azp, đúng một organization alias, tenant đã đăng ký, profile và method. Actor/roles/phòng ban lấy từ person active của xT; không lấy role nghiệp vụ từ JWT. Gate gọi overload private `xt.set_context(jsonb)` để đặt GUC local transaction. Không expose RPC.

### PG-2. Role và danh tính

`xt_authenticator` LOGIN NOINHERIT, NOBYPASSRLS, chỉ được SET ROLE xt_web; không thuộc xt_app/xt_workflow/xt_api_owner. `xt_web` chỉ SELECT view, không được EXECUTE setter (cả hai chữ ký), backend_cur hay gate. Chỉ pre-request được gọi bởi xt_web; pre-config được gọi bởi authenticator. Mọi hàm mới thu quyền PUBLIC.

Cài lại `postgrest.sql` chuẩn hóa thuộc tính của hai role, thu membership theo từng grantor (kể cả ADMIN OPTION và grant phụ thuộc), rồi chỉ cấp `xt_web` cho authenticator với `ADMIN FALSE, INHERIT FALSE, SET TRUE`. Test PostgreSQL 17 thật đã kiểm cấu hình sai từ trước với nhiều grantor; không chỉ kiểm role tạo mới.

Keycloak scope optional `xt-data`: aud xt-data, claim hằng `data_role=xt_web`, organization. Không gán realm-default hoặc DCR; `XT_DATA_CLIENTS` phải khớp allowlist DB. PostgREST dùng `.data_role`, không có anon-role. JWKS chỉ gồm khóa công khai của realm, nạp qua file; xoay khóa phải cập nhật file và reload.

Ranh giới tin cậy: người có mật khẩu authenticator và kết nối SQL trực tiếp có thể giả ngữ cảnh. Giới hạn pg_hba/mạng vào PostgREST, giữ secret ngoài repo; không đưa credentials superuser/runtime khác vào service này. GUC local không phải chữ ký xác thực và không dùng để chống người đã chiếm credentials authenticator.

### PG-3. Schema động

`xt.pgrst_config()` suy danh sách từ tenant đã đăng ký và schema api_t<seq> thật, thuộc xt_api_owner. Schema rỗng xt_api_none luôn đứng đầu. Env PGRST_DB_SCHEMAS chỉ là nền; pre-config và reload đã được kiểm qua PostgREST thật khi thêm/xóa tenant/model lúc server đang chạy.

### PG-4. Reload theo commit

`xt.adapters.db.postgrest.notify_postgrest(conn)` gửi reload config rồi reload schema trong transaction DDL. Gọi từ provision/drop tenant, PgMigrator.apply và install_postgrest_role. Rollback không phát thông báo; test LISTEN/NOTIFY thật đã kiểm. View được compiler tạo/xóa ở pha tx của migration.

### PG-5. Cài mới và nâng cấp

`postgrest.sql` là phần cộng thêm, idempotent, chạy bằng superuser; bootstrap_sql nạp cùng bootstrap gốc. DB đã bootstrap phải gọi `install_postgrest_role(superuser, authenticator_password, issuer, clients)`; không có migration 0004. Dùng `make -C deploy pgrst-db` với XT_DATA_SUPERUSER_URL tường minh, cùng issuer/client như Keycloak. API cài schema nền cho các migration audit cũ: PG-7.

Không áp thay đổi vào stack của session khác. Cài lại giữ OID/ACL của cur và login BI đã có; mật khẩu lấy từ secret, không vào argv/log. Thiếu issuer/client hoặc person chưa đăng ký thì fail-closed.

### PG-6. Giữ nguyên logic RBAC đã audit

Không rename cur: clone định nghĩa gốc bằng pg_get_functiondef sang backend_cur (giữ nguyên prosrc/owner/STABLE/search_path), thu quyền PUBLIC. CREATE OR REPLACE cur chỉ thêm wrapper và giữ OID/ACL cũ. Mọi session ngoài xt_authenticator đi vào backend_cur, không đọc GUC data. Session authenticator chỉ dùng ctx local do gate kiểm.

Cài lại dưới advisory lock: cur còn là core (kể cả core vừa nâng cấp) thì clone core hiện tại; đã là wrapper thì giữ backend; wrapper mất backend thì RAISE thay vì clone đệ quy. Hàm 8 tham số không đổi; overload jsonb là API mới private. Không cấp xid cho GET chỉ để giữ ngữ cảnh: transaction-local GUC tự hết hạn. Không thêm subtransaction vào nhánh backend; bắt lỗi JSON chỉ nằm trong nhánh data.

Đã kiểm PostgreSQL17 + PostgREST16.4: 28 integration test, bao gồm nâng DB cũ/BI có từ trước, source/OID/ACL, reinstall, thiếu backend, GUC spoof, READ ONLY GET/HEAD, pool một connection, metadata model đổi động. Nguồn mẫu: REA (chặn theo phạm vi/qualified boundary), sklite (phân biệt danh tính upstream/tenant; chỉ học pattern từ zip, không chép code vì không có LICENSE).

## Vai trò `xt_migrator` với audit (16/10/2026, chủ dự án – Opus 5.5)

- **Câu hỏi:** `xt_migrator` có được coi là tin cậy với audit không? (audit Model Engine vòng 2, R2-01/R2-04)
- **Quyết định:** **Tin cậy để đổi schema, KHÔNG tin cậy với audit.** (1) Tách role chủ sở hữu audit `xt_audit_owner` (sở hữu bảng/hàm/trigger audit); `xt_migrator` không sửa, tắt hay gọi được hàm ghi audit. (2) Khóa HMAC chỉ đưa vào transaction có ghi field sensitive, không đưa vào mọi `tenant_tx`. (3) Định kỳ neo đầu chuỗi audit ra ngoài DB bằng checkpoint ký Ed25519, lưu ngoài Postgres, để DBA hay role đặc quyền sửa audit đều bị phát hiện.
- **Lý do:** Nguyên tắc phân tách nhiệm vụ: người đổi cấu trúc không được đồng thời kiểm soát bằng chứng kiểm toán. Neo hash ra ngoài đúng thiết kế đã nêu trong kiến trúc v2 và nguyên lý (iii) của đề.
| 16/10/2026 | libpq cho psycopg trong ảnh xt: system libpq hay `psycopg[binary]`? | **`libpq5` của Debian trong ảnh slim** (`apt --no-install-recommends`), giữ psycopg thuần Python | Khớp `THIRD_PARTY_NOTICES.md` (không dùng wheel `psycopg-binary`, không vendor binary LGPL); libpq được vá bảo mật qua base image. Ảnh: uid 10001, không home/shell, `PYTHONDONTWRITEBYTECODE`, compose `read_only` + tmpfs `/tmp` + `cap_drop ALL`. |
| 16/10/2026 | Access token MCP mang aud `account`; có nên bỏ `xt-api-introspect`? | Giữ `xt-api-introspect` (bắt buộc: Keycloak 26 trả `active:false` khi client introspect không nằm trong aud, `identity.md`); bỏ `account` bằng cách gỡ mapper `audience resolve` khỏi client scope `roles` (toàn realm) | aud còn đúng `[resource MCP, xt-api-introspect]`. Không client nào của xT dựa vào aud `account`/`realm-management` (Admin REST kiểm role, không kiểm aud; smoke registry vẫn PASS). Không cần bộ scope riêng từng client. |
| 16/10/2026 | `XT_MCP_HUMAN_CLIENTS` trong compose | xt-api nhận `XT_CONFIRM_CLIENT_ID`; biến cùng tên trong `.env` (xt-desk) chỉ dành cho provision | Composition từ chối human client khác client xác nhận (M6). |
| 16/10/2026 | Ai khởi tạo DB (role, schema, hàng đợi) cho xt-api/worker? | Service một lần `xt-init` (`deploy/scripts/init_db.py`, idempotent); xt-api/worker `depends_on: service_completed_successfully`; khóa tệp sinh bởi `make env` ở `~/.config/xt/app` (0644/0755 vì container uid 10001; thư mục cha 0700) | Không cần bước tay; thiếu biến thì `compose` báo `${VAR:?}` hoặc tiến trình thoát với danh sách lỗi; `restart: on-failure:5` chặn crash-loop vô hạn. |
| 16/10/2026 | D-INT-1. Composition root (`xt.entrypoints.composition`) phải dựng `SpiffProcessEngine`; contract import-linter "chỉ adapters.workflow import SpiffWorkflow/lxml" báo vỡ vì import gián tiếp | Contract giữ nguyên danh sách nguồn/cấm, thêm `allow_indirect_imports = true` (như contract psycopg) | Mục đích của contract là không module nào ngoài `adapters.workflow` gọi thẳng SpiffWorkflow/lxml; entrypoints chỉ đi qua API công khai `xt.adapters.workflow.bpmn.spiff`. Import trực tiếp vẫn bị chặn; tầng domain/application vẫn bị contract tầng chặn. |
| 16/10/2026 | D-INT-2. Gói chuẩn để ở `packs/` hay `packages/`? | Giữ `packages/<gói>/` (architecture-v2 §4 cây thư mục: `packages/ # gói nghiệp vụ = dữ liệu`); `research/packs-draft/` chỉ còn test/báo cáo nghiên cứu | Không di chuyển: kiến trúc đã chốt `packages/`, mọi test/tài liệu trỏ vào đó. |
| 16/10/2026 | D-INT-3. Nạp tổ chức demo từ `seed/binh-minh/org.json` (định dạng HR, không có `sub` Keycloak; vai trò chỉ ghi vai trò chính, trưởng phòng không có `employee`) | `xt org import` tự nhận định dạng HR; liên kết đăng nhập + quyền agent ở tệp `--links`; vai trò nền cộng thêm phải khai tường minh `--base-roles employee`; trưởng đơn vị = nhân sự đang làm có `dept_head` trong đơn vị (nhiều hơn một → lỗi) | Không suy vai trò ngầm, không bịa `sub`. Tệp HR giữ nguyên làm nguồn sự thật. |
| 16/10/2026 | D-INT-4. Đọc bản ghi của tenant khác: PEP trả `access_denied` (policy không thấy thuộc tính) trước khi handler trả `not_found` | Chấp nhận: lỗi giống hệt id không tồn tại trong tenant của người gọi (test e2e so khớp), nên không lộ tồn tại; RLS chặn dữ liệu ở DB | Đổi thứ tự PEP/handler là sửa module đã audit (dispatcher); không cần vì không có oracle. |
| 16/10/2026 | D-INT-5. Ai tạo job role `xt_job_t<seq>` + `xt_queue.tenant_bindings` và đăng ký relay hẹn giờ cho tenant? (xt_migrator không có CREATEROLE) | Lệnh vận hành `xt tenant bind-queue --tenant <alias>` chạy bằng `XT_DB_OWNER_URL` (role có CREATEROLE) + biến của `xt worker`; idempotent; đăng ký job định kỳ `workflow.relay_timers` mỗi phút | Tách đặc quyền: lệnh tenant thường không cần owner; worker không tự đăng ký (handler factory đồng bộ, không được ghi lúc nạp). |

## Tier PII của Desk chat (16/10/2026, chủ dự án – Opus 5.5)

- **Câu hỏi:** audit MCP vòng 2, R2-M4. `xt-desk-agent` là client public, có redirect loopback, nên ai cũng xin được tier `local` (PII đầy đủ cho LLM). Server không kiểm được LLM phía sau có thật sự chạy local hay không.
- **Quyết định:**
  1. Client public (kể cả `xt-desk-agent`) tối đa ở tier masked: LLM chỉ nhận token PII.
  2. Người dùng xem giá trị thật qua kênh `/pii/reveal` (vé dùng một lần, gắn khung App, theo `research/spike-pii-lane.md`). Kênh này hiển thị cho người, không đi vào ngữ cảnh LLM.
  3. Tier `local` chỉ cấp cho client **confidential** phía server, tức Desk BFF trong tương lai, nơi giữ token và gọi LLM nội bộ.
  4. Realm production bỏ redirect loopback; loopback chỉ có trong profile dev.
  5. Chính sách tenant `local_only` luôn được tôn trọng.
- **Lý do:** đặt quyền theo thứ server kiểm chứng được (client confidential), không theo thứ client tự khai. Cảnh demo "lương thật chỉ hiện trong Desk" vẫn giữ nguyên, nhưng giá trị thật đi qua kênh dành cho người thay vì qua LLM.

### PG-7 — cài đặt quản trị schema nền (16/10/2026)

Sau phản biện bằng Codex gpt-6-astra: dùng API cộng thêm `install_data_prerequisites(superuser)` cho giai đoạn cài `/data`. Quyền EXECUTE trigger chỉ tồn tại trong transaction quản trị (cấp + thu hồi trước commit); không sửa thân migration/audit, không mở quyền runtime. Lý do chọn: các migration cũ cần quyền để tạo trigger trong khi bootstrap audit mới đã thu quyền đó. Giữ nguyên ACL/ownership ở trạng thái đã commit và kiểm lại trong integration. Đây không phải cách sửa lỗi tên/hành vi trigger metadata của các module khác; không tuyên bố workflow e2e xanh từ test data. Chi tiết và giới hạn: `docs/integration-notes.md`, mục API cài schema nền cho data.

**Thay thế (16/10/2026):** việc cấp tạm EXECUTE `xt_audit.log()` bị bỏ vì mở lại R2-04 (xt_migrator không tin cậy với audit). `install_data_prerequisites` nay chạy `install_meta_audit()` bằng superuser rồi migrate bằng xt_migrator, không cấp quyền audit nào.

## Đề thi OLP2026 không phát hành (16/10/2026, chủ dự án – Opus 5.5)

- **Câu hỏi:** `research/de_thi_OLP2026.pdf` và `.txt` có vào tarball phát hành và repo công khai không? (PoF I.5: không phát hành kèm tài liệu của bên khác; giấy phép của đề không rõ.)
- **Quyết định:** **Loại cả hai tệp** khỏi `xt-<version>.tar.gz` và khỏi repo công khai. Giữ bản local để đối chiếu. Phần còn lại của `research/` (tài liệu do đội viết) vẫn vào tarball.
- **Cách thực hiện:** `scripts/release.sh` loại hai tệp bằng pathspec `:(exclude)` của `git archive`; `.gitattributes` có `export-ignore` (cho cả tarball tự sinh của GitHub). Checklist `research/olp-checklist.md` trích dẫn đề bằng số mục, không chép nguyên văn.
- **Còn lại trước khi push công khai:** hai tệp hiện đã nằm trong lịch sử git cục bộ. Trước lần push đầu tiên phải gỡ khỏi chỉ mục (`git rm --cached`) và thêm vào `.gitignore`, hoặc viết lại lịch sử để chúng không có trong repo công khai. Chủ dự án thực hiện khi commit; chưa làm ở đây vì chưa commit.
- **Lý do:** đề thuộc VFOSSA, không có giấy phép phân phối lại; phát hành sẽ vi phạm chính tiêu chí PoF về gói/tài liệu đính kèm.

| 16/10/2026 | D-INT-6. Đề xuất thay đổi model: ai duyệt, và với thay đổi PHÁ HỦY thì người thứ hai là ai? (`PendingOperation.confirmers` phải nêu tên trước) | Ai được `request.decide` trên `xrn:<t>:model_proposal:*` theo policy (gói `packages/quan-tri-mo-hinh`: `director`, `it`; người đề xuất bị loại). Người thứ nhất = người gọi `decide_request`. PHÁ HỦY: người thứ hai = quản lý trực tiếp nếu đủ quyền, nếu không thì người đủ quyền đầu tiên theo mã nhân sự, khác người thứ nhất và người đề xuất; không có ai -> `precondition_failed no_second_approver`. Áp dụng do worker (`model.apply`) sau khi duyệt, revalidate kế hoạch lúc plan và lúc xác nhận | Quyền theo dữ liệu (policy), không cài cứng vai trò; người thứ hai xác định được, hiện trên trang xác nhận; theo chuỗi báo cáo trước (bốn mắt theo cấp). |

## REST /workflow/v1 (16/10/2026, chủ dự án – Opus 5.5)

- **Câu hỏi 1 – audience:** API workflow dùng chung resource MCP (`aud` = URL `/mcp`) hay audience riêng `xt-workflow`?
- **Quyết định:** audience **riêng `xt-workflow`** + scope bắt buộc `xt-workflow` (client scope *optional* của Keycloak, như `xt-data`). `XT_WORKFLOW_AUDIENCE` bắt buộc khác `XT_MCP_RESOURCE` (kiểm lúc khởi động). `azp` cho phép = `XT_WORKFLOW_ALLOWED_CLIENTS` (mặc định = `XT_MCP_ALLOWED_CLIENTS` tĩnh); connector DCR không gọi được REST.
- **Lý do:** cùng mô hình với `/data` (aud `xt-data`); RFC 8707/9068: token bị lộ ở một bề mặt không phát lại được sang bề mặt kia (token MCP của connector chat không mở được REST, token curl không mở được `/mcp`). Đã kiểm trong test tích hợp ở cả hai chiều.
- **Câu hỏi 2 – HTTP status của `confirmation_required`:** 202 hay 4xx?
- **Quyết định:** **428 Precondition Required** + envelope lỗi chung (`ok:false`, `details.confirm_url`) + header `Link: <confirm_url>; rel="confirm"`. Gửi lại đúng request với cùng `Idempotency-Key` sau khi người xác nhận → 200 (receipt `replayed:true`).
- **Lý do:** hành động **chưa** chạy. 2xx dễ bị `curl -f`/SDK hiểu là đã duyệt, trái với quy tắc "không bao giờ tuyên bố đã duyệt khi người chưa bấm". 428 đúng nghĩa "thiếu điều kiện tiên quyết", thử lại được.
- **Câu hỏi 3 – `request_info`:** engine có outcome này nhưng `decide_request` v0.1 (ToolContract + composition `DECISIONS`) chưa có.
- **Quyết định:** REST nhận 5 tên outcome của engine; `return_for_revision` → `return`; `request_info` trả **400 `decision_not_supported`** cho tới khi contract `decide_request` thêm giá trị này (cần người được hỏi `ask`, sửa composition: việc của lớp tích hợp). Không đi đường tắt vào `ProcessService.act`.
- **Câu hỏi 4 – `task_id`:** id task Spiff (UUID nội bộ) hay ref đề nghị?
- **Quyết định:** `task_id` = `ref` của mục inbox (`<model>:<uuid>`); bước đang chờ của người gọi do server xác định, như `decide_request`. Không lộ id nội bộ của engine ra làm khóa thao tác.
- **Người không gọi REST:** token client người (`XT_MCP_HUMAN_CLIENTS`) bị 401 như ở MCP (M6); người chỉ quyết định trên `/desk/confirm/{id}` có step-up.

## Token headless xt-workflow / xt-data (16/10/2026, chủ dự án – Opus 5.5)

- **Câu hỏi:** client nào được xin scope `xt-workflow` / `xt-data`: client confidential `xt-automation` (client_credentials) hay public `xt-cli` (auth-code + PKCE)?
- **Quyết định:** **public client `xt-cli`, auth-code + PKCE S256, redirect loopback RFC 8252** (`http://127.0.0.1/callback`, `http://[::1]/callback`), không secret, không client_credentials/ROPC/implicit, `fullScopeAllowed=false`, không nhận `mcp-xt`. Hai scope là *optional*, tạo bởi `provision_data.py`, chỉ gán cho `XT_DATA_CLIENTS` / `XT_WORKFLOW_CLIENTS` (mặc định `xt-cli`); client khác đang giữ scope bị thu hồi; danh sách chứa client MCP/Desk/IdP (`xt-desk*`, introspect, registry) bị từ chối. Mapper audience phát đúng `xt-workflow` / `xt-data`; scope không bao giờ là realm default/optional; DCR không thấy được. `assert_mcp_clean` kiểm `mcp-xt` không có aud này.
- **Không làm `xt-automation` confidential:** REST workflow và `/data` quyết định theo *người* (`sub` liên kết `xt_meta.person`; service account bị `invalid_behalf`), nên client_credentials chỉ cho token vô dụng và mở thêm bề mặt secret. Tự động hóa server-to-server thật (BI) cần mô hình quyền cho service principal: để sau, không thêm lúc này.
- **Lý do:** demo OLP headless cần agent/curl đại diện một người thật; public + PKCE + loopback không giữ bí mật dài hạn, token ngắn hạn, bắt buộc người đăng nhập (OTP/step-up vẫn do Desk). Lấy token: `docs/headless-demo.sh --get-token data|workflow`.

## Harness e2e E0–E11 (16/10/2026, chủ dự án – Opus 5.5)

- **D-E2E-1. Lấy token người dùng test thế nào khi realm tắt ROPC?** Không bật password grant (kể cả dev). Harness lái đúng luồng trình duyệt auth-code + PKCE S256 bằng HTTP headless (`e2e/xte2e/oidc.py`): authorize → trang username (identity-first của Organizations) → mật khẩu → OTP (TOTP seed đã biết) → consent (client DCR) → đổi code. Sau đó dùng refresh token. Lý do: kiểm đúng cấu hình production, không nới realm cho test.
- **D-E2E-2. Stack e2e tách project.** `docker compose -p xt-e2e` (volume riêng, luôn mới khi `make e2e`), cùng `deploy/.env`. Harness ép qua biến shell, không sửa `.env` của người dùng: `XT_MCP_LOCAL_CLIENTS=` (client public chỉ tier masked) và `XT_DATA_CLIENTS=xt-cli`. Ánh xạ hostname ở tầng transport httpx/Playwright `route` tới APISIX `127.0.0.1:9080`, không đụng `/etc/hosts`; `CF-Connecting-IP` giả lập Cloudflare dùng TEST-NET-3 (203.0.113.x), mỗi lần chạy một IP để rate-limit DCR theo IP không dồn qua các lần chạy.
- **D-E2E-3. "User thuộc 2 Organization → tenant_ambiguous" trên Keycloak 26 thật.** Organizations bật identity-first: Keycloak bắt chọn một tổ chức lúc đăng nhập, token chỉ mang tổ chức đã chọn. E4 chấp nhận: token đúng 1 tổ chức đã chọn và xT không cấp quyền gì ngoài tổ chức đó (user không có trong sơ đồ → `access_denied`); nếu token mang >1 tổ chức thì phải là `tenant_ambiguous`. Lý do: đây là hành vi thật của IdP; kiểm `tenant_ambiguous` thuần ở lớp unit/integration đã có.
- **D-E2E-4. SLA ngắn cho E7 mà không sửa code.** demo-seed cài vào tenant B (An Phát) cùng gói `de-xuat-phe-duyet` nhưng mọi `sla.after` đổi thành `PT2M` (chỉ dữ liệu gói, qua đúng `xt pack install` hai bước). Nhân viên AP-003 báo cáo trực tiếp giám đốc để `escalate_to: requester.manager` leo thang tới GĐ.
- **D-E2E-5. Gói không cài được không dừng demo-seed.** Ghi vào `pack_failures` của state; E0 FAIL với đúng tên op/process còn thiếu. Lý do: báo cáo phải cho thấy mọi khoảng trống một lần.
- **D-E2E-6. Cấp quyền agent.** demo-seed cấp L1 tường minh (tệp links của `xt org import`) cho `xt-desk-agent` và `xt-cli` thay mặt từng người có đăng nhập; connector DCR mới (E2) được người vận hành cấp L1 bằng `xt org import --links` sau khi đăng ký. Không có quyền ngầm.
- **D-E2E-7. Bí mật user test.** Mật khẩu ngẫu nhiên + TOTP seed (HMAC-SHA1, 6 số, 30 s) chỉ ở `${XT_SECRETS_DIR:-~/.config/xt}/e2e/demo-seed.json` (0600); báo cáo e2e redact JWT/mật khẩu/vé.
- **D-E2E-8. E11 tamper** sửa một dòng `xt_audit.event` bằng superuser rồi hoàn nguyên sau khi kiểm (stack còn dùng được nếu `E2E_KEEP=1`).
- **Bổ sung sau audit (WA-02/WA-03):** đích ủy quyền phải qua đúng luật của người duyệt (PEP `request.decide` trên đề nghị, SoD, active trong tenant, khác người gọi/người đề nghị) và bị kiểm **lúc plan**, trước PendingOperation: không bắt người duyệt step-up cho thao tác chắc chắn thất bại. Đường gỡ bước kẹt chọn là **người đề nghị rút** (`transition_record` / REST withdraw), không mở quyền "quản trị gán lại" vì composition chưa có `admin_role` theo dữ liệu (không cài cứng vai trò).
