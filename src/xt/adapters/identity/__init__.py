# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Adapter danh tính: Keycloak/JWKS, PRM, step-up, biên nhận Ed25519, introspection."""

from xt.adapters.identity.access_token import AccessTokenVerifier, DpopContext
from xt.adapters.identity.client_registry import (
    ConnectorAllowlist,
    KeycloakClientDirectory,
    StaticClientAllowlist,
    build_client_allowlist,
)
from xt.adapters.identity.config import (
    IdentityConfigError,
    IntrospectionMode,
    load_connector_registry_settings,
    load_introspection_settings,
    load_receipt_settings,
    load_resource_server_settings,
    load_step_up_settings,
)
from xt.adapters.identity.dpop import (
    DpopError,
    DpopSettings,
    DpopVerifier,
    build_dpop_verifier,
    load_dpop_settings,
)
from xt.adapters.identity.introspection import (
    KeycloakIntrospector,
    RevocationGuard,
    build_revocation_guard,
)
from xt.adapters.identity.jwks import HttpJwksFetcher, JwksCache, build_jwks_cache
from xt.adapters.identity.receipts import ReceiptService
from xt.adapters.identity.resource_metadata import (
    build_protected_resource_metadata,
    challenge_for,
    extract_bearer,
    extract_credentials,
    protected_resource_metadata_path,
    protected_resource_metadata_url,
)
from xt.adapters.identity.single_use import InMemorySingleUseStore
from xt.adapters.identity.step_up import ConfirmationContext, StepUpVerifier

__all__ = [
    "AccessTokenVerifier",
    "ConfirmationContext",
    "ConnectorAllowlist",
    "DpopContext",
    "DpopError",
    "DpopSettings",
    "DpopVerifier",
    "HttpJwksFetcher",
    "IdentityConfigError",
    "InMemorySingleUseStore",
    "IntrospectionMode",
    "JwksCache",
    "KeycloakClientDirectory",
    "KeycloakIntrospector",
    "ReceiptService",
    "RevocationGuard",
    "StaticClientAllowlist",
    "StepUpVerifier",
    "build_client_allowlist",
    "build_dpop_verifier",
    "build_jwks_cache",
    "build_protected_resource_metadata",
    "build_revocation_guard",
    "challenge_for",
    "extract_bearer",
    "extract_credentials",
    "load_connector_registry_settings",
    "load_dpop_settings",
    "load_introspection_settings",
    "load_receipt_settings",
    "load_resource_server_settings",
    "load_step_up_settings",
    "protected_resource_metadata_path",
    "protected_resource_metadata_url",
]
