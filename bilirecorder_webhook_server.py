#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BililiveRecorder Webhook -> ServerChan bridge.

Run this service locally and set BililiveRecorder Webhook v2 to:
http://127.0.0.1:18010/webhook/mikufansrecorder
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "serverchan_config.json"

sys.path.insert(0, str(PROJECT_ROOT))

from utils.net_guard import UnsafeUrlError, validate_outbound_url  # noqa: E402


DEFAULT_LOG = PROJECT_ROOT / "bilirecorder_webhook_events.jsonl"
DEFAULT_COOKIE_BACKUP_DIR = PROJECT_ROOT / "backups" / "bilirecorder_cookie"
DEFAULT_WEBHOOK_PATH = "/webhook/mikufansrecorder"


DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": True,
    "sendkey": "",
    "serverchan_url": "",
    "webhook_path": DEFAULT_WEBHOOK_PATH,
    "notify_events": ["StreamStarted", "StreamEnded", "SessionEnded"],
    "dedupe_seconds": 90,
    "timeout_seconds": 10,
    "log_file": str(DEFAULT_LOG),
    "title_templates": {
        "StreamStarted": "开播: {name} - {title}",
        "StreamEnded": "下播: {name}",
        "SessionEnded": "录制结束: {name} - {title}",
        "SessionStarted": "开始录制: {name} - {title}",
        "FileClosed": "录制文件完成: {name}",
    },
    "cookie_monitor": {
        "enabled": True,
        "recorder_config_path": "",
        "check_on_start": True,
        "interval_seconds": 21600,
        "notify_cooldown_seconds": 43200,
        "backup_on_start": True,
        "backup_dir": str(DEFAULT_COOKIE_BACKUP_DIR),
        "nav_api": "https://api.bilibili.com/x/web-interface/nav",
        "refresh_from_autoclip": True,
        "autoclip_config_path": str(PROJECT_ROOT / "configs" / "gui_config.json"),
        "auto_update": False,
        "cookie_source_path": str(PROJECT_ROOT / "configs" / "bilirecorder_cookie_source.txt"),
    },
}


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class NotifyConfig:
    def __init__(self, path: Path, sendkey_override: str = "") -> None:
        self.path = path
        self.settings = self._load(path)
        if sendkey_override:
            self.settings["sendkey"] = sendkey_override
        env_sendkey = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
        if env_sendkey:
            self.settings["sendkey"] = env_sendkey

    @staticmethod
    def _load(path: Path) -> Dict[str, Any]:
        settings = dict(DEFAULT_SETTINGS)
        settings["title_templates"] = dict(DEFAULT_SETTINGS["title_templates"])
        settings["cookie_monitor"] = dict(DEFAULT_SETTINGS["cookie_monitor"])
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            for key, value in data.items():
                if key == "title_templates" and isinstance(value, dict):
                    settings["title_templates"].update(value)
                elif key == "cookie_monitor" and isinstance(value, dict):
                    settings["cookie_monitor"].update(value)
                else:
                    settings[key] = value
        return settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", True))

    @property
    def notify_events(self) -> set[str]:
        return set(self.settings.get("notify_events") or [])

    @property
    def dedupe_seconds(self) -> int:
        return int(self.settings.get("dedupe_seconds") or 90)

    @property
    def timeout_seconds(self) -> int:
        return int(self.settings.get("timeout_seconds") or 10)

    @property
    def log_file(self) -> Path:
        return Path(self.settings.get("log_file") or DEFAULT_LOG)

    @property
    def webhook_path(self) -> str:
        path = str(self.settings.get("webhook_path") or DEFAULT_WEBHOOK_PATH).strip()
        if not path.startswith("/"):
            path = "/" + path
        return path

    def serverchan_endpoint(self) -> str:
        explicit_url = str(self.settings.get("serverchan_url") or "").strip()
        if explicit_url:
            return explicit_url
        sendkey = str(self.settings.get("sendkey") or "").strip()
        if not sendkey:
            return ""
        if sendkey.startswith("http://") or sendkey.startswith("https://"):
            return sendkey
        return f"https://sctapi.ftqq.com/{sendkey}.send"


