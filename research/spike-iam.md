# Spike: Chọn engine IAM cho xT (pycasbin vs vakt vs evaluator tối giản tự viết)

Ngày: 2026-10-15. Codex (gpt-6.1-sol) chạy spike ở `/tmp/xt-spike-iam` nhưng hết quota trước khi kịp viết báo cáo (đã ghi ở `incidents.log`). Opus tiếp quản: chạy lại toàn bộ, rà test, thêm 7 case, sửa 4 lỗi, đo benchmark, so sánh explain và policy-as-data, kiểm tra upstream.
Code và test đã chép về `research/spike-iam/`, mỗi file có header SPDX Apache-2.0. Không có dòng nào chép từ sklite: sklite viết bằng Go, ở đây chỉ học semantics (Deny > Allow, wildcard, TRN) rồi viết lại độc lập bằng Python.
Môi trường: Python 3.13.14, pycasbin 2.8.0, vakt 1.6.0 (jsonpickle 4.1.3), pytest 9.1.1, 12 CPU.

```
cd research/spike-iam
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # 96 passed
.venv/bin/python bench.py 0 200        # benchmark 10k
.venv/bin/python explain_demo.py       # explainability
```

## 0. Kiến trúc của spike (cần hiểu trước khi so engine)

| File | Vai trò |
|---|---|
| `policy_language.py` | Ngôn ngữ policy JSON đóng: `version`, `tenant`, `statements[{id, effect, subjects, actions, resources, conditions}]`. Toán tử chỉ có `eq/ne/le`, field nằm trong whitelist, không `eval`. Wildcard: chỉ `*` là ký tự đặc biệt, so khớp toàn chuỗi, phân biệt hoa thường. Đã sửa lỗi literal prefix/suffix của sklite. |
| `contracts.py` | `Host`: PEP dùng chung cho cả 3 engine. Lớp này lo ranh giới tenant, kiểm tra XRN, schema hàng, mức tự chủ L0–L3, receipt duyệt của người, chặn tự duyệt, phép giao giao (intersection) agent ∩ người được đại diện, ủy quyền khi vắng mặt. `PolicyStore` giữ vòng đời đề xuất → người duyệt, gắn với sha256 của nội dung. |
| `mini.py` | Evaluator tối giản tự viết: khoảng 25 dòng cho `evaluate`, kèm `explain()` trả trace từng predicate. |
| `backends.py` + `model.conf` | Adapter cho pycasbin (RBAC có domain, cộng hàm `star` và `checks` cho ABAC) và vakt (Rule tùy biến). |
| `cases.py`, `test_iam.py` | Ma trận case dùng chung, chạy riêng cho từng engine. |

**Hệ quả quan trọng:** phần lớn logic an toàn (tenant, autonomy, receipt, delegation, self-approval) nằm ở `Host`, không phụ thuộc engine. Engine chỉ quyết định statement nào khớp và gộp hiệu lực Deny > Allow. Vì vậy chọn engine thực chất là chọn theo 4 tiêu chí: đúng, nhanh, giải thích được, lưu được dưới dạng dữ liệu.

## 1. Chạy lại test của codex

```
$ /tmp/xt-spike-iam/.venv/bin/python -m pytest -q -p no:cacheprovider
75 passed in 0.22s
```
Kết quả **75 passed** (25 case × 3 engine) là đúng như codex báo.

### Rà độ phủ 25 case gốc

| Yêu cầu | Case gốc | Đánh giá |
|---|---|---|
| Cô lập đa tenant | 06, 07 | Có: tenant lạ, XRN thừa segment, XRN chứa `*`, actor thuộc tenant khác. **Thiếu:** trường hợp policy document của tenant khác, và trường hợp statement trong tenant A trỏ tới `xrn:other:*`. |
| Deny > Allow | 03 | Chỉ test trên **cùng một subject** (đảo thứ tự). **Thiếu:** Deny gắn role thắng Allow gắn user id; Deny có điều kiện. |
| Wildcard | 04, 05 | Có phản ví dụ literal kiểu sklite (`record.update.extra.record.update`) và hoa thường. **Thiếu:** ký tự regex trong pattern (`.`), `*` vượt segment theo semantics đã chọn. |
| ABAC amount ≤ limit | 08, 09 | Tốt: biên inclusive, None, chuỗi, bool, NaN, inf, thiếu field. |
| Row scope own/team/department | 10, 11, 12 | Đủ. |
| Agent L0–L3 | 13–16 | Đủ. L2/L3 bắt buộc có receipt gắn (actor, action, xrn, revision, hash resource, hạn), và người duyệt phải đang có quyền. |
| Agent tự duyệt | 17, 18, 24 | Đủ, kể cả agent duyệt policy do chính nó đề xuất. |
| Ủy quyền khi vắng | 21, 22, 23 | Đủ cho người nhận là con người. **Thiếu:** người nhận là agent. |
| Schema fail-closed | 25 | Đủ: version lạ, field `condition` lạ, op `eval`, effect lạ, field ngoài whitelist, action lạ. |
| Đa luồng | không có | **Thiếu.** |

