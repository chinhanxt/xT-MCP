#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Spike HITL: Keycloak 26.6.3 (image có sẵn) cổng 19300, channel 19301. Chạy: bash run_all.sh ; dọn: bash run_all.sh clean
set -euo pipefail
W=/tmp/xt-spike-hitl; D=$(cd "$(dirname "$0")" && pwd); PY=$W/venv/bin/python
if [[ "${1:-}" == clean ]]; then
  docker rm -f xt-hitl-kc >/dev/null 2>&1 || true; pkill -f "[c]hannel.py --xt-hitl" || true; rm -rf "$W"; exit 0
fi
mkdir -p $W
[[ -x $PY ]] || { python3 -m venv $W/venv && $W/venv/bin/pip -q install pyotp==2.10.0 httpx==0.28.1 pyjwt==2.15.1 cryptography==50.0.2; }
if ! docker ps --format '{{.Names}}' | grep -qx xt-hitl-kc; then
  docker run -d --name xt-hitl-kc -p 19300:8080 --add-host host.docker.internal:host-gateway \
    -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
    quay.io/keycloak/keycloak:26.6.3 start-dev \
    --spi-ciba-auth-channel--ciba-http-auth-channel--http-authentication-channel-uri=http://host.docker.internal:19301/ciba/auth-channel
  until curl -sf http://localhost:19300/realms/master >/dev/null; do sleep 2; done
fi
$PY $D/kc_setup.py
pgrep -f "[c]hannel.py --xt-hitl" >/dev/null || { nohup $PY -u $D/channel.py --xt-hitl > $W/channel.log 2>&1 & sleep 1; }
$PY $D/ciba_flow.py   | tee $W/ciba.out
$PY $D/receipt.py $W/evidence_ciba.json | tee $W/receipt_ciba.out
$PY $D/stepup_flow.py | tee $W/stepup.out
$PY $D/receipt.py $W/evidence_stepup.json | tee $W/receipt_stepup.out
$PY $D/ciba_acr_probe.py | tee $W/ciba_acr.out
