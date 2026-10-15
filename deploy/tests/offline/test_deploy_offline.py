# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Test offline (loopback, không Docker/Keycloak thật) cho provision_data.py và fetch_jwks.py.

    python3 -m unittest discover -s deploy/tests/offline -t deploy/tests/offline
"""
from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2]
PASSWORD = "S3cret-admin-pw"


def load(name: str, rel: str):
    sys.path[:0] = [str(DEPLOY / "scripts"), str(DEPLOY / "keycloak")]
    spec = importlib.util.spec_from_file_location(name, DEPLOY / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeKC(BaseHTTPRequestHandler):
    state: dict = {}
    log: list = []

    def log_message(self, *a):  # im lặng
        pass

    def _send(self, code, body=None):
        raw = b"" if body is None else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n)

    def _route(self, method):
        try:
            self._route2(method)
        except Exception as e:  # noqa: BLE001 - mô phỏng Keycloak 500
            self._send(500, {"error": type(e).__name__})

    def _route2(self, method):
        st, path = self.state, self.path
        raw = self._body()
        body = json.loads(raw) if raw and "token" not in path else None
        if path.endswith("/protocol/openid-connect/token"):
            self.log.append(("LOGIN", "", None))
            return self._send(200, {"access_token": "tok"})
        if self.headers.get("Authorization") != "Bearer tok":
            return self._send(401, {})
        p = path.split("/admin", 1)[1]
        self.log.append((method, p, body))
        m = re.fullmatch(r"/realms/xt/clients\?clientId=(.*)", p)
        if m and method == "GET":
            from urllib.parse import unquote

            cid = unquote(m.group(1))
            return self._send(200, [c for c in st["clients"].values() if c["clientId"] == cid])
        m = re.fullmatch(r"/realms/xt/clients\?first=(\d+)&max=(\d+)", p)
        if m:
            first, size = map(int, m.groups())
            return self._send(200, list(st["clients"].values())[first:first + size])
        if p == "/realms/xt/client-scopes":
            if method == "GET":
                return self._send(200, [{"id": i, "name": s["name"]} for i, s in st["scopes"].items()])
            if method == "POST":
                st["scopes"]["sc-new"] = {**body, "id": "sc-new", "mappers": {}}
                return self._send(201)
        m = re.fullmatch(r"/realms/xt/client-scopes/([^/]+)", p)
        if m and method == "PUT":
            st["scopes"][m.group(1)].update(body)
            return self._send(204)
        m = re.fullmatch(r"/realms/xt/client-scopes/([^/]+)/protocol-mappers/models(?:/([^/]+))?", p)
        if m:
            maps = st["scopes"][m.group(1)]["mappers"]
            if method == "GET":
                return self._send(200, list(maps.values()))
            if method == "POST":
                mid = f"m{len(maps) + 1}-{body['name']}"
                maps[mid] = {**body, "id": mid}
                return self._send(201)
            if method == "PUT":
                maps[m.group(2)] = body
                return self._send(204)
            if method == "DELETE":
                return self._send(204 if maps.pop(m.group(2), None) else 404)
        m = re.fullmatch(r"/realms/xt/default-(default|optional)-client-scopes", p)
        if m and method == "GET":
            return self._send(200, [{"id": i} for i in st["realm_" + m.group(1)]])
        m = re.fullmatch(r"/realms/xt/default-(default|optional)-client-scopes/([^/]+)", p)
        if m and method == "DELETE":
            ok = m.group(2) in st["realm_" + m.group(1)]
            st["realm_" + m.group(1)].discard(m.group(2))
            return self._send(204 if ok else 404)
        m = re.fullmatch(r"/realms/xt/clients/([^/]+)/(default|optional)-client-scopes(?:/([^/]+))?", p)
        if m:
            cid, kind, sid = m.groups()
            have = st["client_scopes"].setdefault((cid, kind), set())
            if method == "GET":
                return self._send(200, [{"id": x} for x in have])
            if method == "PUT":
                have.add(sid)
                return self._send(204)
            if method == "DELETE":
                ok = sid in have
                have.discard(sid)
                return self._send(204 if ok else 404)
        return self._send(500, {"unhandled": f"{method} {p}"})

    do_GET = lambda self: self._route("GET")  # noqa: E731
    do_POST = lambda self: self._route("POST")  # noqa: E731
    do_PUT = lambda self: self._route("PUT")  # noqa: E731
    do_DELETE = lambda self: self._route("DELETE")  # noqa: E731


def clients(*names, enabled=True):
    return {f"id-{n}": {"id": f"id-{n}", "clientId": n, "enabled": enabled} for n in names}


class ProvisionData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load("provision_data_t", "keycloak/provision_data.py")
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeKC)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        FakeKC.log = []
        FakeKC.state = {
            "clients": {**clients("xt-desk", "xt-integrator", "dcr-evil"), **clients("off", enabled=False)},
            "scopes": {"sc-x": {"name": "other", "mappers": {}}},
            "realm_default": set(),
            "realm_optional": set(),
            "client_scopes": {},
        }

    def kc(self):
        kc = self.mod.KC(self.url, "admin", PASSWORD)
        kc.login()
        return kc

    def run_prov(self, names):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.mod.provision_data(self.kc(), "xt", names)
        return out.getvalue()

    def methods(self):
        return [(m, p) for m, p, _ in FakeKC.log if m != "LOGIN"]

    def test_rest_sequence_and_state(self):
        FakeKC.state["realm_default"].add("sc-new")
        FakeKC.state["realm_optional"].add("sc-new")
        self.run_prov(["xt-desk", "xt-integrator"])
        seq = self.methods()
        # Kiểm client trước, rồi mới tạo scope
        first_post = seq.index(("POST", "/realms/xt/client-scopes"))
        self.assertTrue(all(m == "GET" for m, _ in seq[:first_post]))
        self.assertEqual(sum("clientId=" in p for _, p in seq[:first_post]), 2)
        sc = FakeKC.state["scopes"]["sc-new"]
        got = {m["name"]: m for m in sc["mappers"].values()}
        self.assertEqual(set(got), {"data-audience", "data-role", "organization"})
        self.assertEqual(got["data-audience"]["config"]["included.custom.audience"], "xt-data")
        self.assertEqual(got["data-role"]["config"]["claim.value"], "xt_web")
        self.assertEqual(got["data-role"]["config"]["claim.name"], "data_role")
        self.assertEqual(got["organization"]["config"]["claim.name"], "organization")
        self.assertEqual(FakeKC.state["realm_default"], set())
        self.assertEqual(FakeKC.state["realm_optional"], set())
        cs = FakeKC.state["client_scopes"]
        self.assertEqual(cs[("id-xt-desk", "optional")], {"sc-new"})
        self.assertEqual(cs[("id-xt-integrator", "optional")], {"sc-new"})
        self.assertFalse(cs.get(("id-dcr-evil", "optional")))

    def test_idempotent_second_run_no_duplicate_mappers(self):
        self.run_prov(["xt-desk"])
        self.run_prov(["xt-desk"])
        sc = FakeKC.state["scopes"]["sc-new"]
        self.assertEqual(len(sc["mappers"]), 3)
        self.assertEqual(sum(1 for m, p in self.methods() if (m, p) == ("POST", "/realms/xt/client-scopes")), 1)

    def test_default_scope_on_client_becomes_optional_and_strays_revoked(self):
        self.run_prov(["xt-desk"])
        cs = FakeKC.state["client_scopes"]
        cs[("id-xt-desk", "default")] = {"sc-new"}  # bị chuyển thành default tay
        cs[("id-dcr-evil", "default")] = {"sc-new"}
        cs[("id-dcr-evil", "optional")] = {"sc-new"}
        FakeKC.state["scopes"]["sc-new"]["mappers"]["mx"] = {"id": "mx", "name": "roles-leak"}
        out = self.run_prov(["xt-desk"])
        self.assertEqual(cs[("id-xt-desk", "default")], set())
        self.assertEqual(cs[("id-xt-desk", "optional")], {"sc-new"})
        self.assertEqual(cs[("id-dcr-evil", "default")], set())
        self.assertEqual(cs[("id-dcr-evil", "optional")], set())
        self.assertNotIn("mx", FakeKC.state["scopes"]["sc-new"]["mappers"])
        self.assertIn("thu hồi 2", out)

    def test_missing_or_disabled_client_aborts_before_any_mutation(self):
        for names in (["xt-desk", "ghost"], ["xt-desk", "off"]):
            FakeKC.log = []
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
                self.mod.provision_data(self.kc(), "xt", names)
            self.assertTrue(all(m == "GET" for m, _ in self.methods()), self.methods())
            self.assertNotIn("sc-new", FakeKC.state["scopes"])

    def test_http_failure_aborts_nonzero_without_secret_leak(self):
        FakeKC.state["scopes"] = None  # làm server 500
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            self.mod.provision_data(self.kc(), "xt", ["xt-desk"])
        self.assertNotIn(PASSWORD, str(cm.exception.code) + err.getvalue())
        self.assertNotEqual(cm.exception.code, 0)

    def test_parse_clients(self):
        p = self.mod.parse_clients
        self.assertEqual(p(" a , b ,"), ["a", "b"])
        for bad in ("", "a,a", "*", "a b", "a/../b"):
            with self.assertRaises(SystemExit):
                p(bad)

    def test_main_refuses_empty_list_and_remote_http(self):
        keep = {k: os.environ.get(k) for k in ("XT_DATA_CLIENTS", "KC_ADMIN_URL", "KC_BOOTSTRAP_ADMIN_PASSWORD")}
        try:
            os.environ["KC_BOOTSTRAP_ADMIN_PASSWORD"] = PASSWORD
            os.environ["XT_DATA_CLIENTS"] = ""
            with self.assertRaises(SystemExit):
                self.mod.main()
            os.environ["XT_DATA_CLIENTS"] = "xt-desk"
            os.environ["KC_ADMIN_URL"] = "http://kc.example.com:8080"
            with self.assertRaises(SystemExit) as cm:
                self.mod.main()
            self.assertNotIn(PASSWORD, str(cm.exception.code))
        finally:
            for k, v in keep.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def b64(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()


def rsa_key(kid, bits=2048):
    return {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": b64((1 << (bits - 1)) | 1), "e": b64(65537)}


class HeadlessScopes(ProvisionData):
    """xt-workflow / xt-cli / không lọt aud headless vào MCP (cùng FakeKC)."""

    def test_workflow_scope_exact_audience_optional_not_default(self):
        FakeKC.state["realm_default"].add("sc-new")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.mod.provision_workflow(self.kc(), "xt", ["xt-integrator"])
        sc = FakeKC.state["scopes"]["sc-new"]
        self.assertEqual(sc["name"], "xt-workflow")
        got = {m["name"]: m for m in sc["mappers"].values()}
        self.assertEqual(set(got), {"workflow-audience", "organization"})
        self.assertEqual(got["workflow-audience"]["config"]["included.custom.audience"], "xt-workflow")
        self.assertEqual(got["workflow-audience"]["protocolMapper"], "oidc-audience-mapper")
        self.assertNotIn("data_role", json.dumps(sc))
        self.assertEqual(FakeKC.state["realm_default"], set())
        cs = FakeKC.state["client_scopes"]
        self.assertEqual(cs[("id-xt-integrator", "optional")], {"sc-new"})
        self.assertFalse(cs.get(("id-xt-desk", "optional")))

    def test_both_scopes_never_share_audience(self):
        FakeKC.state["scopes"]["sc-x"]["name"] = "other"
        with contextlib.redirect_stdout(io.StringIO()):
            self.mod.provision_data(self.kc(), "xt", ["xt-desk"])
            # scope thứ hai: POST luôn đặt id sc-new -> đổi tên scope đầu để mô phỏng 2 scope tách biệt
            FakeKC.state["scopes"]["sc-data"] = FakeKC.state["scopes"].pop("sc-new")
            self.mod.provision_workflow(self.kc(), "xt", ["xt-integrator"])
        auds = {s["name"]: {m["config"]["included.custom.audience"] for m in s["mappers"].values()
                            if "included.custom.audience" in m.get("config", {})}
                for s in FakeKC.state["scopes"].values() if s["name"].startswith("xt-")}
        self.assertEqual(auds, {"xt-data": {"xt-data"}, "xt-workflow": {"xt-workflow"}})

    def test_forbidden_mcp_clients_rejected(self):
        env = {"XT_MCP_HUMAN_CLIENTS": "xt-desk,xt-desk2"}
        forb = self.mod.mcp_client_ids(env)
        self.assertEqual(set(forb), {"xt-desk", "xt-desk2", "xt-desk-agent", "xt-desk-confirm", "xt-api-introspect", "xt-client-registry"})
        for bad in ("xt-desk", "xt-cli,xt-desk-agent", "xt-client-registry"):
            with self.assertRaises(SystemExit):
                self.mod.parse_clients(bad, forb, "XT_WORKFLOW_CLIENTS")
        self.assertEqual(self.mod.parse_clients("xt-cli", forb), ["xt-cli"])

    def test_assert_mcp_clean(self):
        st = FakeKC.state["scopes"]
        st["sc-mcp"] = {"name": "mcp-xt", "mappers": {"a": {"id": "a", "name": "aud", "config": {"included.custom.audience": "https://x/mcp"}}}}
        with contextlib.redirect_stdout(io.StringIO()):
            self.mod.assert_mcp_clean(self.kc(), "xt")
        for aud in ("xt-workflow", "xt-data"):
            st["sc-mcp"]["mappers"]["a"]["config"]["included.custom.audience"] = aud
            with self.assertRaises(SystemExit):
                self.mod.assert_mcp_clean(self.kc(), "xt")
        st["sc-mcp"]["mappers"]["a"]["config"]["included.custom.audience"] = "https://x/mcp"
        st["sc-wf"] = {"name": "xt-workflow", "mappers": {}}
        FakeKC.state["realm_default"].add("sc-wf")
        with self.assertRaises(SystemExit):
            self.mod.assert_mcp_clean(self.kc(), "xt")


class CliClient(unittest.TestCase):
    def test_cli_client_rep_public_pkce_no_secret_no_cc(self):
        mod = load("provision_t", "keycloak/provision.py")
        rep = mod.cli_client_rep({})
        self.assertEqual(rep["clientId"], "xt-cli")
        self.assertTrue(rep["publicClient"])
        self.assertTrue(rep["standardFlowEnabled"])
        for k in ("serviceAccountsEnabled", "directAccessGrantsEnabled", "implicitFlowEnabled", "fullScopeAllowed"):
            self.assertFalse(rep[k], k)
        self.assertNotIn("secret", rep)
        self.assertEqual(rep["attributes"]["pkce.code.challenge.method"], "S256")
        self.assertTrue(all(u.startswith(("http://127.0.0.1/", "http://[::1]/")) and "*" not in u for u in rep["redirectUris"]))
        self.assertEqual(mod.cli_client_rep({"XT_CLI_CLIENT_ID": "x"})["clientId"], "x")


class CliDropsMcpScope(unittest.TestCase):
    def test_drop_mcp_scope_removes_optional_and_default(self):
        mod = load("provision_t", "keycloak/provision.py")
        calls = []

        class K:
            def call(self, method, path, body=None, ok=()):
                calls.append((method, path, ok))

        mod.drop_mcp_scope(K(), "xt", "CID", "SID")
        self.assertEqual(calls, [
            ("DELETE", "/realms/xt/clients/CID/optional-client-scopes/SID", (404,)),
            ("DELETE", "/realms/xt/clients/CID/default-client-scopes/SID", (404,))])
        src = (DEPLOY / "keycloak" / "provision.py").read_text()
        self.assertIn("drop_mcp_scope(kc, realm, cli, mcp_sid)", src)


class DeskNginx(unittest.TestCase):
    def test_spa_fallback_and_security_headers(self):
        conf = (DEPLOY.parent / "desk" / "nginx.conf").read_text()
        self.assertRegex(conf, r"try_files\s+\$uri\s+/index\.html;")
        self.assertIn("Content-Security-Policy", conf)
        self.assertIn("frame-ancestors 'none'", conf)
        self.assertIn("nosniff", conf)
        df = (DEPLOY / "docker" / "desk.Dockerfile").read_text()
        self.assertIn("COPY nginx.conf /etc/nginx/conf.d/default.conf", df)


class ComposeDefaults(unittest.TestCase):
    def test_xt_api_healthcheck_and_relay_default(self):
        text = (DEPLOY / "compose.yaml").read_text()
        api = text.split("\n  xt-api:\n", 1)[1].split("\n  xt-", 1)[0]
        self.assertIn("healthcheck:", api)
        self.assertIn("/healthz", api)
        self.assertNotIn("XT_WORKFLOW_RELAY_SECONDS:-60", text)
        self.assertIn("XT_WORKFLOW_RELAY_SECONDS:-15", text)


class Realm(BaseHTTPRequestHandler):
    docs: dict = {}
    hits: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.hits.append(self.path)
        if self.path in self.docs:
            code, body = self.docs[self.path]
            if code in (301, 302):
                self.send_response(code)
                self.send_header("Location", body)
                self.end_headers()
                return
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()


class FetchJwks(unittest.TestCase):
    ISS = "https://auth.example.test/realms/xt"

    @classmethod
    def setUpClass(cls):
        cls.mod = load("fetch_jwks_t", "scripts/fetch_jwks.py")
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Realm)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}/realms/xt"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "pgrst" / "jwks.json"
        disc = {"issuer": self.ISS, "jwks_uri": f"{self.ISS}/protocol/openid-connect/certs"}
        Realm.hits = []
        Realm.docs = {
            "/realms/xt/.well-known/openid-configuration": (200, disc),
            "/realms/xt/protocol/openid-connect/certs": (200, {"keys": [rsa_key("good")]}),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *args):
        argv = ["fetch_jwks", "--out", str(self.out), "--issuer", self.ISS, *args]
        if "--base" not in args:
            argv += ["--base", self.base]
        old = sys.argv
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return self.mod.main()
        finally:
            sys.argv = old

    def test_ok_writes_public_only(self):
        self.assertEqual(self.run_main(), 0)
        d = json.loads(self.out.read_text())
        self.assertEqual([k["kid"] for k in d["keys"]], ["good"])
        self.assertEqual(self.out.stat().st_mode & 0o777, 0o644)

    def test_url_option_removed(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_main("--url", "http://127.0.0.1:1/certs")

    def test_issuer_mismatch_keeps_old_file(self):
        self.out.parent.mkdir(parents=True)
        self.out.write_text("OLD")
        Realm.docs["/realms/xt/.well-known/openid-configuration"] = (200, {"issuer": "https://evil.test/realms/xt", "jwks_uri": "x"})
        with self.assertRaises(SystemExit):
            self.run_main()
        self.assertEqual(self.out.read_text(), "OLD")
        self.assertNotIn("/realms/xt/protocol/openid-connect/certs", Realm.hits)  # không tải khóa khi issuer sai

    def test_foreign_jwks_uri_rejected(self):
        for uri in ("https://evil.test/keys", f"{self.ISS.replace('/xt', '/other')}/protocol/openid-connect/certs", None):
            Realm.docs["/realms/xt/.well-known/openid-configuration"] = (200, {"issuer": self.ISS, "jwks_uri": uri})
            with self.assertRaises(SystemExit):
                self.run_main()
            self.assertFalse(self.out.exists())

    def test_redirect_not_followed(self):
        Realm.docs["/realms/xt/protocol/openid-connect/certs"] = (302, "http://127.0.0.1:1/evil")
        with self.assertRaises(SystemExit):
            self.run_main()
        self.assertFalse(self.out.exists())

    def test_base_validation(self):
        for b in ("http://kc.example.com/realms/xt", "ftp://x/realms/xt", "https://u:p@h/realms/xt", "https://h/realms/xt?a=1",
                  f"{self.base.rsplit('/', 1)[0]}/realms/other"):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.run_main("--base", b)

    def test_private_and_weak_keys(self):
        priv = {**rsa_key("p"), "d": "AQAB"}
        Realm.docs["/realms/xt/protocol/openid-connect/certs"] = (200, {"keys": [rsa_key("good"), priv]})
        with self.assertRaises(SystemExit):
            self.run_main()
        Realm.docs["/realms/xt/protocol/openid-connect/certs"] = (200, {"keys": [rsa_key("weak", 1024)]})
        with self.assertRaises(SystemExit):
            self.run_main()
        self.assertFalse(self.out.exists())

    def test_refuses_output_inside_repo(self):
        self.out = DEPLOY.parent / "jwks.json"
        with self.assertRaises(SystemExit):
            self.run_main()
        self.assertFalse(self.out.exists())


class DeskAgentRedirects(unittest.TestCase):
    """Builder redirect URI của xt-desk-agent theo profile (không cần Keycloak)."""

    DESK = "https://xt.example"

    @classmethod
    def setUpClass(cls):
        cls.mod = load("provision_t", "keycloak/provision.py")

    def setUp(self):
        self._argv = sys.argv
        sys.argv = ["provision.py"]

    def tearDown(self):
        sys.argv = self._argv

    def test_production_has_no_loopback(self):
        for env in ({}, {"XT_PROFILE": ""}, {"XT_PROFILE": "production"}, {"XT_PROFILE": "devx"},
                    {"XT_DESK_AGENT_DEV_REDIRECTS": "http://localhost:5173/auth/callback"}):
            r = self.mod.desk_agent_redirects(env, self.DESK)
            self.assertEqual(r, [f"{self.DESK}/auth/callback"])
            self.assertEqual(self.mod.desk_agent_web_origins(self.DESK, r), [self.DESK])

    def test_dev_adds_loopback(self):
        r = self.mod.desk_agent_redirects({"XT_PROFILE": "dev"}, self.DESK)
        self.assertEqual(r[0], f"{self.DESK}/auth/callback")
        self.assertIn("http://localhost:5173/auth/callback", r)
        self.assertIn("http://127.0.0.1:5173/auth/callback", r)
        self.assertEqual(self.mod.desk_agent_web_origins(self.DESK, r), [self.DESK, "http://localhost:5173", "http://127.0.0.1:5173"])

    def test_dev_flag_and_custom_list(self):
        sys.argv = ["provision.py", "--dev"]
        r = self.mod.desk_agent_redirects({"XT_DESK_AGENT_DEV_REDIRECTS": "http://localhost:3000/auth/callback"}, self.DESK)
        self.assertEqual(r, [f"{self.DESK}/auth/callback", "http://localhost:3000/auth/callback"])


if __name__ == "__main__":
    unittest.main()
