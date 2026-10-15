# Bài học từ sklite để thiết kế xT bằng Python

Ngày khảo sát: 15/10/2026. Nguồn: snapshot tại `/tmp/sklite_src/sklite-main`; thư mục này không có metadata Git để xác định commit. Mọi dẫn chiếu `đường/dẫn:dòng–dòng` dưới đây tính từ gốc snapshot này. Kết luận về hành vi dựa trên mã nguồn, không phải kiểm thử một hệ thống đang triển khai.

**Phạm vi và cách sử dụng:** đối chiếu `internal/{iam,queue,audit,connector,tenant,fulfillment}`, các luồng liên quan trong `cmd/api`, migration, worker/scheduler, `PRD.md`, toàn bộ `docs/*.md`, và tài liệu/template của `.claude/skills/mcp-scaffold`. Không thấy file LICENSE trong snapshot. Theo yêu cầu chỉ học pattern, các đoạn logic quan trọng được diễn giải và viết lại độc lập bằng Python/SQL; không chép nguyên văn hàm Go, template TypeScript hay migration. Đây là ghi chú thiết kế, không phải bản port đã chạy.

**Định hướng:** xT có thể giữ mô hình một Postgres, tách tiến trình MCP/API, worker và scheduler. Phần cần tái hiện là giao kèo giữa IAM, tenant context, connector và từng bước thực thi. Không nên mặc định các nhãn “production-ready” trong tài liệu là bảo đảm cho những giao kèo này. PRD nêu rõ queue bền vững, permission theo action và pipeline cấu hình được (`PRD.md:10–15`); ranh giới tiến trình nằm ở `PRD.md:58–61`, `docs/00-architecture.md:68–86`.

## 1. IAM evaluator: policy, Deny > Allow, wildcard và TRN

### Cấu trúc và thuật toán thực tế

| Thành phần | Hành vi trong sklite | Bằng chứng |
|---|---|---|
| Principal | Cặp `UserID`, `TenantID` gắn vào request context; auth middleware lấy từ JWT loại `access` | `internal/tenant/tenant.go:21–35`; `internal/httpx/auth.go:79–102` |
| Policy | JSON có `version`, `statements`; mỗi statement có `effect`, mảng `action`, mảng `resource` | `internal/iam/policy.go:17–35` |
| Parse | Version thiếu thành `"1"`; chỉ kiểm tra effect Allow/Deny và hai mảng không rỗng | `internal/iam/policy.go:37–59` |
| Persistence | `iam_policies` chứa document JSONB, duy nhất `(tenant_id,name)`; `user_policies` là bảng gắn nhiều policy cho user | `internal/db/migrations/0002_iam.sql:2–19` |
| Load | Join attachment → policy → user, yêu cầu policy và user cùng tenant; nhận `userID`, không nhận tenant hiện hành | `internal/iam/loader.go:12–39` |
| Match | Trong một statement: tồn tại action khớp **và** tồn tại resource khớp | `internal/iam/policy.go:102–111,123–129` |
| Decision | Duyệt mọi policy; Deny khớp trả false ngay; Allow chỉ ghi nhận, vẫn phải duyệt tiếp để tìm Deny; cuối cùng trả cờ Allow | `internal/iam/policy.go:100–120` |
| Enforcement | Helper tải policy rồi evaluate; không có principal hoặc bị từ chối thì 403, lỗi tải policy thì dừng với lỗi server | `internal/httpx/authz.go:19–46` |

Đây là semantics lấy cảm hứng từ IAM, chưa phải toàn bộ AWS IAM. PRD nhắc `Condition`, nhưng struct/parser thực tế không có trường này (`PRD.md:84–89`; `internal/iam/policy.go:25–35,38–58`). JSON field lạ không bị kiểm tra ở parser hiện tại. xT phải từ chối `condition` chưa hỗ trợ thay vì âm thầm biến một Allow có điều kiện thành Allow vô điều kiện. Cũng nên từ chối version lạ, chuỗi rỗng và action không hợp lệ.

Ví dụ policy tự viết cho xT, dùng tên action giả định của operation registry; statement Deny có thể nằm ở policy khác mà vẫn thắng Allow:

```json
{
  "version": "1",
  "statements": [
    {"effect": "Allow", "action": ["workflow.*"],
     "resource": ["trn:018fa011-0234-7000-8000-000000000001:workflow:*"]},
    {"effect": "Deny", "action": ["workflow.delete"],
     "resource": ["trn:018fa011-0234-7000-8000-000000000001:workflow:*"]}
  ]
}
```

### Wildcard: cần đọc thân hàm, không chỉ comment

`matchPattern` tách pattern theo `*`, tìm các đoạn literal theo thứ tự, neo đoạn đầu và kiểm tra hậu tố đoạn cuối (`internal/iam/policy.go:61–89`). So khớp phân biệt hoa thường. Hai wrapper truyền dấu `.` và `:`, nhưng tham số `sep` **không được dùng** (`internal/iam/policy.go:64,92–97`). Vì vậy `*` thực tế có thể vượt ranh giới dấu chấm hoặc dấu hai chấm.

Còn một lỗi tinh vi: pattern **không có `*`** chưa được buộc phải bằng toàn bộ target. Theo luồng dòng 69–88, pattern `workflow.read` vẫn nhận `workflow.read.extra.workflow.read`: đoạn đầu khớp prefix và cùng literal cũng là suffix. Đây là phản ví dụ suy ra trực tiếp từ thuật toán; chưa khẳng định có đường khai thác trong API đang chạy. Khi port, giữ ý nghĩa wildcard đã định nghĩa, không tái hiện lỗi matcher.

TRN có dạng `trn:{tenant_id}:{resource_type}:{resource_id}`. Hàm build chỉ nối chuỗi, không xác thực UUID, thành phần hoặc dấu phân cách (`internal/iam/policy.go:132–136`). Policy owner Allow mọi action trên `trn:{tenant}:*:*`; các policy khác gom quyền product/template/media, order đọc và event check-in (`internal/iam/actions.go:48–100`). xT nên tạo TRN ở host từ tenant đã xác thực, validate từng thành phần; không nhận một TRN tùy ý từ code của agent để quyết định tenant.

### Đoạn logic quan trọng viết lại bằng Python cho xT

Ví dụ sau nhận policy **đã được schema-validate**. Nó chọn semantics `*` khớp toàn chuỗi, như ý định broad glob của sklite, đồng thời sửa lỗi literal ở trên. Nếu muốn wildcard chỉ khớp từng segment, phải đổi thành một version ngôn ngữ policy riêng và test migration quyền.

