<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Audit DPoP (RFC 9449) sau cờ `XT_DPOP_REQUIRED`, 16/10/2026

Phạm vi: `src/xt/adapters/identity/dpop.py`, `access_token.py` (`_check_dpop`), `single_use.py`,
`entrypoints/serve.py` (`IdentityHttpAuth`), `adapters/mcp/http_app.py` (`AuthGate`),
`entrypoints/composition/pii_reveal.py` (vé gắn `jkt`, `ath` = hash vé), `workflow_http.py`, `files_http.py`,
`desk/src/dpop.ts`, `oidc.ts`, `mcp.ts`, `pii.ts`, `deploy/keycloak/provision.py` (`dpop.bound.access.tokens`),
`deploy/compose.yaml`, `deploy/apisix/apisix.yaml.tmpl`, tài liệu `docs/identity.md` §DPoP,
`docs/integration-notes.md` 3b, `SECURITY.md`.

Môi trường: Keycloak 26.6.3 thật trong slot nặng (`free -g` available 8 GB, không có Keycloak/Ollama/compose nặng
nào khác), container test tự xóa (`docker rm -f -v`); PG17 + IdP JWKS tĩnh cho `/pii/reveal`; mọi thứ headless.

## 1. Chạy lại test

| Bộ test | Cờ | Kết quả |
|---|---|---|
| `tests/unit/identity/test_dpop.py`, `tests/unit/mcp/test_mcp_dpop.py` | tắt + bật (param) | 105 passed |
| `tests/integration/test_dpop_keycloak.py` (Keycloak 26.6.3 thật) | tắt + bật | 4 passed |
| `tests/integration/pii_audit/test_pii_reveal_dpop.py` (`xt serve` + PG17) | tắt + bật | 4 passed |
| `desk`: `vitest test/dpop.test.ts test/pii.test.ts` | | 24 passed |
| **Mới** `tests/unit/identity/dpop_audit/` (2 file) | tắt + bật | 65 passed, 5 xfailed (strict) |

`uv run pytest --no-cov --runxfail tests/unit/identity/dpop_audit` xác nhận 5 xfail đều hỏng đúng lý do
(ValueError/500, replay ở tiến trình thứ hai, logout không xoay khóa).

## 2. Kết quả tấn công

