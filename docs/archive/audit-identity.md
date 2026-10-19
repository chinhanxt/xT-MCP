# Audit lớp danh tính (identity layer)

Ngày: 2026-10-15. Người kiểm: agent Opus (tester/verifier). Phạm vi: `src/xt/domain/identity.py`, `src/xt/domain/autonomy.py`, `src/xt/application/ports.py`, `src/xt/adapters/identity/*`, `tests/unit/identity/`, `tests/integration/`, `docs/identity.md`. Đối chiếu với: `docs/architecture-v2.md`, `docs/tool-contracts-v1.md`, `research/verification-spikes.md`, `research/spike-hitl.md` (B + C), `research/connector-requirements.md` (MCP 2026-07-28).

Không sửa `src/`. Test tấn công nằm ở `/tmp/xt-id-audit/test_attack.py` (88 ca). Lỗi thật có test hồi quy tại `tests/unit/identity/audit/test_audit_findings.py`. Các test này đánh dấu `xfail(strict=True)`: sửa xong một lỗi thì test đó XPASS và làm suite đỏ, khi ấy xóa marker.

## 1. Chạy lại test

| Bộ | Lệnh | Kết quả |
|---|---|---|
| Unit identity (bản gốc) | `uv run pytest tests/unit/identity` | **145 passed**, coverage `xt.adapters.identity` và `xt.domain.identity` 100% |
| Unit identity + audit | `uv run pytest tests/unit/identity --no-cov` | 147 passed, 10 xfailed |
| Audit khi bỏ xfail | `... tests/unit/identity/audit --runxfail` | 10 failed, 2 passed. Cả 10 lỗi đều tái hiện được |
| Toàn bộ unit | `uv run pytest --no-cov` | 387 passed, 1 skipped, 23 xfailed |
| Integration | `make test-integration` (Keycloak 26.6.3, cổng 19400) | **9 passed**, 67.7 s. Trước khi chạy cổng 19400 trống. Sau khi chạy container đã được dọn (`docker ps -a` không còn container của test) |
| Lint | `ruff check`, `ruff format --check`, `mypy` trên `tests/unit/identity/audit` | sạch |

## 2. Bảng PASS/FAIL

