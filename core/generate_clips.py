# -*- coding: utf-8 -*-
"""
切片生成脚本
根据Data_source.txt中的元数据，使用FFmpeg生成切片视频（纯画面，不含字幕和封面）
封面生成由步骤6 generate_covers.py 独立完成（Pillow + 速度线 + 多风格）
"""
import os
import sys
import re
import json
import subprocess
from pathlib import Path

# 设置UTF-8编码
sys.stdout.reconfigure(encoding='utf-8')
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from utils.video_filter import filter_danmaku_versions

# ==========================================
# 用户配置区域（可通过命令行参数覆盖）
# ==========================================

def parse_args():
    """解析命令行参数"""
    import argparse
    parser = argparse.ArgumentParser(description='切片生成工具')
    parser.add_argument('--input-dir', '-i', type=str,
                        help='输入目录（包含mp4和Data_source.txt）')
    parser.add_argument('--output-dir', '-o', type=str,
                        help='输出目录（默认：输入目录/切片）')
    parser.add_argument('--ffmpeg', '-f', type=str, default='ffmpeg',
                        help='FFmpeg路径')
    return parser.parse_args()

# 默认配置
DEFAULT_CONFIG = {
    # 输入目录（包含mp4和Data_source.txt）
    "input_dir": r"<录播根目录>\<主播名>\<日期 标题>",
    
    # 输出目录
    "output_dir": None,  # 如果为None，则使用 input_dir/切片
    
    # FFmpeg路径（如果不在PATH中，请填写完整路径）
    "ffmpeg_path": "ffmpeg",
}

def get_config():
    """获取配置，命令行参数优先"""
    args = parse_args()
    config = DEFAULT_CONFIG.copy()
    
    # 命令行参数覆盖默认值
    if args.input_dir:
        config['input_dir'] = args.input_dir
    if args.output_dir:
        config['output_dir'] = args.output_dir
    elif config['output_dir'] is None:
        config['output_dir'] = os.path.join(config['input_dir'], '切片')
    if args.ffmpeg:
        config['ffmpeg_path'] = args.ffmpeg
    
    return config

CONFIG = get_config()

# ==========================================
# 工具函数
# ==========================================

def parse_timestamp(timestamp_str):
    """解析时间戳字符串，返回开始和结束时间（秒）
    自动修正异常格式:
      - 毫秒: 00:00:00.010-00:00:37.109 → 00:00:00-00:00:37
    """
    # 剥离毫秒（.010 / .109 等）
    clean = re.sub(r'\.\d+', '', timestamp_str)
    parts = clean.split('-')
    if len(parts) != 2:
        return None, None
    
    def time_to_seconds(t):
        """将HH:MM:SS或HH:MM:SS.ms格式转换为秒"""
        t = t.strip()
        try:
            h, m, s = map(float, t.split(':'))
            return h * 3600 + m * 60 + s
        except:
            return None
    
    start = time_to_seconds(parts[0])
    end = time_to_seconds(parts[1])
    return start, end


def get_video_file(input_dir, segment):
    """根据segment查找对应的视频文件"""
    # 清理segment中的空格
    segment = segment.strip()
    
    # 尝试直接匹配
    for ext in ['.mp4', '.mkv', '.avi']:
        video_path = os.path.join(input_dir, f"{segment}*{ext}")
        import glob
        files = filter_danmaku_versions(glob.glob(video_path))
        if files:
            return files[0]
    
    # 尝试模糊匹配（前6个字符）
    segment_prefix = segment[:6] if len(segment) >= 6 else segment
    for ext in ['.mp4', '.mkv', '.avi']:
        pattern = os.path.join(input_dir, f"*{segment_prefix}*{ext}")
        import glob
        files = filter_danmaku_versions(glob.glob(pattern))
        if files:
            return files[0]
    
    return None


