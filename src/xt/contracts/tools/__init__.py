# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""ToolContract v1: một nguồn sinh đăng ký MCP, catalog/docs, annotations và contract test."""

from xt.contracts.tools.catalog import BY_NAME, CATALOG, get_contract
from xt.contracts.tools.contract import (
    Annotations,
    Effects,
    ToolContract,
    derive_annotations,
    title_from_name,
)
from xt.contracts.tools.digest import catalog_digest, tool_count
from xt.contracts.tools.envelope import (
    CONFIRMATION_DETAILS,
    CONTRACT_VERSION,
    ERROR_CODES,
    ERROR_ENVELOPE,
    GUIDANCE,
    RETRYABLE,
    UNIVERSAL_ERRORS,
    ErrorCode,
    success_envelope,
)
from xt.contracts.tools.profiles import CORE_10, PROFILES, Profile, profile_tools, profiles_of
from xt.contracts.tools.strict_json import (
    DuplicateKeyError,
    JsonTooDeepError,
    check_depth,
    find_duplicate_keys,
    loads_strict,
)
from xt.contracts.tools.validation import StrictValidator, ecma_pattern, input_issues

__all__ = [
    "BY_NAME",
    "CATALOG",
    "CONFIRMATION_DETAILS",
    "CONTRACT_VERSION",
    "CORE_10",
    "ERROR_CODES",
    "ERROR_ENVELOPE",
    "GUIDANCE",
    "PROFILES",
    "RETRYABLE",
    "UNIVERSAL_ERRORS",
    "Annotations",
    "DuplicateKeyError",
    "Effects",
    "ErrorCode",
    "JsonTooDeepError",
    "Profile",
    "StrictValidator",
    "ToolContract",
    "catalog_digest",
    "check_depth",
    "derive_annotations",
    "ecma_pattern",
    "find_duplicate_keys",
    "get_contract",
    "input_issues",
    "loads_strict",
    "profile_tools",
    "profiles_of",
    "success_envelope",
    "title_from_name",
    "tool_count",
]