| # | Tấn công | Kết quả | Ghi chú |
|---|---|---|---|
| 1 | Phát lại proof cùng endpoint | PASS | `dpop_replay` (khóa cache `dpop|jkt|jti`) |
| 2 | Phát lại proof sang method khác (GET/DELETE) hoặc path khác (`/pii/reveal`) | PASS | `dpop_htm` / `dpop_htu` |
| 3 | Proof `/mcp` đem đổi vé `/pii/reveal` | PASS | `ath` khác (hash token vs hash vé) |
| 4 | Proof origin Desk phát lại ở origin MCP (cả hai được cấu hình) | PASS | được nhận lần đầu (đúng thiết kế), lần hai `dpop_replay`: cache chung trong tiến trình |
| 5 | htu: host/scheme hoa, `:443`, query, fragment | PASS | chuẩn hóa đúng RFC 9449 §4.3 bước 9 |
| 6 | htu: dấu `/` cuối, path hoa, `%6Dcp`, `//mcp`, `/./mcp`, host có dấu chấm cuối, cổng lạ, `http`, userinfo, htu tương đối | PASS | đều `dpop_htu` (fail-closed). `%`-encoding không được giải mã (RFC 3986 §6.2.2.2): chỉ ảnh hưởng tương thích, trình duyệt không sinh dạng này |
| 7 | Host header giả / origin lạ | PASS | tập htu dựng từ cấu hình (`XT_MCP_RESOURCE`, `XT_DESK_URL`, `XT_DPOP_ORIGINS`), không từ `Host`; `TrustedHost` chặn thêm. Sau APISIX/Cloudflare Tunnel: Desk ký `https://xt.freepro.online/mcp`, origin này có trong `XT_DESK_URL` |
| 8 | iat: +30 s nhận, +31 s từ chối; −90 s nhận, −91 s từ chối; `inf`, `-inf`, `1e30`, `true`, chuỗi | PASS | |
| 9 | iat = `NaN` (JSON `NaN` được `json.loads` nhận) | **FAIL (DPA-01)** | `int(NaN)` ném `ValueError` → 500 |
| 10 | jti: đổi hoa/thường, encoding của jti trong proof bị lộ | PASS | jti nằm trong phần ký → `dpop_signature`. Kẻ khác dùng cùng jti không đốt jti của chủ khóa (khóa cache có `jkt`). Proof sai không đốt jti |
| 11 | jti hết hạn trong cache trước khi proof hết hạn | PASS | hạn cache = iat + max_age + 2·leeway ≥ mép cửa sổ chấp nhận (kể cả iat tương lai) |
| 12 | Cache jti nhiều tiến trình / restart | **FAIL có điều kiện (DPA-02)** | xem §4 |
| 13 | Cache dưới tải | **FAIL nhẹ (DPA-04)** | quét O(n) mỗi lần `consume` |
| 14 | `alg=none`, HS256 (khóa công khai làm secret), `jwk` kiểu `oct` | PASS | `dpop_alg` / `dpop_jwk` |
| 15 | `RS256`/`PS256`/`ES384` với `jwk` EC P-256; `ES256` với `jwk` RSA; khóa P-384 với ES256 | PASS | `dpop_jwk` (kty/crv phải khớp alg) |
| 16 | `jwk` có `d` / `k`; `x` có đệm, sai độ dài; thiếu `y`; `use=enc`; điểm ngoài đường cong | PASS | |
| 17 | `jku`/`x5u`/`kid`/`x5c` nhúng; đổi `jwk` sau khi ký | PASS | bị bỏ qua, không fetch; `dpop_key_mismatch` / `dpop_signature` |
| 18 | `crit` | PASS | `dpop_crit` |
| 19 | Token gắn khóa gửi `Bearer` ở `/mcp` (kể cả kèm proof hợp lệ) | PASS | 401 `DPoP error="invalid_token"` (`dpop_bearer_scheme`) |
| 20 | Token gắn khóa ở `/workflow/v1`, `/files/v1` | PASS | `extract_bearer` + `verify()` không ngữ cảnh → `dpop_unsupported` (có hoặc không có `DpopVerifier`); ngoài ra `aud` của Desk không có `xt-workflow`/`xt-files` |
| 21 | Token gắn khóa ở `/data` (PostgREST) | PASS (nhờ `aud`) | PostgREST không hiểu `cnf`; chỉ an toàn vì token `xt-desk-agent` không bao giờ có `aud=xt-data` (`provision.py` `mcp_client_ids`). Token gắn khóa của client khác có `aud=xt-data` sẽ được nhận như Bearer |
| 22 | `/pii/reveal`: vé DPoP đổi bằng `{ticket,ts,sig}` / thiếu proof / khóa lạ | PASS | `DPOP_REQUIRED` / `BAD_DPOP_PROOF` (tiêu vé); không nhận `Authorization` |
| 23 | Scheme `DPoP` với token không gắn khóa | PASS | `dpop_unbound_token` |
| 24 | Cờ bật: Bearer không gắn khóa của `xt-desk-agent` | PASS | `dpop_required`; client MCP khác giữ Bearer |
| 25 | Refresh token không proof / proof khóa khác (Keycloak thật, cờ tắt và bật) | PASS | 400; chủ khóa refresh được, token mới cùng `cnf.jkt` |
| 26 | Desk: khóa riêng extractable? | PASS | `generateKey(..., false, ["sign","verify"])`, lưu nguyên `CryptoKey` trong IndexedDB, chỉ export khóa công khai |
| 27 | Desk: đăng xuất → khóa mới? người khác cùng profile trình duyệt | **FAIL (DPA-03)** | |
| 28 | XSS trong trang (rủi ro tồn dư) | PASS có ghi chú (DPA-05) | đã ghi trong SECURITY.md nhưng mô tả hẹp hơn thực tế |
| 29 | `DPoP-Nonce` | Không cần cho v1.0 | xem §5 |

