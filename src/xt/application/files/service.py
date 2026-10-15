# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Vòng đời một tệp: ý định tải lên -> PUT byte (quarantine/) -> finalize (checksum) -> quét
(tùy chọn) -> khóa theo nội dung `t/<tenant>/sha256/<hh>/<hex>` -> tải về -> xóa mềm.

Không có I/O tự chọn: object storage, máy quét và kho metadata là cổng (`ports.py`); mọi hàm chạy
trong transaction tenant đang mở của người gọi (RLS). Lỗi nghiệp vụ là `ToolError` của contract.
Khi lỗi phải đi kèm thay đổi trạng thái đã ghi (vd checksum lệch -> quay về `awaiting_upload` +
sự kiện `rejected`), hàm TRẢ `Outcome(row, error)` để lớp gọi commit trước rồi mới ném lỗi.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import tempfile
import unicodedata
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import IO, Any, BinaryIO, Literal, cast
from uuid import UUID

from xt.application.files.ports import (
    FileRepository,
    FileRow,
    FileStatus,
    FileStore,
    MalwareScanner,
    ScannerUnavailable,
)
from xt.application.files.sniff import (
    ALLOWED_TYPES,
    SNIFF_BYTES,
    matches,
    normalize_type,
    sniff,
    type_for_name,
)
from xt.application.tools.errors import (
    ToolError,
    access_denied,
    invalid_request,
    precondition_failed,
)
from xt.contracts.tools import ErrorCode

UnscannedPolicy = Literal["deny", "uploader", "allow"]
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
DOWNLOADABLE: frozenset[FileStatus] = frozenset({"clean", "not_scanned"})
PENDING_UPLOAD: frozenset[FileStatus] = frozenset({"awaiting_upload", "uploaded"})
# Trạng thái ở bề mặt tool `upload_file` (enum đóng của TCV1 §5.13): `uploaded` chưa finalize vẫn
# là "chờ tải lên xong" đối với agent.
TOOL_STATUS: dict[FileStatus, str] = {
    "awaiting_upload": "awaiting_upload",
    "uploaded": "awaiting_upload",
    "scanning": "scanning",
    "clean": "clean",
    "not_scanned": "not_scanned",
    "infected": "infected",
    "expired": "expired",
}


@dataclass(frozen=True, slots=True)
class FilePolicy:
    max_bytes: int = 25 * 1024 * 1024
    max_inline_bytes: int = 1024 * 1024
    upload_ttl: timedelta = timedelta(minutes=15)
    scanning: bool = False
    unscanned_download: UnscannedPolicy = "uploader"
    allowed_types: frozenset[str] = field(default_factory=lambda: frozenset(ALLOWED_TYPES))

    def __post_init__(self) -> None:
        if not 1 <= self.max_inline_bytes <= self.max_bytes:
            raise ValueError("max_inline_bytes phải trong 1..max_bytes")
        if not timedelta(seconds=10) <= self.upload_ttl <= timedelta(hours=24):
            raise ValueError("upload_ttl phải trong 10 s..24 h")
        unknown = self.allowed_types - set(ALLOWED_TYPES)
        if unknown:
            raise ValueError(f"kiểu tệp không có bộ nhận dạng magic byte: {sorted(unknown)}")


@dataclass(frozen=True, slots=True)
class Outcome:
    row: FileRow
    error: ToolError | None = None


def too_large(limit: int) -> ToolError:
    return ToolError(
        ErrorCode.RESOURCE_CONSTRAINT,
        "The file is larger than the upload limit.",
        remediation="Send a smaller file (see details.limit_bytes).",
        details={"resource": "file_size", "limit_bytes": limit},
    )


