#!/usr/bin/env python3
"""
Bilibili Video Uploader for AutoClip
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

基于 bilibili-api 库的 B站视频投稿模块。
认证凭据 (SESSDATA / bili_jct / buvid3) 存储在 gui_config.json 的 bilibili 段中。

Usage as module::

    from utils.bilibili_uploader import BilibiliUploader
    uploader = BilibiliUploader()
    uploader.upload("path/to/video.mp4", title="标题", desc="...", tags=["A-SOUL"])

Usage as CLI::

    python utils/bilibili_uploader.py video.mp4 --title "标题" --tags "A-SOUL,切片"
"""

import os
import json
import logging
import asyncio
import argparse
import threading
from typing import Optional, Callable

from bilibili_api import Credential
from bilibili_api.video_uploader import (
    VideoUploader,
    VideoMeta,
    VideoUploaderPage,
    VideoUploaderEvents,
    Lines,
)

# Windows 上 curl_cffi 的 HTTP/2 实现会抛 CURLE_HTTP2_ERROR (16)
# 切换到 httpx 后端绕过，httpx 用 Python 原生 HTTP 栈更稳定
import bilibili_api.utils.network as _bili_net
_bili_net.select_client("httpx")

logger = logging.getLogger("bilibili_uploader")

# 配置文件路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_FILE = os.path.join(_PROJECT_ROOT, "configs", "gui_config.json")


# ═══════════════════════════════════════════════════════════════════
# 凭据管理
# ═══════════════════════════════════════════════════════════════════


def load_credentials(config_path: Optional[str] = None) -> Credential:
    """从 gui_config.json 的 bilibili 段加载登录凭据。

    gui_config.json 中需要存在:
      "bilibili": {
        "sessdata": "xxx",
        "bili_jct": "xxx",
        "buvid3": "xxx",
        "dedeuserid": "xxx"  // 可选
      }
    """
    path = config_path or _CONFIG_FILE
    if not os.path.exists(path):
        raise FileNotFoundError(f"配置文件不存在: {path}")

    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)

    bili = config.get("bilibili", {})
    if not bili or not bili.get("sessdata"):
        raise ValueError(
            "未找到 B站登录凭据。请在 gui_config.json 中添加 bilibili 段，"
            "包含 sessdata / bili_jct / buvid3 字段。"
        )

    return Credential(
        sessdata=bili.get("sessdata", ""),
        bili_jct=bili.get("bili_jct", ""),
        buvid3=bili.get("buvid3", ""),
        buvid4=bili.get("buvid4", ""),
        dedeuserid=bili.get("dedeuserid", ""),
        ac_time_value=bili.get("ac_time_value", ""),
    )


