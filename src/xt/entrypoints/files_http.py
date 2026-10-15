# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Nối dịch vụ tệp (SeaweedFS S3 + ClamAV tùy chọn) vào `xt serve --transport http`.

Không sửa `composition/`: module này chỉ dùng API public của composition (`Runtime`, `Caller`,
`effective_mode`). Lớp tích hợp gọi một dòng trong `Prepared.http_app()`:

    from xt.entrypoints.files_http import with_files_api
    return with_files_api(self, self.cfg.env, with_workflow_api(self, self.cfg.env, merged))

`with_files_api` (khi `XT_FILES_S3_ENDPOINT` đã đặt) làm hai việc:
1. gắn `FileToolService` vào `prepared.dispatcher.services.files`: tool MCP `upload_file` thật
   (`XT_MCP_CAPABILITIES` phải gồm `upload_file`);
2. mount REST `/files/v1` (audience riêng `xt-files`, docs/decisions.md "REST /files/v1").
Chưa đặt `XT_FILES_S3_ENDPOINT` -> không mount, `upload_file` vẫn `capability_unavailable` (không
giả thành công).

Biến môi trường (docs/files-api.md §Cấu hình):
- `XT_FILES_S3_ENDPOINT` (vd http://seaweedfs:8333), `XT_FILES_S3_BUCKET` (mặc định xt-files),
  `XT_FILES_S3_ACCESS_KEY`, `XT_FILES_S3_SECRET_KEY`, `XT_FILES_S3_REGION` (mặc định us-east-1).
- `XT_FILES_PUBLIC_URL`: gốc công khai của `put_url` (vd https://xt-api.freepro.online).
- `XT_FILES_URL_KEY_FILE` (tùy chọn, ≥ 32 byte): khóa ký URL tải lên; mặc định dẫn xuất (HMAC,
  nhãn riêng) từ `XT_CURSOR_KEY_FILE`.
- `XT_FILES_MAX_BYTES` (mặc định 26214400), `XT_FILES_UPLOAD_TTL_SECONDS` (mặc định 900),
  `XT_FILES_UNSCANNED_DOWNLOAD` = deny | uploader (mặc định) | allow,
  `XT_FILES_CLAMD` = host:port (bật quét; rỗng = không quét, tệp ở trạng thái not_scanned).
- `XT_FILES_AUDIENCE` / `XT_FILES_REQUIRED_SCOPES` (mặc định xt-files),
  `XT_FILES_ALLOWED_CLIENTS` (mặc định = `XT_MCP_ALLOWED_CLIENTS`).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from starlette.applications import Starlette
from starlette.routing import Mount

from xt.adapters.blob import ClamdScanner, S3FileStore
from xt.adapters.db.files import PgFileRepository
from xt.adapters.http.files_api import Download, UploadInfo, files_routes
from xt.adapters.http.workflow_api import AuthChallenge
from xt.adapters.identity import (
    StaticClientAllowlist,
    build_jwks_cache,
    build_revocation_guard,
    extract_bearer,
    load_introspection_settings,
)
from xt.adapters.identity.access_token import AccessTokenVerifier
from xt.adapters.identity.config import ResourceServerSettings
from xt.adapters.mcp import ClientClassifier
from xt.application.files import (
    FileManager,
    FilePolicy,
    FileRow,
    Outcome,
    Spool,
    UploadTokens,
    tool_view,
    view,
)
from xt.application.files.service import too_large
from xt.application.files.sniff import ALLOWED_TYPES, SNIFF_BYTES
from xt.application.iam.guard import AccessGuard as IamGuard
from xt.application.tools.context import CallContext, iso
from xt.application.tools.errors import (
    access_denied,
    invalid_request,
    not_found,
    precondition_failed,
)
from xt.application.tools.pii_scrub import ErrorScrubber
from xt.application.tools.ports import Args, CommitResult, MutationPlan, PiiMode
from xt.domain.autonomy import RiskLevel
from xt.domain.iam import AccessRequest, PolicyDocument, PolicyEvaluator
from xt.domain.iam import Principal as IamPrincipal
from xt.domain.identity import IdentityError, InsufficientScopeError, InvalidTokenError, Principal
from xt.domain.model.context import ModelActor
from xt.entrypoints.composition import Composition
from xt.entrypoints.composition.pii import effective_mode
from xt.entrypoints.composition.runtime import Caller, CallerError, Runtime, iam_person

DEFAULT_AUDIENCE = "xt-files"
REALM = "xt-files"
_DESCRIPTIONS = {
    "missing_token": "Authentication required",
    "expired": "The access token expired",
    "audience": "The access token was not issued for the files API",
    "client_not_allowed": "The client is not allowed to access the files API",
    "token_revoked": "The access token is no longer active",
}
UNSCANNED_POLICIES = ("deny", "uploader", "allow")


class FilesConfigError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


def _words(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    raw = env.get(name, "").replace(",", " ")
    return tuple(dict.fromkeys(w for w in raw.split() if w))


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int, p: list[str]) -> int:
    raw = env.get(name, "").strip() or str(default)
    if not raw.isdigit() or not lo <= int(raw) <= hi:
        p.append(f"{name} phải là số nguyên {lo}..{hi}")
        return default
    return int(raw)


def _secret(env: Mapping[str, str], name: str, p: list[str]) -> bytes:
    raw = env.get(name, "").strip()
    try:
        data = Path(raw).read_bytes().strip()
    except OSError as e:
        p.append(f"{name}: không đọc được tệp ({e.strerror})")
        return b""
    if len(data) < 32:
        p.append(f"{name}: khóa phải ≥ 32 byte")
    return data


@dataclass(frozen=True)
class FilesConfig:
    endpoint: str
    bucket: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    region: str
    public_url: str
    url_key: bytes = field(repr=False)
    policy: FilePolicy
    clamd: tuple[str, int] | None

    @staticmethod
    def enabled(env: Mapping[str, str]) -> bool:
        return bool(env.get("XT_FILES_S3_ENDPOINT", "").strip())

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> FilesConfig:
        p: list[str] = []
        endpoint = env.get("XT_FILES_S3_ENDPOINT", "").strip()
        if not endpoint.startswith(("http://", "https://")):
            p.append("XT_FILES_S3_ENDPOINT phải là http(s)://host:port của S3 (SeaweedFS)")
        ak = env.get("XT_FILES_S3_ACCESS_KEY", "").strip()
        sk = env.get("XT_FILES_S3_SECRET_KEY", "").strip()
        if not ak or not sk:
            p.append("XT_FILES_S3_ACCESS_KEY / XT_FILES_S3_SECRET_KEY chưa đặt")
        elif len(sk) < 16:
            p.append("XT_FILES_S3_SECRET_KEY phải ≥ 16 ký tự")
        public = env.get("XT_FILES_PUBLIC_URL", "").strip().rstrip("/")
        allow_http = env.get("XT_IDP_ALLOW_HTTP", "").strip().lower() in ("1", "true", "yes")
        if not (public.startswith("https://") or (allow_http and public.startswith("http://"))):
            p.append("XT_FILES_PUBLIC_URL phải là URL https:// (http chỉ khi XT_IDP_ALLOW_HTTP)")
        if env.get("XT_FILES_URL_KEY_FILE", "").strip():
            key = _secret(env, "XT_FILES_URL_KEY_FILE", p)
        else:
            # Dẫn xuất có nhãn riêng: lộ URL tải lên không giúp giả cursor và ngược lại.
            base = _secret(env, "XT_CURSOR_KEY_FILE", p) if env.get("XT_CURSOR_KEY_FILE") else b""
            if not base:
                p.append("XT_FILES_URL_KEY_FILE (hoặc XT_CURSOR_KEY_FILE) chưa đặt")
            key = hmac.new(base, b"xt-files-url-key-v1", hashlib.sha256).digest() if base else b""
        unscanned = env.get("XT_FILES_UNSCANNED_DOWNLOAD", "").strip() or "uploader"
        if unscanned not in UNSCANNED_POLICIES:
            p.append(f"XT_FILES_UNSCANNED_DOWNLOAD phải là một trong {list(UNSCANNED_POLICIES)}")
            unscanned = "deny"
        clamd: tuple[str, int] | None = None
        raw_clamd = env.get("XT_FILES_CLAMD", "").strip()
        if raw_clamd:
            host, _, port = raw_clamd.rpartition(":")
            if not host or not port.isdigit() or not 1 <= int(port) <= 65535:
                p.append("XT_FILES_CLAMD phải có dạng host:port (vd clamav:3310)")
            else:
                clamd = (host, int(port))
        allowed = frozenset(_words(env, "XT_FILES_ALLOWED_TYPES")) or frozenset(ALLOWED_TYPES)
        if allowed - set(ALLOWED_TYPES):
            p.append(
                "XT_FILES_ALLOWED_TYPES chỉ nhận kiểu có bộ nhận dạng magic byte: "
                f"{sorted(allowed - set(ALLOWED_TYPES))}"
            )
        max_bytes = _int(env, "XT_FILES_MAX_BYTES", 25 * 1024 * 1024, 1024, 1 << 30, p)
        ttl = _int(env, "XT_FILES_UPLOAD_TTL_SECONDS", 900, 10, 86400, p)
        if p:
            raise FilesConfigError(p)
        policy = FilePolicy(
            max_bytes=max_bytes,
            max_inline_bytes=min(1024 * 1024, max_bytes),
            upload_ttl=timedelta(seconds=ttl),
            scanning=clamd is not None,
            unscanned_download=cast(Any, unscanned),
            allowed_types=allowed,
        )
        return cls(
            endpoint,
            env.get("XT_FILES_S3_BUCKET", "").strip() or "xt-files",
            ak,
            sk,
            env.get("XT_FILES_S3_REGION", "").strip() or "us-east-1",
            public,
            key,
            policy,
            clamd,
        )


def mask_name(name: str, mode: PiiMode) -> str:
    """Tên tệp là văn bản tự do có thể mang PII (spike-pii-lane): ngoài tier `full`, che số
    CCCD / điện thoại / email bằng bộ nhận dạng của làn PII (không đảo ngược được)."""
    if mode == "full":
        return name
    return ErrorScrubber(lambda _v: "[PII]").text(name)


# ---- IAM cho thao tác không qua tool --------------------------------------------------------


class _People:
    def __init__(self, rt: Runtime, caller: Caller) -> None:
        self.rt, self.caller = rt, caller

    def get(self, key: str) -> IamPrincipal | None:
        try:
            pid = UUID(key)
        except ValueError:
            return None
        p = self.rt.store.person(self.caller.actor, pid)
        return iam_person(self.caller.alias, p) if p and p.active else None


class _NoGrant:
    def get(self, key: str) -> None:
        return None


class FilesPep:
    """PEP của `file.read` / `file.delete` / `file.create` (finalize) trên `xrn:<t>:file:<id>`,
    thuộc tính `owner`/`creator` = người tải lên (đọc từ DB). Agent: trần tự chủ theo catalog
    mặc định (đọc L0, xóa mềm L1) + quyền của người được đại diện, như MCP."""

    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self._cache: dict[tuple[UUID, int], PolicyEvaluator] = {}
        self._lock = threading.Lock()

    def allowed(self, caller: Caller, action: str, row: FileRow, now: int) -> bool:
        found = self.rt.store.active_policy(caller.actor)
        if found is None:
            return False
        key = (caller.tenant.id, found[0])
        with self._lock:
            ev = self._cache.get(key)
        if ev is None:
            ev = PolicyEvaluator(PolicyDocument.model_validate(found[1]))
            with self._lock:
                self._cache[key] = ev
        owner = str(row.created_by)
        res: dict[str, Any] = {"owner": owner, "creator": owner}
        p = self.rt.store.person(caller.actor, row.created_by)
        if p is not None and p.unit_key:
            res["department"] = p.unit_key
        pep = IamGuard(ev, _People(self.rt, caller), _NoGrant(), _NoGrant())
        xrn = f"xrn:{caller.alias}:file:{row.id}"
        return pep.evaluate(AccessRequest(caller.iam(), action, xrn, res, now=now)).allowed


# ---- dựng các phần ---------------------------------------------------------------------------


@dataclass
class FilesStack:
    config: FilesConfig
    manager: FileManager
    tokens: UploadTokens
    store: S3FileStore

    @classmethod
    def build(cls, env: Mapping[str, str], *, ensure_bucket: bool = True) -> FilesStack:
        cfg = FilesConfig.from_env(env)
        store = S3FileStore(
            cfg.endpoint, cfg.bucket, cfg.access_key, cfg.secret_key, region=cfg.region
        )
        if ensure_bucket:
            store.ensure_bucket()
        scanner = ClamdScanner(*cfg.clamd) if cfg.clamd else None
        return cls(cfg, FileManager(cfg.policy, store, scanner), UploadTokens(cfg.url_key), store)

    def put_url(self, token: str) -> str:
        return f"{self.config.public_url}/files/v1/uploads/{token}"


class FileToolService:
    """`xt.application.tools.ports.FileService`: tool `upload_file` (TCV1 §5.13).

    `text`/`base64`: byte nhỏ có sẵn trong lời gọi được lưu luôn. `upload_link`: tạo ý định, trả
    `upload_url` (trang cho người) + `put_url` (curl/CLI) hạn ngắn; byte KHÔNG đi qua LLM."""

    def __init__(self, rt: Runtime, stack: FilesStack) -> None:
        self.rt, self.stack = rt, stack

    def plan_upload(self, ctx: CallContext, args: Args) -> MutationPlan:
        mgr = self.stack.manager
        name, declared = mgr.declared_type(str(args["name"]), args.get("content_type"))
        source = str(args["source"])
        data: bytes | None = None
        if source == "text":
            data = str(args["text"]).encode("utf-8")
        elif source == "base64":
            try:
                data = base64.b64decode(str(args["base64"]), validate=True)
            except (binascii.Error, ValueError):
                raise invalid_request(
                    "base64 is not valid.", "bad_base64", parameter="base64"
                ) from None
        sha = None
        if data is not None:
            if len(data) > mgr.policy.max_inline_bytes:
                raise too_large(mgr.policy.max_inline_bytes)
            mgr.check_bytes(declared, data[:SNIFF_BYTES], len(data))
            sha = hashlib.sha256(data).hexdigest()
        return MutationPlan(
            level=RiskLevel.L1,
            summary=f"Lưu tệp '{name}' ({declared})",
            digest_source={
                "action": "file.create",
                "name": name,
                "content_type": declared,
                "source": source,
                "sha256": sha,
            },
            payload={"name": name, "declared": declared, "data": data},
        )

    def commit(self, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        caller = self.rt.caller(ctx)
        repo = PgFileRepository(self.rt.uow, caller.actor)
        p = plan.payload
        mgr = self.stack.manager
        mode = effective_mode(caller.profile.llm_egress, ctx.client_class)
        person = caller.person.id
        if p["data"] is not None:
            out = mgr.store_inline(
                repo,
                person=person,
                client_id=caller.client_id,
                name=p["name"],
                declared=p["declared"],
                data=p["data"],
                now=ctx.now,
            )
            if out.error is not None:
                raise out.error
            data: dict[str, Any] = {
                "file": tool_view(out.row, mask_name(out.row.name, mode)),
                "upload_url": None,
                "put_url": None,
                "expires_at": None,
            }
            return CommitResult(data, operation_ref=f"file:{out.row.id}")
        row = mgr.intent(
            repo,
            person=person,
            client_id=caller.client_id,
            name=p["name"],
            declared=p["declared"],
            now=ctx.now,
        )
        assert row.upload_expires_at is not None
        exp = int(row.upload_expires_at.timestamp())
        url = self.stack.put_url(self.stack.tokens.issue(caller.alias, row.id, person, exp))
        data = {
            "file": tool_view(row, mask_name(row.name, mode)),
            "upload_url": url,
            "put_url": url,
            "expires_at": iso(row.upload_expires_at),
        }
        return CommitResult(data, operation_ref=f"file:{row.id}")


class CompositionFilesBackend:
    """`FilesBackend` của REST trên runtime composition: mỗi lời gọi một transaction RLS."""

    def __init__(self, rt: Runtime, stack: FilesStack, pep: FilesPep) -> None:
        self.rt, self.stack, self.pep = rt, stack, pep
        self.max_bytes = stack.config.policy.max_bytes

    @property
    def mgr(self) -> FileManager:
        return self.stack.manager

    def _caller(self, ctx: CallContext) -> Caller:
        try:
            return self.rt.resolve(ctx.actor, ctx.trace_id)
        except CallerError as e:
            raise e.error from None

    def _view(self, caller: Caller, ctx: CallContext, row: FileRow) -> dict[str, Any]:
        mode = effective_mode(caller.profile.llm_egress, ctx.client_class)
        return view(row, mask_name(row.name, mode))

    def _readable(
        self, caller: Caller, ctx: CallContext, repo: PgFileRepository, fid: UUID, lock: bool
    ) -> FileRow:
        row = repo.get(fid, lock=lock)
        now = int(ctx.now.timestamp())
        if row is None or not self.pep.allowed(caller, "file.read", row, now):
            raise not_found(f"file:{fid}")  # tenant khác / không được đọc: cùng một câu trả lời
        return row

    @staticmethod
    def _raise(out: Outcome) -> FileRow:
        if out.error is not None:
            raise out.error
        return out.row

    # ---- presigned ---------------------------------------------------------------------------

    def _grant_actor(self, token: str, trace_id: str) -> tuple[Any, ModelActor]:
        now = datetime.now(UTC)
        grant = self.stack.tokens.verify(token, int(now.timestamp()))
        tenant = self.rt.tenant(grant.tenant)
        if tenant is None:
            raise access_denied("This upload link is not valid.", "upload_url_invalid", None)
        actor = ModelActor(tenant, grant.person, (), trace_id=trace_id[:128])
        return (grant, now), actor

    def _grant_tx(self, actor: ModelActor, fn: Any) -> Any:
        with self.rt.uow.scope(actor):
            return self.rt.uow.run(fn)

    def upload_info(self, token: str, trace_id: str) -> UploadInfo:
        (grant, now), actor = self._grant_actor(token, trace_id)

        def body() -> Outcome:
            repo = PgFileRepository(self.rt.uow, actor)
            row = repo.get(grant.file_id)
            if row is None or row.created_by != grant.person:
                raise access_denied("This upload link is not valid.", "upload_url_invalid", None)
            row = self.mgr.current(repo, row, now)
            if row.status == "expired":
                return Outcome(
                    row,
                    access_denied("This upload link has expired.", "upload_url_expired", None),
                )
            if row.status != "awaiting_upload":
                return Outcome(
                    row,
                    precondition_failed("This upload link was already used.", "already_uploaded"),
                )
            return Outcome(row)

        row = self._raise(self._grant_tx(actor, body))
        return UploadInfo(
            row.name,
            row.declared_type,
            row.status,
            iso(row.upload_expires_at) if row.upload_expires_at else None,
            self.max_bytes,
        )

    def ingest(self, token: str, spool: Spool, trace_id: str, sha256: str | None) -> dict[str, Any]:
        (grant, now), actor = self._grant_actor(token, trace_id)

        def body() -> Outcome:
            repo = PgFileRepository(self.rt.uow, actor)
            row = repo.get(grant.file_id, lock=True)
            if row is None or row.created_by != grant.person:
                raise access_denied("This upload link is not valid.", "upload_url_invalid", None)
            out = self.mgr.ingest(repo, row, spool, now)
            if out.error is None and sha256 is not None:
                out = self.mgr.finalize(repo, out.row, sha256, now)
            return out

        row = self._raise(self._grant_tx(actor, body))
        return view(row)  # người cầm link chính là người đặt tên tệp

    # ---- có Bearer ---------------------------------------------------------------------------

    def finalize(self, ctx: CallContext, file_id: UUID, sha256: str) -> dict[str, Any]:
        caller = self._caller(ctx)

        def body() -> Outcome:
            repo = PgFileRepository(self.rt.uow, caller.actor)
            row = repo.get(file_id, lock=True)
            now = int(ctx.now.timestamp())
            if (
                row is None
                or row.created_by != caller.person.id
                or not self.pep.allowed(caller, "file.create", row, now)
            ):
                raise not_found(f"file:{file_id}")
            return self.mgr.finalize(repo, row, sha256, ctx.now)

        out: Outcome = self.rt.run_as(caller, ctx.actor, body)
        return self._view(caller, ctx, self._raise(out))

    def metadata(self, ctx: CallContext, file_id: UUID) -> dict[str, Any]:
        caller = self._caller(ctx)

        def body() -> FileRow:
            repo = PgFileRepository(self.rt.uow, caller.actor)
            row = self._readable(caller, ctx, repo, file_id, lock=False)
            return self.mgr.current(repo, row, ctx.now)

        row: FileRow = self.rt.run_as(caller, ctx.actor, body)
        return self._view(caller, ctx, row)

    def list_mine(self, ctx: CallContext, limit: int) -> list[dict[str, Any]]:
        caller = self._caller(ctx)

        def body() -> list[FileRow]:
            repo = PgFileRepository(self.rt.uow, caller.actor)
            return repo.created_by(caller.person.id, limit)

        rows: list[FileRow] = self.rt.run_as(caller, ctx.actor, body)
        return [self._view(caller, ctx, r) for r in rows]

    def content(self, ctx: CallContext, file_id: UUID) -> Download:
        caller = self._caller(ctx)

        def body() -> tuple[FileRow, Any]:
            repo = PgFileRepository(self.rt.uow, caller.actor)
            row = self.mgr.current(repo, self._readable(caller, ctx, repo, file_id, False), ctx.now)
            err = self.mgr.download_error(row, caller.person.id)
            if err is not None:
                raise err
            return row, self.mgr.open(repo, row)

        row, chunks = self.rt.run_as(caller, ctx.actor, body)
        mode = effective_mode(caller.profile.llm_egress, ctx.client_class)
        assert row.sha256 is not None and row.size_bytes is not None
        return Download(
            mask_name(row.name, mode),
            row.content_type or row.declared_type,
            row.size_bytes,
            row.sha256,
            chunks,
        )

    def delete(self, ctx: CallContext, file_id: UUID) -> dict[str, Any]:
        caller = self._caller(ctx)

        def body() -> FileRow:
            repo = PgFileRepository(self.rt.uow, caller.actor)
            row = self._readable(caller, ctx, repo, file_id, lock=True)
            if not self.pep.allowed(caller, "file.delete", row, int(ctx.now.timestamp())):
                raise access_denied(
                    "You may read this file but not delete it.",
                    "iam_denied",
                    None,
                    remediation="Ask the uploader or an administrator.",
                )
            ctx.ensure_active(RiskLevel.L1)
            return self.mgr.soft_delete(repo, row, ctx.now)

        row: FileRow = self.rt.run_as(caller, ctx.actor, body)
        return {**self._view(caller, ctx, row), "deleted": True}

    def rescan(self, tenant_alias: str, limit: int = 20) -> int:
        """Quét lại tệp `scanning` của một tenant (clamd từng lỗi). Cho worker/cron gọi."""
        tenant = self.rt.tenant(tenant_alias)
        if tenant is None or self.mgr.scanner is None:
            return 0
        actor = Runtime.lookup_actor(tenant)

        def body() -> int:
            repo = PgFileRepository(self.rt.uow, actor)
            rows = repo.scanning(limit)
            return sum(1 for r in rows if self.mgr.scan(repo, r).status != "scanning")

        return int(self._grant_tx(actor, body))


# ---- danh tính ----------------------------------------------------------------------------


def files_resource_settings(
    rs: ResourceServerSettings, env: Mapping[str, str]
) -> ResourceServerSettings:
    problems: list[str] = []
    audience = env.get("XT_FILES_AUDIENCE", "").strip() or DEFAULT_AUDIENCE
    others = {rs.resource, env.get("XT_WORKFLOW_AUDIENCE", "").strip() or "xt-workflow", "xt-data"}
    if audience in others:
        problems.append("XT_FILES_AUDIENCE phải khác audience MCP / workflow / data")
    if any(c.isspace() for c in audience):
        problems.append("XT_FILES_AUDIENCE không được có khoảng trắng")
    scopes = frozenset(_words(env, "XT_FILES_REQUIRED_SCOPES") or (DEFAULT_AUDIENCE,))
    if "offline_access" in scopes:
        problems.append("XT_FILES_REQUIRED_SCOPES không được chứa offline_access")
    clients = frozenset(_words(env, "XT_FILES_ALLOWED_CLIENTS")) or rs.allowed_clients
    if not clients:
        problems.append("XT_FILES_ALLOWED_CLIENTS rỗng (và XT_MCP_ALLOWED_CLIENTS rỗng)")
    if clients & rs.human_clients:
        problems.append("XT_FILES_ALLOWED_CLIENTS không được chứa client người")
    if problems:
        raise FilesConfigError(problems)
    return replace(
        rs,
        resource=audience,
        allowed_clients=clients,
        required_scopes=scopes,
        scopes_supported=tuple(sorted(scopes)),
        dynamic_connectors=False,
    )


def _audiences(token: str) -> list[str]:
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise InvalidTokenError("malformed") from None
    aud = claims.get("aud") if isinstance(claims, dict) else None
    if isinstance(aud, str):
        return [aud]
    return [a for a in aud if isinstance(a, str)] if isinstance(aud, list) else []


def _quote(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"')


@dataclass(frozen=True, slots=True)
class IdentityFilesAuth:
    """Như `IdentityWorkflowAuth` (workflow_http): token mang thêm aud của bề mặt khác bị từ
    chối, để token lộ ở một bề mặt không dùng được ở bề mặt kia."""

    settings: ResourceServerSettings
    verifier: AccessTokenVerifier
    foreign_audiences: frozenset[str] = frozenset()

    def bearer(self, authorization: str | None) -> str | None:
        return extract_bearer(authorization)

    def verify(self, token: str) -> Principal:
        principal = self.verifier.verify(token)
        if self.foreign_audiences & set(_audiences(token)):
            raise InvalidTokenError("audience", "token also issued for another resource")
        return principal

    def challenge(self, error: IdentityError | None) -> AuthChallenge:
        scope = " ".join(sorted(self.settings.required_scopes))
        if isinstance(error, InsufficientScopeError):
            need = " ".join(sorted(set(error.required) | self.settings.required_scopes))
            value = f'Bearer realm="{REALM}", error="insufficient_scope", scope="{_quote(need)}"'
            return AuthChallenge(403, {"WWW-Authenticate": value})
        parts = [f'realm="{REALM}"']
        if isinstance(error, InvalidTokenError):
            desc = _DESCRIPTIONS.get(error.reason, "The access token is invalid")
            parts += ['error="invalid_token"', f'error_description="{_quote(desc)}"']
        parts.append(f'scope="{_quote(scope)}"')
        return AuthChallenge(401, {"WWW-Authenticate": "Bearer " + ", ".join(parts)})


# ---- ghép vào app ---------------------------------------------------------------------------


def attach_files(prepared: Any, env: Mapping[str, str]) -> CompositionFilesBackend:
    """Gắn tool `upload_file` thật vào dispatcher của `prepared`; trả backend REST."""
    services = prepared.services
    if not isinstance(services, Composition):
        raise FilesConfigError(["/files/v1 cần XT_MCP_SERVICES=xt.entrypoints.composition:build"])
    if not prepared.dispatcher.settings.available("upload_file"):
        raise FilesConfigError(
            ["XT_FILES_S3_ENDPOINT đã đặt nhưng XT_MCP_CAPABILITIES chưa gồm upload_file"]
        )
    stack = FilesStack.build(env)
    rt = services.runtime
    d = prepared.dispatcher
    d.services = replace(d.services, files=FileToolService(rt, stack))
    return CompositionFilesBackend(rt, stack, FilesPep(rt))


def files_mount(prepared: Any, env: Mapping[str, str]) -> Mount:
    """`Mount("/files")` (REST `/files/v1`) + tool `upload_file` thật trên cùng dispatcher."""
    backend = attach_files(prepared, env)
    rs: ResourceServerSettings = prepared.rs
    fs = files_resource_settings(rs, env)
    verifier = AccessTokenVerifier(
        fs,
        build_jwks_cache(fs.idp),
        clients=StaticClientAllowlist(fs.allowed_clients, fs.human_clients),
    )
    revocation = build_revocation_guard(load_introspection_settings(env))
    foreign = {rs.resource, env.get("XT_WORKFLOW_AUDIENCE", "").strip() or "xt-workflow"}
    return files_routes(
        prepared.dispatcher,
        IdentityFilesAuth(fs, verifier, frozenset(foreign)),
        backend,
        classify=ClientClassifier(prepared.cfg.local_clients),
        ensure_active=revocation.ensure_active,
        server_url=backend.stack.config.public_url,
    )


def with_files_api(prepared: Any, env: Mapping[str, str], app: Starlette) -> Starlette:
    """App + `/files/v1` khi `XT_FILES_S3_ENDPOINT` đã đặt (giữ lifespan của app gốc)."""
    if not FilesConfig.enabled(env):
        return app
    return Starlette(
        routes=[files_mount(prepared, env), *app.routes],
        lifespan=app.router.lifespan_context,
    )
