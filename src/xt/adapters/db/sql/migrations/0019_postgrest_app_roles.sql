-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- G8: effective roles + disabled-pack row filters for PostgREST, without new privileges.
-- The audited gate is immutable to a plain migrator login. Upgrade with the existing
-- superuser install_data_prerequisites() path (SET LOCAL ROLE xt_migrator), or first run
-- install_postgrest_role() as superuser before `xt db migrate`. No audit guard is relaxed.

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

REVOKE ALL ON FUNCTION xt.pgrst_gate(text, text) FROM PUBLIC, xt_web;

-- Preserve view OIDs, owners, ACLs and the existing projection (especially sensitive PII).
DO $$
DECLARE v record; cols text;
BEGIN
  FOR v IN
    SELECT c.oid, n.nspname AS api, c.relname AS model, 'xt_t' || t.seq::text AS data
      FROM xt_meta.tenant t
      JOIN pg_namespace n ON n.nspname = 'api_t' || t.seq::text
      JOIN pg_class c ON c.relnamespace = n.oid AND c.relkind = 'v'
     WHERE c.relowner = 'xt_api_owner'::regrole
  LOOP
    SELECT string_agg(quote_ident(a.attname), ', ' ORDER BY a.attnum) INTO cols
      FROM pg_attribute a WHERE a.attrelid = v.oid AND a.attnum > 0 AND NOT a.attisdropped;
    SET LOCAL ROLE xt_api_owner;
    EXECUTE format(
      'CREATE OR REPLACE VIEW %I.%I WITH (security_invoker = false) AS SELECT %s FROM %I.%I'
      ' WHERE session_user <> %L OR coalesce(NOT ((nullif(current_setting(%L, true), %L)::jsonb'
      ' -> %L) ? %L), false)',
      v.api, v.model, cols, v.data, v.model, 'xt_authenticator', 'xt.pgrst_ctx', '',
      'disabled_models', v.model);
    SET LOCAL ROLE xt_migrator;
  END LOOP;
END $$;
