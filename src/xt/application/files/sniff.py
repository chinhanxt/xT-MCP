# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Danh sách kiểu tệp cho phép + nhận dạng bằng magic byte (không tin `Content-Type` client gửi).

Quy tắc: kiểu khai báo phải nằm trong allowlist VÀ byte đầu tệp phải khớp đúng họ của kiểu đó.
Tệp văn bản (`text/*`, `application/json`) phải là UTF-8 hợp lệ, không có byte NUL và không mang
chữ ký của một định dạng nhị phân/thực thi. HTML/SVG/JS cố ý không có trong allowlist (XSS khi tải
về cùng origin); PE/ELF/Mach-O/script shebang luôn bị từ chối.
"""

from __future__ import annotations

import codecs
from collections.abc import Mapping
from dataclasses import dataclass

SNIFF_BYTES = 8192

_OOXML = "application/vnd.openxmlformats-officedocument."
ALLOWED_TYPES: Mapping[str, str] = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
    f"{_OOXML}wordprocessingml.document": "zip",
    f"{_OOXML}spreadsheetml.sheet": "zip",
    f"{_OOXML}presentationml.presentation": "zip",
    "application/vnd.oasis.opendocument.text": "zip",
    "application/vnd.oasis.opendocument.spreadsheet": "zip",
    "application/zip": "zip",
    "text/plain": "text",
    "text/csv": "text",
    "text/markdown": "text",
    "application/json": "text",
}

EXTENSIONS: Mapping[str, str] = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "docx": f"{_OOXML}wordprocessingml.document",
    "xlsx": f"{_OOXML}spreadsheetml.sheet",
    "pptx": f"{_OOXML}presentationml.presentation",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "zip": "application/zip",
    "txt": "text/plain",
    "log": "text/plain",
    "csv": "text/csv",
    "md": "text/markdown",
    "json": "application/json",
}

# Chữ ký nhị phân: (họ, tiền tố). Họ "deny" = luôn từ chối dù khai báo gì.
_SIGNATURES: tuple[tuple[str, bytes], ...] = (
    ("pdf", b"%PDF-"),
    ("png", b"\x89PNG\r\n\x1a\n"),
    ("jpeg", b"\xff\xd8\xff"),
    ("gif", b"GIF87a"),
    ("gif", b"GIF89a"),
    ("zip", b"PK\x03\x04"),
    ("deny", b"MZ"),  # PE (exe/dll)
    ("deny", b"\x7fELF"),
    ("deny", b"\xfe\xed\xfa\xce"),  # Mach-O
    ("deny", b"\xfe\xed\xfa\xcf"),
    ("deny", b"\xcf\xfa\xed\xfe"),
    ("deny", b"\xce\xfa\xed\xfe"),
    ("deny", b"\xca\xfe\xba\xbe"),  # Mach-O fat / Java class
    ("deny", b"#!"),
    ("deny", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),  # OLE2 (doc/xls cũ có macro)
    ("deny", b"Rar!\x1a\x07"),
    ("deny", b"7z\xbc\xaf\x27\x1c"),
    ("deny", b"\x1f\x8b"),  # gzip
)
_MARKUP = (b"<!doctype html", b"<html", b"<script", b"<svg", b"<?xml")


@dataclass(frozen=True, slots=True)
class Sniffed:
    family: str  # pdf | png | jpeg | gif | webp | zip | text | deny | unknown
    detail: str = ""


def normalize_type(value: str | None) -> str | None:
    if value is None:
        return None
    t = value.split(";", 1)[0].strip().lower()
    return t or None


def type_for_name(name: str) -> str | None:
    """Kiểu theo phần mở rộng (chỉ để gợi ý khi client không khai báo)."""
    _, dot, ext = name.rpartition(".")
    return EXTENSIONS.get(ext.lower()) if dot else None


def sniff(head: bytes, *, complete: bool) -> Sniffed:
    """Nhận dạng họ định dạng từ tối đa `SNIFF_BYTES` byte đầu. `complete` = `head` là toàn bộ
    tệp (khi đó UTF-8 cắt dở ở cuối không còn được tha)."""
    for family, prefix in _SIGNATURES:
        if head.startswith(prefix):
            return Sniffed(family, prefix.hex())
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return Sniffed("webp")
    if b"\x00" in head:
        return Sniffed("unknown", "nul")
    lowered = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if any(lowered.startswith(m) for m in _MARKUP):
        return Sniffed("deny", "markup")
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        decoder.decode(head, final=complete)
    except UnicodeDecodeError:
        return Sniffed("unknown", "not_utf8")
    return Sniffed("text")


def matches(declared: str, found: Sniffed) -> bool:
    family = ALLOWED_TYPES.get(declared)
    return family is not None and found.family == family
