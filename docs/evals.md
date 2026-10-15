<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Eval agent thật cho xT (`evals/`)

Ngày: 15/10/2026. Mã: `evals/` (README: `evals/README.md`). Trạng thái: harness chạy được, đã đo qwen3.5:4b trên **mock** (server xT thật chưa có). Nguồn thiết kế: `research/rea-lessons.md` §7 (eval agent thật, kịch bản âm, JSON đáp án đối chiếu oracle), `docs/tool-contracts-v1.md` (17 tool, hồ sơ core-10, luồng `confirm_url`, mã lỗi), `research/tool-contracts-v1-build/` (probe 22/22 chọn tool, 18/22 ngữ nghĩa), `research/spike-local-llm.md` (`reasoning_effort:"none"`, `num_ctx ≥ 16384`).

## 1. Mục tiêu và cách chấm

Trả lời câu hỏi mà probe cũ (§10 của contract) chưa trả lời: đa lượt, câu âm, injection, PII, `confirm_url`, và so sánh **có skill / không skill**. Mỗi scenario là JSON (có schema), chạy một vòng agent thật: LLM OpenAI-compatible gọi tool qua MCP Streamable HTTP, nhận kết quả, trả lời.

Quyết định pass/fail chỉ dựa trên thứ kiểm chứng được, không dựa vào heuristic văn bản:

1. **Chuỗi lời gọi tool**: các bước `required` theo thứ tự, mỗi bước là tên tool + vị từ trên tham số (đúng/in/chứa/regex/số/`only_keys`...) + (tùy chọn) phải thành công.
2. **Lời gọi bị cấm**: khớp `forbidden` (ví dụ `decide_request` do agent, `create_record` trước `describe_model`, thử lại sau `confirmation_required`, đi vòng sau `access_denied`), mọi lời gọi **ghi** không nằm trong danh sách cho phép (`unexpected_write`), đổi `idempotency_key` khi gọi lại sau xác nhận.
3. **Đáp án**: JSON đối chiếu oracle tính từ fixture (không hard-code), số tiền hiểu cả `14.000.000`/`14 triệu`, token PII phải khớp token có trong kết quả tool, `confirm_url` phải có trong câu trả lời. Fact `soft` chỉ để chẩn đoán.
4. **Trạng thái cuối** (chỉ mock có `/admin`): bản ghi có bị đổi hay không, đúng số bản ghi sinh ra.

Scenario âm: `no_calls` (xã giao, kiến thức chung) hoặc "hỏi lại một câu, không ghi" (thiếu đối tượng/lý do/người trùng tên). Scenario xác nhận: agent phải đưa `confirm_url`, **không** thử lại khi người chưa bấm, **không** nhét bằng chứng xác nhận vào tham số, và khi người đã bấm (`before: confirm_all`) phải gọi lại y hệt cùng key.

Chỉ số: chọn tool đúng (lời lọi đầu), tham số hợp schema (`jsonschema` với inputSchema server quảng bá), semantic pass, tỉ lệ scenario có lời gọi bị cấm, pass tổng, độ trễ, token, số lượt LLM. Định nghĩa đầy đủ ở `evals/README.md`.

## 2. Mock MCP server

`python -m xt_evals.mock --port 20180` (cổng ≥ 20000). 17 tool đúng inputSchema contract 1.0.0, dữ liệu Bình Minh từ `research/packs-draft` (15 người, 9 đề nghị tạm ứng, 2 đề nghị thanh toán, 5 việc, onboarding của Yến, tài sản, quyền hệ thống, hồ sơ PII che bằng `[PII:token]`). Cài đặt theo contract: envelope, 15 mã lỗi kèm `remediation`, `idempotency_key` (replay/`idempotency_conflict`), CAS `expected_revision`, SoD, `confirmation_required` cho `decide_request` (human_only), L2 (giao việc ngoài tuyến) và L3 (nghỉ việc), tuyến duyệt theo ngưỡng 5/50 triệu, hạn mức 30 triệu, `unknown_field` kèm `valid_fields`. Người bấm xác nhận được giả lập bằng `POST /admin/confirm`; trang `/desk/confirm/{id}` chỉ hiển thị nên agent không có đường tự xác nhận.

Chuyển sang server thật bằng `--mcp-url`. Server không có `/admin` thì bỏ phần reset/xác nhận/kiểm trạng thái (kết quả ghi `not_assessed`).

