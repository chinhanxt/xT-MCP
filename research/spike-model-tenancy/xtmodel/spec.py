# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Model spec: định nghĩa nghiệp vụ là DỮ LIỆU do agent soạn, người duyệt.

Mọi định danh (tên model, tên trường, tên role) đi qua `Ident`: ASCII snake_case,
không từ khóa SQL, không trùng cột hệ thống. Nhãn tiếng Việt nằm ở `label`.
Biểu thức ràng buộc chéo trường và bộ lọc tìm kiếm dùng chung một AST khai báo
(`Expr`) — không bao giờ nhận SQL/Python tự do.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Union

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field,
                      model_validator)

# Từ khóa Postgres loại R/T/C (lấy từ pg_get_keywords() của PG 17).
PG_RESERVED = frozenset({'all', 'analyse', 'analyze', 'and', 'any', 'array', 'as', 'asc', 'asymmetric', 'authorization', 'between', 'bigint', 'binary', 'bit', 'boolean', 'both', 'case', 'cast', 'char', 'character', 'check', 'coalesce', 'collate', 'collation', 'column', 'concurrently', 'constraint', 'create', 'cross', 'current_catalog', 'current_date', 'current_role', 'current_schema', 'current_time', 'current_timestamp', 'current_user', 'dec', 'decimal', 'default', 'deferrable', 'desc', 'distinct', 'do', 'else', 'end', 'except', 'exists', 'extract', 'false', 'fetch', 'float', 'for', 'foreign', 'freeze', 'from', 'full', 'grant', 'greatest', 'group', 'grouping', 'having', 'ilike', 'in', 'initially', 'inner', 'inout', 'int', 'integer', 'intersect', 'interval', 'into', 'is', 'isnull', 'join', 'json', 'json_array', 'json_arrayagg', 'json_exists', 'json_object', 'json_objectagg', 'json_query', 'json_scalar', 'json_serialize', 'json_table', 'json_value', 'lateral', 'leading', 'least', 'left', 'like', 'limit', 'localtime', 'localtimestamp', 'merge_action', 'national', 'natural', 'nchar', 'none', 'normalize', 'not', 'notnull', 'null', 'nullif', 'numeric', 'offset', 'on', 'only', 'or', 'order', 'out', 'outer', 'overlaps', 'overlay', 'placing', 'position', 'precision', 'primary', 'real', 'references', 'returning', 'right', 'row', 'select', 'session_user', 'setof', 'similar', 'smallint', 'some', 'substring', 'symmetric', 'system_user', 'table', 'tablesample', 'then', 'time', 'timestamp', 'to', 'trailing', 'treat', 'trim', 'true', 'union', 'unique', 'user', 'using', 'values', 'varchar', 'variadic', 'verbose', 'when', 'where', 'window', 'with', 'xmlattributes', 'xmlconcat', 'xmlelement', 'xmlexists', 'xmlforest', 'xmlnamespaces', 'xmlparse', 'xmlpi', 'xmlroot', 'xmlserialize', 'xmltable'})
SYSTEM_COLUMNS = frozenset({
    "id", "tenant_id", "owner_id", "team_id", "department_id", "created_at",
    "updated_at", "revision", "created_by", "updated_by",
    # cột hệ thống ẩn của Postgres
    "ctid", "xmin", "xmax", "cmin", "cmax", "tableoid", "oid",
})
IDENT_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")      # <= 40 byte, ASCII
STABLE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_]{0,23}")  # id bất biến do engine/agent cấp
MAX_FIELDS = 200
MAX_MODELS = 500


def _ident(v: str) -> str:
    if not isinstance(v, str) or not IDENT_RE.fullmatch(v):
        raise ValueError(
            "tên định danh phải là snake_case ASCII: chữ thường đầu tiên, chỉ a-z 0-9 _ , tối đa 40 ký tự")
    if v in PG_RESERVED:
        raise ValueError(f"'{v}' là từ khóa SQL dành riêng")
    if v.endswith("_") or "__" in v:
        raise ValueError("không được có '__' hoặc kết thúc bằng '_'")
    return v


def _stable_id(v: str) -> str:
    if not isinstance(v, str) or not STABLE_ID_RE.fullmatch(v):
        raise ValueError("id ổn định phải khớp [a-z0-9][a-z0-9_]{0,23}")
    return v


def _label(v: str) -> str:
    if any(ord(c) < 32 for c in v):
        raise ValueError("nhãn không được chứa ký tự điều khiển")
    return v


Ident = Annotated[str, AfterValidator(_ident)]
StableId = Annotated[str, AfterValidator(_stable_id)]
Label = Annotated[str, Field(min_length=1, max_length=120), AfterValidator(_label)]
Scalar = Union[bool, int, Decimal, date, datetime, str, None]

