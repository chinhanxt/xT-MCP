#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Sao lưu / khôi phục bản tích hợp xT + Studio (chỉ stdlib).

Một bản lưu là một tệp tar 0600 gồm các thành phần (chọn bằng cờ, ít nhất một):

- ``postgres``: ``pg_dump -Fc`` của database trong biến môi trường ``--pg-url-env``.
  DSN không bao giờ vào argv/log/manifest; chỉ tên biến được ghi lại.
- ``objects``: cây tệp đối tượng (``--objects-dir``).
- ``studio``: ``studio.sqlite`` (chụp bằng ``VACUUM INTO``, không chép ``-wal``/``-shm``),
  ``credential.key`` và các tệp khác trong thư mục Studio, cùng ``studio.key``
  (``--studio-key``, secret HMAC). Hai khóa phải đi cùng database: thiếu
  ``credential.key`` thì Studio không mở lại được database (imports/dify-rea/src/store.ts).

``manifest.json`` (cuối tar) liệt kê sha256 + kích thước từng tệp; ``<bản lưu>.sha256`` là
checksum cả tệp. Khôi phục kiểm checksum, giải nén vào thư mục tạm, kiểm lại từng tệp rồi
mới ghi vào đích; đích không rỗng bị từ chối nếu thiếu ``--force``. Thiếu ``.sha256`` là lỗi trừ
khi có ``--no-sidecar``. Khôi phục dựng objects/Studio vào thư mục tạm cạnh đích, đổi chỗ nguyên tử,
và ghi PostgreSQL sau cùng.

pg_dump/pg_restore phải có major >= major của server (stack dùng postgres:17, client mặc định của
Ubuntu 24.04 là 16 và sẽ bị từ chối): trỏ tới client 17 bằng --pg-dump / --pg-restore / --psql.

Role của cụm (xt_app, xt_migrator, ...) không nằm trong dump: phải tồn tại trước khi restore
(pg_restore giữ owner/ACL, không dùng --no-owner).

Bản lưu chứa bí mật (khóa Studio, ``xt_audit.pepper`` trong dump): giữ quyền 0600, lưu ngoài repo.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

FORMAT = "xt-unified-backup/1"
MANIFEST = "manifest.json"
PG_FILE = "postgres/xt.dump"
OBJECTS = "objects/"
STUDIO_DIR = "studio/data/"
STUDIO_KEY = "studio/studio.key"
SQLITE = "studio.sqlite"
CREDENTIAL = "credential.key"
SKIP_STUDIO = frozenset({"studio.sqlite-wal", "studio.sqlite-shm", "studio.sqlite-journal"})


class BackupError(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class _Hashing(io.RawIOBase):
    """Băm đúng những byte mà tarfile đọc, tránh đọc hai lần một tệp có thể đang đổi."""

    def __init__(self, fh: io.BufferedReader) -> None:
        self.fh, self.h = fh, hashlib.sha256()

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int:  # type: ignore[override]
        n = self.fh.readinto(buffer) or 0
        self.h.update(memoryview(buffer)[:n])
        return n


def _add_file(
    tar: tarfile.TarFile, files: dict[str, dict[str, object]], src: Path, name: str, mode: int
) -> None:
    info = tarfile.TarInfo(name)
    info.size = src.stat().st_size
    info.mode, info.mtime = mode, int(time.time())
    with src.open("rb") as fh:
        hashing = _Hashing(fh)
        tar.addfile(info, io.BufferedReader(hashing))
    files[name] = {"sha256": hashing.h.hexdigest(), "size": info.size, "mode": mode}


def _walk(root: Path) -> Iterator[tuple[Path, str]]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for filename in sorted(filenames):
            p = Path(dirpath, filename)
            if p.is_symlink() or not p.is_file():
                raise BackupError(f"chỉ hỗ trợ tệp thường, gặp: {p}")
            yield p, p.relative_to(root).as_posix()


def pg_env(url: str) -> dict[str, str]:
    """DSN -> biến PG* cho tiến trình con (mật khẩu không vào argv)."""
    u = urlsplit(url)
    if u.scheme not in ("postgres", "postgresql") or not u.path.strip("/"):
        raise BackupError("URL PostgreSQL phải có dạng postgresql://user@host:port/dbname")
    env: dict[str, str] = {"PGDATABASE": unquote(u.path.lstrip("/"))}
    for key, value in (
        ("PGHOST", u.hostname),
        ("PGPORT", u.port),
        ("PGUSER", u.username),
        ("PGPASSWORD", u.password),
    ):
        if value:
            env[key] = unquote(str(value))
    query = {k: v[-1] for k, v in parse_qs(u.query).items()}
    for key, var in (
        ("sslmode", "PGSSLMODE"),
        ("sslrootcert", "PGSSLROOTCERT"),
        ("host", "PGHOST"),
        ("port", "PGPORT"),
    ):
        if key in query:
            env[var] = query[key]
    return env


def _tool(name: str, override: str | None) -> str:
    found = shutil.which(override or name)
    if not found:
        raise BackupError(f"không tìm thấy lệnh {override or name} trong PATH")
    return found


def _run(argv: list[str], url_env: str | None = None, what: str = "") -> str:
    env = dict(os.environ)
    if url_env:
        url = os.environ.get(url_env, "")
        if not url:
            raise BackupError(f"biến môi trường {url_env} chưa đặt")
        for stale in [k for k in env if k.startswith("PG")]:
            del env[stale]
        env.update(pg_env(url))
    proc = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-3:]
        raise BackupError(f"{what or argv[0]} lỗi (mã {proc.returncode}): {' | '.join(tail)}")
    return proc.stdout


