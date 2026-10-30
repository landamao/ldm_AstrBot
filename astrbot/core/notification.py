"""重要通知服务。

用于持久化需要管理员知晓或处理的系统事件（例如上下文压缩失败）。
与日志的区别：日志会被滚动刷掉、没人主动看就毫无察觉；重要通知会一直
残留，直到管理员在 WebUI 通知中心或通过 help 指令看到并手动标记已读。

行为约定：
- 同一 key 的未读通知聚合计数（count + 1），不重复堆积
- 已读必须显式调用 mark_read（打开面板不算已读）
- 已读后同一 key 再次发生会生成一条新通知（旧问题已确认，新发生是新问题）
"""

from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import select

from astrbot.core import db_helper, logger
from astrbot.core.db.po import ImportantNotification


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NotificationService:
    """重要通知的写入与查询。"""

    async def notify(self, key: str, title: str, detail: str = "") -> None:
        """记录一条重要通知。

        同 key 存在未读通知时聚合（count+1、刷新详情与时间），否则新建。
        写入失败只记录日志，不影响调用方主流程。
        """
        try:
            async with db_helper.get_db() as session:
                existing = (
                    await session.execute(
                        select(ImportantNotification)
                        .where(ImportantNotification.key == key)
                        .where(ImportantNotification.read == False)  # noqa: E712
                        .order_by(ImportantNotification.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    existing.count += 1
                    existing.detail = detail
                    existing.updated_at = _now()
                    session.add(existing)
                else:
                    session.add(
                        ImportantNotification(
                            key=key,
                            title=title,
                            detail=detail,
                        )
                    )
                await session.commit()
        except Exception:
            logger.error("写入重要通知失败 (key=%s)", key, exc_info=True)

    async def list_notifications(
        self, include_read: bool = True, limit: int = 100
    ) -> list[ImportantNotification]:
        """按时间倒序列出通知，未读在前。"""
        try:
            async with db_helper.get_db() as session:
                stmt = select(ImportantNotification).order_by(
                    ImportantNotification.read.asc(),
                    ImportantNotification.updated_at.desc(),
                )
                if not include_read:
                    stmt = stmt.where(ImportantNotification.read == False)  # noqa: E712
                stmt = stmt.limit(max(1, min(int(limit), 500)))
                return list((await session.execute(stmt)).scalars().all())
        except Exception:
            logger.error("读取重要通知失败", exc_info=True)
            return []

    async def unread_count(self) -> int:
        try:
            async with db_helper.get_db() as session:
                total = (
                    await session.execute(
                        select(func.count())
                        .select_from(ImportantNotification)
                        .where(ImportantNotification.read == False)  # noqa: E712
                    )
                ).scalar()
                return int(total or 0)
        except Exception:
            logger.error("统计未读重要通知失败", exc_info=True)
            return 0

    async def mark_read(self, ids: list[int]) -> int:
        """将指定 id 的通知标记为已读，返回实际标记数量。"""
        if not ids:
            return 0
        try:
            async with db_helper.get_db() as session:
                stmt = select(ImportantNotification).where(
                    ImportantNotification.id.in_([int(i) for i in ids]),
                    ImportantNotification.read == False,  # noqa: E712
                )
                items = list((await session.execute(stmt)).scalars().all())
                for item in items:
                    item.read = True
                    item.read_at = _now()
                    session.add(item)
                await session.commit()
                return len(items)
        except Exception:
            logger.error("标记重要通知已读失败", exc_info=True)
            return 0


notification_service = NotificationService()
