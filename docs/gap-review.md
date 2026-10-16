<!-- SPDX-License-Identifier: Apache-2.0 -->
# Rà soát khoảng trống sản phẩm (gap review), 16/10/2026

Người rà soát: product reviewer thay chủ dự án. Chỉ đọc mã và tài liệu, không chạy docker.
Đối chiếu với: `docs/demo-storyboard-v1.md` (F-01..F-43), `docs/architecture-v2.md` v2.2 §2, §9, §10, `docs/architecture-v2-verdict.md` §7 (T0/T1), `research/olp-checklist.md`, `research/red-team.md`, `docs/decisions.md`, `docs/audit-*.md`, `docs/e2e-desk-keycloak.md`, cùng trạng thái repo thật.

## 0. Trạng thái hiện tại (cập nhật 16/10/2026, sau `98e1a1b`)

Các mục §1-§5 bên dưới là bản rà soát gốc ngày 16/10 (đầu ngày) và giữ nguyên làm lịch sử. Nguồn của mục này: `git log 98e1a1b..HEAD` (54 commit), `docs/*.md`, `docs/audit-*.md`, `docs/e2e.md`, `INSTALL.md`. Nhãn: **e2e** = đã PASS trên stack compose thật (`docs/e2e.md`, lần chạy 16/10 volume mới); **tích hợp** = có test tích hợp (Postgres/Keycloak/SeaweedFS thật trong container) nhưng **chưa chạy lại e2e sau khi sửa**; **dở** = chưa xong.

- **Số test:** unit **1711 passed** trong 118 s, 0 fail (đo lại hôm nay bằng `uv run pytest -q -o addopts="" tests/unit -p no:cacheprovider`). Tích hợp: `make test-integration` 376 passed, 15 xfailed (5 phút 40 s) và Desk 144 vitest + 19 Playwright, đo trên clone sạch bằng `make verify-clean` (HEAD `dcd2a9f`, `INSTALL.md`); các commit sau đó (DPoP vòng 2, files vòng 3, process `complete_via`, notify) chưa có số đo tích hợp mới.
- **E2E cổng bàn giao:** 6/12 PASS (E2, E3, E4, E5, E6, E11) ở lần chạy đầy đủ gần nhất; E0, E1, E7, E8, E9, E10 FAIL. Nhiều nguyên nhân FAIL đã được sửa bằng commit sau đó nhưng **chưa chạy lại**. Chưa bàn giao được.
- **Không còn đúng so với bản gốc:** ba lỗ chặn (apply model, `/pii/reveal`, MCP App) đã đóng (E5, E6 PASS); REST `/workflow/v1` và `/files/v1` đã có; commit lõi (W0) đã làm (54 commit mới, `make verify-clean` chạy trên bản clone HEAD). Release/tag công khai và remote vẫn chưa có trong tài liệu.

### 0.1. Tính năng

| Tính năng | Trạng thái | Bằng chứng |
|---|---|---|
| Tầng 1 đủ 4 dịch vụ (SSO, Gateway, Data, Workflow) + Files | tích hợp; từng dịch vụ riêng đã e2e một phần (bảng 0.2) | `docs/e2e.md` E2, E4, E8; `docs/headless-demo.sh` |
| SSO (Keycloak) + DPoP mặc định bật cho `xt-desk-agent` | tích hợp (Keycloak 26.6.3 thật + Chromium/IndexedDB thật) | `0134d67`, `c854a7f`, `a475beb`; `docs/audit-dpop.md`; migration `0013_dpop_jti.sql`; `XT_DPOP_REQUIRED=true` trong compose; harness D-E2E-9 |
| API Gateway APISIX (rate-limit DCR/token, `client-control`, route Desk-PII, JWT cho `/data /workflow /files`) | e2e (SSO/discovery, 401); smoke 46/47 ở lần chạy e2e, đã sửa lỗi còn lại ở `d1bbcba` (chờ chạy lại) | `docs/audit-deploy.md`, `deploy/tests/security_smoke.sh`, `docs/e2e.md` E10 |
| Dữ liệu `/data` (PostgREST, tenant, `aud=xt-data`) | e2e (E4: A->A 200, A->B 403) | `a9746a6`, `docs/audit-data.md` (ĐẠT) |
| Tệp `/files/v1` (SeaweedFS, link ký, sha256, magic byte, ClamAV hook, quota, RLS, audit) + tool `upload_file` thật | tích hợp, chờ e2e (E8 còn FAIL ở lần chạy cũ vì chưa có dịch vụ) | `2fb8889`, `4531dee`, `336ffb5`, `cd35303`; `docs/files-api.md`; migration `0009`, `0011` |
| REST `/workflow/v1` (9 endpoint, OpenAPI 3.1, 428 + `confirm_url`, `POST /instances`, decision dùng chung) | e2e một phần (E8: definitions, BPMN DI, instances, decision 428); chờ chạy lại cùng files | `35bd6cf`, `5093a43`, `7ee4888`; `docs/workflow-api.md` |
| MCP: 18 tool trong catalog chạy được (17 v0.1 + `start_process`) và `notify` (tool thứ 19) | `start_process` tích hợp; `notify` **dở** (code + migration `0014`/`0015` có, kênh Telegram và leo thang SLA đang nối, commit `7ee4888` ghi "thông báo leo thang SLA đang tạm hỏng") | `5093a43`; `src/xt/contracts/tools/notify.py`; `docs/notifications.md` |
| Hoàn thành bước người qua `transition_record` (`complete_via`, tự tạo `lifecycle_task`, `get_inbox` có `to_states`) | tích hợp, chờ e2e | `7ee4888`; gói `giao-viec-sla`, `nhan-su-vong-doi` 0.3.2 |
| Gói nghiệp vụ: 7 op an toàn cho giao việc/nhân sự (0.3.0) | tích hợp, chờ e2e (E0 cũ FAIL vì thiếu op) | `704f75d` |
| 3 MCP App (`orgchart`, `approval-card`, `record-table`), tool app-only, CSP chặt | e2e Desk trên server thật (Playwright) | `8536925`, `docs/audit-desk.md` |
| Kênh PII: `/pii/reveal` (vé một lần, gắn khóa khung Desk/`jkt`, IAM `pii.reveal`, audit `egress`) | e2e (E5 PASS) | `ac37aa6`, `e294c4e`, `0b488bc`; `docs/audit-pii-reveal.md` |
| Đổi model: đề xuất -> người duyệt -> worker áp pha 1+2 | e2e (E6 PASS) | `66eee8f`, `e0873b4` |
| SoD tầng DB (trigger chặn `decider = requester` cả SQL trực tiếp) | tích hợp, chờ e2e (E10 cũ FAIL vì chưa có trigger) | `704f75d`, migration `0010_decision_sod.sql` |
| Quyền tối thiểu cho job role (nhóm `xt_job`, RLS restrictive theo tenant) | tích hợp (relay timer + pha 2 chạy qua worker thật) | `6fbfe61`, `6feec27`, migration `0008` |
| Seed mapping vai trò/phòng -> từ vựng gói (`mapping.json`, `xt org import --mapping`), tenant thứ hai Hoa Mai | tích hợp; seed chạy trong harness | `6e7354c`, `dbfec08` |
| Audit metadata `xt_meta.*` vào hash-chain + checkpoint Ed25519 | e2e (E11 PASS) | `a9cc90c`, migration `0006` |
| Eval agent: 73 scenario, qwen3.5:4b core-10 x3: 62% không skill / 59% có skill (trên **mock**, không phải server thật) | đo trên mock; E1 (LLM trên xT thật) chưa PASS | `380b638`, `docs/evals.md` |
| `make verify-clean` (clone HEAD -> setup, check, release, kiểm tarball) | đã chạy 16/10 trên HEAD `dcd2a9f` | `64f35ba`, `INSTALL.md` |
| `make e2e` / `demo-seed` / `e2e-llm` (E0-E11 trên stack minimal thật, 2 tenant, TOTP) | harness có; 6/12 PASS | `cd00f7a`, `docs/e2e.md` |
| Desk: OIDC PKCE + DPoP, SPA fallback `/auth/callback` trong container nginx | tích hợp (vitest 144, Playwright 19); Desk container thật chờ e2e (E1, E9 cũ FAIL) | `d1bbcba`, `docs/audit-desk.md` |

