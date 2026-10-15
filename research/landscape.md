# Bản đồ cạnh tranh và tiền lệ (prior art) cho xT — tính đến 15/10/2026

Phương pháp: WebSearch + kiểm tra trực tiếp API GitHub / trang chủ. Mục đánh dấu **[chưa xác minh]** nghĩa là chỉ có nguồn thứ cấp hoặc chưa đọc được trang gốc. Giấy phép lấy từ GitHub API/LICENSE ngày 15/10/2026 khi có ghi "(đã kiểm)".

---

## 1. Phần mềm doanh nghiệp thương mại "agent-first / MCP-first"

| Sản phẩm | Làm gì | Giấy phép | xT khác/tốt/kém thế nào |
|---|---|---|---|
| **Kite** (kite.vibery.app) — https://kite.vibery.app | Trang chủ (đã đọc): "vận hành trung tâm đào tạo bằng AI"; nhắn Claude/ChatGPT để tạo khóa, mã giảm giá, mở bán, SePay tự đối soát; mô hình "mua một lần, dữ liệu của bạn". Agent tạo bản **nháp**, người dùng bấm "Mở bán (bạn chọn)". Là sản phẩm Việt gần nhất về triết lý. | Đóng (thương mại, mua đứt) | Kite là **vertical** (đào tạo), nghiệp vụ cố định trong code. xT đa ngành, nghiệp vụ là dữ liệu. Kite đã có mẫu "agent đề xuất, người duyệt" nhưng chưa thấy hash-chain audit/RLS đa tenant/BPMN công khai. Kite **tốt hơn** ở độ hoàn thiện sản phẩm và tích hợp thanh toán VN (SePay). |
| **Attio** — https://www.folk.app/articles/attio-mcp , https://truto.one/blog/best-mcp-server-for-attio-in-2026.md | CRM với hosted MCP (OAuth, mọi gói): tìm/tạo/sửa record, note, task. Custom objects linh hoạt. | Đóng | Attio cho phép agent sửa **dữ liệu**, nhưng không có luồng "agent thiết kế model → người duyệt → áp dụng" như một khái niệm hạng nhất. Một bài so sánh (từ đối thủ folk) nêu rủi ro ghi quyền cần quản trị thủ công. |
| **Lark/Feishu** — https://glama.ai/mcp/servers/larksuite/lark-openapi-mcp | MCP chính thức `@larksuiteoapi/lark-mcp` (beta): bọc OpenAPI thành tool (docs, tin nhắn, lịch). Phê duyệt (Approval) là sản phẩm có sẵn của Lark. | MCP: mã nguồn mở trên GitHub **[license chưa kiểm]**; Lark: đóng | Cách tiếp cận là "bọc API sẵn có" (hàng trăm tool), ngược với bộ ~15 tool cố định của xT. Lark mạnh hơn hẳn về hệ sinh thái; xT mạnh ở self-host/dữ liệu chủ quyền. |
| **Notion Custom Agents** — https://alternativeto.net/news/2026/2/notion-3-3-launches-custom-agents-for-autonomous-team-automation | 02/2026: agent chạy theo trigger/lịch, nối Slack/Figma/Linear/HubSpot qua MCP; có audit log và kiểm soát admin; tính credit. Khoảng 21.000 agent do tester tạo (số liệu hãng). | Đóng, SaaS | Notion = agent trên workspace tri thức, không phải hệ ghi nhận nghiệp vụ có RLS/BPMN. Không self-host. |
| **Odoo** — https://apps.odoo.com/apps/modules/19.0/mcp_odoo_pro , https://glama.ai/mcp/servers/bemade/odoo-mcp | Không thấy MCP chính thức trong kết quả; hệ sinh thái bên thứ ba: Odoo MCP Pro (~$59, OAuth2.1/PKCE, audit log), Odoo AI Agent (~$78), ECOSIRE (từ $499, có approval cho bút toán), bemade/odoo-mcp (mã mở). | Odoo Community: LGPL-3.0 (GitHub API trả NOASSERTION; xác nhận theo LICENSE của repo — Enterprise đóng) | Các server này bọc ORM generic (search/create/call_method) — quyền lực rộng, tập tool lớn. xT: tool cố định + phê duyệt là mặc định. Odoo hơn xT cực xa về module nghiệp vụ (kế toán, kho...). |
| **Salesforce Agentforce / Headless 360** — https://developer.salesforce.com/blogs/2025/10/salesforce-hosted-mcp-servers-are-in-beta-today , https://www.salesforce.com/headless/ | Hosted MCP server GA 04/2026 (record, Flow, Apex, Data 360); Agentforce gọi MCP bên ngoài (Summer '26); "Headless 360": 60+ tool MCP, 30+ skill; tự gọi là "System of Agency". Có registry MCP có quản trị. | Đóng | Cùng hướng "headless + MCP" nhưng ở quy mô enterprise, giá cao, khóa nhà cung cấp. xT: mở, nhẹ, chạy local LLM. Không thể cạnh tranh về độ rộng. |
| **Microsoft Dynamics 365 / Dataverse MCP** — https://cloudwars.com/ai/microsoft-unleashes-dynamic-ai-capabilities-in-dynamics-365-erp-mcp-server-empowering-partners-and-custom-agents/ , https://www.ttecdigital.com/articles/guide-microsoft-mcp-servers-for-dynamics-365-msft | Dataverse MCP GA sau Ignite 2025; ERP MCP ban đầu 13 tool, bản "dynamic" (public preview) mở "hàng trăm nghìn hàm ERP"; MCP riêng cho analytics. Định vị "system of record → system of action". | Đóng | Tương tự Salesforce. Điểm đáng chú ý: Microsoft đi từ tập tool cố định (13) sang tool động — xác nhận cả hai hướng đều có thị trường; xT chọn hướng cố định + dữ liệu hóa. |
| **Base.vn (Base 2.0) / 1Office** — https://ecosistemastartup.com/base-vn-lanza-base-2-0-la-ia-como-nueva-fuerza-laboral/ , https://doanhnhan.baophapluat.vn/base-vn-ra-mat-base-2-0-huong-toi-mo-hinh-doanh-nghiep-noi-ai-tro-thanh-luc-luong-lao-dong-moi.md , https://vcci.com.vn/news/ai-dang-tro-thanh-luc-luong-lao-dong-moi-trong-doanh-nghiep | Base 2.0 (Enterprise AI Summit 2026, TP.HCM): kiến trúc viết lại từ đầu để người và AI agent làm chung dữ liệu/quy trình; "AI là lực lượng lao động mới". Phát triển ~2 năm. **1Office: không tìm thấy thông tin tính năng AI công khai [chưa xác minh].** | Đóng | Đối thủ Việt trực diện nhất về thông điệp. Chưa thấy công khai MCP mở, self-host, hay audit hash-chain. Base mạnh ở khách hàng và bộ module HRM/workflow. Điểm xT có thể nhấn: mở nguồn, MCP chuẩn, dùng được với bất kỳ agent. |
| **Zapier MCP** — https://docs.zapier.com/mcp/get-started/quickstart.md | 9.000+ app, 40.000+ action, OAuth, History tab ghi log. Make MCP: **[chưa xác minh]**. | Đóng | Là lớp "tay chân" để agent gọi app khác, không phải hệ ghi nhận nghiệp vụ. Bổ trợ chứ không đối đầu. |
| **Airtable** — https://airtable.com/developers/agents/mcp/getting-started , https://www.airtable.com/articles/mcp-server-interfaces-automations | MCP server chính thức (hosted, mọi gói): hỏi dữ liệu, sửa record, tạo automation; thư viện skills + MCP để mô hình dựng workspace/interface/automation từ mô tả. | Đóng | **Gần nhất với ý "LLM thiết kế app"** nhưng ở dạng thương mại, không có bước phê duyệt dạng phiên bản dữ liệu, không RLS đa tenant do khách kiểm soát. |
| **Baserow** — https://baserow.io/user-docs/mcp-server | MCP nhúng sẵn: endpoint theo workspace, đọc/ghi bảng; có AI assistant. | Lõi MIT **[theo hiểu biết chung, chưa kiểm trong phiên này]**; phần premium/enterprise đóng | Là CSDL no-code + MCP, không có workflow/phê duyệt/audit chuỗi băm. |

