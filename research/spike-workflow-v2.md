<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Spike workflow v2: đóng các khoảng trống mức A của process compiler/runtime

Ngày: 2026-10-15. Người làm: Opus 5.5 (codex hết quota, nhận lại task). Mã: `research/spike-workflow-v2/` (khoảng 1.870 dòng nguồn, 1.250 dòng test). Prototype chạy ở `/tmp/xt-wf2` (đã dọn).
Môi trường: Python 3.12.3, venv riêng, `SpiffWorkflow==3.2.0` (LGPL-3.0, chỉ import), `pydantic 2.14.1` (MIT), `lxml 6.1.3` (BSD-3), `python-dateutil 2.9.0` (Apache-2.0/BSD-3), `jsonschema 4.26` (MIT, chỉ test), `psycopg 3.3.6` (LGPL-3.0, chỉ test). Postgres 17-alpine tạm (cổng 15491, đã xóa). Kiểm BPMN bằng `bpmn-moddle 9.0.4` (MIT) và `bpmn-js 18.31` (MIT + điều khoản giữ logo bpmn.io; chỉ là công cụ kiểm, không phải phụ thuộc) trong Chrome headless.
Không sửa bản gốc `research/spike-spiffworkflow/` và `research/packs-draft/` (đã chụp bản sao input vào `fixtures/`).

## 1. Kết quả tóm tắt

| Gap (REPORT §6) | Mức | Trạng thái | Bằng chứng (test) |
|---|---|---|---|
| G-P1 không có song song | A | **Đóng**: `type: parallel` (fork/join, `mode: all`), từ chối trong nhánh kết thúc cả quy trình (terminate end) | `test_parallel_onboarding_fork_join_with_jsonb_reload`, `test_parallel_offboarding_services_in_branches_and_conditional_after_join`, `test_parallel_reject_in_branch_terminates_whole_process`, `test_parallel_sla_escalation_in_branch`, `test_parallel_inside_restart_loop_no_stale_merge` |
| G-P2 không có timer khởi tạo | A | **Đóng**: `trigger: {kind: timer, rrule, tz}` + timer start event BPMN + poller idempotent theo kỳ | `test_poller_idempotent_per_period`, `test_poller_concurrent_workers_sqlite`, `test_poller_concurrent_workers_postgres` (PG thật, 8 worker), `test_timer_start_event_waits_until_fire_time`, `test_dst_gap_and_overlap_fire_once`, ... |
| G-P3 outcome lạ đi tiếp như duyệt | A | **Đóng**: outcome tường minh, gateway không có nhánh mặc định, runtime chặn trước | `test_gp3_unknown_outcome_rejected_by_runtime_state_unchanged`, `test_gp3_engine_gateway_fails_closed_even_if_runtime_bypassed`, `test_gp3_outcome_not_declared_for_step_rejected`, `test_gp3_bpmn_decision_gateways_have_no_default_flow` |
| G-P4 lane tĩnh | A | **Đóng**: `assignee` động giải từ dữ liệu tổ chức qua resolver đã đăng ký; không giải được thì leo thang, không bỏ bước | `test_dynamic_*` (5 test), `test_resolver_unknown_kind_in_tampered_manifest_does_not_eval` |
| G-I1 SoD cứng theo creator/owner | A | **Đóng ở tầng workflow**: `sod: {not_requester, distinct_from}` theo bước | `test_sod_self_assigned_task_cannot_accept_own_work`, `test_return_to_step_rework_loop_and_sod_on_acceptance` |
| G-P8 spec không có schema | B (chặn B6) | **Đóng**: pydantic `extra=forbid`, lỗi tiếng Việt có gợi ý, kiểm biến theo thứ tự bước, JSON Schema xuất cho agent | `test_gp8_typo_slaa_rejected_not_silently_dropping_timer`, `test_gp8_unknown_keys_everywhere_rejected_in_vietnamese`, `test_gp8_when_uses_undeclared_variable_fails_at_compile`, `test_gp8_variable_from_later_step_or_sibling_branch_rejected`, `test_gp8_original_v1_pack_specs_are_rejected_with_reasons`, `test_json_schema_export_is_strict_and_accepts_v2_packs` |
| BPMNDI (critic) | | **Đóng**: auto-layout pool + lane + shape + edge | `test_bpmndi_complete_and_consistent[6 pack]`, `test_bpmn_opens_in_bpmn_js_without_warnings` |
| G-P9 lỗi service bị che | B | Đóng kèm | `test_service_error_wrapped_and_state_restored` |
| G-P10 idempotency | B | Đóng một phần: key `(instance, task)` truyền cho op có khai tham số | `test_idempotency_key_is_instance_and_task` |
| G-P12 role không escape | C | Đóng (ID regex cho mọi định danh, `quoteattr`) | `test_malicious_spec_values_rejected[role có ký tự XML]` |
| G-M1, G-M2 | A | **Ngoài phạm vi** (model engine) | xem §8 |

