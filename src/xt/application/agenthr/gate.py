# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cổng AgentHR (D-AGENTHR-1, kế hoạch 2026-10-20 A2): quyết định thuần, tất định, không I/O.

Chỉ THÊM điều kiện chặn cho agent có hồ sơ (`ho_so_agent`) — không nới bất kỳ kiểm tra nào của
IAM/tự chủ hiện có. Agent không có hồ sơ (tenant chưa cài gói `nhan-su-ai`, hoặc client chưa được
tuyển) giữ nguyên hành vi cũ: adapter trả `None` và cổng không chạy.

Thứ tự (rẻ trước):
0. nhiều hồ sơ chưa nghỉ cho cùng (client_id, người phụ trách) — dữ liệu cũ — chặn kín
   (`duplicate_agent_profile`), không chọn bừa một hồ sơ;
1. trạng thái hồ sơ (`agent_suspended` / `agent_terminated`) — mọi tool, kể cả đọc;
2. ca làm (`outside_working_hours`) — mọi tool, giờ theo múi giờ tenant;
3. tool ghi: (model, thao tác) phải có trong JD hiệu lực (`outside_job_description`);
4. giá trị phiếu (trường `money`) vượt hạn mức (`over_value_limit`, sự cố `vuot_gia_tri`);
5. số việc trong ngày đã chạm hạn mức (`daily_limit_reached`, sự cố `vuot_han_muc`, tự tạm dừng);
6. nộp duyệt không có giấy phép khớp (`held_for_release`, sự cố `giu_nhap`, bản ghi giữ nháp).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Literal

# Tên model/trường cố định theo hợp đồng dữ liệu AgentHR (dùng chung với Desk).
PACK = "nhan-su-ai"
VAI_TRO = "vai_tro_agent"
HO_SO = "ho_so_agent"
JD = "jd_agent"
GIAY_PHEP = "giay_phep_hanh_dong"
SU_CO = "su_co_agent"
PACK_MODELS = frozenset({VAI_TRO, HO_SO, JD, GIAY_PHEP, SU_CO})

DANG_LAM, CHO_DUYET, TAM_DUNG, DA_NGHI = "dang_lam", "cho_duyet", "tam_dung", "da_nghi"
OPS = ("read", "create", "update", "submit")
Op = Literal["read", "create", "update", "submit"]

# Lý do chặn (details.reason) — mở rộng `REASONS` của hợp đồng tool, không breaking.
AGENT_SUSPENDED = "agent_suspended"
AGENT_TERMINATED = "agent_terminated"
OUTSIDE_JD = "outside_job_description"
OUTSIDE_HOURS = "outside_working_hours"
DAILY_LIMIT = "daily_limit_reached"
OVER_VALUE = "over_value_limit"
HELD = "held_for_release"
DUPLICATE_PROFILE = "duplicate_agent_profile"
REASONS = frozenset({AGENT_SUSPENDED, AGENT_TERMINATED, OUTSIDE_JD, OUTSIDE_HOURS, DAILY_LIMIT,
                     OVER_VALUE, HELD, DUPLICATE_PROFILE})  # fmt: skip

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WINDOW = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])-([01][0-9]|2[0-3]):([0-5][0-9])$")
_MODEL = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class ScheduleError(ValueError):
    """`khung_gio` / `ngay_lam` sai định dạng: cổng chặn (fail closed), op áp JD từ chối."""


def parse_window(text: object) -> tuple[time, time] | None:
    """`08:00-18:00` -> (08:00, 18:00); rỗng = cả ngày. Ca qua nửa đêm không hỗ trợ
    (ScheduleError)."""
    s = str(text or "").strip()
    if not s:
        return None
    m = _WINDOW.match(s)
    if m is None:
        raise ScheduleError(f"khung_gio {s!r} không đúng dạng HH:MM-HH:MM")
    start, end = time(int(m[1]), int(m[2])), time(int(m[3]), int(m[4]))
    if start >= end:
        raise ScheduleError(f"khung_gio {s!r}: giờ bắt đầu phải trước giờ kết thúc (không qua đêm)")
    return start, end


