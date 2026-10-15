# SPDX-License-Identifier: Apache-2.0
"""Render docs/tool-contracts-v1.md from contracts.py + measured sizes + probe results."""
import json

import contracts as C

sizes = json.load(open("sizes.json"))
probe = json.load(open("llm_probe.json"))
rows = {r["name"]: r for r in sizes["rows"]}
ann = {t["name"]: C.annotations(t["effects"]) for t in C.T}


def tf(b):
    return "T" if b else "F"


def level(t):
    return "human-only" if t.get("human_only") else t["max_autonomy"]


INTENT = {
    "inspect_workspace": "Tôi là ai, có model/quy trình/loại việc/policy nào, giới hạn, tool khả dụng",
    "describe_model": "Field, PII, state machine, rule, metric của một model",
    "describe_process": "Bước, người duyệt, SLA, trigger; tính tuyến cho một bộ giá trị",
    "find_people": "Danh bạ, sơ đồ tổ chức: ai báo cáo cho ai, sếp của ai, tìm người để lấy ref",
    "search_records": "Lọc có kiểu trong một model hoặc full-text xuyên model",
    "aggregate_records": "count/sum/avg/min/max, group_by, period, time_bucket; metric có tên; lineage",
    "get_record": "Đọc một ref: record, request + bước, process instance, file + text",
    "get_inbox": "Việc, đề xuất chờ tôi, thông báo, kèm SLA",
    "get_history": "Timeline, audit hash-chain, explain (vì sao bị chặn / số liệu từ đâu)",
    "create_record": "Tạo record, draft request, document, comment",
    "update_record": "Sửa field (gồm đổi người phụ trách, dời hạn task)",
    "transition_record": "Chuyển trạng thái theo state machine (task, rút request, đã đọc, offboarding...)",
    "upload_file": "text / base64 / upload_link → file ref",
    "submit_request": "Gửi đề xuất; tuyến do policy tính",
    "decide_request": "Duyệt / từ chối / trả lại / ủy quyền (luôn qua confirm_url)",
    "create_task": "Giao việc: hạn, cha-con, phụ thuộc, định kỳ",
    "propose_model_change": "Dry-run diff rồi gửi đề xuất đổi model",
    "start_process": "Chạy quy trình trigger manual trên một subject",
    "propose_process_change": "Đổi quy trình có giới hạn (ngưỡng, người duyệt, SLA, bước, trigger)",
    "delete_record": "Xóa mềm theo retention",
    "notify": "Nhắc/thông báo nội bộ ad-hoc",
}


def cat_table(since):
    out = []
    for i, t in enumerate([t for t in C.T if t["since"] == since], 1 if since == "0.1" else 18):
        a = ann[t["name"]]
        out.append(f"| {i} | `{t['name']}` | {INTENT[t['name']]} | {level(t)} | {tf(a['readOnlyHint'])} | {tf(a['destructiveHint'])} | "
                   f"{tf(a['idempotentHint'])} | {tf(a['openWorldHint'])} | {t['ui_resource'] or '-'} |")
    return "\n".join(out)


def pp(o, ind=0, width=110):
    """Pretty JSON, 2-space indent; any node whose one-line form fits the width stays on one line."""
    flat = json.dumps(o, ensure_ascii=False, separators=(", ", ": "))
    if not isinstance(o, (dict, list)) or not o or len(flat) + ind <= width:
        return flat
    pad, pad2 = " " * ind, " " * (ind + 2)
    if isinstance(o, dict):
        items = [f"{pad2}{json.dumps(k, ensure_ascii=False)}: {pp(v, ind + 2, width)}" for k, v in o.items()]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    return "[\n" + ",\n".join(pad2 + pp(v, ind + 2, width) for v in o) + "\n" + pad + "]"


def contract_json(t):
    d = {
        "name": t["name"], "since": t["since"], "description": t["description"],
        "max_autonomy": t["max_autonomy"], "human_only": bool(t.get("human_only")),
        "effects_true": [k for k, v in t["effects"].items() if v],
        "annotations": ann[t["name"]],
        "iam": t["iam"], "errors": t["errors"], "ui_resource": t["ui_resource"],
        "returns_untrusted_content": True,
        "input_schema": t["input_schema"], "output_schema": t["output_schema"], "examples": t["examples"],
    }
    return pp(d)


