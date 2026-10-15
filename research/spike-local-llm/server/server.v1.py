# SPDX-License-Identifier: Apache-2.0
"""Spike MCP Apps: server Python (SDK `mcp` 2.x) + Streamable HTTP, không auth."""
import json, pathlib, sys, time
from typing import Any
from mcp.server.apps import Apps, client_supports_apps
from mcp.server.mcpserver import MCPServer, Context

D = pathlib.Path(__file__).parent
BRIDGE = (D / "bridge.js").read_text()
def page(name): return (D / name).read_text().replace("/*BRIDGE*/", BRIDGE)
LOG = D.parent / "server-calls.log"
def log(tool, args):
    with LOG.open("a") as f: f.write(json.dumps({"ts": time.time(), "tool": tool, "args": args}) + "\n")

EMP = [
 {"id": 1, "name": "Lan", "title": "CEO", "manager_id": None, "email": "lan@xt.vn"},
 {"id": 2, "name": "Minh", "title": "CTO", "manager_id": 1, "email": "minh@xt.vn"},
 {"id": 3, "name": "Hoa", "title": "CFO", "manager_id": 1, "email": "hoa@xt.vn"},
 {"id": 4, "name": "Tuan", "title": "Dev Lead", "manager_id": 2, "email": "tuan@xt.vn"},
 {"id": 5, "name": "Nga", "title": "Kế toán", "manager_id": 3, "email": "nga@xt.vn"},
]
TASKS = {"T-1": {"task_id": "T-1", "title": "Duyệt mua laptop", "requester": "Tuan", "description": "5 laptop dev", "amount": 150000000, "status": "pending"}}

apps = Apps()
ORG_URI, APPROVAL_URI = "ui://xt/org-chart.html", "ui://xt/approval-card.html"
apps.add_html_resource(ORG_URI, page("orgchart.html"), name="org_chart", description="Org chart SVG", prefers_border=True)
apps.add_html_resource(APPROVAL_URI, page("approval.html"), name="approval_card", description="Approval card", prefers_border=True)

@apps.tool(resource_uri=ORG_URI, description="Hiển thị sơ đồ tổ chức")
def show_org_chart(ctx: Context) -> dict[str, Any]:
    log("show_org_chart", {"client_supports_apps": client_supports_apps(ctx)})
    return {"employees": EMP}

@apps.tool(resource_uri=ORG_URI, visibility=["app"], description="Chi tiết nhân viên (UI gọi)")
def get_employee(id: int) -> dict[str, Any]:
    log("get_employee", {"id": id})
    e = next(e for e in EMP if e["id"] == id)
    m = next((x for x in EMP if x["id"] == e["manager_id"]), None)
    return {**e, "manager": m["name"] if m else None}

@apps.tool(resource_uri=APPROVAL_URI, description="Hiển thị thẻ phê duyệt")
def show_approval_card(task_id: str = "T-1") -> dict[str, Any]:
    log("show_approval_card", {"task_id": task_id}); return TASKS[task_id]

@apps.tool(resource_uri=APPROVAL_URI, visibility=["app"], description="Duyệt/từ chối task (UI gọi)")
def act_on_task(task_id: str, decision: str) -> dict[str, Any]:
    log("act_on_task", {"task_id": task_id, "decision": decision})
    TASKS[task_id]["status"] = "approved" if decision == "approve" else "rejected"
    return {"task_id": task_id, "status": TASKS[task_id]["status"]}

mcp = MCPServer("xt-apps-spike", extensions=[apps])

# ---- Bản thêm cho spike local-LLM: tool KHÔNG UI (model nhìn thấy) ----
@mcp.tool(description="Danh sách nhân viên: id, name, title, manager_id (manager_id = id của người quản lý trực tiếp)")
def list_employees() -> dict[str, Any]:
    log("list_employees", {}); return {"employees": EMP}

@mcp.tool(description="Tìm nhân viên theo tên; trả về thông tin, người quản lý (manager) và danh sách báo cáo trực tiếp (direct_reports)")
def find_employee(name: str) -> dict[str, Any]:
    log("find_employee", {"name": name})
    e = next((e for e in EMP if e["name"].lower() == name.strip().lower()), None)
    if not e: return {"error": f"không có nhân viên tên {name}"}
    m = next((x for x in EMP if x["id"] == e["manager_id"]), None)
    return {**e, "manager": m["name"] if m else None, "direct_reports": [x["name"] for x in EMP if x["manager_id"] == e["id"]]}

@mcp.tool(description="Xem một task/đề xuất chờ duyệt theo task_id (vd T-1)")
def get_task(task_id: str) -> dict[str, Any]:
    log("get_task", {"task_id": task_id}); return TASKS.get(task_id) or {"error": "không có task " + task_id}

@mcp.tool(description="Duyệt (decision='approve') hoặc từ chối (decision='reject') một task theo task_id")
def decide_task(task_id: str, decision: str) -> dict[str, Any]:
    log("decide_task", {"task_id": task_id, "decision": decision})
    if task_id not in TASKS: return {"error": "không có task " + task_id}
    TASKS[task_id]["status"] = "approved" if decision == "approve" else "rejected"
    return {"task_id": task_id, "status": TASKS[task_id]["status"]}

if __name__ == "__main__":
    import uvicorn
    from starlette.middleware.cors import CORSMiddleware
    app = mcp.streamable_http_app(streamable_http_path="/mcp")
    # Host chạy trên browser (basic-host) gọi thẳng từ origin khác -> bắt buộc CORS + expose Mcp-Session-Id
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"], expose_headers=["Mcp-Session-Id", "Mcp-Protocol-Version"])
    uvicorn.run(app, host="127.0.0.1", port=18220, log_level="warning")
