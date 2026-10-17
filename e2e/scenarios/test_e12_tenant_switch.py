# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E12 (issue #7): đổi tenant trên Desk bằng đăng nhập lại có gợi ý Organization, trên Keycloak THẬT.

Người dùng test do kịch bản tự tạo (xóa khi xong): `e12.dual` là thành viên cả hai Organization của demo-seed
(binhminh, hoamai) và có person xT ở cả hai; `e12.single` chỉ thuộc binhminh. Kiểm:
  (a) client xt-desk-agent thật sự nhận scope `organization` và token theo `organization:<alias>` mang đúng một tổ chức;
  (b) user không là thành viên tổ chức được chọn thì thất bại sạch (không có token tổ chức đó);
  (c) post_logout_redirect_uri của Desk đã đăng ký (cấu hình Admin + hành vi end_session thật);
  (d) đổi tenant trong container Desk thật (Playwright): đăng xuất RP -> authorize organization:<to> -> token tenant mới,
      màn hình không còn dữ liệu tenant cũ; và token cũ bị Keycloak/xt-api thu hồi (phía server).
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

import pytest
from xte2e import desk, dpop, mcpc, net, oidc, seed
from xte2e.config import ROOT, _password, settings
from xte2e.kcadmin import Admin
from xte2e.report import Checks, Evidence

SCENARIO = "E12"
pytestmark = pytest.mark.e2e

A, B = "binhminh", "hoamai"
AGENT = "xt-desk-agent"
PERSON = {A: "NV-0019", B: "NV-0009"}  # nhân sự không có đăng nhập trong demo-seed
PERSON_SINGLE = "NV-0018"  # chỉ ở binhminh


class Sw:
    """Người dùng và Admin REST của kịch bản."""

    def __init__(self, world: Any) -> None:
        self.world = world
        self.kc = Admin()
        self.kc.wait_ready()
        suffix = secrets.token_hex(2)
        self.dual = {"username": f"e12.dual.{suffix}", "password": _password(), "totp": None}
        self.single = {"username": f"e12.single.{suffix}", "password": _password(), "totp": None}

    def user(self, u: dict[str, Any], orgs: list[str]) -> None:
        uid = self.kc.upsert_user(
            {
                "username": u["username"],
                "email": f"{u['username']}@e12.example",
                "firstName": "E12",
                "lastName": u["username"].split(".")[1],
            },
            u["password"],
            None,
        )
        self.kc.set_realm_roles(uid, ["employee"])
        for alias in orgs:
            self.kc.add_member(str(self.world.tenants[alias]["org_id"]), uid)
        u["sub"] = uid

    def link(self, alias: str, extra: dict[str, str] | None) -> None:
        """`xt org import --force` thật: gắn tài khoản Keycloak với person xT của tenant (hoặc khôi phục)."""
        t = self.world.tenants[alias]
        subjects = {**t["links"]["subjects"], **(extra or {})}
        seed.import_org(alias, Path(t["org_file"]), seed.links_for(subjects), force=True)

    def setup(self) -> None:
        self.user(self.dual, [A, B])
        self.user(self.single, [A])
        self.link(A, {PERSON[A]: self.dual["sub"], PERSON_SINGLE: self.single["sub"]})
        self.link(B, {PERSON[B]: self.dual["sub"]})

    def teardown(self) -> None:
        errs: list[str] = []
        for alias in (A, B):
            try:
                self.link(alias, None)
            except BaseException as e:
                errs.append(f"link {alias}: {e}")
        for u in (self.dual, self.single):
            try:
                self.kc.delete_user(u["username"])
            except BaseException as e:
                errs.append(f"user {u['username']}: {e}")
        if errs:
            pytest.fail("; ".join(errs), pytrace=False)


@pytest.fixture(scope="module")
def sw(world):  # type: ignore[no-untyped-def]
    s = Sw(world)
    try:
        s.setup()
        yield s
    finally:
        s.teardown()


