# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit vòng 2 REST /files/v1 (docs/audit-files.md "Vòng 2"): test hồi quy phòng thủ R2-F1..F6.

Test thường = thuộc tính đã kiểm là ĐÚNG. `xfail(strict=True, raises=AssertionError)` = lỗi đã tái
hiện (mã R2-Fx): sửa xong test XPASS -> strict làm đỏ -> bỏ marker. Điều kiện dựng (setup) hỏng thì
ném `RuntimeError` (không bị xfail che).
"""

from __future__ import annotations

import hashlib
import json
import re
import runpy
import secrets
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from tests.integration.composition.fixtures import (
    DEPT_HEAD,
    REQUESTER,
    ROOT,
    Stack,
    run_admin,
    sub,
)
from tests.integration.files_audit_r2.conftest import (
    ACCOUNTANT,
    COLLEAGUE,
    HR,
    MAX,
    OTHER_HEAD,
    USER_Q,
    A,
    B,
)
from xt.entrypoints.files_http import CompositionFilesBackend, FilesPep

pytestmark = pytest.mark.integration

F = "/files/v1"
MiB = 1024 * 1024


def need(cond: object, msg: str) -> None:
    """Điều kiện dựng test (không phải điều đang kiểm): hỏng -> RuntimeError, không bị xfail che."""
    if not cond:
        raise RuntimeError(f"setup: {msg}")


def ftoken(s: Stack, alias: str, key: str) -> str:
    return s.idp.access_token(sub(alias, key), org=alias, aud=["xt-files"], scope="openid xt-files")


def http(s: Stack, alias: str, key: str) -> httpx.Client:
    return httpx.Client(
        base_url=s.app.base,
        headers={"Authorization": f"Bearer {ftoken(s, alias, key)}"},
        timeout=60,
    )


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def intent(c: httpx.Client, name: str) -> dict[str, Any]:
    r = c.post(F, json={"name": name, "content_type": "text/plain"})
    need(r.status_code == 201, f"tạo ý định: {r.status_code} {r.text[:300]}")
    return dict(r.json()["data"])


def put_finalize(c: httpx.Client, name: str, data: bytes) -> tuple[httpx.Response, str]:
    """Ý định + PUT + finalize. Trả (phản hồi bước đầu tiên không 2xx hoặc finalize, file id)."""
    it = intent(c, name)
    fid = str(it["file"]["id"])
    r = httpx.put(it["put_url"], content=data, timeout=60)
    if r.status_code != 200:
        return r, fid
    return c.post(f"{F}/{fid}/finalize", json={"sha256": sha(data)}), fid


def upload(c: httpx.Client, name: str, data: bytes) -> str:
    r, fid = put_finalize(c, name, data)
    need(r.status_code == 200, f"tải lên {name}: {r.status_code} {r.text[:300]}")
    return fid


def blob(n: int) -> bytes:
    """Văn bản ngẫu nhiên đúng `n` byte (không trùng sha giữa các lần gọi)."""
    return (secrets.token_hex(n // 2 + 1)[:n]).encode()


def backend(s: Stack) -> CompositionFilesBackend:
    svc = cast(Any, s.prepared.dispatcher.services.files)
    return CompositionFilesBackend(svc.rt, svc.stack, FilesPep(svc.rt))


def store(s: Stack) -> Any:
    return cast(Any, s.prepared.dispatcher.services.files).stack.store


def content_key(s: Stack, alias: str, data: bytes) -> str:
    h = sha(data)
    return f"t/{s.tenants[alias].id}/sha256/{h[:2]}/{h}"


# ---- R2-F1: quota tính cả byte đã xóa mềm cho tới khi dọn -------------------------------------


def test_storage_quota_enforced_for_live_files(xstack: Stack) -> None:
    """Đối chứng cho R2-F1: tệp còn sống cộng dồn tới quota người dùng -> 507 `storage_quota`."""
    s = xstack
    size = 200 * 1024  # 5 x 200 KiB < 1 MiB < 6 x 200 KiB
    with http(s, B, COLLEAGUE) as c:
        codes = []
        for i in range(USER_Q // size + 1):
            r, _ = put_finalize(c, f"live-{i}.txt", blob(size))
            codes.append(r.status_code)
    assert codes[:-1] == [200] * (len(codes) - 1), codes
    assert codes[-1] == 507 and r.json()["error"]["details"]["reason"] == "storage_quota", (
        codes,
        r.text[:300],
    )


def test_quota_counts_soft_deleted_bytes(xstack: Stack) -> None:
    """Quyết định "Quyền đọc tệp đính kèm": xóa mềm vẫn tính vào quota tới khi bị dọn hẳn. Tải lên
    -> xóa -> lặp lại phải chạm quota (byte vẫn nằm trên S3 trong thời gian lưu); sau `maintain`
    (retention 0 trong conftest) quota được trả lại."""
    s = xstack
    size = 200 * 1024
    rounds = USER_Q // size + 1
    with http(s, B, HR) as c:
        codes = []
        for i in range(rounds):
            r, fid = put_finalize(c, f"churn-{i}.txt", blob(size))
            codes.append(r.status_code)
            if r.status_code != 200:
                break
            need(c.delete(f"{F}/{fid}").status_code == 200, "xóa mềm")
        assert codes[-1] == 507, f"tải-xóa {rounds} lần x {size} B vẫn không chạm quota: {codes}"
        assert r.json()["error"]["details"]["reason"] == "storage_quota", r.text[:300]
        done = backend(s).maintain(s.tenants[B])
        need(done["purged"] >= 1, f"maintain không dọn gì: {done}")
        r, _ = put_finalize(c, "after-purge.txt", blob(size))
        assert r.status_code == 200, ("dọn xong mà quota không được trả lại", r.text[:300])


# ---- R2-F2: field file dạng xrn:<tenant>:file:<id> ---------------------------------------------


@pytest.mark.anyio
async def test_record_file_field_rejects_colleagues_file_bare_ref(xstack: Stack) -> None:
    """Đối chứng (FB-09 đã sửa): `file:<id>` và id trần của tệp đồng nghiệp bị từ chối."""
    s = xstack
    with http(s, A, COLLEAGUE) as c:
        fid = upload(c, "bang-luong.txt", b"bang luong rieng cua dong nghiep\n")
    for i, ref in enumerate((f"file:{fid}", fid)):
        rec = await s.call(A, REQUESTER, "create_record", {
            "model_id": "tai_lieu", "values": {"tieu_de": "Muon tep", "tep": ref},
            "idempotency_key": f"r2-f2-bare-{i}-{secrets.token_hex(4)}"})  # fmt: skip
        assert not rec["ok"], (ref, rec)


@pytest.mark.anyio
async def test_record_file_field_xrn_ref_validated_like_bare_id(xstack: Stack) -> None:
    """`xrn:<tenant>:file:<id>` được lớp records chấp nhận (parse_record_ref) nên phải qua đúng kiểm
    tra "người ghi đọc được tệp + tệp sẵn sàng" như `file:<id>`. Tệp của chính mình qua xrn vẫn
    gắn được (không chặn mù mọi xrn); tệp đồng nghiệp qua xrn bị từ chối ở cả create lẫn update."""
    s = xstack
    with http(s, A, REQUESTER) as c:
        own = upload(c, "cua-toi.txt", b"tep cua chinh toi\n")
    with http(s, A, COLLEAGUE) as c:
        other = upload(c, "luong-dong-nghiep.txt", b"bang luong dong nghiep xrn\n")
    ok = await s.call(A, REQUESTER, "create_record", {
        "model_id": "tai_lieu", "values": {"tieu_de": "Tep cua toi", "tep": f"xrn:{A}:file:{own}"},
        "idempotency_key": f"r2-f2-own-{secrets.token_hex(4)}"})  # fmt: skip
    need(ok["ok"], f"gắn tệp của chính mình qua xrn: {ok}")
    stolen = await s.call(A, REQUESTER, "create_record", {
        "model_id": "tai_lieu", "values": {"tieu_de": "Muon tep", "tep": f"xrn:{A}:file:{other}"},
        "idempotency_key": f"r2-f2-xrn-{secrets.token_hex(4)}"})  # fmt: skip
    assert not stolen["ok"], f"create_record nhận tệp đồng nghiệp qua xrn: {stolen}"
    rec = ok["data"]["record"]
    upd = await s.call(A, REQUESTER, "update_record", {
        "ref": rec["ref"], "expected_revision": rec["revision"],
        "set": {"tep": f"xrn:{A}:file:{other}"},
        "idempotency_key": f"r2-f2-upd-{secrets.token_hex(4)}"})  # fmt: skip
    assert not upd["ok"], f"update_record nhận tệp đồng nghiệp qua xrn: {upd}"


# ---- R2-F3: purge theo nội dung vs finalize đồng thời cùng nội dung ----------------------------


def _purge_race_setup(s: Stack, tag: str) -> tuple[bytes, str, str, dict[str, Any]]:
    """Tệp 1 (nội dung C) đã finalize rồi xóa mềm -> object `sha256/` của C chờ dọn. Tệp 2 cùng
    nội dung C đã PUT (quarantine), chưa finalize."""
    data = f"chung tu {tag} ".encode() + secrets.token_hex(64).encode() + b"\n"
    with http(s, A, REQUESTER) as c:
        f1 = upload(c, f"{tag}-1.txt", data)
        need(c.delete(f"{F}/{f1}").status_code == 200, "xóa mềm tệp 1")
        it = intent(c, f"{tag}-2.txt")
    need(httpx.put(it["put_url"], content=data, timeout=60).status_code == 200, "PUT tệp 2")
    key = content_key(s, A, data)
    need(store(s).head(key) is not None, "object theo nội dung phải còn trước khi dọn")
    return data, f1, str(it["file"]["id"]), it


def _download(s: Stack, fid: str) -> httpx.Response:
    with http(s, A, REQUESTER) as c:
        return c.get(f"{F}/{fid}/content")


def test_purge_during_finalize_head_check_keeps_bytes(
    xstack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Xen kẽ 1: finalize thấy object `sha256/` đã có (HEAD) nên bỏ qua copy; ngay sau HEAD, job
    `files.maintain` dọn tệp 1 (object_in_use chưa thấy tệp 2 vì tệp 2 chưa commit) và xóa object.
    Kỳ vọng: tệp 2 tải về đúng byte."""
    s = xstack
    data, _f1, f2, _ = _purge_race_setup(s, "race-head")
    st = store(s)
    key = content_key(s, A, data)
    real_head = st.head
    armed = threading.Event()
    armed.set()
    purge: dict[str, Any] = {}

    def run_purge() -> None:
        try:
            purge["done"] = backend(s).maintain(s.tenants[A])
        except Exception as e:
            purge["error"] = e

    worker = threading.Thread(target=run_purge, name="r2-purge")

    def head(k: str) -> Any:
        res = real_head(k)
        if k == key and armed.is_set():
            armed.clear()
            worker.start()
            worker.join(timeout=8)  # bản sửa có khóa: purge chờ finalize commit -> hết hạn chờ
        return res

    monkeypatch.setattr(st, "head", head)
    with http(s, A, REQUESTER) as c:
        fin = c.post(f"{F}/{f2}/finalize", json={"sha256": sha(data)})
    need(not armed.is_set(), "finalize không HEAD object theo nội dung (hook không chạy)")
    worker.join(timeout=60)
    monkeypatch.setattr(st, "head", real_head)
    need("error" not in purge, f"maintain lỗi: {purge.get('error')!r}")
    assert fin.status_code == 200, fin.text[:300]
    dl = _download(s, f2)
    assert dl.status_code == 200 and dl.content == data, (
        "mất byte: finalize trỏ vào object vừa bị purge",
        dl.status_code,
        dl.text[:200],
    )
    assert real_head(key) is not None


