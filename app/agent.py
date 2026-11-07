"""AI 运维 Agent：DeepSeek 大模型（OpenAI 兼容接口）+ 本工程的 MCP Server（stdio 子进程）。

命令行直接问：  .venv\\Scripts\\python -m app.agent "我的仓位是什么情况"
（web.py 里常驻同一个 QuantAgent，连着一个 MCP Server 子进程。）
每次问答写一行审计日志到 logs/audit.jsonl：问题、调用了哪些工具、耗时、token 数（不记录回答全文和任何密钥）。
"""
import asyncio
import json
import os
import sys
import time
from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone

from mcp import Client, StdioServerParameters
from openai import AsyncOpenAI

from . import config as C

MAX_STEPS = 8                  # 一个问题最多调几轮工具
MAX_TOOL_CHARS = 20000         # 单个工具结果交给模型的上限
HISTORY_TURNS = 6              # 多轮对话最多带最近 6 条

SYSTEM = """你是「aiQuantHelper」的 AI 运维助手，帮用户看懂和照看他的币安 U 本位合约账户。
当前时间：北京时间 {now_bj}（UTC {now_utc}）。用户在中国，时间统一用北京时间。
数据模式：{mode_text}

规则：
- 所有数字、状态都必须来自工具返回，绝不编造；工具出错就如实说出错原因（原文里的错误码要保留），再用 search_runbook 查处理办法。
- 问「整体情况 / 日报 / 今天怎么样」优先调用 get_daily_report（一次就够）；问仓位、账户用 get_account；
  问挂单、止损用 get_open_orders；问盈亏、手续费、资金费用 get_income；问风险用 get_risk_report；
  问某个币的价格或资金费率用 get_market_price；问连接、API Key 权限或安全用 check_connectivity；
  问「为什么 / 怎么办 / 是什么意思」或遇到报错时用 search_runbook，并引用条目 id（如 R5）。
- 你只有只读权限：不能下单、撤单、平仓、改杠杆、划转、提现。用户要求这类操作时，明确说你做不到，
  告诉他需要到币安 App 或网页手动操作；可以给建议，但绝不假装已经执行。
- 回答用中文：先一句话结论（正常 / 需要关注 / 需要立即处理），再分点列依据；持仓适合用表格。金额保留 2 位小数。
- 演示模式下，回答开头要提醒一句「当前是演示数据（MOCK），不是你的真实账户」。"""

MODE_TEXT = {
    "LIVE": "LIVE 实盘 —— 工具查询的是用户真实的币安账户（只读）。",
    "MOCK": "MOCK 演示 —— 用户没有配置币安 API Key，工具返回的是本地 mock/ 目录里的模拟数据，不是真实账户。",
}


def _now():
    n = datetime.now(timezone.utc)
    return n.strftime("%Y-%m-%d %H:%M"), n.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")


def _assistant_message(msg):
    """DeepSeek 思考模式 + 工具调用：历史里的 assistant 消息必须带回 reasoning_content，否则 400。"""
    d = {"role": "assistant", "content": msg.content}
    if msg.tool_calls:
        d["tool_calls"] = [{"id": c.id, "type": "function",
                            "function": {"name": c.function.name, "arguments": c.function.arguments}}
                           for c in msg.tool_calls]
    r = getattr(msg, "reasoning_content", None)
    if r:
        d["reasoning_content"] = r
    return d


def _result_text(result):
    if result.is_error:
        return "工具执行出错：" + " ".join(b.text for b in result.content if b.type == "text")
    if result.structured_content is not None:
        text = json.dumps(result.structured_content, ensure_ascii=False, default=str)
    else:
        text = "\n".join(b.text for b in result.content if b.type == "text")
    return text if len(text) <= MAX_TOOL_CHARS else text[:MAX_TOOL_CHARS] + f"……（已截断，原长 {len(text)} 字）"


def server_params(settings):
    return StdioServerParameters(
        command=sys.executable, args=["-m", "app.mcp_server"], cwd=str(C.ROOT),
        env={**os.environ, "AQH_CONFIG": str(settings.config_path), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})