def snapshot_sqlite(src: Path, dest: Path) -> None:
    conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    finally:
        conn.close()
    check = sqlite3.connect(dest)
    try:
        if check.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise BackupError("bản chụp studio.sqlite không qua integrity_check")
    finally:
        check.close()
    dest.chmod(0o600)


def backup(
    out_dir: Path,
    *,
    pg_url_env: str | None = None,
    pg_dump: str | None = None,
    objects_dir: Path | None = None,
    studio_dir: Path | None = None,
    studio_key: Path | None = None,
) -> Path:
    if not (pg_url_env or objects_dir or studio_dir or studio_key):
        raise BackupError("chọn ít nhất một thành phần để sao lưu")
    if (studio_dir is None) != (studio_key is None):
        raise BackupError("--studio-dir và --studio-key phải đi cùng nhau (hai khóa + database)")
    if studio_dir and studio_key:
        for needed in (studio_dir / SQLITE, studio_dir / CREDENTIAL, studio_key):
            if not needed.is_file():
                raise BackupError(f"thiếu {needed}: không tạo bản lưu Studio thiếu khóa/database")
        if len(studio_key.read_bytes()) < 32:
            raise BackupError("studio.key phải có ít nhất 32 byte")
    if objects_dir and not objects_dir.is_dir():
        raise BackupError(f"không có thư mục đối tượng: {objects_dir}")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = out_dir / f"xt-unified-{stamp}.tar"
    files: dict[str, dict[str, object]] = {}
    components: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="xt-backup-", dir=out_dir) as tmp:
        work = Path(tmp)
        fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w") as tar:
                if pg_url_env:
                    dump = work / "xt.dump"
                    _run(
                        [_tool("pg_dump", pg_dump), "-Fc", "--no-password", "-f", str(dump)],
                        pg_url_env,
                        "pg_dump",
                    )
                    if not dump.is_file() or dump.stat().st_size == 0:
                        raise BackupError("pg_dump không tạo ra tệp dump")
                    _add_file(tar, files, dump, PG_FILE, 0o600)
                    components["postgres"] = {"format": "pg_dump -Fc", "url_env": pg_url_env}
                if objects_dir:
                    for p, rel in _walk(objects_dir):
                        _add_file(tar, files, p, OBJECTS + rel, p.stat().st_mode & 0o777)
                    components["objects"] = {"prefix": OBJECTS}
                if studio_dir and studio_key:
                    snap = work / SQLITE
                    snapshot_sqlite(studio_dir / SQLITE, snap)
                    _add_file(tar, files, snap, STUDIO_DIR + SQLITE, 0o600)
                    for p, rel in _walk(studio_dir):
                        if rel != SQLITE and rel not in SKIP_STUDIO:
                            _add_file(tar, files, p, STUDIO_DIR + rel, 0o600)
                    _add_file(tar, files, studio_key, STUDIO_KEY, 0o600)
                    components["studio"] = {"data": STUDIO_DIR, "hmac_key": STUDIO_KEY}
                manifest = {
                    "format": FORMAT,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "components": components,
                    "files": dict(sorted(files.items())),
                }
                blob = json.dumps(manifest, indent=2, ensure_ascii=False).encode()
                info = tarfile.TarInfo(MANIFEST)
                info.size, info.mode, info.mtime = len(blob), 0o600, int(time.time())
                tar.addfile(info, io.BytesIO(blob))
        except BaseException:
            archive.unlink(missing_ok=True)
            raise
    sidecar = archive.with_name(archive.name + ".sha256")
    sidecar.write_text(f"{sha256_file(archive)}  {archive.name}\n")
    sidecar.chmod(0o600)
    return archive