## 3. Lỗi

### DPA-01 (Thấp): `iat = NaN` gây 500
`DpopVerifier.verify()`: `NaN > now + leeway` và `NaN < now - …` đều False nên NaN lọt qua kiểm iat; `int(iat)` khi tính hạn
cache ném `ValueError` nằm ngoài `try`, `AuthGate` không bắt → 500 ở `/mcp`; ở `/pii/reveal` cũng 500 (giao dịch rollback,
vé không bị tiêu). Chỉ chủ khóa mới kích hoạt được (iat kiểm sau `ath`/`jkt`), nên tác động là lỗi 500/log nhiễu, không
vượt quyền. Sửa: `math.isfinite(iat)` cùng chỗ kiểm kiểu. Test: `test_iat_nan_is_a_dpop_error`, `test_nan_iat_gets_401_not_500`.

### DPA-02 (Trung bình, có điều kiện): cache jti theo tiến trình
`build_dpop_verifier()` mặc định `InMemorySingleUseStore`. Hiện `xt-api` là **1 container, 1 tiến trình uvicorn** (không
`--workers`, không replica; cả `xt.freepro.online/mcp` và `xt-mcp.freepro.online/mcp` về cùng `xt-api:8000`), nên chống phát
lại có hiệu lực. Rủi ro định lượng:
- Restart (`restart: on-failure:5`, deploy): proof đã dùng trong ≤ 120 s trước restart (max_age 60 + 2·leeway 30) dùng lại được
  một lần, trong cửa sổ chấp nhận còn lại (≤ 90 s kể từ `iat`).
- N tiến trình/replica: mỗi proof dùng được tới N lần trong 90 s.
- Điều kiện khai thác: kẻ tấn công phải có **cả** access token **và** một proof đã dùng (log TLS-terminating ở Cloudflare/APISIX,
  extension trình duyệt, proxy debug). Proof chỉ ràng `htm`/`htu`/`ath`, **không ràng body**: phát lại `POST /mcp` = gọi tool
  tùy ý bằng token đó, nên một lần phát lại là đủ gây hại.
- `/pii/reveal` dùng một `DpopVerifier` riêng (cache riêng) nhưng không khai thác được: vé dùng một lần nguyên tử ở Postgres.

Test: `test_replay_rejected_by_second_process` (xfail strict), `test_default_replay_store_is_in_memory`.

### DPA-03 (Trung bình, Desk): đăng xuất không xoay khóa DPoP, không kết thúc phiên SSO
`OidcSession.logout()` chỉ xóa token trong bộ nhớ. `KeyStore` không có thao tác xóa; IndexedDB `xt-desk/keys/dpop` giữ nguyên
cặp khóa, không gọi `end_session_endpoint` hay revoke refresh token. Hệ quả trên cùng profile trình duyệt:
- Người dùng B đăng nhập sau A nhận token gắn **cùng `jkt`** với A: DPoP không tách người dùng, `jkt` thành định danh theo dõi
  xuyên tài khoản.
- Phiên SSO Keycloak của A còn sống: bấm "đăng nhập" lại có thể vào thẳng phiên A (không `prompt=login`), refresh token của A
  vẫn hợp lệ ở Keycloak tới khi hết hạn.
Sửa (desk): `KeyStore.clear()` + sinh khóa mới khi logout, gọi `end_session_endpoint` (`id_token_hint`) hoặc revoke refresh
token. Test: `test_desk_logout_rotates_dpop_key_and_ends_sso` (xfail strict, kiểm tĩnh mã Desk).

### DPA-04 (Thấp, hiệu năng): `InMemorySingleUseStore.consume()` quét toàn bộ dict dưới lock
Đo: 1 000 mục → 0,07 ms/lần, 12 000 → 0,74 ms, 120 000 → 9,1 ms. Số mục ổn định = 120 × RPS, nên trần ≈ 330 request/s
mỗi tiến trình trước khi lock bão hòa (luồng threadpool xếp hàng sau lock). APISIX giới hạn 30 r/s/IP (burst 60), cần ≥ 10 IP
có token + khóa hợp lệ để gây nghẽn. Sửa: heap theo hạn hoặc quét định kỳ (mỗi K lần/T giây). Không có test (đo thời gian dễ flaky).

