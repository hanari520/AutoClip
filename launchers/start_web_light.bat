@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set "ROOT=%CD%"
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

if not exist logs mkdir logs

powershell -NoProfile -ExecutionPolicy Bypass -Command "$procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*web_console_light.py*' }; if ($procs) { Write-Host '[i] AutoClip Web 控制台已在后台运行: http://127.0.0.1:5001' } else { try { Start-Process cmd -ArgumentList '/c','pythonw web_console_light.py --port 5001 --host 0.0.0.0 > logs\web_console.log 2>&1' -WorkingDirectory '%ROOT%' -WindowStyle Hidden -ErrorAction Stop; Write-Host '[OK] AutoClip Web 控制台已启动: http://127.0.0.1:5001' } catch { Write-Host ('[!] 启动失败: ' + $_.Exception.Message); exit 1 } }"

if errorlevel 1 (
  pause
  exit /b 1
)

start "AutoClip Web 日志（关闭此窗口不影响后台服务）" powershell -NoProfile -ExecutionPolicy Bypass -Command "if (-not (Test-Path -LiteralPath '%ROOT%\logs\web_console.log')) { New-Item -ItemType File -Path '%ROOT%\logs\web_console.log' -Force | Out-Null }; Write-Host '  ● AutoClip Web 控制台后台运行中: http://127.0.0.1:5001' -ForegroundColor Cyan; Write-Host '  ● 关闭本窗口不会停止服务；停止请运行 stop_web_light.bat；重开本窗口请再运行 start_web_light.bat' -ForegroundColor DarkGray; Get-Content -LiteralPath '%ROOT%\logs\web_console.log' -Wait -Tail 30 -Encoding UTF8"
