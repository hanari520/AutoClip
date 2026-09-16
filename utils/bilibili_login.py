#!/usr/bin/env python3
"""
Bilibili 扫码登录 + 授权管理模块
二维码登录流程参考 biliLive-tools 的登录方式，基于 bilibili-api 的 login_v2.QrCodeLogin
"""

import os
import json
import asyncio
import threading
import logging
from typing import Optional, Callable

from bilibili_api import login_v2, Credential
from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginChannel, QrCodeLoginEvents

logger = logging.getLogger("bilibili_login")

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONFIG_FILE = os.path.join(_PROJECT_ROOT, "configs", "gui_config.json")


# ═══════════════════════════════════════════════════════════════════
# 凭据读写
# ═══════════════════════════════════════════════════════════════════

def load_credentials_from_config(config_path: Optional[str] = None) -> Optional[Credential]:
    """从 gui_config.json 加载 B站凭据，返回 Credential 对象（或 None）"""
    path = config_path or _CONFIG_FILE
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)
    bili = config.get("bilibili", {})
    if not bili or not bili.get("sessdata"):
        return None
    return Credential(
        sessdata=bili.get("sessdata", ""),
        bili_jct=bili.get("bili_jct", ""),
        buvid3=bili.get("buvid3", ""),
        buvid4=bili.get("buvid4", ""),
        dedeuserid=bili.get("dedeuserid", ""),
        ac_time_value=bili.get("ac_time_value", ""),
    )


def save_credential_to_config(
    credential: Credential,
    config_path: Optional[str] = None
) -> None:
    """将凭据保存到 gui_config.json 的 bilibili 段"""
    path = config_path or _CONFIG_FILE
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {}

    config["bilibili"] = {
        "sessdata": getattr(credential, "sessdata", ""),
        "bili_jct": getattr(credential, "bili_jct", ""),
        "buvid3": getattr(credential, "buvid3", ""),
        "buvid4": getattr(credential, "buvid4", ""),
        "dedeuserid": getattr(credential, "dedeuserid", ""),
        "ac_time_value": getattr(credential, "ac_time_value", ""),
    }

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════════
# 扫码登录（同步封装，给 GUI 调用）
# ═══════════════════════════════════════════════════════════════════

async def _ensure_buvid(credential: Credential) -> bool:
    """凭据缺 buvid3 时从 B站获取并补上。

    TV 扫码登录不返回 buvid3（设备 ID），而投稿提交接口缺失该字段
    会被 B站风控以 HTTP 406 拒绝。"""
    if getattr(credential, "buvid3", None):
        return False
    from bilibili_api.utils.network import get_buvid

    buvid3, buvid4 = await get_buvid()
    credential.buvid3 = buvid3
    credential.buvid4 = buvid4
    return True

class BilibiliQRLogin:
    """B站二维码登录器（同步封装）

    用法::

        login = BilibiliQRLogin()
        # 1. 生成二维码
        qr_img_path, qr_terminal = login.generate()
        # 2. 展示二维码（图片路径在 qr_img_path）
        # 3. 轮询状态
        state = login.check()
        if state == "done":
            cred = login.get_credential()
            save_credential_to_config(cred)
        elif state == "timeout":
            # 二维码过期，重新生成
    """

    def __init__(self, channel: str = "tv"):
        # B站已不再把 cookie 放在 WEB 扫码回跳 URL 参数里（解析结果为空），默认走 TV 渠道
        self._channel = QrCodeLoginChannel.WEB if channel == "web" else QrCodeLoginChannel.TV
        self._login: Optional[QrCodeLogin] = None
        self._qr_img_path: str = ""
        self._qr_terminal: str = ""
        self._credential: Optional[Credential] = None

    def generate(self) -> tuple:
        """生成登录二维码，返回 (图片路径, 终端字符串)"""
        async def _gen():
            self._login = QrCodeLogin(platform=self._channel)
            await self._login.generate_qrcode()
            # 获取图片路径 & 终端字符串
            pic = self._login.get_qrcode_picture()
            terminal = self._login.get_qrcode_terminal()
            return pic, terminal

        loop = asyncio.new_event_loop()
        try:
            pic, terminal = loop.run_until_complete(_gen())
            # 图片存储到临时目录
            import tempfile
            qr_img_path = os.path.join(tempfile.gettempdir(), "bilibili_qrcode.png")
            pic.to_file(qr_img_path)
            self._qr_img_path = qr_img_path
            self._qr_terminal = terminal
            return qr_img_path, terminal
        finally:
            loop.close()

    def check(self) -> str:
        """检查扫码状态，返回: 'scan' | 'confirm' | 'timeout' | 'done'"""
        if not self._login:
            return "error"

        async def _check():
            event = await self._login.check_state()
            if event == QrCodeLoginEvents.DONE:
                self._credential = self._login.get_credential()
                try:
                    await _ensure_buvid(self._credential)
                except Exception:
                    pass
            return event

        loop = asyncio.new_event_loop()
        try:
            event = loop.run_until_complete(_check())
            if event == QrCodeLoginEvents.DONE:
                return "done"
            elif event == QrCodeLoginEvents.SCAN:
                return "scan"
            elif event == QrCodeLoginEvents.CONF:
                return "confirm"
            elif event == QrCodeLoginEvents.TIMEOUT:
                return "timeout"
            return "unknown"
        finally:
            loop.close()

    def get_credential(self) -> Optional[Credential]:
        """获取登录成功的凭据（仅在 done 后有效）"""
        return self._credential

    def get_qr_image_path(self) -> str:
        """获取二维码图片路径"""
        return self._qr_img_path

    def get_qr_terminal(self) -> str:
        """获取终端二维码字符串（在 console 里打印用）"""
        return self._qr_terminal


