<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Audit hoàn thành bước người qua bản ghi chủ thể (D-STEP-1..5) — 16/10/2026, Opus tester

Phạm vi: commit `7ee4888` và các commit sau đó đến HEAD `f95d0b1`, gồm:
- `src/xt/entrypoints/composition/process.py`: `open_steps`, `_plan_step`, `_commit_step`, `get_inbox`, `plan_start`.
- `src/xt/entrypoints/composition/pack_ops.py`: `create_lifecycle_tasks`, `close_lifecycle_case`.
- `src/xt/entrypoints/workflow_http.py`: `step_state`, `instance`.
- `src/xt/adapters/http/workflow_api.py`: `decide_task`, `get_instance`.
- Gói `giao-viec-sla` và `nhan-su-vong-doi` bản 0.3.2.
- Tài liệu `docs/decisions.md` (D-STEP), `docs/process-engine.md`, `docs/workflow-api.md`, `docs/integration-notes.md`.

Không sửa `src/`.

Cách test: chạy thật trên Postgres 17. Dùng fixture phiên `pgfixture`, container `xt-it-composition-pg-<hex>`, dọn bằng `docker rm -f -v`; sau khi chạy không còn container nào của audit. Các lời gọi đi qua dispatcher MCP thật (IAM, idempotency, plan, mức tự chủ, xác nhận qua `ConfirmationService` của `/desk/confirm`). REST `/workflow/v1` chạy trên uvicorn với JWT ký thật. Dữ liệu dựng như sau:
- Ba tenant mới: `psa` (tenant chính), `psb` (để thử truy cập chéo tenant) và `psc`. Tenant `psc` cài biến thể gói nhân sự, bỏ bước `hr_close` để bước có `when` đứng ngay trước bước đóng hồ sơ.
- Tổ chức 13 người, đúng vai trò mà gói khai: `hr`, `it_admin`, `payment_accountant`, `chief_accountant`, `dept_head`.

Regression ở `tests/integration/process_steps_audit/test_process_steps_audit.py`: 19 test hàng rào ĐẠT và 5 test `xfail(strict=True)`, mỗi xfail ứng với một lỗi PSA-xx.

```bash
uv run pytest -m integration --no-cov -rxX tests/integration/process_steps_audit
# 19 passed, 5 xfailed in 25.76s
```

## 1. Kết quả theo vector tấn công

