# -*- coding: utf-8 -*-
"""
Qwen3-ASR Worker
由项目内独立虚拟环境 qwen3_env 的 Python 运行,负责模型加载和转录
流程: FFmpeg 提取 16k 单声道 WAV → 低能量点切 ≤180s 块 →
      阶段A Qwen3-ASR 逐块转写 → 阶段B ForcedAligner 逐块对齐 → 句级 SRT

两阶段分别加载/卸载模型,峰值显存 = max(ASR 4.7G, 对齐器 1.3G),适配 8G 显卡。

用法(由 qwen3_asr_transcriber.py 通过 subprocess 调用):
    qwen3_env\\Scripts\\python.exe utils/_qwen3_asr_worker.py \
        --input video.mp4 --output output.srt --language zh
"""
import sys
import os
import json
import time
import pathlib
import tempfile
import subprocess

import re as _re
import unicodedata

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_ROOT = os.path.join(PROJECT_ROOT, 'models')
ALIGNER_MODEL_DIR = os.path.join(MODELS_ROOT, 'Qwen3-ForcedAligner-0.6B')

# 可选转写模型（models/ 下目录名，默认 1.7B）
SUPPORTED_ASR_MODELS = ('Qwen3-ASR-1.7B', 'Qwen3-ASR-0.6B')

# 与 qwen_asr 内部 MAX_FORCE_ALIGN_INPUT_SECONDS 一致(对齐器单次上限180s)
CHUNK_SECONDS = 180

# 输入/输出允许目录白名单: 项目根 + 环境变量 AUTOCLIP_ALLOWED_MEDIA_ROOTS 扩展（分号分隔）
ALLOWED_ROOTS = [PROJECT_ROOT]

# 语言代码 → Qwen3-ASR 规范名
LANGUAGE_MAP = {
    'zh': 'Chinese', 'cn': 'Chinese', 'chinese': 'Chinese',
    'en': 'English', 'english': 'English',
    'ja': 'Japanese', 'jp': 'Japanese', 'japanese': 'Japanese',
    'ko': 'Korean', 'korean': 'Korean',
    'yue': 'Cantonese', 'cantonese': 'Cantonese',
    'auto': None,
}

# 句末标点(切断字幕行) / 句中标点(行内暂停点)
SENTENCE_END = '。！？!?…；;'
CLAUSE_MARK = '，,、：:'
# 单条字幕字符数上限(硬断) / 遇句中标点且已超此长度时断
MAX_CUE_CHARS = 32
CLAUSE_MIN_CHARS = 12
# 相邻 token 间静默超过此值时断句
SILENCE_GAP_SEC = 1.2
MIN_CUE_SEC = 0.5


def log(msg):
    print(f"[Qwen3-Worker] {msg}", flush=True)


