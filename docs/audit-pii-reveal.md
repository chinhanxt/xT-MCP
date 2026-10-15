<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Audit kênh người xem PII (`pii_reveal_ticket` + `POST /pii/reveal`), 16/10/2026

Phạm vi: `src/xt/entrypoints/composition/pii_reveal.py`, `pii.py`, `adapters/db/integration.py` (vault/vé/egress),
migration `0006_pii_reveal.sql`, `ToolOutcome.result_meta` → `_meta["xt/reveal"]` (`adapters/mcp/server.py`),
gói `packages/kenh-pii`, phía Desk `desk/src/pii.ts`, `PiiReveal.tsx`, `bridge.ts`, `agent.ts`, gateway `deploy/apisix`.
Đối chiếu: `research/spike-pii-lane.md` §3.7, `docs/decisions.md` "Tier PII của Desk chat",
`docs/integration-notes.md` "Desk ↔ /pii/reveal", `docs/mcp-server.md`.

Môi trường: xT chạy trong tiến trình (uvicorn) + **một** container `postgres:17-alpine` (tên `xt-it-composition-pg-<rand>`,
cổng ngẫu nhiên, tự xóa), client `mcp` thật qua Streamable HTTP, `httpx` cho `/pii/reveal`, Desk headless (Playwright,
project `pii`). Gateway kiểm trên APISIX đang chạy (`127.0.0.1:9080`, chỉ gửi request đọc).

## 1. Chạy lại test liên quan

| Bộ test | Kết quả |
|---|---|
| `tests/unit/mcp/test_mcp_apps.py`, `tests/unit/tools/audit/test_audit_mcp_r2.py` | 29 passed |
| `tests/integration/composition/test_e2e.py -k "pii or person_details"` (PG17) | 3 passed |
| `desk`: `vitest test/pii.test.ts tests/regression-bridge.test.ts` | 31 passed |
| `desk`: `playwright --project pii` (headless, mock xT) | 1 passed |
| **Mới** `tests/integration/pii_audit/test_pii_reveal_attacks.py` (PG17) | 17 passed, 5 xfailed (strict) |
| **Mới** `tests/integration/pii_audit/test_gateway_pii_route.py` | 1 passed, 1 xfailed (strict) |

```bash
uv run pytest -m integration --no-cov tests/integration/pii_audit -rx
uv run pytest --no-cov tests/integration/pii_audit/test_gateway_pii_route.py --runxfail   # thấy lỗi PIIR-01
```

Mọi xfail đã chạy `--runxfail` để chắc chắn fail đúng lý do (không phải lỗi fixture).

## 2. Bảng PASS/FAIL

