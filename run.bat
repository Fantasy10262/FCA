@echo off
cd /d "%~dp0"
REM 使用 WorkBuddy 管理的 Python 虚拟环境运行（已安装 Flask）
set VENV_PY=c:\Users\lenovo\.workbuddy\binaries\python\envs\default\scripts\python.exe
if exist "%VENV_PY%" (
    "%VENV_PY%" app.py
) else (
    REM 回退：使用系统 python（需自行 pip install -r requirements.txt）
    python app.py
)
pause
