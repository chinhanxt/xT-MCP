# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Read-only collector safety tests; no database or network involved."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import psycopg
import pytest

MODULE_PATH = Path(__file__).resolve().parents[3] / "scripts/live/db.py"
spec = importlib.util.spec_from_file_location("xt_live_db_test", MODULE_PATH)
assert spec is not None and spec.loader is not None
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)

TENANT = UUID("00000000-0000-0000-0000-000000000001")
UNTIL = datetime.now(UTC)
SINCE = UNTIL - timedelta(hours=1)


def connection(monkeypatch: pytest.MonkeyPatch, batches: list[Any]) -> tuple[Any, Any, Any]:
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchmany.side_effect = batches
    connect = MagicMock(return_value=conn)
    monkeypatch.setattr(db.psycopg, "connect", connect)

    def execute(*args: Any, **kwargs: Any) -> None:
        assert conn.read_only is True
        assert conn.isolation_level == psycopg.IsolationLevel.REPEATABLE_READ

    cur.execute.side_effect = execute
    return conn, cur, connect


def initial(*, bypass: bool = True) -> list[Any]:
    return [[{"readonly": "on"}], [{"seq": 1, "id": TENANT}], [{"bypass": bypass}]]


def test_readonly_startup_and_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    conn, cur, connect = connection(
        monkeypatch, [*initial(), [{"n": 0, "first_bad_seq": None}], []]
    )
    malicious = (
        "host=localhost options='-c default_transaction_read_only=off -c statement_timeout=0'"
    )
    result = db.collect({}, "demo", SINCE, UNTIL, malicious)
    options = connect.call_args.kwargs["options"]
    assert "default_transaction_read_only=on" in options
    assert "statement_timeout=15000" in options
    assert "off" not in options
    assert connect.call_args.kwargs["autocommit"] is False
    assert result["tenant_id"] == str(TENANT)
    assert datetime.fromisoformat(result["captured_at"]) >= UNTIL
    assert all("set_context" not in str(call.args[0]) for call in cur.execute.call_args_list)
    alias_call = cur.execute.call_args_list[1]
    assert alias_call.args == ("SELECT seq,id FROM xt.tenant_by_alias(%s)", ("demo",))
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


@pytest.mark.parametrize("rows", [[{"readonly": "off"}], []])
def test_failed_setting_rolls_back(monkeypatch: pytest.MonkeyPatch, rows: list[Any]) -> None:
    conn, _, _ = connection(monkeypatch, [rows])
    with pytest.raises(db.CollectionError):
        db.collect({}, "demo", SINCE, UNTIL, "dsn")
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


@pytest.mark.parametrize("access", [[{"bypass": False, "tenant": TENANT}], []])
def test_restricted_identity_aborts_before_records(
    monkeypatch: pytest.MonkeyPatch, access: list[Any]
) -> None:
    conn, cur, _ = connection(monkeypatch, [*initial()[:2], access])
    with pytest.raises(db.CollectionError, match="administrative"):
        db.collect(
            {"records": [{"label": "r", "model": "stock_request", "match": {"code": "x"}}]},
            "demo",
            SINCE,
            UNTIL,
            "dsn",
        )
    assert len(cur.execute.call_args_list) == 3
    conn.rollback.assert_called_once()


def test_database_error_sanitized_and_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    conn, cur, _ = connection(monkeypatch, [])
    cur.execute.side_effect = psycopg.OperationalError("SECRET credential")
    with pytest.raises(db.CollectionError, match="collection failed") as caught:
        db.collect({}, "demo", SINCE, UNTIL, "dsn")
    assert "SECRET" not in str(caught.value)
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


def test_invalid_alias_never_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, connect = connection(monkeypatch, [])
    with pytest.raises(db.CollectionError, match="alias"):
        db.collect({}, "demo'; DROP TABLE anything; --", SINCE, UNTIL, "dsn")
    connect.assert_not_called()


def test_overflow_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    conn, _, _ = connection(monkeypatch, [*initial(), [{"id": TENANT}] * 10001])
    with pytest.raises(db.CollectionError, match="limit"):
        db.collect(
            {"records": [{"label": "r", "model": "stock_request", "match": {"code": "x"}}]},
            "demo",
            SINCE,
            UNTIL,
            "dsn",
        )
    conn.rollback.assert_called_once()


