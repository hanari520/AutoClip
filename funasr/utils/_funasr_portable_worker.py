# -*- coding: utf-8 -*-
"""
FunASR Portable Worker v2
由便携版 Python 运行,负责模型加载和转录
支持两种模式:
  1. sensevoice: VAD 分段 → 逐段 SenseVoiceSmall → 带时间戳 SRT
  2. paraformer: 自带 VAD+时间戳,直接出 SRT

用法(由 funasr_transcriber.py 通过 subprocess 调用):
    F:\FunASR-Portable-GPU\runtime\python\python.exe utils/_funasr_portable_worker.py \
        --input video.mp4 --output output.srt --model sensevoice --language zh
"""
import sys
import os
import json
import time
import tempfile
import subprocess
from pathlib import Path

# 把便携版的 FunASR 加到 sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, 'app')
MODELS = os.path.join(ROOT, 'workspace', 'models')
SITE_PKGS = os.path.join(ROOT, 'runtime', 'python', 'Lib', 'site-packages')
torch_lib = os.path.join(SITE_PKGS, 'torch', 'lib')

sys.path.insert(0, os.path.join(ROOT, 'runtime', 'python'))
sys.path.insert(0, SITE_PKGS)
sys.path.insert(0, APP)
os.environ['MODELSCOPE_CACHE'] = MODELS
os.environ['HF_HOME'] = os.path.join(MODELS, 'huggingface')
os.environ['TRANSFORMERS_CACHE'] = os.path.join(MODELS, 'transformers')

if sys.platform == 'win32':
    os.add_dll_directory(torch_lib)
os.environ['PATH'] = torch_lib + os.pathsep + os.environ.get('PATH', '')

# ---------------------------------------------------------------------------
# 模型路径映射
# ---------------------------------------------------------------------------
MODEL_PATHS = {
    'sensevoice': os.path.join(MODELS, 'models', 'iic', 'SenseVoiceSmall'),
    'paraformer-zh': os.path.join(MODELS, 'models', 'iic',
                                   'speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch'),
    'paraformer-en': os.path.join(MODELS, 'models', 'iic',
                                   'speech_paraformer-large-vad-punc_asr_nat-en-16k-common-vocab10020'),
    'paraformer-large-zh': os.path.join(MODELS, 'damo',
                                         'speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch'),
}

VAD_MODEL = os.path.join(MODELS, 'models', 'iic', 'speech_fsmn_vad_zh-cn-16k-common-pytorch')
PUNC_MODEL = os.path.join(MODELS, 'models', 'iic',
                           'punc_ct-transformer_cn-en-common-vocab471067-large')

# cam++ 说话人分离模型(本地路径,避免联网下载)
SPK_MODEL = os.path.join(MODELS, 'models', 'iic', 'speech_campplus_sv_zh-cn_16k-common')

# paraformer-zh 官方推荐参数（ModelScope 模型卡/README 用法）
# 其余参数一律走 funasr 1.3.9 内置默认：
#   - batch_size_threshold_s 默认 60，spk_mode 默认 punc_segment
#   - language / use_itn 是 SenseVoice 专用参数，对 Paraformer 无效，已移除
#   - preset_spk_num 非官方推荐项，移除后 cam++ 自动聚类说话人数
PARAFORMER_ZH_CONFIG = {
    'vad_kwargs': {
        # VAD 段长上限 30s（README 推荐值；2026-08-30 拍板，默认 60s）
        'max_single_segment_time': 30000,
    },
    # 官方推荐 batch_size_s=300（funasr 1.3.9 默认值，显式写出防漂移）
    'batch_size_s': 300,
}

# 最小语音段长度(毫秒),太短的可能是噪音
MIN_SEGMENT_MS = 500
# 合并间隔小于此值的相邻段
MERGE_GAP_MS = 300


# SenseVoice 情感标签映射
EMOTION_TAGS = {
    '<|HAPPY|>': 'HAPPY',
    '<|SAD|>': 'SAD',
    '<|ANGRY|>': 'ANGRY',
    '<|NEUTRAL|>': 'NEUTRAL',
    '<|SURPRISED|>': 'SURPRISED',
    '<|FEARFUL|>': 'FEARFUL',
    '<|DISGUSTED|>': 'DISGUSTED',
    '<|EMOTIONAL|>': 'EMOTIONAL',
}