FieldType = Literal["text", "number", "money", "date", "datetime", "bool",
                    "enum", "ref", "user", "file", "json"]
CMP_OPS = ("=", "!=", "<", "<=", ">", ">=")
RULE_OPS = CMP_OPS + ("is_null", "not_null", "in")
FILTER_OPS = RULE_OPS + ("contains", "startswith")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- DSL ----
class Cmp(_Strict):
    """field <op> value | field <op> other_field | field is_null."""
    field: Ident
    op: Literal["=", "!=", "<", "<=", ">", ">=", "is_null", "not_null",
                "in", "contains", "startswith"]
    value: Union[Scalar, list[Scalar]] = None
    other_field: Ident | None = None

    @model_validator(mode="after")
    def _shape(self):
        if self.op in ("is_null", "not_null"):
            if self.value is not None or self.other_field is not None:
                raise ValueError(f"toán tử {self.op} không nhận value/other_field")
        elif self.op == "in":
            if not isinstance(self.value, list) or not (1 <= len(self.value) <= 100):
                raise ValueError("toán tử in cần value là danh sách 1..100 phần tử")
        elif self.other_field is not None and self.value is not None:
            raise ValueError("chỉ được dùng value HOẶC other_field")
        elif self.other_field is None and (self.value is None or isinstance(self.value, list)):
            raise ValueError(f"toán tử {self.op} cần một value vô hướng hoặc other_field")
        return self


class And(_Strict):
    and_: list["Expr"] = Field(alias="and", min_length=1, max_length=50)


class Or(_Strict):
    or_: list["Expr"] = Field(alias="or", min_length=1, max_length=50)


class Not(_Strict):
    not_: "Expr" = Field(alias="not")


Expr = Union[Cmp, And, Or, Not]
for _m in (And, Or, Not):
    _m.model_rebuild()


def expr_depth_and_fields(e: Expr, depth: int = 1) -> tuple[int, set[str]]:
    if depth > 8:
        raise ValueError("biểu thức lồng quá 8 cấp")
    if isinstance(e, Cmp):
        return depth, {e.field} | ({e.other_field} if e.other_field else set())
    kids = e.and_ if isinstance(e, And) else e.or_ if isinstance(e, Or) else [e.not_]
    d, fs = depth, set()
    for k in kids:
        kd, kf = expr_depth_and_fields(k, depth + 1)
        d, fs = max(d, kd), fs | kf
    return d, fs


# --------------------------------------------------------------- spec ----
class EnumOption(_Strict):
    value: Annotated[str, Field(min_length=1, max_length=64)]
    label: Label


class FieldSpec(_Strict):
    id: StableId
    name: Ident
    type: FieldType
    label: Label
    description: Annotated[str, Field(max_length=500)] | None = None
    required: bool = False
    unique: bool = False
    min: Union[int, Decimal, date, datetime, None] = None   # text: độ dài tối thiểu
    max: Union[int, Decimal, date, datetime, None] = None   # text: độ dài tối đa
    regex: Annotated[str, Field(max_length=200)] | None = None
    options: list[EnumOption] | None = None   # cho enum
    ref: Ident | None = None                  # tên model đích cho ref
    default: Scalar = None
    # G-M2: nhãn DLCN theo NĐ 356/2025 (Đ.3 cơ bản, Đ.4 nhạy cảm). Thiếu => engine gợi ý từ tên/nhãn,
    # đánh dấu pii_suggested=true để người duyệt xác nhận; catalog luôn lưu giá trị tường minh.
    pii: Literal["none", "basic", "sensitive"]
    pii_suggested: bool = False
    # G-M1: trường do workflow quản (trạng thái, người duyệt, thời điểm duyệt...). Không ghi được qua
    # create_record/update_record; chỉ đường transition (role DB xt_workflow) mới ghi được.
    managed_by: Literal["workflow"] | None = None

    @model_validator(mode="before")
    @classmethod
    def _pii_default(cls, v):
        if isinstance(v, dict) and v.get("pii") is None:
            v = {**v, "pii": suggest_pii(str(v.get("name", "")), str(v.get("label", "")), str(v.get("type", ""))),
                 "pii_suggested": True}
        return v

    @model_validator(mode="after")
    def _check(self):
        t = self.type
        if self.managed_by and self.required and self.default is None:
            raise ValueError("trường managed_by=workflow bắt buộc phải có default (create_record không ghi được)")
        if t == "enum":
            if not self.options or len(self.options) > 200:
                raise ValueError("enum cần 1..200 options")
            vals = [o.value for o in self.options]
            if len(set(vals)) != len(vals):
                raise ValueError("enum có giá trị trùng")
        elif self.options is not None:
            raise ValueError("options chỉ dùng cho enum")
        if (t == "ref") != (self.ref is not None):
            raise ValueError("ref phải khai báo đúng với type=ref")
        if self.regex is not None:
            if t != "text":
                raise ValueError("regex chỉ dùng cho text")
            # Chỉ chấp nhận regex mà engine Rust (thời gian tuyến tính, không backtracking) của
            # pydantic-core biên dịch được => không backreference/lookaround => không ReDoS ở app.
            # Postgres kiểm lại cú pháp khi diễn tập CHECK.
            from pydantic import TypeAdapter
            try:
                TypeAdapter(Annotated[str, Field(pattern=self.regex)])
            except Exception as e:  # SchemaError
                raise ValueError(f"regex không hợp lệ/không được hỗ trợ: {str(e).splitlines()[-1][:120]}")
        if self.min is not None or self.max is not None:
            if t not in ("text", "number", "money", "date", "datetime"):
                raise ValueError(f"min/max không áp dụng cho kiểu {t}")
            if t in ("text", "money") and any(isinstance(x, (date, datetime)) or (x is not None and int(x) != x) for x in (self.min, self.max)):
                raise ValueError("min/max của text (độ dài) và money (VND) phải là số nguyên")
            if self.min is not None and self.max is not None and self.min > self.max:
                raise ValueError("min > max")
        if self.unique and t in ("json", "bool", "file"):
            raise ValueError(f"unique không áp dụng cho kiểu {t}")
        if self.default is not None and t in ("ref", "user", "file", "json"):
            raise ValueError(f"default không hỗ trợ cho kiểu {t}")
        return self


