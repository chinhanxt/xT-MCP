#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cấp scope headless riêng: xt-data (XT_DATA_CLIENTS) và xt-workflow (XT_WORKFLOW_CLIENTS, mặc định xt-cli).

Không gán scope cho toàn realm/DCR. Chạy sau provision.py; không bật password grant.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from envfile import load_env
from provision import DEPLOY, KC, mcp_client_ids, scope_id, set_client_scope, upsert_mapper


SCOPE = "xt-data"
WORKFLOW_SCOPE = "xt-workflow"
HEADLESS_SCOPES = (SCOPE, WORKFLOW_SCOPE)
CLIENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def parse_clients(raw: str, forbidden: list[str] = (), var: str = "XT_DATA_CLIENTS") -> list[str]:
    """Danh sách clientId tường minh, phân tách bằng dấu phẩy; không wildcard/trùng; không client MCP/Desk/IdP."""
    names = [c.strip() for c in raw.split(",") if c.strip()]
    bad = [c for c in names if not CLIENT_RE.match(c)]
    if bad:
        raise SystemExit(f"{var} có clientId không hợp lệ: {bad}")
    if len(set(names)) != len(names):
        raise SystemExit(f"{var} có clientId trùng")
    if not names:
        raise SystemExit(f"thiếu {var} (danh sách client được duyệt)")
    mcp = [c for c in names if c in forbidden]
    if mcp:
        raise SystemExit(f"{var} không được chứa client MCP/Desk/IdP: {mcp}")
    return names


def resolve_clients(kc: KC, realm: str, clients: list[str]) -> list[str]:
    """Kiểm HẾT client trước khi đổi bất kỳ thứ gì; trả về id nội bộ theo thứ tự đầu vào."""
    ids = []
    for client in clients:
        found = kc.get(f"/realms/{realm}/clients?clientId={quote(client, safe='')}")
        found = [c for c in found if c.get("clientId") == client]
        if len(found) != 1 or not found[0].get("enabled"):
            raise SystemExit(f"client data chưa tồn tại/đang tắt: {client}")
        ids.append(found[0]["id"])
    return ids


def all_clients(kc: KC, realm: str) -> list[dict]:
    """Đọc hết trang: client ngoài trang đầu vẫn phải bị thu hồi scope data."""
    clients = []
    first, size = 0, 200
    while True:
        page = kc.get(f"/realms/{realm}/clients?first={first}&max={size}")
        clients.extend(page)
        if len(page) < size:
            return clients
        first += size


def provision_data(kc: KC, realm: str, clients: list[str]) -> None:
    common = {"access.token.claim": "true", "id.token.claim": "false", "introspection.token.claim": "false"}
    provision_scope(kc, realm, SCOPE, "API dữ liệu xT chỉ đọc (aud xt-data)", [
        {"name": "data-audience", "protocolMapper": "oidc-audience-mapper",
         "config": {**common, "included.custom.audience": "xt-data"}},
        {"name": "data-role", "protocolMapper": "oidc-hardcoded-claim-mapper",
         "config": {**common, "claim.name": "data_role", "claim.value": "xt_web", "jsonType.label": "String"}},
        organization_mapper(),
    ], clients)


def organization_mapper() -> dict:
    common = {"access.token.claim": "true", "id.token.claim": "false", "introspection.token.claim": "false"}
    return {"name": "organization", "protocolMapper": "oidc-organization-membership-mapper",
            "config": {**common, "claim.name": "organization", "multivalued": "true", "jsonType.label": "String"}}


def provision_workflow(kc: KC, realm: str, clients: list[str]) -> None:
    common = {"access.token.claim": "true", "id.token.claim": "false", "introspection.token.claim": "false"}
    provision_scope(kc, realm, WORKFLOW_SCOPE, "REST workflow xT (aud xt-workflow)", [
        {"name": "workflow-audience", "protocolMapper": "oidc-audience-mapper",
         "config": {**common, "included.custom.audience": "xt-workflow"}},
        organization_mapper(),
    ], clients)


def assert_mcp_clean(kc: KC, realm: str) -> None:
    """Token MCP không được mang aud xt-data/xt-workflow: scope mcp-xt không có mapper audience đó và hai scope
    headless không nằm trong default/optional của realm."""
    sid = scope_id(kc, realm, "mcp-xt")
    if sid is not None:
        for m in kc.get(f"/realms/{realm}/client-scopes/{sid}/protocol-mappers/models"):
            cfg = m.get("config") or {}
            auds = {cfg.get("included.custom.audience"), cfg.get("included.client.audience")}
            if auds & set(HEADLESS_SCOPES):
                raise SystemExit(f"scope mcp-xt mang audience headless qua mapper {m.get('name')}")
    for name in HEADLESS_SCOPES:
        hid = scope_id(kc, realm, name)
        for kind in ("default", "optional"):
            if hid and any(x.get("id") == hid for x in kc.get(f"/realms/{realm}/default-{kind}-client-scopes")):
                raise SystemExit(f"scope {name} đang là realm {kind}")
    print("token MCP sạch: mcp-xt không có aud xt-data/xt-workflow; hai scope không phải realm default/optional")


