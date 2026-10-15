# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""EXPLAIN truy vấn trang 'staff own' có gợi ý phạm vi (chạy sau t6)."""

import uuid
from common import *  # noqa

with psycopg.connect(APP) as c, c.transaction():
    actor = uuid.UUID(int=0x5000)
    c.execute(
        "SELECT set_config('xt.tenant_id', %s, true), set_config('xt.actor_id', %s, true), set_config('xt.roles','staff',true)",
        (str(uuid.UUID(int=0xA)), str(actor)),
    )
    for q in (
        "SELECT count(*) FROM xt_data.de_xuat_chi WHERE true AND (owner_id = %s)",
        "SELECT * FROM xt_data.de_xuat_chi WHERE true AND (owner_id = %s) ORDER BY created_at DESC, id LIMIT 20 OFFSET 40",
    ):
        print(q)
        for r in c.execute(
            "EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON) " + q, (actor,)
        ).fetchall():
            if "Filter:" not in r[0]:
                print("   ", r[0])