| # | Hạng mục tấn công | KQ | Bằng chứng |
|---|---|---|---|
| 1 | LLM/agent lấy vé qua text/`structuredContent` | PASS | vé chỉ ở `result._meta`; envelope chỉ `{tokens, expires_in_seconds}` (`test_ticket_only_in_meta…`) |
| 2 | `_meta` lọt vào ngữ cảnh model ở host phổ biến | PASS | host cloud (claude.ai/ChatGPT, `AGENT_CLIENT`) và BFF local đều `client_not_allowed`, không có `_meta.xt/reveal`; Desk: `resultForModel` chỉ gửi `structuredContent ?? content`, `isModelVisible` loại tool `visibility:["app"]`, `runAgent` chặn tên tool ngoài hồ sơ |
| 3 | Envelope lỗi | PASS | lỗi không có `structuredContent`, `_meta` chỉ PROVENANCE; `PII_TOKEN_UNKNOWN` chỉ có `count`, không lặp token |
| 4 | Log | PASS | caplog DEBUG toàn tiến trình: vé/giá trị chỉ thấy ở logger client (`mcp.client`, `httpx`), không ở logger server `xt.*`/`mcp.server`/uvicorn |
| 5 | Phát lại vé, kể cả 16 request song song | PASS | đúng 1 lần 200, còn lại `TICKET_INVALID` (`SELECT … FOR UPDATE` + `used_at`) |
| 6 | Vé tiêu cả khi chữ ký sai | PASS | `BAD_SIGNATURE` rồi khóa đúng → `TICKET_INVALID` |
| 7 | TTL 60 s | PASS | `expires_at - created_at` = 60 s; quá hạn → `TICKET_INVALID` |
| 8 | Trò lệch `ts` | PASS | ±28 s OK, ±35 s / 10^12 → `STALE_REQUEST`; `ts` bool/float/chuỗi/null → 400. Ghi chú: STALE không tiêu vé (kiểm trước bước khóa) — chấp nhận được vì vẫn cần khóa riêng |
| 9 | Giả chữ ký / thay khóa | PASS | khóa khác, chữ ký cho `ts` khác, DER, 0-byte, `r=s=0`, dài sai → `BAD_SIGNATURE` |
| 10 | CORS: `null`, `*`, evil, hậu tố, hoa/thường, `/` cuối, `localhost`, danh sách, thiếu Origin | PASS | preflight 403 không ACAO; POST `BAD_ORIGIN`; `text/plain` (simple request) → 400; không bao giờ có `Access-Control-Allow-Credentials`; `Vary: Origin`, `no-store` |
| 11 | Vé/token chéo tenant | PASS | token acme xin vé ở beta → `PII_TOKEN_UNKNOWN` (HMAC có tenant id, vault RLS); tenant của vé suy từ hash vé |
| 12 | sensitive vs basic | PASS | nhân viên: bank_account `denied`, email (basic) mở; mức không rõ (token từ lưới lỗi) = sensitive; người bị vô hiệu hóa giữa mint và đổi → `TICKET_INVALID` |
| 13 | Vé cho token người dùng không đọc được bản ghi | **FAIL** | PIIR-02 |
| 14 | Audit đủ (người xem, field, bản ghi, lần thất bại) | **FAIL một phần** | người xem + field + tier `human` + không lưu giá trị: OK (cả `xt_audit.event`); thiếu bản ghi/token và lần thất bại: PIIR-03 |
| 15 | Batch / dò token, rate limit | **FAIL (Low)** | 50 token/vé, số vé không giới hạn, không rate limit nào: PIIR-04. Dò token không khả thi (HMAC 80 bit, cần token Desk) |
| 16 | Oracle thời gian (tồn tại token/vé) | PASS | vé 256 bit (`secrets.token_urlsafe(32)`), lưu `sha256`; khác biệt thời gian giữa vé không tồn tại / chữ ký sai không cho thông tin gì khai thác được; tồn tại token chỉ hỏi được bằng token Desk và phải đoán HMAC 80 bit |
| 17 | Nhầm định dạng token `[PII:…]` vs `⟨MÃ.trường⟩` | **FAIL (Low)** | PIIR-06 |
| 18 | Vé/giá trị trong bộ nhớ Desk sau điều hướng | PASS | `revealTokens` giữ vé trong biến cục bộ, khóa non-extractable mới mỗi lần; giá trị chỉ ở `RevealStore` + state, xóa khi `pagehide/popstate/hashchange/pageshow`, unmount, 60 s; e2e headless: reload xong DOM/localStorage/sessionStorage không còn giá trị, LLM không nhận giá trị, không có `pii_reveal_ticket` trong request LLM |
| 19 | Đường đi production qua gateway | **FAIL (High)** | PIIR-01 |
| 20 | Migration 0006 | PASS (ghi chú) | áp đúng; ledger theo tên file nên hai file `0006_*` không đè nhau, nhưng trùng số thứ tự dễ nhầm |

## 3. Lỗi

### PIIR-01 — HIGH — Gateway không định tuyến `/pii/reveal` trên host Desk (kênh xem hỏng ở production)
- **Mô tả:** Desk POST tới `{DESK_XT_ORIGIN}/pii/reveal`, `DESK_XT_ORIGIN = https://xt.freepro.online` (compose), server cũng
  khai `_meta.url` = origin Desk. `deploy/apisix/apisix.yaml.tmpl` cho host Desk chỉ có `desk-confirm` (`/desk/*`), `desk-mcp`
  (`/mcp*`) và `desk-static` (`/*` → nginx của Desk). `/pii/reveal` rơi vào nginx tĩnh.
