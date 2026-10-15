# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""DSL biểu thức khai báo dùng chung cho rule chéo trường (→ CHECK) và bộ lọc tìm kiếm.

AST đóng `Cmp | And | Or | Not`, không bao giờ nhận SQL/Python tự do. Giới hạn: lồng ≤ 8 cấp,
`in` ≤ 100 phần tử, mỗi nhóm and/or ≤ 50 nhánh. App đánh giá rule bằng logic 3 trị giống SQL
(CHECK chỉ vi phạm khi kết quả FALSE) để báo lỗi sớm và khớp hệt DB.

`parse_rule_expr` đọc dạng chuỗi của tool-contracts-v1 (`rule.expr`, ví dụ
`trip_end >= trip_start`) bằng bộ tách token viết tay, không regex, không eval.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from xt.domain.model.ident import Ident

if TYPE_CHECKING:
    from xt.domain.model.spec import FieldSpec

MAX_DEPTH = 8
MAX_IN = 100
MAX_BRANCHES = 50
MAX_EXPR_TEXT = 500

FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]
Scalar = StrictBool | StrictInt | FiniteDecimal | StrictStr | None
Value = Scalar | list[Scalar]

CmpOp = Literal[
    "=", "!=", "<", "<=", ">", ">=", "is_null", "not_null", "in", "contains", "startswith"
]  # fmt: skip
ORDER_OPS = frozenset({"<", "<=", ">", ">="})
RULE_OPS = frozenset({"=", "!=", "<", "<=", ">", ">=", "is_null", "not_null", "in"})
FILTER_OPS = RULE_OPS | {"contains", "startswith"}
ORDERABLE = frozenset({"text", "number", "money", "date", "datetime", "enum"})
NUMERIC = frozenset({"number", "money"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Cmp(_Strict):
    """`field <op> value` | `field <op> other_field` | `field is_null`."""

    field: Ident
    op: CmpOp
    value: Value = None
    other_field: Ident | None = None

    @model_validator(mode="after")
    def _shape(self) -> Cmp:
        if self.op in ("is_null", "not_null"):
            if self.value is not None or self.other_field is not None:
                raise ValueError(f"toán tử {self.op} không nhận value/other_field")
        elif self.op == "in":
            if self.other_field is not None:
                raise ValueError("toán tử in không nhận other_field")
            if not isinstance(self.value, list) or not 1 <= len(self.value) <= MAX_IN:
                raise ValueError(f"toán tử in cần value là danh sách 1..{MAX_IN} phần tử")
        elif self.other_field is not None and self.value is not None:
            raise ValueError("chỉ được dùng value HOẶC other_field")
        elif self.other_field is None and (self.value is None or isinstance(self.value, list)):
            raise ValueError(f"toán tử {self.op} cần một value vô hướng hoặc other_field")
        return self


class And(_Strict):
    and_: list[Expr] = Field(alias="and", min_length=1, max_length=MAX_BRANCHES)


class Or(_Strict):
    or_: list[Expr] = Field(alias="or", min_length=1, max_length=MAX_BRANCHES)


class Not(_Strict):
    not_: Expr = Field(alias="not")


Expr = Union[Cmp, And, Or, Not]  # noqa: UP007 - cần Union cho forward reference đệ quy
for _m in (And, Or, Not):
    _m.model_rebuild()


def children(e: Expr) -> list[Expr]:
    if isinstance(e, And):
        return list(e.and_)
    if isinstance(e, Or):
        return list(e.or_)
    if isinstance(e, Not):
        return [e.not_]
    return []


def expr_fields(e: Expr, depth: int = 1) -> set[str]:
    """Tập trường được tham chiếu; ném ValueError nếu lồng quá MAX_DEPTH."""
    if depth > MAX_DEPTH:
        raise ValueError(f"biểu thức lồng quá {MAX_DEPTH} cấp")
    if isinstance(e, Cmp):
        return {e.field} | ({e.other_field} if e.other_field else set())
    out: set[str] = set()
    for k in children(e):
        out |= expr_fields(k, depth + 1)
    return out


def map_fields(e: Expr, fn: Mapping[str, str]) -> Expr:
    """Đổi tên trường trong biểu thức (dùng để so sánh rule theo id ổn định)."""
    if isinstance(e, Cmp):
        return Cmp(
            field=fn.get(e.field, e.field),
            op=e.op,
            value=e.value,
            other_field=fn.get(e.other_field, e.other_field) if e.other_field else None,
        )
    if isinstance(e, And):
        return And.model_validate({"and": [map_fields(k, fn) for k in e.and_]})
    if isinstance(e, Or):
        return Or.model_validate({"or": [map_fields(k, fn) for k in e.or_]})
    assert isinstance(e, Not)
    return Not.model_validate({"not": map_fields(e.not_, fn)})


# ------------------------------------------------------------------ ép kiểu literal ----
def _decimal(v: object, what: str) -> Decimal:
    if isinstance(v, bool) or not isinstance(v, int | Decimal | str):
        raise ValueError(f"{what}: cần số")
    try:
        d = Decimal(v) if not isinstance(v, Decimal) else v
    except InvalidOperation as e:
        raise ValueError(f"{what}: '{v}' không phải số") from e
    if not d.is_finite():
        raise ValueError(f"{what}: số phải hữu hạn")
    return d


def coerce_literal(f: FieldSpec, v: object) -> object:
    """Ép một literal trong spec/DSL về kiểu Python đúng với trường (ValueError nếu sai)."""
    t, name = f.type, f.name
    if v is None:
        return None
    if t == "text":
        if not isinstance(v, str):
            raise ValueError(f"{name}: cần chuỗi")
        return v
    if t == "enum":
        if v not in {o.value for o in f.options or []}:
            raise ValueError(f"{name}: '{v}' không thuộc danh sách giá trị của enum")
        return v
    if t == "number":
        return _decimal(v, name)
    if t == "money":
        d = _decimal(v, name)
        if d != d.to_integral_value():
            raise ValueError(f"{name}: tiền VND phải là số nguyên đồng")
        return int(d)
    if t == "date":
        if isinstance(v, date) and not isinstance(v, datetime):
            return v
        if not isinstance(v, str):
            raise ValueError(f"{name}: ngày phải dạng YYYY-MM-DD")
        try:
            return date.fromisoformat(v)
        except ValueError as e:
            raise ValueError(f"{name}: ngày phải dạng YYYY-MM-DD") from e
    if t == "datetime":
        if isinstance(v, datetime):
            dt = v
        elif isinstance(v, str):
            try:
                dt = datetime.fromisoformat(v)
            except ValueError as e:
                raise ValueError(f"{name}: thời điểm phải theo ISO 8601") from e
        else:
            raise ValueError(f"{name}: thời điểm phải theo ISO 8601")
        if dt.tzinfo is None:
            raise ValueError(f"{name}: thời điểm phải có múi giờ (vd +07:00)")
        return dt
    if t == "bool":
        if not isinstance(v, bool):
            raise ValueError(f"{name}: cần true/false")
        return v
    if t in ("ref", "user", "file"):
        if isinstance(v, UUID):
            return v
        try:
            return UUID(str(v))
        except ValueError as e:
            raise ValueError(f"{name}: cần UUID") from e
    raise ValueError(f"{name}: không so sánh được với kiểu {t}")


def check_expr_types(e: Expr, by_name: Mapping[str, FieldSpec], *, for_filter: bool) -> None:
    expr_fields(e)
    if not isinstance(e, Cmp):
        for k in children(e):
            check_expr_types(k, by_name, for_filter=for_filter)
        return
    f = by_name.get(e.field)
    if f is None:
        raise ValueError(f"trường '{e.field}' không tồn tại")
    allowed = FILTER_OPS if for_filter else RULE_OPS
    if e.op not in allowed:
        raise ValueError(f"toán tử '{e.op}' không được phép ở đây")
    if f.type == "json" and e.op not in ("is_null", "not_null"):
        raise ValueError(f"trường json '{f.name}' chỉ hỗ trợ is_null/not_null")
    if e.op in ORDER_OPS and f.type not in ORDERABLE:
        raise ValueError(f"không so sánh lớn/nhỏ được với kiểu {f.type} ('{f.name}')")
    if e.op in ("contains", "startswith") and f.type != "text":
        raise ValueError(f"{e.op} chỉ dùng cho trường text")
    if e.other_field:
        g = by_name.get(e.other_field)
        if g is None:
            raise ValueError(f"trường '{e.other_field}' không tồn tại")
        same = f.type == g.type or {f.type, g.type} <= NUMERIC
        if not same:
            raise ValueError(f"không so sánh {f.name}({f.type}) với {g.name}({g.type})")
        if f.type == "json":
            raise ValueError("không so sánh hai trường json")
    elif e.op == "in":
        assert isinstance(e.value, list)
        for x in e.value:
            coerce_literal(f, x)
    elif e.op not in ("is_null", "not_null"):
        coerce_literal(f, e.value)


# ------------------------------------------------------------------ logic 3 trị ----
def eval3(e: Expr, row: Mapping[str, Any], by_name: Mapping[str, FieldSpec]) -> bool | None:
    """Đánh giá như SQL: NULL lan truyền; AND/OR theo bảng chân trị Kleene."""
    if isinstance(e, And):
        rs = [eval3(k, row, by_name) for k in e.and_]
        if False in rs:
            return False
        return True if all(r is True for r in rs) else None
    if isinstance(e, Or):
        rs = [eval3(k, row, by_name) for k in e.or_]
        if True in rs:
            return True
        return False if all(r is False for r in rs) else None
    if isinstance(e, Not):
        r = eval3(e.not_, row, by_name)
        return None if r is None else not r
    f = by_name[e.field]
    v = row.get(e.field)
    if e.op == "is_null":
        return v is None
    if e.op == "not_null":
        return v is not None
    if v is None:
        return None
    if e.op == "in":
        assert isinstance(e.value, list)
        return v in [coerce_literal(f, x) for x in e.value]
    rhs = row.get(e.other_field) if e.other_field else coerce_literal(f, e.value)
    if rhs is None:
        return None
    if e.op in ("contains", "startswith"):
        s, p = str(v).casefold(), str(rhs).casefold()
        return p in s if e.op == "contains" else s.startswith(p)
    return _compare(e.op, v, rhs)


def _compare(op: str, a: Any, b: Any) -> bool:
    if op == "=":
        return bool(a == b)
    if op == "!=":
        return bool(a != b)
    if op == "<":
        return bool(a < b)
    if op == "<=":
        return bool(a <= b)
    if op == ">":
        return bool(a > b)
    return bool(a >= b)


# ------------------------------------------------------- dạng chuỗi (tool-contracts-v1) ----
_OPS2 = {"<=": "<=", ">=": ">=", "!=": "!=", "<>": "!=", "==": "="}
_OPS1 = {"=": "=", "<": "<", ">": ">"}
_KEYWORDS = frozenset({"and", "or", "not", "is", "null", "in", "true", "false"})


class _Tok:
    __slots__ = ("kind", "pos", "text", "value")

    def __init__(self, kind: str, text: str, value: object, pos: int) -> None:
        self.kind, self.text, self.value, self.pos = kind, text, value, pos


def _tokenize(s: str) -> list[_Tok]:
    out: list[_Tok] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in " \t":
            i += 1
        elif c in "(),":
            out.append(_Tok(c, c, None, i))
            i += 1
        elif s[i : i + 2] in _OPS2:
            out.append(_Tok("op", s[i : i + 2], _OPS2[s[i : i + 2]], i))
            i += 2
        elif c in _OPS1:
            out.append(_Tok("op", c, _OPS1[c], i))
            i += 1
        elif c in "'\"":
            j, buf = i + 1, []
            while j < n and s[j] != c:
                buf.append(s[j])
                j += 1
            if j >= n:
                raise ValueError(f"chuỗi chưa đóng nháy ở vị trí {i}")
            out.append(_Tok("str", s[i : j + 1], "".join(buf), i))
            i = j + 1
        elif c.isdigit() or (c == "-" and i + 1 < n and s[i + 1].isdigit()):
            j = i + 1
            while j < n and (s[j].isdigit() or s[j] == "."):
                j += 1
            text = s[i:j]
            if text.count(".") > 1 or text.endswith("."):
                raise ValueError(f"số không hợp lệ '{text}' ở vị trí {i}")
            num: object = Decimal(text) if "." in text else int(text)
            out.append(_Tok("num", text, num, i))
            i = j
        elif "a" <= c <= "z" or c == "_":
            j = i + 1
            while j < n and ("a" <= s[j] <= "z" or s[j].isdigit() or s[j] == "_"):
                j += 1
            word = s[i:j]
            out.append(_Tok("kw" if word in _KEYWORDS else "ident", word, word, i))
            i = j
        else:
            raise ValueError(f"ký tự không hỗ trợ {c!r} ở vị trí {i}")
    return out


class _Parser:
    def __init__(self, toks: list[_Tok]) -> None:
        self.t, self.i = toks, 0

    def peek(self, text: str | None = None) -> _Tok | None:
        tok = self.t[self.i] if self.i < len(self.t) else None
        if tok is not None and text is not None and tok.text != text:
            return None
        return tok

    def take(self, text: str | None = None, kind: str | None = None) -> _Tok:
        tok = self.peek()
        if tok is None or (text is not None and tok.text != text) or (kind and tok.kind != kind):
            where = f"vị trí {tok.pos}" if tok else "cuối biểu thức"
            raise ValueError(f"cú pháp biểu thức sai ở {where}: cần {text or kind}")
        self.i += 1
        return tok

    def expr(self, depth: int) -> Expr:
        if depth > MAX_DEPTH:
            raise ValueError(f"biểu thức lồng quá {MAX_DEPTH} cấp")
        parts = [self.conj(depth)]
        while self.peek("or"):
            self.take("or")
            parts.append(self.conj(depth))
        return parts[0] if len(parts) == 1 else Or.model_validate({"or": parts})

    def conj(self, depth: int) -> Expr:
        parts = [self.unary(depth)]
        while self.peek("and"):
            self.take("and")
            parts.append(self.unary(depth))
        return parts[0] if len(parts) == 1 else And.model_validate({"and": parts})

    def unary(self, depth: int) -> Expr:
        if self.peek("not"):
            self.take("not")
            return Not.model_validate({"not": self.unary(depth + 1)})
        if self.peek("("):
            self.take("(")
            e = self.expr(depth + 1)
            self.take(")")
            return e
        return self.cmp()

    def literal(self) -> Scalar:
        tok = self.take()
        if tok.kind in ("str", "num"):
            return tok.value  # type: ignore[return-value]
        if tok.text in ("true", "false"):
            return tok.text == "true"
        raise ValueError(f"cần giá trị ở vị trí {tok.pos} (chuỗi đặt trong nháy đơn)")

    def cmp(self) -> Expr:
        field = self.take(kind="ident").text
        if self.peek("is"):
            self.take("is")
            neg = bool(self.peek("not"))
            if neg:
                self.take("not")
            self.take("null")
            return Cmp(field=field, op="not_null" if neg else "is_null")
        if self.peek("not") or self.peek("in"):
            neg = bool(self.peek("not"))
            if neg:
                self.take("not")
            self.take("in")
            self.take("(")
            vals: list[Scalar] = [self.literal()]
            while self.peek(","):
                self.take(",")
                vals.append(self.literal())
            self.take(")")
            c = Cmp(field=field, op="in", value=vals)
            return Not.model_validate({"not": c}) if neg else c
        op = self.take(kind="op").value
        nxt = self.peek()
        if nxt is not None and nxt.kind == "ident":
            self.take()
            return Cmp(field=field, op=op, other_field=nxt.text)  # type: ignore[arg-type]
        return Cmp(field=field, op=op, value=self.literal())  # type: ignore[arg-type]


def parse_rule_expr(text: str) -> Expr:
    """`a >= b and (c is not null or d in ('x', 'y'))` → AST. Không regex, không eval."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("biểu thức rỗng")
    if len(text) > MAX_EXPR_TEXT:
        raise ValueError(f"biểu thức dài quá {MAX_EXPR_TEXT} ký tự")
    p = _Parser(_tokenize(text))
    e = p.expr(1)
    if p.peek() is not None:
        tok = p.peek()
        assert tok is not None
        raise ValueError(f"thừa token '{tok.text}' ở vị trí {tok.pos}")
    expr_fields(e)
    return e
