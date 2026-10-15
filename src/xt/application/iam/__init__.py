# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""PEP của IAM: ranh giới tenant/XRN, tự chủ, SoD, receipt, delegation, đại diện."""

from xt.application.iam.guard import AccessGuard
from xt.application.iam.ports import ApprovalStore, DelegationStore, PrincipalDirectory
from xt.application.iam.store import PolicyApprovalError, PolicyStore

__all__ = [
    "AccessGuard",
    "ApprovalStore",
    "DelegationStore",
    "PolicyApprovalError",
    "PolicyStore",
    "PrincipalDirectory",
]
