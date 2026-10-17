# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Test-only byte relay from the isolated Docker network to real host Ollama.

Bind only the E2E network gateway, never all host interfaces. Production requests
still pass through APISIX and the authenticated application LLM endpoint.
"""

from __future__ import annotations

import contextlib
import http.client
import ipaddress
import json
import os
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from xte2e import compose

MAX_BODY = 1024 * 1024


def ollama_address() -> tuple[str, int]:
    port = int(os.environ.get("XT_E2E_OLLAMA_PORT", "11434"))
    if not 1 <= port <= 65535:
        raise ValueError("XT_E2E_OLLAMA_PORT must be a local TCP port")
    return "127.0.0.1", port


class Relay:
    def __init__(
        self,
        gateway: str,
        subnet: str,
        upstream: tuple[str, int] = ("127.0.0.1", 11434),
    ) -> None:
        network = ipaddress.ip_network(subnet)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.connection.settimeout(30)
                if ipaddress.ip_address(self.client_address[0]) not in network:
                    self.send_error(403)
                    return
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("content-length", "0"))
                except ValueError:
                    length = 0
                if not 0 < length <= MAX_BODY or self.headers.get("transfer-encoding"):
                    self.send_error(413)
                    return
                body = self.rfile.read(length)
                if len(body) != length:
                    self.send_error(400)
                    return
                connection = http.client.HTTPConnection(*upstream, timeout=600)
                try:
                    # Never forward a browser token or any caller-supplied upstream.
                    connection.request(
                        "POST", self.path, body, {"Content-Type": "application/json"}
                    )
                    response = connection.getresponse()
                    self.send_response(response.status)
                    self.send_header(
                        "Content-Type", response.getheader("Content-Type", "application/json")
                    )
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    while chunk := response.read1(65536):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (OSError, http.client.HTTPException):
                    self.close_connection = True
                finally:
                    connection.close()

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer((gateway, 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f"http://{gateway}:{self.server.server_port}/v1"

    def __enter__(self) -> Relay:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def network_gateway() -> tuple[str, str]:
    project = compose.settings().project
    result = subprocess.run(
        ["docker", "network", "inspect", f"{project}_default", "--format", "{{json .IPAM.Config}}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    for entry in json.loads(result.stdout):
        gateway, subnet = entry.get("Gateway"), entry.get("Subnet")
        if gateway and subnet and ipaddress.ip_address(gateway).version == 4:
            return gateway, subnet
    raise RuntimeError("E2E Docker network has no IPv4 gateway for the Ollama relay")


@contextlib.contextmanager
def relay_for_stack() -> Iterator[str]:
    gateway, subnet = network_gateway()
    args = ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "xt-api"]
    with Relay(gateway, subnet, ollama_address()) as relay:
        try:
            compose.dc(args, env={"XT_DESK_LLM_URL": relay.url})
            yield relay.url
        finally:
            # Restore the isolated stack's normal URL before closing its test relay.
            compose.dc(args)
