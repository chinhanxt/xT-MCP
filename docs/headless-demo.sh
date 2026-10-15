#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
#
# Demo Tầng 1 headless, KHÔNG dùng MCP: gọi tuần tự 5 bước qua gateway bằng curl.
#   1. SSO       : OIDC discovery + JWKS của Keycloak
#   2. Gateway   : APISIX (Host xt-api) trả lời, và /data không token bị từ chối
#   3. Data      : GET /data/<model> với Accept-Profile: api_t<seq>
#   4. Workflow  : REST /workflow/v1 — định nghĩa + BPMN, khởi động W1, instance, inbox, quyết định
#                  (luôn 428 + confirm_url: người duyệt xác nhận trên trang Desk, script không duyệt thay)
#   5. Files     : REST /files/v1 — ý định tải lên -> presigned PUT -> finalize (sha256) -> tải về,
#                  multipart một bước; dữ liệu phi cấu trúc trên SeaweedFS (docs/files-api.md)
# Luôn gọi curl cả 5 bước (kể cả thiếu token/model/profile: thăm dò không token rồi báo THIẾU, không phải thành công).
# Dịch vụ không có thật (000, 404, 502, 503, 504, Cloudflare 530/1033, chua_trien_khai) in đúng chữ
# "chưa triển khai" và KHÔNG tính là thành công. Kiểm chéo tenant: chỉ 401/403/406 là bị từ chối. Hướng dẫn: docs/headless-data.md.
#
# Cấu hình bằng biến môi trường (không có bí mật trong tệp này):
#   XT_AUTH_BASE      mặc định https://xt-auth.freepro.online
#   XT_API_BASE       mặc định https://xt-api.freepro.online
#   XT_REALM          mặc định xt
#   XT_ACCESS_TOKEN_FILE  tệp chứa access token DATA (aud xt-data); ưu tiên hơn XT_ACCESS_TOKEN
#   XT_ACCESS_TOKEN   token data trực tiếp (lưu ý: nằm trong môi trường tiến trình)
#   XT_WORKFLOW_TOKEN_FILE / XT_WORKFLOW_TOKEN  token RIÊNG cho workflow (aud workflow). Token data
#                     (aud xt-data) KHÔNG bao giờ được gửi sang /workflow hay origin khác.
#   XT_WORKFLOW_BASE  mặc định = XT_API_BASE
#   XT_FILES_TOKEN_FILE / XT_FILES_TOKEN  token RIÊNG cho /files (aud xt-files); không gửi sang dịch vụ khác
#   XT_FILES_BASE     mặc định = XT_API_BASE; XT_FILES_PREFIX mặc định /files/v1
#   XT_FILES_UPLOAD   (tùy chọn) tệp cần tải lên ở bước 5; mặc định một tệp text/plain do script tạo
#   XT_FILES_UPLOAD_TYPE (tùy chọn) Content-Type của XT_FILES_UPLOAD, mặc định text/plain
#   XT_DATA_PREFIX    mặc định /data
#   XT_DATA_MODEL     tên model/view cần đọc (bắt buộc cho bước 3)
#   XT_DATA_PROFILE   schema tenant, dạng api_t<seq> (bắt buộc cho bước 3)
#   XT_DATA_FOREIGN_PROFILE  (tùy chọn) schema tenant khác; bước 3 kiểm phải bị từ chối
#   XT_WORKFLOW_PREFIX mặc định /workflow/v1; XT_WORKFLOW_PATH mặc định $XT_WORKFLOW_PREFIX/definitions
#   XT_WORKFLOW_DEFINITION  quy trình cần tải BPMN, mặc định proposal_approval
#   XT_WORKFLOW_START_FILE  (tùy chọn) JSON body của POST /instances; có thì GHI dữ liệu thật (W1)
#   XT_WORKFLOW_IDEMPOTENCY_KEY (tùy chọn) khóa cho POST /instances; mặc định sinh mới mỗi lần
#   XT_WORKFLOW_APPROVER_TOKEN_FILE / XT_WORKFLOW_APPROVER_TOKEN  (tùy chọn) token workflow của agent
#                     người duyệt: đọc inbox rồi gửi quyết định (XT_WORKFLOW_DECISION, mặc định approve)
#   XT_WORKFLOW_BPMN_OUT (tùy chọn) đường dẫn lưu BPMN tải về
#   XT_DEMO_MAX_BODY  số ký tự thân phản hồi được in, mặc định 600
#   XT_CURL_OPTS      tùy chọn curl thêm, ví dụ "--resolve xt-api.freepro.online:443:127.0.0.1"
#   XT_CLI_CLIENT_ID mặc định xt-cli (client public auth-code + PKCE, scope optional xt-data/xt-workflow/xt-files)
# Lấy token (một lần, trước khi chạy demo): `bash docs/headless-demo.sh --get-token data|workflow|files [tệp]`
#   in URL đăng nhập, người dùng mở bằng trình duyệt, script nhận code ở 127.0.0.1 và ghi token (quyền 600) vào tệp
#   (mặc định ~/.config/xt/<loại>_token); không in token. Token mỗi loại có scope/aud riêng, không dùng chéo.
# Mã thoát: 0 = cả 5 bước thật sự thành công; 2 = chỉ có bước chưa triển khai (không có lỗi); 1 = có bước lỗi/thiếu token/không kết luận.
set -u
set +x
umask 077