```python
import re
from dataclasses import dataclass

@dataclass(frozen=True)
class Rule:
    effect: str
    actions: tuple[str, ...]
    resources: tuple[str, ...]

def star_match(pattern: str, value: str) -> bool:
    expression = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.fullmatch(expression, value, flags=re.DOTALL) is not None

def decision(rules: list[Rule], action: str, resource: str) -> str:
    matched_effects = {
        rule.effect
        for rule in rules
        if any(star_match(p, action) for p in rule.actions)
        and any(star_match(p, resource) for p in rule.resources)
    }
    if "Deny" in matched_effects:
        return "explicit_deny"
    return "allow" if "Allow" in matched_effects else "implicit_deny"
```

Mã trên là minh họa semantics; bản production cần giới hạn số statement, độ dài pattern/số dấu `*`, tránh chi phí regex không giới hạn, và compile/cache pattern theo revision policy. Lớp ngoài phải kiểm tra `principal.tenant_id == resource.tenant_id` trước evaluator: ngay cả policy `resource=["*"]` cũng không cho một tenant principal vượt tenant boundary. Platform operator đi qua một đường quản trị riêng.

Đề xuất cho xT: dùng model immutable cho principal/policy/TRN; repository tải policy bằng **cả tenant và principal** trên connection đã đặt tenant. Cache theo `(tenant_id,principal_id,policy_revision)` và vô hiệu hóa khi gắn/gỡ/sửa quyền. Registry operation khai báo action/resource resolver bắt buộc; mỗi RPC do code sandbox yêu cầu phải qua evaluator, kể cả khi không xuất hiện trong kết quả search. Quyền sửa policy/gắn quyền cần giới hạn quyền được ủy nhiệm để tránh tự cấp quyền rộng hơn.

Test nguồn hiện có bao phủ Allow, implicit Deny, Deny đè Allow, wildcard và tenant scope (`internal/iam/policy_test.go:14–113`). Bộ test xT cần thêm: đảo thứ tự policies vẫn cùng kết quả, literal-prefix/suffix phản ví dụ, wildcard vượt segment theo semantics đã chọn, field/version lạ bị từ chối, broad Allow không vượt tenant, policy bị thu hồi giữa hai step và các operation thiếu khai báo quyền.

## 2. Postgres queue với SKIP LOCKED làm nền chạy workflow

### Schema bảng jobs hiện tại

Nguồn chuẩn là `internal/db/migrations/0006_queue.sql:2–19`; khác sketch PRD ở chỗ `tenant_id` **nullable**, không có FK hoặc RLS trên bảng này trong các migration đã khảo sát.

| Cột | Kiểu/default | Ý nghĩa |
|---|---|---|
| `id` | BIGSERIAL, primary key | Job vật lý |
| `tenant_id` | UUID, nullable | Tenant sở hữu; có thể trống |
| `kind` | TEXT, bắt buộc | Định tuyến handler, hiện có `fulfillment.step` |
| `payload` | JSONB, mặc định `{}`, NOT NULL | Tham số job |
| `status` | TEXT, mặc định `queued` | Trạng thái, chưa có CHECK enum |
| `attempts` | INT, mặc định 0 | Số lần đã claim trong DB |
| `max_attempts` | INT, mặc định 5 | Tổng số lần thử tối đa trong đường Fail bình thường |
| `run_after` | TIMESTAMPTZ, mặc định now | Thời điểm sớm nhất được claim |
| `locked_at`, `locked_by` | TIMESTAMPTZ / TEXT, nullable | Dấu lease theo worker |
| `last_error` | TEXT, nullable | Lỗi gần nhất |
| `created_at`, `updated_at` | TIMESTAMPTZ, mặc định now | Theo dõi vòng đời |

Hai partial index: `run_after` chỉ cho `queued`, `locked_at` chỉ cho `running`. Không phải composite index `(status,run_after)` như một số bảng mô tả trong docs (`docs/02-data-model.md:98–105`).

### Ranh giới giao dịch và retry

1. **Enqueue cùng giao dịch nghiệp vụ.** `Enqueue` nhận transaction có sẵn; caller quyết định commit. `EnqueuePool` là tiện ích tự mở transaction riêng (`internal/queue/queue.go:47–92`). Với Postgres làm cả data store lẫn queue, đây là lợi ích trực tiếp: tạo workflow run và job đầu tiên có thể cùng commit.
2. **Claim bằng transaction ngắn.** Chọn job `queued`, `run_after <= now()`, sắp theo `run_after`, khóa `FOR UPDATE SKIP LOCKED LIMIT 1`; cập nhật `running`, worker, thời điểm và tăng attempts; commit rồi mới trả job (`internal/queue/queue.go:95–143`). Worker khác bỏ qua hàng đang khóa. Khóa hàng hết sau commit, không bảo vệ suốt thời gian gọi dịch vụ ngoài.
3. **Retry trên cùng row.** Fail đọc số attempts từ DB; chưa hết hạn thì đặt lại `queued`, xóa lock và dời `run_after`; hết hạn thành `dead` (`internal/queue/queue.go:159–193`). Công thức chính xác: `min(3600, min(2**attempts,3600) * U[0.8,1.2))` giây (`internal/queue/queue.go:233–245`). Thất bại đầu tiên chờ khoảng 1,6–2,4 giây. Với mặc định 5 attempts, lần thất bại thứ 5 vào dead, không còn chờ 32 giây để thử lần 6.
4. **Stale reset.** Job chạy quá visibility timeout bị trả về queued, xóa lock; không đổi attempts/run_after và không kiểm tra max attempts (`internal/queue/queue.go:196–210`). Vì thế worker liên tục chết trước Fail có thể khiến job được claim vượt giới hạn dự kiến.
5. **Worker polling.** Mỗi tiến trình xử lý tuần tự một job; rỗng hoặc lỗi dequeue chờ khoảng 1 giây; handler lỗi gọi Fail, thành công gọi Complete. Có metric complete/fail (`cmd/worker/main.go:167–221`). `failed` được khai báo nhưng Fail thực tế chuyển thẳng queued/dead, không đi qua trạng thái failed (`internal/queue/queue.go:18–23,170–188`).

**Scheduler:** tick ngay lúc khởi động, sau đó mỗi 30 giây; visibility timeout 5 phút (`cmd/scheduler/main.go:24–27,55–70`). Mỗi tick lấy advisory lock bằng **một connection được acquire riêng**, unlock trên cùng connection; rồi reset stale, xử lý outbox, hết hạn đơn và cập nhật metrics (`cmd/scheduler/main.go:74–129`). Đây là cơ chế chống tick chồng giữa nhiều replica, không phải leader lease có nhiệm kỳ. Docs chỉ nói một scheduler (`docs/07-queue-worker.md:142–150`), còn code đã có khóa để tránh chạy đồng thời.

### Áp dụng cho xT

