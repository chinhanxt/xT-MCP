# Kiểm chứng spike SpiffWorkflow + MCP auth

Ngày: 2026-10-15. Môi trường riêng `/tmp/xt-verify` (venv mới, cài lại `spiffworkflow==3.2.0`, `lxml 6.1.3`, `mcp==2.3.0`, PyJWT 2.15.1). Postgres `xtv-pg` cổng 15433, Keycloak `xtv-kc` (latest) cổng 18380, MCP server cổng 18390, fake issuer 18391/18392. Không sửa code gốc: mã được copy sang `/tmp/xt-verify`, chỉ đổi port/đường dẫn. Đã xóa container sau khi chạy.

## A. SpiffWorkflow

| Kiểm tra | Kết quả | Bằng chứng |
|---|---|---|
| t2 chạy 2 nhánh tiền + timer SLA | PASS | Output khớp báo cáo: 5tr chỉ cần TP, 50tr cần TP + GĐ, timer chỉ bắn sau khi gọi `refresh_timers()` |
| t3 lưu JSONB rồi reload ở process mới (đã có `_reorder`) | PASS | `pg_column_size=3705`, text 12897 B; load -> `ut_gd`; load2 hoàn tất; timer sống sót qua reload -> `ut_gd_esc` |
| Bẫy thứ tự key khi lưu JSONB (không có `_reorder`) | PASS (lỗi được tái hiện) | `order changed: True` -> `WorkflowTaskException: Error evaluating expression 'amount > 10000000'. Did you mean 'tp_decision'?` |
| t4 engine mặc định là RCE | PASS (tái hiện) | PWNED qua os.system, scriptTask, preScript, timer. Script test kiểm marker không đồng bộ (Popen) nên nhãn PWNED lệch 1 dòng, đúng như báo cáo đã ghi |
| t4 engine whitelist chặn 12 payload của spike | PASS | Mọi payload bị chặn, payload hợp lệ vẫn chạy, không có file PWNED |
| t5 versioning v1/v2 + poller `next_due` | PASS | A (v1) đi TP -> GĐ; B (v2) đi TP -> Kế toán; poller tìm thấy `[(3,)]` |
| t6 compiler + 6 spec xấu | PASS | Kết quả 5 luồng và 6 lỗi compile khớp báo cáo |
| Bộ tấn công mở rộng vào engine/compiler (dunder, format string, lambda/comprehension, walrus, starred, DoS lũy thừa/chuỗi, đệ quy sâu, unicode, data JSON, timer injection, XML injection qua role/step name, XXE) | CHƯA HOÀN TẤT | Không chạy trong phiên này. Mục dưới chỉ là nhận xét từ đọc code |
| `_reorder` với parallel gateway và vòng lặp | CHƯA HOÀN TẤT | Không chạy trong phiên này |

Nhận xét từ đọc code (chưa chạy test):
- `compiler.py`: tên role được chèn vào `name="{r}"` của lane mà không escape, và cũng không qua `ID_RE`. Đây là điểm thoát dữ liệu XML. Cần kiểm và sửa trước khi dùng thật.
- Các trường khác (`id`, `op`, `duration`, tên dùng `quoteattr`, điều kiện dùng `escape` + `check_expr`) có vẻ đã được kiểm, nhưng chưa test.

## B. MCP auth (Keycloak + SDK `mcp`)

