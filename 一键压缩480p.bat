@echo off
title 一键压缩480p
cd /d "%~dp0"
echo ============================================
echo   一键压缩本地视频为 480p（CRF30）
echo   产物存到 storage\videos_480p，供推送云端使用
echo   已压过的自动跳过，可断点续跑
echo ============================================
where python >nul 2>nul || (echo [错误] 未找到 python & pause & exit /b 1)
python scripts/build_480p.py
echo.
pause