def agent_login(user: dict[str, Any], scope: str, browser: oidc.Browser | None = None):  # type: ignore[no-untyped-def]
    b = browser or oidc.Browser()
    key = dpop.for_client(AGENT)
    tok = oidc.login(
        b,
        AGENT,
        f"{settings().desk}/auth/callback",
        scope,
        user["username"],
        user["password"],
        None,
        key=key,
    )
    return b, key, tok


def orgs_of(jwt: str) -> list[str]:
    o = oidc.claims(jwt).get("organization") or []
    return sorted(o if isinstance(o, list) else o.keys())


def scope_for(alias: str) -> str:
    """Scope Desk gửi khi đổi tenant (DESK_OIDC_ORG_SCOPE=mcp-xt): scope mang mapper Organization kèm alias."""
    return f"openid mcp-xt:{alias}"


def test_a_organization_scope(ev: Evidence, sw: Sw) -> None:
    c = Checks(ev)
    cid = sw.kc.client(AGENT)["id"]  # type: ignore[index]
    names = {
        k: [s["name"] for s in sw.kc.get(f"/clients/{cid}/{k}-client-scopes")]
        for k in ("default", "optional")
    }
    ev.step("client scopes xt-desk-agent", names)
    c.check(
        "organization" in names["default"] + names["optional"],
        "xt-desk-agent được gán client scope `organization` (Admin REST)",
    )
    resource = f"https://{settings().hosts['mcp']}/mcp"
    for alias in (
        B,
        A,
        B,
        A,
        B,
        A,
    ):  # lặp: lỗi thứ tự của Keycloak là ngẫu nhiên, không phải mỗi lần đều lộ
        b, _, tok = agent_login(sw.dual, scope_for(alias))
        cl = oidc.claims(tok.access)
        ev.step(
            f"token {scope_for(alias)}",
            {
                "organization": cl.get("organization"),
                "scope": cl.get("scope"),
                "azp": cl.get("azp"),
            },
        )
        c.check(
            orgs_of(tok.access) == [alias],
            f"{scope_for(alias)} -> claim organization == [{alias}]",
        )
        c.check(
            f"mcp-xt:{alias}" in cl.get("scope", "").split() and b.picked_org is None,
            "Keycloak chấp nhận scope: có trong claim scope, không hiện trang chọn tổ chức",
        )
        c.check(
            cl.get("azp") == AGENT and resource in oidc.claims(tok.access).get("aud", []),
            "azp = xt-desk-agent, aud = resource MCP",
        )
    # Ghi nhận (không khẳng định): dạng cũ `mcp-xt` trơn + `organization:<alias>` xin hai scope mang mapper Organization,
    # Keycloak 26.6.3 chọn theo thứ tự nội bộ nên bỏ qua alias hoặc cấp token không có tổ chức.
    old: list[Any] = []
    for _ in range(4):
        b, _, tok = agent_login(sw.dual, f"openid mcp-xt organization:{A}")
        old.append({"organization": orgs_of(tok.access), "picked_org": b.picked_org})
    ev.step("đối chứng: dạng cũ `openid mcp-xt organization:<alias>`", old)
    c.done()


def mcp_post(token: str, key: Any, method: str = "tools/list") -> Any:
    """POST /mcp thô (có DPoP proof khi phiên gắn khóa): kiểm mã HTTP mà không qua client MCP."""
    url = settings().mcp_url
    with net.client() as h:
        return h.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": method},
            headers={
                "Accept": "application/json, text/event-stream",
                **dpop.auth_headers(key, "POST", url, token),
            },
        )