class QuantAgent:
    def __init__(self, settings):
        self.s = settings
        self.llm = AsyncOpenAI(api_key=settings.deepseek_key, base_url=settings.deepseek_base_url, timeout=120)
        self._stack, self.mcp, self.tools = None, None, []
        self._lock = asyncio.Lock()

    async def start(self):
        self._stack = AsyncExitStack()
        self.mcp = await self._stack.enter_async_context(Client(server_params(self.s), read_timeout_seconds=120))
        listed = await self.mcp.list_tools()
        self.tools = [{"type": "function", "function": {"name": t.name, "description": t.description or "",
                                                         "parameters": t.input_schema}} for t in listed.tools]
        return self

    async def close(self):
        if self._stack:
            await self._stack.aclose()
            self._stack = None

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, *exc):
        await self.close()

    async def ask(self, question, history=None):
        """返回 {answer, steps, tokens, seconds, model, mode}。history: [{"role": "user"/"assistant", "content": ...}]"""
        async with self._lock:               # 一个 MCP 连接串行处理
            return await self._ask(question, history or [])

    async def _ask(self, question, history):
        t0 = time.perf_counter()
        now_utc, now_bj = _now()
        messages = [{"role": "system", "content": SYSTEM.format(now_utc=now_utc, now_bj=now_bj,
                                                                mode_text=MODE_TEXT[self.s.mode])}]
        for h in history[-HISTORY_TURNS:]:
            if h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
                messages.append({"role": h["role"], "content": h["content"][:4000]})
        messages.append({"role": "user", "content": question})
        steps, tokens, answer = [], 0, None
        for step in range(1, MAX_STEPS + 1):
            resp = await self.llm.chat.completions.create(model=self.s.deepseek_model, messages=messages, tools=self.tools)
            tokens += resp.usage.total_tokens if resp.usage else 0
            msg = resp.choices[0].message
            messages.append(_assistant_message(msg))
            if not msg.tool_calls:
                answer = msg.content
                break
            for call in msg.tool_calls:
                ts = time.perf_counter()
                try:
                    args = json.loads(call.function.arguments or "{}")
                    result = await self.mcp.call_tool(call.function.name, args)
                    text, err = _result_text(result), result.is_error
                except json.JSONDecodeError as e:
                    args, text, err = call.function.arguments, f"工具执行出错：参数不是合法 JSON：{e}", True
                except Exception as e:
                    args, text, err = call.function.arguments, f"工具执行出错：{type(e).__name__}: {e}", True
                steps.append({"step": step, "tool": call.function.name, "args": args, "error": bool(err),
                              "ms": round((time.perf_counter() - ts) * 1000)})
                messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
        if answer is None:
            answer = f"超过 {MAX_STEPS} 步仍没有得出结论，已停止。已调用的工具：" + "、".join(s["tool"] for s in steps)
        out = {"answer": answer, "steps": steps, "tokens": tokens, "seconds": round(time.perf_counter() - t0, 1),
               "model": self.s.deepseek_model, "mode": self.s.mode}
        _audit({"time_utc": now_utc, "mode": self.s.mode, "question": question[:500], "steps": steps,
                "tokens": tokens, "seconds": out["seconds"], "answer_chars": len(answer or "")})
        return out


def _audit(rec):
    C.LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(C.LOG_DIR / "audit.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


async def _main(settings, q):
    async with QuantAgent(settings) as agent:
        r = await agent.ask(q)
    for s in r["steps"]:
        print(f"[第 {s['step']} 步] {s['tool']}({json.dumps(s['args'], ensure_ascii=False)}) "
              f"{'失败' if s['error'] else 'OK'} {s['ms']}ms")
    print(f"\n{r['answer']}\n\n[{r['mode']} · {r['seconds']}s · {r['tokens']} token · {r['model']}]")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:]
    path = C.config_path(argv)
    rest, skip = [], False
    for i, a in enumerate(argv):                       # 去掉 --config <路径>
        if skip:
            skip = False
            continue
        if a == "--config":
            skip = True
            continue
        if not a.startswith("--config="):
            rest.append(a)
    try:
        st = C.load(path)
    except C.ConfigError as e:
        raise SystemExit(str(e))
    asyncio.run(_main(st, " ".join(rest) or "我的仓位是什么情况"))
