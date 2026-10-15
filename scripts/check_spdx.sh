#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

TARGET_DIR="${1:-.}"

if [ ! -d "$TARGET_DIR" ]; then
  echo "Error: Directory '$TARGET_DIR' does not exist." >&2
  exit 1
fi

total=0
missing=0

while IFS= read -r -d '' file; do
  ((total++))
  if ! head -n 5 "$file" 2>/dev/null | grep -Fq 'SPDX-License-Identifier: Apache-2.0'; then
    echo "$file"
    ((missing++))
  fi
done < <(find "$TARGET_DIR" \
  \( -name .git -o -name node_modules -o -name .venv -o -name research -o -name dist \) -prune -o \
  -type f -size +0c \( \
    -name "*.py" -o \
    -name "*.sh" -o \
    -name "*.sql" -o \
    -name "*.yaml" -o \
    -name "*.yml" -o \
    -name "*.js" -o \
    -name "*.ts" -o \
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
