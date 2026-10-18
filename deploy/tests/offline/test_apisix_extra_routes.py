# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Route riêng của máy (extra-routes.json) trong render_apisix.py: không bao giờ chiếm hostname xT."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DEPLOY / "scripts"))
spec = importlib.util.spec_from_file_location("render_apisix", DEPLOY / "scripts" / "render_apisix.py")
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)

ENV = {
    "XT_DOMAIN_DESK": "xt.example.test",
    "XT_DOMAIN_MCP": "xt-mcp.example.test",
    "XT_DOMAIN_API": "xt-api.example.test",
    "XT_DOMAIN_AUTH": "xt-auth.example.test",
}
OTHER = {
    "id": "other-service",
    "hosts": ["other.example.test"],
    "uris": ["/*"],
    "priority": 200,
    "upstream": {"type": "roundrobin", "nodes": {"172.17.0.1:8000": 1}},
}


def template() -> str:
    tmpl = (DEPLOY / "apisix" / "apisix.yaml.tmpl").read_text()
    return re.sub(r"@@([A-Z0-9_]+)@@", lambda m: ENV.get(m.group(1), "x"), tmpl)


class ExtraRoutes(unittest.TestCase):
    def routes_from(self, doc: object) -> list:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "extra.json"
            path.write_text(json.dumps(doc))
            return render.extra_routes({**ENV, "XT_APISIX_EXTRA_ROUTES": str(path)})

    def test_missing_default_file_means_no_extra_routes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            saved = render.DEPLOY
            render.DEPLOY = Path(d)
            try:
                self.assertEqual(render.extra_routes(dict(ENV)), [])
            finally:
                render.DEPLOY = saved

    def test_explicit_missing_file_fails(self) -> None:
        with self.assertRaises(SystemExit):
            render.extra_routes({**ENV, "XT_APISIX_EXTRA_ROUTES": "/nonexistent/extra.json"})

    def test_route_is_inserted_before_end_marker(self) -> None:
        out = render.insert_routes(template(), self.routes_from({"routes": [OTHER]}))
        self.assertTrue(out.rstrip().endswith("#END"))
        line = next(ln for ln in out.splitlines() if '"other-service"' in ln)
        self.assertTrue(line.startswith("  - {"))
        self.assertEqual(json.loads(line[4:])["hosts"], ["other.example.test"])
        self.assertLess(out.index('"other-service"'), out.rindex("#END"))

    def test_xt_hostnames_are_refused(self) -> None:
        for host in ENV.values():
            with self.subTest(host=host), self.assertRaises(SystemExit):
                self.routes_from({"routes": [{**OTHER, "hosts": ["other.example.test", host.upper()]}]})

    def test_route_without_explicit_hosts_is_refused(self) -> None:
        for bad in ({k: v for k, v in OTHER.items() if k != "hosts"}, {**OTHER, "hosts": []},
                    {**OTHER, "host": "other.example.test"}):
            with self.subTest(route=bad), self.assertRaises(SystemExit):
                self.routes_from({"routes": [bad]})

    def test_duplicate_id_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            render.insert_routes(template(), self.routes_from({"routes": [{**OTHER, "id": "mcp"}]}))

    def test_example_file_is_valid(self) -> None:
        example = json.loads((DEPLOY / "apisix" / "extra-routes.example.json").read_text())
        self.assertEqual(len(self.routes_from(example)), 1)


if __name__ == "__main__":
    unittest.main()
