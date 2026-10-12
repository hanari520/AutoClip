#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoClip 一键自动流转脚本
──────────────────────────────
用法:
  python auto_pipeline.py --input-dir "<录播根目录>\<主播名>\<日期 标题>"

功能:
  1. 从目录路径自动检测主播名
  2. 加载 streamer_configs/{主播}.json 默认配置
  3. 顺序执行步骤1~6（准备→字幕→高光→切片→烧录→封面）
  4. 步骤7 整理投稿清单，等待用户确认后再投稿
"""
import os
import sys
import json
import argparse
import subprocess
import re
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
STREAMER_CONFIG_DIR = os.path.join(PROJECT_ROOT, 'configs', 'streamers')
FFMPEG_BIN = os.environ.get('AUTOCLIP_FFMPEG_BIN', '')
if os.path.isdir(FFMPEG_BIN):
    os.environ['PATH'] = FFMPEG_BIN + os.pathsep + os.environ.get('PATH', '')
sys.stdout.reconfigure(encoding='utf-8')


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def detect_streamer(input_dir):
    """从目录路径检测主播名：.../AA录播/{主播名}/{日期 标题}/"""
    parts = Path(input_dir).parts
    # 倒着找：输入目录的父目录名就是主播名
    # 但如果输入的是主播目录本身，则直接用最后的目录名
    recording_dirs = ['AA录播', '录播', 'recordings']
    for i, part in enumerate(parts):
        if part in recording_dirs and i + 1 < len(parts):
            return parts[i + 1]
    # fallback: 倒数第二个目录
    if len(parts) >= 2:
        return parts[-2]
    return os.path.basename(input_dir)


def load_streamer_config(config_path, streamer_name):
    """加载主播默认配置 JSON"""
    if config_path:
        path = config_path
    else:
        path = os.path.join(STREAMER_CONFIG_DIR, f'{streamer_name}.json')

    if not os.path.exists(path):
        print(f"⚠️  未找到主播配置: {path}")
        print(f"   请先运行配置流程创建 {streamer_name}.json")
        return None

    with open(path, 'r', encoding='utf-8-sig') as f:
        return json.load(f)


def run_cmd(cmd, step_name, cwd=None):
    """执行子进程命令，实时输出，返回退出码"""
    print(f"\n{'═' * 50}", flush=True)
    print(f"▶ {step_name}", flush=True)
    print(f"{'═' * 50}", flush=True)
    print(f"$ {' '.join(cmd)}", flush=True)
    print(flush=True)

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd or PROJECT_ROOT,
            text=True,
            encoding='utf-8',
            errors='replace',
        )
        if result.returncode == 0:
            print(f"\n✅ {step_name} 完成")
        else:
            print(f"\n❌ {step_name} 失败 (exit code: {result.returncode})")
        return result.returncode
    except Exception as e:
        print(f"\n💥 {step_name} 异常: {e}")
        return 1


# ═══════════════════════════════════════════════════════════
# 步骤完成度验证
# ═══════════════════════════════════════════════════════════

MAX_RETRIES = 3


def _verify_step_completion(step_func, checker_func, input_dir, cfg, step_no, step_name):
    """执行步骤并验证所有输出文件是否齐全。不全则重试，最多 MAX_RETRIES 次。"""
    ret = step_func(input_dir, cfg)
    if ret != 0:
        return ret

    for attempt in range(MAX_RETRIES):
        missing = checker_func(input_dir, cfg)
        if not missing:
            return 0
        print(f"\n⚠️  仍缺 {len(missing)} 个输出文件，重新执行步骤{step_no} (第{attempt+2}次)...")
        ret = step_func(input_dir, cfg)
        if ret != 0:
            return ret

    # 重试耗尽
    missing = checker_func(input_dir, cfg)
    if missing:
        print(f"\n⚠️  步骤{step_no} 经过 {MAX_RETRIES+1} 次尝试，仍有 {len(missing)} 个文件未完成:")
        for m in missing[:8]:
            print(f"    - {m}")
        if len(missing) > 8:
            print(f"    ... 等 {len(missing)} 个")
    return 0


def _check_step4_clips(input_dir, cfg):
    """检查步骤4：Data_source.txt 中每个 segment 都有对应的 .mp4。返回缺失列表。"""
    ds_path = os.path.join(input_dir, 'Data_source.txt')
    if not os.path.exists(ds_path):
        return []  # 没有数据源，无法验证

    with open(ds_path, 'r', encoding='utf-8') as f:
        clips = json.load(f)

    s4 = cfg.get('step4_clip', {})
    output_dir = s4.get('output_dir', '{input_dir}/切片').replace('{input_dir}', input_dir)
    slices_dir = os.path.join(output_dir, '视频') if os.path.isdir(os.path.join(output_dir, '视频')) else output_dir

    missing = []
    if os.path.isdir(slices_dir):
        existing = set(os.listdir(slices_dir))
        for clip in clips:
            seg = clip.get('segment', '').strip()
            if seg and not any(seg in f and f.endswith('.mp4') for f in existing):
                missing.append(f'[{seg}] {clip.get("title", "?")}')
    return missing


def _check_step5_burn(input_dir, cfg):
    """检查步骤5：每个非subbed .mp4 都有对应的 _subbed.mp4。返回缺失列表。"""
    slices_video_dir = os.path.join(input_dir, '切片', '视频')
    if not os.path.isdir(slices_video_dir):
        slices_video_dir = os.path.join(input_dir, '切片')
    if not os.path.isdir(slices_video_dir):
        return []

    missing = []
    for f in os.listdir(slices_video_dir):
        if f.endswith('.mp4') and not f.endswith('_subbed.mp4'):
            subbed_name = f.replace('.mp4', '_subbed.mp4')
            subbed_path = os.path.join(slices_video_dir, subbed_name)
            if not os.path.exists(subbed_path):
                missing.append(f)
    return missing


def _check_step6_covers(input_dir, cfg):
    """检查步骤6：每个切片视频（优先 _subbed.mp4，无则原版）都有对应的封面。返回缺失列表。"""
    slices_video_dir = os.path.join(input_dir, '切片', '视频')
    if not os.path.isdir(slices_video_dir):
        slices_video_dir = os.path.join(input_dir, '切片')
    covers_dir = os.path.join(input_dir, '切片', '封面')
    if not os.path.isdir(slices_video_dir):
        return []

    from utils.video_filter import filter_danmaku_versions_in_dir
    all_videos = [f for f in os.listdir(slices_video_dir) if f.lower().endswith('.mp4')]
    subbed_files = [f for f in all_videos if f.endswith('_subbed.mp4')]
    # 有字幕版用字幕版，否则用原版
    if subbed_files:
        target = filter_danmaku_versions_in_dir(slices_video_dir, subbed_files)
    else:
        target = filter_danmaku_versions_in_dir(slices_video_dir, all_videos)
    if not target:
        return []

    if not os.path.isdir(covers_dir):
        return list(target)

    existing_covers = set(os.listdir(covers_dir))
    missing = []
    for f in target:
        seg_match = re.match(r'\[([^\]]+)\]', f)
        seg = seg_match.group(1) if seg_match else os.path.splitext(f)[0].replace('_subbed', '')
        if not any(seg in c and c.lower().endswith(('.jpg', '.png', '.jpeg')) for c in existing_covers):
            missing.append(f)
    return missing


# ═══════════════════════════════════════════════════════════
# 各步骤执行函数
# ═══════════════════════════════════════════════════════════

def run_step1_prepare(input_dir, cfg):
    """步骤1: 准备录播文件（FLV → MP4；可选竖屏转横屏；可选 MP4+XML 弹幕压制；弹幕 XML→ASS 前置转换）"""
    s1 = cfg.get('step1_prepare', {})
    python = sys.executable
    recursive = s1.get('recursive')

    if s1.get('convert_flv', True):
        cmd = [
            python, os.path.join('core', 'convert_flv_to_mp4.py'),
            '--input-dir', input_dir,
        ]
        if recursive:
            cmd.append('--recursive')
        if s1.get('overwrite'):
            cmd.append('--overwrite')
        if s1.get('keep_flv_source') or s1.get('keep_source'):
            cmd.append('--keep-source')
        rc = run_cmd(cmd, '步骤1-1: FLV 转 MP4')
        if rc != 0:
            return rc
    else:
        print("\n步骤1-1: FLV 转 MP4 已按配置跳过")

    if s1.get('portrait_to_landscape'):
        landscape_cmd = [
            python, os.path.join('core', 'convert_portrait_to_landscape.py'),
            '--input-dir', input_dir,
        ]
        if recursive:
            landscape_cmd.append('--recursive')
        if s1.get('portrait_overwrite') or s1.get('landscape_overwrite'):
            landscape_cmd.append('--overwrite')
        if s1.get('delete_portrait_source'):
            landscape_cmd.append('--delete-source')
        if s1.get('landscape_width'):
            landscape_cmd.extend(['--width', str(s1.get('landscape_width'))])
        if s1.get('landscape_height'):
            landscape_cmd.extend(['--height', str(s1.get('landscape_height'))])
        if s1.get('landscape_blur') is not None:
            landscape_cmd.extend(['--blur', str(s1.get('landscape_blur'))])
        rc = run_cmd(landscape_cmd, '步骤1-2: 竖屏转横屏')
        if rc != 0:
            return rc
    else:
        print("\n步骤1-2: 竖屏转横屏 已按配置跳过")

    if s1.get('burn_danmu'):
        danmu_cmd = [
            python, os.path.join('core', 'burn_danmu_video.py'),
            '--input-dir', input_dir,
        ]
        if recursive:
            danmu_cmd.append('--recursive')
        if s1.get('burn_danmu_overwrite') or s1.get('overwrite_danmu'):
            danmu_cmd.append('--overwrite')
        if s1.get('keep_danmu_ass'):
            danmu_cmd.append('--keep-ass')
        # 并行路数：缺省用脚本默认（2 路）；配置里显式写了才覆盖
        if s1.get('burn_danmu_jobs'):
            danmu_cmd.extend(['--jobs', str(s1.get('burn_danmu_jobs'))])
        rc = run_cmd(danmu_cmd, '步骤1-3: 压制弹幕')
        if rc != 0:
            return rc
    else:
        print("\n步骤1-3: 压制弹幕 已按配置跳过")

    # 弹幕 XML→ASS 前置转换（默认开启）：为步骤3弹幕分析准备 ASS 源。
    # 属可选增强，失败只提示不阻断 —— 分析仍可按无弹幕源运行。
    if s1.get('convert_danmaku_xml', True):
        danmu_xml_cmd = [
            python, os.path.join('core', 'convert_danmaku_xml.py'),
            '--input-dir', input_dir,
        ]
        if recursive:
            danmu_xml_cmd.append('--recursive')
        rc = run_cmd(danmu_xml_cmd, '步骤1-4: 弹幕 XML→ASS 转换')
        if rc != 0:
            print("\n⚠️  步骤1-4 失败，继续后续步骤（弹幕源可能缺失）")
    else:
        print("\n步骤1-4: 弹幕 XML→ASS 转换 已按配置跳过")

    return 0


def run_step2_subtitles(input_dir, cfg):
    """步骤2: 字幕识别（原始录制视频 → SRT）"""
    s2 = cfg.get('step2_subtitle', {})
    python = sys.executable

    engine = s2.get('engine', 'funasr')
    model = s2.get('model', 'SenseVoiceSmall')
    language = s2.get('language', 'zh')

    cmd = [
        python, os.path.join('core', 'generate_subtitles.py'),
        '--input-dir', input_dir,
        '--engine', engine,
        '--language', language,
    ]

    if engine in ('funasr', 'portable'):
        cmd.extend(['--funasr-model', model])
        if s2.get('speaker_diarization'):
            cmd.append('--speaker')
        if not s2.get('punc', True):
            cmd.append('--no-punc')
    elif engine == 'qwen3':
        # Qwen3-ASR: models/ 下本地模型，主播配置 model 字段可指定 1.7B/0.6B
        if model in ('Qwen3-ASR-1.7B', 'Qwen3-ASR-0.6B'):
            cmd.extend(['--qwen3-model', model])
        if not s2.get('punc', True):
            cmd.append('--no-punc')
    elif engine == 'belle':
        # Belle 模型：使用默认 CTranslate2 路径，无需额外参数
        belle_model = s2.get('belle_model', '')
        if belle_model:
            cmd.extend(['--model', belle_model])
    else:
        cmd.extend(['--model', model])
        if s2.get('auto_lang'):
            cmd.append('--auto-lang')

    return run_cmd(cmd, '步骤2: 字幕识别')


def run_step3_highlight(input_dir, cfg):
    """步骤3: 高光分析（字幕 → LLM 分析 → Data_source_llm.json）"""
    s3 = cfg.get('step3_highlight', {})
    streamer = cfg.get('streamer_name', '')
    python = sys.executable

    method = s3.get('mode', 'llm')
    prompts_dir = s3.get('prompt_dir', '')

    # 从 gui_config.json 读取 API Key 与 provider（与 gui_app 的 _load_gui_config_for_pipeline 一致）
    api_key = ''
    model = ''
    gui_config_path = os.path.join(PROJECT_ROOT, 'configs', 'gui_config.json')
    gui = {}
    try:
        if os.path.exists(gui_config_path):
            with open(gui_config_path, 'r', encoding='utf-8') as f:
                gui = json.load(f)
            model = gui.get('global_model', '')
    except Exception:
        pass

    cmd = [
        python, os.path.join('core', 'generate_clips_combined.py'),
        '--method', method,
        '--input-dir', input_dir,
        '--streamer', streamer,
    ]

    if model:
        cmd.extend(['--model', model])
    # LLM provider 切换：按 provider 取 key + 透传 base_url，让 CLI 与 GUI 用同一份配置
    try:
        from utils.llm_config import get_api_key, resolve_llm_base_url
        api_key = get_api_key(gui_config=gui)
        cmd.extend(['--base-url', resolve_llm_base_url(gui_config=gui)])
    except Exception:
        pass
    if api_key:
        cmd.extend(['--api-key', api_key])
    if method == 'multi' and s3.get('multi_sources'):
        cmd.extend(['--multi-sources', s3['multi_sources']])
    if prompts_dir:
        cmd.extend(['--prompts-dir', prompts_dir])

    return run_cmd(cmd, '步骤3: 高光分析')


def run_step3_buffer(input_dir, cfg):
    """步骤3.5: 气口缓冲（Data_source.txt 时间戳前后各加 1 秒）"""
    ds_path = os.path.join(input_dir, 'Data_source.txt')
    if not os.path.exists(ds_path):
        print("[buffer] Data_source.txt 不存在，跳过缓冲处理")
        return 0
    python = sys.executable
    cmd = [
        python, os.path.join('utils', 'edit_data_source_timestep.py'),
        '--buffer', ds_path,
    ]
    return run_cmd(cmd, '步骤3.5: 气口缓冲')


def run_step4_clips(input_dir, cfg):
    """步骤4: 视频切片（Data_source.txt → 切片视频）"""
    s4 = cfg.get('step4_clip', {})
    output_dir = s4.get('output_dir', '{input_dir}/切片')
    output_dir = output_dir.replace('{input_dir}', input_dir)
    python = sys.executable

    cmd = [
        python, os.path.join('core', 'generate_clips.py'),
        '--input-dir', input_dir,
        '--output-dir', output_dir,
    ]

    return run_cmd(cmd, '步骤4: 视频切片')


def run_step5_burn(input_dir, cfg):
    """步骤5: 字幕烧录（切片视频 → _subbed.mp4）"""
    s5 = cfg.get('step5_burn', {})
    python = sys.executable

    # 切片视频在 切片/视频 子目录
    slices_video_dir = os.path.join(input_dir, '切片', '视频')
    if not os.path.exists(slices_video_dir):
        # fallback: 可能在 切片 目录本身
        slices_video_dir = os.path.join(input_dir, '切片')

    skip_asr = s5.get('skip_asr', False)
    engine = s5.get('engine', 'whisper')
    model = s5.get('model', 'large-v3-turbo')

    # 字体: 支持文件路径（自动解析家族名）或家族名
    font_config = s5.get('font', 'MaokenAssortedSans')

    cmd = [
        python, os.path.join('core', 'burn_subtitles.py'),
        '--input-dir', slices_video_dir,
        '--srt-dir', slices_video_dir,
        '--asr-engine', engine,
        '--asr-model', model,
        '--asr-language', s5.get('language', 'zh'),
    ]

    if font_config.lower().endswith(('.ttf', '.otf')):
        cmd.extend(['--font-file', font_config])
    else:
        cmd.extend(['--font-family', font_config])

    cmd.extend([
        '--font-size', str(s5.get('font_size', 80)),
        '--primary-color', s5.get('color', '&H00FFFFFF'),
        '--outline-color', s5.get('outline_color', '&H00000000'),
        '--outline-width', str(s5.get('outline', 8)),
        '--margin-v', str(s5.get('margin_v', 80)),
        '--margin-l', str(s5.get('margin_l', 80)),
        '--margin-r', str(s5.get('margin_r', 80)),
        '--max-line-chars', str(s5.get('max_line_chars', 0)),
        '--orientation', 'auto',
    ])

    if s5.get('shadow', False):
        cmd.append('--shadow')
    shadow_color = s5.get('shadow_color', 'black')
    if s5.get('shadow', False) and shadow_color != 'black':
        cmd.extend(['--shadow-color', shadow_color])
    if not s5.get('punc', True):
        cmd.append('--no-punc')
    if skip_asr:
        cmd.append('--skip-asr')
    speed_val = s5.get('speed', 'auto')
    if speed_val and speed_val != 'auto':
        cmd.extend(['--speed', speed_val])

    return run_cmd(cmd, '步骤5: 字幕烧录')


def run_step6_covers(input_dir, cfg):
    """
    步骤6: 封面生成（_subbed.mp4 → 封面 JPG）
    【与 gui_app.py _pipeline_step6 逻辑完全一致】
    """
    s6 = cfg.get('step6_cover', {})
    python = sys.executable

    slices_video_dir = os.path.join(input_dir, '切片', '视频')
    covers_output_dir = os.path.join(input_dir, '切片', '封面')

    # 直接用配置里的 style（如 random_1_4），由 generate_covers.py 内部处理
    style = s6.get('style', 'style1')
    font = s6.get('font', 'MaoKenShiJinHei-2.ttf')
    font_path = _resolve_font_path(font)
    use_new_style = s6.get('use_new_style', False)

    cmd = [
        python, os.path.join('core', 'generate_covers.py'),
        '--input-dir', input_dir,
        '--video-dir', slices_video_dir,
        '--match-subbed',
        '--output-dir', covers_output_dir,
        '--style', style,
    ]
    if use_new_style:
        cmd.append('--use-new-style')
    if font_path:
        cmd.extend(['--font', font_path])

    print(f"🎨 封面样式: {style}{' (新样式)' if use_new_style else ''}")
    print(f"📂 视频目录: {slices_video_dir}")
    print(f"📂 输出目录: {covers_output_dir}")

    return run_cmd(cmd, '步骤6: 封面生成')


def _resolve_font_path(font_name):
    """解析字体路径：优先从项目 assets/font 查找"""
    if not font_name:
        return None
    if os.path.isfile(font_name):
        return font_name
    # 在 assets/font 下查找
    font_dir = os.path.join(PROJECT_ROOT, 'assets', 'font')
    if os.path.isdir(font_dir):
        for f in os.listdir(font_dir):
            if font_name.lower() in f.lower():
                return os.path.join(font_dir, f)
    return font_name  # 返回原名，让 generate_covers.py 自己处理


def run_step7_prepare(input_dir, cfg):
    """
    步骤7: 生成投稿清单 upload_manifest.json
    【与 gui_app.py generate_manifest_from_files 逻辑完全一致】
    扫描 切片/视频/ 下的 _subbed.mp4 + 切片/封面/ 下的对应封面 → 生成清单
    """
    import json as _json
    
    s7 = cfg.get('step7_upload', {})
    streamer = cfg.get('streamer_name', '')
    title_suffix_name = str(s7.get('title_suffix_name') or streamer).strip() or streamer
    links = cfg.get('bilibili_links', {})

    videos_dir = os.path.join(input_dir, '切片', '视频')
    covers_dir = os.path.join(input_dir, '切片', '封面')
    if not os.path.isdir(videos_dir):
        videos_dir = os.path.join(input_dir, '切片')
        covers_dir = os.path.join(input_dir, '切片')

    # 扫描切片视频（弹幕版优先）：
    # 优先使用 _subbed.mp4（已烧录字幕版）；没有字幕版时回退使用原版切片 mp4
    subbed = []
    if os.path.isdir(videos_dir):
        raw_subbed = []
        raw_plain = []
        for f in sorted(os.listdir(videos_dir)):
            if f.endswith('_subbed.mp4'):
                raw_subbed.append(f)
            elif f.lower().endswith('.mp4'):
                raw_plain.append(f)
        from utils.video_filter import filter_danmaku_versions_in_dir
        if raw_subbed:
            subbed = filter_danmaku_versions_in_dir(videos_dir, raw_subbed)
            used_fallback = False
        elif raw_plain:
            subbed = filter_danmaku_versions_in_dir(videos_dir, raw_plain)
            used_fallback = True
        else:
            subbed = []
            used_fallback = False
    else:
        used_fallback = False

    print(f"\n{'═' * 50}")
    print(f"▶ 步骤7: 投稿清单")
    print(f"{'═' * 50}")

    if not subbed:
        print(f'❌ 未在 {videos_dir} 找到可投稿的视频（无 _subbed.mp4 或原版切片）')
        return 1
    if used_fallback:
        print(f'⚠️ 未找到 _subbed.mp4，使用原版切片视频生成投稿清单')

    # 加载 Data_source.txt 的 summary 映射 (sanitized_title → summary)
    # ── 关键：文件名经过 sanitize_filename 处理（替换 <>:"/\|?* 为 _），
    # 所以匹配键也必须做同样的 sanitize，否则含特殊字符的标题永远匹配不上。
    _invalid_chars = '<>:"/\\|?*'
    title_meta = {}
    ds_path = os.path.join(input_dir, 'Data_source.txt')
    if os.path.exists(ds_path):
        with open(ds_path, 'r', encoding='utf-8') as _fds:
            for clip in _json.load(_fds):
                t = clip.get('title', '').strip()
                if t:
                    key = t
                    for ch in _invalid_chars:
                        key = key.replace(ch, '_')
                    key = key[:100]
                    title_meta[key] = {
                        'title': t,
                        'summary': clip.get('summary', '').strip(),
                        'real_timestamp': clip.get('real_timestamp', '').strip(),
                    }

    space = links.get('space', '')
    live = links.get('live', '')
    tags = s7.get('tags', [])
    tid = s7.get('tid', 217)
    copyright_original = s7.get('copyright') == 'original'

    upload_list = []
    for video_file in subbed:
        # 兼容两种命名：[seg] title.mp4 或 [seg] title_subbed.mp4
        m = re.match(r'^\[([^\]]+)\]\s*(.+?)(?:_subbed)?\.mp4$', video_file)
        if not m:
            print(f'⚠️ 跳过无法解析的文件名: {video_file}')
            continue
        seg = m.group(1)
        title_raw = m.group(2)
        # 投稿标题正文优先用 Data_source.txt 原始标题（文件名里的 ? : 等已被清洗成 _）
        original_title = title_meta.get(title_raw, {}).get('title', '')
        title = f'【{title_suffix_name}】{original_title or title_raw}'

        # 匹配封面：按完整文件名前缀精确匹配（同 segment 下可能有多个切片）
        # 封面文件名不带 _subbed 后缀，原版与字幕版指向同一封面
        cover = None
        if os.path.isdir(covers_dir):
            cover_base = video_file[:-len('_subbed.mp4')] if video_file.endswith('_subbed.mp4') else video_file[:-len('.mp4')]
            for ext in ('.jpg', '.png', '.jpeg'):
                candidate = os.path.join(covers_dir, cover_base + ext)
                if os.path.exists(candidate):
                    cover = candidate
                    break
            # 回退：按 segment ID 匹配
            if not cover:
                for cf in sorted(os.listdir(covers_dir)):
                    if cf.startswith(f'[{seg}]') and cf.lower().endswith(('.jpg', '.png', '.jpeg')):
                        cover = os.path.join(covers_dir, cf)
                        break

        desc_parts = []
        if space:
            desc_parts.append(f'{streamer}的个人主页：{space}')
        if live:
            desc_parts.append(f'{streamer}的直播间：{live}')
        meta = title_meta.get(title_raw, {})
        real_timestamp = meta.get('real_timestamp', '')
        if real_timestamp:
            desc_parts.append('')
            desc_parts.append(f'直播时间：{real_timestamp}')
        summary = meta.get('summary', '')
        if summary:
            desc_parts.append('')
            desc_parts.append(summary)

        video_path = os.path.join(videos_dir, video_file)
        upload_list.append({
            'video_path': video_path,
            'title': title,
            'desc': '\n'.join(desc_parts),
            'tags': tags,
            'tid': tid,
            'copyright': 1 if copyright_original else 2,
            'cover_path': cover,
        })
        print(f'  📎 [{seg}] {title}')

    manifest_path = os.path.join(input_dir, 'upload_manifest.json')
    with open(manifest_path, 'w', encoding='utf-8') as f:
        _json.dump(upload_list, f, ensure_ascii=False, indent=2)
    covered = sum(1 for u in upload_list if u['cover_path'])
    print(f'📄 投稿清单: {manifest_path} ({len(upload_list)} 个待投稿 / {covered} 有封面)')

    return 0


def run_upload(input_dir, cfg):
    """执行投稿（用户确认后调用）"""
    manifest_path = os.path.join(input_dir, 'upload_manifest.json')
    if not os.path.exists(manifest_path):
        print(f"❌ 未找到投稿清单: {manifest_path}")
        print("   请先运行不带 --upload 的流程生成清单")
        return 1

    with open(manifest_path, 'r', encoding='utf-8') as f:
        upload_list = json.load(f)

    python = sys.executable

    print(f"\n🚀 开始投稿 {len(upload_list)} 个视频...\n")

    success = 0
    fail = 0

    for i, item in enumerate(upload_list, 1):
        print(f"{'─' * 50}")
        print(f"[{i}/{len(upload_list)}] {item['title']}")

        cmd = [
            python, os.path.join('utils', 'bilibili_uploader.py'),
            item['video_path'],
            '--title', item['title'],
            '--desc', item['desc'],
            '--tags', ','.join(item['tags']),
            '--tid', str(item['tid']),
            '--copyright', str(item['copyright']),
        ]
        if item['cover_path']:
            cmd.extend(['--cover', item['cover_path']])

        print(f"  $ {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            text=True,
            encoding='utf-8',
            errors='replace',
        )

        if result.returncode == 0:
            success += 1
        else:
            fail += 1

    print(f"\n{'═' * 50}")
    print(f"投稿完成: ✅ {success} 个成功 / ❌ {fail} 个失败")
    print(f"{'═' * 50}")

    return 0 if fail == 0 else 1


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='AutoClip 一键自动流转 — 从录制目录到B站投稿',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 完整流程（步骤1-6自动，步骤7准备清单）
  python auto_pipeline.py -i "<录播根目录>\<主播名>\<日期 标题>"

  # 只执行特定步骤
  python auto_pipeline.py -i "..." --start 5 --stop 6

  # 确认清单后执行投稿
  python auto_pipeline.py -i "..." --upload
        """
    )
    parser.add_argument('--input-dir', '-i', required=True,
                        help='录制目录路径')
    parser.add_argument('--config', '-c', default=None,
                        help='主播配置 JSON（可选，默认自动检测）')
    parser.add_argument('--start', type=int, default=1,
                        help='起始步骤编号 (默认: 1)')
    parser.add_argument('--stop', type=int, default=6,
                        help='结束步骤编号 (默认: 6)')
    parser.add_argument('--upload', action='store_true',
                        help='执行投稿（需先运行过流程生成清单）')
    parser.add_argument('--dry-run', action='store_true',
                        help='仅打印命令，不实际执行')
    args = parser.parse_args()

    input_dir = os.path.abspath(args.input_dir)
    if not os.path.isdir(input_dir):
        print(f"❌ 目录不存在: {input_dir}")
        return 1

    # 检测主播
    streamer = detect_streamer(input_dir)
    print(f"🎬 AutoClip 自动流转")
    print(f"   输入目录: {input_dir}")
    print(f"   检测主播: {streamer}")

    # 加载配置
    cfg = load_streamer_config(args.config, streamer)
    if cfg is None:
        return 1

    print(f"   配置文件: {args.config or os.path.join(STREAMER_CONFIG_DIR, f'{streamer}.json')}")
    if cfg.get('description'):
        print(f"   描述: {cfg['description']}")

    # 投稿模式
    if args.upload:
        return run_upload(input_dir, cfg)

    # 顺序执行步骤
    steps = [
        (1, '准备录播文件', run_step1_prepare, None),
        (2, '字幕识别', run_step2_subtitles, None),
        (3, '高光分析', run_step3_highlight, None),
        (4, '视频切片', run_step4_clips, _check_step4_clips),
        (5, '字幕烧录', run_step5_burn, _check_step5_burn),
        (6, '封面生成', run_step6_covers, _check_step6_covers),
    ]

    failed = False
    for step_no, step_name, step_func, checker_func in steps:
        if step_no < args.start or step_no > args.stop:
            print(f"\n⏭  跳过步骤{step_no}: {step_name} (不在范围内)")
            continue

        # 步骤5（字幕烧录）为可选项：主播配置 step5_burn.enabled=false 时跳过
        if step_no == 5 and not cfg.get('step5_burn', {}).get('enabled', True):
            print(f"\n⏭  跳过步骤5: 字幕烧录 (主播配置 step5_burn.enabled=false)")
            continue

        if args.dry_run:
            print(f"\n🔍 [DRY RUN] 步骤{step_no}: {step_name}")
            continue

        if checker_func:
            ret = _verify_step_completion(step_func, checker_func, input_dir, cfg, step_no, step_name)
        else:
            ret = step_func(input_dir, cfg)
        if ret != 0:
            print(f"\n⛔ 步骤{step_no}({step_name}) 失败，流水线暂停")
            failed = True
            break

    if args.dry_run:
        print(f"\n🔍 干跑模式完成（未实际执行任何操作）")
        return 0

    if failed:
        return 1

    # 步骤7: 准备投稿清单
    if args.stop >= 7:
        run_step7_prepare(input_dir, cfg)

    return 0


if __name__ == '__main__':
    sys.exit(main())