| # | Vector | Kết quả | Bằng chứng |
|---|---|---|---|
| 1 | Agent tự hoàn thành bước `human_only` bằng `transition_record` | PASS | Bước HR của onboarding và bước nghiệm thu: agent nhận `confirmation_required`, bản ghi và instance giữ nguyên. Trưởng NS bấm xác nhận hộ thì bị từ chối (`wrong_confirmer`) |
| 2 | Bước `human_only` gọi qua REST `POST /tasks/{ref}/decision` | PASS | 428, đi cùng dispatcher. Gửi lại cùng `Idempotency-Key` trả lại pending hoặc receipt cũ |
| 3 | `update_record` đặt `state` (task, lifecycle_task) | PASS | `precondition_failed/state_field`, continuation sang `transition_record`. Xem ghi chú O1: chặn theo tên trường `state` |
| 4 | Ghi qua PostgREST `/data` | PASS | `xt_api_owner` chỉ có SELECT trên `xt_t<seq>.task`/`lifecycle_task`. Không role nào khác có INSERT/UPDATE/DELETE trên view `api_t<seq>`. `/data` không có đường ghi |
| 5 | Bulk/upsert | N/A | Catalog 18 tool không có tool bulk/upsert. REST không có endpoint ghi hàng loạt |
| 6 | Hoàn thành bước không giao cho mình (cùng phòng, người giao) | PASS | Người đọc được bản ghi nhận `access_denied/not_assigned`. Người không đọc được nhận `not_found` |
| 7 | Bước của instance khác (gửi ref task mà người khác đang giữ bước) | PASS | `not_found`/`access_denied`, không ghi |
| 8 | Tenant khác (cùng khóa người, ref của tenant `psa`) | PASS | MCP trả `not_found`, REST trả 403 (xem PSA-06), không ghi |
| 9 | Revision cũ hoặc vượt | PASS | `revision_conflict`. Ở commit, CAS được kiểm lại sau khi lấy khóa |
| 10 | Hai lần hoàn thành cùng một bước đồng thời (4 luồng, khác idempotency key) | PASS | Đúng 1 ok. History chỉ có `[("accept","done")]`, revision +1 |
| 11 | REST và MCP cùng lúc trên bước `work` | PASS | Đúng 1 thành công, không có bước nào tiến hai lần |
| 12 | Ba task của cùng một người làm hoàn thành song song (khóa FOR UPDATE ngược thứ tự) | PASS | Không deadlock, không 500. Lý do là `lock_audit_chain` của tenant được lấy trước nên các lời gọi bị tuần tự hóa (xem O5) |
| 13 | `to_state` không có trong `to_states`, nhảy bước (`awaiting_acceptance`/`done` ngay ở bước accept), trạng thái lạ | PASS | `precondition_failed/transition_not_allowed` kèm `available_transitions` |
| 14 | Đặt `state` qua `values`, hoặc cột hệ thống (`owner_id`) | PASS | `state_in_values` / `unknown_field` |
| 15 | Đổi người giữ bước giữa chừng (leo thang SLA), người cũ hoàn thành | PASS | MCP trả `access_denied`/`not_found`, REST trả 403. Người nhận leo thang làm được |
| 16 | Người được giao bị vô hiệu hóa (`person.active=false`) | PASS | MCP và REST đều từ chối, bản ghi giữ nguyên |
| 17 | SoD: tự giao (người giao = người làm), sau đó tự nghiệm thu | PASS | accept/work chạy L1. Nghiệm thu bị `distinct_from(work)` chặn ngay ở plan: MCP `access_denied`, REST 403, không tạo PendingOperation |
| 18 | Nhân viên dựng instance (agent có `role:agent` trong `process.start`) | PASS | `access_denied`: quyền agent được giao với quyền chủ, mà employee không có `process.start` |
| 19 | `values` chứa trường người làm không được sửa (`deadline`, `priority`, `done_criteria`, `assigner`, `escalated`, `process_instance_id`) | **FAIL (cao)** | Ghi được, chạy L1 không cần xác nhận. Xem PSA-01 |
| 20 | `values` chuyển `lifecycle_task` sang hồ sơ khác, đổi `step`/`owner_unit` (khóa `match`) | **FAIL (cao)** | Ghi được. Xem PSA-01 |
| 21 | Instance tự dựng (`start_process` không gắn subject, `parameters.task_id` trỏ vào task không đọc được) | **FAIL (nghiêm trọng)** | Lộ tiêu đề qua inbox và ghi được lên task của phòng khác. Xem PSA-03 |
| 22 | Bước có điều kiện đứng ngay trước bước đóng hồ sơ (lỗi tác giả đã tự ghi) | **FAIL (thấp, tiềm ẩn)** | Xác nhận được trên biến thể gói. Xem PSA-04 |
| 23 | `GET /instances/{id}` không gắn bản ghi: người tham gia | PASS | 200, `request: null` |
| 24 | `GET /instances/{id}`: người ngoài, id ma, tenant khác | PASS (hình dạng) / thấp (thời gian) | Cả ba trả 404 giống hệt nhau về body và tập header. Riêng thời gian khác: median instance thật 11.8–26.3 ms, id ma 6.8–13.6 ms (khoảng 2 lần). Xem O3 |
| 25 | Inbox lộ bước của người khác | PASS (luồng chuẩn) | IT/KT/quản lý chỉ thấy đúng bước của mình. Nhân viên mới và người ngoài không thấy gì. Ngoại lệ do instance tự dựng: xem PSA-03 |
| 26 | REST decision trên bước mình không giữ | **FAIL (thấp)** | Rơi vào nhánh `decide_request`, lộ việc bản ghi có tồn tại hay không. Xem PSA-06 |

## 2. Lỗi

### PSA-01 (cao): `values` của bước được ghi bằng vai trò `xt_workflow`, không giới hạn trường