def parse_days(text: object) -> frozenset[int] | None:
    """ "mon-fri" / "mon,wed,fri" / "mon-wed,sat" / "all" -> tập weekday (0 = thứ Hai); rỗng = mọi
    ngày. Khoảng phải đi xuôi (mon-fri, không fri-mon)."""
    s = str(text or "").strip().lower()
    if not s or s == "all":
        return None
    out: set[int] = set()
    for part in s.split(","):
        bounds = part.strip().split("-")
        if len(bounds) > 2 or any(b not in _DAYS for b in bounds):
            raise ScheduleError(f"ngay_lam {s!r}: dùng mon..sun, dấu ',' và khoảng 'mon-fri'")
        lo, hi = _DAYS.index(bounds[0]), _DAYS.index(bounds[-1])
        if lo > hi:
            raise ScheduleError(f"ngay_lam {s!r}: khoảng {part!r} phải đi xuôi trong tuần")
        out.update(range(lo, hi + 1))
    return frozenset(out)


def parse_capabilities(value: object) -> dict[str, frozenset[str]]:
    """`nang_luc` -> {model: {thao_tac}}. Sai dạng -> ValueError (JD không dùng được: cổng chặn mọi
    lời ghi).

    Dạng chuẩn (hợp đồng dữ liệu, đi qua được schema `values` của create_record vốn không cho mảng
    lồng trong object): `[{model, read: bool, create: bool, update: bool, submit: bool}]`. Dạng cũ
    `thao_tac` (danh sách hoặc chuỗi "read,create") vẫn đọc được; không trộn hai dạng trong một
    mục."""
    if not isinstance(value, list):
        raise ValueError("nang_luc phải là danh sách [{model, read, create, update, submit}]")
    out: dict[str, set[str]] = {}
    for i, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"nang_luc[{i}] phải là object")
        model = item.get("model")
        if not isinstance(model, str) or not _MODEL.match(model):
            raise ValueError(f"nang_luc[{i}].model không hợp lệ")
        unknown = set(item) - {"model", "thao_tac", "ghi_chu", *OPS}
        if unknown:
            raise ValueError(f"nang_luc[{i}] có khóa lạ {sorted(unknown)}")
        flags = {k: item[k] for k in OPS if k in item}
        if "thao_tac" in item:
            if flags:
                raise ValueError(f"nang_luc[{i}]: dùng read/create/update/submit hoặc thao_tac")
            ops = item["thao_tac"]
            if isinstance(ops, str):
                ops = [o.strip() for o in ops.split(",") if o.strip()]
            if not isinstance(ops, list) or any(o not in OPS for o in ops):
                raise ValueError(f"nang_luc[{i}].thao_tac phải gồm {list(OPS)}")
            chosen = {str(o) for o in ops}
        else:
            if any(not isinstance(v, bool) for v in flags.values()):
                raise ValueError(f"nang_luc[{i}]: read/create/update/submit phải là true/false")
            chosen = {k for k, v in flags.items() if v}
        if not chosen:
            raise ValueError(f"nang_luc[{i}] ({model}) không có thao tác nào")
        out.setdefault(model, set()).update(chosen)
    return {k: frozenset(v) for k, v in out.items()}


def capability_rows(caps: Mapping[str, frozenset[str]]) -> list[dict[str, Any]]:
    """Dạng chuẩn để ghi lại (seed, sao từ vai trò): [{model, read, create, update, submit}]."""
    return [{"model": m, **{op: op in ops for op in OPS}} for m, ops in sorted(caps.items())]


def _int(v: object) -> int:
    if v is None or v == "":
        return 0
    try:
        return max(int(float(str(v))), 0)
    except ValueError:
        return 0


@dataclass(frozen=True, slots=True)
class Profile:
    """Hồ sơ agent đã đọc bằng ngữ cảnh workflow (không qua RLS của người gọi)."""

    id: str
    status: str
    pause_reason: str | None = None
    valid_until: date | None = None
    jd_id: str | None = None
    owner: str | None = None  # người phụ trách (person id)
    client_id: str = ""


@dataclass(frozen=True, slots=True)
class JobDescription:
    id: str
    capabilities: Mapping[str, frozenset[str]]
    autonomy: int = 0
    daily_limit: int = 0  # 0 = không giới hạn
    value_limit: int = 0  # VND, 0 = không giới hạn
    window: str | None = None
    days: str | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> JobDescription:
        return cls(
            str(row["id"]),
            parse_capabilities(row.get("nang_luc")),
            _int(row.get("muc_tu_chu")),
            _int(row.get("han_muc_viec_ngay")),
            _int(row.get("han_muc_gia_tri")),
            row.get("khung_gio"),
            row.get("ngay_lam"),
        )


