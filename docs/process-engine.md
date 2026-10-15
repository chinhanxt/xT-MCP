<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Process engine của xT

Tài liệu cho `xt.domain.process`, `xt.application.process`, `xt.adapters.workflow.bpmn` và `xt.adapters.db.process_timers`. Đây là bản production port từ `research/spike-workflow-v2.md` (G-P1, G-P2, G-P3, G-P4, G-P8, G-I1 tầng workflow, G-P9, G-P10, G-P12 và mọi bản sửa của agent kiểm thử đối kháng). Nguồn thiết kế: `docs/architecture-v2.md` §4.2, §6.2, D4; `research/spike-spiffworkflow.md`; `research/verification-spikes.md` §A.

## 1. Phân lớp

| Lớp | Module | Nội dung |
|---|---|---|
| domain | `xt.domain.process.spec` | `ProcessSpec` v2 (pydantic `extra=forbid`), ngữ pháp người nhận, `parse_spec`, lỗi tiếng Việt `vi_errors`, `json_schema()` |
| domain | `xt.domain.process.expr` | Biểu thức an toàn (AST whitelist): `check_expr`, `expr_names`, `evaluate` |
| domain | `xt.domain.process.assignment` | `OrgDirectory` (port), `AssigneeResolver`, `Assignment` |
| domain | `xt.domain.process.schedule` | Lịch timer start: `Schedule`, `validate_fire`, `timer_inputs` |
| domain | `xt.domain.process.errors` | `SpecError`, `UnsafeExpression`, `DecisionError`, `EngineError`, mỗi lỗi có `code` theo contract lỗi chung |
| application | `xt.application.process.ports` | `ProcessEngine`, `WorkflowHandle`, `CompiledProcess`, `TimerScheduler`, `TimerFireStore`, `InstanceStore` |
| application | `xt.application.process.runtime` | `ProcessRuntime` (một định nghĩa đã compile), `ProcessInstance` (start/inbox/act/reassign/tick) |
| application | `xt.application.process.timers` | `TimerPoller`, `TimerDefinition` |
| application | `xt.application.process.service` | `ProcessService` (use case trên store + catalog), `InMemoryProcessCatalog` |
| application | `xt.application.process.memory` | Bản in-memory của org, instance store, timer store (test/demo; không chứa dữ liệu tổ chức) |
| adapter | `xt.adapters.workflow.bpmn` | Compiler spec -> BPMN 2.0 + BPMNDI, parser cứng, script engine an toàn, serializer, `SpiffProcessEngine` |
| adapter | `xt.adapters.db.process_timers` | `DbApiTimerFireStore` (DB-API: psycopg 3 hoặc sqlite3; không import driver) |

Import-linter có thêm contract `forbidden`: chỉ `xt.adapters.workflow` được import `SpiffWorkflow` và `lxml`. SpiffWorkflow 3.2.0 (LGPL-3.0) là phụ thuộc thư viện dùng nguyên bản: xT chỉ import và subclass (`PythonScriptEngine`, `BasePythonScriptEngineEnvironment`), không sửa, không vendor.

## 2. ProcessSpec v2

Agent và gói nghiệp vụ chỉ nộp JSON. Không bao giờ nộp XML. JSON Schema 2020-12 xuất bằng `json_schema()` và được công bố ở `docs/schema/process-spec-v2.schema.json`; test `test_published_json_schema_matches_code` chặn drift. Mọi object có `additionalProperties: false`.

```json
{
  "id": "proposal_approval", "name": "Đề xuất và phê duyệt", "version": 2,
  "roles": ["dept_head", "chief_accountant", "director"],
  "inputs": {"kind": {"type": "str"}, "amount": {"type": "int", "editable_on_revision": true}},
  "fallback_assignee": "role(director)",
  "trigger": {"kind": "manual"},
  "steps": [
    {"id": "dept_head", "type": "approve", "name": "Trưởng bộ phận duyệt",
     "assignee": {"kind": "department_head", "department_of": "requester", "fallback": ["requester.manager"]},
     "outcomes": ["approve", "reject", "return_for_revision", "delegate", "request_info"],
     "sla": {"after": "PT24H", "escalate_to": "requester.manager"}},
    {"id": "director", "type": "approve", "name": "Giám đốc duyệt", "assignee": "role(director)",
     "when": "amount > 5000000", "outcomes": ["approve", "reject", "return_for_revision"],
     "on_return": {"resume": "restart", "max_rounds": 2}},
    {"id": "ghiso", "type": "service", "name": "Ghi sổ", "op": "post_to_ledger",
     "params": {"amount": "amount", "requester": "requester"}}
  ]
}
```

