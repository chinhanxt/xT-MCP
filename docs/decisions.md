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
| 16/10/2026 | D-INT-7. Vai trò nào được ĐỀ XUẤT thay đổi model; agent có duyệt được không? | Đề xuất (`model.propose`, L1, không cần cổng người): `dept_head`, `director`, `it`; agent (`role:agent`) đề xuất thay mặt người có một trong các vai trò này (guard còn kiểm quyền của người được đại diện, nên nhân viên thường qua agent vẫn bị `behalf_permission`). Duyệt (`request.decide` trên `model_proposal`): chỉ `director`, `it`; agent chỉ mở bước xác nhận (`mdl.agent-open-confirmation`), hiệu lực do người có vai trò duyệt xác nhận ở /desk/confirm; PHÁ HỦY cần hai người khác nhau. Người đề xuất không tự duyệt (SoD, `self_approval`) | Dữ liệu policy của gói `quan-tri-mo-hinh`, không cài cứng; khớp tool-contracts-v1 (L1 đề xuất, human_only quyết định). | 

## DPoP cho Desk trước v1.0 (16/10/2026, chủ dự án – Opus 5.5)

- **Câu hỏi:** audit `/pii/reveal` vòng 2 nêu rủi ro tồn dư: ai cầm access token `xt-desk-agent` ngoài trình duyệt vẫn xem được PII trong phạm vi mình đọc được. Gắn với `sid` không đủ.
- **Quyết định:**
  - Áp DPoP (RFC 9449) cho `xt-desk-agent`: khóa non-extractable trong trình duyệt, token gắn `cnf.jkt`, vé PII gắn `jkt`, và `/pii/reveal` phải được ký bằng chính khóa DPoP đó.
  - Làm ngay sau khi qua cổng e2e E0–E11, trước khi tag v1.0.0 (bản nộp OLP).
  - Cho tới khi xong: chấp nhận rủi ro ở v0.1, kèm điều kiện production không có redirect loopback, CSP chặt, và ghi rủi ro trong SECURITY.md.
- **Lý do:** v1.0 là bản giám khảo chấm. Proof-of-possession biến token bị lộ thành vô dụng nếu không có khóa riêng của trình duyệt. Keycloak 26 hỗ trợ DPoP, nên không phải tự xây.

## Quyền của job role trên outbox xt_meta (16/10/2026, chủ dự án – Opus 5.5)

- **Câu hỏi:** job `workflow.relay_timers` chạy với `xt_job_t<seq>` (AQ-05) nhưng bị `permission denied for schema xt_meta` khi đọc `workflow_timer_outbox`, và không có quyền/policy trên `model_change_request` (policy chỉ `TO xt_app`). Hậu quả trên stack thật: SLA không leo thang, model không bao giờ được áp (E6/E7). Test W2 gọi `jobs.relay` trực tiếp bằng xt_app nên không thấy lỗi. Chọn (a) GRANT tối thiểu + RLS cho job role, hay (b) hàm SECURITY DEFINER bọc thao tác relay?
- **Quyết định: (a), cấp cho NHÓM `xt_job`** (migration `0008_job_role_relay.sql`):
  - `USAGE` schema `xt`, `xt_meta`; `EXECUTE` `xt.cur()`, `xt.tenant()`, `xt.tenant_seq()` (hàm ngữ cảnh mà policy cần).
  - `SELECT` đúng các cột relay đọc, `UPDATE (dispatched_at)` trên `workflow_timer_outbox` và `model_change_request`. Không INSERT/DELETE, không cột khác (`state`, `plan_hash`...), không bảng `xt_meta` nào khác.
  - `model_change_request.tenant_isolation` mở thành `TO xt_app, xt_job`. Thêm policy RESTRICTIVE `job_role_tenant ... TO xt_job` trên cả hai bảng: `current_user = 'xt_job_t' || xt.tenant_seq()`. Role của tenant A với ngữ cảnh B (ghép sai) cũng thấy 0 dòng.
  - Nhóm `xt_job` nay được tạo ngay trong `bootstrap.sql` (CSDL mới). CSDL cũ đã có nhóm này nhờ `xt-init`. Migration báo lỗi rõ ràng nếu thiếu nhóm (giống 0006). `xt tenant bind-queue` bảo đảm `GRANT xt_job TO xt_job_t<seq> WITH INHERIT TRUE` và từ chối chạy nếu chưa có quyền của 0008. Tenant mới không cần DDL riêng.
- **Lý do:**
  - Đúng thiết kế đã ghi (`integration-notes.md` §2: "GRANT cho nhóm xt_job, policy TO xt_app, xt_job").
  - Cô lập vẫn do RLS + ngữ cảnh mà dispatcher đặt. Job role không EXECUTE được `xt.set_context`, còn `GuardedCursor` chặn đổi role/GUC.
  - (b) cần hàm definer thuộc một role vượt được FORCE RLS (xt_migrator có BYPASSRLS), tức tự kiểm tenant bằng tay trong mỗi hàm. Bề mặt đặc quyền lớn hơn, lại phải sửa luồng `SELECT … FOR UPDATE SKIP LOCKED` + enqueue cùng transaction.
  - Kiểm chứng: `test_worker_job_role_relays_timer_escalation_and_model_apply` chạy `xt worker` thật (relay bằng `xt_job_t<seq>` → tick leo thang SLA + `model.apply` pha 1/2 → có cột mới). `test_job_role_cannot_touch_other_tenant_outbox` là test âm.

## REST /files/v1 — dữ liệu phi cấu trúc (16/10/2026, chủ dự án – Opus 5.5)

- **D-FILES-1. Audience:** chọn **riêng `xt-files`** (scope optional `xt-files`, `provision_data.py` cấp cho `XT_FILES_CLIENTS`, mặc định `xt-cli`), **không dùng lại `xt-data`**.
  - Lý do 1: `xt-data` là kênh đọc của PostgREST (gateway chỉ cho GET). Gộp vào thì token đọc dữ liệu có cả quyền ghi tệp.
  - Lý do 2: cùng mô hình RFC 8707 với `/workflow/v1`. Token lộ ở bề mặt này không phát lại được ở bề mặt khác; token còn mang aud MCP/workflow bị từ chối.
  - E8 lấy token `files` (`--get-token files`).