---

## 2. Mã nguồn mở: "LLM thiết kế schema/app" hoặc "MCP trên backend doanh nghiệp"

Giấy phép dưới đây đã kiểm qua GitHub API/LICENSE ngày 15/10/2026 trừ khi ghi khác.

| Dự án | Làm gì | Giấy phép (đã kiểm) | Ghi chú cho xT |
|---|---|---|---|
| **Supabase MCP** — https://github.com/supabase-community/supabase-mcp (repo đã chuyển hướng, API trả "Moved") | Server MCP chính thức: liệt kê dự án, xem schema, chạy SQL, migration. Dùng PAT. | **Chưa kiểm được** (API redirect) — thường là Apache-2.0, cần mở LICENSE để chắc | Mô hình "agent viết SQL/migration trực tiếp" — quyền lực lớn, không có lớp duyệt nghiệp vụ. xT là đối cực: agent chỉ đề xuất model dạng dữ liệu. |
| **Directus** — https://github.com/directus/directus (LICENSE) | Có MCP tích hợp trong lõi (tôn trọng permission hiện có). | **Monospace Sustainable Core License (MSCL-1.0-GPL)** — source-available, KHÔNG phải OSI; chuyển GPL-3.0 sau 4 năm | **Loại khỏi danh sách phụ thuộc** theo quy tắc "chỉ OSI". |
| **NocoDB** — https://github.com/nocodb/nocodb (LICENSE.md, cập nhật 29/01/2026) | No-code trên SQL; MCP chủ yếu do cộng đồng. | **"Sustainable Use License" cho nhánh master** — không OSI (đổi từ AGPL) | **Loại.** Lưu ý: trước đây là AGPL, nay đã đổi — tín hiệu rủi ro license-drift. |
| **Twenty CRM** — https://github.com/twentyhq/twenty , https://interoperable-europe.ec.europa.eu/collection/egovernment/solution/twenty | CRM mã mở, ~58k sao; có MCP gốc theo một trang review (chưa thấy tài liệu chính thức **[chưa xác minh]**). | **AGPL-3.0** có ngoại lệ, một số gói MIT; thư mục enterprise (SSO, row-level perms) thương mại | AGPL là rủi ro lây nhiễm nếu nhúng; chỉ tham khảo ý tưởng (custom objects là dữ liệu). |
| **Appsmith** — https://github.com/appsmithorg/appsmith | Low-code internal tools, có AI. | **Apache-2.0** | Dùng được về license; là UI builder, không phải backend nghiệp vụ. |
| **Budibase** — https://github.com/Budibase/budibase | Low-code + AI agent. | **GPL-3.0 tổng thể** (từng gói có license riêng) | Copyleft mạnh; tránh nhúng. |
| **ToolJet** — https://github.com/ToolJet/ToolJet | Low-code, "AI-native", agent/workflow. | **AGPL-3.0** | Tránh nhúng. |
| **Frappe / ERPNext** — https://github.com/frappe/frappe , https://github.com/frappe/erpnext | Framework metadata-driven (DocType là dữ liệu — rất gần ý "nghiệp vụ là dữ liệu"). MCP: Frappe Assistant Core (AGPL-3.0, 24 tool, OAuth2 như user thật, https://glama.ai/mcp/servers/smgvxho1nm), erpnext-agent (MIT, PyPI). | Frappe **MIT**; ERPNext **GPL-3.0**; Assistant Core **AGPL-3.0** | **Prior art quan trọng nhất về "model là dữ liệu"** (DocType + workflow + permission). Frappe MIT dùng được làm tham khảo/nền, nhưng nặng và gắn MariaDB; không có RLS Postgres. |
| **Odoo** — https://github.com/odoo/odoo | ERP đầy đủ; metadata-driven (ir.model) | Community **LGPL-3.0** (API trả NOASSERTION; Enterprise đóng) | Ý tưởng tương tự Frappe. |
| **crmkit** — https://github.com/crmkit/crmkit , https://chatbotkit.com/changelog/introducing-crmkit | CRM "agent-first": không UI/SDK, API HTTP dạng text, MCP connector, 1 binary Go, SQLite/Postgres; repo `skills` dạy agent vận hành. 0.x, 43 sao (08/2026). | **MIT** | **Gần triết lý nhất ở mã mở** (agent là giao diện, skills đi kèm). Nhưng chỉ CRM, đơn tenant, không phê duyệt/audit/BPMN. |
| **Moqui MCP** — https://github.com/schue/moqui-mcp | Nối agent vào ERP Moqui (duyệt màn hình, tạo đơn/hóa đơn). 8 sao. | **Không có LICENSE** (API: None) | Chỉ học ý; không dùng. |
| **AnythingMCP** — https://anythingmcp.com/ | Gateway biến REST/SOAP/GraphQL/SQL thành tool MCP. | AGPL-3.0 (theo trang) **[chưa kiểm repo]** | Middleware, không phải ERP. |
| **Frihet** — https://www.frihet.io/da/erp-ia-nativa-vs-tradicional | ERP "AI-native", tự nhận MIT, `@frihet/mcp-server`. | MIT (tự khai, **chưa kiểm**) | Tài liệu mang tính quảng cáo; theo dõi. |