Chưa có mã hoặc chưa chạy thật (giữ nhãn "dự kiến"): `aggregate_records` (còn `capability_unavailable`), 2 tool còn lại để đủ 21 (v1.0), kiểm dịch ClamAV/OpenBao ở mức ứng dụng và profile `full`, NATS/Parquet/DuckDB, `xt backup`/`xt restore`, remote công khai + tag phiên bản, smoke Claude Free/ChatGPT (cần tài khoản chủ dự án).

### 0.2. Dịch vụ Tầng 1

| Dịch vụ (đề thi §3) | Thành phần, endpoint | Trạng thái | Bằng chứng |
|---|---|---|---|
| SSO | Keycloak 26.6.3, `/auth` (OIDC, PKCE, DCR có policy, TOTP step-up, DPoP) | e2e PASS (E2); DPoP: tích hợp | `docs/e2e.md` E2, `docs/audit-dpop.md` |
| API Gateway | APISIX, mọi hostname `xt*.freepro.online` | e2e một phần; smoke 46/47 | `docs/e2e.md` E8, E10; `docs/audit-deploy.md` |
| Dữ liệu cấu trúc | PostgreSQL 17 + RLS FORCE + PostgREST `/data` | e2e PASS (E4) | `docs/audit-data.md` |
| Dữ liệu phi cấu trúc | SeaweedFS, `/files/v1` | tích hợp, chờ e2e (E8) | `docs/files-api.md`, `docs/audit-files.md` |
| Workflow | SpiffWorkflow + `xt-worker`, `/workflow/v1` | e2e một phần (E8); tích hợp đầy đủ | `docs/workflow-api.md`, `docs/audit-workflow-api.md` |

### 0.3. Tóm tắt audit (13 báo cáo `docs/audit-*.md`)

Đếm theo mã phát hiện đánh số (không tính ghi chú INFO). "Đã sửa" theo phản hồi tác giả kèm test hồi quy; verdict là của **tester ở vòng cuối được ghi trong báo cáo**, không phải của tác giả.

| Thành phần | Vòng | Verdict vòng cuối | Phát hiện / đã sửa | Còn mở |
|---|---|---|---|---|
| model-engine | 2 | CHƯA ĐẠT (vòng 2); tác giả báo đã sửa hết | 22 / 22 | Vòng 3 chưa có; rủi ro chấp nhận: `xt_migrator` vẫn sửa được dữ liệu (có audit) |
| mcp | 2 | CHƯA ĐẠT (vòng 2); tác giả báo đã sửa | 16 / 15 | Vòng 3 chưa có; R2-M3 chỉ ở mức hợp đồng + contract test; R2-I1 sửa một phần |
| workflow-api | 2 | ĐẠT (có điều kiện nhẹ) | 12 / 12 | WA-11/WA-12 do tác giả sửa sau vòng 2, tester chưa xác nhận |
| queue / worker | 2 (+2b GuardedCursor) | ĐẠT có điều kiện | 16 / 16 | R6 (trễ khởi phát trong tenant, chấp nhận), R9 chưa kiểm |
| desk | 2 | PASS có điều kiện | 9 / 9 | WebRTC/DNS prefetch của iframe: chấp nhận, ghi ở `docs/desk.md` |
| identity | 1 | ACCEPT-WITH-FIXES | 9 / 9 | I1-I5 để nguyên |
| process engine | 1 | PASS có điều kiện | 9 / 9 | I-3, I-4 chuyển sang lớp tích hợp |
| iam | 1 | ACCEPT-WITH-FIXES | 7 / 7 | N1, N2, N4 thuộc adapter, chưa xử lý |
| deploy | 1 | ĐẠT CÓ ĐIỀU KIỆN | 7 / 6 | BUG-C2: APISIX chưa `read_only`; nhóm `full` chưa `cap_drop` |
| pii-reveal | 2 | PASS có điều kiện | 10 / 10 | R2-01/R2-02 do tác giả sửa (`0b488bc`), chưa tester xác nhận |
| data (PostgREST) | 1 | ĐẠT | 1 / 0 | 1 Low (OPTIONS bỏ qua gate), giảm thiểu ở gateway |
| dpop | 2 | Được bật `XT_DPOP_REQUIRED=true` (đã bật mặc định) | 8 / 8 | DPA-07/08 do tác giả sửa sau vòng 2 |
| files | 3 | GẦN ĐẠT (vòng 3) | 18 / 18 | R3-C1 (deadlock quota vs audit chain) sửa ở `cd35303`, chưa có vòng 4 xác nhận |
| **Tổng** | | | **144 / 141** | 3 phát hiện còn mở; 7 báo cáo còn chờ tester xác nhận bản sửa cuối |