- **D-FILES-2. "Presigned URL" do xt-api ký (HMAC-SHA256, nhãn riêng), không dùng SigV4 presign của S3.**
  - URL có dạng `/files/v1/uploads/<token>`. Token gồm tenant, tệp, người tải lên, hạn. Hạn mặc định 15 phút. Link dùng được một lần: chỉ nhận khi tệp còn `awaiting_upload`.
  - Lý do 1: kiến trúc v2 cấm lộ SeaweedFS ra ngoài (N10).
  - Lý do 2: xt-api phải tự đếm byte, chặn quá hạn mức, tính sha256 và kiểm magic byte trước khi bất kỳ byte nào thành object.
  - Lý do 3: SigV4 ký theo Host nên vỡ khi đi qua APISIX/Cloudflare.
  - Đánh đổi: byte đi qua xt-api. Chấp nhận được với hạn mức 25 MiB và một tiến trình `minimal`.
- **D-FILES-3. Trạng thái:** giữ enum đóng của TCV1 §5.13 cho tool (`awaiting_upload, scanning, clean, not_scanned, infected, expired`). REST thêm `uploaded`.
  - "pending_scan" trong yêu cầu = `scanning`; "unscanned" = `not_scanned`.
  - Lý do: contract tool đã phát hành, không đổi tên. Bảng đối chiếu ở `docs/files-api.md` §2.
- **D-FILES-4. Tải về tệp chưa quét (profile minimal):** mặc định `XT_FILES_UNSCANNED_DOWNLOAD=uploader`. Chỉ người tải lên lấy lại được byte của chính mình; người khác nhận 409 `file_not_scanned`. Có thể đặt `deny` hoặc `allow`.
  - Lý do: rủi ro mã độc là lây sang người khác. Trả lại cho chính người tải lên không thêm rủi ro, và giữ được demo roundtrip.
  - Kiến trúc v2 ghi "minimal không tải về được". Quyết định này nới đúng một trường hợp đó, có cấu hình.
- **D-FILES-5. `Idempotency-Key` của `POST /files/v1` là tùy chọn** (workflow bắt buộc).
  - Lý do: ý định không có hệ quả nghiệp vụ, tự hết hạn. Object theo nội dung (sha256) nên tải lại không nhân đôi byte.
  - Có khóa thì gửi lại trả đúng tệp cũ (200). Khóa đi chung namespace actor+tool với `upload_file` của MCP.
- **D-FILES-6. Client S3:** tự viết AWS SigV4 trên `httpx` (6 thao tác), **không thêm boto3**.
  - Lý do: tránh ~90 MB phụ thuộc. Đã kiểm với SeaweedFS 4.48 thật: chữ ký sai → 403, sha256 sai → `BadDigest`.
- **D-FILES-7. Liên kết field `file`:** compiler model sinh FK ghép `(tenant_id, col) → xt_meta.file_object(tenant_id, id)` cho field kiểu `file`, giống field `ref`.
  - Lý do: chặn tham chiếu chéo tenant và id bịa ngay ở DB, không phụ thuộc lớp ứng dụng.
  - Tệp đã xóa mềm vẫn giữ hàng (ON DELETE RESTRICT), nên bản ghi cũ không gãy.
  - Model cũ chưa có FK chỉ được thêm khi model đó đổi.
- **D-FILES-8. IAM tệp là dữ liệu** (gói `tep-dinh-kem`, không cài cứng):
  - `file.create`: employee/agent.
  - `file.read`: người tải lên + vai trò duyệt hồ sơ.
  - `file.delete`: người tải lên.
  - Catalog host thêm `file.read` (L0) và `file.delete` (L1, xóa mềm khôi phục được). Đọc không được thì trả 404 như tệp không tồn tại (WA-07).

## Op dịch vụ của gói giao việc / vòng đời nhân sự (16/10/2026, chủ dự án – Opus 5.5)
Bối cảnh: E0 FAIL vì `giao-viec-sla` khai `close_task`, `weekly_rollup` và `nhan-su-vong-doi` khai `create_lifecycle_tasks`, `activate_employee`, `check_unreturned_assets`, `close_lifecycle_case` mà deployment chỉ có `check_overdue_advances`, `notify`; onboarding/offboarding có SLA nhưng thiếu `notify`.
- **D-OPS-1. Hiện thực op trong registry `OPS` của deployment** (`src/xt/entrypoints/composition/pack_ops.py`), không đổi gói sang op khác.
  - Lý do: các op là nghiệp vụ lõi của chính hai quy trình (đóng việc, kích hoạt nhân viên, đóng hồ sơ). Thay bằng op chung (`set_field`) thì pack phải mô tả cả ghi dữ liệu, mở rộng bề mặt mà không thêm giá trị.
  - Ràng buộc: không mã tùy ý; pack chỉ khai tên + tham số (biểu thức AST whitelist); op kiểm kiểu tham số (uuid bản ghi đúng model, ngày ISO, danh sách vai trò regex); mọi ghi qua `RecordService` với vai trò `xt_workflow` (quyền model + rule + RLS + audit), cùng transaction của bước; idempotent theo dữ liệu.
  - Factory op nhận thêm `ProcessDefinition` (op cần định nghĩa đã duyệt, ví dụ để sinh checklist).