def save_credentials(
    sessdata: str,
    bili_jct: str,
    buvid3: str,
    dedeuserid: str = "",
    config_path: Optional[str] = None,
):
    """保存 B站登录凭据到 gui_config.json。"""
    path = config_path or _CONFIG_FILE

    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {}

    config["bilibili"] = {
        "sessdata": sessdata,
        "bili_jct": bili_jct,
        "buvid3": buvid3,
        "dedeuserid": dedeuserid,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════
# 上传器
# ═══════════════════════════════════════════════════════════════════


class BilibiliUploader:
    """B站视频投稿器，封装 bilibili-api 的 VideoUploader。

    用法::

        uploader = BilibiliUploader()
        uploader.on_progress(lambda event, pct, data: print(event, pct))
        result = uploader.upload(
            video_path="切片.mp4",
            title="【A-SOUL】然然破防名场面",
            desc="嘉然又被乃琳贝拉混合双打了",
            tags=["A-SOUL", "切片", "搞笑"],
            tid=124,
        )
        # result = {"bvid": "BVxxxx", "aid": 123456}

    参数说明:
        video_path:   视频文件路径
        title:        视频标题（≤80字）
        desc:         视频简介（≤2000字）
        tags:         标签列表（≤10个）
        tid:          分区 ID（默认 124 = 生活）
        copyright:    1=原创 2=转载
        source:       转载来源 URL（copyright=2 时填写）
        cover_path:   封面图片路径
        dtime:        定时发布时间戳（秒，不传=立即发布）
    """

    def __init__(self, credential: Optional[Credential] = None):
        self._credential = credential or load_credentials()
        self._progress_cb: Optional[Callable] = None
        self._cancelled = False
        self._current_task: Optional[asyncio.Task] = None

    def on_progress(self, cb: Callable[[str, float, dict], None]):
        """设置进度回调。cb(event: str, progress: float, data: dict)"""
        self._progress_cb = cb

    def cancel(self):
        """取消当前上传。"""
        self._cancelled = True
        if self._current_task:
            self._current_task.cancel()

    # ── 同步入口（给 GUI 调用） ──

    def upload(
        self,
        video_path: str,
        title: str,
        desc: str = "",
        tags: Optional[list] = None,
        tid: int = 124,
        copyright: int = 1,
        source: str = "",
        cover_path: Optional[str] = None,
        dtime: Optional[int] = None,
        no_reprint: int = 1,
        lossless_music: int = 0,
        dolby: int = 0,
        thread_pool_size: int = 3,
    ) -> dict:
        """同步上传入口。

        Returns:
            dict: {"bvid": str, "aid": int} 或上传失败时抛出异常
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        tags = tags or []

        self._cancelled = False

        try:
            result = asyncio.run(
                self._upload_async(
                    video_path=video_path,
                    title=title,
                    desc=desc,
                    tags=tags,
                    tid=tid,
                    copyright=copyright,
                    source=source,
                    cover_path=cover_path,
                    dtime=dtime,
                    no_reprint=no_reprint,
                    lossless_music=lossless_music,
                    dolby=dolby,
                )
            )
            return result

        except asyncio.CancelledError:
            self._emit_progress("cancelled", 0, {})
            raise RuntimeError("用户取消了上传")

        except Exception as e:
            self._emit_progress("error", 0, {"error": str(e)})
            raise

    # ── 异步核心 ──

    async def _upload_async(
        self,
        video_path: str,
        title: str,
        desc: str,
        tags: list,
        tid: int,
        copyright: int,
        source: str,
        cover_path: Optional[str],
        dtime: Optional[int],
        no_reprint: int,
        lossless_music: int,
        dolby: int,
    ) -> dict:
        file_name = os.path.basename(video_path)
        self._emit_progress("start", 0, {"file": file_name})

        # 投稿提交接口需要 buvid3（设备 ID），缺失会被 B站风控以 406 拒绝；
        # TV 扫码登录不返回该字段，这里补取并持久化
        if not self._credential.buvid3:
            from bilibili_api.utils.network import get_buvid

            buvid3, buvid4 = await get_buvid()
            self._credential.buvid3 = buvid3
            self._credential.buvid4 = buvid4
            try:
                from utils.bilibili_login import save_credential_to_config

                save_credential_to_config(self._credential)
            except Exception:
                pass

        # 构建分 P — 单视频只有一个 page
        page = VideoUploaderPage(
            path=video_path,
            title=title,
            description="",
        )

        # 构建元数据
        meta = VideoMeta(
            tid=tid,
            title=title,
            desc=desc,
            cover=cover_path or "",
            tags=tags,
            original=(copyright == 1),
            source=source if copyright == 2 else None,
            no_reprint=(no_reprint == 1),
            lossless_music=(lossless_music == 1),
            dolby=(dolby == 1),
            delay_time=dtime,
        )

        # 创建上传器
        uploader = VideoUploader(
            pages=[page],
            meta=meta,
            credential=self._credential,
            cover=cover_path or "",
        )

        # ── 事件监听 ──

        @uploader.on(VideoUploaderEvents.PREUPLOAD.value)
        async def _on_preupload(data: dict):
            self._emit_progress("preupload", 0.02, {"page": str(data.get("page", ""))})

        @uploader.on(VideoUploaderEvents.PRE_CHUNK.value)
        async def _on_prechunk(data: dict):
            pass  # 各分片开始，静默

        @uploader.on(VideoUploaderEvents.AFTER_CHUNK.value)
        async def _on_afterchunk(data: dict):
            chunk = data.get("chunk_number", 0)
            total = data.get("total_chunk_count", 1)
            pct = 0.05 + 0.85 * ((chunk + 1) / total)
            self._emit_progress("uploading", pct, {
                "chunk": chunk + 1,
                "chunks": total,
            })

        @uploader.on(VideoUploaderEvents.CHUNK_FAILED.value)
        async def _on_chunk_failed(data: dict):
            info = data.get("info", "未知错误")
            logger.warning("分片上传失败 (将重试): %s", info)

        @uploader.on(VideoUploaderEvents.PRE_PAGE_SUBMIT.value)
        async def _on_pre_submit(data: dict):
            self._emit_progress("merging", 0.90, {})

        @uploader.on(VideoUploaderEvents.AFTER_PAGE_SUBMIT.value)
        async def _on_after_submit(data: dict):
            self._emit_progress("merged", 0.93, {})

        @uploader.on(VideoUploaderEvents.PRE_COVER.value)
        async def _on_pre_cover(data: dict):
            self._emit_progress("cover_upload", 0.94, {})

        @uploader.on(VideoUploaderEvents.AFTER_COVER.value)
        async def _on_after_cover(data: dict):
            self._emit_progress("cover_done", 0.95, {})

        @uploader.on(VideoUploaderEvents.PRE_SUBMIT.value)
        async def _on_pre_publish(data: dict):
            self._emit_progress("publishing", 0.96, {})

        @uploader.on(VideoUploaderEvents.COMPLETED.value)
        async def _on_complete(data: dict):
            bvid = data.get("bvid", "?")
            aid = data.get("aid", "?")
            self._emit_progress("done", 1.0, {"bvid": bvid, "aid": aid})

        @uploader.on(VideoUploaderEvents.FAILED.value)
        async def _on_failed(data: dict):
            err = data.get("err", "未知错误")
            self._emit_progress("error", 0, {"error": str(err)})

        # 启动上传
        result = await uploader.start()
        return result

    # ── 内部辅助 ──

    def _emit_progress(self, event: str, progress: float, data: dict):
        if self._progress_cb:
            try:
                self._progress_cb(event, progress, data)
            except Exception as e:
                logger.warning("进度回调异常: %s", e)


# ═══════════════════════════════════════════════════════════════════
# 命令行入口
# ═══════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(description="Bilibili Video Uploader (AutoClip)")
    parser.add_argument("video", help="视频文件路径")
    parser.add_argument("--title", required=True, help="视频标题")
    parser.add_argument("--desc", default="", help="视频简介")
    parser.add_argument("--tags", default="", help="标签，逗号分隔，最多10个")
    parser.add_argument("--tid", type=int, default=124, help="分区 ID (默认 124=生活)")
    parser.add_argument("--copyright", type=int, default=1, help="1=原创 2=转载")
    parser.add_argument("--source", default="", help="转载来源 URL")
    parser.add_argument("--cover", default="", help="封面图片路径")
    parser.add_argument("--dtime", type=int, default=0, help="定时发布时间戳 (0=立即)")
    parser.add_argument("--config", default="", help="配置文件路径")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    def progress_cb(event, pct, data):
        pct_str = f"{pct*100:.1f}%"
        if event == "uploading":
            c = data.get("chunk", 0)
            t = data.get("chunks", 1)
            print(f"\r  上传中... {pct_str} ({c}/{t} 分片)", end="")
        elif event == "done":
            bvid = data.get("bvid", "?")
            aid = data.get("aid", "?")
            print(f"\r投稿成功！BV={bvid} AV={aid}     ")
        elif event == "error":
            print(f"\n投稿失败: {data.get('error')}")
        elif event == "cancelled":
            print("\n已取消")

    config_path = args.config or _CONFIG_FILE
    uploader = BilibiliUploader(credential=load_credentials(config_path))
    uploader.on_progress(progress_cb)

    try:
        result = uploader.upload(
            video_path=args.video,
            title=args.title,
            desc=args.desc,
            tags=tags,
            tid=args.tid,
            copyright=args.copyright,
            source=args.source,
            cover_path=args.cover or None,
            dtime=args.dtime or None,
        )
        print(f"\n发布成功！BV={result.get('bvid')} AV={result.get('aid')}")
        return 0
    except Exception as e:
        logger.error("上传失败: %s", e)
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
