import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def write_cfg(path, ds_key="sk-test-not-a-real-key", bn_key="", bn_secret="", proxy="", port=8866, extra=""):
    path.write_text(f"[deepseek]\napi_key = {ds_key}\nmodel = deepseek-flash\n"
                    f"[binance]\napi_key = {bn_key}\napi_secret = {bn_secret}\nproxy = {proxy}\n"
                    f"[web]\nport = {port}\nopen_browser = no\n{extra}", encoding="utf-8")
    return path


@pytest.fixture
def mock_cfg(tmp_path):
    return write_cfg(tmp_path / "config.ini")

