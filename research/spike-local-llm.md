# Spike: LLM local + client nguồn mở điều khiển tool xT (đường demo "chủ quyền số")

Ngày chạy: 2026-10-15, thư mục làm việc `/tmp/xt-spike-local`. Script + kết quả: `research/spike-local-llm/` (`scripts` ở gốc thư mục, `results/`, `server/`). Server spike chạy cổng 18220; đã dừng, đã `ollama stop` (giữ lại model).

Máy: RTX 3050 Laptop 6GB, RAM 15GB, 12 core, ollama 0.30.10.

## Kết luận ngắn

- **Chạy được trọn vòng local, không cần Internet cho LLM**: `qwen3.5:4b` (Q4, 3.3GB) + Goose CLI (Apache-2.0) hoặc vòng lặp agent Python tự viết đều gọi đúng tool MCP của xT bằng tiếng Việt.
- **Model 4B yếu về ngữ nghĩa quan hệ**, không yếu về chọn tool: "Ai báo cáo cho Minh?" bị trả lời ngược (Minh báo cáo cho Lan) 3/3 lần khi chỉ có tool `find_employee`. Thêm tool đúng ý định `list_direct_reports(manager_name)` thì 3/3 đúng. Bài học thiết kế tool: tool nên trả đúng thứ câu hỏi cần, không bắt model suy luận chiều quan hệ.
- **Render MCP Apps với Goose: chưa kiểm chứng được trên máy này** (xem mục 4). Phải có host có UI; CLI không render.
- **Cần xT Desk** (host mỏng) cho demo giao diện: Goose CLI không có UI; Goose Desktop hỗ trợ MCP Apps nhưng ghi "Experimental" và không chạy được ở đây.

## 1. Model

`ollama list` ban đầu: bge-m3, deepseek-v4-flash:cloud, nomic-embed-text, gemma2:2b (không có tool calling tốt). Tra thư viện ollama.com (2026-10): có qwen3.5 (0.8b/2b/4b/9b/27b...), qwen3.6 (chỉ 23GB+), gemma4 (6.6GB+), qwen3 (4b/8b). Cả qwen3.5 và gemma4 gắn nhãn `tools`. Chọn **`qwen3.5:4b`** (3.3GB): vừa 6GB VRAM dư chỗ cho context; bản 9B là 6.6GB, không vừa GPU. Chỉ pull một model.

```
ollama pull qwen3.5:4b        # 1m13s
scripts/bench.sh              # 3 lần, think=false, num_ctx 8192
```
Kết quả thật:
```
load 10.2s (lần đầu) | gen 216 tok @ 27.0 t/s
load 0.3s  | prompt 31 tok @ 443 t/s | gen 207 tok @ 29.1 t/s
load 0.3s  | prompt 31 tok @ 472 t/s | gen 225 tok @ 28.2 t/s
ollama ps: 100% GPU, VRAM dùng 3084 MiB (ctx 8192)
```
**~28 tok/s, 100% GPU.** Tiếng Việt sinh ra trôi chảy.

**Bẫy quan trọng (đã đo):** trên endpoint OpenAI-compatible của ollama (`/v1/chat/completions`), tham số `"think": false` **bị bỏ qua**: một câu "2+2?" tốn 3293 token suy luận. Phải dùng `"reasoning_effort": "none"` (-> 2 token). Endpoint native `/api/chat` thì `think:false` hoạt động. xT Desk phải gửi `reasoning_effort`.

## 2. Client và giấy phép

| Client | Giấy phép (đã đọc LICENSE) | Kết quả |
|---|---|---|
| Goose CLI v1.54.0 | Apache-2.0. Repo đã chuyển `block/goose` -> `aaif-goose/goose` (Agentic AI Foundation) | Chạy tốt, mục 3b |
| Goose Desktop 1.54.0 (.deb, giải nén bằng `dpkg -x`, không sudo) | Apache-2.0 | Electron thoát ngay: `GPU process isn't usable` (mục 4) |
| LibreChat | MIT (đã đọc LICENSE) | **Bỏ qua**: cần MongoDB (+ thường Meilisearch/RAG API) qua docker, ổ `/` chỉ còn 6.7GB, RAM 15GB đang dùng ~6GB; hỗ trợ MCP Apps chưa kiểm chứng (red-team.md Q2). Không đáng cho spike |
| Vòng lặp agent Python tự viết (`scripts/agent.py`, ~60 dòng, `mcp` 2.3.0 + `openai` SDK trỏ vào ollama) | Apache-2.0 (code của xT) | Chạy tốt, mục 3a |