| # | Hạng mục | Kết quả | Bằng chứng |
|---|---|---|---|
| J1 | `kid` path traversal (`../../etc/passwd`) | PASS | Tra dict, ra `unknown_kid` |
| J2 | Header `jku`/`x5u`/`jwk` nhúng | PASS | Bị bỏ qua, khóa chỉ lấy từ JWKS đã cấu hình. Ký bằng khóa lạ ra `bad_signature`, không có fetch thêm |
| J3 | `crit` lạ | PASS | PyJWT 2.15.1: `malformed: Unsupported critical extension` |
| J4 | Nested JWT (`cty=JWT`) | PASS | Payload không có claim, bị loại |
| J5 | Claim trùng trong JSON payload | PASS | Sửa payload thì chữ ký hỏng (`bad_signature`). Muốn trùng claim thì phải có khóa IdP |
| J6 | Token rất lớn | **FAIL (A7)** | Token 5 MB vẫn được decode, verify và chấp nhận |
| J7 | `sub` không phải chuỗi (int, list, dict, bool) | PASS | `bad_claim:sub` |
| J8 | `aud` là mảng chứa resource và giá trị khác | PASS (ghi chú I1) | Được chấp nhận vì `azp` vẫn phải thuộc allowlist |
| J9 | Thiếu `azp`, `aud` đơn | PASS | `missing_claim:azp` |
| J10 | Biến thể `iss` (thêm `/`, `//`, chữ hoa, `?`, `#`, dấu cách, `HTTPS`) | PASS | So sánh chính xác, cả 7 biến thể bị loại |
| J11 | Lạm dụng leeway | PASS | Đúng biên `exp + leeway` thì nhận, quá 1 s thì loại. `XT_IDP_LEEWAY_SECONDS` bị chặn trên ở 120 |
| J12 | `iat` rất xa trong quá khứ + `exp` rất xa trong tương lai | PASS | `lifetime_exceeded` (trần `exp − iat` là 900 s) |
| J13 | `iat`/`exp` kiểu bool, `scope` kiểu list | PASS | `bad_claim` |
| J14 | `exp = Infinity` (Python `json.loads` chấp nhận) | **FAIL (A5)** | `OverflowError` lọt ra ngoài thay vì `InvalidTokenError` |
| K1 | JWK `use=enc` | PASS | Bị bỏ |
| K2 | JWK có `key_ops=["encrypt"]` nhưng không có `use` | GHI CHÚ (I2) | Vẫn được giữ làm khóa ký |
| K3 | `alg` của khóa khác `alg` của header (RS512 với RS256) | PASS | `key_alg_mismatch` |
| K4 | Khóa EC với header RS256 | PASS | `key_alg_mismatch` |
| K5 | RSA 1024 bit | PASS | Bị bỏ (yêu cầu ≥ 2048) |
| K6 | Nhầm đường cong EC (P-384, khai sai `crv`) | PASS | Bị bỏ, hoặc `compatible_with` kiểm `SECP256R1` |
| K7 | Bão refetch bằng `kid` ngẫu nhiên | PASS | 1000 kid lạ cùng lúc chỉ gây 1 lần fetch; trong 100 s là 11 lần (1 lần / 10 s) |
| K8 | `kid` rác chiếm lượt refetch, khóa mới sau rotation bị trễ | GHI CHÚ (A9) | Khóa mới bị `unknown_kid` tới 10 s |
| K9 | Biên TTL, IdP sập thì fail-closed | PASS | Ở 299.999 s vẫn dùng khóa cũ; ở 300 s mà fetch lỗi thì `IdentityProviderUnavailableError`, không dùng khóa cũ |
| K10 | JWKS trả rác (`keys` không phải list) | PASS | Bị loại |
| P1 | URL PRM (RFC 9728 §3.1) | PASS | `/mcp` → `/.well-known/oauth-protected-resource/mcp`, xử lý đúng gốc, cổng và `/` cuối |
| P2 | 401 khi không có token | PASS | `Bearer resource_metadata="…", scope="mcp-xt"`, không kèm `error` (RFC 6750 §3.1) |
| P3 | 401 khi token sai | PASS | `error="invalid_token"`, mô tả chung chung, không lộ `kid` hay claim |
| P4 | 403 `insufficient_scope` | PASS | Một challenge chứa hợp của scope thao tác và scope nền, kèm `resource_metadata` |
| P5 | Tài liệu PRM | PASS | `resource` khớp, chỉ 1 `authorization_servers`, không có `offline_access` |
| P6 | `extract_bearer` | PASS | Loại `Basic`, chuỗi rỗng, có khoảng trắng bên trong, ký tự tab. Scheme không phân biệt hoa thường |
| S1 | `acr` giả qua `acr_values` hoặc `acr` sai/thiếu/có dấu cách thừa | PASS | `acr_insufficient` |
| S2 | Thiếu `auth_time` hoặc `auth_time` là chuỗi | PASS | Bị loại |
| S3 | Chỉ có `amr=otp`, `acr` thấp | PASS | `acr_insufficient`: không bao giờ chỉ dựa vào `amr` (B3) |
| S4 | Token phiên Desk hoặc agent (`azp` khác, `typ=Bearer`, `aud` khác) | PASS | `wrong_client` / `token_type` / `audience` |
| S5 | Nonce sai/thiếu, `auth_time` trước confirmation, SoD | PASS | Ra đúng `reason` |
| S6 | Claim `acr`/`amr` sai kiểu | **FAIL (A4)** | Ném `InvalidTokenError` thay vì `StepUpRejectedError` |
| S7 | Dùng lại cùng ID token cho cùng confirmation | **FAIL (A1)** | Được chấp nhận hai lần |
| R1 | Ed25519 malleability (S + L) | PASS | `bad_signature` |
| R2 | Base64 không chuẩn ở ký tự cuối chữ ký | PASS | 0 biến thể được nhận |
| R3 | `alg=none`, sai `typ`/`kid`/khóa | PASS | Bị loại |
| R4 | Chuẩn hóa digest: thứ tự khóa | PASS | `sort_keys` đệ quy |
| R5 | Chuẩn hóa digest: định dạng số, khóa không phải chuỗi, Decimal, Unicode | **FAIL (A8)** | `{1:"x"}` và `{"1":"x"}` cho cùng digest; `Decimal` ném `TypeError`; surrogate lẻ ném `UnicodeEncodeError`. 12000000, 12000000.0 và "12,000,000" cho ba digest khác nhau; NFC và NFD cũng khác nhau (fail-closed, ghi nhận) |
| R6 | Đua single-use: 32 thread cùng verify một biên nhận | PASS | Đúng 1 thành công, 31 lần `replayed` |
| R7 | Biên hết hạn | PASS | `now >= exp` thì `expired`. Store xóa khóa khi `exp <= now`, nên không replay được sau khi xóa |
| R8 | Biên nhận sai confirmation không đốt `jti` | PASS | Sau đó vẫn tiêu đúng confirmation được |
| R9 | Nhiều biên nhận (approve + reject) cho cùng một confirmation | **FAIL (A1)** | Cả hai đều tiêu được |
| R10 | Người duyệt trùng người đề nghị, khác hoa thường/Unicode (`ALICE`, `alice `, `аlice` Cyrillic, fullwidth) | PASS (ghi chú I3) | So `sub` chính xác. Với Keycloak, `sub` là UUID bất biến nên đúng |
| C1 | Không có giá trị mặc định cho host, realm, secret | PASS | Env rỗng thì liệt kê đủ biến bắt buộc |
| C2 | Secret không bị echo khi cấu hình lỗi | PASS | Biến secret không bao giờ được in ra |
| C3 | `repr()` của settings | **FAIL (A2)** | `repr(IntrospectionSettings)` chứa `client_secret` |
| C4 | URL có userinfo trong thông báo lỗi | **FAIL (A3)** | `http://u:PASSW0RD@…` bị in nguyên văn |
| E1 | Phản hồi HTTP (header + body) không lộ nội dung token | PASS | Chỉ dùng `_DESCRIPTIONS` theo `reason` |
| E2 | `str(exc)` không lộ nội dung token | **FAIL một phần (A6)** | Payload và chữ ký không bị echo. Riêng `kid`/`alg`/`typ` của header chưa kiểm chữ ký thì bị echo nguyên văn, không giới hạn độ dài (thử 100 KB, có `\n`) |

