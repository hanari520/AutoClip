# -*- coding: utf-8 -*-
"""
封面生成脚本
使用PIL生成切片封面,从视频片段中随机截取帧作为背景
样式参考 Auto_clip.py 封面样式配置区
"""
import functools
import os
import sys
import json
import random
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# 设置UTF-8编码
sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 添加项目根目录到路径，支持 from utils.xxx import xxx
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'utils'))

# ==========================================
# 用户配置区域(可通过命令行参数覆盖)
# ==========================================

def parse_args():
    """解析命令行参数"""
    import argparse
    parser = argparse.ArgumentParser(description='封面生成工具')
    parser.add_argument('--input-dir', '-i', type=str,
                        help='输入目录(包含Data_source.txt和视频文件)')
    parser.add_argument('--output-dir', '-o', type=str,
                        help='输出目录(默认:输入目录/切片/封面)')
    parser.add_argument('--style', '-s', type=str, default='style1',
                        help='封面样式: style1(上白下黄), style2(上黄下白), style3(居中大字), style4(艺术简洁)')
    parser.add_argument('--font', '-f', type=str,
                        help='字体文件路径或序号(默认使用项目字体目录中的字体)')
    parser.add_argument('--use-new-style', action='store_true',
                        help='启用新样式(多行靠左双色风格),走 generate_covers_new 模块;不启用则走原有流程')
    parser.add_argument('--video-dir', type=str,
                        help='视频文件目录(默认与input-dir相同,用于切片视频在子目录的场景)')
    parser.add_argument('--match-subbed', action='store_true',
                        help='只匹配 _subbed.mp4 视频(烧录过字幕的版本)')
    return parser.parse_args()

# 默认配置
DEFAULT_CONFIG = {
    # 输入目录(包含Data_source.txt和视频文件)
    "input_dir": r"<录播根目录>\<主播名>\<日期 标题>",

    # 输出目录
    "output_dir": None,  # 如果为None,则使用 input_dir/切片/封面

    # 字体路径(参考Auto_clip.py)
    # 默认使用项目字体目录中的第一个字体文件,如果没有则使用系统字体
    "font_path": None,  # 将在get_config()中动态设置

    # 封面样式配置(参考Auto_clip.py封面样式配置区)
    "cover": {
        "active_style": "style1",
        "style1": {
            "name": "上白下黄震撼风格",
            "layout": "double",
            "title_position": "split",
            "title_top_y_ratio": 0.2,
            "title_bottom_y_ratio": 0.75,
            "title_size": 150,
            "title_top_color": (255, 255, 255),
            "title_bottom_color": (255, 225, 0),
            "title_stroke_color": (0, 0, 0),
            "title_stroke_width": 12,
            "gradient_start_y": 0.0,
            "gradient_opacity": 0,
            "show_summary": False
        },
        "style2": {
            "name": "上黄下白震撼风格",
            "layout": "double",
            "title_position": "split",
            "title_top_y_ratio": 0.2,
            "title_bottom_y_ratio": 0.75,
            "title_size": 150,
            "title_top_color": (255, 255, 0),
            "title_bottom_color": (255, 225, 255),
            "title_stroke_color": (0, 0, 0),
            "title_stroke_width": 12,
            "gradient_start_y": 0.0,
            "gradient_opacity": 0,
            "show_summary": False
        },
        "style3": {
            "name": "居中大字醒目风格",
            "layout": "center",
            "title_position": "center",
            "title_y_ratio": 0.7,
            "title_size": 150,
            "title_color": (255, 225, 0),
            "title_stroke_color": (0, 0, 0),
            "title_stroke_width": 12,
            "gradient_start_y": 0.0,
            "gradient_opacity": 10,
            "show_summary": False
        },
        "style4": {
            "name": "艺术简洁风格",
            "layout": "center",
            "title_position": "center",
            "title_y_ratio": 0.5,
            "title_size": 120,
            "title_color": (255, 255, 255),
            "title_stroke_color": (50, 50, 50),
            "title_stroke_width": 8,
            "gradient_start_y": 0.0,
            "gradient_opacity": 0,
            "show_summary": False,
            "blur_background": False,
            "blur_radius": 0
        },
        "style5": {
            "name": "多行靠左双色风格",
            "layout": "multiline_left",
            "title_size": 170,
            "odd_text_color": (255, 255, 255),
            "even_text_color": (255, 225, 0),
            "title_stroke_color": (0, 0, 0),
            "title_stroke_width": 12,
            "max_chars_per_line": 8,
            "target_lines": 4,
            "line_spacing_ratio": 1.65,
            "left_margin_ratio": 0.17,
            "vertical_center_ratio": 0.58,
            "gradient_start_y": 0.0,
            "gradient_opacity": 0,
            "show_summary": False
        }
    }
}

