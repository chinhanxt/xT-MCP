# IAM của xT

Tài liệu cho `xt.domain.iam` (kiểu miền + evaluator), `xt.application.iam` (PEP) và `xt.contracts.iam` (schema, `details` của lỗi). Nguồn thiết kế: `docs/architecture-v2.md` §5.2 và §5.4, `docs/tool-contracts-v1.md` §3.5, spike `research/spike-iam.md`.

## 1. Phân lớp

| Lớp | Module | Nội dung |
|---|---|---|
| domain | `xt.domain.iam` | `PolicyDocument` (pydantic), `PolicyEvaluator`, `Glob`, `parse_xrn`, `Principal` (Protocol), `Actor`, `AccessRequest`, `Decision`, `Explanation`, `ApprovalReceipt`, `DelegationGrant`, danh mục mức rủi ro |
| application | `xt.application.iam` | `AccessGuard` (PEP), `PolicyStore` (vòng đời duyệt policy), cổng `PrincipalDirectory`, `ApprovalStore`, `DelegationStore` |
| contracts | `xt.contracts.iam` | `policy_json_schema()`, `explanation_json_schema()`, `access_denied_details()` |

Import-linter giữ hướng `adapters -> application -> contracts -> domain`. Evaluator không I/O, bất biến, an toàn đa luồng; tạo một lần cho mỗi `(tenant, revision)` rồi cache.

## 2. Policy document

```json
{
  "version": "1",
  "tenant": "acme",
  "statements": [
    {
      "id": "own",
      "effect": "Allow",
      "subjects": ["role:staff"],
      "actions": ["record.*"],
      "resources": ["xrn:acme:*:*"],
      "conditions": [{"left": "resource.owner", "op": "eq", "right": {"ref": "subject.id"}}]
    }
  ]
}
```

- Mọi khóa lạ bị từ chối (`extra=forbid`). Tối đa 2000 statement; mỗi statement tối đa 32 pattern mỗi loại và 16 condition.
- `subjects`: `*`, `role:<tên>` hoặc id thuần. Id không chứa `:` nên id người không thể giả mạo namespace role.
- `actions`, `resources`: pattern tối đa 256 ký tự, tối đa 8 dấu `*`, ký tự cho phép `A-Za-z0-9_.:*-`. `*` khớp toàn chuỗi, kể cả `:` và `.`; mọi ký tự khác là literal, phân biệt hoa thường. Không có regex nên không có injection hay backtracking.
- Condition `left op right` với `op` thuộc `eq, ne, le, ge, in`.
  - `left` thuộc whitelist: `subject.{id,limit,team,department}`, `resource.{owner,creator,amount,team,department}`.
  - `right` là literal (chuỗi, số hữu hạn, bool), `{"ref": "<thuộc tính>"}`, hoặc danh sách literal (chỉ với `in`).
  - `le`/`ge` chỉ cho thuộc tính số.
- Lấy JSON Schema để agent đề xuất policy: `xt.contracts.iam.policy_json_schema()`.
- Policy do người duyệt: `PolicyStore` gắn lượt duyệt với `policy_digest` (sha256 JSON chuẩn hóa). Người duyệt phải là người, `iam_admin`, cùng tenant, khác người đề xuất; đổi nội dung sau khi duyệt thì `active()` từ chối.

## 3. Quyết định

`PolicyEvaluator.evaluate(AccessRequest) -> Decision`:

1. XRN phải đúng `xrn:{tenant}:{type}:{id}` (không wildcard), tenant của actor = tenant của XRN = tenant của policy. Ngược lại: `invalid_xrn` hoặc `tenant_boundary`.
2. Mọi statement khớp subject, action, resource và condition đều là "hit".
3. Có hit `Deny` thì `explicit_deny`; còn hit `Allow` thì `allow`; không hit nào thì `implicit_deny`. Thứ tự statement không ảnh hưởng.
4. Fail-closed với condition không đánh giá được (thiếu thuộc tính, khác kiểu, NaN/Inf): statement `Allow` không khớp, statement `Deny` vẫn khớp.

`Decision.reason` là `Reason` (StrEnum, giá trị ổn định để ghi audit): `allow, delegated_allow, explicit_deny, implicit_deny, invalid_attributes, invalid_xrn, tenant_boundary, unknown_action, invalid_behalf, self_approval, human_only, autonomy_ceiling, approval_required, invalid_approver, approval_binding, approver_permission, invalid_delegation, delegation_scope, behalf_permission`.

## 4. PEP: `AccessGuard`

Thứ tự kiểm (IAM và SoD chạy trước xác nhận, tool-contracts §3.5):

