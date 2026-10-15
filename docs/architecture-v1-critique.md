<!-- SPDX-License-Identifier: Apache-2.0 -->
# Phản biện `architecture-v1.md` (vai CRITIC)

Ngày: 15/10/2026. Đối tượng: `docs/architecture-v1.md` (`arch-v1.0-draft`, 752 dòng). Ký hiệu `A Lnnn` là số dòng trong bản kiến trúc. Bằng chứng trong `research/` viết theo dạng `tệp Lnnn`.
Góc nhìn: kỹ sư phải ship v1.0.0 ngày 03/12/2026 với 2 người (chủ dự án + agent Claude) và phải thắng OLP.

**Kết luận chung.** Phần khung đã đúng hướng: tool cố định, nghiệp vụ là dữ liệu, RLS FORCE, audit hash-chain, Spiff có SafeEngine, bỏ Code Mode, Desk làm bề mặt xác nhận. Có 4 blocker cần xử lý trước khi viết code:
1. D6 (JSONB) đi ngược chính bằng chứng được trích và chưa có spike nào chống lưng.
2. Catalog 18 tool bỏ qua các blocker F1–F5 của `tool-contracts-critique.md`.
3. Lịch v0.1 và v1.0 không khả thi.
4. Kênh "người xác nhận" có lỗ hổng: CLI TTY và agent điều khiển trình duyệt.

Ngoài ra, thiết kế ngầm chỉ hợp với **luồng duyệt văn phòng**. Muốn "mọi doanh nghiệp định nghĩa được" thì còn thiếu tổ chức/người duyệt động, dòng chi tiết (line items), quan hệ có FK và action dữ liệu tổng quát.

Mức độ: **BLOCKER** = phải sửa trước khi code phần liên quan · **MAJOR** = sửa trước mốc kế tiếp · **MINOR** = sửa khi tiện.

---

## A. Mâu thuẫn với bằng chứng, quyết định thiếu bằng chứng

### 1. [BLOCKER] D6 "record JSONB" trái với bằng chứng được trích, và chưa có spike
- **Bằng chứng:**
  - A L34 và A L524 trích "BB §C" và "BB rủi ro #4" để bác bỏ bảng vật lý. Thực tế `building-blocks.md` L53 **chọn** đúng hướng ngược lại: "model (bảng/trường/rule/quyền) lưu trong catalog → **sinh DDL** trong transaction (Postgres DDL transactional), sinh RLS policy". BB L60 còn chỉ Baserow là "tạo bảng động thật trong Postgres → mã đáng học".
  - `spike-model-engine.md` không có trong `research/`. Chính A L21 và A L509 cũng ghi "[chờ kết quả]".
  - D6 là quyết định khó đảo ngược nhất của cả hệ thống, vì dữ liệu thật sẽ nằm trên nó. Vậy mà nó đang dựa trên một trích dẫn sai.
- **Vì sao JSONB làm hại đúng những gì chủ dự án muốn ("dùng thật", hiệu năng, BI/[D]):**
  - (a) Field quan hệ là XRN nằm trong JSON nên **không có FK**. Toàn vẹn tham chiếu phải tự viết. Đây cũng là chỗ model nhỏ hay sai (xem mục 26).
  - (b) Metabase/DuckDB/BI chỉ thấy view cast từ `values->>'x'`. Không có metadata FK nên không tự join được. Filter chỉ dùng index khi biểu thức khớp đúng index biểu thức.
  - (c) Lineage mức cột (DE L166 "phả hệ, nguồn gốc") quy hết về một cột `records.values`. Sơ đồ phả hệ trên bản tin trở thành vô nghĩa.
  - (d) Xuất Parquet phải tự xử lý schema theo từng revision. Với bảng thật thì `COPY`/DuckDB đọc thẳng.
  - (e) Ràng buộc kiểu/NOT NULL chỉ còn ở tầng ứng dụng và CHECK biểu thức. Poka-yoke "tại DB" yếu hơn lời quảng cáo ở A L179.
- **Lý do chính của A L518 ("rename/đổi kiểu là phần khó nhất") có cách tránh mà không cần JSONB.** Dùng pattern Baserow: cột vật lý đặt tên theo id bất biến `f_<field_id>`, tên hiển thị nằm trong catalog.
  - Rename chỉ sửa metadata, không DDL.
  - Thêm field là `ADD COLUMN` nullable, Postgres 11+ làm tức thì, không rewrite bảng.
  - Đổi kiểu hoặc bỏ field là L3: thêm cột mới, backfill bằng job, chuyển view, giữ cột cũ tới khi duyệt lần hai.
  - DDL nằm trong cùng transaction với activation (BB L53).
- **Phương án:**
  - Bảng thật cho từng model của từng tenant, đặt trong schema `m`, tên `m.<tenant_short>__<model_id>`. Vẫn có cột `tenant_id` + RLS FORCE để phòng thủ nhiều lớp.
  - Bảng lõi (records meta, history, audit, jobs) vẫn dùng chung.
  - View `api.<model>` đặt `security_invoker=true` với tên cột thân thiện.
  - `record_history` dùng trigger chung `to_jsonb(NEW)`.
  - Interface `RecordRepo` giữ nguyên như A L527.
- **Cổng quyết định:** spike 1 ngày (xong trước 18/10) chạy cả 2 phương án trên 100k bản ghi. Đo thời gian activation, `NOTIFY pgrst`, test RLS qua PostgREST, DuckDB `ATTACH postgres` → Parquet, Metabase/BI đọc view có FK. Nếu bảng thật vượt +3 ngày công so với JSONB thì giữ D6 hiện tại, nhưng ghi rõ giới hạn FK/lineage trong tài liệu.

### 2. [BLOCKER] Catalog 18 tool chép nguyên TC, bỏ qua blocker F1–F5 của bản phản biện contract
- **Bằng chứng:**
  - A L64 liệt kê đúng 18 tên của TC §6.
  - `tool-contracts-critique.md` L7–13 chỉ ra 4 blocker: F1 không có đường tạo xác nhận (L105), F2 không chuyển được trạng thái Record (L118), F3 không upload được file (L127), F4 không tổng hợp được số liệu (L136). Thêm F5 (L145): gate L2 quá tay.
  - A L452 vẫn xếp `submit_request` vào L2 cần approval. Agent không nộp nổi đơn của chính người dùng, nên màn flagship "tạm ứng 8 triệu" có thêm một vòng confirm vô lý.
  - A L114 tự hỏi có cần "tool thứ 19" cho metric hay không. Câu trả lời đã có ở F4.
