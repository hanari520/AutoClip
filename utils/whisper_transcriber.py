# -*- coding: utf-8 -*-
"""
Whisper 字幕识别模块
自动将视频转为 SRT 字幕，支持 large-v3-turbo 模型
"""

import os
import sys
import whisper
import subprocess
from pathlib import Path

# 导入ASR纠错器
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from ASRCorrector import FileBasedCorrector
    ASR_CORRECTOR_AVAILABLE = True
except ImportError:
    ASR_CORRECTOR_AVAILABLE = False


class WhisperTranscriber:
    """基于 Whisper 的音频转录器

    支持三种后端：
      1. faster-whisper（.pt 模型，原生 Silero VAD，推荐）
      2. openai-whisper（.pt 模型，无 Silero VAD，兼容回退）
      3. HuggingFace transformers（HF 目录格式，如 BELLE-2，无 VAD）

    自动检测：model_path 是目录且含 config.json → 走 transformers 分支
    """

    def __init__(self, model_path=None, device=None, use_faster_whisper=False):
        """
        Args:
            model_path: 模型路径，两种格式：
                        - .pt 文件路径，如 "models/whisper/large-v3-turbo.pt"
                        - HF 目录路径，如 "models/whisper/belle-whisper-large-v3-turbo-zh"
                        为 None 时自动下载 large-v3-turbo
            device: 计算设备，None 为自动选择 (cuda/cpu)
            use_faster_whisper: 是否优先用 faster-whisper（原生 Silero VAD）。
                               False 则回退到 openai-whisper。
                               仅对 .pt 格式有效，HF 目录格式始终走 transformers。
        """
        self.model_path = model_path
        self.device = device
        self.use_faster_whisper = use_faster_whisper
        self.model = None
        self._is_hf_format = False  # 标记是否 HF transformers 格式
        self._is_faster_whisper = False  # 标记是否 faster-whisper 后端
        self._hf_tokenizer = None
        self._hf_feature_extractor = None
        self._hf_forced_decoder_ids = None  # HF 模型的 forced_decoder_ids
        self._load_model()

    def _load_model(self):
        """加载 Whisper 模型（自动识别 .pt / HF 目录 / faster-whisper）"""
        print("[Whisper] 正在加载模型...")

        # ── 检测 HuggingFace transformers 格式（目录 + config.json）──
        if self.model_path and os.path.isdir(self.model_path) \
                and os.path.exists(os.path.join(self.model_path, 'config.json')):
            self._load_hf_model()
            return

        # ── .pt 格式：优先 faster-whisper（有 Silero VAD），回退 openai-whisper ──
        if self.use_faster_whisper:
            try:
                self._load_faster_whisper_model()
                return
            except Exception as e:
                print(f"[Whisper] faster-whisper 加载失败: {e}")
                print(f"[Whisper] 回退到 openai-whisper")

        # ── OpenAI whisper .pt 格式 ──
        if self.model_path and os.path.exists(self.model_path):
            print(f"[Whisper] 本地模型: {self.model_path}")
            self.model = whisper.load_model(self.model_path, device=self.device)
        else:
            if self.model_path:
                print(f"[Whisper] 模型路径不存在: {self.model_path}，尝试自动加载")
            self.model = whisper.load_model("large-v3-turbo", device=self.device)

        print("[Whisper] 模型加载完成 (openai-whisper)")

    def _load_faster_whisper_model(self):
        """用 faster-whisper 加载 .pt 模型（原生 Silero VAD 支持）

        faster-whisper 基于 CTranslate2，比 openai-whisper 快 2-4 倍，
        且内置 Silero VAD 人声检测，可自动跳过无人说话的音频段。

        注意：faster-whisper 不能直接读 .pt 文件，需要 CTranslate2 格式。
        策略：从 .pt 文件名提取模型名（如 large-v3-turbo.pt → large-v3-turbo），
        让 faster-whisper 自动下载 CTranslate2 格式到其缓存目录。
        下载只需一次，后续从缓存加载。
        """
        from faster_whisper import WhisperModel
        import torch

        if self.device is None:
            self.device = 'cuda' if torch.cuda.is_available() else 'cpu'

        # faster-whisper 的 compute_type：
        #   cuda → float16（速度优先，精度足够）
        #   cpu  → int8（CPU 上唯一实用选项）
        compute_type = 'float16' if self.device == 'cuda' else 'int8'

        # 从 .pt 路径提取模型名，或直接用传入的名称
        fw_model = self.model_path
        if fw_model and os.path.isfile(fw_model) and fw_model.endswith('.pt'):
            # 从文件名提取模型名：large-v3-turbo.pt → large-v3-turbo
            fw_model = os.path.splitext(os.path.basename(fw_model))[0]
            print(f"[Whisper-FW] 从 .pt 路径提取模型名: {fw_model}")
        elif not fw_model:
            fw_model = 'large-v3-turbo'

        print(f"[Whisper-FW] faster-whisper 加载: {fw_model}")
        print(f"[Whisper-FW] 设备: {self.device}, compute_type: {compute_type}")
        t0 = __import__('time').time()

        # faster-whisper 首次使用某模型名时会自动下载 CTranslate2 格式到缓存
        # 后续从 ~/.cache/huggingface 加载，不再下载
        self.model = WhisperModel(
            fw_model,
            device=self.device,
            compute_type=compute_type,
        )
        self._is_faster_whisper = True
        load_time = __import__('time').time() - t0
        print(f"[Whisper-FW] 模型加载完成 ({load_time:.1f}s, Silero VAD 可用)")

    def _load_hf_model(self):
        """加载 HuggingFace transformers 格式的 Whisper 模型

        用于 BELLE-2/Belle-whisper-large-v3-turbo-zh 等微调模型。
        用 WhisperForConditionalGeneration + WhisperTokenizer + WhisperFeatureExtractor。

        兼容性：旧版 transformers（4.24）的 WhisperProcessor.from_pretrained 会因
        added_tokens 索引校验报错，改为分别加载 Tokenizer 和 FeatureExtractor 绕过。
        且旧版没有 get_decoder_prompt_ids 方法，用 convert_tokens_to_ids 动态查 token ID。
        """
        print(f"[Whisper-HF] 加载 HuggingFace 格式模型: {self.model_path}")
        try:
            from transformers import (
                WhisperForConditionalGeneration,
                WhisperTokenizer,
                WhisperFeatureExtractor,
            )
        except ImportError:
            raise RuntimeError(
                "加载 HuggingFace 格式 Whisper 模型需要 transformers 库\n"
                "请安装: pip install transformers"
            )

        import torch
        if self.device is None:
            self.device = 'cuda' if torch.cuda.is_available() else 'cpu'

        print(f"[Whisper-HF] 设备: {self.device}")
        t0 = __import__('time').time()

        # 分别加载 tokenizer 和 feature_extractor（绕过 WhisperProcessor 的 added_tokens 校验）
        self._hf_tokenizer = WhisperTokenizer.from_pretrained(self.model_path)
        self._hf_feature_extractor = WhisperFeatureExtractor.from_pretrained(self.model_path)
        self.model = WhisperForConditionalGeneration.from_pretrained(self.model_path).to(self.device)

        # ── 构建 forced_decoder_ids（强制中文转录）──
        # forced_decoder_ids 格式: [(position, token_id), ...]
        #   position 1 → <|zh|> (语言标记)
        #   position 2 → <|transcribe|> (任务标记)
        #   position 3 → <|notimestamps|> (关闭时间戳)
        # 用 convert_tokens_to_ids 动态查 ID（不硬编码，兼容不同 tokenizer 版本）
        zh_id = self._hf_tokenizer.convert_tokens_to_ids('<|zh|>')
        transcribe_id = self._hf_tokenizer.convert_tokens_to_ids('<|transcribe|>')
        notimestamps_id = self._hf_tokenizer.convert_tokens_to_ids('<|notimestamps|>')
        self._hf_forced_decoder_ids = [
            (1, zh_id),
            (2, transcribe_id),
            (3, notimestamps_id),
        ]
        print(f"[Whisper-HF] forced_decoder_ids: {self._hf_forced_decoder_ids}")
        print(f"  <|zh|>={zh_id}, <|transcribe|>={transcribe_id}, <|notimestamps|>={notimestamps_id}")

        # 同时设置到 config（部分代码可能从 config 读取）
        self.model.config.forced_decoder_ids = self._hf_forced_decoder_ids

        self._is_hf_format = True
        load_time = __import__('time').time() - t0
        print(f"[Whisper-HF] 模型加载完成 ({load_time:.1f}s)")
    
    def transcribe_video(self, video_path, output_srt_path=None, language="zh",
                         verbose=False, condition_on_previous_text=True,
                         split_sentences=True, max_segment_duration=8.0,
                         no_speech_threshold=0.7, logprob_threshold=-0.5,
                         initial_prompt=None, strip_punctuation=True,
                         vad_filter=True):
        """
        转录视频为 SRT 字幕

        Args:
            video_path: 视频文件路径
            output_srt_path: 输出 SRT 文件路径，None 则与视频同名
            language: 语言代码，中文为 "zh"
            verbose: 是否打印详细进度
            condition_on_previous_text: 是否基于前文条件生成
            split_sentences: 是否按句号拆分长句，让字幕逐句显示（默认开启）
            max_segment_duration: 超过此秒数的 segment 会被拆分（默认 8 秒）
            no_speech_threshold: 无人说话判定阈值（默认 0.7）。
                                 当模型判定当前音频段无人说话的概率 > 此值时，直接舍弃该段。
                                 faster-whisper 和 openai-whisper 都支持。
                                 值越高越严格（更倾向保留），值越低越宽松（更容易丢弃）。
                                 0.7 = 较为激进地丢弃静音段，减少无意义转写。
            logprob_threshold: 低信心强制切分阈值，越高切得越碎（Whisper 默认 -1.0，这里提到 -0.5）
            initial_prompt: Whisper 初始提示文本，用于引导输出风格（如无标点简体中文）
            strip_punctuation: 是否移除所有标点符号（中文默认 True，让字幕更干净）
            vad_filter: 是否启用 Silero VAD 人声检测（仅 faster-whisper 有效）。
                        开启后自动跳过无人说话的音频段，避免产生幻觉转写。

        Returns:
            str: 生成的 SRT 文件路径
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        # 默认输出路径
        if output_srt_path is None:
            output_srt_path = video_path.with_suffix('.srt')
        else:
            output_srt_path = Path(output_srt_path)

        print(f"[Whisper] 开始识别字幕: {video_path.name}")
        print(f"[Whisper] 语言: {language}")
        print(f"[Whisper] no_speech_threshold: {no_speech_threshold}")
        if self._is_faster_whisper and vad_filter:
            print(f"[Whisper] Silero VAD: 已启用（自动跳过无人说话的音频段）")
        if strip_punctuation:
            print(f"[Whisper] 标点模式: 自动移除标点符号")

        # 默认中文 initial_prompt：引导无标点、逐句分行的输出风格
        # 注意：HF 格式模型（BELLE-2）自带中文微调，不需要 initial_prompt
        if initial_prompt is None and language == 'zh' and not self._is_hf_format:
            initial_prompt = (
                "以下是简体中文普通话的转录内容"
                "不使用任何标点符号"
                "每句话独立显示为一行"
            )
            print("[Whisper] 注入 initial_prompt (无标点简体中文)")

        # ── HuggingFace 格式：用 transformers pipeline 转录 ──
        if self._is_hf_format:
            result = self._transcribe_hf(video_path, language, verbose)
        # ── faster-whisper 格式：用 Silero VAD + no_speech_threshold ──
        elif self._is_faster_whisper:
            result = self._transcribe_faster_whisper(
                video_path, language, verbose, condition_on_previous_text,
                no_speech_threshold, logprob_threshold, initial_prompt, vad_filter
            )
        # ── OpenAI whisper 格式 ──
        else:
            transcribe_kwargs = {
                'language': language,
                'verbose': verbose,
                'condition_on_previous_text': condition_on_previous_text,
                'task': 'transcribe',
                'no_speech_threshold': no_speech_threshold,
                'logprob_threshold': logprob_threshold,
            }
            if initial_prompt:
                transcribe_kwargs['initial_prompt'] = initial_prompt
            result = self.model.transcribe(str(video_path), **transcribe_kwargs)
        
        # 生成 SRT 内容（开启句子拆分）
        srt_content = self._generate_srt(
            result["segments"],
            split_sentences=split_sentences,
            max_segment_duration=max_segment_duration,
            strip_punctuation=strip_punctuation,
        )
        
        # 写入文件
        with open(output_srt_path, 'w', encoding='utf-8') as f:
            f.write(srt_content)
        
        print(f"[Whisper] 字幕已保存: {output_srt_path}")
        print(f"[Whisper] 共 {len(result['segments'])} 条字幕片段")
        
        return str(output_srt_path)

    def _transcribe_faster_whisper(self, video_path, language='zh', verbose=False,
                                    condition_on_previous_text=True,
                                    no_speech_threshold=0.7, logprob_threshold=-0.5,
                                    initial_prompt=None, vad_filter=True):
        """用 faster-whisper 转录（原生 Silero VAD 人声检测）

        faster-whisper 的 transcribe 返回 (segments_generator, info)，
        segments 是生成器需转 list，每段有 .start/.end/.text 属性。
        转换为 openai-whisper 兼容格式返回。

        Silero VAD 工作原理：
          1. 先用 Silero 模型检测哪些时间段有人说话
          2. 只对有人说话的段做 Whisper 转录
          3. no_speech_threshold 是 Whisper 层面的二次过滤：
             即使 VAD 认为有声音，如果 Whisper 判定"无人说话"的概率 > 阈值，
             也会丢弃该段（避免背景音乐/噪音产生幻觉转写）
        """
        import time as _time

        # Silero VAD 参数
        vad_parameters = dict(
            threshold=0.5,             # VAD 判定阈值（0-1，越低越敏感）
            min_silence_duration_ms=500,  # 最小静音时长才切分
            speech_pad_ms=200,         # 语音前后补充的 padding
        )

        transcribe_kwargs = dict(
            language=language,
            task='transcribe',
            beam_size=5,
            condition_on_previous_text=condition_on_previous_text,
            no_speech_threshold=no_speech_threshold,  # 0.7: 丢弃高概率无人说话的段
            log_prob_threshold=logprob_threshold,
            vad_filter=vad_filter,     # ← Silero VAD 开关
            vad_parameters=vad_parameters if vad_filter else None,
        )
        if initial_prompt:
            transcribe_kwargs['initial_prompt'] = initial_prompt

        print(f"[Whisper-FW] 开始转录 (VAD={'ON' if vad_filter else 'OFF'}, no_speech={no_speech_threshold})")
        t0 = _time.time()

        # faster-whisper 可以直接接受视频文件路径（内部用 ffmpeg 提取音频）
        segments_gen, info = self.model.transcribe(str(video_path), **transcribe_kwargs)

        # 转换生成器为列表，同时收集进度
        all_segments = []
        full_text_parts = []
        for seg in segments_gen:
            all_segments.append({
                'id': len(all_segments),
                'start': seg.start,
                'end': seg.end,
                'text': seg.text.strip(),
            })
            full_text_parts.append(seg.text.strip())
            if verbose or (len(all_segments) % 20 == 0):
                print(f"  [{len(all_segments)}] {seg.start:.1f}s-{seg.end:.1f}s: {seg.text.strip()[:60]}")

        transcribe_time = _time.time() - t0
        result = {
            'text': ' '.join(full_text_parts),
            'segments': all_segments,
        }
        print(f"[Whisper-FW] 转录完成: {len(all_segments)} 段, 耗时 {transcribe_time:.1f}s")
        if vad_filter:
            # info 里可能有 VAD 跳过的段数统计
            print(f"[Whisper-FW] 语言检测: {info.language} (置信度: {info.language_probability:.2f})")
            if hasattr(info, 'duration_after_vad'):
                vad_duration = info.duration_after_vad
                total_duration = info.duration
                skipped = total_duration - vad_duration
                if skipped > 0:
                    print(f"[Whisper-FW] VAD 跳过静音: {skipped:.1f}s / {total_duration:.1f}s "
                          f"({skipped/total_duration*100:.0f}%)")

        return result

    def _transcribe_hf(self, video_path, language='zh', verbose=False):
        """用 HuggingFace transformers 模型转录（BELLE-2 等微调模型）

        WhisperForConditionalGeneration 不支持直接读视频文件，
        需要先提取音频，再用 processor 编码，最后 generate。
        返回格式与 openai-whisper 兼容：{"text": str, "segments": [...]}
        """
        import subprocess as _sp
        import tempfile as _tf
        import torch

        # 1. FFmpeg 提取 16kHz 单声道 PCM WAV
        tmp_wav = _tf.NamedTemporaryFile(suffix='.wav', delete=False).name
        print(f"[Whisper-HF] 提取音频 → {os.path.basename(tmp_wav)}")
        _sp.run([
            'ffmpeg', '-y', '-v', 'quiet',
            '-i', str(video_path),
            '-vn', '-ac', '1', '-ar', '16000',
            '-acodec', 'pcm_s16le', tmp_wav,
        ], capture_output=True)

        # 2. 加载音频
        import librosa
        audio, sr = librosa.load(tmp_wav, sr=16000)
        os.unlink(tmp_wav)

        # 3. 分块处理（Whisper 30秒窗口）
        chunk_sec = 30
        chunk_len = chunk_sec * sr
        chunks = [(i, min(i + chunk_len, len(audio))) for i in range(0, len(audio), chunk_len)]
        print(f"[Whisper-HF] 音频长度 {len(audio)/sr:.1f}s，分 {len(chunks)} 块处理")

        all_segments = []
        full_text_parts = []

        for idx, (start, end) in enumerate(chunks):
            chunk_audio = audio[start:end]
            t_start = start / sr
            t_end = end / sr

            # 编码 + 生成（用 tokenizer + feature_extractor 代替 processor）
            input_features = self._hf_feature_extractor(
                chunk_audio, sampling_rate=sr, return_tensors="pt"
            ).input_features.to(self.device)

            # 生成文本 — 显式传 forced_decoder_ids（旧版 transformers 不从 config 读）
            with torch.no_grad():
                predicted_ids = self.model.generate(
                    input_features,
                    forced_decoder_ids=self._hf_forced_decoder_ids,
                    max_new_tokens=440,
                    num_beams=1,
                    do_sample=False,
                )

            text = self._hf_tokenizer.batch_decode(predicted_ids, skip_special_tokens=True)[0]

            if text.strip():
                full_text_parts.append(text)
                all_segments.append({
                    'id': idx,
                    'start': t_start,
                    'end': t_end,
                    'text': text.strip(),
                })
                if verbose or (idx + 1) % 5 == 0:
                    print(f"  [{idx+1}/{len(chunks)}] {t_start:.0f}s: {text.strip()[:50]}")

        return {
            'text': ' '.join(full_text_parts),
            'segments': all_segments,
        }

    def _generate_srt(self, segments, split_sentences=True, max_segment_duration=8.0,
                       strip_punctuation=True):
        """
        将 Whisper 的 segments 转为 SRT 格式，并应用ASR纠错
        支持按标点拆分长 segment，让字幕逐句显示

        Args:
            segments: Whisper 转录结果中的 segments 列表
            split_sentences: 是否按句号/问号/感叹号拆分长句（默认开启）
            max_segment_duration: 超过此秒数的 segment 会被拆分（默认8秒）
            strip_punctuation: 是否移除所有标点符号（默认 True，中文字幕更干净）

        Returns:
            str: SRT 格式字符串
        """
        srt_lines = []

        # 初始化纠错器（如果可用）
        corrector = None
        if ASR_CORRECTOR_AVAILABLE:
            try:
                corrector = FileBasedCorrector()
            except Exception as e:
                print(f"[ASR纠错] 初始化失败: {e}")

        # 先展开 segments：如果开启拆分，按句子断开
        expanded_segments = []
        for segment in segments:
            text = segment["text"].strip()
            if not text:
                continue
            duration = segment["end"] - segment["start"]

            if split_sentences and (duration > max_segment_duration or self._has_multiple_sentences(text)):
                sub_segments = self._split_segment_by_sentence(
                    text, segment["start"], segment["end"]
                )
                expanded_segments.extend(sub_segments)
            else:
                expanded_segments.append({
                    "start": segment["start"],
                    "end": segment["end"],
                    "text": text,
                })

        idx = 0
        for seg in expanded_segments:
            idx += 1
            text = seg["text"]

            # 应用ASR纠错
            if corrector:
                text = corrector.correct_text(text)
                if text != seg["text"]:
                    seg["text"] = text

            # 移除标点符号（中文标点 + 英文标点）
            if strip_punctuation:
                text = seg["text"]
                text = self._strip_all_punctuation(text)
                seg["text"] = text

            # 格式化时间
            start_srt = self._format_time(seg["start"])
            end_srt = self._format_time(seg["end"])

            srt_lines.append(f"{idx}")
            srt_lines.append(f"{start_srt} --> {end_srt}")
            srt_lines.append(text)
            srt_lines.append("")  # 空行分隔

        return "\n".join(srt_lines)

    @staticmethod
    def _has_multiple_sentences(text):
        """检查文本是否包含多个句子（有 。！？?! 等句末标点）"""
        import re
        return bool(re.search(r'[。！？?!]', text))

    @staticmethod
    def _strip_all_punctuation(text):
        """
        移除所有中文和英文标点符号，只保留中文汉字、字母、数字和空格
        先标点拆分再逐段去标点，最后用空格拼接，保证每句话独立
        """
        import re
        import string
        # 先按中文句末标点拆成短句
        parts = re.split(r'(?<=[。！？?!])', text)
        # 对每段去标点
        clean_parts = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            # 移除中文标点：Unicode CJK标点范围 + 全角标点
            p = re.sub(r'[\u3000-\u303f\uff00-\uffef]', '', p)
            # 移除英文标点（保留字母数字和空格）
            en_punc = string.punctuation
            p = p.translate(str.maketrans('', '', en_punc))
            p = p.strip()
            if p:
                clean_parts.append(p)
        return ' '.join(clean_parts) if clean_parts else text

    @staticmethod
    def _split_segment_by_sentence(text, seg_start, seg_end):
        """
        按中文句末标点拆分文本为多个子 segment，时间按字符数比例分配

        Args:
            text: 原始文本
            seg_start: 原始 segment 起始时间（秒）
            seg_end: 原始 segment 结束时间（秒）

        Returns:
            list[dict]: [{"start": float, "end": float, "text": str}, ...]
        """
        import re

        total_duration = seg_end - seg_start
        if total_duration <= 0:
            return [{"start": seg_start, "end": seg_end, "text": text}]

        # 按 。！？?! 拆分，保留标点在句子末尾
        parts = re.split(r'(?<=[。！？?!])', text)
        # 过滤空字符串
        parts = [p.strip() for p in parts if p.strip()]

        if len(parts) <= 1:
            # 没有句末标点但 duration 很长时，尝试按逗号拆分
            parts = re.split(r'(?<=[，,；;])', text)
            parts = [p.strip() for p in parts if p.strip()]

        if len(parts) <= 1:
            return [{"start": seg_start, "end": seg_end, "text": text}]

        # 按字符数比例分配时间
        total_chars = sum(len(p) for p in parts)
        if total_chars == 0:
            return [{"start": seg_start, "end": seg_end, "text": text}]

        results = []
        current_time = seg_start
        for p in parts:
            duration = total_duration * (len(p) / total_chars)
            # 最小 0.5 秒、最大不超过剩余时间
            duration = max(0.5, duration)
            sub_end = min(current_time + duration, seg_end)
            results.append({
                "start": current_time,
                "end": sub_end,
                "text": p,
            })
            current_time = sub_end

        return results
    
    def _format_time(self, seconds):
        """
        将秒数转为 SRT 时间格式 (HH:MM:SS,mmm)
        
        Args:
            seconds: 秒数
            
        Returns:
            str: "HH:MM:SS,mmm"
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    
    def transcribe_with_auto_detect(self, video_path, output_srt_path=None, verbose=False):
        """
        自动检测语言并转录（适合多语言视频）
        
        Args:
            video_path: 视频文件路径
            output_srt_path: 输出 SRT 文件路径
            verbose: 是否打印详细进度
            
        Returns:
            str: 生成的 SRT 文件路径
        """
        video_path = Path(video_path)
        
        if output_srt_path is None:
            output_srt_path = video_path.with_suffix('.srt')
        
        print(f"[Whisper] 开始识别字幕 (自动语言检测): {video_path.name}")
        
        # 加载音频并检测语言
        audio = whisper.load_audio(str(video_path))
        mel = whisper.log_mel_spectrogram(audio).to(self.model.device)
        
        # 检测语言
        _, probs = self.model.detect_language(mel)
        detected_lang = max(probs, key=probs.get)
        print(f"[Whisper] 检测到语言: {detected_lang} (置信度: {probs[detected_lang]:.2%})")
        
        # 使用检测到的语言进行转录
        return self.transcribe_video(
            video_path, 
            output_srt_path, 
            language=detected_lang,
            verbose=verbose
        )


