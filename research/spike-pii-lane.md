# Spike: Làn PII (LLM thấy token, người thấy giá trị thật qua kênh của xT)

Ngày chạy: 2026-10-15. Chạy thật trong `/tmp/xt-spike-pii`; code + bằng chứng chép về `research/spike-pii-lane/`. Cổng e2e: 18210 (xT), 18211 (basic-host), 18212 (sandbox). Đã dừng hết tiến trình.
Ngữ cảnh: `red-team.md` Q4, `docs/archive/architecture-v1.md` §5.7, `spike-mcp-apps.md`, `connector-requirements.md`, `spike-model-engine.md`.

## 0. Kết luận nhanh

1. Về pháp lý, gửi kết quả tool chứa DLCN của nhân viên tới ChatGPT/Claude là **chuyển DLCN xuyên biên giới** (Luật 91/2025 Đ.20 k.1 đ.c; NĐ 356/2025 Đ.17 k.1 đ.c). Token hóa có đảo ngược được là bút danh hóa, **dữ liệu vẫn là DLCN** (Luật 91 Đ.12 k.1; NĐ 356 Đ.9 k.3 đ.c). Vì vậy làn PII **giảm phạm vi và rủi ro, nhưng không xóa nghĩa vụ** lập hồ sơ đánh giá tác động chuyển DLCN ra nước ngoài (ĐGTĐ). Nghĩa vụ này không có miễn trừ cho doanh nghiệp nhỏ. Muốn thoát nghĩa vụ phải khử nhận dạng thật (chỉ trả số liệu tổng hợp), và chưa có quy chuẩn kỹ thuật cho việc này.
2. Prototype chạy được: **50/50 test pass** (21 test chính + 29 ca biên do agent kiểm thử viết; vòng đầu các ca biên tìm ra 4 chỗ rò thật và 2 rủi ro, đã sửa hết, xem 4.4). E2E Playwright trên host tham chiếu `basic-host` cũng pass. Client tier cloud chỉ nhận token. Lỗi validate và lỗi unique của engine vốn echo giá trị thật, nhưng đã bị thay bằng token trước khi ra dây. Agent truyền token vào `create_record`/`update_record` thì DB nhận giá trị thật. Audit ghi quyết định egress, không chứa giá trị thật.
3. MCP Apps: spec **không** đảm bảo kết quả của tool `visibility:["app"]` nằm ngoài context của model. Ngoài ra, tool call do app khởi tạo vẫn đi qua backend của host (Claude proxy qua IP Anthropic). Do đó **không được đặt giá trị thật vào bất kỳ phản hồi MCP nào** cho tier cloud, kể cả tool app-only. Kênh cho người: iframe xin "vé" (không phải PII) rồi `fetch` **thẳng** tới xT qua `connectDomains`, kèm chữ ký bằng khóa non-extractable của iframe. Đã chạy thật trên basic-host: 0 giá trị thật trong postMessage và trong traffic host↔MCP; giá trị thật chỉ có trong response `/pii/reveal`.

## 1. Căn cứ pháp lý

Agent nghiên cứu đã đọc toàn văn (Luật 91 qua bản sao lawplayer.com ghi nguồn vbpl.vn; NĐ 356 qua luatnguyen.vn; Luật 134 qua file .docx của luatvietnam.vn). thuvienphapluat.vn trả 403 với truy cập tự động. Các điều khoản dưới đây đã đối chiếu với toàn văn ở bản sao, **chưa đối chiếu Công báo**. Mục nào chỉ đọc qua nguồn thứ cấp thì có ghi [chưa kiểm chứng].

