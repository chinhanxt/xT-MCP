# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E8: Tầng 1 headless, KHÔNG qua MCP: SSO (discovery/JWKS), Gateway (401 không token), Data
(PostgREST + Accept-Profile), Workflow (`/workflow/v1`: definitions, BPMN XML, W1 start -> inbox ->
decision 428 + confirm_url -> người xác nhận), Files (`/files` upload + tải về), và
`docs/headless-demo.sh` (kể cả `--get-token data|workflow` PKCE loopback) exit 0 trên stack thật."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from xte2e import net, oidc
from xte2e.config import HEADLESS_CLIENT, ROOT
from xte2e.flows import A, B, DEPT_HEAD, DIRECTOR, REQUESTER, advance, dumps, key
from xte2e.report import Checks, Evidence

SCENARIO = "E8"
pytestmark = pytest.mark.e2e


def _cli(world, u, kind: str, port: int | None = None) -> str:  # type: ignore[no-untyped-def]
    # RFC 8252: client xt-cli khai `http://127.0.0.1/callback`; Keycloak chấp nhận mọi cổng loopback.
    redirect = f"http://127.0.0.1:{port or 8765}/callback"
    return world.token(u, client=HEADLESS_CLIENT, scope=f"openid xt-{kind}", redirect=redirect)


