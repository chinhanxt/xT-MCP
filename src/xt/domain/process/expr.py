# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Biểu thức an toàn cho `when`, tham số service, điều kiện gateway và `trigger.input`.

Chỉ chấp nhận một tập AST đóng (so sánh, and/or/not, in, chỉ mục, + - /, if-else, hằng,
list/tuple).
Không gọi hàm, không thuộc tính, không lambda/comprehension, không tên bắt đầu bằng `_`,
không bytes.
Trần: ≤ 500 ký tự, ≤ 120 node, độ sâu ≤ 8, hằng chuỗi ≤ 200 ký tự (VERD N5, VS A.1).
Đánh giá bằng `eval` trên cây đã kiểm, `__builtins__` rỗng: không có đường tới hàm dựng sẵn.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Any

from xt.domain.process.errors import UnsafeExpression

ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.BinOp, ast.Add, ast.Sub, ast.Div, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
    ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot, ast.Name, ast.Load, ast.Constant,
    ast.List, ast.Tuple, ast.Subscript, ast.Slice, ast.IfExp,
)  # fmt: skip
# Node "ngữ cảnh/toán tử" không tính vào độ sâu (Load, Add, Eq...): chỉ đo lồng của biểu thức.
_NO_DEPTH: tuple[type[ast.AST], ...] = (ast.Load, ast.operator, ast.boolop, ast.cmpop, ast.unaryop)
MAX_EXPR_LEN = 500
MAX_EXPR_NODES = 120
MAX_EXPR_DEPTH = 8
MAX_STR_CONST = 200
LITERAL_NAMES = frozenset({"True", "False", "None"})


def _depth(node: ast.AST, level: int = 0) -> int:
    if isinstance(node, _NO_DEPTH):
        return level
    children = list(ast.iter_child_nodes(node))
    if not children:
        return level + 1
    return max(_depth(c, level + 1) for c in children)


def check_expr(expr: object) -> ast.Expression:
    """Parse + kiểm whitelist. Lỗi -> `UnsafeExpression` (thông báo tiếng Việt)."""
    if not isinstance(expr, str):
        raise UnsafeExpression("biểu thức phải là chuỗi")
    if len(expr) > MAX_EXPR_LEN:
        raise UnsafeExpression(f"biểu thức dài quá {MAX_EXPR_LEN} ký tự")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except (SyntaxError, RecursionError, MemoryError, ValueError) as e:
        raise UnsafeExpression(f"biểu thức sai cú pháp: {type(e).__name__}") from None
    for count, n in enumerate(ast.walk(tree), start=1):
        if count > MAX_EXPR_NODES:
            raise UnsafeExpression(f"biểu thức quá phức tạp (> {MAX_EXPR_NODES} node)")
        if not isinstance(n, ALLOWED_NODES):
            raise UnsafeExpression(
                f"cú pháp '{type(n).__name__}' không được phép trong biểu thức {expr!r}"
            )
        if isinstance(n, ast.Name) and n.id.startswith("_"):
            raise UnsafeExpression(f"tên {n.id!r} không được phép (bắt đầu bằng '_')")
        if isinstance(n, ast.Constant):
            if isinstance(n.value, bytes):
                raise UnsafeExpression("không cho phép hằng bytes")
            if isinstance(n.value, str) and len(n.value) > MAX_STR_CONST:
                raise UnsafeExpression(f"hằng chuỗi dài quá {MAX_STR_CONST} ký tự")
    try:
        depth = _depth(tree)
    except RecursionError:
        raise UnsafeExpression("biểu thức lồng quá sâu") from None
    if depth > MAX_EXPR_DEPTH:
        raise UnsafeExpression(f"biểu thức lồng quá sâu (> {MAX_EXPR_DEPTH} cấp)")
    return tree


def expr_names(expr: str) -> set[str]:
    """Tên biến mà biểu thức đọc (trừ True/False/None)."""
    return {
        n.id
        for n in ast.walk(check_expr(expr))
        if isinstance(n, ast.Name) and n.id not in LITERAL_NAMES
    }


def evaluate(expr: str, context: Mapping[str, Any]) -> Any:
    """Đánh giá biểu thức đã kiểm trên `context` (dữ liệu task). Không có builtins."""
    tree = check_expr(expr)
    env: dict[str, Any] = dict(context)
    env["__builtins__"] = {}  # gán sau cùng: context không thể ghi đè
    return eval(compile(tree, "<xt-expr>", "eval"), env)
