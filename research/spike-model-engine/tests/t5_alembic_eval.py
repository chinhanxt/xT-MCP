# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Đánh giá Alembic/SQLAlchemy cho model động: dùng compare_metadata để phát hiện drift."""

import copy
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.dialects import postgresql as pg
from common import *  # noqa
from xtmodel import planner as P, compiler as C
from xtmodel.spec import Catalog

SA = {
    "text": sa.Text,
    "number": sa.Numeric,
    "money": sa.BigInteger,
    "date": sa.Date,
    "datetime": lambda: sa.DateTime(timezone=True),
    "bool": sa.Boolean,
    "enum": sa.Text,
    "ref": pg.UUID,
    "user": pg.UUID,
    "file": pg.UUID,
    "json": pg.JSONB,
}


def brief(x):
    x = x[0] if isinstance(x, list) else x
    if x[0] in ("add_column", "remove_column"):
        return (x[0], x[2], x[3].name)
    if x[0].startswith("modify_"):
        return (x[0], x[2], x[3], x[-1])
    return (x[0], str(x[1])[:60])


def relevant(x):
    k = (x[0] if isinstance(x, list) else x)[0]
    return k not in (
        "add_index",
        "remove_index",
        "remove_table",
        "add_table",
        "remove_constraint",
        "remove_fk",
    )


def to_metadata(cat: Catalog, only=None) -> sa.MetaData:
    md = sa.MetaData(schema="xt_data")
    for m in cat.models:
        if only and m.name not in only:
            continue
        cols = [
            sa.Column("id", pg.UUID, primary_key=True),
            sa.Column("tenant_id", pg.UUID, nullable=False),
            sa.Column("owner_id", pg.UUID),
            sa.Column("team_id", pg.UUID),
            sa.Column("department_id", pg.UUID),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", pg.UUID),
            sa.Column("updated_by", pg.UUID),
            sa.Column("revision", sa.Integer, nullable=False),
        ]
        cols += [
            sa.Column(f.name, SA[f.type](), nullable=not f.required, comment=f.label)
            for f in m.fields
        ]
        sa.Table(m.name, md, *cols, comment=m.label)
    return md


eng = sa.create_engine("postgresql+psycopg://xt_migrator:migrator@localhost:15434/xt")
m = mig()
cat = P.load_current(m)
only = {"khach_hang", "de_xuat_chi"}
with eng.connect() as conn:
    mc = MigrationContext.configure(
        conn,
        opts={
            "include_schemas": True,
            "compare_type": True,
            "include_name": lambda n, t, p: t != "table" or n in only or n is None,
        },
    )
    d = [brief(x) for x in compare_metadata(mc, to_metadata(cat, only)) if relevant(x)]
    print("1) DB khớp spec hiện tại -> khác biệt cột/kiểu/nullable/comment:", d)
with psycopg.connect(SU, autocommit=True) as su:
    su.execute("ALTER TABLE xt_data.khach_hang ADD COLUMN cot_la text")  # drift ngoài luồng duyệt
    su.execute("ALTER TABLE xt_data.khach_hang ALTER COLUMN ten DROP NOT NULL")
with eng.connect() as conn:
    mc = MigrationContext.configure(
        conn,
        opts={
            "include_schemas": True,
            "compare_type": True,
            "include_name": lambda n, t, p: t != "table" or n in only or n is None,
        },
    )
    print(
        "2) Sau khi superuser sửa tay -> drift:",
        [brief(x) for x in compare_metadata(mc, to_metadata(cat, only)) if relevant(x)],
    )
    v = copy.deepcopy(cat.model_dump(mode="json", by_alias=True))
    for f in v["models"][0]["fields"]:
        if f["name"] == "ghi_chu_noi_bo":
            f["name"] = "ghi_chu_rieng"  # rename (cùng id f5)
    d = [
        brief(x)
        for x in compare_metadata(mc, to_metadata(Catalog.model_validate(v), only))
        if relevant(x)
    ]
    print(
        "3) Rename ghi_chu_noi_bo -> ghi_chu_rieng theo autogenerate:",
        [x for x in d if "ghi_chu" in str(x)],
    )
with psycopg.connect(SU, autocommit=True) as su:
    su.execute("ALTER TABLE xt_data.khach_hang DROP COLUMN cot_la")
    su.execute("ALTER TABLE xt_data.khach_hang ALTER COLUMN ten SET NOT NULL")
print(
    "4) Policy RLS / trigger / CHECK đặt tên ổn định: compare_metadata không so sánh policy/trigger; CHECK constraint không được autogenerate so sánh."
)
