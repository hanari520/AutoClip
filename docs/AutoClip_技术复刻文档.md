# AutoClip - 自动化直播切片系统技术复刻文档

> 版本: 2026-09-08  
> 项目根目录: `<仓库根目录>`  
> 适用场景: 将直播录播目录自动加工为带字幕、封面、标题和投稿清单的 B 站切片，可通过桌面 GUI、Light GUI、移动 GUI、Web 控制台、Light Web 控制台或命令行运行。

---

## 目录

1. [系统总览](#1-系统总览)
2. [项目结构](#2-项目结构)
3. [运行入口](#3-运行入口)
4. [核心流水线](#4-核心流水线)
5. [ASR 字幕系统](#5-asr-字幕系统)
6. [高光检测系统](#6-高光检测系统)
7. [字幕烧录与封面生成](#7-字幕烧录与封面生成)
8. [B 站投稿模块](#8-b-站投稿模块)
9. [配置系统](#9-配置系统)
10. [GUI 与 Web 控制台](#10-gui-与-web-控制台)
11. [环境与依赖](#11-环境与依赖)
12. [维护注意事项](#12-维护注意事项)

---

## 1. 系统总览

AutoClip 的目标是把一个录播目录自动加工成可投稿的切片产物。

典型输入:

- 一个或多个 `.mp4` 录播视频
- 弹幕文件，如 `.ass`、`.jsonl`
- 可选的 `Data_source.txt`
- 主播配置、提示词和全局配置

典型输出:

- `.srt` 字幕
- 高光片段清单 `Data_source.txt`
- 切片视频
- `_subbed.mp4` 烧录字幕视频
- 封面图
- 投稿清单与批量投稿状态

主要技术栈:

| 层级 | 技术 | 用途 |
| --- | --- | --- |
| 语言 | Python 3.10+ | 核心业务逻辑 |
| ASR | FunASR / Whisper / Belle | 语音转字幕 |
| 视频处理 | FFmpeg / ffprobe | 切片、烧录、抽帧 |
| 图像处理 | Pillow / fonttools | 封面合成与字体处理 |
| LLM | 硅基流动 / DeepSeek 官方 / OpenCode Go / 智谱（OpenAI Chat Completions 兼容接口） | 语义高光、标题、封面文案 |
| GUI | PyQt6 | 桌面端和移动端控制 |
| Web | `http.server` + REST + SSE | 手机远程控制、日志流 |
| 投稿 | `bilibili-api` | B 站上传、封面和参数提交 |

---

## 2. 项目结构

当前代码主体集中在以下目录和文件:

```text
<仓库根目录>
├── core/
│   ├── auto_pipeline.py              # 一键流水线主调度器
│   ├── convert_flv_to_mp4.py         # 步骤1-1: FLV 转 MP4（stream copy）
│   ├── convert_portrait_to_landscape.py # 步骤1-2: 竖屏 MP4 转横屏 1080p
│   ├── burn_danmu_video.py           # 步骤1-3: 同名 MP4 + XML 压制弹幕版
│   ├── convert_danmaku_xml.py        # 步骤1-4: 弹幕 XML→ASS 前置转换（弹幕分析源）
│   ├── generate_subtitles.py         # 步骤2: 批量生成 SRT
│   ├── generate_clips_combined.py    # 步骤3: 高光分析，输出 Data_source.txt
│   ├── generate_clips.py             # 步骤4: 按 Data_source.txt 生成切片 MP4
│   ├── burn_subtitles.py             # 步骤5: ASS 生成与字幕烧录
│   ├── generate_covers.py            # 步骤6: 旧版封面生成
│   └── generate_covers_new.py        # 新版封面样式生成
│
├── analysis/
│   ├── generate_clips_by_llm.py      # 独立 LLM 高光分析
│   └── get_data_by_danmu.py          # 弹幕热度分析
│
├── utils/
│   ├── funasr_transcriber.py         # FunASR 便携/直连转写
│   ├── whisper_transcriber.py        # Whisper 转写
│   ├── belle_transcriber.py          # Belle CTranslate2 转写
│   ├── ASRCorrector.py               # ASR 纠错词典
│   ├── bilibili_login.py             # B 站登录与凭证检测
│   ├── bilibili_uploader.py          # B 站投稿封装
│   ├── bilibili_meme_search.py       # B 站梗搜索与标题分析
│   ├── emotion2vec_analyzer.py       # 情绪分析
│   ├── streamer_prompts.py           # 主播 Prompt 管理
│   ├── get_danmu.py                  # 弹幕抓取
│   ├── video_filter.py               # 视频版本优先筛选（弹幕版/横屏主视频/竖屏备份）
│   └── edit_data_source_timestep.py  # 时间轴缓冲/校准工具
│
├── configs/
│   ├── streamers/                    # 主播配置 JSON
│   ├── prompts/                      # 高光分析提示词
│   ├── streamer_titles/              # 主播标题相关配置
│   ├── asr_dict.txt                  # ASR 纠错词典
│   ├── gui_config.json               # GUI 全局配置和本地凭证
│   └── requirements.txt              # 基础依赖列表
│
├── assets/
│   ├── font/                         # 字体
│   └── image/                        # 封面素材
│
├── launchers/
│   ├── start_gui_light.bat           # Light 版三栏桌面 GUI
│   ├── start_web_light.bat           # Light 版 Web 控制台，默认 5001 端口
│   ├── start_bilirecorder_webhook.bat # 录播姬 Webhook -> Server酱通知桥接 + Cookie 巡检
│   ├── 启动配置生成器.bat            # 调用 config_gui.py
│   └── 旧/                           # 已弃用启动器（桌面 GUI/移动 GUI/原 Web/手动 Cookie 检查）
│
├── archive/                          # 归档脚本（不再使用，保留源码便于还原）
│   ├── gui_app.py                    # 旧桌面 GUI 壳，被 Light GUI 取代
│   ├── config_builder.py             # 旧 PyQt6 配置生成器
│   ├── config_web.py                 # 旧 Web 配置生成器
│   ├── pc_console_gui.py             # 旧 PC 监控面板
│   └── README.md                     # 归档说明与还原方法
│
├── funasr/                           # 便携 FunASR 运行时与模型缓存
├── models/                           # Whisper / Belle 等模型
├── sessions/                         # Web 会话持久化
├── thumbnails/                       # Web 缩略图缓存
├── gui_app_mobile.py                 # GUI 主体实现
├── gui_app_light.py                  # Light 版三栏桌面 GUI，继承原业务逻辑
├── gui_light_theme.py                # Light GUI 全局 QSS 与色彩规范
├── web_console.py                    # Web 控制台
├── web_console_light.py              # Light 版 Web 控制台，复用原后端 API
├── bilirecorder_webhook_server.py    # 录播姬 Webhook v2 通知桥接服务
└── config_gui.py                     # tkinter 配置生成器
```

注意: `funasr/runtime/python` 和 `.venv_ct2_temp` 是运行时/依赖目录，体积很大。日常维护时优先查看根目录、`core/`、`utils/`、`analysis/`、`configs/` 和 `launchers/`。

---

## 3. 运行入口

> 2026-08-09 整理后，根目录只保留活跃入口脚本。旧桌面 GUI（`gui_app.py`）、旧 PyQt6/Web 配置生成器（`config_builder.py`、`config_web.py`）和 PC 监控面板（`pc_console_gui.py`）已移入 `archive/`，对应旧启动器位于 `launchers/旧/`。

### 3.1 桌面 GUI（已归档）

原入口 `gui_app.py` 已移入 `archive/gui_app.py`，启动器 `launchers/旧/start_gui_desktop.bat`。桌面场景请使用 Light 版三栏桌面 GUI（见 3.3）。

### 3.2 移动布局 GUI（已归档）

原入口 `gui_app_mobile.py` 仍保留在根目录（Light GUI 继承其业务逻辑），但独立启动器 `launchers/旧/start_gui_mobile.bat` 已弃用。

```powershell
cd /d <仓库根目录>
python gui_app_mobile.py   # 仍可直接运行，但无专门启动器
```

### 3.3 Light 版三栏桌面 GUI

```powershell
launchers\start_gui_light.bat
```

等价于:

```powershell
cd /d <仓库根目录>
python gui_app_light.py
```

`gui_app_light.py` 是浅色三栏桌面界面（默认浅色主题，顶栏「深色」按钮可切换浅色/深色主题，选择持久化到 `gui_config.json` 的 `ui_theme`）。它继承 `gui_app_mobile.py` 的 `AutoClipGUI`，复用原有任务线程、按钮事件、B 站扫码/Cookie、素材转换、批量投稿、日志和配置读写逻辑，只替换布局与视觉呈现。

界面默认最大化启动；还原为普通窗口时默认尺寸为 `1440 x 860`，最小尺寸 `1024 x 620`，步骤页在小窗口下自动出滚动条。布局由三栏 `QSplitter` + 底部全宽日志抽屉组成:

- 左栏: 可折叠 7 步流程导航。
- 中栏: 路径浏览、核心操作、平台配置分组工具栏 + 当前步骤设置卡片 + 底部素材文件列表。
- 右栏: 实时运行状态、进度条、当前文件、待处理/已完成/总数统计。
- 底部: 全宽执行日志抽屉（默认展开，可收起）。

### 3.4 Web 控制台

原启动器 `launchers/旧/start_web.bat` 已弃用。直接运行:

```powershell
python web_console.py --port 5000
```

默认端口是 `5000`:

```text
http://localhost:5000
http://TAILSCALE_IP:5000
```

### 3.5 Light 版 Web 控制台

```powershell
launchers\start_web_light.bat
```

默认端口是 `5001`，用于避免和原 Web 控制台默认 `5000` 冲突:

```text
http://localhost:5001
http://TAILSCALE_IP:5001
```

等价于:

```powershell
cd /d <仓库根目录>
python web_console_light.py --port 5001
```

`web_console_light.py` 复用 `web_console.py` 的后端、REST API、SSE、文件安全检查、流水线控制和投稿逻辑，只替换嵌入式前端 HTML 的浅色主题与部分交互。

Light Web 的主要差异:

- 全局浅色办公风格，白底卡片、浅灰边框、柔和蓝色主按钮。
- 投稿界面新增「全选」「反选」按钮，并显示 `已选 / 可选` 数量。
- 已投稿成功的条目不会被全选或反选选中，避免重复投稿。
- 投稿卡片按 `upload_status` 显示状态色条、状态徽标、投稿次数、BV 号、失败原因和失败项重试入口。
- 日志区支持全部/错误/警告/成功筛选，并显示最近一条错误提示。
- 一键流转从投稿页真实控件 `up-streamer` / `up-folder` 读取目录选择；Light 包装层不维护独立目录状态。
- 文本、图片、视频预览统一使用浅色模态框，不再直接显示为浏览器黑底或深色代码块。

### 3.6 命令行流水线

```powershell
python core/auto_pipeline.py -i "<录播根目录>\岁己SUI\2026-06-26 高手小鸟玩空洞骑士！"
```

常用参数:

```powershell
# 只跑指定步骤
python core/auto_pipeline.py -i "录播目录" --start 5 --stop 6

# 生成产物后执行投稿
python core/auto_pipeline.py -i "录播目录" --upload

# 仅检查将执行的步骤
python core/auto_pipeline.py -i "录播目录" --dry-run

# 指定主播配置文件
python core/auto_pipeline.py -i "录播目录" -c "configs\streamers\岁己SUI.json"
```

### 3.7 录播姬开播/下播通知桥接

该功能用于承接 BililiveRecorder 的 Webhook v2 事件，并通过 Server酱发送手机通知。

推荐入口:

```powershell
launchers\start_bilirecorder_webhook.bat
```

该启动器只启动通知桥接服务，不再启动录播姬本体。服务启动时会自动执行一次 Cookie 登录态巡检，之后按配置定时巡检。

等价于:

```powershell
cd /d <仓库根目录>
python bilirecorder_webhook_server.py --host 127.0.0.1 --port 18010
```

录播姬 Webhook 配置:

| 配置项 | 填写内容 |
| --- | --- |
| `Webhook v2` | `http://127.0.0.1:18010/webhook/mikufansrecorder` |
| `Webhook v1` | 留空 |

Webhook 路径由 `configs/serverchan_config.json` 的 `webhook_path` 控制，默认值为 `/webhook/mikufansrecorder`。如果以后要换接口路径，只需要修改该配置项并同步录播姬的 Webhook v2 URL；临时启动也可以使用 `--webhook-path` 参数覆盖。

默认推送事件:

| 录播姬事件 | 通知含义 |
| --- | --- |
| `StreamStarted` | 主播开播 |
| `StreamEnded` | 主播下播 |
| `SessionEnded` | 本次录制结束 |

`FileClosed`、`SessionStarted` 等事件会被服务接收并写入 `bilirecorder_webhook_events.jsonl`，但默认不推送，避免录制分段过多时手机通知过载。

### 3.8 录播姬 Cookie 登录态巡检

通知桥接服务同时带有录播姬 Cookie 登录态巡检。默认策略是检查、备份和提醒，不自动修改录播姬配置；也支持“受控自动更新”，需要手动配置新 Cookie 来源后再开启。

默认配置:

| 配置项 | 默认值 |
| --- | --- |
| 录播姬配置文件 | `<录播根目录>\config.json` |
| 启动时检查 | 开启 |
| 定时检查间隔 | 21600 秒，即 6 小时 |
| 失效提醒冷却 | 43200 秒，即 12 小时 |
| Cookie 配置备份目录 | `backups\bilirecorder_cookie` |
| 登录态接口 | `https://api.bilibili.com/x/web-interface/nav` |
| AutoClip 凭据刷新同步 | 默认开启 |
| AutoClip 凭据文件 | `configs\gui_config.json` |
| 手动 Cookie 来源兜底 | 默认关闭 |
| 手动 Cookie 来源文件 | `configs\bilirecorder_cookie_source.txt` |

手动检查:

```powershell
launchers\旧\check_bilirecorder_cookie.bat
```

或:

```powershell
cd /d <仓库根目录>
python bilirecorder_webhook_server.py --check-cookie-once
```

如果检查失败，可手动触发 Server酱提醒:

```powershell
python bilirecorder_webhook_server.py --check-cookie-once --notify-cookie-check
```

检查结果会写入:

```text
bilirecorder_cookie_status.jsonl
```

当检测到 Cookie 变化时，会备份完整录播姬配置文件到 `backups\bilirecorder_cookie`。备份文件包含 Cookie，属于敏感文件，不要上传或外发。

录播姬 Cookie 失效时的自动处理顺序:

1. 调用 `utils.bilibili_login.check_and_refresh_credential()` 检查 AutoClip 的 B 站凭据。
2. 如果 AutoClip 凭据可用或已通过 `ac_time_value` 刷新成功，则将 `SESSDATA`、`bili_jct`、`DedeUserID`、`buvid3` 合并写回录播姬配置。
3. 写回前会先用 B 站登录态接口验证合并后的 Cookie。
4. 验证通过后备份录播姬配置，再写入新 Cookie，并通过 Server酱通知。
5. 如果 AutoClip 凭据也失效，才进入手动 Cookie 来源兜底逻辑。

手动 Cookie 来源兜底开启方式:

1. 复制 `configs\bilirecorder_cookie_source.example.txt` 为 `configs\bilirecorder_cookie_source.txt`。
2. 将新的完整 B 站 Cookie 写入 `bilirecorder_cookie_source.txt`。
3. 在 `configs\serverchan_config.json` 中将 `cookie_monitor.auto_update` 改为 `true`。

自动写回成功后会发送 Server酱通知。如果录播姬已经打开，建议重启录播姬让新 Cookie 完全生效。

不建议脚本直接从浏览器数据库里自动偷取或解密 Cookie；这类方式容易被浏览器加密、权限和 B 站风控影响，稳定性差，也不方便排查。

---

## 4. 核心流水线

主入口: `core/auto_pipeline.py`

流水线按步骤执行:

| 步骤 | 名称 | 入口 | 主要输出 |
| --- | --- | --- | --- |
| 1 | 准备录播文件 | `core/convert_flv_to_mp4.py` / `core/convert_portrait_to_landscape.py` / `core/burn_danmu_video.py` / `core/convert_danmaku_xml.py` | 同名 `.mp4`，可选横屏化 MP4，可选 `-弹幕版.mp4`，可选同名 `.ass`（弹幕分析源） |
| 2 | 字幕识别 | `core/generate_subtitles.py` | `.srt` |
| 3 | 高光分析 | `core/generate_clips_combined.py` | `Data_source.txt` |
| 4 | 视频切片 | `core/generate_clips.py` | 切片 `.mp4` |
| 5 | 字幕烧录 | `core/burn_subtitles.py` | `_subbed.mp4` |
| 6 | 封面生成 | `core/generate_covers.py` / `generate_covers_new.py` | `.jpg` / `.png` |
| 7 | 投稿准备 | `core/auto_pipeline.py` | 投稿清单 |
| upload | 批量投稿 | `utils/bilibili_uploader.py` | BVID / 投稿结果 |

`auto_pipeline.py` 会:

1. 从输入目录路径推断主播名。
2. 加载 `configs/streamers/{主播}.json`。
3. 步骤1按配置拆成四段: 1-1 FLV 转 MP4，1-2 竖屏 MP4 转横屏 1080p，1-3 同名 MP4/XML 弹幕压制，1-4 弹幕 XML→ASS 前置转换（默认开启，失败不阻断）。
4. 根据配置拼接各步骤命令。
5. 对步骤 4、5、6 做产物完整性检查。
6. 缺少切片、烧录视频或封面时最多重试。
7. 生成投稿清单，等待确认后再投稿。

> 步骤5（字幕烧录）是可选项: 主播配置 `step5_burn.enabled=false` 时跳过（命令行、Web、GUI 一致）。跳过时步骤6封面和步骤7清单自动改用原版切片；GUI 面板的「启用字幕烧录」复选框仅在手动修改与主播配置不一致时覆盖配置。

### 4.1 步骤1-1: FLV 转 MP4

入口: `core/convert_flv_to_mp4.py`

用途:

- 自动检测录播目录下的 `.flv` 文件。
- 使用 FFmpeg stream copy 转成同名 `.mp4`。
- 不重编码，速度快，画质无损。
- 转换成功后删除对应源 `.flv`，只保留 `.mp4`。
- 如果同名 `.mp4` 已存在，默认跳过转换，并在确认 MP4 文件可用后删除对应源 `.flv`。
- 正常执行时只输出发现数量、当前文件、输出路径、删除结果；FFmpeg 详细日志仅在失败时截取显示。

核心命令:

```powershell
ffmpeg -hide_banner -n -i "input.flv" -c copy -movflags +faststart "input.tmp.mp4"
```

转换成功后会将临时文件替换为最终同名 `.mp4`。脚本确认 MP4 文件存在且大小大于 0 后，才会删除源 FLV。如果需要覆盖已有 MP4，可通过配置或脚本参数启用 `--overwrite`；如果需要排错时保留源文件，可追加 `--keep-source`。

命令行:

```powershell
python core/convert_flv_to_mp4.py -i "录播目录"
python core/convert_flv_to_mp4.py -i "录播目录" --recursive
python core/convert_flv_to_mp4.py -i "录播目录" --overwrite
python core/convert_flv_to_mp4.py -i "录播目录" --keep-source
```

  主播配置可选字段:

  ```json
  {
    "step1_prepare": {
      "convert_flv": true,
      "recursive": false,
      "overwrite": false,
      "keep_flv_source": false
    }
  }
  ```

字段说明:

| 字段 | 含义 |
| --- | --- |
| `convert_flv` | 是否执行 FLV 转 MP4；旧配置未填写时默认执行 |
| `recursive` | 递归扫描子目录 |
| `overwrite` | FLV 转 MP4 时覆盖已有 MP4 |
| `keep_flv_source` / `keep_source` | 转换成功后保留源 FLV |

### 4.2 步骤1-2: 竖屏转横屏

入口: `core/convert_portrait_to_landscape.py`

用途:

- 既可独立运行，也可通过主播配置 `step1_prepare.portrait_to_landscape=true` 接入一键流转步骤1。
- 扫描 `.mp4`，只处理显示宽度小于高度的竖屏视频；横屏或方形视频自动跳过。
- 默认输出 `1920x1080` 横屏画布: 背景使用同一视频画面放大填满并模糊，前景竖屏画面等比缩放到画布高度后居中叠放。
- 视频编码使用 `h264_nvenc`，`-preset p1` + `-cq 23`（2026-09-10 起；此前为 p5，实测 2.65 倍提速，VMAF 与 p5 差 0.06 分不可感知，代价是文件大约 +31%）。
- 为了后续弹幕压制仍能匹配同名 XML，转换成功后横屏版会替换原 MP4 文件名。
- 默认把原竖屏 MP4 备份为 `原名-竖屏原版.mp4`；设置 `delete_portrait_source=true` 后不保留备份。
- 如果已存在 `原名-竖屏原版.mp4`，默认认为已经处理过并跳过；设置 `portrait_overwrite=true` 后可重新处理。

命令行:

```powershell
python core/convert_portrait_to_landscape.py -i "录播目录"
python core/convert_portrait_to_landscape.py -i "录播目录" --recursive
python core/convert_portrait_to_landscape.py -i "录播目录" --width 1920 --height 1080 --blur 20
python core/convert_portrait_to_landscape.py -i "录播目录" --overwrite
python core/convert_portrait_to_landscape.py -i "录播目录" --delete-source
```

主播配置可选字段:

```json
{
  "step1_prepare": {
    "portrait_to_landscape": true,
    "portrait_overwrite": false,
    "delete_portrait_source": false,
    "landscape_width": 1920,
    "landscape_height": 1080,
    "landscape_blur": 20
  }
}
```

### 4.3 步骤1-3: 弹幕压制

入口: `core/burn_danmu_video.py`

用途:

- 既可独立运行，也可通过主播配置 `step1_prepare.burn_danmu=true` 接入一键流转步骤1。
- 匹配同目录下同名 `.mp4` + `.xml` 文件。
- 如果没有符合条件的同名 `.mp4` + `.xml`，直接跳过。
- 如果同名 `原名-弹幕版.mp4` 已存在，默认跳过，避免重复压制。
- 使用 `DanmakuFactory.exe` 将 XML 转为临时 ASS。
- 使用 FFmpeg 将 ASS 烧录进 MP4。
- 视频编码使用 `h264_nvenc`，`-preset p1` + `-cq 23`（2026-09-10 起；与步骤1-2 同步提速）。启动时会探测 `h264_nvenc` 是否可用，不可用时回退 `libx264 -preset veryfast -crf 23`（软编约慢 2 倍，但不会整批失败）。
- 多文件默认 2 路并行（`--jobs`，2026-09-20 起）。瓶颈是 libass 的逐帧渲染：它是单线程的，一路压制只占满一个核，因此并行是唯一有效的提速手段。实测 2 个 5.5 分钟 1080p60 片段：串行 131s → 2 路 64-72s（约 1.9x），占用约 7 个逻辑核。再往上加路数收益递减，因为 NVENC 编码器是共享的硬件瓶颈。
- 压制子进程以低于正常优先级运行（`--priority below`），Windows 会把 CPU 优先给前台程序；机器空闲时速度不受影响。
- `--hwaccel` 可把解码交给 GPU：实测 CPU 从约 7 核降到约 4 核，但墙钟时间慢约 25%，默认关闭。
- 使用 `ffprobe` 读取源 MP4 的显示分辨率，按实际宽高生成 ASS；例如竖屏 `720x1280` 会传给 DanmakuFactory `--resolution 720x1280`，不会强制套用 `1920x1080`。
- 如果视频带旋转元数据，会按显示方向交换宽高；读取失败时才回退 `1920x1080`。
- 弹幕版命名规则与 biliLive-tools 一致: `原名-弹幕版.mp4`；如果文件已存在，默认跳过，传入 `--overwrite` 时才重新压制覆盖。
- 正常执行时只输出匹配数量、跳过/压制/分辨率/输出结果；DanmakuFactory 和 FFmpeg 详细日志仅在失败时截取显示。

命令行:

```powershell
python core/burn_danmu_video.py -i "录播目录"
python core/burn_danmu_video.py -i "录播目录" --recursive
python core/burn_danmu_video.py -i "录播目录" --overwrite  # 强制重压已有弹幕版
python core/burn_danmu_video.py -i "录播目录" --keep-ass
python core/burn_danmu_video.py -i "录播目录" --jobs 1     # 退回串行（占用最低）
python core/burn_danmu_video.py -i "录播目录" --hwaccel    # GPU 解码，省 CPU 但慢约 25%
```

主播配置可选字段:

```json
{
  "step1_prepare": {
    "burn_danmu": true,
    "burn_danmu_overwrite": false,
    "keep_danmu_ass": false,
    "burn_danmu_jobs": 2
  }
}
```

字段说明:

| 字段 | 含义 |
| --- | --- |
| `burn_danmu` | 是否执行同名 MP4 + XML 弹幕压制 |
| `burn_danmu_overwrite` / `overwrite_danmu` | 弹幕版已存在时强制重压 |
| `keep_danmu_ass` | 保留 DanmakuFactory 生成的 ASS 文件，便于排错 |

### 4.4 步骤1-4: 弹幕 XML→ASS 前置转换

入口: `core/convert_danmaku_xml.py`

用途:

- 既可独立运行，也可通过主播配置 `step1_prepare.convert_danmaku_xml=true` 接入一键流转步骤1（默认开启，未配置时也执行）。
- 只处理与视频同名、且缺少同名 `.ass` 的 XML 弹幕；已有同名 ASS 时跳过，幂等可重复运行。
- 复用 `core/burn_danmu_video.py` 的 DanmakuFactory 调用，把 XML 转成同名 `.ass`，作为步骤3弹幕热度/双保险/多保险分析的弹幕源。
- 尽力而为语义: DanmakuFactory 缺失或单个文件转换失败只打印警告，不阻断流水线（进程仍以 0 退出）；仅目录不存在时返回 1。
- XML 源文件只读，转换只产出 ASS，任何情况下不删除、不修改 XML。
- 步骤3高光分析在需要弹幕源时（`--method danmu`/`combined`，或 `multi` 勾选 `danmu`）也会在分析前调用同一转换逻辑（`ensure_danmaku_ass_files()`），与流水线共用 `core/convert_danmaku_xml.py`。

命令行:

```powershell
python core/convert_danmaku_xml.py -i "录播目录"
python core/convert_danmaku_xml.py -i "录播目录" --recursive
```

主播配置可选字段:

```json
{
  "step1_prepare": {
    "convert_danmaku_xml": true
  }
}
```

字段说明:

| 字段 | 含义 |
| --- | --- |
| `convert_danmaku_xml` | 是否执行弹幕 XML→ASS 前置转换；未填写时默认执行 |

### 4.5 视频版本优先级

入口: `utils/video_filter.py`

下游处理同一基础视频时使用统一优先级:

```text
-弹幕版  >  普通主视频/横屏版  >  -竖屏原版
```

覆盖范围:

- 步骤2字幕生成。
- 步骤3高光匹配。
- 步骤4切片脚本 `generate_clips.py`。
- 步骤5字幕烧录。
- 步骤6封面生成。
- 步骤7投稿清单。

`_subbed.mp4` 成片也遵循同样规则，例如同目录存在 `xxx_subbed.mp4` 和 `xxx-弹幕版_subbed.mp4` 时，投稿清单优先使用 `xxx-弹幕版_subbed.mp4`。`-竖屏原版.mp4` 只作为兜底，避免竖屏备份被误当主视频继续处理。

匹配文件:

```text
2026-07-04 直播.mp4
2026-07-04 直播.xml
```

输出:

```text
2026-07-04 直播-弹幕版.mp4
```

压制参数参考 biliLive-tools:

1. 使用 `DanmakuFactory.exe` 将 XML 转为临时 ASS。
2. 使用 FFmpeg 将 ASS 烧录进 MP4。
3. ASS 渲染仍由 FFmpeg `subtitles` 滤镜在 CPU 上完成；视频编码使用 `h264_nvenc`，音频 `copy`。
4. 弹幕默认样式参考 biliLive-tools 默认配置: 微软雅黑、40px、粗体、文字/描边不透明度 75%、描边 1、阴影 0。
5. 弹幕布局为不重叠、滚动区域 30%、全弹幕区域 100%、滚动 12 秒、固定 5 秒、时间偏移 1 秒。
6. 默认不显示用户名、保留屏蔽弹幕、屏蔽底部固定弹幕。
7. 礼物栏默认显示，宽度最多 500px，高度跟随源视频高度，例如 `500x1280`，位置 `20x0`，文字 38px，持续 10 秒，最小礼物价值 10 RMB。

---

## 5. ASR 字幕系统

入口: `core/generate_subtitles.py`

支持引擎:

| 引擎 | 参数值 | 说明 |
| --- | --- | --- |
| FunASR 便携版 | `portable` | 使用项目内便携 FunASR worker，适合本项目默认部署；同目录多视频走批量模式，模型只加载一次 |
| FunASR 直连 | `funasr` | 直接在当前 Python 环境初始化 FunASR |
| Whisper | `whisper` | 使用 `utils/whisper_transcriber.py` |
| Belle | `belle` | 使用 `utils/belle_transcriber.py`，面向 Belle Whisper CTranslate2 模型 |
| Qwen3-ASR | `qwen3` | 转写模型 `Qwen3-ASR-1.7B`（默认）或 `Qwen3-ASR-0.6B` + ForcedAligner-0.6B，运行于项目内独立 venv `qwen3_env/`，详见下文 |

常用命令:

```powershell
python core/generate_subtitles.py -i "录播目录" --engine portable --funasr-model sensevoice
python core/generate_subtitles.py -i "录播目录" --engine whisper --model "models\whisper\large-v3-turbo.pt" --language zh
python core/generate_subtitles.py -i "录播目录" --engine belle
python core/generate_subtitles.py -i "录播目录" --engine qwen3 --language zh
```

关键参数:

| 参数 | 用途 |
| --- | --- |
| `--engine` | ASR 引擎，取值 `portable`、`whisper`、`funasr`、`belle`、`qwen3` |
| `--funasr-model` | FunASR 模型，如 `sensevoice` / `SenseVoiceSmall` |
| `--qwen3-model` | Qwen3 转写模型，`Qwen3-ASR-1.7B`（默认）/ `Qwen3-ASR-0.6B`，仅 qwen3 引擎有效 |
| `--speaker` | 启用说话人分离 |
| `--no-punc` | 禁用标点恢复 |
| `--hotword` | 传入热词 |
| `--auto-lang` | Whisper 自动识别语言 |

相关模块:

- `utils/funasr_transcriber.py`
- `utils/whisper_transcriber.py`
- `utils/belle_transcriber.py`
- `utils/qwen3_asr_transcriber.py`
- `utils/ASRCorrector.py`
- `configs/asr_dict.txt`

### 5.1 Qwen3-ASR 引擎（qwen3）

架构与 FunASR 便携版同构：主环境不安装任何新依赖，由 `utils/qwen3_asr_transcriber.py` 拉起项目内虚拟环境 `qwen3_env/`（独立 Python venv）中的 `utils/_qwen3_asr_worker.py` 子进程完成推理。

- 模型（本地目录，均离线加载）:
  - `models/Qwen3-ASR-1.7B` — 转写模型（约 4.7GB，bf16 显存约 4.7GB）
  - `models/Qwen3-ASR-0.6B` — 轻量转写模型（约 1.9GB，显存占用约 1.7B 的 40%；`--qwen3-model Qwen3-ASR-0.6B` 或步骤5 `--asr-model Qwen3-ASR-0.6B` 启用；GUI 步骤2/步骤5 页选 Qwen3 引擎后出现模型下拉；主播配置 `step2_subtitle.model` / `step5_burn.model` 填 `Qwen3-ASR-0.6B` 即可切换）
  - `models/Qwen3-ForcedAligner-0.6B` — 强制对齐模型，输出字级时间戳（约 1.3GB）
- venv 依赖: `torch`（CUDA 12.6 构建）、`qwen-asr`（自带 transformers 后端，pypi `qwen-asr`）
- 流程: FFmpeg 提取 16k 单声道 WAV → 低能量点切 ≤180s 块（对齐器单次上限）→ 阶段 A 转写 → 卸载转写模型 → 阶段 B 逐块对齐 → 按标点/静默/长度把字级时间戳合并为句级 SRT
- 两阶段分别加载/卸载模型，峰值显存 = max(约 4.7G, 1.3G)，适配 8G 显卡
- worker 对输入/输出路径做白名单校验（项目根、`<录播根目录>`、环境变量 `AUTOCLIP_ALLOWED_MEDIA_ROOTS` 扩展，分号分隔）
- 步骤5 字幕烧录同样支持 `--asr-engine qwen3`（对切片重新识别后再烧录）
- 语言代码映射: `zh→Chinese`、`en→English`、`ja→Japanese`、`ko→Korean`、`yue→Cantonese`、`auto→自动检测`

---

## 6. 高光检测系统

入口: `core/generate_clips_combined.py`

支持模式:

| 模式 | 参数值 | 说明 |
| --- | --- | --- |
| 弹幕热度 | `danmu` | 从弹幕密度、关键词和权重中找高能段 |
| LLM 语义 | `llm` | 将字幕送入 LLM，输出标题、摘要、封面文案和时间段 |
| 双保险 | `combined` | 弹幕 + LLM 融合 |
| 情绪驱动 | `emotion` | 使用 emotion2vec 分析结果辅助找高光 |
| 多保险融合 | `multi` | 可组合 `danmu,llm,emotion`，默认 `danmu,llm` |

常用命令:

```powershell
python core/generate_clips_combined.py -i "录播目录" --method llm -s "岁己SUI"
python core/generate_clips_combined.py -i "录播目录" --method combined -s "岁己SUI"
python core/generate_clips_combined.py -i "录播目录" --method multi --multi-sources danmu,llm,emotion -s "岁己SUI"
```

高光结果统一写入 `Data_source.txt`，片段格式大致包含:

```json
{
  "segment": "01-04-03",
  "timestamp": "00:02:23-00:03:22",
  "real_timestamp": "01:06:26-01:07:25",
  "title": "切片标题",
  "summary": "内容摘要",
  "cover_text_1": "封面主文案",
  "cover_text_2": "封面副文案",
  "highlight_reason": "入选理由",
  "sources": ["llm", "danmu"]
}
```

时间字段含义:

| 字段 | 含义 |
| --- | --- |
| `segment` | 当前源视频的完整文件名（不含扩展名），如 `01-04-03 标题` 或 `分集_2026-08-09_【直播回放】...`；供切片匹配和封面匹配使用 |
| `timestamp` | 片段在该源视频内的时间范围，如 `00:02:23-00:03:22` |
| `real_timestamp` | `segment + timestamp` 换算出的现实时间范围，如 `01:06:26-01:07:25` |

`real_timestamp` 在高光结果保存并完成气口缓冲后补齐，确保它与最终 `timestamp` 对齐。后续切片仍使用 `timestamp`，投稿清单生成时会把 `real_timestamp` 写入视频简介。

关于 `segment` 的补充说明:

- LLM 分析输出的 `segment` 一律使用当前分析的视频完整文件名（不含扩展名），保证步骤4切片匹配（`generate_clips.py` 按 `{segment}*` 前缀匹配）和步骤6封面匹配不受文件名格式影响。
- `real_timestamp` 只从文件名**开头**的标准时间戳（如 `01-04-03` 或 `01:04:03`）解析；新格式命名（如 `分集_2026-08-09_...`）不含前缀时间戳时不会生成 `real_timestamp`，也不会把文件名中的日期误判为时间。
- LLM 输出的标题/封面文字中的英文单引号（`'`）会被自动清洗为中文右引号（`’`），避免破坏 FFmpeg/ASS 脚本。

LLM 调用逻辑位于 `call_llm_api()`，使用兼容 OpenAI Chat Completions 的接口。默认模型随供应商由 `utils/llm_config.py` 的 `LLM_PROVIDER_MODELS` 决定（默认硅基流动对应 `deepseek-ai/DeepSeek-V4-Flash`，见下节）。

### 6.x LLM 供应商切换（五家供应商）

LLM 供应商与端点由 `utils/llm_config.py` 统一管理，`configs/gui_config.json` 的 `llm_provider` 字段决定：

| `llm_provider` | 端点（base_url） | 默认模型 |
| --- | --- | --- |
| `siliconflow`（默认） | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V4-Flash` |
| `deepseek` | `https://api.deepseek.com/v1` | `deepseek-v4-flash` |
| `opencode-go` | `https://opencode.ai/zen/go/v1` | `deepseek-v4-flash` |
| `zhipu` | `https://open.bigmodel.cn/api/paas/v4` | `glm-5.3-flash` |
| `zhipu-coding` | `https://open.bigmodel.cn/api/coding/paas/v4` | `glm-5.3-flash`（Coding 套餐仅支持 `glm-5.3-flash` / `glm-5.3`，其他模型名会被拒绝） |

- GUI 顶部「高级」面板的「供应商」下拉框切换此字段，切换时模型候选列表按新供应商重建并切换到该供应商的默认模型（当前自定义模型名也会被替换）；`save_config`/`apply_config` 会持久化 `llm_provider` 和 `llm_api_keys`。
- 也可用 `llm_base_url` 直接指定完整端点（优先级高于 `llm_provider`），用于自定义网关。
- CLI 一致性：命令行流水线（`auto_pipeline.py`）、独立脚本（`core/generate_clips_combined.py`、`analysis/generate_clips_by_llm.py`、`analysis/get_data_by_danmu.py`）都通过 `utils.llm_config.resolve_llm_base_url()` 读同一份 `gui_config.json`，或接受 `--base-url` 显式覆盖；GUI 一键流转与命令行因此保持同一供应商配置。
- API Key 按供应商独立存储在 `llm_api_keys[provider]`，各平台 Key 不通用，切换供应商时需填入对应平台的 Key。旧字段 `global_api_key` 仅作历史兼容，只被视为硅基流动的 Key。

---

## 7. 字幕烧录与封面生成

### 7.1 字幕烧录

入口: `core/burn_subtitles.py`

流程:

1. 读取切片视频对应的 `.srt`。
2. 根据视频分辨率和样式配置生成 `.ass`。
3. 调用 FFmpeg `subtitles` 滤镜烧录。
4. 自动检测硬件编码器，如 NVENC、AMF、QSV。
5. 输出 `_subbed.mp4`。

关键配置来自主播 JSON 的 `step5_burn`:

```json
{
  "font": "MaoKenShiJinHei-2.ttf",
  "font_size": 80,
  "outline": 5,
  "shadow": true,
  "margin_v": 100,
  "color": "&H00FFFFFF",
  "outline_color": "&H003718ff"
}
```

### 7.2 封面生成

入口:

- `core/generate_covers.py`
- `core/generate_covers_new.py`

封面生成流程:

1. 根据片段时间从视频抽帧。
2. 裁剪为 16:9。
3. 叠加标题或封面文案。
4. 应用描边、阴影、速度线等素材。
5. 输出到 `{input_dir}\切片\封面`。

抽帧来源:

- 封面背景帧**优先从同名原视频（未烧录字幕的切片）抽取**，避免封面带上烧录的字幕；原视频不存在时才回退到 `_subbed.mp4` 本身。
- 视频匹配先按 `[segment] 标题` 精确匹配（标题来自 `Data_source.txt` 的 `title`），避免多个同源切片错误匹配到同一个视频文件；匹配不到再回退前缀模糊匹配。
- 抽帧位置固定取切片正中间帧（2026-09-10 起，不再随机；同一输入重新生成画面完全一致）。抽帧源是原始完整录播（时长超过片段时间范围 30 秒以上）时，按 `Data_source.txt` 的绝对时间取切片区间中点；抽帧源是切好的切片文件时取文件正中间。

性能说明（2026-09-10）:

- 文字描边改为在文字包围盒附近的小图层上膨胀再贴回，输出与旧实现逐像素一致，单张封面生成约 3~5 倍提速。
- `ffprobe` 探测结果按视频路径缓存，同一视频只探测一次。

配置来自主播 JSON 的 `step6_cover`:

```json
{
  "style": "random_1_4",
  "font": "MaoKenShiJinHei-2.ttf",
  "output_dir": "{input_dir}/切片/封面",
  "match_subbed_only": true
}
```

---

## 8. B 站投稿模块

相关文件:

- `utils/bilibili_login.py`
- `utils/bilibili_uploader.py`
- `utils/bilibili_meme_search.py`

投稿依赖 `configs/gui_config.json` 中的 `bilibili` 字段:

```json
{
  "bilibili": {
    "sessdata": "...",
    "bili_jct": "...",
    "buvid3": "...",
    "dedeuserid": "...",
    "ac_time_value": "..."
  }
}
```

投稿配置来自主播 JSON 的 `step7_upload`:

```json
{
  "tid": 27,
  "tid_name": "动画·综合",
  "copyright": "original",
  "no_reprint": false,
  "title_rule": "从文件名提取",
  "title_suffix_name": "主播短名",
  "desc_template": "",
  "cover_rule": "{input_dir}/切片/封面/[{timestamp}] {title}.jpg",
  "tags": ["虚拟主播", "ai切片", "岁己SUI"]
}
```

投稿简介生成规则:

- `core/auto_pipeline.py`、`gui_app_mobile.py` 和 `web_console.py` 都会读取 `Data_source.txt`，按切片标题匹配 `summary` 与 `real_timestamp`。
- 如果存在 `real_timestamp`，简介会追加 `直播时间：HH:MM:SS-HH:MM:SS`。
- 如果存在 `summary`，会在时间行后空一行追加摘要。
- 重新生成投稿清单时，应保留原有投稿状态字段，避免覆盖已投稿记忆。

投稿清单生成规则（`run_step7_prepare` / GUI `generate_manifest_from_files`）:

- 切片视频目录**优先扫描 `_subbed.mp4`（已烧录字幕版）**；没有字幕版时**回退使用原版切片 mp4**，清单仍正常生成并提示。
- 弹幕版优先过滤（同基础名存在 `-弹幕版` 时优先使用）。
- 封面按完整文件名前缀匹配（原版与字幕版指向同一封面）。
- 步骤5（字幕烧录）是可选项: 主播配置 `step5_burn.enabled=false` 时一键流转/流水线跳过该步骤；无 `_subbed.mp4` 时步骤6封面和步骤7清单会自动改用原版切片。

Web 控制台提供投稿管理 API:

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/uploads` | 获取投稿清单 |
| GET | `/api/uploads/status` | 获取投稿状态 |
| POST | `/api/uploads/submit` | 批量投稿 |
| POST | `/api/uploads/abort` | 中止投稿 |
| PUT | `/api/uploads/{id}` | 更新单条投稿项 |

### 8.1 批量投稿节流

为降低 B 站批量投稿过多触发 406/限流错误的概率，GUI 和 Web 控制台的批量投稿都采用同一节流策略:

| 场景 | 策略 |
| --- | --- |
| 普通连续投稿 | 单个稿件完成后等待 10 秒 |
| 每批投稿数 | 每 5 个稿件为一批 |
| 批次缓冲 | 每投满 5 个后冷却 5 分钟 |
| 406 限流 | 冷却 5 分钟后重试当前稿件，不前进到下一个 |
| 用户中断 | 未开始的稿件标记为 `skipped`，并写入中断原因 |

实现位置:

- GUI: `gui_app_mobile.py` 的 `_run_batch_upload_inline()`
- Light GUI: `gui_app_light.py` 复用同一 `start_task("batch_upload", ...)` 和 `WorkerThread` 流程
- Web: `web_console.py` 的 `run_upload_batch()`
- Light Web: `web_console_light.py` 复用 `web_console.py` 的 `run_upload_batch()`

### 8.2 投稿记忆

投稿状态写回 `upload_manifest.json`，作为 GUI 和 Web 共同使用的持久化记忆。重新打开 GUI/Web 或重新加载清单时，历史状态会继续显示。

每条投稿项新增字段:

| 字段 | 含义 |
| --- | --- |
| `upload_status` | 投稿状态，取值见下表 |
| `upload_attempts` | 已尝试投稿次数 |
| `bvid` | 投稿成功后的 BV 号 |
| `aid` | 投稿成功后的 AV 号 |
| `last_error` | 最近一次失败或跳过原因 |
| `last_upload_at` | 最近一次成功投稿时间 |
| `last_attempt_at` | 最近一次开始投稿时间 |

`upload_status` 状态说明:

| 状态 | 含义 | 默认选择行为 |
| --- | --- | --- |
| `pending` | 尚未投稿 | 默认选中 |
| `uploading` | 正在投稿 | 默认选中，开始后会刷新 |
| `success` | 已投稿成功 | 默认不选中，避免重复投稿 |
| `failed` | 投稿失败 | 默认选中，方便重试 |
| `skipped` | 用户中断或未投稿 | 默认选中，方便继续 |

示例:

```json
{
  "video_path": "<录播根目录>...\\切片\\视频\\[00-01] 标题_subbed.mp4",
  "title": "标题【岁己SUI】",
  "desc": "...",
  "tags": ["虚拟主播", "ai切片"],
  "tid": 27,
  "copyright": 1,
  "cover_path": "<录播根目录>...\\切片\\封面\\[00-01] 标题.jpg",
  "upload_status": "success",
  "upload_attempts": 1,
  "bvid": "BV...",
  "aid": 123456,
  "last_error": "",
  "last_upload_at": "2026-06-26 18:30:00",
  "last_attempt_at": "2026-06-26 18:20:00"
}
```

### 8.3 GUI/Web 展示规则

GUI 的 `BatchUploadDialog`、Light GUI 的 `LightBatchUploadDialog` 和 Web 投稿卡片都会显示:

- 当前状态: 待投稿、投稿中、已投稿、投稿失败、未投稿/已中断
- 投稿次数
- 成功后的 BV 号
- 失败或中断原因

已成功投稿的条目默认不允许勾选，避免重复投稿。失败、跳过和待投稿条目会默认选中，适合继续重试。

Light Web 投稿界面新增「全选」和「反选」工具栏:

- 「全选」只选择 `pending`、`uploading`、`failed`、`skipped` 等可投稿项。
- 「反选」只在可投稿项范围内反转选择。
- `success` 项不会被批量选择，避免重复投稿。
- 工具栏显示 `已选 / 可选` 数量，提交按钮仍显示当前选中数。

Web 的 `/api/uploads/status` 会返回:

```json
{
  "running": false,
  "total": 10,
  "completed": 5,
  "failed": 1,
  "skipped": 4,
  "current_title": "",
  "current_progress": 0.0,
  "results": []
}
```

### 8.4 失败判定规则

投稿成功必须满足上传接口返回有效 `bvid`。如果接口返回对象但没有有效 BV 号，视为失败，并将错误写入 `last_error`。

失败会明确保留在清单中，不会被当作完成项跳过。406 限流是特殊情况: 当前稿件冷却 5 分钟后重试，不计入失败，也不会切换到下一条。

---

## 9. 配置系统

配置分三层:

| 层级 | 文件 | 用途 |
| --- | --- | --- |
| 全局配置 | `configs/gui_config.json` | 当前输入目录、模型路径、LLM key、B 站凭证、GUI 状态 |
| 主播配置 | `configs/streamers/*.json` | 每个主播的流水线默认参数 |
| Prompt 配置 | `configs/streamer_titles/*`、`configs/prompts/*` | LLM 高光分析提示词（按主播；`prompts/` 为历史主播） |
| Server酱通知 | `configs/serverchan_config.json` | 录播姬 Webhook 桥接服务的 Server酱 SENDKEY、事件白名单、标题模板 |

当前主播配置文件:

- `configs/streamers/四时小路Komichi.json`
- `configs/streamers/多人联动.json`
- `configs/streamers/小松绿Viridis.json`
- `configs/streamers/岁己SUI.json`
- `configs/streamers/枝堇Sumire.json`
- `configs/streamers/栞栞Shiori.json`
- `configs/streamers/灰泽满Hazel.json`

主播配置主要字段:

```json
{
  "streamer_name": "岁己SUI",
  "step2_subtitle": {},
  "step3_highlight": {},
  "step4_clip": {},
  "step5_burn": {},
  "step6_cover": {},
  "step7_upload": {},
  "bilibili_links": {},
  "keywords": []
}
```

`keywords` 是主播梗词的唯一持久化字段。配置生成器和日常 Light GUI 的梗搜索会把 B 站标题分析出的词汇直接合并进 `keywords`，高光分析会通过 `utils/streamer_prompts.py` 把它们追加到 LLM 提示词中；不再保存 `_meme_result` 缓存字段。

安全建议:

- `gui_config.json` 当前可能包含 API key、SESSDATA、bili_jct 等敏感信息。
- `serverchan_config.json` 包含 Server酱 SENDKEY 时也属于敏感配置，不要提交到公开仓库。
- 不要将该文件上传到公开仓库。
- 后续建议拆分为 `gui_config.local.json` 或改用环境变量，例如 `DEEPSEEK_API_KEY`。
- B 站 Cookie 失效后应通过 GUI 或登录工具刷新，不要硬编码到代码文件。

### 9.1 Server酱通知配置

首次使用时复制模板:

```powershell
copy configs\serverchan_config.example.json configs\serverchan_config.json
```

然后把 Server酱 SENDKEY 填入:

```json
{
  "enabled": true,
  "sendkey": "SCTxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "notify_events": ["StreamStarted", "StreamEnded", "SessionEnded"],
  "dedupe_seconds": 90
}
```

也可以不写配置文件，启动前设置环境变量:

```powershell
$env:SERVERCHAN_SENDKEY="SCTxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
python bilirecorder_webhook_server.py
```

服务调用 Server酱 Turbo 接口:

```text
POST https://sctapi.ftqq.com/{SENDKEY}.send
title=...
desp=...
```

如果已有完整自定义地址，可填 `serverchan_url`，此时 `sendkey` 会被忽略。

---

## 10. GUI 与 Web 控制台

### 10.1 PyQt6 GUI

入口:

- `gui_app_mobile.py`（GUI 主体，可独立运行）
- `gui_app_light.py`（Light 版三栏桌面 GUI，推荐入口）
- ~~`gui_app.py`~~（旧桌面壳，已归档至 `archive/gui_app.py`）

主要类:

| 类 | 文件 | 用途 |
| --- | --- | --- |
| `DesktopAutoClipGUI` | `archive/gui_app.py` | 旧桌面端窗口壳（已归档） |
| `AutoClipGUI` | `gui_app_mobile.py` | GUI 主体 |
| `WorkerThread` | `gui_app_mobile.py` | 后台执行流水线命令 |
| `BatchUploadDialog` | `gui_app_mobile.py` | 批量投稿窗口 |
| `StepIndicatorWidget` | `gui_app_mobile.py` | 步骤状态显示 |
| `LightAutoClipGUI` | `gui_app_light.py` | Light 版三栏桌面 GUI |
| `LightBatchUploadDialog` | `gui_app_light.py` | Light 版批量投稿窗口 |
| `LightStepIndicator` | `gui_app_light.py` | Light 版右侧步骤状态栏 |

Light GUI 设计原则:

- 不修改旧 GUI 脚本，不复制业务代码；通过继承 `AutoClipGUI` 复用原有事件、任务线程和配置逻辑。
- 全局样式集中在 `gui_light_theme.py`，包括浅色/深色两套配色（`set_theme_colors()` 切换）、按钮状态、输入框焦点态和进度条。
- 布局采用三栏 `QSplitter`，左栏导航、中栏步骤设置/文件列表、右栏任务状态和底部全宽日志抽屉均可随窗口调整。
- 批量投稿窗口保持原 `upload_manifest.json` 读写和 `start_task("batch_upload", ...)` 流程，仅统一浅色视觉。

配置保存:

- Light GUI 继承 `AutoClipGUI.closeEvent()`，关闭窗口时自动调用 `save_config()`。
- 保存目标是 `configs/gui_config.json`。
- 会保存全局目录、主播、API Key、模型、ASR/字幕/封面/投稿等主要配置。
- 单个步骤临时路径并非全部独立保存；重启后 `apply_config()` 会再执行 `apply_global_config()`，部分步骤路径会按全局目录重新填充。

### 10.2 Web 控制台

入口:

- `web_console.py`
- `web_console_light.py`

Web 控制台使用 `ThreadingHTTPServer`，前端是内嵌 HTML/CSS/vanilla JS，后端提供 REST API 和 SSE 日志流。

#### 多录播根目录

Web 控制台支持多个录播根目录，代码中通过 `INPUT_BASES` 列表配置（当前包含主目录和 NAS 备份目录）:

```python
INPUT_BASE = r"<录播根目录>\AA录播"
INPUT_BASE_2 = r"<录播根目录>\AA录播（nas备份）"
INPUT_BASES = [INPUT_BASE, INPUT_BASE_2]
```

- 所有根目录都加入 `ALLOWED_ROOTS` 路径白名单，`_safe_path()` 同时校验两者。
- 投稿页新增「录播根目录」下拉框: 可选 `全部根目录` 或具体根目录；选择具体根目录时 `/api/folders?base=...` 只返回该根目录下的录播文件夹。
- 选择「全部根目录」时，文件夹选项带 `[目录名]` 前缀标注来源，同名文件夹可区分。
- 文件管理页导航栏新增根目录快捷切换下拉框，浏览时自动同步当前所在根目录。
- `/api/pipeline/start` 支持前端传入精确 `dir`（完整路径），避免多根目录下同名主播/文件夹歧义；未传时按 `INPUT_BASES` 顺序查找 `streamer/folder`。
- `/api/streamers` 会过滤掉在所有根目录下都没有实际录播文件夹的主播（仅存配置的旧主播不会显示，配置本身保留，出现录播目录后自动恢复）。

`web_console_light.py` 是浅色前端包装入口:

- 直接导入 `web_console.py`，复用后端常量、`Handler`、API、SSE、投稿线程和流水线控制。
- 在启动前替换 `base.HTML`，注入 Light CSS、投稿全选/反选工具栏和浅色文件预览逻辑。
- 默认通过 `launchers/start_web_light.bat` 使用 `5001` 端口，避免与原控制台 `5000` 冲突。
- 多根目录支持随 `base.HTML` 自动继承，无需额外改动。

主要能力:

- 浏览录播目录
- 预览文本、图片、视频信息
- 上传/下载文件
- 启动、中止、暂停、恢复流水线
- 查看实时日志
- 管理投稿清单
- 批量投稿
- 持久化上次会话

流水线中断:

- `/api/pipeline/abort` 会设置 `pipeline_abort_requested`，并在 Windows 下调用 `taskkill /F /T /PID` 终止 `auto_pipeline.py` 及其子进程树。
- 日志读取循环会检查中断请求，避免前端状态已中断但后台子进程继续运行。
- 如果没有正在运行的流水线，接口返回 `ok` 和提示消息，不保留中断请求状态。
- 前端会读取接口返回值；中断失败时显示后端错误，不再无条件提示“已发送中断信号”。

Light Web 额外能力:

- 投稿页支持「全选」「反选」，并跳过 `success` 状态项。
- 投稿页空态、未选目录、未选步骤、预览失败等情况会显示页面内提示。
- 日志区支持按级别筛选，错误日志会在顶部保留最近错误摘要。
- 文本、图片、视频预览统一在浅色 modal 中显示。
- 文本预览使用浅灰白代码块，避免浏览器默认黑底导致内容不可读。
- 桌面宽屏下底部导航自动变为左侧导航，移动端仍使用底部 tab。

关键 API:

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/files` | 文件列表 |
| GET | `/api/files/download` | 文件下载 |
| GET | `/api/files/preview` | 文件预览 |
| POST | `/api/files/upload` | 文件上传 |
| GET | `/api/pipeline/status` | 流水线状态 |
| POST | `/api/pipeline/start` | 启动流水线 |
| POST | `/api/pipeline/abort` | 中止流水线 |
| POST | `/api/pipeline/pause` | 暂停流水线 |
| POST | `/api/pipeline/resume` | 恢复流水线 |
| GET | `/api/session` | 当前会话 |
| GET | `/api/streamers` | 主播列表（含 `bases`，已过滤无录播目录的主播） |
| GET | `/api/folders` | 录播目录列表（支持 `base` 参数按根目录过滤） |
| GET | `/api/video/info` | 视频信息 |
| GET | `/api/stream` | SSE 日志流 |

路径安全:

- Web 控制台通过 `_safe_path()` 限制可访问根目录。
- 当前白名单包括全部录播根目录（`INPUT_BASES`）和项目根目录。
- 新增文件访问 API 时必须复用该安全检查。

### 10.3 PC 监控面板（已归档）

原入口 `pc_console_gui.py` 已移入 `archive/pc_console_gui.py`。

原用途:

- 读取 `console_activity.jsonl`
- 显示手机端/Web 端指令
- 辅助监控远程控制行为

还原方法见 `archive/README.md`。

---

## 11. 环境与依赖

### 11.1 Python

项目可以使用系统 Python，也包含 FunASR 便携运行时:

```text
funasr\runtime\python
```

基础依赖列表位于:

```text
configs\requirements.txt
```

当前基础依赖:

```text
fonttools==4.60.1
Pillow==12.0.0
Requests==2.32.5
openai-whisper
numpy
torch
torchaudio
```

实际运行还会依赖:

- `PyQt6`
- `bilibili-api`
- `funasr`
- `modelscope`
- `ffmpeg` / `ffprobe`
- Whisper、Belle、FunASR 模型文件

### 11.2 FFmpeg

项目内已适配本机 FFmpeg 目录:

```text
AUTOCLIP_FFMPEG_BIN 指向的目录
```

`auto_pipeline.py`、`gui_app_mobile.py`、`web_console.py` 和 `convert_flv_to_mp4.py` 会自动将该目录加入当前进程 PATH。若迁移到其他机器，可安装系统级 FFmpeg，或修改代码中的 `FFMPEG_BIN` 常量。

手动检查:

```powershell
AUTOCLIP_FFMPEG_BIN 指向的目录\ffmpeg.exe -version
AUTOCLIP_FFMPEG_BIN 指向的目录\ffprobe.exe -version
```

步骤1转 MP4、切片和字幕烧录都依赖 FFmpeg。若相关步骤失败，优先检查:

1. FFmpeg 是否在 PATH。
2. ASS 字幕路径是否包含特殊字符。
3. 字体路径是否存在。
4. 硬件编码器是否可用。

### 11.3 模型目录

常见模型位置:

```text
models\whisper\large-v3-turbo.pt
models\whisper\belle-whisper-large-v3-turbo-zh-ct2
funasr\workspace\models
```

---

## 12. 维护注意事项

### 12.1 编码规范

- 源码和文档统一使用 UTF-8。
- Windows 批处理文件已使用 `chcp 65001`。
- PowerShell 查看中文时建议显式指定 `-Encoding UTF8`。

### 12.2 路径规范

- 代码里优先使用 `os.path.join()` 或 `pathlib.Path`。
- 配置文件中可使用 `{input_dir}` 占位符。
- FFmpeg 命令中包含 Windows 路径时要注意转义和引号。

### 12.3 产物完整性

`auto_pipeline.py` 会检查:

- `Data_source.txt` 中的片段是否都有对应切片。
- 每个原始切片是否都有 `_subbed.mp4`（步骤5启用时）。
- 每个切片视频（优先 `_subbed.mp4`，无则原版）是否都有对应封面。

如果缺失，会重跑相关步骤，最多重试 3 次。步骤5被配置跳过时，封面检查按原版切片判定。

### 12.4 投稿状态维护

`upload_manifest.json` 不再只是一次性投稿清单，也承担投稿记忆职责。

维护规则:

- 重新生成投稿清单时，应尽量按 `video_path` 保留旧的投稿状态字段。
- 投稿前将条目标记为 `uploading`，并递增 `upload_attempts`。
- 成功后写入 `success`、`bvid`、`aid`、`last_upload_at`。
- 失败后写入 `failed` 和 `last_error`。
- 用户中断时，尚未开始的条目写入 `skipped` 和中断原因。
- UI 默认不重复选择 `success` 条目，但允许 `failed`、`skipped`、`pending` 继续重试。
- 判断成功必须以有效 `bvid` 为准，不能仅以 uploader 没抛异常为准。

### 12.5 已知风险

- `configs/gui_config.json` 可能包含敏感凭证。
- `funasr/runtime/python` 目录很大，不适合频繁全量检索。
- Web 控制台暴露文件浏览、上传、流水线启动能力，只建议在可信内网/Tailscale 下使用。
- `git` 不一定在运行环境 PATH 中，维护前需确认版本控制状态。
- `rg.exe` 在部分 Windows 环境可能被拒绝执行，可退回 PowerShell 原生命令。

### 12.6 快速排障

| 现象 | 优先检查 |
| --- | --- |
| GUI 启动失败 | `PyQt6` 是否安装，当前 Python 是否正确 |
| FLV 转 MP4 失败 | FFmpeg 是否在 PATH、同名 MP4 是否已存在、FLV 文件是否仍在写入 |
| 字幕生成失败 | ASR 模型路径、CUDA、音频解码、FunASR 便携 worker |
| 高光分析失败 | API key、模型名、Prompt、字幕文件是否存在 |
| 切片失败 | FFmpeg、时间戳格式、源视频文件匹配 |
| 烧录失败 | ASS 文件、字体、FFmpeg subtitles 滤镜 |
| 封面失败 | Pillow、字体路径、视频抽帧时间 |
| 投稿失败 | B 站凭证、分区 tid、封面路径、网络状态、`upload_manifest.json` 中的 `last_error` |
| 投稿被跳过 | 检查 `upload_status` 是否为 `success` 或 `skipped` |
| 投稿次数异常 | 检查 `upload_attempts` 是否被重新生成清单时保留 |

---

## 附录 A: 常用命令

```powershell
# Light 版三栏桌面 GUI
cd /d <仓库根目录>
python gui_app_light.py

# Web 控制台
cd /d <仓库根目录>
python web_console.py --port 5000

# Light 版 Web 控制台
cd /d <仓库根目录>
python web_console_light.py --port 5001

# 完整流水线
python core/auto_pipeline.py -i "录播目录"

# 只执行步骤1：FLV 转 MP4
python core/auto_pipeline.py -i "录播目录" --start 1 --stop 1
python core/convert_flv_to_mp4.py -i "录播目录"

# 只生成字幕
python core/generate_subtitles.py -i "录播目录" --engine portable --funasr-model sensevoice

# 只跑高光分析（生成 Data_source.txt，切片由步骤4 generate_clips.py 完成）
python core/generate_clips_combined.py -i "录播目录" --method combined -s "主播名"

# 只烧录字幕和生成封面
python core/auto_pipeline.py -i "录播目录" --start 5 --stop 6

# 执行投稿
python core/auto_pipeline.py -i "录播目录" --upload
```

---

## 附录 B: 当前维护重点

后续建议优先改进:

1. 将 API key 和 B 站凭证从 `configs/gui_config.json` 拆出，避免误提交。
2. 给 `configs/requirements.txt` 增加完整版本锁定，避免运行环境漂移。
3. 为 `auto_pipeline.py` 的步骤检查逻辑增加小型测试样例。
4. 将 Web 控制台的录播根目录列表从硬编码逐步迁移到配置文件，便于跨机器迁移。
5. 为投稿状态写回逻辑增加单元测试，覆盖成功、失败、406 重试、中断和重复加载清单。
6. 整理 `generate_covers.py` 与 `generate_covers_new.py` 的职责边界。
