# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Model spec: nghiệp vụ là DỮ LIỆU do agent soạn, người duyệt (architecture-v2 §6.1, D6).

- Pydantic v2 là nguồn sự thật, `extra=forbid` ở mọi tầng; xuất JSON Schema cho tool.
- Spec chỉ nhận JSON (`parse_catalog_json`): YAML 1.1 đọc `no` thành `False`; khóa trùng bị từ chối.
- Regex trong spec chỉ được biên dịch bằng engine Rust của pydantic-core (thời gian tuyến tính,
  không backreference/lookaround) và phải nằm trong tập con mà Postgres ARE hiểu giống hệt.
  Không bao giờ đưa pattern do spec cung cấp vào module `re` của Python (ReDoS).
- Mọi model/field/index/rule có id ổn định: rename giữ id, nên planner sinh RENAME thật.
- G-M1: `managed_by: workflow`. G-M2: `pii: none|basic|sensitive` bắt buộc trong catalog
  (thiếu thì engine gợi ý theo NĐ 356/2025 và đặt `pii_suggested=true`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    TypeAdapter,
    model_validator,
)
from pydantic_core import SchemaError

from xt.domain.model.expr import (
    Expr,
    FiniteDecimal,
    Scalar,
    check_expr_types,
    coerce_literal,
    expr_fields,
)
from xt.domain.model.ident import (
    SYSTEM_COLUMNS,
    Description,
    Ident,
    Label,
    Message,
    RoleName,
    StableId,
    check_label,
)

FieldType = Literal[
    "text", "number", "money", "date", "datetime", "bool", "enum", "ref", "user", "file", "json"
]  # fmt: skip
PiiLevel = Literal["none", "basic", "sensitive"]
Action = Literal["create", "read", "update", "delete"]
Scope = Literal["own", "team", "department", "all"]

PII_RANK: dict[str, int] = {"none": 0, "basic": 1, "sensitive": 2}
SCOPE_RANK: dict[str, int] = {"own": 0, "team": 1, "department": 2, "all": 3}
MAX_FIELDS = 200
MAX_MODELS = 100
MAX_ENUM = 200
MAX_REGEX = 200
REGEX_MAX_REPEAT = 255  # RE_DUP_MAX của Postgres
JSON_MAX_BYTES = 65536
MAX_SPEC_BYTES = 1 << 20  # 1 MiB cho một catalog JSON (AUD-11)
MAX_TOTAL_FIELDS = 3000
SpecInt = StrictInt
Bound = StrictInt | FiniteDecimal | StrictStr | None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# ------------------------------------------------------------------------- regex ----
_SAFE_ESCAPES = frozenset("\\.^$*+?()[]{}|/-nrt")  # \n \r \t: hai engine hiểu giống nhau
_QUANT = frozenset("*+?{")


def _check_repeat(pattern: str, i: int) -> int:
    """`pattern[i] == '{'`: kiểm `{n}`, `{n,}`, `{n,m}` ≤ 255; trả vị trí sau '}'."""
    j = i + 1
    num = ""
    while j < len(pattern) and (pattern[j].isdigit() or pattern[j] == ","):
        num += pattern[j]
        j += 1
    if j >= len(pattern) or pattern[j] != "}" or not num or num.count(",") > 1 or num[0] == ",":
        raise ValueError("regex có '{' không phải lượng từ {n}/{n,}/{n,m}; viết \\{ nếu là ký tự")
    for part in num.split(","):
        if part and int(part) > REGEX_MAX_REPEAT:
            raise ValueError(f"regex lặp tối đa {REGEX_MAX_REPEAT} lần")
    return j + 1