def test_b_non_member_fails_cleanly(ev: Evidence, sw: Sw) -> None:
    """User chỉ thuộc binhminh xin tenant hoamai (không phải thành viên): Keycloak 26.6.3 vẫn đăng nhập xong nhưng KHÔNG cấp
    scope `mcp-xt` (không aud MCP, không claim organization); alias không tồn tại thì lỗi `invalid_scope`. Cả hai đều
    không mở được tenant nào; xt-api từ chối token đó."""
    c = Checks(ev)
    # Đối chứng: cùng user xin đúng tổ chức của mình thì dùng được MCP.
    _, key_ok, ok = agent_login(sw.single, scope_for(A))
    good = mcpc.call(ok.access, "inspect_workspace", {}, key=key_ok)
    ev.step("đối chứng mcp-xt:binhminh", {"organization": orgs_of(ok.access), "ok": good.get("ok")})
    c.check(
        orgs_of(ok.access) == [A] and good.get("ok") is True,
        "đối chứng: user xin đúng tổ chức của mình -> token [binhminh] và inspect_workspace ok",
    )
    resource = f"https://{settings().hosts['mcp']}/mcp"
    for _ in range(3):
        b = oidc.Browser()
        _, key, tok = agent_login(sw.single, scope_for(B), b)
        cl = oidc.claims(tok.access)
        ev.step(
            "user chỉ thuộc binhminh xin mcp-xt:hoamai",
            {
                "organization": cl.get("organization"),
                "scope": cl.get("scope"),
                "aud": cl.get("aud"),
                "trail": b.trail[-4:],
            },
        )
        c.check(all(" -> 5" not in t for t in b.trail), "Keycloak không trả lỗi 5xx")
        c.check(B not in orgs_of(tok.access), f"token không mang tổ chức {B}")
        c.check(
            resource not in cl.get("aud", []) and "mcp-xt" not in cl.get("scope", "").split(),
            "token không được cấp scope/audience MCP",
        )
        r = mcp_post(tok.access, key)
        c.check(r.status_code in (401, 403), f"xt-api từ chối token đó (HTTP {r.status_code})")
    b2 = oidc.Browser()
    try:
        agent_login(sw.single, scope_for("khong-ton-tai"), b2)
        c.check(False, "alias không tồn tại phải bị Keycloak từ chối")
    except oidc.LoginError as e:
        ev.step("alias không tồn tại", {"error": str(e)[:200]})
        c.check("invalid_scope" in str(e), "alias không tồn tại -> invalid_scope (thất bại sạch)")
    c.done()


def introspect(token: str) -> dict[str, Any]:
    e = settings().env
    with net.client() as h:
        r = h.post(
            f"{settings().issuer}/protocol/openid-connect/token/introspect",
            data={"token": token},
            auth=(
                e.get("XT_IDP_INTROSPECTION_CLIENT_ID", "xt-api-introspect"),
                e["XT_IDP_INTROSPECTION_CLIENT_SECRET"],
            ),
        )
    r.raise_for_status()
    out: dict[str, Any] = r.json()
    return out


def rp_logout(b: oidc.Browser, tok: oidc.Tokens, redirect: str) -> Any:
    return b.c.get(
        f"{settings().issuer}/protocol/openid-connect/logout",
        params={
            "client_id": AGENT,
            "id_token_hint": tok.id_token or "",
            "post_logout_redirect_uri": redirect,
        },
    )


def test_c_post_logout_redirect_registered(ev: Evidence, sw: Sw) -> None:
    c = Checks(ev)
    desk_home = f"{settings().desk}/"
    attr = (sw.kc.client(AGENT) or {}).get("attributes", {}).get("post.logout.redirect.uris", "")
    ev.step("post.logout.redirect.uris", attr)
    c.check(
        desk_home in attr.split("##"), f"{desk_home} đã đăng ký cho client {AGENT} (Admin REST)"
    )
    b, _, tok = agent_login(sw.dual, scope_for(A))
    r = rp_logout(b, tok, desk_home)
    ev.step(
        "end_session với URI đã đăng ký",
        {"status": r.status_code, "location": r.headers.get("location")},
    )
    c.check(
        r.status_code in (302, 303) and r.headers.get("location") == desk_home,
        f"end_session chuyển về {desk_home} (HTTP {r.status_code})",
    )
    b2, _, tok2 = agent_login(sw.dual, scope_for(A))
    evil = "https://evil.example/"
    r2 = rp_logout(b2, tok2, evil)
    loc = r2.headers.get("location", "")
    ev.step("end_session với URI lạ", {"status": r2.status_code, "location": loc})
    c.check(
        not loc.startswith(evil),
        "URI chưa đăng ký không được chuyển hướng tới (không open redirect)",
    )
    c.done()