1. Thuộc tính bản ghi: khóa có mặt phải đúng kiểu (`owner/creator/team/department` là chuỗi không rỗng, `amount` là số hữu hạn không âm). Hành động duyệt bắt buộc có `owner` và `creator`.
2. XRN và tenant (evaluator), rồi action phải có trong danh mục mức rủi ro (`unknown_action`).
3. Agent: `behalf` phải là người cùng tenant (`invalid_behalf`).
4. SoD: hành động duyệt (`record.approve`, `request.decide`) bị chặn nếu người tạo hoặc chủ bản ghi là actor hoặc người được đại diện (`self_approval`).
5. Agent và tự chủ: action `human_only` luôn bị từ chối (`human_only`); mức vượt trần của agent bị từ chối (`autonomy_ceiling`); L2 trở lên cần receipt của người khác (`approval_required`). Receipt phải do người cùng tenant, không phải actor, người được đại diện, người tạo hay chủ bản ghi; gắn đúng `(actor, behalf, tenant, action, xrn, revision, digest bản ghi)` và chưa hết hạn (`approval_binding`); người duyệt cũng phải đang có quyền theo policy (`approver_permission`).
6. Policy của chính chủ thể. Nếu có `delegation` và chưa bị Deny: ủy quyền chỉ cấp cho người, còn hiệu lực, đúng phạm vi và hạn mức (`invalid_delegation`, `delegation_scope`); quyền thừa hưởng từ người ủy quyền, và người ủy quyền cũng không được là người tạo/chủ khi duyệt.
7. Agent đại diện người: quyền hiệu lực là giao của agent và người được đại diện (`behalf_permission`).

Mức rủi ro: `DEFAULT_ACTION_LEVELS` (L0-L3, `human_only` cho `request.decide`; xem `xt.domain.autonomy.RiskLevel`). Đây là metadata của host, không bao giờ lấy từ tham số agent.

## 5. Explain

- `PolicyEvaluator.explain(req)`: vết từng statement (`subject/action/resource/conditions` đúng sai), từng condition với `actual` và `expected`, `near_misses` (Allow đã khớp subject/action/resource nhưng trượt condition).
- `AccessGuard.explain(req)`: thêm `stage` (`policy`, `boundary`, `attributes`, `behalf`, `sod`, `autonomy`, `approval`, `delegation`) và `detail` (ví dụ `{"required_level": "L2", "ceiling": "L1"}`).
- `access_denied_details(explanation, explain_ref)` tạo `details` gọn cho lỗi `access_denied`; vết đầy đủ chỉ trả qua `get_history(view="explain")`, và lời gọi đó phải được authorize riêng.

## 6. Tích hợp (việc còn lại cho lớp identity và dispatcher)

- `Principal` là Protocol (`id, tenant, kind, roles, team, department, limit, autonomy, behalf`). Adapter identity/DB cần ánh xạ `xt.domain.identity.Principal` cùng bảng tổ chức (vai trò, phòng ban, hạn mức, trần tự chủ, `behalf`) sang kiểu này; `xt.domain.iam.Actor` là hiện thực tham chiếu.
- Id XRN dùng ký tự `A-Za-z0-9_-` (tối đa 64 mỗi đoạn), chấp nhận cả chữ hoa như `TU-2026-0042`; pattern lowercase trong tool-contracts hẹp hơn nên vẫn hợp lệ.
- Kho receipt, delegation và danh bạ chủ thể là cổng (`ports.py`); cần adapter DB. Receipt phải dùng một lần ở lớp khác (`SingleUseStore`).

## 7. Kiểm thử

`tests/unit/iam`: 32 ca spike chạy trên evaluator thật và oracle pycasbin, nạp mọi `research/packs-draft/*/policies/*.json` và chạy lại 42 ca IAM của REPORT (riêng từng pack và gộp), đối chiếu ngẫu nhiên với pycasbin, test glob/schema/explain/PEP. Benchmark 10k: `uv run pytest -m slow tests/unit/iam/test_benchmark.py -s` (khoảng 200k eval/s ở 6 statement, 45k eval/s ở 206 statement cho evaluator).

## 8. Ghi chú sau audit

- `kind` chỉ đúng `"human"` mới là người; giá trị khác bị xử lý như agent (fail-closed).
- `AccessRequest.now` bắt buộc (keyword-only, epoch giây từ Clock của host).
- Policy JSON nạp qua `loads_policy_json` / `PolicyDocument.model_validate_json` (từ chối khóa trùng, NaN/Infinity); số nguyên trong policy |x| <= 2^53.
- `PolicyStore(doc, proposer: Principal)`: SoD so với người thật đứng sau agent.
- Thuộc tính `datetime` (có múi giờ), `date`, `Decimal` được chuẩn hóa trước khi đánh giá; explain không trả vết của principal khác người gọi.
