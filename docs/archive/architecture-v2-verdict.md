<!-- SPDX-License-Identifier: Apache-2.0 -->
# Phán quyết kiến trúc v2 (JUDGE/VERIFIER)

- Ngày: 15/10/2026. Vai trò: JUDGE độc lập. Không viết lại kiến trúc.
- Đầu vào: `docs/architecture-v2.md` (V2), `docs/archive/architecture-debate.md` (DEB), `docs/tool-contracts-v1.md` (TCV1, bản chốt), `docs/archive/tool-contracts-debate.md` (TCD, bản chốt), `research/packs-draft/REPORT.md` (PACK), `research/*.md`, `CLAUDE.md`, repo hiện tại.
- **Cập nhật 15/10 (vòng 2):** TCV1 và TCD đã chốt: 17 tool ở v0.1, 21 ở v1.0, xác nhận của người không cần tham số. PACK đã chạy 3 gói trên engine thật. Theo chỉ đạo của coordinator, **TCV1 là nguồn sự thật cho bề mặt tool**. V2 phải sửa theo §8. Khoảng trống engine xem §9.
- Cách trích: `tệp:dòng`. V2 = `docs/architecture-v2.md`.
- Kiểm thật trong phiên này:
  - `make check` xanh: 6 test, `reuse` 180/180, `check_spdx` 25 file.
  - `git log`: 3 commit cục bộ.
  - `df -h /`: còn 7,5 GB. `free`: còn khoảng 6 GB RAM, swap đã dùng 3/3 GB.
  - Image đã có sẵn: postgres:17-alpine, keycloak, apisix:3.19.0, seaweedfs, postgrest.
  - Đã có `cloudflared` và `ollama` + `qwen3.5:4b`.
  - Metadata PyPI của các dep chưa có trong research (bảng §3.1).

---

## 0. Phán quyết: **GO-WITH-CONDITIONS**

- Phần lõi của V2 có spike chạy thật đứng sau, gồm: model engine, SpiffWorkflow an toàn, IAM mini, sửa OAuth, Desk, LLM local, và các thông tin connector.
- Không thấy mâu thuẫn nghiêm trọng nào giữa V2 và bằng chứng kỹ thuật.
- Có **6 điều kiện chặn (B1–B6)**. Phải đóng các điều kiện này ở cổng v0.0 (18/10) thì mới bắt đầu phần v0.1 tương ứng:
  - B1: V2 chưa sửa theo TCV1 (danh sách §8), và còn một mâu thuẫn chưa giải: kênh `cli_command` ở stdio.
  - B2: hostname.
  - B3: chưa có bằng chứng step-up cho `/confirm`.
  - B4: D6 trích bằng chứng quá mức.
  - B5: chưa xác minh giấy phép của dep sắp thêm.
  - B6: 4 khoảng trống engine do PACK phát hiện chạm thẳng vào W1 v0.1: G-P3, G-M1, G-M2, G-P8 (§9).
- Các phần còn lại được bắt đầu build ngay từ hôm nay: port model engine, IAM, Spiff và verifier vào `src/xt`, spike hàng đợi, dựng tunnel.

---

## 1. Đối chiếu quyết định với bằng chứng