Giữ mô hình job theo bước với `kind="workflow.step"`, payload nhỏ gồm `run_id`, `step_id`, phiên bản payload; tenant lấy từ envelope DB đáng tin cậy. Thêm `dedupe_key`, `lease_token`/generation, `lease_expires_at`, giới hạn attempts và trạng thái bằng CHECK. Job tenant phải có tenant hợp lệ; job platform có kind/phạm vi riêng. Dispatcher có quyền claim xuyên tenant nhưng không trở thành đường truy vấn dữ liệu tenant tùy ý.

SQL minh họa tự viết cho claim sau khi bổ sung các cột trên; chỉ chạy trong transaction ngắn của dispatcher được cấp quyền:

```sql
WITH candidate AS (
  SELECT id FROM jobs
  WHERE status = 'queued' AND run_after <= now()
    AND attempts < max_attempts
  ORDER BY run_after, id
  LIMIT 1 FOR UPDATE SKIP LOCKED
)
UPDATE jobs AS j
SET status = 'running', attempts = attempts + 1,
    lease_token = gen_random_uuid(),
    lease_expires_at = now() + interval '5 minutes',
    locked_by = $1, locked_at = now(), updated_at = now()
FROM candidate AS c WHERE j.id = c.id
RETURNING j.*;
```

Claim trả trạng thái **sau cập nhật**. Trong sklite, object trả về vẫn chứa attempts trước khi tăng; chỉ Status được sửa nên log attempt bị lệch (`internal/queue/queue.go:104–116,125–143`; `cmd/worker/main.go:195`). Complete/Fail xT phải là conditional update theo `(id,tenant_id,lease_token,status=running)` và kiểm tra row count; worker cũ không được ghi đè sau khi mất lease. Heartbeat cũng dùng token đó; cần deadline riêng cho step, không giữ transaction DB khi chờ HTTP.

Stale recovery của xT phải đưa job hết attempts vào dead, cập nhật đồng bộ trạng thái step/run và phát sự kiện thất bại. Retry phân biệt lỗi transient (timeout/429/5xx), lỗi input/quyền vĩnh viễn và kết quả side effect chưa rõ; tôn trọng Retry-After. Dùng thời gian DB cho lease/scheduling để giảm lệch đồng hồ. Giới hạn concurrency/quota theo tenant, thêm retention/archive jobs và đo queue age, dead count, số lần mất lease. SKIP LOCKED không tự tạo fairness giữa tenant.

Khi run/step và jobs cùng Postgres, transaction hoàn tất step có thể lưu output, đánh dấu job done và insert job kế tiếp **nguyên tử**. Với event phải phát sang hệ thống khác mới cần outbox tương ứng. Queue chỉ cung cấp cơ chế nhận lại job, không tự cung cấp exactly-once cho side effect bên ngoài.

## 3. Audit log: tái sử dụng writer, làm rõ tính bền vững

Schema gồm `id BIGSERIAL`, nullable `tenant_id`, nullable `actor_user_id`, `action`, `resource`, JSONB `metadata`, `created_at`; index `(tenant_id,created_at DESC)`. Cả hai FK nullable dùng `ON DELETE SET NULL` (`internal/db/migrations/0002_iam.sql:21–31`). Tenant bị xóa có thể làm mất khóa phân loại tenant của lịch sử: cần quyết định retention trước khi dùng cho doanh nghiệp.

Writer nhận interface chỉ cần Exec, nên dùng được với pool hoặc transaction; metadata nil thành map rỗng; lỗi serialize/insert được trả về (`internal/audit/audit.go:12–40`). Đây là pattern tốt để Python truyền `UnitOfWork`/connection vào `append_audit`, dùng cùng transaction với mutation. Audit nghiệp vụ khác application debug log.

Đường đọc tenant bắt buộc `audit.read` và SQL tenant filter; đường platform dùng middleware/token riêng và có thể xem xuyên tenant. Có lọc action, actor, khoảng ngày và limit/offset (`cmd/api/audit_handlers.go:27–66,91–149`; `cmd/api/main.go:238–255,317–318`). Chưa thấy RLS cho audit trong migration. Không nên thêm chính sách kiểu “tenant hiện tại OR tenant_id IS NULL” vì sẽ phát tán audit platform.

**Điểm cần sửa khi học:** có caller dùng transaction nhưng bỏ qua lỗi writer (`cmd/api/iam_handlers.go:88–99`); có caller mutate và audit bằng hai lệnh pool tách biệt rồi cũng bỏ qua lỗi (`cmd/api/template_handlers.go:59–79`). Lỗi SQL trong transaction có thể làm transaction hỏng và commit thất bại; không thể kết luận mọi audit lỗi đều bị mất mà mutation vẫn thành công. Tuy nhiên đường pool và lỗi serialize bị bỏ qua có thể để mutation thành công thiếu audit.

Đề xuất xT:

- Audit mutation nhạy cảm cùng transaction, lỗi audit làm rollback; event denied/error cần đường lưu độc lập vì transaction nghiệp vụ có thể rollback.
- Thêm `actor_kind` (human/service/agent/platform), actor bất biến, request/trace id, MCP client/session id, operation, decision/reason, policy revision, run/step/job/attempt và outcome. FK users hiện tại không đại diện được mọi loại actor.
- Allowlist metadata, che token/password/authorization header, hash tham số nhạy cảm thay vì ghi toàn bộ code/input/output. Debug verbose cũng tuân thủ cùng bộ lọc.
- Vai trò runtime chỉ INSERT/SELECT theo scope; không UPDATE/DELETE audit. Retention/partition/backup và khả năng phát hiện sửa log là phần phải bổ sung; writer insert-only không tự biến bảng thành log chống sửa đổi.
- Phân trang theo `(created_at,id)` để ổn định khi có timestamp trùng; ghi intent và outcome cho external side effect, gắn idempotency key để đối soát khi worker chết.

## 4. Multi-tenant RLS và pool kết nối

### Pattern có trong nguồn và khoảng trống wiring

Các policy thường so `tenant_id::text` với `current_setting('app.tenant_id', true)`: users, products, templates, orders, variants/pricing và các bảng fulfillment. Step runs được scope gián tiếp qua run (`internal/db/migrations/0002_iam.sql:33–39`; `internal/db/migrations/0003_products.sql:14–16,32–34`; `internal/db/migrations/0004_orders.sql:33–35`; `internal/db/migrations/0009_variants_pricing.sql:21–23,52–54`; `internal/db/migrations/0018_missing_rls.sql:8–29`).

Helper `SetLocalTenant` đặt `set_config('app.tenant_id', tham_số, true)` ngay sau Begin: tương đương SET LOCAL nhưng bind được giá trị (`internal/tenant/tenant.go:50–57`). Giá trị local chỉ tồn tại trong transaction. `db.Open` còn gắn AfterRelease để xóa tenant session khi trả connection về pool, nhưng bỏ qua lỗi reset và luôn cho tái sử dụng (`internal/db/db.go:25–31`).