Giới hạn: đây là kiểm thử nội bộ bằng agent độc lập với tác giả, không phải audit của bên thứ ba.

### 0.4. Việc còn lại tới cổng e2e (`docs/e2e.md`)

Cổng = `make e2e` xanh E0-E11 (exit 0) trên stack minimal thật, volume mới. Kết quả gần nhất 6/12. Danh sách "việc chặn còn lại" của `docs/e2e.md` và tiến độ:

| # | Việc chặn (E) | Mã sửa đã có | Còn lại |
|---|---|---|---|
| 1 | `xt-desk`: SPA fallback `/auth/callback` (E1, E9) | `d1bbcba` | Chạy lại E1/E9 |
| 2 | Op thiếu của `giao-viec-sla`, `nhan-su-vong-doi`; healthcheck `xt-api`; `xt doctor` trên compose (E0) | `704f75d`, `d1bbcba`, `6e7354c` | Chạy lại E0; process onboarding/offboarding cần `notify` đăng ký (notify còn dở) |
| 3 | Gỡ `mcp-xt` khỏi `xt-cli`; trigger SoD DB (E10) | `d1bbcba`, `704f75d` | Chạy lại E10 (security_smoke.sh) |
| 4 | `/files/v1` + SeaweedFS trong minimal (E8) | `2fb8889`, `4531dee`, `336ffb5`, `cd35303` | Chạy lại E8 (W8 files) |
| 5 | Độ trễ leo thang SLA (E7): đo 212 s > 135 s | relay 15 s (`d1bbcba`) | Chạy lại E7; báo cáo `20261016-025100` vẫn FAIL 212 s; leo thang qua Notifier đang dở |
| 6 | `deploy/tests/desk_oidc_e2e.py` theo profile (E9) | `d1bbcba` | Chạy lại E9 |
| 7 | E1 Desk + qwen3.5:4b (`make e2e-llm`), đạt >= 2/3 lần | chưa | Chạy khi E0/E9 xanh; rủi ro cao nhất (LLM 4B) |
| 8 | Hoàn tất `notify` (Telegram, leo thang SLA, migration `0014`/`0015`) | một phần, chưa commit riêng | Chốt Notifier, test tích hợp |
| 9 | Tester xác nhận bản sửa cuối: model-engine, mcp (vòng 3), files (vòng 4), dpop DPA-07/08, pii R2-01/02, workflow-api WA-11/12 | tác giả đã sửa | Một lượt tester Opus |
| 10 | Ghi transcript e2e xanh vào `docs/e2e-report.md` | chưa | Sau khi `make e2e` exit 0 |


## 1. Bảng F-01..F-43

Chú thích trạng thái:
- **Có + test:** đạt tiêu chí nghiệm thu, có test chỉ ra được.
- **Có chưa test:** có mã nhưng chưa có test.
- **Một phần:** thiếu một phần so với cột nghiệm thu.
- **Chưa có:** chưa làm.

