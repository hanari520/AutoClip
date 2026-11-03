@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set "ROOT=%CD%"
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

if not exist logs mkdir logs

powershell -NoProfile -ExecutionPolicy Bypass -Command "$procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*gui_app_light.py*' }; if ($procs) { Write-Host '[i] AutoClip GUI 已在运行，无需重复启动'; exit 0 } else { try { Start-Process pythonw -ArgumentList 'gui_app_light.py' -WorkingDirectory '%ROOT%' -WindowStyle Hidden -RedirectStandardOutput '%ROOT%\logs\gui_light.log' -RedirectStandardError '%ROOT%\logs\gui_light.err.log' -ErrorAction Stop; Write-Host '[OK] AutoClip GUI 已后台启动' } catch { Write-Host ('[!] 启动失败: ' + $_.Exception.Message); exit 1 } }"

if errorlevel 1 pause