# ═══════════════════════════════════════════════════════════════════
# 凭据验证 & 账户信息
# ═══════════════════════════════════════════════════════════════════

def check_login_status(credential: Credential) -> dict:
    """验证凭据是否有效，返回账户信息"""
    async def _check():
        from bilibili_api import user
        try:
            # 先检查 cookie 是否需要刷新
            if hasattr(credential, 'check_refresh'):
                try:
                    await credential.check_refresh()
                except Exception:
                    pass
            info = await user.get_self_info(credential)
            return {
                "valid": True,
                "uid": info.get("mid", 0),
                "uname": info.get("name", "未知"),
                "face": info.get("face", ""),
                "level": info.get("level", 0),
                "vip_status": info.get("vip", {}).get("status", 0),
                "vip_type": info.get("vip", {}).get("type", 0),
                "coins": info.get("coins", 0),
                "level_exp": info.get("level_exp", {}),
            }
        except Exception as e:
            return {
                "valid": False,
                "error": str(e)[:200],
            }

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_check())
    finally:
        loop.close()


def get_cookie_status_text() -> str:
    """快速检查当前保存的 cookie 是否有效，返回状态文本"""
    cred = load_credentials_from_config()
    if not cred:
        return "未配置 B站登录凭据"
    info = check_login_status(cred)
    if info.get("valid"):
        level_info = f"Lv.{info['level']}"
        if info.get("vip_status"):
            level_info += " 大会员"
        return f"✅ {info['uname']} ({level_info}) — Cookie 有效"
    else:
        return f"❌ Cookie 已失效 — {info.get('error', '未知错误')}"


def check_and_refresh_credential(config_path: Optional[str] = None) -> dict:
    """检查凭据是否有效，若已失效尝试通过 ac_time_value 刷新

    返回:
        {"status": "ok", "uname": "...", "level": ...}  — 有效无需刷新
        {"status": "refreshed", "uname": "..."}          — 已刷新成功
        {"status": "expired", "error": "..."}             — 已过期需重新登录
        {"status": "not_configured"}                      — 未配置
    """
    path = config_path or _CONFIG_FILE
    cred = load_credentials_from_config(path)
    if not cred:
        return {"status": "not_configured"}

    # 补齐缺失的 buvid3 并持久化（旧登录流程保存的凭据可能没有该字段）
    try:
        loop = asyncio.new_event_loop()
        try:
            changed = loop.run_until_complete(_ensure_buvid(cred))
        finally:
            loop.close()
        if changed:
            save_credential_to_config(cred, path)
    except Exception:
        pass

    # 先尝试直接用
    info = check_login_status(cred)
    if info.get("valid"):
        return {
            "status": "ok",
            "uname": info.get("uname", "未知"),
            "uid": info.get("uid", 0),
            "level": info.get("level", 0),
            "vip_status": info.get("vip_status", 0),
        }

    # 尝试用 ac_time_value 刷新（bilibili-api 内置）
    async def _try_refresh():
        try:
            await cred.check_refresh()
            return True
        except Exception:
            return False

    loop = asyncio.new_event_loop()
    try:
        refreshed = loop.run_until_complete(_try_refresh())
    finally:
        loop.close()

    if refreshed:
        # 刷新成功，保存新的凭据
        save_credential_to_config(cred, path)
        info2 = check_login_status(cred)
        if info2.get("valid"):
            return {
                "status": "refreshed",
                "uname": info2.get("uname", "未知"),
                "uid": info2.get("uid", 0),
                "level": info2.get("level", 0),
                "vip_status": info2.get("vip_status", 0),
            }

    return {
        "status": "expired",
        "error": info.get("error", "凭据已过期，需要重新登录"),
    }


# ═══════════════════════════════════════════════════════════════════
# 命令行入口（方便测试）
# ═══════════════════════════════════════════════════════════════════

def login_cli():
    """命令行交互式扫码登录"""
    print("[Bilibili 扫码登录]")
    print("正在生成二维码...")
    
    login = BilibiliQRLogin()
    qr_path, terminal = login.generate()
    
    print()
    print(terminal)
    print()
    print(f"二维码图片已保存到: {qr_path}")
    print("请用 Bilibili APP 扫码登录")
    print()

    import time
    last_state = ""
    while True:
        state = login.check()
        if state != last_state:
            if state == "scan":
                print("📱 已扫描，请在手机上确认...")
            elif state == "confirm":
                print("✅ 已确认，正在获取登录信息...")
            elif state == "timeout":
                print("⏰ 二维码已过期，请重新生成")
                break
            elif state == "done":
                print("🎉 登录成功！")
                cred = login.get_credential()
                save_credential_to_config(cred)
                print(f"凭据已保存到 {_CONFIG_FILE}")
                info = check_login_status(cred)
                if info.get("valid"):
                    print(f"当前账号: {info['uname']} (UID: {info['uid']}, Lv.{info['level']})")
                break
            last_state = state
        time.sleep(0.5)  # 浏览器扫码推荐1~2秒


if __name__ == "__main__":
    login_cli()
