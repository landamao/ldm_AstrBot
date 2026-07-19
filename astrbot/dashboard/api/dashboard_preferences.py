from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from astrbot.dashboard.responses import ok
from astrbot.dashboard.services.dashboard_preference_service import (
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
