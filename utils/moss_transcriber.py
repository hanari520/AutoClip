# -*- coding: utf-8 -*-
"""
MOSS-Transcribe-Diarize 字幕识别模块（外壳）
通过项目内独立虚拟环境 moss_env 的 Python 子进程调用 utils/_moss_transcribe_worker.py，
主环境无需安装 transformers 5.x / torch 2.6。

模型: OpenMOSS-Team/MOSS-Transcribe-Diarize (0.9B, Apache-2.0)
转写 + 说话人分离 + 段级时间戳联合输出,低能量点分块适配 8G 显存。

用法:
    transcriber = MossTranscriber()
    result = transcriber.transcribe_video('video.mp4', 'output.srt', language='zh')
"""
import os
import sys
import json
import subprocess
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PYTHON = os.path.join(PROJECT_ROOT, 'moss_env', 'Scripts', 'python.exe')
WORKER = os.path.join(PROJECT_ROOT, 'utils', '_moss_transcribe_worker.py')

# FFmpeg 目录：可用环境变量 AUTOCLIP_FFMPEG_BIN 指定（存在则注入 PATH，保证 worker 能提取音频）
FFMPEG_BIN = os.environ.get('AUTOCLIP_FFMPEG_BIN', '')

# 支持的模型目录（models/ 下的目录名）
DEFAULT_MODEL = 'MOSS-Transcribe-Diarize-0.9B'


class MossTranscriber:
    """MOSS-Transcribe-Diarize 转录器（子进程外骨骼）"""

    def __init__(self, model_name=DEFAULT_MODEL, device=None, hotwords=None):
        """
        Args:
            model_name: models/ 下的模型目录名
            device: 设备 cuda/cpu（默认自动）
            hotwords: 热词列表（预留,当前未接入 MOSS 提示词）
        """
        self.model_name = model_name or DEFAULT_MODEL
        self.device = device
        self.hotwords = hotwords or []
        self._check_env()

    def _check_env(self):
        if not os.path.exists(VENV_PYTHON):
            raise RuntimeError(
                f"MOSS venv python 未找到: {VENV_PYTHON}\n"
                f"请先创建 moss_env 并安装 torch 2.6/transformers 5.x (见 docs)"
            )
        if not os.path.exists(WORKER):
            raise RuntimeError(f"MOSS worker 未找到: {WORKER}")

    def transcribe_video(self, video_path, output_srt_path=None, language="zh",
                         verbose=False, punc_enabled=True):
        """转录视频为 SRT 字幕（段级时间戳来自模型联合输出,已按官方规则规范化）"""
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
            '--model-dir', self.model_name,
        ]
        if self.device:
            cmd.extend(['--device', self.device])
        if not punc_enabled:
            cmd.append('--no-punc')
        if self.hotwords:
            cmd.extend(['--context', ' '.join(self.hotwords)[:500]])

        env = dict(os.environ)
        env['PYTHONUNBUFFERED'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'
        if os.path.isdir(FFMPEG_BIN):
            env['PATH'] = FFMPEG_BIN + os.pathsep + env.get('PATH', '')

        print(f"[MOSS] 模型: {self.model_name}")
        print(f"[MOSS] 输入: {video_path.name}")

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
                elif line.startswith('[Moss-Worker]'):
                    # 静默模式下输出关键进度
                    if any(k in line for k in ['Device', 'loaded', 'Chunk', 'Split',
                                               'Audio extracted', 'SRT written',
                                               'WARNING', 'done']):
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
                    f"MOSS worker 异常退出 (code={proc.returncode})\n"
                    f"  命令: {cmd_str}\n"
                    f"  最后输出:\n{last_lines[:800]}"
                )

            if result_json and result_json.get('status') == 'ok':
                speakers = result_json.get('speakers', [])
                print(f"[MOSS] 完成: {result_json.get('segments', 0)} 条字幕 "
                      f"说话人: {','.join(speakers) if speakers else '-'} "
                      f"({result_json.get('transcribe_time', 0)}s, {result_json.get('device', '?')})")
                return {
                    'srt_path': str(output_srt_path),
                    'segments': result_json.get('segments', 0),
                    'text_length': result_json.get('text_length', 0),
                    'transcribe_time': result_json.get('transcribe_time', 0),
                    'model': self.model_name,
                    'device': result_json.get('device', self.device or 'auto'),
                    'speakers': speakers,
                    'emotion_data': [],
                    'speaker_data': result_json.get('speaker_data', []),
                }
            if result_json and 'error' in result_json:
                raise RuntimeError(f"MOSS worker 错误: {result_json['error']}")
            raise RuntimeError("MOSS worker 未返回有效结果")

        except Exception as e:
            print(f"[MOSS] 转录失败: {e}")
            import traceback
            traceback.print_exc()
            raise


def main():
    import argparse
    parser = argparse.ArgumentParser(description='MOSS-Transcribe-Diarize 字幕识别工具')
    parser.add_argument('video', help='输入视频文件路径')
    parser.add_argument('-o', '--output', help='输出 SRT 文件路径')
    parser.add_argument('-l', '--language', default='zh', help='语言代码')
    parser.add_argument('-d', '--device', default=None, help='设备 cuda/cpu')
    parser.add_argument('-v', '--verbose', action='store_true')

    args = parser.parse_args()

    transcriber = MossTranscriber(device=args.device)
    result = transcriber.transcribe_video(
        args.video, args.output, language=args.language, verbose=args.verbose)

    print(f"\n[完成] SRT: {result['srt_path']}")
    print(f"[信息] 共 {result.get('segments', 0)} 条字幕")


if __name__ == "__main__":
    main()
