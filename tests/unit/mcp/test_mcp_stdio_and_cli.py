# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""stdio (token-file, đánh dấu khóa trùng theo dòng) và `xt serve` dừng khi thiếu cấu hình."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from tests.unit.mcp.conftest import FakeVerifier, principal
from tests.unit.tools.fakes import make_dispatcher, make_world
from xt.adapters.mcp import (
    DUPLICATE_KEYS_META,
    UiResource,
    XtMcpServer,
    mark_duplicate_keys,
    stdio_caller,
    token_file_source,
)
from xt.adapters.mcp.stdio import _GuardedLines
from xt.contracts.prompt_catalog import default_prompts
from xt.entrypoints.cli import main as cli_main
from xt.entrypoints.serve import load_serve_config
from xt.entrypoints.serve import main as serve_main

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _server(token_file: Path, **kw: Any) -> XtMcpServer:
    verifier = FakeVerifier({"tok-mai": principal("sub-mai")})
    return XtMcpServer(
        make_dispatcher(make_world()),
        stdio_caller(token_file_source(token_file), verifier.verify),
        prompts=default_prompts(),
        **kw,
    )


async def test_stdio_resolver_reads_token_file_each_call(tmp_path: Path) -> None:
    tf = tmp_path / "token"
    tf.write_text("tok-mai\n", encoding="utf-8")
    server = _server(tf)
    async with Client(server.server) as c:
        ok = await c.call_tool("get_record", {"ref": "task:t1"})
        tf.write_text("revoked-or-rotated-garbage", encoding="utf-8")
        bad = await c.call_tool("get_record", {"ref": "task:t1"})
        tf.unlink()
        gone = await c.call_tool("get_record", {"ref": "task:t1"})
    assert ok.is_error is False and ok.structured_content["ok"] is True
    for r in (bad, gone):
        assert r.is_error is True
        assert json.loads(r.content[0].text)["error"]["code"] == "unauthenticated"


async def test_stdio_lines_are_marked_for_duplicate_keys() -> None:
    lines = [
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"x",'
        '"arguments":{"a":1,"a":2}}}\n',
        '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
    ]

    class Src:
        def __aiter__(self) -> Any:
            async def gen() -> Any:
                for line in lines:
                    yield line

            return gen()

    got = [line async for line in _GuardedLines(Src())]  # type: ignore[arg-type]
    assert json.loads(got[0])["params"]["_meta"][DUPLICATE_KEYS_META] == ["a"]
    assert got[1] == lines[1]


def test_mark_duplicate_keys_variants() -> None:
    assert mark_duplicate_keys(b'{"method":"tools/list","a":1}') is None
    assert mark_duplicate_keys(b"{broken") is None
    assert mark_duplicate_keys(b'{"method":"ping","a":1,"a":2}') is None  # chỉ tools/call
    marked = mark_duplicate_keys(
        b'{"method":"tools/call","params":{"name":"t","_meta":{"k":1},"arguments":{"x":1,"x":1}}}'
    )
    assert isinstance(marked, bytes)
    assert json.loads(marked)["params"]["_meta"] == {"k": 1, DUPLICATE_KEYS_META: ["x"]}


async def test_ui_resources_are_linked_only_when_registered(tmp_path: Path) -> None:
    tf = tmp_path / "token"
    tf.write_text("tok-mai", encoding="utf-8")
    ui = UiResource("ui://xt/org-chart", "org_chart", "<!DOCTYPE html><p>org</p>")
    server = _server(tf, ui_resources=[ui])
    async with Client(server.server) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        res = await c.list_resources()
        read = await c.read_resource("ui://xt/org-chart")
    assert tools["find_people"].meta["ui"] == {"resourceUri": "ui://xt/org-chart"}
    assert "ui" not in tools["get_inbox"].meta
    assert [r.mime_type for r in res.resources] == ["text/html;profile=mcp-app"]
    assert read.contents[0].text.startswith("<!DOCTYPE html>")  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        _server(tf, ui_resources=[UiResource("ui://xt/unknown", "x", "<p/>")])
    with pytest.raises(ValueError):
        UiResource("https://evil/x", "x", "<p/>")


# ---- xt serve -------------------------------------------------------------------------------


def test_serve_fails_fast_on_missing_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for k in list(__import__("os").environ):
        if k.startswith("XT_"):
            monkeypatch.delenv(k, raising=False)
    assert cli_main(["serve", "--transport", "http"]) == 2
    err = capsys.readouterr().err
    assert "XT_MCP_SERVICES" in err and "XT_DESK_URL" in err and "--host" in err
    assert serve_main(["--transport", "stdio"]) == 2
    assert "XT_MCP_TOKEN_FILE" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        serve_main(["--transport", "carrier-pigeon"])


