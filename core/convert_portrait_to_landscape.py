#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional step 1 task: convert portrait MP4 videos to 1080p landscape canvas."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


MP4_EXT = ".mp4"
PORTRAIT_BACKUP_SUFFIX = "-竖屏原版"
DEFAULT_FFMPEG_BIN = os.environ.get("AUTOCLIP_FFMPEG_BIN", "")
DEFAULT_FFMPEG = os.path.join(DEFAULT_FFMPEG_BIN, "ffmpeg.exe")


def resolve_ffmpeg():
    return shutil.which("ffmpeg") or (DEFAULT_FFMPEG if os.path.isfile(DEFAULT_FFMPEG) else "")


def ffprobe_path_for(ffmpeg_path):
    ffprobe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    sibling = os.path.join(os.path.dirname(ffmpeg_path), ffprobe_name)
    return sibling if os.path.isfile(sibling) else (shutil.which("ffprobe") or ffprobe_name)


def parse_ffmpeg_time_seconds(value):
    match = re.match(r"(\d+):(\d+):(\d+(?:\.\d+)?)", value or "")
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


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
            return None
        streams = (json.loads(result.stdout).get("streams") or [])
        if not streams:
            return None
        stream = streams[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        if width <= 0 or height <= 0:
            return None

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
        return None


def print_process_error(result, label, max_lines=12):
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return
    print(f"{label} details:")
    for line in lines[-max_lines:]:
        print(f"  {line}")


def run_ffmpeg_with_progress(cmd, duration, label="FFmpeg"):
    progress_cmd = [cmd[0], "-hide_banner", "-nostats", "-progress", "pipe:1", *cmd[1:]]
    print(f"{label}: started")
    process = subprocess.Popen(
        progress_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
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
                print(f"{label}: {percent}% ({current:.1f}s / {duration:.1f}s)")
                last_percent = percent
                last_log_at = now
        elif line == "progress=end":
            print(f"{label}: 100%")

    returncode = process.wait()
    if returncode != 0:
        return subprocess.CompletedProcess(progress_cmd, returncode, "", "\n".join(last_lines[-12:]))
    return subprocess.CompletedProcess(progress_cmd, 0, "", "")


def find_mp4_files(input_dir, recursive=False):
    walker = os.walk(input_dir) if recursive else [(input_dir, [], os.listdir(input_dir))]
    matches = []
    for root, _, files in walker:
        for name in files:
            stem, ext = os.path.splitext(name)
            if ext.lower() != MP4_EXT:
                continue
            if stem.endswith(PORTRAIT_BACKUP_SUFFIX) or stem.endswith("-弹幕版") or stem.endswith("-后处理"):
                continue
            matches.append(os.path.join(root, name))
    return sorted(matches)


def backup_path_for(mp4_path):
    directory = os.path.dirname(mp4_path)
    stem = os.path.splitext(os.path.basename(mp4_path))[0]
    return os.path.join(directory, f"{stem}{PORTRAIT_BACKUP_SUFFIX}.mp4")


def convert_one(mp4_path, ffmpeg_path, width=1920, height=1080, blur=20, overwrite=False, keep_source=True):
    resolution = probe_video_resolution(mp4_path, ffmpeg_path)
    if not resolution:
        print(f"Skip unreadable resolution: {mp4_path}")
        return 0
    src_width, src_height = resolution
    if src_width >= src_height:
        print(f"Skip landscape MP4: {os.path.basename(mp4_path)} ({src_width}x{src_height})")
        return 0

    backup_path = backup_path_for(mp4_path)
    if os.path.exists(backup_path) and not overwrite:
        print(f"Skip already converted portrait MP4: {mp4_path}")
        return 0

    tmp_path = os.path.splitext(mp4_path)[0] + ".landscape.tmp.mp4"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    fg_scale = f"scale=-2:{height}:force_original_aspect_ratio=decrease"
    # 背景模糊用「源分辨率裁剪 → 缩小 → 放大」的低通近似替代全帧 boxblur；
    # 缩小高度 = height/blur 时与 boxblur 半径 blur 强度等效（blur=20 → 缩小到 54px 高）
    bg_blur_height = max(8, round(height / max(blur, 1)))
    bg_crop = f"crop=iw:trunc(iw*{height}/{width}/2)*2"
    filter_complex = (
        f"[0:v]{bg_crop},scale=-2:{bg_blur_height},"
        f"scale={width}:{height}[bg];"
        f"[0:v]{fg_scale}[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    )
    cmd = [
        ffmpeg_path,
        "-y",
        "-i", mp4_path,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "h264_nvenc",
        "-preset", "p1",
        "-rc:v", "vbr",
        "-cq:v", "23",
        "-b:v", "0",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        tmp_path,
    ]

    print(f"Portrait to landscape: {os.path.basename(mp4_path)} ({src_width}x{src_height} -> {width}x{height})")
    result = run_ffmpeg_with_progress(cmd, probe_video_duration(mp4_path, ffmpeg_path), label="Portrait landscape")
    if result.returncode != 0:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        print(f"ERROR: portrait to landscape failed: {mp4_path}")
        print_process_error(result, "FFmpeg")
        return result.returncode

    if keep_source:
        if os.path.exists(backup_path) and overwrite:
            os.remove(backup_path)
        os.replace(mp4_path, backup_path)
    else:
        os.remove(mp4_path)
    os.replace(tmp_path, mp4_path)
    print(f"Landscape MP4 output: {mp4_path}")
    if keep_source:
        print(f"Portrait source kept: {backup_path}")
    return 0


def convert_portrait_videos(input_dir, ffmpeg_path, recursive=False, width=1920, height=1080, blur=20, overwrite=False, keep_source=True):
    mp4_files = find_mp4_files(input_dir, recursive=recursive)
    if not mp4_files:
        print("No MP4 files found; portrait to landscape skipped")
        return 0

    print(f"Found {len(mp4_files)} MP4 file(s); converting portrait videos to {width}x{height} landscape")
    failed = 0
    converted = 0
    for mp4_path in mp4_files:
        before = os.path.exists(backup_path_for(mp4_path))
        rc = convert_one(
            mp4_path,
            ffmpeg_path,
            width=width,
            height=height,
            blur=blur,
            overwrite=overwrite,
            keep_source=keep_source,
        )
        if rc != 0:
            failed += 1
        elif not before and os.path.exists(backup_path_for(mp4_path)):
            converted += 1

    if failed:
        print(f"Portrait to landscape finished with {failed} failed file(s)")
        return 1
    print(f"Portrait to landscape complete; converted {converted} file(s)")
    return 0


def main():
    parser = argparse.ArgumentParser(description="AutoClip optional task: convert portrait MP4 to 1080p landscape")
    parser.add_argument("--input-dir", "-i", required=True, help="recording directory")
    parser.add_argument("--recursive", action="store_true", help="scan subdirectories recursively")
    parser.add_argument("--overwrite", action="store_true", help="re-convert even when portrait backup exists")
    parser.add_argument("--delete-source", action="store_true", help="delete original portrait MP4 instead of keeping a backup")
    parser.add_argument("--width", type=int, default=1920, help="target landscape width")
    parser.add_argument("--height", type=int, default=1080, help="target landscape height")
    parser.add_argument("--blur", type=int, default=20, help="background blur strength (equivalent boxblur radius; implemented as downscale-upscale)")
    args = parser.parse_args()

    input_dir = os.path.abspath(args.input_dir)
    if not os.path.isdir(input_dir):
        print(f"ERROR: directory does not exist: {input_dir}")
        return 1

    ffmpeg_path = resolve_ffmpeg()
    if not ffmpeg_path:
        print(f"ERROR: ffmpeg not found. Check PATH or configured path: {DEFAULT_FFMPEG}")
        return 1

    print(f"FFmpeg: {ffmpeg_path}")
    return convert_portrait_videos(
        input_dir,
        ffmpeg_path,
        recursive=args.recursive,
        width=args.width,
        height=args.height,
        blur=args.blur,
        overwrite=args.overwrite,
        keep_source=not args.delete_source,
    )


if __name__ == "__main__":
    sys.exit(main())
