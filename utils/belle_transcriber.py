# -*- coding: utf-8 -*-
"""
Belle-whisper-large-v3-turbo-zh 独立识别模块
基于 faster-whisper + CTranslate2，专为 Belle 中文微调模型设计。
与其他 Whisper 引擎完全独立，不影响现有 whisper_transcriber.py。

核心设计：
  - 子进程模式：anaconda Python 3.10 的 ctranslate2 加载模型会 segfault，
    用 managed Python 3.13 子进程执行转录，主进程负责 SRT 生成 + ASR 纠错
  - Silero VAD 人声检测
  - 中文专用
"""

import os
import sys
import re
import json
import time
import string
import subprocess
from pathlib import Path

# ── ASR 纠错器 ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from ASRCorrector import FileBasedCorrector
    _ASR_CORRECTOR_AVAILABLE = True
except ImportError:
    _ASR_CORRECTOR_AVAILABLE = False

# ── 路径 ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MODEL_PATH = os.path.join(
    _PROJECT_ROOT, 'models', 'whisper', 'belle-whisper-large-v3-turbo-zh-ct2'
)

# managed Python 3.13（ctranslate2 兼容）；可用环境变量 AUTOCLIP_MANAGED_PYTHON 指定，
# 未指定时回退当前解释器（此时需在当前环境安装 faster-whisper/ctranslate2）
_MANAGED_PYTHON = os.environ.get('AUTOCLIP_MANAGED_PYTHON', '') or sys.executable


