<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->
# Kịch bản demo xT: SOP, phiếu xuất, phê duyệt và tồn kho

Căn cứ: [kế hoạch §9](../plans/2026-10-17-unified-xt-studio-waretrack.md#9-kịch-bản-demo-chốt), [kiến trúc](../diagrams/xt-unified-architecture.html), [harness E0–E12](../e2e.md). Thông điệp trình diễn: H giữ tài liệu và con người, P điều phối phê duyệt, D đọc dữ liệu, I hỗ trợ hỏi đáp; WareTrack là ứng dụng kho dùng chung lõi xT. Người đề xuất và người duyệt phải khác nhau.

**Trạng thái bằng chứng:** đây là hướng dẫn chạy và tiêu chí nghiệm thu, không phải biên bản PASS. Điền đường dẫn báo cáo thực tế sau khi chạy; không dùng kết quả lịch sử E0–E12 để kết luận E13 đã xanh. E13 dùng client MCP có script không tự chứng minh ChatGPT, chất lượng câu trả lời LLM hoặc toàn bộ thao tác UI hoạt động.

## Chuẩn bị stack riêng

Chạy tại thư mục gốc repo. Không đọc/in nội dung `.env`, không đưa mật khẩu, token, TOTP vào báo cáo. Không thay đổi project `xt-e2e-20261016`, dịch vụ systemd `xt-tunnel`, tunnel hay stack của phiên khác. Mọi lệnh `make` dưới đây đều giữ project và hai cổng riêng.

Trước khi dựng stack, chỉ đọc danh sách container và bộ nhớ. Cổng an toàn của phiên demo yêu cầu **không có tên container khớp `^xt-it-|xt-e2e-(uni|acc|sw|stg)` và RAM available ≥ 5 GiB**; nếu chưa đạt, đợi 30 giây rồi kiểm lại, không dừng container của người khác. `docker ps` lỗi thì dừng, không coi là danh sách rỗng. Chỉ một người chạy stack nặng/toàn bộ suite. Kiểm tra `df -h /` trước mỗi lần pull image > 1 GB.

```bash
free -g
docker ps --format '{{.Names}}'
df -h /
```

Sau khi cổng an toàn đạt:

```bash
cp /home/chinhan/xT/deploy/.env deploy/.env
env XT_E2E_PROJECT=xt-e2e-demo APISIX_HTTP_PORT=19180 KC_HTTP_PORT=18280 E2E_KEEP=1 make e2e-up
env XT_E2E_PROJECT=xt-e2e-demo APISIX_HTTP_PORT=19180 KC_HTTP_PORT=18280 E2E_KEEP=1 make demo-seed
env XT_E2E_PROJECT=xt-e2e-demo APISIX_HTTP_PORT=19180 KC_HTTP_PORT=18280 E2E_KEEP=1 make e2e-run E2E_ARGS='-k e13'
```

Seed provision Keycloak, tổ chức, người dùng, vai trò và các gói qua CLI `xt`; CRM được chủ động để lại cho E13 cài và kiểm vòng đời; không chèn SQL để giả nghiệp vụ. Cài gói theo hai lượt: `xt pack install --tenant <tenant> <pack-path> --proposed-by agent:demo-seed --approved-by person:<người-duyệt>` để lấy kế hoạch/digest, rồi cùng lệnh với `--confirm <digest-thực-tế>`. Chuỗi trong dấu `<...>` là tham số phải lấy từ dữ liệu thật, không chạy nguyên văn. Người duyệt phải được phân quyền và khác người đề xuất; digest không thay cho quyền duyệt. Kiểm tra báo cáo vì seed có thể ghi nhận lỗi gói rồi tiếp tục.

Harness định tuyến các hostname qua APISIX local `127.0.0.1:19180`. Mở hostname công khai trong trình duyệt thông thường có thể vào production: chỉ trình diễn web khi đã xác minh trình duyệt thử nghiệm trỏ đúng stack demo. Không chỉnh tunnel đang dùng. Tự động hóa GUI chỉ dùng headless hoặc Xvfb riêng, tuyệt đối không điều khiển/chụp display `:0` hay `:1`.

## Lời dẫn và màn hình trình diễn

Giữ lại mã SOP, kho, hàng hóa, vị trí, phiếu và tồn đầu kỳ từ lần chạy E13 thực tế. Mọi prompt dưới đây dùng các mã đó; không thay bằng số liệu minh họa rồi báo thành công.

| Bước §9 | Người và nơi trình diễn | Thao tác hoặc prompt | Assertion và bằng chứng cần đối chiếu |
|---|---|---|---|
| 1. Doanh nghiệp và đăng nhập | Quản trị; H, màn hình đăng nhập Keycloak | Mở doanh nghiệp demo, danh sách thành viên/vai trò; nhân viên đăng nhập và xem đúng tổ chức. | Tenant, subject và vai trò thật; người tạo khác người duyệt. E0 kiểm seed; E9 kiểm OIDC; E12 kiểm chuyển tenant. E13 cần ghi các danh tính sử dụng, không ghi bí mật. |
| 2. Đọc SOP và tạo phiếu | Nhân viên; H/Tài liệu, Kho/WareTrack tích hợp | Đọc SOP xuất kho, chuyển sang Kho, chọn loại Xuất, kho/hàng/vị trí và số lượng theo E13, bấm Tạo nháp. | SOP có ref/phiên bản, phiếu `stock_request` có ref; chưa ghi giảm tồn khi còn nháp. WareTrack phải mở trong cổng với cầu nối dữ liệu, không dùng tab độc lập hoặc chế độ mô phỏng để chứng minh tồn thực. |
| 3. Kiểm tra và duyệt | Nhân viên gửi phiếu; quản lý ở P/Chờ tôi duyệt | Gửi phiếu đi duyệt; mở đúng phiếu bằng phiên người quản lý và duyệt. Khi MCP trả `confirmation_required`, mở đúng liên kết xác nhận và hoàn tất step-up/TOTP. | Đầu vào sai bị từ chối; đúng tuyến duyệt; người tạo không tự duyệt; không có Decision khi chưa hoàn tất xác nhận. Không mô tả agent là người tự quyết định. |
| 4. Ghi nhận một lần | Quản lý; Kho, D và WareTrack tích hợp | Mở lại phiếu đã duyệt, làm mới số liệu Kho/D/WareTrack, đối chiếu tồn trước/sau; E13 gửi lại cùng idempotency key. | Phiếu approved; tồn giảm đúng số lượng xuất; chỉ một biến động cho phiếu; replay không giảm thêm. Ghi ref phiếu và ref biến động; đối chiếu cùng kho/hàng/vị trí, đơn vị `quantity_milli` (1.000 = 1 đơn vị). |
| 5. Hỏi số liệu và quy định | Nhân viên; ChatGPT có connector xT hoặc Studio | “Tồn hiện tại của [hàng] tại [kho/vị trí] là bao nhiêu? Phiếu [ref] đã xử lý thế nào? Dẫn nguồn SOP liên quan.” | Tool trả số liệu đúng với D và phiếu; dẫn nguồn trỏ đúng SOP người dùng được đọc. Lưu tool result và nguồn, không chỉ câu trả lời văn bản. Nếu chưa có phiên ChatGPT/Studio thực tế, ghi phần giao diện này chưa được kiểm chứng. |
| 6. AI tạo nháp | Cùng người dùng; ChatGPT/Studio | “Dựa trên SOP vừa dẫn, tạo nháp phiếu xuất mới [số lượng] của [hàng] tại [kho/vị trí]. Để người có quyền xem và quyết định bước duyệt.” | Nháp mới có ref thật; chưa có Decision hay biến động kho. MCP có thể chứng minh nháp và quyền; kiểm thử client có script không chứng minh LLM đã chọn đúng tool. |
| 7. Audit và sai quyền | Quản lý mở Lịch sử; phiên người dùng không đủ quyền | Xem người tạo, người duyệt, phiên bản và giao dịch của phiếu. Thử thao tác duyệt bằng người tạo hoặc đọc bằng tenant khác như E13 quy định. | Audit có tác nhân và sự kiện tương ứng, chuỗi kiểm hợp lệ; thao tác bị chặn có mã lỗi thật và không đổi trạng thái/tồn. Không sửa dữ liệu để tạo minh họa lỗi. |

## E13-BUG-01: agent không tạo được phiếu xuất

Lượt chạy thật đầu tiên ghi nhận lỗi tại `e2e/reports/20261018-105439/E13.json` (report local bị gitignore; báo cáo cuối sẽ được bổ sung riêng). MCP `create_record` cho `stock_request`, `kind=issue` trả `access_denied`, `details.reason=implicit_deny`, `details.principal=agent:xt-desk-agent`.

Nguyên nhân đã xác định tại `packages/kho-hang/policies/iam.json`: điều kiện `wh.request-write` so sánh `resource.owner` là UUID của người với `subject.id` là principal agent/client, nên không khớp trong phiên MCP thật. Helper `Warehouse.agent()` của integration test hiện dùng chủ thể HUMAN nên không tái hiện ranh giới này. Không đổi sang người gọi HUMAN hay nới quyền để làm demo xanh.

Hướng sửa đề xuất (ngoài phạm vi PR này): tách `role:agent` khỏi `wh.request-write` thành một statement riêng không điều kiện, như `apv.agent-l0-l1` của gói đề xuất–phê duyệt; `guard.py` vẫn đánh giá lại quyền của người được đại diện với điều kiện `owner == subject.id`. Cùng nguyên nhân còn ở `crm.request-write` (`de_nghi_giam_gia`), `cc.request-write` (cham-cong) và `ts.request-write` (tai-san); E13 không chạm các gói đó.

Rủi ro thứ hai chưa được chạy: `wh.deny-agent-approve` chặn `request.decide` của `role:agent`. Phần chuẩn bị dữ liệu của E13 vì vậy cấp cho quản lý kho một ủy quyền agent không có vai trò `agent` ở client phê duyệt (qua `xt org import`). Cần chủ sản phẩm quyết định đó là cách cấp quyền chủ đích hay xung đột chính sách với luồng xác nhận; kết quả của bước phê duyệt chỉ có giá trị sau khi sửa E13-BUG-01.

`test_demo_story` dùng `xfail(strict=True, raises=StockAgentOwnershipBug)`. Ngoại lệ riêng chỉ được phát khi đúng tool, model, `kind=issue`, mã lỗi, reason và principal nêu trên. Lỗi khác vẫn FAIL; nếu toàn bộ test chạy qua thì XPASS cũng làm suite thất bại để buộc bỏ đánh dấu lỗi đã biết. **XFAIL không phải PASS và không chứng minh luồng kho hoạt động.**

Điểm dừng là bước §9.2 tạo nháp phiếu xuất. Vì vậy chưa có bằng chứng chạy thành công cho nháp kho, gửi duyệt, phê duyệt/TOTP, chống tự duyệt của phiếu kho, biến động đúng một lần, tồn kho/D/lineage nhất quán, nháp tiếp theo không đổi tồn và audit tác nhân/phiên bản của phiếu (§9.3–7). Không dùng số dự kiến 10/4/6 để trình bày như số liệu đã được quan sát.

## Phạm vi mã kiểm thử E13

Mã: [`test_e13_demo_story.py`](../../e2e/scenarios/test_e13_demo_story.py). Hợp đồng sau khi sửa E13-BUG-01 yêu cầu nhập 10 đơn vị, xuất 4, tồn 6 (`6000` milli), hai biến động có tổng `6000`, replay cùng key không thêm biến động. `lineage_ref` được giải thích bằng `get_history(view="explain")` và truy vấn nguồn qua mọi cursor. Lineage là `current_data`, `historical_snapshot=false`, không phải snapshot lịch sử bất biến. Mã cũng có assertion nháp tiếp theo chưa phát sinh biến động và audit người tạo/người duyệt/revision; các assertion sau điểm lỗi hiện chưa chạy tới.

Hai test độc lập không đánh dấu xfail vẫn chạy và cần đọc kết quả riêng:

- `test_sop_document_source`: Files upload bytes thật; tạo/tìm/đọc `tai_lieu`; đối chiếu nội dung, file ref, ref và lưu phiên bản nguồn SOP.
- `test_crm_lifecycle_tenant_and_final_audit`: CLI cài/disable/enable CRM với proposer khác approver và digest thật; thử tự duyệt bị chặn; tạo khách hàng, chặn ghi khi tắt, ẩn app/model, admin đọc dữ liệu giữ lại, bật lại giữ ref/giá trị; thử đọc chéo tenant; kiểm audit và checkpoint có chữ ký.

Người trình diễn có thể đối chiếu danh mục ứng dụng và bản ghi khách hàng trước/sau thao tác CLI, nhưng chỉ công bố những assertion đã đạt trong report riêng. E13 không tự động bấm H/P/D/WareTrack hay gọi LLM/ChatGPT/Studio; nguồn SOP thật không đồng nghĩa đã kiểm RAG sinh câu trả lời có trích dẫn. Bảng §9 là mục tiêu trình diễn rộng hơn bằng chứng hiện có.

## Chạy kiểm chứng và lưu kết quả

Mỗi lượt pytest in thư mục `e2e/reports/<timestamp>/`; giữ `E13.json`, `E13.md`, `summary.json`, `summary.md` của lượt tương ứng. JSON chứa chi tiết bước và assertion; Markdown phục vụ trình bày. Không ghi đè report lượt riêng bằng lượt full. Chỉ kết luận PASS khi pytest trả 0, assertion đạt và không bỏ qua test cần thiết.

Chạy toàn bộ nhóm không LLM trên stack đã seed:

```bash
env XT_E2E_PROJECT=xt-e2e-demo APISIX_HTTP_PORT=19180 KC_HTTP_PORT=18280 E2E_KEEP=1 make e2e-run
```

Lệnh này loại marker `llm`, nên **không chứng minh E1 đã chạy**. Nếu nghiệm thu cả E1, chạy target sau trên stack sạch đã seed, **trước** suite có E10 (E10 cố ý lấp quota xác nhận), và vẫn giữ giới hạn một stack nặng:

```bash
env XT_E2E_PROJECT=xt-e2e-demo APISIX_HTTP_PORT=19180 KC_HTTP_PORT=18280 E2E_KEEP=1 make e2e-llm
```

E1 cần runtime/model thật theo [docs/e2e.md](../e2e.md). Không khởi động runtime nặng thứ hai khi chưa đủ tài nguyên. Báo cáo cuối tách rõ E13, toàn bộ non-LLM và E1; ghi lệnh, mã thoát, commit/source revision, đường dẫn report và lỗi còn lại. Báo cáo full mới có giá trị cho bản nguồn đã chạy, không suy rộng sau khi sửa code.

Dọn stack demo khi hoàn tất hoặc thất bại; `E2E_KEEP=1` không thay cho cleanup:

```bash
env XT_E2E_PROJECT=xt-e2e-demo APISIX_HTTP_PORT=19180 KC_HTTP_PORT=18280 E2E_KEEP=1 make e2e-down
```

Chỉ dọn container, volume và artifact do lượt demo tạo. Nếu cần dọn container test sót, xác minh ownership trước và dùng `docker rm -f -v <tên-container-demo>`; không xóa theo wildcard rộng. Không xóa bằng chứng cần bàn giao.

## Biên bản lượt chạy 18/10/2026

- Full non-LLM (`make e2e-run`, report `20261018-112219`, mã thoát 0): 31 passed, 1 deselected (E1/LLM), 1 xfailed (`test_demo_story`, E13-BUG-01). E0, E2–E12 PASS.
- E13 theo bước §9: đọc SOP (`test_sop_document_source`) PASS; Tier 3 CRM, chặn ghi khi tắt, giữ dữ liệu, tenant khác bị chặn, audit có chữ ký (`test_crm_lifecycle_tenant_and_final_audit`) PASS; tạo phiếu xuất XFAIL (E13-BUG-01). Tự duyệt phiếu, duyệt qua xác nhận, ghi biến động đúng một lần, báo cáo theo kho/tháng và lineage `explain` được viết nhưng **chưa được chạy** vì dừng ở bước tạo phiếu.
- E1/LLM: chưa chạy (`make e2e-llm` riêng).
- Web/ChatGPT/Studio: chưa có phiên trình diễn thật; bảng trên là kịch bản, không phải bằng chứng.