NOTES = {
    "inspect_workspace": "`tools` liệt kê tool khả dụng/không khả dụng kèm lý do (mẫu `tool_availability` của REA); catalog `tools/list` không bao giờ đổi theo người dùng.",
    "describe_model": "`values_schema` là đúng JSON Schema 2020-12 server dùng để validate `values`/`set` của model revision active; agent không cần đoán.",
    "describe_process": "`for_values` cho phép trả lời 'tạm ứng 8 triệu thì ai duyệt' trước khi tạo đề xuất; `applies` = null nếu chưa đánh giá.",
    "find_people": "Tool theo ý định duy nhất được thêm vì model nhỏ (spike: 4B trả lời ngược chiều quan hệ 3/3 khi chỉ có tool tìm chung). Chỉ một trong `direct_reports_of`/`manager_of`/`org_unit`/`role` có hiệu lực; truyền nhiều hơn một → `invalid_request` (kiểm ở server, không dùng combinator). Tên trùng → `ambiguous_anchor`, agent phải hỏi lại. PII của person (SĐT, CCCD) theo làn §3.8; `name` là danh tính công việc, mặc định không `pii`.",
    "search_records": "Không có `model_id` thì `query` bắt buộc (kiểm ở server). Primitive `task`, `request`, `document`, `person`, `notification` đều lọc được bằng `model_id`, IAM chuyên biệt vẫn áp.",
    "aggregate_records": "`metric` (tên) hoặc `metrics` (ad-hoc), không cả hai; không có thì mặc định `count`. `period`/`time_bucket` áp lên `date_field`. Decimal trả chuỗi. Probe cho thấy model 4B hay bỏ sót bộ lọc trạng thái ('đã duyệt'): ưu tiên metric có tên (filter nằm trong định nghĩa đã duyệt) và `summary` luôn nêu filter đã áp để người dùng thấy.",
    "get_record": "Thay `get_process_status` của bản nháp (`ref=process_instance:…`, `include=[steps]`). Text file có phân trang `text_cursor`, luôn untrusted.",
    "get_inbox": "Luôn của người được đại diện; không có tham số chọn người khác.",
    "get_history": "`view=audit` cần `audit.read`; `explain` nhận `explain_ref`/`lineage_ref` từ lỗi hoặc aggregate.",
    "create_record": "`model_id=task` → `invalid_request` reason `wrong_tool` + continuation sang `create_task`. Request luôn được tạo ở state `initial` (draft). Field lạ → `invalid_request` reason `unknown_field` kèm danh sách field hợp lệ (probe: model 4B tự chế field `destination` khi chưa gọi `describe_model`). Level: L1 mặc định; model có thể khai `create_level: L2`.",
    "update_record": "Field `state` → `precondition_failed` reason `state_field` + continuation sang `transition_record`. Field `impact: high` (vd `assignee` của task giao ngoài tuyến) → L2.",
    "transition_record": "`(current_state, to_state)` xác định duy nhất transition. Level do transition khai báo. Request: chỉ `withdrawn`; state khác → `precondition_failed` + continuation `submit_request`/`decide_request`. `depends_on` chưa xong → `precondition_failed` reason `dependency_open`.",
    "upload_file": "`text` chỉ khi `source=text`, `base64` chỉ khi `source=base64` (server kiểm). `put_url` đi qua APISIX, không lộ endpoint SeaweedFS.",
    "submit_request": "L1 với đề xuất của chính người được đại diện; policy có thể nâng. File đính kèm chưa `clean`/`not_scanned` → `precondition_failed` reason `file_not_clean`.",
    "decide_request": "`human_only`: kể cả agent ở trần L3 cũng nhận `confirmation_required`; người xác nhận phải là người được giao bước. Người đề nghị/không được giao → `access_denied` (sod/not_assigned_approver) trước mọi xác nhận. `reason` bắt buộc với reject/return; `delegate_to` bắt buộc với delegate, phải là human khác người đề nghị (kiểm ở server). Request quản trị (proposal) duyệt xong → worker kích hoạt có revalidate.",
    "create_task": "Giao cho cấp dưới trực tiếp = L1 (policy mặc định), ngoài tuyến = L2. `due_at` bỏ trống → SLA của task type. `recurrence` tạo task con theo lịch bằng timer engine.",
    "propose_model_change": "Không còn `preview_token`/`preview_digest`. `dry_run=false` lưu Proposal bất biến + gửi Request quản trị (L1: chỉ đề xuất). Kích hoạt chỉ sau `decide_request` của người (destructive: 2 người). `capability_unavailable` cho phép biến đổi chưa hỗ trợ (đổi kiểu có backfill).",
    "start_process": "Hầu hết quy trình vòng đời khởi động qua `transition_record` (`starts_process`) hoặc trigger timer/event; tool này cho trigger `manual`.",
    "propose_process_change": "Bounded operations trước, chưa mở graph đầy đủ (12 KB của bản nháp). Instance đang chạy giữ revision cũ.",
    "delete_record": "Tool destructive duy nhất. Xóa/ẩn danh PII theo Luật 91/2025 chạy qua process (`transition_record(person,'erasure_requested')`), có human gate và tombstone hash.",
    "notify": "Closed-world theo bất biến §2; rate-limit theo người nhận.",
}


