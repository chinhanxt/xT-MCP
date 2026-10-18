<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->

# Kiểm thử MCP trên ChatGPT — YYYY-MM-DD

Sao chép thành `docs/mcp-chatgpt-YYYYMMDD.md`. Điền từ quan sát thật; mặc định mọi ca
là `not_run`. Bản mẫu, fixture và dry-check không phải bằng chứng live.
Không đưa cookie, đường dẫn cookie, token, DSN, password/TOTP, URL xác nhận còn hiệu lực
hoặc PII gốc vào báo cáo. Tệp thô giữ ngoài repo, thư mục 0700, tệp 0600.

## Candidate và phạm vi

| Mục | Giá trị và bằng chứng |
|---|---|
| Run ID, người vận hành | CHƯA ĐIỀN |
| Bắt đầu/kết thúc (ISO 8601 có múi giờ) | CHƯA ĐIỀN |
| Commit, nhánh, cây làm việc sạch/bẩn | CHƯA ĐIỀN |
| Manifest nguồn thực triển khai và digest image | CHƯA ĐIỀN; HEAD đơn lẻ không đủ |
| Pack, phiên bản/digest, trạng thái app | CHƯA ĐIỀN |
| MCP | https://xt-mcp.freepro.online/mcp |
| Tenant alias / UUID | binhminh / CHƯA XÁC MINH |
| Browser/profile | Xvfb :90, test profile; CHƯA CHẠY |
| Runtime/provenance do người deploy cung cấp | CHƯA ĐIỀN |
| Baseline tồn, SOP, foreign ref, app âm | CHƯA XÁC MINH |
| Phạm vi thực chạy: ChatGPT / Studio / UI / DB | CHƯA CHẠY |

Ghi mọi thay đổi fixture trước lượt thử, người cho phép và đường nghiệp vụ đã dùng.
Không cấp grant tạm, maintenance trực tiếp hoặc seed lại để làm xanh kết quả.

## Danh tính trước mỗi nhóm ghi

| Giờ | Nhóm | Tenant/actor thật | Roles hiệu lực/client | Trace inspect_workspace | Kết quả |
|---|---|---|---|---|---|
| CHƯA ĐIỀN | requester / approver / self-probe | CHƯA ĐIỀN | CHƯA ĐIỀN | CHƯA ĐIỀN | not_run |

Ghi cách reconnect OAuth khi đổi actor và xác minh lại sau reconnect. Người tạo và
người duyệt phải khác nhau. Xác nhận password/TOTP diễn ra trong Desk do đúng người
duyệt thao tác; ChatGPT chỉ giữ args/key để lấy lại biên nhận.

## Câu chuyện demo (§9) và ca âm

| Ca | Kết quả cần quan sát | Host/UI | Server/DB | Trạng thái |
|---|---|---|---|---|
| 1 | Tạo doanh nghiệp/thành viên và SSO xuyên H/P/D/I/WareTrack | CHƯA ĐIỀN | CHƯA ĐIỀN | not_run |
| 2 | Đọc nội dung SOP, mở WareTrack và tạo phiếu xuất | CHƯA ĐIỀN | CHƯA ĐIỀN | not_run |
| 3 | P kiểm đầu vào; quản lý duyệt qua human confirmation | CHƯA ĐIỀN | CHƯA ĐIỀN | not_run |
| 4 | Một movement/dòng; cùng tồn trên WareTrack và D | CHƯA ĐIỀN | CHƯA ĐIỀN | not_run |
| 5 | ChatGPT/Studio đọc tồn, phiếu và quy định có nguồn | CHƯA ĐIỀN | CHƯA ĐIỀN | not_run |
| 6 | Nháp AI còn draft, chưa gửi/duyệt/ghi kho | CHƯA ĐIỀN | CHƯA ĐIỀN | not_run |
| 7 | Audit người tạo/người duyệt/phiên bản/giao dịch | CHƯA ĐIỀN | CHƯA ĐIỀN | not_run |
| N1 | Manager tạo phiếu riêng, tự duyệt bị từ chối; không decision/movement | CHƯA ĐIỀN | CHƯA ĐIỀN | not_run |
| N2 | Ref thật tenant khác: không lộ dữ liệu, revision không đổi | CHƯA ĐIỀN | CHƯA ĐIỀN | not_run |
| N3 | App đã disabled: capability_unavailable, reason=app_disabled; dữ liệu còn | CHƯA ĐIỀN | CHƯA ĐIỀN | not_run |
| N4 | PII tổng hợp có giá trị được che cả payload và lời AI | CHƯA ĐIỀN | CHƯA ĐIỀN | not_run |
| Retry | Args/key giữ nguyên, cùng receipt/ref; không thêm mutation/decision | CHƯA ĐIỀN | CHƯA ĐIỀN | not_run |

