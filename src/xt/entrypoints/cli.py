# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Public CLI composition: preserve existing commands and expose worker startup."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from xt import __version__
from xt.adapters.cli.main import main as legacy_main

ADMIN_GROUPS = frozenset({"db", "tenant", "org", "pack"})


def _help_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xt", description="xT - Agent-native DX-OS")
    parser.add_argument("--version", action="version", version=f"xt {__version__}")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("doctor", help="kiểm tra môi trường (chỉ đọc)")
    subcommands.add_parser("worker", help="run the PostgreSQL job worker")
    subcommands.add_parser("audit", help="neo/kiểm chuỗi audit (xt audit checkpoint|verify)")
    subcommands.add_parser("serve", help="chạy MCP server (--transport http|stdio)")
    subcommands.add_parser("db", help="migration nền tảng (xt db migrate)")
    subcommands.add_parser("tenant", help="đăng ký tenant (xt tenant create)")
    subcommands.add_parser("org", help="nạp sơ đồ tổ chức (xt org import)")
    subcommands.add_parser("pack", help="cài gói nghiệp vụ có xác nhận (xt pack install)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "worker":
        from xt.entrypoints.worker import main as worker_main

        return worker_main(arguments[1:])
    if arguments and arguments[0] == "audit":
        from xt.entrypoints.audit_cli import main as audit_main

        return audit_main(arguments[1:])
    if arguments and arguments[0] == "serve":
        from xt.entrypoints.serve import main as serve_main

        return serve_main(arguments[1:])
    if arguments and arguments[0] in ADMIN_GROUPS:
        from xt.entrypoints.admin import main as admin_main

        return admin_main(arguments)
    if not arguments:
        _help_parser().print_help()
        return 0
    if arguments[0] in {"--help", "-h"}:
        _help_parser().parse_args(arguments)
        return 0
    return legacy_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
