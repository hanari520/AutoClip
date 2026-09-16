# -*- coding: utf-8 -*-
"""
AutoClip Web Console v2 — Tailscale 移动远程控制台
====================================================
基于 Tailscale 内网的移动端 Web 控制台，前后端分离架构。

模块:
  1. 文件管理 — 目录浏览 / 文件预览 / 上传 / 下载
  2. 投稿管理 — 投稿清单 CRUD / 批量投稿 / 状态跟踪
  3. 流水线控制 — 启动 / 中断 / 实时日志 (SSE)

启动:
  python web_console.py --port 5000
  手机通过 Tailscale 访问: http://100.x.x.x:5000

架构:
  后端: Python http.server + RESTful JSON API
  前端: 嵌入式 SPA (vanilla JS, 无外部依赖)
  通信: HTTP JSON + SSE (日志流)
"""

import json
import os
import sys
import re
import time
import shlex
import ctypes
import threading
import subprocess
import traceback
import cgi
import tempfile
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote, quote
from datetime import datetime
from collections import deque

# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(PROJECT_ROOT, "configs", "streamers")
GUI_CONFIG_FILE = os.path.join(PROJECT_ROOT, "configs", "gui_config.json")


def _load_recording_roots():
    """录播根目录：优先 gui_config.json 的 recording_roots（本机私有配置，不入库），
    次选环境变量 AUTOCLIP_RECORDING_ROOTS（分号分隔），均未配置时返回空列表。"""
    roots = []
    try:
        with open(GUI_CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        roots = [str(p).strip() for p in (cfg.get("recording_roots") or []) if str(p).strip()]
    except Exception:
        pass
    if not roots:
        env = os.environ.get("AUTOCLIP_RECORDING_ROOTS", "")
        roots = [p.strip() for p in env.split(";") if p.strip()]
    return roots


INPUT_BASES = _load_recording_roots()
INPUT_BASE = INPUT_BASES[0] if INPUT_BASES else ""
INPUT_BASE_2 = INPUT_BASES[1] if len(INPUT_BASES) > 1 else ""
FFMPEG_BIN = os.environ.get("AUTOCLIP_FFMPEG_BIN", "")
if os.path.isdir(FFMPEG_BIN):
    os.environ["PATH"] = FFMPEG_BIN + os.pathsep + os.environ.get("PATH", "")
SESSIONS_DIR = os.path.join(PROJECT_ROOT, "sessions")
THUMB_DIR = os.path.join(PROJECT_ROOT, "thumbnails")
# B站投稿限频防护：实测单账号约每小时 20 条投稿，超频会被 406 拒绝
# （参考 biliLive-tools 的 minUploadInterval 方案：逐稿最低间隔 + 406 冷却重试 + 连续 406 熔断）
UPLOAD_MIN_INTERVAL_SEC = 240   # 相邻两次投稿的最小间隔（4 分钟 ≈ 15 条/小时）
UPLOAD_406_BACKOFF_SEC = 600    # 命中 406 后的冷却时长
MAX_406_RETRIES = 3             # 连续 406 次数上限，超过则熔断停止本批
LAST_SESSION_FILE = os.path.join(SESSIONS_DIR, "last_session.json")
CONSOLE_ACTIVITY_FILE = os.path.join(PROJECT_ROOT, "console_activity.jsonl")

os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(THUMB_DIR, exist_ok=True)

# 允许文件浏览的根目录白名单（防止路径穿越攻击；空根目录自动剔除）
ALLOWED_ROOTS = [os.path.normpath(p) for p in INPUT_BASES if p]
ALLOWED_ROOTS.append(os.path.normpath(PROJECT_ROOT))

# 视频扩展名（用于投稿清单扫描）
VIDEO_EXTS = {".mp4", ".mkv", ".flv", ".avi", ".mov", ".wmv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
TEXT_EXTS = {".txt", ".json", ".srt", ".ass", ".py", ".md", ".log", ".jsonl"}

# ═══════════════════════════════════════════════════════════════
# 线程安全状态
# ═══════════════════════════════════════════════════════════════

LOCK = threading.Lock()

# 流水线状态
pipeline_proc = None
pipeline_log = deque(maxlen=5000)  # 环形缓冲，防内存膨胀
pipeline_running = False
pipeline_paused = False
pipeline_abort_requested = False
pipeline_step = ""
pipeline_input_dir = ""

# 投稿状态跟踪
upload_status = {
    "running": False,
    "total": 0,
    "completed": 0,
    "failed": 0,
    "skipped": 0,
    "current_title": "",
    "current_progress": 0.0,
    "results": [],  # [{title, bvid, status, error}]
    "log": deque(maxlen=500),
}
upload_thread = None

# SSE 客户端连接列表
sse_clients = set()
sse_lock = threading.Lock()

# 当前会话（刷新恢复用）
current_session = {
    "streamer": "",
    "folder": "",
    "input_dir": "",
    "log": [],
    "completed": False,
}


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _safe_path(raw_path):
    """路径安全校验：解析为绝对路径后检查是否在允许的根目录内。

    防止路径穿越攻击（如 ../../etc/passwd）。
    返回归一化后的绝对路径，不在白名单内返回 None。
    """
    if not raw_path:
        return None
    # 解码 URL 编码 + 拼接为绝对路径
    abs_path = os.path.normpath(os.path.abspath(raw_path))
    for root in ALLOWED_ROOTS:
        try:
            # commonpath 检查 abs_path 是否在 root 下（或就是 root）
            if os.path.commonpath([abs_path, root]) == root:
                return abs_path
        except ValueError:
            continue
    return None


def _file_size_str(size):
    """字节数 → 人类可读"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def _file_icon(ext):
    """根据扩展名返回图标 emoji（优化：区分视频/字幕/文本/数据/字体类型）"""
    e = ext.lower()
    icons = {
        # 视频
        '.mp4': '🎬', '.mkv': '🎬', '.avi': '🎬', '.mov': '🎬', '.wmv': '🎬',
        '.flv': '📀', '.ts': '📀', '.webm': '🎬',
        # 字幕
        '.srt': '💬', '.ass': '💬', '.vtt': '💬', '.sub': '💬',
        # 文本/数据
        '.txt': '📄', '.json': '📋', '.md': '📝', '.py': '🐍',
        '.xml': '📰', '.jsonl': '📋', '.csv': '📊', '.log': '📋',
        # 图片
        '.png': '🖼', '.jpg': '🖼', '.jpeg': '🖼', '.gif': '🖼', '.webp': '🖼', '.bmp': '🖼',
        # 字体
        '.ttf': '🔤', '.otf': '🔤',
        # 压缩包
        '.zip': '📦', '.rar': '📦', '.7z': '📦', '.gz': '📦',
        # 音频
        '.mp3': '🎵', '.wav': '🎵', '.flac': '🎵', '.aac': '🎵',
    }
    return icons.get(e, '📁' if e == '' else '📄')


def _make_thumb(cover_path, max_width=240):
    """生成缩略图，返回缩略图路径。已存在则跳过。"""
    try:
        import hashlib
        h = hashlib.md5(cover_path.encode()).hexdigest()[:12]
        thumb_path = os.path.join(THUMB_DIR, f"{h}.jpg")
        if os.path.isfile(thumb_path):
            return thumb_path
        from PIL import Image
        im = Image.open(cover_path)
        im = im.convert("RGB")
        w, h_ = im.size
        if w > max_width:
            ratio = max_width / w
            im = im.resize((max_width, int(h_ * ratio)), Image.LANCZOS)
        im.save(thumb_path, "JPEG", quality=75)
        return thumb_path
    except Exception:
        return cover_path  # 失败时回退原图


def _log_phone_command(entry):
    """写入手机端指令到共享活动日志（JSON Lines），供 PC GUI 轮询"""
    entry["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(CONSOLE_ACTIVITY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


def _save_last_session():
    """持久化当前会话（供手机掉线刷新后恢复）"""
    try:
        current_session["log"] = list(pipeline_log)[-500:]
        with open(LAST_SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(current_session, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_last_session():
    """启动时恢复上次未完成的会话"""
    if not os.path.isfile(LAST_SESSION_FILE):
        return
    try:
        with open(LAST_SESSION_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if not saved.get("completed", True):
            current_session.update(saved)
    except Exception:
        pass


def _broadcast_sse(data):
    """向所有 SSE 客户端广播一条消息"""
    msg = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    dead = []
    with sse_lock:
        for client_wfile in sse_clients:
            try:
                client_wfile.write(msg.encode("utf-8"))
                client_wfile.flush()
            except Exception:
                dead.append(client_wfile)
        for d in dead:
            sse_clients.discard(d)


def _emit_log(line, level="info"):
    """写入日志缓冲 + 广播 SSE"""
    ts = datetime.now().strftime("%H:%M:%S")
    entry = {"ts": ts, "msg": line, "level": level}
    pipeline_log.append(entry)
    _broadcast_sse({"type": "log", **entry})


# ═══════════════════════════════════════════════════════════════
# Windows 进程挂起/恢复
# ═══════════════════════════════════════════════════════════════

def suspend_process(pid):
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = kernel32.OpenProcess(0x0800, False, pid)
        if not h:
            return False
        ntdll = ctypes.WinDLL("ntdll")
        status = ntdll.NtSuspendProcess(h)
        kernel32.CloseHandle(h)
        return status == 0
    except Exception:
        return False


def resume_process(pid):
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = kernel32.OpenProcess(0x0800, False, pid)
        if not h:
            return False
        ntdll = ctypes.WinDLL("ntdll")
        status = ntdll.NtResumeProcess(h)
        kernel32.CloseHandle(h)
        return status == 0
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
# 文件管理
# ═══════════════════════════════════════════════════════════════

def list_directory(path):
    """列出目录内容，返回 {folders: [...], files: [...]}"""
    folders = []
    files = []
    try:
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            ext = os.path.splitext(name)[1]
            if os.path.isdir(full):
                folders.append({
                    "name": name,
                    "type": "dir",
                    "icon": "📁",
                    "modified": datetime.fromtimestamp(
                        os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M"),
                })
            elif os.path.isfile(full):
                size = os.path.getsize(full)
                files.append({
                    "name": name,
                    "type": "file",
                    "icon": _file_icon(ext),
                    "ext": ext.lower(),
                    "size": _file_size_str(size),
                    "size_bytes": size,
                    "modified": datetime.fromtimestamp(
                        os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M"),
                })
    except PermissionError:
        return {"error": "权限不足"}
    except Exception as e:
        return {"error": str(e)}
    return {"folders": folders, "files": files, "path": path, "bases": INPUT_BASES}


def scan_upload_manifest(input_dir):
    """扫描录播目录，生成/读取投稿清单

    优先读取已有的 upload_manifest.json；
    不存在则扫描 _subbed.mp4 文件自动生成。
    """
    manifest_path = os.path.join(input_dir, "upload_manifest.json")
    streamer_name = os.path.basename(os.path.dirname(input_dir))
    cfg_path = os.path.join(CONFIG_DIR, f"{streamer_name}.json")
    cfg = {}
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    s7 = cfg.get("step7_upload", {})
    title_suffix_name = str(s7.get("title_suffix_name") or cfg.get("streamer_name") or streamer_name).strip() or streamer_name

    # 优先读取已有清单
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                items = json.load(f)
            # 规范化 copyright 为整数（兼容旧版字符串格式）
            changed = False
            for item in items:
                item.setdefault("upload_status", "pending")
                item.setdefault("upload_attempts", 0)
                item.setdefault("bvid", "")
                item.setdefault("aid", "")
                item.setdefault("last_error", "")
                item.setdefault("last_upload_at", "")
                item.setdefault("last_attempt_at", "")
                cp = item.get("cover_path", "")
                if cp and os.path.isfile(cp):
                    item["thumb_path"] = _make_thumb(cp)
                else:
                    item["thumb_path"] = ""
                # 兼容旧版字符串 copyright
                c = item.get("copyright", 1)
                if isinstance(c, str):
                    item["copyright"] = 1 if c == "original" else 2
                    changed = True
                title = str(item.get("title", ""))
                if title:
                    base_title = re.sub(r"^【[^】]*】", "", title)
                    base_title = re.sub(r"【[^】]*】$", "", base_title)
                    new_title = f"【{title_suffix_name}】{base_title}"
                    if new_title != title:
                        item["title"] = new_title
                        changed = True
            if changed:
                save_upload_manifest(input_dir, items)
            return items
        except Exception:
            pass

    # 自动扫描生成
    videos_dir = os.path.join(input_dir, "切片", "视频")
    if not os.path.isdir(videos_dir):
        videos_dir = os.path.join(input_dir, "切片")
    if not os.path.isdir(videos_dir):
        videos_dir = input_dir

    covers_dir = os.path.join(input_dir, "切片", "封面")
    if not os.path.isdir(covers_dir):
        covers_dir = os.path.join(input_dir, "切片")

    default_tags = s7.get("tags", [])
    default_tid = s7.get("tid", 27)
    # 从配置读取版权类型：配置写 "original"→1，"reprint"→2，或直接写整数
    cfg_copyright = s7.get("copyright", "original")
    if isinstance(cfg_copyright, int):
        default_copyright = cfg_copyright
    else:
        default_copyright = 1 if cfg_copyright == "original" else 2

    title_meta = {}
    data_source_path = os.path.join(input_dir, "Data_source.txt")
    if os.path.isfile(data_source_path):
        invalid_chars = '<>:"/\\|?*'
        try:
            with open(data_source_path, "r", encoding="utf-8") as f:
                for clip in json.load(f):
                    title = str(clip.get("title", "")).strip()
                    if not title or not (clip.get("summary") or clip.get("real_timestamp")):
                        continue
                    key = title
                    for ch in invalid_chars:
                        key = key.replace(ch, "_")
                    title_meta[key[:100]] = {
                        "summary": str(clip.get("summary", "")).strip(),
                        "real_timestamp": str(clip.get("real_timestamp", "")).strip(),
                    }
        except Exception:
            title_meta = {}

    items = []
    if os.path.isdir(videos_dir):
        for fname in sorted(os.listdir(videos_dir)):
            if not fname.endswith("_subbed.mp4"):
                continue
            base = fname[:-len("_subbed.mp4")]
            # 匹配封面
            cover = ""
            if os.path.isdir(covers_dir):
                for ext in (".jpg", ".png", ".jpeg"):
                    candidate = os.path.join(covers_dir, base + ext)
                    if os.path.isfile(candidate):
                        cover = candidate
                        break

            # 从文件名提取标题
            m = re.match(r"^\[([^\]]+)\]\s*(.+)$", base)
            title_raw = m.group(2) if m else base
            title = f"【{title_suffix_name}】{title_raw}"
            meta = title_meta.get(title_raw, {})
            desc_parts = []
            if meta.get("real_timestamp"):
                desc_parts.append(f"直播时间：{meta['real_timestamp']}")
            if meta.get("summary"):
                if desc_parts:
                    desc_parts.append("")
                desc_parts.append(meta["summary"])

            items.append({
                "title": title,
                "video_path": os.path.join(videos_dir, fname),
                "cover_path": cover,
                "thumb_path": _make_thumb(cover) if cover else "",
                "desc": "\n".join(desc_parts),
                "tags": list(default_tags) if default_tags else [],
                "tid": default_tid,
                "copyright": default_copyright,
                "upload_status": "pending",
                "upload_attempts": 0,
                "bvid": "",
                "aid": "",
                "last_error": "",
                "last_upload_at": "",
                "last_attempt_at": "",
            })

    # 保存到磁盘
    if items:
        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    return items


def save_upload_manifest(input_dir, items):
    """保存投稿清单到磁盘"""
    manifest_path = os.path.join(input_dir, "upload_manifest.json")
    # 规范化 copyright 为整数（兼容旧版字符串格式）
    for item in items:
        cp = item.get("copyright", 1)
        if isinstance(cp, str):
            item["copyright"] = 1 if cp == "original" else 2
        elif not isinstance(cp, int):
            item["copyright"] = 1
    # 保存时不写 thumb_path（运行时生成的临时字段）
    clean = []
    for item in items:
        clean_item = {k: v for k, v in item.items() if k != "thumb_path"}
        clean.append(clean_item)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)


def update_upload_manifest_item(input_dir, video_path, updates):
    """按 video_path 写回单条投稿状态。"""
    try:
        items = scan_upload_manifest(input_dir)
        changed = False
        target = os.path.normcase(video_path)
        for item in items:
            if os.path.normcase(item.get("video_path", "")) == target:
                item.update(updates)
                changed = True
                break
        if changed:
            save_upload_manifest(input_dir, items)
    except Exception as e:
        _emit_log(f"  ⚠️ 投稿状态写回失败: {e}", "warning")


def terminate_process_tree(proc):
    """终止流水线进程树，返回 (ok, message)。"""
    if not proc or proc.poll() is not None:
        return True, "process not running"
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            output = (result.stdout or result.stderr or "").strip()
            return result.returncode == 0, output or f"taskkill exit {result.returncode}"
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return True, "terminated"
    except Exception as e:
        return False, str(e)


# ═══════════════════════════════════════════════════════════════
# 流水线执行
# ═══════════════════════════════════════════════════════════════

def run_pipeline(input_dir, steps, streamer, folder):
    """后台线程：运行 auto_pipeline.py"""
    global pipeline_proc, pipeline_running, pipeline_paused, pipeline_abort_requested, pipeline_step, pipeline_input_dir

    with LOCK:
        pipeline_running = True
        pipeline_paused = False
        pipeline_abort_requested = False
        pipeline_step = "starting"
        pipeline_input_dir = input_dir
        pipeline_log.clear()

    current_session["streamer"] = streamer
    current_session["folder"] = folder
    current_session["input_dir"] = input_dir
    current_session["completed"] = False

    _emit_log(f"🚀 启动流水线: {streamer} / {folder}", "info")
    _emit_log(f"📁 输入目录: {input_dir}", "info")
    _emit_log(f"📋 步骤: {steps}", "info")

    python = sys.executable
    auto_pipeline = os.path.join(PROJECT_ROOT, "core", "auto_pipeline.py")
    step_nums = sorted(int(s) for s in steps)
    cmd = [
        python, auto_pipeline,
        "--input-dir", input_dir,
        "--start", str(step_nums[0]),
        "--stop", str(step_nums[-1]),
    ]

    _emit_log(f"$ {' '.join(shlex.quote(str(x)) for x in cmd)}", "info")

    try:
        pipeline_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=PROJECT_ROOT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            bufsize=1,
        )

        for line in pipeline_proc.stdout:
            with LOCK:
                aborting = pipeline_abort_requested
            if aborting:
                terminate_process_tree(pipeline_proc)
                break
            line = line.rstrip("\n").rstrip("\r")
            if not line:
                continue
            level = "info"
            if "✅" in line or "完成" in line:
                level = "success"
            elif "❌" in line or "错误" in line or "失败" in line:
                level = "error"
            elif "⚠" in line:
                level = "warning"
            _emit_log(line, level)
            if "步骤" in line:
                pipeline_step = line.strip()
                _broadcast_sse({"type": "step", "step": pipeline_step})

        pipeline_proc.wait()
        rc = pipeline_proc.returncode
        with LOCK:
            aborted = pipeline_abort_requested
        if aborted:
            _emit_log("🛑 流水线已中断", "warning")
        elif rc == 0:
            _emit_log("✅ 流水线完成", "success")
        else:
            _emit_log(f"❌ 流水线退出码: {rc}", "error")

    except Exception as e:
        _emit_log(f"💥 异常: {e}", "error")
        traceback.print_exc()
    finally:
        with LOCK:
            aborted = pipeline_abort_requested
            pipeline_running = False
            pipeline_paused = False
            pipeline_abort_requested = False
            pipeline_step = "done"
            pipeline_proc = None
        current_session["log"] = list(pipeline_log)[-500:]
        current_session["completed"] = not aborted
        _save_last_session()
        _broadcast_sse({"type": "pipeline_done"})
        _log_phone_command({
            "type": "pipeline_done",
            "streamer": streamer,
            "folder": folder,
        })


# ═══════════════════════════════════════════════════════════════
# 投稿执行
# ═══════════════════════════════════════════════════════════════

def run_upload_batch(input_dir, selected_items):
    """后台线程：批量投稿到 B 站"""
    with LOCK:
        upload_status["running"] = True
        upload_status["total"] = len(selected_items)
        upload_status["completed"] = 0
        upload_status["failed"] = 0
        upload_status["skipped"] = 0
        upload_status["current_title"] = ""
        upload_status["current_progress"] = 0.0
        upload_status["results"] = []
        upload_status["log"].clear()

    _emit_log(f"📤 开始批量投稿 ({len(selected_items)} 个视频)", "info")

    try:
        from utils.bilibili_uploader import BilibiliUploader
        uploader = BilibiliUploader()
    except Exception as e:
        _emit_log(f"❌ 投稿模块加载失败: {e}", "error")
        with LOCK:
            upload_status["running"] = False
        return

    i = 0
    consecutive_406 = 0
    while i < len(selected_items):
        item = selected_items[i]
        if not upload_status["running"]:
            _emit_log("🛑 投稿已中断", "warning")
            break

        title = item.get("title", "未知标题")
        video_path = item.get("video_path", "")

        with LOCK:
            upload_status["current_title"] = title
            upload_status["current_progress"] = 0.0

        _emit_log(f"\n--- [{i+1}/{len(selected_items)}] {title} ---", "info")
        if item.get("upload_status") == "success" and item.get("bvid"):
            _emit_log(f"  🔁 已投稿过 BV={item.get('bvid')}，按本次选择重新投稿", "warning")

        attempts = int(item.get("upload_attempts", 0) or 0) + 1
        item["upload_attempts"] = attempts
        update_upload_manifest_item(input_dir, video_path, {
            "upload_status": "uploading",
            "upload_attempts": attempts,
            "last_attempt_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_error": "",
        })

        if not os.path.isfile(video_path):
            _emit_log(f"  ❌ 视频文件不存在: {video_path}", "error")
            update_upload_manifest_item(input_dir, video_path, {
                "upload_status": "failed",
                "last_error": "视频文件不存在",
            })
            with LOCK:
                upload_status["failed"] += 1
                upload_status["results"].append({
                    "title": title, "bvid": "", "status": "failed",
                    "error": "视频文件不存在",
                })
            i += 1
            continue

        # 进度回调
        last_ts = [0.0]
        hit_406 = [False]
        def cb(evt, pct, data):
            now = time.time()
            if evt == "uploading":
                if now - last_ts[0] >= 0.5 or data.get("chunk", 0) >= data.get("chunks", 1):
                    last_ts[0] = now
                    c = data.get("chunk", 0)
                    t = data.get("chunks", 1)
                    _emit_log(f"  上传中 {pct*100:.0f}% (分片 {c}/{t})", "info")
                    with LOCK:
                        upload_status["current_progress"] = pct
                    _broadcast_sse({"type": "upload_progress", "progress": pct})
            elif evt == "done":
                bvid = data.get("bvid", "?")
                _emit_log(f"  ✅ 投稿成功 BV={bvid}", "success")
            elif evt == "error":
                err_msg = str(data.get("error", ""))
                if "406" in err_msg:
                    hit_406[0] = True
                _emit_log(f"  ❌ {err_msg}", "error")

        uploader.on_progress(cb)

        try:
            result = uploader.upload(
                video_path=video_path,
                title=title,
                desc=item.get("desc", ""),
                tags=item.get("tags", []),
                tid=item.get("tid", 27),
                copyright=item.get("copyright", 1),
                cover_path=item.get("cover_path") or None,
            )
            bvid = result.get("bvid", "?")
            aid = result.get("aid", "")
            if bvid and bvid != "?":
                update_upload_manifest_item(input_dir, video_path, {
                    "upload_status": "success",
                    "bvid": bvid,
                    "aid": aid,
                    "last_error": "",
                    "last_upload_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                with LOCK:
                    upload_status["completed"] += 1
                    upload_status["results"].append({
                        "title": title, "bvid": bvid, "status": "success",
                        "error": "",
                    })
            elif hit_406[0]:
                consecutive_406 += 1
                if consecutive_406 > MAX_406_RETRIES:
                    err = (f"B站限频(406)：冷却重试 {MAX_406_RETRIES} 次仍被拒，"
                           f"停止本批投稿，未投稿件保持待投稿状态，可稍后重试")
                    _emit_log(f"  🛑 {err}", "error")
                    update_upload_manifest_item(input_dir, video_path, {
                        "upload_status": "failed",
                        "last_error": err,
                    })
                    with LOCK:
                        upload_status["failed"] += 1
                        upload_status["results"].append({
                            "title": title, "bvid": "", "status": "failed",
                            "error": err,
                        })
                    break
                _emit_log(
                    f"  🕐 检测到 406（B站限频，约每小时20条），"
                    f"冷却 {UPLOAD_406_BACKOFF_SEC // 60} 分钟后重试当前稿件"
                    f"（第 {consecutive_406}/{MAX_406_RETRIES} 次）...", "warning")
                for _ in range(UPLOAD_406_BACKOFF_SEC):
                    if not upload_status["running"]:
                        break
                    time.sleep(1)
                if not upload_status["running"]:
                    break
                continue
            else:
                err = f"上传接口未返回有效 BV 号: {result}"
                _emit_log(f"  ❌ {err}", "error")
                update_upload_manifest_item(input_dir, video_path, {
                    "upload_status": "failed",
                    "last_error": err,
                })
                with LOCK:
                    upload_status["failed"] += 1
                    upload_status["results"].append({
                        "title": title, "bvid": "", "status": "failed",
                        "error": err,
                    })
        except Exception as e:
            err = str(e)[:200]
            _emit_log(f"  ❌ 异常: {err}", "error")
            if hit_406[0] or "406" in err:
                consecutive_406 += 1
                if consecutive_406 > MAX_406_RETRIES:
                    err = (f"B站限频(406)：冷却重试 {MAX_406_RETRIES} 次仍被拒，"
                           f"停止本批投稿，未投稿件保持待投稿状态，可稍后重试")
                    _emit_log(f"  🛑 {err}", "error")
                    update_upload_manifest_item(input_dir, video_path, {
                        "upload_status": "failed",
                        "last_error": err,
                    })
                    with LOCK:
                        upload_status["failed"] += 1
                        upload_status["results"].append({
                            "title": title, "bvid": "", "status": "failed",
                            "error": err,
                        })
                    break
                _emit_log(
                    f"  🕐 检测到 406（B站限频，约每小时20条），"
                    f"冷却 {UPLOAD_406_BACKOFF_SEC // 60} 分钟后重试当前稿件"
                    f"（第 {consecutive_406}/{MAX_406_RETRIES} 次）...", "warning")
                for _ in range(UPLOAD_406_BACKOFF_SEC):
                    if not upload_status["running"]:
                        break
                    time.sleep(1)
                if not upload_status["running"]:
                    break
                continue
            update_upload_manifest_item(input_dir, video_path, {
                "upload_status": "failed",
                "last_error": err,
            })
            with LOCK:
                upload_status["failed"] += 1
                upload_status["results"].append({
                    "title": title, "bvid": "", "status": "failed",
                    "error": err,
                })

        # 投稿间隔：逐稿最低间隔，避免触发 B站限频（约 20 条/小时）
        consecutive_406 = 0
        i += 1
        if i < len(selected_items) and upload_status["running"]:
            _emit_log(f"  ⏳ 等待 {UPLOAD_MIN_INTERVAL_SEC // 60} 分钟后投下一个（防限频）...", "info")
            for _ in range(UPLOAD_MIN_INTERVAL_SEC):
                if not upload_status["running"]:
                    break
                time.sleep(1)

    if not upload_status["running"] and i < len(selected_items):
        for item in selected_items[i:]:
            if item.get("upload_status") != "success":
                update_upload_manifest_item(input_dir, item.get("video_path", ""), {
                    "upload_status": "skipped",
                    "last_error": "用户中断，尚未投稿",
                })

    with LOCK:
        upload_status["running"] = False
        upload_status["current_title"] = ""

    s = upload_status
    _emit_log(f"\n{'='*50}", "info")
    _emit_log(f"📊 投稿完成: ✅{s['completed']} ❌{s['failed']} ⏭{s.get('skipped', 0)} / 共{s['total']}", "info")
    _broadcast_sse({"type": "upload_done"})


# ═══════════════════════════════════════════════════════════════
# HTTP Handler
# ═══════════════════════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    """RESTful API handler"""

    # HTTP/1.1 + keep-alive，解决移动端 fetch 频繁 "Failed to fetch"
    # （HTTP/1.0 默认每请求关连接，移动网络/Tailscale 中继下容易连接复用失败）
    protocol_version = "HTTP/1.1"

    # 关闭默认日志（减少噪音）
    def log_message(self, format, *args):
        pass

    # ── 辅助方法 ──

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return b""
        return self.rfile.read(length)

    def _read_json(self):
        body = self._read_body()
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))

    def _send_file(self, path, mime="application/octet-stream", filename=None):
        try:
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(size))
            if filename:
                self.send_header("Content-Disposition",
                                 f"attachment; filename*=UTF-8''{quote(filename)}")
            self.end_headers()
            # 分块读取，避免大文件占内存
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # ── OPTIONS (CORS preflight) ──

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── GET 路由 ──

    def do_GET(self):
        p = urlparse(self.path)
        path = unquote(p.path)
        qs = parse_qs(p.query)

        # ── 首页（前端 SPA）──
        if path == "/" or path == "/index.html":
            html = HTML.replace("__AUTOCLIP_BASES__", json.dumps(INPUT_BASES, ensure_ascii=False)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        # ── 文件管理 API ──

        if path == "/api/files":
            raw = qs.get("path", [INPUT_BASE])[0]
            safe = _safe_path(raw) or INPUT_BASE or PROJECT_ROOT
            if not os.path.isdir(safe):
                self._json({"error": "目录不存在"}, 404)
                return
            result = list_directory(safe)
            result["parent"] = self._get_parent(safe)
            self._json(result)
            return

        if path == "/api/files/download":
            raw = qs.get("path", [""])[0]
            safe = _safe_path(raw)
            if not safe or not os.path.isfile(safe):
                self._json({"error": "文件不存在或无权限"}, 404)
                return
            ext = os.path.splitext(safe)[1].lower()
            mime_map = {
                ".mp4": "video/mp4", ".mkv": "video/x-matroska",
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp",
                ".txt": "text/plain; charset=utf-8", ".json": "application/json",
                ".srt": "text/plain; charset=utf-8", ".ass": "text/plain; charset=utf-8",
                ".mp3": "audio/mpeg", ".wav": "audio/wav",
            }
            mime = mime_map.get(ext, "application/octet-stream")
            self._send_file(safe, mime, os.path.basename(safe))
            return

        if path == "/api/files/preview":
            raw = qs.get("path", [""])[0]
            safe = _safe_path(raw)
            if not safe or not os.path.isfile(safe):
                self._json({"error": "文件不存在或无权限"}, 404)
                return
            ext = os.path.splitext(safe)[1].lower()
            # 图片直接返回
            if ext in IMAGE_EXTS:
                mime = {".png": "image/png", ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg", ".gif": "image/gif",
                        ".webp": "image/webp", ".bmp": "image/bmp"}.get(ext, "image/jpeg")
                self._send_file(safe, mime)
                return
            # 文本类读取前 50KB
            if ext in TEXT_EXTS or ext in (".py", ".md"):
                try:
                    with open(safe, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(51200)
                    self._json({"type": "text", "content": content,
                                "truncated": os.path.getsize(safe) > 51200})
                except Exception as e:
                    self._json({"error": str(e)}, 500)
                return
            # 视频返回流式
            if ext in VIDEO_EXTS:
                mime = {".mp4": "video/mp4", ".mkv": "video/x-matroska",
                        ".webm": "video/webm"}.get(ext, "video/mp4")
                self._send_file(safe, mime)
                return
            self._json({"type": "binary", "ext": ext, "name": os.path.basename(safe)})
            return

        # ── 缩略图 ──

        if path == "/api/thumb":
            raw = qs.get("p", [""])[0]
            safe = _safe_path(raw)
            if not safe or not os.path.isfile(safe):
                self._json({"error": "not found"}, 404)
                return
            thumb = _make_thumb(safe)
            self._send_file(thumb, "image/jpeg")
            return

        # ── 投稿管理 API ──

        if path == "/api/uploads":
            raw_dir = qs.get("dir", [""])[0]
            if raw_dir:
                safe = _safe_path(raw_dir)
                if not safe or not os.path.isdir(safe):
                    self._json({"error": "目录不存在"}, 404)
                    return
                items = scan_upload_manifest(safe)
                self._json({"items": items, "dir": safe})
            else:
                # 返回各录播根目录下的主播列表
                streamers = []
                for base in INPUT_BASES:
                    if os.path.isdir(base):
                        for name in sorted(os.listdir(base)):
                            full = os.path.join(base, name)
                            if os.path.isdir(full):
                                streamers.append({"name": name, "path": full, "base": base})
                self._json({"streamers": streamers, "base": INPUT_BASE, "bases": INPUT_BASES})
            return

        if path == "/api/uploads/status":
            with LOCK:
                status = {
                    "running": upload_status["running"],
                    "total": upload_status["total"],
                    "completed": upload_status["completed"],
                    "failed": upload_status["failed"],
                    "skipped": upload_status.get("skipped", 0),
                    "current_title": upload_status["current_title"],
                    "current_progress": upload_status["current_progress"],
                    "results": list(upload_status["results"]),
                }
            self._json(status)
            return

        # ── 流水线 API ──

        if path == "/api/pipeline/status":
            with LOCK:
                self._json({
                    "running": pipeline_running,
                    "paused": pipeline_paused,
                    "step": pipeline_step,
                    "input_dir": pipeline_input_dir,
                    "log_count": len(pipeline_log),
                })
            return

        if path == "/api/session":
            self._json({
                "session": current_session,
                "log_tail": list(pipeline_log)[-100:],
            })
            return

        if path == "/api/streamers":
            streamers = []
            if os.path.isdir(CONFIG_DIR):
                for f in sorted(os.listdir(CONFIG_DIR)):
                    if f.endswith(".json"):
                        streamers.append({"name": f[:-5]})
            # 过滤掉在所有录播根目录下都没有实际文件夹的主播（如仅存配置的旧主播）
            existing = set()
            for base in INPUT_BASES:
                if os.path.isdir(base):
                    existing.update(os.listdir(base))
            streamers = [s for s in streamers if s["name"] in existing]
            self._json({"streamers": streamers, "bases": INPUT_BASES})
            return

        if path == "/api/folders":
            streamer = qs.get("streamer", [""])[0]
            if not streamer:
                self._json({"error": "缺少 streamer 参数"}, 400)
                return
            only_base = qs.get("base", [""])[0]
            if only_base:
                only_base = os.path.normpath(only_base)
            folders = []
            bases = []
            for base in INPUT_BASES:
                if not os.path.isdir(base):
                    continue
                if only_base and os.path.normpath(base) != only_base:
                    continue
                bases.append(base)
                base_dir = os.path.join(base, streamer)
                if not os.path.isdir(base_dir):
                    continue
                for name in sorted(os.listdir(base_dir)):
                    full = os.path.join(base_dir, name)
                    if os.path.isdir(full):
                        folders.append({"name": name, "path": full, "base": base})
            self._json({"folders": folders, "base": INPUT_BASE, "bases": bases})
            return

        # ── 视频时长查询（懒加载，不影响文件列表速度） ──

        if path == "/api/video/info":
            qp = parse_qs(p.query)
            file_path = unquote(qp.get("path", [""])[0])
            safe = _safe_path(file_path)
            if not safe or not os.path.isfile(safe):
                self._json({"error": "无效文件路径"}, 404)
                return
            ext = os.path.splitext(safe)[1].lower()
            if ext not in ('.mp4', '.mkv', '.flv', '.avi', '.mov', '.wmv', '.ts'):
                self._json({"error": "非视频文件"}, 400)
                return
            try:
                import subprocess as _sp
                r = _sp.run([
                    "ffprobe", "-v", "quiet",
                    "-show_entries", "format=duration",
                    "-of", "csv=p=0", safe,
                ], capture_output=True, text=True, timeout=5)
                dur = float(r.stdout.strip()) if r.stdout.strip() else 0
                mins = int(dur // 60)
                secs = int(dur % 60)
                self._json({"ok": True, "duration": dur, "display": f"{mins}:{secs:02d}"})
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        # ── SSE 日志流 ──

        if path == "/api/stream":
            self._handle_sse()
            return

        # ── 404 ──
        self._json({"error": f"unknown endpoint: {path}"}, 404)

    # ── POST 路由 ──

    def do_POST(self):
        global pipeline_running, pipeline_paused, pipeline_abort_requested, pipeline_step
        p = urlparse(self.path)
        path = unquote(p.path)

        # ── 文件上传 ──
        if path == "/api/files/upload":
            self._handle_file_upload()
            return

        # ── 投稿提交 ──
        if path == "/api/uploads/submit":
            data = self._read_json()
            input_dir = data.get("dir", "")
            safe = _safe_path(input_dir)
            if not safe:
                self._json({"error": "无效目录"}, 400)
                return
            indices = data.get("indices", [])
            items = scan_upload_manifest(safe)
            selected = [items[i] for i in indices if i < len(items)]
            if not selected:
                self._json({"error": "未选择有效项目"}, 400)
                return

            global upload_thread
            with LOCK:
                if upload_status["running"]:
                    self._json({"error": "已有投稿任务在运行"}, 409)
                    return
            upload_thread = threading.Thread(
                target=run_upload_batch, args=(safe, selected), daemon=True)
            upload_thread.start()
            self._json({"ok": True, "count": len(selected)})
            return

        # ── 投稿中断 ──
        if path == "/api/uploads/abort":
            with LOCK:
                upload_status["running"] = False
            self._json({"ok": True})
            return

        # ── 流水线启动 ──
        if path == "/api/pipeline/start":
            data = self._read_json()
            streamer = data.get("streamer", "")
            folder = data.get("folder", "")
            steps = data.get("steps", [1, 2, 3, 4, 5, 6])

            # 优先使用前端传入的精确目录，避免多根目录下同名主播/文件夹歧义
            input_dir = ""
            if data.get("dir"):
                input_dir = _safe_path(data.get("dir"))
                if input_dir and not os.path.isdir(input_dir):
                    input_dir = ""

            if not input_dir:
                if not streamer or not folder:
                    self._json({"error": "缺少 streamer 或 folder"}, 400)
                    return
                # 在多个录播根目录中查找主播目录（默认优先 INPUT_BASE）
                for base in INPUT_BASES:
                    cand = os.path.join(base, streamer, folder)
                    if os.path.isdir(cand):
                        input_dir = cand
                        break
                if not input_dir:
                    input_dir = os.path.join(INPUT_BASE, streamer, folder)
            if not os.path.isdir(input_dir):
                self._json({"error": f"目录不存在: {input_dir}"}, 404)
                return

            with LOCK:
                if pipeline_running:
                    self._json({"error": "已有流水线在运行"}, 409)
                    return
            t = threading.Thread(
                target=run_pipeline,
                args=(input_dir, steps, streamer, folder),
                daemon=True)
            t.start()
            _log_phone_command({
                "type": "pipeline_start",
                "streamer": streamer, "folder": folder,
                "input_dir": input_dir,
            })
            self._json({"ok": True, "input_dir": input_dir})
            return

        # ── 流水线中断 ──
        if path == "/api/pipeline/abort":
            with LOCK:
                proc = pipeline_proc
                pipeline_abort_requested = True
                was_running = pipeline_running
            if proc and proc.poll() is None:
                ok, message = terminate_process_tree(proc)
                if ok:
                    _emit_log(f"🛑 已发送中断信号: {message}", "warning")
                else:
                    _emit_log(f"❌ 中断失败: {message}", "error")
                    self._json({"error": message}, 500)
                    return
            elif not was_running:
                with LOCK:
                    pipeline_abort_requested = False
                self._json({"ok": True, "message": "没有正在运行的流水线"})
                return
            with LOCK:
                pipeline_running = False
                pipeline_paused = False
                pipeline_step = "aborted"
            _emit_log("🛑 流水线已中断", "warning")
            _broadcast_sse({"type": "pipeline_done"})
            self._json({"ok": True})
            return

        # ── 流水线暂停/恢复 ──
        if path == "/api/pipeline/pause":
            with LOCK:
                if not pipeline_running or pipeline_paused:
                    self._json({"error": "无法暂停"}, 400)
                    return
                proc = pipeline_proc
            if proc and proc.poll() is None:
                if suspend_process(proc.pid):
                    with LOCK:
                        pipeline_paused = True
                        pipeline_step = "(已暂停)"
                    _emit_log("⏸ 流水线已暂停", "warning")
                    _broadcast_sse({"type": "step", "step": "(已暂停)"})
                    self._json({"ok": True, "paused": True})
                else:
                    self._json({"error": "挂起进程失败"}, 500)
            else:
                self._json({"error": "进程已结束"}, 400)
            return

        if path == "/api/pipeline/resume":
            with LOCK:
                if not pipeline_paused:
                    self._json({"error": "未暂停"}, 400)
                    return
                proc = pipeline_proc
            if proc and proc.poll() is None:
                if resume_process(proc.pid):
                    with LOCK:
                        pipeline_paused = False
                        pipeline_step = "(已恢复)"
                    _emit_log("▶ 流水线已恢复", "info")
                    _broadcast_sse({"type": "step", "step": "(已恢复)"})
                    self._json({"ok": True, "paused": False})
                else:
                    self._json({"error": "恢复进程失败"}, 500)
            else:
                self._json({"error": "进程已结束"}, 400)
            return

        self._json({"error": f"unknown endpoint: {path}"}, 404)

    # ── PUT 路由 ──

    def do_PUT(self):
        p = urlparse(self.path)
        path = unquote(p.path)

        # ── 编辑投稿清单项 ──
        if path.startswith("/api/uploads/"):
            try:
                idx = int(path.split("/")[-1])
            except ValueError:
                self._json({"error": "无效索引"}, 400)
                return
            data = self._read_json()
            input_dir = data.pop("_dir", "")
            safe = _safe_path(input_dir)
            if not safe:
                self._json({"error": "无效目录"}, 400)
                return
            items = scan_upload_manifest(safe)
            if idx < 0 or idx >= len(items):
                self._json({"error": "索引超出范围"}, 400)
                return
            # 更新可编辑字段
            for field in ("title", "desc", "tags", "tid", "copyright"):
                if field in data:
                    items[idx][field] = data[field]
            save_upload_manifest(safe, items)
            self._json({"ok": True, "item": items[idx]})
            return

        self._json({"error": f"unknown endpoint: {path}"}, 404)

    # ── 辅助：获取父目录（如果在允许范围内）──

    def _get_parent(self, path):
        parent = os.path.dirname(path)
        if parent == path:
            return None
        if _safe_path(parent):
            return parent
        return None

    # ── 文件上传处理 (multipart/form-data) ──

    def _handle_file_upload(self):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self._json({"error": "需要 multipart/form-data"}, 400)
            return

        # 解析 boundary
        try:
            # 用 cgi 模块解析 multipart（简单可靠）
            environ = {
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers["Content-Type"],
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            }
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ=environ,
            )

            target_dir = form.getvalue("dir", INPUT_BASE)
            safe_dir = _safe_path(target_dir) or INPUT_BASE or PROJECT_ROOT
            if not os.path.isdir(safe_dir):
                os.makedirs(safe_dir, exist_ok=True)

            uploaded = []
            files = form["files"] if "files" in form else []
            if not isinstance(files, list):
                files = [files]

            for item in files:
                if not item.filename:
                    continue
                # 安全文件名：去掉路径分隔符
                fname = os.path.basename(item.filename)
                dest = os.path.join(safe_dir, fname)
                # 写入文件
                with open(dest, "wb") as f:
                    while True:
                        chunk = item.file.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                uploaded.append({
                    "name": fname,
                    "size": os.path.getsize(dest),
                })

            self._json({"ok": True, "uploaded": uploaded, "dir": safe_dir})

        except Exception as e:
            self._json({"error": str(e)}, 500)

    # ── SSE 日志流 ──

    def _handle_sse(self):
        self.close_connection = False  # 保持连接
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        # 注册客户端
        with sse_lock:
            sse_clients.add(self.wfile)

        # 发送最近的日志作为初始快照
        try:
            recent = list(pipeline_log)[-50:]
            for entry in recent:
                msg = f"data: {json.dumps({'type': 'log', **entry}, ensure_ascii=False)}\n\n"
                self.wfile.write(msg.encode("utf-8"))
            # 发送当前状态
            with LOCK:
                state = {
                    "type": "status",
                    "running": pipeline_running,
                    "paused": pipeline_paused,
                    "step": pipeline_step,
                    "input_dir": pipeline_input_dir,
                }
            msg = f"data: {json.dumps(state, ensure_ascii=False)}\n\n"
            self.wfile.write(msg.encode("utf-8"))
            self.wfile.flush()

            # 保持连接（心跳保活，短间隔防止 Tailscale/移动网络断连）
            while True:
                time.sleep(5)
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # 客户端断开（移动网络切换/休眠常见），静默退出
            pass
        except Exception:
            pass
        finally:
            with sse_lock:
                sse_clients.discard(self.wfile)


# ═══════════════════════════════════════════════════════════════
# 前端 SPA (嵌入 HTML)
# ═══════════════════════════════════════════════════════════════

HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0d1b2a">
<title>AutoClip Console</title>
<style>
:root{
  --bg:#0d1b2a;--bg2:#13293d;--card:#1b3a5b;--card2:#234870;
  --border:#2a5a8a;--text:#e8f0fa;--muted:#8aa4c0;
  --accent:#00b4d8;--accent2:#0077b6;
  --green:#4ade80;--red:#f87171;--amber:#fbbf24;--blue:#60a5fa;
  --safe-top:env(safe-area-inset-top,0px);
  --safe-bottom:env(safe-area-inset-bottom,0px);
}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{
  font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;
  background:linear-gradient(180deg,var(--bg) 0%,var(--bg2) 100%);
  color:var(--text);min-height:100dvh;
  padding-top:var(--safe-top);padding-bottom:calc(64px + var(--safe-bottom));
  -webkit-font-smoothing:antialiased;overflow-x:hidden;
}

/* ── 顶部标题栏 ── */
.header{
  background:linear-gradient(135deg,var(--accent2),var(--accent));
  padding:14px 20px;text-align:center;
  position:sticky;top:0;z-index:50;
  box-shadow:0 2px 12px rgba(0,119,182,0.3);
}
.header h1{font-size:18px;font-weight:700;color:#fff;letter-spacing:1px}
.header .sub{font-size:11px;color:rgba(255,255,255,0.7);margin-top:2px}

/* ── 底部导航 ── */
.tabbar{
  position:fixed;bottom:0;left:0;right:0;z-index:100;
  display:flex;background:var(--card);border-top:1px solid var(--border);
  padding-bottom:var(--safe-bottom);
}
.tab{
  flex:1;padding:10px 0;text-align:center;cursor:pointer;
  color:var(--muted);font-size:11px;transition:color .2s;
}
.tab .icon{font-size:22px;display:block;margin-bottom:3px}
.tab.active{color:var(--accent)}
.tab.active .icon{transform:scale(1.1)}

/* ── 视图容器 ── */
.view{display:none;padding:16px;animation:fadeIn .2s}
.view.active{display:block}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}

/* ── 卡片 ── */
.card{
  background:var(--card);border:1px solid var(--border);
  border-radius:12px;padding:16px;margin-bottom:12px;
  transition:border-color .2s;
}
.card:active{border-color:var(--accent)}
.card-title{font-size:14px;font-weight:600;margin-bottom:8px;color:var(--text)}
.card-meta{font-size:12px;color:var(--muted)}

/* ── 按钮 ── */
.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:6px;
  padding:12px 20px;border:none;border-radius:10px;
  font-size:14px;font-weight:600;cursor:pointer;
  transition:opacity .2s,transform .1s;min-height:44px;
}
.btn:active{transform:scale(0.97)}
.btn-primary{background:linear-gradient(135deg,var(--accent2),var(--accent));color:#fff}
.btn-danger{background:linear-gradient(135deg,#dc2626,#ef4444);color:#fff}
.btn-ghost{background:var(--card2);color:var(--text);border:1px solid var(--border)}
.btn-block{display:flex;width:100%}
.btn-sm{padding:8px 14px;font-size:12px;min-height:36px}

/* ── 输入框 ── */
.input,.textarea,.select{
  width:100%;padding:12px 14px;
  background:var(--bg2);border:1px solid var(--border);
  border-radius:8px;color:var(--text);font-size:14px;
  transition:border-color .2s;
}
.input:focus,.textarea:focus,.select:focus{border-color:var(--accent);outline:none}
.textarea{min-height:80px;resize:vertical;font-family:inherit}
.label{display:block;font-size:12px;color:var(--muted);margin-bottom:6px;margin-top:12px}

/* ── 文件列表 ── */
.file-item{
  display:flex;align-items:center;gap:12px;
  padding:14px;background:var(--card);border:1px solid var(--border);
  border-radius:10px;margin-bottom:8px;cursor:pointer;
  transition:background .2s,border-color .2s;
}
.file-item:active{background:var(--card2)}
.file-icon{font-size:24px;flex-shrink:0}
.file-info{flex:1;min-width:0}
.file-name{font-size:14px;font-weight:500;word-break:break-all;line-height:1.4}
.file-meta{font-size:11px;color:var(--muted);margin-top:2px}
.vid-dur{color:var(--accent);cursor:pointer;font-size:11px}
.file-actions{display:flex;gap:6px;flex-shrink:0}

/* ── 文件导航栏 ── */
.files-nav{
  display:flex;align-items:center;gap:6px;
  padding:8px 10px;background:var(--card);border-radius:8px;
  margin-bottom:12px;
}
.files-nav .breadcrumb{
  flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;
  padding:0;margin:0;background:none;font-size:12px;color:var(--muted);
}
.files-nav .breadcrumb a{color:var(--accent);cursor:pointer;text-decoration:none}
.files-nav .breadcrumb .sep{color:var(--border);margin:0 2px}

/* ── 投稿卡片 ── */
.upload-card{
  background:var(--card);border:1px solid var(--border);
  border-radius:12px;margin-bottom:12px;overflow:hidden;
}
.upload-card.selected{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.upload-card-header{
  display:flex;align-items:center;gap:12px;padding:12px;
}
.upload-thumb{
  width:64px;height:40px;border-radius:6px;object-fit:cover;
  background:var(--bg2);flex-shrink:0;
}
.upload-thumb-placeholder{
  width:64px;height:40px;border-radius:6px;
  background:var(--bg2);display:flex;align-items:center;justify-content:center;
  font-size:20px;flex-shrink:0;
}
.upload-info{flex:1;min-width:0}
.upload-title{font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.upload-meta{font-size:11px;color:var(--muted);margin-top:2px}
.upload-state{font-size:11px;margin-top:4px;font-weight:700}
.upload-state.success{color:var(--green)}
.upload-state.failed{color:var(--red)}
.upload-state.uploading{color:var(--accent2)}
.upload-state.pending{color:var(--yellow)}
.upload-state.skipped{color:var(--muted)}
.upload-checkbox.disabled{opacity:.35;cursor:not-allowed}
.upload-checkbox{
  width:22px;height:22px;border:2px solid var(--border);border-radius:6px;
  display:flex;align-items:center;justify-content:center;flex-shrink:0;
  cursor:pointer;transition:all .2s;
}
.upload-checkbox.checked{background:var(--accent);border-color:var(--accent)}
.upload-checkbox.checked::after{content:'✓';color:#fff;font-size:14px;font-weight:bold}
.upload-card-body{padding:0 12px 12px;display:none}
.upload-card.expanded .upload-card-body{display:block}
.tag-chip{
  display:inline-block;padding:4px 10px;margin:2px;
  background:var(--card2);border:1px solid var(--border);
  border-radius:12px;font-size:11px;color:var(--muted);
}
.tag-chip .remove{margin-left:4px;cursor:pointer;color:var(--red)}

/* ── 进度条 ── */
.progress-bar{
  height:8px;background:var(--bg2);border-radius:4px;overflow:hidden;
}
.progress-fill{
  height:100%;background:linear-gradient(90deg,var(--accent2),var(--accent));
  border-radius:4px;transition:width .3s;
}

/* ── 日志 ── */
.log-container{
  background:#0a1520;border:1px solid var(--border);
  border-radius:10px;padding:12px;
  font-family:'SF Mono','Consolas',monospace;font-size:12px;
  max-height:60vh;overflow-y:auto;
  -webkit-overflow-scrolling:touch;
}
.log-line{padding:2px 0;word-break:break-all;line-height:1.5}
.log-line .ts{color:var(--muted);margin-right:6px}
.log-line.info{color:var(--text)}
.log-line.success{color:var(--green)}
.log-line.error{color:var(--red)}
.log-line.warning{color:var(--amber)}

/* ── 状态徽章 ── */
.badge{
  display:inline-flex;align-items:center;gap:4px;
  padding:4px 10px;border-radius:10px;font-size:11px;font-weight:600;
}
.badge-running{background:rgba(0,180,216,0.2);color:var(--accent)}
.badge-success{background:rgba(74,222,128,0.2);color:var(--green)}
.badge-error{background:rgba(248,113,113,0.2);color:var(--red)}
.badge-idle{background:rgba(138,164,192,0.15);color:var(--muted)}
.badge-paused{background:rgba(251,191,36,0.2);color:var(--amber)}

/* ── 空状态 ── */
.empty{text-align:center;padding:40px 20px;color:var(--muted)}
.empty .icon{font-size:48px;margin-bottom:12px;opacity:0.5}

/* ── 模态框 ── */
.modal-overlay{
  position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:200;
  display:none;align-items:flex-end;justify-content:center;
}
.modal-overlay.active{display:flex;animation:fadeIn .2s}
.modal{
  background:var(--bg2);border-radius:16px 16px 0 0;
  width:100%;max-width:600px;max-height:80vh;overflow-y:auto;
  padding:20px;padding-bottom:calc(20px + var(--safe-bottom));
  animation:slideUp .25s;
}
@keyframes slideUp{from{transform:translateY(100%)}to{transform:none}}

/* ── 开关 ── */
.step-chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{
  padding:7px 12px;border:1px solid var(--border);border-radius:18px;
  font-size:12px;cursor:pointer;transition:all .15s;color:var(--muted);
  user-select:none;-webkit-user-select:none;
}
.chip:active{transform:scale(.96)}
.chip.active{background:var(--accent);border-color:var(--accent);color:#fff}

/* ── Toast ── */
.toast{
  position:fixed;bottom:80px;left:50%;transform:translateX(-50%);
  background:var(--card2);color:var(--text);
  padding:12px 24px;border-radius:24px;font-size:13px;
  z-index:300;box-shadow:0 4px 20px rgba(0,0,0,0.4);
  animation:toastIn .3s,toastOut .3s 2.7s forwards;
}
@keyframes toastIn{from{opacity:0;transform:translate(-50%,20px)}to{opacity:1;transform:translateX(-50%)}}
@keyframes toastOut{to{opacity:0;transform:translate(-50%,20px)}}
</style>
</head>
<body>

<div class="header">
  <h1>AutoClip Console</h1>
  <div class="sub" id="header-sub">Tailscale 远程控制台</div>
</div>

<!-- ════════ 文件管理视图 ════════ -->
<div class="view active" id="view-files">
  <!-- 导航栏：返回 + 路径 + 排序 + 上传 -->
  <div class="files-nav">
    <button class="btn btn-ghost btn-sm" id="btn-files-back" onclick="fileGoBack()" style="flex-shrink:0">⬅</button>
    <div class="breadcrumb" id="breadcrumb"></div>
    <select class="select" id="file-base" style="flex-shrink:0;max-width:180px" onchange="browseFiles(this.value)">
      <option value="">…</option>
    </select>
    <button class="btn btn-ghost btn-sm" id="btn-sort" onclick="toggleFileSort()" style="flex-shrink:0" title="排序: 默认/正序↑/倒序↓">🔤↓</button>
    <button class="btn btn-ghost btn-sm" onclick="uploadPicker.click()" style="flex-shrink:0">⬆</button>
    <input type="file" id="uploadPicker" multiple style="display:none" onchange="uploadFiles(this.files)">
  </div>
  <div id="file-list">
    <div class="empty"><div class="icon">📁</div>加载中...</div>
  </div>
</div>

<!-- ════════ 投稿管理视图 ════════ -->
<div class="view" id="view-uploads">
  <div class="card">
    <div class="card-title">📂 选择录播目录</div>
    <label class="label">录播根目录</label>
    <select class="select" id="up-base" onchange="onBaseChange()"></select>
    <label class="label">主播</label>
    <select class="select" id="up-streamer" onchange="loadFolders()"></select>
    <label class="label">录播文件夹</label>
    <select class="select" id="up-folder" onchange="loadManifest()"></select>
    <button class="btn btn-primary btn-block" style="margin-top:12px"
            onclick="loadManifest()">📋 加载投稿清单</button>
  </div>

  <div id="upload-status-bar" style="display:none">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span id="up-status-text">投稿中...</span>
        <span id="up-status-count">0/0</span>
      </div>
      <div class="progress-bar" style="margin-top:8px">
        <div class="progress-fill" id="up-progress" style="width:0%"></div>
      </div>
    </div>
  </div>

  <div id="upload-list">
    <div class="empty"><div class="icon">📤</div>选择主播和文件夹后加载投稿清单</div>
  </div>

  <div id="upload-actions" style="display:none;margin-top:12px">
    <button class="btn btn-primary btn-block" onclick="submitUploads()">
      🚀 投稿选中项 (<span id="sel-count">0</span>)
    </button>
  </div>
</div>

<!-- ════════ 实时日志视图 ════════ -->
<div class="view" id="view-logs">

  <!-- 状态条 -->
  <div class="card" style="padding:10px 12px">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <div style="display:flex;align-items:center;gap:8px;min-width:0">
        <span id="pipe-status-badge" class="badge badge-idle">● 空闲</span>
        <span id="pipe-step-text" style="font-size:12px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>
      </div>
      <div style="display:flex;gap:6px;flex-shrink:0">
        <button class="btn btn-ghost btn-sm" id="btn-pause" onclick="togglePause()">⏸</button>
        <button class="btn btn-danger btn-sm" onclick="abortPipeline()">⏹</button>
      </div>
    </div>
    <div id="pipe-cwd-text" style="font-size:11px;color:var(--muted);margin-top:6px;word-break:break-all;line-height:1.4">📁 未启动</div>
  </div>

  <!-- 日志输出 -->
  <div style="margin-top:10px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <span style="font-size:13px;color:var(--muted)">📋 日志</span>
      <button class="btn btn-ghost btn-sm" onclick="clearLogs()">🗑 清空</button>
    </div>
    <div class="log-container" id="log-container">
      <div class="log-line" style="color:var(--muted)">等待日志...</div>
    </div>
  </div>
</div>

<!-- ════════ 一键流转视图 ════════ -->
<div class="view" id="view-pipeline">
  <!-- 运行目录 + 步骤选择 合并卡片 -->
  <div class="card" style="padding:10px 12px">
    <!-- 运行目录（从投稿页读取） -->
    <div class="card-title" style="padding:0 0 6px;font-size:13px">📍 运行目录</div>
    <div id="picker-info" style="font-size:12px;color:var(--muted);margin-bottom:8px;padding:6px 8px;background:var(--surface);border-radius:6px">
      <span id="picker-text">未选择（请到投稿页选主播/文件夹）</span>
    </div>

    <!-- 步骤选择 -->
    <div class="card-title" style="padding:0 0 6px;font-size:13px;margin-top:4px">⚙️ 步骤</div>
    <div class="step-chips" id="step-chips"></div>

    <!-- 一键流转大按钮 -->
    <button class="btn btn-primary btn-block" id="btn-run-pipeline"
            style="margin-top:10px;padding:14px;font-size:15px;font-weight:600"
            onclick="startPipeline()">🚀 一键流转</button>
  </div>

  <!-- 状态条（流转时复制一份） -->
  <div class="card" style="padding:10px 12px" id="pipeline-status-card">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <div style="display:flex;align-items:center;gap:8px;min-width:0">
        <span id="pipe-status-badge-2" class="badge badge-idle">● 空闲</span>
        <span id="pipe-step-text-2" style="font-size:12px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>
      </div>
      <div style="display:flex;gap:6px;flex-shrink:0">
        <button class="btn btn-ghost btn-sm" onclick="switchTab('logs')">📊 查看日志</button>
      </div>
    </div>
    <div id="pipe-cwd-text-2" style="font-size:11px;color:var(--muted);margin-top:6px;word-break:break-all;line-height:1.4">📁 未启动</div>
  </div>
</div>

<!-- ════════ 设置视图 ════════ -->
<div class="view" id="view-settings">
  <div class="card">
    <div class="card-title">🔗 服务信息</div>
    <div class="card-meta">
      <div>项目根目录: <code id="set-root">...</code></div>
      <div>录播根目录: <code id="set-input">...</code></div>
      <div>当前时间: <code id="set-time">...</code></div>
    </div>
  </div>
  <div class="card">
    <div class="card-title">📖 使用说明</div>
    <div style="font-size:13px;line-height:1.8;color:var(--muted)">
      <p>📁 <b>文件管理</b>：浏览录播目录，支持上传/下载文件</p>
      <p>📤 <b>投稿管理</b>：选择主播和文件夹，编辑投稿信息后批量投稿</p>
      <p>🚀 <b>一键流转</b>：选择运行目录和步骤，启动自动化流水线</p>
      <p>📊 <b>实时日志</b>：查看流水线运行日志和状态</p>
      <p>⚙️ <b>设置</b>：查看服务信息</p>
      <p style="margin-top:8px;color:var(--accent)">通过 Tailscale 内网访问，确保手机和电脑在同一 Tailscale 网络。</p>
    </div>
  </div>
  <button class="btn btn-danger btn-block" onclick="if(confirm('确定停止服务器？')){fetch('/api/pipeline/abort',{method:'POST'});setTimeout(()=>location.reload(),500)}">
    🔄 重启服务
  </button>
</div>

<!-- ════════ 底部导航 ════════ -->
<div class="tabbar">
  <div class="tab active" onclick="switchTab('files')">
    <span class="icon">📁</span>文件
  </div>
  <div class="tab" onclick="switchTab('uploads')">
    <span class="icon">📤</span>投稿
  </div>
  <div class="tab" onclick="switchTab('pipeline')">
    <span class="icon">🚀</span>流转
  </div>
  <div class="tab" onclick="switchTab('logs')">
    <span class="icon">📊</span>日志
  </div>
  <div class="tab" onclick="switchTab('settings')">
    <span class="icon">⚙️</span>设置
  </div>
</div>

<!-- ════════ 编辑投稿模态框 ════════ -->
<div class="modal-overlay" id="edit-modal">
  <div class="modal">
    <h3 style="margin-bottom:16px">编辑投稿信息</h3>
    <label class="label">标题</label>
    <input class="input" id="edit-title">
    <label class="label">简介</label>
    <textarea class="textarea" id="edit-desc"></textarea>
    <label class="label">标签（逗号分隔，≤10个）</label>
    <input class="input" id="edit-tags">
    <label class="label">分区 ID</label>
    <input class="input" id="edit-tid" type="number" value="27">
    <label class="label">版权</label>
    <select class="select" id="edit-copyright">
      <option value="1">原创</option>
      <option value="2">转载</option>
    </select>
    <div style="display:flex;gap:8px;margin-top:16px">
      <button class="btn btn-ghost" style="flex:1" onclick="closeEditModal()">取消</button>
      <button class="btn btn-primary" style="flex:1" onclick="saveEdit()">保存</button>
    </div>
  </div>
</div>

<script>
// ═══════════════════════════════════════════════════════════════
// 全局状态
// ═══════════════════════════════════════════════════════════════
let currentPath = '';
let manifestItems = [];
let manifestDir = '';
let selectedIndices = new Set();
let editingIndex = -1;
let selectedSteps = new Set([1,2,3,4,5,6]);
let sseSource = null;
let pipeRunning = false;
let pipePaused = false;
let uploadWasRunning = false;

const STEPS = [
  {id:1,name:'准备',sub:[]},
  {id:2,name:'字幕',sub:[]},
  {id:3,name:'高光',sub:[]},   // 缓冲已内置在 generate_clips_combined.py 步骤3中
  {id:4,name:'切片',sub:[]},
  {id:5,name:'烧录',sub:[]},
  {id:6,name:'封面',sub:[]},
];

// ═══════════════════════════════════════════════════════════════
// Tab 切换
// ═══════════════════════════════════════════════════════════════
function switchTab(name,ev){
  document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('view-'+name).classList.add('active');
  // 优先用传入的事件对象，否则按 name 查找对应 tab
  if(ev&&ev.currentTarget){ev.currentTarget.classList.add('active');}
  else{
    document.querySelectorAll('.tab').forEach(t=>{
      if(t.getAttribute('onclick')&&t.getAttribute('onclick').indexOf("'"+name+"'")>=0){
        t.classList.add('active');
      }
    });
  }
  if(name==='uploads')initUploads();
  if(name==='pipeline')initPipeline();
  if(name==='logs')initLogs();
  if(name==='settings')initSettings();
}

// ═══════════════════════════════════════════════════════════════
// Toast
// ═══════════════════════════════════════════════════════════════
function toast(msg){
  const t=document.createElement('div');
  t.className='toast';t.textContent=msg;
  document.body.appendChild(t);
  setTimeout(()=>t.remove(),3000);
}

// ═══════════════════════════════════════════════════════════════
// 文件管理
// ═══════════════════════════════════════════════════════════════
let fileSort=2;  // 0=默认(夹在前), 1=名称正序, 2=名称倒序(默认)

function toggleFileSort(){
  // 循环: 0(默认) → 1(正序) → 2(倒序) → 0
  fileSort=(fileSort+1)%3;
  const labels=['🔤','🔤↑','🔤↓'];
  document.getElementById('btn-sort').textContent=labels[fileSort];
  browseFiles(currentPath);
}

async function browseFiles(path){
  // 首次加载时填充根目录切换下拉框
  const baseSel=document.getElementById('file-base');
  if(baseSel&&baseSel.options.length<=1){
    try{
      const br=await fetch('/api/streamers');
      const bd=await br.json();
      (bd.bases||[]).forEach(b=>{
        const short=(b||'').replace(/\\/g,'/').split('/').pop()||b;
        baseSel.innerHTML+=`<option value="${esc(b)}">${esc(short)}</option>`;
      });
    }catch(e){}
  }
  const r=await fetch('/api/files?path='+encodeURIComponent(path||''));
  const d=await r.json();
  if(d.error){toast(d.error);return;}
  currentPath=d.path;
  // 根目录下拉同步当前路径
  if(baseSel){
    let matched='';
    (d.bases||[]).forEach(b=>{
      if(d.path===b||d.path.indexOf(b+'\\')===0)matched=b;
    });
    baseSel.value=matched;
  }
  renderBreadcrumb(d.path,d.parent);
  const list=document.getElementById('file-list');
  if(!d.folders.length&&!d.files.length){
    list.innerHTML='<div class="empty"><div class="icon">📂</div>空目录</div>';
    return;
  }
  // 排序
  if(fileSort>0){
    const cmp=(a,b)=>a.name.localeCompare(b.name,'zh');
    d.folders.sort(fileSort===1?cmp:(a,b)=>cmp(b,a));
    d.files.sort(fileSort===1?cmp:(a,b)=>cmp(b,a));
  }
  let html='';
  d.folders.forEach(f=>{
    const fp=currentPath+'\\'+f.name;
    html+=`<div class="file-item" data-path="${esc(fp)}" onclick="browseFiles(this.dataset.path)">
      <span class="file-icon">${f.icon}</span>
      <div class="file-info"><div class="file-name">${esc(f.name)}</div>
      <div class="file-meta">${f.modified}</div></div>
    </div>`;
  });
  d.files.forEach(f=>{
    const fp=currentPath+'\\'+f.name;
    const canPreview=['.mp4','.mkv','.png','.jpg','.jpeg','.gif','.webp','.txt','.json','.srt','.ass','.md','.py'].includes(f.ext);
    const isVideo=['.mp4','.mkv','.flv','.avi','.mov','.wmv','.ts'].includes(f.ext);
    html+=`<div class="file-item">
      <span class="file-icon">${f.icon}</span>
      <div class="file-info"><div class="file-name">${esc(f.name)}</div>
      <div class="file-meta">${f.size} · ${f.modified}${isVideo?` · <span class="vid-dur" data-path="${esc(fp)}" onclick="event.stopPropagation();fetchVideoDuration(this)">⏳</span>`:''}</div></div>
      <div class="file-actions">
        ${canPreview?`<button class="btn btn-ghost btn-sm" data-path="${esc(fp)}" onclick="previewFile(this.dataset.path)">👁</button>`:''}
        <button class="btn btn-ghost btn-sm" data-path="${esc(fp)}" onclick="downloadFile(this.dataset.path)">⬇</button>
      </div>
    </div>`;
  });
  list.innerHTML=html;
  document.getElementById('sel-count').textContent=selectedIndices.size;
}

function fileGoBack(){
  if(currentPath){
    const parent=currentPath.split('\\').slice(0,-1).join('\\')||'';
    browseFiles(parent);
  }
}

async function fetchVideoDuration(el){
  const path=el.dataset.path;
  el.textContent='⏳';
  el.style.cursor='default';
  try{
    const r=await fetch('/api/video/info?path='+encodeURIComponent(path));
    const d=await r.json();
    if(d.ok){
      el.textContent=d.display;
    }else{
      el.textContent='?';
    }
  }catch(e){
    el.textContent='?';
  }
}

function renderBreadcrumb(path,parent){
  const bc=document.getElementById('breadcrumb');
  // 简洁路径：根 → ... → 当前目录名
  const parts=path.split('\\').filter(Boolean);
  const current=parts[parts.length-1]||'录播根目录';
  let display=current;
  if(parts.length>1){
    display=`<a onclick="browseFiles('')">根</a><span class="sep">/</span>`+current;
  }
  bc.innerHTML=display;
}

function downloadFile(path){
  window.open('/api/files/download?path='+encodeURIComponent(path),'_blank');
}

async function previewFile(path){
  const ext=path.split('.').pop().toLowerCase();
  if(['mp4','mkv'].includes(ext)){
    window.open('/api/files/preview?path='+encodeURIComponent(path),'_blank');
    return;
  }
  if(['png','jpg','jpeg','gif','webp','bmp'].includes(ext)){
    window.open('/api/files/preview?path='+encodeURIComponent(path),'_blank');
    return;
  }
  // 文本预览
  const r=await fetch('/api/files/preview?path='+encodeURIComponent(path));
  const d=await r.json();
  if(d.error){toast(d.error);return;}
  if(d.type==='text'){
    const modal=document.getElementById('edit-modal');
    modal.querySelector('.modal').innerHTML=`
      <h3 style="margin-bottom:12px">${esc(path.split('\\\\').pop())}</h3>
      <pre style="background:#0a1520;padding:12px;border-radius:8px;font-size:12px;overflow-x:auto;max-height:50vh;color:var(--text)">${esc(d.content)}</pre>
      ${d.truncated?'<p style="color:var(--muted);font-size:12px;margin-top:8px">⚠ 文件过大，仅显示前50KB</p>':''}
      <button class="btn btn-primary btn-block" style="margin-top:12px" onclick="closeEditModal()">关闭</button>
    `;
    modal.classList.add('active');
  }
}

async function uploadFiles(files){
  if(!files||!files.length)return;
  if(!currentPath){toast('请先选择目录');return;}
  const fd=new FormData();
  fd.append('dir',currentPath);
  for(const f of files)fd.append('files',f);
  toast('上传中...');
  const r=await fetch('/api/files/upload',{method:'POST',body:fd});
  const d=await r.json();
  if(d.ok){
    toast(`✅ 上传 ${d.uploaded.length} 个文件`);
    browseFiles(currentPath);
  }else{
    toast('❌ '+d.error);
  }
  document.getElementById('uploadPicker').value='';
}

// ═══════════════════════════════════════════════════════════════
// 投稿管理
// ═══════════════════════════════════════════════════════════════
async function initUploads(){
  if(document.getElementById('up-streamer').children.length)return;
  const r=await fetch('/api/streamers');
  const d=await r.json();
  const sel=document.getElementById('up-streamer');
  sel.innerHTML='<option value="">选择主播...</option>';
  d.streamers.forEach(s=>sel.innerHTML+=`<option value="${s.name}">${s.name}</option>`);
  // 根目录下拉：区分主目录与 NAS 备份目录
  const baseSel=document.getElementById('up-base');
  if(baseSel){
    baseSel.innerHTML='<option value="">全部根目录</option>';
    (d.bases||[]).forEach(b=>{
      const short=(b||'').replace(/\\/g,'/').split('/').pop()||b;
      baseSel.innerHTML+=`<option value="${esc(b)}">${esc(short)}</option>`;
    });
  }
  // 轮询投稿状态
  pollUploadStatus();
}

function onBaseChange(){
  const folderSel=document.getElementById('up-folder');
  if(folderSel)folderSel.innerHTML='<option value="">选择文件夹...</option>';
  if(document.getElementById('up-streamer').value)loadFolders();
  refreshPickerInfo();
}

async function loadFolders(){
  const streamer=document.getElementById('up-streamer').value;
  if(!streamer)return;
  const baseSel=document.getElementById('up-base');
  const base=baseSel?baseSel.value:'';
  const url='/api/folders?streamer='+encodeURIComponent(streamer)+(base?'&base='+encodeURIComponent(base):'');
  const r=await fetch(url);
  const d=await r.json();
  const sel=document.getElementById('up-folder');
  sel.innerHTML='<option value="">选择文件夹...</option>';
  d.folders.forEach(f=>{
    const label=f.base&&base?'':(`[${(f.base||'').replace(/\\/g,'/').split('/').pop()}] `);
    sel.innerHTML+=`<option value="${f.name}" data-path="${esc(f.path)}" data-base="${esc(f.base||'')}">${label}${f.name}</option>`;
  });
  refreshPickerInfo();
}

function selectedFolderPath(){
  const sel=document.getElementById('up-folder');
  if(!sel||!sel.selectedIndex)return '';
  return sel.options[sel.selectedIndex].getAttribute('data-path')||'';
}

async function loadManifest(){
  const streamer=document.getElementById('up-streamer').value;
  const folder=document.getElementById('up-folder').value;
  refreshPickerInfo();
  if(!streamer||!folder)return;
  const dir=streamer+'\\\\'+folder;
  const AUTOCLIP_BASES=__AUTOCLIP_BASES__;const BASE0=AUTOCLIP_BASES[0]||'';
  const inputDir=selectedFolderPath()||((BASE0?BASE0+'\\':'')+dir);
  const r=await fetch('/api/uploads?dir='+encodeURIComponent(inputDir));
  const d=await r.json();
  if(d.error){toast(d.error);return;}
  manifestItems=d.items||[];
  manifestDir=d.dir;
  selectedIndices.clear();
  manifestItems.forEach((item,i)=>{
    if((item.upload_status||'pending')!=='success')selectedIndices.add(i);
  });
  renderManifest();
  document.getElementById('upload-actions').style.display='block';
}

function renderManifest(){
  const list=document.getElementById('upload-list');
  if(!manifestItems.length){
    list.innerHTML='<div class="empty"><div class="icon">📭</div>未找到投稿清单<br><small>请先运行流水线生成切片</small></div>';
    return;
  }
  let html='';
  manifestItems.forEach((item,i)=>{
    const sel=selectedIndices.has(i)?'selected':'';
    const chk=selectedIndices.has(i)?'checked':'';
    const status=item.upload_status||'pending';
    const statusText={pending:'待投稿',uploading:'投稿中',success:'已投稿',failed:'投稿失败',skipped:'未投稿/已中断'}[status]||status;
    const disabled='';
    const cover=item.thumb_path?`<img class="upload-thumb" src="/api/thumb?p=${encodeURIComponent(item.cover_path)}">`
      :`<div class="upload-thumb-placeholder">🎬</div>`;
    const tagsHtml=(item.tags||[]).map(t=>`<span class="tag-chip">${esc(t)}</span>`).join('');
    const attempts=parseInt(item.upload_attempts||0);
    const bvid=item.bvid?` · BV:${esc(item.bvid)}`:'';
    const err=(item.last_error&&(status==='failed'||status==='skipped'))?` · ${esc(String(item.last_error).slice(0,90))}`:'';
    html+=`<div class="upload-card ${sel}" id="card-${i}">
      <div class="upload-card-header" onclick="toggleCard(${i})">
        <div class="upload-checkbox ${chk} ${disabled}" onclick="event.stopPropagation();toggleSelect(${i})"></div>
        ${cover}
        <div class="upload-info">
          <div class="upload-title">${esc(item.title)}</div>
          <div class="upload-meta">${esc(item.video_path.split('\\\\').pop())} · tid:${item.tid}</div>
          <div class="upload-state ${status}">状态: ${statusText} · 投稿次数:${attempts}${bvid}${err}</div>
        </div>
      </div>
      <div class="upload-card-body">
        <div style="font-size:12px;color:var(--muted);margin-bottom:8px">${esc(item.desc||'无简介')}</div>
        <div style="margin-bottom:8px">${tagsHtml||'<span style="color:var(--muted);font-size:12px">无标签</span>'}</div>
        <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();openEditModal(${i})">✏ 编辑</button>
      </div>
    </div>`;
  });
  list.innerHTML=html;
}

function toggleCard(i){
  const card=document.getElementById('card-'+i);
  card.classList.toggle('expanded');
}

function toggleSelect(i){
  if(selectedIndices.has(i))selectedIndices.delete(i);
  else selectedIndices.add(i);
  renderManifest();
  document.getElementById('sel-count').textContent=selectedIndices.size;
}

function openEditModal(i){
  editingIndex=i;
  const item=manifestItems[i];
  document.getElementById('edit-title').value=item.title||'';
  document.getElementById('edit-desc').value=item.desc||'';
  document.getElementById('edit-tags').value=(item.tags||[]).join(',');
  document.getElementById('edit-tid').value=item.tid||27;
  document.getElementById('edit-copyright').value=item.copyright||1;
  document.getElementById('edit-modal').classList.add('active');
}

function closeEditModal(){
  document.getElementById('edit-modal').classList.remove('active');
}

async function saveEdit(){
  if(editingIndex<0)return;
  const data={
    _dir:manifestDir,
    title:document.getElementById('edit-title').value,
    desc:document.getElementById('edit-desc').value,
    tags:document.getElementById('edit-tags').value.split(',').map(s=>s.trim()).filter(Boolean),
    tid:parseInt(document.getElementById('edit-tid').value),
    copyright:parseInt(document.getElementById('edit-copyright').value),
  };
  const r=await fetch('/api/uploads/'+editingIndex,{
    method:'PUT',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(data),
  });
  const d=await r.json();
  if(d.ok){
    toast('✅ 已保存');
    manifestItems[editingIndex]=d.item;
    renderManifest();
    closeEditModal();
  }else{
    toast('❌ '+d.error);
  }
}

async function submitUploads(){
  if(!selectedIndices.size){toast('请先选择投稿项');return;}
  const indices=Array.from(selectedIndices).sort((a,b)=>a-b);
  const r=await fetch('/api/uploads/submit',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({dir:manifestDir,indices}),
  });
  const d=await r.json();
  if(d.ok){
    toast(`🚀 开始投稿 ${d.count} 个视频`);
    switchTab.bind(document.querySelector('.tab'))('logs');
    document.querySelectorAll('.tab')[2].click();
  }else{
    toast('❌ '+d.error);
  }
}

async function pollUploadStatus(){
  const r=await fetch('/api/uploads/status');
  const d=await r.json();
  const bar=document.getElementById('upload-status-bar');
  if(d.running){
    uploadWasRunning = true;
    bar.style.display='block';
    document.getElementById('up-status-text').textContent='投稿中: '+d.current_title;
    document.getElementById('up-status-count').textContent=`✅${d.completed} ❌${d.failed} ⏭${d.skipped||0}/${d.total}`;
    document.getElementById('up-progress').style.width=(d.current_progress*100)+'%';
  }else if(d.total>0){
    bar.style.display='block';
    document.getElementById('up-status-text').textContent=`完成: ✅${d.completed} ❌${d.failed} ⏭${d.skipped||0}`;
    document.getElementById('up-status-count').textContent=`${d.completed+d.failed+(d.skipped||0)}/${d.total}`;
    document.getElementById('up-progress').style.width='100%';
    if(uploadWasRunning && manifestDir){
      uploadWasRunning = false;
      loadManifest();
    }
  }else{
    bar.style.display='none';
  }
  setTimeout(pollUploadStatus,2000);
}

// ═══════════════════════════════════════════════════════════════
// 流水线 & 日志
// ═══════════════════════════════════════════════════════════════
function initPipeline(){
  renderStepChips();
  connectSSE();
  loadSession();
  refreshPickerInfo();
}

function initLogs(){
  // 日志页仅需保持 SSE 连接
  connectSSE();
}

function onRunModeChange(){
  updateRunModeUI();
}

function updateRunModeUI(){
  const checked=document.querySelector('input[name="run-mode"]:checked');
  const mode=checked?checked.value:'picker';
  const pickerEl=document.getElementById('picker-info');
  const customEl=document.getElementById('custom-input');
  const pickerLabel=document.getElementById('mode-picker-label');
  const customLabel=document.getElementById('mode-custom-label');
  if(pickerEl)pickerEl.style.display=(mode==='picker')?'block':'none';
  if(customEl)customEl.style.display=(mode==='custom')?'block':'none';
  // 高亮当前选中的 tab label
  if(pickerLabel){
    pickerLabel.style.borderColor=(mode==='picker')?'var(--accent)':'var(--border)';
    pickerLabel.style.color=(mode==='picker')?'var(--accent)':'var(--muted)';
    pickerLabel.style.background=(mode==='picker')?'var(--accent)':'var(--surface)';
    pickerLabel.style.color=(mode==='picker')?'#fff':'var(--muted)';
  }
  if(customLabel){
    customLabel.style.borderColor=(mode==='custom')?'var(--accent)':'var(--border)';
    customLabel.style.color=(mode==='custom')?'#fff':'var(--muted)';
    customLabel.style.background=(mode==='custom')?'var(--accent)':'var(--surface)';
  }
}

function refreshPickerInfo(){
  const s=document.getElementById('up-streamer');
  const f=document.getElementById('up-folder');
  const b=document.getElementById('up-base');
  const el=document.getElementById('picker-text');
  if(!el)return;
  const sv=s?s.value:'';
  const fv=f?f.value:'';
  const bv=b?b.value:'';
  const baseShort=bv?(bv.replace(/\\/g,'/').split('/').pop()):'';
  if(sv&&fv){
    el.textContent='✅ '+sv+' / '+fv+(baseShort?' ['+baseShort+']':'');
  }else if(sv){
    el.textContent='⚠️ 已选主播 '+sv+'，请选文件夹';
  }else{
    el.textContent='未选择（请到投稿页选主播/文件夹）';
  }
}

async function loadQuickDirs(){
  // 加载最近5个有切片产出的目录作为快捷选项
  try{
    const r=await fetch('/api/streamers');
    const d=await r.json();
    const c=document.getElementById('quick-dirs');
    if(!d.streamers||!d.streamers.length){c.innerHTML='<small style="color:var(--muted)">暂无</small>';return;}
    let html='';
    d.streamers.forEach(s=>{
      html+=`<div style="margin-top:4px"><button class="btn btn-ghost btn-sm" style="width:100%;text-align:left;font-size:12px" onclick="pickQuickDir('${esc(s.name)}')">${esc(s.name)} (${s.folders||0})</button></div>`;
    });
    c.innerHTML=html;
  }catch(e){}
}

async function pickQuickDir(streamer){
  // 点击快捷主播 → 加载该主播的最新文件夹填入 custom-dir
  try{
    const baseSel=document.getElementById('up-base');
    const base=baseSel?baseSel.value:'';
    const url='/api/folders?streamer='+encodeURIComponent(streamer)+(base?'&base='+encodeURIComponent(base):'');
    const r=await fetch(url);
    const d=await r.json();
    if(d.folders&&d.folders.length){
      const latest=d.folders[d.folders.length-1];
      document.getElementById('custom-dir').value=latest.path||`${latest.base||(AUTOCLIP_BASES[0]||'')}\\${streamer}\\${latest.name}`;
    }else{
      document.getElementById('custom-dir').value=`${AUTOCLIP_BASES[0]||''}\\${streamer}`;
    }
  }catch(e){toast('加载失败');}
}

function renderStepChips(){
  const c=document.getElementById('step-chips');
  if(!c)return;
  c.innerHTML='';
  STEPS.forEach(s=>{
    const active=selectedSteps.has(s.id)?'active':'';
    const sub=s.sub&&s.sub.length?(' +'+s.sub.length):'';
    c.insertAdjacentHTML('beforeend',
      `<div class="chip ${active}" data-id="${s.id}" onclick="toggleStep(${s.id})">${s.id}.${s.name}${sub}</div>`);
  });
}

function toggleStep(id){
  if(selectedSteps.has(id)){
    selectedSteps.delete(id);
  }else{
    selectedSteps.add(id);
    // 自动加入子步骤（如选3则同时选3.5）
    const step=STEPS.find(s=>s.id===id);
    if(step&&step.sub){
      step.sub.forEach(subId=>selectedSteps.add(subId));
    }
  }
  renderStepChips();
}

function connectSSE(){
  // 避免重复连接：如果已有活跃连接且 readyState=OPEN，直接返回
  if(sseSource && sseSource.readyState===EventSource.OPEN) return;
  // 关闭旧连接前先置空，防止 onerror 里的自动重连干扰
  if(sseSource){ const old=sseSource; sseSource=null; old.close(); }
  sseSource=new EventSource('/api/stream');
  sseSource.onopen=()=>{
    console.log('SSE connected');
    // 重连后刷新会话，避免漏日志
    loadSession();
  };
  sseSource.onerror=(e)=>{
    console.warn('SSE error, will auto-reconnect');
    // EventSource 会自动重连，这里不需要手动 close
  };
  sseSource.onmessage=(e)=>{
    const d=JSON.parse(e.data);
    if(d.type==='log'){
      appendLog(d.ts,d.msg,d.level);
    }else if(d.type==='status'){
      pipeRunning=d.running;
      pipePaused=d.paused;
      updatePipeStatus();
      if(d.step)document.getElementById('pipe-step-text').textContent=d.step;
      const cwdEl=document.getElementById('pipe-cwd-text');
      if(cwdEl&&d.input_dir){
        cwdEl.textContent='📁 '+d.input_dir;
      }
    }else if(d.type==='step'){
      document.getElementById('pipe-step-text').textContent=d.step;
    }else if(d.type==='pipeline_done'){
      pipeRunning=false;
      updatePipeStatus();
      toast('流水线结束');
      const cwdEl=document.getElementById('pipe-cwd-text');
      if(cwdEl)cwdEl.textContent='📁 未启动';
    }else if(d.type==='upload_progress'){
      // 投稿进度通过轮询处理
    }else if(d.type==='upload_done'){
      toast('投稿完成');
    }
    // 流转速览页状态同步
    syncPipelineStatus();
  };
}

function appendLog(ts,msg,level){
  const c=document.getElementById('log-container');
  const div=document.createElement('div');
  div.className='log-line '+(level||'info');
  div.innerHTML=`<span class="ts">${ts}</span>${esc(msg)}`;
  c.appendChild(div);
  // 限制日志行数
  while(c.children.length>500)c.removeChild(c.firstChild);
  c.scrollTop=c.scrollHeight;
}

function clearLogs(){
  const c=document.getElementById('log-container');
  c.innerHTML='<div class="log-line" style="color:var(--muted)">已清空</div>';
  toast('🗑 日志已清空');
}

function updatePipeStatus(){
  const badge=document.getElementById('pipe-status-badge');
  const btnPause=document.getElementById('btn-pause');
  if(pipeRunning){
    if(pipePaused){
      badge.className='badge badge-paused';
      badge.textContent='⏸ 已暂停';
      btnPause.textContent='▶';
    }else{
      badge.className='badge badge-running';
      badge.textContent='● 运行中';
      btnPause.textContent='⏸';
    }
  }else{
    badge.className='badge badge-idle';
    badge.textContent='● 空闲';
    btnPause.textContent='⏸';
  }
}

function syncPipelineStatus(){
  // 流转页状态卡片同步
  const badge=document.getElementById('pipe-status-badge');
  const badge2=document.getElementById('pipe-status-badge-2');
  const step=document.getElementById('pipe-step-text');
  const step2=document.getElementById('pipe-step-text-2');
  const cwd=document.getElementById('pipe-cwd-text');
  const cwd2=document.getElementById('pipe-cwd-text-2');
  if(badge2&&badge){badge2.className=badge.className;badge2.textContent=badge.textContent;}
  if(step2&&step){step2.textContent=step.textContent;}
  if(cwd2&&cwd){cwd2.textContent=cwd.textContent;}
}

async function loadSession(){
  const r=await fetch('/api/session');
  const d=await r.json();
  if(d.log_tail){
    d.log_tail.forEach(e=>appendLog(e.ts,e.msg,e.level));
  }
  if(d.session&&d.session.streamer){
    document.getElementById('pipe-step-text').textContent=d.session.streamer+' / '+d.session.folder;
    const cwdEl=document.getElementById('pipe-cwd-text');
    if(cwdEl){
      const dir=d.session.input_dir||(d.session.streamer+' / '+d.session.folder);
      cwdEl.textContent='📁 '+dir;
    }
  }
  syncPipelineStatus();
}

async function startPipeline(){
  const btn=document.getElementById('btn-run-pipeline');
  try{
    // 校验步骤
    if(!selectedSteps||!selectedSteps.size){
      toast('⚠️ 请选择至少一个步骤');
      return;
    }

    // 从投稿页读取主播和文件夹
    const streamerEl=document.getElementById('up-streamer');
    const folderEl=document.getElementById('up-folder');
    const streamer=streamerEl?streamerEl.value:'';
    const folder=folderEl?folderEl.value:'';
    if(!streamer||!folder){
      toast('⚠️ 请先在投稿页选择主播和文件夹');
      switchTab('uploads');
      return;
    }

    // 禁用按钮防止重复点击
    if(btn){btn.disabled=true;btn.textContent='⏳ 启动中...';}

    const body={
      streamer:streamer,
      folder:folder,
      dir:selectedFolderPath(),
      steps:Array.from(selectedSteps).sort((a,b)=>a-b)
    };

    console.log('[startPipeline] body=',body);
    const r=await fetch('/api/pipeline/start',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body),
      signal:AbortSignal.timeout(10000),  // 10s 超时防止卡死
    });
    if(!r.ok){ throw new Error(`${r.status} ${r.statusText}`); }
    const d=await r.json();
    console.log('[startPipeline] resp=',d);

    if(d.ok){
      toast('🚀 流水线已启动');
      pipeRunning=true;
      updatePipeStatus();
      const cwdEl=document.getElementById('pipe-cwd-text');
      if(cwdEl)cwdEl.textContent='📁 '+(d.input_dir||label);
    }else{
      toast('❌ '+(d.error||'启动失败'));
    }
  }catch(err){
    console.error('[startPipeline] error:',err);
    toast('💥 启动失败: '+(err.name==='TimeoutError'?'请求超时':err.message));
  }finally{
    if(btn){btn.disabled=false;btn.textContent='🚀 一键流转';}
  }
}

async function abortPipeline(){
  if(!confirm('确定中断流水线？'))return;
  const r=await fetch('/api/pipeline/abort',{method:'POST'});
  const d=await r.json().catch(()=>({}));
  if(!r.ok||d.error){
    toast('❌ 中断失败: '+(d.error||r.statusText));
  }else{
    toast(d.message||'⏹ 已发送中断信号');
  }
}

async function togglePause(){
  if(!pipeRunning)return;
  const url=pipePaused?'/api/pipeline/resume':'/api/pipeline/pause';
  const r=await fetch(url,{method:'POST'});
  const d=await r.json();
  if(d.error)toast(d.error);
}

// ═══════════════════════════════════════════════════════════════
// 设置
// ═══════════════════════════════════════════════════════════════
async function initSettings(){
  const r=await fetch('/api/files');
  const d=await r.json();
  document.getElementById('set-root').textContent=d.path||'(未知)';
  document.getElementById('set-input').textContent=(d.bases&&d.bases.length?d.bases:['(未配置录播根目录)']).join('<br>');
  document.getElementById('set-time').textContent=new Date().toLocaleString('zh-CN');
}

// ═══════════════════════════════════════════════════════════════
// 工具
// ═══════════════════════════════════════════════════════════════
function esc(s){
  if(s==null)return'';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ═══════════════════════════════════════════════════════════════
// 启动
// ═══════════════════════════════════════════════════════════════
browseFiles('');
</script>
</body>
</html>'''


# ═══════════════════════════════════════════════════════════════
# 自定义 Server：静默移动端断连异常
# ═══════════════════════════════════════════════════════════════

class SilentThreadingHTTPServer(ThreadingHTTPServer):
    """静默移动端断连异常的 ThreadingHTTPServer。

    移动端（手机锁屏/切网络/Tailscale 中继切换）会频繁 abort TCP 连接，
    默认 socketserver 会把 ConnectionAbortedError/BrokenPipeError 的
    traceback 打到控制台，看着烦但不影响功能。这里静默掉。
    """
    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        # 移动端常见断连，静默
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError,
                            ConnectionRefusedError, BrokenPipeError)):
            return
        # 其他异常正常打印
        super().handle_error(request, client_address)


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="AutoClip Web Console v2")
    parser.add_argument("--port", type=int, default=5000, help="监听端口 (默认: 5000)")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认: 127.0.0.1，手机/局域网访问请传 0.0.0.0)")
    args = parser.parse_args()

    _load_last_session()

    # 获取 Tailscale IP 显示给用户
    ts_ip = "100.x.x.x"
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            ts_ip = result.stdout.strip().split("\n")[0]
    except Exception:
        pass

    base1 = INPUT_BASES[0] if INPUT_BASES else "(未配置：gui_config.json 的 recording_roots)"
    base2 = INPUT_BASES[1] if len(INPUT_BASES) > 1 else "-"
    phone_line = (f"http://{ts_ip}:{args.port}" if args.host not in ("127.0.0.1", "localhost")
                  else f"需 --host 0.0.0.0 后 http://{ts_ip}:{args.port}")
    print(f"""
╔══════════════════════════════════════════════════════╗
║         AutoClip Web Console v2                     ║
║         Tailscale 移动远程控制台                      ║
╠══════════════════════════════════════════════════════╣
║  本机访问:   http://localhost:{args.port:<8}            ║
║  手机访问:   {phone_line:<41} ║
║  项目根目录: {PROJECT_ROOT:<30}    ║
║  录播根目录: {base1:<30}    ║
║  备用根目录: {base2:<30}    ║
╠══════════════════════════════════════════════════════╣
║  按 Ctrl+C 停止服务                                   ║
╚══════════════════════════════════════════════════════╝
""")

    server = SilentThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.server_close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
