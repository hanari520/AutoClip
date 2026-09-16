@echo off
chcp 65001 >nul
cd /d "%~dp0.."

echo ============================================
echo   AutoClip Web Console v2 (Tailscale)
echo ============================================
echo.

echo [AutoClip] Starting web console on port 5000...
echo [AutoClip] Local:   http://localhost:5000
echo [AutoClip] Mobile:  http://TAILSCALE_IP:5000
echo.
echo Press Ctrl+C to stop.
echo.

python web_console.py --port 5000

pause
