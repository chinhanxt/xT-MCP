<!-- SPDX-License-Identifier: Apache-2.0 -->
# Nhật ký hợp nhất tài liệu (consistency pass)

Thứ tự thẩm quyền: `architecture-v2.md` (v2.1) và `architecture-v2-verdict.md` > `tool-contracts-v1.md` (v1.0.1) > storyboard, gói nháp. Không đụng `src/`, `skills/`.

## 1. `cli_command` trong build contract
- `research/tool-contracts-v1-build/contracts.py`: `CONFIRMATION_DETAILS` bỏ `cli_command`, `confirm_url` thành `string` (luôn có khi `pending`, mô tả theo v1.0.1); bỏ `"cli_command": None` ở `SAMPLE_ERRORS`.
- `research/tool-contracts-v1-build/template.md`: thêm khối changelog v1.0.1; dòng "Chế độ stdio" viết lại (không `cli_command`, theo D8); "6 đầu ra" thành "7 đầu ra" (thêm OpenAPI 3.1, CLI vận hành).
- Kiểm: build vào thư mục tạm (`gen_doc.py` trỏ ra `/tmp`) rồi `diff` với `docs/tool-contracts-v1.md`. Trước khi sửa, khác biệt đúng 5 điểm có chủ đích (changelog, 7 đầu ra, dòng stdio, schema `confirm_url`/`cli_command`, ví dụ `cli_command: null`). Sau khi sửa chỉ còn khác ở định dạng một dòng `confirm_url` (doc sửa tay thành một dòng); đã chép bản sinh ra vào `docs/tool-contracts-v1.md` nên rebuild giờ trùng 100%.
- Còn lại: `sizes.json` cũ (`confirmation details` 817 B; build lại ra 799 B). Chưa chạy lại `build.py` trong thư mục thật để không đổi bảng kích thước của doc.
- `architecture-v2.md` §5.5.1: "Yêu cầu TCV1 trả `cli_command=null`" thành "TCV1 v1.0.1 đã bỏ hẳn `cli_command`".

## 2. Làn PII của gói `nhan-su-vong-doi`
- Contract §3.8 / arch §5.7: PII đi theo làn (human full, local full, cloud masked hoặc omitted), không phải Deny IAM.
- `policies/iam.json`: bỏ statement `hr.deny-agent-pii`; thêm `xrn:binhminh:employee_pii:*` vào `hr.agent-l0-l1` (quyền thực tế vẫn là giao với quyền của người được đại diện, nên chỉ người có `hr_pii` hoặc chủ hồ sơ đọc được). Deny còn lại theo contract: `record.approve/delete/policy.activate` của agent, xóa hồ sơ.
- `pack.json`: mô tả role `agent`, mục `autonomy.L0`; thêm `pii_lane` (đề xuất nhãn `pii`/`sensitive` cho `employee_pii`, chờ engine, G-M2). `README.md` cập nhật tương ứng.
- `tests/run_iam.py`: ca agent đọc PII đổi từ `explicit_deny` thành `allow`; thêm ca agent thay người không có `hr_pii` (`behalf_permission`). Tổng 42 ca (trước 41).
- `demo-storyboard-v1.md` (S4 dự phòng, F-29, §12 mục 5): ghi đã hợp nhất. Còn mở: nhãn `pii` trong engine, panel giá trị thật ở Desk.

## 3. `confirmation_ref` trong `architecture-v2.md`
- `grep confirmation_ref docs/architecture-v2.md` rỗng. Đã sửa, không cần làm gì. `demo-storyboard-v1.md` §12 mục 9(a) ghi lại.

## 4. ClamAV
- `architecture-v2.md` §3: kiểm dịch ở profile `full`, mốc v0.2. `tool-contracts-v1.md` §3.9 và `template.md`: `clean` "(v1.0, sau ClamAV)" thành "(v0.2, profile `full` có ClamAV)". `demo-storyboard-v1.md` F-22 và §12 mục 9(b) cập nhật.

## 5. Ngưỡng MFA / step-up
- Nguồn chuẩn: process `proposal_approval` của gói `de-xuat-phe-duyet` (≤5 triệu: trưởng bộ phận; >5 triệu thêm giám đốc; >50 triệu thêm kế toán trưởng). Architecture chỉ nói "vượt ngưỡng", không nêu số. Chốt: step-up (Telegram chuyển thành `/confirm` + TOTP) khi số tiền >50.000.000 VND, không gắn với bước Giám đốc. Bản v0 ("MFA ở bước Giám đốc") bị thay.
- `de-xuat-phe-duyet/pack.json`: thêm `confirm_step_up` (`amount > 50000000`).
- `demo-storyboard-v1.md`: bỏ ghi chú "cần chốt"; biến thể ✂ của S2b đổi "tạm ứng 80 triệu" thành "thanh toán 80 triệu" (tạm ứng bị hạn mức 30 triệu chặn).
- `tool-contracts-v1.md` §3.5 (và template): câu Telegram nêu rõ ngưỡng mặc định. Ví dụ trong contract (8.000.000 VND, kênh `desk`/`telegram`) đã khớp, không đổi.
- `demo-storyboard-v0.md` không sửa (bản cũ, đã bị v1 thay).