Cài Goose: script chính thức `download_cli.sh` hỗ trợ `GOOSE_BIN_DIR` và `CONFIGURE=false`, không đụng hệ thống:
```
GOOSE_BIN_DIR=/tmp/xt-spike-local/goose-bin CONFIGURE=false bash download_cli.sh   # goose 1.54.0, 307MB
```
Chạy cô lập bằng `XDG_*` trỏ vào `goose-home/` (`scripts/goose_run.sh`): `goose run --no-session --max-turns 6 --with-streamable-http-extension "http://127.0.0.1:18220/mcp" -t "<câu hỏi>"` với `GOOSE_PROVIDER=ollama GOOSE_MODEL=qwen3.5:4b`.

## 3. Test tác vụ tiếng Việt

Server: copy từ `research/spike-mcp-apps/server.py`, đổi cổng 18220, CORS `*`, thêm tool không-UI model thấy được (đăng ký bằng `@mcp.tool` vì `apps.tool` bắt buộc `resource_uri`): `list_employees`, `find_employee`, `list_direct_reports`, `get_task`, `decide_task`. Các tool UI-only (`visibility:["app"]`) **vẫn nằm trong `tools/list`**, host phải tự lọc (agent.py lọc; Goose thì không thấy model gọi chúng).

Kỳ vọng: Q1 -> trả "Tuan". Q2 -> `decide_task(T-1, approve)` (hoặc `get_task` rồi approve).

### 3a. Agent tự viết (qwen3.5:4b, mỗi tác vụ 3 lần; `results/run-4b-*.txt`)

| Cấu hình | Q1 "Ai báo cáo cho Minh?" | Q2 "Duyệt task T-1" |
|---|---|---|
| no-think, tool `find_employee` (field `manager`, `direct_reports`) | tool đúng 3/3, **đáp án SAI 3/3** ("Minh báo cáo cho Lan"), 2.1-3.4s | đúng 3/3, 2.8-3.9s |
| think (`reasoning_effort=medium`) | tool đúng, đáp án đúng 1/3, lúng túng 2/3, 8.9-14.4s | đúng 3/3, 5.4-14.3s |
| đổi tên field rõ nghĩa (`reports_to`, `direct_reports_who_report_to_this_person`), no-think | **sai 3/3**; think: sai 2/3 | - |
| thêm tool `list_direct_reports`, no-think | **đúng 3/3** ("Tuan"), 2.2-4.1s | đúng 3/3, 2.5-3.8s |

Mô hình chọn tool và điền tham số đúng ở mọi lần (`{"name":"Minh"}`, `{"task_id":"T-1","decision":"approve"}`); lỗi chỉ nằm ở diễn giải chiều quan hệ. Latency end-to-end ~2-4s khi no-think (1 lượt gọi tool + 1 lượt trả lời). Thinking làm chậm 3-5 lần mà không cứu được.

### 3b. Goose CLI (6 lượt; `results/run-goose.txt`, log server `results/server-calls.log`)

| Lượt | Tool Goose gọi | Thời gian |
|---|---|---|
| Q1 #1 | `list_direct_reports(Minh)` -> Tuan (đúng) | 13.5s |
| Q1 #2 | `find_employee(Minh)` (tool ít phù hợp; lượt này không in câu trả lời cuối) | 19.2s |
| Q1 #3 | `list_direct_reports(Minh)` -> Tuan (đúng) | 9.8s |
| Q2 #1 | `decide_task(T-1, approve)` (không in câu cuối) | 9.2s |
| Q2 #2 | `decide_task(T-1, approve)` -> "đã được duyệt" | 10.6s |
| Q2 #3 | `get_task(T-1)` (task đã approved từ lượt trước nên không gọi decide) | 12.5s |