def get_config():
    """获取配置,命令行参数优先"""
    args = parse_args()
    config = DEFAULT_CONFIG.copy()

    # 命令行参数覆盖默认值
    if args.input_dir:
        config['input_dir'] = args.input_dir
    if args.output_dir:
        config['output_dir'] = args.output_dir
    elif config['output_dir'] is None:
        config['output_dir'] = os.path.join(config['input_dir'], '切片', '封面')
    if args.style:
        config['cover']['active_style'] = args.style

    # 新样式分流标志
    config['use_new_style'] = args.use_new_style

    # 视频目录(用于切片视频在子目录的场景)
    config['video_dir'] = args.video_dir if args.video_dir else None
    config['match_subbed'] = args.match_subbed

    # 设置字体路径
    if args.font:
        # 检查是否是数字序号
        if args.font.isdigit():
            font_idx = int(args.font) - 1  # 转换为0-based索引
            all_fonts = list_all_fonts()
            if 0 <= font_idx < len(all_fonts):
                config['font_path'] = all_fonts[font_idx][0]
                print(f"选择字体 #{args.font}: {all_fonts[font_idx][1]}")
            else:
                print(f"[警告] 字体序号 {args.font} 超出范围,使用默认字体")
                config['font_path'] = find_font_auto()
        else:
            # 用户通过命令行指定了字体路径
            config['font_path'] = args.font
    else:
        # 自动查找字体:优先项目字体目录,然后系统字体
        config['font_path'] = find_font_auto()

    return config


CONFIG = None  # 将在模块加载完成后初始化

def _init_config():
    """延迟初始化配置,确保所有函数都已定义"""
    global CONFIG
    CONFIG = get_config()


def find_font_auto():
    """自动查找可用字体,优先项目字体,其次系统字体"""
    # 1. 优先使用项目字体目录中的字体
    project_font_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "font")
    if os.path.exists(project_font_dir):
        for f in sorted(os.listdir(project_font_dir)):
            if f.lower().endswith(('.ttf', '.otf', '.ttc')):
                return os.path.join(project_font_dir, f)

    # 2. 使用系统字体
    system_font_dir = "C:/Windows/Fonts"
    preferred_fonts = [
        "msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc",
        "simkai.ttf", "simfang.ttf", "STXIHEI.TTF", "STKAITI.TTF"
    ]
    for font_name in preferred_fonts:
        font_path = os.path.join(system_font_dir, font_name)
        if os.path.exists(font_path):
            return font_path

    # 3. 返回任意可用字体
    if os.path.exists(system_font_dir):
        for f in sorted(os.listdir(system_font_dir)):
            if f.lower().endswith(('.ttf', '.otf', '.ttc')):
                return os.path.join(system_font_dir, f)

    return None

# ==========================================
# 工具函数
# ==========================================

def get_project_fonts():
    """获取项目字体目录中的所有字体文件"""
    project_font_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "font")
    fonts = []
    if os.path.exists(project_font_dir):
        for f in sorted(os.listdir(project_font_dir)):
            if f.lower().endswith(('.ttf', '.otf', '.ttc')):
                fonts.append(os.path.join(project_font_dir, f))
    return fonts


def get_system_fonts():
    """获取系统字体目录中的常用中文字体"""
    system_font_dir = "C:/Windows/Fonts"
    # 优先的中文字体(按优先级排序)
    preferred_fonts = [
        "msyh.ttc", "msyhbd.ttc", "msyhl.ttc",  # 微软雅黑
        "simhei.ttf",  # 黑体
        "simsun.ttc", "simsunb.ttf",  # 宋体
        "simkai.ttf",  # 楷体
        "simfang.ttf",  # 仿宋
        "STXIHEI.TTF", "STKAITI.TTF", "STSONG.TTF", "STFANGSO.TTF",  # 华文系列
        "FZSTK.TTF", "FZYTK.TTF",  # 方正系列
        "Deng.ttf", "Dengb.ttf", "Dengl.ttf",  # 等线
        "MiSans-Regular.otf",  # MiSans
        "NotoSansSC-VF.ttf", "NotoSerifSC-VF.ttf",  # Noto
    ]

    fonts = []
    for font_name in preferred_fonts:
        font_path = os.path.join(system_font_dir, font_name)
        if os.path.exists(font_path):
            fonts.append(font_path)

    # 如果没有找到优先字体,返回所有可用的ttf/otf字体
    if not fonts and os.path.exists(system_font_dir):
        for f in sorted(os.listdir(system_font_dir)):
            if f.lower().endswith(('.ttf', '.otf', '.ttc')):
                fonts.append(os.path.join(system_font_dir, f))

    return fonts





