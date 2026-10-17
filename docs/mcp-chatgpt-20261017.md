<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Kiểm thử MCP trên ChatGPT — 17/10/2026

Luồng tạo/gửi giấy, rút giấy và duyệt hai cấp đã chạy được trên ChatGPT thật. Hai cấp duyệt đều xác nhận qua trình duyệt Desk bằng password/TOTP; ChatGPT nhận lại biên nhận và replay thành công. `create_task` tạo/cập nhật được sau sửa binding, dữ liệu bắt buộc và priority. Các ca âm được phân biệt giữa lỗi do máy chủ trả và đầu vào bị host chặn trước MCP.

Phạm vi: stack demo giữ lại `xt-e2e-20261016`, tenant Bình Minh, ba người demo NV-0013/NV-0010/NV-0001. Chỉ thử ChatGPT trong lượt này, dùng browser riêng trên Xvfb :90. Một người điều phối ghi dữ liệu/thao tác browser; ba subagent GPT-6 Sol low kiểm mã, runtime và bằng chứng độc lập. Không commit, push hoặc phát hành.

## Candidate và môi trường

- HEAD nền `2784151e7f7a7a046fff0db1ef2c08e4a1560160` có thay đổi chưa commit; không dùng HEAD làm bằng chứng duy nhất cho candidate.
- 273/273 tệp trong manifest `fixes/candidate-source.json` khớp checkout chính và checkout demo giữ lại. Trong image đang chạy, 208/208 tệp `src/xt` khớp; 65 tệp package không đóng trong image, gói được cài vào DB và kiểm riêng.
- Image `xt/xt:dev`: `sha256:84dd214794b973e50249957b6277a12a4f869e321833d896ee142f18f235df81`.
- Gói `giao-viec-sla` đang cài: `0.3.4`, digest `sha256:2e2e0e0b9230abd59f1b729f272a050281017d2249526e3ba5ba75817e35a3e8`.
- Khôi phục API/worker/PostgREST, tunnel và browser demo hiện có; không tạo stack nặng thứ hai hoặc xóa volume. Public MCP thiếu token trả 401. Catalog có 19 model tool và 3 app-only.
- Sửa dữ liệu hai giấy cũ bị thiếu phòng ban bằng đường maintenance có kiểm revision/audit: approved revision 4→5, giữ nguyên số tiền/trạng thái. Không gọi đây là bản ghi nền chưa sửa.
- Tạo task-kind demo `task_type:e393b823-ffb6-4e8d-b2da-1f37009a2b0c` bằng context nội bộ tạm `xt-demo-fixture`. Không có user seed mang role admin; không thêm role/grant thường trực để làm xanh test.

Mã sản phẩm trong lượt này khớp candidate đã sửa từ lượt trước; công việc mới là phục hồi môi trường, chuẩn bị fixture, kiểm thử và cập nhật bằng chứng/tài liệu.

## Kết quả trực tiếp trên ChatGPT