| V2 | Bằng chứng | Kết luận |
|---|---|---|
| D3 `mcp` 2.3.0, verifier tự viết (V2:45, V2:340-343) | `spike-mcp-auth.md:9,44`; `verification-spikes.md:28-29` (e2e PASS) | **Khớp.** Còn mở rủi ro SDK nhắm spec 2026-07-28 trong khi Claude chỉ hỗ trợ tới 2025-11-25 (`connector-requirements.md:29`). V2 đã đưa việc kiểm vào smoke v0.0 (V2:343) |
| Sửa OAuth VS #1–#5 (V2:434-439) | `verification-spikes.md:31,32,40,41,42,48-54,125` | **Khớp đủ 5 mục**: `azp`, TTL 5 phút + kiểm trạng thái user, cache JWKS có TTL và xóa kid biến mất, `sub/iat/typ` + trần `exp−iat`, xoay passive→active. Có 2 ghi chú (không chặn), xem N3 và N4 |
| D9 scope `mcp-xt` optional, fallback default + denylist `azp` (V2:51, V2:435) | `verification-spikes.md:50` khuyên **bỏ** khỏi realm default; `connector-requirements.md:16` lại khuyên **giữ** audience mapper ở default | V2 đã hòa giải hai nguồn đúng hướng: đặt `scope=` trong 401 (`connector-requirements.md:17`). Nhưng nhánh fallback dùng **denylist**, tức fail-open khi có client nội bộ mới. Xem N2 |
| D4 Spiff in-process + worker, state cột `json` + `_reorder`, SafeEngine (V2:46, V2:616-619) | `spike-spiffworkflow.md:13,71-75,97-103`; `verification-spikes.md:10-15,65-85,110-121` | **Khớp.** `_reorder` đã chạy đúng với song song và vòng lặp (`verification-spikes.md:121`). Chặn được 33/33 payload. V2:600 đã ghi lỗi role không escape (`verification-spikes.md:97-99`). V2:601 cấm XXE, khớp `verification-spikes.md:108`. Thiếu 2 điểm, xem N5 |
| BPMNDI tự sinh (V2:599) | `spike-spiffworkflow.md:157`: compiler spike **không** có DI | Hợp lý nhưng **[chưa kiểm chứng]**. V2 đã đặt nghiệm thu ở v0.2 (mở trong Camunda Modeler) |
| D5 role DB, RLS FORCE, GUC mỗi transaction (V2:460-474) | `spike-model-engine.md:91-100,181-195` | **Khớp** (đã chạy với pool 1) |
| **D6 bảng theo model, namespace `t<seq>__<model>`**, nguồn ghi "SME (chạy thật)" (V2:48, V2:564, V2:636-639) | `spike-model-engine.md:14` chạy bảng **dùng chung** `xt_data.<model>`. `spike-model-engine.md:385` chỉ **đề xuất** namespace như vấn đề mở | **Trích quá mức.** Namespace theo tenant chưa từng chạy. Riêng việc compiler chỉ cần đổi `C.table()` thì có cơ sở (`:385`). Ngoài ra §3.3c (V2:353) dùng view `api.<model>`, cách này **không** dùng được khi mỗi tenant có bảng và cột khác nhau. Xem **B4** |
| Planner, apply 2 pha, audit hash-chain, CRUD (V2:569-593, V2:508-517) | `spike-model-engine.md:136-144,213-269,132-134,199-210,349` | **Khớp**, số liệu trích đúng (1.129–1.560 ghi/s, `verify` 80k mất 658 ms) |
| Alembic chỉ cho schema tĩnh, `compare_metadata` trong doctor (V2:361-364) | `spike-model-engine.md:353-378` | **Khớp** |
| JSON thay YAML, regex tập con Rust, COPY bị chặn dưới RLS (V2:552, V2:166) | `spike-model-engine.md:22-25,333` | **Khớp** |
| D11 IAM mini, condition `eq/ne/le/ge/in`, casbin làm oracle (V2:53, V2:444-454) | `spike-iam.md:19` mới có `eq/ne/le`; `:151` casbin không lưu được condition; `:183-198` | **Khớp có điều kiện.** `ge/in` là phần mở rộng chưa test, DEB:48 thừa nhận. Oracle casbin chỉ so được phần không có condition. Unit test `ge/in` phải có trước khi merge |
| D7 Desk = React/Vite + `@mcp-ui/client` (V2:49, V2:173-190) | `spike-desk.md:11,23-32,52` | **Khớp.** Nhưng bản spike **chép nguyên** `sandbox.ts`/`sandbox.html` của ext-apps (`spike-desk.md:42`). Nếu làm vậy trong repo là vendoring. Xem N1 |
| LLM local qwen3.5:4b, `reasoning_effort:"none"`, hồ sơ rút gọn (V2:115, V2:327) | `spike-local-llm.md:29,31,86-89` | **Khớp.** Spike chỉ thử tối đa 9 tool (`:90`). Goose chậm 10–19 s, có 2/6 lượt không trả lời (`:76`). Đây là rủi ro cho nghiệm thu v0.1 (8), xem T1-16 |
| Thông tin connector (V2:192-200, V2:676) | `connector-requirements.md:10,13,18,25` | **Khớp**: Claude Free 1 connector, ChatGPT Pro chỉ đọc, gói Plus chưa xác nhận, 401+PRM bắt buộc, dải IP `160.79.104.0/21` |
| D12 `/mcp` đi thẳng qua APISIX, xt-api tự trả 401 (V2:54) | `connector-requirements.md:18`; `building-blocks.md:143` (SSE qua APISIX chưa kiểm) | Đúng hướng. Phần SSE **[chưa kiểm chứng]**. Xem N6 |
| D8 `/confirm` xác thực tươi `max_age=0` + TOTP + `acr` (V2:50, V2:491-495) | **Chưa có báo cáo.** `research/spike-hitl/` mới có code (`kc_setup.py`, `stepup_flow.py`, `ciba_flow.py` chưa commit), không có `spike-hitl.md` | **Thiếu bằng chứng**, trong khi đây là phần chịu lực của nghiệm thu v0.1 (1) và (3). Xem **B3** |
| Làn PII (V2:519-528) | Không có `spike-pii-lane.md` | Mới là thiết kế. Làm ở v0.3 nên không chặn |
| Footprint `minimal` < 4 GB (V2:674) | Không có `footprint.md`; `spike-local-llm.md:39` ghi ổ `/` chỉ còn 6,7 GB | Chưa đo. V2 hẹn đo ở v0.2. Máy dev hiện chỉ còn khoảng 6 GB RAM và 7,5 GB đĩa nên cần **đo từ v0.1** (T1-19) |
| Hợp đồng tool (V2:43, V2:108-115) | TCV1:14 (17/21 tool), TCV1:20 (C7: `confirm_url`, trang tự thực thi pending operation), TCV1:3731 (tự ghi chỗ lệch với V2:111) | **Đã chốt.** Catalog 17 tool thỏa trần ≤22 và đủ 6 năng lực của V2 §1.3 (TCV1:3728-3730). V2 còn nhiều chỗ chưa khớp (§8). Riêng TCV1:183,3491-3492 (`cli_command` ở stdio) **ngược với** D8 "không có CLI" (V2:50, DEB:57) |
| Gói nghiệp vụ là dữ liệu (V2:119-126, V2:587) | PACK:13-19: 3 gói, 19 model, 6 process, toàn JSON; apply 100% AN TOÀN; 41/41 ca IAM; SLA leo thang; seed | **Có bằng chứng mạnh** cho "nghiệp vụ là dữ liệu". Nhưng còn 27 khoảng trống engine, 7 mục mức A (PACK:261-305). PACK vẫn chạy bảng dùng chung, không phải namespace theo tenant (PACK:53), nên B4 vẫn còn nguyên |

---

## 2. Tuân thủ đề: đi qua `research/olp-checklist.md`

| Mục CL | Kế hoạch V2 | Đánh giá |
|---|---|---|
| §1 Đội SV ≤3 + GV, đăng ký qua trường (CL:12-13) | Câu hỏi mở (V2:750) | **Gap về điều kiện dự thi**, không phải kỹ thuật. Chủ dự án phải trả lời trước 10–11/2026 (CL:116) |
| §1 Giấy phép OSI, kho công khai (CL:14-15) | Apache-2.0; push sau (V2:693) | Đạt nếu push trước khi nộp. Khuyến nghị push ở v0.1 (N9) |
| I.1 Web viewer, truy cập mở, lịch sử thật (CL:21-23) | Có commit cục bộ, push giữ nguyên ngày | Đạt có điều kiện: phải push lên forge |
| I.2 SPDX từng file, LICENSE toàn văn, tương thích (CL:27-30) | `reuse lint` 180/180; ma trận ở `LICENSING.md` (V2:334) | `LICENSE` đã có. **`LICENSING.md` chưa có**, mà tệp này phục vụ đúng mục "mục đích giấy phép" (CL:29). Phải có ở v0.1 (T1-02) |
| I.3 Release `.tar.gz` có số phiên bản trước hạn (CL:34-36) | `make release` (V2:691) | Đạt. Riêng SHA256SUMS/SBOM mà V2:691 nói "đã có" thì Makefile **chưa có** |
| I.4 Hướng dẫn build, cấu hình trước build, không công cụ đóng (CL:40-43) | `make config/build/up/demo-seed`, bỏ Cloudflare khỏi đường build (V2:336, V2:685-687) | Đạt về thiết kế. `make config` chưa tồn tại, nên nghiệm thu ở T1-01 |
| I.5 Danh mục thư viện, không đính kèm, không sửa (CL:47-49) | `THIRD_PARTY_NOTICES.md` tự sinh, LGPL chỉ import (V2:334) | Rủi ro vendoring `sandbox.ts` (N1). Chưa chọn công cụ sinh SBOM (N8) |
| I.6 Bug tracker, Changelog, README (CL:53-55) | CHANGELOG và README đã có | **V2 không nhắc bug tracker.** Cần chốt Issues trên forge + template (N9) |
| §3 Tầng 1 headless (CL:60) | Ranh giới tầng ở V2:143-145 | Đạt. Khuyến nghị APISIX chặn `/auth/admin` và cổng UI của SeaweedFS để Tầng 1 không lộ UI ngoài trang đăng nhập SSO (N10) |
| §3 SSO (CL:61) | Keycloak, `/auth`, v0.1 | Đạt |
| §3 API Gateway (CL:62) | APISIX 4 namespace, **v0.2** (V2:733) | Đạt ở v0.2. **v0.1 không có APISIX trên đường đi** (V2:732), xem N6 |
| §3 Dữ liệu cấu trúc + phi cấu trúc (CL:63) | Postgres ở v0.1; SeaweedFS + upload ở **v0.2** | Đạt ở v0.2. boto3 với SeaweedFS chưa kiểm (`building-blocks.md:145`) |
| §3 Workflow (CL:64) | Spiff ở v0.1; `/workflow/v1` + `headless-demo.sh` ở v0.2 | Đạt ở v0.2 |
| Mỗi dịch vụ demo headless bằng endpoint riêng | Cột "Cách giám khảo kiểm" (V2:134-139) | Kế hoạch cụ thể. Chỉ kiểm được từ v0.2 |
| §3 Tầng 2 có ≥1 ứng dụng UI thuộc H-P-D-I (CL:65-66) | Desk + MCP Apps; trục [P] e-Office + [I] (V2:202-204) | Đạt. MCP Apps có từ v0.2, Desk đầy đủ ở v0.3 |
| §4 Bảy lớp (đề §4) | Bảng V2:206-216 đủ 7 lớp, có giấy phép | Đạt |
| §4 Bốn nguyên lý (CL:74-77) | V2:218-225: (i)(ii)(iii) ở v1.0, (iv) từ v0.1 | Chấp nhận được vì BTC coi là định hướng (CL:72). Rủi ro: cả 3 nguyên lý dồn vào tuần 22–28/11, mỗi mục ≤1 ngày, và (ii) là mục cắt đầu tiên. Nếu cắt thì slide phải nói rõ (N11) |
| §4 Rà giấy phép thực tế (CL:78) | Bảng stack V2:303-332 | Một phần dep chưa có bằng chứng giấy phép, xem §3.1 |
| §5 II.7–II.11 | Nguyên gốc (agent đề xuất/người duyệt), demo, Desk, docs, showcase | **II.11 cộng đồng: V2 chưa có kế hoạch** (người dùng thử, issue, MCP Registry chỉ nhắc ở V2:735). Không chặn |
| §6 Demo 15 phút, khuyến khích có instance Internet (CL:92-98) | Storyboard; instance công khai qua tunnel ở v0.2 | Đạt |
| §7 Hồ sơ email (CL:102-108) | Không thuộc kiến trúc | Đưa vào checklist v1.0 |
| §8 Năm hạn nộp lệch 2025/2026 (CL:112-114) | V2:783 | Đã ghi nhận. Cần hỏi BTC |

