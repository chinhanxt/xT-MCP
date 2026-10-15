-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Chạy bằng xt_migrator (xt db migrate). Lớp ghép: hồ sơ tenant (alias Organization, múi giờ,
-- llm_egress), tổ chức (đơn vị, người, quyền tự chủ agent), policy IAM, định nghĩa quy trình,
-- ràng buộc model <-> primitive, gói đã cài, thông báo, làn PII (vault token + nhật ký egress).
-- Mọi bảng có tenant_id + RLS theo xt.tenant(); xt_app chỉ đọc dữ liệu cấu hình (ghi bằng migrator
-- qua lệnh quản trị), chỉ ghi thông báo/PII theo đúng tenant của transaction.

CREATE TABLE xt_meta.tenant_profile (
  tenant_id  uuid PRIMARY KEY REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  alias      text NOT NULL UNIQUE CHECK (alias ~ '^[a-z0-9][a-z0-9_-]{0,63}$'),
  timezone   text NOT NULL CHECK (char_length(timezone) BETWEEN 1 AND 64),
  llm_egress text NOT NULL CHECK (llm_egress IN ('masked', 'local_only')),
  pii_epoch  int NOT NULL DEFAULT 1 CHECK (pii_epoch >= 1)
);

-- Tra tenant theo alias của claim Organization TRƯỚC khi có ngữ cảnh RLS (chỉ trả seq/id).
CREATE FUNCTION xt.tenant_by_alias(a text) RETURNS TABLE (seq int, id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT t.seq, t.id FROM xt_meta.tenant_profile p JOIN xt_meta.tenant t ON t.id = p.tenant_id
   WHERE p.alias = a
$$;
-- Trang /desk/confirm/{id} cần tenant của confirmation trước khi người duyệt đăng nhập; id là
-- 128 bit ngẫu nhiên, hàm chỉ lộ "có tồn tại" + tenant id (không lộ nội dung).
CREATE FUNCTION xt.confirmation_tenant(cid text) RETURNS TABLE (seq int, id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT t.seq, t.id FROM xt_meta.tool_operation o JOIN xt_meta.tenant t ON t.id = o.tenant_id
   WHERE o.confirmation_id = cid
$$;
-- Job của worker mang tenant_id (JobRequest); cần seq để dựng ngữ cảnh model engine.
CREATE FUNCTION xt.tenant_by_id(tid uuid) RETURNS TABLE (seq int, id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT t.seq, t.id FROM xt_meta.tenant t WHERE t.id = tid
$$;
REVOKE ALL ON FUNCTION xt.tenant_by_alias(text), xt.confirmation_tenant(text),
  xt.tenant_by_id(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION xt.tenant_by_alias(text), xt.confirmation_tenant(text),
  xt.tenant_by_id(uuid) TO xt_app;

CREATE TABLE xt_meta.org_unit (
  tenant_id uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  id        uuid NOT NULL,
  key       text NOT NULL CHECK (char_length(key) BETWEEN 1 AND 64),
  name      text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
  head      uuid,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, key)
);

CREATE TABLE xt_meta.person (
  tenant_id    uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  id           uuid NOT NULL,
  key          text NOT NULL CHECK (char_length(key) BETWEEN 1 AND 64),
  name         text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
  subject      text CHECK (subject IS NULL OR char_length(subject) BETWEEN 1 AND 255),
  roles        text[] NOT NULL DEFAULT '{}',
  unit         uuid,
  manager      uuid,
  title        text,
  email        text,
  active       boolean NOT NULL DEFAULT true,
  limit_amount numeric,
  PRIMARY KEY (tenant_id, id),
  UNIQUE (tenant_id, key),
  UNIQUE (tenant_id, subject)
);
CREATE INDEX person_roles ON xt_meta.person USING gin (roles);

-- Trần tự chủ người chủ cấp cho từng client agent (0..3 = L0..L3). Không có dòng = L0.
CREATE TABLE xt_meta.agent_grant (
  tenant_id uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  person    uuid NOT NULL,
  client_id text NOT NULL CHECK (char_length(client_id) BETWEEN 1 AND 200),
  autonomy  int NOT NULL CHECK (autonomy BETWEEN 0 AND 3),
  roles     text[] NOT NULL DEFAULT '{agent}',
  PRIMARY KEY (tenant_id, person, client_id),
  FOREIGN KEY (tenant_id, person) REFERENCES xt_meta.person (tenant_id, id) ON DELETE CASCADE
);

CREATE TABLE xt_meta.iam_policy (
  id          uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  tenant_id   uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  revision    int NOT NULL CHECK (revision >= 1),
  document    jsonb NOT NULL,
  digest      text NOT NULL,
  proposed_by text NOT NULL,
  approved_by text NOT NULL CHECK (approved_by <> proposed_by),
  created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, revision)
);

-- Định nghĩa bất biến; instance cũ chạy đúng version đã khởi tạo (sha256 của spec kiểm lúc nạp).
CREATE TABLE xt_meta.process_definition (
  id          uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  tenant_id   uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  process_id  text NOT NULL CHECK (process_id ~ '^[a-z][a-z0-9_]{0,40}$'),
  version     int NOT NULL CHECK (version >= 1),
  spec        jsonb NOT NULL,
  spec_sha256 text NOT NULL,
  allowed_ops jsonb NOT NULL,
  active      boolean NOT NULL DEFAULT false,
  pack        text NOT NULL,
  approved_by text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, process_id, version)
);
CREATE UNIQUE INDEX process_one_active ON xt_meta.process_definition (tenant_id, process_id)
  WHERE active;

-- Model nào là `request` (gắn quy trình + ánh xạ input) hay `task` (ánh xạ trường lõi).
CREATE TABLE xt_meta.model_binding (
  id        uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  tenant_id uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  model     text NOT NULL CHECK (model ~ '^[a-z][a-z0-9_]{0,62}$'),
  primitive text NOT NULL CHECK (primitive IN ('request', 'task')),
  config    jsonb NOT NULL,
  pack      text NOT NULL,
  PRIMARY KEY (tenant_id, model)
);

CREATE TABLE xt_meta.pack_install (
  id          uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  tenant_id   uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  pack        text NOT NULL,
  version     text NOT NULL,
  digest      text NOT NULL,
  proposed_by text NOT NULL,
  approved_by text NOT NULL CHECK (approved_by <> proposed_by),
  installed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, pack)
);

