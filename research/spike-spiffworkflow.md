# Spike: SpiffWorkflow 3.2.0 làm workflow engine nhúng cho xT

Ngày chạy: 2026-10-15. Môi trường: `/tmp/xt-spike-spiff` (uv venv, Python 3.13), `spiffworkflow==3.2.0` (kéo theo `lxml`; thêm `psycopg 3.3`, `pyyaml`), Postgres 16 trong docker `xt-spike-pg` cổng 15432 (đã xóa sau spike).
Mã: `research/spike-spiffworkflow/` (chạy từ thư mục có `src/` chứa các file này: `.venv/bin/python src/t2_execute.py`, ...).

```
uv venv && uv pip install spiffworkflow "psycopg[binary]" pyyaml
docker run -d --name xt-spike-pg -e POSTGRES_PASSWORD=x -e POSTGRES_DB=spike -p 15432:5432 postgres:16-alpine
```

## Kết luận nhanh

**Dùng được, với 4 điều kiện bắt buộc**: (1) thay script engine bằng bản whitelist (mặc định là RCE), (2) tự viết poller timer, (3) sắp lại thứ tự `tasks` khi nạp từ JSONB (bẫy mất dữ liệu), (4) agent chỉ sinh process-spec, compiler sinh BPMN. Cả 7 test đều chạy thật, kết quả bên dưới.

| # | Test | Kết quả |
|---|------|---------|
| 1 | BPMN tạm ứng + boundary timer | Parse OK (`tamung_v1.bpmn`) |
| 2 | Chạy, lane/role, gateway 2 mức tiền, timer | Đúng cả 2 nhánh; timer cần poller gọi `refresh_timers()` |
| 3 | Serialize -> JSONB -> process mới -> tiếp tục | Chạy được **sau khi vá bẫy thứ tự key**; ~13 KB/instance |
| 4 | Script engine | Mặc định thực thi `__import__('os').system`; engine whitelist chặn hết |
| 5 | Versioning | Instance cũ chạy v1, mới chạy v2 (spec nhúng trong state) |
| 6 | Compiler YAML -> BPMN | 431 B YAML -> 4.3 KB XML, chạy đúng; spec độc bị chặn lúc compile |
| 7 | So sánh | Phần cuối |

## 1. BPMN (`tamung_v1.bpmn`)

start -> userTask `ut_tp` "Trưởng phòng duyệt" (lane `truong_phong`) -> exclusiveGateway `gw` (`amount > 10000000` -> userTask `ut_gd` lane `giam_doc`; default -> merge) -> merge -> serviceTask `ghiso` -> end. Boundary timer `sla_tp` (`"PT2S"`, interrupting) -> serviceTask thông báo -> userTask `ut_gd_esc` "Giám đốc duyệt (leo thang)" -> merge.

Điểm cần nhớ khi viết/sinh XML:
- Service task dùng extension Spiff: `<spiffworkflow:serviceTaskOperator id="ghi_so" resultVariable="..."><spiffworkflow:parameters><spiffworkflow:parameter id="amount" type="int" value="amount"/>`. `value` là **biểu thức Python** được evaluate (chuỗi literal phải có nháy: `'abc'`).
- Thời hạn timer cũng là biểu thức Python: trong XML phải là `"PT2S"` (có nháy kép), nên timer là vector tấn công thứ hai (xem mục 4).
- Lane: `task.task_spec.lane` trả tên lane -> dùng làm role cho inbox.
- Không có validator XSD ở cấu hình mặc định mà tôi dùng; lỗi cấu trúc chỉ lộ khi chạy.

## 2. Thực thi (`t2_execute.py`)

API (3.2): `BpmnWorkflow(spec, subprocess_specs, script_engine=...)`; dữ liệu khởi tạo đặt vào `wf.task_tree.data`; `wf.do_engine_steps()` chạy mọi task tự động (gateway, service) tới khi chỉ còn user task READY/WAITING; user task READY = `wf.get_tasks(state=TaskState.READY)` lọc `task_spec.manual`; hoàn tất = `task.data.update(...)` rồi `task.run()` rồi `do_engine_steps()`.

