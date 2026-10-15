# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Statement guard for worker tenant connections while a job handler runs.

Defense in depth, not a sandbox (M2): handlers are first-party code loaded by
the operator. The dispatcher owns the transaction, the tenant context and the
per-tenant job role; this guard stops a handler *bug* from ending the
transaction (AQ-10) or switching role/tenant/search_path (``SET/RESET ROLE``,
``SET SESSION AUTHORIZATION``, ``set_config('xt.*'|'role'|'search_path')``,
``xt.set_context``). Handlers may still elevate to ``xt_workflow``
(architecture §5.3) and use savepoints. Dynamic SQL (``DO``/``EXECUTE``) and
``connection.pgconn`` are not inspected; the dispatcher re-pins role, tenant
and search_path after the handler and the pool's ``DISCARD ALL`` is the
session backstop.
"""

from __future__ import annotations

import re
import weakref
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import contextmanager
from typing import Any, Self

from psycopg import AsyncConnection, AsyncCursor, sql
from psycopg.abc import Params, Query
from psycopg.rows import Row


class TransactionControlError(RuntimeError):
    """A handler tried to end the worker-owned transaction (AQ-10)."""


class SessionControlError(RuntimeError):
    """A handler tried to change the role/tenant context the dispatcher pinned."""


# One left-to-right tokenizer: whichever of comment / literal / quoted
# identifier starts first wins, so "/*" inside a string or "'" inside a comment
# cannot hide code. Dollar quotes and E-strings only start where the previous
# character is not part of a word or a dollar tag (L4).
_TOKENS = re.compile(
    r"(?P<comment>--[^\n]*|/\*.*?\*/)"
    r"|(?P<literal>(?<![\w$])[eE]'(?:[^'\\]|\\.|'')*'|'(?:[^']|'')*'"
    r"|(?<![\w$])\$\$.*?\$\$|(?<![\w$])\$([A-Za-z_][A-Za-z_0-9]*)\$.*?\$\3\$)"
    r"|(?P<uident>[uU]&\"(?:[^\"]|\"\")*\")"
    r"|(?P<ident>\"(?:[^\"]|\"\")*\")",
    re.S,
)
_ENDS_TRANSACTION = re.compile(
    r"\s*(?:COMMIT|END|ABORT|PREPARE\s+TRANSACTION"
    r"|ROLLBACK(?!\s+(?:WORK\s+|TRANSACTION\s+)?TO\b))\b",
    re.I,
)
# SET [SESSION|LOCAL] ROLE / SESSION AUTHORIZATION / search_path (SCHEMA) /
# xt.* GUCs, RESET of the same. The single allowed elevation is the workflow
# transition role. An escaped unicode identifier (U&"...") cannot be judged.
_SESSION_CONTROL = re.compile(
    r"\s*(?:SET\s+(?:SESSION\s+|LOCAL\s+)?"
    r"(?:ROLE|SESSION\s+AUTHORIZATION|SEARCH_PATH\b|SCHEMA\b|XT\.)"
    r"|RESET\s+(?:ROLE|ALL|SESSION\s+AUTHORIZATION|SEARCH_PATH\b|XT\.))"
    r"|.*\bU&\"",
    re.I | re.S,
)
_WORKFLOW_ELEVATION = re.compile(r"\s*SET\s+LOCAL\s+ROLE\s+xt_workflow\s*$", re.I)
# Owner decision (Vòng 2b): handlers never need dynamic SQL or their own
# routines. DO bodies and routine definitions are opaque to statement
# inspection (B12/B13), so they are rejected outright, as is any CREATE that
# names a temporary schema.
_DYNAMIC_CODE = re.compile(
    r"\s*(?:DO\b"
    r"|(?:CREATE|ALTER)\s+(?:OR\s+REPLACE\s+)?(?:(?:TEMP|TEMPORARY)\s+)?"
    r"(?:FUNCTION|PROCEDURE|ROUTINE|AGGREGATE|TRIGGER|EVENT\s+TRIGGER|RULE)\b"
    r"|CREATE\b.*\bpg_(?:toast_)?temp(?:_\d+)?\s*\.)",
    re.I | re.S,
)
# set_config() on the tenant GUCs, role/authorization or search_path, and the
# model-engine context setter (AUD-13). A name that is not a plain literal
# (parameter, E-/U&-string, dollar quote, expression) cannot be judged and is
# rejected as well. Matched on the text with comments removed but literals kept.
_SET_CONFIG = re.compile(
    r"\bset_config\"?\s*\(\s*(?!'(?!\s*(?:xt\.|role'|session_authorization'|search_path'))"
    r"[^']*'\s*,)"
    r"|\bset_context\"?\s*\(",
    re.I,
)

_guarded: weakref.WeakSet[AsyncConnection[Any]] = weakref.WeakSet()


def _code(match: re.Match[str]) -> str:
    # Literals are blanked so their ';' cannot split statements. A quoted
    # identifier keeps its name (minus quotes) so SET ROLE "x" / SET "xt.t" are
    # judged like the unquoted forms; separators inside it cannot split.
    if match.group("ident"):
        return re.sub(r"[\s;'\"$]", "_", match.group("ident")[1:-1]) or "_"
    if match.group("uident"):
        return ' U&" '
    return " "


def _uncommented(match: re.Match[str]) -> str:
    return " " if match.group("comment") else match.group(0)


def _statements(text: str) -> list[str]:
    return _TOKENS.sub(_code, text).split(";")


def ends_transaction(text: str) -> bool:
    """True when any statement in ``text`` would commit/abort the transaction."""
    return any(_ENDS_TRANSACTION.match(part) for part in _statements(text))


def runs_dynamic_code(text: str) -> bool:
    """True for ``DO``, routine definitions and CREATE in a temporary schema."""
    return any(_DYNAMIC_CODE.match(part) for part in _statements(text))


def changes_session_context(text: str) -> bool:
    """True when ``text`` switches role/authorization/search_path or the tenant context."""
    if _SET_CONFIG.search(_TOKENS.sub(_uncommented, text)):
        return True
    return any(
        _SESSION_CONTROL.match(part) and not _WORKFLOW_ELEVATION.match(part)
        for part in _statements(text)
    )


def _query_text(query: Any, connection: AsyncConnection[Any]) -> str:
    if isinstance(query, bytes):
        return query.decode(errors="replace")
    if isinstance(query, str):
        return query
    if isinstance(query, sql.Composable):
        return query.as_string(connection)
    return "".join(getattr(query, "strings", ()))  # PEP 750 template literal parts


@contextmanager
def handler_guard(connection: AsyncConnection[Any]) -> Iterator[None]:
    """Enforce the guard on ``connection`` while a handler runs."""
    _guarded.add(connection)
    try:
        yield
    finally:
        _guarded.discard(connection)


@contextmanager
def guard_suspended(connection: AsyncConnection[Any]) -> Iterator[None]:
    """Trusted adapter code (queue bookkeeping) issuing its own role switches."""
    active = connection in _guarded
    _guarded.discard(connection)
    try:
        yield
    finally:
        if active:
            _guarded.add(connection)


class GuardedCursor(AsyncCursor[Row]):
    """Worker tenant cursor; checks statements only inside ``handler_guard``.

    psycopg's own ``connection.transaction()`` does not go through cursors, so
    savepoints remain available to handlers.
    """

    __slots__ = ()

    def _guard(self, query: Query) -> None:
        if self.connection not in _guarded:
            return
        text = _query_text(query, self.connection)
        if ends_transaction(text):
            raise TransactionControlError("job handlers must not end the worker transaction")
        if runs_dynamic_code(text):
            raise SessionControlError("job handlers must not run DO blocks or define routines")
        if changes_session_context(text):
            raise SessionControlError("job handlers must not change the pinned role/tenant")

    async def execute(
        self,
        query: Query,
        params: Params | None = None,
        *,
        prepare: bool | None = None,
        binary: bool | None = None,
    ) -> Self:
        self._guard(query)
        base: Any = super()
        return await base.execute(query, params, prepare=prepare, binary=binary)  # type: ignore[no-any-return]

    async def executemany(
        self, query: Query, params_seq: Iterable[Params], *, returning: bool = False
    ) -> None:
        self._guard(query)
        await super().executemany(query, params_seq, returning=returning)

    async def stream(
        self,
        query: Query,
        params: Params | None = None,
        *,
        binary: bool | None = None,
        size: int = 1,
    ) -> AsyncIterator[Row]:
        self._guard(query)
        async for row in super().stream(query, params, binary=binary, size=size):
            yield row
