# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit kênh người xem PII (docs/audit-pii-reveal.md): dùng lại fixture PG17 + IdP test của
`tests/integration/composition` (một container postgres tên/cổng ngẫu nhiên, tự dọn)."""

from tests.integration.composition.conftest import (  # noqa: F401
    app_db,
    composition,
    idp,
    mcp_port,
    pg,
    tenants,
)
