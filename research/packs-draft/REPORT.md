<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# REPORT: 3 workflow pack lõi, viết hoàn toàn bằng dữ liệu và chạy trên 3 engine spike thật

Ngày chạy: 2026-10-15. Mọi số liệu và output bên dưới là **output thật** của `tests/run_all.sh` chạy từ thư mục này (bản đầy đủ ở `results/*.txt`). Không sửa dòng nào của ba spike (`spike-model-engine`, `spike-spiffworkflow`, `spike-iam`): test chỉ `import` chúng.

Môi trường: Python 3.13, PostgreSQL 17 (`postgres:17-alpine`, docker, cổng 15440, đã xóa container sau khi chạy; image có sẵn trên máy, không pull mới), pydantic 2.14.1, psycopg 3.3.6, SpiffWorkflow 3.2.0. Kiểm `df -h /` trước khi chạy: còn 7,3 GB.

Chạy lại: `PY=<python có các gói trên> packs-draft/tests/run_all.sh`.

## 0. Kết luận nhanh

| Hạng mục | Kết quả |
|---|---|
| 3 pack, 19 model, 6 process, 3 policy IAM, 3 bộ seed Bình Minh, 3 README tiếng Việt | Đã soạn, toàn bộ là JSON thuần (không có code nghiệp vụ) |
| Model compiler | **Đạt.** 3 pack áp lần lượt thành catalog v1, v2, v3: 6 + 5 + 8 model, mỗi lần 100% thao tác `AN TOÀN`, 0 BỊ CHẶN, post-phase OK. 19/19 bảng bật `ENABLE` + `FORCE RLS`, 85 policy Postgres. Audit hash-chain `verify = (85, None)` |
| Process compiler + SpiffWorkflow | **Đạt trong phạm vi compiler hỗ trợ.** 6/6 spec compile và parse; chạy happy path, từ chối, SLA leo thang (đồng hồ giả), lưu JSONB rồi nạp ở process mới. **Không** có song song, vòng lặp, timer start (mục 6) |
| IAM | **Đạt.** 41/41 ca PASS cho từng policy riêng, và 41/41 khi gộp 3 policy thành một document của tenant. Agent tự duyệt bị chặn ở mọi mức tự chủ |
| Khoảng trống engine do pack phát hiện | 27 mục (12 process, 8 model, 7 IAM; mục 6), trong đó 7 mục mức A cần quyết định trước khi chốt định dạng pack |

## 1. Cấu trúc

```
packs-draft/
  REUSE.toml  REPORT.md
  <pack>/{pack.json, README.md, models/*.json, processes/*.json, policies/iam.json, seed/*.json}
  tests/{run_all.sh, run_models.py, run_db_tests.py, run_iam.py, run_processes.py, seedlib.py, _paths.py}
  results/{models,db,iam,iam_merged,process}.txt
```

| Pack | Model | Process | Policy |
|---|---|---|---|
| `de-xuat-phe-duyet` | loai_de_xuat, de_xuat, tam_ung (03-TT), thanh_toan (05-TT), hoan_ung (04-TT), buoc_duyet | `de_xuat_phe_duyet` (1 quy trình cho 3 mẫu, rẽ nhánh bằng `when`) | 11 statement |
| `giao-viec-sla` | loai_cong_viec, cong_viec, cong_viec_muc, nhat_ky_sla, ban_tin_tuan | `giao_viec_thuong`, `giao_viec_khan`, `ban_tin_tuan` | 9 statement |
| `nhan-su-vong-doi` | phong_ban, nhan_vien, nhan_vien_pii, ho_so_vong_doi, viec_vong_doi, tai_san_cap, cap_quyen, quyet_toan_nghi_viec | `nv_onboarding`, `nv_offboarding` | 12 statement |

`pack.json` là manifest **đề xuất**: khai báo role, op service cần đăng ký (`allowed_ops`), ràng buộc lane động (`lane_bindings`), ánh xạ hành động IAM theo bước (`step_actions`) và mức tự chủ của agent. Chưa engine nào đọc nó (xem G-P4, G-P7).

## 2. Model engine: compile và áp vào Postgres

Áp 3 pack tăng dần (v1, v2, v3) để thử luôn việc cài thêm pack trên catalog đang chạy:

```
=== catalog v1: packs=['de-xuat-phe-duyet'] models=6 DDL statements=173
Tổng: 6 AN TOÀN, 0 RỦI RO, 0 PHÁ HỦY, 0 BỊ CHẶN
APPLY: {'version': 1, 'plan_hash': '3b95b53d1a4874e4', 'tx_steps': 173} post: 0 post_ok: True
=== catalog v2: packs=['de-xuat-phe-duyet', 'giao-viec-sla'] models=11 DDL statements=312
Tổng: 5 AN TOÀN, 0 RỦI RO, 0 PHÁ HỦY, 0 BỊ CHẶN
APPLY: {'version': 2, 'plan_hash': 'e3fbfec4d244c6e8', 'tx_steps': 139} post: 0 post_ok: True
=== catalog v3: packs=['de-xuat-phe-duyet', 'giao-viec-sla', 'nhan-su-vong-doi'] models=19 DDL statements=518
Tổng: 8 AN TOÀN, 0 RỦI RO, 0 PHÁ HỦY, 0 BỊ CHẶN
APPLY: {'version': 3, 'plan_hash': 'f9974a9366e5ca23', 'tx_steps': 206} post: 0 post_ok: True
tables: ['ban_tin_tuan', 'buoc_duyet', 'cap_quyen', 'cong_viec', 'cong_viec_muc', 'de_xuat', 'ho_so_vong_doi', 'hoan_ung', 'loai_cong_viec', 'loai_de_xuat', 'nhan_vien', 'nhan_vien_pii', 'nhat_ky_sla', 'phong_ban', 'quyet_toan_nghi_viec', 'tai_san_cap', 'tam_ung', 'thanh_toan', 'viec_vong_doi']
rls forced: (19, 19)
policies: 85
```

Hai điểm đáng ghi: quan hệ tự tham chiếu (`cong_viec.phu_thuoc`, `nhan_vien.quan_ly`) compile và áp được; rule `nguoi_duyet_cuoi != nguoi_de_nghi` (so sánh hai trường `user`) trở thành CHECK ở DB, NULL được qua.

