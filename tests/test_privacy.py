"""发布前隐私检查：会被提交到 git 的文件里不能有密钥、个人路径。"""
import re
import subprocess

from app.config import ROOT

IGNORED_DIRS = {".venv", ".python", ".python.download", "__pycache__", "logs", ".pytest_cache", "work", ".git"}
IGNORED_FILES = {"config.ini", "PROGRESS.md"}
PATTERNS = {
    "DeepSeek/OpenAI 风格 key": re.compile(r"sk-[A-Za-z0-9]{24,}"),
    "64 位币安 key 形状": re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{64}(?![A-Za-z0-9])"),
    "Windows 用户目录": re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+", re.I),
    "飞书 webhook": re.compile(r"open\.(feishu|larksuite)\.com/open-apis/bot"),
}
# 测试里引用的币安官方文档示例 secret 与签名（公开的示例值，不是任何人的密钥）
ALLOW = {"NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j",
         "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71",
         # tools/setup.ps1：python.org 官方 python-3.13.17-amd64.zip 的 SHA256（公开文件哈希）
         "B26CA4B4EE51E157836049934D2B8EABD55939BE296F3130254251B6DF96EC2F"}


def publish_files():
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if p.is_file() and not (set(rel.parts) & IGNORED_DIRS) and p.name not in IGNORED_FILES:
            yield p


def test_no_secrets_or_personal_paths():
    hits = []
    for p in publish_files():
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, rx in PATTERNS.items():
            for m in rx.finditer(text):
                if m.group(0) not in ALLOW:
                    hits.append(f"{p.relative_to(ROOT)}: {name}: {m.group(0)[:12]}…")
    assert not hits, "\n".join(hits)


def test_gitignore_protects_config():
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "config.ini" in gi and ".venv/" in gi and "logs/" in gi
