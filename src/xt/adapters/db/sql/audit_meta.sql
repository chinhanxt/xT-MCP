-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
--
-- Lớp bảo vệ audit (chạy bằng SUPERUSER, idempotent): bootstrap() chạy ngay sau bootstrap.sql;
-- CSDL đã bootstrap trước đó chạy qua `xt.adapters.db.tenancy.install_meta_audit()`.
--
-- Mô hình đe dọa (docs/decisions.md 16/10/2026): xt_migrator được tin để đổi schema, KHÔNG được tin
-- với audit. Mọi thứ quyết định nội dung audit thuộc xt_audit_owner hoặc superuser:
--   * Registry (xt_audit.rel_registry / xt_audit.sensitive): bảng nào được audit, cột nào sensitive
--     (theo attnum, sống qua rename cột). install() chỉ MỞ RỘNG tập cột sensitive; cột bị bỏ khỏi
--     catalog vẫn được che ("redacted") cho tới khi superuser gọi xt_audit.declassify (R3-03).
--   * Ngữ cảnh actor của audit: xt_audit.session_ctx do xt.set_context (chủ xt_audit_owner) ghi;
--     migrator không ghi được, nên không giả được actor/trace trong event (R3-02).
--   * log()/log_meta() kiểm bảng có trong registry (TG_RELID) và một record_id chỉ thuộc đúng một
--     bảng: không chèn được event giả vào lịch sử bản ghi thật bằng bảng tráo tên (R3-04).
--   * Event trigger (superuser, SECURITY DEFINER): mọi bảng thường trong xt_t<seq> tự được gắn
--     trigger audit; trigger audit phải luôn bật; bảng mang trigger audit không được rời schema của
--     nó; cấm ALTER SCHEMA trên schema có bảng audit, INHERITS/ATTACH PARTITION, RULE; hàm audit
--     và xt.set_context chỉ superuser đổi được (R3-01, R3-02).
--   * Audit bảng xt_meta.* (I-AUD-META): install_meta / install_meta2, event nối vào chuỗi tenant.

-- ------------------------------------------------------------------ registry + ngữ cảnh ----
SET ROLE xt_audit_owner;