## 6. Quy ước đặt tên
- `architecture-v2.md` không có điều khoản đặt tên. Engine spike yêu cầu `snake_case ASCII`; contract dùng id tiếng Anh. Chốt: id kỹ thuật tiếng Anh snake_case, tiếng Việt ở `label`/`name`/message.
- Ghi chú: `architecture-v2-verdict.md` T1-08/T1-13 dùng `den_ngay`, `tu_ngay`, `ma_du_an` trong kịch bản test (SME). Chưa sửa vì là tài liệu thẩm quyền; cần thay bằng `to_date`, `from_date`, `project_code` khi viết test thật.
- Đã đổi trong `research/packs-draft/` (script đổi tên thủ công có kiểm, dữ liệu ở `/tmp` không giữ lại):
  - Model: `loai_de_xuat`→`proposal_type`, `de_xuat`→`proposal`, `tam_ung`→`advance_request`, `thanh_toan`→`payment_request`, `hoan_ung`→`advance_settlement`, `buoc_duyet`→`approval_step`, `loai_cong_viec`→`task_type`, `cong_viec`→`task`, `cong_viec_muc`→`task_item`, `nhat_ky_sla`→`sla_log`, `ban_tin_tuan`→`weekly_digest`, `phong_ban`→`department`, `nhan_vien`→`employee`, `nhan_vien_pii`→`employee_pii`, `ho_so_vong_doi`→`lifecycle_case`, `viec_vong_doi`→`lifecycle_task`, `tai_san_cap`→`asset_issue`, `cap_quyen`→`access_grant`, `quyet_toan_nghi_viec`→`offboarding_settlement`.
  - Field (ví dụ): `so_tien`→`amount`, `ngay_di/ngay_ve`→`trip_start/trip_end`, `trang_thai`→`state`, `nguoi_de_nghi`→`requester`, `cccd`→`national_id`; mọi field, giá trị enum (`da_duyet`→`approved`, `cho_duyet`→`pending_approval`...), id ổn định (`dx_*`→`apv_*`, `gv_*`→`tsk_*`, `ns_*`→`hr_*`).
  - Role: `truong_bo_phan`→`dept_head`, `ke_toan_thanh_toan`→`payment_accountant`, `ke_toan_truong`→`chief_accountant`, `giam_doc`→`director`, `quan_tri`→`admin`, `nhan_su`→`hr`, `nhan_vien`→`employee`, `nguoi_giao/nguoi_thuc_hien`→`assigner/assignee` (giữ `hr_pii`, `it_admin`, `xt_workflow`, `agent`).
  - Process, step, op, biến: `de_xuat_phe_duyet`→`proposal_approval`, `giao_viec_thuong/khan`→`task_normal/task_urgent`, `nv_onboarding/offboarding`→`employee_onboarding/offboarding`, `kiem_tra_cong_no`→`check_overdue_advances`, `ghi_so`→`post_to_ledger`, step (`tbp`→`dept_head`, `gd`→`director`, `ktt`→`chief_acct`...).
  - Tiền tố id statement IAM: `dx.`/`gv.`/`ns.` thành `apv.`/`tsk.`/`hr.`; phần sau cũng sang tiếng Anh (ví dụ `nv-own-read`→`emp-own-read`).
  - Tên file model/process/seed theo id mới; `00_nguoi_dung.json`→`00_users.json`. Giữ nguyên: tên thư mục gói (`de-xuat-phe-duyet`...), mã phòng ban/nhân sự dữ liệu (`KD`, `NV-0001`), mọi nhãn/thông điệp tiếng Việt.
  - Test (`tests/*.py`) cập nhật theo; `REPORT.md` đổi tên theo và có ghi chú đầu trang.
- Kiểm chứng: `PY=/tmp/xt-packs-env/.venv/bin/python tests/run_all.sh` (postgres:17-alpine, cổng 15440, `df -h /` còn 8,2 GB, container tự xóa): models, db, iam, iam(merged), process đều exit 0; IAM 42/42 PASS cả hai chế độ. `results/*.txt` là output lần chạy mới.
- `demo-storyboard-v1.md` mục "Lưu ý đặt tên" đổi thành quy ước đã thống nhất.
