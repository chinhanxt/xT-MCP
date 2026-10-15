<!-- SPDX-License-Identifier: Apache-2.0 -->
# E2E Desk ↔ Keycloak (client `xt-desk-agent`)

Ngày chạy: 16/10/2026. Stack: `make up-minimal` + `make provision-realm` trên volume mới (`xt_pgdata` tạo mới, realm `xt: đã tạo`). Headless hoàn toàn (Chromium của Playwright, không display, không sửa `/etc/hosts`).
Script tái sử dụng: `deploy/tests/desk_oidc_e2e.sh` (điều phối), `desk_oidc_e2e.py` (kiểm client + tạo/xóa user), `desk_oidc_e2e.mjs` (luồng trình duyệt).

## Cách ánh xạ host

Issuer cấu hình là `https://xt-auth.freepro.online/...` (https), còn APISIX cục bộ chỉ nghe http `127.0.0.1:9080`. `--host-resolver-rules` không đủ vì Chromium vẫn đòi TLS, nên dùng `context.route` của Playwright: mọi request tới `xt.freepro.online`, `xt-auth.*`, `xt-mcp.*`, `xt-api.*` được chuyển qua HTTP tới `127.0.0.1:9080` với đúng `Host` (Keycloak/APISIX thật); riêng origin Desk trả bản build `desk/` (SPA fallback). Cấu hình production (https, `KC_HOSTNAME`) giữ nguyên, không nới. Hạn chế của Playwright: đích redirect do `route.fulfill` tạo ra không được route lại (rơi ra mạng thật, Cloudflare trả 530), nên script đổi 3xx của điều hướng thành trang `location.replace` (giữ Set-Cookie); không ảnh hưởng nội dung kiểm.

## 1. security_smoke.sh

`PASS=39 FAIL=0` (toàn bộ nhóm: chặn admin/master/metrics/health, host lạ 404, issuer không bị spoof, DCR redirect/grant/auth-method, rate-limit 429, body 413).

## 2-4. Kết quả `desk_oidc_e2e.sh` (output thật)

User test tạo bằng Admin REST: không role approver (chỉ `default-roles-xt`), mật khẩu ngẫu nhiên, xóa khi xong. Build Desk dùng `DESK_OIDC_ISSUER=https://xt-auth.freepro.online/realms/xt`, `DESK_OIDC_CLIENT_ID=xt-desk-agent`, `DESK_OIDC_SCOPE="openid mcp-xt"`.

```
== Keycloak: cấu hình client ==
== client xt-desk-agent (cấu hình Admin REST) ==
  PASS  publicClient=true (không secret)
  PASS  serviceAccountsEnabled=false (không service account)
  PASS  chỉ authorization-code (implicit/ROPC tắt)
  PASS  pkce.code.challenge.method=S256
  PASS  redirectUris đúng danh sách tài liệu, không wildcard: ['http://127.0.0.1:5173/auth/callback', 'http://localhost:5173/auth/callback', 'https://xt.freepro.online/auth/callback']
  PASS  mcp-xt là OPTIONAL scope (không default)
  PASS  không có service-account-user (HTTP 400)
== hành vi thật qua gateway (Host xt-auth) ==
  PASS  S256 + redirect đúng -> trang login (HTTP 200)
  PASS  thiếu code_challenge bị từ chối (HTTP 302, https://xt.freepro.online/auth/callback?error=invalid_request&error_description=Missing+parameter:+code_challenge_method)
  PASS  code_challenge_method=plain bị từ chối (HTTP 302, https://xt.freepro.online/auth/callback?error=invalid_request&error_description=Invalid+parameter:+code+challenge+method)
  PASS  redirect_uri lạ bị từ chối: https://evil.example/auth/callback (HTTP 400, không redirect)
  PASS  redirect_uri lạ bị từ chối: https://xt.freepro.online/auth/callback/../x (HTTP 400, không redirect)
  PASS  redirect_uri lạ bị từ chối: https://xt.freepro.online.evil.com/auth/callback (HTTP 400, không redirect)
  PASS  redirect_uri lạ bị từ chối: https://xt.freepro.online/other (HTTP 400, không redirect)
  PASS  client_credentials bị từ chối (HTTP 401)
FAIL=0
== Build Desk (DESK_OIDC_* trỏ Keycloak thật qua APISIX) ==
(!) Some chunks are larger than 500 kB after minification. Consider:
== Headless browser flow ==
== Headless flow: Desk -> Keycloak -> Desk ==
  PASS  Desk hiện nút đăng nhập khi chưa có phiên (chưa gọi MCP)
  PASS  chưa đăng nhập: 0 request /mcp (thực tế 0)
  http 503 https://xt.freepro.online/mcp
  PASS  quay lại Desk, nút đăng nhập biến mất (đã đăng nhập)
== Yêu cầu authorize / token (PKCE) ==
  PASS  authorize client_id=xt-desk-agent
  PASS  response_type=code
  PASS  PKCE: code_challenge_method=S256, challenge=ULwyTaVuV2zNKzBOS93haBxaQVxCAsFBTAIRQd6Mu-g
  PASS  scope xin: "openid mcp-xt"
  PASS  redirect_uri=https://xt.freepro.online/auth/callback
  PASS  token request: có code_verifier (64 ký tự), không client_secret
  PASS  SHA256(code_verifier) == code_challenge
== Token trên request /mcp ==
  PASS  bắt được request POST https://xt.freepro.online/mcp có Authorization
  PASS  Authorization: Bearer <JWT>
  claims: {"iss":"https://xt-auth.freepro.online/realms/xt","azp":"xt-desk-agent","aud":["xt-api-introspect","https://xt-mcp.freepro.online/mcp","account"],"scope":"openid profile email mcp-xt","preferred_username":"e2e-adaf2a","realm_access":{"roles":["offline_access","uma_authorization","default-roles-xt"]},"exp":1792085911}
  PASS  azp=xt-desk-agent
  PASS  aud chứa MCP resource https://xt-mcp.freepro.online/mcp (aud=["xt-api-introspect","https://xt-mcp.freepro.online/mcp","account"])
  PASS  scope chứa mcp-xt ("openid profile email mcp-xt")
  PASS  iss=https://xt-auth.freepro.online/realms/xt
  PASS  user test không có role approver
== Lưu trữ phía trình duyệt (origin Desk) ==
  localStorage keys= sessionStorage keys= cookies(Desk origin)=0
  PASS  localStorage không chứa token/JWT
  PASS  sessionStorage không chứa token/JWT
  PASS  cookie origin Desk không chứa token (và rỗng)
  PASS  đã thấy 4 loại token để đối chiếu (access/refresh/id)
== Phản hồi MCP thật cho token này ==
  POST https://xt-mcp.freepro.online/mcp (token trên) -> <html>
 <head><title>503 Service Temporarily Unavailable</title></head>
 <body>
 <center><h1>503 Service Temporarily Unavailable</h1></center>
 <hr><center>openresty</center>
 <p><em>Powered by <a hre
FAIL=0
```

