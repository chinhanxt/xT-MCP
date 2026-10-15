-- SPDX-License-Identifier: Apache-2.0
-- SPDX-FileCopyrightText: 2026 xT contributors
-- PgConfirmationSessionStore.get khóa hàng (SELECT … FOR UPDATE) để state/phiên chỉ dùng một
-- lần; FOR UPDATE cần quyền UPDATE trên bảng (RLS policy FOR ALL đã giới hạn theo tenant).
GRANT UPDATE ON xt_meta.confirmation_session TO xt_app;