def format_srt_time(seconds):
    ms = int(round(max(seconds, 0) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def find_ffmpeg():
    """按 PATH → 本机 FFmpeg 构建 → 便携版运行时 顺序找 ffmpeg"""
    from shutil import which
    exe = which('ffmpeg')
    if exe:
        return exe
    candidates = [
        os.path.join(os.environ.get('AUTOCLIP_FFMPEG_BIN', ''), 'ffmpeg.exe'),
        os.path.join(PROJECT_ROOT, 'funasr', 'runtime', 'ffmpeg', 'ffmpeg.exe'),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def extract_wav_16k(input_path, wav_path):
    """FFmpeg 提取 16kHz 单声道 PCM WAV（参数列表 + shell=False，无命令拼接）"""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError('ffmpeg not found (PATH / AUTOCLIP_FFMPEG_BIN / portable runtime)')
    cmd = [
        ffmpeg, '-y', '-v', 'error', '-i', input_path,
        '-vn', '-ac', '1', '-ar', '16000', '-acodec', 'pcm_s16le', wav_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', shell=False)
    if proc.returncode != 0 or not os.path.isfile(wav_path) or os.path.getsize(wav_path) < 100:
        raise RuntimeError(f'ffmpeg audio extract failed: {(proc.stderr or "")[-300:]}')
    ffprobe = os.path.join(os.path.dirname(ffmpeg), 'ffprobe.exe')
    if not os.path.isfile(ffprobe):
        ffprobe = 'ffprobe'
    probe = subprocess.run(
        [ffprobe, '-v', 'quiet', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', wav_path],
        capture_output=True, text=True, encoding='utf-8', errors='replace', shell=False)
    try:
        duration = float(probe.stdout.strip())
    except Exception:
        duration = 0.0
    return duration


def is_cjk_char(ch):
    code = ord(ch)
    return (0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF
            or 0x20000 <= code <= 0x2A6DF or 0xF900 <= code <= 0xFAFF)


def is_kept_char(ch):
    """与 qwen_asr 对齐器 token 规则一致: 保留字母/数字/'（撇号）"""
    if ch == "'":
        return True
    cat = unicodedata.category(ch)
    return cat.startswith('L') or cat.startswith('N')


def tokenize_like_aligner(text):
    """
    按 Qwen3ForcedAligner 的 tokenize_chinese_mixed 规则切 token。
    返回 [(start_idx_in_text, token_str), ...]，标点/空白被丢弃。
    """
    tokens = []
    current_latin = []
    current_start = None

    def flush():
        nonlocal current_latin, current_start
        if current_latin:
            token = ''.join(current_latin)
            cleaned = ''.join(ch for ch in token if is_kept_char(ch))
            if cleaned:
                tokens.append((current_start, cleaned))
        current_latin = []
        current_start = None

    for i, ch in enumerate(text):
        if is_cjk_char(ch):
            flush()
            tokens.append((i, ch))
        elif is_kept_char(ch):
            if not current_latin:
                current_start = i
            current_latin.append(ch)
        else:
            flush()
    flush()
    return tokens


def _trailing_span(between):
    """句末标点后紧随的收尾标点/空白长度（一并纳入本行）"""
    n = 0
    for ch in between:
        if ch in SENTENCE_END or ch in CLAUSE_MARK or ch in '”’"）)':
            n += 1
        else:
            break
    return n


def group_items_to_cues(text, items, chunk_offset):
    """
    将对齐器输出的字级 token 时间戳按标点分组为句级字幕。

    Args:
        text: 该块 ASR 转写全文(含标点)
        items: ForcedAlignResult.items，与 tokenize_like_aligner(text) 一一对应
        chunk_offset: 该块在整段音频中的起始秒
    Returns:
        [(start_sec, end_sec, cue_text), ...]（已加 chunk_offset）
    """
    tokens = tokenize_like_aligner(text)
    cues = []

    if len(tokens) != len(items):
        # token 与对齐 item 数不匹配时退化为按标点切句 + 时长按字数比例分配
        log(f"token/item mismatch ({len(tokens)} vs {len(items)}), fallback to proportional timing")
        sentences = [s for s in _re.split(r'(?<=[。！？!?…；;])', text) if s.strip()]
        if not sentences:
            sentences = [text] if text.strip() else []
        total_chars = sum(len(s) for s in sentences) or 1
        t0 = items[0].start_time if items else 0.0
        t1 = items[-1].end_time if items else 0.0
        span = max(t1 - t0, 0.1)
        cursor = t0
        for s in sentences:
            dur = span * len(s) / total_chars
            cues.append((cursor, cursor + dur, s.strip()))
            cursor += dur
        return [(a + chunk_offset, b + chunk_offset, c) for a, b, c in cues if c]

    # 两指针: 按标点/长度/静默把 token 流切成字幕行
    def close_cue(start_token, end_token, end_text_idx, start_text_idx):
        if end_token < start_token:
            return
        cue_text = text[start_text_idx:end_text_idx].strip()
        if not cue_text:
            return
        start = items[start_token].start_time
        end = items[end_token].end_time
        cues.append((start, end, cue_text))

    cue_start_token = 0
    cue_start_text = 0
    last_token_idx = -1
    open_tokens = False

    for ti in range(len(tokens)):
        tok_start, tok_text = tokens[ti]
        item = items[ti]

        # 与上一个 token 之间的静默间隔 → 断句
        if last_token_idx >= 0 and open_tokens:
            gap = item.start_time - items[last_token_idx].end_time
            if gap >= SILENCE_GAP_SEC:
                lt = tokens[last_token_idx]
                close_cue(cue_start_token, last_token_idx,
                          lt[0] + len(lt[1]), cue_start_text)
                cue_start_token = ti
                cue_start_text = tok_start
                open_tokens = False

        if not open_tokens:
            cue_start_token = ti
            cue_start_text = tok_start
            open_tokens = True
        last_token_idx = ti

        # 当前 cue 在 text 中的跨度（含标点）
        span_start = tokens[cue_start_token][0]
        span_len = tok_start + len(tok_text) - span_start

        # 当前 token 之后、下一个 token 之前的标点
        next_start = tokens[ti + 1][0] if ti + 1 < len(tokens) else len(text)
        between = text[tok_start + len(tok_text):next_start]

        end_text = tok_start + len(tok_text)
        if any(p in SENTENCE_END for p in between):
            close_cue(cue_start_token, ti, end_text + _trailing_span(between), cue_start_text)
            cue_start_token = ti + 1
            cue_start_text = next_start
            open_tokens = False
        elif span_len >= MAX_CUE_CHARS:
            close_cue(cue_start_token, ti, end_text, cue_start_text)
            cue_start_token = ti + 1
            cue_start_text = next_start
            open_tokens = False
        elif span_len >= CLAUSE_MIN_CHARS and any(p in CLAUSE_MARK for p in between):
            close_cue(cue_start_token, ti, end_text + _trailing_span(between), cue_start_text)
            cue_start_token = ti + 1
            cue_start_text = next_start
            open_tokens = False

    if open_tokens and last_token_idx >= cue_start_token:
        lt = tokens[last_token_idx]
        close_cue(cue_start_token, last_token_idx, lt[0] + len(lt[1]), cue_start_text)

    # 最短时长保障 + 去掉纯标点行
    result = []
    for a, b, c in cues:
        if not any(is_kept_char(ch) for ch in c):
            continue
        if b - a < MIN_CUE_SEC:
            b = a + MIN_CUE_SEC
        result.append((a + chunk_offset, b + chunk_offset, c))
    return result


def strip_punctuation(text):
    """--no-punc: 句中标点替换为空格，合并多余空格"""
    out = []
    for ch in text:
        if ch in SENTENCE_END or ch in CLAUSE_MARK or ch == '…':
            out.append(' ')
        else:
            out.append(ch)
    return _re.sub(r'\s+', ' ', ''.join(out)).strip()


def cleanup_temp(wav_path, tmp_dir):
    """清理临时文件（非递归：只删本 worker 创建的单文件+目录）"""
    try:
        os.unlink(wav_path)
    except OSError:
        pass
    try:
        os.rmdir(tmp_dir)
    except OSError:
        pass


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Qwen3-ASR Worker')
    parser.add_argument('--input', required=True, help='输入音视频文件')
    parser.add_argument('--output', required=True, help='输出 SRT 文件路径')
    parser.add_argument('--language', default='zh', help='语言代码 zh/en/ja/ko/yue/auto')
    parser.add_argument('--asr-model', default='Qwen3-ASR-1.7B',
                        choices=SUPPORTED_ASR_MODELS,
                        help='转写模型（models/ 下目录名，默认 Qwen3-ASR-1.7B）')
    parser.add_argument('--device', default=None, help='设备 cuda/cpu (默认自动)')
    parser.add_argument('--context', default='', help='热词/领域上下文文本')
    parser.add_argument('--no-punc', action='store_true', help='标点替换为空格')
    args = parser.parse_args()

    # ── 输入/输出路径安全校验（入口处内联完成）──
    for raw in (args.input, args.output):
        if '..' in raw.split(os.sep) or '..' in raw.split('/'):
            print(json.dumps({'error': f'Path must not contain "..": {raw}'}), flush=True)
            sys.exit(1)
    input_path = os.path.abspath(args.input)
    output_path = os.path.abspath(args.output)

    allowed = list(ALLOWED_ROOTS)
    extra_roots = os.environ.get('AUTOCLIP_ALLOWED_MEDIA_ROOTS', '')
    allowed.extend(r for r in extra_roots.split(';') if r.strip())
    allowed = [os.path.abspath(r) for r in allowed if os.path.isdir(r)]
    for label, p in (('input', input_path), ('output', output_path)):
        ok = False
        for r in allowed:
            try:
                if os.path.commonpath([p, r]) == r:
                    ok = True
                    break
            except ValueError:
                continue  # 跨盘符
        if not ok:
            print(json.dumps({'error': f'{label} path outside allowed roots: {p}'}), flush=True)
            sys.exit(1)

    if not os.path.isfile(input_path):
        print(json.dumps({'error': f'Input file not found: {input_path}'}), flush=True)
        sys.exit(1)
    if not os.path.isdir(os.path.dirname(output_path)):
        print(json.dumps({'error': f'Output parent dir does not exist: {output_path}'}), flush=True)
        sys.exit(1)
    asr_model_dir = os.path.join(MODELS_ROOT, args.asr_model)
    if not os.path.isdir(asr_model_dir) or not os.path.isdir(ALIGNER_MODEL_DIR):
        print(json.dumps({'error': f'Qwen3 model dirs missing under models/: {args.asr_model}'}), flush=True)
        sys.exit(1)

    import torch
    device = args.device or ('cuda' if torch.cuda.is_available() else 'cpu')
    dtype = torch.bfloat16 if device.startswith('cuda') else torch.float32
    log(f"Device: {device} dtype: {dtype}")
    os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True')

    # 语言归一化: 代码 → 规范名; 无法识别则原样透传(支持直接传 'Chinese')
    language = LANGUAGE_MAP.get(str(args.language).strip().lower(), args.language.strip())
    if language:
        language = language[:1].upper() + language[1:].lower()

    # ── 1. 提取音频 ──
    t_start = time.time()
    tmp_dir = tempfile.mkdtemp(prefix='qwen3_asr_')
    wav_path = os.path.join(tmp_dir, 'audio_16k.wav')
    try:
        duration = extract_wav_16k(input_path, wav_path)
        log(f"Audio extracted: {duration:.1f}s")

        # ── 2. 载入波形并分块 ──
        import soundfile as sf
        wav, sr = sf.read(wav_path, dtype='float32', always_2d=False)
        if wav.ndim > 1:
            wav = wav.mean(axis=1)
        from qwen_asr.inference.utils import split_audio_into_chunks
        chunks = split_audio_into_chunks(wav=wav, sr=sr, max_chunk_sec=CHUNK_SECONDS)
        log(f"Split into {len(chunks)} chunks (≤{CHUNK_SECONDS}s each, low-energy boundaries)")

        from qwen_asr import Qwen3ASRModel, Qwen3ForcedAligner

        # ── 3. 阶段A: Qwen3-ASR 逐块转写 ──
        log(f"Stage A: loading ASR model ({args.asr_model}) ...")
        t0 = time.time()
        model = Qwen3ASRModel.from_pretrained(
            asr_model_dir,
            dtype=dtype,
            device_map=device,
            max_inference_batch_size=1,
            max_new_tokens=2048,
        )
        load_time = time.time() - t0
        log(f"ASR model loaded ({load_time:.1f}s)")

        texts = []
        total_chars = 0
        for i, (cwav, offset) in enumerate(chunks):
            t0 = time.time()
            r = model.transcribe(
                (cwav, sr),
                context=args.context or '',
                language=language,
            )[0]
            texts.append(r.text or '')
            total_chars += len(r.text or '')
            log(f"ASR chunk {i+1}/{len(chunks)} ({offset:.0f}s+{len(cwav)/sr:.0f}s): "
                f"{len(r.text or '')} chars ({time.time()-t0:.1f}s)")

        del model
        if device.startswith('cuda'):
            torch.cuda.empty_cache()
        log(f"Stage A done: {total_chars} chars total")

        # ── 4. 阶段B: ForcedAligner 逐块对齐 ──
        log("Stage B: loading forced aligner (Qwen3-ForcedAligner-0.6B) ...")
        t0 = time.time()
        aligner = Qwen3ForcedAligner.from_pretrained(
            ALIGNER_MODEL_DIR, dtype=dtype, device_map=device)
        log(f"Aligner loaded ({time.time()-t0:.1f}s)")

        all_cues = []
        for i, ((cwav, offset), text) in enumerate(zip(chunks, texts)):
            if not text.strip():
                continue
            t0 = time.time()
            res = aligner.align(audio=(cwav, sr), text=text, language=language)[0]
            cues = group_items_to_cues(text, res.items, offset)
            all_cues.extend(cues)
            log(f"Align chunk {i+1}/{len(chunks)}: {len(res.items)} tokens → {len(cues)} cues "
                f"({time.time()-t0:.1f}s)")

        del aligner
        if device.startswith('cuda'):
            torch.cuda.empty_cache()

        # ── 5. 写 SRT（output_path 已在入口处完成白名单校验）──
        if args.no_punc:
            all_cues = [(a, b, strip_punctuation(c)) for a, b, c in all_cues]
            all_cues = [(a, b, c) for a, b, c in all_cues if c]

        srt_lines = []
        for i, (a, b, c) in enumerate(all_cues, 1):
            srt_lines.append(str(i))
            srt_lines.append(f"{format_srt_time(a)} --> {format_srt_time(b)}")
            srt_lines.append(c)
            srt_lines.append('')

        pathlib.Path(output_path).write_text('\n'.join(srt_lines), encoding='utf-8')

        transcribe_time = time.time() - t_start
        log(f"SRT written: {len(all_cues)} cues → {output_path}")
        print(json.dumps({
            'status': 'ok',
            'model': args.asr_model,
            'device': device,
            'segments': len(all_cues),
            'text_length': total_chars,
            'load_time': round(load_time, 1),
            'transcribe_time': round(transcribe_time, 1),
            'output': output_path,
        }, ensure_ascii=False), flush=True)

    finally:
        cleanup_temp(wav_path, tmp_dir)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    main()