def _load_manifest(tar: tarfile.TarFile) -> dict[str, object]:
    try:
        member = tar.getmember(MANIFEST)
        fh = tar.extractfile(member)
        manifest = json.loads(fh.read()) if fh else None
    except (KeyError, ValueError) as exc:
        raise BackupError("bản lưu không có manifest.json hợp lệ") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise BackupError("manifest không đúng định dạng xt-unified-backup/1")
    return manifest


def verify(
    archive: Path, extract_to: Path | None = None, *, allow_missing_sidecar: bool = False
) -> dict[str, object]:
    """Kiểm sidecar, manifest và sha256 từng tệp; giải nén vào ``extract_to`` nếu có.

    Thiếu ``.sha256`` là lỗi (manifest nằm cùng tar nên không chống được sửa có chủ ý),
    trừ khi ``allow_missing_sidecar`` (cờ ``--no-sidecar``).
    """
    sidecar = archive.with_name(archive.name + ".sha256")
    if sidecar.is_file():
        fields = sidecar.read_text().split()
        if not fields or sha256_file(archive) != fields[0]:
            raise BackupError("checksum của bản lưu không khớp .sha256 (tệp bị đổi hoặc hỏng)")
    elif not allow_missing_sidecar:
        raise BackupError(
            f"thiếu {sidecar.name}; chỉ dùng --no-sidecar nếu chấp nhận bỏ qua bước này"
        )
    try:
        tar = tarfile.open(archive, "r:")  # noqa: SIM115 - đóng bằng `with tar` bên dưới
    except tarfile.TarError as exc:
        raise BackupError("không đọc được bản lưu") from exc
    with tar:
        manifest = _load_manifest(tar)
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise BackupError("manifest thiếu danh sách tệp")
        seen: set[str] = set()
        for member in tar:
            if member.name == MANIFEST:
                continue
            if not member.isreg() or member.name not in files or member.name in seen:
                raise BackupError(f"tệp lạ hoặc trùng trong bản lưu: {member.name}")
            seen.add(member.name)
            fh = tar.extractfile(member)
            assert fh is not None
            h = hashlib.sha256()
            for block in iter(lambda fh=fh: fh.read(1 << 20), b""):  # type: ignore[misc]
                h.update(block)
            if h.hexdigest() != files[member.name]["sha256"]:
                raise BackupError(f"sha256 không khớp: {member.name}")
            if extract_to is not None:
                tar.extract(member, extract_to, filter="data")
        if seen != set(files):
            raise BackupError(f"thiếu tệp so với manifest: {sorted(set(files) - seen)}")
    comps = manifest.get("components", {})
    if isinstance(comps, dict) and "studio" in comps:
        names = {n.removeprefix(STUDIO_DIR) for n in files if n.startswith(STUDIO_DIR)}
        if not {SQLITE, CREDENTIAL} <= names or STUDIO_KEY not in files:
            raise BackupError("bản lưu Studio thiếu studio.sqlite, credential.key hoặc studio.key")
    return manifest


def _nonempty(path: Path) -> bool:
    return path.exists() and (not path.is_dir() or any(path.iterdir()))


def _pg_nonempty(url_env: str, psql: str | None) -> bool:
    query = (
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema','pg_toast') "
        "AND c.relkind IN ('r','v','m','S','f','p')"
    )
    out = _run(
        [_tool("psql", psql), "-X", "-A", "-t", "-q", "--no-password", "-c", query], url_env, "psql"
    )
    return int(out.strip() or "0") > 0


