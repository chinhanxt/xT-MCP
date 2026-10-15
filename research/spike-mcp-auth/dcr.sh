#!/usr/bin/env bash
# Usage: dcr.sh <name> <redirect_uri>   -> in JSON kết quả DCR
curl -s -i -X POST http://localhost:18080/realms/xt/clients-registrations/openid-connect \
  -H 'Content-Type: application/json' \
  -d "{\"client_name\":\"$1\",\"redirect_uris\":[\"$2\"],\"grant_types\":[\"authorization_code\",\"refresh_token\"],\"response_types\":[\"code\"],\"token_endpoint_auth_method\":\"none\"}"