| Thành phần | Quy tắc |
|---|---|
| Loại bước | `approve` (quyết định), `task` (công việc: `done`, `delegate`, `request_info`), `service` (op trong allowlist, tham số đúng khai báo), `parallel` (2-8 nhánh, `mode: all`, nhánh chỉ chứa approve/task/service, không lồng) |
| `outcomes` | `approve` bắt buộc với `approve`; thêm `reject`, `return_for_revision`, `delegate`, `request_info`. Chỉ outcome đã khai mới có nhánh BPMN |
| `on_return` | `resume: restart` (mặc định, chạy lại từ đầu, định tuyến lại theo dữ liệu đã sửa) hoặc `same_step`; `to_step` quay về một bước người đứng trước (nghiệm thu không đạt -> làm lại); `max_rounds` 1-10. `same_step` bị cấm khi có input `editable_on_revision` (H2) |
| `complete_via` | (bước `task`/`approve`, tùy chọn) bản ghi chủ thể của bước: `model`, `match` (trường -> biểu thức trên biến có sẵn tại bước; `id` = id bản ghi), `states` (`to_state` -> outcome của bước), `state_field` (mặc định `state`), `autonomy` (`L1/L2/L3/human_only`, mặc định `human_only`; bước `approve` chỉ `human_only`). Người được giao hoàn thành bước bằng `transition_record` trên bản ghi đó (docs/decisions.md D-STEP). Khóa vắng mặt không đổi sha256 của định nghĩa cũ |
| `sod` | `not_requester` (mặc định true cho approve, false cho task), `distinct_from: [step]`; quan hệ đối xứng ở runtime (H3) |
| `inputs` | `str/int/number/bool/user/department/date/object`; input tùy chọn có thể khai `default` (str/int/number/bool/date, đúng kiểu); input tùy chọn dùng trong biểu thức **bắt buộc** có `default` (AUD-3); khi chạy, mọi input đều có mặt (thiếu thì nhận `default` hoặc `None`); tên dành riêng (`requester`, `xt_*`, hậu tố `_decision/_result/_comment/_actor/_revise/_info`, biến kỳ) bị cấm |
| `trigger` | `manual` hoặc `timer` (`rrule` rút gọn: chỉ `FREQ/INTERVAL/BYDAY/BYMONTHDAY/BYMONTH/BYHOUR/BYMINUTE`, cấm `COUNT/UNTIL/DTSTART/BYSECOND`; `tz` IANA; `input` từ biến kỳ; `catchup: latest|none`; `grace`) |
| Biểu thức | `when`, `params`, `trigger.input`: ≤ 500 ký tự, ≤ 120 node, độ sâu ≤ 8, hằng chuỗi ≤ 200. Biến phải có sẵn **tại vị trí dùng**: input, `requester`, `{step}_result` của service đứng trước, `{step}_decision/_comment` của bước người đứng trước. Biến của nhánh song song anh em bị từ chối |
| Giới hạn | ≤ 60 bước, ≤ 50 input, ≤ 50 vai trò, tên ≤ 200 ký tự, cấm ký tự điều khiển/ẩn |

### Ngữ pháp người nhận

Chuỗi rút gọn được parse bằng regex cố định, không eval:

```
role(x) | role(x) in department(requester) | role(x) in 'KD' | role(x) in bien_phong
requester.manager | manager(requester, 2) | department_head(department(requester))
department_head('KD') | department_head(bien_phong) | user(bien_nguoi_dung)
```

Biến phải là input kiểu `user`/`department` hoặc `requester` (quy trình timer không có `requester`). Có người nhận động thì bắt buộc `fallback_assignee`, và phần tử cuối của chuỗi đó phải là `role(x)` tĩnh. `role(x) in 'KT'` không được coi là tĩnh (H1).

