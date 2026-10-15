# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Bản in-memory của các port (test, demo, CLI). Không chứa dữ liệu tổ chức nào: dữ liệu do người
gọi nạp vào. Production: org đọc `org_unit/position` (architecture-v2 §6.2), instance ở Postgres."""

from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from xt.application.process.ports import InstanceRecord
from xt.domain.process.assignment import OrgUser


class InMemoryOrgDirectory:
    """`users`: id -> {active, department, roles, manager}; `departments`: key -> {head}."""

    def __init__(
        self, users: Mapping[str, Mapping[str, Any]], departments: Mapping[str, Mapping[str, Any]]
    ):
        self.users: dict[str, dict[str, Any]] = {k: dict(v) for k, v in users.items()}
        self.departments: dict[str, dict[str, Any]] = {k: dict(v) for k, v in departments.items()}

    def get_user(self, uid: object) -> OrgUser | None:
        u = self.users.get(uid) if isinstance(uid, str) else None
        if u is None:
            return None
        return OrgUser(
            id=uid,  # type: ignore[arg-type]
            active=bool(u.get("active", False)),
            department=u.get("department"),
            roles=tuple(u.get("roles", ())),
            manager=u.get("manager"),
        )

    def department_head(self, dept: str) -> str | None:
        d = self.departments.get(dept)
        head = d.get("head") if d else None
        return head if isinstance(head, str) else None

    def users_with_role(self, role: str, dept: str | None = None) -> list[str]:
        return sorted(
            uid
            for uid, u in self.users.items()
            if role in u.get("roles", ()) and (dept is None or u.get("department") == dept)
        )

    def department_exists(self, dept: object) -> bool:
        return isinstance(dept, str) and dept in self.departments


class InMemoryInstanceStore:
    def __init__(self) -> None:
        self.records: dict[str, InstanceRecord] = {}

    def insert(self, instance_id: str, record: InstanceRecord) -> None:
        if instance_id in self.records:
            raise KeyError(f"instance {instance_id} đã tồn tại")
        self.records[instance_id] = copy.deepcopy(record)

    def get_for_update(self, instance_id: str) -> InstanceRecord | None:
        r = self.records.get(instance_id)
        return copy.deepcopy(r) if r else None

    def save(self, instance_id: str, record: InstanceRecord) -> None:
        if instance_id not in self.records:
            raise KeyError(f"không có instance {instance_id}")
        self.records[instance_id] = copy.deepcopy(record)

    def open_for(self, user: str) -> list[str]:
        return sorted(
            k for k, r in self.records.items() if r.status == "running" and user in r.candidates
        )

    def due(self, now: datetime) -> list[str]:
        return sorted(
            k for k, r in self.records.items() if r.next_due is not None and r.next_due <= now
        )


class InMemoryTimerFireStore:
    """Một tiến trình duy nhất; khóa duy nhất (process_key, period_key) như bảng thật."""

    def __init__(self) -> None:
        self.fires: dict[tuple[str, str], dict[str, Any]] = {}
        self.instances: dict[str, dict[str, Any]] = {}

    @contextmanager
    def transaction(self) -> Iterator[_MemTx]:
        tx = _MemTx(self)
        yield tx
        self.fires.update(tx.fires)
        self.instances.update(tx.instances)


class _MemTx:
    def __init__(self, store: InMemoryTimerFireStore):
        self.store = store
        self.fires: dict[tuple[str, str], dict[str, Any]] = {}
        self.instances: dict[str, dict[str, Any]] = {}

    def claim_period(
        self, *, process_key: str, period_key: str, status: str, version: int, fired_at: datetime
    ) -> bool:
        key = (process_key, period_key)
        if key in self.store.fires or key in self.fires:
            return False
        self.fires[key] = {"status": status, "version": version, "fired_at": fired_at,
                           "instance_id": None}  # fmt: skip
        return True

    def save_instance(
        self, *, instance_id: str, process_key: str, version: int, state: Mapping[str, Any]
    ) -> None:
        self.instances[instance_id] = {"process_key": process_key, "version": version,
                                       "state": copy.deepcopy(dict(state))}  # fmt: skip

    def link_instance(self, *, process_key: str, period_key: str, instance_id: str) -> None:
        self.fires[(process_key, period_key)]["instance_id"] = instance_id
