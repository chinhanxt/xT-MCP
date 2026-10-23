-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Chạy bằng xt_migrator (xt db migrate). D-AGENTHR-1 (gói nhan-su-ai, cổng AgentHR của dispatcher):
--  * agent_usage: số việc ghi mỗi ngày (giờ tenant) của (client agent, người được đại diện); không
--    vào hash-chain (mỗi lượt một lần tăng = nhiễu); RLS theo tenant như mọi bảng xt_meta.
--  * agenthr_event: mỗi lần cổng chặn một lượt agent (op 'agenthr.block' + lý do) — bảng
--    append-only nằm trong hash-chain audit của tenant (xt_audit.install_meta2).
--  * agent_grant: xt_app được ghi (upsert khi IT duyệt JD, xóa khi agent nghỉ) — chỉ ở ngữ cảnh
--    vai trò dịch vụ xt_workflow (policy RESTRICTIVE); bảng đã ở hash-chain từ 0016.
-- Idempotent.

CREATE TABLE IF NOT EXISTS xt_meta.agent_usage (
  tenant_id uuid NOT NULL DEFAULT xt.tenant() REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  client_id text NOT NULL CHECK (char_length(client_id) BETWEEN 1 AND 200),
  person    uuid NOT NULL,
  day       date NOT NULL,
  count     int NOT NULL DEFAULT 0 CHECK (count >= 0),
  PRIMARY KEY (tenant_id, client_id, person, day),
  FOREIGN KEY (tenant_id, person) REFERENCES xt_meta.person (tenant_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS xt_meta.agenthr_event (
  id        uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  tenant_id uuid NOT NULL DEFAULT xt.tenant() REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  op        text NOT NULL CHECK (op IN ('agenthr.block')),
  at        timestamptz NOT NULL DEFAULT clock_timestamp(),
  client_id text NOT NULL CHECK (char_length(client_id) BETWEEN 1 AND 200),
  person    uuid NOT NULL,
  ho_so     uuid,
  tool      text NOT NULL CHECK (char_length(tool) BETWEEN 1 AND 64),
  reason    text NOT NULL CHECK (reason ~ '^[a-z][a-z0-9_]{0,62}$'),
  detail    jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (octet_length(detail::text) <= 4096),
  FOREIGN KEY (tenant_id, person) REFERENCES xt_meta.person (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS agenthr_event_by_profile ON xt_meta.agenthr_event (tenant_id, ho_so, at);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['agent_usage', 'agenthr_event'] LOOP
    EXECUTE format('ALTER TABLE xt_meta.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE xt_meta.%I FORCE ROW LEVEL SECURITY', t);
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'xt_meta' AND tablename = t
                   AND policyname = 'tenant_isolation') THEN
      EXECUTE format('CREATE POLICY tenant_isolation ON xt_meta.%I TO xt_app USING '
                     '(tenant_id = xt.tenant()) WITH CHECK (tenant_id = xt.tenant())', t);
    END IF;
  END LOOP;
END $$;
GRANT SELECT, INSERT ON xt_meta.agent_usage, xt_meta.agenthr_event TO xt_app;
GRANT UPDATE (count) ON xt_meta.agent_usage TO xt_app;
-- agenthr_event: append-only (không UPDATE/DELETE cho xt_app).

-- agent_grant: ghi chỉ trong ngữ cảnh xt_workflow (op áp JD / hook cho nghỉ của lõi).
GRANT INSERT, DELETE ON xt_meta.agent_grant TO xt_app;
GRANT UPDATE (autonomy, roles) ON xt_meta.agent_grant TO xt_app;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'xt_meta'
                 AND tablename = 'agent_grant' AND policyname = 'agent_grant_insert_workflow') THEN
    CREATE POLICY agent_grant_insert_workflow ON xt_meta.agent_grant AS RESTRICTIVE FOR INSERT
      TO xt_app WITH CHECK (xt.has_role('xt_workflow'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'xt_meta'
                 AND tablename = 'agent_grant' AND policyname = 'agent_grant_update_workflow') THEN
    CREATE POLICY agent_grant_update_workflow ON xt_meta.agent_grant AS RESTRICTIVE FOR UPDATE
      TO xt_app USING (xt.has_role('xt_workflow')) WITH CHECK (xt.has_role('xt_workflow'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'xt_meta'
                 AND tablename = 'agent_grant' AND policyname = 'agent_grant_delete_workflow') THEN
    CREATE POLICY agent_grant_delete_workflow ON xt_meta.agent_grant AS RESTRICTIVE FOR DELETE
      TO xt_app USING (xt.has_role('xt_workflow'));
  END IF;
END $$;

DO $$ BEGIN
  IF to_regprocedure('xt_audit.install_meta2(name, text[], text[], text)') IS NULL THEN
    RAISE EXCEPTION 'thiếu xt_audit.install_meta2: chạy xt.adapters.db.tenancy.install_meta_audit()'
      ' bằng superuser rồi migrate lại';
  END IF;
  GRANT TRIGGER ON xt_meta.agenthr_event TO xt_audit_owner;
  PERFORM xt_audit.install_meta2('agenthr_event', ARRAY['id'], '{}', 'tenant_id');
END $$;