### DPA-05 (Thấp, tài liệu): rủi ro tồn dư XSS mô tả hẹp
SECURITY.md: "XSS vẫn ký được proof bằng khóa … trong lúc trang mở". Thực tế không có nonce nên XSS **ký trước** proof với
`iat` tương lai và mang đi dùng sau khi đóng tab (`test_presigned_future_proof_accepted_later`: iat +3600 s bị từ chối lúc đầu,
không đốt jti, được nhận sau 1 giờ). Giới hạn thực tế là `ath`: proof ràng vào access token hiện tại, nên giá trị tồn dư kéo dài
tới `exp` của token đó (realm `accessTokenLifespan=300`, trần `XT_IDP_MAX_TOKEN_LIFETIME_SECONDS=900`). Proof ký sẵn cho token
endpoint + refresh token lấy được token mới, nhưng không có proof `/mcp` cho `ath` của token mới, nên không duy trì được truy cập
(chỉ phá phiên người dùng nhờ refresh rotation). Vé PII: `ath` = hash vé chưa biết trước, TTL 60 s. Đề nghị bổ sung câu này vào
SECURITY.md.

### DPA-06 (Thông tin, cấu hình): `XT_DPOP_CLIENTS` không truyền vào `xt-api`
`deploy/compose.yaml` không truyền `XT_DESK_AGENT_CLIENT_ID`/`XT_DPOP_CLIENTS` cho `xt-api` (chỉ dùng cho `XT_PII_REVEAL_CLIENTS`).
Đổi tên client Desk trong `.env` thì `provision.py` ép DPoP cho client mới, còn `required_for()` ở xt-api vẫn so với
`xt-desk-agent`, nên lớp chặn Bearer phía RS tắt lặng lẽ (Keycloak vẫn ép gắn khóa, nên còn một lớp).

### Ghi chú ngoài phạm vi
- Desk gửi token gắn khóa tới `/llm` bằng `Bearer` (`authHeaders()` không có request). Gateway LLM thấy token; vô hại khi token
  gắn khóa, nhưng với cờ tắt và token không gắn khóa thì gateway LLM bên thứ ba nhận được token MCP dùng được.
- `apisix.yaml.tmpl` không có route `/llm` cho host Desk: request rơi vào `desk-static` (nginx trả `index.html`).

## 4. Có cần cache jti dùng chung trước khi bật cờ?

**Không bắt buộc để bật cờ trên triển khai hiện tại** (1 tiến trình). **Bắt buộc trước khi** chạy `--workers > 1`, thêm
replica, hoặc nếu coi cửa sổ restart 120 s là không chấp nhận được. Lưu ý khi dùng lại `PgSingleUseStore`:
- Nó chạy trong `tenant_tx(uow.actor)` (RLS, `tenant_id = xt.tenant()`), còn kiểm DPoP chạy trong `AuthGate` trước khi có
  actor. Cần biến thể không theo tenant (bảng riêng `xt_meta.dpop_jti(key PK, expires_at)` không RLS, hoặc tenant suy từ claim
  `organization` của token đã kiểm chữ ký), kèm job xóa mục hết hạn.
- Chi phí: 1 `INSERT … ON CONFLICT` mỗi request `/mcp` (~1 ms), chấp nhận được ở giới hạn 30 r/s/IP.
- Khi DB lỗi phải fail-closed (401/503), không lùi về bộ nhớ.
- Thêm kiểm khởi động: phát hiện nhiều worker (`WEB_CONCURRENCY`, cấu hình replica) mà store là bộ nhớ thì từ chối chạy.

## 5. `DPoP-Nonce`