def check_regex(pattern: str) -> str:
    """Nhận pattern nếu (1) engine Rust biên dịch được, (2) thuộc tập con mà Rust và PG ARE hiểu
    GIỐNG HỆT và (3) không có lượng từ lồng nhau.

    Tập con: escape chỉ dành cho ký tự đặc biệt (`\\.`, `\\-`, `\\(`...); cấm mọi escape chữ/số
    (`\\d \\w \\s \\b \\x41 \\u…` — hai engine hiểu khác nhau, AUD-10); cấm lớp POSIX `[[:alpha:]]`,
    cờ/nhóm có tên `(?…)` trừ `(?:`, lặp > 255 (RE_DUP_MAX của PG). Cấm nhóm có lượng từ bên
    trong lại bị lặp, ví dụ `(a+)+`, `([a-z]*){200}` — Rust tuyến tính nhưng PG có thể tốn CPU
    (AUD-17). Duyệt từng ký tự, không dùng regex.
    """
    if len(pattern) > MAX_REGEX:
        raise ValueError(f"regex dài quá {MAX_REGEX} ký tự")
    i, n = 0, len(pattern)
    groups: list[bool] = []  # mỗi nhóm đang mở: đã có lượng từ bên trong chưa
    closed_had_quant = False
    while i < n:
        c = pattern[i]
        just_closed = False
        if c == "\\":
            if i + 1 >= n:
                raise ValueError("regex kết thúc bằng '\\'")
            if pattern[i + 1] not in _SAFE_ESCAPES:
                raise ValueError(
                    f"regex không được dùng '\\{pattern[i + 1]}' (Rust và Postgres hiểu khác nhau);"
                    " dùng lớp ký tự tường minh như [0-9], [A-Za-z]"
                )
            i += 2
        elif c == "[":
            if pattern[i + 1 : i + 3] in ("[:", "[=", "[."):
                raise ValueError(
                    "regex không được dùng lớp POSIX [[:...:]]; dùng khoảng tường minh"
                )
            j = i + 1
            if j < n and pattern[j] == "^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                if pattern[j] == "\\":
                    if j + 1 >= n or pattern[j + 1] not in _SAFE_ESCAPES:
                        raise ValueError("regex: trong [...] chỉ được escape ký tự đặc biệt")
                    j += 1
                elif pattern[j] == "[":
                    # Rust hiểu lớp lồng/POSIX, PG ARE coi '[' là ký tự (R2-05)
                    raise ValueError("regex không được lồng '[' trong lớp ký tự; viết \\[")
                elif pattern[j : j + 2] in ("&&", "--", "~~"):
                    raise ValueError("regex không dùng phép tập &&, --, ~~ trong lớp ký tự")
                j += 1
            if j >= n:
                raise ValueError("regex có '[' chưa đóng")
            i = j + 1
        elif c == "(":
            if pattern[i + 1 : i + 2] == "?":
                if pattern[i + 2 : i + 3] != ":":
                    raise ValueError(
                        "regex chỉ được dùng nhóm thường hoặc (?:...); cấm cờ/nhóm có tên"
                    )
                i += 3
            else:
                i += 1
            groups.append(False)
            continue
        elif c == ")":
            if not groups:
                raise ValueError("regex có ')' thừa")
            closed_had_quant = groups.pop()
            just_closed = True
            i += 1
        elif c == ".":
            # Rust: '.' không khớp xuống dòng; PG ARE mặc định có khớp (R2-05)
            raise ValueError(
                "regex không dùng '.'; viết lớp ký tự tường minh, ví dụ [^\\n] hoặc [a-z0-9]"
            )
        elif c in _QUANT:
            # lượng từ không đứng sau một nguyên tử (đầu pattern, sau '(' '|' '^' '$' hoặc sau một
            # lượng từ khác như {2}{2}, a+{1,3}): PG từ chối "quantifier operand invalid" (R3-07)
            raise ValueError(f"regex có lượng từ '{c}' không có toán hạng hợp lệ (vị trí {i})")
        elif c in "^$|":
            i += 1
            continue  # neo/tách nhánh: không nhận lượng từ ngay sau
        else:
            i += 1
        # lượng từ ngay sau nguyên tử vừa đọc
        if i < n and pattern[i] in _QUANT:
            start = i
            i = _check_repeat(pattern, i) if pattern[i] == "{" else i + 1
            q = pattern[start:i]
            repeats = q in ("*", "+") or (q.startswith("{") and q not in ("{0,1}", "{1}", "{0}"))
            if just_closed and closed_had_quant and repeats:
                raise ValueError(
                    "regex không được lặp một nhóm có lượng từ bên trong"
                    " (vd (a+)+, ([a-z]*){200}, (a{255}){255}): có thể tốn CPU ở Postgres"
                )
            if i < n and pattern[i] in "?+":  # lazy/possessive
                raise ValueError("regex không dùng lượng từ lazy/possessive (*?, ++)")
            if groups:  # mọi lượng từ (kể cả có cận) trong nhóm: cấm lặp nhóm đó (R2-05)
                groups[-1] = True
        elif just_closed and closed_had_quant and groups:
            groups[-1] = True
    if groups:
        raise ValueError("regex có '(' chưa đóng")
    try:
        TypeAdapter(
            Annotated[str, Field(pattern=pattern)], config=ConfigDict(regex_engine="rust-regex")
        )
    except SchemaError as e:
        detail = str(e).strip().splitlines()[-1][:160] if str(e).strip() else "lỗi cú pháp"
        raise ValueError(f"regex không hợp lệ/không được hỗ trợ: {detail}") from e
    return pattern