| Kiểm tra | Kết quả | Bằng chứng |
|---|---|---|
| `kc_setup.py` (realm, user, scope, audience mapper, Trusted Hosts) | PASS | `realm/user/scope OK`, `trusted-hosts policy updated`, export OK |
| e2e: 401 -> PRM -> DCR -> PKCE -> consent -> token -> `whoami` | PASS | `aud=http://localhost:18390/mcp`, tools `['whoami']` |
| Negative: token hợp lệ / thiếu aud / token realm master / hết hạn / token rác / không token | PASS | 200 / 401 / 401 / 401 / 401 / 401 |
| DCR với redirect `evil.example` / `claude.ai` | PASS | 403 / 201 |
| Token có aud đúng nhưng `azp` là client khác | **FAIL (bị chấp nhận)** | Client `some-other-app`: `aud=[mcp, account]` -> 200. Lý do: scope `mcp-xt` là realm default, nên mọi client trong realm đều nhận aud của MCP, và server không kiểm `azp` |
| Dùng lại token sau khi disable user + logout | **FAIL (chấp nhận, đúng như dự đoán)** | Token mới bị từ chối (`Account disabled`), nhưng token cũ vẫn trả 200. Không có cơ chế thu hồi cho tới `exp` (900 s) |
| `alg=none` dùng kid thật | PASS | 401 `InvalidAlgorithmError` |
| HS256 ký bằng public key PEM của RSA | PASS | 401 `InvalidAlgorithmError` |
| Sửa payload nhưng giữ chữ ký cũ | PASS | 401 `InvalidSignatureError` |
| Dùng refresh_token / id_token làm bearer | PASS | 401 / 401 |
| Token tự ký (fake issuer) thiếu `exp` / `aud` / `iss` | PASS | 401 `MissingRequiredClaimError` |
| `nbf` hoặc `iat` ở tương lai, `iss` có thêm dấu `/` | PASS | 401 |
| Thiếu scope `openid`, hoặc `openidx` | PASS | 403 |
| `exp` +10 năm, claim `typ=Refresh`, thiếu `sub` | **FAIL (chấp nhận)** | 200. Server không giới hạn tuổi token, không kiểm `typ`, không bắt buộc `sub` |
| Xoay khóa: token ký bằng khóa mới ngay sau khi xoay | **FAIL tạm thời** | 401 trong khoảng cooldown 30 s của `PyJWKClient`; sau khoảng 40 s -> 200 (có refetch JWKS) |
| Token ký bằng khóa đã xóa khỏi Keycloak | **FAIL** | Khóa đã biến mất khỏi JWKS nhưng token vẫn 200 (lúc token được 20 s và 64 s tuổi) |
| Nhiều token mang kid ngẫu nhiên | PASS | 20 request -> 0 lần fetch JWKS thêm (cooldown hạn chế khuếch đại) |
| Token khóa đã xóa sau khi JWKS hết `lifespan` 300 s | CHƯA KIỂM | Không chạy kịp. Đọc code: `cache_keys=True` bọc `get_signing_key` bằng `lru_cache` không có TTL, nên khóa cũ có thể được tin cho tới khi restart process. Cần test xác nhận |

## Lỗ hổng đã xác nhận và việc phải sửa trước production

| # | Lỗ hổng | Mức độ | Cách sửa |
|---|---|---|---|
| 1 | Mọi client trong realm đều lấy được token dùng được cho MCP (không kiểm `azp`) | Cao | Bỏ `mcp-xt` khỏi realm default scope và chỉ gán cho client MCP/DCR đã duyệt; hoặc kiểm `azp` theo allowlist/danh sách client DCR. Tốt nhất là dùng realm riêng cho MCP |
| 2 | Không thu hồi được token: user bị disable vẫn dùng được tới khi hết hạn | Trung bình | Giảm `accessTokenLifespan` (5 phút); tại các tool ghi/duyệt, kiểm thêm trạng thái user (introspection hoặc cache sự kiện admin của Keycloak) |
| 3 | Khóa đã xóa vẫn được tin | Cao (khi lộ khóa) | Tắt `cache_keys` hoặc tự viết cache có TTL ngắn, xóa cache khi kid không còn trong JWKS; viết test xoay khóa |
| 4 | Không giới hạn tuổi token, không kiểm `typ`, không bắt buộc `sub` | Thấp | Bắt buộc `sub`, `iat`; kiểm `typ == "Bearer"`; từ chối nếu `exp - iat` lớn hơn trần |
| 5 | Token khóa mới bị từ chối tối đa 30 s sau khi xoay | Thấp | Đăng khóa mới ở chế độ passive trước khi active, hoặc giảm `cooldown_duration` |
| 6 | Tên role trong compiler không được escape (chỉ từ đọc code) | Cần xác nhận | Kiểm role bằng `ID_RE` và dùng `quoteattr` cho mọi giá trị chèn vào XML; thêm test |

