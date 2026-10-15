# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""MCP Apps (`ui://xt/*`): resources/list|read, liên kết tool <-> view từ contract, tool app-only,
CSP chỉ origin xT, thẻ phê duyệt không có đường duyệt/gọi tool."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

import pytest
from mcp.shared.exceptions import MCPError

from tests.unit.mcp.apps_support import APPS_BASE, build_apps_harness
from tests.unit.mcp.conftest import Harness, principal
from tests.unit.tools.fakes import make_dispatcher, make_world
from xt.adapters.mcp import AppTool, UiResource, XtMcpServer, register_apps
from xt.adapters.mcp.apps.registry import normalize_origin
from xt.contracts.prompt_catalog import default_prompts
from xt.contracts.tools import (
    APP_TOOLS,
    APP_TOOLS_BY_NAME,
    APPROVAL_CARD_URI,
    BY_NAME,
    CATALOG,
    CORE_10,
    ORGCHART_URI,
    RECORD_TABLE_URI,
    UI_VIEWS,
    Profile,
)

pytestmark = pytest.mark.anyio
MIME = "text/html;profile=mcp-app"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def h() -> Harness:
    return build_apps_harness(
        tokens={
            "tok-mai": principal("sub-mai"),
            "tok-local": principal("sub-mai", client="goose-local"),
        }
    )


def env(r: Any) -> dict[str, Any]:
    return json.loads(r.content[0].text)


# ---- contract (nguồn duy nhất) --------------------------------------------------------------


def test_contract_links_views_and_app_tools() -> None:
    uris = {v.uri for v in UI_VIEWS}
    assert uris == {ORGCHART_URI, APPROVAL_CARD_URI, RECORD_TABLE_URI}
    for uri in uris:  # mỗi view có ít nhất một tool của model gắn vào
        assert any(c.ui_resource == uri for c in CATALOG), uri
    assert BY_NAME["find_people"].ui_resource == ORGCHART_URI
    assert BY_NAME["search_records"].ui_resource == RECORD_TABLE_URI
    assert BY_NAME["submit_request"].ui_resource == APPROVAL_CARD_URI
    assert BY_NAME["decide_request"].ui_resource == APPROVAL_CARD_URI
    for t in APP_TOOLS:
        assert t.ui_resource in uris and t.name not in BY_NAME
        assert t.delegates_to is None or t.delegates_to in BY_NAME
    # pii_reveal_ticket: kênh người /pii/reveal, do composition đăng ký (cần store vé thật).
    assert set(APP_TOOLS_BY_NAME) == {
        "org_person_details",
        "records_table_page",
        "pii_reveal_ticket",
    }
    # Thẻ phê duyệt không có tool app-only (TCV1: không có tool app-only để ghi/duyệt).
    assert not [t for t in APP_TOOLS if t.ui_resource == APPROVAL_CARD_URI]


# ---- resources ------------------------------------------------------------------------------


async def test_resources_list_and_read(h: Harness) -> None:
    async with h.running(), h.session("tok-mai") as s:
        listed = (await s.list_resources()).resources
        reads = {r.uri: await s.read_resource(r.uri) for r in listed}
        with pytest.raises(MCPError):
            await s.read_resource("ui://xt/unknown")
    assert {str(r.uri) for r in listed} == {v.uri for v in UI_VIEWS}
    for r in listed:
        assert r.mime_type == MIME
        assert r.meta["ui"]["csp"] == {  # type: ignore[index]
            "connectDomains": [APPS_BASE],
            "resourceDomains": [],
            "frameDomains": [],
            "baseUriDomains": [],
        }
        c = reads[str(r.uri)].contents[0]
        assert c.mime_type == MIME and c.text.startswith("<!DOCTYPE html>")  # type: ignore[union-attr]
        assert c.meta["ui"]["csp"]["connectDomains"] == [APPS_BASE]  # type: ignore[index]


