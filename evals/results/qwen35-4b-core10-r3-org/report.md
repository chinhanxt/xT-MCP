# Báo cáo eval agent xT (2026-10-16 02:40:13)

- Model: `qwen3.5:4b` qua `http://127.0.0.1:20434/v1`, num_ctx=0, reasoning_effort=none, temperature 0.3
- MCP: `http://127.0.0.1:20180/mcp` (mock xác định, có admin), catalog digest `84b58845d90e`, 17 tool
- Profile: `core-10`, 2 scenario, repeat 3, tổng thời gian 82.9 s

## Tổng hợp

| Chỉ số | không skill | skills=auto |
|---|---|---|
| Pass tổng (đúng tool + đáp án + không vi phạm) | 100% | 100% |
| Pass nhất quán (mọi lần lặp đều pass) | 100% | 100% |
| Chọn tool đúng (lời gọi đầu) | 100% (n=6) | 100% (n=6) |
| Tham số hợp schema | 100% (7 lời gọi) | 100% (6 lời gọi) |
| Semantic pass (chuỗi bắt buộc + đáp án + trạng thái) | 100% | 100% |
| Đáp án đúng (oracle) | 100% (n=6) | 100% (n=6) |
| Tỉ lệ scenario có lời gọi bị cấm | 0% (0 lời gọi) | 0% (0 lời gọi) |
| Tool trả lỗi / lời gọi | 0% | 0% |
| Lỗi hạ tầng / chạm giới hạn bước | 0 / 0 | 0 / 0 |
| Độ trễ mỗi scenario (TB / p50 / p95, giây) | 8.6 / 5.2 / 28.0 | 5.2 / 5.2 / 5.8 |
| Lượt gọi LLM TB / scenario | 2.2 | 2.0 |
| Token prompt (tổng / TB mỗi scenario) | 57,082 / 9514 | 76,629 / 12772 |
| Token completion (tổng / TB) | 552 / 92 | 496 / 83 |
| Prompt token của lượt gọi đầu (TB) | 4061 | 6116 |

## Theo pack

| pack | n | pass (không skill) | pass (skills=auto) | cấm (không skill) | cấm (skills=auto) |
|---|---|---|---|---|---|
| universal | 6 | 100% | 100% | 0% | 0% |

## Theo loại

| category | n | pass (không skill) | pass (skills=auto) | cấm (không skill) | cấm (skills=auto) |
|---|---|---|---|---|---|
| aggregate | 3 | 100% | 100% | 0% | 0% |
| people | 3 | 100% | 100% | 0% | 0% |

## Từng scenario

P = pass, F = fail (số lần pass/số lần chạy nếu lặp), `!` = có lời gọi bị cấm

| scenario | không skill | skills=auto | ghi chú lỗi (cấu hình cuối) |
|---|---|---|---|
| universal.org_find_by_name | P | P |  |
| universal.org_headcount_marketing | P | P |  |

## Định nghĩa chỉ số

- **Chọn tool đúng**: lời gọi tool đầu tiên thuộc tập cho phép của scenario (hoặc 0 lời gọi với scenario âm).
- **Tham số hợp schema**: tỉ lệ lời gọi có arguments qua `jsonschema` Draft 2020-12 với inputSchema server quảng bá.
- **Semantic pass**: mọi bước `required` xuất hiện đúng thứ tự (kèm điều kiện tham số), đáp án qua oracle, trạng thái cuối đúng.
- **Lời gọi bị cấm**: khớp `forbidden`, hoặc ghi ngoài danh sách cho phép (`unexpected_write`), vượt số lần gọi, đổi idempotency_key khi thử lại.
- **Pass tổng** = semantic pass và không có lời gọi bị cấm và không chạm giới hạn bước.