| F | Mốc | Trạng thái | Bằng chứng | Còn thiếu |
|---|---|---|---|---|
| F-01 | v0.0 | Một phần | `cloudflared` (profile `public`), `KC_HOSTNAME`; `tests/integration/test_identity_keycloak.py::test_discovery_chain`; `docs/connector-checklist.md` (chỉ hướng dẫn) | Không có `research/connector-smoke.md`. Chưa ghi lần nào Claude Free/ChatGPT gọi tool qua tunnel |
| F-02 | v0.0 | Có + test | Model engine, IAM, Spiff SafeEngine/compiler, queue procrastinate trong `src/xt`; `research/spike-queue.md` có số đo | Toàn bộ chưa commit |
| F-03 | v0.1 | Có + test | `adapters/mcp/http_app.py` (401 + PRM); `tests/unit/mcp/test_mcp_http.py`, `tests/unit/identity/test_access_token.py::test_rotated_in_key_accepted_after_refetch`, `test_identity_keycloak.py::test_key_rotation_and_deleted_key`; `provision.py` có `organizationsEnabled`, `accessTokenLifespan=300` | Audit MCP vòng 2 ghi "CHƯA ĐẠT", nhưng phản hồi tác giả cho biết R2-H1/M1/M2/M4 đã sửa và 12 test pass. Cần tester xác nhận vòng 3 |
| F-04 | v0.1 | Có + test | FORCE RLS (`adapters/db/compiler.py`, migrations 0002/0003); `test_model_engine_pg.py::test_rls_and_schema_isolation`, `::test_three_packs_into_two_tenants_with_different_versions`; `composition/test_e2e.py::test_rls_two_tenants`; cô lập 1 connection ở `test_headless_data.py` | Chưa chạy trên stack compose |
| F-05 | v0.1 | Có + test | Đúng 17 tool trong `contracts/tools/catalog.py`; `tests/unit/tools/test_contracts.py::test_catalog_has_the_17_tools_of_section_4_1_in_order`, `::test_catalog_table_columns_match_derived_annotations`; `test_mcp_http.py::test_capability_unavailable_on_the_wire` | Không |
| F-06 | v0.1 | Một phần | `application/model_engine/service.py` (propose, approve_and_apply, 2 pha); `test_model_engine_pg.py::test_propose_model_change_end_to_end` gọi service trực tiếp | Qua MCP, đề xuất được lưu nhưng **không có đường duyệt → apply** (xem F-14) |
| F-07 | v0.1 | Một phần | `domain/model/values.py` `rule_violations`, remediation ở `domain/model/errors.py`; `composition/test_e2e.py::test_poka_yoke_vietnamese_rule` | `explain_ref` chỉ có ở `access_denied`. Chưa có rule hạn mức theo loại đề xuất và rule trùng thiết bị. Test chấp nhận 3 mã lỗi khác nhau |
| F-08 | v0.1 | Có + test | `adapters/workflow/bpmn/spiff.py`, relay `workflow.relay_timers`; `tests/unit/process/test_packs.py::test_b1_sla_24h_escalates`; `composition/test_e2e.py::test_w1_advance_8m_end_to_end` (route dept_head → director), `::test_worker_relays_process_timers_to_queue` | Chưa chạy trên stack có restart worker (T1-12) |
| F-09 | v0.1 | Một phần | `dispatcher.py` (human_only, `not_assigned_approver`), `application/iam/guard.py`; `test_handlers.py::test_sod_denied_before_any_confirmation`, `iam/test_packs.py::test_agent_never_approves_in_any_pack` | **Không có trigger DB `decider ≠ requester`**; chỉ có CHECK `final_approver != requester` trong model, và CHECK này chưa có test. Chưa có test "100 lần" và chưa có assert audit `DENY` |
| F-10 | v0.1 | Có + test | `entrypoints/composition/confirm_web.py` (`prompt=login`, `max_age=0`, `acr_values`, PKCE, nonce, CSRF, TTL 10 phút, nhãn "do agent soạn"); `test_identity_keycloak.py::test_step_up_confirm_and_receipt`; `confirm_in_desk` trong e2e | Chưa có test CSRF sai và test hết TTL. `acr` trên KC 26.x vẫn [CHƯA KIỂM CHỨNG] trên stack |
| F-11 | v0.1 | Một phần | `access_token.py` (azp), `guard.py` `AUTONOMY_CEILING`, `introspection.py`; `test_mcp_http.py::test_revoked_token_blocks_high_risk_write` | Chỉ introspect từ L2 trở lên, nên agent L1 đã bị thu hồi vẫn ghi được tới khi token hết TTL. Chưa có lệnh/API thu hồi agent. Trang Agent thuộc v0.3 |
| F-12 | v0.1 | Có + test | `adapters/db/audit.py`, `entrypoints/audit_cli.py`; `test_model_engine_pg.py::test_audit_chain_detects_tampering`, `::test_external_checkpoint_detects_tail_deletion_and_recompute` | Chưa audit bảng `xt_meta.*` (I-AUD-META) |
| F-13 | v0.1 | Có + test | `domain/iam/evaluator.py`; `test_differential.py` (oracle casbin), `test_packs.py::test_case_count` = 42 ca | Các ca theo gói chưa được đối chiếu với casbin |
| F-14 | v0.1 | Một phần | `contracts/model.py` propose_result (dry_run, diff, severity, affected_count); fuzz tên độc ở `tests/unit/model/test_identifiers.py` | **`approve_and_apply` không được gọi ở `entrypoints/` hay `application/tools`**: `ModelChangeServiceAdapter.commit` chỉ lưu đề xuất. Chưa đo thời gian 120-150 s |
| F-15 | v0.1 | Có + test | 4 skill trong `skills/`, 4 prompt trong `contracts/prompts/`; `test_contracts.py::test_prompts_load_and_reference_only_catalog_tools`; `evals/scenarios/approvals.json` | Storyboard ghi 3 skill: chỉ lệch tài liệu |
| F-16 | v0.1 | Một phần | `evals/results/qwen35-4b-core10-r1/report.md`: pass 65% (không skill) / 63% (`skills=auto`), p95 39,5 s; `desk/e2e/local-llm.spec.ts` dùng Ollama thật | Eval chạy trên **mock MCP**. Chưa chạy Goose CLI. Lượt r3 dừng ở khoảng 41/384 |
| F-17 | v0.2 | Một phần | `deploy/apisix/apisix.yaml.tmpl` định tuyến theo host (`/mcp`, `/data`, `/workflow`, `/files`, `/desk`, auth); `security_smoke.sh` 39/39 | Không có `/rest/v1`, không có prefix `/v1`. **Không có plugin JWT ở gateway.** Rate-limit theo IP, chưa theo `azp` |
| F-18 | v0.2 | Có + test | `adapters/db/postgrest.py`, `sql/postgrest.sql`; `tests/integration/test_headless_data.py` (15 test, gồm `test_cross_tenant_token_and_profile_rejected`, `test_real_curl_transcript`) | Container postgrest chưa được kiểm trên stack |
| F-19 | v0.2 | Một phần | `docs/headless-demo.sh` (4 bước curl, chỉ đọc); BPMN DI `layout.py`, `tests/integration/process/bpmn_viewer/check.mjs` | **Không có endpoint `/workflow/v1`**. Script không chạy được luồng đề xuất → duyệt |
| F-20 | v0.2 | Chưa có | Không có aiogram | Toàn bộ |
| F-21 | v0.2 | Chưa có | Chỉ có PG NOTIFY | SSE, đo thời gian |
| F-22 | v0.2 | Một phần | `seaweedfs` và `clamav` trong compose (profile full); contract `upload_file` | `composition/workspace.py` trả `unavailable("upload_file")`. Chưa có `/desk/upload`, ClamAV, EICAR |
| F-23 | v0.2 | Một phần (stub) | Handler + contract `aggregate_records` | `composition/records.py` trả `unavailable`. Chưa có lineage và số đo p95 |
| F-24 | v0.2 | Một phần | `domain/process/spec.py` (parallel, on_return, SLA, cron); `test_runtime.py::test_return_to_step_rework_loop…`, `test_parallel_*` | Chưa có kiểu field `sequence` (TU-2026-xxxx đang cứng trong seed), chưa có resolver `any_of`, chưa có model `org_unit`/`position` |
| F-25 | v0.2 | Một phần | `ui://xt/request-card`, `ui://xt/org-chart` khai trong catalog; `adapters/mcp/server.py` hỗ trợ `UiResource` | **Production không đăng ký HTML nào.** HTML chỉ có trong `research/spike-mcp-apps` |
| F-26 | v0.2 | Một phần | Profile `public`; `research/footprint.md` (~0,75-1,15 GB) | Không có `INSTALL.md`. RAM đo trên xt-api/worker placeholder |
| F-27 | v0.3 | Một phần | `desk/src` (chat, AppBridge, sandbox CSP, OIDC PKCE); `desk/e2e/*.spec.ts`; audit-desk vòng 2 "PASS có điều kiện"; `deploy/tests/desk_oidc_e2e.sh` FAIL=0 trên Keycloak thật | Chưa có trang Agent, delegation, nút Prompt. **Chưa chạy S1-S6 trên xT thật** (e2e dùng mock) |
| F-28 | v0.3 | Một phần (chỉ contract) | `ui://xt/task-board`, `diff-preview`, `record-card`, `inbox`, `report` có trong catalog | Không có HTML, không có e2e |
| F-29 | v0.3 | Một phần | `composition/pii.py` (`llm_egress` × lớp client, token HMAC `[PII:…]`), chặn ghi ngược ở `security.py`; `test_e2e.py::test_pii_masked_for_cloud_full_for_local`, `tests/unit/tools/test_pii_scrub.py`; Desk `PiiReveal.tsx` | **Server không có `/pii/reveal`.** `pii.spec.ts` chỉ chạy với `mock-pii.mjs` |
| F-30 | v0.3 | Một phần | Process on/offboarding trong `packages/nhan-su-vong-doi`; `test_packs.py::test_b5_offboarding` (dùng fake) | Registry `OPS` thật chỉ có `check_overdue_advances`, `notify`. Chưa thu hồi Keycloak L3, chưa có W4 Ledger, `computed`, `views` |
| F-31 | v0.3 | Chưa có | | Nạp CSV/XLSX |
| F-32 | v0.3 | Một phần | 71 kịch bản trong `evals/`, 1 báo cáo qwen core-10 | Chạy trên mock. Chưa có profile full, Claude, GPT, xuất CI |
| F-33 | v0.3 | Chưa có | `deploy/tests/offline/` chỉ là test loopback | Rút mạng, split-horizon |
| F-34 | v0.3 | Chưa có | `docs/connector-checklist.md` | Kết quả thật |
| F-35 | v1.0 | Chưa có | | Bản tin, lineage, DuckDB, Snapshot |
| F-36 | v1.0 | Chưa có | Catalog có 17 tool | 4 tool v1.0 |
| F-37 | v1.0 | Một phần | Checkpoint audit ký Ed25519, receipt ký | Ký outbox/PDF, Merkle, NATS |
| F-38 | v1.0 | Chưa có | `research/vn-forms-verified.yaml` | Jinja → WeasyPrint |
| F-39 | v1.0 | Một phần | `restart:` trong compose; healthcheck cho pg, keycloak, apisix, postgrest | **xt-api không có healthcheck**. Chưa có backup/restore |
| F-40 | v1.0 | Chưa có | Mới có 3 gói | W7/W5 viết bằng YAML |
| F-41 | v1.0 | Một phần | `reuse lint` trong CI, SBOM CycloneDX, `THIRD_PARTY_NOTICES.md`, `docs/LICENSING.md`, `make release` + SHA256SUMS, `.github/ISSUE_TEMPLATE/` | Tarball thiếu mã chưa commit. Version `0.1.0.dev0`. CHANGELOG chỉ có `[Unreleased]`. Không có tag, `server.json`, remote |
| F-42 | v1.0 | Chưa có | | `demo-agent-deny/tamper/reset`, `XT_PROPOSAL_CACHE`, video |
| F-43 | v1.0 | Chưa có | | `make rehearse` |