**Không được suy ra rằng RLS đã chạy cho mọi request.** Tìm kiếm toàn bộ Go cho thấy không có nơi gọi `SetLocalTenant` ngoài khai báo; các lệnh đặt tenant khác nằm trong test RLS và hook reset. Router chỉ gắn Auth + TenantStatus, rồi handler nhận pool (`cmd/api/main.go:259–274`). Auth chỉ gắn principal vào context (`internal/httpx/auth.go:95–102`); repository connector và runner gọi pool trực tiếp (`internal/connector/connector.go:46–54`; `internal/fulfillment/engine/runner.go:79–81`).

PRD và docs mô tả SET LOCAL theo request (`PRD.md:65–78`; `docs/00-architecture.md:79–86`), nhưng wiring hiện tại chưa thực hiện điều đó. Role thông thường sẽ không đọc được hàng scope khi thiếu setting; role superuser/BYPASSRLS hoặc chủ bảng trong điều kiện mặc định có thể bỏ qua RLS. Test nguồn thừa nhận kết nối superuser, chỉ kiểm tra policy tồn tại, không kiểm tra hàng bị chặn (`cmd/api/main_test.go:176–200`). Không có bằng chứng từ snapshot về role production thực tế.

### Mẫu Python cho một đơn vị công việc tenant

Ví dụ giao kèo connection/pool theo phong cách async; placeholder `$1` được dùng nhất quán trong mẫu này. Không chạy SET LOCAL bằng `pool.execute()` rồi query bằng `pool.fetch()`, vì có thể là hai connection khác nhau.

```python
from contextlib import asynccontextmanager
from uuid import UUID

@asynccontextmanager
async def tenant_transaction(pool, tenant_id: UUID):
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)",
                str(tenant_id),
            )
            yield conn

async def read_run(pool, principal, run_id):
    async with tenant_transaction(pool, principal.tenant_id) as conn:
        return await conn.fetchrow(
            "SELECT id, status FROM workflow_runs "
            "WHERE tenant_id = $1 AND id = $2",
            principal.tenant_id, run_id,
        )
```

Toàn bộ repository trong block nhận `conn`, không giữ quyền tự lấy pool connection khác. Khi exception/cancellation, transaction phải rollback trước release; nếu rollback/reset thất bại phải loại connection. Không chỉ dựa vào hook reset session; cấm dùng SET session tenant trong luồng bình thường. Mỗi transaction mới, kể cả hai transaction trong cùng request, phải đặt lại tenant. ContextVar hữu ích để truyền principal/correlation, nhưng không tự cấu hình Postgres.

Với MCP request có nhiều tool/RPC hoặc workflow có HTTP dài: mỗi đoạn đọc/ghi DB dùng transaction ngắn riêng có SET LOCAL; giải phóng connection khi chờ mạng. Không thay tenant của một session đang chạy bằng tham số do agent cung cấp. Worker claim toàn cục bằng control role hẹp, sau đó mở tenant transaction và kiểm tra job/run/step cùng tenant. Scheduler và admin cần control-plane repository/role riêng; không nới policy tenant để phục vụ tác vụ toàn cục.

Đề xuất migration xT: role migration/chủ bảng tách runtime; runtime không superuser, không BYPASSRLS, không sở hữu bảng. Có thể FORCE RLS cho bảng tenant để giảm rủi ro owner bypass; nó không khắc phục superuser/BYPASSRLS. Khai báo rõ cả `USING` và `WITH CHECK` cho tenant, có thể dùng `tenant_id = NULLIF(current_setting('app.tenant_id',true),'')::uuid`. Rà tất cả bảng con và FK ghép `(tenant_id,id)` để ngăn liên kết chéo tenant; RLS không thay thế toàn vẹn tham chiếu. Custom setting chỉ là context do backend tin cậy đặt, không phải chứng thực cho một client được phép chạy SQL tùy ý.

Test bắt buộc của xT: role runtime thật; SELECT/UPDATE/DELETE/INSERT sai tenant; không có tenant; A → trả pool → B; rollback/exception/cancel rồi mượn lại; pool size 1 để ép tái sử dụng; truy cập bảng con/FK chéo tenant. Phải kiểm tra dữ liệu quan sát được, không chỉ `pg_policies`.

## 5. Connector store theo tenant

`tenant_connectors` gom nhiều provider vào một bảng: PK `(tenant_id,provider)`, credentials JSONB, config JSONB, updated_at. Migration còn chuyển token Telegram và OAuth Google cũ vào bảng chung (`internal/db/migrations/0021_tenant_connectors.sql:2–32`). Store cung cấp Get/Upsert/Delete/List/GetCredential; Get không có row trả nil, GetCredential phân biệt chưa cấu hình và field thiếu (`internal/connector/connector.go:45–175`).

Pattern có ích: handler/action nhận loader credential theo tenant thay vì giữ global token. Worker inject loader cho Telegram, GitHub, Discord; Drive dùng Store riêng (`cmd/worker/main.go:92–108`). LIST trả cờ configured/config; GET còn trả map credentials giữ tên field nhưng mask mọi giá trị (`cmd/api/connector_handlers.go:31–49,70–81`). Không tìm thấy lớp mã hóa credential trong Store: comment chỉ khuyến nghị, dữ liệu được marshal rồi ghi trực tiếp JSONB (`internal/connector/connector.go:26–32,83–95`). Migration connector chưa bật RLS.

Google resolver ưu tiên client id/secret per tenant, fallback cấu hình deployment; token gồm access/refresh/expiry; SaveGoogleTokens giữ refresh token cũ nếu response không trả token mới (`internal/connector/google.go:32–72,79–117`). Drive dùng TokenSource và cố lưu token mới sau khi grant access, nhưng lỗi lưu chỉ cảnh báo (`internal/fulfillment/actions/drive_client.go:38–84`). Cần phân biệt OAuth client chung được quản lý và OAuth **user grant**: app client dùng chung không có nghĩa được dùng token tài khoản platform thay cho tenant.

Discord có reverse lookup `application_id` để tìm tenant, sau đó route webhook có thể xác thực theo public key per tenant. Lookup dùng LIMIT 1; index JSONB chỉ là index thường, chưa UNIQUE (`internal/connector/discord.go:52–91`; `internal/db/migrations/0023_tenant_connectors_app_id_index.sql:6–8`). xT nên có routing key duy nhất theo provider khi một webhook phải xác định đúng một tenant; routing chỉ chọn cấu hình verify, không thay việc xác thực request.

Đề xuất xT: giữ interface Store và adapter provider, nhưng dùng `(tenant_id,connector_id)` làm định danh instance, thêm provider/account/scopes/status/config_version. Để hỗ trợ doanh nghiệp cần nhiều Google/GitHub account, PK một row/provider của sklite không đủ. Workflow tham chiếu connector instance rõ ràng. Secret lưu bằng secret reference hoặc envelope encryption với key id; config chỉ chứa thông tin được phép trả về client. Cache luôn kèm tenant, connector instance và revision.

