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

## Kết quả (16/10/2026, stack `xt-e2e` volume mới, code tại thời điểm chạy)

Lần chạy đầy đủ: `e2e/reports/20261016-023525/` (sau demo-seed trên volume mới). E5/E6/E7 chạy lại trên cùng stack sau khi sửa harness (`20261016-0246*`, `20261016-0251*`). E1: `make e2e-llm`, 3 lần.

| # | Kịch bản | Kết quả | Chặn bởi (tên mảnh còn thiếu / lỗi thật) |
|---|---|---|---|
| E0 | Dựng stack + seed | **FAIL** | (1) `xt-api` không có healthcheck; (2) `xt doctor` báo `postgres: unknown - XT_DATABASE_URL chưa đặt` (compose không cấp); (3) `giao-viec-sla` không cài được: op `close_task`, `weekly_rollup` chưa có hiện thực; `nhan-su-vong-doi` không cài được: op `create_lifecycle_tasks`, `activate_employee`, `check_unreturned_assets`, `close_lifecycle_case` chưa có, process onboarding/offboarding báo `notify` chưa đăng ký. Còn lại đạt: mọi dịch vụ chạy, 2 tenant/Organization, 60 nhân sự A (7 có đăng nhập), 7 người duyệt có TOTP, bind-queue + relay cả 2 tenant, `de-xuat-phe-duyet`, `quan-tri-mo-hinh`, `kenh-pii` cài qua `xt pack install --confirm` |
| E1 | W1 qua Desk + qwen3.5:4b | **FAIL** (0/3) | Desk thật không đăng nhập được: container `xt-desk` (nginx) trả **404 cho `/auth/callback`** (thiếu SPA fallback `try_files ... /index.html`), nên chưa tới bước LLM |
| E2 | W1 qua MCP client có script | **PASS** | PRM + 401 `resource_metadata`; DCR ẩn danh qua gateway; auth-code + PKCE + consent; create/submit 8 triệu, tuyến `dept_head -> director`; `confirmation_required`; thiếu TOTP -> Keycloak dừng ở OTP, không có Decision; TOTP đúng -> "Đã thực hiện"; gọi lại cùng key `replayed=true`; GĐ duyệt -> `approved`; audit `chain_verified` |
| E3 | Poka-yoke | **PASS** | Đúng 1 mã `rule_violation`, thông điệp "'Ngày về' phải sau hoặc bằng 'Ngày đi'", có remediation; sửa -> nháp ok |
| E4 | RLS 2 tenant | **PASS** | MCP: B không thấy/đọc được bản ghi A (lỗi giống id không tồn tại). `/data`: A->A 200, A->B và B->A 403 `profile_mismatch`, không token 401, token MCP 401. User 2 tổ chức: Keycloak bắt chọn 1 tổ chức (D-E2E-3), xT `access_denied` |
| E5 | PII | **PASS** | Client public chỉ thấy `[PII:…]`; `pii_reveal_ticket` chỉ App (`visibility=["app"]`), vé ở `_meta`; `POST /pii/reveal` (Origin Desk, chữ ký P-256) trả giá trị thật cho giám đốc, dùng lại vé 403, Origin lạ 403, `no-store`, có `pii_egress`; ghi ngược token bị từ chối |
| E6 | Đổi model | **PASS** | `propose_model_change` [AN TOÀN] -> `model_proposal`; người đề xuất không tự áp; GĐ duyệt qua `/desk/confirm` + TOTP -> worker áp pha 1+2 -> cột trong `information_schema` -> tạo bản ghi có `ma_du_an`; xóa field có dữ liệu, 1 người duyệt -> cột còn nguyên, chờ người thứ hai |
| E7 | SLA leo thang + restart worker | **FAIL** | Timer sống sót qua `docker compose restart xt-worker`, có `assignment.escalated` + thông báo, GĐ duyệt 1 lần là xong; nhưng leo thang ở **212 s** sau khi gửi (> 135 s): SLA PT2M + chu kỳ relay `XT_WORKFLOW_RELAY_SECONDS=60` + restart |
| E8 | Headless 4 dịch vụ | **FAIL** | Chỉ còn **W8 `/files`**: `/files/v1` trả 404 (chưa có dịch vụ). Đạt: SSO discovery/JWKS; 401 không token ở `/data`, `/workflow`; `/data` + Accept-Profile 200; `xt-cli` redirect loopback cổng bất kỳ; `/workflow/v1` definitions + BPMN có BPMNDI, `POST /instances` 201/200 (idempotent), inbox, decision 428 + `confirm_url` -> xác nhận -> 200; `docs/headless-demo.sh --get-token data|workflow` (PKCE loopback, lái headless) và `headless-demo.sh` chạy thật qua gateway |
| E9 | OIDC trên Desk (container thật) | **FAIL** | (1) `xt-desk` 404 `/auth/callback` (như E1) nên Desk không gọi được `/mcp`; (2) `deploy/tests/desk_oidc_e2e.py check` còn kỳ vọng 3 redirect (có loopback) trong khi realm production theo quyết định "Tier PII" chỉ có `https://xt.freepro.online/auth/callback`: script cần cập nhật theo `XT_PROFILE` |
| E10 | Security smoke + auth âm + SoD | **FAIL** | (1) `security_smoke.sh` PASS=46 FAIL=1: client `xt-cli` đang có `mcp-xt` trong optional scopes (kế thừa default-optional của realm; provision phải gỡ); (2) **W9** chưa có trigger DB chặn `decider = requester`. Đạt: alg=none, HS256 bằng khóa công khai, chữ ký hỏng, refresh/ID token làm bearer, token realm master, token aud `xt-data` đều 401; tự duyệt -> `access_denied` không link; agent gọi `decide_request` 100 lần -> 0 Decision |
| E11 | Audit tamper | **PASS** | `xt audit checkpoint` (Ed25519) + `verify` toàn vẹn; superuser sửa 1 dòng `xt_audit.event` của A -> verify mã 1, báo đúng tenant + seq; B toàn vẹn (dòng được hoàn nguyên sau kiểm) |