Tổng: Có + test 10 (F-02, 03, 04, 05, 08, 10, 12, 13, 15, 18); Một phần 22; Chưa có 11. Theo mốc: v0.1 đạt khoảng 70%; v0.2 khoảng 25%; v0.3 khoảng 15%; v1.0 gần 0, trừ PoF.

## 2. Nghiệm thu T0/T1

### T0: chạy được ngay (không cần stack)
- **T0-01** `make check`: chưa kiểm tại đây. Unit 1261/1261 xanh. Cần chạy cả lint, boundaries, reuse và desk-test.
- **T0-02**, **T0-03**, **T0-05**: test unit iam / spiff / jwt đều xanh.
- **T0-07**, **T0-13**, **T0-16**: là việc đọc tài liệu, đạt.
- **T0-15** `df`/`docker ps`: chạy được.

### T0: cần Postgres container (integration), chạy được
- **T0-04** (reload Spiff) và **T0-06** (RLS/model engine) đã có test trong `tests/integration`.

### T0: bị chặn
| Mục | Chặn bởi |
|---|---|
| T0-08 discovery qua `$XT_HOST` | Cần stack `public` + tunnel đang chạy. Đã chạy được trên stack cục bộ (e2e-desk-keycloak), nhưng chưa ghi lại cho tunnel |
| T0-09 401 + PRM qua tunnel | Như trên |
| T0-10 e2e OAuth DCR qua tunnel | Chưa có script e2e DCR trên stack sản phẩm (script cũ nằm ở spike) |
| T0-11 / T0-12 Claude Free / ChatGPT | **Chủ dự án làm tay.** Chưa có `research/connector-smoke.md` |
| T0-14 `make release` | Lệnh chạy được nhưng **kết quả sai**: tarball thiếu mã chưa commit |

### T1: chạy được ngay hoặc chỉ cần container PG/KC tạm
- **T1-05 / T1-15** (RLS): đã có.
- **T1-06**: tương đương `composition/test_e2e.py::test_w1_advance_8m_end_to_end`, nhưng dùng JWKS tĩnh, chưa có Keycloak thật và Playwright.
- **T1-08**: `test_poka_yoke_vietnamese_rule`.
- **T1-14**: `test_audit_chain_detects_tampering`.
- **T1-17**: test contract.
- **T1-18**: spiff security/reload.
- **T1-22**: `test_capability_unavailable_on_the_wire`.
- **T1-23**: nhánh return, có test runtime.
- **T1-24**: G-M1 đóng ở lớp DB.
- **T1-25**: G-P8 `extra=forbid`.
- **T1-26**: `test_gm2_pii_labels_and_audit_hmac`.