## 3. Hành vi trên DB thật (CRUD, rule, RLS, PII)

```
### SEED
de-xuat-phe-duyet {'loai_de_xuat': 3, 'de_xuat': 3, 'tam_ung': 3, 'buoc_duyet': 6}
giao-viec-sla {'loai_cong_viec': 3, 'cong_viec': 4, 'cong_viec_muc': 4, 'nhat_ky_sla': 2, 'ban_tin_tuan': 1}
nhan-su-vong-doi {'phong_ban': 5, 'nhan_vien': 15, 'nhan_vien_pii': 15, 'tai_san_cap': 5, 'cap_quyen': 6, 'ho_so_vong_doi': 1, 'viec_vong_doi': 4}

### PACK 1: de-xuat-phe-duyet
- nhan_vien (Việt) tạo thẳng de_xuat qua CRUD: {"ok": false, "error": {"code": "FORBIDDEN", "message": "Vai trò ['nhan_vien'] không có quyền 'create' trên 'Đề xuất'", "fields": [], "hint": "Vai trò được phép: ['xt_workflow']"}}
- xt_workflow tạo de_xuat cho Việt: {"ok": true, "ma": "TU-2026-0042"}
- xt_workflow: duyệt cuối = chính người đề nghị (SoD CHECK): {"ok": false, "error": {"code": "CONSTRAINT_VIOLATION", "message": "Người duyệt cuối không được là người đề nghị (tách nhiệm vụ)", "fields": [{"field": "nguoi_de_nghi", "label": "Người đề …
- xt_workflow: da_duyet thiếu người duyệt (rule r2): {"ok": false, "error": {"code": "CONSTRAINT_VIOLATION", "message": "Đề xuất đã duyệt/đã chi phải có người duyệt cuối và thời điểm duyệt", "fields": [{"field": "ngay_duyet", "label": "Thời điểm duy…
- xt_workflow: da_duyet bởi Long (hợp lệ): {"ok": true, "record": {"id": "a2de6e3a-290e-4239-aef6-cfcd278722bb", "revision": 2, "owner_id": "ddf537e7-b0cf-5fbd-8dec-771d94b71bd0", "team_id": null, "department_id": "79ba0319-3193-5ef0-aedd-2ff9759854…
- tam_ung 80tr + ngày về < ngày đi + thiếu STK (storyboard S1): {"ok": false, "error": {"code": "VALIDATION_FAILED", "message": "Dữ liệu 'Giấy đề nghị tạm ứng' chưa hợp lệ (1 lỗi)", "fields": [{"field": "so_tien", "label": "Số tiền tạm ứng (VND)", "…
- tam_ung hợp lệ 8tr: {"ok": true}
- thanh_toan 25tr không có HĐĐT: {"ok": false, "error": {"code": "RULE_VIOLATION", "message": "Thanh toán từ 20 triệu trở lên phải có hóa đơn điện tử và mã số thuế", "fields": [{"field": "hoa_don_dien_tu", "label": "Có hóa đơn điện tử", "code": "rul…
- hoan_ung thực chi > tạm ứng mà không khai chi thừa: {"ok": false, "error": {"code": "RULE_VIOLATION", "message": "Thực chi vượt tạm ứng thì phải khai 'chi thừa đề nghị thanh toán thêm'", "fields": [{"field": "chi_thua_de_nghi_them", "label": "Số c…

--- cô lập dòng theo vai trò (de_xuat)
- Việt (own): {"ok": true, "total": 4, "ma": ["TU-2026-0021", "TU-2026-0027", "TU-2026-0033", "TU-2026-0042"]}
- Anh (own, cùng phòng KD): {"ok": true, "total": 0, "ma": []}
- Mai (TBP, department): {"ok": true, "total": 4, "ma": ["TU-2026-0021", "TU-2026-0027", "TU-2026-0033", "TU-2026-0042"]}
- Hà (KTT, all): {"ok": true, "total": 4, "ma": ["TU-2026-0021", "TU-2026-0027", "TU-2026-0033", "TU-2026-0042"]}
- agent (role agent, không có quyền DB): {"code": "FORBIDDEN", "message": "Vai trò ['agent'] không có quyền 'read' trên 'Đề xuất'", "fields": [], "hint": "Vai trò được phép: ['giam_doc', 'ke_toan_thanh_toan', 'ke_toan_truong', 'nhan_vien', 'truong_b…
- giám đốc tenant ankhang xem de_xuat: {"ok": true, "total": 0}

### PACK 2: giao-viec-sla
- Việt tự cập nhật cv (không có quyền ghi): {"ok": false, "error": {"code": "FORBIDDEN", "message": "Vai trò ['nhan_vien'] không có quyền 'update' trên 'Công việc'", "fields": [], "hint": "Vai trò được phép: ['truong_bo_phan', 'xt_workflow']"}}
- Mai (TBP) tạo công việc giao cho Việt: {"ok": true}
- Mai tạo việc Khẩn nhưng lớp SLA thường (rule): {"ok": false, "error": {"code": "RULE_VIOLATION", "message": "Công việc ưu tiên Khẩn phải chạy quy trình SLA lớp Khẩn", "fields": [{"field": "lop_sla", "label": "Lớp quy trình SLA", "code": "rule:r3",…
- hoàn thành mà không có kết quả (rule): {"ok": false, "error": {"code": "CONSTRAINT_VIOLATION", "message": "Công việc hoàn thành/chờ nghiệm thu phải có kết quả", "fields": [{"field": "ket_qua", "label": "Kết quả nộp", "code": "rule", "message": "Cô…
- tiến độ 150% (max 100): {"ok": false, "error": {"code": "VALIDATION_FAILED", "message": "Dữ liệu 'Công việc' chưa hợp lệ (1 lỗi)", "fields": [{"field": "tien_do", "label": "Tiến độ (%)", "code": "less_than_equal", "message": "'Tiến độ (%)' phải <=…
- phụ thuộc tự tham chiếu (ref self) hợp lệ: {"ok": true, "record": {"id": "b8ac3e25-90cf-43aa-b80c-9fb1ed2df830", "revision": 2, "owner_id": "ddf537e7-b0cf-5fbd-8dec-771d94b71bd0", "team_id": null, "department_id": "79ba0319-3193-5ef0-aedd-2ff97598…
- Việt (own) thấy cv: ["CV-2026-0001", "CV-2026-0004", "CV-2026-0099"]
- Anh (own) thấy cv: ["CV-2026-0002"]
- Mai (department KD) thấy cv: ["CV-2026-0001", "CV-2026-0002", "CV-2026-0003", "CV-2026-0004", "CV-2026-0099"]

### PACK 3: nhan-su-vong-doi
- Hùng (nhan_vien) đọc PII: {"total": 1, "luong": [14500000]}
- Việt đọc PII: {"total": 1, "luong": [18000000]}
- Linh (hr_pii) đọc PII: {"total": 15, "luong": [27000000, 26000000, 25000000]}
- Tâm (nhan_su, không hr_pii) đọc PII: {"total": 1, "luong": [21000000]}
- agent đọc PII (DB): {"code": "FORBIDDEN", "message": "Vai trò ['agent'] không có quyền 'read' trên 'Hồ sơ nhân sự nhạy cảm'", "fields": [], "hint": "Vai trò được phép: ['hr_pii', 'nhan_vien', 'xt_workflow']"}
- Việt (own) đọc nhan_vien: {"total": 1}
- Thịnh (TBP IT, dept) đọc nhan_vien: {"total": 15}
- nhan_vien nghỉ mà thiếu ngày làm việc cuối (rule): {"ok": false, "error": {"code": "CONSTRAINT_VIOLATION", "message": "Nhân viên đã nghỉ/đang nghỉ phải có ngày làm việc cuối", "fields": [{"field": "ngay_nghi", "label": "Ngày làm việc cuối", "code"…
- quyết toán đã chi khi còn tài sản chưa trả (rule): {"ok": false, "error": {"code": "RULE_VIOLATION", "message": "Chỉ được chi quyết toán khi không còn tài sản chưa trả", "fields": [{"field": "da_chi", "label": "Đã chi", "code": "rule:r1", "message…

### RLS thô
row counts: {'loai_de_xuat': 3, 'de_xuat': 4, 'tam_ung': 4, 'buoc_duyet': 6, 'loai_cong_viec': 3, 'cong_viec': 5, 'cong_viec_muc': 4, 'nhat_ky_sla': 2, 'ban_tin_tuan': 1, 'phong_ban': 5, 'nhan_vien': 15, 'nhan_vien_pii': 15, 'ho_so_vong_doi': 1, 'vi…
audit hash-chain verify (n, first_bad_seq): (85, None)
```

