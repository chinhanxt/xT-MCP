# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""backup_unified.py: sao lưu/khôi phục với pg_dump giả, thư mục tạm, không DB thật."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

DEPLOY = Path(__file__).resolve().parents[2]
REPO = DEPLOY.parent
spec = importlib.util.spec_from_file_location(
    "backup_unified", DEPLOY / "scripts/backup_unified.py"
)
bu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bu)

PASSWORD = "s3cret-pw"
URL = f"postgresql://xt_app:{PASSWORD}@db.example:5544/xt?sslmode=require"
DUMP_BYTES = b"PGDMP-fake-custom-format\x00\x01"

FAKE_PG_DUMP = """#!/bin/sh
out=""
while [ $# -gt 0 ]; do
  case "$1" in -f) out="$2"; shift;; esac
  printf 'ARG %s\\n' "$1" >> "$FAKE_LOG"; shift
done
env | grep '^PG' | sed 's/^/ENV /' >> "$FAKE_LOG"
printf 'PGDMP-fake-custom-format\\000\\001' > "$out"
"""
FAKE_PG_RESTORE = """#!/bin/sh
for a in "$@"; do printf 'RARG %s\\n' "$a" >> "$FAKE_LOG"; last="$a"; done
cp "$last" "$FAKE_RESTORED"
"""
FAKE_PSQL = """#!/bin/sh
echo "${FAKE_TABLES:-0}"
"""


def make_bin(root: Path) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("pg_dump", FAKE_PG_DUMP),
        ("pg_restore", FAKE_PG_RESTORE),
        ("psql", FAKE_PSQL),
    ):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    return bin_dir


def tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


