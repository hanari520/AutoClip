@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1

echo ============================================
echo   AutoClip BililiveRecorder Webhook Bridge
echo ============================================
echo.
echo [AutoClip] Listen: http://127.0.0.1:18010/webhook/mikufansrecorder
echo [AutoClip] Config: configs\serverchan_config.json
echo [AutoClip] Cookie check: on start, then every 6 hours
echo.
echo BililiveRecorder Webhook v2 should be:
echo   http://127.0.0.1:18010/webhook/mikufansrecorder
echo.
echo Press Ctrl+C to stop.
echo.

python bilirecorder_webhook_server.py --host 127.0.0.1 --port 18010

pause
