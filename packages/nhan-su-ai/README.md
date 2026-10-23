<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Pack `nhan-su-ai`: Nhân sự AI (AgentHR)

## Mục đích
Tuyển, giao việc, giám sát và cho nghỉ nhân viên AI (ChatGPT, Claude…) như một nhân viên thật: agent chỉ làm đúng JD, trong hạn mức và ca làm; nộp duyệt cần giấy phép do người mở; mọi lần bị chặn có dấu vết audit. Kế hoạch: `docs/plans/2026-10-20-agenthr.md`; quyết định: D-AGENTHR-1. Phụ thuộc `de-xuat-phe-duyet`, `nhan-su-vong-doi` (vai trò `it_admin`).

## Model (tên cố định theo hợp đồng dữ liệu dùng chung với Desk)
| Model | Primitive | Ghi chú |
|---|---|---|
| `vai_tro_agent` | record | Danh mục vai trò (mẫu JD): `nang_luc` [{model, thao_tac}], hạn mức, ca làm, `muc_tu_chu` mặc định (thêm) |
| `ho_so_agent` | record | `client_id` (azp) + `nguoi_phu_trach` (agent làm thay người này). `trang_thai` `cho_duyet`/`dang_lam`/`tam_dung`/`da_nghi`. Lõi ghi `so_viec_hom_nay`, `ngay_dem`, `jd_hieu_luc` (thêm, `managed_by: workflow`) |
| `jd_agent` | request | IT duyệt bằng `decide_request` (OTP trên Desk). `canh_bao` do lõi tính (`diff_risk`) khi tạo/sửa/nộp |
| `giay_phep_hanh_dong` | record | `hanh_dong` = `request.submit:<model>` hoặc `request.submit:*`; `tu_ngay`, `den_ngay` (thêm). `dieu_kien` là ghi chú cho người, lõi không diễn giải |
| `su_co_agent` | record | Lõi tạo (một sự cố mở cho mỗi loại); người xử lý `trang_thai`/`xu_ly` |

## Vòng đời
1. Tuyển: `create_record ho_so_agent` (luôn `cho_duyet`) + `create_record jd_agent` + `submit_request jd_agent`.
2. IT duyệt (`decide_request`, OTP). Quy trình `duyet_jd_agent` (v2, gói 0.1.1): bước `it` (role `it_admin`, giám đốc là người duyệt thay thế `alternates`, fallback giám đốc; IT tự lập JD thì giám đốc duyệt, D-DEMO-LEVELS-1) rồi op `apply_agent_jd`: hồ sơ `dang_lam`, `jd_hieu_luc` = JD này, `xt_meta.agent_grant` (người phụ trách, client) = `muc_tu_chu`, vai trò `["agent"]`. Người duyệt phải khác người lập JD và người phụ trách.
3. Đình chỉ: `update_record ho_so_agent {trang_thai: tam_dung, ly_do_tam_dung: thu_cong}` — lượt kế tiếp của agent bị chặn ngay.
4. Mở lại: chỉ qua JD mới được duyệt (sửa thẳng sang `dang_lam` bị từ chối `reopen_requires_jd_approval`).
5. Cho nghỉ: `update_record ho_so_agent {trang_thai: da_nghi}` — lõi xóa `agent_grant` của cặp (người phụ trách, client); hồ sơ đã nghỉ không mở lại được. Thu hồi consent/token Keycloak: việc tiếp theo (A3).

## Cổng lõi (dispatcher, chỉ cho agent có hồ sơ)
`agent_suspended` / `agent_terminated` (mọi tool) · `outside_working_hours` (giờ tenant) · `outside_job_description` (tool ghi; model của gói này luôn bị chặn) · `over_value_limit` + sự cố `vuot_gia_tri` (trường `money`) · `daily_limit_reached` + sự cố `vuot_han_muc` + tự tạm dừng (`han_muc`) · `held_for_release` + sự cố `giu_nhap` (nộp duyệt không có giấy phép; bản ghi giữ nháp). Mỗi lần chặn ghi `xt_meta.agenthr_event` (op `agenthr.block`, trong hash-chain audit). Agent không có hồ sơ: hành vi cũ.

## IAM
Người: `it_admin`, `director` ghi mọi model; `dept_head` ghi hồ sơ/JD/giấy phép/sự cố cùng phòng; IT/giám đốc quyết định JD; không ai xóa. `role:agent`: đọc được; **Deny mọi thao tác ghi** trên 5 model (`ai.deny-agent-write`), trừ client của Desk `xt-desk-agent` (Desk gọi MCP bằng client agent, D-UNI-AGENT-1) — vẫn giao với quyền của chính người dùng Desk.

## Dữ liệu demo
`seed/01_vai_tro_agent.json`: 6 vai trò (Trợ lý kho, Trợ lý kinh doanh, Chăm sóc khách hàng, Đề xuất mua, Thư ký (chỉ đọc), Trợ lý nhân sự (PII che)) trỏ model thật của `kho-hang`, `crm`, `de-xuat-phe-duyet`, `giao-viec-sla`, `cham-cong`, `nhan-su-vong-doi`. Nạp vào tenant demo ở bước A7.