**Chặn B6 (architecture-v2-verdict §9):** G-P3 và G-P8 đã đóng, có test tên rõ ở bảng trên và ở §4.

Test: **120 passed** (gồm Postgres 17 thật và render bpmn-js trong Chrome headless), log ở `results/pytest.txt`. Không có Postgres/Node thì 118 passed, 2 skipped. Kết quả 6 pack: `results/packs.txt` (trích ở §6).

**Test chứng minh chặn B6:**
- G-P3: `tests/test_runtime.py::test_gp3_unknown_outcome_rejected_by_runtime_state_unchanged`, `::test_gp3_engine_gateway_fails_closed_even_if_runtime_bypassed`, `::test_gp3_outcome_not_declared_for_step_rejected`, `::test_gp3_bpmn_decision_gateways_have_no_default_flow`, `::test_reject_terminates_without_ledger`, `::test_revise_loop_restart_reroutes_by_new_amount`, `::test_return_to_step_rework_loop_and_sod_on_acceptance` (nghiệm thu không đạt quay lại làm, không kết thúc).
- G-P8: `tests/test_spec.py::test_gp8_typo_slaa_rejected_not_silently_dropping_timer`, `::test_gp8_unknown_keys_everywhere_rejected_in_vietnamese`, `::test_gp8_when_uses_undeclared_variable_fails_at_compile`, `::test_gp8_variable_from_later_step_or_sibling_branch_rejected`, `::test_gp8_original_v1_pack_specs_are_rejected_with_reasons`, `::test_json_schema_export_is_strict_and_accepts_v2_packs`, `tests/test_runtime.py::test_inputs_strictly_validated`.

## 2. Định dạng process-spec v2

Toàn bộ là pydantic `extra="forbid"` (`src/xtwf/spec.py`), JSON Schema ở `schema/process-spec-v2.schema.json` (24 KB, mọi object `additionalProperties: false`, mô tả tiếng Việt). Sinh lại: `cd src && python -m xtwf.spec > ../schema/process-spec-v2.schema.json`.

```json
{
  "id": "proposal_approval", "name": "...", "version": 2,
  "roles": ["dept_head", "payment_accountant", "chief_accountant", "director"],
  "inputs": {"kind": {"type": "str"}, "amount": {"type": "int", "editable_on_revision": true}},
  "fallback_assignee": "role(director)",
  "trigger": {"kind": "manual"},
  "steps": [
    {"id": "dept_head", "type": "approve", "name": "Trưởng bộ phận duyệt",
     "assignee": {"kind": "department_head", "department_of": "requester", "fallback": ["requester.manager"]},
     "outcomes": ["approve", "reject", "return_for_revision", "delegate", "request_info"],
     "sla": {"after": "PT24H", "escalate_to": "requester.manager"}},
    {"id": "p", "type": "parallel", "name": "...", "branches": [[{"...": "..."}], [{"...": "..."}]]},
    {"id": "director", "type": "approve", "name": "...", "assignee": "role(director)", "when": "amount > 5000000",
     "outcomes": ["approve", "reject", "return_for_revision"], "on_return": {"resume": "restart", "max_rounds": 2}}
  ]
}
```