---

## 3. Quy tắc của chủ dự án

### 3.1. Dep OSI: đối chiếu từng dep với `verified-repos.md` (VR), `repos-auth-ui-audit.md` (RA), `building-blocks.md` (BB)

| Dep (V2:303-332) | Bằng chứng giấy phép | Trạng thái |
|---|---|---|
| `mcp` MIT, SpiffWorkflow LGPL-3.0, PostgREST MIT | VR:16, VR:7→22, VR:33 | OK |
| Keycloak Apache-2.0, aiogram MIT, `@mcp-ui/client` Apache-2.0, ext-apps MIT/Apache | RA:13, RA:26, RA:23, RA:22 | OK |
| APISIX Apache-2.0, SeaweedFS Apache-2.0 | BB:78, BB:88 | OK |
| psycopg LGPL, pydantic MIT, PyYAML MIT, SQLAlchemy/Alembic MIT | `spike-model-engine.md:5` | OK |
| pycasbin Apache-2.0 | `spike-iam.md:165` | OK |
| PyJWT MIT, uvicorn BSD-3 | `spike-mcp-auth.md:12` chỉ có phiên bản. PyPI (kiểm 15/10): MIT, BSD-3-Clause | OK sau khi kiểm hôm nay. Cần ghi vào VR |
| procrastinate, holidays, python-dateutil, boto3, Starlette, lxml, DuckDB, Jinja2, WeasyPrint | **Không có trong VR/RA/BB.** PyPI (kiểm 15/10): MIT; MIT; Apache-2.0/BSD dual; Apache-2.0; BSD-3; BSD-3; MIT; BSD; BSD | Là OSI theo metadata. **Phải ghi vào `research/verified-repos.md` hoặc `THIRD_PARTY_NOTICES.md` trước khi thêm vào `pyproject.toml`** (B5) |
| NATS, ClamAV (GPL-2.0, tiến trình riêng), OpenBao (MPL-2.0), Mailpit, Ollama, cloudflared | Chỉ có trong `red-team.md:125,231,247` hoặc được ghi "chưa kiểm" (BB:146) | Thuộc profile full/public, mốc v0.2 trở đi. Kiểm khi thêm vào |
| Trọng số qwen3.5:4b | V2:327 ghi "[kiểm giấy phép trọng số]" | Phải kiểm trước khi demo |
| bpmn-js | RA:37: MIT kèm điều khoản watermark | V2 không dùng. Đúng |
| Dev: ruff, mypy, pytest, import-linter, reuse, Playwright, MCP Inspector | V2:332 ghi "kiểm khi scaffold" | Chỉ là dev tool, không phát hành. Vẫn phải liệt kê |

**Bị cấm:** không thấy n8n, Redpanda, SSPL/BSL, MinIO, immudb, Vault hay Open WebUI trong V2. Đạt.

### 3.2. Không vendoring
- Phần Python đạt: LGPL chỉ import (V2:334), release chỉ có nguồn (V2:691).
- **Rủi ro:** spike Desk chép `sandbox.ts`/`sandbox.html` (`spike-desk.md:42`). Xem N1.

### 3.3. Không hard-code, không fallback giả
- Ngưỡng duyệt, SLA, tuyến duyệt là dữ liệu gói (V2:653; `demo-storyboard-v0.md:129`). Đạt. Nghiệm thu bằng T1-06b.
- D9 fallback denylist `azp` (V2:435) là fallback bảo mật thật nhưng **fail-open**. Xem N2.
- `xt-desk`/`xt-bot` "tắt nếu thiếu LLM/token" (V2:674): chấp nhận được nếu `xt doctor` báo rõ, không im lặng (T1-20).
- "Thiếu effects thì server không khởi động" (V2:418): đúng tinh thần, có nghiệm thu T1-20.

### 3.4. Skill thay cho logic cài cứng
- V2 §7 đúng nguyên tắc.
- Nhưng skill/prompt ở v0.1 lệch với TCD. Xem B1(c).

### 3.5. "Không xây từ đầu": những phần ta tự viết dù đã có dự án OSI

