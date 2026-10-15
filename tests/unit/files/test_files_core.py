# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Vòng đời tệp trên port giả: ý định -> PUT -> finalize -> quét -> tải về -> xóa mềm."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.unit.files.fakes import FakeScanner, MemRepo, MemStore
from xt.adapters.blob.clamd import parse_reply
from xt.application.files import (
    FileManager,
    FilePolicy,
    ScannerUnavailable,
    Spool,
    UploadTokens,
    tool_view,
)
from xt.application.files.sniff import sniff, type_for_name
from xt.application.tools.errors import ToolError
from xt.contracts.tools import ErrorCode

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
ALICE, BOB = uuid4(), uuid4()


def mgr(scanning: bool = False, **policy: object) -> tuple[FileManager, MemStore, FakeScanner]:
    store, scanner = MemStore(), FakeScanner()
    pol = FilePolicy(scanning=scanning, **policy)  # type: ignore[arg-type]
    return FileManager(pol, store, scanner if scanning else None), store, scanner


def spool(data: bytes, limit: int = 1 << 20) -> Spool:
    s = Spool(limit)
    for i in range(0, len(data), 7):
        s.write(data[i : i + 7])
    return s


def upload(m: FileManager, repo: MemRepo, data: bytes, name: str = "hd.pdf", who=ALICE):  # type: ignore[no-untyped-def]
    name, declared = m.declared_type(name, None)
    row = m.intent(repo, person=who, client_id="xt-cli", name=name, declared=declared, now=NOW)
    out = m.ingest(repo, row, spool(data), NOW)
    assert out.error is None, out.error
    return out.row


# ---- nhận dạng kiểu ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("head", "family"),
    [
        (PDF, "pdf"),
        (b"\x89PNG\r\n\x1a\n....", "png"),
        (b"\xff\xd8\xff\xe0", "jpeg"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"),
        (b"PK\x03\x04rest", "zip"),
        (b"MZ\x90\x00", "deny"),
        (b"\x7fELF\x02", "deny"),
        (b"#!/bin/sh\nrm -rf /", "deny"),
        (b"  <!DOCTYPE html><script>", "deny"),
        (b"<svg onload=alert(1)>", "deny"),
        ("Biên bản họp\n".encode(), "text"),
        (b"\xff\xfe\x00b", "unknown"),
        (b"abc\x00def", "unknown"),
    ],
)
def test_sniff_families(head: bytes, family: str) -> None:
    assert sniff(head, complete=True).family == family


def test_sniff_tolerates_utf8_cut_at_window_end_only_when_partial() -> None:
    cut = "ệ".encode()[:2]
    assert sniff(b"abc" + cut, complete=False).family == "text"
    assert sniff(b"abc" + cut, complete=True).family == "unknown"


def test_type_for_name_and_declared_checks() -> None:
    m, _, _ = mgr()
    assert type_for_name("Hoa_don.PDF") == "application/pdf"
    assert m.declared_type("bien-ban.md", None) == ("bien-ban.md", "text/markdown")
    for bad, reason in (
        ("virus.exe", "content_type_required"),
        ("a.html", "content_type_required"),
        ("../etc/passwd", "bad_file_name"),
        ("x‮gnp.exe", "bad_file_name"),
    ):
        with pytest.raises(ToolError) as e:
            m.declared_type(bad, None)
        assert e.value.details["reason"] == reason, bad
    with pytest.raises(ToolError) as e:
        m.declared_type("page.html", "text/html")
    assert e.value.details["reason"] == "content_type_not_allowed"


# ---- vòng đời ------------------------------------------------------------------------------


def test_roundtrip_content_addressed_and_dedup() -> None:
    m, store, _ = mgr()
    repo = MemRepo()
    row = upload(m, repo, PDF)
    assert row.status == "uploaded" and row.object_key == m.quarantine_key(repo.tenant, row.id)
    sha = hashlib.sha256(PDF).hexdigest()
    out = m.finalize(repo, row, sha, NOW)
    assert out.error is None and out.row.status == "not_scanned"
    assert out.row.object_key == f"t/{repo.tenant}/sha256/{sha[:2]}/{sha}"
    assert list(store.objects) == [out.row.object_key]  # quarantine đã dọn
    assert b"".join(m.open(repo, out.row)) == PDF
    assert repo.actions(row.id) == ["created", "uploaded", "finalized", "downloaded"]
    # Cùng nội dung, tệp thứ hai: không copy lại object.
    again = m.finalize(repo, upload(m, repo, PDF, "ban-sao.pdf"), sha, NOW).row
    assert again.object_key == out.row.object_key and store.copies == 1
    # finalize lặp lại cùng sha: idempotent
    assert m.finalize(repo, out.row, sha, NOW).error is None
    assert tool_view(out.row)["status"] == "not_scanned"