def parse_sensevoice_output(raw_text):
    """
    解析 SenseVoice 原始输出,分离情感标签和干净文本
    输入: '<|zh|><|HAPPY|><|Speech|><|withitn|>今天真是太开心了。'
    返回: (clean_text, emotion_label)
    """
    import re

    # 1. 提取情感标签
    emotion = 'NEUTRAL'
    for tag, label in EMOTION_TAGS.items():
        if tag in raw_text:
            emotion = label
            break

    # 2. 清理所有特殊标签
    text = re.sub(r'<\|[^|]*\|>', '', raw_text)
    text = re.sub(r'<[^<>]*>', '', text)

    # 3. 英文去重
    words = text.split()
    cleaned = []
    prev = None
    for w in words:
        if w == prev and w.isascii():
            continue
        cleaned.append(w)
        prev = w
    text = ' '.join(cleaned)
    text = re.sub(r'\s+', ' ', text)

    return text.strip(), emotion


def clean_sensevoice_text(text):
    """清理 SenseVoice 输出的特殊标签(兼容旧接口)"""
    import re
    text = re.sub(r'<\|[^|]*\|>', '', text)
    text = re.sub(r'<[^<>]*>', '', text)
    # 英文去重
    words = text.split()
    cleaned = []
    prev = None
    for w in words:
        if w == prev and w.isascii():
            continue
        cleaned.append(w)
        prev = w
    text = ' '.join(cleaned)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _convert_emotion_entries(emotion_entries):
    """
    将 SenseVoice 情感条目转换为 generate_clips_combined.py 兼容格式
    输入格式: {emotion: 'HAPPY', start_ms: 1200, end_ms: 3500, text: '...'}
    输出格式: {label: 'happy', label_cn: '开心', score: 0.85, start: 1.2, end: 3.5, text: '...'}
    """
    SENSEVOICE_TO_EMOTION2VEC = {
        'HAPPY': ('happy', '开心'),
        'SAD': ('sad', '难过'),
        'ANGRY': ('angry', '生气'),
        'NEUTRAL': ('neutral', '平静'),
        'SURPRISED': ('surprised', '惊讶'),
        'FEARFUL': ('fear', '恐惧'),
        'DISGUSTED': ('disgusted', '厌恶'),
        'EMOTIONAL': ('excited', '兴奋'),
    }
    result = []
    for entry in emotion_entries:
        sv_emotion = entry.get('emotion', 'NEUTRAL')
        label, label_cn = SENSEVOICE_TO_EMOTION2VEC.get(sv_emotion, ('neutral', '平静'))
        result.append({
            'label': label,
            'label_cn': label_cn,
            'score': 0.85 if label != 'neutral' else 0.30,
            'start': entry['start_ms'] / 1000.0,
            'end': entry['end_ms'] / 1000.0,
            'start_str': entry.get('start_str', ''),
            'end_str': entry.get('end_str', ''),
            'text': entry.get('text', ''),
            'sensevoice_emotion': sv_emotion,
        })
    return result


def format_srt_time(seconds):
    """秒 → SRT 时间格式"""
    ms = int((seconds % 1) * 1000)
    total_secs = int(seconds)
    h = total_secs // 3600
    m = (total_secs % 3600) // 60
    s = total_secs % 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def merge_segments(segments, min_len_ms=MIN_SEGMENT_MS, merge_gap_ms=MERGE_GAP_MS):
    """合并相邻语音段,去掉太短的"""
    if not segments:
        return []
    merged = []
    current = list(segments[0])
    for seg in segments[1:]:
        if seg[0] - current[1] <= merge_gap_ms:
            current[1] = seg[1]
        else:
            dur = current[1] - current[0]
            if dur >= min_len_ms:
                merged.append(tuple(current))
            current = list(seg)
    dur = current[1] - current[0]
    if dur >= min_len_ms:
        merged.append(tuple(current))
    return merged



