# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Synthetic offline evidence checks; these tests certify no live deployment."""

import importlib.util
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

MODULE_PATH = Path(__file__).resolve().parents[3] / "scripts/live/verify.py"
spec = importlib.util.spec_from_file_location("xt_live_verify_test", MODULE_PATH)
assert spec is not None and spec.loader is not None
verify = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = verify
spec.loader.exec_module(verify)


@pytest.fixture
def evidence() -> Any:
    return json.loads((Path(__file__).parent / "fixtures/demo.json").read_text())


def evaluate(evidence: Any) -> Any:
    snapshot = evidence["snapshot"]
    return verify.evaluate(
        verify.validate_plan(evidence["plan"]),
        snapshot,
        verify.parse_time(snapshot["since"]),
        verify.parse_time(snapshot["until"]),
    )


def assert_rejected(evidence: Any) -> None:
    try:
        checks = evaluate(evidence)
    except ValueError:
        return
    assert checks and any(not check.passed for check in checks)


def test_complete_synthetic_evidence(evidence: Any) -> None:
    checks = evaluate(evidence)
    assert checks and all(check.passed for check in checks), checks
    assert {check.name for check in checks} >= {
        "record/issue",
        "record/draft",
        "record/disabled_probe",
        "operation/0",
        "warehouse/movements",
        "warehouse/decision",
        "warehouse/workflow",
        "audit/chain",
        "audit/issue",
        "audit/draft",
        "disabled/app",
        "pii/egress",
    }


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-18T10:00:00Z",
        "2026-10-18T10:00:00+00:00",
        "2026-10-18T17:00:00+07:00",
    ],
)
def test_timezone_equivalence(timestamp: Any) -> None:
    assert verify.parse_time(timestamp) == datetime(2026, 10, 18, 10, tzinfo=UTC)
    assert verify.parse_time(timestamp).tzinfo is UTC


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-18T10:00:00",
        "2026-10-18",
        "2026-02-30T10:00:00Z",
        "",
        "not-a-date",
    ],
)
def test_invalid_or_naive_time(timestamp: Any) -> None:
    with pytest.raises(ValueError):
        verify.parse_time(timestamp)


@pytest.mark.parametrize("field", ["records", "operations", "warehouse", "disabled_pack", "pii"])
def test_plan_missing_required_field(evidence: Any, field: Any) -> None:
    del evidence["plan"][field]
    with pytest.raises(ValueError):
        verify.validate_plan(evidence["plan"])


@pytest.mark.parametrize(
    "field",
    [
        "records",
        "operations",
        "movements",
        "decisions",
        "workflow",
        "packs",
        "pii",
        "audit_status",
        "audit",
    ],
)
def test_snapshot_missing_collection_fails_closed(evidence: Any, field: Any) -> None:
    del evidence["snapshot"][field]
    assert_rejected(evidence)


@pytest.mark.parametrize("collection", ["operations", "movements", "decisions", "workflow", "pii"])
def test_missing_required_evidence(evidence: Any, collection: Any) -> None:
    evidence["snapshot"][collection] = []
    assert_rejected(evidence)


@pytest.mark.parametrize("collection", ["operations", "movements", "decisions"])
def test_duplicate_side_effect(evidence: Any, collection: Any) -> None:
    rows = evidence["snapshot"][collection]
    rows.append(deepcopy(rows[0]))
    assert_rejected(evidence)


def test_duplicate_record(evidence: Any) -> None:
    rows = evidence["snapshot"]["records"]["issue"]
    rows.append({**rows[0], "id": "00000000-0000-4000-8000-000000000099"})
    assert_rejected(evidence)


@pytest.mark.parametrize(
    ("collection", "field", "value"),
    [
        ("operations", "actor_key", "foreign-actor"),
        ("operations", "idempotency_key", "another-run"),
        ("operations", "state", "pending"),
        ("operations", "receipt", {"ref": "stock_request:00000000-0000-4000-8000-000000000099"}),
        ("movements", "quantity_milli", -999),
        ("movements", "key", "wrong-line-key"),
        ("movements", "request", "00000000-0000-4000-8000-000000000099"),
        ("movements", "approved_by", "00000000-0000-4000-8000-000000000003"),
        ("movements", "posted_at", "2026-10-17T10:30:00Z"),
        ("decisions", "approver", "00000000-0000-4000-8000-000000000003"),
        ("decisions", "confirmed_by", ["someone-else"]),
        ("decisions", "decided_at", "2026-10-17T10:30:00Z"),
        ("workflow", "status", "running"),
        ("workflow", "candidates", ["someone"]),
        ("packs", "status", "enabled"),
        ("packs", "models", ["another_model"]),
        ("pii", "trace_id", "tr_another_run"),
        ("pii", "mode", "full"),
        ("pii", "fields", []),
        ("pii", "at", "2026-10-17T10:30:00Z"),
    ],
)
def test_mutated_evidence_is_not_success(
    evidence: Any, collection: Any, field: Any, value: Any
) -> None:
    evidence["snapshot"][collection][0][field] = value
    assert_rejected(evidence)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "draft"),
        ("code", "foreign-run"),
        ("created_at", "2026-10-17T10:30:00Z"),
    ],
)
def test_wrong_record_or_old_record(evidence: Any, field: Any, value: Any) -> None:
    evidence["snapshot"]["records"]["issue"][0][field] = value
    assert_rejected(evidence)