- **Repro (APISIX đang chạy):**
  ```bash
  curl -s -o /dev/null -w '%{http_code}\n' -X OPTIONS -H 'Host: xt.freepro.online' \
    -H 'Origin: https://xt.freepro.online' -H 'Access-Control-Request-Method: POST' http://127.0.0.1:9080/pii/reveal  # 405 (nginx)
  curl -s -w '\n%{http_code}\n' -X POST -H 'Host: xt.freepro.online' -H 'Origin: https://xt.freepro.online' \
    -H 'content-type: application/json' -d '{"ticket":"aaaaaaaaaaaaaaaaaaaa","ts":1,"sig":"x"}' \
    http://127.0.0.1:9080/pii/reveal                                                            # 404 nginx
  ```
  Test: `test_gateway_pii_route.py::test_desk_host_routes_pii_reveal_to_xt_api` (xfail strict). E2E `E05` chưa chạy nên chưa bắt.
- **Sửa:** thêm route vào template (rồi render lại):
  ```yaml
  - id: desk-pii                # kênh người xem PII: CORS do xt-api tự xử lý
    hosts: ["@@XT_DOMAIN_DESK@@"]
    uris: ["/pii/*"]
    priority: 10
    plugins:
      real-ip: {source: http_cf_connecting_ip, trusted_addresses: ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]}
      client-control: {max_body_size: 4096}
      limit-req: {rate: 5, burst: 10, key_type: var, key: remote_addr, rejected_code: 429}
    upstream: {type: roundrobin, nodes: {"xt-api:8000": 1}}
  ```

### PIIR-02 — MEDIUM — Token là "chìa khóa": xin vé cho token của bản ghi mình không đọc được
- **Mô tả:** `PiiReveal.mint` chỉ kiểm token có trong vault của **tenant** (`pii_lookup`). `/pii/reveal` kiểm `basic` = mọi nhân
  sự đang làm, `sensitive` = `pii.reveal` mức field (`xrn:<t>:<model>:<field>`), không có bản ghi nào. Hợp đồng
  `integration-notes.md` §2 ghi "Server kiểm mọi token thuộc phiên (`tenant|azp|sub|sid`)"; `kenh-pii` giả định "token chỉ có
  khi đã được thấy bản ghi" — không được cưỡng chế. Token ổn định (HMAC theo giá trị), nằm trong lịch sử chat/ảnh chụp/kết
  quả tool của người khác, nên sao chép được.
- **Repro:** `test_token_of_unreadable_record_not_revealable`: HR (`NV-0026`) `get_record` đề nghị tạm ứng của `NV-0013` →
  bị từ chối; dùng token `bank_account` chép từ phiên của `NV-0013` → `pii_reveal_ticket` → `/pii/reveal` → 200, nhận
  `9704221100223344`. Tương tự mọi nhân viên mở được mọi token `basic` của tenant.
- **Sửa (chọn một):** (a) ghi "đã thấy" khi phát token: `PostgresPiiLane.token()` ghi `xt_meta.pii_token_seen(tenant, token,
  person, seen_at)` cho người gọi; `mint` chỉ nhận token mà `caller.person` đã thấy trong N giờ; (b) vault lưu `record ref`
  nguồn, `/pii/reveal` chạy lại kiểm quyền đọc bản ghi (RLS + IAM `record.read`) cho người xem trước khi trả giá trị.

