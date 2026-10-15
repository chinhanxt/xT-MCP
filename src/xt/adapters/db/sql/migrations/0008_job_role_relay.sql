-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Chạy bằng xt_migrator (xt db migrate). Job `workflow.relay_timers` chạy với job role của tenant
-- (`xt_job_t<seq>`, thành viên nhóm `xt_job`, docs/worker-queue.md) trên connection của handler:
-- đọc + đánh dấu `dispatched_at` của hai outbox `xt_meta.workflow_timer_outbox` và
-- `xt_meta.model_change_request`. Cấp tối thiểu cho NHÓM `xt_job` (tenant mới tự có qua
-- `xt tenant bind-queue`, không DDL theo tenant): USAGE schema xt/xt_meta, EXECUTE hàm ngữ cảnh,
-- SELECT cột cần đọc + UPDATE đúng cột `dispatched_at`; không INSERT/DELETE, không bảng xt_meta
-- nào khác. Cô lập tenant: policy theo `xt.tenant()` (ngữ cảnh do dispatcher đặt, job role không
-- EXECUTE được xt.set_context) + policy RESTRICTIVE ràng job role đúng tenant của ngữ cảnh
-- (`xt_job_t<seq>` = tenant hiện tại), nên role tenant A với ngữ cảnh B cũng thấy 0 dòng.
-- decisions.md "Quyền của job role trên outbox xt_meta".
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'xt_job') THEN
    RAISE EXCEPTION 'thiếu nhóm role xt_job: chạy bootstrap/xt-init (superuser) rồi migrate lại';
  END IF;
END $$;

GRANT USAGE ON SCHEMA xt, xt_meta TO xt_job;
GRANT EXECUTE ON FUNCTION xt.cur(), xt.tenant(), xt.tenant_seq() TO xt_job;

GRANT SELECT (tenant_id, instance_id, process_id, due, dispatched_at)
  ON xt_meta.workflow_timer_outbox TO xt_job;
GRANT UPDATE (dispatched_at) ON xt_meta.workflow_timer_outbox TO xt_job;
GRANT SELECT (tenant_id, proposal_id, state, decided_at, dispatched_at)
  ON xt_meta.model_change_request TO xt_job;
GRANT UPDATE (dispatched_at) ON xt_meta.model_change_request TO xt_job;

-- workflow_timer_outbox: policy tenant_isolation (0002) là TO PUBLIC, đã gồm xt_job.
ALTER POLICY tenant_isolation ON xt_meta.model_change_request TO xt_app, xt_job;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['workflow_timer_outbox', 'model_change_request'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'xt_meta' AND tablename = t
                   AND policyname = 'job_role_tenant') THEN
      EXECUTE format('CREATE POLICY job_role_tenant ON xt_meta.%I AS RESTRICTIVE TO xt_job'
                     ' USING (current_user = format(''xt_job_t%%s'', xt.tenant_seq()))'
                     ' WITH CHECK (current_user = format(''xt_job_t%%s'', xt.tenant_seq()))', t);
    END IF;
  END LOOP;
END $$;
