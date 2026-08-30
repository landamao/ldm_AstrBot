from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from astrbot.core import logger
from astrbot.dashboard.async_utils import run_maybe_async
from astrbot.dashboard.responses import error, ok
from astrbot.dashboard.schemas import (
    BackupImportRequest,
    BackupRenameRequest,
    BackupUploadInitRequest,
    BackupUploadSessionRequest,
)
from astrbot.dashboard.services.backup_service import (
    BackupService,
    BackupServiceError,
)

from .auth import AuthContext, require_dashboard_user, require_scope

router = APIRouter(tags=["Backups"])
legacy_router = APIRouter(
    prefix="/api/backup",
    tags=["Dashboard Backups"],
    include_in_schema=False,
)


def get_service(request: Request) -> BackupService:
    return request.app.state.services.backups


async def require_system_scope(request: Request) -> AuthContext:
    return await require_scope(request, "system")


def _model_dict(payload) -> dict:
    return payload.model_dump(exclude_none=True)


def _ok_result(result):
    if isinstance(result, tuple):
        data, message = result
        return ok(data, message)
    return ok(result)


def _safe_backup_filename(filename: str | None) -> str:
    if not filename:
        raise BackupServiceError("缺少参数 filename")
    if ".." in filename or "/" in filename or "\\" in filename:
        raise BackupServiceError("文件名包含非法路径字符")
    return filename