def test_serve_config_validation(tmp_path: Path) -> None:
    from xt.entrypoints.serve import ServeConfigError

    tf = tmp_path / "t"
    tf.write_text("x", encoding="utf-8")
    env = {
        "XT_MCP_SERVICES": "pkg.mod:factory",
        "XT_DESK_URL": "https://xt.example.vn",
        "XT_MCP_TOKEN_FILE": str(tf),
        "XT_MCP_CAPABILITIES": "aggregate_records",
        "XT_MCP_LOCAL_CLIENTS": "desk-bff, ollama-bridge",
        "XT_MCP_ALLOWED_CLIENTS": "claude-connector,desk-bff,ollama-bridge,xt-desk-agent",
        "XT_MCP_CONFIDENTIAL_CLIENTS": "desk-bff,ollama-bridge",
    }
    cfg = load_serve_config(env, "stdio", "core-10", None, None)
    assert cfg.tool_settings.enabled_capabilities == frozenset({"aggregate_records"})
    assert cfg.local_clients == frozenset({"desk-bff", "ollama-bridge"})
    assert cfg.profile.value == "core-10"
    bad = {
        **env,
        "XT_MCP_SERVICES": "nocolon",
        "XT_MCP_CAPABILITIES": "delete_record",
        "XT_CONFIRMATION_TTL_SECONDS": "5",
    }
    with pytest.raises(ServeConfigError) as e:
        load_serve_config(bad, "stdio", "huge", None, None)
    text = " | ".join(e.value.problems)
    for needle in (
        "XT_MCP_SERVICES",
        "XT_MCP_CAPABILITIES",
        "XT_CONFIRMATION_TTL_SECONDS",
        "--profile",
    ):
        assert needle in text
    http = load_serve_config({**env}, "http", "full", "127.0.0.1", 8710)
    assert http.port == 8710


def test_serve_rejects_factory_that_is_not_tool_services() -> None:
    from xt.entrypoints.serve import ServeConfigError, load_services

    with pytest.raises(ServeConfigError):
        load_services("json:dumps", {})
    with pytest.raises(ServeConfigError):
        load_services("xt_missing_module:factory", {})


def test_xt_serve_http_process_smoke(tmp_path: Path) -> None:
    """Chạy thật `xt serve --transport http` (uvicorn) rồi kiểm PRM + 401 qua socket."""
    import os
    import socket
    import subprocess
    import sys
    import time
    import urllib.error
    import urllib.request

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = Path(__file__).resolve().parents[3]
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("XT_")},
        "PYTHONPATH": str(root),
        "XT_MCP_SERVICES": "tests.unit.tools.fakes:services_factory",
        "XT_DESK_URL": "http://127.0.0.1:1/",
        "XT_IDP_ISSUER": "http://127.0.0.1:9/realms/xt",
        "XT_IDP_JWKS_URL": "http://127.0.0.1:9/realms/xt/protocol/openid-connect/certs",
        "XT_IDP_ALLOW_HTTP": "true",
        "XT_MCP_RESOURCE": f"http://127.0.0.1:{port}/mcp",
        "XT_MCP_ALLOWED_CLIENTS": "claude-connector",
        "XT_MCP_REQUIRED_SCOPES": "mcp-xt",
        "XT_MCP_SCOPES_SUPPORTED": "openid mcp-xt",
        "XT_IDP_INTROSPECTION": "off",
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "xt.entrypoints.cli",
            "serve",
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=tmp_path,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{base}/healthz", timeout=1).read()
                break
            except OSError:
                time.sleep(0.1)
        prm = json.loads(
            urllib.request.urlopen(
                f"{base}/.well-known/oauth-protected-resource/mcp", timeout=5
            ).read()
        )
        assert prm["resource"] == f"{base}/mcp"
        req = urllib.request.Request(
            f"{base}/mcp",
            method="POST",
            data=b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_inbox"}}',
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
        )
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        assert e.value.code == 401
        assert "resource_metadata=" in e.value.headers["WWW-Authenticate"]
    finally:
        proc.terminate()
        out, _ = proc.communicate(timeout=10)
    assert b"Traceback" not in out, out.decode(errors="replace")


def test_public_clients_cannot_get_local_pii_tier(tmp_path: Path) -> None:
    """R2-M4 (docs/decisions.md): client public như xt-desk-agent tối đa masked."""
    from xt.entrypoints.serve import ServeConfigError

    tf = tmp_path / "t"
    tf.write_text("x", encoding="utf-8")
    env = {
        "XT_MCP_SERVICES": "pkg.mod:factory",
        "XT_DESK_URL": "https://xt.example.vn",
        "XT_MCP_TOKEN_FILE": str(tf),
        "XT_MCP_ALLOWED_CLIENTS": "xt-desk-agent",
        "XT_MCP_LOCAL_CLIENTS": "xt-desk-agent",
    }
    with pytest.raises(ServeConfigError, match="confidential"):
        load_serve_config(env, "stdio", "full", None, None)
    with pytest.raises(ServeConfigError, match="XT_MCP_ALLOWED_CLIENTS"):
        load_serve_config(
            {**env, "XT_MCP_LOCAL_CLIENTS": "rogue", "XT_MCP_DEV_LOCAL_TIER": "1"},
            "stdio",
            "full",
            None,
            None,
        )
    dev = load_serve_config({**env, "XT_MCP_DEV_LOCAL_TIER": "true"}, "stdio", "full", None, None)
    assert dev.local_clients == frozenset({"xt-desk-agent"})
