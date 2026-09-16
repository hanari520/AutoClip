# AutoClip

AutoClip 是一个面向 B 站直播录播的自动化切片系统。它可以把录播目录加工成带字幕、封面、标题、摘要和投稿清单的切片产物，并提供 Light GUI、Web 控制台（含 Light 版）和命令行流水线。

License: MIT（内置字体与 FunASR 源码的授权见 [LICENSE](LICENSE)、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)）。

完整技术说明见 [AutoClip_技术复刻文档.md](docs/AutoClip_技术复刻文档.md)，主播配置字段说明见 [主播默认配置说明.md](docs/主播默认配置说明.md)。

> **AI Agent 快速上手**: 读 [AGENTS.md](AGENTS.md) 了解项目结构与约定 → 运行 `python scripts\check_env.py` 自检环境缺口 → 按下方「快速开始」补齐 → `python core\auto_pipeline.py -i "<录播目录>" --dry-run` 验证流水线。机器相关路径全部通过环境变量或 `configs/gui_config.json` 配置，代码中无硬编码本机路径。

## 功能概览

- 录播预处理: 自动扫描 `.flv`，使用 FFmpeg stream copy 转为同名 `.mp4`。
- 竖屏转横屏: 可选将竖屏 MP4 转为 1080p 横屏画布（模糊背景 + 居中原画）。
- 弹幕压制: 可选匹配同名 `.mp4` + `.xml`，生成 `原名-弹幕版.mp4`。
- 弹幕 XML 转 ASS: 默认把与视频同名、缺 ASS 的 XML 弹幕转成同名 `.ass`，供步骤3弹幕分析使用（失败不阻断）。
- 字幕生成: 支持 FunASR 便携版、FunASR 直连、Whisper、Belle。
- 高光检测: 支持弹幕热度、LLM 语义、情绪分析和多源融合。
- 视频切片: 根据 `Data_source.txt` 生成高光片段。
- 字幕烧录: 生成 ASS 并调用 FFmpeg 输出 `_subbed.mp4`。
- 封面生成: 抽帧、裁剪、叠加标题/文案和素材。
- B 站投稿: 生成投稿清单，支持批量投稿、状态记忆、失败重试和限流冷却。
- Webhook 通知: 承接 BililiveRecorder Webhook v2 事件，通过 Server 酱推送开播/下播/录制结束通知。

## 技术栈

| 层级 | 技术 | 用途 |
| --- | --- | --- |
| 语言 | Python 3.10+ | 核心业务逻辑 |
| ASR | FunASR / Whisper / Belle | 语音转字幕 |
| 视频处理 | FFmpeg / ffprobe | 转封装、切片、烧录、抽帧 |
| 图像处理 | Pillow / fonttools | 封面合成和字体处理 |
| LLM | 硅基流动 / DeepSeek 官方 / OpenCode Go / 智谱（OpenAI Chat Completions 兼容接口） | 高光分析、标题、摘要、封面文案 |
| GUI | PyQt6 | Light 版三栏桌面控制台 |
| Web | `http.server` + REST + SSE | 远程控制、文件浏览、日志流 |
| 投稿 | `bilibili-api` | B 站上传和投稿参数提交 |

## 项目结构

```text
AutoClip/
├── core/                         # 一键流水线、转码、字幕、切片、烧录、封面
├── analysis/                     # LLM 高光分析、弹幕热度分析
├── utils/                        # ASR、B 站登录/投稿、弹幕、情绪分析等工具
├── configs/                      # 主播配置、Prompt、依赖列表和示例配置
│   ├── streamers/                # 主播默认配置 JSON
│   ├── prompts/                  # 高光分析提示词（历史主播）
│   └── streamer_titles/          # 主播标题相关配置
├── assets/                       # 字体和封面素材
├── launchers/                    # Windows 启动脚本（`旧/` 为已弃用启动器）
├── archive/                      # 归档脚本（gui_app、config_builder 等，见 README）
├── docs/                         # 技术文档
├── funasr/                       # FunASR 相关代码和便携 worker
├── gui_app_light.py              # Light 版三栏桌面 GUI
├── gui_app_mobile.py             # GUI 主体实现
├── web_console.py                # Web 控制台（支持多录播根目录）
├── web_console_light.py          # Light 版 Web 控制台
├── bilirecorder_webhook_server.py # 录播姬 Webhook 通知桥接服务
└── config_gui.py                 # tkinter 配置生成器
```

运行时目录、模型、会话、缩略图、日志和私密配置默认不提交到仓库，见 [.gitignore](.gitignore)。

## 多录播根目录

Web 控制台支持多个录播根目录。根目录列表**不再硬编码在代码里**，按以下优先级读取:

1. `configs/gui_config.json` 的 `recording_roots` 字段（字符串数组，本机私有配置，不入库）
2. 环境变量 `AUTOCLIP_RECORDING_ROOTS`（分号分隔，如 `D:\录播;E:\录播备份`）

```json
// configs/gui_config.json
"recording_roots": ["D:\\录播", "E:\\录播备份"]
```

- 投稿页和文件管理页均可在多个根目录间切换，同名主播/文件夹不会混淆。
- 流水线启动、投稿清单扫描、文件浏览对所有根目录同样生效。
- 无实际录播目录的主播不会出现在主播列表中（配置保留，目录出现后自动恢复）。

## 快速开始（新机器从零搭建）

### 1. 克隆并安装依赖

```powershell
git clone https://github.com/hanari520/AutoClip.git
cd AutoClip
python -m pip install -r configs\requirements.txt
```

要求 Python 3.10+。只用 Web 控制台/CLI 可不装 `PyQt6`；不需要 B 站登录/投稿可不装 `bilibili-api`。

### 2. 安装外部工具

| 工具 | 用途 | 安装方式 |
| --- | --- | --- |
| FFmpeg / ffprobe | 转封装、切片、烧录、抽帧（必需） | 加入 PATH，或设环境变量 `AUTOCLIP_FFMPEG_BIN` 指向其 `bin` 目录 |
| DanmakuFactory | 弹幕 XML 转 ASS / 弹幕压制（可选，步骤1-3/1-4） | 随 [biliLive-tools](https://github.com/renmu333/biliLive-tools) 安装后设 `AUTOCLIP_BILILIVE_TOOLS_BIN` 指向其 bin 目录，或单独加入 PATH |

PowerShell 设置环境变量（永久，用户级）:

```powershell
setx AUTOCLIP_FFMPEG_BIN "C:\Tools\ffmpeg\bin"
setx AUTOCLIP_BILILIVE_TOOLS_BIN "C:\Users\<你>\AppData\Local\Programs\biliLive-tools\resources\app.asar.unpacked\resources\bin"
```

设置后**重开终端**生效。

### 3. 准备 ASR 引擎（至少一个）

| 引擎 | 安装 | 模型 |
| --- | --- | --- |
| funasr（默认推荐） | `pip install funasr`（direct 模式） | 首次运行自动从 ModelScope 下载（SenseVoiceSmall 等） |
| funasr 便携运行时 | 无需 pip；将便携 Python 组装到 `funasr/runtime/python/python.exe`（见技术文档 §5） | 模型放 `funasr/workspace/` |
| qwen3 | 组装 `qwen3_env` 虚拟环境（torch 2.2.2+cu118 + numpy 1.26，其他 torch 版本在部分显卡上会 c10.dll 崩溃） | 模型放 `models/Qwen3-ASR-1.7B` 或 `0.6B` |
| whisper | `pip install openai-whisper` | `.pt` 放 `models/whisper/`，gui_config 里配路径 |
| belle | `pip install faster-whisper` | ct2 模型放 `models/whisper/belle-whisper-large-v3-turbo-zh-ct2` |

### 4. 初始化配置

```powershell
copy configs\gui_config.example.json configs\gui_config.json
```

然后编辑 `configs\gui_config.json`:

- `recording_roots`: 你的录播根目录数组（如 `["D:\\录播"]`），目录结构 `<根目录>\<主播名>\<日期 标题>`
- `global_api_key` 或 `llm_api_keys`: LLM 供应商 API Key（高光分析必需）
- `llm_provider`: `siliconflow` / `deepseek` / `opencode-go` / `zhipu` / `zhipu-coding`

B 站凭证不必手填：启动 Light GUI 后用扫码登录自动写入。

### 5. 生成主播配置

```powershell
python config_gui.py
```

图形化生成器会保存 `configs\streamers\<主播名>.json`。仓库自带的 7 个真实主播配置可直接当模板参考，字段说明见 [docs/主播默认配置说明.md](docs/主播默认配置说明.md)。

### 6. 自检并试运行

```powershell
python scripts\check_env.py                        # 环境自检，缺什么提示什么
python core\auto_pipeline.py -i "<录播根目录>\<主播名>\<日期 标题>" --dry-run
```

## 运行方式

### Light GUI

```powershell
launchers\start_gui_light.bat
```

等价于:

```powershell
python gui_app_light.py
```

### Light Web 控制台

```powershell
launchers\start_web_light.bat
```

默认端口为 `5001`:

```text
http://localhost:5001
```

### Web 控制台（原版）

```powershell
python web_console.py --port 5000
```

默认只监听 `127.0.0.1`（本机访问）。需要手机/局域网访问时显式传 `--host 0.0.0.0`，并确保处于可信网络——控制台具备文件浏览与投稿能力，无内置鉴权。

### 命令行流水线

```powershell
python core\auto_pipeline.py -i "录播目录"
```

常用参数:

```powershell
# 只跑指定步骤
python core\auto_pipeline.py -i "录播目录" --start 5 --stop 6

# 仅检查将执行的步骤
python core\auto_pipeline.py -i "录播目录" --dry-run

# 生成产物后执行投稿
python core\auto_pipeline.py -i "录播目录" --upload

# 指定主播配置
python core\auto_pipeline.py -i "录播目录" -c "configs\streamers\岁己SUI.json"
```

## 流水线步骤

| 步骤 | 名称 | 入口 | 主要输出 |
| --- | --- | --- | --- |
| 1-1 | FLV 转 MP4 | `core/convert_flv_to_mp4.py` | 同名 `.mp4` |
| 1-2 | 竖屏转横屏 | `core/convert_portrait_to_landscape.py` | 横屏 1080p `.mp4`，可选 `原名-竖屏原版.mp4` 备份 |
| 1-3 | 弹幕压制 | `core/burn_danmu_video.py` | `原名-弹幕版.mp4` |
| 1-4 | 弹幕 XML 转 ASS | `core/convert_danmaku_xml.py` | 同名 `.ass`（步骤3弹幕分析源；`step1_prepare.convert_danmaku_xml=false` 时跳过，失败不阻断） |
| 2 | 字幕识别 | `core/generate_subtitles.py` | `.srt` |
| 3 | 高光分析 | `core/generate_clips_combined.py` | `Data_source.txt` |
| 4 | 视频切片 | `core/generate_clips.py` | 切片 `.mp4` |
| 5 | 字幕烧录（可选） | `core/burn_subtitles.py` | `_subbed.mp4`（主播配置 `step5_burn.enabled=false` 时跳过） |
| 6 | 封面生成 | `core/generate_covers.py` / `core/generate_covers_new.py` | `.jpg` / `.png`（抽帧优先用同名原视频，避免封面带字幕；抽帧固定取切片正中间，重新生成画面一致） |
| 7 | 投稿准备 | `core/auto_pipeline.py` | `upload_manifest.json`（有字幕版用字幕版，无则原版） |
| upload | 批量投稿 | `utils/bilibili_uploader.py` | BVID / 投稿结果 |

## 单独执行常用任务

```powershell
# FLV 转 MP4
python core\convert_flv_to_mp4.py -i "录播目录"

# 保留源 FLV 方便排错
python core\convert_flv_to_mp4.py -i "录播目录" --keep-source

# 竖屏转横屏 1080p
python core\convert_portrait_to_landscape.py -i "录播目录"

# 弹幕压制
python core\burn_danmu_video.py -i "录播目录"

# 弹幕 XML 转 ASS（供步骤3弹幕分析，已有 ASS 跳过）
python core\convert_danmaku_xml.py -i "录播目录"

# 生成字幕
python core\generate_subtitles.py -i "录播目录" --engine portable --funasr-model sensevoice

# 高光分析（生成 Data_source.txt，切片由步骤4 generate_clips.py 完成）
python core\generate_clips_combined.py -i "录播目录" --method combined -s "主播名"

# 烧录字幕和生成封面
python core\auto_pipeline.py -i "录播目录" --start 5 --stop 6
```

## 配置说明

配置主要分为三层:

| 层级 | 文件 | 用途 |
| --- | --- | --- |
| 全局配置 | `configs/gui_config.json`（模板: `configs/gui_config.example.json`） | 录播根目录、模型路径、LLM key、B 站凭证、GUI 状态 |
| 主播配置 | `configs/streamers/*.json`（用 `config_gui.py` 生成） | 每个主播的流水线默认参数和梗词 |
| Prompt 配置 | `configs/streamer_titles/*` | LLM 高光分析提示词（按主播） |
| Server 酱通知 | `configs/serverchan_config.json`（模板: `configs/serverchan_config.example.json`） | Webhook 通知桥接配置 |

机器相关路径统一走环境变量，代码中无硬编码:

| 环境变量 | 用途 |
| --- | --- |
| `AUTOCLIP_FFMPEG_BIN` | FFmpeg bin 目录（不在 PATH 上时必设） |
| `AUTOCLIP_BILILIVE_TOOLS_BIN` | biliLive-tools 的 bin 目录（含 DanmakuFactory.exe） |
| `AUTOCLIP_MANAGED_PYTHON` | Belle 引擎子进程用的独立 Python（缺省用当前解释器） |
| `AUTOCLIP_RECORDING_ROOTS` | 录播根目录（分号分隔；gui_config 的 recording_roots 优先） |
| `AUTOCLIP_ALLOWED_MEDIA_ROOTS` | qwen3 ASR worker 允许的媒体根目录（分号分隔） |

主播配置字段说明见 [docs/主播默认配置说明.md](docs/主播默认配置说明.md)。

LLM 供应商（硅基流动 / DeepSeek 官方 / OpenCode Go / 智谱，含智谱 Coding 套餐）由 `configs/gui_config.json` 的 `llm_provider` 字段切换，各供应商 API Key 分开存储于 `llm_api_keys`，详见 [技术文档](docs/AutoClip_技术复刻文档.md)第 6 节。

首次配置 Server 酱通知时复制示例文件:

```powershell
copy configs\serverchan_config.example.json configs\serverchan_config.json
```

然后填入自己的 `sendkey` 或在启动前设置环境变量:

```powershell
$env:SERVERCHAN_SENDKEY="SCTxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
python bilirecorder_webhook_server.py
```

## BililiveRecorder Webhook

启动通知桥接服务:

```powershell
launchers\start_bilirecorder_webhook.bat
```

等价于:

```powershell
python bilirecorder_webhook_server.py --host 127.0.0.1 --port 18010
```

录播姬 Webhook v2 地址:

```text
http://127.0.0.1:18010/webhook/mikufansrecorder
```

默认推送事件:

| 事件 | 含义 |
| --- | --- |
| `StreamStarted` | 主播开播 |
| `StreamEnded` | 主播下播 |
| `SessionEnded` | 本次录制结束 |

服务启动时会执行一次录播姬 Cookie 登录态巡检，之后按配置定时检查。

## 归档说明

`archive/` 目录存放不再使用但保留源码的脚本（含还原方法说明）:

- `gui_app.py` — 旧桌面 GUI 壳，被 Light GUI 取代
- `config_builder.py` / `config_web.py` — 旧配置生成器
- `pc_console_gui.py` — 旧 PC 监控面板

`launchers/旧/` 存放对应的旧启动器。

## 安全注意

- 不要提交 `configs/gui_config.json`，其中可能包含 API key、B 站 Cookie、SESSDATA、bili_jct 等敏感信息。
- 不要提交 `configs/serverchan_config.json`，其中可能包含 Server 酱 SENDKEY。
- 不要提交 `configs/bilirecorder_cookie_source.txt` 或任何 Cookie 备份文件。
- Web 控制台具备文件浏览、上传、流水线启动和投稿能力且无内置鉴权，默认只监听 `127.0.0.1`；`--host 0.0.0.0` 仅应在可信内网或 Tailscale 环境使用。
- Webhook 服务的出站通知地址经 `utils/net_guard.py` 校验：仅允许 http/https，拒绝 localhost、环回、私有、保留、CGNAT 等非公网目标（SSRF 防护）。
- `models/`、`funasr/runtime/`、`sessions/`、`thumbnails/`、`backups/` 等运行时目录默认被忽略。

## 快速排障

| 现象 | 优先检查 |
| --- | --- |
| 环境不确定 | `python scripts\check_env.py` 一键自检 |
| GUI 启动失败 | `PyQt6` 是否安装，当前 Python 是否正确 |
| FLV 转 MP4 失败 | FFmpeg 是否在 PATH 或 `AUTOCLIP_FFMPEG_BIN`、同名 MP4 是否已存在、FLV 是否仍在写入 |
| 弹幕压制/转 ASS 失败或跳过 | DanmakuFactory 是否可寻（PATH 或 `AUTOCLIP_BILILIVE_TOOLS_BIN`） |
| 字幕生成失败 | ASR 模型路径、CUDA、音频解码、FunASR worker |
| 高光分析失败 | API key、模型名、Prompt、字幕文件是否存在 |
| 切片失败 | FFmpeg、时间戳格式、源视频文件匹配 |
| 烧录失败 | ASS 文件、字体（纯文件名会到 `assets/font` 解析）、FFmpeg subtitles 滤镜 |
| 封面失败 | Pillow、字体路径、视频抽帧时间 |
| 投稿失败 | B 站凭证、分区 tid、封面路径、网络状态、`upload_manifest.json` 中的 `last_error` |
| Web 找不到录播目录 | `gui_config.json` 的 `recording_roots`（或 `AUTOCLIP_RECORDING_ROOTS`）是否包含该根目录 |

## 维护建议

- 给依赖增加更完整的版本锁定，减少环境漂移。
- 为流水线产物完整性检查和投稿状态写回逻辑补充测试。
- 新增 Web 文件访问 API 时复用现有路径安全检查。
- 新增机器相关路径时一律走环境变量或 gui_config，不硬编码进代码。
