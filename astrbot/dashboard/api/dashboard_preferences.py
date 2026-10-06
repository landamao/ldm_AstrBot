from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse

from astrbot.dashboard.responses import ApiError, ok
from astrbot.dashboard.services.dashboard_preference_service import (
    DashboardPreferenceService,
    LOGO_FILES_URL_PREFIX,
    LOGO_MAX_SIZE,
    WALLPAPER_FILES_URL_PREFIX,
    WALLPAPER_MAX_SIZE,
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
