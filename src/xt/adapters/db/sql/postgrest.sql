-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
--
-- Tích hợp PostgREST /data (architecture-v2 §3.3(c), D6, D9; SMT §5; docs/decisions.md PG-1…PG-5).
-- Chạy bằng superuser. bootstrap() nối file này sau bootstrap.sql trong cùng transaction; file
-- idempotent nên tenancy.install_postgrest_role() chạy lại được trên CSDL đã bootstrap.
--
-- Cấu hình PostgREST tương ứng:
--   db-uri user xt_authenticator     jwt-aud = "xt-data"     role cố định xt_web (không từ token)
--   db-config = true    db-pre-config = "xt.pgrst_config"    db-pre-request = "xt.pgrst_pre"
--   db-schemas = "xt_api_none" (giá trị khởi đầu; pre-config ghi đè)    db-extra-search-path = ""
--   openapi-mode = "disabled"
-- Tạo/xóa tenant: NOTIFY pgrst, 'reload config' + 'reload schema' (xt.adapters.db.postgrest).
--
-- Role:
--   xt_authenticator  LOGIN NOINHERIT; chỉ là thành viên xt_web (SET, không INHERIT); mật khẩu từ
--                     secret ngoài repo. Không bao giờ là thành viên xt_app/xt_workflow/xt_api_owner.
--   xt_web            role JWT; không gọi được xt.set_context; chỉ chạy xt.pgrst_pre.
--
-- Ngữ cảnh RBAC: PostgREST chạy GET/HEAD trong transaction READ ONLY nên không ghi được
-- xt_meta.session_ctx (xt.set_context). Pre-request kiểm token + bảng tổ chức rồi đặt GUC cục bộ
-- transaction `xt.pgrst_ctx`; xt.cur() chỉ đọc GUC này khi session_user = xt_authenticator (mọi
-- session khác giữ nguyên hai nhánh đã audit). Không có pre-request => GUC rỗng => fail-closed.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'xt_authenticator') THEN
    CREATE ROLE xt_authenticator LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOCREATEDB;
  END IF;
END $$;
ALTER ROLE xt_authenticator LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION
  NOBYPASSRLS CONNECTION LIMIT 20;
ALTER ROLE xt_web NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS;
-- Cài lại cũng phải khôi phục PG-2 khi role có sẵn bị cấp thừa quyền. PostgreSQL 17 cho phép
-- nhiều grantor cho cùng membership: thu từng grant, kể cả ADMIN OPTION, trước khi cấp lại.
DO $$
DECLARE membership record;
BEGIN
  FOR membership IN
    SELECT parent.rolname AS parent_name, child.rolname AS child_name,
           grantor.rolname AS grantor_name
      FROM pg_auth_members m
      JOIN pg_roles parent ON parent.oid = m.roleid
      JOIN pg_roles child ON child.oid = m.member
      JOIN pg_roles grantor ON grantor.oid = m.grantor
     WHERE child.rolname IN ('xt_authenticator', 'xt_web')
  LOOP
    EXECUTE format('REVOKE %I FROM %I GRANTED BY %I CASCADE',
                   membership.parent_name, membership.child_name, membership.grantor_name);
  END LOOP;
END $$;
GRANT xt_web TO xt_authenticator WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;
DO $$ BEGIN EXECUTE format('GRANT CONNECT ON DATABASE %I TO xt_authenticator', current_database()); END $$;
GRANT USAGE ON SCHEMA xt TO xt_authenticator;

-- Schema rỗng luôn đứng đầu db-schemas: request thiếu Accept-Profile rơi vào đây (không phải
-- api_t<seq> của tenant nào) và bị pre-request từ chối.
CREATE SCHEMA IF NOT EXISTS xt_api_none AUTHORIZATION xt_api_owner;
REVOKE ALL ON SCHEMA xt_api_none FROM PUBLIC;
GRANT USAGE ON SCHEMA xt_api_none TO xt_web;

SET ROLE xt_migrator;

