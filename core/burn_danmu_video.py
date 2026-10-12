#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Burn same-name Bilibili danmu XML onto MP4 videos."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


MP4_EXT = ".mp4"
XML_EXT = ".xml"
DANMU_SUFFIX = "-弹幕版"
# libass 渲染是单线程的，一路压制只占满一个核；多路并行是唯一有效的提速手段。
# 实测（2 个 5.5 分钟 1080p60 片段）：串行 131s → 2 路 64-72s，约 1.9x；占用约 7 个逻辑核。
# 再往上加路数收益很小 —— NVENC 编码器是共享的硬件瓶颈（实测无编码器上限 1.7x，带编码器 1.4x）。
DEFAULT_JOBS = 2
# 编码参数：实测 -maxrate/-bufsize、constqp、tune ll 对速度均无改善（NVENC 是硬件瓶颈），
# 唯一有意义的区别是软编回退慢约 2 倍，所以只在 NVENC 缺失时才用 libx264。
NVENC_ARGS = ["-c:v", "h264_nvenc", "-preset", "p1", "-rc:v", "vbr", "-cq:v", "23", "-b:v", "0"]
LIBX264_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
DEFAULT_FFMPEG_BIN = os.environ.get("AUTOCLIP_FFMPEG_BIN", "")
DEFAULT_FFMPEG = os.path.join(DEFAULT_FFMPEG_BIN, "ffmpeg.exe")
DEFAULT_BILILIVE_TOOLS_BIN = os.environ.get("AUTOCLIP_BILILIVE_TOOLS_BIN", "")
DEFAULT_DANMAKU_FACTORY = os.path.join(DEFAULT_BILILIVE_TOOLS_BIN, "DanmakuFactory.exe")


def process_creationflags(priority="below"):
    """压制子进程的优先级：Windows 下让出 CPU 给前台程序，避免压制期间整机变卡。

    below/idle 只在有更高优先级的线程要跑时才让路，机器空闲时速度不受影响。
    """
    if os.name != "nt":
        return 0
    flags = {
        "below": getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000),
        "idle": getattr(subprocess, "IDLE_PRIORITY_CLASS", 0x00000040),
        "normal": 0,
    }
    return flags.get(priority, flags["below"])


def resolve_ffmpeg():
    return shutil.which("ffmpeg") or (DEFAULT_FFMPEG if os.path.isfile(DEFAULT_FFMPEG) else "")


def resolve_danmaku_factory():
    return shutil.which("DanmakuFactory") or (
        DEFAULT_DANMAKU_FACTORY if os.path.isfile(DEFAULT_DANMAKU_FACTORY) else ""
    )


def print_process_error(result, label, max_lines=12, tag=""):
    prefix = f"[{tag}] " if tag else ""
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return
    print(f"{prefix}{label} details:")
    for line in lines[-max_lines:]:
        print(f"  {line}")


def parse_ffmpeg_time_seconds(value):
    match = re.match(r"(\d+):(\d+):(\d+(?:\.\d+)?)", value or "")
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def ffprobe_path_for(ffmpeg_path):
    ffprobe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    sibling = os.path.join(os.path.dirname(ffmpeg_path), ffprobe_name)
    return sibling if os.path.isfile(sibling) else (shutil.which("ffprobe") or ffprobe_name)