| Nội dung | Điều khoản | Hệ quả cho xT |
|---|---|---|
| Hiệu lực | Luật 91/2025/QH15 ban hành 26/6/2025, hiệu lực 01/01/2026 (Đ.38 k.1). NĐ 356/2025/NĐ-CP ban hành 31/12/2025, hiệu lực 01/01/2026. NĐ 13/2023 hết hiệu lực (NĐ 356 Đ.42 k.2) | Áp dụng ngay cho bản OLP |
| DLCN cơ bản / nhạy cảm | Luật 91 Đ.2 k.2-3: theo danh mục Chính phủ. NĐ 356 Đ.3: địa chỉ (k.4); **"Số điện thoại, số định danh cá nhân, số hộ chiếu"** (k.7) là **cơ bản**. NĐ 356 Đ.4 k.1: sức khỏe (đ.d), **hình ảnh** thẻ căn cước (đ.i), thông tin đăng nhập/thẻ/lịch sử giao dịch ngân hàng (đ.k), đời sống riêng tư, bí mật cá nhân (đ.c) là **nhạy cảm** | Số CCCD = `basic`, **ảnh** CCCD = `sensitive`, sức khỏe = `sensitive`. Lương và số tài khoản không được nêu đích danh. xT mặc định xếp `sensitive` (bí mật cá nhân, đ.c) **[chưa kiểm chứng, cần giải thích của cơ quan có thẩm quyền]** |
| Mã hóa / khử nhận dạng | Luật 91 Đ.2 k.1: "Dữ liệu cá nhân sau khi khử nhận dạng không còn là dữ liệu cá nhân". Đ.2 k.11 định nghĩa khử nhận dạng. Đ.12 k.1: "dữ liệu cá nhân sau khi được mã hóa vẫn là dữ liệu cá nhân". Đ.14 k.6 đ.b: cấm tái nhận dạng. NĐ 356 Đ.9 k.3 đ.c: "ẩn danh" (tách dữ liệu định danh, lưu riêng) là **biện pháp bảo vệ** | Token ⟨NV-0042.luong⟩ + vault có thể đảo ngược = bút danh hóa: **vẫn là DLCN** |
| Chuyển xuyên biên giới | Luật 91 Đ.20 k.1 đ.c: "sử dụng nền tảng ở ngoài lãnh thổ … để xử lý dữ liệu cá nhân được thu thập tại Việt Nam". NĐ 356 Đ.17 k.1 đ.c: "chuyển đến nền tảng ở ngoài lãnh thổ … để tiếp tục xử lý" | Gọi ChatGPT/Claude kèm DLCN là chuyển xuyên biên giới (mức chắc chắn cao theo agent) |
| Hồ sơ ĐGTĐ chuyển | Luật 91 Đ.20 k.2-3: lập hồ sơ, gửi bản chính trong 60 ngày kể từ lần chuyển đầu, lập 1 lần và cập nhật theo Đ.22. NĐ 356 Đ.18: báo cáo theo Mẫu 09 (có sơ đồ luồng dữ liệu và đánh giá bên nhận), kèm Mẫu 01a/01b; cơ quan trả kết quả trong 15 ngày | Tool/job sinh bản nháp Mẫu 09 lấy số liệu từ `egress_audit` (mục 3.6) |
| Cập nhật | Luật 91 Đ.22 k.1: định kỳ 6 tháng. NĐ 356 Đ.20: trường hợp đặc biệt cập nhật trong 10 ngày (Mẫu 03a/03b) | Báo cáo audit 6 tháng |
| Miễn trừ | Luật 91 Đ.20 k.6 đ.b: "lưu trữ dữ liệu cá nhân của người lao động … trên dịch vụ điện toán đám mây". NĐ 356 Đ.17 k.3 đ.d: quản lý nhân sự xuyên biên giới theo quy chế lao động | Cả hai nói về **lưu trữ** hoặc nội bộ tập đoàn; áp vào LLM là gượng **[chưa kiểm chứng, cần luật sư]** |
| ĐGTĐ xử lý | Luật 91 Đ.21; NĐ 356 Đ.19 (Mẫu 10) | |
| DN nhỏ | Luật 91 Đ.38 k.2-3: DN nhỏ/khởi nghiệp được miễn Đ.21, Đ.22, Đ.33 k.2 trong 5 năm, **trừ khi** xử lý DLCN nhạy cảm hoặc số lượng lớn chủ thể (NĐ 356 Đ.41: ≥ 100.000). **Không miễn Đ.20** | Khách SME của xT vẫn phải làm hồ sơ chuyển nếu dùng LLM nước ngoài |
| AI | Luật 91 Đ.30 k.4: xử lý DLCN bằng AI phải phân loại theo mức rủi ro; k.1: chỉ xử lý đúng mục đích, trong phạm vi cần thiết. NĐ 356 Đ.10 k.2: kết quả suy luận của AI giúp xác định người thì vẫn là DLCN | Nhãn `pii` + policy `llm_egress` chính là bước phân loại; tối thiểu hóa dữ liệu theo tier |
| Người lao động | Luật 91 Đ.25 k.2 đ.c: xóa/hủy khi chấm dứt hợp đồng | Offboarding phải xóa cả vault token (mục 3.4) |
| Xử phạt | Luật 91 Đ.8 k.4: vi phạm chuyển xuyên biên giới phạt tới **5% doanh thu** năm trước; k.5: vi phạm khác tới 3 tỷ đồng. NĐ 330/2026/NĐ-CP (hiệu lực 19/8/2026) Đ.56: không lập hồ sơ phạt 30–50 triệu; 1–5% doanh thu nếu để lộ dữ liệu **[nguồn thứ cấp: baochinhphu.vn, luatvietnam.vn; chưa đọc văn bản gốc]** | Che PII làm giảm thiệt hại khi lộ (số chủ thể, loại dữ liệu bị ảnh hưởng) |
| Luật AI 134/2025/QH15 | Hiệu lực 01/3/2026; Đ.7 k.3 cấm xử lý dữ liệu vận hành AI trái luật BVDLCN | Không có cơ chế riêng; vẫn dùng Luật 91 |

