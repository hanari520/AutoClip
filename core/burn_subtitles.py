# encoding: utf-8
"""
步骤5: 独立字幕烧录脚本（带 ASR 生成）
流程:
  1. 扫描切片目录所有 MP4 文件
  2. 对每个 MP4，用 FunASR (SenseVoiceSmall) 生成 SRT 字幕
  3. 对每个 SRT 运行 ASRCorrector 文本纠错
  4. 过滤情感标签
  5. 自动检测视频分辨率（横屏/竖屏），生成 ASS 并 FFmpeg 烧录
  6. 长字幕自动换行，不超过视频横向分辨率，居中不靠边
"""
import os, sys, re, json, subprocess, argparse, time
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 添加项目根目录到路径，支持 from utils.xxx import xxx
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'utils'))
from ASRCorrector import FileBasedCorrector

from funasr_transcriber import FunASRTranscriber
from belle_transcriber import BelleTranscriber


def detect_video_resolution(video_path):
    """用 ffprobe 检测视频实际分辨率，返回 (width, height) 或 (None, None)"""
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_streams', video_path
        ], capture_output=True, text=True, encoding='utf-8')
        info = json.loads(result.stdout)
        for stream in info.get('streams', []):
            if stream.get('codec_type') == 'video':
                w = stream.get('width', 0)
                h = stream.get('height', 0)
                if w > 0 and h > 0:
                    return w, h
    except Exception:
        pass
    return None, None


def detect_hardware_encoder(speed="auto"):
    """Detect available hardware H.264 encoder, return params dict or None"""
    ENCODER_PROFILES = {
        "h264_nvenc": {
            "label": "NVIDIA NVENC",
            "presets": {"auto": "p1", "fast": "p1", "balanced": "p4", "quality": "p7"},
            "quality": ["-cq", "23"],
        },
        "h264_amf": {
            "label": "AMD AMF",
            "presets": {"auto": "speed", "fast": "speed", "balanced": "balanced", "quality": "quality"},
            "quality": ["-qp_i", "23", "-qp_p", "23"],
        },
        "h264_qsv": {
            "label": "Intel QSV",
            "presets": {"auto": "veryfast", "fast": "veryfast", "balanced": "medium", "quality": "veryslow"},
            "quality": ["-global_quality", "23"],
        },
    }
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, encoding="utf-8", timeout=10
        )
        available = result.stdout + result.stderr
        for codec, profile in ENCODER_PROFILES.items():
            if codec in available:
                preset = profile["presets"].get(speed, profile["presets"]["auto"])
                return {
                    "codec": codec,
                    "label": profile["label"],
                    "preset": preset,
                    "quality": profile["quality"],
                }
    except Exception:
        pass
    return None


def build_burn_cmd(mp4_path, ass_path, font_dir, subbed_path, speed="auto"):
    """Build FFmpeg burn command, preferring hardware encoding"""
    ass_escaped = ass_path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")

    hw = detect_hardware_encoder(speed)
    if hw:
        print(f"   GPU: {hw['label']} (preset={hw['preset']})")
        return [
            "ffmpeg", "-i", mp4_path,
            "-vf", f"ass='{ass_escaped}':fontsdir='{font_dir}'",
            "-c:v", hw["codec"],
            "-preset", hw["preset"],
            *hw["quality"],
            "-c:a", "copy",
            "-y", subbed_path,
        ]
    else:
        cpu_presets = {"auto": "veryfast", "fast": "veryfast", "balanced": "medium", "quality": "slow"}
        cpu_preset = cpu_presets.get(speed, "veryfast")
        print(f"   CPU: libx264 (preset={cpu_preset})")
        return [
            "ffmpeg", "-i", mp4_path,
            "-vf", f"ass='{ass_escaped}':fontsdir='{font_dir}'",
            "-c:v", "libx264", "-preset", cpu_preset, "-crf", "23",
            "-c:a", "copy",
            "-y", subbed_path,
        ]


