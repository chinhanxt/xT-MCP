#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# Kiểm "dịch từ mã nguồn" như giám khảo: clone HEAD (chỉ nội dung đã commit) ra thư mục tạm, chạy
# `make setup` + `make check` + `make release` ở đó, kiểm tarball (không đề thi/.env/khóa/báo cáo e2e,
# REUSE trong tarball), rồi xóa clone. Tệp chưa commit mà mã đã commit tham chiếu sẽ lộ ra ở đây.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
DIR="$(mktemp -d /tmp/xt-clean-XXXXXX)"
trap 'rm -rf "$DIR"' EXIT
untracked="$(git -C "$ROOT" ls-files --others --exclude-standard -- src desk/src tests packages deploy scripts)"
if [ -n "$untracked" ]; then
  echo "CẢNH BÁO: tệp chưa commit (clone sạch sẽ không có):" >&2
  echo "$untracked" | sed 's/^/  /' >&2
fi
git clone -q "$ROOT" "$DIR/xt"
cd "$DIR/xt"
make setup
make check
make release
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1)"
T="dist/xt-$VERSION.tar.gz"
bad="$(tar tzf "$T" | grep -E '\.pdf$|de_thi_OLP|(^|/)\.env$|\.pem$|\.key$|e2e/reports/' || true)"
[ -z "$bad" ] || { echo "tarball chứa tệp không được phát hành:" >&2; echo "$bad" >&2; exit 1; }
for f in LICENSE NOTICE THIRD_PARTY_NOTICES.md REUSE.toml; do
  tar tzf "$T" | grep -qx "xt-$VERSION/$f" || { echo "tarball thiếu $f" >&2; exit 1; }
done
mkdir x && tar xzf "$T" -C x && (cd "x/xt-$VERSION" && uvx --quiet reuse lint >/dev/null)
echo "clone sạch: setup + check + release OK; tarball sạch và tuân thủ REUSE"
