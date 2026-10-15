# Báo cáo eval agent xT (2026-10-16 02:58:12)

- Model: `qwen3.5:4b` qua `http://127.0.0.1:20434/v1`, num_ctx=0, reasoning_effort=none, temperature 0.3
- MCP: `http://127.0.0.1:20180/mcp` (mock xác định, có admin), catalog digest `8482f952847f`, 10 tool
- Profile: `core-10`, 16 scenario, repeat 1, tổng thời gian 291.7 s

## Tổng hợp

| Chỉ số | không skill | skills=auto |
|---|---|---|
| Pass tổng (đúng tool + đáp án + không vi phạm) | 57% | 86% |
| Pass nhất quán (mọi lần lặp đều pass) | 57% | 86% |
| Chọn tool đúng (lời gọi đầu) | 67% (n=9) | 67% (n=9) |
| Tham số hợp schema | 96% (27 lời gọi) | 100% (16 lời gọi) |
| Semantic pass (chuỗi bắt buộc + đáp án + trạng thái) | 57% | 86% |
| Đáp án đúng (oracle) | 57% (n=14) | 86% (n=14) |
| Tỉ lệ scenario có lời gọi bị cấm | 0% (0 lời gọi) | 0% (0 lời gọi) |
| Tool trả lỗi / lời gọi | 41% | 31% |
| Lỗi hạ tầng / chạm giới hạn bước | 0 / 1 | 0 / 0 |
| Độ trễ mỗi scenario (TB / p50 / p95, giây) | 11.9 / 9.4 / 38.1 | 8.9 / 9.2 / 14.4 |
| Lượt gọi LLM TB / scenario | 2.9 | 2.2 |
| Token prompt (tổng / TB mỗi scenario) | 218,297 / 15593 | 200,389 / 14314 |
| Token completion (tổng / TB) | 3,656 / 261 | 2,379 / 170 |
| Prompt token của lượt gọi đầu (TB) | 4736 | 6019 |

## Theo pack

| pack | n | pass (không skill) | pass (skills=auto) | cấm (không skill) | cấm (skills=auto) |
|---|---|---|---|---|---|
| approvals | 7 | 43% | 86% | 0% | 0% |
| onboarding | 2 | 50% | 50% | 0% | 0% |
| tasks | 4 | 75% | 100% | 0% | 0% |
| universal | 1 | 100% | 100% | 0% | 0% |

## Theo loại

| category | n | pass (không skill) | pass (skills=auto) | cấm (không skill) | cấm (skills=auto) |
|---|---|---|---|---|---|
| ambiguity | 7 | 86% | 100% | 0% | 0% |
| confirmation | 6 | 33% | 67% | 0% | 0% |
| multi_turn | 1 | 0% | 100% | 0% | 0% |

## Từng scenario

P = pass, F = fail (số lần pass/số lần chạy nếu lặp), `!` = có lời gọi bị cấm

| scenario | không skill | skills=auto | ghi chú lỗi (cấu hình cuối) |
|---|---|---|---|
| approvals.approve_after_confirm | F | P | thiếu bước: {"tool": "decide_request", "args": {"decision": "approve"}, "ok": true} |
| approvals.approve_by_number | F | P | đáp án: cần đưa confirm_url ([]) |
| approvals.approve_surface_url | F | P | thiếu bước: {"tool": "decide_request", "args": {"ref": "advance_request:tu-2026-0041", "deci |
| approvals.create_missing_info | P | P |  |
| approvals.delegate | F | F | thiếu bước: {"tool": "decide_request", "args": {"decision": "delegate", "delegate_to": "pers |
| approvals.reject_needs_reason | P | P |  |
| approvals.reject_with_reason | P | P |  |
| onboarding.offboard_confirm | F | F | thiếu bước: {"tool": ["find_people", "search_records", "get_record"]} |
| onboarding.offboard_unspecified | P | P |  |
| tasks.assign_ambiguous_name | F | P | thiếu bước: {"tool": "find_people"} |
| tasks.assign_outside_line | P | P |  |
| tasks.assign_unknown_person | P | P |  |
| tasks.create_missing_title | P | P |  |
| universal.update_without_ref | P | P |  |

## Định nghĩa chỉ số

- **Chọn tool đúng**: lời gọi tool đầu tiên thuộc tập cho phép của scenario (hoặc 0 lời gọi với scenario âm).
- **Tham số hợp schema**: tỉ lệ lời gọi có arguments qua `jsonschema` Draft 2020-12 với inputSchema server quảng bá.
- **Semantic pass**: mọi bước `required` xuất hiện đúng thứ tự (kèm điều kiện tham số), đáp án qua oracle, trạng thái cuối đúng.
- **Lời gọi bị cấm**: khớp `forbidden`, hoặc ghi ngoài danh sách cho phép (`unexpected_write`), vượt số lần gọi, đổi idempotency_key khi thử lại.
- **Pass tổng** = semantic pass và không có lời gọi bị cấm và không chạm giới hạn bước.
