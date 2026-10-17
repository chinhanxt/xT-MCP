# xT Studio

Dự án độc lập tái dựng các nhóm chức năng của Dify từ tài liệu công khai và bằng chứng trình duyệt do REA thu thập. Mã ứng dụng được viết mới; không fork mã Dify. **Chưa tương đương toàn bộ Dify.**

## Chạy

Cần Node.js 24 trở lên. Để đọc PDF cần lệnh `pdftotext` (Poppler), để đọc DOCX cần Python 3; TXT/Markdown/CSV không cần hai công cụ này.

```bash
npm ci
npm run build
npm start
```

Mở **http://127.0.0.1:4317**. Lần đầu tạo workspace, email và mật khẩu của bạn. Không có tài khoản hoặc mật khẩu mặc định.

1. **Model AI → Thêm model**: chọn Ollama, OpenAI-compatible hoặc Anthropic native. Thử kết nối sau khi lưu.
2. **Kiến thức**: tạo kho toàn văn hoặc embedding; tải TXT/MD/CSV/PDF/DOCX, kiểm tra văn bản trích xuất rồi lập chỉ mục. Mở tài liệu để xem chunk, sửa và lập chỉ mục lại.
3. **Ứng dụng**: tạo Chatbot, Workflow hoặc Agent, chọn model và kho kiến thức. Chat hiển thị dần câu trả lời, Markdown, trích nguồn; có dừng và sửa/chạy lại tin nhắn.
4. **Workflow**: thêm/nối/kéo/xóa node; cấu hình trường đầu vào, nhánh, template, HTTP và retry/fallback. Chạy thử và xem log từng bước.
5. **Kết nối MCP**: thêm URL Streamable HTTP, token nếu cần. Trong Agent chọn từng tool được phép sử dụng; lưu và chạy thử để xem tool trace.
6. **Lưu → Publish → API & tích hợp**: tạo API key hoặc link trang ứng dụng. Lịch sử phiên bản cho phép khôi phục một bản cũ vào bản nháp; publish lại tạo phiên bản mới.
7. **Xuất cấu hình/Nhập cấu hình**: chuyển app bằng JSON riêng của xT Studio, chọn lại model, kiến thức và MCP connector tại workspace đích. File không mang theo credential hoặc tài liệu.

Ví dụ Ollama có sẵn trên máy phát triển:

| Mục | Provider | Base URL | Tên model | Loại |
|---|---|---|---|---|
| Chat | Ollama | `http://127.0.0.1:11434` | `qwen3.5:4b` | Chat |
| Embedding | Ollama | `http://127.0.0.1:11434` | `bge-m3:latest` | Embedding |

OpenAI-compatible dùng URL có tiền tố API, ví dụ `https://api.openai.com/v1`. Anthropic native dùng `https://api.anthropic.com/v1` và tên model do tài khoản của bạn hỗ trợ. Ollama là tùy chọn. Credential model/MCP được mã hóa tại server; API cấu hình không trả lại credential đã lưu.

## Đã có

| Nhóm | Chức năng chạy được |
|---|---|
| Chat | Streaming, Markdown/GFM, dừng, sửa/chạy lại, tham số sinh, JSON mode, giới hạn context, hội thoại và trích nguồn lưu SQLite |
| Workflow | Canvas; Input, LLM, Retrieval, IF/ELSE, Template, Aggregate, HTTP, Output; typed input, kiểm tra graph/biến/nhánh, retry/fallback, trace |
| RAG | Trích TXT/MD/CSV/PDF/DOCX, preview và sửa nội dung, chunk overlap, FTS5 hoặc embedding/cosine, trích nguồn, bật/tắt/xóa và reindex nguyên tử |
| Agent | Tool tìm kiến thức/tính toán và MCP; allowlist riêng từng app, kiểm tra schema, giới hạn bước, trace tool |
| Model | OpenAI-compatible/Ollama/Anthropic native; chat/embedding theo provider; thêm/sửa/xóa/thử kết nối; chặn thay đổi làm hỏng kho embedding |
| Publish | Snapshot và lịch sử phiên bản, khôi phục vào nháp, export/import, API key theo app, thu hồi key, link app chia sẻ và thu hồi link |
| Workspace | Khởi tạo owner, đăng nhập/đăng xuất, session hết hạn, dữ liệu giới hạn theo workspace; tách hội thoại Studio/API key/người dùng link chia sẻ |

Chat mới có text delta; Workflow/Agent trả kết quả khi hoàn tất, kể cả khi dùng kênh streaming. JSON mode kiểm tra đầu ra là JSON hợp lệ, chưa phải JSON Schema structured output.

## API ứng dụng

Tạo key tại **API & tích hợp** của app đã publish, rồi dùng:

```bash
curl http://127.0.0.1:4317/v1/apps/APP_ID/run \
  -H 'Authorization: Bearer YOUR_API_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"input":"Xin chào","stream":true}'
```

`stream:true` trả NDJSON: `delta` (chat), rồi `result` hoặc `error`. Không gửi `stream` để nhận JSON một lần. Kết quả gồm `output`, `citations`, `trace`, `run_id` và `conversation_id` cho hội thoại. Gửi lại `conversation_id` để tiếp tục bằng cùng API key. Workflow dùng thêm `inputs`, ví dụ `{"input":"","inputs":{"amount":1000000,"urgent":false}}` theo định nghĩa đã publish.

