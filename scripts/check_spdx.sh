#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# Mỗi tệp mã phải có `SPDX-License-Identifier: Apache-2.0` trong 5 dòng đầu, hoặc được phủ bởi một
# annotation Apache-2.0 trong REUSE.toml ở gốc thư mục kiểm (dùng cho mã nhập vào không sửa tệp,
# ví dụ imports/waretrack-3d theo docs/decisions.md D-UNI-WT-1).

TARGET_DIR="${1:-.}"

if [ ! -d "$TARGET_DIR" ]; then
  echo "Error: Directory '$TARGET_DIR' does not exist." >&2
  exit 1
fi

# Annotation REUSE.toml -> regex (glob: `**/` bất kỳ thư mục, `**` bất kỳ, `*` không gồm `/`).
patterns="$(mktemp)"
trap 'rm -f "$patterns"' EXIT
if [ -f "$TARGET_DIR/REUSE.toml" ]; then
  python3 -I - "$TARGET_DIR/REUSE.toml" > "$patterns" <<'PY'
import re
import sys
import tomllib

with open(sys.argv[1], "rb") as fh:
    data = tomllib.load(fh)


def regex(glob: str) -> str:
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return "^" + "".join(out) + "$"


for ann in data.get("annotations", []):
    if ann.get("SPDX-License-Identifier") != "Apache-2.0":
        continue
    paths = ann.get("path", [])
    for glob in [paths] if isinstance(paths, str) else paths:
        print(regex(glob))
PY
fi

total=0
missing=0

while IFS= read -r -d '' file; do
  ((total++))
  if head -n 5 "$file" 2>/dev/null | grep -Fq 'SPDX-License-Identifier: Apache-2.0'; then
    continue
  fi
  rel="${file#"$TARGET_DIR"/}"
  if [ -s "$patterns" ] && printf '%s' "$rel" | grep -Eqf "$patterns"; then
    continue
  fi
  echo "$file"
  ((missing++))
done < <(find "$TARGET_DIR" \
  \( -name .git -o -name .claude -o -name node_modules -o -name .venv -o -name research -o -name dist \) -prune -o \
  -type f -size +0c \( \
    -name "*.py" -o \
    -name "*.sh" -o \
    -name "*.sql" -o \
    -name "*.yaml" -o \
    -name "*.yml" -o \
    -name "*.js" -o \
    -name "*.ts" -o \
    -name "*.tsx" -o \
    -name "*.mjs" -o \
    -name "*.css" -o \
    -name "*.html" -o \
    -name "*.toml" \
  \) -print0)

if [ "$missing" -eq 0 ]; then
  echo "OK: $total files"
  exit 0
else
  echo "MISSING: $missing files"
  exit 1
fi