- **D-OPS-2. "Cấp tài khoản / thu hồi quyền" = checklist, không gọi hệ thống ngoài.** `create_lifecycle_tasks` sinh `lifecycle_task` từ chính các bước người (không có `when`) của định nghĩa quy trình đã duyệt: id bước (hoặc tiền tố trước `_`) trùng giá trị `owner_unit` của model. Không có danh sách việc cứng trong code. Offboarding: nhân viên chuyển `offboarding_in_progress`, `left_on` = ngày hiệu lực hồ sơ. `close_lifecycle_case` của hồ sơ nghỉ việc lỗi (instance đứng ở bước cuối) khi còn `asset_issue`/`access_grant` chưa `revoked_on` (chốt "không đóng được khi chưa thu hồi" của README gói).
- **D-OPS-3. Sửa DỮ LIỆU gói ở hai chỗ** (gói 0.3.0):
  - `weekly_digest` v3: bước gửi đổi `notify` -> `send_weekly_digest(digest_id, to_roles='director,dept_head')`. Lý do: instance định kỳ không có người đề nghị nên `notify` (gửi người đề nghị) không tới ai; op mới gửi người có vai trò khai trong gói (+ trưởng phòng của bản tin theo phòng) và đánh dấu `sent`. `weekly_rollup` ghi `weekly_digest` (mã `BT-YYYY-Www[-PHÒNG]`, chạy lại thì cập nhật nếu chưa gửi).
  - `employee_onboarding` v3: thêm bước `close_case` (`close_lifecycle_case`), để `activate_employee` chỉ kích hoạt nhân viên (`probation`, `hired_on` = ngày hiệu lực) và hồ sơ tiếp nhận được đóng như hồ sơ nghỉ việc.
  - `nhan-su-vong-doi`: thêm `notify` (SLA), `depends_on: ["de-xuat-phe-duyet"]` (op `check_overdue_advances` đọc `advance_request`).
- **Việc còn mở (ngoài phạm vi):** (1) vai trò/khóa phòng trong `seed/binh-minh/org.json` (`it`, `accountant`, `D04`, `D05`) khác tên gói dùng (`it_admin`, `payment_accountant`, `chief_accountant`, `NS`, `IT`) nên trên seed thật các bước đó rơi về `fallback_assignee`; (2) chưa có bề mặt khởi tạo instance cho quy trình không gắn `request` (task/hồ sơ) và chưa có poller timer-start trong `xt-worker`. Test tích hợp (`tests/integration/composition/test_pack_ops.py`) dùng tổ chức đúng vai trò gói và gọi `ProcessService` thật.

## SoD ở DB: sổ quyết định (16/10/2026, chủ dự án – Opus 5.5)
Bối cảnh: W9 / E10 yêu cầu DB tự chặn `decider = requester`, dưới PEP/IAM và runtime. Trước đây quyết định chỉ nằm trong JSON state của instance, không có dòng nào để ràng buộc.
- **D-SOD-1. Bảng chỉ-ghi-thêm `xt_meta.decision`** (migration `0010_decision_sod.sql`): một dòng mỗi quyết định của người (bước của đề nghị qua `decide_request`; mỗi người duyệt của đề xuất thay đổi model), ghi trong CÙNG transaction, TRƯỚC khi engine chạy. Có RLS tenant, audit hash-chain (`install_meta`), xt_app chỉ SELECT/INSERT.
- **D-SOD-2. Trigger `decision_sod_guard` (BEFORE INSERT/UPDATE/DELETE)** từ chối:
  1. `approver = requester` (thêm CHECK `decision_sod_not_requester`);
  2. người duyệt không phải người đang hoạt động có `subject`, hoặc `subject` đó không nằm trong `confirmed_by` (danh tính đã step-up trên trang xác nhận). Đây là cách diễn đạt "agent thì principal duyệt phải là người": agent (kể cả thay mặt chính người duyệt) không bao giờ có trong `confirmed_by`;
  3. `subject` của người đề nghị nằm trong `confirmed_by`;
  4. mọi UPDATE/DELETE.
  - Ứng dụng dịch `CheckViolation` -> `SodViolation` -> `access_denied` reason `sod`.
- **D-SOD-3. Idempotent:** `CREATE TABLE IF NOT EXISTS`, `CREATE OR REPLACE FUNCTION`, `DROP TRIGGER IF EXISTS` + `CREATE TRIGGER`, policy kiểm `pg_policies`; chạy lại thân migration trên CSDL đã có không lỗi (có test). CSDL cũ nhận bảng qua `xt db migrate` như mọi migration.

## Từ vựng vai trò/phòng: seed ánh xạ vào từ vựng gói (16/10/2026, chủ dự án – Opus 5.5)
- **D-MAP-1. Từ vựng chuẩn = của gói** (`it_admin`, `payment_accountant`, `chief_accountant`, `hr`, `director`; mã phòng `NS`, `IT`, `KT`...), vì quy trình/chính sách của gói là sản phẩm; dữ liệu tổ chức của từng công ty ánh xạ vào đó, không đổi gói và không viết cứng trong code. Giải việc còn mở (1) của D-OPS-3.
- **D-MAP-2. `seed/<công ty>/mapping.json`** `{departments: {id: mã phòng}, roles: {vai trò tệp: [vai trò gói]}}`; khóa `role@ID` cộng thêm vai trò cho người giữ vai trò đó trong đúng phòng (vd `dept_head@D02` -> `chief_accountant`). `xt org import --mapping` nạp tệp này (chỉ với tệp HR); phòng/vai trò của tệp không có trong mapping, mapping sai kiểu, hoặc `role@ID` trỏ phòng không tồn tại -> `AdminError` liệt kê hết. `--base-roles` vẫn cộng nguyên văn sau ánh xạ.
- **D-MAP-3. `org.json` giữ nguyên** (dữ liệu thô từ HR). `make demo-seed` (`e2e/xte2e/seed.py`) truyền `--mapping` (tệp `mapping.json` cạnh `org.json`) và tính vai trò Keycloak/TOTP từ cùng mapping, thay bảng alias cứng cũ.
- Test: `tests/unit/composition/test_org_mapping.py`; `tests/integration/composition/test_seed_mapping.py` (nạp seed/binh-minh, onboarding: hr/it/accounting giao đúng người của seed, không có giám đốc).
- **D-FILES-9 (sau audit, 16/10/2026).**
  - Quota mặc định: 20 ý định mở/người, 500/tenant, 256 MiB/người, 1 GiB/tenant. Kho minimal 2 GiB (`-volume.max=32`). HTTP 429 cho quota ý định, 507 cho quota byte (code `resource_constraint`).
  - Thêm `scan_failed` vào enum trạng thái của `upload_file`. Đây là thay đổi bổ sung; không ép `scan_failed` thành `scanning` hay `infected`, vì cả hai đều nói sai sự thật.
  - Tên tệp trong audit: HMAC có khóa qua cơ chế GUC sẵn có, không bỏ hẳn tên. Lý do: giữ khả năng đối chiếu khi điều tra mà không lộ tên.
  - `file.read` của người duyệt theo phòng ban (director toàn tổ chức). Quyền theo người được giao duyệt cần liên kết tệp ↔ bản ghi, để sau.
  - Multipart có `Transfer-Encoding` bị từ chối (411); PUT vẫn đếm bằng Spool.

