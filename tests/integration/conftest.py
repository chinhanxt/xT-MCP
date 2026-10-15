# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from __future__ import annotations

import secrets
import shutil
from collections.abc import Iterator

import pytest

from tests.integration.composition.fixtures import (  # noqa: F401  (fixture phiên dùng chung)
    app_db,
    composition,
    idp,
    mcp_port,
    pg,
    stack_base,
    tenants,
    worker,
)
from tests.integration.keycloak import Admin, Realm, start_container, stop_container


@pytest.fixture(scope="session")
def kc_admin() -> Iterator[Admin]:
    if shutil.which("docker") is None:
        pytest.fail("test tích hợp cần docker (image quay.io/keycloak/keycloak:26.6.3)")
    password = secrets.token_urlsafe(18)
    start_container(password)
    try:
        yield Admin(password)
    finally:
        stop_container()


@pytest.fixture(scope="session")
def realm(kc_admin: Admin) -> Realm:
    return kc_admin.create_realm("xt-it", stepup=True)
