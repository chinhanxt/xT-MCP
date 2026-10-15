# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit đối kháng /data (PostgREST) — các vector CHƯA được test_headless_data.py phủ.

Mỗi test khẳng định KẾT QUẢ KHAI THÁC THÀNH CÔNG (hành vi không an toàn) và được đánh dấu
`xfail(strict=True)`: hệ thống an toàn ⇒ test xfail (kỳ vọng); nếu một bản vá bị phá vỡ và khai
thác chạy được ⇒ XPASS ⇒ strict biến thành FAIL, bắt được hồi quy ngay.

Stack: 1 Postgres 17 + 1 PostgREST (image local), tên ngẫu nhiên, tự dọn — dùng lại builder thật
trong tests/integration/headless_stack.py và các adapter thật. Chạy: uv run pytest -m integration
-s -rA tests/integration/data_audit/test_data_audit_probes.py
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from tests.integration import headless_stack as hs
from tests.integration.test_headless_data import (
    NATIONAL_ID_A,
    ORG_A,
    ORG_B,
    TICKET,
    apply,
    catalog,
)
from xt.adapters.db.catalog import PgCatalogReader
from xt.adapters.db.connection import AuditKey, Database
from xt.adapters.db.data_setup import install_data_prerequisites
from xt.adapters.db.integration import import_org, person_id, register_tenant, unit_id
from xt.adapters.db.migrator import PgMigrator
from xt.adapters.db.records import PgRecordRepository
from xt.adapters.db.tenancy import bootstrap, install_postgrest_role, provision_tenant  # noqa: F401
from xt.application.model_engine.service import RecordService
from xt.domain.model.context import ModelActor, TenantRef

pytestmark = pytest.mark.integration


@dataclass
class AEnv:
    st: hs.Stack
    rest: hs.Rest
    app: Database
    migrator: PgMigrator
    records: RecordService
    tenants: dict[str, TenantRef] = field(default_factory=dict)
    ids: dict[str, uuid.UUID] = field(default_factory=dict)

    def profile(self, alias: str) -> str:
        return f"api_t{self.tenants[alias].seq}"

    def actor(self, alias: str, key: str, *roles: str, unit: str | None = None) -> ModelActor:
        t = self.tenants[alias]
        depts = (unit_id(t.id, unit),) if unit else ()
        return ModelActor(t, person_id(t.id, key), roles, department_ids=depts)

    def token(self, alias: str, sub: str, **over: Any) -> str:
        return self.st.signer.sign(hs.data_claims(alias, sub, **over))


@pytest.fixture(scope="module")
def ae() -> Iterator[AEnv]:
    if shutil.which("docker") is None:
        pytest.fail("cần docker (postgres:17-alpine + postgrest local)")
    st = hs.new_stack()
    rest = hs.Rest(f"http://127.0.0.1:{st.rest_port}", hs.Evidence(None))
    app = None
    try:
        with st.su() as su:
            assert bootstrap(
                su,
                migrator_password=st.migrator_password,
                app_password=st.app_password,
                pepper=st.pepper,
            )
        with st.su() as su:
            install_data_prerequisites(su)
        with st.su() as su:
            install_postgrest_role(
                su,
                authenticator_password=st.authenticator_password,
                issuer=hs.ISSUER,
                clients=[hs.DATA_CLIENT],
            )
        key = AuditKey(1, st.pepper)
        app = Database(st.app_dsn, max_size=4, audit_key=key)
        migrator = PgMigrator(st.migrator, lock_timeout="2s", post_lock_timeout="5s", audit_key=key)
        cats = PgCatalogReader(app)
        e = AEnv(st, rest, app, migrator, RecordService(cats, PgRecordRepository(app)))
        with st.migrator() as m:
            e.tenants["acme"] = register_tenant(
                m, alias="acme", name="Acme", timezone="Asia/Ho_Chi_Minh", llm_egress="masked"
            )
            e.tenants["globex"] = register_tenant(
                m,
                alias="globex",
                name="Globex",
                timezone="Asia/Ho_Chi_Minh",
                llm_egress="masked",
            )
            import_org(m, e.tenants["acme"], ORG_A)
            import_org(m, e.tenants["globex"], ORG_B)
        apply(e, e.tenants["acme"], catalog(1, TICKET))  # type: ignore[arg-type]
        apply(e, e.tenants["globex"], catalog(1, TICKET))  # type: ignore[arg-type]
        alice = e.actor("acme", "alice", "manager", unit="kt")
        bob = e.actor("acme", "bob", "staff", unit="kt")
        e.records.create(
            alice,
            "ticket",
            {"title": "A-alice-1", "national_id": NATIONAL_ID_A, "phone": "0901234567"},
        )
        e.records.create(bob, "ticket", {"title": "A-bob-1", "phone": "0907654321"})
        st.start_rest()
        yield e
    finally:
        rest.close()
        if app is not None:
            app.close()
        st.stop()


