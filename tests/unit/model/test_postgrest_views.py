# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""New API views retain the request gate, PII projection and read-only grants."""

from tests.unit.model.support import catalog, customer
from xt.adapters.db.compiler import Layout, view_steps


def test_api_view_filters_disabled_models_without_widening_access() -> None:
    spec = customer()
    spec["fields"][-1]["pii"] = "sensitive"
    model = catalog(spec).models[0]
    create, grant = view_steps(Layout(7), model, readers=("bi_reader",))
    query = create.text()
    assert '"api_t7"."customer"' in query and 'FROM "xt_t7"."customer"' in query
    assert '"note"' not in query and '"email"' in query
    assert "session_user <> 'xt_authenticator'" in query
    assert "'disabled_models') ? 'customer'), false)" in query
    assert create.role == grant.role == "xt_api_owner"
    assert grant.text() == 'GRANT SELECT ON "api_t7"."customer" TO "xt_web", "bi_reader"'
