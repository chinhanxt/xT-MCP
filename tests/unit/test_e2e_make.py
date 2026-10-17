# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Exercise the actual E2E Make recipe without starting Docker services."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("failure", "keep", "expected"),
    [
        ("", False, ["e2e-up", "demo-seed", "e2e-run", "e2e-down"]),
        ("e2e-up", False, ["e2e-up", "e2e-down"]),
        ("demo-seed", False, ["e2e-up", "demo-seed", "e2e-down"]),
        ("e2e-run", False, ["e2e-up", "demo-seed", "e2e-run", "e2e-down"]),
        ("", True, ["e2e-up", "demo-seed", "e2e-run"]),
        ("demo-seed", True, ["e2e-up", "demo-seed"]),
    ],
)
def test_e2e_stops_on_failure_and_cleans_up(
    tmp_path: Path, failure: str, keep: bool, expected: list[str]
) -> None:
    result, calls = _run(tmp_path, failure=failure, keep=keep)
    assert calls == expected
    assert (result.returncode != 0) == bool(failure)
    if failure:
        assert "Error 17" in result.stderr


@pytest.mark.parametrize("failure", ["", "e2e-up", "demo-seed", "e2e-run"])
def test_e2e_cleanup_failure_does_not_hide_original_failure(tmp_path: Path, failure: str) -> None:
    result, calls = _run(tmp_path, failure=failure, cleanup_failure=True)
    assert result.returncode != 0
    assert calls[-1] == "e2e-down"
    assert f"Error {17 if failure else 23}" in result.stderr


def test_e2e_termination_still_cleans_up(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, terminate=True)
    assert result.returncode != 0
    assert calls == ["e2e-up", "demo-seed", "e2e-run", "e2e-down"]
    assert "Error 143" in result.stderr


def _run(
    tmp_path: Path,
    *,
    failure: str = "",
    keep: bool = False,
    cleanup_failure: bool = False,
    terminate: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    calls = tmp_path / "calls"
    runner = tmp_path / "make-stub"
    runner.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$1" >> "$XT_TEST_CALLS"\n'
        'if [ "$1" = "$XT_TEST_FAIL" ]; then exit 17; fi\n'
        'if [ "$1" = e2e-down ] && [ "$XT_TEST_CLEANUP_FAIL" = 1 ]; then exit 23; fi\n'
        'if [ "$1" = e2e-run ] && [ "$XT_TEST_TERMINATE" = 1 ]; then kill -TERM "$PPID"; fi\n',
        encoding="utf-8",
    )
    runner.chmod(0o700)
    env = {
        **os.environ,
        "XT_TEST_CALLS": str(calls),
        "XT_TEST_FAIL": failure,
        "XT_TEST_CLEANUP_FAIL": str(int(cleanup_failure)),
        "XT_TEST_TERMINATE": str(int(terminate)),
        "E2E_KEEP": str(int(keep)),
    }
    # A parent make's flags must not redirect this independent invocation.
    env.pop("MAKEFLAGS", None)
    env.pop("MFLAGS", None)
    result = subprocess.run(
        ["make", "--no-print-directory", "-f", str(ROOT / "Makefile"), f"MAKE={runner}", "e2e"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    return result, calls.read_text(encoding="utf-8").splitlines()