| Tự viết | OSI có sẵn | Đánh giá |
|---|---|---|
| IAM mini (~130 dòng) | pycasbin, vakt | **Có lý do bằng số liệu** (`spike-iam.md:171-190`). Giữ |
| Model engine + planner | Alembic, Frappe DocType | **Có lý do** (`spike-model-engine.md:366-378`; `verified-repos.md:117`). Giữ |
| Hàng đợi tự viết nếu procrastinate trượt (V2:628) | procrastinate (MIT), **pgqueuer (MIT, 1.6.0)** | Trước khi tự viết, phải đưa **pgqueuer** vào spike làm ứng viên thứ hai |
| Cache JWKS "tự viết, có TTL" (V2:314) | `PyJWKClient(cache_keys=False, lifespan=…)` | `verification-spikes.md:125` chỉ cần **cấu hình** lại. Chỉ tự viết phần "xóa khi kid biến mất" nếu cấu hình không đủ |
| Kiểm thu hồi token qua "cache sự kiện admin" (V2:436) | Endpoint introspection của Keycloak | Introspection cho tool ghi là chuẩn có sẵn. Xem N3 |
| Audit hash-chain | `pg-tamper-evident-audit` (MIT, 0 sao, RA:29), Trillian/Tessera (RA:34-35) | Chấp nhận vì repo 0 sao chỉ đáng dùng làm ref (RA:55) |
| Compiler BPMN + layout DI | `bpmn-auto-layout` (giấy phép chưa kiểm, DEB:47) | Chấp nhận |
| Harness eval agent (V2:707) | promptfoo, inspect-ai (OSI) | Nên cân nhắc. Không chặn |
| Cấu hình realm bằng `kc_setup.py` | `keycloak-config-cli` (DEB:70) | Đã có điều kiện chọn. Chấp nhận |

---

## 4. Điều kiện chặn

**B1. Sửa V2 theo TCV1 và giải mâu thuẫn còn lại.**
- TCV1 đã có. TCD:385 đã thay placeholder bằng số đo thật: đúng tool 22/22, args hợp schema 22/22, đúng ngữ nghĩa 18/22 (TCV1:3696).
- Đối chiếu 5 điểm lệch của vòng 1 với bản chốt:
  - (a) Mô hình xác nhận: TCV1 không dùng tham số nào. V2 phải sửa (§8, E1–E3).
  - (b) Transport v0.1: TCV1:182 đã theo V2 (HTTP + `/confirm` ở v0.1). Coi như đã giải.
  - (c) Skill/prompt v0.1: TCV1:3704-3711 chốt 4 + 4. V2 phải sửa (E7).
  - (d) Hồ sơ model nhỏ: TCV1:3665 chốt core-10. V2 phải sửa (E6).
  - (e) Độ tươi xác thực: TCV1:170,181 chốt `max_age=0` + TOTP. Điều này khớp V2:493-494, vì cửa sổ 120 s chỉ là ngưỡng chấp nhận sau khi đã buộc đăng nhập lại. Coi như đã giải.
- **Còn mở một điểm:** TCV1:183, 3491-3492 giữ `cli_command="xt confirm …"` chạy trên TTY ở chế độ stdio. V2 (D8, V2:50) và DEB:57 đã bỏ hẳn CLI làm kênh người, vì terminal là thứ agent điều khiển được.
  - Phán quyết: giữ V2. Trong TCV1, `cli_command` luôn `null`, hoặc xóa khỏi schema lỗi. Ở stdio, `confirm_url` vẫn trỏ về Desk.
  - Đây là thay đổi additive/minor của contract, không đổi số tool.
- Việc phải làm trước 18/10:
  - Áp E1–E12 ở §8 vào V2.
  - Sửa `cli_command` trong TCV1.
  - Chủ dự án chốt V2:752 (CLAUDE.md ghi "~15 tool", TCV1 chọn 17).

**B2. Hostname cố định.**
- Smoke test v0.0 dùng được quick tunnel.
- Nhưng v0.1 bắt buộc có hostname ổn định, vì `KC_HOSTNAME`, URL connector và PRM `resource` phải khớp chính xác (`connector-requirements.md:16,18`).
- Chủ dự án phải trả lời V2:751 trước 18/10.

**B3. Bằng chứng step-up cho `/confirm`.** Phải có `research/spike-hitl.md`, chạy thật trên Keycloak 26.8.1 và chứng minh 3 điều:
- `max_age=0` buộc đăng nhập lại kèm TOTP, và `auth_time` được làm mới.
- `acr_values` cho `acr` đúng LoA (`kc_setup.py` đã có `acr.loa.map`).
- Token của `xt-desk` không dùng được cho `/mcp`.

Chưa có báo cáo này thì không code `/confirm`.

**B4. Sửa trích dẫn D6 và thêm test namespace.**
- (a) V2:48 phải ghi namespace `t<seq>__<model>` là **[chưa kiểm chứng]**. Khi port engine ở v0.0 phải có integration test: 2 tenant, model khác nhau, pool 1, FK chéo tenant bị chặn.
- (b) Phải chốt cách PostgREST ánh xạ bảng theo tenant **trước v0.2**. Có hai hướng: schema `api_t<seq>` với `Accept-Profile` (cần reload `db-schemas` khi thêm tenant), hoặc bỏ PostgREST và tự sinh `/data/v1` trong xt-api. V2:353 hiện chưa khả thi với D6. Ý (b) không chặn v0.1.

**B5. Ghi giấy phép trước khi thêm dep.** Mọi dep thêm vào `pyproject.toml` ở v0.0/v0.1 phải có dòng trong `THIRD_PARTY_NOTICES.md` (tên, phiên bản, SPDX, URL, cách dùng) cùng commit. Áp dụng cho procrastinate/pgqueuer, holidays, dateutil, Starlette, lxml, PyJWT, httpx, uvicorn. CI fail nếu dep runtime không có trong bảng.

**B6. Đóng các khoảng trống engine mức A mà W1 v0.1 phụ thuộc** (PACK §6). Theo coordinator, workflow-v2 spike đang xử lý song song. Phải có báo cáo chạy thật cho 4 mục sau trước khi code W1:
- **G-P3** (PACK:271): gateway chỉ kiểm `== 'reject'`, nên `tra_lai_bo_sung` đi tiếp như duyệt và vẫn ghi sổ. Trong khi đó `decide_request` của TCV1 nhận "trả lại" ngay từ v0.1 (TCV1:320).
  - Phải có nhánh tường minh cho `approve`, `reject`, `return`, và nhánh mặc định là lỗi.
  - Nếu chưa làm vòng trả lại thì `return` phải trả `capability_unavailable`, không được duyệt ngầm.
- **G-M1** (PACK:286): quyền `update` áp cho cả dòng. TCV1:1779 mới chặn field `state`; các field do workflow quản (người duyệt, bước hiện tại) vẫn bị `update_record` ghi được.
  - Cần cờ `managed_by: workflow` trong FieldSpec, và `update_record` từ chối các field có cờ này.
- **G-M2** (PACK:287): `FieldSpec` chưa có `pii`/`sensitive`, trong khi `describe_model` của TCV1 (TCV1:259,290) và quy tắc audit chỉ ghi hash cho field sensitive (V2:514) đều cần.
  - Thêm vào spec ngay v0.1, dù làn PII để v0.3.
