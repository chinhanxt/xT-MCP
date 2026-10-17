# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Exercise the Python relay against the actual integrated Node/SQLite service."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route
from starlette.testclient import TestClient

from xt.entrypoints.studio_http import StudioProxy
from xt.entrypoints.studio_tools import StudioTools

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def studio_node(tmp_path: Path) -> Any:
    node = shutil.which("node")
    if not node or not (ROOT / "imports/dify-rea/node_modules").exists():
        pytest.skip("Node >=24 and Studio dependencies required (make unified-setup)")
    version = subprocess.check_output([node, "--version"], text=True)
    if int(version.removeprefix("v").split(".")[0]) < 24:
        pytest.skip("Studio requires Node >=24")
    secret = os.urandom(32)
    key = tmp_path / "studio.key"
    key.write_bytes(secret)
    key.chmod(0o600)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log = (tmp_path / "node.log").open("w+")
    process = subprocess.Popen(
        [node, "src/server.ts"],
        cwd=ROOT / "imports/dify-rea",
        env={
            **os.environ,
            "STUDIO_XT_MODE": "1",
            "XT_STUDIO_SECRET_FILE": str(key),
            "STUDIO_DATA_DIR": str(tmp_path / "data"),
            "PORT": str(port),
            "LOG_LEVEL": "silent",
        },
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    origin = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        with httpx.Client(trust_env=False, timeout=0.5) as client:
            while True:
                if process.poll() is not None:
                    log.seek(0)
                    pytest.fail("Studio exited: " + log.read())
                try:
                    if client.get(origin + "/api/session").status_code == 401:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() >= deadline:
                    pytest.fail("Studio did not listen within 15 seconds")
                time.sleep(0.05)
        yield origin, secret
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


def test_real_python_node_boundary_isolates_tenants_and_roles(studio_node: Any) -> None:
    origin, secret = studio_node
    tenant_a, tenant_b, person = uuid4(), uuid4(), uuid4()
    actors = {
        "admin-a": (tenant_a, ["studio_admin"]),
        "admin-b": (tenant_b, ["studio_admin"]),
        "viewer-a": (tenant_a, ["studio_user"]),
    }

    def authorize(request: Request) -> Any:
        actor = actors.get(request.headers.get("authorization", ""))
        if actor is None:
            raise PermissionError("authentication")
        return SimpleNamespace(
            tenant=SimpleNamespace(id=actor[0]),
            person=SimpleNamespace(id=person, name="Nhân viên", roles=actor[1]),
            alias="company",
        )

    proxy = StudioProxy(authorize, origin, secret, httpx.AsyncClient(trust_env=False))
    app = Starlette(
        routes=[
            Route(
                "/studio-api/{path:path}", proxy.endpoint, methods=["GET", "POST", "PUT", "DELETE"]
            )
        ]
    )
    with TestClient(app) as browser:
        assert browser.get("/studio-api/session").status_code == 401
        browser.headers["authorization"] = "admin-a"
        session = browser.get("/studio-api/session")
        assert session.status_code == 200, session.text
        assert session.json()["integrated"] is True
        created = browser.post("/studio-api/apps", json={"name": "Kho thử nghiệm", "kind": "chat"})
        assert created.status_code == 201, created.text
        app_id = created.json()["id"]
        assert browser.get(f"/studio-api/apps/{app_id}").status_code == 200
        assert browser.get("/studio-api/apps").json()[0]["name"] == "Kho thử nghiệm"
        browser.headers["authorization"] = "admin-b"
        assert browser.get(f"/studio-api/apps/{app_id}").status_code == 404
        assert browser.get("/studio-api/apps").json() == []
        browser.headers["authorization"] = "viewer-a"
        assert browser.get("/studio-api/apps").json() == []
        assert (
            browser.post("/studio-api/apps", json={"name": "Denied", "kind": "chat"}).status_code
            == 403
        )
        assert browser.post("/studio-api/login", json={}).status_code == 404


def test_real_node_tool_delegation_is_bound_to_parent_request(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    from xt.application.tools.context import Actor, CallContext, ClientClass
    from xt.domain.identity import PrincipalKind

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    monkeypatch.setenv("STUDIO_XT_CORE_URL", f"http://127.0.0.1:{port}")
    origin, secret = request.getfixturevalue("studio_node")
    context = CallContext(
        actor=Actor("company-a", "user", PrincipalKind.AGENT, "xt-desk-agent", "user"),
        client_class=ClientClass.CLOUD,
        trace_id="node-bridge",
        now=datetime.now(UTC),
    )
    caller = SimpleNamespace(
        tenant=SimpleNamespace(id=uuid4()),
        person=SimpleNamespace(id=uuid4(), name="Người biên tập", roles=["studio_editor"]),
        alias="company-a",
    )

    def authorize(incoming: Request) -> Any:
        if incoming.headers.get("authorization") != "DPoP user-token":
            raise PermissionError("auth")
        incoming.state.studio_context = context
        return caller

    tools = StudioTools(SimpleNamespace(), secret)
    proxy = StudioProxy(authorize, origin, secret, httpx.AsyncClient(trust_env=False), tools)
    app = Starlette(
        routes=[
            Route("/studio-api/{path:path}", proxy.endpoint, methods=["GET", "POST"]),
            Route("/studio-internal/tools", tools.endpoint, methods=["POST"]),
        ]
    )
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline
            time.sleep(0.01)
        with httpx.Client(trust_env=False) as client:
            response = client.get(
                f"http://127.0.0.1:{port}/studio-api/xt/tools",
                headers={
                    "authorization": "DPoP user-token",
                },
            )
            assert response.status_code == 200, response.text
            names = {tool["name"] for tool in response.json()["tools"]}
            assert "search_records" in names and "submit_request" in names
            assert "decide_request" not in names
            deadline = time.monotonic() + 2
            while tools.active and time.monotonic() < deadline:
                time.sleep(0.01)
            assert tools.active == {}
            assert (
                client.post(
                    f"http://127.0.0.1:{port}/studio-internal/tools", json={"action": "list"}
                ).status_code
                == 401
            )
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()
