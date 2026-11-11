"""待安装插件压缩包的暂存缓存（按内容哈希寻址）。

覆盖安装需要「先看版本、再真装」两次请求：第一次请求遇到同名插件时会把包
下载/上传到本地并读取包内版本，交给前端弹确认框；用户确认后的第二次请求必须
安装**同一份字节**，否则远程仓库 HEAD 可能已经变化，出现「提示的版本与实际
安装的包不一致」。

因此这里用包的 sha256 作为 token：token 即内容指纹，复用前重算哈希校验，
文件被替换或损坏时会校验失败，由调用方回退到重新获取。
"""

import contextlib
import hashlib
import os
import shutil
import time
from pathlib import Path

from astrbot.core import logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

CACHE_DIRNAME = "plugin-install-cache"
TOKEN_HEX_LEN = 64
DEFAULT_TTL_SECONDS = 24 * 3600

_last_purge_at = 0.0


def cache_root() -> str:
    """返回安装包缓存目录（位于 astrbot 临时目录下，随系统清理自然过期）。"""
    return os.path.join(get_astrbot_temp_path(), CACHE_DIRNAME)


def _ensure_cache_root() -> str:
    root = cache_root()
    os.makedirs(root, mode=0o700, exist_ok=True)
    return root


def _is_valid_token(token: str) -> bool:
    if len(token) != TOKEN_HEX_LEN:
        return False
    return all(char in "0123456789abcdef" for char in token.lower())


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def purge_expired(ttl_seconds: int = DEFAULT_TTL_SECONDS) -> int:
    """清理超期的缓存包，返回删除数量。"""
    root = cache_root()
    if not os.path.isdir(root):
        return 0
    deadline = time.time() - ttl_seconds
    removed = 0
    try:
        entries = list(os.scandir(root))
    except OSError as exc:
        logger.debug(f"扫描插件安装缓存失败: {exc!s}")
        return 0
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            if entry.stat().st_mtime >= deadline:
                continue
            os.remove(entry.path)
            removed += 1
        except OSError as exc:
            logger.debug(f"清理插件安装缓存失败 {entry.path}: {exc!s}")
    return removed


def _maybe_purge() -> None:
    global _last_purge_at
    now = time.time()
    if now - _last_purge_at < 600:
        return
    _last_purge_at = now
    purge_expired()


def store_archive(zip_path: str) -> str:
    """把待安装压缩包移入缓存，返回内容哈希 token。

    Args:
        zip_path: 已下载/已保存到本地的插件压缩包路径。

    Returns:
        该压缩包的 sha256（小写十六进制），后续用它复用同一份字节。
    """
    root = _ensure_cache_root()
    _maybe_purge()
    token = _hash_file(zip_path)
    target_path = os.path.join(root, f"{token}.zip")
    if os.path.exists(target_path):
        # 同内容已在缓存中（并发/重复安装）：直接丢弃本次副本
        with contextlib.suppress(OSError):
            os.remove(zip_path)
        return token
    try:
        shutil.move(zip_path, target_path)
    except Exception as exc:
        logger.warning(f"写入插件安装缓存失败，将改为原地使用: {exc!s}")
        raise
    return token


def resolve_archive(token: str) -> str | None:
    """校验并按 token 取回缓存包路径；校验失败返回 None。

    校验既包括文件名白名单（防止路径穿越），也包括重算 sha256，
    确保返回的包与用户确认时看到的内容完全一致。
    """
    token = (token or "").strip().lower()
    if not _is_valid_token(token):
        return None
    target_path = os.path.join(cache_root(), f"{token}.zip")
    if not os.path.isfile(target_path):
        return None
    try:
        if _hash_file(target_path) != token:
            logger.warning(f"插件安装缓存校验失败（内容已变化），将丢弃: {token[:12]}")
            release_archive(token)
            return None
    except OSError as exc:
        logger.warning(f"读取插件安装缓存失败: {exc!s}")
        return None
    return target_path


def release_archive(token: str) -> None:
    """删除缓存包。token 非法或文件已不存在时静默返回。"""
    token = (token or "").strip().lower()
    if not _is_valid_token(token):
        return
    target_path = os.path.join(cache_root(), f"{token}.zip")
    try:
        if os.path.isfile(target_path):
            os.remove(target_path)
    except OSError as exc:
        logger.debug(f"删除插件安装缓存失败 {target_path}: {exc!s}")
    # 解压后可能残留空目录
    try:
        root = Path(cache_root())
        if root.is_dir() and not any(root.iterdir()):
            root.rmdir()
    except OSError:
        pass
