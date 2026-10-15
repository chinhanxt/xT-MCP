# Spike HITL — kênh duyệt của con người mà agent không giả được (L2/L3)

Ngày: 2026-10-15. Keycloak **26.6.3** (image `quay.io/keycloak/keycloak:26.6.3` có sẵn, không pull mới), container `xt-hitl-kc` cổng **19300**, authentication-channel Python cổng **19301**. Thư mục làm việc `/tmp/xt-spike-hitl` (venv: PyOTP 2.10.0, httpx 0.28.1, PyJWT 2.15.1, cryptography 50.0.2). "Trình duyệt" là `httpx.Client` có cookie jar, chạy headless, không đụng display thật. Đã xóa container và thư mục tạm sau khi chạy.

Mã nguồn: `research/spike-hitl/` (mọi file có SPDX Apache-2.0). Output đầy đủ của lần chạy cuối: `research/spike-hitl/run-output.txt`, log channel: `research/spike-hitl/channel.log.txt`.

| File | Dòng | Vai trò |
|---|---|---|
| `common.py` | 30 | Hằng số, `verify_kc()` kiểm RS256 bằng JWKS (`cache_keys=False`, theo vuln #3 ở `verification-spikes.md`) |
| `kc_setup.py` | 121 | Realm `xt-hitl`: chính sách CIBA, user alice/bob, TOTP đã biết, flow step-up LoA, AMR, 4 client. Chỉ dùng Admin REST |
| `channel.py` | 84 | Endpoint authentication-channel cho CIBA và webhook Telegram giả lập |
| `ciba_flow.py` | 118 | A: luồng CIBA đầy đủ và các phép thử giả mạo |
| `ciba_acr_probe.py` | 18 | A6: thử CIBA với `acr_values=otp` |
| `stepup_flow.py` | 170 | B: step-up PKCE với `prompt=login`, `max_age=0`, OTP và các phép thử giả mạo |
| `receipt.py` | 89 | C: biên nhận Ed25519 dùng một lần, demo replay và giả mạo |
| `run_all.sh` | 25 | Chạy lại toàn bộ: `bash run_all.sh`. Dọn: `bash run_all.sh clean` |

```bash
df -h /                       # 8.5G trống (84%) — không pull gì
bash research/spike-hitl/run_all.sh     # rc=0, ~2,5 phút (có 2–3 lần chờ cửa sổ TOTP 30 s)
bash research/spike-hitl/run_all.sh clean
```

Keycloak chạy với SPI channel (cú pháp tùy chọn mới `--spi-<spi>--<provider>--<prop>` của 26.x):
```
start-dev --spi-ciba-auth-channel--ciba-http-auth-channel--http-authentication-channel-uri=http://host.docker.internal:19301/ciba/auth-channel
```

## Mô hình kẻ tấn công

Agent (Claude in Chrome, ChatGPT agent, computer-use) có các khả năng sau:
- giữ token MCP của chính nó, kể cả token mang `sub=alice`;
- lái trình duyệt đang đăng nhập Desk của alice: dùng cookie SSO, autofill mật khẩu, sửa URL, đọc URL redirect;
- gọi HTTP tùy ý.

Agent **không** có: điện thoại của alice (app TOTP, Telegram trên điện thoại), client secret của backend xT, khóa ký của xT.

## Cấu hình Keycloak (`kc_setup.py`)

- **Realm.** `cibaBackchannelTokenDeliveryMode=poll`, `cibaExpiresIn=120`, `cibaInterval=2`, `cibaAuthRequestedUserHint=login_hint`, `acr.loa.map={"urn:xt:acr:pwd":1,"urn:xt:acr:otp":2}`.
- **TOTP với secret đã biết.** Tạo qua `POST /users` với `credentials:[{type:"otp", secretData:'{"value":"<raw>"}', credentialData:'{"subType":"totp","digits":6,"period":30,"algorithm":"HmacSHA1"}'}]`. Keycloak lưu **chuỗi thô**, nên pyotp phải dùng `base32(raw.encode())`. Kết quả: `alice credentials: ['password', 'otp']`.
- **Flow `xt-stepup`** (gắn qua `authenticationFlowBindingOverrides.browser` của `xt-confirm`):
  ```
  Cookie ALTERNATIVE | xt-auth ALTERNATIVE
    xt-loa1 CONDITIONAL: Condition-LoA(level 1, max-age 36000) + Username Password Form
    xt-loa2 CONDITIONAL: Condition-LoA(level 2, max-age 0)     + OTP Form
  ```
- **AMR.** Mapper `oidc-amr-mapper` chỉ ra giá trị khi execution có config `default.reference.value` (`pwd` hoặc `otp`). Với `default.reference.maxAge=0`, bước mật khẩu bị **loại khỏi amr** nếu người dùng mất hơn 0 s mới nhập OTP. Lần chạy đầu cho `amr:["otp"]`, sau khi đặt 300 thì ra `["pwd","otp"]`.
- **Các client:**

| Client | Kiểu | Grant được bật | Ghi chú |
|---|---|---|---|
| `xt-backend-ciba` | confidential | **chỉ CIBA** (`oidc.ciba.grant.enabled`); tắt standard, direct, service-account | Mọi token có `azp=xt-backend-ciba` chắc chắn đến từ CIBA |
| `xt-agent` | confidential | direct + client_credentials | Mô phỏng token MCP của agent |
| `xt-desk` | public, PKCE | standard | Phiên Desk thường ngày |
| `xt-confirm` | public, PKCE S256 bắt buộc | standard | Client riêng cho trang confirm. `minimum.acr.value=urn:xt:acr:otp`, flow `xt-stepup` |

---

## A. Keycloak CIBA + authentication channel (`ciba_flow.py`)

Luồng: backend gọi `POST /ext/ciba/auth`. Keycloak `POST` sang channel với `Authorization: Bearer <JWT do KC ký>` và body JSON. Channel gửi tin Telegram (spike ghi vào outbox) có `callback_data` ký HMAC `(pending_id, chat_id, decision)`. alice bấm nút, Telegram gọi webhook (header `X-Telegram-Bot-Api-Secret-Token`). Channel kiểm secret, `chat_id` và chữ ký, đánh dấu đã dùng, rồi gọi `POST /ext/ciba/auth/callback {"status":"SUCCEED"}` bằng đúng Bearer đó. Backend poll `grant_type=urn:openid:params:grant-type:ciba`.

**A0. `binding_message` tiếng Việt theo đề bài bị Keycloak từ chối:**
```
400 {"error":"invalid_binding_message","error_description":"the binding_message value has to be max 50 characters in length and must contain only basic plain-text characters without spaces"}
```
Regex trong `BackchannelAuthenticationEndpoint` (đọc từ jar 26.6.3) là `^[a-zA-Z0-9-._+/!?#]{1,50}$`. **Cách xử lý:** `binding_message = "R-42#" + sha256(request)[:8]` làm mã đối chiếu. Nội dung tiếng Việt đầy đủ do channel tự tra từ DB của xT và hiển thị. Keycloak chuyển nguyên `binding_message`, `login_hint`, `scope`, `acr_values` sang channel:
```
[channel] from Keycloak: {"scope": "openid profile roles email", "binding_message": "R-42#50cfaf56", "login_hint": "alice", "is_consent_required": false}
```

**A1. Xin xác thực:**
```
200 {"auth_req_id":"eyJhbGciOiJkaXIi…(cắt)", ...}
auth_req_id là JWE, header: {"alg":"dir","enc":"A128CBC-HS256"} | số phần: 5
poll trước khi duyệt: 400 {"error":"authorization_pending", ...}
tin nhắn Telegram: "Duyệt tạm ứng 12.000.000đ #R-42 (bob, công tác Đà Nẵng)\nMã đối chiếu: R-42#50cfaf56"  [Duyệt] [Từ chối]
```

**A2. Agent tấn công trong lúc chờ (đều thất bại):**
```
agent tự gọi backchannel auth: 401 {"error":"invalid_grant","error_description":"Client not allowed OIDC CIBA Grant"}
agent poll bằng auth_req_id bị lộ: 400 {"error":"invalid_grant","error_description":"Client not allowed OIDC CIBA Grant"}
agent password-grant cho alice (không OTP): False invalid_grant Invalid user credentials
client CIBA cấp token qua grant password: 400 {"error":"unauthorized_client","error_description":"Client not allowed for direct access grants"}
client CIBA cấp token qua grant client_credentials: 401 {"error":"unauthorized_client","error_description":"Client not enabled to retrieve service account"}
agent gọi KC callback bằng token của nó: 403 {"error":"invalid_token","error_description":"Invalid token"}
agent gọi KC callback bằng auth_req_id: 403 {"error":"invalid_token","error_description":"Invalid token"}
webhook giả, sai secret_token: 403 {"error": "bad secret_token"}
webhook đúng secret, sai chat_id: 403 {"error": "chat_id không khớp người được hỏi"}
```

**A3. alice bấm Duyệt:**
```
webhook: 200 {"kc_callback": 200, "status": "SUCCEED"}
bấm lại (replay callback_data): 409 {"error": "unknown or used"}
poll: 200
access_token (đã kiểm chữ ký): sub=2001640d-…, preferred_username=alice, azp=xt-backend-ciba,
  auth_time=1792036095, iat=1792036095, acr="urn:xt:acr:pwd", amr=[], jti="onrtci:70cd…"
id_token: cùng sub, auth_time, acr="urn:xt:acr:pwd", amr=[]
auth_time - lúc hỏi = 3s
poll lần 2 (auth_req_id dùng lại): 400 {"error":"invalid_grant","error_description":"Invalid auth_req_id"}
```

**A4. Token của agent nộp làm bằng chứng (`verify_ciba_approval`: kiểm `azp`, người được hỏi và `auth_time ≥ lúc hỏi`):**
```
[REJ] agent alice password-grant: azp=xt-agent != xt-backend-ciba  (azp=xt-agent sub=alice acr=urn:xt:acr:pwd amr=[])
[REJ] agent service-account: azp=xt-agent != xt-backend-ciba  (azp=xt-agent sub=service-account-xt-agent ...)
```

**A5. Từ chối:** `webhook Từ chối: 200 {"status": "UNAUTHORIZED"}` → `poll: 400 {"error":"access_denied","error_description":"not authorized"}`.

**A6. Gửi `acr_values=urn:xt:acr:otp` (`ciba_acr_probe.py`):** channel nhận được `"acr_values": "urn:xt:acr:otp"`, nhưng token vẫn trả `acr: urn:xt:acr:pwd, amr: []`.

### Kết luận A
- **Đúng:** token CIBA chứng minh "Keycloak đã hỏi alice cho đúng `auth_req_id` này, và channel báo SUCCEED lúc T". Có `sub`, `auth_time` = thời điểm callback, `azp` của client chỉ-CIBA. Token của agent không dùng thay được, kể cả khi mang `sub=alice`. `auth_req_id` là JWE gắn với client, dùng một lần.
- **Giới hạn cốt lõi:**
  - Trong CIBA, Keycloak **không tự xác thực người dùng**. `acr` giữ mức mặc định, `amr` rỗng, `acr_values` chỉ được chuyển tiếp chứ không được thực thi.
  - Vì vậy độ tin của A **bằng đúng độ tin của channel** (Telegram), và chỉ có thêm phần đóng gói thành token chuẩn.
  - Lộ Bearer channel token (Keycloak ký, channel giữ) là đủ để gọi SUCCEED. Token này phải ở yên trong channel và không được ghi log.
  - Nếu alice đăng nhập Telegram Web hoặc Desktop trên máy agent đang lái, agent **bấm được nút**.
- `binding_message` không chứa được tiếng Việt hay dấu cách. Chỉ dùng làm mã đối chiếu.

---

## B. Step-up xác thực tươi trên trang confirm của xT (`stepup_flow.py`)

Confirmation `C-42` được tạo lúc `created_at`. `nonce` gắn với C-42. `code_verifier` chỉ nằm ở server xT. URL:
```
.../auth?response_type=code&client_id=xt-confirm&redirect_uri=http://localhost:19302/confirm/cb&scope=openid
  &state=…&nonce=1c4a12db…&code_challenge=…&code_challenge_method=S256&prompt=login&max_age=0&acr_values=urn:xt:acr:otp
```
`verify_confirm()` của xT kiểm các điều kiện sau trên id_token, sau khi kiểm chữ ký với `aud=xt-confirm`:
- `azp=xt-confirm`;
- `nonce` bằng nonce của confirmation;
- `auth_time ≥ created_at` và `now − auth_time ≤ 120 s`;
- `acr = urn:xt:acr:otp` và `"otp" ∈ amr`;
- đúng người duyệt.

**B0. Phiên Desk:** alice đăng nhập `xt-desk`. Kết quả: `azp=xt-desk, acr=urn:xt:acr:pwd, amr=["pwd","otp"]`. Trình duyệt giữ cookie SSO.

**B1. Agent lái trình duyệt (có cookie và autofill mật khẩu, không có OTP):**
```
mở URL: TRANG MẬT KHẨU             <- prompt=login: cookie SSO không được dùng
submit mật khẩu (autofill): TRANG OTP
đoán OTP 000000: TRANG OTP
đoán OTP 123456: TRANG OTP         <- không bao giờ ra code
```
**B2. Agent xóa `prompt`, `max_age`, `acr_values` khỏi URL:** `TRANG MẬT KHẨU`. `minimum.acr.value` và LoA2 `max-age 0` được ép **phía Keycloak**, không phụ thuộc URL.

**B3. Agent lấy token im lặng qua phiên Desk (`prompt=none`, client `xt-desk`, chép cả nonce của C-42):**
```
prompt=none trên xt-desk: REDIRECT có code
[REJ] verify_confirm(desk token): aud sai: Audience doesn't match
   (nếu bỏ qua kiểm aud) claims: {"azp": "xt-desk", "auth_time": 1792036111, "acr": "0", "amr": ["pwd","otp"], "nonce": "1c4a12db…"}
```
Đây là bài học quan trọng. **Nonce khớp, vì agent chép được.** `amr` vẫn ghi `otp` từ lần đăng nhập Desk, vì AMR maxAge 300 tính theo lúc xác thực gốc. Lớp chặn thật nằm ở `aud/azp=xt-confirm`, `acr` (lúc này là `"0"` = SSO), và `auth_time < created_at`. Vì vậy **không được** chỉ kiểm `amr`.

**B4. Người thật (mật khẩu + OTP trên điện thoại):**
```
mở URL: TRANG MẬT KHẨU / mật khẩu: TRANG OTP
   mã OTP hiện tại đã dùng trong cửa sổ này -> Keycloak từ chối tái dùng; chờ mã kế tiếp
OTP: REDIRECT có code
agent (thấy URL redirect) đổi code KHÔNG có verifier: 400 {"error":"invalid_grant","error_description":"PKCE code verifier not specified"}
agent đổi code với verifier đoán: 400 {"error":"invalid_grant","error_description":"Code not valid"}
-> code đã bị đốt; người thật duyệt lại (xT tạo PKCE mới, cùng C-42)
[OK ] xt-confirm id_token: azp=xt-confirm, auth_time=1792036171, acr="urn:xt:acr:otp", amr=["pwd","otp"], nonce=<C-42>
auth_time - created_at = 57s; now - auth_time = 0s
access_token: azp=xt-confirm, acr=urn:xt:acr:otp, amr=["pwd","otp"]
```
- Code đã bị agent thử đổi thì Keycloak đốt luôn (không cấp lại). Hệ quả: agent không cướp được code, nhưng **làm người thật phải duyệt lại** (DoS nhẹ).
- Keycloak chặn tái dùng cùng một mã TOTP. Nếu vừa đăng nhập Desk rồi confirm ngay trong cùng 30 s, người dùng phải chờ mã kế tiếp.

**B5. Ngay sau B4, phiên SSO đã đạt LoA2. Agent thử duyệt C-43 mà không có OTP:**
```
URL bỏ prompt/max_age/acr_values: TRANG OTP       <- LoA2 max-age 0: phiên LoA2 cũ không được tính
prompt=none: error=login_required
hạ acr_values=pwd: TRANG OTP                      <- minimum.acr.value thắng
[REJ] tái dùng id_token C-42 cho C-43: nonce không gắn với confirmation này
```

### Kết luận B
Bằng chứng là chính các claim Keycloak ký: `auth_time` tươi (0 s), `acr=urn:xt:acr:otp`, `amr=[pwd,otp]`, `azp/aud=xt-confirm`, `nonce` gắn với confirmation. Một cú bấm chỉ dựa vào phiên, không có OTP, bị chặn ở cả hai phía: Keycloak không ra code (B1, B2, B5), và xT không nhận token của client khác (B3).

**Rủi ro còn lại:**
1. **TOTP nằm trong password manager trên cùng máy** (1Password, Bitwarden autofill OTP) thì agent lấy được cả hai yếu tố. Phải quy định OTP chỉ nằm trên app điện thoại. Không ép được bằng TOTP, chỉ ép được bằng WebAuthn với authenticator rời hoặc qua điện thoại.
2. **Không có WYSIWYS:** trang login của Keycloak không hiển thị nội dung giao dịch. Agent lái trình duyệt có thể sửa DOM trang confirm để hiện "12 triệu" trong khi C-42 là 120 triệu. Chữ ký OTP không gắn với nội dung (khác với C ở phía server). Giảm thiểu bằng cách hiển thị tóm tắt và mã đối chiếu trên **thiết bị thứ hai** (Telegram) cùng lúc.
3. **Lừa người dùng:** agent hỏi "đọc giúp tôi mã OTP". TOTP không chống phishing, WebAuthn thì chống được.

---

## C. Biên nhận duyệt một lần (`receipt.py`)

xT chỉ phát biên nhận sau khi kiểm bằng chứng A hoặc B. Biên nhận là JWS **EdDSA (Ed25519)** gồm `{iss:"xt", jti, iat, exp(+300 s), request_id, request_digest=sha256(JSON canonical của request), decision, approver(sub), evidence{azp, auth_time, acr, amr, idp_jti}}`.

Lúc phát, xT kiểm `now − auth_time ≤ 120 s` và SoD (người duyệt ≠ người đề nghị). Lúc thực thi, xT tính lại digest từ bản ghi hiện tại và tiêu `jti` đúng một lần. Ở prod, `jti` được tiêu qua `UNIQUE(jti)` trong cùng transaction với hành động.

Kết quả **giống hệt** cho cả hai nguồn bằng chứng:
```
evidence (A): {"azp":"xt-backend-ciba","auth_time":1792036095,"acr":"urn:xt:acr:pwd","amr":[],"idp_jti":"onrtci:70cd…"}
evidence (B): {"azp":"xt-confirm","auth_time":1792036171,"acr":"urn:xt:acr:otp","amr":["pwd","otp"],"idp_jti":"b1b4d987-…"}
[OK ] 1. dùng đúng R-42: approver=alice request=R-42
[REJ] 2. replay lần 2 trên R-42: PermissionError: receipt đã dùng (replay)
[REJ] 3. dùng receipt R-42 cho R-43: PermissionError: receipt cho R-42, không phải R-43
[REJ] 4. R-42 bị sửa amount 12tr->120tr: PermissionError: request_digest không khớp: nội dung yêu cầu đã đổi sau khi duyệt
[REJ] 5. R-42 bị đổi tài khoản nhận: PermissionError: request_digest không khớp: ...
[REJ] 6. agent sửa payload receipt (giữ chữ ký cũ): InvalidSignatureError: Signature verification failed
[REJ] 7. receipt hết hạn: ExpiredSignatureError: Signature has expired
[REJ] 8. phát receipt từ bằng chứng auth_time 600s trước: PermissionError: auth_time cũ 600s > 120s
[REJ] 9. phát receipt khi người duyệt = người đề nghị: PermissionError: SoD: người duyệt = người đề nghị
```
**Lưu ý thiết kế:** `request_digest` phải được **chụp lúc tạo confirmation**, tức đúng thứ hiển thị cho người duyệt và mã trong `binding_message` hoặc tin Telegram. Không lấy digest từ bản ghi tại lúc phát biên nhận. Nếu không, agent có thể sửa request trong khoảng giữa "hỏi" và "duyệt".

---

## So sánh

| Tiêu chí | A. CIBA (channel = Telegram) | B. Step-up `xt-confirm` (PKCE + prompt=login + max_age=0 + TOTP) | Telegram-only (nonce ký, không CIBA) |
|---|---|---|---|
| Agent có token riêng, kể cả `sub=alice` | Chặn (`azp`, client chỉ-CIBA, A2/A4) | Chặn (`aud/azp`, `acr`, `auth_time`, B3) | Chặn nếu xT kiểm callback ký + chat_id |
| Agent lái trình duyệt đang đăng nhập (cookie, autofill mật khẩu) | Chặn, **trừ khi Telegram Web/Desktop đăng nhập trên máy đó** | Chặn (B1, B2, B5), **trừ khi TOTP nằm trong password manager cùng máy** | Như A |
| Bằng chứng do IdP ký về yếu tố xác thực | Không. `acr` mặc định, `amr=[]` (A3, A6). IdP chỉ chứng nhận "channel nói SUCCEED" | Có: `acr=otp`, `amr=[pwd,otp]`, `auth_time` | Không. Chỉ có log của xT |
| Hiển thị nội dung trên thiết bị thứ hai (chống sửa DOM) | Có (tin Telegram + mã `R-42#digest`) | **Không** | Có |
| UX | 1 chạm trên điện thoại (~3 s) | Mở link → mật khẩu (autofill) → 6 số OTP → gửi: ~4 thao tác, 15–25 s. Đụng cửa sổ 30 s thì phải chờ thêm | 1 chạm |
| Offline / demo | Cần Internet tới Telegram, bot token, webhook công khai (hoặc long-polling) | **Chạy offline hoàn toàn** (Keycloak local + app TOTP trên điện thoại) | Cần Internet |
| Khối lượng code (spike) | channel 84 + backend ~40 + cấu hình realm ~15 dòng. Phụ thuộc SPI CIBA của Keycloak | Kiểm token ~25 + PKCE/URL ~20 + cấu hình flow LoA/AMR ~45 dòng. Toàn tính năng OIDC chuẩn | ~60 dòng (webhook + nonce + chat link) |
| Điểm yếu riêng | Lộ Bearer channel = duyệt thay. `binding_message` chỉ ASCII ≤50 | Không gắn nội dung. OTP phishable | Chỉ có log của xT làm bằng chứng. Bot là confused deputy nếu thiếu nonce (critique #19) |

CIBA **không làm Telegram mạnh hơn**. Nó chỉ đóng gói kết quả của Telegram thành token chuẩn của IdP, và thêm phụ thuộc (SPI channel, regex `binding_message`).

## Khuyến nghị

1. **Biên bảo mật cho L2/L3: chọn B + C.**
   - Dùng client `xt-confirm` riêng với PKCE S256, flow LoA (OTP `max-age 0`), `minimum.acr.value=otp`, AMR reference maxAge 300.
   - xT kiểm `aud/azp=xt-confirm`, `nonce` của confirmation, `auth_time ≥ created_at` và `≤ 120 s`, `acr=otp`.
   - Sau đó xT phát biên nhận Ed25519 gắn `(request_id, digest chụp lúc tạo confirmation, approver, decision, exp)` và tiêu `jti` một lần trong cùng transaction với hành động.
   - Phương án này đã chứng minh chạy thật ở đây, chạy offline được cho chung kết, và chỉ dùng tính năng chuẩn của Keycloak.
2. **Telegram là kênh hiển thị và thông báo trên thiết bị thứ hai, không phải bằng chứng.**
   - Tin Telegram gửi tóm tắt tiếng Việt và mã `R-42#<8 hex digest>`. Trang confirm hiện cùng mã đó. Như vậy người dùng tự đối chiếu được nếu agent sửa DOM.
   - Chỉ cho Telegram một chạm làm bằng chứng ở L2 **dưới ngưỡng** khi policy tenant bật `allow_telegram_tap`. Biên nhận khi đó ghi `evidence.azp=telegram`, để audit thấy rõ độ mạnh. L3 và trên ngưỡng luôn đi B.
3. **CIBA: chưa đưa vào v1.**
   - Chỉ đáng giá khi channel tự xác thực mạnh (app điện thoại có passkey hoặc sinh trắc). Lúc đó token IdP mới có ý nghĩa hơn log của xT.
   - Giữ `channel.py` làm tham chiếu, ghi vào roadmap v1.x.
4. **Nâng cấp v1.0:** WebAuthn (authenticator rời hoặc hybrid qua điện thoại, cần HTTPS) thay TOTP ở LoA2. Cách này chống phishing và chống được việc TOTP nằm trong password manager. Flow LoA giữ nguyên, chỉ đổi execution.
5. **Sửa `architecture-v1.md` và D8:**
   - `confirm_url` chỉ là locator.
   - Không dựa vào tham số URL (B2 cho thấy Keycloak phải ép phía server).
   - Không kiểm `amr` đơn lẻ (B3).
   - `binding_message` chỉ là mã ASCII.
   - Ghi rõ OTP phải ở trên điện thoại.

## Dọn dẹp
- `docker rm -f xt-hitl-kc`, dừng `channel.py`, `rm -rf /tmp/xt-spike-hitl`.
- Không pull image mới. Dùng lại `keycloak:26.6.3`.
- Các container khác trên máy (`xtfp-*`, `xt-packs-pg`, …) không phải do spike này tạo nên không đụng tới.
