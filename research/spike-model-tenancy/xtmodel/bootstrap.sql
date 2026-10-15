-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 xT contributors
-- Chạy MỘT LẦN bằng superuser. Tách 3 vai trò:
--   xt_migrator : chủ bảng nghiệp vụ, chạy DDL do model engine sinh (BYPASSRLS để đếm vi phạm xuyên tenant)
--   xt_app      : runtime MCP/CRUD — NOSUPERUSER NOBYPASSRLS, không sở hữu bảng nào
--   xt_audit_owner (NOLOGIN): chủ bảng audit + hàm ghi audit; migrator/app không sửa được audit
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid() có sẵn từ PG13; giữ cho rõ
CREATE ROLE xt_migrator LOGIN PASSWORD 'migrator' NOSUPERUSER BYPASSRLS NOCREATEROLE;
GRANT CREATE ON DATABASE xt TO xt_migrator;  -- tạo schema theo tenant (B2) và api_t<seq>
CREATE ROLE xt_app LOGIN PASSWORD 'app' NOSUPERUSER NOBYPASSRLS NOCREATEROLE;
CREATE ROLE xt_audit_owner NOLOGIN NOSUPERUSER NOBYPASSRLS;
-- G-M1: role ghi trường do workflow quản. xt_app chỉ được SET ROLE (không thừa hưởng quyền) trong
-- đường transition; CRUD tổng quát không bao giờ SET ROLE.
CREATE ROLE xt_workflow NOLOGIN NOSUPERUSER NOBYPASSRLS;
GRANT xt_workflow TO xt_app WITH INHERIT FALSE, SET TRUE;

CREATE SCHEMA xt AUTHORIZATION xt_migrator;        -- hàm ngữ cảnh
CREATE SCHEMA xt_meta AUTHORIZATION xt_migrator;   -- catalog phiên bản model
CREATE SCHEMA xt_data AUTHORIZATION xt_migrator;   -- bảng model dùng chung, có tenant_id + RLS
CREATE SCHEMA xt_audit AUTHORIZATION xt_audit_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA xt, xt_data, xt_audit TO xt_app, xt_workflow;
GRANT USAGE ON SCHEMA xt, public TO xt_audit_owner;  -- public: pgcrypto (hmac, gen_random_bytes)
GRANT USAGE ON SCHEMA xt_audit TO xt_migrator;
ALTER DEFAULT PRIVILEGES FOR ROLE xt_migrator IN SCHEMA xt_data
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO xt_app;

SET ROLE xt_migrator;
-- Ngữ cảnh do backend tin cậy đặt bằng set_config(..., true) trong từng transaction.
-- Không đặt => NULL => mọi policy trả false (fail-closed).
CREATE FUNCTION xt.tenant() RETURNS uuid LANGUAGE sql STABLE PARALLEL SAFE AS
$$ SELECT nullif(current_setting('xt.tenant_id', true), '')::uuid $$;
CREATE FUNCTION xt.actor() RETURNS uuid LANGUAGE sql STABLE PARALLEL SAFE AS
$$ SELECT nullif(current_setting('xt.actor_id', true), '')::uuid $$;
CREATE FUNCTION xt.has_role(r text) RETURNS boolean LANGUAGE sql STABLE PARALLEL SAFE AS
$$ SELECT coalesce(r = ANY (string_to_array(nullif(current_setting('xt.roles', true), ''), ',')), false) $$;
CREATE FUNCTION xt.in_teams(t uuid) RETURNS boolean LANGUAGE sql STABLE PARALLEL SAFE AS
$$ SELECT coalesce(t = ANY (string_to_array(nullif(current_setting('xt.team_ids', true), ''), ',')::uuid[]), false) $$;
CREATE FUNCTION xt.in_departments(d uuid) RETURNS boolean LANGUAGE sql STABLE PARALLEL SAFE AS
$$ SELECT coalesce(d = ANY (string_to_array(nullif(current_setting('xt.department_ids', true), ''), ',')::uuid[]), false) $$;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA xt TO xt_app, xt_audit_owner, xt_workflow;