def parse_ffmpeg_time_seconds(value):
    match = re.match(r"(\d+):(\d+):(\d+(?:\.\d+)?)", value or "")
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def run_ffmpeg_with_progress(cmd, duration, label="FFmpeg"):
    progress_cmd = [cmd[0], "-hide_banner", "-nostats", "-progress", "pipe:1", *cmd[1:]]
    print(f"   {label}: started")
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
                print(f"   {label}: {percent}% ({current:.1f}s / {duration:.1f}s)")
                last_percent = percent
                last_log_at = now
        elif line == "progress=end":
            print(f"   {label}: 100%")

    returncode = process.wait()
    if returncode != 0:
        tail = "\n".join(last_lines[-12:])
        raise subprocess.CalledProcessError(returncode, progress_cmd, output=tail, stderr=tail)


def calc_max_chars(play_res_x, font_size, margin_l=80, margin_r=80):
    """根据视频宽度、字号、左右边距计算每行最大字符数"""
    usable = play_res_x - margin_l - margin_r
    # CJK 字符宽约等于字号，0.88 安全系数防止极端字形溢出
    return max(1, int(usable / font_size * 0.88))


def strip_trailing_punct(text):
    """去掉行尾中文标点符号（，。！？?!;；：、）"""
    return re.sub(r'[，。！？?!;；：、]+$', '', text.strip())


def split_to_fit_lines(text, max_chars_per_line):
    """
    将超长文本拆分为多个单行（不换行），每行去掉末尾标点。
    优先在句号/问号/感叹号处断句，其次在逗号/分号，最后硬断。

    Returns:
        list[str]: 拆分后的行列表，每行不超过 max_chars_per_line 且无末尾标点
    """
    if not text or max_chars_per_line <= 0:
        return [strip_trailing_punct(text)] if text else []

    # 先把已有 \N 换行拆开
    raw_lines = text.split('\\N')
    result = []

    for line in raw_lines:
        stripped = strip_trailing_punct(line)
        if not stripped:
            continue

        if len(stripped) <= max_chars_per_line:
            result.append(stripped)
            continue

        # 超长了，拆成多段
        remaining = stripped
        while remaining:
            if len(remaining) <= max_chars_per_line:
                result.append(strip_trailing_punct(remaining))
                break

            cut_at = max_chars_per_line
            best = -1
            # 第一优先：句末标点
            for punct in ['。', '！', '？', '?', '!']:
                pos = remaining[:cut_at].rfind(punct)
                if pos > best:
                    best = pos
            # 第二优先：逗号/分号
            if best < max_chars_per_line // 3:
                for punct in ['，', '；', ';', ',', ':', '、']:
                    pos = remaining[:cut_at].rfind(punct)
                    if pos > best:
                        best = pos
            # 第三优先：空格
            if best < max_chars_per_line // 3:
                pos = remaining[:cut_at].rfind(' ')
                if pos > best:
                    best = pos
            # 硬断
            if best <= 0:
                best = cut_at
            else:
                best = best + 1  # 包含标点本身，让断句更自然

            part = remaining[:best]
            # 去掉断点处可能残留的标点
            part = strip_trailing_punct(part)
            if part:
                result.append(part)
            remaining = remaining[best:].lstrip('，。！？?!;；：、 ')

    return result


def wrap_subtitle_text(text, max_chars_per_line):
    """
    【已废弃的首选路径 — 改为使用 split_to_fit_lines 逐条拆分】
    长字幕自动换行，保证不超过视频横向分辨率。
    """
    lines = split_to_fit_lines(text, max_chars_per_line)
    return '\\N'.join(lines)