Đọc kết quả: người dùng thường không tạo/sửa được `de_xuat` qua CRUD (`FORBIDDEN`), chỉ danh tính `xt_workflow` ghi được; các rule SoD, hạn mức 30 triệu, ngày về >= ngày đi, hóa đơn điện tử từ 20 triệu, chi thừa/nộp lại, bị chặn kèm thông điệp tiếng Việt; Anh (cùng phòng KD) không thấy phiếu của Việt, Mai (department) và Hà (all) thấy; tenant `ankhang` thấy 0 dòng; agent không có quyền DB trên các bảng này; PII chỉ `hr_pii` thấy toàn bộ.

## 4. Process: BPMN sinh từ spec, chạy bằng SpiffWorkflow với engine whitelist

Dùng đúng `compiler.compile_spec` + `xtflow.safe_engine` của spike. XML sinh ra: `de_xuat_phe_duyet` 13.377 B, `nv_offboarding` 14.227 B, `nv_onboarding` 9.563 B, `giao_viec_*` 7.0 KB, `ban_tin_tuan` 1.8 KB. Các op service chạy thật trên DB (`tong_hop_tuan` đọc `cong_viec` và ghi `ban_tin_tuan`; `tao_viec_vong_doi`, `kich_hoat_nhan_vien`, `dong_ho_so_vong_doi` ghi vào model qua `Engine`).

Compiler và engine **không** kiểm: vai trò của người hoàn tất task, quyền IAM, giá trị quyết định. `run_processes.py` có một harness `Instance.act()` thực hiện đúng 3 kiểm tra mà runtime xT phải làm trước khi `task.run()`: (1) quyết định thuộc `approve|reject`, (2) người thực hiện giữ lane (role tĩnh, hoặc ràng buộc động theo dữ liệu), (3) `Host.evaluate(record.approve|record.update)` của IAM spike cho phép.

SLA dùng đồng hồ giả (vá `datetime` trong module timer của SpiffWorkflow, cộng lệch giờ) để thử 24 giờ trong vài giây; trạng thái được lưu thật vào cột `jsonb` của Postgres, poller chọn `next_due <= now()` rồi nạp lại ở object mới và `refresh_timers()`.