**Kết luận mục 2:** Không tìm thấy dự án mã mở OSI nào kết hợp đồng thời: bộ tool MCP cố định + model/process do agent đề xuất và người duyệt + Postgres RLS đa tenant + BPMN + audit chuỗi băm. Phần "LLM thiết kế schema" hiện chỉ có dạng "agent chạy DDL/SQL" (Supabase MCP) hoặc "agent sửa metadata" (Airtable thương mại, Frappe qua MCP bên thứ ba). Cảnh báo: tìm kiếm không phải là bằng chứng "không tồn tại"; GitHub còn nhiều repo nhỏ chưa lập chỉ mục.

---

## 3. Khung học thuật/công nghiệp, MCP gateway, chuẩn phê duyệt/định danh agent

| Hạng mục | Nội dung | Nguồn | Liên hệ xT |
|---|---|---|---|
| **Kong MCP Gateway/Registry** | 10/2025: MCP gateway trong AI Gateway (sinh MCP server từ API Kong, xác thực tập trung, quan sát). 02/02/2026: MCP Registry (tech preview) làm "system of record" cho tool được duyệt. Thương mại (Kong Gateway lõi Apache-2.0, tính năng AI/MCP enterprise là sản phẩm Konnect **[chưa kiểm chi tiết license]**). | https://www.konghq.com/company/press-room/press-release/kong-introduces-mcp-registry , https://konghq.com/blog/product-releases/enterprise-mcp-gateway | Kong đứng **trước** backend; xT là backend. xT dùng APISIX (Apache-2.0) ở vai trò tương tự. |
| **Google Apigee MCP + Cloud API Registry** | 10/12/2025: endpoint MCP được quản lý, lấy tool từ OpenAPI, OAuth 2.1/OIDC, quota, đăng ký trong API hub; không hỗ trợ Apigee hybrid. | https://cloud.google.com/blog/products/ai-machine-learning/mcp-support-for-apigee , https://docs.cloud.google.com/apigee/docs/api-platform/apigee-mcp/apigee-mcp-overview | Cách "mỗi API operation = 1 tool" — ngược với tool cố định của xT. |
| **agentgateway** (mã mở) | Gateway làm resource server OAuth cho MCP, xác thực lúc kết nối. | https://agentgateway.dev/docs/standalone/latest/tutorials/mcp-authentication/ | Ứng viên thay/bổ sung APISIX; **license chưa kiểm**. |
| **MCP Authorization (OAuth 2.1)** | Spec MCP khuyến nghị OAuth 2.1; Auth0/Gravitee/Keycloak triển khai. Nhận xét chung: OAuth xử lý định danh, quyết định runtime theo tool/resource/purpose là việc riêng. | https://auth0.com/ai/docs/mcp , https://gravitee.io/auth-guide.html | xT dùng Keycloak; chính sách theo từng tool là điểm nên nhấn. |
| **OpenID AuthZEN 1.0** | Final Specification, phê duyệt 12/01/2026 (81 thuận, 1 chống, 25 bỏ phiếu trắng). Mô hình Subject/Resource/Action/Context → Decision giữa PEP và PDP. | https://openid.net/authorization-api-1-0-final-specification-approved/ | **Nên áp dụng**: xT làm PEP gọi PDP qua AuthZEN thay vì API tự chế. Kết quả tìm không đề cập agent cụ thể. |
| **CIBA + RAR (Auth0 Async Authorization)** | Agent gọi `/bc-authorize` kèm Rich Authorization Request, người dùng duyệt trên thiết bị riêng, agent poll `/token`. Auth0 lập luận gõ "đã duyệt" trong chat không có bằng chứng mật mã. | https://auth0.com/ai/docs/intro-asynchronous-authorization , https://auth0.com/blog/building-secure-ai-agents-microsoft-agent-framework-auth0-part-4-human-in-the-loop-approval | **Tiền lệ trực tiếp** cho bước "người duyệt". Keycloak hỗ trợ CIBA **[cần kiểm bản hiện hành]**. |
| **IETF: OAuth 2.0 On-Behalf-Of User Authorization for AI Agents** | Draft cá nhân (WSO2), -02 tháng 8/2025; thêm `requested_actor`/`actor_token`; claim ghi chuỗi ủy quyền user → agent → client. Theo WorkOS (04/2026) chưa được OAuth WG chấp nhận. | https://datatracker.ietf.org/doc/html/draft-oauth-ai-agents-on-behalf-of-user-02 , https://workos.com/blog/oauth-on-behalf-of-ai-agents | Mô hình claim `act` (RFC 8693) cho audit "ai làm thay ai". Trạng thái mới nhất **[chưa xác minh]**. |
| **Khung "agentic enterprise / headless"** | Microsoft: "autonomous enterprise", system of record → system of action. Salesforce: Headless 360, "System of Agency". Phản biện thực hành: headless đưa agent vào cửa nhưng không trả lời lý luận chạy ở đâu / trải nghiệm ở đâu. **Không tìm được báo cáo McKinsey/Gartner cụ thể [chưa xác minh].** | https://www.salesforce.com/headless/ , https://microsoft.com/en-us/dynamics-365/blog/business-leader/2025/05/20/the-autonomous-enterprise-how-generative-ai-is-reshaping-business-applications | Thuật ngữ "headless" đã bị Salesforce chiếm trong marketing; xT cần khác biệt ở "mở + dữ liệu hóa nghiệp vụ". |