## 3. Lỗi

### A1. Single-use theo `jti` của biên nhận, không theo confirmation; ID token step-up dùng lại được (Trung bình)

**Repro:** `test_a1_only_one_receipt_per_confirmation_can_be_consumed`, `test_a1b_step_up_id_token_single_use`. Với một `StepUpEvidence`, `issue()` phát được nhiều biên nhận cho cùng `C-1`, kể cả một `approve` và một `reject`. Mỗi biên nhận có `jti` mới nên đều qua `verify_and_consume`. `StepUpVerifier.verify` cũng nhận lại cùng một ID token cho cùng confirmation trong suốt `max_auth_age_s`.

**Tác động:** Lớp identity hứa "một lần" nhưng không giữ được điều đó ở mức confirmation. Nếu lớp PendingOperation không tự khóa trạng thái, một lần duyệt có thể thực thi hành động nhiều lần, hoặc đảo được quyết định. `architecture-v2` §5.5 có yêu cầu "tiêu thụ cùng transaction với mutation" ở DB, nên đây chưa phải lỗ hổng khai thác được ngay, nhưng phòng thủ nhiều lớp đang thiếu một lớp.

**Sửa:** Trong `verify_and_consume`, tiêu thêm khóa `f"cid:{cid}"` (hoặc chỉ tiêu `cid`) qua `SingleUseStore`. Trong luồng step-up, tiêu `nonce` hoặc `jti` của ID token (UNIQUE ở bản Postgres), hoặc cho `issue()` nhận store và từ chối evidence đã dùng.

### A2. `repr(IntrospectionSettings)` lộ `client_secret` (Thấp–Trung bình)

