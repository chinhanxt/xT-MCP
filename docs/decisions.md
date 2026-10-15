<!-- SPDX-License-Identifier: Apache-2.0 -->
# Nhật ký quyết định

Các quyết định thay chủ dự án do agent Opus 5.5 "decision" đưa ra trong chế độ tự vận hành. Mỗi mục ghi: ngày, câu hỏi, các phương án, lựa chọn, lý do.

| Ngày | Câu hỏi | Lựa chọn | Lý do |
|---|---|---|---|
| 16/10/2026 | Desk production gọi `/mcp` cùng origin (`xt.freepro.online/mcp`) nhưng APISIX đẩy host Desk sang nginx tĩnh (404). Sửa ở đâu? (a) đổi Desk gọi thẳng `https://xt-mcp.freepro.online/mcp` (cần CORS), (b) thêm route APISIX `/mcp` trên host Desk tới `xt-api:8000`, (c) nginx proxy trong ảnh Desk | **(b)**: thêm route `desk-mcp` (hosts Desk, uris `/mcp`, priority > desk-static) tới `xt-api:8000`, KHÔNG chèn bearer | Cùng origin nên không cần CORS và giữ nguyên token trong bộ nhớ; không đổi code Desk; MCP vẫn tự xác thực (gateway không xác thực, D12); `XT_MCP_ALLOWED_HOSTS` cần thêm host Desk. Đã áp dụng 16/10/2026 (`deploy/apisix/apisix.yaml.tmpl`, route `desk-mcp`, kèm `proxy-buffering` tắt, `limit-req`, timeout 3600 s; compose thêm host+origin Desk vào `XT_MCP_ALLOWED_HOSTS/ORIGINS`). Không thêm `/.well-known/*` trên host Desk: Desk dùng issuer/client cấu hình lúc build, PRM chỉ phục vụ connector ở host MCP. |
| 16/10/2026 | Chạy E2E https trên APISIX http cục bộ: nới `KC_HOSTNAME`/cho http, hay giả lập? | Giả lập bằng Playwright route (https giữ nguyên) | Không đổi cấu hình production; kiểm đúng issuer/redirect thật. |
| 16/10/2026 | PostgREST GET chạy trong transaction `READ ONLY`; pre-request có ghi ngữ cảnh vào `xt_meta.session_ctx` qua `xt.set_context` được không? | **Không.** Pre-request ghi ngữ cảnh vào GUC cục bộ transaction `xt.pgrst_ctx`; `xt.cur()` thêm **một nhánh** chỉ đọc GUC này khi `session_user = 'xt_authenticator'` (migration 0004, xem PG-1) | Đã chạy thật trên PG 16 cục bộ (`initdb` tạm, đã xóa): `INSERT` vào bảng unlogged qua hàm SECURITY DEFINER trong `BEGIN READ ONLY` → `cannot execute INSERT in a read-only transaction`; `CREATE TEMP TABLE` cũng bị chặn; chỉ `set_config(…, true)` chạy được. Các session khác giữ nguyên hành vi đã audit. |
| 16/10/2026 | Role PostgREST: tạo ở đâu, quyền gì? | `xt_authenticator` LOGIN NOINHERIT, chỉ là thành viên `xt_web` (`SET TRUE, INHERIT FALSE`); mật khẩu từ secret ngoài repo; tạo bằng bước superuser riêng (PG-2) | `xt_migrator` không có CREATEROLE. Không cấp `xt_app`/`xt_workflow`/`xt_api_owner` cho `xt_authenticator`/`xt_web` (SMT §5.2). Claim role trỏ tới role khác → SET ROLE thất bại. |
| 16/10/2026 | `db-schemas` động: `ALTER ROLE … SET pgrst.db_schemas` (spike) hay `db-pre-config`? | `db-pre-config = xt.pgrst_config` (SECURITY DEFINER, suy ra từ `xt_meta.tenant` + schema thật), schema đầu là placeholder rỗng `xt_api_none` (PG-3) | Không giữ trạng thái thừa, migrator không cần quyền ALTER ROLE; registry rỗng vẫn khởi động được; thiếu `Accept-Profile` rơi vào schema rỗng và bị pre-request từ chối. |
| 16/10/2026 | Hook sau khi tạo/xóa tenant, tạo/xóa model | `pg_notify('pgrst','reload config')` + `'reload schema'` **trong cùng transaction** của `provision_tenant`/`drop_tenant`; model đã có `reload schema` trong `PgMigrator.apply` (PG-4) | NOTIFY chỉ phát khi commit → rollback không gây tác dụng phụ; không có listener thì là no-op ⇒ không đổi hành vi đã audit. |
| 16/10/2026 | DB đã bootstrap áp phần PostgREST thế nào? | Migration cộng thêm `sql/migrations/0004_postgrest.sql` (xt_migrator) + hàm superuser idempotent `install_postgrest_role`; **không** đặt vào `bootstrap.sql`/loader bootstrap (PG-5) | `bootstrap()` trả False khi đã có `xt_meta.platform` nên SQL nạp kèm bootstrap không bao giờ chạy trên DB cũ. |