| Nhóm | Kết quả đã quan sát |
|---|---|
| Kết nối và đọc | Reconnect OAuth, xác minh đúng tenant/actor trước từng nhóm ghi; workspace, model, tìm người, inbox, tìm bản ghi/phân trang, đọc bản ghi và audit hoạt động. PII ở chế độ masked. |
| Tạo/gửi hai giấy mới | B1 và B2 mỗi giấy đúng một create và một submit; dùng lại cùng key trả cùng ref. Cả hai vào `in_review`, revision 2. |
| Metadata rút giấy | Chủ giấy thấy `withdrawn` trong `available_transitions`; trưởng bộ phận đọc được giấy cùng phòng nhưng không thấy đường rút của chủ giấy. |
| Người duyệt đọc giấy | Trưởng bộ phận đọc B1/B2 và giấy cũ approved revision 5 được; audit chain xác minh thành công. |
| Rút B1 | `transition_record` từ in_review revision 2 sang withdrawn revision 3; replay cùng args/key trả `replayed=true`, cùng operation. Workflow withdrawn, không còn candidate hoặc decision. |
| Duyệt B2 cấp trưởng bộ phận | `confirmation_required` → Desk password/TOTP → hai replay nguyên args/key đều trả cùng receipt/decision `step_completed`. Giấy sang revision 3, bước director. |
| Duyệt B2 cấp giám đốc | `confirmation_required` → Desk password/TOTP → hai replay nguyên args/key đều trả cùng receipt/decision `request_approved`. Giấy approved revision 4; workflow completed, không còn candidate, đúng hai decision. Cả hai decision mang client `xt-chatgpt-live-test`. |
| Tạo/cập nhật việc | Tạo đúng một `CV-2026-9717`, replay create không nhân bản. Update một lần revision 1→2; replay update giữ revision 2. Priority công khai `normal` lưu thành `medium`; ngày 26/10 quy về 23:59:59 giờ Việt Nam. Audit chain xác minh. |
| Xung đột | Update revision cũ trả `revision_conflict`; cùng key nhưng đổi payload trả `idempotency_conflict`. Việc vẫn revision 2 và đúng nội dung đã cập nhật. |
| Chín ca âm tới MCP | Thiếu kind/done_criteria/task_no, ngày sai, priority array/enum sai bị validation từ chối; department khác/không tồn tại và owner khác bị từ chối. Không sinh `CV-2026-9718`. |
| Một ca âm bị host chặn | Priority dạng object bị schema của host từ chối trước MCP; không có mã lỗi/trace máy chủ. Không gộp ca này thành phép kiểm server đã đạt. |
| Nhân viên tạo việc | Requester thử một lần tạo `CV-2026-9719`: máy chủ trả `access_denied`, `behalf_permission/implicit_deny`; không sinh task. |
| Cách ly tenant | Requester Bình Minh đọc ref Hoa Mai nhận `access_denied`; không nhận dữ liệu record. |
| Vé notify cũ hết hạn | Retry chính xác message/recipient/key cũ: máy chủ trả `confirmation_declined`, chi tiết `status=expired`. Không cấp vé mới, không xác nhận và không gửi thông báo mới. |
| MCP prompts | Host không cung cấp phương tiện `prompts/list`/`prompts/get` trong phiên đã kiểm. Bốn prompt giữ trạng thái chưa xác minh trên ChatGPT; không thay kết quả SDK thành kết quả host. |

Giấy và công việc dùng trong lượt này:

| Đối tượng | Ref | Trạng thái cuối |
|---|---|---|
| B1, 2 triệu | `advance_request:429fa8e7-d4bf-40f1-896d-e5e6f98a27bf` | withdrawn, revision 3 |
| B2, 6 triệu | `advance_request:d04142d4-7ea3-471e-8a2f-bfe7d5e82896` | approved, revision 4 |
| Công việc | `task:54f1b3dc-b72c-4b45-bb55-4d2c2ccd20b6` | new, revision 2; chưa có process instance |

## Biên nhận và hậu kiểm

| Thao tác | Key/trace tiêu biểu |
|---|---|
| Rút B1 | Key `gpt-20261017-b1-withdraw-v1`; lần đầu `tr_c0259da92405496b8c9b`, replay `tr_570de1c0b4ce4fb78520` |
| Trưởng bộ phận duyệt B2 | Key `gpt-20261017-head-approve-v1`; replay `tr_828103c8760d4f1794b5`, `tr_ef2adfa492cd416f8fe0` |
| Giám đốc duyệt B2 | Key `gpt-20261017-director-approve-v1`; replay `tr_6320f07766ef40a2ad67`, `tr_94c3547b86114459b490` |
| Update stale/key conflict | `tr_0ed446f42cd345ecb4b3` / `tr_5216589ca08643509a44` |
| Nhân viên tạo việc / foreign ref | `tr_386566165a2a40e6a0d1` / `tr_20a5f20b42d8404085da` |
| Notify hết hạn | Key `chatgpt-mcp-full-self-notify-20261016`; `tr_3da3755f40e9479e9821` |

Hậu kiểm SQL/SDK độc lập với model xác nhận số lượng bản ghi, revision, workflow/candidate/decision và audit. B1/B2/task có chuỗi audit hợp lệ với lần lượt 3/4/2 event; inbox phê duyệt của trưởng bộ phận không còn B1/B2. Không có task 9718/9719; chín operation của lượt này đều có key duy nhất. Vé notify cũ không có receipt hoặc notification tương ứng; row operation còn `state=pending` nhưng `pending_status=expired`. Đây là lớp bằng chứng hỗ trợ, không thay thế transcript ChatGPT. Hai giấy gốc vẫn approved revision 5 sau maintenance; chúng không phải B1/B2 mới.

