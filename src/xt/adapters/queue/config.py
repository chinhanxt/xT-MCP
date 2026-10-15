# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Driver-specific connection validation with credential-safe diagnostics."""

from __future__ import annotations

from urllib.parse import urlsplit

from psycopg.conninfo import conninfo_to_dict
from psycopg.errors import InvalidSchemaName, UndefinedFunction, UndefinedTable


def validate_database_url(value: str, *, setting: str, role: str | None) -> str:
    """Validate effective libpq options without exposing the supplied URL.

    libpq applies URL query parameters after the authority, so inspecting only
    the URL username would allow a query parameter to override the login role.
    A ``None`` role denotes the separate operational migrator credential.
    """
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"postgres", "postgresql"}:
            raise ValueError
        parameters = conninfo_to_dict(value)
        if not parameters.get("dbname") or not parameters.get("user"):
            raise ValueError
    except Exception:
        raise ValueError(f"{setting} must be an explicit PostgreSQL URL") from None
    if role is not None and parameters["user"] != role:
        raise ValueError(f"{setting} must use the {role} login role")
    if role is None and parameters["user"] in {"xt_app", "xt_control"}:
        raise ValueError(f"{setting} must use a separate migrator login role")
    return value


def is_schema_error(error: BaseException) -> bool:
    """Recognize missing database objects, including connector-wrapped causes."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (InvalidSchemaName, UndefinedFunction, UndefinedTable)):
            return True
        current = current.__cause__
    return False