- **G-P8** (PACK:276): process-spec không có schema; gõ nhầm `slaa` là mất timer mà compile vẫn OK.
  - Cần `ProcessSpec` pydantic `extra=forbid` trước khi gói W1 được cài qua luồng đề xuất.

Ba mục A còn lại (G-P1 song song, G-P2 timer khởi tạo, G-P4 lane động) và G-I1 (SoD theo quan hệ) trùng với phạm vi v0.2 của V2 (V2:609, 613). Không chặn v0.1.

---

## 5. Vấn đề không chặn

- **N1 (trước v0.3).** Sandbox proxy của Desk phải lấy từ gói npm hoặc tự viết. Không chép `sandbox.ts` của ext-apps vào repo (`spike-desk.md:42`, CL:48).
- **N2.** Fallback denylist `azp` là fail-open. Nên đổi sang allowlist: chỉ nhận `azp` là client do DCR tạo, hoặc gán `mcp-xt` riêng cho client DCR qua client policy. Kiểm ở smoke v0.0.
- **N3.** Kiểm thu hồi cho tool ghi: dùng introspection Keycloak (chuẩn), không tự dựng cache sự kiện admin. Ghi rõ độ trễ tối đa.
- **N4.** JWKS: thử `PyJWKClient(cache_keys=False, lifespan=…)` trước khi tự viết (`verification-spikes.md:125`).
- **N5.** Spiff:
  - Thêm trần độ dài và số node cho `check_expr` (`verification-spikes.md:83`).
  - Giữ subclass vá bug `call_service` của 3.2.0 (`spike-spiffworkflow.md:103`). V2 chưa nhắc.
- **N6.** Cho `/mcp` đi qua APISIX ngay từ v0.1 (chế độ passthrough): tunnel → APISIX → xt-api. Như vậy rủi ro SSE/buffering (`building-blocks.md:143`) lộ ra sớm, không dồn tới v0.2.
- **N7.** Có ba ngôn ngữ biểu thức: rule DSL của model engine, AST Python của Spiff, condition của IAM. Nên gom chung một module `domain/expr` để không lệch semantics.
- **N8.** Chốt công cụ sinh SBOM/THIRD_PARTY (ví dụ `cyclonedx-py`, Apache-2.0) và thêm SHA256SUMS vào `make release`. V2:691 đang ghi là đã có.
- **N9.** Chốt forge công khai + Issues (bug tracker, CL:53). Push từ v0.1 để I.1 có lịch sử thật.
- **N10.** APISIX chặn `/auth/admin` từ ngoài. Không lộ UI master/filer của SeaweedFS.
- **N11.** Bốn nguyên lý dồn vào tuần cuối. Cần chuẩn bị sẵn slide giải thích nếu phải cắt (ii).
- **N12.** Chưa có kế hoạch cộng đồng cho II.11: người dùng thử, `server.json` MCP Registry, hướng dẫn đóng góp.
- **N13.** Tài nguyên máy: RAM còn khoảng 6 GB, swap đã đầy, đĩa còn 7,5 GB. Phải đo `minimal` ngay ở v0.1, không đợi v0.2. Mọi lần pull image > 1 GB phải chạy `df -h /` trước (CLAUDE.md).
- **N14.** v0.1 resolve người duyệt theo `role(x)`, vì `manager_of`/`org_unit` để v0.2 (V2:609,613). Seed demo phải gán role "trưởng phòng" trực tiếp.

---

## 6. Đánh giá mốc

- **v0.0 (18/10).**
  - Phạm vi hợp lý, trải trên 3 ngày, cần ≥5 agent chạy song song.
  - Nghiệm thu ở V2:731 mới có 3 dòng, chưa kiểm phần port spike. Danh sách đầy đủ ở §7.1.
  - Khả thi nếu B1–B3 được đóng song song với việc port.
- **v0.1 (27/10).**
  - Phạm vi dày: OAuth, RLS, model engine + state, Spiff + worker, `/confirm` có step-up, SoD, tập tool, skill, tarball.
  - Khả thi trong 9 ngày nếu chạy song song theo module đã có spike.
  - Rủi ro lớn nhất:
    - `/confirm` step-up (B3).
    - Claude Free phụ thuộc thao tác tay của chủ dự án.
    - Goose + 4B chạy chuỗi nhiều bước với catalog đầy đủ (chưa kiểm vượt 9 tool, `spike-local-llm.md:90`).
  - 8 tiêu chí ở V2:732 cụ thể, nhưng thiếu: auth negative, SoD, idempotency, PoF, footprint. §7.2 bổ sung đủ.
  - Có thêm từ PACK:
    - Đồng bộ workflow → record (G-P11, PACK:279,309) chưa kiểm ở gói đề xuất. Đây là phần runtime v0.1 phải tự viết, thay cho harness `Instance.act()` (PACK:120).
    - Leo thang SLA đang **thay** người chứ không nhắc, và bắt GĐ duyệt 2 lần (G-P5, PACK:149,273). Nghiệm thu (4) phải nêu rõ hành vi mong đợi (T1-12).

---

## 7. Danh sách nghiệm thu cho agent tester

Quy ước chung:
- `$XT_HOST` là hostname công khai. Mọi lệnh chạy từ bản clone sạch `/tmp/xt-accept`.
- GUI chỉ chạy Playwright headless hoặc Xvfb riêng. Cấm dùng `:0`/`:1`.
- "OWNER" là bước chỉ chủ dự án làm được (tài khoản claude.ai). Tester thu bằng chứng qua log server.
- Kết quả ghi vào `docs/acceptance/v0.0.md` và `v0.1.md`, mỗi test có output thật.

### 7.1. v0.0 (cổng 18/10)