CREATE TABLE xt_meta.notification (
  id         uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  tenant_id  uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  recipient  uuid NOT NULL,
  title      text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 500),
  ref        text,
  dedupe_key text NOT NULL CHECK (char_length(dedupe_key) BETWEEN 1 AND 255),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  read_at    timestamptz,
  UNIQUE (tenant_id, recipient, dedupe_key)
);
CREATE INDEX notification_inbox ON xt_meta.notification (tenant_id, recipient, created_at DESC);

-- Outbox sự kiện của process runtime (engine.error = sự cố, assignment.escalated/unresolved...):
-- ghi cùng transaction với instance; quản trị đọc, relay gửi thông báo sau commit.
CREATE TABLE xt_meta.process_event (
  id          uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  tenant_id   uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  instance_id text NOT NULL,
  type        text NOT NULL CHECK (char_length(type) BETWEEN 1 AND 100),
  payload     jsonb NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX process_event_instance ON xt_meta.process_event (tenant_id, instance_id, created_at);

-- Làn PII: token ổn định theo (tenant, epoch, giá trị); vault cho phép `where` so bằng token.
CREATE TABLE xt_meta.pii_token (
  tenant_id  uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  token      text NOT NULL,
  value      text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, token)
);
CREATE TABLE xt_meta.pii_egress (
  id           uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  tenant_id    uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  trace_id     text NOT NULL,
  tool         text NOT NULL,
  client_class text NOT NULL,
  mode         text NOT NULL CHECK (mode IN ('full', 'masked', 'omitted')),
  fields       text[] NOT NULL,
  at           timestamptz NOT NULL DEFAULT clock_timestamp()
);

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['tenant_profile', 'org_unit', 'person', 'agent_grant', 'iam_policy',
      'process_definition', 'model_binding', 'pack_install', 'notification', 'process_event',
      'pii_token', 'pii_egress'] LOOP
    EXECUTE format('ALTER TABLE xt_meta.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE xt_meta.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON xt_meta.%I TO xt_app USING '
                   '(tenant_id = xt.tenant()) WITH CHECK (tenant_id = xt.tenant())', t);
    EXECUTE format('GRANT SELECT ON xt_meta.%I TO xt_app', t);
  END LOOP;
END $$;
GRANT INSERT ON xt_meta.notification, xt_meta.pii_token, xt_meta.pii_egress,
  xt_meta.process_event TO xt_app;
GRANT UPDATE (read_at) ON xt_meta.notification TO xt_app;

-- Thay đổi cấu hình bảo mật / PII egress: bảng append-only của xt_meta (có created_at, người duyệt).
-- Chưa vào hash-chain: xt_audit.log chỉ nhận bảng model của schema tenant (bootstrap R2-04); cần
-- API audit cho xt_meta từ model engine (docs/integration-notes.md "Tích hợp", mục I-AUD-META).
