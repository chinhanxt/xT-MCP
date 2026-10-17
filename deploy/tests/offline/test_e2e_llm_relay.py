# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Exercise real HTTP relay boundaries with a local byte receiver, no LLM inference."""

from __future__ import annotations

import http.client
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "e2e"))

from xte2e.llm import Relay


class RelayTransport(unittest.TestCase):
    def test_forwards_bytes_without_caller_credentials_and_closes_listener(self) -> None:
        observed = []

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                observed.append((self.path, dict(self.headers), body))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b"data: byte-receiver\n\n")

            def log_message(self, *args):
                pass

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        try:
            with Relay("127.0.0.1", "127.0.0.0/8", upstream.server_address) as relay:
                port = relay.server.server_port
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                conn.request(
                    "POST",
                    "/v1/chat/completions",
                    b"{}",
                    {"Authorization": "Bearer private", "DPoP": "private-proof"},
                )
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"data: byte-receiver\n\n")
                conn.close()
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                conn.request("POST", "/api/pull", b"{}")
                self.assertEqual(conn.getresponse().status, 404)
                conn.close()
            self.assertFalse(relay.thread.is_alive())
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0][0], "/v1/chat/completions")
            self.assertEqual(observed[0][2], b"{}")
            self.assertFalse({k.lower() for k in observed[0][1]} & {"authorization", "dpop"})
        finally:
            upstream.shutdown()
            upstream.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