CREATE TABLE IF NOT EXISTS xt_audit.rel_registry (
  relid     oid PRIMARY KEY,
  kind      text NOT NULL CHECK (kind IN ('model', 'meta')),
  relname   name NOT NULL,             -- tên lúc install (meta: không được đổi tên)
  installed timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS xt_audit.sensitive (
  relid  oid NOT NULL,
  attnum int2 NOT NULL,
  active boolean NOT NULL DEFAULT true,  -- false: catalog đã bỏ, vẫn che cho tới declassify
  PRIMARY KEY (relid, attnum)
);
CREATE UNLOGGED TABLE IF NOT EXISTS xt_audit.session_ctx (
  pid             int PRIMARY KEY,
  xid             xid8 NOT NULL,
  actor_id        uuid,
  effective_actor uuid,
  client_azp      text,
  trace_id        text
);
REVOKE ALL ON xt_audit.rel_registry, xt_audit.sensitive, xt_audit.session_ctx FROM PUBLIC;
ALTER TABLE xt_audit.event ADD COLUMN IF NOT EXISTS relid oid;
CREATE INDEX IF NOT EXISTS event_tenant_record ON xt_audit.event (tenant_id, record_id);

-- Nối một event vào chuỗi của tenant. Actor/trace lấy từ ngữ cảnh do xt.set_context ghi (không
-- do caller truyền). Ràng record_id ↔ bảng (relid): event của bảng khác cho cùng record bị từ chối.
CREATE OR REPLACE FUNCTION xt_audit.append(t uuid, model text, rid uuid, op text, d jsonb, rel oid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, xt_audit, pg_temp AS $$
DECLARE prev bytea; h bytea; ts timestamptz := clock_timestamp(); cx record; new_seq bigint;
BEGIN
  SELECT c.actor_id, c.effective_actor, c.client_azp, c.trace_id INTO cx
    FROM xt_audit.session_ctx c
   WHERE c.pid = pg_backend_pid() AND c.xid = pg_current_xact_id_if_assigned();
  PERFORM pg_advisory_xact_lock(hashtextextended('xt_audit:' || t::text, 0));
  IF rid <> '00000000-0000-0000-0000-000000000000'::uuid AND EXISTS (
       SELECT 1 FROM xt_audit.event e
        WHERE e.tenant_id = t AND e.record_id = rid AND e.relid IS NOT NULL AND e.relid <> rel) THEN
    RAISE EXCEPTION 'record % đã thuộc một bảng khác: không ghi event giả vào lịch sử của nó', rid
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO xt_audit.head (tenant_id, seq, hash)
  VALUES (t, 0, '\x0000000000000000000000000000000000000000000000000000000000000000'::bytea)
  ON CONFLICT (tenant_id) DO NOTHING;
  SELECT hd.hash INTO prev FROM xt_audit.head hd WHERE hd.tenant_id = t FOR UPDATE;
  h := xt_audit.digest(prev || xt_audit.canon(t, cx.actor_id, cx.effective_actor, cx.client_azp,
         cx.trace_id, ts, model, rid, op, d), 'sha256');
  INSERT INTO xt_audit.event (tenant_id, actor_id, effective_actor, client_azp, trace_id, at, model,
                              record_id, op, diff, prev_hash, hash, relid)
  VALUES (t, cx.actor_id, cx.effective_actor, cx.client_azp, cx.trace_id, ts, model, rid, op, d,
          prev, h, rel)
  RETURNING seq INTO new_seq;
  UPDATE xt_audit.head SET seq = new_seq, hash = h WHERE tenant_id = t;
END $$;
REVOKE ALL ON FUNCTION xt_audit.append(uuid, text, uuid, text, jsonb, oid) FROM PUBLIC;

-- ------------------------------------------------------------------ bảng model xt_t<seq> ----
CREATE OR REPLACE FUNCTION xt_audit.log() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, xt_audit, pg_temp AS $$
DECLARE
  t uuid; rid uuid; d jsonb; owner_t uuid; hm jsonb; kid int; c text; part text; v text;
  s record;
BEGIN
  IF TG_NAME <> 'xt_audit' THEN
    RAISE EXCEPTION 'xt_audit.log chỉ chạy qua trigger xt_audit' USING ERRCODE = 'insufficient_privilege';
  END IF;
  owner_t := xt_audit.schema_tenant(TG_TABLE_SCHEMA);
  IF owner_t IS NULL OR NOT EXISTS (SELECT 1 FROM xt_audit.rel_registry r
                                     WHERE r.relid = TG_RELID AND r.kind = 'model') THEN
    RAISE EXCEPTION 'audit chỉ áp cho bảng model đã đăng ký của tenant (%.%)', TG_TABLE_SCHEMA,
      TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'DELETE' THEN
    t := OLD.tenant_id; rid := OLD.id; d := jsonb_build_object('old', to_jsonb(OLD));
  ELSIF TG_OP = 'INSERT' THEN
    t := NEW.tenant_id; rid := NEW.id; d := jsonb_build_object('new', to_jsonb(NEW));
  ELSE
    t := NEW.tenant_id; rid := NEW.id;
    SELECT jsonb_build_object('old', coalesce(jsonb_object_agg(n.k, o.value), '{}'),
                              'new', coalesce(jsonb_object_agg(n.k, n.value), '{}'))
      INTO d
      FROM jsonb_each(to_jsonb(NEW)) n(k, value)
      JOIN jsonb_each(to_jsonb(OLD)) o(k2, value) ON o.k2 = n.k
     WHERE n.value IS DISTINCT FROM o.value AND n.k NOT IN ('revision', 'updated_at', 'updated_by');
  END IF;
  IF t IS DISTINCT FROM owner_t THEN
    RAISE EXCEPTION 'tenant_id của dòng không thuộc schema %', TG_TABLE_SCHEMA
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  -- Cột sensitive theo REGISTRY (không theo tham số trigger): active → bắt buộc HMAC do xt-api cấp;
  -- inactive (catalog đã bỏ, chưa declassify) → HMAC nếu có, không thì "redacted".
  FOR s IN SELECT a.attname, x.active FROM xt_audit.sensitive x
             JOIN pg_attribute a ON a.attrelid = x.relid AND a.attnum = x.attnum
            WHERE x.relid = TG_RELID AND NOT a.attisdropped LOOP
    c := s.attname;
    FOREACH part IN ARRAY ARRAY['old', 'new'] LOOP
      IF d -> part ? c AND jsonb_typeof(d -> part -> c) <> 'null' THEN
        IF hm IS NULL THEN
          BEGIN
            hm := coalesce(nullif(current_setting('xt.audit_hmac', true), '')::jsonb, '{}');
          EXCEPTION WHEN others THEN hm := '{}';
          END;
          SELECT p.kid INTO kid FROM xt_audit.pepper p WHERE p.active;
        END IF;
        v := hm -> part ->> c;
        IF v IS NOT NULL AND kid IS NOT NULL AND v ~ ('^hmac:' || kid || ':[0-9a-f]{32}$') THEN
          d := jsonb_set(d, ARRAY[part, c], to_jsonb(v));
        ELSIF s.active THEN
          RAISE EXCEPTION 'ghi field sensitive "%" cần HMAC (%) do xt-api cấp cho khóa đang dùng', c, part
            USING ERRCODE = 'insufficient_privilege';
        ELSE
          d := jsonb_set(d, ARRAY[part, c], to_jsonb('redacted'::text));
        END IF;
      END IF;
    END LOOP;
  END LOOP;
  PERFORM xt_audit.append(t, TG_TABLE_NAME, rid, TG_OP, d, TG_RELID);
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION xt_audit.log_truncate() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, xt_audit, pg_temp AS $$
DECLARE owner_t uuid := xt_audit.schema_tenant(TG_TABLE_SCHEMA);
BEGIN
  IF TG_NAME <> 'xt_audit_truncate' OR owner_t IS NULL THEN
    RAISE EXCEPTION 'xt_audit.log_truncate chỉ chạy qua trigger xt_audit_truncate'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  PERFORM xt_audit.append(owner_t, TG_TABLE_NAME, '00000000-0000-0000-0000-000000000000', 'TRUNCATE',
                          jsonb_build_object('by', session_user::text), TG_RELID);
  RETURN NULL;
END $$;

-- Cài/cập nhật audit cho bảng model. `cols` = cột sensitive theo catalog: được THÊM vào registry
-- (active); cột đang có trong registry mà không còn trong `cols` chỉ chuyển inactive (vẫn che).
CREATE OR REPLACE FUNCTION xt_audit.install(sch name, tbl name, cols text[]) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE c text; rel oid; att int2; keep int2[] := '{}';
BEGIN
  IF xt_audit.schema_tenant(sch) IS NULL THEN
    RAISE EXCEPTION 'không phải schema tenant: %', sch USING ERRCODE = 'insufficient_privilege';
  END IF;
  rel := to_regclass(format('%I.%I', sch, tbl));
  IF rel IS NULL OR (SELECT relkind FROM pg_class WHERE oid = rel) <> 'r' THEN
    RAISE EXCEPTION 'bảng %.% không tồn tại', sch, tbl;
  END IF;
  INSERT INTO xt_audit.rel_registry (relid, kind, relname) VALUES (rel, 'model', tbl)
  ON CONFLICT (relid) DO UPDATE SET relname = EXCLUDED.relname
   WHERE xt_audit.rel_registry.kind = 'model';
  FOREACH c IN ARRAY coalesce(cols, '{}') LOOP
    SELECT a.attnum INTO att FROM pg_attribute a
     WHERE a.attrelid = rel AND a.attname = c AND a.attnum > 0 AND NOT a.attisdropped;
    IF att IS NULL THEN
      RAISE EXCEPTION 'cột % không tồn tại trong %.%', c, sch, tbl;
    END IF;
    keep := keep || att;
    INSERT INTO xt_audit.sensitive (relid, attnum, active) VALUES (rel, att, true)
    ON CONFLICT (relid, attnum) DO UPDATE SET active = true;
  END LOOP;
  UPDATE xt_audit.sensitive SET active = false WHERE relid = rel AND NOT (attnum = ANY (keep));
  EXECUTE format('CREATE OR REPLACE TRIGGER xt_audit AFTER INSERT OR UPDATE OR DELETE ON %I.%I'
                 ' FOR EACH ROW EXECUTE FUNCTION xt_audit.log()', sch, tbl);
  EXECUTE format('CREATE OR REPLACE TRIGGER xt_audit_truncate AFTER TRUNCATE ON %I.%I'
                 ' FOR EACH STATEMENT EXECUTE FUNCTION xt_audit.log_truncate()', sch, tbl);
END $$;

-- ------------------------------------------------------------------ bảng xt_meta.* ----
-- tham số trigger: 'v2', cột tenant ('tenant_id' | 'seq:<cột int>' | 'self:<cột>'), cột khóa
-- (nối bằng dấu phẩy), rồi các cột redact. Bản cũ (không có 'v2'): khóa, rồi redact.
CREATE OR REPLACE FUNCTION xt_audit.log_meta() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, xt_audit, pg_temp AS $$
DECLARE
  row_new jsonb; row_old jsonb; r jsonb; d jsonb; t uuid; rid uuid; tenant_spec text := 'tenant_id';
  keys text[]; first_redact int := 1; c text; part text; i int; hm jsonb; v text; kv text;
BEGIN
  IF TG_NAME <> 'xt_audit_meta' OR TG_TABLE_SCHEMA <> 'xt_meta' OR NOT EXISTS (
       SELECT 1 FROM xt_audit.rel_registry g WHERE g.relid = TG_RELID AND g.kind = 'meta') THEN
    RAISE EXCEPTION 'xt_audit.log_meta chỉ chạy qua trigger xt_audit_meta trên bảng xt_meta đã đăng ký'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_ARGV[0] = 'v2' THEN
    tenant_spec := TG_ARGV[1];
    keys := string_to_array(TG_ARGV[2], ',');
    first_redact := 3;
  ELSE
    keys := ARRAY[TG_ARGV[0]];
  END IF;
  IF TG_OP <> 'INSERT' THEN row_old := to_jsonb(OLD); END IF;
  IF TG_OP <> 'DELETE' THEN row_new := to_jsonb(NEW); END IF;
  r := coalesce(row_new, row_old);
  IF tenant_spec LIKE 'seq:%' THEN
    SELECT x.id INTO t FROM xt_meta.tenant x WHERE x.seq = (r ->> substr(tenant_spec, 5))::int;
    IF t IS NULL AND TG_OP = 'DELETE' THEN  -- tenant đã xóa (cascade): chuỗi nền tảng
      t := '00000000-0000-0000-0000-000000000000';
    END IF;
  ELSIF tenant_spec LIKE 'self:%' THEN
    t := r ->> substr(tenant_spec, 6);
  ELSE
    t := r ->> tenant_spec;
  END IF;
  -- khóa 'id' (uuid riêng của dòng) dùng trực tiếp; khóa khác (vd person, tenant_id) có thể trùng
  -- record_id của bảng khác → băm theo bảng để mỗi record_id chỉ thuộc một bảng (R3-04)
  IF keys = ARRAY['id'] AND (r ->> 'id') ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
    rid := r ->> keys[1];
  ELSE
    SELECT string_agg(coalesce(r ->> k, ''), '|' ORDER BY o) INTO kv
      FROM unnest(keys) WITH ORDINALITY u(k, o);
    rid := md5('xt_meta.' || TG_TABLE_NAME || ':' || kv)::uuid;
  END IF;
  IF t IS NULL OR rid IS NULL
     OR (TG_OP <> 'DELETE' AND tenant_spec NOT LIKE 'self:%'
         AND NOT EXISTS (SELECT 1 FROM xt_meta.tenant x WHERE x.id = t)) THEN
    RAISE EXCEPTION 'dòng metadata thiếu tenant/khóa hợp lệ' USING ERRCODE = 'insufficient_privilege';
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
  -- Cột redact: HMAC có khóa do xt-api cấp (GUC xt.audit_hmac {"meta:<bảng>": {...}}); không có thì
  -- sha256 (chỉ an toàn cho blob entropy cao, vd receipt).
  IF TG_NARGS > first_redact THEN
    BEGIN
      hm := nullif(current_setting('xt.audit_hmac', true), '')::jsonb;
    EXCEPTION WHEN others THEN hm := NULL;
    END;
  END IF;
  FOR i IN first_redact .. TG_NARGS - 1 LOOP
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
  PERFORM xt_audit.append(t, 'xt_meta.' || TG_TABLE_NAME, rid, TG_OP, d, TG_RELID);
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
                          jsonb_build_object('by', session_user::text), TG_RELID);
  RETURN NULL;
END $$;

-- Gắn audit cho bảng xt_meta. tenant_col: cột uuid tenant ('tenant_id'), 'seq:<cột int tenant_seq>',
-- hoặc 'self:<cột>' (chính bảng tenant). key_cols: cột khóa (một cột uuid → dùng trực tiếp; còn lại
-- record_id = md5 của bảng + giá trị khóa).
CREATE OR REPLACE FUNCTION xt_audit.install_meta2(tbl name, key_cols text[], redact text[] DEFAULT '{}',
                                                  tenant_col text DEFAULT 'tenant_id')
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE rel regclass; c text; args text; tcol text;
BEGIN
  rel := to_regclass(format('xt_meta.%I', tbl));
  IF rel IS NULL THEN RAISE EXCEPTION 'bảng xt_meta.% không tồn tại', tbl; END IF;
  IF coalesce(array_length(key_cols, 1), 0) = 0 THEN RAISE EXCEPTION 'cần ít nhất một cột khóa'; END IF;
  tcol := regexp_replace(tenant_col, '^(seq|self):', '');
  FOREACH c IN ARRAY (key_cols || ARRAY[tcol] || coalesce(redact, '{}')) LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = rel AND attname = c
                   AND attnum > 0 AND NOT attisdropped) THEN
      RAISE EXCEPTION 'cột % không tồn tại trong xt_meta.%', c, tbl;
    END IF;
  END LOOP;
  IF tenant_col NOT LIKE 'seq:%' AND NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = rel
       AND attname = tcol AND atttypid = 'uuid'::regtype) THEN
    RAISE EXCEPTION 'xt_meta.% cần cột uuid %', tbl, tcol;
  END IF;
  INSERT INTO xt_audit.rel_registry (relid, kind, relname) VALUES (rel, 'meta', tbl)
  ON CONFLICT (relid) DO UPDATE SET kind = 'meta', relname = EXCLUDED.relname;
  args := quote_literal('v2') || ', ' || quote_literal(tenant_col) || ', '
          || quote_literal(array_to_string(key_cols, ','));
  FOREACH c IN ARRAY coalesce(redact, '{}') LOOP
    args := args || ', ' || quote_literal(c);
  END LOOP;
  EXECUTE format('CREATE OR REPLACE TRIGGER xt_audit_meta AFTER INSERT OR UPDATE OR DELETE'
                 ' ON xt_meta.%I FOR EACH ROW EXECUTE FUNCTION xt_audit.log_meta(%s)', tbl, args);
  EXECUTE format('CREATE OR REPLACE TRIGGER xt_audit_meta_truncate AFTER TRUNCATE ON xt_meta.%I'
                 ' FOR EACH STATEMENT EXECUTE FUNCTION xt_audit.log_meta_truncate()', tbl);
