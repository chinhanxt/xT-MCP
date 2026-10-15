<!-- SPDX-License-Identifier: Apache-2.0 -->
# Triển khai bằng Docker Compose

Mã nguồn ở `deploy/`. Nguồn quyết định: `architecture-v2.md` D10/D12, `identity.md`, `deployment-public.md`, `research/footprint.md`.

## Profile

| Profile | Dịch vụ | Ghi chú |
|---|---|---|
| `minimal` | postgres:17-alpine, keycloak 26.6.3 (`start --optimized`), apisix 3.19.0 standalone, xt-api, xt-worker, xt-desk (nginx tĩnh) | Giới hạn bộ nhớ theo `footprint.md`: Keycloak 768m (heap `-Xms128m -Xmx384m`), APISIX 256m, Postgres 512m, xt-api/worker 512m |
| `full` | `minimal` + ollama 0.13.0, nats 2.11, seaweedfs 4.00, clamav 1.4, openbao 2.4.1 | Ảnh OSI, đã pin phiên bản. OpenBao ở dev mode (chỉ demo) |
| `public` | + cloudflared 2025.9.1 | Đọc `TUNNEL_TOKEN` từ `deploy/.env`; chạy chung netns với APISIX nên `127.0.0.1:9080` đúng như `deployment-public.md` |
| `placeholder` | `xt-api-placeholder` | Tạm thời, xem dưới |

**Tình trạng xt-api (16/10/2026):** `xt serve` + composition thật chạy được: `make up-minimal` dựng `xt-init` (một lần: `bootstrap` role/schema, `xt db migrate`, schema hàng đợi, đặt mật khẩu `xt_app/xt_migrator/xt_control`), rồi `xt-api` và `xt-worker` (`depends_on: service_completed_successfully`). Cấu hình đủ nằm trong `deploy/.env.example`/`compose.yaml`: `make env` tự sinh mật khẩu DB (`XT_DB_*_PASSWORD`) vào `.env` và khóa tệp (`audit.key`, `cursor.key`, `pii.key`, `receipt-ed25519.pem`) vào `${XT_SECRETS_DIR:-~/.config/xt}/app` (ngoài repo, mount `/run/secrets/xt` chỉ đọc). Thiếu cấu hình: compose dừng với `${VAR:?}`, hoặc `xt serve`/`xt worker` in danh sách lỗi và thoát; `restart: on-failure:5` nên không lặp vô hạn. Container tạm `xt-api-placeholder` (profile `placeholder`) chỉ còn để kiểm hạ tầng khi chưa có `xt`. `postgrest` cần thêm `make pgrst-jwks` và role `xt_pgrst` (chưa có bước tạo role trong `xt-init`), nên các lần kiểm 16/10 chạy `postgres keycloak apisix xt-init xt-api xt-worker`, không có `postgrest`/`xt-desk`.

**Ảnh `xt/xt`:** python:3.13-slim + `libpq5` hệ thống (psycopg thuần Python, không `psycopg-binary`; xem `decisions.md`), uid 10001 không shell/home, rootfs `read_only` + tmpfs `/tmp`, `cap_drop ALL`, `no-new-privileges`.

## Lệnh

```bash
make up-minimal        # sinh deploy/.env (bí mật tự sinh), render APISIX, docker compose up
make provision-realm   # tạo/cập nhật realm xt (idempotent)
make -C deploy export-realm   # + xuất deploy/keycloak/realm-xt.json (đã bỏ secret)
make down              # dừng; make -C deploy clean để xóa cả volume
```

- `deploy/.env` sinh từ `deploy/.env.example`, quyền 600, không commit. Chỉ điền các biến bí mật còn trống, không bao giờ ghi đè giá trị đã có. Hostname, cổng, danh sách client của lớp danh tính đều nằm ở đây.
- `deploy/apisix/apisix.yaml.tmpl` dùng `@@XT_DOMAIN_*@@`; `scripts/render_apisix.py` sinh `deploy/apisix/rendered/apisix.yaml` (APISIX tự reload khi tệp đổi). Chạy lại `make -C deploy render` sau khi đổi hostname.
- `provision.py` nói chuyện Admin REST qua `127.0.0.1:${KC_HTTP_PORT}` (publish chỉ trên loopback), không qua APISIX. APISIX chặn `/admin`, `/realms/master`, `/metrics`, `/health` từ ngoài.

## APISIX: định tuyến theo Host