Tổng: **6/12 PASS** (E2, E3, E4, E5, E6, E11). Chưa bàn giao được.

## Việc chặn còn lại (theo thứ tự ưu tiên)

1. `xt-desk`: SPA fallback cho `/auth/callback` (chặn E1, E9).
2. Op còn thiếu của `giao-viec-sla` và `nhan-su-vong-doi` (chặn E0), healthcheck `xt-api`, `xt doctor` kiểm DB trên compose.
3. Gỡ `mcp-xt` khỏi `xt-cli`; trigger SoD ở DB (chặn E10).
4. `/files/v1` + SeaweedFS trong minimal (chặn E8).
5. Độ trễ leo thang SLA: chu kỳ relay ngắn hơn hoặc đánh thức theo `due` (chặn E7).
6. Cập nhật `deploy/tests/desk_oidc_e2e.py` theo profile (E9).

## Độ trễ relay hẹn giờ SLA (E7)

Leo thang trễ nhất ≈ SLA + `XT_WORKFLOW_RELAY_SECONDS` (chu kỳ relay outbox hẹn giờ -> hàng đợi) + thời gian worker nhận job (+ restart nếu có). Mặc định hạ từ 60 s xuống **15 s** (`deploy/compose.yaml`, `deploy/.env.example`; tối thiểu 5 s) để SLA PT2M leo thang trong ≤ 2x tick SLA (≤ 240 s, mục tiêu demo ~135 s). Relay là job tự hẹn lại theo mốc, mỗi tenant một chuỗi nên chi phí thấp; deploy có `.env` cũ phải đổi `XT_WORKFLOW_RELAY_SECONDS=15` rồi chạy lại `xt tenant bind-queue` nếu chuỗi relay đã chết.