## Chi tiết PostgREST runtime (16/10/2026, agent decision)

Phạm vi: Tầng 1 "Data" (`/data`, PostgREST chỉ đọc, `aud = xt-data`). Căn cứ: architecture-v2 §(c), SMT §5, `docs/identity.md`, `bootstrap.sql`, `tenancy.py`, `migrator.py`, `migrations.py`. Mọi mục dưới đây là **quyết định thiết kế, chưa triển khai, chưa có test HTTP** trừ khi ghi rõ "đã chạy thật".

### PG-1. Kênh ngữ cảnh cho PostgREST (nhánh GUC trong `xt.cur`)

- **Sự thật đã chạy thật** (PG 16.x cục bộ, không Docker, thư mục tạm đã xóa): trong `BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY`, hàm SECURITY DEFINER `INSERT … ON CONFLICT` vào bảng UNLOGGED báo `ERROR: cannot execute INSERT in a read-only transaction`; `CREATE TEMP TABLE` báo `cannot execute CREATE TABLE in a read-only transaction`; `pg_current_xact_id()` và `set_config('x','1',true)` chạy được. PostgREST chạy GET/HEAD trong transaction read-only, nên ghi chú cũ "pre-request gọi `xt.set_context`/ghi `session_ctx`" **không khả thi**. Chưa chạy lại trên PG 17 + PostgREST thật: test tích hợp phải xác nhận.
- **Quyết định:** `xt.pgrst_pre()` (SECURITY DEFINER, chủ `xt_migrator`, `SET search_path = pg_catalog, pg_temp`, EXECUTE chỉ cho `xt_web`) đặt `set_config('xt.pgrst_ctx', <jsonb>, true)` gồm `t` (tenant id), `a` (actor), `r` (roles), `tm` (teams, hiện `{}`), `d` (departments). `xt.cur()` được `CREATE OR REPLACE` trong migration 0004 với đúng hai nhánh cũ **giữ nguyên văn**, cộng nhánh thứ ba: `WHERE session_user = 'xt_authenticator'` đọc `current_setting('xt.pgrst_ctx', true)`. Nhánh `session_ctx` thêm điều kiện `session_user <> 'xt_authenticator'`. Mọi session khác (xt_app, BI login, xt_migrator) cho kết quả y hệt bản đã audit.
- **Pre-request kiểm theo thứ tự, lỗi nào cũng `RAISE … USING ERRCODE = '42501'`, thông điệp cố định, không lộ giá trị claim:**
  1. `current_user = 'xt_web'`.
  2. `request.jwt.claims` có; `typ = 'Bearer'`; `sub` khác rỗng; `aud` (chuỗi hoặc mảng) chứa `xt-data` (lặp lại `jwt-aud` để phòng cấu hình sai); `iss` = `xt_meta.platform.value` với key `pgrst_issuer` (thiếu dòng → từ chối, fail-closed). PostgREST **không** kiểm `iss`, nên bước này bắt buộc.
  3. `azp` thuộc `xt_meta.pgrst_client(client_id)` (allowlist, ghi bằng migrator, giống N2/D9). Không có dòng → từ chối.
  4. Claim Organization (`organization`, dạng list alias hoặc object `{alias:{…}}`, cùng cách đọc với `access_token._organizations`) có **đúng một** alias; tra `xt_meta.tenant_profile.alias` → `(seq, id)`.
  5. Profile: `request.method` ∈ {GET, HEAD}; `request.headers->>'accept-profile'` **bắt buộc có** và bằng `'api_t' || seq`; `current_schema()` cũng phải bằng giá trị đó (chặn cả trường hợp không gửi header). Method khác → từ chối (APISIX cũng chỉ cho GET/HEAD).
  6. Actor: `xt_meta.person` với `tenant_id = id`, `subject = sub`, `active` → `actor = person.id`, `roles = person.roles` (mỗi role khớp `^[a-z][a-z0-9_]{0,62}$` như `set_context`), `departments = ARRAY[unit]` nếu `unit` khác NULL. Không có person → từ chối. Token client credentials của nhà tích hợp phải có person tương ứng (subject = `sub` service account), vai trò do xT cấp, không lấy từ token.
