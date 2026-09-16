@echo off
chcp 65001 >nul
cd /d "%~dp0.."
python gui_app.py
pause
