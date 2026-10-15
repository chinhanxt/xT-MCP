# SPDX-License-Identifier: Apache-2.0
import sys
from xml.sax.saxutils import escape, quoteattr

class Page:
    def __init__(s, name, pid):
        s.name, s.pid, s.cells, s.n = name, pid, [], 0
    def _id(s):
        s.n += 1; return f"{s.pid}_{s.n}"
    def box(s, x, y, w, h, label, fill="#ffffff", stroke="#666666", font=12, bold=False,
            align="center", valign="middle", rounded=1, extra=""):
        i = s._id()
        st = (f"rounded={rounded};whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
              f"fontSize={font};align={align};verticalAlign={valign};arcSize=6;spacing=6;"
              + ("fontStyle=1;" if bold else "") + extra)
        s.cells.append(f'<mxCell id="{i}" value={quoteattr(label)} style="{st}" vertex="1" parent="1">'
                       f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return i
    def text(s, x, y, w, h, label, font=12, align="left", bold=False, color="#000000"):
        i = s._id()
        st = (f"text;html=1;whiteSpace=wrap;align={align};verticalAlign=middle;fontSize={font};"
              f"fontColor={color};" + ("fontStyle=1;" if bold else ""))
        s.cells.append(f'<mxCell id="{i}" value={quoteattr(label)} style="{st}" vertex="1" parent="1">'
                       f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return i
    def edge(s, a, b, label="", dashed=False, color="#333333", both=False):
        i = s._id()
        st = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;strokeColor={color};strokeWidth=2;"
              f"fontSize=11;endArrow=block;endFill=1;labelBackgroundColor=#ffffff;"
              + ("dashed=1;" if dashed else "") + ("startArrow=block;startFill=1;" if both else ""))
        s.cells.append(f'<mxCell id="{i}" value={quoteattr(label)} style="{st}" edge="1" parent="1" '
                       f'source="{a}" target="{b}"><mxGeometry relative="1" as="geometry"/></mxCell>')
    def xml(s, w, h):
        return (f'<diagram id="{s.pid}" name={quoteattr(s.name)}><mxGraphModel dx="1600" dy="1000" grid="1" '
                f'gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
                f'pageWidth="{w}" pageHeight="{h}" math="0" shadow="0"><root><mxCell id="0"/>'
                f'<mxCell id="1" parent="0"/>' + "".join(s.cells) + '</root></mxGraphModel></diagram>')

def ul(items):
    return "<br>".join("• " + i for i in items)

# ============ PAGE 1: ĐỀ THI ============
p = Page("1. Đề thi - 3 tầng & 7 lớp", "p1")
p.text(40, 15, 1660, 50, "<b>DX-OS Open-Core — OLP Phần mềm Nguồn mở 2026</b><br><font style='font-size:13px'>Tháp 3 tầng · 7 lớp công nghệ tham chiếu · Chuỗi tiến hóa H-P-D-I · 4 nguyên lý thiết kế</font>", font=22, align="center")

C3, C2, C1 = "#dae8fc", "#d5e8d4", "#ffe6cc"
S3, S2, S1 = "#6c8ebf", "#82b366", "#d79b00"
# --- Tầng 3
p.box(40, 80, 1000, 180, "", C3, S3)
p.text(55, 84, 970, 30, "<b>TẦNG 3 — NỀN TẢNG KINH DOANH SỐ</b> &nbsp;<i>(ứng dụng chuyên ngành, cắm–rút linh hoạt · không bắt buộc)</i>", font=14)
t3 = [("Dòng chảy Hàng hóa", ["OMS – đơn hàng", "WMS – kho bãi", "POS – điểm bán", "TMS – vận tải"]),
      ("Thời gian & Dự án", ["PSA – dịch vụ chuyên nghiệp", "Timesheet – giờ công", "CDE + BIM 3D"]),
      ("Trải nghiệm & Thuê bao", ["eKYC / AML", "LMS – học tập", "EMR – bệnh án", "CRM – khách hàng"]),
      ("Tài sản & Không gian", ["MES / SCADA", "BMS – tòa nhà", "Smart City IoT"])]
for k, (h, it) in enumerate(t3):
    p.box(50 + k*247, 120, 237, 130, f"<b>{h}</b><br>{ul(it)}", "#ffffff", S3, 11, align="left", valign="top")

# --- Tầng 2
p.box(40, 275, 1000, 260, "", C2, S2)
p.text(55, 279, 970, 30, "<b>TẦNG 2 — KHÔNG GIAN NĂNG LỰC SỐ (H-P-D-I)</b> &nbsp;<i>(có giao diện người dùng cuối · bắt buộc ≥ 1 ứng dụng minh họa, 1 không gian là đủ)</i>", font=14)
hpdi = [("[H] Con người số", "#fff2cc", "#d6b656", ["Cổng giao tiếp điện tử tập trung", "Ổ đĩa lưu trữ đám mây", "Kanban & Lịch", "Wiki – quản trị tri thức", "App di động hiện trường"]),
        ("[P] Quy trình số", "#f8cecc", "#b85450", ["Thanh toán & đối soát", "Hóa đơn điện tử", "Giải mã tài liệu trên RAM", "e-Office", "Nhân sự & KPIs", "Dự án & kế hoạch", "Thiết kế form / BPMN"]),
        ("[D] Dữ liệu số", "#e1d5e7", "#9673a6", ["<i>Ấm:</i> NGSI-LD, Lakehouse,", "WebGIS, IOC/BI, DaaS,", "Data Lineage", "<i>Lạnh:</i> phòng đọc chống sửa xóa,", "sổ cái mật mã bất biến"]),
        ("[I] Trí tuệ số", "#dae8fc", "#6c8ebf", ["Tìm kiếm toàn văn + véc-tơ", "Cổng quản trị AI Agents", "OCR bóc tách văn bản", "Trợ lý ảo RAG nội bộ"])]
for k, (h, f, sk, it) in enumerate(hpdi):
    p.box(50 + k*247, 315, 237, 210, f"<b>{h}</b><br>{ul(it)}", f, sk, 11, align="left", valign="top")

# --- Tầng 1
p.box(40, 550, 1000, 345, "", C1, S1)
p.text(55, 554, 970, 30, "<b>TẦNG 1 — NỀN TẢNG DỊCH VỤ LÕI SỐ (Headless PaaS)</b> &nbsp;<i>(chạy ngầm · KHÔNG có UI · phục vụ tầng trên)</i>", font=14)
t1 = [("★ Định danh, Bảo mật & ATTT", ["SSO, MFA", "Két sắt khóa / bí mật", "Kiểm dịch tệp tin", "PKI, HSM, chữ ký số"], True),
      ("Đa thuê ước, Biên mạng & Cá thể hóa", ["★ API Gateway zero-trust", "Che giấu PII", "Multi-tenancy", "Đa ngôn ngữ"], True),
      ("★ Dữ liệu có cấu trúc & Giao dịch", ["OLTP toàn vẹn", "Tự sinh API + RLS", "Phiên bản / lịch sử", "Chuỗi thời gian, semantic layer"], True),
      ("★ Dữ liệu phi cấu trúc & Hồ sơ", ["Object storage", "Hồ sơ / văn bản phê duyệt", "Đánh chỉ mục toàn văn", "Trộn dữ liệu sinh biểu mẫu"], True),
      ("★ Quy trình & Tự động hóa", ["BPMN – luồng phê duyệt", "Serverless script", "Nạp dữ liệu theo lô"], True),
      ("Vận hành, Giám sát & Kiểm toán", ["Log truy vết, audit bất biến", "Event streaming", "GitOps", "Sao lưu / khôi phục"], False),
      ("Truyền thông & Giao vận", ["Định tuyến đa kênh", "WebSocket realtime", "Hàng đợi bất đồng bộ"], False)]
for k, (h, it, req) in enumerate(t1):
    r, c = divmod(k, 4)
    p.box(50 + c*247, 590 + r*150, 237, 140, f"<b>{h}</b><br>{ul(it)}",
          "#fff" if not req else "#fffaf0", "#b85450" if req else S1, 11, align="left", valign="top",
          extra="strokeWidth=2;" if req else "")
p.box(50 + 3*247, 740, 237, 140,
      "<b>★ BẮT BUỘC (Mục 3)</b><br>1. Identity / SSO<br>2. API Gateway<br>3. Dữ liệu cấu trúc / phi cấu trúc<br>4. Workflow engine<br><i>Tham khảo: Supabase · Strapi · Directus</i>",
      "#f8cecc", "#b85450", 11, align="left", valign="top", extra="strokeWidth=2;")

# --- 7 lớp
p.box(1070, 80, 630, 815, "", "#f5f5f5", "#666666")
p.text(1085, 84, 600, 30, "<b>7 LỚP CÔNG NGHỆ THAM CHIẾU</b> <i>(ví dụ — đội thi tự chọn)</i>", font=14)
layers = [("Lớp 1 · Trải nghiệm & Giao diện đa kênh", "Nuxt 3 / Vue 3, Flutter, Centrifugo, Tailwind", "UI Web/Mobile, realtime WebSocket", "→ Tầng 2"),
          ("Lớp 2 · Biên mạng, Cửa ngõ & ATTT", "APISIX, Keycloak, Vault, ClamAV, Coraza WAF", "Zero-Trust, SSO/MFA, bí mật, kiểm dịch", "→ Tầng 1"),
          ("Lớp 3 · Tác nghiệp, Logic & Thực thể", "Hasura GraphQL, PostgreSQL(+PostGIS), MongoDB, Orion", "OLTP, tự sinh API, NGSI-LD, Digital Twin", "→ Tầng 1"),
          ("Lớp 4 · Tự động hóa, Quy trình & DevSecOps", "ArgoCD, GitLab CI, Flowable BPMN, Kestra, Velero <font color='#b85450'>(tránh n8n – phi OSI)</font>", "GitOps, luồng phê duyệt, backup", "→ Tầng 1"),
          ("Lớp 5 · Dữ liệu hội tụ, Truyền phát & Sổ cái", "Redpanda, Apache Iceberg, MinIO, ImmuDB", "Event streaming, Lakehouse, sổ cái bất biến", "→ Tầng 1 / [D]"),
          ("Lớp 6 · Phân tích, Không gian & Trí tuệ", "Trino, DuckDB, GeoServer, Metabase, Cube.dev, Ollama / vLLM", "Truy vấn phân tán, GIS, BI, LLM nội bộ", "→ Tầng 2 [D][I]"),
          ("Lớp 7 · Quản trị, Tìm kiếm & Giám sát", "Vector, Parseable, OpenMetadata, Meilisearch, Qdrant, Prometheus/Grafana", "Audit log, lineage, full-text + vector, giám sát", "→ Tầng 1 / [I]")]
lc = ["#dae8fc", "#f8cecc", "#ffe6cc", "#fff2cc", "#e1d5e7", "#d5e8d4", "#f5f5f5"]
for k, (h, tech, role, tag) in enumerate(layers):
    y = 122 + k*110
    p.box(1080, y, 610, 102, f"<b>{h}</b> &nbsp;<font color='#b85450'><b>{tag}</b></font><br><i>{tech}</i><br>{role}",
          lc[k], "#666666", 11, align="left", valign="top")

# --- H-P-D-I chain
p.text(40, 915, 1000, 25, "<b>CHUỖI TIẾN HÓA H → P → D → I</b> &nbsp;<i>(tuyến tính, không nhảy cóc)</i>", font=14)
ids = []
chain = [("[H] Human", "SSO, chat, wiki, P.A.R.A", "#fff2cc", "#d6b656"),
         ("[P] Process", "SOP, Poka-yoke, workflow", "#f8cecc", "#b85450"),
         ("[D] Data", "Single Source of Truth, BI", "#e1d5e7", "#9673a6"),
         ("[I] Intelligence", "LLM, RAG, AI Agents", "#dae8fc", "#6c8ebf")]
for k, (h, d, f, sk) in enumerate(chain):
    ids.append(p.box(40 + k*255, 945, 215, 70, f"<b>{h}</b><br>{d}", f, sk, 12))
for a, b in zip(ids, ids[1:]):
    p.edge(a, b)
p.box(40, 1025, 1000, 50, "<b>Human-in-the-Loop:</b> thao tác thủ công giảm từ 100% → 10–20%; con người chuyển sang giám sát chất lượng &amp; <b>phê duyệt ngoại lệ</b>. AI ở [I] chỉ đáng tin khi [P] có Poka-yoke và [D] sạch.", "#ffffff", "#999999", 12, align="left")

# --- 4 nguyên lý
p.box(1070, 915, 630, 160,
      "<b>4 NGUYÊN LÝ THIẾT KẾ (Mục 4)</b><br>"
      "(i) Phân tách theo vòng đời &amp; tốc độ dữ liệu — OLTP nhanh, lịch sử đẩy xuống hồ dữ liệu<br>"
      "(ii) Hội tụ dữ liệu IT &amp; OT — quy trình + cảm biến → Digital Twin<br>"
      "(iii) Quản trị dữ liệu thế hệ mới — Data Lineage, nguồn gốc, chất lượng<br>"
      "(iv) Zero-Trust + GitOps — kiểm dịch tại biên, hạ tầng bằng mã, tự phục hồi",
      "#ffffff", "#666666", 12, align="left", valign="top")

# --- Bối cảnh, yêu cầu, chấm điểm
p.box(40, 1095, 325, 175,
      "<b>BỐI CẢNH — 2 THÁI CỰC THẤT BẠI</b><br>1. Phần mềm rời rạc → <i>ốc đảo dữ liệu</i>, GIGO<br>2. Hệ nguyên khối đóng → <i>vendor lock-in</i>, mất <b>chủ quyền số</b><br><br>Lời giải: DX-OS 3 tầng + Open-Core<br>Kế thừa OLP 2023 (LLM/RAG → [I]), 2024 (Low-code → [P]), 2025 (Linked Open Data → [D])",
      "#ffffff", "#999999", 11, align="left", valign="top")
p.box(380, 1095, 325, 175,
      "<b>YÊU CẦU NỘP BÀI</b><br>• Giấy phép OSI, repo công khai có web viewer<br>• Release định dạng mở (.tar.gz), có số phiên bản<br>• README, hướng dẫn build, Changelog, Bug tracker<br>• Header giấy phép trong từng file<br>• Hạn: <b>17:00 CN 06/12/2026</b> → olpvietnam@vaip.vn, thuky@vfossa.vn<br>• Chung kết <b>10/12</b>: 15 phút trình bày + live demo",
      "#ffffff", "#999999", 11, align="left", valign="top")
p.box(720, 1095, 320, 175,
      "<b>CHẤM ĐIỂM 100</b><br><u>PoF 50đ</u>: Repo 5 · License 10 · Release 5 · Build 10 · Thư viện 10 · Tài liệu 10<br><u>Sản phẩm 50đ</u>: Nguyên gốc 10 · Hoàn thiện 10 · Thân thiện 10 · Bền vững 10 · Trình diễn &amp; cộng đồng 10",
      "#ffffff", "#999999", 11, align="left", valign="top")
p.box(1070, 1095, 630, 175,
      "<b>DIỄN GIẢI CỦA BTC (Telegram OLP)</b><br>\"Nguyên lý thiết kế (Mục 4) chỉ mang tính định hướng. Giải pháp cụ thể do đội thi đề xuất để <b>xây dựng Tầng 1 cho bài toán minh họa ở Tầng 2</b>. Bài toán Tầng 2 không cần phủ đủ 4 không gian, <b>chỉ cần 1 không gian</b>.\"<br><br><b>⇒ Tối thiểu:</b> Tầng 1 headless (4 dịch vụ ★) + 1 ứng dụng Tầng 2",
      "#fff2cc", "#d6b656", 11, align="left", valign="top")

# ============ PAGE 2: xT — architecture v2.2 ============
q = Page("2. xT - kiến trúc v2.2", "p2")
q.text(40, 15, 1660, 50, "<b>xT — Agent-native DX-OS · Kiến trúc v2.2</b><br><font style='font-size:13px'>Bộ tool cố định · Nghiệp vụ là dữ liệu · Agent đề xuất, con người duyệt</font>", font=22, align="center")

q.box(40, 80, 1240, 290, "", C2, S2)
q.text(55, 85, 1200, 30, "<b>TẦNG 2 — Desk + MCP Apps + 3 gói nghiệp vụ</b> · [P] e-Office · [I] Quản trị agent", font=14)
desk = q.box(60, 125, 370, 115, "<b>xT Desk · client chính</b><br>React/Vite + backend Python<br>Chat qwen3.5:4b local · proxy MCP<br>/desk/confirm/{id}: UI người (v0.1)<br>Chat + Desk đầy đủ: v0.3", "#ffffff", S2, 12)
apps = q.box(465, 125, 370, 115, "<b>MCP Apps · UI trong chat</b><br>AppRenderer · thẻ duyệt · orgchart<br>Form · Kanban + SLA · diff kế hoạch<br>Nút duyệt mở confirm_url<br>Gói JSON/YAML có phiên bản trong DB", "#ffffff", S2, 12)
llm = q.box(870, 125, 390, 115, "<b>Ollama · profile full</b><br>qwen3.5:4b tự host<br>core-10 · num_ctx ≥ 16384<br>Ngoài Tầng 1; lõi không gọi LLM", "#dae8fc", S3, 12)
q.edge(desk, llm, "proxy LLM", dashed=True, color=S3)
for k, (title, detail) in enumerate([
    ("Đề xuất–phê duyệt", "Đơn đề nghị · người duyệt · SoD"),
    ("Giao việc–SLA", "Hộp việc · deadline · escalation"),
    ("Nhân sự vòng đời", "Onboarding → thay đổi → offboarding"),
]):
    q.box(60 + k*405, 270, 390, 80, f"<b>{title}</b><br>{detail}", "#ffffff", S2, 12)

q.text(40, 390, 1240, 25, "<b>CLIENT NGOÀI</b> · MCP Streamable HTTP + OAuth", font=13)
clients = []
for k, (title, detail) in enumerate([
    ("Claude Free", "1 custom connector · cloud<br>Kết nối thật / Apps: chưa kiểm chứng"),
    ("ChatGPT", "Connector · cloud<br>Demo chỉ đọc"),
    ("Goose CLI + Ollama", "Client nguồn mở · local<br>Xác nhận qua Desk"),
]):
    clients.append(q.box(60 + k*405, 430, 390, 85, f"<b>{title}</b><br>{detail}", "#f5f5f5", "#666666", 12))
tunnel = q.box(60, 555, 1200, 110,
    "<b>Cloudflare Tunnel xt · profile public (demo)</b><br>"
    "xt.freepro.online → Desk + /desk/confirm/{id}<br>"
    "xt-mcp.freepro.online → /mcp · xt-api.freepro.online → /data, /workflow, /files, /rest<br>"
    "xt-auth.freepro.online → Keycloak · HTTPS, TLS kết thúc tại Cloudflare", "#dae8fc", S3, 12)
for client in clients:
    q.edge(client, tunnel, dashed=True, color=S3)

q.box(40, 700, 1240, 720, "", C1, S1)
q.text(55, 705, 1200, 30, "<b>TẦNG 1 — Lõi headless · Python · không UI nghiệp vụ</b>", font=14)
gw = q.box(60, 755, 1200, 95,
    "<b>★ Apache APISIX · 127.0.0.1:9080 · định tuyến theo Host</b><br>"
    "/auth · /data/v1 + /files/v1 · /workflow/v1 · /rest/v1 + /mcp<br>"
    "/mcp + PRM: passthrough, rate-limit IP; xt-api tự xác thực · các API dịch vụ khác: JWT<br>"
    "xt → Desk (Tầng 2) · xt-auth → Keycloak · xt-mcp → xt-api · xt-api → dịch vụ headless", "#fffaf0", "#b85450", 12)
kc = q.box(60, 900, 270, 180,
    "<b>★ Keycloak · SSO / MFA / DCR</b><br>Realm xt · Organization = tenant<br>OIDC / OAuth · PKCE S256<br>TOTP · xác thực tươi<br>Client người: xt-desk<br>xT giữ tổ chức / vai trò<br>Chặn admin UI từ ngoài", "#fffaf0", "#b85450", 12)
api = q.box(375, 900, 490, 210,
    "<b>xt-api · MCP server · 17 tool v0.1</b><br>Catalog cố định: ToolContract · không Code Mode<br>Streamable HTTP · 401 + PRM · REST/OpenAPI<br>JWT → tenant → IAM/SoD → autonomy → confirmation<br>CAS · idempotency · Poka-yoke · PII · audit<br>Model engine: spec → bảng thật + CHECK/FK + RLS<br>SpiffWorkflow: bước người in-process<br>/desk/confirm: Desk gọi API thực thi PendingOperation", "#d5e8d4", S2, 12, extra="strokeWidth=2;")
worker = q.box(910, 900, 350, 180,
    "<b>★ xt-worker · Workflow</b><br>procrastinate + SpiffWorkflow<br>Timer / SLA · service task · outbox<br>Cùng image với xt-api<br>Job mang tenant_id; transaction/tenant<br>Retry · lease fencing · action allowlist<br>Kiểm lại quyền lúc thực thi", "#fffaf0", "#b85450", 12)
pgrst = q.box(60, 1160, 270, 190,
    "<b>PostgREST · chỉ đọc</b><br>aud = xt-data · GET only<br>View api_t&lt;seq&gt;<br>Accept-Profile ràng tenant token<br>db-schemas động + NOTIFY<br>security_invoker=false<br>Loại field sensitive khỏi view", "#fffaf0", "#b85450", 12)
pg = q.box(375, 1160, 490, 190,
    "<b>★ PostgreSQL 17 · schema-per-tenant</b><br>xt_t&lt;seq&gt;.&lt;model&gt; · tenant_id + RLS FORCE<br>xt_meta · xt_audit · jobs (procrastinate)<br>Mutation + audit + outbox + receipt: 1 transaction<br>Audit hash-chain theo tenant · append-only<br>Field sensitive: HMAC có khóa trong audit<br>Model apply 2 pha · rollout migration theo tenant", "#fffaf0", "#b85450", 12, extra="strokeWidth=2;")
s3 = q.box(910, 1160, 350, 190,
    "<b>★ SeaweedFS · object storage S3</b><br>Hồ sơ · hợp đồng · chứng từ<br>/files/v1 qua APISIX<br>Presigned upload qua gateway<br>Quarantine → ClamAV (full)<br>Không lộ UI master / filer", "#fffaf0", "#b85450", 12)
q.edge(tunnel, gw, "4 hostname → 1 gateway")
q.edge(gw, kc, "SSO / OAuth")
q.edge(gw, api, "MCP / REST / workflow")
q.edge(gw, pgrst, "/data/v1", dashed=True)
q.edge(api, pg, "transaction")
q.edge(worker, pg, "jobs / BPMN state")
q.edge(pgrst, pg, "SELECT")
q.edge(api, s3, "files")
q.text(60, 1370, 1200, 30, "<b>Compose:</b> minimal / full / public · Docker Engine hoặc Podman · xT: Apache-2.0 · phụ thuộc OSI", font=12)

q.box(1310, 80, 390, 290,
    "<b>CHECKLIST ĐỀ THI · ÁNH XẠ KIẾN TRÚC</b><br><br>"
    "<b>Tầng 1 — 4 dịch vụ bắt buộc</b><br>"
    "☑ SSO → Keycloak<br>☑ API Gateway → APISIX<br>"
    "☑ Data → PostgreSQL + PostgREST<br>&nbsp;&nbsp; + SeaweedFS (phi cấu trúc)<br>"
    "☑ Workflow → SpiffWorkflow<br>&nbsp;&nbsp; trong xt-api + xt-worker<br><br>"
    "<b>Tầng 2:</b> Desk + MCP Apps + 3 gói<br>Ứng dụng có UI · lõi headless",
    "#ffffff", S2, 12, align="left", valign="top", extra="strokeWidth=2;")
q.box(1310, 390, 390, 360,
    "<b>KÊNH XÁC NHẬN CỦA NGƯỜI</b><br><br>"
    "1. xt-api kiểm IAM/SoD trước xác nhận<br>2. Lưu PendingOperation (TTL 10 phút)<br>"
    "3. Trả confirmation_required + confirm_url<br>4. Người mở /desk/confirm/{id} ở Desk<br>"
    "5. Keycloak: max_age=0 + TOTP;<br>xác thực tươi trong 5 phút<br>"
    "6. Desk gọi xt-api kiểm lại và thực thi;<br>agent gọi lại cùng idempotency_key lấy receipt<br><br>"
    "Tool không nhận tham số gate.<br>Không có kênh xác nhận CLI.<br>"
    "L3/vượt ngưỡng: prompt=login + acr.<br>Telegram nonce ký: kênh phụ (v0.2).",
    "#fff2cc", "#d6b656", 12, align="left", valign="top")
q.box(1310, 770, 390, 310,
    "<b>LÀN PII · THEO azp + POLICY TENANT</b><br><br>"
    "<b>Cloud:</b> Claude / ChatGPT<br>PII luôn masked hoặc omitted<br>"
    "<b>Local:</b> Desk / Goose + Ollama<br>Theo policy tenant: masked / local_only<br>"
    "<b>Human:</b> phiên người ở Desk<br>Giá trị thật theo quyền<br><br>"
    "Nhãn PII bắt buộc từ v0.1;<br>che output + audit egress từ v0.3.<br>"
    "Trước v0.3: full chỉ với dữ liệu demo,<br>không có PII thật.<br>"
    "Từ chối ghi ngược [PII:token] vào DB.",
    "#ffffff", "#9673a6", 12, align="left", valign="top")
q.box(1310, 1100, 390, 320,
    "<b>RANH GIỚI &amp; NGUỒN THAM CHIẾU</b><br><br>"
    "Bộ tool cố định; model/process là dữ liệu.<br>Agent đề xuất, người duyệt rồi mới áp dụng.<br>"
    "Desk / Goose dùng LLM local; lõi không gọi LLM.<br><br>"
    "17 tool v0.1; 21 tool ở v1.0.<br>"
    "Đây là kiến trúc đích, không phải chứng nhận đã triển khai toàn bộ.<br><br>"
    "docs/architecture-v2.md (v2.2)<br>docs/deployment-public.md<br>docs/tool-contracts-v1.md<br>CLAUDE.md",
    "#ffffff", "#666666", 12, align="left", valign="top")

out = ('<mxfile host="app.diagrams.net" type="device">' + p.xml(1740, 1300) + q.xml(1740, 1460) + '</mxfile>')
open(sys.argv[1], "w", encoding="utf-8").write(out)
