# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`MalwareScanner` qua giao thức `clamd` INSTREAM (TCP 3310). ClamAV (GPL-2.0) là tiến trình
riêng ở profile `full`; xT chỉ nói chuyện qua socket, không link thư viện."""

from __future__ import annotations

import socket
import struct
from collections.abc import Iterable

from xt.application.files.ports import ScannerUnavailable, ScanVerdict

MAX_CHUNK = 64 * 1024


class ClamdScanner:
    def __init__(self, host: str, port: int = 3310, *, timeout_s: float = 60.0) -> None:
        self.host, self.port, self.timeout_s = host, port, timeout_s

    def scan(self, chunks: Iterable[bytes]) -> ScanVerdict:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout_s) as s:
                s.sendall(b"zINSTREAM\0")
                for chunk in chunks:
                    for i in range(0, len(chunk), MAX_CHUNK):
                        part = chunk[i : i + MAX_CHUNK]
                        s.sendall(struct.pack("!I", len(part)) + part)
                s.sendall(struct.pack("!I", 0))
                reply = b""
                while not reply.endswith(b"\0"):
                    got = s.recv(4096)
                    if not got:
                        break
                    reply += got
        except OSError as e:
            raise ScannerUnavailable(f"clamd: {type(e).__name__}") from None
        return parse_reply(reply.rstrip(b"\0").decode("utf-8", "replace"))


def parse_reply(text: str) -> ScanVerdict:
    """`stream: OK` | `stream: <tên> FOUND` | `... ERROR` (vd vượt StreamMaxLength)."""
    body = text.split(":", 1)[1].strip() if ":" in text else text.strip()
    if body == "OK":
        return ScanVerdict(True)
    if body.endswith(" FOUND"):
        return ScanVerdict(False, body[: -len(" FOUND")].strip()[:200] or "unknown")
    raise ScannerUnavailable(f"clamd: {body[:120] or 'empty reply'}")
