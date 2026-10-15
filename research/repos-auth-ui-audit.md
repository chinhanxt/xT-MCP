# Repo hữu ích: Auth (Keycloak/MCP) – MCP Apps UI – Telegram – Audit log – Viewer JS

Ngày kiểm chứng: 2026-10-15. Nguồn số liệu: GitHub REST API (`/repos/{owner}/{repo}`; máy không có `gh`, dùng `curl` tương đương). Với repo mà API trả `NOASSERTION`, đã đọc trực tiếp file LICENSE. Chỉ liệt kê repo đã kiểm chứng tồn tại.

Quy ước cột "Cách dùng": **dep** = thêm phụ thuộc, **docker** = chạy container, **ref** = chỉ đọc tham khảo/học pattern (không kéo vào build).

## Bảng repo

| Nhóm | owner/repo | URL | License (đã xác minh) | Sao | Push cuối | Cách dùng | Vì sao hữu ích |
|---|---|---|---|---|---|---|---|
| 1 Keycloak+MCP | PrefectHQ/fastmcp | https://github.com/PrefectHQ/fastmcp | Apache-2.0 | 28077 | 2026-10-14 | dep (Python) | Có sẵn `KeycloakProvider` (`fastmcp/server/auth/providers/keycloak.py`), ví dụ `examples/auth/keycloak_oauth/` (server.py, client.py), doc `docs/integrations/keycloak.mdx`. Remote OAuth + DCR; cần Keycloak >= 26.6. Lựa chọn số 1 cho lõi Python. |
| 1 | modelcontextprotocol/python-sdk | https://github.com/modelcontextprotocol/python-sdk | MIT (đang chuyển dần sang Apache-2.0) | 24577 | 2026-10-13 | dep | SDK chính thức. `examples/servers/simple-auth` tách Authorization Server / Resource Server theo RFC 9728 – mẫu chuẩn để trỏ AS sang Keycloak. |
| 1 | keycloak/keycloak | https://github.com/keycloak/keycloak | Apache-2.0 | 37352 | 2026-10-15 | docker | SSO của Tầng 1. Doc chính thức về MCP: https://www.keycloak.org/securing-apps/mcp-authz-server (không phải repo; liệt kê spec MCP -> RFC cần có; RFC 8707 Resource Indicators còn *experimental*). |
| 1 | keycloak/keycloak-web | https://github.com/keycloak/keycloak-web | Apache-2.0 | 98 | 2026-10-13 | ref | Nguồn website/blog/docs Keycloak (bài MCP nằm ở đây). |
| 1 | keycloak/keycloak-quickstarts | https://github.com/keycloak/keycloak-quickstarts | Apache-2.0 | 2364 | 2026-10-13 | ref | Quickstart chính thức: realm, client, bảo vệ resource server (OIDC). |
| 1 | agentic-community/mcp-gateway-registry | https://github.com/agentic-community/mcp-gateway-registry | Apache-2.0 | 981 | 2026-10-14 | ref (có thể docker) | Gateway + registry MCP doanh nghiệp, hỗ trợ Keycloak; xem cách làm authN/Z tập trung, tool registry. |
| 1 | babs/mcp-auth-proxy | https://github.com/babs/mcp-auth-proxy | Apache-2.0 | 16 | 2026-10-11 | ref / docker | Cầu OAuth 2.1 + OIDC cho MCP server private (stateless, audit). Nhỏ, repo mới -> dùng để học, không phụ thuộc. |
| 1 | drtinkerer/keycloak-mcp-server | https://github.com/drtinkerer/keycloak-mcp-server | Apache-2.0 | 0 | 2026-10-12 | ref | MCP quản trị Keycloak, mỗi Admin API call chạy bằng token của user đăng nhập (per-user). Hay cho pattern token pass-through. 0 sao -> chỉ tham khảo. |
| 2 Remote MCP prod | modelcontextprotocol/inspector | https://github.com/modelcontextprotocol/inspector | MIT -> Apache-2.0 (file LICENSE ghi chuyển giấy phép; API: NOASSERTION) | 11080 | 2026-10-14 | dev tool | Test server Streamable HTTP + luồng OAuth thủ công. Dùng cho agent kiểm thử. |
| 2 | PrefectHQ/fastmcp | (như trên) | Apache-2.0 | 28077 | 2026-10-14 | dep | Streamable HTTP + nhiều provider auth (jwt, introspection, keycloak...) + middleware: gần chuẩn production nhất bằng Python. |
| 2 | openai/openai-apps-sdk-examples | https://github.com/openai/openai-apps-sdk-examples | MIT | 2370 | 2026-04-15 | ref | Có `authenticated_server_python/` (tool theo mức OAuth), `pizzaz_server_python/`, `shopping_cart_python/` (widgetState). Mẫu server Python có auth + widget. |
| 3 MCP Apps | modelcontextprotocol/ext-apps | https://github.com/modelcontextprotocol/ext-apps | MIT (package.json) + Apache-2.0 (code mới); API: NOASSERTION. LICENSE ghi đang chuyển MIT -> Apache-2.0, vẫn đều là OSI | 2958 | 2026-09-25 | dep (JS `@modelcontextprotocol/ext-apps`) + ref | SDK/spec chính thức của MCP Apps: tool khai báo resource `ui://`, host render iframe. Có ví dụ Python: `examples/qr-server`, `examples/say-server` (dùng `uv run`). Nền cho Tầng 2. |
| 3 | MCP-UI-Org/mcp-ui (trước là idosal/mcp-ui, đã chuyển) | https://github.com/MCP-UI-Org/mcp-ui | Apache-2.0 | 5211 | 2026-09-16 | dep (PyPI `mcp-ui-server`, npm client) | Python SDK tạo `UIResource` (`ui://...`, rawHtml/externalUrl/remoteDom). Cách server Python trả UI. |
| 3 | openai/openai-apps-sdk-examples | (như trên) | MIT | 2370 | 2026-04-15 | ref | Widget cho ChatGPT, server Python, cách build asset HTML bằng Vite. |
| 3 | openai/apps-sdk-ui | https://github.com/openai/apps-sdk-ui | MIT | 961 | 2026-05-20 | dep (tuỳ chọn) | Bộ component UI cho widget ChatGPT. |
| 4 Telegram | aiogram/aiogram | https://github.com/aiogram/aiogram | MIT | 5891 | 2026-10-11 | dep (Python, async) | **Chọn cái này.** MIT hợp Apache-2.0. Có `CallbackData` factory, FSM, webhook; examples (`finite_state_machine.py`, `echo_bot_webhook.py`, `scene.py`). Nút Duyệt/Từ chối = InlineKeyboard + callback_data. |
| 4 | python-telegram-bot/python-telegram-bot | https://github.com/python-telegram-bot/python-telegram-bot | **GPL-3.0** (API và LICENSE đều là GPLv3) | 29516 | 2026-10-11 | ref only | **Bẫy giấy phép**: GPL copyleft, không nên làm phụ thuộc của dự án Apache-2.0. Chỉ đọc `examples/inlinekeyboard.py`, `arbitrarycallbackdatabot.py` để học pattern. |
| 5 Audit log | pgaudit/pgaudit | https://github.com/pgaudit/pgaudit | PostgreSQL License (OSI; API: NOASSERTION, đã đọc LICENSE) | 1716 | 2026-09-26 | dep (extension, docker image Postgres tự build) | Audit cấp session/object vào log Postgres. Bổ sung, KHÔNG chống sửa (log nằm ngoài DB). |
| 5 | JooyoungOfficielEpitech/pg-tamper-evident-audit | https://github.com/JooyoungOfficielEpitech/pg-tamper-evident-audit | MIT | 0 | 2026-09-29 | ref (~150 dòng SQL, copy theo MIT) | Đúng nhu cầu: bảng append-only + trigger WORM (chặn UPDATE/DELETE/TRUNCATE) + chuỗi SHA-256 (`prev_hash || canonical(row)`) + `pg_advisory_xact_lock` + `audit.verify()`. 0 sao -> tự review kỹ trước khi dùng. |
| 5 | philtyp/sovereign-audit-log | https://github.com/philtyp/sovereign-audit-log | MIT | 0 | 2026-07-23 | ref | Audit log tamper-evident theo tenant với RLS ép buộc – khớp mô hình Postgres + RLS của xT. 0 sao -> tham khảo. |
| 5 | pgMemento/pgMemento | https://github.com/pgMemento/pgMemento | LGPL-3.0 | 413 | 2026-04-27 | ref | Audit trail theo transaction + schema versioning, có khôi phục. LGPL: dùng làm extension SQL tách biệt thì ổn nhưng nên kiểm với PoF; ưu tiên chỉ tham khảo. |
| 5 | supabase/supa_audit | https://github.com/supabase/supa_audit | Apache-2.0 | 670 | 2024-01-03 | ref | **Đã archived**. Chỉ tham khảo mẫu trigger `record_version` -> jsonb. |
| 5 Thay immudb | sigstore/rekor | https://github.com/sigstore/rekor | Apache-2.0 | 1218 | 2026-10-13 | docker / ref | Transparency log (Merkle tree) cho phần chứng minh bao hàm. Nặng hơn nhu cầu. |
| 5 | google/trillian | https://github.com/google/trillian | Apache-2.0 | 3759 | 2026-10-02 | docker / ref | Verifiable log/map (Merkle) – thay thế OSI gần immudb nhất về ý tưởng. Cần MySQL/Postgres storage. |
| 5 | transparency-dev/tessera | https://github.com/transparency-dev/tessera | Apache-2.0 | 252 | 2026-10-14 | ref (Go lib) | Kế nhiệm Trillian (tile-based log). Go -> chỉ dùng nếu chấp nhận thêm service Go. |
| 5 | codenotary/immudb | https://github.com/codenotary/immudb | **BSL (MariaDB BUSL)** – LICENSE: "Business Source License", Licensor Codenotary, từ bản 1.9DOM.2 | 9043 | 2026-10-05 | KHÔNG dùng | **Bẫy**: bị CLAUDE.md cấm (BSL). |
| 6 Viewer JS | bpmn-io/bpmn-js | https://github.com/bpmn-io/bpmn-js | MIT **kèm điều khoản watermark** (API: NOASSERTION; đã đọc LICENSE): logo bpmn.io PHẢI hiển thị, không được gỡ/che | 9694 | 2026-10-14 | dep (npm) có điều kiện | **Bẫy**: không phải MIT thuần/OSI chuẩn; ép giữ watermark liên kết bpmn.io trong UI. Kiểm tra OSI: điều khoản bổ sung làm giấy phép không còn là MIT nguyên bản -> rủi ro với tiêu chí "chỉ OSI". Trong MCP App dùng được nếu chấp nhận watermark; khuyến nghị tránh, tự vẽ BPMN nhẹ (xem dưới). |
| 6 | bpmn-io/dmn-js | https://github.com/bpmn-io/dmn-js | Cùng LICENSE kiểu bpmn.io (watermark) | 367 | 2026-10-14 | tránh | Cùng bẫy với bpmn-js. |
| 6 | bpmn-io/bpmn-js-examples | https://github.com/bpmn-io/bpmn-js-examples | **Không có LICENSE** (API: None; raw LICENSE 404) | 2050 | 2026-10-05 | ref (đọc, không copy code) | Không có giấy phép = mặc định bảo lưu mọi quyền; không đưa code vào repo. |
| 6 | bumbeishvili/org-chart (d3-org-chart, tên cũ đã đổi) | https://github.com/bumbeishvili/org-chart | MIT | 1215 | 2026-07-08 | dep (npm `d3-org-chart` + d3) | Org chart D3 tuỳ biến cao, expand/collapse, zoom, export; chạy được trong iframe MCP App (bundle 1 file). Chọn số 1 cho sơ đồ tổ chức. |
| 6 | d3/d3 | https://github.com/d3/d3 | ISC | 113752 | 2026-05-28 | dep | Nền cho d3-org-chart và d3-hierarchy tự vẽ. |
| 6 | dabeng/OrgChart | https://github.com/dabeng/OrgChart | MIT | 3043 | 2026-10-15 | dep | Org chart thuần JS, không cần D3; dự phòng nhẹ. |
| 6 | antvis/G6 | https://github.com/antvis/G6 | MIT | 12331 | 2026-10-14 | dep | Thư viện đồ thị tổng quát (tree, flow) – có thể vẽ BPMN đơn giản/flow thay bpmn-js. |
| 6 | NorthwoodsSoftware/GoJS | https://github.com/NorthwoodsSoftware/GoJS | NOASSERTION (thương mại, cần license trả phí) | 8490 | 2026-10-02 | KHÔNG dùng | **Bẫy**: không OSI. (Không tìm thấy file LICENSE ở nhánh master để đọc; lấy theo trạng thái GitHub API và mô tả công khai là thư viện thương mại, cần xác minh nếu cân nhắc.) |