# ------------------------------------------------------------------- gợi ý DLCN ----
# NĐ 356/2025: Đ.3 k.7 (SĐT, số định danh, hộ chiếu), k.4 (địa chỉ), họ tên, email, ngày sinh
# = cơ bản; Đ.4 k.1: sức khỏe, tài khoản/thẻ ngân hàng, ảnh CCCD, tôn giáo, chính trị, tiền án
# = nhạy cảm; lương: xT xếp nhạy cảm (thận trọng). Theo research/spike-pii-lane.md, SMT §9.
_PII_SENSITIVE_NAME = (
    "salary", "wage", "income", "payroll", "health", "medical", "diagnos", "disease",
    "bank_account", "card_number", "credit_card", "religion", "political", "criminal",
    "id_card_image", "biometric", "fingerprint",
    "luong", "thu_nhap", "suc_khoe", "benh", "so_tai_khoan", "tai_khoan_ngan_hang",
    "the_ngan_hang", "anh_cccd", "anh_can_cuoc", "ton_giao", "chinh_tri", "tien_an",
)  # fmt: skip
_PII_SENSITIVE_LABEL = (
    "lương", "thu nhập", "sức khỏe", "sức khoẻ", "bệnh", "tài khoản ngân hàng",
    "số tài khoản", "thẻ ngân hàng", "ảnh cccd", "ảnh căn cước", "tôn giáo", "chính trị",
    "tiền án", "sinh trắc",
)  # fmt: skip
_PII_BASIC_NAME = (
    "national_id", "citizen_id", "id_number", "passport", "phone", "mobile", "email",
    "address", "full_name", "birth", "dob", "gender", "nationality", "personal_tax",
    "cccd", "cmnd", "can_cuoc", "dinh_danh", "ho_chieu", "dien_thoai", "sdt", "dia_chi",
    "ho_ten", "ngay_sinh", "gioi_tinh", "quoc_tich", "ma_so_thue_ca_nhan",
)  # fmt: skip
_PII_BASIC_LABEL = (
    "cccd", "cmnd", "căn cước", "định danh", "hộ chiếu", "điện thoại", "email", "địa chỉ",
    "họ tên", "họ và tên", "ngày sinh", "giới tính", "quốc tịch", "mã số thuế cá nhân",
)  # fmt: skip


def suggest_pii(name: str, label: str, type_: str) -> PiiLevel:
    """Gợi ý nhãn DLCN từ tên/nhãn/kiểu; người duyệt xác nhận (cờ `pii_suggested`)."""
    n, lb = name.lower(), label.casefold()
    if any(k in n for k in _PII_SENSITIVE_NAME) or any(k in lb for k in _PII_SENSITIVE_LABEL):
        return "sensitive"
    if type_ in ("bool", "enum", "ref", "json", "user", "file"):
        return "none"
    if any(k in n for k in _PII_BASIC_NAME) or any(k in lb for k in _PII_BASIC_LABEL):
        return "basic"
    return "none"


# ------------------------------------------------------------------------- spec ----
class EnumOption(_Strict):
    value: Annotated[str, Field(min_length=1, max_length=64), AfterValidator(check_label)]
    label: Label


