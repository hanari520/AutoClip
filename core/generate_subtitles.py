# -*- coding: utf-8 -*-
"""
字幕生成脚本
批量调用Whisper生成SRT字幕文件
"""
import os
import sys
import glob
import argparse
import json
from pathlib import Path

# 设置UTF-8编码
sys.stdout.reconfigure(encoding='utf-8')

# 添加项目根目录到路径，支持 from utils.xxx import xxx
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'utils'))
from ASRCorrector import FileBasedCorrector


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='批量字幕生成工具')
    parser.add_argument('--input-dir', '-i', type=str, required=True,
                        help='输入目录（包含MP4视频文件）')
    parser.add_argument('--model', '-m', type=str,
                        default=None,
                        help='Whisper模型路径（默认: models/whisper/large-v3-turbo.pt）')
    parser.add_argument('--language', '-l', type=str, default='zh',
                        help='语言代码 (zh/en/ja，默认zh)')
    parser.add_argument('--auto-lang', action='store_true',
                        help='启用自动语言检测')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='显示详细进度')
    parser.add_argument('--engine', '-e', type=str, default='portable',
                        choices=['portable', 'whisper', 'funasr', 'belle', 'qwen3', 'moss'],
                        help='ASR引擎: portable (GPU便携版推荐) / whisper / funasr / belle / qwen3 / moss')
    parser.add_argument('--funasr-model', type=str, default='sensevoice',
                        help='FunASR模型 (默认: sensevoice)')
    parser.add_argument('--qwen3-model', type=str, default='Qwen3-ASR-1.7B',
                        choices=['Qwen3-ASR-1.7B', 'Qwen3-ASR-0.6B'],
                        help='Qwen3转写模型 (默认: Qwen3-ASR-1.7B)')
    parser.add_argument('--speaker', action='store_true',
                        help='启用说话人分离 (仅funasr引擎有效)')
    parser.add_argument('--no-punc', action='store_true',
                        help='禁用标点恢复（标点用空格代替，仅funasr portable引擎有效）')
    parser.add_argument('--hotword', nargs='+', default=[],
                        help='热词列表 (仅funasr引擎有效)')
    return parser.parse_args()


def find_video_files(input_dir):
    """查找目录下的所有视频文件（弹幕版优先）"""
    video_extensions = ['*.mp4', '*.mkv', '*.avi', '*.mov', '*.wmv']
    video_files = []
    
    for ext in video_extensions:
        pattern = os.path.join(input_dir, ext)
        video_files.extend(glob.glob(pattern))

    video_files = sorted(video_files)

    # 弹幕版优先：同目录存在 xxx-弹幕版.mp4 时跳过 xxx.mp4
    from utils.video_filter import filter_danmaku_versions
    video_files = filter_danmaku_versions(video_files)

    return video_files


