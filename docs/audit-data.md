<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Audit dịch vụ dữ liệu headless `/data` (PostgREST) — Opus TESTER

Ngày: 16/10/2026. Môi trường: PostgreSQL 17 (`postgres:17-alpine`) + PostgREST `v16.4`
(`sha256:d155c671…`), image local, một cặp container tên ngẫu nhiên, `docker rm -f -v` tự dọn
(`free -g` available = 7 GB ≥ 4). Phạm vi: `docs/headless-data.md`, `docs/decisions.md` (PG-1…PG-7),
`src/xt/adapters/db/postgrest.py` + `sql/postgrest.sql`, `deploy/` (compose `postgrest`, route
APISIX `api-data`, `fetch_jwks.py`, `configure_data.py`), `tests/integration/test_headless_data.py`
+ `headless_stack.py`, `make check-data`.

## Kết quả chạy lại (tự chạy)

- `make test-data-integration` → **31 passed** (7.40s). Tái lập đúng tuyên bố của codex.
- `make check-data` → **173 unit + 21 deploy-offline** passed, ruff/format/mypy/import/REUSE xanh.
  (Tài liệu ghi “18 deploy offline”; thực tế hiện 21 — lệch tài liệu, không ảnh hưởng bảo mật.)
- Suite đối kháng mới `tests/integration/data_audit/test_data_audit_probes.py` → **7 xfailed**
  (6 phòng thủ xác nhận chặn + 1 bug LOW đã biết), không container sót.

## Ma trận tấn công (PASS = chặn đúng)

| Nhóm | Vector | Kết quả | Bằng chứng |
|---|---|---|---|
| JWT | `alg:none`, HS256 ký bằng JWK công khai (RS/HS confusion) | PASS | 401; `test_audience_and_signature_enforced` |
| JWT | sai `aud` | PASS | 401 `PGRST303` |
| JWT | hết hạn `exp` | PASS | 401 |
| JWT | sai `iss` / `azp` ngoài allowlist / `typ` refresh | PASS | 403 `issuer`/`client_not_allowed`/`token_type` |
| JWT | thiếu `organization` / mơ hồ / dị dạng / SQLi trong alias | PASS | 403 `missing_tenant`/`tenant_ambiguous`/`bad_claim`/`unknown_tenant` |
| JWT | thiếu `sub` / người chưa đăng ký / inactive | PASS | 403 `missing_claim:sub`/`invalid_behalf` |
| Tenant | token A + `Accept-Profile` tenant B | PASS | 403 `profile_mismatch` + RLS lớp 2 (0 dòng) |
| Profile | hoa/thường, space/tab/quote, `%00`, `api_tN,xt` | PASS | 406 `PGRST106` (so khớp chính xác) / httpx chặn header bất hợp lệ |
| Profile | schema `xt`,`xt_meta`,`xt_audit`,`public`,`pg_catalog`,`xt_tN` | PASS | 406 (ngoài `db-schemas`) |
| Bypass | `/rpc/*` (gọi hàm nội bộ pgrst_gate/set_context…) | PASS | 404 (không có hàm trong `api_tN`) / 403 `rpc_disabled` |
| Bypass | embedding `select=*,person(*)/tenant(*)/event(*)` qua FK | PASS | 400 `PGRST200` “no relationship” |
| Bypass | `or=`/`and=`/`limit`/`offset` để nới RLS (staff đọc bản ghi người khác) | PASS | staff chỉ thấy bản ghi own; `title=eq.A-alice-1` → `[]` |
| Bypass | POST/PUT/PATCH/DELETE, bulk/`on_conflict` upsert, `Prefer: tx=rollback` | PASS | 403 `read_only_api` / 404 `xt_api_none` / `WRITE_DENIED` |
| Bypass | ghi field `managed_by: workflow` | PASS | không có quyền ghi; đọc-only |
| PII | cột `sensitive` (national_id): `select=`, filter, order, CSV `text/csv` | PASS | 400 “column does not exist”; CSV chỉ có cột view; audit chỉ `hmac:1:…` |
| PII | `basic` (phone) văn bản thuần | CHẤP NHẬN | đúng chính sách v0.3 (`model-engine.md`) |
| GUC | giả `xt.pgrst_ctx`/`request.jwt.claims`/`Role` qua header request | PASS | gate `set_config('xt.pgrst_ctx','',true)` đầu tiên; carol spoof → `[]` |
| Reload | thêm/xóa tenant+model lúc server chạy (NOTIFY) | PASS | trước reload 406, sau 200; `drop_tenant` → 406 |
| APISIX | traversal `/data/rpc/*`, port admin 3001, spoof header | PASS | route chỉ GET/HEAD; `/rpc` chặn bởi gate; 3001 nội mạng; JWT kiểm ở PostgREST |
| JWKS | `fetch_jwks.py`: khóa riêng, non-RS/ES, kid trùng, ghi trong repo, http non-loopback, redirect | PASS | fail-closed, ghi nguyên tử, giữ tệp cũ khi lỗi |
| DoS | range/limit, body lớn | PASS (prod) | compose: `PGRST_DB_MAX_ROWS=1000`, APISIX `limit-req 30/60`, `max_body 1024`, `mem_limit 128m` |