def _show(title: str, r: hs.Resp) -> None:
    body = str(r.body)
    print(f"\n[{title}] HTTP {r.status} :: {body[:400]}")


# --------------------------------------------------------------------------- probes ----
@pytest.mark.xfail(strict=True, reason="phòng thủ: embedding không được chạm bảng/schema khác")
def test_embedding_reaches_foreign_relation(ae: AEnv) -> None:
    pa = ae.profile("acme")
    tok = ae.token("acme", "kc-alice")
    hit = False
    for path in (
        "/ticket?select=*,person(*)",
        "/ticket?select=*,pgrst_client(*)",
        "/ticket?select=*,tenant(*)",
        "/ticket?select=title,national_id_audit:event(*)",
        "/ticket?select=*,ticket(*)",
    ):
        r = ae.rest.get(path, token=tok, profile=pa)
        _show(f"embed {path}", r)
        if r.status == 200 and isinstance(r.body, list):
            for row in r.body:
                nested = [k for k, v in row.items() if isinstance(v, (list, dict))]
                if nested:
                    hit = True
    assert hit, "embedding bị chặn (không với tới quan hệ ngoài)"


@pytest.mark.xfail(strict=True, reason="phòng thủ: RLS phải chặn staff đọc bản ghi người khác")
def test_staff_filter_bypasses_rls(ae: AEnv) -> None:
    pa = ae.profile("acme")
    bob = ae.token("acme", "kc-bob")  # staff: scope own
    seen: set[str] = set()
    for path in (
        "/ticket?select=title&or=(title.eq.A-alice-1,title.eq.A-bob-1)",
        "/ticket?select=title&title=eq.A-alice-1",
        "/ticket?select=title&and=(title.neq.zzz)",
        "/ticket?select=title&limit=1000&offset=0",
    ):
        r = ae.rest.get(path, token=bob, profile=pa)
        _show(f"bob filter {path}", r)
        if r.status == 200 and isinstance(r.body, list):
            seen |= {row.get("title") for row in r.body}
    assert "A-alice-1" in seen, "RLS giữ scope own: staff không thấy bản ghi của alice"


@pytest.mark.xfail(strict=True, reason="phòng thủ: view loại cột sensitive ⇒ CSV không lộ")
def test_csv_export_leaks_sensitive(ae: AEnv) -> None:
    pa = ae.profile("acme")
    tok = ae.token("acme", "kc-alice")
    leaked = False
    for path in ("/ticket?select=*", "/ticket", "/ticket?select=title,national_id"):
        r = ae.rest.call("GET", path, token=tok, profile=pa, headers={"Accept": "text/csv"})
        _show(f"csv {path}", r)
        if NATIONAL_ID_A in str(r.body):
            leaked = True
    assert leaked, "CSV không chứa giá trị sensitive"


@pytest.mark.xfail(strict=True, reason="phòng thủ: profile không khớp chính xác phải bị từ chối")
def test_profile_header_case_or_encoding_bypass(ae: AEnv) -> None:
    pa = ae.profile("acme")
    seq = ae.tenants["acme"].seq
    tok = ae.token("acme", "kc-alice")
    ok = False
    for prof in (
        pa.upper(),
        f" {pa}",
        f"{pa} ",
        f'"{pa}"',
        f"{pa}\t",
        f"api_T{seq}",
        f"{pa}%00",
        f"{pa},xt",
    ):
        r = ae.rest.get("/ticket?select=title", token=tok, headers={"Accept-Profile": prof})
        _show(f"profile={prof!r}", r)
        if r.status == 200 and isinstance(r.body, list) and r.body:
            ok = True
    assert ok, "mọi biến thể profile đều bị từ chối (so khớp chính xác)"


