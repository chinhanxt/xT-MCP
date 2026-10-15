# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Validate spec mẫu -> sinh DDL -> áp vào Postgres bằng xt_migrator."""

import json, sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import psycopg
from examples import V1
from xtmodel.spec import Catalog
from xtmodel.compiler import compile_catalog

cat = Catalog.model_validate(V1)
steps = compile_catalog(cat)
print(f"{len(steps)} câu lệnh DDL")
ddl = [s.text() for s in steps]
print(ddl[1] if "de_xuat" in ddl[1] else ddl[0])
for s in ddl:
    if "POLICY" in s and "m2" in s:
        print(s)
pathlib.Path("schema.model-spec.json").write_text(
    json.dumps(Catalog.model_json_schema(), ensure_ascii=False, indent=1)
)
print("JSON Schema bytes:", pathlib.Path("schema.model-spec.json").stat().st_size)

# YAML: định dạng cho người đọc; agent gửi JSON qua tool. Kiểm tra vòng YAML -> spec.
import yaml
from pydantic import ValidationError

y = yaml.safe_dump(V1, allow_unicode=True, sort_keys=False)
pathlib.Path("examples.yaml").write_text("# SPDX-License-Identifier: Apache-2.0\n" + y)
print(
    "YAML round-trip hợp lệ:",
    Catalog.model_validate(yaml.safe_load(y)) == cat,
    "| dòng YAML:",
    y.count("\n"),
)
bad = y.replace("value: thuong", "value: no")  # bẫy YAML 1.1: 'no' -> False
try:
    Catalog.model_validate(yaml.safe_load(bad))
except ValidationError as e:
    print("YAML 'value: no' ->", e.errors()[0]["loc"][-2:], e.errors()[0]["msg"])