## Bẫy giấy phép (tổng hợp)

1. **codenotary/immudb**: BSL -> loại. Thay bằng hash-chain trong Postgres (pg-tamper-evident-audit) + neo checkpoint định kỳ; nếu cần transparency log thật thì Trillian/Tessera/Rekor (Apache-2.0).
2. **python-telegram-bot**: GPL-3.0 -> chỉ tham khảo; dùng **aiogram** (MIT).
3. **bpmn-io/bpmn-js, dmn-js**: MIT + điều khoản watermark bắt buộc -> không phải OSI thuần; tránh hoặc chấp nhận hiển thị watermark. Thay thế: G6/d3 tự vẽ.
4. **bpmn-io/bpmn-js-examples**: không có LICENSE -> không copy.
5. **GoJS**: thương mại.
6. **pgMemento**: LGPL-3.0 -> ổn nếu chỉ dùng như extension tách rời, nhưng kiểm lại; **supa_audit** đã archived.
7. **modelcontextprotocol/\***: đang chuyển MIT -> Apache-2.0 (LICENSE mô tả hỗn hợp); cả hai đều OSI, nhưng PoF cần ghi chú cả hai nếu vendor code.
8. Repo 0-1 sao (pg-tamper-evident-audit, sovereign-audit-log, drtinkerer/keycloak-mcp-server): giấy phép OK nhưng chưa được cộng đồng thẩm định -> học pattern, tự viết lại.

## Tài liệu Keycloak/MCP (không phải repo, đã xuất hiện qua tìm kiếm web, chưa mở từng trang)

- https://www.keycloak.org/securing-apps/mcp-authz-server – doc chính thức Keycloak về MCP.
- https://aaif.io/blog/using-keycloak-as-an-mcp-authorization-server – blog AAIF: Keycloak 26.7 chưa hỗ trợ RFC 8707, dự kiến thử nghiệm ở 26.8.
- https://kane.mx/posts/2025/deploy-keycloak-aws-mcp-oauth/ – workaround audience bằng protocol mapper (hardcoded `aud`).
- https://www.redhat.com/en/blog/mcp-security-implementing-robust-authentication-and-authorization – đăng ký MCP server làm confidential client.

## Không tìm được

- Ví dụ mã nguồn "Telegram bot phê duyệt bằng inline button" chất lượng cao, giấy phép OSI: các repo tìm ra đều 0-1 sao và không có license -> không liệt kê. Tự dựng bằng aiogram `CallbackData` + FSM theo examples của aiogram.
- Rate limit API GitHub ẩn danh (60 req/giờ) khiến không kiểm được `stephaneberle9/authsome-mcp-proxy` và `apache/age` trực tiếp; đã loại khỏi bảng.