def tools_section(since):
    out = []
    n0 = 1 if since == "0.1" else 1
    sec = "5" if since == "0.1" else "6"
    for i, t in enumerate([t for t in C.T if t["since"] == since], n0):
        r = rows[t["name"]]
        a = ann[t["name"]]
        out.append(f"### {sec}.{i}. `{t['name']}`\n")
        out.append(f"- Mức: **{level(t)}** · effects: `{', '.join(k for k, v in t['effects'].items() if v)}` · annotations: "
                   f"readOnly={a['readOnlyHint']}, destructive={a['destructiveHint']}, idempotent={a['idempotentHint']}, openWorld={a['openWorldHint']}")
        out.append(f"- Kích thước: input {r['in']} B, data {r['data']} B, outputSchema (envelope) {r['out_env']} B.")
        out.append(f"- Ghi chú: {NOTES[t['name']]}\n")
        out.append("```json\n" + contract_json(t) + "\n```\n")
    return "\n".join(out)


def size_rows():
    out = []
    for r in sizes["rows"]:
        out.append(f"| `{r['name']}` | {r['since']} | {r['level']} | {r['in']} | {r['in_compact']} | {r['data']} | {r['out_env']} | "
                   f"{r['list_entry']} | {r['list_entry_with_out']} | {r['desc_chars']} | {r['props']}/{r['required']} | "
                   + " | ".join(tf(r[k]) for k in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")) + " |")
    return "\n".join(out)


def totals():
    R = sizes["rows"]
    tok = probe["tokens"]
    toolstok = {k: v - tok["none"] for k, v in tok.items()}
    lines = ["| Tập | Số tool | Σ input (B) | Σ compact (B) | Σ data (B) | Σ outputSchema (B) | tools/list (B) | tools/list + outputSchema (B) | Token tool thật trên qwen3.5:4b |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for label, sel, key in (("v0.1", lambda r: r["since"] == "0.1", "v0.1-17"), ("v1.0 (tất cả)", lambda r: True, "v1.0-21"),
                            ("core-10 (model nhỏ)", lambda r: r["name"] in C.CORE_PROFILE, "core10")):
        rs = [r for r in R if sel(r)]
        lines.append(f"| {label} | {len(rs)} | {sum(r['in'] for r in rs)} | {sum(r['in_compact'] for r in rs)} | {sum(r['data'] for r in rs)} | "
                     f"{sum(r['out_env'] for r in rs)} | {sum(r['list_entry'] for r in rs)} | {sum(r['list_entry_with_out'] for r in rs)} | {toolstok[key]} |")
    lines.append("")
    lines.append(f"Input lớn nhất: {max(r['in'] for r in R)} B (`propose_model_change`) = {max(r['in'] for r in R) * 100 // 13312}% ngân sách REA 13.312 B. "
                 "Token tool đo bằng `prompt_eval_count` của ollama (`/api/chat`, template tool của qwen3.5) trừ baseline không có tool "
                 f"({tok['none']} token); chỉ gồm name/description/parameters, không gồm outputSchema (ollama không gửi).")
    return "\n".join(lines)


def probe_section():
    res = probe["results"]
    tok = probe["tokens"]
    lines = [
        f"**Token thật** (qwen3.5:4b, ollama 0.30.10): core-10 = **{tok['core10'] - tok['none']}**, v0.1-17 = **{tok['v0.1-17'] - tok['none']}**, "
        f"v1.0-21 = **{tok['v1.0-21'] - tok['none']}** token cho phần tool. Hệ quả: với `num_ctx 8192` của spike, catalog v0.1 đầy đủ chiếm gần hết context; "
        "**client local phải đặt `num_ctx ≥ 16384`** (probe chạy 16384, VRAM vẫn vừa 6 GB) hoặc dùng core-10.",
        "",
        "**Smoke test chọn tool** (`think:false`, `temperature:0`, 11 câu tiếng Việt × 2 catalog, một lời gọi đầu tiên; lần chạy đầu 8 câu × 3 lặp cho 3 lần giống hệt nhau, nên ở temperature 0 mỗi câu coi như 1 mẫu):",
        "",
        "| Catalog | Câu | Tool | Args (rút gọn) | Đúng tool | Args hợp schema | Đúng ngữ nghĩa |",
        "|---|---|---|---|---|---|---|",
    ]
    semantic_bad = {
        ("core10", "Tạo đề nghị tạm ứng"): "chưa: tự chế field `destination`, thiếu `trip_end`/`purpose` → server trả `unknown_field` + danh sách field",
        ("core10", "Tổng tạm ứng (advance_request) đã duyệt theo"): "sai: `period=2026-11`, `date_field=trip_start`, thiếu `state=approved`",
        ("v0.1-17", "Tổng tiền tạm ứng đã duyệt tháng 11"): "thiếu filter `state=approved`",
        ("v0.1-17", "Tổng tạm ứng (advance_request) đã duyệt theo"): "thiếu `state=approved`; `group_by=[month]` không phải field → `unknown_field`",
    }
    ok_sem = 0
    for s, q, rep, name, args, ok, valid, secs in res:
        bad = next((v for (cs, cq), v in semantic_bad.items() if cs == s and q.startswith(cq)), None)
        if not bad:
            ok_sem += 1
        a = json.dumps(args, ensure_ascii=False)
        a = a if len(a) <= 110 else a[:107] + "…"
        lines.append(f"| {s} | {q} | `{name}` | `{a}` | {'có' if ok else 'không'} | {'có' if valid else 'không'} | {bad or 'có'} |")
    n = len(res)
    lines += ["",
              f"Tổng: đúng tool **{sum(r[5] for r in res)}/{n}**, args hợp schema **{sum(r[6] for r in res)}/{n}**, đúng ngữ nghĩa **{ok_sem}/{n}**. "
              "Lời gọi đầu mất 13-16 s vì nạp model; các lời gọi sau 3-7 s.",
              "",
              "**Đọc kết quả cho thiết kế:**",
              "- `find_people` được gọi với đúng tham số theo chiều quan hệ ở cả 2 câu (`direct_reports_of` cho “ai báo cáo cho”, `manager_of` cho “sếp của”); việc đọc kết quả không bị suy luận ngược vì output đã nêu `relation` + `summary` (chưa đo đa lượt).",
              "- Tham số phẳng, enum và pattern được sinh đúng; model tự đặt `idempotency_key` hợp lệ 100%; ghi nhận cho quyết định giữ key bắt buộc (F8).",
              "- Lỗi còn lại là **ngữ nghĩa của aggregate** (bỏ filter trạng thái) và **tự chế field** khi chưa gọi `describe_model`. "
              "Thiết kế đầu tiên `group_by: [\"created_at:month\"]` khiến model sinh `department:month` ở 3/3 lần; đã tách thành `period` + `time_bucket` + `date_field` "
              "và đo lại. Biện pháp tiếp: metric có tên (`advance_total` đã gói filter), `summary` nêu filter, lỗi `unknown_field` liệt kê field hợp lệ.",
              "- Đây chưa phải eval: chưa đa lượt, chưa đo câu âm/injection, chưa đo Claude/GPT.",
              ]
    return "\n".join(lines)


tpl = open("template.md").read()
env_example = C.success_envelope({"type": "object", "description": "<data schema của tool>"})
rep = {
    "CATALOG_TABLE_V01": cat_table("0.1"),
    "CATALOG_TABLE_V10": cat_table("1.0"),
    "TOOLS_V01": tools_section("0.1"),
    "TOOLS_V10": tools_section("1.0"),
    "ENVELOPE_JSON": pp(env_example),
    "ERROR_ENVELOPE_JSON": pp(C.ERROR_ENVELOPE),
    "CONFIRMATION_JSON": pp(C.CONFIRMATION_DETAILS),
    "SAMPLE_ERRORS_JSON": pp(C.SAMPLE_ERRORS),
    "ERROR_TABLE": "\n".join(f"| `{c}` | {'có' if r else 'không'} | {d} |" for c, r, d in C.ERROR_CODES),
    "SIZE_ROWS": size_rows(),
    "SIZE_TOTALS": totals(),
    "ERR_BYTES": str(sizes["error_envelope_bytes"]),
    "CONF_BYTES": str(sizes["confirmation_details_bytes"]),
    "CORE_LIST": ", ".join(C.CORE_PROFILE),
    "PROBE_SECTION": probe_section(),
}
for k in sorted(rep, key=len, reverse=True):
    assert k in tpl, k
    tpl = tpl.replace(k, rep[k])
open("/home/chinhan/xT/docs/tool-contracts-v1.md", "w").write(tpl)

# debate log: insert probe summary
deb = open("/home/chinhan/xT/docs/tool-contracts-debate.md").read()
if "PROBE_RESULTS_PLACEHOLDER" in deb:
    deb = deb.replace("PROBE_RESULTS_PLACEHOLDER", probe_section())
    open("/home/chinhan/xT/docs/tool-contracts-debate.md", "w").write(deb)
print("ok")
