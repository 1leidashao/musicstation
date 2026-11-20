@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

rem ---------- 定位 Python 解释器（优先 .venv，其次 PATH 里的 python / py launcher） ----------
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    echo [ERROR] 未找到 Python，请先安装 Python 3 并加入 PATH，或在本目录创建 .venv 虚拟环境。
    pause
    exit /b 1
)

echo MusicStation 启动中 ... 默认地址 http://localhost:8765 （端口见 config.json）
%PY% app.py
pause
