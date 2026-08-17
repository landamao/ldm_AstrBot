"""项目文档接口：读取源码树根目录的 README.md / CHANGELOG.md。

供 WebUI 左下角「官方文档 / 更新日志」按钮在弹窗内渲染本地文档，
替代原来直接外链 GitHub 的 README。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends

from astrbot.dashboard.responses import ApiError, ok

from .auth import AuthContext, require_dashboard_user

router = APIRouter(tags=["Project Docs"])

_PROJECT_DOC_FILES = {
    "readme": "README.md",
    "changelog": "CHANGELOG.md",
}


def _project_root() -> Path:
    # astrbot/dashboard/api/project_docs.py -> 上溯三级到项目根（含 astrbot/、README.md、CHANGELOG.md）
    return Path(__file__).resolve().parents[3]


@router.get("/project-docs/{doc}")
async def get_project_doc(
    doc: str,
    _auth: AuthContext = Depends(require_dashboard_user),
):
    filename = _PROJECT_DOC_FILES.get(doc)
    if not filename:
        raise ApiError("不支持的文档类型。")
    file_path = _project_root() / filename
    if not file_path.is_file():
        raise ApiError(f"项目根目录未找到 {filename}。")
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise ApiError(f"读取 {filename} 失败。") from exc
    return ok({"content": content})