def test_d_old_token_revoked_by_logout(ev: Evidence, sw: Sw) -> None:
    """Phía server của đổi tenant: sau RP-logout phiên SSO bị hủy nên token tenant cũ không còn active
    (introspection, chính lời gọi mà RevocationGuard L2+ dùng) và refresh token cũ không đổi được token mới.
    Token của Desk bị ràng khóa DPoP của trình duyệt (khóa bị xoay khi đổi tenant) nên phát lại từ ngoài vốn
    đã thiếu proof; vì vậy kiểm bằng phiên có khóa của harness, đi đúng cùng đường đăng nhập/đăng xuất.
    Không khẳng định `/mcp` 401: thao tác L0/L1 chấp nhận token tới `exp` (docs/identity.md, thu hồi theo
    mức rủi ro), còn `/studio-api` và `/llm` không được mount trên stack minimal (404)."""
    c = Checks(ev)
    b, key, tok = agent_login(sw.dual, scope_for(A))
    live = introspect(tok.access)
    r = mcp_post(tok.access, key)
    ev.step("trước đăng xuất", {"active": live.get("active"), "mcp": r.status_code})
    c.check(live.get("active") is True, "đối chứng: introspection active=true trước khi đăng xuất")
    c.check(r.status_code == 200, f"đối chứng: /mcp nhận token (HTTP {r.status_code})")
    c.check(
        oidc.claims(tok.access)["exp"] > time.time() + 30,
        "token còn hạn (không phải hết hạn tự nhiên)",
    )
    out = rp_logout(b, tok, f"{settings().desk}/")
    c.check(
        out.status_code in (302, 303), f"RP-initiated logout thành công (HTTP {out.status_code})"
    )
    dead = introspect(tok.access)
    r2 = mcp_post(tok.access, key)
    refreshed = "đổi được token mới"
    try:
        oidc.refresh(b.c, AGENT, tok, key)
    except oidc.LoginError as e:
        refreshed = str(e)[:160]
    ev.step(
        "sau đăng xuất",
        {"active": dead.get("active"), "mcp_l0_l1": r2.status_code, "refresh": refreshed},
    )
    c.check(dead.get("active") is False, "introspection: token cũ active=false")
    c.check(refreshed != "đổi được token mới", "refresh token của phiên cũ bị từ chối")
    c.done()


def hr_markers(alias: str) -> list[str]:
    folder = {A: "binh-minh", B: "hoa-mai"}[alias]
    hr = json.loads((ROOT / "seed" / folder / "org.json").read_text(encoding="utf-8"))
    return [e["full_name"] for e in hr["employees"]]


def test_e_desk_switch_container(ev: Evidence, sw: Sw) -> None:
    """Đổi tenant trong container Desk thật (Playwright): bộ chọn -> đăng xuất RP -> đăng nhập lại tenant B."""
    c = Checks(ev)
    only_a = [m for m in hr_markers(A) if m not in hr_markers(B)]
    only_b = [m for m in hr_markers(B) if m not in hr_markers(A)]
    cfg = {
        "from": A,
        "to": B,
        "area": "H",
        "action": "Quản lý của tôi",
        "carrier": "mcp-xt",
        "from_markers": only_a,
        "to_markers": only_b,
    }
    rc, res, log = desk.run("switch", sw.dual, timeout=300, extra=cfg)
    old = res.pop("old_access", None)
    ev.step(
        "desk_real.mjs switch",
        {k: res.get(k) for k in ("checks", "sw", "http", "notes")},
    )
    for ch in res.get("checks", []):
        c.check(ch["ok"], ch["msg"])
    if not res:
        c.check(False, f"desk_real.mjs không chạy được: {log[-400:]}")
    c.check(rc == 0, f"desk_real.mjs thoát mã 0 (rc={rc})")
    if old:
        dead = introspect(old)
        ev.step("introspection token tenant cũ của Desk", {"active": dead.get("active")})
        c.check(
            dead.get("active") is False,
            "token tenant cũ của Desk bị Keycloak thu hồi sau khi đổi (active=false)",
        )
    c.done()