def provision_scope(kc: KC, realm: str, scope: str, description: str, wanted: list[dict], clients: list[str]) -> None:
    SCOPE = scope  # noqa: N806 - tên dùng trong log
    ids = resolve_clients(kc, realm, clients)
    rep = {
        "name": SCOPE,
        "protocol": "openid-connect",
        "description": description,
        "attributes": {"include.in.token.scope": "true", "display.on.consent.screen": "false"},
    }
    sid = scope_id(kc, realm, SCOPE)
    if sid is None:
        kc.call("POST", f"/realms/{realm}/client-scopes", rep)
        sid = scope_id(kc, realm, SCOPE)
    else:
        kc.call("PUT", f"/realms/{realm}/client-scopes/{sid}", {**rep, "id": sid})
    if sid is None:
        raise SystemExit(f"Keycloak chưa tạo scope {SCOPE}")
    base = f"/realms/{realm}/client-scopes/{sid}"
    for mapper in wanted:
        upsert_mapper(kc, base, {"protocol": "openid-connect", **mapper})
    # Scope chỉ chứa đúng các mapper khai báo: mapper lạ (role, audience khác…) bị gỡ để không lọt claim/aud.
    keep = {m["name"] for m in wanted}
    for m in kc.get(f"{base}/protocol-mappers/models"):
        if m["name"] not in keep:
            kc.call("DELETE", f"{base}/protocol-mappers/models/{m['id']}", ok=(404,))
            print(f"scope {SCOPE}: đã gỡ mapper lạ {m['name']}")
    # Không bao giờ là scope mặc định của realm (DCR/client tự đăng ký không được nhận aud headless).
    for kind in ("default", "optional"):
        kc.call("DELETE", f"/realms/{realm}/default-{kind}-client-scopes/{sid}", ok=(404,))
    for cid in ids:
        set_client_scope(kc, realm, cid, sid, "optional")
    # Allowlist là tuyệt đối: client nào khác đang giữ scope (cấp tay/lần chạy cũ) bị thu hồi.
    revoked = 0
    for c in all_clients(kc, realm):
        if c["id"] in ids:
            continue
        for kind in ("default", "optional"):
            have = kc.get(f"/realms/{realm}/clients/{c['id']}/{kind}-client-scopes")
            if any(x["id"] == sid for x in have):
                kc.call("DELETE", f"/realms/{realm}/clients/{c['id']}/{kind}-client-scopes/{sid}", ok=(404,))
                revoked += 1
                print(f"scope {SCOPE}: đã thu hồi khỏi client {c.get('clientId')} ({kind})")
    print(f"scope {SCOPE}: optional cho {len(ids)} client đã duyệt, thu hồi {revoked}; không phải scope mặc định")


def main() -> None:
    env = {**load_env(DEPLOY / ".env"), **{k: v for k, v in os.environ.items() if k.startswith(("XT_", "KC_"))}}
    forbidden = mcp_client_ids(env)
    clients = parse_clients(env.get("XT_DATA_CLIENTS", ""), forbidden)
    wf_clients = parse_clients(env.get("XT_WORKFLOW_CLIENTS") or env.get("XT_CLI_CLIENT_ID") or "xt-cli", forbidden, "XT_WORKFLOW_CLIENTS")
    base = env.get("KC_ADMIN_URL") or f"http://127.0.0.1:{env.get('KC_HTTP_PORT') or '18080'}"
    u = urlparse(base)
    if u.scheme != "https" and u.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("KC_ADMIN_URL phải là https hoặc loopback (mật khẩu admin không đi qua http công khai)")
    kc = KC(base, env.get("KC_BOOTSTRAP_ADMIN_USERNAME") or "admin", env["KC_BOOTSTRAP_ADMIN_PASSWORD"])
    try:
        kc.login()
        realm = env.get("XT_REALM") or "xt"
        provision_data(kc, realm, clients)
        provision_workflow(kc, realm, wf_clients)
        assert_mcp_clean(kc, realm)
    except (OSError, KeyError, ValueError) as e:  # mạng/đăng nhập/JSON hỏng: báo gọn, không traceback
        raise SystemExit(f"provision_data: lỗi gọi Keycloak ({type(e).__name__}): {e}") from None


if __name__ == "__main__":
    main()