---

## 4. Tổng hợp

### (a) Đóng góp thực sự độc đáo của xT — và phần KHÔNG độc đáo

**Không độc đáo (đừng nhận là của mình):**
- Bọc backend bằng MCP (Salesforce, Microsoft, Attio, Airtable, Odoo, Frappe, Baserow... đều có).
- Hosted/remote MCP với OAuth 2.1; Keycloak, APISIX, Postgres RLS, SpiffWorkflow, hash-chain log — từng khối đều là kỹ thuật đã biết.
- Agent soạn nháp, người bấm duyệt (Kite, ECOSIRE, Auth0 CIBA đã làm).
- Metadata-driven "model là dữ liệu" (Frappe DocType, Odoo ir.model, Airtable, Twenty custom objects đã có nhiều năm).
- Triết lý "agent là giao diện" (crmkit, Kite, Base 2.0).
- MCP Apps (UI trong chat) là spec của MCP, không phải sáng kiến của xT.

**Có tính độc đáo (ở dạng tổ hợp, cần chứng minh bằng demo/test):**
1. **Bộ tool cố định ~15 + toàn bộ nghiệp vụ là dữ liệu có phiên bản, do agent đề xuất và người duyệt trước khi áp dụng** — phần lớn đối thủ chọn một trong hai cực: tool sinh theo từng API/model (Apigee, Odoo MCP, Dynamics dynamic) hoặc agent chạy SQL/DDL thẳng (Supabase MCP). Chưa thấy ai công khai cả "tool cố định" lẫn "model/process-as-reviewed-data" trong mã nguồn mở OSI.
2. **Tổ hợp mã nguồn mở OSI hoàn chỉnh**: đa tenant bằng Postgres RLS + Keycloak + BPMN + audit hash-chain + PII lane trong cùng một lõi headless Apache-2.0. Đối thủ mã mở hoặc là CRM đơn tenant (crmkit), hoặc license copyleft/source-available (Twenty, NocoDB, Directus, Budibase, ToolJet).
3. **PII lane** (tách đường dữ liệu cá nhân khỏi LLM) và **Desk mỏng với LLM local**: không thấy đối thủ nêu đặc điểm này trong các nguồn đã đọc; nhưng không phải bằng chứng là không có.
4. Cách duyệt gắn chặt với **audit không thể sửa** (mỗi thay đổi model/process có bản ghi chuỗi băm) — các đối thủ có audit log nhưng chưa thấy khẳng định chuỗi băm.