@pytest.mark.parametrize("status", [{"n": 0, "first_bad_seq": None}, {"n": 10, "first_bad_seq": 2}])
def test_empty_or_tampered_audit(evidence: Any, status: Any) -> None:
    evidence["snapshot"]["audit_status"] = status
    assert_rejected(evidence)


def test_missing_audit_mutation(evidence: Any) -> None:
    evidence["snapshot"]["record_audit"]["issue"].pop(0)
    assert_rejected(evidence)


def test_unexpected_audit_mutation(evidence: Any) -> None:
    evidence["snapshot"]["record_audit"]["issue"].append(
        deepcopy(evidence["snapshot"]["record_audit"]["issue"][0])
    )
    assert_rejected(evidence)


def test_new_records_must_carry_run_marker(evidence: Any) -> None:
    evidence["plan"]["records"][0]["match"] = {"code": "unrelated"}
    with pytest.raises(ValueError):
        verify.validate_plan(evidence["plan"])


@pytest.mark.parametrize("identifier", ["stock_request;DROP TABLE x", "schema.table", "../x"])
def test_unsafe_model_identifier(evidence: Any, identifier: Any) -> None:
    evidence["plan"]["records"][0]["model"] = identifier
    with pytest.raises(ValueError):
        verify.validate_plan(evidence["plan"])


@pytest.mark.parametrize("field", ["before", "level_before"])
def test_incorrect_warehouse_baseline(evidence: Any, field: Any) -> None:
    evidence["plan"]["warehouse"]["balances"][0][field] = 10001
    assert_rejected(evidence)


@pytest.mark.parametrize(
    ("label", "field"),
    [
        ("balance", "quantity_milli"),
        ("level", "on_hand_milli"),
    ],
)
def test_incorrect_final_stock(evidence: Any, label: Any, field: Any) -> None:
    evidence["snapshot"]["records"][label][0][field] = 9001
    assert_rejected(evidence)


@pytest.mark.parametrize("field", ["records", "operations"])
def test_empty_required_plan_collection(evidence: Any, field: Any) -> None:
    evidence["plan"][field] = []
    with pytest.raises(ValueError):
        verify.validate_plan(evidence["plan"])


@pytest.mark.parametrize(
    "field",
    [
        "request",
        "requester",
        "approver",
        "approver_subject",
        "lines",
        "balances",
    ],
)
def test_missing_warehouse_contract(evidence: Any, field: Any) -> None:
    del evidence["plan"]["warehouse"][field]
    with pytest.raises(ValueError):
        verify.validate_plan(evidence["plan"])


@pytest.mark.parametrize("field", ["lines", "balances"])
def test_empty_warehouse_contract(evidence: Any, field: Any) -> None:
    evidence["plan"]["warehouse"][field] = []
    with pytest.raises(ValueError):
        verify.validate_plan(evidence["plan"])


@pytest.mark.parametrize("run_id", ["../unsafe", "run;DELETE", "", "unicode-ñ"])
def test_unsafe_run_identifier(evidence: Any, run_id: Any) -> None:
    evidence["plan"]["run_id"] = run_id
    with pytest.raises(ValueError):
        verify.validate_plan(evidence["plan"])


def cli_args(evidence: Any, tmp_path: Any) -> list[str]:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(evidence["plan"]))
    return [
        "--plan",
        str(plan),
        "--tenant",
        "synthetic-demo",
        "--since",
        evidence["snapshot"]["since"],
        "--until",
        evidence["snapshot"]["until"],
    ]


@pytest.fixture
def no_network(monkeypatch: Any) -> list[str]:
    import socket

    attempts: list[str] = []

    def blocked(*args: Any, **kwargs: Any) -> Any:
        attempts.append("network")
        raise AssertionError("Offline unit test attempted network")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    return attempts


@pytest.mark.parametrize("flag", ["--sql", "--write"])
def test_cli_rejects_write_or_sql_flags(
    evidence: Any,
    tmp_path: Any,
    no_network: Any,
    flag: Any,
) -> None:
    with pytest.raises(SystemExit) as exc:
        verify.main([*cli_args(evidence, tmp_path), flag])
    assert exc.value.code == 2
    assert not no_network


