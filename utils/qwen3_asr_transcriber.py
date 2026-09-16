# -*- coding: utf-8 -*-
"""
Qwen3-ASR 字幕识别模块（外壳）
通过项目内独立虚拟环境 qwen3_env 的 Python 子进程调用 utils/_qwen3_asr_worker.py，
主环境无需安装 torch/transformers 新版本。

用法:
    transcriber = Qwen3ASRTranscriber()
    result = transcriber.transcribe_video('video.mp4', 'output.srt', language='zh')
"""
import os
import sys
import json
import subprocess
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PYTHON = os.path.join(PROJECT_ROOT, 'qwen3_env', 'Scripts', 'python.exe')
WORKER = os.path.join(PROJECT_ROOT, 'utils', '_qwen3_asr_worker.py')

# FFmpeg 目录：可用环境变量 AUTOCLIP_FFMPEG_BIN 指定（存在则注入 PATH，保证 worker 能提取音频）
FFMPEG_BIN = os.environ.get('AUTOCLIP_FFMPEG_BIN', '')

# context 热词上限（Qwen3-ASR 上下文提示不宜过长）
MAX_CONTEXT_CHARS = 500

# 支持的转写模型（对应 models/ 下的本地目录名）
SUPPORTED_MODELS = ('Qwen3-ASR-1.7B', 'Qwen3-ASR-0.6B')
DEFAULT_MODEL = 'Qwen3-ASR-1.7B'


class Qwen3ASRTranscriber:
    """Qwen3-ASR + ForcedAligner 转录器（子进程外骨骼）"""

    def __init__(self, model_name=DEFAULT_MODEL, device=None, hotwords=None):
        """
        Args:
            model_name: models/ 下的转写模型目录名（Qwen3-ASR-1.7B / Qwen3-ASR-0.6B），
                        不在支持列表时回退默认 1.7B
            device: 设备 cuda/cpu（默认自动）
            hotwords: 热词列表 → Qwen3-ASR context 提示
        """
        self.model_name = model_name if model_name in SUPPORTED_MODELS else DEFAULT_MODEL
        self.device = device
        self.hotwords = hotwords or []
        self._check_env()

    def _check_env(self):
        if not os.path.exists(VENV_PYTHON):
            raise RuntimeError(
                f"Qwen3 venv python 未找到: {VENV_PYTHON}\n"
                f"请先创建虚拟环境并安装 torch/qwen-asr (见 docs)"
            )
        if not os.path.exists(WORKER):
            raise RuntimeError(f"Qwen3 worker 未找到: {WORKER}")

    def transcribe_video(self, video_path, output_srt_path=None, language="zh",
                         verbose=False, punc_enabled=True):
        """转录视频为 SRT 字幕（句级时间戳来自 ForcedAligner）"""
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        if output_srt_path is None:
            output_srt_path = video_path.with_suffix('.srt')
        else:
            output_srt_path = Path(output_srt_path)

        cmd = [
            VENV_PYTHON, WORKER,
            '--input', str(video_path),
            '--output', str(output_srt_path),
            '--language', language,
            '--asr-model', self.model_name,
        ]
        if self.device:
            cmd.extend(['--device', self.device])
        if not punc_enabled:
            cmd.append('--no-punc')
        context = ' '.join(self.hotwords)[:MAX_CONTEXT_CHARS] if self.hotwords else ''
        if context:
            cmd.extend(['--context', context])

        env = dict(os.environ)
        env['PYTHONUNBUFFERED'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'
        if os.path.isdir(FFMPEG_BIN):
            env['PATH'] = FFMPEG_BIN + os.pathsep + env.get('PATH', '')

        print(f"[Qwen3-ASR] 模型: {self.model_name} + ForcedAligner-0.6B")
        print(f"[Qwen3-ASR] 输入: {video_path.name}")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                env=env,
            )

            result_json = None
            all_lines = []
            for line in proc.stdout:
                line = line.strip()
                all_lines.append(line)
                if not line:
                    continue
                if verbose:
                    print(f"  {line}")
                elif line.startswith('[Qwen3-Worker]'):
                    # 静默模式下输出关键进度
                    if any(k in line for k in ['Stage', 'loaded', 'chunk', 'Split',
                                                'Audio extracted', 'SRT written', 'done',
                                                'Device']):
                        print(f"  {line}")
                if line.startswith('{') and '"status"' in line:
                    try:
                        result_json = json.loads(line)
                    except json.JSONDecodeError:
                        pass

            proc.wait()

            if proc.returncode != 0:
                cmd_str = ' '.join(f'"{a}"' if ' ' in a else a for a in cmd)
                last_lines = '\n'.join(all_lines[-20:])
                raise RuntimeError(
                    f"Qwen3 worker 异常退出 (code={proc.returncode})\n"
                    f"  命令: {cmd_str}\n"
                    f"  最后输出:\n{last_lines[:800]}"
                )

            if result_json and result_json.get('status') == 'ok':
                print(f"[Qwen3-ASR] 完成: {result_json.get('segments', 0)} 条字幕 "
                      f"({result_json.get('transcribe_time', 0)}s, {result_json.get('device', '?')})")
                return {
                    'srt_path': str(output_srt_path),
                    'segments': result_json.get('segments', 0),
                    'text_length': result_json.get('text_length', 0),
                    'transcribe_time': result_json.get('transcribe_time', 0),
                    'model': self.model_name,
                    'device': result_json.get('device', self.device or 'auto'),
                    'emotion_data': [],
                    'speaker_data': [],
                }
            if result_json and 'error' in result_json:
                raise RuntimeError(f"Qwen3 worker 错误: {result_json['error']}")
            raise RuntimeError("Qwen3 worker 未返回有效结果")

        except Exception as e:
            print(f"[Qwen3-ASR] 转录失败: {e}")
            import traceback
            traceback.print_exc()
            raise


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Qwen3-ASR 字幕识别工具')
    parser.add_argument('video', help='输入视频文件路径')
    parser.add_argument('-o', '--output', help='输出 SRT 文件路径')
    parser.add_argument('-l', '--language', default='zh', help='语言代码')
    parser.add_argument('-d', '--device', default=None, help='设备 cuda/cpu')
    parser.add_argument('--hotword', nargs='+', default=[], help='热词列表')
    parser.add_argument('-v', '--verbose', action='store_true')

    args = parser.parse_args()

    transcriber = Qwen3ASRTranscriber(device=args.device, hotwords=args.hotword)
    result = transcriber.transcribe_video(
        args.video, args.output, language=args.language, verbose=args.verbose)

    print(f"\n[完成] SRT: {result['srt_path']}")
    print(f"[信息] 共 {result.get('segments', 0)} 条字幕")


if __name__ == "__main__":
    main()