| ID | Lệnh / thao tác | Đạt khi |
|---|---|---|
| T0-01 | `make check` | Exit 0. `lint-imports` có thêm contract `xt.desk` không import `xt.application` ở trạng thái KEPT |
| T0-02 | `uv run pytest tests/unit -k iam -q` | 32 case × {mini, casbin oracle} pass, gồm case 26–32. Có thêm case cho `ge`, `in` |
| T0-03 | `uv run pytest tests/unit -k "spiff or workflow" -q` | 33 payload bị chặn. 6 spec xấu bị chặn. Role chứa `&`/`<` ra `SpecError` (theo `ID_RE`), không phải lỗi lxml. BPMN có `scriptTask`/DOCTYPE bị từ chối |
| T0-04 | `uv run pytest tests/integration -k "spiff_reload" -q` (PG17 thật, cột `json`) | Reload đúng ở 3 dạng: tuần tự, song song, vòng lặp. Timer sống sót sau reload |
| T0-05 | `uv run pytest tests/unit -k jwt -q` | Các ca VS B bị từ chối với 401: `azp` lạ, `exp−iat` vượt trần, `typ=Refresh`, thiếu `sub`, kid đã xóa (sau TTL). Kid mới xoay passive→active được nhận ngay |
| T0-06 | `uv run pytest tests/integration -k "rls or model_engine" -q` (postgres:17-alpine) | Không ngữ cảnh → 0 dòng. Pool 1 qua A/B không rò. 18/18 tên độc bị chặn. COPY dưới RLS bị chặn. `verify` chỉ đúng seq bị sửa. **2 tenant, model khác nhau, bảng `t<seq>__<model>`**: A thấy 0 dòng của B, FK chéo tenant bị chặn (B4a) |
| T0-07 | Đọc `research/spike-queue.md` | Có số đo thật: rollback thì job không tồn tại; job mang `tenant_id`; worker đặt GUC tenant; retry; periodic; thông lượng. Đã thử procrastinate **và** pgqueuer. Có quyết định |
| T0-08 | `curl -s https://$XT_HOST/auth/realms/xt/.well-known/openid-configuration` | `issuer` = `https://$XT_HOST/auth/realms/xt`. Có `S256`. Có `registration_endpoint`. Thời gian < 10 s |
| T0-09 | `curl -si -X POST https://$XT_HOST/mcp` rồi `curl -s https://$XT_HOST/.well-known/oauth-protected-resource/mcp` | 401, `WWW-Authenticate` có `resource_metadata=` và `scope=` chứa `mcp-xt`. PRM có `resource` == `https://$XT_HOST/mcp` từng ký tự và 1 authorization server |
| T0-10 | Script e2e OAuth (dựa trên `spike-mcp-auth/e2e.py`) qua tunnel | DCR với `claude.ai` callback → 201; `evil.example` → 403. Token có `mcp-xt` gọi `whoami` → 200. Token không có `mcp-xt` → 401 |
| T0-11 | OWNER thêm connector trên Claude Free và hỏi "whoami" | Log xt-api có `tool=whoami`, `azp` là client DCR, `sub` của chủ dự án, `protocolVersion` đã thương lượng. Ghi lại Claude có gửi `scope`/`resource` hay không. Kết quả lưu ở `research/connector-smoke.md` |
| T0-12 | OWNER thử ChatGPT developer mode | Ghi kết quả thật (thành công, chỉ đọc, hoặc bị từ chối) vào `connector-smoke.md`. Đạt nghĩa là đã ghi, không cần thành công |
| T0-13 | `ls research/spike-hitl.md`; `grep -n confirmation_ref docs/architecture-v2.md`; `grep -n cli_command docs/tool-contracts-v1.md` | `spike-hitl.md` tồn tại. V2 đã áp E1–E12 (§8), grep `confirmation_ref` trong V2 rỗng. `cli_command` luôn `null` hoặc đã bị xóa (B1) |
| T0-14 | `make release && tar tzf dist/xt-*.tar.gz` | Có số phiên bản. Không có `node_modules`, `.venv`, bundle |
| T0-15 | `df -h /`; `docker ps -a` | Đĩa trống ≥ 5 GB. Không còn container spike thừa |
| T0-16 | Đọc báo cáo workflow-v2 spike | G-P3, G-M1, G-M2, G-P8 đã đóng bằng test chạy thật (B6). Có tái hiện lại probe ở PACK:177-191 sau khi sửa: khóa lạ bị từ chối, `return` không còn thành APPROVED |

### 7.2. v0.1 (27/10, tag v0.1.0 ≤ 28/10)

