@echo off
rem aiQuantHelper one-click launcher.
rem   tools\setup.ps1 : find or download Python, create/repair .venv, install deps, create config.ini
rem   app.main        : check config.ini and start the web page
rem This file is intentionally pure ASCII (cmd.exe mis-parses UTF-8 batch files); all messages are printed by the scripts.
setlocal
cd /d "%~dp0"
title aiQuantHelper
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup.ps1"
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m app.main %*
if errorlevel 1 goto fail
endlocal
exit /b 0

:fail
set "RC=%errorlevel%"
if not defined AQH_NO_PAUSE (
  echo.
  pause
)
endlocal & exit /b %RC%