| Thành phần | Quy tắc |
|---|---|
| Loại bước | `approve` (quyết định), `task` (công việc: `done`, `delegate`, `request_info`), `service` (op allowlist, tham số đúng khai báo), `parallel` (2–8 nhánh, nhánh chỉ chứa approve/task/service, không lồng) |
| `outcomes` | `approve` (bắt buộc), `reject`, `return_for_revision`, `delegate`, `request_info`. Chỉ outcome đã khai mới có nhánh BPMN |
| `on_return` | `resume: restart` (mặc định: chạy lại từ bước đầu, định tuyến lại theo dữ liệu đã sửa) hoặc `same_step`; `to_step` để quay về một bước người đứng trước (nghiệm thu không đạt → làm lại); `max_rounds` 1–10 |
| `assignee` | Chuỗi rút gọn theo ngữ pháp cố định (regex, không eval) hoặc object. `role(x)`, `role(x) in department(requester)`, `role(x) in 'KD'`, `requester.manager`, `manager(v, 2)`, `department_head(department(requester))`, `department_head('KD')`, `department_head(v)`, `user(v)`. Biến phải là input kiểu `user`/`department` hoặc `requester`. `fallback` tối đa 5 |
| `fallback_assignee` | Bắt buộc khi có người nhận động; phần tử cuối của chuỗi phải là `role(x)` tĩnh |
| `sod` | `not_requester` (mặc định true cho approve, false cho task), `distinct_from: [step]` |
| `inputs` | Kiểu `str/int/number/bool/user/department/date/object`; runtime kiểm khi start và khi nộp lại; tên dành riêng (`requester`, `xt_*`, hậu tố `_decision/_result/...`) bị cấm |
| `trigger` | `manual` hoặc `timer` (`rrule` RFC 5545 rút gọn: chỉ `FREQ/INTERVAL/BYDAY/BYMONTHDAY/BYMONTH/BYHOUR/BYMINUTE`, cấm `COUNT/UNTIL/DTSTART/BYSECOND`; `tz` IANA; `input` ánh xạ từ biến kỳ; `catchup: latest|none`; `grace`) |
| Biểu thức | `when`, `params`, `trigger.input`: AST whitelist cũ + trần 500 ký tự, 120 node (đóng minor VS A.1). Tên biến phải có sẵn **tại vị trí đó**: input, `requester`, `{step}_result` của service đứng trước, `{step}_decision/_comment` của bước người đứng trước; biến từ nhánh song song anh em bị từ chối |

Lỗi tiếng Việt, thật (từ test và `results/packs.txt`):

```
steps[1].slaa: khóa lạ 'slaa' không được phép (có phải 'sla'?); khóa hợp lệ: assignee, id, name, outcomes, sla, sod, type, when
steps[1].assignee.departmnt_of: khóa lạ 'departmnt_of' không được phép (có phải 'department_of'?); khóa hợp lệ: department, department_of, ...
steps[5](director).when: biểu thức 'amout > 5000000' dùng biến chưa khai báo ['amout'] (biến có sẵn: [...])
steps[2]: loại 'script' không được hỗ trợ; chỉ chấp nhận 'approve', 'task', 'service', 'parallel'
trigger: loại 'schedule' không được hỗ trợ; chỉ chấp nhận 'manual', 'timer'
```

## 3. Compiler: BPMN sinh ra

`src/xtwf/compiler.py` trả `Compiled(bpmn, manifest, spec)`. Manifest (JSON) cho runtime: mỗi userTask → bước, loại (`decision|task|revise|info`), assignee, outcomes, sod, on_return; `step_order`; `spec_sha256`. Chỉ phát sinh: `startEvent` (thường hoặc timer `timeDate = xt_fire_at`), `userTask`, `serviceTask`, `exclusiveGateway`, `parallelGateway`, `boundaryEvent` timer (SLA), `endEvent` (thường, terminate cho từ chối/rút). Không có `scriptTask`; `harden_bpmn` từ chối DOCTYPE/ENTITY/scriptTask/preScript/postScript trước khi đưa cho SpiffWorkflow (khuyến nghị VS A.3).

