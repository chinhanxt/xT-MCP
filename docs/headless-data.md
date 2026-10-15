<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Dịch vụ dữ liệu headless (`/data`, PostgREST) và demo Tầng 1

Tầng 1 của đề thi gồm 4 dịch vụ: SSO, API Gateway, Data, Workflow. Tài liệu này mô tả đường `/data` (PostgREST đọc view `api_t<seq>` của từng tenant) và cách chạy `docs/headless-demo.sh` để gọi cả 4 dịch vụ bằng `curl`, không qua MCP. Nguồn sự thật là `src/xt/adapters/db/sql/postgrest.sql`, `tenancy.py` và `data_setup.py`. Hợp đồng API đã đổi: `docs/integration-notes.md`, quyết định PG-1…PG-7 trong `docs/decisions.md`.

## Trạng thái triển khai (đọc trước)

Mục nào chưa có bằng chứng chạy thật thì ghi rõ, không suy diễn.

| Thành phần | Trạng thái |
|---|---|
| Role `xt_web`, schema `api_t<seq>`, view không chứa field `sensitive`, RLS `FORCE` | Có (`bootstrap.sql`, `tenancy.py`, `compiler.py`) |
| Lớp PostgREST: role `xt_authenticator`, schema rỗng `xt_api_none`, bảng `xt_meta.pgrst_client`, `xt.pgrst_pre()`, `xt.pgrst_gate(text, text)`, `xt.pgrst_config()`, `xt.set_context(jsonb)`, `xt.backend_cur()` + wrapper `xt.cur()` | Có trong `postgrest.sql` (idempotent). `bootstrap()` nạp sẵn; DB đã bootstrap cài bằng `tenancy.install_postgrest_role(...)`. **Không có migration `0004_postgrest.sql`** (`migrator` không có `CREATE ROLE`) |
| Đọc `GET`/`HEAD` trong transaction `READ ONLY` | Đã kiểm qua PostgREST thật trên PostgreSQL 17 bằng JWT khóa test; xem "Kết quả chạy thật". Không suy ra trạng thái triển khai công khai |
| Client scope Keycloak `xt-data` | Có `deploy/keycloak/provision_data.py` (chưa chạy trên Keycloak thật trong phiên này) |
| Service `postgrest` trong `deploy/compose.yaml` | Có khai báo (ảnh ghim digest, `read_only`, user `65534:65534`, JWKS qua `@file`); fixture cùng digest đã chạy trên PostgREST thật; chưa áp Compose vào stack công khai |
| HTTPS công khai 4 hostname | **Không khả dụng**: cả 4 trả HTTP 530 / `error code: 1033` (mục "Hiện trạng công khai") |
| Route APISIX `/data` | Có cấu hình upstream `postgrest:3000`, bỏ tiền tố `/data`, giữ bearer/profile. Chữ ký JWT được kiểm ở PostgREST; chưa xác nhận trên gateway công khai |
| `/workflow/v1` trong `xt-api` | **Chưa có** route HTTP: `http_app.py` chỉ có `/mcp`, PRM, `/healthz`; APISIX chuyển `/workflow` tới `xt-api:8000` nên hiện nhận 404 |

Khi dịch vụ chưa có, demo in `chưa triển khai` và thoát mã 2. Demo không bao giờ in thành công cho bước không nhận được HTTP 2xx thật.

## Cách hoạt động

```
client --Bearer(aud=xt-data, azp∈allowlist)--> APISIX --> PostgREST (login xt_authenticator)
   PostgREST: kiểm chữ ký JWKS + aud + exp; SET LOCAL ROLE xt_web (từ claim data_role)
   db-pre-request xt.pgrst_pre() -> xt.pgrst_gate(): iss, azp, organization, Accept-Profile,
        xt_meta.person (sub) -> xt.set_context(jsonb)  (GUC xt.pgrst_ctx, cục bộ transaction)
   SELECT trên view api_t<seq> (security_invoker=false) lọc theo RLS qua xt.cur()
```

Cấu hình PostgREST (đúng như `deploy/compose.yaml`): `db-uri` user `xt_authenticator`; `db-config=true`; `db-pre-config=xt.pgrst_config`; `db-pre-request=xt.pgrst_pre`; `db-schemas=xt_api_none` (giá trị khởi đầu, pre-config ghi đè thành `xt_api_none` + các `api_t<seq>` thật); `db-extra-search-path=""`; `jwt-aud=xt-data`; `jwt-role-claim-key=.data_role`; `openapi-mode=disabled`; `jwt-secret=@/run/secrets/pgrst/jwks.json`. **Không đặt `db-anon-role`**: không token thì PostgREST trả 401 (`PGRST302`).