def test_checksum_mismatch_discards_bytes_and_allows_reupload() -> None:
    m, store, _ = mgr()
    repo = MemRepo()
    row = upload(m, repo, PDF)
    out = m.finalize(repo, row, "0" * 64, NOW)
    assert out.error is not None and out.error.details["reason"] == "checksum_mismatch"
    assert out.row.status == "awaiting_upload" and not store.objects
    assert repo.actions(row.id)[-1] == "rejected"
    again = m.ingest(repo, out.row, spool(PDF), NOW)
    assert again.error is None and again.row.status == "uploaded"


def test_finalize_before_upload_and_bad_sha() -> None:
    m, _, _ = mgr()
    repo = MemRepo()
    row = m.intent(repo, person=ALICE, client_id=None, name="a.pdf", declared="application/pdf",
                   now=NOW)  # fmt: skip
    out = m.finalize(repo, row, "a" * 64, NOW)
    assert out.error is not None and out.error.details["reason"] == "not_uploaded"
    with pytest.raises(ToolError):
        m.finalize(repo, row, "xyz", NOW)


def test_magic_bytes_beat_declared_type() -> None:
    m, store, _ = mgr()
    repo = MemRepo()
    row = m.intent(repo, person=ALICE, client_id=None, name="hd.pdf", declared="application/pdf",
                   now=NOW)  # fmt: skip
    out = m.ingest(repo, row, spool(b"MZ\x90\x00 fake pdf"), NOW)
    assert out.error is not None and out.error.details["reason"] == "content_type_not_allowed"
    out = m.ingest(repo, row, spool(b"just text pretending"), NOW)
    assert out.error is not None and out.error.details == {
        "reason": "content_type_mismatch",
        "declared": "application/pdf",
        "detected": "text",
    }
    assert not store.objects and out.row.status == "awaiting_upload"
    out = m.ingest(repo, row, spool(b""), NOW)
    assert out.error is not None and out.error.details["reason"] == "empty_file"


def test_size_cap_enforced_while_streaming() -> None:
    s = Spool(10)
    s.write(b"12345")
    with pytest.raises(ToolError) as e:
        s.write(b"678901")
    assert e.value.code is ErrorCode.RESOURCE_CONSTRAINT
    assert e.value.details == {"resource": "file_size", "limit_bytes": 10}


def test_upload_intent_expires() -> None:
    m, store, _ = mgr(upload_ttl=timedelta(minutes=1))
    repo = MemRepo()
    row = upload(m, repo, PDF)
    late = NOW + timedelta(minutes=2)
    out = m.finalize(repo, row, hashlib.sha256(PDF).hexdigest(), late)
    assert out.error is not None and out.error.details["reason"] == "upload_expired"
    assert out.row.status == "expired" and not store.objects
    fresh = m.intent(repo, person=ALICE, client_id=None, name="b.pdf",
                     declared="application/pdf", now=NOW)  # fmt: skip
    out = m.ingest(repo, fresh, spool(PDF), late)
    assert out.error is not None and out.error.details["reason"] == "upload_url_expired"


def test_used_link_cannot_be_reused() -> None:
    m, _, _ = mgr()
    repo = MemRepo()
    row = upload(m, repo, PDF)
    out = m.ingest(repo, row, spool(PDF), NOW)
    assert out.error is not None and out.error.details["reason"] == "already_uploaded"