class FieldSpec(_Strict):
    id: StableId
    name: Ident
    type: FieldType
    label: Label
    description: Description | None = None
    required: StrictBool = False
    unique: StrictBool = False
    min: Bound = None  # text: độ dài tối thiểu
    max: Bound = None  # text: độ dài tối đa
    regex: Annotated[str, Field(max_length=MAX_REGEX)] | None = None
    options: Annotated[list[EnumOption], Field(min_length=1, max_length=MAX_ENUM)] | None = None
    ref: Ident | None = None
    default: Scalar = None
    pii: PiiLevel = "none"  # thiếu → gợi ý (validator "before"), catalog luôn lưu tường minh
    pii_suggested: StrictBool = False
    managed_by: Literal["workflow"] | None = None

    @model_validator(mode="before")
    @classmethod
    def _pii_default(cls, v: Any) -> Any:
        if isinstance(v, Mapping) and v.get("pii") is None:
            level = suggest_pii(str(v.get("name", "")), str(v.get("label", "")), str(v.get("type")))
            return {**v, "pii": level, "pii_suggested": True}
        return v

    @model_validator(mode="after")
    def _check(self) -> Self:
        t = self.type
        if self.managed_by and self.required and self.default is None:
            raise ValueError(
                "trường managed_by=workflow mà bắt buộc thì phải có default"
                " (create_record không ghi được trường này)"
            )
        if t == "enum":
            if not self.options:
                raise ValueError("enum cần danh sách options (1..200)")
            vals = [o.value for o in self.options]
            if len(set(vals)) != len(vals):
                raise ValueError("enum có giá trị trùng")
        elif self.options is not None:
            raise ValueError("options chỉ dùng cho kiểu enum")
        if (t == "ref") != (self.ref is not None):
            raise ValueError("thuộc tính ref chỉ và bắt buộc khai báo khi type=ref")
        if self.regex is not None:
            if t != "text":
                raise ValueError("regex chỉ dùng cho kiểu text")
            check_regex(self.regex)
        if self.min is not None or self.max is not None:
            self._check_bounds()
        if self.unique and t in ("json", "bool", "file"):
            raise ValueError(f"unique không áp dụng cho kiểu {t}")
        if self.default is not None:
            if t in ("ref", "user", "file", "json"):
                raise ValueError(f"default không hỗ trợ cho kiểu {t}")
            d = coerce_literal(self, self.default)
            self._check_default(d)
        return self

    def _check_bounds(self) -> None:
        t = self.type
        if t not in ("text", "number", "money", "date", "datetime"):
            raise ValueError(f"min/max không áp dụng cho kiểu {t}")
        if t == "text":
            for b in (self.min, self.max):
                if b is not None and (not isinstance(b, int) or isinstance(b, bool) or b < 0):
                    raise ValueError("min/max của text là độ dài, phải là số nguyên ≥ 0")
            lo: Any = self.min
            hi: Any = self.max
        else:
            lo, hi = coerce_literal(self, self.min), coerce_literal(self, self.max)
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("min lớn hơn max")

    def _check_default(self, d: object) -> None:
        if self.type == "text":
            assert isinstance(d, str)
            if self.min is not None and len(d) < int(self.min):
                raise ValueError("default ngắn hơn min")
            if self.max is not None and len(d) > int(self.max):
                raise ValueError("default dài hơn max")
        elif self.type in ("number", "money", "date", "datetime"):
            lo, hi = coerce_literal(self, self.min), coerce_literal(self, self.max)
            if (lo is not None and d < lo) or (hi is not None and d > hi):  # type: ignore[operator]
                raise ValueError("default nằm ngoài khoảng min..max")

    def coerced(self, attr: Literal["min", "max", "default"]) -> object:
        """Giá trị đã ép kiểu (so sánh ổn định giữa spec gốc và spec nạp lại từ catalog)."""
        v = getattr(self, attr)
        if v is None:
            return None
        if self.type == "text" and attr != "default":
            return int(v)
        return coerce_literal(self, v)


class IndexSpec(_Strict):
    id: StableId
    fields: Annotated[list[Ident], Field(min_length=1, max_length=5)]
    unique: StrictBool = False


class RuleSpec(_Strict):
    id: StableId
    check: Expr
    message: Message


class PermSpec(_Strict):
    actions: Annotated[list[Action], Field(min_length=1, max_length=4)]
    scope: Scope = "own"

    @model_validator(mode="after")
    def _uniq(self) -> Self:
        if len(set(self.actions)) != len(self.actions):
            raise ValueError("actions bị trùng")
        return self