# Gợi ý theo NĐ 356/2025: Đ.3 k.7 (SĐT, số định danh, hộ chiếu), k.4 (địa chỉ), họ tên, email = cơ bản;
# Đ.4 k.1: sức khỏe (đ.d), ảnh CCCD (đ.i), tài khoản/thẻ ngân hàng (đ.k) = nhạy cảm; lương: xT xếp nhạy cảm
# (thận trọng, bí mật cá nhân đ.c) — theo research/spike-pii-lane.md.
_PII_SENSITIVE = ("luong", "thu_nhap", "suc_khoe", "benh", "so_tai_khoan", "tai_khoan_ngan_hang", "the_ngan_hang",
                  "anh_cccd", "anh_can_cuoc", "ton_giao", "chinh_tri", "tien_an")
_PII_BASIC = ("cccd", "cmnd", "can_cuoc", "dinh_danh", "ho_chieu", "dien_thoai", "sdt", "email", "dia_chi",
              "ho_ten", "ngay_sinh", "gioi_tinh", "quoc_tich", "ma_so_thue_ca_nhan")


def suggest_pii(name: str, label: str, type_: str) -> str:
    n = name.lower()
    if type_ in ("bool", "enum", "ref", "json") and not any(k in n for k in _PII_SENSITIVE):
        return "none"
    if any(k in n for k in _PII_SENSITIVE):
        return "sensitive"
    if any(k in n for k in _PII_BASIC):
        return "basic"
    return "none"


class IndexSpec(_Strict):
    id: StableId
    fields: list[Ident] = Field(min_length=1, max_length=5)
    unique: bool = False


class RuleSpec(_Strict):
    id: StableId
    check: Expr
    message: Label   # thông báo tiếng Việt khi vi phạm


class PermSpec(_Strict):
    actions: list[Literal["create", "read", "update", "delete"]] = Field(min_length=1)
    scope: Literal["own", "team", "department", "all"] = "own"


class ModelSpec(_Strict):
    id: StableId
    name: Ident
    label: Label
    title_field: Ident | None = None
    fields: list[FieldSpec] = Field(min_length=1, max_length=MAX_FIELDS)
    indexes: list[IndexSpec] = []
    rules: list[RuleSpec] = Field(default=[], max_length=50)
    permissions: dict[Ident, PermSpec] = {}

    @model_validator(mode="after")
    def _check(self):
        names = [f.name for f in self.fields]
        ids = [f.id for f in self.fields]
        for what, xs in (("tên trường", names), ("id trường", ids)):
            dup = {x for x in xs if xs.count(x) > 1}
            if dup:
                raise ValueError(f"{what} bị trùng: {sorted(dup)}")
        bad = SYSTEM_COLUMNS & set(names)
        if bad:
            raise ValueError(f"tên trường trùng cột hệ thống: {sorted(bad)}")
        by = {f.name: f for f in self.fields}
        if self.title_field and self.title_field not in by:
            raise ValueError(f"title_field '{self.title_field}' không tồn tại")
        for ix in self.indexes:
            miss = [x for x in ix.fields if x not in by]
            if miss:
                raise ValueError(f"index {ix.id} tham chiếu trường không tồn tại: {miss}")
        for r in self.rules:
            _, fs = expr_depth_and_fields(r.check)
            miss = fs - set(by)
            if miss:
                raise ValueError(f"rule {r.id} tham chiếu trường không tồn tại: {sorted(miss)}")
            check_expr_types(r.check, by, for_filter=False)
        sids = [x.id for x in self.indexes] + [x.id for x in self.rules]
        if len(set(sids)) != len(sids):
            raise ValueError("id index/rule bị trùng")
        return self

    def field(self, name: str) -> FieldSpec:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(name)


