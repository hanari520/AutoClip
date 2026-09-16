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

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


MP4_EXT = ".mp4"
XML_EXT = ".xml"
DANMU_SUFFIX = "-弹幕版"
DEFAULT_FFMPEG_BIN = os.environ.get("AUTOCLIP_FFMPEG_BIN", "")
DEFAULT_FFMPEG = os.path.join(DEFAULT_FFMPEG_BIN, "ffmpeg.exe")
DEFAULT_BILILIVE_TOOLS_BIN = os.environ.get("AUTOCLIP_BILILIVE_TOOLS_BIN", "")
DEFAULT_DANMAKU_FACTORY = os.path.join(DEFAULT_BILILIVE_TOOLS_BIN, "DanmakuFactory.exe")


def resolve_ffmpeg():
    return shutil.which("ffmpeg") or (DEFAULT_FFMPEG if os.path.isfile(DEFAULT_FFMPEG) else "")


def resolve_danmaku_factory():
    return shutil.which("DanmakuFactory") or (
        DEFAULT_DANMAKU_FACTORY if os.path.isfile(DEFAULT_DANMAKU_FACTORY) else ""
    )


def print_process_error(result, label, max_lines=12):
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return
    print(f"{label} details:")
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


def convert_xml_to_ass(xml_path, ass_path, danmaku_factory_path, width=1920, height=1080):
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
    )


def burn_one(mp4_path, xml_path, ffmpeg_path, danmaku_factory_path, overwrite=False, keep_ass=False):
    output_path = bili_danmu_output_path(mp4_path)
    if os.path.exists(output_path) and not overwrite:
        print(f"Skip existing danmu video: {output_path}")
        return 0

    if is_xml_danmu_empty(xml_path):
        print(f"Skip empty danmu XML: {xml_path}")
        return 0

    if overwrite and os.path.exists(output_path):
        os.remove(output_path)

    ass_path = os.path.join(tempfile.gettempdir(), f"autoclip_danmu_{uuid.uuid4()}.ass")
    print(f"Burn danmu: {os.path.basename(mp4_path)} + {os.path.basename(xml_path)}")
    width, height = probe_video_resolution(mp4_path, ffmpeg_path)
    print(f"Danmu resolution: {width}x{height}")
    ass_result = convert_xml_to_ass(xml_path, ass_path, danmaku_factory_path, width=width, height=height)
    if ass_result.returncode != 0:
        print(f"ERROR: danmu XML to ASS failed: {xml_path}")
        print_process_error(ass_result, "DanmakuFactory")
        return 1
    if not os.path.isfile(ass_path) or os.path.getsize(ass_path) <= 0:
        print(f"ERROR: ASS output is not usable: {ass_path}")
        return 1

    ass_filter_path = escape_ffmpeg_filter_path(ass_path)
    tmp_output = os.path.join(
        os.path.dirname(output_path),
        f"{os.path.splitext(os.path.basename(output_path))[0]}.tmp.mp4",
    )
    if os.path.exists(tmp_output):
        os.remove(tmp_output)

    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-y",
        "-i", mp4_path,
        "-vf", f"subtitles='{ass_filter_path}'",
        "-c:v", "h264_nvenc",
        "-preset", "p1",
        "-rc:v", "vbr",
        "-cq:v", "23",
        "-b:v", "0",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        tmp_output,
    ]
    duration = probe_video_duration(mp4_path, ffmpeg_path)
    result = run_ffmpeg_with_progress(cmd, duration, label="Danmu burn")
    if result.returncode != 0:
        if os.path.exists(tmp_output):
            os.remove(tmp_output)
        print(f"ERROR: danmu burn failed: {mp4_path}")
        print_process_error(result, "FFmpeg")
        if not keep_ass:
            try:
                os.remove(ass_path)
            except OSError:
                pass
        return result.returncode

    os.replace(tmp_output, output_path)
    print(f"Danmu video output: {output_path}")
    if keep_ass:
        ass_keep_path = os.path.splitext(output_path)[0] + ".ass"
        shutil.move(ass_path, ass_keep_path)
        print(f"ASS kept: {ass_keep_path}")
    else:
        try:
            os.remove(ass_path)
        except OSError:
            pass
    return 0


def burn_danmu_videos(input_dir, ffmpeg_path, danmaku_factory_path, recursive=False, overwrite=False, keep_ass=False):
    pairs = find_mp4_xml_pairs(input_dir, recursive=recursive)
    if not pairs:
        print("No same-name MP4 + XML pairs found; danmu burn skipped")
        return 0

    print(f"Found {len(pairs)} same-name MP4 + XML pair(s); creating danmu videos")
    failed = 0
    for mp4_path, xml_path in pairs:
        if burn_one(
            mp4_path,
            xml_path,
            ffmpeg_path,
            danmaku_factory_path,
            overwrite=overwrite,
            keep_ass=keep_ass,
        ) != 0:
            failed += 1
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

    print(f"FFmpeg: {ffmpeg_path}")
    print(f"DanmakuFactory: {danmaku_factory_path}")
    return burn_danmu_videos(
        input_dir,
        ffmpeg_path,
        danmaku_factory_path,
        recursive=args.recursive,
        overwrite=args.overwrite,
        keep_ass=args.keep_ass,
    )


if __name__ == "__main__":
    sys.exit(main())