-- Client OIDC (azp) được đọc /data. Rỗng => mọi token bị từ chối. Ghi bằng install_postgrest_role.
CREATE TABLE IF NOT EXISTS xt_meta.pgrst_client (
  client_id text PRIMARY KEY CHECK (client_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$')
);
REVOKE ALL ON xt_meta.pgrst_client FROM PUBLIC;

-- Điểm nối API READ ONLY: giữ nguyên thân hàm đã audit trong backend_cur. Wrapper cur giữ OID
-- và ACL hiện có (kể cả login BI), không đổi logic ngữ cảnh của backend/BI.
DO $$
DECLARE source text;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('xt:postgrest:install', 0));
  SELECT prosrc INTO source FROM pg_proc WHERE oid = 'xt.cur()'::regprocedure;
  IF position('xt.backend_cur()' IN source) > 0 THEN
    IF to_regprocedure('xt.backend_cur()') IS NULL THEN
      RAISE EXCEPTION 'thiếu hàm ngữ cảnh backend; khôi phục bản đã audit trước khi cài PostgREST'
        USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
  ELSE
    -- Cài lần đầu hoặc core vừa nâng cấp cur: clone thân core hiện tại, không quay về bản cũ.
    EXECUTE regexp_replace(pg_get_functiondef('xt.cur()'::regprocedure),
      '^CREATE OR REPLACE FUNCTION xt\.cur\(', 'CREATE OR REPLACE FUNCTION xt.backend_cur(');
  END IF;
END $$;
REVOKE ALL ON FUNCTION xt.backend_cur() FROM PUBLIC;

CREATE OR REPLACE FUNCTION xt.cur(OUT tenant_id uuid, OUT actor_id uuid, OUT roles text[],
                                  OUT team_ids uuid[], OUT department_ids uuid[])
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE g jsonb;
BEGIN
  IF session_user <> 'xt_authenticator' THEN
    SELECT b.tenant_id, b.actor_id, b.roles, b.team_ids, b.department_ids
      INTO tenant_id, actor_id, roles, team_ids, department_ids FROM xt.backend_cur() b;
    RETURN;
  END IF;
  BEGIN
    g := nullif(current_setting('xt.pgrst_ctx', true), '')::jsonb;
    IF g IS NULL THEN RETURN; END IF;
    SELECT (g ->> 't')::uuid, (g ->> 'a')::uuid,
         ARRAY(SELECT jsonb_array_elements_text(g -> 'r')),
         ARRAY(SELECT jsonb_array_elements_text(g -> 'tm'))::uuid[],
         ARRAY(SELECT jsonb_array_elements_text(g -> 'd'))::uuid[]
    INTO tenant_id, actor_id, roles, team_ids, department_ids;
    RETURN;
  EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value THEN
    RETURN;
  END;
END
$$;

-- Overload riêng cho pre-request đã kiểm JWT + quyền xT. Hàm 8 tham số đã audit giữ nguyên;
-- xt_web/xt_authenticator KHÔNG được gọi trực tiếp overload này (chỉ gate do migrator sở hữu).
CREATE OR REPLACE FUNCTION xt.set_context(ctx jsonb) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF session_user <> 'xt_authenticator' THEN
    RAISE EXCEPTION 'ngữ cảnh chỉ dành cho PostgREST' USING ERRCODE = '42501';
  END IF;
  PERFORM set_config('xt.pgrst_ctx', ctx::text, true);
END
$$;
REVOKE ALL ON FUNCTION xt.set_context(jsonb) FROM PUBLIC;

-- db-pre-config (chạy bằng xt_authenticator, trước khi đổi role): db-schemas = xt_api_none + đúng
-- các schema api_t<seq> có thật (chủ xt_api_owner) của tenant đã đăng ký.
CREATE OR REPLACE FUNCTION xt.pgrst_config() RETURNS void
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE s text;
BEGIN
  SELECT string_agg(n.nspname, ',' ORDER BY t.seq) INTO s
    FROM xt_meta.tenant t
    JOIN pg_namespace n ON n.nspname = 'api_t' || t.seq::text
    JOIN pg_roles r ON r.oid = n.nspowner AND r.rolname = 'xt_api_owner';
  PERFORM set_config('pgrst.db_schemas', concat_ws(',', 'xt_api_none', s), true);
END $$;

-- Lõi pre-request (SECURITY DEFINER, chủ xt_migrator). `profile`/`who` lấy từ search_path/role
-- do PostgREST đặt, trước khi gate ghim search_path. SECURITY DEFINER không kế thừa USAGE của
-- xt_web nên không dùng current_schema() (nó có thể trả NULL dưới quyền migrator).
-- Mọi lỗi: 42501 (PostgREST trả 403), HINT là mã lý do ổn định; không lộ giá trị claim.
CREATE OR REPLACE FUNCTION xt.pgrst_gate(profile text, who text) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  c jsonb; hdr jsonb; aud jsonb; org jsonb; orgs text[]; org_alias text; sub text; azp text;
  iss text; method text; want text; t_id uuid; t_seq int; p record; reason text; r text;