class Catalog(_Strict):
    """Toàn bộ model của một deployment (một phiên bản)."""
    version: int = Field(ge=1)
    models: list[ModelSpec] = Field(max_length=MAX_MODELS)

    @model_validator(mode="after")
    def _check(self):
        names = [m.name for m in self.models]
        ids = [m.id for m in self.models]
        if len(set(names)) != len(names) or len(set(ids)) != len(ids):
            raise ValueError("tên hoặc id model bị trùng")
        for m in self.models:
            for f in m.fields:
                if f.type == "ref" and f.ref not in names:
                    raise ValueError(f"{m.name}.{f.name}: model đích '{f.ref}' không tồn tại")
        return self

    def model(self, name: str) -> ModelSpec:
        for m in self.models:
            if m.name == name:
                return m
        raise KeyError(name)


# ------------------------------------------------- kiểm kiểu biểu thức ----
ORDERABLE = {"text", "number", "money", "date", "datetime", "enum"}


def coerce_value(f: FieldSpec, v: Any) -> Any:
    """Ép một literal trong DSL về kiểu Python đúng với trường (ném ValueError)."""
    from uuid import UUID
    t = f.type
    if v is None:
        return None
    if t in ("text",):
        if not isinstance(v, str):
            raise ValueError(f"{f.name}: cần chuỗi")
        return v
    if t == "enum":
        if v not in {o.value for o in f.options or []}:
            raise ValueError(f"{f.name}: '{v}' không thuộc enum")
        return v
    if t == "number":
        if isinstance(v, bool):
            raise ValueError(f"{f.name}: cần số")
        return Decimal(str(v))
    if t == "money":
        if isinstance(v, bool) or Decimal(str(v)) != int(Decimal(str(v))):
            raise ValueError(f"{f.name}: tiền VND phải là số nguyên đồng")
        return int(Decimal(str(v)))
    if t == "date":
        return v if isinstance(v, date) and not isinstance(v, datetime) else date.fromisoformat(str(v))
    if t == "datetime":
        return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    if t == "bool":
        if not isinstance(v, bool):
            raise ValueError(f"{f.name}: cần true/false")
        return v
    if t in ("ref", "user", "file"):
        return UUID(str(v))
    raise ValueError(f"{f.name}: không so sánh được kiểu {t}")


def check_expr_types(e: Expr, by: dict[str, FieldSpec], for_filter: bool) -> None:
    if isinstance(e, Cmp):
        f = by.get(e.field)
        if f is None:
            raise ValueError(f"trường '{e.field}' không tồn tại")
        allowed = FILTER_OPS if for_filter else RULE_OPS
        if e.op not in allowed:
            raise ValueError(f"toán tử '{e.op}' không được phép ở đây")
        if f.type == "json" and e.op not in ("is_null", "not_null"):
            raise ValueError(f"trường json '{f.name}' chỉ hỗ trợ is_null/not_null")
        if e.op in ("<", "<=", ">", ">=") and f.type not in ORDERABLE:
            raise ValueError(f"không so sánh lớn/nhỏ được với kiểu {f.type}")
        if e.op in ("contains", "startswith") and f.type != "text":
            raise ValueError(f"{e.op} chỉ dùng cho text")
        if e.other_field:
            g = by.get(e.other_field)
            if g is None:
                raise ValueError(f"trường '{e.other_field}' không tồn tại")
            if {f.type, g.type} - {"number", "money"} and f.type != g.type:
                raise ValueError(f"không so sánh {f.name}({f.type}) với {g.name}({g.type})")
        elif e.op == "in":
            for x in e.value:
                coerce_value(f, x)
        elif e.op not in ("is_null", "not_null"):
            coerce_value(f, e.value)
        return
    kids = e.and_ if isinstance(e, And) else e.or_ if isinstance(e, Or) else [e.not_]
    for k in kids:
        check_expr_types(k, by, for_filter)
