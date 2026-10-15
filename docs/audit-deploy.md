<!-- SPDX-License-Identifier: Apache-2.0 -->
# Audit hạ tầng deploy (Opus TESTER) — 15/10/2026

Phạm vi: `deploy/` (compose.yaml, keycloak/provision.py, apisix/apisix.yaml.tmpl + rendered, docker/*.Dockerfile, .env.example, scripts/, Makefile) và `docs/deploy.md`.
Đối chiếu: `architecture-v2.md` v2.2, `identity.md`, `deployment-public.md`, `research/spike-mcp-auth.md`, `research/spike-hitl.md`, `research/verification-spikes.md`, `research/connector-requirements.md`, `docs/audit-desk.md`.

**Phương pháp:** dựng thật bằng `docker compose --profile placeholder up` (postgres:17-alpine, `xt/keycloak:26.6.3-optimized` build tại chỗ, apache/apisix:3.19.0-debian) + `python3 -I keycloak/provision.py`; Keycloak healthy ~10 s. Tấn công qua APISIX `127.0.0.1:9080` bằng header `Host`. `make up-minimal` đầy đủ **không dựng được** (xem BUG-C0). Đã dọn sạch container/volume/image tự tạo.

Regression: `deploy/tests/security_smoke.sh` (mới) — 17 PASS, 2 FAIL cố ý chốt BUG-D1/BUG-D2.

## Verdict

**ĐẠT CÓ ĐIỀU KIỆN.** Lõi an ninh mạnh: APISIX chặn admin/master/metrics/health kể cả khi lách URL-encode/traversal; issuer Keycloak không bị `X-Forwarded-Host` đầu độc; không có bearer tĩnh; DCR ép redirect ở mức host và chặn leo thang bằng protocol mapper; không rò secret ra file tracked; cổng chỉ bind loopback. **Hai lỗi MEDIUM phải sửa trước khi công khai** (DCR không chặn grant_type → mint service-account token aud=MCP ẩn danh; DCR không bị rate-limit → DoS cạn slot client), cộng vài LOW về hardening compose.

| # | Khu vực | Kết quả |
|---|---|---|
| 1 | APISIX uri-blocker (admin/master/metrics/health) + traversal/encode | **PASS** |
| 2 | APISIX host-routing, host lạ→404, admin API tắt, no static bearer | **PASS** |
| 3 | Keycloak issuer vs X-Forwarded-* spoof | **PASS** |
| 4 | DCR redirect host policy (evil/subdomain/mix) | **PASS** |
| 5 | DCR leo thang (mapper, consent, scope mcp-xt làm default) | **PASS** |
| 6 | DCR grant_type (client_credentials/password) | **FAIL — BUG-D1** |
| 7 | DCR rate-limit tại gateway | **FAIL — BUG-D2** |
| 8 | Realm settings (brute-force, password, token lifetime, rotation, offline_access) | **PASS** |
| 9 | Secrets (sinh, quyền, không rò ra tracked/export/log) | **PASS** |
| 10 | Compose hardening (non-root, read-only, limits, pin digest, request size) | **PARTIAL — BUG-C1..C4** |

---

## PASS (có chứng cứ)

**1. APISIX uri-blocker chống lách.** `block_rules: ["^/admin","^/realms/master","^/metrics","^/health"]` khớp trên URI đã chuẩn hóa. Mọi biến thể đều 403/404, không có 200:
```
/admin/master/console/ 403   /%61dmin/master/console/ 403   //admin/master/console/ 403
/./admin/... 403   /x/../admin/master/console/ 403   /admin%2fmaster 403   /realms%2fmaster 403
/..%2fadmin 400   /ADMIN/... 404 (KC phân biệt hoa thường)   /metrics 403   /health 403
```
Path hợp lệ vẫn chạy (`/.well-known/openid-configuration` 200). Admin console chỉ truy cập trực tiếp `127.0.0.1:18080` (loopback, tunnel không trỏ tới) — đúng thiết kế provision.

**2. Host-routing + no static bearer + admin API tắt.** Host không khai báo → 404 (`evil.invalid`, request không Host). APISIX standalone `role: data_plane, config_provider: yaml` → `/apisix/admin/routes` 404, không port 9180/9090 lắng nghe. Không route nào `set` header `Authorization` (khớp `audit-desk.md` mục LOW). `/mcp` có `proxy-buffering.disable_proxy_buffering=true` + `limit-req` theo IP (30/60) + timeout đọc 3600 s cho SSE; gateway không xác thực (xt-api tự trả 401+PRM, D12). (SSE thật chưa test được vì placeholder không stream.)

**3. Issuer không bị spoof.** `KC_HOSTNAME=https://xt-auth.freepro.online` cố định tuyệt đối + `proxy-rewrite` ghi đè `X-Forwarded-Proto/Host`. Gửi `X-Forwarded-Host: evil.attacker.com` → `issuer`/`token_endpoint` vẫn `https://xt-auth.freepro.online/...`.

**4. DCR redirect host policy.** `trusted-hosts` (`host-sending...=false`, `client-uris-must-match=true`, hosts `chatgpt.com,claude.ai,localhost,127.0.0.1`). `evil.example` 403, `evil.chatgpt.com` 403, `chatgpt.com.evil.com` 403, mix `claude.ai + evil` 403. Chỉ nhận host tin cậy → 201.

**5. Chống leo thang qua DCR.** Định dạng OIDC DCR từ chối trường `protocol_mappers`/`consentRequired` (400 "Unrecognized field"); thêm chính sách `allowed-protocol-mappers` chỉ cho mapper vô hại (không có `oidc-hardcoded-role-mapper`, `oidc-audience-mapper`, group mapper) → **hai lớp chặn** hardcoded-role/forge-aud. Policy `consent-required` (bật) buộc `consentRequired=true`. Request `scope=mcp-xt` tường minh bị policy `Allowed Client Scopes` từ chối 403.

**8. Realm settings.** `bruteForceProtected=true` (failureFactor 30), `revokeRefreshToken=true` + `refreshTokenMaxReuse=0` (rotation), `accessTokenLifespan=300` (khớp trần thu hồi identity.md), `ssoSessionMaxLifespan=36000`, `registrationAllowed=false`, passwordPolicy 12 ký tự + hoa/thường/số + notUsername/notEmail + history(3), OTP `totp/HmacSHA1/6/30`. `offline_access` đã gỡ khỏi realm default-optional và không nằm trong allowed-client-scopes của DCR (default-optional còn `address,phone,microprofile-jwt,organization,mcp-xt`). Flow `xt-stepup` LoA1 pwd / LoA2 OTP `loa-max-age=0`, mapper amr maxAge 300 — khớp spike-hitl. `CONFIGURE_TOTP` bật (0 user hiện tại là đúng, chưa có user).

**9. Secrets.** `envfile.ensure_env` tạo `.env` quyền 600, chỉ điền biến trống bằng `secrets.token_urlsafe(24)`, không ghi đè. Giá trị secret thật trong `.env` **không** xuất hiện trong `realm-xt.json`, `.env.example`, template/rendered APISIX. `provision.strip_secrets` + `partial-export` → `realm-xt.json` có 0 trường `secret/clientSecret/registrationAccessToken` và 0 chuỗi `**********`. `.gitignore` chặn `.env` và `apisix/rendered/`. Log provision không in giá trị secret.

---

## BUG (ưu tiên sửa)

### BUG-D1 — DCR cấp mọi grant_type → mint service-account token aud=MCP ẩn danh (MEDIUM)
`provision.ensure_dcr_policies` không khống chế `grant_types`/`token_endpoint_auth_method`. Keycloak DCR nhận 201 cho `grant_types:["client_credentials"]` (bật `serviceAccountsEnabled`) và `["password"]` (bật `directAccessGrantsEnabled`).

**Repro** (ẩn danh, chỉ cần redirect host tin cậy):
```
POST /realms/xt/clients-registrations/openid-connect
{"client_name":"cc","redirect_uris":["https://claude.ai/api/mcp/auth_callback"],"grant_types":["client_credentials"]}
-> 201  client_id=<uuid>  client_secret=<...>
POST .../token  grant_type=client_credentials&scope=mcp-xt  -u <uuid>:<secret>
-> access_token: aud=['xt-api-introspect','https://xt-mcp.freepro.online/mcp'], scope="... mcp-xt"
```
Token mang **đúng audience MCP + scope mcp-xt**, qua được bước 1–4 của `AccessTokenVerifier`. Chặn lại DUY NHẤT nhờ allowlist `azp` (`XT_MCP_ALLOWED_CLIENTS`): azp = UUID lạ → `client_not_allowed` (401). Token cũng không có role/organization nên tác động nghiệp vụ ~0 **miễn là** allowlist azp được giữ nghiêm. Rủi ro: (a) allowlist azp trở thành điểm chết một-lỗi; (b) `mcp-xt` là realm default-optional nên *mọi* client đều lấy được aud MCP; (c) client `password`/`directAccessGrants` ẩn danh mở bề mặt ROPC/credential-stuffing (được brute-force protection giảm nhẹ). Khác giả định identity.md (DCR client = public auth-code).

**Fix** (không sửa file hiện tại — đề xuất cho provision.py): thêm client registration policy chặn hoặc hậu-xử-lý kẹp `serviceAccountsEnabled=false`, `directAccessGrantsEnabled=false`, chỉ cho `grant_types ⊆ {authorization_code, refresh_token}` cho client DCR ẩn danh; hoặc bật một `allowed-client-templates` nghiêm hơn. Vẫn giữ azp allowlist làm lớp cuối.

### BUG-D2 — DCR không bị rate-limit → DoS cạn slot client (MEDIUM)
Route `auth` (host `xt-auth`) chỉ có `uri-blocker` + `proxy-rewrite`, **không** `limit-req`. Policy `max-clients=200`. 8/8 POST DCR liên tiếp đều 201 (không 429). Kẻ tấn công ẩn danh flood tới 200 client → DCR trả lỗi cho mọi connector thật (ChatGPT/Claude không đăng ký được) = DoS lớp đăng ký.

**Fix:** thêm `limit-req`/`limit-count` theo IP cho riêng path `/realms/*/clients-registrations/*` trên route `auth` (route khác của Keycloak giữ nguyên); cân nhắc hạ `max-clients` + job dọn client DCR rác.

---

## LOW / hardening

- **BUG-C0 (chặn dựng, không phải an ninh):** `make up-minimal` nhánh `HAS_SERVE=yes` build `xt-desk` thất bại — `desk/vite.config.ts` đòi `DESK_APP_MAX_ARGS_BYTES, DESK_APP_GLOBAL_CALLS_PER_SEC, DESK_APP_GLOBAL_BURST` nhưng `compose.yaml` (service `xt-desk` args) và `.env.example` thiếu 3 biến này. Phải bổ sung build-arg cho khớp desk.
- **BUG-C1:** `client_max_body_size 0` trong nginx.conf APISIX = **không giới hạn kích thước request** ở gateway (mọi route gồm `/mcp`). Nên đặt trần hợp lý (ví dụ `client-control`/`request-validation` hoặc `client_max_body_size`) để chặn DoS thân request lớn.
- **BUG-C2:** Không có `read_only`, `cap_drop:[ALL]`, `security_opt:[no-new-privileges:true]`, `pids_limit`, giới hạn CPU cho container nào (chỉ có `mem_limit`). Thêm cho postgres/keycloak/apisix/xt-*.
- **BUG-C3:** Ảnh pin theo tag, **không pin digest** (`python:3.13-slim`, `node:22-alpine`, `ghcr.io/astral-sh/uv:0.8`, `quay.io/keycloak/keycloak:26.6.3`...). Pin `@sha256:` để tái lập + chống nhiễm nguồn. OSI: tất cả hợp lệ (đã ghi giấy phép trong compose).
- **BUG-C4 (INFO):** `redirect http://chatgpt.com/cb` và wildcard `https://chatgpt.com/*` được DCR nhận (policy chỉ kiểm host, không kiểm scheme/path) — đã ghi là giới hạn trong deploy.md/identity.md; chấp nhận vì host do OpenAI/Anthropic kiểm soát và còn azp gate. `javascript:alert(1)` → **HTTP 500** `unknown_error` thay vì 400 (không tạo client; chỉ là robustness/khó chịu log).
- **INFO:** `trusted-hosts` gồm `localhost,127.0.0.1` trên instance công khai → ai cũng đăng ký được client redirect loopback (khó khai thác trực tiếp; `.env.example` đã ghi "bỏ nếu không cần"). Container `postgres`/placeholder chạy user root ở cấp container (tiến trình postgres thực tế hạ quyền qua gosu; placeholder chỉ là responder 503 tạm). keycloak(uid 1000)/apisix(uid 636)/xt-api(uid 10001)/desk(nginx-unprivileged) đều non-root.


## Phản hồi tác giả (15/10/2026)

Đã sửa; `deploy/tests/security_smoke.sh` **39/39 PASS** (chạy nhiều lần, stack dựng lại từ volume trống).

- **BUG-D1 (đã sửa):** thêm client policy `xt-dcr-anonymous` (ByAnonymous: PKCE S256, reject ROPC/implicit, response_type `code`, full-scope-disabled, consent, `secure-redirect-uris-enforcer` không wildcard/chỉ host cho phép) trong `provision.py`, cộng schema ở gateway (route `auth-dcr`): `token_endpoint_auth_method=none` bắt buộc, `grant_types` ⊆ {authorization_code, refresh_token}, `response_types=[code]`, 1-2 redirect URI. `grant client_credentials/password/implicit` giờ 400; client DCR hợp lệ không lấy được token `client_credentials` (401) hay ROPC (400). `localhost` bỏ khỏi trusted-hosts mặc định (giữ `127.0.0.1`). `mcp-xt` vẫn là default-optional (cần cho DCR connector xin scope); allowlist `azp` vẫn là lớp cuối. Nếu connector thật không gửi `token_endpoint_auth_method` thì sẽ bị 400: cần xác nhận với Claude/ChatGPT thật (docs nói gửi `none`), chưa thử.
- **BUG-D2 (đã sửa):** `limit-req` 1 r/s burst 4 + `limit-count` 20/giờ/IP cho DCR; `limit-count` 60/phút/IP cho token/introspect; `max-clients` 50 (từ 200). `limit-req` chỉ làm chậm client tuần tự nên cần `limit-count` mới ra 429. Khoá theo IP thật qua plugin `real-ip` (`CF-Connecting-IP`, chỉ tin từ dải nội bộ). Botnet nhiều IP vẫn có thể lấp 50 slot: chưa có job dọn client DCR rác (việc tiếp theo).
- **BUG-C0 (đã sửa):** thêm 3 build-arg desk vào compose/Dockerfile/.env.example; `xt-desk` build thật và phục vụ 200 qua APISIX. `xt serve` đã có nên compose dùng `serve --transport http`, thêm `XT_MCP_SERVICES`, `XT_DESK_URL`, `XT_MCP_ALLOWED_HOSTS`; xt-api thật chưa chạy thử.
- **BUG-C1 (đã sửa):** `client-control.max_body_size` theo từng route (xem docs/deploy.md); smoke kiểm 413.
- **BUG-C2 (một phần):** `no-new-privileges`, `cap_drop ALL`, `pids_limit`, `cpus`, `read_only`+tmpfs cho các dịch vụ minimal đã chạy thử. APISIX chưa `read_only`; nhóm `full` chỉ no-new-privileges/pids/cpus (chưa thử cap_drop). Healthcheck APISIX hỏng sẵn (ảnh không có curl) đã sửa bằng bash `/dev/tcp`.
- **BUG-C3 (đã sửa):** pin `@sha256:` mọi ảnh gốc (compose + Dockerfile); cách tra cứu ghi trong docs/deploy.md.
- **BUG-C4 (đã sửa):** `javascript:` giờ 400 (gateway) / 403 (policy), không còn 500; wildcard và http không-loopback bị chặn; `localhost` bị chặn.
- **Bổ sung theo identity agent:** policy `pkce-enforcer` (auto-configure, S256) áp cho DCR ẩn danh và cập nhật bằng registration token; service account `xt-client-registry` (chỉ `view-clients`) + biến `.env` cho `XT_MCP_CLIENT_REGISTRY=keycloak`. Smoke đọc client vừa DCR bằng chính service account đó: `pkce.code.challenge.method=S256`.
