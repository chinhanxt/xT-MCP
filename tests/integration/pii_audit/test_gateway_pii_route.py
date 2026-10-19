# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""PIIR-01 (docs/archive/audit-pii-reveal.md): Desk POST `{DESK_XT_ORIGIN}/pii/reveal`, DESK_XT_ORIGIN =
host Desk, nhưng APISIX không có route `/pii/*` cho host Desk -> rơi vào `desk-static`
(nginx, 404/405).
Không cần docker: đọc template APISIX và mô phỏng chọn route (host + uri + priority)."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TMPL = ROOT / "deploy" / "apisix" / "apisix.yaml.tmpl"
DESK = "@@XT_DOMAIN_DESK@@"


def _routes() -> list[dict[str, object]]:
    text = TMPL.read_text(encoding="utf-8").split("\nroutes:", 1)[1]
    out = []
    for block in re.split(r"\n  - id:", text)[1:]:
        rid = block.split("\n", 1)[0].strip().split()[0]
        hosts = re.findall(r'"([^"]+)"', (re.search(r"hosts:\s*\[([^\]]*)\]", block) or [""])[0])
        uris = re.search(r"uris:\s*\[([^\]]*)\]", block)
        one = re.search(r'\n    uri:\s*"([^"]+)"', block)
        pats = re.findall(r'"([^"]+)"', uris.group(1)) if uris else ([one.group(1)] if one else [])
        prio = re.search(r"\n    priority:\s*(-?\d+)", block)
        nodes = re.findall(r'nodes:\s*\{"([^"]+)"', block)
        out.append({"id": rid, "hosts": hosts, "uris": pats,
                    "priority": int(prio.group(1)) if prio else 0, "nodes": nodes})  # fmt: skip
    return out


def _route_for(host: str, path: str) -> dict[str, object] | None:
    hits = [
        r for r in _routes()
        if host in r["hosts"]  # type: ignore[operator]
        and any(fnmatch.fnmatchcase(path, p) or path == p for p in r["uris"])  # type: ignore[attr-defined]
    ]  # fmt: skip
    return max(hits, key=lambda r: r["priority"]) if hits else None  # type: ignore[arg-type,return-value]


def test_parser_sees_known_desk_routes() -> None:
    assert _route_for(DESK, "/mcp")["id"] == "desk-mcp"  # type: ignore[index]
    assert _route_for(DESK, "/desk/confirm/x")["id"] == "desk-confirm"  # type: ignore[index]


def test_desk_host_routes_pii_reveal_to_xt_api() -> None:
    """PIIR-01 đã sửa: route `desk-pii` (host Desk, `/pii/*`) -> xt-api, trần body 4 KiB,
    limit-req theo IP."""
    r = _route_for(DESK, "/pii/reveal")
    assert r is not None and r["id"] == "desk-pii" and r["nodes"] == ["xt-api:8000"], r
    block = TMPL.read_text(encoding="utf-8").split("- id: desk-pii", 1)[1].split("\n  - id:", 1)[0]
    assert "max_body_size: 4096" in block and "limit-req:" in block
