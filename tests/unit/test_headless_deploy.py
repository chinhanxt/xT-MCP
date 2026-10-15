# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Kiểm biên tin cậy JWKS của công cụ vận hành PostgREST, không gọi IdP thật."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm


@pytest.fixture(scope="module")
def fetcher() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "deploy/scripts/fetch_jwks.py"
    spec = importlib.util.spec_from_file_location("xt_test_fetch_jwks", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def public_jwk() -> dict[str, Any]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    result: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    result.update(kid="rotation-key", use="sig", alg="RS256")
    return result


def test_only_verification_keys_reach_postgrest(
    fetcher: ModuleType, public_jwk: dict[str, Any]
) -> None:
    keys = [
        public_jwk,
        {**public_jwk, "kid": "encryption", "use": "enc"},
        {"kid": "shared-secret", "kty": "oct", "alg": "HS256"},
        {**public_jwk, "kid": "weak", "n": "AQAB"},
    ]
    accepted = fetcher.build_jwks({"keys": keys})["keys"]
    assert len(accepted) == 1 and accepted[0]["kid"] == public_jwk["kid"]
    got = RSAAlgorithm.from_jwk(accepted[0])
    expected = RSAAlgorithm.from_jwk(public_jwk)
    assert isinstance(got, rsa.RSAPublicKey) and isinstance(expected, rsa.RSAPublicKey)
    assert got.public_numbers() == expected.public_numbers()


def test_private_material_rejects_entire_jwks(
    fetcher: ModuleType, public_jwk: dict[str, Any]
) -> None:
    with pytest.raises(SystemExit):
        fetcher.build_jwks({"keys": [public_jwk, {**public_jwk, "kid": "private", "d": "AQAB"}]})


def test_duplicate_kid_is_not_selected_arbitrarily(
    fetcher: ModuleType, public_jwk: dict[str, Any]
) -> None:
    with pytest.raises(SystemExit):
        fetcher.build_jwks({"keys": [public_jwk, public_jwk]})


@pytest.mark.parametrize("document", [{}, {"keys": []}, {"keys": "not-an-array"}])
def test_missing_verification_keys_fail_closed(fetcher: ModuleType, document: Any) -> None:
    with pytest.raises(SystemExit):
        fetcher.build_jwks(document)


@pytest.mark.parametrize("operations", [["encrypt"], ["sign"], "verify", []])
def test_key_operations_must_allow_verification(
    fetcher: ModuleType, public_jwk: dict[str, Any], operations: Any
) -> None:
    with pytest.raises(SystemExit):
        fetcher.build_jwks({"keys": [{**public_jwk, "key_ops": operations}]})


def test_explicit_verification_operation_is_accepted(
    fetcher: ModuleType, public_jwk: dict[str, Any]
) -> None:
    assert fetcher.build_jwks({"keys": [{**public_jwk, "key_ops": ["verify"]}]})["keys"]


def test_data_scope_revocation_enumerates_all_client_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deploy = Path(__file__).resolve().parents[2] / "deploy"
    monkeypatch.syspath_prepend(str(deploy / "keycloak"))
    monkeypatch.syspath_prepend(str(deploy / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "xt_test_provision_data", deploy / "keycloak/provision_data.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class ClientPages:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def get(self, path: str) -> list[dict[str, str]]:
            self.paths.append(path)
            if "first=0&" in path:
                return [{"id": str(i)} for i in range(200)]
            assert "first=200&" in path
            return [{"id": "last-client"}]

    kc = ClientPages()
    assert [c["id"] for c in module.all_clients(kc, "xt")][-1] == "last-client"
    assert len(kc.paths) == 2


def test_gateway_blocks_options_that_bypass_postgrest_context_gate() -> None:
    template = (Path(__file__).resolve().parents[2] / "deploy/apisix/apisix.yaml.tmpl").read_text()
    route = re.search(r"(?ms)^  - id: api-data\b.*?(?=^  - id:|\Z)", template)
    assert route is not None
    methods = re.search(r"methods:\s*(\[[^\]]+\])", route.group())
    assert methods is not None
    assert set(json.loads(methods.group(1))) == {"GET", "HEAD"}