### T1: bị chặn hoặc chỉ đạt một phần
| Mục | Chặn bởi |
|---|---|
| T1-01 clone sạch → `make config/build/up/demo-seed/xt doctor` | Không có `make config` và `make demo-seed` ở root. Lệnh up nằm ở `deploy/Makefile`. Chưa có bước seed tenant + org + gói + user Keycloak (TOTP, Organization) trên stack |
| T1-02 release | CHANGELOG chưa có mục 0.1.0, version `.dev0`, tarball thiếu mã, chưa chạy grep TODO |
| T1-03 auth negative trên stack thật | Chưa có `tests/boundary/test_auth_live.py`. Hiện chỉ có `test_identity_keycloak.py` trên KC container, không qua APISIX |
| T1-04 user thuộc 0/1/2 Organization | Có `test_unknown_org_is_rejected` dùng JWKS tĩnh. Chưa có ca 2 org → `tenant_ambiguous` trên KC thật |
| T1-06b đổi ngưỡng bằng dữ liệu gói | Chưa có test. Phụ thuộc đường apply model/gói qua đề xuất (F-14) |
| T1-07 Claude Free | Chủ dự án làm tay. Cần stack public có seed |
| T1-09 `/confirm` + Playwright + KC TOTP + replay | Replay có ở unit. Chưa có test Playwright chạy trên KC thật với TOTP; `acr` chưa được kiểm chứng |
| T1-10 SoD | Thiếu trigger DB `decider ≠ requester` |
| T1-11 trần tự chủ | Có ở unit. Chưa có luồng "agent DCR mới = L0 → người cấp L1" (không có API cấp/thu hồi) |
| T1-12 SLA PT2M + restart worker | Chưa có trên stack: cần `xt tenant bind-queue`, seed và kịch bản restart |
| T1-13 đề xuất field → apply | **Chặn cứng:** chưa nối `decide_request` → `approve_and_apply` → worker pha 2 |
| T1-16 Goose + qwen ≥2/3 | Eval chỉ chạy trên mock. Chưa chạy Goose |
| T1-19 RAM minimal ghi vào INSTALL.md | Chưa có `INSTALL.md`. Số đo hiện tại lấy từ placeholder |
| T1-20 fail-closed khi thiếu cấu hình + `xt doctor` | Có `xt doctor`, nhưng chưa thấy test cho ca thiếu `XT_OIDC_ISSUER` trên image |
| T1-21 tag v0.1.0 | Không có remote, không có tag, mã chưa commit |

## 3. Độ phủ yêu cầu đề thi

### 3.1. Bốn dịch vụ Tầng 1 bắt buộc, có chứng minh được ở chế độ headless không

| Dịch vụ | Trạng thái | Bằng chứng | Thiếu để giám khảo `curl` được |
|---|---|---|---|
| SSO (Keycloak) | **Đạt** | Realm-as-code `deploy/keycloak/realm-xt.json` + `provision.py`; `desk_oidc_e2e.sh` FAIL=0; discovery/JWKS là bước 1 của `headless-demo.sh` | Ghi transcript qua tunnel |
| API Gateway (APISIX) | **Đạt một phần** | `security_smoke.sh` 39/39 (uri-blocker, host lạ 404, DCR, 429, 413) | Thiếu JWT ở gateway cho `/data /workflow /files`, nên chưa có cảnh "không token → 401 **ở gateway**" như §2.1 cam kết. Rate-limit chưa theo `azp` |
| Data | **Đạt một phần** | Có cấu trúc: PostgREST + RLS + `Accept-Profile`, `test_headless_data.py` (15 test, PG thật) | Chưa chạy container postgrest trên stack (cần `make pgrst-*`, `provision-data`, tenant). **Phi cấu trúc: chưa có.** Đề ghi "quản trị dữ liệu cấu trúc/phi cấu trúc"; hiện `upload_file` là stub và SeaweedFS chỉ có ở profile full |
| Workflow | **Chưa đạt headless** | Engine Spiff + `xt-worker` + BPMN DI có test | **Không có `/workflow/v1`** (definitions + BPMN XML, instances, tasks, complete). Hiện workflow chỉ đi qua MCP, đúng kiểu câu hỏi red-team Q7 "workflow chỉ là thư viện nhúng trong MCP server" |

### 3.2. Tầng 2 (ít nhất một ứng dụng có giao diện)
- **Một phần.** xT Desk có chat, AppBridge và sandbox, đăng nhập OIDC thật đạt.
- MCP App chạy trong Desk vẫn là app của spike. Sản phẩm chưa phục vụ `ui://` nào.
- Chưa có đường nghiệp vụ nào từ Desk đi tới dữ liệu thật.
- Trang `/desk/confirm` server-render là giao diện thật duy nhất của sản phẩm có test.

### 3.3. Bảy lớp (DE §4)
| Lớp | Thực tế |
|---|---|
| 1 Trải nghiệm | Desk (một phần), `/desk/confirm`. Chưa có MCP App sản phẩm, Telegram, SSE |
| 2 Biên/ATTT | APISIX + Keycloak chạy thật. OpenBao/ClamAV mới chỉ khai báo trong compose full |
| 3 Tác nghiệp | Postgres 17 + model engine + PostgREST: mạnh nhất |
| 4 Quy trình/DevSecOps | Spiff + worker procrastinate + CI. Chưa có backup |
| 5 Hội tụ/sổ cái | Hash-chain + checkpoint Ed25519. NATS/SeaweedFS chỉ là image trong compose |
| 6 Phân tích/trí tuệ | Ollama + qwen (eval trên mock). Chưa có DuckDB, chưa có `aggregate_records` |
| 7 Giám sát/tìm kiếm | Chưa có: không OTel, không `/metrics`, không FTS |

### 3.4. Bốn nguyên lý
- (i) Vòng đời dữ liệu → lake: **chưa có**.
- (ii) IT/OT: **chưa có** (nằm trong thứ tự cắt đầu tiên, chấp nhận).
- (iii) Phả hệ/chất lượng: **chưa có** (lineage_ref là stub).
- (iv) Zero-trust + GitOps: **một phần**.
  - Đã có: realm/route khai báo bằng file, fail-closed nhiều lớp, `restart:`.
  - Còn thiếu: ClamAV, healthcheck xt-api, tự hồi phục có kiểm chứng.
- Kết luận: cả bốn mới chủ yếu nằm trên tài liệu. Đề coi đây là định hướng, không bắt buộc, nhưng giám khảo sẽ hỏi (red-team Q11). Cần ít nhất (iii) và (iv) có bằng chứng chạy.

### 3.5. PoF (50 điểm)
| Mục | Trạng thái |
|---|---|
| I.1 repo công khai, web viewer, lịch sử thật (5đ) | **Thiếu**: không có remote; lõi chưa commit |
| I.2 SPDX từng tệp, LICENSE toàn văn, tương thích, mục đích (10đ) | Đạt. Chỉ 2 `__init__.py` rỗng chưa có header, được REUSE.toml phủ |
| I.3 release `.tar.gz` có phiên bản trước khi nộp (5đ) | **Một phần**: `make release` chạy được nhưng tarball thiếu mã, `.dev0`, chưa có tag |
| I.4 hướng dẫn build, cấu hình trước build, không công cụ đóng (10đ) | Đạt một phần: README, `dev-setup.md`, `deploy.md`, `.env.example`. Thiếu `INSTALL.md` và luồng clone-sạch-chạy-được (T1-01) |
| I.5 danh mục thư viện, không vendor/sửa (10đ) | Đạt (THIRD_PARTY_NOTICES, SBOM; audit-process xác nhận Spiff không bị sửa) |
| I.6 bug tracker, changelog, README (10đ) | Một phần: đã có template issue nhưng chưa có forge; CHANGELOG chưa có mục phiên bản |

