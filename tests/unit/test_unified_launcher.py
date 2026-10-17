# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Launcher preflight refuses invalid config or occupied ports before building/starting."""

from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def launch(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    inherited = {k: v for k, v in os.environ.items() if not k.startswith(("XT_", "DESK_"))}
    return subprocess.run(
        ["node", "scripts/unified-start.mjs"],
        cwd=ROOT,
        env={**inherited, **env},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_requires_explicit_private_runtime_config(tmp_path: Path) -> None:
    assert "XT_UNIFIED_ENV_FILE" in launch({}).stderr
    config = tmp_path / "runtime.env"
    config.write_text("XT_DB_APP_URL=not-logged-secret\n")
    config.chmod(0o644)
    result = launch({"XT_UNIFIED_ENV_FILE": str(config)})
    assert result.returncode and "chmod 600" in result.stderr
    assert "not-logged-secret" not in result.stderr
    config.chmod(0o600)
    result = launch({"XT_UNIFIED_ENV_FILE": str(config)})
    assert result.returncode and "Missing XT_IDP_ISSUER" in result.stderr
    assert "not-logged-secret" not in result.stderr


def test_occupied_port_never_stops_existing_service_or_starts_children(tmp_path: Path) -> None:
    config = tmp_path / "runtime.env"
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        values = {
            "XT_DB_APP_URL": "not-logged-secret",
            "XT_IDP_ISSUER": "https://auth.example.test/realms/xt",
            "XT_MCP_RESOURCE": "https://mcp.example.test/mcp",
            "XT_RECEIPT_SIGNING_KEY_FILE": str(tmp_path / "receipt.key"),
            "XT_CURSOR_KEY_FILE": str(tmp_path / "cursor.key"),
            "XT_UNIFIED_CORE_PORT": str(port),
            "XT_UNIFIED_STATE_DIR": str(tmp_path / "state"),
        }
        config.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
        config.chmod(0o600)
        result = launch({"XT_UNIFIED_ENV_FILE": str(config)})
        assert result.returncode and f"Port {port} already in use" in result.stderr
        assert "not-logged-secret" not in result.stderr
        assert "xT portal:" not in result.stdout
        assert occupied.fileno() >= 0