class ModelSpec(_Strict):
    id: StableId
    name: Ident
    label: Label
    description: Description | None = None
    title_field: Ident | None = None
    fields: Annotated[list[FieldSpec], Field(min_length=1, max_length=MAX_FIELDS)]
    indexes: Annotated[list[IndexSpec], Field(max_length=20)] = []
    rules: Annotated[list[RuleSpec], Field(max_length=50)] = []
    permissions: Annotated[dict[RoleName, PermSpec], Field(max_length=50)] = {}

    @model_validator(mode="after")
    def _check(self) -> Self:
        for what, xs in (
            ("tên trường", [f.name for f in self.fields]),
            ("id trường", [f.id for f in self.fields]),
        ):
            dup = sorted({x for x in xs if xs.count(x) > 1})
            if dup:
                raise ValueError(f"{what} bị trùng: {dup}")
        by = self.by_name()
        bad = sorted(SYSTEM_COLUMNS & set(by))
        if bad:
            raise ValueError(f"tên trường trùng cột hệ thống: {bad}")
        if self.title_field and self.title_field not in by:
            raise ValueError(f"title_field '{self.title_field}' không tồn tại")
        for ix in self.indexes:
            miss = [x for x in ix.fields if x not in by]
            if miss:
                raise ValueError(f"index {ix.id} tham chiếu trường không tồn tại: {miss}")
            if len(set(ix.fields)) != len(ix.fields):
                raise ValueError(f"index {ix.id} có trường trùng")
            if any(by[x].type == "json" for x in ix.fields):
                raise ValueError(f"index {ix.id} không được chứa trường json")
        for r in self.rules:
            miss = sorted(expr_fields(r.check) - set(by))
            if miss:
                raise ValueError(f"rule {r.id} tham chiếu trường không tồn tại: {miss}")
            check_expr_types(r.check, by, for_filter=False)
        sids = [x.id for x in self.indexes] + [x.id for x in self.rules]
        if len(set(sids)) != len(sids):
            raise ValueError("id index/rule bị trùng")
        return self

    def by_name(self) -> dict[str, FieldSpec]:
        return {f.name: f for f in self.fields}

    def by_id(self) -> dict[str, FieldSpec]:
        return {f.id: f for f in self.fields}

    def field(self, name: str) -> FieldSpec:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(name)


class Catalog(_Strict):
    """Toàn bộ model của MỘT tenant ở một phiên bản (`catalog_version`)."""

    version: Annotated[int, Field(ge=1, strict=True)]
    models: Annotated[list[ModelSpec], Field(max_length=MAX_MODELS)]

    @model_validator(mode="after")
    def _check(self) -> Self:
        names = [m.name for m in self.models]
        ids = [m.id for m in self.models]
        if len(set(names)) != len(names):
            raise ValueError(
                f"tên model bị trùng: {sorted({x for x in names if names.count(x) > 1})}"
            )
        if len(set(ids)) != len(ids):
            raise ValueError(f"id model bị trùng: {sorted({x for x in ids if ids.count(x) > 1})}")
        total = sum(len(m.fields) for m in self.models)
        if total > MAX_TOTAL_FIELDS:
            raise ValueError(f"catalog có {total} trường, vượt trần {MAX_TOTAL_FIELDS}")
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

    def by_id(self) -> dict[str, ModelSpec]:
        return {m.id: m for m in self.models}

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    def digest(self) -> str:
        canon = json.dumps(self.dump(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        import hashlib

        return hashlib.sha256(canon.encode()).hexdigest()


# ------------------------------------------------------------------ JSON đầu vào ----
class SpecFormatError(ValueError):
    """Đầu vào không phải JSON hợp lệ cho spec (YAML, khóa trùng, NaN...)."""


def _no_dupes(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise SpecFormatError(f"khóa JSON bị trùng: '{k}'")
        out[k] = v
    return out


def _no_constant(name: str) -> Any:
    raise SpecFormatError(f"JSON không được chứa {name}")


def load_spec_json(text: str | bytes) -> Any:
    """json.loads chặt: từ chối khóa trùng, NaN/Infinity; số thực đọc thành Decimal."""
    if not isinstance(text, str | bytes):
        raise SpecFormatError("spec phải là chuỗi JSON")
    size = len(text.encode("utf-8")) if isinstance(text, str) else len(text)
    if size > MAX_SPEC_BYTES:
        raise SpecFormatError(f"spec JSON {size} byte vượt trần {MAX_SPEC_BYTES} byte")
    try:
        return json.loads(
            text, object_pairs_hook=_no_dupes, parse_float=Decimal, parse_constant=_no_constant
        )
    except SpecFormatError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise SpecFormatError(f"spec phải là JSON hợp lệ (YAML không được nhận): {e}") from e


def parse_catalog_json(text: str | bytes) -> Catalog:
    return Catalog.model_validate(load_spec_json(text))


def parse_model_json(text: str | bytes) -> ModelSpec:
    return ModelSpec.model_validate(load_spec_json(text))


def _schema(model: type[BaseModel], title: str) -> dict[str, Any]:
    s = model.model_json_schema(by_alias=True)
    s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    s["title"] = title
    return s


def catalog_json_schema() -> dict[str, Any]:
    return _schema(Catalog, "xT model catalog v1")


def model_spec_json_schema() -> dict[str, Any]:
    return _schema(ModelSpec, "xT model spec v1")
