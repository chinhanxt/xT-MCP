-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Chạy bằng xt_migrator (xt db migrate). Instance quy trình + outbox hẹn giờ theo tenant.
CREATE TABLE IF NOT EXISTS xt_meta.workflow_instance (
 tenant_id uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
 id text NOT NULL,
 process_id text NOT NULL,
 version integer NOT NULL CHECK (version > 0),
 status text NOT NULL,
 state json NOT NULL,
 candidates text[] NOT NULL,
 next_due timestamptz,
 PRIMARY KEY (tenant_id, id)
);
CREATE TABLE IF NOT EXISTS xt_meta.workflow_request (
 tenant_id uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
 ref text NOT NULL,
 instance_id text NOT NULL,
 PRIMARY KEY (tenant_id, ref),
 UNIQUE (tenant_id, instance_id),
 FOREIGN KEY (tenant_id, instance_id) REFERENCES xt_meta.workflow_instance(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS xt_meta.workflow_timer_outbox (
 tenant_id uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
 instance_id text NOT NULL,
 process_id text NOT NULL,
 due timestamptz NOT NULL,
 dispatched_at timestamptz,
 PRIMARY KEY (tenant_id, instance_id, due)
);
CREATE INDEX IF NOT EXISTS workflow_candidates ON xt_meta.workflow_instance USING gin(candidates);
CREATE INDEX IF NOT EXISTS workflow_timer_pending ON xt_meta.workflow_timer_outbox(tenant_id,due)
 WHERE dispatched_at IS NULL;
ALTER TABLE xt_meta.workflow_instance ENABLE ROW LEVEL SECURITY;
ALTER TABLE xt_meta.workflow_instance FORCE ROW LEVEL SECURITY;
ALTER TABLE xt_meta.workflow_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE xt_meta.workflow_request FORCE ROW LEVEL SECURITY;
ALTER TABLE xt_meta.workflow_timer_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE xt_meta.workflow_timer_outbox FORCE ROW LEVEL SECURITY;
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['workflow_instance','workflow_request','workflow_timer_outbox'] LOOP
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='xt_meta' AND tablename=t
    AND policyname='tenant_isolation') THEN
   EXECUTE format('CREATE POLICY tenant_isolation ON xt_meta.%I USING (tenant_id=xt.tenant()) WITH CHECK (tenant_id=xt.tenant())',t);
  END IF;
  EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON xt_meta.%I TO xt_app',t);
 END LOOP;
END $$;