Lưu ý trung thực: (1) và (2) là tổ hợp thiết kế, không phải phát minh; giá trị phụ thuộc vào việc chạy thật được. Đối thủ lớn (Microsoft/Salesforce) có thể thêm "approval" bất cứ lúc nào.

### (b) 5 câu định vị cho OLP

1. "xT là hệ điều hành doanh nghiệp mã nguồn mở mà agent là giao diện: ~15 tool MCP cố định, mọi nghiệp vụ là dữ liệu."
2. "Agent đề xuất mô hình và quy trình; con người duyệt rồi mới áp dụng — mỗi bước được ghi vào nhật ký chuỗi băm."
3. "Salesforce và Microsoft đã mở MCP cho ERP/CRM của họ; xT đưa cùng năng lực đó cho mọi doanh nghiệp Việt Nam, tự host, Apache-2.0, không khóa nhà cung cấp."
4. "Mỗi doanh nghiệp là một tenant cô lập bằng Postgres RLS, dữ liệu cá nhân đi trên đường riêng không vào mô hình ngôn ngữ, và có thể chạy hoàn toàn với LLM local."
5. "Không xây lại ERP từng ngành: một lõi headless, mô hình hóa bằng hội thoại, dùng được với ChatGPT, Claude hay mô hình cục bộ."

