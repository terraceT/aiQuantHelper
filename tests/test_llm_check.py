"""启动时的大模型检查：DeepSeek 严格，其他 OpenAI 兼容平台宽松（模型列表不一定完整）。"""
import pytest
import requests

from app import config as C
from app import main
from conftest import write_cfg


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._b = status, body or {}

    def json(self):
        return self._b


def settings(tmp_path, base_url, model="m1"):
    p = write_cfg(tmp_path / "c.ini")
    s = p.read_text(encoding="utf-8").replace("model = deepseek-flash",
                                              f"model = {model}\nbase_url = {base_url}")
    p.write_text(s, encoding="utf-8")
    return C.load(p)


def fake(monkeypatch, resp):
    def get(url, headers, timeout):
        if isinstance(resp, Exception):
            raise resp
        return resp
    monkeypatch.setattr(main.requests, "get", get)


MODELS = Resp(200, {"data": [{"id": "m1"}, {"id": "m2"}]})


@pytest.mark.parametrize("base", ["https://api.deepseek.com", "https://dashscope.aliyuncs.com/compatible-mode/v1"])
def test_rejected_key_refuses_on_any_platform(tmp_path, monkeypatch, base):
    fake(monkeypatch, Resp(401))
    ok, msg = main.check_deepseek(settings(tmp_path, base))
    assert ok is False and "401" in msg


def test_unknown_model_on_deepseek_refuses(tmp_path, monkeypatch):
    fake(monkeypatch, MODELS)
    ok, msg = main.check_deepseek(settings(tmp_path, "https://api.deepseek.com", model="nope"))
    assert ok is False and "m1" in msg


def test_unknown_model_on_other_platform_only_warns(tmp_path, monkeypatch):
    fake(monkeypatch, MODELS)
    ok, msg = main.check_deepseek(settings(tmp_path, "https://api.moonshot.cn/v1", model="nope"))
    assert ok is True and msg.startswith("⚠") and "api.moonshot.cn" in msg and "DeepSeek" not in msg


def test_known_model_on_other_platform_ok(tmp_path, monkeypatch):
    fake(monkeypatch, MODELS)
    ok, msg = main.check_deepseek(settings(tmp_path, "http://127.0.0.1:11434/v1", model="m2"))
    assert ok is True and msg.startswith("✓") and "127.0.0.1:11434" in msg


@pytest.mark.parametrize("resp", [Resp(404), Resp(200, {"unexpected": 1}), requests.ConnectionError("x")])
def test_no_model_list_or_network_error_does_not_block(tmp_path, monkeypatch, resp):
    fake(monkeypatch, resp)
    ok, _ = main.check_deepseek(settings(tmp_path, "https://open.bigmodel.cn/api/paas/v4"))
    assert ok is True
