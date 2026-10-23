import logging
import socket
import ssl
import threading
import time
from typing import Any

import aiohttp.connector as aiohttp_connector

from astrbot.utils.http_ssl_common import build_ssl_context_with_certifi

logger = logging.getLogger(__name__)

# 移动网络/公共 DNS 被拦截时，glibc 偶发返回 EAI_AGAIN（Temporary failure in
# name resolution）。aiohttp 每次重连都会重新解析，失败一次整条连接就断，
# 表现为 QQ 机器人一直卡在“Websocket session starting”循环里。
_DNS_RETRY_ATTEMPTS = 3
_DNS_RETRY_DELAY = 0.4
_DNS_CACHE_TTL = 60.0

_dns_cache: dict[tuple[Any, ...], tuple[float, list[Any]]] = {}
_dns_cache_lock = threading.Lock()
_getaddrinfo_orig = socket.getaddrinfo
_dns_patch_installed = False


def _getaddrinfo_with_retry(
    host: Any,
    port: Any,
    family: int = 0,
    type: int = 0,
    proto: int = 0,
    flags: int = 0,
) -> list[Any]:
    """对 EAI_AGAIN 做短重试并缓存成功结果，其余错误原样抛出。"""
    key = (host, port, family, type, proto, flags)
    now = time.monotonic()
    with _dns_cache_lock:
        cached = _dns_cache.get(key)
    if cached is not None and now - cached[0] < _DNS_CACHE_TTL:
        return cached[1]

    last_exc: BaseException | None = None
    for attempt in range(_DNS_RETRY_ATTEMPTS):
        try:
            result = _getaddrinfo_orig(host, port, family, type, proto, flags)
        except socket.gaierror as exc:
            last_exc = exc
            if exc.errno != socket.EAI_AGAIN or attempt + 1 >= _DNS_RETRY_ATTEMPTS:
                raise
            time.sleep(_DNS_RETRY_DELAY * (attempt + 1))
            continue

        with _dns_cache_lock:
            if len(_dns_cache) > 512:
                # 域名数量异常多时直接清空，避免长期运行内存无限增长
                _dns_cache.clear()
            _dns_cache[key] = (time.monotonic(), result)
            if len(_dns_cache) > 256:
                cutoff = time.monotonic() - _DNS_CACHE_TTL
                for cached_key, (cached_at, _) in list(_dns_cache.items()):
                    if cached_at < cutoff:
                        _dns_cache.pop(cached_key, None)
        return result

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("getaddrinfo failed without exception")


def install_dns_retry_patch(log_obj: Any | None = None) -> bool:
    """全局接管 socket.getaddrinfo（asyncio 的线程池解析也会走到这里）。"""
    global _dns_patch_installed
    if _dns_patch_installed:
        return True
    log = log_obj or logger
    try:
        socket.getaddrinfo = _getaddrinfo_with_retry  # type: ignore[assignment]
        _dns_patch_installed = True
        log.debug(
            "DNS 解析已启用 EAI_AGAIN 重试（%s 次）与 %ss 缓存",
            _DNS_RETRY_ATTEMPTS,
            int(_DNS_CACHE_TTL),
        )
        return True
    except Exception as exc:
        log.error("Failed to install DNS retry patch: %r", exc)
        return False


def install_psutil_boot_time_fallback(log_obj: Any | None = None) -> bool:
    """Android proot 下 /proc/stat 可能缺少 btime。

    SELinux 会过滤 /proc/stat，App 的假 /proc/stat 也可能没绑上，psutil 读不到
    btime 就抛 RuntimeError，导致重启前清理子进程、状态插件等全部失败。
    这里用 /proc/uptime 反推 boot_time 作为兜底。
    """
    try:
        import psutil
        from psutil import _pslinux
    except Exception:
        # 非 Linux 平台没有 _pslinux，直接跳过
        return False

    log = log_obj or logger
    original_boot_time = _pslinux.boot_time

    def boot_time() -> float:
        try:
            return original_boot_time()
        except Exception:
            try:
                with open("/proc/uptime", encoding="ascii") as f:
                    uptime = float(f.readline().split()[0])
            except Exception:
                uptime = 0.0
            return time.time() - uptime

    try:
        _pslinux.boot_time = boot_time
        psutil.boot_time = boot_time
        log.debug("psutil.boot_time 已启用 /proc/uptime 兜底")
        return True
    except Exception as exc:
        log.error("Failed to install psutil boot_time fallback: %r", exc)
        return False


def _try_patch_aiohttp_ssl_context(
    ssl_context: ssl.SSLContext,
    log_obj: Any | None = None,
) -> bool:
    log = log_obj or logger
    attr_name = "_SSL_CONTEXT_VERIFIED"

    if not hasattr(aiohttp_connector, attr_name):
        log.warning(
            "aiohttp connector does not expose _SSL_CONTEXT_VERIFIED; skipped patch.",
        )
        return False

    current_value = getattr(aiohttp_connector, attr_name, None)
    if current_value is not None and not isinstance(current_value, ssl.SSLContext):
        log.warning(
            "aiohttp connector exposes _SSL_CONTEXT_VERIFIED with unexpected type; skipped patch.",
        )
        return False

    setattr(aiohttp_connector, attr_name, ssl_context)
    return True


def configure_runtime_ca_bundle(log_obj: Any | None = None) -> bool:
    log = log_obj or logger

    try:
        ssl_context = build_ssl_context_with_certifi(log_obj=log)
        return _try_patch_aiohttp_ssl_context(ssl_context, log_obj=log)
    except Exception as exc:
        log.error("Failed to configure runtime CA bundle for aiohttp: %r", exc)
        return False


def initialize_runtime_bootstrap(log_obj: Any | None = None) -> bool:
    ca_ok = configure_runtime_ca_bundle(log_obj=log_obj)
    dns_ok = install_dns_retry_patch(log_obj=log_obj)
    # 兜底补丁，失败不影响主流程（非 Linux 平台会直接跳过）
    install_psutil_boot_time_fallback(log_obj=log_obj)
    return ca_ok and dns_ok