## Kiểm tra mã

| Kiểm tra | Kết quả mới ngày 17/10 |
|---|---|
| Unit tập trung | 245 passed, gồm 120 test runtime và 125 test pack/catalog |
| Integration tập trung | 19 passed, 69,55 giây |
| `make check` | 1.789 Python passed, 560 deselected; 154 Vitest; 19 Playwright |
| Kiểm tĩnh/giấy phép trong `make check` | Ruff/format, mypy 181 tệp, 4 import contract, REUSE và SPDX đạt |

Không chạy lại toàn bộ integration/E0–E11 trong lượt này. Kết quả toàn bộ ngày 16/10 và giới hạn raw log được giữ riêng ở [báo cáo xác minh](verification-20261016.md).

## Giới hạn còn lại

- ChatGPT vẫn hiện “Suspicious Instruction” tại một hộp xin quyền replay `decide_request` của giám đốc. Sau khi kiểm đúng args/thao tác demo đã được phép, chọn **Allow once** cho phép lời gọi chạy và nhận receipt; không bật Always allow. Mô tả tool làm gọn chưa loại bỏ hoàn toàn cảnh báo host.
- Có một lượt hiển thị “Connection interrupted” khi model điều phối tạo/gửi giấy. Đọc lại/replay cùng key và DB xác nhận không nhân bản. `get_record(include=steps)` chưa hỗ trợ; model phục hồi bằng đọc thông thường.
- OAuth dùng confidential client test cấu hình sẵn, callback/PKCE/consent đúng. DCR tự động và refresh dài hạn qua idle/max 900/1800 giây chưa được chứng nhận; không đổi chính sách công khai.
- Chưa xác minh bốn MCP prompt qua host, `approval-card` và human PII reveal qua ChatGPT. Settings còn cảnh báo widget của private tool; một số lượt đã hiển thị “CSP off”, nên không dùng lượt live này để chứng nhận CSP của host.
- Task chỉ được kiểm create/update/replay và quyền/validation; chưa chạy vòng đời giao việc/SLA tới terminal. `aggregate_records` và nhánh dương manual `start_process` chưa được kiểm lại; không ghi thành đã hoạt động đầy đủ.
- Kiểm cách ly phòng ban thuần dựa thêm regression PostgreSQL: người trưởng bộ phận ngoài phòng sẵn có trong demo còn role chief_accountant với quyền đọc rộng, nên không dùng người này làm đối chứng live “phải bị từ chối”.
- Credential migrator vẫn ở API/worker; tách executor/queue là việc riêng chưa hoàn tất. Không thử lại Claude, production, quét malware hoặc phát hành trong lượt này.

## Bằng chứng và bàn giao

Bằng chứng riêng tư (0600) tại `/home/chinhan/.cache/xt-live-mcp/chatgpt-20261017/`: `source-checks.json`, `runtime-verification.json`, `integration.log`, `make-check.log`, các `*-transcript.txt`, `head-confirm-result.json`, `director-confirm-result.json`, `final-state.json`, `final-integrity.json`, `final-root-verification.json`. Manifest candidate nằm ở thư mục `../fixes/`, log maintenance là `repair-result.log` trong thư mục lượt này. Không đưa cookie, token, password/TOTP hoặc raw PII vào repo.

Ma trận `/home/chinhan/.cache/xt-live-mcp/full-matrix.json` giữ nguyên 88 dòng 16/10, thêm 17 nhóm ca ngày 17/10, tổng 105 dòng; không phải 105 ca đều đạt. Các dòng mới gồm 11 nhóm passed, một nhóm passed có cảnh báo host, ba nhóm từ chối đúng kỳ vọng, một nhóm host schema chặn và một nhóm prompt chưa xác minh. Kết quả Claude cũ giữ nguyên. Stack demo được giữ chạy và connector chuyển lại requester để tiếp tục thử.

Kiểm cuối sau cập nhật tài liệu: `git diff --check` và REUSE đạt (1.063 tệp); 273 đường dẫn candidate vẫn khớp cả hai checkout, API/PostgREST healthy và public MCP thiếu token trả 401. Hai subagent đã ghi được hậu kiểm/ma trận trước khi hết hạn mức; phần kiểm chốt do người điều phối hoàn tất.
