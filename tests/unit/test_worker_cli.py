# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Worker startup contracts: fail closed, trusted plugins, explicit schema changes."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


@pytest.fixture
def worker_env() -> dict[str, str]:
    return {
        "XT_QUEUE_DATABASE_URL": "postgresql://xt_control:secret@localhost/xt",
        "XT_WORKER_DATABASE_URL": "postgresql://xt_app:secret@localhost/xt",
        "XT_WORKER_CONCURRENCY": "4",
        "XT_WORKER_STALLED_SECONDS": "30",
        "XT_WORKER_RECOVER_SECONDS": "5",
        "XT_WORKER_MAX_ATTEMPTS": "3",
        "XT_WORKER_RETRY_SECONDS": "2",
        "XT_WORKER_HANDLERS": "worker_plugins:handlers",
    }


def test_worker_configuration_requires_every_operational_setting(
    worker_env: dict[str, str],
) -> None:
    from xt.entrypoints.worker_config import WorkerConfig

    for variable in worker_env:
        incomplete = dict(worker_env)
        incomplete.pop(variable)
        with pytest.raises(ValueError, match=variable):
            WorkerConfig.from_env(incomplete)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("XT_WORKER_CONCURRENCY", "0"),
        ("XT_WORKER_CONCURRENCY", "many"),
        ("XT_WORKER_STALLED_SECONDS", "2"),
        ("XT_WORKER_RECOVER_SECONDS", "0"),
        ("XT_WORKER_RECOVER_SECONDS", "15"),
        ("XT_WORKER_MAX_ATTEMPTS", "0"),
        ("XT_WORKER_RETRY_SECONDS", "0"),
        ("XT_WORKER_HANDLERS", ""),
        ("XT_WORKER_HANDLERS", "worker_plugins"),
        ("XT_WORKER_HANDLERS", "worker_plugins:handlers,"),
        ("XT_QUEUE_DATABASE_URL", "postgresql://postgres:password@localhost/xt"),
        ("XT_WORKER_DATABASE_URL", "postgresql://xt_control:password@localhost/xt"),
        ("XT_QUEUE_DATABASE_URL", "not-a-url-with-secret"),
    ],
)
def test_worker_configuration_rejects_unsafe_settings(
    worker_env: dict[str, str], variable: str, value: str
) -> None:
    from xt.entrypoints.worker_config import WorkerConfig

    worker_env[variable] = value
    with pytest.raises(ValueError, match=variable) as error:
        WorkerConfig.from_env(worker_env)
    assert "password" not in str(error.value)
    assert "secret" not in str(error.value)


def test_worker_configuration_keeps_credentials_out_of_repr(worker_env: dict[str, str]) -> None:
    from xt.entrypoints.worker_config import WorkerConfig

    config = WorkerConfig.from_env(worker_env)
    assert config.concurrency == 4
    assert config.recover_seconds == 5
    assert config.handler_factories == ("worker_plugins:handlers",)
    assert "secret" not in repr(config)


