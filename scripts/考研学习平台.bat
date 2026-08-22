@echo off
setlocal enabledelayedexpansion
title 考研学习平台 - 一键启动

REM ===== 定位项目根目录（兼容脚本放在 scripts\ 内或桌面/项目旁）=====
set "PROJECT="
if exist "%~dp0..\.env.example" set "PROJECT=%~dp0.."
if not defined PROJECT if exist "%~dp0DIFY考研学习平台\本地文件\.env.example" set "PROJECT=%~dp0DIFY考研学习平台\本地文件"
if not defined PROJECT if exist "%~dp0..\DIFY考研学习平台\本地文件\.env.example" set "PROJECT=%~dp0..\DIFY考研学习平台\本地文件"
if not defined PROJECT (
    echo [X] 无法定位项目目录，请把脚本放在 scripts 文件夹内或项目文件夹旁再运行
    pause
    exit /b 1
)
if not exist "%PROJECT%\scripts" (
    echo [X] 定位到 "%PROJECT%" 但未找到 scripts 文件夹，请检查目录
    pause
    exit /b 1
)
cd /d "%PROJECT%"

echo ====================================
echo   考研学习平台 - 一键启动
echo   项目目录: %PROJECT%
echo ====================================
echo.

REM ===== 1. 查找 Python =====
set "PY="
if exist "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe" (
    set "PY=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
) else (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [X] 未找到 Python，请先安装 Python 3.11
    pause
    exit /b 1
)
echo [OK] Python: %PY%

REM ===== 2. 检查 .env（找不到才提示，正常情况不会弹）=====
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo [!] 首次运行：已从模板生成 .env，请填入 API Key 后重新运行
    start notepad ".env"
    pause
    exit /b 1
)

REM ===== 3. 检查依赖 =====
"%PY%" -c "import fastapi, sqlalchemy, starlette" >nul 2>&1
if errorlevel 1 (
    echo [!] 正在安装依赖...
    cd backend
    "%PY%" -m pip install -r requirements.txt
    cd ..
) else (
    echo [OK] 依赖已就绪
)

REM ===== 4. 端口检测：服务已在运行则询问是否重启 =====
echo.
set "OLDPID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000 " ^| findstr LISTENING') do (
    if not defined OLDPID set "OLDPID=%%p"
)
if defined OLDPID goto :ask
goto :start

:ask
echo [!] 检测到旧服务仍在运行（PID: !OLDPID!）
set "CHOICE="
set /p "CHOICE=代码更新后需重启：按 Y 重启服务，按 N 仅打开页面："
if /i not "%CHOICE%"=="Y" goto :open
echo [*] 正在结束旧进程 !OLDPID! ...
taskkill /PID !OLDPID! /F >nul 2>&1
timeout /t 1 /nobreak >nul 2>&1

:start
echo [OK] 正在启动服务...
echo     访问地址: http://localhost:8000 （关闭此窗口即停止服务）
echo.
start "" http://localhost:8000
cd backend
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
exit /b 0

:open
start "" http://localhost:8000
pause
exit /b 0