def transcribe_sensevoice(video_path, output_path, language, device,
                          enable_speaker=False, punc_enabled=True, models=None):
    """两阶段管道:VAD 分段 → SenseVoiceSmall 逐段转录(可选标点恢复) → SRT

    models: 批处理模式传入常驻 (vad, asr, punc_model) 复用已加载模型;
            None=单文件模式自行加载并在转录后释放
    """
    from funasr import AutoModel

    # 阶段1:VAD 检测语音段(批处理模式下模型常驻,只跑推理)
    t0 = time.time()
    if models is not None:
        vad, asr, punc_model = models
        vad_result = vad.generate(input=video_path)
        vad_time = time.time() - t0
        load_time = 0.0
    else:
        print("[Worker] Stage 1/2: VAD speech detection...", flush=True)
        vad = AutoModel(model=VAD_MODEL, device=device, disable_update=True)
        vad_result = vad.generate(input=video_path)
        vad_time = time.time() - t0

    if not vad_result or len(vad_result) == 0:
        raise ValueError('VAD returned empty result')

    # VAD 返回格式: [{'key': 'filename', 'value': [[s1,e1], [s2,e2], ...]}]
    vad_value = vad_result[0].get('value', [])
    if not vad_value:
        # 可能整个文件都是语音,作为一个大段
        # 用 FFmpeg 获取时长
        import re as _re
        probe = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
            capture_output=True, text=True, encoding='utf-8', errors='replace'
        )
        try:
            duration_ms = int(float(probe.stdout.strip()) * 1000)
        except Exception:
            duration_ms = 300000  # 默认5分钟
        vad_value = [[0, duration_ms]]

    raw_segments = [(int(s[0]), int(s[1])) for s in vad_value if len(s) >= 2]
    segments = merge_segments(raw_segments)
    print(f"[Worker] VAD done ({vad_time:.1f}s): {len(segments)} speech segments "
          f"(from {len(raw_segments)} raw)", flush=True)

    if not segments:
        raise ValueError('No speech detected')

    # 阶段2:加载 SenseVoiceSmall,逐段转录(批处理模式下已在常驻模型中,跳过)
    if models is None:
        del vad  # 释放 GPU 显存

        print("[Worker] Stage 2/2: SenseVoiceSmall per-segment ASR...", flush=True)
        t0 = time.time()

        model_path = MODEL_PATHS['sensevoice']
        asr_kwargs = {'model': model_path, 'device': device, 'disable_update': True}
        if enable_speaker:
            asr_kwargs['spk_model'] = SPK_MODEL
            print('[Worker] Speaker diarization: cam++ enabled', flush=True)
        asr = AutoModel(**asr_kwargs)
        load_time = time.time() - t0

        # 标点恢复模型（ct-transformer，SenseVoice 本身不带标点）
        punc_model = None
        if punc_enabled and os.path.isdir(PUNC_MODEL):
            try:
                punc_model = AutoModel(model=PUNC_MODEL, device=device, disable_update=True)
                print('[Worker] Punctuation model: ct-transformer enabled', flush=True)
            except Exception as e:
                print(f"[Worker] Warning: punc model load failed: {e}", flush=True)

    # 创建临时目录存放提取的音频段
    tmp_dir = Path(tempfile.mkdtemp(prefix='autoclip_worker_'))
    srt_lines = []
    emotion_entries = []  # 情感数据收集
    segment_count = 0
    total_chars = 0
    transcribe_start = time.time()

    FFMPEG = 'ffmpeg'
    # 尝试找便携版自带的 ffmpeg
    portable_ffmpeg = os.path.join(ROOT, 'runtime', 'ffmpeg', 'ffmpeg.exe')
    if os.path.exists(portable_ffmpeg):
        FFMPEG = portable_ffmpeg

    for i, (seg_start, seg_end) in enumerate(segments):
        seg_audio = tmp_dir / f'seg_{i:04d}.wav'
        start_sec = seg_start / 1000.0
        duration_sec = (seg_end - seg_start) / 1000.0
        if duration_sec < 0.1:  # 太短跳过
            continue

        # FFmpeg 提取音频段
        ff_cmd = [
            FFMPEG, '-y', '-v', 'quiet',
            '-ss', str(start_sec),
            '-t', str(duration_sec),
            '-i', video_path,
            '-vn', '-ac', '1', '-ar', '16000',
            '-acodec', 'pcm_s16le',
            str(seg_audio),
        ]
        subprocess.run(ff_cmd, capture_output=True)

        if not seg_audio.exists() or seg_audio.stat().st_size < 100:
            continue

        # ASR 转录
        try:
            r = asr.generate(input=str(seg_audio), language=language)
            raw_text = r[0].get('text', '') if r else ''
            clean_text, emotion = parse_sensevoice_output(raw_text)
            if not clean_text.strip():
                continue

            # 标点恢复（失败时保留原文本）
            if punc_model:
                try:
                    punc_result = punc_model.generate(input=clean_text)
                    if punc_result and punc_result[0].get('text'):
                        clean_text = punc_result[0]['text'].strip()
                except Exception as e:
                    print(f"[Worker] Warning: punc failed for segment {i}: {e}", flush=True)

            # 提取说话人标签(cam++)
            speaker_entry = None
            if enable_speaker and r and len(r) > 0:
                spk_info = r[0].get('spk', None)
                if spk_info is not None:
                    speaker_entry = {'spk': spk_info}

            segment_count += 1
            total_chars += len(clean_text)
            srt_lines.append(str(segment_count))
            srt_lines.append(
                f"{format_srt_time(seg_start / 1000)} --> {format_srt_time(seg_end / 1000)}"
            )
            srt_lines.append(f"[{emotion}] {clean_text}")
            srt_lines.append('')

            # 记录情感数据
            entry = {
                'index': segment_count,
                'start_ms': seg_start,
                'end_ms': seg_end,
                'start_str': format_srt_time(seg_start / 1000),
                'end_str': format_srt_time(seg_end / 1000),
                'emotion': emotion,
                'text': clean_text,
            }
            if speaker_entry:
                entry['spk'] = speaker_entry['spk']
            emotion_entries.append(entry)
        except Exception as e:
            print(f"[Worker] Warning: segment {i} failed: {e}", flush=True)
            continue

        # 清理临时文件
        try:
            seg_audio.unlink()
        except Exception:
            pass

    transcribe_time = time.time() - transcribe_start

    # 清理临时目录
    try:
        tmp_dir.rmdir()
    except Exception:
        pass

    # 写入 SRT(含情感标签前缀)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(srt_lines))

    # 写入 _emotion.json(供 step 3 情绪驱动分析使用)
    emotion_json_path = output_path.replace('.srt', '_emotion.json')
    emotion_segments_for_json = _convert_emotion_entries(emotion_entries)
    with open(emotion_json_path, 'w', encoding='utf-8') as f:
        json.dump({'emotion_segments': emotion_segments_for_json}, f, ensure_ascii=False, indent=2)
    print(f"[Worker] Emotion data saved: {os.path.basename(emotion_json_path)} "
          f"({len(emotion_segments_for_json)} entries)", flush=True)

    return {
        'segments': segment_count,
        'text_length': total_chars,
        'load_time': load_time,
        'transcribe_time': transcribe_time,
        'vad_time': vad_time,
        'emotion_json': emotion_json_path,
    }


