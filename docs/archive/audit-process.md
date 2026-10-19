<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Audit process engine (port production từ spike workflow v2)

Ngày: 2026-10-15. Người kiểm: Opus 5.5 (tester/verifier). Chỉ đọc và chạy, không sửa `src/`.
Phạm vi: `src/xt/domain/process/`, `src/xt/application/process/`, `src/xt/adapters/workflow/bpmn/`, `src/xt/adapters/db/process_timers.py`, `tests/unit/process/`, `tests/integration/process/`, `docs/process-engine.md`, `docs/schema/process-spec-v2.schema.json`.
Bộ tấn công gốc `/tmp/xt-wf2-review/` không còn, nên đã dựng lại theo `research/spike-workflow-v2.md` §7 và `research/verification-spikes.md` §A, chạy trên API đã port. Script tấn công nằm ở `/tmp/xt-audit-proc/` (ngoài repo).
Môi trường: `.venv` của repo (Python 3.13, SpiffWorkflow 3.2.0, lxml 6.1.3). Postgres `postgres:17-alpine` tạm ở `127.0.0.1:20210` (container `xt-audit-process-pg`), đã xóa sau khi chạy. Node 24.14.1, bpmn-moddle 9.0.4 lấy từ `~/.cache/xt/bpmn-viewer`.

## 1. Kết luận

**Verdict: PASS có điều kiện.** Port giữ đúng spike. Không tìm thấy đường RCE, XXE hay chèn XML. 3 lỗi High cũ (H1-H3) và M1-M4 đã được sửa thật. Còn **3 lỗi Med** mới (AUD-1..3) và **6 lỗi Low** (AUD-4..9), tất cả đều fail closed (không ai vượt quyền được) nhưng có thể làm hỏng SLA hoặc làm instance kẹt. Nên sửa AUD-1..3 trước khi bật SLA và cho agent tự viết spec ở production.

Test hồi quy: `tests/unit/process/audit/test_audit_process.py` gồm 10 test `xfail(strict=True)`. Đã chạy `--runxfail` để xác nhận cả 10 đều fail đúng vì lỗi đã mô tả. Bộ test hiện có: `tests/unit/process` cho **184 passed, 10 xfailed**.

## 2. Bảng PASS/FAIL