| Host | Đường dẫn | Đích |
|---|---|---|
| `xt-*` Desk | `/desk/*` | xt-api (trang confirm) |
| | `/mcp`, `/mcp/*` | xt-api (route `desk-mcp`, passthrough như host MCP, không bearer, SSE không đệm; Desk gọi cùng origin) |
| | `/*` | xt-desk (tĩnh) |
| xt-mcp | `/mcp`, `/mcp/*`, `/.well-known/*` | xt-api, **không plugin xác thực**, `proxy-buffering` tắt (SSE), `limit-req` theo IP, timeout đọc 3600 s. xt-api tự trả 401 + PRM |
| xt-api | `/data/*` | `503` có thông báo "chưa triển khai (PostgREST, v0.2)" |
| | `/workflow/*`, `/files/*` | xt-api |
| | `/sandbox.html`, `/assets/*` | xt-desk (origin khác Desk cho sandbox MCP Apps) |
| xt-auth | `/realms/*/clients-registrations/*` | keycloak, **DCR**: body tối đa 8 KB; schema bắt buộc `token_endpoint_auth_method=none`, `grant_types` chỉ `authorization_code`/`refresh_token`, `response_types=[code]`, 1-2 `redirect_uris` (https không wildcard, hoặc http loopback `127.0.0.1`/`[::1]`); `limit-req` 1 r/s burst 4 + `limit-count` 20/giờ theo IP; PUT cũng qua schema; GET/DELETE (route riêng) 120/giờ |
| | `/realms/*/protocol/openid-connect/token*` | keycloak, `limit-count` 60/phút theo IP, body tối đa 16 KB |
| | `/*` | keycloak (`X-Forwarded-Proto/Host`), `KC_HOSTNAME=https://<xt-auth>` |

Không route nào chèn `Authorization`/bearer tĩnh (mục LOW của `audit-desk.md`). Host không khai báo thì APISIX trả `404`.

## Realm `xt` (provision.py)

- **Profile (16/10/2026, `decisions.md` "Tier PII của Desk chat").** Client public `xt-desk-agent` có redirect theo `XT_PROFILE`. Production (mặc định, để trống): chỉ `https://<XT_DOMAIN_DESK>/auth/callback`, **không** loopback, `webOrigins` chỉ origin Desk. Dev (`XT_PROFILE=dev` hoặc `provision.py --dev`): thêm `XT_DESK_AGENT_DEV_REDIRECTS` (mặc định `localhost:5173` và `127.0.0.1:5173`) và origin tương ứng. Giá trị khác `dev` (kể cả `devx`) coi là production. Test: `deploy/tests/offline` lớp `DeskAgentRedirects`. Chạy lại provision ở production sẽ gỡ loopback khỏi client đã có (upsert ghi đè `redirectUris`).

- Organizations bật; mật khẩu tối thiểu 12 ký tự + hoa/thường/số, không trùng username/email, nhớ 3 mật khẩu gần nhất; `accessTokenLifespan` 300 s; refresh token xoay vòng; brute-force bật.
- Role realm lấy từ `research/packs-draft/*/policies`: `employee, dept_head, director, chief_accountant, payment_accountant, hr, hr_pii, it_admin, admin, agent`.
- Client scope `mcp-xt`: mapper audience `aud = https://<xt-mcp>/mcp`, mapper audience `xt-api-introspect` (bắt buộc giữ; nếu thiếu, introspection Keycloak 26 trả `active:false`, `identity.md`), mapper Organization. **Không** nằm trong default của realm; là *default optional* để connector xin `scope=mcp-xt`. `offline_access` bị gỡ khỏi optional mặc định.
- Access token chỉ mang `aud = [resource MCP, xt-api-introspect]`: provision gỡ mapper `audience resolve` khỏi scope `roles` nên không còn `account`.
- Client: `xt-desk` (public, PKCE S256, nhận `mcp-xt` mặc định, `XT_MCP_HUMAN_CLIENTS`), `xt-desk-confirm` (public, PKCE S256, flow `xt-stepup`: LoA1 mật khẩu, LoA2 OTP với `loa-max-age=0`, `minimum.acr.value=urn:xt:acr:otp`, `acr.loa.map` ở realm, mapper `amr` tuổi tối đa 300 s theo `spike-hitl`), `xt-api-introspect` (confidential, chỉ để introspection, secret lấy từ `.env`).
- DCR ẩn danh (sửa BUG-D1/D2 của `audit-deploy.md`), nhiều lớp:
  - Gateway (xem bảng): schema chặn `client_credentials`/`password`/`implicit`, client confidential, wildcard, http không-loopback, >2 redirect URI, `javascript:` (400 thay vì 500).
  - Keycloak client policy `xt-dcr-anonymous` (điều kiện `client-updater-context` = ByAnonymous hoặc ByRegistrationAccessToken, tức cả cập nhật client DCR): `pkce-enforcer` (S256), `reject-ropc-grant`, `reject-implicit-grant`, `secure-response-type`, `full-scope-disabled`, `consent-required`, `secure-redirect-uris-enforcer` (không wildcard, chỉ `chatgpt.com`/`claude.ai`/`127.0.0.1`).
  - Registration policy: trusted-hosts `chatgpt.com, claude.ai, 127.0.0.1` (bỏ `localhost`; Claude Code dùng `127.0.0.1`), `client-uris-must-match=true`, `max-clients=50`, allowed-client-scopes không có `offline_access`.
  - Không có service account/mapper tuỳ biến cho client DCR; client public không lấy được token `client_credentials`/ROPC (đã thử, 401/400). Allowlist `azp` của xt-api vẫn là lớp cuối.
  - Còn lại: client DCR rác không tự hết hạn (cần job dọn); `allow-http-scheme` của executor phải bật để nhận loopback http nên http không-loopback do gateway chặn, không phải Keycloak.
