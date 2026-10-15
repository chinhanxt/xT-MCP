# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""SeaweedFS thật cho test tích hợp: `weed server -s3` một container, cổng loopback ngẫu nhiên,
dữ liệu trên tmpfs (không ghi đĩa `/`), danh tính S3 sinh ngẫu nhiên mỗi lần chạy.

Không tự pull image (đĩa gần đầy): thiếu image thì lỗi rõ. Container chạy user `seaweed` (uid
1000, không root) và được xóa bằng `docker rm -f -v` khi xong.
"""

from __future__ import annotations

import json
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

# Bản 4.48 (Apache-2.0), cùng digest với deploy/compose.yaml.
IMAGE = (
    "chrislusf/seaweedfs@sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d"
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass(frozen=True)
class Seaweed:
    name: str
    endpoint: str
    access_key: str
    secret_key: str
    config_dir: Path

    def stop(self) -> None:
        subprocess.run(["docker", "rm", "-f", "-v", self.name], capture_output=True, check=False)
        shutil.rmtree(self.config_dir, ignore_errors=True)


def start() -> Seaweed:
    have = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False)
    if have.returncode != 0:
        raise RuntimeError(f"thiếu image {IMAGE} (không tự pull; kiểm `df -h /` rồi docker pull)")
    name = f"xt-it-seaweed-{secrets.token_hex(4)}"
    port = _free_port()
    ak, sk = "xt" + secrets.token_hex(8), secrets.token_urlsafe(24)
    cfg_dir = Path(tempfile.mkdtemp(prefix="xt-sw-"))
    cfg = {
        "identities": [
            {
                "name": "xt",
                "credentials": [{"accessKey": ak, "secretKey": sk}],
                "actions": ["Admin", "Read", "Write", "List", "Tagging"],
            }
        ]
    }
    (cfg_dir / "s3.json").write_text(json.dumps(cfg), encoding="utf-8")
    # Như deploy/compose.yaml (FB-03): JWT cho volume + filer, chỉ S3 (SigV4) đọc/ghi được byte.
    sec = '[guard]\nwhite_list = "127.0.0.1,@"\n' + "".join(
        f'[{s}]\nkey = "{secrets.token_urlsafe(32)}"\nexpires_after_seconds = 10\n'
        for s in ("jwt.signing", "jwt.signing.read", "jwt.filer_signing", "jwt.filer_signing.read")
    )
    (cfg_dir / "security.toml").write_text(sec, encoding="utf-8")
    for f in ("s3.json", "security.toml"):
        (cfg_dir / f).chmod(0o644)
    cfg_dir.chmod(0o755)
    subprocess.run(
        [
            "docker", "run", "-d", "--name", name,
            "--user", "1000:1000", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--memory", "512m", "--pids-limit", "256",
            "--tmpfs", "/data:uid=1000,gid=1000,size=256m",
            "--tmpfs", "/tmp:uid=1000,gid=1000,size=16m",
            "-v", f"{cfg_dir}:/etc/seaweedfs:ro",
            "-p", f"127.0.0.1:{port}:8333",
            IMAGE,
            "server", "-dir=/data", "-s3", "-s3.port=8333", "-s3.config=/etc/seaweedfs/s3.json",
            "-master.volumeSizeLimitMB=64", "-volume.max=8", "-ip=127.0.0.1", "-ip.bind=0.0.0.0",
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    sw = Seaweed(name, f"http://127.0.0.1:{port}", ak, sk, cfg_dir)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            r = httpx.get(sw.endpoint + "/", timeout=2)
            if r.status_code in (200, 403):  # 403 = S3 sẵn sàng và đòi chữ ký
                return sw
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    logs = subprocess.run(["docker", "logs", "--tail", "40", name], capture_output=True, text=True)
    sw.stop()
    raise RuntimeError(
        f"SeaweedFS không lên trong 90 s: {logs.stdout[-2000:]}{logs.stderr[-2000:]}"
    )
