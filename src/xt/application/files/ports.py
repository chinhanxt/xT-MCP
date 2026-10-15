# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cổng của dịch vụ tệp (dữ liệu phi cấu trúc, architecture-v2 §3 "Dữ liệu").

- `FileStore`: object storage S3 (SeaweedFS). Khóa do application dựng, luôn có tiền tố tenant.
- `MalwareScanner`: quét virus (ClamAV `clamd` ở profile full). Không cấu hình = không quét.
- `FileRepository`: metadata trong Postgres (bảng `xt_meta.file_object`, RLS theo tenant, audit
  hash-chain). Mọi hàm chạy trong transaction tenant đang mở của lời gọi.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, BinaryIO, Literal, Protocol
from uuid import UUID

FileStatus = Literal[
    "awaiting_upload",  # đã tạo ý định tải lên, chưa có byte
    "uploaded",  # byte đã ở quarantine/, chờ finalize (checksum)
    "scanning",  # đã finalize, chờ ClamAV (khi bật quét)
    "clean",  # ClamAV sạch
    "not_scanned",  # profile không quét (minimal): policy quyết định ai tải về được
    "infected",  # ClamAV phát hiện; byte đã xóa
    "scan_failed",  # clamd lỗi quá số lần thử (FB-01): không tải về/đính kèm được, có thể quét lại
    "expired",  # quá hạn tải lên
]


@dataclass(frozen=True, slots=True)
class BlobInfo:
    size: int
    sha256: str | None


class FileStore(Protocol):
    def put(self, key: str, data: BinaryIO, size: int, sha256: str, content_type: str) -> None:
        """Ghi `size` byte từ `data`. Server object storage tự kiểm `sha256` (SigV4 signed payload);
        lệch -> ném `BlobError`."""
        ...

    def head(self, key: str) -> BlobInfo | None: ...

    def stream(self, key: str) -> Iterator[bytes]:
        """Đọc từng khối; không có -> `BlobError`."""
        ...

    def copy(self, src: str, dst: str) -> None: ...

    def delete(self, key: str) -> None: ...


class BlobError(Exception):
    """Lỗi object storage (mạng, 5xx, checksum)."""


@dataclass(frozen=True, slots=True)
class ScanVerdict:
    clean: bool
    signature: str | None = None


class ScannerUnavailable(Exception):
    """clamd không trả lời / lỗi: tệp giữ `scanning`, quét lại sau."""


class MalwareScanner(Protocol):
    def scan(self, chunks: Iterable[bytes]) -> ScanVerdict: ...


@dataclass(frozen=True, slots=True)
class FileRow:
    id: UUID
    name: str
    declared_type: str
    status: FileStatus
    created_by: UUID
    client_id: str | None
    created_at: datetime
    content_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    object_key: str | None = None
    upload_expires_at: datetime | None = None
    finalized_at: datetime | None = None
    scan_signature: str | None = None
    deleted_at: datetime | None = None
    scan_attempts: int = 0


@dataclass(frozen=True, slots=True)
class Usage:
    """Đang giữ: ý định mở (chưa hết hạn) và byte (tệp chưa xóa, trừ expired/infected)."""

    user_pending: int
    tenant_pending: int
    user_bytes: int
    tenant_bytes: int


class FileRepository(Protocol):
    def tenant_id(self) -> UUID:
        """Tenant của transaction đang mở (tiền tố khóa object)."""
        ...

    def insert(self, row: FileRow) -> None: ...

    def get(self, file_id: UUID, *, lock: bool = False) -> FileRow | None:
        """Bản ghi chưa xóa mềm của tenant hiện tại (RLS), None nếu không thấy."""
        ...

    def update(self, row: FileRow) -> None: ...

    def event(self, file_id: UUID, action: str, detail: Mapping[str, Any]) -> None:
        """Một dòng `xt_meta.file_event` (vào hash-chain audit): created, uploaded, finalized,
        scanned, downloaded, deleted, rejected."""
        ...

    def scanning(self, limit: int) -> list[FileRow]:
        """Tệp đang `scanning` (quét lại khi clamd từng lỗi)."""
        ...

    def usage(self, person: UUID) -> Usage:
        """Đếm cho quota (khóa tư vấn theo tenant tới hết transaction)."""
        ...

    def expired_pending(self, limit: int) -> list[FileRow]:
        """Ý định/byte quarantine quá hạn chưa finalize (khóa dòng, bỏ qua dòng đang bị khóa)."""
        ...

    def purgeable_deleted(self, older_than_s: int, limit: int) -> list[FileRow]: ...

    def object_in_use(self, object_key: str, except_id: UUID) -> bool: ...

    def lock_content(self, sha256: str) -> None:
        """Khóa theo nội dung (tenant, sha256) tới hết transaction (R2-F3)."""
        ...