AUTH_BASE="${XT_AUTH_BASE:-https://xt-auth.freepro.online}"
API_BASE="${XT_API_BASE:-https://xt-api.freepro.online}"
WF_BASE="${XT_WORKFLOW_BASE:-$API_BASE}"
REALM="${XT_REALM:-xt}"
DATA_PREFIX="${XT_DATA_PREFIX:-/data}"
WF_PREFIX="${XT_WORKFLOW_PREFIX:-/workflow/v1}"
WF_PATH="${XT_WORKFLOW_PATH:-$WF_PREFIX/definitions}"
WF_DEF="${XT_WORKFLOW_DEFINITION:-proposal_approval}"
FILES_BASE="${XT_FILES_BASE:-$API_BASE}"
FILES_PREFIX="${XT_FILES_PREFIX:-/files/v1}"
MAX_BODY="${XT_DEMO_MAX_BODY:-600}"
# shellcheck disable=SC2206
CURL_EXTRA=(${XT_CURL_OPTS:-})

if [[ "${1:-}" == "--get-token" ]]; then
  kind="${2:-}"; [[ "$kind" == data || "$kind" == workflow || "$kind" == files ]] || { echo "dùng: $0 --get-token data|workflow|files [tệp]" >&2; exit 1; }
  out="${3:-$HOME/.config/xt/${kind}_token}"; mkdir -p "$(dirname "$out")"
  exec python3 -I - "$AUTH_BASE" "$REALM" "${XT_CLI_CLIENT_ID:-xt-cli}" "xt-$kind" "$out" <<'PY'
import base64, hashlib, http.server, json, secrets, sys, urllib.parse, urllib.request
base, realm, client, scope, out = sys.argv[1:6]
oidc = f"{base}/realms/{realm}/protocol/openid-connect"
ver = secrets.token_urlsafe(64); state = secrets.token_urlsafe(16)
chal = base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).rstrip(b"=").decode()
got = {}
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        got.update({k: v[0] for k, v in q.items()})
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK, quay lai terminal.")
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H)
redir = f"http://127.0.0.1:{srv.server_address[1]}/callback"
print("Mo URL nay bang trinh duyet:\n" + oidc + "/auth?" + urllib.parse.urlencode({
    "client_id": client, "response_type": "code", "scope": f"openid {scope}", "redirect_uri": redir,
    "state": state, "code_challenge": chal, "code_challenge_method": "S256"}))
srv.handle_request()
if got.get("state") != state or "code" not in got:
    sys.exit("dang nhap that bai: " + got.get("error", "state/code khong hop le"))
req = urllib.request.Request(oidc + "/token", urllib.parse.urlencode({
    "grant_type": "authorization_code", "client_id": client, "code": got["code"],
    "redirect_uri": redir, "code_verifier": ver}).encode())
tok = json.load(urllib.request.urlopen(req))["access_token"]
import os
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f: f.write(tok)
print(f"da ghi token {scope} vao {out} (khong in)")
PY
fi