def extract_audio_to_wav(video_path, output_wav_path):
    """
    使用 FFmpeg 从视频提取音频为 WAV 格式
    （备用方案，Whisper 内部已处理，一般不需要手动调用）
    
    Args:
        video_path: 视频文件路径
        output_wav_path: 输出 WAV 文件路径
    """
    cmd = [
        'ffmpeg', '-i', str(video_path),
        '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
        '-y', str(output_wav_path)
    ]
    
    print(f"[FFmpeg] 提取音频: {video_path.name}")
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    if Path(output_wav_path).exists():
        print(f"[FFmpeg] 音频已提取: {output_wav_path}")
        return True
    else:
        print(f"[FFmpeg] 音频提取失败")
        return False


def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Whisper 字幕识别工具')
    parser.add_argument('video', help='输入视频文件路径')
    parser.add_argument('-o', '--output', help='输出 SRT 文件路径 (默认与视频同名)')
    parser.add_argument('-m', '--model', default=None,
                        help='Whisper 模型路径（默认: models/whisper/large-v3-turbo.pt）')
    parser.add_argument('-l', '--language', default='zh',
                        help='语言代码 (zh/en/ja 等，默认自动检测)')
    parser.add_argument('--auto-lang', action='store_true',
                        help='启用自动语言检测')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='显示详细进度')
    parser.add_argument('--no-split', action='store_true',
                        help='禁用长句拆分（默认会按句号自动拆分）')
    parser.add_argument('--max-sec', type=float, default=8.0,
                        help='拆分阈值秒数，超过此长度的segment会被拆分（默认8秒）')
    parser.add_argument('--noise-threshold', type=float, default=None,
                        help='VAD 静音阈值 (0-1, 越低对停顿越敏感, 默认 0.35)')
    parser.add_argument('--logprob-threshold', type=float, default=None,
                        help='低信心切分阈值 (越高切越碎, 默认 -0.5, Whisper内置 -1.0)')
    parser.add_argument('--initial-prompt', type=str, default=None,
                        help='Whisper 初始提示文本（中文默认自动注入无标点引导）')
    parser.add_argument('--strip-punctuation', action='store_true', default=True,
                        help='移除字幕中的标点符号（中文默认开启）')
    parser.add_argument('--keep-punctuation', action='store_true',
                        help='保留标点符号（覆盖 --strip-punctuation 默认值）')
    
    args = parser.parse_args()
    
    # 初始化转录器
    model_path = args.model
    if model_path is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        model_path = os.path.join(project_root, 'models', 'whisper', 'large-v3-turbo.pt')
    transcriber = WhisperTranscriber(model_path=model_path)
    
    # 执行转录
    split = not args.no_split
    strip_punc = args.strip_punctuation and not args.keep_punctuation
    # 构建 kwargs，CLI 不传就用默认值
    kw = {
        'split_sentences': split,
        'max_segment_duration': args.max_sec,
        'strip_punctuation': strip_punc,
    }
    if args.noise_threshold is not None:
        kw['no_speech_threshold'] = args.noise_threshold
    if args.logprob_threshold is not None:
        kw['logprob_threshold'] = args.logprob_threshold
    if args.initial_prompt is not None:
        kw['initial_prompt'] = args.initial_prompt

    if args.auto_lang:
        transcriber.transcribe_with_auto_detect(args.video, args.output, verbose=args.verbose)
    else:
        transcriber.transcribe_video(args.video, args.output, 
                                      language=args.language, verbose=args.verbose,
                                      **kw)


if __name__ == "__main__":
    main()
