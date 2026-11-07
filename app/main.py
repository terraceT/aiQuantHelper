"""启动入口：python -m app.main [--config <路径>] [--no-browser] [--check]

  1. 读 config.ini 并校验（没填 DeepSeek key ⇒ 打印申请步骤，拒绝启动）
  2. 现验 DeepSeek key（401 ⇒ 拒绝启动；网络不通 ⇒ 警告后照常启动）
  3. 币安：没填 key ⇒ 演示模式（MOCK）；填了 ⇒ 实盘模式（LIVE），做一次连通性与权限自检（失败不阻止启动，页面会显示原因）
  4. 检查端口 → 启动网页服务 → 打开浏览器
  --check：只做 1~3 步就退出（退出码 0 = 可以启动）

退出码：0 正常；2 配置有问题；3 DeepSeek key 无效或模型名不对；4 端口被占用。
"""
import logging
import socket
import sys
import threading
import webbrowser

from urllib.parse import urlparse

import requests

from . import config as C

LINE = "=" * 64


def check_deepseek(s):
    """现验大模型 key 与模型名。返回 (能否启动, 说明)。

    默认是 DeepSeek；也可以在 [deepseek] 段改 base_url / model / api_key，换成任何 OpenAI 兼容、
    支持工具调用（Function Calling）的大模型（见 README「换用其他大模型」）。
    只有「key 被明确拒绝（401/403）」和「DeepSeek 上模型名不存在」才拒绝启动；
    其他平台的模型列表不一定完整，模型名对不上只警告。
    """
    host = urlparse(s.deepseek_base_url).netloc or s.deepseek_base_url
    is_deepseek = host.endswith("deepseek.com")
    name = "DeepSeek" if is_deepseek else f"大模型服务 {host}"
    try:
        r = requests.get(f"{s.deepseek_base_url}/models", headers={"Authorization": f"Bearer {s.deepseek_key}"}, timeout=15)
    except requests.RequestException as e:
        return True, f"⚠ 暂时连不上 {name}（{type(e).__name__}），先照常启动；AI 助手可能不可用，请检查网络和 base_url。"
    if r.status_code in (401, 403):
        where = ("请到 https://platform.deepseek.com → API keys 重新复制或新建一个" if is_deepseek
                 else "请到该平台的控制台重新复制 API Key")
        return False, (f"{name} 拒绝了这个 API Key（HTTP {r.status_code}）：key 填错了、已被删除，或复制时多了/少了字符。\n"
                       f"{where}，填入：\n  {s.config_path}\n然后重新运行 start.bat。")
    if r.status_code != 200:
        return True, f"⚠ {name} 返回 HTTP {r.status_code}（可能不支持查询模型列表），先照常启动；AI 助手如果报错，请检查 base_url。"
    try:
        models = [m.get("id") for m in r.json().get("data", [])]
    except (ValueError, AttributeError):
        models = []
    if models and s.deepseek_model not in models:
        msg = (f"[deepseek] model = {s.deepseek_model} 不在 {name} 返回的模型列表里。"
               f"可用的模型：{', '.join(models[:20])}{' 等' if len(models) > 20 else ''}\n请修改：{s.config_path}")
        if is_deepseek:
            return False, msg
        return True, "⚠ " + msg + "\n  （该平台的列表不一定完整，先照常启动；AI 助手如果报「模型不存在」，再改 model。）"
    return True, f"✓ {name} API Key 有效，模型 {s.deepseek_model}"


def check_binance(s):
    from .datasource import DataSource
    if s.mode == "MOCK":
        return ["✓ 币安：未填写 API Key ⇒ 演示模式（MOCK），数据来自 mock/ 目录，不是你的真实账户。",
                "  想看真实账户：在 config.ini 的 [binance] 填 api_key 和 api_secret（见 README.txt 第五节）。"]
    lines = ["● 币安：已填写 API Key ⇒ 实盘模式（LIVE，只读）" + (f"，代理 {s.binance_proxy}" if s.binance_proxy else "，直连")]
    c = DataSource(s).connectivity()
    if not c.get("reachable"):
        lines.append(f"  ⚠ 连不上币安：{c.get('error')}")
        lines.append("    先照常启动，页面上会显示错误原因。")
        return lines
    lines.append(f"  ✓ 币安可达，往返 {c['latency_ms']} ms，时钟偏差 {c['time_offset_ms']} ms")
    if "api_permissions" in c:
        lines.append("  ✓ API Key 有效，权限：" + "、".join(f"{k}={'开' if v else '关'}" for k, v in c["api_permissions"].items()))
    else:
        lines.append(f"  ⚠ API Key 校验失败：{c.get('api_permissions_error')}")
        lines.append("    先照常启动，页面上会显示错误原因（处理办法见 docs/runbook.md）。")
    for k in ("time_warning", "security_warning", "security_note"):
        if c.get(k):
            lines.append(f"  ⚠ {c[k]}")
    return lines


def _open_in_notepad(path):
    """配置有问题：直接用记事本打开 config.ini，用户改完保存、重新双击 start.bat 即可。"""
    import os
    if os.name != "nt" or os.environ.get("AQH_NO_NOTEPAD") or not path.exists():
        return
    try:
        import subprocess
        subprocess.Popen(["notepad.exe", str(path)])
        print()
        print("已用记事本打开配置文件，填好并保存后，重新双击 start.bat。", flush=True)
    except OSError:
        pass


def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        try:
            sk.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    print(LINE + "\n  aiQuantHelper —— 币安合约账户 AI 运维助手（只读）\n" + LINE, flush=True)
    path = C.config_path(argv)
    try:
        s = C.load(path)
    except C.ConfigError as e:
        print(str(e), flush=True)
        _open_in_notepad(path)
        return 2
    print(f"配置文件：{s.config_path}", flush=True)

    ok, msg = check_deepseek(s)
    print(msg, flush=True)
    if not ok:
        return 3
    for line in check_binance(s):
        print(line, flush=True)

    if "--check" in argv:
        print("自检完成（--check），未启动服务。", flush=True)
        return 0

    if not port_free(s.port):
        print(f"\n端口 {s.port} 已被占用：可能已经开着一个 aiQuantHelper 窗口（先关掉它），"
              f"或者把 config.ini 的 [web] port 改成别的数字（如 8877）。", flush=True)
        return 4

    C.LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(filename=C.LOG_DIR / "app.log", level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    url = f"http://127.0.0.1:{s.port}/"
    print(f"\n页面地址：{url}\n关闭本窗口（或按 Ctrl + C）即停止。\n", flush=True)
    if s.open_browser and "--no-browser" not in argv:
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()

    import uvicorn
    from .web import create_app
    uvicorn.run(create_app(s), host="127.0.0.1", port=s.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
