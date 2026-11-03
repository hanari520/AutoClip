@echo off
chcp 65001 >nul
cd /d "%~dp0.."

powershell -NoProfile -ExecutionPolicy Bypass -Command "$procs = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*gui_app_light.py*' }); if ($procs.Count -gt 0) { foreach ($p in $procs) { taskkill /F /T /PID $p.ProcessId | Out-Null }; Write-Host ('[OK] 已关闭 AutoClip GUI（共 ' + $procs.Count + ' 个进程）') } else { Write-Host '[i] AutoClip GUI 当前未在运行' }"

ping -n 3 127.0.0.1 >nul