Nguồn:
- Luật 91 toàn văn: https://lawplayer.com/vn/act/vn-vbpl-179252 (TVPL: https://thuvienphapluat.vn/van-ban/Bo-may-hanh-chinh/Luat-Bao-ve-du-lieu-ca-nhan-2025-so-91-2025-QH15-625628.aspx)
- NĐ 356: https://luatnguyen.vn/van-ban-phap-luat/nghi-dinh-356-2025-nd-cp-huong-dan-luat-bao-ve-du-lieu-ca-nhan-2574.html (TVPL: https://thuvienphapluat.vn/van-ban/Quyen-dan-su/Nghi-dinh-356-2025-ND-CP-huong-dan-Luat-Bao-ve-du-lieu-ca-nhan-687428.aspx)
- NĐ 330/2026 (thứ cấp): https://baochinhphu.vn/muc-xu-phat-vi-pham-hanh-chinh-doi-voi-hanh-vi-vi-pham-ve-bao-ve-du-lieu-ca-nhan-102260822154959691.htm
- Luật 134: https://static3.luatvietnam.vn/uploaded/others/2025/12/25/luat-134-2025-qh15_2512152052.docx

Chưa kiểm chứng: (a) tên đơn vị chuyên trách (NĐ 356 Đ.39 k.1 chỉ ghi "đơn vị trực thuộc Bộ Công an", không ghi tên A05); (b) quy chuẩn kỹ thuật khử nhận dạng (NĐ 356 Đ.34 k.4 giao Bộ Công an) chưa thấy ban hành; (c) chưa thấy văn bản nào sửa NĐ 356 tính đến 10/2026.

**Sửa lại `red-team.md` Q4:** điều khoản đúng là Đ.20 **k.1 đ.c** (không phải "20.1(iii)"). Số CCCD là DLCN **cơ bản**, không phải nhạy cảm. Câu "che PII thì hết rủi ro pháp lý" là sai. Câu nên nói khi pitch: *"xT tối thiểu hóa dữ liệu ra LLM nước ngoài (Đ.30 k.1), giữ dữ liệu nhạy cảm ở lại Việt Nam, và sinh sẵn số liệu cho hồ sơ Mẫu 09"*.

## 2. Mô hình đe dọa: những đường dữ liệu ra LLM

| Kênh ra | Có vào context model / backend nhà cung cấp? | Xử lý trong làn |
|---|---|---|
| `content` của tool result | Có | Chiếu + quét |
| `structuredContent` | Có. Spec MCP Apps chỉ *khuyến nghị* "not added to model context" (draft:1720-1722, không chuẩn tắc). OpenAI: "Surfaced to the model and the component". Claude Code: đưa vào `tool_result` (claude.com/docs/connectors/building/mcp-apps/quickstart.md) | Chiếu **giống hệt** `content` |
| `_meta` của result | OpenAI: "Delivered only to the component. Hidden from the model." Spec MCP Apps: "not intended for model context" (không bắt buộc). Claude: chưa rõ | Chỉ đặt vé (không phải PII), vé gắn khóa iframe |
| Lỗi (`isError`, thông điệp validate, lỗi DB, exception) | Có | Quét sâu bằng giá trị đã biết + bộ nhận dạng |
| Tool `visibility:["app"]` | Spec chỉ cấm liệt kê tool cho model (draft:419-420; 2026-01-26:400-401), **không** nói kết quả được ẩn. Call vẫn đi qua backend host (Claude: proxy qua `160.79.104.0/21`, connector-requirements [A7]) | Coi như kênh cloud |
| `ui/update-model-context`, `ui/message` | Có, khi View chủ động gửi (draft:1228-1237) | UI của xT không bao giờ gửi giá trị thật qua hai kênh này |
| Resource, prompt, completion, `notifications/message`, progress, elicitation | Có | **Chưa bọc trong spike**. v1 phải đi qua cùng `PiiLane.project/scrub` |
| Iframe `fetch` thẳng xT (`connectDomains`) | Không qua backend host; về thẳng trình duyệt người dùng | Kênh cho người (mục 3.7) |

Spec không có trường nào cho server biết `tools/call` đến từ UI hay từ model (agent kiểm chứng: grep spec draft không thấy). Vì vậy server không được tin "call này do UI gọi nên trả raw được".

## 3. Thiết kế

### 3.1. Phân loại field trong model spec
`FieldSpec.pii: none|basic|sensitive` cộng với `pii_kind` (name, phone, national_id, address, salary, health, bank, email, dob). Kind chỉ dùng cho gợi ý che và cho bộ nhận dạng, không ảnh hưởng quyết định egress. Mặc định bám NĐ 356: `so_cccd`, `so_dien_thoai`, `dia_chi`, `ho_ten`, `email` là `basic`; `suc_khoe` là `sensitive`; `luong` và `so_tai_khoan` xT xếp `sensitive` (thận trọng). Tenant có thể nâng mức, không được hạ dưới mức của gói chuẩn (kiểm ở `propose_model_change`, chưa làm trong spike). Model YAML của agent đề xuất phải có `pii` cho mọi field kiểu text/number. Thiếu nhãn thì engine gợi ý từ tên/nhãn field, và người duyệt xác nhận khi duyệt proposal.

### 3.2. Server biết đầu bên kia là LLM nào bằng cách nào?
**Không biết chắc.** Server chỉ có `azp` (client_id) trong JWT đã ký. Tier là **khẳng định của admin về client**, lưu phía server:

| Client | Cách đăng ký | Tier |
|---|---|---|
| ChatGPT, Claude.ai, Claude Code, Goose, Cursor… tự đăng ký | DCR (client_id ngẫu nhiên) | **cloud** (không có trong registry: fail closed) |
| `xt-desk-agent`: agent của Desk gọi LLM nội bộ (vLLM/Ollama trong LAN) | Confidential client cấp sẵn (client credentials), APISIX giới hạn IP nội bộ/mTLS | **local** |
| `xt-desk`: phiên người trên Desk, không có LLM | OIDC + MFA | **human** |
| Goose/LM Studio của nhân viên chạy model local | Admin cấp client riêng (không DCR), gắn `xt.trust_tier=local` | **local**. Rủi ro còn lại: người dùng đổi provider của Goose sang cloud. Server không phát hiện được, chỉ ràng buộc bằng quy chế nội bộ |

- Claim do client tự khai (vd `xt_tier`) bị bỏ qua (test `test_tier_from_azp_fail_closed`).
- Production: registry = thuộc tính client Keycloak `xt.trust_tier`, đọc qua Admin API và cache. Client DCR không có thuộc tính này vì policy DCR ẩn danh không cho tự thêm mapper/thuộc tính.

### 3.3. Policy `llm_egress` theo tenant và theo agent

| mode \ pii | none | basic | sensitive |
|---|---|---|---|
| `full` | raw | raw | raw |
| `masked` | raw | token + gợi ý (`Nguyễn V*** A`, `********2345`, `***, Hà Nội`) | token |
| `local_only` | raw | token | **bỏ** (`_omitted`, không có token, không tham chiếu được) |

- Mặc định: `cloud: masked`, `local: full`, `human: full`. Tier không có trong policy thì dùng `local_only`.
- Override theo agent (`azp`) **chỉ được chặt hơn**. Override "full" cho client cloud bị bỏ qua (test `test_agent_override_only_tightens`).
- `hints` tắt được theo tenant, vì gợi ý cũng là mảnh DLCN.
- Quyền xem giá trị thật của người: `reveal_roles` (`basic`: mọi người dùng trong tenant; `sensitive`: `hr`, `giam_doc`). Production thay bằng IAM policy của xT.

### 3.4. Token
- Dạng `⟨MÃ_NGHIỆP_VỤ.field⟩`, ví dụ `⟨NV-0042.so_cccd⟩`. Agent đọc hiểu được ("CCCD của NV-0042"), tham chiếu được, truyền lại được. PII lọt vào text tự do mà bộ nhận dạng bắt được thì thành `⟨PII-n.kind⟩`; cùng giá trị cho ra cùng số trong một phiên.
- **Phạm vi phiên = `tenant|azp|sub|sid`.** `sid` của Keycloak ổn định qua refresh token. Token của phiên khác, hoặc của client khác dù cùng `sid`, đều bị từ chối `PII_TOKEN_UNKNOWN`. Token tất định trong phiên. Vault lưu snapshot giá trị: spike dùng sqlite; production dùng Postgres + RLS, cột value mã hóa bằng khóa tenant, TTL theo phiên, xóa khi logout hoặc offboarding (Đ.25).
- **Detokenize ở chiều vào** theo schema của tool (`ToolShape`: `data_args`, `filter_args`, `sort_args`). Token đứng nguyên một giá trị thì giữ kiểu gốc (lương ra int); token nằm giữa chuỗi thì thay bằng giá trị thật.
- **Chống rửa (downgrade):** không cho ghi token mức X vào field mức < X. Ví dụ `⟨NV-0042.luong⟩` không được vào `ghi_chu` (none) hay `so_dien_thoai` (basic). Nếu để lọt, đọc lại `ghi_chu` sẽ ra raw cho cloud. Arg ngoài schema được coi là mức `none`.
- **Chống oracle:** với mode ≠ full, field `sensitive` chỉ được lọc bằng `eq` với token. Bị chặn: `luong > X`, `luong = 25000000`, `contains` trên `suc_khoe`, và sắp xếp theo field sensitive. Nếu cho phép, agent dò ra lương bằng tìm kiếm nhị phân. Field `basic` chỉ cho các op trong `TenantPolicy.basic_filter_ops`, mặc định `{eq}`. Agent kiểm thử đã chứng minh `so_cccd contains <tiền tố+d>` dựng lại đủ 12 số CCCD chỉ bằng cách đếm số kết quả.
- **Gốc token không được là PII:** nếu mã nghiệp vụ trông như SĐT/CCCD/email, có ≥ 8 chữ số, hoặc sai cú pháp token, thì gốc token đổi thành `R-<sha256(model|key)[:10]>`. Nếu không, `⟨0912345678.ho_ten⟩` tự làm lộ giá trị (lỗi agent kiểm thử tìm ra).
- Validate kiểu của SDK MCP chạy **trước** làn PII, nên tool khai `data: dict` để validate thật xảy ra **sau** detokenize, ở trong engine. Khi đó lỗi echo raw và làn phải chặn được. Lỗi ở tầng SDK chỉ echo đúng input agent gửi (token hoặc giá trị chính agent gõ), không có raw mới.