Cổng quyết định (G-P3): `ut_X → dec_ut_X` với **một nhánh có điều kiện cho mỗi outcome đã khai, không có `default`**:
- `approve`/`done` → bước kế; `reject` → `end_rejected` (terminate);
- `delegate` → quay lại chính `ut_X` (runtime gán người được ủy quyền);
- `request_info` → `ri_ut_X` (người được hỏi) → quay lại `ut_X` (đúng người đã hỏi);
- `return_for_revision` → `rv_X` (người đề nghị: `resubmit`/`withdraw`) → điểm bắt đầu (restart) hoặc chính bước; hoặc thẳng về `to_step`.

Giá trị khác → SpiffWorkflow ném `WorkflowException: No conditions satisfied for dec_ut_dept_head` → runtime khôi phục state. Đã chạy thật bằng cách bỏ qua runtime và ghi `dept_head_decision='return_for_info'` vào task như harness cũ: lỗi, `post_to_ledger` không chạy, process không hoàn tất.

**BPMNDI** (`src/xtwf/layout.py`): `collaboration` + 1 `participant` (pool), lane ngang theo người nhận (mô tả tiếng Việt: "Trưởng phòng của requester", "Vai trò director", "Người đề nghị", "Hệ thống xT"...), cột = lớp longest-path trên đồ thị đã bỏ cạnh quay lui (DFS), hàng theo lane. Cạnh quay lui đi vòng phía dưới, boundary đặt trên mép dưới task. Kiểm:
- SpiffWorkflow parse OK cả 6 pack;
- mọi flow node có `BPMNShape`, mọi `sequenceFlow` có `BPMNEdge` ≥ 2 waypoint, mỗi node thuộc đúng 1 lane và nằm trong dải lane, không hai shape nào chồng nhau;
- `bpmn-moddle`: 0 cảnh báo; `bpmn-js` `importXML` trong Chrome headless: 0 cảnh báo, render ra 7–68 shape mỗi pack (ảnh chụp từng pack đã xem, sơ đồ hiện đủ pool/lane/task/gateway/boundary).

Bố cục đúng nhưng chưa đẹp: lane theo người nhận làm nhiều cạnh dọc chéo lane, nhãn gateway dày. Đủ cho tiêu chí "BPMN mở được trong Camunda Modeler"; sửa tay vẫn được vì là DI chuẩn.

## 4. Runtime

`src/xtwf/runtime.py`, `org.py`, `engine.py`. Mỗi `act()`:
1. task phải READY; actor phải nằm trong `candidates` của assignment;
2. outcome phải nằm trong `outcomes` của task đó (fail closed: `DecisionError`, state không đổi);
3. kiểm riêng: `return_for_revision` cần lý do và chưa vượt `max_rounds`; `delegate` cần người khác, còn hoạt động, không bị SoD loại; `request_info` cần câu hỏi, người được hỏi hợp lệ; `resubmit` chỉ sửa input `editable_on_revision` và đúng kiểu;
4. chỉ ghi các khóa `{step}_decision/_comment/_actor` (hoặc `_info`, `_revise`);
5. snapshot → `task.run()` + `do_engine_steps()`; lỗi → khôi phục snapshot, ném `EngineError` (đóng luôn G-P9: lỗi service được bọc thành chuỗi, `assert False, {dict}` không còn làm SpiffWorkflow nổ `TypeError`);
6. giải người nhận cho các task mới READY, ghi `reason`, `via`, `trail`.

**Resolver (G-P4).** `AssigneeResolver.kinds = {role, manager, department_head, user}`, mỗi kind là một hàm Python đọc `OrgDirectory` (giao diện: `get_user`, `department_head`, `users_with_role`, `department_exists`). Không có đường eval: kind không đăng ký thì bỏ qua và ghi trail (đã thử bằng cách gỡ resolver khỏi registry). Thứ tự: assignee → `fallback` của nó → `fallback_assignee` của quy trình → `unresolved`. Ứng viên bị lọc nếu không còn hoạt động hoặc bị SoD loại. `unresolved`: task vẫn READY, không ai act được, phát sự kiện `assignment.unresolved`; chỉ người có vai trò quản trị (cấu hình `admin_role`) gán lại được, và vẫn qua SoD. Leo thang SLA mà giải ra đúng người đang giữ việc quá hạn thì đi tiếp chuỗi fallback.

