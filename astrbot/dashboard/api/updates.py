from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from astrbot.core import logger
from astrbot.core.desktop_runtime import DESKTOP_MANAGED_RESTART_MESSAGE
from astrbot.dashboard.async_utils import run_maybe_async
from astrbot.dashboard.schemas import PipInstallRequest, UpdateRequest
from astrbot.dashboard.services.update_service import (
    UpdateService,
    UpdateServiceError,
    UpdateServiceResult,
)

from .auth import AuthContext, require_dashboard_user, require_scope

router = APIRouter(tags=["Updates"])
legacy_router = APIRouter(
    prefix="/api/update",
    tags=["Dashboard Updates"],
    include_in_schema=False,
)


def get_service(request: Request) -> UpdateService:
    return request.app.state.services.updates


async def require_system_scope(request: Request) -> AuthContext:
    return await require_scope(request, "system")


def _model_dict(payload) -> dict:
    return payload.model_dump(exclude_none=True)


def _result_payload(result: UpdateServiceResult) -> dict:
    if result.status == "success":
        return {
            "status": "success",
            "message": result.message,
            "data": result.data,
        }
    if result.status == "warning":
        return {
            "status": "warning",
            "message": result.message,
            "data": result.data,
        }
    return {
        "status": "ok",
        "message": result.message,
        "data": {} if result.data is None else result.data,
    }


def _service_response(result: UpdateServiceResult) -> JSONResponse:
    return JSONResponse(
        _result_payload(result),
        status_code=200,
        headers=result.headers or None,
    )


def _service_error(exc: UpdateServiceError) -> JSONResponse:
    logger.error(f"Dashboard update operation failed: {exc}", exc_info=True)
    if exc.code == "desktop_managed":
        return JSONResponse(
            {
                "status": "error",
                "message": DESKTOP_MANAGED_RESTART_MESSAGE,
                "data": None,
            },
            status_code=200,
        )
    return JSONResponse(
        {"status": "error", "message": "An internal error has occurred.", "data": None},
        status_code=200,
    )


async def _run(operation) -> JSONResponse:
    try:
        result = await run_maybe_async(operation)
        return _service_response(result)
    except UpdateServiceError as exc:
        return _service_error(exc)


