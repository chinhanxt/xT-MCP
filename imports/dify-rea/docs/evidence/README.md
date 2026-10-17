# Bằng chứng REA và kiểm thử

`summary.json` là chỉ mục có thể chia sẻ: snapshot nguồn, kết quả kiểm tra và giới hạn. Không chứa credential. Các JSON đầy đủ và log giữ cục bộ, gitignore.

| File | Ý nghĩa |
|---|---|
| `rea-original.json` | Quan sát tham chiếu: Dify Cloud chuyển sang đăng nhập; chưa kiểm tra bên trong |
| `rea-session.json` | Bundle MCP REA của đợt trước; không đại diện snapshot hiện tại |
| `rea-*.json` | Capture trình duyệt theo scenario qua REA CLI; mỗi file có Evidence ID và trạng thái cleanup |
| `live-smoke.json` | Qwen/BGE thật: RAG/chat/streaming, workflow, calculator, Agent MCP và typed workflow trên workspace tạm |
| `tests.log`, `build.log`, `live-smoke.log` | Log lần chạy gần nhất; summary ghi hash để nhận diện |
| `*.png` | Ảnh chụp từ capture trình duyệt local; chỉ chứa dữ liệu QA |

MCP REA đã được dùng trực tiếp ở đợt đầu. Nếu phiên Codex không expose tool REA, tiếp tục bằng `rea capture-browser-scenario` CLI đã cài và ghi rõ nguồn bằng chứng. CLI hoạt động không chứng minh MCP server đã kết nối lại. Kết quả self-test/browser local chỉ chứng minh bản dựng này hoạt động trong phạm vi đã thử, không chứng minh tương đương Dify.

API provider trả tiền và Dify đã đăng nhập chưa được kiểm thử. MCP trong live-smoke là server mẫu cục bộ, không phải hệ thống doanh nghiệp thật.
