# -*- coding: utf-8 -*-
"""
MOSS-Transcribe-Diarize Worker
由项目内独立虚拟环境 moss_env 的 Python 运行,负责模型加载和转录
流程: FFmpeg 提取 16k 单声道 WAV → 低能量点切 ≤600s 块 →
      逐块生成带时间戳/说话人标签的转录 → 解析 → 字幕规范化(合并短句/切长句) → SRT

模型单次推理官方上限 90 分钟,但 KV cache 按 token 线性增长,
8G 显卡上长音频会 OOM,因此本 worker 主动按低能量点分块。

用法(由 moss_transcriber.py 通过 subprocess 调用):
    moss_env\\Scripts\\python.exe utils/_moss_transcribe_worker.py \
        --input video.mp4 --output output.srt --language zh
"""
import sys
import os
import json
import time
import pathlib
import tempfile
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_ROOT = os.path.join(PROJECT_ROOT, 'models')

# 本地模型目录(models/ 下目录名),目录内含 trust_remote_code 所需的 *.py
DEFAULT_MODEL_DIR = 'MOSS-Transcribe-Diarize-0.9B'

# 分块目标时长(秒)。8G 显存实测: 600s 块峰值 7.7-7.8G 触顶,疑似触发 Windows CUDA
# 共享内存回退导致 30min 音频 50min+ 跑不完; 300s 块把 KV cache/激活显存减半,远离触顶
CHUNK_SECONDS = 300.0
# 切点搜索半径(秒): 在目标切点附近找最低能量窗口,避免切断词句
CUT_SEARCH_SECONDS = 30.0
# 尾段小于此时长并入上一块
MIN_CHUNK_SECONDS = 60.0
# 每块生成 token 上限: 直播语速约 3-5 字/秒 + 时间戳/说话人标签开销,
# 官方 README 按 90min→65536 换算约 12 token/s
MAX_NEW_TOKENS_PER_SEC = 12

# 输入/输出允许目录白名单: 项目根 + 环境变量 AUTOCLIP_ALLOWED_MEDIA_ROOTS 扩展（分号分隔）
ALLOWED_ROOTS = [PROJECT_ROOT]


def log(msg):
    print(f"[Moss-Worker] {msg}", flush=True)


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


def find_quiet_cut(wav, sr, target_sample, search_seconds=CUT_SEARCH_SECONDS):
    """在 target_sample 附近 ±search_seconds 内找能量最低的 0.5s 窗口中心作为切点"""
    n = len(wav)
    search = int(search_seconds * sr)
    lo = max(target_sample - search, 0)
    hi = min(target_sample + search, n)
    if hi - lo < int(1.0 * sr) or lo == 0:
        return target_sample
    win = int(0.5 * sr)
    hop = int(0.25 * sr)
    abs_sq = None
    best_pos, best_energy = target_sample, None
    # 用滑动累加和求窗口能量,避免逐窗重复计算
    x = wav[lo:hi].astype('float32')
    x = x * x
    cum = _cumsum(x)
    positions = range(0, max(len(x) - win, 1), hop)
    for p in positions:
        energy = cum[p + win] - cum[p] if p + win < len(cum) else cum[-1] - cum[p]
        if best_energy is None or energy < best_energy:
            best_energy = energy
            best_pos = lo + p + win // 2
    return best_pos


def _cumsum(x):
    import numpy as np
    c = np.zeros(len(x) + 1, dtype='float64')
    c[1:] = np.cumsum(x, dtype='float64')
    return c


def split_audio_chunks(wav, sr, chunk_seconds=None):
    """按目标时长 + 低能量切点分块，返回 [(start_sample, end_sample), ...]"""
    if chunk_seconds is None:
        chunk_seconds = CHUNK_SECONDS
    n = len(wav)
    min_chunk = int(MIN_CHUNK_SECONDS * sr)
    chunks = []
    start = 0
    while start < n:
        end = min(start + int(chunk_seconds * sr), n)
        if n - end < min_chunk:
            end = n  # 尾段太短并入本块
        elif end < n:
            end = find_quiet_cut(wav, sr, end)
        chunks.append((start, end))
        start = end
    return chunks


