# Lớp danh tính (identity)

Phạm vi: kiểm access token cho MCP resource server, Protected Resource Metadata (RFC 9728), step-up cho `/desk/confirm/{id}`, biên nhận duyệt Ed25519 dùng một lần, và kiểm thu hồi token cho thao tác rủi ro cao. Nguồn quyết định: `architecture-v2.md` §5.1/§5.5, `architecture-v2-verdict.md` (N2, N3, N4, B3), `research/verification-spikes.md` (VS B), `research/spike-hitl.md` (phương án B + C), `research/connector-requirements.md` §2.

## Bố cục mã

| Module | Nội dung |
|---|---|
| `xt.domain.identity` | `Principal`, `PrincipalKind`, `StepUpEvidence`, `ConfirmationBinding`, `ApprovalReceipt`, `ApprovalDecision`, `request_digest()`, các lỗi (`InvalidTokenError`, `InsufficientScopeError`, `TokenRevokedError`, `StepUpRejectedError`, `SeparationOfDutiesError`, `ReceiptRejectedError`, `IdentityProviderUnavailableError`). Thuần, không I/O |
| `xt.domain.autonomy` | `RiskLevel` (L0–L3, `human_only`), `high_risk` |
| `xt.application.ports` | `SingleUseStore`, `TokenIntrospector`, `IntrospectionResult` |
| `xt.adapters.identity.config` | Đọc biến môi trường, gom mọi lỗi vào `IdentityConfigError` |
| `xt.adapters.identity.jwks` | `JwksCache` (TTL, refetch kid lạ có giới hạn tần suất), `HttpJwksFetcher`, `build_jwks_cache` |
| `xt.adapters.identity.jwt_core` | Kiểm header, chữ ký, `iss/aud/exp/nbf/iat/typ/sub`, trần tuổi token |
| `xt.adapters.identity.access_token` | `AccessTokenVerifier.verify(token, required_scopes, *, dpop=DpopContext) -> Principal` |
| `xt.adapters.identity.dpop` | DPoP (RFC 9449): `DpopVerifier`, `DpopSettings`/`load_dpop_settings`, `DpopError`, `normalize_htu`, `jwk_thumbprint` (RFC 7638) |
| `xt.adapters.identity.resource_metadata` | PRM, `WWW-Authenticate` (`Bearer` + `DPoP`), `challenge_for()`, `extract_credentials()`, `extract_bearer()` |
| `xt.adapters.identity.step_up` | `StepUpVerifier`, `ConfirmationContext`, PKCE, URL authorize của trang confirm |
| `xt.adapters.identity.receipts` | `ReceiptService.issue()` / `verify_and_consume()` |
| `xt.adapters.identity.single_use` | `InMemorySingleUseStore` (test, một tiến trình). Bản Postgres làm sau ở `adapters/db` |
| `xt.adapters.identity.introspection` | `KeycloakIntrospector`, `RevocationGuard`, `build_revocation_guard` |

Mọi thời gian dùng đồng hồ tiêm vào; PyJWT chỉ kiểm chữ ký với **một** thuật toán đã lọc từ header.

## Biến môi trường

Không có host, realm hay secret mặc định. Thiếu hoặc sai thì hàm `load_*_settings()` ném `IdentityConfigError` liệt kê đủ lỗi; gọi lúc khởi động để dừng tiến trình.