END $$;

-- API cũ (giữ chữ ký cho migration 0006/0009/0010/0014): bảng có tenant_id uuid + một cột khóa.
CREATE OR REPLACE FUNCTION xt_audit.install_meta(tbl name, key_col name DEFAULT 'id',
                                                 redact text[] DEFAULT '{}')
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT xt_audit.install_meta2(tbl, ARRAY[key_col::text], redact, 'tenant_id')
$$;

-- Đọc nguyên dòng chuỗi (kiểm phía client, R3-05): không phán quyết gì trong DB.
CREATE OR REPLACE FUNCTION xt_audit.chain(t uuid)
RETURNS TABLE (seq bigint, actor_id uuid, effective_actor uuid, client_azp text, trace_id text,
               at timestamptz, model text, record_id uuid, op text, diff text, prev_hash bytea,
               hash bytea)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT e.seq, e.actor_id, e.effective_actor, e.client_azp, e.trace_id, e.at, e.model, e.record_id,
         e.op, e.diff::text, e.prev_hash, e.hash
    FROM xt_audit.event e WHERE e.tenant_id = t ORDER BY e.seq
$$;

REVOKE ALL ON FUNCTION xt_audit.log_meta(), xt_audit.log_meta_truncate(),
  xt_audit.install_meta(name, name, text[]), xt_audit.install_meta2(name, text[], text[], text),
  xt_audit.chain(uuid) FROM PUBLIC;
