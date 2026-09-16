# -*- coding: utf-8 -*-
"""
FunASR 字幕识别模块
支持三种后端：
  1. direct  - 直接加载模型（需要系统 Python 已安装 FunASR）
  2. portable - 调用 FunASR Portable GPU 包的独立 Python（支持 SenseVoiceSmall）
  3. whisper - 使用 OpenAI Whisper

用法:
    transcriber = FunASRTranscriber(backend='portable')
    result = transcriber.transcribe('video.mp4', 'output.srt')
"""

import os
import sys
import re
import json
import subprocess
from pathlib import Path

# 导入ASR纠错器
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from ASRCorrector import FileBasedCorrector
    ASR_CORRECTOR_AVAILABLE = True
except ImportError:
    ASR_CORRECTOR_AVAILABLE = False


class FunASRTranscriber:
    """FunASR 音频转录器（多后端）"""

    # 便携版配置（相对于项目根目录）
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    PORTABLE_ROOT = os.path.join(PROJECT_ROOT, 'funasr')
    PORTABLE_PYTHON = os.path.join(PORTABLE_ROOT, 'runtime', 'python', 'python.exe')
    PORTABLE_WORKER = os.path.join(PORTABLE_ROOT, 'utils', '_funasr_portable_worker.py')

    # 直接加载模式支持的模型
    DIRECT_MODELS = {
        'paraformer-zh': {
            'model_id': 'damo/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch',
            'desc': '中文生产级ASR + 时间戳 (Paraformer)',
            'languages': ['zh', 'en'],
            'has_emotion': False,
            'has_speaker': True,
        }
    }

    # 便携版支持的模型
    PORTABLE_MODELS = {
        'sensevoice': {
            'desc': '多语言 SENSEVOICE 多语言识别 + 情绪 + 事件 + 说话人（便携版）',
            'languages': ['zh', 'en', 'ja', 'ko', 'yue', 'auto'],
            'has_emotion': True,
            'has_speaker': True,
        },
        'paraformer-zh': {
            'desc': '中文 Paraformer 生产级 ASR + VAD + 标点（便携版）',
            'languages': ['zh', 'en'],
            'has_emotion': False,
            'has_speaker': True,
        },
        'paraformer-large-zh': {
            'desc': '中文 Paraformer-Large 长音频模型（自带 VAD+标点）',
            'languages': ['zh', 'en'],
            'has_emotion': False,
            'has_speaker': True,
        },
        'paraformer-en': {
            'desc': '英文 Paraformer ASR + VAD（便携版）',
            'languages': ['en'],
            'has_emotion': False,
            'has_speaker': False,
        },
    }

    # 所有可用模型（含 Whisper）
    WHISPER_MODELS = {
        'whisper-large-v3': {
            'desc': 'OpenAI Whisper Large-v3（本地）',
            'languages': ['zh', 'en', 'ja', 'ko', 'auto'],
            'has_emotion': False,
            'has_speaker': False,
        },
    }

    # 模型名归一化（兼容 GUI 配置中的旧写法）
    MODEL_ALIASES = {
        'SenseVoiceSmall': 'sensevoice',
        'sensevoicesmall': 'sensevoice',
        'whisper-large-v3-turbo': 'whisper-large-v3',
        'large-v3-turbo': 'whisper-large-v3',
    }

    def __init__(self, model_name='sensevoice', backend='portable', device=None,
                 hotwords=None):
        """
        Args:
            model_name: 模型名（自动归一化）
            backend: 'portable' / 'direct' / 'whisper'
            device: 设备（cuda/cpu）
            hotwords: 热词列表
        """
        # 归一化模型名，兼容 GUI 配置中的旧写法
        self.model_name = self.MODEL_ALIASES.get(model_name, model_name)
        self.backend = backend
        if device:
            self.device = device
        elif backend == 'portable':
            self.device = 'cuda' if self._check_portable_cuda() else 'cpu'
        else:
            self.device = 'cuda' if self._check_cuda() else 'cpu'
        self.hotwords = hotwords or []
        self.model = None  # 仅 direct/whisper 后端使用

    def _check_cuda(self):
        try:
            import torch
            return torch.cuda.is_available()
        except Exception:
            return False

    def _check_portable_cuda(self):
        if not os.path.exists(self.PORTABLE_PYTHON):
            return False
        try:
            proc = subprocess.run(
                [
                    self.PORTABLE_PYTHON,
                    '-c',
                    'import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
            return proc.returncode == 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Portable 后端
    # ------------------------------------------------------------------
    def _check_portable(self):
        """检查便携版是否就绪"""
        if not os.path.exists(self.PORTABLE_PYTHON):
            raise RuntimeError(
                f"FunASR Portable Python 未找到: {self.PORTABLE_PYTHON}\n"
                f"请确认便携版已放置于: {self.PORTABLE_ROOT}"
            )
        if not os.path.exists(self.PORTABLE_WORKER):
            raise RuntimeError(
                f"Portable worker 未找到: {self.PORTABLE_WORKER}"
            )

    def _get_valid_models(self):
        """获取当前后端可用模型"""
        if self.backend == 'portable':
            return self.PORTABLE_MODELS
        elif self.backend == 'direct':
            return self.DIRECT_MODELS
        elif self.backend == 'whisper':
            return self.WHISPER_MODELS
        return {}

    def transcribe_video(self, video_path, output_srt_path=None, language="zh",
                         verbose=False, enable_emotion=False, enable_speaker=False,
                         punc_enabled=True):
        """转录视频为 SRT 字幕
        
        Args:
            punc_enabled: 是否启用标点恢复（portable 模式有效，whisper/direct 忽略）
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        if output_srt_path is None:
            output_srt_path = video_path.with_suffix('.srt')
        else:
            output_srt_path = Path(output_srt_path)

        if self.backend == 'portable':
            return self._transcribe_portable(str(video_path), str(output_srt_path),
                                             language, verbose, enable_speaker,
                                             punc_enabled)
        elif self.backend == 'direct':
            return self._transcribe_direct(str(video_path), str(output_srt_path),
                                           language, verbose, enable_emotion,
                                           enable_speaker)
        elif self.backend == 'whisper':
            return self._transcribe_whisper(str(video_path), str(output_srt_path),
                                            language, verbose)
        else:
            raise ValueError(f"未知后端: {self.backend}")

    def _transcribe_portable(self, video_path, output_srt_path, language, verbose,
                             enable_speaker=False, punc_enabled=True):
        """通过便携版 Python 子进程转录"""
        self._check_portable()

        cmd = [
            self.PORTABLE_PYTHON,
            self.PORTABLE_WORKER,
            '--input', video_path,
            '--output', output_srt_path,
            '--model', self.model_name,
            '--language', language,
        ]

        if enable_speaker:
            cmd.append('--speaker')
        if not punc_enabled:
            cmd.append('--no-punc')

        if self.device == 'cpu':
            cmd.extend(['--device', 'cpu'])
        elif self.device == 'cuda':
            cmd.extend(['--device', 'cuda'])

        print(f"[FunASR] Portable 转录 {'GPU' if self.device == 'cuda' else 'CPU'}")
        print(f"[FunASR] 模型: {self.model_name}")
        print(f"[FunASR] 输入: {Path(video_path).name}")

        try:
            # stderr 合并到 stdout，避免 FunASR 进度条写满 stderr 管道导致死锁
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                env={**os.environ, 'PYTHONUNBUFFERED': '1'},
            )

            result_json = None
            all_lines = []  # 缓存全部输出，失败时用于诊断
            for line in proc.stdout:
                line = line.strip()
                all_lines.append(line)
                if not line:
                    continue
                if verbose:
                    print(f"  [Worker] {line}")
                # 最后的 JSON 行就是结果
                if line.startswith('{') and '"status"' in line:
                    try:
                        result_json = json.loads(line)
                    except json.JSONDecodeError:
                        pass
                elif not verbose and line.startswith('[Worker]'):
                    # 静默模式下也输出关键进度
                    if any(k in line for k in ['Loading', 'Transcribing', 'done',
                                                'Model loaded', 'Writing']):
                        print(f"  {line}")

            proc.wait()

            if proc.returncode != 0:
                cmd_str = ' '.join(f'"{a}"' if ' ' in a else a for a in cmd)
                last_lines = '\n'.join(all_lines[-20:])
                raise RuntimeError(
                    f"Portable worker 异常退出 (code={proc.returncode})\n"
                    f"  命令: {cmd_str}\n"
                    f"  最后输出:\n{last_lines[:800]}"
                )

            if result_json and result_json.get('status') == 'ok':
                print(f"[FunASR] 完成: {result_json.get('segments', 0)} 条字幕 "
                      f"({result_json.get('transcribe_time', 0)}s)")
                return {
                    'srt_path': output_srt_path,
                    'segments': result_json.get('segments', 0),
                    'text_length': result_json.get('text_length', 0),
                    'transcribe_time': result_json.get('transcribe_time', 0),
                    'model': result_json.get('model', self.model_name),
                    'device': result_json.get('device', self.device),
                    'emotion_data': result_json.get('emotion_data', []),
                    'speaker_data': result_json.get('speaker_data', []),
                }
            elif result_json and 'error' in result_json:
                raise RuntimeError(f"Portable worker 错误: {result_json['error']}")
            else:
                raise RuntimeError("Portable worker 未返回有效结果")

        except Exception as e:
            print(f"[FunASR] 转录失败: {e}")
            import traceback
            traceback.print_exc()
            raise

    def transcribe_batch(self, items, language='zh', verbose=False,
                         enable_speaker=False, punc_enabled=True):
        """批量转录:一次拉起 portable worker,模型只加载一次,顺序处理多个视频

        Args:
            items: [(video_path, output_srt_path), ...]
        Returns:
            与 items 等长的结果列表,成功 {'ok':True,'srt_path','segments',
            'text_length','transcribe_time','speaker_data'},
            失败 {'ok':False,'error','srt_path'}
        Raises:
            RuntimeError: worker 进程级失败(清单错误/模型加载失败),此时全部条目未产出
        """
        if self.backend != 'portable':
            raise ValueError(f"批量模式仅支持 portable 后端，当前: {self.backend}")
        self._check_portable()
        if not items:
            return []

        manifest = {
            'items': [
                {'input': os.path.abspath(str(v)), 'output': os.path.abspath(str(s))}
                for v, s in items
            ],
            'model': self.model_name,
            'language': language,
            'device': self.device,
            'speaker': bool(enable_speaker),
            'punc': bool(punc_enabled),
        }

        import tempfile
        fd, manifest_path = tempfile.mkstemp(prefix='autoclip_batch_', suffix='.json')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, ensure_ascii=False)

            cmd = [self.PORTABLE_PYTHON, self.PORTABLE_WORKER,
                   '--batch-manifest', manifest_path]
            print(f"[FunASR] Portable 批量转录 {'GPU' if self.device == 'cuda' else 'CPU'} "
                  f"× {len(manifest['items'])} 个文件 (模型加载一次)")
            print(f"[FunASR] 模型: {self.model_name}")

            # stderr 合并到 stdout，避免 FunASR 进度条写满 stderr 管道导致死锁
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                env={**os.environ, 'PYTHONUNBUFFERED': '1'},
            )

            item_results = {}
            summary = None
            all_lines = []
            for line in proc.stdout:
                line = line.strip()
                all_lines.append(line)
                if not line:
                    continue
                if verbose:
                    print(f"  [Worker] {line}")
                parsed = None
                if line.startswith('{'):
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        parsed = None
                if isinstance(parsed, dict) and parsed.get('type') == 'item':
                    item_results[parsed.get('index')] = parsed
                    name = Path(parsed.get('input', '')).name
                    no = parsed.get('index', 0) + 1
                    if parsed.get('status') == 'ok':
                        print(f"  [批量 {no}/{len(manifest['items'])}] OK {name} "
                              f"({parsed.get('segments', 0)} 条字幕, "
                              f"{parsed.get('transcribe_time', 0)}s)")
                    else:
                        print(f"  [批量 {no}/{len(manifest['items'])}] 失败 {name}: "
                              f"{str(parsed.get('error', ''))[:150]}")
                elif isinstance(parsed, dict) and parsed.get('status') == 'ok' \
                        and 'batch' in parsed:
                    summary = parsed
                elif not verbose and line.startswith('[Worker]'):
                    if any(k in line for k in ['Batch:', 'Models loaded', 'VAD done',
                                               'Punctuation model', 'Speaker diarization']):
                        print(f"  {line}")

            proc.wait()

            if proc.returncode != 0 or summary is None:
                cmd_str = ' '.join(f'"{a}"' if ' ' in a else a for a in cmd)
                last_lines = '\n'.join(all_lines[-20:])
                raise RuntimeError(
                    f"Portable 批量 worker 异常退出 (code={proc.returncode})\n"
                    f"  命令: {cmd_str}\n"
                    f"  最后输出:\n{last_lines[:800]}"
                )

            results = []
            for i, (v, srt) in enumerate(items):
                entry = item_results.get(i, {})
                if entry.get('status') == 'ok':
                    results.append({
                        'ok': True,
                        'srt_path': str(srt),
                        'segments': entry.get('segments', 0),
                        'text_length': entry.get('text_length', 0),
                        'transcribe_time': entry.get('transcribe_time', 0),
                        'speaker_data': entry.get('speaker_data', []),
                    })
                else:
                    results.append({
                        'ok': False,
                        'error': entry.get('error', 'worker 未返回该条目结果'),
                        'srt_path': str(srt),
                    })
            ok_n = sum(1 for r in results if r['ok'])
            print(f"[FunASR] 批量完成: {ok_n}/{len(results)} 成功")
            return results
        finally:
            try:
                os.unlink(manifest_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Direct 后端（仅保留 paraformer-zh，SenseVoice 交给 portable）
    # ------------------------------------------------------------------
    def _load_model_direct(self, enable_speaker=False):
        """加载系统 Python 的 FunASR 模型"""
        from funasr import AutoModel

        model_cfg = self.DIRECT_MODELS.get(self.model_name)
        if not model_cfg:
            raise ValueError(f"Direct 后端不支持的模型: {self.model_name}")

        # 确保 cam++ 等模型下载到项目目录内（与 portable worker 保持一致）
        models_dir = os.path.join(self.PORTABLE_ROOT, 'workspace', 'models')
        os.environ.setdefault('MODELSCOPE_CACHE', models_dir)

        spk_note = " + cam++" if enable_speaker else ""
        print(f"[FunASR] 加载模型: {self.model_name} (direct{spk_note})")
        print(f"[FunASR] 设备: {self.device}")

        model_kwargs = {
            'model': model_cfg['model_id'],
            'vad_model': 'fsmn-vad',
            'punc_model': 'ct-punc',
            'device': self.device,
        }
        if enable_speaker and model_cfg.get('has_speaker'):
            model_kwargs['spk_model'] = 'cam++'

        self.model = AutoModel(**model_kwargs)
        self._direct_speaker_enabled = enable_speaker
        print("[FunASR] 模型加载完成")

    def _transcribe_direct(self, video_path, output_srt_path, language, verbose,
                           enable_emotion, enable_speaker):
        """Direct 模式转录"""
        # 说话人开关变化时重新加载模型
        if self.model is None or getattr(self, '_direct_speaker_enabled', None) != enable_speaker:
            self._check_installation()
            self._load_model_direct(enable_speaker=enable_speaker)

        spk_note = " + speaker" if enable_speaker else ""
        print(f"[FunASR] 开始识别字幕{spk_note}: {Path(video_path).name}")

        kwargs = {'input': video_path}
        if self.hotwords:
            kwargs['hotword'] = ' '.join(self.hotwords)

        result = self.model.generate(**kwargs)

        if verbose:
            print(f"[FunASR] 原始结果: {result}")

        srt_content, segments, speaker_data = self._parse_result_to_srt(result, enable_speaker)

        with open(output_srt_path, 'w', encoding='utf-8') as f:
            f.write(srt_content)

        print(f"[FunASR] 字幕已保存: {output_srt_path}")
        print(f"[FunASR] 共 {len(segments)} 条字幕片段")
        if speaker_data:
            print(f"[FunASR] 共 {len(speaker_data)} 条说话人标注")

        return {
            'srt_path': output_srt_path,
            'segments': len(segments),
            'emotion_data': [],
            'speaker_data': speaker_data,
        }

    def _check_installation(self):
        """检查系统 FunASR 是否已安装"""
        try:
            import funasr
            print(f"[FunASR] 版本: {funasr.__version__}")
        except ImportError:
            print("[FunASR] 未安装，正在安装...")
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'funasr', '-q'])
            print("[FunASR] 安装完成")

    # ------------------------------------------------------------------
    # Whisper 后端（保留兼容）
    # ------------------------------------------------------------------
    def _transcribe_whisper(self, video_path, output_srt_path, language, verbose):
        """Whisper 转录（委托给 whisper_transcriber）"""
        from whisper_transcriber import WhisperTranscriber

        # 解析模型路径 — 与步骤2保持一致
        model_path = None

        # 1. 如果 model_name 本身是目录（HuggingFace 格式，如 BELLE-2），直接使用
        if os.path.isdir(self.model_name):
            model_path = self.model_name
        else:
            # 2. 去掉 "whisper-" 前缀，尝试 .pt 文件匹配
            model_short = self.model_name.replace('whisper-', '')
            if os.path.isabs(model_short) and os.path.isfile(model_short):
                model_path = model_short
            elif not os.path.isabs(model_short) and not os.path.isfile(model_short):
                candidate = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    'models', 'whisper', f'{model_short}.pt')
                if os.path.isfile(candidate):
                    model_path = candidate
            if not model_path and os.path.isfile(model_short):
                model_path = model_short

        wt = WhisperTranscriber(
            model_path=model_path,
            device=self.device,
        )
        return wt.transcribe_video(video_path, output_srt_path, language, verbose)

    # ------------------------------------------------------------------
    # 通用工具方法
    # ------------------------------------------------------------------
    def _parse_result_to_srt(self, result, enable_speaker=False):
        """将 FunASR 结果解析为 SRT"""
        srt_lines = []
        segments = []
        speaker_data = []

        if not result or not isinstance(result, list) or len(result) == 0:
            return '\n'.join(srt_lines), segments, speaker_data

        r = result[0]
        if not isinstance(r, dict):
            return '\n'.join(srt_lines), segments, speaker_data

        text = r.get('text', '')
        timestamps = r.get('timestamp', [])

        corrector = None
        if ASR_CORRECTOR_AVAILABLE:
            try:
                corrector = FileBasedCorrector()
            except Exception:
                pass

        if timestamps and len(timestamps) > 0:
            for i, ts in enumerate(timestamps, 1):
                if isinstance(ts, list) and len(ts) >= 2:
                    start_ms, end_ms = ts[0], ts[1]
                    seg_text = ""
                    if isinstance(text, list) and len(text) >= i:
                        seg_text = text[i - 1]
                    elif isinstance(text, str):
                        sentences = re.split(r'[。！？\.\!\?]', text)
                        sentences = [s.strip() for s in sentences if s.strip()]
                        if len(sentences) >= i:
                            seg_text = sentences[i - 1]
                    if not seg_text:
                        continue

                    if corrector:
                        seg_text = corrector.correct_text(seg_text)

                    # 说话人信息（如果有）
                    spk_label = ''
                    if enable_speaker:
                        spk = r.get('spk', None)
                        if isinstance(spk, list) and len(spk) >= i:
                            spk_id = spk[i - 1]
                        elif isinstance(spk, (int, float)):
                            spk_id = int(spk)
                        else:
                            spk_id = None
                        if spk_id is not None:
                            spk_label = f'[SPK:{spk_id}] '
                            speaker_data.append({
                                'index': i, 'spk': int(spk_id),
                                'start': start_ms / 1000,
                                'end': end_ms / 1000,
                                'start_str': _format_srt_time(start_ms / 1000),
                                'end_str': _format_srt_time(end_ms / 1000),
                                'text': seg_text,
                            })

                    srt_lines.append(str(i))
                    srt_lines.append(
                        f"{_format_srt_time(start_ms / 1000)} --> "
                        f"{_format_srt_time(end_ms / 1000)}"
                    )
                    srt_lines.append(f"{spk_label}{seg_text}")
                    srt_lines.append('')
                    segments.append({
                        'index': i, 'start': start_ms / 1000,
                        'end': end_ms / 1000, 'text': seg_text,
                    })
        else:
            if text:
                if isinstance(text, list):
                    text = ' '.join(text)
                if corrector:
                    text = corrector.correct_text(text)
                srt_lines.append("1")
                srt_lines.append("00:00:00,000 --> 00:00:05,000")
                srt_lines.append(text[:2000])
                srt_lines.append('')
                segments.append({
                    'index': 1, 'start': 0, 'end': 5, 'text': text,
                })

        return '\n'.join(srt_lines), segments, speaker_data

    @staticmethod
    def list_models(backend='portable'):
        """列出某后端支持的模型"""
        if backend == 'portable':
            return dict(FunASRTranscriber.PORTABLE_MODELS)
        elif backend == 'direct':
            return dict(FunASRTranscriber.DIRECT_MODELS)
        elif backend == 'whisper':
            return dict(FunASRTranscriber.WHISPER_MODELS)
        return {}


def _format_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    import argparse
    parser = argparse.ArgumentParser(description='FunASR 字幕识别工具')
    parser.add_argument('video', help='输入视频文件路径')
    parser.add_argument('-o', '--output', help='输出 SRT 文件路径')
    parser.add_argument('-b', '--backend', default='portable',
                        choices=['portable', 'direct', 'whisper'])
    parser.add_argument('-m', '--model', default='sensevoice',
                        help='模型名称')
    parser.add_argument('-l', '--language', default='zh', help='语言代码')
    parser.add_argument('-d', '--device', default=None, help='设备')
    parser.add_argument('--speaker', action='store_true', help='启用说话人分离')
    parser.add_argument('--hotword', nargs='+', default=[], help='热词列表')
    parser.add_argument('-v', '--verbose', action='store_true')

    args = parser.parse_args()

    transcriber = FunASRTranscriber(
        model_name=args.model,
        backend=args.backend,
        device=args.device,
        hotwords=args.hotword,
    )

    result = transcriber.transcribe_video(
        args.video, args.output,
        language=args.language,
        verbose=args.verbose,
        enable_emotion=('sensevoice' in args.model.lower()),
        enable_speaker=args.speaker,
    )

    print(f"\n[完成] SRT: {result['srt_path']}")
    print(f"[信息] 共 {result.get('segments', 0)} 条字幕")


if __name__ == "__main__":
    main()
