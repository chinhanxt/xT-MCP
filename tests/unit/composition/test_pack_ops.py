# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Mọi gói trong `packages/` khai op có hiện thực trong registry của deployment (đúng tham số) và
compile được; checklist vòng đời sinh từ chính bước người của định nghĩa quy trình."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from xt.adapters.db.integration import ProcessDefinition
from xt.adapters.workflow.bpmn.spiff import SpiffProcessEngine
from xt.entrypoints.composition.pack_ops import OpInputError, _checklist, record_id
from xt.entrypoints.composition.packs import load_pack
from xt.entrypoints.composition.process import OPS, check_pack_ops

ROOT = Path(__file__).resolve().parents[3]
PACKS = sorted(p.parent for p in (ROOT / "packages").glob("*/pack.json"))
UNITS = {"hr", "it", "accounting", "manager", "chief_accountant"}


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.name)
def test_pack_ops_registered_and_processes_compile(pack: Path) -> None:
    lp = load_pack(pack)
    assert check_pack_ops(lp.manifest.allowed_ops) == []
    engine = SpiffProcessEngine()
    for spec in lp.processes:
        engine.compile(spec, lp.manifest.allowed_ops)


def test_registry_entries_well_formed() -> None:
    for name, (params, factory) in OPS.items():
        assert params and "idempotency_key" not in params and callable(factory), name


def _definition(pack: str, process: str) -> ProcessDefinition:
    lp = load_pack(ROOT / "packages" / pack)
    spec: dict[str, Any] = next(s for s in lp.processes if s["id"] == process)
    return ProcessDefinition(
        process_id=process, version=int(spec["version"]), spec=spec, spec_sha256="x",
        allowed_ops=lp.manifest.allowed_ops, active=True, pack=pack,
    )  # fmt: skip


def test_checklist_from_process_steps() -> None:
    on = _checklist(_definition("nhan-su-vong-doi", "employee_onboarding"), UNITS)
    assert sorted(u for u, _, _ in on) == ["accounting", "hr", "it", "manager"]
    assert {s for _, _, s in on} == {"hr", "it", "accounting", "manager"}
    off = _checklist(_definition("nhan-su-vong-doi", "employee_offboarding"), UNITS)
    # mỗi bước người (kể cả chief_acct_overdue có `when`) có đúng một dòng chủ thể, khóa = id bước
    assert [(u, s) for u, _, s in off] == [
        ("hr", "hr_confirm"), ("manager", "manager"), ("it", "it"), ("accounting", "accounting"),
        ("chief_accountant", "chief_acct_overdue"), ("hr", "hr_close"),
    ]  # fmt: skip


def test_record_id_validation() -> None:
    u = uuid.uuid4()
    assert record_id(str(u), "task") == u
    assert record_id(f"task:{u}", "task") == u
    with pytest.raises(OpInputError):
        record_id(f"employee:{u}", "task")
    with pytest.raises(OpInputError):
        record_id("'; DROP TABLE x; --", "task")


def test_sync_access_grants_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """Op chạy ngay sau bước `it`: người duyệt = actor của bước, người đề nghị = initiated_by."""
    from tests.unit.iam.test_role_grants import HR, IT, NEW, T_A, FakeStore, _actor
    from xt.entrypoints.composition import pack_ops

    case, emp = str(uuid.uuid4()), str(uuid.uuid4())
    grants: list[dict[str, Any]] = [
        {
            "id": "g1",
            "system": "keycloak",
            "role": "employee",
            "revoked_on": None,
            "created_by": str(HR),
        },
        {
            "id": "g2",
            "system": "keycloak",
            "role": "hr",
            "revoked_on": "2026-10-17",
            "created_by": str(HR),
        },
    ]
    rows = {"lifecycle_case": {"id": case, "employee": emp, "initiated_by": str(HR)},
            "employee": {"id": emp, "user_id": str(NEW), "emp_no": "NV-1"}}  # fmt: skip

    class FakeData:
        def __init__(self, pc: object) -> None: ...

        def get(self, model: str, rid: uuid.UUID) -> dict[str, Any]:
            return rows[model]

        def rows(self, model: str, where: object) -> list[dict[str, Any]]:
            return grants

    store = FakeStore(people={T_A.id: {HR, IT, NEW}})
    monkeypatch.setattr(pack_ops, "_Data", FakeData)
    pc: Any = SimpleNamespace(rt=SimpleNamespace(store=store), actor=_actor())
    op = pack_ops.sync_access_grants(pc, _definition("nhan-su-vong-doi", "employee_onboarding"))
    with pytest.raises(OpInputError):  # không có bước người đang hoàn thành -> fail closed
        op(case_id=case, allowed_roles="employee,hr")
    saved, grants[:] = list(grants), []  # nhân viên không có quyền SSO xT: no-op
    assert op(case_id=case, allowed_roles="employee") == dict.fromkeys(
        ("applied", "already", "revoked", "skipped"), 0
    )
    grants[:] = saved
    token = pack_ops.ACTING.set(("i1", "t1"))
    try:
        assert op(case_id=case, allowed_roles="employee,hr") == {
            "applied": 1, "already": 1, "revoked": 0, "skipped": 0,
        }  # fmt: skip
        assert store.effective(T_A, NEW) == {"employee"}
        assert op(case_id=case, allowed_roles="employee,hr")["already"] == 2
        with pytest.raises(OpInputError):
            op(case_id=case, allowed_roles="hr")  # employee ngoài allow-list
        with pytest.raises(OpInputError):
            op(case_id=case, allowed_roles="")
        # it_admin vừa tạo bản ghi grant vừa hoàn thành bước `it` -> tự duyệt, từ chối
        grants.append({"id": "g3", "system": "keycloak", "role": "hr", "revoked_on": None,
                       "created_by": str(IT)})  # fmt: skip
        with pytest.raises(OpInputError, match="người tạo"):
            op(case_id=case, allowed_roles="employee,hr")
        assert store.effective(T_A, NEW) == {"employee"}
    finally:
        pack_ops.ACTING.reset(token)
