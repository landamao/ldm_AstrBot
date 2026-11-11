from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse

from astrbot.dashboard.responses import ApiError, ok
from astrbot.dashboard.services.dashboard_preference_service import (
    LOGO_FILES_URL_PREFIX,
    LOGO_MAX_SIZE,
    WALLPAPER_FILES_URL_PREFIX,
    WALLPAPER_MAX_SIZE,
    DashboardPreferenceService,
)

from .auth import require_dashboard_user

router = APIRouter(tags=["Dashboard Preferences"])
legacy_router = APIRouter(tags=["Dashboard Preferences"], include_in_schema=False)


def get_service(request: Request) -> DashboardPreferenceService:
    return request.app.state.services.dashboard_preferences


async def _json_or_empty(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


@router.get("/ui-preferences")
async def get_ui_preferences(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    """获取仪表盘 UI 偏好（侧边栏顺序、主题色）。"""
    return ok(await service.get_all())


@router.get("/ui-preferences/sidebar")
async def get_sidebar_preference(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    return ok({"sidebar": await service.get_sidebar_customization()})


@router.put("/ui-preferences/sidebar")
async def put_sidebar_preference(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    body = await _json_or_empty(request)
    # 兼容直接传 {mainItems, moreItems} 或包一层 sidebar
    payload = body.get("sidebar", body)
    sidebar = await service.set_sidebar_customization(payload)
    return ok({"sidebar": sidebar}, message="侧边栏布局已保存")


@router.get("/ui-preferences/theme-colors")
async def get_theme_colors_preference(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    return ok({"theme_colors": await service.get_theme_colors()})


@router.put("/ui-preferences/theme-colors")
async def put_theme_colors_preference(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    body = await _json_or_empty(request)
    payload = body.get("theme_colors", body.get("themeColors", body))
    theme_colors = await service.set_theme_colors(payload)
    return ok({"theme_colors": theme_colors}, message="主题颜色已保存")


@router.get("/ui-preferences/motion")
async def get_motion_preference(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    return ok({"motion": await service.get_motion()})


@router.put("/ui-preferences/motion")
async def put_motion_preference(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    body = await _json_or_empty(request)
    payload = body.get("motion", body)
    motion = await service.set_motion(payload)
    return ok({"motion": motion}, message="动效设置已保存")


@router.get("/ui-preferences/wallpaper")
async def get_wallpaper_preference(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    return ok({"wallpaper": await service.get_wallpaper()})


@router.put("/ui-preferences/wallpaper")
async def put_wallpaper_preference(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    body = await _json_or_empty(request)
    payload = body.get("wallpaper", body.get("wallpaperSettings", body))
    wallpaper = await service.set_wallpaper(payload)
    return ok({"wallpaper": wallpaper}, message="壁纸设置已保存")


@router.post("/ui-preferences/wallpaper/upload")
async def upload_wallpaper(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
    file: UploadFile = File(...),
    compress: bool = True,
):
    """上传壁纸图片，保存到 data/wallpapers/ 并返回可访问 URL。

    compress=true（默认）时自动压缩（最长边 1920px 内、无透明转 JPEG /
    有透明转 WebP、GIF 动图保留）；compress=false 原样保存。
    """
    content = await file.read()
    if len(content) > WALLPAPER_MAX_SIZE:
        raise ApiError(f"图片大小超出限制（最大 {WALLPAPER_MAX_SIZE // 1024 // 1024}MB）", status_code=400)
    try:
        url = service.save_uploaded_wallpaper(
            file.filename or "", content, compress=compress
        )
    except ValueError as exc:
        raise ApiError(str(exc), status_code=400) from exc
    return ok({"url": url}, message="壁纸上传成功")


# 带 token 的壁纸地址是「一次性」的唯一地址，可以放心让浏览器长期缓存：
# 设置页预览、点下载再取同一 token 时直接命中缓存或服务端令牌缓存，不会再换图
_WALLPAPER_IMAGE_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
}


@router.get("/ui-preferences/wallpaper/proxy")
async def proxy_wallpaper(
    url: str,
    t: str = "",
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    """远程壁纸取图接口。

    两种用法：
    - 带 `t`（令牌）：流式透传远程图片，边收边发给浏览器（不缓冲，首屏快），
      同时把这次字节钉在令牌上；同一令牌再次请求直接返回这份字节，
      所以「设置页预览 / 下载当前壁纸」拿到的和页面背景是同一张。
    - 不带 `t`：一次性收完再返回（兼容旧调用）。
    """
    if t:
        cached = service.get_cached_wallpaper_image(t)
        if cached is not None:
            content, media_type = cached
            return Response(
                content=content,
                media_type=media_type,
                headers=_WALLPAPER_IMAGE_HEADERS,
            )
    try:
        if t:
            stream, media_type = await service.open_remote_wallpaper_stream(url, t)
            return StreamingResponse(
                stream,
                media_type=media_type,
                headers=_WALLPAPER_IMAGE_HEADERS,
            )
        content, media_type = await service.fetch_remote_wallpaper(url)
    except ValueError as exc:
        raise ApiError(str(exc), status_code=400) from exc
    except Exception as exc:  # 网络异常等
        reason = str(exc).strip() or repr(exc)
        raise ApiError(
            f"服务端拉取壁纸失败（{type(exc).__name__}）：{reason}"
            "。如果是网络/代理问题，可在 AstrBot 的 .env 里配置 https_proxy",
            status_code=502,
        ) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers=_WALLPAPER_IMAGE_HEADERS,
    )


@router.get("/ui-preferences/wallpaper/files/{filename}")
async def get_wallpaper_file(
    filename: str,
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    """访问上传的壁纸文件（需登录，防路径穿越；uuid 文件名内容不变，长缓存）。"""
    path = service.resolve_wallpaper_file_path(
        f"{WALLPAPER_FILES_URL_PREFIX}{filename}"
    )
    if path is None or not path.is_file():
        raise ApiError("壁纸文件不存在", status_code=404)
    return FileResponse(
        path,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/ui-preferences/login-wallpaper")
async def get_login_wallpaper_preference(
    service: DashboardPreferenceService = Depends(get_service),
):
    """获取登录页壁纸设置（公开接口，无需登录，供登录页读取）。"""
    return ok({"login_wallpaper": await service.get_login_wallpaper()})


@router.put("/ui-preferences/login-wallpaper")
async def put_login_wallpaper_preference(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    body = await _json_or_empty(request)
    payload = body.get("login_wallpaper", body)
    login_wallpaper = await service.set_login_wallpaper(payload)
    return ok({"login_wallpaper": login_wallpaper}, message="登录页壁纸设置已保存")


@router.get("/ui-preferences/logo")
async def get_logo_preference(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    return ok({"logo": await service.get_logo()})


@router.put("/ui-preferences/logo")
async def put_logo_preference(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    body = await _json_or_empty(request)
    payload = body.get("logo", body)
    logo = await service.set_logo(payload)
    return ok({"logo": logo}, message="Logo 设置已保存")


@router.post("/ui-preferences/logo/upload")
async def upload_logo(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
    file: UploadFile = File(...),
    compress: bool = True,
):
    """上传 Logo 图片，保存到 data/logos/ 并返回可访问 URL（最长边缩到 512px）。"""
    content = await file.read()
    if len(content) > LOGO_MAX_SIZE:
        raise ApiError(
            f"图片大小超出限制（最大 {LOGO_MAX_SIZE // 1024 // 1024}MB）",
            status_code=400,
        )
    try:
        url = service.save_uploaded_logo(
            file.filename or "", content, compress=compress
        )
    except ValueError as exc:
        raise ApiError(str(exc), status_code=400) from exc
    return ok({"url": url}, message="Logo 上传成功")


@router.get("/ui-preferences/logo/files/{filename}")
async def get_logo_file(
    filename: str,
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    """访问上传的 Logo 文件（需登录，防路径穿越；uuid 文件名内容不变，长缓存）。"""
    path = service.resolve_logo_file_path(f"{LOGO_FILES_URL_PREFIX}{filename}")
    if path is None or not path.is_file():
        raise ApiError("Logo 文件不存在", status_code=404)
    return FileResponse(
        path,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# ---- legacy ----

@legacy_router.get("/api/ui-preferences")
async def legacy_get_ui_preferences(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    return ok(await service.get_all())


@legacy_router.get("/api/ui-preferences/sidebar")
async def legacy_get_sidebar(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    return ok({"sidebar": await service.get_sidebar_customization()})


@legacy_router.post("/api/ui-preferences/sidebar")
async def legacy_save_sidebar(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    body = await _json_or_empty(request)
    payload = body.get("sidebar", body)
    sidebar = await service.set_sidebar_customization(payload)
    return ok({"sidebar": sidebar}, message="侧边栏布局已保存")


@legacy_router.get("/api/ui-preferences/theme-colors")
async def legacy_get_theme_colors(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    return ok({"theme_colors": await service.get_theme_colors()})


@legacy_router.post("/api/ui-preferences/theme-colors")
async def legacy_save_theme_colors(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    body = await _json_or_empty(request)
    payload = body.get("theme_colors", body.get("themeColors", body))
    theme_colors = await service.set_theme_colors(payload)
    return ok({"theme_colors": theme_colors}, message="主题颜色已保存")


@legacy_router.get("/api/ui-preferences/motion")
async def legacy_get_motion(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    return ok({"motion": await service.get_motion()})


@legacy_router.post("/api/ui-preferences/motion")
async def legacy_save_motion(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    body = await _json_or_empty(request)
    payload = body.get("motion", body)
    motion = await service.set_motion(payload)
    return ok({"motion": motion}, message="动效设置已保存")


@legacy_router.get("/api/ui-preferences/wallpaper")
async def legacy_get_wallpaper(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    return ok({"wallpaper": await service.get_wallpaper()})


@legacy_router.post("/api/ui-preferences/wallpaper")
async def legacy_save_wallpaper(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    body = await _json_or_empty(request)
    payload = body.get("wallpaper", body.get("wallpaperSettings", body))
    wallpaper = await service.set_wallpaper(payload)
    return ok({"wallpaper": wallpaper}, message="壁纸设置已保存")


@legacy_router.post("/api/ui-preferences/wallpaper/upload")
async def legacy_upload_wallpaper(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
    file: UploadFile = File(...),
    compress: bool = True,
):
    content = await file.read()
    if len(content) > WALLPAPER_MAX_SIZE:
        raise ApiError(f"图片大小超出限制（最大 {WALLPAPER_MAX_SIZE // 1024 // 1024}MB）", status_code=400)
    try:
        url = service.save_uploaded_wallpaper(
            file.filename or "", content, compress=compress
        )
    except ValueError as exc:
        raise ApiError(str(exc), status_code=400) from exc
    return ok({"url": url}, message="壁纸上传成功")


@legacy_router.get("/api/ui-preferences/logo")
async def legacy_get_logo(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    return ok({"logo": await service.get_logo()})


@legacy_router.post("/api/ui-preferences/logo")
async def legacy_save_logo(
    request: Request,
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
):
    body = await _json_or_empty(request)
    payload = body.get("logo", body)
    logo = await service.set_logo(payload)
    return ok({"logo": logo}, message="Logo 设置已保存")


@legacy_router.post("/api/ui-preferences/logo/upload")
async def legacy_upload_logo(
    _username: str = Depends(require_dashboard_user),
    service: DashboardPreferenceService = Depends(get_service),
    file: UploadFile = File(...),
    compress: bool = True,
):
    content = await file.read()
    if len(content) > LOGO_MAX_SIZE:
        raise ApiError(f"图片大小超出限制（最大 {LOGO_MAX_SIZE // 1024 // 1024}MB）", status_code=400)
    try:
        url = service.save_uploaded_logo(
            file.filename or "", content, compress=compress
        )
    except ValueError as exc:
        raise ApiError(str(exc), status_code=400) from exc
    return ok({"url": url}, message="Logo 上传成功")