class WebhookBridge:
    def __init__(self, config: NotifyConfig) -> None:
        self.config = config
        self._recent: Dict[Tuple[str, str, str], float] = {}

    def _safe_endpoint(self) -> Tuple[str, str]:
        """返回 (endpoint, reason)；reason 非空表示该地址不可出站请求。"""
        endpoint = self.config.serverchan_endpoint()
        if not endpoint:
            return "", "missing_sendkey"
        try:
            validate_outbound_url(endpoint)
        except UnsafeUrlError as exc:
            print(f"[AutoClip] 拒绝出站通知地址: {exc}")
            return "", "unsafe_endpoint"
        return endpoint, ""

    def handle_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        event_type = str(payload.get("EventType") or "")
        event_data = payload.get("EventData") or {}
        if not isinstance(event_data, dict):
            event_data = {}

        summary = self._event_summary(event_type, event_data)
        self._write_log(payload, summary)

        if not self.config.enabled:
            return {"ok": True, "notified": False, "reason": "disabled", **summary}
        if event_type not in self.config.notify_events:
            return {"ok": True, "notified": False, "reason": "event_not_enabled", **summary}
        if self._is_duplicate(event_type, event_data):
            return {"ok": True, "notified": False, "reason": "duplicate", **summary}

        endpoint, reason = self._safe_endpoint()
        if not endpoint:
            return {"ok": True, "notified": False, "reason": reason, **summary}

        title, desp = self._format_message(event_type, event_data, payload)
        self._send_serverchan(endpoint, title, desp)
        return {"ok": True, "notified": True, **summary}

    def _is_duplicate(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        now = time.time()
        ttl = max(1, self.config.dedupe_seconds)
        expired = [key for key, ts in self._recent.items() if now - ts > ttl]
        for key in expired:
            self._recent.pop(key, None)

        room_id = str(event_data.get("RoomId") or "")
        session_id = str(event_data.get("SessionId") or "")
        title = str(event_data.get("Title") or "")
        key = (event_type, room_id, session_id or title)
        if key in self._recent:
            return True
        self._recent[key] = now
        return False

    def _format_message(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Tuple[str, str]:
        room_id = event_data.get("RoomId") or ""
        name = event_data.get("Name") or f"房间 {room_id}"
        live_title = event_data.get("Title") or ""
        area_parent = event_data.get("AreaNameParent") or ""
        area_child = event_data.get("AreaNameChild") or ""
        relative_path = event_data.get("RelativePath") or ""
        event_time = payload.get("EventTimestamp") or datetime.now().isoformat(timespec="seconds")
        room_url = f"https://live.bilibili.com/{room_id}" if room_id else ""

        template = self.config.settings.get("title_templates", {}).get(event_type)
        if not template:
            template = "{event_type}: {name} - {title}"
        title = template.format(
            event_type=event_type,
            name=name,
            title=live_title,
            room_id=room_id,
            area=f"{area_parent}/{area_child}".strip("/"),
        )

        lines = [
            f"事件: {event_type}",
            f"主播: {name}",
            f"标题: {live_title or '-'}",
            f"房间: {room_id or '-'}",
            f"分区: {('/'.join(x for x in [area_parent, area_child] if x)) or '-'}",
            f"时间: {event_time}",
        ]
        if room_url:
            lines.append(f"直播间: {room_url}")
        if relative_path:
            lines.append(f"文件: {relative_path}")
        return title, "\n\n".join(lines)

    def _send_serverchan(self, endpoint: str, title: str, desp: str) -> None:
        response = requests.post(
            endpoint,
            data={"title": title, "desp": desp},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()

    def send_notice(self, title: str, desp: str) -> bool:
        endpoint, _reason = self._safe_endpoint()
        if not self.config.enabled or not endpoint:
            return False
        self._send_serverchan(endpoint, title, desp)
        return True

    def _event_summary(self, event_type: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "event_type": event_type,
            "room_id": event_data.get("RoomId"),
            "name": event_data.get("Name"),
            "title": event_data.get("Title"),
        }

    def _write_log(self, payload: Dict[str, Any], summary: Dict[str, Any]) -> None:
        log_file = self.config.log_file
        log_file.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            **summary,
            "event_id": payload.get("EventId"),
            "event_timestamp": payload.get("EventTimestamp"),
        }
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class CookieMonitor:
    def __init__(self, config: NotifyConfig, bridge: WebhookBridge) -> None:
        self.config = config
        self.bridge = bridge
        self.settings = config.settings.get("cookie_monitor") or {}
        self._stop_event = threading.Event()
        self._last_notice_at = 0.0
        self._last_cookie_hash = ""

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", False))

    def start(self) -> None:
        if not self.enabled:
            print("[AutoClip] Cookie monitor: disabled")
            return
        thread = threading.Thread(target=self._run_loop, name="CookieMonitor", daemon=True)
        thread.start()
        print("[AutoClip] Cookie monitor: enabled")

    def stop(self) -> None:
        self._stop_event.set()

    def _run_loop(self) -> None:
        if self.settings.get("check_on_start", True):
            self.check_once(notify=True)

        interval = max(300, int(self.settings.get("interval_seconds") or 21600))
        while not self._stop_event.wait(interval):
            self.check_once(notify=True)

    def check_once(self, notify: bool = True) -> Dict[str, Any]:
        recorder_config = Path(str(self.settings.get("recorder_config_path") or ""))
        try:
            cookie = self._read_cookie(recorder_config)
            if self.settings.get("backup_on_start", True):
                self._backup_config_if_changed(recorder_config, cookie)
            status = self._check_bilibili_nav(cookie)
        except Exception as exc:
            status = {
                "ok": False,
                "is_login": False,
                "reason": str(exc),
                "path": str(recorder_config),
            }

        self._write_cookie_status(status)
        if not status.get("is_login"):
            update_status = self._try_refresh_from_autoclip(recorder_config)
            if update_status:
                self._write_cookie_status(update_status)
                status = update_status
        if not status.get("is_login"):
            update_status = self._try_auto_update(recorder_config)
            if update_status:
                self._write_cookie_status(update_status)
                status = update_status
        if notify and not status.get("is_login"):
            self._notify_invalid(status)
        print(
            "[AutoClip] Cookie monitor:",
            "login ok" if status.get("is_login") else f"login invalid ({status.get('reason')})",
        )
        return status

    def _read_cookie(self, recorder_config: Path) -> str:
        if not recorder_config.exists():
            raise FileNotFoundError(f"recorder config not found: {recorder_config}")
        with recorder_config.open("r", encoding="utf-8") as f:
            data = json.load(f)
        cookie_node = data.get("global", {}).get("Cookie", {})
        cookie = str(cookie_node.get("Value") or "").strip()
        if not cookie:
            raise ValueError("recorder cookie is empty")
        return cookie

    def _write_cookie(self, recorder_config: Path, cookie: str) -> None:
        with recorder_config.open("r", encoding="utf-8") as f:
            data = json.load(f)
        global_node = data.setdefault("global", {})
        cookie_node = global_node.setdefault("Cookie", {})
        cookie_node["HasValue"] = True
        cookie_node["Value"] = cookie
        with recorder_config.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    def _parse_cookie_pairs(self, cookie: str) -> Dict[str, str]:
        pairs: Dict[str, str] = {}
        for item in cookie.replace("\n", ";").split(";"):
            item = item.strip()
            if not item or "=" not in item:
                continue
            key, value = item.split("=", 1)
            pairs[key.strip()] = value.strip()
        return pairs

    def _format_cookie_pairs(self, pairs: Dict[str, str]) -> str:
        return "; ".join(f"{key}={value}" for key, value in pairs.items() if value is not None)

    def _build_cookie_from_autoclip(self, base_cookie: str, autoclip_bili: Dict[str, Any]) -> str:
        pairs = self._parse_cookie_pairs(base_cookie)
        mapping = {
            "SESSDATA": autoclip_bili.get("sessdata"),
            "bili_jct": autoclip_bili.get("bili_jct"),
            "buvid3": autoclip_bili.get("buvid3"),
            "DedeUserID": autoclip_bili.get("dedeuserid"),
        }
        for key, value in mapping.items():
            if value:
                pairs[key] = str(value)
        return self._format_cookie_pairs(pairs)

    def _check_bilibili_nav(self, cookie: str) -> Dict[str, Any]:
        api = str(self.settings.get("nav_api") or DEFAULT_SETTINGS["cookie_monitor"]["nav_api"])
        try:
            validate_outbound_url(api)
        except UnsafeUrlError as exc:
            return {
                "ok": False,
                "is_login": False,
                "reason": f"unsafe nav_api: {exc}",
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }
        response = requests.get(
            api,
            headers={
                "Cookie": cookie,
                "User-Agent": "Mozilla/5.0 AutoClip BililiveRecorder Cookie Monitor",
                "Referer": "https://www.bilibili.com/",
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        is_login = bool(data.get("isLogin"))
        return {
            "ok": payload.get("code") == 0,
            "is_login": is_login,
            "code": payload.get("code"),
            "message": payload.get("message"),
            "uname": data.get("uname") if is_login else "",
            "mid": data.get("mid") if is_login else "",
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _try_auto_update(self, recorder_config: Path) -> Optional[Dict[str, Any]]:
        if not self.settings.get("auto_update", False):
            return None
        source_path = Path(str(self.settings.get("cookie_source_path") or ""))
        try:
            if not source_path.exists():
                raise FileNotFoundError(f"cookie source not found: {source_path}")
            new_cookie = source_path.read_text(encoding="utf-8").strip()
            if not new_cookie:
                raise ValueError("cookie source is empty")
            check = self._check_bilibili_nav(new_cookie)
            if not check.get("is_login"):
                raise ValueError(f"source cookie invalid: {check.get('message') or check.get('code')}")
            self._backup_config_if_changed(recorder_config, self._read_cookie(recorder_config))
            self._write_cookie(recorder_config, new_cookie)
            self._backup_config_if_changed(recorder_config, new_cookie)
            notice = "\n\n".join(
                [
                    "录播姬 Cookie 已从受控同步源自动更新。",
                    f"账号: {check.get('uname') or '-'}",
                    f"MID: {check.get('mid') or '-'}",
                    f"配置: {recorder_config}",
                    "建议: 如果录播姬已经打开，请重启录播姬让新 Cookie 完全生效。",
                ]
            )
            try:
                self.bridge.send_notice("录播姬 Cookie 已自动更新", notice)
            except Exception as exc:
                print(f"[AutoClip] Cookie auto-update notice failed: {exc}")
            return {
                **check,
                "auto_updated": True,
                "reason": "updated_from_cookie_source",
                "path": str(recorder_config),
            }
        except Exception as exc:
            return {
                "ok": False,
                "is_login": False,
                "auto_updated": False,
                "reason": f"auto_update_failed: {exc}",
                "path": str(recorder_config),
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }

    def _try_refresh_from_autoclip(self, recorder_config: Path) -> Optional[Dict[str, Any]]:
        if not self.settings.get("refresh_from_autoclip", True):
            return None
        autoclip_config = Path(str(self.settings.get("autoclip_config_path") or ""))
        try:
            from utils.bilibili_login import check_and_refresh_credential

            result = check_and_refresh_credential(str(autoclip_config))
            if result.get("status") not in {"ok", "refreshed"}:
                raise ValueError(result.get("error") or result.get("status") or "autoclip credential invalid")

            with autoclip_config.open("r", encoding="utf-8") as f:
                data = json.load(f)
            autoclip_bili = data.get("bilibili") or {}
            if not autoclip_bili.get("sessdata") or not autoclip_bili.get("bili_jct"):
                raise ValueError("autoclip credential missing sessdata or bili_jct")

            current_cookie = self._read_cookie(recorder_config)
            new_cookie = self._build_cookie_from_autoclip(current_cookie, autoclip_bili)
            check = self._check_bilibili_nav(new_cookie)
            if not check.get("is_login"):
                raise ValueError(f"merged autoclip cookie invalid: {check.get('message') or check.get('code')}")

            self._backup_config_if_changed(recorder_config, current_cookie)
            self._write_cookie(recorder_config, new_cookie)
            self._backup_config_if_changed(recorder_config, new_cookie)

            notice = "\n\n".join(
                [
                    "录播姬 Cookie 已从 AutoClip B站凭据刷新并写回。",
                    f"AutoClip 刷新状态: {result.get('status')}",
                    f"账号: {check.get('uname') or result.get('uname') or '-'}",
                    f"MID: {check.get('mid') or result.get('uid') or '-'}",
                    f"配置: {recorder_config}",
                    "建议: 如果录播姬已经打开，请重启录播姬让新 Cookie 完全生效。",
                ]
            )
            try:
                self.bridge.send_notice("录播姬 Cookie 已刷新", notice)
            except Exception as exc:
                print(f"[AutoClip] Cookie refresh notice failed: {exc}")

            return {
                **check,
                "auto_updated": True,
                "reason": f"refreshed_from_autoclip:{result.get('status')}",
                "path": str(recorder_config),
            }
        except Exception as exc:
            return {
                "ok": False,
                "is_login": False,
                "auto_updated": False,
                "reason": f"refresh_from_autoclip_failed: {exc}",
                "path": str(recorder_config),
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }

    def _backup_config_if_changed(self, recorder_config: Path, cookie: str) -> None:
        digest = hashlib.sha256(cookie.encode("utf-8")).hexdigest()
        backup_dir = Path(str(self.settings.get("backup_dir") or DEFAULT_COOKIE_BACKUP_DIR))
        backup_dir.mkdir(parents=True, exist_ok=True)
        state_file = backup_dir / ".last_cookie_sha256"
        previous_digest = self._last_cookie_hash
        if state_file.exists():
            previous_digest = state_file.read_text(encoding="utf-8").strip()
        if digest == previous_digest:
            self._last_cookie_hash = digest
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = backup_dir / f"config_cookie_backup_{stamp}.json"
        shutil.copy2(recorder_config, target)
        self._last_cookie_hash = digest
        state_file.write_text(digest, encoding="utf-8")

    def _write_cookie_status(self, status: Dict[str, Any]) -> None:
        log_file = self.config.log_file.with_name("bilirecorder_cookie_status.jsonl")
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), **status}
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _notify_invalid(self, status: Dict[str, Any]) -> None:
        now = time.time()
        cooldown = max(300, int(self.settings.get("notify_cooldown_seconds") or 43200))
        if now - self._last_notice_at < cooldown:
            return
        reason = status.get("reason") or status.get("message") or f"code={status.get('code')}"
        desp = "\n\n".join(
            [
                "录播姬 Cookie 登录态检查失败。",
                f"原因: {reason}",
                f"配置: {self.settings.get('recorder_config_path')}",
                "处理: 打开录播姬重新登录/更新 Cookie；更新前建议保留当前配置备份。",
            ]
        )
        try:
            if self.bridge.send_notice("录播姬 Cookie 可能已失效", desp):
                self._last_notice_at = now
        except Exception as exc:
            print(f"[AutoClip] Cookie monitor notice failed: {exc}")


def make_handler(bridge: WebhookBridge, webhook_path: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AutoClipBilirecorderWebhook/1.0"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/health"}:
                self._json(200, {"ok": True, "service": "bilirecorder-webhook", "webhook_path": webhook_path})
                return
            self._json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != webhook_path:
                self._json(404, {"ok": False, "error": "not_found"})
                return

            try:
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode("utf-8")) if raw else {}
                if not isinstance(payload, dict):
                    raise ValueError("payload must be a JSON object")
                result = bridge.handle_payload(payload)
                self._json(200, result)
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})

        def log_message(self, fmt: str, *args: Any) -> None:
            now = datetime.now().strftime("%H:%M:%S")
            print(f"[{now}] {self.address_string()} {fmt % args}")

        def _json(self, status: int, body: Dict[str, Any]) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BililiveRecorder webhook to ServerChan bridge")
    parser.add_argument("--host", default="127.0.0.1", help="listen host")
    parser.add_argument("--port", type=int, default=18010, help="listen port")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="config json path")
    parser.add_argument("--sendkey", default="", help="ServerChan sendkey override")
    parser.add_argument("--webhook-path", default="", help="webhook path override, for example /webhook/mikufansrecorder")
    parser.add_argument("--check-cookie-once", action="store_true", help="check recorder cookie and exit")
    parser.add_argument("--notify-cookie-check", action="store_true", help="send ServerChan notice if one-shot cookie check fails")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = NotifyConfig(Path(args.config), args.sendkey)
    bridge = WebhookBridge(config)
    cookie_monitor = CookieMonitor(config, bridge)
    if args.check_cookie_once:
        status = cookie_monitor.check_once(notify=args.notify_cookie_check)
        printable = {k: v for k, v in status.items() if k not in {"cookie"}}
        print(json.dumps(printable, ensure_ascii=False, indent=2))
        return 0 if status.get("is_login") else 2

    webhook_path = args.webhook_path.strip() if args.webhook_path else config.webhook_path
    if not webhook_path.startswith("/"):
        webhook_path = "/" + webhook_path

    handler = make_handler(bridge, webhook_path)
    try:
        server = ReusableThreadingHTTPServer((args.host, args.port), handler)
    except PermissionError as exc:
        print(f"[AutoClip] Failed to listen on {args.host}:{args.port}: {exc}")
        print("[AutoClip] Windows refused this port. Wait a few seconds and retry.")
        print("[AutoClip] If it keeps failing, change both the recorder Webhook v2 URL and this script's --port.")
        return 1
    except OSError as exc:
        print(f"[AutoClip] Failed to listen on {args.host}:{args.port}: {exc}")
        print("[AutoClip] Check whether another process is already using this port.")
        return 1

    endpoint = config.serverchan_endpoint()
    print(f"[AutoClip] BililiveRecorder webhook server: http://{args.host}:{args.port}{webhook_path}")
    print(f"[AutoClip] Config: {config.path}")
    print(f"[AutoClip] ServerChan: {'configured' if endpoint else 'missing sendkey'}")
    cookie_monitor.start()
    print("[AutoClip] Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[AutoClip] Stopped.")
    finally:
        cookie_monitor.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
