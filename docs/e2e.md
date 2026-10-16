<!-- SPDX-License-Identifier: Apache-2.0 -->
# E2E toàn bộ E0–E11 (cổng bàn giao)

Định nghĩa: `docs/gap-review.md` §4.1. Tất cả chạy trên stack compose **thật** profile `minimal` (Postgres, Keycloak, APISIX, PostgREST, xt-api, xt-worker, xt-desk), volume mới, qua APISIX bằng hostname thật. Không mock OIDC, MCP, PII, LLM. Quyết định của harness: `docs/decisions.md` mục "Harness e2e E0–E11" (D-E2E-1..8).

## Cách chạy

```bash
make e2e          # e2e-up (volume mới, build) -> demo-seed -> E0, E2..E11 -> e2e-down (E2E_KEEP=1 để giữ stack)
make demo-seed    # chỉ seed trên stack đang chạy (idempotent)
make e2e-run      # chỉ chạy kịch bản trên stack đã seed; E2E_ARGS="scenarios/test_e05_pii.py" để chạy riêng
make e2e-llm      # E1: Desk + Ollama qwen3.5:4b (slot heavy riêng; cần stack đã up + seed)
```

- `e2e-up` tuân quy tắc heavy-stack: chờ `MemAvailable >= 4 GiB` và không có Keycloak/Ollama container khác; project compose riêng `xt-e2e`.
- Bằng chứng: `e2e/reports/<timestamp>/E<n>.json` + `E<n>.md` + `summary.md` (JWT, mật khẩu, vé bị redact). Thư mục bị gitignore.
- Bí mật user test (mật khẩu, TOTP seed): `~/.config/xt/e2e/demo-seed.json` (0600, ngoài repo).
- Tính năng chưa có không bị skip: kịch bản FAIL với dòng `THIẾU <mảnh>` hoặc tên kiểm tra hỏng.

## Harness (`e2e/`)

| Tệp | Vai trò |
|---|---|
| `xte2e/net.py` | Transport httpx/httpx2 ánh xạ `https://xt*.freepro.online` -> `127.0.0.1:9080` (giữ `Host`, cookie jar theo URL logic https; chặn mọi host khác). `HostProxy`/`TlsGateway` loopback cho công cụ không tự ánh xạ được (`headless-demo.sh`) |
| `xte2e/oidc.py` | Đăng nhập Keycloak thật headless: auth-code + PKCE S256, identity-first (username -> mật khẩu), chọn Organization, OTP (RFC 6238), consent, DCR ẩn danh |
| `xte2e/mcpc.py` | Client `mcp` thật (Streamable HTTP) tới `https://xt-mcp.freepro.online/mcp` |
| `xte2e/seed.py` | `make demo-seed`: provision realm + `xt-data`/`xt-workflow`, lớp PostgREST, JWKS, bật postgrest; 2 tenant (A Bình Minh `seed/binh-minh/org.json`, B Hoa Mai `seed/hoa-mai/org.json`) = 2 Organization; user Keycloak (A: giám đốc, kế toán, 2 trưởng phòng, 3 nhân viên; B: 3 người; 1 user thuộc 2 tổ chức), người duyệt có TOTP seed đã biết; `xt org import` + links; `xt pack install` hai bước (kế hoạch -> `--confirm <digest>`) mọi gói trong `packages/`; `xt tenant bind-queue`; `xt doctor` |
| `xte2e/stack.py`, `compose.py` | Vòng đời stack, `xt` CLI trong ảnh thật, psql superuser để kiểm chứng/tamper |
| `desk/desk_real.mjs` | Playwright Chromium headless: Desk từ **container xt-desk** sau APISIX (không serve build qua route), Keycloak thật, LLM Ollama host |
| `scenarios/test_e00..e11_*.py` | 12 kịch bản, marker `e2e` (E1 thêm `llm`) |

## Kết quả lượt 2 (16/10/2026 ~09:40, `make e2e` volume mới, build lại ảnh, HEAD f95d0b1)