async def test_html_is_self_contained_and_safe(h: Harness) -> None:
    async with h.running(), h.session("tok-mai") as s:
        pages = {
            u: (await s.read_resource(u)).contents[0].text  # type: ignore[union-attr]
            for u in (ORGCHART_URI, APPROVAL_CARD_URI, RECORD_TABLE_URI)
        }
    for uri, html in pages.items():
        assert "/*BRIDGE*/" not in html and "__XT_ORIGIN__" not in html, uri
        assert not re.search(r"<script[^>]+src=|<link|@import|<iframe|<img", html, re.I), uri
        assert not re.search(r"\beval\(|new Function|document\.write|innerHTML|outerHTML", html), (
            uri
        )
        # mọi URL tuyệt đối trong HTML chỉ có thể là namespace SVG hoặc origin xT
        urls = set(re.findall(r"https?://[^\s\"')<>]+", html))
        assert urls <= {"http://www.w3.org/2000/svg", APPS_BASE}, (uri, urls)
        assert 'lang="vi"' in html
    card = pages[APPROVAL_CARD_URI]
    assert '<meta name="xt-origin" content="https://xt.example.test">' in card
    assert "ui/open-link" in card and "Mở trang xác nhận" in card
    # thẻ phê duyệt không có đường gọi tool nào, không có hành động duyệt
    assert "tools/call" not in card and "callTool" not in card
    assert not re.search(r"\bapprove\b|\breject\b|Duyệt\b|Từ chối", card)
    assert "tools/call" in pages[ORGCHART_URI] and "tools/call" in pages[RECORD_TABLE_URI]
    assert "ui/open-link" not in pages[ORGCHART_URI] + pages[RECORD_TABLE_URI]


# ---- tools/list: liên kết và visibility ------------------------------------------------------


async def test_tool_meta_linkage_and_app_only_visibility(h: Harness) -> None:
    async with h.running(), h.session("tok-mai") as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
    registered = {v.uri for v in UI_VIEWS}
    for c in CATALOG:
        ui = (tools[c.name].meta or {}).get("ui")
        if c.ui_resource in registered:
            assert ui == {"resourceUri": c.ui_resource}, c.name  # model + app (mặc định)
        else:
            assert ui is None, c.name  # không gắn resource chưa đăng ký
    # register_apps đăng ký 2 tool app-only; pii_reveal_ticket do composition thêm.
    assert {t.name for t in APP_TOOLS if t.name in tools} == {
        "org_person_details",
        "records_table_page",
    }
    for t in (t for t in APP_TOOLS if t.name in tools):
        ui = tools[t.name].meta["ui"]  # type: ignore[index]
        assert ui == {"resourceUri": t.ui_resource, "visibility": ["app"]}
        assert tools[t.name].annotations.read_only_hint is True  # type: ignore[union-attr]

    def only_app(t: Any) -> bool:
        return ((t.meta or {}).get("ui") or {}).get("visibility") == ["app"]

    model_visible = [n for n, t in tools.items() if not only_app(t)]
    assert not {"org_person_details", "records_table_page"} & set(model_visible)
    assert len(model_visible) == len(CATALOG)


async def test_core10_profile_keeps_views_of_listed_tools() -> None:
    h = build_apps_harness(profile=Profile.CORE_10)
    async with h.running(), h.session("tok-viet") as s:
        names = {t.name for t in (await s.list_tools()).tools}
    assert set(CORE_10) | {"org_person_details", "records_table_page"} == names


async def test_app_tools_are_absent_without_apps() -> None:
    server = XtMcpServer(make_dispatcher(make_world()), lambda ctx: None, prompts=default_prompts())  # type: ignore[arg-type,return-value]
    assert not [t for t in server._listed if t.name in APP_TOOLS_BY_NAME]
    assert not server._app_tools


def test_server_rejects_inconsistent_app_registration() -> None:
    world = make_world()
    d = make_dispatcher(world)
    bundle = register_apps(xt_origin=APPS_BASE, dispatcher=d, people=world.services.directory)  # type: ignore[arg-type]
    tool = bundle.app_tools[0]

    def build(**kw: Any) -> XtMcpServer:
        return XtMcpServer(d, lambda ctx: None, prompts=default_prompts(), **kw)  # type: ignore[arg-type,return-value]

    with pytest.raises(ValueError, match="thiếu resource"):
        build(app_tools=[tool])
    with pytest.raises(ValueError, match="trùng"):
        build(ui_resources=bundle.ui_resources, app_tools=[tool, tool])
    fake = AppTool(replace(tool.contract, name="x_tool"), tool.run)
    with pytest.raises(ValueError, match="chưa có trong contract"):
        build(ui_resources=bundle.ui_resources, app_tools=[fake])
    with pytest.raises(ValueError):
        UiResource("https://evil/x", "x", "<p/>")


