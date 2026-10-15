# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tests.unit.process.support import (
    Clock,
    Services,
    clock_fixture,
    make_runtime,
    seed_org,
)
from xt.application.process.memory import InMemoryOrgDirectory
from xt.application.process.runtime import ProcessRuntime

MakeRt = Callable[..., ProcessRuntime]


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[Clock]:
    yield from clock_fixture(monkeypatch)


@pytest.fixture
def org() -> InMemoryOrgDirectory:
    return seed_org()


@pytest.fixture
def services() -> Services:
    return Services()


@pytest.fixture
def make_rt(org: InMemoryOrgDirectory, services: Services) -> MakeRt:
    def mk(
        spec: str | dict[str, Any],
        admin_role: str | None = "admin",
        events: list[dict[str, Any]] | None = None,
    ) -> ProcessRuntime:
        return make_runtime(spec, services, org, admin_role=admin_role, events=events)

    return mk
