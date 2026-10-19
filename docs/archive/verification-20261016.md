<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Xác minh tiếp tục phiên 16/10/2026

**Kiểm lại 17/10:** [báo cáo ChatGPT](mcp-chatgpt-20261017.md) ghi kết quả trên candidate sau sửa MCP. `make check` mới đạt 1.789 Python, 154 Vitest và 19 Playwright; 19 test tích hợp tập trung đạt. Không chạy lại toàn bộ integration/E0–E11 trong lượt này. Phần MCP bên dưới là lịch sử ngày 16/10.

Phạm vi: tích hợp sửa Model Engine/audit R4, fixture integration và PII, harness E2E, proxy LLM xác thực và Desk production dưới CSP. Không commit, push hay phát hành. Ba subagent được phân công worktree riêng; khi tiếp tục sau lần ngắt, ba việc rà soát đơn giản chỉ đọc dùng GPT-6 Sol với reasoning low theo yêu cầu chủ dự án.

## Đầu vào và bằng chứng

HEAD nền: `2784151e7f7a7a046fff0db1ef2c08e4a1560160`, có thay đổi chưa commit. SHA256 của bản đồ đường dẫn → SHA256 nội dung (JSON sort_keys, separators `,`/`:`):

`961dcc8893598a2f5c73b42d04c27c98375d37ddae12ec2d82c413bb1c4cb1cc`

Manifest và log mới nằm ngoài repo tại `/home/chinhan/.cache/xt-verification-20261016/`. Mã nguồn chính được đối chiếu khớp bản E2E trước khi chạy lại kiểm thử. Ảnh tài liệu do headless test tạo lại được trả về bản có sẵn trước kiểm thử. Sau xác minh chỉ thêm báo cáo này và sửa trạng thái tài liệu E2E/CHANGELOG; không đổi mã chạy hay test.

## Kết quả

| Kiểm tra | Kết quả |
|---|---|
| `make check` | 1.780 test Python; 154 test Desk; 19 Playwright headless; lint/format, mypy 181 tệp, 4 import contract, REUSE và SPDX đạt |
| `uv run pytest deploy/tests/offline --no-cov` | 53 passed |
| `XT_BPMN_MODDLE_DIR=… make test-integration` | 548 passed, 2 deselected, 7 xfailed; 409.09 s; không failure/error/skip |
| E0–E11 với DPoP bật | 12/12; E1 2/3; 24 test ngoài LLM đạt |
| E0–E11 với DPoP tắt | 12/12; E1 3/3; 24 test ngoài LLM đạt |

Ba dòng đầu được chạy lại sau lần ngắt môi trường và có log bền (`check.log`, `deploy-offline.log`, `integration.log`). Hai dòng E2E có output thành công trước lần ngắt trong cùng hội thoại; raw log, image IDs và config digests ở `/tmp` đã mất. `e2e-conversation-record.json` ghi lại nguồn gốc và giới hạn này, không thay thế raw log. Không chạy lại E2E sau lần ngắt chỉ để tạo lại log.

BPMN sử dụng dependency khóa từ `tests/integration/process/bpmn_viewer/package-lock.json`, cài với `npm ci --ignore-scripts` ngoài repo. Chỉ một bộ integration nặng chạy; kết thúc không còn container test, các container khác của người dùng được giữ nguyên.

## Giới hạn còn lại

- Sáu xfail kiểm phòng thủ là các phép thử tấn công bị chặn đúng thiết kế; một xfail LOW là oracle metadata OPTIONS khi tới PostgREST trực tiếp. APISIX chặn OPTIONS; không coi đường trực tiếp đã được sửa.
- Credential migrator vẫn có trong API/worker. Việc tách đặc quyền cần executor/queue riêng và chưa hoàn tất; xem `model-engine.md` §7a. Kết quả này không chứng nhận cô lập credential.
- E1 chỉ đạt tiêu chí ≥2/3 với qwen3.5:4b, không bảo đảm model luôn làm đúng. Host không gọi tool nghiệp vụ thay model để làm xanh test.
- Không kiểm triển khai production hay phát hành; chưa thực hiện các thao tác đó.