### Lỗi tiếng Việt

`parse_spec` và compiler ném `SpecError` (`code = rule_violation`) với danh sách lỗi có vị trí và gợi ý:

```
steps[1].slaa: khóa lạ 'slaa' không được phép (có phải 'sla'?); khóa hợp lệ: assignee, id, name, outcomes, sla, sod, type, when
steps[5](director).when: biểu thức 'amout > 5000000' dùng biến chưa khai báo ['amout'] (biến có sẵn: [...])
trigger: loại 'schedule' không được hỗ trợ; chỉ chấp nhận 'manual', 'timer'
bước ghiso: op 'os_system' không có trong allowlist [...]
```

## 3. Compiler

`SpiffProcessEngine().compile(spec, allowed_ops)` (hoặc `compile_process`) trả `CompiledProcess(process_id, bpmn, manifest, spec)` và parse thử BPMN bằng SpiffWorkflow ngay. `allowed_ops` là allowlist `op -> [tham số]` của deployment; bước có SLA cần op `notify`.

- Chỉ phát sinh `startEvent` (thường hoặc timer `timeDate = xt_fire_at`), `userTask`, `serviceTask`, `exclusiveGateway`, `parallelGateway`, `boundaryEvent` timer (SLA), `endEvent` (thường hoặc terminate cho từ chối/rút). Không có `scriptTask`.
- **SLA tính một lần cho cả bước** (AUD-2): trước `ut_X` có service nội bộ `slas_X` (op `xt_sla_deadline`, tiền tố `xt_` dành riêng, spec không dùng được) ghi hạn tuyệt đối `xt_sla_due_X = bây giờ + after`. Boundary trên `ut_X` **và** trên `ri_ut_X` dùng `timeDate = xt_sla_due_X`: ủy quyền hay hỏi thêm thông tin không đặt lại hạn và không dừng đồng hồ; quá hạn khi đang chờ bổ sung thì task hỏi bị hủy và leo thang. Làm lại bước (`restart`, `to_step`) đi qua `slas_X` nên có hạn mới.
- Mọi tham số service do compiler tự sinh cũng qua `check_expr` lúc compile; tên bước trong thông báo leo thang cắt ở 150 ký tự (AUD-1). BPMN sinh ra không parse được -> `SpecError` (AUD-7).
- Mọi định danh qua regex `^[a-z][a-z0-9_]{0,40}$`; mọi tên/giá trị chèn vào XML qua `quoteattr`/`escape` (G-P12).
- Cổng quyết định `dec_ut_X` có đúng một nhánh có điều kiện cho mỗi outcome đã khai, **không có nhánh mặc định**. Giá trị lạ -> `No conditions satisfied` -> runtime khôi phục state (G-P3).
  - `approve`/`done` -> bước kế; `reject` -> `end_rejected` (terminate);
  - `delegate` -> quay lại `ut_X`; `request_info` -> `ri_ut_X` -> quay lại `ut_X`;
  - `return_for_revision` -> `rv_X` (người đề nghị `resubmit`/`withdraw`) -> điểm đầu (restart) hoặc chính bước; hoặc thẳng về `to_step`.
- BPMNDI: một pool, lane theo người nhận ("Trưởng phòng của requester", "Vai trò director", "Người đề nghị", "Hệ thống xT"...), cột theo longest-path trên đồ thị đã bỏ cạnh quay lui. Mọi node có shape, mọi flow có edge, không shape nào chồng nhau; bpmn-moddle (MIT) không cảnh báo.
- Manifest (JSON) cho runtime: mỗi user task -> bước, loại (`decision|task|revise|info`), assignee, outcomes, sod, on_return; `distinct` đối xứng; `step_order`; `spec_sha256`.

## 4. Runtime

`ProcessRuntime(compiled, engine, services, org, admin_role=..., on_event=..., scheduler=...)` gắn một định nghĩa với engine, registry service (`op -> hàm`), `OrgDirectory`, vai trò quản trị (được gán lại task) và các hook.

