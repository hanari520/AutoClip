@echo off
chcp 65001 >nul
cd /d "%~dp0.."
python web_console_light.py --port 5001 --host 0.0.0.0
if errorlevel 1 pause
