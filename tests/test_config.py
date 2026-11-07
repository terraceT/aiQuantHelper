import pytest

from app import config as C
from conftest import write_cfg


def test_example_config_has_no_keys():
    s = C.EXAMPLE_CONFIG.read_text(encoding="utf-8")
    import configparser
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_string(s)
    assert cp.get("deepseek", "api_key") == ""
    assert cp.get("binance", "api_key") == "" and cp.get("binance", "api_secret") == ""
    assert cp.get("binance", "proxy") == ""


def test_missing_deepseek_key_refuses_with_guide(tmp_path):
    p = write_cfg(tmp_path / "c.ini", ds_key="")
    with pytest.raises(C.ConfigError) as e:
        C.load(p)
    msg = str(e.value)
    assert "platform.deepseek.com" in msg and "API keys" in msg and str(p) in msg


def test_no_binance_key_is_mock(tmp_path):
    s = C.load(write_cfg(tmp_path / "c.ini"))
    assert s.mode == "MOCK"


def test_full_binance_key_is_live(tmp_path):
    s = C.load(write_cfg(tmp_path / "c.ini", bn_key="k" * 64, bn_secret="s" * 64, proxy="http://127.0.0.1:7890"))
    assert s.mode == "LIVE" and s.binance_proxy == "http://127.0.0.1:7890"


@pytest.mark.parametrize("k,sec,missing", [("k" * 64, "", "api_secret"), ("", "s" * 64, "api_key")])
def test_half_binance_key_refuses(tmp_path, k, sec, missing):
    with pytest.raises(C.ConfigError, match=missing):
        C.load(write_cfg(tmp_path / "c.ini", bn_key=k, bn_secret=sec))


def test_quotes_and_bom_tolerated(tmp_path):
    p = tmp_path / "c.ini"
    p.write_bytes("﻿[deepseek]\napi_key = \"sk-abc\"\n[binance]\napi_key =\napi_secret =\n".encode("utf-8"))
    s = C.load(p)
    assert s.deepseek_key == "sk-abc" and s.port == 8866 and s.mode == "MOCK"


@pytest.mark.parametrize("extra_key,val,match", [("port", "80", "port"), ("port", "abc", "port")])
def test_bad_port(tmp_path, extra_key, val, match):
    p = tmp_path / "c.ini"
    write_cfg(p)
    p.write_text(p.read_text(encoding="utf-8").replace("port = 8866", f"{extra_key} = {val}"), encoding="utf-8")
    with pytest.raises(C.ConfigError, match=match):
        C.load(p)


def test_proxy_needs_scheme(tmp_path):
    with pytest.raises(C.ConfigError, match="proxy"):
        C.load(write_cfg(tmp_path / "c.ini", bn_key="k", bn_secret="s", proxy="127.0.0.1:7890"))


def test_missing_file(tmp_path):
    with pytest.raises(C.ConfigError, match="config.example.ini"):
        C.load(tmp_path / "nope.ini")


def test_config_path_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("AQH_CONFIG", str(tmp_path / "env.ini"))
    assert C.config_path(["--config", str(tmp_path / "arg.ini")]).name == "arg.ini"
    assert C.config_path([]).name == "env.ini"
    monkeypatch.delenv("AQH_CONFIG")
    assert C.config_path([]) == C.DEFAULT_CONFIG
