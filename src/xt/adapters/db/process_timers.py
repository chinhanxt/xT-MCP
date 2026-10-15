# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`TimerFireStore` trên DB-API 2.0 (psycopg 3 hoặc sqlite3; module này không import driver).

Khóa duy nhất (process_key, period_key) + `INSERT ... ON CONFLICT DO NOTHING`: nhiều worker quét
cùng lúc thì chỉ một transaction giành được kỳ (đã chạy thật: 8 worker trên Postgres 17). State
instance lưu cột `json` (giữ thứ tự key; không dùng `jsonb`, SSPIFF §3). `process_key` nên mang
tiền tố tenant khi nhiều tenant dùng chung bảng.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Literal, Protocol

Dialect = Literal["postgres", "sqlite"]

_DDL: dict[str, list[str]] = {
    "postgres": [
        """CREATE TABLE IF NOT EXISTS wf_timer_fire (
            process_key text NOT NULL, period_key text NOT NULL, status text NOT NULL,
            version integer NOT NULL, instance_id text, fired_at timestamptz NOT NULL,
            PRIMARY KEY (process_key, period_key))""",
        """CREATE TABLE IF NOT EXISTS wf_instance (
            id text PRIMARY KEY, process_key text NOT NULL, version integer NOT NULL,
            state json NOT NULL)""",
    ],
    "sqlite": [
        """CREATE TABLE IF NOT EXISTS wf_timer_fire (
            process_key TEXT NOT NULL, period_key TEXT NOT NULL, status TEXT NOT NULL,
            version INTEGER NOT NULL, instance_id TEXT, fired_at TEXT NOT NULL,
            PRIMARY KEY (process_key, period_key))""",
        """CREATE TABLE IF NOT EXISTS wf_instance (
            id TEXT PRIMARY KEY, process_key TEXT NOT NULL, version INTEGER NOT NULL,
            state TEXT NOT NULL)""",
    ],
}


class _Cursor(Protocol):
    rowcount: int

    def execute(self, sql: Any, params: Any = ..., /) -> Any: ...


class _Conn(Protocol):
    def cursor(self) -> Any: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class DbApiTimerFireStore:
    def __init__(self, conn: _Conn, dialect: Dialect):
        if dialect not in _DDL:
            raise ValueError(f"dialect {dialect!r} không hỗ trợ")
        self.conn = conn
        self.dialect: Dialect = dialect
        self.ph = "%s" if dialect == "postgres" else "?"

    def ensure_schema(self) -> None:
        cur = self.conn.cursor()
        for d in _DDL[self.dialect]:
            cur.execute(d)
        self.conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[_DbApiTx]:
        tx = _DbApiTx(self)
        try:
            yield tx
        except BaseException:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()


class _DbApiTx:
    def __init__(self, store: DbApiTimerFireStore):
        self.s = store
        self.cur: _Cursor = store.conn.cursor()

    def _q(self, sql: str) -> str:
        return sql.replace("?", self.s.ph)

    def _ts(self, t: datetime) -> Any:
        return t if self.s.dialect == "postgres" else t.isoformat()

    def claim_period(
        self, *, process_key: str, period_key: str, status: str, version: int, fired_at: datetime
    ) -> bool:
        self.cur.execute(
            self._q(
                "INSERT INTO wf_timer_fire(process_key, period_key, status, version, fired_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT (process_key, period_key) DO NOTHING"
            ),
            (process_key, period_key, status, version, self._ts(fired_at)),
        )
        return self.cur.rowcount == 1

    def save_instance(
        self, *, instance_id: str, process_key: str, version: int, state: Mapping[str, Any]
    ) -> None:
        self.cur.execute(
            self._q("INSERT INTO wf_instance(id, process_key, version, state) VALUES (?,?,?,?)"),
            (instance_id, process_key, version, json.dumps(dict(state), ensure_ascii=False)),
        )

    def link_instance(self, *, process_key: str, period_key: str, instance_id: str) -> None:
        self.cur.execute(
            self._q("UPDATE wf_timer_fire SET instance_id=? WHERE process_key=? AND period_key=?"),
            (instance_id, process_key, period_key),
        )