def _split_text_to_utterances(text, n_parts):
    """
    将全文按中文标点切分,再按比例分配到 n_parts 个 utterance 段。
    输入: 合并后的时间戳个数
    输出: 等长的文本段列表
    """
    import re
    if n_parts <= 1:
        return [text]
    # 按标点拆句子
    sentences = re.split(r'(?<=[。!?,、;:\n\.\!\?,;:])', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return [text]
    # 如果全文只有 1 句(无标点),按字数比例均分
    if len(sentences) == 1:
        full = sentences[0]
        # 先按空格分词
        words = full.split()
        if len(words) >= n_parts:
            # 词够多:按词数比例分配
            result = []
            words_per = len(words) / n_parts
            for i in range(n_parts):
                start = int(i * words_per)
                end = int((i + 1) * words_per)
                result.append(' '.join(words[start:end]))
            return result
        else:
            # 词不够:直接按字符比例分
            result = []
            chars_per = len(full) / n_parts
            for i in range(n_parts):
                start = int(i * chars_per)
                end = int((i + 1) * chars_per)
                result.append(full[start:end].strip())
            return result
    if len(sentences) <= n_parts:
        # 不够分,用空格填
        result = sentences[:]
        while len(result) < n_parts:
            result.append('')
        return result
    # 句子比 utterance 多,按比例归并
    result = []
    ratio = len(sentences) / n_parts
    for i in range(n_parts):
        start = int(i * ratio)
        end = int((i + 1) * ratio)
        chunk = ''.join(sentences[start:end])
        result.append(chunk)
    return result


def _clean_punctuation(text):
    """
    句中标点用空格代替,结尾不留空格。
    处理中英文标点:,。!?、;:""''()...- ,.!?;:()
    """
    import re
    # 中文标点 → 空格
    text = re.sub(r'[,。!?、;:""''()...-]', ' ', text)
    # 英文标点 → 空格(保留字母数字周边的)
    text = re.sub(r'[,!?;:()\.]', ' ', text)
    # 合并多余空格,去首尾
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _merge_cjk_spaces(text):
    """合并相邻 CJK 字符/假名/谚文之间的空格,保留英文/数字周围的空格。"""
    import re
    # CJK: 中文 + 日文假名 + 韩文
    cjk = r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]'
    # 删除 CJK 之间的空格: 用 lookbehind/lookahead 避免重叠匹配漏掉
    text = re.sub(f'(?<={cjk})\s+(?={cjk})', '', text)
    return text


