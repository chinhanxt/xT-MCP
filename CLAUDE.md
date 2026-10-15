# xT — Agent-native DX-OS (OLP PMNM 2026)

## Sản phẩm
xT là MCP server đa năng cho mọi doanh nghiệp. Lõi headless (Tầng 1) gồm SSO Keycloak, API Gateway APISIX, Postgres + RLS, object storage, workflow engine. Lõi được lộ ra qua MCP; agent (ChatGPT / Claude / LLM local) chính là giao diện.
- **Bộ tool cố định (~15 tool), nghiệp vụ là dữ liệu.** Model và process do agent đề xuất, con người duyệt rồi mới áp dụng.
- **Workflow lõi mà doanh nghiệp nào cũng có:** đề xuất & phê duyệt, giao việc + SLA, vòng đời nhân sự, thu–chi, bán hàng, văn bản, helpdesk.
- **Giải các pain point chung của doanh nghiệp,** không gắn với một ngành cụ thể.

## Quyết định đã chốt
- **Ngôn ngữ lõi:** Python. **Giấy phép:** Apache-2.0. **Repo:** `/home/chinhan/xT`.
- **Mốc OLP:** nộp bài 17:00 06/12/2026, chung kết 10/12/2026.
- **Tài liệu tham chiếu:**
  - Đề thi: `research/de_thi_OLP2026.txt`
  - Sơ đồ kiến trúc: `/home/chinhan/xT_DXOS_OLP2026.drawio`
  - Kết quả nghiên cứu: `research/*.md`
- **Bám đề thi:**
  - Tầng 1 headless bắt buộc đủ 4 dịch vụ: SSO, API Gateway, Data, Workflow.
  - Tầng 2: ít nhất 1 ứng dụng có giao diện (MCP Apps hiển thị trong chat).
  - Checklist PoF: header giấy phép trong từng file, LICENSE toàn văn, release `.tar.gz` có số phiên bản, README, CHANGELOG, bug tracker, hướng dẫn build.
- **Chỉ dùng phụ thuộc có giấy phép OSI.** Tránh n8n (fair-code), Redpanda (BSL), và mọi thứ SSPL/BSL.

## Quy tắc làm việc (bắt buộc)
- **Luôn có >5 agent chạy song song,** chia việc rõ ràng theo năng lực từng model.
- **Task khó → Opus 5.5. Task nhỏ/vừa → Sonnet 5.5 hoặc Opus 4.6.**
- **Codex CLI** (tận dụng tối đa):
  - `codex exec --skip-git-repo-check -C <dir> -m gpt-6.1-sol -c model_reasoning_effort=high "..."` cho code nặng.
  - `-m gpt-6-astra -c model_reasoning_effort=low` cho việc nhanh.
- **agyxt CLI:** `agyxt -p "..." --model gemini-3.8-flash-high --dangerously-skip-permissions` (tự bật proxy cổng 8899).
  - **Hay bịa**, nên chỉ giao việc tìm repo hữu ích và các task nhỏ nhất, đơn giản nhất.
  - Kết quả luôn phải được kiểm chứng.
- **Khi agyxt hoặc codex không kết nối được** (lỗi auth, mạng, hết quota, treo quá timeout, output rỗng/hỏng): lập tức giao lại đúng task đó cho model Claude tương ứng, không chờ, không dừng tiến trình.
  - Thay codex sol → Opus 5.5.
  - Thay codex astra → Sonnet 5.5.
  - Thay agyxt → Sonnet 5.5 hoặc Haiku 5.5. Kết quả vẫn phải kiểm chứng.
  - Ghi lại sự cố, sau đó thử lại CLI ở task kế tiếp.
- **Cấm chụp màn hình hoặc điều khiển display thật của user** (`:0`, `:1`). Test GUI chỉ được chạy headless hoặc trong Xvfb riêng.
- **Ổ đĩa `/` gần đầy:** trước mỗi lần pull image > 1 GB phải kiểm tra `df -h /`. Xong việc thì xóa image/artifact do chính mình tạo ra.
- **Bắt buộc có 1 agent Opus chuyên kiểm tra + test.** Các agent phải debate/phản biện nhau trước khi chốt.
- **Không xây từ đầu:** thu thập repo nguồn mở (giấy phép OSI) rồi phát triển trên đó.
- **Luôn học từ hai nguồn để làm MCP tốt:**
  - REA: `/home/chinhan/projects/reclaw/repos/rea`
  - sklite: `/home/chinhan/Downloads/sklite-main.zip`, bản giải nén ở `/tmp/sklite_src/sklite-main`. Repo này không có LICENSE nên chỉ học pattern, không copy code.
- **Sản phẩm dùng thật:** không hard-code, không fallback giả. Viết skill cho sản phẩm thay vì cài cứng logic.
- **Test thật kỹ trước khi bàn giao.**
- **Tư tưởng chung:** làm tốt để đạt giải. Sản phẩm phải đa năng.