BEGIN
  PERFORM set_config('xt.pgrst_ctx', '', true);
  IF session_user <> 'xt_authenticator' OR who IS DISTINCT FROM 'xt_web' THEN
    reason := 'not_postgrest';
  END IF;

  IF reason IS NULL THEN
    BEGIN
      c := nullif(current_setting('request.jwt.claims', true), '')::jsonb;
      hdr := coalesce(nullif(current_setting('request.headers', true), '')::jsonb, '{}');
    EXCEPTION WHEN others THEN
      reason := 'bad_request_context';
    END;
  END IF;
  IF reason IS NULL AND (c IS NULL OR jsonb_typeof(c) <> 'object') THEN
    reason := 'missing_token';
  END IF;
  IF reason IS NULL AND jsonb_typeof(hdr) <> 'object' THEN
    reason := 'bad_request_context';
  END IF;

  -- Access token Keycloak (typ=Bearer); refresh/ID token bị từ chối.
  IF reason IS NULL AND (jsonb_typeof(c -> 'typ') IS DISTINCT FROM 'string'
                         OR c ->> 'typ' <> 'Bearer') THEN
    reason := 'token_type';
  END IF;
  IF reason IS NULL THEN
    sub := CASE WHEN jsonb_typeof(c -> 'sub') = 'string' THEN c ->> 'sub' END;
    IF sub IS NULL OR char_length(sub) NOT BETWEEN 1 AND 255 THEN
      reason := 'missing_claim:sub';
    END IF;
  END IF;
  -- PostgREST đã kiểm jwt-aud/exp; kiểm lại để sai cấu hình vẫn fail-closed.
  IF reason IS NULL THEN
    aud := c -> 'aud';
    IF NOT (coalesce(jsonb_typeof(aud) = 'string' AND aud #>> '{}' = 'xt-data', false)
            OR coalesce(jsonb_typeof(aud) = 'array' AND aud @> '["xt-data"]'::jsonb, false)) THEN
      reason := 'audience';
    END IF;
  END IF;
  IF reason IS NULL THEN
    BEGIN
      IF jsonb_typeof(c -> 'exp') IS DISTINCT FROM 'number'
         OR (c ->> 'exp')::numeric <= extract(epoch FROM clock_timestamp()) THEN
        reason := 'expired';
      END IF;
    EXCEPTION WHEN others THEN
      reason := 'expired';
    END;
  END IF;
  -- PostgREST không kiểm iss: so với issuer đã cấu hình (thiếu cấu hình => từ chối).
  IF reason IS NULL THEN
    SELECT pl.value INTO iss FROM xt_meta.platform pl WHERE pl.key = 'pgrst_issuer';
    IF iss IS NULL OR jsonb_typeof(c -> 'iss') IS DISTINCT FROM 'string' OR c ->> 'iss' <> iss THEN
      reason := 'issuer';
    END IF;
  END IF;
  IF reason IS NULL THEN
    azp := CASE WHEN jsonb_typeof(c -> 'azp') = 'string' THEN c ->> 'azp' END;
    IF azp IS NULL OR NOT EXISTS (SELECT 1 FROM xt_meta.pgrst_client pc WHERE pc.client_id = azp) THEN
      reason := 'client_not_allowed';
    END IF;
  END IF;

  -- Organization (mapper Keycloak, cùng quy tắc với xt.adapters.identity.access_token): list alias,
  -- một chuỗi, hoặc object {alias: {...}}. Đúng một tenant; nhiều => tenant_ambiguous.
  IF reason IS NULL THEN
    org := c -> 'organization';
    IF org IS NULL OR jsonb_typeof(org) = 'null' THEN
      orgs := '{}';
    ELSIF jsonb_typeof(org) = 'string' THEN
      orgs := ARRAY[org #>> '{}'];
    ELSIF jsonb_typeof(org) = 'object' THEN
      SELECT coalesce(array_agg(DISTINCT k), '{}') INTO orgs FROM jsonb_object_keys(org) k;
    ELSIF jsonb_typeof(org) = 'array' THEN
      IF EXISTS (SELECT 1 FROM jsonb_array_elements(org) e WHERE jsonb_typeof(e) <> 'string') THEN
        reason := 'bad_claim:organization';
      ELSE
        SELECT coalesce(array_agg(DISTINCT e), '{}') INTO orgs FROM jsonb_array_elements_text(org) e;
      END IF;
    ELSE
      reason := 'bad_claim:organization';
    END IF;
  END IF;
  IF reason IS NULL THEN
    IF cardinality(orgs) = 0 THEN
      reason := 'missing_tenant';
    ELSIF cardinality(orgs) > 1 THEN
      reason := 'tenant_ambiguous';
    ELSE
      org_alias := orgs[1];
      SELECT t.id, t.seq INTO t_id, t_seq
        FROM xt_meta.tenant_profile tp JOIN xt_meta.tenant t ON t.id = tp.tenant_id
       WHERE tp.alias = org_alias;
      IF t_id IS NULL THEN
        reason := 'unknown_tenant';
      END IF;
    END IF;
  END IF;

  -- Profile: schema của request phải đúng api_t<seq> của tenant trong token, header bắt buộc
  -- (Accept-Profile khi đọc, Content-Profile khi ghi); không dựa vào schema mặc định. Chỉ đọc.
  IF reason IS NULL THEN
    want := 'api_t' || t_seq::text;
    method := upper(coalesce(current_setting('request.method', true), ''));
    IF coalesce(current_setting('request.path', true), '') ~* '^/+rpc(/|$)' THEN
      reason := 'rpc_disabled';
    ELSIF method IN ('GET', 'HEAD') THEN
      IF (hdr ->> 'accept-profile') IS DISTINCT FROM want THEN
        reason := 'profile_mismatch';
      END IF;
    ELSIF (hdr ->> 'content-profile') IS DISTINCT FROM want
          OR (hdr ? 'accept-profile' AND (hdr ->> 'accept-profile') IS DISTINCT FROM want) THEN
      reason := 'profile_mismatch';
    ELSE
      reason := 'read_only_api';
    END IF;
    IF reason IS NULL AND profile IS DISTINCT FROM want THEN
      reason := 'profile_mismatch';
    END IF;
  END IF;

  -- Vai trò/phạm vi lấy từ bảng tổ chức của xT (không từ token), như đường MCP: sub phải gắn với
  -- một người đang hoạt động của đúng tenant.
  IF reason IS NULL THEN
    SELECT pe.id, ARRAY(SELECT DISTINCT role FROM (
             SELECT unnest(pe.roles) AS role
             UNION ALL
             SELECT g.role FROM xt_meta.person_role_grant g
              WHERE g.tenant_id = pe.tenant_id AND g.person = pe.id AND g.revoked_at IS NULL
           ) effective ORDER BY role) AS roles, pe.unit INTO p
      FROM xt_meta.person pe
     WHERE pe.tenant_id = t_id AND pe.subject = sub AND pe.active;
    IF NOT FOUND THEN
      reason := 'invalid_behalf';
    ELSE
      FOREACH r IN ARRAY p.roles LOOP
        IF r !~ '^[a-z][a-z0-9_]{0,62}$' THEN
          reason := 'bad_role';
        END IF;
      END LOOP;
    END IF;
  END IF;

  IF reason IS NOT NULL THEN
    RAISE EXCEPTION 'truy cập dữ liệu bị từ chối' USING ERRCODE = 'insufficient_privilege',
      HINT = reason;
  END IF;

  PERFORM xt.set_context(jsonb_build_object(
    't', t_id, 'a', p.id, 'r', to_jsonb(p.roles), 'tm', '[]'::jsonb,
    -- Lọc ở từng view, kể cả quan hệ nhúng; admin vẫn phải qua quyền đọc RLS của model.
    'disabled_models', CASE WHEN 'admin' = ANY(p.roles) THEN '[]'::jsonb ELSE
      (SELECT coalesce(jsonb_agg(DISTINCT m.name), '[]'::jsonb)
         FROM xt_meta.pack_install pi CROSS JOIN LATERAL unnest(pi.models) m(name)
        WHERE pi.tenant_id = t_id AND pi.status = 'disabled') END,
    'd', CASE WHEN p.unit IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(p.unit) END));
END $$;

-- Wrapper SECURITY DEFINER đọc profile từ GUC thô trước khi gate ghim search_path. Thân chỉ
-- dùng tên đủ schema, kể cả kiểu cast: search_path do caller đặt không chiếm được đối tượng.
-- GUC role do PostgREST SET LOCAL ROLE đặt; SECURITY DEFINER không đổi giá trị GUC này.
CREATE OR REPLACE FUNCTION xt.pgrst_pre() RETURNS void
LANGUAGE sql VOLATILE SECURITY DEFINER AS
$$ SELECT xt.pgrst_gate(pg_catalog.btrim(pg_catalog.split_part(
                         pg_catalog.current_setting('search_path', true), ',', 1), ' "'),
                       pg_catalog.current_setting('role', true)::pg_catalog.text) $$;

REVOKE ALL ON FUNCTION xt.pgrst_config(), xt.pgrst_gate(text, text), xt.pgrst_pre() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION xt.pgrst_config() TO xt_authenticator;
REVOKE ALL ON FUNCTION xt.pgrst_gate(text, text) FROM xt_web;
GRANT EXECUTE ON FUNCTION xt.pgrst_pre() TO xt_web;
RESET ROLE;
