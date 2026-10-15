# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Port giả của dịch vụ tệp: object storage trong bộ nhớ, kho metadata, máy quét."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from xt.application.files import BlobError, BlobInfo, FileRow, ScannerUnavailable, ScanVerdict


class MemStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.copies = 0

    def put(self, key: str, data: BinaryIO, size: int, sha256: str, content_type: str) -> None:
        raw = data.read(size)
        if hashlib.sha256(raw).hexdigest() != sha256:
            raise BlobError("checksum")
        self.objects[key] = (raw, sha256)

    def head(self, key: str) -> BlobInfo | None:
        hit = self.objects.get(key)
        return BlobInfo(len(hit[0]), hit[1]) if hit else None

    def stream(self, key: str) -> Iterator[bytes]:
        if key not in self.objects:
            raise BlobError("missing")
        raw = self.objects[key][0]
        return iter([raw[i : i + 5] for i in range(0, len(raw), 5)])

    def copy(self, src: str, dst: str) -> None:
        self.copies += 1
        self.objects[dst] = self.objects[src]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


@dataclass
class MemRepo:
    tenant: UUID = field(default_factory=uuid4)
    rows: dict[UUID, FileRow] = field(default_factory=dict)
    events: list[tuple[UUID, str, dict[str, Any]]] = field(default_factory=list)

    def tenant_id(self) -> UUID:
        return self.tenant

    def insert(self, row: FileRow) -> None:
        self.rows[row.id] = row

    def get(self, file_id: UUID, *, lock: bool = False) -> FileRow | None:
        r = self.rows.get(file_id)
        return r if r and r.deleted_at is None else None

    def update(self, row: FileRow) -> None:
        assert row.id in self.rows
        self.rows[row.id] = row

    def event(self, file_id: UUID, action: str, detail: Mapping[str, Any]) -> None:
        self.events.append((file_id, action, dict(detail)))

    def scanning(self, limit: int) -> list[FileRow]:
        return [r for r in self.rows.values() if r.status == "scanning"][:limit]

    def actions(self, file_id: UUID) -> list[str]:
        return [a for f, a, _ in self.events if f == file_id]


class FakeScanner:
    def __init__(self) -> None:
        self.down = False
        self.seen: list[bytes] = []

    def scan(self, chunks: Iterable[bytes]) -> ScanVerdict:
        if self.down:
            raise ScannerUnavailable("down")
        raw = b"".join(chunks)
        self.seen.append(raw)
        if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in raw:
            return ScanVerdict(False, "Eicar-Test-Signature")
        return ScanVerdict(True)