## 4. Đường găng tới "e2e toàn bộ xanh"

### 4.1. Định nghĩa

"E2E toàn bộ" của lần bàn giao này là bộ **E0-E11** dưới đây.
- Tất cả chạy trên **stack compose thật**: profile `minimal`, Keycloak, APISIX, Postgres, PostgREST, xt-api, xt-worker, xt-desk. Thêm Ollama cho E1.
- Đi qua APISIX bằng hostname thật (Playwright `route` hoặc `--resolve`, như `e2e-desk-keycloak.md`).
- **Không dùng mock** cho OIDC, MCP, PII hay LLM.
- Dữ liệu lấy từ `make demo-seed` trên volume mới.
- Chạy bằng **một lệnh** (`make e2e`), headless, exit 0, có transcript lưu vào `docs/e2e-report.md`.

Ngoài phạm vi gating, chỉ ghi kết quả:
- Claude Free / ChatGPT, vì cần tài khoản chủ dự án (T0-11, T1-07).
- Telegram, SSE, F-30 trở đi.

| # | Kịch bản e2e | Tiêu chí xanh | Việc chặn |
|---|---|---|---|
| E0 | Dựng stack + seed | Volume mới → `make up-minimal && make demo-seed && xt doctor` OK. Có 2 tenant (Bình Minh + tenant B, model khác), user Keycloak có Organization + TOTP, gói `de-xuat-phe-duyet` đã cài, `bind-queue` xong | **W1** `make demo-seed`: provision tenant, org import, pack install, user KC + TOTP seed, `provision-data`, `pgrst-*`, `bind-queue`. Thêm healthcheck xt-api |
| E1 | W1 qua Desk + LLM local | Nhân viên đăng nhập Desk (OIDC thật) và chat "tạm ứng 8 triệu đi Đà Nẵng 3 ngày" với qwen3.5:4b. Kết quả: create + submit, thẻ request-card render, trưởng phòng mở `/desk/confirm` + TOTP, GĐ duyệt, `state=approved`, audit đúng thứ tự. Đạt ≥ 2/3 lần | E0; **W3** MCP App sản phẩm (request-card); **W6** Playwright Desk trên stack thật (bỏ spike/mock); độ ổn định LLM (eval hiện 65% trên mock, cần chạy eval trên xT thật và chỉnh skill) |
| E2 | W1 qua MCP client có script | OAuth DCR + PKCE tới Keycloak thật qua APISIX → `submit_request` → `decide_request` bằng token agent trả `confirmation_required`. Playwright hoàn tất `/desk/confirm` (mật khẩu + TOTP). Agent gọi lại cùng key thì `replayed=true`. Mở link khi thiếu TOTP → không có Decision (T1-06, T1-09) | E0; harness OAuth có script (dựa trên `spike-mcp-auth/e2e.py`); TOTP seed để Playwright sinh mã |
| E3 | Poka-yoke | `den_ngay < tu_ngay` → `rule_violation`, thông điệp tiếng Việt có "Đến ngày" + `remediation`. Gọi lại với dữ liệu đã sửa → ok (T1-08) | E0. Siết test để chỉ chấp nhận đúng 1 mã lỗi |
| E4 | RLS 2 tenant | Token tenant A: MCP `search_records` và PostgREST `/data` đều 0 dòng của B. Gửi `Accept-Profile` của B → 401/403. User có 2 Organization → `tenant_ambiguous` (T1-04, T1-05) | E0 (tenant B + user đa org); postgrest chạy được trên stack |
| E5 | PII | Cùng câu hỏi lương: client cloud/public nhận `[PII:…]`. Người dùng Desk bấm xem thì `/pii/reveal` trả giá trị thật với vé dùng một lần, giá trị không vào ngữ cảnh LLM. Audit có `egress`. Ghi token ngược → bị từ chối | **W4** endpoint `/pii/reveal` phía server (vé một lần, gắn phiên người, CSP connect); E0 có dữ liệu `employee_pii` |
| E6 | Đổi model | Agent `propose_model_change` thêm `ma_du_an`. Plan tiếng Việt `[AN TOÀN]`. Agent không tự apply được. Duyệt ở `/desk/confirm` → worker pha 2 → có cột trong `information_schema`. Tạo bản ghi với field mới ok. Xóa field có dữ liệu mà chỉ 1 người duyệt → bị từ chối (T1-13) | **W2** nối decide → `approve_and_apply` → job pha 2 trong worker; E0 |
| E7 | SLA leo thang | Process SLA PT2M; giữa chừng `docker compose restart xt-worker`. Trong ≤ 2 phút 15 s có task leo thang cho GĐ + bản ghi notify, timer không mất, GĐ không phải duyệt 2 lần (T1-12) | E0 (bind-queue, relay timer); biến cấu hình SLA ngắn qua dữ liệu gói, không sửa code |
| E8 | Headless 4 dịch vụ | `docs/headless-demo.sh` exit 0 trên stack: discovery/JWKS; gateway trả 401 khi không token; `/data/<model>` có `Accept-Profile`; `/workflow/v1/definitions` trả BPMN XML; W1 start → task → complete hoàn toàn không qua MCP (bước duyệt đi qua đường xác nhận người); `/files` upload + tải về | **W5** REST `/workflow/v1`; **W7** JWT ở APISIX; **W8** `/files` + SeaweedFS trong minimal; E0 (token `xt-data`) |
| E9 | Đăng nhập OIDC trên Desk | `desk_oidc_e2e.sh` FAIL=0, chạy với container `xt-desk` thật (không serve bản build qua route) | Gộp vào `make e2e`; kiểm container xt-desk (chưa kiểm) |
| E10 | Security smoke + auth âm | `security_smoke.sh` 39/39. Auth âm T1-03 qua APISIX (alg=none, HS256, refresh token làm bearer, realm master, kid đã xóa…). SoD: người đề nghị tự duyệt → `access_denied` không link; agent gọi `decide_request` 100 lần → 0 Decision + audit DENY; SQL `decider = requester` bị trigger chặn (T1-10) | Viết `tests/boundary/test_auth_live.py`; **W9** trigger SoD ở DB |
| E11 | Audit tamper | `xt audit verify` trên stack trả intact. Superuser sửa 1 dòng → báo đúng `seq`. Tenant B vẫn intact (T1-14) | E0 |