def _paraformer_model_kwargs(model_name, device, vad_enabled, punc_enabled, enable_speaker):
    """构造 Paraformer AutoModel 参数（VAD/标点/说话人按模型与开关组装）"""
    model_path = MODEL_PATHS.get(model_name)
    if not model_path or not os.path.isdir(model_path):
        model_path = f"iic/{model_name}"

    model_kwargs = {'model': model_path, 'device': device, 'disable_update': True}

    # paraformer-zh 应用官方推荐配置（仅该模型，不影响其他模型）
    if model_name == 'paraformer-zh':
        model_kwargs.update(PARAFORMER_ZH_CONFIG)
        if not vad_enabled:
            model_kwargs.pop('vad_kwargs', None)

    # 长音频模型自带 VAD+标点,但允许叠加外部 VAD 控制分段粒度
    is_long_audio = 'large-zh' in model_name
    if vad_enabled and os.path.isdir(VAD_MODEL):
        if model_name == 'paraformer-zh':
            # 参考配置自带 vad_kwargs，只需补充模型路径
            model_kwargs['vad_model'] = VAD_MODEL
        else:
            model_kwargs['vad_model'] = VAD_MODEL
            model_kwargs['vad_kwargs'] = {'max_single_segment_time': 30000}
    if not is_long_audio and os.path.isdir(PUNC_MODEL):
        # 标点模型:非长音频版始终加载(保时间戳分割),no_punc 只在文案层处理
        # 长音频版自带标点,不重复加载
        model_kwargs['punc_model'] = PUNC_MODEL

    # 说话人分离（cam++），参考配置指定 preset_spk_num=5
    if enable_speaker and os.path.isdir(SPK_MODEL):
        model_kwargs['spk_model'] = SPK_MODEL

    return model_kwargs