```
== amount=5000000
 READY: [('ut_tp', 'truong_phong', 'Trưởng phòng duyệt')]
 READY sau TP: []
 completed: True | data: {... 'ghiso_result': {'so': 'PC-0001', 'amount': 5000000}}
== amount=50000000
 READY: [('ut_tp', 'truong_phong', 'Trưởng phòng duyệt')]
 READY sau TP: [('ut_gd', 'giam_doc', 'Giám đốc duyệt')]
 completed: True | data: {... 'gd_decision': 'approve', 'ghiso_result': {'so': 'PC-0002', ...}}
== timer SLA (PT2S) – không ai duyệt
 t=0 READY: [('ut_tp', 'truong_phong', 'Trưởng phòng duyệt')]
 t=0 sau refresh_timers, READY: [('ut_tp', ...)]
 t=2.2 chưa refresh, READY: [('ut_tp', ...)]            <- timer KHÔNG tự bắn
   [notify] SLA vượt hạn - leo thang
 t=2.2 sau refresh_timers+do_engine_steps, READY: [('ut_gd_esc', 'giam_doc', 'Giám đốc duyệt (leo thang)')]
 completed: True
```

**Timer**: không có thread/scheduler nào trong thư viện. Timer chỉ được đánh giá khi gọi `wf.refresh_timers()` (`refresh_waiting_tasks()` đã deprecated, chỉ gọi lại hàm trên) rồi `do_engine_steps()`. Thiết kế đã chạy thử (t5): sau mỗi lần lưu, ghi `next_due = min(e.value for e in wf.waiting_events())` (ISO timestamp tuyệt đối, ví dụ `2026-10-15T03:00:39+00:00`) vào cột `timestamptz`; worker định kỳ `select id from proc_instance where not done and next_due <= now()` (nên `for update skip locked`), nạp, `refresh_timers()`, `do_engine_steps()`, lưu. Output thật: `poller tìm thấy instance quá hạn: [(3,)]` rồi READY = `Trưởng phòng duyệt (leo thang)` lane `giam_doc`.

Lưu ý nghiệp vụ: gateway không kiểm tra quyết định. Nếu user task không có nhánh `reject`, giá trị `reject` vẫn đi tiếp. Compiler (mục 6) tự chèn cổng `<step>_decision == 'reject'` -> `end_rejected`. Quyền "ai được complete task" phải do xT kiểm (lane chỉ là nhãn).

## 3. Persist Postgres JSONB (`t3_persist.py`, `xtflow.to_state/from_state`)

`BpmnWorkflowSerializer(BpmnWorkflowSerializer.configure(spiff.serializer.DEFAULT_CONFIG))`, `serialize_json(wf)`. Script engine **không** được serialize: sau khi nạp phải gán lại `wf.script_engine = <engine an toàn>`.

```
size bytes: 13021 | spec: 7568 | tasks: 13 (5142 B) | instance không spec: 5419
pg jsonb: pg_column_size = 3708 B (TOAST nén), text = 12900 B
```
Spec nhúng chiếm ~58%: nên lưu spec 1 lần theo (key, version) và nhét lại khi nạp nếu cần tiết kiệm (chưa làm; kích thước 13 KB/instance là chấp nhận được).