@router.get("/updates/check")
async def check_updates(
    update_type: str | None = Query(default=None, alias="type"),
    force_refresh: bool = Query(default=False),
    mirror_url: str = Query(default=""),
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    return await _run(
        lambda: service.check_update(
            update_type, force_refresh=force_refresh, mirror_url=mirror_url
        )
    )


@legacy_router.get("/check")
async def check_dashboard_updates(
    update_type: str | None = Query(default=None, alias="type"),
    force_refresh: bool = Query(default=False),
    mirror_url: str = Query(default=""),
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    return await _run(
        lambda: service.check_update(
            update_type, force_refresh=force_refresh, mirror_url=mirror_url
        )
    )


@router.get("/updates/releases")
async def update_releases(
    force_refresh: bool = Query(default=False),
    mirror_url: str = Query(default=""),
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    return await _run(
        lambda: service.get_releases(
            force_refresh=force_refresh, mirror_url=mirror_url
        )
    )


@legacy_router.get("/releases")
async def dashboard_update_releases(
    force_refresh: bool = Query(default=False),
    mirror_url: str = Query(default=""),
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    return await _run(
        lambda: service.get_releases(
            force_refresh=force_refresh, mirror_url=mirror_url
        )
    )


@router.get("/updates/progress/{task_id}")
async def update_progress(
    task_id: str,
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    return await _run(lambda: service.get_update_progress(task_id))


@legacy_router.get("/progress")
async def dashboard_update_progress(
    progress_id: str | None = Query(default=None, alias="id"),
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    return await _run(lambda: service.get_update_progress(progress_id or ""))


@router.post("/updates/core")
async def update_core(
    payload: UpdateRequest,
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    return await _run(lambda: service.update_project(_model_dict(payload)))


@legacy_router.post("/do")
async def update_dashboard_core(
    payload: UpdateRequest,
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    return await _run(lambda: service.update_project(_model_dict(payload)))


@router.post("/updates/dashboard")
async def update_dashboard(
    payload: UpdateRequest | None = None,
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    mirror_url = _model_dict(payload).get("mirror_url", "") if payload else ""
    return await _run(lambda: service.update_dashboard(mirror_url=mirror_url))


@legacy_router.post("/dashboard")
async def update_dashboard_assets(
    payload: UpdateRequest | None = None,
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    mirror_url = _model_dict(payload).get("mirror_url", "") if payload else ""
    return await _run(lambda: service.update_dashboard(mirror_url=mirror_url))


@router.post("/pip/install")
async def install_pip_package(
    payload: PipInstallRequest,
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    return await _run(lambda: service.install_pip_package(_model_dict(payload)))


@legacy_router.post("/pip-install")
async def install_dashboard_pip_package(
    payload: PipInstallRequest,
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    return await _run(lambda: service.install_pip_package(_model_dict(payload)))


@router.post("/updates/upload")
async def upload_update_package(
    file: UploadFile = File(...),
    reboot: bool = Query(default=True),
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    """从用户上传的 zip 压缩包应用更新。"""
    try:
        file_bytes = await file.read()
        result = await service.update_from_upload(
            file_bytes=file_bytes,
            filename=file.filename or "upload.zip",
            reboot=reboot,
        )
        return _service_response(result)
    except UpdateServiceError as exc:
        return _service_error(exc)
    except Exception as exc:
        logger.error(f"上传更新包失败: {exc}", exc_info=True)
        return JSONResponse(
            {"status": "error", "message": str(exc), "data": None},
            status_code=200,
        )


@legacy_router.post("/upload")
async def upload_update_package_legacy(
    file: UploadFile = File(...),
    reboot: bool = Query(default=True),
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    """从用户上传的 zip 压缩包校验（legacy 路由）。"""
    try:
        file_bytes = await file.read()
        result = await service.update_from_upload(
            file_bytes=file_bytes,
            filename=file.filename or "upload.zip",
            reboot=reboot,
        )
        return _service_response(result)
    except UpdateServiceError as exc:
        return _service_error(exc)
    except Exception as exc:
        logger.error(f"上传更新包失败: {exc}", exc_info=True)
        return JSONResponse(
            {"status": "error", "message": str(exc), "data": None},
            status_code=200,
        )


@router.post("/updates/upload/apply")
async def apply_uploaded_package(
    payload: dict,
    _auth: AuthContext = Depends(require_system_scope),
    service: UpdateService = Depends(get_service),
):
    """应用已校验通过的上传压缩包。"""
    try:
        zip_path = payload.get("zip_path", "")
        reboot = payload.get("reboot", True)
        result = await service.apply_uploaded_package(
            zip_path_str=zip_path,
            reboot=reboot,
        )
        return _service_response(result)
    except UpdateServiceError as exc:
        return _service_error(exc)
    except Exception as exc:
        logger.error(f"应用上传更新包失败: {exc}", exc_info=True)
        return JSONResponse(
            {"status": "error", "message": str(exc), "data": None},
            status_code=200,
        )


@legacy_router.post("/upload/apply")
async def apply_uploaded_package_legacy(
    payload: dict,
    _username: str = Depends(require_dashboard_user),
    service: UpdateService = Depends(get_service),
):
    """应用已校验通过的上传压缩包（legacy 路由）。"""
    try:
        zip_path = payload.get("zip_path", "")
        reboot = payload.get("reboot", True)
        result = await service.apply_uploaded_package(
            zip_path_str=zip_path,
            reboot=reboot,
        )
        return _service_response(result)
    except UpdateServiceError as exc:
        return _service_error(exc)
    except Exception as exc:
        logger.error(f"应用上传更新包失败: {exc}", exc_info=True)
        return JSONResponse(
            {"status": "error", "message": str(exc), "data": None},
            status_code=200,
        )
