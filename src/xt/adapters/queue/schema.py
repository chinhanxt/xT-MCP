# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Explicit bootstrap for queue metadata; never run during worker startup."""

from typing import LiteralString

SCHEMA: LiteralString = """
CREATE SCHEMA xt_queue;
REVOKE ALL ON SCHEMA xt_queue FROM PUBLIC;
CREATE TABLE xt_queue.requests (
    tenant_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    fingerprint text NOT NULL,
    envelope_hash text NOT NULL,
    job_id bigint,
    schedule_id bigint,
    completed boolean NOT NULL DEFAULT false,
    cancelled boolean NOT NULL DEFAULT false,
    dead_at timestamptz,
    last_error text,
    PRIMARY KEY (tenant_id, idempotency_key)
);
CREATE INDEX requests_job_id ON xt_queue.requests (job_id);
CREATE TABLE xt_queue.schedules (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    key text NOT NULL,
    fingerprint text NOT NULL,
    task_name text NOT NULL,
    payload jsonb NOT NULL,
    cron_utc text NOT NULL,
    timezone text NOT NULL,
    local_cron text NOT NULL,
    next_at timestamptz NOT NULL,
    active boolean NOT NULL DEFAULT true,
    last_error text,
    last_error_at timestamptz,
    -- consecutive materialization failures and the backoff deadline (capped)
    failures integer NOT NULL DEFAULT 0 CHECK (failures >= 0),
    retry_at timestamptz,
    UNIQUE (tenant_id, key),
    UNIQUE (tenant_id, id)
);
ALTER TABLE xt_queue.requests ADD FOREIGN KEY (tenant_id, schedule_id)
    REFERENCES xt_queue.schedules(tenant_id, id);
ALTER TABLE xt_queue.requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE xt_queue.requests FORCE ROW LEVEL SECURITY;
CREATE POLICY requests_tenant ON xt_queue.requests TO xt_app
    USING (tenant_id = nullif(current_setting('xt.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('xt.tenant_id', true), '')::uuid);
ALTER TABLE xt_queue.schedules ENABLE ROW LEVEL SECURITY;
ALTER TABLE xt_queue.schedules FORCE ROW LEVEL SECURITY;
CREATE POLICY schedules_tenant ON xt_queue.schedules TO xt_app
    USING (tenant_id = nullif(current_setting('xt.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = nullif(current_setting('xt.tenant_id', true), '')::uuid);
CREATE POLICY schedules_control ON xt_queue.schedules FOR SELECT TO xt_control USING (true);
-- Control marks dead letters (AQ-08) and lists them; it cannot change outcomes.
CREATE POLICY requests_control_read ON xt_queue.requests FOR SELECT TO xt_control USING (true);
CREATE POLICY requests_control_dead ON xt_queue.requests FOR UPDATE TO xt_control
    USING (true) WITH CHECK (true);
GRANT USAGE ON SCHEMA xt_queue TO xt_app, xt_control;
GRANT USAGE ON SCHEMA public TO xt_app, xt_control;
GRANT SELECT, INSERT, UPDATE ON xt_queue.requests, xt_queue.schedules TO xt_app;
GRANT SELECT ON xt_queue.schedules, xt_queue.requests TO xt_control;
GRANT UPDATE (dead_at, last_error) ON xt_queue.requests TO xt_control;

-- AQ-05: trusted tenant -> (schema, job role) catalog, written only by the
-- installer/migrator during tenant provisioning. Handlers run as the bound
-- NOLOGIN role, which may use its own tenant schema and no other.
CREATE TABLE xt_queue.tenant_bindings (
    tenant_id uuid PRIMARY KEY,
    schema_name name NOT NULL UNIQUE,
    job_role name NOT NULL UNIQUE
);
REVOKE ALL ON xt_queue.tenant_bindings FROM PUBLIC;
CREATE FUNCTION xt_queue.job_binding(tid uuid, OUT tenant_schema name, OUT tenant_role name)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r record;
BEGIN
    IF tid IS DISTINCT FROM nullif(current_setting('xt.tenant_id', true), '')::uuid THEN
        RAISE EXCEPTION 'job binding requires the matching tenant context'
            USING ERRCODE = '42501';
    END IF;
    SELECT b.schema_name, b.job_role INTO tenant_schema, tenant_role
        FROM xt_queue.tenant_bindings b WHERE b.tenant_id = tid;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'tenant has no queue job binding' USING ERRCODE = '42501';
    END IF;
    SELECT rolsuper, rolbypassrls, rolcanlogin INTO r FROM pg_roles WHERE rolname = tenant_role;
    IF NOT FOUND OR r.rolsuper OR r.rolbypassrls OR r.rolcanlogin THEN
        RAISE EXCEPTION 'tenant job role must be NOLOGIN NOSUPERUSER NOBYPASSRLS'
            USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname = tenant_schema
                   AND has_schema_privilege(tenant_role, n.oid, 'USAGE')) THEN
        RAISE EXCEPTION 'tenant job role cannot use its tenant schema' USING ERRCODE = '42501';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname <> tenant_schema
               AND (n.nspname ~ '^xt_t[0-9]+$' OR n.nspname IN (
                   SELECT o.schema_name FROM xt_queue.tenant_bindings o))
               AND has_schema_privilege(tenant_role, n.oid, 'USAGE')) THEN
        RAISE EXCEPTION 'tenant job role can use another tenant schema' USING ERRCODE = '42501';
    END IF;
END $$;
REVOKE ALL ON FUNCTION xt_queue.job_binding(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION xt_queue.job_binding(uuid) TO xt_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA xt_queue TO xt_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON procrastinate_jobs, procrastinate_events,
    procrastinate_workers, procrastinate_periodic_defers TO xt_control;
GRANT USAGE ON SEQUENCE procrastinate_jobs_id_seq, procrastinate_events_id_seq,
    procrastinate_workers_id_seq, procrastinate_periodic_defers_id_seq TO xt_control;
-- Defer/cancel functions are SECURITY INVOKER: API transactions need their tables.
-- RLS also protects the upstream queue against accidental cross-tenant SQL.
-- AQ-09: xt_app may only defer xt.dispatch at default priority (column-limited
-- INSERT) and move its own pending job to cancelled (UPDATE of status only).
GRANT SELECT ON procrastinate_jobs, procrastinate_events TO xt_app;
GRANT INSERT (queue_name, task_name, priority, lock, queueing_lock, args, scheduled_at)
    ON procrastinate_jobs TO xt_app;
GRANT UPDATE (status) ON procrastinate_jobs TO xt_app;
GRANT INSERT ON procrastinate_events TO xt_app;
GRANT USAGE ON SEQUENCE procrastinate_jobs_id_seq, procrastinate_events_id_seq TO xt_app;
ALTER TABLE procrastinate_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE procrastinate_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY xt_queue_jobs_control ON procrastinate_jobs TO xt_control USING (true);
CREATE POLICY xt_queue_jobs_tenant_read ON procrastinate_jobs FOR SELECT TO xt_app
    USING (args->>'tenant_id' = nullif(current_setting('xt.tenant_id', true), ''));
CREATE POLICY xt_queue_jobs_tenant_defer ON procrastinate_jobs FOR INSERT TO xt_app
    WITH CHECK (args->>'tenant_id' = nullif(current_setting('xt.tenant_id', true), '')
                AND task_name = 'xt.dispatch' AND queue_name = 'xt' AND priority = 0
                AND lock IS NULL AND queueing_lock IS NULL);
CREATE POLICY xt_queue_jobs_tenant_cancel ON procrastinate_jobs FOR UPDATE TO xt_app
    USING (args->>'tenant_id' = nullif(current_setting('xt.tenant_id', true), '')
           AND status = 'todo')
    WITH CHECK (args->>'tenant_id' = nullif(current_setting('xt.tenant_id', true), '')
                AND status = 'cancelled');
ALTER TABLE procrastinate_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE procrastinate_events FORCE ROW LEVEL SECURITY;
CREATE POLICY xt_queue_events_control ON procrastinate_events TO xt_control USING (true);
CREATE POLICY xt_queue_events_tenant ON procrastinate_events TO xt_app
    USING (EXISTS (SELECT 1 FROM procrastinate_jobs j WHERE j.id=job_id))
    WITH CHECK (EXISTS (SELECT 1 FROM procrastinate_jobs j WHERE j.id=job_id));

-- The dispatcher holds its delivery's row lock until commit. xt_app has no
-- general UPDATE on procrastinate_jobs (AQ-09), so the lock is taken here.
CREATE FUNCTION xt_queue.lock_delivery(jid bigint, wid bigint, generation integer)
RETURNS boolean LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.procrastinate_jobs j
        WHERE j.id = jid AND j.status = 'doing' AND j.worker_id = wid
          AND j.attempts = generation
          AND j.args->>'tenant_id' = nullif(current_setting('xt.tenant_id', true), '')
        FOR UPDATE)
$$;
REVOKE ALL ON FUNCTION xt_queue.lock_delivery(bigint,bigint,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION xt_queue.lock_delivery(bigint,bigint,integer) TO xt_app;
-- Dead letters stay visible on the durable request (AQ-08).
CREATE FUNCTION xt_queue.mark_dead(jid bigint, reason text) RETURNS void LANGUAGE sql
SET search_path = pg_catalog AS $$
    UPDATE xt_queue.requests SET dead_at = now(), last_error = left(reason, 200)
    WHERE job_id = jid AND dead_at IS NULL
$$;
-- Fencing must cover queue acknowledgements too: the upstream by-id methods
-- can acknowledge/retry a later delivery or even resurrect a failed job.
CREATE FUNCTION xt_queue.finish_owned(jid bigint, wid bigint, generation integer,
                                     outcome text, reason text) RETURNS boolean
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE j public.procrastinate_jobs;
BEGIN
    SELECT * INTO j FROM public.procrastinate_jobs WHERE id=jid FOR UPDATE;
    IF NOT FOUND OR j.status <> 'doing' OR j.worker_id IS DISTINCT FROM wid
       OR j.attempts <> generation THEN RETURN false; END IF;
    PERFORM public.procrastinate_finish_job_v1(
        jid, outcome::public.procrastinate_job_status, false);
    IF outcome = 'failed' THEN
        PERFORM xt_queue.mark_dead(jid, coalesce(reason, 'failed'));
    END IF;
    RETURN true;
END $$;
CREATE FUNCTION xt_queue.retry_owned(jid bigint, wid bigint, generation integer,
    max_attempts integer, retry_at timestamptz, priority integer,
    queue_name text, new_lock text, reason text) RETURNS boolean LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $$
DECLARE j public.procrastinate_jobs;
BEGIN
    SELECT * INTO j FROM public.procrastinate_jobs WHERE id=jid FOR UPDATE;
    IF NOT FOUND OR j.status <> 'doing' OR j.worker_id IS DISTINCT FROM wid
       OR j.attempts <> generation THEN RETURN false; END IF;
    IF j.attempts + 1 >= max_attempts THEN
        PERFORM public.procrastinate_finish_job_v1(jid, 'failed', false);
        PERFORM xt_queue.mark_dead(jid, coalesce(reason, 'attempts exhausted'));
    ELSE
        PERFORM public.procrastinate_retry_job_v2(jid, retry_at, priority,
                                                queue_name::varchar, new_lock::varchar);
    END IF;
    RETURN true;
END $$;
-- DLQ operations for xt_control (``xt worker dlq requeue|purge``). SECURITY
-- DEFINER keeps xt_control's column grants narrow; the owner bypasses RLS (L8).
-- Requeue defers a NEW job for the same durable request and repoints the
-- request to it: deliveries of the old job id fail the dispatcher's job_id
-- check, so fencing holds, and the attempt budget starts afresh.
CREATE FUNCTION xt_queue.requeue_dead(jid bigint) RETURNS bigint LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE j public.procrastinate_jobs; r xt_queue.requests; nid bigint;
BEGIN
    SELECT * INTO j FROM public.procrastinate_jobs
        WHERE id = jid AND queue_name = 'xt' AND task_name = 'xt.dispatch' FOR UPDATE;
    IF NOT FOUND OR j.status <> 'failed' THEN RETURN NULL; END IF;
    SELECT * INTO r FROM xt_queue.requests WHERE job_id = jid FOR UPDATE;
    IF NOT FOUND OR r.completed OR r.cancelled THEN RETURN NULL; END IF;
    INSERT INTO public.procrastinate_jobs (queue_name, task_name, priority, args)
        VALUES (j.queue_name, j.task_name, j.priority, j.args) RETURNING id INTO nid;
    UPDATE xt_queue.requests SET job_id = nid, dead_at = NULL, last_error = NULL
        WHERE tenant_id = r.tenant_id AND idempotency_key = r.idempotency_key;
    RETURN nid;
END $$;
-- Purge abandons a dead letter: the request becomes cancelled (a replay of
-- the key stays a no-op) and the failed job row and its events are deleted.
CREATE FUNCTION xt_queue.purge_dead(jid bigint) RETURNS boolean LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM 1 FROM public.procrastinate_jobs
        WHERE id = jid AND queue_name = 'xt' AND task_name = 'xt.dispatch'
          AND status = 'failed' FOR UPDATE;
    IF NOT FOUND THEN RETURN false; END IF;
    UPDATE xt_queue.requests SET cancelled = true WHERE job_id = jid AND NOT completed;
    DELETE FROM public.procrastinate_jobs WHERE id = jid;
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION xt_queue.requeue_dead(bigint), xt_queue.purge_dead(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION xt_queue.requeue_dead(bigint), xt_queue.purge_dead(bigint)
    TO xt_control;
REVOKE ALL ON FUNCTION xt_queue.mark_dead(bigint,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION xt_queue.finish_owned(bigint,bigint,integer,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION xt_queue.retry_owned(bigint,bigint,integer,integer,timestamptz,
                                          integer,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION xt_queue.mark_dead(bigint,text) TO xt_control;
GRANT EXECUTE ON FUNCTION xt_queue.finish_owned(bigint,bigint,integer,text,text) TO xt_control;
GRANT EXECUTE ON FUNCTION xt_queue.retry_owned(bigint,bigint,integer,integer,timestamptz,
                                             integer,text,text,text) TO xt_control;
"""