Tool chọn hợp lý 6/6, tham số đúng. Goose chậm hơn agent tự viết ~4x (10-19s vs 2-4s): system prompt + schema của Goose lớn hơn nhiều, và model thinking mặc định. Có 2/6 lượt kết thúc không có câu trả lời văn bản cuối (có thể là phần lọc output của tôi hoặc model trả rỗng; chưa phân biệt). Lưu ý "approve" chạy thẳng không hỏi người dùng: đúng với nguyên tắc xT thì tool ghi L2/L3 phải trả `approval_required`; spike không có chính sách đó.

## 4. MCP Apps trong Goose

- Blog Goose 2026-01-06 "goose Lands MCP Apps": hỗ trợ bản draft SEP-1865, render HTML trong iframe sandbox, mục "**This is Experimental**" (đã fetch). Hiển thị chỉ có ở Goose Desktop (Electron), CLI không render.
- Đã tải `goose_1.54.0_amd64.deb` (159MB), giải nén bằng `dpkg -x` vào `/tmp/xt-spike-local/goose-desktop`, chạy với config cô lập (extension `streamable_http` trỏ cổng 18220). Kết quả: `FATAL: GPU process isn't usable. Goodbye.` (môi trường chạy tool không cấp GPU/GL cho Electron). Máy không có Xvfb nên không có display cô lập; display `:1` là màn hình thật của người dùng đang mở nhiều ứng dụng riêng tư, nên **tôi không thử lại bằng `--disable-gpu` trên đó** (và đã xóa một ảnh chụp toàn màn hình lỡ chụp khi thử). **Trạng thái: chưa kiểm chứng.** Việc cần làm sau: cài `xvfb` hoặc chạy Goose Desktop trực tiếp trên màn hình, thử `show_approval_card`/`show_org_chart`.
- Việc cần chú ý khi thử: Goose Desktop dùng model local Q4 4B; tool app `ui://` phải do model gọi `show_*` đúng, còn `act_on_task` là tool app-only.

## 5. Kết luận cho demo live trên phần cứng này

1. **Model khuyến nghị: `qwen3.5:4b`, no-think (`reasoning_effort:"none"`)**, ctx 8192: ~28 tok/s, 3GB VRAM, 2-4s mỗi câu lệnh một tool. Giữ trống VRAM để có thể mở thêm embedding (bge-m3). Chưa thử qwen3:8b / gemma4 (gemma4 nhỏ nhất 6.6GB, không vừa GPU 6GB; qwen3:8b Q4 5.2GB vừa khít nhưng chậm hơn, chưa đo).
2. **Thiết kế tool cho model nhỏ:** tool theo ý định (một tool = một câu hỏi nghiệp vụ), kết quả phẳng, tên field không mơ hồ; 15 tool cố định là hợp lý, cần mô tả bằng ngôn ngữ người dùng ("Ai báo cáo cho X?"). Cần bộ eval tiếng Việt và số liệu pass thật (xem red-team.md Q3): với 4B, tỷ lệ phụ thuộc mạnh vào thiết kế tool.
3. **Đường demo "rút dây mạng" khả thi:** Goose CLI + ollama + xT MCP chạy offline hoàn toàn. Nhưng thao tác ghi quan trọng (duyệt) cần cơ chế `approval_required` + người bấm, không để model 4B tự duyệt.
4. **Có cần xT Desk (host tự viết) không: CÓ**, cho phần giao diện. Lý do: (a) Goose CLI không có UI; (b) MCP Apps trên Goose Desktop ghi Experimental và chưa chạy được ở đây; (c) vòng lặp agent chỉ ~60 dòng, đã chứng minh chạy với ollama (cần lọc tool `visibility:["app"]`, gửi `reasoning_effort:"none"`); phần render dùng `basic-host` của ext-apps (đã chạy được ở spike trước) + Keycloak. Goose giữ vai trò "client nguồn mở thứ ba" để minh họa tính chuẩn MCP ("1 nghiệp vụ, 3 client").
5. Rủi ro còn lại: Goose chậm 10-19s/lượt (nên demo bằng xT Desk, Goose làm minh họa phụ), LibreChat chưa thử, MCP Apps trong Goose chưa kiểm chứng, chưa thử phần cứng với tool > 9 và ngữ cảnh dài.