### 3.5. Lọc chiều ra: chiếu + 2 lưới
1. **Chiếu theo field**: mọi kết quả dạng `{model, record|records}` được chiếu theo (mode, pii).
2. **Lưới 1, giá trị đã biết**: tập giá trị thật trong lời gọi (từ detokenize, từ mọi hàng engine đọc kể cả hàng không trả về, và mọi giá trị đã cấp token trong phiên). Mỗi giá trị sinh các biến thể: NFC, không dấu phân cách, `25,000,000`, `25.000.000`, `25 000 000`, `25tr`, `25 triệu`, `18,5 triệu`. Quét không phân biệt hoa/thường. Biến thể chữ dùng biên từ; biến thể số **không** dùng biên `\w`, chỉ chặn chữ số liền trước, để bắt cả `25.000.000đ`, `25000000VND` và hai số dính nhau. Phạm vi quét: **mọi chuỗi** của envelope, gồm thông điệp lỗi, key dict và số nguyên trùng giá trị.
3. **Lưới 2, bộ nhận dạng VN**: CCCD 12 số (mã tỉnh 001-096), di động VN (+84/0, cho phép dấu cách/chấm/gạch), email. Kết quả bắt được thành `⟨PII-n.kind⟩` và vẫn tham chiếu được.
4. Lỗi bất ngờ (`Exception`) cũng đi qua làn, không trả stacktrace thô.

Kiểm chứng bằng đột biến: tắt `_deep_scrub` thì **22/50 test fail** (rò CCCD/SĐT/email trong ghi chú, lỗi validate echo địa chỉ, lỗi unique echo CCCD, exception echo SĐT, lương trong ghi chú…). Bỏ riêng lưới "hàng engine đã đọc" thì không test nào fail, vì lưới này là lớp phòng thủ thừa; vault phiên và detokenize đã phủ.

### 3.6. Audit egress
Bảng `egress_audit` (hash-chain `prev_hash → entry_hash`) có các cột: `tenant, azp, sub, tier, mode, tool, outcome, egress_json` (đếm `model.field:hành_động`), `detok_json` (token đã mở), `scrubbed` (số lần lưới 1 thay), `hits_json` (bộ nhận dạng theo kind). **Không chứa giá trị thật**: test quét toàn bộ bảng bằng `assert_no_raw`. Từ bảng này sinh được: số lần/loại DLCN đã ra từng nhà cung cấp, cho Mẫu 09 và cho cập nhật 6 tháng (Đ.22). Lần người xem giá trị thật ghi `tier=human, tool=pii_reveal, *:reveal_human`. Production gộp vào `audit_log` chung (cột `egress` đã có trong architecture §5.6).

### 3.7. Kênh cho người: MCP Apps
Luồng (đã chạy thật trên basic-host):
1. `get_record` (tool hiển thị, `_meta.ui.resourceUri = ui://xt/pii-card.html`) trả record đã chiếu. Thẻ hiện token + gợi ý.
2. Người bấm "Xem giá trị thật". Iframe tạo cặp khóa ECDSA P-256 **non-extractable** (WebCrypto), rồi gọi tool app-only `pii_reveal_ticket(tokens, pubkey_jwk)` qua host. Server kiểm mọi token thuộc phiên, rồi cấp vé ngẫu nhiên 256 bit: dùng 1 lần, sống 60 s, gắn `sub/roles` + tập token + public key. Vé **chỉ nằm trong `_meta`**, không có trong `content`/`structuredContent` (test kiểm).
3. Iframe `POST {xT}/pii/reveal` (khai `_meta.ui.csp.connectDomains=[xT]`; basic-host đặt `connect-src 'self' http://localhost:18210`) với `{ticket, ts, sig}`. `sig` là chữ ký P1363 trên `ticket.ts`. Server kiểm: Origin thuộc allowlist sandbox, vé chưa dùng (đánh dấu nguyên tử bằng `UPDATE … RETURNING`), lệch giờ ≤ 30 s, chữ ký khớp khóa iframe, và quyền xem theo mức pii. Response có `Cache-Control: no-store`. Audit `human`.
4. Iframe render giá trị vào DOM; không gửi `ui/message` hay `ui/update-model-context`.

Vé đi qua host, nên có thể lọt vào log của nhà cung cấp hoặc vào context (nếu host đưa `_meta` vào). Kẻ chép được vé vẫn không có private key của iframe nên không đổi được vé (test `test_reveal_rejects_copied_ticket_wrong_origin_and_role`).

