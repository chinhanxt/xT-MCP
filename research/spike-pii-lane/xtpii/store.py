# SPDX-License-Identifier: Apache-2.0
"""Engine CRUD giả lập lõi xT trên sqlite.

CỐ Ý "ngây thơ" giống engine thật: lỗi validate là `str(ValidationError)` của pydantic (có `input_value=...`),
lỗi unique bắt chước chi tiết của Postgres (`Key (so_cccd)=(...) already exists`). Mục tiêu spike: chứng minh
làn PII chặn được rò rỉ mà KHÔNG cần engine tự cẩn thận.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Callable

from pydantic import ValidationError, create_model

from .spec import CATALOG, FieldSpec, ModelSpec

_PY = {"text": str, "int": int, "money": int, "date": dt.date, "enum": str}


class EngineError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class Store:
    def __init__(self, conn: sqlite3.Connection, catalog: dict[str, ModelSpec] = CATALOG):
        self.c, self.catalog = conn, catalog
        self.on_row: Callable[[ModelSpec, dict], None] = lambda m, r: None   # làn PII gắn hook theo dõi hàng đã đọc
        for m in catalog.values():
            cols = ", ".join(f'"{f.name}" {"INTEGER" if f.type in ("int", "money") else "TEXT"}'
                             f'{" UNIQUE" if f.unique else ""}' for f in m.fields)
            self.c.execute(f'CREATE TABLE IF NOT EXISTS "{m.name}" (rev INTEGER NOT NULL DEFAULT 1, {cols})')

    def model(self, name: str) -> ModelSpec:
        m = self.catalog.get(name)
        if m is None:
            raise EngineError("UNKNOWN_MODEL", f"Model '{name}' không tồn tại")
        return m

    def _payload(self, m: ModelSpec, partial: bool):
        fs = {f.name: ((_PY[f.type] | None), None if partial or not f.required else ...) for f in m.fields}
        return create_model(f"{m.name}_payload", __config__={"extra": "forbid"}, **fs)

    def _validate(self, m: ModelSpec, data: dict, partial: bool) -> dict:
        try:
            obj = self._payload(m, partial).model_validate(data)
        except ValidationError as e:
            raise EngineError("VALIDATION_FAILED", str(e))      # str() của pydantic có input_value=...
        out = obj.model_dump(exclude_unset=True)
        return {k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in out.items()}

    def _row(self, m: ModelSpec, r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        self.on_row(m, d)
        return d

    def _unique_error(self, m: ModelSpec, data: dict, e: sqlite3.IntegrityError) -> EngineError:
        col = str(e).rsplit(".", 1)[-1]
        return EngineError("DUPLICATE", f'duplicate key value violates unique constraint "uq_{m.name}_{col}" '
                                        f'DETAIL: Key ({col})=({data.get(col)}) already exists.')

    def get(self, model: str, key: str) -> dict:
        m = self.model(model)
        self.c.row_factory = sqlite3.Row
        r = self._row(m, self.c.execute(f'SELECT * FROM "{m.name}" WHERE "{m.key_field}"=?', (key,)).fetchone())
        if r is None:
            raise EngineError("NOT_FOUND", f"Không tìm thấy {m.label} '{key}'")
        return r

    def search(self, model: str, filter: dict[str, Any] | None = None, sort: list[str] | None = None,
               limit: int = 50) -> list[dict]:
        m = self.model(model)
        where, params = [], []
        for k, cond in (filter or {}).items():
            f = m.field(k)
            if f is None:
                raise EngineError("UNKNOWN_FIELD", f"Trường '{k}' không tồn tại trong '{m.label}'")
            op, val = (cond.get("op", "eq"), cond.get("value")) if isinstance(cond, dict) else ("eq", cond)
            sqlop = {"eq": "=", "gt": ">", "lt": "<", "gte": ">=", "lte": "<=", "contains": "LIKE"}.get(op)
            if sqlop is None:
                raise EngineError("BAD_FILTER", f"op '{op}' không hỗ trợ (giá trị: {val!r})")
            where.append(f'"{k}" {sqlop} ?')
            params.append(f"%{val}%" if op == "contains" else val)
        order = ", ".join(f'"{s.lstrip("-")}" {"DESC" if s.startswith("-") else "ASC"}' for s in (sort or [])
                          if m.field(s.lstrip("-")))
        q = f'SELECT * FROM "{m.name}"' + (f" WHERE {' AND '.join(where)}" if where else "") \
            + (f" ORDER BY {order}" if order else "") + f" LIMIT {int(limit)}"
        self.c.row_factory = sqlite3.Row
        return [self._row(m, r) for r in self.c.execute(q, params).fetchall()]

    def create(self, model: str, data: dict) -> dict:
        m = self.model(model)
        clean = self._validate(m, data, partial=False)
        cols = list(clean)
        try:
            self.c.execute(f'INSERT INTO "{m.name}" ({", ".join(chr(34)+c+chr(34) for c in cols)}) '
                           f'VALUES ({", ".join("?" for _ in cols)})', [clean[c] for c in cols])
        except sqlite3.IntegrityError as e:
            raise self._unique_error(m, clean, e)
        return self.get(model, clean[m.key_field])

    def update(self, model: str, key: str, patch: dict) -> dict:
        m = self.model(model)
        self.get(model, key)
        clean = self._validate(m, patch, partial=True)
        if not clean:
            raise EngineError("EMPTY_PATCH", "Không có trường nào để cập nhật")
        sets = ", ".join(f'"{k}"=?' for k in clean)
        try:
            self.c.execute(f'UPDATE "{m.name}" SET {sets}, rev=rev+1 WHERE "{m.key_field}"=?', [*clean.values(), key])
        except sqlite3.IntegrityError as e:
            raise self._unique_error(m, clean, e)
        return self.get(model, key)


def field_of(m: ModelSpec, name: str) -> FieldSpec | None:
    return m.field(name)
