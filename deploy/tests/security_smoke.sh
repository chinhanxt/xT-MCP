#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
#
# Regression an ninh cho hạ tầng deploy/ (docs/audit-deploy.md).
# Yêu cầu: stack đang chạy (postgres + keycloak + apisix) qua `make up-minimal` + `make provision-realm`.
#
#   GW=127.0.0.1:9080 ./deploy/tests/security_smoke.sh
#
# BUG-D1 (DCR grant_type/auth method) và BUG-D2 (DCR rate-limit) đã sửa: các check tương ứng phải PASS.
# Mỗi request DCR dùng IP giả riêng qua CF-Connecting-IP (APISIX chỉ tin header này từ mạng nội bộ)
# để không dùng chung hạn mức limit-count 20/giờ/IP; riêng phần flood dùng một IP cố định.
set -u
GW="${GW:-127.0.0.1:9080}"
AUTH="${XT_DOMAIN_AUTH:-xt-auth.freepro.online}"
REG="http://$GW/realms/${XT_REALM:-xt}/clients-registrations/openid-connect"
TOK="http://$GW/realms/${XT_REALM:-xt}/protocol/openid-connect/token"
pass=0; fail=0
ok()   { echo "  PASS  $1"; pass=$((pass+1)); }
ko()   { echo "  FAIL  $1"; fail=$((fail+1)); }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
rip()  { echo "203.0.113.$((RANDOM%250+1)).$RANDOM" | cut -d. -f1-4; }
CREATED="$(mktemp)"; trap 'cleanup' EXIT
# DCR hợp lệ mặc định (public, auth-code, PKCE): thêm "$extra" để thử biến thể
body(){ printf '{"client_name":"smoke","redirect_uris":%s,"token_endpoint_auth_method":"none"%s}' "$1" "${2:+,$2}"; }
jpost(){ curl -s -H "Host: $AUTH" -H "CF-Connecting-IP: $(rip)" -H 'Content-Type: application/json' -X POST "$REG" -d "$1" | tee -a "$CREATED.raw"; }
dpost(){ # $1=IP $2=body -> mã HTTP; body ghi vào $CREATED.raw để dọn client
  curl -s -o "$CREATED.one" -w '%{http_code}' -H "Host: $AUTH" -H "CF-Connecting-IP: $1" -H 'Content-Type: application/json' -X POST "$REG" -d "$2"
  cat "$CREATED.one" >> "$CREATED.raw"; }
dcode(){ dpost "$(rip)" "$1"; }
cleanup() {  # xóa client DCR do smoke tạo (registration_client_uri + registrationAccessToken)
  [ -f "$CREATED.raw" ] || return 0
  python3 - "$CREATED.raw" "$GW" "$AUTH" <<'PY' 2>/dev/null
import sys,re,json,urllib.request
raw=open(sys.argv[1]).read(); gw,auth=sys.argv[2],sys.argv[3]
for m in re.finditer(r'\{.*?\}(?=\{|$)',raw,re.S):
    try: d=json.loads(m.group(0))
    except Exception: continue
    u,t=d.get("registration_client_uri"),d.get("registration_access_token")
    if u and t:
        u=re.sub(r'^https://[^/]+',"http://"+gw,u)
        try: urllib.request.urlopen(urllib.request.Request(u,method="DELETE",headers={"Host":auth,"Authorization":"Bearer "+t,"CF-Connecting-IP":"203.0.113."+str(__import__("random").randint(1,250))}))
        except Exception: pass
PY
  rm -f "$CREATED" "$CREATED.raw" "$CREATED.one"
}

echo "== APISIX: chặn admin/master/metrics/health kể cả khi lách URL-encode/traversal =="
for p in "/admin/master/console/" "/realms/master" "/metrics" "/health" \
         "/%61dmin/master/console/" "//admin/master/console/" "/x/../admin/master/console/" \
         "/admin%2fmaster" "/realms%2fmaster"; do
  c=$(code -H "Host: $AUTH" "http://$GW$p")
  case "$c" in 200) ko "lọt admin: $p -> $c";; *) ok "chặn $p -> $c";; esac
done