Giới hạn, nói đúng mức:
- **Host kiểm soát sandbox.** Về kỹ thuật, host có thể đọc DOM của iframe. Mô hình này chống "dữ liệu vào context/log của LLM", không chống một host độc hại.
- **Origin không phải bằng chứng** với client ngoài trình duyệt. Một agent cloud có công cụ chạy code và có mạng có thể tự gọi `pii_reveal_ticket` (nếu host để lọt tool app-only), tự sinh khóa, giả Origin. Với `sensitive`, tenant nên đặt `reveal_channel=xt_page`: iframe gọi `ui/open-link` mở `https://xt/r/⟨token⟩` trên origin của xT, đăng nhập Keycloak + MFA. Đây là bằng chứng có người thật. Spike chưa code phần này. Kênh này cũng là phương án dự phòng cho host không hỗ trợ MCP Apps hoặc chặn `connectDomains` (spec cho phép host siết thêm: "MAY further restrict", draft:296).
- Claude yêu cầu `_meta.ui.domain` (hash URL server + `.claudemcpcontent.com`). Origin sandbox thật phải đưa vào allowlist. iOS không gửi `Referer`, nên chỉ dựa vào `Origin`. **[chưa test trên Claude/ChatGPT thật: cần tunnel HTTPS + tài khoản]**.

**Desk (LLM local):** client `xt-desk-agent` ở tier `local` nên nhận raw (test `test_local_tier_sees_full`). Giao diện Desk của người là tier `human`, hiển thị đầy đủ. Đây là đường duy nhất được quảng cáo "thấy hết" mà không có điều kiện.

## 4. Prototype

Mã: `research/spike-pii-lane/` (mỗi file có header SPDX Apache-2.0). Phụ thuộc: `mcp` 2.3.0 (MIT), `pydantic` 2.13 (MIT), `cryptography` 41 (Apache-2.0/BSD), `starlette`/`uvicorn` (BSD-3), `pytest`, `playwright` (Apache-2.0, chỉ để test). basic-host của `ext-apps` (MIT) chỉ dùng để test, không đóng gói.

| File | Vai trò |
|---|---|
| `xtpii/spec.py` | `FieldSpec.pii/pii_kind`, model `nhan_vien`, `hop_dong` |
| `xtpii/policy.py` | `Principal`, `ClientRegistry` (azp→tier, fail closed), `TenantPolicy` (mode theo tier, override chỉ chặt hơn, `reveal_roles`), bảng hành động |
| `xtpii/vault.py` | Vault token theo phiên (bind theo bản ghi, free cho text) |
| `xtpii/recognize.py` | Bộ nhận dạng CCCD/SĐT/email VN |
| `xtpii/store.py` | Engine CRUD sqlite **cố ý echo input trong lỗi** (pydantic `input_value=…`, kiểu lỗi Postgres `Key (so_cccd)=(…)`) |
| `xtpii/lane.py` | `PiiLane.call`: detokenize (downgrade, oracle) → gọi → chiếu → 2 lưới → audit |
| `xtpii/reveal.py` | Vé + chữ ký ECDSA P1363 + đổi vé |
| `xtpii/server.py` | `MCPServer` + `Apps`: `get_record` (có UI), `search_records`, `create_record`, `update_record`, `pii_reveal_ticket` (app-only), route `POST /pii/reveal` |
| `xtpii/ui/pii-card.html` | Thẻ MCP App (bridge tự viết, không thư viện) |
| `tests/` | `test_pii_lane.py` (18), `test_reveal.py` (3), `test_edge_cases.py` (16 hàm / 29 ca, do agent kiểm thử viết) |
| `outputs/pytest.txt` | Output `pytest -v -s` thật |
| `e2e/` | `run_server.py`, `e2e.py` (Playwright headless, spy postMessage + mạng), ảnh, log |

Chạy:
```bash
cd research/spike-pii-lane
python3 -m pytest -q tests -o addopts="" -p no:warnings -p no:cacheprovider   # -o addopts: bỏ --cov của pyproject gốc
# e2e (cần clone ext-apps, build basic-host, sửa cổng sandbox 8081 -> 18212 trong src/implementation.ts)
python3 e2e/run_server.py &
(cd /path/ext-apps/examples/basic-host && HOST_PORT=18211 SANDBOX_PORT=18212 SERVERS='["http://localhost:18210/mcp"]' npx tsx serve.ts &)
python3 e2e/e2e.py e2e/out
```

### 4.1. Kết quả thật

```
50 passed in 2.70s
```
(xem `outputs/pytest.txt`)

Client cloud (`azp=5c1e7a0e-dcr-claude`) gọi `get_record NV-0042`:
```json
{"rev": 1, "ma_nv": "NV-0042", "ho_ten": "⟨NV-0042.ho_ten⟩", "phong_ban": "Kỹ thuật", "so_dien_thoai": "⟨NV-0042.so_dien_thoai⟩", "email": "⟨NV-0042.email⟩", "dia_chi": "⟨NV-0042.dia_chi⟩", "so_cccd": "⟨NV-0042.so_cccd⟩", "luong": "⟨NV-0042.luong⟩", "suc_khoe": "⟨NV-0042.suc_khoe⟩", "so_tai_khoan": "⟨NV-0042.so_tai_khoan⟩", "ghi_chu": "Trưởng nhóm", "_hint": {"ho_ten": "Nguyễn V*** A", "so_dien_thoai": "*******678", "email": "a***@congty.vn", "dia_chi": "***, Hà Nội", "so_cccd": "********2345"}}
```
Trường `ghi_chu` (pii=none) chứa PII của người thân: `Người thân: chị Mai ⟨PII-1.phone⟩, CCCD ⟨PII-2.national_id⟩, mail ⟨PII-3.email⟩`. Lưu ý chữ "chị Mai" vẫn lọt: bộ nhận dạng chưa nhận ra tên người trong text tự do.