class Spool:
    """Nhận byte theo khối: đếm (chặn quá hạn mức ngay khi vượt), sha256, giữ `SNIFF_BYTES` đầu.
    Dữ liệu nằm trong bộ nhớ tới 1 MiB rồi tràn ra tệp tạm (tự xóa khi đóng)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.size = 0
        self._sha = hashlib.sha256()
        self._head = bytearray()
        self.file: IO[bytes] = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)  # noqa: SIM115

    def write(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.size += len(chunk)
        if self.size > self.limit:
            raise too_large(self.limit)
        self._sha.update(chunk)
        if len(self._head) < SNIFF_BYTES:
            self._head += chunk[: SNIFF_BYTES - len(self._head)]
        self.file.write(chunk)

    @property
    def head(self) -> bytes:
        return bytes(self._head)

    @property
    def sha256(self) -> str:
        return self._sha.hexdigest()

    def rewind(self) -> IO[bytes]:
        self.file.seek(0)
        return self.file

    def close(self) -> None:
        self.file.close()

    def __enter__(self) -> Spool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ---- URL tải lên ký sẵn (presigned PUT) -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class UploadGrant:
    tenant: str
    file_id: UUID
    person: UUID
    expires: int


class UploadTokens:
    """Capability URL `/files/v1/uploads/<token>`: HMAC-SHA256 trên (tenant, file, người, hạn).

    Không phải SigV4 của S3: SeaweedFS không bao giờ lộ ra ngoài; xt-api nhận byte, đếm, băm,
    nhận dạng kiểu rồi mới ghi vào object storage. Hạn ngắn + một lần (chỉ khi tệp còn
    `awaiting_upload`).
    """

    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("khóa ký URL tải lên phải ≥ 32 byte")
        self._key = key

    def _mac(self, payload: bytes) -> str:
        mac = hmac.new(self._key, b"xt-files-upload-v1\n" + payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()

    def issue(self, tenant: str, file_id: UUID, person: UUID, expires: int) -> str:
        payload = f"{tenant}~{file_id.hex}~{person.hex}~{expires}".encode()
        body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
        return f"{body}.{self._mac(payload)}"

    def verify(self, token: str, now: int) -> UploadGrant:
        bad = access_denied(
            "This upload link is not valid.",
            "upload_url_invalid",
            None,
            remediation="Create a new upload (POST /files/v1 or upload_file) and use its link.",
        )
        body, dot, mac = token.partition(".")
        if not dot or len(token) > 512:
            raise bad
        try:
            payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        except ValueError:
            raise bad from None
        if not hmac.compare_digest(mac, self._mac(payload)):
            raise bad
        try:
            tenant, fid, person, exp = payload.decode().split("~")
            grant = UploadGrant(tenant, UUID(hex=fid), UUID(hex=person), int(exp))
        except ValueError:
            raise bad from None
        if grant.expires <= now:
            raise access_denied(
                "This upload link has expired.",
                "upload_url_expired",
                None,
                remediation="Create a new upload and use the new link within its time limit.",
            )
        return grant


# ---- dịch vụ -----------------------------------------------------------------------------


def _clean_name(name: str) -> str:
    n = unicodedata.normalize("NFC", name).strip()
    if (
        not 1 <= len(n) <= 255
        or any(unicodedata.category(c) in ("Cc", "Cf") for c in n)
        or "/" in n
        or "\\" in n
        or n in (".", "..")
    ):
        raise invalid_request(
            "File name must be 1-255 characters without path separators or control characters.",
            "bad_file_name",
            parameter="name",
        )
    return n


class FileManager:
    def __init__(
        self,
        policy: FilePolicy,
        store: FileStore,
        scanner: MalwareScanner | None = None,
    ) -> None:
        if policy.scanning and scanner is None:
            raise ValueError("bật quét nhưng chưa có MalwareScanner")
        self.policy = policy
        self.store = store
        self.scanner = scanner if policy.scanning else None

    # ---- kiểm tra đầu vào ------------------------------------------------------------------

    def declared_type(self, name: str, content_type: str | None) -> tuple[str, str]:
        """(tên đã chuẩn hóa, kiểu khai báo) hoặc `invalid_request`."""
        clean = _clean_name(name)
        declared = normalize_type(content_type) or type_for_name(clean)
        allowed = sorted(self.policy.allowed_types)
        if declared is None:
            raise invalid_request(
                "Cannot tell the file type; send content_type.",
                "content_type_required",
                parameter="content_type",
                allowed=allowed,
            )
        if declared not in self.policy.allowed_types:
            raise invalid_request(
                "This file type is not allowed.",
                "content_type_not_allowed",
                remediation="Use one of details.allowed (e.g. convert to PDF).",
                parameter="content_type",
                declared=declared,
                allowed=allowed,
            )
        return clean, declared

    def check_bytes(self, declared: str, head: bytes, size: int) -> None:
        if size == 0:
            raise invalid_request("The file is empty.", "empty_file")
        found = sniff(head, complete=size <= len(head))
        if found.family == "deny":
            raise invalid_request(
                "The file content is a forbidden format (executable, script, markup or archive"
                " with macros).",
                "content_type_not_allowed",
                remediation="Send the document as PDF, image, Office (OOXML) or plain text.",
                declared=declared,
                detected="forbidden",
            )
        if not matches(declared, found):
            raise invalid_request(
                "The file content does not match its declared type.",
                "content_type_mismatch",
                remediation="Declare the real type of the file (the server checks the bytes).",
                declared=declared,
                detected=found.family,
            )

    # ---- khóa object -----------------------------------------------------------------------

    @staticmethod
    def quarantine_key(tenant: UUID, file_id: UUID) -> str:
        return f"t/{tenant}/quarantine/{file_id}"

    @staticmethod
    def content_key(tenant: UUID, sha256: str) -> str:
        return f"t/{tenant}/sha256/{sha256[:2]}/{sha256}"

    # ---- vòng đời --------------------------------------------------------------------------

    def intent(
        self,
        repo: FileRepository,
        *,
        person: UUID,
        client_id: str | None,
        name: str,
        declared: str,
        now: datetime,
    ) -> FileRow:
        row = FileRow(
            id=uuid.uuid4(),
            name=name,
            declared_type=declared,
            status="awaiting_upload",
            created_by=person,
            client_id=client_id,
            created_at=now,
            upload_expires_at=now + self.policy.upload_ttl,
        )
        repo.insert(row)
        repo.event(row.id, "created", {"declared_type": declared})
        return row

    def current(self, repo: FileRepository, row: FileRow, now: datetime) -> FileRow:
        """Áp hạn tải lên (lười): quá hạn khi chưa finalize -> `expired`, xóa byte quarantine."""
        if (
            row.status in PENDING_UPLOAD
            and row.upload_expires_at is not None
            and row.upload_expires_at <= now
        ):
            if row.status == "uploaded":
                self.store.delete(self.quarantine_key(repo.tenant_id(), row.id))
            row = replace(row, status="expired", object_key=None)
            repo.update(row)
            repo.event(row.id, "expired", {})
        return row

    def ingest(self, repo: FileRepository, row: FileRow, spool: Spool, now: datetime) -> Outcome:
        row = self.current(repo, row, now)
        if row.status == "expired":
            return Outcome(
                row,
                access_denied(
                    "This upload link has expired.",
                    "upload_url_expired",
                    None,
                    remediation="Create a new upload and use the new link within its time limit.",
                ),
            )
        if row.status != "awaiting_upload":
            return Outcome(
                row,
                precondition_failed(
                    "This upload link was already used.",
                    "already_uploaded",
                    remediation="Finalize the upload, or create a new one for another file.",
                ),
            )
        try:
            self.check_bytes(row.declared_type, spool.head, spool.size)
        except ToolError as e:
            repo.event(row.id, "rejected", {"reason": e.details.get("reason")})
            return Outcome(row, e)
        key = self.quarantine_key(repo.tenant_id(), row.id)
        self.store.put(
            key, cast(BinaryIO, spool.rewind()), spool.size, spool.sha256, row.declared_type
        )
        row = replace(
            row,
            status="uploaded",
            size_bytes=spool.size,
            sha256=spool.sha256,
            content_type=row.declared_type,
            object_key=key,
        )
        repo.update(row)
        repo.event(row.id, "uploaded", {"size_bytes": spool.size, "sha256": spool.sha256})
        return Outcome(row)

    def finalize(self, repo: FileRepository, row: FileRow, sha256: str, now: datetime) -> Outcome:
        sha256 = sha256.strip().lower()
        if not SHA256_HEX.fullmatch(sha256):
            raise invalid_request(
                "sha256 must be 64 lowercase hex characters.", "schema", parameter="sha256"
            )
        row = self.current(repo, row, now)
        if row.status not in ("uploaded", "awaiting_upload", "expired"):
            if row.sha256 == sha256:
                return Outcome(row)  # finalize lặp lại: idempotent
            return Outcome(row, self._mismatch())
        if row.status == "expired":
            return Outcome(
                row,
                precondition_failed(
                    "The upload expired before it was finalized.",
                    "upload_expired",
                    remediation="Create a new upload.",
                ),
            )
        if row.status == "awaiting_upload":
            return Outcome(
                row,
                precondition_failed(
                    "No bytes were uploaded yet.",
                    "not_uploaded",
                    remediation="PUT the file to put_url first, then finalize.",
                ),
            )
        tenant = repo.tenant_id()
        qkey = self.quarantine_key(tenant, row.id)
        stored = self.store.head(qkey)
        intact = (
            stored is not None
            and stored.size == row.size_bytes
            and (stored.sha256 is None or stored.sha256 == row.sha256)
        )
        if row.sha256 != sha256 or not intact:
            self.store.delete(qkey)
            row = replace(
                row,
                status="awaiting_upload",
                size_bytes=None,
                sha256=None,
                content_type=None,
                object_key=None,
            )
            repo.update(row)
            repo.event(
                row.id,
                "rejected",
                {"reason": "checksum_mismatch" if intact else "stored_object_mismatch"},
            )
            return Outcome(row, self._mismatch())
        row = replace(row, finalized_at=now, upload_expires_at=None)
        if self.scanner is not None:
            row = replace(row, status="scanning")
            repo.update(row)
            repo.event(row.id, "finalized", {"sha256": sha256, "scan": "pending"})
            return Outcome(self.scan(repo, row))
        row = self._promote(tenant, replace(row, status="not_scanned"))
        repo.update(row)
        repo.event(row.id, "finalized", {"sha256": sha256, "scan": "disabled"})
        return Outcome(row)

    @staticmethod
    def _mismatch() -> ToolError:
        return invalid_request(
            "The sha256 does not match the uploaded bytes; the upload was discarded.",
            "checksum_mismatch",
            remediation="Upload the file again to the same put_url (while it is valid), then "
            "finalize with the sha256 of exactly those bytes.",
        )

    def _promote(self, tenant: UUID, row: FileRow) -> FileRow:
        assert row.sha256 is not None
        qkey = self.quarantine_key(tenant, row.id)
        ckey = self.content_key(tenant, row.sha256)
        if self.store.head(ckey) is None:
            self.store.copy(qkey, ckey)
        self.store.delete(qkey)
        return replace(row, object_key=ckey)

    def scan(self, repo: FileRepository, row: FileRow) -> FileRow:
        """Quét tệp `scanning`. clamd lỗi -> giữ nguyên (quét lại sau)."""
        if row.status != "scanning" or self.scanner is None:
            return row
        tenant = repo.tenant_id()
        qkey = self.quarantine_key(tenant, row.id)
        try:
            verdict = self.scanner.scan(self.store.stream(qkey))
        except ScannerUnavailable:
            return row
        if verdict.clean:
            row = self._promote(tenant, replace(row, status="clean"))
        else:
            self.store.delete(qkey)
            row = replace(row, status="infected", object_key=None, scan_signature=verdict.signature)
        repo.update(row)
        repo.event(row.id, "scanned", {"clean": verdict.clean, "signature": verdict.signature})
        return row

    def store_inline(
        self,
        repo: FileRepository,
        *,
        person: UUID,
        client_id: str | None,
        name: str,
        declared: str,
        data: bytes,
        now: datetime,
    ) -> Outcome:
        """`upload_file(source=text|base64)`: byte nhỏ có sẵn trong lời gọi -> ý định + PUT +
        finalize trong cùng transaction."""
        if len(data) > self.policy.max_inline_bytes:
            raise too_large(self.policy.max_inline_bytes)
        row = self.intent(
            repo, person=person, client_id=client_id, name=name, declared=declared, now=now
        )
        with Spool(self.policy.max_inline_bytes) as spool:
            spool.write(data)
            out = self.ingest(repo, row, spool, now)
            if out.error is not None:
                return out
            return self.finalize(repo, out.row, spool.sha256, now)

    def download_error(self, row: FileRow, person: UUID) -> ToolError | None:
        if row.status == "clean":
            return None
        if row.status == "not_scanned":
            p = self.policy.unscanned_download
            if p == "allow" or (p == "uploader" and row.created_by == person):
                return None
            return precondition_failed(
                "This file was not scanned for malware, so only its uploader can download it.",
                "file_not_scanned",
                remediation="Ask the uploader, or an administrator to enable scanning.",
                policy=p,
            )
        if row.status == "scanning":
            return precondition_failed(
                "The file is still being scanned for malware.",
                "file_scanning",
                remediation="Try again in a minute.",
            )
        if row.status == "infected":
            return precondition_failed(
                "The file was flagged by the malware scanner and was removed.",
                "file_infected",
                remediation="Tell the user the file was rejected.",
            )
        return precondition_failed(
            "The file has no content yet.",
            "file_not_ready",
            remediation="Finish the upload (PUT then finalize) first.",
        )

    def open(self, repo: FileRepository, row: FileRow) -> Iterator[bytes]:
        assert row.object_key is not None
        repo.event(row.id, "downloaded", {})
        return self.store.stream(row.object_key)

    def soft_delete(self, repo: FileRepository, row: FileRow, now: datetime) -> FileRow:
        if row.status == "uploaded":
            self.store.delete(self.quarantine_key(repo.tenant_id(), row.id))
        # Byte theo nội dung (sha256) giữ lại: tệp khác của tenant có thể cùng nội dung;
        # dọn vật lý theo retention là việc của tác vụ nền (không xóa cứng ở đây).
        row = replace(row, deleted_at=now)
        repo.update(row)
        repo.event(row.id, "deleted", {})
        return row


def view(row: FileRow, name: str | None = None) -> dict[str, Any]:
    """Metadata ở bề mặt REST (`name` truyền vào khi đã qua làn PII)."""
    return {
        "id": str(row.id),
        "ref": f"file:{row.id}",
        "name": row.name if name is None else name,
        "declared_type": row.declared_type,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "created_by": f"person:{row.created_by}",
        "upload_expires_at": row.upload_expires_at.isoformat() if row.upload_expires_at else None,
        "finalized_at": row.finalized_at.isoformat() if row.finalized_at else None,
        "scan": {"enabled": row.status in ("scanning", "clean", "infected"),
                 "signature": row.scan_signature},
    }  # fmt: skip


def tool_view(row: FileRow, name: str | None = None) -> dict[str, Any]:
    """`data.file` của `upload_file` (enum trạng thái đóng của contract)."""
    return {
        "ref": f"file:{row.id}",
        "name": row.name if name is None else name,
        "content_type": row.content_type or row.declared_type,
        "size_bytes": row.size_bytes,
        "sha256": row.sha256,
        "status": TOOL_STATUS[row.status],
    }