**BẪY QUAN TRỌNG (phát hiện thật)**: 3.x lưu data của task dạng *delta so với task cha* và khi deserialize phụ thuộc **thứ tự key của dict `tasks`**. JSONB sắp lại key (theo độ dài rồi bảng chữ cái) nên sau reload mọi biến mất sạch:
```
WorkflowTaskException: Error evaluating expression 'amount > 10000000'.  Did you mean 'tp_decision'?
```
Cách vá (đã chạy): trước `deserialize_json`, sắp `tasks` (và `subprocesses[*].tasks`) theo BFS từ `root` (cha trước con) -> `_reorder()` trong `xtflow.py`. Hoặc lưu cột `json`/`text` thay vì `jsonb`. Sau vá:
```
--- load   : reloaded READY: [('ut_tp','truong_phong')] ; READY: ['ut_gd']
--- load2  : completed: True {'amount': 50000000, ... 'ghiso_result': {...}}      (mỗi bước là 1 process Python mới)
--- timer_save / (đợi 2.5s) / timer_load: timer sau reload ở process mới -> READY: ['ut_gd_esc']
```
Timer treo sống sót qua serialize (lưu thời điểm tuyệt đối).

## 4. Bảo mật script engine (`t4_security.py`) — phát hiện lớn nhất

Mặc định `PythonScriptEngine` = `TaskDataEnvironment` dùng `eval`/`exec` thẳng. Marker file `PWNED` do payload `touch` tạo ra:

```
### DEFAULT PythonScriptEngine
  !!! PWNED | gateway: os.system                 | ĐÃ CHẠY
  !!! PWNED | gateway: object-graph escape (Popen) | ĐÃ CHẠY   (file xuất hiện trễ, ghi nhận ở dòng kế)
  !!! PWNED | scriptTask: os.system              | ĐÃ CHẠY
  !!! PWNED | preScript (userTask ext)           | ĐÃ CHẠY
  !!! PWNED | timer expr: __import__             | ĐÃ CHẠY
```
(Dòng "dunder attr" bị đánh PWNED là hiệu ứng trễ: payload "object-graph escape" ngay trước đó dùng `Popen` bất đồng bộ nên file `touch` xuất hiện sau khi kiểm tra; nghĩa là thoát qua `().__class__.__base__.__subclasses__()` cũng khai thác được dù không dùng `import`. Bản bảng đã sửa: `os.system`, object-graph, scriptTask, preScript, timer đều thực thi.)

Như vậy **mọi chỗ có biểu thức Python đều là điểm RCE**: điều kiện gateway/sequence flow, scriptTask, `spiffworkflow:preScript/postScript`, tham số serviceTask, biểu thức timer. Tắt riêng scriptTask là không đủ.

Cấu hình an toàn (đã chạy, `xtflow.py`): subclass `BasePythonScriptEngineEnvironment` + `PythonScriptEngine`:
- `evaluate`: `ast.parse(mode="eval")`, duyệt cây, chỉ cho `BoolOp/UnaryOp/BinOp(+,-,/)/Compare/Name/Constant/List/Tuple/Subscript/IfExp`; cấm `Call`, `Attribute`, `Lambda`, comprehension, f-string, `Mult`/`Pow`/`Mod` (chống DoS `[0]*10**9`), tên bắt đầu `_`; `eval` với `{"__builtins__": {}}`.
- `execute`: luôn raise -> scriptTask/preScript/postScript chết.
- `call_service`: tra `services[operation_name]` (allowlist Python callable do xT đăng ký), không có -> lỗi; trả `json.dumps(result)` (Spiff yêu cầu chuỗi JSON).
- Subclass `call_service` của engine vì **bug 3.2.0**: nhánh lỗi gốc tham chiếu biến `script` không tồn tại -> `NameError: name 'script' is not defined` che mất lỗi thật.