def test_plugin_factories_are_explicit_and_duplicates_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xt.entrypoints.worker import load_handlers

    (tmp_path / "explicit_plugins.py").write_text(
        "async def handler(connection, job): pass\n"
        "def first(): return {'notify': handler}\n"
        "def second(): return {'notify': handler}\n"
        "def invalid(): return {'sync': lambda connection, job: None}\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    result = load_handlers(("explicit_plugins:first",))
    assert set(result) == {"notify"}
    with pytest.raises(ValueError, match="duplicate"):
        load_handlers(("explicit_plugins:first", "explicit_plugins:second"))
    with pytest.raises(ValueError, match="async"):
        load_handlers(("explicit_plugins:invalid",))


def test_plugin_errors_do_not_expose_exception_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xt.entrypoints.worker import load_handlers

    (tmp_path / "broken_plugins.py").write_text(
        "def handlers(): raise RuntimeError('private-database-password')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(ValueError, match="broken_plugins:handlers") as error:
        load_handlers(("broken_plugins:handlers",))
    assert "private-database-password" not in str(error.value)


def _runtime_module(events: list[object]) -> ModuleType:
    module = ModuleType("xt.adapters.queue.runtime")

    class Runtime:
        async def install_schema(self, migrator_dsn: str) -> None:
            events.append(("installed", migrator_dsn))

        async def run(self, name: str | None = None) -> None:
            events.append(("ran", name))

    def create_runtime(**kwargs: Any) -> Runtime:
        events.append(kwargs)
        return Runtime()

    module.create_runtime = create_runtime  # type: ignore[attr-defined]
    return module


def test_worker_cli_dispatches_and_never_installs_implicitly(
    worker_env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xt.entrypoints.cli import main

    events: list[object] = []
    monkeypatch.setitem(sys.modules, "xt.adapters.queue.runtime", _runtime_module(events))
    for variable, value in worker_env.items():
        monkeypatch.setenv(variable, value)
    (tmp_path / "worker_plugins.py").write_text(
        "async def handler(connection, job): pass\ndef handlers(): return {'notify': handler}\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    assert main(["worker", "--name", "worker-one"]) == 0
    assert events[1:] == [("ran", "worker-one")]
    arguments = events[0]
    assert isinstance(arguments, dict)
    assert arguments["queue_dsn"] == worker_env["XT_QUEUE_DATABASE_URL"]
    assert arguments["tenant_dsn"] == worker_env["XT_WORKER_DATABASE_URL"]
    assert arguments["concurrency"] == 4
    assert set(arguments["handlers"]) == {"notify"}


def test_worker_cli_schema_install_is_explicit_and_does_not_run_plugins(
    worker_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from xt.entrypoints.worker import main

    events: list[object] = []
    monkeypatch.setitem(sys.modules, "xt.adapters.queue.runtime", _runtime_module(events))
    for variable, value in worker_env.items():
        monkeypatch.setenv(variable, value)
    monkeypatch.delitem(sys.modules, "worker_plugins", raising=False)
    monkeypatch.setenv("XT_WORKER_HANDLERS", "missing_plugins:handlers")
    monkeypatch.setenv(
        "XT_QUEUE_MIGRATOR_DATABASE_URL", "postgresql://xt_migrator:secret@localhost/xt"
    )
    assert main(["--install-schema"]) == 0
    assert events[1:] == [("installed", "postgresql://xt_migrator:secret@localhost/xt")]


def test_worker_cli_configuration_failure_is_clear_and_redacted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from xt.entrypoints.worker import main

    monkeypatch.delenv("XT_QUEUE_DATABASE_URL", raising=False)
    assert main([]) == 2
    assert "XT_QUEUE_DATABASE_URL" in capsys.readouterr().err


def test_wrapper_preserves_version_and_doctor(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from xt import __version__
    from xt.entrypoints.cli import main

    with pytest.raises(SystemExit) as error:
        main(["--version"])
    assert error.value.code == 0
    assert capsys.readouterr().out.strip() == f"xt {__version__}"
    monkeypatch.delenv("XT_DATABASE_URL", raising=False)
    assert main(["doctor"]) == 0
    assert "postgres: unknown" in capsys.readouterr().out


def test_wrapper_help_advertises_worker(capsys: pytest.CaptureFixture[str]) -> None:
    module = importlib.import_module("xt.entrypoints.cli")
    with pytest.raises(SystemExit) as error:
        module.main(["--help"])
    assert error.value.code == 0
    assert "worker" in capsys.readouterr().out


def test_schema_install_requires_separate_operational_credentials(
    worker_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from xt.entrypoints.worker import main

    for variable, value in worker_env.items():
        monkeypatch.setenv(variable, value)
    monkeypatch.delenv("XT_QUEUE_MIGRATOR_DATABASE_URL", raising=False)
    assert main(["--install-schema"]) == 2
    assert "XT_QUEUE_MIGRATOR_DATABASE_URL" in capsys.readouterr().err


@pytest.mark.parametrize("role", ["xt_control", "xt_app"])
def test_schema_install_rejects_runtime_credentials(worker_env: dict[str, str], role: str) -> None:
    from xt.entrypoints.worker_config import WorkerConfig

    worker_env["XT_QUEUE_MIGRATOR_DATABASE_URL"] = f"postgresql://{role}:secret@localhost/xt"
    with pytest.raises(ValueError, match="XT_QUEUE_MIGRATOR_DATABASE_URL"):
        WorkerConfig.from_env(worker_env, install_schema=True)


def test_database_url_query_cannot_override_login_role(worker_env: dict[str, str]) -> None:
    from xt.entrypoints.worker_config import WorkerConfig

    worker_env["XT_WORKER_DATABASE_URL"] += "?user=postgres"
    with pytest.raises(ValueError, match="XT_WORKER_DATABASE_URL"):
        WorkerConfig.from_env(worker_env)


def test_runtime_errors_never_echo_connection_credentials(
    worker_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from xt.entrypoints.worker import main

    module = ModuleType("xt.adapters.queue.runtime")

    def create_runtime(**kwargs: Any) -> None:
        raise ValueError("postgresql://xt_app:private-password@localhost/xt")

    module.create_runtime = create_runtime  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "xt.adapters.queue.runtime", module)
    for variable, value in worker_env.items():
        monkeypatch.setenv(variable, value)
    monkeypatch.setenv(
        "XT_QUEUE_MIGRATOR_DATABASE_URL", "postgresql://postgres:secret@localhost/xt"
    )
    assert main(["--install-schema"]) == 1
    assert "private-password" not in capsys.readouterr().err


@pytest.mark.parametrize("wrapped", [False, True])
def test_missing_schema_error_explains_explicit_install_command(
    wrapped: bool,
    worker_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from psycopg.errors import UndefinedTable

    from xt.entrypoints.worker import main

    module = ModuleType("xt.adapters.queue.runtime")

    def create_runtime(**kwargs: Any) -> None:
        if wrapped:
            raise RuntimeError("private-wrapper-secret") from UndefinedTable(
                "private-database-password"
            )
        raise UndefinedTable("private-database-password")

    module.create_runtime = create_runtime  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "xt.adapters.queue.runtime", module)
    for variable, value in worker_env.items():
        monkeypatch.setenv(variable, value)
    monkeypatch.setenv(
        "XT_QUEUE_MIGRATOR_DATABASE_URL", "postgresql://postgres:secret@localhost/xt"
    )
    assert main(["--install-schema"]) == 1
    error = capsys.readouterr().err
    assert "xt worker --install-schema" in error
    assert "XT_QUEUE_MIGRATOR_DATABASE_URL" in error
    assert "private-database-password" not in error
    assert "private-wrapper-secret" not in error