echo "== APISIX: host lạ -> 404, không có bearer tĩnh, admin API tắt =="
[ "$(code -H 'Host: evil.invalid' "http://$GW/")" = 404 ] && ok "host lạ 404" || ko "host lạ không 404"
[ "$(code -H "Host: $AUTH" "http://$GW/apisix/admin/routes")" != 200 ] && ok "admin API APISIX tắt" || ko "admin API APISIX lộ"

echo "== Keycloak: issuer không bị X-Forwarded-Host đầu độc =="
iss=$(curl -s -H "Host: $AUTH" -H "X-Forwarded-Host: evil.attacker.com" \
      "http://$GW/realms/${XT_REALM:-xt}/.well-known/openid-configuration" \
      | python3 -c 'import sys,json;print(json.load(sys.stdin).get("issuer",""))' 2>/dev/null)
case "$iss" in *evil*) ko "issuer bị spoof: $iss";; https://$AUTH/*) ok "issuer cố định: $iss";; *) ko "issuer bất ngờ: $iss";; esac

echo "== DCR: redirect ngoài host tin cậy bị từ chối =="
for u in "https://evil.example/cb" "https://evil.chatgpt.com/cb" "https://chatgpt.com.evil.com/cb"; do
  c=$(dcode "$(body "[\"$u\"]")")
  [ "$c" = 403 ] && ok "chặn redirect $u" || ko "nhận redirect $u -> $c"
done
# mix hợp lệ + độc -> phải 403 (tất cả URI phải khớp)
c=$(dcode "$(body '["https://claude.ai/api/mcp/auth_callback","https://evil.example/cb"]')")
[ "$c" = 403 ] && ok "chặn mix allowed+evil" || ko "nhận mix allowed+evil -> $c"

echo "== DCR: không leo thang bằng protocol mapper / consent =="
r=$(jpost "$(body '["https://claude.ai/api/mcp/auth_callback"]' '"protocol_mappers":[{"name":"ha","protocol":"openid-connect","protocolMapper":"oidc-hardcoded-role-mapper","config":{"role":"admin"}}]')")
echo "$r" | grep -q 'protocol_mappers' && ok "DCR từ chối protocol_mappers" || ko "DCR nhận protocol_mappers: $(echo "$r"|head -c120)"

echo "== BUG-D1: DCR chỉ cấp client public auth-code (PKCE), không client_credentials/password/implicit =="
CB='["https://claude.ai/api/mcp/auth_callback"]'
exp(){ # tên, mã mong đợi (regex), body
  c=$(dcode "$3"); echo "$c" | grep -Eq "^($2)$" && ok "$1 -> $c" || ko "$1 -> $c (cần $2)"; }
exp "grant client_credentials bị chặn" "400|403" "$(body "$CB" '"grant_types":["client_credentials"]')"
exp "grant password (ROPC) bị chặn" "400|403" "$(body "$CB" '"grant_types":["password"]')"
exp "grant implicit + response_type token bị chặn" "400|403" "$(body "$CB" '"grant_types":["implicit"],"response_types":["token"]')"
exp "thiếu token_endpoint_auth_method (client confidential) bị chặn" "400|403" '{"redirect_uris":["https://claude.ai/api/mcp/auth_callback"]}'
exp "auth method client_secret_basic bị chặn" "400|403" '{"redirect_uris":["https://claude.ai/api/mcp/auth_callback"],"token_endpoint_auth_method":"client_secret_basic"}'
exp "3 redirect URI bị chặn (tối đa 2)" "400|403" "$(body '["https://claude.ai/a","https://claude.ai/b","https://claude.ai/c"]')"
exp "wildcard redirect bị chặn" "400|403" "$(body '["https://chatgpt.com/*"]')"
exp "http không-loopback bị chặn" "400|403" "$(body '["http://chatgpt.com/cb"]')"
exp "javascript: redirect -> 4xx (không 500)" "400|403" "$(body '["javascript:alert(1)"]')"
exp "localhost (không phải 127.0.0.1) bị chặn" "400|403" "$(body '["http://localhost:3333/cb"]')"
exp "connector hợp lệ (claude.ai) vẫn 201" "201" "$(body "$CB" '"grant_types":["authorization_code","refresh_token"],"response_types":["code"]')"
exp "connector hợp lệ (chatgpt.com) vẫn 201" "201" "$(body '["https://chatgpt.com/connector_platform_oauth_redirect"]')"
exp "loopback 127.0.0.1 (Claude Code) vẫn 201" "201" "$(body '["http://127.0.0.1:33333/callback"]')"
# PKCE: client vừa DCR phải có pkce.code.challenge.method=S256 (đọc bằng service account view-clients)
ENVF="$(dirname "$0")/../.env"; envv(){ sed -n "s/^$1=\"\?\([^\" #]*\).*/\1/p" "$ENVF" 2>/dev/null | head -1; }
KCDIRECT="http://127.0.0.1:$(envv KC_HTTP_PORT)"; RID=$(envv XT_IDP_ADMIN_CLIENT_ID); RSEC=$(envv XT_IDP_ADMIN_CLIENT_SECRET)
newc=$(jpost "$(body '["https://claude.ai/api/mcp/auth_callback"]')" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("client_id",""))' 2>/dev/null)
rt=$(curl -s -X POST "$KCDIRECT/realms/${XT_REALM:-xt}/protocol/openid-connect/token" -d grant_type=client_credentials -d "client_id=$RID" --data-urlencode "client_secret=$RSEC" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null)
if [ -n "$rt" ] && [ -n "$newc" ]; then
  pk=$(curl -s -H "Authorization: Bearer $rt" "$KCDIRECT/admin/realms/${XT_REALM:-xt}/clients?clientId=$newc" | python3 -c 'import sys,json;d=json.load(sys.stdin)[0];print(d["attributes"].get("pkce.code.challenge.method"),d["publicClient"],d["fullScopeAllowed"],d.get("serviceAccountsEnabled"),d["directAccessGrantsEnabled"],len(d.get("protocolMappers") or []))' 2>/dev/null)
  [ "$pk" = "S256 True False False False 0" ] && ok "client DCR mới: pkce=S256, public, fullScope=false, không SA/ROPC/mapper ($pk)" || ko "thuộc tính client DCR sai: $pk"
  c=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $rt" "$KCDIRECT/admin/realms/${XT_REALM:-xt}/users")
  [ "$c" = 403 ] && ok "service account registry không đọc được /users (403)" || ko "registry đọc được /users ($c)"
  c=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE -H "Authorization: Bearer $rt" "$KCDIRECT/admin/realms/${XT_REALM:-xt}/clients/$newc")
  [ "$c" = 403 ] && ok "service account registry không xóa/sửa được client (403)" || ko "registry sửa được client ($c)"
else ko "không lấy được token registry hoặc client DCR (cần make provision-realm)"; fi
# client DCR hợp lệ không mint được token client_credentials (public client)
cc=$(jpost "$(body "$CB")")
cid=$(echo "$cc"|python3 -c 'import sys,json;print(json.load(sys.stdin).get("client_id",""))' 2>/dev/null)
c=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$TOK" -H "Host: $AUTH" -d grant_type=client_credentials -d scope=mcp-xt -d "client_id=$cid")
case "$c" in 4*) ok "client DCR không lấy được token client_credentials ($c)";; *) ko "client DCR lấy được token client_credentials ($c)";; esac
c=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$TOK" -H "Host: $AUTH" -d grant_type=password -d username=x -d password=y -d "client_id=$cid")
case "$c" in 4*) ok "client DCR không dùng được ROPC ($c)";; *) ko "client DCR dùng được ROPC ($c)";; esac