def test_foreign_evidence_is_exact_and_readonly(monkeypatch: pytest.MonkeyPatch) -> None:
    other = UUID("00000000-0000-0000-0000-000000000002")
    rid = UUID("00000000-0000-0000-0000-000000000003")
    conn, cur, _ = connection(
        monkeypatch,
        [
            *initial(),
            [{"n": 0, "first_bad_seq": None}],
            [],
            [{"seq": 2, "id": other}],
            [{"id": rid, "revision": 7}],
            [{"changes": 0}],
        ],
    )
    plan = {"foreign": {"tenant": "hoamai", "ref": f"stock_request:{rid}", "revision": 7}}
    result = db.collect(plan, "demo", SINCE, UNTIL, "dsn")
    assert result["foreign"] == {
        "tenant": "hoamai",
        "tenant_id": str(other),
        "ref": f"stock_request:{rid}",
        "records": [{"id": str(rid), "revision": 7}],
        "changes": 0,
    }
    calls = cur.execute.call_args_list
    assert calls[-3].args[1] == ("hoamai",)
    assert calls[-2].args[0].as_string() == (
        'SELECT id,revision FROM "xt_t2"."stock_request" WHERE id=%s LIMIT 10001'
    )
    assert calls[-2].args[1] == (rid,)
    assert calls[-1].args[1] == (other, "stock_request", rid, SINCE, UNTIL)
    conn.rollback.assert_called_once()


@pytest.mark.parametrize(
    "foreign",
    [
        {"tenant": "demo", "ref": f"stock_request:{TENANT}"},
        {"tenant": "x'; DROP TABLE x; --", "ref": f"stock_request:{TENANT}"},
        {"tenant": "hoamai", "ref": f"stock_request;DROP:{TENANT}"},
        {"tenant": "hoamai", "ref": "stock_request:bad-uuid"},
    ],
)
def test_bad_foreign_input_never_selects_foreign(
    monkeypatch: pytest.MonkeyPatch,
    foreign: dict[str, Any],
) -> None:
    conn, cur, _ = connection(
        monkeypatch,
        [
            *initial(),
            [{"n": 0, "first_bad_seq": None}],
            [],
        ],
    )
    with pytest.raises(db.CollectionError):
        db.collect({"foreign": foreign}, "demo", SINCE, UNTIL, "dsn")
    assert len(cur.execute.call_args_list) == 5
    conn.rollback.assert_called_once()


def test_record_audit_covers_deleted_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    event = {"record_id": TENANT, "model": "note", "op": "DELETE", "seq": 9, "at": SINCE}
    conn, cur, _ = connection(
        monkeypatch,
        [
            *initial(),
            [],
            [event],
            [{"n": 1, "first_bad_seq": None}],
            [],
        ],
    )
    result = db.collect(
        {
            "records": [
                {
                    "label": "deleted",
                    "model": "note",
                    "match": {"code": "marker"},
                    "in_window": True,
                }
            ]
        },
        "demo",
        SINCE,
        UNTIL,
        "dsn",
    )
    assert result["records"]["deleted"] == []
    assert result["record_audit"]["deleted"][0]["op"] == "DELETE"
    call = cur.execute.call_args_list[4]
    assert "diff->'new' @> %s::jsonb OR diff->'old' @> %s::jsonb" in call.args[0]
    assert "at>=" not in call.args[0]
    assert call.args[1][2].obj == {"code": "marker"}
    assert "diff" not in result["record_audit"]["deleted"][0]
    conn.rollback.assert_called_once()


def test_pii_collects_whole_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    _, cur, _ = connection(
        monkeypatch,
        [
            *initial(),
            [],
            [{"n": 0, "first_bad_seq": None}],
            [],
        ],
    )
    db.collect({"pii": {"trace_id": "trace", "tool": "get_record"}}, "demo", SINCE, UNTIL, "dsn")
    call = cur.execute.call_args_list[3]
    assert "client_class" in call.args[0]
    assert "tool=%s" not in call.args[0]
    assert call.args[1] == (TENANT, "trace", SINCE, UNTIL)


def test_stock_changes_use_only_numeric_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    _, cur, _ = connection(
        monkeypatch,
        [
            *initial(),
            [{"id": TENANT}],
            [],
            [{"id": TENANT}],
            [],
            [{"before": 2000, "after": 1000}],
            [{"before": 2000, "after": 1000}],
            [],
            [{"n": 2, "first_bad_seq": None}],
            [],
        ],
    )
    result = db.collect(
        {
            "records": [
                {"label": "b", "model": "stock_balance", "match": {"key": "k"}},
                {"label": "l", "model": "stock_level", "match": {"key": "k"}},
            ],
            "warehouse": {
                "approver": str(TENANT),
                "requester": str(TENANT),
                "balances": [{"balance": "b", "level": "l"}],
            },
        },
        "demo",
        SINCE,
        UNTIL,
        "dsn",
    )
    assert result["stock_changes"] == [
        {"before": 2000, "after": 1000},
        {"before": 2000, "after": 1000},
    ]
    calls = cur.execute.call_args_list
    for index, field in ((7, "quantity_milli"), (8, "on_hand_milli")):
        assert calls[index].args[1][0:2] == (field, field)
        assert calls[index].args[1][-1] == field
        assert "diff->'old'->%s AS before,diff->'new'->%s AS after" in calls[index].args[0]