def test_cli_missing_dsn_never_connects(
    evidence: Any,
    tmp_path: Any,
    monkeypatch: Any,
    no_network: Any,
    capsys: Any,
) -> None:
    monkeypatch.delenv("XT_LIVE_VERIFY_DSN", raising=False)
    assert verify.main(cli_args(evidence, tmp_path)) == 2
    assert not no_network
    assert "FAIL" in capsys.readouterr().err


def test_cli_missing_snapshot_never_connects(
    evidence: Any,
    tmp_path: Any,
    no_network: Any,
) -> None:
    args = [*cli_args(evidence, tmp_path), "--snapshot", str(tmp_path / "missing.json")]
    assert verify.main(args) == 2
    assert not no_network


def test_cli_offline_fixture_is_explicitly_labeled(
    evidence: Any,
    tmp_path: Any,
    no_network: Any,
    capsys: Any,
) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(evidence["snapshot"]))
    assert verify.main([*cli_args(evidence, tmp_path), "--snapshot", str(snapshot)]) == 0
    assert not no_network
    assert "OFFLINE / FIXTURE" in capsys.readouterr().out


@pytest.mark.parametrize(
    "field", ["record_audit", "stock_changes", "foreign", "people", "captured_at"]
)
def test_missing_additional_snapshot_contract(evidence: Any, field: Any) -> None:
    del evidence["snapshot"][field]
    assert_rejected(evidence)


@pytest.mark.parametrize("collection", ["movements", "decisions"])
def test_self_probe_cannot_post_or_decide(evidence: Any, collection: Any) -> None:
    row = deepcopy(evidence["snapshot"][collection][0])
    self_id = evidence["snapshot"]["records"]["self"][0]["id"]
    row["request" if collection == "movements" else "ref"] = (
        self_id if collection == "movements" else "stock_request:" + self_id
    )
    evidence["snapshot"][collection].append(row)
    assert_rejected(evidence)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant", "synthetic-demo"),
        ("tenant_id", "00000000-0000-4000-8000-000000000008"),
        ("changes", 1),
        ("records", []),
        ("records", [{"id": "00000000-0000-4000-8000-000000000099", "revision": 2}]),
        ("ref", "stock_request:00000000-0000-4000-8000-000000000001"),
    ],
)
def test_foreign_baseline_must_match(evidence: Any, field: Any, value: Any) -> None:
    evidence["snapshot"]["foreign"][field] = value
    assert_rejected(evidence)


def test_deleted_duplicate_is_still_counted(evidence: Any) -> None:
    events = evidence["snapshot"]["record_audit"]["issue"]
    events.append({**events[0], "record_id": "00000000-0000-4000-8000-000000000088"})
    assert_rejected(evidence)


def test_marker_audit_outside_window_rejected(evidence: Any) -> None:
    evidence["snapshot"]["record_audit"]["issue"][0]["at"] = "2026-10-17T10:30:00Z"
    assert_rejected(evidence)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("before", 9999),
        ("after", 9001),
        ("op", "INSERT"),
        ("at", "2026-10-17T10:30:00Z"),
    ],
)
def test_stock_audit_baseline_cannot_be_asserted_without_proof(
    evidence: Any,
    field: Any,
    value: Any,
) -> None:
    evidence["snapshot"]["stock_changes"][0][field] = value
    assert_rejected(evidence)


def test_duplicate_stock_change_rejected(evidence: Any) -> None:
    evidence["snapshot"]["stock_changes"].append(deepcopy(evidence["snapshot"]["stock_changes"][0]))
    assert_rejected(evidence)


def test_other_tool_unmasked_egress_is_failure(evidence: Any) -> None:
    evidence["snapshot"]["pii"].append(
        {
            **evidence["snapshot"]["pii"][0],
            "tool": "lookup_person",
            "mode": "full",
        }
    )
    assert_rejected(evidence)


def test_noncloud_egress_does_not_certify_cloud_masking(evidence: Any) -> None:
    evidence["snapshot"]["pii"][0]["client_class"] = "local"
    assert_rejected(evidence)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("commit_state", "pending"),
        ("committed_at", "2026-10-17T10:30:00Z"),
    ],
)
def test_operation_receipt_must_be_committed_in_window(
    evidence: Any,
    field: Any,
    value: Any,
) -> None:
    evidence["snapshot"]["operations"][0]["receipt"][field] = value
    assert_rejected(evidence)


def test_numeric_boolean_does_not_satisfy_revision(evidence: Any) -> None:
    evidence["snapshot"]["records"]["draft"][0]["revision"] = True
    assert_rejected(evidence)
