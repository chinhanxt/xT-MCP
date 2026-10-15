# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Lớp thứ hai sau pre-request: RLS qua view security_invoker; xt_web không SET ROLE được xt_workflow."""
from common import *  # noqa
with su() as x:
    x.execute("ALTER ROLE xt_web LOGIN PASSWORD 'web'")
try:
    with psycopg.connect(DSN + " user=xt_web password=web") as c:
        with c.transaction():
            ctx(c, 7)
            print("xt_web, ngữ cảnh T7, đọc api_t8.model_00 (bỏ qua pre-request) ->", c.execute("SELECT count(*) FROM api_t8.model_00").fetchone()[0], "dòng")
            print("xt_web, ngữ cảnh T7, đọc api_t7.model_00 ->", c.execute("SELECT count(*) FROM api_t7.model_00").fetchone()[0], "dòng")
        try:
            with c.transaction():
                c.execute("SET ROLE xt_workflow")
            print("xt_web SET ROLE xt_workflow -> ĐƯỢC (lỗi!)")
        except psycopg.Error as e:
            print("xt_web SET ROLE xt_workflow ->", type(e).__name__, e.diag.message_primary)
finally:
    with su() as x:
        x.execute("ALTER ROLE xt_web NOLOGIN")