```
======== PACK 1 de_xuat_phe_duyet
  tạm ứng 4tr: Mai                               outcome=APPROVED     path=['NV-0005:Trưởng bộ phận duyệt[approve]'] 
  tạm ứng 8tr: Mai, Long (S1/S2)                 outcome=APPROVED     path=['NV-0005:Trưởng bộ phận duyệt[approve]', 'NV-0001:Giám đốc duyệt[approve]'] 
  thanh toán 80tr: Mai, Bảo, Hà, Long            outcome=APPROVED     path=['NV-0005:Trưởng bộ phận duyệt[approve]', 'NV-0003:Kế toán thanh toán kiểm chứng từ[approve]', 'NV-0002:Kế toán trưởng duyệt[approve]', 'NV-0001:Giám đốc duyệt[approve]'] 
  hoàn ứng 12tr: Mai, Bảo, Long                  outcome=APPROVED     path=['NV-0005:Trưởng bộ phận duyệt[approve]', 'NV-0003:Kế toán thanh toán kiểm chứng từ[approve]', 'NV-0001:Giám đốc duyệt[approve]'] 
  reject tại Mai (bước 1)                        outcome=REJECTED     path=['NV-0005:Trưởng bộ phận duyệt[reject]'] ledger+0
  reject tại Long (bước cuối) 80tr               outcome=REJECTED     path=['NV-0005:Trưởng bộ phận duyệt[approve]', 'NV-0003:Kế toán thanh toán kiểm chứng từ[approve]', 'NV-0002:Kế toán trưởng duyệt[approve]', 'NV-0001:Giám đốc duyệt[reject]'] ledger+0
  Mai là người đề nghị (bỏ bước TBP): Long       outcome=APPROVED     path=['NV-0001:Giám đốc duyệt[approve]'] 
  còn nợ quá hạn: Mai, Hà(xem xét nợ), Long      outcome=APPROVED     path=['NV-0005:Trưởng bộ phận duyệt[approve]', 'NV-0002:Kế toán trưởng xem xét nợ tạm ứng quá hạn[approve]', 'NV-0001:Giám đốc duyệt[approve]'] 
  -- chặn tại bước (SoD / lane / quyết định lạ / agent)
  Mai tự duyệt đề xuất của mình (định tuyến sai) outcome=PENDING:Trưởng bộ phận duyệt@truong_bo_phan path=['DENY(NV-0005):iam:self_approval'] 
  Việt/Anh (không có vai trò) duyệt bước Mai     outcome=PENDING:Trưởng bộ phận duyệt@truong_bo_phan path=['DENY(NV-0006):lane_mismatch(lane=truong_bo_phan)', 'DENY(NV-0007):lane_mismatch(lane=truong_bo_phan)'] 
  Thịnh (TBP IT, sai phòng) duyệt                outcome=PENDING:Trưởng bộ phận duyệt@truong_bo_phan path=['DENY(NV-0006):lane_mismatch(lane=truong_bo_phan)', 'DENY(NV-0007):lane_mismatch(lane=truong_bo_phan)', 'DENY(NV-0012):iam:implicit_deny'] 
  Mai trả quyết định 'return' (ngoài approve/reject) outcome=PENDING:Trưởng bộ phận duyệt@truong_bo_phan path=['DENY(NV-0006):lane_mismatch(lane=truong_bo_phan)', 'DENY(NV-0007):lane_mismatch(lane=truong_bo_phan)', 'DENY(NV-0012):iam:implicit_deny', 'DENY(NV-0005):invalid_decision'] 
  agent-claude duyệt bước TBP (không có lane)    outcome=PENDING:Trưởng bộ phận duyệt@truong_bo_phan path=['DENY(agent-claude):lane_mismatch(lane=truong_bo_phan)'] 
  agent-claude gán nhầm role TBP, L3, không receipt outcome=PENDING:Trưởng bộ phận duyệt@truong_bo_phan path=['DENY(agent-claude):lane_mismatch(lane=truong_bo_phan)', 'DENY(agent-claude):iam:approval_required'] 
  -- ghi sổ (LEDGER): [('NV-0006', 4000000), ('NV-0006', 8000000), ('NV-0006', 80000000), ('NV-0006', 12000000), ('NV-0005', 8000000), ('NV-0006', 8000000)]
======== PACK 1: SLA leo thang (đồng hồ giả +25h) + lưu JSONB Postgres + poller next_due
  lưu instance #1: next_due=2026-10-16 03:46:04.548078+00:00
  READY ban đầu: ['Trưởng bộ phận duyệt@truong_bo_phan']
  poller (now giả = +25h) tìm thấy: [(1,)]
  sau refresh_timers (process mới, nạp từ JSONB): ['Trưởng bộ phận duyệt (leo thang)@giam_doc'] | NOTES: ['SLA vượt hạn: Trưởng bộ phận duyệt']
  Mai (đã bị thay thế) cố duyệt sau khi leo thang outcome=PENDING:Trưởng bộ phận duyệt (leo thang)@giam_doc path=['DENY(NV-0005):lane_mismatch(lane=giam_doc)'] 
  Long duyệt bước leo thang                      outcome=PENDING:Giám đốc duyệt@giam_doc path=['DENY(NV-0005):lane_mismatch(lane=giam_doc)', 'NV-0001:Trưởng bộ phận duyệt (leo thang)[approve]'] 
  Long duyệt tiếp bước GĐ (>5tr, lặp)            outcome=APPROVED     path=['DENY(NV-0005):lane_mismatch(lane=giam_doc)', 'NV-0001:Trưởng bộ phận duyệt (leo thang)[approve]', 'NV-0001:Giám đốc duyệt[approve]'] <- GAP: GĐ phải duyệt 2 lần
======== PACK 2 giao_viec_*
  happy: Việt nhận, Việt nộp, Mai nghiệm thu     outcome=APPROVED     path=['NV-0006:Xác nhận nhận việc[approve]', 'NV-0006:Hoàn thành và nộp kết quả[approve]', 'NV-0005:Nghiệm thu kết quả[approve]'] 
  Việt tự nghiệm thu                             outcome=PENDING:Nghiệm thu kết quả@nguoi_giao path=['NV-0006:Xác nhận nhận việc[approve]', 'NV-0006:Hoàn thành và nộp kết quả[approve]', 'DENY(NV-0006):lane_mismatch(lane=nguoi_giao)'] 
  Việt từ chối nhận việc                         outcome=REJECTED     path=['NV-0006:Xác nhận nhận việc[reject]'] 
  Mai không chấp nhận kết quả                    outcome=REJECTED     path=['NV-0006:Xác nhận nhận việc[approve]', 'NV-0006:Hoàn thành và nộp kết quả[approve]', 'NV-0005:Nghiệm thu kết quả[reject]'] <- GAP: kết thúc luôn, không có vòng làm lại
  +5h chưa nhận việc -> ['Xác nhận nhận việc (leo thang)@nguoi_giao']
  Mai (người giao) xử lý bước nhận đã leo thang  outcome=PENDING:Hoàn thành và nộp kết quả@nguoi_thuc_hien path=['NV-0005:Xác nhận nhận việc (leo thang)[approve]'] 
  +73h chưa nộp kết quả -> ['Hoàn thành và nộp kết quả (leo thang)@truong_bo_phan'] (người làm bị thay bằng truong_bo_phan)
  lớp khẩn +1.5h chưa nhận -> ['Xác nhận nhận việc (leo thang)@nguoi_giao']
  lớp khẩn: +0.5h sau khi nhận, vẫn chờ người làm -> ['Hoàn thành và nộp kết quả@nguoi_thuc_hien']
  -- weekly digest (op tổng hợp đọc DB thật)
  outcome: APPROVED | notify: Tuần W42: giao mới 5, hoàn thành 1, quá hạn 1, bị chặn 1
  kiểm chứng bằng SQL: [('BT-2026-W42', Decimal('5'), Decimal('1'), Decimal('1'), Decimal('1'), ['CV-2026-0002'])]
======== PACK 3 nv_onboarding / nv_offboarding
  Tâm (người khởi tạo hồ sơ) tự duyệt bước HR    outcome=PENDING:Nhân sự: hồ sơ, HĐLĐ, BHXH@nhan_su path=['DENY(NV-0009):iam:self_approval'] 
  onboarding: Linh, Vy, Bảo, Mai                 outcome=APPROVED     path=['DENY(NV-0009):iam:self_approval', 'NV-0010:Nhân sự: hồ sơ, HĐLĐ, BHXH[approve]', 'NV-0013:IT: email, tài khoản, thiết bị[approve]', 'NV-0003:Kế toán: mã lương, tài khoản nhận lương[approve]', 'NV-0005:Quản lý: buddy và kế …
  DB: checklist = [('it', 1), ('ke_toan', 1), ('nhan_su', 1), ('quan_ly', 1)] | nhan_vien.trang_thai = [('thu_viec',)]
  offboarding sạch: Linh, Mai, Vy(thu hồi), Bảo, Linh(đóng) outcome=APPROVED     path=['NV-0010:Nhân sự: xác nhận ngày nghỉ và đơn thôi việc[approve]', 'NV-0005:Quản lý: xác nhận bàn giao công việc, khách hàng, tài liệu[approve]', 'NV-0013:IT: thu hồi toàn bộ quyền truy cập và thiết bị[approve]', '…
  DB: [('da_nghi',)] [('hoan_tat',)] | checklist: [(5,)]
  tài sản chưa thu hồi -> bước kế tiếp: ['Kế toán trưởng xử lý khoản còn nợ hoặc tài sản chưa trả@ke_toan_truong']
  Hà xử lý khoản nợ -> bước kế tiếp: ['Nhân sự: ẩn danh dữ liệu theo thời hạn, đóng hồ sơ@nhan_su']
  GUARD dong_ho_so_vong_doi chặn đóng hồ sơ: WorkflowTaskException -> RuntimeError:không đóng được hồ sơ: còn 1 tài sản và 1 quyền chưa thu hồi. 
  DB: ho_so = [('dang_xu_ly',)] | nhan_vien = [('cho_vao',)]
  trạng thái task sau lỗi: [('ut_hr_dong', 'COMPLETED'), ('sv_dong_ho_so', 'ERROR')]
  IT idle +5h (SLA 4h, 'thu hồi trong ngày') -> ['IT: thu hồi toàn bộ quyền truy cập và thiết bị (leo thang)@giam_doc']
  HR từ chối ngay bước đầu                       outcome=REJECTED     path=['NV-0010:Nhân sự: xác nhận ngày nghỉ và đơn thôi việc[reject]'] 
======== GAP PROBES: compiler/engine
  parallel_with/parallel bị bỏ qua im lặng, XML y hệt: True
  gõ sai khóa 'sla'->'slaa': compile OK, số boundary timer: 3 (gốc: 4 )
  type=parallel   -> SpecError: step hr: unsupported type 'parallel'
  type=task       -> SpecError: step hr: unsupported type 'task'
  type=wait       -> SpecError: step hr: unsupported type 'wait'
  type=timer      -> SpecError: step hr: unsupported type 'timer'
  type=subprocess -> SpecError: step hr: unsupported type 'subprocess'
  khóa 'trigger' lạ ở cấp spec: bị bỏ qua: True
  sla P7D: compile OK
  sla P1W -> SpecError step hr: bad ISO duration 'P1W'
  role 'nhóm&<x>': compile_spec trả XML (không kiểm ID_RE/escape lane name)
    nhưng parser BPMN từ chối: XMLSyntaxError xmlParseEntityRef: no name, line 11, column 26 (<string>, line 11)
  biến thiếu trong 'when' chỉ lỗi lúc chạy: TypeError can only concatenate str (not "dict") to str
  engine trần: tbp_decision='tra_lai_bo_sung' -> APPROVED + ghi sổ (gateway chỉ kiểm == 'reject')
  thiếu tbp_decision -> WorkflowTaskException Error evaluating expression 'tbp_decision == 'reject''.  Did you mean one of '['loai', 'amount', 're
LEDGER [('NV-0006', 4000000), ('NV-0006', 8000000), ('NV-0006', 80000000), ('NV-0006', 12000000), ('NV-0005', 8000000), ('NV-0006', 8000000), ('NV-0006', 8000000), ('NV-0006', 4000000)] | NOTES ['SLA vượt hạn: Trưởng bộ phận duyệt', 'SLA vượt hạn: Xác nhận nhận việc', 'SLA vượt hạn: Hoàn thành và n…
```