- **Phương án: 16 tool ở v0.1** (gần "~15" của CLAUDE.md, khép câu hỏi mở Q1):
  - Đọc: `inspect_workspace`, `describe` (model | process | policy; gộp `describe_model` và `describe_process` vì cùng authority, cùng outcome), `search_records` (`model_id` tùy chọn, full-text xuyên model, `view` = truy vấn đặt tên, xem mục 26), `aggregate_records`, `get_record` (gộp `get_process_status`, đọc được text của document), `get_inbox`, `get_history` (có facet `explain`).
  - Ghi: `create_record`, `update_record` (`set` | `transition`), `upload_file`, `submit_request` (`submit` | `withdraw`), `decide_request`, `create_task`, `update_task`, `start_process`, `propose_change` (`target`: model | process-ops | policy).
  - Hoãn sang v1.0: `delete_record`, `notify`, `ack_notifications`.
- **Mức tự chủ theo F5:**
  - L1 gồm submit đơn của chính mình và tự chuyển trạng thái việc của mình.
  - Hard gate vĩnh viễn: `decide_request`, ký, lệnh chi, activate định nghĩa.

### 3. [MAJOR] Kết quả `spike-local-llm.md` chưa được tích hợp, kênh demo chính đang mâu thuẫn
- **Bằng chứng:**
  - A L151 chọn "Desk + LLM local **và Goose** + LLM local" làm kênh chính. Nhưng spike đo được Goose mất 10–19 s mỗi lượt và có 2/6 lượt không trả câu cuối (`spike-local-llm.md` L76). MCP Apps trên Goose Desktop chưa chạy được, vẫn "Experimental" (L12, L81). Spike kết luận "**Cần xT Desk**" (L89).
  - A L716 đặt Desk chat ở v1.0 và cắt đầu tiên nếu trễ. Như vậy kênh demo chính có thể không tồn tại.
  - `tools/list` khoảng 9k token (`tool-contracts-critique.md` L45). Spike chạy `num_ctx 8192` (`spike-local-llm.md` L20–27) và chưa thử trên 9 tool (L90). Catalog 18 tool có nguy cơ bị cắt prompt âm thầm (critique L50).
  - Bẫy `think:false` bị bỏ qua trên endpoint OpenAI-compatible, phải gửi `reasoning_effort:"none"` (L31). A chưa ghi điểm này.
- **Phương án:**
  - Desk chat chuyển vào **v0.3 làm lõi**. Vòng lặp agent khoảng 60 dòng đã chạy được (L40).
  - Desk đưa cho model local bộ "core-8" (critique L304, L351) và lọc tool `visibility:["app"]` (L50).
  - Ghim `qwen3.5:4b`, `reasoning_effort:"none"`, đo lại với `num_ctx` 16k trên GPU 6 GB.
  - Goose CLI chỉ dùng cho màn "client nguồn mở thứ ba". Mọi màn chấm UX đi qua Desk.

### 4. [MAJOR] D4 tự mâu thuẫn: "Spiff trong `xt-worker` tách tiến trình" so với hoàn tất user task trong một transaction ở use case
- **Bằng chứng:**
  - A L32 và A L218 đặt Spiff trong worker. Nhưng A L573 cho use case `decide_request`, vốn chạy trong `xt-api`, gọi `task.run()` → `do_engine_steps()` → lưu → audit trong **một transaction**. Như vậy `xt-api` cũng phải import Spiff.
  - Quy tắc "chỉ `adapters/workflow` import SpiffWorkflow" (A L386) vẫn đúng. Câu "trong xt-worker" thì sai.
- **Phương án (trả lời câu hỏi "worker riêng hay in-process"):**
  - Cả hai, cùng một image.
  - **In-process trong `xt-api`** cho bước người dùng (đồng bộ, người duyệt thấy ngay bước kế tiếp).
  - **`xt-worker`** cho timer, service task, outbox, ngoài transaction (A L582).
  - Không cần "dịch vụ workflow" container thứ ba. Sửa D4 và sơ đồ A L217–221 cho đúng.

### 5. [MAJOR] D9 "scope `mcp-xt` optional" xung đột với khuyến nghị connector, chưa test với ChatGPT
- **Bằng chứng:**
  - `connector-requirements.md` L16 và L60 khuyến nghị **giữ audience mapper ở realm default scope** để vẫn đúng `aud` khi client bỏ qua `resource`.
  - ChatGPT chỉ chắc chắn xin các scope OIDC trong `scopes_supported` (CR L17).
  - Nếu ChatGPT không xin `mcp-xt` thì token thiếu `aud`, kết nối rơi vào vòng 401.
  - `verification-spikes.md` L50 đưa ra hai cách sửa: bỏ khỏi default **hoặc** kiểm `azp`.
- **Phương án:**
  - Ngày đầu v0.2: test thật cả hai host với scope optional.
  - Nếu ChatGPT fail: giữ default scope, kiểm `azp` theo **denylist cố định** các client nội bộ (`xt-desk`, `xt-bot`, `xt-desk-agent`, `xt-data`, đều khai trong `realm-xt.json`). Danh sách ngắn, tất định, test được.
  - Đồng thời PostgREST dùng `aud` riêng (mục 21).

### 6. [MAJOR] JWT ở APISIX cho `/mcp` làm hỏng yêu cầu 401 + `resource_metadata`
- **Bằng chứng:**
  - Claude yêu cầu "401 **BẮT BUỘC** kèm `resource_metadata`; header trên 200 bị bỏ qua" (CR L18).
  - A L91 ("`curl` không token → 401 ở gateway") và A L495 để APISIX tự trả 401. Plugin `openid-connect`/`jwt-auth` không tự sinh header PRM.
  - Lỗi này chỉ lộ khi gắn connector thật, mà lúc đó đã là v0.2.
- **Phương án:**
  - Trên route `/mcp` và `/.well-known/oauth-protected-resource/mcp`, APISIX chỉ làm TLS, `limit-count` theo IP, chặn method lạ, rồi cho đi thẳng. `xt-api` tự trả 401 có PRM, kiểm JWT, rate-limit theo `azp` ở tầng ứng dụng.
  - JWT ở APISIX áp cho `/rest`, `/data`, `/workflow`.
  - Nếu muốn giữ JWT ở gateway cho `/mcp`: thêm `response-rewrite` có điều kiện status 401 để chèn header **[chưa kiểm chứng]**, có test boundary.

