# Audit IAM (bản port chưa commit)

Ngày: 2026-10-15. Người kiểm: Opus (tester/verifier độc lập). Phạm vi: `src/xt/domain/iam/`, `src/xt/application/iam/`, `src/xt/contracts/iam.py`, `tests/unit/iam/`, `docs/iam.md`. Đối chiếu với `docs/architecture-v2.md` v2.1 (§5.2–§5.5), `docs/tool-contracts-v1.md` (§3.5, XRN, `human_only`, explain), `research/spike-iam.md`, `research/packs-draft/*/policies`.

Không sửa `src/`. Probe tấn công nằm ở `/tmp/xt-iam-audit/` (`probe.py`, `probe2.py`, `test_property.py`). Test sẵn để thêm vào repo (file mới): `tests/unit/iam/audit/test_audit_findings.py` (13 ca, `xfail(strict=True)`) và `tests/unit/iam/audit/test_property_oracle.py` (hypothesis, tự skip nếu chưa cài).

## Kết luận

**ACCEPT-WITH-FIXES.** Lõi evaluator đúng. Deny > Allow, implicit deny, fail-closed của condition, glob, XRN, ranh giới tenant đều qua cả test tấn công lẫn property test 8000 ca so với pycasbin. Có 1 lỗi High ở PEP (B1) phải sửa trước khi nối identity và dispatcher, cùng 4 lỗi Medium và 2 lỗi Low.

## 1. Chạy lại test

```
cd /home/chinhan/xT && uv run pytest tests/unit/iam -q -p no:cacheprovider
```

| Lần chạy | Kết quả |
|---|---|
| Bộ gốc, trước khi thêm audit | **234 passed, 2 deselected** (2 benchmark `slow`), 13.3 s. Khớp với con số bàn giao. |
| Sau khi thêm `tests/unit/iam/audit/` | 234 passed, 1 skipped (module hypothesis khi chưa cài), 2 deselected, 13 xfailed |
| `uv run --with hypothesis pytest tests/unit/iam/audit` | 2 passed, 13 xfailed |
| `--runxfail` | 13 failed, mỗi ca fail đúng lỗi mô tả (đã kiểm từng traceback) |
| `ruff check`, `ruff format --check` (IAM và audit) | Sạch |

## 2. Bảng kiểm