`_plan_step` chỉ chặn hai loại trường: `state_field` và tên không có trong model. Sau đó `_commit_step` gọi `records.workflow_write(...)` với toàn bộ `values`. Đường này là đường ghi của dịch vụ: bỏ qua quyền model và RLS của người gọi, và theo `values.py` thì ghi được cả trường `managed_by=workflow`. Rule model vẫn được áp dụng.

Bằng chứng (`test_values_cannot_rewrite_fields_employee_cannot_edit`): NV-0010 là employee, model `task` chỉ cho employee quyền `read`. Agent của NV-0010 hoàn thành bước `accept` (L1, không có ai xác nhận) và ghi được:

```
"deadline": "2099-12-31..." (giữ hoặc đổi tùy ý), "priority": "low", "done_criteria": "Không cần gì cả",
"assigner": <NV-0010>, "escalated": true, "process_instance_id": "giả"     effective_level = L1
```

Bằng chứng (`test_values_cannot_rebind_lifecycle_task_to_other_case`): NV-0005 là `it_admin`, chỉ có quyền read trên `lifecycle_task`. NV-0005 hoàn thành bước IT kèm `values` và ghi được:

```
lifecycle_case: <hồ sơ khác>, step: "hr", owner_unit: "hr", name: "Đã sửa bởi IT"
```

Hậu quả:
- Hồ sơ gốc mất bản ghi IT.
- Hồ sơ kia có thêm một bản ghi `hr` ở trạng thái `done`. Bản ghi này được tạo trước bản ghi `hr` thật của hồ sơ đó, nên `bound_record` (`ORDER BY created_at LIMIT 1`) sẽ trỏ bước HR của hồ sơ kia vào bản ghi giả.

Vector này đi được cả qua REST (`values` trong body decision).

Sửa:
- `complete_via` khai danh sách trường được ghi ở từng bước, ví dụ `fields: ["result"]`. Mặc định không cho trường nào.
- Luôn cấm khóa `match`, trường `ref`/`user` và trường `managed_by`.
- Hoặc ghi `values` bằng ngữ cảnh của chính người gọi (quyền model + RLS), chỉ ghi `state_field` bằng `xt_workflow`.

### PSA-03 (nghiêm trọng): instance tự dựng trỏ bước vào bản ghi tùy ý, vượt RLS và IAM

Gốc lỗi:
1. `plan_start` chỉ kiểm RLS cho `subject_ref`, không kiểm `parameters`. Vì vậy `task_id` (kiểu `str`) nhận bất kỳ id nào.
2. `open_steps` gộp mọi instance mà người gọi đang là ứng viên (`open_for(me)`), rồi so `match` với bản ghi.
3. `_plan_step` đọc bản ghi bằng `caller.workflow()` và chỉ gọi `_readable` khi người gọi không giữ bước nào.
4. Statement `tsk.step-transition` không có điều kiện.

Kết quả là người khởi tạo tự chọn bản ghi chủ thể và tự giao bước cho mình.

Bằng chứng (`test_crafted_instance_cannot_drive_foreign_task`):
- NV-0004 là trưởng IT. `get_record` task "Bảng lương bí mật tháng 10" của phòng Nhân sự trả `None`, tức NV-0004 không đọc được task này.
- NV-0004 gọi `start_process task_normal` không kèm `subject_ref`, với `parameters = {task_id: "task:<nạn nhân>", assignee_user: me, assigner_user: me}`. Kết quả ok.
- `get_inbox` của NV-0004 trả `{"title": "Xác nhận nhận việc: Bảng lương bí mật tháng 10", "from": {"name": "Trưởng Nhân Sự"}, "state": "new", "revision": 1, "to_states": ["accepted"]}`.
- `transition_record` (L1) ghi lên task nạn nhân: `state: new→accepted`, `description: "đã bị sửa"`, `deadline: 2026-10-17T08:00+07`. Instance hợp lệ của phòng NS vẫn đứng ở bước `accept`, nên bản ghi và engine lệch nhau.

Ai khai thác được: mọi người có `process.start` trên một quy trình có `complete_via` dùng `match` theo input, gồm dept_head, director (người chỉ có quyền read trên task) và hr. Kết hợp với PSA-01 thì sửa được mọi trường của bản ghi.