def list_all_fonts():
    """列出所有可用字体,返回 (路径, 名称, 来源) 列表,项目字体置顶"""
    fonts = []

    # 项目字体(置顶)
    project_font_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "font")
    if os.path.exists(project_font_dir):
        for f in sorted(os.listdir(project_font_dir)):
            if f.lower().endswith(('.ttf', '.otf', '.ttc')):
                path = os.path.join(project_font_dir, f)
                name = os.path.splitext(f)[0]
                fonts.append((path, name, "项目字体"))

    # 系统字体
    system_fonts = get_system_fonts()
    for path in system_fonts:
        name = os.path.splitext(os.path.basename(path))[0]
        # 去重
        if path not in [f[0] for f in fonts]:
            fonts.append((path, name, "系统字体"))

    return fonts


@functools.lru_cache(maxsize=32)
def load_font_cached(font_path, size):
    """带缓存的字体加载,同一 (路径, 字号) 只解析一次,新旧两套渲染共用"""
    return ImageFont.truetype(font_path, size)


def get_font(size, font_path=None):
    """获取字体,优先使用指定路径,其次使用配置中的路径"""
    if font_path is None:
        font_path = CONFIG.get('font_path')

    if font_path and os.path.exists(font_path):
        try:
            return load_font_cached(font_path, size)
        except Exception as e:
            print(f"  [警告] 字体加载失败: {font_path}, 错误: {e}")

    # 备用字体列表
    fallback_fonts = [
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
    ]

    for fp in fallback_fonts:
        if os.path.exists(fp):
            try:
                return load_font_cached(fp, size)
            except:
                continue

    return ImageFont.load_default()


def detect_video_resolution(video_path):
    """用 ffprobe 检测视频实际分辨率，返回 (width, height) 或 (None, None)"""
    w, h, _ = detect_video_info(video_path)
    return w, h


@functools.lru_cache(maxsize=64)
def detect_video_info(video_path):
    """用 ffprobe 检测视频分辨率 + 实际时长，返回 (width, height, duration_sec)。
    同一视频在一次运行内结果不变，按路径缓存避免每张封面重复探测。"""
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_streams', '-show_format', video_path
        ], capture_output=True, text=True, encoding='utf-8')
        info = json.loads(result.stdout)
        w = h = dur = None
        for stream in info.get('streams', []):
            if stream.get('codec_type') == 'video':
                w = stream.get('width', 0)
                h = stream.get('height', 0)
                if w <= 0:
                    w = None
                if h <= 0:
                    h = None
                break
        try:
            dur = float(info.get('format', {}).get('duration', 0))
        except (ValueError, TypeError):
            dur = None
        return w, h, dur if dur and dur > 0 else None
    except Exception:
        pass
    return None, None, None


def extract_frame(video_path, timestamp_sec, output_path):
    """从视频中提取指定时间戳的帧"""
    cmd = [
        'ffmpeg', '-ss', str(timestamp_sec), '-i', video_path,
        '-frames:v', '1', '-update', '1', '-q:v', '2', '-y', output_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        err = result.stderr.decode('utf-8', errors='replace').strip()
        # 只打印最后一行有意义的信息
        last_lines = [l for l in err.split('\n') if l.strip()][-2:]
        print(f"  [ffmpeg错误] {' | '.join(last_lines)}")
    return result.returncode == 0 and os.path.exists(output_path)


def crop_to_16_9(img):
    """将图像居中裁剪为 16:9 比例，适应B站封面展示。
    竖屏视频(如1080×1920)裁高度，超宽视频裁宽度。"""
    w, h = img.size
    target_ratio = 16 / 9
    current_ratio = w / h

    # 容差 0.5%，接近 16:9 就不裁
    if abs(current_ratio - target_ratio) < 0.005:
        return img

    if current_ratio > target_ratio:
        # 太宽：裁左右，保留中间
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        # 太窄/竖屏：裁上下，保留中间
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))


@functools.lru_cache(maxsize=128)
def _font_cmap(font_path):
    """字体覆盖的 unicode 码点集合（用于缺字形检测）。解析失败返回 None。"""
    try:
        from fontTools.ttLib import TTFont
        f = TTFont(font_path, fontNumber=0, lazy=True)
        cmap = set()
        for table in f['cmap'].tables:
            cmap.update(table.cmap.keys())
        f.close()
        return cmap
    except Exception:
        return None