Lượt `XT_DPOP_REQUIRED=false`: 19/23 test xanh; E7 chạy lại sau khi sửa truy vấn thông báo của harness (thông báo nay có `kind`): PASS. Lượt `XT_DPOP_REQUIRED=true` và `make e2e-llm` **chưa chạy**: lỗi gốc ở dòng E9 bên dưới khiến Keycloak từ chối mọi lần đổi code kèm proof DPoP của `xt-desk-agent` (đã tái hiện bằng harness), nên kết quả biết trước là đỏ. Giữ slot heavy cho agent khác.

| # | Kịch bản | DPoP tắt | Nguyên nhân gốc nếu đỏ |
|---|---|---|---|
| E0 | Dựng stack + seed | **FAIL** | `deploy/compose.yaml`: `configs.content` (seaweed-s3, seaweed-security) trên service `read_only` không chạy với Docker Compose 2.40.3 của máy này ("`file` is the sole supported option"). Harness dùng đường vòng `configs.file` (cùng nội dung, ngoài repo) để chạy tiếp, nhưng E0 vẫn báo đỏ. Mọi mục khác của E0 xanh (healthcheck xt-api, `xt doctor`, đủ gói, TOTP, bind-queue) |
| E1 | Desk + qwen3.5:4b | **chưa chạy** | Cùng lỗi gốc với E9 (Desk luôn gửi proof DPoP khi đổi code) |
| E2 | W1 qua MCP client có script | PASS | |
| E3 | Poka-yoke | PASS | |
| E4 | RLS 2 tenant | PASS | |
| E5 | PII | PASS | |
| E6 | Đổi model | PASS | |
| E7 | SLA + restart worker | PASS | Leo thang ở 124 s (< 135 s), có thông báo `kind=escalation`, người nhận duyệt một lần là xong |
| E8 | Headless 4 dịch vụ + Files | PASS | Gồm `/files/v1` multipart, PUT `put_url` (Content-Digest), đọc chéo tenant -> 404, token MCP -> 401 |
| E9 | OIDC trên Desk (container thật) | **FAIL** | Keycloak trả `400 DPoP HTTP URL mismatch` ở token endpoint: APISIX chuyển `X-Forwarded-Port: 9080`, nên Keycloak dựng htu `https://xt-auth.freepro.online:9080/...`. Kiểm trực tiếp Keycloak: cùng proof đó với `X-Forwarded-Port: 443` thì qua bước kiểm DPoP. Sửa: các route `auth*` trong `deploy/apisix/apisix.yaml.tmpl` thêm `X-Forwarded-Port: "443"` vào `proxy-rewrite.headers.set` (production qua cloudflared cũng tới APISIX :9080 nên cũng hỏng) |
| E10 | Security + auth âm + SoD | PASS | Có trigger `decision_sod_guard`; INSERT trực tiếp người duyệt = người đề nghị bị DB chặn |
| E11 | Audit tamper | PASS | |

Tổng (DPoP tắt): **9/12 PASS**, E0 và E9 đỏ, E1 chưa chạy. Lượt DPoP bật cần sửa `X-Forwarded-Port` trước.

## Việc chặn còn lại

1. APISIX: `X-Forwarded-Port: "443"` cho các route Keycloak (chặn E1, E9 và cả lượt DPoP bật).
2. `compose.yaml`: cấp cấu hình SeaweedFS bằng `configs.file` (sinh bởi `make env`) thay cho `configs.content`, hoặc bỏ `read_only` cho seaweedfs (chặn E0 trên Docker Compose 2.40).
3. Sau 1-2: chạy lại `make e2e` hai lượt (`XT_DPOP_REQUIRED=true|false`) và `make e2e-llm`.

## Độ trễ relay hẹn giờ SLA (E7)

Leo thang trễ nhất ≈ SLA + `XT_WORKFLOW_RELAY_SECONDS` (chu kỳ relay outbox hẹn giờ -> hàng đợi) + thời gian worker nhận job (+ restart nếu có). Mặc định hạ từ 60 s xuống **15 s** (`deploy/compose.yaml`, `deploy/.env.example`; tối thiểu 5 s) để SLA PT2M leo thang trong ≤ 2x tick SLA (≤ 240 s, mục tiêu demo ~135 s). Relay là job tự hẹn lại theo mốc, mỗi tenant một chuỗi nên chi phí thấp; deploy có `.env` cũ phải đổi `XT_WORKFLOW_RELAY_SECONDS=15` rồi chạy lại `xt tenant bind-queue` nếu chuỗi relay đã chết.