Kết luận: client public, không secret, không service account, chỉ auth-code, PKCE S256 bắt buộc (thiếu hoặc `plain` bị từ chối), redirect URI đúng 3 giá trị tài liệu (không wildcard; biến thể lạ nhận 400, không redirect), `mcp-xt` là optional scope. Token Desk gửi tới `/mcp`: `azp=xt-desk-agent`, `aud` có `https://xt-mcp.freepro.online/mcp`, scope có `mcp-xt`. localStorage/sessionStorage/cookie của origin Desk không chứa token (sessionStorage chỉ giữ `{state,verifier,nonce}` tạm trong lúc chuyển hướng và bị xóa sau callback).

## Phát hiện (16/10/2026) và trạng thái sửa

Chạy lại 16/10/2026 trên volume mới (`xt-init` + `xt-api` + `xt-worker` thật, `make provision-realm`): `security_smoke.sh` **PASS=39 FAIL=0**; `desk_oidc_e2e.sh` **FAIL=0** ở mọi nhóm.

1. **Desk `/mcp` sai route: ĐÃ SỬA.** Route APISIX `desk-mcp` (host Desk, `/mcp`, `/mcp/*`) tới `xt-api:8000`; `XT_MCP_ALLOWED_HOSTS/ORIGINS` có host/origin Desk. `POST https://xt.freepro.online/mcp` có token nay đi tới xt-api (không token: 401 từ xt-api, không còn 404).
2. **Crash-loop xt-api/xt-worker: ĐÃ SỬA.** Ảnh có `libpq5`; env/khóa/role DB do `xt-init` + `make env`; `restart: on-failure:5`. Phản hồi MCP thật với token Desk (`tools/list` qua `xt-mcp.freepro.online/mcp`): **HTTP 200**, trả danh sách tool. Dòng `http 405 .../mcp` trong log trình duyệt là `GET /mcp` (kênh SSE tùy chọn) mà server không hỗ trợ, hợp lệ theo MCP Streamable HTTP.
3. **aud `account`: ĐÃ SỬA.** Claims thật: `"aud":["https://xt-mcp.freepro.online/mcp","xt-api-introspect"]`, `azp=xt-desk-agent`, scope `openid mcp-xt profile email`. `xt-api-introspect` giữ vì Keycloak 26 cần cho introspection (`identity.md`).

Chưa kiểm: `postgrest`, `xt-desk` container (e2e phục vụ bản build Desk qua Playwright), tool call có dữ liệu tenant (chưa tạo tenant).