def build_messages(audio_array, prompt):
    """与 inference_utils.build_transcription_messages 相同结构,
    但直接传 numpy 数组(transformers load_audio 对 ndarray 原样透传,
    避免其字符串路径走 librosa/torchcodec 后端)"""
    return [
        {
            "role": "user",
            "content": [
                {"type": "audio", "audio": audio_array},
                {"type": "text", "text": prompt},
            ],
        }
    ]


def generate_chunk(model, processor, wav_array, device, dtype, max_new_tokens, prompt):
    """
    与官方 inference_utils.generate_transcription 相同的生成流程,
    但音频直接以 numpy 数组传给 processor——官方 process_audio_info
    对消息里的 audio 字段做 `or` 链取值,ndarray 会触发歧义值错误,
    且其字符串路径依赖 librosa/torchcodec 后端。
    """
    import copy
    import torch

    messages = build_messages(wav_array, prompt)
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    audio_kwargs = {"device": str(device)} if device.type == "cuda" else {}
    amp = (torch.amp.autocast("cuda", dtype=dtype)
           if device.type == "cuda" and dtype in (torch.float16, torch.bfloat16)
           else torch.no_grad())
    with amp:
        inputs = processor(
            text=text,
            audio=[wav_array],
            max_length=131072,
            audio_kwargs=audio_kwargs,
            return_tensors="pt",
        ).to(device)

    prompt_len = int(inputs["attention_mask"][0].sum().item())
    generation_config = copy.deepcopy(model.generation_config)
    generation_config.max_new_tokens = max_new_tokens
    generation_config.do_sample = False

    with torch.inference_mode(), amp:
        outputs = model.generate(
            input_ids=inputs["input_ids"],
            attention_mask=inputs["attention_mask"],
            input_features=inputs["input_features"],
            audio_feature_lengths=inputs["audio_feature_lengths"],
            audio_chunk_mapping=inputs["audio_chunk_mapping"],
            generation_config=generation_config,
        )

    generated_ids = outputs[0][prompt_len:]
    text = processor.tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
    return text, int(generated_ids.numel())


SENTENCE_END = '。！？!?…；;'
CLAUSE_MARK = '，,、：:'


def strip_punctuation(text):
    """--no-punc: 句中标点替换为空格，合并多余空格"""
    import re as _re
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


def load_model(model_dir, device):
    """与官方 app/model_runner.py 一致: sdpa 优先(未装 flash-attn),失败退 eager"""
    import torch
    from transformers import AutoModelForCausalLM, AutoProcessor
    candidates = ("sdpa", "eager") if device.type == "cuda" else ("sdpa",)
    model = None
    last_err = None
    for attn in candidates:
        try:
            model = AutoModelForCausalLM.from_pretrained(
                model_dir,
                trust_remote_code=True,
                dtype="auto",
                attn_implementation=attn,
            )
            log(f"Model loaded with attn_implementation={attn}")
            break
        except Exception as e:
            last_err = e
            log(f"attn_implementation={attn} failed: {e}")
    if model is None:
        raise RuntimeError(f"model load failed: {last_err}")
    processor = AutoProcessor.from_pretrained(
        model_dir, trust_remote_code=True, fix_mistral_regex=True)
    dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
    model = model.to(dtype=dtype).to(device).eval()
    return model, processor, dtype