Log review bảo mật của codex (`review-security-0.log`) đã nêu 3 lỗi nhưng **chưa sửa và chưa có test**. Tôi probe lại trên code hiện tại và cả 3 vẫn còn. Ngoài ra tôi tìm thêm lỗi thứ 4 (vakt chạy đa luồng).

```
mini   {'F1_deleg_agent_limit': ('implicit_deny', 'delegated_allow'), 'F2_role_namespace': False, 'F3_behalf_swap': True, 'F4_threads_wrong/errs': (0, [])}
casbin {'F1_deleg_agent_limit': ('implicit_deny', 'delegated_allow'), 'F2_role_namespace': True,  'F3_behalf_swap': True, 'F4_threads_wrong/errs': (0, [])}
vakt   {'F1_deleg_agent_limit': ('implicit_deny', 'delegated_allow'), 'F2_role_namespace': False, 'F3_behalf_swap': True, 'F4_threads_wrong/errs': (12, [])}
```

### 7 case bổ sung (26–32)

| Case | Nội dung | Lỗi phát hiện |
|---|---|---|
| 26_deny_cross_subject_and_conditional | Deny `role:staff` thắng Allow `alice`; Deny `department ne sales` chỉ chặn khi điều kiện đúng | không |
| 27_policy_tenant_mismatch | Policy doc tenant `other` nạp cho actor `acme`; statement acme trỏ tới `xrn:other:*` | không |
| 28_wildcard_semantics | `xrn:acme:*` vượt segment (theo thiết kế); `exp.nse` không khớp `expense`; `record.*` không khớp `recordXupdate` | không |
| 29_subject_namespace_collision | User có id `role:staff` biến thành node role trong casbin, khiến `eve` (staff) thừa hưởng `iam_admin` | **casbin: leo thang quyền** |
| 30_agent_delegation_cannot_bypass_limit | Agent limit=10 nhận delegation limit 5000 và duyệt được 11 | **cả 3: vượt trần agent** (lỗi của Host) |
| 31_receipt_bound_to_behalf | Receipt cấp cho bot đại diện `boss` vẫn dùng được khi đổi sang đại diện `boss2` | **cả 3** (lỗi binding của Host) |
| 32_concurrent_evaluation_isolated | 8 luồng cùng dùng một engine, `sys.setswitchinterval(1e-6)` | **vakt: list `hits` dùng chung giữa các request**, trả sai statement/reason |

Kết quả trước khi sửa:
```
FAILED test_iam.py::test_iam[29_subject_namespace_collision-casbin]
FAILED test_iam.py::test_iam[30_agent_delegation_cannot_bypass_limit-{mini,casbin,vakt}]
FAILED test_iam.py::test_iam[31_receipt_bound_to_behalf-{mini,casbin,vakt}]
FAILED test_iam.py::test_iam[32_concurrent_evaluation_isolated-vakt]
8 failed, 88 passed in 7.41s
```

Các bản sửa (mỗi bản chỉ vài dòng):
- **Host:** delegation chỉ cấp cho người (`req.actor.kind != 'human'` dẫn tới `invalid_delegation`). Agent chỉ hành động qua behalf + autonomy. Binding của receipt thêm `req.actor.behalf`.
- **Casbin adapter:** tách namespace, subject của user mã hóa thành `user:<id>`, role giữ dạng `role:<name>`.
- **Vakt adapter:** mỗi lần evaluate tạo một đối tượng `Call(req)` truyền qua context, không dùng list chung nữa.
- **Mini:** `evaluate` short-circuit; trace đầy đủ chuyển sang `explain()` (xem phần benchmark).

