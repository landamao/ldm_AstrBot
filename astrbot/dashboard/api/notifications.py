from fastapi import APIRouter, Depends
from pydantic import BaseModel

from astrbot.core.notification import notification_service
from astrbot.dashboard.responses import ok

from .auth import require_dashboard_user

router = APIRouter(tags=["Notifications"])


class MarkReadRequest(BaseModel):
    ids: list[int]


class DeleteRequest(BaseModel):
    ids: list[int] = []
    clear_read: bool = False


@router.get("/notifications")
async def list_notifications(
    include_read: bool = True,
    limit: int = 100,
    _username: str = Depends(require_dashboard_user),
):
    """列出重要通知（未读在前），附带未读数量。"""
    items = await notification_service.list_notifications(
        include_read=include_read, limit=limit
    )
    unread = await notification_service.unread_count()
    return ok(
        {
            "items": [item.model_dump(mode="json") for item in items],
            "unread": unread,
        }
    )


@router.post("/notifications/read")
async def mark_notifications_read(
    body: MarkReadRequest,
    _username: str = Depends(require_dashboard_user),
):
    """将指定通知标记为已读。必须显式调用，打开面板不会自动已读。"""
    marked = await notification_service.mark_read(body.ids)
    return ok({"marked": marked, "unread": await notification_service.unread_count()})


@router.post("/notifications/delete")
async def delete_notifications(
    body: DeleteRequest,
    _username: str = Depends(require_dashboard_user),
):
    """删除通知。

    - body.ids：删这几条（已读未读都删）
    - body.clear_read=true：清空所有已读通知
    """
    deleted = await notification_service.delete_notifications(
        ids=body.ids,
        clear_read=body.clear_read,
    )
    return ok({"deleted": deleted, "unread": await notification_service.unread_count()})