| # | Hạng mục | KQ | Bằng chứng |
|---|---|---|---|
| C1 | Deny > Allow, không phụ thuộc thứ tự | PASS | `evaluator.evaluate` gom mọi hit rồi mới xét Deny. `test_deny_wins_regardless_of_statement_order`. Property: `allowed == (có matched) và (mọi matched là Allow)` |
| C2 | Implicit deny | PASS | Không có hit thì `IMPLICIT_DENY`. Policy rỗng thì từ chối (property, n=0 statement) |
| C3 | Condition của Allow lỗi thì KHÔNG allow | PASS | `_Rule.applies`: `None` chỉ khớp khi effect là Deny. Probe: amount `"7"` với `le`, `True` eq `1`, NaN, thiếu thuộc tính đều không cho Allow khớp |
| C4 | Lỗi khi đánh giá → fail-closed | **FAIL một phần** | Exception thoát ra ngoài thay vì trả `Decision` từ chối: B2 (OverflowError) và B7 (TypeError). Kết quả không phải allow, nhưng PEP không đóng gói lỗi |
| C5 | Glob `*` / `**` / biên | PASS | `**` tương đương `*`. `a*a` không khớp `a`, `ab*b*c` không khớp `abc`, `*` khớp chuỗi rỗng. Property 3000 ca: `Glob == fnmatch.fnmatchcase` (alphabet `ab:.*`) |
| C6 | Phân biệt hoa thường, unicode/homoglyph | PASS | Pattern và subject chỉ nhận ASCII `[A-Za-z0-9_.:*-]`. `record.аpprove` (chữ а Cyrillic), `аlice`, `\n` cuối chuỗi đều bị từ chối. Action phải khớp chính xác danh mục, nếu không là `unknown_action` |
| C7 | Chèn `:` hoặc `*` vào thành phần XRN | PASS | `parse_xrn` bắt đúng 4 đoạn `[A-Za-z0-9_-]{1,64}` bằng `fullmatch`. `xrn:acme:a:b:c`, `xrn:acme:a:*`, `xrn:acme:a:b\n`, chữ số full-width đều trả `None`. Id model chứa `:` không lọt được |
| C8 | Nhầm tenant (actor A, XRN B, policy wildcard `xrn:*`) | PASS | `check_boundary` đòi actor = XRN = policy tenant trước khi duyệt statement. Explain ở tầng boundary không có traces |
| C9 | Agent tự duyệt qua chuỗi ủy quyền/đại diện | PASS | Delegation chỉ cấp cho người. Chỉ đánh giá policy trực tiếp của người ủy quyền nên không có ủy quyền bắc cầu. Agent đại diện agent thì `invalid_behalf`. SoD xét cả actor lẫn người được đại diện |
| C10 | SoD: requester == approver qua id/email/hoa thường khác nhau | **CHÚ Ý** | So khớp chuỗi chính xác. Creator `Alice` hoặc `alice@acme.vn` so với actor `alice` vẫn được duyệt (N1). An toàn chỉ khi host luôn lưu `sub` chuẩn hóa |
| C11 | SoD khi duyệt policy | **FAIL** | B5 |
| C12 | `human_only` với agent "on behalf of" | PASS (PEP) | Agent `kind="agent"` luôn bị `human_only`, không phụ thuộc trần. **Nhưng** B1 cho phép vượt qua bằng `kind` khác |
| C13 | Kind không xác định | **FAIL** | B1 |
| C14 | Ngôn ngữ condition (simpleeval) | PASS / N/A | Lõi không dùng simpleeval, không `eval`. Condition là dữ liệu đóng: whitelist 9 thuộc tính, 5 op, `le/ge` chỉ cho thuộc tính số. Không truy cập được attribute, không có `str*int`, chi phí bị chặn (`MAX_*`). simpleeval chỉ là dep dev, kéo theo pycasbin. Riêng số khổng lồ: B2 |
| C15 | JSON Schema chặt (khóa lạ, kiểu) | PASS | `extra=forbid` ở mọi tầng. `"effect":"allow"`, `"version":1`, chuỗi cho `le`, bool cho `le` đều bị từ chối |
| C16 | Khóa JSON trùng | **FAIL** | B4 |
| C17 | Explain không lộ policy tenant khác | PASS | Boundary chặn trước khi tạo traces |
| C18 | Explain không lộ PII hay thuộc tính người khác | **FAIL** | B6 |
| C19 | Receipt gắn đúng thao tác, hết hạn | **FAIL một phần** | Binding gồm 7 trường, đúng. Hết hạn sai khi `now` để mặc định: B3 |
| C20 | Differential so với pycasbin | PASS | Property 8000 ca (policy 0–6 statement ngẫu nhiên, condition ngẫu nhiên gồm ref, `in`, khác kiểu) cộng 600 ca có sẵn. Không lệch, trừ lỗi của oracle O-1 |

## 3. Lỗi

### B1 — High: `kind` khác `"agent"` bị coi là người, đi vòng qua mọi cổng agent

- Vị trí: `src/xt/application/iam/guard.py`, `is_agent = actor.kind == AGENT`.
- Repro: `actor(id="bot", kind="service"|"Agent"|"", autonomy=0)` gọi `request.decide` hoặc `record.delete` khi không có receipt và không có `behalf` → **allow** (`tests/unit/iam/audit/test_audit_findings.py::test_b1_*`).
- Tác động: bỏ qua `human_only`, trần tự chủ, receipt L2/L3, kiểm `behalf` và phép giao quyền với người được đại diện. Rủi ro tích hợp cụ thể: `xt.domain.identity.PrincipalKind` định nghĩa `human` là "token auth-code qua một client", trong khi tool-contracts quy định token của connector (Claude, ChatGPT, Goose) luôn là `invoking_actor=agent(azp)`. Nếu adapter map thẳng `identity.kind` sang IAM, agent cầm token người sẽ được coi là người.
- Sửa: chỉ chấp nhận `kind in {HUMAN, AGENT}`, ngoài ra trả `INVALID_ATTRIBUTES`/`INVALID_BEHALF`. Đổi `is_agent` thành `kind != HUMAN`. Trong adapter, chỉ `azp=xt-desk` mới thành `human`.

### B2 — Medium: số nguyên khổng lồ gây `OverflowError` thoát khỏi PEP

- Vị trí: `math.isfinite(int)` trong `evaluator._kind` và `guard._is_number`.
- Repro: `resource.amount = 10**400`, hoặc `subject.limit = 10**400` khi so `ge` với ref, hoặc literal `10**400` trong policy (`StrictInt` không giới hạn) → `OverflowError`.
- Tác động: một policy đã duyệt có literal khổng lồ làm sập mọi lần đánh giá chạm tới statement đó (DoS). Lỗi không biến thành allow, nhưng caller nhận exception chứ không nhận `access_denied` + `explain_ref`.
- Sửa: giới hạn literal số trong `Condition` (ví dụ `|x| <= 2**53`). Trong `_kind` và `_is_number`, bắt `OverflowError` (hoặc kiểm `abs(v) <= 2**53` với `int`) rồi trả `None` hoặc `INVALID_ATTRIBUTES`.