- **Rủi ro chấp nhận (ghi cho review):** ai có mật khẩu `xt_authenticator` và kết nối SQL trực tiếp thì đặt được `request.jwt.claims`/`xt.pgrst_ctx` và đọc view `api_t*` của mọi tenant (field sensitive vẫn không có trong view). Đây là ranh giới tin cậy cố hữu của PostgREST; HMAC trên GUC không giải được vì kẻ tấn công gọi lại chính `xt.pgrst_pre`. Giảm thiểu: mật khẩu ở secret ngoài repo, `pg_hba` chỉ cho IP/container PostgREST, `CONNECTION LIMIT`, không cấp gì khác cho role này.
- **Lý do không chọn phương án khác:** (a) ghi `session_ctx` — đã chứng minh lỗi read-only; (b) mỗi tenant một PostgREST với login gắn qua `login_tenant` — 200 tiến trình, trái D6; (c) để `xt.cur` đọc thẳng `request.jwt.claims` — phải tra tổ chức trong hàm STABLE gọi theo từng dòng, chậm và không có điểm chặn profile.

### PG-2. Role và quyền

- `CREATE ROLE xt_authenticator LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION CONNECTION LIMIT 20`; `GRANT xt_web TO xt_authenticator WITH INHERIT FALSE, SET TRUE`; `GRANT CONNECT ON DATABASE … TO xt_authenticator`; `ALTER ROLE xt_authenticator PASSWORD <secret>` (giá trị từ `~/.config/xt/` hoặc Compose secret, truyền qua `sql.Literal` như `bootstrap()`, không log).
- Bất biến kiểm bằng test: `pg_has_role('xt_authenticator', r, 'MEMBER')` chỉ đúng với `xt_web`; `xt_web` không là thành viên role nào; không role nào ngoài code xT là thành viên `xt_app`.
- PostgREST: `jwt-role-claim-key = ."xt_pgrst_role"`; Keycloak client scope `xt-data` có hardcoded-claim mapper `xt_pgrst_role = xt_web` và audience mapper `included.custom.audience = xt-data`; scope này chỉ gán cho `xt-desk` và client nhà tích hợp, **không** gán cho client MCP/DCR. **Không đặt `db-anon-role`** (không token → 401). Claim role bị sửa sẽ làm hỏng chữ ký; claim role hợp lệ nhưng khác `xt_web` → SET ROLE thất bại vì không là thành viên.
- Hàm: không có hàm nào trong `api_t*`/`xt_api_none` ⇒ không có RPC. `xt.pgrst_pre` EXECUTE cho `xt_web`; `xt.pgrst_config` EXECUTE cho `xt_authenticator`; cả hai `REVOKE ALL … FROM PUBLIC`. `db-extra-search-path = ''`.

### PG-3. `db-schemas` động

- `db-config = true`, `db-pre-config = xt.pgrst_config`. Hàm (SECURITY DEFINER, chủ `xt_migrator`) đặt `set_config('pgrst.db_schemas', 'xt_api_none' || coalesce(',' || string_agg('api_t' || seq ORDER BY seq), ''), true)` chỉ với tenant có schema `api_t<seq>` thật trong `pg_namespace`.
- `xt_api_none`: schema rỗng do migration tạo, chủ `xt_api_owner`, `REVOKE ALL FROM PUBLIC`, `GRANT USAGE TO xt_web`. Là profile mặc định nên request thiếu `Accept-Profile` không đọc được gì và bị pre-request từ chối.
- Env `PGRST_DB_SCHEMAS=xt_api_none` làm giá trị nền. Thứ tự ưu tiên env ↔ in-db config của bản PostgREST được pin **[cần kiểm chứng bằng test reload]**.
- Trần ~200 tenant/CSDL (SMT §3) ⇒ chuỗi < 2 KB.

### PG-4. Hook (root triển khai, không đổi hành vi)

- `tenancy.py`: thêm hàm `notify_postgrest(conn, *, config: bool) -> None` chạy `SELECT pg_notify('pgrst', 'reload config')` (khi `config`) rồi `pg_notify('pgrst', 'reload schema')`. Gọi **bên trong** `with migrator.transaction()` ở cuối `provision_tenant` và `drop_tenant` với `config=True`. Kênh `pgrst` = `db-channel` mặc định.
- Model tạo/sửa/xóa: `PgMigrator.apply` đã gửi `reload schema` + `xt_catalog` trong transaction (`migrator.py`). Không cần `reload config` vì danh sách schema không đổi. Root cần xác nhận không có bước tạo/xóa view nào chạy ở pha `post/concurrent` (view `order=95/96` phải nằm trong pha `tx`); nếu có thì `run_jobs` cũng phải gửi `reload schema` sau commit.
- Migration 0004 kết thúc bằng hai `pg_notify` tương tự.

### PG-5. Áp lên DB đã bootstrap