command -v curl >/dev/null || { echo "thiếu curl" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
# Authorization nằm trong tệp, không lên argv (ps) và không in ra. Hai tệp riêng: data / workflow.
HDR_data="$WORK/data.hdr"; HDR_workflow="$WORK/workflow.hdr"; HDR_approver="$WORK/approver.hdr"
HDR_files="$WORK/files.hdr"
: >"$HDR_data"; : >"$HDR_workflow"; : >"$HDR_approver"; : >"$HDR_files"

# load_token <loại> <tên biến FILE> <tên biến trực tiếp>
load_token() {
  local kind="$1" fvar="$2" tvar="$3" f="${!2:-}" t="${!3:-}"
  if [[ -n "$f" ]]; then
    [[ -r "$f" ]] || { echo "không đọc được $fvar" >&2; exit 1; }
    t="$(tr -d '[:space:]' <"$f")"
  elif [[ "$t" =~ [[:space:]] ]]; then
    echo "$tvar chứa khoảng trắng hoặc xuống dòng không hợp lệ" >&2; exit 1
  fi
  # Tệp rỗng không được tính là đã có token; không tạo header Authorization rỗng.
  if [[ -n "$t" ]]; then
    printf 'Authorization: Bearer %s\n' "$t" >"$WORK/$kind.hdr"
  fi
}
load_token data XT_ACCESS_TOKEN_FILE XT_ACCESS_TOKEN
load_token workflow XT_WORKFLOW_TOKEN_FILE XT_WORKFLOW_TOKEN
load_token approver XT_WORKFLOW_APPROVER_TOKEN_FILE XT_WORKFLOW_APPROVER_TOKEN
load_token files XT_FILES_TOKEN_FILE XT_FILES_TOKEN
HAVE_DATA=0; HAVE_WF=0; HAVE_WFA=0; HAVE_FILES=0
[[ -s "$HDR_files" ]] && HAVE_FILES=1
[[ -s "$HDR_data" ]] && HAVE_DATA=1
[[ -s "$HDR_workflow" ]] && HAVE_WF=1
[[ -s "$HDR_approver" ]] && HAVE_WFA=1
[[ -n "${XT_WORKFLOW_START_FILE:-}" || $HAVE_WFA == 1 || $HAVE_FILES == 1 ]] && { command -v python3 >/dev/null || { echo "thiếu python3 (đọc JSON cho bước 4d-4g)" >&2; exit 1; }; }
if [[ ! "$WF_DEF" =~ ^[a-z][a-z0-9_]*$ ]]; then echo "XT_WORKFLOW_DEFINITION sai dạng" >&2; exit 1; fi

RESULTS=()      # "bước|trạng thái"
STATUS=""       # HTTP code của lần gọi gần nhất ("000" = không kết nối)
BODY_FILE="$WORK/body"
REQ_BODY_FILE=""   # nếu khác rỗng: gửi làm thân request (--data-binary @tệp)
REQ_FORM=""        # nếu khác rỗng: multipart (-F "$REQ_FORM")

# json_get <tệp> <đường dẫn>: a.b.0.c; "items.ref=<giá trị>.x" chọn phần tử đầu có ref khớp
# (giá trị rỗng: phần tử approval đầu tiên). In rỗng nếu không có. Đường dẫn rỗng: chỉ kiểm JSON.
json_get() {
  python3 -I - "$1" "$2" <<'PY'
import json, sys
try:
    node = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(1)
for part in [p for p in sys.argv[2].split(".") if p]:
    if "=" in part and isinstance(node, list):
        k, _, v = part.partition("=")
        node = next((x for x in node if isinstance(x, dict) and (x.get(k) == v if v else x.get("kind") == "approval")), None)
    elif isinstance(node, list) and part.isdigit():
        node = node[int(part)] if int(part) < len(node) else None
    elif isinstance(node, dict):
        node = node.get(part)
    else:
        node = None
    if node is None:
        break
print("" if node is None or isinstance(node, (dict, list)) else node)
PY
}

# req <none|data|workflow|approver|files> <METHOD> <URL> [header...] — token đúng loại (nếu có) kèm qua tệp.
req() {
  local kind="$1" method="$2" url="$3"; shift 3
  local args=(-sS -m 20 -o "$BODY_FILE" -w '%{http_code}' -X "$method")
  local h
  for h in "$@"; do args+=(-H "$h"); done
  case "$kind" in
    data) [[ $HAVE_DATA == 1 ]] && args+=(-H "@$HDR_data") ;;
    workflow) [[ $HAVE_WF == 1 ]] && args+=(-H "@$HDR_workflow") ;;
    approver) [[ $HAVE_WFA == 1 ]] && args+=(-H "@$HDR_approver") ;;
    files) [[ $HAVE_FILES == 1 ]] && args+=(-H "@$HDR_files") ;;
  esac
  [[ -n "$REQ_BODY_FILE" ]] && args+=(--data-binary "@$REQ_BODY_FILE")
  [[ -n "$REQ_FORM" ]] && args+=(-F "$REQ_FORM")
  : >"$BODY_FILE"
  STATUS="$(curl "${args[@]}" "${CURL_EXTRA[@]}" "$url" 2>"$WORK/err")" || STATUS="000"
  [[ -z "$STATUS" ]] && STATUS="000"
}

show_body() {
  local n; n="$(wc -c <"$BODY_FILE")"
  if [[ "$n" -eq 0 ]]; then echo "    (thân rỗng)"; return; fi
  echo "    thân ($n byte, cắt ở $MAX_BODY):"
  head -c "$MAX_BODY" "$BODY_FILE" | sed 's/^/    | /'
  echo
}

