# SPDX-License-Identifier: Apache-2.0
"""Làn PII: middleware bọc MỌI tool call (input + output + lỗi) trước adapter MCP.

Chiều vào : token ⟨NV-0042.field⟩ -> giá trị thật (kiểm phiên, kiểm không hạ cấp pii, chặn filter/sort oracle).
Chiều ra  : chiếu field theo (mode, pii) -> raw | token+gợi ý | token | bỏ; sau đó quét SÂU mọi chuỗi
            (kể cả thông điệp lỗi) bằng (1) danh sách giá trị thật đã biết trong lời gọi/phiên và
            (2) bộ nhận dạng VN. Engine bên dưới không cần cẩn thận; làn này là chốt chặn duy nhất ra LLM.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from . import recognize
from .policy import Mode, Principal, TenantPolicy, action_for
from .spec import LEVEL_RANK, ModelSpec
from .store import EngineError, Store
from .vault import TOKEN_RE, Vault

OMITTED = "⟨đã ẩn⟩"
AGENT_NOTE = ("Giá trị dạng ⟨MÃ.trường⟩ là token PII: truyền NGUYÊN token vào tool để xT dùng giá trị thật; "
              "không đoán giá trị. Người dùng xem giá trị thật trong thẻ xT (nút 'Xem'), không qua chat.")


class LaneError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class ToolShape:
    """Khai báo arg nào mang dữ liệu theo field của model (để detokenize đúng mức pii đích)."""
    model_arg: str | None = "model"
    data_args: tuple[str, ...] = ()        # dict {field: value}
    filter_args: tuple[str, ...] = ()      # dict {field: value | {op, value}}
    sort_args: tuple[str, ...] = ()        # list ["field", "-field"]


@dataclass
class CallCtx:
    p: Principal
    mode: Mode
    scrub: dict[str, str] = field(default_factory=dict)      # biến thể chuỗi thật -> token thay thế
    detok: list[str] = field(default_factory=list)
    egress: Counter = field(default_factory=Counter)          # "model.field:action" -> số lần
    hits: Counter = field(default_factory=Counter)            # kind -> số lần bộ nhận dạng bắt
    scrubbed: int = 0


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _variants(v: Any) -> list[str]:
    if isinstance(v, bool) or v is None:
        return []
    if isinstance(v, int):
        s = str(v)
        if len(s) < 4:
            return []
        out = {s, f"{v:,}", f"{v:,}".replace(",", "."), f"{v:,}".replace(",", " ")}
        if v >= 1_000_000 and v % 100_000 == 0:          # 25tr, 18,5 triệu, 18.5 tr
            for m in {f"{v / 1_000_000:g}", f"{v / 1_000_000:g}".replace(".", ",")}:
                out.update(m + suf for suf in ("tr", " tr", " triệu", "triệu"))
        return list(out)
    s = _nfc(str(v)).strip()
    if len(s) < 3:
        return []
    out = {s}
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 8 and len(digits) >= len(s) - 4:   # số điện thoại/CCCD có dấu phân cách
        out.add(digits)
    return list(out)


def _pattern(v: str) -> re.Pattern:
    """Chữ: biên từ (tránh che nhầm giữa từ). Số/dạng số: KHÔNG dùng biên \\w (vì '25.000.000đ', '25000000VND',
    hai số dính nhau '0912345678001090012345' đều phải bị che); chỉ chặn chữ số liền trước."""
    if re.fullmatch(r"[\d.,\s]+(?:tr| tr| triệu|triệu)?", v):
        return re.compile(r"(?<![\d.,])" + re.escape(v), re.IGNORECASE)
    return re.compile(r"(?<!\w)" + re.escape(v) + r"(?!\w)", re.IGNORECASE)


def hint(kind: str | None, v: Any) -> str | None:
    s = _nfc(str(v))
    if kind == "name":
        w = s.split()
        return " ".join([w[0], *[x[0] + "***" for x in w[1:-1]], w[-1][0]]) if len(w) > 1 else s[0] + "***"
    if kind in ("phone", "bank"):
        d = re.sub(r"\D", "", s)
        return "*" * (len(d) - 3) + d[-3:]
    if kind == "national_id":
        return "*" * (len(s) - 4) + s[-4:]
    if kind == "email" and "@" in s:
        u, d = s.split("@", 1)
        return f"{u[0]}***@{d}"
    if kind == "address":
        return "***, " + s.rsplit(",", 1)[-1].strip()
    return None


class Audit:
    """Nhật ký egress (hash-chain). KHÔNG bao giờ chứa giá trị thật: chỉ field, hành động, token."""

    def __init__(self, conn: sqlite3.Connection):
        self.c = conn
        self.c.execute("""CREATE TABLE IF NOT EXISTS egress_audit(
            seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, tenant TEXT, azp TEXT, sub TEXT, tier TEXT,
            mode TEXT, tool TEXT, outcome TEXT, egress_json TEXT, detok_json TEXT, scrubbed INTEGER,
            hits_json TEXT, prev_hash TEXT, entry_hash TEXT)""")

    def write(self, ctx: CallCtx, tool: str, outcome: str) -> dict:
        prev = (self.c.execute("SELECT entry_hash FROM egress_audit ORDER BY seq DESC LIMIT 1").fetchone() or ["0" * 64])[0]
        row = {"ts": time.time(), "tenant": ctx.p.tenant, "azp": ctx.p.azp, "sub": ctx.p.sub, "tier": ctx.p.tier,
               "mode": ctx.mode, "tool": tool, "outcome": outcome,
               "egress_json": json.dumps(dict(sorted(ctx.egress.items())), ensure_ascii=False),
               "detok_json": json.dumps(ctx.detok, ensure_ascii=False), "scrubbed": ctx.scrubbed,
               "hits_json": json.dumps(dict(ctx.hits))}
        h = hashlib.sha256((prev + json.dumps(row, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
        self.c.execute("INSERT INTO egress_audit(ts,tenant,azp,sub,tier,mode,tool,outcome,egress_json,detok_json,"
                       "scrubbed,hits_json,prev_hash,entry_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (*row.values(), prev, h))
        return row

    def rows(self) -> list[dict]:
        cur = self.c.execute("SELECT * FROM egress_audit ORDER BY seq")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


class PiiLane:
    def __init__(self, store: Store, vault: Vault, audit: Audit, policy: TenantPolicy):
        self.store, self.vault, self.audit, self.policy = store, vault, audit, policy

    # ------------------------------------------------------------- vào ----
    def _detok(self, ctx: CallCtx, v: Any, target_level: str, where: str) -> Any:
        if not isinstance(v, str) or "⟨" not in v:
            return v
        ms = list(TOKEN_RE.finditer(v))
        if not ms:
            return v
        out, last = [], 0
        for m in ms:
            e = self.vault.get(ctx.p.session_key, m.group(0))
            if e is None:
                raise LaneError("PII_TOKEN_UNKNOWN", f"Token {m.group(0)} không thuộc phiên này hoặc đã hết hạn; "
                                                     f"đọc lại bản ghi để lấy token mới.")
            if LEVEL_RANK[e.level] > LEVEL_RANK[target_level]:
                raise LaneError("PII_DOWNGRADE", f"Không được ghi {m.group(0)} (pii={e.level}) vào '{where}' "
                                                 f"(pii={target_level}): sẽ làm lộ dữ liệu qua trường mức thấp hơn.")
            ctx.detok.append(m.group(0))
            for var in _variants(e.value):
                ctx.scrub.setdefault(var, m.group(0))
            if m.span() == (0, len(v)):
                return e.value                      # nguyên token -> giữ kiểu gốc (int lương, ...)
            out.append(v[last:m.start()]); out.append(str(e.value)); last = m.end()
        out.append(v[last:])
        return "".join(out)

    def _detok_args(self, ctx: CallCtx, shape: ToolShape, args: dict) -> dict:
        m: ModelSpec | None = None
        if shape.model_arg and shape.model_arg in args:
            m = self.store.model(args[shape.model_arg])
        strict = ctx.mode != "full"
        res = {}
        for k, v in args.items():
            if k in shape.data_args and isinstance(v, dict) and m:
                res[k] = {fk: self._detok(ctx, fv, (m.field(fk).pii if m.field(fk) else "none"), f"{m.name}.{fk}")
                          for fk, fv in v.items()}
            elif k in shape.filter_args and isinstance(v, dict) and m:
                nf = {}
                for fk, cond in v.items():
                    f = m.field(fk)
                    lvl = f.pii if f else "none"
                    op, val = (cond.get("op", "eq"), cond.get("value")) if isinstance(cond, dict) else ("eq", cond)
                    if strict and lvl == "basic" and op not in self.policy.basic_filter_ops:
                        raise LaneError("PII_FILTER_DENIED", f"Client này chỉ được lọc '{m.name}.{fk}' (pii=basic) bằng "
                                                             f"{sorted(self.policy.basic_filter_ops)}; contains/gt/lt cho phép dò từng ký tự.")
                    if strict and lvl == "sensitive" and not (op == "eq" and isinstance(val, str)
                                                              and TOKEN_RE.fullmatch(val)):
                        # chặn oracle: luong > X, sắp xếp theo lương, dò bằng giá trị thử
                        raise LaneError("PII_FILTER_DENIED", f"Client này chỉ được lọc '{m.name}.{fk}' (pii=sensitive) "
                                                             f"bằng phép eq với token; dùng Desk/LLM nội bộ để lọc theo giá trị.")
                    dv = self._detok(ctx, val, lvl, f"{m.name}.{fk}")
                    nf[fk] = {"op": op, "value": dv} if isinstance(cond, dict) else dv
                res[k] = nf
            elif k in shape.sort_args and isinstance(v, list) and m:
                for s in v:
                    f = m.field(str(s).lstrip("-"))
                    if strict and f and f.pii == "sensitive":
                        raise LaneError("PII_SORT_DENIED", f"Client này không được sắp xếp theo '{m.name}.{f.name}' (pii=sensitive).")
                res[k] = v
            else:
                res[k] = self._detok_any(ctx, v, k)
        return res

    def _detok_any(self, ctx: CallCtx, v: Any, where: str) -> Any:
        if isinstance(v, dict):
            return {k: self._detok_any(ctx, x, f"{where}.{k}") for k, x in v.items()}
        if isinstance(v, list):
            return [self._detok_any(ctx, x, where) for x in v]
        return self._detok(ctx, v, "none", where)   # arg ngoài schema = mức none

    # -------------------------------------------------------------- ra ----
    def _project_record(self, ctx: CallCtx, m: ModelSpec, row: dict) -> dict:
        key = row.get(m.key_field)
        out: dict[str, Any] = {"rev": row.get("rev")}
        hints, omitted = {}, []
        for f in m.fields:
            v = row.get(f.name)
            act = action_for(ctx.mode, f.pii)
            if f.pii != "none" and v is not None:
                ctx.egress[f"{m.name}.{f.name}:{act}"] += 1
            if v is None or act == "raw":
                out[f.name] = v
                continue
            if act == "omit":
                omitted.append(f.name)
                for var in _variants(v):
                    ctx.scrub.setdefault(var, OMITTED)
                continue
            tok = self.vault.bind(ctx.p.session_key, m.name, key, f.name, f.pii, f.pii_kind, v)
            for var in _variants(v):
                ctx.scrub.setdefault(var, tok)
            out[f.name] = tok
            if act == "token_hint" and self.policy.hints and (h := hint(f.pii_kind, v)):
                hints[f.name] = h
        if hints:
            out["_hint"] = hints
        if omitted:
            out["_omitted"] = omitted
        return out


    def _project(self, ctx: CallCtx, obj: Any) -> Any:
        if isinstance(obj, dict) and "model" in obj and ("record" in obj or "records" in obj):
            m = self.store.model(obj["model"])
            o = dict(obj)
            if "record" in o and isinstance(o["record"], dict):
                o["record"] = self._project_record(ctx, m, o["record"])
            if "records" in o:
                o["records"] = [self._project_record(ctx, m, r) for r in o["records"]]
            return o
        return obj

    @staticmethod
    def _outside_tokens(s: str, fn: Callable[[str], str]) -> str:
        """Áp fn lên các đoạn KHÔNG phải token (token có sẵn giữ nguyên)."""
        out, last = [], 0
        for m in TOKEN_RE.finditer(s):
            out.append(fn(s[last:m.start()])); out.append(m.group(0)); last = m.end()
        out.append(fn(s[last:]))
        return "".join(out)

    def _scrub_str(self, ctx: CallCtx, s: str, pats: list[tuple[re.Pattern, str]]) -> str:
        def known(t: str) -> str:                      # lưới 1: giá trị thật đã biết
            for rx, rep in pats:
                t, n = rx.subn(rep, t)
                ctx.scrubbed += n
            return t

        def recog(t: str) -> str:                      # lưới 2: bộ nhận dạng VN
            out, c = [], 0
            for a, b, kind, level in recognize.find(t):
                out.append(t[c:a]); out.append(self.vault.free(ctx.p.session_key, kind, level, t[a:b]))
                ctx.hits[kind] += 1; c = b
            out.append(t[c:])
            return "".join(out)
        s = self._outside_tokens(_nfc(s), known)
        return self._outside_tokens(s, recog)

    def _deep_scrub(self, ctx: CallCtx, obj: Any) -> Any:
        for tok, val in self.vault.all_values(ctx.p.session_key):   # giá trị phiên đã thấy trước đây
            for var in _variants(val):
                ctx.scrub.setdefault(var, tok)
        pats = [(_pattern(k), v) for k, v in sorted(ctx.scrub.items(), key=lambda kv: -len(kv[0]))]

        def walk(o):
            if isinstance(o, str):
                return self._scrub_str(ctx, o, pats)
            if isinstance(o, dict):
                return {walk(k) if isinstance(k, str) else k: walk(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [walk(x) for x in o]
            if isinstance(o, int) and not isinstance(o, bool) and str(o) in ctx.scrub:
                return ctx.scrub[str(o)]               # số thật (lương) nằm ở chỗ khác trong output
            return o
        return walk(obj)

    # ------------------------------------------------------------- call ----
    def call(self, p: Principal, tool: str, shape: ToolShape, fn: Callable[..., Any], args: dict) -> tuple[dict, bool]:
        ctx = CallCtx(p=p, mode=self.policy.mode_for(p))
        seen: list[tuple[ModelSpec, dict]] = []
        self.store.on_row = lambda m, r: seen.append((m, r))
        is_error = False
        try:
            real_args = self._detok_args(ctx, shape, args)
            env: dict[str, Any] = {"ok": True, "result": fn(**real_args)}
            outcome = "ok"
        except (LaneError, EngineError) as e:
            env, outcome, is_error = {"ok": False, "error": {"code": e.code, "message": e.message}}, e.code, True
        except Exception as e:                          # lỗi bất ngờ: vẫn đi qua làn (message có thể chứa input)
            env, outcome, is_error = {"ok": False, "error": {"code": "INTERNAL",
                                                             "message": f"{type(e).__name__}: {e}"}}, "INTERNAL", True
        finally:
            self.store.on_row = lambda m, r: None
        if ctx.mode != "full":
            # mọi hàng engine đã đọc trong lời gọi (kể cả hàng không trả về) đều vào danh sách che
            for m, r in seen:
                for f in m.fields:
                    if f.pii != "none" and r.get(f.name) is not None:
                        act = action_for(ctx.mode, f.pii)
                        rep = OMITTED if act == "omit" else self.vault.bind(
                            p.session_key, m.name, r[m.key_field], f.name, f.pii, f.pii_kind, r[f.name])
                        for var in _variants(r[f.name]):
                            ctx.scrub.setdefault(var, rep)
            if "result" in env:
                env["result"] = self._project(ctx, env["result"])
            env = self._deep_scrub(ctx, env)
            env["_pii"] = {"mode": ctx.mode, "note": AGENT_NOTE}
        else:
            if "result" in env:
                self._project(ctx, env["result"])          # chỉ để đếm egress raw cho audit
        self.audit.write(ctx, tool, outcome)
        return env, is_error