### B3 — Medium: `AccessRequest.now` mặc định 0 làm receipt hết hạn vẫn còn hiệu lực

- Repro: receipt `expires=1`, request không truyền `now` → **allow** (`test_b3_*`). Với delegation cũng vậy: `start <= 0 < end` mở cửa sổ sai.
- Sửa: bỏ giá trị mặc định của `now` (bắt buộc truyền), hoặc guard trả từ chối khi `now <= 0`.

### B4 — Medium: khóa JSON trùng bị nuốt (last-wins)

- Repro: `{"effect":"Deny","effect":"Allow",...}` qua `model_validate_json` hoặc `json.loads` → statement thành **Allow** (`test_b4_*`).
- Tác động: người duyệt đọc JSON thô thấy `Deny`, còn digest và evaluator lại dùng `Allow`. Đây là kiểu tấn công lừa người duyệt khi agent đề xuất policy.
- Sửa: dùng một loader duy nhất `json.loads(raw, object_pairs_hook=_reject_duplicates)` rồi `model_validate`. Override `PolicyDocument.model_validate_json` để luôn đi qua loader này. UI duyệt hiển thị bản canonical (`model_dump`), không hiển thị raw.

### B5 — Medium: SoD duyệt policy so id agent thay vì effective human

- Vị trí: `PolicyStore(doc, proposer: str)`, kiểm `actor.id == self.proposer`.
- Repro: agent `bot` (`behalf=alice`) đề xuất, rồi alice (`iam_admin`) tự duyệt → được chấp nhận. So khớp còn phân biệt hoa thường (`Alice` khác `alice`).
- Spec: architecture-v2 §5.5 "SoD so theo **effective human**". `policy.activate` là thao tác L3/`human_only`.
- Sửa: nhận `proposer: Principal`, tính `effective = proposer.behalf if kind == AGENT else proposer.id`, rồi chặn khi `approver.id == effective`.

### B6 — Low-Medium: explain lộ thuộc tính của người duyệt (hạn mức)

- Repro: agent bị `approver_permission` → `AccessGuard.explain` dựng traces cho **người duyệt** carol, trong đó `expected = 31337` là `subject.limit` của carol (`test_b6_*`). Tương tự với người ủy quyền khi `delegated_allow`.
- Spike §196 đã cảnh báo trace lộ giá trị thuộc tính.
- Sửa: với `APPROVER_PERMISSION` (và donor của delegation), chỉ trả `reason`, `stage`, `principal`, không trả `traces`. Nếu vẫn cần traces thì che `actual`/`expected` của mọi `subject.*` khi principal khác người gọi.

### B7 — Low: resource không tuần tự hóa được JSON gây `TypeError`

- Repro: resource có `datetime` (thường gặp khi đọc từ Postgres) với agent L2 → `fingerprint` ném `TypeError` (`test_b7_*`). Cùng nhóm: `Decimal` (kiểu `numeric` của Postgres) cho `amount` bị `invalid_attributes`. Không phải lỗ hổng, nhưng mọi bản ghi tiền lấy từ DB đều bị chặn nếu adapter không ép kiểu.
- Sửa: bọc `ApprovalBinding.of` bằng try và trả `INVALID_ATTRIBUTES`. Adapter chuẩn hóa `Decimal`/`datetime` trước khi đưa vào IAM (hoặc `_is_number` nhận `Decimal` hữu hạn).

## 4. Ghi chú (không chặn)

- **N1** (SoD, hoa thường/email): id so khớp chính xác. Adapter phải ghi `owner`/`creator` bằng `sub` chuẩn hóa (UUID Keycloak), không bằng email/username. Pack `de-xuat-phe-duyet` dùng field `requester` (rule `final_approver != requester`), trong khi guard chỉ biết `owner`/`creator`. Cần ánh xạ rõ ràng `requester → owner`, và creator phải là effective human chứ không phải id agent.
- **N2** (`human_only` của agent): guard trả `human_only` (deny), còn tool-contracts §3.5 yêu cầu agent nhận `confirmation_required` + PendingOperation. Dispatcher phải chuyển đổi. Việc kiểm "chính người được giao bước" thuộc process engine, guard không làm.
- **N3** (`autonomy=True`): `bool` được nhận như `int` (trần L1). Nên từ chối bool.
- **N4** (receipt dùng lại): guard không đánh dấu receipt đã dùng. `docs/iam.md` §6 đã giao việc này cho `SingleUseStore`, nhưng phải có test tích hợp.
- **N5** (explain boundary): `detail` trả `policy_tenant`. Nếu policy được nạp theo tenant của actor thì giá trị này luôn trùng tenant của người gọi, nên không lộ gì. Chỉ cần giữ bất biến đó ở loader.
- **O-1** (oracle trong test): `support._oracle_cond` cho `"7" le "7"` là True vì không ép `le/ge` chỉ dùng cho số lúc chạy. Evaluator thật trả indeterminate, tức là evaluator đúng. Hypothesis phát hiện chỗ lệch này. `test_property_oracle.py` đang `assume` bỏ qua tổ hợp đó. Nên sửa oracle: thêm `if c.op in (LE, GE) and _kind(left) != "num": return None`.