- Allowlist động connector (`identity.md`): client confidential `xt-client-registry` (service account, chỉ role `realm-management/view-clients`; `fullScopeAllowed=true` vì token cần mang role đó, không có role nào khác). `.env` sinh sẵn `XT_MCP_CLIENT_REGISTRY=keycloak`, `XT_IDP_ADMIN_CLIENT_ID/SECRET`, `XT_MCP_CONNECTOR_REDIRECT_HOSTS`, compose truyền cùng `XT_IDP_ADMIN_API_URL`/`XT_IDP_ADMIN_TOKEN_URL` nội bộ cho xt-api. Smoke chứng minh: client vừa DCR có `pkce.code.challenge.method=S256`, public, `fullScopeAllowed=false`, không service account/ROPC/mapper; token registry bị 403 khi đọc `/users` và khi xóa client.
- TOTP: required action `CONFIGURE_TOTP` bật; user giữ role trong `XT_TOTP_ROLES` bị buộc cấu hình TOTP mỗi lần chạy provision. Flow `xt-stepup` tự đòi thiết lập OTP nếu user chưa có.
- Giới hạn: không ép đường dẫn chính xác của redirect URI (chỉ host + không wildcard). `XT_MCP_ALLOWED_CLIENTS` vẫn phải thêm `client_id` của connector DCR sau khi đăng ký (`identity.md`).
- `deploy/keycloak/realm-xt.json`: partial export để review, đã loại secret; không dùng để import.

## Hardening container và ảnh

- Mọi dịch vụ đã chạy thử (postgres, keycloak, apisix, xt-api, xt-worker, xt-desk, placeholder): `no-new-privileges`, `cap_drop: [ALL]` (postgres thêm `CHOWN, SETUID, SETGID, FOWNER, DAC_OVERRIDE` cho entrypoint), `pids_limit: 256`, giới hạn `cpus`. `read_only` + tmpfs cho postgres, keycloak, xt-api, xt-worker, xt-desk, placeholder (chạy uid 65534). APISIX **chưa** `read_only` vì nginx.conf sinh lúc khởi động trong `conf/` lẫn bind mount.
- Profile `full`/`public` (ollama, nats, seaweedfs, clamav, openbao, cloudflared): chỉ `no-new-privileges`, `pids_limit`, `cpus` (cloudflared thêm `cap_drop ALL` + `read_only`); `cap_drop`/`read_only` của nhóm full **chưa thử** nên chưa bật.
- Giới hạn thân request ở APISIX bằng `client-control`: `/mcp` 1 MiB, DCR 8 KB, token 16 KB, `/desk` 64 KB, `/workflow` 1 MiB, `/files` 64 MiB, uri xt-auth còn lại 64 KB.
- Healthcheck APISIX dùng `bash /dev/tcp` (ảnh không có curl).
- Pin digest (`@sha256:`) cho mọi ảnh gốc, trong `compose.yaml` và `deploy/docker/*.Dockerfile`. Ảnh có sẵn local lấy bằng `docker inspect --format '{{index .RepoDigests 0}}' <ảnh>`; ảnh còn lại lấy bằng `docker buildx imagetools inspect <tag>` (dòng `Digest:`, manifest list đa kiến trúc). Khi nâng phiên bản phải cập nhật lại cả tag lẫn digest. Ảnh tự build (`xt/keycloak`, `xt/xt`, `xt/desk`) tái lập qua digest của ảnh gốc.

## Kiểm thử an ninh

`deploy/tests/security_smoke.sh` (stack đã chạy + provision): 39 check, **39 PASS, 0 FAIL** (chạy nhiều lần liên tiếp), gồm chặn admin/master kể cả lách URL-encode, issuer không bị spoof, DCR (redirect, grant, auth method, wildcard, http, `javascript:`, số lượng URI), client DCR không lấy được token `client_credentials`/ROPC, flood DCR 1 IP bị 429 (IP khác không bị ảnh hưởng), flood token bị 429, body 40 KB bị 413. Script dùng `CF-Connecting-IP` giả (APISIX chỉ tin header này từ mạng nội bộ/loopback; origin không được mở ra Internet ngoài tunnel) và tự xóa client DCR đã tạo.