```
### SAFE engine
  safe | gateway: os.system / object-graph / dunder / lambda / fstring / DoS list*1e9 | CHẶN (WorkflowTaskException)
  safe | gateway: legit amount>10tr           | ĐÃ CHẠY
  safe | scriptTask, preScript                | CHẶN: UnsafeExpression: script execution ... is disabled
  safe | timer expr: __import__               | CHẶN
  safe | serviceTask: op chưa đăng ký (rm_rf) | CHẶN: UnsafeExpression: service operation 'rm_rf' not registered
  safe | serviceTask: op đã đăng ký (echo)    | ĐÃ CHẠY
  ls PWNED -> No such file
```
Phòng thủ nhiều lớp đề xuất: (a) agent không bao giờ nộp XML thô, chỉ spec -> compiler; (b) compiler gọi `check_expr` cho mọi `when/params`; (c) engine whitelist là lớp cuối kể cả khi ai đó upload BPMN thủ công; (d) khi upload BPMN ngoài phải quét từ chối `scriptTask`/`preScript`/`postScript` và `check_expr` cho mọi `conditionExpression`/`timeDuration`; (e) giới hạn thời gian/bộ nhớ cho worker. Rủi ro còn lại: so sánh chuỗi/list lớn lấy từ data (DoS nhẹ) — chưa giải quyết.

## 5. Versioning (`t5_versioning.py`)

Bảng `proc_def(key, version, bpmn_xml, sha256)` bất biến + `proc_instance(key, version, state jsonb, next_due, done)`. Kết quả thật:
```
deploy v2 (ngưỡng 20tr + bước kế toán); instance B (v2) tạo, amount 15tr
proc_def: [('tamung',1,4277,'5deb7bfdf4'), ('tamung',2,4888,'2af62a3457')]
A hoàn tất (process mới, reload từ PG): (1, ['Trưởng phòng duyệt', 'Giám đốc duyệt'], True)   # A tạo trước v2: 15tr > 10tr vẫn cần GĐ
B hoàn tất: (2, ['Trưởng phòng duyệt', 'Kế toán kiểm tra'], True)                              # 15tr < 20tr, có bước kế toán
```
Cùng `process id` `tamung` ở 2 version không xung đột vì state tự mang spec nhúng. Hệ quả: instance cũ không bị ảnh hưởng khi deploy v2, nhưng cũng không tự nâng cấp; migrate instance đang chạy sang version mới là việc tự làm (không có sẵn).

## 6. Compiler spec -> BPMN (`compiler.py`, `t6_compile.py`)

Spec YAML (431 B) do agent đề xuất:
```yaml
id: tamung
roles: [truong_phong, giam_doc]
steps:
  - {id: tp, type: approve, name: Trưởng phòng duyệt, role: truong_phong, sla: {after: PT2S, escalate_to: giam_doc}}
  - {id: gd, type: approve, name: Giám đốc duyệt, role: giam_doc, when: "amount > 10000000"}
  - {id: ghiso, type: service, name: Ghi sổ, op: ghi_so, params: {amount: amount, requester: requester}}
```
Compiler (~110 dòng) phát: start, userTask + lane theo role, gateway `when`, gateway quyết định (reject -> `end_rejected`), boundary timer + service notify + userTask leo thang, service task theo allowlist, tự chèn merge gateway cho node nhiều đầu vào. Chạy BPMN sinh ra:
```
5tr, approve    : ['Trưởng phòng duyệt@truong_phong'] -> APPROVED/ghi sổ
50tr, approve   : ['Trưởng phòng duyệt@truong_phong', 'Giám đốc duyệt@giam_doc'] -> APPROVED/ghi sổ
50tr, GĐ reject : [..., 'Giám đốc duyệt@giam_doc'] -> REJECTED (không ghi sổ)
5tr, TP reject  : -> REJECTED
5tr, SLA quá hạn: ['Trưởng phòng duyệt (leo thang)@giam_doc'] -> APPROVED/ghi sổ ; [notify] SLA vượt hạn: Trưởng phòng duyệt
LEDGER [5000000, 50000000, 5000000]
```
Spec độc/sai bị chặn lúc compile (feedback rõ ràng cho agent tự sửa):
```
role lạ            : SpecError: step tp: role 'hacker' not in roles
op ngoài allowlist : SpecError: step ghiso: op 'os_system' not in allowlist ['ghi_so', 'notify']
when độc           : UnsafeExpression: node Call not allowed in: "__import__('os').system('id')"
SLA không phải ISO : SpecError: bad ISO duration
param service độc  : UnsafeExpression: node Call not allowed in: "open('/etc/passwd').read()"
type script        : SpecError: unsupported type 'script'
```
Giới hạn: mới có tuần tự + điều kiện bỏ qua bước + SLA + từ chối; chưa có song song, vòng lặp, subprocess, message/signal (SpiffWorkflow hỗ trợ, compiler cần mở rộng). Compiler không sinh phần hình học (BPMNDI) nên không mở được trong designer (Camunda Modeler) cho đến khi thêm auto-layout; engine không cần.

