# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""AgentHR ở composition root (D-AGENTHR-1): hiện thực `AgentHrGate` cho dispatcher, hook ghi của
gói `nhan-su-ai` và op dịch vụ `apply_agent_jd` (IT duyệt JD).

- Hồ sơ/JD/giấy phép luôn đọc bằng ngữ cảnh workflow của người gọi (không qua RLS của người được
  đại diện): người không thấy hồ sơ không làm cổng "mở" được.
- Không có model `ho_so_agent` (tenant chưa cài gói), gói đang tắt, hoặc không có hồ sơ cho
  (client_id, người được đại diện): không làm gì — hành vi cũ.
- Chặn = `access_denied(reason, remediation=...)`. Tác dụng phụ của lần chặn (dòng audit
  `xt_meta.agenthr_event` op `agenthr.block`, sự cố `su_co_agent`, tự tạm dừng) chạy qua
  `after_rollback`: transaction ngoài của lời gọi, sau khi phần ghi của tool đã rollback.
- `xt_meta.agent_grant` chỉ được ghi trong ngữ cảnh `xt_workflow` (policy RLS của migration 0022):
  upsert khi JD được duyệt, xóa khi hồ sơ chuyển `da_nghi`. Thu hồi consent/token Keycloak của
  client cho người đó là việc tiếp theo (op dịch vụ không gọi hệ thống ngoài, xem `pack_ops`);
  cổng chặn ngay lượt kế tiếp nên không phụ thuộc token hết hạn.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date
from typing import TYPE_CHECKING, Any, NoReturn
from uuid import UUID
from zoneinfo import ZoneInfo

from xt.application.agenthr.gate import (
    DA_NGHI,
    DANG_LAM,
    GIAY_PHEP,
    HO_SO,
    JD,
    SU_CO,
    TAM_DUNG,
    VAI_TRO,
    Block,
    Intent,
    JobDescription,
    Permit,
    Profile,
    ScheduleError,
    check_write,
    hours_block,
    intent_model,
    limit_block,
    parse_capabilities,
    parse_days,
    parse_window,
    ref_model,
    status_block,
)
from xt.application.agenthr.risk import diff_risk
from xt.application.process.ports import ServiceFn
from xt.application.tools.context import CallContext
from xt.application.tools.errors import access_denied, invalid_request, precondition_failed
from xt.application.tools.ports import Args, MutationPlan
from xt.domain.model.errors import ModelError
from xt.domain.model.spec import Catalog
from xt.entrypoints.composition.pack_ops import ACTING, OpInputError, _Data, record_id
from xt.entrypoints.composition.runtime import Caller, Runtime, after_rollback

if TYPE_CHECKING:
    from xt.adapters.db.integration import ProcessDefinition
    from xt.entrypoints.composition.process import ProcessContext

log = logging.getLogger("xt.agenthr")
AGENT_ROLES = ("agent",)
# Client cổng Desk của chính người dùng (D-UNI-AGENT-1, khớp `ai.desk-client-write` của gói): không
# phải nhân viên AI — cổng AgentHR không áp, không tuyển được hồ sơ cho client này.
PORTAL_CLIENTS = frozenset({"xt-desk-agent"})
ROLE_DEFAULTS = (
    "nang_luc",
    "muc_tu_chu",
    "han_muc_viec_ngay",
    "han_muc_gia_tri",
    "khung_gio",
    "ngay_lam",
)
MAX_PROFILES = 50


def _uuid(v: object) -> UUID | None:
    try:
        return UUID(str(v).rsplit(":", 1)[-1]) if v else None
    except ValueError:
        return None


def _date(v: object) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def profile_of(row: Mapping[str, Any]) -> Profile:
    return Profile(
        str(row["id"]),
        str(row.get("trang_thai") or ""),
        row.get("ly_do_tam_dung"),
        _date(row.get("hieu_luc_den")),
        str(row["jd_hieu_luc"]) if row.get("jd_hieu_luc") else None,
        str(row.get("nguoi_phu_trach") or "") or None,
        str(row.get("client_id") or ""),
    )


def _eq(field: str, value: Any) -> dict[str, Any]:
    return {"field": field, "op": "=", "value": value}


