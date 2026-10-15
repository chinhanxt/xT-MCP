# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Dịch vụ dữ liệu phi cấu trúc (tệp): cổng + vòng đời + nhận dạng kiểu. Xem docs/files-api.md."""

from xt.application.files.ports import (
    BlobError,
    BlobInfo,
    FileRepository,
    FileRow,
    FileStatus,
    FileStore,
    MalwareScanner,
    ScannerUnavailable,
    ScanVerdict,
    Usage,
)
from xt.application.files.service import (
    FileManager,
    FilePolicy,
    Outcome,
    Spool,
    UploadGrant,
    UploadTokens,
    tool_view,
    view,
)

__all__ = [
    "BlobError",
    "BlobInfo",
    "FileManager",
    "FilePolicy",
    "FileRepository",
    "FileRow",
    "FileStatus",
    "FileStore",
    "MalwareScanner",
    "Outcome",
    "ScanVerdict",
    "ScannerUnavailable",
    "Spool",
    "UploadGrant",
    "UploadTokens",
    "Usage",
    "tool_view",
    "view",
]