def sec_to_ass_time(seconds):
    """秒数 -> ASS 时间 h:mm:ss.cs"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def create_ass(subs, output_path, start_offset, end_offset, style_config):
    """创建 ASS 字幕文件（双层字幕实现阴影：底层黑色 \pos 偏移 + 顶层主文本描边）"""
    s = style_config

    # 分辨率必须由调用方传入（已在 main 中通过 ffprobe 检测）
    play_res_x = s.get('play_res_x', 1920)
    play_res_y = s.get('play_res_y', 1080)

    font_family = s.get('font_family', 'MaokenAssortedSans')
    font_size = s.get('scaled_font_size', s.get('font_size', 80))
    primary_color = s.get('primary_color', '&H00FFFFFF')
    outline_color = s.get('outline_color', '&H00000000')
    outline_w = s.get('outline_width', 8)
    shadow_d = s.get('shadow_depth', 0)
    margin_v = s.get('scaled_margin_v', s.get('margin_v', 100))
    margin_l = s.get('margin_l', 80)
    margin_r = s.get('margin_r', 80)

    # 计算每行最大字符数（自动根据实际分辨率+字号+边距）
    max_chars = s.get('max_line_chars', 0)
    if max_chars <= 0:
        max_chars = calc_max_chars(play_res_x, font_size, margin_l, margin_r)

    # 阴影色：ASS 原生 BorderStyle=1 投影（BackColour 控制阴影颜色）
    if shadow_d > 0:
        shadow_color = s.get('shadow_color', 'black')
        back_color = '&H80FFFFFF' if shadow_color == 'white' else '&H80000000'
    else:
        back_color = '-1'

    # 单 Style：BorderStyle=1 原生描边+投影，不需要双层事件
    styles = f"""Style: Main,{font_family},{font_size},{primary_color},{primary_color},{outline_color},{back_color},-1,0,0,0,100,100,0,0,1,{outline_w},{shadow_d},2,{margin_l},{margin_r},{margin_v},1"""

    header = f"""[Script Info]
Title: AutoClip Subtitles
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    clip_dur = end_offset - start_offset
    for sub in subs:
        if sub['end'] > start_offset and sub['start'] < end_offset:
            rel_start = max(0, sub['start'] - start_offset)
            rel_end = min(clip_dur, sub['end'] - start_offset)
            sub_dur = rel_end - rel_start
            if sub_dur <= 0:
                continue

            # 过滤情感标签
            clean = re.sub(
                r'^\[(?:HAPPY|SAD|ANGRY|NEUTRAL|SURPRISED|FEARFUL|DISGUSTED|EMOTIONAL)\]\s*',
                '', sub['text'])
            raw_text = clean.replace('\n', '\\N')
            # 拆成不换行的单条字幕（去掉行尾标点，超长则分成多条各有自己的时间窗）
            lines = split_to_fit_lines(raw_text, max_chars)
            if not lines:
                continue

            # 按字符数比例分配时间给每条子字幕
            total_chars = sum(len(ln) for ln in lines)
            cursor = rel_start
            for ln in lines:
                char_ratio = len(ln) / total_chars if total_chars > 0 else 1.0 / len(lines)
                seg_dur = sub_dur * char_ratio
                seg_start = cursor
                seg_end = min(cursor + seg_dur, rel_end)
                if seg_end - seg_start < 0.5:
                    seg_end = min(seg_start + 0.5, rel_end)
                cursor = seg_end

                start_str = sec_to_ass_time(seg_start)
                end_str = sec_to_ass_time(seg_end)

                # 原生 BorderStyle=1 投影，单条事件即可
                events.append(
                    f"Dialogue: 0,{start_str},{end_str},Main,,0,0,0,,{ln}"
                )

    with open(output_path, 'w', encoding='utf-8-sig') as f:
        f.write(header + '\n'.join(events))
    return len(events)