def test_finalize_between_purge_check_and_delete_keeps_bytes(
    xstack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Xen kẽ 2: purge đã kiểm object_in_use (= không) rồi mới xóa; finalize tệp 2 chen vào giữa,
    thấy object còn nên không copy, commit trỏ vào nó; purge xóa tiếp. Kỳ vọng: tệp 2 vẫn tải về
    đúng byte (finalize sau cuộc đua vẫn đọc được)."""
    s = xstack
    data, _f1, f2, _ = _purge_race_setup(s, "race-delete")
    st = store(s)
    key = content_key(s, A, data)
    real_delete = st.delete
    armed = threading.Event()
    armed.set()
    fin: dict[str, Any] = {}

    def do_finalize() -> None:
        with http(s, A, REQUESTER) as c:
            fin["r"] = c.post(f"{F}/{f2}/finalize", json={"sha256": sha(data)})

    racer = threading.Thread(target=do_finalize, name="r2-finalize")

    def delete(k: str) -> Any:
        if k == key and armed.is_set():
            armed.clear()
            racer.start()
            racer.join(timeout=8)  # bản sửa có khóa: finalize chờ purge -> hết hạn chờ
        return real_delete(k)

    monkeypatch.setattr(st, "delete", delete)
    done = backend(s).maintain(s.tenants[A])
    if armed.is_set():  # bản sửa: purge thấy object còn được dùng / không xóa -> tự finalize
        armed.clear()
        racer.start()
    racer.join(timeout=60)
    monkeypatch.setattr(st, "delete", real_delete)
    need("r" in fin, f"finalize không trả lời ({done})")
    assert fin["r"].status_code == 200, fin["r"].text[:300]
    dl = _download(s, f2)
    assert dl.status_code == 200 and dl.content == data, (
        "mất byte: purge xóa object sau khi finalize đã dùng lại",
        dl.status_code,
        dl.text[:200],
    )


# ---- R2-F4: master SeaweedFS không nhận thao tác admin/assign không xác thực ---------------------


COMPOSE = ROOT / "deploy" / "compose.yaml"


def _compose_config(name: str, tmp_path: Path) -> str:
    """Read the actual generated configs.file using isolated test secrets."""
    text = COMPOSE.read_text(encoding="utf-8")
    m = re.search(rf"^  {re.escape(name)}:\n    file: ([^\n]+)", text, re.M)
    need(m, f"không tìm thấy configs.{name}.file trong compose")
    assert m is not None
    prefix = "${XT_SECRETS_DIR:-${HOME}/.config/xt}/"
    need(m.group(1).startswith(prefix), "config phải nằm trong XT_SECRETS_DIR")
    config = (tmp_path / m.group(1).removeprefix(prefix)).resolve()
    need(config.is_relative_to(tmp_path.resolve()), "config nằm ngoài secrets test")
    generator = runpy.run_path(str(ROOT / "deploy/scripts/envfile.py"))
    env = {key: secrets.token_urlsafe(24) for key in generator["SECRET_KEYS"]}
    env["XT_SECRETS_DIR"] = str(tmp_path)
    generator["ensure_seaweed_configs"](env)
    return config.read_text(encoding="utf-8")


def _toml_sections(text: str) -> set[str]:
    return set(re.findall(r"^\s*\[([A-Za-z0-9_.]+)\]\s*$", text, re.M))


def test_compose_seaweed_only_on_internal_files_network() -> None:
    """FB-03 (đã sửa): seaweedfs chỉ ở mạng `files` internal, không publish cổng."""
    text = COMPOSE.read_text(encoding="utf-8")
    m = re.search(r"^  seaweedfs:\n((?:    .*\n|\s*\n)+)", text, re.M)
    need(m, "không tìm thấy service seaweedfs")
    assert m is not None
    svc = m.group(1)
    assert re.search(r"^    networks: \[files\]$", svc, re.M), svc
    assert not re.search(r"^    ports:", svc, re.M)
    assert re.search(r"^  files:\n    internal: true$", text, re.M)


def test_compose_seaweed_master_has_guard_whitelist(tmp_path: Path) -> None:
    """security.toml của compose phải có `[guard] white_list` (chỉ 127.0.0.1 / tiến trình S3 cùng
    container): master :9333 bọc `/dir/assign`, `/col/delete`, `/vol/grow`, `/vol/vacuum` bằng
    `guard.WhiteList`; white_list rỗng = cho mọi IP. JWT `[jwt.signing]` KHÔNG che master: master
    chính là nơi CẤP JWT ghi qua `/dir/assign`."""
    sec = _compose_config("seaweed-security", tmp_path)
    assert "guard" in _toml_sections(sec), sorted(_toml_sections(sec))
    m = re.search(r"^\[guard\]\n(?:.*\n)*?\s*white_list\s*=\s*\"([^\"]*)\"", sec + "\n", re.M)
    assert m and m.group(1).strip(), "white_list của [guard] rỗng = không chặn ai"


def test_seaweed_harness_mirrors_compose_security(xstack: Stack, sw: Any, tmp_path: Path) -> None:
    """SeaweedFS của test (tests/integration/seaweed.py) phải dùng cùng các mục security.toml như
    compose, để test sống dưới đây phản ánh đúng cấu hình triển khai."""
    harness = (Path(sw.config_dir) / "security.toml").read_text(encoding="utf-8")
    assert _toml_sections(harness) == _toml_sections(_compose_config("seaweed-security", tmp_path))


def _peer_get(sw: Any, paths: list[str]) -> list[tuple[str, str]]:
    """GET từ một container khác cùng mạng bridge (giả lập peer trong mạng `files`: clamav / worker
    bị chiếm) tới master :9333. Trả [(dòng trạng thái, thân)]."""
    ip = subprocess.run(
        ["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}",
         sw.name], capture_output=True, text=True, check=True, timeout=30,
    ).stdout.split()  # fmt: skip
    need(ip, "không lấy được IP container seaweed")
    script = "; ".join(
        f"echo '#>{i}'; wget -S -q -O - 'http://{ip[0]}:9333{p}' 2>&1; echo"
        for i, p in enumerate(paths)
    )
    peer = f"xt-it-peer-{secrets.token_hex(4)}"
    try:
        r = subprocess.run(
            ["docker", "run", "--rm", "--name", peer, "--network", "bridge", "--read-only",
             "--cap-drop", "ALL", "--memory", "32m", "alpine:latest", "sh", "-c", script],
            capture_output=True, text=True, timeout=60, check=False,
        )  # fmt: skip
    finally:
        subprocess.run(["docker", "rm", "-f", "-v", peer], capture_output=True, check=False)
    out = []
    for i in range(len(paths)):
        seg = r.stdout.split(f"#>{i}\n", 1)[1].split(f"#>{i + 1}\n", 1)[0]
        m = re.search(r"HTTP/1\.[01] (\d{3})[^\n]*", seg)
        need(m, f"peer không tới được master {paths[i]}: {seg[-300:]}")
        assert m is not None
        out.append((m.group(1), seg))
    return out


def test_seaweed_master_rejects_unauthenticated_admin_from_peer(xstack: Stack, sw: Any) -> None:
    """Peer cùng mạng gọi master :9333 không xác thực. Kỳ vọng `guard` chặn (401/403):
    `/dir/assign` (cấp fid ghi, kích hoạt grow volume) và `/col/delete` (xóa cả collection = mọi
    byte của bucket; ở đây dò bằng tên collection KHÔNG tồn tại nên không phá dữ liệu)."""
    ghost = f"r2ghost{secrets.token_hex(4)}"
    (a_code, a_body), (d_code, d_body) = _peer_get(
        sw, ["/dir/assign", f"/col/delete?collection={ghost}"]
    )
    problems = []
    if a_code not in ("401", "403") or '"fid"' in a_body:
        fid = re.search(r'"fid":"([^"]+)"', a_body)
        problems.append(f"/dir/assign -> {a_code} fid={fid.group(1) if fid else None}")
    if d_code not in ("401", "403"):
        tail = d_body.strip().splitlines()[-1][-120:] if d_body.strip() else ""
        problems.append(f"/col/delete -> {d_code} (qua guard): {tail}")
    assert not problems, problems


# ---- R2-F5: quyền đọc tệp đi theo quyền đọc bản ghi đã gắn -------------------------------------


PAY_MODEL = {
    "id": "r2_pay_doc",
    "name": "chung_tu_tt",
    "label": "Chứng từ thanh toán (audit R2)",
    "title_field": "noi_dung",
    "fields": [
        {"id": "f1", "name": "noi_dung", "type": "text", "label": "Nội dung", "required": True,
         "max": 200},
        {"id": "f2", "name": "chung_tu", "type": "file", "label": "Chứng từ", "required": True},
    ],
    "indexes": [],
    "rules": [],
    "permissions": {
        "employee": {"actions": ["create", "read", "update"], "scope": "own"},
        "accountant": {"actions": ["read"], "scope": "all"},
        "director": {"actions": ["read"], "scope": "all"},
    },
}  # fmt: skip


def _stmt(sid: str, roles: list[str], actions: list[str], own: bool) -> dict[str, Any]:
    cond = [{"left": "resource.owner", "op": "eq", "right": {"ref": "subject.id"}}] if own else []
    return {
        "id": sid,
        "effect": "Allow",
        "subjects": [f"role:{r}" for r in roles],
        "actions": actions,
        "resources": ["xrn:binhminh:chung_tu_tt:*"],
        "conditions": cond,
    }


@pytest.fixture(scope="module")
def paypack(xstack: Stack, tmp_path_factory: pytest.TempPathFactory) -> str:
    """Gói test: đề nghị thanh toán kèm chứng từ (field `file`); kế toán đọc mọi bản ghi (mọi
    phòng), nhân viên chỉ bản ghi của mình. Cài vào tenant A qua `xt pack install` thật."""
    s = xstack
    root = tmp_path_factory.mktemp("r2-paypack")
    (root / "models").mkdir()
    (root / "policies").mkdir()
    (root / "pack.json").write_text(json.dumps({
        "format": "xt-pack/1", "pack": "r2-chung-tu", "version": "0.1.0",
        "title": "Audit R2: chứng từ thanh toán", "models": ["chung_tu_tt"], "policies": ["iam"],
    }), encoding="utf-8")  # fmt: skip
    (root / "models" / "chung_tu_tt.json").write_text(json.dumps(PAY_MODEL), encoding="utf-8")
    acts = ["record.read", "record.create", "record.update"]
    (root / "policies" / "iam.json").write_text(json.dumps({
        "version": "1", "tenant": "binhminh", "statements": [
            _stmt("r2pay.emp-own", ["employee"], acts, True),
            _stmt("r2pay.acc-read", ["accountant", "director"], ["record.read", "record.search"],
                  False),
            _stmt("r2pay.agent", ["agent"], [*acts, "record.search"], False),
        ]}), encoding="utf-8")  # fmt: skip
    common = ["pack", "install", "--tenant", A, str(root)]
    who = ["--proposed-by", "agent:it", "--approved-by", "person:admin"]
    rc, plan = run_admin([*common, *who], s.env)
    need(rc == 0, plan)
    digest = re.search(r"Mã xác nhận \(digest\): (sha256:[0-9a-f]{64})", plan)
    need(digest, plan)
    assert digest is not None
    rc, out = run_admin([*common, *who, "--confirm", digest.group(1)], s.env)
    need(rc == 0, out)
    return "chung_tu_tt"


def test_unlinked_file_readable_only_by_uploader_and_department(xstack: Stack) -> None:
    """Tệp chưa gắn bản ghi: người tải lên + trưởng phòng cùng phòng (policy) đọc được; kế toán /
    HR / trưởng phòng khác phòng / đồng nghiệp (employee) đều 404 như id bịa."""
    s = xstack
    data = b"chung tu chua gan " + secrets.token_hex(8).encode() + b"\n"
    with http(s, A, REQUESTER) as c:
        fid = upload(c, "chua-gan.txt", data)
        assert c.get(f"{F}/{fid}/content").content == data
    with http(s, A, DEPT_HEAD) as c:
        assert c.get(f"{F}/{fid}/content").status_code == 200
    for who in (ACCOUNTANT, HR, OTHER_HEAD, COLLEAGUE):
        with http(s, A, who) as c:
            r = c.get(f"{F}/{fid}/content")
            assert r.status_code == 404 and data not in r.content, (who, r.status_code)


@pytest.mark.anyio
async def test_attachment_of_unreadable_record_stays_private(xstack: Stack, paypack: str) -> None:
    """Gắn vào bản ghi mà kế toán KHÔNG đọc được (`tai_lieu` của nhân viên) không mở tệp cho kế
    toán: quyền đi theo bản ghi, không phải theo việc "đã gắn"."""
    s = xstack
    data = b"tai lieu rieng " + secrets.token_hex(8).encode() + b"\n"
    with http(s, A, REQUESTER) as c:
        fid = upload(c, "rieng.txt", data)
    rec = await s.call(A, REQUESTER, "create_record", {
        "model_id": "tai_lieu", "values": {"tieu_de": "Rieng", "tep": f"file:{fid}"},
        "idempotency_key": f"r2-f5-priv-{secrets.token_hex(4)}"})  # fmt: skip
    need(rec["ok"], f"tạo tai_lieu: {rec}")
    got = await s.call(A, ACCOUNTANT, "get_record", {"ref": rec["data"]["record"]["ref"]})
    need(not got["ok"], f"kế toán không được đọc tai_lieu của nhân viên: {got}")
    with http(s, A, ACCOUNTANT) as c:
        r = c.get(f"{F}/{fid}/content")
    assert r.status_code == 404 and data not in r.content, r.status_code


@pytest.mark.anyio
async def test_accountant_reads_attachment_of_readable_cross_department_record(
    xstack: Stack, paypack: str
) -> None:
    """Quyết định "Quyền đọc tệp đính kèm": kế toán (phòng Kế toán) đọc được đề nghị thanh toán
    của phòng khác thì tải được chứng từ gắn vào đề nghị đó (metadata + byte)."""
    s = xstack
    data = b"hoa don VAT " + secrets.token_hex(8).encode() + b"\n"
    with http(s, A, REQUESTER) as c:
        fid = upload(c, "hoa-don.txt", data)
    rec = await s.call(A, REQUESTER, "create_record", {
        "model_id": paypack, "values": {"noi_dung": "Thanh toan VPP", "chung_tu": f"file:{fid}"},
        "idempotency_key": f"r2-f5-pay-{secrets.token_hex(4)}"})  # fmt: skip
    need(rec["ok"], f"tạo đề nghị thanh toán: {rec}")
    ref = rec["data"]["record"]["ref"]
    got = await s.call(A, ACCOUNTANT, "get_record", {"ref": ref})
    need(got["ok"], f"kế toán phải đọc được đề nghị khác phòng (setup): {got}")
    need(
        str(got["data"]["record"]["values"]["chung_tu"]).endswith(fid),
        f"bản ghi không trỏ tới tệp: {got['data']['record']['values']}",
    )
    with http(s, A, ACCOUNTANT) as c:
        meta = c.get(f"{F}/{fid}")
        dl = c.get(f"{F}/{fid}/content")
    assert meta.status_code == 200, ("metadata", meta.status_code)
    assert dl.status_code == 200 and dl.content == data, ("content", dl.status_code)
    # Người không đọc được bản ghi (HR khác phòng) vẫn không thấy tệp.
    with http(s, A, HR) as c:
        assert c.get(f"{F}/{fid}/content").status_code == 404


# ---- R2-F6: CL + TE: server ngừng đọc trong một trần nhỏ, đo phía server -----------------------


@pytest.fixture
def server_bytes(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[int, int]]:
    """Đếm byte uvicorn (h11) THỰC SỰ đọc từ socket, theo cổng nguồn của client. Không tính phần
    client "đã gửi" nhưng còn nằm trong bộ đệm kernel."""
    from uvicorn.protocols.http.h11_impl import H11Protocol

    counts: dict[int, int] = {}
    real: Callable[..., None] = H11Protocol.data_received
    lock = threading.Lock()

    def counted(self: Any, data: bytes) -> None:
        if self.client:
            with lock:
                counts[self.client[1]] = counts.get(self.client[1], 0) + len(data)
        real(self, data)

    monkeypatch.setattr(H11Protocol, "data_received", counted)
    yield counts


def _flood(port: int, head: bytes, first: bytes, total: int) -> tuple[int, bytes]:
    """Gửi header + thân chunked tới `total` byte (dừng khi server đóng). Trả (cổng nguồn, phản
    hồi)."""
    with socket.create_connection(("127.0.0.1", port), timeout=30) as so:
        local = so.getsockname()[1]
        so.sendall(head)
        chunk = b"z" * 65536
        try:
            so.sendall(b"%x\r\n" % len(first) + first + b"\r\n")
            sent = 0
            while sent < total:
                so.sendall(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                sent += len(chunk)
            so.sendall(b"0\r\n\r\n")
        except OSError:
            pass  # server đóng sớm = đúng
        resp = b""
        try:
            while c := so.recv(65536):
                resp += c
        except OSError:
            pass
    time.sleep(0.3)  # để event loop server xử lý nốt data_received đã nhận
    return local, resp


BOUND = 2 * MiB


def test_multipart_cl_te_server_reads_bounded(xstack: Stack, server_bytes: dict[int, int]) -> None:
    """FB-06 vòng 2: multipart với CL nhỏ + `Transfer-Encoding: chunked`, client đẩy 32 MiB. Đo
    phía server (byte h11 đọc từ socket): phải ≤ 2 MiB trước khi từ chối 400/411/413."""
    s = xstack
    tok = ftoken(s, A, REQUESTER)
    boundary = "xtr2"
    part = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.txt\"\r\n"
            "Content-Type: text/plain\r\n\r\n").encode()  # fmt: skip
    head = (f"POST {F} HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {tok}\r\n"
            f"Content-Type: multipart/form-data; boundary={boundary}\r\nContent-Length: 100\r\n"
            "Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n").encode()  # fmt: skip
    local, resp = _flood(s.app.port, head, part, 32 * MiB)
    read = server_bytes.get(local, 0)
    need(read > 0, "bộ đếm phía server không ghi nhận kết nối")
    assert resp.startswith((b"HTTP/1.1 400", b"HTTP/1.1 411", b"HTTP/1.1 413")), resp[:200]
    assert read <= BOUND, f"server đọc {read} B (> {BOUND}) trước khi từ chối"


def test_put_cl_te_server_reads_bounded(xstack: Stack, server_bytes: dict[int, int]) -> None:
    """PUT link ký sẵn với CL nhỏ + chunked: Spool cắt ở MAX -> 413; server đọc ≤ MAX + 2 MiB."""
    s = xstack
    with http(s, A, REQUESTER) as c:
        it = intent(c, "cl-te.txt")
    path = "/" + it["put_url"].split("/", 3)[3]
    head = (f"PUT {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 10\r\n"
            "Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n").encode()  # fmt: skip
    local, resp = _flood(s.app.port, head, b"x", 32 * MiB)
    read = server_bytes.get(local, 0)
    need(read > 0, "bộ đếm phía server không ghi nhận kết nối")
    assert resp.startswith((b"HTTP/1.1 400", b"HTTP/1.1 411", b"HTTP/1.1 413")), resp[:200]
    assert read <= MAX + BOUND, f"server đọc {read} B (> MAX + {BOUND}) trước khi từ chối"


# ---- worker: job files.maintain đã được đăng ký? -----------------------------------------------


def test_worker_registers_files_maintain(
    xstack: Stack, pg: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`xt worker` (handlers của composition) phải có `files.maintain` khi `XT_FILES_S3_ENDPOINT`
    đặt, kể cả không có ClamAV (dọn quarantine/xóa mềm là FB-02, không phụ thuộc quét)."""
    from xt.entrypoints.composition import worker

    env = {k: v for k, v in xstack.env.items() if k != "XT_FILES_CLAMD"}
    env |= {
        "XT_WORKER_DATABASE_URL": pg.app_dsn,
        "XT_QUEUE_DATABASE_URL": pg.app_dsn,
        "XT_WORKFLOW_RELAY_SECONDS": "30",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    names = set(worker.handlers())
    need(names, "handlers() rỗng")
    assert "files.maintain" in names, sorted(names)