Mọi CRUD/test/connect/disconnect phải có IAM riêng và audit. **Nguồn hiện tại thiếu bước đó:** router chỉ bọc nhóm connector bằng tenant auth/status, handler đọc principal rồi ghi Store; không gọi CheckPermission/evaluator (`cmd/api/main.go:349–359`; `cmd/api/connector_handlers.go:87–145,220–234`), dù docs nói cần `connectors:*` (`docs/10-connectors.md:61–74`). Đây là khác biệt có ảnh hưởng trực tiếp tới quyền staff.

Upsert hiện thay toàn bộ hai JSON; API và SaveGoogleTokens tự đọc–merge–ghi (`internal/connector/connector.go:83–95`; `cmd/api/connector_handlers.go:113–144`; `internal/connector/google.go:84–117`). Hai writer có thể làm mất token/field của nhau. xT cần optimistic version/CAS hoặc transaction khóa hàng cho cập nhật ngắn; refresh bên ngoài nên dùng một lease/single-flight theo connector và CAS khi lưu, không giữ row lock trong lúc chờ IdP. Nếu provider xoay token, lưu grant/token mới ngay sau refresh và trước side effect nghiệp vụ để lỗi nghiệp vụ không làm mất refresh token mới.

## 6. Fulfillment pipeline → workflow engine

### Hợp đồng khai báo và luồng thực thi

Template có ordered JSONB steps; mỗi step có `id`, `action`, input map, `on_failure`, `max_attempts`. Validator chỉ kiểm tra step id không rỗng/không trùng và action thuộc whitelist 12 tên; array rỗng được chấp nhận (`internal/fulfillment/template/validator.go:9–60`). Template CRUD dùng IAM và gọi validator trước insert (`cmd/api/template_handlers.go:34–65`). Template thực tế gắn với variant, không còn product (`internal/db/migrations/0009_variants_pricing.sql:56–74`).

Registry ánh xạ tên action sang handler có Execute(context,input) → output/error; cấm tên trùng. Context mang tenant, order/item/product, buyer, previous outputs và SMTP/S3 per tenant (`internal/fulfillment/actions/actions.go:11–59`). Đây là interface có thể viết lại bằng Python Protocol với async callable và model input/output theo từng operation.

Đường chạy một job (`internal/fulfillment/engine/runner.go`):

1. Parse `{order_id,order_item_id,step_index}`; tải order, item/variant, product và tenant config (`internal/fulfillment/engine/runner.go:20–25,54–140`).
2. Không có template thì tạo run completed; có template thì tải steps **tại thời điểm chạy**, tạo/upsert run khi step 0 hoặc cập nhật current index (`internal/fulfillment/engine/runner.go:143–192`).
3. Đọc output các step completed có index nhỏ hơn, map theo step id (`internal/fulfillment/engine/runner.go:194–225,350–369`).
4. Nội suy input rồi lấy handler và Execute; lỗi ghi step failed rồi trả error cho queue retry (`internal/fulfillment/engine/runner.go:228–269,372–384`).
5. Thành công lưu step output. Bước cuối đánh dấu run completed và xét trạng thái order; nếu chưa cuối thì enqueue index kế tiếp (`internal/fulfillment/engine/runner.go:271–311,387–397,429–464`).

`fulfillment_runs` có unique order_item_id, status/current index/timestamp; `fulfillment_step_runs` có unique `(run_id,step_index)`, step id/action/input/output/status/attempt/error/timestamp (`internal/db/migrations/0007_fulfillment_runs.sql:2–36`). Đây là checkpoint dữ liệu tốt, nhưng unique row kết quả **không** có nghĩa action chỉ thực thi một lần.

