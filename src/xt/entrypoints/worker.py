# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Composition root for the tenant-scoped PostgreSQL job worker."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import logging
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from xt.adapters.queue.config import is_schema_error
from xt.entrypoints.worker_config import WorkerConfig

if TYPE_CHECKING:
    from xt.adapters.queue.runtime import JobHandler


def load_handlers(factories: Sequence[str]) -> dict[str, JobHandler]:
    """Load trusted, operator-selected Python exports; never discover plugins."""
    handlers: dict[str, JobHandler] = {}
    for spec in factories:
        module_name, separator, export = spec.partition(":")
        if not separator:
            raise ValueError("XT_WORKER_HANDLERS expects module:factory exports")
        try:
            factory = getattr(importlib.import_module(module_name), export)
            result = factory()
        except Exception as exc:
            raise ValueError(
                f"XT_WORKER_HANDLERS could not load factory {spec} ({type(exc).__name__})"
            ) from None
        if not isinstance(result, dict) or not result:
            raise ValueError(f"XT_WORKER_HANDLERS factory {spec} must return a nonempty dict")
        for name, handler in result.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"XT_WORKER_HANDLERS factory {spec} returned an invalid job kind")
            if name in handlers:
                raise ValueError(f"XT_WORKER_HANDLERS has a duplicate job kind: {name}")
            if not callable(handler) or not (
                inspect.iscoroutinefunction(handler)
                or inspect.iscoroutinefunction(handler.__call__)
            ):
                raise ValueError(f"XT_WORKER_HANDLERS job kind {name} requires an async handler")
            try:
                inspect.signature(handler).bind(None, None)
            except (TypeError, ValueError):
                raise ValueError(
                    f"XT_WORKER_HANDLERS job kind {name} must accept (connection, job)"
                ) from None
            handlers[name] = cast("JobHandler", handler)
    return handlers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xt worker", description="Run the xT job worker")
    parser.add_argument("--name", help="worker identity used by the queue")
    parser.add_argument(
        "--install-schema", action="store_true", help="install queue schema explicitly, then exit"
    )
    return parser


async def _execute(
    config: WorkerConfig,
    handlers: dict[str, JobHandler],
    *,
    install_schema: bool,
    name: str | None,
) -> None:
    from xt.adapters.queue.runtime import create_runtime

    runtime = create_runtime(
        queue_dsn=config.queue_dsn,
        tenant_dsn=config.tenant_dsn,
        handlers=handlers,
        concurrency=config.concurrency,
        stalled_seconds=config.stalled_seconds,
        recover_seconds=config.recover_seconds,
        max_attempts=config.max_attempts,
        retry_seconds=config.retry_seconds,
    )
    if install_schema:
        if config.migrator_dsn is None:
            raise ValueError("XT_QUEUE_MIGRATOR_DATABASE_URL is required for schema installation")
        await runtime.install_schema(config.migrator_dsn)
    else:
        await runtime.run(name=name)


def _operation_failure(error: BaseException) -> str:
    if is_schema_error(error):
        return (
            "queue schema is missing or incompatible; check migrations and run "
            "'xt worker --install-schema' for a fresh database with "
            "XT_QUEUE_MIGRATOR_DATABASE_URL configured"
        )
    return (
        f"operation failed ({type(error).__name__}); check worker configuration and database access"
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["dlq"]:
        from xt.adapters.queue.dlq import main as dlq_main

        return dlq_main(arguments[1:])
    args = build_parser().parse_args(arguments)
    logging.basicConfig(
        level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        config = WorkerConfig.from_env(install_schema=args.install_schema)
        handlers = {} if args.install_schema else load_handlers(config.handler_factories)
    except ValueError as exc:
        print(f"xt worker: {exc}", file=sys.stderr)
        return 2
    try:
        asyncio.run(_execute(config, handlers, install_schema=args.install_schema, name=args.name))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # Driver/plugin exception messages can contain DSNs and arbitrary secrets.
        print(f"xt worker: {_operation_failure(exc)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