@dataclass(frozen=True, slots=True)
class Permit:
    action: str  # "request.submit:<model>" hoặc "request.submit:*"
    since: date | None = None
    until: date | None = None

    def covers(self, action: str, today: date) -> bool:
        if self.since is not None and today < self.since:
            return False
        if self.until is not None and today > self.until:
            return False
        kind, _, target = self.action.partition(":")
        want_kind, _, want_target = action.partition(":")
        return kind == want_kind and target in (want_target, "*")


@dataclass(frozen=True, slots=True)
class Intent:
    """Một lời ghi của agent quy về (model, thao tác) của JD."""

    tool: str
    model: str
    op: Op
    amounts: tuple[int, ...] = ()  # giá trị trường money của bản ghi sau lời ghi

    @property
    def release(self) -> str:
        return f"request.submit:{self.model}"


Incident = Literal["vuot_han_muc", "vuot_gia_tri", "ngoai_gio", "ngoai_jd", "giu_nhap", "tam_dung"]


@dataclass(frozen=True, slots=True)
class Block:
    reason: str
    message: str
    remediation: str
    incident: Incident | None = None
    suspend: bool = False  # tự tạm dừng hồ sơ (ly_do_tam_dung = han_muc)
    details: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- ánh xạ tool -> JD ----
_RECORD_OPS: Mapping[str, Op] = {
    "create_record": "create",
    "create_task": "create",
    "update_record": "update",
    "transition_record": "update",
    "submit_request": "submit",
}
# Tool ghi khác: tài nguyên IAM tương ứng, thao tác create (JD phải liệt kê mới được làm).
_OTHER_WRITES: Mapping[str, str] = {
    "notify": "notification",
    "upload_file": "file",
    "start_process": "process",
    "propose_model_change": "model_proposal",
}
# decide_request luôn human_only (người được giao xác nhận bằng OTP): chỉ qua cổng trạng thái/ca.
NOT_JD_GATED = frozenset({"decide_request"})


def ref_model(ref: object) -> str | None:
    s = str(ref or "")
    if ":" not in s:
        return None
    model = s.rsplit(":", 1)[0].rsplit(":", 1)[-1]
    return model if _MODEL.match(model) else None


def intent_model(tool: str, args: Mapping[str, Any]) -> tuple[str, Op] | None:
    """(model, thao tác) mà lời gọi ghi; None = tool không qua JD (đọc hoặc decide_request)."""
    if tool in NOT_JD_GATED:
        return None
    op = _RECORD_OPS.get(tool)
    if op is not None:
        if tool == "create_record":
            model = str(args.get("model_id") or "")
        elif tool == "create_task":
            model = str(args.get("task_type") or "task")
        else:
            model = ref_model(args.get("ref")) or ""
        return model, op
    if tool in _OTHER_WRITES:
        return _OTHER_WRITES[tool], "create"
    return None


# ---------------------------------------------------------------- quyết định ----
def _remedy(text: str) -> str:
    return text + " Do not retry it another way; explain the reason to the user."


def duplicate_block(live: Sequence[Profile]) -> Block | None:
    """Hơn một hồ sơ chưa `da_nghi` cho cùng (client_id, người phụ trách): không biết hồ sơ nào là
    "thật" nên chặn kín (một hồ sơ tạm dừng không được bị hồ sơ khác che)."""
    if len(live) < 2:
        return None
    return Block(
        DUPLICATE_PROFILE,
        "This AI agent has more than one active profile in xT (Nhân sự AI), so it cannot act.",
        _remedy(
            "Tell the user to ask IT to terminate (da_nghi) the extra profiles of this agent in"
            " xT Desk, keeping exactly one."
        ),
        details={"profiles": ",".join(p.id for p in live), "count": len(live)},
    )


