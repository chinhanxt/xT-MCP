# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Định danh trong model spec: whitelist ASCII snake_case, không từ khóa SQL, không cột hệ thống.

Đây là lớp phòng thủ thứ nhất. Lớp thứ hai là compiler: mọi định danh đi qua
`psycopg.sql.Identifier` (luôn quote), mọi giá trị qua `sql.Literal`/placeholder.
Regex ở đây là hằng số của xT, không bao giờ là pattern do spec cung cấp.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated

from pydantic import AfterValidator, Field

# Từ khóa Postgres loại R/T/C (pg_get_keywords() của PG 17, catcode <> 'U').
PG_RESERVED = frozenset(
    {
        "all", "analyse", "analyze", "and", "any", "array", "as", "asc", "asymmetric",
        "authorization", "between", "bigint", "binary", "bit", "boolean", "both", "case", "cast",
        "char", "character", "check", "coalesce", "collate", "collation", "column", "concurrently",
        "constraint", "create", "cross", "current_catalog", "current_date", "current_role",
        "current_schema", "current_time", "current_timestamp", "current_user", "dec", "decimal",
        "default", "deferrable", "desc", "distinct", "do", "else", "end", "except", "exists",
        "extract", "false", "fetch", "float", "for", "foreign", "freeze", "from", "full", "grant",
        "greatest", "group", "grouping", "having", "ilike", "in", "initially", "inner", "inout",
        "int", "integer", "intersect", "interval", "into", "is", "isnull", "join", "json",
        "json_array", "json_arrayagg", "json_exists", "json_object", "json_objectagg",
        "json_query", "json_scalar", "json_serialize", "json_table", "json_value", "lateral",
        "leading", "least", "left", "like", "limit", "localtime", "localtimestamp",
        "merge_action", "national", "natural", "nchar", "none", "normalize", "not", "notnull",
        "null", "nullif", "numeric", "offset", "on", "only", "or", "order", "out", "outer",
        "overlaps", "overlay", "placing", "position", "precision", "primary", "real",
        "references", "returning", "right", "row", "select", "session_user", "setof", "similar",
        "smallint", "some", "substring", "symmetric", "system_user", "table", "tablesample",
        "then", "time", "timestamp", "to", "trailing", "treat", "trim", "true", "union", "unique",
        "user", "using", "values", "varchar", "variadic", "verbose", "when", "where", "window",
        "with", "xmlattributes", "xmlconcat", "xmlelement", "xmlexists", "xmlforest",
        "xmlnamespaces", "xmlparse", "xmlpi", "xmlroot", "xmlserialize", "xmltable",
    }
)  # fmt: skip

# Cột hệ thống mà compiler sinh cho mọi bảng model + cột ẩn của Postgres.
SYSTEM_COLUMNS = frozenset(
    {
        "id", "tenant_id", "owner_id", "team_id", "department_id", "created_at", "updated_at",
        "revision", "created_by", "updated_by",
        "ctid", "xmin", "xmax", "cmin", "cmax", "tableoid", "oid",
    }
)  # fmt: skip

IDENT_MAX = 40
STABLE_ID_MAX = 24
_IDENT_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")
_STABLE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_]{0,23}")
_ROLE_RE = re.compile(r"[a-z][a-z0-9_]{0,62}")


def is_ident(v: str) -> bool:
    return isinstance(v, str) and _IDENT_RE.fullmatch(v) is not None


def check_ident(v: str) -> str:
    if not is_ident(v):
        raise ValueError(
            "tên định danh phải là snake_case ASCII: bắt đầu bằng chữ thường a-z, chỉ gồm a-z 0-9"
            " _,"
            f" tối đa {IDENT_MAX} ký tự"
        )
    if v in PG_RESERVED:
        raise ValueError(f"'{v}' là từ khóa SQL dành riêng, hãy chọn tên khác")
    if v.endswith("_") or "__" in v:
        raise ValueError("tên định danh không được chứa '__' hoặc kết thúc bằng '_'")
    return v


def check_stable_id(v: str) -> str:
    if not isinstance(v, str) or _STABLE_ID_RE.fullmatch(v) is None:
        raise ValueError(
            f"id ổn định phải gồm a-z 0-9 _, bắt đầu bằng chữ hoặc số, tối đa {STABLE_ID_MAX} ký tự"
        )
    return v


def check_role(v: str) -> str:
    """Tên role đi vào GUC `xt.roles` (danh sách phân tách bằng dấu phẩy): chặn chèn ','."""
    if not isinstance(v, str) or _ROLE_RE.fullmatch(v) is None:
        raise ValueError(f"tên vai trò không hợp lệ: {v!r}")
    return v


_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn"})


def check_label(v: str) -> str:
    """Nhãn/thông điệp hiện cho người duyệt: cấm ký tự điều khiển, định dạng vô hình (bidi
    U+202E, zero-width U+200B…), tách dòng U+2028/U+2029 — không giả được dòng trong kế hoạch."""
    if any(unicodedata.category(c) in _FORBIDDEN_CATEGORIES for c in v):
        raise ValueError("nhãn không được chứa ký tự điều khiển/định dạng vô hình/tách dòng")
    if v != v.strip():
        raise ValueError("nhãn không được có khoảng trắng ở đầu/cuối")
    return v


Ident = Annotated[str, AfterValidator(check_ident)]
StableId = Annotated[str, AfterValidator(check_stable_id)]
RoleName = Annotated[str, AfterValidator(check_ident)]
Label = Annotated[str, Field(min_length=1, max_length=120), AfterValidator(check_label)]
Message = Annotated[str, Field(min_length=1, max_length=300), AfterValidator(check_label)]
Description = Annotated[str, Field(max_length=500), AfterValidator(check_label)]