Key chỉ truy cập app tương ứng và luôn chạy bản publish. API là định dạng riêng của xT Studio, chưa tương thích API/DSL Dify. Một API key là một miền hội thoại; backend tích hợp nhiều người dùng phải giữ `conversation_id` ở đúng người dùng, không chuyển ID giữa họ.

## MCP

Chỉ owner đã đăng nhập cấu hình endpoint/token. Hỗ trợ Streamable HTTP với JSON hoặc SSE response; hỗ trợ endpoint cục bộ để nối server doanh nghiệp trên cùng máy. Chưa hỗ trợ stdio, transport HTTP+SSE legacy, OAuth discovery/login hoặc marketplace.

- Mỗi app chọn tối đa 16 tool; công cụ không được chọn sẽ không gửi cho model và không được thực thi.
- Schema được ghim khi chọn tool, kiểm tra lại khi gọi. Nếu schema đổi, vào MCP cập nhật danh sách, chọn lại tool trong app và publish lại.
- Connector có thể tắt để dừng các lượt gọi tiếp theo. Xóa bị chặn khi còn app tham chiếu. Các phiên bản cũ chỉ khôi phục được nếu dependency vẫn còn và khớp schema.
- Mỗi kết nối tối đa 30 giây, mỗi request tool 15 giây; giới hạn 100 tool/5 trang, tham số 32.000 ký tự, kết quả 64.000 ký tự. Không tự retry tool MCP. Dừng phía client không hoàn tác tác động mà server công cụ đã thực hiện.
- Người có link chia sẻ hoặc API key có thể dùng các tool đã publish bằng credential của workspace. Chỉ chọn các tool phù hợp với người sẽ nhận link/key.

## Kiểm thử

```bash
npm test
npm run build
npm run test:live
```

Test tự động dùng provider/MCP fixture cục bộ, gồm success/failure/cancel, schema drift, cách ly và migration. `test:live` cần Ollama cùng hai model ở bảng trên; không tự tải model. Nó dùng workspace và MCP server mẫu trong `/tmp`, chạy chat/RAG/streaming, workflow, Agent calculator và Agent gọi MCP bằng Qwen thật. Không thay dữ liệu workspace của bạn.

Kết quả tại `docs/evidence/live-smoke.json`. `LIVE_KEEP_OPEN=1` giữ server QA cổng 4318 và MCP fixture để kiểm tra trình duyệt; `LIVE_PORT` đổi cổng QA. Các fixture là dữ liệu mô phỏng, không kết nối hệ thống công ty thật. Xem [chỉ mục bằng chứng](docs/evidence/README.md) và [kế hoạch](docs/reconstruction-plan.md).

## Dữ liệu và giới hạn

- `.data/studio.sqlite` chứa cấu hình, tài liệu, hội thoại, phiên bản và lịch sử chạy. `.data/credential.key` giải mã credential model/MCP. Sao lưu cả database lẫn key bằng SQLite backup hoặc dừng server trước khi sao chép. Thiếu key của database cũ thì server dừng, không tạo key thay thế.
- Migration giữ hội thoại trước đây trong miền `studio`. Các hội thoại API tạo trước khi có phân tách audience cần bắt đầu lại bằng API; không tự gán lịch sử cũ cho một API key.
- Reindex tạo chunk/vector mới trước, chỉ thay nội dung cũ khi thành công. Nếu lỗi hoặc server restart, tài liệu đã có chunk vẫn truy xuất được; xem lỗi và thử lại. Extraction/indexing hiện chạy trong request, chưa có job queue bền vững.
- Server mặc định loopback. `PORT` đổi cổng, `STUDIO_DATA_DIR` chọn thư mục dữ liệu. Log phát triển ghi method/path/status/thời gian; `LOG_LEVEL=silent` tắt log request.
- Một owner/workspace tạo qua UI. Chưa có onboarding nhiều doanh nghiệp, mời thành viên, vai trò, SSO, quota hoặc kiểm thử tải production.
- File tối đa 5 MB, văn bản tối đa 1 triệu ký tự; PDF scan chưa có OCR. Chưa có crawler/connector đồng bộ, hybrid/rerank hoặc vector index cho tập lớn.
- Workflow DAG tối đa 50 node, chưa có loop/iteration, code sandbox, chạy riêng từng node, lịch chạy và worker. HTTP node chỉ GET/POST, URL có origin cố định, không redirect; chưa có UI cấu hình header xác thực.
- Chat chưa có ảnh/file đa phương thức hoặc widget iframe. Model discovery, chi phí/token usage, marketplace chưa có. Chưa nối MCP xT hiện có.
- Kiểm thử provider thật mới có Ollama; OpenAI và Anthropic được kiểm tra với fixture giao thức. Chưa có tài khoản Dify đăng nhập để chứng minh tương đương hành vi.

## Giấy phép

Mã mới dùng Apache-2.0. Đây không phải bản phân phối của Dify. Dify là sản phẩm tham khảo, có [giấy phép riêng](https://github.com/langgenius/dify/blob/main/LICENSE).