def status_block(p: Profile, today: date) -> Block | None:
    if p.status == DANG_LAM and (p.valid_until is None or today <= p.valid_until):
        return None
    if p.status == DA_NGHI:
        return Block(
            AGENT_TERMINATED,
            "This AI agent has been terminated in xT (Nhân sự AI); it can no longer act.",
            _remedy("Tell the user this agent was let go; a person must hire a new agent."),
            details={"status": p.status},
        )
    expired = p.status == DANG_LAM
    return Block(
        AGENT_SUSPENDED,
        "This AI agent is not on duty in xT (Nhân sự AI): "
        + ("its assignment has expired." if expired else f"status {p.status}."),
        _remedy(
            "Tell the user the agent is suspended; its owner or IT reopens it through a job"
            " description approval in xT Desk."
        ),
        details={
            "status": "het_hieu_luc" if expired else p.status,
            **({"pause_reason": p.pause_reason} if p.pause_reason else {}),
        },
    )


def hours_block(jd: JobDescription | None, now_local: datetime) -> Block | None:
    if jd is None:
        return None
    try:
        window, days = parse_window(jd.window), parse_days(jd.days)
    except ScheduleError as e:
        return Block(
            OUTSIDE_HOURS,
            "The agent's working schedule is invalid, so it cannot act.",
            _remedy("Ask IT to fix khung_gio/ngay_lam of the job description."),
            details={"error": str(e)[:200]},
        )
    on_day = days is None or now_local.weekday() in days
    t = now_local.time().replace(tzinfo=None)
    in_hours = window is None or window[0] <= t < window[1]
    if on_day and in_hours:
        return None
    return Block(
        OUTSIDE_HOURS,
        "Outside this agent's working hours (tenant time).",
        _remedy("Tell the user to retry during the agent's working hours."),
        details={
            "now": now_local.isoformat(timespec="minutes"),
            "khung_gio": jd.window or "",
            "ngay_lam": jd.days or "",
        },
    )


def jd_block(jd: JobDescription | None, intent: Intent) -> Block | None:
    allowed = jd.capabilities.get(intent.model, frozenset()) if jd is not None else frozenset()
    if intent.model not in PACK_MODELS and intent.op in allowed:
        return None
    return Block(
        OUTSIDE_JD,
        f"'{intent.op}' on '{intent.model}' is outside this agent's job description.",
        _remedy(
            "Tell the user the agent's job description does not include this; its owner can"
            " propose a JD change for IT approval in xT Desk."
        ),
        details={"model": intent.model, "operation": intent.op, "jd": jd.id if jd else None},
    )


def value_block(jd: JobDescription, intent: Intent) -> Block | None:
    if jd.value_limit <= 0 or not intent.amounts:
        return None
    worst = max(intent.amounts)
    if worst <= jd.value_limit:
        return None
    return Block(
        OVER_VALUE,
        f"Value {worst:,} VND is over this agent's limit of {jd.value_limit:,} VND per item.",
        _remedy("Tell the user a person must handle this item, or ask IT to raise the limit."),
        incident="vuot_gia_tri",
        details={"value": worst, "limit": jd.value_limit, "model": intent.model},
    )


def limit_block(jd: JobDescription, count: int) -> Block | None:
    if jd.daily_limit <= 0 or count < jd.daily_limit:
        return None
    return Block(
        DAILY_LIMIT,
        f"The agent reached its daily limit of {jd.daily_limit} write actions and was paused.",
        _remedy("Tell the user the agent is paused for today; its owner resolves the incident."),
        incident="vuot_han_muc",
        suspend=True,
        details={"count": count, "limit": jd.daily_limit},
    )


def release_block(intent: Intent, permits: Iterable[Permit], today: date) -> Block | None:
    if intent.op != "submit" or any(p.covers(intent.release, today) for p in permits):
        return None
    return Block(
        HELD,
        "Drafted and kept as a draft: submitting needs a release (giấy phép hành động) from the"
        " agent's owner.",
        "Tell the user the item is drafted and waiting; once they open a release for"
        f" '{intent.release}' in xT Desk, call submit_request again.",
        incident="giu_nhap",
        details={"release": intent.release},
    )


def check_write(
    jd: JobDescription | None,
    intent: Intent,
    count: int,
    permits: Sequence[Permit],
    today: date,
) -> Block | None:
    """Bước 3-6 cho một lời ghi (sau khi trạng thái + ca đã qua)."""
    blocked = jd_block(jd, intent)
    if blocked is not None or jd is None:
        return blocked
    return (
        value_block(jd, intent) or limit_block(jd, count) or release_block(intent, permits, today)
    )