def transcribe_paraformer(video_path, output_path, model_name, device, vad_enabled,
                          punc_enabled, enable_speaker=False, model=None):
    """Paraformer 管道:自带 VAD+时间戳,直接出 SRT

    model: 批处理模式传入常驻 AutoModel 复用;None=单文件模式自加载
    """
    from funasr import AutoModel

    model_kwargs = _paraformer_model_kwargs(model_name, device, vad_enabled,
                                            punc_enabled, enable_speaker)

    if model is None:
        print(f"[Worker] Loading model '{model_name}'...", flush=True)
        t0 = time.time()
        model = AutoModel(**model_kwargs)
        load_time = time.time() - t0
        print(f"[Worker] Model loaded ({load_time:.1f}s)", flush=True)
    else:
        load_time = 0.0

    print(f"[Worker] Transcribing: {os.path.basename(video_path)} "
          f"({os.path.getsize(video_path)/1024/1024:.1f}MB)", flush=True)
    t0 = time.time()
    result = model.generate(input=video_path)
    transcribe_time = time.time() - t0

    # Paraformer 结果自带 timestamp,按分段生成 SRT
    srt_lines = []
    segment_count = 0
    total_chars = 0
    speaker_data = []  # 说话人数据收集

    if result and isinstance(result, list):
        for seg_item in result:
            if not isinstance(seg_item, dict):
                continue
            text = seg_item.get('text', '')
            if not text.strip():
                continue

            timestamps = seg_item.get('timestamp', [])
            if timestamps and len(timestamps) > 0:
                has_sentence_info = 'sentence_info' in seg_item

                # 合并短时间戳为 utterance 段(无 sentence_info 时)
                if has_sentence_info:
                    ts_list = [[int(ts[0]), int(ts[1])] for ts in timestamps]
                else:
                    # 合并间隔 < 500ms 和时长 < 2s 的相邻语音段
                    merged = []
                    grp_start, grp_end = int(timestamps[0][0]), int(timestamps[0][1])
                    for ts in timestamps[1:]:
                        s, e = int(ts[0]), int(ts[1])
                        # 间隔 ≤500ms 且当前组还不长 → 合并
                        if s - grp_end <= 500 and (e - grp_start) < 8000:
                            grp_end = e
                        else:
                            merged.append([grp_start, grp_end])
                            grp_start, grp_end = s, e
                    merged.append([grp_start, grp_end])
                    ts_list = merged
                    # 用中文标点续断处切分全文,按时间比例分配到各 utterance
                    text_parts = _split_text_to_utterances(text, len(ts_list))

                for i, ts in enumerate(ts_list):
                    if not isinstance(ts, (list, tuple)) or len(ts) < 2:
                        continue
                    start_ms, end_ms = int(ts[0]), int(ts[1])
                    if end_ms - start_ms < 500:  # 合并后仍太短 → 跳过
                        continue

                    # 从 sentence_info 取分段文本(如果有)
                    if has_sentence_info:
                        si = seg_item['sentence_info']
                        seg_text = si[i].get('text', '') if i < len(si) else ''
                    else:
                        seg_text = text_parts[i] if i < len(text_parts) else text

                    # 长音频模型输出是空格分隔的单字，合并 CJK 间的空格
                    if 'large-zh' in model_name:
                        seg_text = _merge_cjk_spaces(seg_text)

                    if not seg_text.strip():
                        continue

                    # 提取说话人标签
                    spk_id = None
                    if enable_speaker:
                        spk_list = seg_item.get('spk', [])
                        if isinstance(spk_list, list) and i < len(spk_list):
                            spk_id = int(spk_list[i]) if spk_list[i] is not None else None
                        elif isinstance(spk_list, (int, float)):
                            spk_id = int(spk_list)

                    segment_count += 1
                    total_chars += len(seg_text)
                    if enable_speaker and spk_id is not None:
                        speaker_data.append({
                            'index': segment_count, 'spk': int(spk_id),
                            'start': start_ms / 1000, 'end': end_ms / 1000,
                            'start_str': format_srt_time(start_ms / 1000),
                            'end_str': format_srt_time(end_ms / 1000),
                            'text': seg_text,
                        })
                    srt_lines.append(str(segment_count))
                    srt_lines.append(
                        f"{format_srt_time(start_ms / 1000)} --> "
                        f"{format_srt_time(end_ms / 1000)}"
                    )
                    spk_prefix = f'[SPK:{spk_id}] ' if spk_id is not None else ''
                    display_text = seg_text if punc_enabled else _clean_punctuation(seg_text)
                    srt_lines.append(f'{spk_prefix}{display_text}')
                    srt_lines.append('')
            else:
                # 无 timestamp:整个作为一段
                segment_count += 1
                total_chars += len(text)
                srt_lines.append(str(segment_count))
                srt_lines.append("00:00:00,000 --> 00:00:05,000")
                display_text = text[:2000] if punc_enabled else _clean_punctuation(text[:2000])
                srt_lines.append(display_text)
                srt_lines.append('')

    if segment_count == 0:
        # 回退
        text = str(result[0].get('text', '')) if result else ''
        if text:
            display_text = text[:2000] if punc_enabled else _clean_punctuation(text[:2000])
            srt_lines = ["1", "00:00:00,000 --> 00:00:05,000", display_text, ""]
            segment_count = 1
            total_chars = len(text)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(srt_lines))

    return {
        'segments': segment_count,
        'text_length': total_chars,
        'load_time': load_time,
        'transcribe_time': transcribe_time,
        'speaker_data': speaker_data,
    }


