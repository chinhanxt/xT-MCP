-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Supersedes the view step of immutable 0019; protected gate DDL is superuser-only.

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