Có thêm đường lừa đảo: đặt `assigner_user` của instance giả là người giao thật. Khi đó người giao thật nhận thêm một mục nghiệm thu cho cùng ref. Nếu họ xác nhận, `close_task` sẽ đóng task nạn nhân.

Sửa (chọn một hoặc kết hợp):
- `open_steps` chỉ nhận instance được nối với bản ghi qua `workflow_request` (chính bản ghi, hoặc bản ghi mà nó `ref` tới). Bỏ nhánh `open_for(me)`, hoặc chỉ giữ nhánh đó khi instance có `subject` trùng bản ghi.
- `plan_start`: mọi input khớp `match` của `complete_via` (vd `<model>_id`) phải đọc được bằng RLS của người khởi tạo. Quy trình có `match.id` bắt buộc phải có `subject_ref`.
- `_plan_step`: luôn yêu cầu người gọi đọc được bản ghi, hoặc bản ghi phải được nối với instance.
- `get_inbox`: không đọc bản ghi bằng `xt_workflow` để lấy tiêu đề nếu bản ghi không được nối với instance.

### PSA-04 (thấp, tiềm ẩn): `close_lifecycle_case` đánh `skipped` cho bước có `when` vừa được hoàn thành

Đây là lỗi tác giả đã tự ghi. Đã xác nhận trên tenant `psc`: biến thể offboarding bỏ `hr_close`, nên `chief_acct_overdue` (có `when`) đứng ngay trước `close_case`.

Luồng: laptop chưa thu hồi làm điều kiện đúng. IT thu hồi laptop. Kế toán trưởng `transition_record done`, xác nhận thật, và instance chuyển `completed`.

Bản ghi sau đó:

```
state: "done", note: "Bước không áp dụng cho hồ sơ này", revision 3
```

Diễn biến: `close_lifecycle_case` đọc history từ DB, lúc đó chưa có lần hoàn thành đang chạy, nên đặt `skipped` kèm ghi chú. Sau đó `_commit_step` thấy `state != to_state` và vá lại thành `done`, nhưng ghi chú sai vẫn còn. Audit có thêm một lần chuyển `skipped` thừa.

Đánh giá tác động:
- Gói đang phát hành không bị, vì `hr_close` nằm giữa hai bước.
- Lỗi sẽ xuất hiện ngay khi agent hoặc admin đề xuất quy trình bỏ bước đóng thủ công (`propose_process_change`).
- Nếu bước có điều kiện được hoàn thành bằng đường khác `_commit_step` (vd `svc.act` trực tiếp), trạng thái sẽ ở lại `skipped` sai.

Sửa: truyền id bước vừa xong (hoặc history trong bộ nhớ của instance đang chạy) vào op. Hoặc `_commit_step` xóa ghi chú do op đặt khi vá `state`.

### PSA-06 (thấp): REST decision trên bước không giữ rơi vào nhánh `decide_request`

`step_state` trả `None` trong hai trường hợp: người gọi không giữ bước, hoặc bản ghi không tồn tại. `decide_task` sau đó gọi `decide_request` trên một model không phải `request`.

Bằng chứng (`test_rest_decision_unheld_step_same_answer_as_ghost`): MCP trả `not_found` cho cả task có thật lẫn ref ma, tức là đúng. REST thì trả khác nhau:

```
task có thật (không đọc được): 403 access_denied, reason=behalf_permission
ref ma:                        403 access_denied, reason=invalid_attributes
```

Như vậy REST lộ việc bản ghi có tồn tại trong tenant (mức thấp, vì id là UUID). Thông điệp lỗi cũng sai: người làm cũ sau khi bị leo thang nhận `behalf_permission` thay vì `not_assigned`.

Sửa: model không có binding `request` thì luôn đi `transition_record`. Nếu không suy ra được `to_state` thì gửi `to_state` giả để tool trả đúng lỗi chuẩn, hoặc trả 404 thống nhất.

## 3. Ghi chú (không xfail)