-- Trigger chung cho mọi bảng model: revision/updated_at cho optimistic concurrency,
-- chặn đổi tenant_id/id/created_at.
CREATE FUNCTION xt.row_touch() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
      RAISE EXCEPTION 'không được đổi tenant_id/id/created_at' USING ERRCODE = 'check_violation',
        CONSTRAINT = 'xt_immutable_system_columns';
    END IF;
    NEW.revision := OLD.revision + 1;
    NEW.updated_at := clock_timestamp();
    NEW.updated_by := xt.actor();
  ELSE
    NEW.revision := 1;
    NEW.created_by := xt.actor();
  END IF;
  RETURN NEW;
END $$;

CREATE TABLE xt_meta.tenant (
  seq  int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id   uuid NOT NULL UNIQUE,
  name text NOT NULL
);
-- tenant_seq = 0: catalog dùng chung (biến thể A); >0: catalog riêng của tenant (biến thể B)
CREATE TABLE xt_meta.catalog_version (
  tenant_seq  int NOT NULL DEFAULT 0,
  version     int NOT NULL,
  spec        jsonb NOT NULL,
  spec_hash   text NOT NULL,
  plan        jsonb NOT NULL,
  plan_hash   text NOT NULL,
  proposed_by text NOT NULL,
  approved_by text NOT NULL,
  applied_at  timestamptz NOT NULL DEFAULT now(),
  post_status jsonb NOT NULL DEFAULT '{}',
  PRIMARY KEY (tenant_seq, version)
);
-- Pha 2 của apply: hàng đợi bền, enqueue cùng transaction pha 1, worker chạy + retry.
CREATE TABLE xt_meta.apply_job (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_seq int NOT NULL,
  version    int NOT NULL,
  ord        int NOT NULL,
  phase      text NOT NULL CHECK (phase IN ('post', 'concurrent')),
  op_id      text NOT NULL,
  sql        text NOT NULL,
  status     text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed', 'dead')),
  attempts   int NOT NULL DEFAULT 0,
  last_error text,
  UNIQUE (tenant_seq, version, ord)
);
RESET ROLE;

-- ---------------------------------------------------------------- audit ----
SET ROLE xt_audit_owner;
CREATE TABLE xt_audit.event (
  seq       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id uuid NOT NULL,
  actor_id  uuid,
  at        timestamptz NOT NULL,
  model     text NOT NULL,
  record_id uuid NOT NULL,
  op        text NOT NULL,
  diff      jsonb NOT NULL,
  prev_hash bytea NOT NULL,
  hash      bytea NOT NULL
);
CREATE INDEX event_tenant_seq ON xt_audit.event (tenant_id, seq);
-- G-M2: khóa HMAC cho giá trị sensitive trong audit (hash thuần của lương/CCCD dò ngược được).
CREATE TABLE xt_audit.pepper (key bytea NOT NULL);
INSERT INTO xt_audit.pepper VALUES (gen_random_bytes(32));
REVOKE ALL ON xt_audit.pepper FROM PUBLIC;