echo "== BUG-D2: DCR và token endpoint bị rate-limit theo IP tại gateway =="
n=0; hit=0; for i in $(seq 1 26); do
  c=$(dpost 198.51.100.77 "$(body "$CB")")
  [ "$c" = 201 ] && n=$((n+1)); [ "$c" = 429 ] && { hit=1; break; }
done
[ "$hit" = 1 ] && ok "DCR flood 1 IP bị 429 sau $n lần 201" || ko "26 DCR liên tiếp, không 429 ($n lần 201)"
c=$(dpost 198.51.100.78 "$(body "$CB")")
[ "$c" = 201 ] && ok "IP khác không bị ảnh hưởng ($c)" || ko "IP khác bị chặn lây ($c)"
t429=0; for i in $(seq 1 80); do
  c=$(code -X POST "$TOK" -H "Host: $AUTH" -H "CF-Connecting-IP: 198.51.100.79" -d grant_type=client_credentials -d client_id=none)
  [ "$c" = 429 ] && { t429=1; break; }
done
[ "$t429" = 1 ] && ok "token endpoint flood bị 429" || ko "token endpoint: 80 request liên tiếp không 429"
c=$(code -X POST "$TOK" -H "Host: $AUTH" -H "CF-Connecting-IP: 198.51.100.80" -H 'Content-Type: application/json' --data-binary "$(head -c 40000 /dev/zero | tr '\0' a)")
[ "$c" = 413 ] && ok "body quá lớn bị 413" || ko "body 40 KB tới token endpoint không bị 413 ($c)"

echo
echo "PASS=$pass FAIL=$fail"
[ "$fail" -eq 0 ]
