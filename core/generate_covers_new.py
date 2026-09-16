# -*- coding: utf-8 -*-
"""
封面生成新模块 — 多行靠左双色风格
独立模块，不修改 generate_covers.py 任何现有方法。
通过 useNewStyle 参数分流调用：新样式走本模块，旧样式走原流程。
"""
import os
import re
import tempfile
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# 复用原模块的工具函数（只读引用，不修改）
from core.generate_covers import (
    detect_video_info,
    extract_frame,
    crop_to_16_9,
    draw_text_with_multilayer_stroke,
    load_font_cached,
    measure_text,
    parse_timestamp,
    sanitize_filename,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ============================================================
# 新样式配置
# ============================================================
NEW_STYLE_CONFIG = {
    "name": "多行靠左双色风格",
    "layout": "multiline_left",
    "title_size": 170,
    "odd_text_color": (255, 255, 255),    # 奇数行白色字体
    "even_text_color": (255, 225, 0),     # 偶数行黄色字体
    "title_stroke_color": (0, 0, 0),      # 黑色描边
    "title_stroke_width": 12,
    "max_chars_per_line": 8,
    "target_lines": 4,
    "line_spacing_ratio": 1.65,
    "left_margin_ratio": 0.17,
    "vertical_center_ratio": 0.58,
    "gradient_start_y": 0.0,
    "gradient_opacity": 0,
}


# ============================================================
# 文字拆分工具
# ============================================================
def split_text_to_lines(text, max_chars=6, target_lines=4):
    """把一句话拆分成多行，每行尽量不超过 max_chars 个字。
    1. 按标点切分成短语
    2. 去掉每行句尾标点
    3. 统计短语数：≥4用4行策略，<4用3行策略
    4. 贪心填充：将短语均匀分配到目标行数
    """
    if not text:
        return []

    # 标点映射：中文标点 → 英文标点
    punct_map = {
        "，": ",", "。": ".", "！": "!", "？": "?",
        "；": ";", "：": ":", "、": ",",
    }
    # 去掉头尾的标点
    strip_chars = "，。！？；：、,.!?;:\"'""''()（）[]【】《》<>"
    cleaned = text.strip().strip(strip_chars)

    # 句中标点转英文
    for cn, en in punct_map.items():
        cleaned = cleaned.replace(cn, en)

    # 第一步：按英文标点切分成短语
    phrases = re.split(r'([,.!?;:])', cleaned)
    merged = []
    for p in phrases:
        p = p.strip()
        if not p:
            continue
        if p in ',.!?;:' and merged:
            merged[-1] = merged[-1] + p
        else:
            merged.append(p)

    # 第二步：去掉每行句尾标点
    trailing_punct = ",.!?;:，。！？；：、"
    phrases_clean = [p.rstrip(trailing_punct) for p in merged if p.rstrip(trailing_punct)]

    if not phrases_clean:
        return []

    # 第三步：根据短语数决定策略
    if len(phrases_clean) >= 4:
        goal = 4
    elif len(phrases_clean) >= 3:
        goal = 3
    else:
        goal = 3

    # 第四步：贪心填充，将短语均匀分配到 goal 行
    total_chars = sum(len(p) for p in phrases_clean)
    target_avg = total_chars / goal

    lines = []
    current_line = ""
    lines_planned = 0

    for i, phrase in enumerate(phrases_clean):
        # 如果是最后几个短语需要凑够剩余行数
        remaining_phrases = len(phrases_clean) - i
        remaining_lines = goal - lines_planned
        must_start_new = remaining_phrases <= remaining_lines

        # 当前行加上这个短语是否超过目标平均长度
        if current_line and (len(current_line) + len(phrase) > target_avg or must_start_new):
            lines.append(current_line)
            lines_planned += 1
            current_line = phrase
        else:
            current_line = current_line + phrase if current_line else phrase

    if current_line:
        lines.append(current_line)
        lines_planned += 1

    # 第五步：如果行数不足 goal，拆分最长行
    while len(lines) < goal:
        max_idx = max(range(len(lines)), key=lambda i: len(lines[i]))
        if len(lines[max_idx]) <= 1:
            break
        half = len(lines[max_idx]) // 2
        right = lines[max_idx][half:]
        lines[max_idx] = lines[max_idx][:half]
        lines.insert(max_idx + 1, right)

    # 第六步：如果行数超过 goal，合并最短相邻行
    while len(lines) > goal:
        min_len = float('inf')
        merge_idx = 0
        for i in range(len(lines) - 1):
            combined = len(lines[i]) + len(lines[i + 1])
            if combined < min_len:
                min_len = combined
                merge_idx = i
        lines[merge_idx] = lines[merge_idx] + lines[merge_idx + 1]
        lines.pop(merge_idx + 1)

    return lines


# ============================================================
# 多行靠左双色渲染
# ============================================================
def render_multiline_left(img, text_lines, style_config, font_path):
    """多行靠左对齐，奇数行白字黑描边，偶数行黄字黑描边。兼容 4:3 安全区，动态字号+位置。"""
    width, height = img.size

    font_scale = width / 1920.0
    base_size = int(style_config.get("title_size", 170) * font_scale)
    stroke_color = style_config.get("title_stroke_color", (0, 0, 0))
    max_chars_per_line = style_config.get("max_chars_per_line", 8)

    # 4:3 安全区
    safe_zone_ratio = 0.75
    crop_side = int(width * (1 - safe_zone_ratio) / 2)
    safe_left = crop_side
    safe_right = width - crop_side
    base_left_margin = max(int(width * style_config.get("left_margin_ratio", 0.17)), safe_left + 10)

    # ── 动态字号：按字数比例缩小/放大 ──
    max_line_chars = max(len(l) for l in text_lines) if text_lines else 0
    dynamic_size = base_size
    if max_line_chars > max_chars_per_line:
        ratio = max_chars_per_line / max_line_chars
        dynamic_size = max(36, int(base_size * ratio * 1.05))
    dynamic_stroke = max(2, int(style_config.get("title_stroke_width", 12) * font_scale * (dynamic_size / base_size)))

    # 测量像素宽度
    try:
        probe_font = load_font_cached(font_path, dynamic_size) if font_path else ImageFont.load_default()
    except Exception:
        probe_font = ImageFont.load_default()
    draw_probe = ImageDraw.Draw(Image.new("RGBA", img.size, (0, 0, 0, 0)))
    max_line_w = max(measure_text(l, probe_font) for l in text_lines) if text_lines else 0
    available_w = safe_right - base_left_margin - 40

    # 二次校正：像素仍溢出则缩小
    if max_line_w > available_w:
        scale = available_w / max_line_w
        dynamic_size = max(36, int(dynamic_size * scale * 0.97))
        dynamic_stroke = max(2, int(dynamic_stroke * scale * 0.97))
        try:
            probe_font = load_font_cached(font_path, dynamic_size) if font_path else ImageFont.load_default()
        except Exception:
            probe_font = ImageFont.load_default()
        draw_probe = ImageDraw.Draw(Image.new("RGBA", img.size, (0, 0, 0, 0)))
        max_line_w = max(measure_text(l, probe_font) for l in text_lines)

    # ── 动态左距：短文字微调向右 ──
    if max_line_w < available_w * 0.5:
        left_margin = int(base_left_margin + (available_w - max_line_w) * 0.15)
    else:
        left_margin = base_left_margin
    # 右边界约束：绝不超出 4:3 安全区右侧（留 10px 余量）
    if left_margin + max_line_w > safe_right - 10:
        left_margin = max(safe_left + 10, safe_right - 10 - max_line_w)

    line_h = int(dynamic_size * style_config.get("line_spacing_ratio", 1.65))
    total_h = line_h * len(text_lines)
    vert_ratio = style_config.get("vertical_center_ratio", 0.58)
    if len(text_lines) < style_config.get("target_lines", 4):
        vert_ratio += 0.04
    start_y = int(height * vert_ratio) - total_h // 2

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    odd_color = style_config.get("odd_text_color", (255, 255, 255))
    even_color = style_config.get("even_text_color", (255, 225, 0))

    for i, line in enumerate(text_lines):
        y = start_y + i * line_h
        text_color = odd_color if (i % 2 == 0) else even_color

        draw_text_with_multilayer_stroke(
            draw, (left_margin, y), line, probe_font,
            text_color, stroke_color, dynamic_stroke,
            anchor="lm",
        )

    return Image.alpha_composite(img, overlay)


# ============================================================
# 新样式封面生成（单张）
# ============================================================
def generate_cover_new(video_path, start_sec, end_sec, cover_text, output_path, font_path, style_config=None):
    """新样式封面生成：从视频截帧 + 多行靠左双色文字烧录。"""
    if style_config is None:
        style_config = NEW_STYLE_CONFIG

    if not video_path:
        print(f"  [错误] video_path 为空,无法生成封面")
        return False

    # 检测视频信息
    vid_width, vid_height, actual_duration = detect_video_info(video_path)
    if not vid_width or not vid_height:
        vid_width, vid_height = 1920, 1080

    # 截帧：固定取切片内容正中间，保证重新生成时画面不变
    is_subbed_clip = '_subbed' in os.path.basename(video_path)
    clip_duration = end_sec - start_sec
    from_original = bool(actual_duration) and clip_duration > 0 and actual_duration > clip_duration + 30
    if is_subbed_clip or not from_original:
        timestamp = actual_duration * 0.5 if actual_duration else 0.5
    else:
        timestamp = start_sec + clip_duration * 0.5

    temp_frame = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
    temp_frame.close()

    try:
        if not extract_frame(video_path, timestamp, temp_frame.name):
            if not extract_frame(video_path, 1.0, temp_frame.name):
                print(f"  [警告] 无法从视频提取帧，使用纯色背景")
                img = Image.new('RGBA', (vid_width, vid_height), (30, 30, 30, 255))
        else:
            img = Image.open(temp_frame.name).convert('RGBA')

        # 16:9 裁剪 + 缩放
        img = crop_to_16_9(img)
        if img.size != (1920, 1080):
            img = img.resize((1920, 1080), Image.LANCZOS)

        # 拆分文字
        text_lines = split_text_to_lines(
            cover_text,
            max_chars=style_config.get("max_chars_per_line", 8),
            target_lines=style_config.get("target_lines", 4),
        )
        print(f"  [拆分] {cover_text} → {text_lines}")

        # 渲染
        img = render_multiline_left(img, text_lines, style_config, font_path)

        # 锐化并保存
        final_img = img.convert("RGB")
        final_img = final_img.filter(ImageFilter.SHARPEN)
        final_img.save(output_path, quality=95)

        print(f"  [OK] 封面生成成功: {os.path.basename(output_path)}")
        return True

    except Exception as e:
        print(f"  [错误] 封面生成失败: {e}")
        return False
    finally:
        if os.path.exists(temp_frame.name):
            os.remove(temp_frame.name)


# ============================================================
# 新样式批量入口
# ============================================================
def generate_covers_new_style(input_dir, output_dir, video_dir, match_subbed, font_path, style_config=None):
    """新样式批量封面生成入口。读取 Data_source.txt，使用 cover_text 字段。"""
    import json
    from utils.video_filter import filter_danmaku_versions

    if style_config is None:
        style_config = NEW_STYLE_CONFIG

    print("=" * 60)
    print("封面生成工具 [新样式]")
    print(f"样式: {style_config.get('name', '多行靠左双色风格')}")
    print("=" * 60)
    print(f"输入目录: {input_dir}")
    print(f"输出目录: {output_dir}")
    print(f"字体: {os.path.basename(font_path or '默认')}")
    print()

    # 1. 读取 Data_source.txt
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
    print("[OK] 输出目录已创建")

    # 3. 查找视频文件
    print("\n[3/4] 查找视频文件...")
    print(f"   视频目录: {video_dir}")
    if match_subbed:
        print(f"   匹配模式: 切片 _subbed.mp4（抽帧优先用同名原视频，避免封面带字幕）")

    video_files = []
    video_exts = ('.mp4', '.flv', '.mkv', '.mov', '.ts', '.avi', '.wmv')
    if os.path.isdir(video_dir):
        for f in os.listdir(video_dir):
            if not f.lower().endswith(video_exts):
                continue
            video_files.append(os.path.join(video_dir, f))

    video_files = filter_danmaku_versions(video_files)

    # 抽帧来源优先原视频：同目录存在同名未烧录视频（xxx.mp4 / xxx-弹幕版.mp4）时，
    # 用它代替 xxx_subbed.mp4 作为抽帧源，避免封面带上烧录的字幕。
    # 仅当没有原视频时才回退到 _subbed.mp4 本身。
    subbed_sources = []
    for vf in video_files:
        base = os.path.basename(vf)
        if base.endswith('_subbed.mp4'):
            raw_base = base[:-len('_subbed.mp4')]
            raw_candidates = [
                os.path.join(video_dir, raw_base + ext)
                for ext in ('.mp4', '.mkv', '.mov', '.ts', '.avi', '.flv')
            ]
            raw = next((c for c in raw_candidates if os.path.isfile(c)), None)
            if raw:
                print(f"  [封面抽帧] 用原视频替代 {base} → {os.path.basename(raw)}")
                subbed_sources.append(raw)
            else:
                subbed_sources.append(vf)
        else:
            subbed_sources.append(vf)
    video_files = subbed_sources

    if not video_files:
        print("[警告] 未找到视频文件,将使用纯色背景")
    else:
        print(f"[OK] 找到 {len(video_files)} 个视频文件")

    # 4. 生成封面
    print("\n[4/4] 生成封面...")
    success_count = 0
    fail_count = 0

    for i, clip in enumerate(clips, 1):
        segment = clip.get('segment', 'unknown').strip()
        title = clip.get('title', f'切片_{i}')
        # 新字段：cover_text（单字段一句话）
        cover_text = clip.get('cover_text', '')
        # 兼容旧字段：如果没有 cover_text，尝试拼接 cover_text_1 + cover_text_2
        if not cover_text:
            ct1 = clip.get('cover_text_1', '')
            ct2 = clip.get('cover_text_2', '')
            cover_text = (ct1 + '，' + ct2) if (ct1 and ct2) else (ct1 or ct2)

        # 解析时间戳
        time_range = clip.get('timestamp', '00:00:00-00:00:00')
        start_str, end_str = time_range.split('-', 1)
        start_sec = parse_timestamp(start_str)
        end_sec = parse_timestamp(end_str)

        print(f"\n[{i}/{len(clips)}] {title}")
        print(f"   时间段: {start_str} - {end_str}")
        print(f"   封面文字: {cover_text}")

        # 查找视频文件（标题精确优先 → 带括号 → 去空格 → 原始包含 → 时间前缀）
        video_path = None
        segment_nospace = segment.replace(' ', '')
        safe_title = sanitize_filename(title)
        for vf in video_files:
            vf_name = os.path.basename(vf)
            if vf_name.startswith(f"[{segment}] {safe_title}") and vf_name.endswith('.mp4'):
                video_path = vf
                break
        if not video_path:
            for vf in video_files:
                vf_name = os.path.basename(vf)
                vf_name_nospace = vf_name.replace(' ', '')
                if vf_name.startswith(f"[{segment}]"):
                    video_path = vf
                    break
                if segment_nospace and segment_nospace in vf_name_nospace:
                    video_path = vf
                    break
                if segment in vf_name or segment.replace('-', '_') in vf_name:
                    video_path = vf
                    break
                time_prefix = segment.split(' ')[0] if ' ' in segment else segment
                if time_prefix and vf_name.startswith(time_prefix):
                    video_path = vf
                    break

        if not video_path:
            print(f"  [跳过] 未找到 segment={segment} 对应的视频")
            fail_count += 1
            continue

        print(f"   使用视频: {os.path.basename(video_path)}")

        # 生成文件名
        safe_title = sanitize_filename(title)
        cover_filename = f"[{segment}] {safe_title}.jpg"
        cover_output = os.path.join(output_dir, cover_filename)

        # 跳过已存在
        if os.path.exists(cover_output) and os.path.getsize(cover_output) > 0:
            print(f"  [跳过] 封面已存在: {cover_filename}")
            success_count += 1
            continue

        # 生成封面
        try:
            if generate_cover_new(video_path, start_sec, end_sec, cover_text, cover_output, font_path, style_config):
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            print(f"  [错误] 封面生成异常: {e}")
            fail_count += 1

    print(f"\n{'='*60}")
    print("处理完成!")
    print(f"成功: {success_count} 个")
    print(f"失败: {fail_count} 个")
    print(f"\n输出目录: {output_dir}")