| ID | Lệnh / thao tác | Đạt khi |
|---|---|---|
| T1-01 | `git clone` sạch → `make config && make build && make up PROFILE=minimal && make demo-seed && xt doctor` | Mọi bước exit 0, không sửa tay tệp nào. `xt doctor` báo OK cho postgres, keycloak, xt-api, xt-worker, xt-desk(`/confirm`) |
| T1-02 | `make check`; `make release`; `grep -rnE "TODO\|FIXME" src` | Xanh. Có `dist/xt-0.1.0.tar.gz` + SHA256SUMS. CHANGELOG có mục 0.1.0. Có `LICENSING.md`. `THIRD_PARTY_NOTICES.md` đủ mọi dep runtime. grep rỗng |
| T1-03 | Bộ auth negative trên stack thật (`tests/boundary/test_auth_live.py`) | 401 cho: không token (có PRM), `azp` không được phép (`xt-desk`/`xt-bot`/client thử), hết hạn, `alg=none`, HS256, payload sửa, refresh/id token làm bearer, token realm master, `exp−iat` vượt trần, `typ≠Bearer`, thiếu `sub`, kid đã xóa. Kid mới (passive→active) → 200. User bị disable thì tool ghi bị từ chối trong ≤ 60 s |
| T1-04 | Seed user thuộc 0, 1 và 2 Organization | 1 org → đúng `tenant_id`. 2 org → `tenant_ambiguous`. 0 org → từ chối |
| T1-05 | `pytest tests/integration -k "rls or tenant"` (chạy trong CI) | Như T0-06 trên stack compose. `xt_app` không tắt được RLS. Không ngữ cảnh → 0 dòng |
| T1-06 | Kịch bản (1) tự động: MCP client script (OAuth scripted, user nhân viên) + Playwright headless cho `/confirm` | Draft "tạm ứng 8 triệu đi Đà Nẵng 3 ngày" → submit ở L1, **không** trả `confirmation_required` → trưởng phòng thấy trong inbox → duyệt ở `/confirm` (mật khẩu + TOTP) → vì 8 tr > 5 tr nên sinh bước GĐ → GĐ duyệt → state `da_duyet`. Audit đủ event theo thứ tự |
| T1-06b | Đổi ngưỡng 5 tr → 10 tr bằng **dữ liệu gói** (đề xuất + duyệt), rồi tạo đơn 8 tr mới | Đơn mới không qua GĐ. `git diff src/` rỗng, tức không đổi code |
| T1-07 | OWNER chạy kịch bản (1) trên Claude Free qua `$XT_HOST` | Log có chuỗi tool đúng, `azp` của client Claude. Bước duyệt đi qua `/confirm` |
| T1-08 | Kịch bản (2): `den_ngay < tu_ngay` | Lỗi `RULE_VIOLATION` hoặc `CONSTRAINT_VIOLATION`, thông điệp tiếng Việt nêu "Đến ngày". Lần gọi kế tiếp đã sửa ngày thì thành công (log) |
| T1-09 | Kịch bản (3): token agent gọi `decide_request` | Trả `confirmation_required` với `confirm_url` = `https://$XT_HOST/desk/confirm/{id}`. URL không chứa `token=`, `code=` hay `eyJ`. Input schema của mọi tool **không** có tham số gate (TCV1:20). Playwright mở URL khi chưa đăng nhập → bị đưa tới Keycloak (`max_age=0`). Đúng mật khẩu nhưng thiếu hoặc sai TOTP → không có Decision. Sau khi người bấm Đồng ý, chính trang thực thi pending operation; Decision có `auth_time`, `acr`, `azp=xt-desk`. Agent gọi lại **y hệt** (cùng `idempotency_key`) → `meta.receipt.replayed=true`. Pending quá TTL 10 phút → `confirmation_declined` hoặc hết hạn, không thực thi |
| T1-10 | SoD | Agent đại diện người đề nghị gọi `decide_request` trên đơn của chính người đó → `access_denied` kèm `explain_ref`, **không** sinh link xác nhận (TCV1:150-159). Người đề nghị mở `/desk/confirm` đơn của mình → bị từ chối. Với role `xt_app`, chèn SQL `decisions` có `decider = requester` → trigger báo lỗi |
| T1-11 | Trần tự chủ | Agent DCR mới chỉ được L0. Sau khi người chủ cấp L1 thì tạo/submit được. Hành động L2 không có xác nhận → `confirmation_required`. `decide_request` không bao giờ thực thi bằng token agent, kể cả khi policy tenant cho phép |
| T1-12 | Kịch bản (4): SLA PT2M; giữa chừng chạy `docker compose restart xt-worker` | Trong ≤ 2 phút 15 s có task leo thang cho GĐ + bản ghi notify. Restart không làm mất timer. Hành vi theo G-P5 phải khớp đúng bản đã chốt trong workflow-v2 spike: nếu GĐ đã duyệt bước leo thang thì **không** bắt GĐ duyệt lần 2 cho cùng đơn; nếu vẫn chọn hành vi cũ thì phải ghi rõ trong README gói |
| T1-13 | Kịch bản (5): đề xuất thêm field `ma_du_an` (text, không bắt buộc) | Plan tiếng Việt có `[AN TOÀN]`. Agent không tự apply được. Duyệt ở `/confirm` → worker chạy pha 2 → cột có trong `information_schema.columns`, còn 0 constraint NOT VALID. Tạo bản ghi có field mới thành công. Drop field có dữ liệu mà chỉ 1 người duyệt → bị từ chối |
| T1-14 | Kịch bản (6): `xt audit verify --tenant A`; superuser tắt trigger và sửa 1 dòng | Trước khi sửa: `(n, null)`. Sau khi sửa: trả đúng seq bị sửa. Tenant B vẫn `(m, null)` |
| T1-15 | Kịch bản (7) | Bằng T1-05, xanh trong CI |
| T1-16 | Kịch bản (8): Goose CLI + `qwen3.5:4b`, `num_ctx ≥ 16384` (TCV1:3667), chạy create + submit 3 lần với catalog 17 tool; lặp lại bằng vòng agent Python với hồ sơ core-10 | Đạt ≥ 2/3 ở mỗi cấu hình, args hợp lệ ngay lần đầu. Ghi latency từng lần. Mọi lần `decide` đều ra `confirmation_required` |
| T1-17 | `pytest -k "contract"` | `tools/list` có đúng **17 tool** theo TCV1:302-322, digest khớp catalog sinh từ contract và giống nhau giữa 2 user. 0 tool destructive, 0 openWorld (TCV1:19,60). Mọi tool có `title` + 3 hint, `annotations == derive(effects)`. Mọi example pass. Không tool app-only nào ghi. Không input nào có `confirmation_ref`, `approval_ref` hay `preview_token` |
| T1-18 | `pytest -k "spiff_security or reload"` trong CI | Như T0-03 và T0-04, chạy trên image xT |
| T1-19 | `docker stats --no-stream`; `df -h /` | Ghi tổng RAM của profile `minimal` vào `INSTALL.md`. Đĩa trống ≥ 4 GB sau `make up` |
| T1-20 | Khởi động xt-api thiếu `XT_OIDC_ISSUER`; ToolContract thiếu `effects` | Exit ≠ 0 kèm thông báo rõ, không chạy bằng giá trị mặc định ngầm. Nếu xt-desk chạy không có LLM hoặc xt-bot không có token thì `xt doctor` báo rõ là đang tắt |
| T1-21 | `git tag v0.1.0`; nếu đã push thì kiểm release trên forge | Tag trỏ đúng commit của tarball T1-02 |
| T1-22 | Gọi `upload_file` và `aggregate_records` trên v0.1 | Trả `capability_unavailable` có remediation, không giả thành công (TCV1:3732). Schema tool vẫn đầy đủ |
| T1-23 | `decide_request(decision="return")` trên W1; gọi engine với quyết định lạ | Hoặc có nhánh trả lại thật (đơn quay về người đề nghị), hoặc `capability_unavailable`. **Không bao giờ** ra APPROVED hay ghi sổ (G-P3). Giá trị ngoài enum → lỗi |
| T1-24 | Người đề nghị (hoặc agent L1 của họ) `update_record` field `state`, người duyệt, bước hiện tại của đơn đang chờ duyệt | `state` → `precondition_failed` + continuation `transition_record` (TCV1:1779). Field có `managed_by: workflow` → bị từ chối (G-M1) |
| T1-25 | Cài process-spec có khóa lạ (`slaa`) hoặc biến `when` không khai báo | Bị từ chối lúc validate kèm thông điệp rõ (G-P8), không compile ra BPMN |
| T1-26 | `describe_model` của model tạm ứng/nhân sự có field `sensitive`; sửa field đó rồi đọc audit | Spec trả `pii`/`sensitive` (G-M2). Audit diff chỉ có hash, không có giá trị (V2:514) |

---

## 8. Sửa bắt buộc trong V2 để khớp TCV1 (TCV1 là nguồn sự thật cho bề mặt tool)

