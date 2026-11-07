# aiQuantHelper 环境准备（由 start.bat 调用；PowerShell 5.1 起，Windows 10/11 自带）。
#   1. 准备 Python：工程自带的 .python → 电脑上的 Python 3.10+ → 都没有就自动下载官方免安装版到 .python
#      （不需要管理员权限，不改系统 PATH，不写注册表；删掉 .python 文件夹即彻底清除）
#   2. 准备虚拟环境 .venv（坏了自动重建）
#   3. toolsootstrap.py：安装依赖（多源自动切换）、生成 config.ini
# 启动程序本身由 start.bat 直接做（参数原样传给 app.main，不经过 PowerShell 解析）。
# 退出码：0 环境就绪；1 失败（已打印原因）。

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$LocalPy = Join-Path $Root '.python\python.exe'
$VenvPy  = Join-Path $Root '.venv\Scripts\python.exe'

$PyVersion = '3.13.17'
$PyFile    = "python-$PyVersion-amd64.zip"
$PySha256  = 'B26CA4B4EE51E157836049934D2B8EABD55939BE296F3130254251B6DF96EC2F'
$PySources = @(
    @{ Name = 'python.org 官方源';   Url = "https://www.python.org/ftp/python/$PyVersion/$PyFile" },
    @{ Name = 'npmmirror 国内镜像'; Url = "https://registry.npmmirror.com/-/binary/python/$PyVersion/$PyFile" }
)

function Say([string]$msg) { Write-Host $msg }

function Test-Py([string]$exe, [string[]]$pre = @()) {
    # 能运行且版本 >= 3.10 ⇒ $true。Windows 应用商店的 python.exe 占位符会失败（返回非 0），自然被排除。
    try {
        & $exe @pre -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-Python {
    if ((Test-Path -LiteralPath $LocalPy) -and (Test-Py $LocalPy)) { return @{ Exe = $LocalPy; Pre = @() } }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '-3.13', '-3.12', '-3.11', '-3.10', '-3.14') {
            if (Test-Py 'py' @($v)) { return @{ Exe = 'py'; Pre = @($v) } }
        }
    }
    foreach ($name in 'python', 'python3') {
        $c = Get-Command $name -ErrorAction SilentlyContinue
        if ($c -and (Test-Py $c.Source)) { return @{ Exe = $c.Source; Pre = @() } }
    }
    return $null
}

function Get-File([string]$url, [string]$out) {
    # 优先用 Windows 自带的 curl.exe（快；速度低于 50 KB/s 持续 30 秒就放弃，换下一个源）
    $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
    if (Test-Path -LiteralPath $curl) {
        & $curl -fsSL --connect-timeout 20 --speed-limit 51200 --speed-time 30 --retry 2 -o $out $url
        return ($LASTEXITCODE -eq 0)
    }
    try {
        Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing -TimeoutSec 300
        return $true
    } catch { return $false }
}

function Install-Python {
    Say '      电脑上没有可用的 Python 3.10+，自动下载一份放到本工程的 .python 文件夹。'
    Say '      （免安装：不需要管理员权限，不修改系统设置，删掉这个文件夹即可彻底清除）'
    if (-not [Environment]::Is64BitOperatingSystem) { Say '      本程序需要 64 位 Windows。'; return $false }
    $tmp = Join-Path $Root '.python.download'
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $zip = Join-Path $tmp $PyFile
    $ok = $false
    foreach ($s in $PySources) {
        Say ("      正在从 {0} 下载 Python {1}（约 30 MB）..." -f $s.Name, $PyVersion)
        if (-not (Get-File $s.Url $zip)) { Say '      下载失败或太慢，换下一个源。'; continue }
        if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne $PySha256) {
            Say '      文件校验失败（下载不完整或被篡改），已丢弃，换下一个源。'
            Remove-Item -LiteralPath $zip -Force
            continue
        }
        $ok = $true; break
    }
    if (-not $ok) { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue; return $false }
    Say '      下载完成，校验通过，正在解压...'
    $unpacked = Join-Path $tmp 'unpacked'
    Expand-Archive -LiteralPath $zip -DestinationPath $unpacked -Force
    $target = Join-Path $Root '.python'
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    Move-Item -LiteralPath $unpacked -Destination $target
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    if (-not (Test-Py $LocalPy)) { Say '      Python 已解压，但无法运行。'; return $false }
    Say ("      Python {0} 已就绪。" -f $PyVersion)
    return $true
}

function Fail([string]$msg) {
    Say ''
    Say $msg
    exit 1
}

# ---------------------------------------------------------------- 1~2. Python 与虚拟环境
$needVenv = $true
if (Test-Path -LiteralPath $VenvPy) {
    if (Test-Py $VenvPy) { $needVenv = $false }
    else {
        Say '虚拟环境 .venv 已损坏（常见原因：建它时用的 Python 被卸载或升级了），正在自动重建...'
        try { Remove-Item -LiteralPath (Join-Path $Root '.venv') -Recurse -Force }
        catch { Fail '无法删除 .venv 文件夹：请关闭所有 aiQuantHelper 窗口后，重新双击 start.bat。' }
    }
}
if ($needVenv) {
    Say '[1/3] 正在准备 Python...'
    $py = Find-Python
    if (-not $py) {
        if (-not (Install-Python)) {
            Fail ("================================================================`n" +
                  "  自动下载 Python 失败（多半是网络问题）。`n" +
                  "================================================================`n" +
                  "  · 请确认能上网；如果平时上网要开代理，请先开好代理，再双击 start.bat 重试。`n" +
                  "  · 也可以手动安装 Python 3.12：https://www.python.org/downloads/windows/`n" +
                  "    安装第一屏勾选 `"Add python.exe to PATH`"，装好后重新双击 start.bat。")
        }
        $py = @{ Exe = $LocalPy; Pre = @() }
    }
    Say ("      使用：{0} {1}" -f $py.Exe, ($py.Pre -join ' '))
    Say '[2/3] 正在创建虚拟环境 .venv（只在第一次运行时做）...'
    $pre = $py.Pre
    & $py.Exe @pre -m venv (Join-Path $Root '.venv')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Py $VenvPy)) {
        Fail '创建虚拟环境失败。可以删除本目录下的 .venv 和 .python 文件夹后，重新双击 start.bat。'
    }
}

# ---------------------------------------------------------------- 3. 依赖与配置
Say '[3/3] 检查依赖...'
& $VenvPy (Join-Path $Root 'tools\bootstrap.py')
if ($LASTEXITCODE -ne 0) { exit 1 }

exit 0