@pytest.mark.xfail(strict=True, reason="BUG LOW: OPTIONS bỏ qua xt.pgrst_pre ⇒ oracle metadata")
def test_options_bypasses_gate_as_cross_tenant_oracle(ae: AEnv) -> None:
    """OPTIONS KHÔNG kích hoạt db-pre-request của PostgREST nên gate không chạy: token acme hợp lệ
    nhận 200 trên profile của globex và trên bảng bất kỳ đang expose, biến OPTIONS thành oracle
    tồn-tại bảng xuyên tenant. (Chỉ tới được PostgREST trực tiếp; APISIX chặn OPTIONS bằng allowlist
    method GET/HEAD — đây là lớp phòng thủ DUY NHẤT.)"""
    tok = ae.token("acme", "kc-alice")  # token tenant acme
    pb = ae.profile("globex")  # profile tenant KHÁC
    probes = [
        ("own ticket", "/ticket", ae.profile("acme")),
        ("cross-tenant globex ticket", "/ticket", pb),  # gate sẽ 403 nếu chạy
        ("nonexistent table", "/no_such_table", ae.profile("acme")),
    ]
    cross = None
    for label, path, prof in probes:
        r = ae.rest.call("OPTIONS", path, token=tok, headers={"Accept-Profile": prof})
        allow = r.headers.get("allow") or r.headers.get("Allow")
        _show(f"OPTIONS {label} allow={allow}", r)
        if label.startswith("cross"):
            cross = r
    # OPTIONS không token vẫn 401 (JWT của PostgREST có chạy)
    r = ae.rest.call("OPTIONS", "/ticket", headers={"Accept-Profile": ae.profile("acme")})
    _show("OPTIONS no-token", r)
    # KỲ VỌNG AN TOÀN (hiện SAI ⇒ xfail): OPTIONS tới profile tenant khác phải bị gate từ chối
    # (403 profile_mismatch) đúng như GET/HEAD. Khi vá (chạy gate cho OPTIONS, hoặc PostgREST bản
    # mới), test XPASS ⇒ strict báo để gỡ marker.
    assert cross is not None and cross.status == 403, (
        "OPTIONS bỏ qua gate ⇒ oracle metadata chéo tenant"
    )


@pytest.mark.xfail(strict=True, reason="phòng thủ: header request không được giả ngữ cảnh GUC")
def test_request_header_context_spoof(ae: AEnv) -> None:
    pa = ae.profile("acme")
    g = ae.tenants["globex"]
    import json

    forged = json.dumps(
        {"t": str(g.id), "a": str(person_id(g.id, "dave")), "r": ["manager"], "tm": [], "d": []}
    )
    # carol: người acme KHÔNG có vai trò ⇒ hợp lệ nhưng RLS cho 0 dòng. Thử bơm ngữ cảnh qua header.
    tok = ae.token("acme", "kc-carol")
    hit = False
    for hdr in (
        {"xt.pgrst_ctx": forged},
        {"X-Pgrst-Ctx": forged},
        {"Request-Jwt-Claims": '{"organization":"globex"}'},
        {"Role": "xt_app"},
    ):
        h = {"Accept-Profile": pa, **hdr}
        r = ae.rest.call("GET", "/ticket?select=title", token=tok, headers=h)
        _show(f"spoof {list(hdr)}", r)
        if r.status == 200 and isinstance(r.body, list) and r.body:
            hit = True
    # thử đọc trực tiếp profile globex với ngữ cảnh giả qua header
    r = ae.rest.call(
        "GET",
        "/ticket?select=title",
        token=ae.token("acme", "kc-carol"),
        headers={"Accept-Profile": ae.profile("globex"), "xt.pgrst_ctx": forged},
    )
    _show("spoof globex profile", r)
    if r.status == 200 and isinstance(r.body, list) and r.body:
        hit = True
    assert hit, "không bơm được ngữ cảnh qua header request"


@pytest.mark.xfail(strict=True, reason="phòng thủ: tx=rollback/Prefer không mở đường ghi qua GET")
def test_prefer_tx_or_return_writes(ae: AEnv) -> None:
    pa = ae.profile("acme")
    tok = ae.token("acme", "kc-alice")
    wrote = False
    for method, hdr in (
        ("GET", {"Prefer": "tx=commit"}),
        ("GET", {"Prefer": "tx=rollback"}),
    ):
        r = ae.rest.call(
            method,
            "/ticket?id=eq." + str(uuid.uuid4()),
            token=tok,
            content_profile=pa,
            headers=hdr,
            json_body={"title": "x"},
        )
        _show(f"{method} {hdr}", r)
    # POST với Prefer resolution=merge-duplicates (upsert/on_conflict) phải vẫn bị chặn ghi
    r = ae.rest.call(
        "POST",
        "/ticket?on_conflict=title",
        token=tok,
        content_profile=pa,
        headers={"Prefer": "resolution=merge-duplicates,tx=rollback"},
        json_body=[{"title": "A-alice-1", "state": "closed"}],
    )
    _show("POST upsert on_conflict", r)
    if r.status < 300:
        wrote = True
    # kiểm DB: không có dòng title=injected nào
    with ae.st.su() as su:
        from psycopg import sql as _sql

        n = su.execute(
            _sql.SQL("SELECT count(*) FROM {} WHERE title = 'injected'").format(
                _sql.Identifier(f"xt_t{ae.tenants['acme'].seq}", "ticket")
            )
        ).fetchone()
    assert wrote or (n is not None and n[0] > 0), "mọi đường ghi (kể cả upsert) bị chặn"
