#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# Chạy toàn bộ kiểm chứng: Postgres tạm (docker, cổng 15440) -> compile model -> DB/RLS -> IAM -> SpiffWorkflow.
# Cần: docker, python venv có psycopg[binary], psycopg-pool, pydantic, PyYAML, spiffworkflow==3.2.0, lxml.
#   PY=/đường/dẫn/python ./run_all.sh
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESEARCH="${XT_RESEARCH:-$(cd "$HERE/../.." && pwd)}"
PY="${PY:-python3}"
OUT="${OUT:-$HERE/../results}"
mkdir -p "$OUT"
df -h / | tail -1
docker rm -f xt-packs-pg >/dev/null 2>&1
docker run -d --name xt-packs-pg -e POSTGRES_PASSWORD=owner -e POSTGRES_USER=xt_owner -e POSTGRES_DB=xt -p 15440:5432 postgres:17-alpine >/dev/null
trap 'docker rm -f xt-packs-pg >/dev/null 2>&1' EXIT
for _ in $(seq 1 30); do docker exec xt-packs-pg pg_isready -U xt_owner >/dev/null 2>&1 && break; sleep 1; done
sleep 2
docker exec xt-packs-pg psql -q -U xt_owner -d postgres -c "drop database if exists xt with (force)" -c "create database xt" >/dev/null
docker exec -i xt-packs-pg psql -v ON_ERROR_STOP=1 -q -U xt_owner -d xt < "$RESEARCH/spike-model-engine/xtmodel/bootstrap.sql" > "$OUT/boot.txt" 2>&1 && echo BOOT_OK
cd "$HERE"
$PY run_models.py    > "$OUT/models.txt" 2>&1; echo "models  exit=$?"
$PY run_db_tests.py  > "$OUT/db.txt"     2>&1; echo "db      exit=$?"
$PY run_iam.py       > "$OUT/iam.txt"    2>&1; echo "iam     exit=$?"
IAM_MERGED=1 $PY run_iam.py > "$OUT/iam_merged.txt" 2>&1; echo "iam(merged) exit=$?"
$PY run_processes.py > "$OUT/process.txt" 2>&1; echo "process exit=$?"