Kết quả với dữ liệu seed thật của pack (`fixtures/org_users.json`, `org_nhan_vien.json`):

| Tình huống | Kết quả |
|---|---|
| NV-0006 (KD) đề nghị | `department_head(department(requester))` → NV-0005 |
| NV-0005 (trưởng KD) tự đề nghị | NV-0005 bị SoD → fallback `requester.manager` → NV-0001, sự kiện `assignment.escalated`. Bỏ được cờ `requester_is_dept_head` mà harness cũ phải truyền tay |
| NV-0001 (giám đốc, không có quản lý) tự đề nghị | trail: trưởng BGD là chính mình (SoD) → "NV-0001 không có quản lý (cấp 1)" → `role(director)` cũng là chính mình → `unresolved`; NV-0001/0005/0002 act đều bị từ chối; NV-0006 gán lại bị từ chối (không có vai trò admin); NV-0012 (admin) gán lại cho NV-0001 bị chặn SoD, cho NV-0002 thì được |
| NV-0005 bị đánh dấu nghỉ | trưởng KD và quản lý của NV-0006 đều là NV-0005 → `process_fallback` → NV-0001 |
| Onboarding NV-0015 | `employee_user.manager` → NV-0005; `role(it_admin)` → NV-0012/13/14 |
| `manager(requester, 2)` của NV-0013 | NV-0012 → NV-0001 |
| `role(employee) in department(requester)` của NV-0006 | NV-0005, 0007, 0008, 0015 |

**SoD theo bước (G-I1 tầng workflow).** Giao việc: người giao vừa là người khởi tạo vẫn nghiệm thu được (`not_requester: false`), còn người làm không được (`distinct_from: [work]`). Thay được `iam_resource_mapping` "creator = người làm" của pack. Khi tự giao cho chính mình (NV-0005 giao NV-0005), nghiệm thu leo thang tới NV-0001.

**Phát hiện mới: join của SpiffWorkflow gộp data kiểu "nhánh về sau ghi đè".** `UnstructuredJoin._run_hook` `DeepMerge` data của từng nhánh theo `last_state_change`. Biến có sẵn trước fork, bị một nhánh sửa, sẽ bị bản cũ của nhánh khác đè. `test_spiff_join_merge_is_last_writer_wins_documented`: `x='old'`, nhánh a ghi `x='new_from_a'`, sau join `x == 'old'`. Lần chạy đầu của chính spike này dính lỗi đó (runtime khởi tạo sẵn `*_decision = None`, sau join chỉ còn một nhánh có giá trị). Cách xử lý:
- không khởi tạo sẵn biến quyết định;
- mỗi bước chỉ ghi khóa có tiền tố id bước (id duy nhất toàn spec, nên hai nhánh không ghi cùng khóa);
- không cho `return_for_revision` (sửa input) trong nhánh song song;
- khi quay vòng (`resubmit`, `to_step`) runtime **xóa** (không gán `None`) mọi biến của các bước từ điểm quay lại trở đi.

`test_parallel_inside_restart_loop_no_stale_merge` chạy song song hai vòng qua trả lại + hỏi thông tin, kiểm `a_decision/b_decision` sau join đúng ở cả hai vòng.

**Lưu/khôi phục.** Mọi test runtime reload từ JSON đã `sort_keys` và xáo trộn `tasks` (mô phỏng JSONB), giữa các bước song song, trong vòng trả lại, hỏi thông tin, timer SLA. `_reorder` (BFS cha trước con, nay bỏ qua node lặp) vẫn đủ.

## 5. Timer start (G-P2): thiết kế poller

```
wf_timer_fire(process_key, period_key, status, version, instance_id, fired_at, PRIMARY KEY(process_key, period_key))
```

