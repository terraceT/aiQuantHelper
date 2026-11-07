"""⛔ 只读是本产品的硬约束：币安客户端只能发 GET，AI 没有任何写操作工具。"""
import ast
import asyncio
import re

from app import binance
from app.config import ROOT

WRITE_WORDS = re.compile(r"place|create_order|new_order|cancel|close_position|transfer|withdraw|leverage_set|set_|modify|delete",
                         re.I)


def test_binance_client_only_issues_get():
    src = (ROOT / "app" / "binance.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    http_calls = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr in ("get", "post", "put", "delete", "patch", "request")
                  and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "s"}
    assert http_calls == {"get"}
    paths = re.findall(r'"(/(?:fapi|api|sapi)/[^"]+)"', src)
    assert paths and not any(re.search(r"/order$|/batchOrders|/leverage|/transfer|/withdraw|/algoOrder$", p) for p in paths)


def test_binance_client_has_no_write_methods():
    public = [m for m in dir(binance.BinanceClient) if not m.startswith("_")]
    assert not [m for m in public if WRITE_WORDS.search(m)]


def test_mcp_tools_are_all_read_only(mock_cfg, monkeypatch):
    monkeypatch.setenv("AQH_CONFIG", str(mock_cfg))
    from app.mcp_server import mcp

    async def go():
        return await mcp.list_tools()
    tools = asyncio.run(go())
    names = [t.name for t in tools]
    assert len(names) == 9
    assert not [n for n in names if WRITE_WORDS.search(n)]
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)