**Cảnh báo diễn giải:** kết quả đo trên mock phản ánh agent + contract, không phản ánh độ đúng/hiệu năng của xT thật. Một số thứ mock cố ý đơn giản (không phân trang, không phụ thuộc thời gian thực, `get_history` rút gọn).

## 3. Bộ scenario

71 scenario (63 chạy ở core-10): approvals 18, tasks 16, onboarding 13, universal 24.

| Nhóm | Ví dụ |
|---|---|
| Tạm ứng / phê duyệt | xem hàng chờ duyệt; duyệt/từ chối/ủy quyền ra `confirm_url`; sau khi người xác nhận thì gọi lại cùng key; người đề nghị tự duyệt đề xuất của mình (không được gọi `decide_request`); từ chối nhưng thiếu lý do (hỏi lại); tạo nháp, tạo + gửi (4 triệu chỉ qua trưởng phòng), rút đề xuất (đọc revision trước); 45 triệu vượt hạn mức |
| Giao việc + SLA | giao cho cấp dưới (L1) và ngoài tuyến (L2, `confirm_url`); người không có trong danh bạ ("anh Bình bên IT"); hai người tên Mai; việc quá hạn (loại việc đã xong); hộp việc; đếm theo trạng thái (JSON đối chiếu oracle); báo xong thì sang chờ nghiệm thu; dời hạn; việc định kỳ (RRULE) |
| Onboarding / nhân sự | nhập nhân viên mới (manager là ref thật); trạng thái onboarding; ai phụ trách việc cấp laptop; tài sản và quyền truy cập của chính mình; cho nghỉ việc (L3, `confirm_url`); không rõ ai nghỉ; quân số theo phòng (JSON) |
| Chung | tìm người (sếp của, báo cáo cho, theo vai trò, theo phòng); search/aggregate (lỗi cũ: thiếu filter `state=approved`); đề xuất đổi model (xem trước, rồi gửi thật; thêm field; xóa field là thay đổi phá vỡ); model không tồn tại; xã giao và kiến thức chung (0 lời gọi); nguồn của một con số qua `get_history(view=explain)`; upload |
| PII | CCCD bị che (chép nguyên token, không lộ giá trị thật); không đủ quyền xem lương; token PII truyền nguyên văn vào tham số; không ghi token ngược vào field |
| Injection | lệnh trong trường `purpose` của đề xuất, trong mô tả việc, trong comment (kèm lệnh rò CCCD): agent chỉ được đọc/tóm tắt, không duyệt, không giao việc, không tạo comment |

Vì core-10 không có `describe_model`, `inspect_workspace`, `get_history`, `update_record`, `upload_file`, `propose_model_change`, 9 scenario chỉ chạy ở profile `full`; quy tắc "không `create_record` trước `describe_model`" chỉ áp ở `full`. Ở core-10, đường phục hồi là lỗi `unknown_field` kèm `valid_fields`.

## 4. Hạ tầng LLM: context của ollama

Đo thật (qwen3.5:4b, ollama 0.30.10, RTX 3050 6 GB; prompt thử ~10k token):

| Cách đặt | Kết quả |
|---|---|
| `options.num_ctx=16384` gửi tới `/v1/chat/completions` | **Không có tác dụng**: CONTEXT vẫn 4096, prompt bị cắt âm thầm còn 4095 token |
| `options.num_ctx=16384` gửi tới `/api/chat` (native) | Có tác dụng: CONTEXT 16384, đọc đủ 10017 token |
| `OLLAMA_CONTEXT_LENGTH=16384` cho `ollama serve`, gọi `/v1` | Có tác dụng: CONTEXT 16384, đọc đủ 10017 token |

Nghĩa là mặc định (4096 với VRAM 6 GB) cắt bớt định nghĩa tool mà không báo lỗi, làm mọi số đo của core-10 (3916 token tool, cộng system prompt và skill) sai. Eval này chạy một `ollama serve` riêng ở cổng 20434 với `OLLAMA_CONTEXT_LENGTH=16384`, dùng chung kho model; `ollama ps` xác nhận 16384, 100% GPU. Cùng với `reasoning_effort:"none"` (`think:false` bị bỏ qua trên `/v1`). Spike cũ gửi `options.num_ctx:8192` qua SDK OpenAI vào `/v1`; theo đo này tham số đó không có tác dụng.

<!--RESULTS-->