| Thao tác | Hành vi |
|---|---|
| `start(data, requester)` | Kiểm input đúng khai báo; người đề nghị phải còn hoạt động; quy trình timer chỉ nhận `fire` từ poller (không có requester, `validate_fire` chống giả mạo kỳ) |
| `inbox(user)` | Task READY mà `user` là ứng viên, kèm `candidates`, `reason`, `via` (`primary/fallback/process_fallback/unresolved/override/admin`), `trail` |
| `act(task_id, actor, outcome, comment, delegate_to=, ask=, updates=)` | Kiểm hết trước khi chạm engine, rồi chạy nguyên tử (xem dưới) |
| `reassign(task_id, admin, to_user, reason)` | Chỉ người có `admin_role`; vẫn qua SoD; không gán lại bước sửa đề nghị. Bước sửa `unresolved` (người đề nghị đã nghỉ): người có `admin_role` được `act(..., "withdraw", lý do)`, không được `resubmit` (AUD-9) |
| `tick()` | Đánh giá timer (SLA, timer start) theo đồng hồ hiện tại |
| `to_state()` / `load(state)` | State `{"wf": ..., "meta": ...}`; `load` từ chối meta của định nghĩa/phiên bản khác, và engine từ chối `wf` có spec SpiffWorkflow khác digest của BPMN đã compile (AUD-4). `ProcessService` còn kiểm `meta.id` khớp id dòng (AUD-5) |

Mỗi `act`:
1. Task phải READY; actor phải còn hoạt động và nằm trong `candidates`;
2. `outcome` phải nằm trong `outcomes` của task (fail closed, state không đổi);
3. Kiểm riêng: `return_for_revision` cần lý do và chưa vượt `max_rounds`; `delegate` cần người khác, còn hoạt động, không bị SoD loại; `request_info` cần câu hỏi và người được hỏi hợp lệ; `resubmit` chỉ sửa input `editable_on_revision`, đúng kiểu; tham số không thuộc outcome bị từ chối; `delegate` + `request_info` tối đa 5 lần mỗi bước (M4);
4. Chỉ ghi khóa có tiền tố id bước (`{step}_decision/_comment/_actor`, `_info`, `_revise`);
5. Snapshot -> chạy task + `do_engine_steps()`; lỗi -> khôi phục snapshot, ném `EngineError`. Lỗi service luôn được bọc thành chuỗi (G-P9);
6. Giải người nhận cho task mới READY; sự kiện `assignment.escalated`/`assignment.unresolved` ghi vào `meta.events` (outbox) rồi mới gửi best-effort (M1);
7. Có `scheduler` thì hẹn `wake_at(next_due)` cho timer gần nhất.

**Resolver.** `AssigneeResolver.kinds = {role, manager, department_head, user}`, mỗi kind là hàm đọc `OrgDirectory`. Kind chưa đăng ký thì bỏ qua và ghi trail, không có đường eval. Thứ tự: assignee -> `fallback` của nó -> `fallback_assignee` của quy trình -> `unresolved` (task vẫn READY, không ai act được cho tới khi quản trị gán lại). Ứng viên đã nghỉ hoặc bị SoD loại bị lọc. Leo thang SLA ra đúng người đang giữ việc quá hạn thì đi tiếp chuỗi fallback. Bước sửa chỉ giao cho người đề nghị; người đó nghỉ thì `unresolved` (M3). Mọi ứng viên của task đang mở đều nghỉ thì lần `refresh_assignments`/`tick` kế tiếp giải lại theo chuỗi fallback và phát sự kiện (AUD-8).

**Sự cố engine** (AUD-3): engine lỗi khi `act`/`tick` thì wf được khôi phục, `meta.incidents` thêm một mục `{task, actor, error}` và phát `engine.error`; `ProcessService` vẫn lưu meta đó rồi ném `EngineError`, quản trị thấy qua `get(...)["incidents"]`.

**Idempotency (I-1)**: khóa `"{instance}:{task}"` đổi theo mỗi vòng lặp vì `task.id` mới. Không đặt service có tác dụng phụ (ghi sổ, gửi ra ngoài) **trước** một bước duyệt có `resume: restart`; nếu cần, op tự chặn trùng theo khóa nghiệp vụ.