def probe_video_duration(video_path, ffmpeg_path):
    try:
        result = subprocess.run(
            [
                ffprobe_path_for(ffmpeg_path),
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        return max(float(result.stdout.strip()), 0.1)
    except Exception:
        return 0.0


def probe_video_resolution(video_path, ffmpeg_path):
    """Return display resolution as (width, height), falling back to 1920x1080."""
    fallback = (1920, 1080)
    try:
        result = subprocess.run(
            [
                ffprobe_path_for(ffmpeg_path),
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
                "-of", "json",
                video_path,
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return fallback

        data = json.loads(result.stdout)
        streams = data.get("streams") or []
        if not streams:
            return fallback
        stream = streams[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        if width <= 0 or height <= 0:
            return fallback

        rotation = 0
        tags = stream.get("tags") or {}
        if tags.get("rotate"):
            rotation = int(float(tags.get("rotate") or 0))
        for side_data in stream.get("side_data_list") or []:
            if side_data.get("rotation") is not None:
                rotation = int(float(side_data.get("rotation") or 0))
                break
        if abs(rotation) % 180 == 90:
            width, height = height, width

        return width, height
    except Exception:
        return fallback


def run_ffmpeg_with_progress(cmd, duration, label="FFmpeg", creationflags=0, tag=""):
    prefix = f"[{tag}] " if tag else ""
    progress_cmd = [cmd[0], "-hide_banner", "-nostats", "-progress", "pipe:1", *cmd[1:]]
    print(f"{prefix}{label}: started")
    process = subprocess.Popen(
        progress_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
    )
    last_lines = []
    last_percent = -1
    last_log_at = 0.0
    duration = max(float(duration or 0), 0.1)

    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line:
            continue
        last_lines.append(line)
        if len(last_lines) > 30:
            last_lines = last_lines[-30:]
        if line.startswith("out_time="):
            current = parse_ffmpeg_time_seconds(line.split("=", 1)[1])
            if current is None:
                continue
            percent = min(99, int(current * 100 / duration))
            now = time.monotonic()
            if percent >= last_percent + 10 or now - last_log_at >= 20:
                print(f"{prefix}{label}: {percent}% ({current:.1f}s / {duration:.1f}s)")
                last_percent = percent
                last_log_at = now
        elif line == "progress=end":
            print(f"{prefix}{label}: 100%")

    returncode = process.wait()
    if returncode != 0:
        tail = "\n".join(last_lines[-12:])
        return subprocess.CompletedProcess(progress_cmd, returncode, "", tail)
    return subprocess.CompletedProcess(progress_cmd, 0, "", "")


def find_mp4_xml_pairs(input_dir, recursive=False):
    pairs = []
    walker = os.walk(input_dir) if recursive else [(input_dir, [], os.listdir(input_dir))]
    for root, _, files in walker:
        file_set = {name.lower(): name for name in files}
        for name in files:
            stem, ext = os.path.splitext(name)
            if ext.lower() != MP4_EXT:
                continue
            if stem.endswith(DANMU_SUFFIX) or stem.endswith("-后处理"):
                continue
            xml_name = stem + XML_EXT
            actual_xml = file_set.get(xml_name.lower())
            if not actual_xml:
                continue
            pairs.append((os.path.join(root, name), os.path.join(root, actual_xml)))
    return sorted(pairs)


def is_xml_danmu_empty(xml_path):
    markers = ("</d>", "</gift>", "</sc>", "</guard>")
    try:
        with open(xml_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if any(marker in line for marker in markers):
                    return False
    except OSError:
        return True
    return True


def bili_danmu_output_path(mp4_path):
    directory = os.path.dirname(mp4_path)
    stem = os.path.splitext(os.path.basename(mp4_path))[0]
    return os.path.join(directory, f"{stem}{DANMU_SUFFIX}.mp4")


def escape_ffmpeg_filter_path(path):
    # FFmpeg filter arguments use ':' as separators, so Windows drive letters need escaping.
    return os.path.abspath(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def convert_xml_to_ass(xml_path, ass_path, danmaku_factory_path, width=1920, height=1080, creationflags=0):
    msgbox_width = min(500, max(1, int(width)))
    msgbox_height = max(1, int(height))
    cmd = [
        danmaku_factory_path,
        "-i", xml_path,
        "-t", "1.0",
        "-o", ass_path,
        "--ignore-warnings",
        "--force",
        "--resolution", f"{width}x{height}",
        "--scrolltime", "12",
        "--fixtime", "5",
        "--density", "-1",
        "--line-spacing", "0",
        "--fontname", "Microsoft YaHei",
        "--fontsize", "40",
        "--opacity", "191",
        "--outline", "1",
        "--outline-blur", "0",
        "--outline-opacity", "191",
        "--shadow", "0",
        "--bold", "TRUE",
        "--scrollarea", "0.3",
        "--displayarea", "1",
        "--saveblocked", "TRUE",
        "--showusernames", "FALSE",
        "--blockmode", "BOTTOM",
        "--showmsgbox", "TRUE",
        "--msgboxsize", f"{msgbox_width}x{msgbox_height}",
        "--msgboxpos", "20x0",
        "--msgboxfontsize", "38",
        "--msgboxduration", "10",
        "--giftminprice", "10",
    ]
    return subprocess.run(
        cmd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )


def detect_video_encoder(ffmpeg_path):
    """探测可用的 H.264 编码器参数；NVENC 不可用时回退 libx264。

    原实现硬编码 h264_nvenc：显卡驱动异常或 NVENC 会话占满时整个文件直接失败，
    没有任何退路。
    """
    try:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        if "h264_nvenc" in (result.stdout or ""):
            return list(NVENC_ARGS)
    except Exception:
        pass
    print("NVENC not available; falling back to libx264 (slower)")
    return list(LIBX264_ARGS)


def probe_hwaccel(ffmpeg_path, hwaccel="cuda"):
    """确认硬件解码真的可用（编译进了不代表驱动能用），不可用则静默退回软解。"""
    try:
        result = subprocess.run(
            [
                ffmpeg_path, "-hide_banner", "-v", "error",
                "-hwaccel", hwaccel,
                "-f", "lavfi", "-i", "color=black:s=64x64:d=0.1",
                "-f", "null", "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        return result.returncode == 0
    except Exception:
        return False


def burn_one(mp4_path, xml_path, ffmpeg_path, danmaku_factory_path, overwrite=False, keep_ass=False,
             priority="below", hwaccel="", encoder_args=None):
    tag = os.path.basename(mp4_path)
    prefix = f"[{tag}] "
    creationflags = process_creationflags(priority)
    output_path = bili_danmu_output_path(mp4_path)
    if os.path.exists(output_path) and not overwrite:
        print(f"{prefix}Skip existing danmu video: {output_path}")
        return 0

    if is_xml_danmu_empty(xml_path):
        print(f"{prefix}Skip empty danmu XML: {xml_path}")
        return 0

    if overwrite and os.path.exists(output_path):
        os.remove(output_path)

    ass_path = os.path.join(tempfile.gettempdir(), f"autoclip_danmu_{uuid.uuid4()}.ass")
    print(f"{prefix}Burn danmu: {os.path.basename(mp4_path)} + {os.path.basename(xml_path)}")
    width, height = probe_video_resolution(mp4_path, ffmpeg_path)
    print(f"{prefix}Danmu resolution: {width}x{height}")
    ass_result = convert_xml_to_ass(
        xml_path, ass_path, danmaku_factory_path,
        width=width, height=height, creationflags=creationflags,
    )
    if ass_result.returncode != 0:
        print(f"{prefix}ERROR: danmu XML to ASS failed: {xml_path}")
        print_process_error(ass_result, "DanmakuFactory", tag=tag)
        return 1
    if not os.path.isfile(ass_path) or os.path.getsize(ass_path) <= 0:
        print(f"{prefix}ERROR: ASS output is not usable: {ass_path}")
        return 1

    ass_filter_path = escape_ffmpeg_filter_path(ass_path)
    tmp_output = os.path.join(
        os.path.dirname(output_path),
        f"{os.path.splitext(os.path.basename(output_path))[0]}.tmp.mp4",
    )
    try:
        if os.path.exists(tmp_output):
            os.remove(tmp_output)
    except OSError:
        # 上一次运行留下的残留文件，或另一路压制正在写同一个目标（例如流水线
        # 与 GUI 手动压制同时跑）。换个唯一名字继续，别让整个文件失败。
        tmp_output = os.path.join(
            os.path.dirname(output_path),
            f"{os.path.splitext(os.path.basename(output_path))[0]}.{uuid.uuid4().hex[:8]}.tmp.mp4",
        )

    cmd = [ffmpeg_path, "-hide_banner", "-y"]
    if hwaccel:
        cmd += ["-hwaccel", hwaccel]
    cmd += [
        "-i", mp4_path,
        "-vf", f"subtitles='{ass_filter_path}'",
        *(encoder_args or NVENC_ARGS),
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        tmp_output,
    ]
    duration = probe_video_duration(mp4_path, ffmpeg_path)
    result = run_ffmpeg_with_progress(
        cmd, duration, label="Danmu burn", creationflags=creationflags, tag=tag,
    )
    if result.returncode != 0:
        if os.path.exists(tmp_output):
            os.remove(tmp_output)
        print(f"{prefix}ERROR: danmu burn failed: {mp4_path}")
        print_process_error(result, "FFmpeg", tag=tag)
        if not keep_ass:
            try:
                os.remove(ass_path)
            except OSError:
                pass
        return result.returncode

    os.replace(tmp_output, output_path)
    print(f"{prefix}Danmu video output: {output_path}")
    if keep_ass:
        ass_keep_path = os.path.splitext(output_path)[0] + ".ass"
        shutil.move(ass_path, ass_keep_path)
        print(f"{prefix}ASS kept: {ass_keep_path}")
    else:
        try:
            os.remove(ass_path)
        except OSError:
            pass
    return 0


def burn_danmu_videos(input_dir, ffmpeg_path, danmaku_factory_path, recursive=False, overwrite=False,
                      keep_ass=False, jobs=DEFAULT_JOBS, priority="below", hwaccel="", encoder_args=None):
    pairs = find_mp4_xml_pairs(input_dir, recursive=recursive)
    if not pairs:
        print("No same-name MP4 + XML pairs found; danmu burn skipped")
        return 0

    workers = max(1, min(int(jobs or 1), len(pairs)))
    parallel_note = f" ({workers} in parallel)" if workers > 1 else ""
    print(f"Found {len(pairs)} same-name MP4 + XML pair(s); creating danmu videos{parallel_note}")

    def work(pair):
        mp4_path, xml_path = pair
        try:
            return burn_one(
                mp4_path,
                xml_path,
                ffmpeg_path,
                danmaku_factory_path,
                overwrite=overwrite,
                keep_ass=keep_ass,
                priority=priority,
                hwaccel=hwaccel,
                encoder_args=encoder_args,
            )
        except Exception as exc:  # 单文件异常不应带走整批并行任务
            print(f"[{os.path.basename(mp4_path)}] ERROR: {exc}")
            return 1

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(work, pairs))
    else:
        results = [work(pair) for pair in pairs]

    failed = sum(1 for code in results if code != 0)
    if failed:
        print(f"Danmu burn finished with {failed} failed file(s)")
        return 1
    print("Danmu burn complete")
    return 0


def main():
    parser = argparse.ArgumentParser(description="AutoClip optional task: burn same-name MP4 + XML danmu videos")
    parser.add_argument("--input-dir", "-i", required=True, help="recording directory")
    parser.add_argument("--recursive", action="store_true", help="scan subdirectories recursively")
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing danmu videos")
    parser.add_argument("--keep-ass", action="store_true", help="keep generated ASS files next to output videos")
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS,
                        help=f"parallel burn jobs (default {DEFAULT_JOBS}; libass is single-threaded)")
    parser.add_argument("--priority", choices=("below", "normal", "idle"), default="below",
                        help="child process priority so foreground apps keep the CPU (default below)")
    parser.add_argument("--hwaccel", action="store_true",
                        help="decode on GPU (cuda): ~42%% less CPU but ~25%% slower wall time")
    args = parser.parse_args()

    input_dir = os.path.abspath(args.input_dir)
    if not os.path.isdir(input_dir):
        print(f"ERROR: directory does not exist: {input_dir}")
        return 1

    ffmpeg_path = resolve_ffmpeg()
    if not ffmpeg_path:
        print(f"ERROR: ffmpeg not found. Check PATH or configured path: {DEFAULT_FFMPEG}")
        return 1

    danmaku_factory_path = resolve_danmaku_factory()
    if not danmaku_factory_path:
        print(f"ERROR: DanmakuFactory not found. Check path: {DEFAULT_DANMAKU_FACTORY}")
        return 1

    hwaccel = ""
    if args.hwaccel:
        if probe_hwaccel(ffmpeg_path):
            hwaccel = "cuda"
        else:
            print("WARNING: --hwaccel requested but cuda decode is unusable; using software decode")

    encoder_args = detect_video_encoder(ffmpeg_path)

    print(f"FFmpeg: {ffmpeg_path}")
    print(f"DanmakuFactory: {danmaku_factory_path}")
    print(f"Jobs: {args.jobs} | Priority: {args.priority} | Encoder: {encoder_args[1]}"
          + (f" | Decode: {hwaccel}" if hwaccel else ""))
    return burn_danmu_videos(
        input_dir,
        ffmpeg_path,
        danmaku_factory_path,
        recursive=args.recursive,
        overwrite=args.overwrite,
        keep_ass=args.keep_ass,
        jobs=args.jobs,
        priority=args.priority,
        hwaccel=hwaccel,
        encoder_args=encoder_args,
    )


if __name__ == "__main__":
    sys.exit(main())
