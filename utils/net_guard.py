# -*- coding: utf-8 -*-
"""出站 URL 安全校验。

服务端向外发起 HTTP 请求前调用 ``validate_outbound_url``：
仅允许 http/https，并拒绝 localhost、环回、私有、保留、链路本地、
多播与未指定地址（IP 字面量与域名解析结果都检查），避免被当作 SSRF 跳板。
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Optional, Union
from urllib.parse import urlparse

__all__ = ["UnsafeUrlError", "validate_outbound_url"]


class UnsafeUrlError(ValueError):
    """URL 未通过出站安全校验。"""


def _is_forbidden_ip(ip: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # 非全局单播一律拒绝：覆盖 private/loopback/link-local/reserved/multicast/
    # unspecified、CGNAT(100.64/10)、文档网段等所有特殊用途地址。
    if not ip.is_global:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local)
    )


def validate_outbound_url(url: str, *, resolve: bool = True) -> str:
    """校验 URL 是否可以安全出站请求。

    通过时原样返回 URL；不通过则抛 ``UnsafeUrlError``（附带原因）。
    ``resolve=False`` 时跳过 DNS 解析检查，仅校验协议与 IP 字面量。
    """
    raw = str(url or "").strip()
    if not raw:
        raise UnsafeUrlError("URL 为空")

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"仅允许 http/https，收到: {parsed.scheme or '(空)'}")

    host = parsed.hostname
    if not host:
        raise UnsafeUrlError(f"URL 缺少主机名: {raw}")

    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(".localhost"):
        raise UnsafeUrlError(f"拒绝 localhost 目标: {host}")

    try:
        literal: Optional[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]] = ipaddress.ip_address(lowered)
    except ValueError:
        literal = None

    if literal is not None:
        if _is_forbidden_ip(literal):
            raise UnsafeUrlError(f"拒绝非公网地址: {host}")
        return raw

    if not resolve:
        return raw

    try:
        infos = socket.getaddrinfo(lowered, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        # fail closed：无法验证的目标一律拒绝
        raise UnsafeUrlError(f"域名无法解析，拒绝未验证目标: {host} ({exc})") from exc

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise UnsafeUrlError(f"域名未解析到任何地址: {host}")

    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])
        except ValueError as exc:
            raise UnsafeUrlError(f"无法解析的地址: {addr}") from exc
        if _is_forbidden_ip(ip):
            raise UnsafeUrlError(f"域名 {host} 解析到非公网地址 {addr}，已拒绝")

    return raw