## 5. IAM: các ca chính

Policy dùng `policy_language` + `Mini` + `Host` của spike. Agent `agent-claude` đại diện một người (`behalf`); quyền hiệu lực là giao của agent và người đó.

```
=== IAM mode: PER-PACK documents
=== PACK 1: de-xuat-phe-duyet
  [PASS] Việt tạo đề xuất của chính mình                                                -> allow              stmts=['dx.nv-own-create']
  [PASS] Việt đọc đề xuất của Anh (NV-0007)                                             -> implicit_deny      stmts=[]
  [PASS] Việt tự duyệt đề xuất của mình                                                 -> self_approval      stmts=[]
  [PASS] Mai (TBP KD) duyệt đề xuất của Việt (phòng KD)                                 -> allow              stmts=['dx.tbp-approve']
  [PASS] Mai tự duyệt đề xuất do chính mình đề nghị                                     -> self_approval      stmts=[]
  [PASS] Long (GĐ) duyệt đề xuất của Việt                                               -> allow              stmts=['dx.gd-read-approve']
  [PASS] Thịnh (TBP IT) duyệt đề xuất phòng KD (sai phòng)                              -> implicit_deny      stmts=[]
  [PASS] Hà (KTT) duyệt thanh toán 80tr của Việt                                        -> allow              stmts=['dx.ktt-read-approve']
  [PASS] Long xóa đề xuất (chứng từ không được xóa)                                     -> explicit_deny      stmts=['dx.deny-delete-chung-tu']
  [PASS] agent-claude (L1, thay Việt) tạo nháp đề xuất                                  -> allow              stmts=['dx.agent-l0-l1']
  [PASS] agent-claude (L1, thay Việt) đọc đề xuất của Anh                               -> behalf_permission  stmts=[]
  [PASS] agent-claude (L1, thay Việt) TỰ DUYỆT đề xuất của Việt                         -> self_approval      stmts=[]
  [PASS] agent-claude (L1, thay Mai) duyệt đề xuất của Việt                             -> autonomy_ceiling   stmts=[]
  [PASS] agent L3 + receipt hợp lệ của Long, thay Mai duyệt đề xuất của Việt            -> explicit_deny      stmts=['dx.deny-agent-decide']
  [PASS] agent L3 (thay Mai) xóa đề xuất, không có receipt                              -> approval_required  stmts=[]
  [PASS] agent L3 (thay Mai) xóa đề xuất, có receipt của Long (Long cũng không được xóa) -> approver_permission stmts=['dx.deny-delete-chung-tu']
  [PASS] actor tenant ankhang truy cập xrn của binhminh                                 -> tenant_boundary    stmts=[]
--- explain() cho ca agent tự duyệt bị chặn bởi policy (L3 + receipt):
   {"allowed": false, "reason": "explicit_deny", "statements": ["dx.deny-agent-decide"], "matched_trace": [{"id": "dx.deny-agent-decide", "effect": "Deny", "predicates": {"subject": true, "action": true, "resource"…

=== PACK 2: giao-viec-sla
  [PASS] Việt (người làm) cập nhật tiến độ việc của mình                                -> allow              stmts=['gv.nguoi-thuc-hien-update']
  [PASS] Anh cập nhật việc của Việt                                                     -> implicit_deny      stmts=[]
  [PASS] Việt tự nghiệm thu việc của mình                                               -> self_approval      stmts=[]
  [PASS] Mai (người giao, TBP KD) nghiệm thu việc của Việt                              -> allow              stmts=['gv.nghiem-thu']
  [PASS] [GAP] Mai nghiệm thu khi creator = người giao (đúng dữ liệu DB)                -> self_approval      stmts=[]
  [PASS] Thịnh (TBP IT) nghiệm thu việc phòng KD                                        -> implicit_deny      stmts=[]
  [PASS] Mai tạo công việc giao cho nhân viên phòng KD                                  -> allow              stmts=['gv.tbp-giao-viec']
  [PASS] agent-claude (L1, thay Mai) tạo công việc (tách việc từ chỉ đạo)               -> allow              stmts=['gv.agent-l0-l1']
  [PASS] agent-claude (L1, thay Mai) nghiệm thu                                         -> autonomy_ceiling   stmts=[]
  [PASS] agent L3 + receipt của Long nghiệm thu thay Mai                                -> explicit_deny      stmts=['gv.deny-agent-nghiem-thu']

=== PACK 3: nhan-su-vong-doi
  [PASS] Hùng đọc hồ sơ PII của chính mình                                              -> allow              stmts=['ns.nv-own-read']
  [PASS] Việt đọc PII của Hùng                                                          -> implicit_deny      stmts=[]
  [PASS] Linh (hr_pii) đọc PII của Hùng                                                 -> allow              stmts=['ns.hr-pii']
  [PASS] Tâm (nhan_su, không có hr_pii) đọc PII của Hùng                                -> implicit_deny      stmts=[]
  [PASS] agent-claude (thay Linh, người có hr_pii) đọc PII                              -> explicit_deny      stmts=['ns.deny-agent-pii']
  [PASS] agent-claude (thay Linh) đọc hồ sơ nhân viên (không PII)                       -> allow              stmts=['ns.agent-l0-l1']
  [PASS] Linh duyệt bước HR hồ sơ onboarding NV-0016 (phòng KD)                         -> allow              stmts=['ns.nhan-su-all']
  [PASS] Tâm (người khởi tạo hồ sơ) tự duyệt bước HR                                    -> self_approval      stmts=[]
  [PASS] Vy (it_admin) duyệt bước IT                                                    -> allow              stmts=['ns.it-admin']
  [PASS] Mai (quản lý KD) duyệt bước quản lý của hồ sơ KD                               -> allow              stmts=['ns.tbp-ban-giao']
  [PASS] Mai duyệt bước quản lý của hồ sơ nhân viên phòng IT                            -> implicit_deny      stmts=[]
  [PASS] Anh (người nghỉ việc) tự duyệt hồ sơ nghỉ việc của mình                        -> self_approval      stmts=[]
  [PASS] Hà (KTT) duyệt quyết toán nghỉ việc                                            -> allow              stmts=['ns.ke-toan']
  [PASS] Long xóa hồ sơ nhân viên                                                       -> explicit_deny      stmts=['ns.deny-delete-ho-so']

FAILED CASES: none
```

