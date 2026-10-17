# Phạm vi tái dựng Dify bằng REA

## Mục tiêu và nguồn

Người dùng chọn toàn bộ năm nhóm: chat, workflow, RAG, agent, quản lý model. Đầu ra là dự án riêng ở `projects/reclaw/repos/dify-rea`, tên hiển thị tạm là xT Studio. Repo `rea` và dự án xT hiện tại không được sửa.

Mức bằng chứng:

- **Quan sát thật:** REA mở Dify Cloud và thấy chuyển hướng sang trang đăng nhập. Chưa có tài khoản Dify để kiểm thử bên trong; người dùng chọn tiếp tục từ tài liệu.
- **Đặc tả từ tài liệu:** các chức năng bên trong dựa trên nguồn chính thức bên dưới, không được coi là đã quan sát runtime.
- **Lựa chọn triển khai mới:** TypeScript/React, Node HTTP/SQLite, công cụ agent và API riêng. Các lựa chọn này không phải suy luận về mã nội bộ Dify.

Nguồn:

1. [Dify README](https://github.com/langgenius/dify): các nhóm sản phẩm workflow, RAG, agent và model.
2. [Workflow quick start](https://docs.dify.ai/en/guides/application-orchestrate/creating-an-application): input, biến, LLM, IF/ELSE, output, test run và publish update.
3. [Workflow checklist](https://docs.dify.ai/versions/legacy/en/user-guide/debug-app/chatflow-and-workflow/checklist): kiểm tra node chưa hoàn chỉnh/chưa nối trước publish. Đây là tài liệu legacy, không xác định hành vi phiên bản Cloud hiện hành.
4. [Model configuration](https://docs.dify.ai/zh-hans/guides/model-configuration/readme): provider, credential và model trong workspace.
5. [Knowledge introduction](https://docs.dify.ai/guides/knowledge-base/retrieval): kiến thức và truy xuất phục vụ ứng dụng.

## Phạm vi thay đổi và quyền

- Cho phép: tạo mã, cấu hình, tài liệu, test và server phát triển cục bộ trong dự án mới; đọc tài liệu công khai; REA quan sát URL Dify và runtime cục bộ; gọi Ollama đã có để kiểm thử.
- Không có bước commit/push/deploy/release tự động. Không tải stack Docker Dify do dung lượng `/home` hạn chế tại lúc bắt đầu.
- Chỉ agent chính ghi mã. Các subagent Sol 6 low đọc tài liệu/rà soát độc lập, không ghi chung checkout.
- Đây là quyết định thực thi công việc phát triển được người dùng yêu cầu, không phải receipt phê duyệt phát hành. Bằng chứng kiểm tra được gắn với snapshot mã trong `evidence/summary.json`.

## Đợt 1 — bộ chức năng cơ bản chạy xuyên suốt

Đã triển khai; kết quả thực thi nằm trong evidence:

| Hạng mục | Điều kiện nghiệm thu |
|---|---|
| Khởi tạo và session | Tạo owner; đăng nhập; từ chối mật khẩu sai, request cross-origin và truy cập khi chưa đăng nhập |
| Model | Lưu key mã hóa; không trả key trong API list; thử kết nối; thông báo lỗi HTTP/format |
| Knowledge | Lập chỉ mục; query trả chunk có tên tài liệu; tắt/xóa tài liệu làm mất kết quả tương ứng; lỗi embedding được lưu |
| Chat | Model trả lời từ dữ liệu; citations; tải lại hội thoại; nháp và publish không dùng lẫn lịch sử |
| Workflow | Tạo/nối/cấu hình graph; Input→LLM→Output; hai nhánh điều kiện; trace; từ chối cycle, node rời, biến không có giá trị bảo đảm |
| Agent | Gọi đúng công cụ; kết quả tính toán; giới hạn bước; trace của lỗi |
| Publish | Snapshot giữ nguyên sau sửa nháp; API key chỉ dùng cho app tương ứng; API ép dùng bản publish; thu hồi có hiệu lực |
| Persistence | Mở lại SQLite và đọc lịch sử/key; hai server cạnh tranh cùng hội thoại không ghi đè nhau |

## Đợt 2 — luồng thao tác chính

| Hạng mục | Trạng thái triển khai |
|---|---|
| Streaming chat, Markdown/GFM, dừng, sửa/chạy lại | Đã có; không lưu hội thoại dở khi provider lỗi/hủy |
| Tham số model, JSON mode, context | Đã có; JSON mode chưa phải structured output theo schema |
| Typed input | Đã có text/number/boolean/select, required/default; sửa checkbox false và lọc trường cũ khi chuyển chế độ |
| Export/import | Đã có JSON riêng, rebind model/knowledge/MCP, không mang credential |
| Lịch sử publish/khôi phục | Đã có snapshot bất biến; khôi phục vào nháp, publish tăng phiên bản |
| Aggregate, HTTP, retry/fallback | Đã có; trace số lần chạy/fallback; HTTP GET/POST origin cố định |
| Trang app chia sẻ | Đã có link thu hồi, visitor cookie và tách miền hội thoại; không trả trace nội bộ |
| API streaming | Đã có NDJSON; chat delta, Workflow/Agent chỉ kết quả cuối |
| File/ảnh chat, iframe embed, chạy riêng node, nhánh lỗi riêng | Chưa có |
| Đối chiếu Dify đã đăng nhập | Chưa có tài khoản tham chiếu; chưa thể chứng minh parity |

Nghiệm thu đã triển khai: test mock provider, lỗi/hủy, tách quyền, restore và migration; chạy lại model thật và browser gắn snapshot nguồn. Self-test không được coi là bằng chứng tương đương Dify.

## Đợt 3 — RAG, agent và provider

| Hạng mục | Trạng thái triển khai |
|---|---|
| PDF/DOCX, văn bản preview/chunk/reindex | Đã có; reindex thất bại giữ chunk/nội dung cũ; PDF dùng Poppler, DOCX dùng Python stdlib |
| Anthropic native | Đã có adapter chat/tool/streaming; fixture giao thức đã kiểm tra, chưa test paid API thật |
| MCP remote | Đã có SDK Streamable HTTP, token mã hóa, inventory phân trang, app allowlist, schema pin và kiểm tra trước gọi, timeout/abort, giới hạn kết quả |
| MCP export/import và thu hồi | Đã có connector binding, tắt connector có hiệu lực với app publish, từ chối truy cập chéo workspace |
| Kiểm thử MCP | Fixture SDK JSON/SSE, trùng tên tool, schema drift, sai args, tool ngoài quyền, hủy đang chạy; Qwen thật gọi MCP fixture trong live-smoke |
| Durable extraction/indexing jobs | Chưa có; hiện chạy trong request, phát hiện trạng thái dở sau restart |
| RAG nâng cao | Chưa có crawler/sync connector, metadata filter, hybrid/rerank, index corpus lớn và bộ đánh giá truy xuất |
| Provider nâng cao | Chưa có discovery/capabilities, usage/cost/quota, mặc định workspace |
| Workflow nâng cao | Chưa có iteration/loop, document/parameter extractor node, lịch chạy và queue worker |
| Plugin/OAuth/stdio MCP | Chưa có; không tự cấp shell/code execution cho Agent |

Nghiệm thu tiếp theo: test provider thật với credential do người dùng cấu hình; corpus có đáp án; queue failure/restart; thêm tool doanh nghiệp được cấp quyền. Hủy request không thể hoàn tác tác động đã thực hiện ở server công cụ.

## Đợt 4 — workspace doanh nghiệp và tích hợp xT

1. Onboarding workspace; thành viên/vai trò; OIDC/SSO; phân tách dữ liệu và credential; migration/backup/restore.
2. Gateway/API quota, log/audit, phân quyền publish và sử dụng app; observability/chi phí.
3. Nối xT MCP và nghiệp vụ doanh nghiệp mô phỏng qua hợp đồng API/tool; giữ ứng dụng này dùng được độc lập cho doanh nghiệp khác.
4. Đóng gói self-host, tài liệu quản trị, cấu hình triển khai; bộ demo bám yêu cầu cuộc thi.
5. Rà soát nguồn gốc mã/asset/dependency và giấy phép trước phát hành. Giấy phép mã mới không tự bảo đảm một bản sao tương tác đáp ứng mọi quyền bên thứ ba.

Nghiệm thu: kiểm thử hai workspace độc lập, vai trò bị từ chối đúng, cài mới/sao lưu/phục hồi, flow demo chạy xuyên ChatGPT→MCP→nghiệp vụ và UI quản trị.

## Trạng thái hoàn tất

Chỉ được gọi là hoàn tất phạm vi Dify đã chọn khi các yêu cầu trong backlog được triển khai hoặc được người dùng điều chỉnh rõ ràng, có bằng chứng kiểm thử phù hợp. Hiện trạng đã có các luồng chính của cả năm nhóm và MCP remote, chưa phải bản thay thế Dify đầy đủ.

## Ưu tiên triển khai kế tiếp

1. **Indexing job bền vững**: lưu trạng thái queued/running/done/error, retry/cancel/progress, phục hồi sau restart; nghiệm thu không mất bản tài liệu đang truy xuất khi lần index mới lỗi.
2. **RAG có đánh giá**: corpus mẫu cố định có đáp án; threshold/metadata filter, hybrid và rerank; chỉ đổi thuật toán sau khi đo precision/recall và thời gian.
3. **Workflow nâng cao**: chạy riêng từng node và structured output theo schema trước; sau đó iteration có giới hạn và semantics lỗi/hủy rõ ràng. Chưa thêm code execution khi chưa có sandbox.
4. **Workspace doanh nghiệp**: mời thành viên/vai trò, quản lý quyền app/tool/knowledge; test hai workspace và từng vai trò trước khi nối nghiệp vụ xT thực tế.

Đây là backlog còn lại của yêu cầu toàn bộ Dify, không phải các hạng mục đã hoàn tất. Các check hiện tại được ghi bằng snapshot nguồn tại `evidence/summary.json`.
