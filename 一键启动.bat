@echo off
chcp 936 >nul
title 直播源解析工具 - 一键启动
cd /d "%~dp0"

echo.
echo  ============================================================
echo   直播源解析工具  (based on wbt5/real-url)
echo  ============================================================
echo.

where python >nul 2>nul
if not errorlevel 1 goto found_python
where py >nul 2>nul
if not errorlevel 1 goto found_py
echo [错误] 未检测到 Python，请先安装 Python 3.8 以上版本，
echo         安装时请勾选 "Add Python to PATH"。
echo.
pause
exit /b 1

:found_python
set "PY=python"
goto python_ok
:found_py
set "PY=py"

:python_ok
echo [1/4] Python 已就绪: %PY%

if exist ".venv\Scripts\python.exe" goto venv_ok
echo [2/4] 首次运行，正在创建虚拟环境...
"%PY%" -m venv .venv
if errorlevel 1 (
  echo [错误] 虚拟环境创建失败，请检查 Python 安装。
  pause
  exit /b 1
)
:venv_ok
set "VPY=%CD%\.venv\Scripts\python.exe"
echo [2/4] 虚拟环境已就绪

echo [3/4] 正在检查依赖...
"%VPY%" -c "import flask,requests,execjs,Crypto,loguru,distro,tqdm,httpx,qrcode,PIL" >nul 2>nul
if not errorlevel 1 goto deps_ok

"%VPY%" -m pip install setuptools --retries 8 --timeout 45 --disable-pip-version-check -q >nul 2>nul
"%VPY%" -m pip install -r requirements.txt --no-build-isolation --retries 8 --timeout 45 --disable-pip-version-check
if errorlevel 1 (
  echo       默认源安装失败，尝试清华镜像...
  "%VPY%" -m pip install -r requirements.txt --no-build-isolation -i https://pypi.tuna.tsinghua.edu.cn/simple --retries 5 --timeout 30 --disable-pip-version-check
  if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络连接后重试。
    pause
    exit /b 1
  )
)
:deps_ok
echo [3/4] 依赖已就绪

where node >nul 2>nul
if errorlevel 1 (
  echo [提示] 未检测到 Node.js，斗鱼/爱奇艺等平台可能无法解析。
) else (
  echo [4/4] Node.js 已就绪
)

echo.
echo  正在启动服务，浏览器将自动打开 http://127.0.0.1:8899/
echo  关闭本窗口或按 Ctrl+C 即可停止服务。
echo.
"%VPY%" web\app.py --port 8899

echo.
echo  服务已停止。
pause