Chạy lại cả 41 ca với **một document duy nhất** gộp 3 pack (mô phỏng bảng `iam_policy` theo tenant); id statement được gắn tiền tố pack (`dx.`, `gv.`, `ns.`) để không trùng:

```
=== IAM mode: MERGED (1 doc, 32 statements)
FAILED CASES: none
(41 ca PASS, cùng 41 ca như trên)
```

## 6. Khoảng trống của các engine do pack phát hiện

Mức: **A** = chặn/đổi thiết kế pack, **B** = có cách né nhưng cần quy ước/glue, **C** = nhỏ.

### Process compiler / SpiffWorkflow

| ID | Mức | Phát hiện (bằng chứng ở `results/process.txt`) | Cách né trong pack | Đề xuất |
|---|---|---|---|---|
| G-P1 | A | **Không có song song.** `type: parallel` bị `SpecError`; khóa `parallel`, `parallel_with` bị bỏ qua im lặng (XML y hệt). Onboarding/offboarding có IT, Kế toán, Quản lý độc lập nhau nhưng phải chạy tuần tự | Chạy tuần tự HR, IT, Kế toán, Quản lý | Thêm `type: parallel` (nhóm bước, join). SpiffWorkflow đã chạy được parallelGateway + `_reorder` (verification-spikes A.4), chỉ thiếu ở compiler |
| G-P2 | A | **Không có timer khởi tạo / lặp.** Bản tin tuần không tự chạy; khối `trigger` trong spec bị compiler bỏ qua | Lịch nằm trong khối `trigger` (chưa được ai đọc); cần `xt-worker` đọc | Thêm `start: {timer: cron}` hoặc định nghĩa lịch là dữ liệu riêng được kiểm chứng |
| G-P3 | A | **Quyết định ngoài `approve|reject` đi tiếp như duyệt.** Gateway chỉ kiểm `== 'reject'`: `tbp_decision='tra_lai_bo_sung'` cho ra APPROVED và ghi sổ. Không có kết quả "trả lại bổ sung" hay vòng làm lại: từ chối là kết thúc (nghiệm thu không đạt cũng thế) | Harness chặn giá trị lạ trước khi gọi `task.run()` | Gateway sinh nhánh tường minh `approve`/`reject`/`return`, nhánh mặc định là lỗi; thêm bước lặp |
| G-P4 | A | **Lane = role tĩnh.** "Người làm của đúng việc này", "quản lý của phòng người nghỉ" không biểu diễn được | `lane_bindings` trong `pack.json`, harness gán lane động từ dữ liệu instance | Cho lane tham chiếu biến (`role: "@nguoi_thuc_hien"`) |
| G-P5 | B | **Leo thang thay thế chứ không nhắc.** Boundary timer ngắt (interrupting): người làm bị thay bằng role cấp trên (+73 giờ: `Hoàn thành và nộp kết quả (leo thang)@truong_bo_phan`). Chỉ một cấp, không có "nhắc rồi mới leo thang". Giám đốc bị lặp: trưởng bộ phận leo thang lên giám đốc rồi bước `gd` (>5 triệu) bắt giám đốc duyệt lần hai | Chấp nhận, ghi trong README | Boundary không ngắt (nhắc) + nhiều mức; bước tự bỏ qua nếu cùng người đã duyệt |
| G-P6 | B | **SLA là hằng số trong spec.** `after` chỉ nhận `PT..H|PnD..`; `P1W` bị từ chối; không đọc được từ `loai_cong_viec.sla_*_gio` | Hai file `giao_viec_thuong`, `giao_viec_khan` | Cho `after` là biểu thức an toàn lấy từ dữ liệu |
| G-P7 | B | **Không có hành động IAM theo bước.** Mọi `approve` thành `record.approve`; nhưng "nhận việc/nộp kết quả" không phải duyệt, và `Host` chặn `record.approve` khi chủ sở hữu là chính người làm | `step_actions` trong `pack.json` (`nhan`, `lam` dùng `record.update`) | Spec bước khai `iam_action`; thêm loại bước `task` (không có cổng quyết định) |
| G-P8 | B | **Spec không có schema.** Khóa lạ bị bỏ qua: gõ nhầm `sla` thành `slaa` làm mất timer (3 boundary thay vì 4) mà compile vẫn OK. `when` dùng biến không tồn tại chỉ lỗi lúc chạy; đầu vào process không được khai báo ở đâu | `process_inputs` trong `pack.json` (chưa kiểm) | Pydantic `ProcessSpec` `extra=forbid` như model engine; kiểm tên biến với danh sách đầu vào |
| G-P9 | B | **Lỗi service bị che.** Ngoại lệ có `args[0]` không phải chuỗi (ví dụ `assert out['ok'], out`) làm `create_task_exec_exception` của SpiffWorkflow 3.2.0 nổ `TypeError`, che lỗi thật (cùng họ với bug `call_service` đã ghi ở spike). Khi op raise (đúng ý, là chốt "không đóng hồ sơ khi còn tài sản"), task service ở `ERROR`, task người trước đó đã `COMPLETED`, không có retry/bù trừ | Op luôn raise `RuntimeError(str)` | Bọc ngoại lệ trong `SafePythonScriptEngine`; định nghĩa retry/bù trừ ở runtime |
| G-P10 | B | **Không idempotent.** Trong lúc phát triển, `tong_hop_tuan` ghi bản ghi rồi lỗi serialize kết quả; chạy lại tạo trùng (chặn nhờ `unique`). Op có tác dụng phụ cần transaction chung với việc lưu state | | Idempotency key theo (instance, task) |
| G-P11 | B | Compiler không có hook vào/ra bước, nên `de_xuat.trang_thai`, `buoc_hien_tai`, `buoc_duyet` phải do runtime đồng bộ từ task | Chưa kiểm chứng ở pack 1 và 2 (xem mục 7) | Sự kiện `step.enter/exit` hoặc bước `service` do compiler chèn |
| G-P12 | C | `roles` không qua `ID_RE`, tên lane không escape: `compile_spec` nhận role `nhóm&<x>`, parser lxml từ chối sau đó (fail-closed, trùng ghi chú ở verification-spikes A.2) | | `ID_RE` + `quoteattr` |