Mỗi lần quét (xt-worker, 5 s), với mỗi định nghĩa timer đang active, trong **một transaction**:
1. `occ` = lần kích hoạt gần nhất ≤ now theo `rrule` + `tz`, và ≥ `not_before` (thời điểm kích hoạt định nghĩa);
2. `period_key` = `occ` ISO có offset (`2026-10-19T08:00:00+07:00`). Khóa **không gồm version**, nên kích hoạt version mới giữa kỳ không chạy lại kỳ đó;
3. `INSERT ... ON CONFLICT (process_key, period_key) DO NOTHING`. `rowcount = 0` → kỳ đã được worker khác xử lý → rollback, bỏ qua;
4. `rowcount = 1` → tính input từ biến kỳ (`period_start_date`, `period_end_date`, `fire_at`...) bằng engine biểu thức an toàn, `runtime.start(..., fire=...)`, ghi `wf_instance` **cùng transaction**, commit. Lỗi → rollback cả dòng kỳ, lần quét sau thử lại;
5. `catchup: latest` (worker chết nhiều kỳ chỉ chạy kỳ gần nhất); `catchup: none` + `grace` (trễ quá thì ghi `skipped`, không tạo instance).

BPMN có `startEvent` + `timerEventDefinition/timeDate = xt_fire_at`: nếu poller tạo instance sớm, engine vẫn chờ đúng giờ (`test_timer_start_event_waits_until_fire_time`). Instance timer không có `requester`. Gọi `start` tay hoặc kèm requester đều bị từ chối. Spec timer không được dùng `requester`, `requester.manager` hay `return_for_revision`.

Đã chạy thật:
- 8 worker song song trên **Postgres 17** (barrier, cùng `now`): 7 `already` + 1 `fired`, 1 instance, 1 dòng kỳ. 8 thread trên sqlite cho kết quả tương tự.
- DST (`Europe/Berlin`, 02:30 hằng ngày): 29/03 02:30 không tồn tại → `03:30+02:00`; 25/10 02:30 lặp hai lần → chỉ 1 lần chạy.

Hạn chế: chuỗi kỳ neo theo `not_before`, nên `INTERVAL>1` phụ thuộc ngày kích hoạt. Chưa có lịch nghỉ lễ cho timer start (SLA calendar là việc khác).

## 6. Chạy lại 6 process của packs-draft (`results/packs.txt`)

**A. Spec v1 gốc qua validator v2:** cả 6 bị từ chối, có lý do:
- `ban_tin_tuan`: 1 lỗi (`trigger: loại 'schedule' không được hỗ trợ`);
- `de_xuat_phe_duyet`: 14 lỗi; `giao_viec_*`: 9 lỗi mỗi file; `nv_offboarding`: 17; `nv_onboarding`: 12. Nội dung là `role: khóa lạ`, `assignee: thiếu`, `escalate_to 'director' không hợp lệ`, `when` dùng `requester_is_dept_head` chưa khai báo...

Trước đây những thứ này bị bỏ qua im lặng hoặc chỉ lỗi lúc chạy.

**B. Spec v2** (`packs/`, giữ id/op/role tiếng Anh như packs-draft hiện tại):

| Process | BPMN | userTask | parallelGW | boundary | lane | DI shape/edge |
|---|---|---|---|---|---|---|
| proposal_approval | 58 KB | 16 | 0 | 4 | 8 | 68/85 |
| task_normal, task_urgent | 26 KB | 8 | 0 | 3 | 6 | 37/34 |
| weekly_digest | 4 KB | 0 | 0 | 0 | 1 | 6/3 (timer start) |
| employee_onboarding | 35 KB | 10 | 2 | 4 | 10 | 52/44 |
| employee_offboarding | 45 KB | 12 | 2 | 5 | 10 | 63/55 |