**Join song song.** `UnstructuredJoin` của SpiffWorkflow gộp data kiểu "nhánh về sau ghi đè" (`test_spiff_join_merge_is_last_writer_wins_documented`). Runtime không khởi tạo sẵn biến quyết định, mỗi bước chỉ ghi khóa có tiền tố id bước, cấm `return_for_revision` trong nhánh song song, và khi quay vòng (`resubmit`, `to_step`) thì **xóa** (không gán `None`) mọi biến của các bước từ điểm quay lại trở đi.

**Lưu trữ.** State lưu cột `json` (giữ thứ tự key), không dùng `jsonb`. `_reorder` (BFS cha trước con) là lớp phòng thủ thứ hai: test tích hợp ghi state vào `jsonb` thật của Postgres 17 rồi nạp lại giữa các nhánh song song vẫn chạy đúng. State hỏng (task mồ côi, thiếu task con) bị từ chối. Script engine không bao giờ được serialize: mỗi lần nạp gắn lại engine an toàn.

### Mã lỗi

| Lỗi | `code` | Khi nào |
|---|---|---|
| `SpecError` | `rule_violation` | Spec sai, op ngoài allowlist |
| `DecisionError` | `invalid_request` | Input/outcome/tham số sai |
| `DecisionError` | `access_denied` (`reason`: `not_assigned_approver`, `sod`) | Không được giao, SoD, đã nghỉ, không có vai trò quản trị |
| `DecisionError` | `precondition_failed` | Task không READY, vượt `max_rounds`/trần ủy quyền, state khác định nghĩa |
| `DecisionError` | `not_found` | `ProcessService`: không có quy trình/instance |
| `EngineError` | `internal_error` | Engine/service lỗi; state đã khôi phục, thử lại được |

## 5. Timer start (G-P2)

```
wf_timer_fire(process_key, period_key, status, version, instance_id, fired_at, PRIMARY KEY(process_key, period_key))
wf_instance(id, process_key, version, state json)
```

`TimerPoller(store, [TimerDefinition(process_key, runtime, not_before)]).poll(now)` chạy trong xt-worker (mỗi 5 s). Với mỗi định nghĩa, trong **một transaction**:
1. `occ` = lần kích hoạt gần nhất ≤ now theo `rrule` + `tz`, và ≥ `not_before`;
2. `period_key = occ.isoformat()` (ví dụ `2026-10-19T08:00:00+07:00`), **không gồm version**: kích hoạt version mới giữa kỳ không chạy lại kỳ đó;
3. `claim_period` = `INSERT ... ON CONFLICT DO NOTHING`; không giành được -> `already`;
4. Giành được -> tính input từ biến kỳ (`fire_at`, `fire_date`, `period_start(_date)`, `period_end_date`) bằng engine biểu thức an toàn, `runtime.start(fire=...)`, lưu instance cùng transaction. Lỗi -> rollback cả dòng kỳ, lần quét sau thử lại;
5. `catchup: latest` chỉ chạy kỳ gần nhất; `catchup: none` + trễ quá `grace` -> `skipped`. Một định nghĩa lỗi không chặn định nghĩa khác (`result: error`).

Lớp ghép (`xt-worker`, job `workflow.timer_starts` do relay hẹn mỗi chu kỳ): `PgTimerFireStore` trên `xt_meta.workflow_timer_fire` (PK tenant + process + kỳ, migration 0012), `TimerPoller(..., processes=ProcessService)` tạo instance qua `ProcessService.start(requester=None, fire=..., version=...)` cùng transaction với dòng kỳ; `not_before` = `process_definition.created_at`.

`dtstart` của lịch là mốc cố định 05/01/2026 nên `INTERVAL>1` không lệch pha theo ngày kích hoạt. DST: giờ không tồn tại được chuẩn hóa qua UTC, giờ lặp chỉ chạy một lần (so sánh theo timestamp UTC). BPMN có timer start event `timeDate = xt_fire_at`: poller tạo sớm thì engine vẫn chờ đúng giờ.

## 6. An toàn