### Model engine

| ID | Mức | Phát hiện | Cách né | Đề xuất |
|---|---|---|---|---|
| G-M1 | A | **Không có quyền ghi theo trường/trạng thái.** Quyền `update` là quyền trên cả dòng: nếu cấp cho người đề nghị thì họ sửa được cả `trang_thai` và `nguoi_duyet_cuoi` (suy ra từ rule DSL, rule SoD chỉ cấm trùng người, không cấm người khác; không chạy vì pack đã bỏ quyền này) | Người dùng chỉ có `read`; mọi ghi qua danh tính `xt_workflow`. Phải đảm bảo công cụ CRUD tổng quát không cho agent/người dùng mượn danh tính này | Thuộc tính `managed_by: workflow` hoặc quyền theo trường |
| G-M2 | A | **Không có nhãn PII** (`FieldSpec` `extra=forbid`), trong khi F-21 cần `pii` + `llm_egress` | Tách `nhan_vien_pii` thành model riêng, role `hr_pii`, Deny cho agent | Thêm `pii: bool/loại` vào `FieldSpec` |
| G-M3 | B | **Không có phạm vi theo "người tham gia".** Scope chỉ `own/team/department/all` theo `owner_id`/`department_id`; người giao và người làm khác nhau | Quy ước `owner_id` = người làm, `nguoi_giao` là trường riêng | Scope `participant` theo danh sách trường `user` |
| G-M4 | B | **Rule chỉ ở mức một dòng.** Không có ràng buộc giữa các dòng (không tạm ứng mới khi còn nợ quá hạn, ngân sách còn lại) và không có số học (`thuc_chi - tam_ung = nop_lai`) | Op service (`kiem_tra_cong_no`, `kiem_tra_tai_san`) + bước xem xét | Rule dạng truy vấn tổng hợp có khai báo, hoặc cột tính toán |
| G-M5 | B | **Catalog phẳng toàn deployment.** Tên và `id` model là toàn cục; pack phải tự gắn tiền tố id (`dx_`, `gv_`, `ns_`); không có khái niệm pack, phụ thuộc giữa pack, gỡ pack | Quy ước tiền tố | Namespace pack trong `Catalog` |
| G-M6 | C | Kiểu `user` chỉ là `uuid`, không kiểm có tồn tại | | Tham chiếu bảng người dùng |
| G-M7 | C | Validation theo giai đoạn: lỗi kiểu/min/max hiện trước, lỗi rule chỉ hiện sau khi sửa (80 triệu + ngày về < ngày đi chỉ báo 1 lỗi) | | Gộp lỗi một lần |
| G-M8 | B | **Không có định dạng seed/import có tham chiếu ký hiệu.** Seed dùng `@model:key`, `@user:`, `@dept:` do `tests/seedlib.py` giải; engine chỉ có CRUD | Bộ nạp trong test | `import.records` có dry-run và resolver khóa tự nhiên |