class BackupRestore(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bin = make_bin(self.root)
        self.log = self.root / "fake.log"
        self.restored = self.root / "restored.dump"
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_LOG": str(self.log),
            "FAKE_RESTORED": str(self.restored),
            "XT_TEST_PG_URL": URL,
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.studio = self.root / "state/studio"
        self.studio.mkdir(parents=True)
        self.key = self.root / "state/studio.key"
        self.key.write_bytes(bytes(range(32)))
        (self.studio / "credential.key").write_bytes(bytes(range(100, 132)))
        (self.studio / "extra.txt").write_text("extra")
        # WAL: các dòng nằm trong -wal vì kết nối còn mở; chép tệp thường sẽ làm mất chúng.
        self.live = sqlite3.connect(self.studio / "studio.sqlite")
        self.addCleanup(self.live.close)
        self.live.execute("PRAGMA journal_mode=WAL")
        self.live.execute("PRAGMA wal_autocheckpoint=0")
        self.live.execute("CREATE TABLE models(id TEXT PRIMARY KEY, secret TEXT)")
        self.live.execute("INSERT INTO models VALUES('m1','enc-blob-1'),('m2','enc-blob-2')")
        self.live.commit()
        self.objects = self.root / "objects"
        (self.objects / "a/b").mkdir(parents=True)
        (self.objects / "a/b/file.bin").write_bytes(b"\x00payload" * 1000)
        (self.objects / "top.txt").write_text("top")
        self.out = self.root / "out"

    def backup(self, **kw: object) -> Path:
        args: dict[str, object] = {
            "pg_url_env": "XT_TEST_PG_URL",
            "objects_dir": self.objects,
            "studio_dir": self.studio,
            "studio_key": self.key,
        }
        args.update(kw)
        return bu.backup(self.out, **args)  # type: ignore[arg-type]

    def test_roundtrip_all_components(self) -> None:
        self.assertTrue((self.studio / "studio.sqlite-wal").stat().st_size > 0)
        archive = self.backup()
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
        self.assertIn(archive.name, archive.with_name(archive.name + ".sha256").read_text())
        # pg_dump giả thật sự được chạy; mật khẩu chỉ ở env, không ở argv, manifest, archive.
        log = self.log.read_text()
        self.assertIn("ENV PGPASSWORD=" + PASSWORD, log)
        self.assertIn("ENV PGHOST=db.example", log)
        self.assertIn("ENV PGSSLMODE=require", log)
        self.assertNotIn(PASSWORD, "".join(ln for ln in log.splitlines() if ln.startswith("ARG")))
        self.assertNotIn(PASSWORD.encode(), archive.read_bytes())
        with tarfile.open(archive) as tar:
            manifest = json.loads(tar.extractfile("manifest.json").read())
        self.assertEqual(manifest["components"]["postgres"]["url_env"], "XT_TEST_PG_URL")
        self.assertIn("studio/data/credential.key", manifest["files"])
        self.assertNotIn("studio/data/studio.sqlite-wal", manifest["files"])

        want_objects, want_key = tree(self.objects), self.key.read_bytes()
        want_cred = (self.studio / "credential.key").read_bytes()
        self.live.close()
        new = self.root / "new"
        done = bu.restore(
            archive,
            pg_url_env="XT_TEST_PG_URL",
            objects_dir=new / "objects",
            studio_dir=new / "studio",
            studio_key=new / "studio.key",
        )
        self.assertEqual(sorted(done), ["objects", "postgres", "studio"])
        self.assertEqual(self.restored.read_bytes(), DUMP_BYTES)
        self.assertIn("RARG --dbname\nRARG xt", self.log.read_text().replace("\n", "\n"))
        self.assertEqual(tree(new / "objects"), want_objects)
        self.assertEqual((new / "studio.key").read_bytes(), want_key)
        self.assertEqual((new / "studio/credential.key").read_bytes(), want_cred)
        self.assertEqual((new / "studio/extra.txt").read_text(), "extra")
        self.assertEqual(stat.S_IMODE((new / "studio").stat().st_mode), 0o700)
        for name in ("studio.key", "studio/credential.key", "studio/studio.sqlite"):
            self.assertEqual(stat.S_IMODE((new / name).stat().st_mode), 0o600, name)
        conn = sqlite3.connect(new / "studio/studio.sqlite")
        self.addCleanup(conn.close)
        self.assertEqual(
            conn.execute("SELECT id,secret FROM models ORDER BY id").fetchall(),
            [("m1", "enc-blob-1"), ("m2", "enc-blob-2")],
        )

    def test_refuses_non_empty_targets_without_force(self) -> None:
        archive = self.backup()
        new = self.root / "new"
        (new / "objects").mkdir(parents=True)
        (new / "objects/keep").write_text("keep")
        with self.assertRaisesRegex(bu.BackupError, "không rỗng"):
            bu.restore(archive, objects_dir=new / "objects")
        (new / "studio.key").write_bytes(b"k" * 40)
        with self.assertRaisesRegex(bu.BackupError, "không rỗng"):
            bu.restore(archive, studio_dir=new / "studio", studio_key=new / "studio.key")
        self.assertEqual(tree(new), {"objects/keep": b"keep", "studio.key": b"k" * 40})
        with (
            mock.patch.dict(os.environ, {"FAKE_TABLES": "7"}),
            self.assertRaisesRegex(bu.BackupError, "database đích không rỗng"),
        ):
            bu.restore(archive, pg_url_env="XT_TEST_PG_URL")
        self.assertFalse(self.restored.exists())

    def test_force_renames_existing_and_cleans_database(self) -> None:
        archive = self.backup()
        new = self.root / "new"
        (new / "objects").mkdir(parents=True)
        (new / "objects/old").write_text("old")
        with mock.patch.dict(os.environ, {"FAKE_TABLES": "7"}):
            bu.restore(
                archive, force=True, pg_url_env="XT_TEST_PG_URL", objects_dir=new / "objects"
            )
        self.assertEqual(tree(new / "objects"), tree(self.objects))
        moved = [p for p in new.iterdir() if ".pre-restore-" in p.name]
        self.assertEqual(len(moved), 1)
        self.assertEqual((moved[0] / "old").read_text(), "old")
        log = self.log.read_text()
        self.assertIn("RARG --clean", log)
        self.assertIn("RARG --if-exists", log)

    def test_tampered_archive_is_rejected_and_target_untouched(self) -> None:
        archive = self.backup()
        new = self.root / "new"
        (new / "objects").mkdir(parents=True)
        (new / "objects/old").write_text("old")
        before = tree(new)
        data = bytearray(archive.read_bytes())
        data[data.index(b"payload")] ^= 0x01  # lật một bit trong nội dung objects/a/b/file.bin
        # 1) sidecar bắt được
        archive.write_bytes(bytes(data))
        with self.assertRaisesRegex(bu.BackupError, "checksum"):
            bu.restore(archive, force=True, objects_dir=new / "objects")
        # 2) không có sidecar: sha256 trong manifest bắt được
        archive.with_name(archive.name + ".sha256").unlink()
        with self.assertRaisesRegex(bu.BackupError, "sha256 không khớp"):
            bu.restore(archive, force=True, objects_dir=new / "objects")
        self.assertEqual(tree(new), before)

    def test_refuses_partial_studio_backup(self) -> None:
        (self.studio / "credential.key").unlink()
        with self.assertRaisesRegex(bu.BackupError, "credential.key"):
            self.backup()
        self.assertEqual(list(self.out.glob("*.tar")), [])
        with self.assertRaisesRegex(bu.BackupError, "đi cùng nhau"):
            self.backup(studio_key=None)

    def test_failed_backup_leaves_no_archive(self) -> None:
        with (
            mock.patch.dict(os.environ, {"PATH": "/nonexistent"}),
            self.assertRaisesRegex(bu.BackupError, "pg_dump"),
        ):
            self.backup()
        self.assertEqual(list(self.out.glob("xt-unified-*")), [])

    @unittest.skipUnless(shutil.which("node"), "cần Node >=24")
    def test_restored_studio_still_decrypts_stored_credential(self) -> None:
        version = subprocess.run(
            ["node", "-p", "process.versions.node.split('.')[0]"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if not version.isdigit() or int(version) < 24:
            self.skipTest("cần Node >=24")
        store = (REPO / "imports/dify-rea/src/store.ts").as_uri()
        script = (
            f"import {{ Store }} from '{store}';"
            "const [dir, mode] = process.argv.slice(1);"
            "const s = new Store(dir);"
            "if (mode === 'write') {"
            " s.db.exec(\"INSERT INTO workspaces VALUES('w','W')\");"
            " s.db.prepare('INSERT INTO models VALUES(?,?,?,?,?,?,?,?)')"
            ".run('m','w','M','openai','http://x','gpt','chat',s.encrypt('sk-live-credential'));"
            "} else { console.log(s.model('w','m').api_key); }"
            "s.close();"
        )
        env = {**os.environ, "NODE_NO_WARNINGS": "1"}
        node = ["node", "--input-type=module", "-e", script]
        node_dir = self.root / "nodestate/studio"
        subprocess.run([*node, str(node_dir), "write"], check=True, env=env, capture_output=True)
        key = self.root / "nodestate/studio.key"
        key.write_bytes(os.urandom(32))
        archive = bu.backup(self.out, studio_dir=node_dir, studio_key=key)
        shutil.rmtree(node_dir)
        key.unlink()
        # Khôi phục đúng credential.key thì Store mới giải mã được secret đã lưu.
        bu.restore(archive, studio_dir=node_dir, studio_key=key)
        out = subprocess.run(
            [*node, str(node_dir), "read"], check=True, env=env, capture_output=True, text=True
        ).stdout
        self.assertEqual(out.strip(), "sk-live-credential")


if __name__ == "__main__":
    with contextlib.suppress(SystemExit):
        unittest.main()
