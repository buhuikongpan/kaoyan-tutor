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

REM 版本配套：fastapi 0.141.1 + starlette 1.6.0（2025-08 升级，不再强制降级 starlette 0.27）

REM ============ 4. Start ============
echo.
netstat -ano | findstr ":8000 " | findstr LISTENING >nul 2>&1
if not errorlevel 1 (
    echo [OK] Platform already running. Opening browser...
    start "" http://localhost:8000
    pause
    exit /b 0
)

echo [OK] Starting server...
echo     URL: http://localhost:8000  (close this window to stop)
echo.
start "" http://localhost:8000
cd backend
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000

pause
