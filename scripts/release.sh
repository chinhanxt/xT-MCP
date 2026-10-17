#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# Tạo dist/: wheel, xt-<version>.tar.gz (git archive), SBOM CycloneDX (Python + Desk), SHA256SUMS.
# Dùng: scripts/release.sh [ref]   (mặc định HEAD; workflow release gọi với tag)
set -euo pipefail
cd "$(dirname "$0")/.."

REF="${1:-HEAD}"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1)"
[ -n "$VERSION" ] || { echo "không đọc được version từ pyproject.toml" >&2; exit 1; }
DIST="$PWD/dist"

if [ "$REF" = HEAD ] && [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "CẢNH BÁO: cây làm việc có thay đổi chưa commit; tarball chỉ chứa nội dung đã commit ở $REF." >&2
fi

rm -rf "$DIST"
mkdir -p "$DIST"

# 1. wheel
uv build --wheel --out-dir "$DIST"   # sdist bỏ: trùng tên xt-<version>.tar.gz của git archive

# 2. Tarball nguồn. .gitattributes tại REF loại đề thi, ảnh tham chiếu và thư mục sinh cục bộ.
git archive --format=tar.gz --prefix="xt-$VERSION/" -o "$DIST/xt-$VERSION.tar.gz" "$REF" -- .

# 3. SBOM Python (CycloneDX 1.6): môi trường runtime sạch (không nhóm dev) dựng từ uv.lock.
SBOM_VENV="$(mktemp -d)"
trap 'rm -rf "$SBOM_VENV"' EXIT
UV_PROJECT_ENVIRONMENT="$SBOM_VENV" uv sync --frozen --no-dev --quiet
uvx --quiet --from cyclonedx-bom cyclonedx-py environment "$SBOM_VENV/bin/python" \
  --pyproject pyproject.toml --output-reproducible --validate \
  -o "$DIST/xt-$VERSION-python.cdx.json"

# 4. SBOM Desk (npm): chỉ phụ thuộc runtime, đọc từ package-lock.json (@cyclonedx/cyclonedx-npm, Apache-2.0).
# shellcheck disable=SC1091
. "$HOME/.nvm/nvm.sh" >/dev/null 2>&1 || true
(cd desk && npx --yes @cyclonedx/cyclonedx-npm --package-lock-only --omit dev \
  --output-reproducible --validate --output-format JSON \
  --output-file "$DIST/xt-$VERSION-desk.cdx.json")

# 5. Checksum
(cd "$DIST" && sha256sum -- * > SHA256SUMS.tmp && mv SHA256SUMS.tmp SHA256SUMS && sha256sum -c SHA256SUMS)
ls -l "$DIST"
