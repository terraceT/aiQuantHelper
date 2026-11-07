"""aiQuantHelper 的 MCP Server（stdio）：把账户数据和运维手册暴露成只读工具。

由 agent.py 作为子进程拉起（通过环境变量 AQH_CONFIG 拿到配置文件路径）。
也可以接进任何支持 MCP 的客户端：  .venv\\Scripts\\python -m app.mcp_server
⛔ 全部只读：没有下单、撤单、改杠杆、划转、提现的工具。
"""
import sys
from typing import Annotated

from pydantic import Field

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import config as C
from .datasource import DataSource, _err
from .runbook import Runbook

RO = ToolAnnotations(read_only_hint=True)
RO_NET = ToolAnnotations(read_only_hint=True, open_world_hint=True)

mcp = MCPServer("aiquanthelper", instructions="币安 U 本位合约账户（或演示数据）的只读运维工具。")
runbook = Runbook()
_ds = None


def ds():
    global _ds
    if _ds is None:
        _ds = DataSource(C.load())
    return _ds


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:                 # 把原因交给模型，而不是一句 Error executing tool
        raise ToolError(_err(e)) from e


@mcp.tool(annotations=RO_NET)
def get_account() -> dict:
    """合约账户：数据模式（LIVE 实盘 / MOCK 演示）、权益、钱包余额、未实现盈亏、可用余额、保证金、保证金率、
    多空敞口、实际杠杆（总敞口/权益），以及逐个持仓（方向/数量/开仓价/标记价/名义/未实现盈亏/杠杆/全仓逐仓/强平价/强平距离）。
    回答「我的仓位」「账户情况」用它。"""
    return _safe(lambda: ds().account())


@mcp.tool(annotations=RO_NET)
def get_open_orders() -> list[dict]:
    """当前挂单：普通挂单 + 条件单（止损/止盈，币安 Algo 订单）。kind 字段区分两类。"""
    return _safe(lambda: ds().open_orders())


@mcp.tool(annotations=RO_NET)
def get_income(days: Annotated[int, Field(ge=1, le=30, description="统计最近几天")] = 7) -> dict:
    """合约资金流水：已实现盈亏、手续费、资金费等，按类型和按币种汇总，以及最近 20 条明细（北京时间）。"""
    return _safe(lambda: ds().income(days))


@mcp.tool(annotations=RO_NET)
def get_spot_balances() -> list[dict]:
    """现货账户里余额不为 0 的币（可用 / 冻结 / 合计数量）。"""
    return _safe(lambda: ds().spot_balances())


@mcp.tool(annotations=RO_NET)
def get_risk_report() -> dict:
    """风险体检：实际杠杆、保证金率、各持仓强平距离、集中度、可用余额、持仓是否挂了止损条件单。
    overall = 正常 / 需要关注 / 需要立即处理，checks 列出每一项（level: ok / warn / danger）。"""
    return _safe(lambda: ds().risk_report())


@mcp.tool(annotations=RO_NET)
def get_market_price(symbol: Annotated[str, Field(description="交易对，如 BTCUSDT；只写 BTC 也可以")]) -> dict:
    """某个 U 本位永续合约的标记价、指数价、当前资金费率（%）、下次资金费时间。"""
    return _safe(lambda: ds().mark_price(symbol))


@mcp.tool(annotations=RO_NET)
def check_connectivity() -> dict:
    """检查与币安的连通性：能否访问、往返耗时、本机时钟偏差、是否配置了代理，以及这个 API Key 开了哪些权限
    （读取/合约/交易/提现/IP 白名单）。用户问「连接正常吗」「API Key 安全吗」或出现网络/签名类报错时用它。"""
    return _safe(lambda: ds().connectivity())


@mcp.tool(annotations=RO_NET)
def get_daily_report() -> dict:
    """运维日报（现查，一次拿全）：账户与持仓、挂单与条件单、近 7 天资金流水、现货余额、风险体检、连通性与 API 权限。
    回答「今天情况怎么样」「日报」「整体看一下」时优先用它，一次就够。各块独立：某块出错时 ok=false 并给出 error。"""
    return _safe(lambda: ds().daily_report())


@mcp.tool(annotations=RO)
def search_runbook(
    query: Annotated[str, Field(description="要查的问题或关键词，如「-2015」「强平价是什么」「怎么配置代理」")],
    k: Annotated[int, Field(ge=1, le=6, description="返回几段")] = 3,
) -> list[dict]:
    """检索运维手册（错误码处理、API Key 配置与安全、代理、风险概念解释、本程序的使用说明）。
    遇到报错、异常、问「为什么 / 怎么办 / 是什么意思」时先查它，回答时引用条目 id（如 R5）。"""
    return runbook.search(query, k)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    mcp.run()
