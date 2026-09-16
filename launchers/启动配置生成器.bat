@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title AutoClip Config Generator

echo =============================================
echo   AutoClip Config Generator (Desktop)
echo =============================================
echo.
echo Starting config_gui.py...
echo.

python "%~dp0..\config_gui.py"
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Exit code: %errorlevel%
    echo.
    pause
)