@functools.lru_cache(maxsize=8)
def _fallback_font_paths():
    """缺字形备用字体链: 项目字体目录(除主字体外) + 系统常用中文字体, 存在才返回"""
    proj = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets', 'font')
    candidates = []
    if os.path.isdir(proj):
        for f in sorted(os.listdir(proj)):
            if f.lower().endswith(('.ttf', '.otf', '.ttc')):
                candidates.append(os.path.join(proj, f))
    candidates += [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
    ]
    return [p for p in candidates if os.path.exists(p)]


def split_text_runs(text, font):
    """
    把文本按字形可用性切成 [(font, seg), ...]:
    主字体没有的字形(如"栞")分配到备用字体, 避免画成空白。
    主字体无法解析或无缺字时返回单段原样。
    """
    font_path = getattr(font, 'path', None)
    if not font_path or not os.path.exists(font_path):
        return [(font, text)]
    cmap = _font_cmap(font_path)
    if cmap is None:
        return [(font, text)]

    fallback_cmaps = None  # 懒加载: [(path, cmap_or_None), ...]

    def font_for_char(ch):
        if ch == '\n' or ord(ch) in cmap:
            return font
        nonlocal fallback_cmaps
        if fallback_cmaps is None:
            exclude = os.path.abspath(font_path)
            fallback_cmaps = [(p, _font_cmap(p)) for p in _fallback_font_paths()
                              if os.path.abspath(p) != exclude]
        for fp, c in fallback_cmaps:
            if c and ord(ch) in c:
                return load_font_cached(fp, font.size)
        return font  # 备用链也没有 → 保持主字体(至少不崩)

    runs = []
    cur_font = None
    cur_chars = []
    for ch in text:
        f = font_for_char(ch)
        if f is not cur_font and cur_chars:
            runs.append((cur_font, ''.join(cur_chars)))
            cur_chars = []
        cur_font = f
        cur_chars.append(ch)
    if cur_chars:
        runs.append((cur_font, ''.join(cur_chars)))
    return runs


def measure_text(text, font):
    """fallback 感知的文本宽度: 缺字形字符按备用字体测宽, 与绘制一致"""
    total = 0.0
    for f, seg in split_text_runs(text, font):
        total += f.getlength(seg)
    return total


def draw_text_with_multilayer_stroke(draw, position, text, font, fill_color, stroke_color, stroke_width, anchor="mm"):
    """绘制带多层描边的文字(参考Auto_clip.py); 缺字形字符自动回退备用字体"""
    x, y = position

    # 获取画布尺寸
    img = draw._image
    width, height = img.size

    # 字形回退分段: 仅当确实出现缺字(多段)且锚点支持水平定位时才逐段绘制
    runs = split_text_runs(text, font)
    multi_font = len(runs) > 1 and anchor in ("mm", "lm")
    if multi_font:
        total_w = measure_text(text, font)
        x0 = x - total_w / 2 if anchor == "mm" else x

    def paint(layer, fill):
        if multi_font:
            cursor = x0
            for f, seg in runs:
                layer.text((cursor, y), seg, font=f, fill=fill, anchor="lm")
                cursor += f.getlength(seg)
        else:
            layer.text((x, y), text, font=font, fill=fill, anchor=anchor)

    # 创建临时图层用于绘制文字主体
    text_layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    paint(text_draw, fill_color)

    # 创建描边图层
    stroke_layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    stroke_draw = ImageDraw.Draw(stroke_layer)
    paint(stroke_draw, stroke_color)

    # 膨胀只影响文字包围盒外扩 stroke_width 像素的区域：
    # 裁出带余量的小块再逐轮 MaxFilter，与全幅膨胀逐像素等效，但快约 5 倍
    bbox = stroke_layer.getbbox()
    if bbox is None:
        return
    pad = stroke_width + 2
    bx0 = max(0, bbox[0] - pad)
    by0 = max(0, bbox[1] - pad)
    bx1 = min(width, bbox[2] + pad)
    by1 = min(height, bbox[3] + pad)
    stroke_tile = stroke_layer.crop((bx0, by0, bx1, by1))
    for _ in range(stroke_width):
        stroke_tile = stroke_tile.filter(ImageFilter.MaxFilter(3))

    # 将描边层和文字层合成到原画布
    text_tile = text_layer.crop((bx0, by0, bx1, by1))
    img.paste(stroke_tile, (bx0, by0), stroke_tile)
    img.paste(text_tile, (bx0, by0), text_tile)


