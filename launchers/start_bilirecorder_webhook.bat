@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set "ROOT=%CD%"
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

if not exist logs mkdir logs

powershell -NoProfile -ExecutionPolicy Bypass -Command "$procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*bilirecorder_webhook_server.py*' }; if ($procs) { Write-Host '[i] Webhook 桥接已在后台运行: http://127.0.0.1:18010/webhook/mikufansrecorder' } else { try { Start-Process cmd -ArgumentList '/c','pythonw bilirecorder_webhook_server.py --host 127.0.0.1 --port 18010 > logs\webhook.log 2>&1' -WorkingDirectory '%ROOT%' -WindowStyle Hidden -ErrorAction Stop; Write-Host '[OK] BililiveRecorder Webhook 桥接已启动: http://127.0.0.1:18010/webhook/mikufansrecorder' } catch { Write-Host ('[!] 启动失败: ' + $_.Exception.Message); exit 1 } }"

if errorlevel 1 (
  pause
  exit /b 1
)

start "Webhook 日志（关闭此窗口不影响后台服务）" powershell -NoProfile -ExecutionPolicy Bypass -Command "if (-not (Test-Path -LiteralPath '%ROOT%\logs\webhook.log')) { New-Item -ItemType File -Path '%ROOT%\logs\webhook.log' -Force | Out-Null }; Write-Host '  ● Webhook 桥接后台运行中: http://127.0.0.1:18010/webhook/mikufansrecorder' -ForegroundColor Cyan; Write-Host '  ● 关闭本窗口不会停止服务；停止请运行 stop_bilirecorder_webhook.bat；重开本窗口请再运行 start_bilirecorder_webhook.bat' -ForegroundColor DarkGray; Get-Content -LiteralPath '%ROOT%\logs\webhook.log' -Wait -Tail 30 -Encoding UTF8"