RESET ROLE;

-- Hàm append cũ (5 tham số) chỉ còn là chốt chặn: không ai gọi được để nối event.
SET ROLE xt_audit_owner;
CREATE OR REPLACE FUNCTION xt_audit.append(t uuid, model text, rid uuid, op text, d jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  RAISE EXCEPTION 'xt_audit.append(5 tham số) đã bỏ' USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION xt_audit.append(uuid, text, uuid, text, jsonb) FROM PUBLIC;
RESET ROLE;
GRANT EXECUTE ON FUNCTION xt_audit.install(name, name, text[]),
  xt_audit.install_meta(name, name, text[]), xt_audit.install_meta2(name, text[], text[], text)
  TO xt_migrator;
GRANT EXECUTE ON FUNCTION xt_audit.chain(uuid) TO xt_app;

-- Hạ nhãn sensitive thật sự (ghi plaintext từ đây) cần superuser: không có GRANT nào.
CREATE OR REPLACE FUNCTION xt_audit.declassify(rel regclass, col name) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  DELETE FROM xt_audit.sensitive s USING pg_attribute a
   WHERE s.relid = rel AND a.attrelid = rel AND a.attname = col AND s.attnum = a.attnum
$$;
REVOKE ALL ON FUNCTION xt_audit.declassify(regclass, name) FROM PUBLIC;

-- Ngữ cảnh: xt.set_context (8 tham số) thuộc xt_audit_owner, ghi cả ngữ cảnh RBAC (xt_meta) lẫn
-- ngữ cảnh actor của audit (xt_audit.session_ctx, migrator không ghi được).
GRANT SELECT, INSERT, UPDATE ON xt_meta.session_ctx TO xt_audit_owner;
CREATE OR REPLACE FUNCTION xt.set_context(t uuid, a uuid, r text[], teams uuid[], depts uuid[],
                                          ea uuid, azp text, tr text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE x text;
BEGIN
  IF t IS NULL THEN RAISE EXCEPTION 'thiếu tenant'; END IF;
  FOREACH x IN ARRAY coalesce(r, '{}') LOOP
    IF x !~ '^[a-z][a-z0-9_]{0,62}$' THEN RAISE EXCEPTION 'tên vai trò không hợp lệ: %', x; END IF;
  END LOOP;
  INSERT INTO xt_meta.session_ctx AS c VALUES (pg_backend_pid(), pg_current_xact_id(), t, a,
         coalesce(r, '{}'), coalesce(teams, '{}'), coalesce(depts, '{}'), ea, azp, tr)
  ON CONFLICT (pid) DO UPDATE SET xid = EXCLUDED.xid, tenant_id = EXCLUDED.tenant_id,
     actor_id = EXCLUDED.actor_id, roles = EXCLUDED.roles, team_ids = EXCLUDED.team_ids,
     department_ids = EXCLUDED.department_ids, effective_actor = EXCLUDED.effective_actor,
     client_azp = EXCLUDED.client_azp, trace_id = EXCLUDED.trace_id;
  INSERT INTO xt_audit.session_ctx AS c VALUES (pg_backend_pid(), pg_current_xact_id(), a, ea, azp, tr)
  ON CONFLICT (pid) DO UPDATE SET xid = EXCLUDED.xid, actor_id = EXCLUDED.actor_id,
     effective_actor = EXCLUDED.effective_actor, client_azp = EXCLUDED.client_azp,
     trace_id = EXCLUDED.trace_id;
  PERFORM set_config('xt.tenant_id', t::text, true);  -- chỉ để tương thích hàng đợi
END $$;
ALTER FUNCTION xt.set_context(uuid, uuid, text[], uuid[], uuid[], uuid, text, text)
  OWNER TO xt_audit_owner;
REVOKE ALL ON FUNCTION xt.set_context(uuid, uuid, text[], uuid[], uuid[], uuid, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION xt.set_context(uuid, uuid, text[], uuid[], uuid[], uuid, text, text)
  TO xt_app;
GRANT SELECT ON xt_meta.tenant TO xt_audit_owner;

-- Registry cho bảng đã gắn trigger trước khi có registry (CSDL nâng cấp).
INSERT INTO xt_audit.rel_registry (relid, kind, relname)
SELECT g.tgrelid, CASE WHEN g.tgname = 'xt_audit' THEN 'model' ELSE 'meta' END, c.relname
  FROM pg_trigger g JOIN pg_class c ON c.oid = g.tgrelid
 WHERE g.tgname IN ('xt_audit', 'xt_audit_meta') AND NOT g.tgisinternal
ON CONFLICT (relid) DO NOTHING;
INSERT INTO xt_audit.sensitive (relid, attnum, active)
SELECT g.tgrelid, a.attnum, true
  FROM pg_trigger g
  CROSS JOIN LATERAL unnest(string_to_array(trim(trailing E'\\000' FROM encode(g.tgargs, 'escape')),
                                            E'\\000')) arg(col)
  JOIN pg_attribute a ON a.attrelid = g.tgrelid AND a.attname = arg.col AND NOT a.attisdropped
 WHERE g.tgname = 'xt_audit' AND g.tgnargs > 0
ON CONFLICT (relid, attnum) DO NOTHING;

-- ------------------------------------------------------------------ event trigger ----
CREATE OR REPLACE FUNCTION xt_audit.is_su() RETURNS boolean LANGUAGE sql STABLE
SET search_path = pg_catalog, pg_temp AS
$$ SELECT coalesce((SELECT rolsuper FROM pg_roles WHERE rolname = session_user), false) $$;

CREATE OR REPLACE FUNCTION xt_audit.ddl_guard() RETURNS event_trigger LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE r record; rel oid; nsp text; bad text; su boolean := xt_audit.is_su(); fn record;
BEGIN
  FOR r IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
    -- 1) hàm audit/ngữ cảnh: chỉ superuser (cài/nâng cấp) đổi được
    IF r.classid = 'pg_proc'::regclass AND NOT su THEN
      SELECT p.proname, n.nspname INTO fn FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
       WHERE p.oid = r.objid;
      IF fn.nspname = 'xt_audit' OR (fn.nspname = 'xt' AND fn.proname = 'set_context') THEN
        RAISE EXCEPTION 'hàm %.% chỉ superuser được đổi', fn.nspname, fn.proname
          USING ERRCODE = 'insufficient_privilege';
      END IF;
    END IF;
    -- 2) đổi tên/chủ schema chứa bảng audit hoặc schema nền tảng
    IF r.classid = 'pg_namespace'::regclass AND r.command_tag = 'ALTER SCHEMA' AND NOT su THEN
      IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_trigger g ON g.tgrelid = c.oid
                  WHERE c.relnamespace = r.objid AND g.tgname IN ('xt_audit', 'xt_audit_meta'))
         OR (SELECT nspname FROM pg_namespace WHERE oid = r.objid) ~ '^(xt|xt_meta|xt_audit|xt_t[0-9]+|api_t[0-9]+)$'
      THEN
        RAISE EXCEPTION 'không được ALTER SCHEMA % (chứa bảng có audit)', r.object_identity
          USING ERRCODE = 'insufficient_privilege';
      END IF;
    END IF;
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
    -- 3) kế thừa/partition dính tới bảng có audit hoặc nằm trong schema tenant
    IF EXISTS (SELECT 1 FROM pg_inherits i JOIN pg_class p ON p.oid = i.inhparent
                JOIN pg_class k ON k.oid = i.inhrelid
                JOIN pg_namespace pn ON pn.oid = p.relnamespace
                JOIN pg_namespace kn ON kn.oid = k.relnamespace
               WHERE (i.inhrelid = rel OR i.inhparent = rel)
                 AND (pn.nspname ~ '^xt_t[0-9]+$' OR kn.nspname ~ '^xt_t[0-9]+$'
                      OR EXISTS (SELECT 1 FROM xt_audit.rel_registry g
                                  WHERE g.relid IN (i.inhparent, i.inhrelid)))) THEN
      RAISE EXCEPTION 'cấm INHERITS/PARTITION với bảng có audit (%)', rel::regclass
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- 4) bảng mang trigger audit model không được rời schema tenant; meta không được rời xt_meta
    --    hay đổi tên
    IF EXISTS (SELECT 1 FROM pg_trigger g WHERE g.tgrelid = rel AND g.tgname = 'xt_audit')
       AND nsp !~ '^xt_t[0-9]+$' THEN
      RAISE EXCEPTION 'bảng model có audit không được rời schema tenant (%)', rel::regclass
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF EXISTS (SELECT 1 FROM xt_audit.rel_registry g WHERE g.relid = rel AND g.kind = 'meta'
                AND (nsp <> 'xt_meta' OR g.relname <> (SELECT relname FROM pg_class WHERE oid = rel)))
       AND NOT su THEN
      RAISE EXCEPTION 'bảng metadata có audit không được đổi tên/dời schema (%)', rel::regclass
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF nsp ~ '^xt_t[0-9]+$' THEN
      IF r.command_tag = 'CREATE RULE' THEN
        RAISE EXCEPTION 'cấm RULE trên bảng model tenant (né audit)'
          USING ERRCODE = 'insufficient_privilege';
      END IF;
      IF (SELECT relkind FROM pg_class WHERE oid = rel) = 'p' THEN
        RAISE EXCEPTION 'cấm bảng partition trong schema tenant' USING ERRCODE = 'insufficient_privilege';
      END IF;
      -- 5) mọi bảng thường trong schema tenant LUÔN có trigger audit (tự gắn nếu thiếu)
      --    (chỉ với lệnh trên bảng; lệnh CREATE TRIGGER lồng của chính install thì bỏ qua)
      IF r.classid = 'pg_class'::regclass AND (
           NOT EXISTS (SELECT 1 FROM pg_trigger g WHERE g.tgrelid = rel AND g.tgname = 'xt_audit')
           OR NOT EXISTS (SELECT 1 FROM pg_trigger g WHERE g.tgrelid = rel
                           AND g.tgname = 'xt_audit_truncate')) THEN
        EXECUTE format('GRANT TRIGGER ON %s TO xt_audit_owner', rel::regclass);
        PERFORM xt_audit.install(nsp::name, (SELECT relname FROM pg_class WHERE oid = rel),
          ARRAY(SELECT a.attname::text FROM xt_audit.sensitive s JOIN pg_attribute a
                 ON a.attrelid = s.relid AND a.attnum = s.attnum WHERE s.relid = rel AND s.active));
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
SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE r record; su boolean := xt_audit.is_su();
BEGIN
  FOR r IN SELECT * FROM pg_event_trigger_dropped_objects() LOOP
    IF NOT su AND r.original AND r.object_type = 'trigger' AND (
         (r.schema_name ~ '^xt_t[0-9]+$' AND (r.object_identity LIKE 'xt_audit on %'
                                              OR r.object_identity LIKE 'xt_audit_truncate on %'))
      OR (r.schema_name = 'xt_meta' AND (r.object_identity LIKE 'xt_audit_meta on %'
                                         OR r.object_identity LIKE 'xt_audit_meta_truncate on %'))) THEN
      RAISE EXCEPTION 'không được xóa trigger audit %', r.object_identity
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT su AND r.object_type = 'function' AND (r.schema_name = 'xt_audit'
         OR r.object_identity LIKE 'xt.set_context(%') THEN
      RAISE EXCEPTION 'không được xóa hàm %', r.object_identity USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF r.object_type = 'table' AND r.original THEN
      IF NOT su AND EXISTS (SELECT 1 FROM xt_audit.rel_registry g WHERE g.relid = r.objid
                             AND g.kind = 'meta') THEN
        RAISE EXCEPTION 'không được xóa bảng metadata có audit %', r.object_identity
          USING ERRCODE = 'insufficient_privilege';
      END IF;
    END IF;
    IF r.object_type = 'table' THEN  -- tránh oid bị tái dùng mang registry cũ
      DELETE FROM xt_audit.sensitive WHERE relid = r.objid;
      DELETE FROM xt_audit.rel_registry WHERE relid = r.objid AND kind = 'model';
    END IF;
  END LOOP;
END $$;
REVOKE ALL ON FUNCTION xt_audit.ddl_guard(), xt_audit.drop_guard(), xt_audit.is_su() FROM PUBLIC;
DROP EVENT TRIGGER IF EXISTS xt_audit_ddl_guard;
DROP EVENT TRIGGER IF EXISTS xt_audit_drop_guard;
CREATE EVENT TRIGGER xt_audit_ddl_guard ON ddl_command_end EXECUTE FUNCTION xt_audit.ddl_guard();
CREATE EVENT TRIGGER xt_audit_drop_guard ON sql_drop EXECUTE FUNCTION xt_audit.drop_guard();

INSERT INTO xt_meta.platform (key, value) VALUES ('audit_meta_version', '2')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