| Biến | Bắt buộc | Mặc định | Ý nghĩa |
|---|---|---|---|
| `XT_IDP_ISSUER` | có | | `iss` chính xác từng ký tự (tên miền công khai của Keycloak, ví dụ `https://auth.example.vn/realms/xt`) |
| `XT_IDP_JWKS_URL` | có | | URL JWKS; có thể là địa chỉ nội bộ khác `iss` (arch §5.1). Không tự suy từ issuer |
| `XT_IDP_ALLOW_HTTP` | không | `false` | Cho phép URL `http://` (chỉ dev/test) |
| `XT_IDP_ALGORITHMS` | không | `RS256,ES256` | Tập con của `{RS256, ES256}`; `none`, `HS*` và thuật toán khác bị từ chối lúc khởi động |
| `XT_IDP_LEEWAY_SECONDS` | không | `30` | Độ lệch đồng hồ cho `exp/nbf/iat` (0–120) |
| `XT_IDP_MAX_TOKEN_LIFETIME_SECONDS` | không | `900` | Trần `exp − iat` (khuyến nghị realm `accessTokenLifespan` = 300) |
| `XT_IDP_JWKS_TTL_SECONDS` | không | `300` | Sau TTL bỏ toàn bộ khóa và tải lại; kid đã xóa khỏi Keycloak hết được tin sau tối đa TTL |
| `XT_IDP_JWKS_MIN_REFETCH_SECONDS` | không | `10` | Khoảng tối thiểu giữa hai lần gọi JWKS (chặn khuếch đại bằng kid ngẫu nhiên); 1–3600, phải ≤ TTL |
| `XT_IDP_MAX_TOKEN_BYTES` | không | `16384` | Trần độ dài token (access/ID token), kiểm trước khi parse (1024–1048576). Biên nhận có trần cố định 8 KiB |
| `XT_IDP_HTTP_TIMEOUT_SECONDS` | không | `5` | Timeout gọi JWKS/introspection |
| `XT_IDP_TENANT_CLAIM` | không | `organization` | Claim của mapper Organization (list alias hoặc object `{alias: {...}}`) |
| `XT_MCP_RESOURCE` | có | | URL MCP = `aud` bắt buộc = `resource` trong PRM. Không query, không fragment; không đổi dấu `/` cuối so với URL đăng ký connector |
| `XT_MCP_ALLOWED_CLIENTS` | có | | Allowlist `azp` của client MCP (connector, agent Desk), phân cách dấu phẩy (VS #1, VERD N2). Token của các client này luôn là `agent` |
| `XT_MCP_CLIENT_REGISTRY` | không | `static` | `static`: chỉ allowlist tĩnh. `keycloak`: thêm allowlist động cho connector DCR (mục "Connector DCR"). Khi `keycloak`, `XT_MCP_ALLOWED_CLIENTS` được phép rỗng |
| `XT_IDP_ADMIN_API_URL` | khi `keycloak` | | Admin REST của realm, ví dụ `http://keycloak:8080/admin/realms/xt` (mạng nội bộ; APISIX chặn `/admin` từ ngoài) |
| `XT_IDP_ADMIN_TOKEN_URL` | khi `keycloak` | | Token endpoint để service account lấy token client_credentials |
| `XT_IDP_ADMIN_CLIENT_ID` / `_SECRET` | khi `keycloak` | | Service account **chỉ có** `realm-management/view-clients` |
| `XT_MCP_CONNECTOR_REDIRECT_HOSTS` | khi `keycloak` | | Host redirect của connector được nhận, ví dụ `chatgpt.com,claude.ai,localhost`. So khớp chính xác host |
| `XT_MCP_CONNECTOR_CACHE_SECONDS` | không | `60` | Cache dương (1–300). Disable client có hiệu lực trong tối đa từng ấy giây |
| `XT_MCP_CONNECTOR_NEGATIVE_CACHE_SECONDS` | không | `30` | Cache âm (1–300) |
| `XT_MCP_CONNECTOR_LOOKUPS_PER_MINUTE` | không | `60` | Trần số lần tra Admin REST mỗi phút (token bucket). Vượt trần thì fail-closed |
| `XT_MCP_HUMAN_CLIENTS` | không | rỗng | Client phiên web Desk được gọi resource server với tư cách `human`. Không được trùng `XT_MCP_ALLOWED_CLIENTS` |
| `XT_MCP_REQUIRED_SCOPES` | có | | Scope bắt buộc cho mọi request (phân cách khoảng trắng), ví dụ `mcp-xt` |
| `XT_MCP_SCOPES_SUPPORTED` | có | | `scopes_supported` của PRM; phải chứa scope bắt buộc; cấm `offline_access` |
| `XT_CONFIRM_CLIENT_ID` | có (step-up) | | Client Keycloak của trang confirm (TCV1 C7: `xt-desk`; spike-hitl dùng client riêng `xt-confirm`) |
| `XT_CONFIRM_ACR` | có (step-up) | | Mức `acr` OTP, ví dụ `urn:xt:acr:otp` |
| `XT_CONFIRM_MAX_AUTH_AGE_SECONDS` | không | `300` | Cửa sổ tươi của `auth_time` (arch v2.1 mục 5) |
| `XT_CONFIRM_REQUIRED_AMR` | không | rỗng | Giá trị phải có trong `amr`, ví dụ `otp`. Chỉ là kiểm bổ sung, không thay `acr` |
| `XT_RECEIPT_ISSUER` | có (biên nhận) | | `iss` của biên nhận |
| `XT_RECEIPT_SIGNING_KEY_FILE` | có (biên nhận) | | Khóa riêng Ed25519 PKCS#8 PEM. Tạo: `openssl genpkey -algorithm ed25519 -out receipt.pem` |
| `XT_RECEIPT_TTL_SECONDS` | không | `300` | Hạn biên nhận |
| `XT_IDP_INTROSPECTION` | có | | `off` hoặc `high_risk`. Phải chọn rõ, không có mặc định |
| `XT_IDP_INTROSPECTION_URL` | khi `high_risk` | | `…/protocol/openid-connect/token/introspect` |
| `XT_IDP_INTROSPECTION_CLIENT_ID` / `_SECRET` | khi `high_risk` | | Client confidential của xt-api. Đặt các biến này khi `off` là lỗi cấu hình |
| `XT_IDP_INTROSPECTION_CACHE_SECONDS` | không | `30` | Cache kết quả theo SHA-256 của token, tối đa 60 |
| `XT_DPOP_REQUIRED` | không | `false` | `true`: client trong `XT_DPOP_CLIENTS` **phải** dùng token gắn DPoP (Bearer thường bị 401), vé `/pii/reveal` chỉ gắn khóa DPoP. Token có `cnf.jkt` luôn bị kiểm proof, bất kể cờ. `provision.py` dùng cùng cờ cho `dpop.bound.access.tokens` |
| `XT_DPOP_CLIENTS` | không | `XT_DESK_AGENT_CLIENT_ID` hoặc `xt-desk-agent` | Client bị cờ trên ràng buộc (phân cách dấu phẩy) |
| `XT_DPOP_ALGORITHMS` | không | `ES256` | `alg` nhận cho proof; tập con của `{ES256, ES384, RS256, PS256}`. `none`/`HS*` bị từ chối lúc khởi động |
| `XT_DPOP_PROOF_MAX_AGE_SECONDS` | không | `60` | Tuổi tối đa của proof theo `iat` (1–300), cộng thêm `XT_DPOP_LEEWAY_SECONDS` |
| `XT_DPOP_LEEWAY_SECONDS` | không | `30` | Lệch đồng hồ cho `iat` (0–120): tương lai ≤ leeway, quá khứ ≤ max_age + leeway |
| `XT_DPOP_ORIGINS` | không | rỗng | Origin công khai bổ sung cho `htu` (vd dev `http://localhost:5173`; http cần `XT_IDP_ALLOW_HTTP`). Luôn gồm origin của `XT_MCP_RESOURCE` và `XT_DESK_URL` |

## Access token cho MCP resource server

`AccessTokenVerifier.verify()` kiểm theo thứ tự:

1. JWS compact 3 phần (JWE 5 phần bị từ chối). Header `alg` thuộc allowlist, nên `none` và `HS256` ký bằng public key bị loại trước khi đụng khóa. `kid` bắt buộc. Header `typ` nếu có phải là `JWT` hoặc `at+jwt`. Mọi `crit` bị từ chối.
2. Khóa lấy từ `JwksCache` theo `kid`. Chỉ nhận khóa ký `RSA ≥ 2048 bit` hoặc `EC P-256`. Khóa `use=enc`, `oct`, kid trùng thì bỏ. Kiểu khóa phải khớp `alg` (chống đổi `alg` sang ES256 với kid RSA).
3. `iss` bằng đúng `XT_IDP_ISSUER`. Có `exp`, `iat`; `nbf` nếu có. Leeway áp cho cả ba. `exp − iat` không vượt trần.
4. `typ` (claim Keycloak) = `Bearer`, hoặc `DPoP` (token gắn khóa, bắt buộc có `cnf.jkt`), nên refresh token (`Refresh`, và ở Keycloak còn ký HS512) và ID token (`ID`) bị từ chối. `sub` bắt buộc.
5. `aud` chứa `XT_MCP_RESOURCE`. `azp` thuộc allowlist.
6. Scope: `XT_MCP_REQUIRED_SCOPES` ∪ `required_scopes` của thao tác. Thiếu thì ném `InsufficientScopeError` (403). Mọi lỗi ở bước 1–5 là `InvalidTokenError` (401), mang `reason` ổn định (`alg_not_allowed`, `unknown_kid`, `bad_signature`, `issuer`, `audience`, `client_not_allowed`, `expired`, `not_yet_valid`, `iat_in_future`, `lifetime_exceeded`, `token_type`, `missing_claim:<tên>`…).

7. DPoP (mục dưới): token có `cnf.jkt` chỉ hợp lệ với `Authorization: DPoP` + proof đúng khóa; cờ `XT_DPOP_REQUIRED` buộc client Desk phải có token gắn khóa.

`Principal`: `subject` (`sub` của token), `kind` và `on_behalf_of` **suy từ client `azp`**, không từ loại grant (arch v2.2, TCV1 C7):
- `azp` ∈ `XT_MCP_ALLOWED_CLIENTS`: `agent`. Token auth-code thì `on_behalf_of` = `sub` của người đã đăng nhập. Token service account (claim `client_id` = `azp`, Keycloak 25+) thì `on_behalf_of` = None.
- `azp` ∈ `XT_MCP_HUMAN_CLIENTS` (phiên web Desk): `human`, `on_behalf_of` = None. Token service account của client này bị từ chối (`service_account_not_human`).
- Client khác: `client_not_allowed`. Client confirm không đi qua đây: ID token của nó chỉ được nhận ở `StepUpVerifier`.

Các trường còn lại: `client_id` (= `azp`), `issuer`, `username`, `organizations` + `tenant` (đúng một Organization, nhiều thì `tenant_ambiguous`), `roles` (`realm_access.roles`), `scopes`, `auth_time`, `acr`, `amr`, `token_id`, `issued_at`, `expires_at`, `dpop_jkt` (thumbprint khóa DPoP đã kiểm; None với Bearer, đi vào `Actor.dpop_jkt`). Vai trò nghiệp vụ vẫn lấy từ bảng tổ chức của xT (arch §5.1); `roles` trong token chỉ để tham khảo.

### JWKS và xoay khóa

- Gọi JWKS nằm ngoài lock theo kiểu single-flight: request dùng kid đã biết không bao giờ chờ mạng; request cần kid lạ thì chờ đúng lần fetch đang chạy.
- Hết TTL thì bỏ cả bộ khóa rồi tải lại. Kid biến mất khỏi Keycloak hết được tin sau tối đa `XT_IDP_JWKS_TTL_SECONDS` (sửa VS #3; `PyJWKClient(cache_keys=True)` tin kid cũ tới khi restart).
- Gặp kid lạ thì refetch ngay nếu lần gọi trước đã cách ít nhất `XT_IDP_JWKS_MIN_REFETCH_SECONDS`. 20 kid ngẫu nhiên trong một cửa sổ chỉ gây 1 lần gọi.
- IdP không tới được: còn trong TTL thì dùng bộ khóa hiện có; hết TTL thì **fail-closed** (`IdentityProviderUnavailableError`, tầng HTTP trả 503), không dùng khóa cũ.
- Xoay khóa theo thứ tự passive → active (arch §5.1 mục 5). Nếu kích hoạt khóa mới ngay, token ký bằng khóa mới có thể bị 401 tối đa `XT_IDP_JWKS_MIN_REFETCH_SECONDS` (VS #5).
- Lý do tự viết thay vì `PyJWKClient(cache_keys=False, lifespan=…)` (VERD N4): cần tiêm đồng hồ để test xác định, lọc khóa `enc`/thuật toán ngoài allowlist, và giới hạn refetch rõ ràng.

## DPoP (RFC 9449) cho Desk

Quyết định: `docs/decisions.md` "DPoP cho Desk trước v1.0". Client public `xt-desk-agent` giữ khóa ECDSA P-256 **non-extractable** trong trình duyệt (`desk/src/dpop.ts`, IndexedDB). Keycloak 26.6 gắn token vào khóa khi token request có header `DPoP` (access token `typ=DPoP`, `cnf.jkt`; refresh token của client public cũng gắn khóa). Token bị trích khỏi trình duyệt là vô dụng nếu thiếu khóa riêng.

Resource server (`/mcp`, `AuthGate` → `IdentityHttpAuth` → `AccessTokenVerifier`):

| Token \ request | `Bearer` | `DPoP` + proof hợp lệ |
|---|---|---|
| có `cnf.jkt` | 401 `DPoP error="invalid_token"` (`dpop_bearer_scheme`, chống hạ cấp) | nhận, `Principal.dpop_jkt = jkt` |
| không `cnf.jkt` | nhận; **trừ** client `XT_DPOP_CLIENTS` khi `XT_DPOP_REQUIRED=true` (`dpop_required`) | 401 (`dpop_unbound_token`) |

Bề mặt không có ngữ cảnh DPoP (REST `/workflow/v1`, `/files/v1`, stdio) luôn từ chối token gắn khóa (`dpop_unsupported`, fail-closed).

`DpopVerifier.verify()` theo RFC 9449 §4.3, lỗi nào cũng là `DpopError` (401 `invalid_dpop_proof`):
1. Đúng **một** header `DPoP`, JWS compact ≤ 8 KiB.
2. Header `typ=dpop+jwt`; `alg` ∈ `XT_DPOP_ALGORITHMS` (không `none`, không HMAC); không `crit`.
3. `jwk` là khóa **công khai**: không thành viên riêng (`d`, `p`, `q`, `k`...), `kty`/`crv` khớp `alg`, tọa độ EC base64url **chuẩn tắc** đúng độ dài (cùng lớp với R2-02), RSA ≥ 2048.
4. Chữ ký đúng với chính `jwk` đó.
5. `jti` chuỗi 1–256 ký tự; `htm` == method (phân biệt hoa thường); `htu` sau chuẩn hóa (bỏ query/fragment, scheme/host chữ thường, bỏ cổng mặc định) thuộc `{origin + path}` với mọi origin công khai (origin của `XT_MCP_RESOURCE`, `XT_DESK_URL`, `XT_DPOP_ORIGINS`: Desk gọi `/mcp` cùng origin qua APISIX nên `htu` là URL Desk).
6. `iat` số: `now − max_age − leeway ≤ iat ≤ now + leeway`.
7. `ath` == base64url(SHA-256(access token)) (với `/pii/reveal`: hash của vé).
8. Thumbprint RFC 7638 của `jwk` == `cnf.jkt` (so sánh hằng thời gian).
9. Cuối cùng mới tiêu `jti` (khóa `dpop|jkt|jti`, hết hạn sau cửa sổ iat): proof sai không đốt jti của proof thật; phát lại bị `dpop_replay`. Cache trong bộ nhớ của tiến trình (`xt-api` chạy một tiến trình; nhiều replica cần store chung).

Challenge: `WWW-Authenticate: DPoP error="…", error_description="…", algs="ES256", Bearer resource_metadata="…", scope="…"` (vẫn có PRM cho client MCP). Server không phát `DPoP-Nonce`.

Keycloak (`deploy/keycloak/provision.py`, `desk_agent_client_rep`): attribute client `dpop.bound.access.tokens` (`OIDCConfigAttributes.DPOP_BOUND_ACCESS_TOKENS`, "Require DPoP bound tokens") = `XT_DPOP_REQUIRED`. Tắt: Desk gửi proof vẫn nhận token gắn khóa, Keycloak không ép. Bật: token request thiếu proof bị 400. CORS token endpoint của Keycloak đã cho phép header `DPoP`.

## Connector DCR: allowlist động

Connector đăng ký bằng DCR (Claude, ChatGPT) có `client_id` ngẫu nhiên, nên không thể khai trước trong `XT_MCP_ALLOWED_CLIENTS`. Khi `XT_MCP_CLIENT_REGISTRY=keycloak`, `AccessTokenVerifier` nhận `clients=build_client_allowlist(rs, load_connector_registry_settings())`. Port nằm ở `xt.application.ports`: `ClientDirectory`, `ClientAllowlist`. Adapter là `xt.adapters.identity.client_registry`.

Thứ tự xét một `azp` (chỉ sau khi chữ ký, `iss`, `aud`, `exp` đã đạt):

1. **Allowlist tĩnh first-party** trước: `XT_MCP_ALLOWED_CLIENTS` → `agent`, `XT_MCP_HUMAN_CLIENTS` (Desk) → `human`. Không gọi Keycloak.
2. Nếu không nằm trong allowlist tĩnh, xT tra `GET {XT_IDP_ADMIN_API_URL}/clients?clientId=<azp>&search=false` bằng service account `view-clients`. Client chỉ được nhận là connector (`agent`, `on_behalf_of=sub`) khi **mọi** điều kiện sau đúng (`connector_violation`, `xt.domain.identity`):
   - client tồn tại và khớp chính xác `clientId`, `enabled`, giao thức `openid-connect`;
   - public client (`publicClient=true`, không `bearerOnly`);
   - chỉ authorization code: `standardFlowEnabled=true`; không implicit, không `directAccessGrants` (password), không service account (client_credentials); không bật thuộc tính `*grant.enabled`/token exchange nào (device, CIBA, JWT grant…);
   - PKCE: thuộc tính `pkce.code.challenge.method=S256`. Keycloak tự gắn thuộc tính này khi DCR nếu realm có client policy `pkce-enforcer` với `auto-configure=true` (đã đo trên 26.6.3);
   - `fullScopeAllowed=false` (policy anonymous "Full Scope Disabled");
   - không có protocol mapper riêng của client;
   - có ít nhất một redirect URI. Mọi redirect phải không có `*`, không có userinfo/fragment, host nằm trong `XT_MCP_CONNECTOR_REDIRECT_HOSTS`, scheme `https`. `http` chỉ được nhận cho host loopback có trong danh sách.
3. Kết quả cache theo `client_id`: đạt thì cache `XT_MCP_CONNECTOR_CACHE_SECONDS`, không đạt thì cache âm `XT_MCP_CONNECTOR_NEGATIVE_CACHE_SECONDS`. Lỗi tra cứu (mạng, 401/403/5xx, phản hồi sai) không được cache và trả `IdentityProviderUnavailableError` (503, fail-closed), không dùng kết quả cũ đã hết hạn. Vượt `XT_MCP_CONNECTOR_LOOKUPS_PER_MINUTE` cũng trả 503. Gọi mạng nằm ngoài lock.

**Thu hồi connector:** admin disable client (hoặc xóa) trong Keycloak; xT từ chối trong tối đa `XT_MCP_CONNECTOR_CACHE_SECONDS`. Token đã cấp cho client đó cũng bị từ chối vì `azp` được kiểm ở mỗi request.

**Phân tách lớp:** allowlist động là lớp **kiểm độc lập** với chính sách DCR của Keycloak (`deploy/`, BUG-D1). Nếu Keycloak lỡ cấp client `client_credentials`/`password` qua DCR, xT vẫn từ chối (`not_public`/`service_account`/`password_grant`). Test tích hợp đã chứng minh điều này với một client `client_credentials` đăng ký ẩn danh.

**Service account least privilege:** tạo client confidential `xt-client-registry`, bật service account, chỉ gán client role `realm-management/view-clients`. Test tích hợp kiểm token của nó bị 403 khi đọc `/users` và khi sửa client.

## PRM và `WWW-Authenticate`

- PRM ở `protected_resource_metadata_path(resource)`. Với `https://host/mcp` thì đường dẫn là `/.well-known/oauth-protected-resource/mcp` (RFC 9728 §3.1). APISIX phải forward nguyên đường dẫn này. Nội dung: `resource`, `authorization_servers` (đúng một issuer), `scopes_supported`, `bearer_methods_supported: ["header"]`.
- Không có token: `401`, `WWW-Authenticate: Bearer resource_metadata="…", scope="mcp-xt"`. Không có `error` (RFC 6750 §3). Claude bắt buộc 401 phải có `resource_metadata`.
- Token sai: `401`, thêm `error="invalid_token"` và `error_description` công khai theo `reason`. Không lộ kid hay giá trị claim.
- Thiếu scope: `403`, `Bearer error="insufficient_scope", scope="<mọi scope thao tác cần>", resource_metadata="…"`. Theo MCP 2026-07-28: một challenge chứa đủ scope để client lấy hợp scope rồi xin lại token.
- `extract_bearer()` chỉ nhận header `Authorization: Bearer <token>`, không nhận token ở query. `/mcp` dùng `extract_credentials()` (nhận `Bearer` và `DPoP`).
- Lỗi DPoP: `401`, challenge `DPoP` đứng trước challenge `Bearer` (mục "DPoP").

## Step-up cho `/desk/confirm/{id}` (spike-hitl B)

Trang confirm chạy luồng auth-code + PKCE S256 (`new_pkce_pair()`, `confirm_authorization_url()` với `prompt=login`, `max_age=0`, `acr_values`, `nonce` của confirmation). `code_verifier` chỉ nằm ở server. Tham số URL chỉ là gợi ý. Keycloak phải ép phía server (B2):

- client confirm public, `pkce.code.challenge.method=S256`, `minimum.acr.value=<XT_CONFIRM_ACR>`;
- flow LoA gắn qua `authenticationFlowBindingOverrides.browser`: LoA1 mật khẩu, LoA2 OTP `loa-max-age=0`;
- realm `acr.loa.map`, mapper `oidc-amr-mapper` với `default.reference.maxAge` 300.

`StepUpVerifier.verify(id_token, ConfirmationContext)` kiểm **ID token**:

- chữ ký, `iss`, `exp/iat`, trần tuổi như access token;
- `typ=ID`, `aud` và `azp` bằng `XT_CONFIRM_CLIENT_ID`. Token im lặng của client khác bị loại dù nonce chép được và `amr` có `otp` (B3);
- `nonce` bằng nonce của confirmation; `acr` bằng `XT_CONFIRM_ACR`;
- `auth_time ≥ created_at − leeway` và `now − auth_time ≤ XT_CONFIRM_MAX_AUTH_AGE_SECONDS` (mặc định 300 s);
- `XT_CONFIRM_REQUIRED_AMR ⊆ amr`;
- `jti` bắt buộc và được tiêu một lần (`idt:<iss>:<jti>` qua `SingleUseStore`, hạn = `exp` + leeway). Mỗi lần đăng nhập step-up chỉ cho đúng một bằng chứng. Mặc định store in-memory; chạy nhiều tiến trình thì truyền bản Postgres;
- `sub` = người duyệt được chỉ định (nếu có); `sub ≠ requester` (`SeparationOfDutiesError`).

Kết quả là `StepUpEvidence`. Mọi lỗi, kể cả claim sai kiểu, là `StepUpRejectedError` với `reason`.

## Biên nhận duyệt Ed25519 (spike-hitl C)

- `issue(binding, evidence, decision)` kiểm lại SoD và độ tươi `auth_time`, rồi ký JWS `EdDSA`. Header `typ=xt-approval+jwt`, `kid` = RFC 7638 thumbprint. Claim: `iss, jti, iat, exp, sub` (người duyệt), `cid` (confirmation_id), `rdg` (request digest), `req` (người đề nghị), `decision`, `evidence{azp, acr, amr, auth_time, sid, idp_jti}`.
- `request_digest()` = SHA-256 của JSON chuẩn hóa RFC 8785 (JCS): khóa sắp theo UTF-16, chuỗi escape tối thiểu, UTF-8. Quy tắc cho tập con xT dùng:
  - khóa object phải là chuỗi;
  - số chỉ là số nguyên với |n| ≤ 2^53−1. **Tiền là số nguyên VND** (đơn vị nhỏ nhất). `Decimal` nguyên (NUMERIC từ Postgres) được nhận như số nguyên. `float` và `Decimal` có phần lẻ bị từ chối (`ValueError`), vì vậy digest không phụ thuộc đường đi qua DB. Giá trị thập phân thật (tỷ lệ, đơn giá lẻ) truyền dạng chuỗi do nghiệp vụ chuẩn hóa;
  - chuỗi chứa surrogate lẻ bị từ chối. Unicode không được chuẩn hóa trong hàm digest: NFC phải làm ở biên nhập liệu.
  Digest phải được **chụp lúc tạo confirmation**, đúng thứ hiển thị cho người duyệt.
- `verify_and_consume(receipt, confirmation_id, current_request_digest)`: chỉ nhận `alg=EdDSA`, đúng `typ` và `kid`; kiểm chữ ký, `iss`, hạn và trần `exp − iat`, `cid`, `rdg` (so hằng thời gian), `sub ≠ req`, `decision`. **Sau cùng** mới tiêu khóa `cid:<confirmation_id>` qua `SingleUseStore`, giữ vĩnh viễn: mỗi confirmation chỉ có **một** quyết định, dù phát bao nhiêu biên nhận (approve hoặc reject). Lần thứ hai trả `confirmation_already_decided`. Biên nhận sai yêu cầu không đốt khóa.
- Prod: `SingleUseStore` bản Postgres với `UNIQUE(confirmation_id)` trong bảng decisions, tiêu trong cùng transaction với mutation + audit.
- Thông báo lỗi (`IdentityError`) chỉ chứa giá trị một dòng, tối đa 160 ký tự (`safe_detail`). Giá trị header chưa kiểm chữ ký không thể chèn dòng log hay làm phình log. `repr` của settings không chứa secret hay khóa riêng; lỗi cấu hình URL có userinfo không in giá trị URL.

## Thu hồi token (VS #2, VERD N3)

JWT không thu hồi được trước `exp`. Lập trường:

- Realm đặt `accessTokenLifespan` = 300 s. Thao tác L0/L1 chấp nhận độ trễ thu hồi tối đa bằng tuổi token (≤ `XT_IDP_MAX_TOKEN_LIFETIME_SECONDS`).
- Thao tác L2, L3, `human_only`: dispatcher gọi `RevocationGuard.ensure_active(principal, token, level)`. Khi `XT_IDP_INTROSPECTION=high_risk`, guard hỏi introspection (RFC 7662) của Keycloak. Kết quả cache ≤ `XT_IDP_INTROSPECTION_CACHE_SECONDS` (≤ 60 s), đó cũng là độ trễ thu hồi tối đa. `active=false` → `TokenRevokedError` (401). `sub`/`client_id` lệch → 401. IdP lỗi → `IdentityProviderUnavailableError` (fail-closed).
- `XT_IDP_INTROSPECTION=off` phải đặt rõ. Khi đó độ trễ thu hồi của mọi thao tác bằng tuổi token.
- **Keycloak 26:** introspection trả `active:false` nếu client đang hỏi không nằm trong `aud` của token (log `Client '…' is not in the token audience`, đã gặp khi chạy test tích hợp). Scope `mcp-xt` phải có thêm mapper `oidc-audience-mapper` với `included.client.audience` = client introspection của xt-api.

## Cấu hình Keycloak bắt buộc (đã sửa so với spike)

1. Scope `mcp-xt` có audience mapper `included.custom.audience` = `XT_MCP_RESOURCE` và mapper audience cho client introspection. Scope này **không** nằm trong realm default. Chỉ gán cho client MCP đã duyệt (client policy cho client DCR, client agent khai trong `realm-xt.json`).
2. `XT_MCP_ALLOWED_CLIENTS` liệt kê đúng các `azp` đó. Client nội bộ mới (`xt-bot`, `xt-desk`…) bị từ chối mặc định kể cả khi lỡ có audience (fail-closed, test tích hợp `it-rogue`).
3. Connector DCR được nhận qua allowlist động (`XT_MCP_CLIENT_REGISTRY=keycloak`, mục "Connector DCR"). Realm cần client policy `pkce-enforcer` auto-configure cho DCR ẩn danh và service account `view-clients`.

## Kiểm thử

```sh
make test               # unit, khóa sinh cục bộ, không mạng (tests/unit/identity)
make test-integration   # Keycloak 26.6.3 thật trong docker, cổng 19400, tự xóa container
```

- Unit (`tests/unit/identity/`) phủ mọi ca âm VS B: `alg=none`, HS256 ký bằng public key RSA, đổi `alg` với kid RSA, payload bị sửa, khóa lạ cùng kid, `azp` lạ hoặc thiếu, `aud` sai, thiếu hoặc thêm `/`, `iss` sai hoặc thêm `/`, hết hạn, thiếu `exp`/`iat`, `nbf`/`iat` ở tương lai, `exp` +10 năm, `typ=Refresh`/`ID`, refresh/ID token làm bearer, thiếu `sub`, khóa đã xóa sau TTL, refetch kid lạ bị giới hạn tần suất, IdP sập sau TTL. Cùng với đó là step-up (client, acr, amr, auth_time, nonce, người duyệt, SoD), biên nhận (replay, sửa payload, hết hạn, sai confirmation, sai digest, khóa khác, `alg`/`typ` sai, SoD, bằng chứng cũ), introspection (cache, L0/L1 bỏ qua, revoked, lệch sub/client, IdP lỗi) và cấu hình.
- DPoP (`tests/unit/identity/test_dpop.py`, chạy với cờ tắt và bật): mọi ca âm RFC 9449 §4.3 (thiếu/hai header, không phải JWS, `typ` sai, `alg=none`/HS256/ngoài allowlist, `crit`, thiếu `jwk`/có khóa riêng/kty sai/tọa độ không chuẩn tắc, chữ ký khóa khác, payload sửa, `jti` thiếu/dài, `htm`/`htu` lệch, `iat` tương lai/cũ/không phải số, `ath` thiếu/sai/thừa, thumbprint ≠ `cnf.jkt`, replay, proof sai không đốt jti), vector RFC 7638, chuẩn hóa `htu`, §7.1 (hạ cấp Bearer, scheme DPoP với token không gắn, cờ bắt buộc), challenge. `tests/unit/mcp/test_mcp_dpop.py`: app MCP thật + verifier thật (JWKS tĩnh) qua HTTP. `tests/integration/pii_audit/test_pii_reveal_dpop.py`: `xt serve` + PG17 + IdP JWKS tĩnh, vé PII gắn jkt. `tests/integration/test_dpop_keycloak.py`: Keycloak 26.6.3 thật, client dựng bằng builder của `provision.py`, token/refresh gắn khóa, Keycloak từ chối proof sai/phát lại, xt kiểm proof.
- Allowlist động (`tests/unit/identity/test_client_registry.py`, Admin API giả qua `httpx.MockTransport`) gồm: 23 vi phạm chính sách, cache dương và thu hồi trong TTL, cache âm, rate limit, lỗi tra cứu fail-closed, client_id sai dạng, refresh token admin khi gặp 401. Test tích hợp `tests/integration/test_connector_registry.py` chạy trên Keycloak thật: client DCR đúng chính sách được nhận; client DCR `client_credentials`, client có thêm redirect host (Keycloak tin nhưng xT không) và client không tồn tại bị từ chối; service account `view-clients` không ghi được; client bị disable bị từ chối sau TTL.
- Tích hợp (`tests/integration/`) dựng realm bằng Admin REST, mật khẩu admin ngẫu nhiên mỗi lần chạy. Chứng minh trên token thật:
  - token user và service account của client allowlist được nhận, đúng `kind`;
  - `it-rogue` có aud nhưng bị từ chối `client_not_allowed`; `it-plain` không có aud;
  - refresh/ID token, payload sửa, token realm master, token hết hạn, vượt trần tuổi và `iss` lệch đều bị từ chối;
  - xoay khóa: kid mới được nhận ngay, kid đã xóa bị từ chối sau TTL;
  - user bị khóa: JWT vẫn qua chữ ký nhưng introspection L3 chặn;
  - luồng confirm PKCE + mật khẩu + TOTP: không có OTP thì không ra code; thiếu `code_verifier` bị Keycloak từ chối; ID token hợp lệ qua `StepUpVerifier`; token client khác, nonce khác và SoD bị chặn; biên nhận tiêu được một lần, replay và sai digest bị từ chối.