def main():
    import argparse

    parser = argparse.ArgumentParser(description='MOSS-Transcribe-Diarize Worker')
    parser.add_argument('--input', required=True, help='输入音视频文件')
    parser.add_argument('--output', required=True, help='输出 SRT 文件路径')
    parser.add_argument('--language', default='zh', help='语言代码(兼容接口,模型自动识别语言)')
    parser.add_argument('--model-dir', default=DEFAULT_MODEL_DIR,
                        help=f'models/ 下模型目录名 (默认: {DEFAULT_MODEL_DIR})')
    parser.add_argument('--device', default=None, help='设备 cuda/cpu (默认自动)')
    parser.add_argument('--context', default='', help='热词/领域上下文(预留,当前未接入提示词)')
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
    model_dir = os.path.join(MODELS_ROOT, args.model_dir)
    if not os.path.isdir(model_dir):
        print(json.dumps({'error': f'MOSS model dir missing under models/: {args.model_dir}'}), flush=True)
        sys.exit(1)

    import torch
    device = torch.device(args.device or ('cuda' if torch.cuda.is_available() else 'cpu'))
    log(f"Device: {device}")
    os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True')

    t_start = time.time()
    tmp_dir = tempfile.mkdtemp(prefix='moss_asr_')
    wav_path = os.path.join(tmp_dir, 'audio_16k.wav')
    try:
        # ── 1. 提取音频 ──
        duration = extract_wav_16k(input_path, wav_path)
        log(f"Audio extracted: {duration:.1f}s")

        import numpy as np
        import soundfile as sf
        from moss_transcribe_diarize import parse_transcript
        from moss_transcribe_diarize.inference_utils import (
            DEFAULT_PROMPT, resolve_device,
        )
        from moss_transcribe_diarize.subtitle.postprocess import (
            subtitle_segments_from_transcript_segments,
        )

        # ── 2. 载入波形并分块 ──
        wav, sr = sf.read(wav_path, dtype='float32', always_2d=False)
        if wav.ndim > 1:
            wav = wav.mean(axis=1)
        chunks = split_audio_chunks(wav, sr)
        log(f"Split into {len(chunks)} chunks (≤{CHUNK_SECONDS:.0f}s each, low-energy boundaries)")

        # ── 3. 载入模型 ──
        log("Loading model ...")
        t0 = time.time()
        model, processor, dtype = load_model(model_dir, resolve_device(str(device)))
        load_time = time.time() - t0
        log(f"Model loaded ({load_time:.1f}s)")

        # ── 4. 逐块转写 ──
        raw_segments = []  # (start_sec, end_sec, speaker, text) 全局时间轴
        total_chars = 0
        for i, (s0, s1) in enumerate(chunks):
            chunk_wav = wav[s0:s1]
            chunk_dur = (s1 - s0) / sr
            offset = s0 / sr
            max_new_tokens = min(16384, max(2048, int(chunk_dur * MAX_NEW_TOKENS_PER_SEC)))
            t0 = time.time()
            text, gen_tokens = generate_chunk(
                model, processor, chunk_wav,
                resolve_device(str(device)), dtype,
                max_new_tokens, DEFAULT_PROMPT,
            )
            elapsed = time.time() - t0
            if gen_tokens >= max_new_tokens:
                log(f"WARNING chunk {i+1}: hit max_new_tokens cap, text may be truncated")
            parsed = parse_transcript(text)
            for seg in parsed:
                raw_segments.append((seg.start + offset, seg.end + offset,
                                     seg.speaker, seg.text))
            total_chars += len(text)
            log(f"Chunk {i+1}/{len(chunks)} ({offset:.0f}s+{chunk_dur:.0f}s): "
                f"{len(parsed)} segments, {len(text)} chars ({elapsed:.1f}s, "
                f"RTFx {chunk_dur/elapsed if elapsed > 0 else 0:.1f}x)")

        del model
        if device.type == 'cuda':
            torch.cuda.empty_cache()

        # ── 5. 字幕规范化(合并短句/切长句/修重叠,官方 subtitle 后处理) ──
        from moss_transcribe_diarize.transcript_parser import TranscriptSegment
        ts_segments = [TranscriptSegment(start=a, end=b, speaker=sp, text=tx)
                       for a, b, sp, tx in raw_segments]
        if args.no_punc:
            ts_segments = [TranscriptSegment(start=s.start, end=s.end, speaker=s.speaker,
                                             text=strip_punctuation(s.text))
                           for s in ts_segments]
            ts_segments = [s for s in ts_segments if s.text]
        subs = subtitle_segments_from_transcript_segments(ts_segments)

        # ── 6. 写 SRT ──
        srt_lines = []
        speaker_data = []
        speakers_seen = set()
        for i, seg in enumerate(subs, 1):
            srt_lines.append(str(i))
            srt_lines.append(f"{format_srt_time(seg.start)} --> {format_srt_time(seg.end)}")
            srt_lines.append(seg.text)
            srt_lines.append('')
            speaker_data.append({'start': round(seg.start, 3), 'end': round(seg.end, 3),
                                 'speaker': seg.speaker})
            speakers_seen.add(seg.speaker)

        pathlib.Path(output_path).write_text('\n'.join(srt_lines), encoding='utf-8')

        transcribe_time = time.time() - t_start
        log(f"SRT written: {len(subs)} cues, speakers={sorted(speakers_seen)} → {output_path}")
        print(json.dumps({
            'status': 'ok',
            'model': args.model_dir,
            'device': str(device),
            'segments': len(subs),
            'speakers': sorted(speakers_seen),
            'speaker_data': speaker_data,
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
