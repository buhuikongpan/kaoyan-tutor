@echo off
setlocal enabledelayedexpansion
title Kaoyan Platform - One-Click Start

cd /d "%~dp0.."

echo ========================================
echo   Kaoyan Learning Platform - Start
echo ========================================
echo.

REM ============ 1. Find Python ============
set "PY="
if exist "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe" (
    set "PY=C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe"
) else (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [X] Python 3.11 not found. Please install it first.
    pause
    exit /b 1
)
echo [OK] Python: %PY%

REM ============ 2. Check .env ============
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo [!] .env created from template. Edit it with API keys then run again.
    start notepad ".env"
    pause
    exit /b 1
)

REM ============ 3. Check dependencies ============
"%PY%" -c "import fastapi, sqlalchemy, starlette" >nul 2>&1
if errorlevel 1 (
    echo [!] Installing dependencies...
    cd backend
    "%PY%" -m pip install -r requirements.txt
    cd ..
) else (
    echo [OK] Dependencies ready
)

REM ============ 4. 端口 8000 检测：已运行则询问重启 ============
set "OLDPID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000 " ^| findstr LISTENING') do (
    if not defined OLDPID set "OLDPID=%%p"
)
if defined OLDPID (
    echo [!] 检测到旧服务进程 ^(PID: !OLDPID!^) 仍在运行
    set "CONFIRM="
    set /p "CONFIRM=代码已更新需重启：按 Y 结束旧进程并重启，按 N 仅打开页面："
    if /i "!CONFIRM!"=="Y" (
        echo [*] 结束旧进程 !OLDPID! ...
        taskkill /PID !OLDPID! /F >nul 2>&1
        timeout /t 1 /nobreak >nul
    ) else (
        echo [OK] 保留旧服务，打开页面。
        start "" http://localhost:8000
        exit /b 0
    )
)
REM 双保险：清理可能残留的监听（按 PID 精确结束，绝不按进程名批量杀）
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000 " ^| findstr LISTENING') do (
    taskkill /PID %%p /F >nul 2>&1
)

echo [OK] Starting server...
echo     URL: http://localhost:8000  (close this window to stop)
echo.
start "" http://localhost:8000
cd backend
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000

pause