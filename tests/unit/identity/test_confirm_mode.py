# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`XT_CONFIRM_MODE_FILE` (chỉ demo): acr/amr step-up theo tệp, đọc mỗi request, cache mtime."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.unit.identity.factory import FakeClock, SigningKey, sign, su_settings
from tests.unit.identity.test_config import BASE
from tests.unit.identity.test_step_up import CTX, id_claims
from xt.adapters.identity import confirm_mode
from xt.adapters.identity.config import load_step_up_settings
from xt.adapters.identity.jwks import JwksCache
from xt.adapters.identity.step_up import StepUpVerifier, confirm_authorization_url
from xt.domain.identity import StepUpRejectedError

OTP = "urn:xt:acr:otp"
PWD = "urn:xt:acr:pwd"


def write_mode(path: Path, body: str, bump: int = 0) -> None:
    path.write_text(body, encoding="utf-8")
    st = path.stat()  # mtime thô trên vài FS: ép thay đổi để kiểm cache theo mtime
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + bump))


def id_token(key: SigningKey, acr: str, amr: list[str], jti: str) -> str:
    return sign(key, id_claims(acr=acr, amr=amr, jti=jti))


def test_unset_keeps_env_behaviour() -> None:
    s = su_settings()
    assert s.mode_file is None and s.effective_acr() == OTP
    assert s.effective_required_amr() == {"otp"}
    s2 = su_settings(acr=PWD, required_amr=frozenset({"pwd"}))
    assert (s2.effective_acr(), s2.effective_required_amr()) == (PWD, frozenset({"pwd"}))


def test_loader_reads_mode_file_env(tmp_path: Path) -> None:
    env = {**BASE, "XT_CONFIRM_CLIENT_ID": "xt-desk", "XT_CONFIRM_ACR": OTP}
    assert load_step_up_settings(env).mode_file is None
    assert load_step_up_settings({**env, "XT_CONFIRM_MODE_FILE": ""}).mode_file is None
    f = tmp_path / "mode.json"
    assert load_step_up_settings({**env, "XT_CONFIRM_MODE_FILE": str(f)}).mode_file == f


def test_mode_file_maps_true_false_and_follows_changes(tmp_path: Path) -> None:
    f = tmp_path / "mode.json"
    s = su_settings(mode_file=f, acr="urn:x:fallback", required_amr=frozenset({"x"}))
    write_mode(f, '{"otp": true}')
    assert (s.effective_acr(), s.effective_required_amr()) == (OTP, frozenset({"otp"}))
    write_mode(f, '{"otp": false}', bump=1_000_000)
    assert (s.effective_acr(), s.effective_required_amr()) == (PWD, frozenset({"pwd"}))
    write_mode(f, '{"otp": true, "updated_at": "now"}', bump=2_000_000)
    assert s.effective_acr() == OTP


@pytest.mark.parametrize(
    "body",
    ["", "not json", "[]", '{"otp": "true"}', '{"otp": 1}', "{}", '{"otp": true}' + " " * 5000],
)
def test_missing_or_bad_file_falls_back_to_env(tmp_path: Path, body: str | None) -> None:
    f = tmp_path / "mode.json"
    s = su_settings(mode_file=f)
    assert s.effective_acr() == OTP  # tệp thiếu
    write_mode(f, body)
    assert (s.effective_acr(), s.effective_required_amr()) == (OTP, frozenset({"otp"}))


def test_cache_by_mtime_avoids_rereading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "mode.json"
    write_mode(f, '{"otp": false}')
    reads: list[int] = []
    real = confirm_mode._parse

    def counting(path: Path, size: int) -> Any:
        reads.append(1)
        return real(path, size)

    monkeypatch.setattr(confirm_mode, "_parse", counting)
    s = su_settings(mode_file=f)
    for _ in range(5):
        assert s.effective_acr() == PWD
    assert len(reads) == 1
    write_mode(f, '{"otp": true}', bump=5_000_000)
    assert s.effective_acr() == OTP and len(reads) == 2


def test_verifier_follows_mode_file(
    tmp_path: Path, jwks: JwksCache, clock: FakeClock, rsa_key: SigningKey
) -> None:
    f = tmp_path / "mode.json"
    v = StepUpVerifier(su_settings(mode_file=f), jwks, clock)
    pwd_tok = lambda j: id_token(rsa_key, PWD, ["pwd"], j)  # noqa: E731
    otp_tok = lambda j: id_token(rsa_key, OTP, ["pwd", "otp"], j)  # noqa: E731

    write_mode(f, '{"otp": false}')
    assert v.verify(pwd_tok("j1"), CTX).acr == PWD
    with pytest.raises(StepUpRejectedError) as e:
        v.verify(otp_tok("j2"), CTX)
    assert e.value.reason == "acr_insufficient"

    write_mode(f, '{"otp": true}', bump=1_000_000)
    assert v.verify(otp_tok("j3"), CTX).acr == OTP
    with pytest.raises(StepUpRejectedError) as e2:
        v.verify(pwd_tok("j4"), CTX)
    assert e2.value.reason == "acr_insufficient"


def test_authorization_url_uses_current_acr(tmp_path: Path) -> None:
    f = tmp_path / "mode.json"
    s = su_settings(mode_file=f)
    write_mode(f, '{"otp": false}')

    def acr_values() -> str:
        url = confirm_authorization_url(
            s,
            "https://sso.example.test/auth",
            "https://x/cb",
            state="s",
            nonce="n",
            code_challenge="c",
        )
        return parse_qs(urlsplit(url).query)["acr_values"][0]

    assert acr_values() == PWD
    write_mode(f, '{"otp": true}', bump=1_000_000)
    assert acr_values() == OTP