## Kiểm chứng thực tế (15/10/2026)

Điều kiện: dựng từ volume trống bằng `make up-minimal` (alpine placeholder cho xt-api, vì `xt serve` chưa có) rồi `make provision-realm`; Keycloak healthy sau khoảng 25 s. Chưa chạy: xt-api thật, xt-worker, xt-desk, profile `full`/`public`, SSE thật qua `/mcp` (placeholder không stream), luồng step-up OTP bằng trình duyệt.

OIDC discovery qua APISIX (Host header, không qua Internet):

```
$ curl -s -H 'Host: xt-auth.freepro.online' 127.0.0.1:9080/realms/xt/.well-known/openid-configuration
issuer                 = https://xt-auth.freepro.online/realms/xt
authorization_endpoint = https://xt-auth.freepro.online/realms/xt/protocol/openid-connect/auth
token_endpoint         = https://xt-auth.freepro.online/realms/xt/protocol/openid-connect/token
jwks_uri               = https://xt-auth.freepro.online/realms/xt/protocol/openid-connect/certs
registration_endpoint  = https://xt-auth.freepro.online/realms/xt/clients-registrations/openid-connect
introspection_endpoint = https://xt-auth.freepro.online/realms/xt/protocol/openid-connect/token/introspect
```

DCR ẩn danh (`POST /realms/xt/clients-registrations/openid-connect`, cùng Host):

```
redirect_uri https://claude.ai/api/mcp/auth_callback -> HTTP 201 (cấp client_id)
redirect_uri https://chatgpt.com/connector_platform_oauth_redirect -> HTTP 201
redirect_uri https://evil.example/cb -> HTTP 403
  {"error":"insufficient_scope","error_description":"Policy 'Trusted Hosts' rejected request ... URI doesn't match any trusted host or trusted domain"}
```

Chặn từ ngoài và placeholder:

```
Host xt-auth /admin/master/console/ -> 403      Host xt-auth /realms/master -> 403
Host xt-mcp  /mcp        -> 503 {"error":"chua_trien_khai","message":"xt-api chưa triển khai: lệnh `xt serve` chưa tồn tại ..."}
Host xt-api  /data/v1/x  -> 503 {"error":"chua_trien_khai","message":"Dịch vụ /data (PostgREST) chưa triển khai; dự kiến v0.2 ..."}
```

Token thật (user tạm có role `dept_head` trong Organization `acme`, client tạm có direct grant; đã xóa sau khi thử):

```
scope=openid          -> aud=['account'];  introspect active = False
scope=openid mcp-xt   -> aud=['xt-api-introspect','https://xt-mcp.freepro.online/mcp','account'],
                         organization=['acme'];  introspect active = True
```

Idempotent: chạy `provision.py` nhiều lần liên tiếp, export không đổi nội dung có nghĩa. Số liệu DCR phía trên là của bản chưa có schema gateway/client policy; kết quả hiện hành nằm ở mục Kiểm thử an ninh. Bộ nhớ lúc chạy: Keycloak 542 MiB, Postgres 51 MiB, APISIX 254 MiB (có page cache; `footprint.md` đo ổn định ~88 MiB), placeholder 0.5 MiB; `docker inspect` APISIX: không OOM, 0 restart.

## Log Postgres an toàn

Theo `docs/model-engine.md` §8 và `docs/integration-notes.md`: câu lệnh và tham số bind (mật khẩu role, PII) không được vào log.

- `deploy/compose.yaml`, service `postgres`: `command` có `-c log_statement=none -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0 -c log_error_verbosity=terse -c log_min_duration_statement=-1 -c log_min_duration_sample=-1 -c log_transaction_sample_rate=0`; không nạp `auto_explain` (không đặt `shared_preload_libraries`).
- `deploy/scripts/init_db.py` (idempotent): kết nối superuser `SET log_statement='none'` trước `bootstrap()`, sau đó `ALTER ROLE xt_app` và `xt_migrator` `SET` 4 tham số `log_statement`, `log_parameter_max_length`, `log_parameter_max_length_on_error`, `log_error_verbosity`.
- Test offline: `deploy/tests/offline/test_postgres_logging.py`.
- Smoke sau triển khai: `SHOW log_statement;` (none), `SHOW log_parameter_max_length;` (0), `SHOW log_error_verbosity;` (terse), `SELECT current_setting('shared_preload_libraries') NOT LIKE '%auto_explain%';` (t).
