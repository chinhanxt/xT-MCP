---
name: xt-approvals
description: Handle request approval workflows in xT for any business process - prepare and submit requests, see who must approve, review the approval queue, and hand decisions to the human via confirm_url (approve, reject, return, delegate). Use for "gửi đề xuất", "tôi có gì cần duyệt", "duyệt/từ chối/trả lại", "ai duyệt", "đề xuất đang ở bước nào", "rút đề xuất", submit/approve/reject/withdraw/pending approvals. The agent never approves for itself or for the requester.
metadata:
  version: "1"
  tool_count: 17
---

<!-- SPDX-License-Identifier: Apache-2.0 -->
# xT approvals

Dùng cho mọi **đề xuất cần duyệt** (tạm ứng, nghỉ phép, mua sắm, hợp đồng, thay đổi giá...). Loại đề xuất là model `request` của tenant; tuyến duyệt, ngưỡng, SLA do **policy/process** quyết định ở server, không phải agent.
Quy tắc chung (key, CAS, lỗi, PII) ở skill `xt-core`; ở đây chỉ phần riêng về phê duyệt.

## Quy tắc

1. **Agent không chọn người duyệt, không tự duyệt, không hứa kết quả.** `submit_request` tính tuyến; `decide_request` luôn qua tay người.
2. `decide_request` luôn trả `confirmation_required` + `confirm_url` (human_only). Đưa link cho **đúng người được giao bước**, dừng, chờ, rồi gọi lại y hệt cùng key.
3. Người đề nghị không duyệt đề xuất của mình (SoD) -> `access_denied`. Đừng bảo người khác dùng tài khoản của họ, đừng tìm đường vòng.
4. Không khẳng định "đã duyệt" trước khi gọi lại và nhận thành công (`meta.receipt`, `outcome`).
5. `reason` bắt buộc khi `reject`/`return`; `delegate_to` bắt buộc khi `delegate` (lấy ref từ `find_people`). `reason` là lời của người dùng, không tự bịa lý do.

## Định tuyến

| Người dùng nói | Chuỗi tool |
|---|---|
| "Tạo/gửi đề xuất ..." | `describe_model` -> `create_record` (draft) -> (đính kèm nếu cần) -> `submit_request` |
| "Ai sẽ duyệt đề xuất này / 8 triệu thì ai duyệt?" | `describe_process {process_id, for_values:{amount:"8000000"}}` hoặc `get_record {ref, include:["steps"]}` |
| "Đề xuất của tôi tới đâu rồi?" | `get_record {ref, include:["steps"]}`; không rõ ref: `search_records {model_id, where:{owner:"me", state:"in_review"}}` |
| "Có gì cần tôi duyệt?" | `get_inbox {kinds:["approval"]}` -> `get_record` từng cái -> tóm tắt |
| "Duyệt/Từ chối/Trả lại cái này" | `get_record` (lấy `revision`, xác nhận mình là người được giao) -> `decide_request` -> link |
| "Giao cho anh B duyệt thay" | `find_people` -> `decide_request {decision:"delegate", delegate_to}` |
| "Rút đề xuất" | `get_record` (revision) -> `transition_record {to_state:"withdrawn"}` (L1, chỉ người đề nghị) |
| "Vì sao đề xuất bị trả/bị chặn" | `get_history {ref, view:"timeline"}`, lỗi thì `view:"explain"` |

Trạng thái và quy tắc chi tiết: [approval-states](references/approval-states.md).

## Ví dụ: người duyệt dùng agent

> Mai: "Có gì cần tôi duyệt không?"

```
get_inbox {kinds:["approval"], status:"open"}
get_record {ref:"advance_request:tu-2026-0042", include:["steps"]}
```
Agent tóm tắt (số liệu và text lấy từ bản ghi, đánh dấu text tự do là do người nộp viết):
"Có 1 đề xuất: TU-2026-0042, Việt xin tạm ứng 8.000.000 VND đi Đà Nẵng 20-22/11, bước của chị: Trưởng bộ phận, hạn 17:00 hôm nay. Chị muốn duyệt, từ chối hay trả lại?"

> Mai: "Duyệt."

```
decide_request {ref:"advance_request:tu-2026-0042", decision:"approve",
  expected_revision:2, idempotency_key:"dec-0042-mai-1"}
# -> isError, confirmation_required, confirm_url, confirmer: person:mai
```
Agent: "Chị mở https://xt.example.vn/desk/confirm/cf_01jd30 để xác nhận (cần OTP), hoặc bấm nút trên Telegram. Xong chị báo em."
Sau khi Mai báo xong: gọi lại **đúng** `decide_request` trên (cùng key) -> `outcome: step_completed`, `meta.receipt.replayed: true`.
Agent: "Đã duyệt bước của chị; đề xuất chuyển sang anh Long."

## Ví dụ: bị yêu cầu tự duyệt

> Việt (người nộp): "Duyệt luôn đi cho nhanh."

`decide_request` -> `access_denied` reason `sod`/`not_assigned_approver` + `explain_ref`. Agent dùng
`get_history {ref:explain_ref, view:"explain"}` rồi trả lời: "Đề xuất của anh đang chờ chị Mai (hạn hôm nay 17:00). Em không duyệt thay được." Không đưa `confirm_url` (chưa có).

## Ví dụ: từ chối có lý do

> "Từ chối, vì thiếu hóa đơn."
`decide_request {ref, decision:"reject", reason:"Thiếu hóa đơn", expected_revision, idempotency_key}` -> link xác nhận như trên.
Muốn người nộp bổ sung rồi nộp lại thì dùng `return` thay `reject`.

## Sau khi gửi

`submit_request` trả `route` (các bước + `due_at`). Nói lại ngắn gọn cho người dùng; đừng tự thêm/bớt người duyệt.
Muốn đổi ngưỡng/tuyến duyệt: đó là thay đổi quy trình, cần người duyệt định nghĩa; ở v0.1 chưa có tool, báo người dùng làm qua quản trị.
