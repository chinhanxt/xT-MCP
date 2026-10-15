# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cổng mà PEP cần; adapter (DB/identity) hiện thực. Chỉ đọc dữ liệu host tin cậy."""

from __future__ import annotations

from typing import Protocol

from xt.domain.iam import ApprovalReceipt, DelegationGrant, Principal


class PrincipalDirectory(Protocol):
    def get(self, principal_id: str) -> Principal | None: ...


class ApprovalStore(Protocol):
    def get(self, key: str) -> ApprovalReceipt | None: ...


class DelegationStore(Protocol):
    def get(self, key: str) -> DelegationGrant | None: ...