Kết quả sau khi sửa (chạy 2 lần liên tiếp):
```
96 passed in 4.78s
96 passed in 5.64s
```

**Rủi ro còn lại của casbin adapter:** adapter đồng bộ `g()` theo từng request (`remove_filtered_grouping_policy` / `add_role_for_user_in_domain`), tức là ghi vào trạng thái chung của enforcer. Test 32 vẫn pass vì mỗi luồng là một user khác nhau. Nhưng nếu cùng một user có 2 snapshot role chạy song song (ví dụ role vừa bị thu hồi) thì sẽ race. Muốn dùng casbin thật thì phải nạp `g` từ DB theo revision và không sửa trong lúc enforce.

## 2. Benchmark 10.000 lượt evaluate

Có 6 request xoay vòng: allow own, implicit deny, team allow, department deny, ABAC approve, agent L0 read. Mỗi actor id có một tập role cố định để casbin không phải re-sync `g` (lần đo đầu dùng chung id `alice` nên casbin chậm thêm 12–24%). `backend` là engine thuần; `host` là toàn bộ PEP. Lần đo 206 statement là 6 statement gốc cộng 200 statement nhiễu.

```
$ python bench.py 0 200
N=10000 evaluations, 6 rotating requests
engine  stmts mode    build_ms  total_s    eval/s   p50_us   p99_us
mini        6 backend      0.3    0.046    217495      3.4     12.4
mini        6 host         0.3    0.088    113611      6.2     24.4
casbin      6 backend      1.0    1.233      8107     88.4    535.6
casbin      6 host         1.0    1.538      6502    102.7    730.7
vakt        6 backend      0.6    0.142     70423     12.2     32.5
vakt        6 host         0.6    0.180     55659     14.9     41.5
mini      206 backend      3.3    0.280     35685     24.6     57.5
mini      206 host         3.3    0.345     29007     27.6     85.1
casbin    206 backend      4.2   17.004       588   1483.8   3734.3
casbin    206 host         4.2   16.897       592   1376.6   4193.9
vakt      206 backend      5.1    1.235      8100    111.4    279.9
vakt      206 host         5.1    1.385      7222    114.8    281.8
```
Trước khi tối ưu, mini dựng trace cho mọi statement nên ở 206 statement chỉ đạt khoảng 3.000 eval/s, chậm hơn vakt. Sau khi short-circuit, mini nhanh gấp khoảng 4,4 lần vakt và khoảng 60 lần casbin. Casbin chậm vì mỗi hàng policy phải chạy matcher qua simpleeval kèm 2 hàm Python tùy biến. Thêm nữa, một statement bị nhân thành subjects × resources × actions hàng.
Cả 3 engine đều nhanh hơn nhiều so với một round-trip Postgres, nên hiệu năng không phải yếu tố quyết định ở quy mô xT. Chỉ riêng casbin có p99 khoảng 4 ms ở 200 statement là đáng lưu ý.

## 3. Explainability: engine tự nói được gì?

Lưu ý: case 08 chỉ kiểm `explain()` dùng hàm `diagnostics()` chung (lấy từ mini). Vì vậy việc casbin/vakt pass case 08 **không** chứng minh được khả năng giải thích native. Kết quả của `explain_demo.py`:

```
### allow (3 allows match)
  mini   allowed=True  reason=allow         statements=['own', 'staff-update', 'limit'] native=()
  casbin allowed=True  reason=allow         statements=['limit'] native=({'native_rule': ['role:staff', 'acme', 'xrn:acme:*:*', 'record.*', 'limit', 'allow']},)
  vakt   allowed=True  reason=allow         statements=['own', 'staff-update', 'limit'] native=({'matched': ('own', 'staff-update', 'limit')},)
### explicit deny (no-hr)
  mini   allowed=False reason=explicit_deny statements=['own', 'staff-update', 'no-hr', 'limit'] native=()
  casbin allowed=False reason=explicit_deny statements=['no-hr'] native=({'native_rule': ['*', 'acme', 'xrn:acme:*:*', 'record.*', 'no-hr', 'deny']},)
  vakt   allowed=False reason=explicit_deny statements=['own', 'staff-update', 'no-hr', 'limit'] native=(...)
### implicit deny (amount>limit, other owner)
  mini/casbin/vakt: statements=[]
  shared explain() -> not matched: [{"id": "own", "failed": ["conditions"], "conditions": [{"field": "resource.owner", "op": "eq", "actual": "bob", "expected": "alice", "passed": false}]},
    {"id": "staff-update", "failed": ["action"]}, {"id": "no-hr", ...}, {"id": "limit", "failed": ["conditions"], "conditions": [{"field": "resource.amount", "op": "le", "actual": 5001, "expected": 5000, "passed": false}]}]
```

