from __future__ import annotations

import gzip
import mimetypes
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from astrbot.dashboard.services.static_file_service import StaticFileService

router = APIRouter(include_in_schema=False)
service = StaticFileService()

# 静态资源缓存策略：
# - assets/ 下的构建产物文件名自带内容 hash，可以一年长缓存（immutable）
# - index.html 等入口文件必须 no-cache（每次条件请求协商，未变返回 304），
#   否则发版后浏览器会长期停留在旧入口页，引用旧资源，Ctrl+F5 都可能绕不开
_ASSETS_CACHE_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
}
_ENTRY_CACHE_HEADERS = {
    "Cache-Control": "no-cache",
}

# 可 gzip 压缩的静态类型（woff2/png/jpg 等本身已压缩的类型不压，省 CPU 且无收益）
_COMPRESSIBLE_SUFFIXES = {
    ".js",
    ".css",
    ".svg",
    ".json",
    ".txt",
    ".html",
    ".htm",
    ".woff",
    ".map",
    ".xml",
    ".md",
    ".webmanifest",
}

# 防止并发首次访问时重复压缩同一文件
_GZIP_LOCK = threading.Lock()


# 版本标记文件不通过静态通道对外提供：后端直接读磁盘，前端版本信息走 /api 获取。
# 否则未登录用户可直接 GET /version、/assets/version 读到版本号。
_PROTECTED_STATIC_PATHS = ("version", "assets/version")


def _is_protected_static_path(static_path: str) -> bool:
    normalized = static_path.replace("\\", "/").strip("/")
    # 去掉空段与 "./"，防止 /./version 之类的写法绕过
    parts = [part for part in normalized.split("/") if part and part != "."]
    normalized = "/".join(parts)
    for protected in _PROTECTED_STATIC_PATHS:
        if normalized == protected or normalized.startswith(protected + "."):
            # startswith 覆盖 .gz 压缩缓存与 .gz.tmp 临时文件
            return True
    return False


def _static_folder(request: Request) -> str | None:
    return getattr(request.app.state, "dashboard_static_folder", None)


def _not_found_response() -> PlainTextResponse:
    return PlainTextResponse(service.get_not_found_message(), status_code=404)


def _accepts_gzip(request: Request) -> bool:
    return "gzip" in request.headers.get("accept-encoding", "").lower()


def _is_compressible(file_path: Path) -> bool:
    return file_path.suffix.lower() in _COMPRESSIBLE_SUFFIXES


def _ensure_gzip_file(file_path: Path) -> Path | None:
    """确保 file_path 同目录存在对应的 .gz 压缩缓存，返回 gz 路径；失败返回 None。

    首次请求某静态文件时压缩一次并落盘，后续请求直接读磁盘 .gz，零重复压缩开销。
    源文件比 .gz 新（部署覆盖、手动修改）时自动重新压缩，避免一直返回旧内容。
    """
    gz_path = file_path.with_name(file_path.name + ".gz")
    try:
        src_mtime = file_path.stat().st_mtime
    except OSError:
        return None
    if gz_path.exists():
        try:
            if gz_path.stat().st_mtime >= src_mtime:
                return gz_path
        except OSError:
            pass
    compressed = gzip.compress(file_path.read_bytes(), compresslevel=6, mtime=0)
    with _GZIP_LOCK:
        if gz_path.exists() and gz_path.stat().st_mtime >= src_mtime:
            return gz_path
        tmp_path = gz_path.with_name(gz_path.name + ".tmp")
        try:
            tmp_path.write_bytes(compressed)
            tmp_path.replace(gz_path)
        except OSError:
            return None
    return gz_path


def _file_response(
    file_path: Path,
    request: Request | None = None,
    cache_headers: dict[str, str] | None = None,
) -> FileResponse:
    headers = dict(cache_headers or _ASSETS_CACHE_HEADERS)
    if (
        request is not None
        and _is_compressible(file_path)
        and _accepts_gzip(request)
    ):
        gz_path = _ensure_gzip_file(file_path)
        if gz_path is not None:
            media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            headers["Content-Encoding"] = "gzip"
            headers["Vary"] = "Accept-Encoding"
            return FileResponse(gz_path, media_type=media_type, headers=headers)
    return FileResponse(file_path, headers=headers)


async def serve_index(request: Request):
    index_file = service.resolve_index_file(_static_folder(request))
    if index_file is None:
        return _not_found_response()
    return _file_response(index_file, request, cache_headers=_ENTRY_CACHE_HEADERS)


async def serve_static_file(request: Request, static_path: str):
    if request.url.path.startswith("/api"):
        raise HTTPException(status_code=404)

    if _is_protected_static_path(static_path):
        # 版本标记文件一律 404，不暴露存在性
        return _not_found_response()

    file_path = service.resolve_static_file(_static_folder(request), static_path)
    if file_path is None:
        return _not_found_response()

    normalized_path = static_path.replace("\\", "/").lstrip("/")
    cache_headers = (
        _ASSETS_CACHE_HEADERS
        if normalized_path == "assets" or normalized_path.startswith("assets/")
        else _ENTRY_CACHE_HEADERS
    )
    return _file_response(file_path, request, cache_headers=cache_headers)


for index_route in service.list_index_routes():
    router.add_api_route(index_route, serve_index, methods=["GET"])

router.add_api_route("/{static_path:path}", serve_static_file, methods=["GET"])
