# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""ProcessSpec v2: pydantic `extra=forbid`, lỗi tiếng Việt, xuất JSON Schema cho agent (G-P8).

Năng lực mới so với spike v1:
- `type: parallel` (fork/join, mode all) — G-P1
- `trigger: {kind: timer, rrule, tz}` — G-P2
- `outcomes` tường minh: approve, reject, return_for_revision, delegate, request_info — G-P3
- `assignee` động: role(x), role(x) in department(v), v.manager, department_head(...), user(v) + fallback — G-P4
- `sod` theo bước (not_requester, distinct_from) — tầng workflow của G-I1
"""
from __future__ import annotations

import difflib
import re
import typing
import unicodedata
from datetime import date, datetime
from typing import Annotated, Literal, Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .engine import UnsafeExpression, check_expr, expr_names

ID_PATTERN = r"^[a-z][a-z0-9_]{0,40}$"
DEPT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$"
DUR_RE = re.compile(r"^P(?:(\d{1,3})D)?(?:T(?:(\d{1,4})H)?(?:(\d{1,5})M)?(?:(\d{1,6})S)?)?$")
MAX_DURATION_S = 366 * 86400
RESERVED_VARS = {"requester", "true", "false", "none"}
RESERVED_PREFIX = ("xt_",)
RESERVED_SUFFIX = ("_decision", "_result", "_comment", "_actor", "_revise", "_info")
TIMER_CTX_VARS = {"fire_at", "fire_date", "period_start", "period_start_date", "period_end_date"}

Id = Annotated[str, Field(pattern=ID_PATTERN, description="Định danh: a-z, 0-9, '_', bắt đầu bằng chữ, tối đa 41 ký tự")]
DeptKey = Annotated[str, Field(pattern=DEPT_PATTERN, description="Mã phòng ban, ví dụ KD")]


def _clean_text(v: str) -> str:
    if any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co") and c not in "\t" for c in v):
        raise ValueError("không được chứa ký tự điều khiển/ẩn")
    return v


Text = Annotated[str, Field(min_length=1, max_length=200, description="Tên hiển thị (tiếng Việt), ≤200 ký tự")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, validate_default=True)


# ---------------------------------------------------------------- expressions / durations
def _expr(v: str, key: str = "") -> str:
    try:
        check_expr(v)
    except UnsafeExpression as e:
        raise ValueError(f"biểu thức{' ' + key if key else ''} không an toàn: {e}") from None
    return v


Expr = Annotated[str, Field(min_length=1, max_length=500, description="Biểu thức an toàn: so sánh, and/or/not, in, chỉ mục, + - /; không gọi hàm, không thuộc tính")]


def duration_seconds(v: str) -> int:
    m = DUR_RE.match(v)
    if not m or v in ("P", "PT") or v.endswith("T"):
        raise ValueError(f"thời lượng {v!r} không đúng ISO 8601 (ví dụ PT4H, P2D, P1DT12H)")
    d, h, mi, s = (int(x or 0) for x in m.groups())
    total = d * 86400 + h * 3600 + mi * 60 + s
    if total <= 0 or total > MAX_DURATION_S:
        raise ValueError(f"thời lượng {v!r} phải lớn hơn 0 và không quá 366 ngày")
    return total


# ---------------------------------------------------------------- assignee (no expressions, fixed grammar)
_ARG = r"(?:'(?P<{n}lit>[A-Za-z0-9][A-Za-z0-9_-]{{0,40}})'|department\((?P<{n}dof>[a-z][a-z0-9_]{{0,40}})\)|(?P<{n}var>[a-z][a-z0-9_]{{0,40}}))"
SHORTHAND = [
    ("role_in", re.compile(r"^role\((?P<role>[a-z][a-z0-9_]{0,40})\)\s+in\s+" + _ARG.format(n="d") + r"$")),
    ("role", re.compile(r"^role\((?P<role>[a-z][a-z0-9_]{0,40})\)$")),
    ("manager_dot", re.compile(r"^(?P<var>[a-z][a-z0-9_]{0,40})\.manager$")),
    ("manager_fn", re.compile(r"^manager\((?P<var>[a-z][a-z0-9_]{0,40})(?:\s*,\s*(?P<levels>[1-5]))?\)$")),
    ("head", re.compile(r"^department_head\(" + _ARG.format(n="d") + r"\)$")),
    ("user", re.compile(r"^user\((?P<var>[a-z][a-z0-9_]{0,40})\)$")),
]
ASSIGNEE_HELP = ("cú pháp người nhận hợp lệ: role(x) | role(x) in department(requester) | role(x) in 'KD' | requester.manager | "
                 "manager(requester, 2) | department_head(department(requester)) | department_head('KD') | department_head(bien_phong_ban) | user(bien_nguoi_dung)")


def parse_assignee(s: str) -> dict:
    s = s.strip()
    for kind, rx in SHORTHAND:
        m = rx.match(s)
        if not m:
            continue
        g = m.groupdict()
        if kind in ("role", "role_in", "head"):
            out = {"kind": "role" if kind != "head" else "department_head"}
            if kind != "head":
                out["role"] = g["role"]
            if g.get("dlit"):
                out["department"] = g["dlit"]
            elif g.get("ddof"):
                out["department_of"] = g["ddof"]
            elif g.get("dvar"):
                if kind == "role_in" and g["dvar"] == "department":
                    break
                out["department_var"] = g["dvar"]
            return out
        if kind in ("manager_dot", "manager_fn"):
            return {"kind": "manager", "user_var": g["var"], "levels": int(g.get("levels") or 1)}
        if kind == "user":
            return {"kind": "user", "user_var": g["var"]}
    raise ValueError(f"người nhận {s!r} không hợp lệ; {ASSIGNEE_HELP}")


class Assignee(Strict):
    """Người nhận bước, giải lúc chạy từ dữ liệu tổ chức qua resolver đã đăng ký (không phải biểu thức)."""
    kind: Literal["role", "manager", "department_head", "user"] = Field(description="role: theo vai trò; manager: quản lý của người trong biến; department_head: trưởng phòng; user: người trong biến")
    role: Optional[Id] = Field(None, description="Vai trò (kind=role), phải có trong roles")
    department: Optional[DeptKey] = Field(None, description="Mã phòng cố định")
    department_of: Optional[Id] = Field(None, description="Biến người dùng; lấy phòng của người đó (ví dụ requester)")
    department_var: Optional[Id] = Field(None, description="Biến chứa mã phòng (input kiểu department)")
    user_var: Optional[Id] = Field(None, description="Biến chứa người dùng (kind=user/manager)")
    levels: int = Field(1, ge=1, le=5, description="Số cấp quản lý (kind=manager)")
    fallback: list[Union[str, "Assignee"]] = Field(default_factory=list, max_length=5, description="Chuỗi thay thế khi không giải được (leo thang, không bao giờ bỏ qua bước)")

    @field_validator("fallback", mode="after")
    @classmethod
    def _fb(cls, v):
        return [Assignee.model_validate(parse_assignee(x)) if isinstance(x, str) else x for x in v]

    @model_validator(mode="after")
    def _shape(self):
        dept_fields = [f for f in ("department", "department_of", "department_var") if getattr(self, f)]
        if len(dept_fields) > 1:
            raise ValueError(f"chỉ được chọn một trong department/department_of/department_var (đang có {dept_fields})")
        if self.kind == "role":
            if not self.role:
                raise ValueError("kind=role cần khóa 'role'")
            if self.user_var:
                raise ValueError("kind=role không dùng user_var")
        elif self.kind == "department_head":
            if self.role or self.user_var:
                raise ValueError("kind=department_head không dùng role/user_var")
            if not dept_fields:
                raise ValueError("kind=department_head cần department, department_of hoặc department_var")
        else:
            if not self.user_var:
                raise ValueError(f"kind={self.kind} cần khóa 'user_var'")
            if self.role or dept_fields:
                raise ValueError(f"kind={self.kind} không dùng role/department")
        if self.kind != "manager" and self.levels != 1:
            raise ValueError("levels chỉ dùng cho kind=manager")
        return self

    def is_static(self) -> bool:
        return self.kind == "role" and not (self.department or self.department_of or self.department_var)

    def describe(self) -> str:
        if self.kind == "role":
            where = (f" thuộc phòng {self.department}" if self.department else f" cùng phòng với {self.department_of}" if self.department_of
                     else f" thuộc phòng trong biến {self.department_var}" if self.department_var else "")
            return f"Vai trò {self.role}{where}"
        if self.kind == "manager":
            return f"Quản lý {'trực tiếp' if self.levels == 1 else f'cấp {self.levels}'} của {self.user_var}"
        if self.kind == "department_head":
            return ("Trưởng phòng " + (self.department if self.department else f"của {self.department_of}" if self.department_of else f"trong biến {self.department_var}"))
        return f"Người trong biến {self.user_var}"

    def key(self) -> str:
        return "|".join(f"{k}={v}" for k, v in self.model_dump(exclude={"fallback"}).items() if v not in (None, 1) or k == "kind")

    def vars(self) -> set:
        return {v for v in (self.department_of, self.department_var, self.user_var) if v}

    def all(self):
        yield self
        for f in self.fallback:
            yield from f.all()


AssigneeField = Annotated[Union[Annotated[str, Field(max_length=120, description=ASSIGNEE_HELP)], Assignee],
                          Field(description="Người nhận: chuỗi rút gọn hoặc object Assignee")]


def _to_assignee(v):
    return Assignee.model_validate(parse_assignee(v)) if isinstance(v, str) else v


# ---------------------------------------------------------------- steps
DecisionOutcome = Literal["approve", "reject", "return_for_revision", "delegate", "request_info"]
TaskOutcome = Literal["done", "delegate", "request_info"]


class Sla(Strict):
    after: Annotated[str, Field(max_length=24, description="ISO 8601, ví dụ PT24H, P2D")]
    escalate_to: AssigneeField

    _a = field_validator("after")(classmethod(lambda cls, v: (duration_seconds(v), v)[1]))
    _e = field_validator("escalate_to", mode="after")(classmethod(lambda cls, v: _to_assignee(v)))


class OnReturn(Strict):
    resume: Literal["restart", "same_step"] = Field("restart", description="restart: chạy lại từ bước đầu (định tuyến lại theo dữ liệu đã sửa); same_step: quay lại đúng bước đã trả")
    max_rounds: int = Field(3, ge=1, le=10, description="Số lần trả lại tối đa; quá số này runtime từ chối return_for_revision")
    to_step: Optional[Id] = Field(None, description="Trả về một bước người ở trước (cùng cấp, ví dụ nghiệm thu không đạt -> làm lại), thay vì về người đề nghị")

    @model_validator(mode="after")
    def _ts(self):
        if self.to_step and self.resume != "restart":
            raise ValueError("to_step không dùng cùng resume")
        return self


class SoD(Strict):
    not_requester: bool = Field(True, description="Người đề nghị không được tự làm bước này")
    distinct_from: list[Id] = Field(default_factory=list, max_length=10, description="Người làm bước này phải khác người đã làm các bước được liệt kê")


class _Human(Strict):
    id: Id
    name: Text
    assignee: AssigneeField
    when: Optional[Expr] = None
    sla: Optional[Sla] = None
    _a = field_validator("assignee", mode="after")(classmethod(lambda cls, v: _to_assignee(v)))
    _w = field_validator("when")(classmethod(lambda cls, v: v if v is None else _expr(v)))
    _n = field_validator("name")(classmethod(lambda cls, v: _clean_text(v)))


class ApproveStep(_Human):
    """Bước quyết định của con người. Chỉ các outcome khai ở đây mới có nhánh; giá trị khác bị từ chối (fail closed)."""
    type: Literal["approve"]
    outcomes: list[DecisionOutcome] = Field(default_factory=lambda: ["approve", "reject"], min_length=1, max_length=5)
    on_return: Optional[OnReturn] = None
    sod: SoD = Field(default_factory=SoD)

    @model_validator(mode="after")
    def _o(self):
        if len(set(self.outcomes)) != len(self.outcomes):
            raise ValueError("outcomes bị trùng")
        if "approve" not in self.outcomes:
            raise ValueError("bước approve phải có outcome 'approve'")
        if self.on_return is not None and "return_for_revision" not in self.outcomes:
            raise ValueError("on_return chỉ dùng khi outcomes có 'return_for_revision'")
        if "return_for_revision" in self.outcomes and self.on_return is None:
            self.on_return = OnReturn()
        return self


class TaskStep(_Human):
    """Bước công việc (không phải duyệt): nhận việc, làm, bàn giao."""
    type: Literal["task"]
    outcomes: list[TaskOutcome] = Field(default_factory=lambda: ["done"], min_length=1, max_length=3)
    sod: SoD = Field(default_factory=lambda: SoD(not_requester=False))

    @model_validator(mode="after")
    def _o(self):
        if len(set(self.outcomes)) != len(self.outcomes) or "done" not in self.outcomes:
            raise ValueError("outcomes của task phải có 'done' và không trùng")
        return self


class ServiceStep(Strict):
    type: Literal["service"]
    id: Id
    name: Text
    op: Id = Field(description="Tên op trong allowlist của deployment")
    params: dict[Id, Expr] = Field(default_factory=dict, max_length=20)
    when: Optional[Expr] = None
    _w = field_validator("when")(classmethod(lambda cls, v: v if v is None else _expr(v)))
    _p = field_validator("params")(classmethod(lambda cls, v: {k: _expr(x, k) for k, x in v.items()}))
    _n = field_validator("name")(classmethod(lambda cls, v: _clean_text(v)))


BranchStep = Annotated[Union[ApproveStep, TaskStep, ServiceStep], Field(discriminator="type")]


class ParallelStep(Strict):
    """Các nhánh chạy song song, gộp lại khi tất cả xong (mode all). Từ chối ở một nhánh kết thúc cả quy trình."""
    type: Literal["parallel"]
    id: Id
    name: Text
    when: Optional[Expr] = None
    mode: Literal["all"] = Field("all", description="Chỉ hỗ trợ 'all' (join đủ mọi nhánh)")
    branches: list[Annotated[list[BranchStep], Field(min_length=1, max_length=20)]] = Field(min_length=2, max_length=8)
    _w = field_validator("when")(classmethod(lambda cls, v: v if v is None else _expr(v)))
    _n = field_validator("name")(classmethod(lambda cls, v: _clean_text(v)))


Step = Annotated[Union[ApproveStep, TaskStep, ServiceStep, ParallelStep], Field(discriminator="type")]


# ---------------------------------------------------------------- inputs / trigger
class InputSpec(Strict):
    type: Literal["str", "int", "number", "bool", "user", "department", "date", "object"]
    required: bool = True
    editable_on_revision: bool = Field(False, description="Người đề nghị được sửa giá trị này khi bị trả lại bổ sung")
    description: Optional[Text] = None


class ManualTrigger(Strict):
    kind: Literal["manual"]


RRULE_KEYS = {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "BYMONTH", "BYHOUR", "BYMINUTE"}
_DAY = r"(?:[+-]?[1-5])?(?:MO|TU|WE|TH|FR|SA|SU)"
RRULE_VALUE = {
    "FREQ": re.compile(r"^(HOURLY|DAILY|WEEKLY|MONTHLY|YEARLY)$"),
    "INTERVAL": re.compile(r"^([1-9]\d{0,2})$"),
    "BYDAY": re.compile(rf"^{_DAY}(?:,{_DAY}){{0,6}}$"),
    "BYMONTHDAY": re.compile(r"^-?(?:[1-9]|[12]\d|3[01])(?:,-?(?:[1-9]|[12]\d|3[01])){0,30}$"),
    "BYMONTH": re.compile(r"^(?:[1-9]|1[0-2])(?:,(?:[1-9]|1[0-2])){0,11}$"),
    "BYHOUR": re.compile(r"^(?:1?\d|2[0-3])(?:,(?:1?\d|2[0-3])){0,23}$"),
    "BYMINUTE": re.compile(r"^(?:[1-5]?\d)$"),
}


def validate_rrule(v: str) -> dict:
    if len(v) > 200:
        raise ValueError("rrule quá dài")
    parts = {}
    for item in v.split(";"):
        if "=" not in item:
            raise ValueError(f"rrule sai cú pháp ở {item!r} (dạng KEY=VALUE;...)")
        k, val = item.split("=", 1)
        if k not in RRULE_KEYS:
            raise ValueError(f"khóa rrule {k!r} không được phép; chỉ chấp nhận {sorted(RRULE_KEYS)} (COUNT/UNTIL/BYSECOND/DTSTART bị cấm)")
        if k in parts:
            raise ValueError(f"khóa rrule {k!r} bị lặp")
        if not RRULE_VALUE[k].match(val):
            raise ValueError(f"giá trị rrule {k}={val!r} không hợp lệ")
        parts[k] = val
    if "FREQ" not in parts:
        raise ValueError("rrule thiếu FREQ")
    if parts["FREQ"] != "HOURLY" and ("BYHOUR" not in parts or "BYMINUTE" not in parts):
        raise ValueError("rrule theo ngày/tuần/tháng phải khai rõ BYHOUR và BYMINUTE (giờ chạy)")
    if parts["FREQ"] == "HOURLY" and "BYMINUTE" not in parts:
        raise ValueError("rrule HOURLY phải khai BYMINUTE")
    return parts


class TimerTrigger(Strict):
    """Khởi tạo định kỳ. Poller của xt-worker tạo đúng 1 instance cho mỗi kỳ (khóa duy nhất process_key + kỳ)."""
    kind: Literal["timer"]
    rrule: Annotated[str, Field(max_length=200, description="RFC 5545 rút gọn, ví dụ FREQ=WEEKLY;BYDAY=MO;BYHOUR=8;BYMINUTE=0")]
    tz: Annotated[str, Field(max_length=64, description="Múi giờ IANA, ví dụ Asia/Ho_Chi_Minh")]
    input: dict[Id, Expr] = Field(default_factory=dict, max_length=20, description=f"Ánh xạ input <- biểu thức trên biến kỳ {sorted(TIMER_CTX_VARS)}")
    catchup: Literal["latest", "none"] = Field("latest", description="latest: bỏ lỡ nhiều kỳ chỉ chạy kỳ gần nhất; none: bỏ kỳ đã trễ quá grace")
    grace: Annotated[str, Field(max_length=24)] = "PT1H"

    _r = field_validator("rrule")(classmethod(lambda cls, v: (validate_rrule(v), v)[1]))
    _g = field_validator("grace")(classmethod(lambda cls, v: (duration_seconds(v), v)[1]))
    _i = field_validator("input")(classmethod(lambda cls, v: {k: _expr(x, k) for k, x in v.items()}))

    @field_validator("tz")
    @classmethod
    def _tz(cls, v):
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"múi giờ {v!r} không phải tên IANA hợp lệ") from None
        return v


Trigger = Annotated[Union[ManualTrigger, TimerTrigger], Field(discriminator="kind")]


# ---------------------------------------------------------------- process
class ProcessSpec(Strict):
    """Đặc tả quy trình xT v2. Agent chỉ nộp JSON theo schema này, không bao giờ nộp XML."""
    id: Id
    name: Text
    version: int = Field(ge=1, le=100000)
    description: Optional[Annotated[str, Field(max_length=2000)]] = None
    roles: list[Id] = Field(default_factory=list, max_length=50)
    inputs: dict[Id, InputSpec] = Field(default_factory=dict, max_length=50)
    trigger: Trigger = Field(default_factory=lambda: ManualTrigger(kind="manual"))
    fallback_assignee: Optional[AssigneeField] = Field(None, description="Bắt buộc khi có người nhận động: nơi leo thang khi không giải được người nhận")
    steps: list[Step] = Field(min_length=1, max_length=60)

    _f = field_validator("fallback_assignee", mode="after")(classmethod(lambda cls, v: v if v is None else _to_assignee(v)))
    _n = field_validator("name")(classmethod(lambda cls, v: _clean_text(v)))

    def iter_steps(self):
        for st in self.steps:
            if isinstance(st, ParallelStep):
                yield st, None
                for bi, br in enumerate(st.branches):
                    for s in br:
                        yield s, (st.id, bi)
            else:
                yield st, None

    @model_validator(mode="after")
    def _cross(self):
        errs = []
        timer = isinstance(self.trigger, TimerTrigger)
        if len(set(self.roles)) != len(self.roles):
            errs.append("roles bị trùng")
        for name in self.inputs:
            if name in RESERVED_VARS or name.startswith(RESERVED_PREFIX) or name.endswith(RESERVED_SUFFIX) or name in TIMER_CTX_VARS:
                errs.append(f"inputs.{name}: tên biến dành riêng cho runtime")
        ids, human_ids = [], set()
        for st, par in self.iter_steps():
            ids.append(st.id)
            if isinstance(st, (ApproveStep, TaskStep)):
                human_ids.add(st.id)
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            errs.append(f"id bước bị trùng: {dup}")
        # biến khả dụng theo thứ tự (G-P8: `when` dùng biến không tồn tại phải lỗi lúc compile, không phải lúc chạy)
        base = set(self.inputs) | ({"xt_fire_at", "xt_period_key"} if timer else {"requester"})
        user_vars = {k for k, v in self.inputs.items() if v.type == "user"} | (set() if timer else {"requester"})
        dept_vars = {k for k, v in self.inputs.items() if v.type == "department"}
        dynamic = False

        def check_assignee(a: Assignee, where: str):
            nonlocal dynamic
            for x in a.all():
                if not x.is_static():
                    dynamic = True
                if x.role and x.role not in self.roles:
                    errs.append(f"{where}: vai trò {x.role!r} không có trong roles {self.roles}")
                for v in (x.department_of, x.user_var):
                    if v and v not in user_vars:
                        errs.append(f"{where}: biến {v!r} phải là input kiểu user" + ("" if timer else " hoặc requester") + f" (đang có {sorted(user_vars)})")
                if x.department_var and x.department_var not in dept_vars:
                    errs.append(f"{where}: biến {x.department_var!r} phải là input kiểu department")

        def check_names(expr, avail, where):
            unknown = expr_names(expr) - avail - {"True", "False", "None"}
            if unknown:
                errs.append(f"{where}: biểu thức {expr!r} dùng biến chưa khai báo {sorted(unknown)} (biến có sẵn: {sorted(avail)})")

        def walk(steps, avail, where, in_parallel=False):
            for i, st in enumerate(steps):
                w = f"{where}[{i}]({st.id})"
                if st.when:
                    check_names(st.when, avail, f"{w}.when")
                if isinstance(st, ParallelStep):
                    produced = set()
                    for bi, br in enumerate(st.branches):
                        produced |= walk(br, set(avail), f"{w}.branches[{bi}]", True)
                    avail |= produced
                    continue
                if isinstance(st, ServiceStep):
                    for k, e in st.params.items():
                        check_names(e, avail, f"{w}.params.{k}")
                    avail.add(f"{st.id}_result")
                    continue
                check_assignee(st.assignee, f"{w}.assignee")
                if st.sla:
                    check_assignee(st.sla.escalate_to, f"{w}.sla.escalate_to")
                if in_parallel and "return_for_revision" in st.outcomes:
                    errs.append(f"{w}: return_for_revision không dùng được trong nhánh song song (sẽ làm hỏng join); dùng request_info")
                ts = st.on_return.to_step if isinstance(st, ApproveStep) and st.on_return else None
                if ts and ts not in [p.id for p in steps[:i] if isinstance(p, (ApproveStep, TaskStep))]:
                    errs.append(f"{w}.on_return.to_step: {ts!r} phải là bước người đứng trước, cùng cấp")
                if timer and ("return_for_revision" in st.outcomes) and not ts:
                    errs.append(f"{w}: quy trình khởi tạo bằng timer không có người đề nghị để trả lại")
                for d in st.sod.distinct_from:
                    if d not in human_ids:
                        errs.append(f"{w}.sod.distinct_from: {d!r} không phải bước người")
                    if d == st.id:
                        errs.append(f"{w}.sod.distinct_from: không được tham chiếu chính nó")
                avail |= {f"{st.id}_decision", f"{st.id}_comment"}
            return avail

        walk(self.steps, set(base), "steps")
        editable = [k for k, v in self.inputs.items() if v.editable_on_revision]
        for st, _ in self.iter_steps():
            if isinstance(st, ApproveStep) and st.on_return and st.on_return.resume == "same_step" and editable:
                errs.append(f"bước {st.id}: on_return.resume=same_step không dùng được khi có input sửa được {editable} "
                            "(sửa dữ liệu phải định tuyến lại từ đầu, nếu không các bước 'when' phía trước bị bỏ qua)")
        if timer:
            for k in self.trigger.input:
                if k not in self.inputs:
                    errs.append(f"trigger.input.{k}: không phải input đã khai báo")
            for k, e in self.trigger.input.items():
                check_names(e, TIMER_CTX_VARS, f"trigger.input.{k}")
            missing = [k for k, v in self.inputs.items() if v.required and k not in self.trigger.input]
            if missing:
                errs.append(f"trigger.input thiếu input bắt buộc {missing}")
        if self.fallback_assignee is not None:
            check_assignee(self.fallback_assignee, "fallback_assignee")
            if not list(self.fallback_assignee.all())[-1].is_static():
                errs.append("fallback_assignee phải kết thúc bằng vai trò tĩnh role(x) (phần tử cuối của chuỗi fallback) để luôn có nơi leo thang")
        if dynamic and self.fallback_assignee is None:
            errs.append("có người nhận động nên phải khai fallback_assignee (nơi leo thang khi không giải được người nhận)")
        if errs:
            raise ValueError("; ".join(errs))
        return self


# ---------------------------------------------------------------- Vietnamese errors
class SpecError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Đặc tả quy trình không hợp lệ:\n" + "\n".join(f"  - {e}" for e in errors))


def _unwrap(tp):
    while True:
        origin = typing.get_origin(tp)
        if origin is Annotated:
            tp = typing.get_args(tp)[0]
        elif origin is Union or (origin is not None and str(origin) == "<class 'types.UnionType'>"):
            return [a for a in typing.get_args(tp) if a is not type(None)]
        else:
            return [tp]


def _model_at(loc):
    cands = [ProcessSpec]
    for seg in loc:
        if isinstance(seg, str) and seg.startswith(("function-", "constrained-")):
            continue   # nhãn nội bộ của pydantic cho validator/union, không phải khóa
        nxt = []
        for c in cands:
            for t in _unwrap(c) if not isinstance(c, type) or not issubclass(c, BaseModel) else [c]:
                if isinstance(t, type) and issubclass(t, BaseModel):
                    if seg == t.__name__:
                        nxt.append(t)
                    elif seg in t.model_fields:
                        nxt.append(t.model_fields[seg].annotation)
                    elif isinstance(seg, str) and "type" in t.model_fields and seg in typing.get_args(t.model_fields["type"].annotation):
                        nxt.append(t)
                    elif isinstance(seg, str) and "kind" in t.model_fields and seg in typing.get_args(t.model_fields["kind"].annotation):
                        nxt.append(t)
                else:
                    o = typing.get_origin(t)
                    if o is list and isinstance(seg, int):
                        nxt.append(typing.get_args(t)[0])
                    elif o is dict and isinstance(seg, str):
                        nxt.append(typing.get_args(t)[1])
        cands = nxt
    models = []
    for c in cands:
        for t in _unwrap(c):
            if isinstance(t, type) and issubclass(t, BaseModel):
                models.append(t)
    return models


TAGS = {"approve", "task", "service", "parallel", "manual", "timer", "str", "Assignee"}
ID_HINT = "chỉ gồm a-z, 0-9, '_', bắt đầu bằng chữ thường, tối đa 41 ký tự"


def _fmt_loc(loc):
    out = ""
    for seg in loc:
        if isinstance(seg, int):
            out += f"[{seg}]"
        elif seg in TAGS or seg.startswith(("function-", "list[", "dict[", "constrained-")):
            continue
        else:
            out += ("." if out else "") + seg
    return out or "(gốc)"


def vi_errors(e: ValidationError) -> list[str]:
    msgs, seen = [], set()
    for err in e.errors(include_url=False):
        loc, t, ctx, inp = err["loc"], err["type"], err.get("ctx") or {}, err.get("input")
        if t == "string_type" and loc and loc[-1] in ("str", "constrained-str") and isinstance(inp, dict):
            continue  # nhánh str của union str|Assignee: lỗi thật nằm ở nhánh Assignee
        where = _fmt_loc(loc)
        if t == "extra_forbidden":
            parent = _model_at(loc[:-1])
            valid = sorted(set().union(*[set(m.model_fields) for m in parent])) if parent else []
            sug = difflib.get_close_matches(str(loc[-1]), valid, n=1)
            m = f"{where}: khóa lạ {loc[-1]!r} không được phép" + (f" (có phải '{sug[0]}'?)" if sug else "") + (f"; khóa hợp lệ: {', '.join(valid)}" if valid else "")
        elif t == "missing":
            m = f"{where}: thiếu trường bắt buộc"
        elif t == "literal_error":
            m = f"{where}: giá trị {inp!r} không hợp lệ; chỉ chấp nhận {ctx.get('expected')}"
        elif t == "union_tag_invalid":
            m = f"{where}: loại {ctx.get('tag')!r} không được hỗ trợ; chỉ chấp nhận {ctx.get('expected_tags')}"
        elif t == "union_tag_not_found":
            m = f"{where}: thiếu khóa phân loại {ctx.get('discriminator')}"
        elif t == "string_pattern_mismatch":
            m = f"{where}: {inp!r} sai định dạng" + (f" ({ID_HINT})" if ctx.get("pattern") == ID_PATTERN else f" (cần khớp {ctx.get('pattern')})")
        elif t in ("string_too_long", "too_long"):
            m = f"{where}: quá dài (tối đa {ctx.get('max_length')})"
        elif t in ("string_too_short", "too_short"):
            m = f"{where}: quá ngắn (tối thiểu {ctx.get('min_length')})"
        elif t in ("greater_than_equal", "less_than_equal", "greater_than", "less_than"):
            m = f"{where}: giá trị {inp!r} ngoài giới hạn {ctx}"
        elif t in ("string_type", "int_type", "int_parsing", "bool_type", "dict_type", "list_type", "model_type", "model_attributes_type", "int_from_float", "bool_parsing"):
            m = f"{where}: sai kiểu dữ liệu ({t}), nhận được {type(inp).__name__}"
        elif t in ("value_error", "assertion_error"):
            m = f"{where}: {str(ctx.get('error', err['msg'])).removeprefix('Value error, ')}"
        else:
            m = f"{where}: {err['msg']} ({t})"
        if m not in seen:
            seen.add(m)
            msgs.append(m)
    # union str|Assignee sinh 2 lỗi cho cùng vị trí; giữ lỗi có ý nghĩa
    return msgs


def parse_spec(data) -> ProcessSpec:
    if not isinstance(data, dict):
        raise SpecError(["đặc tả phải là object JSON"])
    try:
        return ProcessSpec.model_validate(data)
    except ValidationError as e:
        raise SpecError(vi_errors(e)) from None


Assignee.model_rebuild()


def json_schema() -> dict:
    s = ProcessSpec.model_json_schema()
    s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    s["$id"] = "https://xt.local/schema/process-spec-v2.json"
    return s


if __name__ == "__main__":   # python -m xtwf.spec > schema/process-spec-v2.schema.json
    import json as _json
    print(_json.dumps(json_schema(), ensure_ascii=False, indent=2))