**Repro:** `test_a2_introspection_settings_repr_hides_secret`. Dataclass sinh `repr` có `client_secret='S3CR3T-VALUE'`. Bất kỳ `log.debug(settings)` nào, hoặc traceback có biến cục bộ (Sentry, `rich`), cũng sẽ lộ secret.

**Sửa:** `client_secret: str | None = field(repr=False)`, hoặc bọc trong một kiểu `Secret` có `__repr__` trả `'***'`.

### A3. Lỗi cấu hình URL in nguyên userinfo (Thấp)

**Repro:** `test_a3_config_error_redacts_url_credentials`. Kiểm `http` và `fragment` chạy trước, thông báo dùng `{v!r}` nên mật khẩu trong URL bị in ra, dù sau đó đã có kiểm "không được chứa thông tin đăng nhập".

**Sửa:** Trong `_Env.url`, nếu `parts.username or parts.password` thì chỉ báo lỗi đó và `return` ngay. Hoặc thay `v` bằng bản đã che userinfo trong mọi thông báo.

### A4. `StepUpVerifier` để lọt `InvalidTokenError` (Thấp)

**Repro:** `test_a4_step_up_bad_claim_type_is_step_up_rejection`. Các lệnh `string(claims, "acr")`, `str_list(claims, "amr")`, `preferred_username`, `sid`, `jti` nằm ngoài `try`. Claim sai kiểu ném `InvalidTokenError`, trong khi contract là `StepUpRejectedError`. Handler `/desk/confirm` nếu chỉ bắt `StepUpRejectedError` sẽ trả 500, hoặc map nhầm sang 401 của resource server. Vẫn fail-closed, nhưng sai contract.

**Sửa:** Bọc cả phần đọc claim vào một `try/except InvalidTokenError` rồi chuyển thành `StepUpRejectedError`.

### A5. `exp`/`iat` = `Infinity`/`NaN` gây crash (Thấp)

**Repro:** `test_a5_infinite_exp_is_invalid_token`. `numeric()` gọi `int(float('inf'))` nên ném `OverflowError`; với NaN là `ValueError`. Cần chữ ký IdP mới khai thác được, nên rủi ro thấp, nhưng nó vi phạm nguyên tắc "mọi lỗi token là `InvalidTokenError`".

**Sửa:** Trong `numeric()`, nếu `isinstance(v, float) and not math.isfinite(v)` thì ném `bad_claim`.

### A6. Exception echo giá trị header chưa kiểm chữ ký, không giới hạn (Thấp)

**Repro:** `test_a6_error_detail_bounded_and_single_line`. `unknown_kid` dùng nguyên `kid`; `alg_not_allowed`, `header_typ`, `key_alg_mismatch` dùng `repr` của giá trị do attacker gửi. Thử nghiệm cho thông báo dài 100 027 ký tự và có xuống dòng. Hệ quả là log injection và phình log. Phản hồi HTTP không bị ảnh hưởng (E1 PASS). Ngoài ra `issuer`/`client_not_allowed` echo claim đã qua kiểm chữ ký; việc này chấp nhận được.

**Sửa:** Hàm `_safe(v)` trả `repr(v)[:64]` và bỏ ký tự điều khiển. Dùng nó cho mọi detail lấy từ header.

### A7. Không có trần kích thước token (Thấp)

**Repro:** `test_a7_oversized_token_rejected`. Token 1–5 MB vẫn qua base64, verify RSA và `json.loads`. APISIX/uvicorn có giới hạn header nên giảm được rủi ro, nhưng `StepUpVerifier` nhận ID token từ form hoặc callback nên không được bảo vệ như vậy.

**Sửa:** Trong `verify_signed_jwt`, `if len(token) > MAX_TOKEN_BYTES` (ví dụ 16 KiB) thì ném `InvalidTokenError("token_too_large")` trước khi parse. Áp dụng tương tự cho `verify_and_consume`.

### A8. `request_digest` chưa phải canonical JSON thật (Thấp–Trung bình)