# not_deployed: dịch vụ không có thật ở đầu kia (kể cả Cloudflare Tunnel 530 / lỗi 1033).
not_deployed() {
  case "$STATUS" in 000|404|502|503|504|530|1033) return 0 ;; esac
  grep -qE 'chua_trien_khai|error code: ?1033' "$BODY_FILE" 2>/dev/null && return 0
  return 1
}

record() { RESULTS+=("$1|$2"); }
say_not_deployed() {
  echo "  => $1: chưa triển khai (HTTP $STATUS)"
  [[ "$STATUS" == "000" ]] && sed 's/^/    curl: /' "$WORK/err"
  show_body
  record "$1" "chưa triển khai"
}

echo "== xT Tầng 1 headless: demo tuần tự 5 bước (không MCP) =="
echo "   auth=$AUTH_BASE api=$API_BASE workflow=$WF_BASE files=$FILES_BASE realm=$REALM"
[[ $HAVE_DATA == 1 ]] && echo "   token data: có (không in)" || echo "   token data: KHÔNG có (đặt XT_ACCESS_TOKEN_FILE)"
[[ $HAVE_WF == 1 ]] && echo "   token workflow: có (không in)" || echo "   token workflow: KHÔNG có (đặt XT_WORKFLOW_TOKEN_FILE)"
[[ $HAVE_WFA == 1 ]] && echo "   token workflow người duyệt: có (không in)"
[[ $HAVE_FILES == 1 ]] && echo "   token files: có (không in)" || echo "   token files: KHÔNG có (đặt XT_FILES_TOKEN_FILE)"

