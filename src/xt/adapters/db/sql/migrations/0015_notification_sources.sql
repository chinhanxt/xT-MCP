-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
CREATE TABLE IF NOT EXISTS xt_meta.notification_source (
    tenant_id uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
    source_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, source_key)
);
ALTER TABLE xt_meta.notification_source ENABLE ROW LEVEL SECURITY;
ALTER TABLE xt_meta.notification_source FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON xt_meta.notification_source TO xt_app
    USING (tenant_id=xt.tenant()) WITH CHECK (tenant_id=xt.tenant());
GRANT SELECT, INSERT ON xt_meta.notification_source TO xt_app;
