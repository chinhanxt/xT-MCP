-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
--
-- Audit cho bảng metadata nền tảng xt_meta.* (I-AUD-META) + event trigger bảo vệ trigger audit.
-- Chạy bằng SUPERUSER, idempotent: bootstrap() chạy sau bootstrap.sql; CSDL đã bootstrap trước đó
-- chạy qua `xt.adapters.db.tenancy.install_meta_audit()`. Sau đó migration 0006 (xt_migrator) gắn
-- trigger cho các bảng xt_meta bằng `xt_audit.install_meta`.
--
-- Thiết kế:
--   * Event metadata nối vào CHUỖI CỦA TENANT (theo cột tenant_id của dòng), model =
--     'xt_meta.<bảng>', nên checkpoint ngoài DB (xt_audit.heads) và verify phủ luôn metadata.
--   * TRUNCATE (không biết tenant) nối vào chuỗi nền tảng tenant_id = 00000000-…-0000.
--   * Cột liệt kê ở `redact` lưu "redacted:hmac:<kid>:<hex>" khi xt-api cấp HMAC qua GUC
--     xt.audit_hmac = {"meta:<bảng>": {"new"|"old": {cột: "hmac:kid:hex"}}} (khóa không vào DB);
--     không có thì "redacted:sha256:<hex>" (chỉ cho blob entropy cao, ví dụ receipt).
--   * Trigger thuộc xt_audit_owner; xt_migrator chỉ gọi install_meta (sau khi GRANT TRIGGER);
--     event trigger chặn DISABLE/DROP trigger xt_audit_meta* và RULE trên bảng đã gắn.

SET ROLE xt_audit_owner;

CREATE OR REPLACE FUNCTION xt_audit.log_meta() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, xt_audit, pg_temp AS $$
DECLARE
  row_new jsonb; row_old jsonb; d jsonb; t uuid; rid uuid; key_col text := TG_ARGV[0];
  c text; part text; i int; hm jsonb; v text;
BEGIN
  IF TG_NAME <> 'xt_audit_meta' OR TG_TABLE_SCHEMA <> 'xt_meta' THEN
    RAISE EXCEPTION 'xt_audit.log_meta chỉ chạy qua trigger xt_audit_meta trên xt_meta.*'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP <> 'INSERT' THEN row_old := to_jsonb(OLD); END IF;
  IF TG_OP <> 'DELETE' THEN row_new := to_jsonb(NEW); END IF;
  t := coalesce(row_new, row_old) ->> 'tenant_id';
  rid := coalesce(row_new, row_old) ->> key_col;
  IF t IS NULL OR rid IS NULL
     OR (TG_OP <> 'DELETE' AND NOT EXISTS (SELECT 1 FROM xt_meta.tenant x WHERE x.id = t)) THEN
    RAISE EXCEPTION 'dòng metadata thiếu tenant_id/khóa hợp lệ' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'INSERT' THEN
    d := jsonb_build_object('new', row_new);
  ELSIF TG_OP = 'DELETE' THEN
    d := jsonb_build_object('old', row_old);
  ELSE
    SELECT jsonb_build_object('old', coalesce(jsonb_object_agg(n.k, o.value), '{}'),
                              'new', coalesce(jsonb_object_agg(n.k, n.value), '{}'))
      INTO d
      FROM jsonb_each(row_new) n(k, value) JOIN jsonb_each(row_old) o(k2, value) ON o.k2 = n.k
     WHERE n.value IS DISTINCT FROM o.value;
    IF d = '{"new": {}, "old": {}}'::jsonb THEN RETURN NULL; END IF;  -- UPDATE không đổi gì
  END IF;
  -- Cột redact: HMAC có khóa do xt-api cấp qua GUC xt.audit_hmac (khóa không bao giờ vào DB,
  -- như cột sensitive của model; FB-04: giá trị entropy thấp như tên tệp "CCCD_<số>.pdf"); không
  -- có HMAC hợp lệ thì sha256 (chỉ an toàn cho blob entropy cao, vd receipt).
  IF TG_NARGS > 1 THEN
    BEGIN
      hm := nullif(current_setting('xt.audit_hmac', true), '')::jsonb;
    EXCEPTION WHEN others THEN hm := NULL;
    END;
  END IF;
  FOR i IN 1 .. TG_NARGS - 1 LOOP
    c := TG_ARGV[i];
    FOREACH part IN ARRAY ARRAY['old', 'new'] LOOP
      IF d -> part ? c AND jsonb_typeof(d -> part -> c) <> 'null' THEN
        v := hm -> ('meta:' || TG_TABLE_NAME) -> part ->> c;
        IF v IS NOT NULL AND v ~ '^hmac:[0-9]+:[0-9a-f]{32,64}$' THEN
          d := jsonb_set(d, ARRAY[part, c], to_jsonb('redacted:' || v));
        ELSE
          d := jsonb_set(d, ARRAY[part, c], to_jsonb('redacted:sha256:' ||
               encode(xt_audit.digest(convert_to((d -> part -> c)::text, 'UTF8'), 'sha256'), 'hex')));
        END IF;
      END IF;
    END LOOP;
  END LOOP;
  PERFORM xt_audit.append(t, 'xt_meta.' || TG_TABLE_NAME, rid, TG_OP, d);
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION xt_audit.log_meta_truncate() RETURNS trigger LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, xt_audit, pg_temp AS $$
BEGIN
  IF TG_NAME <> 'xt_audit_meta_truncate' OR TG_TABLE_SCHEMA <> 'xt_meta' THEN
    RAISE EXCEPTION 'xt_audit.log_meta_truncate chỉ chạy qua trigger xt_audit_meta_truncate'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  PERFORM xt_audit.append('00000000-0000-0000-0000-000000000000', 'xt_meta.' || TG_TABLE_NAME,
                          '00000000-0000-0000-0000-000000000000', 'TRUNCATE',
                          jsonb_build_object('by', session_user::text));
  RETURN NULL;
