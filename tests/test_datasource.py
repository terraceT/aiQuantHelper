import json

from app import config as C
from app.binance import BinanceError
from app.datasource import DataSource, MockRaw


def mock_ds(mock_cfg):
    return DataSource(C.load(mock_cfg))


def test_mock_account_is_consistent(mock_cfg):
    a = mock_ds(mock_cfg).account()
    raw = json.loads((C.MOCK_DIR / "futures_account.json").read_text(encoding="utf-8"))
    assert a["mode"] == "MOCK"
    assert a["equity_usdt"] == round(float(raw["totalMarginBalance"]), 2)
    assert abs(a["wallet_balance_usdt"] + a["unrealized_pnl_usdt"] - a["equity_usdt"]) < 0.01
    # 零仓位（DOGEUSDT）被过滤；按名义价值从大到小
    syms = [p["symbol"] for p in a["positions"]]
    assert "DOGEUSDT" not in syms and len(syms) == 3
    assert [p["notional_usdt"] for p in a["positions"]] == sorted([p["notional_usdt"] for p in a["positions"]], reverse=True)
    assert abs(sum(p["unrealized_pnl"] for p in a["positions"]) - a["unrealized_pnl_usdt"]) < 0.01
    eth = next(p for p in a["positions"] if p["symbol"] == "ETHUSDT")
    assert eth["side"] == "空" and eth["qty"] == 1.2 and eth["liq_distance_pct"] > 0


def test_mock_orders_include_algo(mock_cfg):
    orders = mock_ds(mock_cfg).open_orders()
    kinds = {o["kind"] for o in orders}
    assert kinds == {"普通", "条件单"}
    stop = next(o for o in orders if o["kind"] == "条件单")
    assert stop["type"] == "STOP_MARKET" and stop["stop_price"] == 68000 and stop["close_position"]


def test_mock_income_recent_and_sums(mock_cfg):
    inc = mock_ds(mock_cfg).income(7)
    assert inc["count"] > 0                                   # 时间被平移到最近：任何一天打开都有数据
    assert abs(sum(inc["by_type"].values()) - inc["total_usdt"]) < 0.001
    assert abs(sum(inc["by_symbol"].values()) - inc["total_usdt"]) < 0.001
    assert set(inc["by_type"]) <= {"已实现盈亏", "手续费", "资金费"}


def test_risk_report_flags_unprotected_positions(mock_cfg):
    r = mock_ds(mock_cfg).risk_report()
    stop = next(c for c in r["checks"] if c["item"] == "止损单")
    assert stop["level"] == "warn" and "ETHUSDT" in stop["detail"] and "BTCUSDT" not in stop["detail"]
    assert r["overall"] == "需要关注"


def test_spot_has_no_identity_fields(mock_cfg):
    rows = mock_ds(mock_cfg).spot_balances()
    assert rows and all(set(r) == {"asset", "free", "locked", "total"} for r in rows)


class _BrokenFutures(MockRaw):
    def futures_account(self):
        raise BinanceError("币安返回错误 HTTP 401 code=-2015：Invalid API-key", 401, -2015)


def test_overview_sections_fail_independently(mock_cfg):
    ov = DataSource(C.load(mock_cfg), raw=_BrokenFutures()).overview()
    assert ov["account"]["ok"] is False and "-2015" in ov["account"]["error"]
    assert ov["risk"]["ok"] is False
    assert ov["spot"]["ok"] and ov["open_orders"]["ok"] and ov["income"]["ok"]


class _AlgoFails(MockRaw):
    def open_algo_orders(self):
        raise BinanceError("boom")


def test_algo_failure_is_visible_not_silent(mock_cfg):
    ds = DataSource(C.load(mock_cfg), raw=_AlgoFails())
    orders = ds.open_orders()
    assert any("algo_error" in o for o in orders)
    stop = next(c for c in ds.risk_report()["checks"] if c["item"] == "止损单")
    assert "无法判断" in stop["detail"]          # ⛔ 不能因为查不到条件单就报「没挂止损」


def test_mark_price_accepts_short_symbol(mock_cfg):
    p = mock_ds(mock_cfg).mark_price("btc")
    assert p["symbol"] == "BTCUSDT" and p["mark_price"] == 76000 and p["next_funding_time"]