**Repro:** `test_a8_digest_rejects_non_string_keys`, `test_a8_digest_decimal_same_as_int`. `json.dumps` đổi khóa `1` thành `"1"`, nên hai mapping khác nhau cho cùng digest. `Decimal` (NUMERIC đọc từ Postgres) ném `TypeError`, còn surrogate lẻ ném `UnicodeEncodeError`. 12000000 và 12000000.0 cho hai digest khác nhau, NFC và NFD cũng vậy.

**Tác động:** Digest chụp lúc tạo confirmation rồi tính lại từ PendingOperation đọc từ DB. Nếu đường đi qua DB làm đổi kiểu số (int sang float hay Decimal) hoặc dạng Unicode, kết quả là `digest_mismatch` giả (fail-closed, chặn người duyệt hợp lệ) hoặc crash. Va chạm khóa int/str là điểm nhập nhằng lý thuyết.

**Sửa:** Dùng RFC 8785 (JCS), ví dụ gói `rfc8785` (Apache-2.0), hoặc tự kiểm chặt: chỉ nhận khóa `str`; tiền là số nguyên đơn vị nhỏ nhất hoặc chuỗi thập phân chuẩn; cấm float; chuẩn hóa NFC ở biên nhập liệu (không làm trong hàm digest); lỗi kiểu thì ném `ValueError` có `reason`. Ghi quy tắc này vào `docs/identity.md`.

### A9. Refetch giữ lock trong lúc gọi mạng; `kid` rác chiếm lượt refetch (Thấp)

**Bằng chứng:** `/tmp/xt-id-audit` `test_rotation_blocked_by_storm`: ra `ROTATION_AFTER_STORM unknown_kid`. `JwksCache.get` gọi HTTP khi đang giữ `threading.Lock`, nên khi IdP chậm, mọi request bị chặn tới `http_timeout_s` (5 s), mỗi 10 s một lần, và chỉ cần một `kid` rác để kích hoạt. Cấu hình còn cho `XT_IDP_JWKS_MIN_REFETCH_SECONDS=0`, tức không giới hạn tần suất refetch.

**Sửa:** Đặt cận dưới của min refetch là 1. Fetch ngoài lock theo kiểu single-flight, request khác dùng tiếp khóa cũ còn hạn TTL.

## 4. Ghi chú (không phải lỗi)

- **I1.** Nhận `aud` nhiều giá trị (resource + `account` + RS khác). MCP yêu cầu token "được cấp cho mình", và kiểm `contains` + allowlist `azp` là đủ. Nên cấu hình Keycloak để `aud` chỉ có URL MCP (bỏ mapper `account`), và có thể thêm tùy chọn chặt `XT_MCP_STRICT_AUDIENCE`.
- **I2.** Có thể bỏ JWK nếu `key_ops` có mặt mà không chứa `verify`. Keycloak không phát kiểu khóa này.
- **I3.** SoD so `sub` chính xác là đúng với UUID của Keycloak. Không nên so `preferred_username`.
- **I4.** Body của 401 khi không có token có `error=invalid_token`, trong khi header (đúng RFC 6750) không có. Đây là body riêng của xT, chỉ cần thống nhất.
- **I5.** `auth_time + leeway < created_at` cho phép xác thực sớm hơn confirmation tối đa `leeway_s` (≤ 120 s). Spike yêu cầu `auth_time ≥ created_at`. Nonce gắn theo confirmation nên rủi ro không đáng kể.
- Integration (9 test) phủ đúng luồng thật: discovery, allowlist, service account, master realm, hết hạn, introspection khi khóa user, rotation/xóa khóa, PKCE + mật khẩu + TOTP → step-up → biên nhận.

## 5. Kết luận

**ACCEPT-WITH-FIXES.**

Không có lỗi nào cho phép vượt qua xác thực: alg confusion, giả mạo chữ ký, sai issuer/audience/azp, token Desk thay ID token confirm và replay biên nhận đều bị chặn, có test unit và test Keycloak thật làm bằng chứng. Phải sửa trước khi nối `/desk/confirm` vào luồng thực thi:

- **A1:** tiêu theo confirmation và theo ID token;
- **A2:** che secret trong `repr`;
- **A8:** quy tắc canonical cho digest.