# ---------- 1. SSO ----------
echo
echo "[1/5] SSO Keycloak: OIDC discovery"
req none GET "$AUTH_BASE/realms/$REALM/.well-known/openid-configuration" "Accept: application/json"
if [[ "$STATUS" == "200" ]] && grep -q '"jwks_uri"' "$BODY_FILE"; then
  ISSUER="$(grep -o '"issuer" *: *"[^"]*"' "$BODY_FILE" | head -1 | sed 's/.*: *"//; s/"$//')"
  JWKS_URI="$(grep -o '"jwks_uri" *: *"[^"]*"' "$BODY_FILE" | head -1 | sed 's/.*: *"//; s/"$//')"
  echo "    issuer   = $ISSUER"
  echo "    jwks_uri = $JWKS_URI"
  req none GET "$JWKS_URI" "Accept: application/json"
  KEYS="$(grep -o '"kid"' "$BODY_FILE" | wc -l | tr -d ' ')"
  if [[ "$STATUS" == "200" && "$KEYS" -ge 1 ]]; then
    echo "  => SSO: OK (discovery 200, JWKS có $KEYS khóa)"
    record "1 SSO" "OK"
  elif not_deployed; then
    say_not_deployed "1 SSO"
  else
    echo "  => SSO: JWKS lỗi (HTTP $STATUS, $KEYS khóa)"; show_body
    record "1 SSO" "LỖI"
  fi
elif not_deployed; then
  say_not_deployed "1 SSO"
else
  echo "  => SSO: lỗi (HTTP $STATUS)"; show_body
  record "1 SSO" "LỖI"
fi

# ---------- 2. Gateway ----------
echo
echo "[2/5] API Gateway APISIX: /data không token phải bị từ chối (401/403), không phải 2xx"
req none GET "$API_BASE$DATA_PREFIX/" "Accept: application/json"
if not_deployed; then
  say_not_deployed "2 Gateway"
elif [[ "$STATUS" == "401" || "$STATUS" == "403" ]]; then
  echo "  => Gateway: OK (APISIX/PostgREST trả $STATUS cho yêu cầu không token)"
  record "2 Gateway" "OK"
elif [[ "$STATUS" == 2* ]]; then
  echo "  => Gateway: LỖI BẢO MẬT, /data trả HTTP $STATUS khi không có token"; show_body
  record "2 Gateway" "LỖI"
else
  echo "  => Gateway: kết quả không mong đợi (HTTP $STATUS)"; show_body
  record "2 Gateway" "LỖI"
fi

# ---------- 3. Data ----------
echo
echo "[3/5] Data (PostgREST qua $DATA_PREFIX): đọc view tenant với Accept-Profile"
PARAM_OK=1
if [[ -n "${XT_DATA_MODEL:-}" && ! "$XT_DATA_MODEL" =~ ^[a-z][a-z0-9_]*$ ]]; then PARAM_OK=0; fi
if [[ -n "${XT_DATA_PROFILE:-}" && ! "$XT_DATA_PROFILE" =~ ^api_t[0-9]+$ ]]; then PARAM_OK=0; fi
if [[ $PARAM_OK == 0 ]]; then
  echo "  => Data: XT_DATA_PROFILE phải dạng api_t<seq>, XT_DATA_MODEL dạng chữ thường/số/_ (không gọi curl với tham số sai)"
  record "3 Data" "LỖI (tham số)"
elif [[ $HAVE_DATA == 0 || -z "${XT_DATA_MODEL:-}" || -z "${XT_DATA_PROFILE:-}" ]]; then
  # Thiếu token/model/profile: vẫn gọi curl thật, không token, để biết dịch vụ có hay không.
  MISSING=""
  [[ $HAVE_DATA == 0 ]] && MISSING+=" token(XT_ACCESS_TOKEN_FILE)"
  [[ -z "${XT_DATA_MODEL:-}" ]] && MISSING+=" XT_DATA_MODEL"
  [[ -z "${XT_DATA_PROFILE:-}" ]] && MISSING+=" XT_DATA_PROFILE"
  echo "    thiếu:$MISSING; thăm dò không token, KHÔNG tính là đọc dữ liệu"
  PROBE_PATH="$DATA_PREFIX/${XT_DATA_MODEL:-}"
  PROBE_H=("Accept: application/json")
  [[ -n "${XT_DATA_PROFILE:-}" ]] && PROBE_H+=("Accept-Profile: $XT_DATA_PROFILE")
  req none GET "$API_BASE$PROBE_PATH" "${PROBE_H[@]}"
  if not_deployed; then
    say_not_deployed "3 Data"
  elif [[ "$STATUS" == 2* ]]; then
    echo "  => Data: LỖI BẢO MẬT, không token mà HTTP $STATUS"; show_body
    record "3 Data" "LỖI"
  else
    echo "  => Data: THIẾU$MISSING; dịch vụ có trả lời (HTTP $STATUS) nhưng chưa kiểm đọc dữ liệu"
    record "3 Data" "BỎ QUA (thiếu${MISSING})"
  fi
else
  req data GET "$API_BASE$DATA_PREFIX/$XT_DATA_MODEL?limit=5" "Accept: application/json" "Accept-Profile: $XT_DATA_PROFILE"
  if not_deployed; then
    say_not_deployed "3 Data"
  elif [[ "$STATUS" == "200" ]]; then
    echo "  => Data: OK (HTTP 200, profile $XT_DATA_PROFILE)"; show_body
    D_RES="OK"
    if [[ -n "${XT_DATA_FOREIGN_PROFILE:-}" ]]; then
      if [[ ! "$XT_DATA_FOREIGN_PROFILE" =~ ^api_t[0-9]+$ ]]; then
        echo "    XT_DATA_FOREIGN_PROFILE sai dạng"; D_RES="LỖI (tham số)"
      else
        req data GET "$API_BASE$DATA_PREFIX/$XT_DATA_MODEL?limit=5" "Accept: application/json" "Accept-Profile: $XT_DATA_FOREIGN_PROFILE"
        case "$STATUS" in
          401|403|406)
            echo "    kiểm chéo tenant: profile $XT_DATA_FOREIGN_PROFILE bị từ chối (HTTP $STATUS), đúng mong đợi" ;;
          2*)
            echo "    LỖI CÔ LẬP: token đọc được profile tenant khác $XT_DATA_FOREIGN_PROFILE (HTTP $STATUS)"; show_body
            D_RES="LỖI (cô lập tenant)" ;;
          *)
            echo "    kiểm chéo tenant KHÔNG KẾT LUẬN (HTTP $STATUS): chỉ 401/403/406 là từ chối; 5xx/timeout không phải bằng chứng bảo mật"; show_body
            D_RES="LỖI (kiểm chéo tenant không kết luận)" ;;
        esac
      fi
    fi
    record "3 Data" "$D_RES"
  else
    echo "  => Data: không đọc được (HTTP $STATUS; 401 token/aud/issuer sai, 403 profile không thuộc tenant, 406 schema chưa có)"; show_body
    record "3 Data" "LỖI"
  fi
fi

