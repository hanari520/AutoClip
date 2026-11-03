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

    @staticmethod
    def _detect_fw_device():
        """faster-whisper 基于 CTranslate2 而非 torch，优先用 ctranslate2 探测。

        解释器里装的是 CPU 版 torch 时，torch.cuda.is_available() 会把
        ctranslate2 实际可用的 GPU 误判成 CPU。
        """
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                return 'cuda'
        except Exception:
            pass
        try:
            import torch
            return 'cuda' if torch.cuda.is_available() else 'cpu'
        except Exception:
            return 'cpu'

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

        if self.device is None:
            self.device = self._detect_fw_device()

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
        try:
            self.model = WhisperModel(
                fw_model,
                device=self.device,
                compute_type=compute_type,
            )
        except Exception as e:
            # CUDA 运行库缺失(cuDNN/cuBLAS)等运行期问题：回退 CPU，不让整个引擎挂掉
            if self.device != 'cuda':
                raise
            print(f"[Whisper-FW] CUDA 加载失败({e})，回退 CPU 重试")
            self.device = 'cpu'
            compute_type = 'int8'
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
                         verbose=False):
        """转录视频为 SRT 字幕(官方推荐配置,2026-11-01 从零重写)

        openai-whisper 路径仅传 language/task/verbose,其余全部使用官方默认值:
        temperature=(0.0..1.0) 回退调度, condition_on_previous_text=True,
        no_speech_threshold=0.6, logprob_threshold=-1.0,
        compression_ratio_threshold=2.4, 无 initial_prompt。
        SRT 直接采用模型原生分段(标点保留),不拆条、不改时间。
        旧版的句子拆分/去标点/无标点引导/收紧阈值等自定义干预已全部移除。

        Args:
            video_path: 视频文件路径
            output_srt_path: 输出 SRT 文件路径，None 则与视频同名
            language: 语言代码，中文为 "zh"
            verbose: 是否打印详细进度

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
        print(f"[Whisper] 语言: {language} (官方默认参数)")

        # ── HuggingFace 格式：用 transformers pipeline 转录 ──
        if self._is_hf_format:
            result = self._transcribe_hf(video_path, language, verbose)
        # ── faster-whisper 格式：Silero VAD + 官方默认阈值 ──
        elif self._is_faster_whisper:
            result = self._transcribe_faster_whisper(video_path, language, verbose)
        # ── OpenAI whisper 格式：官方默认参数 ──
        else:
            result = self.model.transcribe(
                str(video_path), language=language, task='transcribe', verbose=verbose)

        # 生成 SRT(模型原生分段原样使用,标点保留,仅过纠错字典)
        srt_content = self._generate_srt(result["segments"])

        # 写入文件
        with open(output_srt_path, 'w', encoding='utf-8') as f:
            f.write(srt_content)

        print(f"[Whisper] 字幕已保存: {output_srt_path}")
        print(f"[Whisper] 共 {len(result['segments'])} 条字幕片段")

        return str(output_srt_path)

    def _transcribe_faster_whisper(self, video_path, language='zh', verbose=False,
                                   vad_filter=True):
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
            condition_on_previous_text=True,
            vad_filter=vad_filter,     # Silero VAD(fw 路径保留,自动跳过无人声段)
            vad_parameters=vad_parameters if vad_filter else None,
        )
        # 其余参数(beam_size/no_speech_threshold/log_prob_threshold 等)
        # 全部走 faster-whisper 官方默认值

        print(f"[Whisper-FW] 开始转录 (VAD={'ON' if vad_filter else 'OFF'})")
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

    def _generate_srt(self, segments):
        """
        将 Whisper 的原生 segments 转为 SRT 格式(2026-11-01 从零重写)

        官方分段原样使用:不拆条、不按字符比例分时、标点保留;
        仅应用 ASR 纠错字典(项目层功能,与模型配置无关)。

        Args:
            segments: Whisper 转录结果中的 segments 列表

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

        idx = 0
        for seg in segments:
            text = seg["text"].strip()
            if not text:
                continue

            # 应用ASR纠错
            if corrector:
                text = corrector.correct_text(text)
            if not text:
                continue

            idx += 1
            srt_lines.append(f"{idx}")
            srt_lines.append(f"{self._format_time(seg['start'])} --> "
                             f"{self._format_time(seg['end'])}")
            srt_lines.append(text)
            srt_lines.append("")  # 空行分隔

        return "\n".join(srt_lines)

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

    args = parser.parse_args()

    # 初始化转录器
    model_path = args.model
    if model_path is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        model_path = os.path.join(project_root, 'models', 'whisper', 'large-v3-turbo.pt')
    transcriber = WhisperTranscriber(model_path=model_path)

    # 执行转录(官方默认参数)
    if args.auto_lang:
        transcriber.transcribe_with_auto_detect(args.video, args.output, verbose=args.verbose)
    else:
        transcriber.transcribe_video(args.video, args.output,
                                      language=args.language, verbose=args.verbose)


if __name__ == "__main__":
    main()