## MCP trên Claude và ChatGPT thật

Đây là ảnh chụp trạng thái lượt smoke đầu. Lượt mở rộng và trạng thái cuối (hai giấy approved revision 4, các lỗi tìm được) nằm ở [mcp-live-20261016.md](mcp-live-20261016.md).

Sau bộ kiểm thử ở trên, khởi động lại riêng stack demo `xt-e2e-20261016` và tunnel được chủ dự án cho phép để thử trên hai tài khoản test. Dùng browser riêng trên Xvfb :90; không điều khiển desktop thật. Stack/browser được giữ chạy cho phiên thử, nên nhận xét trước đó về không còn container test chỉ áp dụng tại thời điểm kết thúc integration.

| Kiểm tra trực tiếp | Claude | ChatGPT |
|---|---|---|
| OAuth, `inspect_workspace`, `get_inbox` | Thành công; tenant `binhminh`, inbox rỗng | Thành công; tenant `binhminh`, inbox rỗng |
| Tạo giấy tạm ứng, gửi duyệt | 8.000.000 đồng, Đà Nẵng | 6.000.000 đồng, Huế |
| Đối chiếu DB và operation receipt | Một bản ghi, một `create_record` và một `submit_request` hoàn tất | Một bản ghi, một `create_record` và một `submit_request` hoàn tất |
| Trạng thái sau gửi | `in_review`, revision 2 | `in_review`, revision 2 |
| Tự duyệt bằng người lập | `access_denied`, `self_approval`; DB không thay đổi | `access_denied`, `self_approval`; DB không thay đổi |

Claude: `advance_request:e6ed8399-5d12-43e6-a91a-006c609a12c1`, process `81118d40-669d-4317-8b09-506a87dc0a18`. ChatGPT: `advance_request:1cccb9e0-ed3a-45d0-9fdf-8a133334a44f`, process `08282f0d-b69d-4898-a7b3-f88d95a43b25`. Cả hai workflow đang chạy; tuyến trong submit receipt là trưởng bộ phận rồi director. Không coi đây là chứng nhận đã duyệt hết hai bước hoặc đã chi tiền. Mục đích giấy Huế được lưu trong ghi chú gửi workflow, vì schema không có cột mục đích riêng.

Bằng chứng riêng tư nằm ở `/home/chinhan/.cache/xt-live-mcp/`: kết quả UI `claude-{read,write,negative}-result.txt`, `chatgpt-{read,write,negative}-result.txt`; đối chiếu DB `claude-db-verification.json`, `chatgpt-db-verification.json`; log MCP/OAuth tối thiểu `chatgpt-read-verification.json`. Access log không ghi tên tool đọc hoặc tenant; phần đọc dựa vào giao diện nền tảng cùng traffic MCP 200, còn phần ghi có bằng chứng DB và actor client độc lập. Không đưa cookie, token hoặc mật khẩu vào repo.

**Giới hạn tương thích:** hai client thực gửi DCR với `client_secret_post`, không khớp chính sách public-only hiện tại. Cả hai còn yêu cầu `offline_access`. Lượt thử dùng confidential client cấu hình sẵn `xt-claude-live-test`/`xt-chatgpt-live-test`, callback chính xác, PKCE S256, consent, cấp quyền masked cho người demo NV-0013; chỉ hai client test được thêm offline scope, realm test giới hạn idle/max 900/1800 giây. Không đổi policy DCR hoặc metadata scope công khai. Phiên Claude hết hạn trong khoảng nghỉ đã phải kết nối lại. DCR tự động và độ bền refresh qua thời hạn này chưa được chứng nhận.

ChatGPT có lúc nút Connect trong Settings quay chờ; mở trang plugin ở tab mới cho phép kết nối và hoàn tất OAuth. Hộp xin quyền `decide_request` của ChatGPT còn hiện nhãn “Suspicious Instruction” cho hướng dẫn xác nhận trong mô tả tool; cần rà lại nội dung contract trước khi coi trải nghiệm kết nối hoàn thiện. `get_record` với `include=steps`/`related` vẫn chưa được hỗ trợ; không suy ra các bước duyệt đã được mở rộng trong kết quả đọc.
