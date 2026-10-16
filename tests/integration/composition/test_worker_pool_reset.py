# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""The queue's security reset must survive many jobs on one physical connection."""

from __future__ import annotations

import asyncio

import pytest

from tests.integration.composition.fixtures import Worker
from xt.adapters.queue.runtime import _reset_session, create_runtime

pytestmark = pytest.mark.integration


def test_repeated_tenant_pool_resets_keep_driver_and_server_statement_state_aligned(
    worker: Worker,
) -> None:
    """Repeated queries cross psycopg's default auto-prepare threshold after DISCARD ALL.

    Use the same pooled connection and the production reset function deterministically;
    borrowing from the pool each time could mask the failure by cycling physical connections.
    """

    async def scenario() -> None:
        runtime = create_runtime(
            queue_dsn=worker.env["XT_QUEUE_DATABASE_URL"],
            tenant_dsn=worker.env["XT_WORKER_DATABASE_URL"],
            handlers={},
            concurrency=1,
            stalled_seconds=6,
            recover_seconds=1,
            max_attempts=2,
            retry_seconds=1,
        )
        async with runtime.tenant_pool:
            async with runtime.tenant_pool.connection() as connection:
                for value in range(16):
                    result = await connection.execute("SELECT %s::int AS value", (value,))
                    assert await result.fetchone() == (value,)
                    await _reset_session(connection)

    asyncio.run(scenario())
