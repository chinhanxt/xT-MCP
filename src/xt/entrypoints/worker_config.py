# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Explicit worker settings; credentials are never included in diagnostics."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from xt.adapters.queue.config import validate_database_url


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _number(env: Mapping[str, str], name: str, minimum: int) -> int:
    raw = _required(env, name)
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer >= {minimum}") from None
    if value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _dsn(env: Mapping[str, str], name: str, role: str | None) -> str:
    return validate_database_url(_required(env, name), setting=name, role=role)


def _factory_specs(env: Mapping[str, str]) -> tuple[str, ...]:
    name = "XT_WORKER_HANDLERS"
    specs = tuple(item.strip() for item in _required(env, name).split(","))
    for spec in specs:
        module, separator, factory = spec.partition(":")
        if (
            not separator
            or not module
            or not all(part.isidentifier() for part in module.split("."))
            or not factory.isidentifier()
        ):
            raise ValueError(f"{name} must contain comma-separated module:factory exports")
    return specs


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    """No implicit operational defaults: the operator supplies every setting."""

    queue_dsn: str = field(repr=False)
    tenant_dsn: str = field(repr=False)
    concurrency: int
    stalled_seconds: int
    recover_seconds: int
    max_attempts: int
    retry_seconds: int
    handler_factories: tuple[str, ...]
    migrator_dsn: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, install_schema: bool = False
    ) -> WorkerConfig:
        source = os.environ if env is None else env
        queue_dsn = _dsn(source, "XT_QUEUE_DATABASE_URL", "xt_control")
        tenant_dsn = _dsn(source, "XT_WORKER_DATABASE_URL", "xt_app")
        concurrency = _number(source, "XT_WORKER_CONCURRENCY", 1)
        stalled_seconds = _number(source, "XT_WORKER_STALLED_SECONDS", 3)
        recover_seconds = _number(source, "XT_WORKER_RECOVER_SECONDS", 1)
        if recover_seconds * 2 >= stalled_seconds:
            raise ValueError(
                "XT_WORKER_RECOVER_SECONDS must be less than XT_WORKER_STALLED_SECONDS / 2"
            )
        max_attempts = _number(source, "XT_WORKER_MAX_ATTEMPTS", 1)
        retry_seconds = _number(source, "XT_WORKER_RETRY_SECONDS", 1)
        handlers = _factory_specs(source)
        migrator_dsn = (
            _dsn(source, "XT_QUEUE_MIGRATOR_DATABASE_URL", None) if install_schema else None
        )
        return cls(
            queue_dsn=queue_dsn,
            tenant_dsn=tenant_dsn,
            concurrency=concurrency,
            stalled_seconds=stalled_seconds,
            recover_seconds=recover_seconds,
            max_attempts=max_attempts,
            retry_seconds=retry_seconds,
            handler_factories=handlers,
            migrator_dsn=migrator_dsn,
        )