### Role

| Role | Vai trò |
|---|---|
| `xt_authenticator` | Login của PostgREST. `LOGIN NOINHERIT`, chỉ là thành viên `xt_web` (`ADMIN FALSE, INHERIT FALSE, SET TRUE`); không là thành viên `xt_app`, `xt_workflow`, `xt_migrator`, `xt_api_owner`. Mật khẩu từ secret ngoài repo |
| `xt_web` | Role JWT duy nhất. Chỉ `SELECT` trên view `api_t<seq>`; không gọi được `xt.set_context`, `xt.backend_cur()` hoặc gate; chỉ pre-request `xt.pgrst_pre()` được cấp `EXECUTE` trong các hàm PostgREST mới |
| `xt_api_none` | Không phải role mà là schema rỗng đứng đầu `db-schemas`: request thiếu `Accept-Profile` rơi vào đây; schema không có view nên không trả dữ liệu |

Cài lại chuẩn hóa thuộc tính của cả `xt_authenticator` và `xt_web`, thu toàn bộ membership đã cấp cho hai role theo từng grantor (`REVOKE … GRANTED BY … CASCADE`, [PostgreSQL 17](https://www.postgresql.org/docs/17/sql-revoke.html)), rồi chỉ cấp lại `xt_web` cho authenticator. `xt_web` luôn `NOLOGIN NOINHERIT NOBYPASSRLS`, không có quyền tạo role/database.

`xt_authenticator` chỉ được `SET ROLE xt_web`, nên dù claim role bị giả thành giá trị khác, PostgREST không leo được sang role mạnh hơn. Role không bao giờ lấy từ role realm của người dùng: Keycloak gắn claim hằng số `data_role = xt_web` cho scope `xt-data`.

## Yêu cầu Keycloak (thật, không mock)

| Yêu cầu | Giá trị | Ghi chú |
|---|---|---|
| Issuer (`iss`) | `https://<XT_DOMAIN_AUTH>/realms/<XT_REALM>` (mặc định `https://xt-auth.freepro.online/realms/xt`); in bằng `make -C deploy pgrst-issuer` | PostgREST **không** kiểm `iss`; `xt.pgrst_gate` so với `xt_meta.platform['pgrst_issuer']` (ghi bởi `install_postgrest_role`). Thiếu cấu hình thì từ chối (`hint=issuer`) |
| Audience (`aud`) | `xt-data` | PostgREST kiểm `jwt-aud`, gate kiểm lại. Token MCP không dùng được. Scope **optional** `xt-data` (không mặc định cho realm, không gán DCR), tạo bằng `provision_data.py` cho đúng các client trong `XT_DATA_CLIENTS` (không được chứa client MCP/Desk/IdP). Client headless chuẩn: `xt-cli` (public, auth-code + PKCE S256, loopback `127.0.0.1`; không secret, không client_credentials; xem `docs/decisions.md`). Xin token: `scope=openid xt-data`; token MCP (`mcp-xt`) không mang aud này |
| Client (`azp`) | Thuộc allowlist `xt_meta.pgrst_client` | Danh sách này **phải trùng** `XT_DATA_CLIENTS`; lệch thì `hint=client_not_allowed` |
| Loại token | `typ=Bearer` | Refresh/ID token bị từ chối (`hint=token_type`) |
| Tenant | claim `organization` (alias; list, chuỗi, hoặc object `{alias: {...}}`) | Đúng một alias, đã đăng ký trong `xt_meta.tenant_profile`. 0 alias: `missing_tenant`; nhiều: `tenant_ambiguous`; lạ: `unknown_tenant` |
| Role DB | claim `data_role = xt_web` | Mapper hằng số của scope `xt-data` |
| Chữ ký | RS256/ES256 qua JWKS realm | Không chấp nhận `alg: none`/HS256 |
| Tuổi token | Ngắn (≤ 5 phút) | Lấy token mới mỗi lần demo |

**Quyền nghiệp vụ không lấy từ token.** Gate tra `xt_meta.person` (`tenant_id` từ alias, `subject = sub`, `active`); vai trò (`roles`) và phòng (`unit` -> `department_ids`) lấy từ bảng này, không từ `realm_access.roles`, `roles` hay `team_ids` (`identity.md`). `sub` chưa đăng ký hoặc không hoạt động: `hint=invalid_behalf`. Hiện `team_ids` luôn rỗng. Bảng `agent_grant` chưa được gate dùng.

Gate từ chối bằng `403` với mã Postgres `42501`; `hint` là mã lý do ổn định, không chứa giá trị claim. PostgREST có thể từ chối trước gate bằng `401` (JWT), `404` (view/RPC không có) hoặc `406` (schema không được expose).

## Accept-Profile

Mỗi tenant có schema riêng `api_t<seq>` (seq do `xt tenant create` in ra). Client **bắt buộc** gửi:

```
GET /data/<model>      Accept-Profile: api_t<seq>
```

- Profile thuộc tenant khác nhưng đang được expose: `403` (`hint=profile_mismatch`). Thiếu `Accept-Profile`: schema mặc định là `xt_api_none`; `GET /ticket` trả `404` (`PGRST205`, view không có trong schema cache) trước pre-request, còn request đi tới gate bị `403` (`profile_mismatch`). Không có nhánh nào tự chọn schema tenant.
- Profile chưa có (tenant chưa tạo/chưa reload) hoặc đã xóa: `406` (`PGRST106`). Model chưa tạo hoặc đã xóa trong profile hợp lệ: `404`. Các schema `xt`, `xt_meta`, `xt_audit`, `public` không nằm trong `db-schemas` nên cũng `406`.
- `/data` **chỉ đọc**: APISIX chỉ route `GET`/`HEAD`; các method ghi không khớp route. Gọi trực tiếp PostgREST trên view hợp lệ với `Content-Profile` đúng trả `403` (`hint=read_only_api`); profile sai bị chặn trước hoặc ở gate. `/rpc/*` không có hàm trong schema API (`404`); gate cũng từ chối nếu request RPC tới được pre-request (`hint=rpc_disabled`).
- RLS là lớp thứ hai: ngữ cảnh tenant A đọc thẳng `api_t<B>` được 0 dòng (spike t5b).
- Field do workflow quản lý (`managed_by: workflow`) không ghi được từ `/data`; `xt_web` không có quyền ghi và không là thành viên `xt_app`.

### Dữ liệu cá nhân trong view

View `api_t<seq>` (`compiler.py`: `security_invoker=false`, chủ `xt_api_owner`) **loại bỏ hoàn toàn** cột `pii: sensitive`, nên không có cột đó dưới mọi dạng trong schema API. Audit lưu giá trị `sensitive` dưới dạng HMAC do app cấp; khóa HMAC không nằm trong DB. Tài liệu này **không** tuyên bố view công khai chứa HMAC thô. Field `pii: basic` được giữ trong view dưới dạng **văn bản thuần** theo chính sách hiện hành (`model-engine.md`: `basic` lưu nguyên văn, đổi ở v0.3); ai đọc được tenant đó thấy giá trị thật của các field này, nên gắn nhãn `sensitive` cho dữ liệu cần ẩn. Kiểm bằng test, không chỉ bằng tài liệu.

## Điểm nối READ ONLY

PostgREST chạy `GET`/`HEAD` trong transaction `READ ONLY` ([tài liệu transaction](https://docs.postgrest.org/en/stable/references/transactions.html)). `xt.set_context` (8 tham số, đã audit) ghi bảng UNLOGGED `xt_meta.session_ctx` nên sẽ lỗi `25006` ở mọi `GET`. Giải pháp là phần mở rộng API cộng thêm trong `postgrest.sql`, không sửa thân hàm đã audit:

| Hàm | Thực tế |
|---|---|
| `xt.set_context(8 tham số)` | Giữ nguyên, không đụng |
| `xt.backend_cur()` | Bản sao đúng thân `xt.cur()` gốc (tạo một lần từ `pg_get_functiondef`, chỉ đổi tên); không ai gọi trực tiếp |
| `xt.cur()` | `CREATE OR REPLACE` thành wrapper: giữ OID và ACL cũ (kể cả login BI). `session_user <> xt_authenticator` thì trả đúng `xt.backend_cur()`; `xt_authenticator` thì đọc GUC `xt.pgrst_ctx` cục bộ transaction (rỗng thì trả ngữ cảnh NULL, fail-closed) |
| `xt.set_context(jsonb)` | Overload riêng, chỉ chạy khi `session_user = xt_authenticator`, `set_config('xt.pgrst_ctx', …, true)`; `EXECUTE` không cấp cho `PUBLIC`/`xt_web`; chỉ `xt.pgrst_gate` gọi |
| `xt.pgrst_pre()` | `SECURITY DEFINER` tối giản, mọi tên đủ schema, không đặt `search_path` để thấy schema của request; đọc profile từ phần tử đầu của GUC `search_path` do PostgREST đặt, rồi gọi gate với profile và GUC `role` |
| `xt.pgrst_gate(text, text)` | `SECURITY DEFINER` chủ `xt_migrator`, `search_path = pg_catalog, pg_temp`; tham số: profile và role hiện tại |

Sau lần cài đầu tiên phải khởi động lại PostgREST. Đường `GET`/`HEAD` đã được kiểm trên fixture PostgREST thật; kết quả loopback không thay cho kiểm chứng gateway và Keycloak production.

## Cài đặt

Bí mật nằm ngoài repo (`~/.config/xt/`, `deploy/.env` do `make env` sinh). `xt-init` do integrator sở hữu, hiện chưa gọi installer PostgREST; cần chạy bước quản trị dưới đây trên DB đã bootstrap. Không đưa kết nối quản trị vào service PostgREST.

1. Tạo cấu hình theo `docs/deploy.md`: `make -C deploy env render`. Bootstrap DB bằng luồng triển khai của dự án; nếu đang nâng DB đã bootstrap thì giữ nguyên dữ liệu.
2. Đặt `XT_DATA_SUPERUSER_URL` trong môi trường từ secret (không truyền DSN hoặc mật khẩu qua argv). Điền `XT_DATA_CLIENTS` và `XT_PGRST_PASSWORD` trong `deploy/.env`, rồi chạy:

```bash
make -C deploy pgrst-db
```

Lệnh gọi `install_data_prerequisites` và `install_postgrest_role`: cài API audit metadata (superuser), cài migration nền, chạy lại phần cộng thêm `postgrest.sql`, đặt mật khẩu authenticator, ghi issuer và **thay toàn bộ** allowlist client. Thiếu issuer/client hoặc người dùng chưa đăng ký thì fail-closed. Quyền quản trị chỉ được dùng cho bước cài đặt; xem mục "Cài schema nền bằng đường quản trị" và PG-7.

3. Keycloak: `make -C deploy provision-realm`, rồi `make -C deploy provision-data`. `XT_DATA_CLIENTS` phải giống allowlist DB; issuer mặc định được dựng từ `XT_DOMAIN_AUTH` và `XT_REALM` (in bằng `make -s -C deploy pgrst-issuer`).
4. JWKS: `make -C deploy pgrst-jwks` ghi `${XT_SECRETS_DIR:-~/.config/xt}/pgrst/jwks.json`, rồi khởi động `postgrest` trong compose. Trước khi chạy Docker, kiểm `free -g`: available phải ≥ 4 GB; chỉ một stack nặng tại một thời điểm.
5. Tenant: `xt tenant create --alias <alias> --name … --timezone … --llm-egress masked`, đăng ký người (`xt_meta.person`) và cài gói nghiệp vụ. Tạo/xóa tenant hoặc model tự phát `reload config` + `reload schema` sau commit.
6. APISIX: render rồi nạp cấu hình route `/data` chỉ cho `GET`/`HEAD` (không expose `OPTIONS`); bỏ tiền tố, giữ `Authorization`, `Accept-Profile`, `Content-Profile`. Xác thực JWT thực hiện ở PostgREST và gate SQL.
7. Chạy demo (mục dưới). Không suy cấu hình đã render thành dịch vụ đã chạy.

Kiểm tra:

```bash
make check-data              # ruff, mypy, lint-imports, bash -n demo, reuse lint (SPDX)
make test-data-integration   # cần Docker: 1 Postgres + 1 PostgREST tạm, tự dọn
make check                   # kiểm tra toàn bộ repository; không bao gồm integration Docker
```

## Xoay khóa JWKS

PostgREST đọc JWKS từ tệp (`@/run/secrets/pgrst/jwks.json`, bind chỉ đọc, container `read_only` chạy uid `65534`); không tự tải qua mạng. Ảnh PostgREST ghim digest `sha256:d155c6718e…` (v16.4 trong `compose.yaml`).

1. `make -C deploy pgrst-jwks` (`python3 scripts/fetch_jwks.py`): chỉ tin `--base`, discovery phải khai đúng `issuer` và `jwks_uri` dạng `/realms/<realm>/protocol/openid-connect/certs`; chỉ nhận khóa `RS256`/`ES256`, từ chối khóa riêng. Lỗi bất kỳ: thoát mã 1 và **không** đụng tệp cũ; ghi nguyên tử; từ chối đích nằm trong repo. `issuer` phải trùng `xt_meta.platform['pgrst_issuer']`.
2. Báo PostgREST nạp lại: `docker compose kill -s USR2 postgrest` (không restart).
3. Khi Keycloak xoay khóa, giữ khóa cũ và mới trong JWKS suốt thời gian sống tối đa của access token rồi mới bỏ khóa cũ.

Kiểm chứng: token ký khóa đã bỏ phải ra `401`; token khóa mới ra `200`.

## Reload động

Khi tạo hoặc xóa tenant/model, schema `api_t<seq>` đổi. `xt.adapters.db.postgrest.notify_postgrest(conn)` phát trong cùng transaction với DDL (chỉ gửi khi commit; rollback không đổi cache):

```sql
SELECT pg_notify('pgrst', 'reload config');   -- db-schemas từ xt.pgrst_config()
SELECT pg_notify('pgrst', 'reload schema');   -- cache schema/quan hệ
```

- [Hook `db-pre-config`](https://docs.postgrest.org/en/stable/references/configuration.html#db-pre-config) gọi `xt.pgrst_config()`; danh sách schema được suy ra từ `xt_meta.tenant` và schema thật có chủ `xt_api_owner`; chưa có tenant thì chỉ còn `xt_api_none`.
- Spike đo: thêm tenant lúc chạy, trước reload `406`, sau reload `200` (~3 s); nạp lại cache ~105 ms cho 1.470 view.
- Sau `drop_tenant`, profile cũ phải trả `406` sau reload.

## Hiện trạng công khai (curl thật, 16/10/2026)

`curl -sS https://<host>.freepro.online/` cả 4 hostname Cloudflare Tunnel đều trả:

| Hostname | HTTP | Thân |
|---|---|---|
| `xt-auth.freepro.online` | 530 | `error code: 1033` |
| `xt-api.freepro.online` | 530 | `error code: 1033` |
| `xt-mcp.freepro.online` | 530 | `error code: 1033` |
| `xt.freepro.online` | 530 | `error code: 1033` |

Cloudflare 1033 nghĩa là tunnel không có connector đang chạy. **Không khả dụng**, chưa triển khai; không có bước nào ở đây là xanh. Demo phân loại 530/1033 là `chưa triển khai`.

## Cài schema nền bằng đường quản trị

`xt-init` hiện chưa gọi installer PostgREST. Sau bootstrap, dùng script đã có để cài migration nền và cấu hình role/issuer/allowlist:

```bash
# Nạp DSN quản trị từ secret vào XT_DATA_SUPERUSER_URL; không truyền DSN qua argv.
# deploy/.env: XT_DATA_CLIENTS=<client đã duyệt>, XT_PGRST_PASSWORD, XT_DOMAIN_AUTH/XT_REALM
make -C deploy pgrst-db
make -C deploy provision-data
make -C deploy pgrst-jwks
```

`pgrst-db` gọi `install_data_prerequisites` trên một transaction quản trị: bước superuser chạy `install_meta_audit()` (idempotent: `xt_audit.install_meta` SECURITY DEFINER thuộc `xt_audit_owner` + event trigger bảo vệ), rồi `SET LOCAL ROLE xt_migrator` chạy migration nguyên bản; 0006 gắn audit `xt_meta.*` qua `install_meta`. **Không cấp/thu quyền audit nào cho xt_migrator**, kể cả tạm thời (R2-04: migrator không được tin cậy với audit, `docs/decisions.md`). `xt_migrator` không bao giờ có EXECUTE `xt_audit.log()`/`append()`; ACL hai hàm này trước/sau cài giống nhau. Hồi quy: `test_data_setup_never_grants_migrator_audit_write` probe `has_function_privilege` trước, trong transaction (trước/sau migration, gọi thử `append` → 42501) và sau commit. Phạm vi và giới hạn: docs/integration-notes.md, mục API cài schema nền cho data. Không đưa DSN quản trị vào PostgREST.

## Demo `docs/headless-demo.sh`

Gọi tuần tự: (1) SSO discovery + JWKS, (2) gateway: `/data` không token phải bị từ chối, (3) `/data/<model>` với `Accept-Profile`, (4) `/workflow/v1/definitions`. Cả 4 bước **luôn** gọi curl thật; thiếu token/model/profile thì thăm dò không token và báo `BỎ QUA (thiếu …)`, không bao giờ là `OK`.

Biến môi trường (script không chứa bí mật):

| Biến | Ý nghĩa |
|---|---|
| `XT_AUTH_BASE`, `XT_API_BASE`, `XT_REALM` | Mặc định `https://xt-auth.freepro.online`, `https://xt-api.freepro.online`, `xt` |
| `XT_ACCESS_TOKEN_FILE` | Tệp chứa access token data (`aud` = `xt-data`); nên dùng thay `XT_ACCESS_TOKEN`. Chỉ gửi tới `$XT_API_BASE$XT_DATA_PREFIX`, không gửi sang SSO hay workflow |
| `XT_WORKFLOW_TOKEN_FILE` (hoặc `XT_WORKFLOW_TOKEN`), `XT_WORKFLOW_BASE` | Token riêng cho workflow (audience của workflow, không phải `xt-data`). `XT_WORKFLOW_BASE` mặc định `XT_API_BASE`. Thiếu token: thăm dò không token, báo `BỎ QUA (thiếu token workflow)` |
| `XT_DATA_MODEL`, `XT_DATA_PROFILE` | Model cần đọc và schema `api_t<seq>`; thiếu thì bỏ qua bước 3 |
| `XT_DATA_FOREIGN_PROFILE` | Tùy chọn: schema tenant khác. Chỉ `401`/`403`/`406` tính là từ chối; `2xx` là lỗi cô lập; 5xx/timeout/404 là `không kết luận` (lỗi), không phải bằng chứng bảo mật |
| `XT_DATA_PREFIX`, `XT_WORKFLOW_PATH` | Mặc định `/data`, `/workflow/v1/definitions` |
| `XT_WORKFLOW_START_FILE`, `XT_WORKFLOW_APPROVER_TOKEN_FILE`, `XT_WORKFLOW_DEFINITION` | Bước 4 chạy W1 qua REST `/workflow/v1` (khởi động, instance, inbox, quyết định → 428 + `confirm_url`). Chi tiết: `docs/workflow-api.md` |
| `XT_CURL_OPTS` | Tùy chọn curl thêm (ví dụ `--resolve` khi chạy cục bộ qua APISIX `127.0.0.1:9080`) |

Mỗi loại token đi vào curl bằng tệp header tạm riêng (quyền 600, xóa khi thoát), không nằm trên dòng lệnh (`ps`) và không bị in. Tệp token rỗng được coi là thiếu; token lấy trực tiếp từ môi trường chứa khoảng trắng/xuống dòng bị từ chối. Script tắt `set -x`. Phân loại `chưa triển khai` gồm HTTP `000`, `404`, `502`, `503`, `504`, `530`, thân `error code: 1033` và `chua_trien_khai`.

Lấy token bằng client `xt-cli` (public, auth-code + PKCE, scope optional; mỗi loại một token):

```bash
bash docs/headless-demo.sh --get-token data       # scope xt-data     -> ~/.config/xt/data_token
bash docs/headless-demo.sh --get-token workflow   # scope xt-workflow -> ~/.config/xt/workflow_token
```

Ví dụ (token lưu trong tệp quyền 600):

```bash
export XT_ACCESS_TOKEN_FILE=~/.config/xt/data_token
export XT_DATA_MODEL=<model> XT_DATA_PROFILE=api_t<seq>
bash docs/headless-demo.sh
```

Mã thoát: `0` cả 4 bước thành công thật; `2` chỉ có bước chưa triển khai (không có lỗi); `1` có lỗi, thiếu token hoặc không kết luận.

### Kết quả chạy thật (16/10/2026)

PostgreSQL 17 + PostgREST 16.4, image có sẵn local; một cặp container tên ngẫu nhiên, pool data = 1. JWKS RSA test sinh mới, nạp từ file chỉ đọc dưới uid 65534; không dùng JWT Keycloak thật cho các test này. Container và thư mục JWKS đã dọn.

```console
$ make test-data-integration
collected 31 items
tests/integration/test_headless_data.py ...............................  [100%]
31 passed in 15.35s
```

`make check-data` cũng xanh: lint/format, mypy, import boundaries, SPDX, **173 unit test** (gồm 13 test cấu hình data/deploy) và **18 kiểm thử deploy offline**.

`make check` toàn repo trong phiên này cũng thoát mã 0: **1.265 test Python**, **118 test Desk**, **15 test Playwright headless**, cùng kiểm tra lint/type/import/REUSE/SPDX. Các test Desk dùng fixture của Desk; kết quả này không thay cho kiểm chứng bốn dịch vụ công khai.

Giới hạn review: lượt gọi bổ sung `claude --model claude-opus-5-5` chỉ đọc đã hết timeout 100 giây (mã 124, output rỗng). Không tính lượt đó là review hoàn tất; phản biện SQL/deploy và test được thực hiện bởi các agent hiện có, với kết quả chạy thật nêu trên.

Chạy lúc 01:03:38 ngày 16/10/2026 (+07). Bao gồm khôi phục role/membership bị cấp thừa khi cài lại, nâng cấp DB trước PostgREST, BI có từ trước, giữ source/OID/ACL, install lặp, rollback NOTIFY, tenant/model tạo và xóa lúc server đang chạy, chống GUC/role spoof, GET/HEAD READ ONLY, own-scope, token sai aud/iss/azp, workflow-managed không ghi được và sensitive chỉ có HMAC trong audit (không nằm trong view).

Các khối sau là stdout của **curl thật** trong `test_real_curl_transcript`, gọi trực tiếp PostgREST của fixture. `$XT_DATA_URL` là URL loopback của fixture (không có prefix `/data`); khi đi APISIX dùng `https://xt-api.freepro.online/data`. Mỗi khối dùng tệp header mới quyền 0600 và truyền bằng `-H @file`; token được thay bằng tên biến trong transcript, không ghi bí mật. Không ghép các lệnh `echo >>` từ nhiều khối vào cùng tệp.

```console
$ echo 'Accept-Profile: api_t1' >> "$HDR_FILE"
$ echo 'Authorization: Bearer $TOKEN_ACME_ALICE' >> "$HDR_FILE"
$ curl -sS -i -X GET -H @"$HDR_FILE" "$XT_DATA_URL/ticket?select=title&order=title"
HTTP/1.1 200 OK
Date: Thu, 15 Oct 2026 18:03:39 GMT
Server: postgrest/16.4
Content-Range: 0-1/*
Content-Location: /ticket?order=title&select=title
Content-Length: 46
Content-Type: application/json; charset=utf-8
Content-Profile: api_t1
Vary: Accept, Prefer, Range

[{"title":"A-alice-1"}, 
 {"title":"A-bob-1"}]
```

```console
$ echo 'Accept-Profile: api_t2' >> "$HDR_FILE"
$ echo 'Authorization: Bearer $TOKEN_ACME_ALICE' >> "$HDR_FILE"
$ curl -sS -i -X GET -H @"$HDR_FILE" "$XT_DATA_URL/ticket?select=title"
HTTP/1.1 403 Forbidden
Date: Thu, 15 Oct 2026 18:03:39 GMT
Server: postgrest/16.4
Content-Type: application/json; charset=utf-8
Content-Length: 109
Proxy-Status: PostgREST; error=42501

{"code":"42501","details":null,"hint":"profile_mismatch","message":"truy cập dữ liệu bị từ chối"}
```

```console
$ echo 'Accept-Profile: api_t1' >> "$HDR_FILE"
$ echo 'Authorization: Bearer $TOKEN_AUD_XT_MCP' >> "$HDR_FILE"
$ curl -sS -i -X GET -H @"$HDR_FILE" "$XT_DATA_URL/ticket"
HTTP/1.1 401 Unauthorized
Date: Thu, 15 Oct 2026 18:03:39 GMT
Server: postgrest/16.4
Content-Type: application/json; charset=utf-8
Content-Length: 78
Proxy-Status: PostgREST; error=PGRST303
WWW-Authenticate: Bearer error="invalid_token", error_description="JWT not in audience"

{"code":"PGRST303","details":null,"hint":null,"message":"JWT not in audience"}
```

```console
$ echo 'Content-Profile: api_t1' >> "$HDR_FILE"
$ echo 'Content-Type: application/json' >> "$HDR_FILE"
$ echo 'Authorization: Bearer $TOKEN_ACME_ALICE' >> "$HDR_FILE"
$ curl -sS -i -X PATCH -H @"$HDR_FILE" "$XT_DATA_URL/ticket?id=eq.ade6e36c-7a90-4894-a151-30737d642f18" --data '{"state":"open"}'
HTTP/1.1 403 Forbidden
Date: Thu, 15 Oct 2026 18:03:39 GMT
Server: postgrest/16.4
Content-Type: application/json; charset=utf-8
Content-Length: 106
Proxy-Status: PostgREST; error=42501

{"code":"42501","details":null,"hint":"read_only_api","message":"truy cập dữ liệu bị từ chối"}
```

```console
$ echo 'Accept-Profile: api_t1' >> "$HDR_FILE"
$ curl -sS -i -X GET -H @"$HDR_FILE" "$XT_DATA_URL/ticket"
HTTP/1.1 401 Unauthorized
Date: Thu, 15 Oct 2026 18:03:39 GMT
Server: postgrest/16.4
Content-Type: application/json; charset=utf-8
Content-Length: 87
Proxy-Status: PostgREST; error=PGRST302
WWW-Authenticate: Bearer

{"code":"PGRST302","details":null,"hint":null,"message":"Anonymous access is disabled"}
```

Demo bốn dịch vụ qua hostname công khai đã gọi curl thật, hiện tunnel trả HTTP 530 / `error code: 1033`. Script thoát **2**, không tính là thành công:

```console
$ bash docs/headless-demo.sh
== xT Tầng 1 headless: demo tuần tự 4 dịch vụ (không MCP) ==
   auth=https://xt-auth.freepro.online api=https://xt-api.freepro.online workflow=https://xt-api.freepro.online realm=xt
   token data: KHÔNG có (đặt XT_ACCESS_TOKEN_FILE)
   token workflow: KHÔNG có (đặt XT_WORKFLOW_TOKEN_FILE)

[1/4] SSO Keycloak: OIDC discovery
  => 1 SSO: chưa triển khai (HTTP 530)
    thân (17 byte, cắt ở 600):
    | error code: 1033


[2/4] API Gateway APISIX: /data không token phải bị từ chối (401/403), không phải 2xx
  => 2 Gateway: chưa triển khai (HTTP 530)
    thân (17 byte, cắt ở 600):
    | error code: 1033


[3/4] Data (PostgREST qua /data): đọc view tenant với Accept-Profile
    thiếu: token(XT_ACCESS_TOKEN_FILE) XT_DATA_MODEL XT_DATA_PROFILE; thăm dò không token, KHÔNG tính là đọc dữ liệu
  => 3 Data: chưa triển khai (HTTP 530)
    thân (17 byte, cắt ở 600):
    | error code: 1033


[4/4] Workflow (https://xt-api.freepro.online/workflow/v1/definitions): dùng token workflow riêng, không dùng token data
    thiếu token workflow (XT_WORKFLOW_TOKEN_FILE); thăm dò không token, KHÔNG gửi token data
  => 4 Workflow: chưa triển khai (HTTP 530)
    thân (17 byte, cắt ở 600):
    | error code: 1033


== Tổng kết ==
  1 SSO        chưa triển khai
  2 Gateway    chưa triển khai
  3 Data       chưa triển khai
  4 Workflow   chưa triển khai
```

Đây là trạng thái public chưa khả dụng; không suy kết quả integration loopback thành demo bốn dịch vụ đã xanh. `/workflow/v1/definitions` cũng cần endpoint phía integrator; script dùng token workflow riêng và in **chưa triển khai** khi chưa có dịch vụ.

## Rủi ro và điểm cần các agent khác phản biện

- **Pre-request kiểm các request truy vấn dữ liệu**, `SECURITY DEFINER`, mọi tham chiếu đủ schema; gate ghim `search_path`. Profile kiểm ở gate, RLS là lớp thứ hai.
- **`Content-Profile`, `OPTIONS`, RPC**: gate từ chối ghi và `/rpc`; APISIX chỉ route GET/HEAD, không lộ OPTIONS từ cache PostgREST.
- **Claim `role`**: cố định bằng claim ký sẵn `data_role`; `xt_authenticator` chỉ `SET ROLE xt_web`.
- **Audience và issuer**: PostgREST kiểm `aud`; `iss` và `azp` do gate kiểm. Token MCP không được dùng lại.
- **`organization` nhiều giá trị**: người ở nhiều tenant phải chọn đúng một tenant; mơ hồ thì từ chối.
- **JWKS rỗng/hỏng** sau lỗi tải: `fetch_jwks.py` giữ tệp cũ, không ghi tệp rỗng.
- **PII**: view loại cột `sensitive`; `basic` là văn bản thuần theo chính sách. Kiểm bằng test.
- **Giới hạn hiện tại**: `team_ids` luôn rỗng; `agent_grant` chưa được dùng; `xt-init` chưa tự gọi `install_postgrest_role`.
