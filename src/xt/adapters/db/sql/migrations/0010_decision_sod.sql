-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- Chạy bằng xt_migrator (xt db migrate). W9 / T1-10: tách nhiệm vụ (SoD) ở MỨC DB, lớp phòng thủ
-- dưới IAM/PEP và process runtime (docs/decisions.md "SoD ở DB: sổ quyết định").
--
-- `xt_meta.decision`: mỗi quyết định của người (bước duyệt của đề nghị, đề xuất thay đổi model) một
-- dòng, ghi CÙNG transaction với quyết định (decide_request sau /desk/confirm). Chỉ ghi thêm.
-- Trigger `decision_sod_guard` từ chối (kể cả INSERT SQL trực tiếp bằng xt_app):
--   (1) người duyệt = người đề nghị;
--   (2) người duyệt không phải người thật đã tự xác nhận: `subject` (Keycloak) của người duyệt phải
--       nằm trong `confirmed_by` (danh tính người đã step-up trên trang xác nhận). Agent (kể cả
--       khi thay mặt chính người duyệt) không bao giờ là principal xác nhận;
--   (3) người đề nghị nằm trong danh sách xác nhận.
-- Idempotent: chạy lại trên CSDL đã có bảng/trigger không lỗi, không đổi dữ liệu.

CREATE TABLE IF NOT EXISTS xt_meta.decision (
  id           uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  tenant_id    uuid NOT NULL REFERENCES xt_meta.tenant(id) ON DELETE CASCADE,
  kind         text NOT NULL CHECK (kind IN ('request', 'model_change')),
  ref          text NOT NULL CHECK (char_length(ref) BETWEEN 1 AND 300),
  instance_id  text CHECK (char_length(instance_id) <= 100),
  task_id      text CHECK (char_length(task_id) <= 100),
  step         text NOT NULL CHECK (char_length(step) BETWEEN 1 AND 100),
  decision     text NOT NULL CHECK (char_length(decision) BETWEEN 1 AND 40),
  approver     uuid NOT NULL,
  requester    uuid,
  actor_kind   text NOT NULL CHECK (actor_kind IN ('human', 'agent', 'service')),
  actor_client text NOT NULL CHECK (char_length(actor_client) BETWEEN 1 AND 200),
  confirmed_by text[] NOT NULL CHECK (cardinality(confirmed_by) >= 1),
  decided_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
  -- Đề nghị luôn có người đề nghị; đề xuất model của tài khoản dịch vụ có thể không gắn người.
  CONSTRAINT decision_requester_known CHECK (kind = 'model_change' OR requester IS NOT NULL),
  CONSTRAINT decision_sod_not_requester CHECK (approver IS DISTINCT FROM requester),
  FOREIGN KEY (tenant_id, approver) REFERENCES xt_meta.person (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS decision_ref ON xt_meta.decision (tenant_id, ref, decided_at);

CREATE OR REPLACE FUNCTION xt_meta.decision_sod_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp AS $$
DECLARE approver_sub text; requester_sub text;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'xt_meta.decision chỉ ghi thêm (% bị cấm)', TG_OP
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.requester IS NOT NULL AND NEW.approver = NEW.requester THEN
    RAISE EXCEPTION 'SoD: người duyệt trùng người đề nghị (%)', NEW.ref
      USING ERRCODE = 'check_violation', CONSTRAINT = 'decision_sod_not_requester';
  END IF;
  SELECT p.subject INTO approver_sub FROM xt_meta.person p
   WHERE p.tenant_id = NEW.tenant_id AND p.id = NEW.approver AND p.active;
  IF approver_sub IS NULL THEN
    RAISE EXCEPTION 'SoD: người duyệt % không phải người đang hoạt động có tài khoản đăng nhập',
      NEW.approver USING ERRCODE = 'check_violation', CONSTRAINT = 'decision_sod_human_approver';
  END IF;
  IF NOT approver_sub = ANY (NEW.confirmed_by) THEN
    RAISE EXCEPTION 'SoD: quyết định phải do chính người duyệt (principal người) xác nhận'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'decision_sod_human_approver';
  END IF;
  IF NEW.requester IS NOT NULL THEN
    SELECT p.subject INTO requester_sub FROM xt_meta.person p
     WHERE p.tenant_id = NEW.tenant_id AND p.id = NEW.requester;
    IF requester_sub IS NOT NULL AND requester_sub = ANY (NEW.confirmed_by) THEN
      RAISE EXCEPTION 'SoD: người đề nghị không được là người xác nhận quyết định (%)', NEW.ref
        USING ERRCODE = 'check_violation', CONSTRAINT = 'decision_sod_not_requester';
    END IF;
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION xt_meta.decision_sod_guard() FROM PUBLIC;

-- CREATE OR REPLACE (không DROP): trigger trên xt_meta không được xóa bởi role thường (R4-01)
CREATE OR REPLACE TRIGGER decision_sod_guard BEFORE INSERT OR UPDATE OR DELETE ON xt_meta.decision
  FOR EACH ROW EXECUTE FUNCTION xt_meta.decision_sod_guard();

ALTER TABLE xt_meta.decision ENABLE ROW LEVEL SECURITY;
ALTER TABLE xt_meta.decision FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'xt_meta' AND tablename = 'decision'
                 AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON xt_meta.decision TO xt_app
      USING (tenant_id = xt.tenant()) WITH CHECK (tenant_id = xt.tenant());
  END IF;
END $$;
GRANT SELECT, INSERT ON xt_meta.decision TO xt_app;

-- Hash-chain audit của tenant (I-AUD-META) như các bảng xt_meta khác.
DO $$ BEGIN
  IF to_regprocedure('xt_audit.install_meta(name, name, text[])') IS NULL THEN
    RAISE EXCEPTION 'thiếu xt_audit.install_meta: chạy xt.adapters.db.tenancy.install_meta_audit()'
      ' bằng superuser rồi migrate lại';
  END IF;
  GRANT TRIGGER ON xt_meta.decision TO xt_audit_owner;
  PERFORM xt_audit.install_meta('decision', 'id', '{}');
END $$;