class AgentHr:
    """`AgentHrGate` + hook ghi của gói nhan-su-ai (một instance cho mỗi Composition)."""

    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

    # ---- đọc ------------------------------------------------------------------------------
    def _catalog(self, caller: Caller) -> Catalog | None:
        current = self.rt.catalogs.current(caller.actor)
        if current is None:
            return None
        names = {m.name for m in current[1].models}
        if HO_SO not in names or JD not in names:
            return None
        if HO_SO in self.rt.catalogs.disabled(caller.actor).models:
            return None  # gói đang tắt (đề xuất + duyệt): giữ hành vi cũ, dữ liệu vẫn còn
        return current[1]

    def _rows(self, caller: Caller, model: str, where: Mapping[str, Any]) -> list[dict[str, Any]]:
        out = self.rt.records.search(
            caller.workflow(), model, where, ("-created_at",), MAX_PROFILES, 0
        )
        return [dict(r) for r in out["items"]]

    def _get(self, caller: Caller, model: str, rid: UUID) -> dict[str, Any] | None:
        try:
            return dict(self.rt.records.get(caller.workflow(), model, rid)["record"])
        except ModelError as e:
            if e.code == "not_found":
                return None
            raise

    def profile(self, caller: Caller) -> tuple[Profile, dict[str, Any]] | None:
        """Hồ sơ của (client agent, người được đại diện): ưu tiên `dang_lam`, không thì mới nhất."""
        if not caller.is_agent or caller.client_id in PORTAL_CLIENTS:
            return None  # cổng Desk của người dùng: không phải nhân viên AI, không đếm/giới hạn
        if self._catalog(caller) is None:
            return None
        rows = self._rows(
            caller,
            HO_SO,
            {"and": [_eq("client_id", caller.client_id),
                     _eq("nguoi_phu_trach", str(caller.person.id))]},
        )  # fmt: skip
        if not rows:
            return None
        row = next((r for r in rows if r.get("trang_thai") == DANG_LAM), rows[0])
        return profile_of(row), row

    def job(self, caller: Caller, p: Profile) -> JobDescription | None:
        """JD hiệu lực = JD do op `apply_agent_jd` ghi vào hồ sơ, còn ở trạng thái approved."""
        rid = _uuid(p.jd_id)
        row = self._get(caller, JD, rid) if rid else None
        if row is None or row.get("state") != "approved" or str(row.get("ho_so")) != p.id:
            return None
        try:
            return JobDescription.from_row(row)
        except ValueError:
            log.warning("agenthr: jd %s có nang_luc không hợp lệ", rid)
            return None

    def permits(self, caller: Caller, p: Profile) -> list[Permit]:
        rows = self._rows(caller, GIAY_PHEP, _eq("ho_so", p.id))
        return [
            Permit(str(r.get("hanh_dong") or ""), _date(r.get("tu_ngay")), _date(r.get("den_ngay")))
            for r in rows
        ]

    @staticmethod
    def _now(caller: Caller, ctx: CallContext) -> Any:
        return ctx.now.astimezone(ZoneInfo(caller.profile.timezone))

    # ---- cổng -----------------------------------------------------------------------------
    def check_call(self, ctx: CallContext, tool: str) -> None:
        caller = self.rt.caller(ctx)
        found = self.profile(caller)
        if found is None:
            return
        p, _ = found
        now = self._now(caller, ctx)
        blocked = status_block(p, now.date())
        if blocked is None:
            blocked = hours_block(self.job(caller, p), now)
        if blocked is not None:
            self._block(caller, ctx, tool, p, blocked)

    def check_write(self, ctx: CallContext, tool: str, args: Args, plan: MutationPlan) -> None:
        caller = self.rt.caller(ctx)
        found = self.profile(caller)
        if found is None:
            return
        p, row = found
        target = intent_model(tool, args)
        if target is None:
            return
        catalog = self._catalog(caller)
        assert catalog is not None
        now = self._now(caller, ctx)
        today = now.date()
        intent = Intent(
            tool, target[0], target[1], self._amounts(caller, catalog, target, args, plan)
        )
        jd = self.job(caller, p)
        person = caller.person.id
        count = self.rt.store.agent_usage(caller.actor, caller.client_id, person, today)
        permits = self.permits(caller, p) if intent.op == "submit" else []
        blocked = check_write(jd, intent, count, permits, today)
        if blocked is None and jd is not None:
            taken = self.rt.store.take_agent_usage(
                caller.actor, caller.client_id, person, today, jd.daily_limit
            )
            if taken is None:  # lời gọi song song vừa lấy suất cuối
                blocked = limit_block(jd, max(count, jd.daily_limit))
            else:
                self._mirror_count(caller, row, taken, today)
        if blocked is not None:
            self._block(caller, ctx, tool, p, blocked)

    def _amounts(
        self, caller: Caller, catalog: Catalog, target: tuple[str, str], args: Args, plan: Any
    ) -> tuple[int, ...]:
        """Giá trị các trường `money` của bản ghi SAU lời ghi (tạo: values; sửa: bản ghi hiện tại
        + set; chuyển trạng thái / nộp duyệt: bản ghi hiện tại)."""
        model = target[0]
        try:
            fields = [f.name for f in catalog.model(model).fields if f.type == "money"]
        except KeyError:
            return ()
        if not fields:
            return ()
        values: dict[str, Any] = {}
        rid = _uuid(args.get("ref")) if args.get("ref") else None
        if rid is not None and ref_model(args.get("ref")) == model:
            values.update(self._get(caller, model, rid) or {})
        payload = getattr(plan, "payload", None)
        written = getattr(payload, "values", None)
        if isinstance(written, Mapping):
            values.update(written)
        out: list[int] = []
        for f in fields:
            try:
                if values.get(f) is not None:
                    out.append(int(float(str(values[f]))))
            except ValueError:
                continue
        return tuple(out)

    def _mirror_count(self, caller: Caller, row: Mapping[str, Any], n: int, today: date) -> None:
        """so_viec_hom_nay của hồ sơ = bộ đếm ngày (một lần ghi mỗi lời gọi). Xung đột revision
        (lời gọi song song) thì bỏ qua: bảng agent_usage là nguồn đúng."""

        def write() -> None:
            self.rt.records.workflow_write(
                caller.workflow(),
                HO_SO,
                UUID(str(row["id"])),
                int(row["revision"]),
                {"so_viec_hom_nay": n, "ngay_dem": today.isoformat()},
            )

        try:
            self.rt.uow.run(write)
        except ModelError as e:
            if e.code != "revision_conflict":
                raise

    def _block(self, caller: Caller, ctx: CallContext, tool: str, p: Profile, b: Block) -> NoReturn:
        detail = {k: v for k, v in b.details.items() if v is not None}

        def record() -> None:
            self.rt.store.agenthr_block(
                caller.actor,
                client_id=caller.client_id,
                person=caller.person.id,
                ho_so=UUID(p.id),
                tool=tool,
                reason=b.reason,
                detail={**detail, "trace_id": ctx.trace_id},
            )
            if b.incident is not None:
                self._incident(caller, p, b)
            if b.suspend:
                self._suspend(caller, p)

        def safe() -> None:
            try:
                self.rt.uow.run(record)
            except Exception as e:  # lỗi ghi sự cố không được đổi lỗi chặn thành internal_error
                log.error(
                    "agenthr: ghi lần chặn lỗi trace_id=%s (%s)", ctx.trace_id, type(e).__name__
                )

        after_rollback(safe)
        raise access_denied(
            b.message, b.reason, None, remediation=b.remediation, agent=p.id, **detail
        )

    def _incident(self, caller: Caller, p: Profile, b: Block) -> None:
        open_ = self._rows(
            caller,
            SU_CO,
            {"and": [_eq("ho_so", p.id), _eq("loai", b.incident), _eq("trang_thai", "mo")]},
        )
        if open_:
            return  # một sự cố mở cho mỗi loại: không nhân bản khi agent thử lại
        detail = "; ".join(f"{k}={v}" for k, v in b.details.items() if v is not None)
        self.rt.records.create(
            caller.workflow(),
            SU_CO,
            {"ho_so": p.id, "loai": b.incident, "chi_tiet": f"{b.message} [{detail}]"[:2000],
             "trang_thai": "mo"},
        )  # fmt: skip

    def _suspend(self, caller: Caller, p: Profile) -> None:
        row = self._get(caller, HO_SO, UUID(p.id))
        if row is None or row.get("trang_thai") != DANG_LAM:
            return
        self.rt.records.workflow_write(
            caller.workflow(),
            HO_SO,
            UUID(p.id),
            int(row["revision"]),
            {"trang_thai": TAM_DUNG, "ly_do_tam_dung": "han_muc"},
        )

    # ---- hook ghi của gói (create/update_record, submit_request) ------------------------------
    def before_write(
        self, caller: Caller, model: str, rid: UUID | None, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Bất biến của hồ sơ/JD trước khi plan (người hay Desk đều qua đây). Trả values dùng để
        ghi: JD mới không có `nang_luc` (và hạn mức, ca, tự chủ) thì sao từ vai trò của hồ sơ."""
        out = dict(values)
        if model == HO_SO:
            self._check_profile_write(caller, rid, out)
        elif model == JD:
            if rid is None:
                out = self._from_role(caller, out)
            self._check_jd_values(out)
        return out

    def _from_role(self, caller: Caller, values: dict[str, Any]) -> dict[str, Any]:
        ho_id = _uuid(values.get("ho_so"))
        ho = self._get(caller, HO_SO, ho_id) if ho_id else None
        role_id = _uuid(ho.get("vai_tro")) if ho else None
        role = self._get(caller, VAI_TRO, role_id) if role_id else None
        if role is None:
            if "nang_luc" not in values:
                raise invalid_request(
                    "JD cần nang_luc, hoặc hồ sơ agent phải có vai_tro để sao năng lực.",
                    "validation",
                    remediation="Set vai_tro on the ho_so_agent or pass nang_luc.",
                )
            return values
        for f in ROLE_DEFAULTS:
            if values.get(f) is None and role.get(f) is not None:
                values[f] = role[f]
        return values

    def _check_profile_write(
        self, caller: Caller, rid: UUID | None, values: Mapping[str, Any]
    ) -> None:
        to = values.get("trang_thai")
        if rid is None and str(values.get("client_id")) in PORTAL_CLIENTS:
            raise invalid_request(
                "Client cổng Desk là phiên của chính người dùng, không tuyển làm agent được.",
                "portal_client",
            )
        if rid is None:
            if to not in (None, "cho_duyet"):
                raise invalid_request(
                    "Hồ sơ agent mới luôn ở 'Chờ duyệt'; agent chỉ vào làm khi IT duyệt JD.",
                    "hire_requires_jd_approval",
                    remediation="Create the profile, then create + submit a jd_agent request.",
                )
            return
        cur = self._get(caller, HO_SO, rid)
        if cur is None:
            return
        for f in ("client_id", "nguoi_phu_trach"):
            if f in values and str(values[f]) != str(cur.get(f)):
                raise precondition_failed(
                    f"Không đổi '{f}' của hồ sơ agent; cho nghỉ rồi tuyển hồ sơ mới.",
                    "immutable_field",
                    field=f,
                )
        if cur.get("trang_thai") == DA_NGHI and to is not None and to != DA_NGHI:
            raise precondition_failed(
                "Agent đã nghỉ việc; không mở lại được hồ sơ này (tuyển hồ sơ mới).",
                "agent_terminated",
            )
        if to == DANG_LAM and cur.get("trang_thai") != DANG_LAM:
            raise precondition_failed(
                "Mở lại agent phải qua yêu cầu JD được IT duyệt (OTP), không sửa trực tiếp.",
                "reopen_requires_jd_approval",
                remediation="Create a jd_agent for this profile and submit_request it.",
            )

    @staticmethod
    def _check_jd_values(values: Mapping[str, Any]) -> None:
        try:
            if "nang_luc" in values:
                parse_capabilities(values["nang_luc"])
            parse_window(values.get("khung_gio"))
            parse_days(values.get("ngay_lam"))
        except (ValueError, ScheduleError) as e:
            raise invalid_request(f"JD không hợp lệ: {e}", "validation") from None

    def after_write(self, caller: Caller, model: str, rec: Mapping[str, Any]) -> dict[str, Any]:
        """Sau khi create/update đã ghi: JD -> canh_bao; hồ sơ `da_nghi` -> xóa agent_grant.
        Trả bản ghi mới nhất (revision đúng cho lời gọi kế tiếp)."""
        if model == JD:
            patch = self.risk_patch(caller, rec)
            if patch:
                out = self.rt.records.workflow_write(
                    caller.workflow(), JD, UUID(str(rec["id"])), int(rec["revision"]), patch
                )
                return dict(out["record"])
        elif model == HO_SO and rec.get("trang_thai") == DA_NGHI:
            self.release_grant(caller, rec)
        return dict(rec)

    def risk_patch(self, caller: Caller, rec: Mapping[str, Any]) -> dict[str, Any]:
        """canh_bao = diff_risk(JD hiệu lực của hồ sơ, JD này); rỗng nếu không đổi."""
        current = self.rt.catalogs.current(caller.actor)
        if current is None:
            return {}
        catalog = current[1]
        try:
            new = JobDescription.from_row(rec)
        except ValueError:
            return {}
        ho = self._get(caller, HO_SO, UUID(str(rec["ho_so"])))
        old = self.job(caller, profile_of(ho)) if ho else None
        if old is not None and old.id == new.id:
            old = None
        pii = {m.name for m in catalog.models if any(f.pii != "none" for f in m.fields)}
        money = {m.name for m in catalog.models if any(f.type == "money" for f in m.fields)}
        flags = diff_risk(old, new, pii_models=pii, money_models=money)
        return {} if rec.get("canh_bao") == flags else {"canh_bao": flags}

    def release_grant(self, caller: Caller, rec: Mapping[str, Any]) -> bool:
        """Hồ sơ đã nghỉ: xóa trần tự chủ của (người phụ trách, client) nếu không còn hồ sơ khác
        đang làm cho cùng cặp đó."""
        person, client = _uuid(rec.get("nguoi_phu_trach")), str(rec.get("client_id") or "")
        if person is None or not client:
            return False
        others = self._rows(
            caller,
            HO_SO,
            {"and": [_eq("client_id", client), _eq("nguoi_phu_trach", str(person)),
                     _eq("trang_thai", DANG_LAM)]},
        )  # fmt: skip
        if any(str(o["id"]) != str(rec["id"]) for o in others):
            return False
        return self.rt.store.delete_agent_grant(caller.workflow(), person, client)


# ---------------------------------------------------------------- op dịch vụ ----
def apply_agent_jd(pc: ProcessContext, d: ProcessDefinition) -> ServiceFn:
    """Ngay sau bước IT duyệt: hồ sơ -> `dang_lam` (gỡ lý do tạm dừng), JD hiệu lực = JD này,
    `xt_meta.agent_grant` (người phụ trách, client_id) = mức tự chủ của JD, vai trò ["agent"].

    SoD: người duyệt khác người lập JD (chủ/người tạo) và khác người phụ trách agent. Hồ sơ đã
    nghỉ không mở lại được. JD sai dạng (năng lực, ca làm) -> bước lỗi, instance đứng nguyên."""

    def op(jd_id: str) -> dict[str, Any]:
        if ACTING.get() is None:
            raise OpInputError("apply_agent_jd chỉ chạy ngay sau bước duyệt của một người")
        db = _Data(pc)
        jd = db.get(JD, record_id(jd_id, JD))
        ho = db.get(HO_SO, record_id(jd["ho_so"], HO_SO))
        approver = str(pc.actor.actor_id)
        if approver in {str(jd.get("owner_id")), str(jd.get("created_by")),
                        str(ho.get("nguoi_phu_trach"))}:  # fmt: skip
            raise OpInputError(
                "người duyệt JD phải khác người lập JD và khác người phụ trách agent",
                reason="self_approval",
            )
        if ho.get("trang_thai") == DA_NGHI:
            raise OpInputError(f"hồ sơ agent {ho['id']} đã nghỉ việc, không áp JD được")
        try:
            job = JobDescription.from_row(jd)
            parse_window(job.window)
            parse_days(job.days)
        except ValueError as e:
            raise OpInputError(f"JD {jd['id']} không hợp lệ: {e}") from None
        person = _uuid(ho.get("nguoi_phu_trach"))
        client = str(ho.get("client_id") or "")
        if person is None or not client:
            raise OpInputError(f"hồ sơ agent {ho['id']} thiếu người phụ trách hoặc client_id")
        autonomy = min(max(job.autonomy, 0), 3)
        db.write(
            HO_SO,
            ho,
            {"trang_thai": DANG_LAM, "ly_do_tam_dung": None, "jd_hieu_luc": str(jd["id"])},
        )
        pc.rt.store.upsert_agent_grant(pc.actor, person, client, autonomy, AGENT_ROLES)
        return {"ho_so": str(ho["id"]), "trang_thai": DANG_LAM, "autonomy": autonomy}

    return op


AGENTHR_OPS: dict[str, tuple[tuple[str, ...], Any]] = {
    "apply_agent_jd": (("jd_id",), apply_agent_jd),
}