## Quyền đọc tệp đính kèm (16/10/2026, chủ dự án – Opus 5.5)

- **Câu hỏi:** audit `/files` vòng 2 nêu: `file.read` theo phòng làm kế toán/HR không đọc được chứng từ của phòng khác, dù họ phải xử lý đề nghị thanh toán và hồ sơ nhân sự.
- **Quyết định:**
  - Quyền đọc tệp **đi theo quyền đọc bản ghi mà tệp được gắn vào**: ai đọc được bản ghi (IAM + RLS) thì đọc được tệp đính kèm của bản ghi đó.
  - Tệp chưa gắn bản ghi nào: chỉ người tải lên và phòng của họ (theo policy) đọc được.
  - Xóa mềm vẫn tính vào quota cho tới khi bị dọn hẳn.
- **Lý do:** không phải duy trì một ma trận quyền riêng cho tệp, tránh lệch giữa quyền đọc bản ghi và quyền đọc tệp, và khớp nghiệp vụ thực tế (chứng từ đi kèm đề nghị).

## start_process + timer start (16/10/2026, chủ dự án – Opus 5.5)
Bối cảnh: sau D-OPS, demo 3 gói lõi kẹt vì (1) không có bề mặt khởi động `giao-viec-sla` / `nhan-su-vong-doi` (quy trình không gắn model đề nghị), (2) `xt-worker` không chạy timer start (`weekly_digest`).
- **D-START-1. Hiện thực `start_process` đúng contract TCV1 §6.1** (schema, effects -> annotations, IAM `process.start`, trần L3), là tool v1.0 đầu tiên trong catalog (18 tool). MCP và REST `POST /workflow/v1/instances` (`{definition_id, subject_ref?, parameters?}`) đi CÙNG dispatcher (IAM -> idempotency -> plan -> tự chủ -> commit -> làn PII), không đường tắt.
- **D-START-2. Không đưa vào `core-10`.** Hồ sơ model nhỏ giữ đúng 10 tool đo ở §10 (token, eval qwen3.5:4b); khởi động quy trình là việc hiếm, của người quản lý/HR. Hồ sơ đầy đủ có tool; skill nêu nó ở `references/`.
- **D-START-3. Mức hiệu lực L1.** Người được đại diện luôn là người khởi tạo (`requester`), tương đương "gửi đề xuất của chính mình" (§3.5); quyền ai được khởi động quy trình nào là IAM dữ liệu của gói (statement `process.start` trên `xrn:<t>:process:<id>`). Trần L3 của contract giữ chỗ cho quy trình khai mức cao sau này (spec chưa có trường mức).
- **D-START-4. Phạm vi:** chỉ trigger `manual` không gắn model `request` (gắn -> `wrong_tool`, đi `submit_request`; `timer` -> `not_manual_trigger`). Subject phải đọc được bằng RLS của người gọi; quy ước dữ liệu: input `<model>_id` kiểu str nhận ref subject nếu chưa truyền. Subject được nối với instance (inbox/timeline thấy bản ghi), subject đã gắn instance khác thì giữ liên kết cũ.
- **D-START-5. Timer start là job `workflow.timer_starts`**, relay hẹn mỗi chu kỳ (không thêm chuỗi tự hẹn thứ hai, không thêm bước `bind-queue`). Idempotent bằng khóa chính `xt_meta.workflow_timer_fire(tenant, process_id, period_key)` trong CÙNG transaction tạo instance qua `ProcessService`; `not_before` = `process_definition.created_at` (không chạy bù kỳ trước lúc cài). Đã kiểm 2 worker đồng thời: đúng một instance/kỳ.
- **Việc còn mở:** ~~chưa có tool hoàn thành bước người của quy trình không gắn đề nghị~~ -> đã đóng bằng D-STEP (dưới).