# ---------- 4. Workflow ----------
# REST /workflow/v1 (docs/workflow-api.md): định nghĩa + BPMN, khởi động W1, instance, inbox, quyết định.
# Mọi bước dùng token workflow (aud xt-workflow) của agent đại diện người; bước duyệt luôn trả 428 +
# confirm_url: người duyệt bấm trên trang xác nhận, script KHÔNG tự duyệt thay người.
echo
echo "[4/5] Workflow ($WF_BASE$WF_PREFIX): dùng token workflow riêng, không dùng token data"
W_RES="OK"
wf_fail() { echo "  => Workflow: $1 (HTTP $STATUS)"; show_body; W_RES="LỖI"; }
if [[ $HAVE_WF == 0 ]]; then
  echo "    thiếu token workflow (XT_WORKFLOW_TOKEN_FILE); thăm dò không token, KHÔNG gửi token data"
  req none GET "$WF_BASE$WF_PATH" "Accept: application/json"
  if not_deployed; then
    say_not_deployed "4 Workflow"
  elif [[ "$STATUS" == 2* ]]; then
    echo "  => Workflow: LỖI BẢO MẬT, không token mà HTTP $STATUS"; show_body
    record "4 Workflow" "LỖI"
  else
    echo "  => Workflow: THIẾU token workflow; dịch vụ có trả lời (HTTP $STATUS) nhưng chưa kiểm gọi thật"
    record "4 Workflow" "BỎ QUA (thiếu token workflow)"
  fi
else
  # 4a. Không token phải bị 401 ngay ở xt-api.
  req none GET "$WF_BASE$WF_PATH" "Accept: application/json"
  if not_deployed; then
    say_not_deployed "4 Workflow"
    W_RES=""
  elif [[ "$STATUS" != "401" ]]; then
    wf_fail "không token mà không phải 401"
  else
    echo "    4a không token: 401 (đúng)"
  fi
  # 4b. Danh sách định nghĩa + tải BPMN (BPMNDI) của $WF_DEF.
  if [[ "$W_RES" == "OK" ]]; then
    req workflow GET "$WF_BASE$WF_PATH" "Accept: application/json"
    if [[ "$STATUS" == "200" ]] && grep -q "\"id\":\"$WF_DEF\"" "$BODY_FILE"; then
      echo "    4b definitions: 200, có $WF_DEF"
      req workflow GET "$WF_BASE$WF_PREFIX/definitions/$WF_DEF/bpmn" "Accept: application/xml"
      if [[ "$STATUS" == "200" ]] && grep -q "BPMNDiagram" "$BODY_FILE"; then
        cp "$BODY_FILE" "$WORK/$WF_DEF.bpmn"
        [[ -n "${XT_WORKFLOW_BPMN_OUT:-}" ]] && cp "$BODY_FILE" "$XT_WORKFLOW_BPMN_OUT"
        echo "    4c BPMN: 200, $(wc -c <"$BODY_FILE" | tr -d ' ') byte, có BPMNDI${XT_WORKFLOW_BPMN_OUT:+ (lưu $XT_WORKFLOW_BPMN_OUT)}"
      else
        wf_fail "tải BPMN lỗi"
      fi
    else
      wf_fail "definitions lỗi hoặc thiếu $WF_DEF"
    fi
  fi
  # 4d. Khởi động W1 (ghi dữ liệu thật): chỉ khi có XT_WORKFLOW_START_FILE (JSON body POST /instances).
  REQ_REF=""
  if [[ "$W_RES" == "OK" && -n "${XT_WORKFLOW_START_FILE:-}" ]]; then
    if [[ ! -r "$XT_WORKFLOW_START_FILE" ]] || ! json_get "$XT_WORKFLOW_START_FILE" "" >/dev/null; then
      echo "    XT_WORKFLOW_START_FILE không đọc được hoặc không phải JSON"; W_RES="LỖI (tham số)"
    else
      IDEM="${XT_WORKFLOW_IDEMPOTENCY_KEY:-demo-$(date +%s)-$RANDOM}"
      REQ_BODY_FILE="$XT_WORKFLOW_START_FILE"
      req workflow POST "$WF_BASE$WF_PREFIX/instances" "Content-Type: application/json" "Idempotency-Key: $IDEM"
      REQ_BODY_FILE=""
      if [[ "$STATUS" == "201" || "$STATUS" == "200" ]]; then
        IID="$(json_get "$BODY_FILE" data.instance.id)"
        REQ_REF="$(json_get "$BODY_FILE" data.request.ref)"
        echo "    4d POST /instances (Idempotency-Key $IDEM): $STATUS, instance $IID, đề nghị $REQ_REF"
        req workflow GET "$WF_BASE$WF_PREFIX/instances/$IID" "Accept: application/json"
        if [[ "$STATUS" == "200" ]]; then
          echo "    4e GET /instances/$IID: 200, trạng thái $(json_get "$BODY_FILE" data.instance.status), bước $(json_get "$BODY_FILE" data.instance.tasks.0.step)"
        else
          wf_fail "đọc instance lỗi"
        fi
      elif [[ "$STATUS" == "428" ]]; then
        echo "    4d cần người xác nhận: $(json_get "$BODY_FILE" error.details.confirm_url)"; W_RES="CHỜ XÁC NHẬN"
      else
        wf_fail "POST /instances lỗi"
      fi
    fi
  elif [[ "$W_RES" == "OK" ]]; then
    echo "    4d bỏ qua khởi động W1 (đặt XT_WORKFLOW_START_FILE để ghi dữ liệu thật)"
  fi
  # 4f. Inbox người duyệt + quyết định: agent luôn nhận 428 confirmation_required + confirm_url.
  if [[ "$W_RES" == "OK" && $HAVE_WFA == 1 ]]; then
    req approver GET "$WF_BASE$WF_PREFIX/inbox?kinds=approval" "Accept: application/json"
    if [[ "$STATUS" != "200" ]]; then
      wf_fail "inbox người duyệt lỗi"
    else
      ITEM_REF="$(json_get "$BODY_FILE" "data.items.ref=${REQ_REF}.ref")"
      ITEM_REV="$(json_get "$BODY_FILE" "data.items.ref=${REQ_REF}.revision")"
      if [[ -z "$ITEM_REF" ]]; then
        echo "    4f inbox người duyệt: 200, không có việc duyệt${REQ_REF:+ cho $REQ_REF}"
      else
        echo "    4f inbox người duyệt: 200, việc duyệt $ITEM_REF (revision $ITEM_REV)"
        printf '{"decision":"%s","expected_revision":%s}' "${XT_WORKFLOW_DECISION:-approve}" "$ITEM_REV" >"$WORK/decision.json"
        REQ_BODY_FILE="$WORK/decision.json"
        DKEY="${XT_WORKFLOW_DECISION_KEY:-demo-dec-$(date +%s)-$RANDOM}"
        req approver POST "$WF_BASE$WF_PREFIX/tasks/$ITEM_REF/decision" "Content-Type: application/json" "Idempotency-Key: $DKEY"
        REQ_BODY_FILE=""
        if [[ "$STATUS" == "428" ]] && grep -q '"confirmation_required"' "$BODY_FILE"; then
          echo "    4g quyết định qua REST: 428 confirmation_required (agent KHÔNG tự duyệt được)"
          echo "       người duyệt mở: $(json_get "$BODY_FILE" error.details.confirm_url)"
          echo "       sau khi xác nhận, gửi lại đúng request với Idempotency-Key: $DKEY"
        elif [[ "$STATUS" == "200" ]]; then
          echo "    4g quyết định: 200 (đã được người xác nhận trước đó), kết quả $(json_get "$BODY_FILE" data.outcome)"
        else
          wf_fail "quyết định lỗi"
        fi
      fi
    fi
  elif [[ "$W_RES" == "OK" ]]; then
    echo "    4f bỏ qua inbox/quyết định (đặt XT_WORKFLOW_APPROVER_TOKEN_FILE)"
  fi
  if [[ -n "$W_RES" ]]; then
    [[ "$W_RES" == "OK" ]] && echo "  => Workflow: OK"
    record "4 Workflow" "$W_RES"
  fi
