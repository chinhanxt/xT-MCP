# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Thế giới giả có sẵn MCP Apps: thư mục tổ chức giàu dữ liệu, làn PII che email/điện thoại,
tìm kiếm có phân trang + lọc. Dùng chung cho unit test và e2e Desk (desk/e2e/xt_apps_server.py)."""

from __future__ import annotations

import zlib
from dataclasses import dataclass, replace
from typing import Any

from tests.unit.identity.factory import rs_settings
from tests.unit.mcp.conftest import FakeVerifier, Harness, principal
from tests.unit.tools.fakes import (
    FakeDirectory,
    FakePii,
    FakeRecords,
    Rec,
    make_dispatcher,
    make_world,
)
from xt.adapters.mcp import (
    ClientClassifier,
    XtMcpServer,
    build_http_app,
    http_caller,
    register_apps,
)
from xt.application.tools import CallContext, ClientClass
from xt.application.tools.errors import not_found
from xt.application.tools.ports import Args, Data, PiiProjection
from xt.contracts.prompt_catalog import default_prompts
from xt.contracts.tools import Profile
from xt.entrypoints.serve import IdentityHttpAuth

APPS_BASE = "https://xt.example.test"

# ref -> (tên, chức danh, đơn vị, quản lý, email, điện thoại)
ORG: dict[str, tuple[str, str, str, str | None, str, str]] = {
    "person:lan": ("Lan", "Tổng giám đốc", "Ban giám đốc", None, "lan@xt.vn", "0901000001"),
    "person:minh": ("Minh", "Giám đốc CN", "Công nghệ", "person:lan", "minh@xt.vn", "0901000002"),
    "person:hoa": ("Hoa", "Giám đốc TC", "Tài chính", "person:lan", "hoa@xt.vn", "0901000003"),
    "person:tuan": (
        "Tuấn",
        "Trưởng nhóm dev",
        "Công nghệ",
        "person:minh",
        "tuan@xt.vn",
        "0901000004",
    ),
    "person:binh": (
        "Bình",
        "Kỹ sư phần mềm",
        "Công nghệ",
        "person:tuan",
        "binh@xt.vn",
        "0901000005",
    ),
    "person:nga": ("Nga", "Kế toán trưởng", "Tài chính", "person:hoa", "nga@xt.vn", "0901000006"),
}


def _named(ref: str) -> dict[str, str]:
    return {"ref": ref, "name": ORG[ref][0]}


def _row(ref: str) -> dict[str, Any]:
    name, title, unit, mgr, _, _ = ORG[ref]
    return {
        "ref": ref,
        "name": name,
        "title": title,
        "org_unit": {"ref": "org_unit:" + unit.lower().replace(" ", "_"), "name": unit},
        "manager": _named(mgr) if mgr else None,
        "status": "active",
    }


class OrgDirectory(FakeDirectory):
    """Cổng thư mục tổ chức: `find_people` + `person_details` (cổng PersonDetails)."""

    def _match(self, key: str) -> str | None:
        k = key.lower()
        return next((r for r, v in ORG.items() if r == key or v[0].lower() == k), None)

    def find_people(self, ctx: CallContext, args: Args) -> Data:
        anchor: str | None = None
        relation = "search"
        if "direct_reports_of" in args or "manager_of" in args:
            key = "direct_reports_of" if "direct_reports_of" in args else "manager_of"
            anchor = self._match(str(args[key]))
            if anchor is None:
                raise not_found(str(args[key]))
            relation = key
            if key == "direct_reports_of":
                refs = [r for r, v in ORG.items() if v[3] == anchor]
            else:
                refs = [m] if (m := ORG[anchor][3]) else []
        else:
            q = str(args.get("query", "")).lower()
            refs = [r for r, v in ORG.items() if q in " ".join((v[0], v[1], v[4])).lower()]
        return {
            "relation": relation,
            "anchor": _named(anchor) if anchor else None,
            "summary": f"{len(refs)} người phù hợp.",
            "people": [_row(r) for r in refs],
            "next_cursor": None,
            "total": len(refs),
        }

    def person_details(self, ctx: CallContext, ref: str) -> Data | None:
        if ref not in ORG:
            return None
        return {
            **_row(ref),
            "email": ORG[ref][4],
            "phone": ORG[ref][5],
            "direct_reports": [_named(r) for r, v in ORG.items() if v[3] == ref],
        }


class MaskingPii(FakePii):
    """Client `cloud` chỉ thấy token cho email/điện thoại (MCP không trả giá trị thật cho agent)."""

    FIELDS = frozenset({"email", "phone", "national_id"})

    def outbound(self, ctx: CallContext, tool: str, data: Data) -> PiiProjection:
        if ctx.client_class is not ClientClass.CLOUD:
            return PiiProjection(data, "full")
        masked: set[str] = set()

        def walk(x: Any) -> Any:
            if isinstance(x, dict):
                out = {}
                for k, v in x.items():
                    if k in self.FIELDS and isinstance(v, str):
                        out[k] = f"[PII:{zlib.crc32(k.encode()) & 0xFFFFFF:06x}]"
                        masked.add(k)
                    else:
                        out[k] = walk(v)
                return out
            return [walk(v) for v in x] if isinstance(x, list) else x

        return PiiProjection(walk(data), "masked", tuple(sorted(masked)))


class PagedRecords(FakeRecords):
    """`search` có lọc `query`, `where.state`, `model_id`, phân trang bằng cursor `c<offset>`."""

    def search(self, ctx: CallContext, args: Args) -> Data:
        rows = [
            r
            for r in self.s.records.values()
            if r.model_id == args.get("model_id", r.model_id)
            and str(args.get("query", "")).lower() in r.title.lower()
        ]
        state = (args.get("where") or {}).get("state")
        if isinstance(state, str):
            rows = [r for r in rows if r.state == state]
        off = int(str(args["cursor"])[1:]) if args.get("cursor") else 0
        limit = int(args.get("limit", 20))
        nxt = f"c{off + limit}" if off + limit < len(rows) else None
        return {
            "items": [r.summary() for r in rows[off : off + limit]],
            "next_cursor": nxt,
            "total": len(rows),
        }


def seed_records(world: Any, n: int = 12) -> None:
    states = ["draft", "in_review", "approved"]
    for i in range(n):
        world.store.add(
            Rec(
                f"advance_request:bt-{i:03d}",
                "advance_request",
                f"Tạm ứng công tác đợt {i + 1}",
                states[i % 3],
                1,
                {"amount": str(1_000_000 * (i + 1)), "purpose": f"Công tác {i + 1}"},
                "sub-viet",
            )
        )


@dataclass
class AppsHarness(Harness):
    bundle: Any = None


def build_apps_harness(
    xt_origin: str = APPS_BASE,
    *,
    profile: Profile = Profile.FULL,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
    tokens: dict[str, Any] | None = None,
) -> AppsHarness:
    world = make_world()
    seed_records(world)
    world.services = replace(
        world.services,
        directory=OrgDirectory(),
        pii=MaskingPii(),
        records=PagedRecords(world.store),
    )
    world.pii = world.services.pii  # type: ignore[assignment]
    world.records = world.services.records  # type: ignore[assignment]
    dispatcher = make_dispatcher(world, confirm_base_url=xt_origin)
    bundle = register_apps(
        xt_origin=xt_origin,
        dispatcher=dispatcher,
        people=world.services.directory,  # type: ignore[arg-type]
    )
    verifier = FakeVerifier(
        tokens
        if tokens is not None
        else {"tok-mai": principal("sub-mai"), "tok-viet": principal("sub-viet")}
    )
    server = XtMcpServer(
        dispatcher,
        http_caller,
        prompts=default_prompts(),
        profile=profile,
        classify=ClientClassifier(frozenset({"goose-local"})),
        ui_resources=bundle.ui_resources,
        app_tools=bundle.app_tools,
    )
    app = build_http_app(
        server,
        IdentityHttpAuth(rs_settings(), verifier),
        mcp_path="/mcp",
        allowed_hosts=allowed_hosts or ["xt.example.test"],
        allowed_origins=allowed_origins,
    )
    return AppsHarness(world, verifier, app, server, bundle=bundle)
