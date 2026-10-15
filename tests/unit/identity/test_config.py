# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, generate_private_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from xt.adapters.identity.config import (
    IdentityConfigError,
    IntrospectionMode,
    load_introspection_settings,
    load_receipt_settings,
    load_resource_server_settings,
    load_step_up_settings,
)

BASE = {
    "XT_IDP_ISSUER": "https://sso.example.test/realms/xt",
    "XT_IDP_JWKS_URL": "http://keycloak:8080/realms/xt/protocol/openid-connect/certs",
    "XT_IDP_ALLOW_HTTP": "true",
    "XT_MCP_RESOURCE": "https://xt.example.test/mcp",
    "XT_MCP_ALLOWED_CLIENTS": "claude, chatgpt ,",
    "XT_MCP_REQUIRED_SCOPES": "mcp-xt",
    "XT_MCP_SCOPES_SUPPORTED": "openid email mcp-xt",
}


def problems(fn: object, env: dict[str, str]) -> list[str]:
    assert callable(fn)
    with pytest.raises(IdentityConfigError) as e:
        fn(env)
    return e.value.problems


def test_resource_server_ok_with_defaults() -> None:
    s = load_resource_server_settings(BASE)
    assert s.allowed_clients == {"claude", "chatgpt"}
    assert s.required_scopes == {"mcp-xt"} and s.scopes_supported == ("openid", "email", "mcp-xt")
    assert s.idp.algorithms == {"RS256", "ES256"}
    assert (s.idp.leeway_s, s.idp.max_token_lifetime_s, s.idp.jwks_ttl_s) == (30, 900, 300)
    assert s.idp.jwks_min_refetch_interval_s == 10 and s.idp.tenant_claim == "organization"


def test_empty_env_lists_every_missing_var() -> None:
    p = problems(load_resource_server_settings, {})
    for name in (
        "XT_IDP_ISSUER",
        "XT_IDP_JWKS_URL",
        "XT_MCP_RESOURCE",
        "XT_MCP_ALLOWED_CLIENTS",
        "XT_MCP_REQUIRED_SCOPES",
        "XT_MCP_SCOPES_SUPPORTED",
    ):
        assert any(name in x for x in p), name


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"XT_IDP_ALLOW_HTTP": "false"}, "dùng http"),
        ({"XT_IDP_ALLOW_HTTP": "maybe"}, "boolean"),
        ({"XT_IDP_ISSUER": "sso.example.test"}, "URL http(s)"),
        ({"XT_IDP_ISSUER": "https://u:p@sso.example.test/x"}, "đăng nhập"),
        ({"XT_MCP_RESOURCE": "https://xt.example.test/mcp#x"}, "fragment"),
        ({"XT_MCP_RESOURCE": "https://xt.example.test/mcp?t=1"}, "query"),
        ({"XT_IDP_ALGORITHMS": "RS256,HS256"}, "HS256"),
        ({"XT_IDP_ALGORITHMS": "none"}, "none"),
        ({"XT_MCP_ALLOWED_CLIENTS": " , "}, "XT_MCP_ALLOWED_CLIENTS rỗng"),
        ({"XT_MCP_SCOPES_SUPPORTED": "openid"}, "thiếu scope bắt buộc"),
        ({"XT_MCP_SCOPES_SUPPORTED": "openid mcp-xt offline_access"}, "offline_access"),
        ({"XT_IDP_LEEWAY_SECONDS": "600"}, "ngoài khoảng"),
        ({"XT_IDP_LEEWAY_SECONDS": "abc"}, "số nguyên"),
        ({"XT_IDP_HTTP_TIMEOUT_SECONDS": "x"}, "không phải số"),
        ({"XT_IDP_HTTP_TIMEOUT_SECONDS": "100"}, "ngoài khoảng"),
        ({"XT_IDP_JWKS_TTL_SECONDS": "5", "XT_IDP_JWKS_MIN_REFETCH_SECONDS": "10"}, "<="),
        ({"XT_MCP_HUMAN_CLIENTS": "claude"}, "vừa là agent vừa là human"),
        ({"XT_IDP_MAX_TOKEN_BYTES": "10"}, "ngoài khoảng"),
    ],
)
def test_resource_server_misconfig(over: dict[str, str], needle: str) -> None:
    p = problems(load_resource_server_settings, {**BASE, **over})
    assert any(needle in x for x in p), p


