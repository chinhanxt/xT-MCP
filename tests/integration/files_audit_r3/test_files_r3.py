# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit vòng 3: kiểm sâu các bản sửa vòng 2 (docs/audit-files.md).

- R3-A: purge/finalize đua ở mức cao (20 finalize cùng nội dung + vòng purge) — không 500, không
  mất byte (khóa `(tenant, sha256)` của R2-F3 phải chịu tải, không chỉ 2 luồng).
- R3-B: guard SeaweedFS `white_list = "127.0.0.1,@"` — peer cùng mạng KHÔNG assign/xóa được qua
  HTTP admin, và `@` KHÔNG mở gRPC quản trị (`weed shell volume.list`) cho peer.
- R3-C: quota tenant khi 2 người CÙNG tenant nạp song song sát trần — khóa tư vấn theo tenant phải
  tuần tự hóa kiểm-rồi-ghi, tổng byte nhận ≤ tenant_quota (không vượt trần do đua).
- R3-W: `files.maintain` chạy QUA tiến trình `xt worker` thật (không gọi hàm trực tiếp) và dọn
  được object của tệp xóa mềm quá hạn lưu.

Test thường = thuộc tính đã sửa phải GIỮ đúng. `xfail(strict=True, raises=AssertionError)` chỉ dùng
khi vòng 3 tái hiện lỗi mới. Setup hỏng -> `RuntimeError` (không bị xfail che).
"""

from __future__ import annotations

import concurrent.futures as cf
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.integration.composition.fixtures import REQUESTER, ROOT, Stack
from tests.integration.files_audit_r2.test_files_r2 import (
    F,
    backend,
    content_key,
    http,
    intent,
    need,
    sha,
    store,
    upload,
)
from tests.integration.files_audit_r3.conftest import A, COLLEAGUE, MAX, TENANT_Q

pytestmark = pytest.mark.integration


# ---- R3-A: đua purge/finalize ở mức cao --------------------------------------------------------


def test_purge_finalize_high_concurrency_keeps_bytes(r3stack: Stack) -> None:
    """20 tệp cùng nội dung C finalize song song trong khi một vòng `files.maintain` purge object C
    (tệp gốc đã xóa mềm, hạn lưu 0). Kỳ vọng: mọi finalize 200, mọi tải về 200 + đúng byte, object
    C còn (còn tệp sống trỏ vào). Không có 500 (BlobError) nào — khóa `(tenant,sha256)` chịu tải."""
    s = r3stack
    data = b"chung tu R3A " + secrets.token_hex(96).encode() + b"\n"
    key = content_key(s, A, data)
    with http(s, A, REQUESTER) as c:
        f0 = upload(c, "r3a-seed.txt", data)
        need(c.delete(f"{F}/{f0}").status_code == 200, "xóa mềm tệp gốc")
        fids = []
        for i in range(20):
            it = intent(c, f"r3a-{i}.txt")
            need(httpx.put(it["put_url"], content=data, timeout=60).status_code == 200, f"PUT {i}")
            fids.append(str(it["file"]["id"]))
    need(store(s).head(key) is not None, "object nội dung phải còn trước khi đua")

    stop = threading.Event()
    purge_err: list[Exception] = []

    def purge_loop() -> None:
        while not stop.is_set():
            try:
                backend(s).maintain(s.tenants[A])
            except Exception as e:  # noqa: BLE001
                purge_err.append(e)
                return

    def do_finalize(fid: str) -> tuple[int, int, bytes]:
        with http(s, A, REQUESTER) as c:
            fin = c.post(f"{F}/{fid}/finalize", json={"sha256": sha(data)})
            dl = c.get(f"{F}/{fid}/content")
            return fin.status_code, dl.status_code, dl.content

    pw = threading.Thread(target=purge_loop, name="r3a-purge")
    pw.start()
    try:
        with cf.ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(do_finalize, fids))
    finally:
        stop.set()
        pw.join(timeout=30)

    need(not purge_err, f"purge lỗi khi đua: {purge_err!r}")
    bad_fin = [r[0] for r in results if r[0] != 200]
    bad_dl = [(r[1]) for r in results if r[1] != 200]
    assert not bad_fin, f"finalize không 200: {bad_fin}"
    assert not bad_dl, f"tải về không 200 (mất byte/500): {bad_dl}"
    assert all(r[2] == data for r in results), "byte tải về sai nội dung"
    # còn 20 tệp sống trỏ vào C -> object C không được xóa.
    assert store(s).head(key) is not None, "object nội dung bị purge dù còn tệp sống dùng"


# ---- R3-B: guard SeaweedFS chặn peer cùng mạng -------------------------------------------------


def _peer_run(image: str, ip: str, script: str) -> subprocess.CompletedProcess[str]:
    name = f"xt-it-peer-{secrets.token_hex(4)}"
    try:
        return subprocess.run(
            ["docker", "run", "--rm", "--name", name, "--network", "bridge", "--read-only",
             "--tmpfs", "/tmp", "--cap-drop", "ALL", "--memory", "64m", "--entrypoint", "sh",
             image, "-c", script],
            capture_output=True, text=True, timeout=120, check=False,
        )  # fmt: skip
    finally:
        subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, check=False)


def test_guard_blocks_peer_http_admin_and_grpc(r3stack: Stack, sw: Any) -> None:
    """Peer cùng mạng bridge gọi master/volume/filer:
    - HTTP admin `/dir/assign`, `/col/delete`, `/vol/grow`, `/vol/vacuum`, `/dir/status` -> 401/403
      (guard white_list chặn mọi IP khác loopback).
    - gRPC quản trị qua `weed shell volume.list` -> KHÔNG liệt kê được volume (`@` chỉ cho nội tiến
      trình, không mở gRPC cho peer)."""
    ip = subprocess.run(
        ["docker", "inspect", "-f",
         "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", sw.name],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()  # fmt: skip
    need(ip, "không lấy được IP container seaweed")
    image = subprocess.run(
        ["docker", "inspect", "-f", "{{.Config.Image}}", sw.name],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()

    admin_paths = ["/dir/assign", "/col/delete?collection=r3ghost",
                   "/vol/grow?count=1", "/vol/vacuum", "/dir/status"]  # fmt: skip
    http_script = "; ".join(
        f"echo '#>{i}'; wget -S -q -O - 'http://{ip}:9333{p}' 2>&1 | head -2; echo"
        for i, p in enumerate(admin_paths)
    )
    r = _peer_run(image, ip, http_script)
    problems = []
    for i, p in enumerate(admin_paths):
        seg = r.stdout.split(f"#>{i}\n", 1)[-1].split(f"#>{i + 1}\n", 1)[0]
        m = re.search(r"HTTP/1\.[01] (\d{3})", seg)
        need(m, f"peer không tới được master {p}: {seg[-200:]}")
        assert m is not None
        if m.group(1) not in ("401", "403"):
            problems.append(f"{p} -> {m.group(1)} (không bị guard chặn)")
    assert not problems, problems

    grpc = _peer_run(
        image,
        ip,
        f"printf 'volume.list\\nexit\\n' | timeout 30 weed shell -master={ip}:9333 2>&1 | tail -40",
    )
    low = grpc.stdout.lower()
    # volume.list thành công in topology ("Topology ... DataCenter ... volume id:"); peer phải KHÔNG
    # thấy được -> không có dòng mô tả volume id.
    assert "volume id:" not in low and '"id":' not in low, (
        "gRPC quản trị mở cho peer (@ không được mở master gRPC ra peer)",
        grpc.stdout[-500:],
    )


# ---- R3-C: quota tenant khi 2 người cùng tenant nạp song song sát trần -------------------------


def _one_upload(s: Stack, who: str, idx: int, size: int) -> tuple[int, int]:
    data = (secrets.token_hex(size // 2 + 1)[:size]).encode()
    with http(s, A, who) as c:
        it = c.post(F, json={"name": f"q-{who}-{idx}.txt", "content_type": "text/plain"})
        if it.status_code != 201:
            return it.status_code, 0
        info = it.json()["data"]
        fid = str(info["file"]["id"])
        put = httpx.put(info["put_url"], content=data, timeout=60)
        if put.status_code != 200:
            return put.status_code, 0
        fin = c.post(f"{F}/{fid}/finalize", json={"sha256": sha(data)})
        return fin.status_code, (size if fin.status_code == 200 else 0)


def test_tenant_quota_parallel_two_users_no_overcommit(r3stack: Stack) -> None:
    """REQUESTER và COLLEAGUE (cùng tenant) nạp song song, mỗi người 4 tệp 64 KiB = 256 KiB; tổng
    512 KiB > TENANT_Q (384 KiB). Hợp đồng mong muốn: mỗi lời gọi kết thúc 200 (nhận) hoặc 507
    (`storage_quota`) — KHÔNG 5xx; tổng byte finalize ≤ TENANT_Q và có ít nhất một 507.

    Thực tế vòng 3: dưới tranh chấp, hai khóa cấp-tenant (audit-chain ở `lock_audit_chain` và khóa
    tư vấn quota ở `usage`) bị lấy ngược thứ tự giữa các transaction -> `DeadlockDetected` -> client
    nhận 500. Lặp vài vòng để tái hiện tất định. Đây là lỗi mới (R3-C1)."""
    s = r3stack
    size = MAX  # 64 KiB
    codes: list[int] = []
    accepted = 0
    for _ in range(3):
        jobs = [(REQUESTER, i) for i in range(4)] + [(COLLEAGUE, i) for i in range(4)]
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            res = list(ex.map(lambda j: _one_upload(s, j[0], j[1], size), jobs))
        codes += [c for c, _ in res]
        accepted = max(accepted, sum(b for _, b in res))
        # dọn để vòng sau lại sát trần
        backend(s).maintain(s.tenants[A])
    # 507 = storage_quota (kết quả hợp lệ theo hợp đồng ở docstring), không phải lỗi server.
    server_errors = [c for c in codes if c >= 500 and c != 507]
    assert not server_errors, f"5xx khi tranh chấp quota (deadlock): {server_errors} trong {codes}"
    assert accepted <= TENANT_Q, f"vượt trần tenant do đua: nhận {accepted} > {TENANT_Q}"
    assert 507 in codes, f"không có 507 dù vượt trần: {codes}"


# ---- R3-W: files.maintain chạy QUA tiến trình xt worker ----------------------------------------


def test_files_maintain_runs_via_xt_worker(r3stack: Stack, r3worker: Any, pg: Any) -> None:
    """Tệp xóa mềm (hạn lưu 0) + `bind-queue` (đăng ký chuỗi files.maintain, mốc now) + tiến trình
    `xt worker` THẬT. Worker phải chạy job `files.maintain` và dọn object trên S3 (object_key của
    tệp bị xóa, object nội dung biến mất). Chứng minh đường worker, không gọi hàm trực tiếp."""
    s = r3stack
    data = b"R3W maintain qua worker " + secrets.token_hex(96).encode() + b"\n"
    key = content_key(s, A, data)
    with http(s, A, REQUESTER) as c:
        fid = upload(c, "r3w.txt", data)
        need(c.delete(f"{F}/{fid}").status_code == 200, "xóa mềm tệp R3W")
    need(store(s).head(key) is not None, "object phải còn trước khi worker dọn")

    rc, out = run_bind_queue(r3worker)
    need(rc == 0, f"bind-queue: {out}")

    proc = subprocess.Popen(
        [r3worker.xt_bin, "worker", "--name", f"it-r3w-{secrets.token_hex(4)}"],
        env={**r3worker.env, "PYTHONPATH": str(ROOT)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    purged = False
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if store(s).head(key) is None:
                purged = True
                break
            if proc.poll() is not None:
                break
            time.sleep(1)
    finally:
        proc.terminate()
        log = proc.communicate(timeout=30)[0].decode(errors="replace")
    assert purged, (
        f"worker không dọn object qua files.maintain trong 90 s\n--- log ---\n{log[-3000:]}"
    )


def run_bind_queue(worker: Any) -> tuple[int, str]:
    from tests.integration.composition.fixtures import run_admin

    return run_admin(["tenant", "bind-queue", "--tenant", A], worker.env)
