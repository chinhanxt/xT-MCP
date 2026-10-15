-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Chạy bằng xt_migrator (xt db migrate). Kỳ đã kích hoạt của quy trình định kỳ (trigger timer),
-- `TimerPoller` của xt-worker (job `workflow.timer_starts`). Khóa chính (tenant, process, kỳ) +
-- INSERT ... ON CONFLICT DO NOTHING: nhiều worker quét cùng kỳ thì đúng một instance.
-- Idempotent: chạy lại trên CSDL đã có bảng không lỗi.
CREATE TABLE IF NOT EXISTS xt_meta.workflow_timer_fire (
 tenant_id uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
 process_id text NOT NULL CHECK (char_length(process_id) BETWEEN 1 AND 63),
 period_key text NOT NULL CHECK (char_length(period_key) BETWEEN 1 AND 64),
 status text NOT NULL CHECK (status IN ('fired', 'skipped')),
 version integer NOT NULL CHECK (version > 0),
 instance_id text,
 fired_at timestamptz NOT NULL,
 PRIMARY KEY (tenant_id, process_id, period_key),
 FOREIGN KEY (tenant_id, instance_id) REFERENCES xt_meta.workflow_instance(tenant_id, id)
);
ALTER TABLE xt_meta.workflow_timer_fire ENABLE ROW LEVEL SECURITY;
ALTER TABLE xt_meta.workflow_timer_fire FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='xt_meta'
   AND tablename='workflow_timer_fire' AND policyname='tenant_isolation') THEN
  CREATE POLICY tenant_isolation ON xt_meta.workflow_timer_fire
   USING (tenant_id=xt.tenant()) WITH CHECK (tenant_id=xt.tenant());
 END IF;
END $$;
GRANT SELECT, INSERT, UPDATE ON xt_meta.workflow_timer_fire TO xt_app;