## 7. So sánh với state machine thuần Postgres (không chạy thử, đánh giá thiết kế)

| | SpiffWorkflow (BPMN) | Postgres state machine (`transitions`/`python-statemachine`, bảng `task`) |
|---|---|---|
| Định nghĩa là dữ liệu | Có, chuẩn BPMN 2.0 (XML), công cụ ngoài đọc được | Có nếu tự định nghĩa JSON; không chuẩn |
| Chữ "BPMN" của đề OLP | **Đáp ứng trực tiếp**, có thể xuất sơ đồ cho ban giám khảo | Không; phải giải thích "tương đương BPMN" |
| Timer/SLA, song song, subprocess, message | Có sẵn (cần poller) | Tự viết toàn bộ |
| Rủi ro bảo mật | Script engine = RCE nếu để mặc định (đã vá bằng whitelist) | Thấp, ít bề mặt |
| Persist | Tự lo + bẫy JSONB; 13 KB/instance | Hàng nhỏ, query/BI/RLS trực tiếp trên bảng |
| Công sức / độ phức tạp | Trung bình; thêm compiler, poller, engine an toàn | Thấp cho luồng tuần tự; tăng nhanh khi có rẽ nhánh/SLA/song song |
| Giấy phép | LGPL-3.0 (import làm thư viện, ổn với Apache-2.0; sửa lib phải công bố) | MIT/LGPL nhẹ hoặc không phụ thuộc |

BPMN đáng giá khi: đề chấm điểm chữ "BPMN"/chuẩn mở; process có >2-3 nhánh, SLA/leo thang, song song, quy trình do agent sinh và cần hiển thị cho người duyệt; cần giao tiếp với hệ khác qua chuẩn. State machine đủ khi: vòng đời đối tượng đơn giản (trạng thái hồ sơ, ticket) không cần model hóa quy trình. Đề xuất cho xT: **SpiffWorkflow cho workflow lõi (đề nghị/phê duyệt, thu–chi, văn bản, helpdesk SLA)**; vòng đời đơn giản (trạng thái thực thể) có thể là cột `status` + bảng chuyển trạng thái, không cần BPMN.

## Verdict

**Chốt dùng SpiffWorkflow 3.2 làm engine nhúng**, kèm việc phải làm (đã có mã mẫu trong spike): 
1. `SafeEnvironment` làm mặc định, test regression chặn RCE (t4) chạy trong CI.
2. `from_state/to_state` với `_reorder` (hoặc cột `json`) + test reload bắt buộc.
3. Worker poller dựa trên `next_due` + `for update skip locked`; lock theo instance khi complete task (tránh hai người duyệt cùng lúc).
4. Compiler spec -> BPMN là cổng duy nhất agent đi qua; mở rộng thêm loại bước khi cần.
5. Kiểm quyền người duyệt theo lane/role ở lớp MCP; ghi audit mỗi lần `complete`.
6. Theo dõi bug `call_service` (`script` chưa định nghĩa) khi nâng version; ghi chú LGPL: không sửa trực tiếp mã SpiffWorkflow.
Phương án B (Flowable REST) giữ nguyên như đã ghi ở `building-blocks.md` nếu sau này cần.