def generate_cover(video_path, start_sec, end_sec, title, cover_text_1, cover_text_2, output_path, style_config):
    """生成封面图片,从视频中随机截取一帧作为背景"""

    if not video_path:
        print(f"  [错误] video_path 为空,无法生成封面")
        return False

    # 0. 检测视频分辨率 + 实际时长
    vid_width, vid_height, actual_duration = detect_video_info(video_path)
    if not vid_width or not vid_height:
        vid_width, vid_height = 1920, 1080  # 兜底

    # 1. 确定抽帧时间戳：固定取切片内容正中间，保证重新生成时画面不变
    is_subbed_clip = '_subbed' in os.path.basename(video_path)
    clip_duration = end_sec - start_sec
    # 抽帧源是原始完整录播（时长远大于切片区间）时，按 Data_source 绝对时间取切片区间中点；
    # 抽帧源是切好的切片（时间轴从 0 开始，含 _subbed）时，取文件正中间帧
    from_original = bool(actual_duration) and clip_duration > 0 and actual_duration > clip_duration + 30
    if is_subbed_clip or not from_original:
        timestamp = actual_duration * 0.5 if actual_duration else 0.5
    else:
        timestamp = start_sec + clip_duration * 0.5

    # 创建临时文件保存帧
    temp_frame = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
    temp_frame.close()

    try:
        # 提取帧
        if not extract_frame(video_path, timestamp, temp_frame.name):
            # 第一次失败尝试 1 秒处重试
            if not extract_frame(video_path, 1.0, temp_frame.name):
                print(f"  [警告] 无法从视频提取帧，使用纯色背景 ({vid_width}×{vid_height})")
                img = Image.new('RGBA', (vid_width, vid_height), (30, 30, 30, 255))
        else:
            img = Image.open(temp_frame.name).convert('RGBA')
            # 不再强制 resize，保持视频原生分辨率

        # 裁剪为 16:9 比例（居中），适应B站封面展示
        old_size = img.size
        img = crop_to_16_9(img)
        if img.size != old_size:
            print(f"  [裁剪] {old_size[0]}×{old_size[1]} → {img.size[0]}×{img.size[1]} (16:9)")

        # 统一缩放到 1920×1080，确保封面文字渲染清晰一致
        if img.size != (1920, 1080):
            img = img.resize((1920, 1080), Image.LANCZOS)
            if img.size != old_size or old_size != (1920, 1080):
                print(f"  [缩放] → 1920×1080 (B站标准封面尺寸)")

        # 如果需要模糊背景
        if style_config.get('blur_background', False):
            img = img.filter(ImageFilter.GaussianBlur(style_config.get('blur_radius', 3)))

        width, height = img.size

        # B站封面展示有16:9和4:3两个比例，4:3是从16:9居中裁剪的
        # safe_zone_ratio: 1.0=全宽，0.75=4:3安全区（1920→1440有效宽度）
        safe_zone_ratio = style_config.get('bilibili_safe_zone', 0.75)
        safe_width = int(width * safe_zone_ratio)
        safe_margin = (width - safe_width) // 2

        # 根据视频宽度等比缩放字体大小（竖屏1080px宽用180px字体会溢出）
        font_scale = width / 1920.0
        scaled_title_size = max(24, int(style_config['title_size'] * font_scale))
        scaled_stroke_width = max(2, int(style_config.get('title_stroke_width', 8) * font_scale))

        # 创建渐变遮罩
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        gradient_start_y = int(height * style_config.get('gradient_start_y', 0.6))
        gradient_opacity = style_config.get('gradient_opacity', 200)

        if gradient_opacity > 0 and gradient_start_y < height:
            for y in range(gradient_start_y, height):
                progress = (y - gradient_start_y) / (height - gradient_start_y)
                alpha = int(progress * gradient_opacity)
                draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))

        # 加载字体
        try:
            font_path = CONFIG.get('font_path')
            if font_path and os.path.exists(font_path):
                title_font = load_font_cached(font_path, scaled_title_size)
            else:
                print(f"  [警告] 字体文件不存在: {font_path}")
                title_font = get_font(scaled_title_size)
        except Exception as e:
            print(f"  [警告] 字体加载失败: {e}")
            title_font = get_font(scaled_title_size)

        # 直接使用 cover_text_1 和 cover_text_2,不做智能分割
        text_1 = cover_text_1 if cover_text_1 else title
        text_2 = cover_text_2 if cover_text_2 else ""

        # 根据布局绘制文字
        layout = style_config.get('layout', 'bottom')

        if layout == "double" and style_config.get('title_position') == "split":
            # 上下分割布局(上白下黄等)
            y1 = int(height * style_config.get('title_top_y_ratio', 0.2))
            top_color = style_config.get('title_top_color', style_config.get('title_color', (255, 255, 255)))
            # 上下分割布局也需要检查文字是否溢出
            t1_font = title_font
            t1_width = measure_text(text_1, t1_font)
            if t1_width > safe_width - 40:
                t1_size = max(18, int(scaled_title_size * (safe_width - 40) / t1_width))
                try:
                    fp = CONFIG.get('font_path')
                    t1_font = load_font_cached(fp, t1_size) if fp and os.path.exists(fp) else get_font(t1_size)
                except:
                    t1_font = get_font(t1_size)
            draw_text_with_multilayer_stroke(
                draw, (width / 2, y1), text_1, t1_font,
                top_color, style_config['title_stroke_color'], scaled_stroke_width
            )
            if text_2:
                y2 = int(height * style_config.get('title_bottom_y_ratio', 0.75))
                bottom_color = style_config.get('title_bottom_color', style_config.get('title_color', (255, 255, 255)))
                t2_font = title_font
                t2_width = measure_text(text_2, t2_font)
                if t2_width > safe_width - 40:
                    t2_size = max(18, int(scaled_title_size * (safe_width - 40) / t2_width))
                    try:
                        fp = CONFIG.get('font_path')
                        t2_font = load_font_cached(fp, t2_size) if fp and os.path.exists(fp) else get_font(t2_size)
                    except:
                        t2_font = get_font(t2_size)
                draw_text_with_multilayer_stroke(
                    draw, (width / 2, y2), text_2, t2_font,
                    bottom_color, style_config['title_stroke_color'], scaled_stroke_width
                )
        elif layout == "multiline_left":
            # 多行靠左双色风格（Style 5 — 原 use_new_style 迁移）
            from core.generate_covers_new import split_text_to_lines
            cover_text = cover_text_1 if cover_text_1 else title
            # 如果有 cover_text_2 且与 cover_text_1 不同，拼接到一起
            if cover_text_2 and cover_text_2 != cover_text_1:
                cover_text = f"{cover_text_1}，{cover_text_2}" if cover_text_1 else cover_text_2
            text_lines = split_text_to_lines(
                cover_text,
                max_chars=style_config.get("max_chars_per_line", 8),
                target_lines=style_config.get("target_lines", 4),
            )
            print(f"  [拆分] {cover_text} → {text_lines}")
            # ── 安全区：4:3 居中裁剪，safe_margin = (width * 0.25) / 2 ──
            safe_left = safe_margin
            safe_right = width - safe_margin
            base_left_margin = max(int(width * style_config.get("left_margin_ratio", 0.17)), safe_left + 10)
            max_chars_per_line = style_config.get("max_chars_per_line", 8)

            # ── 动态字号：预先测最长行宽度，超出则等比缩小整组 ──
            max_line_chars = max(len(l) for l in text_lines) if text_lines else 0
            dynamic_size = scaled_title_size
            if max_line_chars > max_chars_per_line:
                # 超出参考字数 → 按比例缩小，留 5% 余量
                ratio = max_chars_per_line / max_line_chars
                dynamic_size = max(36, int(scaled_title_size * ratio * 1.05))
            dynamic_stroke = max(2, int(style_config.get('title_stroke_width', 12) * font_scale * (dynamic_size / scaled_title_size)))

            # 测量实际像素宽度
            try:
                fp = CONFIG.get('font_path')
                probe_font = load_font_cached(fp, dynamic_size) if fp and os.path.exists(fp) else get_font(dynamic_size)
            except:
                probe_font = get_font(dynamic_size)
            max_line_w = max(measure_text(l, probe_font) for l in text_lines) if text_lines else 0
            available_w = safe_right - base_left_margin - 40

            # 二次校正：像素宽度仍超出则继续缩小
            if max_line_w > available_w:
                scale = available_w / max_line_w
                dynamic_size = max(36, int(dynamic_size * scale * 0.97))
                dynamic_stroke = max(2, int(dynamic_stroke * scale * 0.97))
                try:
                    fp = CONFIG.get('font_path')
                    probe_font = load_font_cached(fp, dynamic_size) if fp and os.path.exists(fp) else get_font(dynamic_size)
                except:
                    probe_font = get_font(dynamic_size)
                max_line_w = max(measure_text(l, probe_font) for l in text_lines)

            # ── 动态左距：文字短时往右微调，保持视觉平衡 ──
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
            # 行数少于 target_lines 时整体下移，避免视觉偏上
            if len(text_lines) < style_config.get("target_lines", 4):
                vert_ratio += 0.04
            start_y = int(height * vert_ratio) - total_h // 2

            odd_color = style_config.get("odd_text_color", (255, 255, 255))
            even_color = style_config.get("even_text_color", (255, 225, 0))
            for idx, line in enumerate(text_lines):
                y = start_y + idx * line_h
                text_color = odd_color if (idx % 2 == 0) else even_color
                draw_text_with_multilayer_stroke(
                    draw, (left_margin, y), line, probe_font,
                    text_color, style_config['title_stroke_color'], dynamic_stroke,
                    anchor="lm",
                )
        else:
            # 居中布局
            has_subtitle = bool(text_2)

            # 单行文字：垂直居中 + 可适当放大
            if not has_subtitle:
                title_y = int(height * 0.5)
                solo_scale = min(1.3, width / max(1, measure_text(text_1, title_font)))
                solo_size = max(scaled_title_size, int(scaled_title_size * solo_scale))
                try:
                    fp = CONFIG.get('font_path')
                    t1_font = load_font_cached(fp, solo_size) if fp and os.path.exists(fp) else get_font(solo_size)
                except:
                    t1_font = get_font(solo_size)
            else:
                title_y = int(height * style_config.get('title_y_ratio', 0.7))
                t1_font = title_font

            # 如果文字太长,自动缩小字体
            text_width = measure_text(text_1, t1_font)
            if text_width > safe_width - 40:
                scaled_size = max(18, int(scaled_title_size * (safe_width - 40) / text_width))
                try:
                    fp = CONFIG.get('font_path')
                    t1_font = load_font_cached(fp, scaled_size) if fp and os.path.exists(fp) else get_font(scaled_size)
                except:
                    t1_font = get_font(scaled_size)

            title_color = style_config.get('title_color', (255, 255, 255))
            draw_text_with_multilayer_stroke(
                draw, (width / 2, title_y), text_1, t1_font,
                title_color, style_config['title_stroke_color'], scaled_stroke_width
            )

            # 如果有第二行文字,在下方绘制
            if has_subtitle:
                y2 = title_y + int(scaled_title_size * 1.2)
                if y2 < height - 50:
                    draw_text_with_multilayer_stroke(
                        draw, (width / 2, y2), text_2, t1_font,
                        title_color, style_config['title_stroke_color'], scaled_stroke_width
                    )

        # 合成图层
        img = Image.alpha_composite(img, overlay)

        # 转换为RGB并锐化
        final_img = img.convert('RGB')
        final_img = final_img.filter(ImageFilter.SHARPEN)
        final_img.save(output_path, quality=95)

        print(f"  [OK] 封面生成成功: {os.path.basename(output_path)}")
        return True

    except Exception as e:
        print(f"  [错误] 封面生成失败: {e}")
        return False
    finally:
        # 清理临时文件
        if os.path.exists(temp_frame.name):
            os.remove(temp_frame.name)


