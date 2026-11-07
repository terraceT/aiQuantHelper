from urllib.parse import parse_qs, urlparse

import pytest
import requests

from app.binance import BinanceClient, BinanceError


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


def test_signature_matches_binance_doc_example(monkeypatch):
    """币安文档里的 HMAC 示例：同一 secret + 查询串必须得到文档给出的签名。"""
    secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
    c = BinanceClient("dummy-key", secret)   # 签名只用 secret；下面的 secret 是币安官方文档公开的示例值
    seen = {}

    def fake_get(url, timeout):
        seen["url"] = url
        return FakeResp(200, {})
    monkeypatch.setattr(c.s, "get", fake_get)
    monkeypatch.setattr("app.binance.time.time", lambda: 1499827319.559)
    c._get("https://x", "/p", {"symbol": "LTCBTC", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC",
                               "quantity": 1, "price": 0.1}, signed=True)
    q = parse_qs(urlparse(seen["url"]).query)
    assert q["timestamp"] == ["1499827319559"]
    # 我们固定加 recvWindow=10000，所以与文档（5000）不同；单独用文档原串验算法
    import hashlib, hmac
    doc = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
    assert hmac.new(secret.encode(), doc.encode(), hashlib.sha256).hexdigest() == \
        "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"
    body = urlparse(seen["url"]).query.rsplit("&signature=", 1)
    assert q["signature"][0] == hmac.new(secret.encode(), body[0].encode(), hashlib.sha256).hexdigest()


def test_error_code_gets_chinese_hint(monkeypatch):
    c = BinanceClient("k", "s", retries=0)
    monkeypatch.setattr(c.s, "get", lambda url, timeout: FakeResp(401, {"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."}))
    with pytest.raises(BinanceError) as e:
        c.futures_account()
    assert e.value.code == -2015 and "白名单" in str(e.value)


def test_network_error_retries_then_explains_proxy(monkeypatch):
    c = BinanceClient("k", "s", retries=2)
    calls = []

    def boom(url, timeout):
        calls.append(url)
        raise requests.ConnectionError("x")
    monkeypatch.setattr(c.s, "get", boom)
    monkeypatch.setattr("app.binance.time.sleep", lambda s: None)
    with pytest.raises(BinanceError, match="proxy"):
        c.futures_account()
    assert len(calls) == 3


def test_proxy_only_from_config():
    c = BinanceClient("k", "s", proxy="http://127.0.0.1:7890")
    assert c.s.trust_env is False and c.s.proxies["https"] == "http://127.0.0.1:7890"
