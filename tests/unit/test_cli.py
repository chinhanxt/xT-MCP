# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from __future__ import annotations

import socket
import subprocess
import sys

import pytest

from xt import __version__
from xt.adapters.cli.main import main


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"xt {__version__}"


def test_doctor_without_db_reports_unknown(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XT_DATABASE_URL", raising=False)
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "python: ok" in out
    assert "postgres: unknown" in out


def test_doctor_unreachable_db_fails(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("XT_DATABASE_URL", f"postgresql://u:p@127.0.0.1:{port}/d")
    assert main(["doctor"]) == 1
    assert "postgres: fail" in capsys.readouterr().out


def test_doctor_reachable_db_ok(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        monkeypatch.setenv("XT_DATABASE_URL", f"postgresql://u:p@127.0.0.1:{port}/d")
        assert main(["doctor"]) == 0
    assert "postgres: ok" in capsys.readouterr().out


def test_console_entry_module() -> None:
    r = subprocess.run(
        [sys.executable, "-m", "xt.adapters.cli.main", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert r.stdout.startswith("xt ")
