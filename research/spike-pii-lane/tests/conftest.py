# SPDX-License-Identifier: Apache-2.0
import asyncio
import json
import sqlite3
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mcp import Client  # noqa: E402

from xtpii.lane import _variants  # noqa: E402
from xtpii.policy import ClientRegistry, Principal, TenantPolicy  # noqa: E402
from xtpii.server import App  # noqa: E402
from xtpii.spec import CATALOG  # noqa: E402

SANDBOX = "https://3f2a9c0d1e4b5a6978c0d1e2f3a4b5c6.claudemcpcontent.com"
REGISTRY = ClientRegistry({"xt-desk-agent": "local", "xt-desk": "human"})

SEED = [
    {"ma_nv": "NV-0042", "ho_ten": "Nguyễn Văn An", "phong_ban": "Kỹ thuật", "so_dien_thoai": "0912345678",
     "email": "an.nguyen@congty.vn", "dia_chi": "12 Lý Thường Kiệt, Hoàn Kiếm, Hà Nội", "so_cccd": "001090012345",
     "luong": 25000000, "suc_khoe": "Tiểu đường type 2", "so_tai_khoan": "0011004455667", "ghi_chu": "Trưởng nhóm"},
    {"ma_nv": "NV-0043", "ho_ten": "Trần Thị Bích Ngọc", "phong_ban": "Kế toán", "so_dien_thoai": "0987001122",
     "email": "ngoc.tran@congty.vn", "dia_chi": "45 Nguyễn Huệ, Quận 1, TP. Hồ Chí Minh", "so_cccd": "079185004321",
     "luong": 18500000, "suc_khoe": "Đang điều trị viêm gan B", "so_tai_khoan": "1903555777999", "ghi_chu": None},
    {"ma_nv": "NV-0044", "ho_ten": "Lê Hoàng Phúc", "phong_ban": "Kinh doanh", "so_dien_thoai": "0356789012",
     "email": "phuc.le@congty.vn", "dia_chi": "7 Trần Phú, Hải Châu, Đà Nẵng", "so_cccd": "048200007788",
     "luong": 32000000, "suc_khoe": None, "so_tai_khoan": None,
     # PII nằm trong trường pii=none: chỉ bộ nhận dạng bắt được
     "ghi_chu": "Người thân: chị Mai 0978 111 222, CCCD 031195001234, mail mai.le@gmail.com"},
]
FREE_PII = ["0978 111 222", "0978111222", "031195001234", "mai.le@gmail.com"]


def raw_corpus() -> list[str]:
    out = set(FREE_PII)
    m = CATALOG["nhan_vien"]
    for r in SEED:
        for f in m.fields:
            if f.pii != "none" and r.get(f.name) is not None:
                out.update(_variants(r[f.name]))
    return sorted(out, key=len, reverse=True)


class Ctx:
    def __init__(self):
        self.p = Principal(tenant="t1", azp="5c1e7a0e-dcr-claude", sub="u-hr-01", sid="sid-A",
                           roles=frozenset({"hr"}), tier=REGISTRY.tier_of("5c1e7a0e-dcr-claude"))
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.policy = TenantPolicy()
        self.app = App(self.conn, self.policy, lambda: self.p, public_url="https://xt.example",
                       sandbox_origins=frozenset({SANDBOX}))
        for r in SEED:
            self.app.store.create("nhan_vien", r)

    def as_client(self, azp: str, sid: str = "sid-A", roles=frozenset({"hr"}), sub="u-hr-01"):
        self.p = Principal(tenant="t1", azp=azp, sub=sub, sid=sid, roles=roles, tier=REGISTRY.tier_of(azp))

    def call(self, tool: str, args: dict):
        async def go():
            async with Client(self.app.mcp) as c:
                return await c.call_tool(tool, args)
        res = asyncio.run(go())
        wire = res.model_dump_json(by_alias=True)                 # đúng thứ đi ra dây JSON-RPC
        env = json.loads(res.content[0].text) if res.content else None
        return res, wire, env

    def db(self, model: str, key_field: str, key: str) -> dict:
        self.conn.row_factory = sqlite3.Row
        return dict(self.conn.execute(f'SELECT * FROM "{model}" WHERE "{key_field}"=?', (key,)).fetchone())


def assert_no_raw(blob: str):
    leaked = [v for v in raw_corpus() if v.lower() in blob.lower()]
    assert not leaked, f"RÒ RỈ: {leaked}"


@pytest.fixture
def x():
    return Ctx()