1. Superuser: `install_postgrest_role(superuser, *, authenticator_password: str, issuer: str) -> None` (idempotent, hàm mới trong `tenancy.py`, không đổi chữ ký `bootstrap()`): tạo/`ALTER` role theo PG-2, `INSERT … ON CONFLICT DO UPDATE` `xt_meta.platform('pgrst_issuer', issuer)`.
2. `xt db migrate` (xt_migrator) áp `0004_postgrest.sql`: kiểm `xt_authenticator` tồn tại (không có → `RAISE` rõ ràng, transaction rollback, ledger không ghi); tạo `xt_api_none`, `xt_meta.pgrst_client`, `xt.pgrst_pre`, `xt.pgrst_config`, `CREATE OR REPLACE xt.cur` (PG-1); grant; notify.
3. Thứ tự cài mới: `bootstrap` → `install_postgrest_role` → `migrate` → `provision_tenant`. Tenant tạo trước 0004 vẫn dùng được vì schema list suy ra động.
- File `sql/postgrest.sql` nạp kèm loader bootstrap **không dùng**: DB cũ không bao giờ chạy tới.

### Học từ nguồn tham khảo
- REA (`AGENTS.md`/`CONTRIBUTING.md`): nguyên tắc tích hợp fail-closed, lỗi mang lý do ổn định, che credential khi ghi log → áp vào pre-request (lỗi 42501 cố định, không lộ claim) và vào hàm cài role (không log mật khẩu).
- sklite: bản giải nén `/tmp/sklite_src` không còn trên máy lúc ra quyết định; không đọc, không chép (repo không có LICENSE).

## Vai trò `xt_migrator` với audit (16/10/2026, chủ dự án – Opus 5.5)

- **Câu hỏi:** `xt_migrator` có được coi là tin cậy với audit không? (audit Model Engine vòng 2, R2-01/R2-04)
- **Quyết định:** **Tin cậy để đổi schema, KHÔNG tin cậy với audit.** (1) Tách role chủ sở hữu audit `xt_audit_owner` (sở hữu bảng/hàm/trigger audit); `xt_migrator` không sửa, tắt hay gọi được hàm ghi audit. (2) Khóa HMAC chỉ đưa vào transaction có ghi field sensitive, không đưa vào mọi `tenant_tx`. (3) Định kỳ neo đầu chuỗi audit ra ngoài DB bằng checkpoint ký Ed25519, lưu ngoài Postgres, để DBA hay role đặc quyền sửa audit đều bị phát hiện.
- **Lý do:** Nguyên tắc phân tách nhiệm vụ: người đổi cấu trúc không được đồng thời kiểm soát bằng chứng kiểm toán. Neo hash ra ngoài đúng thiết kế đã nêu trong kiến trúc v2 và nguyên lý (iii) của đề.
| 16/10/2026 | libpq cho psycopg trong ảnh xt: system libpq hay `psycopg[binary]`? | **`libpq5` của Debian trong ảnh slim** (`apt --no-install-recommends`), giữ psycopg thuần Python | Khớp `THIRD_PARTY_NOTICES.md` (không dùng wheel `psycopg-binary`, không vendor binary LGPL); libpq được vá bảo mật qua base image. Ảnh: uid 10001, không home/shell, `PYTHONDONTWRITEBYTECODE`, compose `read_only` + tmpfs `/tmp` + `cap_drop ALL`. |
| 16/10/2026 | Access token MCP mang aud `account`; có nên bỏ `xt-api-introspect`? | Giữ `xt-api-introspect` (bắt buộc: Keycloak 26 trả `active:false` khi client introspect không nằm trong aud, `identity.md`); bỏ `account` bằng cách gỡ mapper `audience resolve` khỏi client scope `roles` (toàn realm) | aud còn đúng `[resource MCP, xt-api-introspect]`. Không client nào của xT dựa vào aud `account`/`realm-management` (Admin REST kiểm role, không kiểm aud; smoke registry vẫn PASS). Không cần bộ scope riêng từng client. |
| 16/10/2026 | `XT_MCP_HUMAN_CLIENTS` trong compose | xt-api nhận `XT_CONFIRM_CLIENT_ID`; biến cùng tên trong `.env` (xt-desk) chỉ dành cho provision | Composition từ chối human client khác client xác nhận (M6). |
| 16/10/2026 | Ai khởi tạo DB (role, schema, hàng đợi) cho xt-api/worker? | Service một lần `xt-init` (`deploy/scripts/init_db.py`, idempotent); xt-api/worker `depends_on: service_completed_successfully`; khóa tệp sinh bởi `make env` ở `~/.config/xt/app` (0644/0755 vì container uid 10001; thư mục cha 0700) | Không cần bước tay; thiếu biến thì `compose` báo `${VAR:?}` hoặc tiến trình thoát với danh sách lỗi; `restart: on-failure:5` chặn crash-loop vô hạn. |