### 4.2. Thứ tự găng

1. **W0** commit lõi. Đây là điều kiện tiên quyết cho mọi thứ khác, tránh mất mã và tránh release rỗng.
2. **W1** `demo-seed` (E0). Mọi kịch bản E1-E11 đều phụ thuộc.
3. Sau W1, chạy song song **W2** (apply model), **W4** (`/pii/reveal`), **W5** (`/workflow/v1`), **W7** (JWT gateway), **W9** (trigger SoD).
4. **W3** MCP App sản phẩm, sau đó **W6** Desk e2e thật (E1).
5. Ghép tất cả vào `make e2e`.

E1 là mục rủi ro cao nhất vì LLM 4B không ổn định. Cần chạy lại eval trên xT thật sớm, không đợi tới cuối.

## 5. Mười việc tiếp theo

Thứ hạng đã cân cả (a) điều kiện bàn giao e2e và (b) điểm OLP.
Cỡ: S ≤ ½ ngày, M 1-2 ngày, L 3 ngày trở lên.
Người làm gợi ý theo CLAUDE.md: Opus cho việc khó hoặc bảo mật, codex sol cho code nặng, codex astra/Sonnet cho việc nhanh, agyxt chỉ tìm repo.

| # | Việc | (a) e2e | (b) OLP | Cỡ | Người làm |
|---|---|---|---|---|---|
| 1 | **W0 commit toàn bộ lõi** theo các commit có nghĩa (identity, IAM, model, process, queue, MCP, composition, evals). Sau đó agent decision chốt forge + remote rồi push; sửa `release.sh` để báo lỗi khi cây có tệp chưa commit | Tiên quyết (bảo toàn mã) | PoF I.1 (5đ) + I.3 (5đ) | S | Sonnet (codex astra nếu cần chia commit). Agent decision chọn forge |
| 2 | **W1 `make demo-seed` + E0**: tenant A/B, `xt org import` Bình Minh, `xt pack install`, user KC có Organization + TOTP (seed bằng Admin REST), `provision-data`, `pgrst-*`, `bind-queue`, healthcheck xt-api; `xt doctor` xanh | Chặn E1-E11 | I.4 (T1-01), II.8 | M | Opus (đụng KC, DB role, secret) |
| 3 | **W2 nối duyệt → apply model**: đề xuất model là một request có route duyệt; `decide_request` approved → enqueue `approve_and_apply` → worker pha 2; test E6 | Chặn E6 | II.7 (cảnh S3 "agent đề xuất, người duyệt" là điểm nguyên gốc chính) | M | codex sol (thay thế: Opus) + Opus tester |
| 4 | **W5 REST `/workflow/v1`** (definitions + BPMN XML/DI, instances, tasks, complete qua đường xác nhận người) khai trong ToolContract `rest:`; cập nhật `headless-demo.sh` để chạy W1 không MCP | Chặn E8 | Bắt buộc của đề (Workflow headless), red-team Q7 | M | codex sol |
| 5 | **W3 MCP App sản phẩm**: port HTML request-card, org-chart, diff-preview từ spike vào gói nguồn và đăng ký `UiResource` trong `serve`; CSP `_meta.ui` | Chặn E1 | Tầng 2 có UI (bắt buộc), II.9 | M | codex sol; Opus review bảo mật sandbox |
| 6 | **W6 Desk e2e trên stack thật** (`make e2e`): Playwright với KC thật + xT thật + Ollama, bỏ `start-mcp`/`mock-oidc`/`mock-pii` khỏi profile e2e-stack; chạy lại eval qwen trên xT thật và chỉnh skill | Chính là E1/E9; khung chạy E0-E11 | II.8, II.11 (demo ổn định) | L | Opus (agent kiểm tra + test bắt buộc) |
| 7 | **W4 `/pii/reveal` phía server**: vé dùng một lần gắn phiên người + khung App, không trả qua tool, audit `egress`; nối `PiiReveal.tsx` | Chặn E5 | II.7 (làn PII + Luật 91/2025, red-team Q4) | M | Opus |
| 8 | **W7 + W9 Zero-trust**: plugin JWT/openid-connect ở APISIX cho `/data /workflow /files`, rate-limit theo `azp`; trigger DB `decider ≠ requester`; `tests/boundary/test_auth_live.py` (T1-03, T1-10) | Chặn E8, E10 | Nguyên lý (iv); Gateway headless | M | codex astra (APISIX) + Sonnet (trigger, test); Opus kiểm |
| 9 | **W8 dữ liệu phi cấu trúc**: `upload_file(source=upload_link)` + `/files/v1` trên SeaweedFS ở **minimal** (tệp `unscanned`, không tải về được khi chưa quét); ClamAV + EICAR ở full | Bước files của E8 | Bắt buộc của đề ("cấu trúc/phi cấu trúc") | M | codex sol |
| 10 | **PoF v0.1.0**: version 0.1.0, CHANGELOG mục 0.1.0, `INSTALL.md` có RAM đo thật trên stack sau W1, `server.json`, tag v0.1.0 (T1-02, T1-19, T1-21); sau đó `aggregate_records` thật có `lineage_ref` (nguyên lý iii) | Không chặn e2e | PoF I.3, I.4, I.6; nguyên lý (iii) | S (+M cho aggregate) | Sonnet / codex astra; aggregate: codex sol |

Ngoài top 10, cần làm sớm nhưng không chặn e2e:
- Chủ dự án chạy smoke Claude Free / ChatGPT và ghi `research/connector-smoke.md` (T0-11, T0-12).
- Tester Opus xác nhận vòng 3 cho audit MCP và audit model engine (vòng 2 vẫn ghi "CHƯA ĐẠT", phản hồi tác giả báo đã sửa).
- Mục F-07 còn thiếu: rule hạn mức, `explain_ref` cho `rule_violation`.
- Thu hồi agent cho L1 (F-11).