## Việc còn mở

Bộ tấn công engine/compiler (bao gồm XXE với parser lxml của SpiffWorkflow), `_reorder` với parallel gateway và vòng lặp, và test khóa đã xóa sau 300 s chưa được chạy. Cần một phiên kiểm thử riêng trước khi chốt "engine an toàn".

## Phần A mở rộng (chạy thật, bổ sung)

Môi trường: cùng venv `/tmp/xt-verify/spiff`, Postgres `xtv-pg` cổng 15433. Mọi payload chạy thật qua `SafeEnvironment.evaluate` / `compile_spec` / `load_specs` (parser SpiffWorkflow).

### 1. Bộ tấn công engine AST-whitelist (33 payload)

| Nhóm payload | Kết quả |
|---|---|
| dunder attr (`amount.__class__`), `()...__subclasses__()`, `lst.__class__` | PASS chặn (node Attribute/Call) |
| format string (`'{0.__class__}'.format`, `'{}'.format`, `'{0.__init__.__globals__}'.format`) | PASS chặn (node Call) |
| f-string, f-string attr | PASS chặn (node JoinedStr) |
| lambda, lambda no-call, list/set/dict comprehension, generator | PASS chặn (Lambda/ListComp/SetComp/DictComp/Call) |
| walrus, starred in call, starred in list | PASS chặn (NamedExpr/Call/Starred) |
| `9**9**9`, `'a'*10**9`, `s*10**9` | PASS chặn (node Pow/Mult bị cấm trước khi tính) |
| `__import__`, `getattr`, `exec`, `eval`, `compile`, `open`, `.upper()`, `.bit_length()`, `b'x'.decode()` | PASS chặn (node Call) |
| `__builtins__` (tên), `d['__builtins__']` | PASS chặn (tên `_`; KeyError vì builtins trống) |
| unicode homoglyph `аmount` (Cyrillic) | PASS chặn (NameError, không có trong context) |
| 5 biểu thức hợp lệ (so sánh, in, index, ifexp, bool) | PASS chạy đúng |
| Không có file PWNED | PASS |

DoS phân tích riêng:
- Ngoặc lồng sâu: n=100 parse OK (vô hại, trả `1`); n>=500 -> `SyntaxError: too many nested parentheses` (bắt được, worker sống).
- BinOp 20k số hạng (`amount + amount + ...`) -> `RecursionError` lúc `ast.walk` trong `check_expr`, bị bắt -> fail-closed. **Minor:** `check_expr` không có trần độ dài biểu thức/số node ngoài giới hạn hằng chuỗi 200 ký tự. Nên thêm `len(expr) < N` và đếm node.

Kết luận: engine chặn 33/33 payload RCE/leak/DoS; chỉ còn minor thiếu trần số node (fail-closed nên không phải lỗ hổng thực thi).

### 2. XML injection ở compiler (role/step name)

| Payload | Kết quả |
|---|---|
| role chứa `< > & ' "` | PASS: lxml từ chối (`Unescaped '<' not allowed in attributes`) — fail-closed |
| role `x" onx="1` (thoát attr) | PASS: `Attribute onx redefined` |
| role `x"/><bpmn:userTask id="evil"/>` (chèn tag) | PASS: `Opening and ending tag mismatch` — KHÔNG inject được |
| role `x]]>y` (CDATA end) | PASS: well-formed, 0 node lạ, SpiffWorkflow parse OK |
| step name `< & " `, step name `"/><evil/>` | PASS: `quoteattr` escape đúng, 0 node lạ |

**Xác nhận bug role-name:** compiler emit `name="{r}"` cho lane KHÔNG qua `quoteattr` và KHÔNG kiểm `ID_RE`. Không gây code-injection (phá well-formed -> parser từ chối), nhưng:
- Là bug đúng-đắn: role hợp lệ nhưng có `&`/`<` (hiếm) sẽ làm compile chết.
- Severity: Thấp. **Fix:** kiểm mọi role bằng `ID_RE` ở đầu `compile_spec` và dùng `quoteattr(r)` cho `name`.

