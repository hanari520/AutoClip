@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1

echo ============================================
echo   BililiveRecorder Cookie Login Check
echo ============================================
echo.

python bilirecorder_webhook_server.py --check-cookie-once

echo.
pause