| | Statement khớp | Lý do deny ngầm (cái gì sai, giá trị thật/kỳ vọng) |
|---|---|---|
| **casbin** | `enforce_ex` chỉ trả **1 hàng** (hàng khớp cuối cùng), không phải mọi statement. Với deny thì trả đúng hàng deny. | Không có: chỉ trả `[]`. |
| **vakt** | Native chỉ trả bool. `audit_log` có ghi `candidates/deciders` nhưng qua logging. Danh sách `matched` ở trên là do adapter tự hook vào Rule ABAC. | Không có. |
| **mini** | Đủ mọi statement khớp. | `explain()` chỉ ra predicate nào sai (subject/action/resource/conditions) và cặp `actual/expected` cho từng điều kiện. |

Cả 3 engine đều phải dùng `diagnostics()` tự viết thì mới trả lời được câu "vì sao bị từ chối". Điều này cần cho tool `iam.explain` và cho thông báo `approval_required` gửi agent. Nghĩa là dù chọn casbin hay vakt, ta vẫn phải tự viết và duy trì một evaluator thứ hai song song.

## 4. Policy-as-data: agent đề xuất, người duyệt, lưu trong Postgres

Luồng mong muốn: agent gửi JSON statement → `validate()` fail-closed → lưu bản nháp (revision, sha256) → người có `iam_admin` duyệt, không phải người đề xuất và cùng tenant (case 24) → kích hoạt revision → evaluator nạp lại theo `(tenant, revision)`.

| | Mô hình lưu | Đánh giá |
|---|---|---|
| **mini** | Policy document chính là JSON cần lưu: cột JSONB `doc`, `sha256`, `revision`, `proposed_by/kind`, `approved_by/at`, `status`, có RLS theo tenant. Duyệt bằng cách so `sha256` (đã chạy trong `PolicyStore`). | Khớp 1:1. Diff giữa 2 revision đọc được bằng mắt. Agent viết được policy ngay từ JSON Schema. |
| **casbin** | Adapter SQL lưu hàng `ptype, v0..v5`. Một statement bị bung thành tích Descartes nhiều hàng. Conditions không lưu được trong casbin, vì matcher gọi `checks(r.ctx, p.sid)` tra một dict Python. Ngoài ra `model.conf` là một biểu thức giống code. | Vẫn phải tự giữ bảng statement gốc và đồng bộ sang hàng casbin, tức là có 2 nguồn sự thật. `model.conf` phải do host cố định, không cho agent sửa. |
| **vakt** | `vakt.storage.sql` lưu Rule qua `jsonpickle`. **Đã kiểm chứng:** `Rule.from_json` (được dùng ở `vakt/storage/sql/model.py:96,157`) gọi được callable tùy ý. | **Loại.** Policy do agent đề xuất mà lưu trong DB thì thành vector thực thi code khi deserialize: |

```
$ python -I -c "from vakt.rules.base import Rule; Rule.from_json('{\"py/reduce\": [{\"py/function\": \"builtins.print\"}, {\"py/tuple\": [\"CALLED builtins.print FROM POLICY JSON\"]}]}')"
jsonpickle 4.1.3
CALLED builtins.print FROM POLICY JSON
```

## 5. Giấy phép và bảo trì upstream (GitHub API, 2026-10-15)

| Repo | License | Trạng thái |
|---|---|---|
| `kolotaev/vakt` | Apache-2.0 (`/license` API trả `LICENSE`, `Apache-2.0`) | `archived=false`, ★194, 8 issue mở. **Push cuối 2024-04-02** (sửa README). **Release cuối 1.6.0 ngày 2023-04-12** (trước đó 1.5.0 ngày 2020-07-23). PyPI 1.6.0. Thực tế đã ngừng phát triển khoảng 2,5 năm. |
| `casbin/pycasbin` → **đã chuyển thành `apache/casbin-pycasbin`** (API trả 301) | Apache-2.0 | `archived=false`, ★1774, 2 issue mở, **push 2026-10-09**. Release v2.8.0 ngày 2026-02-02, v2.7.1 ngày 2026-01-01, v2.7.0 ngày 2025-12-28 (theo `upstream.json` của codex và PyPI `pycasbin` 2.8.0 upload 2026-02-02). Gói PyPI cũ `casbin` dừng ở 1.43.0 (2025-05-10), phải dùng tên `pycasbin`. Bảo trì tốt. |