def test_sso_gateway_data(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    with net.client() as h:
        disc = h.get(f"{world.s.issuer}/.well-known/openid-configuration")
        jwks = h.get(f"{world.s.issuer}/protocol/openid-connect/certs")
        ev.step(
            "SSO",
            {
                "discovery": disc.status_code,
                "issuer": disc.json().get("issuer"),
                "jwks_keys": len(jwks.json().get("keys", [])),
            },
        )
        c.check(
            disc.json().get("issuer") == world.s.issuer and jwks.json().get("keys"),
            "SSO: discovery + JWKS qua gateway",
        )
        anon = {
            p: h.get(f"{world.s.api}{p}").status_code
            for p in ("/data/advance_request", "/workflow/v1/definitions", "/files/v1")
        }
        ev.step("không token", anon)
        for p, code in anon.items():
            c.check(code == 401, f"Gateway/dịch vụ: {p} không token -> 401 (thực tế {code})")
        u = world.by_key(A, REQUESTER)
        tok = _cli(world, u, "data", 41234)
        r = h.get(
            f"{world.s.api}/data/advance_request",
            headers={
                "Authorization": f"Bearer {tok}",
                "Accept-Profile": world.tenants[A]["profile"],
            },
        )
        ev.step(
            "GET /data/advance_request",
            {
                "status": r.status_code,
                "rows": len(r.json()) if r.status_code == 200 else r.text[:200],
            },
        )
        c.check(
            r.status_code == 200, f"Data: /data/<model> + Accept-Profile -> 200 ({r.status_code})"
        )
    tok2 = world.session(
        u,
        client=HEADLESS_CLIENT,
        scope="openid xt-data",
        redirect="http://127.0.0.1:59999/callback",
    )
    c.check(
        bool(tok2.access()),
        "xt-cli: redirect loopback cổng bất kỳ được Keycloak chấp nhận (RFC 8252)",
    )
    c.done()


def test_workflow_rest_w1(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    req, dh, _dr = world.by_key(A, REQUESTER), world.by_key(A, DEPT_HEAD), world.by_key(A, DIRECTOR)
    api = f"{world.s.api}/workflow/v1"
    tok = _cli(world, req, "workflow")
    cl = oidc.claims(tok)
    ev.step("token workflow", {k: cl.get(k) for k in ("aud", "azp", "scope", "organization")})
    c.check(
        "xt-workflow"
        in ([cl.get("aud")] if isinstance(cl.get("aud"), str) else cl.get("aud") or []),
        "token xt-cli mang aud xt-workflow",
    )
    H = {"Authorization": f"Bearer {tok}"}
    with net.client(timeout=60) as h:
        defs = h.get(f"{api}/definitions", headers=H)
        ev.step("GET /definitions", {"status": defs.status_code, "body": defs.text[:400]})
        if defs.status_code in (404, 502, 503):
            ev.gap("W5 REST /workflow/v1 trên stack", f"GET /definitions -> {defs.status_code}")
        c.check(
            defs.status_code == 200 and "proposal_approval" in defs.text,
            "definitions có proposal_approval",
        )
        bpmn = h.get(f"{api}/definitions/proposal_approval/bpmn", headers=H)
        ev.step(
            "GET BPMN",
            {
                "status": bpmn.status_code,
                "ctype": bpmn.headers.get("content-type"),
                "head": bpmn.text[:160],
            },
        )
        c.check(
            bpmn.status_code == 200
            and "bpmn" in bpmn.text[:500].lower()
            and "BPMNDiagram" in bpmn.text,
            "BPMN 2.0 XML có BPMNDI",
        )
        idem = key("wf")
        body = {
            "definition_id": "proposal_approval",
            "note": "Công tác Đà Nẵng (REST)",
            "values": advance(8_000_000),
        }
        st = h.post(f"{api}/instances", headers={**H, "Idempotency-Key": idem}, json=body)
        again = h.post(f"{api}/instances", headers={**H, "Idempotency-Key": idem}, json=body)
        ev.step(
            "POST /instances (+ gửi lại cùng key)",
            {"status": st.status_code, "again": again.status_code, "body": st.text[:600]},
        )
        c.check(
            st.status_code == 201 and again.status_code == 200,
            f"W1 start 201, gửi lại 200 ({st.status_code}/{again.status_code})",
        )
        if st.status_code != 201:
            c.done()
        data = st.json().get("data") or {}
        ref = (data.get("request") or {}).get("ref") or data.get("ref")
        inst = st.headers.get("location", "")
        tdh = _cli(world, dh, "workflow")
        inbox = h.get(f"{api}/inbox?kinds=approval", headers={"Authorization": f"Bearer {tdh}"})
        items = (
            (inbox.json().get("data") or {}).get("items", []) if inbox.status_code == 200 else []
        )
        item = next((i for i in items if i.get("ref") == ref), None)
        ev.step("GET /inbox trưởng phòng", {"status": inbox.status_code, "found": bool(item)})
        c.check(item is not None, "đề nghị có trong inbox trưởng phòng (REST)")
        if not item:
            c.done()
        task = item.get("task_id") or item["ref"]

        def decide(tk: str, rev: int, who: dict) -> tuple[int, dict]:  # type: ignore[type-arg]
            r = h.post(
                f"{api}/tasks/{task}/decision",
                headers={
                    "Authorization": f"Bearer {tk}",
                    "Idempotency-Key": f"{idem}-{who['key']}",
                },
                json={"decision": "approve", "expected_revision": rev},
            )
            return r.status_code, r.json() if r.headers.get("content-type", "").startswith(
                "application/"
            ) else {}

        code, out = decide(tdh, item["revision"], dh)
        ev.step("POST decision (agent)", {"status": code, "body": out})
        url = ((out.get("error") or {}).get("details") or {}).get("confirm_url", "")
        c.check(
            code == 428 and url.startswith(f"{world.s.desk}/desk/confirm/"),
            f"agent -> 428 + confirm_url ({code})",
        )
        if url:
            ev.step("trưởng phòng xác nhận", world.confirm(url, dh))
            code2, out2 = decide(tdh, item["revision"], dh)
            ev.step("decision sau xác nhận", {"status": code2, "body": out2})
            c.check(code2 == 200, f"decision hoàn tất sau xác nhận ({code2})")
        if inst:
            ir = h.get(inst if inst.startswith("http") else f"{world.s.api}{inst}", headers=H)
            ev.step("GET /instances/{id}", {"status": ir.status_code, "body": ir.text[:400]})
            c.check(
                ir.status_code == 200 and "director" in ir.text,
                "instance chuyển sang bước giám đốc",
            )
    c.done()


def test_files(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    u = world.by_key(A, REQUESTER)
    tok = _cli(world, u, "files")  # aud xt-files riêng (docs/decisions.md "REST /files/v1")
    body = b"xT e2e"
    with net.client() as h:
        r = h.post(
            f"{world.s.api}/files/v1",
            headers={"Authorization": f"Bearer {tok}"},
            files={"file": ("e2e.txt", body, "text/plain")},
        )
        ev.step("POST /files/v1", {"status": r.status_code, "body": r.text[:300]})
        if r.status_code in (404, 405, 501, 502, 503) or "unavailable" in r.text:
            ev.gap(
                "W8 dữ liệu phi cấu trúc /files (SeaweedFS ở profile minimal)",
                f"POST /files/v1 -> {r.status_code}",
            )
        assert r.status_code in (200, 201), r.text[:300]
        fid = r.json()["data"]["file"]["id"]
        dl = h.get(
            f"{world.s.api}/files/v1/{fid}/content", headers={"Authorization": f"Bearer {tok}"}
        )
        ev.step("GET /files/v1/{id}/content", {"status": dl.status_code, "bytes": len(dl.content)})
    assert dl.status_code == 200 and dl.content == body, dl.text[:300]


def _get_token(kind: str, user: dict, proxy: net.HostProxy, out: Path, ev: Evidence) -> int:  # type: ignore[type-arg]
    """Lái `headless-demo.sh --get-token` headless: script in URL + nghe 127.0.0.1; harness đăng nhập
    Keycloak thật (qua proxy -> APISIX) rồi đi theo redirect loopback về script."""
    env = {**os.environ, "XT_AUTH_BASE": proxy.base, "XT_REALM": world_realm()}
    # Script chạy `python3 -I` (bỏ qua PYTHONUNBUFFERED): cấp pty để stdout line-buffered, đọc được URL ngay.
    cmd = f"bash {ROOT / 'docs' / 'headless-demo.sh'} --get-token {kind} {out}"
    p = subprocess.Popen(
        ["script", "-qfec", cmd, "/dev/null"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    assert p.stdout is not None
    url = ""
    lines: list[str] = []
    deadline = time.time() + 30
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            break
        lines.append(line.rstrip())
        if line.strip().startswith("http"):
            url = line.strip()
            break
    if not url:
        p.kill()
        ev.step(f"--get-token {kind}", lines)
        return p.wait(10) or 1
    b = oidc.Browser()
    # "Trình duyệt của người dùng" mở URL qua đúng hostname công khai (proxy chỉ là đường của script).
    url = url.replace(proxy.base, f"https://{proxy.host}", 1)
    redirect = re.search(r"redirect_uri=([^&]+)", url)
    from urllib.parse import unquote

    stop = unquote(redirect.group(1)) if redirect else "http://127.0.0.1"
    step = b.get(url, stop=stop)
    step = b.login_pages(step, user["username"], user["password"], user.get("totp"), stop=stop)
    b.c.get(step.url)  # trình duyệt về loopback của script
    try:
        rest, _ = p.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        rest, _ = p.communicate()
    ev.step(f"--get-token {kind}", [*lines[:1], *rest.splitlines()[-2:]])
    return p.returncode


def world_realm() -> str:
    from xte2e.config import settings

    return settings().realm


def test_headless_demo_script(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    u, dh = world.by_key(A, REQUESTER), world.by_key(A, DEPT_HEAD)
    d = Path(tempfile.mkdtemp(prefix="xt-e2e-tok-"))
    try:
        with net.HostProxy(world.s.hosts["auth"]) as proxy:
            rc_d = _get_token("data", u, proxy, d / "data_token", ev)
            rc_w = _get_token("workflow", u, proxy, d / "wf_token", ev)
            rc_a = _get_token("workflow", dh, proxy, d / "apr_token", ev)
            rc_f = _get_token("files", u, proxy, d / "files_token", ev)
        c.check(
            rc_d == 0 and (d / "data_token").exists(), f"--get-token data (PKCE loopback) rc={rc_d}"
        )
        c.check(rc_w == 0 and rc_a == 0, f"--get-token workflow rc={rc_w}/{rc_a}")
        c.check(rc_f == 0, f"--get-token files rc={rc_f}")
        start = d / "start.json"
        start.write_text(
            dumps(
                {
                    "definition_id": "proposal_approval",
                    "note": "headless-demo",
                    "values": advance(6_000_000),
                }
            ),
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "XT_ACCESS_TOKEN_FILE": str(d / "data_token"),
            "XT_WORKFLOW_TOKEN_FILE": str(d / "wf_token"),
            "XT_WORKFLOW_APPROVER_TOKEN_FILE": str(d / "apr_token"),
            "XT_FILES_TOKEN_FILE": str(d / "files_token"),
            "XT_DATA_MODEL": "advance_request",
            "XT_DATA_PROFILE": world.tenants[A]["profile"],
            "XT_DATA_FOREIGN_PROFILE": world.tenants[B]["profile"],
            "XT_WORKFLOW_START_FILE": str(start),
        }
        # URL https mặc định của script (như production); TLS cục bộ thay Cloudflare, -k vì cert tự ký.
        with net.TlsGateway() as tls:
            env["XT_CURL_OPTS"] = (
                f"-k --connect-to ::127.0.0.1:{tls.port} -H CF-Connecting-IP:{net._IP}"
            )
            r = subprocess.run(
                ["bash", str(ROOT / "docs" / "headless-demo.sh")],
                capture_output=True,
                text=True,
                env=env,
                timeout=180,
            )
        out = r.stdout.splitlines()
        ev.step(
            "headless-demo.sh", {"rc": r.returncode, "stdout": out[-40:], "stderr": r.stderr[-400:]}
        )
        c.check(
            r.returncode == 0,
            f"headless-demo.sh exit 0 (rc={r.returncode}; "
            f"{' / '.join(x.strip() for x in out if 'chưa triển khai' in x or 'LỖI' in x or 'THIẾU' in x)[:400]})",
        )
    finally:
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
    c.done()