### 7. [MAJOR] "Xuất BPMN mở bằng công cụ khác" sẽ ra sơ đồ trống
- **Bằng chứng:** `spike-spiffworkflow.md` L157 ghi compiler "không sinh BPMNDI nên không mở được trong designer". Trong khi đó A L93 và red-team Q7(d) (`red-team.md` L92) dùng chính việc này làm bằng chứng cho giám khảo. DE L108–109 còn nêu "công cụ thiết kế trực quan... quy trình BPMN".
- **Phương án:** compiler sinh BPMNDI bằng layout tuyến tính đơn giản (khoảng 40 dòng: x tăng theo bước, nhánh lệch y), hoặc chạy `bpmn-auto-layout` (bpmn-io, **[chưa kiểm chứng giấy phép]**) lúc export. MCP App "process" tự vẽ SVG từ DI, không dùng bpmn-js (watermark, `red-team.md` L243). Demo: export XML → mở bằng Camunda Modeler → thấy đúng sơ đồ.

### 8. [MINOR] Spike IAM đã chạy nhưng kiến trúc không dùng
- **Bằng chứng:**
  - `incidents.log` L2: 75 test pass nhưng chưa ghi `spike-iam.md`.
  - `research/spike-iam/backends.py` so 3 backend (`mini` khoảng 59 dòng, casbin, vakt), cả ba qua 75 ca (`.codex_iam.log` L4230). Casbin `enforce_ex` "là rule witness, không phải explanation trace" (L4196).
  - A L426 lại **cấm `condition`**, trong khi spike có điều kiện ABAC (ví dụ `resource.amount ≤ 5000`) kèm trace giải thích.
- **Phương án:**
  - Dùng `mini` + `policy_language.py` của spike làm evaluator, hỗ trợ một tập condition đóng (`eq/ne/le/ge/in`, chỉ trên thuộc tính đã khai).
  - Casbin chỉ dùng làm **oracle trong CI** (differential test). Như vậy trả lời được câu "không xây từ đầu" bằng bằng chứng.
  - Ghi `spike-iam.md`.

### 9. [MINOR] Trích dẫn lệch
- A L132 trích "SAPPS §3.3" cho luận điểm "cùng token". Bằng chứng thực ở `spike-mcp-apps.md` L19 (host proxy `tools/call`) và CR L22 ("proxied through Claude's backend").
- A L21: `spike-local-llm.md` đã có, không còn "[chờ kết quả]".
- A L162: Metabase OSS là AGPL-3.0, giấy phép OSI. Lý do loại hợp lệ là JVM/UI, không phải giấy phép.

---

## B. Over-engineering so với lịch

### 10. [BLOCKER] Lịch không khả thi
- **Bằng chứng:**
  - v0.1 (A L704) có 11 ngày cho: stdio + CLI, RLS, model engine đủ vòng proposal → activate, Spiff + worker + timer, Task/SLA, audit, 18 tool, skill, release. Mỗi mục đều kèm test tích hợp thật (A L678).
  - v1.0 (A L707): từ 23/11 tới RC 30/11 chỉ có 7 ngày cho khoảng 12 hạng mục lớn (W7, W6 + ký, W8 + lineage, PII, IT/OT + NATS, Parquet + DuckDB, ClamAV, OpenBao, GitOps, backup/restore, eval, Desk chat, Registry, tài liệu VN/EN).
  - Đường găng thật (OAuth với host thật, SDK 2.3.0 so với spec 2025-11-25, CR L29) bị dời tới v0.2.
- **Phương án:** xem mục "Lịch đề xuất" ở cuối. Nguyên tắc:
  - (a) v0.1 là **walking skeleton W1** chạy qua HTTP + OAuth, kiểm thật với Claude qua tunnel ngay tuần đầu. Spike SAUTH đã có e2e, nên chi phí thấp mà xóa được rủi ro lớn nhất.
  - (b) Đóng băng tính năng 28/11. Từ 29/11 tới 03/12 chỉ test, quay video, viết tài liệu.
  - (c) W7 Helpdesk phải là **gói dữ liệu thuần, không code mới**. Đây là phép thử tính phổ quát (mục 23–25).

### 11. [MAJOR] Những gì nên cắt, gộp hoặc hoãn mà không mất điểm
| Hạng mục (A) | Đề xuất | Lý do |
|---|---|---|
| WAF Coraza (A L107) | Cắt. Dùng plugin có sẵn của APISIX (`uri-blocker`, `request-validation`, `limit-*`) | "[chưa kiểm chứng tương thích APISIX 3.19]", có thể ngốn vài ngày |
| OpenBao cấp credential DB động (A L104) | Chỉ dùng KV + Transit (khóa ký W6), script init một lần | Credential động tốn công, giám khảo khó thấy |
| Prometheus container (A L163, L642) | Chỉ endpoint `/metrics` + OTel trace id | Không có màn demo nào dùng |
| Neo Merkle root vào git công khai (A L480) | Chỉ neo vào `s3://anchors/` | Tùy chọn, ít điểm |
| `xt gitops sync` tự viết (A L121) | `keycloak-config-cli` (adorsys, Apache-2.0, **[chưa kiểm chứng hỗ trợ KC 26.8]**) cho realm. APISIX standalone tự reload YAML. Self-heal bằng `restart` + healthcheck | Mục 30 |
| 7 MCP App (A L138) | v0.3 có 4 app: hộp việc/thẻ duyệt, form sinh từ model, Kanban + SLA, diff proposal. v1.0 thêm bản tin. Orgchart lấy từ spike. Digital twin chỉ là màu trạng thái trong bản tin | Ít mà sâu, tương tác thật (red-team Q1a) |
| 11 prompt, 9 skill (A L617–629) | v0.1: 3 prompt + 2 skill (`xt-core`, `xt-approvals`) | critique F27 L304 |
| `propose_process_change` dạng graph đầy đủ (12 KB schema) | v0.1 dùng process mẫu. v1.0 chỉ cho operation có giới hạn (`set_threshold`, `set_approver`, `set_sla`, `add_step`) | critique L317 |
| 2 người duyệt cho mọi L3 (A L453) | Chỉ áp cho model change dạng destructive và erase PII | Nếu không, demo cần 3 tài khoản cho mỗi thao tác |
| Alembic `downgrade` (A L310) | Giữ Alembic, nhưng bỏ lý do "rollback demo". Rollback model đi qua model engine (revision), không qua Alembic | Lập luận sai, không đổi công sức |