async def _json_or_empty(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


async def _run(operation, *, prefix: str):
    try:
        result = await run_maybe_async(operation)
        return _ok_result(result)
    except BackupServiceError as exc:
        return error(str(exc))
    except Exception as exc:
        logger.error("%s: %s", prefix, exc, exc_info=True)
        return error(f"{prefix}: {exc!s}")


def _download_error_response(message: str, *, status_code: int = 400) -> JSONResponse:
    """下载失败必须带正确 HTTP 状态码，避免浏览器把错误 JSON 当 zip 保存。"""
    return JSONResponse(error(message), status_code=status_code)


def _download_response(download) -> FileResponse:
    return FileResponse(
        download.path,
        filename=download.filename,
        media_type="application/zip",
    )


def _download_backup(
    *,
    filename: str | None,
    service: BackupService,
    ticket: str | None = None,
):
    try:
        filename = _safe_backup_filename(filename)
        if ticket:
            download = service.consume_download_ticket(
                filename=filename,
                ticket=ticket,
            )
        else:
            download = service.prepare_download(filename=filename)
        return _download_response(download)
    except BackupServiceError as exc:
        msg = str(exc)
        # 凭证类问题用 401/403 更合适；不存在用 404；其余 400
        if "不存在" in msg:
            code = 404
        elif "凭证" in msg or "过期" in msg or "不匹配" in msg:
            code = 401
        else:
            code = 400
        return _download_error_response(msg, status_code=code)
    except Exception as exc:
        logger.error("下载备份失败: %s", exc, exc_info=True)
        return _download_error_response(f"下载备份失败: {exc!s}", status_code=500)


@router.get("/backups")
async def list_backups(
    page: int = Query(default=1),
    page_size: int = Query(default=20),
    _auth: AuthContext = Depends(require_system_scope),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.list_backups(page=page, page_size=page_size),
        prefix="获取备份列表失败",
    )


@legacy_router.get("/list")
async def list_dashboard_backups(
    page: int = Query(default=1),
    page_size: int = Query(default=20),
    _username: str = Depends(require_dashboard_user),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.list_backups(page=page, page_size=page_size),
        prefix="获取备份列表失败",
    )


@router.post("/backups")
async def create_backup(
    _auth: AuthContext = Depends(require_system_scope),
    service: BackupService = Depends(get_service),
):
    return await _run(service.export_backup, prefix="创建备份失败")


@legacy_router.post("/export")
async def export_dashboard_backup(
    _username: str = Depends(require_dashboard_user),
    service: BackupService = Depends(get_service),
):
    return await _run(service.export_backup, prefix="创建备份失败")


@router.post("/backups/upload")
async def upload_backup(
    file: UploadFile = File(...),
    _auth: AuthContext = Depends(require_system_scope),
    service: BackupService = Depends(get_service),
):
    return await _run(lambda: service.upload_backup(file), prefix="上传备份文件失败")


@legacy_router.post("/upload")
async def upload_dashboard_backup(
    file: UploadFile = File(...),
    _username: str = Depends(require_dashboard_user),
    service: BackupService = Depends(get_service),
):
    return await _run(lambda: service.upload_backup(file), prefix="上传备份文件失败")


@router.post("/backups/upload/init")
async def init_backup_upload(
    payload: BackupUploadInitRequest,
    _auth: AuthContext = Depends(require_system_scope),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.upload_init(_model_dict(payload)),
        prefix="初始化分片上传失败",
    )


@legacy_router.post("/upload/init")
async def init_dashboard_backup_upload(
    payload: BackupUploadInitRequest,
    _username: str = Depends(require_dashboard_user),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.upload_init(_model_dict(payload)),
        prefix="初始化分片上传失败",
    )


@router.post("/backups/upload/chunk")
async def upload_backup_chunk(
    upload_id: str = Form(...),
    chunk_index: str = Form(...),
    chunk: UploadFile = File(...),
    _auth: AuthContext = Depends(require_system_scope),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.upload_chunk(
            upload_id=upload_id,
            chunk_index_str=chunk_index,
            chunk_file=chunk,
        ),
        prefix="上传分片失败",
    )


@legacy_router.post("/upload/chunk")
async def upload_dashboard_backup_chunk(
    upload_id: str = Form(...),
    chunk_index: str = Form(...),
    chunk: UploadFile = File(...),
    _username: str = Depends(require_dashboard_user),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.upload_chunk(
            upload_id=upload_id,
            chunk_index_str=chunk_index,
            chunk_file=chunk,
        ),
        prefix="上传分片失败",
    )


@router.post("/backups/upload/complete")
async def complete_backup_upload(
    payload: BackupUploadSessionRequest,
    _auth: AuthContext = Depends(require_system_scope),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.upload_complete(_model_dict(payload)),
        prefix="完成分片上传失败",
    )


@legacy_router.post("/upload/complete")
async def complete_dashboard_backup_upload(
    payload: BackupUploadSessionRequest,
    _username: str = Depends(require_dashboard_user),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.upload_complete(_model_dict(payload)),
        prefix="完成分片上传失败",
    )


@router.post("/backups/upload/abort")
async def abort_backup_upload(
    payload: BackupUploadSessionRequest,
    _auth: AuthContext = Depends(require_system_scope),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.upload_abort(_model_dict(payload)),
        prefix="取消上传失败",
    )


@legacy_router.post("/upload/abort")
async def abort_dashboard_backup_upload(
    payload: BackupUploadSessionRequest,
    _username: str = Depends(require_dashboard_user),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.upload_abort(_model_dict(payload)),
        prefix="取消上传失败",
    )


@router.get("/backups/tasks/{task_id}")
async def get_backup_progress(
    task_id: str,
    _auth: AuthContext = Depends(require_system_scope),
    service: BackupService = Depends(get_service),
):
    return await _run(lambda: service.get_progress(task_id), prefix="获取任务进度失败")


@legacy_router.get("/progress")
async def get_dashboard_backup_progress(
    task_id: str | None = Query(default=None),
    _username: str = Depends(require_dashboard_user),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.get_progress(task_id),
        prefix="获取任务进度失败",
    )


@router.post("/backups/{filename:path}/download-ticket")
async def issue_backup_download_ticket(
    filename: str,
    auth: AuthContext = Depends(require_system_scope),
    service: BackupService = Depends(get_service),
):
    """签发短时可复用下载票据（需登录）。随后用原生导航流式下载大文件。"""
    return await _run(
        lambda: service.issue_download_ticket(
            _safe_backup_filename(filename),
            username=auth.username,
        ),
        prefix="签发下载凭证失败",
    )


@legacy_router.post("/download-ticket")
async def issue_dashboard_backup_download_ticket(
    filename: str | None = Query(default=None),
    username: str = Depends(require_dashboard_user),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.issue_download_ticket(
            _safe_backup_filename(filename),
            username=username,
        ),
        prefix="签发下载凭证失败",
    )


@router.get("/backups/{filename:path}")
async def download_backup(
    request: Request,
    filename: str,
    ticket: str | None = Query(default=None),
    service: BackupService = Depends(get_service),
):
    # 优先：短时票据 → 浏览器原生流式（适合 GB 级，URL 不带登录 JWT）
    # 票据有效期内可重复使用（兼容手机 HEAD/重试）；否则走会话鉴权
    if ticket:
        return _download_backup(filename=filename, service=service, ticket=ticket)
    await require_system_scope(request)
    return _download_backup(filename=filename, service=service)


@legacy_router.get("/download")
async def download_dashboard_backup(
    request: Request,
    filename: str | None = Query(default=None),
    ticket: str | None = Query(default=None),
    service: BackupService = Depends(get_service),
):
    if ticket:
        return _download_backup(filename=filename, service=service, ticket=ticket)
    await require_dashboard_user(request)
    return _download_backup(filename=filename, service=service)


@router.patch("/backups/{filename:path}")
async def rename_backup(
    filename: str,
    payload: BackupRenameRequest,
    _auth: AuthContext = Depends(require_system_scope),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.rename_backup(
            {"filename": _safe_backup_filename(filename), **_model_dict(payload)}
        ),
        prefix="重命名备份失败",
    )


@legacy_router.post("/rename")
async def rename_dashboard_backup(
    payload: BackupRenameRequest,
    filename: str | None = Query(default=None),
    _username: str = Depends(require_dashboard_user),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.rename_backup({"filename": filename, **_model_dict(payload)}),
        prefix="重命名备份失败",
    )


@router.delete("/backups/{filename:path}")
async def delete_backup(
    filename: str,
    _auth: AuthContext = Depends(require_system_scope),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.delete_backup({"filename": _safe_backup_filename(filename)}),
        prefix="删除备份失败",
    )


@legacy_router.post("/delete")
async def delete_dashboard_backup(
    request: Request,
    filename: str | None = Query(default=None),
    _username: str = Depends(require_dashboard_user),
    service: BackupService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(
        lambda: service.delete_backup({"filename": filename, **body}),
        prefix="删除备份失败",
    )


@router.post("/backups/{filename:path}/check")
async def check_backup(
    filename: str,
    _auth: AuthContext = Depends(require_system_scope),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.check_backup({"filename": _safe_backup_filename(filename)}),
        prefix="预检查备份文件失败",
    )


@legacy_router.post("/check")
async def check_dashboard_backup(
    request: Request,
    filename: str | None = Query(default=None),
    _username: str = Depends(require_dashboard_user),
    service: BackupService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(
        lambda: service.check_backup({"filename": filename, **body}),
        prefix="预检查备份文件失败",
    )


@router.post("/backups/{filename:path}/import")
async def import_backup(
    filename: str,
    payload: BackupImportRequest,
    _auth: AuthContext = Depends(require_system_scope),
    service: BackupService = Depends(get_service),
):
    return await _run(
        lambda: service.import_backup(
            {"filename": _safe_backup_filename(filename), **_model_dict(payload)}
        ),
        prefix="导入备份失败",
    )


@legacy_router.post("/import")
async def import_dashboard_backup(
    request: Request,
    filename: str | None = Query(default=None),
    _username: str = Depends(require_dashboard_user),
    service: BackupService = Depends(get_service),
):
    body = await _json_or_empty(request)
    return await _run(
        lambda: service.import_backup({"filename": filename, **body}),
        prefix="导入备份失败",
    )