### PIIR-03 — MEDIUM — Audit reveal thiếu bản ghi/token và không ghi lần thất bại
- **Mô tả:** mỗi lần đổi vé thành công ghi một dòng `pii_egress` (`tier human`, `person`, `fields` dạng
  `model.field:reveal_human`, `trace_id = reveal:<16 hex sha256 vé>`), không có giá trị — đúng. Nhưng: không biết **bản ghi/
  token nào** bị xem (hai tài khoản ngân hàng của hai người cùng field = một phần tử); không đếm số giá trị; `denied` không
  ghi; `BAD_SIGNATURE`, `STALE_REQUEST`, `TICKET_INVALID`, `BAD_ORIGIN` không để lại dấu vết; việc xin vé (mint) cũng không ghi.
  Không trả lời được câu hỏi kiểm toán "ai đã xem STK của nhân viên X".
- **Repro:** `test_reveal_audit_identifies_record`, `test_failed_reveal_attempts_are_audited` (xfail strict).
- **Sửa:** thêm cột `tokens text[]` (token, không phải giá trị) và/hoặc `records text[]`, `denied text[]`, `outcome text`
  (`ok|bad_signature|stale|invalid|bad_origin`) vào `pii_egress` (đã nằm trong hash-chain qua `0006_audit_meta`); ghi một dòng
  ở mint và ở mọi nhánh lỗi sau khi suy được tenant (vé không suy được tenant thì log cấu trúc không chứa vé).

### PIIR-04 — LOW — Không có rate limit cho mint / đổi vé
- **Mô tả:** 50 token/vé, số vé không giới hạn; server không đếm, gateway chưa có route (PIIR-01). Bridge của Desk chỉ giới hạn
  App, không giới hạn nút "Hiện". Người có quyền có thể rút hàng loạt giá trị (kết hợp PIIR-02) mà không bị chặn.
- **Repro:** `test_mint_and_reveal_are_rate_limited` (40 vòng mint + đổi, không lần nào 429).
- **Sửa:** `limit-req` trên route `desk-pii` (PIIR-01) + quota theo người trong `mint` (đếm vé `created_at > now()-1 phút`
  của `person` trong `pii_reveal_ticket`, quá ngưỡng → `rate_limited`).

### PIIR-05 — LOW — Vault giữ field của lần ghi đầu khi mức được nâng lên sensitive
- **Mô tả:** `pii_put` `ON CONFLICT`: `field = coalesce(cũ, mới)`, `level` chỉ nâng. Cùng giá trị xuất hiện ở field basic
  trước rồi ở field sensitive → token mang `level=sensitive` nhưng `field` basic: `_may_reveal` kiểm `pii.reveal` trên xrn của
  field basic, audit ghi sai field. Policy hiện tại cấp `xrn:<t>:*` nên tác động thấp, nhưng policy mức model/field sẽ sai.
- **Repro:** `test_vault_field_follows_sensitive_source` (field còn `person.email` sau khi ghi `hr.salary` sensitive).
- **Sửa:** `field = CASE WHEN EXCLUDED.level = 'sensitive' AND pii_token.level IS DISTINCT FROM 'sensitive' THEN EXCLUDED.field
  ELSE coalesce(pii_token.field, EXCLUDED.field) END`, hoặc lưu tập field (`text[]`) và kiểm quyền trên field nhạy nhất.

### PIIR-06 — LOW — Nhầm định dạng token giữa Desk và server, DoS cả lô bằng dữ liệu
- **Mô tả:** `PII_TOKEN_RE` của Desk nhận `[PII:<16 base32>]` **và** `⟨MÃ.trường⟩` (spike); schema `pii_reveal_ticket` chỉ nhận
  `^\[PII:[a-z2-7]{16}\]$`. Desk gom mọi chuỗi khớp trong kết quả tool vào **một** lần mint; một token `⟨…⟩` hoặc một chuỗi
  `[PII:…]` không có trong vault (người dùng gõ vào field text của bản ghi) làm cả lần mint lỗi (`schema`/`PII_TOKEN_UNKNOWN`)
  → nút "Hiện" hỏng cho mọi token thật trong kết quả đó. Mock e2e Desk (`mock-pii.mjs`) dùng `⟨NV-0042.luong⟩` nên e2e Desk
  không kiểm định dạng thật. Không lộ dữ liệu.