| # | Hạng mục | Kết quả | Bằng chứng |
|---|---|---|---|
| 1 | Port đúng spike | PASS | Cả 120 tên test của spike đều có ở bản port (118 unit + 2 integration), thêm 1 test integration mới và các test unit mới. `tests/unit/process` 184 passed. Ngữ nghĩa giữ nguyên ở `_exclude`, `_distinct_map`, `MAX_LOOPS_PER_STEP`, `editors`, `reorder_tasks`, kiểm `load` |
| 2 | SpiffWorkflow không bị sửa/vendor | PASS | So hash từ `RECORD` của `spiffworkflow-3.2.0.dist-info`: **181/181 file khớp, 0 file bị sửa**. Không có bản sao trong `src/`. Chỉ import + subclass (`PythonScriptEngine`, `BasePythonScriptEngineEnvironment`). Bản trong `uv.lock` là wheel PyPI 3.2.0 kèm sha256 |
| 3 | SafeEngine: 33 payload (VS A.1) | PASS 33/33 | Chặn dunder/format/f-string/lambda/comprehension/walrus/starred/Pow/Mult/Call/`__builtins__`/homoglyph Cyrillic. Thêm `＿＿builtins＿＿` (gạch dưới full-width, NFKC thành `__builtins__`) cũng bị chặn. Không tạo được file PWNED. Kiểm thêm: ngoặc lồng 100 tầng vẫn chạy (an toàn), 600 tầng bị trần 500 ký tự chặn; 20k số hạng, list 200 phần tử, `not` lồng 9 tầng, `@ % // << ~` đều bị chặn; `1/0` và slice step 0 ném lỗi, runtime khôi phục state |
| 4 | XXE / entity expansion | PASS (kèm AUD-6 Low) | `file://` XXE, billion laughs, parameter entity, `<!doctype>` viết thường: bị từ chối. XInclude không được xử lý (không lộ file). UTF-16 và UTF-32 vượt được bước kiểm DOCTYPE theo byte, nhưng parser cứng vẫn không resolve entity (xem AUD-6) |
| 5 | Chèn qua tên vào compiler | PASS (kèm AUD-7 Low) | Tên process/bước chứa `"/><bpmn:scriptTask>`, `]]>`, `<!--`: được `quoteattr`, cây parse lại không có `scriptTask`. Role, id, op, khóa param, dept literal, duration đều bị regex chặn và trả `SpecError`. Param value hoặc `when` có thẻ XML được escape. RLO bị `clean_text` chặn. U+FFFE/U+FFFF lọt xuống tới parser (AUD-7) |
| 6 | H1 `role(x) in 'KT'` | PASS | Dạng chuỗi và dạng object đều chỉ ra `NV-0002/3/4` (thuộc KT). `fallback_assignee: role(director) in 'BGD'` bị từ chối vì không tĩnh |
| 7 | H2 same_step bỏ qua `when` phía trước | PASS | Có input `editable_on_revision` mà dùng `same_step` thì bị `SpecError`. Không có input sửa được thì `same_step` từ chối mọi `updates` (`amount`, `note`, `kind`). `restart` 8 tr thành 80 tr thì định tuyến lại đủ `dept_head, kttt, ktt, director` |
| 8 | H3 `distinct_from` bất đối xứng ở nhánh song song | PASS | Chạy 4 tổ hợp (khai ở a/b × làm a/b trước), có reload JSONB giữa các bước: người thứ hai luôn bị `access_denied`, admin gán lại cũng bị chặn. Biến thể SLA: nhánh a leo thang tới đúng người đã làm b thì người đó bị loại, task đi tiếp `process_fallback` NV-0001 |
| 9 | Người dùng đã nghỉ | PASS về an toàn, Low về khả năng chạy tiếp (AUD-8, AUD-9) | Chặn khi: requester nghỉ lúc start, actor nghỉ lúc act, ủy quyền/hỏi người đã nghỉ, admin đã nghỉ gán lại, override cho người nghỉ sau khi được ủy quyền. Nhưng task không được giải lại người nhận (AUD-8) |
| 10 | Tự duyệt qua fallback | PASS | NV-0005 tự đề nghị: bị SoD loại, đi fallback NV-0001. NV-0001 nghỉ: `unresolved`, cả NV-0005 lẫn NV-0001 đều không act được, admin gán cho requester bị chặn (`sod`), ủy quyền cho requester bị chặn (`sod`) |
| 11 | Ủy quyền qua lại vs SLA | PASS kèm ghi chú | Dừng đúng sau 5 lần ủy quyền. Với SLA 24h, ủy quyền qua lại mỗi 23h kéo được 115h mà không leo thang (đã ghi ở §8 spike). Lỗ thật nằm ở `request_info` (AUD-2) |
| 12 | Timer start: 8 poller đồng thời, Postgres 17 thật | PASS | 5 lần lặp × 8 thread có barrier, cùng `now`: luôn ra 7 `already` + 1 `fired`, 1 `wf_instance`, 1 dòng kỳ có `instance_id`. 8 worker × 3 tuần × `now` lệch ngẫu nhiên 0-600 s: đúng 3 kỳ, 3 instance. 3 lần `start` đầu ném lỗi: rollback, worker sau giành lại kỳ, cuối cùng 1 `fired` và 1 instance |
| 13 | Join song song ghi đè giá trị cũ | PASS | Vòng `to_step` đi qua parallel: vòng 2 làm b trước, a hỏi thêm thông tin rồi mới duyệt, reload JSONB giữa chừng. Sau join `a_comment='r2-a'`, `b_comment='r2-b'`, `b_actor='NV-0014'`, không còn giá trị vòng 1. `when` sau join chạy đúng |
| 14 | Outcome lạ bị chặn (fail closed) | PASS | `Approve`, `approve `, `return_for_info`, `None`, list, `1`, `True`, outcome của loại task khác: đều `DecisionError`, state giữ nguyên từng byte. Bỏ qua runtime và ghi thẳng vào engine `bogus/None/APPROVE` thì ra `No conditions satisfied`; không ghi decision thì ra `NameError` |
| 15 | `load()` gắn với spec sha | PARTIAL (AUD-4, AUD-5 Low) | Sửa `spec_sha256`/`version`/`process_id` trong meta thì bị từ chối. Nhưng phần `wf` không được đối chiếu với BPMN đã compile |
| 16 | bpmn-js không phải phụ thuộc bắt buộc | PASS (cần dọn theo quyết định của chủ dự án, §4) | Không có trong `pyproject.toml`/`uv.lock`. Chỉ nằm trong `tests/integration/process/bpmn_viewer/package.json`, test tự skip nếu thiếu |