def main():
    args = parse_args()
    
    input_dir = args.input_dir
    
    # 解析 Whisper 模型默认路径
    if args.model is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        args.model = os.path.join(project_root, 'models', 'whisper', 'large-v3-turbo.pt')
    
    print("=" * 60)
    engine_display = {
        'portable': 'FunASR GPU便携版', 'funasr': 'FunASR Direct',
        'whisper': 'Whisper', 'belle': 'Belle-whisper-large-v3-turbo-zh',
        'qwen3': 'Qwen3-ASR-1.7B + ForcedAligner',
        'moss': 'MOSS-Transcribe-Diarize (转写+说话人分离)',
    }
    print(f"{engine_display.get(args.engine, args.engine)} 字幕批量生成工具")
    if args.engine in ('portable', 'funasr'):
        print(f"模型: {args.funasr_model}")
    elif args.engine == 'belle':
        print(f"模型: models/whisper/belle-whisper-large-v3-turbo-zh-ct2")
    elif args.engine == 'qwen3':
        print(f"模型: models/Qwen3-ASR-1.7B + models/Qwen3-ForcedAligner-0.6B")
    elif args.engine == 'moss':
        print(f"模型: models/MOSS-Transcribe-Diarize-0.9B")
    else:
        print(f"模型: {args.model}")
    print("=" * 60)
    print(f"输入目录: {input_dir}")
    print(f"语言: {args.language}")
    print(f"引擎: {args.engine}")
    if args.speaker:
        print("说话人分离: 已启用")
    print()
    
    # 检查输入目录
    if not os.path.exists(input_dir):
        print(f"[错误] 目录不存在: {input_dir}")
        return 1
    
    # 查找视频文件
    print("[1/4] 扫描视频文件...")
    video_files = find_video_files(input_dir)
    
    if not video_files:
        print(f"[错误] 未找到视频文件 (支持: mp4, mkv, avi, mov, wmv)")
        return 1
    
    print(f"[OK] 找到 {len(video_files)} 个视频文件:")
    for vf in video_files:
        print(f"  - {os.path.basename(vf)}")
    
    # 初始化纠错器（每次从最新 asr_dict.txt 读取；提前到此处，确保已有字幕纠错可用）
    print("\n[2/4] 加载纠错字典...")
    corrector = FileBasedCorrector()

    # 检查已有字幕文件
    print("\n[3/4] 检查已有字幕...")
    videos_to_process = []
    skipped_but_correct = []  # 已有字幕，但仍需跑纠错
    for vf in video_files:
        srt_path = os.path.splitext(vf)[0] + '.srt'
        if os.path.exists(srt_path):
            print(f"  [跳过] {os.path.basename(vf)} (已有字幕，将追加纠错)")
            skipped_but_correct.append(vf)
        else:
            videos_to_process.append(vf)
    
    # 对已有字幕的文件也跑纠错（词条可能更新了）
    if skipped_but_correct:
        print(f"\n  对 {len(skipped_but_correct)} 个已有字幕执行纠错...")
        for vf in skipped_but_correct:
            srt_path = os.path.splitext(vf)[0] + '.srt'
            try:
                corrector.process_file(srt_path, output_path=srt_path)
                print(f"  [OK] {os.path.basename(vf)} 纠错完成")
            except Exception as e:
                print(f"  [警告] {os.path.basename(vf)} 纠错失败: {e}")
    
    if not videos_to_process:
        print("[OK] 所有视频都已有字幕，无需处理")
        return 0
    
    print(f"\n[OK] 需要处理 {len(videos_to_process)} 个视频")
    
    # 初始化转录器
    print(f"\n[4/4] 初始化 {args.engine} 引擎...")
    transcriber = None
    try:
        if args.engine in ('portable', 'funasr'):
            from funasr_transcriber import FunASRTranscriber

            # SenseVoiceSmall 只能走 portable 后端
            backend = 'portable' if args.engine == 'portable' or args.funasr_model in ('SenseVoiceSmall', 'sensevoice') else 'direct'
            transcriber = FunASRTranscriber(
                model_name=args.funasr_model,
                backend=backend,
                hotwords=args.hotword
            )
        elif args.engine == 'belle':
            from belle_transcriber import BelleTranscriber

            transcriber = BelleTranscriber(model_path=args.model)
        elif args.engine == 'qwen3':
            from qwen3_asr_transcriber import Qwen3ASRTranscriber

            transcriber = Qwen3ASRTranscriber(model_name=args.qwen3_model)
        elif args.engine == 'moss':
            from moss_transcriber import MossTranscriber

            transcriber = MossTranscriber()
        else:
            from whisper_transcriber import WhisperTranscriber

            transcriber = WhisperTranscriber(model_path=args.model)
    except Exception as e:
        print(f"[错误] 模型加载失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # 批量处理
    print(f"\n{'='*60}")
    print("开始生成字幕...")
    print(f"{'='*60}")
    
    success_count = 0
    fail_count = 0

    # portable 后端批量模式:整个目录只付一次模型冷启动(直接后端不适用,走原逐个路径)
    batch_results = None
    if args.engine in ('portable', 'funasr') \
            and getattr(transcriber, 'backend', '') == 'portable':
        batch_items = [(vf, os.path.splitext(vf)[0] + '.srt') for vf in videos_to_process]
        print(f"\n[批量模式] {len(batch_items)} 个视频共用一次模型加载")
        try:
            batch_results = transcriber.transcribe_batch(
                batch_items,
                language=args.language,
                verbose=args.verbose,
                enable_speaker=args.speaker,
                punc_enabled=not args.no_punc
            )
        except Exception as e:
            print(f"[错误] 批量转录失败: {e}")
            import traceback
            traceback.print_exc()
            batch_results = [{'ok': False, 'error': str(e)[:200]} for _ in batch_items]

    for i, video_path in enumerate(videos_to_process, 1):
        video_name = os.path.basename(video_path)
        print(f"\n[{i}/{len(videos_to_process)}] {video_name}")

        try:
            if batch_results is not None:
                # portable 批量结果逐条消费,失败的条目计为失败
                r = batch_results[i - 1]
                if not r.get('ok'):
                    raise RuntimeError(r.get('error', '批量转录失败'))
                result = r
                print(f"  [OK] 字幕生成成功 ({r.get('segments', 0)} 条字幕)")
            elif args.engine in ('portable', 'funasr'):
                # 直接后端(direct)仍走单文件路径
                result = transcriber.transcribe_video(
                    video_path,
                    language=args.language,
                    verbose=args.verbose,
                    enable_speaker=args.speaker,
                    punc_enabled=not args.no_punc
                )
                # 保存说话人数据到JSON
                if args.speaker and result.get('speaker_data'):
                    speaker_path = os.path.splitext(video_path)[0] + '_speaker.json'
                    with open(speaker_path, 'w', encoding='utf-8') as f:
                        json.dump(result['speaker_data'], f, ensure_ascii=False, indent=2)
                    print(f"  [OK] 说话人数据已保存")
            elif args.engine == 'belle':
                # Belle 模型：中文专用，不需要 language / auto_lang 参数
                transcriber.transcribe_video(
                    video_path,
                    verbose=args.verbose,
                )
            elif args.engine == 'qwen3':
                transcriber.transcribe_video(
                    video_path,
                    language=args.language,
                    verbose=args.verbose,
                )
            elif args.engine == 'moss':
                result = transcriber.transcribe_video(
                    video_path,
                    language=args.language,
                    verbose=args.verbose,
                )
                # MOSS 自带说话人分离: 保存说话人数据到JSON
                if result.get('speaker_data'):
                    speaker_path = os.path.splitext(video_path)[0] + '_speaker.json'
                    with open(speaker_path, 'w', encoding='utf-8') as f:
                        json.dump(result['speaker_data'], f, ensure_ascii=False, indent=2)
                    print(f"  [OK] 说话人数据已保存")
            else:
                if args.auto_lang:
                    transcriber.transcribe_with_auto_detect(
                        video_path, 
                        verbose=args.verbose
                    )
                else:
                    transcriber.transcribe_video(
                        video_path,
                        language=args.language,
                        verbose=args.verbose
                    )
            success_count += 1
            print(f"  [OK] 字幕生成成功")

            # 自动纠错：对生成的 SRT 跑 ASR 纠错
            srt_path = os.path.splitext(video_path)[0] + '.srt'
            if os.path.exists(srt_path):
                try:
                    before_stats = os.path.getsize(srt_path)
                    corrector.process_file(srt_path, output_path=srt_path)
                    after_stats = os.path.getsize(srt_path)
                    print(f"  [OK] 纠错完成 ({before_stats} -> {after_stats} bytes)")
                except Exception as e:
                    print(f"  [警告] 纠错失败: {e}")

        except Exception as e:
            fail_count += 1
            print(f"  [错误] {e}")
    
    # 完成
    print(f"\n{'='*60}")
    print("处理完成!")
    print(f"{'='*60}")
    print(f"成功: {success_count} 个")
    print(f"失败: {fail_count} 个")
    print(f"跳过: {len(video_files) - len(videos_to_process)} 个 (已有字幕)")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
