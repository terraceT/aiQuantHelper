"""由 start.bat 调用（用 .venv 里的 Python 运行，只用标准库）：

  1. 安装 / 更新依赖：requirements.txt 没变且依赖都能 import ⇒ 跳过；
     否则依次尝试 官方 PyPI → 阿里云镜像 → 清华镜像 → 中科大镜像，哪个通用哪个。
  2. 没有 config.ini ⇒ 从 config.example.ini 复制一份。

退出码：0 成功；1 依赖装不上。
"""
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "requirements.txt"
STAMP = ROOT / ".venv" / ".deps_stamp"
INDEXES = [
    ("官方 PyPI", "https://pypi.org/simple"),
    ("阿里云镜像", "https://mirrors.aliyun.com/pypi/simple/"),
    ("清华镜像", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("中科大镜像", "https://mirrors.ustc.edu.cn/pypi/simple"),
]
CHECK = "import mcp, openai, starlette, uvicorn, requests, socks"


def deps_ok():
    if not STAMP.exists() or STAMP.read_text(encoding="utf-8").strip() != hashlib.sha256(REQ.read_bytes()).hexdigest():
        return False
    return subprocess.run([sys.executable, "-c", CHECK], capture_output=True).returncode == 0


def install():
    for name, url in INDEXES:
        print(f"正在从 {name} 安装依赖（首次约 1~3 分钟，请耐心等待）…", flush=True)
        host = url.split("/")[2]
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                            "--timeout", "30", "--retries", "2", "-q", "-i", url, "--trusted-host", host,
                            "-r", str(REQ)])
        if r.returncode == 0 and subprocess.run([sys.executable, "-c", CHECK], capture_output=True).returncode == 0:
            STAMP.write_text(hashlib.sha256(REQ.read_bytes()).hexdigest(), encoding="utf-8")
            print(f"依赖安装完成（来源：{name}）。", flush=True)
            return True
        print(f"从 {name} 安装失败，换下一个源重试…\n", flush=True)
    return False


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if deps_ok():
        print("依赖已就绪。", flush=True)
    elif not install():
        print("\n所有安装源都失败了。请检查网络（能否打开 https://pypi.org 或 https://mirrors.aliyun.com），\n"
              "如果你平时上网要开代理，请先开好代理再运行 start.bat。", flush=True)
        return 1
    cfg = ROOT / "config.ini"
    if not cfg.exists():
        shutil.copyfile(ROOT / "config.example.ini", cfg)
        print(f"已生成配置文件：{cfg}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
