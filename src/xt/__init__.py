# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""xT - Agent-native DX-OS."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("xt")
except PackageNotFoundError:  # chạy từ cây nguồn chưa cài
    __version__ = "0+unknown"
