-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Derived query metadata only. Expired entries are removed on the next aggregate in the tenant.
CREATE TABLE xt_meta.aggregate_explain (
  tenant_id uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  trace_id text NOT NULL CHECK (char_length(trace_id) BETWEEN 1 AND 128),
  actor_key text NOT NULL CHECK (char_length(actor_key) = 64),
  query jsonb NOT NULL,
  expires_at timestamptz NOT NULL DEFAULT (clock_timestamp() + interval '24 hours'),
  PRIMARY KEY (tenant_id, trace_id)
);
CREATE INDEX aggregate_explain_expiry ON xt_meta.aggregate_explain (tenant_id, expires_at);
ALTER TABLE xt_meta.aggregate_explain ENABLE ROW LEVEL SECURITY;
ALTER TABLE xt_meta.aggregate_explain FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON xt_meta.aggregate_explain TO xt_app
  USING (tenant_id = xt.tenant()) WITH CHECK (tenant_id = xt.tenant());
GRANT SELECT, INSERT, DELETE ON xt_meta.aggregate_explain TO xt_app;