## Bug

**B1 — LOW (defense-in-depth): `OPTIONS` bỏ qua `xt.pgrst_pre` ⇒ oracle metadata chéo tenant.**
PostgREST **không** chạy `db-pre-request` cho method `OPTIONS`, nên gate (kiểm tenant/person/profile)
không thực thi. Với một access token `xt-data` hợp lệ (bất kỳ client allowlist/tenant nào), OPTIONS
trả `200` cho bảng đang expose của **mọi** tenant, kể cả profile của tenant khác (đáng lẽ 403), và
`200` vs `404` phân biệt bảng tồn tại — biến OPTIONS thành oracle tồn-tại-bảng xuyên tenant. Header
`Allow: OPTIONS,GET,HEAD,POST,PUT,PATCH,DELETE` cũng quảng bá verb ghi (dù verb ghi vẫn bị gate chặn).
Không lộ dòng dữ liệu. OPTIONS không token vẫn `401` (JWT của PostgREST vẫn chạy).

- Repro: `OPTIONS /ticket` + token tenant acme + `Accept-Profile: api_t<seq_globex>` → `200`
  (gate lẽ ra `403 profile_mismatch`). Xem `test_options_bypasses_gate_as_cross_tenant_oracle`.
- Giảm thiểu hiện có: route APISIX `api-data` chỉ cho `methods: [GET, HEAD]` ⇒ OPTIONS trả `404` ở
  gateway, không tới được PostgREST; PostgREST chỉ nằm trong mạng compose nội bộ. Vì vậy **không khai
  thác được qua đường công khai** — đây là lớp phòng thủ **duy nhất**.
- Fix đề xuất (không bắt buộc cho OLP): giữ allowlist method ở APISIX làm bất biến có test; ghi rõ
  trong `docs/headless-data.md` rằng OPTIONS cố ý không đi qua gate và cấm thêm OPTIONS vào route; nếu
  sau này cần CORS, thêm lớp từ chối OPTIONS riêng thay vì mở route. `postgrest.sql` không tự vá được
  (PostgREST không gọi pre-request cho OPTIONS).

## Rủi ro đã ghi nhận (ACCEPTED, không phải bug)

- **PG-2 ranh giới tin cậy**: ai có mật khẩu `xt_authenticator` + kết nối SQL trực tiếp có thể giả
  ngữ cảnh (GUC local không phải chữ ký). Đúng như tài liệu; chấp nhận được với điều kiện pg_hba/mạng
  cô lập PostgREST và secret ngoài repo. Kiểm chứng: `xt_web`/`xt_authenticator` không `SET ROLE` lên
  role mạnh, không EXECUTE setter, view fail-closed 0 dòng.
- **`basic` PII văn bản thuần**: quyết định chính sách v0.3, không coi là lỗ hổng.

## Nhận xét nhỏ (INFO)

- Lệch tài liệu: `docs/headless-data.md` ghi “18 test deploy offline”, thực tế 21.
- Fixture `headless_stack.start_rest` không đặt `PGRST_DB_MAX_ROWS` (compose có `1000`): các tuyên bố
  chống DoS dựa vào cấu hình compose, không vào fixture — đúng, chỉ cần lưu ý khi đọc bằng chứng.
- Header `Allow` của OPTIONS liệt kê verb ghi (thông tin thừa; vô hại do verb ghi vẫn bị chặn).

## Kết luận

**ĐẠT.** Trên PG17 + PostgREST thật, mọi vector rò rỉ/leo quyền/chéo tenant/ghi/PII đều bị chặn
đúng (gate SECURITY DEFINER + RLS lớp 2 + view loại cột sensitive + JWT/JWKS fail-closed). Không tìm
thấy lỗ hổng lộ dữ liệu hay vượt tenant. Chỉ có **1 phát hiện LOW** (B1, OPTIONS bỏ qua gate) đã được
giảm thiểu hoàn toàn ở tầng gateway và không khai thác được qua đường công khai; khuyến nghị ghi chú
+ khóa allowlist method APISIX bằng test hồi quy.

Test hồi quy: `tests/integration/data_audit/test_data_audit_probes.py` (7 test, `xfail(strict=True)`).
6 test khẳng định khai thác (xfail = bị chặn, XPASS nếu phòng thủ vỡ); test OPTIONS khẳng định kỳ
vọng an toàn (xfail = bug còn mở, XPASS khi vá — nhắc gỡ marker).