def _suffix() -> str:
    now = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{now}-{uuid.uuid4().hex[:6]}"


def _precheck_target(path: Path, *, is_dir: bool, force: bool) -> None:
    """Mọi lý do khiến việc ghi đích sẽ lỗi phải lộ ra ở đây, trước khi đụng vào đích nào."""
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.is_dir() != is_dir:
            raise BackupError(f"đích không phải {'thư mục' if is_dir else 'tệp'} thường: {path}")
        if _nonempty(path) and not force:
            raise BackupError(f"đích không rỗng: {path}; dùng --force (đích cũ được đổi tên)")
    parent = path.parent
    while not parent.exists():
        parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        raise BackupError(f"không ghi được vào {parent} (đích {path})")


def _stage_beside(src: Path, target: Path, mode: int | None) -> Path:
    """Chép ``src`` vào cạnh ``target`` (cùng filesystem) để đổi chỗ bằng rename nguyên tử."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f".{target.name}.restore-{uuid.uuid4().hex[:8]}")
    if src.is_dir():
        shutil.copytree(src, staged, symlinks=True)
    else:
        shutil.copyfile(src, staged)
    if mode is not None:
        staged.chmod(mode)
    return staged


def _swap(staged: Path, target: Path) -> Path | None:
    """Đưa ``staged`` vào ``target``. Trả về đường dẫn bản cũ (đã đổi tên) nếu có."""
    old: Path | None = None
    if target.exists() and not (target.is_dir() and not any(target.iterdir())):
        old = target.with_name(f"{target.name}.pre-restore-{_suffix()}")
        target.rename(old)
    try:
        staged.rename(target)  # thư mục đích rỗng được rename đè nguyên tử trên POSIX
    except BaseException:
        if old is not None:
            old.rename(target)
        raise
    return old


def _discard(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def restore(
    archive: Path,
    *,
    force: bool = False,
    allow_missing_sidecar: bool = False,
    pg_url_env: str | None = None,
    pg_restore: str | None = None,
    psql: str | None = None,
    objects_dir: Path | None = None,
    studio_dir: Path | None = None,
    studio_key: Path | None = None,
) -> list[str]:
    """Trả về danh sách thành phần đã khôi phục.

    Ba pha: (1) kiểm + giải nén + kiểm tra trước mọi đích; (2) dựng objects/Studio vào thư mục
    tạm cạnh đích; (3) đổi chỗ nguyên tử objects/Studio rồi ghi PostgreSQL CUỐI CÙNG. Lỗi ở
    pha 1-2 không đụng đích nào; lỗi khi đổi chỗ thì hoàn tác các đích đã đổi.
    """
    if (studio_dir is None) != (studio_key is None):
        raise BackupError("--studio-dir và --studio-key phải đi cùng nhau")
    staged: list[tuple[Path, Path]] = []
    swapped: list[tuple[Path, Path | None]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="xt-restore-") as tmp:
            stage = Path(tmp)
            manifest = verify(archive, stage, allow_missing_sidecar=allow_missing_sidecar)
            comps = manifest["components"]
            assert isinstance(comps, dict)
            wanted = [
                c
                for c, given in (
                    ("postgres", pg_url_env),
                    ("objects", objects_dir),
                    ("studio", studio_dir),
                )
                if given
            ]
            if not wanted:
                raise BackupError("chọn ít nhất một đích khôi phục")
            for c in wanted:
                if c not in comps:
                    raise BackupError(f"bản lưu không có thành phần {c}")
            # Pha 1: kiểm tra trước toàn bộ đích.
            restore_argv: list[str] = []
            if "postgres" in wanted:
                assert pg_url_env
                dbname = pg_env(os.environ.get(pg_url_env, ""))["PGDATABASE"]
                restore_argv = [
                    _tool("pg_restore", pg_restore),
                    "--exit-on-error",
                    "--no-password",
                    "--dbname",
                    dbname,
                ]
                if force:
                    restore_argv += ["--clean", "--if-exists"]
                if not force and _pg_nonempty(pg_url_env, psql):
                    raise BackupError("database đích không rỗng; dùng --force để ghi đè")
            if "objects" in wanted and objects_dir:
                _precheck_target(objects_dir, is_dir=True, force=force)
            if "studio" in wanted and studio_dir and studio_key:
                _precheck_target(studio_dir, is_dir=True, force=force)
                _precheck_target(studio_key, is_dir=False, force=force)
            # Pha 2: dựng bản mới cạnh đích (chưa đụng đích).
            if "objects" in wanted and objects_dir:
                src = stage / OBJECTS.rstrip("/")
                src.mkdir(exist_ok=True)
                staged.append((_stage_beside(src, objects_dir, None), objects_dir))
            if "studio" in wanted and studio_dir and studio_key:
                data = stage / STUDIO_DIR.rstrip("/")
                for p in data.rglob("*"):
                    p.chmod(0o700 if p.is_dir() else 0o600)
                staged.append((_stage_beside(data, studio_dir, 0o700), studio_dir))
                staged.append((_stage_beside(stage / STUDIO_KEY, studio_key, 0o600), studio_key))
            # Pha 3: đổi chỗ objects/Studio, PostgreSQL ghi sau cùng.
            try:
                for new, target in staged:
                    swapped.append((target, _swap(new, target)))
            except BaseException:
                for target, old in reversed(swapped):
                    _discard(target)
                    if old is not None:
                        old.rename(target)
                swapped.clear()
                raise
            staged.clear()
            if "postgres" in wanted:
                assert pg_url_env
                _run([*restore_argv, str(stage / PG_FILE)], pg_url_env, "pg_restore")
    finally:
        for new, _ in staged:
            _discard(new)
    return wanted


def _default_state() -> Path:
    base = os.environ.get("XT_UNIFIED_STATE_DIR")
    return Path(base) if base else Path.home() / ".local/state/xt-unified"


def main(argv: Sequence[str] | None = None) -> int:
    state = _default_state()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify")
    for name in ("backup", "restore"):
        p = sub.add_parser(name)
        p.add_argument("--pg-url-env", help="tên biến môi trường chứa URL PostgreSQL")
        p.add_argument("--objects-dir", type=Path)
        p.add_argument(
            "--studio-dir",
            type=Path,
            nargs="?",
            const=state / "studio",
            help="mặc định: $XT_UNIFIED_STATE_DIR/studio",
        )
        p.add_argument(
            "--studio-key",
            type=Path,
            nargs="?",
            const=Path(os.environ.get("XT_STUDIO_SECRET_FILE", state / "studio.key")),
            help="mặc định: $XT_STUDIO_SECRET_FILE hoặc <state>/studio.key",
        )
    sub.choices["backup"].add_argument("--out", type=Path, required=True)
    sub.choices["backup"].add_argument("--pg-dump")
    sub.choices["restore"].add_argument("archive", type=Path)
    sub.choices["restore"].add_argument("--force", action="store_true")
    for name in ("restore", "verify"):
        sub.choices[name].add_argument(
            "--no-sidecar",
            action="store_true",
            help="chấp nhận bản lưu không có .sha256 (mặc định: thiếu sidecar là lỗi)",
        )
    sub.choices["restore"].add_argument("--pg-restore")
    sub.choices["restore"].add_argument("--psql")
    sub.choices["verify"].add_argument("archive", type=Path)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "backup":
            path = backup(
                a.out,
                pg_url_env=a.pg_url_env,
                pg_dump=a.pg_dump,
                objects_dir=a.objects_dir,
                studio_dir=a.studio_dir,
                studio_key=a.studio_key,
            )
            print(f"OK: {path}")
        elif a.cmd == "restore":
            done = restore(
                a.archive,
                force=a.force,
                allow_missing_sidecar=a.no_sidecar,
                pg_url_env=a.pg_url_env,
                pg_restore=a.pg_restore,
                psql=a.psql,
                objects_dir=a.objects_dir,
                studio_dir=a.studio_dir,
                studio_key=a.studio_key,
            )
            print("OK: đã khôi phục " + ", ".join(done))
        else:
            m = verify(a.archive, allow_missing_sidecar=a.no_sidecar)
            print(f"OK: {len(m['files'])} tệp khớp sha256")  # type: ignore[arg-type]
    except BackupError as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    with contextlib.suppress(BrokenPipeError):
        sys.exit(main())