### (c) Ý tưởng đáng vay mượn

- **Kite**: mô hình trạng thái "Nháp → Mở bán (bạn chọn)" làm chuẩn UX cho mọi đề xuất; tích hợp SePay/VietQR cho thu–chi VN; giá "mua một lần, dữ liệu của bạn" cho SME.
- **crmkit**: repo `skills` đi kèm dạy agent vận hành hệ thống; "operating manual một trang"; API dạng text dễ grep.
- **Frappe DocType**: mô hình hóa trường + quyền + workflow trong một đối tượng JSON; đọc để thiết kế schema của "model as data" (Frappe MIT — chỉ học ý, vẫn tránh copy trừ khi chấp nhận license).
- **Auth0 CIBA + RAR**: nội dung phê duyệt hiển thị cho người duyệt trên kênh riêng, token ngắn hạn ràng buộc đúng hành động; so sánh với tính năng CIBA của Keycloak.
- **OpenID AuthZEN**: chuẩn hóa PEP/PDP cho kiểm tra quyền mỗi tool-call.
- **RFC 8693 / draft OBO**: claim `act` ghi chuỗi "người → agent" vào audit.
- **Kong MCP Registry / Apigee API hub**: danh mục tool được duyệt, khả năng quan sát lưu lượng MCP, chính sách kế thừa — ý tưởng cho trang quản trị tool/tenant.
- **Microsoft**: đi từ 13 tool cố định lên tool động cho thấy nhu cầu mở rộng — chuẩn bị cơ chế "gói nghiệp vụ" (data packs) thay vì thêm tool.
- **Zapier MCP**: History tab (nhật ký hành động hiển thị cho người dùng) và mô hình "tool sẵn bật theo kết nối".
- **Cảnh báo license**: NocoDB đã đổi từ AGPL sang Sustainable Use (01/2026), Directus dùng MSCL, n8n/Redpanda theo CLAUDE.md — nên ghim phiên bản và kiểm LICENSE trong CI cho mọi phụ thuộc.

### Việc cần xác minh thêm trước khi đưa vào hồ sơ
Supabase MCP license; Make MCP; 1Office AI; Twenty MCP chính thức; Kong/agentgateway license; hỗ trợ CIBA của Keycloak bản đang dùng; trạng thái mới nhất của draft IETF; báo cáo phân tích (Gartner/McKinsey) về agentic ERP.
