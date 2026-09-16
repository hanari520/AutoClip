#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Step 1: convert FLV recordings to MP4 using stream copy."""

import argparse
import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


VIDEO_EXT = ".flv"
DEFAULT_FFMPEG_BIN = os.environ.get("AUTOCLIP_FFMPEG_BIN", "")
DEFAULT_FFMPEG = os.path.join(DEFAULT_FFMPEG_BIN, "ffmpeg.exe")


def resolve_ffmpeg():
    return shutil.which("ffmpeg") or (DEFAULT_FFMPEG if os.path.isfile(DEFAULT_FFMPEG) else "")


def print_process_error(result, label, max_lines=12):
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return
    print(f"{label} details:")
    for line in lines[-max_lines:]:
        print(f"  {line}")


def find_flv_files(input_dir, recursive=False):
    if recursive:
        matches = []
        for root, _, files in os.walk(input_dir):
            for name in files:
                if name.lower().endswith(VIDEO_EXT):
                    matches.append(os.path.join(root, name))
        return sorted(matches)

    return sorted(
        os.path.join(input_dir, name)
        for name in os.listdir(input_dir)
        if name.lower().endswith(VIDEO_EXT)
    )


def remove_source_if_ready(flv_path, mp4_path, keep_source=False):
    if keep_source:
        return
    if not os.path.isfile(mp4_path) or os.path.getsize(mp4_path) <= 0:
        print(f"WARNING: MP4 output is not usable; keeping source FLV: {flv_path}")
        return
    try:
        os.remove(flv_path)
        print(f"Deleted source FLV: {flv_path}")
    except OSError as exc:
        print(f"WARNING: failed to delete source FLV: {flv_path} ({exc})")


def convert_one(flv_path, ffmpeg_path, overwrite=False, keep_source=False):
    mp4_path = os.path.splitext(flv_path)[0] + ".mp4"
    if os.path.exists(mp4_path) and not overwrite:
        print(f"Skip existing MP4: {mp4_path}")
        remove_source_if_ready(flv_path, mp4_path, keep_source=keep_source)
        return 0

    tmp_path = os.path.splitext(mp4_path)[0] + ".tmp.mp4"
    if os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-y" if overwrite else "-n",
        "-i", flv_path,
        "-c", "copy",
        "-movflags", "+faststart",
        tmp_path,
    ]
    print(f"Convert: {os.path.basename(flv_path)}")

    result = subprocess.run(
        cmd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        print(f"ERROR: convert failed: {flv_path}")
        print_process_error(result, "FFmpeg")
        return result.returncode

    if os.path.exists(mp4_path) and overwrite:
        os.remove(mp4_path)
    os.replace(tmp_path, mp4_path)
    print(f"Output: {mp4_path}")
    remove_source_if_ready(flv_path, mp4_path, keep_source=keep_source)
    return 0


def main():
    parser = argparse.ArgumentParser(description="AutoClip step 1: convert FLV to MP4 with stream copy")
    parser.add_argument("--input-dir", "-i", required=True, help="recording directory")
    parser.add_argument("--recursive", action="store_true", help="scan subdirectories recursively")
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing MP4 files")
    parser.add_argument("--keep-source", action="store_true", help="keep source FLV after successful conversion")
    args = parser.parse_args()

    input_dir = os.path.abspath(args.input_dir)
    if not os.path.isdir(input_dir):
        print(f"ERROR: directory does not exist: {input_dir}")
        return 1

    flv_files = find_flv_files(input_dir, recursive=args.recursive)
    if not flv_files:
        print("No FLV files found; step 1 has nothing to convert")
        return 0

    ffmpeg_path = resolve_ffmpeg()
    if not ffmpeg_path:
        print(f"ERROR: ffmpeg not found. Check PATH or configured path: {DEFAULT_FFMPEG}")
        return 1

    print(f"FFmpeg: {ffmpeg_path}")
    print(f"Found {len(flv_files)} FLV files; converting to MP4 with ffmpeg -c copy")
    if not args.keep_source:
        print("Source FLV files will be deleted after a successful conversion or confirmed existing MP4")

    failed = 0
    for flv_path in flv_files:
        if convert_one(flv_path, ffmpeg_path, overwrite=args.overwrite, keep_source=args.keep_source) != 0:
            failed += 1

    if failed:
        print(f"Step 1 finished with {failed} failed file(s)")
        return 1

    print("Step 1: FLV to MP4 complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
