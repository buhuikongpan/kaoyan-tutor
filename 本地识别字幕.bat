@echo off
title 本地识别字幕
cd /d "%~dp0"
echo ============================================
echo   本地识别字幕脚本
echo   扫描本地未识别视频 - 逐个调用本地平台 ASR 识别
echo   （本地平台未运行会自动启动）
echo ============================================
where python >nul 2>nul || (echo [错误] 未找到 python & pause & exit /b 1)
python scripts/local_subtitle.py
echo.
pause