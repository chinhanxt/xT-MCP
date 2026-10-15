# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""WA-01 (offline, không Docker): env thật của service `xt-api` trong `deploy/compose.yaml`, nội
suy bằng `deploy/.env.example`, phải dựng được cấu hình resource server MCP lẫn API workflow,
kể cả khi `XT_MCP_ALLOWED_CLIENTS` rỗng (chỉ connector DCR), và nhận đúng client mà
`provision_data.py` cấp scope `xt-workflow`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from xt.adapters.identity import load_resource_server_settings
from xt.entrypoints.workflow_http import WorkflowHttpConfigError, workflow_resource_settings

DEPLOY = Path(__file__).resolve().parents[3] / "deploy"
_VAR = re.compile(r"\$\{([A-Z0-9_]+)(?::([-?])([^}]*))?\}")


def _env_example() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (DEPLOY / ".env.example").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).split("#", 1)[0].strip().strip('"')
    return out


def _interpolate(value: str, env: dict[str, str]) -> str:
    def sub(m: re.Match[str]) -> str:
        name, op, arg = m.group(1), m.group(2), m.group(3) or ""
        v = env.get(name, "")
        if op == "-" and not v:
            return arg
        return v or ("secret" if op == "?" else "")

    return _VAR.sub(sub, value)


def xt_api_env(**over: str) -> dict[str, str]:
    text = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
    block = text.split("\n  xt-api:\n", 1)[1].split("\n    environment:\n", 1)[1]
    src = {
        **_env_example(),
        "XT_DOMAIN_AUTH": "auth.example.test",
        "XT_DOMAIN_MCP": "mcp.example.test",
        "XT_DOMAIN_API": "api.example.test",
        "XT_DOMAIN_DESK": "desk.example.test",
        **over,
    }
    env: dict[str, str] = {}
    for line in block.splitlines():
        if line and not line.startswith("      "):
            break  # hết khối environment
        m = re.match(r"^      ([A-Z0-9_]+): (.*?)(\s+#.*)?$", line)
        if m:
            env[m.group(1)] = _interpolate(m.group(2).strip().strip('"'), src)
    return env


def test_compose_xt_api_accepts_the_provisioned_workflow_client() -> None:
    env = xt_api_env(XT_MCP_ALLOWED_CLIENTS="")
    assert env["XT_MCP_ALLOWED_CLIENTS"] == "" and env["XT_MCP_CLIENT_REGISTRY"] == "keycloak"
    rs = load_resource_server_settings(env)  # registry động: allowlist MCP rỗng là hợp lệ
    ws = workflow_resource_settings(rs, env)
    assert ws.allowed_clients == {"xt-cli"} and ws.resource == "xt-workflow"
    assert env["XT_WORKFLOW_PUBLIC_URL"] == "https://api.example.test"
    custom = workflow_resource_settings(rs, xt_api_env(XT_WORKFLOW_CLIENTS="ops-cli"))
    assert custom.allowed_clients == {"ops-cli"}


def test_minimum_is_documented_when_no_client_at_all() -> None:
    env = xt_api_env(XT_MCP_ALLOWED_CLIENTS="")
    rs = load_resource_server_settings(env)
    env["XT_WORKFLOW_ALLOWED_CLIENTS"] = ""
    with pytest.raises(WorkflowHttpConfigError, match="XT_WORKFLOW_ALLOWED_CLIENTS"):
        workflow_resource_settings(rs, env)
