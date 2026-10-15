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
import codecs
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
    BlobError,
    FileRepository,
    FileRow,
    FileStatus,
    FileStore,
    MalwareScanner,
    ScannerUnavailable,
    ScanVerdict,
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

UnscannedPolicy = Literal["deny", "uploader", "readers", "allow"]
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
    "scan_failed": "scan_failed",
    "expired": "expired",
}
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,400}\.[A-Za-z0-9_-]{43}$")


@dataclass(frozen=True, slots=True)
class FilePolicy:
    max_bytes: int = 25 * 1024 * 1024
    max_inline_bytes: int = 1024 * 1024
    upload_ttl: timedelta = timedelta(minutes=15)
    scanning: bool = False
    unscanned_download: UnscannedPolicy = "readers"
    allowed_types: frozenset[str] = field(default_factory=lambda: frozenset(ALLOWED_TYPES))
    # FB-02: quota (đếm từ metadata, cùng transaction có khóa tư vấn theo tenant).
    max_pending_per_user: int = 20
    max_pending_per_tenant: int = 500
    user_quota_bytes: int = 256 * 1024 * 1024
    tenant_quota_bytes: int = 1024 * 1024 * 1024
    # FB-01: số lần clamd lỗi liên tiếp trước khi `scan_failed`.
    scan_max_attempts: int = 5
    # Tệp xóa mềm giữ byte bao lâu trước khi dọn object (khôi phục được trong khoảng này).
    retention: timedelta = timedelta(days=30)

    def __post_init__(self) -> None:
        if not 1 <= self.max_inline_bytes <= self.max_bytes:
            raise ValueError("max_inline_bytes phải trong 1..max_bytes")
        if not timedelta(seconds=10) <= self.upload_ttl <= timedelta(hours=24):
            raise ValueError("upload_ttl phải trong 10 s..24 h")
        if min(self.max_pending_per_user, self.max_pending_per_tenant, self.scan_max_attempts) < 1:
            raise ValueError("quota ý định và số lần quét phải ≥ 1")
        if not self.max_bytes <= self.user_quota_bytes <= self.tenant_quota_bytes:
            raise ValueError("phải có max_bytes ≤ user_quota_bytes ≤ tenant_quota_bytes")
        unknown = self.allowed_types - set(ALLOWED_TYPES)
        if unknown:
            raise ValueError(f"kiểu tệp không có bộ nhận dạng magic byte: {sorted(unknown)}")


@dataclass(frozen=True, slots=True)
class Outcome:
    row: FileRow
    error: ToolError | None = None


def quota_error(reason: str, limit: int, message: str) -> ToolError:
    return ToolError(
        ErrorCode.RESOURCE_CONSTRAINT,
        message,
        remediation="Finish or let open uploads expire, delete files you no longer need, or ask "
        "the administrator to raise the quota.",
        details={"resource": reason, "reason": reason, "limit": limit},
    )