Không cần cho v1.0: Keycloak 26.6 không phát nonce ở token endpoint, `ath` đã giới hạn giá trị của proof ký trước vào đời
access token (≤ 300 s), và khóa non-extractable chặn trích khóa. Nên xem lại nếu tăng `accessTokenLifespan`, hoặc khi cần
ràng proof vào thời điểm server (đồng hồ client lệch > 30 s hiện bị 401 `dpop_iat_future`/`stale`, Desk không tự sửa lệch).

## 6. Kết luận

| Hạng mục | Kết quả |
|---|---|
| Kiểm proof RFC 9449 §4.3 (alg, jwk, chữ ký, htm, htu, ath, jkt, iat, jti) | PASS, trừ DPA-01 |
| Chống hạ cấp Bearer (`/mcp`, `/workflow`, `/files`, `/pii/reveal`; `/data` nhờ `aud`) | PASS |
| Keycloak: `dpop.bound.access.tokens`, refresh token gắn khóa | PASS (Keycloak thật, cờ tắt/bật) |
| `/pii/reveal` gắn `jkt`, `ath` = hash vé | PASS |
| Cache phát lại | PASS cho 1 tiến trình; FAIL khi nhiều tiến trình/restart (DPA-02) |
| Desk: khóa non-extractable | PASS; xoay khóa khi đăng xuất FAIL (DPA-03) |
| Tài liệu | PASS, cần bổ sung DPA-05 và cảnh báo nhiều worker |

**Verdict: ĐƯỢC bật `XT_DPOP_REQUIRED=true` trên triển khai hiện tại (một tiến trình xt-api)** sau khi sửa DPA-01 (một dòng).
DPA-03 nên sửa trước demo trên máy dùng chung. DPA-02 (store dùng chung qua Postgres, biến thể không theo tenant) là điều kiện
bắt buộc trước mọi scale-out. Không có lỗi nào cho phép dùng token/proof bị trộm mà thiếu khóa riêng.

## 7. Đã sửa (16/10/2026, chủ dự án: sửa hết)

| # | Sửa | Kiểm chứng |
|---|---|---|
| DPA-01 | `math.isfinite(iat)` cùng chỗ kiểm kiểu → `dpop_iat` (401) | `test_iat_nan_is_a_dpop_error`, `test_nan_iat_gets_401_not_500` (bỏ xfail) |
| DPA-02 | `xt_identity.dpop_jti` (migration 0013, không theo tenant, PK (jkt, jti), chỉ hàm SECURITY DEFINER), `PgDpopReplayStore`, `XT_DPOP_REPLAY_STORE=postgres` trong compose, /mcp và /pii/reveal dùng chung bảng, purge trong job relay của worker, DB lỗi → 503; `postgres` thiếu store hoặc `WEB_CONCURRENCY>1` với `memory` → dừng khởi động. Kèm sửa: `DpopVerifier`/`StepUpVerifier` bỏ qua store rỗng truyền vào (`replay or …` với `__len__ == 0`) | `test_replay_rejected_by_second_process` (bỏ xfail), `tests/integration/test_dpop_replay_pg.py` (2 pool + restart, purge, quyền xt_app, fail-closed) |
| DPA-03 | Desk `logout()`: `DpopSigner.reset()` → `KeyStore.clear()` (IndexedDB `delete`), RP-initiated logout (`id_token_hint`, `post_logout_redirect_uri`); `provision.py` đăng ký `post.logout.redirect.uris` | `test_desk_logout_rotates_dpop_key_and_ends_sso` (bỏ xfail), vitest `đăng xuất (audit DPA-03)`, `reset (đăng xuất)` |
| DPA-04 | `InMemorySingleUseStore`: heap theo hạn, không quét toàn dict | unit identity |
| DPA-05 | SECURITY.md: proof ký sẵn iat tương lai, giới hạn bởi `ath`/đời token | tài liệu |
| DPA-06 | compose truyền `XT_DPOP_CLIENTS` (mặc định `XT_DESK_AGENT_CLIENT_ID`) + `.env.example` | `test_deploy_offline.DeskAgentDpop` |
