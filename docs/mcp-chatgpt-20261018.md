<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->

# Kiểm thử MCP trên ChatGPT — 18/10/2026

Lượt live 18/10 trên stack demo công khai: phase 1 (đọc) và phase 2 (kho) đều hoàn tất sau khi người deploy cấp agent grant cho NV-0014 (lần chạy đầu bị `blocked_prerequisite`, xem mục Lịch sử). Luồng nhập kho và xuất kho đi hết đường: tạo, gửi, duyệt `confirmation_required` → Desk password+TOTP của Trâm → replay → đúng một movement mỗi phiếu; tồn cuối 8000 milli. Một phát hiện sản phẩm: movement của phiếu xuất không có `occurred_on` nên báo cáo theo tháng bỏ sót nó (xem Ghi chú). Chưa chạy: SOP, WareTrack/UI, app disabled, PII có dữ liệu, nháp AI, ca manager tự duyệt đúng nghĩa; §9 chưa đạt toàn bộ.

## Candidate và phạm vi

| Mục | Giá trị và bằng chứng |
|---|---|
| Run ID, người vận hành | `20261018-k3x9` (phase 2); phase 1 `p1-20261018`. Điều phối viên giao việc, agent Sonnet thao tác browser |
| Bắt đầu/kết thúc (ISO 8601) | Phase 1 05:25Z–05:35Z; phase 2 lần 1 05:36Z–05:52Z; phase 2 lần 2 (sau #106) 05:58Z–06:10Z |
| Commit, nhánh, cây làm việc | Lần chạy đầu: backend `e2e2bae` (#103). Lần chạy sau: `e08cf0c` (#106), theo điều phối viên. Kit live `scripts/live` từ `staging`. Cây làm việc repo có tệp docs chưa commit (tệp này) |
| Manifest nguồn thực triển khai và digest image | Image `xt/xt:dev` `sha256:24b9abfdd01c3b034d064f2c598bb3b005c7c4926b04cda307aa53e2f31f293e`; chưa đối chiếu manifest 273 tệp trong lượt này |
| Pack, phiên bản/digest, trạng thái app | Phase 1: `kho-hang` 0.3.1, `cham-cong`/`crm`/`tai-san` 0.1.2, cả bốn `enabled` (`tr_eb2a603a340d43129394`). Sau #106 theo điều phối viên: `kho-hang` 0.3.2, ba pack còn lại 0.1.3; chưa lấy digest |
| MCP | https://xt-mcp.freepro.online/mcp |
| Tenant alias / UUID | `binhminh` / `0e090578-0240-4eaf-8c94-32455be07b56` (từ seed + DB); tenant đối chứng `hoamai` |
| Browser/profile | Xvfb :90, profile `chrome-profile`; CDP và noVNC chỉ loopback; còn chạy, đã đăng nhập, bàn giao cho điều phối viên |
| Runtime/provenance | Do người deploy cung cấp: stack `xt-e2e-20261016`, redeploy trước 12:21 +07 |
| Baseline tồn, SOP, foreign ref, app âm | Tồn trước lượt: **rỗng** (stock_balance/level/movement = 0). SOP: chưa chạy. Foreign ref thật: `advance_request:37d82766-664c-438d-bbda-d202889c24d5` (Hoa Mai, rev 1, xác minh có trong DB). App âm: chưa chạy |
| Phạm vi thực chạy: ChatGPT / Studio / UI / DB | ChatGPT: có. Studio, H/P/D/I/WareTrack UI: không chạy. DB: kiểm đọc độc lập (psql READ ONLY và `scripts.live.verify`) |

Thay đổi fixture trước lượt thử: kho `KHO-HN`, vị trí `A-01`, SKU `GIAY-A4`, vai trò demo và phiếu xuất `PX-8CA93F` (Dũng, 2 ram, `in_review`) do điều phối viên/người deploy tạo qua luồng sản phẩm trước lượt này. Lượt này không cấp grant, không maintenance, không seed lại.

Sự cố môi trường: 12:08–12:20 +07 Chrome gặp lặp `ERR_NETWORK_CHANGED` do docker bridge/veth đổi liên tục lúc redeploy; MCP trả 502/503 đến ~12:21 rồi ổn định. Không đụng docker.

## Danh tính trước mỗi nhóm ghi

| Giờ (UTC) | Nhóm | Tenant/actor thật | Roles hiệu lực/client | Trace inspect_workspace | Kết quả |
|---|---|---|---|---|---|
| 05:36 | Đọc baseline (requester) | binhminh / Phan Tiến Dũng `person:b81c6623-bedd-5e1e-83e1-a85c3c67c3e3` (NV-0013) | employee (+ sales, technician từ 05:47) / `xt-chatgpt-live-test`, L1, masked | `tr_dad718b3c2bc48309df0` | OK |
| 05:38 | Tạo + gửi phiếu nhập (requester) | như trên | như trên | `tr_950f165ab78541e7a3e9`, `tr_55660854fa87466da188` | OK |
| 05:43 | Approver Trâm (NV-0014) sau Reconnect OAuth | binhminh / Dương Bảo Trâm `person:ec1db5d6-2f90-5b3f-a5de-e877a3a64024` | — | `tr_77c54f94be534706a6ba` | **access_denied / implicit_deny** (lần 1, chưa có agent grant) |
| 05:47 | Ca âm (requester, reconnect lại Dũng) | binhminh / Dũng | employee, sales, technician | `tr_223cd1caa9584fb48774` | OK |
| 06:01 | Approver Trâm (NV-0014) sau #106, Reconnect + TOTP | binhminh / Dương Bảo Trâm `person:ec1db5d6-2f90-5b3f-a5de-e877a3a64024` | employee, sales, `warehouse_manager` / `xt-chatgpt-live-test`, L1 | `tr_b1f0b3281d304901990d` | OK |

Cách đổi actor: Settings → Plugins → xT → `...` → Reconnect → Connect xT; trước đó xóa cookie `xt-auth.freepro.online` của context để Keycloak không giữ SSO người cũ. Mật khẩu lấy từ file seed riêng tư, không in. Trâm sau #106 đăng nhập Keycloak bằng password + mã TOTP (secret trong file seed, không in) và qua màn consent. Danh tính sau reconnect được kiểm lại bằng `inspect_workspace`.

Lần 1: `xt_meta.agent_grant` của client `xt-chatgpt-live-test` chỉ có NV-0001, NV-0010, NV-0013; NV-0014 thiếu. Sau #106 Trâm có grant L1 và `inspect_workspace` OK.

## Câu chuyện demo (§9) và ca âm

| Ca | Kết quả cần quan sát | Host/UI | Server/DB | Trạng thái |
|---|---|---|---|---|
| 1 | Tạo doanh nghiệp/thành viên và SSO xuyên H/P/D/I/WareTrack | không chạy | — | not_run |
| 2 | Đọc SOP, mở WareTrack, tạo phiếu | không chạy (ChatGPT tạo phiếu qua MCP, không phải WareTrack) | — | not_run |
| 3 | P kiểm đầu vào; quản lý duyệt qua human confirmation | Trâm gọi `decide_request`: `confirmation_required` (level `human_only`, confirmer Trâm, `tr_1a94923291584791bf43`); Desk password+TOTP+Đồng ý lúc 06:05:12Z; replay hai lần `replayed=true`, `request_approved`, cùng `operation:20c28301d1404e9d8ce9e9007b3f26e6`, `confirmed_via=desk` | Phiếu nhập `approved` rev 3, 1 movement +10000 | passed_with_evidence |
| 4 | Một movement/dòng; cùng tồn | Phiếu xuất `PX-8CA93F` (Dũng, 2000): cùng chuỗi; Desk 06:08:16Z; replay hai lần cùng `operation:f6ff332371ce4cedbaf60a8f156406a9`, không movement thêm; `search_records` movement=1, -2000 | DB: đúng 1 movement mỗi phiếu, `approved_by` Trâm; `stock_balance`=8000, `stock_level.on_hand`=8000 (`below_reorder=true`) | passed_with_evidence (WareTrack/D chưa chạy) |
| 5 | Báo cáo tồn có nguồn | `aggregate_records` stock_balance: 8000 (`tr_9b0a48b8753f4fd9a084`); movement tháng 10: count 1, sum 10000 (`tr_dbe5f1bc640444b19260`); `get_history explain` cả hai `ok` | Khớp balance=8000. Báo cáo tháng thiếu movement phiếu xuất vì `occurred_on` NULL (phiếu không có `doc_date`) | passed_with_evidence cho tồn; **báo cáo tháng lệch**, xem Ghi chú |
| 6 | Nháp AI còn draft | không chạy | — | not_run |
| 7 | Audit tạo/duyệt/phiên bản | Phase 1 chỉ đọc; sau #106 `view=audit` yêu cầu `audit.read` (theo điều phối viên); chưa chạy lại | `xt audit verify` ok (xem Hậu kiểm) | một phần |
| N1 | Manager tạo phiếu riêng, tự duyệt bị từ chối | không chạy (cần grant cho NV-0014); thay bằng ca "người tạo tự duyệt" của Dũng, xem bên dưới | — | not_run |
| N1' | Người tạo (Dũng) tự duyệt phiếu của chính mình | `access_denied`, `details.reason=self_approval`, `tr_f1197c213a744ba48de0`; không `confirmation_required` | Không decision, phiếu vẫn `in_review` rev 2 (lúc đó) | server_rejected_expected. Dũng không có `warehouse_manager` nên chưa tách SoD khỏi IAM |
| N2 | Ref thật tenant khác | `get_record(advance_request:37d82766…)` (Hoa Mai) trả `access_denied`, `behalf_permission`/`implicit_deny`, không lộ dữ liệu, `tr_ab50d49f255145be924e`. Ref `person:` Hoa Mai và ref không tồn tại cho cùng mã lỗi (không phân biệt được) | verifier: foreign record giữ revision 1, không mutation trong cửa sổ | server_rejected_expected |
| N2' | Tạo phiếu với `workspace="hoamai"` | `capability_unavailable` ("workspace-scoped writes ... planned for v0.2"), `tr_d53f7468bc6e42049b31` | Không có `20261018-k3x9-BAD-XT` | server_rejected_expected, nhưng **không phải** kiểm cách ly tenant |
| N3 | App disabled: `app_disabled` | không chạy | — | not_run |
| N4 | PII che | Phase 1: `find_people` meta `pii.mode=masked`, kết quả không có email/phone; không có `masked_fields` nên không phân biệt "che" với "bỏ" | — | một phần, chưa có fixture PII có dữ liệu |
| CRM | `describe_model(khach_hang)` sau #106 | `ok=true`, 7 trường, `tr_843c3b2a0fdb4c578ce8` (trước đó `internal_error`) | — | passed_with_evidence |
| N5 | Thay mặt người khác (owner_id) | Ca A: `owner_id="person:…"` bị `access_denied` "Invalid owner_id" `bad_reference` (`tr_5398f8884afe49f18b89`), chỉ là lỗi định dạng. Ca A2: UUID thô: `access_denied`, `behalf_permission`, `owner_reason=implicit_deny` (`tr_be69f55fa25e4dcc8574`) | Không có `…-BAD-OBO`, `…-BAD-OBO2` (DB và `search_records`=0) | server_rejected_expected |
| Retry | Args/key giữ nguyên, cùng receipt/ref | create/submit/decide (nhập và xuất) replay: cùng operation/ref/revision; decide replay sau xác nhận không tạo movement/decision thêm | `tool_operation` mỗi key một dòng `done` | passed_with_evidence |

Host: ChatGPT hiện hộp "Suspicious Instruction" cho các lời gọi tạo phiếu âm (mã `BAD-OBO`, "thay mặt người khác"). Đã chọn **Allow once** (không Always allow) cho args đúng ca được phép: 05:47:57Z (ca A và B) và 05:49:07Z (ca A2). Các lời gọi sau đó có trace server nên không bị host chặn.

## Tool, biên nhận và replay

| Ca/lần gọi | Actor/tenant | Tool + args đã làm sạch | Key | Ref/revision | Trace/error/receipt | Giờ (UTC) |
|---|---|---|---|---|---|---|
| Đọc baseline | Dũng/binhminh | `search_records` stock_request, warehouse, location, stock_item, stock_balance, stock_level; `describe_model`, `describe_process` | — | 1 phiếu có sẵn `stock_request:0c8a207c-04ba-4b96-b62d-18ffaf25d3e9` (issue, `in_review`, rev 2, 2000 milli) | `tr_a7d68a16032f4c948749`, `tr_a133b12f9601423f83d9`, `tr_7499914d0eb249bfa525`, `tr_3bcb8ad45c234e93b8b7`, `tr_16f38bbac3184bc6a014` | 05:36–05:37 |
| Tạo phiếu nhập | Dũng/binhminh | `create_record` stock_request `{code:20261018-k3x9-IN, kind:receive, warehouse:b04d0694…, doc_date:2026-10-18, lines:[{item:c75f956e…, location:bf267c2b…, quantity_milli:10000}]}` | `20261018-k3x9-receive-create` | `stock_request:5d650a66-5c4e-42c6-b082-c3dab92e446f`, rev 1 | lần đầu `tr_ed384d7334f5443ab412`, `operation:74be0545e9e6410981932acc61299ead`, replayed=false; replay `tr_878d962c9fda4f06b592`, replayed=true | 05:38 |
| Gửi duyệt | Dũng/binhminh | `submit_request` expected_revision=1 | `20261018-k3x9-receive-submit` | rev 2, `in_review`, `process_instance:4002e803-d8fb-4d25-9b05-eb4fedd9bc8e`, bước `manager`, người duyệt Trâm | lần đầu `tr_96f8e687d2d54642a74d`, `operation:5ecb7e36f5a04e6ba06dc70051121d92`; replay `tr_e009159f104e43c3bfaf` replayed=true | 05:38–05:39 |
| Dũng tự duyệt | Dũng/binhminh | `decide_request` approve expected_revision=2 | `20261018-k3x9-neg-dung-self-approve` | rev 2 không đổi | `access_denied`/`self_approval`, `tr_f1197c213a744ba48de0`; `get_history explain` của `explain_ref`: `not_found`, `tr_e0292df83f9441088e54` | 05:39–05:40 |
| Trâm đọc (reconnect) | Trâm/binhminh | `inspect_workspace` | — | — | `access_denied`/`implicit_deny`, `tr_77c54f94be534706a6ba` | 05:44 |
| Ca âm thay mặt, tenant khác | Dũng/binhminh | `create_record` stock_request (owner_id / workspace=hoamai) | `…-neg-obo-create`, `…-neg-obo2-create`, `…-neg-xtenant-create` | không tạo | `tr_5398f8884afe49f18b89`, `tr_be69f55fa25e4dcc8574`, `tr_d53f7468bc6e42049b31`; đọc lại `tr_16804abbc77049f2a5a0`, `tr_9178747b37aa40ef9b47`, `tr_834484b53e164ad79e9c` (0 bản ghi) | 05:47–05:49 |
| Báo cáo | Dũng/binhminh | `aggregate_records` stock_balance theo kho; stock_movement tháng `2026-10` (`occurred_on`); `get_history explain` | — | — | `tr_fde9cae8cdf048bb9a46` (0 nhóm), `tr_bc571042e7834f5aaa3c` (0), explain `tr_2c8f02ad3f2f4156b5e0`, `tr_003ae81958b4490dbdc9` | 05:49 |
| Ref Hoa Mai | Dũng/binhminh | `get_record(advance_request:37d82766…)` | — | — | `tr_ab50d49f255145be924e` | 05:51 |

| Trâm duyệt phiếu nhập | Trâm/binhminh | `decide_request` approve expected_revision=2 | `20261018-k3x9-receive-approve` | rev 3 `approved`; movement `stock_movement:05b63ac1-1279-4c2f-ae27-2e821b71e513` +10000 | `confirmation_required` `tr_1a94923291584791bf43`; Desk 06:05:12Z; replay `tr_85e137a3b822465baff7`, `tr_6593a285c22a4ad29530`, `operation:20c28301d1404e9d8ce9e9007b3f26e6` | 06:01–06:06 |
| Trâm duyệt phiếu xuất | Trâm/binhminh | `decide_request` approve expected_revision=2 trên `stock_request:0c8a207c-04ba-4b96-b62d-18ffaf25d3e9` | `20261018-k3x9-issue-approve` | rev 3 `approved`; movement -2000 | Desk 06:08:16Z; replay `tr_e54c50230fc646689ac9`, `tr_3d5bd0a2996841a9a7d7`, `operation:f6ff332371ce4cedbaf60a8f156406a9` | 06:07–06:09 |
| Báo cáo + CRM | Trâm/binhminh | `aggregate_records`, `get_history explain`, `describe_model(khach_hang)` | — | — | `tr_9b0a48b8753f4fd9a084`, `tr_dbe5f1bc640444b19260`, `tr_cc62e01da6c04b09b7bd`, `tr_ab83d1e526d94a5d9b92`, `tr_843c3b2a0fdb4c578ce8` | 06:09 |

Lần 1 chưa chạy: lần `decide_request` của người duyệt kho, xác nhận Desk (password/TOTP) và replay quyết định; không có `confirmation_required` nào được tạo, nên không có URL xác nhận.

## Hậu kiểm độc lập

Lần 1 (sau bước 3–4) `verify.py` trả 18 PASS và 7 FAIL (bản trước ghi nhầm 17/8). Nguyên nhân từng FAIL và cách xử lý:

| FAIL | Nguyên nhân gốc | Xử lý |
|---|---|---|
| `operation/0` (create) | **Lỗi của verifier**: receipt `create_record` có ref ở `data.record.ref`, verifier đọc `data.ref` (fixture tự viết sai khớp với lỗi). `submit`/`decide` đọc đúng `data.request.ref` | Sửa `scripts/live/verify.py` và fixture, thêm test hồi quy |
| `warehouse/movements`, `warehouse/balances` | Định dạng plan chỉ cho một phiếu xuất (dòng âm), không biểu diễn phiếu nhập (+) và chuỗi audit tạo dòng tồn mới (INSERT 0, UPDATE 0→10000) | verifier: dấu dòng theo `kind` (receive +, issue −), nhiều phiếu qua `warehouse.also`, chuỗi tồn theo `posted_at`, bỏ INSERT khởi tạo |
| `warehouse/draft`, `negative/self`, `disabled/app`, `pii/egress` | Ca chưa chạy mà plan vẫn khẳng định | `self_request`, `foreign`, `disabled_pack`, `pii`, nháp `draft` thành tùy chọn: chỉ có check khi plan liệt kê |
| (phiếu xuất `PX-8CA93F` tạo trước lượt) | create/submit không thuộc lượt | record `baseline: true`: chỉ yêu cầu thao tác của lượt (`decide_request`) |

Kết quả chạy lại (verifier đã sửa, plan khớp cấu trúc thật: phiếu nhập +10000 làm phiếu đầu, phiếu xuất −2000 trong `warehouse.also`, ba bản ghi `BAD-*` bằng 0, `foreign`, `pii` trên `advance_request.bank_account`): cửa sổ 2026-10-18T05:38:00Z → ~07:12Z, **25 PASS, 0 FAIL, exit 0**, gồm `operation/0..3`, `warehouse/movements|decision|workflow` cho cả hai phiếu, `warehouse/balances` (8000 qua hai movement), `negative/foreign`, `pii/egress`, `audit/chain`. `disabled/app` không có trong plan vì không chụp trạng thái DB lúc crm tắt. Unit test `tests/unit/live`: 129 passed.

| Kiểm | Kết quả/nguồn |
|---|---|
| Truy vấn READ ONLY riêng | `stock_request` nhập và xuất đều `approved` rev 3; `stock_movement` +10000 (`occurred_on` 2026-10-18) và −2000 (`occurred_on` NULL vì phiếu không có `doc_date`, lỗi đã được sửa riêng ở #108 cho phiếu mới); `stock_balance`=8000; không có `20261018-k3x9-BAD%` |
| `xt audit verify --against-checkpoint latest` | rc 0, checkpoint `audit-checkpoint-20261018T062247113577Z.json`, 2/2 tenant toàn vẹn (binhminh seq 920, 751 sự kiện), catalog bảo mật không đổi |
| Neo audit ngoài DB | Có chữ ký qua luồng checkpoint của người deploy; checkpoint 06:22Z có trước các lượt CRM/PII sau đó |

## Ghi chú và việc cần quyết

1. **Lệch báo cáo tháng (phát hiện sản phẩm).** Phiếu xuất `PX-8CA93F` không có `doc_date`, nên movement `-2000` có `occurred_on` NULL. Báo cáo `stock_movement` theo `occurred_on` tháng 10 chỉ thấy +10000 (count 1) trong khi `stock_balance`=8000. Cần chọn: bắt buộc `doc_date` khi gửi, hoặc đặt `occurred_on` mặc định theo ngày ghi sổ.
2. Dũng không có `warehouse_manager`; ca SoD đúng nghĩa (manager tự tạo rồi tự duyệt) và `audit.read` sau #106 chưa chạy. Cần người quản lý kho thứ hai nếu runtime yêu cầu người duyệt khác ngay khi gửi.
3. `get_history(view=explain)` trên `explain_ref` của lỗi từ chối trả `not_found` cho chính actor; chỉ có `details.reason`.
4. Ca tenant khác: ghi với `workspace="hoamai"` trả `capability_unavailable` (không phải kiểm cách ly); ca đọc ref Hoa Mai thật bị `access_denied` mới là bằng chứng cách ly.
5. Hộp "Suspicious Instruction" của host xuất hiện ở các ca âm (mã `BAD-OBO`); chọn Allow once, không Always allow.

Lịch sử: lần chạy đầu bị `blocked_prerequisite` ở bước 3/4 vì NV-0014 chưa có agent grant (`implicit_deny` ở `inspect_workspace`, `tr_77c54f94be534706a6ba`); đã xử lý bởi người deploy trong #106. `describe_model(khach_hang)` từng `internal_error` ở phase 1 (`tr_ad0de04238144c6a803e` và hai trace khác), đã sửa. `view=audit` của employee từng đọc được ở phase 1 (`tr_f0b49cad52554cf39822`); đã đổi theo thiết kế sau #106.

## Bổ sung: SOP, tắt/bật app Tầng 3, PII trên dữ liệu thật (lượt 3, 06:35Z–07:10Z)

| Hạng mục | Kết quả | Trace | Trạng thái |
|---|---|---|---|
| (a) Đọc SOP/tài liệu | `describe_model(tai_lieu)` ok (primitive `record`; `tieu_de`, `tep` (file), `loai` bắt buộc); `search_records(tai_lieu)` trả 0; DB `tai_lieu`=0. Không tạo được SOP qua MCP vì `tep` là tệp bắt buộc và catalog không có tool tải tệp | `tr_2fffd37e33004208b1fc`, `tr_a9617be311cd4fa8ba5a`, `tr_e1115981cc974f999a59` | blocked_prerequisite (cần tải một tệp qua Desk/files API) |
| (b) Tắt crm | Người vận hành chạy `xt pack disable --tenant binhminh crm --proposed-by person:NV-0031 --approved-by person:NV-0001` rồi `--confirm` lúc 06:54Z. Lần đầu kế hoạch bị chặn: bốn gói cài trước khi có vòng đời ứng dụng (`kenh-pii`, `quan-tri-mo-hinh`, `tep-dinh-kem`, `thong-bao`: `depends_on` NULL) nên "chưa rõ phụ thuộc". Đã cài lại đúng bốn gói qua `xt pack install` hai bước (kế hoạch không đổi model, cùng phiên bản) rồi mới tắt được | — | thao tác vận hành |
| (b) Khi crm tắt (người đại diện Hoàng Quốc Bảo, NV-0010) | `inspect_workspace`: app `crm` vắng khỏi `apps`; `create_record(khach_hang)` → `capability_unavailable`, `details.reason=app_disabled`, `pack=crm`, `model=khach_hang`; `search_records` cũng `app_disabled`. Có trace server nên không phải host chặn | `tr_f6b21179fa7045e39c7a`, `tr_ada45e67dbda42138a6a`, `tr_66a2d09e3fa54fe088b9` | server_rejected_expected (`passed_with_evidence`) |
| (b) Bật lại crm | `xt pack enable … --confirm` lúc 06:58:04Z (dữ liệu giữ nguyên). `inspect_workspace` `crm` `enabled` 0.1.3; `search_records`, `aggregate_records`, `describe_model(khach_hang)` ok (0 bản ghi) | `tr_c3f874a883974b81b856`, `tr_e9b5cfc4365d4fb184ba`, `tr_0cb89a7f837b479e8cf1`, `tr_62f8aa7961734ef2801e` | passed_with_evidence |
| (c) PII dữ liệu thật | `advance_request.bank_account` (dữ liệu thật của các lượt 16–17/10): `search_records` cloud trả `meta.pii.mode=masked`, `masked_fields=["advance_request.bank_account"]`, bản ghi có giá trị hiển thị dạng token `[PII:…]`, các bản ghi còn lại `null`. Đối chiếu độc lập: giá trị gốc trong DB không xuất hiện trong transcript và payload tool; bảng `xt_meta.pii_egress` có đúng dòng `search_records`/`cloud`/`masked`/`{advance_request.bank_account}` (07:05:55Z); verifier `pii/egress` PASS | `tr_ddfc237e540a43b89cf7` | passed_with_evidence |
| (c) CCCD/điện thoại nhân sự | Không có dữ liệu để kiểm: `employee_pii` rỗng và `employee` có 4 dòng nhưng agent của Trâm, Bảo không thấy dòng nào (`total=0`); `find_people` không trả email/điện thoại | `tr_616419af8e5a46e1bd81`, `tr_3d5008442a674e6bb158` | thiếu fixture |
| CRM ghi | `create_record(khach_hang)` khi crm **đang bật** bị `access_denied`/`implicit_deny` cho cả Trâm (`tr_caef7376e19344e2932d`) và Bảo, vai trò `sales_manager` (`tr_1f445fa77e5f471a99a8`); nên chưa có khách hàng để kiểm PII phone/email của CRM | — | phát hiện: agent chưa tạo được CRM record |

Lưu ý: `describe_model(khach_hang)` liệt kê `kind`, `status` bắt buộc ở mô tả trường nhưng `required` của JSON Schema chỉ có `code`, `name` (ChatGPT ghi nhận khác biệt này). Connector được đổi giữa Dũng, Trâm, Bảo bằng Reconnect OAuth; token connector hết hạn sau khoảng 30 phút nhàn rỗi nên phải Reconnect.

## Manifest bằng chứng và bàn giao

Tệp thô (0600, ngoài repo) trong `/tmp/live/phase1/` và `/tmp/live/phase2/`: ảnh chụp màn hình, `sN-*.txt` (transcript), `*-calls.json`/`*-raw.json` (args + kết quả tool thô, chứa ref và PII đã masked), `plan.json`. Chưa tính SHA-256 từng tệp; liên hệ run/ref/trace nằm trong các bảng trên. Browser (Xvfb :90, CDP/noVNC loopback) giữ nguyên, đã đăng nhập, connector hiện đang là Dũng (NV-0013).

Tóm tắt: ca có bằng chứng đạt = tạo/gửi/duyệt/replay phiếu nhập và xuất, tự duyệt bị từ chối, thay mặt người khác bị từ chối, ref tenant khác bị từ chối, báo cáo+nguồn, CRM `describe_model`; thất bại/phát hiện = 1 (báo cáo tháng bỏ movement thiếu `occurred_on`); chưa chạy = SOP, WareTrack/UI, app disabled, PII có dữ liệu, nháp AI, SoD đúng nghĩa. Unit/fixture không chuyển thành live pass. PASS của verifier là trạng thái DB trong phạm vi kiểm; §9 chưa đạt toàn bộ.
