"""MCP stdio 子进程 + Web 接口（演示模式，不调用大模型、不访问网络）。"""
import asyncio
import json

from mcp import Client

from app import config as C
from app.agent import server_params


def test_mcp_stdio_subprocess_mock(mock_cfg):
    s = C.load(mock_cfg)

    async def go():
        async with Client(server_params(s), read_timeout_seconds=60) as c:
            tools = await c.list_tools()
            r = await c.call_tool("get_account", {})
            rb = await c.call_tool("search_runbook", {"query": "-2015"})
            return [t.name for t in tools.tools], r, rb
    names, r, rb = asyncio.run(go())
    assert "get_daily_report" in names and "search_runbook" in names
    assert not r.is_error
    data = r.structured_content if r.structured_content is not None else json.loads(r.content[0].text)
    assert data["mode"] == "MOCK" and data["position_count"] == 3
    assert "R5" in "".join(b.text for b in rb.content if b.type == "text")


def test_web_page_and_overview(mock_cfg):
    from starlette.testclient import TestClient
    from app.web import create_app
    c = TestClient(create_app(C.load(mock_cfg)))          # 不进 lifespan：不预热 Agent
    r = c.get("/")
    assert r.status_code == 200 and "AI 运维助手" in r.text and "ai-fab" in r.text
    ov = c.get("/api/overview").json()
    assert ov["mode"] == "MOCK"
    assert all(ov[k]["ok"] for k in ("account", "open_orders", "income", "spot", "risk"))
    assert c.post("/api/ask", json={"question": "  "}).status_code == 400
    assert c.post("/api/ask", content=b"not json", headers={"Content-Type": "application/json"}).status_code == 400
    assert c.post("/api/ask", json={"question": "x" * 2001}).status_code == 400