def parse_timestamp(time_str):
    """解析时间字符串为秒数，非数字输入返回 0"""
    time_str = time_str.strip()
    parts = time_str.split(':')
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except (ValueError, TypeError):
        print(f"  [警告] 无法解析时间戳: {time_str}")
        return 0
    return 0


def sanitize_filename(filename):
    """清理文件名中的非法字符"""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    return filename[:100]


# ==========================================
# 主程序
# ==========================================

def main():
    input_dir = CONFIG['input_dir']
    output_dir = CONFIG['output_dir']

    # ====== 新样式分流：启用时走新模块，旧代码完全不执行 ======
    if CONFIG.get('use_new_style', False):
        from core.generate_covers_new import generate_covers_new_style
        video_dir = CONFIG.get('video_dir') or input_dir
        generate_covers_new_style(
            input_dir=input_dir,
            output_dir=output_dir,
            video_dir=video_dir,
            match_subbed=CONFIG.get('match_subbed', False),
            font_path=CONFIG.get('font_path'),
        )
        return
    # ====== 旧样式：原有流程零改动 ======

    print("=" * 60)
    print("封面生成工具")
    print("=" * 60)
    print(f"输入目录: {input_dir}")
    print(f"输出目录: {output_dir}")

    print(f"当前使用字体: {os.path.basename(CONFIG.get('font_path', '默认'))}")
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
    print("[OK] 输出目录已创建")

    # 3. 查找视频文件
    print("\n[3/4] 查找视频文件...")
    video_dir = CONFIG.get('video_dir') or input_dir
    match_subbed = CONFIG.get('match_subbed', False)
    print(f"   视频目录: {video_dir}")
    if match_subbed:
        print(f"   匹配模式: 切片 _subbed.mp4（抽帧优先用同名原视频，避免封面带字幕）")

    video_files = []
    video_exts = ('.mp4', '.flv', '.mkv', '.mov', '.ts', '.avi', '.wmv')
    for f in os.listdir(video_dir):
        if not f.lower().endswith(video_exts):
            continue
        video_files.append(os.path.join(video_dir, f))

    # 弹幕版优先：同目录存在 xxx-弹幕版.mp4 时跳过 xxx.mp4
    from utils.video_filter import filter_danmaku_versions
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

    # 获取当前激活的样式
    active_style = CONFIG['cover']['active_style']
    random_mode = active_style == 'random_1_4'
    if not random_mode:
        style_config = CONFIG['cover'].get(active_style, CONFIG['cover']['style1'])
        print(f"使用封面样式: {style_config.get('name', active_style)}")
    else:
        print("使用封面样式: 随机 (style1 ~ style3)")

    for i, clip in enumerate(clips, 1):
        # 每视频独立随机样式
        if random_mode:
            r_style = random.choice(['style1', 'style2', 'style3'])
            style_config = CONFIG['cover'].get(r_style, CONFIG['cover']['style1'])
            print(f"  🎲 本段随机到: {style_config.get('name', r_style)}")
        segment = clip.get('segment', 'unknown').strip()
        title = clip.get('title', f'切片_{i}')
        cover_text_1 = clip.get('cover_text_1', '')
        cover_text_2 = clip.get('cover_text_2', '')

        # 解析时间戳
        time_range = clip.get('timestamp', '00:00:00-00:00:00')
        start_str, end_str = time_range.split('-', 1)  # 只拆第一个，防止多段分隔
        start_sec = parse_timestamp(start_str)
        end_sec = parse_timestamp(end_str)

        print(f"\n[{i}/{len(clips)}] {title}")
        print(f"   时间段: {start_str} - {end_str}")

        # 查找对应的视频文件（精确匹配文件名前缀）
        video_path = None
        # 去除空格用于模糊匹配
        segment_nospace = segment.replace(' ', '')
        # 标题 sanitize 后与文件名比对（切片文件命名: [segment] title.mp4 / title_subbed.mp4）
        safe_title = sanitize_filename(title)
        for vf in video_files:
            vf_name = os.path.basename(vf)
            # 0. 标题优先精确匹配：[segment] title.mp4 或 [segment] title_subbed.mp4
            if vf_name.startswith(f"[{segment}] {safe_title}") and (
                vf_name.endswith('.mp4')
            ):
                video_path = vf
                break
        if not video_path:
            for vf in video_files:
                vf_name = os.path.basename(vf)
                vf_name_nospace = vf_name.replace(' ', '')
                # 1. 带括号格式匹配：[segment] 标题.mp4
                if vf_name.startswith(f"[{segment}]"):
                    video_path = vf
                    break
                # 2. 去空格包含匹配：segment(去空格) in filename(去空格)
                if segment_nospace and segment_nospace in vf_name_nospace:
                    video_path = vf
                    break
                # 3. 原始包含匹配
                if segment in vf_name or segment.replace('-', '_') in vf_name:
                    video_path = vf
                    break
                # 4. 时间前缀匹配（segment 开头的时间码，如 "14-50-11"）
                time_prefix = segment.split(' ')[0] if ' ' in segment else segment
                if time_prefix and vf_name.startswith(time_prefix):
                    video_path = vf
                    break

        if not video_path:
            print(f"  [跳过] 未找到 segment={segment} 对应的视频文件")
            fail_count += 1
            continue

        print(f"   使用视频: {os.path.basename(video_path)}")

        # 生成文件名
        safe_title = sanitize_filename(title)
        cover_filename = f"[{segment}] {safe_title}.jpg"
        cover_output = os.path.join(output_dir, cover_filename)

        # 跳过已存在的封面
        if os.path.exists(cover_output) and os.path.getsize(cover_output) > 0:
            print(f"  [跳过] 封面已存在: {cover_filename}")
            success_count += 1
            continue

        # 生成封面
        try:
            if generate_cover(video_path, start_sec, end_sec, title, cover_text_1, cover_text_2, cover_output, style_config):
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            print(f"  [错误] 封面生成异常: {e}")
            fail_count += 1

    # 5. 完成
    print(f"\n{'='*60}")
    print("处理完成!")
    print(f"{'='*60}")
    print(f"成功: {success_count} 个")
    print(f"失败: {fail_count} 个")
    print(f"\n输出目录: {output_dir}")


if __name__ == "__main__":
    _init_config()
    main()