Agent truyền token vào `create_record hop_dong`:
```
DB hop_dong -> {'cccd_nguoi_ky': '001090012345', 'sdt_lien_he': '0912345678', 'muc_luong': 25000000}
```

Lỗi engine **sau** làn (engine gốc echo nguyên địa chỉ / CCCD; test cũng kiểm chính engine có echo):
```
1 validation error for nhan_vien_payload | luong |   Input should be a valid integer, unable to parse string as an integer [type=int_parsing, input_value='⟨NV-0042.dia_chi⟩', input_type=str] | ...
duplicate key value violates unique constraint "uq_nhan_vien_so_cccd" DETAIL: Key (so_cccd)=(⟨NV-0042.so_cccd⟩) already exists.
```

Audit (4 lời gọi):
```
{'seq': 1, 'azp': '5c1e7a0e-dcr-claude', 'tier': 'cloud', 'mode': 'masked', 'tool': 'get_record', 'outcome': 'ok', 'egress_json': '{"nhan_vien.dia_chi:token_hint": 1, "nhan_vien.email:token_hint": 1, "nhan_vien.ho_ten:token_hint": 1, "nhan_vien.luong:token": 1, "nhan_vien.so_cccd:token_hint": 1, "nhan_vien.so_dien_thoai:token_hint": 1, "nhan_vien.so_tai_khoan:token": 1, "nhan_vien.suc_khoe:token": 1}', 'detok_json': '[]', 'scrubbed': 0, 'hits_json': '{}'}
{'seq': 2, 'azp': '5c1e7a0e-dcr-claude', 'tier': 'cloud', 'mode': 'masked', 'tool': 'update_record', 'outcome': 'VALIDATION_FAILED', 'egress_json': '{}', 'detok_json': '["⟨NV-0042.dia_chi⟩"]', 'scrubbed': 1, 'hits_json': '{}'}
{'seq': 3, 'azp': '5c1e7a0e-dcr-claude', 'tier': 'cloud', 'mode': 'masked', 'tool': 'search_records', 'outcome': 'PII_SORT_DENIED', 'egress_json': '{}', 'detok_json': '[]', 'scrubbed': 0, 'hits_json': '{}'}
{'seq': 4, 'azp': 'xt-desk-agent', 'tier': 'local', 'mode': 'full', 'tool': 'get_record', 'outcome': 'ok', 'egress_json': '{"nhan_vien.dia_chi:raw": 1, ..., "nhan_vien.suc_khoe:raw": 1}', 'detok_json': '[]', 'scrubbed': 0, 'hits_json': '{}'}
```

Kênh cho người (pytest, Starlette TestClient):
```
NGƯỜI thấy (fetch thẳng xT) -> {'values': {'⟨NV-0042.ho_ten⟩': 'Nguyễn Văn An', '⟨NV-0042.so_cccd⟩': '001090012345', '⟨NV-0042.luong⟩': 25000000, '⟨NV-0042.suc_khoe⟩': 'Tiểu đường type 2'}, 'denied': []}
```
Các trường hợp bị từ chối (403): đổi lại vé đã dùng; khóa khác; Origin `https://evil.example`; `ts` lệch 120 s. Người không có role `hr` chỉ nhận `ho_ten`, `luong` nằm trong `denied`.

### 4.2. E2E trên basic-host (Playwright headless, `e2e/out/`)
```
BEFORE (iframe): ho_ten ⟨NV-0042.ho_ten⟩ (Nguyễn V*** A) | so_dien_thoai ⟨NV-0042.so_dien_thoai⟩ (*******678) | ... | luong ⟨NV-0042.luong⟩ | suc_khoe ⟨NV-0042.suc_khoe⟩ | ...
AFTER  (iframe): ho_ten Nguyễn Văn An | so_dien_thoai 0912345678 | ... | luong 25000000 | suc_khoe Tiểu đường type 2 | so_tai_khoan 0011004455667
REVEAL requests: [('POST', 'http://localhost:18210/pii/reveal', ...)]
Leak trong postMessage (host<->iframe): [] (17 msg)
Leak trong traffic host<->MCP server: [] (9 resp)
Giá trị thật trong response /pii/reveal: ['001090012345', '0011004455667', '0912345678', '12 Lý Thường Kiệt, Hoàn Kiếm, Hà Nội', '25000000', 'Nguyễn Văn An', 'Tiểu đường type 2', 'an.nguyen@congty.vn']
```
Ảnh: `e2e/out/1-card-tokens.png`, `e2e/out/2-card-revealed.png`. Toàn bộ postMessage giữa các frame (spy cài vào mọi frame): `e2e/out/e2e-postmessages.jsonl`.