-- Chuỗi băm theo tenant: hash = sha256(prev_hash || canonical(row)).
CREATE FUNCTION xt_audit.canon(t uuid, a uuid, at timestamptz, m text, r uuid, o text, d jsonb)
RETURNS bytea LANGUAGE sql IMMUTABLE AS $$
  SELECT convert_to(concat_ws('|', t::text, coalesce(a::text, '-'),
         to_char(at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'), m, r::text, o, d::text), 'UTF8')
$$;

CREATE FUNCTION xt_audit.log() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, xt_audit, public AS $$
DECLARE
  t uuid; rid uuid; d jsonb; prev bytea; ts timestamptz := clock_timestamp(); a uuid := xt.actor();
  pk bytea; c text; part text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    t := OLD.tenant_id; rid := OLD.id; d := jsonb_build_object('old', to_jsonb(OLD));
  ELSIF TG_OP = 'INSERT' THEN
    t := NEW.tenant_id; rid := NEW.id; d := jsonb_build_object('new', to_jsonb(NEW));
  ELSE
    t := NEW.tenant_id; rid := NEW.id;
    SELECT jsonb_build_object('old', coalesce(jsonb_object_agg(k, o.value), '{}'),
                              'new', coalesce(jsonb_object_agg(k, n.value), '{}'))
      INTO d
      FROM jsonb_each(to_jsonb(NEW)) n(k, value)
      JOIN jsonb_each(to_jsonb(OLD)) o(k2, value) ON o.k2 = n.k
     WHERE n.value IS DISTINCT FROM o.value AND n.k NOT IN ('revision', 'updated_at', 'updated_by');
  END IF;
  IF TG_NARGS > 0 THEN   -- cột sensitive: chỉ lưu HMAC
    SELECT p.key INTO pk FROM xt_audit.pepper p LIMIT 1;
    FOREACH c IN ARRAY TG_ARGV LOOP
      FOREACH part IN ARRAY ARRAY['old', 'new'] LOOP
        IF d -> part ? c AND jsonb_typeof(d -> part -> c) <> 'null' THEN
          d := jsonb_set(d, ARRAY[part, c], to_jsonb('hmac:' || left(encode(hmac(convert_to((d -> part ->> c), 'UTF8'), pk, 'sha256'), 'hex'), 32)));
        END IF;
      END LOOP;
    END LOOP;
  END IF;
  -- tuần tự hóa theo tenant để chuỗi tuyến tính
  PERFORM pg_advisory_xact_lock(hashtextextended('xt_audit:' || t::text, 0));
  SELECT e.hash INTO prev FROM xt_audit.event e WHERE e.tenant_id = t ORDER BY e.seq DESC LIMIT 1;
  prev := coalesce(prev, '\x0000000000000000000000000000000000000000000000000000000000000000'::bytea);
  INSERT INTO xt_audit.event (tenant_id, actor_id, at, model, record_id, op, diff, prev_hash, hash)
  VALUES (t, a, ts, TG_TABLE_NAME, rid, TG_OP, d, prev,
          sha256(prev || xt_audit.canon(t, a, ts, TG_TABLE_NAME, rid, TG_OP, d)));
  RETURN NULL;
END $$;

CREATE FUNCTION xt_audit.forbid() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'xt_audit.event là append-only (% bị cấm)', TG_OP; END $$;
CREATE TRIGGER no_update_delete BEFORE UPDATE OR DELETE ON xt_audit.event
  FOR EACH ROW EXECUTE FUNCTION xt_audit.forbid();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON xt_audit.event
  FOR EACH STATEMENT EXECUTE FUNCTION xt_audit.forbid();

-- Kiểm chứng chuỗi bằng một truy vấn: trả seq đầu tiên bị hỏng (NULL = toàn vẹn).
CREATE FUNCTION xt_audit.verify(t uuid) RETURNS TABLE(n bigint, first_bad_seq bigint)
LANGUAGE sql STABLE AS $$
  WITH c AS (
    SELECT seq, hash, prev_hash,
           lag(hash, 1, '\x0000000000000000000000000000000000000000000000000000000000000000'::bytea)
             OVER (ORDER BY seq) AS expect_prev,
           sha256(prev_hash || xt_audit.canon(tenant_id, actor_id, at, model, record_id, op, diff)) AS expect_hash
      FROM xt_audit.event WHERE tenant_id = t)
  SELECT count(*), min(seq) FILTER (WHERE prev_hash <> expect_prev OR hash <> expect_hash) FROM c
$$;

ALTER TABLE xt_audit.event ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_read ON xt_audit.event FOR SELECT TO xt_app USING (tenant_id = xt.tenant());
REVOKE ALL ON xt_audit.event FROM PUBLIC;
GRANT SELECT ON xt_audit.event TO xt_app;
GRANT EXECUTE ON FUNCTION xt_audit.verify(uuid), xt_audit.canon(uuid,uuid,timestamptz,text,uuid,text,jsonb) TO xt_app;
REVOKE ALL ON FUNCTION xt_audit.log() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION xt_audit.log() TO xt_migrator, xt_app;  -- chỉ có nghĩa khi gọi qua trigger
RESET ROLE;
