# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Digest catalog bảo mật của checkpoint ổn định qua pg_dump/pg_restore (digest v2), vẫn bắt
thay đổi thật; checkpoint digest cũ báo "cần neo lại", không coi là bị sửa."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from xt.adapters.db import audit
from xt.adapters.db.audit import (
    DIGEST_VERSION,
    SECURITY_CATALOG_SQL,
    AnchorResult,
    CatalogDiff,
    canonical_entry,
    checkpoint_outdated,
    sign_checkpoint,
    write_checkpoint,
)
from xt.entrypoints import audit_cli


def test_policy_roles_order_is_irrelevant() -> None:
    # pg_restore tạo lại policy với polroles theo OID mới → thứ tự mảng đổi.
    a = canonical_entry("t|r|(tenant_id = xt.tenant())", ["xt_app", "xt_workflow", "xt_api_owner"])
    b = canonical_entry("t|r|(tenant_id = xt.tenant())", ["xt_api_owner", "xt_workflow", "xt_app"])
    assert a == b


def test_acl_item_order_is_irrelevant() -> None:
    a = canonical_entry("xt_owner|t|t", ["xt_owner=arwdDxtm/xt_owner", "xt_app=r/xt_owner"])
    b = canonical_entry("xt_owner|t|t", ["xt_app=r/xt_owner", "xt_owner=arwdDxtm/xt_owner"])
    assert a == b


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (["xt_app=r/xt_owner"], ["xt_app=rw/xt_owner"]),  # thêm quyền
        (["xt_app=r/xt_owner"], ["xt_app=r/xt_owner", "=r/xt_owner"]),  # cấp PUBLIC
        (["xt_app"], ["xt_app", "public"]),  # policy áp thêm role
        (["xt_app=r/xt_owner"], []),  # thu hết quyền
    ],
)
def test_real_membership_change_is_detected(before: list[str], after: list[str]) -> None:
    assert canonical_entry("d", before) != canonical_entry("d", after)


def test_definition_change_is_detected_and_members_are_separated() -> None:
    assert canonical_entry("t|f", ["x"]) != canonical_entry("t|t", ["x"])
    assert canonical_entry("a", None) == "a"
    assert canonical_entry(None, None) == ""
    # tập rỗng (ACL đã thu hết) khác "không có tập"
    assert canonical_entry("a", []) != canonical_entry("a", None)


def test_catalog_sql_has_no_oid_order_or_null_acl_sources() -> None:
    q = SECURITY_CATALOG_SQL
    for raw in ("relacl::text", "proacl::text", "nspacl::text", "typacl::text", "regrole[]"):
        assert raw not in q
    for default in ("acldefault('r'", "acldefault('f'", "acldefault('n'", "acldefault('T'"):
        assert default in q
    assert "acldefault('f', p.proowner)" in audit.AUDIT_FUNCTIONS_SQL


def test_outdated_detection() -> None:
    assert checkpoint_outdated({"format": audit.CHECKPOINT_FORMAT})  # v1: không có trường
    assert checkpoint_outdated({"digest_version": 1})
    assert not checkpoint_outdated({"digest_version": DIGEST_VERSION})


@pytest.fixture
def env(tmp_path: Path) -> tuple[dict[str, str], Ed25519PrivateKey]:
    key = Ed25519PrivateKey.generate()
    pub = tmp_path / "pub.pem"
    pub.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    e = {
        "XT_DB_APP_URL": "postgresql://unused",
        "XT_AUDIT_CHECKPOINT_DIR": str(tmp_path / "cp"),
        "XT_AUDIT_VERIFY_KEY_FILE": str(pub),
    }
    return e, key


def _run(
    monkeypatch: pytest.MonkeyPatch,
    env: tuple[dict[str, str], Ed25519PrivateKey],
    doc: dict[str, Any],
    *,
    chain: str = "ok",
    catalog: CatalogDiff | None = None,
) -> int:
    e, key = env
    write_checkpoint(sign_checkpoint(doc, key, "k"), Path(e["XT_AUDIT_CHECKPOINT_DIR"]))

    @contextmanager
    def fake_connect(_dsn: str) -> Iterator[object]:
        yield object()

    def fake_diff(_c: Any, _d: Any) -> CatalogDiff:
        assert catalog is not None, "checkpoint cũ không được so digest catalog"
        return catalog

    monkeypatch.setattr(audit_cli, "connect", fake_connect)
    monkeypatch.setattr(
        audit_cli,
        "verify_against_checkpoint",
        lambda _c, _d: [AnchorResult("t1", 3, chain, 3, None)],
    )
    monkeypatch.setattr(audit_cli, "diff_security_catalog", fake_diff)
    return audit_cli.run(["verify", "--against-checkpoint", "latest"], e)


V1: dict[str, Any] = {
    "format": audit.CHECKPOINT_FORMAT,
    "heads": [],
    "security_catalog": {"table:x": "aa"},
}


def test_old_checkpoint_reports_reanchor_not_tampering(
    monkeypatch: pytest.MonkeyPatch, env: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(monkeypatch, env, dict(V1)) == 2
    out = capsys.readouterr().out
    assert "checkpoint cũ — cần neo lại" in out
    assert "catalog bảo mật:" not in out


def test_old_checkpoint_still_reports_broken_chain(
    monkeypatch: pytest.MonkeyPatch, env: Any
) -> None:
    assert _run(monkeypatch, env, dict(V1), chain="mismatch") == 1


def test_current_checkpoint_reports_catalog_tampering(
    monkeypatch: pytest.MonkeyPatch, env: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = {**V1, "digest_version": DIGEST_VERSION}
    diff = CatalogDiff(changed=["policy:xt_t1.x.p"], added=[], removed=[])
    assert _run(monkeypatch, env, doc, catalog=diff) == 1
    assert "catalog đổi: policy:xt_t1.x.p" in capsys.readouterr().out


def test_current_checkpoint_clean(
    monkeypatch: pytest.MonkeyPatch, env: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = {**V1, "digest_version": DIGEST_VERSION}
    assert _run(monkeypatch, env, doc, catalog=CatalogDiff([], [], [])) == 0
    assert "catalog bảo mật: không đổi" in capsys.readouterr().out
