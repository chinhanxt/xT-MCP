# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from __future__ import annotations

import pytest

from tests.unit.identity.factory import FakeClock, FakeJwks, SigningKey, new_ec, new_rsa
from xt.adapters.identity.jwks import JwksCache


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def rsa_key() -> SigningKey:
    return new_rsa("kid-rsa-1")


@pytest.fixture
def ec_key() -> SigningKey:
    return new_ec("kid-ec-1")


@pytest.fixture
def jwks_source(rsa_key: SigningKey, ec_key: SigningKey) -> FakeJwks:
    return FakeJwks(keys=[rsa_key, ec_key])


@pytest.fixture
def jwks(jwks_source: FakeJwks, clock: FakeClock) -> JwksCache:
    return JwksCache(jwks_source, ttl_s=300, min_refetch_interval_s=10, clock=clock)
