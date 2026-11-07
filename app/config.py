"""读取 config.ini 并校验。

配置文件位置：默认是工程根目录的 config.ini；可用环境变量 AQH_CONFIG 或启动参数 --config <路径> 指定别的文件。
（MCP 子进程由 agent 拉起时，通过环境变量 AQH_CONFIG 拿到同一份路径。）
"""
import configparser
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.ini"
EXAMPLE_CONFIG = ROOT / "config.example.ini"
MOCK_DIR = ROOT / "mock"
LOG_DIR = ROOT / "logs"

DEEPSEEK_GUIDE = """
================================================================
  没有配置 DeepSeek 大模型的 API Key，程序不能启动。
================================================================
AI 运维助手需要调用 DeepSeek 大模型。请按下面的步骤拿到 key（约 3 分钟）：

  1. 浏览器打开 https://platform.deepseek.com ，用手机号或邮箱注册并登录。
  2. 左侧菜单「充值」(Top up)：充值少量余额（几元即可，每次问答通常不到 1 分钱）。
     没有余额时调用会返回 402 Insufficient Balance。
  3. 左侧菜单「API keys」→「创建 API key」，名字随便填（如 aiQuantHelper）。
  4. 复制弹出的 key（以 sk- 开头）。⚠️ 它只显示这一次，请立即复制保存。
  5. 用记事本打开下面这个文件，粘贴到 [deepseek] 下面的 api_key = 后面，保存：
       {config}
     例如：  api_key = sk-0123456789abcdef
  6. 重新运行 start.bat。

⛔ 不要把 key 发给别人、不要上传到网上；config.ini 已被 .gitignore 忽略。
"""


class ConfigError(Exception):
    """配置有问题，不允许启动。消息直接给用户看。"""


@dataclass(frozen=True)
class Settings:
    config_path: Path
    deepseek_key: str
    deepseek_model: str
    deepseek_base_url: str
    binance_key: str
    binance_secret: str
    binance_proxy: str
    port: int
    open_browser: bool

    @property
    def mode(self):
        """LIVE = 读币安真实账户；MOCK = 读 mock/ 目录的模拟数据。"""
        return "LIVE" if self.binance_key else "MOCK"


def config_path(argv=None):
    """--config <路径> 优先，其次环境变量 AQH_CONFIG，最后默认 config.ini。"""
    argv = list(argv or [])
    for i, a in enumerate(argv):
        if a == "--config" and i + 1 < len(argv):
            return Path(argv[i + 1]).expanduser().resolve()
        if a.startswith("--config="):
            return Path(a.split("=", 1)[1]).expanduser().resolve()
    env = os.environ.get("AQH_CONFIG", "").strip()
    return Path(env).expanduser().resolve() if env else DEFAULT_CONFIG


def _clean(v):
    """去掉首尾空白和用户误加的引号。"""
    v = (v or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1].strip()
    return v


def load(path=None):
    """读取并校验配置。有问题抛 ConfigError（消息给用户看）。"""
    path = Path(path or config_path())
    if not path.exists():
        raise ConfigError(f"找不到配置文件：{path}\n请把 config.example.ini 复制一份，改名为 config.ini（start.bat 会自动做这一步）。")
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(path, encoding="utf-8-sig")       # utf-8-sig：兼容记事本保存时加的 BOM
    except configparser.Error as e:
        raise ConfigError(f"配置文件格式错误：{path}\n{e}") from e

    def get(sec, key, default=""):
        return _clean(cp.get(sec, key, fallback=default))

    ds_key = get("deepseek", "api_key")
    if not ds_key:
        raise ConfigError(DEEPSEEK_GUIDE.format(config=path))
    if not ds_key.isascii() or " " in ds_key:
        raise ConfigError(f"[deepseek] api_key 里有中文或空格，看起来没有粘贴对。请检查：{path}")

    bn_key, bn_secret = get("binance", "api_key"), get("binance", "api_secret")
    if bool(bn_key) != bool(bn_secret):
        missing = "api_secret" if bn_key else "api_key"
        raise ConfigError(
            f"[binance] 只填了一半：缺少 {missing}。\n"
            f"  · 要读真实账户：api_key 和 api_secret 两个都要填；\n"
            f"  · 要用演示数据（MOCK）：两个都留空。\n"
            f"配置文件：{path}")
    for name, v in (("api_key", bn_key), ("api_secret", bn_secret)):
        if v and (not v.isascii() or " " in v):
            raise ConfigError(f"[binance] {name} 里有中文或空格，看起来没有粘贴对。请检查：{path}")

    proxy = get("binance", "proxy")
    if proxy and "://" not in proxy:
        raise ConfigError(f"[binance] proxy 要写完整，例如 http://127.0.0.1:7890 （现在是：{proxy}）")

    port_s = get("web", "port", "8866") or "8866"
    try:
        port = int(port_s)
        assert 1024 <= port <= 65535
    except (ValueError, AssertionError):
        raise ConfigError(f"[web] port 必须是 1024~65535 之间的数字（现在是：{port_s}）") from None

    return Settings(
        config_path=path,
        deepseek_key=ds_key,
        deepseek_model=get("deepseek", "model", "deepseek-flash") or "deepseek-flash",
        deepseek_base_url=(get("deepseek", "base_url", "https://api.deepseek.com") or "https://api.deepseek.com").rstrip("/"),
        binance_key=bn_key,
        binance_secret=bn_secret,
        binance_proxy=proxy,
        port=port,
        open_browser=get("web", "open_browser", "yes").lower() in ("yes", "true", "1", "on", "y"),
    )