### 3. XXE / billion laughs trong parser BPMN (lxml 6.1.3)

| Tấn công | Kết quả |
|---|---|
| External entity `file:///tmp/.../secret.txt` qua `&xxe;` | PASS: `XMLSyntaxError: Entity 'xxe' not defined` — entity KHÔNG được resolve, không đọc file |
| Billion laughs (7 tầng) | PASS: `XMLSyntaxError: Maximum entity amplification factor exceeded` (libxml2 chặn sẵn) |

Lưu ý: SpiffWorkflow dùng `etree.fromstring(bpmn_str)` với parser lxml mặc định (`BpmnParser.add_bpmn_str`), KHÔNG tự cấu hình `resolve_entities=False`. Kết quả an toàn ở đây phụ thuộc mặc định lxml 6.1.3. **Khuyến nghị defense-in-depth:** trước khi đưa XML vào SpiffWorkflow, tự parse bằng parser cứng (`etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)`) và từ chối nếu có DOCTYPE; vì xT chỉ sinh BPMN từ compiler nên có thể cấm hẳn DOCTYPE/DTD ở đầu vào upload.

### 4. `_reorder` với parallel gateway + loop (chạy JSONB thật)

BPMN `par_loop.bpmn`: start -> parallelGateway fork -> (uA ‖ uB) -> parallelGateway join -> uLoop -> exclusiveGateway `n < 2` quay lại uLoop / ra end.

| Kiểm tra | Kết quả |
|---|---|
| Sau fork: 2 user task song song READY | PASS: `['uA','uB']` |
| Lưu JSONB (key bị sắp lại) rồi `from_state`: vẫn song song, giữ data | PASS: `['uA','uB']`, `marker='KEEP'` |
| Hoàn tất 2 nhánh (reload trước mỗi bước) -> join | PASS: READY `uLoop`; live task giữ `uA_done=uB_done=True`, `marker` |
| Vòng lặp n=0->1->2, serialize/reload mỗi vòng, gateway dừng ở n=2 | PASS: lặp đúng 2 lần, `completed=True` |

`_reorder` (BFS cha-trước-con) xử lý đúng cả nhánh song song và vòng lặp, không chỉ tuyến tính. (Lần chạy đầu thấy "n không tăng" là do test đọc `wf.task_tree.data` ở root giữa chừng; đọc từ task đang active thì đúng.)

### 5. JWKS lru_cache sau khi xoay/xóa khóa (bổ sung chứng minh vuln #3)

Chứng minh từ source PyJWT 2.15.1: với `cache_keys=True`, `get_signing_key` bị bọc `functools.lru_cache(maxsize=16)` **không có TTL**. `lifespan=300` chỉ áp cho cache của cả JWK **Set**, không áp cho cache từng kid. Hệ quả: một kid đã resolve thành công sẽ được tin **cho tới khi bị LRU đẩy ra (16 kid khác) hoặc restart process**, bất kể khóa đã bị xóa khỏi Keycloak hay `lifespan` đã hết. Khớp quan sát thực tế ở Phần B (token khóa đã xóa vẫn 200 ở tuổi 20 s và 64 s). Nâng đánh giá vuln #3: **tắt `cache_keys` (chỉ giữ cache Set có TTL), hoặc tự viết verifier xóa cache kid khi kid không còn trong JWKS**.

### Tổng kết Phần A
Engine AST-whitelist: an toàn trước toàn bộ battery (33/33), chỉ còn minor thiếu trần số node. XXE/billion-laughs: chặn (nhờ lxml mặc định, nên thêm parser cứng cho đầu vào upload). Compiler: không inject được, nhưng bug role-name (Thấp) cần sửa bằng `ID_RE`+`quoteattr`. `_reorder`: đúng với parallel + loop. Vuln JWKS lru_cache (#3) được xác nhận nghiêm trọng hơn ở mức source.
