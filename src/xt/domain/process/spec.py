# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""ProcessSpec v2: đặc tả quy trình mà agent/gói nộp (JSON), pydantic `extra=forbid`.

- Loại bước: `approve`, `task`, `service`, `parallel` (fork/join, mode all; G-P1).
- `trigger`: `manual` hoặc `timer` (rrule RFC 5545 rút gọn + tz IANA; G-P2).
- `outcomes` tường minh, gateway không có nhánh mặc định (G-P3).
- `assignee` động theo ngữ pháp cố định (không eval), có `fallback` (G-P4).
- `sod` theo bước: `not_requester`, `distinct_from` (G-I1 tầng workflow).
- Biến trong biểu thức phải có sẵn TẠI vị trí dùng (G-P8): lỗi lúc compile, không phải lúc chạy.
Lỗi trả bằng tiếng Việt kèm gợi ý (`vi_errors`); `json_schema()` xuất JSON Schema 2020-12 cho agent.
"""

from __future__ import annotations

import difflib
import re
import types
import typing
import unicodedata
from collections.abc import Iterator
from typing import Annotated, Any, Literal, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from xt.domain.process.errors import SpecError, UnsafeExpression
from xt.domain.process.expr import check_expr, expr_names

ID_PATTERN = r"^[a-z][a-z0-9_]{0,40}$"
DEPT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DUR_RE = re.compile(r"^P(?:(\d{1,3})D)?(?:T(?:(\d{1,4})H)?(?:(\d{1,5})M)?(?:(\d{1,6})S)?)?$")
MAX_DURATION_S = 366 * 86400
RESERVED_VARS = frozenset({"requester", "true", "false", "none"})
RESERVED_PREFIX = ("xt_",)
RESERVED_SUFFIX = ("_decision", "_result", "_comment", "_actor", "_revise", "_info")
TIMER_CTX_VARS = frozenset(
    {"fire_at", "fire_date", "period_start", "period_start_date", "period_end_date"}
)
SCHEMA_ID = "https://xt.local/schema/process-spec-v2.json"

Id = Annotated[
    str,
    Field(
        pattern=ID_PATTERN,
        description="Định danh: a-z, 0-9, '_', bắt đầu bằng chữ, tối đa 41 ký tự",
    ),
]
DeptKey = Annotated[str, Field(pattern=DEPT_PATTERN, description="Mã phòng ban, ví dụ KD")]
Text = Annotated[
    str, Field(min_length=1, max_length=200, description="Tên hiển thị (tiếng Việt), ≤200 ký tự")
]
Expr = Annotated[
    str,
    Field(
        min_length=1,
        max_length=500,
        description=(
            "Biểu thức an toàn: so sánh, and/or/not, in, chỉ mục, + - /; "
            "không gọi hàm, không thuộc tính"
        ),
    ),
]


def _noncharacter(c: str) -> bool:
    cp = ord(c)
    return 0xFDD0 <= cp <= 0xFDEF or (cp & 0xFFFE) == 0xFFFE


def clean_text(v: str) -> str:
    """Cấm ký tự điều khiển/ẩn (Cc, Cf, Cs, Co), trừ tab, và noncharacter (U+FFFE, U+FFFF...)
    vốn không hợp lệ trong XML 1.0 (AUD-7)."""
    if any(
        (unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co") and c != "\t") or _noncharacter(c)
        for c in v
    ):
        raise ValueError("không được chứa ký tự điều khiển/ẩn hoặc ký tự không hợp lệ trong XML")
    return v


def safe_expr(v: str, key: str = "") -> str:
    try:
        check_expr(v)
    except UnsafeExpression as e:
        raise ValueError(f"biểu thức{' ' + key if key else ''} không an toàn: {e}") from None
    return v


def duration_seconds(v: str) -> int:
    """ISO 8601 thời lượng giới hạn (P[n]DT[n]H[n]M[n]S), > 0 và ≤ 366 ngày."""
    m = DUR_RE.match(v)
    if not m or v in ("P", "PT") or v.endswith("T"):
        raise ValueError(f"thời lượng {v!r} không đúng ISO 8601 (ví dụ PT4H, P2D, P1DT12H)")
    d, h, mi, s = (int(x or 0) for x in m.groups())
    total = d * 86400 + h * 3600 + mi * 60 + s
    if total <= 0 or total > MAX_DURATION_S:
        raise ValueError(f"thời lượng {v!r} phải lớn hơn 0 và không quá 366 ngày")
    return total


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, validate_default=True)


# ------------------------------------------------------------------ assignee (ngữ pháp cố định)
_ID = r"[a-z][a-z0-9_]{0,40}"
_ARG = (
    r"(?:'(?P<dlit>[A-Za-z0-9][A-Za-z0-9_-]{0,40})'"
    rf"|department\((?P<ddof>{_ID})\)|(?P<dvar>{_ID}))"
)
SHORTHAND: list[tuple[str, re.Pattern[str]]] = [
    ("role_in", re.compile(rf"^role\((?P<role>{_ID})\)\s+in\s+{_ARG}$")),
    ("role", re.compile(rf"^role\((?P<role>{_ID})\)$")),
    ("manager_dot", re.compile(rf"^(?P<var>{_ID})\.manager$")),
    ("manager_fn", re.compile(rf"^manager\((?P<var>{_ID})(?:\s*,\s*(?P<levels>[1-5]))?\)$")),
    ("head", re.compile(rf"^department_head\({_ARG}\)$")),
    ("user", re.compile(rf"^user\((?P<var>{_ID})\)$")),
]
ASSIGNEE_HELP = (
    "cú pháp người nhận hợp lệ: role(x) | role(x) in department(requester) | role(x) in 'KD' | "
    "requester.manager | manager(requester, 2) | department_head(department(requester)) | "
    "department_head('KD') | department_head(bien_phong_ban) | user(bien_nguoi_dung)"
)


def parse_assignee(s: str) -> dict[str, Any]:
    """Chuỗi rút gọn -> dict Assignee. Chỉ regex, không eval."""
    s = s.strip()
    for kind, rx in SHORTHAND:
        m = rx.match(s)
        if not m:
            continue
        g = m.groupdict()
        if kind in ("role", "role_in", "head"):
            out: dict[str, Any] = {"kind": "department_head" if kind == "head" else "role"}
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
        return {"kind": "user", "user_var": g["var"]}
    raise ValueError(f"người nhận {s!r} không hợp lệ; {ASSIGNEE_HELP}")


class Assignee(Strict):
    """Người nhận bước, giải lúc chạy từ dữ liệu tổ chức qua resolver đã đăng ký."""

    kind: Literal["role", "manager", "department_head", "user"] = Field(
        description=(
            "role: theo vai trò; manager: quản lý của người trong biến; "
            "department_head: trưởng phòng; user: người trong biến"
        )
    )
    role: Id | None = Field(None, description="Vai trò (kind=role), phải có trong roles")
    department: DeptKey | None = Field(None, description="Mã phòng cố định")
    department_of: Id | None = Field(
        None, description="Biến người dùng; lấy phòng của người đó (ví dụ requester)"
    )
    department_var: Id | None = Field(
        None, description="Biến chứa mã phòng (input kiểu department)"
    )
    user_var: Id | None = Field(None, description="Biến chứa người dùng (kind=user/manager)")
    levels: int = Field(1, ge=1, le=5, description="Số cấp quản lý (kind=manager)")
    fallback: list[Annotated[str, Field(max_length=120)] | Assignee] = Field(
        default_factory=list,
        max_length=5,
        description="Chuỗi thay thế khi không giải được (leo thang, không bao giờ bỏ qua bước)",
    )

    @field_validator("fallback", mode="after")
    @classmethod
    def _fallback(cls, v: list[str | Assignee]) -> list[str | Assignee]:
        return [to_assignee(x) for x in v]

    @model_validator(mode="after")
    def _shape(self) -> Assignee:
        dept_fields = [
            f for f in ("department", "department_of", "department_var") if getattr(self, f)
        ]
        if len(dept_fields) > 1:
            raise ValueError(
                "chỉ được chọn một trong department/department_of/department_var "
                f"(đang có {dept_fields})"
            )
        if self.kind == "role":
            if not self.role:
                raise ValueError("kind=role cần khóa 'role'")
            if self.user_var:
                raise ValueError("kind=role không dùng user_var")
        elif self.kind == "department_head":
            if self.role or self.user_var:
                raise ValueError("kind=department_head không dùng role/user_var")
            if not dept_fields:
                raise ValueError(
                    "kind=department_head cần department, department_of hoặc department_var"
                )
        else:
            if not self.user_var:
                raise ValueError(f"kind={self.kind} cần khóa 'user_var'")
            if self.role or dept_fields:
                raise ValueError(f"kind={self.kind} không dùng role/department")
        if self.kind != "manager" and self.levels != 1:
            raise ValueError("levels chỉ dùng cho kind=manager")
        return self

    def is_static(self) -> bool:
        """Vai trò toàn công ty (không phụ thuộc dữ liệu). `role(x) in 'KT'` KHÔNG tĩnh (H1)."""
        return self.kind == "role" and not (
            self.department or self.department_of or self.department_var
        )

    def describe(self) -> str:
        if self.kind == "role":
            if self.department:
                where = f" thuộc phòng {self.department}"
            elif self.department_of:
                where = f" cùng phòng với {self.department_of}"
            elif self.department_var:
                where = f" thuộc phòng trong biến {self.department_var}"
            else:
                where = ""
            return f"Vai trò {self.role}{where}"
        if self.kind == "manager":
            lvl = "trực tiếp" if self.levels == 1 else f"cấp {self.levels}"
            return f"Quản lý {lvl} của {self.user_var}"
        if self.kind == "department_head":
            if self.department:
                return f"Trưởng phòng {self.department}"
            if self.department_of:
                return f"Trưởng phòng của {self.department_of}"
            return f"Trưởng phòng trong biến {self.department_var}"
        return f"Người trong biến {self.user_var}"

    def key(self) -> str:
        return "|".join(
            f"{k}={v}"
            for k, v in self.model_dump(exclude={"fallback"}).items()
            if v not in (None, 1) or k == "kind"
        )

    def variables(self) -> set[str]:
        return {v for v in (self.department_of, self.department_var, self.user_var) if v}

    def chain(self) -> Iterator[Assignee]:
        """Chính nó rồi tới chuỗi fallback (theo chiều sâu)."""
        yield self
        for f in self.fallback:
            if isinstance(f, Assignee):
                yield from f.chain()


def to_assignee(v: str | Assignee) -> Assignee:
    return Assignee.model_validate(parse_assignee(v)) if isinstance(v, str) else v


AssigneeField = Annotated[
    Annotated[str, Field(max_length=120, description=ASSIGNEE_HELP)] | Assignee,
    Field(description="Người nhận: chuỗi rút gọn hoặc object Assignee"),
]

# ------------------------------------------------------------------ steps
DecisionOutcome = Literal["approve", "reject", "return_for_revision", "delegate", "request_info"]
TaskOutcome = Literal["done", "delegate", "request_info"]


def _default_decision_outcomes() -> list[DecisionOutcome]:
    return ["approve", "reject"]


def _default_task_outcomes() -> list[TaskOutcome]:
    return ["done"]


def _default_sod() -> SoD:
    return SoD.model_validate({})


def _task_sod() -> SoD:
    return SoD.model_validate({"not_requester": False})


class Sla(Strict):
    after: Annotated[str, Field(max_length=24, description="ISO 8601, ví dụ PT24H, P2D")]
    escalate_to: AssigneeField

    @field_validator("after")
    @classmethod
    def _after(cls, v: str) -> str:
        duration_seconds(v)
        return v

    @field_validator("escalate_to", mode="after")
    @classmethod
    def _esc(cls, v: str | Assignee) -> Assignee:
        return to_assignee(v)

    def target(self) -> Assignee:
        return to_assignee(self.escalate_to)


class OnReturn(Strict):
    resume: Literal["restart", "same_step"] = Field(
        "restart",
        description=(
            "restart: chạy lại từ bước đầu (định tuyến lại theo dữ liệu đã sửa); "
            "same_step: quay lại đúng bước đã trả"
        ),
    )
    max_rounds: int = Field(
        3, ge=1, le=10, description="Số lần trả lại tối đa; quá số này runtime từ chối"
    )
    to_step: Id | None = Field(
        None,
        description=(
            "Trả về một bước người ở trước (cùng cấp, ví dụ nghiệm thu không đạt -> làm lại), "
            "thay vì về người đề nghị"
        ),
    )

    @model_validator(mode="after")
    def _to_step(self) -> OnReturn:
        if self.to_step and self.resume != "restart":
            raise ValueError("to_step không dùng cùng resume")
        return self


class SoD(Strict):
    not_requester: bool = Field(True, description="Người đề nghị không được tự làm bước này")
    distinct_from: list[Id] = Field(
        default_factory=list,
        max_length=10,
        description="Người làm bước này phải khác người đã làm các bước được liệt kê",
    )


class _Human(Strict):
    id: Id
    name: Text
    assignee: AssigneeField
    when: Expr | None = None
    sla: Sla | None = None

    @field_validator("assignee", mode="after")
    @classmethod
    def _assignee(cls, v: str | Assignee) -> Assignee:
        return to_assignee(v)

    @field_validator("when")
    @classmethod
    def _when(cls, v: str | None) -> str | None:
        return v if v is None else safe_expr(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return clean_text(v)

    def target(self) -> Assignee:
        return to_assignee(self.assignee)


class ApproveStep(_Human):
    """Bước quyết định. Chỉ outcome khai ở đây mới có nhánh; giá trị khác bị từ chối."""

    type: Literal["approve"]
    outcomes: list[DecisionOutcome] = Field(
        default_factory=_default_decision_outcomes, min_length=1, max_length=5
    )
    on_return: OnReturn | None = None
    sod: SoD = Field(default_factory=_default_sod)

    @model_validator(mode="after")
    def _outcomes(self) -> ApproveStep:
        if len(set(self.outcomes)) != len(self.outcomes):
            raise ValueError("outcomes bị trùng")
        if "approve" not in self.outcomes:
            raise ValueError("bước approve phải có outcome 'approve'")
        if self.on_return is not None and "return_for_revision" not in self.outcomes:
            raise ValueError("on_return chỉ dùng khi outcomes có 'return_for_revision'")
        if "return_for_revision" in self.outcomes and self.on_return is None:
            self.on_return = OnReturn.model_validate({})
        return self


class TaskStep(_Human):
    """Bước công việc (không phải duyệt): nhận việc, làm, bàn giao."""

    type: Literal["task"]
    outcomes: list[TaskOutcome] = Field(
        default_factory=_default_task_outcomes, min_length=1, max_length=3
    )
    sod: SoD = Field(default_factory=_task_sod)

    @model_validator(mode="after")
    def _outcomes(self) -> TaskStep:
        if len(set(self.outcomes)) != len(self.outcomes) or "done" not in self.outcomes:
            raise ValueError("outcomes của task phải có 'done' và không trùng")
        return self


class ServiceStep(Strict):
    type: Literal["service"]
    id: Id
    name: Text
    op: Id = Field(description="Tên op trong allowlist của deployment")
    params: dict[Id, Expr] = Field(default_factory=dict, max_length=20)
    when: Expr | None = None

    @field_validator("when")
    @classmethod
    def _when(cls, v: str | None) -> str | None:
        return v if v is None else safe_expr(v)

    @field_validator("params")
    @classmethod
    def _params(cls, v: dict[str, str]) -> dict[str, str]:
        return {k: safe_expr(x, k) for k, x in v.items()}

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return clean_text(v)


BranchStep = Annotated[ApproveStep | TaskStep | ServiceStep, Field(discriminator="type")]


class ParallelStep(Strict):
    """Các nhánh chạy song song, gộp khi tất cả xong (mode all). Từ chối ở một nhánh kết thúc
    cả quy trình (terminate end)."""

    type: Literal["parallel"]
    id: Id
    name: Text
    when: Expr | None = None
    mode: Literal["all"] = Field("all", description="Chỉ hỗ trợ 'all' (join đủ mọi nhánh)")
    branches: list[Annotated[list[BranchStep], Field(min_length=1, max_length=20)]] = Field(
        min_length=2, max_length=8
    )

    @field_validator("when")
    @classmethod
    def _when(cls, v: str | None) -> str | None:
        return v if v is None else safe_expr(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return clean_text(v)


HumanStep = ApproveStep | TaskStep
AnyStep = ApproveStep | TaskStep | ServiceStep | ParallelStep
Step = Annotated[AnyStep, Field(discriminator="type")]


# ------------------------------------------------------------------ inputs / trigger
InputType = Literal["str", "int", "number", "bool", "user", "department", "date", "object"]


class InputSpec(Strict):
    type: InputType
    required: bool = True
    editable_on_revision: bool = Field(
        False, description="Người đề nghị được sửa giá trị này khi bị trả lại bổ sung"
    )
    description: Text | None = None
    default: StrictStr | StrictBool | StrictInt | StrictFloat | None = Field(
        None,
        description=(
            "Giá trị khi input tùy chọn bị bỏ trống. Bắt buộc nếu input tùy chọn được dùng trong "
            "biểu thức (when/params); chỉ cho kiểu str/int/number/bool/date"
        ),
    )

    @model_validator(mode="after")
    def _default(self) -> InputSpec:
        d = self.default
        if d is None:
            return self
        if self.required:
            raise ValueError("default chỉ dùng cho input tùy chọn (required: false)")
        ok = {
            "str": isinstance(d, str) and len(d) <= 2000,
            "int": isinstance(d, int) and not isinstance(d, bool),
            "number": isinstance(d, int | float) and not isinstance(d, bool),
            "bool": isinstance(d, bool),
            "date": isinstance(d, str) and bool(_DATE_RE.match(d)),
        }.get(self.type, False)
        if not ok:
            raise ValueError(f"default {d!r} không hợp lệ cho kiểu {self.type}")
        return self


class ManualTrigger(Strict):
    kind: Literal["manual"]


RRULE_KEYS = frozenset({"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "BYMONTH", "BYHOUR", "BYMINUTE"})
_DAY = r"(?:[+-]?[1-5])?(?:MO|TU|WE|TH|FR|SA|SU)"
_MDAY = r"-?(?:[1-9]|[12]\d|3[01])"
_HOUR = r"(?:1?\d|2[0-3])"
RRULE_VALUE: dict[str, re.Pattern[str]] = {
    "FREQ": re.compile(r"^(HOURLY|DAILY|WEEKLY|MONTHLY|YEARLY)$"),
    "INTERVAL": re.compile(r"^([1-9]\d{0,2})$"),
    "BYDAY": re.compile(rf"^{_DAY}(?:,{_DAY}){{0,6}}$"),
    "BYMONTHDAY": re.compile(rf"^{_MDAY}(?:,{_MDAY}){{0,30}}$"),
    "BYMONTH": re.compile(r"^(?:[1-9]|1[0-2])(?:,(?:[1-9]|1[0-2])){0,11}$"),
    "BYHOUR": re.compile(rf"^{_HOUR}(?:,{_HOUR}){{0,23}}$"),
    "BYMINUTE": re.compile(r"^(?:[1-5]?\d)$"),
}


def validate_rrule(v: str) -> dict[str, str]:
    """RFC 5545 rút gọn: chỉ các khóa trong RRULE_KEYS; cấm COUNT/UNTIL/DTSTART/BYSECOND."""
    if len(v) > 200:
        raise ValueError("rrule quá dài")
    parts: dict[str, str] = {}
    for item in v.split(";"):
        if "=" not in item:
            raise ValueError(f"rrule sai cú pháp ở {item!r} (dạng KEY=VALUE;...)")
        k, val = item.split("=", 1)
        if k not in RRULE_KEYS:
            raise ValueError(
                f"khóa rrule {k!r} không được phép; chỉ chấp nhận {sorted(RRULE_KEYS)} "
                "(COUNT/UNTIL/BYSECOND/DTSTART bị cấm)"
            )
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
    """Khởi tạo định kỳ. Poller tạo đúng 1 instance cho mỗi kỳ (khóa duy nhất process + kỳ)."""

    kind: Literal["timer"]
    rrule: Annotated[
        str,
        Field(
            max_length=200,
            description="RFC 5545 rút gọn, ví dụ FREQ=WEEKLY;BYDAY=MO;BYHOUR=8;BYMINUTE=0",
        ),
    ]
    tz: Annotated[str, Field(max_length=64, description="Múi giờ IANA, ví dụ Asia/Ho_Chi_Minh")]
    input: dict[Id, Expr] = Field(
        default_factory=dict,
        max_length=20,
        description=f"Ánh xạ input <- biểu thức trên biến kỳ {sorted(TIMER_CTX_VARS)}",
    )
    catchup: Literal["latest", "none"] = Field(
        "latest",
        description="latest: bỏ lỡ nhiều kỳ chỉ chạy kỳ gần nhất; none: bỏ kỳ đã trễ quá grace",
    )
    grace: Annotated[str, Field(max_length=24)] = "PT1H"

    @field_validator("rrule")
    @classmethod
    def _rrule(cls, v: str) -> str:
        validate_rrule(v)
        return v

    @field_validator("grace")
    @classmethod
    def _grace(cls, v: str) -> str:
        duration_seconds(v)
        return v

    @field_validator("input")
    @classmethod
    def _input(cls, v: dict[str, str]) -> dict[str, str]:
        return {k: safe_expr(x, k) for k, x in v.items()}

    @field_validator("tz")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"múi giờ {v!r} không phải tên IANA hợp lệ") from None
        return v


Trigger = Annotated[ManualTrigger | TimerTrigger, Field(discriminator="kind")]


# ------------------------------------------------------------------ process
class ProcessSpec(Strict):
    """Đặc tả quy trình xT v2. Agent chỉ nộp JSON theo schema này, không bao giờ nộp XML."""

    id: Id
    name: Text
    version: int = Field(ge=1, le=100000)
    description: Annotated[str, Field(max_length=2000)] | None = None
    roles: list[Id] = Field(default_factory=list, max_length=50)
    inputs: dict[Id, InputSpec] = Field(default_factory=dict, max_length=50)
    trigger: Trigger = Field(default_factory=lambda: ManualTrigger(kind="manual"))
    fallback_assignee: AssigneeField | None = Field(
        None,
        description="Bắt buộc khi có người nhận động: nơi leo thang khi không giải được người nhận",
    )
    steps: list[Step] = Field(min_length=1, max_length=60)

    @field_validator("fallback_assignee", mode="after")
    @classmethod
    def _fallback(cls, v: str | Assignee | None) -> Assignee | None:
        return v if v is None else to_assignee(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return clean_text(v)

    def fallback(self) -> Assignee | None:
        return None if self.fallback_assignee is None else to_assignee(self.fallback_assignee)

    def is_timer(self) -> bool:
        return isinstance(self.trigger, TimerTrigger)

    def iter_steps(self) -> Iterator[tuple[AnyStep, tuple[str, int] | None]]:
        """Mọi bước theo thứ tự khai báo; bước trong nhánh kèm (id parallel, chỉ số nhánh)."""
        for st in self.steps:
            yield st, None
            if isinstance(st, ParallelStep):
                for bi, br in enumerate(st.branches):
                    for s in br:
                        yield s, (st.id, bi)

    @model_validator(mode="after")
    def _cross(self) -> ProcessSpec:
        errs = _CrossCheck(self).run()
        if errs:
            raise ValueError("; ".join(errs))
        return self


class _CrossCheck:
    """Kiểm chéo cả spec: tên dành riêng, id trùng, biến theo thứ tự bước, assignee, SoD, timer."""

    def __init__(self, spec: ProcessSpec):
        self.s = spec
        self.errs: list[str] = []
        self.timer = spec.is_timer()
        self.user_vars = {k for k, v in spec.inputs.items() if v.type == "user"} | (
            set() if self.timer else {"requester"}
        )
        self.dept_vars = {k for k, v in spec.inputs.items() if v.type == "department"}
        self.dynamic = False
        self.human_ids = {
            st.id for st, _ in spec.iter_steps() if isinstance(st, ApproveStep | TaskStep)
        }

    def run(self) -> list[str]:
        s = self.s
        if len(set(s.roles)) != len(s.roles):
            self.errs.append("roles bị trùng")
        for name in s.inputs:
            if (
                name in RESERVED_VARS
                or name.startswith(RESERVED_PREFIX)
                or name.endswith(RESERVED_SUFFIX)
                or name in TIMER_CTX_VARS
            ):
                self.errs.append(f"inputs.{name}: tên biến dành riêng cho runtime")
        ids = [st.id for st, _ in s.iter_steps()]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            self.errs.append(f"id bước bị trùng: {dup}")
        base = set(s.inputs) | ({"xt_fire_at", "xt_period_key"} if self.timer else {"requester"})
        self._walk(list(s.steps), base, "steps", in_parallel=False)
        editable = [k for k, v in s.inputs.items() if v.editable_on_revision]
        for st, _ in s.iter_steps():
            if (
                isinstance(st, ApproveStep)
                and st.on_return
                and st.on_return.resume == "same_step"
                and editable
            ):
                self.errs.append(
                    f"bước {st.id}: on_return.resume=same_step không dùng được khi có input sửa "
                    f"được {editable} (sửa dữ liệu phải định tuyến lại từ đầu, nếu không các bước "
                    "'when' phía trước bị bỏ qua)"
                )
        if isinstance(s.trigger, TimerTrigger):
            for k in s.trigger.input:
                if k not in s.inputs:
                    self.errs.append(f"trigger.input.{k}: không phải input đã khai báo")
            for k, e in s.trigger.input.items():
                self._names(e, set(TIMER_CTX_VARS), f"trigger.input.{k}")
            missing = [k for k, v in s.inputs.items() if v.required and k not in s.trigger.input]
            if missing:
                self.errs.append(f"trigger.input thiếu input bắt buộc {missing}")
        fb = s.fallback()
        if fb is not None:
            self._assignee(fb, "fallback_assignee")
            if not list(fb.chain())[-1].is_static():
                self.errs.append(
                    "fallback_assignee phải kết thúc bằng vai trò tĩnh role(x) (phần tử cuối của "
                    "chuỗi fallback) để luôn có nơi leo thang"
                )
        if self.dynamic and fb is None:
            self.errs.append(
                "có người nhận động nên phải khai fallback_assignee "
                "(nơi leo thang khi không giải được người nhận)"
            )
        return self.errs

    def _assignee(self, a: Assignee, where: str) -> None:
        for x in a.chain():
            if not x.is_static():
                self.dynamic = True
            if x.role and x.role not in self.s.roles:
                self.errs.append(f"{where}: vai trò {x.role!r} không có trong roles {self.s.roles}")
            for v in (x.department_of, x.user_var):
                if v and v not in self.user_vars:
                    alt = "" if self.timer else " hoặc requester"
                    self.errs.append(
                        f"{where}: biến {v!r} phải là input kiểu user{alt} "
                        f"(đang có {sorted(self.user_vars)})"
                    )
            if x.department_var and x.department_var not in self.dept_vars:
                self.errs.append(
                    f"{where}: biến {x.department_var!r} phải là input kiểu department"
                )

    def _names(self, expr: str, avail: set[str], where: str) -> None:
        names = expr_names(expr)
        loose = sorted(
            n for n in names
            if n in self.s.inputs and not self.s.inputs[n].required
            and self.s.inputs[n].default is None
        )  # fmt: skip
        if loose:
            self.errs.append(
                f"{where}: biểu thức {expr!r} dùng input tùy chọn {loose} chưa có default "
                "(khai 'default' để biểu thức luôn đánh giá được)"
            )
        unknown = names - avail
        if unknown:
            self.errs.append(
                f"{where}: biểu thức {expr!r} dùng biến chưa khai báo {sorted(unknown)} "
                f"(biến có sẵn: {sorted(avail)})"
            )

    def _walk(
        self, steps: list[Any], avail: set[str], where: str, *, in_parallel: bool
    ) -> set[str]:
        for i, st in enumerate(steps):
            w = f"{where}[{i}]({st.id})"
            if st.when:
                self._names(st.when, avail, f"{w}.when")
            if isinstance(st, ParallelStep):
                produced: set[str] = set()
                for bi, br in enumerate(st.branches):
                    produced |= self._walk(
                        list(br), set(avail), f"{w}.branches[{bi}]", in_parallel=True
                    )
                avail |= produced
                continue
            if isinstance(st, ServiceStep):
                for k, e in st.params.items():
                    self._names(e, avail, f"{w}.params.{k}")
                avail.add(f"{st.id}_result")
                continue
            self._human(st, steps[:i], w, in_parallel=in_parallel)
            avail |= {f"{st.id}_decision", f"{st.id}_comment"}
        return avail

    def _human(self, st: HumanStep, before: list[Any], w: str, *, in_parallel: bool) -> None:
        self._assignee(st.target(), f"{w}.assignee")
        if st.sla:
            self._assignee(st.sla.target(), f"{w}.sla.escalate_to")
        rfr = "return_for_revision" in st.outcomes
        if in_parallel and rfr:
            self.errs.append(
                f"{w}: return_for_revision không dùng được trong nhánh song song "
                "(sẽ làm hỏng join); dùng request_info"
            )
        ts = st.on_return.to_step if isinstance(st, ApproveStep) and st.on_return else None
        if ts and ts not in [p.id for p in before if isinstance(p, ApproveStep | TaskStep)]:
            self.errs.append(
                f"{w}.on_return.to_step: {ts!r} phải là bước người đứng trước, cùng cấp"
            )
        if self.timer and rfr and not ts:
            self.errs.append(
                f"{w}: quy trình khởi tạo bằng timer không có người đề nghị để trả lại"
            )
        for d in st.sod.distinct_from:
            if d not in self.human_ids:
                self.errs.append(f"{w}.sod.distinct_from: {d!r} không phải bước người")
            if d == st.id:
                self.errs.append(f"{w}.sod.distinct_from: không được tham chiếu chính nó")


Assignee.model_rebuild()


# ------------------------------------------------------------------ lỗi tiếng Việt
def _unwrap(tp: Any) -> list[Any]:
    while True:
        origin = typing.get_origin(tp)
        if origin is Annotated:
            tp = typing.get_args(tp)[0]
        elif origin is Union or origin is types.UnionType:
            return [a for a in typing.get_args(tp) if a is not type(None)]
        else:
            return [tp]


def _is_model(t: Any) -> bool:
    return isinstance(t, type) and issubclass(t, BaseModel)


def _tag_values(model: type[BaseModel], field: str) -> tuple[Any, ...]:
    f = model.model_fields.get(field)
    return typing.get_args(f.annotation) if f is not None else ()


def _model_at(loc: tuple[int | str, ...]) -> list[type[BaseModel]]:
    """Model pydantic ở vị trí `loc` (để gợi ý khóa hợp lệ)."""
    cands: list[Any] = [ProcessSpec]
    for seg in loc:
        if isinstance(seg, str) and seg.startswith(("function-", "constrained-")):
            continue  # nhãn nội bộ của pydantic cho validator/union, không phải khóa
        nxt: list[Any] = []
        for c in cands:
            for t in [c] if _is_model(c) else _unwrap(c):
                if _is_model(t):
                    if seg == t.__name__:
                        nxt.append(t)
                    elif isinstance(seg, str) and seg in t.model_fields:
                        nxt.append(t.model_fields[seg].annotation)
                    elif isinstance(seg, str) and (
                        seg in _tag_values(t, "type") or seg in _tag_values(t, "kind")
                    ):
                        nxt.append(t)
                else:
                    o = typing.get_origin(t)
                    if o is list and isinstance(seg, int):
                        nxt.append(typing.get_args(t)[0])
                    elif o is dict and isinstance(seg, str):
                        nxt.append(typing.get_args(t)[1])
        cands = nxt
    return [t for c in cands for t in _unwrap(c) if _is_model(t)]


_TAGS = frozenset({"approve", "task", "service", "parallel", "manual", "timer", "str", "Assignee"})
ID_HINT = "chỉ gồm a-z, 0-9, '_', bắt đầu bằng chữ thường, tối đa 41 ký tự"
_TYPE_ERRORS = frozenset(
    {
        "string_type", "int_type", "int_parsing", "bool_type", "dict_type", "list_type",
        "model_type", "model_attributes_type", "int_from_float", "bool_parsing",
    }
)  # fmt: skip


def _fmt_loc(loc: tuple[int | str, ...]) -> str:
    out = ""
    for seg in loc:
        if isinstance(seg, int):
            out += f"[{seg}]"
        elif seg in _TAGS or seg.startswith(("function-", "list[", "dict[", "constrained-")):
            continue
        else:
            out += ("." if out else "") + seg
    return out or "(gốc)"


def _vi_message(err: Any) -> str | None:
    loc, t, inp = err["loc"], err["type"], err.get("input")
    ctx = err.get("ctx") or {}
    if (
        t == "string_type"
        and loc
        and loc[-1] in ("str", "constrained-str")
        and isinstance(inp, dict)
    ):
        return None  # nhánh str của union str|Assignee: lỗi thật nằm ở nhánh Assignee
    where = _fmt_loc(loc)
    if t == "extra_forbidden":
        parent = _model_at(loc[:-1])
        valid = sorted(set().union(*[set(m.model_fields) for m in parent])) if parent else []
        sug = difflib.get_close_matches(str(loc[-1]), valid, n=1)
        return (
            f"{where}: khóa lạ {loc[-1]!r} không được phép"
            + (f" (có phải '{sug[0]}'?)" if sug else "")
            + (f"; khóa hợp lệ: {', '.join(valid)}" if valid else "")
        )
    if t == "missing":
        return f"{where}: thiếu trường bắt buộc"
    if t == "literal_error":
        return f"{where}: giá trị {inp!r} không hợp lệ; chỉ chấp nhận {ctx.get('expected')}"
    if t == "union_tag_invalid":
        return (
            f"{where}: loại {ctx.get('tag')!r} không được hỗ trợ; "
            f"chỉ chấp nhận {ctx.get('expected_tags')}"
        )
    if t == "union_tag_not_found":
        return f"{where}: thiếu khóa phân loại {ctx.get('discriminator')}"
    if t == "string_pattern_mismatch":
        hint = (
            f" ({ID_HINT})"
            if ctx.get("pattern") == ID_PATTERN
            else f" (cần khớp {ctx.get('pattern')})"
        )
        return f"{where}: {inp!r} sai định dạng{hint}"
    if t in ("string_too_long", "too_long"):
        return f"{where}: quá dài (tối đa {ctx.get('max_length')})"
    if t in ("string_too_short", "too_short"):
        return f"{where}: quá ngắn (tối thiểu {ctx.get('min_length')})"
    if t in ("greater_than_equal", "less_than_equal", "greater_than", "less_than"):
        return f"{where}: giá trị {inp!r} ngoài giới hạn {ctx}"
    if t in _TYPE_ERRORS:
        return f"{where}: sai kiểu dữ liệu ({t}), nhận được {type(inp).__name__}"
    if t in ("value_error", "assertion_error"):
        return f"{where}: {str(ctx.get('error', err['msg'])).removeprefix('Value error, ')}"
    return f"{where}: {err['msg']} ({t})"


def vi_errors(e: ValidationError) -> list[str]:
    msgs: list[str] = []
    for err in e.errors(include_url=False):
        m = _vi_message(err)
        if m is not None and m not in msgs:
            msgs.append(m)
    return msgs


def parse_spec(data: object) -> ProcessSpec:
    """Dict JSON -> ProcessSpec; mọi lỗi -> `SpecError` (danh sách tiếng Việt)."""
    if isinstance(data, ProcessSpec):
        return data
    if not isinstance(data, dict):
        raise SpecError(["đặc tả phải là object JSON"])
    try:
        return ProcessSpec.model_validate(data)
    except ValidationError as e:
        raise SpecError(vi_errors(e)) from None


def json_schema() -> dict[str, Any]:
    """JSON Schema 2020-12 của ProcessSpec v2 (mọi object `additionalProperties: false`)."""
    s = ProcessSpec.model_json_schema()
    s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    s["$id"] = SCHEMA_ID
    return s