END $$;

-- Gắn audit cho một bảng xt_meta (bảng phải có tenant_id uuid và cột khóa uuid duy nhất mỗi dòng).
CREATE OR REPLACE FUNCTION xt_audit.install_meta(tbl name, key_col name DEFAULT 'id',
                                                 redact text[] DEFAULT '{}')
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE rel regclass; c text; args text;
BEGIN
  rel := to_regclass(format('xt_meta.%I', tbl));
  IF rel IS NULL THEN RAISE EXCEPTION 'bảng xt_meta.% không tồn tại', tbl; END IF;
  FOREACH c IN ARRAY ARRAY['tenant_id', key_col::text] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = rel AND attname = c
                   AND atttypid = 'uuid'::regtype AND attnum > 0 AND NOT attisdropped) THEN
      RAISE EXCEPTION 'xt_meta.% cần cột uuid %', tbl, c;
    END IF;
  END LOOP;
  args := quote_literal(key_col);
  FOREACH c IN ARRAY coalesce(redact, '{}') LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = rel AND attname = c
                   AND attnum > 0 AND NOT attisdropped) THEN
      RAISE EXCEPTION 'cột % không tồn tại trong xt_meta.%', c, tbl;
    END IF;
    args := args || ', ' || quote_literal(c);
  END LOOP;
  EXECUTE format('CREATE OR REPLACE TRIGGER xt_audit_meta AFTER INSERT OR UPDATE OR DELETE'
                 ' ON xt_meta.%I FOR EACH ROW EXECUTE FUNCTION xt_audit.log_meta(%s)', tbl, args);
  EXECUTE format('CREATE OR REPLACE TRIGGER xt_audit_meta_truncate AFTER TRUNCATE ON xt_meta.%I'
                 ' FOR EACH STATEMENT EXECUTE FUNCTION xt_audit.log_meta_truncate()', tbl);
END $$;

REVOKE ALL ON FUNCTION xt_audit.log_meta(), xt_audit.log_meta_truncate(),
  xt_audit.install_meta(name, name, text[]) FROM PUBLIC;
RESET ROLE;
GRANT EXECUTE ON FUNCTION xt_audit.install_meta(name, name, text[]) TO xt_migrator;