@pytest.mark.parametrize(
    "bad",
    [
        "http://xt.example.vn",
        "https://xt.example.vn/desk",
        "https://u:p@xt.example.vn",
        "xt.example.vn",
        "ftp://x.vn",
        "https://xt.vn?a=1",
    ],
)
def test_register_apps_rejects_bad_origin(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_origin(bad)
    assert normalize_origin("http://127.0.0.1:19520/") == "http://127.0.0.1:19520"
    assert normalize_origin("https://xt.freepro.online") == "https://xt.freepro.online"


# ---- hành vi tool app-only -------------------------------------------------------------------


async def test_org_person_details_masks_pii_for_cloud_clients(h: Harness) -> None:
    async with h.running():
        async with h.session("tok-mai") as s:
            r = await s.call_tool("org_person_details", {"ref": "person:tuan"})
            cloud = env(r)
            assert r.structured_content == cloud and not r.is_error
        async with h.session("tok-local") as s:
            local = env(await s.call_tool("org_person_details", {"ref": "person:tuan"}))
    d = cloud["data"]
    assert d["name"] == "Tuấn" and d["manager"]["name"] == "Minh"
    assert [x["ref"] for x in d["direct_reports"]] == ["person:binh"]
    assert re.fullmatch(r"\[PII:[0-9a-f]{6}\]", d["email"]) and re.fullmatch(
        r"\[PII:[0-9a-f]{6}\]", d["phone"]
    )
    assert "tuan@xt.vn" not in json.dumps(cloud) and "0901000004" not in json.dumps(cloud)
    assert cloud["meta"]["pii"]["mode"] == "masked"
    assert local["data"]["email"] == "tuan@xt.vn"  # lớp client local: theo policy llm_egress


async def test_org_person_details_errors(h: Harness) -> None:
    async with h.running(), h.session("tok-mai") as s:
        missing = await s.call_tool("org_person_details", {"ref": "person:ghost"})
        bad = await s.call_tool("org_person_details", {"ref": "task:t1"})
        extra = await s.call_tool("org_person_details", {"ref": "person:lan", "x": 1})
        h.world.access.deny_tools["org_person_details"] = "no_scope"
        denied = await s.call_tool("org_person_details", {"ref": "person:lan"})
    assert missing.is_error and env(missing)["error"]["code"] == "not_found"
    assert bad.is_error and env(bad)["error"]["code"] == "invalid_request"
    assert extra.is_error and env(extra)["error"]["code"] == "invalid_request"
    assert denied.is_error and env(denied)["error"]["code"] == "access_denied"


async def test_records_table_page_paginates_and_filters(h: Harness) -> None:
    async with h.running(), h.session("tok-mai") as s:
        p1 = env(
            await s.call_tool("records_table_page", {"model_id": "advance_request", "limit": 5})
        )
        p2 = env(
            await s.call_tool(
                "records_table_page",
                {"model_id": "advance_request", "limit": 5, "cursor": p1["data"]["next_cursor"]},
            )
        )
        flt = env(
            await s.call_tool(
                "records_table_page",
                {"model_id": "advance_request", "where": {"state": "approved"}, "limit": 50},
            )
        )
        qry = env(await s.call_tool("records_table_page", {"query": "đợt 7"}))
        none = await s.call_tool("records_table_page", {})
    assert p1["data"]["total"] >= 12 and len(p1["data"]["items"]) == 5 and p1["data"]["next_cursor"]
    assert {i["ref"] for i in p1["data"]["items"]}.isdisjoint(i["ref"] for i in p2["data"]["items"])
    assert flt["data"]["items"] and {i["state"] for i in flt["data"]["items"]} == {"approved"}
    assert [i["title"] for i in qry["data"]["items"]] == ["Tạm ứng công tác đợt 7"]
    assert (
        none.is_error and env(none)["error"]["code"] == "invalid_request"
    )  # thiếu model_id và query


async def test_approval_card_has_no_app_tool_and_decide_never_runs_from_app(h: Harness) -> None:
    """Dữ liệu cho thẻ phê duyệt đến từ decide_request (confirmation_required + confirm_url của xT);
    không có tool app-only nào duyệt được."""
    args = {
        "ref": "advance_request:tu-0042",
        "decision": "approve",
        "expected_revision": 2,
        "idempotency_key": "dec-app-0001",
    }
    async with h.running(), h.session("tok-mai") as s:
        r = await s.call_tool("decide_request", args)
        names = {t.name for t in (await s.list_tools()).tools}
    d = env(r)["error"]["details"]
    assert r.is_error and d["confirm_url"].startswith(APPS_BASE + "/desk/confirm/")
    assert d["plan_summary"] and d["confirmer"]["name"]
    assert not [n for n in names if (n in APP_TOOLS_BY_NAME and "decide" in n) or "approve" in n]
