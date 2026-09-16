# -*- coding: utf-8 -*-
"""
Data_source.txt 时间戳后处理工具
支持两种模式:
  --buffer:     前后各加 BUFFER_SECONDS 秒缓冲（给剪辑留说话气口），自动检测视频时长、防越界
  --calibrate:  时间校准（对齐官方/非官方录播时间）
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime


# ================= 配置 =================
BUFFER_SECONDS = 1    # buffer 模式前后各加的秒数
DEFAULT_FILE = "Data_source.txt"
# 以下为 calibrate 模式专用
TIME_1 = "00:09:04"   # 官方录播op结束时间 (基准)
TIME_2 = "00:00:55"   # 非官方录播op结束时间 (当前)
# =======================================


def time_str_to_seconds(time_str):
    try:
        t = datetime.strptime(time_str, "%H:%M:%S")
        return t.hour * 3600 + t.minute * 60 + t.second
    except ValueError:
        return 0


def seconds_to_time_str(seconds):
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def extract_json_array(text):
    s = text.find('[')
    e = text.rfind(']')
    if s != -1 and e != -1 and e > s:
        return text[s:e+1]
    return None


def get_video_duration(video_path):
    """用 ffprobe 获取视频时长（秒）"""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'csv=p=0', video_path],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        print(f"  [警告] ffprobe 失败: {video_path} → {e}")
    return None


def find_video_for_segment(video_dir, segment):
    """根据 segment 名查找对应的源视频文件"""
    segment = segment.strip()
    for ext in ('.mp4', '.mkv', '.avi', '.flv', '.ts', '.mov'):
        # 精确匹配：{segment}*.ext
        pattern = re.compile(re.escape(segment) + r'.*' + re.escape(ext) + r'$', re.IGNORECASE)
        for f in os.listdir(video_dir):
            if pattern.match(f):
                return os.path.join(video_dir, f)
        # 兜底：glob 模糊
        import glob
        files = glob.glob(os.path.join(video_dir, f"*{segment[:6]}*{ext}"))
        if files:
            return files[0]
    return None


# ---- buffer 模式 ----

def apply_buffer(file_path, video_dir=None, buffer_seconds=None):
    """
    前后各加 buffer 秒，自动检测视频时长防越界
    start = max(0, start - buffer)
    end   = min(video_duration, end + buffer)
    """
    if buffer_seconds is None:
        buffer_seconds = BUFFER_SECONDS

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            raw = f.read()
    except FileNotFoundError:
        print(f"[错误] 文件不存在: {file_path}")
        return

    json_str = extract_json_array(raw)
    if not json_str:
        data = json.loads(raw)
    else:
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            data = json.loads(raw)

    # 缓存: segment → video_duration（避免重复 ffprobe）
    duration_cache = {}
    modified_count = 0
    clamped_count = 0

    for item in data:
        ts = item.get("timestamp", "")
        if not ts or '-' not in ts:
            continue

        start_str, end_str = ts.split('-', 1)
        start_sec = time_str_to_seconds(start_str.strip())
        end_sec = time_str_to_seconds(end_str.strip())

        new_start = start_sec - buffer_seconds
        new_end = end_sec + buffer_seconds

        # 视频时长检测 + 越界修正
        if video_dir and os.path.isdir(video_dir):
            segment = item.get("segment", "").strip()
            if segment and segment not in duration_cache:
                vid = find_video_for_segment(video_dir, segment)
                if vid:
                    dur = get_video_duration(vid)
                    duration_cache[segment] = dur if dur else float('inf')
                else:
                    duration_cache[segment] = float('inf')

            max_dur = duration_cache.get(segment, float('inf'))
            if max_dur != float('inf'):
                if new_start < 0:
                    new_start = 0
                    clamped_count += 1
                if new_end > max_dur:
                    new_end = max_dur
                    clamped_count += 1

        item["timestamp"] = f"{seconds_to_time_str(new_start)}-{seconds_to_time_str(new_end)}"
        modified_count += 1

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[buffer] 已处理 {modified_count} 条，前后各+{buffer_seconds}s → {file_path}")
    if clamped_count:
        print(f"  (其中 {clamped_count} 处因视频长度越界被修正)")


# ---- calibrate 模式（原有逻辑） ----

def apply_calibration(file_path):
    diff = time_str_to_seconds(TIME_1) - time_str_to_seconds(TIME_2)
    print(f"时间差: {diff}s  ({TIME_1} - {TIME_2})")

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            raw = f.read()
    except FileNotFoundError:
        print(f"[错误] 文件不存在: {file_path}")
        return

    json_str = extract_json_array(raw)
    if not json_str:
        print("[错误] 未找到 JSON 数组")
        return

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"[错误] JSON 解析失败: {e}")
        return

    count = 0
    for item in data:
        ts = item.get("timestamp", "")
        if not ts or '-' not in ts:
            continue
        start_str, end_str = ts.split('-', 1)
        start = time_str_to_seconds(start_str.strip()) - diff
        end = time_str_to_seconds(end_str.strip()) - diff
        item["timestamp"] = f"{seconds_to_time_str(start)}-{seconds_to_time_str(end)}"
        count += 1

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[calibrate] 已校准 {count} 条 → {file_path}")


# ---- CLI ----

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    fp = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_FILE
    # 第三个参数可选：视频目录
    vd = sys.argv[3] if len(sys.argv) > 3 else None

    if mode == '--buffer':
        apply_buffer(fp, video_dir=vd)
    elif mode == '--calibrate':
        apply_calibration(fp)
    else:
        print("用法:")
        print("  python edit_data_source_timestep.py --buffer [文件] [视频目录]     前后各加1s缓冲（可选视频目录做越界检测）")
        print("  python edit_data_source_timestep.py --calibrate [文件]            时间校准")
        print(f"  不传文件默认: {DEFAULT_FILE}")


if __name__ == "__main__":
    main()