def run_batch(manifest_path):
    """批处理模式:读取清单,模型只加载一次,逐个转录(单条失败不影响后续)

    清单格式: {"items": [{"input": "...", "output": "..."}, ...],
              "model": "sensevoice", "language": "zh", "device": null,
              "speaker": false, "punc": true, "vad": true}
    每条完成后输出一行 {"type":"item",...} JSON;结束输出 {"status":"ok","batch":{...}} 汇总。
    """
    try:
        with open(manifest_path, encoding='utf-8') as f:
            manifest = json.load(f)
    except Exception as e:
        print(json.dumps({'error': f'Batch manifest unreadable: {e}'}), flush=True)
        sys.exit(1)

    items = manifest.get('items') or []
    model_name = manifest.get('model', 'sensevoice')
    language = manifest.get('language', 'zh')
    device = manifest.get('device')
    enable_speaker = bool(manifest.get('speaker'))
    punc_enabled = bool(manifest.get('punc', True))
    vad_enabled = bool(manifest.get('vad', True))

    if not items:
        print(json.dumps({'error': 'Batch manifest has no items'}), flush=True)
        sys.exit(1)
    if model_name not in MODEL_PATHS:
        print(json.dumps({'error': f'Unknown model: {model_name}'}), flush=True)
        sys.exit(1)

    if device is None:
        import torch
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"[Worker] Batch: {len(items)} items | model: {model_name} | device: {device}",
          flush=True)

    # 检查 FFmpeg（SenseVoice 模式抽音频段必需）
    ffmpeg_path = os.path.join(ROOT, 'runtime', 'ffmpeg', 'ffmpeg.exe')
    if model_name == 'sensevoice' and not any(
        os.path.exists(p) for p in ['ffmpeg', 'ffmpeg.exe', ffmpeg_path]
    ):
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        except Exception:
            print(json.dumps({
                'error': 'FFmpeg not found. SenseVoice mode requires ffmpeg for audio extraction.'
            }), flush=True)
            sys.exit(1)

    # 模型只加载一次,跨条目常驻
    models = None
    pf_model = None
    from funasr import AutoModel
    t0 = time.time()
    if model_name == 'sensevoice':
        vad = AutoModel(model=VAD_MODEL, device=device, disable_update=True)
        asr_kwargs = {'model': MODEL_PATHS['sensevoice'], 'device': device,
                      'disable_update': True}
        if enable_speaker:
            asr_kwargs['spk_model'] = SPK_MODEL
            print('[Worker] Speaker diarization: cam++ enabled', flush=True)
        asr = AutoModel(**asr_kwargs)
        punc_model = None
        if punc_enabled and os.path.isdir(PUNC_MODEL):
            try:
                punc_model = AutoModel(model=PUNC_MODEL, device=device, disable_update=True)
                print('[Worker] Punctuation model: ct-transformer enabled', flush=True)
            except Exception as e:
                print(f"[Worker] Warning: punc model load failed: {e}", flush=True)
        models = (vad, asr, punc_model)
    else:
        pf_kwargs = _paraformer_model_kwargs(model_name, device, vad_enabled,
                                             punc_enabled, enable_speaker)
        pf_model = AutoModel(**pf_kwargs)
    print(f"[Worker] Models loaded once for batch ({time.time()-t0:.1f}s)", flush=True)

    results = []
    ok_count = 0
    for idx, item in enumerate(items):
        inp = item.get('input', '')
        outp = item.get('output', '')
        t0 = time.time()
        try:
            if not inp or not os.path.isfile(inp):
                raise FileNotFoundError(f'input not found: {inp}')
            out_parent = os.path.dirname(os.path.abspath(outp)) if outp else ''
            if not outp or not os.path.isdir(out_parent):
                raise FileNotFoundError(f'output parent dir missing: {outp}')
            if model_name == 'sensevoice':
                info = transcribe_sensevoice(inp, outp, language, device,
                                             enable_speaker=enable_speaker,
                                             punc_enabled=punc_enabled, models=models)
            else:
                info = transcribe_paraformer(inp, outp, model_name, device, vad_enabled,
                                             punc_enabled, enable_speaker=enable_speaker,
                                             model=pf_model)
            ok_count += 1
            entry = {'type': 'item', 'index': idx, 'status': 'ok',
                     'input': inp, 'output': outp,
                     'segments': info.get('segments', 0),
                     'text_length': info.get('text_length', 0),
                     'transcribe_time': round(time.time() - t0, 1),
                     'speaker_data': info.get('speaker_data', [])}
        except (Exception, SystemExit) as e:
            entry = {'type': 'item', 'index': idx, 'status': 'error',
                     'input': inp, 'output': outp, 'error': str(e)[:300]}
        results.append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)

    summary = {'status': 'ok', 'model': model_name, 'device': device,
               'batch': {'total': len(items), 'ok': ok_count,
                         'failed': len(items) - ok_count, 'results': results}}
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def main():
    import argparse

    parser = argparse.ArgumentParser(description='FunASR Portable Worker v2')
    parser.add_argument('--input', default=None, help='输入音视频文件(单文件模式)')
    parser.add_argument('--output', default=None, help='输出 SRT 文件路径(单文件模式)')
    parser.add_argument('--batch-manifest', default=None,
                        help='批处理清单 JSON 路径(items=[{input,output}],model,language,device,speaker,punc,vad)')
    parser.add_argument('--model', default='sensevoice',
                        choices=['sensevoice', 'paraformer-zh', 'paraformer-en', 'paraformer-large-zh'])
    parser.add_argument('--language', default='zh', help='语言代码')
    parser.add_argument('--device', default=None, help='设备 (cuda/cpu)')
    parser.add_argument('--vad', action='store_true', default=True, help='启用 VAD')
    parser.add_argument('--no-vad', dest='vad', action='store_false', help='禁用 VAD')
    parser.add_argument('--punc', action='store_true', default=True, help='启用标点恢复')
    parser.add_argument('--no-punc', dest='punc', action='store_false', help='禁用标点恢复')
    parser.add_argument('--speaker', action='store_true', help='启用说话人分离 (spk_model=cam++)')

    args = parser.parse_args()

    if args.batch_manifest:
        run_batch(args.batch_manifest)
        return

    if not args.input or not args.output:
        parser.error('单文件模式需要 --input/--output，批处理请用 --batch-manifest')

    if not os.path.exists(args.input):
        print(json.dumps({'error': f'Input file not found: {args.input}'}), flush=True)
        sys.exit(1)

    # 设备检测
    device = args.device
    if device is None:
        import torch
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"[Worker] Model: {MODEL_PATHS.get(args.model, '?')}", flush=True)
    print(f"[Worker] Device: {device}", flush=True)

    # 检查 FFmpeg
    ffmpeg_path = os.path.join(ROOT, 'runtime', 'ffmpeg', 'ffmpeg.exe')
    if args.model == 'sensevoice' and not any(
        os.path.exists(p) for p in ['ffmpeg', 'ffmpeg.exe', ffmpeg_path]
    ):
        # 检查是否在 PATH 中
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        except Exception:
            print(json.dumps({
                'error': 'FFmpeg not found. SenseVoice mode requires ffmpeg for audio extraction. '
                         'Please install ffmpeg or use --model paraformer-zh instead.'
            }), flush=True)
            sys.exit(1)

    if args.model == 'sensevoice':
        try:
            info = transcribe_sensevoice(args.input, args.output, args.language, device,
                                         enable_speaker=args.speaker,
                                         punc_enabled=args.punc)
        except ValueError as e:
            # 空结果/无语音:保持原协议,输出 error JSON 后退出
            print(json.dumps({'error': str(e)}), flush=True)
            sys.exit(1)
    else:
        info = transcribe_paraformer(args.input, args.output, args.model, device,
                                     args.vad, args.punc,
                                     enable_speaker=args.speaker)

    result_json = {
        'status': 'ok',
        'model': args.model,
        'device': device,
        'input': args.input,
        'output': args.output,
        'load_time': round(info['load_time'], 1),
        'transcribe_time': round(info['transcribe_time'], 1),
        'segments': info['segments'],
        'text_length': info['text_length'],
        'speaker_data': info.get('speaker_data', []),
    }
    print(json.dumps(result_json, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