### IAM (policy language + Host)

| ID | Mức | Phát hiện | Cách né | Đề xuất |
|---|---|---|---|---|
| G-I1 | A | **`self_approval` cứng theo `creator`/`owner`.** Trong giao việc, người giao (creator thật) là người nghiệm thu hợp lệ nhưng bị chặn (ca `[GAP]` trong `results/iam.txt`) | Ánh xạ tài nguyên cho IAM: `creator` = người làm (`iam_resource_mapping`) | Cho policy khai quan hệ SoD (trường nào là "người làm") |
| G-I2 | B | Tập hành động cố định (6 mức trong `LEVELS`): không có `submit`, `escalate`, `assign`, `revoke`; `unknown_action` | Dùng `create/update/approve` | Đăng ký hành động theo pack với mức tự chủ |
| G-I3 | B | Thuộc tính tài nguyên chỉ `owner, creator, amount, team, department`: không có trạng thái, loại đề xuất. Không viết được "chỉ sửa khi còn nháp", "hạn mức 30 triệu chỉ với tạm ứng" | Rule ở model, kiểm trạng thái ở công cụ workflow | Mở rộng whitelist theo model |
| G-I4 | B | `subject.kind` không dùng được trong điều kiện; chặn agent dựa vào role tên `agent` gắn cho danh tính agent. Nếu danh tính agent thiếu role này, Deny không khớp (suy ra từ `Mini.evaluate`, không chạy; vẫn còn trần tự chủ, receipt, quyền người đại diện) | Quy ước + test | Subject pattern `kind:agent` |
| G-I5 | C | `subject.limit` mô hình hạn mức theo người, không hợp với chuỗi nhiều bước cùng một số tiền; pack không dùng | | |
| G-I6 | C | Ủy quyền khi vắng mặt (có trong `Host`) chưa được pack nào dùng/kiểm | | |
| G-I7 | B | Id statement phải duy nhất trong document; ba pack đều muốn `agent-l0-l1`, `deny-agent-decide`, nên phải gắn tiền tố pack khi gộp | Tiền tố `dx.`, `gv.`, `ns.` | Pack được namespace khi nạp |

## 7. Những gì chưa được kiểm chứng

- **Đồng bộ workflow → model** (cập nhật `de_xuat.trang_thai`, `buoc_hien_tai`, ghi `buoc_duyet`, `nhat_ky_sla`) ở pack 1 và 2: chỉ kiểm hai nửa riêng biệt (model/RLS/rule trên DB; process chạy với op giả `ghi_so`/`notify`). Chỉ pack 3 có op ghi DB thật (G-P11).
- **Đồng hồ thật**: SLA 24 giờ được thử bằng đồng hồ giả, không chờ thời gian thực.
- `pack.json` (`allowed_ops`, `lane_bindings`, `step_actions`, `process_inputs`, `iam_resource_mapping`) và khối `trigger` là **đề xuất**, không engine nào đọc.
- Tầng MCP (tool cố định), Keycloak (role, `azp`, MFA bước giám đốc), Telegram, MCP Apps, in PDF: ngoài phạm vi. Test dùng `Engine` và `Host` trực tiếp.
- Khả năng **agent tự sinh** các pack này qua `propose_model_change`: chưa thử. Pack do người (Claude) soạn tay, rồi mới kiểm.
- Ủy quyền khi vắng mặt, chặn cứng nợ quá hạn, ngân sách, duyệt song song, nhiều thực thể phụ thuộc nhau (G-P1, G-M4, G-I6).
- Hiệu năng, tải đồng thời.
- Dữ liệu seed là hư cấu (`synthetic=true`), không có dữ liệu người thật; ngưỡng 20 triệu hóa đơn điện tử là tham số cấu hình, chưa đối chiếu điều khoản pháp lý cụ thể.

## 8. SPDX, REUSE

JSON không có chú thích, nên **không gắn được header SPDX vào file JSON**. `packs-draft/REUSE.toml` có một annotation `path = "**"` phủ toàn bộ `packs-draft/**` (đường dẫn tính từ thư mục chứa file), `Apache-2.0`, `precedence = "override"`. File `.py`, `.sh` có header; `.md` có chú thích HTML. Không dùng sidecar `.license` để tránh nhân đôi file; REUSE.toml gốc của repo cũng đã phủ `research/**`.

## 9. Việc kế tiếp đề xuất

1. Quyết định G-P1 (song song), G-P3 (cổng quyết định), G-P4 (lane động), G-M1 (ghi do workflow quản lý): bốn mục quyết định hình dạng định dạng pack.
2. Viết `ProcessSpec` pydantic `extra=forbid` (G-P8) để cùng cấp bảo vệ với model spec.
3. Cho pack khai hành động IAM theo bước và quan hệ SoD (G-P7, G-I1) rồi chuyển harness `Instance.act()` thành mô-đun runtime thật.
4. Đưa 41 ca IAM, kịch bản process và các thăm dò khoảng trống vào CI (`tests/run_all.sh` đã làm được cả ba).