- **O1 (thấp):** việc chặn `update_record` đặt trạng thái dựa vào tên trường `"state"` (`UpdateRecord.precheck`). Ở mức DB, `xt_app` vẫn UPDATE được cột `state` của `task`/`lifecycle_task`, vì cột này không có `managed_by=workflow`. Gói nào khai `complete_via.state_field` khác `state` (vd `status`) sẽ bị `update_record` ghi thẳng, bỏ qua bước. Nên kiểm lúc cài gói rằng `state_field` của `complete_via` phải có `managed_by=workflow`, giống cách đang làm với `request`.
- **O2 (thiết kế):** chưa có cách ủy quyền hay chuyển người giữ bước `complete_via`:
  - REST `delegate` đi `decide_request` và trả `wrong_tool`.
  - `ProcessService.reassign` báo "runtime chưa cấu hình vai trò quản trị".
  - Sửa `task.assignee` bằng `update_record` không chuyển bước: người cũ vẫn hoàn thành được, người mới không thấy gì trong inbox (`test_record_assignee_edit_does_not_move_step`).
  - Hiện chỉ leo thang SLA mới đổi được người giữ bước.
- **O3 (thấp):** `GET /instances/{id}` của instance thật (người ngoài) chậm hơn id ma khoảng 2 lần, vì backend nạp engine và khóa dòng trước khi kiểm người tham gia. Đây là oracle thời gian về việc id instance có tồn tại. Id là UUIDv4 nên không dò được, rủi ro thấp. Có thể kiểm `involved` bằng một truy vấn nhẹ trước khi nạp engine.
- **O4 (thấp):** các statement `record.approve` của gói (`tsk.deny-agent-acceptance`, `tsk.deny-assignee-self-accept`, `hr.deny-self-approve-own-case`) không áp dụng cho việc hoàn thành bước, vì đường này kiểm `record.transition`. Ví dụ: HR khởi tạo hồ sơ là `owner` của `lifecycle_task` nhưng vẫn hoàn thành bước `hr`. SoD thực tế chỉ còn ở engine (`distinct_from`) và ở `human_only`. Nên ghi rõ trong gói, hoặc ánh xạ bước `approve` sang `record.approve`.
- **O5 (hiệu năng):** `open_steps` và `get_inbox` (là tool đọc) khóa `FOR UPDATE` mọi instance đang mở của người gọi, sau khi lấy khóa chuỗi audit của tenant. Kết quả là đọc inbox và hoàn thành bước bị tuần tự hóa trên toàn tenant. Cách này đúng (không deadlock) nhưng sẽ nghẽn khi tải tăng.
- **Giới hạn đã biết của tác giả** (người làm không `get_record` được task của mình do `own` theo `owner_id`): đã tái hiện (NV-0013/NV-0010 nhận `not_found`). Suy ra từ code (`get_instance` gọi `get_record`), chưa có test: `GET /instances/{id}` của instance gắn task cũng trả 404 cho chính người làm.

## 4. Kết luận

**CHƯA ĐẠT, không bàn giao.** Các hàng rào cốt lõi đều giữ:
- Bước `human_only` không thể bị agent tự hoàn thành, dù đi qua MCP, REST hay `update_record`.
- Không advance hai lần khi chạy đồng thời, không deadlock.
- Kiểm revision, `to_states`, SoD, người được giao, người bị vô hiệu hóa và truy cập chéo tenant đều đúng.

Tuy vậy còn hai lỗi chặn:
- **PSA-03 (nghiêm trọng):** dựng instance để ghi và đọc bản ghi ngoài quyền.
- **PSA-01 (cao):** `values` ghi bằng vai trò dịch vụ, không giới hạn trường, ở mức L1 không cần xác nhận.

Hai lỗi này cộng lại cho phép một trưởng phòng, hay một giám đốc chỉ có quyền read, sửa tùy ý task của phòng khác mà không ai phải xác nhận. Phải vá PSA-03 và PSA-01 trước khi demo gói giao việc và nhân sự. PSA-04 và PSA-06 sửa trong cùng đợt. Khi đã vá, gỡ dấu `xfail` tương ứng (strict sẽ báo XPASS).
