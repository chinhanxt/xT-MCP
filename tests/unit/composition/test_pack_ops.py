# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Mọi gói trong `packages/` khai op có hiện thực trong registry của deployment (đúng tham số) và
compile được; checklist vòng đời sinh từ chính bước người của định nghĩa quy trình."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from xt.adapters.db.integration import ProcessDefinition
from xt.adapters.workflow.bpmn.spiff import SpiffProcessEngine
from xt.entrypoints.composition.pack_ops import OpInputError, _checklist, record_id
from xt.entrypoints.composition.packs import load_pack
from xt.entrypoints.composition.process import OPS, check_pack_ops

ROOT = Path(__file__).resolve().parents[3]
PACKS = sorted(p.parent for p in (ROOT / "packages").glob("*/pack.json"))
UNITS = {"hr", "it", "accounting", "manager"}


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
    assert sorted(u for u, _ in on) == ["accounting", "hr", "it", "manager"]
    off = _checklist(_definition("nhan-su-vong-doi", "employee_offboarding"), UNITS)
    # chief_acct_overdue có `when` -> không vào checklist cố định
    assert [u for u, _ in off] == ["hr", "manager", "it", "accounting", "hr"]


def test_record_id_validation() -> None:
    u = uuid.uuid4()
    assert record_id(str(u), "task") == u
    assert record_id(f"task:{u}", "task") == u
    with pytest.raises(OpInputError):
        record_id(f"employee:{u}", "task")
    with pytest.raises(OpInputError):
        record_id("'; DROP TABLE x; --", "task")