| # | Chỗ trong V2 | Hiện ghi | Sửa thành (nguồn) |
|---|---|---|---|
| E1 | V2:111 (§1.3) | "Một tên tham số gate duy nhất `confirmation_ref` (F16)" | "**Không có tham số gate.** Lời gọi thiếu xác nhận tạo PendingOperation (TTL 10 phút) và trả `confirmation_required` {`confirm_url`, `confirmer`, `plan_summary`, `plan_digest`, `expires_at`, `continuation`}. Trang xác nhận tự thực thi; agent gọi lại cùng `idempotency_key` để nhận receipt replay" (TCV1:20,161-177) |
| E2 | V2:97 (năng lực 1); V2:490 (§5.5.1) | `confirmation_ref` + `details.confirmation_ref` | `details.confirmation_id` (pending) + `confirm_url` + `continuation` (TCV1:3731) |
| E3 | V2:491 (§5.5.2), V2:185-186, V2:50 | Route `/confirm/{id}` | `/desk/confirm/{id}` qua APISIX → Desk backend, client `xt-desk` không bao giờ là DCR (TCV1:168,181) |
| E4 | V2:91-92, V2:752, V2:732 ("tập tool đầu tiên theo TCV1 (đọc, tạo/sửa, …)") | Đề xuất 16–17; ≤22 | **17 tool ở v0.1, 21 ở v1.0**, liệt kê theo TCV1:302-322 và §4.2 |
| E5 | V2:478-484 (§5.4) | L1 không có giao việc; L2 "giao việc cho người khác"; hard gate | Theo TCV1:139-145: L1 gồm giao việc cho cấp dưới trực tiếp (policy mặc định); L2 là giao ngoài tuyến hoặc field `impact: high`; lớp `human_only` tách khỏi thang L |
| E6 | V2:115, V2:664, V2:707 | Hồ sơ "core-8"; `num_ctx` 8k và 16k | **core-10** (TCV1:3665). Client local dùng catalog đủ phải đặt `num_ctx ≥ 16384`, vì 17 tool tốn 8.155 token (TCV1:3667). Bỏ cấu hình eval 8k với catalog đủ |
| E7 | V2:659 (§7), V2:732 ("3 skill-prompt") | 2 skill + 3 prompt | 4 skill (`xt-core`, `xt-approvals`, `xt-tasks`, `xt-data-modeling`) + 4 prompt (+`change_model_safely`) (TCV1:3704-3711) |
| E8 | V2:519-527 (§5.7), V2:156 | `pii ≥ basic`; `local` theo `full\|masked` | Field `pii: true` / `sensitive: true`; `llm_egress ∈ {masked, local_only}`; `cloud` → masked hoặc omitted; dạng `[PII:token]`; ghi token ngược bị từ chối (TCV1:257-272) |
| E9 | V2:408-416 (§4.3) | Sáu đầu ra gồm OpenAPI | TCV1:49 liệt kê CLI thay cho OpenAPI. Giữ OpenAPI 3.1 (bằng chứng cho đề và CRIT 27), và sửa TCV1:49 cho đủ cả hai. CLI chỉ là CLI vận hành, không phải kênh xác nhận |
| E10 | V2:93-102 + bảng §10 v0.1 | Sáu năng lực coi như có ở v0.1 | Thêm cột "khả dụng": `upload_file`, `aggregate_records` trả `capability_unavailable` tới v0.2; làn PII ở v0.3 (TCV1:3732) |
| E11 | V2:498-501 (§5.5 SoD) | SoD ở trigger + delegation | Thêm thứ tự kiểm của TCV1:147-157: IAM/SoD trả `access_denied` **trước** khi tạo xác nhận, nên agent tự duyệt không bao giờ ra link (storyboard S2a) |
| E12 | V2:187, V2:733 (MCP App thẻ duyệt) | "thẻ duyệt" | Nút "Duyệt" trên thẻ mở `confirm_url` bằng `ui/open-link`; không có tool app-only để ghi; thẻ tốn 2 chạm, Telegram 1 chạm (TCV1:186) |

Ngoài V2 còn phải sửa (không chặn):
- Storyboard theo TCV1:3719-3725.
- `cli_command` trong TCV1 (B1).
- TCV1:49 (E9).

---

## 9. Khoảng trống engine từ PACK: ánh xạ vào mốc

| ID | Mức | Mốc phải đóng | Lý do |
|---|---|---|---|
| G-P3 cổng quyết định | A | **v0.1 (B6)** | `decide_request` có "trả lại" ở v0.1; hiện engine duyệt ngầm |
| G-M1 field do workflow quản | A | **v0.1 (B6)** | `update_record` có ở v0.1 |
| G-M2 nhãn PII trong FieldSpec | A | **v0.1 (B6)** (chỉ spec + audit hash); làn PII ở v0.3 | TCV1 `describe_model` đã có field này |
| G-P8 ProcessSpec `extra=forbid` | B | **v0.1 (B6)** | Gói W1 cài qua đề xuất |
| G-P11 đồng bộ workflow → record | B | v0.1 (runtime dispatcher) | T1-06 kiểm `state=da_duyet` |
| G-P5 leo thang thay người, GĐ duyệt 2 lần | B | v0.1 (chốt hành vi), v0.2 (boundary không ngắt) | T1-12 |
| G-P9 lỗi service bị che, G-P10 op không idempotent, G-P12 role chưa escape | B/C | v0.1 (worker) | Trùng VS #6 và bug `call_service` (N5) |
| G-P1 song song, G-P2 timer khởi tạo, G-P4 lane động, G-I1 SoD theo quan hệ, G-P7 hành động IAM theo bước, G-I2/G-I3 | A/B | v0.2 | Trùng phạm vi `parallel`, `trigger`, resolver, W2 của V2:609 |
| G-M3 scope participant, G-M4 rule nhiều dòng, G-P6 SLA từ dữ liệu | B | v0.2–v0.3 | W2/W4 |
| G-M5 namespace gói, G-I7 id statement theo gói | B | **cùng B4** (trước khi chốt định dạng gói) | Đụng D6 namespace theo tenant |
| G-M6, G-M7, G-M8, G-I4–G-I6 | B/C | v0.3 | Nhập lô, ủy quyền |

Ghi nhận thêm:
- PACK cho thấy W3 on/offboarding đã chạy được (PACK:163-175), dù V2 xếp ở v0.3. Có thể kéo sớm nếu cần.
- 3 gói hiện **chưa có `pack.json` nào được engine đọc** (PACK:311). Phải có loader trước khi đếm "W5/W7 chỉ YAML" ở v1.0 (V2:126).

---

## Trạng thái điều kiện (ghi chú của ARCHITECT, 15/10/2026; không sửa phần phán quyết ở trên)

- **B4: ĐÃ ĐÓNG.** Bằng chứng: `research/spike-model-tenancy.md`, chạy thật trên PG 17 + PostgREST 16.4.
  - D6 chuyển sang schema riêng cho từng tenant `xt_t<seq>.<model>` + `api_t<seq>`, vẫn giữ `tenant_id` + RLS FORCE.
  - PostgREST dùng `Accept-Profile` + `db-schemas` động + pre-request ràng tenant. B4(b) không cần phương án dự phòng.
  - Đã đưa vào `architecture-v2.md` bản v2.2: D5, D6, §3.3(c), §6.3.
- **B6, phần G-M1: ĐÃ ĐÓNG** (SMT §8): `managed_by: workflow`, quyền cột, role `xt_workflow`.
- **B6, phần G-M2: ĐÃ ĐÓNG** (SMT §9): nhãn `none/basic/sensitive` bắt buộc, HMAC có khóa trong audit.
- **B6, phần G-P3 và G-P8: CÒN MỞ.** Chờ báo cáo workflow-v2 spike (T0-16).
- **B1: ĐÃ HÒA GIẢI** trong v2.1 (E1–E12). Còn việc sửa TCV1 ở `:49`, `:183`, `:3491-3492`; việc này thuộc người giữ hợp đồng tool.
- **B2, B3, B5:** không đổi so với phán quyết.