## Hoàn thành bước người không gắn đề nghị qua bản ghi chủ thể (16/10/2026, chủ dự án – Opus 5.5)
Bối cảnh: `accept`/`work`/`acceptance` của `task_normal`/`task_urgent`, các bước HR/IT/Kế toán/Quản lý của tiếp nhận/nghỉ việc không hoàn thành được (`decide_request` chỉ nhận đề nghị); `GET /workflow/v1/instances/{id}` 404 với instance không gắn bản ghi.
- **D-STEP-1. Không thêm tool.** Catalog giữ 18 tool (eval: model nhỏ kém đi khi catalog lớn). Mỗi bước người khai `complete_via` trong ProcessSpec: `{model, match, states, state_field="state", autonomy}`; `transition_record` trên đúng bản ghi chủ thể chuyển sang `to_state` -> bước nhận outcome `states[to_state]` (`done`/`approve`/`reject`/`return_for_revision`). Lý do chọn spec (không phải `bindings` của pack): ràng buộc đi theo version quy trình đã duyệt (bất biến, có trong sha256), một model dùng được cho nhiều quy trình, `propose_process_change` khai được. Spec cũ không có `complete_via` giữ nguyên sha (khóa vắng mặt bị bỏ khi băm).
- **D-STEP-2. Kiểm tra theo thứ tự:** IAM `record.transition` trên XRN bản ghi (statement `tsk.step-transition` / `hr.step-transition` của gói) -> bản ghi là chủ thể của task đang mở (instance gắn chính bản ghi hoặc bản ghi nó tham chiếu, cộng instance có việc của người gọi) -> người được đại diện là ứng viên của task (`not_assigned`) -> SoD của bước (`check_act`, kiểm lại lúc `act`) -> `to_state` hợp lệ (`transition_not_allowed` + danh sách) -> CAS revision. Ghi bản ghi bằng vai trò `xt_workflow` SAU `act` (bước dịch vụ phía sau như `close_task` có thể đã ghi; chỉ ghi phần còn thiếu), cùng transaction, rule model áp dụng.
- **D-STEP-3. Mức tự chủ là dữ liệu của gói:** `autonomy` L1/L2/L3 theo trần agent; `human_only` (mặc định, bắt buộc cho bước `approve`) -> plan L3 với người xác nhận = chính người được giao -> agent luôn nhận `confirmation_required`. Gói 0.3.2: `accept`/`work` L1 (việc của chính người làm), nghiệm thu và mọi bước vòng đời nhân sự `human_only`.
- **D-STEP-4. Bước không có bản ghi chủ thể sẵn -> tạo tự động khi quy trình chạy, không thêm tool.** `create_lifecycle_tasks` sinh một dòng `lifecycle_task` cho MỌI bước người của định nghĩa (kể cả bước có `when`, vd `chief_acct_overdue`), trường mới `step` = id bước (khóa `match`), `owner_unit` thêm `chief_accountant`. Bước có điều kiện chưa từng chạy -> `close_lifecycle_case` đặt `skipped` kèm ghi chú.
- **D-STEP-5. Bề mặt:** `get_inbox` trả `ref` = bản ghi chủ thể, `revision`, `to_states`, `suggested_tool:"transition_record"`; `get_record` điền `available_transitions` (kèm `level`) cho bước người gọi giữ. REST `POST /tasks/{ref}/decision`: backend ánh xạ `approve`->`done` (bước task)/`approve`, `reject`, `return_for_revision` sang `to_state` rồi gọi CÙNG tool `transition_record` (`values` tùy chọn); gửi lại cùng Idempotency-Key lấy lại `to_state` của lần đầu. `GET /instances/{id}` của instance không gắn bản ghi: người tham gia (khởi tạo, đang/đã giữ bước) nhận `request: null` + `task_id` = ref bản ghi chủ thể; người khác 404 như id không tồn tại.
- **D-STEP-6 (vá audit `docs/audit-process-steps.md`, gói 0.3.3).** PSA-03: `start_process` kiểm MỌI ref bản ghi trong `parameters`/`subject_ref` (input khớp `complete_via.match`, và mọi giá trị dạng `<model>:<id>`): đúng model, cùng tenant, người khởi tạo đọc được (RLS + IAM `record.read`), bản ghi quy trình sẽ ghi (`match.id`) cần thêm `record.update`; từ chối = `not_found` như id ma. `get_inbox` không lộ tiêu đề/người tạo của bản ghi người xem không đọc được. PSA-01: `values` chỉ nhận `complete_via.writable_fields` của bước (mặc định rỗng; khóa match, `state_field`, ref/user/file, managed_by luôn bị loại; kiểm lại lúc cài gói), lỗi `field_not_writable`; ghi qua `RecordService.workflow_write` (validator kiểu + rule của model). Gói: `work` -> `["result"]`, bước nhân sự -> `["note"]`, còn lại rỗng. PSA-04: task đang hoàn thành đưa vào ngữ cảnh op (`ACTING`), `close_lifecycle_case` không đánh `skipped` bước vừa xong. PSA-06: REST decision trên bản ghi không phải đề nghị/đề xuất model luôn đi `transition_record` (không suy ra `to_state` thì gửi tên quyết định) -> cùng lỗi chuẩn (404 như ref ma). O3: `GET /instances/{id}` kiểm người tham gia trên meta trước khi nạp engine. Chưa làm (ghi nhận): O1 (state_field không managed_by: chặn `update_record` theo tên `state`), O2 (chuyển người giữ bước ngoài leo thang SLA), O4 (statement `record.approve` của gói không áp vào bước; SoD bước = `distinct_from` + `human_only`), O5 (inbox khóa FOR UPDATE).
- **Giới hạn đã biết:** RLS/IAM `own` của `task` theo `owner_id` (người tạo), nên người làm không `get_record` được task của mình; luồng dùng `revision` từ `get_inbox` (đủ cho transition). 

## Tải tệp chưa quét ở profile minimal (16/10/2026, chủ dự án – Opus 5.5)

- **Câu hỏi:** ở `minimal` không có ClamAV, tệp ở trạng thái `not_scanned`. Với mặc định `uploader`, kế toán không tải được chứng từ của đề nghị mà họ có quyền xử lý.
- **Quyết định:**
  - `XT_FILES_UNSCANNED_DOWNLOAD=readers` là mặc định của `minimal`: ai đọc được bản ghi gắn kèm thì tải được, luôn kèm `Content-Disposition: attachment` + `X-Content-Type-Options: nosniff`, và audit ghi rõ trạng thái `not_scanned`.
  - Profile `full`: bắt buộc quét bằng ClamAV, chỉ tải được khi `clean`.