- **Script engine**: `SafeEnvironment.evaluate` chỉ chạy biểu thức đã qua `check_expr`, `__builtins__` rỗng; `execute` (scriptTask/preScript/postScript) luôn bị từ chối; service chỉ chạy op có trong registry, tham số đúng khai báo; op khai tham số `idempotency_key` nhận khóa `"{instance}:{task}"` (G-P10). Subclass `call_service` vá bug của SpiffWorkflow 3.2.0 (biến `script` không định nghĩa, `args[0]` không phải chuỗi).
- **XML**: BPMN được parse bằng lxml với `resolve_entities=False`, `no_network=True`, `load_dtd=False`, `huge_tree=False`, trần 2 MB; từ chối DOCTYPE/ENTITY (kiểm cả trên cây đã parse qua `docinfo`, nên không lọt bằng UTF-16/BOM, AUD-6), `scriptTask`, `callActivity`, `script`, `preScript`, `postScript`. SpiffWorkflow chỉ nhận cây đã parse (`add_bpmn_xml`), không bao giờ tự parse chuỗi thô.
- **Không hard-code dữ liệu tổ chức**: mọi người nhận giải qua `OrgDirectory`. Bản production đọc `org_unit/position` (architecture-v2 §6.2); `InMemoryOrgDirectory` chỉ dùng cho test/demo, dữ liệu do người gọi nạp.

## 7. Nối vào hệ thống (việc của lớp tích hợp)

| Port | Hiện thực có sẵn | Production |
|---|---|---|
| `ProcessEngine` | `SpiffProcessEngine` | như cũ |
| `OrgDirectory` | `InMemoryOrgDirectory` | adapter đọc `org_unit/position` |
| `InstanceStore` | `InMemoryInstanceStore` | bảng `wf_instance` (cột `json`), `SELECT ... FOR UPDATE`, cùng transaction với audit + outbox |
| `TimerFireStore` | `DbApiTimerFireStore` (psycopg/sqlite), `InMemoryTimerFireStore` | `DbApiTimerFireStore(conn, "postgres")`; `process_key` mang tiền tố tenant |
| `TimerScheduler` | (không) | bọc `xt.application.jobs.JobQueue.schedule_at`, dedupe theo `(instance, due)`; job gọi `ProcessService.tick` |
| `EventSink` | callback | outbox relay |

IAM, mức tự chủ, xác nhận của người (D8) và audit nằm ở dispatcher bao ngoài `ProcessService`. Assignment chỉ trả lời "ai được làm task này"; tool vẫn phải kiểm IAM.

## 8. Kiểm thử

```bash
uv run pytest tests/unit/process                       # unit (không cần dịch vụ ngoài)
uv run pytest -m integration tests/integration/process --no-cov   # Postgres 17 thật (docker, cổng 19900, tự dọn)
```

- `tests/unit/process`: toàn bộ test của spike (spec, runtime, timers, BPMNDI, hồi quy kiểm thử đối kháng), 6 process v2 chạy theo kịch bản của `research/spike-workflow-v2/results/packs.txt`, use case, parser cứng, script engine, serializer. Fixture: `tests/fixtures/process/` (6 spec v2, 6 spec v1 gốc, seed tổ chức của pack).
- `tests/integration/process`: 8 worker poller song song trên Postgres 17 (1 `fired`, 7 `already`, 1 instance; container tên ngẫu nhiên, cổng `XT_IT_PROCESS_PG_PORT`, mặc định 19900), state qua `json` và `jsonb` thật, kiểm cấu trúc BPMN 2.0 + DI của 6 pack bằng bpmn-moddle (MIT, không cần trình duyệt; mẫu cố ý làm hỏng phải bị bắt). Test bpmn-moddle tự skip nếu chưa cài: `cp tests/integration/process/bpmn_viewer/package*.json <dir>/ && (cd <dir> && npm ci)` rồi đặt `XT_BPMN_MODDLE_DIR=<dir>`.

## 9. Chưa làm

- `parallel mode: any`, song song lồng nhau, `return_for_revision` trong nhánh: validator từ chối rõ ràng.
- G-P5 (nhắc trước rồi mới leo thang, bỏ bước khi cùng người đã duyệt), G-P6 (SLA lấy từ dữ liệu, lịch nghỉ lễ), G-P7 (hành động IAM theo bước), G-P11 (đồng bộ trạng thái workflow -> record).
- Bố cục DI đơn giản: cạnh cắt chéo lane khi có nhiều người nhận.