A3–A7 và A9 là lỗi nhỏ, sửa trong cùng PR. Test hồi quy cho từng lỗi đã có trong `tests/unit/identity/audit/test_audit_findings.py`.


## Phản hồi tác giả

Ngày 2026-10-15. Đã sửa A1–A9. Mỗi test `xfail` trong `tests/unit/identity/audit/test_audit_findings.py` đã bỏ marker và đang pass. Thêm 2 test cho A9: kid đã biết không bị chặn khi refetch đang treo, và cận dưới của min refetch.

| # | Sửa |
|---|---|
| A1 | Biên nhận tiêu khóa `cid:<confirmation_id>` (giữ vĩnh viễn), không tiêu theo `jti`: mỗi confirmation chỉ một quyết định. `StepUpVerifier` bắt buộc `jti` và tiêu `idt:<iss>:<jti>` sau khi mọi kiểm tra khác đã qua |
| A2 | `field(repr=False)` cho `client_secret` và `signing_key` |
| A3 | URL có userinfo chỉ báo lỗi theo tên biến rồi dừng, không echo giá trị. Lỗi parse URL cũng không echo |
| A4 | Toàn bộ phần đọc claim nằm trong `_verify`; `verify()` đổi mọi `InvalidTokenError` sang `StepUpRejectedError` |
| A5 | `numeric()` từ chối `float` không hữu hạn |
| A6 | `IdentityError` làm sạch `reason`/`detail` qua `safe_detail` (một dòng, ≤ 160 ký tự). Áp cho mọi lỗi, không chỉ header |
| A7 | `XT_IDP_MAX_TOKEN_BYTES` (mặc định 16 KiB) kiểm trước khi parse; biên nhận trần 8 KiB |
| A8 | Tự cài JCS (RFC 8785) cho tập con dùng trong xT, không thêm dependency. Khóa phải là chuỗi; số chỉ là số nguyên an toàn; `Decimal` nguyên coi như int; float, Decimal lẻ và surrogate lẻ ném `ValueError`. Quy tắc tiền (số nguyên VND) ghi ở `docs/identity.md` |
| A9 | Fetch JWKS ngoài lock, single-flight bằng `Condition`. Kid đã biết không chờ mạng. `XT_IDP_JWKS_MIN_REFETCH_SECONDS` có cận dưới là 1 |

Các điểm tôi làm khác đề xuất, kèm lý do:

1. **Đổi `test_a8_digest_distinguishes_formats_documented`.** Test cũ khẳng định `12000000` và `12000000.0` cho hai digest khác nhau. Theo JCS thì hai giá trị này có cùng dạng chuẩn, nên khẳng định đó mâu thuẫn với yêu cầu dùng RFC 8785. Tôi chọn cách chặt hơn: từ chối float. Test đổi tên thành `test_a8_digest_number_rules_documented`, khẳng định float và Decimal lẻ bị `ValueError`, còn chuỗi `"12,000,000"` vẫn cho digest khác.
2. **A1, cách khóa.** Tôi chỉ tiêu `cid` (thay vì cả `jti` lẫn `cid`): tiêu theo `cid` đã bao hàm chống replay `jti`, và một thao tác store thì nguyên tử. Lý do reason đổi từ `replayed` thành `confirmation_already_decided`.
3. **A1b, store mặc định.** `StepUpVerifier(store=None)` dùng in-memory để giữ đúng chữ ký hàm mà test audit gọi. Bảo đảm cứng vẫn là `UNIQUE(confirmation_id)` ở DB. Khi chạy nhiều tiến trình, phải truyền store Postgres (đã ghi trong `docs/identity.md`).
4. **I1–I5** để nguyên như ghi chú, chưa sửa.

Ngoài audit, theo phát hiện B1 của audit IAM: `PrincipalKind` nay suy từ `azp`. Client MCP cho `agent` với `on_behalf_of=sub`, client phiên Desk (`XT_MCP_HUMAN_CLIENTS`) cho `human`, client lạ bị từ chối. Trường `on_behalf_of` có trong `Principal`.