Trích kịch bản (mỗi bước đều reload từ JSON đã sắp lại key):
```
tạm ứng 4tr                 completed ['dept_head@NV-0005:approve'] ledger=[('NV-0006', 4000000)]
thanh toán 80tr             completed [dept_head@NV-0005, kttt@NV-0003, ktt@NV-0002, director@NV-0001]
G-P3: outcome 'return_for_info'  running ["DENY ... DecisionError: kết quả 'return_for_info' không hợp lệ ..."] ledger=[]
trả lại bổ sung 8tr->4tr    completed [dept_head:approve, director:return_for_revision, director[revise]@NV-0006:resubmit, dept_head:approve] ledger=[('NV-0006', 4000000)]
trả lại rồi rút             withdrawn ledger=[]
SLA 24h quá hạn             ut_dept_head_esc -> NV-0001 (process_fallback; NV-0005 'đang giữ việc bị quá hạn')
task_normal: nghiệm thu không đạt -> làm lại -> đạt     completed [accept, work, acceptance:return_for_revision, work@NV-0007, acceptance:approve]
task_normal: người làm cố nhận nghiệm thu               DENY 'NV-0007' không phải người được giao
weekly_digest: 07:59 -> []; 08:00:30 worker0 fired, worker1 already; 08:05 already; 26/10 fired; 16/11 fired (bỏ 02/11, 09/11: catchup=latest)
   rollup: (2026-10-12..2026-10-18), (2026-10-19..2026-10-25), (2026-11-09..2026-11-15)
onboarding: sau HR READY = it[NV-0012,13,14] ‖ accounting[NV-0003] ‖ manager[NV-0005]; IT hỏi thêm QL; activate 1 lần
offboarding còn tài sản: hr_close -> EngineError "không đóng được hồ sơ: còn 1 tài sản..." state khôi phục, hr_close vẫn chờ
```

## 7. Kiểm thử đối kháng (agent Opus riêng) và bản sửa

Một agent Opus độc lập đã tấn công prototype bằng mã chạy thật. Nó tìm ra 3 lỗi High, 4 Med và một số Low. Đã sửa tất cả High, Med và các Low rẻ. Mỗi lỗi có một test hồi quy trong `tests/test_review_findings.py`.

| # | Mức | Phát hiện | Sửa | Test |
|---|---|---|---|---|
| H1 | High | `role(x) in 'KT'` bỏ qua phòng (`is_static` coi là tĩnh) → giao cho cả công ty | `is_static` tính cả `department` | `test_h1_role_in_literal_department_is_scoped` |
| H2 | High | `resume: same_step` + input sửa được: tăng tiền khi nộp lại mà các bước `when` phía trước (KTT > 50 tr) không chạy lại | Validator cấm `same_step` khi có input `editable_on_revision`; runtime cũng chặn `updates` khi `same_step` | `test_h2_same_step_with_editable_inputs_rejected` |
| H3 | High | `distinct_from` một chiều: ở hai nhánh song song, ai làm nhánh không khai trước thì lọt | Manifest `distinct` đối xứng, kiểm lại lúc act | `test_h3_distinct_from_symmetric_across_parallel[a-b, b-a]` |
| M1 | Med | `on_event` ném lỗi giữa `_sync` → act dở dang | Sự kiện ghi vào meta (outbox) rồi mới gửi best-effort; lỗi gửi ghi `event.delivery_failed` | `test_m1_event_hook_failure_does_not_break_act` |
| M2 | Med | Người đã nghỉ vẫn act được; override (ủy quyền / trả về người hỏi) không lọc người nghỉ hoặc bị SoD | Kiểm `active` lúc act; override bị lọc, không hợp lệ thì giải lại | `test_m2_inactive_candidate_cannot_act_and_override_filtered` |
| M3 | Med | Bước sửa giao cho fallback khi người đề nghị nghỉ: người đó sửa input rồi tự duyệt | Bước sửa chỉ giao cho người đề nghị; nghỉ → `unresolved`; cấm gán lại bước sửa; người đã sửa bị loại khỏi bước `not_requester` | `test_m3_revise_never_given_to_someone_else` |
| M4 | Med | Ủy quyền qua lại vô hạn làm SLA khởi động lại mãi | Trần 5 lần `delegate` + `request_info` mỗi bước | `test_m4_delegate_ping_pong_capped` |
| L | Low | Tham số thừa (`delegate_to` với `approve`) lọt vào audit | Từ chối tham số không thuộc outcome | `test_low_param_hygiene` |
| L | Low | `load()` nhận state của quy trình/phiên bản khác | meta mang `process_id/version/spec_sha256`, `load` kiểm | `test_low_load_bound_to_definition` |
| L | Low | Lỗi biểu thức lúc `start` không được bọc | `EngineError` | `test_low_start_expression_error_wrapped` |
| L | Low | Kỳ giả mạo khi gọi `start(fire=...)` | `validate_fire`: ISO có múi giờ, `period_key == fire_at`, phải đúng một lần kích hoạt của lịch | `test_low_forged_fire_rejected[4 ca]` |
| L | Low | `INTERVAL>1` lệch pha khi version mới kích hoạt ngày khác | `dtstart` là mốc cố định 05/01/2026 | `test_low_interval_phase_independent_of_activation` |
| L | Low | Một định nghĩa timer lỗi làm hỏng cả vòng quét | Cô lập lỗi theo định nghĩa (`result: error`) | `test_low_one_bad_definition_does_not_block_poll` |
| L | Low | DST: so sánh giờ mập mờ (fold) giữa hai tzinfo luôn "khác nhau" (PEP 495) | So sánh theo timestamp UTC | `test_dst_gap_and_overlap_fire_once` |

