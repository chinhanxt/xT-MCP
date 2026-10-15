# SPDX-License-Identifier: Apache-2.0
"""Vault token theo phiên: token tất định trong một phiên, vô nghĩa ở phiên khác.

- Token gắn bản ghi: ⟨NV-0042.so_cccd⟩ (mã nghiệp vụ + tên field; agent đọc được, tham chiếu được).
- Token tự do (bộ nhận dạng bắt được trong text): ⟨PII-3.phone⟩, cùng giá trị -> cùng số trong phiên.
Production: bảng Postgres có RLS theo tenant, cột value mã hóa bằng khóa tenant (OpenBao), TTL = vòng đời
phiên Keycloak (`sid`), xóa khi logout/hết hạn. Spike: sqlite.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass

TOKEN_RE = re.compile(r"⟨([A-Za-z0-9][A-Za-z0-9_\-]{0,63})\.([a-z][a-z0-9_]{0,63})⟩")


@dataclass(frozen=True)
class Entry:
    token: str
    level: str
    kind: str | None
    model: str | None
    record_key: str | None
    field: str | None
    value: object


class Vault:
    def __init__(self, conn: sqlite3.Connection):
        self.c = conn
        self.c.executescript("""
        CREATE TABLE IF NOT EXISTS pii_vault(
          session_key TEXT NOT NULL, token TEXT NOT NULL, level TEXT NOT NULL, kind TEXT,
          model TEXT, record_key TEXT, field TEXT, value_json TEXT NOT NULL, vhash TEXT NOT NULL,
          created_at REAL NOT NULL, PRIMARY KEY(session_key, token));
        CREATE INDEX IF NOT EXISTS pii_vault_vh ON pii_vault(session_key, vhash);
        """)

    @staticmethod
    def _vh(value: object) -> str:
        return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def root(model: str, record_key: object) -> str:
        """Gốc token = mã nghiệp vụ, TRỪ KHI mã đó trông như PII (SĐT/CCCD/email) hoặc không hợp cú pháp token:
        khi đó dùng gốc băm, nếu không token ⟨0912345678.ho_ten⟩ sẽ tự làm lộ giá trị."""
        from . import recognize
        k = str(record_key)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-]{0,63}", k) and not recognize.find(k) and sum(c.isdigit() for c in k) < 8:
            return k
        return "R-" + hashlib.sha256(f"{model}|{k}".encode()).hexdigest()[:10]

    def bind(self, sk: str, model: str, record_key: str, field: str, level: str, kind: str | None, value) -> str:
        token = f"⟨{self.root(model, record_key)}.{field}⟩"
        # Ghi đè để token luôn trỏ giá trị mới nhất mà phiên này đã thấy
        self.c.execute("INSERT OR REPLACE INTO pii_vault VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (sk, token, level, kind, model, record_key, field,
                        json.dumps(value, ensure_ascii=False), self._vh(value), time.time()))
        return token

    def free(self, sk: str, kind: str, level: str, value: str) -> str:
        vh = self._vh(value)
        row = self.c.execute("SELECT token FROM pii_vault WHERE session_key=? AND vhash=? AND record_key LIKE 'PII-%'",
                             (sk, vh)).fetchone()
        if row:
            return row[0]
        n = self.c.execute("SELECT COUNT(*) FROM pii_vault WHERE session_key=? AND record_key LIKE 'PII-%'",
                           (sk,)).fetchone()[0] + 1
        rk = f"PII-{n}"
        token = f"⟨{rk}.{kind}⟩"
        self.c.execute("INSERT INTO pii_vault VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (sk, token, level, kind, None, rk, None, json.dumps(value, ensure_ascii=False), vh, time.time()))
        return token

    def get(self, sk: str, token: str) -> Entry | None:
        r = self.c.execute("SELECT token, level, kind, model, record_key, field, value_json FROM pii_vault "
                           "WHERE session_key=? AND token=?", (sk, token)).fetchone()
        return None if r is None else Entry(r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6]))

    def all_values(self, sk: str) -> list[tuple[str, object]]:
        """(token, value) đã cấp trong phiên: dùng để quét lại mọi output (giá trị cũ vẫn bị che)."""
        return [(t, json.loads(v)) for t, v in
                self.c.execute("SELECT token, value_json FROM pii_vault WHERE session_key=?", (sk,))]

    def drop_session(self, sk: str) -> int:
        return self.c.execute("DELETE FROM pii_vault WHERE session_key=?", (sk,)).rowcount