## 3. Lỗi tìm được

### AUD-1 (Med): tên bước dài làm SLA không bao giờ leo thang
- **Nguyên nhân:** `compiler.py` sinh tham số `msg = repr(f"SLA vượt hạn: {st.name}")`. `Text` cho phép tên tới 200 ký tự, nhưng `check_expr` giới hạn hằng chuỗi 200 ký tự. Vì vậy tên dài hơn 186 ký tự cho ra hằng dài hơn 200. Compile vẫn qua vì compiler chỉ `check_expr` điều kiện của flow, không kiểm tham số service do chính nó sinh.
- **Tái hiện:** `de_xuat_phe_duyet` có `steps[1].name = "D"*195`, start, tua 25h, `tick()` → `EngineError: ... hằng chuỗi dài quá 200 ký tự`. State được khôi phục, task không bao giờ leo thang. `next_due` vẫn nằm trong quá khứ nên worker sẽ đánh thức lại liên tục.
- **Test:** `test_aud1_sla_escalation_works_with_long_step_name`.
- **Sửa:** cắt tên trong `msg` (ví dụ `st.name[:150]`) hoặc truyền tên qua biến. Đồng thời `check_expr` mọi `params` của node service do compiler sinh, ngay lúc compile.

### AUD-2 (Med): `request_info` làm treo SLA vô hạn
- **Nguyên nhân:** boundary SLA chỉ gắn vào `ut_X`. Khi người duyệt hỏi thêm, `ut_X` bị đóng, task `ri_ut_X` lại không có SLA. `next_due()` trả `None`.
- **Tái hiện:** NV-0005 `request_info` hỏi NV-0007, tua 30 ngày, `tick()`: inbox chỉ còn `ri_ut_dept_head`, không có `_esc`. Người duyệt có thể né SLA 24h bằng cách hỏi một người không bao giờ trả lời. Trần 5 lần không giúp gì vì chỉ cần hỏi 1 lần.
- **Test:** `test_aud2_request_info_does_not_freeze_sla`.
- **Sửa:** chọn một trong hai. (a) Gắn boundary SLA (dùng `sla.after` của bước) vào `ri_ut_X`, leo thang về `escalate_to`. (b) Tính hạn SLA một lần cho cả bước (lưu `deadline` trong meta lúc vào bước lần đầu) và không khởi động lại khi delegate/request_info. Cách (b) xử lý luôn việc ủy quyền qua lại kéo SLA ×6. Cập nhật §8 của tài liệu.

### AUD-3 (Med): input tùy chọn dùng trong `when`/`params` làm instance kẹt vĩnh viễn
- **Nguyên nhân:** `_CrossCheck._walk` coi mọi input, kể cả `required: false`, là biến luôn có sẵn. Runtime lại không gán `None` cho input tùy chọn bị bỏ trống, nên lúc đánh giá biểu thức bị `NameError`.
- **Tái hiện:** spec có `inputs.note {required:false}`, bước b `when: "note == 'x'"`. Start không có `note`, NV-0001 duyệt bước a → `EngineError ... NameError 'note'`, state được khôi phục. Lần duyệt nào cũng lỗi như vậy, chỉ còn cách sửa DB.
- **Test:** `test_aud3_optional_input_in_when_does_not_wedge_instance`.
- **Sửa:** `validate_inputs(partial=False)` gán `None` cho input tùy chọn bị thiếu (khóa nào cũng có mặt). Hoặc validator từ chối dùng input tùy chọn trong biểu thức.

### AUD-4 (Low): `load()` không gắn `wf` với BPMN đã compile
- `SpiffProcessEngine.load` bỏ qua tham số `compiled`, vì state của SpiffWorkflow mang theo spec riêng. Lấy meta đúng của `task_normal` ghép với `wf` của `proposal_approval` thì vẫn nạp được (READY `ut_dept_head`). Cùng spec nhưng meta v9 ghép `wf` v2 cũng nạp được.
- Kẻ tấn công phải ghi được DB mới khai thác được. Rủi ro thật nằm ở migration: nếu sau này compiler đổi BPMN, instance cũ sẽ chạy BPMN cũ với manifest mới.
- **Test:** `test_aud4_...`.
- **Sửa:** lưu `bpmn_sha256` vào manifest/meta. Lúc `load`, so sánh với hash của spec trong `state["wf"]["spec"]`, hoặc so tập `task_specs` với spec parse từ `compiled.bpmn`.

