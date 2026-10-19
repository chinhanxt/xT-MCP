# Thiết lập môi trường phát triển

Yêu cầu: Python >= 3.12, [uv](https://docs.astral.sh/uv/) (Apache-2.0/MIT), GNU make, git.

```sh
make setup     # uv sync: cài xt + công cụ dev (ruff, mypy, pytest, pytest-cov, import-linter, reuse)
make lint      # ruff check + ruff format --check + mypy (strict)
make boundaries# import-linter: domain <- contracts <- application <- adapters/entrypoints
make reuse     # reuse lint (REUSE.toml bao file không có header)
make test      # pytest + coverage
make test-integration # Keycloak 26.6.3 thật trong docker (cổng 19400), tự xóa container
make check     # tất cả các bước trên + scripts/check_spdx.sh
make release   # uv build (sdist + wheel) + git archive -> dist/xt-<version>.tar.gz
```

`make release` dùng `git archive HEAD`: chỉ chứa file đã commit.

Bố cục `src/xt/` theo `docs/archive/architecture-v1.md` mục 4: `domain`, `contracts`, `application`,
`adapters/{mcp,http,cli,db,workflow,blob,identity,notify}`, `entrypoints`.

`xt doctor` chỉ đọc: kiểm tra phiên bản Python và, nếu đặt `XT_DATABASE_URL`, kết nối TCP tới
host:port Postgres (chưa xác thực đăng nhập). Không đặt biến này thì báo `unknown`, exit 0.

## Kết quả `make check` thực tế (sau khi bỏ mã màu/thanh tiến trình)

```text
uv run ruff check src tests
All checks passed!
uv run ruff format --check src tests
19 files already formatted
uv run mypy
Success: no issues found in 15 source files
uv run lint-imports
╔══╗─────────▶╔╗ ╔╗      ╔╗◀───┐
╚╣╠╝◀─────┐  ╔╝╚╗║║────▶╔╝╚╗   │
 ║║   ╔══╦══╦╩╗╔╝║║  ╔╦═╩╗╔╝╔═╦══╗
 ║║╔══╣╔╗║╔╗║╔╣║ ║║ ╔╬╣╔╗║║ ║│║╔═╝
╔╣╠╣║║║╚╝║╚╝║║║╚╗║╚═╝║║║║║╚╗║═╣║
╚══╩╩╩╣╔═╩══╩╝╚═╝╚═══╩╩╝╚╩═╩╩═╩╝
  └──▶║║                    ▲ 
      ╚╝────────────────────┘
∙∙∙ 🧱 Building graph...

---------
Contracts
---------
Analyzed 15 files, 1 dependencies.
----------------------------------
Phụ thuộc hướng vào trong: entrypoints/adapters -> application -> contracts -> 
domain KEPT
Các adapter độc lập với nhau KEPT
Contracts: 2 kept, 0 broken.
uv run reuse lint
# SUMMARY
* Bad licenses: 0
* Deprecated licenses: 0
* Licenses without file extension: 0
* Missing licenses: 0
* Unused licenses: 0
* Used licenses: Apache-2.0
* Read errors: 0
* Invalid SPDX License Expressions: 0
* Files with copyright information: 180 / 180
* Files with license information: 180 / 180
Congratulations! Your project is compliant with version 3.3 of the REUSE Specification :-)
uv run pytest
============================= test session starts ==============================
platform linux -- Python 3.13.14, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/chinhan/xT
configfile: pyproject.toml
testpaths: tests
plugins: cov-7.1.0
collected 6 items
tests/unit/test_boundaries.py .                                          [ 16%]
tests/unit/test_cli.py .....                                             [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.13.14-final-0 _______________
Name                                   Stmts   Miss  Cover   Missing
--------------------------------------------------------------------
src/xt/__init__.py                         5      2    60%   9-10
src/xt/adapters/__init__.py                0      0   100%
src/xt/adapters/blob/__init__.py           0      0   100%
src/xt/adapters/cli/__init__.py            0      0   100%
src/xt/adapters/cli/main.py               60      6    90%   38-39, 48, 78-79, 83
src/xt/adapters/db/__init__.py             0      0   100%
src/xt/adapters/http/__init__.py           0      0   100%
src/xt/adapters/identity/__init__.py       0      0   100%
src/xt/adapters/mcp/__init__.py            0      0   100%
src/xt/adapters/notify/__init__.py         0      0   100%
src/xt/adapters/workflow/__init__.py       0      0   100%
src/xt/application/__init__.py             0      0   100%
src/xt/contracts/__init__.py               0      0   100%
src/xt/domain/__init__.py                  0      0   100%
src/xt/entrypoints/__init__.py             0      0   100%
--------------------------------------------------------------------
TOTAL                                     65      8    88%
============================== 6 passed in 0.44s ===============================
scripts/check_spdx.sh .
OK: 25 files
```
