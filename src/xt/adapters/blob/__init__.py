# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Object storage (S3/SeaweedFS) và máy quét virus (clamd) cho dịch vụ tệp."""

from xt.adapters.blob.clamd import ClamdScanner
from xt.adapters.blob.s3 import S3FileStore

__all__ = ["ClamdScanner", "S3FileStore"]
