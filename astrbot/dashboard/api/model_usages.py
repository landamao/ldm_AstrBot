from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from astrbot.dashboard.responses import ok

from .auth import AuthContext, require_scope

router = APIRouter(tags=["Model Usages"])


async def require_config_scope(request: Request) -> AuthContext:
    return await require_scope(request, "config")


def get_service(request: Request):
    return request.app.state.services.model_usages


@router.get("/model-usages/scan")
async def scan_model_usages(
    _auth: AuthContext = Depends(require_config_scope),
    service=Depends(get_service),
):
    """扫描主配置与插件配置中所有模型（提供商）使用选择。"""
    return ok(service.scan())


@router.post("/model-usages/apply")
async def apply_model_usage_replacements(
    payload: dict,
    _auth: AuthContext = Depends(require_config_scope),
    service=Depends(get_service),
):
    """把一批配置项的模型引用直接改写为指定值（new_value）。"""
    replacements = payload.get("replacements")
    if not isinstance(replacements, list):
        replacements = []
    return ok(await service.apply(replacements))
