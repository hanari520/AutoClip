# AutoClip 归档脚本

本目录存放当前不再使用（或暂时不使用）的脚本，保留完整源码以便将来还原。

## 归档内容

| 文件 | 原位置 | 状态说明 |
| --- | --- | --- |
| `gui_app.py` | 根目录 | 旧桌面 GUI 壳，已被 `gui_app_light.py`（Light 三栏 GUI）取代；对应启动器 `launchers/旧/start_gui_desktop.bat` |
| `config_builder.py` | 根目录 | 旧 PyQt6 配置生成器，已被 `config_gui.py`（tkinter 版，由 `launchers/启动配置生成器.bat` 启动）取代 |
| `config_web.py` | 根目录 | Web 版配置生成器，无启动器引用 |
| `pc_console_gui.py` | 根目录 | PC 监控面板（读取 `console_activity.jsonl`），无启动器引用 |

## 还原方法

```powershell
cd /d <仓库根目录>
git mv archive\gui_app.py gui_app.py
# 或直接复制：
# copy archive\gui_app.py .
```

完整还原快照还备份在 `backups/root_cleanup_20260809_135440/`（含全部根目录脚本、README、AGENTS.md、launchers 和 debug_response.txt）。

## 归档时间

2026-08-09