Cả 2 đều dùng giấy phép OSI, hợp lệ với yêu cầu của đề. Lượt gọi API thứ hai (releases/commits của casbin) bị rate limit 60/h, nên phần release lấy theo PyPI và `upstream.json`.

## 6. Tổng hợp

| Tiêu chí | mini (tự viết) | pycasbin | vakt |
|---|---|---|---|
| 32 case (sau khi sửa) | 32/32 | 32/32 (cần sửa namespace) | 32/32 (cần sửa luồng) |
| Lỗi do chính engine/adapter gây ra | 0 | leo thang quyền qua namespace; mutate `g` mỗi request | race chia sẻ `hits` |
| eval/s ở 6 / 206 statement (backend) | 217k / 36k | 8k / 0,6k | 70k / 8k |
| Giải thích native | đủ: mọi statement khớp + predicate sai + actual/expected | 1 hàng; deny ngầm không có lý do | chỉ bool |
| Policy-as-data, Postgres | JSONB 1:1, duyệt bằng sha256 | 2 nguồn sự thật, conditions nằm ngoài casbin | jsonpickle, thực thi code khi deserialize |
| Bảo trì | tự giữ (khoảng 60 dòng kernel + 73 dòng ngôn ngữ) | tốt (Apache org) | ngừng từ 2023 |
| License | Apache-2.0 (của xT) | Apache-2.0 | Apache-2.0 |

## 7. Khuyến nghị

**Chọn evaluator tối giản (`mini.py` + `policy_language.py`) làm engine chính, và giữ `Host` (PEP chung) làm nơi enforce các ranh giới agent.** Lý do:
1. Đây là engine duy nhất đáp ứng trọn 2 yêu cầu riêng của xT: (a) giải thích được vì sao deny cho agent và người duyệt, (b) policy JSON do agent đề xuất được lưu nguyên trạng trong Postgres, duyệt bằng hash. Nếu dùng casbin, ta vẫn phải tự viết `diagnostics()` và bảng statement gốc. Khi đó casbin chỉ còn là một bản sao chậm hơn khoảng 60 lần.
2. Phần khó và dễ sai nằm ở `Host`: 3 trên 4 lỗi tìm được thuộc Host hoặc adapter, không phải engine. Thư viện không giúp gì ở phần này.
3. Quy tắc "không xây từ đầu" vẫn được giữ ở tầng hạ tầng (Keycloak, Postgres RLS, APISIX). Kernel IAM chỉ khoảng 130 dòng, có test đối kháng, và rủi ro thấp hơn việc phụ thuộc một thư viện đã ngừng bảo trì (vakt) hoặc một thư viện phải bọc rất nhiều (casbin).

**Loại vakt:** ngừng bảo trì từ 2023, lưu trữ SQL qua jsonpickle thực thi được code, và adapter phải tự hook mới biết statement nào khớp.

**Giữ pycasbin làm differential oracle trong CI** (chỉ là dependency dev, không chạy runtime): chạy cùng ma trận `cases.py` trên cả mini và casbin. Mọi khác biệt sẽ báo lỗi semantics, giúp tránh việc tự viết mà không có đối chứng.

Việc cần làm khi đưa vào sản phẩm (không thuộc phạm vi spike):
- Compile/cache theo `(tenant_id, policy_revision)`, index statement theo tiền tố action.
- Bảng `iam_policy` JSONB có RLS, cấm UPDATE nội dung đã duyệt (mỗi lần sửa là một revision mới).
- Ghi AuditLog cả `reason` lẫn `statements` của mỗi quyết định.
- Expose `iam.explain` qua quyền riêng, vì trace có lộ giá trị thuộc tính.
- Receipt duyệt lưu ở DB, gắn với `behalf`. Delegation chỉ cấp cho người.
- Giữ nguyên các case 26–32 làm regression.