fi

# ---------- 5. Files ----------
# REST /files/v1 (docs/files-api.md): byte KHÔNG đi qua LLM/MCP. Ý định (JSON) -> put_url ký HMAC hạn
# ngắn (PUT không Bearer) -> finalize với sha256 -> tải về và so sha256; rồi multipart một bước.
echo
echo "[5/5] Files ($FILES_BASE$FILES_PREFIX): dữ liệu phi cấu trúc trên SeaweedFS, token files riêng"
F_RES="OK"
f_fail() { echo "  => Files: $1 (HTTP $STATUS)"; show_body; F_RES="LỖI"; }
sha256_of() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
if [[ $HAVE_FILES == 0 ]]; then
  echo "    thiếu token files (XT_FILES_TOKEN_FILE); thăm dò không token, KHÔNG gửi token khác"
  req none GET "$FILES_BASE$FILES_PREFIX" "Accept: application/json"
  if not_deployed; then
    say_not_deployed "5 Files"
  elif [[ "$STATUS" == 2* ]]; then
    echo "  => Files: LỖI BẢO MẬT, không token mà HTTP $STATUS"; show_body
    record "5 Files" "LỖI"
  else
    echo "  => Files: THIẾU token files; dịch vụ có trả lời (HTTP $STATUS) nhưng chưa kiểm tải lên/tải về"
    record "5 Files" "BỎ QUA (thiếu token files)"
  fi