### AUD-5 (Low): `ProcessService` tin `meta.id`
- Dòng `A` mang state của `B` thì `svc.get("A")` trả về instance `B`, và `act` sẽ lưu đè vào `A`.
- **Test:** `test_aud5_...`.
- **Sửa:** trong `_load`/`get`, kiểm `inst.id == instance_id`.

### AUD-6 (Low): kiểm DOCTYPE theo byte bị vượt bằng UTF-16/UTF-32
- `b"<!DOCTYPE" in raw.upper()` không bắt được khi XML được mã hóa UTF-16/UTF-32. `parse_bpmn` vẫn nhận DOCTYPE có internal entity. Entity không được mở rộng (`resolve_entities=False`), external entity và billion laughs vẫn bị libxml2 chặn, nên chưa có tác động thật. Hiện chỉ có XML do compiler sinh (UTF-8) đi qua đây.
- **Test:** `test_aud6_...`.
- **Sửa:** sau khi parse, từ chối nếu `root.getroottree().docinfo.internalDTD is not None` hoặc `docinfo.doctype` khác rỗng. Hoặc bắt buộc UTF-8 (từ chối BOM và khai `encoding` khác UTF-8).

### AUD-7 (Low): U+FFFE/U+FFFF trong tên không ra `SpecError`
- `clean_text` chỉ cấm Cc/Cf/Cs/Co, bỏ sót noncharacter (Cn) không hợp lệ trong XML 1.0. Compile chết ở `parse_bpmn` với `UnsafeExpression` (`invalid_request`) thay vì `SpecError` (`rule_violation`), và thông báo không chỉ ra trường bị sai. Vẫn fail closed.
- **Test:** `test_aud7_...[￾, ￿]`.
- **Sửa:** cấm ký tự ngoài `Char` của XML 1.0 (`￾`, `￿`, noncharacter) trong `clean_text`. `compile` bọc lỗi parse thành `SpecError`.

### AUD-8 (Low): người nhận duy nhất nghỉ việc thì task kẹt, không có sự kiện
- Ứng viên được giải một lần lúc task READY. NV-0003 (người duy nhất của `kttt`) nghỉ thì bị chặn act, đúng. Nhưng `tick()` hay `load()` không giải lại, không có `assignment.unresolved`. Inbox vẫn hiện `['NV-0003']`. Tương tự với override (người được ủy quyền nghỉ sau đó).
- **Test:** `test_aud8_...`.
- **Sửa:** trong `refresh_assignments`/`tick`, giải lại task có toàn bộ ứng viên đã nghỉ (đi tiếp chuỗi fallback) và phát `assignment.unresolved`/`escalated`.

### AUD-9 (Low): bước sửa `unresolved` không ai rút được
- Requester nghỉ thì bước sửa thành `unresolved` với thông báo "chờ quản trị (chỉ được rút)". Nhưng `reassign` cấm bước sửa (M3, đúng), `act` lại đòi actor nằm trong `candidates`, và runtime không có API hủy. Admin NV-0012 gọi `withdraw` bị `access_denied`. Instance kẹt vĩnh viễn.
- **Test:** `test_aud9_...`.
- **Sửa:** cho người có `admin_role` act `withdraw` (không được `resubmit`) trên bước sửa `unresolved`, ghi vào history. Hoặc thêm `cancel(admin, reason)` ở runtime/service.