## 5. Property test và giấy phép

- hypothesis 6.169.3, `License-Expression: MPL-2.0` (OSI). Đã thêm dòng vào `THIRD_PARTY_NOTICES.md` mục B (dev, tùy chọn). **Chưa** thêm vào `pyproject.toml` vì file này đang có agent khác sửa. Chạy bằng `uv run --with hypothesis pytest tests/unit/iam/audit`.
- Kết quả: glob so với `fnmatchcase` (3000 ca) và evaluator so với pycasbin (8000 ca, kèm bất biến Deny/implicit) đều không lệch.

## 6. Kiểm tra "ruff format có thể đã định dạng lại file identity"

`git diff --stat src/xt/adapters/identity src/xt/domain/identity.py` cho ra `src/xt/adapters/identity/__init__.py | 52 +++` (chỉ thêm: docstring và re-export). Các file còn lại (`access_token.py`, `config.py`, `introspection.py`, `jwks.py`, `jwt_core.py`, `receipts.py`, `resource_metadata.py`, `single_use.py`, `step_up.py`, `src/xt/domain/identity.py`) **chưa được track**, nên git không chứng minh được có lần reformat nào hay không. mtime nằm gọn trong 10:59–11:02, khớp với một đợt ghi hoặc format hàng loạt. `ruff format --check` hiện báo mọi file đã đúng định dạng. ruff format không đổi ngữ nghĩa, nên nếu có reformat thì cũng chỉ khác khoảng trắng và dấu ngoặc. Đây là WIP của agent khác nên tôi không động vào.

## 7. Điều kiện chuyển sang ACCEPT

1. Sửa B1 (bắt buộc trước khi nối identity → IAM), B2, B3, B4, B5.
2. Sửa B6 và B7, hoặc ghi rõ thành giới hạn đã biết.
3. Sửa xong lỗi nào thì gỡ `xfail` của lỗi đó trong `tests/unit/iam/audit/test_audit_findings.py`. `strict=True` sẽ báo XPASS cho tới khi gỡ.

## Phản hồi tác giả

Đã sửa B1-B7 và O-1; gỡ toàn bộ `xfail` (13 ca audit đều pass), thêm `hypothesis` vào nhóm dev của `pyproject.toml`.

- **B1:** chỉ `kind == "human"` là người; mọi giá trị khác (`service`, `Agent`, rỗng) đi cổng agent (bắt buộc `behalf` là người cùng tenant, `human_only`, trần tự chủ, receipt). Bool cho `autonomy` coi như L0 (N3).
- **B2:** literal số nguyên trong policy giới hạn |x| <= 2^53 (`MAX_SAFE_INT`); `_kind`/`_is_number` coi số lớn hơn là không đánh giá được. `AccessGuard.evaluate` bắt `OverflowError/TypeError/ValueError` và trả `invalid_attributes`.
- **B3:** `AccessRequest.now` là keyword-only bắt buộc, guard từ chối `now <= 0` hoặc không phải int.
- **B4:** `loads_policy_json` (object_pairs_hook, từ chối NaN/Infinity); `PolicyDocument.model_validate_json` luôn đi qua loader này.
- **B5:** `PolicyStore(doc, proposer: Principal)`; chặn người duyệt trùng id agent hoặc người được đại diện (so casefold).
- **B6:** explain không trả vết khi principal quyết định khác người gọi (người duyệt, người ủy quyền, người được đại diện); chỉ còn `reason`, `stage`, `statements`, `detail`.
- **B7:** `normalize_resource`: datetime có múi giờ -> ISO UTC, date -> ISO, Decimal hữu hạn -> int/float; fingerprint dùng cùng quy tắc. Bất đồng nhỏ: datetime **không múi giờ** bị từ chối (`invalid_attributes`) thay vì giả định UTC, vì giả định sai múi giờ làm sai hạn receipt.
- **N1, N2, N4:** thuộc adapter/dispatcher, chưa xử lý trong lõi.
