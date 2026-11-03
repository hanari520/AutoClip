@echo off
chcp 65001 >nul
cd /d "%~dp0.."

powershell -NoProfile -ExecutionPolicy Bypass -Command "$procs = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*bilirecorder_webhook_server.py*' }); foreach ($p in $procs) { taskkill /F /T /PID $p.ProcessId | Out-Null }; $tails = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'powershell.exe' -and $_.ProcessId -ne $PID -and $_.CommandLine -like '*Get-Content*webhook.log*' }); foreach ($t in $tails) { Stop-Process -Id $t.ProcessId -Force -ErrorAction SilentlyContinue }; if ($procs.Count -gt 0) { Write-Host '[OK] 已关闭 BililiveRecorder Webhook 桥接（端口 18010）' } else { Write-Host '[i] Webhook 桥接当前未在运行' }"

ping -n 3 127.0.0.1 >nul