def parse_and_correct_srt(srt_path, corrector):
    """解析 SRT 并用 ASRCorrector 纠错，返回字幕列表"""
    if not os.path.exists(srt_path):
        return []

    with open(srt_path, 'r', encoding='utf-8') as f:
        srt_content = f.read()

    subs = []
    for block in re.split(r'\n\n+', srt_content.strip()):
        lines = block.strip().split('\n')
        if len(lines) >= 3:
            tm = re.search(
                r'(\d+:\d+:\d+[,\.]\d+)\s*-->\s*(\d+:\d+:\d+[,\.]\d+)',
                lines[1])
            if tm:
                # 解析时间戳
                t1 = tm.group(1).replace(',', '.').strip()
                t2 = tm.group(2).replace(',', '.').strip()
                p1 = t1.split(':')
                p2 = t2.split(':')
                start = int(p1[0]) * 3600 + int(p1[1]) * 60 + float(p1[2])
                end = int(p2[0]) * 3600 + int(p2[1]) * 60 + float(p2[2])

                text = '\n'.join(lines[2:])

                # ASRCorrector 纠错
                fixed = corrector.correct_text(text)
                if fixed != text:
                    # suppress
                    print(f"   🔧 [{lines[0]}] {text[:30]} → {fixed[:30]}")

                subs.append({
                    'start': start,
                    'end': end,
                    'text': fixed,
                })

    # 更新 SRT 文件（写入纠错后的内容）
    corrected_srt_lines = []
    for i, sub in enumerate(subs, 1):
        s_h = int(sub['start'] // 3600)
        s_m = int((sub['start'] % 3600) // 60)
        s_s = int(sub['start'] % 60)
        s_ms = int((sub['start'] % 1) * 1000)
        e_h = int(sub['end'] // 3600)
        e_m = int((sub['end'] % 3600) // 60)
        e_s = int(sub['end'] % 60)
        e_ms = int((sub['end'] % 1) * 1000)
        corrected_srt_lines.append(str(i))
        corrected_srt_lines.append(
            f"{s_h:02d}:{s_m:02d}:{s_s:02d},{s_ms:03d} --> "
            f"{e_h:02d}:{e_m:02d}:{e_s:02d},{e_ms:03d}"
        )
        corrected_srt_lines.append(sub['text'])
        corrected_srt_lines.append('')

    if corrected_srt_lines:
        with open(srt_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(corrected_srt_lines))

    return subs


def main():
    parser = argparse.ArgumentParser(description='字幕烧录 - 步骤5')
    parser.add_argument('--input-dir', required=True, help='切片视频目录')
    parser.add_argument('--srt-dir', help='SRT 输出目录（默认: 与切片同目录）')
    # ASR 参数
    parser.add_argument('--asr-engine', default='portable',
                        choices=['portable', 'direct', 'whisper', 'belle', 'qwen3', 'moss'],
                        help='ASR 引擎 (default: portable)')
    parser.add_argument('--asr-model', default='sensevoice',
                        help='ASR 模型 (default: sensevoice)')
    parser.add_argument('--asr-language', default='zh',
                        help='源语言 (default: zh)')
    parser.add_argument('--asr-device', default=None,
                        help='设备 cuda/cpu (default: 自动)')
    parser.add_argument('--skip-asr', action='store_true',
                        help='跳过 ASR，使用已有 SRT 文件')
    parser.add_argument('--no-punc', action='store_true',
                        help='禁用标点恢复（标点用空格代替）')
    # 字幕样式
    parser.add_argument('--font-family', default='MaokenAssortedSans', help='ASS 字体名')
    parser.add_argument('--font-file', default=None, help='字体文件路径（.ttf/.otf），自动解析家族名并加入搜索目录')
    parser.add_argument('--font-size', type=int, default=80, help='字号')
    parser.add_argument('--primary-color', default='&H00FFFFFF',
                        help='字体颜色 &HAABBGGRR')
    parser.add_argument('--outline-color', default='&H00000000', help='描边颜色')
    parser.add_argument('--outline-width', type=int, default=8, help='描边宽度')
    parser.add_argument('--shadow', action='store_true', help='启用字幕阴影')
    parser.add_argument('--shadow-color', choices=['black', 'white'], default='black',
                        help='阴影颜色 black/white (默认 black)')
    parser.add_argument('--margin-v', type=int, default=100, help='底部边距（1080p 基准，自动按分辨率缩放）')
    parser.add_argument('--margin-l', type=int, default=80, help='左边界边距（防止字幕贴左边）')
    parser.add_argument('--margin-r', type=int, default=80, help='右边界边距（防止字幕贴右边）')
    parser.add_argument('--max-line-chars', type=int, default=0,
                        help='每行最大字符数（0=自动根据分辨率和字号计算）')
    parser.add_argument('--orientation', default='auto',
                        help='字幕方向 auto/horizontal/vertical (default: auto=根据视频分辨率自动)')
    parser.add_argument('--speed', default='auto',
                        choices=['auto', 'fast', 'balanced', 'quality'],
                        help='编码速度 (auto=自动检测GPU加速, fast=最快, balanced=均衡, quality=画质优先)')
    parser.add_argument('--replace', action='store_true',
                        help='覆盖原视频（默认输出 _subbed.mp4 保留原文件）')

    args = parser.parse_args()
    input_dir = os.path.abspath(args.input_dir)
    srt_dir = os.path.abspath(args.srt_dir) if args.srt_dir else input_dir

    print("=" * 60)
    print("🔤 步骤5: 字幕烧录（集成 FunASR ASR）")
    print("=" * 60)

    # ── 1. 加载 ASRCorrector ──
    print("\n📚 加载 ASRCorrector...")
    corrector = FileBasedCorrector()
    print(f"   已加载 {len(corrector.sorted_keys)} 条纠错规则")

    # ── 2. 初始化 ASR 引擎 ──
    transcriber = None
    if not args.skip_asr:
        print(f"\n🎙 初始化 ASR 引擎: {args.asr_engine} / {args.asr_model}")
        if args.asr_engine == 'belle':
            transcriber = BelleTranscriber()
        elif args.asr_engine == 'qwen3':
            from qwen3_asr_transcriber import Qwen3ASRTranscriber
            transcriber = Qwen3ASRTranscriber(
                model_name=args.asr_model, device=args.asr_device)
        elif args.asr_engine == 'moss':
            from moss_transcriber import MossTranscriber
            transcriber = MossTranscriber(device=args.asr_device)
        else:
            transcriber = FunASRTranscriber(
                model_name=args.asr_model,
                backend=args.asr_engine,
                device=args.asr_device,
            )

    # ── 3. 扫描 MP4 切片（弹幕版优先）──
    # 弹幕版优先：同目录存在 xxx-弹幕版.mp4 时跳过 xxx.mp4
    from utils.video_filter import filter_danmaku_versions
    raw_mp4 = [
        os.path.join(input_dir, f) for f in os.listdir(input_dir)
        if f.lower().endswith('.mp4') and not f.endswith('_subbed.mp4')
    ]
    mp4_files = sorted(filter_danmaku_versions(raw_mp4))

    if not mp4_files:
        print("\n⚠️ 未找到 MP4 切片文件")
        return

    # ── 3.5 批量预转录（portable 后端）:全部待烧录切片共用一次模型冷启动 ──
    batch_asr_done = set()
    if (not args.skip_asr) and transcriber is not None \
            and getattr(transcriber, 'backend', '') == 'portable' \
            and hasattr(transcriber, 'transcribe_batch'):
        pending = []
        for mp4 in mp4_files:
            base = os.path.splitext(mp4)[0]
            subbed = base + "_subbed.mp4"
            if os.path.exists(subbed) and os.path.getsize(subbed) > 0:
                continue  # 已烧录，主循环会跳过
            video_name = os.path.splitext(os.path.basename(mp4))[0]
            pending.append((mp4, os.path.join(srt_dir, video_name + '.srt')))
        if pending:
            print(f"\n🎙 批量预转录: {len(pending)} 个切片共用一次模型加载")
            try:
                pre_results = transcriber.transcribe_batch(
                    pending,
                    language=args.asr_language,
                    punc_enabled=not args.no_punc,
                )
            except Exception as e:
                print(f"   ⚠️ 批量预转录整体失败: {e}，回退逐个转录")
                pre_results = [{'ok': False} for _ in pending]
            for (mp4, _srt), r in zip(pending, pre_results):
                if r.get('ok'):
                    batch_asr_done.add(mp4)
            print(f"🎙 批量预转录完成: {len(batch_asr_done)}/{len(pending)} 成功"
                  f"（失败条目将在下方逐个重试）")

    # 基准分辨率（字号与边距在此分辨率下为配置值）
    BASE_RES_Y = 1080
    base_font_size = args.font_size
    base_margin_v = args.margin_v

    def scale_font_to_res(font_size, res_y):
        """字号按分辨率等比缩放，保证不同分辨率字幕视觉大小一致"""
        return max(36, int(round(font_size * res_y / BASE_RES_Y)))

    def scale_margin_to_res(margin, res_y):
        """底部边距按分辨率等比缩放，保证不同分辨率字幕位置一致"""
        return max(20, int(round(margin * res_y / BASE_RES_Y)))

    base_style_config = {
        'font_family': args.font_family,
        'font_size': base_font_size,  # 保留原始值（用于 calc_max_chars）
        'scaled_font_size': base_font_size,  # 将写入 ASS 的实际字号
        'primary_color': args.primary_color,
        'outline_color': args.outline_color,
        'outline_width': args.outline_width,
        'shadow_depth': 7 if args.shadow else 0,
        'shadow_color': args.shadow_color if args.shadow else 'black',
        'margin_v': base_margin_v,
        'scaled_margin_v': base_margin_v,  # 将写入 ASS 的实际边距
        'margin_l': args.margin_l,
        'margin_r': args.margin_r,
        'max_line_chars': args.max_line_chars,
        'orientation': args.orientation,
    }

    # 字体目录
    extra_font_dirs = []
    font_file = args.font_file
    if font_file and not os.path.isfile(font_file):
        # 裸文件名：到项目 assets/font 下解析
        _proj_font = os.path.join(PROJECT_ROOT, 'assets', 'font', os.path.basename(font_file))
        if os.path.isfile(_proj_font):
            font_file = _proj_font
    if font_file and os.path.isfile(font_file):
        try:
            from fontTools.ttLib import TTFont
            ft = TTFont(font_file)
            for record in ft['name'].names:
                if record.nameID == 1:  # Family name
                    family = record.toUnicode()
                    if family and 'Mao' in family:
                        break  # prefer English family name
                    if family and not extra_font_dirs:  # save first found
                        base_style_config['font_family'] = family
            # Prefer English name from another pass
            for record in ft['name'].names:
                if record.nameID == 1:
                    family = record.toUnicode()
                    if family and all(ord(c) < 128 for c in family):
                        base_style_config['font_family'] = family
                        break
            font_file_dir = os.path.dirname(os.path.abspath(font_file))
            extra_font_dirs.append(font_file_dir)
            print(f"   🔤 字体文件: {font_file} → 家族名: {base_style_config['font_family']}")
        except Exception as e:
            print(f"   ⚠️ 字体文件解析失败: {e}，使用 --font-family 值")

    font_dir = os.path.join(PROJECT_ROOT, 'assets', 'font')
    if not os.path.exists(font_dir):
        font_dir = os.getcwd()
    all_font_dirs = [font_dir] + extra_font_dirs
    font_dir_escaped = ';'.join(
        d.replace('\\', '/').replace(':', r'\:') for d in all_font_dirs
    )

    print(f"\n📂 输入目录: {input_dir}")
    print(f"📂 SRT 目录: {srt_dir}")
    print(f"🎬 切片数量: {len(mp4_files)}")
    if not args.skip_asr:
        print(f"🎙 ASR 引擎: {args.asr_engine}/{args.asr_model}")
    print(f"🔤 字体: {base_style_config['font_family']} {base_style_config['font_size']}px")
    print(f"🖊 描边: {base_style_config['outline_width']}px | 阴影: {base_style_config['shadow_depth']}px ({base_style_config['shadow_color']})")
    print(f"📏 字幕换行: 自动根据每个视频分辨率计算")

    # ── 4. 逐文件处理 ──
    total = len(mp4_files)
    for idx, mp4_path in enumerate(mp4_files, 1):
        base = os.path.splitext(mp4_path)[0]
        video_name = os.path.splitext(os.path.basename(mp4_path))[0]
        srt_path = os.path.join(srt_dir, video_name + '.srt')
        temp_ass = base + "_temp.ass"
        subbed_path = base + "_subbed.mp4"

        print(f"\n{'=' * 50}")
        print(f"🎬 [{idx}/{total}] {os.path.basename(mp4_path)}")

        # 跳过已烧录的视频
        if os.path.exists(subbed_path) and os.path.getsize(subbed_path) > 0:
            print(f"   ⏭  已烧录，跳过")
            continue

        # ── 4a. 生成 SRT ──
        srt_exists = os.path.exists(srt_path)
        if args.skip_asr and srt_exists:
            print(f"   📄 SRT 已存在，跳过 ASR")
        elif args.skip_asr and not srt_exists:
            print(f"   ⚠️ SRT 未找到，跳过烧录 (--skip-asr 模式下必须已有 SRT)")
            continue
        else:
            if mp4_path in batch_asr_done:
                print(f"   🎙 ASR 已由批量预转录完成")
            else:
                if srt_exists:
                    print(f"   🔄 已存在 SRT，重新生成...")
                print(f"   🎙 {args.asr_engine} {args.asr_model} 正在转录...")
            try:
                if args.asr_engine == 'belle':
                    # Belle 模型：中文专用，不需要 language/punc 参数
                    transcriber.transcribe_video(
                        mp4_path,
                        output_srt_path=srt_path,
                        verbose=False,
                    )
                    print(f"   ✅ Belle ASR 完成")
                elif args.asr_engine == 'qwen3':
                    result = transcriber.transcribe_video(
                        mp4_path, srt_path,
                        language=args.asr_language,
                        verbose=False,
                    )
                    if isinstance(result, str):
                        print(f"   ✅ Qwen3-ASR 完成")
                    else:
                        seg = result.get('segments', 0)
                        rt = result.get('transcribe_time', 0)
                        print(f"   ✅ Qwen3-ASR 完成: {seg} 条字幕 ({rt:.1f}s)")
                elif args.asr_engine == 'moss':
                    result = transcriber.transcribe_video(
                        mp4_path, srt_path,
                        language=args.asr_language,
                        verbose=False,
                    )
                    if isinstance(result, str):
                        print(f"   ✅ MOSS-Transcribe 完成")
                    else:
                        seg = result.get('segments', 0)
                        rt = result.get('transcribe_time', 0)
                        print(f"   ✅ MOSS-Transcribe 完成: {seg} 条字幕 ({rt:.1f}s)")
                else:
                    result = transcriber.transcribe_video(
                        mp4_path, srt_path,
                        language=args.asr_language,
                        verbose=False,
                        punc_enabled=not args.no_punc,
                    )
                    if isinstance(result, str):
                        print(f"   ✅ ASR 完成")
                    else:
                        seg = result.get('segments', 0)
                        rt = result.get('transcribe_time', 0)
                        print(f"   ✅ ASR 完成: {seg} 条字幕 ({rt:.1f}s)")
            except Exception as e:
                print(f"   ❌ ASR 失败: {e}")
                continue

        # ── 4b. 解析 SRT + ASRCorrector 纠错 ──
        subs = parse_and_correct_srt(srt_path, corrector)
        if not subs:
            print(f"   ⚠️ SRT 无有效字幕，跳过烧录")
            continue
        print(f"   📝 {len(subs)} 条字幕段落")

        # ── 4c. 检测视频分辨率 + 时长 ──
        probe = subprocess.run([
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', mp4_path
        ], capture_output=True, text=True, encoding='utf-8')
        duration = 60
        vid_width, vid_height = None, None
        try:
            info = json.loads(probe.stdout)
            duration = float(info.get('format', {}).get('duration', 60))
            for stream in info.get('streams', []):
                if stream.get('codec_type') == 'video':
                    vid_width = stream.get('width')
                    vid_height = stream.get('height')
                    break
        except Exception:
            pass

        # 为当个视频构建 style_config（含分辨率 + 分辨率自适应字号）
        file_style = dict(base_style_config)
        if vid_width and vid_height:
            file_style['play_res_x'] = vid_width
            file_style['play_res_y'] = vid_height
            # 分辨率自适应字号：保证不同分辨率字幕视觉大小一致
            file_style['scaled_font_size'] = scale_font_to_res(base_font_size, vid_height)
            file_style['scaled_margin_v'] = scale_margin_to_res(base_margin_v, vid_height)
            # 自动判断方向
            is_vertical = vid_width < vid_height
            direction = '竖屏' if is_vertical else '横屏'
            print(f"   📐 检测到 {direction} {vid_width}×{vid_height} → 字号 {file_style['scaled_font_size']}px")
        else:
            # 兜底
            if base_style_config.get('orientation') == 'vertical':
                file_style['play_res_x'], file_style['play_res_y'] = 1080, 1920
            else:
                file_style['play_res_x'], file_style['play_res_y'] = 1920, 1080
            file_style['scaled_font_size'] = base_font_size
            print(f"   ⚠️ 未能检测分辨率，使用兜底 {file_style['play_res_x']}×{file_style['play_res_y']} → 字号 {file_style['scaled_font_size']}px")

        # 打印当前文件的换行参数
        fc = file_style.get('max_line_chars', 0)
        if fc <= 0:
            fc = calc_max_chars(file_style['play_res_x'], file_style['font_size'],
                                file_style['margin_l'], file_style['margin_r'])
        print(f"   📏 每行最多 ~{fc} 字符 (宽度{file_style['play_res_x']}px, 边距L{file_style['margin_l']}/R{file_style['margin_r']}, 底部边距{file_style['scaled_margin_v']}px)")

        # ── 4d. 创建 ASS ──
        count = create_ass(subs, temp_ass, 0, duration, file_style)
        if count == 0:
            print("   ⚠️ 无匹配字幕段落，跳过")
            if os.path.exists(temp_ass):
                os.unlink(temp_ass)
            continue

        # 对 FFmpeg 滤镜参数字符串做单引号转义
        ass_path = temp_ass.replace('\\', '/').replace(':', r'\:').replace("'", r"\'")

        # ── 4e. FFmpeg 烧录（自动检测 GPU 加速）──
        burn_cmd = build_burn_cmd(
            mp4_path, temp_ass, font_dir_escaped, subbed_path,
            speed=args.speed
        )

        try:
            run_ffmpeg_with_progress(burn_cmd, duration, label="字幕烧录")
            print(f"   ✅ 字幕烧录完成")

            if os.path.exists(temp_ass):
                os.unlink(temp_ass)
            if os.path.exists(srt_path):
                os.unlink(srt_path)

            if args.replace:
                os.replace(subbed_path, mp4_path)
                print(f"   🔄 已覆盖原文件")

        except subprocess.CalledProcessError as e:
            stderr = str(e.stderr or e.output or '')[-500:]
            print(f"   ❌ FFmpeg 失败: {stderr}")
            if os.path.exists(temp_ass):
                os.unlink(temp_ass)

    print(f"\n{'=' * 60}")
    print(f"✅ 字幕烧录完成 ({total} 个文件)")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