### 12. [MINOR] PostgREST có đáng không? **Có, giữ.**
- Một binary, đáp ứng đúng chữ "tự động tạo lập API từ CSDL tích hợp phân quyền tới cấp dòng" (DE L74–75). Supabase, ví dụ đề nêu ở DE L156, cũng dựa trên PostgREST.
- Chi phí thật nằm ở 3 điểm cấu hình mà A chưa ghi:
  - (a) JWKS phải nạp tĩnh vào `jwt-secret`, nên khi xoay khóa phải reload PostgREST. Gắn bước này vào quy trình xoay khóa (VS #5).
  - (b) Cần `jwt-role-claim-key` hoặc role mặc định, vì token Keycloak không có claim `role`.
  - (c) Rủi ro ở view, xem mục 21.
- Chỉ đọc là đúng (A L301).

### 13. [MINOR] NATS có cần không? Chỉ ở profile `full`, v1.0, phạm vi tối thiểu
- Một stream `xt.<tenant>.>`, một consumer có ack, cổng MQTT cho cảm biến giả lập. NATS gọn và rẻ nhất để phủ 3 ô (`red-team.md` L247). Giữ như A L120.
- Sửa cách nói: "hàng đợi... **chống chối bỏ**" (DE L95–96) nghĩa là bằng chứng mật mã, không phải ack.
- Ký Ed25519 từng outbox event, dùng chung khóa Transit với W6, và đưa `event_hash` vào hash-chain. Khi đó "chống chối bỏ" là thật, còn NATS chỉ là phương tiện vận chuyển.

### 14. [MINOR] Một realm hay realm-per-tenant? **Một realm là đúng** (CR L18: chỉ dùng phần tử đầu của `authorization_servers`)
- Mapper Organizations → `tenant_id` chưa kiểm chứng (A L411).
- Ràng buộc v1.0: **mỗi user thuộc đúng một Organization**. Token có từ 2 org trở lên thì từ chối với `tenant_ambiguous`, tránh phải làm UI chọn tenant.
- Test trong tuần đầu v0.2.

---

## C. Bảo mật

### 15. Đánh giá luận điểm "kênh con người" của xT Desk
**Luận điểm đúng.** Iframe gọi `tools/call` thì được host proxy bằng cùng kết nối và token (`spike-mcp-apps.md` L19, CR L22). `visibility:["app"]` chỉ do host lọc, server không kiểm được (`spike-mcp-apps.md` L13). Tool app-only vẫn nằm trong `tools/list` (`spike-local-llm.md` L50). Vì vậy nút bấm trong MCP App chạy trên host ngoài không thể là bằng chứng có người. **Nhưng thiết kế hiện tại còn 4 lỗ** (mục 16–19).

### 16. [BLOCKER] Agent điều khiển trình duyệt bấm được `/confirm` trong phiên Desk của người
- **Bằng chứng:**
  - A L470 chỉ dựa vào "phiên OIDC của người", step-up MFA chỉ khi vượt ngưỡng.
  - Claude in Chrome, ChatGPT agent và computer-use đều thao tác ngay trong trình duyệt đang đăng nhập của người dùng.
  - A L473 trả `confirm_url` cho agent. Agent có cả URL lẫn khả năng bấm.
- **Phương án:**
  - **Mọi** confirmation phải có xác thực tươi ở **thiết bị/yếu tố agent không chạm được**:
    - L2: nút Telegram trên điện thoại (thiết bị khác), **hoặc** `/confirm` với OIDC `max_age=0` + TOTP.
    - L3 và vượt ngưỡng: `/confirm` với `prompt=login` + `acr_values` (TOTP hoặc WebAuthn; Keycloak có Level-of-Authentication theo ACR, **[chưa kiểm chứng trên 26.8]**, như A L103).
  - Lưu `auth_time`, `acr`, `azp=xt-desk` vào Confirmation. Server từ chối nếu `now − auth_time > 120 s`.
  - `confirm_url` chỉ là **locator**, không phải capability: không chứa token, không tự duyệt khi mở.
- **Phương án thay thế đã cân nhắc:**
  - *Link ký một lần (bearer):* loại. Link đi qua ngữ cảnh agent, nên ai có link là duyệt được.
  - *CIBA của Keycloak:* chuẩn hóa đẹp (`binding_message` = mã ngắn của digest, token do Keycloak cấp). Nhưng phải tự cài endpoint "authentication channel" và luồng callback **[chưa kiểm chứng]**. Để sau v1.0, ghi vào roadmap.
  - *Elicitation dạng URL của MCP:* đưa `confirm_url` lên đúng kênh chuẩn. Host có hỗ trợ hay không thì **[chưa kiểm chứng]** (critique L114). Làm như lớp hiển thị bổ sung, không phải biên bảo mật.

### 17. [MAJOR] CLI `xt decide` trên TTY (v0.1) không chứng minh được có người
- **Bằng chứng:**
  - A L469 coi "người gõ trên TTY" là bằng chứng. Claude Code và Goose đều có công cụ shell. `script -qc` tạo TTY giả. Tệp danh tính stdio (A L415) đọc được bởi chính agent chạy cùng user OS.
  - Hệ quả: màn demo (3) "agent gọi `decide_request` → bị chặn" ở v0.1 bị phá chỉ bằng `xt decide`.
- **Phương án:**
  - `xt decide` dùng **OAuth Device Authorization Grant của Keycloak**: CLI in mã, người duyệt đăng nhập trên điện thoại có MFA. Đây là chuẩn có sẵn, không phải tự viết.
  - Nếu Keycloak chưa có ở v0.1: TOTP (`pyotp`, MIT) nhập tay, ghi rõ "dev mode".
  - Danh tính stdio phải là **Actor kind=agent** có `owner_human_ref`, không phải danh tính người.

### 18. [MAJOR] Chưa có bất biến SoD (tách nhiệm)
- **Bằng chứng:** A chỉ ghi "không tự duyệt" ở mức skill và trần tự chủ (A L455, L620). Không có ràng buộc "người quyết định ≠ người đề nghị" theo **effective human** (critique F20 L266). Không có ràng buộc delegation không được bao gồm `decide`.
- **Phương án:**
  - Trigger DB trên `decisions`: `decider_human_ref <> request.requester_human_ref` (trừ khi policy có `self_approval_allowed`, có audit).
  - Delegation (A L413) và service account `xt-desk-agent` **không bao giờ** mang quyền `decide`, ký hay activate. Server kiểm, không dựa vào cấu hình Keycloak.

### 19. [MAJOR] Bot Telegram là "confused deputy"
- **Bằng chứng:** A L414 và L471 cho `xt-bot` gọi REST bằng service account rồi khẳng định "user X đã bấm". Lộ bot token hoặc secret của `xt-bot` là duyệt được thay bất kỳ ai.
- **Phương án:**
  - `callback_data` mang nonce do xT ký, gắn với `(confirmation_id, chat_id, actor)`, dùng một lần.
  - xT tự kiểm chữ ký và `chat_id` đã liên kết. Service account của bot chỉ gọi được đúng endpoint `confirm.telegram`.
  - Vượt ngưỡng thì luôn chuyển sang `/confirm` (A L140 đã có).
  - Telegram webhook có `secret_token` header.

### 20. [MAJOR] Làn PII trên ChatGPT/Claude không giải được Điều 20
- **Bằng chứng:**
  - Mọi kết quả tool, kể cả `_meta` chỉ dành cho widget, đều đi qua backend của host (CR L22).
  - Đ.20 là chuyện **chuyển dữ liệu ra nền tảng nước ngoài** (`red-team.md` L51), không phải chuyện "model có đọc hay không".
  - Ý tưởng iframe tự fetch bằng `connectDomains` cần token người dùng trong iframe (`red-team.md` L62). Token đó cũng phải đi qua host.
  - A L489 đã thừa nhận một phần, nhưng A L486 vẫn để mode `full` cho lớp `cloud`.
- **Phương án:**
  - Lớp client `cloud`: `pii ≥ basic` luôn `masked`, **không có `full`**.
  - Giá trị thật chỉ xem được qua nút "Mở trong xT Desk" (phiên người, tự host trong nước).
  - Bỏ câu "người xem thấy tên thật" khỏi mọi slide nói về host đóng.
  - Input từ chối giá trị đã che hoặc token hóa (critique F9 L201).
  - Màn demo Q4 giữ nguyên: Claude thấy dữ liệu che, Desk + LLM local thấy số thật, audit ghi `egress`.

### 21. [MAJOR] PostgREST có thể đi vòng làn PII và RLS
- **Bằng chứng:**
  - A L302 cho rằng view "đã lọc field PII theo policy". Nhưng `llm_egress` phụ thuộc lớp client (A L486), view tĩnh không biết client là ai.
  - A L444 map JWT bất kỳ sang `app.tenant_id`. Token của connector (DCR) có đi được `/data/v1` hay không thì A chưa nói.
  - View Postgres mặc định chạy bằng quyền **owner**. Nếu owner là superuser hoặc role BYPASSRLS (role migrate hay bị đặt như vậy) thì RLS bị bỏ qua hoàn toàn.
- **Phương án:**
  - PostgREST `jwt-aud = xt-data`, chỉ client `xt-desk` và client credentials của nhà tích hợp có mapper tới `aud` này. Token MCP không bao giờ có.
  - View `api.*` đặt `security_invoker=true`, owner là `xt_owner` (không superuser, không BYPASSRLS).
  - Field `pii=sensitive` không có trong `api.*`.
  - Test RLS chạy **qua HTTP PostgREST** chứ không chỉ qua `xt_app`.

---

## D. Tính phổ quát: thiết kế có thật sự "mọi doanh nghiệp" không?

Đánh giá: **chưa**. Catalog trung tính, nhưng mô hình dữ liệu và process chỉ đủ cho "đơn từ + duyệt tuần tự". Thử bán hàng (W5), kho, thu–chi nhiều dòng là gãy. HR không phải điểm nghẽn. Điểm nghẽn là **khuôn office-approval**.

### 22. [MAJOR] Người duyệt chỉ là role tĩnh, không có cơ cấu tổ chức
- **Bằng chứng:**
  - Lane = role (`spike-spiffworkflow.md` L32). "Lane chỉ là nhãn" (L59). Compiler chỉ kiểm `role ∈ roles` (A L564).
  - Ở doanh nghiệp VN, quy tắc phổ biến nhất là "**trưởng phòng của người đề nghị**", "giám đốc khối", "kế toán phụ trách chi nhánh". A không có primitive OrgUnit/Position.
  - Spike LLM cho thấy câu hỏi quan hệ tổ chức là chỗ model nhỏ sai (`spike-local-llm.md` L10).
- **Phương án:**
  - Primitive `org_unit` (cây) + `position` (giữ bởi actor, có hiệu lực theo thời gian). Nguồn sự thật nằm ở xT, Keycloak chỉ lo xác thực.
  - Process-spec có `assignee` dạng resolver whitelist: `role(x)`, `manager_of(requester, levels=1)`, `head_of(org_unit_field)`, `field(record.x)`, `any_of([...])`.
  - Worker resolve lúc tạo bước và lưu vào `approval_steps`, để audit biết vì sao người này được chọn.

### 23. [MAJOR] Process thiếu "trả lại bổ sung", duyệt song song và any-of
- **Bằng chứng:**
  - Decision có `return` (critique L68, TC). Nhưng compiler chỉ sinh reject → `end_rejected`, chưa có vòng lặp hay song song (`spike-spiffworkflow.md` L157).
  - Gần như mọi luồng duyệt VN đều có "trả lại để bổ sung" và "các phòng cùng ký".
  - Spiff persist được cả song song lẫn vòng lặp (`verification-spikes.md` L110–121), nên chỉ thiếu ở compiler.
- **Phương án:**
  - Compiler v0.2 thêm: `on_return: requester` (vòng về bước draft, giữ dữ liệu), `parallel: {mode: all|any, steps:[...]}`, `trigger: manual|timer(rrule)|event(type)` (critique F13).
  - Test reload cho mỗi dạng mới.

### 24. [MAJOR] Model YAML không biểu diễn được nghiệp vụ ngoài đơn từ
- **Bằng chứng:**
  - Ví dụ A L545–558 chỉ có field vô hướng.
  - Không có:
    - dòng chi tiết (đơn hàng, phiếu chi nhiều khoản, phiếu nhập kho);
    - field tính toán (tổng = Σ dòng);
    - số chứng từ theo mẫu (`PC-2026-0001` theo tenant/năm, đề nghị nào cũng cần);
    - quyền theo field (lương chỉ HR xem: RLS là theo dòng, PII không phải ACL);
    - state machine (F2).
  - Quan hệ không có FK (mục 1).
- **Phương án** (thêm vào ModelDefinition v0.2, mỗi mục tách thành một test gói):
  - `relation` có FK thật (bảng thật);
  - `children: {model, min, max}` (bảng con có FK tới cha, ghi cùng transaction);
  - `computed` (biểu thức whitelist dùng chung `expr.py`, server tính, cấm agent ghi);
  - `sequence: {pattern: "PC-{yyyy}-{seq:4}", scope: tenant|year}`;
  - `access.read/write: [roles]` theo field;
  - `states/transitions`.
  - **Tiêu chí phổ quát:** gói W5 CRM và một gói "Kho đơn giản" viết được thuần YAML, không cần code mới, chạy trong CI.

### 25. [MAJOR] Action registry đóng nên mọi tác dụng phụ mới đều cần code
- **Bằng chứng:**
  - `call_service` tra allowlist Python (`spike-spiffworkflow.md` L102). A L220 liệt kê `notify · ledger.post · iam.deprovision`.
  - Doanh nghiệp muốn "duyệt xong thì tạo phiếu chi", "đóng ticket thì cập nhật tài sản", "gọi API hóa đơn điện tử". Mỗi việc như vậy hiện là một PR Python, trái với "nghiệp vụ là dữ liệu".
- **Phương án:**
  - Action dữ liệu **tổng quát**: `record.create|update|transition` trên model bất kỳ (ánh xạ tham số bằng `expr`, chạy với quyền của process, qua cùng Poka-yoke), `notify`, `webhook` (domain allowlist theo tenant, secret trong OpenBao, khai là L3 lúc activate, idempotency key).
  - Đây cũng là câu trả lời có thật cho ô "serverless" (DE L88): tự động hóa ngầm mà không chạy code tự do. Tốt hơn câu "cố ý không có" ở A L117.

### 26. [MINOR] Tool chung chung khiến model nhỏ hiểu sai quan hệ
- **Bằng chứng:**
  - Khi chỉ có `find_employee`, model sai 3/3. Đổi tên field cũng vẫn sai. Có tool đúng ý định thì đúng 3/3 (`spike-local-llm.md` L58–61).
  - `search_records(model, filters)` của xT chính là dạng tool chung chung.
- **Phương án:**
  - Model YAML có `views:` là truy vấn đặt tên, có tham số. Ví dụ `direct_reports(manager)`, `my_open_advances()`, `overdue_tasks(org_unit)`. Mỗi view có câu hỏi mẫu bằng tiếng Việt.
  - Gọi qua `search_records(view=…)`, hiện trong `describe`.
  - Agent đề xuất view qua `propose_change`. Catalog không đổi mà model nhỏ vẫn có "tool đúng ý định".

---

## E. Độ khớp với đề thi

### 27. [MAJOR] Bốn dịch vụ Tầng 1 chưa "thấy được" như dịch vụ riêng
- **Bằng chứng:** mọi thứ đi qua `POST /rest/v1/tools/{name}` (A L395). Giám khảo kiến trúc (`red-team.md` Q7 L82–92) sẽ thấy một monolith RPC, không thấy dịch vụ Data và dịch vụ Workflow.
- **Phương án (rẻ, chỉ là route và tag sinh từ cùng contract):**
  - APISIX tách namespace:
    - `/auth` → Keycloak;
    - `/data/v1` → PostgREST, cộng `/files/v1` presign và quét;
    - `/workflow/v1` → definitions (GET BPMN XML có DI), instances, tasks, complete;
    - `/rest/v1/tools`, `/mcp`.
  - OpenAPI chia tag theo 4 dịch vụ.
  - Mỗi dịch vụ có `curl` demo riêng trong `docs/headless-demo.sh`. Sơ đồ slide vẽ đúng 4 khối này.

### 28. [MINOR] Những chỗ giám khảo dễ thấy "giả"
| Chỗ | Rủi ro | Sửa |
|---|---|---|
| "HSM" với tệp khóa Ed25519 (A L106) | Nói quá | Dùng SoftHSM2 qua PKCS#11 (BSD-2, `red-team.md` L232) **hoặc** chỉ nói "ký số nội bộ + Transit", không dùng chữ HSM |
| NGSI-LD "qua định dạng của `get_record`" (A L159) | Mơ hồ | Một endpoint `GET /data/v1/ngsi-ld/entities/{id}` cho model Thiết bị, hoặc bỏ khỏi slide |
| "Serverless: cố ý không có" (A L117) | Bỏ trống một ô | Mục 25 (`webhook` + `record.*`) |
| Lakehouse là vài tệp Parquet | Chấp nhận được | Dùng DuckDB `ATTACH postgres` + `COPY TO 's3://lake/…'` (extension có sẵn), không tự viết exporter |
| BPMN không mở được | Mục 7 | — |
| [D] "Trung tâm điều hành/BI" (DE L113) chỉ là một MCP App | Giám khảo kỳ vọng dashboard | Bản tin MCP App là chính. Nếu chọn bảng thật (mục 1), Metabase OSS (AGPL-3.0, OSI) có thể là tùy chọn ở profile `full`, mỗi tenant một connection gắn `app.tenant_id`. Không đưa vào `minimal` |

### 29. [MINOR] Rủi ro điều kiện thi, không thuộc kiến trúc nhưng chặn giải
- Đội phải có tối đa 3 sinh viên và 1 giảng viên (`olp-checklist.md` L12). Mô hình "2 người: chủ dự án + agent" không phải đội thi.
- Repo chưa có commit. I.1 trừ 5đ nếu không thấy sử dụng thực tế (`olp-checklist.md` L23).
- A L719 đã nêu (Q5). Đề nghị nâng thành việc làm **hôm nay**: tạo repo công khai, commit `docs/` + `research/` + LICENSE.

---

## F. "Không xây từ đầu": chỗ đang tự viết lại thứ đã có

### 30. [MAJOR] Danh sách thay thế
| A tự viết | Dự án OSI nên dùng hoặc thử trước | Ghi chú |
|---|---|---|
| Hàng đợi `jobs` với lease, backoff, stale, dead, scheduler cron (A L577–588) | `procrastinate` (MIT, Postgres, `SKIP LOCKED`, LISTEN/NOTIFY, retry, periodic task) | **[chưa kiểm chứng]** khả năng enqueue trong cùng transaction psycopg của use case và việc chạy với RLS. Spike nửa ngày, không đạt thì giữ bản tự viết |
| `xt gitops sync` reconcile realm (A L121) | `keycloak-config-cli` (Apache-2.0) | Realm khai bằng YAML trong git, chạy idempotent khi `make up` |
| Exporter Parquet (A L169) | DuckDB extension `postgres` + `httpfs` | Một câu SQL |
| Layout BPMN | `bpmn-auto-layout` hoặc layout tuyến tính | Mục 7 |
| Lịch làm việc/SLA, ngày lễ VN | `holidays` (MIT), `python-dateutil` rrule | Không tự viết lịch lễ |
| Kênh người qua CLI | Keycloak Device Authorization Grant | Mục 17 |
| Renderer MCP Apps trong Desk | gói `@modelcontextprotocol/ext-apps` (phần host/AppBridge) | **[chưa kiểm chứng]** API host của gói. Spike chỉ dùng `basic-host` để test (`spike-mcp-apps.md` L251) |
| IAM evaluator | `mini` của spike-iam + casbin làm oracle | Mục 8 |
| Mẫu bảng động | Đọc mã Baserow (lõi MIT, BB L60) | Mục 1 |

**Tự viết là đúng** khi không có thứ OSI tương đương: audit hash-chain (immudb là BSL, BB L14), compiler spec → BPMN, model engine (BB L66 "không tìm thấy agent-native ERP OSI"), lớp dispatcher IAM/autonomy.

---

## Lịch đề xuất (thay A §10)

| Mốc | Ngày | Phạm vi cốt lõi | Nghiệm thu |
|---|---|---|---|
| **v0.0** | 18/10 | Repo công khai + CI PoF (`reuse lint`, `make dist`). Spike model engine 1 ngày (mục 1). Spike `procrastinate` nửa ngày | Quyết định D6 có số đo. Ghi `spike-iam.md` |
| **v0.1** walking skeleton | 27/10 | Postgres RLS + 2 tenant. Model engine additive cho 1 gói. W1 tuần tự + SLA trên Spiff. **Keycloak + Streamable HTTP + một connector Claude qua tunnel.** 10 tool (đọc, `create/update_record`, `submit/decide_request`, `get_inbox`, `propose_change` model). Audit hash-chain. Device grant cho `xt decide`. Tarball v0.1.0 | Demo (1)(2)(3)(5)(6)(7) của A L704, chạy trên Claude thật |
| **v0.2** | 09/11 | APISIX (mục 6), PostgREST (mục 21), SeaweedFS + `upload_file` + ClamAV (quét đơn giản), Telegram (mục 19), W2 Task/SLA, `on_return`/`parallel`/`manager_of` (mục 22–23), `aggregate_records`, ChatGPT, sửa VS B, instance công khai | A L705 + `curl` cho từng dịch vụ (mục 27) |
| **v0.3** | 21/11 | Desk (đăng nhập, host 4 app, `/confirm` có xác thực tươi, **chat LLM local**), W3 + W4, `children`/`sequence`/`computed`, eval v1 (local + Claude + GPT) | A L706 + Desk là kênh demo chính |
| **v1.0** | đóng băng 28/11, tag 03/12 | Mỗi mục ≤ 1 ngày: bản tin + lineage, DuckDB lake, PII masked + egress audit, Ed25519 cho văn bản + outbox, NATS/MQTT IT/OT tối thiểu, backup/restore, **W7 Helpdesk + W5 CRM chỉ bằng YAML** | 6 màn demo + video. Gói mới không có code mới |

**Thứ tự cắt khi trễ:** IT/OT → UI lineage → OpenBao → NATS → ký W6 → Metabase. **Không bao giờ cắt:** 4 dịch vụ, Desk + `/confirm`, PoF, test RLS/SoD.

---

## Đề xuất sửa (diff theo mục của `architecture-v1.md`)

```diff
§0 bảng D1–D10
- D1 | 18 tool cố định ... (TC §6)
+ D1 | 16 tool cố định (critique E đã gộp/hoãn); thêm upload_file, aggregate_records, update_record.transition; describe gộp; get_process_status gộp vào get_record; propose_change(target)
- D4 | SpiffWorkflow 3.2.0 nhúng trong xt-worker tách tiến trình
+ D4 | SpiffWorkflow nhúng ở adapters/workflow, dùng in-process trong xt-api (bước người, đồng bộ) và trong xt-worker (timer/service/outbox); cùng image
- D6 | Model engine tự viết: record JSONB ... (BB §C)
+ D6 | [chờ spike 18/10] Ưu tiên bảng thật/model (cột f_<id>, ADD COLUMN additive, rename = metadata, đổi kiểu = L3 + backfill), view api.* security_invoker; fallback JSONB nếu spike vượt +3 ngày công
- D8 | Xác nhận L2/L3 chỉ qua kênh con người ... CLI TTY ở chế độ stdio
+ D8 | Xác nhận cần xác thực tươi (max_age=0/prompt=login + TOTP/WebAuthn) hoặc Telegram có nonce ký; CLI dùng Device Authorization Grant; confirm_url chỉ là locator
- D9 | ... scope mcp-xt là optional + kiểm azp
+ D9 | ... thử optional ở ngày 1 v0.2; fallback default scope + denylist azp nội bộ cố định; PostgREST aud riêng xt-data; 1 user = 1 Organization ở v1.0

§1.3
- tools/list luôn đúng 18 tool của TC §6: ...
+ tools/list 16 tool (danh sách mục 2 của critique); hồ sơ core-8 cho LLM local do Desk áp phía client
- Câu hỏi mở Q1 (18 hay 15)
+ Đóng Q1: 16

§1.4 Workflow theo phiên bản
+ v1.0: W7 Helpdesk và W5 CRM phải là gói YAML thuần, có test CI "không thêm mã Python"

§2.1
- API Gateway ... `curl` không token → 401 ở gateway
+ API Gateway: /mcp đi thẳng, 401 + resource_metadata do xt-api trả; JWT ở APISIX cho /rest, /data, /workflow
+ Thêm cột "Endpoint riêng": /auth, /data/v1 + /files/v1, /workflow/v1 (OpenAPI tag theo dịch vụ)
- Workflow ... Xuất BPMN XML
+ Workflow ... Xuất BPMN XML có BPMNDI (mở được bằng Camunda Modeler)

§2.2
- Két sắt: OpenBao cấp credential DB
+ Két sắt: OpenBao KV + Transit (khóa ký); credential động sau v1.0
- Zero-trust biên: ... WAF Coraza + CRS
+ Zero-trust biên: ... plugin APISIX có sẵn; Coraza ra roadmap
- Serverless script: Cố ý không có
+ Tự động hóa ngầm: action dữ liệu record.* + webhook allowlist (không chạy code tự do)
- Streaming + hàng đợi chống chối bỏ: Outbox → NATS có ack
+ ... Outbox ký Ed25519 + event_hash vào hash-chain (chống chối bỏ thật); NATS chỉ vận chuyển
- Chữ ký số/PKI/HSM: ... tệp khóa (minimal)
+ ... không dùng chữ "HSM" trừ khi dùng SoftHSM2/PKCS#11
- GitOps: xt gitops sync tự viết
+ GitOps: keycloak-config-cli + APISIX standalone reload + restart/healthcheck

§2.3
- Kênh demo chính: Desk + LLM local và Goose + LLM local
+ Kênh demo chính: Desk + qwen3.5:4b (reasoning_effort none, core-8). Goose CLI = minh họa client thứ ba. ChatGPT/Claude = "cũng chạy được"
- MCP Apps 5 app ở v0.3 ... v1.0 thêm (6)(7)
+ 4 app ở v0.3 (inbox/thẻ duyệt, form, kanban+SLA, diff); v1.0 + bản tin; orgchart từ spike

§2.4 lớp 6
- Metabase bỏ: ... giấy phép [chưa kiểm chứng]
+ Metabase OSS (AGPL-3.0, OSI): tùy chọn profile full nếu D6 = bảng thật; không ở minimal

§3.3(c) PostgREST
+ jwt-aud = xt-data; JWKS tĩnh, reload khi xoay khóa; jwt-role-claim-key; view security_invoker, owner không BYPASSRLS; field sensitive không có trong api.*; test RLS qua HTTP

§4.2
- import-linter (giấy phép [chưa kiểm chứng])
+ import-linter (kiểm giấy phép khi scaffold) hoặc test AST

§5.1
- Chế độ stdio: Tệp danh tính cục bộ ... gắn actor
+ Chế độ stdio: actor kind=agent, owner_human_ref; không bao giờ là danh tính người

§5.2
- Parser strict: từ chối ... `condition`
+ Dùng evaluator mini + policy_language của spike-iam (condition đóng: eq/ne/le/ge/in), casbin làm oracle trong CI

§5.4
- L2 ... submit_request ...
+ L1: submit/withdraw đơn của chính người được đại diện; tự chuyển trạng thái việc của mình
+ Hard gate: decide_request, ký, lệnh chi, activate định nghĩa
- L3 phá hủy cần 2 người
+ 2 người chỉ cho destructive model change và erase PII

§5.5
- CLI `xt decide` trên TTY (stdio) | Tiến trình riêng, người gõ
+ CLI `xt decide` | Device Authorization Grant (Keycloak) hoặc TOTP ở dev mode
- Trang /confirm/{id} | Phiên OIDC ..., step-up MFA khi vượt ngưỡng
+ Trang /confirm/{id} | xác thực tươi mỗi lần (max_age=0 + TOTP; L3: prompt=login + acr); lưu auth_time/acr; từ chối nếu > 120 s
- Nút Telegram | Callback ... chat_id đã liên kết
+ Nút Telegram | callback_data mang nonce do xT ký (confirmation_id, chat_id, actor), dùng 1 lần; webhook secret_token
+ Bất biến SoD: trigger decider_human ≠ requester_human; delegation và xt-desk-agent không bao giờ có decide/ký/activate
+ Chống consent phishing: /confirm chỉ hiện plan do server tính; text do agent soạn gắn nhãn; giới hạn số confirmation pending mỗi agent

§5.7
- giá trị `full | masked | local_only` cho lớp cloud
+ lớp cloud: pii ≥ basic luôn masked; giá trị thật chỉ ở Desk/local; input từ chối giá trị đã che
- "Người xem thấy tên thật, LLM chỉ thấy token": ... trong host ngoài ... [chưa kiểm chứng]
+ Không hứa làn PII trong host đóng (dữ liệu vẫn đi qua backend nhà cung cấp, Đ.20)

§6.1
- Quyết định tạm (D6): bảng chung records(... values jsonb ...)
+ Theo kết quả spike 18/10 (mục 1). Bổ sung ModelDefinition: relation (FK), children, computed, sequence, access theo field, states/transitions, views (truy vấn đặt tên)
- Bị loại: Bảng vật lý theo model ... (BB rủi ro #4)
+ (bỏ trích dẫn sai; BB §C chọn DDL)

§6.2
+ Process-spec: assignee resolver (role, manager_of, head_of, field, any_of); on_return; parallel all|any; trigger manual|timer|event
+ Action registry: record.create|update|transition, notify, webhook (allowlist, secret OpenBao, L3 lúc activate)
+ Primitive org_unit + position (nguồn sự thật ở xT)
- Hàng đợi jobs (tự viết)
+ Thử procrastinate trước (spike nửa ngày); không đạt thì giữ thiết kế hiện tại

§7
- 9 skill, 11 prompt theo mốc
+ v0.1: xt-core, xt-approvals; 3 prompt. Còn lại thêm theo eval

§8.1
- full: ... prometheus ...
+ full: bỏ prometheus container; Desk chuyển sang minimal (là bề mặt xác nhận bắt buộc)

§10
- (bảng mốc hiện tại)
+ (bảng "Lịch đề xuất" của critique: v0.0 18/10, v0.1 27/10 có OAuth + Claude thật, đóng băng 28/11)
- Đường găng: model engine ... OAuth với host thật
+ Đường găng: (1) spike D6 18/10, (2) Claude/ChatGPT thật ở v0.1/v0.2 đầu, (3) Desk /confirm v0.3

§11
- Q1 (18 hay 15)
+ Đã đóng: 16
- Q2 Desk chat v1.0?
+ Đã đóng: có, v0.3, là kênh demo chính
+ Q mới: chọn D6 sau spike; đội thi 3 sinh viên + GVHD; repo công khai hôm nay
```