Danh sách trên lấy từ tóm tắt kết quả của agent kiểm thử. Các tấn công khác (outcome lạ, khóa lạ, biểu thức RCE/DoS, assignee chèn lệnh, XML injection, poller chạy đua) đã có test riêng ở §1 và §4.

## 8. Còn mở / không làm

- **G-M1 (ghi theo trường/trạng thái), G-M2 (nhãn PII)** là model engine, ngoài phạm vi compiler/runtime. Runtime v2 giảm nhu cầu của G-M1 cho phía workflow: chỉ runtime ghi `*_decision`, input chỉ sửa được khi `editable_on_revision`. Nhưng quyền cột ở DB vẫn phải làm ở model engine.
- **G-P5 (B):** "nhắc rồi mới leo thang" (boundary không ngắt) và "bỏ bước nếu cùng người đã duyệt" chưa làm. Ví dụ thật: NV-0005 tự đề nghị 8 tr → NV-0001 duyệt ở bước TBP (fallback) rồi duyệt lần nữa ở bước giám đốc.
- **G-P6** (SLA từ dữ liệu), **G-P7** (hành động IAM theo bước: đã có loại `task`, chưa có `iam_action`), **G-P11** (hook đồng bộ bước → model): chưa làm.
- `parallel mode: any`, song song lồng nhau, `return_for_revision` trong nhánh: bị validator từ chối rõ ràng, chưa hỗ trợ.
- Bước có SLA: mỗi lần `delegate`/`request_info` quay lại, timer SLA tính lại từ đầu (đã giới hạn 5 lần mỗi bước, chưa có SLA tổng).
- Ủy quyền tại runtime không kiểm IAM/mức tự chủ của người nhận ủy quyền (việc của `Host` IAM). Assignment chỉ là "ai được phép làm task", IAM vẫn phải kiểm ở tool.
- Layout DI đơn giản, cạnh cắt chéo lane khi nhiều người nhận.
- `OrgDirectory` mới có adapter in-memory từ seed; bản thật đọc `org_unit/position` (architecture §6.2).

## 9. Cấu trúc thư mục

```
research/spike-workflow-v2/
  src/xtwf/{spec,compiler,layout,engine,org,runtime,timers}.py
  tests/{conftest,test_spec,test_runtime,test_timers,test_bpmn_di}.py, tests/data/join_merge.bpmn
  packs/*.json            6 spec v2
  fixtures/               bản chụp input từ packs-draft (seed org, 6 spec v1)
  schema/process-spec-v2.schema.json
  results/packs.txt, results/pytest.txt
  tests/test_review_findings.py
  run_packs.py, pytest.ini, viewer/check.mjs (+package.json; node_modules không lưu)
```

Chạy lại:

```bash
cd research/spike-workflow-v2
uv venv .venv -p 3.12 && uv pip install -p .venv/bin/python spiffworkflow==3.2.0 pydantic python-dateutil tzdata pytest jsonschema 'psycopg[binary]'
.venv/bin/python -m pytest -q
XT_PG_DSN=postgresql://... .venv/bin/python -m pytest tests/test_timers.py -k postgres
.venv/bin/python run_packs.py > results/packs.txt
(cd viewer && npm i && cd .. && .venv/bin/python -m pytest tests/test_bpmn_di.py)   # cần Chrome
```
