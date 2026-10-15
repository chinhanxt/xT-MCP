#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
#
# Demo Tầng 1 headless, KHÔNG dùng MCP: gọi tuần tự 4 dịch vụ qua gateway bằng curl.
#   1. SSO       : OIDC discovery + JWKS của Keycloak
#   2. Gateway   : APISIX (Host xt-api) trả lời, và /data không token bị từ chối
#   3. Data      : GET /data/<model> với Accept-Profile: api_t<seq>
#   4. Workflow  : GET /workflow/v1/definitions
# Luôn gọi curl cả 4 bước (kể cả thiếu token/model/profile: thăm dò không token rồi báo THIẾU, không phải thành công).
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
#   XT_DATA_PREFIX    mặc định /data
#   XT_DATA_MODEL     tên model/view cần đọc (bắt buộc cho bước 3)
#   XT_DATA_PROFILE   schema tenant, dạng api_t<seq> (bắt buộc cho bước 3)
#   XT_DATA_FOREIGN_PROFILE  (tùy chọn) schema tenant khác; bước 3 kiểm phải bị từ chối
#   XT_WORKFLOW_PATH  mặc định /workflow/v1/definitions
#   XT_DEMO_MAX_BODY  số ký tự thân phản hồi được in, mặc định 600
#   XT_CURL_OPTS      tùy chọn curl thêm, ví dụ "--resolve xt-api.freepro.online:443:127.0.0.1"
# Mã thoát: 0 = cả 4 bước thật sự thành công; 2 = chỉ có bước chưa triển khai (không có lỗi); 1 = có bước lỗi/thiếu token/không kết luận.
set -u
set +x
umask 077

AUTH_BASE="${XT_AUTH_BASE:-https://xt-auth.freepro.online}"
API_BASE="${XT_API_BASE:-https://xt-api.freepro.online}"
WF_BASE="${XT_WORKFLOW_BASE:-$API_BASE}"
REALM="${XT_REALM:-xt}"
DATA_PREFIX="${XT_DATA_PREFIX:-/data}"
WF_PATH="${XT_WORKFLOW_PATH:-/workflow/v1/definitions}"
MAX_BODY="${XT_DEMO_MAX_BODY:-600}"
# shellcheck disable=SC2206
CURL_EXTRA=(${XT_CURL_OPTS:-})

command -v curl >/dev/null || { echo "thiếu curl" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
# Authorization nằm trong tệp, không lên argv (ps) và không in ra. Hai tệp riêng: data / workflow.
HDR_data="$WORK/data.hdr"; HDR_workflow="$WORK/workflow.hdr"
: >"$HDR_data"; : >"$HDR_workflow"

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
HAVE_DATA=0; HAVE_WF=0
[[ -s "$HDR_data" ]] && HAVE_DATA=1
[[ -s "$HDR_workflow" ]] && HAVE_WF=1

RESULTS=()      # "bước|trạng thái"
STATUS=""       # HTTP code của lần gọi gần nhất ("000" = không kết nối)
BODY_FILE="$WORK/body"

# req <none|data|workflow> <METHOD> <URL> [header...] — token đúng loại (nếu có) kèm qua tệp.
req() {
  local kind="$1" method="$2" url="$3"; shift 3
  local args=(-sS -m 20 -o "$BODY_FILE" -w '%{http_code}' -X "$method")
  local h
  for h in "$@"; do args+=(-H "$h"); done
  case "$kind" in
    data) [[ $HAVE_DATA == 1 ]] && args+=(-H "@$HDR_data") ;;
    workflow) [[ $HAVE_WF == 1 ]] && args+=(-H "@$HDR_workflow") ;;
  esac
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

echo "== xT Tầng 1 headless: demo tuần tự 4 dịch vụ (không MCP) =="
echo "   auth=$AUTH_BASE api=$API_BASE workflow=$WF_BASE realm=$REALM"
[[ $HAVE_DATA == 1 ]] && echo "   token data: có (không in)" || echo "   token data: KHÔNG có (đặt XT_ACCESS_TOKEN_FILE)"
[[ $HAVE_WF == 1 ]] && echo "   token workflow: có (không in)" || echo "   token workflow: KHÔNG có (đặt XT_WORKFLOW_TOKEN_FILE)"

# ---------- 1. SSO ----------
echo
echo "[1/4] SSO Keycloak: OIDC discovery"
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
echo "[2/4] API Gateway APISIX: /data không token phải bị từ chối (401/403), không phải 2xx"
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
echo "[3/4] Data (PostgREST qua $DATA_PREFIX): đọc view tenant với Accept-Profile"
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
echo
echo "[4/4] Workflow ($WF_BASE$WF_PATH): dùng token workflow riêng, không dùng token data"
if [[ $HAVE_WF == 1 ]]; then
  req workflow GET "$WF_BASE$WF_PATH" "Accept: application/json"
  if not_deployed; then
    say_not_deployed "4 Workflow"
  elif [[ "$STATUS" == "200" ]]; then
    echo "  => Workflow: OK (HTTP 200)"; show_body
    record "4 Workflow" "OK"
  else
    echo "  => Workflow: HTTP $STATUS"; show_body
    record "4 Workflow" "LỖI"
  fi
else
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
