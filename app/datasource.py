"""数据源：LIVE（币安真实账户，只读）或 MOCK（mock/ 目录里的模拟数据）。

两种模式走**同一套**规整代码：mock/*.json 就是币安接口的原始响应格式，
所以演示模式下页面和 AI 看到的字段，和接上真实账户后完全一样。
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import config as C
from .binance import BinanceClient, BinanceError

BJ = timezone(timedelta(hours=8))


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _r(x, n=2):
    return round(_f(x), n)


def _ts(ms):
    """毫秒时间戳 → 北京时间字符串。"""
    if not ms:
        return ""
    return datetime.fromtimestamp(int(ms) / 1000, BJ).strftime("%Y-%m-%d %H:%M:%S")


def now_bj():
    return datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------- 原始数据来源
class LiveRaw:
    def __init__(self, s):
        self.c = BinanceClient(s.binance_key, s.binance_secret, proxy=s.binance_proxy)
        self.proxy = bool(s.binance_proxy)

    def futures_account(self):
        return self.c.futures_account()

    def position_risk(self):
        return self.c.futures_position_risk()

    def open_orders(self):
        return self.c.futures_open_orders()

    def open_algo_orders(self):
        return self.c.futures_open_algo_orders()

    def income(self, start_ms):
        return self.c.futures_income(start_ms)

    def spot_account(self):
        return self.c.spot_account()

    def api_restrictions(self):
        return self.c.api_restrictions()

    def premium_index(self, symbol):
        return self.c.premium_index(symbol)

    def ping(self):
        offset, rtt = self.c.sync_time()
        return {"reachable": True, "latency_ms": rtt, "time_offset_ms": offset}


class MockRaw:
    """读 mock/*.json。时间字段整体平移到「最近」，这样「近 7 天」之类的统计在任何一天打开都有数据。"""

    def __init__(self, d=C.MOCK_DIR):
        self.d = d

    def _load(self, name):
        p = self.d / name
        if not p.exists():
            raise FileNotFoundError(f"找不到模拟数据文件 {p}（演示模式从 mock/ 目录读数据）")
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError as e:
            raise ValueError(f"模拟数据文件 {p.name} 不是合法 JSON：{e}") from e

    @staticmethod
    def _shift(rows, key="time"):
        ts = [r[key] for r in rows if r.get(key)]
        if not ts:
            return rows
        delta = int(time.time() * 1000) - 30 * 60 * 1000 - max(ts)   # 最新一条 = 30 分钟前
        return [{**r, key: r[key] + delta} if r.get(key) else r for r in rows]

    def futures_account(self):
        return self._load("futures_account.json")

    def position_risk(self):
        return self._shift(self._load("position_risk.json"), "updateTime")

    def open_orders(self):
        return self._shift(self._load("open_orders.json"))

    def open_algo_orders(self):
        return self._shift(self._load("open_algo_orders.json"), "createTime")

    def income(self, start_ms):
        return [r for r in self._shift(self._load("income.json")) if r["time"] >= start_ms]

    def spot_account(self):
        return self._load("spot_account.json")

    def api_restrictions(self):
        return self._load("api_restrictions.json")

    def premium_index(self, symbol):
        table = self._load("premium_index.json")
        if symbol not in table:
            raise ValueError(f"模拟数据里没有 {symbol}（mock/premium_index.json 里有：{', '.join(table)}）")
        period = 8 * 3600 * 1000                                  # 下次资金费时间：按 8 小时周期现算
        return {**table[symbol], "nextFundingTime": (int(time.time() * 1000) // period + 1) * period}

    def ping(self):
        return {"reachable": True, "latency_ms": 0, "time_offset_ms": 0}


# ---------------------------------------------------------------------- 规整后的数据
class DataSource:
    def __init__(self, settings, raw=None):
        self.s = settings
        self.mode = settings.mode
        self.raw = raw or (LiveRaw(settings) if self.mode == "LIVE" else MockRaw())

    # ---- 合约持仓
    @staticmethod
    def _position(p):
        amt = _f(p.get("positionAmt"))
        entry, mark, liq = _f(p.get("entryPrice")), _f(p.get("markPrice")), _f(p.get("liquidationPrice"))
        notional = _f(p.get("notional")) or amt * mark
        lev = _f(p.get("leverage"), 1) or 1
        upnl = _f(p.get("unRealizedProfit"))
        margin = abs(notional) / lev if lev else 0
        liq_dist = (abs(mark - liq) / mark * 100) if (mark and liq) else None
        return {
            "symbol": p.get("symbol"),
            "side": "多" if amt > 0 else "空",
            "position_side": p.get("positionSide", "BOTH"),
            "qty": abs(amt),
            "entry_price": entry,
            "mark_price": mark,
            "notional_usdt": _r(abs(notional)),
            "unrealized_pnl": _r(upnl),
            "pnl_pct_of_margin": _r(upnl / margin * 100) if margin else None,
            "price_change_pct": _r((mark - entry) / entry * 100 * (1 if amt > 0 else -1)) if entry else None,
            "leverage": int(lev),
            "margin_type": "逐仓" if str(p.get("marginType", "")).lower() == "isolated" else "全仓",
            "liquidation_price": float(f"{liq:.6g}") if liq else None,
            "liq_distance_pct": _r(liq_dist) if liq_dist is not None else None,
            "updated": _ts(p.get("updateTime")),
        }

    def positions(self):
        rows = [self._position(p) for p in self.raw.position_risk() if _f(p.get("positionAmt")) != 0]
        return sorted(rows, key=lambda r: -r["notional_usdt"])

    def account(self):
        """合约账户汇总 + 持仓 + 敞口。"""
        with ThreadPoolExecutor(2) as ex:
            fa, fp = ex.submit(self.raw.futures_account), ex.submit(self.positions)
            a, pos = fa.result(), fp.result()
        equity = _f(a.get("totalMarginBalance"))
        long_n = sum(p["notional_usdt"] for p in pos if p["side"] == "多")
        short_n = sum(p["notional_usdt"] for p in pos if p["side"] == "空")
        maint = _f(a.get("totalMaintMargin"))
        return {
            "mode": self.mode,
            "equity_usdt": _r(equity),
            "wallet_balance_usdt": _r(a.get("totalWalletBalance")),
            "unrealized_pnl_usdt": _r(a.get("totalUnrealizedProfit")),
            "available_balance_usdt": _r(a.get("availableBalance")),
            "initial_margin_usdt": _r(a.get("totalInitialMargin")),
            "maint_margin_usdt": _r(maint),
            "margin_ratio_pct": _r(maint / equity * 100) if equity else None,
            "long_notional_usdt": _r(long_n),
            "short_notional_usdt": _r(short_n),
            "net_notional_usdt": _r(long_n - short_n),
            "gross_leverage": _r((long_n + short_n) / equity) if equity else None,
            "position_count": len(pos),
            "positions": pos,
            "queried_at": now_bj() + "（北京时间）",
        }

    # ---- 挂单
    @staticmethod
    def _order(o, kind):
        return {
            "kind": kind,
            "symbol": o.get("symbol"),
            "side": {"BUY": "买", "SELL": "卖"}.get(o.get("side"), o.get("side")),
            "type": o.get("orderType") or o.get("type"),
            "price": _f(o.get("price")) or None,
            "stop_price": _f(o.get("triggerPrice") or o.get("stopPrice")) or None,
            "qty": _f(o.get("quantity") or o.get("origQty")),
            "filled_qty": _f(o.get("executedQty")),
            "reduce_only": bool(o.get("reduceOnly")),
            "close_position": bool(o.get("closePosition")),
            "time": _ts(o.get("createTime") or o.get("time")),
            "order_id": o.get("algoId") or o.get("orderId"),
        }

    def open_orders(self):
        """普通挂单 + 条件单（Algo）。条件单接口失败时照样返回普通挂单，并在 algo_error 说明。"""
        out = [self._order(o, "普通") for o in self.raw.open_orders()]
        try:
            out += [self._order(o, "条件单") for o in self.raw.open_algo_orders()]
        except Exception as e:
            out.append({"kind": "条件单", "algo_error": f"条件单查询失败：{_err(e)}"})
        return sorted(out, key=lambda r: r.get("time", ""), reverse=True)

    # ---- 资金流水
    def income(self, days=7):
        days = max(1, min(int(days), 30))
        start = int(time.time() * 1000) - days * 86400 * 1000
        rows = self.raw.income(start)
        by_type, by_symbol = {}, {}
        for r in rows:
            v = _f(r.get("income"))
            by_type[r.get("incomeType")] = by_type.get(r.get("incomeType"), 0) + v
            sym = r.get("symbol") or "（无币种）"
            by_symbol[sym] = by_symbol.get(sym, 0) + v
        names = {"REALIZED_PNL": "已实现盈亏", "COMMISSION": "手续费", "FUNDING_FEE": "资金费",
                 "TRANSFER": "划转", "INSURANCE_CLEAR": "强平清算", "WELCOME_BONUS": "体验金"}
        recent = sorted(rows, key=lambda r: r.get("time", 0), reverse=True)[:20]
        return {
            "days": days,
            "count": len(rows),
            "total_usdt": _r(sum(_f(r.get("income")) for r in rows), 4),
            "by_type": {names.get(k, k): _r(v, 4) for k, v in by_type.items()},
            "by_symbol": {k: _r(v, 4) for k, v in sorted(by_symbol.items(), key=lambda kv: kv[1])},
            "recent": [{"time": _ts(r.get("time")), "symbol": r.get("symbol"),
                        "type": names.get(r.get("incomeType"), r.get("incomeType")),
                        "amount": _r(r.get("income"), 4), "asset": r.get("asset")} for r in recent],
        }

    # ---- 现货
    def spot_balances(self):
        a = self.raw.spot_account()
        out = [{"asset": b["asset"], "free": _f(b.get("free")), "locked": _f(b.get("locked")),
                "total": _f(b.get("free")) + _f(b.get("locked"))} for b in a.get("balances", [])]
        return [b for b in out if b["total"] > 0]   # ⛔ 不返回 uid 等账户身份信息

    # ---- 行情
    def mark_price(self, symbol):
        symbol = (symbol or "").upper().strip()
        if not symbol.endswith("USDT"):
            symbol += "USDT"
        p = self.raw.premium_index(symbol)
        return {"symbol": symbol, "mark_price": _f(p.get("markPrice")), "index_price": _f(p.get("indexPrice")),
                "funding_rate_pct": _r(_f(p.get("lastFundingRate")) * 100, 4),
                "next_funding_time": _ts(p.get("nextFundingTime"))}

    # ---- 连通性与 API 权限
    def connectivity(self):
        out = {"mode": self.mode, "proxy_configured": bool(self.s.binance_proxy)}
        try:
            out.update(self.raw.ping())
            if abs(out["time_offset_ms"]) > 5000:
                out["time_warning"] = "本机时间与币安相差超过 5 秒，签名请求可能被拒（-1021）：请同步 Windows 时间。"
        except Exception as e:
            out.update({"reachable": False, "error": str(e)})
            return out
        try:
            r = self.raw.api_restrictions()
            out["api_permissions"] = {
                "读取": r.get("enableReading"), "合约": r.get("enableFutures"),
                "现货与杠杆交易": r.get("enableSpotAndMarginTrading"), "提现": r.get("enableWithdrawals"),
                "IP 白名单": r.get("ipRestrict")}
            if r.get("enableWithdrawals"):
                out["security_warning"] = "这个 API Key 开启了提现权限：强烈建议到币安关闭（本程序只需要读取权限）。"
            elif r.get("enableSpotAndMarginTrading") or r.get("enableFutures"):
                out["security_note"] = "这个 API Key 带交易权限；本程序只需要「读取」，单独建一个只读 Key 更安全。"
        except Exception as e:
            out["api_permissions_error"] = str(e)
        return out

    # ---- 风险体检
    def risk_report(self, account=None, orders=None):
        a = account or self.account()
        if orders is None:
            try:
                orders = self.open_orders()
            except Exception:
                orders = None
        checks = []

        def add(level, item, detail):
            checks.append({"level": level, "item": item, "detail": detail})

        lev = a.get("gross_leverage")
        if lev is None:
            add("warn", "杠杆", "权益为 0，无法计算杠杆")
        elif lev > 5:
            add("danger", "杠杆", f"总敞口 / 权益 = {lev} 倍，高于 5 倍")
        elif lev > 3:
            add("warn", "杠杆", f"总敞口 / 权益 = {lev} 倍，高于 3 倍")
        else:
            add("ok", "杠杆", f"总敞口 / 权益 = {lev} 倍")
        mr = a.get("margin_ratio_pct")
        if mr is not None:
            add("danger" if mr > 50 else "warn" if mr > 20 else "ok", "保证金率",
                f"维持保证金 / 权益 = {mr}%（达到 100% 会被强平）")
        for p in a["positions"]:
            d = p["liq_distance_pct"]
            if d is not None and d < 10:
                add("danger" if d < 5 else "warn", f"{p['symbol']} 强平距离",
                    f"标记价 {p['mark_price']} 距强平价 {p['liquidation_price']} 只有 {d}%")
        gross = a["long_notional_usdt"] + a["short_notional_usdt"]
        for p in a["positions"]:
            if gross and p["notional_usdt"] / gross > 0.6 and len(a["positions"]) > 1:
                add("warn", f"{p['symbol']} 集中度", f"占总敞口 {round(p['notional_usdt'] / gross * 100, 1)}%")
        if a["equity_usdt"] and a["available_balance_usdt"] / a["equity_usdt"] < 0.05 and a["positions"]:
            add("warn", "可用余额", f"可用 {a['available_balance_usdt']} USDT，不足权益的 5%：已无余量开新仓或应对波动")
        algo_failed = orders is not None and any("algo_error" in o for o in orders)
        if orders is not None and a["positions"] and not algo_failed:
            protected = {o["symbol"] for o in orders if "STOP" in (o.get("type") or "")}
            naked = [p["symbol"] for p in a["positions"] if p["symbol"] not in protected]
            if naked:
                add("warn", "止损单", "这些持仓没有挂止损条件单：" + "、".join(naked))
            else:
                add("ok", "止损单", "每个持仓都挂了止损条件单")
        elif algo_failed:
            add("warn", "止损单", "条件单查询失败，无法判断持仓有没有止损")
        worst = "danger" if any(c["level"] == "danger" for c in checks) else \
                "warn" if any(c["level"] == "warn" for c in checks) else "ok"
        return {"mode": self.mode, "overall": {"ok": "正常", "warn": "需要关注", "danger": "需要立即处理"}[worst],
                "checks": checks}

    # ---- 页面用：一次拿全部，每块独立成败
    def overview(self, income_days=7):
        jobs = {"account": self.account, "open_orders": self.open_orders,
                "income": lambda: self.income(income_days), "spot": self.spot_balances}
        out = {"mode": self.mode, "queried_at": now_bj()}
        with ThreadPoolExecutor(len(jobs)) as ex:
            futs = {k: ex.submit(fn) for k, fn in jobs.items()}
            for k, f in futs.items():
                try:
                    out[k] = {"ok": True, "data": f.result()}
                except Exception as e:
                    out[k] = {"ok": False, "error": _err(e)}
        if out["account"]["ok"]:
            try:
                out["risk"] = {"ok": True, "data": self.risk_report(
                    out["account"]["data"], out["open_orders"]["data"] if out["open_orders"]["ok"] else None)}
            except Exception as e:
                out["risk"] = {"ok": False, "error": _err(e)}
        else:
            out["risk"] = {"ok": False, "error": "账户没查到，无法体检"}
        return out

    def daily_report(self):
        """AI 用：一次拿到账户、挂单、7 天流水、现货、风险体检、连通性。"""
        ov = self.overview(7)
        ov["connectivity"] = self.connectivity()
        return ov


def _err(e):
    return str(e) if isinstance(e, (BinanceError, ValueError, FileNotFoundError)) else f"{type(e).__name__}: {e}"