- **Repro:** `test_spike_token_format_rejected_by_server` (ghi nhận hành vi server).
- **Sửa:** Desk chỉ nhận `[PII:…]` (bỏ nhánh `⟨⟩`, sửa mock); server trả token lạ vào `denied` (hoặc Desk thử lại không có token
  lạ) thay vì từ chối cả lô.

### PIIR-07 — LOW / rủi ro còn lại — "Gắn khóa khung Desk" thực chất là khóa do người gọi tự chọn
- **Mô tả:** server nhận bất kỳ `pubkey_jwk` nào từ client trong `XT_PII_REVEAL_CLIENTS`; Origin chỉ là header (client
  ngoài trình duyệt tự đặt được). Vì vậy: (1) ai cầm access token `xt-desk-agent` (profile dev có redirect loopback, hoặc XSS ở
  Desk) tự sinh khóa, xin vé, đặt `Origin` và nhận giá trị thật — một agent có quyền chạy code lấy được PII theo đường này
  (`test_desk_agent_token_outside_browser_reveals`, ghi nhận); (2) MCP App trong iframe gọi được `pii_reveal_ticket` qua bridge
  (không có trong `APP_DENYLIST`, cố ý theo integration-notes §6) và nhận `_meta` có vé; hiện chỉ bị chặn vì origin sandbox
  (`xt-api…`) không nằm trong `XT_PII_REVEAL_ORIGINS`, trong khi CSP của App lại mở `connectDomains=[origin Desk]` "cho
  /pii/reveal". Production (không loopback) thì đường LLM → giá trị không tồn tại.
- **Sửa:** thêm `pii_reveal_ticket` vào `APP_DENYLIST` (Desk host tự gọi, App không cần); `xt serve` từ chối cấu hình khi
  `XT_PII_REVEAL_ORIGINS` chứa origin sandbox; giữ quyết định không loopback cho `xt-desk-agent` ở prod; về sau ràng buộc vé
  với `sid`/DPoP của phiên Desk.

### PIIR-08 — LOW (hardening)
- `endpoint` đọc toàn bộ body (`await request.body()`) rồi mới so `MAX_BODY` → cần `client-control max_body_size` ở gateway
  (đã có trong đề xuất PIIR-01) hoặc đọc stream có trần.
- `xt_meta.pii_reveal_ticket` không có dọn vé hết hạn (chỉ có index `expires_at`): thêm job xóa định kỳ ở worker.
- Hai migration cùng tiền tố `0006_` (`audit_meta`, `pii_reveal`): chạy đúng (ledger theo tên file) nhưng nên đánh số `0007`
  cho file sau để tránh nhầm thứ tự.
- Tài liệu lệch code: integration-notes §2/§4 còn "token thuộc phiên `tenant|azp|sub|sid`" và "`reveal_roles`"; code dùng
  vault tenant + IAM `pii.reveal`.

## 4. Kết luận

- **Mục tiêu bảo mật lõi đạt:** không có đường MCP nào cho LLM/agent của host phổ biến lấy được vé hay giá trị thật (vé chỉ ở
  `_meta`, chỉ client Desk xin được, Desk không đưa `_meta`/tool app-only cho model); vé 256 bit, một lần (kể cả song song và
  khi chữ ký sai), TTL 60 s, lệch `ts` ≤ 30 s, chữ ký P-256 không giả được, CORS đúng một origin, không credentials, cách ly
  tenant, phân tầng basic/sensitive, không lưu giá trị trong audit.
- **Chưa đạt để phát hành:** PIIR-01 (HIGH) làm kênh xem không chạy qua gateway production; PIIR-02 và PIIR-03 (MEDIUM) cần
  sửa trước khi nộp vì trái hợp đồng đã ghi (token gắn phiên, audit đủ để trả lời "ai xem gì").
- **Verdict: FAIL có điều kiện** — sửa PIIR-01, PIIR-02, PIIR-03 rồi bỏ `xfail` tương ứng trong `tests/integration/pii_audit/`
  (test sẽ XPASS → đỏ khi đã sửa); PIIR-04…08 sửa theo đề xuất, không chặn demo.