Trạng thái dùng: `not_run`, `blocked_prerequisite`, `missing_implementation`,
`host_blocked`, `pending_confirmation`, `server_rejected_expected`, `failed`,
`passed_with_evidence`. Nếu tool bị host chặn trước MCP, không có trace server và
không tính ca đó là server từ chối. `confirmation_required` chưa phải đã duyệt.

WareTrack hiện chỉ đọc và H cần model có binding document để hiển thị SOP. Ghi
`missing_implementation` nếu đường UI tương ứng chưa có trên candidate; tạo phiếu
qua MCP không đủ chứng minh đã tạo trong WareTrack. Ghi riêng phạm vi Studio chưa chạy.

## Tool, biên nhận và replay

| Ca/lần gọi | Actor/tenant | Tool + args đã làm sạch | Key | Ref/revision | Trace/error/receipt | Giờ |
|---|---|---|---|---|---|---|
| CHƯA ĐIỀN | CHƯA ĐIỀN | CHƯA ĐIỀN | CHƯA ĐIỀN | CHƯA ĐIỀN | CHƯA ĐIỀN | CHƯA ĐIỀN |

Lưu lần đầu, pending confirmation, lần lấy receipt và replay riêng. So sánh
`operation_ref`, `commit_state`, `replayed`, ref/revision và fingerprint args.
Không đổi expected_revision khi replay. Ghi rõ ca đổi payload/cùng key hoặc stale
revision đã chạy hay chưa; không suy từ không có bản ghi mới.

## Hậu kiểm độc lập

| Kiểm | Kết quả/nguồn |
|---|---|
| Plan đã bind dữ liệu thật, hash plan, cửa sổ thời gian | CHƯA ĐIỀN |
| Lệnh verifier (chỉ tên biến, không DSN), exit code | CHƯA CHẠY |
| PASS/FAIL table trạng thái server | CHƯA CHẠY |
| Mỗi mã phiếu đúng một bản ghi; audit count/revision | CHƯA CHẠY |
| Movement key/line/delta/principal, baseline và hai cấp tồn | CHƯA CHẠY |
| Decision, confirmed subjects, workflow/candidates | CHƯA CHẠY |
| Operation theo tenant/actor/tool/key, receipt commit | CHƯA CHẠY |
| Self-probe, foreign revision/mutation, disabled-model không sinh record | CHƯA CHẠY |
| PII egress trace/tool/fields/mode và đối chiếu payload thật | CHƯA CHẠY |
| xt_audit.verify toàn tenant (chain consistency) | CHƯA CHẠY |
| Neo audit ngoài DB có chữ ký, nếu áp dụng | CHƯA CHẠY; không suy từ consistency |

DB không có sổ bền chung cho mọi denial. Dùng cả envelope thật của MCP và bất biến
DB để kết luận ca âm. Metadata PII không chứng minh nội dung mọi kênh đã che; so
sánh riêng với fixture tổng hợp được phép dùng, không đưa giá trị gốc lên cloud.
Baseline tồn lấy trước thử; concurrent movement ngoài lượt thử làm đối soát FAIL,
cần giải thích bằng nguồn thật trước khi chốt.

## Manifest bằng chứng và bàn giao

| Mã tệp ngoài repo | SHA-256 | Nguồn: ChatGPT/UI/DB/fixture | Liên hệ run/ref/trace |
|---|---|---|---|
| CHƯA ĐIỀN | CHƯA ĐIỀN | CHƯA ĐIỀN | CHƯA ĐIỀN |

Chỉ đưa trích đoạn đã làm sạch vào repo. Nếu cần ảnh trong báo cáo, nhúng URL ảnh
đã duyệt và ghim SHA; không đưa ảnh display thật hoặc bí mật lên public.
Ghi app đã phục hồi qua luồng quản trị hay còn disabled theo kế hoạch; browser
do ai sở hữu và đã dọn PID của lượt này hay đang giữ theo bàn giao. Không dừng
public stack/tunnel trong lúc dọn browser.

Tóm tắt sau cùng: số ca đạt có bằng chứng, bị host chặn, thiếu triển khai, chưa
chạy và thất bại. Unit/fixture/SDK không chuyển thành live pass. PASS của verifier
là trạng thái DB trong phạm vi kiểm; §9 chỉ đạt khi cả host, UI và server đủ chứng cứ.
