# -*- coding: utf-8 -*-
"""AutoClip 环境自检脚本

用法:
  python scripts\\check_env.py

零第三方依赖，裸 clone 后即可运行。逐项检查 Python 版本、依赖包、
外部工具（FFmpeg / DanmakuFactory）、配置文件与模型目录，
缺什么打印什么，方便人工或 AI Agent 快速定位环境缺口。

退出码: 0 = 核心项全部就绪, 1 = 存在缺失的核心项。
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MISSING_CORE = []


def ok(msg):
    print(f"  [OK]   {msg}")


def warn(msg, hint=""):
    print(f"  [缺]   {msg}")
    if hint:
        print(f"         -> {hint}")


def info(msg):
    print(f"  [信息] {msg}")


def check_module(name, pip_name=None, core=False, hint=""):
    try:
        __import__(name)
        ok(f"Python 包 {pip_name or name}")
        return True
    except ImportError:
        warn(f"Python 包 {pip_name or name}",
             hint or f"python -m pip install {pip_name or name}")
        if core:
            MISSING_CORE.append(pip_name or name)
        return False


def check_tool(exe_name, env_var, purpose, join_file=""):
    """PATH 或环境变量目录中查找外部工具。"""
    from shutil import which
    if which(exe_name):
        ok(f"{exe_name} ({purpose}) - 在 PATH 上")
        return
    d = os.environ.get(env_var, "")
    if d:
        candidate = os.path.join(d, exe_name + (".exe" if os.name == "nt" else ""))
        if os.path.isfile(candidate):
            ok(f"{exe_name} ({purpose}) - 由 {env_var} 指定")
            return
    warn(f"{exe_name} ({purpose})",
         f"安装后加入 PATH，或设置环境变量 {env_var} 指向其所在目录")


def main():
    print("=" * 62)
    print("AutoClip 环境自检")
    print("=" * 62)

    # 1. Python 版本
    print("\n[1] Python")
    v = sys.version_info
    if v >= (3, 10):
        ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        warn(f"Python {v.major}.{v.minor} (需要 3.10+)", "https://www.python.org/downloads/")
        MISSING_CORE.append("python>=3.10")

    # 2. 依赖包
    print("\n[2] 依赖包 (configs/requirements.txt)")
    check_module("PIL", "Pillow", core=True)
    check_module("fontTools", "fonttools", core=True)
    check_module("requests", "Requests", core=True)
    check_module("numpy", "numpy", core=True)
    check_module("PyQt6", "PyQt6",
                 hint="只用 Web 控制台/CLI 可不装; python -m pip install PyQt6")
    check_module("bilibili_api", "bilibili-api",
                 hint="需要 B 站登录/投稿时安装; python -m pip install bilibili-api")

    print("\n[3] ASR 引擎 (按需, 至少装一个)")
    has_funasr = check_module("funasr", "funasr",
                              hint="funasr 引擎; python -m pip install funasr")
    check_module("faster_whisper", "faster-whisper",
                 hint="whisper/belle 引擎的 ct2 后端")
    check_module("whisper", "openai-whisper",
                 hint="openai-whisper 引擎")
    portable_py = os.path.join(PROJECT_ROOT, "funasr", "runtime", "python", "python.exe")
    if os.path.isfile(portable_py):
        ok(f"FunASR 便携运行时 funasr/runtime/python/python.exe")
    elif has_funasr:
        info("未组装 FunASR 便携运行时 (已可直接 import funasr, 走 direct 模式)")
    else:
        info("未组装 FunASR 便携运行时 (可选, 见 README 环境搭建)")

    # 3. 外部工具
    print("\n[4] 外部工具")
    check_tool("ffmpeg", "AUTOCLIP_FFMPEG_BIN", "转码/切片/烧录/封面")
    check_tool("ffprobe", "AUTOCLIP_FFMPEG_BIN", "媒体探测")
    check_tool("DanmakuFactory", "AUTOCLIP_BILILIVE_TOOLS_BIN",
               "弹幕 XML 转 ASS (步骤1-3/1-4, 可选)",
               )

    # 4. 配置文件
    print("\n[5] 配置文件")
    gui_cfg = os.path.join(PROJECT_ROOT, "configs", "gui_config.json")
    if os.path.isfile(gui_cfg):
        ok("configs/gui_config.json")
        try:
            import json
            with open(gui_cfg, encoding="utf-8") as f:
                cfg = json.load(f)
            roots = cfg.get("recording_roots") or []
            if roots and all(os.path.isdir(r) for r in roots):
                ok(f"录播根目录 recording_roots: {len(roots)} 个且都存在")
            elif roots:
                warn("recording_roots 中有目录不存在", str(roots))
            else:
                info("未配置 recording_roots (Web 控制台将无录播根目录可浏览)")
            if not (cfg.get("global_api_key") or (cfg.get("llm_api_keys") or {}).get(
                    cfg.get("llm_provider", ""), "")):
                warn("LLM API Key 未配置",
                     "在 GUI 全局设置或 gui_config.json 的 global_api_key / llm_api_keys 填入")
        except Exception as e:
            warn(f"gui_config.json 解析失败: {e}")
    else:
        warn("configs/gui_config.json 不存在",
             "复制 configs/gui_config.example.json 为 gui_config.json 后填写")
        MISSING_CORE.append("gui_config.json")

    streamer_dir = os.path.join(PROJECT_ROOT, "configs", "streamers")
    if os.path.isdir(streamer_dir):
        js = [f for f in os.listdir(streamer_dir) if f.endswith(".json")]
        ok(f"主播配置 {len(js)} 个 (configs/streamers/)")
        info("新增主播配置: python config_gui.py (图形化生成器)")
    else:
        warn("configs/streamers/ 不存在", "运行 python config_gui.py 生成")

    # 5. 模型目录 (信息性)
    print("\n[6] 本地模型目录 (按引擎, 可选)")
    for rel, label in [
        ("models\\whisper", "whisper/belle 模型 (models/whisper)"),
        ("funasr\\workspace", "FunASR 模型缓存 (funasr/workspace)"),
    ]:
        d = os.path.join(PROJECT_ROOT, *rel.split("\\"))
        if os.path.isdir(d) and os.listdir(d):
            ok(label)
        else:
            info(f"未发现 {label} (用到对应引擎时再准备)")
    qwen_env = os.path.join(PROJECT_ROOT, "qwen3_env")
    if os.path.isdir(qwen_env):
        ok("qwen3_env 虚拟环境 (Qwen3-ASR 引擎)")
    else:
        info("无 qwen3_env (Qwen3-ASR 引擎需要, 见 README; 其余引擎不受影响)")

    # 总结
    print("\n" + "=" * 62)
    if MISSING_CORE:
        print(f"结果: 缺少核心项 {len(MISSING_CORE)} 个 -> {', '.join(MISSING_CORE)}")
        print("请按上面 [缺] 行的提示补齐后重跑本脚本。")
        sys.exit(1)
    print("结果: 核心环境就绪。可运行: python core\\auto_pipeline.py -i \"<录播目录>\" --dry-run")
    sys.exit(0)


if __name__ == "__main__":
    main()