def test_step_up() -> None:
    env = {**BASE, "XT_CONFIRM_CLIENT_ID": "xt-desk", "XT_CONFIRM_ACR": "urn:xt:acr:otp"}
    s = load_step_up_settings(env)
    assert (s.client_id, s.acr, s.max_auth_age_s, s.required_amr) == (
        "xt-desk",
        "urn:xt:acr:otp",
        300,
        frozenset(),
    )
    s2 = load_step_up_settings({**env, "XT_CONFIRM_REQUIRED_AMR": "otp"})
    assert s2.required_amr == {"otp"}
    p = problems(load_step_up_settings, BASE)
    assert any("XT_CONFIRM_CLIENT_ID" in x for x in p) and any("XT_CONFIRM_ACR" in x for x in p)


def test_receipt_key_loading(tmp_path: Path) -> None:
    good = tmp_path / "ed.pem"
    good.write_bytes(
        Ed25519PrivateKey.generate().private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        )
    )
    s = load_receipt_settings({"XT_RECEIPT_ISSUER": "xt", "XT_RECEIPT_SIGNING_KEY_FILE": str(good)})
    assert s.ttl_s == 300 and s.issuer == "xt"
    wrong = tmp_path / "ec.pem"
    wrong.write_bytes(
        generate_private_key(SECP256R1()).private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        )
    )
    for path, needle in (
        (wrong, "Ed25519"),
        (tmp_path / "missing.pem", "No such file"),
        (tmp_path, "Is a directory"),
    ):
        env = {"XT_RECEIPT_ISSUER": "xt", "XT_RECEIPT_SIGNING_KEY_FILE": str(path)}
        assert any(needle in x for x in problems(load_receipt_settings, env))
    p = problems(load_receipt_settings, {})
    assert len(p) == 2


def test_introspection_modes() -> None:
    off = load_introspection_settings({"XT_IDP_INTROSPECTION": "off"})
    assert off.mode is IntrospectionMode.OFF
    p = problems(load_introspection_settings, {})
    assert any("XT_IDP_INTROSPECTION bắt buộc" in x for x in p)
    p = problems(load_introspection_settings, {"XT_IDP_INTROSPECTION": "sometimes"})
    assert any("'off' hoặc 'high_risk'" in x for x in p)
    p = problems(
        load_introspection_settings,
        {"XT_IDP_INTROSPECTION": "off", "XT_IDP_INTROSPECTION_CLIENT_ID": "x"},
    )
    assert any("XT_IDP_INTROSPECTION_CLIENT_ID" in x for x in p)
    p = problems(load_introspection_settings, {"XT_IDP_INTROSPECTION": "high_risk"})
    assert len(p) == 3
    s = load_introspection_settings(
        {
            "XT_IDP_INTROSPECTION": "high_risk",
            "XT_IDP_INTROSPECTION_URL": "https://kc/realms/xt/protocol/openid-connect/token/introspect",
            "XT_IDP_INTROSPECTION_CLIENT_ID": "xt-api",
            "XT_IDP_INTROSPECTION_CLIENT_SECRET": "s",
            "XT_IDP_INTROSPECTION_CACHE_SECONDS": "60",
        }
    )
    assert s.mode is IntrospectionMode.HIGH_RISK and s.cache_ttl_s == 60
    p = problems(
        load_introspection_settings,
        {"XT_IDP_INTROSPECTION": "off", "XT_IDP_INTROSPECTION_CACHE_SECONDS": "61"},
    )
    assert any("ngoài khoảng" in x for x in p)


def test_reads_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in BASE.items():
        monkeypatch.setenv(k, v)
    assert load_resource_server_settings().resource == BASE["XT_MCP_RESOURCE"]
