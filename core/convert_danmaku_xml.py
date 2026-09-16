#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""弹幕 XML → ASS 前置转换。

把录播目录里与视频同名、且缺少对应 ASS 的 XML 弹幕用 DanmakuFactory 转成 ASS，
供步骤3高光分析的弹幕热度子模式使用。已有同名 ASS 时跳过（幂等，可重复运行）。

使用入口：
  1. auto_pipeline 步骤1-4（默认开启，配置 step1_prepare.convert_danmaku_xml=false 关闭）
  2. generate_clips_combined.py 分析前的 ensure_danmaku_ass_files 委托
  3. 手动运行: python core/convert_danmaku_xml.py -i "录播目录" [--recursive]

失败语义：尽力而为 —— DanmakuFactory 缺失或单个文件转换失败只打印警告，
不影响其余文件，进程以 0 退出（不阻断流水线）；仅目录不存在时返回 1。
XML 源文件只读：转换只产出 ASS，任何情况下不删除、不修改 XML。
"""

import argparse
import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

VIDEO_EXTS = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv')


def _scan_dir(root, files):
    """扫描单个目录，返回有同名视频但缺同名 ASS 的 [(xml_path, ass_path)]。"""
    stems = {os.path.splitext(f)[0] for f in files if f.lower().endswith(VIDEO_EXTS)}
    out = []
    for f in sorted(files):
        if not f.lower().endswith('.xml'):
            continue
        stem = os.path.splitext(f)[0]
        if stem not in stems:
            continue
        ass_path = os.path.join(root, stem + '.ass')
        if os.path.exists(ass_path):
            continue
        out.append((os.path.join(root, f), ass_path))
    return out


def find_pending_xml(input_dir, recursive=False):
    """找出目录（可选递归）里待转换的 XML 弹幕。"""
    pending = []
    if recursive:
        for root, _dirs, files in os.walk(input_dir):
            pending.extend(_scan_dir(root, files))
    else:
        pending.extend(_scan_dir(input_dir, os.listdir(input_dir)))
    return pending


def convert_dir_xml_to_ass(input_dir, recursive=False, log=print):
    """转换目录内缺失的弹幕 ASS，返回成功数（0 = 无待转换；-1 = 工具不可用）。"""
    pending = find_pending_xml(input_dir, recursive)
    if not pending:
        return 0

    try:
        from burn_danmu_video import convert_xml_to_ass, resolve_danmaku_factory
    except Exception as e:
        log(f"[弹幕前置] 无法加载转换模块: {e}")
        return -1

    factory = resolve_danmaku_factory()
    if not factory:
        log("[弹幕前置] 未找到 DanmakuFactory.exe，跳过 XML→ASS 转换（弹幕源将缺失）")
        return -1

    log(f"[弹幕前置] {len(pending)} 个 XML 弹幕缺少对应 ASS，开始转换...")
    ok_count = 0
    for xml_path, ass_path in pending:
        xml_name = os.path.basename(xml_path)
        stem = os.path.splitext(os.path.basename(ass_path))[0]
        try:
            result = convert_xml_to_ass(xml_path, ass_path, factory)
            if result.returncode == 0 and os.path.isfile(ass_path) and os.path.getsize(ass_path) > 0:
                ok_count += 1
                log(f"  [OK] {stem}.ass")
            else:
                if os.path.isfile(ass_path):
                    os.remove(ass_path)
                log(f"  [失败] {xml_name} (exit={result.returncode})，该视频按无弹幕处理")
        except Exception as e:
            if os.path.isfile(ass_path):
                os.remove(ass_path)
            log(f"  [失败] {xml_name}: {e}")
    log(f"[弹幕前置] 转换完成 {ok_count}/{len(pending)}")
    return ok_count


def main():
    parser = argparse.ArgumentParser(
        description='弹幕 XML→ASS 前置转换（仅处理有同名视频但缺同名 ASS 的 XML）')
    parser.add_argument('--input-dir', '-i', required=True, help='录播目录')
    parser.add_argument('--recursive', action='store_true', help='递归处理子目录')
    args = parser.parse_args()

    if not os.path.isdir(args.input_dir):
        print(f"❌ 目录不存在: {args.input_dir}")
        return 1

    convert_dir_xml_to_ass(args.input_dir, recursive=args.recursive)
    return 0


if __name__ == '__main__':
    sys.exit(main())