class BelleTranscriber:
    """Belle-whisper-large-v3-turbo-zh 中文语音识别器"""

    def __init__(self, model_path=None, device=None):
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self.device = device

        if not os.path.isdir(self.model_path):
            raise FileNotFoundError(
                f"Belle 模型目录不存在: {self.model_path}\n"
                f"请确保已下载模型并转换为 CTranslate2 格式。"
            )

    def transcribe_video(self, video_path, output_srt_path=None,
                         verbose=False, **kwargs):
        """
        转录视频为 SRT 字幕

        Args:
            video_path: 视频文件路径
            output_srt_path: 输出 SRT 路径，None 则与视频同名
            verbose: 是否打印详细进度

        Returns:
            str: 生成的 SRT 文件路径
        """
        video_path = str(Path(video_path))
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        if output_srt_path is None:
            output_srt_path = os.path.splitext(video_path)[0] + '.srt'

        # 通过 managed Python 子进程执行转录
        segments = self._run_transcribe(video_path, verbose)

        # 主进程生成 SRT（含 ASR 纠错 + 去标点）
        srt_content = self._build_srt(segments)
        with open(output_srt_path, 'w', encoding='utf-8') as f:
            f.write(srt_content)

        print(f"[Belle] 字幕已保存: {output_srt_path}")
        return output_srt_path

    # ═══════════════════════════════════════════════════════
    # 子进程转录
    # ═══════════════════════════════════════════════════════

    def _run_transcribe(self, video_path, verbose=False):
        """用 managed Python 子进程执行 faster-whisper 转录，返回 segments 列表"""

        # 写临时脚本文件（避免命令行转义问题）
        import tempfile
        script_content = self._build_subprocess_script(video_path, verbose)
        script_file = os.path.join(tempfile.gettempdir(), '_belle_transcribe.py')
        with open(script_file, 'w', encoding='utf-8') as f:
            f.write(script_content)

        json_output = video_path + '.belle.json'

        print(f"[Belle] 开始识别: {os.path.basename(video_path)}")
        t0 = time.time()

        proc = subprocess.run(
            [_MANAGED_PYTHON, '-u', script_file, video_path, json_output],
            capture_output=True, text=True, timeout=3600,
            encoding='utf-8', errors='replace',
        )

        # 打印子进程输出
        if proc.stdout:
            print(proc.stdout, end='')
        if proc.stderr:
            for line in proc.stderr.split('\n'):
                line = line.strip()
                if line and 'UserWarning' not in line and 'bottleneck' not in line:
                    print(f"  [stderr] {line}")

        # 清理临时脚本
        try:
            os.unlink(script_file)
        except OSError:
            pass

        if proc.returncode != 0:
            raise RuntimeError(f"Belle 子进程退出码: {proc.returncode}")

        # 读取 JSON 结果
        if not os.path.exists(json_output):
            raise RuntimeError("子进程未输出 JSON 结果")

        with open(json_output, 'r', encoding='utf-8') as f:
            result = json.load(f)
        os.unlink(json_output)

        elapsed = time.time() - t0
        seg_count = len(result.get('segments', []))
        print(f"[Belle] 转录完成: {seg_count} 段, 耗时 {elapsed:.1f}s")

        return result.get('segments', [])

    def _build_subprocess_script(self, video_path, verbose):
        """生成子进程脚本内容"""
        return f'''# -*- coding: utf-8 -*-
import sys, os, json, time
sys.stdout.reconfigure(encoding='utf-8')

from faster_whisper import WhisperModel
import torch

MODEL_PATH = r"{self.model_path}"
video_path = sys.argv[1]
json_output = sys.argv[2]
verbose = {'True' if verbose else 'False'}

device = 'cuda' if torch.cuda.is_available() else 'cpu'
compute_type = 'int8_float16' if device == 'cuda' else 'int8'

print(f"[Belle] 加载模型 (设备: {{device}}, compute: {{compute_type}})")
t0 = time.time()

try:
    model = WhisperModel(MODEL_PATH, device=device, compute_type=compute_type)
except Exception as e:
    if compute_type == 'int8_float16':
        print(f"[Belle] int8_float16 失败 ({{e}})，回退到 int8")
        compute_type = 'int8'
        model = WhisperModel(MODEL_PATH, device=device, compute_type=compute_type)
    else:
        raise

print(f"[Belle] 模型加载完成 ({{time.time()-t0:.1f}}s)")

vad_params = dict(threshold=0.5, min_silence_duration_ms=500, speech_pad_ms=200)

t1 = time.time()
segments_gen, info = model.transcribe(
    video_path,
    language='zh',
    task='transcribe',
    beam_size=5,
    condition_on_previous_text=True,
    no_speech_threshold=0.7,
    log_prob_threshold=-0.5,
    vad_filter=True,
    vad_parameters=vad_params,
)

all_segments = []
for seg in segments_gen:
    text = seg.text.strip()
    all_segments.append({{"start": round(seg.start, 3), "end": round(seg.end, 3), "text": text}})
    if verbose or len(all_segments) % 20 == 0:
        print(f"  [{{len(all_segments)}}] {{seg.start:.1f}}s-{{seg.end:.1f}}s: {{text[:60]}}")

elapsed = time.time() - t1
print(f"[Belle] 转录: {{len(all_segments)}} 段, {{elapsed:.1f}}s")
print(f"[Belle] 语言: {{info.language}} ({{info.language_probability:.2f}})")

result = {{"segments": all_segments}}
with open(json_output, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False)
'''

    # ═══════════════════════════════════════════════════════
    # SRT 生成
    # ═══════════════════════════════════════════════════════

    def _build_srt(self, segments):
        """
        将 segments 转为 SRT 字符串

        流程：
          1. ASR 纠错（保留原始标点供纠错器匹配）
          2. 按句末标点拆分长句
          3. 去标点
          4. 过滤空文本
          5. 输出 SRT
        """
        # 初始化纠错器
        corrector = None
        if _ASR_CORRECTOR_AVAILABLE:
            try:
                corrector = FileBasedCorrector()
            except Exception as e:
                print(f"[Belle] ASR纠错初始化失败: {e}")

        # 处理每个 segment
        all_subs = []
        for seg in segments:
            text = seg['text'].strip()
            if not text:
                continue

            start = seg['start']
            end = seg['end']

            # 1. ASR 纠错
            if corrector:
                text = corrector.correct_text(text).strip()
                if not text:
                    continue

            # 2. 按句末标点拆分
            sub_parts = self._split_by_punctuation(text, start, end)

            for part in sub_parts:
                p_text = part['text'].strip()
                if not p_text:
                    continue

                # 3. 去标点
                p_text = self._remove_punctuation(p_text)
                if not p_text:
                    continue

                # 4. 收集
                all_subs.append({
                    'start': part['start'],
                    'end': part['end'],
                    'text': p_text,
                })

        # 5. 输出 SRT
        lines = []
        for i, sub in enumerate(all_subs, 1):
            lines.append(str(i))
            lines.append(f"{self._fmt_time(sub['start'])} --> {self._fmt_time(sub['end'])}")
            lines.append(sub['text'])
            lines.append('')

        return '\n'.join(lines)

    @staticmethod
    def _split_by_punctuation(text, start, end):
        """按中文句末标点（。！？?!）拆分，时间按字符比例分配。
        如果没有句末标点，尝试按逗号拆分。都没有则返回整体。"""
        total_dur = end - start
        if total_dur <= 0:
            return [{'start': start, 'end': end, 'text': text}]

        # 按句末标点拆
        parts = re.split(r'(?<=[。！？?!])', text)
        parts = [p.strip() for p in parts if p.strip()]

        # 没拆开，试逗号
        if len(parts) <= 1:
            parts = re.split(r'(?<=[，,；;])', text)
            parts = [p.strip() for p in parts if p.strip()]

        # 还是没拆开，返回整体
        if len(parts) <= 1:
            return [{'start': start, 'end': end, 'text': text}]

        # 按字符比例分配时间
        total_chars = sum(len(p) for p in parts)
        if total_chars == 0:
            return [{'start': start, 'end': end, 'text': text}]

        results = []
        cur = start
        for p in parts:
            dur = total_dur * (len(p) / total_chars)
            dur = max(0.5, dur)
            sub_end = min(cur + dur, end)
            results.append({'start': cur, 'end': sub_end, 'text': p})
            cur = sub_end

        return results

    @staticmethod
    def _remove_punctuation(text):
        """移除所有中文和英文标点符号"""
        # 中文标点（含全角）
        text = re.sub(r'[\u3000-\u303f\uff00-\uffef]', '', text)
        # 英文标点
        text = text.translate(str.maketrans('', '', string.punctuation))
        # 收缩多余空格
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    @staticmethod
    def _fmt_time(seconds):
        """秒 → SRT 时间 HH:MM:SS,mmm"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ═══════════════════════════════════════════════════════════
# 命令行入口
# ═══════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Belle-whisper-large-v3-turbo-zh 中文语音识别工具'
    )
    parser.add_argument('video', help='输入视频文件路径')
    parser.add_argument('-o', '--output', help='输出 SRT 路径 (默认与视频同名)')
    parser.add_argument('-m', '--model', default=None,
                        help=f'模型路径 (默认: {DEFAULT_MODEL_PATH})')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='显示详细进度')

    args = parser.parse_args()

    if not os.path.exists(args.video):
        print(f"文件不存在: {args.video}")
        return 1

    transcriber = BelleTranscriber(model_path=args.model)
    transcriber.transcribe_video(
        args.video,
        output_srt_path=args.output,
        verbose=args.verbose,
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
