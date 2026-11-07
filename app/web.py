"""HTTP 服务（只监听 127.0.0.1）。

  GET  /              页面：账户概览 + 右下角「AI 运维助手」
  GET  /api/overview  账户、持仓、挂单、资金流水、现货、风险体检（每块独立成败）
  GET  /api/health    AI 助手状态 {ok, mode, model, tools}
  POST /api/ask       {"question": "...", "history": [...]} → {answer, steps, tokens, seconds, model, mode}
"""
import asyncio
import contextlib
import logging

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from .agent import QuantAgent
from .config import ROOT
from .datasource import DataSource

log = logging.getLogger("aiquanthelper")
STATIC = ROOT / "app" / "static"


def create_app(settings):
    ds = DataSource(settings)
    state = {"agent": None, "error": None}
    lock = asyncio.Lock()

    async def get_agent():
        """常驻一个 Agent（一个 MCP 子进程）；连接坏了就丢掉，下次请求重连。"""
        async with lock:
            if state["agent"] is None:
                try:
                    state["agent"] = await QuantAgent(settings).start()
                    state["error"] = None
                except Exception as e:
                    state["error"] = f"{type(e).__name__}: {e}"
                    raise
            return state["agent"]

    async def reset_agent():
        a, state["agent"] = state["agent"], None
        if a:
            with contextlib.suppress(Exception):
                await a.close()

    async def page(request):
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    async def overview(request):
        data = await run_in_threadpool(ds.overview)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    async def health(request):
        try:
            a = await get_agent()
            return JSONResponse({"ok": True, "mode": settings.mode, "model": settings.deepseek_model, "tools": len(a.tools)})
        except Exception:
            return JSONResponse({"ok": False, "mode": settings.mode, "error": state["error"]}, status_code=503)

    async def ask(request):
        try:
            body = await request.json()
            assert isinstance(body, dict)
        except Exception:
            return JSONResponse({"error": "请求体必须是 JSON：{\"question\": \"...\"}"}, status_code=400)
        q = str(body.get("question") or "").strip()
        if not q:
            return JSONResponse({"error": "问题不能为空"}, status_code=400)
        if len(q) > 2000:
            return JSONResponse({"error": "问题太长（上限 2000 字）"}, status_code=400)
        history = body.get("history") if isinstance(body.get("history"), list) else []
        try:
            a = await get_agent()
            return JSONResponse(await a.ask(q, history))
        except Exception as e:
            log.exception("ask 失败")
            await reset_agent()
            msg = f"{type(e).__name__}: {str(e)[:500]}"
            if "401" in msg or "Authentication" in msg:
                msg += "\n（大模型 API Key 无效：检查 config.ini 的 [deepseek] api_key，见运维手册 R10）"
            elif "402" in msg or "Insufficient Balance" in msg:
                msg += "\n（大模型账户余额不足：到大模型平台充值（DeepSeek 是 https://platform.deepseek.com），见运维手册 R10）"
            elif "tool" in msg.lower() and ("support" in msg.lower() or "invalid" in msg.lower()):
                msg += "\n（这个模型可能不支持工具调用 Function Calling：AI 运维助手必须用支持工具调用的模型，见 README「换用其他大模型」）"
            return JSONResponse({"error": msg}, status_code=500)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        with contextlib.suppress(Exception):
            await get_agent()           # 预热 MCP 子进程；失败照样起服务，/api/health 会报原因
        yield
        await reset_agent()

    return Starlette(routes=[Route("/", page), Route("/api/overview", overview), Route("/api/health", health),
                             Route("/api/ask", ask, methods=["POST"])], lifespan=lifespan)