else
  req none GET "$FILES_BASE$FILES_PREFIX" "Accept: application/json"
  if not_deployed; then
    say_not_deployed "5 Files"; F_RES=""
  elif [[ "$STATUS" != "401" ]]; then
    f_fail "không token mà không phải 401"
  else
    echo "    5a không token: 401 (đúng)"
  fi
  UP="${XT_FILES_UPLOAD:-$WORK/xt-demo.txt}"
  UP_TYPE="${XT_FILES_UPLOAD_TYPE:-text/plain}"
  [[ -z "${XT_FILES_UPLOAD:-}" ]] && printf 'xT headless demo %s\nTệp này đi qua /files/v1, không qua LLM.\n' "$(date -u +%FT%TZ)" >"$UP"
  if [[ "$F_RES" == "OK" && ! -r "$UP" ]]; then echo "    XT_FILES_UPLOAD không đọc được"; F_RES="LỖI (tham số)"; fi
  UP_NAME="$(basename "$UP")"
  if [[ "$F_RES" == "OK" && ! "$UP_NAME" =~ ^[A-Za-z0-9._-]{1,200}$ ]]; then echo "    tên tệp chỉ dùng [A-Za-z0-9._-] trong demo"; F_RES="LỖI (tham số)"; fi
  if [[ "$F_RES" == "OK" ]]; then
    UP_SHA="$(sha256_of "$UP")"
    printf '{"name":"%s","content_type":"%s"}' "$UP_NAME" "$UP_TYPE" >"$WORK/intent.json"
    REQ_BODY_FILE="$WORK/intent.json"
    req files POST "$FILES_BASE$FILES_PREFIX" "Content-Type: application/json"
    REQ_BODY_FILE=""
    if [[ "$STATUS" != "201" ]]; then
      f_fail "tạo ý định tải lên lỗi"
    else
      FID="$(json_get "$BODY_FILE" data.file.id)"; PUT_URL="$(json_get "$BODY_FILE" data.put_url)"
      echo "    5b POST $FILES_PREFIX: 201, tệp $FID, put_url hết hạn $(json_get "$BODY_FILE" data.expires_at)"
      REQ_BODY_FILE="$UP"
      req none PUT "$PUT_URL" "Content-Type: $UP_TYPE"
      REQ_BODY_FILE=""
      if [[ "$STATUS" != "200" ]]; then
        f_fail "presigned PUT lỗi"
      else
        echo "    5c PUT put_url (không Bearer): 200, server tính sha256 $(json_get "$BODY_FILE" data.file.sha256)"
        printf '{"sha256":"%s"}' "$UP_SHA" >"$WORK/finalize.json"
        REQ_BODY_FILE="$WORK/finalize.json"
        req files POST "$FILES_BASE$FILES_PREFIX/$FID/finalize" "Content-Type: application/json"
        REQ_BODY_FILE=""
        if [[ "$STATUS" != "200" ]]; then
          f_fail "finalize lỗi"
        else
          echo "    5d finalize (sha256 khớp): 200, trạng thái $(json_get "$BODY_FILE" data.file.status)"
          req files GET "$FILES_BASE$FILES_PREFIX/$FID/content"
          if [[ "$STATUS" == "200" && "$(sha256_of "$BODY_FILE")" == "$UP_SHA" ]]; then
            echo "    5e GET .../content: 200, $(wc -c <"$BODY_FILE" | tr -d ' ') byte, sha256 khớp"
          else
            f_fail "tải về lỗi hoặc sha256 không khớp"
          fi
        fi
      fi
    fi
  fi
  if [[ "$F_RES" == "OK" ]]; then
    REQ_FORM="file=@$UP;type=$UP_TYPE"
    req files POST "$FILES_BASE$FILES_PREFIX"
    REQ_FORM=""
    if [[ "$STATUS" == "201" ]]; then
      echo "    5f multipart một bước: 201, tệp $(json_get "$BODY_FILE" data.file.id) ($(json_get "$BODY_FILE" data.file.status))"
    else
      f_fail "multipart lỗi"
    fi
  fi
  if [[ -n "$F_RES" ]]; then
    [[ "$F_RES" == "OK" ]] && echo "  => Files: OK"
    record "5 Files" "$F_RES"
  fi
fi

# ---------- Tổng kết ----------
echo
echo "== Tổng kết =="
RC=0
for r in "${RESULTS[@]}"; do
  printf '  %-12s %s\n' "${r%%|*}" "${r#*|}"
  case "${r#*|}" in
    OK) ;;
    "chưa triển khai") [[ $RC -eq 0 ]] && RC=2 ;;
    *) RC=1 ;;
  esac
done
exit "$RC"
