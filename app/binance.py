"""币安 REST 只读客户端（HMAC-SHA256 签名的普通 API Key）。

⛔ 只有 GET：本模块没有、也不允许加入任何下单 / 撤单 / 划转 / 提现函数（tests/test_readonly.py 钉住）。
"""
import hashlib
import hmac
import time
from urllib.parse import urlencode

import requests

FAPI = "https://fapi.binance.com"      # U 本位合约
SAPI = "https://api.binance.com"       # 现货 / 账户

# 常见错误码 → 给用户看的中文解释（AI 也会看到）
HINTS = {
    -1021: "本机时间与币安服务器相差太大：请在 Windows「设置 → 时间和语言」里点「立即同步」。",
    -1022: "签名不对：api_secret 填错了（注意不要多复制空格或换行）。",
    -2008: "API Key 无效：api_key 填错了或已被删除。",
    -2014: "API Key 格式不对：api_key 填错了。",
    -2015: "API Key 无效、IP 不在白名单、或这个 Key 没有开通对应权限（合约要勾选「允许合约」）。",
    -1003: "请求太频繁被限流，稍等一分钟再试。",
}


class BinanceError(Exception):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status, self.code = status, code


class BinanceClient:
    def __init__(self, api_key, api_secret, proxy="", timeout=15, retries=2):
        self.key, self.secret = api_key, api_secret
        self.timeout, self.retries = timeout, retries
        self.s = requests.Session()
        self.s.trust_env = False                       # 只认 config.ini 里的 proxy，不受系统环境变量影响
        if proxy:
            self.s.proxies = {"http": proxy, "https": proxy}
        self.s.headers["X-MBX-APIKEY"] = api_key
        self._offset_ms = 0                            # 本机时间与币安服务器的差，签名时补上

    # ------------------------------------------------------------------ 底层
    def _get(self, base, path, params=None, signed=False):
        params = dict(params or {})
        last = None
        for attempt in range(self.retries + 1):
            q = dict(params)
            if signed:
                q["timestamp"] = int(time.time() * 1000) + self._offset_ms
                q["recvWindow"] = 10000
                qs = urlencode(q)
                qs += "&signature=" + hmac.new(self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
            else:
                qs = urlencode(q)
            url = f"{base}{path}" + (f"?{qs}" if qs else "")
            try:
                r = self.s.get(url, timeout=self.timeout)
            except requests.RequestException as e:     # 网络错误：重试
                last = BinanceError(f"连不上币安（{type(e).__name__}）。如果你所在的网络访问不了币安，"
                                    f"请在 config.ini 的 [binance] proxy 填代理地址。")
                time.sleep(1.0 * (attempt + 1))
                continue
            if r.status_code == 200:
                return r.json()
            try:
                body = r.json()
            except ValueError:
                body = {}
            code, msg = body.get("code"), (body.get("msg") or r.text[:200]).rstrip(". ")
            if code == -1021 and signed and attempt < self.retries:   # 时间偏差：对一次时再试
                self.sync_time()
                continue
            if r.status_code in (418, 429):
                raise BinanceError(f"币安限流（HTTP {r.status_code}）：{msg}。{HINTS[-1003]}", r.status_code, code)
            if r.status_code == 451:
                raise BinanceError("币安拒绝了你所在地区的访问（HTTP 451）：请在 config.ini 配置代理。", 451, code)
            if r.status_code >= 500:
                last = BinanceError(f"币安服务器错误 HTTP {r.status_code}：{msg}", r.status_code, code)
                time.sleep(1.0 * (attempt + 1))
                continue
            hint = HINTS.get(code, "")
            raise BinanceError(f"币安返回错误 HTTP {r.status_code} code={code}：{msg}" + (f"。{hint}" if hint else ""),
                               r.status_code, code)
        raise last

    def sync_time(self):
        """返回 (本机与服务器的时差 ms, 往返耗时 ms)，并记住时差用于签名。"""
        t0 = time.time()
        server = self._get(FAPI, "/fapi/v1/time")["serverTime"]
        t1 = time.time()
        local_mid = (t0 + t1) / 2 * 1000
        self._offset_ms = int(server - local_mid)
        return self._offset_ms, round((t1 - t0) * 1000)

    # ------------------------------------------------------------------ 只读接口（原样返回币安 JSON）
    def futures_account(self):
        return self._get(FAPI, "/fapi/v3/account", signed=True)

    def futures_position_risk(self):
        return self._get(FAPI, "/fapi/v2/positionRisk", signed=True)

    def futures_open_orders(self):
        return self._get(FAPI, "/fapi/v1/openOrders", signed=True)

    def futures_open_algo_orders(self):
        """条件单（止损/止盈等）：币安新版把它们放在 Algo 订单接口，普通 openOrders 里查不到。"""
        return self._get(FAPI, "/fapi/v1/openAlgoOrders", signed=True)

    def futures_income(self, start_ms, limit=1000):
        return self._get(FAPI, "/fapi/v1/income", {"startTime": start_ms, "limit": limit}, signed=True)

    def spot_account(self):
        return self._get(SAPI, "/api/v3/account", {"omitZeroBalances": "true"}, signed=True)

    def api_restrictions(self):
        return self._get(SAPI, "/sapi/v1/account/apiRestrictions", signed=True)

    def premium_index(self, symbol):
        return self._get(FAPI, "/fapi/v1/premiumIndex", {"symbol": symbol})
