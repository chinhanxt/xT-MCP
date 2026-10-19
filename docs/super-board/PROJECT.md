<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 xT contributors -->

# xT — PROJECT

Nền tảng doanh nghiệp nguồn mở (OLP PMNM 2026): 3 tầng, 7 lớp, 4 không gian H/P/D/I.

| Phần | Đường dẫn | Stack |
|---|---|---|
| Lõi Tầng 1 (SSO, gateway, data, workflow, MCP) | `src/xt`, `deploy/` | Python, PostgreSQL/RLS, Keycloak, APISIX |
| Gói nghiệp vụ kho | `packages/kho-hang` | Model Engine pack |
| Cổng H/P/D/I + Desk | `desk/` | React/Vite, OIDC + DPoP |
| Studio (AI: chat/workflow/RAG/agent) | `imports/dify-rea` | Node, SQLite, phục vụ tại `/studio/` |
| WareTrack 3D | `imports/waretrack-3d` | Vite/Three.js, phục vụ tại `/waretrack/` |

- Kế hoạch: `docs/archive/2026-10-17-unified-xt-studio-waretrack.md` · nhật ký: `docs/archive/unified-execution.md`.
- Chạy local: `docs/unified-local.md` (`make unified-setup|build|test|start`).
- Kiểm chứng nhẹ: `uv run ruff check src tests && uv run pytest -q --no-cov`; integration (`-m integration`) là stack nặng — mỗi lúc chỉ 1.