### Ghi chú (Info, không có test)
- **I-1:** `resume: restart` chạy lại cả service đứng trước điểm trả lại. Thử chèn `post_to_ledger` lên đầu `de_xuat_phe_duyet`, trả lại rồi nộp lại: sổ bị ghi 2 lần với 2 `idempotency_key` khác nhau, vì `task.id` mới ở mỗi vòng. Nếu muốn chặn trùng thì đổi key thành `(instance, step, số vòng)` hoặc `(instance, step)` cho op không được lặp. Ít nhất cần ghi vào tài liệu: không đặt service có tác dụng phụ trước bước duyệt có `restart`.
- **I-2:** `tests/integration/process/conftest.py` cố định cổng 19900 và tên `xt-it-process-pg`, rồi gọi `docker rm -f` lúc bắt đầu. Hai lượt chạy song song (nhiều agent) sẽ xóa container của nhau. Lúc audit đang có một container tên này chạy, nên audit không chạy test đó mà dùng Postgres riêng ở 20210. Nên thêm hậu tố ngẫu nhiên và cho chọn cổng qua biến môi trường.
- **I-3:** poller ghi `wf_instance(id, process_key, version, state)` nhưng không có `status/candidates/next_due` như `InstanceRecord`. Instance tạo bằng timer chưa hiện trong `ProcessService.inbox` cho tới khi lớp tích hợp gộp hai bảng.
- **I-4:** `rt.start` trong poller gọi `on_event`/`scheduler.wake_at` ngay trong transaction DB, trước khi commit. Nếu transaction rollback sau đó thì sự kiện hoặc lịch hẹn trỏ tới instance không tồn tại. Nên gửi sau khi commit (outbox).
- `expr.evaluate` đặt `__builtins__` trước rồi mới `env.update(context)`. Hiện không có đường nào đưa khóa `__builtins__` lên mức đỉnh của task data (input theo regex, kết quả service nằm dưới `{id}_result`), nhưng nên gán builtins sau cùng để phòng thủ nhiều lớp.

## 4. bpmn-js: hiện trạng và việc phải đổi (chủ dự án quyết định bỏ, dùng bpmn-moddle)

Hiện trạng: bpmn-js **không** phải phụ thuộc của sản phẩm, nhưng vẫn được khai trong công cụ test và tài liệu. Đã chạy thử bản kiểm chỉ dùng bpmn-moddle (`/tmp/xt-audit-proc/moddle_check.mjs`, không cần Chrome). Bản này kiểm: cảnh báo/tham chiếu không giải được của moddle, mọi flow node có `BPMNShape` có bounds, mọi `sequenceFlow` có `BPMNEdge` với ít nhất 2 waypoint, mỗi node thuộc đúng 1 lane. Kết quả: 6 pack cho **0 lỗi**. Mẫu cố ý làm hỏng (bỏ shape `ut_ktt`, đổi một `bpmnElement`) bị bắt đủ 4 lỗi. Các gói kéo theo của bpmn-moddle (moddle, moddle-xml, saxen, min-dash, ids) đều MIT.

Việc phải đổi:
1. `tests/integration/process/bpmn_viewer/package.json`: bỏ `bpmn-js` và `playwright-core`, chỉ giữ `bpmn-moddle` 9.0.4. Sinh lại `package-lock.json`. Sửa `description`.
2. `tests/integration/process/bpmn_viewer/check.mjs`: thay bằng bản kiểm cấu trúc chỉ dùng bpmn-moddle như trên, không dùng Chrome/`page.evaluate`.
3. `tests/integration/process/test_process_integration.py`: đổi `test_bpmn_opens_in_bpmn_js_without_warnings` thành kiểm cấu trúc DI bằng bpmn-moddle. Bỏ điều kiện Chrome, skip theo `node_modules/bpmn-moddle`. Sửa docstring đầu file.
4. `docs/process-engine.md` dòng 95 ("mở được trong bpmn-js") và 180 (render bpmn-js, lưu ý giấy phép): đổi sang bpmn-moddle.
5. `THIRD_PARTY_NOTICES.md` dòng 83-84: bỏ dòng bpmn-js, bỏ `playwright-core` khỏi dòng bpmn-moddle, liệt kê các gói MIT kéo theo.
6. `CHANGELOG.md` dòng 14-15: đổi "render bpmn-js" và ghi chú giấy phép bpmn-js thành bpmn-moddle.
7. Không cần sửa `src/`. `tests/unit/process/test_bpmn_di.py` (kiểm DI bằng lxml) giữ nguyên. Tài liệu lịch sử ở `research/` để nguyên.

## 5. Chạy lại

