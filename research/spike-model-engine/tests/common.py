# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
import json, pathlib, sys, uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import psycopg
from psycopg_pool import ConnectionPool

DSN = "host=localhost port=15434 dbname=xt"
MIG = DSN + " user=xt_migrator password=migrator"
APP = DSN + " user=xt_app password=app"
SU = DSN + " user=xt_owner password=owner"


def mig():
    return psycopg.connect(MIG, autocommit=True)


def app_pool(size=1):
    return ConnectionPool(APP, min_size=size, max_size=size, open=True)


def show(title, obj):
    print(f"--- {title}")
    print(json.dumps(obj, ensure_ascii=False, default=str, indent=None)[:900])


U = lambda: uuid.uuid4()
