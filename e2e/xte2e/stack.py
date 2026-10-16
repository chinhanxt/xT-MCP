# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Vòng đời stack e2e: `python -m xte2e.stack up|down|status`.

`up` = volume MỚI (down -v trước), render APISIX, build, khởi động các dịch vụ minimal (postgrest bật sau
khi demo-seed có JWKS). Tuân quy tắc heavy-stack: chỉ chạy khi `free` báo available >= 4 GiB và không có
stack Keycloak/Ollama nặng khác đang chạy.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from xte2e import compose
from xte2e.config import DEPLOY, settings

MIN_AVAILABLE_GIB = 4.0


def available_gib() -> float:
    for line in open("/proc/meminfo", encoding="ascii"):
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024 / 1024
    return 0.0


def other_heavy() -> list[str]:
    proj = settings().project
    out = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"], capture_output=True, text=True
    ).stdout
    return [
        ln.split("\t")[0]
        for ln in out.splitlines()
        if any(k in ln for k in ("keycloak", "ollama/ollama")) and not ln.startswith(f"{proj}-")
    ]


def preflight(wait: float = 600) -> None:
    end = time.time() + wait
    while True:
        avail, heavy = available_gib(), other_heavy()
        if avail >= MIN_AVAILABLE_GIB and not heavy:
            print(f"[stack] RAM available {avail:.1f} GiB, không có stack nặng khác")
            return
        if time.time() > end:
            sys.exit(
                f"[stack] không đủ điều kiện heavy-stack: available={avail:.1f} GiB, đang chạy: {heavy}"
            )
        print(f"[stack] chờ: available={avail:.1f} GiB, stack nặng khác: {heavy}")
        time.sleep(30)


def up() -> None:
    preflight()
    compose.dc(["--profile", "*", "down", "--remove-orphans", "--volumes"], check=False)
    # `make -C deploy render` = env (sinh bí mật mới thêm vào .env.example: S3/SeaweedFS JWT...) + APISIX.
    subprocess.run(["make", "-s", "render"], cwd=DEPLOY, check=True)
    print("[stack] build + up (volume mới)")
    compose.dc(["build"], timeout=1800, capture=False)
    issues: list[str] = []
    compose.override_file().unlink(missing_ok=True)
    r = compose.dc(["up", "-d", *compose.CORE], timeout=900, check=False)
    print((r.stdout or "")[-1500:], (r.stderr or "")[-1500:])
    if (
        r.returncode != 0
        and "read-only service" in (r.stderr or "")
        and "`file` is the sole" in (r.stderr or "")
    ):
        # Docker Compose này không nạp `configs.content` vào service read_only. Đường vòng CHỈ cho harness:
        # cùng nội dung (đã nội suy) ghi ra tệp ngoài repo rồi dùng `file:`. E0 vẫn FAIL với lỗi gốc.
        issues.append(
            "compose.yaml: `configs.content` + `read_only` không chạy được với "
            + subprocess.run(
                ["docker", "compose", "version", "--short"], capture_output=True, text=True
            ).stdout.strip()
            + f" ({(r.stderr or '').strip().splitlines()[-1][:200]}); harness dùng `configs.file` thay thế"
        )
        write_config_override()
        r = compose.dc(["up", "-d", *compose.CORE], timeout=900, capture=False, check=False)
    issues_file().write_text(json.dumps(issues, ensure_ascii=False), encoding="utf-8")
    if r.returncode != 0:
        # Không dừng: kịch bản E0 báo dịch vụ nào hỏng kèm log (báo cáo vẫn được sinh).
        print(f"[stack] CẢNH BÁO: docker compose up rc={r.returncode}\n{compose.ps()}")


def issues_file() -> Path:
    settings().state_dir.mkdir(parents=True, exist_ok=True)
    return settings().state_dir / "stack-issues.json"


def write_config_override() -> None:
    compose.override_file().unlink(missing_ok=True)
    cfg = json.loads(compose.dc(["config", "--format", "json"]).stdout)
    d = settings().state_dir / "compose-configs"
    d.mkdir(parents=True, exist_ok=True)
    out = ["configs:"]
    for name, c in (cfg.get("configs") or {}).items():
        if "content" in c:
            f = d / name
            f.write_text(c["content"], encoding="utf-8")
            f.chmod(0o644)  # đọc bởi uid trong container; thư mục cha 0700 (bind do daemon)
            out += [f"  {name}:", "    content: !reset null", f"    file: {f}"]
    compose.override_file().write_text("\n".join(out) + "\n", encoding="utf-8")


def down() -> None:
    compose.dc(
        ["--profile", "*", "down", "--remove-orphans", "--volumes"], check=False, capture=False
    )


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "up":
        up()
    elif cmd == "down":
        down()
    elif cmd == "preflight":
        preflight()
    else:
        print(compose.ps())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