```bash
cd /home/chinhan/xT
.venv/bin/python -m pytest -q --no-cov -o addopts="" tests/unit/process            # 184 passed, 10 xfailed
.venv/bin/python -m pytest -q --no-cov -o addopts="" --runxfail tests/unit/process/audit   # 10 failed = lỗi còn mở
```
Script tấn công (ngoài repo): `/tmp/xt-audit-proc/{a1_expr_xml.py,test_a2.py,test_a3.py,test_a4.py,test_pg.py,utf16.py,moddle_check.mjs,verify_record.py}`. Riêng `test_pg.py` cần Postgres ở 127.0.0.1:20210 và file mật khẩu `/tmp/xt-audit-proc/pgpw`; cả hai đã được xóa sau audit.

## 6. Phản hồi tác giả (2026-10-15)

Đã sửa cả 9 lỗi; 10 test hồi quy bỏ `xfail` và đều pass, thêm 3 test (`test_aud2_delegate_and_info_keep_original_deadline`, `test_aud3_optional_input_with_default_works_and_missing_default_rejected`, `test_aud3_engine_failure_is_recorded_as_admin_visible_incident`). `tests/unit/process`: 197 passed; `tests/integration/process`: 3 passed.

| # | Cách sửa |
|---|---|
| AUD-1 | Tên bước trong `msg` cắt 150 ký tự; compiler `check_expr` mọi tham số service do chính nó sinh, lỗi -> `SpecError`. Không có khóa timer/job nào lấy từ tên: id bước/quy trình đã bị regex giới hạn 41 ký tự |
| AUD-2 | Theo cách (b): service nội bộ `slas_X` (op `xt_sla_deadline`) ghi hạn tuyệt đối `xt_sla_due_X` một lần khi vào bước; boundary `timeDate` trên `ut_X` và cả `ri_ut_X`. Ủy quyền/hỏi thêm không đặt lại hạn, chờ bổ sung không dừng đồng hồ. Op tiền tố `xt_` bị cấm trong spec |
| AUD-3 | Cả hai: `InputSpec.default` + validator bắt buộc `default` cho input tùy chọn dùng trong biểu thức; runtime luôn gán mọi input (default hoặc `None`). Ngoài ra lỗi engine lúc `act`/`tick` ghi `meta.incidents` + sự kiện `engine.error`, `ProcessService` lưu lại rồi ném `EngineError` (quản trị thấy, không mất dấu) |
| AUD-4 | `SpiffProcessEngine.load` so digest của `spec` + `subprocess_specs` trong state với spec sinh từ `compiled.bpmn` (cache theo sha BPMN); lệch -> `EngineError(reason="spec_mismatch")` |
| AUD-5 | `ProcessService._load`/`get` kiểm `inst.id == instance_id`; `inbox` bỏ qua dòng hỏng thay vì hỏng cả inbox |
| AUD-6 | Sau khi parse, từ chối nếu `docinfo.doctype`/`internalDTD`/`externalDTD` có mặt (độc lập mã hóa); giữ kiểm theo byte làm lớp đầu |
| AUD-7 | `clean_text` cấm noncharacter (U+FDD0-U+FDEF, mọi U+xFFFE/U+xFFFF); `compile` bọc lỗi parse BPMN thành `SpecError` |
| AUD-8 | `refresh_assignments` (gọi từ `act`/`tick`) giải lại task mà mọi ứng viên đã nghỉ, ghi trail và phát sự kiện `assignment.escalated`/`unresolved` |
| AUD-9 | Người có `admin_role` được `withdraw` (bắt buộc lý do, không được `resubmit`) bước sửa `unresolved` |
| I-1 | Ghi vào `docs/process-engine.md` (không đặt service có tác dụng phụ trước bước duyệt `restart`); giữ khóa `(instance, task)` |
| I-2 | Container `xt-it-process-pg-<ngẫu nhiên>`, cổng qua `XT_IT_PROCESS_PG_PORT`, không còn `docker rm -f` tên cố định |
| I-3, I-4 | Không đổi trong module này: việc gộp `wf_instance` với `InstanceRecord` và gửi sự kiện/hẹn timer sau commit thuộc lớp tích hợp, đã ghi ở `docs/integration-notes.md` |
| Ghi chú builtins | `expr.evaluate` gán `__builtins__` sau cùng |
| bpmn-js | Đã bỏ theo quyết định chủ dự án: `bpmn_viewer` chỉ còn bpmn-moddle 9.0.4 (5 gói MIT), `check.mjs` kiểm cấu trúc như §4, test tích hợp có thêm ca cố ý làm hỏng DI; `~/.cache/xt/bpmn-viewer` đã xóa |