Interpolator tự đi đệ quy map/list, không eval biểu thức. Toàn chuỗi `{{path}}` giữ kiểu gốc; token nằm trong câu thì stringify. Root hợp lệ là order/order_item/product/buyer/tenant/**previous**; tham chiếu output dùng `{{previous.<step_id>.<field>}}`, không phải `previous_step.output` trong docs (`internal/fulfillment/engine/interpolator.go:20–74,104–173`; `docs/06-fulfillment.md:79–90`). Full root cũng có thể trả nguyên map. xT nên giới hạn root/field, depth/kích thước và không đưa secret vào scope.

### Outbox: học ý tưởng, sửa transaction boundary

Payment handler khóa order, ghi payment, insert outbox cho từng item và đổi trạng thái trong cùng transaction (`internal/payment/sepay/webhook_handler.go:93–169,214–247`). Outbox có id/order/tenant/item/payload/processed/job_id/created_at và partial index chưa xử lý (`internal/db/migrations/0017_fulfillment_outbox.sql:2–14`). Đây là phần ghi event nguyên tử đáng giữ.

Nhưng processor SELECT SKIP LOCKED rồi **commit để nhả khóa trước khi xử lý**; sau đó enqueue bằng transaction riêng và cập nhật processed bằng lệnh pool khác (`internal/fulfillment/outbox/processor.go:25–40,60–70,83–100`). Hai processor có thể nhận cùng event; crash hoặc update processed lỗi sau enqueue sẽ tạo job trùng ở tick sau. Advisory lock scheduler chỉ giảm trường hợp chạy tick chồng; không loại bỏ crash window. Vì event và jobs đều cùng DB, xT nên khóa event → insert job bằng chính transaction → đánh dấu processed → commit; thêm unique dedupe từ event id. Không cần gọi HTTP trong giao dịch này.

### Ánh xạ sang xT

| sklite | xT đề xuất | Thay đổi cần thiết |
|---|---|---|
| Fulfillment template | Workflow definition + immutable version | Snapshot definition/hash khi tạo run; không đọc “bản mới nhất” giữa các step |
| Run theo order item | Workflow run theo trigger | Tenant, initiator/service principal, input, version, trạng thái tổng |
| Ordered step/index | Step id và dependency | MVP giữ chuỗi; DAG/branch/join/wait/approval phải có semantics mới |
| Action registry | Operation registry dùng chung MCP và worker | Input/output schema, IAM resolver, connector scopes, timeout, retry, side-effect class |
| Previous output | Output reference từ step đã commit | Validate tham chiếu trước chạy, lưu output có kiểm soát kích thước/quyền đọc |
| Queue job | Một lần dispatch step | Dedupe logical step, retry cùng identity, lease/generation cho mỗi lần claim |
| Retry API | Resume/retry workflow | Transaction đổi state + enqueue; optimistic version chống hai request retry cùng lúc |

Ví dụ definition **mới của xT**, không phải cấu hình chạy được nguyên trạng trong sklite:

```json
{
  "version": 1,
  "steps": [
    {"id": "lookup", "operation": "crm.contact.read", "connector": "crm-main"},
    {"id": "report", "operation": "document.create", "depends_on": ["lookup"],
     "input": {"contact": "{{previous.lookup.contact}}"}},
    {"id": "notify", "operation": "chat.message.send", "depends_on": ["report"],
     "retry": {"max_attempts": 4}, "on_failure": "stop"}
  ]
}
```

Runner xT nên chia: (A) transaction tenant ngắn kiểm tra lease/state/dependency, nạp definition version và kiểm tra quyền; (B) gọi adapter ngoài transaction, với idempotency key ổn định `(tenant,run,step,generation)`; (C) transaction tenant kiểm tra lease lần nữa, lưu output/outcome, chuyển step, enqueue successor và complete job. Chỉ tăng generation khi chủ động yêu cầu thực hiện lại side effect, không tăng vì automatic retry. Nếu adapter hỗ trợ idempotency key thì tái sử dụng qua retry; nếu không, cần khả năng đối soát hoặc trạng thái “chưa rõ kết quả”.

Điểm (C) nguyên tử giải quyết ghi output/enqueue/ack bị dở dang; không giải quyết crash sau dịch vụ ngoài thành công nhưng trước commit DB. Vì vậy không tuyên bố exactly-once toàn workflow. Event audit và thông báo completion cũng cần durable enqueue thay cho callback chỉ chạy sau commit một lần.

## 7. mcp-scaffold: Code Mode search/execute và auth modes

### Tách scaffold, MCP runtime và Go backend

Đây là skill tạo **Cloudflare Worker TypeScript** bọc REST API; không phải MCP server Go đã cài trong `cmd/api`. Skill dùng `openApiMcpServer`, DynamicWorkerExecutor, OAuth provider; hướng dẫn tạo project/install/KV, không deploy (`.claude/skills/mcp-scaffold/SKILL.md:8–25,133–149`). Khảo sát này chỉ đọc tài liệu/template, không thực thi workflow scaffold.

Spec source độc lập với auth mode: upstream OpenAPI, local file, Discovery chuyển thành OpenAPI, hoặc merge nhiều nguồn (`.claude/skills/mcp-scaffold/references/spec-sources.md:7–12,18–48,61–81`). Runtime lấy bundled spec hoặc bản trong SPEC_KV, lập endpoint index/operation map (`.claude/skills/mcp-scaffold/templates/src/index.ts.tpl:38–130`). xT có thể dùng một catalog chuẩn hóa, nhưng mỗi operation cần giữ provider/connector/base URL/schema version của riêng nó; merge spec không tự tạo router đa provider.

**Cần phân biệt reference và template thực tế:** reference nói `search(query)` dùng BM25, execute chạy TypeScript (`.claude/skills/mcp-scaffold/references/codemode-api.md:21–42`). Template hiện tại override `search` qua private `_registeredTools`, callback nhận `{code}` và chạy sandbox (`.claude/skills/mcp-scaffold/templates/src/index.ts.tpl:489–538`). Các capability cho search:

- `findEndpoints({query,tag,method,limit})`: lọc substring trên path/opId/summary/tags, phân biệt tag chính xác, mặc định lấy 10; đây không phải BM25 của reference (`.claude/skills/mcp-scaffold/templates/src/index.ts.tpl:509–530`).
- `spec()`: trả spec đang giữ; raw spec không được resolve toàn bộ trước khi trả như description hứa (`.claude/skills/mcp-scaffold/templates/src/index.ts.tpl:318–326,508`). Chỉ nên lấy projection schema cần thiết.
- `refreshSpec(url?)`: host fetch spec, ghi SPEC_KV và đổi cache/index; vẫn nằm trong tool search, không thành tool MCP thứ ba (`.claude/skills/mcp-scaffold/templates/src/index.ts.tpl:229–299,532–534`). Search vì thế hiện có cả side effect quản trị spec.

Execute được thư viện tạo từ spec, host callback tạo HTTP request và inject bearer token. Template validate request khi tìm thấy operation; response schema drift chỉ được log; sau cùng trả body (`.claude/skills/mcp-scaffold/templates/src/index.ts.tpl:417–485`). `_registeredTools` và shape callback là chi tiết nội bộ cần tránh biến thành phụ thuộc cứng của xT. Chính reference/deploy checklist cũng lệch nhau về input search (`.claude/skills/mcp-scaffold/references/deploy-checklist.md:44–50`).

Sandbox được cấu hình `globalOutbound:null`, timeout 30 giây; token chỉ nằm ở host (`.claude/skills/mcp-scaffold/templates/src/index.ts.tpl:411–450`). Đây là pattern đáng giữ: agent viết chương trình để phối hợp nhiều operation trong một lần execute, nhưng host mới có quyền nối mạng và truy cập secret. Cấu hình này không ngăn agent thực hiện nhầm một thao tác vốn đang được host cấp quyền; vẫn cần IAM, kiểm tra tham số và giới hạn kết quả.

### Bốn mode: ý định và phần triển khai thấy được

| Mode | Ý định theo tài liệu | Đối chiếu source |
|---|---|---|
| `oauth` | Worker login email/password với upstream; cấp OAuth cho MCP client; refresh upstream khi 401 | `.claude/skills/mcp-scaffold/templates/src/auth-handler.ts.tpl:13–66`; `.claude/skills/mcp-scaffold/templates/src/index.ts.tpl:456–460,567–588` |
| `oauth-multitenant` | Login → hỏi tenant → chọn → login có tenant_slug; bỏ picker nếu chỉ một tenant | `.claude/skills/mcp-scaffold/templates/src/auth-handler-multitenant.ts.tpl:24–85,101–135`; API nguồn có `/auth/tenants` tại `cmd/api/main.go:204–206` |
| `bearer` | Theo docs: PAT/bearer passthrough, không login UI, không refresh | `.claude/skills/mcp-scaffold/references/auth-modes.md:5–17`; **script vẫn chọn index/auth-handler của oauth**, không có nhánh passthrough riêng tại `.claude/skills/mcp-scaffold/scripts/scaffold.sh:190–212` |
| `oauth-relay` | Redirect tới IdP, callback exchange token, lưu grant, host dùng token gọi upstream và refresh nếu cần | `.claude/skills/mcp-scaffold/templates/src/auth-handler-relay.ts.tpl:38–158`; `.claude/skills/mcp-scaffold/templates/src/index-relay.ts.tpl:367–406,409–479` |

`bearer` được accept trong script nhưng sản phẩm sinh ra vẫn qua OAuthProvider, yêu cầu session props và upstream email/password login. Vì vậy không ghi nhận bearer passthrough đã hoàn thiện chỉ dựa trên danh sách mode.

Hai lớp token khác nhau: token MCP client dùng để vào Worker và token upstream/IdP do host giữ. OAuth scope chung `mcp` trong template không thay quyền từng operation (`.claude/skills/mcp-scaffold/templates/src/index.ts.tpl:580–588`). Không được nhầm `oauth-multitenant` là cơ chế RLS: nó chỉ chọn tenant khi đăng nhập.

Auth context cũng có ranh giới vòng đời: template đọc props được OAuthProvider đặt vào context ở outer fetch, không gọi `getMcpAuthContext()` tại đó (`.claude/skills/mcp-scaffold/templates/src/index.ts.tpl:398–405`). Tài liệu known issues ghi vòng lặp 401 do hàm này chỉ thấy AsyncLocalStorage trong tool callback (`.claude/skills/mcp-scaffold/references/known-issues.md:7–31`), trái mô tả reference cũ. Port Python cần middleware/context isolation theo request và test nhiều session đồng thời; không giữ principal/credential ở biến global. Hai tool search/execute là một cách bọc catalog API, không buộc toàn bộ sản phẩm xT chỉ có hai tool.

Relay lưu refresh token riêng vì session props bất biến, giữ token cũ nếu IdP không trả token mới; bản access token mới chỉ cache trong request hiện hành (`.claude/skills/mcp-scaffold/templates/src/index-relay.ts.tpl:367–427`). Raw row `relay:user:{userId}` được ghi JSON trực tiếp (`.claude/skills/mcp-scaffold/templates/src/auth-handler-relay.ts.tpl:134–140`), khác props qua OAuth provider. Không thấy envelope encryption ở lớp template cho row này; không suy rộng mô tả “props encrypted” sang mọi giá trị KV hay bảo đảm xoay token của mọi IdP.

### Port ý tưởng Code Mode sang xT Python

Không thay V8 isolate bằng `exec`/`eval` Python ngay trong tiến trình server. Có thể giữ Python làm host và executor cô lập riêng chạy ngôn ngữ code được chọn; sandbox chỉ có RPC capability. Một phương án MVP là execute nhận plan/DSL giới hạn nếu chưa có isolation đủ mạnh. API Python-facing không buộc ngôn ngữ code của agent phải là Python.

Host dispatcher là nơi thống nhất: xác thực session → lấy tenant bất biến → operation registry → validate args → resolve resource → IAM → quota/deadline → connector đúng tenant → gọi adapter → audit/outcome. Search cũng lọc catalog theo quyền, nhưng execute luôn kiểm tra lại. Code có thể gọi operation trực tiếp mà không search trước. Không truyền raw DB, arbitrary HTTP, bearer token hoặc credential loader vào sandbox.

Search xT nên nhận filter/schema request có cấu trúc, trả index gọn và schema theo operation id. Refresh spec là chức năng quản trị có quyền, audit, allowlist URL, schema diff và version; không cho agent thường thay shared catalog. Execute có budget số call, concurrency, CPU/memory, output bytes; kiểm soát từng RPC, không chỉ timeout toàn snippet. Các tác vụ dài khởi tạo workflow bền vững rồi trả run id; một snippet đang chạy trong RAM chưa phải workflow có thể resume sau crash.

Auth xT nên chuẩn hóa thành principal/session tenant-bound, rồi tách loại credential connector. Bearer/service token phải có audience/scope/expiry/revocation; OAuth/SSO grant cần map identity đã xác minh tới membership tenant, không tin claim chưa verify. Token store theo `(tenant,connector_instance,issuer,subject,grant_id)` thay cho chỉ userId; refresh single-flight/CAS và giữ access token mới bền vững để tránh refresh lại mỗi request. Chọn tenant không được mở rộng quyền của subject.

## 8. Những điểm yếu/rủi ro xT cần tránh

Các hàng sau là kết quả đọc source hoặc suy luận failure mode từ source, không phải báo cáo khai thác runtime. “P0/P1” là mức ưu tiên thiết kế **đề xuất cho xT**.

| Ưu tiên | Phát hiện và bằng chứng | Hệ quả / cách tránh trong xT |
|---|---|---|
| P0 | RLS helper chưa có caller runtime; test dùng superuser (`internal/tenant/tenant.go:50–57`; `cmd/api/main.go:259–274`; `cmd/api/main_test.go:193–200`) | Tách DB role, transaction tenant bắt buộc, test isolation bằng role thật; không dùng kết quả test hiện tại làm chứng nhận cách ly |
| P0 | Không thấy RLS cho connector, IAM policy/attachment, audit, jobs trong migrations; schema nằm ở `internal/db/migrations/0002_iam.sql:2–31`, `internal/db/migrations/0006_queue.sql:2–19`, `internal/db/migrations/0021_tenant_connectors.sql:2–11` | Lập ma trận từng bảng/role; tenant-facing table bắt buộc scope; bảng control plane chỉ có API/role hẹp |
| P0 | Connector CRUD thiếu IAM và audit dù docs mô tả có permission (`cmd/api/main.go:349–359`; `cmd/api/connector_handlers.go:87–145,220–234`) | Staff có thể đổi credential trong tenant dù quyền nghiệp vụ hẹp; enforce connector.manage/test/read và audit ở service layer |
| P0 | Detach policy kiểm quyền caller nhưng DELETE chỉ theo user_id/policy_id, không kiểm tenant target như Attach (`cmd/api/iam_handlers.go:621–634`; đối chiếu `cmd/api/iam_handlers.go:547–559`) | Khi caller có quyền IAM update và tải policy thành công, biết UUID pair có thể gỡ attachment tenant khác; user_policies vốn không có RLS. DELETE phải JOIN/EXISTS đúng tenant và có constraint/RLS cho attachment |
| P0 | Matcher bỏ qua sep và literal match sai (`internal/iam/policy.go:64–89`) | Quyền khớp rộng ngoài ý định; định nghĩa grammar, full match, validate và test đối kháng |
| P0 | Policy không xử lý Condition/version semantics (`internal/iam/policy.go:25–58`; `PRD.md:88`) | Không chấp nhận âm thầm field bảo mật chưa hỗ trợ; parser strict và deny khi không hiểu |
| P0 | Complete/Fail update bằng id, không kiểm tra owner/lease; ResetStale không có fencing (`internal/queue/queue.go:146–210`) | Worker cũ ack/fail job đã bị worker khác nhận; dùng lease token, heartbeat, conditional write và đối soát external effects |
| P0 | Outbox nhả khóa trước dispatch và enqueue/processed tách transaction (`internal/fulfillment/outbox/processor.go:60–100`) | Duplicate jobs khi race/crash; atomic DB dispatch + dedupe event/job |
| P0 | Runner gọi external trước lưu output; lưu output, enqueue next, Complete là các thao tác tách (`internal/fulfillment/engine/runner.go:262–307`; `cmd/worker/main.go:206–217`) | Crash/retry có thể gửi mail, tạo invite/license/ticket lại; idempotency + atomic save/next/ack. License và ticket hiện insert mới mỗi Execute (`internal/fulfillment/actions/license_generate_key.go:29–45`; `internal/fulfillment/actions/ticket_generate_code.go:25–55`) |
| P1 | `on_failure`, `max_attempts` tồn tại nhưng runner không dùng; queue chỉ đổi job dead, không đồng bộ run failed (`internal/fulfillment/template/validator.go:26–31`; `internal/fulfillment/engine/runner.go:228–305`; `internal/queue/queue.go:170–175`) | Không hứa configurable retry khi chỉ lưu config. Implement semantics, cập nhật terminal state; retry API nguồn đòi run failed/partially_fulfilled (`cmd/api/order_handlers.go:754–792`) nên có thể không resume được run mắc ở running |
| P1 | Run tạo lười khi step 0; đánh giá hoàn tất chỉ đếm run đang tồn tại (`internal/fulfillment/engine/runner.go:177–192,429–447`) | Với nhiều item, item đầu xong trước khi item khác tạo run có thể làm order fulfilled quá sớm. Tạo đủ expected step/run khi start, hoặc đối chiếu số run bắt buộc |
| P1 | Template đọc lại giữa các bước; item được load chỉ theo item id, không ràng buộc order id trong query (`internal/fulfillment/engine/runner.go:91–106,157–175`) | Definition đổi gây sai checkpoint; payload lệch có thể phối sai dữ liệu khi thiếu ràng buộc khác. Snapshot version; kiểm tra tenant/run/order/item nhất quán |
| P1 | Validator chấp nhận steps rỗng; runner chỉ kiểm tra index >= len, không chặn index âm (`internal/fulfillment/template/validator.go:34–59`; `internal/fulfillment/engine/runner.go:173–175,228`) | Empty template có thể lỗi thay vì no-op; index âm có thể panic. Xác định empty semantics và validate boundary trước mọi index |
| P1 | Secrets ghi JSONB thẳng; upsert/refresh đọc–ghi toàn row (`internal/connector/connector.go:83–95`; `internal/connector/google.go:84–117`) | Rò rỉ qua DB/log và lost update; secret vault/envelope, CAS/single-flight, cache key đầy đủ tenant |
| P1 | Webhook có timeout, chặn private host, giới hạn response 10 KB, nhưng kiểm DNS rồi dial hostname lại; DNS lỗi return false trái comment (`internal/fulfillment/actions/webhook.go:26–47,93–122,132–148`) | Còn TOCTOU/DNS rebinding; resolve/validate/pin IP tại egress, chặn redirect ngoài policy, allowlist connector host/scheme. Không nhầm có SSRF guard là đã an toàn mọi đích |
| P1 | Search `refreshSpec(url)` fetch URL tùy ý và ghi shared KV (`.claude/skills/mcp-scaffold/templates/src/index.ts.tpl:229–299,532–534`) | Agent có thể đổi catalog dùng chung/gây outbound ngoài ý định. Tách admin capability, allowlist URL, version theo tenant/provider và limit bytes |
| P1 | Validator template chỉ xử lý một phần schema: null bỏ qua, integer chỉ kiểm number; chỉ validate nếu tìm được op (`.claude/skills/mcp-scaffold/templates/src/index.ts.tpl:154–226,423–435`) | Không xem đây là JSON Schema validator đầy đủ; fail khi op không có trong registry, validate đầy đủ enum/oneOf/required/path/body/format theo contract |
| P1 | `bearer` chỉ chọn template oauth; relay decode ID token, không thấy verify trong resolveUserId, không thấy PKCE challenge/verifier trên hop IdP (`.claude/skills/mcp-scaffold/scripts/scaffold.sh:203–207`; `.claude/skills/mcp-scaffold/templates/src/auth-handler-relay.ts.tpl:63–107,161–196`) | Tự kiểm từng mode, không tin nhãn. Xác minh signature/issuer/audience/expiry/nonce phù hợp; state one-time, PKCE đúng hop, credential không vào code sandbox |
| P1 | Multi-tenant picker truyền password qua hidden form; relay refresh store key chỉ userId (`.claude/skills/mcp-scaffold/templates/src/auth-handler-multitenant.ts.tpl:38–42,80–85`; `.claude/skills/mcp-scaffold/templates/src/auth-handler-relay.ts.tpl:134–140`) | Không round-trip password trong HTML; dùng login challenge ngắn hạn server-side. Namespace grant theo tenant/connector/issuer/session để tránh ghi đè grant khác |
| P1 | Audit lỗi bị bỏ qua; claim code/invite code còn được log (`cmd/api/template_handlers.go:72–78`; `internal/fulfillment/actions/discord_claim_code.go:55–61`; `internal/fulfillment/actions/discord_create_invite.go:58`) | Atomic audit và redaction mặc định; không coi verbose logging là quyền ghi secret hoặc dữ liệu có thể đổi quyền truy cập |

Một số chênh lệch docs/source cần giữ trong test contract: docs queue ORDER BY id nhưng code ORDER BY run_after (`docs/07-queue-worker.md:75–84`; `internal/queue/queue.go:108–112`); docs mô tả previous_step nhưng code dùng previous; docs connector dùng tên credential GitHub khác loader thực tế `access_token` (`docs/10-connectors.md:38–45`; `cmd/worker/main.go:103–105`); reference Code Mode mô tả BM25 nhưng template override substring; tài liệu journey có ví dụ advisory lock dùng pool, trong khi code hiện đã sửa sang dedicated connection (`docs/DEVELOPMENT_JOURNEY.md:171–184`; `cmd/scheduler/main.go:74–99`). Không lấy snippet trong tài liệu lịch sử làm implementation chuẩn.

Các test nguồn đáng tham khảo nhưng chưa đủ: queue concurrency 100 jobs/3 workers đếm tập job id, không đếm riêng số lần side effect; outbox test chỉ một lần xử lý thành công (`internal/queue/queue_integration_test.go:50–90`; `internal/fulfillment/outbox/processor_test.go:50–115`). xT cần fault injection ở các điểm trước/sau external call, output commit, enqueue next, ack; stale reset khi worker cũ còn sống; hai processor outbox; hai request retry; refresh token đồng thời; và nhiều item/step hoàn tất lệch thứ tự. Những test này kiểm tra giao kèo liên mô-đun mà unit test matcher/handler không chứng minh được.

Thứ tự triển khai đề xuất: (1) tenant UnitOfWork + role/RLS thật + principal/IAM strict; (2) operation registry + connector secret store + audit; (3) queue lease/dedupe + workflow state/version và transaction boundaries; (4) Code Mode sandbox/host dispatcher; (5) scheduler, recovery, quota và fault-injection tests. Trong lần khảo sát này không chạy test Go, migration, scaffold, gọi provider hoặc đổi cấu hình; chỉ tạo tài liệu research.
