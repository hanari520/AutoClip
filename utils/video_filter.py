# -*- coding: utf-8 -*-
"""
视频版本优先过滤。

规则：
- 如果同目录下同时存在 xxx.mp4 和 xxx-弹幕版.mp4，则只保留 xxx-弹幕版.mp4。
- 如果同时存在 xxx.mp4 和 xxx-竖屏原版.mp4，则只保留 xxx.mp4。
- 如果同时存在 xxx_subbed.mp4 和 xxx-弹幕版_subbed.mp4，也按弹幕版优先。

不影响没有更优版本对应的独立文件。
"""

import os


PORTRAIT_BACKUP_SUFFIX = "-竖屏原版"
DANMAKU_SUFFIX = "-弹幕版"
SUBBED_SUFFIX = "_subbed"


def _split_video_version(path):
    name = os.path.basename(path)
    base, ext = os.path.splitext(name)
    is_subbed = False
    if base.endswith(SUBBED_SUFFIX):
        base = base[:-len(SUBBED_SUFFIX)]
        is_subbed = True

    is_danmaku = False
    is_portrait_backup = False
    if base.endswith(DANMAKU_SUFFIX):
        base = base[:-len(DANMAKU_SUFFIX)]
        is_danmaku = True
    if base.endswith(PORTRAIT_BACKUP_SUFFIX):
        base = base[:-len(PORTRAIT_BACKUP_SUFFIX)]
        is_portrait_backup = True

    return {
        "canonical": f"{base}{SUBBED_SUFFIX if is_subbed else ''}",
        "is_danmaku": is_danmaku,
        "is_portrait_backup": is_portrait_backup,
        "ext": ext.lower(),
    }


def _version_priority(path):
    info = _split_video_version(path)
    if info["is_danmaku"]:
        rank = 0
    elif info["is_portrait_backup"]:
        rank = 3
    else:
        rank = 1

    ext_rank = {
        ".mp4": 0,
        ".mkv": 1,
        ".mov": 2,
        ".avi": 3,
        ".wmv": 4,
        ".flv": 5,
    }.get(info["ext"], 9)
    return rank, ext_rank, os.path.basename(path).lower()


def filter_danmaku_versions(paths):
    """
    从视频路径列表中过滤出更优版本。

    Args:
        paths: 文件绝对路径列表

    Returns:
        过滤后的路径列表。弹幕版对应的普通版、横屏主视频对应的竖屏备份会被移除；
        独立文件（无更优版本对应）原样保留。

    Example:
        输入: ['/a/运动一会儿.mp4', '/a/运动一会儿-弹幕版.mp4', '/a/闲聊.mp4']
        输出: ['/a/运动一会儿-弹幕版.mp4', '/a/闲聊.mp4']
    """
    if not paths:
        return paths

    # 按目录分组处理
    by_dir = {}
    for p in paths:
        d = os.path.dirname(p)
        by_dir.setdefault(d, []).append(p)

    result = []
    for d, files in by_dir.items():
        grouped = {}
        for f in files:
            info = _split_video_version(f)
            grouped.setdefault(info["canonical"], []).append(f)

        for _, candidates in grouped.items():
            ordered = sorted(candidates, key=_version_priority)
            chosen = ordered[0]
            result.append(chosen)
            for skipped in ordered[1:]:
                chosen_name = os.path.splitext(os.path.basename(chosen))[0]
                skipped_name = os.path.splitext(os.path.basename(skipped))[0]
                print(f'[视频优先] 使用 {chosen_name}，跳过 {skipped_name}')

    return sorted(result, key=_version_priority)


def filter_danmaku_versions_in_dir(dir_path, filenames):
    """
    对指定目录下的文件名列表进行弹幕版过滤。

    Args:
        dir_path: 文件所在目录
        filenames: 纯文件名列表（不含路径）

    Returns:
        过滤后的文件名列表
    """
    full_paths = [os.path.join(dir_path, f) for f in filenames]
    filtered = filter_danmaku_versions(full_paths)
    return [os.path.basename(f) for f in filtered]