def test_scanning_clean_infected_and_retry() -> None:
    m, store, scanner = mgr(scanning=True)
    repo = MemRepo()
    clean = m.finalize(repo, upload(m, repo, PDF), hashlib.sha256(PDF).hexdigest(), NOW).row
    assert clean.status == "clean" and clean.object_key and "sha256" in clean.object_key
    bad_row = upload(m, repo, EICAR, "eicar.txt")
    bad = m.finalize(repo, bad_row, hashlib.sha256(EICAR).hexdigest(), NOW).row
    assert bad.status == "infected" and bad.object_key is None
    assert bad.scan_signature == "Eicar-Test-Signature"
    assert not any("quarantine" in k for k in store.objects)
    err = m.download_error(bad, ALICE)
    assert err is not None and err.details["reason"] == "file_infected"
    scanner.down = True
    pending_row = upload(m, repo, b"notes\n", "n.txt")
    pending = m.finalize(repo, pending_row, hashlib.sha256(b"notes\n").hexdigest(), NOW).row
    assert pending.status == "scanning"
    err = m.download_error(pending, ALICE)
    assert err is not None and err.details["reason"] == "file_scanning"
    scanner.down = False
    assert [m.scan(repo, r).status for r in repo.scanning(10)] == ["clean"]


@pytest.mark.parametrize(
    ("policy", "alice", "bob"),
    [("deny", False, False), ("uploader", True, False), ("allow", True, True)],
)
def test_unscanned_download_policy(policy: str, alice: bool, bob: bool) -> None:
    m, _, _ = mgr(unscanned_download=policy)
    repo = MemRepo()
    row = m.finalize(repo, upload(m, repo, PDF), hashlib.sha256(PDF).hexdigest(), NOW).row
    assert (m.download_error(row, ALICE) is None) is alice
    assert (m.download_error(row, BOB) is None) is bob


def test_inline_store_and_soft_delete() -> None:
    m, store, _ = mgr()
    repo = MemRepo()
    out = m.store_inline(repo, person=ALICE, client_id="c", name="bb.md", declared="text/markdown",
                         data="# Biên bản\n".encode(), now=NOW)  # fmt: skip
    assert out.error is None and out.row.status == "not_scanned"
    deleted = m.soft_delete(repo, out.row, NOW)
    assert deleted.deleted_at == NOW and repo.get(out.row.id) is None
    assert store.objects  # byte theo nội dung giữ lại (retention dọn sau)
    with pytest.raises(ToolError) as e:
        m.store_inline(repo, person=ALICE, client_id="c", name="x.txt", declared="text/plain",
                       data=b"x" * (m.policy.max_inline_bytes + 1), now=NOW)  # fmt: skip
    assert e.value.code is ErrorCode.RESOURCE_CONSTRAINT


def test_policy_validation() -> None:
    with pytest.raises(ValueError):
        FilePolicy(allowed_types=frozenset({"text/html"}))
    with pytest.raises(ValueError):
        FileManager(FilePolicy(scanning=True), MemStore(), None)


# ---- URL tải lên ký sẵn --------------------------------------------------------------------


def test_upload_tokens() -> None:
    t = UploadTokens(b"k" * 32)
    fid, who = uuid4(), uuid4()
    tok = t.issue("acme", fid, who, 2_000)
    g = t.verify(tok, 1_999)
    assert (g.tenant, g.file_id, g.person) == ("acme", fid, who)
    with pytest.raises(ToolError) as e:
        t.verify(tok, 2_000)
    assert e.value.details["reason"] == "upload_url_expired"
    body, mac = tok.split(".")
    forged = UploadTokens(b"x" * 32).issue("beta", fid, who, 9_999).split(".")[0] + "." + mac
    for bad in (forged, body + "." + mac[:-2] + "AA", "garbage", body):
        with pytest.raises(ToolError) as e:
            t.verify(bad, 1)
        assert e.value.details["reason"] == "upload_url_invalid"
    with pytest.raises(ValueError):
        UploadTokens(b"short")


def test_clamd_reply_parsing() -> None:
    assert parse_reply("stream: OK").clean
    v = parse_reply("stream: Win.Test.EICAR_HDB-1 FOUND")
    assert not v.clean and v.signature == "Win.Test.EICAR_HDB-1"
    with pytest.raises(ScannerUnavailable):
        parse_reply("INSTREAM size limit exceeded. ERROR")


def test_row_is_immutable_snapshot() -> None:
    m, _, _ = mgr()
    repo = MemRepo()
    row = upload(m, repo, PDF)
    assert replace(row, name="khac.pdf").name == "khac.pdf" and repo.rows[row.id].name == "hd.pdf"