def is_text(data: bytes) -> bool:
    """UTF-8 hợp lệ trọn vẹn, không có NUL (FB-07)."""
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


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
        # FB-07: kiểm UTF-8 + NUL trên TOÀN BỘ luồng (cho họ text), không chỉ 8 KiB đầu.
        self._utf8 = codecs.getincrementaldecoder("utf-8")("strict")
        self._text = True
        self.file: IO[bytes] = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)  # noqa: SIM115

    def write(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.size += len(chunk)
        if self.size > self.limit:
            raise too_large(self.limit)
        self._sha.update(chunk)
        if self._text:
            if b"\x00" in chunk:
                self._text = False
            else:
                try:
                    self._utf8.decode(chunk)
                except UnicodeDecodeError:
                    self._text = False
        if len(self._head) < SNIFF_BYTES:
            self._head += chunk[: SNIFF_BYTES - len(self._head)]
        self.file.write(chunk)

    @property
    def head(self) -> bytes:
        return bytes(self._head)

    @property
    def text_ok(self) -> bool:
        """Toàn bộ byte đã nhận là UTF-8 hợp lệ, đủ ký tự, không có NUL."""
        if self._text:
            try:
                self._utf8.decode(b"", final=True)
            except UnicodeDecodeError:
                self._text = False
        return self._text

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
        # FB-05: chỉ base64url ASCII đúng độ dài, so bằng bytes (không bao giờ TypeError -> 500).
        if not TOKEN_RE.fullmatch(token):
            raise bad
        body, _, mac = token.partition(".")
        try:
            payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        except ValueError:
            raise bad from None
        if base64.urlsafe_b64encode(payload).rstrip(b"=").decode() != body:
            raise bad  # base64 không chuẩn tắc
        if not hmac.compare_digest(mac.encode(), self._mac(payload).encode()):
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

    def check_bytes(self, declared: str, head: bytes, size: int, text_ok: bool = True) -> None:
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
        if found.family == "text" and not text_ok:
            found = replace(found, family="binary")
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
        self.check_quota(repo, person)
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

    def check_quota(self, repo: FileRepository, person: UUID, adding: int = 0) -> None:
        """FB-02: trước khi tạo ý định (`adding=0`) và trước khi nhận/finalize byte."""
        p = self.policy
        u = repo.usage(person)
        if adding == 0 and u.user_pending >= p.max_pending_per_user:
            raise quota_error(
                "pending_uploads_quota",
                p.max_pending_per_user,
                "You have too many unfinished uploads.",
            )
        if adding == 0 and u.tenant_pending >= p.max_pending_per_tenant:
            raise quota_error(
                "pending_uploads_quota",
                p.max_pending_per_tenant,
                "The organization has too many unfinished uploads.",
            )
        if u.user_bytes + adding > p.user_quota_bytes:
            raise quota_error("storage_quota", p.user_quota_bytes, "Your file storage is full.")
        if u.tenant_bytes + adding > p.tenant_quota_bytes:
            raise quota_error(
                "storage_quota", p.tenant_quota_bytes, "The organization's file storage is full."
            )

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
            self.check_bytes(row.declared_type, spool.head, spool.size, spool.text_ok)
            self.check_quota(repo, row.created_by, adding=spool.size)
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
            # FB-01: chỉ chuyển `scanning` rồi commit; quét chạy NGOÀI transaction (`scan_bytes`
            # + `apply_scan`), không giữ khóa dòng/kết nối DB trong lúc chờ clamd.
            row = replace(row, status="scanning", scan_attempts=0)
            repo.update(row)
            repo.event(row.id, "finalized", {"sha256": sha256, "scan": "pending"})
            return Outcome(row)
        row = self._promote(repo, replace(row, status="not_scanned"))
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

    def _promote(self, repo: FileRepository, row: FileRow) -> FileRow:
        """Chuyển quarantine -> khóa theo nội dung, GIỮ khóa nội dung tới commit (R2-F3): purge
        đang chạy thì chờ nó xong rồi mới HEAD (object có thể vừa bị xóa -> copy lại)."""
        assert row.sha256 is not None
        tenant = repo.tenant_id()
        repo.lock_content(row.sha256)
        qkey = self.quarantine_key(tenant, row.id)
        ckey = self.content_key(tenant, row.sha256)
        if self.store.head(ckey) is None:
            self.store.copy(qkey, ckey)
        self.store.delete(qkey)
        return replace(row, object_key=ckey)

    def scan_bytes(self, tenant: UUID, row: FileRow) -> ScanVerdict | None:
        """I/O thuần (S3 -> clamd), không đụng DB. None = clamd/S3 lỗi lần này."""
        if row.status != "scanning" or self.scanner is None:
            return None
        try:
            return self.scanner.scan(self.store.stream(self.quarantine_key(tenant, row.id)))
        except (ScannerUnavailable, BlobError):
            return None

    def apply_scan(
        self, repo: FileRepository, row: FileRow, verdict: ScanVerdict | None
    ) -> FileRow:
        """Ghi kết quả (gọi trong transaction, dòng đã khóa và vẫn `scanning`). Lỗi quét: tăng
        `scan_attempts`; tới `scan_max_attempts` -> `scan_failed` (FB-01), hiện rõ, không kẹt."""
        if row.status != "scanning":
            return row
        tenant = repo.tenant_id()
        qkey = self.quarantine_key(tenant, row.id)
        if verdict is None:
            attempts = row.scan_attempts + 1
            if attempts >= self.policy.scan_max_attempts:
                row = replace(row, status="scan_failed", scan_attempts=attempts)
                repo.update(row)
                repo.event(row.id, "scan_failed", {"attempts": attempts})
            else:
                row = replace(row, scan_attempts=attempts)
                repo.update(row)
            return row
        if verdict.clean:
            row = self._promote(repo, replace(row, status="clean"))
        else:
            self.store.delete(qkey)
            row = replace(row, status="infected", object_key=None, scan_signature=verdict.signature)
        repo.update(row)
        repo.event(row.id, "scanned", {"clean": verdict.clean, "signature": verdict.signature})
        return row

    def scan(self, repo: FileRepository, row: FileRow) -> FileRow:
        """Quét + ghi trong một bước (byte nhỏ của tool, worker đã giải phóng khóa trước)."""
        return self.apply_scan(repo, row, self.scan_bytes(repo.tenant_id(), row))

    def retry_scan(self, repo: FileRepository, row: FileRow) -> FileRow:
        """Đưa `scan_failed` về `scanning` để quét lại (quản trị / worker sau khi clamd ổn)."""
        if row.status != "scan_failed":
            return row
        row = replace(row, status="scanning", scan_attempts=0)
        repo.update(row)
        return row

    def expire_and_purge(self, repo: FileRepository, now: datetime, limit: int = 100) -> int:
        """FB-02: dọn ý định quá hạn (xóa byte quarantine) và object của tệp xóa mềm quá hạn lưu
        mà không còn hàng nào khác trỏ tới (khóa theo nội dung dùng chung)."""
        done = 0
        for row in repo.expired_pending(limit):
            self.current(repo, row, now)
            done += 1
        secs = int(self.policy.retention.total_seconds())
        for row in repo.purgeable_deleted(secs, limit):
            key = row.object_key
            assert key is not None
            if row.sha256 is not None and "/sha256/" in key:
                # R2-F3: quyết định xóa + xóa trong cùng transaction, sau khi giữ khóa nội dung
                # (finalize cùng nội dung chưa commit -> chờ; đã commit -> object_in_use thấy).
                repo.lock_content(row.sha256)
            if not repo.object_in_use(key, row.id):
                self.store.delete(key)
            repo.update(replace(row, object_key=None))
            repo.event(row.id, "purged", {})
            done += 1
        return done

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
            out = self.finalize(repo, out.row, spool.sha256, now)
        if out.error is None and out.row.status == "scanning":
            # Byte nhỏ (≤ max_inline_bytes): quét ngay trong lời gọi tool.
            return Outcome(self.scan(repo, out.row))
        return out

    def download_error(self, row: FileRow, person: UUID) -> ToolError | None:
        if row.status == "clean":
            return None
        if row.status == "not_scanned":
            p = self.policy.unscanned_download
            # Profile full (có ClamAV): chỉ `clean` tải về được (decisions "Tải tệp chưa quét").
            # `readers` = ai qua được `file.read` (người tải lên, phòng, người đọc được bản ghi
            # gắn kèm); tải về luôn `attachment` + `nosniff`, audit ghi trạng thái.
            if self.scanner is None and (
                p in ("allow", "readers") or (p == "uploader" and row.created_by == person)
            ):
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
        if row.status == "scan_failed":
            return precondition_failed(
                "The malware scan could not be completed; the file is blocked.",
                "file_scan_failed",
                remediation="Ask the administrator to check ClamAV and rescan, or upload again.",
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
        repo.event(row.id, "downloaded", {"status": row.status})
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
        "scan": {"enabled": row.status in ("scanning", "clean", "infected", "scan_failed"),
                 "signature": row.scan_signature, "attempts": row.scan_attempts},
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