-- Event trigger (superuser) bảo vệ trigger audit.
--   xt_t<seq>.*: chỉ được có xt_touch (xt.row_touch) và xt_audit/xt_audit_truncate (xt_audit.*),
--                luôn bật; cấm RULE.
--   xt_meta.*:   bảng đã gắn xt_audit_meta thì trigger đó (và _truncate) phải luôn bật, đúng hàm;
--                cấm RULE trên bảng đó.
CREATE OR REPLACE FUNCTION xt_audit.ddl_guard() RETURNS event_trigger LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp AS $$
DECLARE r record; rel oid; nsp text; bad text;
BEGIN
  FOR r IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
    rel := NULL;
    IF r.classid = 'pg_class'::regclass THEN
      SELECT c.oid INTO rel FROM pg_class c WHERE c.oid = r.objid AND c.relkind IN ('r', 'p');
    ELSIF r.classid = 'pg_trigger'::regclass THEN  -- schema_name của trigger là NULL
      SELECT g.tgrelid INTO rel FROM pg_trigger g WHERE g.oid = r.objid;
    ELSIF r.classid = 'pg_rewrite'::regclass THEN
      SELECT w.ev_class INTO rel FROM pg_rewrite w WHERE w.oid = r.objid;
    END IF;
    IF rel IS NULL THEN CONTINUE; END IF;
    SELECT n.nspname INTO nsp FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.oid = rel;
    IF nsp ~ '^xt_t[0-9]+$' THEN
      IF r.command_tag = 'CREATE RULE' THEN
        RAISE EXCEPTION 'cấm RULE trên bảng model tenant (né audit)'
          USING ERRCODE = 'insufficient_privilege';
      END IF;
      SELECT string_agg(g.tgname || ':' || g.tgenabled::text || ':' || p.oid::regprocedure::text, ', ')
        INTO bad
        FROM pg_trigger g JOIN pg_proc p ON p.oid = g.tgfoid
       WHERE g.tgrelid = rel AND NOT g.tgisinternal
         AND NOT (g.tgenabled = 'O' AND (
               (g.tgname = 'xt_touch' AND p.oid = 'xt.row_touch()'::regprocedure)
            OR (g.tgname = 'xt_audit' AND p.oid = 'xt_audit.log()'::regprocedure)
            OR (g.tgname = 'xt_audit_truncate' AND p.oid = 'xt_audit.log_truncate()'::regprocedure)));
    ELSIF nsp = 'xt_meta' AND EXISTS (SELECT 1 FROM pg_trigger g WHERE g.tgrelid = rel
                                       AND g.tgname IN ('xt_audit_meta', 'xt_audit_meta_truncate')) THEN
      IF r.command_tag = 'CREATE RULE' THEN
        RAISE EXCEPTION 'cấm RULE trên bảng metadata có audit (né audit)'
          USING ERRCODE = 'insufficient_privilege';
      END IF;
      SELECT string_agg(g.tgname || ':' || g.tgenabled::text || ':' || p.oid::regprocedure::text, ', ')
        INTO bad
        FROM pg_trigger g JOIN pg_proc p ON p.oid = g.tgfoid
       WHERE g.tgrelid = rel AND NOT g.tgisinternal
         AND (g.tgname IN ('xt_audit_meta', 'xt_audit_meta_truncate')
              OR p.oid IN ('xt_audit.log_meta()'::regprocedure,
                           'xt_audit.log_meta_truncate()'::regprocedure))
         AND NOT (g.tgenabled = 'O' AND (
               (g.tgname = 'xt_audit_meta' AND p.oid = 'xt_audit.log_meta()'::regprocedure)
            OR (g.tgname = 'xt_audit_meta_truncate'
                AND p.oid = 'xt_audit.log_meta_truncate()'::regprocedure)));
      -- metadata: không chặn trigger nghiệp vụ khác của bảng, chỉ bảo vệ trigger audit
    ELSE
      CONTINUE;
    END IF;
    IF bad IS NOT NULL THEN
      RAISE EXCEPTION 'trigger không được phép/bị tắt trên %: %', rel::regclass, bad
        USING ERRCODE = 'insufficient_privilege';
    END IF;
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION xt_audit.drop_guard() RETURNS event_trigger LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp AS $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM pg_event_trigger_dropped_objects() LOOP
    IF r.original AND r.object_type = 'trigger' AND current_user <> 'xt_audit_owner' AND (
         (r.schema_name ~ '^xt_t[0-9]+$' AND (r.object_identity LIKE 'xt_audit on %'
                                              OR r.object_identity LIKE 'xt_audit_truncate on %'))
      OR (r.schema_name = 'xt_meta' AND (r.object_identity LIKE 'xt_audit_meta on %'
                                         OR r.object_identity LIKE 'xt_audit_meta_truncate on %'))) THEN
      RAISE EXCEPTION 'không được xóa trigger audit %', r.object_identity
        USING ERRCODE = 'insufficient_privilege';
    END IF;
  END LOOP;
END $$;
REVOKE ALL ON FUNCTION xt_audit.ddl_guard(), xt_audit.drop_guard() FROM PUBLIC;
DROP EVENT TRIGGER IF EXISTS xt_audit_ddl_guard;
DROP EVENT TRIGGER IF EXISTS xt_audit_drop_guard;
CREATE EVENT TRIGGER xt_audit_ddl_guard ON ddl_command_end EXECUTE FUNCTION xt_audit.ddl_guard();
CREATE EVENT TRIGGER xt_audit_drop_guard ON sql_drop EXECUTE FUNCTION xt_audit.drop_guard();

INSERT INTO xt_meta.platform (key, value) VALUES ('audit_meta_version', '1')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