### 4.3. Phát hiện khi làm
1. **SDK `mcp` 2.3: `@apps.tool` khai báo SAU `MCPServer(extensions=[apps])` thì bị bỏ qua, không báo lỗi.** Tool không có trong `tools/list`, gọi thì ra `Unknown tool`. Phải khai báo mọi tool app trước khi tạo server.
2. Trong harness dev, `CORSMiddleware` toàn cục của Starlette chặn preflight của `/pii/reveal` trước khi tới route, vì allowlist của middleware chỉ có origin host. Phải tách: `/pii/*` tự xử lý CORS theo allowlist sandbox. Production để APISIX làm CORS theo từng route.
3. basic-host chuyển `_meta` của kết quả tool call do app khởi tạo vào iframe, nên vé trong `_meta` dùng được. Host thương mại: [chưa kiểm chứng].
4. Agent kiểm thử đối kháng: lần chạy đầu bị bộ lọc an toàn của model cắt giữa chừng, không ra kết quả. Đã giao lại với brief hẹp hơn ("test hồi quy cho middleware che dữ liệu").

### 4.4. Phản biện của agent kiểm thử (Opus) và cách sửa
Vòng 1 (`test_edge_cases.py`): 4 fail, 2 xfail, 44 pass. Các lỗi đều là rò thật trên dây:

| Ca | Wire rò | Sửa |
|---|---|---|
| `⟨NV-0042.so_dien_thoai⟩⟨NV-0042.so_cccd⟩` + thiếu `ma_hd` | `input_value={'cccd_nguoi_ky': '0912345678001090012345'}` (biên `\w` không khớp 2 số dính nhau) | `lane._pattern`: biến thể số không dùng biên `\w` |
| `ma_nv` = `0912345678` | `⟨0912345678.ho_ten⟩` (gốc token là PII; `_outside_tokens` bỏ qua token) | `Vault.root`: gốc băm khi mã trông như PII |
| `ghi_chu` = `Lương 25.000.000đ` / `25000000VND` | nguyên văn | như dòng 1 |
| xfail: `so_cccd contains` dò từng chữ số | dựng lại đủ 12 số | `basic_filter_ops={eq}` |
| xfail: `25tr`, `18,5 triệu` trong ghi chú | nguyên văn | `_variants` thêm dạng triệu |

Sau khi sửa: 50/50 pass, các ca xfail chuyển thành test thường. Các ca đã pass ngay từ đầu: lỗi echo dạng NFD/khác hoa-thường, token sai định dạng, token chưa cấp, sort `luong`/`--luong`/field lạ, key là token, patch `ma_nv` thành token, create field lạ (token hoặc raw), dict lồng sâu ngoài schema, limit âm.

## 5. Rủi ro còn lại và việc tiếp theo

| # | Rủi ro | Xử lý |
|---|---|---|
| 1 | Pháp lý: token hóa vẫn là DLCN, vẫn phải làm hồ sơ Mẫu 09 | Tài liệu cho khách: xT giảm phạm vi và sinh số liệu cho hồ sơ, không thay hồ sơ. Thêm mode `aggregate` (chỉ trả số liệu tổng hợp, k-ẩn danh) cho tenant muốn tránh chuyển DLCN; cần luật sư xác nhận |
| 2 | Tên người, địa chỉ trong text tự do (không có trong DB) không bị bắt | Thêm NER tiếng Việt chạy local (underthesea, GPL-3.0: **không dùng được** trong lõi Apache; cần tìm model OSI-permissive) **[chưa kiểm chứng]**. Trước mắt, đánh dấu field text tự do quan trọng là `basic` |
| 3 | Gợi ý che (`Nguyễn V*** A` + phòng ban) vẫn đủ nhận ra người trong công ty nhỏ | `hints=false` cho tenant nghiêm ngặt |
| 4 | Kênh chưa bọc: resources, prompts, completions, log, progress, elicitation | v1: mọi handler MCP đi qua `PiiLane`. Thêm test quét toàn bộ JSON-RPC ra dây |
| 5 | Agent cloud có công cụ chạy code và có mạng tự đổi vé | `reveal_channel=xt_page` cho `sensitive` (Keycloak + MFA trên origin xT) |
| 6 | Tier `local` dựa vào khẳng định của admin | Ràng buộc mạng (APISIX allowlist IP/mTLS cho client local) + quy chế. Ghi rõ trong README |
| 7 | Vault chứa giá trị thật | Mã hóa theo tenant (OpenBao), TTL theo `sid`, xóa khi logout/offboarding, có RLS |
| 8 | `basic_filter_ops={eq}` làm cloud không tìm được theo tên gần đúng ("Nguyễn…") | Đánh đổi có chủ ý. Tenant có thể mở `contains` cho field `name`, chấp nhận rủi ro dò; hoặc tìm trên Desk/LLM local |
| 9 | Chưa test trên Claude.ai/ChatGPT thật | Tunnel HTTPS + tài khoản. Kiểm: `_meta` có tới iframe không, `connectDomains` có được tôn trọng không, `structuredContent` có vào model không |
