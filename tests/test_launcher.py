"""启动链路：每个模块都能编译、启动入口对配置错误给出正确退出码、start.bat 是纯 ASCII + CRLF。"""
import os
import py_compile
import subprocess
import sys

import pytest

from app.config import ROOT
from conftest import write_cfg

PY_FILES = sorted(p for d in ("app", "tools", "tests") for p in (ROOT / d).rglob("*.py"))


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_python_file_compiles(path, tmp_path):
    py_compile.compile(str(path), cfile=str(tmp_path / "x.pyc"), doraise=True)


def _run_main(cfg, *extra):
    env = {**os.environ, "AQH_NO_NOTEPAD": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-m", "app.main", "--config", str(cfg), *extra], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_main_refuses_without_deepseek_key(tmp_path):
    r = _run_main(write_cfg(tmp_path / "c.ini", ds_key=""))
    assert r.returncode == 2 and "platform.deepseek.com" in r.stdout


def test_main_refuses_half_binance_key(tmp_path):
    r = _run_main(write_cfg(tmp_path / "c.ini", bn_key="k" * 10))
    assert r.returncode == 2 and "api_secret" in r.stdout


def test_main_missing_config_file(tmp_path):
    r = _run_main(tmp_path / "none.ini")
    assert r.returncode == 2 and "config.example.ini" in r.stdout


def test_start_bat_is_ascii_crlf():
    b = (ROOT / "start.bat").read_bytes()
    b.decode("ascii")                       # cmd.exe 会把 UTF-8 批处理里的中文行切坏 ⇒ 必须纯 ASCII
    assert b"\r\n" in b and b.count(b"\n") == b.count(b"\r\n")


def test_setup_ps1_has_bom_and_crlf():
    b = (ROOT / "tools" / "setup.ps1").read_bytes()
    assert b.startswith(b"\xef\xbb\xbf")    # 没有 BOM 时 PowerShell 5.1 会按 ANSI 读，中文乱码、甚至语法错
    assert b.count(b"\n") == b.count(b"\r\n")