def generate_clip(video_path, output_path, start_time, end_time, title):
    """使用FFmpeg生成切片视频"""
    duration = end_time - start_time
    
    cmd = [
        CONFIG['ffmpeg_path'],
        '-ss', str(start_time),
        '-t', str(duration),
        '-i', video_path,
        '-c', 'copy',
        '-avoid_negative_ts', 'make_zero',
        '-y',
        output_path
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if result.returncode == 0:
            print(f"  [OK] 切片生成成功: {os.path.basename(output_path)}")
            return True
        else:
            print(f"  [错误] 切片生成失败: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"  [错误] 切片生成异常: {e}")
        return False


def generate_cover(output_path, title, cover_text_1, cover_text_2, style):
    """[已废弃] 简易封面生成 — 已移除，封面由步骤6 generate_covers.py 独立完成

    保留函数签名仅为向后兼容（万一有外部调用），实际不执行任何操作。
    """
    return True


def sanitize_filename(filename):
    """清理文件名中的非法字符"""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    return filename[:100]  # 限制长度


# ==========================================
# 主程序
# ==========================================

def main():
    input_dir = CONFIG['input_dir']
    output_dir = CONFIG['output_dir']
    
    print("=" * 60)
    print("切片生成工具")
    print("=" * 60)
    print(f"输入目录: {input_dir}")
    print(f"输出目录: {output_dir}")
    print()
    
    # 1. 读取Data_source.txt
    data_source_path = os.path.join(input_dir, "Data_source.txt")
    if not os.path.exists(data_source_path):
        print(f"[错误] 未找到Data_source.txt: {data_source_path}")
        return
    
    print("[1/4] 读取切片元数据...")
    with open(data_source_path, 'r', encoding='utf-8') as f:
        clips = json.load(f)
    
    print(f"[OK] 读取到 {len(clips)} 个切片")
    
    # 2. 创建输出目录
    print("\n[2/4] 创建输出目录...")
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, "视频"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "封面"), exist_ok=True)
    print("[OK] 输出目录已创建")
    
    # 3. 生成切片（纯视频，不含封面 — 封面由步骤6 generate_covers.py 独立完成）
    print("\n[3/4] 生成切片...")
    success_count = 0
    fail_count = 0

    for i, clip in enumerate(clips, 1):
        segment = clip.get('segment', 'unknown').strip()
        timestamp = clip.get('timestamp', '')
        title = clip.get('title', f'切片_{i}')

        print(f"\n[{i}/{len(clips)}] {title}")
        print(f"  来源: {segment}, 时间: {timestamp}")

        # 解析时间戳
        start_time, end_time = parse_timestamp(timestamp)
        if start_time is None or end_time is None:
            print(f"  [错误] 时间戳格式错误: {timestamp}")
            fail_count += 1
            continue

        # 查找视频文件
        video_path = get_video_file(input_dir, segment)
        if not video_path:
            print(f"  [错误] 未找到视频文件: {segment}")
            fail_count += 1
            continue

        # 生成文件名
        safe_title = sanitize_filename(title)
        clip_filename = f"[{segment}] {safe_title}.mp4"

        clip_output = os.path.join(output_dir, "视频", clip_filename)

        # 跳过已存在的切片
        if os.path.exists(clip_output) and os.path.getsize(clip_output) > 0:
            print(f"  [跳过] 切片已存在: {os.path.basename(clip_output)}")
            success_count += 1
            continue

        # 生成切片（纯视频）
        if generate_clip(video_path, clip_output, start_time, end_time, title):
            success_count += 1
        else:
            fail_count += 1
            continue

        # 封面生成已移除 — 由步骤6 generate_covers.py 用 Pillow + 速度线独立完成
    
    # 4. 完成
    print(f"\n{'='*60}")
    print("处理完成!")
    print(f"{'='*60}")
    print(f"成功: {success_count} 个")
    print(f"失败: {fail_count} 个")
    print(f"\n输出目录: {output_dir}")
    print(f"  - 视频: {os.path.join(output_dir, '视频')}")
    print(f"  - 封面: {os.path.join(output_dir, '封面')}")


if __name__ == "__main__":
    main()