- **Lý do:** profile `minimal` dành cho demo và thử nghiệm. Nghiệp vụ phải chạy được, rủi ro được khoanh vùng bằng header tải về và audit; triển khai thật dùng `full`.
- **D-FILES-10 (vòng 2, 16/10/2026).**
  - Hiện thực hai quyết định "Quyền đọc tệp đính kèm" và "Tải tệp chưa quét ở profile minimal" (mặc định `readers`; profile full chỉ cho tải `clean`).
  - Tuần tự hóa purge/finalize bằng advisory lock theo (tenant, sha256), không dùng refcount. Lý do: không thêm cột phải giữ đồng bộ; quyết định xóa nằm trong cùng transaction với khóa.
  - Guard SeaweedFS là `127.0.0.1,@`, vì `@` là địa chỉ của gRPC nội tiến trình (S3/filer → master/volume). Thiếu `@` thì không grow được volume.

## Thông báo nội bộ và Telegram hiển thị (16/10/2026)

- **D-NOTIFY-1.** Tool `notify` v1.0 giữ nguyên input/output/effects của TCV1 §6.4, mức L2; IAM chuẩn là **`notify.send`** theo yêu cầu triển khai. Sửa duy nhất metadata IAM §6.4 từ `notification.send`; tên cũ trong action catalog chỉ để tương thích dữ liệu cũ, không cấp quyền gọi tool mới. Không thêm vào core-10.
- **D-NOTIFY-2.** Port `Notifier` ghi inbox và delivery outbox trong transaction của mutation/receipt. Dùng lại `xt_meta.notification` để `get_inbox` thấy ngay; RLS hạn chế SELECT/đánh dấu đọc đúng người nhận, worker dùng vai trò dịch vụ `xt_workflow`. Recipients là người đang hoạt động trong tenant, không nhận `chat_id` hay địa chỉ ngoài tổ chức từ tool.
- **D-NOTIFY-3.** **Telegram không phải bằng chứng duyệt**, không có callback Duyệt/Từ chối hay đường gọi ConfirmationService. Quyết định này thay mọi mô tả Telegram tap/nonce cũ trong arch-v2.2 D8/§5.5 và storyboard TCV1. Tin chỉ gồm tóm tắt an toàn, mã `confirmation_id#8-hex-digest` chụp từ pending operation và `confirm_url` của Desk; Desk hiển thị cùng mã. Người xác nhận vẫn phải đăng nhập tươi + OTP trên Desk.
- **D-NOTIFY-4.** Mọi text tự do và tên người đều bị **omitted** khi gửi Telegram; chỉ dùng mẫu thông báo cố định + mã opaque + locator Desk đã kiểm origin/path. Regex nhận diện email/điện thoại/CCCD là chốt cuối, không dùng để chứng minh text tự do hết PII. Ghi `pii_egress(client_class=telegram, mode=omitted)` bền trước khi gọi Bot API hoặc trả tóm tắt; không ghi nội dung gốc vào log. HTTPX log được che credential bot.
- **D-NOTIFY-5.** Bot API dùng `httpx` BSD-3-Clause đã có, không thêm SDK. aiogram MIT đã kiểm nhưng chưa cần; không dùng `python-telegram-bot` GPL. Tham chiếu giao thức: [sendMessage](https://core.telegram.org/bots/api#sendmessage), [setWebhook](https://core.telegram.org/bots/api#setwebhook). Test chỉ dùng `httpx.MockTransport`/IdP cục bộ, không đăng ký webhook hay gọi Telegram thật.
- **D-NOTIFY-6.** Link code 256 bit, SHA256 trong DB, TTL 10 phút, tiêu một lần nguyên tử và gắn người đang hoạt động. Desk/CLI chỉ khởi tạo luồng; **chỉ trang người dùng sau OIDC PKCE + OTP mới hiển thị mã**, tránh agent đại diện tự liên kết tài khoản Telegram của kẻ khác. Webhook chỉ chấp nhận private chat có `from.id == chat.id`, header secret duy nhất và `/start <code>`, `/link <code>`, `/summary <ref>`. Bỏ qua callback/forward/group; mã update chống replay bền trong DB.
- **D-NOTIFY-7.** Worker checkpoint assignment/escalation/confirmation_required cùng transaction enqueue; quét bước đang mở để bù runtime chưa phát event assignment thông thường. Reservation `sending` và audit egress commit trước network. Bot API `sendMessage` không có idempotency key: không tự gửi lại trạng thái `sending/failed` khi kết quả mơ hồ; inbox vẫn bền, người vận hành đối soát trước khi gửi lại. Không tuyên bố exactly-once qua mạng.
- **D-NOTIFY-8.** `xt-bot` nằm **trong xt-api** (route webhook/link) và xt-worker (relay), không thêm container hay stack nặng. Token rỗng tắt Telegram; profile full dùng env `TELEGRAM_*`. APISIX giữ nguyên header secret và bearer/DPoP, không chèn credential.
- **D-NOTIFY-9. Leo thang SLA báo ngay (16/10/2026).** Sự kiện `assignment.escalated` do timer phát ra được `ProcessContext._event` ghi `process_event` rồi gửi qua Notifier (`enqueue_notification`) trong cùng transaction, claim nguồn `event:<id>` giống hệt khóa của NotificationRelay. Lý do: relay chạy trước tick nên leo thang phải chờ thêm một chu kỳ relay; claim chung bảo đảm đúng một thông báo `kind=escalation`. Recipient bị chặn quota (`rate_limited`) thì savepoint hoàn tác claim, relay gửi lại sau. Phương án bị loại: khôi phục `store.notify` cũ (đi vòng Notifier, không vào outbox Telegram).
- **D-E2E-9. DPoP trong harness (16/10/2026).** Khi `XT_DPOP_REQUIRED=true`, phiên của client trong `XT_DPOP_CLIENTS` (mặc định `xt-desk-agent`) sinh một khóa ES256 riêng (`e2e/xte2e/dpop.py`): proof ở token endpoint (đổi code, refresh), mỗi request `/mcp` một proof (`htm`/`htu` theo URL https logic, `ath` = hash access token, scheme `DPoP`), `/pii/reveal` ký `ath` = hash vé. E10 thêm ca âm: token gắn khóa gửi như Bearer, proof khóa khác, sai `htu`, thiếu `ath`, phát lại `jti`. Cổng bàn giao chạy 2 lượt `make e2e` + `make e2e-llm`: cờ tắt và cờ bật.

## Mô hình đe dọa của `xt_migrator`: phòng ngừa + phát hiện (16/10/2026, chủ dự án – Opus 5.5)

- **Bối cảnh:** audit Model Engine vòng 1–4 lần nào cũng tìm ra đường mới để migrator chen code (trigger trên bảng miễn audit, CHECK/DEFAULT/domain, DROP COLUMN CASCADE…). Gốc rễ: migrator sở hữu bảng, nên Postgres luôn còn đường vòng. Vá từng lỗ không có điểm dừng.
- **Quyết định:**
  1. **Phòng ngừa** cho các đường có tác động lớn đã biết (R4-01, R4-03, R4-02/04): event trigger chặn trigger/rule trên mọi bảng `xt_meta` (kể cả bảng miễn audit), chặn DROP/ALTER cột `tenant_id` và mọi thao tác làm mất policy RESTRICTIVE, chặn đổi constraint trên `login_tenant`/`session_ctx`, bảo vệ thân `xt.row_touch()` và mọi hàm trong schema `xt`/`xt_audit` như `xt.cur()`, cấm gọi `xt.set_context` từ biểu thức (CHECK/DEFAULT/generated/domain).
  2. **Phát hiện** cho mọi thứ còn lại: checkpoint Ed25519 chụp digest **toàn bộ catalog liên quan bảo mật** (policy, trigger, rule, constraint, default, hàm, event trigger, role membership, grant) của các schema xt*. `xt audit verify` báo đúng đối tượng nào bị đổi kể từ checkpoint.
  3. **Mục tiêu cô lập credential — chưa hoàn tất (xem D-RESUME-2):** mật khẩu `xt_migrator` chỉ có trong container `xt-init` (one-shot); `xt-api`/`xt-worker` không bao giờ có. Bị lộ credential migrator được coi tương đương lộ DBA.
- **Lý do:** chặn những gì đã biết là nguy hiểm, và biến mọi thay đổi lén lút còn lại thành thứ **phát hiện được** bằng bằng chứng mật mã. Đây là chuẩn thực tế cho vai trò quản trị CSDL.

## Tiếp tục phiên bằng Codex (16/10/2026)

- **D-RESUME-1 — Quyền và phạm vi.** Chủ dự án yêu cầu tiếp tục phát triển với đúng **3 subagent**; yêu cầu này thay quy tắc số agent cũ trong `CLAUDE.md`. Mỗi worker nhận một worktree riêng từ `2784151` kèm bản chụp các thay đổi chưa commit. Integrator giữ cây `/home/chinhan/xT`, rà và tích hợp delta; không commit, push, release hay triển khai production. Ba phạm vi: Model Engine/audit vòng 4, fixture/PII/integration, harness E2E/deploy. Mỗi lúc chỉ một worker giữ suất kiểm thử nặng. Biên nhận cục bộ và bản chụp đầu vào: `/tmp/xt-resume-20261016/`.
- **D-E2E-10 — Giữ lỗi vòng đời.** `make e2e` phải dừng ngay nếu dựng stack hoặc seed lỗi; không chạy kịch bản trên đầu vào chưa hợp lệ. Trap EXIT dọn stack kể cả khi lỗi hoặc nhận HUP/INT/TERM, trừ `E2E_KEEP=1`. Lỗi dọn dẹp làm lượt chạy thất bại nếu các bước trước thành công; nếu đã có lỗi thì giữ mã lỗi ban đầu. Kiểm tra recipe thật qua subprocess với runner giả cho từng bước, không cần Docker cho các đường lỗi này.
- **D-DESK-LLM-1 — Proxy LLM thật trong bản triển khai.** Bổ sung route APISIX `/llm/chat/completions` -> xt-api vì bản static nginx không có Vite proxy. Dùng verifier MCP hiện có (scope, DPoP, replay), kiểm client Desk + tenant + người đang hoạt động và thu hồi L2. Endpoint chỉ gọi model/upstream cấu hình của operator; không chuyển credential từ trình duyệt sang LLM, không redirect. Giới hạn 1 MiB JSON, 4096 token sinh, 4 lượt mỗi tiến trình, 420 giây tuyệt đối; hủy hoặc ngắt kết nối phải trả suất. E1 dùng relay byte chỉ trên gateway của mạng Docker test để tới Ollama loopback thật, không đổi dịch vụ của người dùng.
- **D-RESUME-2 — Đối chiếu tuyên bố cô lập credential.** Rà soát độc lập xác nhận API/worker vẫn cần DSN migrator cho rehearsal/replan và apply/pha 2; Compose vẫn cấp biến. Giữ mục tiêu tách đặc quyền ở trạng thái **chưa hoàn tất**, sửa tài liệu từng mô tả như đã đạt. Không gỡ biến đơn thuần làm mất model động. Đợt kiểm E2E hiện tại chứng nhận chức năng/guard/checkpoint; không chứng nhận ranh giới credential. Thiết kế và tiêu chí executor tiếp theo ở `docs/model-engine.md` §7a.

- **D-DESK-LLM-2 — Khám phá và phục hồi bằng schema chuẩn.** Profile mặc định `standard` thêm `inspect_workspace`/`describe_model` bên cạnh core-10 không đổi. Build nhúng chính `skills/xt-core/SKILL.md`, không duy trì bản prompt nghiệp vụ riêng. Lỗi validation trả `field_contracts` có kiểu/ràng buộc cần sửa, tối đa 16 KiB và báo trường bị bỏ; không trả default/giá trị người dùng. Model bỏ ref tùy chọn nếu người dùng không yêu cầu và chưa có UUID thật; yêu cầu “tạo và gửi duyệt” cho phép tiếp tục submit với ref/revision thật vừa nhận, vẫn giữ confirmation/denial phía server. Temperature mặc định 0, tối đa 10 bước.
- **D-E2E-11 — E1 đủ đầu vào, kiểm kết quả thật.** Câu người dùng nêu rõ ngày lập, ngày công tác và hạn hoàn ứng theo lịch chạy, giữ mục tiêu tạm ứng 8 triệu/Đà Nẵng/3 ngày/tiền mặt; không điền ngầm dữ liệu nghiệp vụ còn thiếu. Mỗi lượt phải có LLM 200, đúng proof DPoP khi bật, bản ghi có giá trị chính xác và thẻ DOM “Đề xuất đã gửi duyệt”; ba hội thoại độc lập, ít nhất hai thành công, sau đó trưởng phòng và giám đốc duyệt bằng OTP thật.
- **D-DESK-CSP-1 — Validator không sinh mã và header sandbox riêng.** Dùng provider CfWorker JSON Schema cho cả MCP client và bridge App để hoạt động dưới CSP production không có unsafe-eval. Giữ DENY cho Desk; location sandbox chỉ cho đúng ancestor Desk cấu hình, bootstrap áp policy App trước khi tạo srcdoc. Renderer chỉ nhận origin URL hợp lệ; build sử dụng context skill chuẩn riêng.

- **D-DESK-IDEM-1 — Khóa ghi thuộc phiên hội thoại.** E1 phát hiện qwen temperature 0 dùng cùng alias idempotency ở nhiều hội thoại độc lập và nhận kết quả cũ (UI xanh nhưng không có bản ghi mới). Desk ánh xạ alias theo tên tool sang UUID riêng trong hội thoại xác thực, chỉ khi schema khai idempotency_key. Lịch sử assistant và chip ghi đúng UUID đã gửi; cả alias và UUID đều retry về cùng khóa, kể cả sau xác nhận ở lượt tiếp theo. Kho ánh xạ xóa khi đăng xuất/kết nối mới. Không đổi key phía server, quyền hay dữ liệu nghiệp vụ. Regression kiểm hai hội thoại khác nhau và retry/confirmation cùng hội thoại.

- **D-DESK-LLM-3 — Giữ quy tắc gần kết quả tool.** E1 trên tenant sạch phát hiện model 4B có thể dừng ở draft hoặc suy diễn ref tùy chọn dù skill đã ở đầu hội thoại. Mỗi request sau bước tool thêm một thông điệp system chứa nguyên mục Quy tắc của skill chuẩn; không lưu lặp vào history, không biến kết quả tool thành chỉ thị. Làm rõ mặc định đại diện người đăng nhập và chỉ gửi sau create khi người dùng yêu cầu gửi. Không tăng trần bước, không tự gọi tool hoặc điền nghiệp vụ phía host.

## Hợp nhất xT + Studio + WareTrack (17/10/2026)

- **D-UNI-H-1 — Thêm thành viên / gán vai trò ở không gian H.** Dùng gói `packages/nhan-su-vong-doi` (employee, department, lifecycle_case, access_grant) qua tool cố định và quy trình duyệt; không thêm MCP tool, không gọi `xt org import` từ web.
  - **Lý do:** `xt org import` chạy bằng DSN `xt_migrator` (quyền cao nhất) nên chỉ dùng để nạp sơ đồ tổ chức ban đầu từ CLI. Luồng pack có người đề xuất, người duyệt, audit và thu hồi (`revoked_on`), dùng chung được cho web và ChatGPT/MCP.
  - **Hệ quả:** cần một application service trong lõi áp `access_grant` đã duyệt (system `keycloak`, role thuộc danh sách cho phép của tenant) thành vai trò hiệu lực của person; thu hồi có hiệu lực ở lần kiểm `ensure_active` kế tiếp. Người tạo không tự duyệt cấp quyền cho chính mình.
- **D-UNI-WT-1 — Nguồn gốc WareTrack 3D.** `imports/waretrack-3d` do thành viên team (NguyenThong251) chia sẻ cho dự án; đã gỡ `.git` upstream, mã thuộc repo xT và phát hành theo giấy phép của dự án (Apache-2.0), ghi công tác giả trong `imports/README.md`.
- **D-UNI-AGG-1 — Hiện thực `aggregate_records` ở lõi.** Báo cáo D (nhập/xuất theo kỳ, tồn theo kho/SKU) cần cộng dồn phía server; `aggregate_records` đã có trong hợp đồng tool v1 nhưng composition trả `unavailable`. Hiện thực tool này (count/sum trường milli, group_by kể cả ref, time_bucket ngày/tuần/tháng, where như `search_records`) dưới cùng IAM đọc, RLS và quy tắc PII; không thêm tool mới. Bổ sung `doc_date` (thời gian nghiệp vụ) cho phiếu và `occurred_on` cho biến động, tách khỏi `posted_at` (thời gian tiếp nhận).
  - **Lý do:** agent không được tự cộng số từ `search_records`; giới hạn trang làm sai tổng. Giữ bộ tool cố định.
- **D-UNI-T3-1 — Tầng 3 là nền tảng ứng dụng cắm-rút theo 4 nhóm.** Chủ dự án đồng ý (17/10/2026). Mỗi ứng dụng chuyên ngành là gói nghiệp vụ + manifest ứng dụng; lõi hỗ trợ cài/tắt/bật lại gói theo tenant qua đề xuất–duyệt–audit, tắt không xóa dữ liệu. Cổng có trang 4 nhóm; mỗi nhóm một ứng dụng mẫu (WMS, Timesheet/PSA, CRM, Tài sản/IoT mô phỏng). Kế hoạch: G8, nghiệm thu U15.
  - **Lý do:** đề thi mô tả Tầng 3 là ứng dụng chuyên ngành cắm-rút linh hoạt chia 4 nhóm; trước đây xT chỉ có WareTrack gắn cứng, không thể hiện được cơ chế này. Tận dụng "nghiệp vụ là dữ liệu" có sẵn, không cần viết ứng dụng riêng cho từng ngành.
