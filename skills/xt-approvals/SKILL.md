---
name: xt-approvals
description: Handle request approval workflows in xT for any business process - prepare and submit requests, see who must approve, review the approval queue, and hand decisions to the human via confirm_url (approve, reject, return, delegate). Use for "gửi đề xuất", "tôi có gì cần duyệt", "duyệt/từ chối/trả lại", "ai duyệt", "đề xuất đang ở bước nào", "rút đề xuất", submit/approve/reject/withdraw/pending approvals. The agent never approves for itself or for the requester.
metadata:
  version: "1"
  tool_count: 17
---

<!-- SPDX-License-Identifier: Apache-2.0 -->
# xT approvals

Đề xuất cần duyệt (tạm ứng, nghỉ phép, mua sắm...). Tuyến duyệt do server tính; agent không chọn người duyệt, không tự duyệt.
Quy tắc chung ở `xt-core`. Chỉ gọi tool có trong danh sách tool của bạn.

## Quy tắc

1. **Duyệt / từ chối / trả lại / ủy quyền:** `get_record` lấy `revision`, rồi gọi `decide_request` ngay. Đừng dừng ở bước đọc. Bước hiện tại là phần tử `steps` có `state:"active"`; các bước `pending` là bước sau, không phải bước đang chờ.
2. `decide_request` luôn trả `confirmation_required` kèm `confirm_url`. **Chép nguyên link nhận được** (không tự viết link), nói người được giao bước phải mở, rồi dừng. Không gọi lại khi người dùng chưa báo đã bấm.
3. Người dùng báo đã bấm -> gọi lại `decide_request` **y hệt** (cùng tham số, cùng `idempotency_key`), báo `outcome`.
4. `reject`/`return` cần lý do **do người dùng nói**. Chưa có lý do -> hỏi "Lý do từ chối là gì?" và dừng; không tự viết lý do, không gọi `decide_request`.
5. `delegate`: `find_people` lấy ref người nhận -> `decide_request {decision:"delegate", delegate_to:"person:..."}`.
6. Người đề nghị không tự duyệt đề xuất của mình: không gọi `decide_request`; nói ai đang phải duyệt (từ `get_record`).
7. Chỉ có mã (TU-2026-0041): tìm ref bằng `get_inbox` hoặc `search_records {query:"TU-2026-0041"}`.

## Chuỗi tool

| Người dùng nói | Chuỗi |
|---|---|
| Có gì cần tôi duyệt | `get_inbox` |
| Đề xuất tới đâu / ai đang duyệt | `get_record` (không rõ ref: `search_records {where:{owner:"me"}}`) |
| Tạo đề xuất (nháp) | `create_record` |
| Tạo và gửi / gửi bản nháp | `create_record` -> `submit_request` |
| Rút đề xuất | `get_record` -> `transition_record {to_state:"withdrawn"}` |
| Duyệt / từ chối có lý do | `get_record` -> `decide_request` -> đưa `confirm_url` |

## Ví dụ

> Mai: "Duyệt TU-2026-0042."

```
get_record {ref:"advance_request:tu-2026-0042"}           # record.revision: 2
decide_request {ref:"advance_request:tu-2026-0042", decision:"approve",
  expected_revision:2, idempotency_key:"dec-0042-mai"}     # -> confirmation_required, details.confirm_url
```
Trả lời: "Chị mở link này để xác nhận: <confirm_url đúng như kết quả tool>. Xong chị báo em."
> Mai: "Xong rồi." -> gọi lại đúng lời gọi trên, cùng key -> báo `outcome`.

Trạng thái, mã lỗi, hồ sơ đầy đủ: [approval-states](references/approval-states.md).
